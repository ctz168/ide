"""
PhoneIDE - Execution / Run API routes.
"""

import os
import re
import json
import time
import subprocess as sp
from flask import Blueprint, jsonify, request, Response
from utils import (
    handle_error, load_config, WORKSPACE, shlex_quote,
    run_process, stop_process, running_processes, process_outputs,
    _verify_process_state, IS_WINDOWS, get_default_compiler,
)

bp = Blueprint('run', __name__)

# IDE's own port — never kill this
_IDE_PORT = int(os.environ.get('PHONEIDE_PORT', 12345))


# ── Project Type Detection & Run Configuration ────────────────

def _detect_project_type(project_dir):
    """Detect project type by checking for marker files.
    Returns dict with: type, label, scripts (for node), entry_files, etc.

    Priority: Python markers > Node.js > Go > Rust > Java > C/C++
    A package.json alone doesn't mean it's a Node.js project — many Python
    projects include package.json for frontend tooling. We only classify as
    Node.js if there are NO Python markers (requirements.txt, setup.py,
    pyproject.toml, Pipfile, or .py files with main/__main__/app patterns).
    """
    result = {
        'type': 'unknown',
        'label': '未知',
        'scripts': {},       # npm scripts (for node projects)
        'entry_files': [],   # suggested entry files
        'compiler': get_default_compiler(),
    }

    if not project_dir or not os.path.isdir(project_dir):
        return result

    # ── Scan directory once ──
    try:
        dir_files = os.listdir(project_dir)
    except OSError:
        return result

    has_package_json = 'package.json' in dir_files
    has_py_files = False
    has_python_markers = False
    py_entry = []
    for fname in dir_files:
        if fname.endswith('.py'):
            has_py_files = True
            py_entry.append(fname)
        if fname in ('requirements.txt', 'setup.py', 'pyproject.toml', 'Pipfile', 'setup.cfg', 'tox.ini'):
            has_python_markers = True

    # ── Python Project (takes priority over Node.js) ──
    # If there are Python-specific markers OR .py files with no package.json,
    # classify as Python. Even if package.json exists, Python markers like
    # requirements.txt or pyproject.toml are stronger signals.
    if has_python_markers or (has_py_files and not has_package_json):
        result['type'] = 'python'
        result['label'] = 'Python'
        result['compiler'] = get_default_compiler()
        result['entry_files'] = py_entry
        # Also note if there's a package.json (hybrid project)
        if has_package_json:
            result['has_node'] = True
        return result

    # ── Node.js Project ──
    # Only classify as Node.js if package.json exists AND no Python markers
    pkg_json_path = os.path.join(project_dir, 'package.json')
    if has_package_json:
        result['type'] = 'node'
        result['label'] = 'Node.js'
        result['compiler'] = 'node'
        try:
            with open(pkg_json_path, 'r', encoding='utf-8') as f:
                pkg = json.load(f)
            scripts = pkg.get('scripts', {})
            result['scripts'] = scripts
            # Suggest entry files
            main = pkg.get('main', 'index.js')
            if main:
                result['entry_files'].append(main)
            # Check common entry points
            for candidate in ['index.js', 'index.ts', 'app.js', 'server.js', 'main.js', 'src/index.js', 'src/index.ts', 'src/app.js', 'src/main.js']:
                full = os.path.join(project_dir, candidate)
                if os.path.isfile(full) and candidate not in result['entry_files']:
                    result['entry_files'].append(candidate)
            return result
        except Exception:
            result['scripts'] = {}
            return result

    # Check for Go project
    if os.path.isfile(os.path.join(project_dir, 'go.mod')):
        result['type'] = 'go'
        result['label'] = 'Go'
        result['compiler'] = 'go run'
        return result

    # Check for Rust project
    if os.path.isfile(os.path.join(project_dir, 'Cargo.toml')):
        result['type'] = 'rust'
        result['label'] = 'Rust'
        result['compiler'] = 'cargo run'
        return result

    # Check for Java project
    if os.path.isfile(os.path.join(project_dir, 'pom.xml')) or os.path.isfile(os.path.join(project_dir, 'build.gradle')):
        result['type'] = 'java'
        result['label'] = 'Java'
        result['compiler'] = 'java'
        return result

    # Check for C/C++ project
    if os.path.isfile(os.path.join(project_dir, 'Makefile')) or os.path.isfile(os.path.join(project_dir, 'CMakeLists.txt')):
        result['type'] = 'c_cpp'
        result['label'] = 'C/C++'
        result['compiler'] = 'make'
        return result

    # Check for shell scripts
    sh_files = [f for f in os.listdir(project_dir) if f.endswith('.sh')]
    if sh_files:
        result['type'] = 'shell'
        result['label'] = 'Shell'
        result['compiler'] = 'bash'
        result['entry_files'] = sh_files
        return result

    return result


@bp.route('/api/run/detect', methods=['GET'])
@handle_error
def detect_project():
    """Detect the current project type and return run configuration.
    Returns: type, label, scripts (for node), entry_files, compiler, etc.
    """
    config = load_config()
    base = config.get('workspace', WORKSPACE)
    project = config.get('project', None)

    if project:
        project_dir = os.path.join(base, project)
    else:
        project_dir = base

    result = _detect_project_type(project_dir)
    result['project_dir'] = project_dir
    return jsonify(result)


@bp.route('/api/run/npm-install', methods=['POST'])
@handle_error
def run_npm_install():
    """Run npm install (or yarn/pnpm equivalent) in the project directory.
    This is the Node.js equivalent of 'pip install -r requirements.txt'.
    Body: { production: false }  — if true, uses --production flag
    """
    data = request.json or {}
    production = data.get('production', False)

    config = load_config()
    base = config.get('workspace', WORKSPACE)
    project = config.get('project', None)
    if project:
        project_dir = os.path.join(base, project)
        if os.path.isdir(project_dir):
            base = project_dir

    # Verify package.json exists
    pkg_path = os.path.join(base, 'package.json')
    if not os.path.isfile(pkg_path):
        return jsonify({'error': 'package.json not found in project directory'}), 400

    # Use yarn/pnpm if lock file exists
    if os.path.isfile(os.path.join(base, 'yarn.lock')):
        cmd = 'yarn install'
        if production:
            cmd += ' --production'
    elif os.path.isfile(os.path.join(base, 'pnpm-lock.yaml')):
        cmd = 'pnpm install'
        if production:
            cmd += ' --prod'
    else:
        cmd = 'npm install'
        if production:
            cmd += ' --production'

    proc_id = run_process(cmd, cwd=base)
    return jsonify({'ok': True, 'proc_id': proc_id, 'cwd': base, 'cmd': cmd})


@bp.route('/api/run/npm-script', methods=['POST'])
@handle_error
def run_npm_script():
    """Execute an npm script from package.json.
    Body: { script: 'start' | 'dev' | ..., args: '' }
    """
    data = request.json or {}
    script_name = data.get('script', '').strip()
    args = data.get('args', '').strip()

    if not script_name:
        return jsonify({'error': 'No script name provided'}), 400

    config = load_config()
    base = config.get('workspace', WORKSPACE)
    project = config.get('project', None)
    if project:
        project_dir = os.path.join(base, project)
        if os.path.isdir(project_dir):
            base = project_dir

    # Verify the script exists in package.json
    pkg_path = os.path.join(base, 'package.json')
    if os.path.isfile(pkg_path):
        try:
            with open(pkg_path, 'r', encoding='utf-8') as f:
                pkg = json.load(f)
            scripts = pkg.get('scripts', {})
            if script_name not in scripts:
                return jsonify({'error': f'Script "{script_name}" not found in package.json'}), 400
        except Exception:
            pass  # proceed anyway

    # Build the command — only npm start/test/restart/stop can omit 'run';
    # all other scripts need 'npm run <name>'.  yarn never needs 'run'.
    # pnpm always needs 'run' for non-lifecycle scripts.
    _NPM_LIFECYCLE = {'start', 'test', 'restart', 'stop'}
    if os.path.isfile(os.path.join(base, 'yarn.lock')):
        cmd = f'yarn {shlex_quote(script_name)}'
    elif os.path.isfile(os.path.join(base, 'pnpm-lock.yaml')):
        if script_name in _NPM_LIFECYCLE:
            cmd = f'pnpm {shlex_quote(script_name)}'
        else:
            cmd = f'pnpm run {shlex_quote(script_name)}'
    else:
        if script_name in _NPM_LIFECYCLE:
            cmd = f'npm {shlex_quote(script_name)}'
        else:
            cmd = f'npm run {shlex_quote(script_name)}'

    if args:
        cmd += f' {args}'

    # Detect ports from package.json scripts content
    killed_ports = []
    detected_ports = set()
    try:
        with open(pkg_path, 'r', encoding='utf-8') as f:
            pkg = json.load(f)
        script_content = pkg.get('scripts', {}).get(script_name, '')
        detected_ports = _extract_ports_from_code(script_content)
    except Exception:
        pass

    # Also scan project config files for ports
    detected_ports.update(_scan_project_for_ports(base))

    if detected_ports:
        killed_ports = _kill_port_occupants(detected_ports)
        if killed_ports:
            time.sleep(0.5)

    proc_id = run_process(cmd, cwd=base)

    # Monitor for port-in-use errors and auto-retry
    if detected_ports:
        _schedule_port_error_retry(proc_id, cmd, base, detected_ports)

    result = {'ok': True, 'proc_id': proc_id, 'cwd': base, 'cmd': cmd}
    if detected_ports:
        result['detected_ports'] = sorted(detected_ports)
    if killed_ports:
        result['killed_ports'] = killed_ports
    return jsonify(result)


def _extract_ports_from_code(code_text):
    """Extract port numbers from source code.
    Detects patterns like:
      port=5000, port = 8080, app.run(port=3000)
      .listen(3000), HOST:5000, 0.0.0.0:8000
      socket.bind(('0.0.0.0', 9000))
      { port: 3000 }, ("port", 3000), PORT=5000
      vite.config, next.config, webpack, etc.
    """
    ports = set()
    # Pattern 1: port=NNNN or port = NNNN (most common: Flask, Django, etc.)
    for m in re.finditer(r'port\s*=\s*(\d{2,5})', code_text, re.IGNORECASE):
        port = int(m.group(1))
        if 10 <= port <= 65535:
            ports.add(port)
    # Pattern 2: host:port pattern like '0.0.0.0:8000' or 'localhost:5000'
    for m in re.finditer(r'(?:\d+\.\d+\.\d+\.\d+|localhost|127\.0\.0\.1):(\d{2,5})', code_text):
        port = int(m.group(1))
        if 10 <= port <= 65535:
            ports.add(port)
    # Pattern 3: .listen(NNNN) or .listen({ port: NNNN }) (Node.js/Express style)
    for m in re.finditer(r'\.listen\s*\(\s*(?:\{[^}]*?port\s*:\s*)?(\d{2,5})', code_text):
        port = int(m.group(1))
        if 10 <= port <= 65535:
            ports.add(port)
    # Pattern 4: ("port", NNNN) or ('port', NNNN) — tuple/dict style
    for m in re.finditer(r'[\'"]port[\'"\s,]+(\d{2,5})', code_text):
        port = int(m.group(1))
        if 10 <= port <= 65535:
            ports.add(port)
    # Pattern 5: { port: NNNN } or {port: NNNN} — JS object style
    for m in re.finditer(r'\{\s*port\s*:\s*(\d{2,5})\s*\}', code_text):
        port = int(m.group(1))
        if 10 <= port <= 65535:
            ports.add(port)
    # Pattern 6: PORT = NNNN (uppercase env var assignment)
    for m in re.finditer(r'PORT\s*=\s*(\d{2,5})', code_text):
        port = int(m.group(1))
        if 10 <= port <= 65535:
            ports.add(port)
    return ports


def _scan_project_for_ports(base_dir):
    """Scan common project config files for port numbers.
    Checks: .env, .env.local, .env.development, vite.config.*, next.config.*,
    webpack.config.*, docker-compose.*, Dockerfile, package.json scripts, etc.
    """
    ports = set()
    config_files = [
        '.env', '.env.local', '.env.development', '.env.dev',
        'vite.config.js', 'vite.config.ts', 'vite.config.mjs',
        'next.config.js', 'next.config.mjs', 'next.config.ts',
        'webpack.config.js', 'webpack.config.ts',
        'docker-compose.yml', 'docker-compose.yaml',
        'Dockerfile',
        '.flaskenv',
        'config.py', 'settings.py', 'config.json', 'config.yaml', 'config.yml',
        'application.yml', 'application.properties',
    ]
    for fname in config_files:
        fpath = os.path.join(base_dir, fname)
        if os.path.isfile(fpath):
            try:
                with open(fpath, 'r', encoding='utf-8', errors='ignore') as f:
                    text = f.read()
                ports.update(_extract_ports_from_code(text))
            except Exception:
                pass

    # Also check all npm scripts in package.json for port references
    pkg_path = os.path.join(base_dir, 'package.json')
    if os.path.isfile(pkg_path):
        try:
            with open(pkg_path, 'r', encoding='utf-8') as f:
                pkg = json.load(f)
            for script_cmd in pkg.get('scripts', {}).values():
                ports.update(_extract_ports_from_code(script_cmd))
                # Also check --port and -p flags in script commands
                for m in re.finditer(r'(?:--port|-p)\s+(\d{2,5})', script_cmd):
                    port = int(m.group(1))
                    if 10 <= port <= 65535:
                        ports.add(port)
        except Exception:
            pass

    return ports


def _kill_port_occupants(ports):
    """Kill processes occupying the given ports. Returns list of killed info."""
    killed = []
    ide_port = _IDE_PORT
    for port in ports:
        if port == ide_port:
            continue  # Never kill IDE's own port
        if IS_WINDOWS:
            try:
                result = sp.run(
                    f'netstat -ano | findstr :{port} | findstr LISTENING',
                    shell=True, capture_output=True, text=True, timeout=5
                )
                for line in result.stdout.strip().splitlines():
                    parts = line.strip().split()
                    if parts:
                        pid = parts[-1]
                        try:
                            sp.run(f'taskkill /F /PID {pid}', shell=True, capture_output=True, timeout=5)
                            killed.append({'port': port, 'pid': pid})
                        except Exception:
                            pass
            except Exception:
                pass
        else:
            try:
                result = sp.run(
                    f'lsof -ti :{port}', shell=True, capture_output=True, text=True, timeout=5
                )
                for pid_str in result.stdout.strip().splitlines():
                    pid_str = pid_str.strip()
                    if pid_str:
                        try:
                            os.kill(int(pid_str), 9)
                            killed.append({'port': port, 'pid': pid_str})
                        except (OSError, ValueError):
                            pass
            except Exception:
                pass
        # Also stop any of our managed processes that might be using this port
        for proc_id, info in list(running_processes.items()):
            if info.get('running') and str(port) in info.get('cmd', ''):
                stop_process(proc_id)
                killed.append({'port': port, 'managed_proc': proc_id})
    return killed


# Track which procs we've already retried to avoid infinite loops
_port_retry_tried = set()


def _schedule_port_error_retry(proc_id, cmd, cwd, detected_ports):
    """Monitor a process output for port-in-use errors and auto-restart.

    After spawning, waits a few seconds then checks if the process died
    with an EADDRINUSE / address-already-in-use error. If so, kills
    whatever is on that port and re-launches the same command.
    """
    import threading

    def _check():
        # Wait for the process to produce output (up to 5s)
        time.sleep(3)
        if proc_id not in running_processes:
            return
        info = running_processes.get(proc_id, {})
        if info.get('running'):
            return  # Process is fine, no error

        # Process has exited — check if it's a port error
        output_lines = process_outputs.get(proc_id, [])
        output_text = '\n'.join(output_lines).lower()
        port_error_keywords = [
            'eaddrinuse', 'address already in use',
            'port is already in use', 'only one usage of each socket address',
            'errno 98', 'errno 10048', 'error: listen econnrefused',
            'bind: address already in use',
        ]
        has_port_error = any(kw in output_text for kw in port_error_keywords)

        if not has_port_error:
            return  # Different error, don't retry

        # Avoid infinite retry
        retry_key = f"{proc_id}:{','.join(str(p) for p in sorted(detected_ports))}"
        if retry_key in _port_retry_tried:
            return
        _port_retry_tried.add(retry_key)

        # Find which specific port from the error message
        retry_port = None
        for port in detected_ports:
            if str(port) in output_text:
                retry_port = port
                break
        if not retry_port and detected_ports:
            retry_port = list(detected_ports)[0]

        # Kill the port occupant
        if retry_port:
            _kill_port_occupants({retry_port})
            time.sleep(0.5)

        # Re-launch the same command
        new_proc_id = run_process(cmd, cwd=cwd)
        if new_proc_id:
            print(f'[run] Auto-restarted proc {new_proc_id} after port {retry_port} conflict (original: {proc_id})')

    t = threading.Thread(target=_check, daemon=True)
    t.start()


@bp.route('/api/run/execute', methods=['POST'])
@handle_error
def execute_code():
    data = request.json
    code = data.get('code', '')
    file_path = data.get('file_path', '')
    compiler = data.get('compiler', '') or get_default_compiler()
    args = data.get('args', '')
    config = load_config()
    base = config.get('workspace', WORKSPACE)

    # When a project is open, run code in the project directory
    project = config.get('project', None)
    if project:
        project_dir = os.path.join(base, project)
        if os.path.isdir(project_dir):
            base = project_dir

    # Warn if no venv is configured (helpful for Python projects)
    no_venv = False
    if compiler in ('python3', 'python') and not config.get('venv_path'):
        no_venv = True

    # ── Auto-detect ports and kill occupants ──
    # Read the source code to find port numbers, then kill any processes
    # occupying those ports so the new process can start cleanly.
    killed_ports = []
    source_text = code  # start with inline code if provided
    if file_path:
        # file_path can be relative to workspace or project
        target = os.path.realpath(os.path.join(config.get('workspace', WORKSPACE), file_path))
        ws = os.path.realpath(config.get('workspace', WORKSPACE))
        if not target.startswith(ws):
            return jsonify({'error': 'Access denied'}), 403
        # Also read the file content for port detection
        try:
            with open(target, 'r', encoding='utf-8', errors='ignore') as f:
                source_text = f.read()
        except Exception:
            source_text = code
        cmd = f'{compiler} {shlex_quote(target)} {args}'
    else:
        # Write temp file in the effective base (project dir or workspace)
        tmp_file = os.path.join(base, '.phoneide_tmp.py')
        with open(tmp_file, 'w', encoding='utf-8') as f:
            f.write(code)
        cmd = f'{compiler} {shlex_quote(tmp_file)} {args}'

    # Detect ports from source code + args
    detected_ports = _extract_ports_from_code(source_text)
    # Also check args for --port NNNN pattern
    for m in re.finditer(r'(?:--port|-p)\s+(\d{2,5})', args):
        port = int(m.group(1))
        if 10 <= port <= 65535:
            detected_ports.add(port)

    # Also scan project config files for ports
    detected_ports.update(_scan_project_for_ports(base))

    if detected_ports:
        killed_ports = _kill_port_occupants(detected_ports)
        # Delay to let OS fully release the port
        time.sleep(0.5)

    proc_id = run_process(cmd, cwd=base)

    # Monitor output for EADDRINUSE / address already in use errors
    # If detected, kill the occupying process and auto-restart
    if detected_ports:
        _schedule_port_error_retry(proc_id, cmd, base, detected_ports)

    result = {'ok': True, 'proc_id': proc_id, 'no_venv': no_venv, 'cwd': base}
    if detected_ports:
        result['detected_ports'] = sorted(detected_ports)
    if killed_ports:
        result['killed_ports'] = killed_ports
    return jsonify(result)


@bp.route('/api/run/shell', methods=['POST'])
@handle_error
def execute_shell():
    """Execute a raw shell command directly (not as code file).
    Used by the terminal/shell input bar for commands like 'dir', 'ls', 'pip install', etc."""
    data = request.json or {}
    command = data.get('command', '').strip()
    if not command:
        return jsonify({'error': 'No command provided'}), 400

    config = load_config()
    base = config.get('workspace', WORKSPACE)

    # When a project is open, run commands in the project directory
    project = config.get('project', None)
    if project:
        project_dir = os.path.join(base, project)
        if os.path.isdir(project_dir):
            base = project_dir

    # On Windows, wrap with cmd /c to ensure built-in commands (dir, cd, etc.) work
    # On Linux/macOS, use bash -c for consistency
    if IS_WINDOWS:
        cmd = f'cmd /c {command}'
    else:
        cmd = command  # shell=True already uses bash

    # Auto-detect and kill port occupants from the command
    killed_ports = []
    detected_ports = _extract_ports_from_code(command)
    detected_ports.update(_scan_project_for_ports(base))

    if detected_ports:
        killed_ports = _kill_port_occupants(detected_ports)
        if killed_ports:
            time.sleep(0.5)

    proc_id = run_process(cmd, cwd=base)

    if detected_ports:
        _schedule_port_error_retry(proc_id, cmd, base, detected_ports)

    result = {'ok': True, 'proc_id': proc_id, 'cwd': base}
    if detected_ports:
        result['detected_ports'] = sorted(detected_ports)
    if killed_ports:
        result['killed_ports'] = killed_ports
    return jsonify(result)


@bp.route('/api/run/stop', methods=['POST'])
@handle_error
def stop_execution():
    data = request.json
    proc_id = data.get('proc_id', '')
    if proc_id and proc_id in running_processes:
        stopped = stop_process(proc_id)
        return jsonify({'ok': stopped})
    return jsonify({'ok': False})


@bp.route('/api/run/kill-port', methods=['POST'])
@handle_error
def kill_port():
    """Kill any process listening on the given port. Useful before starting a server
    to avoid 'port already in use' errors."""
    import subprocess as sp
    data = request.json or {}
    port = data.get('port')
    if not port:
        return jsonify({'error': 'Port number required'}), 400
    try:
        port = int(port)
    except (ValueError, TypeError):
        return jsonify({'error': 'Invalid port number'}), 400

    # SAFETY: Never kill the IDE's own port
    ide_port = int(os.environ.get('PHONEIDE_PORT', 12345))
    if port == ide_port:
        return jsonify({'error': f'BLOCKED: Port {port} is the PhoneIDE server port — killing it would shut down the IDE. Operation refused.'}), 403

    killed_pids = []
    if IS_WINDOWS:
        # Windows: use netstat to find PID, then taskkill
        try:
            result = sp.run(
                f'netstat -ano | findstr :{port} | findstr LISTENING',
                shell=True, capture_output=True, text=True, timeout=5
            )
            for line in result.stdout.strip().splitlines():
                parts = line.strip().split()
                if parts:
                    pid = parts[-1]
                    try:
                        sp.run(f'taskkill /F /PID {pid}', shell=True, capture_output=True, timeout=5)
                        killed_pids.append(pid)
                    except Exception:
                        pass
        except Exception:
            pass
    else:
        # Linux/macOS: use lsof to find PID, then kill
        try:
            result = sp.run(
                f'lsof -ti :{port}',
                shell=True, capture_output=True, text=True, timeout=5
            )
            pids = result.stdout.strip().splitlines()
            for pid in pids:
                pid = pid.strip()
                if pid:
                    try:
                        os.kill(int(pid), 9)
                        killed_pids.append(pid)
                    except (OSError, ValueError):
                        pass
        except Exception:
            pass

    # Also stop any of our managed processes that might be using this port
    for proc_id, info in list(running_processes.items()):
        if info.get('running') and str(port) in info.get('cmd', ''):
            stop_process(proc_id)
            killed_pids.append(f'managed:{proc_id}')

    if killed_pids:
        return jsonify({'ok': True, 'killed': killed_pids, 'message': f'Killed processes on port {port}: {killed_pids}'})
    else:
        return jsonify({'ok': True, 'killed': [], 'message': f'No process found on port {port}'})


@bp.route('/api/run/processes', methods=['GET'])
@handle_error
def list_processes():
    """List all running and recent processes.
    Uses proc.poll() to verify actual OS process state,
    so this is accurate even after page refreshes."""
    processes = []
    for pid, info in running_processes.items():
        start = info.get('start_time')
        # Verify actual process state at the OS level
        running = _verify_process_state(pid)
        uptime = ''
        if start:
            elapsed = time.time() - start
            mins, secs = divmod(int(elapsed), 60)
            hours, mins = divmod(mins, 60)
            if hours > 0:
                uptime = f'{hours}h {mins}m {secs}s'
            elif mins > 0:
                uptime = f'{mins}m {secs}s'
            else:
                uptime = f'{secs}s'
        # Truncate command for display
        cmd = info.get('cmd', '')
        if len(cmd) > 120:
            cmd = cmd[:120] + '...'
        processes.append({
            'id': pid,
            'running': running,
            'cwd': info.get('cwd', ''),
            'cmd': cmd,
            'exit_code': info.get('exit_code'),
            'uptime': uptime,
            'start_time': start,
        })
    return jsonify({'processes': processes})


@bp.route('/api/run/output', methods=['GET'])
@handle_error
def get_output():
    proc_id = request.args.get('proc_id', '')
    since = int(request.args.get('since', 0))

    if proc_id and proc_id in process_outputs:
        outputs = process_outputs[proc_id][since:]
        # Verify actual process state (not just the flag)
        is_running = _verify_process_state(proc_id)
        return jsonify({
            'outputs': outputs,
            'since': len(process_outputs[proc_id]),
            'running': is_running,
        })
    return jsonify({'outputs': [], 'since': 0, 'running': False})


@bp.route('/api/run/output/stream', methods=['GET'])
def stream_output():
    """SSE endpoint for real-time output"""
    proc_id = request.args.get('proc_id', '')

    def generate():
        idx = 0
        # Wait briefly for processOutputs to be populated
        time.sleep(0.15)
        while True:
            if proc_id and proc_id in process_outputs:
                outputs = process_outputs[proc_id]
                if idx < len(outputs):
                    for item in outputs[idx:]:
                        evt_type = item.get('type', 'stdout')
                        # Send as named SSE event so frontend addEventListener works
                        yield f"event: {evt_type}\ndata: {json.dumps(item)}\n\n"
                    idx = len(outputs)

                # Verify actual process state (not just the flag)
                is_running = _verify_process_state(proc_id)
                if not is_running:
                    exit_code = running_processes.get(proc_id, {}).get('exit_code', 0)
                    yield f"event: exit\ndata: {json.dumps({'exit_code': exit_code or 0})}\n\n"
                    break
            else:
                # proc_id not found — process may have finished before we started
                # Check one more time after a brief delay
                time.sleep(0.2)
                if proc_id and proc_id not in process_outputs:
                    yield f"event: done\ndata: \"Process not found\"\n\n"
                    break

            time.sleep(0.1)

    return Response(generate(), mimetype='text/event-stream',
                    headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})
