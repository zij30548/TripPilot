"""Real InnoDB registration/ownership; independent temporary loopback databases.

No upstream API, original SQLite database or deployed server is used. Omit the
explicit test-server setting to skip, never silently substitute SQLite for MySQL.
"""
import hashlib
import io
import json
import secrets
from threading import Barrier, Event
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
from unittest.mock import patch

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import inspect, text
from sqlalchemy.exc import OperationalError
from sqlmodel import Session, select

from core import auth
from api.auth import router
from memory.identity_models import AccountRow, InvitationRow, LoginBucketRow, SessionRow
from memory.mysql_storage import (MYSQL_STORAGE_VERSION, current_mysql_engine, initialize_private_schema,
                               mysql_configured, validate_private_schema, validated_url)
from memory.saved_trips import ArchiveError, get_record, listing, save
from core.schemas.saved_trip import SaveTrip
from memory.storage import StorageError
from tests.mysql_support import temporary_mysql
from tests.test_saved_trips import payload
from tools import invitation_admin

PASSWORD = "controlled-registration-passphrase"
ORIGIN = "http://127.0.0.1:3000"


class MySQLConfiguration(unittest.TestCase):
    def test_strict_selection_and_secret_setting_never_falls_back(self):
        for url in ("", "sqlite:///private.db", "mysql://u:p@localhost/db", "mysql+pymysql://u@localhost/db",
                    "mysql+pymysql://u:p@localhost/db?local_infile=1", "mysql+pymysql://u:p@localhost/db#fragment",
                    "mysql+pymysql://u:p@localhost/db/extra", "mysql+pymysql://u:p@localhost/db?charset=latin1"):
            with self.subTest(kind=url.split(":")[0]):
                with self.assertRaises(StorageError) as raised:
                    validated_url(url)
                self.assertEqual(str(raised.exception), "invalid_mysql_configuration")
                self.assertNotIn(url, str(raised.exception)) if url else None
        valid = validated_url("mysql+pymysql://application:placeholder@127.0.0.1:3306/trippilot")
        self.assertEqual(valid.query, {"charset": "utf8mb4"})
        with patch("memory.mysql_storage._configured_url", return_value="malformed"), patch("memory.storage.engine_for", side_effect=AssertionError("No fallback")):
            from memory.storage import current_engine
            with self.assertRaises(StorageError):
                current_engine()

    def test_sqlite_registration_is_explicitly_unavailable_without_mutation(self):
        with patch("core.auth.mysql_configured", return_value=False), patch("core.auth.identity_engine", side_effect=AssertionError("No SQLite registration write")):
            with self.assertRaises(HTTPException) as raised:
                auth.register_account("new-user", PASSWORD, "a" * 43)
            self.assertEqual(raised.exception.status_code, 503)


class MySQLIdentity(unittest.TestCase):
    def setUp(self):
        self.engine, self.cleanup = temporary_mysql()
        initialize_private_schema(self.engine)
        self.patches = [patch("core.auth.identity_engine", return_value=self.engine),
                        patch("core.auth.current_mysql_engine", return_value=self.engine),
                        patch("core.auth.mysql_configured", return_value=True)]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.cleanup()

    def invitation(self):
        return auth.create_invitation()

    def test_schema_capacity_charset_and_no_implicit_reinitialization(self):
        validate_private_schema(self.engine)
        self.assertEqual(inspect(self.engine).get_columns("saved_trips")[-1]["name"], "snapshot_json")
        with self.assertRaises(StorageError) as raised:
            initialize_private_schema(self.engine)
        self.assertEqual(raised.exception.code, "mysql_target_not_empty")
        with self.engine.begin() as db:
            db.execute(text("UPDATE storage_metadata SET value='future' WHERE `key`='version'"))
        with self.assertRaises(StorageError):
            validate_private_schema(self.engine)

    def test_creation_redemption_password_hash_and_normal_login(self):
        identifier, token = self.invitation()
        with Session(self.engine) as db:
            row = db.get(InvitationRow, identifier)
            self.assertEqual(row.token_hash, hashlib.sha256(token.encode()).hexdigest())
            self.assertNotEqual(row.token_hash, token)
        auth.register_account("registered-a", PASSWORD, token)
        with Session(self.engine) as db:
            row = db.exec(select(AccountRow)).one()
            invite = db.get(InvitationRow, identifier)
            self.assertEqual(invite.consumed_by, row.id)
            self.assertIsNotNone(invite.consumed_at)
            self.assertNotIn(PASSWORD, row.password_hash)
            self.assertTrue(row.password_hash.startswith("$argon2id$"))
            self.assertEqual(db.exec(select(SessionRow)).all(), [])
        result, _ = auth.login("registered-a", PASSWORD, None)
        self.assertTrue(result["authenticated"])

    def test_concurrent_logins_keep_eight_session_limit_with_current_reads(self):
        owner = auth.create_account("session-user", PASSWORD)
        now = auth.now_seconds()
        with Session(self.engine) as db:
            for index in range(7):
                db.add(SessionRow(id=f"{index:032x}", token_hash=f"{index:064x}", user_id=owner,
                                  csrf_token="a" * 43, created_at=now - 10, expires_at=now + 3600))
            db.commit()
        real_hasher = auth.PASSWORD_HASHER
        verified = Barrier(2)
        def verify(encoded, password):
            result = real_hasher.verify(encoded, password)
            verified.wait(timeout=5)
            return result
        # Both calls verify the same initial account before either acquires its
        # admission lock. No mock session/database responses are substituted.
        with patch("core.auth.PASSWORD_HASHER", wraps=real_hasher) as hasher:
            hasher.verify.side_effect = verify
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(lambda _: auth.login("session-user", PASSWORD, None), range(2)))
        self.assertTrue(all(result[0]["authenticated"] for result in results))
        with Session(self.engine) as db:
            self.assertEqual(len(db.exec(select(SessionRow).where(SessionRow.user_id == owner)).all()), 8)

    def test_disable_committed_during_password_check_prevents_new_session(self):
        owner = auth.create_account("disabled-during-login", PASSWORD)
        real_hasher = auth.PASSWORD_HASHER
        verified, proceed = Event(), Event()
        def verify(encoded, password):
            result = real_hasher.verify(encoded, password)
            verified.set()
            if not proceed.wait(timeout=5):
                raise AssertionError("Controlled disable did not complete")
            return result
        with patch("core.auth.PASSWORD_HASHER", wraps=real_hasher) as hasher:
            hasher.verify.side_effect = verify
            with ThreadPoolExecutor(max_workers=1) as pool:
                result = pool.submit(auth.login, "disabled-during-login", PASSWORD, None)
                try:
                    self.assertTrue(verified.wait(timeout=5))
                    auth.revoke_account("disabled-during-login", disable=True)
                finally:
                    proceed.set()
                with self.assertRaises(HTTPException) as raised:
                    result.result(timeout=5)
        self.assertEqual(raised.exception.status_code, 401)
        with Session(self.engine) as db:
            self.assertTrue(db.get(AccountRow, owner).disabled)
            self.assertEqual(db.exec(select(SessionRow).where(SessionRow.user_id == owner)).all(), [])

    def test_changed_password_hash_after_verification_is_not_used_to_publish_session(self):
        owner = auth.create_account("changed-password", PASSWORD)
        real_hasher = auth.PASSWORD_HASHER
        original_hash = None
        with Session(self.engine) as db:
            original_hash = db.get(AccountRow, owner).password_hash
        def verify(encoded, password):
            result = real_hasher.verify(encoded, password)
            with Session(self.engine) as db:
                row = db.get(AccountRow, owner)
                row.password_hash = real_hasher.hash("controlled-new-password")
                db.add(row)
                db.commit()
            return result
        with patch("core.auth.PASSWORD_HASHER", wraps=real_hasher) as hasher:
            hasher.verify.side_effect = verify
            with self.assertRaises(HTTPException) as raised:
                auth.login("changed-password", PASSWORD, None)
        self.assertEqual(raised.exception.status_code, 401)
        with Session(self.engine) as db:
            self.assertNotEqual(db.get(AccountRow, owner).password_hash, original_hash)
            self.assertEqual(db.exec(select(SessionRow).where(SessionRow.user_id == owner)).all(), [])

    def test_same_invitation_concurrency_creates_exactly_one_account(self):
        _, token = self.invitation()
        def submit(username):
            try:
                auth.register_account(username, PASSWORD, token)
                return "success"
            except HTTPException as error:
                return error.detail["code"]
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(submit, ["contender-a", "contender-b"]))
        self.assertEqual(results.count("success"), 1)
        self.assertEqual(results.count("registration_rejected"), 1)
        with Session(self.engine) as db:
            self.assertEqual(len(db.exec(select(AccountRow)).all()), 1)
            self.assertEqual(len(db.exec(select(InvitationRow).where(InvitationRow.consumed_by.is_not(None))).all()), 1)

    def test_same_username_with_two_invites_consumes_only_successful_invite(self):
        invitations = [self.invitation(), self.invitation()]
        def submit(value):
            try:
                auth.register_account("one-username", PASSWORD, value[1])
                return "success"
            except HTTPException as error:
                return error.detail["code"]
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(submit, invitations))
        self.assertEqual(results.count("success"), 1)
        self.assertEqual(results.count("registration_rejected"), 1)
        with Session(self.engine) as db:
            self.assertEqual(len(db.exec(select(AccountRow)).all()), 1)
            self.assertEqual(len(db.exec(select(InvitationRow).where(InvitationRow.consumed_by.is_(None))).all()), 1)

    def test_unready_schema_is_retained_but_runtime_validation_rejects(self):
        engine, cleanup = temporary_mysql()
        try:
            initialize_private_schema(engine, ready=False)
            with self.assertRaises(StorageError) as raised:
                validate_private_schema(engine)
            self.assertEqual(raised.exception.code, "unsupported_mysql_storage_version")
            self.assertTrue(inspect(engine).has_table("accounts"))
            with engine.begin() as db:
                db.execute(text("INSERT INTO storage_metadata (`key`, value) VALUES ('version', :version)"),
                           {"version": MYSQL_STORAGE_VERSION})
            validate_private_schema(engine)
            with engine.begin() as db:
                db.execute(text("ALTER TABLE invitations ADD unexpected VARCHAR(20)"))
            with self.assertRaises(StorageError):
                validate_private_schema(engine)
        finally:
            cleanup()

    def test_username_conflict_and_commit_failure_do_not_consume_invite(self):
        auth.create_account("occupied-user", PASSWORD)
        identifier, token = self.invitation()
        with self.assertRaises(HTTPException):
            auth.register_account("occupied-user", PASSWORD, token)
        with Session(self.engine) as db:
            self.assertIsNone(db.get(InvitationRow, identifier).consumed_by)
        # The limiter's short transaction is independent. Fail only the account
        # transaction COMMIT and assert both account and consumption roll back.
        original = Session.commit
        def fail_account_commit(db):
            if any(isinstance(row, AccountRow) for row in db.identity_map.values()):
                raise OperationalError("controlled", {}, Exception("not disclosed"))
            return original(db)
        with patch.object(Session, "commit", fail_account_commit):
            with self.assertRaises(OperationalError):
                auth.register_account("rollback-user", PASSWORD, token)
        with Session(self.engine) as db:
            self.assertIsNone(db.get(InvitationRow, identifier).consumed_by)
            self.assertIsNone(db.exec(select(AccountRow).where(AccountRow.username == "rollback-user")).first())
        auth.register_account("committed-user", PASSWORD, token)

    def test_expired_revoked_used_and_unknown_invites_are_rejected(self):
        expired_id, expired = self.invitation()
        revoked_id, revoked = self.invitation()
        with Session(self.engine) as db:
            row = db.get(InvitationRow, expired_id)
            row.expires_at = auth.now_seconds() - 1
            db.add(row)
            db.commit()
        auth.revoke_invitation(revoked_id)
        errors = []
        for index, token in enumerate([expired, revoked, secrets.token_urlsafe(32)]):
            with self.assertRaises(HTTPException) as raised:
                auth.register_account(f"rejected-{index}", PASSWORD, token)
            errors.append(raised.exception.detail)
        self.assertEqual(errors[0], errors[1])
        self.assertEqual(errors[0], errors[2])
        with Session(self.engine) as db:
            self.assertEqual(db.exec(select(AccountRow)).all(), [])

    def test_persistent_registration_limit_before_hashing_and_shared_hash_capacity(self):
        _, token = self.invitation()
        with patch("core.auth._HASH_CAPACITY.acquire", return_value=False):
            with self.assertRaises(HTTPException) as raised:
                auth.register_account("busy-user", PASSWORD, token)
            self.assertEqual(raised.exception.detail["code"], "registration_busy")
        for index in range(9):
            auth.reserve_identity_attempt("registration", f"user-{index}")
        self.engine.dispose()
        with patch("core.auth.PASSWORD_HASHER") as hasher:
            hasher.hash.side_effect = AssertionError("Limit before expensive work")
            with self.assertRaises(HTTPException) as raised:
                auth.register_account("over-limit", PASSWORD, token)
            self.assertEqual(raised.exception.detail["code"], "registration_rate_limited")
        with Session(self.engine) as db:
            self.assertEqual(len(db.exec(select(LoginBucketRow)).all()), 11)

    def test_mysql_owned_archives_and_concurrent_idempotency(self):
        a = auth.create_account("owner-a", PASSWORD)
        b = auth.create_account("owner-b", PASSWORD)
        value = SaveTrip.model_validate_json(json.dumps(payload()), strict=True)
        def submit(owner):
            with Session(self.engine) as db:
                return owner, save(db, value, owner).id
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(submit, [a, b, a, b]))
        self.assertEqual(len({record for owner, record in results if owner == a}), 1)
        self.assertEqual(len({record for owner, record in results if owner == b}), 1)
        aid = next(record for owner, record in results if owner == a)
        with Session(self.engine) as db:
            self.assertEqual(len(listing(db, 0, 10, a).items), 1)
            with self.assertRaises(ArchiveError) as raised:
                get_record(db, aid, b)
            self.assertEqual(raised.exception.status, 404)

    def test_register_asgi_csrf_strict_body_no_auto_login_or_business_execution(self):
        settings = auth.AuthSettings(_env_file=None, auth_cookie_secure=False, auth_allowed_origins=(ORIGIN,))
        app = FastAPI()
        app.add_middleware(auth.AuthenticationMiddleware)
        app.include_router(router)
        _, token = self.invitation()
        value = {"username": "web-user", "password": PASSWORD, "invitation_code": token}
        with patch("core.auth.get_auth_settings", return_value=settings), patch("api.auth.get_auth_settings", return_value=settings), TestClient(app) as client:
            response = client.get("/auth/session")
            headers = {"origin": ORIGIN, "x-csrf-token": response.json()["csrf_token"]}
            self.assertEqual(client.post("/auth/register", json=value).status_code, 403)
            self.assertEqual(client.post("/auth/register", json={**value, "owner_id": "forged"}, headers=headers).status_code, 422)
            self.assertEqual(client.post("/auth/register", content=b"x" * 4097, headers={**headers, "content-type": "application/json"}).status_code, 413)
            response = client.post("/auth/register", json=value, headers=headers)
            self.assertEqual(response.json(), {"registered": True})
            self.assertIn("no-store", response.headers["cache-control"])
            self.assertNotIn("set-cookie", response.headers)
            self.assertFalse(client.get("/auth/session").json()["authenticated"])

    def test_invitation_cli_requires_tty_and_only_exposes_secret_once(self):
        with patch("sys.argv", ["invitation_admin", "create"]), patch("sys.stdin.isatty", return_value=False), patch("tools.invitation_admin.create_invitation", side_effect=AssertionError("Do not create")):
            self.assertEqual(invitation_admin.main(), 2)
        class TerminalOutput(io.StringIO):
            def isatty(self):
                return True
        output = TerminalOutput()
        with patch("sys.argv", ["invitation_admin", "create", "--valid-hours", "12"]), patch("sys.stdin.isatty", return_value=True), patch("sys.stdout.isatty", return_value=True), redirect_stdout(output):
            self.assertEqual(invitation_admin.main(), 0)
        self.assertEqual(output.getvalue().count("One-time invitation code"), 1)


if __name__ == "__main__":
    unittest.main()
