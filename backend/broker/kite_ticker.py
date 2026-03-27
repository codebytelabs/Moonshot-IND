"""
KiteTicker — WebSocket wrapper for real-time Kite quote streaming.

Wraps kiteconnect.KiteTicker with reconnect logic and a simple
callback-based interface. Used by position_manager for live price
updates when available (falls back to REST polling otherwise).
"""
import logging
import os
import threading
from typing import Callable, Dict, List, Optional

from dotenv import load_dotenv

logger = logging.getLogger("moonshotx.broker.kite_ticker")


class KiteTickerWrapper:
    """
    Thin wrapper around kiteconnect.KiteTicker.

    Usage:
        ticker = KiteTickerWrapper()
        ticker.add_callback(on_tick)
        ticker.subscribe(["NSE:RELIANCE", "NSE:INFY"])
        ticker.start()
        ...
        ticker.stop()
    """

    def __init__(self):
        load_dotenv(override=True)
        self._api_key = os.getenv("Zerodha_KITE_PAID_API_KEY")
        self._access_token = os.getenv("Zerodha_KITE_PAID_ACCESS_TOKEN", "")
        self._ticker = None
        self._callbacks: List[Callable] = []
        self._subscribed_tokens: List[int] = []
        self._running = False
        self._prices: Dict[int, float] = {}

    def add_callback(self, fn: Callable):
        """Register a tick callback: fn(ticks: list)."""
        self._callbacks.append(fn)

    def subscribe(self, instrument_tokens: List[int]):
        """Add instrument tokens to subscription list."""
        self._subscribed_tokens = list(set(self._subscribed_tokens + instrument_tokens))
        if self._ticker and self._running:
            self._ticker.subscribe(instrument_tokens)
            self._ticker.set_mode(self._ticker.MODE_LTP, instrument_tokens)

    def start(self):
        """Start the WebSocket connection in a background thread."""
        if self._running:
            return
        try:
            from kiteconnect import KiteTicker
            self._ticker = KiteTicker(self._api_key, self._access_token)
            self._ticker.on_ticks = self._on_ticks
            self._ticker.on_connect = self._on_connect
            self._ticker.on_error = self._on_error
            self._ticker.on_close = self._on_close
            self._ticker.on_reconnect = self._on_reconnect
            self._running = True
            t = threading.Thread(target=self._ticker.connect, kwargs={"threaded": True}, daemon=True)
            t.start()
            logger.info("[TICKER] KiteTicker started")
        except Exception as e:
            logger.error(f"[TICKER] Failed to start: {e}")

    def stop(self):
        if self._ticker:
            try:
                self._ticker.stop()
            except Exception:
                pass
        self._running = False
        logger.info("[TICKER] KiteTicker stopped")

    def get_ltp(self, instrument_token: int) -> Optional[float]:
        return self._prices.get(instrument_token)

    def _on_connect(self, ws, response):
        logger.info("[TICKER] WebSocket connected")
        if self._subscribed_tokens:
            ws.subscribe(self._subscribed_tokens)
            ws.set_mode(ws.MODE_LTP, self._subscribed_tokens)

    def _on_ticks(self, ws, ticks):
        for tick in ticks:
            token = tick.get("instrument_token")
            ltp = tick.get("last_price")
            if token and ltp:
                self._prices[token] = float(ltp)
        for cb in self._callbacks:
            try:
                cb(ticks)
            except Exception as e:
                logger.error(f"[TICKER] Callback error: {e}")

    def _on_error(self, ws, code, reason):
        logger.error(f"[TICKER] WebSocket error: code={code} reason={reason}")

    def _on_close(self, ws, code, reason):
        logger.warning(f"[TICKER] WebSocket closed: code={code} reason={reason}")
        self._running = False

    def _on_reconnect(self, ws, attempts_count):
        logger.info(f"[TICKER] Reconnect attempt #{attempts_count}")
        # Refresh token before reconnect
        token = os.getenv("Zerodha_KITE_PAID_ACCESS_TOKEN", "")
        if token:
            ws.set_access_token(token)
