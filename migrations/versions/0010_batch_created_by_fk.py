from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0010_batch_created_by_fk"
down_revision = "0009_add_batch_daerah_negeri"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # batches.created_by was never actually populated before this (the
    # create route always passed None), but clear anything that doesn't
    # point at a real user so the constraint below can't fail to apply.
    op.execute(
        sa.text(
            "UPDATE batches SET created_by = NULL "
            "WHERE created_by IS NOT NULL AND created_by NOT IN (SELECT id FROM users)"
        )
    )
    with op.batch_alter_table("batches") as batch_op:
        batch_op.create_foreign_key("fk_batches_created_by_users", "users", ["created_by"], ["id"], ondelete="SET NULL")


def downgrade() -> None:
    with op.batch_alter_table("batches") as batch_op:
        batch_op.drop_constraint("fk_batches_created_by_users", type_="foreignkey")
