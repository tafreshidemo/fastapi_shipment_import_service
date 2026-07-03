#!/usr/bin/env sh

set -eu

echo "Waiting for PostgreSQL..."

python - <<'PY'
import os
import time

import psycopg


database_url = os.environ["DATABASE_URL"]

if database_url.startswith("postgresql+psycopg://"):
    database_url = database_url.replace(
        "postgresql+psycopg://",
        "postgresql://",
        1,
    )

max_attempts = 30
delay_seconds = 2

for attempt in range(1, max_attempts + 1):
    try:
        with psycopg.connect(database_url):
            print("PostgreSQL is ready.")
            break
    except psycopg.OperationalError as exc:
        if attempt == max_attempts:
            raise RuntimeError(
                "PostgreSQL did not become ready in time."
            ) from exc

        print(
            f"PostgreSQL is not ready "
            f"({attempt}/{max_attempts}); retrying..."
        )
        time.sleep(delay_seconds)
PY

echo "Applying Alembic migrations..."

alembic upgrade head

echo "Migrations completed successfully."
