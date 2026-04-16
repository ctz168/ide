#!/bin/bash
# PhoneIDE - One-liner installer for Termux
#
# Usage:
#   bash install.sh                        # Install in current directory
#   curl -fsSL https://raw.githubusercontent.com/ctz168/phoneide/main/install.sh | bash
#
# Strictly following stableclaw_android's install.sh pattern.

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

echo -e "${BLUE}"
echo "╔═══════════════════════════════════════════╗"
echo "║     PhoneIDE Installer                    ║"
echo "║     Mobile Web IDE for Android            ║"
echo "╚═══════════════════════════════════════════╝"
echo -e "${NC}"

# Check if running in Termux
if [ ! -d "/data/data/com.termux" ] && [ -z "$TERMUX_VERSION" ]; then
    echo -e "${YELLOW}Warning:${NC} Not running in Termux - some features may not work"
fi

# Detect package manager
if command -v pkg &> /dev/null; then
    PKG_MANAGER="pkg"
    echo -e "  Detected: Termux"
elif command -v apt-get &> /dev/null; then
    PKG_MANAGER="apt-get"
    echo -e "  Detected: Ubuntu/Debian"
elif command -v dnf &> /dev/null; then
    PKG_MANAGER="dnf"
    echo -e "  Detected: Fedora"
else
    echo -e "${YELLOW}Warning:${NC} No recognized package manager found"
    PKG_MANAGER=""
fi

install_pkg() {
    if [ -n "$PKG_MANAGER" ]; then
        echo -e "  ${GREEN}✓${NC} Installing $1..."
        if [ "$PKG_MANAGER" = "pkg" ]; then
            pkg install -y "$1" 2>/dev/null || echo -e "  ${YELLOW}!${NC} Install $1 failed"
        elif [ "$PKG_MANAGER" = "apt-get" ]; then
            sudo apt-get install -y "$1" 2>/dev/null || echo -e "  ${YELLOW}!${NC} Install $1 failed"
        elif [ "$PKG_MANAGER" = "dnf" ]; then
            sudo dnf install -y "$1" 2>/dev/null || echo -e "  ${YELLOW}!${NC} Install $1 failed"
        fi
    fi
}

# Step 1: Install Python
echo ""
echo -e "${BLUE}[1/2]${NC} Installing Python..."
install_pkg python
install_pkg python3

if command -v python3 &> /dev/null; then
    PYTHON="python3"
elif command -v python &> /dev/null; then
    PYTHON="python"
else
    echo -e "  ${RED}✗${NC} Python not found! Please install Python 3.8+"
    exit 1
fi
echo -e "  ${GREEN}✓${NC} $($PYTHON --version 2>&1)"

# Step 2: Install pip + dependencies
echo ""
echo -e "${BLUE}[2/2]${NC} Installing pip and dependencies..."
if ! $PYTHON -m pip --version &> /dev/null; then
    echo -e "  Installing pip..."
    if [ "$PKG_MANAGER" = "pkg" ]; then
        pkg install -y python-pip 2>/dev/null || true
    else
        curl -sS https://bootstrap.pypa.io/get-pip.py | $PYTHON
    fi
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
$PYTHON -m pip install --upgrade pip 2>/dev/null || true
$PYTHON -m pip install flask flask-cors 2>/dev/null || \
    $PYTHON -m pip install -r "$SCRIPT_DIR/requirements.txt" 2>/dev/null || \
    echo -e "  ${YELLOW}!${NC} Python dependencies install failed"

# Create workspace
mkdir -p "$HOME/phoneide_workspace"
mkdir -p "$HOME/.phoneide"

echo ""
echo -e "${GREEN}═══════════════════════════════════════════${NC}"
echo -e "${GREEN}Installation complete!${NC}"
echo -e "${GREEN}═══════════════════════════════════════════${NC}"
echo ""
echo -e "Start PhoneIDE:"
echo "  cd $SCRIPT_DIR"
echo "  python3 server.py"
echo ""
echo -e "Open in browser: ${BLUE}http://localhost:1239${NC}"
echo ""
echo -e "${YELLOW}Tip:${NC} Disable battery optimization for Termux"
echo ""
