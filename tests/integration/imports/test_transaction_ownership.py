from __future__ import annotations

from pathlib import Path


def test_repositories_do_not_control_transactions_or_sessions() -> None:
    repository_root = Path(__file__).resolve().parents[3] / "app" / "imports" / "repositories"
    sources = "\n".join(path.read_text() for path in repository_root.glob("*.py"))

    assert ".commit(" not in sources
    assert ".rollback(" not in sources
    assert ".close(" not in sources


def test_process_service_defines_the_chunk_transaction_boundary() -> None:
    source_path = (
        Path(__file__).resolve().parents[3]
        / "app"
        / "imports"
        / "services"
        / "process_import.py"
    )
    source = source_path.read_text()

    assert "with session.begin():" in source
    assert "session.commit(" not in source
    assert "session.rollback(" not in source
