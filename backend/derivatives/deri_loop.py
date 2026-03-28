"""Derivatives trading loop — runs as a parallel asyncio task alongside equity loop.

Cycle: every 5 minutes during NSE F&O hours (09:15–15:00 IST, Mon–Fri).
Flow per cycle:
  1. Check market open + DTE window
  2. Fetch NIFTY options chain (NSE API)
  3. Compute T (years to expiry) and get regime
  4. Select strategy via strategies.select_strategy()
  5. Risk check via DerivativesRiskManager
  6. Place legs via Kite (paper or live)
  7. Monitor open strategies → stop-out or take-profit
  8. Force-close all legs at 15:00 IST (MIS expiry safety)
  9. Broadcast state via WebSocket
  10. Persist to MongoDB derivatives_positions + derivatives_events
"""
import asyncio
import logging
from datetime import datetime, timezone, time as dtime
from zoneinfo import ZoneInfo
from typing import Callable, Optional

from derivatives.chain import fetch_chain_nse, OptionChain
from derivatives.expiry import (
    current_expiry, days_to_expiry, trading_days_to_expiry, is_trading_day
)
from derivatives.strategies import select_strategy, StrategySignal, StrategyLeg
from derivatives.deri_risk import DerivativesRiskManager, OpenStrategy

logger = logging.getLogger("moonshotx.derivatives.loop")

IST = ZoneInfo("Asia/Kolkata")

# ── Timing constants ──────────────────────────────────────────────────────────
DERI_LOOP_INTERVAL_SECS  = 300      # scan every 5 min
DERI_FORCE_CLOSE_TIME    = dtime(15, 0)   # close all at 15:00 IST (before MIS auto-sq)
DERI_NO_ENTRY_TIME       = dtime(14, 30)  # no new entries after 14:30 IST
NSE_OPEN_TIME            = dtime(9, 15)
NSE_CLOSE_TIME           = dtime(15, 30)

SYMBOLS = ["NIFTY"]   # expand to ["NIFTY", "BANKNIFTY"] when ready


def _now_ist() -> datetime:
    return datetime.now(IST)


def _is_fo_open() -> bool:
    now = _now_ist()
    if now.weekday() >= 5:
        return False
    if not is_trading_day(now.date()):
        return False
    t = now.time()
    return NSE_OPEN_TIME <= t < NSE_CLOSE_TIME


def _past_no_entry_time() -> bool:
    t = _now_ist().time()
    return t >= DERI_NO_ENTRY_TIME


def _past_force_close_time() -> bool:
    t = _now_ist().time()
    return t >= DERI_FORCE_CLOSE_TIME


class DerivativesLoop:
    def __init__(
        self,
        db,
        kite,
        regime_manager,
        broadcast_fn: Callable,
    ):
        self.db = db
        self.kite = kite
        self.regime_manager = regime_manager
        self.broadcast = broadcast_fn
        self.risk = DerivativesRiskManager()
        self._running = False
        self._loop_count = 0
        self._last_chain: Optional[OptionChain] = None
        self._force_closed_today = False

    # ── Public control ────────────────────────────────────────────────────────

    def start(self) -> asyncio.Task:
        self._running = True
        return asyncio.create_task(self._run())

    def stop(self):
        self._running = False

    @property
    def is_running(self) -> bool:
        return self._running

    # ── Main loop ─────────────────────────────────────────────────────────────

    async def _run(self):
        logger.info("[DERI] Derivatives loop started")
        while self._running:
            try:
                await self._cycle()
            except Exception as e:
                logger.error(f"[DERI] Loop error: {e}", exc_info=True)
            await asyncio.sleep(DERI_LOOP_INTERVAL_SECS)

    async def _cycle(self):
        self._loop_count += 1
        now_ist = _now_ist()
        logger.info(f"[DERI] #{self._loop_count} — {now_ist.strftime('%H:%M IST')}")

        if not _is_fo_open():
            logger.info("[DERI] F&O market closed — skipping cycle")
            self._force_closed_today = False   # reset at day start
            self._broadcast_status()
            return

        # ── Force-close all legs at 15:00 IST ────────────────────────────────
        if _past_force_close_time() and not self._force_closed_today:
            logger.info("[DERI] 15:00 IST — force-closing all derivative positions")
            await self._close_all_strategies("Force close at 15:00 IST")
            self._force_closed_today = True
            self._broadcast_status()
            return

        # ── Monitor open strategies first ─────────────────────────────────────
        await self._monitor_open_strategies()

        # ── No new entries after 14:30 IST ────────────────────────────────────
        if _past_no_entry_time():
            logger.info("[DERI] Past 14:30 IST — no new entries, monitoring only")
            self._broadcast_status()
            return

        # ── Fetch regime ──────────────────────────────────────────────────────
        try:
            regime_data = await self.regime_manager.get_current()
        except Exception as e:
            logger.warning(f"[DERI] Regime fetch failed: {e}")
            self._broadcast_status()
            return

        regime    = regime_data.get("regime", "neutral")
        india_vix = regime_data.get("india_vix", 16.0)

        # ── Process each symbol ───────────────────────────────────────────────
        for symbol in SYMBOLS:
            await self._process_symbol(symbol, regime, india_vix)

        self._broadcast_status()

    async def _process_symbol(self, symbol: str, regime: str, india_vix: float):
        dte = days_to_expiry(symbol)
        tdte = trading_days_to_expiry(symbol)

        logger.info(f"[DERI] {symbol} DTE={dte} trading_DTE={tdte} VIX={india_vix:.1f} regime={regime}")

        # Already have a strategy for this symbol?
        active = [
            s for s in self.risk.open_strategies
            if not s.stopped and s.symbol == symbol
        ]
        if active:
            logger.info(f"[DERI] {symbol}: {len(active)} active strategy — skipping new entry")
            return

        # ── Fetch options chain ───────────────────────────────────────────────
        try:
            chain = await fetch_chain_nse(symbol)
        except Exception as e:
            logger.warning(f"[DERI] Chain fetch failed for {symbol}: {e}")
            return

        if chain is None or chain.spot <= 0:
            logger.warning(f"[DERI] Empty chain for {symbol}")
            return

        self._last_chain = chain

        # ── T in years ───────────────────────────────────────────────────────
        T = max(dte / 365.0, 1 / 365.0)

        # ── Select strategy ───────────────────────────────────────────────────
        signal = select_strategy(chain, T, regime, india_vix, chain.pcr)
        if signal is None:
            logger.info(f"[DERI] {symbol}: No qualifying strategy this cycle")
            return

        # ── Risk check ────────────────────────────────────────────────────────
        rc = self.risk.check_entry(signal, tdte, india_vix)
        if not rc.passed:
            logger.info(f"[DERI] {symbol}: Risk check failed — {rc.reason}")
            await self._log_event(symbol, "entry_blocked", rc.reason, signal)
            return

        # ── Place legs ────────────────────────────────────────────────────────
        logger.info(
            f"[DERI] ENTERING: {signal.name} on {symbol} | "
            f"credit=₹{signal.net_premium:.0f} | confidence={signal.confidence:.2f}"
        )
        success = await self._place_legs(symbol, signal, chain)
        if not success:
            return

        # Register with risk manager
        expiry_str = chain.expiry
        ts = datetime.now(timezone.utc).isoformat()
        open_strat = self.risk.add_strategy(signal, symbol, expiry_str, ts)

        await self._log_event(symbol, "entry", signal.rationale, signal)
        await self._persist_strategy(open_strat, signal)

        self.broadcast({
            "type": "derivatives_entry",
            "symbol": symbol,
            "strategy": signal.name,
            "net_premium": signal.net_premium,
            "confidence": signal.confidence,
            "rationale": signal.rationale,
            "ts": ts,
        })

    # ── Monitor & exit open strategies ───────────────────────────────────────

    async def _monitor_open_strategies(self):
        active = [s for s in self.risk.open_strategies if not s.stopped]
        if not active:
            return

        for strat in active:
            # Fetch current exit cost from chain
            try:
                chain = await fetch_chain_nse(strat.symbol)
                if chain:
                    exit_cost = self._calc_exit_cost(strat, chain)
                    idx = self.risk.open_strategies.index(strat)
                    self.risk.update_pnl(idx, exit_cost)
            except Exception as e:
                logger.warning(f"[DERI] MTM update failed for {strat.strategy_name}: {e}")
                continue

            # Check stop-out
            stop_rc = self.risk.check_stop_out(strat)
            if stop_rc.passed:
                logger.warning(f"[DERI] STOP-OUT: {strat.strategy_name} — {stop_rc.reason}")
                await self._close_strategy(strat, stop_rc.reason)
                continue

            # Check profit target
            profit_rc = self.risk.check_profit_target(strat)
            if profit_rc.passed:
                logger.info(f"[DERI] TAKE PROFIT: {strat.strategy_name} — {profit_rc.reason}")
                await self._close_strategy(strat, profit_rc.reason)

    def _calc_exit_cost(self, strat: OpenStrategy, chain: OptionChain) -> float:
        """Current cost to close all legs (₹)."""
        total = 0.0
        for leg in strat.legs:
            chain_leg = chain.get_leg(leg.strike, leg.option_type)
            ltp = chain_leg.ltp if chain_leg else leg.ltp  # fallback to entry
            # If we sold → buy to close (cost); if we bought → sell to close (credit)
            multiplier = 1 if leg.side == "sell" else -1
            total += multiplier * ltp * leg.lot_size
        return total

    # ── Order placement ───────────────────────────────────────────────────────

    async def _place_legs(self, symbol: str, signal: StrategySignal, chain: OptionChain) -> bool:
        """Place all legs of the strategy via Kite. Returns True if all filled."""
        for leg in signal.legs:
            ts = _build_tradingsymbol(symbol, chain.expiry, leg.strike, leg.option_type)
            leg.tradingsymbol = ts

            try:
                transaction = "BUY" if leg.side == "buy" else "SELL"
                order = await self.kite.place_option_order(
                    symbol=ts,
                    side=transaction,
                    qty=leg.lot_size,
                    exchange="NFO",
                    product="MIS",        # intraday — no overnight F&O risk for now
                )
                logger.info(f"[DERI] Order: {transaction} {ts} x{leg.lot_size} → {order}")
            except Exception as e:
                logger.error(f"[DERI] Order failed for {ts}: {e}")
                return False   # abort strategy on any leg failure

        return True

    async def _close_strategy(self, strat: OpenStrategy, reason: str):
        """Close all legs of an open strategy."""
        for leg in strat.legs:
            try:
                close_side = "sell" if leg.side == "buy" else "buy"
                await self.kite.place_option_order(
                    symbol=leg.tradingsymbol or leg.option_type,
                    side=close_side.upper(),
                    qty=leg.lot_size,
                    exchange="NFO",
                    product="MIS",
                )
                logger.info(f"[DERI] Closed leg: {close_side} {leg.tradingsymbol}")
            except Exception as e:
                logger.error(f"[DERI] Close leg failed: {e}")

        self.risk.mark_stopped(strat, reason)
        pnl = strat.current_pnl
        await self._log_event(strat.symbol, "exit", reason, None, pnl=pnl)

        self.broadcast({
            "type": "derivatives_exit",
            "symbol": strat.symbol,
            "strategy": strat.strategy_name,
            "pnl": pnl,
            "reason": reason,
            "ts": datetime.now(timezone.utc).isoformat(),
        })

    async def _close_all_strategies(self, reason: str):
        active = [s for s in self.risk.open_strategies if not s.stopped]
        for strat in active:
            await self._close_strategy(strat, reason)

    # ── Persistence ───────────────────────────────────────────────────────────

    async def _log_event(self, symbol: str, event_type: str, detail: str,
                          signal: Optional[StrategySignal], pnl: float = 0.0):
        try:
            doc = {
                "ts":        datetime.now(timezone.utc).isoformat(),
                "symbol":    symbol,
                "type":      event_type,
                "detail":    detail,
                "pnl":       pnl,
                "strategy":  signal.name if signal else "",
                "confidence": signal.confidence if signal else 0.0,
            }
            await self.db.derivatives_events.insert_one(doc)
        except Exception as e:
            logger.warning(f"[DERI] Event log failed: {e}")

    async def _persist_strategy(self, open_strat: OpenStrategy, signal: StrategySignal):
        try:
            doc = {
                "ts":            open_strat.entry_ts,
                "symbol":        open_strat.symbol,
                "strategy_name": open_strat.strategy_name,
                "entry_credit":  open_strat.entry_credit,
                "max_loss":      open_strat.max_loss,
                "expiry":        open_strat.expiry,
                "net_delta":     open_strat.net_delta,
                "net_theta":     open_strat.net_theta,
                "net_vega":      open_strat.net_vega,
                "rationale":     signal.rationale,
                "legs": [
                    {
                        "option_type": l.option_type, "strike": l.strike,
                        "side": l.side, "ltp": l.ltp, "lot_size": l.lot_size,
                        "tradingsymbol": l.tradingsymbol,
                    }
                    for l in signal.legs
                ],
                "stopped":       False,
                "current_pnl":   0.0,
            }
            await self.db.derivatives_positions.insert_one(doc)
        except Exception as e:
            logger.warning(f"[DERI] Persist strategy failed: {e}")

    # ── Broadcast ─────────────────────────────────────────────────────────────

    def _broadcast_status(self):
        summary = self.risk.get_portfolio_summary()
        self.broadcast({
            "type": "derivatives_status",
            "loop_count": self._loop_count,
            "is_running": self._running,
            **summary,
        })

    # ── Public stats ─────────────────────────────────────────────────────────

    def get_status(self) -> dict:
        summary = self.risk.get_portfolio_summary()
        chain_info = {}
        if self._last_chain:
            chain_info = {
                "symbol": self._last_chain.symbol,
                "spot":   self._last_chain.spot,
                "atm":    self._last_chain.atm_strike,
                "pcr":    self._last_chain.pcr,
                "expiry": self._last_chain.expiry,
            }
        return {
            "loop_count":    self._loop_count,
            "is_running":    self._running,
            "last_chain":    chain_info,
            **summary,
        }


# ── Helpers ───────────────────────────────────────────────────────────────────

def _build_tradingsymbol(symbol: str, expiry_str: str, strike: float, option_type: str) -> str:
    """Build Kite tradingsymbol. e.g., NIFTY27MAR2522500CE.

    expiry_str from NSE: '27-Mar-2025' → parse to '27MAR25'
    """
    # NSE expiry format: '27-Mar-2025' or '27 Mar 2025'
    try:
        from datetime import datetime as _dt
        for fmt in ("%d-%b-%Y", "%d %b %Y", "%d%b%Y"):
            try:
                d = _dt.strptime(expiry_str, fmt)
                expiry_kite = d.strftime("%d%b%y").upper()
                break
            except ValueError:
                continue
        else:
            expiry_kite = expiry_str.upper().replace("-", "").replace(" ", "")[:7]
    except Exception:
        expiry_kite = expiry_str.upper()[:7]

    strike_str = str(int(strike)) if strike == int(strike) else str(strike)
    return f"{symbol.upper()}{expiry_kite}{strike_str}{option_type}"
