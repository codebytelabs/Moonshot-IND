"""India universe scanner — self-updating NSE universe with momentum & volume discovery.

Data source priority:
  1. Kite historical API (intraday OHLCV, filled via KiteBroker.get_bars)
  2. yfinance (NSE suffix .NS) for daily OHLCV fallback

Universe:
  SEED_UNIVERSE  → India curated large/mid-cap seed (always scanned)
  BROAD_WATCHLIST → NIFTY200 + select midcap (watchlist scan)
  NIFTY500       → live NSE constituent list (discovery)

All quality filters adapted for NSE:
  - Min price: ₹20 (not $5 US)
  - Min avg daily volume: ₹5Cr (not $10M US)
  - Pump-and-dump cap: ±25% 5d move (NSE circuit limits help here)
"""
import asyncio
import logging
from typing import List, Dict, Optional
import pandas as pd
import numpy as np
import yfinance as yf
from datetime import datetime, timezone

logger = logging.getLogger("moonshotx.scanner")

from data.india_universe import INDIA_SEED_UNIVERSE, INDIA_BROAD_WATCHLIST

SEED_UNIVERSE = INDIA_SEED_UNIVERSE
BROAD_WATCHLIST = INDIA_BROAD_WATCHLIST

# ── NSE-calibrated quality filters ────────────────────────────────────────────
_MIN_PRICE = 20.0                   # ₹20 minimum (avoids illiquid penny stocks)
_MIN_AVG_RUPEE_VOL = 5_00_00_000    # ₹5 crore avg daily turnover minimum
_MAX_MOM_5D = 0.25                  # reject >25% 5d moves (NSE circuit-adj)
_SKIP_SUFFIXES = ("-RE", "-BE",)   # NSE rights/book entitlements
# ── Known-bad or illiquid NSE symbols ────────────────────────────────────────
_BLOCKED_TICKERS = {"NIFTYBEES", "BANKBEES", "JUNIORBEES"}  # ETFs, not stocks


def _is_tradeable_ticker(sym: str) -> bool:
    """Filter out ETFs, illiquid symbols, and malformed NSE tickers."""
    if not sym or len(sym) > 20:
        return False
    if sym in _BLOCKED_TICKERS:
        return False
    for sfx in _SKIP_SUFFIXES:
        if sym.endswith(sfx):
            return False
    return True

# ── Cache & refresh config ────────────────────────────────────────────────────
_SCORE_CACHE: Dict = {}
_SCORE_CACHE_TTL = 600           # rank cache: 10 min (per-cycle scan results)
_DISCOVERY_CACHE: Dict = {}
_DISCOVERY_TTL = 900             # universe discovery refresh: 15 min


def _fetch_stock_data_nse(ticker: str) -> dict:
    """Fetch and score one NSE ticker via yfinance (.NS suffix)."""
    import math

    def _safe(v, default=0.0):
        return default if (isinstance(v, float) and (math.isnan(v) or math.isinf(v))) else v

    try:
        # Use .NS suffix for NSE on yfinance
        yf_sym = ticker if ticker.endswith(".NS") else f"{ticker}.NS"
        t = yf.Ticker(yf_sym)
        hist = t.history(period="1mo", interval="1d")
        if hist is None or len(hist) < 5:
            return {"ticker": ticker, "error": "insufficient data"}

        close = hist["Close"]
        volume = hist["Volume"]
        high = hist["High"]
        low  = hist["Low"]

        price = _safe(float(close.iloc[-1]), 0.0)
        mom_5d  = _safe(float((close.iloc[-1] - close.iloc[-5])  / close.iloc[-5])  if len(close) >= 5  else 0.0)
        mom_20d = _safe(float((close.iloc[-1] - close.iloc[0])   / close.iloc[0])   if len(close) >= 20 else 0.0)

        avg_vol   = float(volume.iloc[-20:].mean()) if len(volume) >= 20 else float(volume.mean())
        vol_ratio = _safe(float(volume.iloc[-1] / avg_vol) if avg_vol > 0 else 1.0, 1.0)

        delta     = close.diff()
        gain      = delta.clip(lower=0)
        loss      = -delta.clip(upper=0)
        rs        = gain.rolling(14).mean() / (loss.rolling(14).mean() + 1e-9)
        rsi       = _safe(float(100 - (100 / (1 + rs.iloc[-1]))) if len(close) >= 14 else 50.0, 50.0)

        ema9  = _safe(float(close.ewm(span=9).mean().iloc[-1]))
        ema21 = _safe(float(close.ewm(span=21).mean().iloc[-1]))
        ema_bullish = ema9 > ema21

        tr  = pd.concat([high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
        atr = _safe(float(tr.rolling(14).mean().iloc[-1]) if len(tr) >= 14 else float(tr.mean()))
        atr_pct = _safe(atr / price if price > 0 else 0.0)

        gap_pct = _safe(float((close.iloc[-1] - close.iloc[-2]) / close.iloc[-2]) if len(close) >= 2 else 0.0)

        # ── NSE quality gates ────────────────────────────────────────────
        if price <= 0 or price < _MIN_PRICE:
            return {"ticker": ticker, "error": f"price ₹{price:.2f} below ₹{_MIN_PRICE} min"}

        avg_rupee_vol = avg_vol * price
        if avg_rupee_vol < _MIN_AVG_RUPEE_VOL:
            return {"ticker": ticker, "error": f"avg rupee vol ₹{avg_rupee_vol/1e7:.2f}Cr below ₹5Cr min"}

        if abs(mom_5d) > _MAX_MOM_5D:
            return {"ticker": ticker, "error": f"5d momentum {mom_5d*100:.0f}% exceeds ±{_MAX_MOM_5D*100:.0f}% cap"}

        # ── Scoring (identical structure to original — algo unchanged) ────
        rsi_score  = 1.0 if 40 <= rsi <= 65 else (0.6 if 35 <= rsi <= 70 else 0.2)
        vol_score  = min(1.0, vol_ratio / 2.0)
        mom_score  = 1.0 if mom_5d > 0.02 else (0.6 if mom_5d > 0.0 else 0.2)
        gap_contrib = min(2.0, abs(gap_pct * 10))
        trend_score = 1.0 if ema_bullish else 0.3
        atr_ok     = atr_pct >= 0.003   # ₹20 stock: 0.3% ATR is sufficient

        bayesian_score = round(0.3 * rsi_score + 0.25 * vol_score + 0.25 * mom_score + 0.20 * trend_score, 3)
        composite = round(0.25 * mom_score + 0.25 * vol_score + 0.20 * gap_contrib + 0.30 * rsi_score, 3)

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


_fetch_stock_data = _fetch_stock_data_nse


class UniverseScanner:
    """Self-updating India universe scanner.

    Every 15 min it discovers new hot NSE stocks via:
      1. KiteBroker.get_most_active() → NSE actives by volume
      2. KiteBroker.get_top_movers()  → NSE top % gainers
      3. Broad NIFTY200 watchlist momentum scan — volume spike + uptrend filter
    Then merges with INDIA_SEED_UNIVERSE for the active scanning pool.
    """

    def __init__(self, kite_client=None):
        self._kite = kite_client
        self._dynamic_universe: List[str] = list(SEED_UNIVERSE)
        self._last_discovery: Optional[datetime] = None
        self._discovery_stats: Dict = {}

    async def _discover_universe(self) -> List[str]:
        """Build the active NSE universe from multiple discovery sources."""
        now = datetime.now(timezone.utc)

        if self._last_discovery and (now - self._last_discovery).total_seconds() < _DISCOVERY_TTL:
            return self._dynamic_universe

        logger.info("[SCANNER] NSE universe discovery starting...")
        discovered: set = set(SEED_UNIVERSE)

        # ── Source 1: Kite broker screener (most-active + movers) ────────
        kite_actives = []
        kite_movers  = []
        if self._kite:
            try:
                raw_actives, raw_movers = await asyncio.gather(
                    self._kite.get_most_active(top=50),
                    self._kite.get_top_movers(top=50),
                )
                kite_actives = [s for s in raw_actives if _is_tradeable_ticker(s)]
                kite_movers  = [s for s in raw_movers  if _is_tradeable_ticker(s)]
                discovered.update(kite_actives[:30])
                discovered.update(kite_movers[:20])
            except Exception as e:
                logger.warning(f"[SCANNER] Kite screener discovery failed: {e}")

        # ── Source 2: Broad NIFTY200 watchlist volume/momentum scan ──────
        watchlist_hot = await self._quick_scan_watchlist()
        discovered.update(watchlist_hot)

        self._dynamic_universe = sorted(discovered)
        self._last_discovery = now
        self._discovery_stats = {
            "total": len(self._dynamic_universe),
            "from_seed": len(SEED_UNIVERSE),
            "from_kite_active": len(kite_actives[:30]),
            "from_kite_movers": len(kite_movers[:20]),
            "from_watchlist_scan": len(watchlist_hot),
            "updated_at": now.isoformat(),
        }
        logger.info(
            f"[SCANNER] Discovery complete: {len(self._dynamic_universe)} stocks "
            f"(seed={len(SEED_UNIVERSE)}, kite_active={len(kite_actives[:30])}, "
            f"movers={len(kite_movers[:20])}, watchlist_hot={len(watchlist_hot)})"
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
