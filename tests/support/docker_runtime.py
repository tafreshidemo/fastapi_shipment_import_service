from __future__ import annotations

import json
import os
import shlex
import subprocess
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

TEST_COMPOSE_PROJECT = "fastapi_technical_assessment_test"
TEST_API_HOST_PORT = 18000
TEST_POSTGRES_HOST_PORT = 55432
TEST_RABBITMQ_HOST_PORT = 56730
TEST_RABBITMQ_MANAGEMENT_HOST_PORT = 15630


@dataclass(frozen=True)
class DockerRuntimeStack:
    project_root: Path
    api_url: str
    postgres_url: str
    rabbitmq_url: str


def _test_compose_environment() -> dict[str, str]:
    environment = os.environ.copy()
    environment.update(
        {
            "COMPOSE_PROJECT_NAME": TEST_COMPOSE_PROJECT,
            "API_HOST_PORT": str(TEST_API_HOST_PORT),
            "POSTGRES_HOST_PORT": str(TEST_POSTGRES_HOST_PORT),
            "RABBITMQ_HOST_PORT": str(TEST_RABBITMQ_HOST_PORT),
            "RABBITMQ_MANAGEMENT_HOST_PORT": str(
                TEST_RABBITMQ_MANAGEMENT_HOST_PORT
            ),
        }
    )
    return environment


def run_command(
    project_root: Path,
    *args: str,
    timeout: int = 300,
    environment: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            args,
            cwd=project_root,
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=dict(environment) if environment is not None else None,
        )
    except subprocess.CalledProcessError as exc:
        command = [str(part) for part in exc.cmd]
        raise AssertionError(
            f"Command failed: {shlex.join(command)}\n"
            f"exit code: {exc.returncode}\n"
            f"stdout:\n{exc.stdout or ''}\n"
            f"stderr:\n{exc.stderr or ''}"
        ) from exc
    except subprocess.TimeoutExpired as exc:  # pragma: no cover
        command = exc.cmd if isinstance(exc.cmd, (list, tuple)) else [str(exc.cmd)]
        raise AssertionError(
            "Command timed out after "
            f"{timeout}s: {shlex.join([str(part) for part in command])}\n"
            f"stdout:\n{exc.output or ''}\n"
            f"stderr:\n{exc.stderr or ''}"
        ) from exc


def docker_compose(
    project_root: Path,
    *args: str,
) -> subprocess.CompletedProcess[str]:
    return run_command(
        project_root,
        "docker",
        "compose",
        *args,
        environment=_test_compose_environment(),
    )


def inspect_container_output(
    project_root: Path,
    service: str,
    *,
    lines: int = 100,
) -> subprocess.CompletedProcess[str]:
    return docker_compose(
        project_root,
        "logs",
        "--no-color",
        f"--tail={lines}",
        service,
    )


def inspect_health(
    project_root: Path,
    service: str,
) -> dict[str, object]:
    container_id = docker_compose(
        project_root,
        "ps",
        "-q",
        service,
    ).stdout.strip()

    if not container_id:
        raise AssertionError(
            f"No container found for test service: {service}"
        )

    result = run_command(
        project_root,
        "docker",
        "inspect",
        "--format",
        "{{json .State.Health}}",
        container_id,
    )

    return json.loads(result.stdout)


def wait_for_service_health(
    project_root: Path,
    service: str,
    *,
    timeout: int = 120,
) -> dict[str, object]:
    deadline = time.monotonic() + timeout
    last_health: dict[str, object] | None = None

    while time.monotonic() < deadline:
        last_health = inspect_health(project_root, service)

        if last_health.get("Status") == "healthy":
            return last_health

        time.sleep(2)

    logs = inspect_container_output(project_root, service).stdout

    raise AssertionError(
        f"{service} did not become healthy: {last_health}\n"
        f"logs:\n{logs}"
    )


def ping_worker(
    project_root: Path,
) -> subprocess.CompletedProcess[str]:
    return docker_compose(
        project_root,
        "exec",
        "-T",
        "worker",
        "/bin/sh",
        "-lc",
        'celery -A app.celery_app:celery_app '
        'inspect ping -d "celery@$HOSTNAME"',
    )
