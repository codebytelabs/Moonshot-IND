"""
India market feed — real-time and historical OHLCV data via Kite + yfinance fallback.

Primary: Zerodha Kite historical API (intraday + daily)
Fallback: yfinance for daily OHLCV (NSE prefix: "SYMBOL.NS")

Used by scanner.py, regime.py, morning_brief.py.
"""
import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone, date
from typing import Dict, List, Optional, Tuple

import pandas as pd
import yfinance as yf
from zoneinfo import ZoneInfo

logger = logging.getLogger("moonshotx.data.india_market_feed")

IST = ZoneInfo("Asia/Kolkata")


def _ist_now() -> datetime:
    return datetime.now(IST)


# ── Kite-backed data fetchers ─────────────────────────────────────────────────

async def get_ohlcv_kite(
    symbol: str,
    interval: str = "day",
    days: int = 60,
    exchange: str = "NSE",
) -> pd.DataFrame:
    """
    Fetch OHLCV bars from Kite historical API.
    interval: minute, 3minute, 5minute, 10minute, 15minute, 30minute, 60minute, day
    Returns DataFrame with columns: date, open, high, low, close, volume
    """
    try:
        return await asyncio.to_thread(
            _fetch_kite_bars_sync, symbol, interval, days, exchange
        )
    except Exception as e:
        logger.warning(f"[FEED] Kite OHLCV failed for {symbol}: {e} — falling back to yfinance")
        return await get_ohlcv_yfinance(symbol, days=days)


def _fetch_kite_bars_sync(
    symbol: str,
    interval: str,
    days: int,
    exchange: str,
) -> pd.DataFrame:
    """Synchronous Kite historical fetch."""
    from kiteconnect import KiteConnect
    api_key = os.getenv("Zerodha_KITE_PAID_API_KEY")
    access_token = os.getenv("Zerodha_KITE_PAID_ACCESS_TOKEN", "")
    if not api_key or not access_token:
        raise ValueError("Kite credentials not set in env")

    kite = KiteConnect(api_key=api_key)
    kite.set_access_token(access_token)

    instruments = kite.instruments(exchange)
    token = next((i["instrument_token"] for i in instruments if i["tradingsymbol"] == symbol), None)
    if not token:
        raise ValueError(f"Instrument not found: {exchange}:{symbol}")

    to_dt = datetime.now(IST)
    from_dt = to_dt - timedelta(days=days)
    data = kite.historical_data(
        instrument_token=token,
        from_date=from_dt.strftime("%Y-%m-%d %H:%M:%S"),
        to_date=to_dt.strftime("%Y-%m-%d %H:%M:%S"),
        interval=interval,
    )
    if not data:
        return pd.DataFrame()
    df = pd.DataFrame(data)
    df.rename(columns={"date": "date", "open": "open", "high": "high", "low": "low",
                        "close": "close", "volume": "volume"}, inplace=True)
    return df


async def get_ohlcv_yfinance(symbol: str, days: int = 60) -> pd.DataFrame:
    """
    Fallback: fetch daily OHLCV from yfinance using NSE suffix (.NS).
    """
    try:
        ticker = symbol if symbol.endswith(".NS") or symbol.endswith(".BO") else f"{symbol}.NS"
        period = f"{max(days, 5)}d"
        df = await asyncio.to_thread(_yf_download_sync, ticker, period)
        return df
    except Exception as e:
        logger.error(f"[FEED] yfinance OHLCV failed for {symbol}: {e}")
        return pd.DataFrame()


def _yf_download_sync(ticker: str, period: str) -> pd.DataFrame:
    data = yf.download(ticker, period=period, progress=False, auto_adjust=True)
    if data.empty:
        return pd.DataFrame()
    data = data.reset_index()
    data.columns = [c.lower() for c in data.columns]
    return data


# ── Index / reference data ────────────────────────────────────────────────────

# yfinance tickers for major Indian indices and macro refs
INDIA_INDEX_TICKERS = {
    "nifty50":      "^NSEI",
    "nifty_bank":   "^NSEBANK",
    "nifty_it":     "^CNXIT",
    "nifty_fmcg":   "^CNXFMCG",
    "nifty_pharma": "^CNXPHARMA",
    "india_vix":    "^INDIAVIX",
    "sensex":       "^BSESN",
    "nifty_midcap": "^NSEMDCP50",
    "nifty500":     "^CRSLDX",
    # Global references
    "sgx_nifty":    "IN:SGXNIFTY",   # may not be available; fallback
    "nasdaq":       "^IXIC",
    "sp500":        "^GSPC",
    "dxy":          "DX-Y.NYB",
    "brent":        "BZ=F",
    "gold":         "GC=F",
    "usdinr":       "INR=X",
}


async def get_index_data(keys: List[str], period: str = "5d") -> Dict[str, pd.DataFrame]:
    """
    Fetch OHLCV for multiple indices.
    keys: list of keys from INDIA_INDEX_TICKERS
    """
    tickers = [INDIA_INDEX_TICKERS[k] for k in keys if k in INDIA_INDEX_TICKERS]
    if not tickers:
        return {}
    try:
        result = await asyncio.to_thread(_yf_download_multi_sync, tickers, period)
        return result
    except Exception as e:
        logger.error(f"[FEED] get_index_data error: {e}")
        return {}


def _yf_download_multi_sync(tickers: List[str], period: str) -> Dict[str, pd.DataFrame]:
    data = {}
    for ticker in tickers:
        try:
            df = yf.download(ticker, period=period, progress=False, auto_adjust=True)
            if not df.empty:
                data[ticker] = df
        except Exception as e:
            logger.warning(f"[FEED] yf download {ticker} failed: {e}")
    return data


async def get_nifty50_return(days: int = 1) -> float:
    """Returns NIFTY50 % return over last `days` sessions."""
    try:
        df = await get_ohlcv_yfinance("^NSEI", days=days + 5)
        if df.empty or len(df) < 2:
            return 0.0
        closes = df["close"].dropna().values
        if len(closes) < 2:
            return 0.0
        return float((closes[-1] - closes[-2]) / closes[-2])
    except Exception as e:
        logger.warning(f"[FEED] get_nifty50_return error: {e}")
        return 0.0


async def get_india_vix() -> float:
    """Returns latest India VIX value."""
    try:
        df = await get_ohlcv_yfinance("^INDIAVIX", days=5)
        if df.empty:
            return 15.0
        last = df["close"].dropna().iloc[-1]
        return float(last)
    except Exception as e:
        logger.warning(f"[FEED] get_india_vix error: {e}")
        return 15.0


async def get_advance_decline_ratio(sample_size: int = 100) -> Tuple[int, int, float]:
    """
    Compute advance/decline ratio from a sample of the broad universe.
    Returns (advances, declines, ratio).
    """
    try:
        from data.india_universe import get_seed_universe
        symbols = get_seed_universe()[:sample_size]
        advances, declines = 0, 0
        for sym in symbols:
            try:
                df = await get_ohlcv_yfinance(sym, days=3)
                if not df.empty and len(df) >= 2:
                    closes = df["close"].dropna().values
                    if closes[-1] > closes[-2]:
                        advances += 1
                    else:
                        declines += 1
            except Exception:
                continue
        total = advances + declines
        ratio = advances / total if total > 0 else 0.5
        return advances, declines, ratio
    except Exception as e:
        logger.error(f"[FEED] advance_decline error: {e}")
        return 50, 50, 0.5


# ── FII / DII flow data ───────────────────────────────────────────────────────

async def get_fii_dii_flow() -> dict:
    """
    Fetch latest FII/DII provisional flow data from NSE.
    Returns dict with fii_net, dii_net, date.
    """
    try:
        result = await asyncio.to_thread(_fetch_fii_dii_sync)
        return result
    except Exception as e:
        logger.warning(f"[FEED] FII/DII fetch error: {e}")
        return {"fii_net": 0.0, "dii_net": 0.0, "date": "", "error": str(e)}


def _fetch_fii_dii_sync() -> dict:
    """Synchronous NSE FII/DII fetch."""
    import requests
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        "Accept": "application/json",
        "Referer": "https://www.nseindia.com/",
    }
    session = requests.Session()
    session.get("https://www.nseindia.com", headers=headers, timeout=10)
    url = "https://www.nseindia.com/api/fiidiiTradeReact"
    resp = session.get(url, headers=headers, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    if not data:
        return {"fii_net": 0.0, "dii_net": 0.0, "date": ""}
    latest = data[0] if isinstance(data, list) else data
    fii_net = float(latest.get("fiiNet", 0) or 0)
    dii_net = float(latest.get("diiNet", 0) or 0)
    dt = latest.get("date", "")
    return {"fii_net": fii_net, "dii_net": dii_net, "date": dt}


async def get_sgx_nifty() -> float:
    """Fetch SGX Nifty (proxy for global cues on Indian market open)."""
    try:
        df = await get_ohlcv_yfinance("^NSEI", days=2)
        if df.empty:
            return 0.0
        return float(df["close"].dropna().iloc[-1])
    except Exception as e:
        logger.warning(f"[FEED] get_sgx_nifty fallback error: {e}")
        return 0.0
