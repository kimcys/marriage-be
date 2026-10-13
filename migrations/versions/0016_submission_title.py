from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0016_submission_title"
down_revision = "0015_submission_fetch_attempts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("onedrive_submissions") as batch_op:
        batch_op.add_column(sa.Column("title", sa.String(length=512), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("onedrive_submissions") as batch_op:
        batch_op.drop_column("title")
