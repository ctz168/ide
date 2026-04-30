#!/bin/bash
# PhoneIDE - Start Tray Manager Script
cd "$(dirname "$0")"

DIR="$(pwd)"
VENV_DIR="$DIR/.venv"

# Find the right Python
if [ -f "$VENV_DIR/bin/python" ]; then
    PYTHON="$VENV_DIR/bin/python"
else
    PYTHON=$(command -v python3 || command -v python)
fi

# Install tray dependencies if needed
$PYTHON -c "import pystray, PIL, psutil" 2>/dev/null || {
    echo "[INFO] Installing tray dependencies..."
    $PYTHON -m pip install -q pystray Pillow psutil 2>/dev/null || \
    $PYTHON -m pip install -q --break-system-packages pystray Pillow psutil 2>/dev/null || {
        echo "[ERROR] Failed to install tray dependencies"
        echo "Please run: pip install pystray Pillow psutil"
        exit 1
    }
}

export IDE_PORT="${IDE_PORT:-8080}"
echo "Starting PhoneIDE tray manager (port $IDE_PORT)..."
echo "Right-click the tray icon for options."

exec $PYTHON tray_manager.py
