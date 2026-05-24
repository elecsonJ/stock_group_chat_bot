from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
import warnings
from zoneinfo import ZoneInfo

try:
    import pandas_market_calendars as mcal
except Exception:  # pragma: no cover
    mcal = None


@dataclass(frozen=True)
class ExchangeSession:
    state: str
    regular_session: bool
    reason: str
    calendar_name: str
    calendar_source: str
    local_time: str
    open_at: str = ""
    close_at: str = ""


class ExchangeCalendarService:
    """Exchange calendar helper with pandas-market-calendars when available and a weekday fallback."""

    CALENDAR_CANDIDATES = {
        "united_states": ["XNYS", "NYSE", "NASDAQ"],
        "korea": ["XKRX"],
        "korea_kospi": ["XKRX"],
        "korea_kosdaq": ["XKRX"],
        "japan": ["JPX", "XTKS"],
        "hong_kong": ["XHKG", "HKEX"],
        "china": ["SSE"],
        "china_shanghai": ["SSE", "XSHG"],
        "china_shenzhen": ["SZSE", "XSHE"],
        "united_kingdom": ["XLON", "LSE"],
        "canada": ["XTSE", "TSX"],
        "canada_tsx": ["XTSE", "TSX"],
        "canada_tsxv": ["XTSE"],
        "australia": ["XASX", "ASX"],
        "india_nse": ["XNSE", "NSE"],
        "india_bse": ["XBOM", "BSE"],
        "germany": ["XETR"],
        "france": ["XPAR"],
        "singapore": ["XSES", "SGX"],
        "brazil": ["BVMF"],
        "mexico": ["XMEX"],
    }

    def __init__(self):
        self._calendar_cache: dict[str, Any | None] = {}

    def session_state(self, instrument: Any, when: datetime | None = None) -> ExchangeSession:
        now = when or datetime.now()
        local_dt = self._to_market_time(now, getattr(instrument, "timezone", "UTC"))
        calendar_name, calendar = self._calendar_for_market(str(getattr(instrument, "market", "")))
        if calendar is not None:
            session = self._pandas_calendar_state(calendar_name, calendar, local_dt, getattr(instrument, "timezone", "UTC"))
            if session:
                return session
        return self._fallback_state(instrument, local_dt, calendar_name)

    def _calendar_for_market(self, market: str) -> tuple[str, Any | None]:
        candidates = self.CALENDAR_CANDIDATES.get(market, [])
        for name in candidates:
            if name in self._calendar_cache:
                cached = self._calendar_cache[name]
                if cached is not None:
                    return name, cached
                continue
            if mcal is None:
                self._calendar_cache[name] = None
                continue
            try:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    calendar = mcal.get_calendar(name)
                self._calendar_cache[name] = calendar
                return name, calendar
            except Exception:
                self._calendar_cache[name] = None
        return candidates[0] if candidates else "", None

    def _pandas_calendar_state(self, calendar_name: str, calendar: Any, local_dt: datetime, timezone: str) -> ExchangeSession | None:
        try:
            start = local_dt.strftime("%Y-%m-%d")
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                schedule = calendar.schedule(start_date=start, end_date=start)
            if schedule is None or getattr(schedule, "empty", True):
                return ExchangeSession(
                    state="holiday",
                    regular_session=False,
                    reason="exchange_calendar_closed",
                    calendar_name=calendar_name,
                    calendar_source="pandas_market_calendars",
                    local_time=local_dt.strftime("%Y-%m-%dT%H:%M:%S"),
                )
            row = schedule.iloc[0]
            open_at = row.get("market_open")
            close_at = row.get("market_close")
            zone = ZoneInfo(timezone)
            if getattr(open_at, "tzinfo", None) is None:
                open_at = open_at.tz_localize("UTC")
            if getattr(close_at, "tzinfo", None) is None:
                close_at = close_at.tz_localize("UTC")
            open_local = open_at.tz_convert(zone).to_pydatetime().replace(tzinfo=None)
            close_local = close_at.tz_convert(zone).to_pydatetime().replace(tzinfo=None)
            in_regular = open_local <= local_dt <= close_local
            return ExchangeSession(
                state="regular" if in_regular else "closed",
                regular_session=in_regular,
                reason="open" if in_regular else "outside_regular_hours",
                calendar_name=calendar_name,
                calendar_source="pandas_market_calendars",
                local_time=local_dt.strftime("%Y-%m-%dT%H:%M:%S"),
                open_at=open_local.strftime("%Y-%m-%dT%H:%M:%S"),
                close_at=close_local.strftime("%Y-%m-%dT%H:%M:%S"),
            )
        except Exception:
            return None

    def _fallback_state(self, instrument: Any, local_dt: datetime, calendar_name: str) -> ExchangeSession:
        open_time = datetime.strptime(str(getattr(instrument, "regular_open", "09:30")), "%H:%M").time()
        close_time = datetime.strptime(str(getattr(instrument, "regular_close", "16:00")), "%H:%M").time()
        is_weekday = local_dt.weekday() < 5
        in_regular = is_weekday and open_time <= local_dt.time() <= close_time
        state = "regular" if in_regular else ("closed" if is_weekday else "weekend")
        reason = "open" if in_regular else ("outside_regular_hours" if is_weekday else "weekend")
        return ExchangeSession(
            state=state,
            regular_session=in_regular,
            reason=reason,
            calendar_name=calendar_name,
            calendar_source="weekday_fallback",
            local_time=local_dt.strftime("%Y-%m-%dT%H:%M:%S"),
            open_at=f"{local_dt.strftime('%Y-%m-%d')}T{getattr(instrument, 'regular_open', '09:30')}:00",
            close_at=f"{local_dt.strftime('%Y-%m-%d')}T{getattr(instrument, 'regular_close', '16:00')}:00",
        )

    def _to_market_time(self, value: datetime, timezone: str) -> datetime:
        try:
            zone = ZoneInfo(timezone)
            if value.tzinfo is None:
                local_zone = datetime.now().astimezone().tzinfo
                value = value.replace(tzinfo=local_zone)
            return value.astimezone(zone).replace(tzinfo=None)
        except Exception:
            return value.replace(tzinfo=None)
