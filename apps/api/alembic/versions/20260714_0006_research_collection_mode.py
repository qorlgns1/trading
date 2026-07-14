"""Persist the price collection mode for research sync runs.

Revision ID: 20260714_0006
Revises: 20260712_0005
Create Date: 2026-07-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260714_0006"
down_revision: str | None = "20260712_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "research_sync_runs",
        sa.Column("collection_mode", sa.String(length=16), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("research_sync_runs", "collection_mode")
