from __future__ import annotations

from pathlib import Path


def test_startup_and_beat_delegate_to_the_same_stale_import_recovery_service() -> None:
    project_root = Path(__file__).resolve().parents[3]
    startup_source = (project_root / "app/workers/startup_recovery.py").read_text()
    beat_source = (project_root / "app/workers/beat_schedule.py").read_text()

    for source in (startup_source, beat_source):
        assert "RecoverStaleImportsService" in source
        assert ".recover_stale_imports(" in source
        assert "ImportJob" not in source
        assert "ImportDispatchOutbox" not in source
