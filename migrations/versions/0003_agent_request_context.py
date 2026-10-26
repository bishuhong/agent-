"""Persist generation inputs required to resume the exact accepted AgentRun.

Revision ID: 0003_agent_request_context
Revises: 0002_langgraph_checkpoints
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0003_agent_request_context"
down_revision = "0002_langgraph_checkpoints"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agent_runs",
        sa.Column(
            "request_context",
            postgresql.JSONB(none_as_null=True),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("agent_runs", "request_context")
