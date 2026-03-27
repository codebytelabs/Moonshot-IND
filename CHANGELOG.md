# Changelog

All notable changes to MoonshotX are documented in this file.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

---

## [0.3.0] — 2026-03-27

### Added
- **Orphan stop order cleanup** — cancels any existing open stop-sell orders for a ticker before placing a new one, preventing stale stops from accumulating on Alpaca
- **Relaxed momentum gate for high-conviction picks** — stocks with conviction ≥ 0.80 now pass momentum checks with relaxed thresholds (1/2 up bars instead of 2/2 in fear, halved price-vs-avg threshold, skip red-candle and local-top checks)
- **Blocked tickers list** — `_BLOCKED_TICKERS` set in `scanner.py` filters out known-bad symbols (`SQ`, `NVD`, `FB`) during both watchlist scans and dynamic discovery
- **Daily market comparison logger** — `trading/market_compare.py` auto-runs after EOD force-close, fetches returns for SPY, QQQ, DIA, IWM, SMH, XLK, XLE, ARKK, compares against portfolio, appends to `PERFORMANCE_LOG.md` at project root
- **Start-of-day equity capture** — records `last_equity` on first market-open loop cycle for accurate daily return calculation
- **`PERFORMANCE_LOG.md`** — persistent daily performance log with markdown table (auto-appended)
- **`CHANGELOG.md`** — this file

### Changed
- `momentum.py` — `confirm_intraday_momentum()` now accepts `conviction` parameter; high-conviction picks bypass red-candle, local-top checks and need fewer up bars
- `loop.py` — passes conviction to momentum gate before sizing; conviction extracted earlier in `_execute_entry()`

### Removed
- `SQ` removed from `BROAD_WATCHLIST` (delisted/renamed)
- `SQ` removed from `SECTOR_MAP` in `correlation.py`

### Fixed
- Duplicate conviction extraction in `_execute_entry()` — now extracted once at the top of the method

---

## [0.2.0] — 2026-03-26 (post-Day 1)

### Added
- **Confidence-based position sizing** — tiered conviction multipliers (0.3x at <0.60 → 2.0x at ≥0.90) replace flat sizing
- **ATR-based default stop loss** — when LLM returns no stop_loss, calculates one from 2× ATR or 5% fallback
- **Per-loop entry counter fix** — `_execute_entry()` now returns `bool`; counter only increments on actual order placement (was counting blocked entries)

### Changed
- `risk.py` — fear regime `size_mult` increased from 0.60 → 0.70; `max_new_per_loop` in fear increased from 1 → 2
- `loop.py` — pre-market queue also respects the boolean return from `_execute_entry()`

### Fixed
- Entry counter bug: `new_this_loop` was incrementing even when entries were blocked by cooldown/momentum gate, causing premature "Per-loop cap reached" messages

---

## [0.1.0] — 2026-03-26 (initial release)

### Added
- **Core trading loop** (`trading/loop.py`) — 60-second cycle with position management every tick, entry scanning every 5 minutes
- **Batched LLM pipeline** (`agents/pipeline.py`) — 2-phase architecture: Phase A (QUICK model screens all candidates in 1 call) → Phase B (DEEP model generates trade plans in 1 call). 99.2% reduction in LLM calls vs per-ticker approach
- **Morning intelligence brief** (`trading/morning_brief.py`) — T-25min pre-market: gathers US futures, international indices, macro indicators (VIX, gold, oil, DXY, yields), news headlines, pre-market movers → 1 DEEP LLM call → trading stance + top picks queue
- **Regime-aware risk management** (`trading/risk.py`) — VIX + Fear & Greed Index → 5 regimes (bull/neutral/fear/choppy/bear_mode/extreme_fear) with per-regime max positions, size multipliers, daily trade caps
- **Position manager** (`trading/position_manager.py`) — trailing stop (+3% activation, 2.5% trail), breakeven stop (+2% → entry+0.3%), partial profit taking (1/3 at +5%, 1/3 at +10%, 1/2 at +20%), quick reversal exit (≥1.5% down in 30 min), momentum fade exit, regime-adaptive stale exit, regime downgrade exit
- **Re-entry cooldown** — 2-hour block after loss exits (max_loss, quick_reversal, momentum_fade), persisted to MongoDB
- **Intraday momentum gate** (`trading/momentum.py`) — 5-min bar analysis: price direction, price-vs-average, volume collapse, candle color, local-top detection (all regime-aware)
- **Correlation guard** (`trading/correlation.py`) — sector map (~80 tickers → 12 sectors), regime-dependent sector concentration limits
- **Earnings blackout** (`trading/earnings.py`) — blocks entry 2 days before / 1 day after earnings via yfinance calendar API
- **Dynamic universe scanner** (`trading/scanner.py`) — seed universe of 13 mega-caps + broad watchlist of 70+ tickers, self-updating discovery via momentum/volume spikes, Bayesian scoring, quality filters (min price, min dollar volume, pump-and-dump cap, leveraged ETF blocklist)
- **Alpaca client** (`trading/alpaca_client.py`) — full REST wrapper: market orders, stop orders, partial closes, order cancellation, snapshot data, intraday bars, clock, account
- **OpenRouter LLM shim** (`emergentintegrations/llm/chat.py`) — `requests`-based client (not openai SDK) for OpenRouter compatibility
- **FastAPI backend** (`server.py`) — REST API + WebSocket live feed, 15+ endpoints for dashboard, positions, trades, regime, universe, performance, agent logs, morning brief, config
- **React frontend** — Dashboard (live NAV chart, P&L, loop status), Positions (open + management state), Agent Brain (LLM decision logs), Performance (trade history, metrics), Universe (scanned rankings), Settings
- **Infrastructure scripts** — `restart_all.sh` (kill + restart all), `start_backend.sh`, `start_frontend.sh`
- **Test suite** — `__tests__/test_batch_pipeline.py` (6 tests: compact data, JSON parsing, schema, savings math)

### Models
- **QUICK**: `google/gemini-2.5-flash-lite-preview-09-2025` (quality 7.7/10, latency 2.9s, $0.00004/call)
- **DEEP**: `anthropic/claude-haiku-4-5` (quality 9.8/10, latency 6.3s, $0.0013/call)
- **Provider**: OpenRouter

### Performance (Day 1 — 2026-03-26)
- **MoonshotX: -0.58%** vs SPY -1.79%, QQQ -2.39%, DIA -1.04%, SMH -4.56%
- Outperformed all major indices in a broad market selloff
- Regime: fear (VIX 27.06, F&G 30.2)
- Portfolio: ~$138,000 (Alpaca paper)
- LLM cost: ~$0.00134/loop (2 calls/loop)
