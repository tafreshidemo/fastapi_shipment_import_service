from __future__ import annotations

import ast
from pathlib import Path


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
        elif isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)

    return modules


def test_worker_execution_does_not_depend_on_outbox_or_recovery() -> None:
    project_root = Path(__file__).resolve().parents[3]
    worker_modules = [
        project_root / "app" / "imports" / "services" / "process_import.py",
        project_root / "app" / "workers" / "tasks.py",
    ]

    imported_modules = set().union(
        *(_imported_modules(module_path) for module_path in worker_modules)
    )

    assert not any(module.startswith("app.outbox") for module in imported_modules)
    assert "app.imports.services.recover_stale_imports" not in imported_modules


def test_outbox_and_recovery_do_not_depend_on_worker_execution() -> None:
    project_root = Path(__file__).resolve().parents[3]
    step6_modules = [
        project_root / "app" / "outbox" / "repositories" / "outbox_repository.py",
        project_root / "app" / "outbox" / "services" / "publish_outbox.py",
        project_root / "app" / "imports" / "services" / "recover_stale_imports.py",
        project_root / "app" / "workers" / "startup_recovery.py",
        project_root / "app" / "workers" / "beat_schedule.py",
    ]

    imported_modules = set().union(
        *(_imported_modules(module_path) for module_path in step6_modules)
    )

    assert "app.imports.services.process_import" not in imported_modules
