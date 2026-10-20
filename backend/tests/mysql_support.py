"""Opt-in real MySQL tests. Never use application/production connection settings."""
from __future__ import annotations

import os
import re
import unittest
from uuid import uuid4

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url


def temporary_mysql(*, prefix: str = "trippilot_test_"):
    if prefix not in {"trippilot_test_", "trippilot_restore_"}:
        raise RuntimeError("unsafe_test_database_prefix")
    raw = os.environ.get("TRIPPILOT_TEST_MYSQL_ADMIN_URL")
    if not raw:
        raise unittest.SkipTest("real MySQL test requires isolated loopback test service")
    url = make_url(raw)
    if (url.drivername != "mysql+pymysql" or url.host != "127.0.0.1" or url.port != 13306
            or url.database != "trippilot_test_control" or url.query):
        raise RuntimeError("unsafe_mysql_test_configuration")
    admin = create_engine(url, isolation_level="AUTOCOMMIT", hide_parameters=True)
    name = prefix + uuid4().hex
    with admin.connect() as connection:
        connection.execute(text(f"CREATE DATABASE `{name}` CHARACTER SET utf8mb4 COLLATE utf8mb4_bin"))
    engine = create_engine(url.set(database=name).update_query_dict({"charset": "utf8mb4"}),
                           hide_parameters=True, pool_pre_ping=True)

    def cleanup():
        engine.dispose()
        if re.fullmatch(r"(?:trippilot_test_|trippilot_restore_)[0-9a-f]{32}", name) is None:
            raise RuntimeError("unsafe_test_database_cleanup")
        with admin.connect() as connection:
            connection.execute(text(f"DROP DATABASE `{name}`"))
        admin.dispose()

    return engine, cleanup
