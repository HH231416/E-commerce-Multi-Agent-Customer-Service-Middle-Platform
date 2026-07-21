"""补齐生产工单状态、事件审计和高频查询索引。"""

from alembic import op
import sqlalchemy as sa

revision = "20260719_02"
down_revision = "20260719_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("support_tickets", sa.Column("reason", sa.Text(), nullable=False, server_default=""))
    op.add_column("support_tickets", sa.Column("source", sa.String(32), nullable=False, server_default="chat"))
    op.add_column("support_tickets", sa.Column("sentiment", sa.String(32), nullable=False, server_default="normal"))
    op.add_column("support_tickets", sa.Column("assigned_to", sa.String(128), nullable=True))
    op.add_column("support_tickets", sa.Column("resolution_note", sa.Text(), nullable=True))
    op.add_column(
        "support_tickets",
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
    )
    op.add_column("support_tickets", sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("support_tickets", sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True))
    op.create_table(
        "ticket_events",
        sa.Column("event_id", sa.String(64), primary_key=True),
        sa.Column("ticket_id", sa.String(64), sa.ForeignKey("support_tickets.ticket_id", ondelete="CASCADE"), nullable=False),
        sa.Column("tenant_id", sa.String(128), sa.ForeignKey("tenants.tenant_id"), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("actor_id", sa.String(128), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
    )
    op.create_index("ix_ticket_events_ticket_created", "ticket_events", ["ticket_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_ticket_events_ticket_created", table_name="ticket_events")
    op.drop_table("ticket_events")
    op.drop_column("support_tickets", "resolved_at")
    op.drop_column("support_tickets", "assigned_at")
    op.drop_column("support_tickets", "updated_at")
    op.drop_column("support_tickets", "resolution_note")
    op.drop_column("support_tickets", "assigned_to")
    op.drop_column("support_tickets", "sentiment")
    op.drop_column("support_tickets", "source")
    op.drop_column("support_tickets", "reason")
