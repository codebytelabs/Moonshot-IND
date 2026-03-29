"""Historical backtester for Zen / Curvature / ZenCurve Hybrid strategies.

Data sources:
  - NIFTY 5-min OHLC: yfinance (^NSEI) — free, 60-day intraday limit
  - NIFTY daily close: yfinance (^NSEI) — 2 years available
  - ATM options prices: approximated via Black-Scholes using HV20 as IV proxy
    (real option data requires DhanHQ historical API — expensive; BS gives
     a sound approximation for backtesting spread credit/debit)

Spread P&L simulation:
  - Entry: receive net credit (sell ATM premium - buy hedge premium)
  - Exit: either next-day open (overnight hold) or stop-loss if MTM >= SL
  - Overnight gap risk: modelled by repricing at next-day open using BS
  - Stop-loss: ₹3,000 per lot

Metrics reported:
  - Total trades, win rate, avg credit, avg P&L, total P&L
  - Max drawdown (₹ and %), Sharpe ratio (annualised)
  - Best / worst trade
  - P&L equity curve
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, date
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger("moonshotx.strategies.backtest")

RISK_FREE_RATE = 0.07          # India 91-day T-Bill ~7%
NIFTY_LOT_SIZE = 25
SPREAD_WIDTH   = 400           # points
MAX_LOSS_RS    = 3_000         # ₹ per trade SL
MIN_CREDIT     = 50.0          # ₹/unit min viable credit (else skip)
SLEEP_DAYS     = 1             # bars to skip after a trade (avoid overlapping)


# ── Black-Scholes helpers ─────────────────────────────────────────────────

def _d1_d2(S, K, T, r, sigma):
    if T <= 0 or sigma <= 0:
        return 0.0, 0.0
    d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    return d1, d2


def _norm_cdf(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2)))


def bs_put(S, K, T, r, sigma) -> float:
    """Black-Scholes put price."""
    if T <= 0:
        return max(K - S, 0.0)
    d1, d2 = _d1_d2(S, K, T, r, sigma)
    return K * math.exp(-r * T) * _norm_cdf(-d2) - S * _norm_cdf(-d1)


def bs_call(S, K, T, r, sigma) -> float:
    """Black-Scholes call price."""
    if T <= 0:
        return max(S - K, 0.0)
    d1, d2 = _d1_d2(S, K, T, r, sigma)
    return S * _norm_cdf(d1) - K * math.exp(-r * T) * _norm_cdf(d2)


def spread_credit(S, short_K, long_K, T, r, sigma, opt_type) -> float:
    """Net credit received (sell short_K, buy long_K). Both same type."""
    if opt_type == "PE":
        short_p = bs_put(S, short_K, T, r, sigma)
        long_p  = bs_put(S, long_K,  T, r, sigma)
    else:
        short_p = bs_call(S, short_K, T, r, sigma)
        long_p  = bs_call(S, long_K,  T, r, sigma)
    return max(0.0, short_p - long_p)


def spread_value(S, short_K, long_K, T, r, sigma, opt_type) -> float:
    """Current value of the spread (cost to buy back)."""
    return spread_credit(S, short_K, long_K, T, r, sigma, opt_type)


# ── Data loader ──────────────────────────────────────────────────────────

def load_nifty_daily(years: int = 2) -> pd.DataFrame:
    """Load NIFTY daily OHLCV from yfinance."""
    try:
        import yfinance as yf
        end   = datetime.today()
        start = end - timedelta(days=365 * years)
        df = yf.download("^NSEI", start=start.strftime("%Y-%m-%d"),
                         end=end.strftime("%Y-%m-%d"), interval="1d",
                         progress=False, auto_adjust=True)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.droplevel(1)
        df.index = pd.to_datetime(df.index)
        df.rename(columns=str.lower, inplace=True)
        logger.info("[BACKTEST] Loaded %d daily NIFTY bars", len(df))
        return df
    except Exception as e:
        logger.error("[BACKTEST] yfinance daily load failed: %s", e)
        return pd.DataFrame()


def _hv(closes: pd.Series, window: int = 20) -> pd.Series:
    """Historical volatility (annualised) using log-returns."""
    log_ret = np.log(closes / closes.shift(1))
    return log_ret.rolling(window).std() * math.sqrt(252)


# ── Backtest engine ──────────────────────────────────────────────────────

@dataclass
class BacktestTrade:
    entry_date: date
    exit_date: Optional[date]
    direction: str
    short_strike: int
    long_strike: int
    opt_type: str
    spot_entry: float
    spot_exit: float
    credit_per_unit: float
    exit_value_per_unit: float
    lots: int
    pnl: float
    status: str                    # 'profit' | 'stopped' | 'expired'


@dataclass
class BacktestResult:
    strategy: str
    trades: list = field(default_factory=list)
    equity_curve: list = field(default_factory=list)

    @property
    def n_trades(self) -> int:
        return len(self.trades)

    @property
    def wins(self) -> list:
        return [t for t in self.trades if t.pnl > 0]

    @property
    def losses(self) -> list:
        return [t for t in self.trades if t.pnl <= 0]

    @property
    def win_rate(self) -> float:
        return len(self.wins) / max(1, self.n_trades)

    @property
    def total_pnl(self) -> float:
        return sum(t.pnl for t in self.trades)

    @property
    def avg_pnl(self) -> float:
        return self.total_pnl / max(1, self.n_trades)

    @property
    def avg_credit(self) -> float:
        return np.mean([t.credit_per_unit * NIFTY_LOT_SIZE for t in self.trades]) if self.trades else 0.0

    @property
    def max_drawdown(self) -> float:
        """Max peak-to-trough drawdown in ₹."""
        if not self.equity_curve:
            return 0.0
        eq = np.array(self.equity_curve)
        peak = np.maximum.accumulate(eq)
        dd   = peak - eq
        return float(dd.max())

    @property
    def sharpe(self) -> float:
        """Annualised Sharpe of trade P&Ls (assume ~2 trades/week = 104/yr)."""
        if self.n_trades < 4:
            return 0.0
        pnls = np.array([t.pnl for t in self.trades])
        if pnls.std() == 0:
            return 0.0
        trades_per_year = 104
        return float((pnls.mean() / pnls.std()) * math.sqrt(trades_per_year))

    def report(self) -> dict:
        return {
            "strategy": self.strategy,
            "n_trades": self.n_trades,
            "win_rate_pct": round(self.win_rate * 100, 1),
            "total_pnl": round(self.total_pnl, 0),
            "avg_pnl_per_trade": round(self.avg_pnl, 0),
            "avg_credit_per_trade": round(self.avg_credit, 0),
            "max_drawdown": round(self.max_drawdown, 0),
            "sharpe": round(self.sharpe, 2),
            "best_trade": round(max((t.pnl for t in self.trades), default=0), 0),
            "worst_trade": round(min((t.pnl for t in self.trades), default=0), 0),
        }

    def print_report(self):
        r = self.report()
        print(f"\n{'='*55}")
        print(f"  BACKTEST REPORT — {r['strategy'].upper()}")
        print(f"{'='*55}")
        print(f"  Trades       : {r['n_trades']}")
        print(f"  Win Rate     : {r['win_rate_pct']}%")
        print(f"  Total P&L    : ₹{r['total_pnl']:,.0f}")
        print(f"  Avg P&L/trade: ₹{r['avg_pnl_per_trade']:,.0f}")
        print(f"  Avg Credit   : ₹{r['avg_credit_per_trade']:,.0f}")
        print(f"  Max Drawdown : ₹{r['max_drawdown']:,.0f}")
        print(f"  Sharpe       : {r['sharpe']}")
        print(f"  Best Trade   : ₹{r['best_trade']:,.0f}")
        print(f"  Worst Trade  : ₹{r['worst_trade']:,.0f}")
        print(f"{'='*55}\n")


def run_backtest(
    strategy: str = "zen",
    years: int = 1,
    initial_capital: float = 50_000.0,
    lots: int = 1,
    max_loss_rs: float = MAX_LOSS_RS,
    spread_width: int = SPREAD_WIDTH,
    alpha_bull: float = 0.80,
    alpha_bear: float = 0.20,
    dte_target: int = 7,
) -> BacktestResult:
    """
    Run offline backtest on NIFTY daily data using Black-Scholes pricing.

    strategy: 'zen' | 'curvature' | 'zenCurve'
    years: how many years of history to use
    initial_capital: starting capital in ₹
    lots: number of lots per trade
    """
    result = BacktestResult(strategy=strategy)
    df = load_nifty_daily(years)
    if df.empty or len(df) < 30:
        logger.error("[BACKTEST] Insufficient data")
        return result

    df["hv20"] = _hv(df["close"], 20).fillna(0.20)
    df["hv5"]  = _hv(df["close"], 5).fillna(0.20)

    equity    = initial_capital
    skip_days = 0
    equity_curve = [equity]
    prev_date  = None

    for i in range(30, len(df) - 2):
        row      = df.iloc[i]
        next_row = df.iloc[i + 1]
        entry_date = row.name.date() if hasattr(row.name, "date") else row.name

        if skip_days > 0:
            skip_days -= 1
            continue

        spot   = float(row["close"])
        hv     = max(0.05, float(row["hv20"]))
        hv_short = max(0.05, float(row["hv5"]))

        # ── Signal generation (simplified daily proxy) ────────────────
        # Use 5-day vs 20-day momentum rank as Zen alpha proxy on daily bars
        window = df["close"].iloc[max(0, i-40): i+1]
        fwd_ret = float(df["close"].iloc[i] / df["close"].iloc[i-1] - 1) if i > 0 else 0.0
        rank_val = float(np.sum(window.pct_change().dropna() <= fwd_ret)) / max(len(window) - 1, 1)

        # Curvature proxy: HV ratio (short-term vs long-term vol imbalance)
        curv_proxy = float(np.clip(hv_short / max(hv, 0.01), 0.5, 2.0))
        curv_alpha_val = (np.tanh((curv_proxy - 1.0) * 2) + 1) / 2  # → [0,1]

        # Drifting proxy: GBM band probability using rolling mu/sigma
        # mu/sigma computed from last 20 bars of daily log-returns
        log_rets = np.log(df["close"].iloc[max(0,i-20):i+1] / df["close"].iloc[max(0,i-20):i+1].shift(1)).dropna()
        drift_mu    = float(log_rets.mean() * 252) if len(log_rets) >= 5 else 0.0
        drift_sigma = max(0.01, float(log_rets.std() * math.sqrt(252)))
        T_drift = 1.0 / 252.0  # 1 trading day horizon
        lower_band = spot - SPREAD_WIDTH
        upper_band = spot + SPREAD_WIDTH
        log_l = math.log(max(lower_band, 1.0) / spot)
        log_u = math.log(upper_band / spot)
        mean_d = (drift_mu - 0.5 * drift_sigma**2) * T_drift
        std_d  = drift_sigma * math.sqrt(T_drift)
        from math import erf
        def _ncdf(x): return 0.5*(1+erf(x/math.sqrt(2)))
        p_band = max(0.0, min(1.0, _ncdf((log_u-mean_d)/std_d) - _ncdf((log_l-mean_d)/std_d)))
        drifting_alpha = p_band + 0.30 * (0.5 + 0.5*(drift_mu/max(abs(drift_mu),0.001)))
        drifting_alpha = min(1.0, max(0.0, drifting_alpha))

        # V-Score proxy: inverse vol score (alpha) + viscosity (alpha9)
        # Alpha  = 1 - TSRank(HV20, 60) → high when vol is SUPPRESSED
        # Alpha9 = TSRank(-ΔHV × |ret/HV|, 40) → high when vol DROPS as price moves
        hv_window = df["hv20"].iloc[max(0, i-60): i+1]
        inv_vol_score = 1.0 / max(hv, 0.01)
        rank_inv_vol = float(np.sum((1.0 / hv_window.replace(0, np.nan).fillna(0.20)) <= inv_vol_score)) / max(len(hv_window) - 1, 1)
        hv_change = float(df["hv20"].iloc[i] - df["hv20"].iloc[i-1]) if i > 0 else 0.0
        viscosity_raw = (-hv_change) * (abs(fwd_ret) / max(hv, 0.01))
        visc_window_vals = []
        for vi in range(max(0, i-40), i+1):
            if vi > 0:
                dhv = float(df["hv20"].iloc[vi] - df["hv20"].iloc[vi-1])
                fr  = float(df["close"].iloc[vi] / df["close"].iloc[vi-1] - 1)
                hvv = max(0.01, float(df["hv20"].iloc[vi]))
                visc_window_vals.append((-dhv) * (abs(fr) / hvv))
        alpha9_vscore = float(np.sum(np.array(visc_window_vals[:-1]) <= viscosity_raw)) / max(len(visc_window_vals) - 1, 1) if len(visc_window_vals) > 1 else 0.5

        if strategy == "zen":
            alpha = rank_val
            composite = alpha
        elif strategy == "curvature":
            alpha = curv_alpha_val
            composite = alpha
        elif strategy == "drifting":
            composite = drifting_alpha
        elif strategy == "vscore":
            composite = 0.55 * rank_inv_vol + 0.45 * alpha9_vscore
        else:  # zenCurve hybrid
            composite = 0.60 * rank_val + 0.40 * curv_alpha_val

        # V-Score uses same alpha thresholds — high score = vol suppressed = bullish (sell PE)
        # Drifting direction: only enter when GBM says range-bound, drift sets direction
        if strategy == "drifting":
            if p_band > alpha_bull and drift_mu > 0:
                direction = "bullish"
            elif p_band > alpha_bull and drift_mu < 0:
                direction = "bearish"
            else:
                continue
        else:
            if composite > alpha_bull:
                direction = "bullish"
            elif composite < alpha_bear:
                direction = "bearish"
            else:
                continue

        # ── Spread construction ───────────────────────────────────────
        atm = int(round(spot / 50) * 50)
        T   = dte_target / 365.0

        if direction == "bullish":
            short_K, long_K, opt_type = atm, atm - spread_width, "PE"
        else:
            short_K, long_K, opt_type = atm, atm + spread_width, "CE"

        credit_pu = spread_credit(spot, short_K, long_K, T, RISK_FREE_RATE, hv, opt_type)
        if credit_pu < MIN_CREDIT:
            continue

        # ── Simulate overnight exit ───────────────────────────────────
        exit_spot = float(next_row["close"])
        hv_exit   = max(0.05, float(next_row.get("hv20", hv)))
        T_exit    = max(0.001, (dte_target - 1) / 365.0)

        exit_value_pu = spread_value(exit_spot, short_K, long_K, T_exit, RISK_FREE_RATE, hv_exit, opt_type)

        # P&L per unit = credit received - cost to close
        pnl_pu = credit_pu - exit_value_pu

        # Apply stop-loss
        max_loss_pu = max_loss_rs / (lots * NIFTY_LOT_SIZE)
        if pnl_pu < -max_loss_pu:
            pnl_pu = -max_loss_pu
            status = "stopped"
        elif exit_value_pu < 0.10 * credit_pu:
            status = "expired"
        else:
            status = "profit" if pnl_pu > 0 else "stopped"

        trade_pnl = pnl_pu * lots * NIFTY_LOT_SIZE
        equity   += trade_pnl
        equity_curve.append(equity)

        result.trades.append(BacktestTrade(
            entry_date=entry_date,
            exit_date=next_row.name.date() if hasattr(next_row.name, "date") else next_row.name,
            direction=direction,
            short_strike=short_K,
            long_strike=long_K,
            opt_type=opt_type,
            spot_entry=spot,
            spot_exit=exit_spot,
            credit_per_unit=round(credit_pu, 2),
            exit_value_per_unit=round(exit_value_pu, 2),
            lots=lots,
            pnl=round(trade_pnl, 2),
            status=status,
        ))
        skip_days = SLEEP_DAYS

    result.equity_curve = equity_curve
    return result


def run_all_backtests(
    years: int = 1,
    initial_capital: float = 50_000.0,
    lots: int = 1,
) -> dict:
    """Run all five strategies and return comparative report."""
    results = {}
    for strat in ["zen", "curvature", "zenCurve", "drifting", "vscore"]:
        r = run_backtest(strategy=strat, years=years,
                         initial_capital=initial_capital, lots=lots)
        results[strat] = r
        r.print_report()
    return results
