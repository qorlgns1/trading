"""Add local research synchronization metadata.

Revision ID: 20260710_0002
Revises: 20260710_0001
Create Date: 2026-07-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260710_0002"
down_revision: str | None = "20260710_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "universe_snapshots",
        sa.Column("version", sa.String(length=96), nullable=False),
        sa.Column("source_json", sa.JSON(), nullable=False),
        sa.Column("counts_json", sa.JSON(), nullable=False),
        sa.Column("manifest_path", sa.String(length=512), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("version"),
    )
    op.create_index("ix_universe_snapshots_is_active", "universe_snapshots", ["is_active"])
    op.create_table(
        "research_sync_runs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("trigger", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("stage", sa.String(length=48), nullable=False),
        sa.Column("completed_batches", sa.Integer(), nullable=False),
        sa.Column("total_batches", sa.Integer(), nullable=False),
        sa.Column("universe_version", sa.String(length=96), nullable=True),
        sa.Column("data_version", sa.String(length=96), nullable=True),
        sa.Column("failed_json", sa.JSON(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_research_sync_runs_status", "research_sync_runs", ["status"])


def downgrade() -> None:
    op.drop_index("ix_research_sync_runs_status", table_name="research_sync_runs")
    op.drop_table("research_sync_runs")
    op.drop_index("ix_universe_snapshots_is_active", table_name="universe_snapshots")
    op.drop_table("universe_snapshots")
