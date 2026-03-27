"""
India macro data — aggregates all macroeconomic signals needed by
morning_brief.py and regime.py:
  - India VIX, NIFTY50/BankNifty/Midcap returns
  - Global cues: SGX Nifty, US futures, Asia indices, DXY, Brent, Gold, USD/INR
  - FII/DII flows
  - RBI repo rate (static, updated manually until a reliable API is found)
"""
import asyncio
import logging
from datetime import datetime
from typing import Dict, Any
from zoneinfo import ZoneInfo

import yfinance as yf

logger = logging.getLogger("moonshotx.data.india_macro")

IST = ZoneInfo("Asia/Kolkata")

# ── RBI Policy Rate (manual — update when RBI changes rate) ──────────────────
RBI_REPO_RATE_PCT = 6.50   # percent, as of June 2025

# ── Global reference tickers ──────────────────────────────────────────────────
_GLOBAL_TICKERS = {
    "us_futures":    "ES=F",      # S&P 500 futures
    "nasdaq_futures": "NQ=F",     # NASDAQ 100 futures
    "vix_us":        "^VIX",
    "nikkei":        "^N225",
    "hsi":           "^HSI",
    "shanghai":      "000001.SS",
    "dxy":           "DX-Y.NYB",
    "brent":         "BZ=F",
    "gold":          "GC=F",
    "silver":        "SI=F",
    "usdinr":        "INR=X",
    "nifty50":       "^NSEI",
    "nifty_bank":    "^NSEBANK",
    "india_vix":     "^INDIAVIX",
    "nifty_midcap":  "^NSEMDCP50",
    "nifty_it":      "^CNXIT",
}


async def get_global_macro_snapshot() -> Dict[str, Any]:
    """
    Pull all global macro data in one batch.
    Returns dict with last_price, pct_change, direction for each index.
    """
    try:
        data = await asyncio.to_thread(_fetch_all_macro_sync)
        return data
    except Exception as e:
        logger.error(f"[MACRO] get_global_macro_snapshot error: {e}")
        return {}


def _fetch_all_macro_sync() -> Dict[str, Any]:
    """Synchronous batch fetch via yfinance."""
    all_tickers = list(_GLOBAL_TICKERS.values())
    results = {}
    for label, ticker in _GLOBAL_TICKERS.items():
        try:
            data = yf.download(ticker, period="2d", progress=False, auto_adjust=True)
            if data.empty or len(data) < 1:
                continue
            closes = data["Close"].dropna()
            if len(closes) == 0:
                continue
            last = float(closes.iloc[-1])
            prev = float(closes.iloc[-2]) if len(closes) >= 2 else last
            pct = (last - prev) / prev * 100 if prev != 0 else 0.0
            results[label] = {
                "price": round(last, 2),
                "pct_change": round(pct, 2),
                "direction": "up" if pct > 0 else ("down" if pct < 0 else "flat"),
            }
        except Exception as e:
            logger.debug(f"[MACRO] {label} ({ticker}) fetch error: {e}")
    return results


async def get_india_macro_summary() -> Dict[str, Any]:
    """
    High-level India-specific macro summary for morning_brief and regime.
    Combines market data with static macro parameters.
    """
    from data.india_market_feed import get_india_vix, get_nifty50_return, get_fii_dii_flow

    india_vix, nifty_ret, fii_dii, global_data = await asyncio.gather(
        get_india_vix(),
        get_nifty50_return(days=1),
        get_fii_dii_flow(),
        get_global_macro_snapshot(),
    )

    nifty50 = global_data.get("nifty50", {})
    nifty_bank = global_data.get("nifty_bank", {})
    nifty_it = global_data.get("nifty_it", {})
    nifty_mid = global_data.get("nifty_midcap", {})
    sgx_approx = nifty50.get("price", 0)  # using Nifty spot as proxy
    brent = global_data.get("brent", {})
    gold = global_data.get("gold", {})
    usdinr = global_data.get("usdinr", {})
    us_fut = global_data.get("us_futures", {})
    nikkei = global_data.get("nikkei", {})
    hsi = global_data.get("hsi", {})

    return {
        # India indices
        "india_vix": india_vix,
        "nifty50_pct": nifty50.get("pct_change", nifty_ret * 100),
        "nifty50_price": nifty50.get("price", 0),
        "nifty_bank_pct": nifty_bank.get("pct_change", 0),
        "nifty_it_pct": nifty_it.get("pct_change", 0),
        "nifty_midcap_pct": nifty_mid.get("pct_change", 0),
        # Institutional flows
        "fii_net_cr": round(fii_dii.get("fii_net", 0) / 1e7, 2),   # convert lakhs → crores
        "dii_net_cr": round(fii_dii.get("dii_net", 0) / 1e7, 2),
        "fii_direction": "buying" if fii_dii.get("fii_net", 0) > 0 else "selling",
        # Global cues
        "us_futures_pct": us_fut.get("pct_change", 0),
        "us_futures_direction": us_fut.get("direction", "flat"),
        "nikkei_pct": nikkei.get("pct_change", 0),
        "hsi_pct": hsi.get("pct_change", 0),
        "brent_price": brent.get("price", 0),
        "brent_pct": brent.get("pct_change", 0),
        "gold_price": gold.get("price", 0),
        "gold_pct": gold.get("pct_change", 0),
        "usdinr": usdinr.get("price", 84.0),
        "usdinr_pct": usdinr.get("pct_change", 0),
        # SGX Nifty proxy
        "sgx_nifty": sgx_approx,
        # Monetary policy
        "rbi_repo_rate_pct": RBI_REPO_RATE_PCT,
        # Full raw data (for LLM context)
        "global_raw": global_data,
    }


async def get_regime_macro_inputs() -> Dict[str, Any]:
    """
    Returns the minimal macro inputs needed by regime.py.
    Fast, targeted — called every loop tick.
    """
    from data.india_market_feed import get_india_vix, get_nifty50_return, get_advance_decline_ratio

    india_vix, nifty_1d, ad_ratio = await asyncio.gather(
        get_india_vix(),
        get_nifty50_return(days=1),
        get_advance_decline_ratio(sample_size=50),
    )
    advances, declines, ad_val = ad_ratio

    return {
        "india_vix": india_vix,
        "nifty_1d_return": nifty_1d,
        "advance_decline": ad_val,
        "advances": advances,
        "declines": declines,
    }
