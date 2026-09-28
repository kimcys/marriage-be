from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0015_add_submission_fetch_attempts"
down_revision = "0014_add_gemini_batch_columns"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("onedrive_submissions") as batch_op:
        batch_op.add_column(sa.Column("fetch_attempts", sa.Integer(), nullable=False, server_default="0"))


def downgrade() -> None:
    with op.batch_alter_table("onedrive_submissions") as batch_op:
        batch_op.drop_column("fetch_attempts")
