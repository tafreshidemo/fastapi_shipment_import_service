from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_step6_sources_pass_ruff_when_pytest_runs() -> None:
    project_root = Path(__file__).resolve().parents[3]
    paths = [
        "app/outbox",
        "app/imports/services/recover_stale_imports.py",
        "app/workers/startup_recovery.py",
        "app/workers/beat_schedule.py",
        "tests/integration/outbox",
        "tests/integration/imports/test_watchdog_recovery.py",
        "tests/integration/imports/test_watchdog_concurrency.py",
        "tests/integration/imports/test_processing_max_attempts.py",
        "tests/integration/runtime/test_dlq_configuration.py",
        "tests/unit/workers/test_shared_recovery_service.py",
        "tests/unit/workers/test_task_retry_behavior.py",
        "tests/unit/quality/test_step6_ruff_quality_gate.py",
    ]

    result = subprocess.run(
        [sys.executable, "-m", "ruff", "check", *paths],
        cwd=project_root,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
