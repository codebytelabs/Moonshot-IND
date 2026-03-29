"""Zen Credit Spread Overnight — signal engine.

Reconstructed from Dhan/Stratzy public description.
Operates on 5-minute NIFTY bars with ATM CE/PE volume + IV data.

Alpha 1 (alpha):  time-series rank of forward 5-min return, 800-min lookback.
Alpha 2 (alpha2): forward return × (ATM PE vol / ATM CE vol) ÷ ATM vol,
                  then 300-min time-series rank.

Signal rules (10:15–14:15 IST window):
  Both > 0.8  → BULLISH  → sell ATM PE, buy ITM PE −400 pts
  Both < 0.2  → BEARISH  → sell ATM CE, buy OTM CE +400 pts
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import time, datetime
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger("moonshotx.strategies.zen")

ENTRY_START = time(10, 15)
ENTRY_END   = time(14, 15)
ALPHA1_LOOKBACK = 800    # minutes (160 five-minute bars)
ALPHA2_LOOKBACK = 300    # minutes (60 five-minute bars)
BULLISH_THRESH  = 0.80
BEARISH_THRESH  = 0.20
SPREAD_WIDTH    = 400    # NIFTY points
MAX_RUPEE_LOSS  = 3_000  # ₹ per trade


@dataclass
class CreditSpreadSignal:
    timestamp: datetime
    direction: str          # 'bullish' or 'bearish'
    alpha1: float
    alpha2: float
    spot: float
    short_strike: int
    long_strike: int
    opt_type: str           # 'PE' (bullish) or 'CE' (bearish)


@dataclass
class SpreadOrder:
    signal: CreditSpreadSignal
    lots: int
    lot_size: int
    margin_per_lot: float
    stop_loss_pct: float
    strategy: str = "zen"
    short_security_id: Optional[str] = None
    long_security_id: Optional[str] = None
    expiry: Optional[str] = None


def _ts_rank(series: pd.Series, window: int) -> pd.Series:
    """Rolling time-series rank: fraction of past values below current value."""
    def rank_last(arr):
        if len(arr) < 2:
            return 0.5
        return float(np.sum(arr[:-1] <= arr[-1])) / (len(arr) - 1)
    return series.rolling(window, min_periods=max(10, window // 8)).apply(rank_last, raw=True)


def compute_alpha1(df: pd.DataFrame, lookback_bars: int = 160) -> pd.Series:
    """
    Alpha1 = time-series rank of 5-min forward price change / open.
    df must have columns: open, close  (5-min bars, indexed by datetime)
    """
    fwd_return = (df["close"].shift(-1) / df["open"] - 1)
    return _ts_rank(fwd_return, lookback_bars)


def compute_alpha2(
    df: pd.DataFrame,
    lookback_bars: int = 60,
    eps: float = 1e-8,
) -> pd.Series:
    """
    Alpha2 = TSR(forward_return × vol_ratio ÷ atm_vol, 300-min lookback).

    Required df columns:
      close, atm_ce_vol, atm_pe_vol, atm_ce_iv, atm_pe_iv
    iv columns are annualised (e.g. 0.18 for 18%).
    If IV columns absent, falls back to realized-vol proxy.
    """
    fwd_return = df["close"].pct_change().shift(-1)

    if "atm_ce_vol" in df.columns and "atm_pe_vol" in df.columns:
        vol_ratio = (df["atm_pe_vol"] + eps) / (df["atm_ce_vol"] + eps)
    else:
        vol_ratio = pd.Series(1.0, index=df.index)

    if "atm_ce_iv" in df.columns and "atm_pe_iv" in df.columns:
        atm_vol = df["atm_ce_iv"].rolling(lookback_bars, min_periods=5).std() + \
                  df["atm_pe_iv"].rolling(lookback_bars, min_periods=5).std() + eps
    else:
        atm_vol = df["close"].pct_change().rolling(lookback_bars, min_periods=5).std() + eps

    raw = fwd_return * vol_ratio / atm_vol
    return _ts_rank(raw, lookback_bars)


def _in_entry_window(ts: pd.Timestamp) -> bool:
    t = ts.time()
    return ENTRY_START <= t <= ENTRY_END


def generate_zen_signals(df: pd.DataFrame) -> list[CreditSpreadSignal]:
    """
    Generate Zen credit spread signals from a 5-min OHLCV DataFrame.

    Required columns: open, close
    Optional: atm_ce_vol, atm_pe_vol, atm_ce_iv, atm_pe_iv
    Index must be a DatetimeIndex (IST tz-aware or naive IST).
    """
    if df.empty or len(df) < 20:
        return []

    df = df.copy()
    df["alpha1"] = compute_alpha1(df)
    df["alpha2"] = compute_alpha2(df)
    df.dropna(subset=["alpha1", "alpha2"], inplace=True)

    signals: list[CreditSpreadSignal] = []
    for ts, row in df.iterrows():
        if not _in_entry_window(ts):
            continue
        a1, a2 = row["alpha1"], row["alpha2"]
        spot   = float(row["close"])
        atm    = int(round(spot / 50) * 50)

        if a1 > BULLISH_THRESH and a2 > BULLISH_THRESH:
            signals.append(CreditSpreadSignal(
                timestamp=ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts,
                direction="bullish",
                alpha1=round(a1, 4),
                alpha2=round(a2, 4),
                spot=spot,
                short_strike=atm,
                long_strike=atm - SPREAD_WIDTH,
                opt_type="PE",
            ))
        elif a1 < BEARISH_THRESH and a2 < BEARISH_THRESH:
            signals.append(CreditSpreadSignal(
                timestamp=ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts,
                direction="bearish",
                alpha1=round(a1, 4),
                alpha2=round(a2, 4),
                spot=spot,
                short_strike=atm,
                long_strike=atm + SPREAD_WIDTH,
                opt_type="CE",
            ))

    logger.debug("[ZEN] %d signals generated from %d bars", len(signals), len(df))
    return signals


def construct_spread_order(
    signal: CreditSpreadSignal,
    lot_size: int = 25,
    margin_per_lot: float = 20_000.0,
    allocated_capital: float = 50_000.0,
    max_rupee_loss: float = MAX_RUPEE_LOSS,
    strategy: str = "zen",
    short_security_id: Optional[str] = None,
    long_security_id: Optional[str] = None,
    expiry: Optional[str] = None,
) -> SpreadOrder:
    """Convert a signal into a concrete SpreadOrder with sizing + SL."""
    max_lots = max(1, int(allocated_capital / max(1, margin_per_lot)))
    lots = max(1, min(max_lots, int(allocated_capital * 0.5 / max(1, margin_per_lot))))
    sl_pct = round(max_rupee_loss / max(1, margin_per_lot) * 100, 2)
    return SpreadOrder(
        signal=signal,
        lots=lots,
        lot_size=lot_size,
        margin_per_lot=margin_per_lot,
        stop_loss_pct=sl_pct,
        strategy=strategy,
        short_security_id=short_security_id,
        long_security_id=long_security_id,
        expiry=expiry,
    )
