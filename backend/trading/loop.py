"""Main trading loop — orchestrates all components."""
import asyncio
import logging
from datetime import datetime, timezone, date
from trading.position_manager import PositionManager
from trading.correlation import can_add_to_sector, get_concentration_summary
from trading.earnings import is_in_blackout
from trading.momentum import confirm_intraday_momentum
from trading.morning_brief import run_morning_brief
from trading.scanner import BROAD_WATCHLIST

logger = logging.getLogger("moonshotx.loop")

PRE_MARKET_WINDOW_MINS = 25   # minutes before open to run pre-market scan (includes morning brief)
PRE_MARKET_CANDIDATES = 10    # how many tickers to pre-analyze
EOD_CLOSE_MINS = 15           # force-close all positions within 15 min of market close
EOD_NO_ENTRY_MINS = 30        # block new entries within 30 min of market close
ENTRY_SCAN_INTERVAL_MINS = 5  # only scan for new entries every 5 min (PM runs every 60s)


class TradingLoop:
    def __init__(self, db, alpaca, pipeline, regime_manager, scanner, risk_manager, broadcast_fn):
        self.db = db
        self.alpaca = alpaca
        self.pipeline = pipeline
        self.regime = regime_manager
        self.scanner = scanner
        self.risk = risk_manager
        self.broadcast = broadcast_fn
        self.position_mgr = PositionManager(alpaca, db, broadcast_fn)
        self.loop_count = 0
        self._premarket_queue: list = []   # pre-approved entries ready to fire at open
        self._premarket_date: date = None  # date we last ran pre-market scan
        self._last_scan_time = None        # last time we ran entry scanning (5-min gate)

    async def run(self, state):
        """Main loop — runs every 60s while state.is_running is True."""
        logger.info("Trading loop started")
        await self.position_mgr.load_cooldowns()
        while state.is_running:
            try:
                await self._cycle(state)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Loop cycle error: {e}")
            await asyncio.sleep(60)
        logger.info("Trading loop stopped")

    async def _cycle(self, state):
        self.loop_count += 1
        state.loop_count = self.loop_count
        logger.info(f"Loop #{self.loop_count} starting")

        # ── 1. Market clock check ──────────────────────────────────────────
        clock = await self.alpaca.get_clock()
        is_open = clock.get("is_open", False)

        # ── 2. Get regime ──────────────────────────────────────────────────
        regime_data = await self.regime.get_current()
        regime = regime_data.get("regime", "neutral")
        state.regime = regime

        # Update regime in DB
        await self.db.regime_history.insert_one({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "regime": regime,
            **{k: v for k, v in regime_data.items() if k not in ("updated_at",)},
        })

        # ── 3. Update account info ─────────────────────────────────────────
        account = await self.alpaca.get_account()
        portfolio_value = float(account.get("portfolio_value", 0))
        equity = float(account.get("equity", 0))
        if portfolio_value > 0:
            self.risk.set_capital(
                initial=float(account.get("last_equity", portfolio_value)),
                current=equity,
            )

        # ── 3b. Store portfolio NAV snapshot ──────────────────────────────
        if portfolio_value > 0:
            await self.db.nav_snapshots.insert_one({
                "ts": datetime.now(timezone.utc).isoformat(),
                "value": portfolio_value,
                "equity": equity,
                "regime": regime,
            })

        # ── 4. Sync positions from Alpaca ─────────────────────────────────
        await self._sync_positions()

        # ── 5. Broadcast loop tick ─────────────────────────────────────────
        self.broadcast({
            "type": "loop_tick",
            "loop_count": self.loop_count,
            "regime": regime,
            "is_market_open": is_open,
            "portfolio_value": portfolio_value,
            "regime_data": regime_data,
            "risk_stats": self.risk.get_stats(),
            "llm_cost_today": round(self.pipeline.llm_cost_today, 4),
            "ts": datetime.now(timezone.utc).isoformat(),
        })

        if not is_open:
            # ── Pre-market scan (T-20 min before open) ───────────────────────
            next_open_str = clock.get("next_open", "")
            if next_open_str:
                next_open_dt = datetime.fromisoformat(next_open_str.replace("Z", "+00:00"))
                mins_to_open = (next_open_dt - datetime.now(timezone.utc)).total_seconds() / 60
                today = datetime.now(timezone.utc).date()
                if 0 < mins_to_open <= PRE_MARKET_WINDOW_MINS and self._premarket_date != today:
                    await self._pre_market_scan(regime, regime_data, portfolio_value)
                    self._premarket_date = today
            logger.info(f"Market closed — regime={regime}, skipping trade entry")
            return

        # ── 5b. EOD force-close check ────────────────────────────────────
        next_close_str = clock.get("next_close", "")
        mins_to_close = None
        if next_close_str:
            next_close_dt = datetime.fromisoformat(next_close_str.replace("Z", "+00:00"))
            mins_to_close = (next_close_dt - datetime.now(timezone.utc)).total_seconds() / 60

        if mins_to_close is not None and mins_to_close <= EOD_CLOSE_MINS:
            positions = await self.alpaca.get_positions()
            if positions:
                logger.warning(f"[EOD] {mins_to_close:.0f}min to close — force-closing {len(positions)} positions")
                await self.alpaca.cancel_all_orders()
                await self.alpaca.close_all_positions()
                for pos in positions:
                    sym = pos.get("symbol", "")
                    await self.db.positions.update_one(
                        {"ticker": sym, "status": "open"},
                        {"$set": {
                            "status": "closed",
                            "close_reason": "eod_force_close",
                            "closed_at": datetime.now(timezone.utc).isoformat(),
                        }},
                    )
                self.broadcast({"type": "eod_close", "count": len(positions), "ts": datetime.now(timezone.utc).isoformat()})
            return

        # ── 5c. Manage open positions (trailing stops, partials, stale) ──
        await self.position_mgr.manage_positions(regime)

        # ── 6. Risk check ──────────────────────────────────────────
        can_trade, reason = self.risk.can_trade(regime)
        if not can_trade:
            logger.info(f"Risk block: {reason}")
            return

        # ── 7. Get open positions + pending orders (prevent duplicate buys) ───
        open_positions = await self.alpaca.get_positions()
        open_count = len(open_positions)
        open_tickers = {p.get("symbol", "") for p in open_positions}
        # Also block tickers with any open/pending buy orders (race condition guard)
        pending_orders = await self.alpaca.get_orders(status="open")
        for o in pending_orders:
            if o.get("side") == "buy":
                open_tickers.add(o.get("symbol", ""))
        if pending_orders:
            pending_buys = [o.get("symbol") for o in pending_orders if o.get("side") == "buy"]
            if pending_buys:
                logger.info(f"Pending buy orders blocking re-entry: {pending_buys}")

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
                in_blackout, _ = await is_in_blackout(ticker)
                if in_blackout:
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
                logger.info(f"Sector block {ticker}: {sector_reason}")
                continue
            in_blackout, blackout_reason = await is_in_blackout(ticker)
            if in_blackout:
                logger.info(f"Earnings blackout {ticker}: {blackout_reason}")
                continue
            md = await self.scanner.get_ticker_data(ticker)
            if md.get("error"):
                continue
            md["regime"] = regime
            md["vix"] = regime_data.get("vix", 20)
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
        """Submit a market order + stop for an approved entry. Returns True if order placed."""
        ticker = entry_data["ticker"]
        result = entry_data.get("result", {})
        md = entry_data.get("md", {})
        plan = result.get("plan", {})

        conviction = result.get("verdict", {}).get("conviction_score", 0.7)

        # ── Re-entry cooldown: don't re-buy stocks we just dumped ──────────
        in_cooldown, cooldown_reason = self.position_mgr.is_in_cooldown(ticker)
        if in_cooldown:
            logger.info(f"Entry BLOCKED for {ticker}: {cooldown_reason}")
            return False

        # ── Intraday momentum gate: only buy if stock is trending UP now ──
        confirmed, reason = await confirm_intraday_momentum(self.alpaca, ticker, regime, conviction=conviction)
        if not confirmed:
            logger.info(f"Entry BLOCKED for {ticker}: intraday momentum failed — {reason}")
            return False

        entry_price = float(plan.get("entry_price", md.get("price", 0)))
        stop_loss = float(plan.get("stop_loss", 0))
        take_profit = float(plan.get("take_profit", 0))

        # ── Default stop from ATR if LLM didn't provide one ─────────────────
        if stop_loss <= 0 or stop_loss >= entry_price:
            atr = float(md.get("atr", 0))
            atr_pct = float(md.get("atr_pct", 0))
            if atr > 0:
                stop_loss = round(entry_price - 2.0 * atr, 2)
            elif atr_pct > 0:
                stop_loss = round(entry_price * (1 - 2.0 * atr_pct / 100), 2)
            else:
                stop_loss = round(entry_price * 0.95, 2)   # 5% default
            logger.info(f"{ticker}: LLM stop missing, using ATR-based default SL=${stop_loss:.2f}")

        size = self.risk.calculate_position_size(
            portfolio_value=portfolio_value,
            entry_price=entry_price,
            stop_price=stop_loss,
            regime=regime,
            confidence=conviction,
        )
        if size <= 0:
            logger.info(f"Entry BLOCKED for {ticker}: position size 0 (px=${entry_price:.2f}, sl=${stop_loss:.2f}, conv={conviction:.2f})")
            return False

        order = await self.alpaca.submit_market_order(symbol=ticker, qty=size, side="buy")
        if not order.get("id"):
            return False

        # ── Cancel any existing stop orders for this ticker first ────────
        existing_stops = await self.alpaca.get_orders_for_symbol(ticker, status="open")
        for old_order in existing_stops:
            if old_order.get("type") == "stop" and old_order.get("side") == "sell":
                cancelled = await self.alpaca.cancel_order(old_order["id"])
                if cancelled:
                    logger.info(f"{ticker}: cancelled orphan stop order {old_order['id'][:8]}")

        stop_order = {}
        if stop_loss > 0 and stop_loss < entry_price:
            stop_order = await self.alpaca.submit_stop_order(symbol=ticker, qty=size, stop_price=stop_loss)

        trade_doc = {
            "ticker": ticker,
            "decision_id": result.get("decision_id"),
            "order_id": order.get("id"),
            "stop_order_id": stop_order.get("id"),
            "entry_price": entry_price,
            "shares": size,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "regime": regime,
            "status": "open",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        await self.db.positions.insert_one(trade_doc)
        self.risk.record_trade(0)
        logger.info(f"Trade entered: {ticker} x{size} @ ~${entry_price:.2f} (SL=${stop_loss:.2f}, TP=${take_profit:.2f}, conv={conviction:.2f})")
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
        """Run T-25min before open: gather overnight intel, analyze macro, queue best candidates."""
        logger.info("[PRE-MARKET] ══════════════════════════════════════════════")
        logger.info("[PRE-MARKET] Morning intelligence brief + candidate scan starting")
        self.broadcast({"type": "premarket_scan_start", "ts": datetime.now(timezone.utc).isoformat()})
        try:
            open_positions = await self.alpaca.get_positions()
            open_tickers = {p.get("symbol", "") for p in open_positions}
            open_count = len(open_positions)
            max_pos = self.risk.max_positions(regime, portfolio_value)
            slots_available = max(0, max_pos - open_count)
            if slots_available == 0:
                logger.info("[PRE-MARKET] No slots available — skipping scan")
                return

            # ── STEP 1: Morning intelligence brief (1 DEEP LLM call) ─────────
            brief = await run_morning_brief(
                alpaca=self.alpaca,
                pipeline=self.pipeline,
                regime_data=regime_data,
                watchlist=BROAD_WATCHLIST,
            )

            # Persist brief to DB for frontend display
            if brief:
                await self.db.morning_briefs.insert_one({
                    **{k: v for k, v in brief.items() if k != "raw_intel"},
                    "raw_intel_summary": {
                        "futures_count": len(brief.get("raw_intel", {}).get("us_futures", {})),
                        "headlines_count": len(brief.get("raw_intel", {}).get("headlines", [])),
                        "movers_count": len(brief.get("raw_intel", {}).get("pre_market_movers", [])),
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

            # ── STEP 3: Pre-filter (no LLM) ──────────────────────────────────
            batch_payload = []
            for ticker in all_candidates:
                if ticker in open_tickers:
                    continue
                sector_ok, _ = can_add_to_sector(ticker, open_positions, regime)
                if not sector_ok:
                    continue
                in_blackout, _ = await is_in_blackout(ticker)
                if in_blackout:
                    continue
                md = await self.scanner.get_ticker_data(ticker)
                if md.get("error"):
                    continue
                md["regime"] = regime
                md["vix"] = regime_data.get("vix", 20)
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
        """Sync Alpaca positions with MongoDB."""
        alpaca_positions = await self.alpaca.get_positions()
        alpaca_symbols = {p.get("symbol") for p in alpaca_positions}

        # Update or close positions in DB
        for pos in alpaca_positions:
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
                }},
                upsert=True,
            )

        # Mark closed positions
        await self.db.positions.update_many(
            {"ticker": {"$nin": list(alpaca_symbols)}, "status": "open"},
            {"$set": {"status": "closed", "closed_at": datetime.now(timezone.utc).isoformat()}},
        )

        self.broadcast({
            "type": "position_update",
            "positions": alpaca_positions,
            "ts": datetime.now(timezone.utc).isoformat(),
        })
