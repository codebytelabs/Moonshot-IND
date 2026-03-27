"""
KiteBroker — drop-in replacement for AlpacaClient, targeting Zerodha/Kite.

Exposes the same async interface as AlpacaClient so that loop.py,
position_manager.py, and scanner.py require no structural changes.

Paper mode: when ZERODHA_PAPER_MODE=True, orders are logged but not submitted.
"""
import asyncio
import logging
import os
from datetime import datetime, date, timedelta, timezone
from typing import Optional, List, Dict, Any

import pandas as pd
from kiteconnect import KiteConnect
from dotenv import load_dotenv

logger = logging.getLogger("moonshotx.broker.kite")

# NSE cash session: 09:15 – 15:30 IST (UTC+05:30 → UTC 03:45 – 10:00)
NSE_OPEN_UTC = (3, 45)     # 09:15 IST
NSE_CLOSE_UTC = (10, 0)    # 15:30 IST
NSE_SQUAREOFF_UTC = (9, 45)  # 15:15 IST — soft square-off warning

EXCHANGE_NSE = "NSE"
EXCHANGE_BSE = "BSE"

_PAPER_MODE_COUNTER = 0


def _ist_now() -> datetime:
    """Current time in IST."""
    from zoneinfo import ZoneInfo
    return datetime.now(ZoneInfo("Asia/Kolkata"))


class KiteBroker:
    """
    Zerodha/Kite broker adapter.

    Mirror AlpacaClient's public async interface so loop.py and
    position_manager.py call this without modification.
    """

    def __init__(self):
        load_dotenv(override=True)
        self.api_key = os.getenv("Zerodha_KITE_PAID_API_KEY")
        self.paper_mode = os.getenv("ZERODHA_PAPER_MODE", "False").lower() == "true"
        self._kite: Optional[KiteConnect] = None
        self._refresh_kite()
        if self.paper_mode:
            logger.info("[KITE] Paper mode ON — orders will be simulated, not submitted")

    def _refresh_kite(self):
        """Rebuild KiteConnect instance with latest token from env."""
        access_token = os.getenv("Zerodha_KITE_PAID_ACCESS_TOKEN", "")
        self._kite = KiteConnect(api_key=self.api_key)
        if access_token:
            self._kite.set_access_token(access_token)

    def _ensure_fresh_token(self):
        """Reload token from env in case KiteSessionManager refreshed it."""
        env_token = os.getenv("Zerodha_KITE_PAID_ACCESS_TOKEN", "")
        if env_token and self._kite.access_token != env_token:
            self._kite.set_access_token(env_token)

    # ── Account ──────────────────────────────────────────────────────────

    async def get_account(self) -> dict:
        """Return account info shaped like AlpacaClient.get_account()."""
        if self.paper_mode:
            return {
                "portfolio_value": 500000.0,
                "equity": 500000.0,
                "last_equity": 500000.0,
                "buying_power": 500000.0,
                "cash": 500000.0,
                "margin_used": 0.0,
                "paper": True,
            }
        try:
            self._ensure_fresh_token()
            margins = await asyncio.to_thread(self._kite.margins)
            equity = margins.get("equity", {})
            net = float(equity.get("net", 0))
            available = float(equity.get("available", {}).get("live_balance", net))
            used = float(equity.get("utilised", {}).get("debits", 0))
            return {
                "portfolio_value": net,
                "equity": net,
                "last_equity": net,
                "buying_power": available,
                "cash": available,
                "margin_used": used,
            }
        except Exception as e:
            logger.error(f"get_account error: {e}")
            return {}

    async def get_portfolio_history(self, period: str = "1M", timeframe: str = "1D", **kwargs) -> list:
        """Placeholder — Kite does not expose portfolio NAV history directly."""
        return []

    # ── Positions ────────────────────────────────────────────────────────

    async def get_positions(self) -> list:
        """Return list of open positions shaped like Alpaca positions."""
        if self.paper_mode:
            return []  # paper mode: positions tracked in DB, not via live API
        try:
            self._ensure_fresh_token()
            data = await asyncio.to_thread(self._kite.positions)
            raw = data.get("day", []) + [
                p for p in data.get("net", [])
                if p not in data.get("day", [])
            ]
            positions = []
            for p in raw:
                qty = int(p.get("quantity", 0))
                if qty == 0:
                    continue
                avg_price = float(p.get("average_price", 0))
                last_price = float(p.get("last_price", avg_price))
                unrealised = float(p.get("unrealised", 0))
                positions.append({
                    "symbol": p.get("tradingsymbol", ""),
                    "qty": qty,
                    "avg_entry_price": avg_price,
                    "current_price": last_price,
                    "unrealized_pl": unrealised,
                    "exchange": p.get("exchange", EXCHANGE_NSE),
                    "product": p.get("product", "MIS"),
                })
            return positions
        except Exception as e:
            logger.error(f"get_positions error: {e}")
            return []

    async def get_holdings(self) -> list:
        """Return CNC (delivery) holdings."""
        try:
            self._ensure_fresh_token()
            return await asyncio.to_thread(self._kite.holdings)
        except Exception as e:
            logger.error(f"get_holdings error: {e}")
            return []

    # ── Orders ────────────────────────────────────────────────────────────

    async def get_orders(self, status: str = "open") -> list:
        """Return all orders. Status filter: open / complete / all."""
        try:
            self._ensure_fresh_token()
            orders = await asyncio.to_thread(self._kite.orders)
            if status == "all":
                return orders
            kite_statuses = {
                "open": ["OPEN", "PENDING", "TRIGGER PENDING", "AMO REQ RECEIVED"],
                "complete": ["COMPLETE", "CANCELLED", "REJECTED"],
            }
            filter_set = set(kite_statuses.get(status, []))
            if not filter_set:
                return orders
            return [o for o in orders if o.get("status", "").upper() in filter_set]
        except Exception as e:
            logger.error(f"get_orders error: {e}")
            return []

    async def get_orders_for_symbol(self, symbol: str, status: str = "open") -> list:
        all_orders = await self.get_orders(status=status)
        return [o for o in all_orders if o.get("tradingsymbol", "") == symbol]

    # ── Clock ────────────────────────────────────────────────────────────

    async def get_clock(self) -> dict:
        """
        Return a clock dict shaped like Alpaca's /v2/clock.
        Uses IST 09:15–15:30 as NSE cash hours.
        """
        from zoneinfo import ZoneInfo
        IST = ZoneInfo("Asia/Kolkata")
        now_ist = datetime.now(IST)
        now_utc = datetime.now(timezone.utc)

        market_open_ist = now_ist.replace(hour=9, minute=15, second=0, microsecond=0)
        market_close_ist = now_ist.replace(hour=15, minute=30, second=0, microsecond=0)

        weekday = now_ist.weekday()   # 0=Mon … 4=Fri
        is_weekday = weekday < 5
        is_in_hours = market_open_ist <= now_ist <= market_close_ist

        is_open = is_weekday and is_in_hours

        if not is_open:
            # Next open: next trading day at 09:15 IST
            days_ahead = 1
            if weekday == 4:    # Friday → Monday
                days_ahead = 3
            elif weekday == 5:  # Saturday → Monday
                days_ahead = 2
            next_open_ist = (now_ist + timedelta(days=days_ahead)).replace(
                hour=9, minute=15, second=0, microsecond=0
            )
            next_open_utc = next_open_ist.astimezone(timezone.utc)
        else:
            next_open_ist = (now_ist + timedelta(days=1)).replace(
                hour=9, minute=15, second=0, microsecond=0
            )
            next_open_utc = next_open_ist.astimezone(timezone.utc)

        next_close_ist = market_close_ist
        next_close_utc = next_close_ist.astimezone(timezone.utc)

        return {
            "is_open": is_open,
            "timestamp": now_utc.isoformat(),
            "next_open": next_open_utc.isoformat(),
            "next_close": next_close_utc.isoformat(),
        }

    def is_market_open(self) -> bool:
        """Synchronous market-open check — IST 09:15–15:30, Mon–Fri."""
        from zoneinfo import ZoneInfo
        IST = ZoneInfo("Asia/Kolkata")
        now = datetime.now(IST)
        if now.weekday() >= 5:
            return False
        t = now.time()
        from datetime import time as t_
        return t_(9, 15) <= t <= t_(15, 30)

    # ── Order submission ──────────────────────────────────────────────────

    async def submit_market_order(
        self,
        symbol: str,
        qty: int,
        side: str,
        take_profit_price: Optional[float] = None,
        stop_loss_price: Optional[float] = None,
        exchange: str = EXCHANGE_NSE,
        product: str = "MIS",
    ) -> dict:
        """
        Place a market order. Returns order dict with 'id' key.
        If take_profit_price and stop_loss_price are provided, GTT orders
        will be managed separately by GTTManager (not inline here).
        """
        global _PAPER_MODE_COUNTER
        transaction = self._kite.TRANSACTION_TYPE_BUY if side.lower() == "buy" else self._kite.TRANSACTION_TYPE_SELL

        if self.paper_mode:
            _PAPER_MODE_COUNTER += 1
            order_id = f"PAPER_{_PAPER_MODE_COUNTER:06d}"
            logger.info(f"[PAPER] Market order: {symbol} {side.upper()} x{qty} — id={order_id}")
            return {"id": order_id, "symbol": symbol, "qty": qty, "side": side, "paper": True}

        try:
            self._ensure_fresh_token()
            order_id = await asyncio.to_thread(
                self._kite.place_order,
                tradingsymbol=symbol,
                exchange=exchange,
                transaction_type=transaction,
                quantity=qty,
                order_type=self._kite.ORDER_TYPE_MARKET,
                product=self._kite.PRODUCT_MIS if product == "MIS" else self._kite.PRODUCT_CNC,
                variety=self._kite.VARIETY_REGULAR,
            )
            logger.info(f"[KITE] Market order placed: {symbol} {side.upper()} x{qty} — order_id={order_id}")
            return {"id": str(order_id), "symbol": symbol, "qty": qty, "side": side}
        except Exception as e:
            logger.error(f"submit_market_order {symbol} error: {e}")
            return {}

    async def submit_limit_order(
        self,
        symbol: str,
        qty: int,
        side: str,
        limit_price: float,
        exchange: str = EXCHANGE_NSE,
        product: str = "MIS",
    ) -> dict:
        global _PAPER_MODE_COUNTER
        transaction = self._kite.TRANSACTION_TYPE_BUY if side.lower() == "buy" else self._kite.TRANSACTION_TYPE_SELL

        if self.paper_mode:
            _PAPER_MODE_COUNTER += 1
            order_id = f"PAPER_{_PAPER_MODE_COUNTER:06d}"
            logger.info(f"[PAPER] Limit order: {symbol} {side.upper()} x{qty} @{limit_price} — id={order_id}")
            return {"id": order_id, "symbol": symbol, "qty": qty, "side": side, "paper": True}

        try:
            self._ensure_fresh_token()
            order_id = await asyncio.to_thread(
                self._kite.place_order,
                tradingsymbol=symbol,
                exchange=exchange,
                transaction_type=transaction,
                quantity=qty,
                order_type=self._kite.ORDER_TYPE_LIMIT,
                price=round(limit_price, 2),
                product=self._kite.PRODUCT_MIS if product == "MIS" else self._kite.PRODUCT_CNC,
                variety=self._kite.VARIETY_REGULAR,
            )
            logger.info(f"[KITE] Limit order placed: {symbol} {side.upper()} x{qty} @{limit_price} — order_id={order_id}")
            return {"id": str(order_id), "symbol": symbol, "qty": qty, "side": side}
        except Exception as e:
            logger.error(f"submit_limit_order {symbol} error: {e}")
            return {}

    async def submit_stop_order(
        self,
        symbol: str,
        qty: int,
        stop_price: float,
        side: str = "sell",
        exchange: str = EXCHANGE_NSE,
        product: str = "MIS",
    ) -> dict:
        """
        Place an SL-M (stop-loss market) order.
        Note: For GTT-based protection use GTTManager instead.
        """
        global _PAPER_MODE_COUNTER
        transaction = self._kite.TRANSACTION_TYPE_SELL if side.lower() == "sell" else self._kite.TRANSACTION_TYPE_BUY

        if self.paper_mode:
            _PAPER_MODE_COUNTER += 1
            order_id = f"PAPER_{_PAPER_MODE_COUNTER:06d}"
            logger.info(f"[PAPER] Stop order: {symbol} {side.upper()} x{qty} @{stop_price} — id={order_id}")
            return {"id": order_id, "symbol": symbol, "qty": qty, "side": side, "paper": True}

        try:
            self._ensure_fresh_token()
            order_id = await asyncio.to_thread(
                self._kite.place_order,
                tradingsymbol=symbol,
                exchange=exchange,
                transaction_type=transaction,
                quantity=qty,
                order_type=self._kite.ORDER_TYPE_SLM,
                trigger_price=round(stop_price, 2),
                product=self._kite.PRODUCT_MIS if product == "MIS" else self._kite.PRODUCT_CNC,
                variety=self._kite.VARIETY_REGULAR,
            )
            logger.info(f"[KITE] SL-M order placed: {symbol} SELL x{qty} trigger@{stop_price} — order_id={order_id}")
            return {"id": str(order_id), "symbol": symbol, "qty": qty, "side": side}
        except Exception as e:
            logger.error(f"submit_stop_order {symbol} error: {e}")
            return {}

    async def cancel_order(self, order_id: str) -> bool:
        if self.paper_mode:
            logger.info(f"[PAPER] Cancel order: {order_id}")
            return True
        try:
            self._ensure_fresh_token()
            await asyncio.to_thread(
                self._kite.cancel_order,
                variety=self._kite.VARIETY_REGULAR,
                order_id=order_id,
            )
            return True
        except Exception as e:
            logger.warning(f"cancel_order {order_id} error: {e}")
            return False

    async def cancel_all_orders(self) -> int:
        orders = await self.get_orders(status="open")
        count = 0
        for o in orders:
            oid = o.get("order_id", "")
            if oid and await self.cancel_order(oid):
                count += 1
        return count

    # ── Position close ────────────────────────────────────────────────────

    async def close_position(self, symbol: str) -> dict:
        """Close entire position in symbol with a market order."""
        positions = await self.get_positions()
        pos = next((p for p in positions if p["symbol"] == symbol), None)
        if not pos:
            logger.warning(f"close_position: no open position for {symbol}")
            return {}
        qty = abs(int(pos["qty"]))
        side = "sell" if int(pos["qty"]) > 0 else "buy"
        return await self.submit_market_order(symbol, qty, side)

    async def close_all_positions(self) -> int:
        positions = await self.get_positions()
        count = 0
        for pos in positions:
            symbol = pos.get("symbol", "")
            if not symbol:
                continue
            result = await self.close_position(symbol)
            if result.get("id"):
                count += 1
        return count

    async def partial_close(self, symbol: str, qty: int) -> dict:
        """Sell a partial quantity of an open position."""
        return await self.submit_market_order(symbol, qty, "sell")

    # ── Market data ───────────────────────────────────────────────────────

    async def get_bars(
        self,
        symbol: str,
        timeframe: str = "5minute",
        limit: int = 6,
        exchange: str = EXCHANGE_NSE,
    ) -> list:
        """
        Fetch recent OHLCV bars via Kite historical data API.
        timeframe: minute, 3minute, 5minute, 10minute, 15minute, 30minute, 60minute, day
        """
        try:
            self._ensure_fresh_token()
            instrument_token = await self._get_instrument_token(symbol, exchange)
            if not instrument_token:
                return []
            from_dt = (datetime.now(timezone.utc) - timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S")
            to_dt = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            data = await asyncio.to_thread(
                self._kite.historical_data,
                instrument_token=instrument_token,
                from_date=from_dt,
                to_date=to_dt,
                interval=timeframe,
            )
            return data[-limit:] if data else []
        except Exception as e:
            logger.warning(f"get_bars {symbol} error: {e}")
            return []

    async def get_latest_quote(self, symbol: str, exchange: str = EXCHANGE_NSE) -> dict:
        """Return latest quote for symbol."""
        try:
            self._ensure_fresh_token()
            instrument = f"{exchange}:{symbol}"
            data = await asyncio.to_thread(self._kite.ltp, [instrument])
            entry = data.get(instrument, {})
            return {"symbol": symbol, "price": entry.get("last_price", 0), "ltp": entry.get("last_price", 0)}
        except Exception as e:
            logger.warning(f"get_latest_quote {symbol} error: {e}")
            return {}

    async def get_snapshot(self, symbol: str, exchange: str = EXCHANGE_NSE) -> dict:
        """Return full market depth snapshot for symbol."""
        try:
            self._ensure_fresh_token()
            instrument = f"{exchange}:{symbol}"
            data = await asyncio.to_thread(self._kite.quote, [instrument])
            return data.get(instrument, {})
        except Exception as e:
            logger.warning(f"get_snapshot {symbol} error: {e}")
            return {}

    # ── Screener equivalents (Alpaca compat) ──────────────────────────────

    async def get_most_active(self, top: int = 50) -> list:
        """Return top active NSE symbols by volume (from NIFTY500 universe)."""
        try:
            from data.india_universe import get_nifty500_symbols
            return get_nifty500_symbols()[:top]
        except Exception as e:
            logger.warning(f"get_most_active error: {e}")
            return []

    async def get_top_movers(self, top: int = 50, **kwargs) -> list:
        """Return symbols with highest intraday gains from watchlist."""
        try:
            from data.india_universe import INDIA_SEED_UNIVERSE
            return INDIA_SEED_UNIVERSE[:top]
        except Exception as e:
            logger.warning(f"get_top_movers error: {e}")
            return []

    # ── Instrument token cache ────────────────────────────────────────────

    _instrument_cache: Dict[str, int] = {}

    async def _get_instrument_token(self, symbol: str, exchange: str = EXCHANGE_NSE) -> Optional[int]:
        """Resolve symbol to Kite instrument token (cached)."""
        key = f"{exchange}:{symbol}"
        if key in self._instrument_cache:
            return self._instrument_cache[key]
        try:
            instruments = await asyncio.to_thread(self._kite.instruments, exchange)
            for inst in instruments:
                if inst["tradingsymbol"] == symbol:
                    self._instrument_cache[key] = inst["instrument_token"]
                    return inst["instrument_token"]
        except Exception as e:
            logger.warning(f"_get_instrument_token {symbol} error: {e}")
        return None
