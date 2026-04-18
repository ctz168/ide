"""
PhoneIDE - Browser preview and inspection API.
Provides command queue for AI tools to control the preview iframe.
"""

import json
import uuid
import time
import threading

from flask import Blueprint, jsonify, request
from utils import handle_error

bp = Blueprint('browser', __name__)

# ── In-memory command queue ──
# AI tool creates command → frontend polls & executes → frontend posts result → tool returns
_commands = {}  # cmd_id -> {action, params, status, result, event, created}
_lock = threading.Lock()
COMMAND_TIMEOUT = 20  # seconds


def _cleanup_old_commands():
    """Remove commands older than 60 seconds."""
    now = time.time()
    expired = [cid for cid, cmd in _commands.items() if now - cmd.get('created', 0) > 60]
    for cid in expired:
        cmd = _commands.pop(cid, None)
        if cmd and cmd.get('event'):
            cmd['event'].set()


def create_browser_command(action, params):
    """Create a pending browser command. Returns cmd_id."""
    _cleanup_old_commands()
    cmd_id = uuid.uuid4().hex[:8]
    with _lock:
        _commands[cmd_id] = {
            'action': action,
            'params': params,
            'status': 'pending',  # pending -> claimed -> done
            'result': None,
            'error': None,
            'event': threading.Event(),
            'created': time.time(),
        }
    return cmd_id


def wait_browser_result(cmd_id, timeout=COMMAND_TIMEOUT):
    """Wait for the frontend to execute a command and return the result."""
    with _lock:
        cmd = _commands.get(cmd_id)
    if not cmd:
        return {'error': 'Command not found (may have expired)'}
    ok = cmd['event'].wait(timeout=timeout)
    with _lock:
        result = cmd.get('result')
        error = cmd.get('error')
    if not ok:
        return {'error': f'Browser command timed out after {timeout}s. Is the preview tab active with a loaded page?'}
    if error:
        return {'error': error}
    return result


def set_browser_result(cmd_id, result):
    """Frontend posts command execution result."""
    with _lock:
        cmd = _commands.get(cmd_id)
        if not cmd:
            return False
        cmd['result'] = result
        cmd['status'] = 'done'
        cmd['event'].set()
    return True


def set_browser_error(cmd_id, error):
    """Frontend posts command execution error."""
    with _lock:
        cmd = _commands.get(cmd_id)
        if not cmd:
            return False
        cmd['error'] = error
        cmd['status'] = 'done'
        cmd['event'].set()
    return True


# ── API Routes ──

@bp.route('/api/browser/poll')
@handle_error
def poll_command():
    """Frontend polls for a pending browser command to execute.
    Returns the next pending command and marks it as 'claimed'."""
    with _lock:
        # Find oldest pending command
        pending = [
            (cid, cmd) for cid, cmd in _commands.items()
            if cmd['status'] == 'pending'
        ]
        if pending:
            pending.sort(key=lambda x: x[1]['created'])
            cid, cmd = pending[0]
            cmd['status'] = 'claimed'
            return jsonify({
                'cmd_id': cid,
                'action': cmd['action'],
                'params': cmd['params'],
            })
    return jsonify({'cmd_id': None})


@bp.route('/api/browser/result', methods=['POST'])
@handle_error
def post_result():
    """Frontend posts the result of executing a browser command."""
    data = request.json or {}
    cmd_id = data.get('cmd_id', '')
    result = data.get('result')
    error = data.get('error')

    if not cmd_id:
        return jsonify({'error': 'cmd_id required'}), 400

    if error:
        set_browser_error(cmd_id, error)
    else:
        set_browser_result(cmd_id, result)

    return jsonify({'ok': True})


@bp.route('/api/browser/status')
@handle_error
def browser_status():
    """Return current browser command queue status."""
    with _lock:
        pending = sum(1 for c in _commands.values() if c['status'] == 'pending')
        claimed = sum(1 for c in _commands.values() if c['status'] == 'claimed')
    return jsonify({
        'pending': pending,
        'claimed': claimed,
        'total': pending + claimed,
    })
