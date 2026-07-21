"""创建生产部署使用的租户、工单、退款与审计基础表。"""

from alembic import op
import sqlalchemy as sa

revision = "20260719_01"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tenants",
        sa.Column("tenant_id", sa.String(128), primary_key=True),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
    )
    op.create_table(
        "support_tickets",
        sa.Column("ticket_id", sa.String(64), primary_key=True),
        sa.Column("tenant_id", sa.String(128), sa.ForeignKey("tenants.tenant_id"), nullable=False),
        sa.Column("user_id", sa.String(128), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("priority", sa.String(32), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("context_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
    )
    op.create_index("ix_support_tickets_tenant_status", "support_tickets", ["tenant_id", "status"])
    op.create_table(
        "refund_requests",
        sa.Column("idempotency_key", sa.String(128), primary_key=True),
        sa.Column("tenant_id", sa.String(128), sa.ForeignKey("tenants.tenant_id"), nullable=False),
        sa.Column("order_no", sa.String(128), nullable=False),
        sa.Column("user_id", sa.String(128), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
    )
    op.create_table(
        "audit_events",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("actor_id", sa.String(128), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("resource_id", sa.String(128), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("audit_events")
    op.drop_table("refund_requests")
    op.drop_index("ix_support_tickets_tenant_status", table_name="support_tickets")
    op.drop_table("support_tickets")
    op.drop_table("tenants")
