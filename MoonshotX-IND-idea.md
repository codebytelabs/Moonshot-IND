Yes — **MoonshotX-IND should be a MoonshotX-led product, not a DayTraderAI-IND-led rewrite**. The cleanest design is to keep MoonshotX’s agent orchestration, batched LLM pipeline, trading loop, and API/dashboard shape, then import the India-specific broker/auth/session layer and market plumbing from DayTraderAI-IND. 

## Product paper

### Product thesis

MoonshotX is already positioned as an “autonomous AI-powered intraday momentum trading bot” with a morning intelligence brief, a batched intraday decision pipeline, regime-aware risk management, and mandatory end-of-day flattening, so that should remain the core product identity of MoonshotX-IND.   
MoonshotX-IND should therefore be presented as **the Indian-market edition of MoonshotX**, built for NSE/BSE intraday trading through Zerodha/Kite, rather than as a separate product philosophy. 

### Product goal

The goal of MoonshotX-IND is to deliver the same daily operating model MoonshotX already uses: pre-open intelligence generation, recurring intraday scans, active position management, and flat-by-close execution.   
What changes is the market substrate: broker connectivity, session lifecycle, market hours, risk calibration, and data sources must be adapted for India, while the product behavior remains recognizably MoonshotX. 

### Users

The primary user is an advanced retail or small-prop Indian trader who wants a mostly autonomous intraday system with human-readable reasoning, execution discipline, and daily operational repeatability.   
Because MoonshotX already exposes an API/backend and dashboard-oriented structure, MoonshotX-IND should keep that product posture: operator-driven oversight on top of automated trade lifecycle management. 

### Product shape

MoonshotX’s product loop is already strong: a morning brief before the session, a 5-minute entry pipeline, 60-second position management, and forced exit before the session ends.   
MoonshotX-IND should preserve that exact operator experience, only replacing US-session assumptions with Indian-session assumptions like IST timing, NSE cash/F&O constraints, and Zerodha session renewal requirements. 

## Product architecture

### Core identity

MoonshotX-IND should be **90% MoonshotX in product architecture** and **10% DayTraderAI-IND in market adaptation**.   
That means users should still recognize the product as a multi-stage AI trading system built around specialist components, not as a monolithic “one script does everything” bot. 

### Multi-agent stance

MoonshotX already has an `agents/` module and a staged intelligence workflow, so MoonshotX-IND should absolutely remain agentic in design.   
However, it does not need to become a full debate-style TradingAgents replica unless you explicitly want analyst-versus-analyst reasoning, because TradingAgents is positioned as a separate “Multi-Agents LLM Financial Trading Framework,” while MoonshotX already succeeds with a leaner batched decision architecture. [github](https://github.com/TauricResearch/TradingAgents/blob/main/pyproject.toml)

### Positioning

So the best product statement is: **MoonshotX-IND is MoonshotX’s autonomous intraday agent system, localized for India through Zerodha, NSE market structure, and Indian macro/news inputs**.   
That framing is cleaner than saying you are “porting DayTraderAI-IND into MoonshotX,” because the core value, workflow, and operator experience still come from MoonshotX. 

## What stays

### MoonshotX pieces

The following should stay fundamentally unchanged because they define the MoonshotX product itself. 

| Area | Keep from MoonshotX | Reason |
|---|---|---|
| Product identity | Autonomous intraday momentum system  | This is already the clearest market-facing positioning for MoonshotX-IND  |
| AI workflow | Morning intelligence brief plus staged intraday analysis  | This is the core decision engine and should remain the product’s signature behavior  |
| Agent structure | `backend/agents/` stays the control center  | It preserves the agentic architecture instead of collapsing into utility scripts  |
| Trading orchestration | `backend/trading/` stays the execution/risk shell  | This is the right place to swap broker and market adapters without changing product logic  |
| API layer | `backend/server.py` remains the platform entry point  | It keeps the service interface stable while the market backend changes  |
| Risk philosophy | Regime-aware risk, trailing stops, partial profit logic, flat-by-close behavior  | These are product-level behaviors, not US-only behaviors  |

### Why it stays

MoonshotX’s backend is already split into `agents/`, `trading/`, and `server.py`, which is exactly the kind of modular separation you want when localizing a product to a new market.   
Its README also shows the right trading lifecycle abstraction—intelligence, entry screening, risk control, and forced close—so you do not need a new product architecture for India; you need a localized implementation. 

## What comes over

### DayTraderAI-IND pieces

The DayTraderAI-IND imports should be selective and infrastructure-heavy rather than product-defining. 

| Area | Bring from DayTraderAI-IND | Why |
|---|---|---|
| Zerodha auth | `auto_token_refresh.py` and its `ZerodhaAutoToken` flow  | Zerodha access tokens are daily-session dependent, so this is operationally essential  |
| Token fallback tools | `perform_login.py` in `backend/scripts/` plus manual token helpers around the refresh flow  | This gives you backup login and maintenance paths when the primary refresh flow fails  |
| Env/token lifecycle | Automatic `.env` token rewriting in the refresh flow  | This is the safest path to keep the runtime bot aligned with the current session token  |
| Bot restart behavior | Post-refresh process restart logic from the token refresher  | MoonshotX-IND should relaunch the India bot after token refresh just as DayTraderAI-IND relaunches its runtime  |
| India-market adapters | Broker session, order routing, exchange calendar, and NSE constraints as design imports | These are the places where India-specific logic belongs |
| India data posture | India news, India macro inputs, and local market timing as product localization inputs | These adapt the decision engine without changing its architecture |

### Why it comes over

The strongest proven India-specific asset in DayTraderAI-IND is the Zerodha session machinery: the `auto_token_refresh.py` flow logs in, completes TOTP-based 2FA, extracts the request token, generates the access token, writes it back to `.env`, and can restart the bot.   
That means DayTraderAI-IND contributes the **operational bridge to the Indian broker**, while MoonshotX contributes the **decision-making and orchestration framework**. 

## Product boundaries

### What not to import

MoonshotX-IND should not inherit a script-heavy, ops-heavy public surface as its primary architecture.   
In other words, do not let DayTraderAI-IND’s maintenance utilities become the new product center; keep them as internal support tooling under a cleaner MoonshotX-style structure. 

### What not to change

Do not change MoonshotX’s core user promise from “autonomous intraday AI trading system” into “Zerodha automation toolkit.”   
The broker integration is necessary plumbing, but the product customers will care about is still the intelligence engine, trade selection quality, and disciplined risk loop. 

## Technical paper

### System design

MoonshotX-IND should be implemented as a layered system with four tiers: **Agent Layer**, **Market Intelligence Layer**, **Execution Layer**, and **Operations Layer**.   
MoonshotX already gives you the Agent Layer and the broad trading orchestration shell, while DayTraderAI-IND gives you the broker-session mechanics that belong in the Operations and Execution layers. 

### Target architecture

```text
MoonshotX-IND
├── backend/
│   ├── agents/                # stays from MoonshotX
│   ├── trading/               # mostly stays from MoonshotX
│   │   ├── risk_engine.py     # MoonshotX style
│   │   ├── position_manager.py# MoonshotX style
│   │   ├── order_router.py    # India-adapted
│   │   └── regime_india.py    # new India regime adapter
│   ├── broker/
│   │   ├── kite_session.py    # from DayTraderAI-IND concepts
│   │   ├── token_refresh.py   # ported from auto_token_refresh.py
│   │   └── login_fallback.py  # perform_login.py style fallback
│   ├── data/
│   │   ├── india_market_feed.py
│   │   ├── india_news_feed.py
│   │   └── india_universe.py
│   └── server.py              # stays from MoonshotX
```

This keeps the product’s center of gravity in MoonshotX’s service and agent architecture while isolating Indian broker/session logic behind a clean adapter boundary.   
It also prevents token refresh code from leaking across the rest of the system, which will matter for reliability and maintainability. 

### Control flow

MoonshotX describes a daily workflow with a morning brief, repeated entry scanning, continuous position management, and forced close by session end, and that workflow should remain intact in MoonshotX-IND.   
The only major runtime insertion is a **pre-session Zerodha session bootstrap**, where the token manager validates or refreshes the daily session before the trading engine starts. 

### Session management

DayTraderAI-IND’s `auto_token_refresh.py` is a proper session pipeline rather than a one-off helper script.   
It performs login, TOTP-based two-factor authentication, request-token extraction, session generation through KiteConnect, `.env` update, token verification, and optional bot restart, so MoonshotX-IND should expose this as a reusable `KiteSessionManager` service rather than leave it as an external script only. 

### Agent model

MoonshotX-IND should keep MoonshotX’s staged intelligence design and express it as specialized agents such as `MarketContextAgent`, `ScannerAgent`, `DecisionAgent`, and `RiskAgent`.   
If you later want a richer debate system, you can add TradingAgents-style specialist personas, but that should be a second-stage enhancement rather than the baseline architecture. [github](https://github.com/TauricResearch)

### Data adaptation

MoonshotX’s morning brief currently reflects a macro-context-first design, so the Indian port should preserve that pattern but swap the data inputs to Indian equivalents.   
Concretely, US-centric context signals should become India-centric context signals such as India VIX, Nifty/Bank Nifty tone, INR sensitivity, local macro headlines, and Indian sector rotation inputs, while the surrounding prompt-and-decision workflow stays MoonshotX-like. 

### Execution adaptation

Execution is where the largest market-specific change happens, because the India port has to respect Zerodha/Kite sessions, NSE timing, and Indian product/order constraints.   
That means the execution engine should be rewritten as a broker adapter under MoonshotX’s orchestration layer rather than by rewriting the agents themselves. 

## Clear split

### Ownership map

Here is the clearest ownership split for the final product. 

| Product/technical concern | Primary source |
|---|---|
| Product philosophy | MoonshotX  |
| Agent orchestration | MoonshotX  |
| Morning brief workflow | MoonshotX  |
| Intraday scan/deep-analysis loop | MoonshotX  |
| Risk-management flow | MoonshotX  |
| API/backend shell | MoonshotX  |
| Zerodha daily auth/session flow | DayTraderAI-IND  |
| TOTP-based login automation | DayTraderAI-IND  |
| Token fallback/login helper | DayTraderAI-IND  |
| India exchange/broker localization | DayTraderAI-IND concepts + new MoonshotX-IND adapters  |
| Optional richer debate-style analyst layer | Inspired by TradingAgents, not required baseline  [github](https://github.com/TauricResearch) |

### Final product statement

**MoonshotX-IND is MoonshotX’s autonomous intraday AI trading platform, replatformed for India with Zerodha/Kite session management and Indian-market execution adapters sourced from DayTraderAI-IND.**   
So in plain terms: **MoonshotX stays the brain and operating system; DayTraderAI-IND supplies the India wiring, broker ignition, and market-local execution layer.** 

## Build plan

### Phase plan

- Phase 1: Clone MoonshotX structure and preserve `agents/`, `trading/`, and `server.py` as the base service architecture. 
- Phase 2: Port the DayTraderAI-IND token/session stack, starting with `auto_token_refresh.py` and the `perform_login.py` fallback path. 
- Phase 3: Build a `KiteSessionManager` and `KiteOrderRouter` so the rest of the MoonshotX engine talks to an India broker adapter instead of direct US-market assumptions. 
- Phase 4: Replace market-context inputs and prompts with Indian equivalents while leaving the agent pipeline structure intact. 

### Success criteria

A successful MoonshotX-IND should still look and behave like MoonshotX to the operator while running fully on Indian-market assumptions under the hood.   
If the final repo feels like a bag of India scripts with an LLM pasted on top, the architecture has drifted in the wrong direction. 

If you want, I can turn this next into a **repo blueprint document** with exact proposed folders, exact filenames, and a **“copy / rewrite / create new”** map for MoonshotX-IND.




Good — now I have the exact ground truth on both repos. Here is the precise, file-by-file blueprint document you asked for.

***

# MoonshotX-IND — Repo Blueprint

## One-Line Architecture Rule

> **MoonshotX is the brain. DayTraderAI-IND is the ignition key. Everything else is India wiring.**

This document is the exact "copy / rewrite / create new" map. No ambiguity, no "roughly similar" — every file has an instruction.

***

## MoonshotX's Actual File Inventory

Confirmed from the live repos, here is what MoonshotX actually contains: 

**`backend/trading/`**: `loop.py` (30KB), `position_manager.py` (20KB), `scanner.py` (12KB), `risk.py` (10KB), `morning_brief.py` (10KB), `alpaca_client.py` (9.5KB), `momentum.py` (5.9KB), `earnings.py` (3.8KB), `regime.py` (3.5KB), `market_compare.py` (4.5KB), `correlation.py` (5.1KB)

**`backend/agents/`**: `pipeline.py` (38KB — the full batched LLM pipeline) 

**`backend/`**: `server.py` (23KB — FastAPI shell), `requirements.txt`, `emergentintegrations/` (provider-agnostic LLM layer) 

***

## The Complete File Map

### `backend/agents/` — **COPY, minimal touch**

| File | Instruction | Change |
|---|---|---|
| `pipeline.py` | **COPY** | Change all prompt context strings: "US market", "NYSE/NASDAQ", "S&P 500", "Fed/FOMC" → India equivalents. All graph structure, memory, batching logic — zero change. |
| `__init__.py` | **COPY** | No change |

The 38KB `pipeline.py` is your biggest asset — it is a full staged intelligence workflow.  Localising it is a **prompt engineering exercise**, not a structural rewrite. Every reference to "earnings beat," "EPS surprise," "pre-market gap" gets a corresponding India-market equivalent injected, while the graph topology, node routing, and LLM call batching stay identical.

***

### `backend/emergentintegrations/` — **COPY, zero change**

This is the provider-agnostic LLM abstraction layer.  It calls OpenAI/Anthropic/Gemini without any market assumptions. Copy wholesale. This is what allows MoonshotX-IND to swap LLM providers without touching trading logic.

***

### `backend/trading/` — **File-by-file**

| File | Instruction | What Changes |
|---|---|---|
| `loop.py` | **COPY + REWRITE timezone/hours block** | Replace `ET`→`IST`, `9:30`→`9:15`, `16:00`→`15:30`, `eod_exit_time = "15:57"`→`square_off_time = "15:15"`. Add pre-loop call to `KiteSessionManager.assert_valid()`. Inject `circuit_breaker_gate()` check in the 60s tick. |
| `position_manager.py` | **COPY + ADD 2 India gates** | Add `force_squareoff_15_15()` method. Add `cancel_orphaned_gtts()` — GTT cleanup when a position closes externally. Everything else (trailing stops, R-multiples, partial exits) is unchanged. |
| `risk.py` | **COPY + ADD 1 India gate** | Add `circuit_limit_check(symbol, price)` — if price within 0.5% of NSE circuit limit, flag position for exit. All drawdown limits, regime gates, daily-loss halt logic unchanged. |
| `regime.py` | **REWRITE inputs, keep structure** | Swap 4 data inputs: `VIX (CBOE)` → `India VIX (NSE)`, `SPY 20d return` → `NIFTY50 20d return`, `NYSE Breadth` → `NIFTY500 A/D ratio`, `CNN F&G` → `FII Net Flow (3-day)`. The 4-bucket output (`BULL/NEUTRAL/CAUTION/FEAR`) stays identical. |
| `scanner.py` | **REWRITE data calls, keep logic** | Replace Alpaca data fetches with Kite historical API calls. Replace SP500 universe with NIFTY500 constituent list. All momentum scoring, volume filter, gap filter logic — unchanged. |
| `morning_brief.py` | **REWRITE data inputs** | Replace US macro inputs with: India VIX, NIFTY/Bank Nifty prior-day performance, FII/DII flow (published ~7 PM IST prior day), RBI calendar check, SGX Nifty pre-open. The brief generation LLM call structure — unchanged. |
| `alpaca_client.py` | **DELETE** | Replaced entirely by `broker/kite_client.py`. Every call site in `loop.py` and `position_manager.py` gets refactored to call `KiteBroker` interface. |
| `earnings.py` | **REWRITE** | Replace Yahoo Finance earnings calendar with BSE results calendar API. Keep the same interface: `get_upcoming_earnings(symbol, days=5) → bool`. Rename to `results.py` internally. |
| `momentum.py` | **COPY** | Pure math (rate of change, ATR normalization). Zero broker/market dependency. No change. |
| `correlation.py` | **COPY + 1 line change** | Change benchmark ticker `SPY` → `^NSEI` (NIFTY50 Yahoo Finance symbol). All OLS beta math unchanged. |
| `market_compare.py` | **COPY + 2 line changes** | Change benchmark to NIFTY50. Change display label "vs SPY" → "vs NIFTY50". |

***

### `backend/broker/` — **CREATE NEW (from DayTraderAI-IND source)**

This entire directory is new in MoonshotX-IND. It is the India wiring layer.

```
backend/broker/
├── __init__.py
├── kite_session.py          # KiteSessionManager class
├── token_refresh.py         # Ported from DayTraderAI-IND auto_token_refresh.py
├── login_fallback.py        # Ported from DayTraderAI-IND perform_login.py path
├── kite_client.py           # KiteBroker — the drop-in alpaca_client.py replacement
├── kite_ticker.py           # KiteTicker WebSocket wrapper
└── gtt_manager.py           # GTT order lifecycle manager (the bracket replacement)
```

**`kite_session.py` — new class wrapping DayTraderAI-IND's session machinery**

```python
class KiteSessionManager:
    """
    Owns the daily token lifecycle.
    Called once at bot startup; call assert_valid() at top of every loop tick.
    """
    def assert_valid(self) -> None:
        """Raises SessionExpiredError if token age > 20h or validation fails."""
    
    def refresh_if_stale(self) -> bool:
        """Calls token_refresh.py flow. Returns True if refresh happened."""
    
    def restart_bot_after_refresh(self) -> None:
        """subprocess restart — matches DayTraderAI-IND behavior."""
```

**`token_refresh.py` — ported from `auto_token_refresh.py` (13.5KB original)** 
The DayTraderAI-IND version does: login → TOTP 2FA → request token extraction → `generate_session()` → `.env` rewrite → token verification. Port it directly. Wrap it as a callable function `refresh_kite_token() -> str` rather than a standalone script.

**`gtt_manager.py` — the most critical India-specific new file**

```python
class GTTManager:
    """
    Replaces bracket orders entirely.
    Every filled entry creates 2 GTTs: one SL, one TP.
    Monitors GTT existence every 5s. Cancels the survivor on trigger.
    """
    def place_entry_with_protection(
        self, symbol: str, qty: int, 
        entry_price: float, stop_loss: float, take_profit: float
    ) -> GTTPair:
        """Fires entry order, then on fill immediately creates SL+TP GTT pair."""
    
    def verify_gtt_pair(self, gtt_pair: GTTPair) -> GTTPairStatus:
        """Called every 5s from loop. Re-creates GTTs if silently deleted."""
    
    def cancel_survivor(self, gtt_pair: GTTPair, triggered_side: str) -> None:
        """On SL trigger, cancel TP GTT. On TP trigger, cancel SL GTT."""
```

The GTT silent-deletion problem is the hardest-learned lesson from DayTraderAI-IND's `BRACKET_AUTO_RECREATION_FIXED.md` and `BRACKET_RECREATION_FINAL_FIX.md`.  The `verify_gtt_pair()` call must run every 5 seconds in the main loop — not on position entry alone.

**`kite_client.py` — the adapter interface**

Expose the same method signatures that MoonshotX's `alpaca_client.py` currently exposes, so that `loop.py` and `position_manager.py` see no difference:

```python
class KiteBroker:
    # Mirror AlpacaClient's public interface
    def get_account(self) -> AccountInfo: ...
    def get_positions(self) -> list[Position]: ...
    def place_order(self, symbol, qty, side, order_type, ...) -> Order: ...
    def cancel_order(self, order_id) -> None: ...
    def get_bars(self, symbol, timeframe, limit) -> pd.DataFrame: ...
    def get_latest_quote(self, symbol) -> Quote: ...
    def is_market_open(self) -> bool: ...   # IST 9:15–15:30
```

The adapter pattern means `loop.py` imports `KiteBroker` as a drop-in for `AlpacaClient`. No trading logic changes. Only the constructor and the internals change.

***

### `backend/data/` — **CREATE NEW**

```
backend/data/
├── __init__.py
├── india_universe.py        # NIFTY500 constituent list + liquidity filter
├── india_market_feed.py     # Kite historical OHLCV fetcher
├── india_news_feed.py       # BSE announcements + ET Markets / MoneyControl RSS
├── india_macro.py           # India VIX, FII/DII flows, SGX Nifty pre-open
└── bse_results_calendar.py  # Quarterly results calendar (replaces earnings.py source)
```

**`india_macro.py`** is the most important new data file. It feeds `regime.py` and `morning_brief.py`:

```python
def get_india_vix() -> float:
    """NSE India VIX via nsetools or direct NSE URL."""

def get_fii_dii_flow(days: int = 3) -> dict:
    """FII/DII net buying/selling from NSE data. Published daily ~7 PM IST."""
    # Returns: {"fii_net": [-450, -1200, +320], "dii_net": [+600, +900, +150]}

def get_sgx_nifty() -> float:
    """SGX Nifty pre-open (available 7:30–9:15 IST window)."""
```

***

### `backend/server.py` — **COPY + minimal additions**

The 23KB FastAPI shell stays structurally identical.  Add 3 India-specific endpoints:

```python
@app.get("/api/kite/session-status")
# Returns: token age, validity, next refresh time

@app.post("/api/kite/refresh-token")  
# Manually triggers token refresh (useful when TOTP flow needs human assist)

@app.get("/api/india/market-status")
# Returns: NSE open/closed, current circuit filter level, India VIX live
```

***

### Files to **DELETE / NOT PORT** from DayTraderAI-IND

Do not bring these into MoonshotX-IND:

- All 60+ `check_*.py` diagnostic scripts — internal tools, not product
- All `EMERGENCY_FIX_*.md`, `BRACKET_*.md`, `CRITICAL_FIXES_*.md` — documentation of debt already learned
- `backend/main.py` (the 67KB monolith) — the whole point of MoonshotX-IND is escaping this
- `audit_alpaca_orders.py`, `close_tsla_position.py` — US-market residue in DayTraderAI-IND
- `backend/advisory/`, `backend/copilot/` sub-directories from DayTraderAI-IND — out of scope

These belong in a private `ops/` folder at most, never in the main product surface. 

***

## The "Copy / Rewrite / Create New" Master Table

| File/Module | Action | Source | Effort |
|---|---|---|---|
| `agents/pipeline.py` | **COPY + prompt localization** | MoonshotX | Low |
| `emergentintegrations/` | **COPY** | MoonshotX | Zero |
| `server.py` | **COPY + 3 endpoints** | MoonshotX | Low |
| `trading/loop.py` | **COPY + timezone/hours rewrite** | MoonshotX | Low |
| `trading/position_manager.py` | **COPY + 2 India gates** | MoonshotX | Low |
| `trading/risk.py` | **COPY + circuit limit gate** | MoonshotX | Low |
| `trading/momentum.py` | **COPY** | MoonshotX | Zero |
| `trading/correlation.py` | **COPY + 1 line** | MoonshotX | Zero |
| `trading/market_compare.py` | **COPY + 2 lines** | MoonshotX | Zero |
| `trading/regime.py` | **REWRITE inputs** | MoonshotX shape | Medium |
| `trading/scanner.py` | **REWRITE data calls** | MoonshotX shape | Medium |
| `trading/morning_brief.py` | **REWRITE data inputs** | MoonshotX shape | Medium |
| `trading/earnings.py` | **REWRITE** → `results.py` | MoonshotX shape | Medium |
| `broker/token_refresh.py` | **PORT** | DayTraderAI-IND `auto_token_refresh.py` | Medium |
| `broker/login_fallback.py` | **PORT** | DayTraderAI-IND `perform_login.py` | Low |
| `broker/kite_client.py` | **CREATE** (adapter) | DayTraderAI-IND concepts | High |
| `broker/kite_session.py` | **CREATE** | DayTraderAI-IND concepts | Medium |
| `broker/kite_ticker.py` | **PORT + wrap** | DayTraderAI-IND | Low |
| `broker/gtt_manager.py` | **CREATE** | DayTraderAI-IND battle knowledge | High |
| `data/india_universe.py` | **CREATE NEW** | — | Medium |
| `data/india_market_feed.py` | **CREATE NEW** | — | Medium |
| `data/india_macro.py` | **CREATE NEW** | — | Medium |
| `data/india_news_feed.py` | **CREATE NEW** | — | Low |
| `data/bse_results_calendar.py` | **CREATE NEW** | — | Low |
| `trading/alpaca_client.py` | **DELETE** | — | — |

***

## The 2 Files That Decide if This Succeeds

Out of everything above, two files carry disproportionate risk:

**`broker/gtt_manager.py`** — If GTT verification has any gap, positions will sit unprotected and Zerodha's RMS squares them off with a penalty. Every `BRACKET_*.md` in DayTraderAI-IND is a lesson about this one file.  Budget 3x the time you think it needs.

**`broker/token_refresh.py`** — If the daily token refresh fails silently, the bot trades zero and you don't know until the morning brief doesn't fire. Add an `assert_valid()` health gate in `loop.py` that halts the loop (not crashes the process) until the session is recovered. DayTraderAI-IND's `auto_token_refresh.py` is the right starting source — it's 13.5KB of proven flow.  Port it, don't rewrite it from scratch.

Everything else is localization work. These two are correctness-critical.