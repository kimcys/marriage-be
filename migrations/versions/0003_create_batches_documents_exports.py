from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003_batches_documents_exports"
down_revision = "0002_create_ocr_records"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "batches",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.String(length=1000), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_by", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_batches_status", "batches", ["status"])
    op.create_index("ix_batches_created_by", "batches", ["created_by"])

    op.create_table(
        "documents",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("batch_id", sa.Uuid(as_uuid=True), sa.ForeignKey("batches.id", ondelete="CASCADE"), nullable=False),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("safe_filename", sa.String(length=255), nullable=False),
        sa.Column("media_type", sa.String(length=100), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("storage_key", sa.String(length=500), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("page_count", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_documents_batch_id", "documents", ["batch_id"])
    op.create_index("ix_documents_sha256", "documents", ["sha256"])
    op.create_index("ix_documents_status", "documents", ["status"])

    op.create_table(
        "exports",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("batch_id", sa.Uuid(as_uuid=True), sa.ForeignKey("batches.id", ondelete="CASCADE"), nullable=False),
        sa.Column("format", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("storage_key", sa.String(length=500), nullable=True),
        sa.Column("record_count", sa.Integer(), nullable=True),
        sa.Column("created_by", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("error_message", sa.String(length=1000), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_exports_batch_id", "exports", ["batch_id"])
    op.create_index("ix_exports_status", "exports", ["status"])
    op.create_index("ix_exports_created_by", "exports", ["created_by"])

    with op.batch_alter_table("ocr_jobs") as batch_op:
        batch_op.add_column(sa.Column("batch_id", sa.Uuid(as_uuid=True), nullable=True))
        batch_op.add_column(sa.Column("document_id", sa.Uuid(as_uuid=True), nullable=True))
        batch_op.create_index("ix_ocr_jobs_batch_id", ["batch_id"])
        batch_op.create_index("ix_ocr_jobs_document_id", ["document_id"])
        batch_op.create_foreign_key(
            "fk_ocr_jobs_batch_id_batches", "batches", ["batch_id"], ["id"], ondelete="SET NULL"
        )
        batch_op.create_foreign_key(
            "fk_ocr_jobs_document_id_documents", "documents", ["document_id"], ["id"], ondelete="SET NULL"
        )

    with op.batch_alter_table("ocr_records") as batch_op:
        batch_op.add_column(sa.Column("batch_id", sa.Uuid(as_uuid=True), nullable=True))
        batch_op.add_column(sa.Column("document_id", sa.Uuid(as_uuid=True), nullable=True))
        batch_op.add_column(sa.Column("source_page", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("source_record_index", sa.Integer(), nullable=False, server_default="0"))
        batch_op.add_column(sa.Column("normalized_data", sa.JSON(), nullable=False, server_default=sa.text("'{}'")))
        batch_op.add_column(sa.Column("corrected_data", sa.JSON(), nullable=False, server_default=sa.text("'{}'")))
        batch_op.add_column(sa.Column("review_status", sa.String(length=32), nullable=False, server_default="PENDING"))
        batch_op.create_index("ix_ocr_records_batch_id", ["batch_id"])
        batch_op.create_index("ix_ocr_records_document_id", ["document_id"])
        batch_op.create_index("ix_ocr_records_review_status", ["review_status"])
        batch_op.create_foreign_key(
            "fk_ocr_records_batch_id_batches", "batches", ["batch_id"], ["id"], ondelete="SET NULL"
        )
        batch_op.create_foreign_key(
            "fk_ocr_records_document_id_documents", "documents", ["document_id"], ["id"], ondelete="SET NULL"
        )
        # The application-level duplicate check (records.repositories._get_record_by_key)
        # keys on (job_id, source_key, source_page, source_record_index); widen the
        # DB-level uniqueness guarantee to match, otherwise a caller the app considers
        # "not a duplicate" can still hit a narrower DB unique-constraint violation.
        batch_op.drop_constraint("uq_ocr_records_job_source_key", type_="unique")
        batch_op.create_unique_constraint(
            "uq_ocr_records_job_source_key",
            ["job_id", "source_key", "source_page", "source_record_index"],
        )


def downgrade() -> None:
    with op.batch_alter_table("ocr_records") as batch_op:
        batch_op.drop_constraint("uq_ocr_records_job_source_key", type_="unique")
        batch_op.create_unique_constraint(
            "uq_ocr_records_job_source_key",
            ["job_id", "source_key"],
        )
        batch_op.drop_constraint("fk_ocr_records_document_id_documents", type_="foreignkey")
        batch_op.drop_constraint("fk_ocr_records_batch_id_batches", type_="foreignkey")
        batch_op.drop_index("ix_ocr_records_review_status")
        batch_op.drop_index("ix_ocr_records_document_id")
        batch_op.drop_index("ix_ocr_records_batch_id")
        batch_op.drop_column("review_status")
        batch_op.drop_column("corrected_data")
        batch_op.drop_column("normalized_data")
        batch_op.drop_column("source_record_index")
        batch_op.drop_column("source_page")
        batch_op.drop_column("document_id")
        batch_op.drop_column("batch_id")

    with op.batch_alter_table("ocr_jobs") as batch_op:
        batch_op.drop_constraint("fk_ocr_jobs_document_id_documents", type_="foreignkey")
        batch_op.drop_constraint("fk_ocr_jobs_batch_id_batches", type_="foreignkey")
        batch_op.drop_index("ix_ocr_jobs_document_id")
        batch_op.drop_index("ix_ocr_jobs_batch_id")
        batch_op.drop_column("document_id")
        batch_op.drop_column("batch_id")

    op.drop_index("ix_exports_created_by", table_name="exports")
    op.drop_index("ix_exports_status", table_name="exports")
    op.drop_index("ix_exports_batch_id", table_name="exports")
    op.drop_table("exports")

    op.drop_index("ix_documents_status", table_name="documents")
    op.drop_index("ix_documents_sha256", table_name="documents")
    op.drop_index("ix_documents_batch_id", table_name="documents")
    op.drop_table("documents")

    op.drop_index("ix_batches_created_by", table_name="batches")
    op.drop_index("ix_batches_status", table_name="batches")
    op.drop_table("batches")
