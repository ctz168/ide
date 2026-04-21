"""
PhoneIDE - IDE Update API routes.

Checks for two types of updates:
1. APK update — from ctz168/phoneide GitHub Releases
2. Code update — from ctz168/ide GitHub repository
"""

import os
import re
import sys
import json
import subprocess
import threading
import time
import tempfile
import shutil
import urllib.request
import urllib.error
import tarfile
from datetime import datetime
from flask import Blueprint, jsonify, request
from utils import handle_error, load_config, save_chat_history, WORKSPACE, SERVER_DIR, PORT, HOST, CONFIG_DIR, log_write
from routes.git import git_cmd

bp = Blueprint('update', __name__)

# GitHub repos
IDE_REPO = 'ctz168/ide'          # Code (server, routes, static)
APK_REPO = 'ctz168/phoneide'     # APK releases

# GitHub API URLs
IDE_COMMITS_URL = f'https://api.github.com/repos/{IDE_REPO}/commits/main'
APK_RELEASES_URL = f'https://api.github.com/repos/{APK_REPO}/releases/latest'


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
            with open(vtxt, 'r') as f:
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
            with open(ctxt, 'r') as f:
                sha = f.read().strip()[:40]
                if sha and len(sha) >= 7:
                    return sha
        except Exception:
            pass
    return ''


@bp.route('/api/update/check', methods=['POST'])
@handle_error
def update_check():
    """Check for code updates only from ctz168/ide repository.
    
    Note: APK updates are intentionally disabled to avoid requiring
    app termination for installation (self-killing paradox).
    """
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
            'new_version': '',  # Not applicable for code updates
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
    """Download latest code from ctz168/ide and update server files.

    Does NOT require a git repository. Downloads the repo tarball from GitHub,
    extracts server files (server.py, utils.py, routes/, static/) into SERVER_DIR.
    Caller (Android) handles server restart after this returns.
    """
    try:
        log_write('[UPDATE] Starting code update from GitHub...')

        # 1. Try git pull if .git exists and remote points to ctz168/ide (fast, incremental)
        if os.path.exists(os.path.join(SERVER_DIR, '.git')):
            try:
                # Check if remote already points to ctz168/ide
                remote_check = git_cmd('remote get-url origin', cwd=SERVER_DIR)
                if remote_check['ok']:
                    remote_url = remote_check['stdout'].strip()
                    if IDE_REPO not in remote_url:
                        # Update remote to point to ctz168/ide
                        git_cmd(f'remote set-url origin https://github.com/{IDE_REPO}.git', cwd=SERVER_DIR)
                        log_write(f'[UPDATE] Updated git remote to {IDE_REPO}')

                fetch_result = git_cmd('fetch origin main', cwd=SERVER_DIR, timeout=120)
                if not fetch_result['ok']:
                    log_write(f'[UPDATE] Git fetch failed: {fetch_result.get("stderr", "unknown error")}')
                else:
                    # After fetch, compare local HEAD with origin/main to confirm
                    # there's actually a newer commit to pull
                    rev_parse = git_cmd('rev-parse origin/main', cwd=SERVER_DIR)
                    local_rev = git_cmd('rev-parse HEAD', cwd=SERVER_DIR)
                    if (rev_parse['ok'] and local_rev['ok']
                            and rev_parse['stdout'].strip() != local_rev['stdout'].strip()):
                        # origin/main is newer — reset to it
                        reset_result = git_cmd('reset --hard origin/main', cwd=SERVER_DIR)
                        if reset_result['ok']:
                            log_write(f'[UPDATE] Git pull succeeded: {local_rev["stdout"].strip()[:8]} → {rev_parse["stdout"].strip()[:8]}')
                            return jsonify({
                                'ok': True,
                                'method': 'git',
                                'message': '代码已通过 Git 更新，服务器将重启。',
                            })
                    elif rev_parse['ok'] and local_rev['ok'] and rev_parse['stdout'].strip() == local_rev['stdout'].strip():
                        log_write('[UPDATE] Git fetch succeeded but already up-to-date, falling back to download')
                    else:
                        log_write(f'[UPDATE] Git rev-parse failed after fetch: {rev_parse.get("stderr", "")} / {local_rev.get("stderr", "")}')
            except Exception as e:
                log_write(f'[UPDATE] Git pull failed, falling back to download: {e}')

        # 2. Download tarball from ctz168/ide
        tarball_url = f'https://github.com/{IDE_REPO}/archive/refs/heads/main.tar.gz'
        log_write(f'[UPDATE] Downloading from {tarball_url}')

        req = urllib.request.Request(tarball_url, headers={
            'User-Agent': 'PhoneIDE-Server',
        })
        with urllib.request.urlopen(req, timeout=120) as resp:
            tarball_data = resp.read()

        log_write(f'[UPDATE] Downloaded {len(tarball_data) // 1024}KB, extracting...')

        # 3. Extract to temp directory
        tmpdir = tempfile.mkdtemp(prefix='phoneide_update_')
        try:
            tarball_path = os.path.join(tmpdir, 'main.tar.gz')
            with open(tarball_path, 'wb') as f:
                f.write(tarball_data)

            with tarfile.open(tarball_path, 'r:gz') as tar:
                tar.extractall(tmpdir)

            # Find the extracted repo directory (GitHub archives as <repo>-main/)
            extracted_dir = None
            for entry in os.listdir(tmpdir):
                full = os.path.join(tmpdir, entry)
                if os.path.isdir(full) and entry.endswith('-main'):
                    extracted_dir = full
                    break

            if not extracted_dir:
                return jsonify({'error': '无法解析下载的压缩包'}), 500

            # 4. Copy server files to SERVER_DIR
            files_to_copy = ['server.py', 'utils.py', 'requirements.txt']
            dirs_to_copy = ['routes', 'static']

            for fname in files_to_copy:
                src = os.path.join(extracted_dir, fname)
                dst = os.path.join(SERVER_DIR, fname)
                if os.path.exists(src):
                    shutil.copy2(src, dst)
                    log_write(f'[UPDATE] Copied {fname}')

            for dirname in dirs_to_copy:
                src = os.path.join(extracted_dir, dirname)
                dst = os.path.join(SERVER_DIR, dirname)
                if os.path.exists(src):
                    if os.path.exists(dst):
                        shutil.rmtree(dst)
                    shutil.copytree(src, dst)
                    log_write(f'[UPDATE] Copied {dirname}/')

            # 5. Clean up __pycache__ to prevent stale bytecode
            for root, dirs, files in os.walk(SERVER_DIR):
                if '__pycache__' in dirs:
                    shutil.rmtree(os.path.join(root, '__pycache__'))
                    log_write(f'[UPDATE] Cleaned __pycache__ in {root}')

            log_write('[UPDATE] Code update completed successfully')

            return jsonify({
                'ok': True,
                'method': 'download',
                'message': '代码已更新，服务器将重启。',
            })

        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    except Exception as e:
        log_write(f'[UPDATE] Failed: {e}')
        return jsonify({'error': str(e)}), 500
