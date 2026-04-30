#!/usr/bin/env python3
"""
PhoneIDE - Standalone update script.

This script runs as a completely independent process (detached from the server)
to pull the latest code from GitHub and restart the server.

Usage:
    python3 scripts/update_code.py /path/to/server_dir [port]

It writes status updates to <CONFIG_DIR>/update_status.json so the frontend
can poll for progress.  The server process is NOT a parent of this script,
so it survives even if the server crashes during the update.
"""

import os
import sys
import json
import subprocess
import shutil
import signal
import time

# ── Parse arguments ──
if len(sys.argv) < 2:
    print('ERROR: SERVER_DIR argument required')
    sys.exit(1)

SERVER_DIR = os.path.abspath(sys.argv[1])
PORT = sys.argv[2] if len(sys.argv) > 2 else '1239'

# Config directory (same as utils.py logic)
CONFIG_DIR = os.environ.get('PHONEIDE_CONFIG_DIR',
    os.path.join(os.path.expanduser('~'), '.phoneide'))
os.makedirs(CONFIG_DIR, exist_ok=True)

STATUS_FILE = os.path.join(CONFIG_DIR, 'update_status.json')
LOG_FILE = os.path.join(CONFIG_DIR, 'update.log')


def write_status(phase, status, message='', detail=''):
    """Write update status to JSON file for frontend polling."""
    data = {
        'phase': phase,       # 'fetch', 'reset', 'clean', 'restart', 'done', 'error'
        'status': status,     # 'running', 'ok', 'fail'
        'message': message,
        'detail': detail,
        'time': time.strftime('%Y-%m-%d %H:%M:%S'),
    }
    try:
        with open(STATUS_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass
    # Also log to file
    log_line = f"[{data['time']}] [{phase}] {status}: {message}"
    if detail:
        log_line += f"\n  {detail}"
    print(log_line, flush=True)
    try:
        with open(LOG_FILE, 'a', encoding='utf-8') as f:
            f.write(log_line + '\n')
    except Exception:
        pass


def clean_git_locks(git_dir):
    """Remove stale git lock files that could prevent operations."""
    lock_patterns = [
        os.path.join(git_dir, 'index.lock'),
    ]
    # Also clean ref locks
    refs_dir = os.path.join(git_dir, 'refs')
    if os.path.isdir(refs_dir):
        for root, dirs, files in os.walk(refs_dir):
            for fname in files:
                if fname.endswith('.lock'):
                    lock_patterns.append(os.path.join(root, fname))

    removed = []
    for lock in lock_patterns:
        if os.path.exists(lock):
            try:
                os.remove(lock)
                removed.append(lock)
            except Exception as e:
                write_status('clean_locks', 'warn', f'Could not remove lock: {lock}', str(e))
    return removed


def run_git(args, timeout=180, cwd=None):
    """Run a git command and return (success, stdout, stderr)."""
    cmd = ['git'] + args
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd or SERVER_DIR)
        return (r.returncode == 0, r.stdout.strip(), r.stderr.strip())
    except subprocess.TimeoutExpired:
        return (False, '', f'Command timed out after {timeout}s: {" ".join(cmd)}')
    except FileNotFoundError:
        return (False, '', 'git command not found')
    except Exception as e:
        return (False, '', str(e))


def main():
    write_status('start', 'running', 'Update process started', f'SERVER_DIR={SERVER_DIR}')

    # ── Step 1: Wait for server to finish responding ──
    write_status('wait', 'running', 'Waiting for server to finish...')
    time.sleep(2)

    # ── Step 2: Validate environment ──
    if not os.path.isdir(SERVER_DIR):
        write_status('error', 'fail', f'SERVER_DIR not found: {SERVER_DIR}')
        sys.exit(1)

    git_dir = os.path.join(SERVER_DIR, '.git')
    has_git = os.path.isdir(git_dir)

    if not has_git:
        write_status('error', 'fail', 'No .git directory found, cannot update via git')
        sys.exit(1)

    os.chdir(SERVER_DIR)
    write_status('chdir', 'ok', f'Changed to {SERVER_DIR}')

    # ── Step 3: Clean git locks ──
    write_status('clean_locks', 'running', 'Cleaning stale git lock files...')
    locks = clean_git_locks(git_dir)
    if locks:
        write_status('clean_locks', 'ok', f'Removed {len(locks)} stale lock(s)',
                     ', '.join(locks))
    else:
        write_status('clean_locks', 'ok', 'No stale locks found')

    # ── Step 4: Discard any local changes (safe for a self-updating IDE) ──
    write_status('discard', 'running', 'Discarding local changes...')
    ok, out, err = run_git(['reset', '--hard', 'HEAD'], timeout=30)
    if not ok:
        write_status('discard', 'warn', 'git reset HEAD failed (non-fatal)', err)
    else:
        write_status('discard', 'ok', 'Local changes discarded')

    # Also clean untracked files that could conflict
    ok, out, err = run_git(['clean', '-fd'], timeout=30)
    if not ok:
        write_status('clean', 'warn', 'git clean failed (non-fatal)', err)
    else:
        write_status('clean', 'ok', 'Untracked files cleaned')

    # ── Step 5: Fetch from origin ──
    write_status('fetch', 'running', 'Fetching from origin...')
    ok, out, err = run_git(['fetch', 'origin', 'main'], timeout=180)
    if not ok:
        write_status('fetch', 'fail', 'git fetch failed', err)
        sys.exit(2)
    write_status('fetch', 'ok', 'Fetch successful')

    # ── Step 6: Reset to origin/main ──
    write_status('reset', 'running', 'Resetting to origin/main...')
    ok, out, err = run_git(['reset', '--hard', 'origin/main'], timeout=60)
    if not ok:
        write_status('reset', 'fail', 'git reset --hard failed', err)
        sys.exit(3)
    write_status('reset', 'ok', 'Reset to origin/main successful')

    # ── Step 7: Clean __pycache__ ──
    write_status('clean_pycache', 'running', 'Cleaning __pycache__...')
    cleaned = 0
    for root, dirs, _files in os.walk(SERVER_DIR):
        if '__pycache__' in dirs:
            try:
                shutil.rmtree(os.path.join(root, '__pycache__'))
                cleaned += 1
            except Exception:
                pass
    write_status('clean_pycache', 'ok', f'Cleaned {cleaned} __pycache__ dir(s)')

    # ── Step 8: Restart server ──
    write_status('restart', 'running', 'Restarting server...')
    server_script = os.path.join(SERVER_DIR, 'phoneide_server.py')

    if not os.path.exists(server_script):
        # Fallback: try run_server.py
        server_script = os.path.join(SERVER_DIR, 'run_server.py')

    if not os.path.exists(server_script):
        write_status('restart', 'fail', 'Server script not found')
        sys.exit(4)

    try:
        # Kill existing server on the same port
        try:
            ok, out, err = run_git([], timeout=5)  # dummy, just to check git works
        except Exception:
            pass

        # Find and kill the old server process
        import re as _re
        try:
            # Use lsof or ss to find process on our port
            for cmd in [
                ['fuser', f'{PORT}/tcp', '-k', '-9'],
                ['lsof', '-t', '-i', f':{PORT}', '-s', 'TCP:LISTEN'],
            ]:
                try:
                    r = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
                    if r.returncode == 0 and r.stdout.strip():
                        pids = [p.strip() for p in r.stdout.strip().split('\n') if p.strip().isdigit()]
                        for pid in pids:
                            if int(pid) != os.getpid():
                                try:
                                    os.kill(int(pid), signal.SIGKILL)
                                except Exception:
                                    pass
                except Exception:
                    pass
        except Exception:
            pass

        # Start new server in a new session (fully detached)
        env = os.environ.copy()
        env['PHONEIDE_PORT'] = PORT

        subprocess.Popen(
            [sys.executable, server_script],
            env=env,
            stdout=open(os.path.join(CONFIG_DIR, 'server.log'), 'a', encoding='utf-8'),
            stderr=subprocess.STDOUT,
            start_new_session=True,
            cwd=SERVER_DIR,
        )

        write_status('done', 'ok', 'Update complete, server restarted')
        sys.exit(0)

    except Exception as e:
        write_status('restart', 'fail', 'Failed to restart server', str(e))
        sys.exit(5)


if __name__ == '__main__':
    main()
