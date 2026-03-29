"""V-Score Credit Spread Overnight — signal engine.

Reconstructed from DhanHQ/Stratzy public description.
Variant of Zen using VOLATILITY VISCOSITY SCORING to identify
optimal low-volatility resistance windows for credit spread entry.

Two signals:
  Alpha   (inverse vol score):  HIGH when volatility is LOW (suppressed)
                                 → bullish > 0.75 | bearish < 0.25
  Alpha9  (viscosity signal):   combines vol CHANGES with spot returns
                                 to detect favorable liquidity conditions
                                 → bullish > 0.70 | bearish < 0.30

Logic: when vol is suppressed AND liquidity is thick (viscous) near ATM,
the market is in a "low resistance" state — credit spreads are most
likely to expire worthless (ideal selling window).

Execution:
  Bullish → sell ATM PE, buy ITM PE −400
  Bearish → sell ATM CE, buy OTM CE +400
  Window:  10:15–14:15 IST
  SL:      ₹3,000 / (margin_required) × 100%
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import time, datetime
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger("moonshotx.strategies.vscore")

ENTRY_START     = time(10, 15)
ENTRY_END       = time(14, 15)
SPREAD_WIDTH    = 400
MAX_RUPEE_LOSS  = 3_000
ALPHA_BULL      = 0.75
ALPHA_BEAR      = 0.25
ALPHA9_BULL     = 0.70
ALPHA9_BEAR     = 0.30
WINDOW_ALPHA    = 160       # bars (800 min at 5-min)
WINDOW_ALPHA9   = 60        # bars (300 min at 5-min)


@dataclass
class VScoreSignal:
    timestamp: datetime
    direction: str          # 'bullish' | 'bearish'
    alpha: float            # inverse vol score
    alpha9: float           # viscosity signal
    spot: float
    short_strike: int
    long_strike: int
    opt_type: str           # 'PE' | 'CE'


# ── Core signal computations ──────────────────────────────────────────────

def _ts_rank(series: pd.Series) -> pd.Series:
    """Rolling time-series rank: fraction of past values ≤ current value."""
    def _rank(window):
        if len(window) == 0:
            return np.nan
        return float(np.sum(window[:-1] <= window[-1])) / max(len(window) - 1, 1)
    return series.rolling(len(series), min_periods=2).apply(_rank, raw=True)


def _ts_rank_rolling(series: pd.Series, window: int) -> pd.Series:
    """Efficient rolling TSRank over a fixed window."""
    def _rank(arr):
        return float(np.sum(arr[:-1] <= arr[-1])) / max(len(arr) - 1, 1)
    return series.rolling(window=window, min_periods=max(2, window // 4)).apply(_rank, raw=True)


def compute_vscore_alpha(
    df: pd.DataFrame,
    hv_col: str = "hv20",
    window: int = WINDOW_ALPHA,
) -> pd.Series:
    """
    Alpha = TSRank(inverse_vol_score, window).
    Inverse vol score: normalized measure of how LOW current vol is.
    High alpha → vol is suppressed → ideal credit selling window.
    """
    # Inverse vol: when HV is low relative to recent history, score is high
    inv_vol = 1.0 / df[hv_col].replace(0, np.nan).fillna(0.20)
    return _ts_rank_rolling(inv_vol, window)


def compute_vscore_alpha9(
    df: pd.DataFrame,
    hv_col: str = "hv20",
    window: int = WINDOW_ALPHA9,
    chain_df: Optional[pd.DataFrame] = None,
) -> pd.Series:
    """
    Alpha9 = TSRank(viscosity_raw, window).
    Viscosity raw = (-ΔHV) × |spot_return / HV|
    When vol DROPS as price moves → market is viscous (liquidity absorbs moves) → high score.
    Optionally uses ATM option volume ratio from chain_df if available.
    """
    fwd_ret = df["close"].pct_change().abs()
    delta_hv = df[hv_col].diff()                   # positive = vol rising, negative = falling
    neg_delta_hv = -delta_hv                        # high when vol FALLING
    hv_safe = df[hv_col].replace(0, np.nan).fillna(0.20)
    viscosity_raw = neg_delta_hv * (fwd_ret / hv_safe)
    viscosity_raw = viscosity_raw.fillna(0.0)

    # Optionally blend with ATM CE/PE volume ratio if available
    if chain_df is not None and not chain_df.empty:
        if "atm_ce_vol" in chain_df.columns and "atm_pe_vol" in chain_df.columns:
            vol_ratio = (chain_df["atm_pe_vol"] + 1e-9) / (chain_df["atm_ce_vol"] + 1e-9)
            vol_ratio = vol_ratio.reindex(df.index, method="nearest").fillna(1.0)
            viscosity_raw = viscosity_raw * np.log1p(vol_ratio.values)

    return _ts_rank_rolling(viscosity_raw, window)


# ── Signal generation ──────────────────────────────────────────────────────

def generate_vscore_signals(
    df_5m: pd.DataFrame,
    hv_col: str = "hv20",
    chain_df: Optional[pd.DataFrame] = None,
    alpha_bull: float = ALPHA_BULL,
    alpha_bear: float = ALPHA_BEAR,
    alpha9_bull: float = ALPHA9_BULL,
    alpha9_bear: float = ALPHA9_BEAR,
) -> list[VScoreSignal]:
    """
    Generate V-Score Credit Spread signals from 5-min NIFTY bars.

    df_5m: requires 'close' and hv_col columns.
           For backtest, hv_col = 'hv20' (pre-computed).
           For live, hv_col = 'hv20' (rolling 20-bar realized vol).
    """
    if df_5m.empty or hv_col not in df_5m.columns:
        logger.warning("[VSCORE] Missing required columns")
        return []

    df = df_5m.copy()

    alpha_s  = compute_vscore_alpha(df, hv_col=hv_col)
    alpha9_s = compute_vscore_alpha9(df, hv_col=hv_col, chain_df=chain_df)

    df["alpha"]  = alpha_s
    df["alpha9"] = alpha9_s
    df.dropna(subset=["alpha", "alpha9"], inplace=True)

    signals: list[VScoreSignal] = []
    for ts, row in df.iterrows():
        t = ts.time() if hasattr(ts, "time") else ts.to_pydatetime().time()
        if not (ENTRY_START <= t <= ENTRY_END):
            continue

        a  = float(row["alpha"])
        a9 = float(row["alpha9"])
        spot = float(row["close"])
        atm  = int(round(spot / 50) * 50)

        if a > alpha_bull and a9 > alpha9_bull:
            direction = "bullish"
            short_K, long_K, opt_type = atm, atm - SPREAD_WIDTH, "PE"
        elif a < alpha_bear and a9 < alpha9_bear:
            direction = "bearish"
            short_K, long_K, opt_type = atm, atm + SPREAD_WIDTH, "CE"
        else:
            continue

        signals.append(VScoreSignal(
            timestamp=ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts,
            direction=direction,
            alpha=round(a, 4),
            alpha9=round(a9, 4),
            spot=spot,
            short_strike=short_K,
            long_strike=long_K,
            opt_type=opt_type,
        ))

    logger.debug("[VSCORE] %d signals from %d bars", len(signals), len(df))
    return signals


def vscore_signal_to_spread(
    sig: VScoreSignal,
    lot_size: int = 25,
    margin_per_lot: float = 20_000.0,
    allocated_capital: float = 50_000.0,
    max_rupee_loss: float = MAX_RUPEE_LOSS,
    short_security_id: Optional[str] = None,
    long_security_id: Optional[str] = None,
    expiry: Optional[str] = None,
):
    """Convert a VScoreSignal to a SpreadOrder."""
    from strategies.zen_spread import CreditSpreadSignal, construct_spread_order
    fake_sig = CreditSpreadSignal(
        timestamp=sig.timestamp,
        direction=sig.direction,
        alpha1=sig.alpha,
        alpha2=sig.alpha9,
        spot=sig.spot,
        short_strike=sig.short_strike,
        long_strike=sig.long_strike,
        opt_type=sig.opt_type,
    )
    return construct_spread_order(
        fake_sig,
        lot_size=lot_size,
        margin_per_lot=margin_per_lot,
        allocated_capital=allocated_capital,
        max_rupee_loss=max_rupee_loss,
        strategy="vscore",
        short_security_id=short_security_id,
        long_security_id=long_security_id,
        expiry=expiry,
    )
