"""Risk management — portfolio-scaled, regime-adaptive position & trade limits."""
import logging
import math
from datetime import datetime, timezone, timedelta
from typing import Optional
from motor.motor_asyncio import AsyncIOMotorDatabase

logger = logging.getLogger("moonshotx.risk")

# ── Regime profiles ───────────────────────────────────────────────────────────
# Each regime defines:
#   size_mult        — multiplier on per-trade risk allocation
#   target_pos_pct   — target % of portfolio per position (drives max positions)
#   max_pos_cap      — absolute ceiling regardless of portfolio size
#   min_pos_floor    — minimum positions allowed (if regime trades at all)
#   scan_mult        — how many candidates to scan relative to max positions
#   allow_longs      — whether to open new long positions
#   daily_trade_mult — multiplier on base daily trade limit
REGIME_PROFILES = {
    "bull":          {"size_mult": 1.25, "target_pos_pct": 0.05, "max_pos_cap": 20, "min_pos_floor": 6,  "scan_mult": 2.0, "allow_longs": True,  "daily_trade_mult": 1.5, "max_new_per_loop": 3},
    "neutral":       {"size_mult": 1.00, "target_pos_pct": 0.07, "max_pos_cap": 12, "min_pos_floor": 4,  "scan_mult": 2.0, "allow_longs": True,  "daily_trade_mult": 1.2, "max_new_per_loop": 2},
    "fear":          {"size_mult": 0.70, "target_pos_pct": 0.10, "max_pos_cap": 6,  "min_pos_floor": 2,  "scan_mult": 1.5, "allow_longs": True,  "daily_trade_mult": 0.8, "max_new_per_loop": 2},
    "choppy":        {"size_mult": 0.40, "target_pos_pct": 0.15, "max_pos_cap": 5,  "min_pos_floor": 1,  "scan_mult": 1.5, "allow_longs": True,  "daily_trade_mult": 0.6, "max_new_per_loop": 1},
    "bear_mode":     {"size_mult": 0.30, "target_pos_pct": 0.20, "max_pos_cap": 3,  "min_pos_floor": 0,  "scan_mult": 1.0, "allow_longs": False, "daily_trade_mult": 0.5, "max_new_per_loop": 0},
    "extreme_fear":  {"size_mult": 0.00, "target_pos_pct": 1.00, "max_pos_cap": 0,  "min_pos_floor": 0,  "scan_mult": 0.0, "allow_longs": False, "daily_trade_mult": 0.0, "max_new_per_loop": 0},
}

# ── Base guardrails (scale with portfolio) ────────────────────────────────────
BASE_CONFIG = {
    "max_daily_loss_pct": 0.03,        # 3% daily stop
    "max_drawdown_pct": 0.15,          # 15% total drawdown kill switch
    "base_daily_trades": 25,           # base trades/day (scaled by regime)
    "risk_per_trade_pct": 0.012,       # 1.2% risk per trade (base)
    "consecutive_loss_pause": 4,       # pause after N consecutive losses
    "pause_minutes": 20,               # pause duration
    "min_position_value": 500,         # never open a position smaller than this
    "bayesian_threshold": 0.45,
}


def _get_profile(regime: str) -> dict:
    return REGIME_PROFILES.get(regime, REGIME_PROFILES["neutral"])


class RiskManager:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db
        self.daily_trades = 0
        self.daily_pnl = 0.0
        self.initial_capital = 0.0
        self.current_capital = 0.0
        self.consecutive_losses = 0
        self.pause_until: Optional[datetime] = None
        self._date = datetime.now(timezone.utc).date()

    def _reset_daily(self):
        today = datetime.now(timezone.utc).date()
        if today != self._date:
            self.daily_trades = 0
            self.daily_pnl = 0.0
            self.consecutive_losses = 0
            self.pause_until = None
            self._date = today

    # ── Dynamic limits ────────────────────────────────────────────────────

    def max_positions(self, regime: str, portfolio_value: float = 0) -> int:
        """Dynamic max positions = portfolio_value / target_position_value, clamped by regime caps."""
        pv = portfolio_value or self.current_capital or 50_000
        p = _get_profile(regime)
        if p["size_mult"] == 0.0:
            return 0
        raw = int(pv * (1.0 / max(pv * p["target_pos_pct"], 1)))
        return max(p["min_pos_floor"], min(raw, p["max_pos_cap"]))

    def max_daily_trades(self, regime: str) -> int:
        p = _get_profile(regime)
        return max(5, int(BASE_CONFIG["base_daily_trades"] * p["daily_trade_mult"]))

    def candidates_to_scan(self, regime: str, portfolio_value: float = 0) -> int:
        """How many candidates the scanner should evaluate per cycle."""
        p = _get_profile(regime)
        max_pos = self.max_positions(regime, portfolio_value)
        return max(5, int(max_pos * p["scan_mult"]))

    # ── Trade gates ───────────────────────────────────────────────────────

    def can_trade(self, regime: str) -> tuple[bool, str]:
        self._reset_daily()

        if self.pause_until and datetime.now(timezone.utc) < self.pause_until:
            return False, f"Consecutive loss pause until {self.pause_until.strftime('%H:%M')}"

        max_trades = self.max_daily_trades(regime)
        if self.daily_trades >= max_trades:
            return False, f"Max daily trades reached ({max_trades})"

        if self.current_capital > 0 and self.initial_capital > 0:
            daily_loss_pct = self.daily_pnl / self.initial_capital
            if daily_loss_pct <= -BASE_CONFIG["max_daily_loss_pct"]:
                return False, f"Daily loss limit hit ({daily_loss_pct:.1%})"

            total_drawdown = (self.current_capital - self.initial_capital) / self.initial_capital
            if total_drawdown <= -BASE_CONFIG["max_drawdown_pct"]:
                return False, f"Max drawdown exceeded ({total_drawdown:.1%})"

        p = _get_profile(regime)
        if not p["allow_longs"]:
            return False, f"Longs blocked in {regime} regime"

        return True, "ok"

    def can_add_position(self, regime: str, open_count: int, portfolio_value: float = 0) -> bool:
        return open_count < self.max_positions(regime, portfolio_value)

    def max_new_per_loop(self, regime: str) -> int:
        """Max new entries allowed per single loop cycle."""
        return _get_profile(regime).get("max_new_per_loop", 2)

    # ── Position sizing (portfolio-scaled) ────────────────────────────────

    def calculate_position_size(
        self, portfolio_value: float, entry_price: float, stop_price: float, regime: str, confidence: float = 0.7
    ) -> int:
        """Confidence-scaled position sizing.
        
        Conviction tiers (the primary sizing driver):
          >= 0.90  →  2.0x  (very high conviction = scale up aggressively)
          >= 0.80  →  1.5x  (high conviction = larger position)
          >= 0.70  →  1.0x  (moderate = standard size)
          >= 0.60  →  0.6x  (low-moderate = reduced)
          <  0.60  →  0.3x  (low conviction = minimal skin)
        """
        p = _get_profile(regime)
        if p["size_mult"] == 0.0 or entry_price <= 0 or stop_price <= 0:
            return 0

        # Conviction-tiered multiplier (primary sizing driver)
        if confidence >= 0.90:
            conf_mult = 2.0
        elif confidence >= 0.80:
            conf_mult = 1.5
        elif confidence >= 0.70:
            conf_mult = 1.0
        elif confidence >= 0.60:
            conf_mult = 0.6
        else:
            conf_mult = 0.3

        risk_pct = BASE_CONFIG["risk_per_trade_pct"] * p["size_mult"] * conf_mult
        dollar_risk = portfolio_value * risk_pct
        stop_distance = abs(entry_price - stop_price) / entry_price

        if stop_distance < 0.005:
            stop_distance = 0.05   # floor at 5% if stop is suspiciously tight

        raw_value = dollar_risk / stop_distance
        # Confidence also lifts the position cap for high-conviction picks
        max_pct = p["target_pos_pct"] * (1.0 + max(0, confidence - 0.7))  # e.g. 0.85 conv → +15% cap
        max_value = portfolio_value * min(max_pct, 0.20)                  # absolute ceiling: 20% of portfolio
        position_value = min(raw_value, max_value)

        if position_value < BASE_CONFIG["min_position_value"]:
            return 0

        shares = int(position_value / entry_price)
        logger.info(f"Size calc: conv={confidence:.2f} conf_mult={conf_mult}x regime_mult={p['size_mult']}x → ${position_value:,.0f} = {shares} shares @ ${entry_price:.2f}")
        return max(1, shares)

    # ── Recording ─────────────────────────────────────────────────────────

    def record_trade(self, pnl: float):
        self._reset_daily()
        self.daily_trades += 1
        self.daily_pnl += pnl
        if pnl < 0:
            self.consecutive_losses += 1
            if self.consecutive_losses >= BASE_CONFIG["consecutive_loss_pause"]:
                self.pause_until = datetime.now(timezone.utc) + timedelta(minutes=BASE_CONFIG["pause_minutes"])
                logger.warning(f"{self.consecutive_losses} consecutive losses — pausing for {BASE_CONFIG['pause_minutes']} min")
        else:
            self.consecutive_losses = 0

    def set_capital(self, initial: float, current: float):
        self.initial_capital = initial
        self.current_capital = current

    def get_stats(self) -> dict:
        regime = "neutral"  # default for stats
        return {
            "daily_trades": self.daily_trades,
            "daily_pnl": round(self.daily_pnl, 2),
            "consecutive_losses": self.consecutive_losses,
            "pause_until": self.pause_until.isoformat() if self.pause_until else None,
            "max_daily_trades": self.max_daily_trades(regime),
            "portfolio_value": round(self.current_capital, 2),
        }
