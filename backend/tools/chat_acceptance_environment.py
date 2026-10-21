"""Explicit isolated LOCAL MySQL setup for the authorized chat acceptance.

No dotenv, app defaults, upstream HTTP, account bypass or runtime DDL is used.
Preparation is one-shot: partial databases/users/files are retained on failure;
there is deliberately no delete/reset/force operation or automatic retry.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import secrets
import stat
import time
from uuid import uuid4

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine, URL, make_url
from sqlalchemy.exc import SQLAlchemyError

from core.auth import PASSWORD_HASHER
from core.business_usage import SERVICES
from memory.chat_places_storage import ChatPlacesStore
from memory.chat_storage import ChatStore
from memory.identity_models import AccountRow
from memory.mysql_storage import initialize_private_schema, validate_private_schema
from memory.mysql_usage import initialize_usage_schema, validate_usage_schema
from core.schemas.chat import Intent, SubmitTurn
from tooling.travel.chat import render_reply
from tools.chat_places_storage import provision_chat_places_extension
from tools.chat_storage import provision_chat_extension

ROOT = Path(__file__).resolve().parents[2]
VERSION = "chat-acceptance-environment-v1"
CASE_IDS = tuple(f"api{i:02}" for i in range(1, 9)) + ("chrome",)


class Refused(Exception):
    """Fixed safe error only; never expose SQL, passwords or connection URLs."""


def admin_url(raw: str) -> URL:
    try:
        url = make_url(raw)
        if (url.drivername != "mysql+pymysql" or url.host != "127.0.0.1"
                or url.port != 13306 or url.database != "trippilot_test_control"
                or url.query or not url.username):
            raise ValueError()
        return url
    except (ValueError, TypeError, SQLAlchemyError):
        raise Refused("isolated_local_test_mysql_required") from None


def _new(path: Path, value: object) -> None:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False).encode() + b"\n"
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _directory(path: Path) -> None:
    if (not path.is_absolute() or path.exists() or path.is_symlink()
            or path.resolve().is_relative_to(ROOT)
            or any(parent.is_symlink() for parent in path.parents)):
        raise Refused("new_external_private_directory_required")
    parent = path.parent.stat()
    if not stat.S_ISDIR(parent.st_mode) or parent.st_uid != os.geteuid():
        raise Refused("owned_parent_directory_required")
    path.mkdir(mode=0o700)
    if path.stat().st_mode & 0o077:
        raise Refused("private_directory_required")


def usage_limits() -> dict:
    return {name: {"account": 12 if name in {"qwen_text", "amap_poi"} else 0,
                   "global": 12 if name in {"qwen_text", "amap_poi"} else 0}
            for name in SERVICES}


def validate_seeds(seeds: object) -> dict:
    if not isinstance(seeds, dict) or set(seeds) != set(CASE_IDS):
        raise Refused("exact_acceptance_seed_cases_required")
    for seed in seeds.values():
        if (not isinstance(seed, dict) or set(seed) not in ({"kind"}, {"kind", "text", "requirements"})
                or seed.get("kind") not in {"empty", "intent", "places"}
                or seed["kind"] == "empty" and set(seed) != {"kind"}
                or seed["kind"] != "empty" and set(seed) != {"kind", "text", "requirements"}):
            raise Refused("invalid_acceptance_seed")
        if seed["kind"] != "empty":
            try:
                Intent.model_validate(seed["requirements"])
                SubmitTurn(operation_id=str(uuid4()), expected_revision=0, text=seed["text"])
            except (ValueError, TypeError):
                raise Refused("invalid_acceptance_seed") from None
    return seeds


def seed_conversations(engine: Engine, owner: str, seeds: dict) -> dict:
    """Use ordinary storage transitions, with explicitly controlled seed history.

    These seeds are test context, NOT real model results. Only the view is
    exported; expected labels and case IDs never enter conversation messages.
    """
    validate_seeds(seeds)
    chat, places, results = ChatStore(engine), ChatPlacesStore(engine), {}
    for identifier in CASE_IDS:
        seed = seeds[identifier]
        conversation = chat.create(owner, str(uuid4()))
        if seed["kind"] != "empty":
            operation = str(uuid4())
            pending = chat.begin_turn(owner, conversation["id"], operation, 0, seed["text"])
            conversation = chat.finish_turn(owner, conversation["id"], operation, pending["revision"],
                seed["requirements"], render_reply(Intent.model_validate(seed["requirements"]), []), [])
        workspace = places.get(owner, conversation["id"])
        if seed["kind"] == "places":
            workspace = places.set_city(owner, conversation["id"], str(uuid4()), conversation["revision"], 0,
                Intent.model_validate(seed["requirements"]).destination_id)
        results[identifier] = {"seed_kind": seed["kind"], "conversation": conversation, "places": workspace}
    return results


def seed_plan(manifest: dict) -> dict:
    """Project only seed context from the frozen case document, never labels."""
    if manifest.get("schema_version") != "chat12-acceptance-v1":
        raise Refused("acceptance_manifest_required")
    definitions = manifest["seeds"]
    cases = {case["id"]: case for case in manifest["cases"]}
    result = {"chrome": {"kind": "empty"}}
    for identifier in CASE_IDS[:-1]:
        case = cases[identifier]
        seed = definitions[case["seed"]]
        if case["seed"] == "empty":
            result[identifier] = {"kind": "empty"}
        else:
            result[identifier] = {"kind": "places" if case.get("setup", {}).get("set_city") else "intent",
                "text": seed["history"][0]["text"], "requirements": seed["intent"]}
    return validate_seeds(result)


def prepare(output: Path, raw_admin_url: str, seeds: dict) -> dict:
    """Create one new independent test DB, DML identity, two ordinary accounts."""
    url = admin_url(raw_admin_url)
    validate_seeds(seeds)
    _directory(output)
    database = "trippilot_test_" + uuid4().hex
    username, password, identity = "tpacc_" + secrets.token_hex(8), secrets.token_urlsafe(32), uuid4().hex
    runtime_url = url.set(username=username, password=password, database=database).update_query_dict({"charset": "utf8mb4"})
    resources = {"schema_version": VERSION, "database": database, "database_user": username,
                 "host": "127.0.0.1", "port": 13306, "state": "planned", "created_at": int(time.time()),
                 "source_database_read": False, "automatic_cleanup": False}
    _new(output / "resources.json", resources)
    _new(output / "runtime-database.json", {"TRIPPILOT_MYSQL_URL": runtime_url.render_as_string(hide_password=False),
        "BUSINESS_USAGE_IDENTITY_PATH": str(output / "usage.identity"),
        "BUSINESS_DAILY_LIMITS": usage_limits(), "BUSINESS_RATE_ACCOUNT_LIMIT": 60,
        "BUSINESS_RATE_GLOBAL_LIMIT": 300})
    # Anchor is independent and exclusive; never silently recreated by runtime.
    fd = os.open(output / "usage.identity", os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    try:
        os.write(fd, (identity + "\n").encode("ascii")); os.fsync(fd)
    finally:
        os.close(fd)
    admin = create_engine(url, isolation_level="AUTOCOMMIT", hide_parameters=True)
    engine, phase = None, "new_database_and_dml_user"
    try:
        with admin.connect() as db:
            db.execute(text(f"CREATE DATABASE `{database}` CHARACTER SET utf8mb4 COLLATE utf8mb4_bin"))
            # Bound values; only the generated, strictly shaped database is an identifier.
            db.execute(text("CREATE USER :username@'127.0.0.1' IDENTIFIED BY :password"), {"username": username, "password": password})
            db.execute(text(f"GRANT SELECT, INSERT, UPDATE, DELETE ON `{database}`.* TO :username@'127.0.0.1'"), {"username": username})
        engine = create_engine(url.set(database=database).update_query_dict({"charset": "utf8mb4"}),
            hide_parameters=True, isolation_level="READ COMMITTED")
        phase = "private_usage_chat_places_schema"
        initialize_private_schema(engine)
        initialize_usage_schema(engine, identity)
        provision_chat_extension(engine)
        provision_chat_places_extension(engine)
        accounts, phase = {}, "ordinary_accounts"
        with engine.begin() as db:
            for label in ("a", "b"):
                owner, account_name, account_password = uuid4().hex, "accept-" + label + "-" + secrets.token_hex(6), secrets.token_urlsafe(24)
                db.execute(AccountRow.__table__.insert().values(id=owner, username=account_name,
                    password_hash=PASSWORD_HASHER.hash(account_password), disabled=False, created_at=int(time.time())))
                _new(output / ("account-" + label + ".json"), {"username": account_name, "password": account_password})
                accounts[label] = {"id": owner, "username": account_name}
        phase = "controlled_conversation_seeds"
        cases = seed_conversations(engine, accounts["a"]["id"], seeds)
        validate_private_schema(engine)
        with engine.connect() as db:
            validate_usage_schema(db, identity)
            if db.execute(text("SELECT COUNT(*) FROM usage_attempts")).scalar_one() != 0:
                raise Refused("unexpected_preparation_usage")
        # Runtime verification uses the new DML credential, never root/admin.
        phase = "dml_runtime_read_validation"
        runtime = create_engine(runtime_url, hide_parameters=True, isolation_level="READ COMMITTED")
        try:
            validate_private_schema(runtime)
            if ChatStore(runtime).list(accounts["b"]["id"])["items"]:
                raise Refused("unexpected_account_b_data")
        finally:
            runtime.dispose()
        public = {"schema_version": VERSION, "accounts": accounts, "cases": cases,
                  "seed_is_controlled": True, "external_calls": 0, "production_cutover": False}
        _new(output / "public-bindings.json", public)
        _new(output / "prepared.json", {"schema_version": VERSION, "state": "prepared", "external_calls": 0,
            "database": database, "database_user": username, "runtime_permissions": "SELECT,INSERT,UPDATE,DELETE",
            "accounts": 2, "conversations": len(cases), "usage_attempts": 0})
        return public
    except Exception:
        # Never interpolate the exception: a driver may embed SQL/credentials.
        _new(output / "failed.json", {"schema_version": VERSION, "state": "failed_preserve_resources",
            "phase": phase, "automatic_retry": False, "external_calls": 0})
        raise Refused("preparation_failed_preserve_resources") from None
    finally:
        if engine is not None:
            engine.dispose()
        admin.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()
    try:
        # Explicit test-only variable, no BaseSettings or .env read.
        raw = os.environ.get("TRIPPILOT_TEST_MYSQL_ADMIN_URL", "")
        if not args.manifest.is_file() or args.manifest.stat().st_size > 64 * 1024:
            raise Refused("bounded_seed_file_required")
        seeds = seed_plan(json.loads(args.manifest.read_text()))
        prepare(args.output, raw, seeds)
        print("Independent local acceptance environment prepared; private credentials were not printed; external_calls=0.")
    except (Refused, OSError, ValueError, TypeError):
        raise SystemExit("Preparation stopped; preserve resources and inspect the private fixed-code receipt.") from None


if __name__ == "__main__":
    main()
