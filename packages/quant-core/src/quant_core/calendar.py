from datetime import UTC, date, datetime, timedelta
from typing import cast

import exchange_calendars as xcals

CALENDAR_BY_MARKET = {
    "US": "XNYS",
    "KR": "XKRX",
}


def next_trading_date(signal_date: date, market: str) -> date:
    """Return the first exchange session strictly after a signal date."""
    calendar_name = CALENDAR_BY_MARKET.get(market)
    if calendar_name is None:
        raise ValueError(f"지원하지 않는 시장입니다: {market}")
    calendar = xcals.get_calendar(calendar_name)
    sessions = calendar.sessions_in_range(
        signal_date + timedelta(days=1), signal_date + timedelta(days=14)
    )
    if len(sessions) == 0:
        raise ValueError("14일 안에 다음 거래일을 찾지 못했습니다.")
    return cast(date, sessions[0].date())


def latest_completed_trading_date(now: datetime, market: str) -> date:
    """Return the most recent exchange session whose regular close has passed."""
    calendar_name = CALENDAR_BY_MARKET.get(market)
    if calendar_name is None:
        raise ValueError(f"지원하지 않는 시장입니다: {market}")
    normalized = now.astimezone(UTC)
    calendar = xcals.get_calendar(calendar_name)
    sessions = calendar.sessions_in_range(
        normalized.date() - timedelta(days=14), normalized.date()
    )
    if len(sessions) == 0:
        raise ValueError("14일 안에 완료 거래일을 찾지 못했습니다.")
    latest = sessions[-1]
    if calendar.session_close(latest).to_pydatetime() > normalized:
        latest = calendar.previous_session(latest)
    return cast(date, latest.date())
