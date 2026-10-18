"""Disk-backed ownership/migration tests; only private temporary databases."""
import hashlib
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from sqlalchemy.exc import OperationalError
from sqlmodel import Session

from app.saved_trips import ArchiveError, SavedTripRow, canonical, get_record, listing, save
from app.schemas.saved_trip import SaveTrip
from app.storage import LEGACY_PATH, STORAGE_VERSION, StorageError, StorageSettings, database_path, engine_for, migrate_legacy_copy, restore_legacy_backup
from tests.archive_owner import OWNER, seed_owner
from tests.test_saved_trips import payload

OTHER = "b" * 32


class IdentityStorage(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        self.path = self.folder / "private.sqlite3"
        self.engine = engine_for(str(self.path))
        seed_owner(self.engine)
        seed_owner(self.engine, OTHER)

    def tearDown(self):
        self.engine.dispose()
        engine_for.cache_clear()
        self.temp.cleanup()

    def test_same_operation_is_scoped_to_owner_and_direct_ids_do_not_leak(self):
        request = SaveTrip.model_validate_json(json.dumps(payload()), strict=True)
        with Session(self.engine) as session:
            a = save(session, request, OWNER)
            b = save(session, request, OTHER)
            self.assertNotEqual(a.id, b.id)
            self.assertEqual(save(session, request, OWNER).id, a.id)
            self.assertEqual([r.id for r in listing(session, 0, 10, OWNER).items], [a.id])
            self.assertEqual([r.id for r in listing(session, 0, 10, OTHER).items], [b.id])
            with self.assertRaises(ArchiveError) as raised:
                get_record(session, a.id, OTHER)
            self.assertEqual((raised.exception.status, raised.exception.code), (404, "not_found"))
            changed = request.model_copy(update={"name": "另一用户自己的内容"})
            with self.assertRaises(ArchiveError) as raised:
                save(session, changed, OWNER)
            self.assertEqual(raised.exception.code, "operation_conflict")

    def test_concurrent_retry_and_new_owner_do_not_merge_records(self):
        request = SaveTrip.model_validate_json(json.dumps(payload()), strict=True)
        def submit(owner):
            with Session(self.engine) as session:
                return owner, save(session, request, owner).id
        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(submit, [OWNER, OTHER] * 3))
        self.assertEqual(len({record_id for owner, record_id in results if owner == OWNER}), 1)
        self.assertEqual(len({record_id for owner, record_id in results if owner == OTHER}), 1)
        self.assertEqual(len({record_id for _, record_id in results}), 2)

    def test_missing_identity_rejected_and_failed_commit_rolls_back(self):
        request = SaveTrip.model_validate_json(json.dumps(payload()), strict=True)
        with Session(self.engine) as session:
            for invoke in [lambda: save(session, request, ""), lambda: listing(session, 0, 10, ""), lambda: get_record(session, "a" * 32, "")]:
                with self.assertRaises(ArchiveError) as raised:
                    invoke()
                self.assertEqual(raised.exception.status, 401)
            with patch.object(session, "commit", side_effect=OperationalError("private SQL", {}, Exception("private path"))):
                with self.assertRaises(ArchiveError) as raised:
                    save(session, request, OWNER)
                self.assertEqual(str(raised.exception), "storage_unavailable")
            self.assertEqual(listing(session, 0, 10, OWNER).items, [])

    def test_reopen_preserves_identity_schema_and_owned_records(self):
        request = SaveTrip.model_validate_json(json.dumps(payload()), strict=True)
        with Session(self.engine) as session:
            result = save(session, request, OWNER)
        self.engine.dispose(); engine_for.cache_clear()
        self.engine = engine_for(str(self.path))
        with Session(self.engine) as session:
            self.assertEqual(get_record(session, result.id, OWNER), result)
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute("SELECT value FROM storage_metadata WHERE key='version'").fetchone(), (STORAGE_VERSION,))

    def test_original_database_and_relative_paths_are_refused_before_open(self):
        for target in [str(LEGACY_PATH), "relative.sqlite3"]:
            with self.assertRaises(StorageError): engine_for(target)
            with patch.dict("os.environ", {"TRIPPILOT_DATABASE_PATH": target}):
                with self.assertRaises(StorageError): database_path()

    def test_fixed_dotenv_path_and_process_override_are_supported(self):
        configuration = self.folder / "controlled.env"
        configured_database = self.folder / "from-config.sqlite3"
        configuration.write_text(f"TRIPPILOT_DATABASE_PATH={configured_database}\nUNRELATED_VARIABLE=ignored\n")
        parsed = StorageSettings(_env_file=configuration)
        self.assertEqual(parsed.trippilot_database_path, str(configured_database))
        with patch.dict("os.environ", {"TRIPPILOT_DATABASE_PATH": str(self.path)}):
            self.assertEqual(StorageSettings(_env_file=configuration).trippilot_database_path, str(self.path))
            self.assertEqual(database_path(), self.path)

    def test_configured_question_and_fragment_characters_are_literal_filename(self):
        parent = self.folder / "literal?#directory"
        target = parent / "private?mode=other#fragment.sqlite3"
        engine = engine_for(str(target))
        seed_owner(engine)
        request = SaveTrip.model_validate_json(json.dumps(payload()), strict=True)
        with Session(engine) as session:
            saved = save(session, request, OWNER)
        engine.dispose(); engine_for.cache_clear()
        self.assertEqual({entry.name for entry in parent.iterdir()}, {target.name})
        with closing(sqlite3.connect(target)) as db:
            self.assertEqual(db.execute("SELECT id FROM saved_trips").fetchone(), (saved.id,))
        reopened = engine_for(str(target))
        with Session(reopened) as session:
            self.assertEqual(get_record(session, saved.id, OWNER), saved)
        reopened.dispose()

    def test_version_marker_does_not_bypass_owner_and_idempotency_constraints(self):
        columns = ["id VARCHAR NOT NULL PRIMARY KEY", "owner_id VARCHAR NULL REFERENCES accounts(id)",
                   "operation_id VARCHAR NOT NULL", "name VARCHAR NOT NULL", "saved_at VARCHAR NOT NULL",
                   "schema_version VARCHAR NOT NULL", "start_date VARCHAR NOT NULL", "end_date VARCHAR NOT NULL",
                   "transport_mode VARCHAR NOT NULL", "status VARCHAR NOT NULL", "snapshot_json VARCHAR NOT NULL"]
        unique = "CONSTRAINT uq_saved_trips_owner_operation UNIQUE (owner_id, operation_id)"
        cases = {
            "missing_unique": columns,
            "partial_unique": columns,
            "global_unique": columns + ["UNIQUE (operation_id)"],
            "extra_global_unique": columns + [unique, "UNIQUE (operation_id)"],
            "extra_field": columns + ["client_owner VARCHAR", unique],
            "missing_owner": [column for column in columns if not column.startswith("owner_id ")] + ["UNIQUE (operation_id)"],
            "missing_fk": [column.replace(" REFERENCES accounts(id)", "") for column in columns] + [unique],
            "wrong_fk": [column.replace("REFERENCES accounts(id)", "REFERENCES accounts(username)") for column in columns] + [unique],
            "nonnull_owner": [column.replace("owner_id VARCHAR NULL", "owner_id VARCHAR NOT NULL") for column in columns] + [unique],
        }
        for name, definition in cases.items():
            with self.subTest(name=name):
                target = self.folder / f"tampered-{name}.sqlite3"
                with closing(sqlite3.connect(target)) as db:
                    db.execute("CREATE TABLE storage_metadata (key VARCHAR NOT NULL PRIMARY KEY, value VARCHAR NOT NULL)")
                    db.execute("INSERT INTO storage_metadata VALUES ('version', ?)", (STORAGE_VERSION,))
                    db.execute("CREATE TABLE saved_trips (" + ", ".join(definition) + ")")
                    if name == "partial_unique":
                        db.execute("CREATE UNIQUE INDEX partial_owner_operation ON saved_trips (owner_id, operation_id) WHERE owner_id IS NULL")
                    db.commit()
                with self.assertRaises(StorageError) as raised:
                    engine_for(str(target))
                self.assertEqual(raised.exception.code, "invalid_storage_schema")
                with closing(sqlite3.connect(target)) as db:
                    self.assertNotIn("accounts", {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")})


def make_legacy(path: Path) -> tuple[str, str]:
    request = SaveTrip.model_validate_json(json.dumps(payload()), strict=True)
    content = canonical(request.snapshot)
    names = ("id", "operation_id", "name", "saved_at", "schema_version", "start_date", "end_date", "transport_mode", "status", "snapshot_json")
    values = ("c" * 32, request.operation_id, request.name, "2026-10-01T01:02:03+00:00", request.snapshot.schema_version,
              request.snapshot.schedule.request.start_date.isoformat(), request.snapshot.schedule.request.end_date.isoformat(),
              request.snapshot.schedule.request.transport_mode, request.snapshot.schedule.status, content)
    with closing(sqlite3.connect(path)) as db:
        db.execute("CREATE TABLE saved_trips (id VARCHAR PRIMARY KEY NOT NULL, operation_id VARCHAR UNIQUE NOT NULL, name VARCHAR NOT NULL, saved_at VARCHAR NOT NULL, schema_version VARCHAR NOT NULL, start_date VARCHAR NOT NULL, end_date VARCHAR NOT NULL, transport_mode VARCHAR NOT NULL, status VARCHAR NOT NULL, snapshot_json VARCHAR NOT NULL)")
        db.execute(f"INSERT INTO saved_trips ({','.join(names)}) VALUES ({','.join('?' for _ in names)})", values)
        db.commit()
    return values[0], content


class IdentityMigration(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.folder = Path(self.temp.name)
        self.source = self.folder / "legacy.sqlite3"
        self.destination = self.folder / "private.sqlite3"
        self.backup = self.folder / "pre-identity.sqlite3"
        self.record_id, self.content = make_legacy(self.source)
        self.original_hash = hashlib.sha256(self.source.read_bytes()).hexdigest()

    def tearDown(self):
        engine_for.cache_clear(); self.temp.cleanup()

    def test_explicit_copy_migrates_without_mutating_source_and_quarantines_legacy(self):
        with self.assertRaises(StorageError) as raised: engine_for(str(self.source))
        self.assertEqual(raised.exception.code, "storage_migration_required")
        result = migrate_legacy_copy(self.source, self.destination, self.backup)
        self.assertEqual(result, {"storage_version": STORAGE_VERSION, "quarantined_records": 1})
        self.assertEqual(hashlib.sha256(self.source.read_bytes()).hexdigest(), self.original_hash)
        engine = engine_for(str(self.destination)); seed_owner(engine)
        with Session(engine) as session:
            row = session.get(SavedTripRow, self.record_id)
            self.assertIsNone(row.owner_id)
            self.assertEqual(row.snapshot_json, self.content)
            self.assertEqual(row.saved_at, "2026-10-01T01:02:03+00:00")
            self.assertEqual(listing(session, 0, 20, OWNER).items, [])
            with self.assertRaises(ArchiveError): get_record(session, self.record_id, OWNER)
            retried = payload()
            retried["operation_id"] = row.operation_id
            created = save(session, SaveTrip.model_validate_json(json.dumps(retried), strict=True), OWNER)
            self.assertNotEqual(created.id, self.record_id)
            self.assertIsNone(session.get(SavedTripRow, self.record_id).owner_id)
        engine.dispose()

    def test_repeat_never_overwrites_and_rollback_restores_a_new_copy(self):
        migrate_legacy_copy(self.source, self.destination, self.backup)
        before = self.destination.read_bytes()
        with self.assertRaises(StorageError) as raised: migrate_legacy_copy(self.source, self.destination, self.backup)
        self.assertEqual(raised.exception.code, "migration_target_exists")
        self.assertEqual(self.destination.read_bytes(), before)
        restored = self.folder / "rollback.sqlite3"
        restore_legacy_backup(self.backup, restored)
        with closing(sqlite3.connect(restored)) as db:
            self.assertEqual(db.execute("SELECT snapshot_json FROM saved_trips").fetchone(), (self.content,))
            self.assertNotIn("owner_id", {r[1] for r in db.execute("PRAGMA table_info(saved_trips)")})
        with self.assertRaises(StorageError): restore_legacy_backup(self.backup, restored)
        self.assertEqual(hashlib.sha256(self.source.read_bytes()).hexdigest(), self.original_hash)

    def test_schema_conflicts_and_unknown_versions_fail_safely(self):
        with closing(sqlite3.connect(self.source)) as db:
            db.execute("CREATE TABLE unexpected (id INTEGER)"); db.commit()
        with self.assertRaises(StorageError) as raised: migrate_legacy_copy(self.source, self.destination, self.backup)
        self.assertEqual(raised.exception.code, "migration_source_schema_unsupported")
        self.assertFalse(self.destination.exists()); self.assertFalse(self.backup.exists())
        unknown = self.folder / "unknown.sqlite3"
        with closing(sqlite3.connect(unknown)) as db:
            db.execute("CREATE TABLE storage_metadata (key TEXT PRIMARY KEY, value TEXT)")
            db.execute("INSERT INTO storage_metadata VALUES ('version', 'future')"); db.commit()
        with self.assertRaises(StorageError) as raised: engine_for(str(unknown))
        self.assertEqual(raised.exception.code, "unsupported_storage_version")
