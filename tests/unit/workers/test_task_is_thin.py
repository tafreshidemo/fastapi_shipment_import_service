from __future__ import annotations

from pathlib import Path


def test_process_import_task_only_builds_dependencies_and_delegates() -> None:
    source_path = Path(__file__).resolve().parents[3] / "app" / "workers" / "tasks.py"
    source = source_path.read_text()

    assert "ProcessImportService(" in source
    assert "service.run(" in source
    assert "self.retry(" in source
    assert "session.execute(" not in source
    assert "ImportJob" not in source
    assert "ShipmentRepository" not in source
