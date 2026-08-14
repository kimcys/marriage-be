from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004_add_document_type"
down_revision = "0003_batches_documents_exports"
branch_labels = None
depends_on = None

_DEFAULT_DOCUMENT_TYPE = "HANDWRITTEN_REGISTER"


def upgrade() -> None:
    with op.batch_alter_table("documents") as batch_op:
        batch_op.add_column(
            sa.Column(
                "document_type",
                sa.String(length=32),
                nullable=False,
                server_default=_DEFAULT_DOCUMENT_TYPE,
            )
        )

    with op.batch_alter_table("ocr_jobs") as batch_op:
        batch_op.add_column(
            sa.Column(
                "document_type",
                sa.String(length=32),
                nullable=False,
                server_default=_DEFAULT_DOCUMENT_TYPE,
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("ocr_jobs") as batch_op:
        batch_op.drop_column("document_type")

    with op.batch_alter_table("documents") as batch_op:
        batch_op.drop_column("document_type")
