#!/bin/bash
set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}   LC Chatbot - Setup${NC}"
echo -e "${BLUE}========================================${NC}"
echo

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

ISSUES=()

# --- Prerequisites ---
echo -e "${BLUE}Checking prerequisites...${NC}"

# Python (via pyenv or system)
if command -v pyenv &> /dev/null && pyenv version-name &> /dev/null; then
    export PYENV_VERSION=$(pyenv version-name | tr -d ' ')
    PYTHON_CMD="python3"
    PYTHON_VERSION=$($PYTHON_CMD --version 2>&1 | cut -d' ' -f2)
    echo -e "${GREEN}✓${NC} Python $PYTHON_VERSION (pyenv)"
elif command -v python3 &> /dev/null; then
    PYTHON_CMD="python3"
    PYTHON_VERSION=$($PYTHON_CMD --version 2>&1 | cut -d' ' -f2)
    echo -e "${GREEN}✓${NC} Python $PYTHON_VERSION"
else
    echo -e "${RED}✗${NC} Python not found"
    echo -e "  Install pyenv: ${BLUE}brew install pyenv${NC}"
    echo -e "  Then: ${BLUE}pyenv install 3.11 && pyenv global 3.11${NC}"
    ISSUES+=("Python 3.11+ required - install via pyenv or system")
fi

# Node.js
if command -v node &> /dev/null; then
    echo -e "${GREEN}✓${NC} Node.js $(node --version)"
else
    echo -e "${RED}✗${NC} Node.js not found"
    ISSUES+=("Node.js is required")
fi

# npm
if command -v npm &> /dev/null; then
    echo -e "${GREEN}✓${NC} npm $(npm --version)"
else
    echo -e "${RED}✗${NC} npm not found"
    ISSUES+=("npm is required")
fi

echo

# --- Backend ---
echo -e "${BLUE}Setting up backend...${NC}"

if [ ! -d "server/venv" ]; then
    echo "Creating virtual environment..."
    $PYTHON_CMD -m venv server/venv
    echo -e "${GREEN}✓${NC} Virtual environment created"
else
    echo -e "${GREEN}✓${NC} Virtual environment exists"
fi

echo "Installing Python dependencies..."
source server/venv/bin/activate
pip install --upgrade pip -q
pip install -r server/requirements.txt -q
echo -e "${GREEN}✓${NC} Python dependencies installed"

if [ ! -f "server/.env" ]; then
    if [ -f "server/.env.example" ]; then
        cp server/.env.example server/.env
        echo -e "${YELLOW}⚠${NC} Created server/.env from example - edit with your values"
        ISSUES+=("Edit server/.env with your ANTHROPIC_API_KEY")
    else
        ISSUES+=("server/.env missing and no .env.example found")
    fi
else
    echo -e "${GREEN}✓${NC} server/.env exists"
fi

echo "Running migrations..."
cd server
export DJANGO_SETTINGS_MODULE=chatbot_server.settings
if python manage.py migrate --verbosity 0 2>/dev/null; then
    echo -e "${GREEN}✓${NC} Migrations complete"
else
    echo -e "${YELLOW}⚠${NC} Migrations failed - run manually if needed"
fi
cd ..
deactivate

echo

# --- Frontend ---
echo -e "${BLUE}Setting up frontend...${NC}"

if [ -f "package.json" ]; then
    npm install --silent 2>/dev/null
    echo -e "${GREEN}✓${NC} npm dependencies installed"
else
    ISSUES+=("package.json not found")
fi

echo

# --- Env Check ---
echo -e "${BLUE}Checking environment...${NC}"

if [ -f "server/.env" ]; then
    if grep -q "^ANTHROPIC_API_KEY=sk-ant-your-key-here" server/.env; then
        echo -e "${YELLOW}⚠${NC} ANTHROPIC_API_KEY is placeholder"
        ISSUES+=("Set ANTHROPIC_API_KEY in server/.env")
    elif grep -q "^ANTHROPIC_API_KEY=" server/.env; then
        echo -e "${GREEN}✓${NC} ANTHROPIC_API_KEY configured"
    else
        echo -e "${RED}✗${NC} ANTHROPIC_API_KEY missing"
        ISSUES+=("Add ANTHROPIC_API_KEY to server/.env")
    fi
fi

# --- Summary ---
echo
echo -e "${BLUE}========================================${NC}"
if [ ${#ISSUES[@]} -eq 0 ]; then
    echo -e "${GREEN}✓ Setup complete!${NC}"
    echo -e "  Run ${BLUE}./start.sh${NC} to start servers"
else
    echo -e "${YELLOW}Setup done with ${#ISSUES[@]} issue(s):${NC}"
    for issue in "${ISSUES[@]}"; do
        echo -e "  ${YELLOW}•${NC} $issue"
    done
fi
echo -e "${BLUE}========================================${NC}"
