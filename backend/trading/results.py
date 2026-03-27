"""
India Quarterly Results Gate — replaces US earnings.py for NSE.

NSE/BSE companies announce quarterly results during trading hours.
Entering a position before a result announcement is a binary-event risk
(like trading into US earnings). This module gates entries and exits
around result dates.

Key rules:
  - Block NEW entries if result is within RESULTS_BLOCK_DAYS_BEFORE days.
  - Flag EXISTING positions with imminent results for early exit.
  - Log a daily results roster at session start (used in morning brief).
"""
import asyncio
import logging
from datetime import date, datetime, timedelta, timezone
from typing import Dict, List, Optional, Set

logger = logging.getLogger("moonshotx.results")

RESULTS_BLOCK_DAYS_BEFORE = 2   # block entry N days before result date
RESULTS_EXIT_DAYS_BEFORE  = 1   # flag for exit N days before result date

_calendar_cache: List[Dict] = []
_cache_loaded_date: Optional[date] = None
_imminent_symbols: Set[str] = set()   # symbols with results within block window


async def _ensure_calendar_loaded() -> None:
    """Lazy-load results calendar once per session day."""
    global _calendar_cache, _cache_loaded_date, _imminent_symbols

    today = date.today()
    if _cache_loaded_date == today and _calendar_cache is not None:
        return

    try:
        from data.bse_results_calendar import get_results_calendar
        data = await get_results_calendar(days_ahead=10)
        _calendar_cache = data or []
        _cache_loaded_date = today
        _imminent_symbols = {
            e["symbol"].upper()
            for e in _calendar_cache
            if _within_days(e.get("result_date", ""), RESULTS_BLOCK_DAYS_BEFORE)
        }
        if _imminent_symbols:
            logger.info(
                f"[RESULTS] {len(_imminent_symbols)} symbols have results within "
                f"{RESULTS_BLOCK_DAYS_BEFORE} days: {sorted(_imminent_symbols)}"
            )
    except Exception as e:
        logger.warning(f"[RESULTS] Calendar load failed: {e}")
        _calendar_cache = []
        _cache_loaded_date = today
        _imminent_symbols = set()


def _within_days(date_str: str, n: int) -> bool:
    """Return True if date_str falls within the next n days (inclusive)."""
    if not date_str:
        return False
    today = date.today()
    cutoff = today + timedelta(days=n)
    for fmt in ("%d-%b-%Y", "%d-%m-%Y", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            dt = datetime.strptime(date_str, fmt).date()
            return today <= dt <= cutoff
        except ValueError:
            continue
    return False


async def block_entry_for_results(symbol: str) -> bool:
    """
    Returns True if entry should be BLOCKED because the symbol has a
    quarterly result announcement within RESULTS_BLOCK_DAYS_BEFORE days.
    """
    await _ensure_calendar_loaded()
    blocked = symbol.upper() in _imminent_symbols
    if blocked:
        logger.info(f"[RESULTS] BLOCKED entry for {symbol} — result imminent")
    return blocked


async def flag_exit_for_results(symbol: str) -> bool:
    """
    Returns True if an EXISTING position should be considered for early exit
    because a result is within RESULTS_EXIT_DAYS_BEFORE days.
    """
    await _ensure_calendar_loaded()
    today = date.today()
    for entry in _calendar_cache:
        if entry.get("symbol", "").upper() != symbol.upper():
            continue
        if _within_days(entry.get("result_date", ""), RESULTS_EXIT_DAYS_BEFORE):
            result_date = entry.get("result_date", "?")
            logger.info(
                f"[RESULTS] EXIT flag for {symbol} — result on {result_date}"
            )
            return True
    return False


async def get_results_roster() -> Dict:
    """
    Return a summary roster for logging / morning brief:
      today    → list of {symbol, result_type}
      this_week → list of {symbol, result_date, result_type}
    """
    await _ensure_calendar_loaded()
    today_str = date.today().isoformat()

    today_results = [
        e for e in _calendar_cache
        if _within_days(e.get("result_date", ""), 0)
    ]
    week_results = _calendar_cache  # already filtered to 10 days

    return {
        "today": today_results,
        "this_week": week_results,
        "blocked_symbols": sorted(_imminent_symbols),
        "as_of": datetime.now(timezone.utc).isoformat(),
    }


async def log_results_roster() -> None:
    """Log today's and this week's results roster at session start."""
    roster = await get_results_roster()
    today_list = [f"{e['symbol']} ({e.get('result_type', '')})" for e in roster["today"]]
    week_list  = [f"{e['symbol']} on {e.get('result_date', '?')}" for e in roster["this_week"]]

    if today_list:
        logger.info(f"[RESULTS] Today's results: {', '.join(today_list)}")
    else:
        logger.info("[RESULTS] No quarterly results announced today")

    if week_list:
        logger.info(f"[RESULTS] This week's results: {', '.join(week_list[:10])}")
