"""
India trading universe — NIFTY500 constituent list + curated seed universe.

Provides:
  INDIA_SEED_UNIVERSE  — high-liquidity NSE large/mid cap momentum candidates
  INDIA_BROAD_WATCHLIST — broader NSE universe for scanner
  get_nifty500_symbols() — fetch live NIFTY500 constituents (cached)
"""
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import List, Optional
import functools
import time

logger = logging.getLogger("moonshotx.data.india_universe")

# ── Curated seed universe (high-liquidity NSE large-cap momentum names) ───────
# These are always scanned first every cycle. High-confidence, high-volume.
INDIA_SEED_UNIVERSE: List[str] = [
    # NIFTY50 core
    "RELIANCE", "TCS", "HDFCBANK", "INFY", "ICICIBANK",
    "HINDUNILVR", "ITC", "SBIN", "BAJFINANCE", "BHARTIARTL",
    "KOTAKBANK", "LT", "HCLTECH", "AXISBANK", "ASIANPAINT",
    "MARUTI", "TITAN", "SUNPHARMA", "ULTRACEMCO", "NTPC",
    "POWERGRID", "TECHM", "WIPRO", "ONGC", "M&M",
    "INDUSINDBK", "BAJAJ-AUTO", "ADANIENT", "ADANIPORTS", "COALINDIA",
    # High-momentum mid-cap additions
    "TATACONSUM", "TATAPOWER", "TATASTEEL", "TATAMOTORS", "JSWSTEEL",
    "HDFCLIFE", "SBILIFE", "BAJAJFINSV", "DIVISLAB", "CIPLA",
    "DRREDDY", "APOLLOHOSP", "DABUR", "BRITANNIA", "NESTLEIND",
    "PIDILITIND", "SIEMENS", "ABB", "VOLTAS", "HAVELLS",
    "MCDOWELL-N", "GRASIM", "HINDALCO", "VEDL", "SAIL",
    # IT momentum names
    "LTIM", "COFORGE", "PERSISTENT", "MPHASIS", "OFSS",
    # Banking / NBFC
    "FEDERALBNK", "BANDHANBNK", "IDFCFIRSTB", "RBLBANK", "PNB",
    # PSU momentum
    "BEL", "HAL", "IRFC", "RVNL", "IRCTC",
]

# ── Broad watchlist (NIFTY200 + select midcap) ────────────────────────────────
INDIA_BROAD_WATCHLIST: List[str] = INDIA_SEED_UNIVERSE + [
    "AUROPHARMA", "LUPIN", "BIOCON", "TORNTPHARM", "ALKEM",
    "MFSL", "CHOLAFIN", "MANAPPURAM", "MUTHOOTFIN", "LICHSGFIN",
    "BANKBARODA", "CANBK", "UNIONBANK", "INDHOTEL", "EICHERMOT",
    "HEROMOTOCO", "BALKRISIND", "CEATLTD", "MRF", "APOLLOTYRE",
    "ZOMATO", "NYKAA", "PAYTM", "POLICYBZR", "DELHIVERY",
    "TRENT", "ABFRL", "PAGEIND", "KALYANKJIL", "MANYAVAR",
    "DEEPAKNTR", "SOLARINDS", "AAVAS", "HOMEFIRST", "APTUS",
    "CAMS", "CDSL", "BSE", "MCX", "IEX",
    "CONCOR", "BLUEDART", "VRL", "DELHIVERY", "GICRE",
    "NIACL", "MFSL", "ICICIPRULI", "HDFCAMC", "NIPPONLIFE",
]

# ── NSE circuit limits (for circuit_breaker_gate in loop.py) ─────────────────
NSE_CIRCUIT_LIMITS_PCT = {
    "default": 0.20,   # 20% circuit filter for most NIFTY500 stocks
    "sme": 0.05,       # 5% for SME stocks
    "F&O": 0.20,       # 20% for F&O stocks (standard)
}

# ── NIFTY500 live fetch ───────────────────────────────────────────────────────
_nifty500_cache: Optional[List[str]] = None
_nifty500_fetched_at: Optional[datetime] = None
_NIFTY500_CACHE_TTL_HOURS = 24


def get_nifty500_symbols(force_refresh: bool = False) -> List[str]:
    """
    Return NIFTY500 constituent symbols.
    Fetches from NSE India website (CSV) and caches for 24h.
    Falls back to INDIA_BROAD_WATCHLIST if fetch fails.
    """
    global _nifty500_cache, _nifty500_fetched_at
    now = datetime.now(timezone.utc)

    if (
        not force_refresh
        and _nifty500_cache
        and _nifty500_fetched_at
        and (now - _nifty500_fetched_at).total_seconds() < _NIFTY500_CACHE_TTL_HOURS * 3600
    ):
        return _nifty500_cache

    try:
        symbols = _fetch_nifty500_from_nse()
        if symbols:
            _nifty500_cache = symbols
            _nifty500_fetched_at = now
            logger.info(f"[UNIVERSE] NIFTY500 refreshed: {len(symbols)} symbols")
            return symbols
    except Exception as e:
        logger.warning(f"[UNIVERSE] NIFTY500 fetch failed: {e} — using BROAD_WATCHLIST")

    return list(INDIA_BROAD_WATCHLIST)


def _fetch_nifty500_from_nse() -> List[str]:
    """Fetch NIFTY500 CSV from NSE India indices page."""
    import requests
    url = "https://www.nseindia.com/api/equity-stockIndices?index=NIFTY%20500"
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        "Accept": "application/json",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://www.nseindia.com/",
    }
    session = requests.Session()
    session.get("https://www.nseindia.com", headers=headers, timeout=10)
    resp = session.get(url, headers=headers, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    symbols = [item["symbol"] for item in data.get("data", []) if item.get("symbol")]
    return symbols


def get_seed_universe() -> List[str]:
    """Return the curated seed universe (always available, no network required)."""
    return list(INDIA_SEED_UNIVERSE)


def is_circuit_hit(symbol: str, price_change_pct: float) -> bool:
    """
    Returns True if price_change_pct (absolute value) exceeds the circuit limit.
    Used in circuit_breaker_gate() in loop.py.
    """
    limit = NSE_CIRCUIT_LIMITS_PCT.get("default", 0.20)
    return abs(price_change_pct) >= limit * 0.95   # 95% of limit = warning zone
