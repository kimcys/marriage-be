from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0009_add_batch_daerah_negeri"
down_revision = "0008_add_users"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("batches") as batch_op:
        batch_op.add_column(sa.Column("daerah", sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column("negeri", sa.String(length=255), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("batches") as batch_op:
        batch_op.drop_column("negeri")
        batch_op.drop_column("daerah")
