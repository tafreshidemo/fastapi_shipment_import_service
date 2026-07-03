from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

from sqlalchemy import create_engine
from sqlalchemy.engine import make_url


def create_temporary_database_url(base_url: str) -> str:
    base = make_url(base_url)
    server_url = base.set(database="postgres")
    database_name = f"step2_{uuid4().hex}"
    engine = create_engine(server_url, isolation_level="AUTOCOMMIT")
    with engine.connect() as connection:
        connection.exec_driver_sql(f'CREATE DATABASE "{database_name}"')
    engine.dispose()
    return base.set(database=database_name).render_as_string(hide_password=False)


def drop_temporary_database(base_url: str, database_url: str) -> None:
    database_name = make_url(database_url).database
    if database_name is None:
        return
    engine = create_engine(
        make_url(base_url).set(database="postgres"),
        isolation_level="AUTOCOMMIT",
    )
    with engine.connect() as connection:
        connection.exec_driver_sql(
            """
            SELECT pg_terminate_backend(pid)
            FROM pg_stat_activity
            WHERE datname = %s AND pid <> pg_backend_pid()
            """,
            (database_name,),
        )
        connection.exec_driver_sql(f'DROP DATABASE IF EXISTS "{database_name}"')
    engine.dispose()


def run_alembic(
    project_root: Path,
    database_url: str,
    *args: str,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["DATABASE_URL"] = database_url
    env.setdefault("APP_ENV", "test")
    result = subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=project_root,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    if result.returncode != 0:
        raise AssertionError(
            "alembic command failed:\n"
            f"args: {args}\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )
    return result
