from __future__ import annotations

import threading
from dataclasses import asdict, dataclass

import anyio
import pytest
from fastapi import APIRouter, FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.core.settings import Settings
from app.db.session import build_session_factory
from tests.support.docker_runtime import DockerRuntimeStack


@dataclass(frozen=True)
class BoundaryResult:
    session_id: int
    event_loop_thread_name: str
    created_thread_name: str
    used_thread_name: str
    closed_thread_name: str
    query_result: int
    session_closed_before_return: bool


def build_test_app(postgres_url: str) -> tuple[FastAPI, dict[str, str | None]]:
    settings = Settings(database_url=postgres_url)
    session_factory = build_session_factory(settings)
    router = APIRouter()
    failure_state: dict[str, str | None] = {
        "event_loop_thread_name": None,
        "created_thread_name": None,
        "used_thread_name": None,
        "closed_thread_name": None,
    }

    def collect_boundary_data(event_loop_thread_name: str) -> BoundaryResult:
        created_thread_name = threading.current_thread().name
        session: Session = session_factory()
        used_thread_name = threading.current_thread().name
        try:
            query_result = session.execute(text("SELECT 1")).scalar_one()
        finally:
            session.close()
            closed_thread_name = threading.current_thread().name
        return BoundaryResult(
            session_id=id(session),
            event_loop_thread_name=event_loop_thread_name,
            created_thread_name=created_thread_name,
            used_thread_name=used_thread_name,
            closed_thread_name=closed_thread_name,
            query_result=query_result,
            session_closed_before_return=True,
        )

    @router.get("/boundary")
    async def boundary() -> dict[str, object]:
        event_loop_thread_name = threading.current_thread().name
        result = await run_in_threadpool(collect_boundary_data, event_loop_thread_name)
        return asdict(result)

    def fail_boundary(event_loop_thread_name: str) -> None:
        failure_state["event_loop_thread_name"] = event_loop_thread_name
        failure_state["created_thread_name"] = threading.current_thread().name
        session: Session = session_factory()
        failure_state["used_thread_name"] = threading.current_thread().name
        try:
            session.execute(text("SELECT 1")).scalar_one()
            raise RuntimeError("threadpool failure")
        finally:
            session.close()
            failure_state["closed_thread_name"] = threading.current_thread().name

    @router.get("/boundary-failure")
    async def boundary_failure() -> None:
        event_loop_thread_name = threading.current_thread().name
        await run_in_threadpool(fail_boundary, event_loop_thread_name)

    app = FastAPI()
    app.include_router(router)
    return app, failure_state


async def test_concurrent_boundary_calls_use_distinct_sessions(
    docker_runtime_stack: DockerRuntimeStack,
) -> None:
    app, _ = build_test_app(docker_runtime_stack.postgres_url)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://testserver") as client:

        async def make_request() -> dict[str, object]:
            response = await client.get("/boundary")
            response.raise_for_status()
            return response.json()

        responses: list[dict[str, str | int]] = []

        async def collect_response() -> None:
            responses.append(await make_request())

        async with anyio.create_task_group() as task_group:
            task_group.start_soon(collect_response)
            task_group.start_soon(collect_response)

        first, second = sorted(responses, key=lambda item: int(item["session_id"]))

    assert first["session_id"] != second["session_id"]
    assert first["query_result"] == 1
    assert second["query_result"] == 1
    assert first["created_thread_name"] == first["used_thread_name"] == first["closed_thread_name"]
    assert (
        second["created_thread_name"] == second["used_thread_name"] == second["closed_thread_name"]
    )
    assert first["created_thread_name"] != first["event_loop_thread_name"]
    assert second["created_thread_name"] != second["event_loop_thread_name"]
    assert first["session_closed_before_return"] is True
    assert second["session_closed_before_return"] is True
    assert all(isinstance(value, (int, str, bool)) for value in first.values())
    assert all(isinstance(value, (int, str, bool)) for value in second.values())


async def test_boundary_failure_closes_session_and_propagates_exception(
    docker_runtime_stack: DockerRuntimeStack,
) -> None:
    app, failure_state = build_test_app(docker_runtime_stack.postgres_url)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        with pytest.raises(RuntimeError, match="threadpool failure"):
            await client.get("/boundary-failure")

    assert failure_state["event_loop_thread_name"] is not None
    assert failure_state["created_thread_name"] is not None
    assert failure_state["used_thread_name"] is not None
    assert failure_state["closed_thread_name"] is not None
    assert (
        failure_state["created_thread_name"]
        == failure_state["used_thread_name"]
        == failure_state["closed_thread_name"]
    )
    assert failure_state["created_thread_name"] != failure_state["event_loop_thread_name"]
