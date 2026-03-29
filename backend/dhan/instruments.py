"""NIFTY option security_id resolver for DhanHQ.

DhanHQ identifies every option contract by a numeric `security_id`.
This module fetches the compact instruments CSV from DhanHQ and builds
an in-memory lookup: (symbol, expiry, strike, opt_type) → security_id.

The CSV is refreshed once per day (or on-demand).
"""
import csv
import io
import logging
import os
from datetime import date, datetime, timedelta
from functools import lru_cache
from typing import Optional

import requests

logger = logging.getLogger("moonshotx.dhan.instruments")

COMPACT_CSV_URL  = "https://images.dhan.co/api-data/api-scrip-master.csv"
NIFTY_SECURITY_ID = "13"          # NIFTY 50 index — NSE segment
NIFTY_LOT_SIZE    = 25            # post Nov-2024
BANKNIFTY_LOT_SIZE = 15           # post Nov-2024
STRIKE_STEP       = 50            # NIFTY strike interval

_instrument_cache: dict = {}       # (symbol, expiry_str, strike, opt_type) → security_id
_cache_date: Optional[date] = None


def _refresh_cache() -> None:
    global _instrument_cache, _cache_date
    try:
        resp = requests.get(COMPACT_CSV_URL, timeout=30)
        resp.raise_for_status()
        reader = csv.DictReader(io.StringIO(resp.text))
        new_cache: dict = {}
        for row in reader:
            if row.get("SEM_EXM_EXCH_ID") != "NFO":
                continue
            sym    = row.get("SEM_TRADING_SYMBOL", "")
            s_id   = row.get("SEM_SMST_SECURITY_ID", "")
            expiry = row.get("SEM_EXPIRY_DATE", "")           # YYYY-MM-DD
            strike_str = row.get("SEM_STRIKE_PRICE", "0")
            inst   = row.get("SEM_INSTRUMENT_NAME", "")       # OPTIDX or FUTSTK
            series = row.get("SEM_OPTION_TYPE", "")           # CE or PE
            if not (s_id and expiry and series in ("CE", "PE")):
                continue
            try:
                strike = int(float(strike_str))
            except ValueError:
                continue
            # Normalise underlying name: 'NIFTY' appears in SEM_TRADING_SYMBOL
            underlying = sym.split(expiry[:4])[0].strip() if expiry else sym[:5]
            key = (underlying.upper(), expiry, strike, series)
            new_cache[key] = s_id
        _instrument_cache = new_cache
        _cache_date = date.today()
        logger.info("[DHAN] Instruments cache refreshed: %d NFO contracts", len(new_cache))
    except Exception as e:
        logger.error("[DHAN] Instruments cache refresh failed: %s", e)


def _ensure_cache() -> None:
    if _cache_date != date.today() or not _instrument_cache:
        _refresh_cache()


def get_security_id(
    underlying: str,
    expiry: str,        # 'YYYY-MM-DD'
    strike: int,
    opt_type: str,      # 'CE' or 'PE'
) -> Optional[str]:
    """Return DhanHQ security_id for a NIFTY/BANKNIFTY option contract."""
    _ensure_cache()
    key = (underlying.upper(), expiry, int(strike), opt_type.upper())
    sid = _instrument_cache.get(key)
    if not sid:
        logger.warning("[DHAN] Security ID not found: %s %s %d%s", underlying, expiry, strike, opt_type)
    return sid


def atm_strike(spot: float, step: int = STRIKE_STEP) -> int:
    """Round spot to nearest strike step."""
    return int(round(spot / step) * step)


def spread_strikes(spot: float, direction: str, width: int = 400) -> tuple:
    """
    Return (short_strike, long_strike, short_type, long_type) for a credit spread.

    Bullish  → sell ATM PE, buy ITM PE (ATM - width)
    Bearish  → sell ATM CE, buy OTM CE (ATM + width)
    """
    atm = atm_strike(spot)
    if direction == "bullish":
        return atm, atm - width, "PE", "PE"
    else:
        return atm, atm + width, "CE", "CE"


def get_spread_security_ids(
    underlying: str,
    expiry: str,
    spot: float,
    direction: str,
    width: int = 400,
) -> Optional[tuple]:
    """Return (short_sec_id, long_sec_id, short_strike, long_strike, opt_type) or None."""
    short_strike, long_strike, short_type, long_type = spread_strikes(spot, direction, width)
    short_id = get_security_id(underlying, expiry, short_strike, short_type)
    long_id  = get_security_id(underlying, expiry, long_strike,  long_type)
    if not short_id or not long_id:
        return None
    return short_id, long_id, short_strike, long_strike, short_type
