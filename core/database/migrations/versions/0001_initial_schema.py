"""initial TCCS Core schema

Revision ID: 0001_initial_schema
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0001_initial_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    uuid = postgresql.UUID(as_uuid=True)
    op.create_table("sites",
        sa.Column("id", uuid, primary_key=True), sa.Column("code", sa.String(32), nullable=False),
        sa.Column("name", sa.String(128), nullable=False), sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()), sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("code", name="uq_sites_code"))
    op.create_table("control_sections",
        sa.Column("id", uuid, primary_key=True), sa.Column("site_id", uuid, sa.ForeignKey("sites.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("code", sa.String(32), nullable=False), sa.Column("name", sa.String(128), nullable=False), sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.UniqueConstraint("site_id", "code", name="uq_section_site_code"))
    op.create_table("devices",
        sa.Column("id", uuid, primary_key=True), sa.Column("site_id", uuid, sa.ForeignKey("sites.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("section_id", uuid, sa.ForeignKey("control_sections.id", ondelete="SET NULL")), sa.Column("device_code", sa.String(64), nullable=False),
        sa.Column("name", sa.String(128), nullable=False), sa.Column("device_type", sa.String(32), nullable=False), sa.Column("ip_address", sa.String(64)),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()), sa.UniqueConstraint("device_code", name="uq_devices_device_code"))
    op.create_table("endpoints",
        sa.Column("id", uuid, primary_key=True), sa.Column("device_id", uuid, sa.ForeignKey("devices.id", ondelete="CASCADE"), nullable=False),
        sa.Column("endpoint_identity", sa.String(128), nullable=False), sa.Column("endpoint_type", sa.String(32), nullable=False),
        sa.Column("sip_username", sa.String(64), nullable=False), sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.UniqueConstraint("endpoint_identity", name="uq_endpoints_identity"), sa.UniqueConstraint("sip_username", name="uq_endpoints_sip_username"))
    op.create_table("extensions",
        sa.Column("id", uuid, primary_key=True), sa.Column("endpoint_id", uuid, sa.ForeignKey("endpoints.id", ondelete="CASCADE"), nullable=False),
        sa.Column("number", sa.String(8), nullable=False), sa.Column("display_name", sa.String(128), nullable=False), sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.UniqueConstraint("endpoint_id", name="uq_extensions_endpoint"), sa.UniqueConstraint("number", name="uq_extensions_number"))
    op.create_table("groups",
        sa.Column("id", uuid, primary_key=True), sa.Column("code", sa.String(8), nullable=False), sa.Column("name", sa.String(128), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()), sa.UniqueConstraint("code", name="uq_groups_code"))
    op.create_table("calls",
        sa.Column("id", uuid, primary_key=True), sa.Column("source_extension", sa.String(8), nullable=False), sa.Column("target", sa.String(128), nullable=False),
        sa.Column("mode", sa.String(32), nullable=False, server_default="individual"), sa.Column("state", sa.String(32), nullable=False, server_default="initiated"),
        sa.Column("conference_id", sa.String(128)), sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now()), sa.Column("ended_at", sa.DateTime(timezone=True)))
    op.create_table("call_participants",
        sa.Column("id", uuid, primary_key=True), sa.Column("call_id", uuid, sa.ForeignKey("calls.id", ondelete="CASCADE"), nullable=False),
        sa.Column("extension", sa.String(8), nullable=False), sa.Column("role", sa.String(32), nullable=False, server_default="participant"),
        sa.Column("muted", sa.Boolean(), nullable=False, server_default=sa.false()), sa.Column("connected_at", sa.DateTime(timezone=True)), sa.Column("disconnected_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("call_id", "extension", name="uq_call_participant"))
    op.create_table("call_events",
        sa.Column("id", uuid, primary_key=True), sa.Column("call_id", uuid, sa.ForeignKey("calls.id", ondelete="CASCADE"), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False), sa.Column("actor", sa.String(128)), sa.Column("payload", sa.Text()), sa.Column("occurred_at", sa.DateTime(timezone=True), server_default=sa.func.now()))
    op.create_table("audit_logs",
        sa.Column("id", uuid, primary_key=True), sa.Column("actor", sa.String(128), nullable=False), sa.Column("action", sa.String(128), nullable=False),
        sa.Column("resource_type", sa.String(64), nullable=False), sa.Column("resource_id", sa.String(128)), sa.Column("details", sa.Text()), sa.Column("occurred_at", sa.DateTime(timezone=True), server_default=sa.func.now()))


def downgrade() -> None:
    for table in ("audit_logs", "call_events", "call_participants", "calls", "groups", "extensions", "endpoints", "devices", "control_sections", "sites"):
        op.drop_table(table)
