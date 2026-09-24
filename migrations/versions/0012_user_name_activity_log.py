from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0012_user_name_activity_log"
down_revision = "0011_add_user_number"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch_op:
        batch_op.add_column(sa.Column("name", sa.String(length=255), nullable=True))

    op.create_table(
        "activity_log",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("user_id", sa.Uuid(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("user_code", sa.String(length=32), nullable=True),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("batch_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("target_type", sa.String(length=32), nullable=True),
        sa.Column("target_id", sa.String(length=64), nullable=True),
        sa.Column("target_label", sa.String(length=1000), nullable=True),
        sa.Column("summary", sa.String(length=1000), nullable=False),
        sa.Column("details", sa.JSON(), nullable=True),
    )
    op.create_index("ix_activity_log_created_at", "activity_log", ["created_at"])
    op.create_index("ix_activity_log_user_id", "activity_log", ["user_id"])
    op.create_index("ix_activity_log_action", "activity_log", ["action"])
    op.create_index("ix_activity_log_batch_id", "activity_log", ["batch_id"])


def downgrade() -> None:
    op.drop_index("ix_activity_log_batch_id", table_name="activity_log")
    op.drop_index("ix_activity_log_action", table_name="activity_log")
    op.drop_index("ix_activity_log_user_id", table_name="activity_log")
    op.drop_index("ix_activity_log_created_at", table_name="activity_log")
    op.drop_table("activity_log")
    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_column("name")
