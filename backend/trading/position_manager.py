"""Position manager — trailing stops, partial profits, stop tightening, stale exits."""
import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional

logger = logging.getLogger("moonshotx.position_mgr")

# ── Trailing stop config ─────────────────────────────────────────────────────
# Once a position is up >= TRAIL_ACTIVATE_PCT, start trailing the stop
# at TRAIL_DISTANCE_PCT below the high watermark.
TRAIL_ACTIVATE_PCT = 0.03        # activate trailing once +3% from entry
TRAIL_DISTANCE_PCT = 0.025       # trail 2.5% below high watermark

# ── Breakeven stop config ────────────────────────────────────────────────────
BREAKEVEN_ACTIVATE_PCT = 0.02    # move stop to breakeven once +2%
BREAKEVEN_BUFFER_PCT = 0.003     # breakeven + 0.3% buffer (cover commissions)

# ── Partial profit config ────────────────────────────────────────────────────
# At each threshold, sell PARTIAL_PCT of remaining shares
PARTIAL_THRESHOLDS = [
    {"pct": 0.05, "sell_frac": 0.33},  # at +5%, sell 1/3
    {"pct": 0.10, "sell_frac": 0.33},  # at +10%, sell 1/3 of remaining
    {"pct": 0.20, "sell_frac": 0.50},  # at +20%, sell 1/2 of remaining
]

# ── Regime-adaptive max loss (hard stop) ──────────────────────────────────────
# In fear/choppy markets, cut losers much faster. "Ride waves, get off fast."
MAX_LOSS_BY_REGIME = {
    "bull":          0.06,   # 6% max loss
    "neutral":       0.04,   # 4% max loss
    "fear":          0.025,  # 2.5% max loss — CUT FAST in fear
    "choppy":        0.025,  # 2.5% max loss
    "bear_mode":     0.02,   # 2% max loss
    "extreme_fear":  0.015,  # 1.5% max loss
}
MAX_LOSS_PCT = 0.06  # fallback

# ── Quick reversal exit (thesis-is-wrong detector) ────────────────────────────
# If a trade drops this much within the first N minutes, the entry was bad — exit.
QUICK_REVERSAL_PCT = 0.015       # -1.5% drop
QUICK_REVERSAL_WINDOW_MIN = 30   # within first 30 minutes

# ── Momentum fade exit ────────────────────────────────────────────────────────
# If position is underwater after a reasonable holding period, momentum is gone.
MOMENTUM_FADE_LOSS_PCT = 0.01    # down >= 1% from entry
MOMENTUM_FADE_MIN_HOLD_MIN = 45  # after holding for 45+ minutes
MOMENTUM_FADE_HWM_DROP_PCT = 0.02  # AND price dropped 2%+ from high watermark

# ── Stale position config ────────────────────────────────────────────────────
STALE_HOURS_BY_REGIME = {
    "bull": 8, "neutral": 6, "fear": 3, "choppy": 3,
    "bear_mode": 2, "extreme_fear": 1,
}
STALE_HOURS = 8  # fallback
STALE_MOVE_PCT = 0.005           # "meaningful movement" = ±0.5%

# ── Regime downgrade exit config ─────────────────────────────────────────────
REGIME_SEVERITY = {
    "bull": 0, "neutral": 1, "fear": 2, "choppy": 3,
    "bear_mode": 4, "extreme_fear": 5,
}
REGIME_EXIT_JUMP = 3             # close positions if regime jumps >= 3 severity levels


# Re-entry cooldown: don't re-buy a stock we just dumped for losing money
REENTRY_COOLDOWN_MIN = 120  # 2-hour cooldown after a loss exit
LOSS_EXIT_REASONS = ("max_loss", "quick_reversal", "momentum_fade")


class PositionManager:
    """Manages open NSE positions: trailing stops, partial exits, stale detection, GTT cleanup."""

    def __init__(self, kite, db, broadcast_fn):
        self.kite = kite
        self.db = db
        self.broadcast = broadcast_fn
        # In-memory tracking: {symbol: {high_watermark, entry_price, entry_time, partials_taken, entry_regime, trailing_active, gtt_id}}
        self._tracking: Dict[str, dict] = {}
        # Cooldown tracking: {symbol: datetime} — persisted to DB so restarts don't wipe them
        self._cooldowns: Dict[str, datetime] = {}

    async def load_cooldowns(self):
        """Load active cooldowns from DB on startup (survives process restarts)."""
        now = datetime.now(timezone.utc)
        cutoff = now.timestamp() - (REENTRY_COOLDOWN_MIN * 60)
        docs = await self.db.cooldowns.find(
            {"expires_at": {"$gt": now.isoformat()}}, {"_id": 0}
        ).to_list(500)
        loaded = 0
        for doc in docs:
            sym = doc.get("symbol")
            ts_str = doc.get("closed_at")
            if sym and ts_str:
                try:
                    self._cooldowns[sym] = datetime.fromisoformat(ts_str)
                    loaded += 1
                except Exception:
                    pass
        if loaded:
            logger.info(f"[PM] Loaded {loaded} active cooldowns from DB")

    def is_in_cooldown(self, symbol: str) -> tuple[bool, str]:
        """Check if a ticker is in re-entry cooldown after a losing exit."""
        if symbol not in self._cooldowns:
            return False, ""
        closed_at = self._cooldowns[symbol]
        elapsed_min = (datetime.now(timezone.utc) - closed_at).total_seconds() / 60
        if elapsed_min < REENTRY_COOLDOWN_MIN:
            remaining = int(REENTRY_COOLDOWN_MIN - elapsed_min)
            return True, f"cooldown: {remaining}min remaining (closed for loss at {closed_at.strftime('%H:%M')})"
        del self._cooldowns[symbol]
        return False, ""

    async def manage_positions(self, regime: str):
        """Run every loop cycle — manage all open positions."""
        positions = await self.kite.get_positions()
        if not positions:
            return

        managed_symbols = set()
        actions_taken = []

        for pos in positions:
            symbol = pos.get("symbol", "")
            if not symbol:
                continue
            managed_symbols.add(symbol)

            current_price = float(pos.get("current_price", 0))
            entry_price = float(pos.get("avg_entry_price", 0))
            qty = int(pos.get("qty", 0))
            unrealized_pnl = float(pos.get("unrealized_pl", 0))

            if current_price <= 0 or entry_price <= 0 or qty <= 0:
                continue

            pnl_pct = (current_price - entry_price) / entry_price

            # Initialize tracking if new position
            if symbol not in self._tracking:
                self._tracking[symbol] = {
                    "high_watermark": current_price,
                    "entry_price": entry_price,
                    "entry_time": datetime.now(timezone.utc),
                    "partials_taken": [],
                    "entry_regime": regime,
                    "trailing_active": False,
                    "gtt_id": None,
                    "breakeven_set": False,
                }

            track = self._tracking[symbol]

            # Update high watermark
            if current_price > track["high_watermark"]:
                track["high_watermark"] = current_price

            hold_min = (datetime.now(timezone.utc) - track["entry_time"]).total_seconds() / 60
            hwm = track["high_watermark"]
            hwm_drop_pct = (hwm - current_price) / hwm if hwm > 0 else 0
            regime_max_loss = MAX_LOSS_BY_REGIME.get(regime, MAX_LOSS_PCT)
            regime_stale_hrs = STALE_HOURS_BY_REGIME.get(regime, STALE_HOURS)

            # ── 0. Hard stop (regime-adaptive max loss) ──────────────────
            if pnl_pct <= -regime_max_loss:
                logger.warning(f"[PM] {symbol}: MAX LOSS hit ({pnl_pct*100:.1f}% <= -{regime_max_loss*100:.1f}% for {regime}) — closing")
                await self._close_full_position(symbol, qty, f"max_loss:{pnl_pct*100:.1f}%")
                actions_taken.append({"symbol": symbol, "action": "max_loss", "pnl_pct": round(pnl_pct * 100, 2)})
                continue

            # ── 0b. Quick reversal exit (thesis-is-wrong) ────────────────
            if hold_min <= QUICK_REVERSAL_WINDOW_MIN and pnl_pct <= -QUICK_REVERSAL_PCT:
                logger.warning(f"[PM] {symbol}: QUICK REVERSAL ({pnl_pct*100:.1f}% in {hold_min:.0f}min) — thesis wrong, closing")
                await self._close_full_position(symbol, qty, f"quick_reversal:{pnl_pct*100:.1f}%@{hold_min:.0f}min")
                actions_taken.append({"symbol": symbol, "action": "quick_reversal", "pnl_pct": round(pnl_pct * 100, 2), "hold_min": round(hold_min)})
                continue

            # ── 0c. Momentum fade exit ───────────────────────────────────
            if (hold_min >= MOMENTUM_FADE_MIN_HOLD_MIN
                    and pnl_pct <= -MOMENTUM_FADE_LOSS_PCT
                    and hwm_drop_pct >= MOMENTUM_FADE_HWM_DROP_PCT):
                logger.warning(f"[PM] {symbol}: MOMENTUM FADE ({pnl_pct*100:.1f}%, hwm_drop={hwm_drop_pct*100:.1f}%, held {hold_min:.0f}min) — closing")
                await self._close_full_position(symbol, qty, f"momentum_fade:{pnl_pct*100:.1f}%")
                actions_taken.append({"symbol": symbol, "action": "momentum_fade", "pnl_pct": round(pnl_pct * 100, 2), "hwm_drop": round(hwm_drop_pct * 100, 2)})
                continue

            # ── 1. Regime downgrade check ─────────────────────────────────
            action = self._check_regime_exit(symbol, regime, track, pnl_pct)
            if action:
                logger.info(f"[PM] {symbol}: regime exit — {action}")
                await self._close_full_position(symbol, qty, f"regime_exit:{action}")
                actions_taken.append({"symbol": symbol, "action": "regime_exit", "detail": action})
                continue

            # ── 2. Trailing stop logic ────────────────────────────────────
            trail_action = await self._manage_trailing_stop(symbol, current_price, entry_price, qty, track, pnl_pct)
            if trail_action:
                actions_taken.append(trail_action)

            # ── 3. Breakeven stop ─────────────────────────────────────────
            if not track["breakeven_set"] and pnl_pct >= BREAKEVEN_ACTIVATE_PCT:
                be_action = await self._set_breakeven_stop(symbol, entry_price, qty, track)
                if be_action:
                    actions_taken.append(be_action)

            # ── 4. Partial profit taking ──────────────────────────────────
            partial_action = await self._check_partial_profit(symbol, current_price, entry_price, qty, track, pnl_pct)
            if partial_action:
                actions_taken.append(partial_action)

            # ── 5. Stale position check (regime-adaptive) ────────────────
            stale_action = self._check_stale(symbol, pnl_pct, track, regime_stale_hrs)
            if stale_action:
                logger.info(f"[PM] {symbol}: stale position ({regime_stale_hrs}h in {regime}) — closing")
                await self._close_full_position(symbol, qty, f"stale_exit:{regime}")
                actions_taken.append({"symbol": symbol, "action": "stale_exit", "pnl_pct": round(pnl_pct * 100, 2)})
                continue

        # Cleanup tracking for closed positions
        closed = set(self._tracking.keys()) - managed_symbols
        for sym in closed:
            del self._tracking[sym]

        if actions_taken:
            self.broadcast({
                "type": "position_management",
                "actions": actions_taken,
                "ts": datetime.now(timezone.utc).isoformat(),
            })

    def _check_regime_exit(self, symbol: str, current_regime: str, track: dict, pnl_pct: float) -> Optional[str]:
        """Close if regime jumped severely since entry."""
        entry_sev = REGIME_SEVERITY.get(track["entry_regime"], 1)
        current_sev = REGIME_SEVERITY.get(current_regime, 1)
        jump = current_sev - entry_sev
        if jump >= REGIME_EXIT_JUMP:
            return f"{track['entry_regime']}->{current_regime} (jump={jump})"
        return None

    async def _manage_trailing_stop(self, symbol: str, current_price: float, entry_price: float, qty: int, track: dict, pnl_pct: float) -> Optional[dict]:
        """Activate or update trailing stop via GTT replacement."""
        if pnl_pct < TRAIL_ACTIVATE_PCT:
            return None

        hwm = track["high_watermark"]
        trail_stop = round(hwm * (1 - TRAIL_DISTANCE_PCT), 2)
        trail_stop = max(trail_stop, round(entry_price * (1 + BREAKEVEN_BUFFER_PCT), 2))

        if not track["trailing_active"]:
            track["trailing_active"] = True
            await self._cancel_gtt(symbol, track)
            # Place a new GTT stop (no take-profit override — just trailing SL)
            gtt_id = await self._place_gtt_stop(symbol, qty, trail_stop)
            track["gtt_id"] = gtt_id
            logger.info(f"[PM] {symbol}: trailing stop activated @ ₹{trail_stop:.2f} (hwm=₹{hwm:.2f}, gtt={gtt_id})")
            return {"symbol": symbol, "action": "trail_activated", "stop": trail_stop, "hwm": hwm}
        else:
            # Cancel existing GTT and replace with updated stop
            await self._cancel_gtt(symbol, track)
            gtt_id = await self._place_gtt_stop(symbol, qty, trail_stop)
            track["gtt_id"] = gtt_id
            logger.info(f"[PM] {symbol}: trailing stop updated @ ₹{trail_stop:.2f} (hwm=₹{hwm:.2f}, gtt={gtt_id})")
            return {"symbol": symbol, "action": "trail_updated", "stop": trail_stop, "hwm": hwm}

    async def _set_breakeven_stop(self, symbol: str, entry_price: float, qty: int, track: dict) -> Optional[dict]:
        """Move stop to breakeven + buffer once position is up enough."""
        if track["trailing_active"]:
            track["breakeven_set"] = True
            return None

        be_price = round(entry_price * (1 + BREAKEVEN_BUFFER_PCT), 2)
        await self._cancel_gtt(symbol, track)
        gtt_id = await self._place_gtt_stop(symbol, qty, be_price)
        if gtt_id:
            track["breakeven_set"] = True
            track["gtt_id"] = gtt_id
            logger.info(f"[PM] {symbol}: breakeven stop set @ ₹{be_price:.2f} (entry=₹{entry_price:.2f}, gtt={gtt_id})")
            return {"symbol": symbol, "action": "breakeven_stop", "stop": be_price}
        return None

    async def _check_partial_profit(self, symbol: str, current_price: float, entry_price: float, qty: int, track: dict, pnl_pct: float) -> Optional[dict]:
        """Sell partial position at profit thresholds. Only one threshold processed per cycle."""
        taken = track["partials_taken"]

        for threshold in PARTIAL_THRESHOLDS:
            level = threshold["pct"]
            frac = threshold["sell_frac"]

            if level in taken:
                continue
            if pnl_pct < level:
                break  # thresholds are ordered — no point checking higher ones

            sell_qty = max(1, int(qty * frac))
            if sell_qty >= qty:
                sell_qty = qty - 1
            if sell_qty <= 0:
                taken.append(level)
                continue

            # Cancel GTT before partial sell to avoid conflict
            await self._cancel_gtt(symbol, track)

            order = await self.kite.partial_close(symbol, sell_qty)
            taken.append(level)  # mark taken regardless — avoid retry spam
            if order.get("id"):
                profit = round((current_price - entry_price) * sell_qty, 2)
                logger.info(f"[PM] {symbol}: partial profit @ +{level*100:.0f}% — sold {sell_qty} shares (₹{profit:+.2f})")
                remaining = qty - sell_qty
                if remaining > 0 and track.get("trailing_active"):
                    hwm = track["high_watermark"]
                    new_stop = round(hwm * (1 - TRAIL_DISTANCE_PCT), 2)
                    new_stop = max(new_stop, round(entry_price * (1 + BREAKEVEN_BUFFER_PCT), 2))
                    gtt_id = await self._place_gtt_stop(symbol, remaining, new_stop)
                    track["gtt_id"] = gtt_id
                return {
                    "symbol": symbol,
                    "action": "partial_profit",
                    "threshold": f"+{level*100:.0f}%",
                    "sold_qty": sell_qty,
                    "profit": profit,
                }
            return None
        return None

    def _check_stale(self, symbol: str, pnl_pct: float, track: dict, stale_hours: float = None) -> bool:
        """Close if position has barely moved after stale_hours (regime-adaptive)."""
        hrs = stale_hours or STALE_HOURS
        elapsed = (datetime.now(timezone.utc) - track["entry_time"]).total_seconds() / 3600
        if elapsed >= hrs and abs(pnl_pct) < STALE_MOVE_PCT:
            return True
        return False

    async def _close_full_position(self, symbol: str, qty: int, reason: str):
        """Close entire position and cleanup GTTs."""
        track = self._tracking.get(symbol, {})
        await self._cancel_gtt(symbol, track)
        await self.kite.close_position(symbol)
        logger.info(f"[PM] {symbol}: full close — reason={reason}")

        # Record cooldown for loss exits — prevent immediate re-entry
        if any(lr in reason for lr in LOSS_EXIT_REASONS):
            now = datetime.now(timezone.utc)
            self._cooldowns[symbol] = now
            expires = datetime.fromtimestamp(
                now.timestamp() + REENTRY_COOLDOWN_MIN * 60, tz=timezone.utc
            )
            await self.db.cooldowns.update_one(
                {"symbol": symbol},
                {"$set": {
                    "symbol": symbol,
                    "closed_at": now.isoformat(),
                    "expires_at": expires.isoformat(),
                    "reason": reason,
                }},
                upsert=True,
            )
            logger.info(f"[PM] {symbol}: cooldown set for {REENTRY_COOLDOWN_MIN}min (reason={reason}, persisted to DB)")

        await self.db.positions.update_one(
            {"ticker": symbol, "status": "open"},
            {"$set": {
                "status": "closed",
                "close_reason": reason,
                "closed_at": datetime.now(timezone.utc).isoformat(),
            }},
        )

    async def _cancel_gtt(self, symbol: str, track: dict):
        """Cancel the active GTT for a symbol (before replacing or closing)."""
        gtt_id = track.get("gtt_id")
        if not gtt_id:
            return
        try:
            if hasattr(self.kite, "gtt") and self.kite.gtt:
                await self.kite.gtt.cancel_gtt(gtt_id)
                logger.info(f"[PM] {symbol}: cancelled GTT {gtt_id}")
        except Exception as e:
            logger.warning(f"[PM] {symbol}: GTT cancel failed ({gtt_id}): {e}")
        track["gtt_id"] = None

    async def _place_gtt_stop(self, symbol: str, qty: int, stop_price: float) -> Optional[str]:
        """Place a single-leg GTT stop order. Returns gtt_id or None."""
        try:
            if hasattr(self.kite, "gtt") and self.kite.gtt:
                gtt_id = await self.kite.gtt.place_single(
                    symbol=symbol, qty=qty, trigger_price=stop_price, order_type="MARKET"
                )
                return gtt_id
        except Exception as e:
            logger.warning(f"[PM] {symbol}: GTT stop placement failed: {e}")
        return None
