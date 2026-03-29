"""Drifting Credit Spread Overnight — signal engine.

Reconstructed from Dhan/Stratzy public description.
Models NIFTY as GBM to estimate probability of price staying within a
±400-point band (i.e., the credit spread staying OTM overnight).
Combines with IV skew (CE vs PE ATM IV difference) and volume flow.

GBM band probability:
  P(lower < S_T < upper | S_0, mu, sigma, T) via log-normal CDF
  Combined with skew score → direction + alpha

Signal rules (10:15–14:15 IST):
  alpha > 0.70 → BULLISH  (range-bound + mild upside) → sell ATM PE, buy ITM PE −400
  alpha < 0.30 → BEARISH  (range-bound + mild downside) → sell ATM CE, buy OTM CE +400

Differs from Zen: Zen = pure momentum rank; Drifting = GBM probability + skew
Use Drifting as a range-bound REGIME FILTER for Zen signals.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import time, datetime
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger("moonshotx.strategies.drifting")

ENTRY_START      = time(10, 15)
ENTRY_END        = time(14, 15)
SPREAD_WIDTH     = 400
MAX_RUPEE_LOSS   = 3_000
BULLISH_THRESH   = 0.70
BEARISH_THRESH   = 0.30
DRIFT_WINDOW     = 48           # bars (240 min) for rolling mu/sigma
HORIZON_MINUTES  = 390          # overnight ~= next-day open, approx 1 trading day
SKEW_WEIGHT      = 0.30         # weight of IV skew vs GBM probability


@dataclass
class DriftSignal:
    timestamp: datetime
    direction: str          # 'bullish' | 'bearish'
    alpha: float
    gbm_score: float        # P(price stays in band)
    skew_score: float       # normalised CE-PE IV skew
    drift: float            # annualised mu
    vol: float              # annualised sigma
    spot: float
    short_strike: int
    long_strike: int
    opt_type: str           # 'PE' | 'CE'


# ── GBM helpers ──────────────────────────────────────────────────────────

def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def gbm_band_probability(
    spot: float,
    mu: float,          # annualised drift
    sigma: float,       # annualised vol
    T_years: float,     # horizon in years
    lower: float,
    upper: float,
) -> float:
    """
    Approximate P(lower < S_T < upper) under GBM via log-normal CDF at T.
    Note: ignores barrier-crossing probability for simplicity;
    for single overnight hold this is a reasonable first-order approximation.
    """
    if sigma <= 0 or T_years <= 0 or spot <= 0:
        return 0.5
    mean = (mu - 0.5 * sigma ** 2) * T_years
    std  = sigma * math.sqrt(T_years)
    log_l = math.log(max(lower, 1e-6) / spot)
    log_u = math.log(max(upper, 1e-6) / spot)
    return max(0.0, min(1.0, _norm_cdf((log_u - mean) / std) - _norm_cdf((log_l - mean) / std)))


def _rolling_mu_sigma(
    closes: pd.Series, window: int, bars_per_year: int = 12 * 48
) -> tuple[pd.Series, pd.Series]:
    """Return annualised rolling drift and vol from log-returns."""
    log_ret = np.log(closes / closes.shift(1))
    mu    = log_ret.rolling(window).mean() * bars_per_year
    sigma = log_ret.rolling(window).std()  * math.sqrt(bars_per_year)
    return mu, sigma


# ── IV skew ───────────────────────────────────────────────────────────────

def _iv_skew_score(chain: Optional[pd.DataFrame], spot: float) -> float:
    """
    IV skew = ATM CE IV − ATM PE IV.
    Positive → calls more expensive → market leans bearish (fear of downside hedge gone, upside bid).
    Normalised via tanh to [-1, 1].
    """
    if chain is None or chain.empty:
        return 0.0
    try:
        chain = chain.copy()
        chain["dist"] = (chain["strike"] - spot).abs()
        atm_ce = chain[(chain["type"] == "CE")].nsmallest(1, "dist")
        atm_pe = chain[(chain["type"] == "PE")].nsmallest(1, "dist")
        if atm_ce.empty or atm_pe.empty:
            return 0.0
        iv_ce = float(atm_ce["iv"].iloc[0])
        iv_pe = float(atm_pe["iv"].iloc[0])
        raw_skew = iv_ce - iv_pe
        return float(np.tanh(raw_skew / 0.05))
    except Exception:
        return 0.0


# ── Signal generation ─────────────────────────────────────────────────────

def generate_drifting_signals(
    df_5m: pd.DataFrame,
    chain_history: Optional[dict] = None,   # {pd.Timestamp → chain DataFrame}
    drift_window: int = DRIFT_WINDOW,
    horizon_minutes: int = HORIZON_MINUTES,
    skew_weight: float = SKEW_WEIGHT,
) -> list[DriftSignal]:
    """
    Generate Drifting Credit Spread signals.

    df_5m: 5-min NIFTY bars; required columns: close
    chain_history: optional {Timestamp → DataFrame(strike,iv,type)} for IV skew
    """
    if df_5m.empty or len(df_5m) < drift_window + 5:
        return []

    df = df_5m.copy()
    bars_per_year = 252 * 75    # ~75 five-min bars per trading day
    mu_s, sigma_s = _rolling_mu_sigma(df["close"], drift_window, bars_per_year)
    df["mu"]    = mu_s
    df["sigma"] = sigma_s
    df.dropna(subset=["mu", "sigma"], inplace=True)

    T = (horizon_minutes / 60.0) / 252.0    # convert to fraction of year

    signals: list[DriftSignal] = []
    for ts, row in df.iterrows():
        t = ts.time() if hasattr(ts, "time") else ts.to_pydatetime().time()
        if not (ENTRY_START <= t <= ENTRY_END):
            continue

        spot  = float(row["close"])
        mu    = float(row["mu"])
        sigma = max(0.01, float(row["sigma"]))
        atm   = int(round(spot / 50) * 50)

        lower = spot - SPREAD_WIDTH
        upper = spot + SPREAD_WIDTH
        p_band = gbm_band_probability(spot, mu, sigma, T, lower, upper)

        # IV skew from nearest chain snapshot
        chain = None
        if chain_history:
            candidates = [ct for ct in chain_history if ct <= ts]
            if candidates:
                chain = chain_history[max(candidates)]
        skew = _iv_skew_score(chain, spot)

        # Alpha: GBM probability weighted with skew nudge for direction
        # p_band > threshold → range-bound → sell spread
        # skew direction determines which side
        alpha_bull = p_band + skew_weight * max(0.0,  skew)   # skew > 0 → CE expensive → bullish
        alpha_bear = p_band + skew_weight * max(0.0, -skew)   # skew < 0 → PE expensive → bearish

        alpha_bull = min(1.0, alpha_bull)
        alpha_bear = min(1.0, alpha_bear)

        if p_band > BULLISH_THRESH and alpha_bull >= BULLISH_THRESH and mu > 0:
            signals.append(DriftSignal(
                timestamp=ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts,
                direction="bullish",
                alpha=round(alpha_bull, 4),
                gbm_score=round(p_band, 4),
                skew_score=round(skew, 4),
                drift=round(mu, 4),
                vol=round(sigma, 4),
                spot=spot,
                short_strike=atm,
                long_strike=atm - SPREAD_WIDTH,
                opt_type="PE",
            ))
        elif p_band > BULLISH_THRESH and alpha_bear >= BULLISH_THRESH and mu < 0:
            signals.append(DriftSignal(
                timestamp=ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts,
                direction="bearish",
                alpha=round(alpha_bear, 4),
                gbm_score=round(p_band, 4),
                skew_score=round(skew, 4),
                drift=round(mu, 4),
                vol=round(sigma, 4),
                spot=spot,
                short_strike=atm,
                long_strike=atm + SPREAD_WIDTH,
                opt_type="CE",
            ))

    logger.debug("[DRIFT] %d signals from %d bars", len(signals), len(df))
    return signals


def drifting_signal_to_spread(
    sig: DriftSignal,
    lot_size: int = 25,
    margin_per_lot: float = 20_000.0,
    allocated_capital: float = 50_000.0,
    max_rupee_loss: float = MAX_RUPEE_LOSS,
    short_security_id: Optional[str] = None,
    long_security_id: Optional[str] = None,
    expiry: Optional[str] = None,
):
    """Convert a DriftSignal to a SpreadOrder (reuses zen_spread.SpreadOrder)."""
    from strategies.zen_spread import CreditSpreadSignal, construct_spread_order
    fake_sig = CreditSpreadSignal(
        timestamp=sig.timestamp,
        direction=sig.direction,
        alpha1=sig.alpha,
        alpha2=sig.gbm_score,
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
        strategy="drifting",
        short_security_id=short_security_id,
        long_security_id=long_security_id,
        expiry=expiry,
    )
