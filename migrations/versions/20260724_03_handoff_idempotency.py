"""Add deduplication and conversation handoff metadata to support tickets."""

from alembic import op
import sqlalchemy as sa

revision = "20260724_03"
down_revision = "20260719_02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("support_tickets", sa.Column("idempotency_key", sa.String(128), nullable=True))
    op.add_column("support_tickets", sa.Column("conversation_id", sa.String(256), nullable=True))
    op.create_index(
        "uq_support_tickets_tenant_idempotency",
        "support_tickets",
        ["tenant_id", "idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
        sqlite_where=sa.text("idempotency_key IS NOT NULL"),
    )
    op.create_index(
        "ix_support_tickets_open_conversation",
        "support_tickets",
        ["tenant_id", "user_id", "conversation_id", "status"],
    )


def downgrade() -> None:
    op.drop_index("ix_support_tickets_open_conversation", table_name="support_tickets")
    op.drop_index("uq_support_tickets_tenant_idempotency", table_name="support_tickets")
    op.drop_column("support_tickets", "conversation_id")
    op.drop_column("support_tickets", "idempotency_key")
