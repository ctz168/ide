#!/bin/bash
# PhoneIDE IDE - Cross-platform one-line installer
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/ctz168/ide/main/install.sh | bash
#
# Works on: Termux, Ubuntu/Debian, Fedora, CentOS, macOS, Alpine, Arch Linux
# Installs: Python 3, pip, flask, flask-cors, clones repo, launches server

set -e

# ── Colors ──────────────────────────────────────────────
if [ -t 1 ]; then
    RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
    BLUE='\033[0;34m'; CYAN='\033[0;36m'; NC='\033[0m'
else
    RED=''; GREEN=''; YELLOW=''; BLUE=''; CYAN=''; NC=''
fi

info()  { echo -e "${BLUE}  [✦]${NC} $1"; }
ok()    { echo -e "${GREEN}  [✓]${NC} $1"; }
warn()  { echo -e "${YELLOW}  [!]${NC} $1"; }
fail()  { echo -e "${RED}  [✗]${NC} $1"; }

echo -e "${CYAN}"
echo "╔══════════════════════════════════════════╗"
echo "║       PhoneIDE IDE Installer             ║"
echo "║       Mobile Web IDE                     ║"
echo "╚══════════════════════════════════════════╝"
echo -e "${NC}"

# ── Detect platform ───────────────────────────────────
detect_platform() {
    if [ -n "$TERMUX_VERSION" ] || [ -d "/data/data/com.termux" ]; then
        echo "termux"
    elif [ "$(uname)" = "Darwin" ]; then
        echo "macos"
    elif command -v apt-get &>/dev/null; then
        echo "debian"
    elif command -v dnf &>/dev/null; then
        echo "fedora"
    elif command -v yum &>/dev/null; then
        echo "centos"
    elif command -v apk &>/dev/null; then
        echo "alpine"
    elif command -v pacman &>/dev/null; then
        echo "arch"
    elif command -v zypper &>/dev/null; then
        echo "opensuse"
    else
        echo "unknown"
    fi
}

PLATFORM=$(detect_platform)
INSTALL_DIR="${PHONEIDE_INSTALL_DIR:-$HOME/phoneide-ide}"
INSTALL_DIR="$(echo "$INSTALL_DIR" | sed "s|~|$HOME|")"

info "Platform: $PLATFORM"
info "Install dir: $INSTALL_DIR"
echo ""

# ── Package installer ─────────────────────────────────
install_packages() {
    # $1 = platform, $2.. = packages
    local platform="$1"; shift
    case "$platform" in
        termux)  pkg install -y "$@" 2>/dev/null ;;
        debian)  sudo apt-get update -qq && sudo apt-get install -y "$@" 2>/dev/null ;;
        fedora)  sudo dnf install -y "$@" 2>/dev/null ;;
        centos)  sudo yum install -y "$@" 2>/dev/null ;;
        alpine)  sudo apk add --no-progress "$@" 2>/dev/null ;;
        arch)    sudo pacman -S --noconfirm "$@" 2>/dev/null ;;
        opensuse) sudo zypper install -y "$@" 2>/dev/null ;;
        macos)   brew install "$@" 2>/dev/null ;;
    esac
}

# ── Step 1: Install Python ─────────────────────────────
echo -e "${BLUE}[1/3]${NC} Checking Python..."

if command -v python3 &>/dev/null && python3 -c "import sys; exit(0 if sys.version_info >= (3,8) else 1)" 2>/dev/null; then
    PYTHON="python3"
    ok "$($PYTHON --version 2>&1)"
elif command -v python &>/dev/null && python -c "import sys; exit(0 if sys.version_info >= (3,8) else 1)" 2>/dev/null; then
    PYTHON="python"
    ok "$($PYTHON --version 2>&1)"
else
    info "Python 3.8+ not found, installing..."
    case "$PLATFORM" in
        termux)  install_packages termux python python-pip ;;
        debian)  install_packages debian python3 python3-pip python3-venv ;;
        fedora)  install_packages fedora python3 python3-pip ;;
        centos)  install_packages centos python3 python3-pip ;;
        alpine)  install_packages alpine python3 py3-pip ;;
        arch)    install_packages arch python python-pip ;;
        opensuse) install_packages opensuse python3 python3-pip ;;
        macos)   install_packages macos python ;;
        *)       warn "Unknown platform — please install Python 3.8+ manually" && exit 1 ;;
    esac

    # Re-detect after install
    if command -v python3 &>/dev/null; then
        PYTHON="python3"
    elif command -v python &>/dev/null; then
        PYTHON="python"
    else
        fail "Python installation failed"
        exit 1
    fi
    ok "$($PYTHON --version 2>&1)"
fi

# ── Step 2: Install pip + dependencies ────────────────
echo ""
echo -e "${BLUE}[2/4]${NC} Installing dependencies..."

# Ensure pip
if ! $PYTHON -m pip --version &>/dev/null 2>&1; then
    info "Installing pip..."
    case "$PLATFORM" in
        termux)
            pkg install -y python-pip 2>/dev/null || true
            ;;
        macos)
            # pip should come with python on macOS
            ;;
        *)
            curl -sS https://bootstrap.pypa.io/get-pip.py | $PYTHON 2>/dev/null || \
            $PYTHON -m ensurepip --upgrade 2>/dev/null || true
            ;;
    esac
fi

if ! $PYTHON -m pip --version &>/dev/null 2>&1; then
    warn "pip not available — trying alternative install..."
fi

# Install flask + flask-cors
# Use --break-system-packages on Debian/Ubuntu 12+ where externally managed env blocks pip
PIP_FLAGS=""
if [ "$PLATFORM" = "debian" ] || [ "$PLATFORM" = "ubuntu" ] || [ "$PLATFORM" = "alpine" ]; then
    $PYTHON -m pip install --break-system-packages flask flask-cors 2>/dev/null || \
    $PYTHON -m pip install flask flask-cors 2>/dev/null || \
        warn "pip install failed — try: $PYTHON -m pip install --user flask flask-cors"
else
    $PYTHON -m pip install flask flask-cors 2>/dev/null || \
        warn "pip install failed — try: $PYTHON -m pip install --user flask flask-cors"
fi

ok "flask + flask-cors"

# ── Git clone with retry + mirror fallback ───────────
CLONE_URLS=(
    "https://github.com/ctz168/ide.git"
    "https://ghfast.top/https://github.com/ctz168/ide.git"
    "https://gh-proxy.com/https://github.com/ctz168/ide.git"
    "https://mirror.ghproxy.com/https://github.com/ctz168/ide.git"
)

# ── Step 3: Clone & launch ────────────────────────────
echo ""
echo -e "${BLUE}[3/4]${NC} Setting up PhoneIDE IDE..."

# Clone or update
if [ -d "$INSTALL_DIR/.git" ]; then
    info "Updating existing installation..."
    cd "$INSTALL_DIR"
    git pull --ff-only 2>&1 || warn "git pull failed — using existing files"
else
    if [ -d "$INSTALL_DIR" ]; then
        warn "Directory $INSTALL_DIR exists but is not a git repo"
        INSTALL_DIR="${INSTALL_DIR}-$(date +%s)"
        warn "Using $INSTALL_DIR instead"
    fi

    # Try cloning with retries and mirror fallback
    CLONE_OK=false
    for url in "${CLONE_URLS[@]}"; do
        for attempt in 1 2 3; do
            info "Cloning (attempt $attempt/3)..."
            CLONE_ERR=$(git clone --depth 1 "$url" "$INSTALL_DIR" 2>&1) && {
                CLONE_OK=true
                break 2
            }
            # Show the actual error on last attempt for this URL
            if [ $attempt -eq 3 ]; then
                warn "Failed with ${url%%\/*} — $(echo "$CLONE_ERR" | tail -1)"
            else
                sleep 2
            fi
        done
        $CLONE_OK && break
        # Clean up failed partial clone
        rm -rf "$INSTALL_DIR"
    done

    if ! $CLONE_OK; then
        fail "All clone attempts failed."
        fail "Last error: $(echo "$CLONE_ERR" | tail -3)"
        echo ""
        info "Try manually:"
        echo -e "  ${CYAN}git clone https://github.com/ctz168/ide.git ~/phoneide-ide${NC}"
        echo -e "  ${CYAN}cd ~/phoneide-ide && python3 server.py${NC}"
        exit 1
    fi

    # Normalize remote to official GitHub (in case we cloned via mirror)
    if [ "$url" != "https://github.com/ctz168/ide.git" ]; then
        cd "$INSTALL_DIR"
        git remote set-url origin https://github.com/ctz168/ide.git 2>/dev/null || true
        info "Remote set to official GitHub URL"
    fi
fi

# ── Step 4: Final verification in target environment ──
echo ""
echo -e "${BLUE}[4/4]${NC} Verifying in target environment..."

VERIFY_FAILED=false
if ! python3 -c "import flask" 2>/dev/null; then
    info "flask not found in current python3 — installing..."
    if command -v pip3 &>/dev/null; then
        pip3 install --break-system-packages flask flask-cors 2>&1 || \
        pip3 install flask flask-cors 2>&1 || \
        pip3 install --user flask flask-cors 2>&1 || VERIFY_FAILED=true
    elif command -v pip &>/dev/null; then
        pip install --break-system-packages flask flask-cors 2>&1 || \
        pip install flask flask-cors 2>&1 || \
        pip install --user flask flask-cors 2>&1 || VERIFY_FAILED=true
    else
        # Try via python3 -m pip
        python3 -m pip install --break-system-packages flask flask-cors 2>&1 || \
        python3 -m pip install flask flask-cors 2>&1 || \
        python3 -m ensurepip 2>/dev/null && python3 -m pip install flask flask-cors 2>&1 || VERIFY_FAILED=true
    fi
    if $VERIFY_FAILED; then
        # Last resort: apt-get (works in proot Ubuntu)
        if command -v apt-get &>/dev/null; then
            apt-get update -qq 2>/dev/null
            apt-get install -y python3-flask 2>/dev/null || \
            apt-get install -y python3-pip 2>/dev/null && pip3 install flask flask-cors 2>/dev/null || \
                warn "Could not install flask automatically"
        else
            warn "Could not install flask automatically"
        fi
    else
        ok "flask installed"
    fi
else
    ok "flask $(python3 -c 'import flask; print(flask.__version__)' 2>/dev/null)"
fi

if ! python3 -c "import flask_cors" 2>/dev/null; then
    info "flask-cors missing — installing..."
    pip3 install --break-system-packages flask-cors 2>/dev/null || \
    pip3 install flask-cors 2>/dev/null || \
    python3 -m pip install flask-cors 2>/dev/null || true
fi

# Final smoke test
if python3 -c "from flask import Flask; from flask_cors import CORS; print('OK')" 2>/dev/null; then
    ok "All dependencies ready"
else
    warn "Flask import still fails — you may need to run:"
    echo -e "  ${CYAN}pip3 install flask flask-cors${NC}"
fi

# Create workspace & config dirs
mkdir -p "$HOME/phoneide_workspace"
mkdir -p "$HOME/.phoneide"

ok "Ready at $INSTALL_DIR"

# ── Done ───────────────────────────────────────────────
echo ""
echo -e "${GREEN}════════════════════════════════════════════${NC}"
echo -e "${GREEN}  Installation complete!${NC}"
echo -e "${GREEN}════════════════════════════════════════════${NC}"
echo ""
echo -e "  Start server:"
echo -e "    ${CYAN}cd $INSTALL_DIR && python3 server.py${NC}"
echo ""
echo -e "  Then open: ${BLUE}http://localhost:1239${NC}"
echo ""

# Auto-launch
if [ -n "$PHONEIDE_AUTO_START" ]; then
    echo -e "${CYAN}Starting server...${NC}"
    cd "$INSTALL_DIR"
    exec python3 server.py
fi
