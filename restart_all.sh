#!/bin/bash
# MoonshotX — Kill everything and restart backend + frontend
set -e

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
BACKEND_DIR="$ROOT_DIR/backend"
FRONTEND_DIR="$ROOT_DIR/frontend"
VENV="$BACKEND_DIR/venv"
BACKEND_LOG="/tmp/moonshotx_backend.log"
BACKEND_PID_FILE="/tmp/moonshotx_backend.pid"
FRONTEND_PID_FILE="/tmp/moonshotx_frontend.pid"

# ── Colors ────────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'

log()  { echo -e "${CYAN}[MoonshotX]${NC} $1"; }
ok()   { echo -e "${GREEN}[OK]${NC} $1"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }

echo ""
echo -e "${CYAN}╔══════════════════════════════════════╗${NC}"
echo -e "${CYAN}║       MoonshotX — Full Restart       ║${NC}"
echo -e "${CYAN}╚══════════════════════════════════════╝${NC}"
echo ""

# ── 1. Kill by PID files ──────────────────────────────────────────────────────
log "Stopping existing processes..."

for pidfile in "$BACKEND_PID_FILE" "$FRONTEND_PID_FILE"; do
    if [ -f "$pidfile" ]; then
        pid=$(cat "$pidfile" 2>/dev/null)
        if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
            kill "$pid" 2>/dev/null && warn "Killed PID $pid ($pidfile)"
        fi
        rm -f "$pidfile"
    fi
done

# ── 2. Kill by port (belt + suspenders) ──────────────────────────────────────
for port in 8001 3000; do
    pids=$(lsof -ti tcp:"$port" 2>/dev/null || true)
    if [ -n "$pids" ]; then
        echo "$pids" | xargs kill -9 2>/dev/null || true
        warn "Killed process(es) on port $port"
    fi
done

# ── 3. Kill any stray uvicorn / yarn / node for this project ─────────────────
pkill -f "uvicorn server:app" 2>/dev/null || true
pkill -f "moonshotx.*yarn"    2>/dev/null || true

sleep 2
ok "All processes stopped"

# ── 4. Verify MongoDB is up ───────────────────────────────────────────────────
log "Checking MongoDB..."
if mongosh --eval 'db.runCommand({ping:1}).ok' --quiet 2>/dev/null | grep -q 1; then
    ok "MongoDB connected"
else
    warn "MongoDB may not be running — start it with: brew services start mongodb-community"
fi

# ── 5. Start Backend ─────────────────────────────────────────────────────────
log "Starting backend (port 8001)..."
cd "$BACKEND_DIR"
nohup "$VENV/bin/uvicorn" server:app --host 0.0.0.0 --port 8001 > "$BACKEND_LOG" 2>&1 &
echo $! > "$BACKEND_PID_FILE"
BACKEND_PID=$!

# Wait for backend to be ready
for i in $(seq 1 15); do
    sleep 1
    if curl -sf http://localhost:8001/api/ > /dev/null 2>&1; then
        ok "Backend up (PID $BACKEND_PID) → http://localhost:8001"
        # Print active LLM provider and models
        cfg=$(curl -sf http://localhost:8001/api/config 2>/dev/null || echo "{}")
        provider=$(echo "$cfg" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('llm_provider','?').upper())" 2>/dev/null || echo "?")
        quick=$(echo "$cfg"   | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('quick_model','?'))" 2>/dev/null || echo "?")
        deep=$(echo "$cfg"    | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('deep_model','?'))" 2>/dev/null || echo "?")
        echo -e "  ${CYAN}LLM Provider:${NC} ${YELLOW}$provider${NC}"
        echo -e "  ${CYAN}Quick model:${NC}  $quick"
        echo -e "  ${CYAN}Deep model:${NC}   $deep"
        break
    fi
    if [ $i -eq 15 ]; then
        warn "Backend slow to start — check logs: tail -f $BACKEND_LOG"
    fi
done

# ── 6. Start Frontend ─────────────────────────────────────────────────────────
log "Starting frontend (port 3000)..."
cd "$FRONTEND_DIR"
nohup yarn start > /tmp/moonshotx_frontend.log 2>&1 &
echo $! > "$FRONTEND_PID_FILE"
FRONTEND_PID=$!

# Give it a moment to begin loading
sleep 3
if kill -0 $FRONTEND_PID 2>/dev/null; then
    ok "Frontend starting (PID $FRONTEND_PID) → http://localhost:3000"
    ok "Frontend log: tail -f /tmp/moonshotx_frontend.log"
else
    warn "Frontend may have exited — check: tail -f /tmp/moonshotx_frontend.log"
fi

# ── 7. Summary ────────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}╔══════════════════════════════════════╗${NC}"
echo -e "${GREEN}║         MoonshotX is Live!           ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════╝${NC}"
echo ""
echo -e "  Backend  → ${CYAN}http://localhost:8001${NC}  (PID $BACKEND_PID)"
echo -e "  Frontend → ${CYAN}http://localhost:3000${NC}  (PID $FRONTEND_PID)"
echo ""
echo -e "  Logs:"
echo -e "    Backend:  tail -f $BACKEND_LOG"
echo -e "    Frontend: tail -f /tmp/moonshotx_frontend.log"
echo ""
echo -e "  To stop all:  ${YELLOW}./stop_all.sh${NC}"
echo ""
