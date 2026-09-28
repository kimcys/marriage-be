from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0014_add_gemini_batch_columns"
down_revision = "0013_add_user_is_active"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("ocr_jobs") as batch_op:
        batch_op.add_column(sa.Column("gemini_batch_stage", sa.String(length=16), nullable=True))
        batch_op.add_column(sa.Column("gemini_batch_name", sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column("gemini_batch_updated_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.create_index("ix_ocr_jobs_gemini_batch_stage", ["gemini_batch_stage"])


def downgrade() -> None:
    with op.batch_alter_table("ocr_jobs") as batch_op:
        batch_op.drop_index("ix_ocr_jobs_gemini_batch_stage")
        batch_op.drop_column("gemini_batch_updated_at")
        batch_op.drop_column("gemini_batch_name")
        batch_op.drop_column("gemini_batch_stage")
