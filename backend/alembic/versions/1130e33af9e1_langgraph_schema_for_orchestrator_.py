"""langgraph schema for orchestrator checkpoints

Revision ID: 1130e33af9e1
Revises: cdf7bd4f62d2
Create Date: 2026-11-09 23:56:11.705336

"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '1130e33af9e1'
down_revision: str | Sequence[str] | None = 'cdf7bd4f62d2'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """LangGraph's checkpointer creates its own tables (incl. one named `checkpoints`, which the platform already
    uses for executions); keep them in a dedicated schema. The tables themselves are created by
    AsyncPostgresSaver.setup() — LangGraph owns and migrates them (ADR-016)."""
    op.execute("CREATE SCHEMA IF NOT EXISTS langgraph")


def downgrade() -> None:
    op.execute("DROP SCHEMA IF EXISTS langgraph CASCADE")
