"""
India market regime detection — classifies NSE environment for risk calibration.

Inputs (replacing US VIX/SPY):
  - India VIX  (^INDIAVIX)  → fear/volatility gauge
  - NIFTY50 1-day return    → trend / momentum gauge
  - NSE advance/decline ratio → breadth gauge

Regime labels (unchanged from MoonshotX, compatible with risk.py and position_manager.py):
  bull | neutral | fear | choppy | bear_mode | extreme_fear
"""
import asyncio
import logging
from datetime import datetime, timezone
from typing import Dict

import numpy as np
import yfinance as yf

logger = logging.getLogger("moonshotx.regime")

REGIME_CACHE: Dict = {"regime": "neutral", "updated_at": None, "data": {}}
CACHE_TTL_SECONDS = 300  # 5 minutes

# ── India VIX thresholds (calibrated for NSE; US VIX thresholds don't apply) ──
# India VIX normal range: 10–18. Elevated: 18–25. Fear: 25–30. Crisis: 30+
INDIA_VIX_NORMAL     = 18.0
INDIA_VIX_ELEVATED   = 22.0
INDIA_VIX_FEAR       = 26.0
INDIA_VIX_PANIC      = 32.0


def _fetch_regime_data() -> dict:
    """Synchronous fetch of India regime inputs."""
    # ── India VIX ────────────────────────────────────────────────────────
    try:
        vix_ticker = yf.Ticker("^INDIAVIX")
        vix_hist = vix_ticker.history(period="5d", interval="1d")
        india_vix = float(vix_hist["Close"].iloc[-1]) if not vix_hist.empty else 16.0
    except Exception:
        india_vix = 16.0

    # ── NIFTY50 momentum ─────────────────────────────────────────────────
    try:
        nifty = yf.Ticker("^NSEI")
        nifty_hist = nifty.history(period="1mo", interval="1d")
        if len(nifty_hist) >= 20:
            nifty_20d = float(
                (nifty_hist["Close"].iloc[-1] - nifty_hist["Close"].iloc[-20])
                / nifty_hist["Close"].iloc[-20]
            )
        elif len(nifty_hist) >= 2:
            nifty_20d = float(
                (nifty_hist["Close"].iloc[-1] - nifty_hist["Close"].iloc[0])
                / nifty_hist["Close"].iloc[0]
            )
        else:
            nifty_20d = 0.0
        nifty_1d = float(
            (nifty_hist["Close"].iloc[-1] - nifty_hist["Close"].iloc[-2])
            / nifty_hist["Close"].iloc[-2]
        ) if len(nifty_hist) >= 2 else 0.0
        nifty_price = float(nifty_hist["Close"].iloc[-1]) if not nifty_hist.empty else 0.0
    except Exception:
        nifty_20d = 0.0
        nifty_1d  = 0.0
        nifty_price = 0.0

    # ── NSE breadth: NIFTY500 A/D estimate via Nifty vs 200 DMA ──────────
    try:
        nifty_long = yf.Ticker("^NSEI").history(period="1y", interval="1d")
        sma_200 = (
            float(nifty_long["Close"].rolling(200).mean().iloc[-1])
            if len(nifty_long) >= 200
            else nifty_price * 0.95
        )
        breadth = 0.65 if nifty_price > sma_200 else 0.35
    except Exception:
        breadth = 0.50

    # ── Composite fear/greed proxy ────────────────────────────────────────
    # Inverted India VIX (higher VIX → lower score) + NIFTY momentum
    vix_score = max(0.0, min(100.0, 100.0 - (india_vix - 10.0) * 3.5))
    mom_score = max(0.0, min(100.0, 50.0 + nifty_20d * 500.0))
    fear_greed = round(0.6 * vix_score + 0.4 * mom_score, 1)

    regime = _classify_india(india_vix, fear_greed, breadth, nifty_20d)

    return {
        "regime": regime,
        "india_vix": round(india_vix, 2),
        "fear_greed": round(fear_greed, 1),
        "breadth": round(breadth, 3),
        "nifty_20d_return": round(nifty_20d * 100, 2),
        "nifty_1d_return": round(nifty_1d * 100, 2),
        "nifty_price": round(nifty_price, 2),
    }


def _classify_india(
    india_vix: float, fg: float, breadth: float, nifty_20d: float
) -> str:
    """Classify NSE regime from India-specific inputs."""
    if india_vix >= INDIA_VIX_PANIC and breadth < 0.25:
        return "extreme_fear"
    if india_vix >= INDIA_VIX_FEAR and breadth < 0.35:
        return "bear_mode"
    if india_vix >= INDIA_VIX_ELEVATED or breadth < 0.40 or fg < 25:
        return "fear"
    if fg < 40 or nifty_20d < -0.02 or (india_vix >= INDIA_VIX_NORMAL and breadth < 0.50):
        return "choppy"
    if fg > 68 and breadth > 0.60 and nifty_20d > 0.03:
        return "bull"
    return "neutral"


class RegimeManager:
    async def get_current(self) -> dict:
        now = datetime.now(timezone.utc)
        if (
            REGIME_CACHE["updated_at"] is None
            or (now - REGIME_CACHE["updated_at"]).total_seconds() > CACHE_TTL_SECONDS
        ):
            try:
                data = await asyncio.to_thread(_fetch_regime_data)
                REGIME_CACHE.update(data)
                REGIME_CACHE["regime"] = data["regime"]
                REGIME_CACHE["updated_at"] = now
                logger.info(
                    f"[REGIME] {data['regime'].upper()} — "
                    f"IndiaVIX={data['india_vix']} "
                    f"NIFTY20d={data['nifty_20d_return']}% "
                    f"breadth={data['breadth']}"
                )
            except Exception as e:
                logger.error(f"Regime fetch error: {e}")
        return dict(REGIME_CACHE)

    def regime_allows_longs(self, regime: str) -> bool:
        return regime in ("bull", "neutral", "fear")

    def max_positions(self, regime: str) -> int:
        return {"bull": 5, "neutral": 4, "fear": 3, "choppy": 0, "bear_mode": 0, "extreme_fear": 0}.get(regime, 0)
