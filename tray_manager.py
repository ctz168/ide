"""
PhoneIDE System Tray Manager
Features: Start/stop service, open dashboard, auto-start on boot (Windows).
"""

import os
import sys
import time
import psutil
import threading
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Optional

IS_PYTHONW = (
    getattr(sys, 'frozen', False)
    or sys.executable.endswith('pythonw.exe')
    or sys.executable.endswith('pythonw3.exe')
    or not sys.stdout
)

try:
    import pystray
    from pystray import MenuItem as Item, Menu
    from PIL import Image, ImageDraw
except ImportError:
    _msg = "Error: Missing tray dependencies. Install with: pip install pystray Pillow psutil"
    print(_msg)
    try:
        with open(Path(__file__).parent / "tray_manager.log", "a", encoding="utf-8") as f:
            f.write(f"[{datetime.now()}] [FATAL] {_msg}\n")
    except Exception:
        pass
    sys.exit(1)

APP_DIR = Path(__file__).parent.absolute()
START_SCRIPT = APP_DIR / "phoneide_server.py"
ICON_PATH = APP_DIR / "ide_icon.png"
LOG_FILE = APP_DIR / "tray_manager.log"


def _get_port():
    """Read IDE_PORT from env or default."""
    port = os.environ.get("IDE_PORT", "").strip()
    if port:
        try:
            return int(port)
        except ValueError:
            pass
    return 8080


SERVICE_PORT = _get_port()


def log(message, level="INFO"):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    msg = f"[{ts}] [{level}] {message}"
    if not IS_PYTHONW:
        try:
            print(msg)
        except Exception:
            pass
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(msg + "\n")
    except Exception:
        pass


def _draw_icon(color='#4CAF50', size=64):
    if ICON_PATH.exists():
        try:
            return Image.open(ICON_PATH).resize((size, size))
        except Exception:
            pass
    image = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle([2, 2, size - 3, size - 3], radius=10, fill='#1a1a2e')
    margin = size // 4
    draw.ellipse([margin, margin, size - margin, size - margin], fill=color)
    return image


def _is_app_process(proc):
    try:
        if not proc.info.get('name') or 'python' not in proc.info['name'].lower():
            return False
        cmdline = ' '.join(proc.info.get('cmdline') or [])
        if 'phoneide_server.py' not in cmdline:
            return False
        app_dir = str(APP_DIR)
        return app_dir in cmdline or app_dir.replace('/', '\\') in cmdline
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return False


def get_server_state():
    try:
        for proc in psutil.process_iter(['pid', 'name', 'cmdline', 'cwd']):
            try:
                if _is_app_process(proc):
                    return "running"
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
    except Exception as e:
        log(f"Error checking state: {e}", "ERROR")
    return "stopped"


def start_server():
    if get_server_state() == "running":
        log("Service already running")
        return True
    python_exe = sys.executable
    if python_exe.lower().endswith("pythonw.exe"):
        python_exe = python_exe[:-1]
        if not Path(python_exe).exists():
            python_exe = sys.executable
    log(f"Starting PhoneIDE: {python_exe} {START_SCRIPT}")
    try:
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"
        env["IDE_PORT"] = str(SERVICE_PORT)
        proc = subprocess.Popen(
            [python_exe, str(START_SCRIPT)],
            cwd=str(APP_DIR),
            env=env,
            creationflags=(subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW)
            if os.name == 'nt' else 0,
        )
        time.sleep(3)
        if proc.poll() is not None:
            log(f"Process exited immediately with code {proc.returncode}", "ERROR")
            return False
        return True
    except Exception as e:
        log(f"Failed to start: {e}", "ERROR")
        return False


def stop_server():
    killed = 0
    for proc in psutil.process_iter(['pid', 'name', 'cmdline', 'cwd']):
        try:
            if _is_app_process(proc):
                proc.kill()
                killed += 1
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    if killed:
        time.sleep(1)
        log(f"Killed {killed} process(es)")
        return True
    return False


def open_dashboard():
    import webbrowser
    webbrowser.open(f"http://localhost:{SERVICE_PORT}")


class TrayManager:
    def __init__(self):
        self.icon = None
        self.state = "stopped"
        self.running = True
        self._autostart = False
        if os.name == 'nt':
            self._autostart = self._check_autostart()

    def _check_autostart(self):
        try:
            import winreg
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_READ)
            try:
                winreg.QueryValueEx(key, "PhoneIDE")
                winreg.CloseKey(key)
                return True
            except WindowsError:
                winreg.CloseKey(key)
                return False
        except Exception:
            return False

    def _toggle_autostart(self):
        if os.name != 'nt':
            return False
        import winreg
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_ALL_ACCESS)
            try:
                winreg.QueryValueEx(key, "PhoneIDE")
                winreg.DeleteValue(key, "PhoneIDE")
                winreg.CloseKey(key)
                self._autostart = False
                log("Auto-start DISABLED")
                return False
            except WindowsError:
                winreg.SetValueEx(key, "PhoneIDE", 0, winreg.REG_SZ, str(APP_DIR / "start_tray.bat"))
                winreg.CloseKey(key)
                self._autostart = True
                log("Auto-start ENABLED")
                return True
        except Exception as e:
            log(f"Auto-start error: {e}", "ERROR")
            return False

    def _status_text(self, *args):
        return "运行状态: ● 已运行" if self.state == "running" else "运行状态: ○ 已停止"

    def _autostart_text(self, *args):
        return "开机启动: ✓ 已启用" if self._autostart else "开机启动:   未启用"

    def create_menu(self):
        menu = Menu(
            Item(lambda *a: self._status_text(), lambda *a: None, enabled=False),
            Menu.SEPARATOR,
            Item(lambda *a: self._autostart_text(), lambda *a: self._toggle_autostart()),
            Menu.SEPARATOR,
            Item("启动服务", self._on_start),
            Item("停止服务", self._on_stop),
            Menu.SEPARATOR,
            Item("管理面板", self._on_open),
            Menu.SEPARATOR,
            Item("退出", self._on_exit),
        )
        return menu

    def _on_start(self, icon=None, item=None):
        if self.state == "running":
            return
        if start_server():
            self._update()

    def _on_stop(self, icon=None, item=None):
        if self.state == "stopped":
            return
        stop_server()
        self._update()

    def _on_open(self, icon=None, item=None):
        if self.state != "running":
            start_server()
            self._update()
            time.sleep(2)
        open_dashboard()

    def _on_exit(self, icon=None, item=None):
        log("Exiting...")
        stop_server()
        self.running = False
        if self.icon:
            self.icon.visible = False
            self.icon.stop()

    def _update(self):
        self.state = get_server_state()
        if self.icon:
            if self.state == "running":
                self.icon.title = "PhoneIDE - 运行中"
                self.icon.icon = _draw_icon('#4CAF50')
            else:
                self.icon.title = "PhoneIDE - 已停止"
                self.icon.icon = _draw_icon('#F44336')
            if os.name == 'nt':
                self._autostart = self._check_autostart()
            self.icon.menu = self.create_menu()

    def _monitor(self):
        while self.running:
            try:
                self._update()
            except Exception as e:
                log(f"Monitor error: {e}", "ERROR")
            time.sleep(3)

    def run(self):
        log("PhoneIDE Tray Manager starting...")
        log(f"App dir: {APP_DIR}")
        log(f"Port: {SERVICE_PORT}")
        menu = self.create_menu()
        self.icon = pystray.Icon("phoneide", _draw_icon('#9E9E9E'), "PhoneIDE - 启动中...", menu)
        log("Auto-starting service...")
        start_server()
        self._update()
        threading.Thread(target=self._monitor, daemon=True).start()
        log("Tray active. Right-click for menu.")
        try:
            self.icon.run()
        except Exception as e:
            log(f"Tray error: {e}", "ERROR")


def main():
    try:
        if os.name != 'nt':
            log("Warning: Auto-start only supported on Windows.")
        TrayManager().run()
    except KeyboardInterrupt:
        pass
    except Exception as e:
        import traceback
        log(f"Fatal: {e}\n{traceback.format_exc()}", "ERROR")


if __name__ == "__main__":
    main()
