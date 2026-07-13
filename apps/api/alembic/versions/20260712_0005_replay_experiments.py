"""Add replay experiments and strategy-aware forward accounts.

Revision ID: 20260712_0005
Revises: 20260711_0004
Create Date: 2026-07-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260712_0005"
down_revision: str | None = "20260711_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _columns(table: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table(table):
        return set()
    return {str(column["name"]) for column in inspector.get_columns(table)}


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    run_columns = _columns("backtest_runs")
    if "cancellation_requested" not in run_columns:
        op.add_column(
            "backtest_runs",
            sa.Column(
                "cancellation_requested",
                sa.Boolean(),
                server_default=sa.false(),
                nullable=False,
            ),
        )
    if "parent_run_id" not in run_columns:
        op.add_column("backtest_runs", sa.Column("parent_run_id", sa.String(36)))
        op.create_index("ix_backtest_runs_parent_run_id", "backtest_runs", ["parent_run_id"])

    if not inspector.has_table("replay_experiments"):
        op.create_table(
            "replay_experiments",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("name", sa.String(120), nullable=False),
            sa.Column("hypothesis", sa.String(500), nullable=False),
            sa.Column("objective", sa.String(32), nullable=False),
            sa.Column("success_criteria_json", sa.JSON(), nullable=False),
            sa.Column("data_version", sa.String(96), nullable=False),
            sa.Column("universe_mode", sa.String(32), nullable=False),
            sa.Column("period_json", sa.JSON(), nullable=False),
            sa.Column("status", sa.String(32), server_default="ACTIVE", nullable=False),
            sa.Column("notes", sa.Text()),
            sa.Column("archived", sa.Boolean(), server_default=sa.false(), nullable=False),
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
        )
        op.create_index("ix_replay_experiments_objective", "replay_experiments", ["objective"])
        op.create_index(
            "ix_replay_experiments_data_version", "replay_experiments", ["data_version"]
        )
        op.create_index("ix_replay_experiments_status", "replay_experiments", ["status"])
        op.create_index("ix_replay_experiments_archived", "replay_experiments", ["archived"])

    if not inspector.has_table("replay_experiment_runs"):
        op.create_table(
            "replay_experiment_runs",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column(
                "experiment_id",
                sa.String(36),
                sa.ForeignKey("replay_experiments.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "run_id",
                sa.String(36),
                sa.ForeignKey("backtest_runs.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("role", sa.String(24), nullable=False),
            sa.Column("label", sa.String(80), nullable=False),
            sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("CURRENT_TIMESTAMP"),
                nullable=False,
            ),
            sa.UniqueConstraint(
                "experiment_id", "run_id", name="uq_replay_experiment_run"
            ),
        )
        op.create_index(
            "ix_replay_experiment_runs_experiment_id",
            "replay_experiment_runs",
            ["experiment_id"],
        )
        op.create_index(
            "ix_replay_experiment_runs_run_id", "replay_experiment_runs", ["run_id"]
        )
        op.create_index(
            "ix_replay_experiment_runs_role", "replay_experiment_runs", ["role"]
        )

    account_columns = _columns("paper_accounts")
    additions = (
        ("account_type", sa.String(16), "BASELINE"),
        ("name", sa.String(80), "기준 포트폴리오"),
    )
    for name, column_type, default in additions:
        if name not in account_columns:
            op.add_column(
                "paper_accounts",
                sa.Column(name, column_type, server_default=default, nullable=False),
            )
    optional_columns: tuple[tuple[str, sa.types.TypeEngine[object]], ...] = (
        ("strategy_config_json", sa.JSON()),
        ("strategy_config_hash", sa.String(64)),
        ("source_experiment_id", sa.String(36)),
        ("source_run_id", sa.String(36)),
    )
    for name, column_type in optional_columns:
        if name not in account_columns:
            op.add_column("paper_accounts", sa.Column(name, column_type))
    if "account_type" not in account_columns:
        op.create_index("ix_paper_accounts_account_type", "paper_accounts", ["account_type"])
    if "strategy_config_hash" not in account_columns:
        op.create_index(
            "ix_paper_accounts_strategy_config_hash",
            "paper_accounts",
            ["strategy_config_hash"],
        )
    op.execute(
        sa.text(
            "UPDATE paper_accounts SET active_slot='BASELINE', account_type='BASELINE' "
            "WHERE active_slot='CURRENT'"
        )
    )
    position_columns = _columns("paper_positions")
    if "highest_close" not in position_columns:
        op.add_column("paper_positions", sa.Column("highest_close", sa.Float()))
    if "last_volatility" not in position_columns:
        op.add_column("paper_positions", sa.Column("last_volatility", sa.Float()))


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table("replay_experiment_runs"):
        op.drop_table("replay_experiment_runs")
    if inspector.has_table("replay_experiments"):
        op.drop_table("replay_experiments")
    account_columns = _columns("paper_accounts")
    for name in (
        "source_run_id",
        "source_experiment_id",
        "strategy_config_hash",
        "strategy_config_json",
        "name",
        "account_type",
    ):
        if name in account_columns:
            op.drop_column("paper_accounts", name)
    position_columns = _columns("paper_positions")
    for name in ("last_volatility", "highest_close"):
        if name in position_columns:
            op.drop_column("paper_positions", name)
    run_columns = _columns("backtest_runs")
    if "parent_run_id" in run_columns:
        op.drop_column("backtest_runs", "parent_run_id")
    if "cancellation_requested" in run_columns:
        op.drop_column("backtest_runs", "cancellation_requested")
