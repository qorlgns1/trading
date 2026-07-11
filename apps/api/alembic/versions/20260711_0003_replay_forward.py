"""Add real replay metadata and forward portfolio ledger.

Revision ID: 20260711_0003
Revises: 20260710_0002
Create Date: 2026-07-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260711_0003"
down_revision: str | None = "20260710_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        )
    ]


def upgrade() -> None:
    op.add_column(
        "backtest_runs",
        sa.Column(
            "run_kind",
            sa.String(length=32),
            server_default="DEMO_BACKTEST",
            nullable=False,
        ),
    )
    op.add_column("backtest_runs", sa.Column("data_version", sa.String(length=96), nullable=True))
    op.add_column(
        "backtest_runs",
        sa.Column("stage", sa.String(length=48), server_default="QUEUED", nullable=False),
    )
    op.add_column(
        "backtest_runs",
        sa.Column("completed_units", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "backtest_runs",
        sa.Column("total_units", sa.Integer(), server_default="0", nullable=False),
    )
    op.create_index("ix_backtest_runs_run_kind", "backtest_runs", ["run_kind"])
    op.create_index("ix_backtest_runs_data_version", "backtest_runs", ["data_version"])

    op.create_table(
        "candidate_snapshots",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("data_version", sa.String(length=96), nullable=False),
        sa.Column("as_of", sa.Date(), nullable=False),
        sa.Column("artifact_path", sa.String(length=512), nullable=False),
        sa.Column("artifact_sha256", sa.String(length=64), nullable=False),
        sa.Column("counts_json", sa.JSON(), nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("data_version", "as_of", name="uq_candidate_snapshot_version_date"),
    )
    op.create_index("ix_candidate_snapshots_data_version", "candidate_snapshots", ["data_version"])
    op.create_index("ix_candidate_snapshots_as_of", "candidate_snapshots", ["as_of"])

    op.create_table(
        "candidate_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("snapshot_id", sa.String(length=36), nullable=False),
        sa.Column("as_of", sa.Date(), nullable=False),
        sa.Column("event_type", sa.String(length=16), nullable=False),
        sa.Column("asset_id", sa.String(length=160), nullable=False),
        sa.Column("symbol", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=256), nullable=False),
        sa.Column("peer_group", sa.String(length=48), nullable=False),
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column("previous_score", sa.Float(), nullable=True),
        sa.Column("details_json", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["snapshot_id"], ["candidate_snapshots.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "snapshot_id",
            "asset_id",
            "event_type",
            name="uq_candidate_event_snapshot_asset_type",
        ),
    )
    for column in ("snapshot_id", "as_of", "event_type", "asset_id", "peer_group"):
        op.create_index(f"ix_candidate_events_{column}", "candidate_events", [column])

    op.create_table(
        "paper_accounts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("active_slot", sa.String(length=16), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("initial_capital_krw", sa.Float(), nullable=False),
        sa.Column("weights_json", sa.JSON(), nullable=False),
        sa.Column("score_version", sa.String(length=64), nullable=False),
        sa.Column("portfolio_version", sa.String(length=64), nullable=False),
        sa.Column("baseline_data_version", sa.String(length=96), nullable=False),
        sa.Column("last_data_version", sa.String(length=96), nullable=True),
        sa.Column("last_review_date", sa.Date(), nullable=True),
        sa.Column("review_required_json", sa.JSON(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("active_slot"),
    )
    op.create_index("ix_paper_accounts_status", "paper_accounts", ["status"])

    op.create_table(
        "paper_reviews",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("account_id", sa.String(length=36), nullable=False),
        sa.Column("review_date", sa.Date(), nullable=False),
        sa.Column("data_version", sa.String(length=96), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("details_json", sa.JSON(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["account_id"], ["paper_accounts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("account_id", "review_date", name="uq_paper_review_account_date"),
    )
    op.create_index("ix_paper_reviews_account_id", "paper_reviews", ["account_id"])
    op.create_index("ix_paper_reviews_review_date", "paper_reviews", ["review_date"])

    op.create_table(
        "paper_orders",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("idempotency_key", sa.String(length=160), nullable=False),
        sa.Column("account_id", sa.String(length=36), nullable=False),
        sa.Column("review_id", sa.String(length=36), nullable=False),
        sa.Column("asset_id", sa.String(length=160), nullable=False),
        sa.Column("symbol", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=256), nullable=False),
        sa.Column("peer_group", sa.String(length=48), nullable=False),
        sa.Column("sleeve", sa.String(length=32), nullable=False),
        sa.Column("currency", sa.String(length=8), nullable=False),
        sa.Column("side", sa.String(length=8), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("scheduled_date", sa.Date(), nullable=False),
        sa.Column("filled_date", sa.Date(), nullable=True),
        sa.Column("quantity", sa.Integer(), nullable=True),
        sa.Column("price", sa.Float(), nullable=True),
        sa.Column("notional", sa.Float(), nullable=True),
        sa.Column("cost", sa.Float(), nullable=True),
        sa.Column("reason", sa.String(length=64), nullable=False),
        *_timestamps(),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["account_id"], ["paper_accounts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["review_id"], ["paper_reviews.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key"),
    )
    for column in ("account_id", "review_id", "asset_id", "status"):
        op.create_index(f"ix_paper_orders_{column}", "paper_orders", [column])

    op.create_table(
        "paper_trades",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("order_id", sa.String(length=36), nullable=False),
        sa.Column("account_id", sa.String(length=36), nullable=False),
        sa.Column("traded_on", sa.Date(), nullable=False),
        sa.Column("asset_id", sa.String(length=160), nullable=False),
        sa.Column("symbol", sa.String(length=64), nullable=False),
        sa.Column("side", sa.String(length=8), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("price", sa.Float(), nullable=False),
        sa.Column("notional", sa.Float(), nullable=False),
        sa.Column("cost", sa.Float(), nullable=False),
        sa.Column("currency", sa.String(length=8), nullable=False),
        sa.Column("reason", sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["paper_accounts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["order_id"], ["paper_orders.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("order_id"),
    )
    op.create_index("ix_paper_trades_account_id", "paper_trades", ["account_id"])
    op.create_index("ix_paper_trades_traded_on", "paper_trades", ["traded_on"])

    op.create_table(
        "paper_positions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("account_id", sa.String(length=36), nullable=False),
        sa.Column("asset_id", sa.String(length=160), nullable=False),
        sa.Column("symbol", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=256), nullable=False),
        sa.Column("peer_group", sa.String(length=48), nullable=False),
        sa.Column("sleeve", sa.String(length=32), nullable=False),
        sa.Column("currency", sa.String(length=8), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("average_cost", sa.Float(), nullable=False),
        sa.Column("last_price", sa.Float(), nullable=False),
        sa.Column("last_score", sa.Float(), nullable=False),
        sa.Column("data_status", sa.String(length=32), nullable=False),
        sa.Column("review_required", sa.Boolean(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["account_id"], ["paper_accounts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("account_id", "asset_id", name="uq_paper_position_account_asset"),
    )
    op.create_index("ix_paper_positions_account_id", "paper_positions", ["account_id"])

    op.create_table(
        "paper_cash",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("account_id", sa.String(length=36), nullable=False),
        sa.Column("sleeve", sa.String(length=32), nullable=False),
        sa.Column("currency", sa.String(length=8), nullable=False),
        sa.Column("balance", sa.Float(), nullable=False),
        sa.Column("target_per_slot", sa.Float(), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["paper_accounts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("account_id", "sleeve", name="uq_paper_cash_account_sleeve"),
    )
    op.create_index("ix_paper_cash_account_id", "paper_cash", ["account_id"])

    op.create_table(
        "paper_valuations",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("account_id", sa.String(length=36), nullable=False),
        sa.Column("data_version", sa.String(length=96), nullable=False),
        sa.Column("as_of", sa.Date(), nullable=False),
        sa.Column("market_dates_json", sa.JSON(), nullable=False),
        sa.Column("total_value_krw", sa.Float(), nullable=False),
        sa.Column("cash_krw", sa.Float(), nullable=False),
        sa.Column("invested_krw", sa.Float(), nullable=False),
        sa.Column("benchmark_value_krw", sa.Float(), nullable=True),
        sa.Column("drawdown", sa.Float(), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["paper_accounts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("account_id", "data_version", name="uq_paper_value_account_version"),
    )
    op.create_index("ix_paper_valuations_account_id", "paper_valuations", ["account_id"])
    op.create_index("ix_paper_valuations_as_of", "paper_valuations", ["as_of"])


def downgrade() -> None:
    op.drop_table("paper_valuations")
    op.drop_table("paper_cash")
    op.drop_table("paper_positions")
    op.drop_table("paper_trades")
    op.drop_table("paper_orders")
    op.drop_table("paper_reviews")
    op.drop_table("paper_accounts")
    op.drop_table("candidate_events")
    op.drop_table("candidate_snapshots")
    op.drop_index("ix_backtest_runs_data_version", table_name="backtest_runs")
    op.drop_index("ix_backtest_runs_run_kind", table_name="backtest_runs")
    op.drop_column("backtest_runs", "total_units")
    op.drop_column("backtest_runs", "completed_units")
    op.drop_column("backtest_runs", "stage")
    op.drop_column("backtest_runs", "data_version")
    op.drop_column("backtest_runs", "run_kind")
