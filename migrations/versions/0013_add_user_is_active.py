from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0013_add_user_is_active"
down_revision = "0012_user_name_activity_log"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch_op:
        batch_op.add_column(sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()))


def downgrade() -> None:
    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_column("is_active")
