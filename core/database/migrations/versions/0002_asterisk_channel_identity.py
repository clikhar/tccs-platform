"""persist Asterisk channel identity on call participants

Revision ID: 0002_asterisk_channel_identity
"""

from alembic import op
import sqlalchemy as sa

revision = "0002_asterisk_channel_identity"
down_revision = "0001_initial_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("call_participants", sa.Column("asterisk_channel_id", sa.String(128), nullable=True))
    op.create_index(
        "ix_call_participants_asterisk_channel_id",
        "call_participants",
        ["asterisk_channel_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_call_participants_asterisk_channel_id", table_name="call_participants")
    op.drop_column("call_participants", "asterisk_channel_id")
