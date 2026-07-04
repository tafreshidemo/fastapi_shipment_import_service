"""add import fingerprint lookup index"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "0003_fingerprint_index"
down_revision = "0002_step2_models"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_import_jobs_idempotency_fingerprint",
        "import_jobs",
        ["idempotency_fingerprint"],
    )


def downgrade() -> None:
    op.drop_index("ix_import_jobs_idempotency_fingerprint", table_name="import_jobs")
