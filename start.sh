#!/bin/bash

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}   LC Chatbot - Start${NC}"
echo -e "${BLUE}========================================${NC}"
echo

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# --- Pre-flight ---
echo -e "${BLUE}Pre-flight checks...${NC}"

CAN_START=true

if [ ! -d "server/venv" ]; then
    echo -e "${RED}✗${NC} No venv - run ${BLUE}./setup.sh${NC} first"
    CAN_START=false
else
    echo -e "${GREEN}✓${NC} Virtual environment"
fi

if [ ! -d "node_modules" ]; then
    echo -e "${RED}✗${NC} No node_modules - run ${BLUE}./setup.sh${NC} first"
    CAN_START=false
else
    echo -e "${GREEN}✓${NC} Node modules"
fi

if [ ! -f "server/.env" ]; then
    echo -e "${RED}✗${NC} No server/.env - run ${BLUE}./setup.sh${NC} first"
    CAN_START=false
else
    echo -e "${GREEN}✓${NC} Environment file"
    if grep -q "^ANTHROPIC_API_KEY=sk-ant-your-key-here" server/.env || ! grep -q "^ANTHROPIC_API_KEY=" server/.env; then
        echo -e "${YELLOW}⚠${NC} ANTHROPIC_API_KEY not configured - AI won't work"
    fi
fi

echo

if [ "$CAN_START" = false ]; then
    echo -e "${RED}Cannot start. Fix issues above.${NC}"
    exit 1
fi

# --- Setup ---
LOG_DIR="$SCRIPT_DIR/.logs"
mkdir -p "$LOG_DIR"

cleanup() {
    echo
    echo -e "${BLUE}Shutting down...${NC}"
    [ -n "$BACKEND_PID" ] && kill $BACKEND_PID 2>/dev/null && echo -e "${GREEN}✓${NC} Backend stopped"
    [ -n "$FRONTEND_PID" ] && kill $FRONTEND_PID 2>/dev/null && echo -e "${GREEN}✓${NC} Frontend stopped"
    exit 0
}
trap cleanup SIGINT SIGTERM

# --- Start Backend ---
echo -e "${BLUE}Starting backend...${NC}"
(
    source server/venv/bin/activate
    cd server
    export DJANGO_SETTINGS_MODULE=chatbot_server.settings
    python manage.py runserver 0.0.0.0:8001 2>&1
) > "$LOG_DIR/backend.log" 2>&1 &
BACKEND_PID=$!

sleep 1
if ! kill -0 $BACKEND_PID 2>/dev/null; then
    echo -e "${RED}✗${NC} Backend failed:"
    tail -20 "$LOG_DIR/backend.log"
    exit 1
fi
echo -e "${GREEN}✓${NC} Backend on http://localhost:8001"

# --- Start Frontend ---
echo -e "${BLUE}Starting frontend...${NC}"
npm run dev > "$LOG_DIR/frontend.log" 2>&1 &
FRONTEND_PID=$!

sleep 2
if ! kill -0 $FRONTEND_PID 2>/dev/null; then
    echo -e "${RED}✗${NC} Frontend failed:"
    tail -20 "$LOG_DIR/frontend.log"
    cleanup
    exit 1
fi
echo -e "${GREEN}✓${NC} Frontend on http://localhost:5173"

# --- Running ---
echo
echo -e "${BLUE}========================================${NC}"
echo -e "${GREEN}   Servers running!${NC}"
echo -e "${BLUE}========================================${NC}"
echo -e "  Frontend: ${BLUE}http://localhost:5173${NC}"
echo -e "  Backend:  ${BLUE}http://localhost:8001${NC}"
echo -e "  Logs:     ${BLUE}$LOG_DIR/${NC}"
echo
echo -e "${YELLOW}Ctrl+C to stop${NC}"
echo

tail -f "$LOG_DIR/backend.log" "$LOG_DIR/frontend.log"
