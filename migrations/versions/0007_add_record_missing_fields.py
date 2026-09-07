from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0007_add_record_missing_fields"
down_revision = "0006_add_onedrive_submissions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("ocr_records") as batch_op:
        batch_op.add_column(
            sa.Column("missing_fields", sa.JSON(), nullable=False, server_default="[]")
        )


def downgrade() -> None:
    with op.batch_alter_table("ocr_records") as batch_op:
        batch_op.drop_column("missing_fields")
