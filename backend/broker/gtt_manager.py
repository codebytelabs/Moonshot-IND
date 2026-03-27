"""
GTTManager — Good Till Triggered order lifecycle manager.

Replaces bracket orders entirely. Every filled entry creates 2 GTTs:
  one SL (stop-loss trigger), one TP (take-profit trigger).

Critical: GTTs can be silently deleted by Zerodha's RMS when a position
closes externally (square-off by RMS, manual exit, etc.). The
verify_gtt_pair() method MUST be called every 5 seconds in the main loop.

Battle-tested pattern from DayTraderAI-IND bracket protection docs.
"""
import asyncio
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, Optional, List

from kiteconnect import KiteConnect
from dotenv import load_dotenv

logger = logging.getLogger("moonshotx.broker.gtt")

GTT_CHECK_INTERVAL_S = 5   # verify every 5 seconds in the loop tick
GTT_STATUS_ACTIVE = "active"
GTT_STATUS_TRIGGERED = "triggered"
GTT_STATUS_CANCELLED = "cancelled"
GTT_STATUS_DISABLED = "disabled"


@dataclass
class GTTPair:
    """Tracks the SL + TP GTT pair for one position."""
    symbol: str
    qty: int
    entry_price: float
    stop_loss: float
    take_profit: float
    sl_gtt_id: Optional[int] = None
    tp_gtt_id: Optional[int] = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    triggered_side: Optional[str] = None   # "sl" | "tp" once one fires
    closed: bool = False


@dataclass
class GTTPairStatus:
    sl_active: bool
    tp_active: bool
    sl_triggered: bool
    tp_triggered: bool
    needs_recreation: bool
    message: str


class GTTManager:
    """
    Manages SL + TP GTT pairs for all open positions.

    Call place_entry_with_protection() after a fill.
    Call verify_all_gtt_pairs() every 5s in the main loop tick.
    """

    def __init__(self, kite_client=None):
        self._pairs: Dict[str, GTTPair] = {}   # symbol → GTTPair
        self._kite_client = kite_client         # KiteBroker instance
        self._kite: Optional[KiteConnect] = None
        self._paper_mode = os.getenv("ZERODHA_PAPER_MODE", "False").lower() == "true"
        self._init_kite()

    def _init_kite(self):
        load_dotenv(override=True)
        api_key = os.getenv("Zerodha_KITE_PAID_API_KEY")
        access_token = os.getenv("Zerodha_KITE_PAID_ACCESS_TOKEN", "")
        if api_key:
            self._kite = KiteConnect(api_key=api_key)
            if access_token:
                self._kite.set_access_token(access_token)

    def _refresh_token(self):
        """Reload token in case KiteSessionManager updated it."""
        if self._kite:
            token = os.getenv("Zerodha_KITE_PAID_ACCESS_TOKEN", "")
            if token and self._kite.access_token != token:
                self._kite.set_access_token(token)

    # ── Entry + GTT creation ──────────────────────────────────────────────

    async def place_entry_with_protection(
        self,
        symbol: str,
        qty: int,
        entry_price: float,
        stop_loss: float,
        take_profit: float,
        exchange: str = "NSE",
    ) -> GTTPair:
        """
        After a filled market order, place SL + TP GTT pair.
        Returns a GTTPair tracking object.
        """
        pair = GTTPair(
            symbol=symbol,
            qty=qty,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
        )

        if self._paper_mode:
            pair.sl_gtt_id = -1
            pair.tp_gtt_id = -2
            self._pairs[symbol] = pair
            logger.info(f"[GTT][PAPER] Protection placed: {symbol} SL={stop_loss} TP={take_profit}")
            return pair

        try:
            self._refresh_token()
            sl_id, tp_id = await asyncio.to_thread(
                self._create_gtt_pair,
                symbol, qty, entry_price, stop_loss, take_profit, exchange,
            )
            pair.sl_gtt_id = sl_id
            pair.tp_gtt_id = tp_id
            self._pairs[symbol] = pair
            logger.info(f"[GTT] Protection placed: {symbol} SL={stop_loss}(id={sl_id}) TP={take_profit}(id={tp_id})")
        except Exception as e:
            logger.error(f"[GTT] Failed to place GTTs for {symbol}: {e}")

        return pair

    def _create_gtt_pair(
        self,
        symbol: str,
        qty: int,
        entry_price: float,
        stop_loss: float,
        take_profit: float,
        exchange: str,
    ):
        """Synchronous GTT creation (runs in thread)."""
        # SL GTT — single trigger, sell at market when price drops to stop_loss
        sl_gtt = self._kite.place_gtt(
            trigger_type=self._kite.GTT_TYPE_SINGLE,
            tradingsymbol=symbol,
            exchange=exchange,
            trigger_values=[round(stop_loss, 2)],
            last_price=entry_price,
            orders=[{
                "exchange": exchange,
                "tradingsymbol": symbol,
                "transaction_type": self._kite.TRANSACTION_TYPE_SELL,
                "quantity": qty,
                "order_type": self._kite.ORDER_TYPE_MARKET,
                "product": self._kite.PRODUCT_MIS,
                "price": round(stop_loss * 0.995, 2),   # 0.5% slippage buffer
            }]
        )
        # TP GTT — single trigger, sell at limit when price rises to take_profit
        tp_gtt = self._kite.place_gtt(
            trigger_type=self._kite.GTT_TYPE_SINGLE,
            tradingsymbol=symbol,
            exchange=exchange,
            trigger_values=[round(take_profit, 2)],
            last_price=entry_price,
            orders=[{
                "exchange": exchange,
                "tradingsymbol": symbol,
                "transaction_type": self._kite.TRANSACTION_TYPE_SELL,
                "quantity": qty,
                "order_type": self._kite.ORDER_TYPE_LIMIT,
                "product": self._kite.PRODUCT_MIS,
                "price": round(take_profit, 2),
            }]
        )
        return sl_gtt["trigger_id"], tp_gtt["trigger_id"]

    # ── Verification loop ─────────────────────────────────────────────────

    async def verify_all_gtt_pairs(self) -> List[dict]:
        """
        Verify all active GTT pairs. Recreate any that were silently deleted.
        Returns list of action dicts taken.
        Call this every 5s from the main loop.
        """
        actions = []
        for symbol, pair in list(self._pairs.items()):
            if pair.closed:
                continue
            try:
                action = await self._verify_pair(pair)
                if action:
                    actions.append(action)
            except Exception as e:
                logger.error(f"[GTT] verify error for {symbol}: {e}")
        return actions

    async def _verify_pair(self, pair: GTTPair) -> Optional[dict]:
        """Check and repair one GTT pair."""
        if self._paper_mode:
            return None

        self._refresh_token()
        sl_status, tp_status = await asyncio.to_thread(self._get_gtt_statuses, pair)

        # One side already triggered — cancel the survivor
        if sl_status == GTT_STATUS_TRIGGERED and not pair.closed:
            logger.info(f"[GTT] {pair.symbol}: SL triggered — cancelling TP GTT {pair.tp_gtt_id}")
            await self._cancel_gtt(pair.tp_gtt_id)
            pair.triggered_side = "sl"
            pair.closed = True
            return {"symbol": pair.symbol, "action": "sl_triggered", "sl": pair.stop_loss}

        if tp_status == GTT_STATUS_TRIGGERED and not pair.closed:
            logger.info(f"[GTT] {pair.symbol}: TP triggered — cancelling SL GTT {pair.sl_gtt_id}")
            await self._cancel_gtt(pair.sl_gtt_id)
            pair.triggered_side = "tp"
            pair.closed = True
            return {"symbol": pair.symbol, "action": "tp_triggered", "tp": pair.take_profit}

        # Silently deleted / disabled — recreate
        needs_recreation = (
            sl_status in (GTT_STATUS_CANCELLED, GTT_STATUS_DISABLED, None)
            or tp_status in (GTT_STATUS_CANCELLED, GTT_STATUS_DISABLED, None)
        )
        if needs_recreation:
            logger.warning(f"[GTT] {pair.symbol}: GTT silently gone (sl={sl_status}, tp={tp_status}) — recreating")
            try:
                sl_id, tp_id = await asyncio.to_thread(
                    self._create_gtt_pair,
                    pair.symbol, pair.qty, pair.entry_price,
                    pair.stop_loss, pair.take_profit, "NSE",
                )
                pair.sl_gtt_id = sl_id
                pair.tp_gtt_id = tp_id
                return {"symbol": pair.symbol, "action": "gtt_recreated", "sl_id": sl_id, "tp_id": tp_id}
            except Exception as e:
                logger.error(f"[GTT] Recreation failed for {pair.symbol}: {e}")

        return None

    def _get_gtt_statuses(self, pair: GTTPair):
        """Synchronous status fetch for SL + TP GTTs."""
        try:
            gtts = self._kite.get_gtts()
            gtt_map = {g["id"]: g["status"] for g in gtts}
            sl_status = gtt_map.get(pair.sl_gtt_id, None)
            tp_status = gtt_map.get(pair.tp_gtt_id, None)
            return sl_status, tp_status
        except Exception as e:
            logger.error(f"_get_gtt_statuses error: {e}")
            return None, None

    async def _cancel_gtt(self, gtt_id: Optional[int]):
        """Cancel a single GTT by id."""
        if not gtt_id or gtt_id < 0:
            return
        try:
            self._refresh_token()
            await asyncio.to_thread(self._kite.delete_gtt, gtt_id)
            logger.info(f"[GTT] Cancelled GTT id={gtt_id}")
        except Exception as e:
            logger.warning(f"[GTT] Cancel GTT {gtt_id} error: {e}")

    # ── Pair management ───────────────────────────────────────────────────

    async def cancel_survivor(self, symbol: str, triggered_side: str):
        """
        Called when one side of a GTT pair triggers.
        Cancels the other GTT so it doesn't fire on an already-closed position.
        """
        pair = self._pairs.get(symbol)
        if not pair:
            return
        if triggered_side == "sl":
            await self._cancel_gtt(pair.tp_gtt_id)
        else:
            await self._cancel_gtt(pair.sl_gtt_id)
        pair.closed = True
        pair.triggered_side = triggered_side

    async def cancel_all_for_symbol(self, symbol: str):
        """Cancel both GTTs when a position is manually closed."""
        pair = self._pairs.get(symbol)
        if not pair:
            return
        await self._cancel_gtt(pair.sl_gtt_id)
        await self._cancel_gtt(pair.tp_gtt_id)
        pair.closed = True
        logger.info(f"[GTT] All GTTs cancelled for {symbol}")

    def update_stop_loss(self, symbol: str, new_sl: float):
        """Update tracked stop-loss price. Next verify cycle will recreate if needed."""
        pair = self._pairs.get(symbol)
        if pair:
            pair.stop_loss = new_sl

    def get_pair(self, symbol: str) -> Optional[GTTPair]:
        return self._pairs.get(symbol)

    def get_all_pairs(self) -> Dict[str, GTTPair]:
        return dict(self._pairs)

    def remove_closed_pairs(self):
        """Clean up closed pairs to avoid stale tracking."""
        self._pairs = {s: p for s, p in self._pairs.items() if not p.closed}
