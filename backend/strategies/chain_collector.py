"""Option chain snapshot collector.

Runs alongside strategy_loop.py to persist 5-min option chain snapshots
to MongoDB. After 3+ months of collection, Curvature and ZenCurve can be
properly backtested on real IV smile data instead of HV proxies.

Collection window: 09:30–15:30 IST every 5 minutes on market days.
Storage: MongoDB collection `nifty_chain_snapshots`
  {
    ts:     ISODate,              # snapshot timestamp (IST)
    expiry: "YYYY-MM-DD",         # contract expiry
    spot:   24500.0,              # NIFTY spot at snapshot time
    chain:  [                     # one doc per strike
      {strike, type, ltp, iv, oi, volume, bid, ask}
    ]
  }
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, time, timedelta

logger = logging.getLogger("moonshotx.chain_collector")

COLLECT_START = time(9, 30)
COLLECT_END   = time(15, 30)
INTERVAL_S    = 300     # 5 minutes


def _now_ist() -> datetime:
    from datetime import timezone
    return datetime.now(timezone.utc).astimezone(
        __import__("zoneinfo").ZoneInfo("Asia/Kolkata")
    )


def _is_weekday() -> bool:
    return _now_ist().weekday() < 5


def _in_window() -> bool:
    t = _now_ist().time()
    return COLLECT_START <= t <= COLLECT_END


async def _nearest_expiry(client) -> str | None:
    from strategies.strategy_loop import _nearest_expiry as _ne
    try:
        expiries = await client.expiry_list()
        if isinstance(expiries, list) and expiries:
            today = _now_ist().date().isoformat()
            future = sorted([e for e in expiries if e >= today])
            return future[0] if future else _ne()
    except Exception:
        pass
    return _ne()


async def collect_snapshot(client, db, expiry: str) -> None:
    """Fetch one option chain snapshot and persist to MongoDB."""
    from dhan.client import NIFTY_SECURITY_ID, NSE_SEGMENT

    try:
        # Spot from latest intraday bar
        today = _now_ist().date().isoformat()
        bars = await client.intraday_5min(NIFTY_SECURITY_ID, NSE_SEGMENT, "IDX_I", today, today)
        data = bars.get("data", {})
        closes = data.get("close", [])
        spot = float(closes[-1]) if closes else None
        if not spot:
            logger.warning("[COLLECTOR] No spot price available")
            return

        # Option chain
        chain_resp = await client.option_chain(NIFTY_SECURITY_ID, NSE_SEGMENT, expiry)
        if chain_resp.get("status") != "success":
            logger.warning("[COLLECTOR] chain fetch failed: %s", chain_resp.get("remarks"))
            return

        raw = chain_resp.get("data", {})
        records = []
        for item in raw.get("data", []):
            for opt_type in ("CE", "PE"):
                leg = item.get(opt_type, {})
                if not leg:
                    continue
                records.append({
                    "strike":  item.get("strike_price"),
                    "type":    opt_type,
                    "ltp":     leg.get("last_price", 0),
                    "iv":      leg.get("implied_volatility", 0),
                    "oi":      leg.get("oi", 0),
                    "volume":  leg.get("volume", 0),
                    "bid":     leg.get("bid_price", 0),
                    "ask":     leg.get("ask_price", 0),
                })

        if not records:
            return

        doc = {
            "ts":     _now_ist().replace(tzinfo=None),
            "expiry": expiry,
            "spot":   spot,
            "chain":  records,
        }
        await db.nifty_chain_snapshots.insert_one(doc)
        logger.debug("[COLLECTOR] Saved %d strikes for expiry %s @ %.0f", len(records), expiry, spot)

    except Exception as e:
        logger.error("[COLLECTOR] snapshot error: %s", e)


class ChainCollector:
    """Background task that snapshots the NIFTY option chain every 5 minutes."""

    def __init__(self, client, db):
        self.client   = client
        self.db       = db
        self._running = False
        self._expiry: str | None = None

    async def start(self) -> None:
        self._running = True
        logger.info("[COLLECTOR] Chain collector started")
        while self._running:
            if _is_weekday() and _in_window():
                if not self._expiry:
                    self._expiry = await _nearest_expiry(self.client)
                if self._expiry:
                    await collect_snapshot(self.client, self.db, self._expiry)
                    # Reset expiry after 15:30 so it refreshes next day
                    if _now_ist().time() >= COLLECT_END:
                        self._expiry = None
            await asyncio.sleep(INTERVAL_S)

    def stop(self) -> None:
        self._running = False
        logger.info("[COLLECTOR] Chain collector stopped")

    def status(self) -> dict:
        return {
            "running": self._running,
            "current_expiry": self._expiry,
            "in_window": _in_window(),
        }
