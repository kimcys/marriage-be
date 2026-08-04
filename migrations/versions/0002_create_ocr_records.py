from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002_create_ocr_records"
down_revision = "0001_create_ocr_jobs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ocr_records",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("job_id", sa.Uuid(as_uuid=True), sa.ForeignKey("ocr_jobs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_key", sa.String(length=200), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("field_values", sa.JSON(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("validation_issues", sa.JSON(), nullable=False),
        sa.Column("reviewed_by", sa.String(length=255), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("job_id", "source_key", name="uq_ocr_records_job_source_key"),
    )
    op.create_index("ix_ocr_records_job_id", "ocr_records", ["job_id"])
    op.create_index("ix_ocr_records_status", "ocr_records", ["status"])

    op.create_table(
        "record_revisions",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "record_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("ocr_records.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("previous_values", sa.JSON(), nullable=False),
        sa.Column("new_values", sa.JSON(), nullable=False),
        sa.Column("reviewer", sa.String(length=255), nullable=True),
        sa.Column("note", sa.String(length=1000), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("record_id", "version", name="uq_record_revisions_record_version"),
    )
    op.create_index("ix_record_revisions_record_id", "record_revisions", ["record_id"])


def downgrade() -> None:
    op.drop_index("ix_record_revisions_record_id", table_name="record_revisions")
    op.drop_table("record_revisions")

    op.drop_index("ix_ocr_records_status", table_name="ocr_records")
    op.drop_index("ix_ocr_records_job_id", table_name="ocr_records")
    op.drop_table("ocr_records")
