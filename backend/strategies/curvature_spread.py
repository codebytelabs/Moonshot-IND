"""Curvature Credit Spread Overnight — signal engine.

Reconstructed from Dhan/Stratzy conceptual description.
Models the option chain as a fluid: 'viscosity' = ATM vs wing liquidity
density; 'curvature' = second derivative of IV smile (quadratic fit |a|).

Alpha = tanh(z-score(curvature) × log(viscosity + 1))
  > 0.7 → BULLISH  → sell ATM PE, buy ITM PE −400 pts
  < 0.3 → BEARISH  → sell ATM CE, buy OTM CE +400 pts
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import time, datetime
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger("moonshotx.strategies.curvature")

ENTRY_START = time(10, 15)
ENTRY_END   = time(14, 15)
CURV_LOOKBACK  = 60       # bars (300 min) for rolling stats
BULLISH_THRESH = 0.70
BEARISH_THRESH = 0.30
SPREAD_WIDTH   = 400
MAX_RUPEE_LOSS = 3_000
ATM_BAND_PCT   = 0.02     # ±2% of spot = near-ATM zone


@dataclass
class CurvatureSignal:
    timestamp: datetime
    direction: str          # 'bullish' or 'bearish'
    alpha: float
    curvature_score: float
    viscosity_score: float
    spot: float
    short_strike: int
    long_strike: int
    opt_type: str


def compute_iv_curvature(chain: pd.DataFrame, spot: float) -> float:
    """
    Fit quadratic IV = a*x² + b*x + c  where x = moneyness = strike/spot - 1.
    Returns normalised curvature score = |a| / mean(iv).

    chain must have columns: strike, iv  (iv in decimal, e.g. 0.18).
    """
    if chain.empty or len(chain) < 4:
        return 0.0
    try:
        x = (chain["strike"].astype(float) / spot - 1.0).values
        y = chain["iv"].astype(float).values
        mask = np.isfinite(x) & np.isfinite(y) & (y > 0)
        if mask.sum() < 4:
            return 0.0
        coeffs = np.polyfit(x[mask], y[mask], 2)
        a = coeffs[0]                           # curvature coefficient
        mean_iv = y[mask].mean()
        return abs(a) / max(mean_iv, 1e-6)
    except Exception:
        return 0.0


def compute_liquidity_viscosity(chain: pd.DataFrame, spot: float) -> float:
    """
    Viscosity = ATM density / wing density.
    ATM zone = strikes within ATM_BAND_PCT of spot.
    Wing zone = everything outside.

    chain must have columns: strike, volume, oi.
    """
    if chain.empty:
        return 1.0
    chain = chain.copy()
    chain["liquidity"] = chain.get("volume", pd.Series(0, index=chain.index)).fillna(0) + \
                         chain.get("oi", pd.Series(0, index=chain.index)).fillna(0)
    band = spot * ATM_BAND_PCT
    atm_mask  = (chain["strike"] >= spot - band) & (chain["strike"] <= spot + band)
    wing_mask = ~atm_mask
    atm_liq   = chain.loc[atm_mask,  "liquidity"].sum()
    wing_liq  = chain.loc[wing_mask, "liquidity"].sum()
    n_atm  = max(1, atm_mask.sum())
    n_wing = max(1, wing_mask.sum())
    atm_density  = atm_liq  / n_atm
    wing_density = wing_liq / n_wing
    return atm_density / max(wing_density, 1.0)


def curvature_alpha(
    curv: float,
    visc: float,
    rolling_mean: float,
    rolling_std: float,
) -> float:
    """Combine curvature z-score with log-viscosity weighting → [0, 1]."""
    if rolling_std < 1e-9:
        z = 0.0
    else:
        z = (curv - rolling_mean) / rolling_std
    raw = z * np.log1p(max(visc, 1.0))
    return float((np.tanh(raw) + 1.0) / 2.0)   # map [-1,1] → [0,1]


def generate_curvature_signals(
    chain_history: dict,           # {pd.Timestamp: pd.DataFrame (strike,iv,volume,oi)}
    spot_history: pd.Series,       # pd.Series indexed by pd.Timestamp, values = NIFTY spot
    lookback: int = CURV_LOOKBACK,
) -> list[CurvatureSignal]:
    """
    Generate Curvature signals by iterating over timestamps.

    chain_history: dict mapping pd.Timestamp → option chain DataFrame
                   (columns: strike, iv, volume, oi)
    spot_history:  pd.Series (datetime index, NIFTY spot price)
    """
    if not chain_history or spot_history.empty:
        return []

    timestamps = sorted(set(chain_history) & set(spot_history.index))
    curvatures: list[float] = []
    viscosities: list[float] = []
    ts_list: list = []

    for ts in timestamps:
        chain = chain_history[ts]
        spot  = float(spot_history.loc[ts])
        c = compute_iv_curvature(chain, spot)
        v = compute_liquidity_viscosity(chain, spot)
        curvatures.append(c)
        viscosities.append(v)
        ts_list.append(ts)

    curv_arr = np.array(curvatures, dtype=float)
    signals: list[CurvatureSignal] = []

    for i, ts in enumerate(ts_list):
        t = ts.time() if hasattr(ts, "time") else datetime.fromtimestamp(ts).time()
        if not (ENTRY_START <= t <= ENTRY_END):
            continue
        if i < max(5, lookback // 2):
            continue
        window = curv_arr[max(0, i - lookback): i]
        r_mean = float(window.mean())
        r_std  = float(window.std())
        alpha  = curvature_alpha(curvatures[i], viscosities[i], r_mean, r_std)

        spot   = float(spot_history.loc[ts])
        atm    = int(round(spot / 50) * 50)

        if alpha > BULLISH_THRESH:
            signals.append(CurvatureSignal(
                timestamp=ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts,
                direction="bullish",
                alpha=round(alpha, 4),
                curvature_score=round(curvatures[i], 4),
                viscosity_score=round(viscosities[i], 4),
                spot=spot,
                short_strike=atm,
                long_strike=atm - SPREAD_WIDTH,
                opt_type="PE",
            ))
        elif alpha < BEARISH_THRESH:
            signals.append(CurvatureSignal(
                timestamp=ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts,
                direction="bearish",
                alpha=round(alpha, 4),
                curvature_score=round(curvatures[i], 4),
                viscosity_score=round(viscosities[i], 4),
                spot=spot,
                short_strike=atm,
                long_strike=atm + SPREAD_WIDTH,
                opt_type="CE",
            ))

    logger.debug("[CURV] %d signals generated from %d chain snapshots", len(signals), len(ts_list))
    return signals


def generate_curvature_signals_from_chain_df(
    chain_df: pd.DataFrame,
    spot_series: pd.Series,
    lookback: int = CURV_LOOKBACK,
) -> list[CurvatureSignal]:
    """
    Convenience wrapper when you have a multi-index DataFrame:
      chain_df.index = (timestamp, strike), columns = [iv, volume, oi, type]

    Groups by timestamp and calls generate_curvature_signals.
    """
    if chain_df.empty:
        return []
    chain_history = {
        ts: grp.reset_index(level=1) if grp.index.nlevels > 1 else grp
        for ts, grp in chain_df.groupby(level=0)
    }
    return generate_curvature_signals(chain_history, spot_series, lookback)
