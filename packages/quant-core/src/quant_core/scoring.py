import hashlib
import json
import math
from collections.abc import Mapping
from typing import Any

import polars as pl

from quant_core.config import TrendScoreConfig
from quant_core.enums import CandidateState, ExclusionCode, PeerGroup, WarningCode

REQUIRED_COLUMNS = {
    "date",
    "asset_id",
    "symbol",
    "name",
    "peer_group",
    "currency",
    "open",
    "close",
    "adjusted_close",
    "volume",
    "is_suspended",
    "is_supported",
    "benchmark_close",
    "fx_krw_per_usd",
}


def config_hash(config: TrendScoreConfig) -> str:
    payload = {
        "version": config.version,
        "candidate_threshold": config.candidate_threshold,
        "strong_candidate_threshold": config.strong_candidate_threshold,
        "retention_threshold": config.retention_threshold,
        "minimum_peer_count": config.minimum_peer_count,
        "order_to_adv_limit": config.order_to_adv_limit,
        "component_weights_bps": config.component_weights_bps,
        "require_above_sma200": config.require_above_sma200,
        "require_positive_six_month": config.require_positive_six_month,
        "require_absolute_liquidity": config.require_absolute_liquidity,
        "require_order_size_liquidity": config.require_order_size_liquidity,
        "minimum_adv_multiplier": config.minimum_adv_multiplier,
        "minimum_adv": {key.value: value for key, value in config.minimum_adv.items()},
        "planned_order_value": {
            key.value: value for key, value in config.planned_order_value.items()
        },
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()[:16]


def _pct_rank(expression: pl.Expr, *, descending: bool = False) -> pl.Expr:
    keys = ["date", "peer_group"]
    rank = expression.rank(method="average", descending=descending).over(keys)
    count = expression.count().over(keys)
    return pl.when(count > 1).then((rank - 1) / (count - 1)).otherwise(0.5)


def compute_trend_features(bars: pl.DataFrame) -> pl.DataFrame:
    """Build the fixed lookback features shared by every Trend Score projection."""
    missing = REQUIRED_COLUMNS - set(bars.columns)
    if missing:
        raise ValueError(f"필수 열이 없습니다: {sorted(missing)}")

    frame = bars.sort(["asset_id", "date"]).with_columns(
        pl.col("date").cast(pl.Date),
        (pl.col("close") * pl.col("volume")).alias("traded_value"),
        pl.col("adjusted_close").log().diff().over("asset_id").alias("log_return"),
    )
    frame = frame.with_columns(
        pl.col("adjusted_close").rolling_mean(50, min_samples=50).over("asset_id").alias("sma50"),
        pl.col("adjusted_close")
        .rolling_mean(200, min_samples=200)
        .over("asset_id")
        .alias("sma200"),
        pl.col("adjusted_close").shift(63).over("asset_id").alias("price_63"),
        pl.col("adjusted_close").shift(126).over("asset_id").alias("price_126"),
        pl.col("adjusted_close").shift(21).over("asset_id").alias("price_21"),
        pl.col("adjusted_close").shift(252).over("asset_id").alias("price_252"),
        pl.col("adjusted_close")
        .rolling_max(252, min_samples=252)
        .over("asset_id")
        .alias("high_252"),
        (
            pl.col("log_return").rolling_std(60, min_samples=55).over("asset_id") * math.sqrt(252)
        ).alias("vol60"),
        pl.col("traded_value").rolling_median(60, min_samples=55).over("asset_id").alias("adv60"),
        pl.col("adjusted_close")
        .is_not_null()
        .cast(pl.Int16)
        .rolling_sum(253, min_samples=1)
        .over("asset_id")
        .alias("valid_253"),
        pl.col("adjusted_close")
        .is_not_null()
        .cast(pl.Int16)
        .rolling_sum(60, min_samples=1)
        .over("asset_id")
        .alias("valid_60"),
        pl.col("benchmark_close")
        .rolling_mean(200, min_samples=200)
        .over("asset_id")
        .alias("benchmark_sma200"),
    )
    frame = frame.with_columns(
        pl.col("sma200").shift(20).over("asset_id").alias("sma200_20d_ago"),
        (pl.col("adjusted_close") / pl.col("price_63") - 1).alias("r63"),
        (pl.col("adjusted_close") / pl.col("price_126") - 1).alias("r126"),
        (pl.col("price_21") / pl.col("price_252") - 1).alias("r12_1"),
        (pl.col("adjusted_close") / pl.col("high_252")).alias("high_ratio"),
        (pl.col("adjusted_close") / pl.col("sma50") - 1).alias("sma50_distance"),
    )
    frame = frame.with_columns(
        (
            (pl.col("valid_253") >= 253)
            & (pl.col("valid_60") >= 55)
            & pl.col("adjusted_close").is_not_null()
            & pl.col("close").is_not_null()
            & pl.col("volume").is_not_null()
            & pl.col("is_supported")
            & ~pl.col("is_suspended")
        ).alias("data_eligible"),
        (0.20 * pl.col("r63") + 0.40 * pl.col("r126") + 0.40 * pl.col("r12_1")).alias(
            "relative_momentum"
        ),
    )
    eligible_momentum = pl.when(pl.col("data_eligible")).then(pl.col("relative_momentum"))
    eligible_volatility = pl.when(pl.col("data_eligible")).then(pl.col("vol60"))
    eligible_activity = pl.when(pl.col("data_eligible")).then((1 + pl.col("adv60")).log())
    eligible_overheat = pl.when(pl.col("data_eligible")).then(pl.col("sma50_distance"))
    frame = frame.with_columns(
        eligible_momentum.count().over(["date", "peer_group"]).alias("peer_count"),
        _pct_rank(eligible_momentum).alias("relative_strength_rank"),
        _pct_rank(eligible_volatility).alias("volatility_rank"),
        _pct_rank(eligible_activity).alias("activity_rank"),
        _pct_rank(eligible_overheat).alias("overheat_rank"),
    )

    return frame.with_columns(
        (
            15 * (pl.col("adjusted_close") > pl.col("sma200")).cast(pl.Int8)
            + 10 * (pl.col("sma50") > pl.col("sma200")).cast(pl.Int8)
            + 5 * (pl.col("sma200") > pl.col("sma200_20d_ago")).cast(pl.Int8)
        )
        .cast(pl.Float64)
        .truediv(30)
        .alias("long_term_trend_unit"),
        (
            5 * (pl.col("r63") > 0).cast(pl.Int8)
            + 10 * (pl.col("r126") > 0).cast(pl.Int8)
            + 10 * (pl.col("r12_1") > 0).cast(pl.Int8)
        )
        .cast(pl.Float64)
        .truediv(25)
        .alias("absolute_momentum_unit"),
        pl.col("relative_strength_rank").alias("relative_strength_unit"),
        ((pl.col("high_ratio") - 0.80) / 0.15)
        .clip(lower_bound=0, upper_bound=1)
        .alias("high_proximity_unit"),
        (1 - pl.col("volatility_rank")).alias("volatility_stability_unit"),
        pl.col("activity_rank").alias("trading_activity_unit"),
    )


def project_trend_scores(
    features: pl.DataFrame,
    config: TrendScoreConfig | None = None,
    *,
    data_version: str = "unknown",
) -> pl.DataFrame:
    """Apply configurable weights and eligibility gates to fixed trend features."""
    config = config or TrendScoreConfig()
    minimum_adv = pl.col("peer_group").replace_strict(
        {
            key.value: value * config.minimum_adv_multiplier
            for key, value in config.minimum_adv.items()
        },
        default=None,
        return_dtype=pl.Float64,
    )
    planned_order = pl.col("peer_group").replace_strict(
        {key.value: value for key, value in config.planned_order_value.items()},
        default=None,
        return_dtype=pl.Float64,
    )
    frame = features.with_columns(
        (pl.col("adv60") >= minimum_adv).alias("absolute_liquidity_eligible"),
        ((planned_order / pl.col("adv60")) <= config.order_to_adv_limit).alias(
            "order_size_eligible"
        ),
    )
    weights = config.component_weights_bps
    frame = frame.with_columns(
        (pl.col("long_term_trend_unit") * (weights["long_term_trend"] / 100)).alias(
            "long_term_trend_score"
        ),
        (pl.col("absolute_momentum_unit") * (weights["absolute_momentum"] / 100)).alias(
            "absolute_momentum_score"
        ),
        (pl.col("relative_strength_unit") * (weights["relative_strength"] / 100)).alias(
            "relative_strength_score"
        ),
        (pl.col("high_proximity_unit") * (weights["high_proximity"] / 100)).alias(
            "high_proximity_score"
        ),
        (pl.col("volatility_stability_unit") * (weights["volatility_stability"] / 100)).alias(
            "volatility_score"
        ),
        (pl.col("trading_activity_unit") * (weights["trading_activity"] / 100)).alias(
            "activity_score"
        ),
    )
    above_sma_gate = (
        pl.col("adjusted_close") > pl.col("sma200") if config.require_above_sma200 else pl.lit(True)
    )
    momentum_gate = pl.col("r126") > 0 if config.require_positive_six_month else pl.lit(True)
    absolute_liquidity_gate = (
        pl.col("absolute_liquidity_eligible").fill_null(False)
        if config.require_absolute_liquidity
        else pl.lit(True)
    )
    order_size_gate = (
        pl.col("order_size_eligible").fill_null(False)
        if config.require_order_size_liquidity
        else pl.lit(True)
    )
    frame = frame.with_columns(
        (
            pl.col("data_eligible")
            & (pl.col("peer_count") >= config.minimum_peer_count)
            & absolute_liquidity_gate
            & order_size_gate
            & above_sma_gate
            & momentum_gate
        ).alias("candidate_eligible"),
        pl.sum_horizontal(
            "long_term_trend_score",
            "absolute_momentum_score",
            "relative_strength_score",
            "high_proximity_score",
            "volatility_score",
            "activity_score",
        )
        .round(1)
        .alias("trend_score"),
    )
    state = (
        pl.when(~pl.col("data_eligible") | (pl.col("peer_count") < config.minimum_peer_count))
        .then(pl.lit(CandidateState.NOT_AVAILABLE.value))
        .when(~pl.col("candidate_eligible"))
        .then(pl.lit(CandidateState.EXCLUDED.value))
        .when(pl.col("trend_score") >= config.strong_candidate_threshold)
        .then(pl.lit(CandidateState.STRONG_CANDIDATE.value))
        .when(pl.col("trend_score") >= config.candidate_threshold)
        .then(pl.lit(CandidateState.CANDIDATE.value))
        .when(pl.col("trend_score") >= 50)
        .then(pl.lit(CandidateState.WATCH.value))
        .otherwise(pl.lit(CandidateState.WEAK.value))
    )
    return frame.with_columns(
        state.alias("candidate_state"),
        pl.lit(config.version).alias("score_version"),
        pl.lit(data_version).alias("data_version"),
        pl.lit(config_hash(config)).alias("score_config_hash"),
    )


def score_trends(
    bars: pl.DataFrame,
    config: TrendScoreConfig | None = None,
    *,
    data_version: str = "unknown",
) -> pl.DataFrame:
    """Calculate a deterministic Trend Score projection without mutating input data."""
    return project_trend_scores(
        compute_trend_features(bars),
        config,
        data_version=data_version,
    )


def exclusion_codes(row: Mapping[str, Any], config: TrendScoreConfig | None = None) -> list[str]:
    config = config or TrendScoreConfig()
    codes: list[str] = []
    if int(row.get("valid_253") or 0) < 253:
        codes.append(ExclusionCode.INSUFFICIENT_HISTORY.value)
    elif int(row.get("valid_60") or 0) < 55:
        codes.append(ExclusionCode.MISSING_DATA.value)
    if not bool(row.get("is_supported", True)):
        codes.append(ExclusionCode.INSTRUMENT_NOT_SUPPORTED.value)
    if bool(row.get("is_suspended", False)):
        codes.append(ExclusionCode.SUSPENDED.value)
    if int(row.get("peer_count") or 0) < config.minimum_peer_count:
        codes.append(ExclusionCode.INSUFFICIENT_PEERS.value)
    if config.require_above_sma200 and (
        row.get("adjusted_close") is not None
        and row.get("sma200") is not None
        and float(row["adjusted_close"]) <= float(row["sma200"])
    ):
        codes.append(ExclusionCode.BELOW_SMA200.value)
    if (
        config.require_positive_six_month
        and row.get("r126") is not None
        and float(row["r126"]) <= 0
    ):
        codes.append(ExclusionCode.NEGATIVE_6M_MOMENTUM.value)
    if config.require_absolute_liquidity and row.get("absolute_liquidity_eligible") is False:
        codes.append(ExclusionCode.LOW_ABSOLUTE_LIQUIDITY.value)
    if config.require_order_size_liquidity and row.get("order_size_eligible") is False:
        codes.append(ExclusionCode.ORDER_TOO_LARGE.value)
    return list(dict.fromkeys(codes))


def warning_codes(row: Mapping[str, Any]) -> list[str]:
    warnings: list[str] = []
    if (
        row.get("benchmark_close") is not None
        and row.get("benchmark_sma200") is not None
        and float(row["benchmark_close"]) <= float(row["benchmark_sma200"])
    ):
        warnings.append(WarningCode.WEAK_MARKET.value)
    if (
        row.get("adjusted_close") is not None
        and row.get("sma50") is not None
        and float(row["adjusted_close"]) <= float(row["sma50"])
    ):
        warnings.append(WarningCode.BELOW_SMA50.value)
    if float(row.get("volatility_rank") or 0) >= 0.8:
        warnings.append(WarningCode.HIGH_VOLATILITY.value)
    if float(row.get("overheat_rank") or 0) >= 0.95:
        warnings.append(WarningCode.SHORT_TERM_OVERHEAT.value)
    if row.get("peer_group") in {
        PeerGroup.US_STOCK.value,
        PeerGroup.US_EQUITY_ETF.value,
        PeerGroup.KR_OVERSEAS_EQUITY_ETF.value,
    }:
        warnings.append(WarningCode.FX_EXPOSURE.value)
    return warnings


def explain_result(row: Mapping[str, Any]) -> dict[str, list[str]]:
    reasons: list[str] = []
    if row.get("sma200") and float(row["adjusted_close"]) > float(row["sma200"]):
        distance = (float(row["adjusted_close"]) / float(row["sma200"]) - 1) * 100
        reasons.append(f"현재가는 200일 이동평균선보다 {distance:.1f}% 높습니다.")
    if all(float(row.get(key) or -1) > 0 for key in ("r63", "r126", "r12_1")):
        reasons.append("3개월, 6개월, 12-1개월 수익률이 모두 양수입니다.")
    rank = row.get("relative_strength_rank")
    if rank is not None and float(rank) >= 0.5:
        reasons.append(f"복합 모멘텀은 비교군 상위 {(1 - float(rank)) * 100:.0f}%입니다.")
    if float(row.get("high_ratio") or 0) >= 0.95:
        reasons.append("현재 가격이 최근 52주 고점에 가깝습니다.")

    warning_text = {
        WarningCode.WEAK_MARKET.value: "시장 전체 흐름이 200일 이동평균선 아래에 있습니다.",
        WarningCode.BELOW_SMA50.value: "장기 추세는 유지되지만 최근 흐름은 약해졌습니다.",
        WarningCode.HIGH_VOLATILITY.value: "같은 유형보다 최근 가격 등락이 큰 편입니다.",
        WarningCode.SHORT_TERM_OVERHEAT.value: "단기 급등으로 50일 이동평균선과 거리가 큽니다.",
        WarningCode.FX_EXPOSURE.value: "원화 수익률은 환율 변화의 영향을 받을 수 있습니다.",
    }
    exclusion_text = {
        ExclusionCode.INSUFFICIENT_HISTORY.value: "점수 계산에 필요한 가격 이력이 부족합니다.",
        ExclusionCode.MISSING_DATA.value: "최근 가격 또는 거래량 데이터가 충분하지 않습니다.",
        ExclusionCode.INSUFFICIENT_PEERS.value: "같은 비교군의 유효 종목 수가 부족합니다.",
        ExclusionCode.INSTRUMENT_NOT_SUPPORTED.value: "현재 지원하지 않는 상품 유형입니다.",
        ExclusionCode.SUSPENDED.value: "현재 거래할 수 없는 상태입니다.",
        ExclusionCode.BELOW_SMA200.value: "현재가가 200일 이동평균선 아래에 있습니다.",
        ExclusionCode.NEGATIVE_6M_MOMENTUM.value: "최근 6개월 수익률이 0 이하입니다.",
        ExclusionCode.LOW_ABSOLUTE_LIQUIDITY.value: "최근 거래대금이 최소 기준보다 적습니다.",
        ExclusionCode.ORDER_TOO_LARGE.value: "계획 주문금액이 최근 거래대금에 비해 큽니다.",
    }
    warning_messages = [warning_text[code] for code in warning_codes(row) if code in warning_text]
    exclusion_messages = [
        exclusion_text[code] for code in exclusion_codes(row) if code in exclusion_text
    ]
    return {
        "reasons": reasons[:3],
        "warnings": warning_messages[:2],
        "exclusions": exclusion_messages,
    }
