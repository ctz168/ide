#!/usr/bin/env python3
"""
PhoneIDE - Mobile-Optimized Web IDE for Termux/Ubuntu
Lightweight Python server (default port: 12345, configurable via PHONEIDE_PORT env)

Refactored: routes split into routes/ directory, shared utilities in utils.py.
"""

import os
import sys
from flask import Flask, send_from_directory, jsonify, make_response
from flask_cors import CORS

# ==================== Create App ====================
from utils import SERVER_DIR, WORKSPACE, PORT, HOST, CONFIG_DIR, CHAT_HISTORY_FILE

app = Flask(__name__, static_folder=os.path.join(SERVER_DIR, 'static'), static_url_path=None)
app.url_map.strict_slashes = False
CORS(app)

# Ensure all API errors return JSON, not HTML
@app.errorhandler(Exception)
def handle_unhandled_exception(e):
    """Global error handler — always return JSON for API routes."""
    import traceback as _tb
    _tb.print_exc()
    return jsonify({'error': str(e)}), 500

# Handle 405 specifically — show the real error, not a wrapper
@app.errorhandler(405)
def handle_method_not_allowed(e):
    import traceback as _tb
    _tb.print_exc()
    return jsonify({'error': f'405 Method Not Allowed: {e.description or request.method}', 'url': request.path, 'method': request.method}), 405

# Ensure directories exist
os.makedirs(CONFIG_DIR, exist_ok=True)
os.makedirs(WORKSPACE, exist_ok=True)

# ==================== Register Blueprints ====================
from routes.files import bp as files_bp
from routes.run import bp as run_bp
from routes.git import bp as git_bp
from routes.chat import bp as chat_bp
from routes.venv import bp as venv_bp
try:
    from routes.update import bp as update_bp
except Exception as e:
    print(f"[WARN] Failed to load update module: {e}")
    update_bp = None
from routes.server_mgmt import bp as server_mgmt_bp

app.register_blueprint(files_bp)
app.register_blueprint(run_bp)
app.register_blueprint(git_bp)
app.register_blueprint(chat_bp)
app.register_blueprint(venv_bp)
if update_bp:
    app.register_blueprint(update_bp)
app.register_blueprint(server_mgmt_bp)

# ==================== Frontend Serving ====================
# static_url_path=None: disable Flask's built-in static route to avoid
# route conflicts with POST/PUT/DELETE API blueprints.
# Instead we serve static files manually with an explicit GET-only route.

@app.route('/')
def index():
    resp = make_response(send_from_directory(app.static_folder, 'index.html'))
    resp.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    return resp

@app.route('/<path:path>', methods=['GET'])
def static_files(path):
    resp = make_response(send_from_directory(app.static_folder, path))
    resp.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    return resp

# ==================== Main ====================
if __name__ == '__main__':
    # Ensure workspace exists
    os.makedirs(WORKSPACE, exist_ok=True)

    # Set up log file
    from utils import log_write
    _log_file_path = os.path.join(CONFIG_DIR, 'server.log')
    _log_fh = open(_log_file_path, 'a')
    _log_fh.write(f'\n--- PhoneIDE Server starting at {__import__("datetime").datetime.now().isoformat()} ---\n')
    _log_fh.flush()

    # Redirect stdout/stderr to log file while keeping console output
    import io

    class _TeeStream:
        """Tee output to both file and console."""
        def __init__(self, *targets):
            self.targets = targets
            self._lock = __import__('threading').Lock()
        def write(self, data):
            with self._lock:
                for t in self.targets:
                    try:
                        t.write(data)
                        t.flush()
                    except Exception:
                        pass
                log_write(data.rstrip('\n'))
        def flush(self):
            with self._lock:
                for t in self.targets:
                    try:
                        t.flush()
                    except Exception:
                        pass
        def isatty(self):
            return False

    sys.stdout = _TeeStream(sys.__stdout__, _log_fh)
    sys.stderr = _TeeStream(sys.__stderr__, _log_fh)

    from utils import SERVER_DIR as _SD, PORT as _P, HOST as _H, load_config, shlex_quote
    print(f"""
    ╔══════════════════════════════════╗
    ║       PhoneIDE Server           ║
    ║   Mobile Web IDE for Termux     ║
    ╠══════════════════════════════════╣
    ║  Port:    {_P:<22}║
    ║  Host:    {_H:<22}║
    ║  Workspace: {os.path.basename(WORKSPACE):<18}║
    ║  URL:     http://localhost:{_P:<8}║
    ║  Source:  ctz168/ide              ║
    ╚══════════════════════════════════╝
    """)

    # Initialize git if needed (only for non-workspace dirs, skip workspace git init)
    # Git init is now handled per-project via the Project panel
    log_write(f'[SERVER] Starting on {HOST}:{PORT}, workspace: {WORKSPACE}')

    app.run(host=HOST, port=PORT, debug=False, threaded=True)
