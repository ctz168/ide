"""
PhoneIDE - Virtual Environment & Compiler API routes.
"""

import os
import json
import subprocess
from flask import Blueprint, jsonify, request
from utils import handle_error, load_config, save_config, WORKSPACE, shlex_quote

bp = Blueprint('venv', __name__)


def _get_effective_base(config=None):
    """Get the effective base directory for venv operations.
    When a project is open, returns the project directory.
    Otherwise returns the workspace root."""
    if config is None:
        config = load_config()
    base = config.get('workspace', WORKSPACE)
    project = config.get('project', None)
    if project:
        project_dir = os.path.join(base, project)
        if os.path.isdir(project_dir):
            return project_dir
    return base


@bp.route('/api/compilers', methods=['GET'])
@handle_error
def list_compilers():
    compilers = []
    checks = [
        ('python3', 'Python 3', 'python3 --version'),
        ('python', 'Python', 'python --version'),
        ('node', 'Node.js', 'node --version'),
        ('gcc', 'GCC C', 'gcc --version | head -1'),
        ('g++', 'G++ C++', 'g++ --version | head -1'),
        ('go', 'Go', 'go version'),
        ('rustc', 'Rust', 'rustc --version'),
        ('ruby', 'Ruby', 'ruby --version'),
        ('lua', 'Lua', 'lua -v'),
        ('bash', 'Bash', 'bash --version | head -1'),
    ]
    for cmd, name, version_cmd in checks:
        try:
            result = subprocess.run(version_cmd, shell=True, capture_output=True, text=True, timeout=5)
            if result.returncode == 0:
                version = result.stdout.strip().split('\n')[0]
                compilers.append({'id': cmd, 'name': name, 'version': version})
        except:
            pass
    return jsonify({'compilers': compilers})


@bp.route('/api/venv/create', methods=['POST'])
@handle_error
def create_venv():
    from utils import run_process

    data = request.json
    path = data.get('path', '')
    config = load_config()
    base = config.get('workspace', WORKSPACE)

    # When a project is open, create venv inside the project directory
    effective_base = _get_effective_base(config)

    if not path:
        path = '.venv'

    # Resolve path relative to the effective base (project dir), not CWD
    if not os.path.isabs(path):
        target = os.path.realpath(os.path.join(effective_base, path))
    else:
        target = os.path.realpath(path)

    proc_id = run_process(f'python3 -m venv {shlex_quote(target)}', cwd=effective_base)
    if proc_id:
        config['venv_path'] = target
    save_config(config)
    return jsonify({'ok': True, 'proc_id': proc_id, 'venv_path': target})


@bp.route('/api/venv/list', methods=['GET'])
@handle_error
def list_venvs():
    config = load_config()
    base = config.get('workspace', WORKSPACE)
    venvs = []

    # When a project is open, only scan the project directory
    scan_base = _get_effective_base(config)

    # Search for common venv directories
    for root, dirs, files in os.walk(scan_base):
        # Skip hidden dirs except .venv
        dirs[:] = [d for d in dirs if not d.startswith('.') or d == '.venv']
        # Limit depth
        depth = root[len(scan_base):].count(os.sep)
        if depth > 2:
            continue
        if 'pyvenv.cfg' in files:
            rel = os.path.relpath(root, base)
            venvs.append({
                'path': rel,
                'full_path': root,
                'active': config.get('venv_path') == root,
                'name': os.path.basename(root),
            })

    # If the current venv_path doesn't exist, clear it (stale)
    current_venv = config.get('venv_path', '')
    project = config.get('project', None)
    cleared_stale = False
    if current_venv:
        if not os.path.isdir(current_venv) or not os.path.exists(os.path.join(current_venv, 'pyvenv.cfg')):
            # venv directory no longer exists — clear it
            config['venv_path'] = ''
            save_config(config)
            current_venv = ''
            cleared_stale = True
        elif project:
            # If a project is open and the venv is outside the project, it's stale
            project_dir = os.path.realpath(os.path.join(base, project))
            if not current_venv.startswith(project_dir):
                config['venv_path'] = ''
                save_config(config)
                current_venv = ''
                cleared_stale = True

    return jsonify({
        'venvs': venvs,
        'current': current_venv,
        'cleared_stale': cleared_stale,
        'has_project': bool(project),
    })


@bp.route('/api/venv/activate', methods=['POST'])
@handle_error
def activate_venv():
    data = request.json
    path = data.get('path', '')
    config = load_config()
    base = config.get('workspace', WORKSPACE)

    if not path:
        return jsonify({'error': 'Path required'}), 400

    # Resolve relative paths from workspace root (matching list_venvs output format)
    if not os.path.isabs(path):
        target = os.path.realpath(os.path.join(base, path))
    else:
        target = os.path.realpath(path)

    if os.path.exists(os.path.join(target, 'pyvenv.cfg')):
        config['venv_path'] = target
        save_config(config)
        return jsonify({'ok': True, 'venv_path': target})
    return jsonify({'error': 'Invalid venv directory'}), 400


@bp.route('/api/venv/packages', methods=['GET'])
@handle_error
def list_packages():
    config = load_config()
    venv_path = config.get('venv_path', '')
    if venv_path and os.path.exists(venv_path):
        pip = os.path.join(venv_path, 'bin', 'pip')
        if not os.path.exists(pip):
            pip = 'pip3'
        result = subprocess.run(f'{pip} list --format=json', shell=True, capture_output=True, text=True, timeout=30)
        if result.returncode == 0:
            try:
                packages = json.loads(result.stdout)
                return jsonify({'packages': packages})
            except:
                pass
    return jsonify({'packages': []})
