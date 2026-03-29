"""Paper trading engine for Momentum, Zen, Curvature strategies.

Runs every 5 minutes during market hours (10:00-15:30 IST).
Fetches live 5-min ^NSEI data, evaluates signals, tracks paper positions,
and saves NAV snapshots to MongoDB.

MongoDB collections:
  strategy_nav_snapshots  — {ts, nav: {momentum, zen, curvature}, session_id}
  strategy_paper_trades   — {ts, strategy, direction, entry_spot, exit_spot, pnl, status}
"""
from __future__ import annotations

import asyncio
import logging
import math
import uuid
from datetime import datetime, time as dtime
from typing import Optional

import numpy as np

logger = logging.getLogger("moonshotx.strategies.paper_trader")

# ── Constants (mirrors backtest_5min.py) ──────────────────────────────────────
NIFTY_LOT_SIZE    = 25
SPREAD_WIDTH      = 400
MAX_LOSS_RS       = 3_000
POSITION_NOTIONAL = 67_500.0
ALPHA_BULL        = 0.80
ALPHA_BEAR        = 0.20
ALPHA1_WINDOW     = 160
RISK_FREE_RATE    = 0.07
BARS_PER_DAY      = 75
INITIAL_CAPITAL   = 100_000.0
TRADING_START     = dtime(10, 0)
TRADING_END       = dtime(15, 30)
TICK_INTERVAL_S   = 300  # 5 min


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2)))


def _bs_put(S, K, T, r, sigma) -> float:
    if T <= 0 or sigma <= 0:
        return max(K - S, 0.0)
    d1 = (math.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    return K * math.exp(-r * T) * _norm_cdf(-d2) - S * _norm_cdf(-d1)


def _bs_call(S, K, T, r, sigma) -> float:
    if T <= 0 or sigma <= 0:
        return max(S - K, 0.0)
    d1 = (math.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    return S * _norm_cdf(d1) - K * math.exp(-r * T) * _norm_cdf(d2)


def _spread_credit(S, short_K, long_K, T, r, sigma, opt_type: str) -> float:
    fn = _bs_put if opt_type == "PE" else _bs_call
    return max(0.0, fn(S, short_K, T, r, sigma) - fn(S, long_K, T, r, sigma))


def _tsrank(series: np.ndarray) -> float:
    if len(series) < 2:
        return 0.5
    return float(np.sum(series[:-1] <= series[-1])) / (len(series) - 1)


def _fetch_nifty_bars(bars: int = 220) -> Optional[object]:
    """Fetch latest 5-min ^NSEI bars from yfinance. Returns DataFrame or None."""
    try:
        import yfinance as yf
        import pandas as pd
        ticker = yf.Ticker("^NSEI")
        df = ticker.history(period="5d", interval="5m")
        if df.empty:
            return None
        df.index = pd.to_datetime(df.index)
        if df.index.tzinfo is not None:
            df.index = df.index.tz_convert("Asia/Kolkata").tz_localize(None)
        df = df[["Open", "High", "Low", "Close", "Volume"]].rename(columns=str.lower)
        df = df.dropna(subset=["close"])
        df = df[df.index.time >= dtime(9, 15)]
        df = df[df.index.time <= dtime(15, 30)]
        return df.tail(bars)
    except Exception as e:
        logger.warning("yfinance fetch failed: %s", e)
        return None


class StrategyState:
    """Per-strategy paper trading state."""

    def __init__(self, name: str):
        self.name       = name
        self.equity     = INITIAL_CAPITAL
        self.in_trade   = False
        self.trade: Optional[dict] = None
        self.n_trades   = 0
        self.n_wins     = 0
        self.peak       = INITIAL_CAPITAL

    @property
    def win_rate(self) -> float:
        return self.n_wins / self.n_trades if self.n_trades else 0.0

    @property
    def drawdown(self) -> float:
        self.peak = max(self.peak, self.equity)
        return self.peak - self.equity

    def to_dict(self) -> dict:
        return {
            "name":      self.name,
            "equity":    round(self.equity, 2),
            "pnl":       round(self.equity - INITIAL_CAPITAL, 2),
            "roc_pct":   round((self.equity - INITIAL_CAPITAL) / INITIAL_CAPITAL * 100, 2),
            "in_trade":  self.in_trade,
            "n_trades":  self.n_trades,
            "win_rate":  round(self.win_rate * 100, 1),
            "drawdown":  round(self.drawdown, 2),
        }


class PaperTradingEngine:
    """
    Real-time paper trading engine for Momentum, Zen, Curvature.

    Usage:
        engine = PaperTradingEngine(db=mongo_db)
        await engine.start()   # non-blocking: starts background task
        engine.stop()
    """

    STRATEGIES = ["momentum", "zen", "curvature"]

    def __init__(self, db=None):
        self._db         = db
        self._running    = False
        self._task: Optional[asyncio.Task] = None
        self._session_id = str(uuid.uuid4())[:8]
        self._states     = {s: StrategyState(s) for s in self.STRATEGIES}
        self._start_time: Optional[datetime] = None
        self._tick_count = 0
        self._last_error: Optional[str] = None

    # ── Public API ────────────────────────────────────────────────────────────

    async def start(self):
        if self._running:
            return
        self._running    = True
        self._start_time = datetime.now()
        logger.info("[PaperTrader] Starting — session %s", self._session_id)
        self._task = asyncio.ensure_future(self._loop())

    def stop(self):
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
        logger.info("[PaperTrader] Stopped after %d ticks", self._tick_count)

    def status(self) -> dict:
        return {
            "running":    self._running,
            "session_id": self._session_id,
            "start_time": self._start_time.isoformat() if self._start_time else None,
            "tick_count": self._tick_count,
            "last_error": self._last_error,
            "strategies": {s: self._states[s].to_dict() for s in self.STRATEGIES},
        }

    async def get_nav_history(self, days: int = 30) -> list:
        """Return NAV snapshots from MongoDB, newest first."""
        if self._db is None:
            return []
        try:
            from datetime import timedelta
            since = datetime.now() - timedelta(days=days)
            docs = await self._db.strategy_nav_snapshots.find(
                {"ts": {"$gte": since}},
                {"_id": 0},
            ).sort("ts", 1).to_list(10_000)
            return docs
        except Exception as e:
            logger.warning("Nav history fetch error: %s", e)
            return []

    # ── Internal loop ─────────────────────────────────────────────────────────

    async def _loop(self):
        while self._running:
            try:
                now = datetime.now()
                if TRADING_START <= now.time() <= TRADING_END:
                    await self._tick()
                else:
                    logger.debug("[PaperTrader] Outside market hours — sleeping")
            except asyncio.CancelledError:
                break
            except Exception as e:
                self._last_error = str(e)
                logger.error("[PaperTrader] Tick error: %s", e, exc_info=True)
            await asyncio.sleep(TICK_INTERVAL_S)

    async def _tick(self):
        df = await asyncio.to_thread(_fetch_nifty_bars, 220)
        if df is None or len(df) < ALPHA1_WINDOW + 5:
            logger.warning("[PaperTrader] Not enough bars (%s)", len(df) if df is not None else 0)
            return

        self._tick_count += 1
        log_ret    = np.log(df["close"] / df["close"].shift(1)).fillna(0.0).values
        alpha1     = _tsrank(log_ret[-ALPHA1_WINDOW:])
        spot       = float(df["close"].iloc[-1])
        hv20       = float(np.log(df["close"] / df["close"].shift(1)).rolling(20).std().iloc[-1]) * math.sqrt(252 * BARS_PER_DAY)
        hv5        = float(np.log(df["close"] / df["close"].shift(1)).rolling(5).std().iloc[-1]) * math.sqrt(252 * BARS_PER_DAY)
        hv20       = max(0.05, hv20)
        hv5        = max(0.05, hv5)
        ts         = df.index[-1]

        nav_snapshot = {"ts": datetime.now(), "session_id": self._session_id, "spot": round(spot, 2), "nav": {}}

        for strat_name in self.STRATEGIES:
            state = self._states[strat_name]
            await self._update_strategy(state, strat_name, alpha1, spot, hv20, hv5, ts)
            nav_snapshot["nav"][strat_name] = round(state.equity, 2)

        await self._save_snapshot(nav_snapshot)

    async def _update_strategy(self, state: StrategyState, strat: str,
                                alpha1: float, spot: float, hv20: float, hv5: float, ts):
        """Evaluate signal, manage open trade, update equity."""
        # ── Exit any open overnight trade ─────────────────────────────────
        if state.in_trade and state.trade:
            t = state.trade
            entry_date = t["entry_ts"].date() if hasattr(t["entry_ts"], "date") else None
            today      = datetime.now().date()
            if entry_date and today > entry_date:
                pnl = self._calc_exit_pnl(strat, t, spot, hv20)
                state.equity   += pnl
                state.n_trades += 1
                if pnl > 0:
                    state.n_wins += 1
                state.in_trade = False
                state.trade    = None
                logger.info("[PaperTrader] %s EXIT: spot=%.0f pnl=₹%.0f equity=₹%.0f",
                            strat, spot, pnl, state.equity)
                await self._save_trade(strat, t, spot, pnl)

        # ── Check for new entry ───────────────────────────────────────────
        if state.in_trade:
            return

        composite = self._composite_signal(strat, alpha1, hv5, hv20)

        if composite > ALPHA_BULL:
            direction = "bullish"
        elif composite < ALPHA_BEAR:
            direction = "bearish"
        else:
            return

        if strat == "momentum":
            state.trade = {"entry_ts": ts, "direction": direction, "spot": spot, "opt_type": "NA"}
        else:
            atm = int(round(spot / 50) * 50)
            T   = 4 / 365.0
            if direction == "bullish":
                short_K, long_K, opt_type = atm, atm - SPREAD_WIDTH, "PE"
            else:
                short_K, long_K, opt_type = atm, atm + SPREAD_WIDTH, "CE"
            credit_pu = _spread_credit(spot, short_K, long_K, T, RISK_FREE_RATE, hv20, opt_type)
            if credit_pu < 30.0:
                return
            state.trade = {
                "entry_ts": ts, "direction": direction, "spot": spot,
                "short_K": short_K, "long_K": long_K, "opt_type": opt_type,
                "credit_pu": credit_pu,
            }

        state.in_trade = True
        logger.info("[PaperTrader] %s ENTRY: %s spot=%.0f composite=%.3f",
                    strat, direction, spot, composite)

    def _composite_signal(self, strat: str, alpha1: float, hv5: float, hv20: float) -> float:
        if strat == "zen":
            return alpha1
        elif strat == "curvature":
            curv_proxy = min(2.0, hv5 / max(hv20, 0.01))
            return min(1.0, max(0.0, 0.65 * alpha1 + 0.35 * (curv_proxy - 0.5)))
        elif strat == "momentum":
            return alpha1
        return alpha1

    def _calc_exit_pnl(self, strat: str, trade: dict, spot: float, hv20: float) -> float:
        if strat == "momentum":
            direction_mult = 1.0 if trade["direction"] == "bullish" else -1.0
            raw = direction_mult * (spot - trade["spot"]) / trade["spot"] * POSITION_NOTIONAL
            return max(raw, -MAX_LOSS_RS * 3)  # 3 lots equivalent
        else:
            T_exit   = max(0.0001, 3 / 365.0)
            exit_val = _spread_credit(spot, trade["short_K"], trade["long_K"],
                                      T_exit, RISK_FREE_RATE, hv20, trade["opt_type"])
            pnl_pu   = trade["credit_pu"] - exit_val
            pnl_pu   = max(pnl_pu, -MAX_LOSS_RS / NIFTY_LOT_SIZE)
            return pnl_pu * 3 * NIFTY_LOT_SIZE  # 3 lots

    async def _save_snapshot(self, doc: dict):
        if self._db is None:
            return
        try:
            await self._db.strategy_nav_snapshots.insert_one(doc)
        except Exception as e:
            logger.warning("Snapshot save error: %s", e)

    async def _save_trade(self, strat: str, trade: dict, exit_spot: float, pnl: float):
        if self._db is None:
            return
        try:
            await self._db.strategy_paper_trades.insert_one({
                "ts":          datetime.now(),
                "strategy":    strat,
                "session_id":  self._session_id,
                "direction":   trade["direction"],
                "entry_spot":  round(trade["spot"], 2),
                "entry_ts":    trade["entry_ts"],
                "exit_spot":   round(exit_spot, 2),
                "pnl":         round(pnl, 2),
            })
        except Exception as e:
            logger.warning("Trade save error: %s", e)
