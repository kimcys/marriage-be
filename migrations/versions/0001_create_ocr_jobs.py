from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0001_create_ocr_jobs"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ocr_jobs",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("stored_filename", sa.String(length=255), nullable=False),
        sa.Column("content_type", sa.String(length=100), nullable=False),
        sa.Column("file_size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("input_relative_path", sa.String(length=500), nullable=False),
        sa.Column("output_relative_path", sa.String(length=500), nullable=True),
        sa.Column("debug_relative_path", sa.String(length=500), nullable=False),
        sa.Column("stdout_log_relative_path", sa.String(length=500), nullable=False),
        sa.Column("stderr_log_relative_path", sa.String(length=500), nullable=False),
        sa.Column("ocr_git_ref", sa.String(length=100), nullable=False),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("error_message", sa.String(length=1000), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_ocr_jobs_status", "ocr_jobs", ["status"])
    op.create_index("ix_ocr_jobs_created_at", "ocr_jobs", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_ocr_jobs_created_at", table_name="ocr_jobs")
    op.drop_index("ix_ocr_jobs_status", table_name="ocr_jobs")
    op.drop_table("ocr_jobs")
