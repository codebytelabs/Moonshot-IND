"""ZenCurve strategy runner — async 5-min loop (10:15–14:15 IST).

Architecture:
  - Runs as a background asyncio task, similar to the equity/derivatives loops
  - Every 5 minutes: fetch NIFTY 5-min bars + option chain → generate signals
  - Portfolio manager enforces capital limits + risk guards
  - All orders routed to DhanHQ (sandbox in test mode, live in production)
  - Force-closes all open spreads at 15:15 IST via market orders

Configuration (from .env):
  DHAN_CLIENT_ID, DHAN_ACCESS_TOKEN
  DHAN_SANDBOX=true  (set false for live)
  STRATEGY_CAPITAL=100000
  ZEN_ALLOC=0.4        (fraction for Zen)
  CURV_ALLOC=0.4       (fraction for Curvature)
  HYBRID_ALLOC=0.2     (fraction for ZenCurve hybrid)
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, time, date, timedelta
from typing import Optional

import pandas as pd

from dhan.client import (
    DhanClient, NIFTY_SECURITY_ID, NIFTY_LOT_SIZE,
    NSE_SEGMENT, NSE_FNO_SEGMENT, INDEX_INSTRUMENT,
)
from dhan.instruments import get_spread_security_ids
from strategies.zen_spread import generate_zen_signals, construct_spread_order
from strategies.curvature_spread import generate_curvature_signals
from strategies.hybrid_spread import generate_hybrid_signals, hybrid_signal_to_spread_order
from strategies.portfolio import PortfolioManager, TradeRecord

logger = logging.getLogger("moonshotx.strategies.loop")

LOOP_INTERVAL_S = 300           # 5 minutes
ENTRY_START     = time(10, 15)
ENTRY_END       = time(14, 15)
FORCE_CLOSE_AT  = time(15, 15)
IST_OFFSET      = timedelta(hours=5, minutes=30)
SPREAD_WIDTH    = 400
MAX_LOSS_RS     = 3_000


def _nearest_expiry() -> str:
    """Compute nearest upcoming NIFTY weekly expiry (Thursday)."""
    today = _now_ist().date()
    # NIFTY weeklies expire on Thursday (weekday=3)
    days_ahead = (3 - today.weekday()) % 7
    if days_ahead == 0 and _now_ist().time() > time(15, 30):
        days_ahead = 7
    expiry = today + timedelta(days=days_ahead)
    return expiry.isoformat()


def _load_yfinance_5min(from_date: str, to_date: str) -> pd.DataFrame:
    """Fetch NIFTY 5-min bars from yfinance (sandbox fallback)."""
    try:
        import yfinance as yf
        df = yf.download(
            "^NSEI", start=from_date, end=to_date,
            interval="5m", progress=False, auto_adjust=True,
        )
        if df.empty:
            return pd.DataFrame()
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.droplevel(1)
        df.rename(columns=str.lower, inplace=True)
        df.index = pd.to_datetime(df.index)
        if df.index.tz is None:
            df.index = df.index.tz_localize("UTC")
        df.index = df.index.tz_convert("Asia/Kolkata")
        return df.sort_index()
    except Exception as e:
        logger.warning("[LOOP] yfinance 5min load failed: %s", e)
        return pd.DataFrame()


def _now_ist() -> datetime:
    from datetime import timezone
    return datetime.now(timezone.utc).astimezone(
        __import__("zoneinfo").ZoneInfo("Asia/Kolkata")
    )


def _ist_time() -> time:
    return _now_ist().time()


def _is_trading_day() -> bool:
    return _now_ist().weekday() < 5  # Mon–Fri


def _in_entry_window() -> bool:
    t = _ist_time()
    return _is_trading_day() and ENTRY_START <= t <= ENTRY_END


def _past_force_close() -> bool:
    return _ist_time() >= FORCE_CLOSE_AT


def _parse_chain_to_df(chain_data: dict) -> pd.DataFrame:
    """Parse DhanHQ option_chain response into a flat DataFrame."""
    rows = []
    data = chain_data.get("data", chain_data) if isinstance(chain_data, dict) else []
    if isinstance(data, list):
        for item in data:
            for opt_type in ("CE", "PE"):
                leg = item.get(opt_type, {})
                if not leg:
                    continue
                rows.append({
                    "strike": float(item.get("strike_price", item.get("strikePrice", 0))),
                    "type": opt_type,
                    "iv": float(leg.get("implied_volatility", leg.get("iv", 0)) or 0) / 100.0,
                    "volume": float(leg.get("volume", 0) or 0),
                    "oi": float(leg.get("open_interest", leg.get("oi", 0)) or 0),
                    "ltp": float(leg.get("last_price", leg.get("ltp", 0)) or 0),
                })
    return pd.DataFrame(rows)


def _parse_intraday_to_df(intraday_data: dict) -> pd.DataFrame:
    """Parse DhanHQ intraday_minute_data response into OHLCV DataFrame."""
    data = intraday_data.get("data", {}) if isinstance(intraday_data, dict) else {}
    if not data:
        return pd.DataFrame()
    try:
        timestamps = data.get("timestamp", [])
        opens  = data.get("open",   [])
        highs  = data.get("high",   [])
        lows   = data.get("low",    [])
        closes = data.get("close",  [])
        volumes= data.get("volume", [])
        df = pd.DataFrame({
            "open":   opens,
            "high":   highs,
            "low":    lows,
            "close":  closes,
            "volume": volumes,
        }, index=pd.to_datetime(timestamps, unit="s", utc=True))
        df.index = df.index.tz_convert("Asia/Kolkata")
        return df.sort_index()
    except Exception as e:
        logger.warning("[LOOP] intraday parse error: %s", e)
        return pd.DataFrame()


class StrategyLoop:
    """Main async trading loop for Zen/Curvature/ZenCurve strategies."""

    def __init__(self, broadcast_fn=None):
        self.sandbox   = os.environ.get("DHAN_SANDBOX", "true").lower() == "true"
        self.client    = DhanClient(sandbox=self.sandbox)
        self.broadcast = broadcast_fn or (lambda msg: None)

        total_cap = float(os.environ.get("STRATEGY_CAPITAL", "100000"))
        zen_alloc  = float(os.environ.get("ZEN_ALLOC",   "0.40"))
        curv_alloc = float(os.environ.get("CURV_ALLOC",  "0.40"))
        hyb_alloc  = float(os.environ.get("HYBRID_ALLOC","0.20"))

        self.portfolio = PortfolioManager(
            total_capital=total_cap,
            allocations={
                "zen":      zen_alloc,
                "curvature": curv_alloc,
                "zenCurve": hyb_alloc,
            },
        )
        self._running  = False
        self._loop_count = 0
        self._last_chain: pd.DataFrame = pd.DataFrame()
        self._events: list[dict] = []
        self._expiry: Optional[str] = None

    # ── Event log ─────────────────────────────────────────────────────────

    def _log_event(self, msg: str, level: str = "INFO"):
        evt = {"ts": _now_ist().isoformat(), "level": level, "msg": msg}
        self._events.append(evt)
        if len(self._events) > 200:
            self._events = self._events[-200:]
        logger.info("[LOOP] %s", msg)
        self.broadcast({"type": "strategy_event", "event": evt})

    # ── Data fetching ─────────────────────────────────────────────────────

    async def _get_expiry(self) -> Optional[str]:
        """Get nearest NIFTY weekly expiry from DhanHQ."""
        try:
            expiries = await self.client.expiry_list(NIFTY_SECURITY_ID, NSE_SEGMENT)
            if expiries:
                today_str = date.today().isoformat()
                future = [e for e in sorted(expiries) if e >= today_str]
                return future[0] if future else None
        except Exception as e:
            logger.warning("[LOOP] expiry_list error: %s", e)
        return None

    async def _fetch_5min_bars(self) -> pd.DataFrame:
        today = _now_ist().date().isoformat()
        if self.sandbox:
            # Sandbox doesn't provide live market data — use yfinance
            yesterday = (_now_ist().date() - timedelta(days=5)).isoformat()
            return await asyncio.to_thread(_load_yfinance_5min, yesterday, today)
        resp = await self.client.intraday_5min(
            NIFTY_SECURITY_ID, NSE_SEGMENT, INDEX_INSTRUMENT,
            today, today,
        )
        return _parse_intraday_to_df(resp)

    async def _fetch_chain(self, expiry: str) -> pd.DataFrame:
        resp = await self.client.option_chain(NIFTY_SECURITY_ID, NSE_SEGMENT, expiry)
        return _parse_chain_to_df(resp)

    # ── Force-close all open positions ────────────────────────────────────

    async def _force_close_all(self):
        """Close all open spread positions at market (15:15 IST)."""
        for strategy_name, acc in self.portfolio.accounts.items():
            for trade in acc.open_trades():
                self._log_event(f"Force-closing trade {trade.trade_id} ({strategy_name})")
                if trade.short_security_id:
                    await self.client.place_order(
                        security_id=trade.short_security_id,
                        exchange_segment=NSE_FNO_SEGMENT,
                        transaction_type="BUY",
                        quantity=trade.lots * NIFTY_LOT_SIZE,
                        product_type="INTRA",
                        tag=f"fc_{trade.trade_id}",
                    )
                if trade.long_security_id:
                    await self.client.place_order(
                        security_id=trade.long_security_id,
                        exchange_segment=NSE_FNO_SEGMENT,
                        transaction_type="SELL",
                        quantity=trade.lots * NIFTY_LOT_SIZE,
                        product_type="INTRA",
                        tag=f"fc_{trade.trade_id}",
                    )
                self.portfolio.close_trade(strategy_name, trade.trade_id, pnl=0.0)

    # ── Signal processing + order routing ────────────────────────────────

    async def _process_signals(
        self,
        df_5m: pd.DataFrame,
        chain_df: pd.DataFrame,
        expiry: str,
    ):
        if df_5m.empty:
            return

        spot = float(df_5m["close"].iloc[-1])

        # ── Build chain_history for curvature ─────────────────────────
        chain_history: dict = {}
        if not chain_df.empty:
            now_ts = pd.Timestamp.now(tz="Asia/Kolkata").floor("5min")
            chain_history[now_ts] = chain_df
            spot_series = pd.Series({now_ts: spot})

        # ── ZEN signals ───────────────────────────────────────────────
        if self.portfolio.check_strategy_risk("zen"):
            if not chain_df.empty:
                atm = int(round(spot / 50) * 50)
                ce_rows = chain_df[chain_df["type"] == "CE"]
                pe_rows = chain_df[chain_df["type"] == "PE"]
                atm_ce = ce_rows[ce_rows["strike"] == atm]
                atm_pe = pe_rows[pe_rows["strike"] == atm]
                if not atm_ce.empty and not atm_pe.empty:
                    df_5m["atm_ce_vol"] = float(atm_ce["volume"].values[0])
                    df_5m["atm_pe_vol"] = float(atm_pe["volume"].values[0])
                    df_5m["atm_ce_iv"]  = float(atm_ce["iv"].values[0])
                    df_5m["atm_pe_iv"]  = float(atm_pe["iv"].values[0])

            zen_sigs = generate_zen_signals(df_5m)
            if zen_sigs:
                sig = zen_sigs[-1]
                ids = get_spread_security_ids("NIFTY", expiry, spot, sig.direction, SPREAD_WIDTH)
                short_id, long_id = (ids[0], ids[1]) if ids else (None, None)
                order = construct_spread_order(
                    sig,
                    lot_size=NIFTY_LOT_SIZE,
                    margin_per_lot=20_000,
                    allocated_capital=self.portfolio.accounts["zen"].equity,
                    short_security_id=short_id,
                    long_security_id=long_id,
                    expiry=expiry,
                )
                await self._place_strategy_order("zen", sig, order, spot)

        # ── CURVATURE signals ─────────────────────────────────────────
        if self.portfolio.check_strategy_risk("curvature") and chain_history:
            curv_sigs = generate_curvature_signals(chain_history, spot_series)
            if curv_sigs:
                sig = curv_sigs[-1]
                ids = get_spread_security_ids("NIFTY", expiry, spot, sig.direction, SPREAD_WIDTH)
                short_id, long_id = (ids[0], ids[1]) if ids else (None, None)
                from strategies.zen_spread import CreditSpreadSignal, construct_spread_order as cso
                fake_sig = CreditSpreadSignal(
                    timestamp=sig.timestamp, direction=sig.direction,
                    alpha1=sig.alpha, alpha2=sig.alpha, spot=sig.spot,
                    short_strike=sig.short_strike, long_strike=sig.long_strike,
                    opt_type=sig.opt_type,
                )
                order = cso(
                    fake_sig,
                    lot_size=NIFTY_LOT_SIZE,
                    margin_per_lot=20_000,
                    allocated_capital=self.portfolio.accounts["curvature"].equity,
                    strategy="curvature",
                    short_security_id=short_id,
                    long_security_id=long_id,
                    expiry=expiry,
                )
                await self._place_strategy_order_raw("curvature", sig, order, spot)

        # ── HYBRID (ZenCurve) signals ─────────────────────────────────
        if self.portfolio.check_strategy_risk("zenCurve"):
            ch = chain_history if chain_history else None
            hyb_sigs = generate_hybrid_signals(df_5m, ch)
            if hyb_sigs:
                sig = hyb_sigs[-1]
                ids = get_spread_security_ids("NIFTY", expiry, spot, sig.direction, SPREAD_WIDTH)
                short_id, long_id = (ids[0], ids[1]) if ids else (None, None)
                order = hybrid_signal_to_spread_order(
                    sig,
                    lot_size=NIFTY_LOT_SIZE,
                    margin_per_lot=20_000,
                    allocated_capital=self.portfolio.accounts["zenCurve"].equity,
                    short_security_id=short_id,
                    long_security_id=long_id,
                    expiry=expiry,
                )
                await self._place_strategy_order("zenCurve", sig, order, spot)

    async def _place_strategy_order(self, strategy_name, sig, order, spot):
        """Route a SpreadOrder to broker after portfolio manager approval."""
        credit_est = (SPREAD_WIDTH * 0.05)      # rough estimate ₹/unit
        record = self.portfolio.request_trade(
            strategy_name=strategy_name,
            direction=sig.direction,
            short_strike=order.signal.short_strike if hasattr(order.signal, "short_strike") else sig.short_strike,
            long_strike=order.signal.long_strike  if hasattr(order.signal, "long_strike")  else sig.long_strike,
            opt_type=order.signal.opt_type        if hasattr(order.signal, "opt_type")     else sig.opt_type,
            entry_credit=credit_est,
            margin_per_lot=order.margin_per_lot,
            expiry=order.expiry,
            short_security_id=order.short_security_id,
            long_security_id=order.long_security_id,
        )
        if record and order.short_security_id and order.long_security_id:
            resp = await self.client.place_spread(
                short_security_id=order.short_security_id,
                long_security_id=order.long_security_id,
                quantity=order.lots * NIFTY_LOT_SIZE,
                exchange_segment=NSE_FNO_SEGMENT,
                tag=f"{strategy_name}_{record.trade_id}",
            )
            self._log_event(
                f"[{strategy_name.upper()}] {sig.direction.upper()} spread placed: "
                f"sell {order.signal.short_strike if hasattr(order.signal,'short_strike') else ''}"
                f" / buy {order.signal.long_strike if hasattr(order.signal,'long_strike') else ''}"
                f" {'SANDBOX' if self.sandbox else 'LIVE'}"
            )

    async def _place_strategy_order_raw(self, strategy_name, sig, order, spot):
        """Same as above but for signals from curvature (different signal type)."""
        credit_est = SPREAD_WIDTH * 0.05
        record = self.portfolio.request_trade(
            strategy_name=strategy_name,
            direction=sig.direction,
            short_strike=sig.short_strike,
            long_strike=sig.long_strike,
            opt_type=sig.opt_type,
            entry_credit=credit_est,
            margin_per_lot=order.margin_per_lot,
            expiry=order.expiry,
            short_security_id=order.short_security_id,
            long_security_id=order.long_security_id,
        )
        if record and order.short_security_id and order.long_security_id:
            await self.client.place_spread(
                short_security_id=order.short_security_id,
                long_security_id=order.long_security_id,
                quantity=order.lots * NIFTY_LOT_SIZE,
                exchange_segment=NSE_FNO_SEGMENT,
                tag=f"{strategy_name}_{record.trade_id}",
            )
            self._log_event(
                f"[{strategy_name.upper()}] {sig.direction.upper()} spread: "
                f"sell {sig.short_strike} / buy {sig.long_strike} "
                f"{'SANDBOX' if self.sandbox else 'LIVE'}"
            )

    # ── Main loop ────────────────────────────────────────────────────────

    async def _run_cycle(self):
        self._loop_count += 1
        now = _now_ist()
        self._log_event(f"Cycle #{self._loop_count} — {now.strftime('%H:%M')} IST")

        if not _is_trading_day():
            self._log_event("Weekend — skipping cycle")
            return

        t = now.time()
        if t < ENTRY_START or t > FORCE_CLOSE_AT:
            self._log_event(f"Outside trading window ({t.strftime('%H:%M')}) — idle")
            return

        if _past_force_close():
            self._log_event("15:15 IST — force-closing all spreads")
            await self._force_close_all()
            return

        # Refresh expiry daily
        if not self._expiry:
            self._expiry = await self._get_expiry()
            if not self._expiry:
                # Fallback: compute nearest Thursday expiry locally
                self._expiry = _nearest_expiry()
                self._log_event(f"Using computed expiry: {self._expiry}")
        if not self._expiry:
            self._log_event("Could not determine NIFTY expiry — skip", "WARN")
            return

        df_5m   = await self._fetch_5min_bars()
        chain_df= await self._fetch_chain(self._expiry)
        self._last_chain = chain_df

        if df_5m.empty:
            self._log_event("No 5-min bar data received — skip")
            return

        spot = float(df_5m["close"].iloc[-1])
        self._log_event(f"NIFTY spot={spot:.0f}, bars={len(df_5m)}, expiry={self._expiry}")

        if _in_entry_window():
            await self._process_signals(df_5m, chain_df, self._expiry)

        self.broadcast({
            "type": "strategy_status",
            "portfolio": self.portfolio.summary(),
            "cycle": self._loop_count,
            "ts": now.isoformat(),
        })

    async def start(self):
        if self._running:
            return
        self._running = True
        self._log_event(f"Strategy loop started — {'SANDBOX' if self.sandbox else 'LIVE'} mode")
        while self._running:
            try:
                await self._run_cycle()
            except Exception as e:
                logger.error("[LOOP] Unhandled error in cycle: %s", e, exc_info=True)
                self._log_event(f"Cycle error: {e}", "ERROR")
            await asyncio.sleep(LOOP_INTERVAL_S)

    def stop(self):
        self._running = False
        self._log_event("Strategy loop stopped")

    def status(self) -> dict:
        return {
            "running": self._running,
            "sandbox": self.sandbox,
            "loop_count": self._loop_count,
            "expiry": self._expiry,
            "portfolio": self.portfolio.summary(),
            "recent_events": self._events[-20:],
        }
