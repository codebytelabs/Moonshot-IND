# MoonshotX

> **Autonomous AI-powered intraday momentum trading bot** — built on real-time market intelligence, regime-aware risk management, and a batched LLM pipeline that makes institutional-grade entry/exit decisions at a fraction of the cost.

[![Python](https://img.shields.io/badge/Python-3.11%2B-blue?logo=python)](https://www.python.org/)
[![React](https://img.shields.io/badge/React-18-61DAFB?logo=react)](https://reactjs.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.110-009688?logo=fastapi)](https://fastapi.tiangolo.com/)
[![MongoDB](https://img.shields.io/badge/MongoDB-Motor-47A248?logo=mongodb)](https://www.mongodb.com/)
[![Alpaca](https://img.shields.io/badge/Alpaca-Trading%20API-FFCD00)](https://alpaca.markets/)
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

MoonshotX is a fully autonomous intraday trading system that:

1. **Reads the world every morning** — 25 minutes before market open, it gathers US futures, international indices, macro indicators (VIX, gold, oil, USD, yields), overnight news headlines, and pre-market movers.
2. **Thinks before it acts** — one LLM call produces a structured morning intelligence brief: expected regime, hot sectors, top picks, stocks to avoid, and trading stance (`aggressive` / `normal` / `cautious` / `sit_out`).
3. **Hunts intraday momentum** — every 5 minutes during market hours, a batched 2-call LLM pipeline screens and deep-analyzes candidates in seconds.
4. **Protects capital aggressively** — regime-adaptive stop losses (2.5% in fear, 6% in bull), quick-reversal exits, trailing stops, partial profit taking, and a 2-hour re-entry cooldown after losses.
5. **Closes flat every day** — all positions force-closed by 15:45 EST. No overnight gap risk.

---

## Live Performance

Daily returns are auto-logged after every market close and compared against major indices.
See **[PERFORMANCE_LOG.md](PERFORMANCE_LOG.md)** for the full history.

| Date | MoonshotX | SPY | QQQ | DIA | Regime |
|------|-----------|-----|-----|-----|--------|
| 2026-03-26 | **-0.58%** | -1.79% | -2.39% | -1.04% | fear |

> **Day 1**: Outperformed every major index during a broad selloff. QQQ -2.39%, SMH -4.56%, MoonshotX -0.58%.

**System stats (paper trading):**
- Portfolio: ~$138,000 on Alpaca Paper
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
│  │ VIX + F&G    │    │ (T-25 min)   │    │  Trailing stops   │  │
│  │ → bull/fear/ │    │ Futures+News │    │  Partial profits  │  │
│  │   choppy     │    │ LLM Macro    │    │  Loss exits       │  │
│  └──────┬───────┘    └──────┬───────┘    └───────────────────┘  │
│         │                  │                                     │
│         ▼                  ▼                                     │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │                  ENTRY PIPELINE (5 min)                 │    │
│  │                                                         │    │
│  │  Scanner → Correlation → Earnings → Momentum Gate       │    │
│  │      ↓                                                  │    │
│  │  Phase A: QUICK LLM — screen all candidates (1 call)    │    │
│  │  Phase B: DEEP LLM  — full trade plans (1 call)         │    │
│  │      ↓                                                  │    │
│  │  APPROVE → Cooldown check → Execute market order        │    │
│  └─────────────────────────────────────────────────────────┘    │
│                                                                 │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │              EOD (15:45 EST)                             │    │
│  │  Force-close all → Cancel orders → Log daily comparison  │    │
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
- Pulls **US futures** (ES, NQ, YM, RTY), **international indices** (Nikkei, Hang Seng, DAX, FTSE, ASX), and **macro indicators** (VIX, Gold, Oil, DXY, 10yr yield) in parallel
- Fetches up to 20 deduplicated headlines from SPY/QQQ/VIX news feeds
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
| Regime | Max Loss | Stale Exit | Max Positions |
|--------|----------|------------|---------------|
| Bull | 6% | 8 hours | 8 |
| Neutral | 4% | 6 hours | 6 |
| Fear | 2.5% | 3 hours | 3 |
| Bear Mode | 2% | 2 hours | 2 |
| Extreme Fear | 1.5% | 1 hour | 1 |

### 🚦 Entry Quality Gates (in order)
1. **Re-entry cooldown** — 2-hour block after any loss exit (persisted to MongoDB, survives restarts)
2. **Pending order guard** — checks open Alpaca orders to prevent duplicate buys (race condition fix)
3. **Earnings blackout** — blocks entry 2 days before / 1 day after earnings
4. **Correlation guard** — regime-dependent sector concentration limits
5. **Intraday momentum gate** — requires 2/3 up 5-min bars in fear/choppy regimes; checks price vs average, volume, candle color

### 💰 Position Management
- **Trailing stop**: activates at +3% from entry, trails 2.5% below high watermark
- **Breakeven stop**: moves stop to entry+0.3% once position up +2%
- **Partial profit taking**: sells 1/3 at +5%, 1/3 at +10%, 1/2 at +20%
- **Quick reversal exit**: down ≥1.5% in first 30 minutes → exit immediately
- **Momentum fade exit**: held ≥45 min AND down ≥1% AND HWM dropped ≥2% → exit

### 🌅 EOD Force-Close
- **15:30 EST**: no new entries allowed
- **15:45 EST**: cancel all orders + close all positions → flat overnight, every night

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| **Backend** | Python 3.11+, FastAPI, uvicorn, asyncio |
| **Database** | MongoDB (Motor async driver) |
| **Broker** | Alpaca Markets (paper + live) |
| **Market Data** | Alpaca Data API, yfinance |
| **LLM** | OpenRouter → Gemini Flash (quick) + Claude Haiku (deep) |
| **Frontend** | React 18, Recharts, Lucide icons, WebSocket |
| **Risk** | Custom regime-aware position sizing and stop logic |

---

## Project Structure

```
MoonshotX/
├── backend/
│   ├── server.py                  # FastAPI app, all API routes, WebSocket
│   ├── agents/
│   │   └── pipeline.py            # Batched LLM pipeline (Phase A + B)
│   ├── trading/
│   │   ├── loop.py                # Main trading loop orchestrator
│   │   ├── position_manager.py    # Trailing stops, partials, exits
│   │   ├── morning_brief.py       # Pre-market intelligence gather + LLM
│   │   ├── market_compare.py      # Daily performance vs index comparison
│   │   ├── momentum.py            # Intraday momentum confirmation gate
│   │   ├── regime.py              # VIX + Fear/Greed regime classifier
│   │   ├── risk.py                # Position sizing, drawdown limits
│   │   ├── scanner.py             # Dynamic universe discovery + ranking
│   │   ├── alpaca_client.py       # Alpaca broker + data API wrapper
│   │   ├── correlation.py         # Sector concentration guard
│   │   └── earnings.py            # Earnings calendar blackout
│   ├── emergentintegrations/      # LLM chat shim (OpenRouter via requests)
│   ├── __tests__/                 # Unit + integration tests
│   └── requirements.txt
├── frontend/
│   └── src/
│       ├── pages/
│       │   ├── Dashboard.jsx      # Live P&L, NAV chart, loop status
│       │   ├── Positions.jsx      # Open positions + management state
│       │   ├── AgentBrain.jsx     # LLM decision logs + pipeline view
│       │   ├── Performance.jsx    # Trade history + metrics
│       │   ├── Universe.jsx       # Scanned universe + rankings
│       │   └── Settings.jsx       # Bot configuration
│       ├── components/            # Shared UI components
│       └── hooks/
│           └── useWebSocket.js    # Live WebSocket feed
├── PERFORMANCE_LOG.md             # Auto-generated daily returns vs indices
├── CHANGELOG.md                   # Version history + all changes
├── restart_all.sh                 # Kill + restart backend + frontend
├── start_backend.sh
└── start_frontend.sh
```

---

## Getting Started

### Prerequisites
- Python 3.11+
- Node.js 18+
- MongoDB (local or Atlas)
- Alpaca Markets account (paper or live)
- OpenRouter API key

### 1. Clone

```bash
git clone https://github.com/codebytelabs/MoonshotX.git
cd MoonshotX
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

Create `backend/.env`:

```env
# Alpaca
ALPACA_API_KEY=your_alpaca_key
ALPACA_SECRET_KEY=your_alpaca_secret
ALPACA_BASE_URL=https://paper-api.alpaca.markets   # or live URL

# OpenRouter (LLM)
OPENROUTER_API_KEY=your_openrouter_key

# LLM Models (optional — defaults shown)
Openrouter_Quick_Primary_Model=google/gemini-2.5-flash-lite-preview-09-2025
Openrouter_Research_Primary_Model=anthropic/claude-haiku-4-5

# MongoDB
MONGODB_URI=mongodb://localhost:27017
MONGODB_DB=moonshotx

# Frontend
REACT_APP_BACKEND_URL=http://localhost:8001
```

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
The regime classifier uses VIX and Fear & Greed Index:

| Regime | VIX | F&G |
|--------|-----|-----|
| `extreme_fear` | >35 | <20 |
| `fear` | >25 | <35 |
| `choppy` | >20 | <45 |
| `neutral` | 15-20 | 45-60 |
| `bull` | <15 | >60 |

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
PRE_MARKET_WINDOW_MINS = 25   # run morning brief T-25min before open
ENTRY_SCAN_INTERVAL_MINS = 5  # scan for new entries every 5 min
EOD_NO_ENTRY_MINS = 30        # no new entries within 30min of close
EOD_CLOSE_MINS = 15           # force-close all positions 15min before close
```

---

## API Reference

All endpoints are prefixed with `/api`.

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/system/status` | Loop status, regime, loop count |
| `POST` | `/system/start` | Start the trading loop |
| `POST` | `/system/stop` | Stop the trading loop |
| `GET` | `/account` | Alpaca account info + portfolio value |
| `GET` | `/positions` | Open positions with P&L |
| `GET` | `/trades` | Closed trade history |
| `GET` | `/regime` | Current market regime + VIX/F&G |
| `GET` | `/morning-brief` | Latest pre-market intelligence brief |
| `GET` | `/universe` | Scanned universe ranked by score |
| `GET` | `/nav` | Portfolio NAV history (1D/1W/1M) |
| `GET` | `/agent-logs` | LLM decision logs |
| `GET` | `/agent-logs/{id}` | Full detail for a specific decision |
| `GET` | `/performance` | Win rate, avg gain/loss, Sharpe-like stats |
| `GET` | `/positions/concentration` | Sector breakdown |
| `GET` | `/positions/earnings` | Earnings proximity for open positions |
| `GET` | `/positions/management` | Trailing stop / HWM tracking state |
| `GET` | `/config` | Active model names + cost summary |
| `WS` | `/ws` | WebSocket live feed (loop ticks, trades, morning brief) |

---

## Trading Logic

### Daily Flow
```
06:00 EST  Market scans universe, regime updates
09:05 EST  Morning Brief fires: futures + news + LLM macro analysis
09:30 EST  Market open → pre-market queue executed (top picks from brief)
09:30-15:00  Every 60s: position management (exits, trailing stops)
             Every 5min: new entry scan (scanner → LLM pipeline)
15:30 EST  No new entries
15:45 EST  Force-close all positions
16:00 EST  Market close, flat overnight
```

### Entry Decision Flow
```
Universe Scanner (200+ tickers)
    → Bayesian pre-filter (score ≥ 0.45)
    → Sector concentration check
    → Earnings blackout check
    → Re-entry cooldown check (MongoDB-persisted)
    → Pending order dedup guard
    → Phase A: QUICK LLM screen (all candidates, 1 call)
    → Phase B: DEEP LLM trade plan (shortlist, 1 call)
    → Intraday momentum gate (5-min bars, regime-aware)
    → Position size (regime × portfolio × ATR-based)
    → Market order + stop order
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
- Paper trading by default — change `ALPACA_BASE_URL` for live trading
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

- [x] Batched LLM pipeline (99% cost reduction)
- [x] Morning intelligence brief (pre-market macro analysis)
- [x] Regime-adaptive position management
- [x] Daily performance tracking vs indices
- [ ] Live trading deployment with position limits
- [ ] Slack/Telegram alerts for entries, exits, morning brief
- [ ] Options flow data integration (unusual whales)
- [ ] Multi-account support
- [ ] Backtesting engine against historical data
- [ ] Mobile-responsive frontend
- [ ] Docker / docker-compose deployment

---

## Disclaimer

MoonshotX is provided for **educational and research purposes only**. It is not financial advice. Trading stocks involves substantial risk of loss. Past performance (including paper trading results) does not guarantee future results. The authors are not responsible for any financial losses incurred from using this software. Always do your own research and consult a qualified financial advisor before making investment decisions.

---

## License

MIT License — see [LICENSE](LICENSE) for details.

---

<div align="center">
Built with care by <a href="https://github.com/codebytelabs">codebytelabs</a>
</div>
