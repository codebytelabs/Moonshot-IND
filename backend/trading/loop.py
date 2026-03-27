"""Main trading loop — India/NSE edition.

Key India-specific changes vs MoonshotX US:
  • IST (Asia/Kolkata) timezone throughout
  • NSE market hours: 09:15–15:30 IST
  • Force squareoff at 15:10 IST (MIS auto-squares at 15:20; we close 10 min early)
  • Pre-market brief at 08:55 IST (T-20 before open)
  • KiteSessionManager for daily token refresh before market hours
  • GTTManager for stop-loss / take-profit instead of Alpaca stop orders
  • Results gate (block_entry_for_results) replaces US earnings blackout
"""
import asyncio
import logging
from datetime import datetime, timezone, date, time as dtime
from zoneinfo import ZoneInfo
from trading.position_manager import PositionManager
from trading.correlation import can_add_to_sector, get_concentration_summary
from trading.results import block_entry_for_results
from trading.momentum import confirm_intraday_momentum
from trading.morning_brief import run_morning_brief
from trading.market_compare import log_daily_comparison
from trading.scanner import BROAD_WATCHLIST

logger = logging.getLogger("moonshotx.loop")

IST = ZoneInfo("Asia/Kolkata")

# ── NSE market timing constants (IST) ────────────────────────────────────
NSE_OPEN      = dtime(9, 15, tzinfo=IST)
NSE_CLOSE     = dtime(15, 30, tzinfo=IST)
FORCE_SQ_TIME = dtime(15, 10, tzinfo=IST)  # MIS auto-squares at 15:20; we close 10 min early
PRE_BRIEF_TIME = dtime(8, 55, tzinfo=IST)  # run morning brief ~20 min before open

PRE_MARKET_WINDOW_MINS  = 25   # minutes before open to run pre-market scan
PRE_MARKET_CANDIDATES   = 10   # how many tickers to pre-analyze
EOD_NO_ENTRY_MINS       = 30   # block new entries within 30 min of NSE close
ENTRY_SCAN_INTERVAL_MINS = 5   # only scan for new entries every 5 min


def _now_ist() -> datetime:
    return datetime.now(IST)


def _is_nse_open() -> bool:
    """Return True if NSE is currently open (Mon–Fri 09:15–15:30 IST)."""
    now = _now_ist()
    if now.weekday() >= 5:   # Saturday=5, Sunday=6
        return False
    t = now.time().replace(tzinfo=IST)
    return NSE_OPEN <= t < NSE_CLOSE


def _mins_to_nse_open() -> float:
    """Minutes until next NSE open. Negative if already open."""
    now = _now_ist()
    today_open = now.replace(hour=9, minute=15, second=0, microsecond=0)
    delta = (today_open - now).total_seconds() / 60
    return delta


def _mins_to_nse_close() -> float:
    """Minutes until today's NSE close. Negative if already closed."""
    now = _now_ist()
    today_close = now.replace(hour=15, minute=30, second=0, microsecond=0)
    delta = (today_close - now).total_seconds() / 60
    return delta


def _past_force_sq_time() -> bool:
    """True if current IST time is past 15:10 (force-squareoff window)."""
    now = _now_ist()
    sq = now.replace(hour=15, minute=10, second=0, microsecond=0)
    return now >= sq


class TradingLoop:
    def __init__(self, db, kite, pipeline, regime_manager, scanner, risk_manager, broadcast_fn):
        self.db = db
        self.kite = kite           # KiteBroker instance (Alpaca-compatible interface)
        self.pipeline = pipeline
        self.regime = regime_manager
        self.scanner = scanner
        self.risk = risk_manager
        self.broadcast = broadcast_fn
        self.position_mgr = PositionManager(kite, db, broadcast_fn)
        self.loop_count = 0
        self._premarket_queue: list = []    # pre-approved entries ready to fire at open
        self._premarket_date: date = None   # date we last ran pre-market scan
        self._last_scan_time = None         # last time we ran entry scanning (5-min gate)
        self._eod_compare_date: date = None # date we last ran daily market comparison
        self._sod_equity: float = 0.0       # start-of-day equity for comparison
        self._token_refreshed_date: date = None  # date of last successful token refresh

    async def run(self, state):
        """Main loop — runs every 60s while state.is_running is True."""
        logger.info("[LOOP] MoonshotX-IND trading loop started (IST timezone, NSE hours)")
        await self.position_mgr.load_cooldowns()
        while state.is_running:
            try:
                await self._maybe_refresh_token()
                await self._cycle(state)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[LOOP] Cycle error: {e}")
            await asyncio.sleep(60)
        logger.info("[LOOP] Trading loop stopped")

    async def _maybe_refresh_token(self):
        """Refresh Kite access token once per day at ~08:00 IST (before market open)."""
        now = _now_ist()
        today = now.date()
        if self._token_refreshed_date == today:
            return
        if now.hour < 8:
            return   # too early — wait until 08:xx IST
        try:
            from broker.kite_session import KiteSessionManager
            session_mgr = KiteSessionManager()
            await session_mgr.assert_valid()
            self._token_refreshed_date = today
            logger.info("[LOOP] Kite session verified/refreshed for today")
        except Exception as e:
            logger.warning(f"[LOOP] Token refresh check failed: {e}")

    async def _cycle(self, state):
        self.loop_count += 1
        state.loop_count = self.loop_count
        now_ist = _now_ist()
        logger.info(f"[LOOP] #{self.loop_count} — {now_ist.strftime('%H:%M IST')}")

        # ── 1. NSE market clock (IST-native, no broker API call) ───────────────────
        is_open        = _is_nse_open()
        mins_to_open   = _mins_to_nse_open()
        mins_to_close  = _mins_to_nse_close()

        # ── 2. Get regime ─────────────────────────────────────────────────────────────────
        regime_data = await self.regime.get_current()
        regime = regime_data.get("regime", "neutral")
        state.regime = regime

        await self.db.regime_history.insert_one({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "regime": regime,
            **{k: v for k, v in regime_data.items() if k not in ("updated_at",)},
        })

        # ── 3. Update account info from Kite ───────────────────────────────────────
        account = await self.kite.get_account()
        portfolio_value = float(account.get("portfolio_value", 0))
        equity = float(account.get("equity", 0))
        if portfolio_value > 0:
            self.risk.set_capital(
                initial=float(account.get("last_equity", portfolio_value)),
                current=equity,
            )

        if portfolio_value > 0:
            await self.db.nav_snapshots.insert_one({
                "ts": datetime.now(timezone.utc).isoformat(),
                "ist": now_ist.isoformat(),
                "value": portfolio_value,
                "equity": equity,
                "regime": regime,
            })

        # ── 4. Sync positions from Kite ───────────────────────────────────────────
        await self._sync_positions()

        # ── 5. Broadcast loop tick ─────────────────────────────────────────────────────
        self.broadcast({
            "type": "loop_tick",
            "loop_count": self.loop_count,
            "regime": regime,
            "is_market_open": is_open,
            "portfolio_value": portfolio_value,
            "regime_data": regime_data,
            "risk_stats": self.risk.get_stats(),
            "llm_cost_today": round(self.pipeline.llm_cost_today, 4),
            "ist_time": now_ist.strftime("%H:%M IST"),
            "ts": datetime.now(timezone.utc).isoformat(),
        })

        if not is_open:
            # ── Pre-market scan at T-25 min before NSE open ────────────────────────
            today = now_ist.date()
            if 0 < mins_to_open <= PRE_MARKET_WINDOW_MINS and self._premarket_date != today:
                await self._pre_market_scan(regime, regime_data, portfolio_value)
                self._premarket_date = today
            logger.info(f"[LOOP] NSE closed — regime={regime}, {mins_to_open:.0f}min to open")
            return

        # ── 5a. Capture start-of-day equity (first open cycle) ──────────────────
        if self._sod_equity <= 0:
            self._sod_equity = float(account.get("last_equity", portfolio_value))
            logger.info(f"[SOD] Start-of-day equity: ₹{self._sod_equity:,.2f}")

        # ── 5b. NSE force squareoff at 15:10 IST ───────────────────────────────
        if _past_force_sq_time():
            positions = await self.kite.get_positions()
            if positions:
                logger.warning(
                    f"[EOD] 15:10 IST force squareoff — closing {len(positions)} MIS positions"
                )
                await self.kite.cancel_all_orders()
                await self.kite.close_all_positions()
                for pos in positions:
                    sym = pos.get("symbol", "")
                    await self.db.positions.update_one(
                        {"ticker": sym, "status": "open"},
                        {"$set": {
                            "status": "closed",
                            "close_reason": "nse_force_squareoff_15:10",
                            "closed_at": datetime.now(timezone.utc).isoformat(),
                        }},
                    )
                self.broadcast({"type": "eod_close", "count": len(positions), "ts": datetime.now(timezone.utc).isoformat()})

            # ── Daily market comparison (once per day) ───────────────────────
            today = now_ist.date()
            if self._eod_compare_date != today:
                self._eod_compare_date = today
                try:
                    eod_account = await self.kite.get_account()
                    end_equity = float(eod_account.get("equity", portfolio_value))
                    start_eq   = self._sod_equity if self._sod_equity > 0 else end_equity
                    summary = await log_daily_comparison(
                        start_equity=start_eq,
                        end_equity=end_equity,
                        regime=regime,
                    )
                    await self.db.daily_comparisons.insert_one(summary)
                    self.broadcast({"type": "daily_comparison", **summary})
                    logger.info(f"[EOD] Comparison logged: MoonshotX-IND={summary.get('portfolio_return_pct')}%")
                    self._sod_equity = 0.0   # reset for tomorrow
                except Exception as e:
                    logger.error(f"[EOD] Daily comparison failed: {e}")
            return

        # ── 5c. Manage open positions (trailing stops, partials, stale) ──
        await self.position_mgr.manage_positions(regime)

        # ── 6. Risk check ──────────────────────────────────────────────────────────
        can_trade, reason = self.risk.can_trade(regime)
        if not can_trade:
            logger.info(f"[LOOP] Risk block: {reason}")
            return

        # ── 7. Get open positions + pending orders (prevent duplicate buys) ───
        open_positions = await self.kite.get_positions()
        open_count = len(open_positions)
        open_tickers = {p.get("symbol", "") for p in open_positions}
        pending_orders = await self.kite.get_orders(status="open")
        for o in pending_orders:
            if o.get("side") == "buy":
                open_tickers.add(o.get("symbol", ""))
        if pending_orders:
            pending_buys = [o.get("symbol") for o in pending_orders if o.get("side") == "buy"]
            if pending_buys:
                logger.info(f"[LOOP] Pending buy orders blocking re-entry: {pending_buys}")

        # ── 7a. Block new entries near EOD ────────────────────────────────
        if mins_to_close is not None and mins_to_close <= EOD_NO_ENTRY_MINS:
            logger.info(f"[EOD] {mins_to_close:.0f}min to close — no new entries")
            return

        max_pos = self.risk.max_positions(regime, portfolio_value)
        if not self.risk.can_add_position(regime, open_count, portfolio_value):
            logger.info(f"Max positions ({max_pos}) for regime {regime}: {open_count} open")
            return

        # ── 7b. Entry scan rate limiter: only scan every 5 min ────────────────
        now_utc = datetime.now(timezone.utc)
        if self._last_scan_time is not None:
            mins_since_scan = (now_utc - self._last_scan_time).total_seconds() / 60
            if mins_since_scan < ENTRY_SCAN_INTERVAL_MINS:
                logger.info(f"Entry scan skipped — {mins_since_scan:.1f}min since last scan (gate={ENTRY_SCAN_INTERVAL_MINS}min)")
                return

        # ── 7b. Fire pre-market queue first (entries pre-approved before open) ──
        if self._premarket_queue:
            logger.info(f"[PRE-MARKET] Executing {len(self._premarket_queue)} pre-approved entries")
            fired = []
            for entry in list(self._premarket_queue):
                if not self.risk.can_add_position(regime, open_count, portfolio_value):
                    break
                ticker = entry["ticker"]
                if ticker in open_tickers:
                    continue
                sector_ok, _ = can_add_to_sector(ticker, open_positions, regime)
                if not sector_ok:
                    continue
                if await block_entry_for_results(ticker):  # India results gate
                    continue
                executed = await self._execute_entry(entry, open_count, open_positions, portfolio_value, regime)
                if executed:
                    open_count += 1
                    open_tickers.add(ticker)
                    fired.append(ticker)
            self._premarket_queue = [r for r in self._premarket_queue if r["ticker"] not in fired]
            if fired:
                logger.info(f"[PRE-MARKET] Fired entries: {fired}")

        if not self.risk.can_add_position(regime, open_count, portfolio_value):
            return

        # ── 8. Scan universe (dynamic count based on regime + portfolio) ─
        n_candidates = self.risk.candidates_to_scan(regime, portfolio_value)
        candidates = await self.scanner.get_top_candidates(n=n_candidates, min_bayesian=0.45)
        logger.info(f"Scanning {len(candidates)}/{n_candidates} candidates (max_pos={max_pos}, regime={regime}, pv=${portfolio_value:,.0f})")

        can_trade, trade_reason = self.risk.can_trade(regime)
        if not can_trade:
            logger.info(f"Risk block: {trade_reason}")
            return

        # ── 9. Pre-filter candidates (NO LLM calls — fast local checks) ──
        batch_payload = []
        for ticker in candidates:
            if ticker in open_tickers:
                continue
            sector_ok, sector_reason = can_add_to_sector(ticker, open_positions, regime)
            if not sector_ok:
                logger.info(f"[SCAN] Sector block {ticker}: {sector_reason}")
                continue
            if await block_entry_for_results(ticker):   # India results gate
                logger.info(f"[SCAN] Results blackout: {ticker}")
                continue
            md = await self.scanner.get_ticker_data(ticker)
            if md.get("error"):
                continue
            md["regime"] = regime
            md["india_vix"] = regime_data.get("india_vix", 16)
            md["fear_greed"] = regime_data.get("fear_greed", 50)
            batch_payload.append({
                "ticker": ticker,
                "md": md,
                "bayesian_score": md.get("bayesian_score", 0.5),
            })

        if not batch_payload:
            logger.info("No candidates passed pre-filter")
            return

        logger.info(f"Pre-filter passed {len(batch_payload)}/{len(candidates)} candidates — sending to batched pipeline (2 LLM calls)")

        # ── 10. Batched pipeline (2 LLM calls for ALL candidates) ─────────
        portfolio_context = {
            "open_positions": open_count,
            "daily_pnl": self.risk.daily_pnl,
            "regime": regime,
            "portfolio_value": portfolio_value,
        }
        results = await self.pipeline.run_batch(
            candidates_with_data=batch_payload,
            regime=regime,
            regime_data=regime_data,
            portfolio_context=portfolio_context,
        )
        self._last_scan_time = datetime.now(timezone.utc)   # update scan gate timestamp

        # ── 11. Save logs + execute approved entries (capped per loop) ────
        max_new = self.risk.max_new_per_loop(regime)
        new_this_loop = 0
        for result in results:
            await self.db.agent_logs.insert_one({
                **result,
                "created_at": datetime.now(timezone.utc).isoformat(),
            })
            if result.get("decision") == "APPROVE":
                if new_this_loop >= max_new:
                    logger.info(f"Per-loop cap reached ({max_new} in {regime}) — deferring remaining approvals")
                    break
                if not self.risk.can_add_position(regime, open_count, portfolio_value):
                    logger.info(f"Max positions reached ({open_count}/{max_pos}) — no more entries")
                    break
                ticker = result["ticker"]
                md = next((c["md"] for c in batch_payload if c["ticker"] == ticker), {})
                entry_data = {
                    "ticker": ticker,
                    "result": result,
                    "md": md,
                    "portfolio_value": portfolio_value,
                    "regime": regime,
                }
                executed = await self._execute_entry(entry_data, open_count, open_positions, portfolio_value, regime)
                if executed:
                    open_count += 1
                    open_tickers.add(ticker)
                    new_this_loop += 1

    async def _execute_entry(self, entry_data: dict, open_count: int, open_positions: list, portfolio_value: float, regime: str) -> bool:
        """Submit a MIS market order + GTT stop/target for an approved NSE entry."""
        ticker = entry_data["ticker"]
        result = entry_data.get("result", {})
        md = entry_data.get("md", {})
        plan = result.get("plan", {})

        conviction = result.get("verdict", {}).get("conviction_score", 0.7)

        in_cooldown, cooldown_reason = self.position_mgr.is_in_cooldown(ticker)
        if in_cooldown:
            logger.info(f"[ENTRY] BLOCKED {ticker}: {cooldown_reason}")
            return False

        confirmed, reason = await confirm_intraday_momentum(self.kite, ticker, regime, conviction=conviction)
        if not confirmed:
            logger.info(f"[ENTRY] BLOCKED {ticker}: momentum failed — {reason}")
            return False

        entry_price = float(plan.get("entry_price", md.get("price", 0)))
        stop_loss   = float(plan.get("stop_loss", 0))
        take_profit = float(plan.get("take_profit", 0))

        # ── Default stop from ATR if LLM didn't provide one ────────────────────
        if stop_loss <= 0 or stop_loss >= entry_price:
            atr = float(md.get("atr", 0))
            atr_pct = float(md.get("atr_pct", 0))
            if atr > 0:
                stop_loss = round(entry_price - 2.0 * atr, 2)
            elif atr_pct > 0:
                stop_loss = round(entry_price * (1 - 2.0 * atr_pct / 100), 2)
            else:
                stop_loss = round(entry_price * 0.97, 2)   # 3% default (NSE: tighter)
            logger.info(f"[ENTRY] {ticker}: ATR-based SL=₹{stop_loss:.2f}")

        if take_profit <= 0 or take_profit <= entry_price:
            risk = entry_price - stop_loss
            take_profit = round(entry_price + 2.0 * risk, 2)  # 2:1 RRR default

        size = self.risk.calculate_position_size(
            portfolio_value=portfolio_value,
            entry_price=entry_price,
            stop_price=stop_loss,
            regime=regime,
            confidence=conviction,
        )
        if size <= 0:
            logger.info(f"[ENTRY] BLOCKED {ticker}: size=0 (px=₹{entry_price:.2f}, sl=₹{stop_loss:.2f})")
            return False

        # ── Place MIS market order via Kite ──────────────────────────────────
        order = await self.kite.submit_market_order(symbol=ticker, qty=size, side="buy")
        if not order.get("id"):
            return False

        # ── Place GTT stop-loss + target (replaces Alpaca bracket order) ───
        gtt_id = None
        if hasattr(self.kite, "gtt") and self.kite.gtt:
            try:
                gtt_id = await self.kite.gtt.place_oco(
                    symbol=ticker,
                    qty=size,
                    entry_price=entry_price,
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                )
            except Exception as e:
                logger.warning(f"[ENTRY] GTT placement failed for {ticker}: {e}")

        trade_doc = {
            "ticker": ticker,
            "decision_id": result.get("decision_id"),
            "order_id": order.get("id"),
            "gtt_id": gtt_id,
            "entry_price": entry_price,
            "shares": size,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "regime": regime,
            "status": "open",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "ist_time": _now_ist().strftime("%H:%M IST"),
        }
        await self.db.positions.insert_one(trade_doc)
        self.risk.record_trade(0)
        logger.info(
            f"[ENTRY] {ticker} x{size} @ ₹{entry_price:.2f} "
            f"(SL=₹{stop_loss:.2f}, TP=₹{take_profit:.2f}, conv={conviction:.2f}, gtt={gtt_id})"
        )
        self.broadcast({
            "type": "trade_executed",
            "ticker": ticker,
            "size": size,
            "price": entry_price,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "conviction": conviction,
            "decision_id": result.get("decision_id"),
            "ts": datetime.now(timezone.utc).isoformat(),
        })
        return True

    async def _pre_market_scan(self, regime: str, regime_data: dict, portfolio_value: float):
        """Run at 08:55 IST: India morning brief + candidate queue before NSE open."""
        logger.info("[PRE-MARKET] ════════════════════════════════════════════")
        logger.info("[PRE-MARKET] India morning brief + NSE candidate scan starting")
        self.broadcast({"type": "premarket_scan_start", "ts": datetime.now(timezone.utc).isoformat()})
        try:
            open_positions = await self.kite.get_positions()
            open_tickers = {p.get("symbol", "") for p in open_positions}
            open_count = len(open_positions)
            max_pos = self.risk.max_positions(regime, portfolio_value)
            slots_available = max(0, max_pos - open_count)
            if slots_available == 0:
                logger.info("[PRE-MARKET] No slots available — skipping scan")
                return

            # ── STEP 1: India morning intelligence brief (1 DEEP LLM call) ───────
            brief = await run_morning_brief(
                kite_client=self.kite,
                pipeline=self.pipeline,
                regime_data=regime_data,
                watchlist=list(BROAD_WATCHLIST),
            )

            if brief:
                await self.db.morning_briefs.insert_one({
                    **{k: v for k, v in brief.items() if k != "raw_intel"},
                    "raw_intel_summary": {
                        "headlines_count": len(brief.get("raw_intel", {}).get("headlines", [])),
                        "results_today": len(brief.get("raw_intel", {}).get("results_today", [])),
                    },
                    "created_at": datetime.now(timezone.utc).isoformat(),
                })
                self.broadcast({
                    "type": "morning_brief",
                    "stance": brief.get("trading_stance", "normal"),
                    "expected_regime": brief.get("expected_regime", regime),
                    "hot_sectors": brief.get("hot_sectors", []),
                    "top_picks": [p.get("symbol") for p in brief.get("top_picks", [])],
                    "brief_summary": brief.get("brief_summary", ""),
                    "ts": datetime.now(timezone.utc).isoformat(),
                })

            # Extract brief signals
            brief_stance      = brief.get("trading_stance", "normal") if brief else "normal"
            brief_top_picks   = {p["symbol"] for p in brief.get("top_picks", [])} if brief else set()
            brief_avoid       = set(brief.get("avoid_picks", [])) if brief else set()
            brief_hot_sectors = brief.get("hot_sectors", []) if brief else []

            # If brief says sit_out, don't queue anything
            if brief_stance == "sit_out":
                logger.warning("[PRE-MARKET] Morning brief says SIT OUT — no entries queued")
                return

            # ── STEP 2: Get candidates (boost top_picks, cap list) ────────────
            # Pull more candidates than usual so brief's top_picks are included
            n_candidates = max(PRE_MARKET_CANDIDATES, len(brief_top_picks) + 5)
            candidates = await self.scanner.get_top_candidates(
                n=n_candidates, min_bayesian=0.45
            )

            # Merge brief top picks into candidate list (front of queue = higher priority)
            all_candidates = list(brief_top_picks) + [c for c in candidates if c not in brief_top_picks]
            # Remove avoid picks
            all_candidates = [c for c in all_candidates if c not in brief_avoid]

            # ── STEP 3: Pre-filter (no LLM) ───────────────────────────────────────────────
            batch_payload = []
            for ticker in all_candidates:
                if ticker in open_tickers:
                    continue
                sector_ok, _ = can_add_to_sector(ticker, open_positions, regime)
                if not sector_ok:
                    continue
                if await block_entry_for_results(ticker):  # India results gate
                    logger.info(f"[PRE-MARKET] Results block: {ticker}")
                    continue
                md = await self.scanner.get_ticker_data(ticker)
                if md.get("error"):
                    continue
                md["regime"] = regime
                md["india_vix"] = regime_data.get("india_vix", 16)
                md["fear_greed"] = regime_data.get("fear_greed", 50)
                # Tag brief top picks for pipeline awareness
                md["brief_top_pick"] = ticker in brief_top_picks
                batch_payload.append({
                    "ticker": ticker,
                    "md": md,
                    "bayesian_score": md.get("bayesian_score", 0.5),
                })
                if len(batch_payload) >= PRE_MARKET_CANDIDATES:
                    break

            if not batch_payload:
                logger.info("[PRE-MARKET] No candidates passed pre-filter")
                return

            # ── STEP 4: Batch pipeline (2 LLM calls) with morning brief context ──
            portfolio_context = {
                "open_positions": open_count,
                "daily_pnl": self.risk.daily_pnl,
                "regime": regime,
                "portfolio_value": portfolio_value,
                # Pass morning brief signals as extra context
                "morning_stance": brief_stance,
                "hot_sectors": brief_hot_sectors,
                "session_sentiment": brief.get("session_sentiment", "") if brief else "",
                "key_themes": brief.get("key_themes", []) if brief else [],
                "macro_risks": brief.get("macro_risks", []) if brief else [],
            }
            results = await self.pipeline.run_batch(
                candidates_with_data=batch_payload,
                regime=regime,
                regime_data=regime_data,
                portfolio_context=portfolio_context,
            )

            approved = []
            for result in results:
                await self.db.agent_logs.insert_one({
                    **result,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "context": "premarket",
                })
                if result.get("decision") == "APPROVE" and len(approved) < slots_available:
                    ticker = result["ticker"]
                    md = next((c["md"] for c in batch_payload if c["ticker"] == ticker), {})
                    approved.append({"ticker": ticker, "result": result, "md": md})
                    source = "📌 brief pick" if ticker in brief_top_picks else "scanner"
                    logger.info(f"[PRE-MARKET] Queued: {ticker} ({source}, conv={result.get('verdict', {}).get('conviction_score', '?')})")

            self._premarket_queue = approved
            logger.info(f"[PRE-MARKET] Complete — {len(approved)} entries queued | "
                        f"stance={brief_stance} | {len(batch_payload)} candidates, 3 LLM calls total")
            self.broadcast({
                "type": "premarket_scan_complete",
                "queued": [e["ticker"] for e in approved],
                "scanned": len(all_candidates),
                "stance": brief_stance,
                "top_picks": list(brief_top_picks),
                "ts": datetime.now(timezone.utc).isoformat(),
            })
        except Exception as e:
            logger.error(f"[PRE-MARKET] Scan error: {e}")

    async def _sync_positions(self):
        """Sync Kite positions with MongoDB."""
        kite_positions = await self.kite.get_positions()
        kite_symbols = {p.get("symbol") for p in kite_positions}

        for pos in kite_positions:
            symbol = pos.get("symbol")
            unrealized_pl = float(pos.get("unrealized_pl", 0))
            current_price = float(pos.get("current_price", 0))
            avg_entry = float(pos.get("avg_entry_price", 0))
            qty = int(pos.get("qty", 0))

            await self.db.positions.update_one(
                {"ticker": symbol, "status": "open"},
                {"$set": {
                    "current_price": current_price,
                    "unrealized_pnl": round(unrealized_pl, 2),
                    "shares": qty,
                    "entry_price": avg_entry,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                    "ist_time": _now_ist().strftime("%H:%M IST"),
                }},
                upsert=True,
            )

        await self.db.positions.update_many(
            {"ticker": {"$nin": list(kite_symbols)}, "status": "open"},
            {"$set": {"status": "closed", "closed_at": datetime.now(timezone.utc).isoformat()}},
        )

        self.broadcast({
            "type": "position_update",
            "positions": kite_positions,
            "ts": datetime.now(timezone.utc).isoformat(),
        })
