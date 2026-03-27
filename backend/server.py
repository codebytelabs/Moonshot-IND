"""MoonshotX — FastAPI Backend Server."""
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

# ── Trading Components (lazy init) ────────────────────────────────────────────
_alpaca = None
_pipeline = None
_regime_mgr = None
_scanner = None
_risk_mgr = None
_trading_loop = None


def get_components():
    global _alpaca, _pipeline, _regime_mgr, _scanner, _risk_mgr, _trading_loop
    if _alpaca is None:
        from trading.alpaca_client import AlpacaClient
        from agents.pipeline import AgentPipeline
        from trading.regime import RegimeManager
        from trading.scanner import UniverseScanner
        from trading.risk import RiskManager
        from trading.loop import TradingLoop

        _alpaca = AlpacaClient(
            api_key=os.environ.get("ALPACA_API_KEY", ""),
            secret_key=os.environ.get("ALPACA_SECRET_KEY", ""),
            base_url=os.environ.get("ALPACA_BASE_URL", "https://paper-api.alpaca.markets"),
        )
        from agents.pipeline import _LLM_API_KEY as _active_llm_key
        _pipeline = AgentPipeline(
            llm_api_key=_active_llm_key,
            broadcast_fn=lambda data: asyncio.create_task(manager.broadcast(data)),
        )
        _regime_mgr = RegimeManager()
        _scanner = UniverseScanner(alpaca_client=_alpaca)
        _risk_mgr = RiskManager(db)
        _trading_loop = TradingLoop(
            db=db,
            alpaca=_alpaca,
            pipeline=_pipeline,
            regime_manager=_regime_mgr,
            scanner=_scanner,
            risk_manager=_risk_mgr,
            broadcast_fn=lambda data: asyncio.create_task(manager.broadcast(data)),
        )
    return _alpaca, _pipeline, _regime_mgr, _scanner, _risk_mgr, _trading_loop


# ── App ───────────────────────────────────────────────────────────────────────
app = FastAPI(title="MoonshotX API", version="1.1")
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
    return {"service": "MoonshotX", "version": "1.1", "status": "online"}


@api_router.get("/system/status", response_model=SystemStatus)
async def get_system_status():
    _, pipeline, _, _, risk_mgr, _ = get_components()
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
    state.is_running = True
    state.started_at = datetime.now(timezone.utc).isoformat()
    trading_task = asyncio.create_task(trading_loop.run(state))
    await manager.broadcast({"type": "system_status", "is_running": True, "is_halted": False})
    await db.system_events.insert_one({"event": "START", "ts": datetime.now(timezone.utc).isoformat()})
    return {"status": "started"}


@api_router.post("/system/stop")
async def stop_trading():
    global trading_task
    state.is_running = False
    if trading_task and not trading_task.done():
        trading_task.cancel()
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

    alpaca, _, _, _, _, _ = get_components()
    try:
        await alpaca.cancel_all_orders()
    except Exception:
        pass
    try:
        await alpaca.close_all_positions()
    except Exception:
        pass

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


@api_router.get("/account")
async def get_account():
    alpaca, _, _, _, _, _ = get_components()
    try:
        account = await alpaca.get_account()
        clock = await alpaca.get_clock()
        return {
            "portfolio_value": float(account.get("portfolio_value", 0)),
            "equity": float(account.get("equity", 0)),
            "cash": float(account.get("cash", 0)),
            "buying_power": float(account.get("buying_power", 0)),
            "last_equity": float(account.get("last_equity", 0)),
            "daily_pnl": float(account.get("equity", 0)) - float(account.get("last_equity", 0)),
            "status": account.get("status", "unknown"),
            "is_market_open": clock.get("is_open", False),
            "next_open": clock.get("next_open", ""),
            "next_close": clock.get("next_close", ""),
        }
    except Exception as e:
        raise HTTPException(500, f"Alpaca error: {str(e)}")


@api_router.get("/positions")
async def get_positions():
    alpaca, _, _, _, _, loop = get_components()
    try:
        positions = await alpaca.get_positions()

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
    alpaca, _, _, _, _, _ = get_components()
    positions = await alpaca.get_positions()
    _, _, regime_mgr, _, _, _ = get_components()
    regime_data = await regime_mgr.get_current()
    regime = regime_data.get("regime", "neutral")
    return {
        "regime": regime,
        "sectors": get_concentration_summary(positions, regime),
        "open_positions": len(positions),
    }


@api_router.get("/positions/earnings")
async def get_earnings_check():
    from trading.earnings import is_in_blackout, get_earnings_date
    alpaca, _, _, _, _, _ = get_components()
    positions = await alpaca.get_positions()
    results = []
    for pos in positions:
        sym = pos.get("symbol", "")
        ed = await get_earnings_date(sym)
        in_bo, reason = await is_in_blackout(sym)
        results.append({
            "symbol": sym,
            "earnings_date": ed.isoformat() if ed else None,
            "in_blackout": in_bo,
            "detail": reason,
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
    """Return portfolio NAV history.
    Primary source: Alpaca /v2/account/portfolio/history (live exchange data).
    Fallback: MongoDB nav_snapshots (only if Alpaca call fails).

    Timeframe → Alpaca (period, bar_timeframe):
      5m  → 1D  / 5Min   (last day,   5-min bars)
      1H  → 1W  / 1H     (last week,  hourly bars)
      6H  → 1M  / 1D     (last month, daily bars)
      1D  → 6M  / 1D     (last 6mo,   daily bars)
      1W  → 1A  / 1D     (last year,  daily bars)
    """
    alpaca, *_ = get_components()

    # Map UI label → (Alpaca period, Alpaca timeframe)
    tf_map = {
        "5m": ("1D",  "5Min"),
        "1H": ("1W",  "1H"),
        "6H": ("1M",  "1D"),
        "1D": ("6M",  "1D"),
        "1W": ("1A",  "1D"),
    }
    alpaca_period, alpaca_tf = tf_map.get(timeframe, ("6M", "1D"))

    # ── Primary: fetch from Alpaca exchange ───────────────────────────────
    data = await alpaca.get_portfolio_history(period=alpaca_period, timeframe=alpaca_tf)

    if data:
        return {"timeframe": timeframe, "source": "alpaca", "data": data}

    # ── Fallback: use MongoDB snapshots ───────────────────────────────────
    logger.warning(f"Alpaca portfolio history unavailable for {timeframe}, falling back to DB")
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
        alpaca, *_ = get_components()
        return await alpaca.get_account()
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
    """Manually trigger agent pipeline for a ticker."""
    _, pipeline, regime_mgr, scanner, risk_mgr, _ = get_components()

    md = await scanner.get_ticker_data(ticker.upper())
    if md.get("error"):
        raise HTTPException(400, f"Could not fetch data for {ticker}: {md['error']}")

    regime_data = await regime_mgr.get_current()
    regime = request.regime or regime_data.get("regime", "neutral")
    md.update({"regime": regime, "vix": regime_data.get("vix", 20), "fear_greed": regime_data.get("fear_greed", 50)})

    result = await pipeline.run(
        ticker=ticker.upper(),
        market_data=md,
        regime=regime,
        portfolio_context={"open_positions": 0, "daily_pnl": risk_mgr.daily_pnl, "regime": regime, "portfolio_value": 50000},
        bayesian_score=md.get("bayesian_score", 0.5),
    )

    await db.agent_logs.insert_one({
        **result,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "manual": True,
    })
    return result


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
        await websocket.send_json({"type": "connected", "message": "MoonshotX WebSocket connected"})
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
    global trading_task
    if state.is_halted:
        logger.info("Startup: system is halted — not auto-starting loop")
        return
    _, _, _, _, _, trading_loop = get_components()
    state.is_running = True
    trading_task = asyncio.create_task(trading_loop.run(state))
    logger.info("Startup: trading loop auto-started (will idle until market opens)")
    await db.system_events.insert_one({"event": "AUTO_START", "ts": datetime.now(timezone.utc).isoformat()})


@app.on_event("shutdown")
async def shutdown():
    global trading_task
    state.is_running = False
    if trading_task and not trading_task.done():
        trading_task.cancel()
    _client.close()
