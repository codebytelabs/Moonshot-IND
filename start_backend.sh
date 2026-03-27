#!/bin/bash
# MoonshotX — Start Backend
set -e

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
BACKEND_DIR="$ROOT_DIR/backend"
VENV="$BACKEND_DIR/venv"
ENV_FILE="$ROOT_DIR/.env"

# ── Colors ────────────────────────────────────────────────────────────────────
CYAN='\033[0;36m'; YELLOW='\033[1;33m'; GREEN='\033[0;32m'; NC='\033[0m'

# ── Read LLM config from .env ─────────────────────────────────────────────────
_env() { grep -E "^$1=" "$ENV_FILE" 2>/dev/null | cut -d'=' -f2- | tr -d '\r' || echo "?"; }

PROVIDER=$(_env LLM_PROVIDER)
if [ "$PROVIDER" = "ollama" ]; then
    QUICK=$(_env Ollama_Quick_Primary_Model)
    DEEP=$(_env Ollama_Research_Primary_Model)
else
    PROVIDER="openrouter"
    QUICK=$(_env Openrouter_Quick_Primary_Model)
    DEEP=$(_env Openrouter_Research_Primary_Model)
fi

echo ""
echo -e "${CYAN}🚀 Starting MoonshotX Backend${NC}"
echo -e "   MongoDB : $(mongosh --eval 'db.runCommand({ping:1}).ok' --quiet 2>/dev/null | grep -q 1 && echo 'connected' || echo 'check mongod')"
echo -e "   Provider: ${YELLOW}$(echo $PROVIDER | tr '[:lower:]' '[:upper:]')${NC}"
echo -e "   Quick   : $QUICK"
echo -e "   Deep    : $DEEP"
echo ""

cd "$BACKEND_DIR"
"$VENV/bin/uvicorn" server:app --host 0.0.0.0 --port 8001 --reload
