from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0006_add_onedrive_submissions"
down_revision = "0005_add_job_page_number"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "onedrive_submissions",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("batch_id", sa.Uuid(as_uuid=True), sa.ForeignKey("batches.id", ondelete="CASCADE"), nullable=False),
        sa.Column("url", sa.String(length=2048), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("error_message", sa.String(length=1000), nullable=True),
        sa.Column("skipped_files", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_onedrive_submissions_batch_id", "onedrive_submissions", ["batch_id"])
    op.create_index("ix_onedrive_submissions_status", "onedrive_submissions", ["status"])
    op.create_index("ix_onedrive_submissions_url", "onedrive_submissions", ["url"], unique=True)

    with op.batch_alter_table("documents") as batch_op:
        batch_op.add_column(sa.Column("onedrive_submission_id", sa.Uuid(as_uuid=True), nullable=True))
        batch_op.create_index("ix_documents_onedrive_submission_id", ["onedrive_submission_id"])
        batch_op.create_foreign_key(
            "fk_documents_onedrive_submission_id_onedrive_submissions",
            "onedrive_submissions",
            ["onedrive_submission_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    with op.batch_alter_table("documents") as batch_op:
        batch_op.drop_constraint("fk_documents_onedrive_submission_id_onedrive_submissions", type_="foreignkey")
        batch_op.drop_index("ix_documents_onedrive_submission_id")
        batch_op.drop_column("onedrive_submission_id")

    op.drop_index("ix_onedrive_submissions_url", table_name="onedrive_submissions")
    op.drop_index("ix_onedrive_submissions_status", table_name="onedrive_submissions")
    op.drop_index("ix_onedrive_submissions_batch_id", table_name="onedrive_submissions")
    op.drop_table("onedrive_submissions")
