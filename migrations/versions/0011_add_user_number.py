from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0011_add_user_number"
down_revision = "0010_batch_created_by_fk"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch_op:
        batch_op.add_column(sa.Column("number", sa.Integer(), nullable=True))
    # Existing accounts get 1, 2, 3, ... in the order they were created.
    op.execute(
        sa.text(
            "UPDATE users SET number = numbered.rn FROM "
            "(SELECT id, ROW_NUMBER() OVER (ORDER BY created_at, id) AS rn FROM users) AS numbered "
            "WHERE users.id = numbered.id"
        )
    )
    with op.batch_alter_table("users") as batch_op:
        batch_op.alter_column("number", existing_type=sa.Integer(), nullable=False)
        batch_op.create_unique_constraint("uq_users_number", ["number"])


def downgrade() -> None:
    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_constraint("uq_users_number", type_="unique")
        batch_op.drop_column("number")
