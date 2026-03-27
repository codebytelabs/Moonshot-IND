"""Universe scanner — self-updating universe with momentum & volume discovery."""
import asyncio
import logging
from typing import List, Dict, Optional
import pandas as pd
import numpy as np
import yfinance as yf
from datetime import datetime, timezone

logger = logging.getLogger("moonshotx.scanner")

# ── Seed universe: always-included liquid mega-caps (never removed) ───────────
SEED_UNIVERSE = [
    "AAPL", "MSFT", "NVDA", "GOOGL", "META", "AMZN", "TSLA", "AVGO",
    "AMD", "NFLX", "CRM", "COIN", "PLTR",
]

# ── Broad watchlist: scanned for momentum/volume spikes during discovery ──────
BROAD_WATCHLIST = [
    "AAPL", "MSFT", "NVDA", "GOOGL", "META", "AMZN", "TSLA", "AVGO",
    "AMD", "QCOM", "INTC", "MU", "AMAT", "LRCX", "MRVL", "ON", "KLAC", "ASML",
    "CRM", "NOW", "ORCL", "ADBE", "WDAY", "TEAM", "HUBS", "VEEV",
    "NFLX", "SPOT", "UBER", "ABNB", "DASH", "RBLX",
    "COIN", "PYPL", "SOFI", "HOOD", "MSTR",
    "PANW", "CRWD", "DDOG", "NET", "SNOW", "ZS", "FTNT", "S",
    "PLTR", "SHOP", "SMCI", "ARM", "AI", "PATH", "IONQ",
    "MRNA", "REGN", "VRTX", "ISRG",
    "XOM", "CVX", "OXY", "SLB",
    "GS", "JPM", "MS", "SCHW",
    "BA", "CAT", "DE", "LMT", "RTX",
    "ENPH", "FSLR", "CEG", "VST",
    "LLY", "UNH", "ABBV", "PFE",
    "V", "MA", "AXP",
    "COST", "WMT", "TGT", "HD",
    "DIS", "CMCSA", "T", "TMUS",
]

# ── Quality filters for discovered tickers ────────────────────────────────────
_MIN_PRICE = 5.0                 # skip penny stocks
_MIN_AVG_DOLLAR_VOL = 10_000_000 # $10M avg daily dollar volume minimum
_MAX_MOM_5D = 1.00               # reject >100% 5d moves (pump-and-dump filter)
_SKIP_SUFFIXES = ("W", "WS", "U", "R", "Z")  # warrants, units, rights
# ── Known-bad tickers (delisted, no data, yfinance errors) ─────────────────
_BLOCKED_TICKERS = {
    "SQ",     # delisted / renamed to XYZ — yfinance returns no data
    "NVD",    # invalid symbol — 404 on yfinance
    "FB",     # renamed to META
}

_LEVERAGED_ETFS = {
    "SOXL", "SOXS", "TQQQ", "SQQQ", "SPXU", "SPXS", "UPRO", "TZA", "TNA",
    "LABU", "LABD", "FNGU", "FNGD", "TSLL", "TSLS", "SPDN", "UVXY", "SVXY",
    "NUGT", "DUST", "JNUG", "JDST", "TECL", "TECS", "FAS", "FAZ",
    "ERX", "ERY", "NAIL", "DRV", "CURE", "DPST", "BITO",
    "UVIX", "VXX", "VIXY", "SVOL",  # volatility products
}

def _is_tradeable_ticker(sym: str) -> bool:
    """Filter out warrants, units, rights, leveraged ETFs, and malformed tickers."""
    if not sym or len(sym) > 5:
        return False
    if sym in _BLOCKED_TICKERS:
        return False
    if sym in _LEVERAGED_ETFS:
        return False
    for sfx in _SKIP_SUFFIXES:
        if sym.endswith(sfx) and len(sym) > len(sfx):
            return False
    if any(c.isdigit() for c in sym):
        return False
    return True

# ── Cache & refresh config ────────────────────────────────────────────────────
_SCORE_CACHE: Dict = {}
_SCORE_CACHE_TTL = 600           # rank cache: 10 min (per-cycle scan results)
_DISCOVERY_CACHE: Dict = {}
_DISCOVERY_TTL = 900             # universe discovery refresh: 15 min


def _fetch_stock_data(ticker: str) -> dict:
    try:
        t = yf.Ticker(ticker)
        hist = t.history(period="1mo", interval="1d")
        if hist is None or len(hist) < 5:
            return {"ticker": ticker, "error": "insufficient data"}

        close = hist["Close"]
        volume = hist["Volume"]

        price = float(close.iloc[-1])

        mom_5d = float((close.iloc[-1] - close.iloc[-5]) / close.iloc[-5]) if len(close) >= 5 else 0.0
        mom_20d = float((close.iloc[-1] - close.iloc[0]) / close.iloc[0]) if len(close) >= 20 else 0.0

        avg_vol = float(volume.iloc[-20:].mean()) if len(volume) >= 20 else float(volume.mean())
        vol_ratio = float(volume.iloc[-1] / avg_vol) if avg_vol > 0 else 1.0

        delta = close.diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)
        avg_gain = gain.rolling(14).mean()
        avg_loss = loss.rolling(14).mean()
        rs = avg_gain / (avg_loss + 1e-9)
        rsi = float(100 - (100 / (1 + rs.iloc[-1]))) if len(close) >= 14 else 50.0

        ema9 = float(close.ewm(span=9).mean().iloc[-1])
        ema21 = float(close.ewm(span=21).mean().iloc[-1])
        ema_bullish = ema9 > ema21

        high = hist["High"]
        low = hist["Low"]
        tr = pd.concat([high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
        atr = float(tr.rolling(14).mean().iloc[-1]) if len(tr) >= 14 else float(tr.mean())
        atr_pct = atr / price if price > 0 else 0.0

        gap_pct = float((close.iloc[-1] - close.iloc[-2]) / close.iloc[-2]) if len(close) >= 2 else 0.0

        # Sanitize NaN/Inf
        import math
        def _safe(v, default=0.0):
            return default if (isinstance(v, float) and (math.isnan(v) or math.isinf(v))) else v

        price = _safe(price, 0.0)
        mom_5d = _safe(mom_5d, 0.0)
        mom_20d = _safe(mom_20d, 0.0)
        vol_ratio = _safe(vol_ratio, 1.0)
        rsi = _safe(rsi, 50.0)
        atr = _safe(atr, 0.0)
        atr_pct = _safe(atr_pct, 0.0)
        gap_pct = _safe(gap_pct, 0.0)

        if price <= 0 or price < _MIN_PRICE:
            return {"ticker": ticker, "error": f"price ${price:.2f} below minimum ${_MIN_PRICE}"}

        avg_dollar_vol = avg_vol * price
        if avg_dollar_vol < _MIN_AVG_DOLLAR_VOL:
            return {"ticker": ticker, "error": f"avg dollar vol ${avg_dollar_vol/1e6:.1f}M below ${_MIN_AVG_DOLLAR_VOL/1e6:.0f}M min"}

        if abs(mom_5d) > _MAX_MOM_5D:
            return {"ticker": ticker, "error": f"5d momentum {mom_5d*100:.0f}% exceeds ±{_MAX_MOM_5D*100:.0f}% cap"}

        rsi_score = 1.0 if 40 <= rsi <= 65 else (0.6 if 35 <= rsi <= 70 else 0.2)
        vol_score = min(1.0, vol_ratio / 2.0)
        mom_score = 1.0 if mom_5d > 0.02 else (0.6 if mom_5d > 0.0 else 0.2)
        # Cap gap contribution to prevent pump-and-dump outliers
        gap_contrib = min(2.0, abs(gap_pct * 10))
        trend_score = 1.0 if ema_bullish else 0.3
        atr_ok = atr_pct >= 0.005

        bayesian_score = round(0.3 * rsi_score + 0.25 * vol_score + 0.25 * mom_score + 0.20 * trend_score, 3)
        composite = round(
            0.25 * mom_score + 0.25 * vol_score + 0.20 * gap_contrib + 0.30 * rsi_score, 3
        )

        return {
            "ticker": ticker,
            "price": round(price, 2),
            "momentum_5d": round(mom_5d * 100, 2),
            "momentum_20d": round(mom_20d * 100, 2),
            "volume_ratio": round(vol_ratio, 2),
            "rsi": round(rsi, 1),
            "ema_bullish": ema_bullish,
            "atr": round(atr, 2),
            "atr_pct": round(atr_pct * 100, 2),
            "atr_ok": atr_ok,
            "bayesian_score": bayesian_score,
            "composite_score": composite,
            "source": "scan",
            "error": None,
        }
    except Exception as e:
        logger.debug(f"Scanner error {ticker}: {e}")
        return {"ticker": ticker, "error": str(e), "bayesian_score": 0.0, "composite_score": 0.0}


class UniverseScanner:
    """Self-updating universe scanner.

    Every 15 min it discovers new hot stocks via:
      1. Alpaca screener — most-active by volume + top gainers
      2. Broad watchlist momentum scan — volume spike + uptrend filter
    Then merges with the seed universe for the active scanning pool.
    """

    def __init__(self, alpaca_client=None):
        self._alpaca = alpaca_client
        self._dynamic_universe: List[str] = list(SEED_UNIVERSE)
        self._last_discovery: Optional[datetime] = None
        self._discovery_stats: Dict = {}

    async def _discover_universe(self) -> List[str]:
        """Build the active universe from multiple discovery sources."""
        now = datetime.now(timezone.utc)

        # Check if discovery cache is still fresh
        if self._last_discovery and (now - self._last_discovery).total_seconds() < _DISCOVERY_TTL:
            return self._dynamic_universe

        logger.info("Universe discovery starting...")
        discovered: set = set(SEED_UNIVERSE)

        # ── Source 1: Alpaca screener (real-time most-active + top movers) ─
        alpaca_actives = []
        alpaca_movers = []
        if self._alpaca:
            try:
                raw_actives, raw_movers = await asyncio.gather(
                    self._alpaca.get_most_active(top=50),
                    self._alpaca.get_top_movers(top=50),
                )
                alpaca_actives = [s for s in raw_actives if _is_tradeable_ticker(s)]
                alpaca_movers = [s for s in raw_movers if _is_tradeable_ticker(s)]
                discovered.update(alpaca_actives[:30])
                discovered.update(alpaca_movers[:20])
            except Exception as e:
                logger.warning(f"Alpaca screener discovery failed: {e}")

        # ── Source 2: Broad watchlist quick-scan for volume/momentum ───────
        # Fetch data for all broad watchlist stocks, keep those with
        # volume_ratio > 1.3 OR positive 5d momentum
        watchlist_hot = await self._quick_scan_watchlist()
        discovered.update(watchlist_hot)

        self._dynamic_universe = sorted(discovered)
        self._last_discovery = now
        self._discovery_stats = {
            "total": len(self._dynamic_universe),
            "from_seed": len(SEED_UNIVERSE),
            "from_alpaca_active": len(alpaca_actives[:30]),
            "from_alpaca_movers": len(alpaca_movers[:20]),
            "from_watchlist_scan": len(watchlist_hot),
            "updated_at": now.isoformat(),
        }
        logger.info(
            f"Universe discovery complete: {len(self._dynamic_universe)} stocks "
            f"(seed={len(SEED_UNIVERSE)}, alpaca_active={len(alpaca_actives[:30])}, "
            f"movers={len(alpaca_movers[:20])}, watchlist_hot={len(watchlist_hot)})"
        )
        return self._dynamic_universe

    async def _quick_scan_watchlist(self) -> List[str]:
        """Quick parallel scan of broad watchlist — return tickers with volume spike or uptrend."""
        try:
            results = await asyncio.gather(
                *[asyncio.to_thread(_fetch_stock_data, t) for t in BROAD_WATCHLIST],
                return_exceptions=True,
            )
            hot = []
            for r in results:
                if isinstance(r, Exception) or not isinstance(r, dict) or r.get("error"):
                    continue
                vol = r.get("volume_ratio", 0)
                mom = r.get("momentum_5d", 0)
                ema_bull = r.get("ema_bullish", False)
                # Volume spike (>1.3x average) OR strong momentum with trend
                if vol > 1.3 or (mom > 1.0 and ema_bull):
                    hot.append(r["ticker"])
            return hot
        except Exception as e:
            logger.warning(f"Watchlist quick-scan failed: {e}")
            return []

    async def get_ranked(self, n: int = 50) -> List[dict]:
        """Discover universe, fetch data, rank by composite score."""
        now = datetime.now(timezone.utc)
        cache_key = "full_scan"
        cached = _SCORE_CACHE.get(cache_key)
        if cached and (now - cached["updated_at"]).total_seconds() < _SCORE_CACHE_TTL:
            return cached["data"][:n]

        universe = await self._discover_universe()
        logger.info(f"Scanning {len(universe)} stocks...")

        results = await asyncio.gather(
            *[asyncio.to_thread(_fetch_stock_data, t) for t in universe],
            return_exceptions=True,
        )
        valid = [r for r in results if isinstance(r, dict) and r.get("error") is None]
        ranked = sorted(valid, key=lambda x: x["composite_score"], reverse=True)
        _SCORE_CACHE[cache_key] = {"data": ranked, "updated_at": now}
        return ranked[:n]

    async def get_top_candidates(self, n: int = 10, min_bayesian: float = 0.45) -> List[str]:
        """Return top n tickers that pass the Bayesian pre-gate."""
        ranked = await self.get_ranked(max(n * 2, 50))
        viable = [r["ticker"] for r in ranked if r["bayesian_score"] >= min_bayesian]
        return viable[:n]

    async def get_ticker_data(self, ticker: str) -> dict:
        return await asyncio.to_thread(_fetch_stock_data, ticker)

    def get_discovery_stats(self) -> dict:
        return self._discovery_stats
