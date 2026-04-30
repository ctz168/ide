"""
PhoneIDE - IDE Update API routes.

Checks for updates from ctz168/ide GitHub repository and applies them
by spawning a completely detached standalone update script.
"""

import os
import re
import sys
import json
import subprocess
import time
import tempfile
import urllib.request
import urllib.error
from flask import Blueprint, jsonify, request
from utils import handle_error, load_config, WORKSPACE, SERVER_DIR, PORT, HOST, CONFIG_DIR, log_write
from routes.git import git_cmd

bp = Blueprint('update', __name__)

# GitHub repos
IDE_REPO = 'ctz168/ide'          # Code (server, routes, static)
APK_REPO = 'ctz168/phoneide'     # APK releases

# GitHub API URLs
IDE_COMMITS_URL = f'https://api.github.com/repos/{IDE_REPO}/commits/main'
APK_RELEASES_URL = f'https://api.github.com/repos/{APK_REPO}/releases/latest'

# Update status file (written by the standalone update script)
UPDATE_STATUS_FILE = os.path.join(CONFIG_DIR, 'update_status.json')


def _fetch_github_json(url, timeout=15):
    """Helper to fetch JSON from GitHub API."""
    req = urllib.request.Request(url, headers={
        'User-Agent': 'PhoneIDE-Server',
        'Accept': 'application/vnd.github.v3+json',
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def _parse_version(version_str):
    """Parse version string like '3.0.40' or '3.0.40-build.72' into comparable tuple.

    Returns (major, minor, patch, build) tuple. Build defaults to 0 if absent.
    Returns None if parsing fails.
    """
    if not version_str:
        return None
    cleaned = version_str.lstrip('v')
    m = re.match(r'(\d+)\.(\d+)\.(\d+)(?:-build\.?(\d+))?', cleaned)
    if m:
        return (int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4) or 0))
    return None


def _get_current_version():
    """Read the current app version. Priority: version.txt > git describe."""
    # 1. version.txt (written by CI build when bundled in APK)
    vtxt = os.path.join(SERVER_DIR, 'version.txt')
    if os.path.exists(vtxt):
        try:
            with open(vtxt, 'r', encoding='utf-8') as f:
                v = f.read().strip()
                if v:
                    return v
        except Exception:
            pass
    # 2. git describe (when running from git clone)
    try:
        r = git_cmd('describe --tags --abbrev=0', cwd=SERVER_DIR)
        if r['ok'] and r['stdout'].strip():
            return r['stdout'].strip().lstrip('v')
    except Exception:
        pass
    return '0.0.0'


def _get_local_commit():
    """Get local commit SHA. Try git, then commit.txt fallback."""
    try:
        r = git_cmd('rev-parse HEAD', cwd=SERVER_DIR)
        if r['ok'] and r['stdout'].strip():
            return r['stdout'].strip()[:40]
    except Exception:
        pass
    # Fallback: read commit.txt (written by CI build when bundled in APK)
    ctxt = os.path.join(SERVER_DIR, 'commit.txt')
    if os.path.exists(ctxt):
        try:
            with open(ctxt, 'r', encoding='utf-8') as f:
                sha = f.read().strip()[:40]
                if sha and len(sha) >= 7:
                    return sha
        except Exception:
            pass
    return ''


@bp.route('/api/update/check', methods=['POST'])
@handle_error
def update_check():
    """Check for code updates from ctz168/ide repository."""
    try:
        # Get current version
        current_version = _get_current_version()

        # Get local commit SHA
        local_sha = _get_local_commit()

        # === Code Update Check (from ctz168/ide commits) ===
        remote_sha = ''
        remote_message = ''
        code_update = False
        try:
            commit_data = _fetch_github_json(IDE_COMMITS_URL)
            remote_sha = commit_data.get('sha', '')
            remote_message = commit_data.get('commit', {}).get('message', '')

            if local_sha and remote_sha and local_sha != remote_sha:
                code_update = True
        except Exception as e:
            # If GitHub API fails, assume no update
            log_write(f'[UPDATE] GitHub check failed: {e}')
            pass

        return jsonify({
            'update_available': code_update,
            'apk_update': False,  # Disabled
            'code_update': code_update,
            'current_version': current_version,
            'new_version': '',
            'latest_tag': '',
            'release_name': '',
            'release_body': '',
            'release_date': '',
            'release_url': '',
            'apk_url': '',
            'apk_size': 0,
            'apk_size_human': '',
            'local_sha': local_sha[:8] if local_sha else 'unknown',
            'remote_sha': remote_sha[:8] if remote_sha else 'unknown',
            'remote_message': remote_message.split('\n')[0] if remote_message else '',
            'commits_behind': 1 if code_update else 0,
        })
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return jsonify({'error': 'No releases found', 'update_available': False, 'current_version': _get_current_version()})
        return jsonify({'error': f'GitHub API error: {e.code}', 'update_available': False, 'current_version': _get_current_version()})
    except Exception as e:
        return jsonify({'error': str(e), 'update_available': False, 'current_version': _get_current_version()})


@bp.route('/api/update/apply', methods=['POST'])
@handle_error
def update_apply():
    """Spawn a fully detached update script that pulls code and restarts the server.

    The update runs as an independent process (start_new_session=True) so it:
      - Survives even if the server crashes during the update
      - Won't be killed when the server process exits
      - Writes progress to update_status.json for frontend polling
    """
    # Clear any previous update status
    try:
        if os.path.exists(UPDATE_STATUS_FILE):
            os.remove(UPDATE_STATUS_FILE)
    except Exception:
        pass

    # Path to the standalone update script
    update_script = os.path.join(SERVER_DIR, 'scripts', 'update_code.py')

    if not os.path.exists(update_script):
        log_write(f'[UPDATE] Standalone update script not found: {update_script}')
        return jsonify({
            'ok': False,
            'error': f'更新脚本不存在: scripts/update_code.py',
        }), 500

    try:
        # Spawn the update script as a fully detached process.
        # start_new_session=True creates a new process group so the update
        # script survives even if the current server process is killed.
        log_file = open(os.path.join(CONFIG_DIR, 'update.log'), 'a', encoding='utf-8')

        subprocess.Popen(
            [sys.executable, update_script, SERVER_DIR, str(PORT)],
            stdout=log_file,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            cwd=SERVER_DIR,
            env=os.environ.copy(),
        )
        log_file.close()  # Popen has its own handle now

        log_write(f'[UPDATE] Spawned detached update script (PID will be independent)')
        return jsonify({
            'ok': True,
            'method': 'bg_update',
            'message': '代码正在后台更新，服务器将自动重启。',
        })
    except Exception as e:
        log_write(f'[UPDATE] Failed to spawn update script: {e}')
        return jsonify({
            'ok': False,
            'error': f'启动更新进程失败: {e}',
        }), 500


@bp.route('/api/update/status', methods=['GET'])
@handle_error
def update_status():
    """Return the current update progress written by the standalone script.

    The standalone update_code.py writes phases to update_status.json:
      start → clean_locks → discard → clean → fetch → reset → clean_pycache → restart → done/error
    """
    if not os.path.exists(UPDATE_STATUS_FILE):
        return jsonify({
            'phase': 'idle',
            'status': 'idle',
            'message': '',
        })

    try:
        with open(UPDATE_STATUS_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return jsonify(data)
    except Exception:
        return jsonify({
            'phase': 'unknown',
            'status': 'unknown',
            'message': '无法读取更新状态',
        })


@bp.route('/api/update/diagnose', methods=['GET'])
@handle_error
def update_diagnose():
    """Run diagnostic checks on the update environment and return a report."""
    import shutil as _shutil

    diag = {}

    # Basic process info
    diag['pid'] = os.getpid()
    diag['uid'] = os.getuid() if hasattr(os, 'getuid') else 'N/A'
    diag['gid'] = os.getgid() if hasattr(os, 'getgid') else 'N/A'
    diag['cwd'] = os.getcwd()
    diag['user_home'] = os.path.expanduser('~')
    diag['tempdir'] = tempfile.gettempdir()
    diag['APP_VERSION'] = _get_current_version()

    # SERVER_DIR checks
    diag['SERVER_DIR'] = SERVER_DIR
    diag['SERVER_DIR_exists'] = os.path.isdir(SERVER_DIR)
    if os.path.isdir(SERVER_DIR):
        stat = os.stat(SERVER_DIR)
        diag['SERVER_DIR_stat'] = {
            'mode': oct(stat.st_mode),
            'writable': os.access(SERVER_DIR, os.W_OK),
            'readable': os.access(SERVER_DIR, os.R_OK),
        }
        # Write test
        try:
            test_file = os.path.join(SERVER_DIR, '.write_test_tmp')
            with open(test_file, 'w') as f:
                f.write('test')
            os.remove(test_file)
            diag['SERVER_DIR_write'] = True
        except Exception as e:
            diag['SERVER_DIR_write'] = False
            diag['SERVER_DIR_write_error'] = str(e)

    # /tmp write test
    try:
        tmp = tempfile.mktemp(dir=diag['tempdir'])
        with open(tmp, 'w') as f:
            f.write('test')
        os.remove(tmp)
        diag['tmp_write'] = True
    except Exception as e:
        diag['tmp_write'] = False
        diag['tmp_write_error'] = str(e)

    # Disk space
    try:
        if hasattr(_shutil, 'disk_usage'):
            for path in [SERVER_DIR, diag['tempdir']]:
                try:
                    usage = _shutil.disk_usage(path)
                    label = path.replace('/', '_').rstrip('_') or 'root'
                    diag[f'disk_{label}_free_mb'] = round(usage.free / (1024 * 1024))
                except Exception:
                    pass
    except Exception:
        pass

    # Git checks
    git_dir = os.path.join(SERVER_DIR, '.git')
    diag['git_dir_exists'] = os.path.isdir(git_dir)
    if os.path.isdir(git_dir):
        try:
            r = git_cmd('remote get-url origin', cwd=SERVER_DIR)
            diag['git_remote'] = r['stdout'].strip() if r['ok'] else 'error'
        except Exception as e:
            diag['git_error'] = str(e)
    else:
        diag['git_error'] = 'No .git directory'

    # Network - GitHub API
    try:
        data = _fetch_github_json(IDE_COMMITS_URL, timeout=10)
        diag['github_api'] = 'ok'
        diag['github_latest_sha'] = data.get('sha', '')[:8]
        diag['github_latest_msg'] = data.get('commit', {}).get('message', '').split('\n')[0]
    except Exception as e:
        diag['github_api'] = f'fail: {e}'

    # Network - ZIP download check (HEAD only)
    try:
        zip_url = 'https://github.com/ctz168/ide/archive/refs/heads/main.tar.gz'
        req = urllib.request.Request(zip_url, method='HEAD', headers={'User-Agent': 'PhoneIDE-Server'})
        with urllib.request.urlopen(req, timeout=10) as resp:
            diag['github_zip'] = 'ok'
            size = int(resp.headers.get('Content-Length', 0))
            diag['github_zip_size'] = f'{round(size / 1024)}KB'
    except Exception as e:
        diag['github_zip'] = f'fail: {e}'

    # Config
    try:
        cfg = load_config()
        if cfg.get('workspace'):
            diag['config_workspace'] = cfg['workspace']
        diag['config_has_token'] = bool(cfg.get('github_token'))
    except Exception:
        pass

    # Server log tail
    log_file_path = os.path.join(CONFIG_DIR, 'server.log')
    update_log_path = os.path.join(CONFIG_DIR, 'update.log')
    log_tail = []
    for lp in [update_log_path, log_file_path]:
        if os.path.exists(lp):
            try:
                with open(lp, 'r', encoding='utf-8', errors='ignore') as f:
                    lines = f.readlines()
                log_tail.extend([l.rstrip() for l in lines[-20:]])
            except Exception:
                pass
    diag['server_log_tail'] = log_tail[-20:]

    # Update script exists?
    diag['update_script_exists'] = os.path.exists(os.path.join(SERVER_DIR, 'scripts', 'update_code.py'))

    return jsonify(diag)
