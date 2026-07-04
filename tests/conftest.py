from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.app import create_app
from app.core.settings import Settings, get_settings
from app.db.session import build_session_factory
from tests.support.docker_runtime import (
    TEST_API_HOST_PORT,
    TEST_POSTGRES_HOST_PORT,
    TEST_RABBITMQ_HOST_PORT,
    DockerRuntimeStack,
    docker_compose,
    ping_worker,
    wait_for_service_health,
)
from tests.support.postgres_database import (
    create_temporary_database_url,
    drop_temporary_database,
    run_alembic,
)


@pytest.fixture(autouse=True)
def clear_settings_cache() -> Iterator[None]:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def app() -> FastAPI:
    return create_app()


@pytest.fixture
async def async_client(
    app: FastAPI,
) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)

    async with AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        yield client


@pytest.fixture
def event_loop() -> Iterator[asyncio.AbstractEventLoop]:
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="session")
def docker_runtime_stack() -> Iterator[DockerRuntimeStack]:
    project_root = Path(__file__).resolve().parents[1]

    docker_compose(
        project_root,
        "down",
        "--remove-orphans",
        "--volumes",
    )

    try:
        # One shared image is used by migrate, API, and worker.
        docker_compose(
            project_root,
            "build",
            "migrate",
        )
        docker_compose(
            project_root,
            "up",
            "-d",
            "postgres",
            "rabbitmq",
        )

        wait_for_service_health(project_root, "postgres")
        wait_for_service_health(project_root, "rabbitmq")

        docker_compose(
            project_root,
            "up",
            "--exit-code-from",
            "migrate",
            "migrate",
        )

        docker_compose(
            project_root,
            "up",
            "-d",
            "api",
            "worker",
        )

        wait_for_service_health(project_root, "api")
        wait_for_service_health(project_root, "worker")
        ping_worker(project_root)

        yield DockerRuntimeStack(
            project_root=project_root,
            api_url=f"http://127.0.0.1:{TEST_API_HOST_PORT}",
            postgres_url=(
                "postgresql+psycopg://postgres:postgres@127.0.0.1:"
                f"{TEST_POSTGRES_HOST_PORT}/import_service"
            ),
            rabbitmq_url=(
                "amqp://import_user:import_password@127.0.0.1:"
                f"{TEST_RABBITMQ_HOST_PORT}//"
            ),
        )
    finally:
        # This tears down only fastapi_technical_assessment_test.
        # The normal development stack remains untouched.
        docker_compose(
            project_root,
            "down",
            "--remove-orphans",
            "--volumes",
        )


@pytest.fixture
def database_url(
    docker_runtime_stack: DockerRuntimeStack,
) -> Iterator[str]:
    database_url = create_temporary_database_url(
        docker_runtime_stack.postgres_url
    )

    try:
        yield database_url
    finally:
        drop_temporary_database(
            docker_runtime_stack.postgres_url,
            database_url,
        )


@pytest.fixture
def migrated_database_url(
    database_url: str,
) -> Iterator[str]:
    project_root = Path(__file__).resolve().parents[1]

    run_alembic(
        project_root,
        database_url,
        "upgrade",
        "head",
    )

    yield database_url


@pytest.fixture
def session_factory(
    migrated_database_url: str,
):
    session_factory = build_session_factory(
        Settings(
            database_url=migrated_database_url
        )
    )
    engine = session_factory.kw["bind"]

    try:
        yield session_factory
    finally:
        engine.dispose()
