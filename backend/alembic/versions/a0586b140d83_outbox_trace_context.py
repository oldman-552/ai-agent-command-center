"""outbox trace context

Revision ID: a0586b140d83
Revises: 705166b93b12
Create Date: 2026-11-10 12:27:41.951510

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'a0586b140d83'
down_revision: Union[str, Sequence[str], None] = '705166b93b12'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("outbox_messages", sa.Column("trace_context", postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("outbox_messages", "trace_context")
