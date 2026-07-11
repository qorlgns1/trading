import hashlib
import json
import os
import shutil
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path
from typing import Any

import polars as pl
from quant_core import PortfolioConfig, TrendScoreConfig
from quant_core.config import TREND_SCORE_VERSION
from quant_core.enums import DataStatus, PeerGroup
from quant_core.market_portfolio import (
    MarketReplayRun,
    PreparedMarketReplay,
    prepare_market_replay,
    simulate_prepared_replay,
)
from quant_core.models import BacktestResult
from quant_core.replay_analysis import ReplayAnalysisBuild, analyze_replay
from quant_core.scoring import config_hash, score_trends

from quant_api.research_quality import QUALITY_POLICY_VERSION
from quant_api.research_store import ResearchSnapshotStore

REPLAY_ENGINE_VERSION = "real-replay-v1.1.0"
REPLAY_SCORE_CACHE_VERSION = "replay-score-cache-v1.0.0"
REPLAY_SCORE_COLUMNS = [
    "date",
    "asset_id",
    "symbol",
    "name",
    "peer_group",
    "currency",
    "trend_score",
    "relative_momentum",
    "data_eligible",
    "candidate_eligible",
    "benchmark_close",
    "benchmark_sma200",
]

ReplayProgress = Callable[[str, int, int], None]


@dataclass(frozen=True)
class ReplayBuild:
    result: BacktestResult
    actual_run: MarketReplayRun
    no_cost_run: MarketReplayRun
    prepared: PreparedMarketReplay
    analysis: ReplayAnalysisBuild
    cache_key: str
    cache_hit: bool
    score_root: Path


def _cache_key(manifest: dict[str, Any], score_config: TrendScoreConfig) -> str:
    payload = {
        "cache_version": REPLAY_SCORE_CACHE_VERSION,
        "data_version": manifest["data_version"],
        "bars_sha256": manifest["bars_sha256"],
        "quality_policy": QUALITY_POLICY_VERSION,
        "score_config_hash": config_hash(score_config),
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()[:20]


def _group_year_paths(snapshot: Path, group: PeerGroup) -> list[Path]:
    return sorted((snapshot / "bars" / f"peer_group={group.value}").rglob("bars.parquet"))


def _year_from_path(path: Path) -> int:
    return int(next(part.split("=", 1)[1] for part in path.parts if part.startswith("year=")))


class PartitionedReplayScorer:
    def __init__(self, root: Path) -> None:
        self.root = root

    def build(
        self,
        snapshot: Path,
        manifest: dict[str, Any],
        *,
        score_config: TrendScoreConfig,
        progress: ReplayProgress | None = None,
    ) -> tuple[Path, str, bool]:
        key = _cache_key(manifest, score_config)
        final_root = self.root / "replay-cache" / key
        cache_manifest = final_root / "manifest.json"
        if cache_manifest.is_file():
            payload = json.loads(cache_manifest.read_text(encoding="utf-8"))
            if payload.get("complete") is True:
                return final_root, key, True

        staging = self.root / "replay-cache" / f".staging-{key}"
        shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir(parents=True)
        universe = pl.read_csv(snapshot / "universe.csv")
        latest = pl.read_parquet(snapshot / "scores" / "latest.parquet")
        invalid_ids = set(
            latest.filter(pl.col("data_status") == DataStatus.INVALID_DATA.value)
            .get_column("asset_id")
            .to_list()
        )
        supported_ids = sorted(
            universe.filter(pl.col("is_supported").fill_null(False))
            .get_column("asset_id")
            .to_list()
        )
        supported_ids = [asset_id for asset_id in supported_ids if asset_id not in invalid_ids]
        jobs = [(group, path) for group in PeerGroup for path in _group_year_paths(snapshot, group)]
        completed = 0
        for group in PeerGroup:
            carry: pl.DataFrame | None = None
            for path in _group_year_paths(snapshot, group):
                target = pl.read_parquet(path).filter(pl.col("asset_id").is_in(supported_ids))
                combined = (
                    target if carry is None else pl.concat([carry, target], how="diagonal_relaxed")
                ).sort(["asset_id", "date"])
                scored = score_trends(
                    combined,
                    score_config,
                    data_version=str(manifest["data_version"]),
                )
                year = _year_from_path(path)
                output = scored.filter(pl.col("date").dt.year() == year).select(
                    REPLAY_SCORE_COLUMNS
                )
                output_path = (
                    staging / f"peer_group={group.value}" / f"year={year}" / "scores.parquet"
                )
                output_path.parent.mkdir(parents=True, exist_ok=True)
                output.write_parquet(output_path, compression="zstd", statistics=True)
                carry = (
                    combined.group_by("asset_id", maintain_order=True)
                    .tail(253)
                    .sort(["asset_id", "date"])
                )
                completed += 1
                if progress:
                    progress("SCORE_PARTITIONS", completed, len(jobs))
        payload = {
            "cache_version": REPLAY_SCORE_CACHE_VERSION,
            "cache_key": key,
            "data_version": manifest["data_version"],
            "bars_sha256": manifest["bars_sha256"],
            "score_version": score_config.version,
            "score_config_hash": config_hash(score_config),
            "quality_policy": QUALITY_POLICY_VERSION,
            "excluded_invalid_assets": sorted(invalid_ids),
            "complete": True,
        }
        (staging / "manifest.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        final_root.parent.mkdir(parents=True, exist_ok=True)
        if final_root.exists():
            shutil.rmtree(final_root)
        os.replace(staging, final_root)
        return final_root, key, False


def _last_dates_by_week(paths: list[Path]) -> dict[tuple[int, int], date]:
    dates = (
        pl.scan_parquet([str(path) for path in paths])
        .select("date")
        .unique()
        .sort("date")
        .collect()
        .get_column("date")
        .to_list()
    )
    result: dict[tuple[int, int], date] = {}
    for current in dates:
        key = current.isocalendar()[:2]
        result[key] = max(current, result.get(key, current))
    return result


def _completed_schedule(score_root: Path) -> pl.DataFrame:
    from quant_core.calendar import next_trading_date

    market_groups = {
        "US": [PeerGroup.US_STOCK, PeerGroup.US_EQUITY_ETF],
        "KR": [
            PeerGroup.KR_KOSPI,
            PeerGroup.KR_KOSDAQ,
            PeerGroup.KR_DOMESTIC_EQUITY_ETF,
            PeerGroup.KR_OVERSEAS_EQUITY_ETF,
        ],
    }
    market_weeks: dict[str, dict[tuple[int, int], date]] = {}
    for market, groups in market_groups.items():
        paths = sorted(
            path
            for group in groups
            for path in (score_root / f"peer_group={group.value}").rglob("scores.parquet")
        )
        weeks = _last_dates_by_week(paths)
        market_weeks[market] = {
            key: value
            for key, value in weeks.items()
            if next_trading_date(value, market).isocalendar()[:2] != key
        }
    common = sorted(set(market_weeks["US"]) & set(market_weeks["KR"]))
    rows: list[dict[str, Any]] = []
    for key in common:
        review_date = max(market_weeks["US"][key], market_weeks["KR"][key])
        for market, groups in market_groups.items():
            signal_date = market_weeks[market][key]
            for group in groups:
                rows.append(
                    {
                        "peer_group": group.value,
                        "signal_date": signal_date,
                        "review_date": review_date,
                    }
                )
    if not rows:
        raise RuntimeError("완결된 한국·미국 주간 신호가 없습니다.")
    return pl.DataFrame(rows).sort(["review_date", "peer_group"])


def _weekly_signals(score_root: Path, schedule: pl.DataFrame) -> tuple[pl.DataFrame, pl.DataFrame]:
    readiness_frames: list[pl.DataFrame] = []
    for group in PeerGroup:
        mapping = schedule.filter(pl.col("peer_group") == group.value).select(
            pl.col("signal_date").alias("date"), "review_date"
        )
        pattern = score_root / f"peer_group={group.value}" / "year=*" / "scores.parquet"
        readiness_frames.append(
            pl.scan_parquet(str(pattern), hive_partitioning=True)
            .join(mapping.lazy(), on="date", how="inner")
            .group_by("review_date")
            .agg(
                pl.col("data_eligible").fill_null(False).sum().alias("eligible_assets"),
                pl.col("benchmark_sma200").is_not_null().any().alias("benchmark_ready"),
            )
            .with_columns(pl.lit(group.value).alias("peer_group"))
            .collect()
        )
    readiness = (
        pl.concat(readiness_frames)
        .with_columns(
            ((pl.col("eligible_assets") >= 30) & pl.col("benchmark_ready")).alias("group_ready")
        )
        .group_by("review_date")
        .agg(
            pl.len().alias("peer_groups"),
            pl.col("group_ready").all().alias("all_groups_ready"),
        )
        .filter((pl.col("peer_groups") == len(PeerGroup)) & pl.col("all_groups_ready"))
        .select("review_date")
        .sort("review_date")
    )
    if readiness.is_empty():
        raise RuntimeError("여섯 비교군의 최소 30종목과 200일 벤치마크가 확보된 주가 없습니다.")
    first_ready_review = readiness.get_column("review_date").item(0)

    all_pattern = score_root / "peer_group=*" / "year=*" / "scores.parquet"
    candidate_ids = (
        pl.scan_parquet(str(all_pattern), hive_partitioning=True)
        .filter(pl.col("candidate_eligible").fill_null(False) & (pl.col("trend_score") >= 65))
        .select("asset_id")
        .unique()
        .collect()
        .get_column("asset_id")
        .to_list()
    )
    signal_columns = [
        "signal_date",
        "review_date",
        "asset_id",
        "peer_group",
        "trend_score",
        "relative_momentum",
        "data_eligible",
        "candidate_eligible",
        "benchmark_close",
        "benchmark_sma200",
    ]
    frames: list[pl.DataFrame] = []
    for group in PeerGroup:
        mapping = schedule.filter(
            (pl.col("peer_group") == group.value) & (pl.col("review_date") >= first_ready_review)
        ).select(pl.col("signal_date").alias("date"), "review_date")
        pattern = score_root / f"peer_group={group.value}" / "year=*" / "scores.parquet"
        frames.append(
            pl.scan_parquet(str(pattern), hive_partitioning=True)
            .filter(pl.col("asset_id").is_in(candidate_ids))
            .join(mapping.lazy(), on="date", how="inner")
            .rename({"date": "signal_date"})
            .select(signal_columns)
            .collect()
        )
    weekly = pl.concat(frames, how="diagonal_relaxed")
    actual_ids = (
        weekly.filter(pl.col("candidate_eligible").fill_null(False) & (pl.col("trend_score") >= 65))
        .get_column("asset_id")
        .unique()
        .to_list()
    )
    weekly = weekly.filter(pl.col("asset_id").is_in(actual_ids)).sort(
        ["review_date", "peer_group", "asset_id"]
    )
    metadata = (
        pl.scan_parquet(str(all_pattern), hive_partitioning=True)
        .filter(pl.col("asset_id").is_in(actual_ids))
        .select("asset_id", "symbol", "name", "peer_group", "currency")
        .unique("asset_id")
        .collect()
    )
    return weekly, metadata


class ResearchReplayEngine:
    def __init__(self, store: ResearchSnapshotStore) -> None:
        self.store = store
        self.scorer = PartitionedReplayScorer(store.root)

    def run(
        self,
        run_id: str,
        *,
        data_version: str,
        portfolio_config: PortfolioConfig,
        progress: ReplayProgress | None = None,
    ) -> ReplayBuild:
        snapshot = self.store.snapshot_path(data_version)
        manifest = json.loads((snapshot / "manifest.json").read_text(encoding="utf-8"))
        score_config = TrendScoreConfig()
        score_root, cache_key, cache_hit = self.scorer.build(
            snapshot,
            manifest,
            score_config=score_config,
            progress=progress,
        )
        if progress:
            progress("WEEKLY_SIGNALS", 0, 1)
        schedule = _completed_schedule(score_root)
        weekly, metadata = _weekly_signals(score_root, schedule)
        first_review = weekly.get_column("review_date").min()
        if not isinstance(first_review, date):
            raise RuntimeError("과거 재생의 시작 기준일을 결정할 수 없습니다.")
        candidate_ids = weekly.get_column("asset_id").unique().to_list()
        if progress:
            progress("LOAD_MARKET_EVENTS", 0, 1)
        bars = (
            self.store.scan_bars(snapshot_path=snapshot, start=first_review)
            .filter(pl.col("asset_id").is_in(candidate_ids))
            .select(
                "date",
                "asset_id",
                pl.col("open").cast(pl.Float64),
                pl.col("close").cast(pl.Float64),
                pl.col("split_ratio").cast(pl.Float64),
                pl.col("dividend").cast(pl.Float64),
                pl.col("recovery_value").cast(pl.Float64),
            )
            .collect()
        )
        reference_assets = (
            pl.scan_parquet(
                str(score_root / "peer_group=*" / "year=*" / "scores.parquet"),
                hive_partitioning=True,
            )
            .group_by(["peer_group", "asset_id"])
            .agg(pl.len().alias("sessions"))
            .sort(["peer_group", "sessions", "asset_id"], descending=[False, True, False])
            .group_by("peer_group", maintain_order=True)
            .first()
            .collect()
            .get_column("asset_id")
            .to_list()
        )
        reference = (
            self.store.scan_bars(snapshot_path=snapshot, start=first_review)
            .filter(pl.col("asset_id").is_in(reference_assets))
            .group_by(["date", "peer_group"])
            .agg(
                pl.col("benchmark_close").drop_nulls().first(),
                pl.col("fx_krw_per_usd").drop_nulls().first(),
            )
            .collect()
            .sort(["date", "peer_group"])
        )
        if progress:
            progress("PREPARE_MARKET", 0, 1)

        prepared = prepare_market_replay(
            bars,
            weekly,
            reference,
            portfolio_config=portfolio_config,
            asset_metadata=metadata,
        )
        if progress:
            progress("PREPARE_MARKET", 1, 1)

        def simulation_progress(completed: int, total: int) -> None:
            if progress:
                progress("SIMULATE_ACTUAL", completed, total)

        actual_run = simulate_prepared_replay(
            prepared,
            data_version=data_version,
            score_version=TREND_SCORE_VERSION,
            portfolio_config=portfolio_config,
            run_id=run_id,
            prices_are_split_adjusted=True,
            progress=simulation_progress,
        )
        if progress:
            progress("SIMULATE_NO_COST", 0, 1)
        no_cost_config = replace(
            portfolio_config,
            us_trade_cost=0.0,
            kr_trade_cost=0.0,
            initial_fx_cost=0.0,
        )
        no_cost_run = simulate_prepared_replay(
            prepared,
            data_version=data_version,
            score_version=TREND_SCORE_VERSION,
            portfolio_config=no_cost_config,
            run_id=f"{run_id}-no-cost",
            prices_are_split_adjusted=True,
        )
        if progress:
            progress("SIMULATE_NO_COST", 1, 1)
            progress("ANALYZE", 0, 1)
        analysis = analyze_replay(
            prepared,
            actual_run,
            no_cost_run,
            portfolio_config=portfolio_config,
        )
        for index, point in enumerate(actual_run.result.equity_curve):
            point["exposure_matched_benchmark"] = round(
                analysis.exposure_matched_curve[index], 0
            )
            point["no_cost_portfolio"] = round(no_cost_run.equity_values[index], 0)
        result = actual_run.result
        repaired = int(manifest.get("quality", {}).get("totals", {}).get("repaired_assets", 0))
        if repaired:
            result.warnings.append(
                f"공급자 복구 이력이 있는 종목 {repaired:,}개가 입력 데이터에 포함됩니다."
            )
        result.warnings.append(
            "과거 가격은 공급자의 분할 조정 단위를 사용해 분할 수량을 중복 적용하지 않습니다."
        )
        if progress:
            progress("ANALYZE", 1, 1)
        return ReplayBuild(
            result=result,
            actual_run=actual_run,
            no_cost_run=no_cost_run,
            prepared=prepared,
            analysis=analysis,
            cache_key=cache_key,
            cache_hit=cache_hit,
            score_root=score_root,
        )
