from __future__ import annotations

import json
import shlex
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class DockerRuntimeStack:
    project_root: Path
    api_url: str
    postgres_url: str
    rabbitmq_url: str


def run_command(
    project_root: Path, *args: str, timeout: int = 300
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            args,
            cwd=project_root,
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:  # pragma: no cover - diagnostic path
        command = exc.cmd if isinstance(exc.cmd, (list, tuple)) else [str(exc.cmd)]
        raise AssertionError(
            "Command timed out after "
            f"{timeout}s: {shlex.join([str(part) for part in command])}\n"
            f"stdout:\n{exc.output or ''}\n"
            f"stderr:\n{exc.stderr or ''}"
        ) from exc


def docker_compose(project_root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return run_command(project_root, "docker", "compose", *args)


def inspect_container_output(
    project_root: Path, service: str, *, lines: int = 100
) -> subprocess.CompletedProcess[str]:
    return docker_compose(project_root, "logs", "--no-color", f"--tail={lines}", service)


def inspect_health(project_root: Path, service: str) -> dict[str, object]:
    container_id = docker_compose(project_root, "ps", "-q", service).stdout.strip()
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
    project_root: Path, service: str, *, timeout: int = 120
) -> dict[str, object]:
    deadline = time.time() + timeout
    last_health: dict[str, object] | None = None
    while time.time() < deadline:
        last_health = inspect_health(project_root, service)
        if last_health.get("Status") == "healthy":
            return last_health
        time.sleep(2)
    raise AssertionError(f"{service} did not become healthy: {last_health}")


def ping_worker(project_root: Path) -> subprocess.CompletedProcess[str]:
    return docker_compose(
        project_root,
        "exec",
        "-T",
        "worker",
        "/bin/sh",
        "-lc",
        'celery -A app.celery_app:celery_app inspect ping -d "celery@$HOSTNAME"',
    )
