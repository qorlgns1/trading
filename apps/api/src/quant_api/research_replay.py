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
from quant_core.config import PEER_GROUP_SLEEVE, REPLAY_SCORE_VERSION
from quant_core.enums import DataStatus, PeerGroup, ReviewFrequency, UniverseMode
from quant_core.market_portfolio import (
    MarketReplayRun,
    PreparedMarketReplay,
    prepare_market_replay,
    simulate_prepared_replay,
)
from quant_core.models import BacktestResult
from quant_core.replay_analysis import ReplayAnalysisBuild, analyze_replay
from quant_core.replay_validation import (
    build_stress_tests,
    build_validation,
    build_walk_forward,
)
from quant_core.scoring import compute_trend_features, project_trend_scores

from quant_api.research_quality import QUALITY_POLICY_VERSION
from quant_api.research_store import ResearchSnapshotStore

REPLAY_ENGINE_VERSION = "real-replay-v2.0.0"
REPLAY_SCORE_CACHE_VERSION = "replay-feature-cache-v2.0.0"
REPLAY_SCORE_COLUMNS = [
    "date",
    "asset_id",
    "symbol",
    "name",
    "peer_group",
    "currency",
    "trend_score",
    "relative_momentum",
    "vol60",
    "data_eligible",
    "candidate_eligible",
    "benchmark_close",
    "benchmark_sma200",
]
REPLAY_FEATURE_COLUMNS = [
    "date",
    "asset_id",
    "symbol",
    "name",
    "peer_group",
    "currency",
    "adjusted_close",
    "sma200",
    "r126",
    "relative_momentum",
    "vol60",
    "adv60",
    "data_eligible",
    "peer_count",
    "benchmark_close",
    "benchmark_sma200",
    "long_term_trend_unit",
    "absolute_momentum_unit",
    "relative_strength_unit",
    "high_proximity_unit",
    "volatility_stability_unit",
    "trading_activity_unit",
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
    validation: dict[str, Any] | None = None
    walk_forward: dict[str, Any] | None = None
    stress_tests: dict[str, Any] | None = None


@dataclass(frozen=True)
class ReplaySignalContext:
    snapshot: Path
    manifest: dict[str, Any]
    score_root: Path
    cache_key: str
    cache_hit: bool
    features_by_group: dict[PeerGroup, pl.DataFrame]


def _cache_key(manifest: dict[str, Any]) -> str:
    payload = {
        "cache_version": REPLAY_SCORE_CACHE_VERSION,
        "data_version": manifest["data_version"],
        "bars_sha256": manifest["bars_sha256"],
        "quality_policy": QUALITY_POLICY_VERSION,
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
        key = _cache_key(manifest)
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
                scored = compute_trend_features(combined)
                year = _year_from_path(path)
                output = scored.filter(pl.col("date").dt.year() == year).select(
                    REPLAY_FEATURE_COLUMNS
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
            "feature_version": REPLAY_SCORE_CACHE_VERSION,
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


def _period_key(current: date, frequency: ReviewFrequency) -> tuple[int, int]:
    if frequency is ReviewFrequency.WEEKLY:
        return current.isocalendar()[:2]
    return current.year, current.month


def _last_dates_by_period(
    paths: list[Path], frequency: ReviewFrequency
) -> dict[tuple[int, int], date]:
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
        key = _period_key(current, frequency)
        result[key] = max(current, result.get(key, current))
    return result


def _completed_schedule(
    score_root: Path,
    *,
    frequency: ReviewFrequency,
    enabled_groups: set[PeerGroup],
) -> pl.DataFrame:
    from quant_core.calendar import next_trading_date

    if frequency is ReviewFrequency.DAILY:
        daily_rows: list[dict[str, Any]] = []
        for group in sorted(enabled_groups, key=lambda item: item.value):
            paths = sorted((score_root / f"peer_group={group.value}").rglob("scores.parquet"))
            dates = (
                pl.scan_parquet([str(path) for path in paths])
                .select("date")
                .unique()
                .sort("date")
                .collect()
                .get_column("date")
                .to_list()
            )
            daily_rows.extend(
                {
                    "peer_group": group.value,
                    "signal_date": current,
                    "review_date": current,
                }
                for current in dates
            )
        if not daily_rows:
            raise RuntimeError("완결된 일별 신호가 없습니다.")
        return pl.DataFrame(daily_rows).sort(["review_date", "peer_group"])

    market_groups: dict[str, list[PeerGroup]] = {
        "US": [
            group
            for group in (PeerGroup.US_STOCK, PeerGroup.US_EQUITY_ETF)
            if group in enabled_groups
        ],
        "KR": [
            group
            for group in (
                PeerGroup.KR_KOSPI,
                PeerGroup.KR_KOSDAQ,
                PeerGroup.KR_DOMESTIC_EQUITY_ETF,
                PeerGroup.KR_OVERSEAS_EQUITY_ETF,
            )
            if group in enabled_groups
        ],
    }
    market_weeks: dict[str, dict[tuple[int, int], date]] = {}
    for market, groups in market_groups.items():
        if not groups:
            continue
        paths = sorted(
            path
            for group in groups
            for path in (score_root / f"peer_group={group.value}").rglob("scores.parquet")
        )
        weeks = _last_dates_by_period(paths, frequency)
        market_weeks[market] = {
            key: value
            for key, value in weeks.items()
            if _period_key(next_trading_date(value, market), frequency) != key
        }
    period_sets = [set(values) for values in market_weeks.values()]
    common = sorted(set.intersection(*period_sets)) if period_sets else []
    rows: list[dict[str, Any]] = []
    for key in common:
        review_date = max(values[key] for values in market_weeks.values())
        for market, groups in market_groups.items():
            if market not in market_weeks:
                continue
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
        raise RuntimeError("완결된 시장 평가 신호가 없습니다.")
    return pl.DataFrame(rows).sort(["review_date", "peer_group"])


def _scheduled_features(
    score_root: Path,
    schedule: pl.DataFrame,
    *,
    enabled_groups: set[PeerGroup],
    start: date | None = None,
    end: date | None = None,
    point_in_time_membership: pl.DataFrame | None = None,
) -> dict[PeerGroup, pl.DataFrame]:
    features_by_group: dict[PeerGroup, pl.DataFrame] = {}
    for group in sorted(enabled_groups, key=lambda item: item.value):
        mapping = schedule.filter(pl.col("peer_group") == group.value)
        if start is not None:
            mapping = mapping.filter(pl.col("review_date") >= start)
        if end is not None:
            mapping = mapping.filter(pl.col("review_date") <= end)
        mapping = mapping.select(pl.col("signal_date").alias("date"), "review_date")
        pattern = score_root / f"peer_group={group.value}" / "year=*" / "scores.parquet"
        features = (
            pl.scan_parquet(str(pattern), hive_partitioning=True)
            .join(mapping.lazy(), on="date", how="inner")
            .collect()
        )
        if features.is_empty():
            raise RuntimeError(f"{group.value} 비교군의 평가 신호가 없습니다.")
        if point_in_time_membership is not None:
            required = {"asset_id", "valid_from", "valid_to"}
            if not required.issubset(point_in_time_membership.columns):
                raise RuntimeError("시점 기준 종목군 파일에 필수 열이 없습니다.")
            membership = point_in_time_membership.select(
                "asset_id",
                pl.col("valid_from").cast(pl.Date),
                pl.col("valid_to").cast(pl.Date).fill_null(date.max),
            )
            features = (
                features.join(membership, on="asset_id", how="inner")
                .filter(
                    (pl.col("date") >= pl.col("valid_from"))
                    & (pl.col("date") <= pl.col("valid_to"))
                )
                .drop("valid_from", "valid_to")
            )
            duplicates = features.group_by("asset_id", "date").len().filter(pl.col("len") > 1)
            if not duplicates.is_empty():
                raise RuntimeError("시점 기준 종목군 기간이 서로 겹칩니다.")
            if features.is_empty():
                raise RuntimeError(f"{group.value}의 시점 기준 종목군 평가 신호가 없습니다.")
        features_by_group[group] = features
    return features_by_group


def _project_scheduled_features(
    features_by_group: dict[PeerGroup, pl.DataFrame],
    *,
    score_config: TrendScoreConfig,
    portfolio_config: PortfolioConfig,
    enabled_groups: set[PeerGroup],
) -> tuple[pl.DataFrame, pl.DataFrame]:
    projected_frames: list[pl.DataFrame] = []
    first_ready_dates: list[date] = []
    for group in sorted(enabled_groups, key=lambda item: item.value):
        features = features_by_group.get(group)
        if features is None or features.is_empty():
            raise RuntimeError(f"{group.value} 비교군의 평가 신호가 없습니다.")
        readiness = (
            features.group_by("review_date")
            .agg(
                pl.col("data_eligible").fill_null(False).sum().alias("eligible_assets"),
                pl.col("benchmark_sma200").is_not_null().any().alias("benchmark_ready"),
            )
            .filter(
                (pl.col("eligible_assets") >= score_config.minimum_peer_count)
                & pl.col("benchmark_ready")
            )
            .sort("review_date")
        )
        if readiness.is_empty():
            raise RuntimeError(f"{group.value} 비교군의 최소 종목 수와 벤치마크가 부족합니다.")
        first_ready_dates.append(readiness.get_column("review_date").item(0))
        projected_frames.append(
            project_trend_scores(features, score_config).rename({"date": "signal_date"})
        )
    first_ready = max(first_ready_dates)
    signals = pl.concat(projected_frames, how="diagonal_relaxed").filter(
        pl.col("review_date") >= first_ready
    )
    threshold = pl.col("peer_group").replace_strict(
        {group.value: portfolio_config.entry_score_for(group) for group in enabled_groups},
        default=portfolio_config.entry_score,
        return_dtype=pl.Float64,
    )
    actual_ids = (
        signals.filter(
            pl.col("candidate_eligible").fill_null(False) & (pl.col("trend_score") >= threshold)
        )
        .get_column("asset_id")
        .unique()
        .to_list()
    )
    if not actual_ids:
        raise RuntimeError("선택한 기간과 조건에 진입 가능한 후보가 없습니다.")
    signals = (
        signals.filter(pl.col("asset_id").is_in(actual_ids))
        .select(
            "signal_date",
            "review_date",
            *REPLAY_SCORE_COLUMNS[1:],
        )
        .sort(["review_date", "peer_group", "asset_id"])
    )
    metadata = signals.select("asset_id", "symbol", "name", "peer_group", "currency").unique(
        "asset_id"
    )
    return signals, metadata


def _scheduled_signals(
    score_root: Path,
    schedule: pl.DataFrame,
    *,
    score_config: TrendScoreConfig,
    portfolio_config: PortfolioConfig,
    enabled_groups: set[PeerGroup],
    start: date | None = None,
    end: date | None = None,
    point_in_time_membership: pl.DataFrame | None = None,
) -> tuple[pl.DataFrame, pl.DataFrame]:
    return _project_scheduled_features(
        _scheduled_features(
            score_root,
            schedule,
            enabled_groups=enabled_groups,
            start=start,
            end=end,
            point_in_time_membership=point_in_time_membership,
        ),
        score_config=score_config,
        portfolio_config=portfolio_config,
        enabled_groups=enabled_groups,
    )


class ResearchReplayEngine:
    def __init__(self, store: ResearchSnapshotStore) -> None:
        self.store = store
        self.scorer = PartitionedReplayScorer(store.root)

    @staticmethod
    def _point_in_time_membership(
        snapshot: Path,
        manifest: dict[str, Any],
        universe_mode: UniverseMode,
    ) -> pl.DataFrame | None:
        if universe_mode is not UniverseMode.POINT_IN_TIME:
            return None
        if not bool(manifest.get("supports_point_in_time", False)):
            raise RuntimeError("현재 데이터 스냅샷은 시점 기준 종목군을 지원하지 않습니다.")
        membership_value = manifest.get("point_in_time_membership_path")
        if not membership_value:
            raise RuntimeError("시점 기준 종목군 경로가 manifest에 없습니다.")
        membership_path = snapshot / str(membership_value)
        if not membership_path.is_file():
            raise RuntimeError("시점 기준 종목군 파일을 찾을 수 없습니다.")
        return pl.read_parquet(membership_path)

    def signal_context(
        self,
        *,
        data_version: str,
        score_config: TrendScoreConfig,
        frequency: ReviewFrequency,
        enabled_groups: set[PeerGroup],
        start_date: date | None,
        end_date: date | None,
        universe_mode: UniverseMode,
        progress: ReplayProgress | None = None,
    ) -> ReplaySignalContext:
        snapshot = self.store.snapshot_path(data_version)
        manifest = json.loads((snapshot / "manifest.json").read_text(encoding="utf-8"))
        membership = self._point_in_time_membership(snapshot, manifest, universe_mode)
        score_root, cache_key, cache_hit = self.scorer.build(
            snapshot,
            manifest,
            score_config=score_config,
            progress=progress,
        )
        schedule = _completed_schedule(
            score_root,
            frequency=frequency,
            enabled_groups=enabled_groups,
        )
        return ReplaySignalContext(
            snapshot=snapshot,
            manifest=manifest,
            score_root=score_root,
            cache_key=cache_key,
            cache_hit=cache_hit,
            features_by_group=_scheduled_features(
                score_root,
                schedule,
                enabled_groups=enabled_groups,
                start=start_date,
                end=end_date,
                point_in_time_membership=membership,
            ),
        )

    @staticmethod
    def project_context(
        context: ReplaySignalContext,
        *,
        score_config: TrendScoreConfig,
        portfolio_config: PortfolioConfig,
        enabled_groups: set[PeerGroup],
    ) -> tuple[pl.DataFrame, pl.DataFrame]:
        return _project_scheduled_features(
            context.features_by_group,
            score_config=score_config,
            portfolio_config=portfolio_config,
            enabled_groups=enabled_groups,
        )

    def prepare_market(
        self,
        *,
        data_version: str,
        score_root: Path,
        signals: pl.DataFrame,
        metadata: pl.DataFrame,
        portfolio_config: PortfolioConfig,
        end_date: date | None,
    ) -> PreparedMarketReplay:
        snapshot = self.store.snapshot_path(data_version)
        first_review = signals.get_column("review_date").min()
        if not isinstance(first_review, date):
            raise RuntimeError("과거 재생의 시작 기준일을 결정할 수 없습니다.")
        candidate_ids = signals.get_column("asset_id").unique().to_list()
        bars = (
            self.store.scan_bars(snapshot_path=snapshot, start=first_review)
            .filter(pl.col("asset_id").is_in(candidate_ids))
            .filter(pl.col("date") <= end_date if end_date is not None else pl.lit(True))
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
            .filter(pl.col("date") <= end_date if end_date is not None else pl.lit(True))
            .group_by(["date", "peer_group"])
            .agg(
                pl.col("benchmark_close").drop_nulls().first(),
                pl.col("fx_krw_per_usd").drop_nulls().first(),
            )
            .collect()
            .sort(["date", "peer_group"])
        )
        return prepare_market_replay(
            bars,
            signals,
            reference,
            portfolio_config=portfolio_config,
            asset_metadata=metadata,
        )

    def run(
        self,
        run_id: str,
        *,
        data_version: str,
        portfolio_config: PortfolioConfig,
        score_config: TrendScoreConfig | None = None,
        start_date: date | None = None,
        split_date: date | None = None,
        end_date: date | None = None,
        universe_mode: UniverseMode = UniverseMode.CURRENT_LISTED,
        walk_forward_train_years: int = 3,
        walk_forward_test_years: int = 1,
        walk_forward_step_years: int = 1,
        include_diagnostics: bool = True,
        progress: ReplayProgress | None = None,
    ) -> ReplayBuild:
        score_config = score_config or TrendScoreConfig(version=REPLAY_SCORE_VERSION)
        enabled_groups = {
            group
            for group in PeerGroup
            if portfolio_config.peer_group_slots[group] > 0
            and portfolio_config.sleeve_weights_bps[PEER_GROUP_SLEEVE[group]] > 0
        }
        if progress:
            progress("BUILD_SIGNALS", 0, 1)
        context = self.signal_context(
            data_version=data_version,
            score_config=score_config,
            frequency=portfolio_config.review_frequency,
            enabled_groups=enabled_groups,
            start_date=start_date,
            end_date=end_date,
            universe_mode=universe_mode,
            progress=progress,
        )
        signals, metadata = self.project_context(
            context,
            score_config=score_config,
            portfolio_config=portfolio_config,
            enabled_groups=enabled_groups,
        )
        manifest = context.manifest
        score_root = context.score_root
        cache_key = context.cache_key
        cache_hit = context.cache_hit
        del context
        if progress:
            progress("BUILD_SIGNALS", 1, 1)
            progress("LOAD_MARKET_EVENTS", 0, 1)
        if progress:
            progress("PREPARE_MARKET", 0, 1)
        prepared = self.prepare_market(
            data_version=data_version,
            score_root=score_root,
            signals=signals,
            metadata=metadata,
            portfolio_config=portfolio_config,
            end_date=end_date,
        )
        del signals, metadata
        if split_date is not None:
            split_index = next(
                (index for index, current in enumerate(prepared.dates) if current >= split_date),
                None,
            )
            if split_index is None or split_index < 252 or len(prepared.dates) - split_index < 252:
                raise RuntimeError("학습·검증 구간은 각각 최소 252 평가일이어야 합니다.")
        if progress:
            progress("PREPARE_MARKET", 1, 1)

        def simulation_progress(completed: int, total: int) -> None:
            if progress:
                progress("SIMULATE_ACTUAL", completed, total)

        actual_run = simulate_prepared_replay(
            prepared,
            data_version=data_version,
            score_version=score_config.version,
            portfolio_config=portfolio_config,
            run_id=run_id,
            prices_are_split_adjusted=True,
            progress=simulation_progress,
        )
        result = actual_run.result
        repaired = int(manifest.get("quality", {}).get("totals", {}).get("repaired_assets", 0))
        if repaired:
            result.warnings.append(
                f"공급자 복구 이력이 있는 종목 {repaired:,}개가 입력 데이터에 포함됩니다."
            )
        result.warnings.append(
            "과거 가격은 공급자의 분할 조정 단위를 사용해 분할 수량을 중복 적용하지 않습니다."
        )
        if universe_mode is UniverseMode.POINT_IN_TIME:
            result.warnings = [
                warning for warning in result.warnings if "현재 상장 종목 기준" not in warning
            ]
            result.warnings.append("각 평가일에 유효한 시점 기준 종목군을 사용했습니다.")
        if not include_diagnostics:
            return ReplayBuild(
                result=result,
                actual_run=actual_run,
                no_cost_run=actual_run,
                prepared=prepared,
                analysis=ReplayAnalysisBuild(
                    analysis={},
                    exposure_matched_curve=actual_run.benchmark_values,
                ),
                cache_key=cache_key,
                cache_hit=cache_hit,
                score_root=score_root,
            )
        if progress:
            progress("SIMULATE_NO_COST", 0, 1)
        no_cost_config = replace(
            portfolio_config,
            us_trade_cost=0.0,
            kr_trade_cost=0.0,
            initial_fx_cost=0.0,
            us_buy_cost=0.0,
            us_sell_cost=0.0,
            kr_buy_cost=0.0,
            kr_sell_cost=0.0,
            slippage_bps=0.0,
        )
        no_cost_run = simulate_prepared_replay(
            prepared,
            data_version=data_version,
            score_version=score_config.version,
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
            point["exposure_matched_benchmark"] = round(analysis.exposure_matched_curve[index], 0)
            point["no_cost_portfolio"] = round(no_cost_run.equity_values[index], 0)
        validation: dict[str, Any] | None = None
        walk_forward: dict[str, Any] | None = None
        stress_tests: dict[str, Any] | None = None
        if split_date is not None:
            if progress:
                progress("VALIDATE", 0, 3)
            validation, _ = build_validation(
                prepared,
                actual_run,
                split_date=split_date,
                data_version=data_version,
                score_version=score_config.version,
                portfolio_config=portfolio_config,
            )
            if progress:
                progress("VALIDATE", 1, 3)
            walk_forward = build_walk_forward(
                prepared,
                data_version=data_version,
                score_version=score_config.version,
                portfolio_config=portfolio_config,
                train_years=walk_forward_train_years,
                test_years=walk_forward_test_years,
                step_years=walk_forward_step_years,
            )
            if progress:
                progress("VALIDATE", 2, 3)
            stress_tests = build_stress_tests(
                prepared,
                actual_run,
                data_version=data_version,
                score_version=score_config.version,
                portfolio_config=portfolio_config,
            )
            if progress:
                progress("VALIDATE", 3, 3)
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
            validation=validation,
            walk_forward=walk_forward,
            stress_tests=stress_tests,
        )
