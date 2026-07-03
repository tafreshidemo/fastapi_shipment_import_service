from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.app import create_app
from app.core.settings import get_settings
from tests.support.docker_runtime import (
    DockerRuntimeStack,
    docker_compose,
    ping_worker,
    wait_for_service_health,
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
async def async_client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


@pytest.fixture
def event_loop() -> Iterator[asyncio.AbstractEventLoop]:
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="session")
def docker_runtime_stack() -> Iterator[DockerRuntimeStack]:
    project_root = Path(__file__).resolve().parents[1]
    try:
        docker_compose(project_root, "down", "--remove-orphans", "--volumes")
        docker_compose(project_root, "build", "migrate", "api", "worker")
        docker_compose(project_root, "up", "-d", "postgres", "rabbitmq")
        wait_for_service_health(project_root, "postgres")
        wait_for_service_health(project_root, "rabbitmq")
        docker_compose(project_root, "up", "--exit-code-from", "migrate", "migrate")
        docker_compose(project_root, "up", "-d", "api", "worker")
        wait_for_service_health(project_root, "api")
        wait_for_service_health(project_root, "worker")
        ping_worker(project_root)

        yield DockerRuntimeStack(
            project_root=project_root,
            api_url="http://127.0.0.1:8000",
            postgres_url="postgresql+psycopg://postgres:postgres@127.0.0.1:54329/import_service",
            rabbitmq_url="amqp://import_user:import_password@127.0.0.1:56729//",
        )
    finally:
        docker_compose(project_root, "down", "--remove-orphans", "--volumes")
