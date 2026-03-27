"""Daily market comparison logger — records system performance vs NSE/BSE indices.

Runs automatically after NSE EOD force-close (15:10 IST). Appends a markdown
table row to PERFORMANCE_LOG.md so performance can be tracked over time.
"""
import asyncio
import logging
import os
from datetime import datetime, timezone, date
from pathlib import Path
from typing import Dict, Optional

import yfinance as yf

logger = logging.getLogger("moonshotx.market_compare")

# Project root — PERFORMANCE_LOG.md lives here
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_LOG_FILE = _PROJECT_ROOT / "PERFORMANCE_LOG.md"

# India NSE indices to compare against (yfinance symbols)
_BENCHMARKS = {
    "^NSEI":     "NIFTY50",
    "^NSMIDCP":  "NIFTY MidCap",
    "^NSEBANK":  "Bank Nifty",
    "^CNXIT":    "Nifty IT",
    "^CNXAUTO":  "Nifty Auto",
    "^CNXPHARMA":"Nifty Pharma",
}

_HEADER = """# MoonshotX-IND — Daily Performance Log

> Auto-generated after each NSE close (15:10 IST). Compares MoonshotX-IND portfolio returns against Indian market indices.

---

## Daily Comparison

| Date | MoonshotX-IND | NIFTY50 | MidCap | BankNifty | IT | Auto | Pharma | Regime | Notes |
|------|---------------|---------|--------|-----------|----|------|--------|--------|-------|
"""


def _fetch_index_returns() -> Dict[str, Optional[float]]:
    """Fetch today's return for each benchmark index."""
    results = {}
    for sym in _BENCHMARKS:
        try:
            t = yf.Ticker(sym)
            hist = t.history(period="5d", interval="1d")
            if hist is None or len(hist) < 2:
                results[sym] = None
                continue
            last_close = float(hist["Close"].iloc[-1])
            prev_close = float(hist["Close"].iloc[-2])
            ret = (last_close / prev_close - 1) * 100
            results[sym] = round(ret, 2)
        except Exception as e:
            logger.warning(f"[MARKET_COMPARE] Failed to fetch {sym}: {e}")
            results[sym] = None
    return results


def _calc_portfolio_return(start_equity: float, end_equity: float) -> float:
    """Calculate portfolio daily return percentage."""
    if start_equity <= 0:
        return 0.0
    return round((end_equity / start_equity - 1) * 100, 2)


def _fmt_ret(val: Optional[float]) -> str:
    """Format return value for markdown table."""
    if val is None:
        return "N/A"
    sign = "+" if val >= 0 else ""
    return f"{sign}{val:.2f}%"


def _ensure_log_file():
    """Create the log file with header if it doesn't exist."""
    if not _LOG_FILE.exists():
        _LOG_FILE.write_text(_HEADER)
        logger.info(f"[MARKET_COMPARE] Created {_LOG_FILE}")


async def log_daily_comparison(
    start_equity: float,
    end_equity: float,
    regime: str,
    notes: str = "",
) -> dict:
    """Fetch index returns, calculate portfolio return, append to PERFORMANCE_LOG.md.

    Args:
        start_equity: Portfolio value at start of day (previous close equity).
        end_equity: Portfolio value at end of day.
        regime: Market regime for the day.
        notes: Optional notes (e.g., "Day 1", "new sizing logic").

    Returns:
        dict with all return values for broadcasting/DB storage.
    """
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    logger.info(f"[MARKET_COMPARE] Generating daily comparison for {today_str}")

    # Fetch index returns in a thread to avoid blocking
    index_returns = await asyncio.to_thread(_fetch_index_returns)

    portfolio_ret = _calc_portfolio_return(start_equity, end_equity)

    # Build the markdown table row
    row_parts = [
        today_str,
        f"**{_fmt_ret(portfolio_ret)}**",
    ]
    for sym in _BENCHMARKS:
        row_parts.append(_fmt_ret(index_returns.get(sym)))
    row_parts.append(regime)
    row_parts.append(notes)

    row = "| " + " | ".join(row_parts) + " |"

    # Append to log file
    _ensure_log_file()
    with open(_LOG_FILE, "a") as f:
        f.write(row + "\n")

    logger.info(f"[MARKET_COMPARE] Logged: MoonshotX-IND={_fmt_ret(portfolio_ret)} vs NIFTY50={_fmt_ret(index_returns.get('^NSEI'))} BankNifty={_fmt_ret(index_returns.get('^NSEBANK'))}")

    # Return summary for DB/broadcast
    summary = {
        "date": today_str,
        "portfolio_return_pct": portfolio_ret,
        "start_equity": start_equity,
        "end_equity": end_equity,
        "regime": regime,
        "index_returns": index_returns,
        "notes": notes,
        "logged_at": datetime.now(timezone.utc).isoformat(),
    }
    return summary
