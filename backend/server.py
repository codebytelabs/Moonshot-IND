"""MoonshotX-IND — FastAPI Backend Server (India/NSE edition)."""
import asyncio
import logging
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from fastapi import APIRouter, FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
from pydantic import BaseModel, Field

ROOT_DIR = Path(__file__).parent
ENV_FILE = ROOT_DIR.parent / ".env"   # root-level .env — single source of truth
load_dotenv(ENV_FILE, override=True)
sys.path.insert(0, str(ROOT_DIR))

# ── MongoDB ───────────────────────────────────────────────────────────────────
mongo_url = os.environ["MONGO_URL"]
_client = AsyncIOMotorClient(mongo_url)
db = _client[os.environ["DB_NAME"]]

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("moonshotx.server")

# ── WebSocket Manager ─────────────────────────────────────────────────────────
class ConnectionManager:
    def __init__(self):
        self.connections: List[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.connections.append(ws)

    def disconnect(self, ws: WebSocket):
        if ws in self.connections:
            self.connections.remove(ws)

    async def broadcast(self, data: dict):
        dead = []
        for ws in self.connections:
            try:
                await ws.send_json(data)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)


manager = ConnectionManager()


# ── Global Trading State ──────────────────────────────────────────────────────
class TradingState:
    is_running: bool = False
    is_halted: bool = False
    regime: str = "neutral"
    loop_count: int = 0
    started_at: Optional[str] = None


state = TradingState()
trading_task: Optional[asyncio.Task] = None
derivatives_task: Optional[asyncio.Task] = None
strategy_task: Optional[asyncio.Task] = None
chain_collector_task: Optional[asyncio.Task] = None

# ── Trading Components (lazy init) ────────────────────────────────────────────
_kite = None
_pipeline = None
_regime_mgr = None
_scanner = None
_risk_mgr = None
_trading_loop = None
_deri_loop = None
_strategy_loop = None
_chain_collector = None


def get_strategy_loop():
    global _strategy_loop
    if _strategy_loop is None:
        from strategies.strategy_loop import StrategyLoop
        _strategy_loop = StrategyLoop(broadcast_fn=lambda msg: asyncio.get_event_loop().call_soon_threadsafe(
            asyncio.ensure_future, manager.broadcast(msg)
        ))
    return _strategy_loop


def get_chain_collector():
    global _chain_collector
    if _chain_collector is None:
        from dhan.client import DhanClient
        from strategies.chain_collector import ChainCollector
        _chain_collector = ChainCollector(
            client=DhanClient(sandbox=os.environ.get("DHAN_SANDBOX", "true").lower() == "true"),
            db=db,
        )
    return _chain_collector


def get_components():
    global _kite, _pipeline, _regime_mgr, _scanner, _risk_mgr, _trading_loop, _deri_loop
    if _kite is None:
        from broker.kite_client import KiteBroker
        from agents.pipeline import AgentPipeline
        from trading.regime import RegimeManager
        from trading.scanner import UniverseScanner
        from trading.risk import RiskManager
        from trading.loop import TradingLoop
        from derivatives.deri_loop import DerivativesLoop

        _kite = KiteBroker()
        from agents.pipeline import _LLM_API_KEY as _active_llm_key
        _pipeline = AgentPipeline(
            llm_api_key=_active_llm_key,
            broadcast_fn=lambda data: asyncio.create_task(manager.broadcast(data)),
        )
        _regime_mgr = RegimeManager()
        _scanner = UniverseScanner(kite_client=_kite)
        _risk_mgr = RiskManager(db)
        _trading_loop = TradingLoop(
            db=db,
            kite=_kite,
            pipeline=_pipeline,
            regime_manager=_regime_mgr,
            scanner=_scanner,
            risk_manager=_risk_mgr,
            broadcast_fn=lambda data: asyncio.create_task(manager.broadcast(data)),
        )
        _deri_loop = DerivativesLoop(
            db=db,
            kite=_kite,
            regime_manager=_regime_mgr,
            broadcast_fn=lambda data: asyncio.create_task(manager.broadcast(data)),
        )
    return _kite, _pipeline, _regime_mgr, _scanner, _risk_mgr, _trading_loop


def get_deri_loop():
    get_components()   # ensure initialised
    return _deri_loop


# ── App ───────────────────────────────────────────────────────────────────────
app = FastAPI(title="MoonshotX-IND API", version="1.1-IND")
api_router = APIRouter(prefix="/api")

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.environ.get("CORS_ORIGINS", "*").split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Pydantic Models ───────────────────────────────────────────────────────────
class SystemStatus(BaseModel):
    is_running: bool
    is_halted: bool
    regime: str
    loop_count: int
    started_at: Optional[str]
    daily_trades: int
    daily_pnl: float
    llm_cost_today: float

class AnalyzeRequest(BaseModel):
    regime: Optional[str] = "neutral"

# ── Endpoints ─────────────────────────────────────────────────────────────────

@api_router.get("/")
async def root():
    return {"service": "MoonshotX-IND", "version": "1.1-IND", "market": "NSE/BSE India", "status": "online"}


@api_router.get("/system/status", response_model=SystemStatus)
async def get_system_status():
    _, pipeline, _, _, risk_mgr, _ = get_components()  # type: ignore
    return SystemStatus(
        is_running=state.is_running,
        is_halted=state.is_halted,
        regime=state.regime,
        loop_count=state.loop_count,
        started_at=state.started_at,
        daily_trades=risk_mgr.daily_trades,
        daily_pnl=round(risk_mgr.daily_pnl, 2),
        llm_cost_today=round(pipeline.llm_cost_today, 4),
    )


@api_router.post("/system/start")
async def start_trading():
    global trading_task
    if state.is_halted:
        raise HTTPException(400, "System is halted. Reset before starting.")
    if state.is_running:
        return {"status": "already_running"}

    _, _, _, _, _, trading_loop = get_components()
    deri_loop = get_deri_loop()
    state.is_running = True
    state.started_at = datetime.now(timezone.utc).isoformat()
    trading_task = asyncio.create_task(trading_loop.run(state))
    derivatives_task = deri_loop.start()
    sl = get_strategy_loop()
    strategy_task = asyncio.create_task(sl.start())
    await manager.broadcast({"type": "system_status", "is_running": True, "is_halted": False})
    await db.system_events.insert_one({"event": "START", "ts": datetime.now(timezone.utc).isoformat()})
    return {"status": "started"}


@api_router.post("/system/stop")
async def stop_trading():
    global trading_task, derivatives_task, strategy_task
    state.is_running = False
    if trading_task and not trading_task.done():
        trading_task.cancel()
    deri = get_deri_loop()
    if deri:
        deri.stop()
    if derivatives_task and not derivatives_task.done():
        derivatives_task.cancel()
    sl = get_strategy_loop()
    sl.stop()
    if strategy_task and not strategy_task.done():
        strategy_task.cancel()
    await manager.broadcast({"type": "system_status", "is_running": False, "is_halted": False})
    await db.system_events.insert_one({"event": "STOP", "ts": datetime.now(timezone.utc).isoformat()})
    return {"status": "stopped"}


@api_router.post("/system/emergency-halt")
async def emergency_halt():
    global trading_task
    state.is_running = False
    state.is_halted = True
    if trading_task and not trading_task.done():
        trading_task.cancel()

    kite, _, _, _, _, _ = get_components()
    try:
        await kite.cancel_all_orders()
    except Exception:
        pass
    try:
        await kite.close_all_positions()
    except Exception:
        pass

    deri = get_deri_loop()
    if deri:
        deri.stop()
    if derivatives_task and not derivatives_task.done():
        derivatives_task.cancel()

    # Mark all open positions as halted
    await db.positions.update_many(
        {"status": "open"},
        {"$set": {"status": "halted", "closed_at": datetime.now(timezone.utc).isoformat()}},
    )

    await manager.broadcast({"type": "system_status", "is_running": False, "is_halted": True})
    await db.system_events.insert_one({
        "event": "EMERGENCY_HALT",
        "ts": datetime.now(timezone.utc).isoformat(),
        "reason": "manual_emergency_halt",
    })
    return {"status": "halted", "message": "All positions closed, orders cancelled, system halted"}


@api_router.post("/system/reset")
async def reset_system():
    state.is_halted = False
    state.is_running = False
    await manager.broadcast({"type": "system_status", "is_running": False, "is_halted": False})
    return {"status": "reset"}


# ── Kite Auth ─────────────────────────────────────────────────────────────────

@api_router.get("/kite/login-url")
async def kite_login_url():
    """
    Return the Kite Connect login URL.

    Workflow:
      1. Click the returned `login_url` in your already-logged-in Zerodha browser.
      2. Kite redirects to the redirect_url configured in your Kite developer app.
         If that redirect_url is http://localhost:8001/api/kite/callback, the token
         is saved automatically.
         Otherwise paste the full callback URL into GET /api/kite/callback?url=<encoded>.
    """
    from broker.login_fallback import generate_login_url
    url = generate_login_url()
    return {
        "login_url": url,
        "instructions": "Click login_url in your logged-in Zerodha browser. "
                        "After redirect, the server captures the request_token automatically "
                        "if your Kite app redirect_url points to http://localhost:8001/api/kite/callback",
    }


@api_router.get("/kite/callback")
async def kite_callback(request_token: str = None, url: str = None, status: str = None):
    """
    Zerodha redirects here after browser login with ?request_token=XXX&status=success.

    Also accepts ?url=<full_callback_url> if you need to paste it manually.

    On success: exchanges request_token for access_token, writes to .env, reloads KiteBroker.
    """
    from broker.login_fallback import complete_browser_login

    if status and status != "success":
        raise HTTPException(400, f"Kite login failed with status={status}")

    token_input = request_token or url
    if not token_input:
        raise HTTPException(400, "Provide ?request_token=XXX or ?url=<callback_url>")

    try:
        access_token = await asyncio.to_thread(complete_browser_login, token_input)
    except Exception as e:
        logger.error(f"[KITE_CALLBACK] Token exchange failed: {e}")
        raise HTTPException(500, f"Token exchange failed: {e}")

    # Reset KiteBroker so it reinitializes with fresh api_key + new token from .env
    global _kite, _pipeline, _regime_mgr, _scanner, _risk_mgr, _trading_loop
    _kite = None
    _pipeline = None
    _regime_mgr = None
    _scanner = None
    _risk_mgr = None
    _trading_loop = None

    logger.info(f"[KITE_CALLBACK] New access token saved: {access_token[:10]}...")
    await manager.broadcast({"type": "kite_token_refreshed", "ts": datetime.now(timezone.utc).isoformat()})
    return {
        "status": "ok",
        "message": "Access token saved and active",
        "token_preview": f"{access_token[:10]}...",
    }


def _nse_market_times():
    """Return is_market_open, next_open (ISO), next_close (ISO) in UTC."""
    from datetime import timedelta
    import zoneinfo
    IST = zoneinfo.ZoneInfo("Asia/Kolkata")
    now_ist = datetime.now(IST)
    today = now_ist.date()
    open_ist  = datetime(today.year, today.month, today.day, 9, 15, tzinfo=IST)
    close_ist = datetime(today.year, today.month, today.day, 15, 30, tzinfo=IST)
    is_open = open_ist <= now_ist < close_ist and now_ist.weekday() < 5

    if is_open:
        next_open  = (open_ist + timedelta(days=1)).isoformat()
        next_close = close_ist.isoformat()
    else:
        # find next weekday open
        delta = 1
        while True:
            candidate = today + timedelta(days=delta)
            if candidate.weekday() < 5:
                break
            delta += 1
        next_open  = datetime(candidate.year, candidate.month, candidate.day, 9, 15, tzinfo=IST).isoformat()
        next_close = datetime(candidate.year, candidate.month, candidate.day, 15, 30, tzinfo=IST).isoformat()

    return is_open, next_open, next_close


@api_router.get("/account")
async def get_account():
    kite, _, _, _, _, _ = get_components()
    try:
        account = await kite.get_account()
        is_open, next_open, next_close = _nse_market_times()
        return {
            "equity": float(account.get("equity", 0)),
            "cash": float(account.get("cash", 0)),
            "buying_power": float(account.get("buying_power", 0)),
            "last_equity": float(account.get("last_equity", 0)),
            "daily_pnl": float(account.get("equity", 0)) - float(account.get("last_equity", 0)),
            "status": account.get("status", "active"),
            "is_market_open": is_open,
            "next_open": next_open,
            "next_close": next_close,
        }
    except Exception as e:
        raise HTTPException(500, f"Kite error: {str(e)}")


@api_router.get("/positions")
async def get_positions():
    kite, _, _, _, _, loop = get_components()
    try:
        positions = await kite.get_positions()

        # Join with MongoDB to get SL, TP stored at trade entry
        tickers = [p.get("symbol") for p in positions]
        db_docs = await db.positions.find(
            {"ticker": {"$in": tickers}, "status": "open"},
            {"_id": 0, "ticker": 1, "stop_loss": 1, "take_profit": 1, "entry_price": 1, "decision_id": 1}
        ).to_list(50)
        db_map = {d["ticker"]: d for d in db_docs}

        # Pull live trailing stop state from position manager in-memory tracking
        trail_map = loop.position_mgr._tracking if loop and loop.position_mgr else {}

        TRAIL_DISTANCE_PCT = 0.025

        result = []
        for p in positions:
            symbol = p.get("symbol")
            entry = float(p.get("avg_entry_price", 0))
            db_pos = db_map.get(symbol, {})
            stop_loss = db_pos.get("stop_loss") or 0.0
            take_profit = db_pos.get("take_profit") or 0.0
            # Partial profit targets from position manager: +5% / +10%
            t1 = round(entry * 1.05, 2) if entry > 0 else 0.0
            t2 = round(entry * 1.10, 2) if entry > 0 else 0.0
            # Trailing stop from in-memory tracking
            track = trail_map.get(symbol, {})
            trailing_active = track.get("trailing_active", False)
            hwm = track.get("high_watermark", 0.0)
            trailing_stop = round(hwm * (1 - TRAIL_DISTANCE_PCT), 2) if trailing_active and hwm > 0 else 0.0
            result.append({
                "ticker": symbol,
                "qty": int(float(p.get("qty", 0))),
                "entry_price": entry,
                "current_price": float(p.get("current_price", 0)),
                "market_value": float(p.get("market_value", 0)),
                "cost_basis": float(p.get("cost_basis", 0)),
                "unrealized_pnl": float(p.get("unrealized_pl", 0)),
                "unrealized_pnl_pct": float(p.get("unrealized_plpc", 0)) * 100,
                "side": p.get("side", "long"),
                "stop_loss": round(stop_loss, 2),
                "take_profit": round(take_profit, 2),
                "target_1": t1,
                "target_2": t2,
                "trailing_active": trailing_active,
                "trailing_stop": trailing_stop,
                "high_watermark": round(hwm, 2) if hwm > 0 else 0.0,
                "decision_id": db_pos.get("decision_id"),
            })
        return result
    except Exception as e:
        raise HTTPException(500, str(e))


@api_router.get("/trades")
async def get_trades(limit: int = 50):
    trades = await db.positions.find(
        {"status": {"$in": ["closed", "halted"]}}, {"_id": 0}
    ).sort("closed_at", -1).limit(limit).to_list(limit)
    return trades


@api_router.get("/regime")
async def get_regime():
    _, _, regime_mgr, _, _, _ = get_components()
    data = await regime_mgr.get_current()
    return {k: v for k, v in data.items() if k != "updated_at"}


@api_router.get("/universe")
async def get_universe():
    _, _, _, scanner, _, _ = get_components()
    return await scanner.get_ranked(50)


@api_router.get("/universe/discovery")
async def get_universe_discovery():
    _, _, _, scanner, _, _ = get_components()
    stats = scanner.get_discovery_stats()
    return {
        "universe_size": len(scanner._dynamic_universe),
        "tickers": scanner._dynamic_universe,
        **stats,
    }


@api_router.get("/positions/concentration")
async def get_sector_concentration():
    from trading.correlation import get_concentration_summary
    kite, _, _, _, _, _ = get_components()
    positions = await kite.get_positions()
    _, _, regime_mgr, _, _, _ = get_components()
    regime_data = await regime_mgr.get_current()
    regime = regime_data.get("regime", "neutral")
    return {
        "regime": regime,
        "sectors": get_concentration_summary(positions, regime),
        "open_positions": len(positions),
    }


@api_router.get("/positions/results-gate")
async def get_results_gate_check():
    """Check India quarterly results gate for all open positions."""
    from trading.results import IndiaResultsGate
    kite, _, _, _, _, _ = get_components()
    positions = await kite.get_positions()
    gate = IndiaResultsGate()
    results = []
    for pos in positions:
        sym = pos.get("symbol", "")
        blocked, reason = await gate.is_entry_blocked(sym)
        flag_exit, exit_reason = await gate.should_flag_exit(sym)
        results.append({
            "symbol": sym,
            "entry_blocked": blocked,
            "block_reason": reason,
            "flag_exit": flag_exit,
            "exit_reason": exit_reason,
        })
    return results


@api_router.get("/positions/management")
async def get_position_management():
    _, _, _, _, _, loop = get_components()
    tracking = loop.position_mgr._tracking
    return {
        "tracked_positions": len(tracking),
        "positions": {
            sym: {
                "entry_price": t["entry_price"],
                "high_watermark": t["high_watermark"],
                "trailing_active": t["trailing_active"],
                "breakeven_set": t["breakeven_set"],
                "partials_taken": [f"+{p*100:.0f}%" for p in t["partials_taken"]],
                "entry_regime": t["entry_regime"],
                "entry_time": t["entry_time"].isoformat(),
            }
            for sym, t in tracking.items()
        },
    }


@api_router.get("/nav")
async def get_nav_chart(timeframe: str = "1D"):
    """Return portfolio NAV history from MongoDB nav_snapshots.

    Timeframe windows:
      5m  → last 24 hours
      1H  → last 7 days
      6H  → last 30 days
      1D  → last 180 days
      1W  → last 365 days
    """
    logger.info(f"Fetching NAV history from DB for {timeframe}")
    from datetime import timedelta
    now = datetime.now(timezone.utc)
    window = {"5m": timedelta(hours=24), "1H": timedelta(days=7),
              "6H": timedelta(days=30), "1D": timedelta(days=180), "1W": timedelta(days=365)}
    since_iso = (now - window.get(timeframe, timedelta(days=30))).isoformat()

    docs = await db.nav_snapshots.find(
        {"ts": {"$gte": since_iso}}, {"_id": 0, "ts": 1, "value": 1}
    ).sort("ts", 1).to_list(5000)

    return {"timeframe": timeframe, "source": "db", "data": [{"ts": d["ts"], "value": d["value"]} for d in docs]}


async def _get_account_cached():
    try:
        kite, *_ = get_components()
        return await kite.get_account()
    except Exception:
        return {}


@api_router.get("/config")
async def get_config():
    """Return current active configuration (model names, etc.)."""
    from agents.pipeline import QUICK_MODEL, QUICK_FALLBACK, DEEP_MODEL, DEEP_FALLBACK, LLM_PROVIDER
    return {
        "llm_provider": LLM_PROVIDER,
        "quick_model": QUICK_MODEL,
        "quick_fallback": QUICK_FALLBACK,
        "deep_model": DEEP_MODEL,
        "deep_fallback": DEEP_FALLBACK,
    }


@api_router.get("/agent-logs")
async def get_agent_logs(limit: int = 20):
    logs = await db.agent_logs.find({}, {"_id": 0}).sort("created_at", -1).limit(limit).to_list(limit)
    # Return summary only
    return [{
        "decision_id": log.get("decision_id"),
        "ticker": log.get("ticker"),
        "decision": log.get("decision"),
        "regime": log.get("regime"),
        "bayesian_score": log.get("bayesian_score"),
        "duration_s": log.get("duration_s"),
        "reasoning": log.get("reasoning", ""),
        "agent_count": len(log.get("agents", [])),
        "created_at": log.get("created_at"),
    } for log in logs]


@api_router.get("/agent-logs/{decision_id}")
async def get_agent_log_detail(decision_id: str):
    log = await db.agent_logs.find_one({"decision_id": decision_id}, {"_id": 0})
    if not log:
        raise HTTPException(404, "Decision log not found")
    return log


@api_router.post("/trading/analyze/{ticker}")
async def manual_analyze(ticker: str, request: AnalyzeRequest):
    """Manually trigger agent pipeline for an NSE ticker."""
    _, pipeline, regime_mgr, scanner, risk_mgr, _ = get_components()

    md = await scanner.get_ticker_data(ticker.upper())
    if md.get("error"):
        raise HTTPException(400, f"Could not fetch data for {ticker}: {md['error']}")

    regime_data = await regime_mgr.get_current()
    regime = request.regime or regime_data.get("regime", "neutral")
    md.update({
        "regime": regime,
        "india_vix": regime_data.get("india_vix", 16),
        "fear_greed": regime_data.get("fear_greed", 50),
        "fii_direction": regime_data.get("fii_direction", "neutral"),
    })

    result = await pipeline.run(
        ticker=ticker.upper(),
        market_data=md,
        regime=regime,
        portfolio_context={"open_positions": 0, "daily_pnl": risk_mgr.daily_pnl, "regime": regime, "portfolio_value": 500000},
        bayesian_score=md.get("bayesian_score", 0.5),
    )

    await db.agent_logs.insert_one({
        **result,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "manual": True,
    })
    return result


# ── Derivatives endpoints ───────────────────────────────────────────────────────

@api_router.get("/derivatives/status")
async def get_derivatives_status():
    """Return derivatives loop status, open strategies, and portfolio Greeks."""
    deri = get_deri_loop()
    return deri.get_status()


@api_router.get("/derivatives/chain")
async def get_options_chain(symbol: str = "NIFTY"):
    """Fetch live options chain from NSE for NIFTY or BANKNIFTY."""
    try:
        from derivatives.chain import fetch_chain_nse
        chain = await fetch_chain_nse(symbol.upper())
        if chain is None:
            raise HTTPException(503, f"Could not fetch chain for {symbol}")
        return {
            "symbol": chain.symbol,
            "spot": chain.spot,
            "atm_strike": chain.atm_strike,
            "expiry": chain.expiry,
            "lot_size": chain.lot_size,
            "pcr": chain.pcr,
            "strikes_near_atm": chain.strikes_near_atm(6),
            "calls": [
                {"strike": l.strike, "ltp": l.ltp, "iv": l.iv, "oi": l.oi,
                 "volume": l.volume, "bid": l.bid, "ask": l.ask}
                for l in chain.calls if l.strike in chain.strikes_near_atm(6)
            ],
            "puts": [
                {"strike": l.strike, "ltp": l.ltp, "iv": l.iv, "oi": l.oi,
                 "volume": l.volume, "bid": l.bid, "ask": l.ask}
                for l in chain.puts if l.strike in chain.strikes_near_atm(6)
            ],
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


@api_router.get("/derivatives/expiry")
async def get_expiry_info(symbol: str = "NIFTY"):
    """Return current/next weekly expiry info for a symbol."""
    from derivatives.expiry import current_expiry, next_expiry, days_to_expiry, trading_days_to_expiry
    cur  = current_expiry(symbol)
    nxt  = next_expiry(symbol)
    return {
        "symbol":           symbol.upper(),
        "current_expiry":   cur.isoformat(),
        "next_expiry":      nxt.isoformat(),
        "dte":              days_to_expiry(symbol),
        "trading_dte":      trading_days_to_expiry(symbol),
    }


@api_router.get("/derivatives/positions")
async def get_derivatives_positions(limit: int = 20):
    """Return recent derivatives positions from MongoDB."""
    docs = await db.derivatives_positions.find(
        {}, {"_id": 0}
    ).sort("ts", -1).limit(limit).to_list(limit)
    return docs


@api_router.get("/derivatives/events")
async def get_derivatives_events(limit: int = 30):
    """Return recent derivatives events (entries, exits, blocks)."""
    docs = await db.derivatives_events.find(
        {}, {"_id": 0}
    ).sort("ts", -1).limit(limit).to_list(limit)
    return docs


@api_router.post("/derivatives/start")
async def start_derivatives():
    """Manually start the derivatives loop."""
    global derivatives_task
    deri = get_deri_loop()
    if deri.is_running:
        return {"status": "already_running"}
    derivatives_task = deri.start()
    return {"status": "derivatives_started"}


@api_router.post("/derivatives/stop")
async def stop_derivatives():
    """Stop the derivatives loop (does not close open positions)."""
    global derivatives_task
    deri = get_deri_loop()
    deri.stop()
    if derivatives_task and not derivatives_task.done():
        derivatives_task.cancel()
    return {"status": "derivatives_stopped"}


# ── ZenCurve Strategy endpoints ────────────────────────────────────────────────

@api_router.get("/strategies/status")
async def get_strategies_status():
    """Return ZenCurve strategy loop status + portfolio summary."""
    sl = get_strategy_loop()
    return sl.status()


@api_router.get("/strategies/portfolio")
async def get_strategies_portfolio():
    """Return per-strategy virtual capital and P&L breakdown."""
    sl = get_strategy_loop()
    return sl.portfolio.summary()


@api_router.get("/strategies/events")
async def get_strategies_events(limit: int = 50):
    """Return recent strategy loop events."""
    sl = get_strategy_loop()
    return sl._events[-limit:]


@api_router.post("/strategies/start")
async def start_strategies():
    """Manually start the ZenCurve strategy loop."""
    global strategy_task
    sl = get_strategy_loop()
    if sl._running:
        return {"status": "already_running"}
    strategy_task = asyncio.create_task(sl.start())
    return {"status": "started", "sandbox": sl.sandbox}


@api_router.post("/strategies/stop")
async def stop_strategies():
    """Manually stop the ZenCurve strategy loop."""
    global strategy_task
    sl = get_strategy_loop()
    sl.stop()
    if strategy_task and not strategy_task.done():
        strategy_task.cancel()
    return {"status": "stopped"}


@api_router.get("/strategies/backtest")
async def run_backtest_endpoint(
    strategy: str = "zenCurve",
    years: int = 1,
    capital: float = 50000,
    lots: int = 1,
):
    """Run historical backtest and return metrics. strategy: zen|curvature|zenCurve."""
    try:
        from strategies.backtest import run_backtest
        result = await asyncio.to_thread(
            run_backtest, strategy, years, capital, lots
        )
        return result.report()
    except Exception as e:
        raise HTTPException(500, f"Backtest error: {str(e)}")


@api_router.get("/strategies/chain-collector/status")
async def get_chain_collector_status():
    """Status of the background option chain snapshot collector."""
    cc = get_chain_collector()
    try:
        count = await db.nifty_chain_snapshots.count_documents({})
    except Exception:
        count = 0
    status = cc.status()
    status["total_snapshots_stored"] = count
    status["note"] = "Collector runs only in live mode (DHAN_SANDBOX=false). Snapshots enable proper Curvature/ZenCurve backtesting."
    return status


@api_router.get("/strategies/backtest/all")
async def run_all_backtests_endpoint(years: int = 1, capital: float = 50000, lots: int = 1):
    """Run all four strategies and return comparative report."""
    try:
        from strategies.backtest import run_all_backtests
        results = await asyncio.to_thread(run_all_backtests, years, capital, lots)
        return {k: v.report() for k, v in results.items()}
    except Exception as e:
        raise HTTPException(500, f"Backtest error: {str(e)}")


# ── India-specific endpoints ────────────────────────────────────────────────────

@api_router.get("/kite/session")
async def get_kite_session():
    """Check Kite session token validity."""
    try:
        from broker.kite_session import KiteSessionManager
        mgr = KiteSessionManager()
        valid = mgr.is_valid()
        return {
            "valid": valid,
            "api_key_set": bool(os.environ.get("Zerodha_KITE_PAID_API_KEY")),
            "token_set": bool(os.environ.get("Zerodha_KITE_PAID_ACCESS_TOKEN")),
            "paper_mode": os.environ.get("ZERODHA_PAPER_MODE", "true"),
        }
    except Exception as e:
        return {"valid": False, "error": str(e)}


@api_router.get("/nse/circuit-check")
async def get_circuit_check():
    """Live NSE circuit breaker gate status (NIFTY % change today)."""
    try:
        from data.india_market_feed import get_index_data
        from trading.risk import circuit_breaker_gate
        nifty = await asyncio.to_thread(get_index_data, "^NSEI")
        pct = nifty.get("pct_change", 0.0)
        blocked, reason = circuit_breaker_gate(pct)
        return {
            "nifty_pct_change": round(pct, 3),
            "circuit_blocked": blocked,
            "reason": reason,
            "levels": ["-5% (45min halt)", "-10% (105min halt)", "-15% (rest of day)"],
        }
    except Exception as e:
        return {"error": str(e)}


@api_router.get("/results-calendar")
async def get_results_calendar(days_ahead: int = 7):
    """Upcoming NSE/BSE quarterly results within the next N days."""
    try:
        from data.bse_results_calendar import get_upcoming_results
        results = await asyncio.to_thread(get_upcoming_results, days_ahead)
        return {"days_ahead": days_ahead, "count": len(results), "results": results}
    except Exception as e:
        return {"error": str(e)}


@api_router.get("/performance")
async def get_performance():
    # Compute from closed trades
    trades = await db.positions.find(
        {"status": "closed", "unrealized_pnl": {"$exists": True}}, {"_id": 0}
    ).to_list(1000)

    if not trades:
        return {
            "total_trades": 0, "win_rate": 0, "profit_factor": 0,
            "total_pnl": 0, "avg_win": 0, "avg_loss": 0,
            "max_drawdown": 0, "sharpe": 0,
            "regime_breakdown": {}, "equity_curve": [],
        }

    pnls = [t.get("unrealized_pnl", 0) for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]

    profit_factor = sum(wins) / abs(sum(losses)) if losses else float("inf")
    equity = 50000
    curve = []
    peak = equity
    max_dd = 0
    for pnl in pnls:
        equity += pnl
        if equity > peak:
            peak = equity
        dd = (equity - peak) / peak
        max_dd = min(max_dd, dd)
        curve.append(round(equity, 2))

    return {
        "total_trades": len(pnls),
        "win_rate": round(len(wins) / len(pnls), 4) if pnls else 0,
        "profit_factor": round(profit_factor, 3),
        "total_pnl": round(sum(pnls), 2),
        "avg_win": round(sum(wins) / len(wins), 2) if wins else 0,
        "avg_loss": round(sum(losses) / len(losses), 2) if losses else 0,
        "max_drawdown": round(max_dd * 100, 2),
        "equity_curve": curve[-50:],  # last 50 data points
    }


@api_router.get("/system-events")
async def get_system_events(limit: int = 20):
    events = await db.system_events.find({}, {"_id": 0}).sort("ts", -1).limit(limit).to_list(limit)
    return events


@api_router.get("/morning-brief")
async def get_morning_brief():
    """Return the latest morning intelligence brief (runs T-25min before open)."""
    brief = await db.morning_briefs.find_one({}, {"_id": 0}, sort=[("created_at", -1)])
    if not brief:
        return {"status": "no_brief_yet", "message": "Brief runs 25 minutes before market open"}
    return brief


# ── WebSocket ─────────────────────────────────────────────────────────────────
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        await websocket.send_json({"type": "connected", "message": "MoonshotX-IND WebSocket connected"})
        while True:
            await asyncio.sleep(30)
            await websocket.send_json({"type": "ping"})
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception:
        manager.disconnect(websocket)


app.include_router(api_router)


@app.on_event("startup")
async def startup():
    """Auto-start the trading loop when the backend boots.
    The loop handles market-closed gracefully — it just idles until 09:30.
    No manual button press needed."""
    global trading_task, derivatives_task, strategy_task, chain_collector_task
    if state.is_halted:
        logger.info("Startup: system is halted — not auto-starting loop")
        return
    _, _, _, _, _, trading_loop = get_components()
    deri_loop = get_deri_loop()
    state.is_running = True
    trading_task = asyncio.create_task(trading_loop.run(state))
    derivatives_task = deri_loop.start()
    sl = get_strategy_loop()
    strategy_task = asyncio.create_task(sl.start())
    # Start chain collector only in live mode (sandbox has no chain data)
    if os.environ.get("DHAN_SANDBOX", "true").lower() != "true":
        cc = get_chain_collector()
        chain_collector_task = asyncio.create_task(cc.start())
        logger.info("Startup: chain collector auto-started (live mode)")
    logger.info("Startup: equity + derivatives + strategy loops auto-started (idle until NSE opens at 09:15 IST)")
    await db.system_events.insert_one({"event": "AUTO_START", "ts": datetime.now(timezone.utc).isoformat()})


@app.on_event("shutdown")
async def shutdown():
    global trading_task, derivatives_task, strategy_task, chain_collector_task
    state.is_running = False
    if trading_task and not trading_task.done():
        trading_task.cancel()
    if derivatives_task and not derivatives_task.done():
        derivatives_task.cancel()
    if strategy_task and not strategy_task.done():
        strategy_task.cancel()
    if chain_collector_task and not chain_collector_task.done():
        chain_collector_task.cancel()
    _client.close()
