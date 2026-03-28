"""Derivatives risk manager — Greeks-based portfolio limits for NSE F&O.

Hard limits (per-portfolio):
  Max net delta        : ±50 (equiv. to ~±50 NIFTY index points exposure)
  Max net vega         : -₹5,000 (don't be too short vega in panic)
  Max net theta collect: +₹2,000/day (don't over-sell premium)
  Max open strategies  : 2 concurrent (NIFTY + BANKNIFTY)
  Max capital at risk  : 5% of portfolio per strategy (defined-risk legs)
  Max loss per strategy: ₹10,000 per lot before stop-out
"""
import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger("moonshotx.derivatives.risk")

# ── Hard limits ───────────────────────────────────────────────────────────────
MAX_NET_DELTA        = 50.0      # absolute net portfolio delta
MAX_NET_VEGA         = -5000.0  # floor on net vega (too negative = vega short)
MAX_THETA_PER_DAY    = 2000.0   # cap daily theta collection (₹)
MAX_OPEN_STRATEGIES  = 2        # max concurrent live strategies
MAX_LOSS_PER_STRATEGY = 10000.0 # ₹ stop-out per strategy
CAPITAL_RISK_PCT     = 0.05     # max 5% of portfolio at risk per strategy
MIN_DTE              = 1        # don't enter within 1 day of expiry (gamma risk)
MAX_DTE              = 5        # don't enter more than 5 days from expiry


@dataclass
class RiskCheck:
    passed: bool
    reason: str = ""


@dataclass
class OpenStrategy:
    strategy_name: str
    symbol: str
    entry_credit: float          # net credit received (₹)
    max_loss: float              # ₹ (positive number)
    current_pnl: float = 0.0    # mark-to-market P&L (₹)
    legs: list = field(default_factory=list)
    entry_ts: str = ""
    expiry: str = ""
    net_delta: float = 0.0
    net_theta: float = 0.0
    net_vega: float = 0.0
    stopped: bool = False


class DerivativesRiskManager:
    def __init__(self, portfolio_value: float = 500_000):
        self.portfolio_value = portfolio_value
        self.open_strategies: list[OpenStrategy] = []

    def update_portfolio_value(self, value: float):
        self.portfolio_value = value

    # ── Pre-entry checks ──────────────────────────────────────────────────────

    def check_entry(
        self,
        strategy,          # StrategySignal
        dte: int,          # days to expiry
        india_vix: float,
    ) -> RiskCheck:
        """Run all pre-trade risk checks before entering a strategy."""

        # 1. DTE window
        if dte < MIN_DTE:
            return RiskCheck(False, f"DTE {dte} < {MIN_DTE} — too close to expiry (gamma risk)")
        if dte > MAX_DTE:
            return RiskCheck(False, f"DTE {dte} > {MAX_DTE} — too far from expiry (enter closer)")

        # 2. Max concurrent strategies
        active = [s for s in self.open_strategies if not s.stopped]
        if len(active) >= MAX_OPEN_STRATEGIES:
            return RiskCheck(False, f"Max open strategies ({MAX_OPEN_STRATEGIES}) reached")

        # 3. Capital at risk
        if strategy.max_loss != float("inf"):
            capital_at_risk = strategy.max_loss
            max_allowed = self.portfolio_value * CAPITAL_RISK_PCT
            if capital_at_risk > max_allowed:
                return RiskCheck(
                    False,
                    f"Capital at risk ₹{capital_at_risk:.0f} > {CAPITAL_RISK_PCT*100:.0f}% "
                    f"of portfolio (₹{max_allowed:.0f})"
                )

        # 4. Portfolio Greeks after adding this strategy
        current_delta = sum(s.net_delta for s in active)
        current_vega  = sum(s.net_vega  for s in active)
        current_theta = sum(s.net_theta for s in active)

        new_delta = current_delta + strategy.net_delta
        new_vega  = current_vega  + strategy.net_vega
        new_theta = current_theta + strategy.net_theta

        if abs(new_delta) > MAX_NET_DELTA:
            return RiskCheck(False, f"Portfolio delta would be {new_delta:.1f} > limit ±{MAX_NET_DELTA}")
        if new_vega < MAX_NET_VEGA:
            return RiskCheck(False, f"Portfolio vega would be {new_vega:.0f} < floor {MAX_NET_VEGA:.0f}")
        if new_theta > MAX_THETA_PER_DAY:
            return RiskCheck(False, f"Portfolio theta would be +₹{new_theta:.0f}/day — over-selling premium")

        # 5. Don't sell premium when IV is very low (not worth the risk)
        if strategy.name in ("iron_condor", "short_strangle") and india_vix < 12:
            return RiskCheck(False, f"India VIX {india_vix:.1f} too low to sell premium profitably")

        return RiskCheck(True, "All risk checks passed")

    # ── Mark-to-market & stop-out ─────────────────────────────────────────────

    def update_pnl(self, strategy_idx: int, current_value: float):
        """Update mark-to-market P&L for a strategy.

        current_value: current cost to close all legs (₹) — negative credit strategies
                       will have positive P&L when this decreases.
        """
        if strategy_idx >= len(self.open_strategies):
            return
        s = self.open_strategies[strategy_idx]
        # For credit strategies: P&L = entry_credit - current_exit_cost
        s.current_pnl = s.entry_credit - current_value

    def check_stop_out(self, strategy: OpenStrategy) -> RiskCheck:
        """Return True if strategy should be stopped out."""
        if strategy.stopped:
            return RiskCheck(False, "Already stopped")

        loss = -strategy.current_pnl   # positive loss
        if loss >= MAX_LOSS_PER_STRATEGY:
            return RiskCheck(True, f"Stop-out: loss ₹{loss:.0f} >= ₹{MAX_LOSS_PER_STRATEGY:.0f}")

        # Also stop if loss exceeds 2× entry credit (for credit strategies)
        if strategy.entry_credit > 0 and loss >= 2 * strategy.entry_credit:
            return RiskCheck(True, f"Stop-out: loss {loss:.0f} > 2× entry credit {strategy.entry_credit:.0f}")

        return RiskCheck(False, "Within stop-loss limits")

    def check_profit_target(self, strategy: OpenStrategy) -> RiskCheck:
        """Take profit at 50% of max profit for credit strategies."""
        if strategy.entry_credit <= 0:
            return RiskCheck(False, "Debit strategy — no credit profit target")
        target = strategy.entry_credit * 0.5
        if strategy.current_pnl >= target:
            return RiskCheck(True, f"Profit target hit: +₹{strategy.current_pnl:.0f} >= 50% of ₹{strategy.entry_credit:.0f}")
        return RiskCheck(False, f"P&L ₹{strategy.current_pnl:.0f}, target ₹{target:.0f}")

    # ── Strategy lifecycle ────────────────────────────────────────────────────

    def add_strategy(self, strategy, symbol: str, expiry: str, entry_ts: str) -> OpenStrategy:
        """Register a new strategy as open after entry orders are filled."""
        s = OpenStrategy(
            strategy_name=strategy.name,
            symbol=symbol,
            entry_credit=strategy.net_premium,
            max_loss=strategy.max_loss if strategy.max_loss != float("inf") else MAX_LOSS_PER_STRATEGY,
            legs=strategy.legs,
            entry_ts=entry_ts,
            expiry=expiry,
            net_delta=strategy.net_delta,
            net_theta=strategy.net_theta,
            net_vega=strategy.net_vega,
        )
        self.open_strategies.append(s)
        logger.info(
            f"[DERI_RISK] Registered: {strategy.name} on {symbol} | "
            f"credit=₹{strategy.net_premium:.0f} max_loss=₹{s.max_loss:.0f}"
        )
        return s

    def mark_stopped(self, strategy: OpenStrategy, reason: str):
        strategy.stopped = True
        logger.info(f"[DERI_RISK] Strategy stopped: {strategy.strategy_name} — {reason}")

    def get_portfolio_summary(self) -> dict:
        active = [s for s in self.open_strategies if not s.stopped]
        return {
            "open_strategies": len(active),
            "net_delta": round(sum(s.net_delta for s in active), 3),
            "net_theta": round(sum(s.net_theta for s in active), 2),
            "net_vega":  round(sum(s.net_vega  for s in active), 2),
            "total_pnl": round(sum(s.current_pnl for s in active), 2),
            "strategies": [
                {
                    "name":   s.strategy_name,
                    "symbol": s.symbol,
                    "entry_credit": s.entry_credit,
                    "current_pnl":  s.current_pnl,
                    "max_loss":     s.max_loss,
                    "stopped":      s.stopped,
                    "expiry":       s.expiry,
                }
                for s in self.open_strategies
            ],
        }
