"""
PhoneIDE - Execution / Run API routes.
"""

import os
import json
import time
from flask import Blueprint, jsonify, request, Response
from utils import (
    handle_error, load_config, WORKSPACE, shlex_quote,
    run_process, stop_process, running_processes, process_outputs,
)

bp = Blueprint('run', __name__)


@bp.route('/api/run/execute', methods=['POST'])
@handle_error
def execute_code():
    data = request.json
    code = data.get('code', '')
    file_path = data.get('file_path', '')
    compiler = data.get('compiler', 'python3')
    args = data.get('args', '')
    config = load_config()
    base = config.get('workspace', WORKSPACE)

    if file_path:
        target = os.path.realpath(os.path.join(base, file_path))
        if not target.startswith(os.path.realpath(base)):
            return jsonify({'error': 'Access denied'}), 403
        cmd = f'{compiler} {shlex_quote(target)} {args}'
    else:
        # Write temp file
        tmp_file = os.path.join(base, '.phoneide_tmp.py')
        with open(tmp_file, 'w') as f:
            f.write(code)
        cmd = f'{compiler} {shlex_quote(tmp_file)} {args}'

    proc_id = run_process(cmd, cwd=base)
    return jsonify({'ok': True, 'proc_id': proc_id})


@bp.route('/api/run/stop', methods=['POST'])
@handle_error
def stop_execution():
    data = request.json
    proc_id = data.get('proc_id', '')
    if proc_id and proc_id in running_processes:
        stopped = stop_process(proc_id)
        return jsonify({'ok': stopped})
    return jsonify({'ok': False})


@bp.route('/api/run/processes', methods=['GET'])
@handle_error
def list_processes():
    """List all running and recent processes"""
    processes = []
    for pid, info in running_processes.items():
        start = info.get('start_time')
        running = info.get('running', False)
        uptime = ''
        if start and running:
            elapsed = time.time() - start
            mins, secs = divmod(int(elapsed), 60)
            hours, mins = divmod(mins, 60)
            if hours > 0:
                uptime = f'{hours}h {mins}m {secs}s'
            elif mins > 0:
                uptime = f'{mins}m {secs}s'
            else:
                uptime = f'{secs}s'
        processes.append({
            'id': pid,
            'running': running,
            'cwd': info.get('cwd', ''),
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
        is_running = running_processes.get(proc_id, {}).get('running', False)
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

                is_running = running_processes.get(proc_id, {}).get('running', False)
                if not is_running:
                    yield f"event: exit\ndata: {json.dumps({'exit_code': 0})}\n\n"
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
