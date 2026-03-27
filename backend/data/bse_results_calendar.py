"""
BSE/NSE quarterly results calendar — India equivalent of US earnings calendar.

Fetches upcoming and recent quarterly result dates from NSE India's API.
Used by morning_brief.py to flag stocks with imminent result dates
(avoid entering positions before results — binary event risk).
"""
import asyncio
import logging
from datetime import datetime, date, timedelta, timezone
from typing import Dict, List, Optional

logger = logging.getLogger("moonshotx.data.bse_results_calendar")

# Cache results for the session (reset daily)
_calendar_cache: Optional[List[Dict]] = None
_cache_date: Optional[date] = None


async def get_results_calendar(days_ahead: int = 7) -> List[Dict]:
    """
    Return list of companies reporting results in the next `days_ahead` days.
    Each entry: {symbol, company, result_date, result_type}
    """
    global _calendar_cache, _cache_date

    today = date.today()
    if _calendar_cache is not None and _cache_date == today:
        return _filter_upcoming(_calendar_cache, days_ahead)

    try:
        data = await asyncio.to_thread(_fetch_nse_results_calendar_sync)
        if data:
            _calendar_cache = data
            _cache_date = today
            return _filter_upcoming(data, days_ahead)
    except Exception as e:
        logger.warning(f"[RESULTS] Calendar fetch error: {e}")

    return []


def _filter_upcoming(calendar: List[Dict], days_ahead: int) -> List[Dict]:
    today = date.today()
    cutoff = today + timedelta(days=days_ahead)
    upcoming = []
    for entry in calendar:
        dt_str = entry.get("result_date", "")
        if not dt_str:
            continue
        try:
            dt = _parse_date(dt_str)
            if today <= dt <= cutoff:
                upcoming.append(entry)
        except Exception:
            continue
    return upcoming


def _parse_date(s: str) -> date:
    for fmt in ("%d-%b-%Y", "%d-%m-%Y", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Cannot parse date: {s}")


def _fetch_nse_results_calendar_sync() -> List[Dict]:
    """Synchronous NSE corporate results calendar fetch."""
    import requests
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        "Accept": "application/json",
        "Referer": "https://www.nseindia.com/",
    }
    session = requests.Session()
    session.get("https://www.nseindia.com", headers=headers, timeout=10)
    url = "https://www.nseindia.com/api/event-calendar"
    resp = session.get(url, headers=headers, timeout=15)
    resp.raise_for_status()
    raw = resp.json()
    entries = []
    for item in (raw if isinstance(raw, list) else raw.get("data", [])):
        symbol = item.get("symbol", "")
        company = item.get("company", item.get("companyName", ""))
        event_date = item.get("date", item.get("bm_date", ""))
        event_purpose = item.get("purpose", item.get("subject", ""))
        if not symbol or not event_date:
            continue
        # Only keep quarterly result events
        if any(kw in event_purpose.upper() for kw in [
            "QUARTERLY", "RESULTS", "FINANCIAL RESULT", "Q1", "Q2", "Q3", "Q4", "ANNUAL"
        ]):
            entries.append({
                "symbol": symbol,
                "company": company,
                "result_date": event_date,
                "result_type": event_purpose,
            })
    return entries


async def is_result_imminent(symbol: str, days: int = 3) -> bool:
    """
    Returns True if `symbol` has a result date within `days` days.
    Used as a gate in scanner.py — avoid entering before binary events.
    """
    calendar = await get_results_calendar(days_ahead=days)
    symbol_upper = symbol.upper()
    for entry in calendar:
        if entry.get("symbol", "").upper() == symbol_upper:
            return True
    return False


async def get_results_today() -> List[Dict]:
    """Return companies reporting results today (used in morning brief headline)."""
    calendar = await get_results_calendar(days_ahead=0)
    today = date.today()
    return [e for e in calendar if _parse_date_safe(e.get("result_date", "")) == today]


def _parse_date_safe(s: str) -> Optional[date]:
    try:
        return _parse_date(s)
    except Exception:
        return None
