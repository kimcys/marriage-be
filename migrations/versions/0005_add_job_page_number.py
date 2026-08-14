from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0005_add_job_page_number"
down_revision = "0004_add_document_type"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("ocr_jobs") as batch_op:
        batch_op.add_column(sa.Column("page_number", sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("ocr_jobs") as batch_op:
        batch_op.drop_column("page_number")
