"""
PhoneIDE - Git API routes.
"""

import os
import subprocess
from datetime import datetime
from flask import Blueprint, jsonify, request
from utils import handle_error, load_config, WORKSPACE, shlex_quote

bp = Blueprint('git', __name__)


def resolve_cwd():
    """Resolve the git working directory from the request (query args or JSON body).

    The frontend sends the *relative* path inside the workspace (e.g. 'myrepo').
    This function joins it with the configured workspace to get an absolute path.
    If no path is provided, the workspace root is used.
    """
    config = load_config()
    base = config.get('workspace', WORKSPACE)

    # Try query string first (GET requests)
    rel = request.args.get('path') or request.args.get('cwd', '')
    # Fall back to JSON body (POST requests)
    if not rel:
        try:
            data = request.json or {}
            rel = data.get('path') or data.get('cwd', '')
        except Exception:
            pass

    if rel and rel.strip():
        target = os.path.realpath(os.path.join(base, rel.strip()))
        # Security: must stay under workspace
        if target.startswith(os.path.realpath(base)):
            return target
    return base


def git_cmd(args, cwd=None, timeout=60):
    config = load_config()
    base = cwd or config.get('workspace', WORKSPACE)
    cmd = f'git -C {shlex_quote(base)} {args}'
    try:
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return {'ok': result.returncode == 0, 'stdout': result.stdout, 'stderr': result.stderr, 'code': result.returncode}
    except subprocess.TimeoutExpired:
        return {'ok': False, 'stdout': '', 'stderr': 'Command timed out', 'code': -1}
    except Exception as e:
        return {'ok': False, 'stdout': '', 'stderr': str(e), 'code': -1}


@bp.route('/api/git/init', methods=['POST'])
@handle_error
def git_init():
    """Initialize a new git repository in the current directory."""
    cwd = resolve_cwd()
    print(f'[git/init] cwd={cwd}')

    # Ensure target directory exists
    if not os.path.isdir(cwd):
        print(f'[git/init] directory does not exist, creating: {cwd}')
        try:
            os.makedirs(cwd, exist_ok=True)
        except Exception as e:
            return jsonify({'error': f'无法创建目录 {cwd}: {str(e)}'}), 400

    r = git_cmd('init', cwd=cwd)
    print(f'[git/init] result: ok={r["ok"]}, stderr={r["stderr"][:200]}')
    if not r['ok']:
        # Friendly error message
        stderr = r['stderr'].strip()
        if 'already' in stderr.lower() or 'reinitialized' in stderr.lower():
            return jsonify({'ok': True, 'path': cwd, 'note': 'Git 仓库已存在'})
        return jsonify({'error': f'git init 失败: {stderr or "未知错误"}'}), 500

    # Set safe defaults for mobile/termux environments
    git_cmd('config user.name "PhoneIDE"', cwd=cwd)
    git_cmd('config user.email "phoneide@local"', cwd=cwd)

    return jsonify({'ok': True, 'path': cwd})


@bp.route('/api/git/status', methods=['GET'])
@handle_error
def git_status():
    cwd = resolve_cwd()

    # Check if this is a git repo at all
    check = git_cmd('rev-parse --is-inside-work-tree', cwd=cwd)
    if not check['ok']:
        return jsonify({
            'branch': '',
            'changed': [],
            'staged': [],
            'untracked': [],
            'not_a_repo': True,
            'error': 'Not a git repository',
        })

    r = git_cmd('status --porcelain -b', cwd=cwd)
    if not r['ok']:
        return jsonify({'error': r['stderr']}), 500
    lines = r['stdout'].strip().split('\n') if r['stdout'].strip() else []
    branch = ''
    changed = []
    staged = []
    untracked = []
    for line in lines:
        if line.startswith('##'):
            branch = line[2:].strip().split('...')[0]
            continue
        if len(line) >= 2:
            status = line[:2]
            filepath = line[3:]
            if status[0] == '?' and status[1] == '?':
                untracked.append({'path': filepath, 'status': 'untracked'})
            elif status[0] != ' ':
                staged.append({'path': filepath, 'status': 'staged', 'change': status[0]})
            else:
                changed.append({'path': filepath, 'status': 'modified', 'change': status[1]})
    return jsonify({
        'branch': branch,
        'changed': changed,
        'staged': staged,
        'untracked': untracked,
    })


@bp.route('/api/git/log', methods=['GET'])
@handle_error
def git_log():
    count = request.args.get('count', 20)
    cwd = resolve_cwd()
    r = git_cmd(f'log --oneline --decorate -n {count} --format="%H|%an|%ae|%at|%s"', cwd=cwd)
    if not r['ok']:
        return jsonify({'commits': [], 'error': r['stderr']})
    commits = []
    for line in r['stdout'].strip().split('\n'):
        if line and '|' in line:
            parts = line.split('|', 4)
            if len(parts) == 5:
                commits.append({
                    'hash': parts[0][:8],
                    'full_hash': parts[0],
                    'author': parts[1],
                    'email': parts[2],
                    'date': datetime.fromtimestamp(int(parts[3])).isoformat(),
                    'message': parts[4],
                })
    return jsonify({'commits': commits})


@bp.route('/api/git/branch', methods=['GET'])
@handle_error
def git_branch():
    cwd = resolve_cwd()
    r = git_cmd('branch -a', cwd=cwd)
    if not r['ok']:
        return jsonify({'branches': [], 'error': r['stderr']})
    branches = []
    current = ''
    for line in r['stdout'].strip().split('\n'):
        if line:
            active = line.startswith('*')
            name = line.lstrip('* ').strip()
            if active:
                current = name
            branches.append({'name': name, 'active': active})
    return jsonify({'branches': branches, 'current': current})


@bp.route('/api/git/checkout', methods=['POST'])
@handle_error
def git_checkout():
    data = request.json
    branch = data.get('branch', '')
    if not branch:
        return jsonify({'error': 'Branch name required'}), 400
    cwd = resolve_cwd()
    r = git_cmd(f'checkout {shlex_quote(branch)}', cwd=cwd)
    if not r['ok']:
        return jsonify({'error': r['stderr']}), 500
    return jsonify({'ok': True})


@bp.route('/api/git/add', methods=['POST'])
@handle_error
def git_add():
    try:
        data = request.json or {}
    except:
        data = {}
    paths = data.get('paths', [])
    cwd = resolve_cwd()
    if not paths:
        r = git_cmd('add -A', cwd=cwd)
    else:
        files = ' '.join(shlex_quote(p) for p in paths)
        r = git_cmd(f'add {files}', cwd=cwd)
    return jsonify({'ok': r['ok'], 'stderr': r['stderr']})


@bp.route('/api/git/commit', methods=['POST'])
@handle_error
def git_commit():
    try:
        data = request.json or {}
    except:
        data = {}
    message = data.get('message', '')
    if not message:
        return jsonify({'error': 'Commit message required'}), 400
    cwd = resolve_cwd()
    r = git_cmd(f'commit -m {shlex_quote(message)}', cwd=cwd)
    if not r['ok']:
        return jsonify({'error': r['stderr']}), 500
    return jsonify({'ok': True})


@bp.route('/api/git/push', methods=['POST'])
@handle_error
def git_push():
    data = request.json
    remote = data.get('remote', 'origin')
    branch = data.get('branch', '')
    set_upstream = data.get('set_upstream', False)
    cmd = f'push {remote} {branch}'
    if set_upstream:
        cmd = f'push -u {remote} {branch}'
    cwd = resolve_cwd()
    r = git_cmd(cmd, cwd=cwd, timeout=120)
    return jsonify({'ok': r['ok'], 'stdout': r['stdout'], 'stderr': r['stderr']})


@bp.route('/api/git/pull', methods=['POST'])
@handle_error
def git_pull():
    data = request.json
    remote = data.get('remote', 'origin')
    branch = data.get('branch', '')
    cwd = resolve_cwd()
    r = git_cmd(f'pull {remote} {branch}', cwd=cwd, timeout=120)
    return jsonify({'ok': r['ok'], 'stdout': r['stdout'], 'stderr': r['stderr']})


@bp.route('/api/git/clone', methods=['POST'])
@handle_error
def git_clone():
    data = request.json
    url = data.get('url', '')
    path = data.get('path', '')
    config = load_config()
    base = config.get('workspace', WORKSPACE)

    if not url:
        return jsonify({'error': 'URL required'}), 400

    if path:
        target = os.path.join(base, path)
    else:
        # Extract repo name from URL
        name = url.rstrip('/').split('/')[-1]
        if name.endswith('.git'):
            name = name[:-4]
        target = os.path.join(base, name)

    r = git_cmd(f'clone {shlex_quote(url)} {shlex_quote(target)}', cwd=base, timeout=300)
    if r['ok']:
        return jsonify({'ok': True, 'path': os.path.relpath(target, base)})
    return jsonify({'error': r['stderr']}), 500


@bp.route('/api/git/remote', methods=['GET'])
@handle_error
def git_remote():
    cwd = resolve_cwd()
    r = git_cmd('remote -v', cwd=cwd)
    if not r['ok']:
        return jsonify({'remotes': []})
    remotes = []
    for line in r['stdout'].strip().split('\n'):
        if line:
            parts = line.split('\t')
            if len(parts) == 2:
                name, url = parts
                url = url.split(' ')[0]
                remotes.append({'name': name.strip(), 'url': url})
    return jsonify({'remotes': remotes})


@bp.route('/api/git/diff', methods=['GET'])
@handle_error
def git_diff():
    staged = request.args.get('staged', 'false').lower() == 'true'
    filepath = request.args.get('file', request.args.get('filepath', ''))
    cwd = resolve_cwd()
    cmd = 'diff --cached' if staged else 'diff'
    if filepath:
        cmd += f' -- {shlex_quote(filepath)}'
    r = git_cmd(cmd, cwd=cwd)
    return jsonify({'ok': r['ok'], 'diff': r['stdout'], 'stderr': r['stderr']})


@bp.route('/api/git/stash', methods=['POST'])
@handle_error
def git_stash():
    data = request.json
    action = data.get('action', 'push')
    cwd = resolve_cwd()
    r = git_cmd(f'stash {action}', cwd=cwd)
    return jsonify({'ok': r['ok'], 'stdout': r['stdout'], 'stderr': r['stderr']})


@bp.route('/api/git/reset', methods=['POST'])
@handle_error
def git_reset():
    data = request.json
    mode = data.get('mode', 'soft')
    cwd = resolve_cwd()
    r = git_cmd(f'reset {mode} HEAD', cwd=cwd)
    return jsonify({'ok': r['ok'], 'stderr': r['stderr']})
