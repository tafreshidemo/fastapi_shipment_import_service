from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

import app.workers.tasks as tasks_module
from app.core.settings import Settings


class RetryRequested(Exception):
    pass


class FakeTask:
    def __init__(self) -> None:
        self.request = SimpleNamespace(hostname="worker-a", retries=1)
        self.retry_calls: list[dict[str, object]] = []

    def retry(self, **kwargs: object) -> None:
        self.retry_calls.append(kwargs)
        raise RetryRequested


def _run_task_with_fake_context(task: FakeTask, import_id: str) -> None:
    task_runner = getattr(
        tasks_module.process_import_task.run,
        "__func__",
        tasks_module.process_import_task.run,
    )
    task_runner(task, import_id)


def test_task_retries_only_retryable_processing_errors(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class RetryableService:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

        def run(self, import_id) -> None:
            raise tasks_module.RetryableImportProcessingError("temporary database failure")

    monkeypatch.setattr(tasks_module, "get_settings", lambda: Settings(import_max_attempts=3))
    monkeypatch.setattr(tasks_module, "get_session_factory", lambda: "worker-session-factory")
    monkeypatch.setattr(tasks_module, "ProcessImportService", RetryableService)

    task = FakeTask()
    with pytest.raises(RetryRequested):
        _run_task_with_fake_context(task, str(uuid4()))

    assert captured["session_factory"] == "worker-session-factory"
    assert captured["worker_id"] == "worker-a"
    assert task.retry_calls[0]["countdown"] == 2
    assert task.retry_calls[0]["max_retries"] == 2


def test_task_propagates_non_retryable_errors_without_calling_celery_retry(monkeypatch) -> None:
    class BrokenService:
        def __init__(self, **_kwargs: object) -> None:
            pass

        def run(self, _import_id) -> None:
            raise RuntimeError("unexpected worker failure")

    monkeypatch.setattr(tasks_module, "get_settings", Settings)
    monkeypatch.setattr(tasks_module, "get_session_factory", lambda: "worker-session-factory")
    monkeypatch.setattr(tasks_module, "ProcessImportService", BrokenService)

    task = FakeTask()
    with pytest.raises(RuntimeError, match="unexpected worker failure"):
        _run_task_with_fake_context(task, str(uuid4()))

    assert task.retry_calls == []
