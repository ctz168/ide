"""
PhoneIDE - Shared utilities, constants, and helper functions.
"""

import os
import sys
import json
import subprocess
import threading
import time
import re
import shutil
import traceback
import uuid
import fnmatch
from pathlib import Path
from datetime import datetime
from functools import wraps
from flask import jsonify

# ==================== Constants ====================
SERVER_DIR = os.path.dirname(os.path.abspath(__file__))
WORKSPACE = os.environ.get('PHONEIDE_WORKSPACE', os.path.expanduser('~/phoneide_workspace'))
PORT = int(os.environ.get('PHONEIDE_PORT', 12345))
HOST = os.environ.get('PHONEIDE_HOST', '0.0.0.0')

CONFIG_DIR = os.path.expanduser('~/.phoneide')
CONFIG_FILE = os.path.join(CONFIG_DIR, 'config.json')
LLM_CONFIG_FILE = os.path.join(CONFIG_DIR, 'llm_config.json')
CHAT_HISTORY_FILE = os.path.join(CONFIG_DIR, 'chat_history.json')

# ==================== Log Buffer ====================
import collections

_log_buffer = collections.deque(maxlen=10000)
_log_lock = threading.Lock()


def log_write(line):
    """Write a line to the in-memory ring buffer."""
    with _log_lock:
        _log_buffer.append({'time': datetime.now().isoformat(), 'text': line})


# ==================== Config Management ====================
def load_config():
    try:
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, 'r') as f:
                return json.load(f)
    except Exception:
        pass
    return {
        'workspace': WORKSPACE,
        'venv_path': '',
        'compiler': 'python3',
        'theme': 'claude',
        'font_size': 14,
        'tab_size': 4,
        'show_line_numbers': True,
    }


def save_config(config):
    with open(CONFIG_FILE, 'w') as f:
        json.dump(config, f, indent=2, ensure_ascii=False)


DEFAULT_LLM_MODELS = [
    {
        'name': 'OpenAI',
        'provider': 'openai',
        'api_type': 'openai',
        'api_key': '',
        'api_base': '',
        'model': 'gpt-4o-mini',
        'enabled': True,
        'temperature': 0.7,
        'max_tokens': 4096,
    },
    {
        'name': 'Anthropic',
        'provider': 'anthropic',
        'api_type': 'anthropic',
        'api_key': '',
        'api_base': 'https://api.anthropic.com/v1',
        'model': 'claude-sonnet-4-20250514',
        'enabled': False,
        'temperature': 0.7,
        'max_tokens': 4096,
    },
    {
        'name': 'Ollama',
        'provider': 'ollama',
        'api_type': 'ollama',
        'api_key': '',
        'api_base': 'http://localhost:11434',
        'model': 'llama3',
        'enabled': False,
        'temperature': 0.7,
        'max_tokens': 4096,
    },
    {
        'name': 'ModelScope',
        'provider': 'modelscope',
        'api_type': 'openai',
        'api_key': 'ms-3eca52df-ea14-481b-9e72-73b988b612f7',
        'api_base': 'https://api-inference.modelscope.cn/v1',
        'model': 'stepfun-ai/Step-3.5-Flash',
        'enabled': False,
        'temperature': 0.7,
        'max_tokens': 16384,
    },
]


def load_llm_config():
    """Load LLM config, migrating legacy single-model format to multi-model format."""
    if os.path.exists(LLM_CONFIG_FILE):
        with open(LLM_CONFIG_FILE, 'r') as f:
            config = json.load(f)
        # Migrate legacy single-model config to multi-model format
        if 'models' not in config:
            legacy = {
                'name': config.get('provider', 'openai').capitalize(),
                'provider': config.get('provider', 'openai'),
                'api_type': config.get('api_type', 'openai'),
                'api_key': config.get('api_key', ''),
                'api_base': config.get('api_base', ''),
                'model': config.get('model', 'gpt-4o-mini'),
                'enabled': True,
                'temperature': config.get('temperature', 0.7),
                'max_tokens': config.get('max_tokens', 4096),
            }
            config['models'] = [legacy]
            del config['provider']
            del config['api_key']
            del config['api_base']
            del config['model']
            del config['temperature']
            del config['max_tokens']
            if 'api_type' in config:
                del config['api_type']
            save_llm_config(config)
        return config
    return {
        'models': DEFAULT_LLM_MODELS,
        'system_prompt': 'You are a helpful coding assistant integrated in PhoneIDE.',
    }


def save_llm_config(config):
    with open(LLM_CONFIG_FILE, 'w') as f:
        json.dump(config, f, indent=2, ensure_ascii=False)


def get_active_llm_config(config=None):
    """Get the first enabled model config as a flat dict (compatible with existing code)."""
    if config is None:
        config = load_llm_config()
    models = config.get('models', [])
    system_prompt = config.get('system_prompt', '')
    for m in models:
        if m.get('enabled'):
            flat = dict(m)
            flat['system_prompt'] = flat.get('system_prompt') or system_prompt
            return flat
    # Fallback: return first model or default
    if models:
        flat = dict(models[0])
        flat['system_prompt'] = flat.get('system_prompt') or system_prompt
        return flat
    return {
        'provider': 'openai', 'api_type': 'openai', 'api_key': '',
        'api_base': '', 'model': 'gpt-4o-mini', 'temperature': 0.7,
        'max_tokens': 4096, 'system_prompt': system_prompt,
    }


def load_chat_history():
    if os.path.exists(CHAT_HISTORY_FILE):
        with open(CHAT_HISTORY_FILE, 'r') as f:
            return json.load(f)
    return []


def save_chat_history(history):
    # Keep last 200 messages
    history = history[-200:]
    with open(CHAT_HISTORY_FILE, 'w') as f:
        json.dump(history, f, indent=2, ensure_ascii=False)


# ==================== Process Management ====================
running_processes = {}
process_outputs = {}


def run_process(cmd, cwd=None, timeout=300, proc_id=None):
    """Run a subprocess and capture output"""
    if not proc_id:
        proc_id = str(uuid.uuid4())[:8]

    process_outputs[proc_id] = []
    running_processes[proc_id] = {
        'process': None,
        'cwd': cwd,
        'running': False,
        'start_time': None,
    }

    def execute():
        try:
            env = os.environ.copy()
            config = load_config()
            if config.get('venv_path') and os.path.exists(config['venv_path']):
                venv_bin = os.path.join(config['venv_path'], 'bin')
                if os.path.exists(venv_bin):
                    env['PATH'] = venv_bin + ':' + env.get('PATH', '')
                    env['VIRTUAL_ENV'] = config['venv_path']

            running_processes[proc_id]['running'] = True
            running_processes[proc_id]['start_time'] = time.time()

            proc = subprocess.Popen(
                cmd,
                shell=True,
                cwd=cwd or config.get('workspace', WORKSPACE),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                env=env,
                text=True,
                bufsize=1,
            )
            running_processes[proc_id]['process'] = proc

            for line in iter(proc.stdout.readline, ''):
                if not running_processes[proc_id]['running']:
                    break
                output = line.rstrip('\n')
                process_outputs[proc_id].append({
                    'type': 'stdout',
                    'text': output,
                    'time': datetime.now().isoformat(),
                })

            proc.wait(timeout=5)
            code = proc.returncode

            process_outputs[proc_id].append({
                'type': 'status',
                'text': f'Process exited with code {code}',
                'exit_code': code,
                'time': datetime.now().isoformat(),
            })
        except subprocess.TimeoutExpired:
            process_outputs[proc_id].append({
                'type': 'error',
                'text': 'Process timed out',
                'time': datetime.now().isoformat(),
            })
        except Exception as e:
            process_outputs[proc_id].append({
                'type': 'error',
                'text': str(e),
                'time': datetime.now().isoformat(),
            })
        finally:
            running_processes[proc_id]['running'] = False

    t = threading.Thread(target=execute, daemon=True)
    t.start()
    return proc_id


def stop_process(proc_id):
    if proc_id in running_processes:
        proc = running_processes[proc_id]
        if proc['process'] and proc['running']:
            proc['running'] = False
            try:
                proc['process'].terminate()
                proc['process'].wait(timeout=3)
            except:
                try:
                    proc['process'].kill()
                except:
                    pass
            return True
    return False


# ==================== File Type Detection ====================
def get_file_type(filename):
    ext = os.path.splitext(filename)[1].lower()
    type_map = {
        '.py': 'python', '.js': 'javascript', '.ts': 'typescript',
        '.jsx': 'javascript', '.tsx': 'typescript',
        '.html': 'html', '.htm': 'html', '.css': 'css', '.scss': 'scss',
        '.json': 'json', '.xml': 'xml', '.yaml': 'yaml', '.yml': 'yaml',
        '.md': 'markdown', '.txt': 'text', '.sh': 'shell', '.bash': 'shell',
        '.c': 'c', '.cpp': 'cpp', '.h': 'c', '.hpp': 'cpp',
        '.java': 'java', '.kt': 'kotlin', '.swift': 'swift',
        '.go': 'go', '.rs': 'rust', '.rb': 'ruby', '.php': 'php',
        '.sql': 'sql', '.r': 'r', '.lua': 'lua', '.vim': 'vim',
        '.dockerfile': 'dockerfile', '.toml': 'toml', '.ini': 'ini',
        '.cfg': 'ini', '.conf': 'ini', '.log': 'text',
        '.env': 'shell', '.gitignore': 'text', '.editorconfig': 'ini',
    }
    # Check special filenames
    if filename == 'Dockerfile':
        return 'dockerfile'
    if filename == 'Makefile':
        return 'makefile'
    return type_map.get(ext, 'text')


def get_icon_for_file(filename):
    if os.path.isdir(filename) if isinstance(filename, str) else False:
        return 'folder'
    ext = os.path.splitext(filename)[1].lower()
    icon_map = {
        '.py': '🐍', '.js': '📜', '.ts': '📘', '.html': '🌐',
        '.css': '🎨', '.json': '📋', '.md': '📝', '.txt': '📄',
        '.sh': '⚡', '.yml': '⚙️', '.yaml': '⚙️', '.toml': '⚙️',
        '.gitignore': '🚫', '.env': '🔒',
        '.c': '🔧', '.cpp': '🔧', '.h': '🔧',
        '.java': '☕', '.go': '🐹', '.rs': '🦀', '.rb': '💎',
        '.sql': '🗃️', '.xml': '📰', '.svg': '🖼️',
        '.png': '🖼️', '.jpg': '🖼️', '.jpeg': '🖼️', '.gif': '🖼️',
    }
    if filename == 'Dockerfile':
        return '🐳'
    if filename == 'Makefile':
        return '🔨'
    if filename == 'README.md':
        return '📖'
    if filename.startswith('.'):
        return '⚙️'
    return icon_map.get(ext, '📄')


# ==================== Error Handler ====================
def handle_error(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        try:
            return f(*args, **kwargs)
        except Exception as e:
            traceback.print_exc()
            return jsonify({'error': str(e)}), 500
    return wrapper


# ==================== Helper ====================
def shlex_quote(s):
    """Simple shell quoting"""
    return "'" + s.replace("'", "'\\''") + "'"
