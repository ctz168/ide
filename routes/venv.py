"""
PhoneIDE - Virtual Environment & Compiler API routes.
"""

import os
import json
import subprocess
from flask import Blueprint, jsonify, request
from utils import handle_error, load_config, save_config, WORKSPACE, shlex_quote

bp = Blueprint('venv', __name__)


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

    if not path:
        path = os.path.join(base, '.venv')

    target = os.path.realpath(path)
    proc_id = run_process(f'python3 -m venv {shlex_quote(target)}', cwd=base)
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
    # Search for common venv directories
    for root, dirs, files in os.walk(base):
        # Skip hidden dirs except .venv
        dirs[:] = [d for d in dirs if not d.startswith('.') or d == '.venv']
        # Limit depth
        depth = root[len(base):].count(os.sep)
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
    return jsonify({'venvs': venvs, 'current': config.get('venv_path', '')})


@bp.route('/api/venv/activate', methods=['POST'])
@handle_error
def activate_venv():
    data = request.json
    path = data.get('path', '')
    config = load_config()
    base = config.get('workspace', WORKSPACE)

    target = os.path.realpath(os.path.join(base, path))
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
