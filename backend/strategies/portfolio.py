"""Virtual capital allocator — multi-strategy portfolio manager.

Tracks strategy-level P&L independently of the broker's consolidated
account. Each StrategyAccount gets a virtual capital slice; lot sizing
is computed against that slice, not total broker funds.

Risk guards:
  - Global: halt all strategies if total daily loss > global_drawdown_pct
  - Per-strategy: pause if strategy equity drawdown > strategy_drawdown_pct
  - Per-trade: max loss capped at max_rupee_loss (handled by strategy engine)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, date
from typing import Optional
from uuid import uuid4

logger = logging.getLogger("moonshotx.strategies.portfolio")

NIFTY_LOT_SIZE     = 25
DEFAULT_MARGIN      = 20_000.0    # ₹ per lot (conservative estimate)
MAX_RUPEE_LOSS      = 3_000.0     # ₹ per trade
GLOBAL_DD_PCT       = 0.05        # 5% of total capital → halt all
STRATEGY_DD_PCT     = 0.10        # 10% of strategy capital → pause strategy
RISK_FRACTION       = 0.10        # use at most 10% of strategy equity per trade


@dataclass
class TradeRecord:
    trade_id: str
    strategy_id: str
    timestamp: datetime
    direction: str
    short_strike: int
    long_strike: int
    opt_type: str
    lots: int
    entry_credit: float            # ₹ received per lot
    realized_pnl: float = 0.0
    status: str = "open"           # open | closed | stopped
    exit_timestamp: Optional[datetime] = None
    short_security_id: Optional[str] = None
    long_security_id: Optional[str] = None
    expiry: Optional[str] = None


@dataclass
class StrategyAccount:
    name: str
    capital_allocated: float
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0
    trades: list = field(default_factory=list)
    paused: bool = False
    _peak_equity: float = 0.0

    def __post_init__(self):
        self._peak_equity = self.capital_allocated

    @property
    def equity(self) -> float:
        return self.capital_allocated + self.realized_pnl + self.unrealized_pnl

    @property
    def drawdown_pct(self) -> float:
        if self._peak_equity <= 0:
            return 0.0
        return max(0.0, (self._peak_equity - self.equity) / self._peak_equity)

    def update_peak(self):
        if self.equity > self._peak_equity:
            self._peak_equity = self.equity

    def max_lots(
        self,
        margin_per_lot: float = DEFAULT_MARGIN,
        risk_fraction: float = RISK_FRACTION,
    ) -> int:
        usable = self.equity * risk_fraction
        return max(0, int(usable / max(1, margin_per_lot)))

    def record_trade(self, record: TradeRecord):
        self.trades.append(record)
        logger.info(
            "[PORTFOLIO] %s: new trade %s %s %s x%d lots",
            self.name, record.trade_id, record.direction,
            record.opt_type, record.lots,
        )

    def close_trade(self, trade_id: str, pnl: float, ts: Optional[datetime] = None):
        for t in self.trades:
            if t.trade_id == trade_id:
                t.realized_pnl = pnl
                t.status = "closed"
                t.exit_timestamp = ts or datetime.utcnow()
                self.realized_pnl += pnl
                self.unrealized_pnl = max(0.0, self.unrealized_pnl - t.entry_credit * t.lots)
                self.update_peak()
                logger.info("[PORTFOLIO] %s: trade %s closed, P&L=₹%.0f", self.name, trade_id, pnl)
                return
        logger.warning("[PORTFOLIO] %s: trade_id %s not found to close", self.name, trade_id)

    def open_trades(self) -> list[TradeRecord]:
        return [t for t in self.trades if t.status == "open"]

    def today_pnl(self) -> float:
        today = date.today()
        return sum(
            t.realized_pnl for t in self.trades
            if t.status == "closed" and t.exit_timestamp
            and t.exit_timestamp.date() == today
        )


class PortfolioManager:
    """Manages multiple StrategyAccounts and enforces global risk guards."""

    def __init__(
        self,
        total_capital: float,
        allocations: dict,       # {"strategy_name": fraction, ...}  fractions must sum to 1.0
        global_drawdown_pct: float = GLOBAL_DD_PCT,
        strategy_drawdown_pct: float = STRATEGY_DD_PCT,
    ):
        self.total_capital = total_capital
        self.global_drawdown_pct = global_drawdown_pct
        self.strategy_drawdown_pct = strategy_drawdown_pct
        self.halted = False

        self.accounts: dict[str, StrategyAccount] = {}
        for name, frac in allocations.items():
            alloc = round(total_capital * frac, 2)
            self.accounts[name] = StrategyAccount(name=name, capital_allocated=alloc)
            logger.info("[PORTFOLIO] %s allocated ₹%.0f (%.0f%%)", name, alloc, frac * 100)

    # ── Risk checks ──────────────────────────────────────────────────────

    def check_global_risk(self) -> bool:
        """Returns True if trading should continue."""
        if self.halted:
            return False
        total_pnl = sum(a.realized_pnl + a.unrealized_pnl for a in self.accounts.values())
        total_drawdown = -total_pnl / max(self.total_capital, 1)
        if total_drawdown >= self.global_drawdown_pct:
            logger.warning(
                "[PORTFOLIO] GLOBAL HALT: drawdown %.1f%% >= limit %.1f%%",
                total_drawdown * 100, self.global_drawdown_pct * 100,
            )
            self.halted = True
            return False
        return True

    def check_strategy_risk(self, name: str) -> bool:
        """Returns True if this specific strategy can still trade."""
        if self.halted:
            return False
        acc = self.accounts.get(name)
        if not acc:
            return False
        if acc.paused:
            return False
        if acc.drawdown_pct >= self.strategy_drawdown_pct:
            logger.warning(
                "[PORTFOLIO] STRATEGY PAUSED: %s drawdown %.1f%% >= limit %.1f%%",
                name, acc.drawdown_pct * 100, self.strategy_drawdown_pct * 100,
            )
            acc.paused = True
            return False
        return True

    # ── Trade lifecycle ──────────────────────────────────────────────────

    def request_trade(
        self,
        strategy_name: str,
        direction: str,
        short_strike: int,
        long_strike: int,
        opt_type: str,
        entry_credit: float,
        margin_per_lot: float = DEFAULT_MARGIN,
        expiry: Optional[str] = None,
        short_security_id: Optional[str] = None,
        long_security_id: Optional[str] = None,
    ) -> Optional[TradeRecord]:
        """
        Validate risk limits and create a TradeRecord if approved.
        Returns the record (caller must then route to broker), or None if rejected.
        """
        if not self.check_global_risk():
            logger.info("[PORTFOLIO] Trade rejected: global halt active")
            return None
        if not self.check_strategy_risk(strategy_name):
            logger.info("[PORTFOLIO] Trade rejected: strategy %s paused/halted", strategy_name)
            return None

        acc = self.accounts[strategy_name]
        if acc.open_trades():
            logger.info("[PORTFOLIO] %s: already has open trade — skip", strategy_name)
            return None

        lots = acc.max_lots(margin_per_lot)
        if lots < 1:
            logger.info("[PORTFOLIO] %s: insufficient capital for even 1 lot", strategy_name)
            return None

        record = TradeRecord(
            trade_id=str(uuid4())[:8],
            strategy_id=strategy_name,
            timestamp=datetime.utcnow(),
            direction=direction,
            short_strike=short_strike,
            long_strike=long_strike,
            opt_type=opt_type,
            lots=lots,
            entry_credit=entry_credit,
            short_security_id=short_security_id,
            long_security_id=long_security_id,
            expiry=expiry,
        )
        acc.unrealized_pnl += entry_credit * lots
        acc.record_trade(record)
        return record

    def close_trade(
        self, strategy_name: str, trade_id: str, exit_pnl: float,
        ts: Optional[datetime] = None,
    ):
        acc = self.accounts.get(strategy_name)
        if acc:
            acc.close_trade(trade_id, exit_pnl, ts)

    # ── Reporting ────────────────────────────────────────────────────────

    def summary(self) -> dict:
        total_pnl = sum(a.realized_pnl for a in self.accounts.values())
        return {
            "total_capital": self.total_capital,
            "total_pnl": round(total_pnl, 2),
            "halted": self.halted,
            "strategies": {
                name: {
                    "capital": round(acc.capital_allocated, 2),
                    "equity": round(acc.equity, 2),
                    "realized_pnl": round(acc.realized_pnl, 2),
                    "unrealized_pnl": round(acc.unrealized_pnl, 2),
                    "drawdown_pct": round(acc.drawdown_pct * 100, 2),
                    "open_trades": len(acc.open_trades()),
                    "total_trades": len(acc.trades),
                    "paused": acc.paused,
                    "today_pnl": round(acc.today_pnl(), 2),
                }
                for name, acc in self.accounts.items()
            },
        }
