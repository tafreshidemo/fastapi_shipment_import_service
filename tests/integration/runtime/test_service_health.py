from __future__ import annotations

import logging
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI, Query
from httpx import ASGITransport, AsyncClient

from app.api.errors import register_exception_handlers
from tests.support.docker_runtime import (
    DockerRuntimeStack,
    docker_compose,
    inspect_health,
    ping_worker,
)


def test_runtime_stack_starts_and_runs_services(
    docker_runtime_stack: DockerRuntimeStack,
) -> None:
    postgres_health = inspect_health(docker_runtime_stack.project_root, "postgres")
    rabbitmq_health = inspect_health(docker_runtime_stack.project_root, "rabbitmq")
    api_health = inspect_health(docker_runtime_stack.project_root, "api")
    worker_health = inspect_health(docker_runtime_stack.project_root, "worker")
    api_response = httpx.get(f"{docker_runtime_stack.api_url}/api/v1/health", timeout=10)
    worker_ping = ping_worker(docker_runtime_stack.project_root)
    registered_tasks = docker_compose(
        docker_runtime_stack.project_root,
        "exec",
        "-T",
        "worker",
        "/bin/sh",
        "-lc",
        'celery -A app.celery_app:celery_app inspect registered -d "celery@$HOSTNAME"',
    )
    migrate_ps = docker_compose(docker_runtime_stack.project_root, "ps", "-a", "migrate")

    assert postgres_health["Status"] == "healthy"
    assert rabbitmq_health["Status"] == "healthy"
    assert api_health["Status"] == "healthy"
    assert worker_health["Status"] == "healthy"
    assert api_response.status_code == 200
    assert api_response.json() == {"status": "ok"}
    assert "OK" in worker_ping.stdout
    assert "app.workers.tasks.process_import" not in registered_tasks.stdout
    assert "Exit 0" in migrate_ps.stdout or "exited (0)" in migrate_ps.stdout.lower()


@pytest.mark.asyncio
async def test_error_contract_covers_validation_http_and_unexpected_errors(
    caplog: pytest.LogCaptureFixture,
) -> None:
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/validation")
    async def validation(limit: int = Query(..., ge=1)) -> dict[str, int]:
        return {"limit": limit}

    @app.get("/boom")
    async def boom() -> None:
        raise RuntimeError("hidden failure")

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    caplog.set_level(logging.ERROR)

    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        validation_response = await client.get("/validation", params={"limit": 0})
        not_found_response = await client.get("/missing")
        boom_response = await client.get("/boom")

    assert validation_response.status_code == 422
    assert validation_response.json()["error"]["code"] == "INVALID_REQUEST"
    assert validation_response.json()["error"]["message"] == "Request validation failed."
    assert isinstance(validation_response.json()["error"]["details"], list)

    assert not_found_response.status_code == 404
    assert not_found_response.json() == {
        "error": {
            "code": "HTTP_ERROR",
            "message": "Not Found",
            "details": None,
        }
    }

    assert boom_response.status_code == 500
    assert boom_response.json() == {
        "error": {
            "code": "INTERNAL_ERROR",
            "message": "An internal server error occurred.",
            "details": None,
        }
    }
    assert "Unhandled API exception" in caplog.text


def test_runtime_files_exist_for_compose_and_migrations() -> None:
    project_root = Path(__file__).resolve().parents[3]
    versions_dir = project_root / "migrations" / "versions"

    assert (project_root / "docker-compose.yml").exists()
    assert (project_root / "alembic.ini").exists()
    assert versions_dir.exists()
    assert any(version.suffix == ".py" for version in versions_dir.iterdir())
