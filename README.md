# MoonshotX-IND

> **Autonomous AI-powered intraday momentum trading bot for Indian markets (NSE/BSE)** — built on real-time market intelligence, regime-aware risk management, and a batched LLM pipeline that makes institutional-grade entry/exit decisions at a fraction of the cost.

[![Python](https://img.shields.io/badge/Python-3.11%2B-blue?logo=python)](https://www.python.org/)
[![React](https://img.shields.io/badge/React-18-61DAFB?logo=react)](https://reactjs.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.110-009688?logo=fastapi)](https://fastapi.tiangolo.com/)
[![MongoDB](https://img.shields.io/badge/MongoDB-Motor-47A248?logo=mongodb)](https://www.mongodb.com/)
[![Zerodha](https://img.shields.io/badge/Zerodha-Kite%20Connect-387ED1)](https://kite.trade/)
[![License](https://img.shields.io/badge/License-MIT-green)](LICENSE)

---

## Table of Contents

- [Overview](#overview)
- [Live Performance](#live-performance)
- [Architecture](#architecture)
- [Key Features](#key-features)
- [Tech Stack](#tech-stack)
- [Project Structure](#project-structure)
- [Getting Started](#getting-started)
- [Configuration](#configuration)
- [API Reference](#api-reference)
- [Trading Logic](#trading-logic)
- [Frontend Dashboard](#frontend-dashboard)
- [Testing](#testing)
- [Contributing](#contributing)
- [Changelog](#changelog)
- [Roadmap](#roadmap)
- [Disclaimer](#disclaimer)

---

## Overview

MoonshotX-IND is a fully autonomous intraday trading system built for Indian markets (NSE/BSE) that:

1. **Reads India every morning** — 25 minutes before NSE open (08:50 IST), it gathers Asia futures, global indices, macro indicators (India VIX, Brent, Gold, USD/INR), FII/DII flow data, and pre-market news.
2. **Thinks before it acts** — one LLM call produces a structured morning intelligence brief: expected regime, hot sectors, top NSE picks, stocks to avoid, and trading stance (`aggressive` / `normal` / `cautious` / `sit_out`).
3. **Hunts intraday momentum** — every 5 minutes during NSE hours (09:15–15:30 IST), a batched 2-call LLM pipeline screens and deep-analyzes NIFTY500 candidates in seconds.
4. **Protects capital aggressively** — regime-adaptive stop losses (2.5% in fear, 6% in bull), NSE circuit breaker gate, GTT-based stops, partial profit taking (₹), and a 2-hour re-entry cooldown.
5. **Closes flat every day** — all positions force-closed at 15:10 IST via GTT cleanup. No overnight gap risk.

---

## Live Performance

Daily returns are auto-logged after every NSE close and compared against NIFTY50/NIFTY500.
See **[PERFORMANCE_LOG.md](PERFORMANCE_LOG.md)** for the full history.

| Date | MoonshotX-IND | NIFTY50 | NIFTY500 | Regime |
|------|---------------|---------|----------|--------|
| — | Paper trading | — | — | — |

**System stats (paper trading):**
- Portfolio: ~₹5,00,000 (Zerodha Paper / ZERODHA_PAPER_MODE=true)
- LLM pipeline: 2 calls/loop (down from 252) — **99% cost reduction**
- Loop cycle: position management every 60s, entry scanning every 5 min
- LLM cost: ~$0.00134 per scan loop (~$0.40/day)

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        TRADING LOOP (60s)                       │
│                                                                 │
│  ┌──────────────┐    ┌──────────────┐    ┌───────────────────┐  │
│  │ Regime Mgr   │    │ Morning Brief│    │  Position Manager │  │
│  │ India VIX    │    │ (T-25 min)   │    │  GTT Stops        │  │
│  │ + FII/DII    │    │ Asia+News    │    │  Partial profits  │  │
│  │ → bull/fear/ │    │ LLM Macro    │    │  Loss exits       │  │
│  └──────┬───────┘    └──────┬───────┘    └───────────────────┘  │
│         │                  │                                     │
│         ▼                  ▼                                     │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │                  ENTRY PIPELINE (5 min)                 │    │
│  │                                                         │    │
│  │  Scanner → Correlation → Results Gate → Momentum Gate   │    │
│  │      ↓                                                  │    │
│  │  Phase A: QUICK LLM — screen all candidates (1 call)    │    │
│  │  Phase B: DEEP LLM  — full trade plans (1 call)         │    │
│  │      ↓                                                  │    │
│  │  APPROVE → Cooldown check → Execute market order        │    │
│  └─────────────────────────────────────────────────────────┘    │
│                                                                 │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │              EOD (15:10 IST)                             │    │
│  │  GTT cleanup → Force-close all → Log daily comparison    │    │
│  └─────────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────┘

┌──────────────────────────┐    ┌──────────────────────────────┐
│   FastAPI Backend        │    │   React Frontend             │
│   :8001                  │◄──►│   :3000                      │
│   MongoDB (Motor)        │    │   Dashboard / Positions /    │
│   WebSocket live feed    │    │   AgentBrain / Performance   │
└──────────────────────────┘    └──────────────────────────────┘
```

---

## Key Features

### 🧠 Morning Intelligence Brief
- Pulls **Asia futures** (SGX Nifty, Dow futures), **global indices** (Nikkei, Hang Seng, DAX), and **India macro** (India VIX, Brent, Gold, USD/INR) in parallel
- Fetches FII/DII flow data from NSE and up to 20 deduplicated headlines from India financial news
- One DEEP LLM call produces: `expected_regime`, `hot_sectors`, `avoid_sectors`, `top_picks`, `macro_risks`, `trading_stance`
- `sit_out` stance → zero entries queued for the session

### 🎯 Batched LLM Pipeline (99% cost reduction)
| Metric | Before | After |
|--------|--------|-------|
| LLM calls/loop | 252 | 2 |
| Cost/loop | ~$0.0101 | ~$0.00134 |
| Latency (20 candidates) | ~252s | ~17s |

- **Phase A (QUICK model):** Compact JSON array of all candidates → single screen call → shortlist of ≥0.60 confidence bullish picks
- **Phase B (DEEP model):** Full trade plans (entry, stop loss, take profit, conviction, risk level) for shortlisted tickers

### 🛡️ Regime-Adaptive Risk Management

Max positions scale with **both** portfolio size and regime (log₂-scaled base × regime multiplier):

| Regime | Max Loss | Stale Exit | ₹5L | ₹10L | ₹25L | ₹50L+ |
|--------|----------|------------|-----|------|------|-------|
| Bull | 6% | 8 hours | 10 | 14 | 19 | 20 |
| Neutral | 4% | 6 hours | 7 | 10 | 14 | 15 |
| Fear | 2.5% | 3 hours | 5 | 7 | 9 | 10 |
| Choppy | 2.5% | 3 hours | 4 | 5 | 7 | 8 |
| Bear Mode | 2% | 2 hours | 2 | 3 | 4 | 4 |
| Extreme Fear | 1.5% | 1 hour | 0 | 0 | 0 | 0 |

### 🚦 Entry Quality Gates (in order)
1. **Re-entry cooldown** — 2-hour block after any loss exit (persisted to MongoDB, survives restarts)
2. **Pending order guard** — checks open Kite orders to prevent duplicate buys (race condition fix)
3. **NSE Results gate** — blocks entry 2 days before / 1 day after quarterly results (BSE calendar)
4. **NSE Circuit breaker gate** — blocks all trades if NIFTY down -5%/-10%/-15% (SEBI rules)
5. **Correlation guard** — regime-dependent sector concentration limits
6. **Intraday momentum gate** — requires 2/3 up 5-min bars in fear/choppy regimes; checks price vs average, volume, candle color

### 💰 Position Management
- **Trailing stop**: activates at +3% from entry, trails 2.5% below high watermark
- **Breakeven stop**: moves stop to entry+0.3% once position up +2%
- **Partial profit taking**: sells 1/3 at +5%, 1/3 at +10%, 1/2 at +20%
- **Quick reversal exit**: down ≥1.5% in first 30 minutes → exit immediately
- **Momentum fade exit**: held ≥45 min AND down ≥1% AND HWM dropped ≥2% → exit

### 🌅 EOD Force-Close (NSE)
- **15:00 IST**: no new entries allowed
- **15:10 IST**: GTT order cleanup + cancel all orders + close all positions via Kite MIS squareoff
- **15:30 IST**: NSE market close — system flat overnight, every night

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| **Backend** | Python 3.11+, FastAPI, uvicorn, asyncio |
| **Database** | MongoDB (Motor async driver) |
| **Broker** | Zerodha Kite Connect (paper + live, NSE/BSE) |
| **Order Management** | GTT (Good Till Triggered) orders for stop-loss/take-profit |
| **Market Data** | Kite Historical API, yfinance, NSE India |
| **LLM** | OpenRouter → Gemini Flash (quick) + Claude Haiku (deep) |
| **Frontend** | React 18, Recharts, Lucide icons, WebSocket |
| **Risk** | Custom regime-aware sizing + NSE circuit breaker gate |

---

## Project Structure

```
MoonshotX-IND/
├── backend/
│   ├── server.py                  # FastAPI app, all API routes, WebSocket
│   ├── agents/
│   │   └── pipeline.py            # Batched LLM pipeline (India-localized prompts)
│   ├── broker/                    # India broker layer (NEW)
│   │   ├── kite_session.py        # Zerodha session lifecycle management
│   │   ├── token_refresh.py       # Auto TOTP token refresh (no Selenium)
│   │   ├── login_fallback.py      # Manual token injection fallback
│   │   ├── kite_client.py         # KiteBroker drop-in (mirrors AlpacaClient API)
│   │   ├── kite_ticker.py         # WebSocket live price streaming
│   │   └── gtt_manager.py         # GTT stop-loss / take-profit management
│   ├── data/                      # India data layer (NEW)
│   │   ├── india_universe.py      # NIFTY500 universe + SEED_UNIVERSE
│   │   ├── india_market_feed.py   # Kite/yfinance OHLCV + FII/DII data
│   │   ├── india_macro.py         # India macro aggregator (VIX, FII, USD/INR)
│   │   ├── india_news_feed.py     # India financial news (ET, Moneycontrol, etc.)
│   │   └── bse_results_calendar.py # BSE/NSE quarterly results calendar
│   ├── trading/
│   │   ├── loop.py                # IST-aware trading loop (09:15–15:30 IST)
│   │   ├── position_manager.py    # GTT stops, partials, ₹ exits
│   │   ├── morning_brief.py       # India macro + Asia futures + LLM brief
│   │   ├── results.py             # India quarterly results gate (NEW)
│   │   ├── market_compare.py      # Daily performance vs NIFTY50/500
│   │   ├── momentum.py            # Intraday momentum confirmation gate
│   │   ├── regime.py              # India VIX + NIFTY + A/D ratio classifier
│   │   ├── risk.py                # Position sizing + NSE circuit breaker gate
│   │   ├── scanner.py             # NIFTY500 universe scanner
│   │   └── correlation.py         # Sector concentration guard
│   ├── emergentintegrations/      # LLM chat shim (OpenRouter via requests)
│   ├── __tests__/                 # Unit + integration tests
│   └── requirements.txt
├── frontend/
│   └── src/
│       ├── pages/
│       │   ├── Dashboard.jsx      # Live P&L, NAV chart, loop status
│       │   ├── Positions.jsx      # Open positions + GTT state
│       │   ├── AgentBrain.jsx     # LLM decision logs + pipeline view
│       │   ├── Performance.jsx    # Trade history + metrics
│       │   ├── Universe.jsx       # NIFTY500 universe rankings
│       │   └── Settings.jsx       # Bot configuration
│       ├── components/            # Shared UI components
│       └── hooks/
│           └── useWebSocket.js    # Live WebSocket feed
├── PERFORMANCE_LOG.md             # Auto-generated daily returns vs NIFTY
├── CHANGELOG.md                   # Version history
├── restart_all.sh
├── start_backend.sh
└── start_frontend.sh
```

---

## Getting Started

### Prerequisites
- Python 3.11+
- Node.js 18+
- MongoDB (local or Atlas)
- Zerodha account with Kite Connect API subscription (₹2000/month)
- OpenRouter API key
- Google Authenticator / TOTP app for 2FA

### 1. Clone

```bash
git clone https://github.com/codebytelabs/MoonshotX-IND.git
cd MoonshotX-IND
```

### 2. Backend Setup

```bash
cd backend
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 3. Frontend Setup

```bash
cd frontend
npm install
```

### 4. Environment Variables

Create `.env` at the project root:

```env
# Zerodha Kite Connect
Zerodha_KITE_PAID_API_KEY=your_kite_api_key
Zerodha_KITE_PAID_Secret_KEY=your_kite_secret
Zerodha_KITE_PAID_ACCESS_TOKEN=your_access_token   # refreshed daily
ZERODHA_USER_ID=your_zerodha_user_id
ZERODHA_PASSWORD=your_zerodha_password
ZERODHA_TOTP_SECRET=your_totp_base32_secret
ZERODHA_PAPER_MODE=true                             # set false for live trading

# OpenRouter (LLM)
OPENROUTER_API_KEY=your_openrouter_key

# LLM Models (optional — defaults shown)
Openrouter_Quick_Primary_Model=google/gemini-2.5-flash-lite-preview-09-2025
Openrouter_Research_Primary_Model=anthropic/claude-haiku-4-5

# MongoDB
MONGO_URL=mongodb://localhost:27017
DB_NAME=moonshotx_ind

# Frontend
REACT_APP_BACKEND_URL=http://localhost:8001
```

> **Token Refresh**: The system auto-refreshes the Kite access token daily at 08:00 IST using `broker/token_refresh.py`. Ensure `ZERODHA_TOTP_SECRET` is set correctly.

### 5. Run

```bash
# Full restart (recommended)
./restart_all.sh

# Or individually:
./start_backend.sh    # FastAPI on :8001
./start_frontend.sh   # React on :3000
```

Open [http://localhost:3000](http://localhost:3000)

---

## Configuration

### Regime Thresholds (`trading/regime.py`)
The regime classifier uses **India VIX**, **NIFTY50 return**, and **NSE A/D ratio**:

| Regime | India VIX | NIFTY 20d Return | A/D Ratio |
|--------|-----------|------------------|-----------|
| `extreme_fear` | >30 | <-8% | <0.35 |
| `fear` | >22 | <-3% | <0.45 |
| `choppy` | >18 | <0% | <0.50 |
| `neutral` | 13-18 | 0–4% | 0.45-0.60 |
| `bull` | <13 | >4% | >0.60 |

### LLM Models (`agents/pipeline.py`)
Models are loaded from `.env` with fallbacks:

```python
QUICK_MODEL    = "google/gemini-2.5-flash-lite-preview-09-2025"   # ~$0.00004/call
QUICK_FALLBACK = "google/gemini-3.1-flash-lite-preview"
DEEP_MODEL     = "anthropic/claude-haiku-4-5"                      # ~$0.0013/call
DEEP_FALLBACK  = "minimax/minimax-m2.7"
```

### Key Timing Constants (`trading/loop.py`)
```python
PRE_MARKET_WINDOW_MINS = 25   # run morning brief at 08:50 IST (T-25min)
ENTRY_SCAN_INTERVAL_MINS = 5  # scan for new entries every 5 min
EOD_NO_ENTRY_MINS = 30        # no new entries after 15:00 IST
EOD_CLOSE_MINS = 20           # GTT cleanup + force-close at 15:10 IST
MARKET_OPEN_IST  = "09:15"    # NSE equities open
MARKET_CLOSE_IST = "15:30"    # NSE equities close
```

---

## API Reference

All endpoints are prefixed with `/api`.

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/system/status` | Loop status, regime, loop count |
| `POST` | `/system/start` | Start the trading loop |
| `POST` | `/system/stop` | Stop the trading loop |
| `POST` | `/system/emergency-halt` | Cancel all orders + GTT cleanup + halt |
| `GET` | `/account` | Kite account info + portfolio value (₹) |
| `GET` | `/positions` | Open NSE positions with P&L |
| `GET` | `/trades` | Closed trade history |
| `GET` | `/regime` | Current market regime + India VIX/FII |
| `GET` | `/morning-brief` | Latest India pre-market intelligence brief |
| `GET` | `/universe` | NIFTY500 universe ranked by score |
| `GET` | `/nav` | Portfolio NAV history (MongoDB snapshots) |
| `GET` | `/agent-logs` | LLM decision logs |
| `GET` | `/agent-logs/{id}` | Full detail for a specific decision |
| `GET` | `/performance` | Win rate, avg gain/loss, Sharpe-like stats |
| `GET` | `/positions/concentration` | Sector breakdown |
| `GET` | `/positions/results-gate` | BSE results blackout for open positions |
| `GET` | `/positions/management` | GTT / trailing stop / HWM tracking state |
| `GET` | `/config` | Active model names + cost summary |
| `GET` | `/kite/session` | **India** — Kite session token validity |
| `GET` | `/nse/circuit-check` | **India** — NIFTY circuit breaker gate status |
| `GET` | `/results-calendar` | **India** — Upcoming NSE/BSE quarterly results |
| `WS` | `/ws` | WebSocket live feed (loop ticks, trades, morning brief) |

---

## Trading Logic

### Daily Flow (IST)
```
07:30 IST  Token refresh: auto-renew Kite access token (TOTP)
08:50 IST  Morning Brief fires: Asia futures + India macro + LLM brief
09:15 IST  NSE market open → pre-market queue executed (top picks from brief)
09:15–15:00  Every 60s: position management (GTT stops, partials, exits)
             Every 5min: new entry scan (NIFTY500 scanner → LLM pipeline)
15:00 IST  No new entries allowed
15:10 IST  GTT cleanup + force-close all MIS positions
15:30 IST  NSE market close — flat overnight
```

### Entry Decision Flow
```
NIFTY500 Scanner (300+ tickers)
    → Bayesian pre-filter (score ≥ 0.45)
    → NSE circuit breaker gate (NIFTY % change)
    → Sector concentration check
    → India results gate (BSE quarterly calendar)
    → Re-entry cooldown check (MongoDB-persisted)
    → Pending order dedup guard
    → Phase A: QUICK LLM screen (all candidates, 1 call)
    → Phase B: DEEP LLM trade plan (shortlist, 1 call)
    → Intraday momentum gate (5-min bars, regime-aware)
    → Position size (regime × portfolio × ATR-based, min ₹5000)
    → Kite MIS market order + GTT stop-loss/take-profit
```

---

## Frontend Dashboard

| Page | Description |
|------|-------------|
| **Dashboard** | Live NAV chart (WebSocket), P&L stats, loop status, regime badge |
| **Positions** | Open positions with unrealized P&L, trailing stop state, HWM |
| **Agent Brain** | Full LLM decision pipeline — screen results, trade plans, reasoning |
| **Performance** | Closed trade history, win rate, cumulative P&L chart |
| **Universe** | Live universe rankings with momentum scores, RSI, Bayesian score |
| **Settings** | Bot configuration, model selection, risk parameters |

---

## Testing

```bash
# Backend tests
cd backend
source venv/bin/activate
pytest __tests__/ -v
pytest tests/ -v

# Key test files:
# __tests__/test_batch_pipeline.py  — 6 tests for batched LLM pipeline
```

---

## Security Notes

- **Never commit** `.env` files — they are gitignored
- All API keys are loaded via `python-dotenv` server-side only
- React frontend never touches API keys — all calls go through the FastAPI backend
- Paper trading by default — set `ZERODHA_PAPER_MODE=false` for live trading
- `ZERODHA_TOTP_SECRET` and `ZERODHA_PASSWORD` are extremely sensitive — never expose them
- Input sanitization on all API endpoints
- No secrets in client-side code

---

## Contributing

1. Fork the repo
2. Create a feature branch: `git checkout -b feat/my-feature`
3. Make changes, add tests, ensure `pytest __tests__/ -v` passes
4. Commit with conventional commits: `feat:`, `fix:`, `docs:`, `refactor:`
5. Push and open a Pull Request

**Code style:**
- `camelCase` for JS/TS functions and variables
- `PascalCase` for React components
- `snake_case` for Python functions and variables
- All new features require tests in `__tests__/`
- Update `CHANGELOG.md` with every PR

---

## Changelog

See **[CHANGELOG.md](CHANGELOG.md)** for a full history of changes, features, and fixes across all releases.

---

## Roadmap

- [x] Zerodha Kite Connect broker layer (session + GTT + ticker)
- [x] Auto TOTP token refresh (no Selenium)
- [x] India macro data layer (VIX, FII/DII, USD/INR, BSE calendar)
- [x] NIFTY500 universe scanner
- [x] India regime classifier (India VIX + NIFTY + A/D ratio)
- [x] NSE circuit breaker gate (SEBI -5/-10/-15% rules)
- [x] India quarterly results gate (BSE calendar)
- [x] GTT-based stop-loss / take-profit order management
- [x] IST-aware trading loop (09:15–15:30 IST)
- [x] India-localized LLM agent prompts (₹, India VIX, FII/DII)
- [x] 3 India-specific API endpoints (session, circuit, results calendar)
- [x] Batched LLM pipeline (99% cost reduction)
- [ ] Live trading deployment (ZERODHA_PAPER_MODE=false)
- [ ] Telegram alerts for entries, exits, morning brief
- [ ] Options flow integration (NSE options OI data)
- [ ] Backtesting against Kite historical data
- [ ] Docker / docker-compose deployment

---

## Disclaimer

MoonshotX-IND is provided for **educational and research purposes only**. It is not SEBI-registered investment advice. Trading NSE/BSE equities involves substantial risk of loss. Past performance (including paper trading results) does not guarantee future results. The authors are not responsible for any financial losses incurred from using this software. Always do your own research and consult a SEBI-registered investment advisor before making investment decisions.

---

## License

MIT License — see [LICENSE](LICENSE) for details.

---

<div align="center">
Built with care by <a href="https://github.com/codebytelabs">codebytelabs</a>
</div>
