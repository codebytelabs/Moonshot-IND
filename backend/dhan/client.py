"""DhanHQ REST client wrapper — sandbox / live toggle.

Usage:
    from dhan.client import DhanClient
    client = DhanClient(sandbox=True)          # uses sandbox.dhan.co
    client = DhanClient(sandbox=False)         # uses api.dhan.co (live)
"""
import asyncio
import logging
import os
from datetime import datetime, date
from typing import Optional

from dhanhq import dhanhq

logger = logging.getLogger("moonshotx.dhan")

NIFTY_SECURITY_ID  = "13"          # NIFTY 50 index on NSE
NIFTY_LOT_SIZE     = 25            # post-Nov 2024
NSE_SEGMENT        = "NSE_EQ"      # dhanhq.NSE constant value
NSE_FNO_SEGMENT    = "NSE_FNO"     # dhanhq.NSE_FNO constant value
INDEX_INSTRUMENT   = "IDX_I"       # dhanhq.INDEX constant value
SANDBOX_BASE_URL   = "https://sandbox.dhan.co/v2"
LIVE_BASE_URL      = "https://api.dhan.co/v2"


class DhanClient:
    """Thin async wrapper around the `dhanhq` SDK."""

    def __init__(self, sandbox: bool = True):
        client_id    = os.environ.get("DHAN_CLIENT_ID", "")
        access_token = os.environ.get("DHAN_ACCESS_TOKEN", "")
        if not client_id or not access_token:
            logger.warning("[DHAN] DHAN_CLIENT_ID or DHAN_ACCESS_TOKEN not set in .env")

        self._dhan = dhanhq(client_id, access_token)
        self.sandbox = sandbox
        if sandbox:
            self._dhan.base_url = SANDBOX_BASE_URL
            logger.info("[DHAN] Client initialised — SANDBOX mode (%s)", SANDBOX_BASE_URL)
        else:
            self._dhan.base_url = LIVE_BASE_URL
            logger.info("[DHAN] Client initialised — LIVE mode (%s)", LIVE_BASE_URL)

    # ── Account ──────────────────────────────────────────────────────────

    async def get_fund_limits(self) -> dict:
        return await asyncio.to_thread(self._dhan.get_fund_limits)

    async def get_positions(self) -> list:
        resp = await asyncio.to_thread(self._dhan.get_positions)
        if isinstance(resp, dict) and resp.get("status") == "failure":
            logger.error("[DHAN] get_positions failed: %s", resp.get("remarks"))
            return []
        return resp.get("data", resp) if isinstance(resp, dict) else resp

    async def get_order_list(self) -> list:
        resp = await asyncio.to_thread(self._dhan.get_order_list)
        return resp.get("data", resp) if isinstance(resp, dict) else resp

    # ── Market data ──────────────────────────────────────────────────────

    async def intraday_5min(
        self,
        security_id: str,
        exchange_segment: str,
        instrument_type: str,
        from_date: str,
        to_date: str,
    ) -> dict:
        """Fetch 5-minute OHLCV bars. from_date/to_date: 'YYYY-MM-DD'."""
        return await asyncio.to_thread(
            self._dhan.intraday_minute_data,
            security_id,
            exchange_segment,
            instrument_type,
            from_date,
            to_date,
            5,
        )

    async def option_chain(
        self,
        under_security_id: str,
        under_exchange_segment: str,
        expiry: str,          # 'YYYY-MM-DD'
    ) -> dict:
        """Fetch full option chain for underlying at given expiry."""
        return await asyncio.to_thread(
            self._dhan.option_chain,
            under_security_id,
            under_exchange_segment,
            expiry,
        )

    async def expiry_list(
        self,
        under_security_id: str = NIFTY_SECURITY_ID,
        under_exchange_segment: str = NSE_SEGMENT,
    ) -> list:
        resp = await asyncio.to_thread(
            self._dhan.expiry_list, under_security_id, under_exchange_segment
        )
        return resp.get("data", []) if isinstance(resp, dict) else resp

    async def get_margin(
        self,
        security_id: str,
        exchange_segment: str,
        transaction_type: str,
        quantity: int,
        product_type: str,
        price: float,
    ) -> float:
        """Return total margin required (₹) or 0 on failure."""
        try:
            resp = await asyncio.to_thread(
                self._dhan.margin_calculator,
                security_id, exchange_segment, transaction_type,
                quantity, product_type, price,
            )
            data = resp.get("data", {}) if isinstance(resp, dict) else {}
            return float(data.get("totalMargin", data.get("total_margin", 0)))
        except Exception as e:
            logger.warning("[DHAN] margin_calculator error: %s", e)
            return 0.0

    # ── Orders ───────────────────────────────────────────────────────────

    async def place_order(
        self,
        security_id: str,
        exchange_segment: str,
        transaction_type: str,     # "BUY" or "SELL"
        quantity: int,
        product_type: str = "INTRADAY",
        order_type: str = "MARKET",
        price: float = 0.0,
        tag: Optional[str] = None,
    ) -> dict:
        """Place a single-leg order. Returns response dict with order_id."""
        txn = self._dhan.BUY if transaction_type.upper() == "BUY" else self._dhan.SELL
        otype = self._dhan.MARKET if order_type.upper() == "MARKET" else self._dhan.LIMIT
        ptype = self._dhan.INTRA if product_type.upper() in ("INTRA", "INTRADAY") else self._dhan.CNC

        try:
            resp = await asyncio.to_thread(
                self._dhan.place_order,
                security_id=security_id,
                exchange_segment=exchange_segment,
                transaction_type=txn,
                quantity=quantity,
                order_type=otype,
                product_type=ptype,
                price=round(price, 2),
                tag=tag,
            )
            logger.info(
                "[DHAN] Order placed: %s %s x%d %s — resp=%s",
                transaction_type, security_id, quantity,
                "SANDBOX" if self.sandbox else "LIVE",
                resp,
            )
            return resp if isinstance(resp, dict) else {"data": resp}
        except Exception as e:
            logger.error("[DHAN] place_order error: %s", e)
            return {"status": "failure", "remarks": str(e)}

    async def place_spread(
        self,
        short_security_id: str,
        long_security_id: str,
        quantity: int,
        exchange_segment: str = "NSE_FNO",
        product_type: str = "INTRA",
        tag: Optional[str] = None,
    ) -> dict:
        """Place both legs of a credit spread atomically (short + long)."""
        sell_resp = await self.place_order(
            security_id=short_security_id,
            exchange_segment=exchange_segment,
            transaction_type="SELL",
            quantity=quantity,
            product_type="INTRADAY",
            order_type="MARKET",
            tag=tag,
        )
        buy_resp = await self.place_order(
            security_id=long_security_id,
            exchange_segment=exchange_segment,
            transaction_type="BUY",
            quantity=quantity,
            product_type="INTRADAY",
            order_type="MARKET",
            tag=tag,
        )
        return {"short_leg": sell_resp, "long_leg": buy_resp}

    async def cancel_order(self, order_id: str) -> dict:
        resp = await asyncio.to_thread(self._dhan.cancel_order, order_id)
        return resp if isinstance(resp, dict) else {"data": resp}
