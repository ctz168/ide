"""
PhoneIDE - Runtime debugger using sys.settrace().
Provides breakpoint, step-through, variable inspection, and expression evaluation.
Only works with Python files. Zero external dependencies.
"""

import os
import sys
import io
import json
import time
import uuid
import threading
import traceback as _traceback
from flask import Blueprint, jsonify, request, Response
from utils import handle_error, WORKSPACE, load_config

bp = Blueprint('debug', __name__)

# ── Global Debug Session (singleton – one at a time) ──
_session = None
_session_lock = threading.Lock()


# ═══════════════════════════════════════════════════════════════
#  Output Capture – redirects stdout/stderr during debugging
# ═══════════════════════════════════════════════════════════════

class _OutputCapture(io.StringIO):
    """Thread-safe capture of stdout / stderr emitted by the debugged program."""
    def __init__(self, session, stream_type):
        super().__init__()
        self._session = session
        self._stream_type = stream_type

    def write(self, s):
        if s:
            self._session.output_lines.append({
                'type': self._stream_type,
                'text': s,
                'time': time.time(),
            })
        return super().write(s)

    def flush(self):
        pass


# ═══════════════════════════════════════════════════════════════
#  DebugSession – the core debugger
# ═══════════════════════════════════════════════════════════════

class DebugSession:
    """Lightweight Python debugger built on sys.settrace().

    Lifecycle
    ---------
    1. Create → idle
    2. start() → running  (new daemon thread)
    3. breakpoint hit or step → paused  (thread blocks on Event)
    4. continue / step → running
    5. program ends → stopped / error
    """

    def __init__(self, file_path, breakpoints=None):
        self.id = uuid.uuid4().hex[:8]
        self.file_path = os.path.abspath(file_path)
        self.breakpoints = set()          # {(abs_path, lineno), ...}
        self.state = 'idle'               # idle | running | paused | stopped | error
        self.current_file = ''
        self.current_line = 0
        self.variables = {}
        self.call_stack = []
        self.step_mode = None             # None | step | step_over | step_out
        self.step_depth = 0
        self.pause_event = threading.Event()
        self._current_frame = None
        self.output_lines = []
        self.error = None
        self.thread = None
        self.created = time.time()
        self.activity_log = []           # [{action, detail, time}, ...]

        # Set initial breakpoints
        if breakpoints:
            for bp in breakpoints:
                f = os.path.abspath(bp.get('file', self.file_path))
                l = bp.get('line', 0)
                if l > 0:
                    self.breakpoints.add((f, l))

        # Determine the workspace/project root for trace filtering
        try:
            config = load_config()
            ws = config.get('workspace', WORKSPACE)
            project = config.get('project', None)
            if project:
                root = os.path.abspath(os.path.join(ws, project))
            else:
                root = os.path.abspath(ws)
            self._trace_root = root
        except Exception:
            self._trace_root = os.path.abspath(WORKSPACE)

    # ── start / stop ────────────────────────────────────────────

    def start(self):
        self.state = 'running'
        self._log('start', f'开始调试: {os.path.basename(self.file_path)}')
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self):
        old_trace = sys.gettrace()
        old_stdout = sys.stdout
        old_stderr = sys.stderr
        try:
            sys.settrace(self._trace_func)
            sys.stdout = _OutputCapture(self, 'stdout')
            sys.stderr = _OutputCapture(self, 'stderr')

            with open(self.file_path, 'r', encoding='utf-8', errors='replace') as f:
                source = f.read()
            code = compile(source, self.file_path, 'exec')

            ns = {
                '__name__': '__main__',
                '__file__': self.file_path,
                '__builtins__': __builtins__,
            }
            exec(code, ns)

            if self.state == 'running':
                self.state = 'stopped'
                self._log('info', '程序正常结束')
        except SyntaxError as e:
            self.state = 'error'
            self.error = f'语法错误: {e}\n文件: {e.filename}, 行: {e.lineno}'
            self._log('error', self.error)
        except Exception as e:
            self.state = 'error'
            self.error = f'{type(e).__name__}: {e}\n{_traceback.format_exc()}'
            self._log('error', f'运行时错误: {e}')
        finally:
            sys.settrace(old_trace)
            sys.stdout = old_stdout
            sys.stderr = old_stderr
            if self.state == 'paused':
                self.state = 'stopped'

    # ── trace filtering ──────────────────────────────────────────

    def _should_trace(self, filename):
        if not filename or filename.startswith('<'):
            return False
        try:
            ap = os.path.abspath(filename)
            return ap.startswith(self._trace_root + os.sep) or ap == self._trace_root
        except Exception:
            return False

    # ── sys.settrace callback ─────────────────────────────────────

    def _trace_func(self, frame, event, arg):
        if self.state == 'stopped':
            return None

        filename = frame.f_code.co_filename
        lineno = frame.f_lineno

        if event == 'call':
            self.call_stack.append({
                'file': filename, 'line': lineno,
                'func': frame.f_code.co_name,
            })

        elif event == 'line':
            if not self._should_trace(filename):
                return self._trace_func

            abs_path = os.path.abspath(filename)

            if (abs_path, lineno) in self.breakpoints:
                self._pause_at(frame, abs_path, lineno)
                return self._trace_func

            if self.step_mode == 'step':
                self._pause_at(frame, abs_path, lineno)
                return self._trace_func

            elif self.step_mode == 'step_over':
                if len(self.call_stack) <= self.step_depth:
                    self._pause_at(frame, abs_path, lineno)
                    self.step_mode = None
                    return self._trace_func

        elif event == 'return':
            # pop matching entry
            if self.call_stack:
                top = self.call_stack[-1]
                if top['func'] == frame.f_code.co_name and top.get('file') == filename:
                    self.call_stack.pop()
                else:
                    # fallback: pop by index
                    self.call_stack.pop()

            if self.step_mode == 'step_out':
                if len(self.call_stack) < self.step_depth:
                    abs_path = os.path.abspath(filename)
                    self._pause_at(frame, abs_path, lineno)
                    self.step_mode = None
                    return self._trace_func

        return self._trace_func

    # ── pause / continue / step ──────────────────────────────────

    def _pause_at(self, frame, filepath, lineno):
        self.state = 'paused'
        self.current_file = filepath
        self.current_line = lineno
        self._current_frame = frame

        # Capture variables (truncate large values)
        self.variables = {}
        for name, value in frame.f_locals.items():
            try:
                val = repr(value)
                if len(val) > 500:
                    val = val[:500] + '...'
                self.variables[name] = val
            except Exception:
                self.variables[name] = '<无法序列化>'

        self._log('breakpoint', f'断点命中: {os.path.basename(filepath)}:{lineno}')

        # Block until continue / step / stop
        self.pause_event.clear()
        self.pause_event.wait(timeout=300)  # 5 min max

        if self.pause_event.is_set():
            self.state = 'running'

    def continue_run(self):
        if self.state == 'paused':
            self.step_mode = None
            self.pause_event.set()
            self._log('continue', '继续运行')
            return True
        return False

    def step(self, mode='step'):
        if self.state == 'paused':
            self.step_mode = mode
            if mode == 'step_over':
                self.step_depth = len(self.call_stack)
            elif mode == 'step_out':
                self.step_depth = max(0, len(self.call_stack) - 1)
            self.pause_event.set()
            labels = {'step': '单步执行', 'step_over': '步过', 'step_out': '步出'}
            self._log('step', labels.get(mode, mode))
            return True
        return False

    def stop(self):
        self.state = 'stopped'
        self.pause_event.set()
        self._log('stop', '调试已停止')

    # ── evaluate ─────────────────────────────────────────────────

    def evaluate_expression(self, expression):
        if self.state != 'paused' or not self._current_frame:
            return {'error': '未在断点处暂停'}
        try:
            result = eval(expression, self._current_frame.f_globals, self._current_frame.f_locals)
            return {'ok': True, 'result': repr(result)}
        except SyntaxError:
            try:
                exec(expression, self._current_frame.f_globals, self._current_frame.f_locals)
                return {'ok': True, 'result': '(已执行)'}
            except Exception as e:
                return {'error': str(e)}
        except Exception as e:
            return {'error': str(e)}

    # ── breakpoints ─────────────────────────────────────────────────

    def set_breakpoints(self, add=None, remove=None):
        changed = False
        if add:
            for bp in add:
                f = os.path.abspath(bp.get('file', self.file_path))
                l = bp.get('line', 0)
                if l > 0 and (f, l) not in self.breakpoints:
                    self.breakpoints.add((f, l))
                    changed = True
        if remove:
            for bp in remove:
                f = os.path.abspath(bp.get('file', self.file_path))
                l = bp.get('line', 0)
                if (f, l) in self.breakpoints:
                    self.breakpoints.discard((f, l))
                    changed = True
        if changed:
            self._log('breakpoint', f'断点更新: 共 {len(self.breakpoints)} 个')
        return changed

    # ── helpers ────────────────────────────────────────────────────

    def _log(self, action, detail):
        self.activity_log.append({
            'action': action, 'detail': detail, 'time': time.time(),
        })
        if len(self.activity_log) > 200:
            self.activity_log = self.activity_log[-200:]

    def get_state_dict(self):
        bps = sorted(self.breakpoints)
        return {
            'state': self.state,
            'currentFile': self.current_file,
            'currentLine': self.current_line,
            'currentFileShort': os.path.basename(self.current_file) if self.current_file else '',
            'breakpoints': [{'file': f, 'line': l, 'fileShort': os.path.basename(f)} for f, l in bps],
            'variables': [{'name': n, 'value': v} for n, v in self.variables.items()],
            'callStack': [
                {
                    'index': i,
                    'file': s['file'], 'fileShort': os.path.basename(s['file']),
                    'line': s['line'], 'func': s['func'],
                }
                for i, s in enumerate(reversed(self.call_stack))
            ],
            'activity': [
                {'action': a['action'], 'detail': a['detail'], 'time': a['time']}
                for a in self.activity_log[-60:]
            ],
            'error': self.error,
            'outputCount': len(self.output_lines),
        }

    def get_output_since(self, since_idx=0):
        return self.output_lines[since_idx:]


# ═══════════════════════════════════════════════════════════════
#  Session management helpers
# ═══════════════════════════════════════════════════════════════

def get_session():
    with _session_lock:
        return _session


def create_session(file_path, breakpoints=None):
    global _session
    with _session_lock:
        if _session and _session.state not in ('stopped', 'error', 'idle'):
            _session.stop()
        _session = DebugSession(file_path, breakpoints)
        _session.start()
        return _session


# ═══════════════════════════════════════════════════════════════
#  API Routes
# ═════════════════════════════════════════════════════════════════

@bp.route('/api/debug/start', methods=['POST'])
@handle_error
def api_debug_start():
    data = request.json or {}
    file_path = data.get('file_path', '')
    breakpoints = data.get('breakpoints', [])
    if not file_path:
        return jsonify({'error': 'file_path is required'}), 400
    if not os.path.isfile(file_path):
        return jsonify({'error': f'File not found: {file_path}'}), 400
    session = create_session(file_path, breakpoints)
    return jsonify({'ok': True, 'session_id': session.id, 'state': session.state})


@bp.route('/api/debug/stop', methods=['POST'])
@handle_error
def api_debug_stop():
    session = get_session()
    if not session:
        return jsonify({'error': 'No active debug session'})
    session.stop()
    return jsonify({'ok': True, 'state': session.state})


@bp.route('/api/debug/continue', methods=['POST'])
@handle_error
def api_debug_continue():
    session = get_session()
    if not session:
        return jsonify({'error': 'No active debug session'})
    ok = session.continue_run()
    return jsonify({'ok': ok, 'state': session.state})


@bp.route('/api/debug/step', methods=['POST'])
@handle_error
def api_debug_step():
    data = request.json or {}
    mode = data.get('mode', 'step')
    session = get_session()
    if not session:
        return jsonify({'error': 'No active debug session'})
    ok = session.step(mode)
    return jsonify({'ok': ok, 'state': session.state})


@bp.route('/api/debug/breakpoints', methods=['POST'])
@handle_error
def api_debug_breakpoints():
    data = request.json or {}
    add = data.get('add', [])
    remove = data.get('remove', [])
    session = get_session()
    if not session:
        return jsonify({'error': 'No active debug session'})
    session.set_breakpoints(add, remove)
    return jsonify({'ok': True, 'breakpoints': [
        {'file': f, 'line': l} for f, l in session.breakpoints
    ]})


@bp.route('/api/debug/state')
@handle_error
def api_debug_state():
    session = get_session()
    if not session:
        return jsonify({'state': 'no_session'})
    return jsonify(session.get_state_dict())


@bp.route('/api/debug/state/stream')
def api_debug_state_stream():
    """SSE endpoint – pushes debug state ~2× per second."""
    def generate():
        last_output_idx = 0
        while True:
            session = get_session()
            if not session:
                yield f"data: {json.dumps({'state': 'no_session'})}\n\n"
                time.sleep(1)
                continue

            state = session.get_state_dict()
            state['outputLines'] = session.get_output_since(last_output_idx)
            last_output_idx = len(session.output_lines)
            yield f"data: {json.dumps(state)}\n\n"

            if session.state in ('stopped', 'error'):
                yield f"data: {json.dumps({'state': session.state, 'done': True})}\n\n"
                break

            time.sleep(0.5)

    return Response(generate(), mimetype='text/event-stream',
                    headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})


@bp.route('/api/debug/evaluate', methods=['POST'])
@handle_error
def api_debug_evaluate():
    data = request.json or {}
    expression = data.get('expression', '')
    if not expression:
        return jsonify({'error': 'expression is required'}), 400
    session = get_session()
    if not session:
        return jsonify({'error': 'No active debug session'})
    result = session.evaluate_expression(expression)
    session._log('evaluate', f'执行: {expression} → {str(result.get("result", result.get("error", ""))[:100]}')
    return jsonify(result)
