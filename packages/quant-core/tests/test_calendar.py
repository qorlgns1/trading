from datetime import UTC, date, datetime

from quant_core.calendar import latest_completed_trading_date, next_trading_date


def test_us_holiday_is_not_treated_as_trading_day() -> None:
    assert next_trading_date(date(2025, 7, 3), "US") == date(2025, 7, 7)


def test_korean_weekend_is_not_treated_as_trading_day() -> None:
    assert next_trading_date(date(2025, 7, 4), "KR") == date(2025, 7, 7)


def test_latest_completed_session_respects_market_close() -> None:
    before_us_close = datetime(2026, 7, 10, 18, tzinfo=UTC)
    after_us_close = datetime(2026, 7, 10, 21, tzinfo=UTC)
    assert latest_completed_trading_date(before_us_close, "US") == date(2026, 7, 9)
    assert latest_completed_trading_date(after_us_close, "US") == date(2026, 7, 10)
