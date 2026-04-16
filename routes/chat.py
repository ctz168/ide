"""
PhoneIDE - LLM Chat + AI Agent routes.
"""

import os
import json
import re
import time
import shutil
import subprocess
import fnmatch
import urllib.request
import urllib.error
import urllib.parse
from datetime import datetime
from flask import Blueprint, jsonify, request, Response
from utils import (
    handle_error, load_config, load_llm_config, save_llm_config,
    get_active_llm_config,
    load_chat_history, save_chat_history,
    load_conversations, save_conversations, get_conversation, save_conversation, delete_conversation,
    WORKSPACE, SERVER_DIR,
    get_file_type, shlex_quote,
)
from routes.git import git_cmd

bp = Blueprint('chat', __name__)

# ==================== System Prompt ====================
DEFAULT_SYSTEM_PROMPT = f"""You are PhoneIDE AI Agent, a powerful coding assistant integrated in a mobile IDE.
You have access to tools that let you read/write files, execute code, search projects, manage git, and more.

## Available Tools
You have 19 tools available. When you need to perform an action, call the appropriate tool using function calling.
For multi-step tasks, think step by step and use tools in sequence.

## Important Rules
1. Always use absolute paths when referencing files
2. Before writing a file, read it first to understand existing content
3. When modifying code, use edit_file for targeted changes instead of rewriting entire files
4. After executing commands, check the output for errors before proceeding
5. For large files, use offset_line and limit_lines to read specific sections
6. When searching, use specific patterns rather than broad terms
7. If a tool fails, analyze the error and try a different approach
8. Always explain what you're doing and why before taking action
9. Respect the workspace boundary - all file operations are scoped to the workspace
10. When running shell commands, be cautious with destructive operations

## Workspace
Current workspace: {WORKSPACE}
Server directory: {SERVER_DIR}
"""

# ==================== Tool Definitions ====================
AGENT_TOOLS = [
    {
        'type': 'function',
        'function': {
            'name': 'read_file',
            'description': (
                'Read the content of a file. Supports automatic encoding detection (UTF-8, GBK, Latin-1, etc.). '
                'Returns file content with line numbers for easy reference. For large files, use offset_line and '
                'limit_lines to read specific sections. Files larger than 10MB will be rejected. '
                'Binary files will return an error.'
            ),
            'parameters': {
                'type': 'object',
                'properties': {
                    'path': {
                        'type': 'string',
                        'description': 'Absolute path to the file to read',
                    },
                    'offset_line': {
                        'type': 'integer',
                        'description': 'Start reading from this line number (1-based). Default: 1',
                        'default': 1,
                    },
                    'limit_lines': {
                        'type': 'integer',
                        'description': 'Maximum number of lines to read. Default: read entire file',
                    },
                },
                'required': ['path'],
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'write_file',
            'description': (
                'Write content to a file, creating it if it does not exist. Parent directories are automatically '
                'created. Overwrites existing files entirely. For targeted modifications, prefer edit_file instead. '
                'Content is written as UTF-8 text.'
            ),
            'parameters': {
                'type': 'object',
                'properties': {
                    'path': {
                        'type': 'string',
                        'description': 'Absolute path to the file to write',
                    },
                    'content': {
                        'type': 'string',
                        'description': 'Full content to write to the file',
                    },
                    'create_dirs': {
                        'type': 'boolean',
                        'description': 'Automatically create parent directories. Default: true',
                        'default': True,
                    },
                },
                'required': ['path', 'content'],
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'edit_file',
            'description': (
                'Search and replace text within a file. Performs exact string matching of old_text and replaces '
                'it with new_text. If old_text appears multiple times, all occurrences will be replaced - be '
                'specific with surrounding context to avoid unintended changes. Returns the number of replacements made.'
            ),
            'parameters': {
                'type': 'object',
                'properties': {
                    'path': {
                        'type': 'string',
                        'description': 'Absolute path to the file to edit',
                    },
                    'old_text': {
                        'type': 'string',
                        'description': 'Exact text to search for (must match precisely including whitespace)',
                    },
                    'new_text': {
                        'type': 'string',
                        'description': 'Replacement text',
                    },
                },
                'required': ['path', 'old_text', 'new_text'],
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'list_directory',
            'description': (
                'List files and directories at a given path. Returns file names, types (file/directory), sizes, '
                'and modification times. Automatically detects file types by extension. Hidden files are excluded '
                'by default unless show_hidden is true.'
            ),
            'parameters': {
                'type': 'object',
                'properties': {
                    'path': {
                        'type': 'string',
                        'description': 'Absolute path to the directory to list. Default: workspace root',
                        'default': WORKSPACE,
                    },
                    'show_hidden': {
                        'type': 'boolean',
                        'description': 'Include hidden files (starting with dot). Default: false',
                        'default': False,
                    },
                },
                'required': [],
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'search_files',
            'description': (
                'Search for text patterns across files in the workspace. Supports both literal text and regex '
                'patterns. Skips common ignore directories (.git, node_modules, __pycache__, etc.). Returns '
                'file paths, line numbers, and matching line content. Use specific patterns for better performance.'
            ),
            'parameters': {
                'type': 'object',
                'properties': {
                    'pattern': {
                        'type': 'string',
                        'description': 'Text or regex pattern to search for',
                    },
                    'path': {
                        'type': 'string',
                        'description': 'Root directory to search in. Default: workspace root',
                        'default': WORKSPACE,
                    },
                    'include': {
                        'type': 'string',
                        'description': 'File glob pattern to filter files (e.g. "*.py", "*.{js,ts}"). Default: all files',
                    },
                    'max_results': {
                        'type': 'integer',
                        'description': 'Maximum number of results to return. Default: 50',
                        'default': 50,
                    },
                },
                'required': ['pattern'],
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'run_command',
            'description': (
                'Execute a shell command and return its output (stdout + stderr combined). Has a configurable '
                'timeout to prevent hanging. WARNING: This can execute arbitrary commands - be careful with '
                'destructive operations like rm -rf, format, etc. Commands run in the workspace directory by default. '
                'Output is captured and returned, limited to 30000 characters.'
            ),
            'parameters': {
                'type': 'object',
                'properties': {
                    'command': {
                        'type': 'string',
                        'description': 'Shell command to execute',
                    },
                    'timeout': {
                        'type': 'integer',
                        'description': 'Timeout in seconds. Default: 120',
                        'default': 120,
                    },
                    'cwd': {
                        'type': 'string',
                        'description': 'Working directory for the command. Default: workspace root',
                    },
                },
                'required': ['command'],
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'git_status',
            'description': (
                'Show the current git repository status including branch name, staged changes, modified files, '
                'and untracked files. Useful for understanding what has changed before committing.'
            ),
            'parameters': {
                'type': 'object',
                'properties': {
                    'repo_path': {
                        'type': 'string',
                        'description': 'Path to the git repository. Default: workspace root',
                        'default': WORKSPACE,
                    },
                },
                'required': [],
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'git_diff',
            'description': (
                'Show git diff output. Can show staged changes, unstaged changes, or changes to a specific file. '
                'Returns the unified diff format.'
            ),
            'parameters': {
                'type': 'object',
                'properties': {
                    'repo_path': {
                        'type': 'string',
                        'description': 'Path to the git repository. Default: workspace root',
                    },
                    'staged': {
                        'type': 'boolean',
                        'description': 'Show staged changes (git diff --cached). Default: false',
                        'default': False,
                    },
                    'file_path': {
                        'type': 'string',
                        'description': 'Specific file to show diff for. If omitted, shows all changes.',
                    },
                },
                'required': [],
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'git_commit',
            'description': (
                'Stage all changes and create a git commit. By default stages all changes (git add -A) before '
                'committing. Use add_all=false to only commit previously staged changes.'
            ),
            'parameters': {
                'type': 'object',
                'properties': {
                    'message': {
                        'type': 'string',
                        'description': 'Commit message',
                    },
                    'repo_path': {
                        'type': 'string',
                        'description': 'Path to the git repository. Default: workspace root',
                    },
                    'add_all': {
                        'type': 'boolean',
                        'description': 'Stage all changes before committing (git add -A). Default: true',
                        'default': True,
                    },
                },
                'required': ['message'],
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'install_package',
            'description': (
                'Install a package using pip or npm. Automatically detects the package manager based on package '
                'name format. If a virtual environment is configured, uses the venv pip. Supports version '
                'specifiers (e.g. "flask==2.3.0", "numpy>=1.24").'
            ),
            'parameters': {
                'type': 'object',
                'properties': {
                    'package_name': {
                        'type': 'string',
                        'description': 'Package name to install (e.g. "flask", "numpy>=1.24", "express")',
                    },
                    'manager': {
                        'type': 'string',
                        'description': 'Package manager: "pip", "npm", or "auto-detect". Default: auto-detect',
                        'default': 'auto',
                    },
                },
                'required': ['package_name'],
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'list_packages',
            'description': (
                'List installed packages. Returns package names and versions. For pip, uses the virtual environment '
                'pip if one is configured.'
            ),
            'parameters': {
                'type': 'object',
                'properties': {
                    'manager': {
                        'type': 'string',
                        'description': 'Package manager: "pip" or "npm". Default: "pip"',
                        'default': 'pip',
                    },
                },
                'required': [],
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'grep_code',
            'description': (
                'Advanced code search with context lines. Searches for a regex pattern across files and returns '
                'matching lines with surrounding context. Useful for understanding function usage, variable '
                'definitions, and code structure. Supports include/exclude file filters.'
            ),
            'parameters': {
                'type': 'object',
                'properties': {
                    'pattern': {
                        'type': 'string',
                        'description': 'Regex pattern to search for',
                    },
                    'path': {
                        'type': 'string',
                        'description': 'Root directory to search in. Default: workspace root',
                    },
                    'context_lines': {
                        'type': 'integer',
                        'description': 'Number of context lines before and after each match. Default: 2',
                        'default': 2,
                    },
                    'include': {
                        'type': 'string',
                        'description': 'File glob pattern to include (e.g. "*.py"). Default: all files',
                    },
                    'exclude': {
                        'type': 'string',
                        'description': 'File glob pattern to exclude (e.g. "*.min.js"). Default: none',
                    },
                },
                'required': ['pattern'],
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'file_info',
            'description': (
                'Get detailed metadata about a file or directory. Returns file size, last modified time, '
                'file type (regular file, directory, symlink), and permissions (in octal and rwx format).'
            ),
            'parameters': {
                'type': 'object',
                'properties': {
                    'path': {
                        'type': 'string',
                        'description': 'Absolute path to the file or directory',
                    },
                },
                'required': ['path'],
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'create_directory',
            'description': (
                'Create a new directory, including any necessary parent directories (equivalent to mkdir -p). '
                'If the directory already exists, the operation succeeds silently.'
            ),
            'parameters': {
                'type': 'object',
                'properties': {
                    'path': {
                        'type': 'string',
                        'description': 'Absolute path of the directory to create',
                    },
                },
                'required': ['path'],
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'web_search',
            'description': (
                'Search the web for information using DuckDuckGo. Returns a list of search results with titles, URLs, and snippets. '
                'Useful for finding documentation, APIs, libraries, or solutions to coding problems.'
            ),
            'parameters': {
                'type': 'object',
                'properties': {
                    'query': {
                        'type': 'string',
                        'description': 'Search query string',
                    },
                },
                'required': ['query'],
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'web_fetch',
            'description': (
                'Fetch a web page and return its text content. Strips HTML tags and returns plain text. '
                'Useful for reading documentation, API references, or any web page content. Max 10000 characters.'
            ),
            'parameters': {
                'type': 'object',
                'properties': {
                    'url': {
                        'type': 'string',
                        'description': 'URL to fetch',
                    },
                },
                'required': ['url'],
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'git_log',
            'description': (
                'Show git commit history. Returns a list of recent commits in oneline format.'
            ),
            'parameters': {
                'type': 'object',
                'properties': {
                    'count': {
                        'type': 'integer',
                        'description': 'Number of commits to show. Default: 10',
                        'default': 10,
                    },
                    'repo_path': {
                        'type': 'string',
                        'description': 'Path to the git repository. Default: workspace root',
                        'default': WORKSPACE,
                    },
                },
                'required': [],
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'git_checkout',
            'description': (
                'Switch to a different git branch or restore working tree files.'
            ),
            'parameters': {
                'type': 'object',
                'properties': {
                    'branch': {
                        'type': 'string',
                        'description': 'Branch name or reference to checkout',
                    },
                    'repo_path': {
                        'type': 'string',
                        'description': 'Path to the git repository. Default: workspace root',
                        'default': WORKSPACE,
                    },
                },
                'required': ['branch'],
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'delete_path',
            'description': (
                'Delete a file or directory. WARNING: This is a destructive and irreversible operation. '
                'For directories, use recursive=true to delete all contents. The workspace root itself '
                'cannot be deleted for safety.'
            ),
            'parameters': {
                'type': 'object',
                'properties': {
                    'path': {
                        'type': 'string',
                        'description': 'Absolute path to delete',
                    },
                    'recursive': {
                        'type': 'boolean',
                        'description': 'For directories, delete all contents recursively. Default: false',
                        'default': False,
                    },
                },
                'required': ['path'],
            },
        },
    },
]

# ==================== Security Helpers ====================
def _validate_path(path):
    """Ensure path stays within WORKSPACE. Returns resolved absolute path or raises ValueError."""
    real_workspace = os.path.realpath(WORKSPACE)
    real_path = os.path.realpath(path)
    if not real_path.startswith(real_workspace + os.sep) and real_path != real_workspace:
        raise ValueError(f'Access denied: path "{path}" is outside workspace')
    return real_path

def _truncate(text, limit=30000):
    """Truncate text to limit characters, appending [truncated] marker if needed."""
    if len(text) > limit:
        return text[:limit] + '\n\n[truncated: output too long, showed first ' + str(limit) + ' of ' + str(len(text)) + ' characters]'
    return text

# ==================== Tool Execution ====================
def _tool_read_file(args):
    path = _validate_path(args['path'])
    if not os.path.isfile(path):
        return f'Error: File not found: {path}'
    size = os.path.getsize(path)
    if size > 10 * 1024 * 1024:
        return f'Error: File too large ({size} bytes, max 10MB)'
    offset = args.get('offset_line', 1) - 1  # convert to 0-based
    limit = args.get('limit_lines')
    try:
        with open(path, 'rb') as f:
            raw = f.read()
        encodings = ['utf-8', 'utf-8-sig', 'gbk', 'gb2312', 'latin-1']
        content = None
        used_enc = 'utf-8'
        for enc in encodings:
            try:
                content = raw.decode(enc)
                used_enc = enc
                break
            except (UnicodeDecodeError, LookupError):
                continue
        if content is None:
            content = raw.decode('utf-8', errors='replace')
        lines = content.split('\n')
        end = (offset + limit) if limit else None
        selected = lines[offset:end]
        header = f'File: {path} (encoding: {used_enc}, size: {size} bytes, total lines: {len(lines)})'
        numbered = []
        for i, line in enumerate(selected, start=offset + 1):
            numbered.append(f'  {i:>6}\t{line}')
        result = header + '\n' + '\n'.join(numbered)
        if end and end < len(lines):
            result += f'\n\n[showing lines {offset+1}-{end} of {len(lines)}]'
        return _truncate(result)
    except Exception as e:
        return f'Error reading file: {str(e)}'

def _tool_write_file(args):
    path = _validate_path(args['path'])
    content = args['content']
    create_dirs = args.get('create_dirs', True)
    if create_dirs:
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(content)
    return f'File written successfully: {path} ({os.path.getsize(path)} bytes)'

def _tool_edit_file(args):
    path = _validate_path(args['path'])
    old_text = args['old_text']
    new_text = args['new_text']
    if not os.path.isfile(path):
        return f'Error: File not found: {path}'
    with open(path, 'r', encoding='utf-8') as f:
        content = f.read()
    count = content.count(old_text)
    if count == 0:
        return f'Error: old_text not found in file. Make sure the text matches exactly (including whitespace).'
    if count > 1:
        return f'Warning: old_text found {count} times. All occurrences will be replaced. Use more context to be specific.\nReplacements made: {count}'
    new_content = content.replace(old_text, new_text)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(new_content)
    return f'Edited file: {path} ({count} replacement(s) made)'

def _tool_list_directory(args):
    path = _validate_path(args.get('path', WORKSPACE))
    show_hidden = args.get('show_hidden', False)
    if not os.path.isdir(path):
        return f'Error: Directory not found: {path}'
    items = []
    for entry in sorted(os.listdir(path)):
        if not show_hidden and entry.startswith('.'):
            continue
        full = os.path.join(path, entry)
        try:
            st = os.stat(full)
            is_dir = os.path.isdir(full)
            ftype = 'dir' if is_dir else get_file_type(entry)
            perm = oct(st.st_mode)[-3:]
            mod_time = datetime.fromtimestamp(st.st_mtime).strftime('%Y-%m-%d %H:%M:%S')
            sz = st.st_size
            items.append(f'  {"[DIR]" if is_dir else "[FILE]"} {perm} {mod_time} {sz:>10}  {entry}  ({ftype})')
        except (PermissionError, OSError):
            items.append(f'  [??]  {entry}  (permission denied)')
    header = f'Directory: {path} ({len(items)} entries)'
    return header + '\n' + '\n'.join(items) if items else header + '\n  (empty directory)'

def _tool_search_files(args):
    pattern = args['pattern']
    search_path = _validate_path(args.get('path', WORKSPACE))
    include = args.get('include', None)
    max_results = args.get('max_results', 50)
    try:
        regex = re.compile(pattern, re.IGNORECASE)
    except re.error as e:
        return f'Error: Invalid regex pattern: {e}'
    results = []
    skip_dirs = {'.git', '__pycache__', 'node_modules', '.venv', 'venv', '.idea', '.vscode', '.svn'}
    for root, dirs, files in os.walk(search_path):
        dirs[:] = [d for d in dirs if d not in skip_dirs and not d.startswith('.')]
        for fname in files:
            if len(results) >= max_results:
                break
            if include and not fnmatch.fnmatch(fname, include):
                continue
            fpath = os.path.join(root, fname)
            try:
                with open(fpath, 'r', encoding='utf-8', errors='ignore') as f:
                    for i, line in enumerate(f, 1):
                        if regex.search(line):
                            rel = os.path.relpath(fpath, search_path)
                            results.append(f'{rel}:{i}: {line.rstrip()[:300]}')
                            if len(results) >= max_results:
                                break
            except (PermissionError, OSError):
                continue
        if len(results) >= max_results:
            break
    if not results:
        return f'No matches found for pattern "{pattern}"'
    header = f'Search results for "{pattern}" ({len(results)} matches):'
    return header + '\n' + '\n'.join(results)

def _tool_run_command(args):
    command = args['command']
    timeout = args.get('timeout', 120)
    cwd = args.get('cwd', WORKSPACE)
    try:
        cwd = _validate_path(cwd)
    except ValueError:
        cwd = WORKSPACE
    config = load_config()
    env = os.environ.copy()
    venv_path = config.get('venv_path', '')
    if venv_path and os.path.exists(venv_path):
        venv_bin = os.path.join(venv_path, 'bin')
        if os.path.exists(venv_bin):
            env['PATH'] = venv_bin + ':' + env.get('PATH', '')
            env['VIRTUAL_ENV'] = venv_path
    try:
        result = subprocess.run(
            command, shell=True, cwd=cwd, capture_output=True, text=True,
            timeout=timeout, env=env,
        )
        output = ''
        if result.stdout:
            output += result.stdout
        if result.stderr:
            output += ('\n' if output else '') + result.stderr
        exit_info = f'\n[Exit code: {result.returncode}]'
        return _truncate((output or '(no output)') + exit_info)
    except subprocess.TimeoutExpired:
        return f'Error: Command timed out after {timeout} seconds'
    except Exception as e:
        return f'Error executing command: {str(e)}'

def _tool_git_status(args):
    repo_path = args.get('repo_path', WORKSPACE)
    r = git_cmd('status --porcelain -b', cwd=repo_path)
    if not r['ok']:
        return f'Error: {r["stderr"]}'
    return r['stdout'] or 'Clean working tree (no changes)'

def _tool_git_diff(args):
    repo_path = args.get('repo_path', WORKSPACE)
    staged = args.get('staged', False)
    file_path = args.get('file_path', '')
    cmd = 'diff --cached' if staged else 'diff'
    if file_path:
        cmd += f' -- {shlex_quote(file_path)}'
    r = git_cmd(cmd, cwd=repo_path)
    return r['stdout'] or 'No changes to display'

def _tool_git_commit(args):
    message = args['message']
    repo_path = args.get('repo_path', WORKSPACE)
    add_all = args.get('add_all', True)
    if add_all:
        git_cmd('add -A', cwd=repo_path)
    r = git_cmd(f'commit -m {shlex_quote(message)}', cwd=repo_path)
    if r['ok']:
        return f'Commit successful: "{message}"'
    return f'Error: {r["stderr"]}'

def _tool_install_package(args):
    package_name = args['package_name']
    manager = args.get('manager', 'auto')
    config = load_config()
    if manager == 'auto':
        manager = 'npm' if package_name.startswith('@') or not re.search(r'[a-zA-Z]-[a-zA-Z]', package_name) and os.path.exists(os.path.join(WORKSPACE, 'package.json')) else 'pip'
    if manager == 'npm':
        cmd = f'npm install {shlex_quote(package_name)}'
    else:
        venv = config.get('venv_path', '')
        pip = os.path.join(venv, 'bin', 'pip') if venv and os.path.exists(os.path.join(venv, 'bin', 'pip')) else 'pip3'
        cmd = f'{pip} install {shlex_quote(package_name)}'
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=300, cwd=WORKSPACE)
    output = r.stdout or ''
    if r.stderr:
        output += ('\n' if output else '') + r.stderr
    if r.returncode == 0:
        return _truncate(f'Package installed successfully: {package_name}\n{output}')
    return _truncate(f'Error installing {package_name} (exit code {r.returncode}):\n{output}')

def _tool_list_packages(args):
    manager = args.get('manager', 'pip')
    config = load_config()
    if manager == 'npm':
        r = subprocess.run('npm list --depth=0 2>/dev/null', shell=True, capture_output=True, text=True, timeout=30, cwd=WORKSPACE)
        return r.stdout or 'No packages found'
    venv = config.get('venv_path', '')
    pip = os.path.join(venv, 'bin', 'pip') if venv and os.path.exists(os.path.join(venv, 'bin', 'pip')) else 'pip3'
    r = subprocess.run(f'{pip} list --format=json', shell=True, capture_output=True, text=True, timeout=30)
    if r.returncode == 0:
        try:
            pkgs = json.loads(r.stdout)
            lines = [f'  {p["name"]}=={p["version"]}' for p in pkgs]
            return f'Installed packages ({len(lines)}):\n' + '\n'.join(lines)
        except Exception:
            pass
    return r.stdout or r.stderr or 'No packages found'

def _tool_grep_code(args):
    pattern = args['pattern']
    search_path = _validate_path(args.get('path', WORKSPACE))
    context = args.get('context_lines', 2)
    include = args.get('include', None)
    exclude = args.get('exclude', None)
    try:
        regex = re.compile(pattern, re.IGNORECASE)
    except re.error as e:
        return f'Error: Invalid regex: {e}'
    results = []
    skip_dirs = {'.git', '__pycache__', 'node_modules', '.venv', 'venv', '.idea', '.vscode'}
    for root, dirs, files in os.walk(search_path):
        dirs[:] = [d for d in dirs if d not in skip_dirs]
        for fname in files:
            if include and not fnmatch.fnmatch(fname, include):
                continue
            if exclude and fnmatch.fnmatch(fname, exclude):
                continue
            fpath = os.path.join(root, fname)
            try:
                with open(fpath, 'r', encoding='utf-8', errors='ignore') as f:
                    all_lines = f.readlines()
                matches = []
                for i, line in enumerate(all_lines):
                    if regex.search(line):
                        matches.append(i)
                if not matches:
                    continue
                rel = os.path.relpath(fpath, search_path)
                for idx in matches:
                    start = max(0, idx - context)
                    end = min(len(all_lines), idx + context + 1)
                    results.append(f'\n{rel}:{idx+1}:\n' + ''.join(
                        f'  {"*" if j == idx else " "} {j+1:>5}\t{all_lines[j].rstrip()}\n'
                        for j in range(start, end)
                    ))
                if len(results) >= 30:
                    break
            except (PermissionError, OSError):
                continue
        if len(results) >= 30:
            break
    if not results:
        return f'No matches for pattern "{pattern}"'
    return f'Found {len(results)} match(es) for "{pattern}":\n' + '\n'.join(results)

def _tool_file_info(args):
    path = _validate_path(args['path'])
    if not os.path.exists(path):
        return f'Error: Path not found: {path}'
    st = os.stat(path)
    is_dir = os.path.isdir(path)
    is_link = os.path.islink(path)
    ftype = 'symlink' if is_link else ('directory' if is_dir else 'regular file')
    size = st.st_size
    if is_dir:
        try:
            size = sum(
                os.path.getsize(os.path.join(dp, f))
                for dp, dn, fn in os.walk(path)
                for f in fn
            )
        except (PermissionError, OSError):
            size = 0
    mod_time = datetime.fromtimestamp(st.st_mtime).strftime('%Y-%m-%d %H:%M:%S')
    perm_oct = oct(st.st_mode)[-3:]
    perm_rwx = ''
    for p in perm_oct:
        perm_rwx += {'0': '---', '1': '--x', '2': '-w-', '3': '-wx', '4': 'r--', '5': 'r-x', '6': 'rw-', '7': 'rwx'}[p] + ' '
    return (
        f'Path:     {path}\n'
        f'Type:     {ftype}\n'
        f'Size:     {size:,} bytes\n'
        f'Modified: {mod_time}\n'
        f'Permissions: {perm_oct} ({perm_rwx.strip()})'
    )

def _tool_create_directory(args):
    path = _validate_path(args['path'])
    os.makedirs(path, exist_ok=True)
    return f'Directory created: {path}'

def _tool_web_search(args):
    query = args.get('query', '')
    try:
        url = 'https://html.duckduckgo.com/html/?q=' + urllib.parse.quote_plus(query)
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (compatible; PhoneIDE Bot)'})
        with urllib.request.urlopen(req, timeout=15) as resp:
            html_content = resp.read().decode('utf-8', errors='ignore')
        results = []
        for match in re.finditer(r'<a rel="nofollow" class="result__a" href="([^"]+)">([^<]+)</a>.*?<a class="result__snippet"[^>]*>([^<]*(?:<[^a][^<]*)*)</a>', html_content, re.DOTALL):
            link = match.group(1)
            title = re.sub(r'<[^>]+>', '', match.group(2)).strip()
            snippet = re.sub(r'<[^>]+>', '', match.group(3)).strip()
            if link.startswith('//'):
                link = 'https:' + link
            results.append({'title': title, 'url': link, 'snippet': snippet})
            if len(results) >= 10:
                break
        if not results:
            return f'No results found for "{query}"'
        lines = []
        for i, r in enumerate(results, 1):
            lines.append(f'{i}. {r["title"]}')
            lines.append(f'   URL: {r["url"]}')
            lines.append(f'   {r["snippet"]}')
            lines.append('')
        return f'Search results for "{query}" ({len(results)} results):\n' + '\n'.join(lines)
    except Exception as e:
        return f'Error searching: {str(e)}'

def _tool_web_fetch(args):
    url = args.get('url', '')
    if not url:
        return 'Error: URL is required'
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (compatible; PhoneIDE Bot)'})
        with urllib.request.urlopen(req, timeout=15) as resp:
            html_content = resp.read().decode('utf-8', errors='ignore')
        # Strip HTML tags
        text = re.sub(r'<script[^>]*>[\s\S]*?</script>', '', html_content, flags=re.IGNORECASE)
        text = re.sub(r'<style[^>]*>[\s\S]*?</style>', '', text, flags=re.IGNORECASE)
        text = re.sub(r'<[^>]+>', ' ', text)
        text = re.sub(r'&nbsp;', ' ', text)
        text = re.sub(r'&amp;', '&', text)
        text = re.sub(r'&lt;', '<', text)
        text = re.sub(r'&gt;', '>', text)
        text = re.sub(r'&quot;', '"', text)
        text = re.sub(r'&#39;', "'", text)
        text = re.sub(r'\s+', ' ', text).strip()
        if len(text) > 10000:
            text = text[:10000] + '\n\n[truncated: content exceeds 10000 character limit]'
        if not text:
            return 'No text content found at the URL'
        return f'Content from {url}:\n{text}'
    except Exception as e:
        return f'Error fetching URL: {str(e)}'

def _tool_git_log(args):
    count = args.get('count', 10)
    repo_path = args.get('repo_path', WORKSPACE)
    r = git_cmd(f'log --oneline -n {count}', cwd=repo_path)
    if not r['ok']:
        return f'Error: {r["stderr"]}'
    return r['stdout'] or 'No commits found'

def _tool_git_checkout(args):
    branch = args.get('branch', '')
    repo_path = args.get('repo_path', WORKSPACE)
    if not branch:
        return 'Error: branch name is required'
    r = git_cmd(f'checkout {shlex_quote(branch)}', cwd=repo_path)
    if r['ok']:
        return f'Switched to branch "{branch}"'
    return f'Error: {r["stderr"]}'

def _tool_delete_path(args):
    path = _validate_path(args['path'])
    real_ws = os.path.realpath(WORKSPACE)
    if os.path.realpath(path) == real_ws:
        return 'Error: Cannot delete the workspace root'
    if not os.path.exists(path):
        return f'Error: Path not found: {path}'
    recursive = args.get('recursive', False)
    try:
        if os.path.isdir(path):
            if recursive:
                shutil.rmtree(path)
                return f'Directory deleted recursively: {path}'
            else:
                try:
                    os.rmdir(path)
                    return f'Directory deleted (must be empty): {path}'
                except OSError as e:
                    return f'Error: Directory not empty. Use recursive=true to delete: {e}'
        else:
            os.remove(path)
            return f'File deleted: {path}'
    except Exception as e:
        return f'Error deleting path: {str(e)}'

_TOOL_HANDLERS = {
    'read_file': _tool_read_file,
    'write_file': _tool_write_file,
    'edit_file': _tool_edit_file,
    'list_directory': _tool_list_directory,
    'search_files': _tool_search_files,
    'run_command': _tool_run_command,
    'git_status': _tool_git_status,
    'git_diff': _tool_git_diff,
    'git_commit': _tool_git_commit,
    'git_log': _tool_git_log,
    'git_checkout': _tool_git_checkout,
    'install_package': _tool_install_package,
    'list_packages': _tool_list_packages,
    'grep_code': _tool_grep_code,
    'file_info': _tool_file_info,
    'create_directory': _tool_create_directory,
    'delete_path': _tool_delete_path,
    'web_search': _tool_web_search,
    'web_fetch': _tool_web_fetch,
}

def execute_agent_tool(name, arguments):
    """Execute a tool by name with given arguments. Returns (ok, result_string, elapsed_seconds)."""
    handler = _TOOL_HANDLERS.get(name)
    if not handler:
        return False, f'Error: Unknown tool "{name}". Available tools: {", ".join(_TOOL_HANDLERS.keys())}', 0
    t0 = time.time()
    try:
        result = handler(arguments)
        elapsed = time.time() - t0
        return True, result, elapsed
    except ValueError as e:
        return False, f'Security error: {e}', time.time() - t0
    except Exception as e:
        return False, f'Tool execution error: {str(e)}', time.time() - t0

# ==================== LLM Integration ====================
def _build_api_messages(messages, llm_config):
    """Convert chat history to API format with system prompt."""
    sys_prompt = llm_config.get('system_prompt', '')
    if not sys_prompt:
        sys_prompt = DEFAULT_SYSTEM_PROMPT
    elif WORKSPACE not in sys_prompt:
        sys_prompt += f'\n\nCurrent workspace: {WORKSPACE}\nServer directory: {SERVER_DIR}\n'
    api_messages = [{'role': 'system', 'content': sys_prompt}]
    for msg in messages:
        role = msg.get('role', '')
        if role == 'system':
            continue
        elif role == 'tool':
            api_messages.append({
                'role': 'tool',
                'tool_call_id': msg.get('tool_call_id', 'call_default'),
                'content': msg.get('content', ''),
            })
        elif role == 'assistant' and msg.get('tool_calls'):
            api_messages.append({
                'role': 'assistant',
                'content': msg.get('content', None),
                'tool_calls': msg['tool_calls'],
            })
        elif role in ('user', 'assistant'):
            api_messages.append({'role': role, 'content': msg.get('content', '')})
    return api_messages

def _get_llm_endpoint(llm_config, model=None):
    """Build URL and headers for an LLM API call based on api_type.

    Returns (url, headers) tuple.
    """
    api_key = llm_config.get('api_key', '')
    api_type = llm_config.get('api_type', 'openai')
    api_base = (llm_config.get('api_base') or '').rstrip('/')
    model = model or llm_config.get('model', 'gpt-4o-mini')

    headers = {'Content-Type': 'application/json'}

    if api_type == 'ollama':
        # Ollama local server — no auth needed
        if not api_base:
            api_base = 'http://localhost:11434'
        # Ollama uses /api/chat (not /v1/chat/completions)
        url = api_base + '/api/chat'
    elif api_type == 'azure':
        if not api_base:
            raise Exception('Azure OpenAI: API base URL is required (e.g. https://xxx.openai.azure.com)')
        url = api_base + f'/openai/deployments/{model}/chat/completions?api-version=2024-02-01'
        if api_key:
            headers['Authorization'] = f'Bearer {api_key}'
    else:
        # openai / custom — OpenAI-compatible format
        if not api_base:
            api_base = 'https://api.openai.com/v1'
        # Ensure we use /v1/chat/completions/ (with trailing slash to avoid redirects)
        # If api_base already ends with /v1, use /chat/completions/
        # Otherwise, use /v1/chat/completions/
        if api_base.endswith('/v1'):
            url = api_base + '/chat/completions/'
        else:
            url = api_base + '/v1/chat/completions/'
        if api_key:
            headers['Authorization'] = f'Bearer {api_key}'

    return url, headers


def _call_llm_api(messages, llm_config, stream=False):
    """Make a non-streaming LLM API call. Returns parsed response dict."""
    model = llm_config.get('model', 'gpt-4o-mini')
    temperature = llm_config.get('temperature', 0.7)
    max_tokens = llm_config.get('max_tokens', 4096)

    api_messages = _build_api_messages(messages, llm_config)

    payload = {
        'model': model,
        'messages': api_messages,
        'temperature': temperature,
        'max_tokens': max_tokens,
        'tools': AGENT_TOOLS,
        'tool_choice': 'auto',
    }
    if stream:
        payload['stream'] = True

    try:
        url, headers = _get_llm_endpoint(llm_config, model)
    except Exception as e:
        raise Exception(f'LLM config error: {e}')

    headers = headers or {'Content-Type': 'application/json'}

    req = urllib.request.Request(url, json.dumps(payload).encode(), headers=headers, method='POST')

    with urllib.request.urlopen(req, timeout=180) as resp:
        resp_body = resp.read().decode()
        try:
            result = json.loads(resp_body)
        except (json.JSONDecodeError, ValueError) as je:
            raise Exception(f'Invalid JSON response from LLM API: {str(je)}')
    return result

def _call_llm_stream_raw(messages, llm_config):
    """Stream LLM response as raw SSE data chunks. Yields parsed delta objects."""
    import urllib.request

    model = llm_config.get('model', 'gpt-4o-mini')
    temperature = llm_config.get('temperature', 0.7)
    max_tokens = llm_config.get('max_tokens', 4096)

    api_messages = _build_api_messages(messages, llm_config)

    payload = {
        'model': model,
        'messages': api_messages,
        'temperature': temperature,
        'max_tokens': max_tokens,
        'tools': AGENT_TOOLS,
        'tool_choice': 'auto',
        'stream': True,
    }

    try:
        url, headers = _get_llm_endpoint(llm_config, model)
    except Exception as e:
        raise Exception(f'LLM config error: {e}')

    headers = headers or {'Content-Type': 'application/json'}

    req = urllib.request.Request(url, json.dumps(payload).encode(), headers=headers, method='POST')

    with urllib.request.urlopen(req, timeout=180) as resp:
        buffer = ''
        while True:
            chunk = resp.read(4096)
            if not chunk:
                break
            buffer += chunk.decode('utf-8', errors='ignore')
            while '\n' in buffer:
                line, buffer = buffer.split('\n', 1)
                line = line.strip()
                if not line.startswith('data: '):
                    continue
                data_str = line[6:]
                if data_str == '[DONE]':
                    return
                try:
                    data = json.loads(data_str)
                    choices = data.get('choices', [])
                    if choices:
                        yield choices[0].get('delta', {})
                except json.JSONDecodeError:
                    continue

# ==================== Context Window Management ====================
def _estimate_tokens(text):
    """Rough token estimation: ~4 characters per token."""
    return len(text) // 4

def _compress_context(messages, max_tokens=None):
    """Compress conversation history to fit within context window.
    Keeps: system prompt, last 2 user messages, last 4 tool results, summary of older messages.
    """
    if not messages:
        return messages
    max_tokens = max_tokens or 60000
    total = sum(_estimate_tokens(m.get('content', '') or '') for m in messages)
    if total <= max_tokens:
        return messages

    # Split messages into older and recent
    # Find the last 2 user messages from the end
    user_indices = [i for i, m in enumerate(messages) if m.get('role') == 'user']
    if len(user_indices) >= 2:
        split_idx = user_indices[-2]
    else:
        split_idx = max(0, len(messages) - 6)

    older = messages[:split_idx]
    recent = messages[split_idx:]

    # Build a summary of older messages
    summary_parts = []
    for msg in older:
        role = msg.get('role', '')
        content = msg.get('content', '')
        if role == 'user':
            summary_parts.append(f'[User]: {content[:200]}')
        elif role == 'assistant':
            summary_parts.append(f'[Assistant]: {content[:200]}')
        elif role == 'tool':
            name = msg.get('name', 'tool')
            summary_parts.append(f'[Tool {name}]: {_truncate(content, 100)}')

    summary = 'Earlier conversation summary:\n' + '\n'.join(summary_parts[-10:])
    summary_msg = {'role': 'system', 'content': summary}

    # Compress recent tool results if still too large
    compressed_recent = []
    for msg in recent:
        if msg.get('role') == 'tool':
            content = msg.get('content', '')
            if len(content) > 3000:
                msg = dict(msg, content=content[:3000] + '\n[truncated for context]')
        compressed_recent.append(msg)

    # Re-check total size
    all_msgs = [summary_msg] + compressed_recent
    total2 = sum(_estimate_tokens(m.get('content', '') or '') for m in all_msgs)
    if total2 > max_tokens:
        # Further trim tool results
        for msg in all_msgs:
            if msg.get('role') == 'tool':
                content = msg.get('content', '')
                if len(content) > 1000:
                    msg['content'] = content[:1000] + '\n[truncated]'

    return all_msgs

# ==================== Agent Loop ====================
MAX_AGENT_ITERATIONS = 100  # Increased from 15 for complex tasks
MAX_ITERATION_RETRIES = 3

def run_agent_loop(user_message, llm_config, history=None, stream_callback=None):
    """Run the full agent loop: LLM -> tools -> LLM -> ... until final answer.

    Args:
        user_message: The user's message string.
        llm_config: LLM configuration dict.
        history: Existing chat history list (will be appended to).
        stream_callback: Optional callable(event_dict) for real-time streaming.

    Returns:
        dict with 'content' (final text), 'iterations', 'tool_calls_made', 'history'
    """
    if history is None:
        history = load_chat_history()

    user_msg = {'role': 'user', 'content': user_message, 'time': datetime.now().isoformat()}
    history.append(user_msg)

    # Compress context if needed
    context = _compress_context(history, max_tokens=llm_config.get('max_tokens', 4096) * 10)

    def _emit(event):
        if stream_callback:
            stream_callback(event)

    final_content = ''
    total_iterations = 0
    all_tool_calls = []

    for iteration in range(MAX_AGENT_ITERATIONS):
        total_iterations = iteration + 1
        _emit({'type': 'thinking', 'content': f'Iteration {iteration + 1}: Calling LLM...'})

        # Call LLM with retries
        response = None
        for retry in range(MAX_ITERATION_RETRIES):
            try:
                response = _call_llm_api(context, llm_config)
                break
            except urllib.error.HTTPError as e:
                body = e.read().decode() if hasattr(e, 'read') else ''
                if retry < MAX_ITERATION_RETRIES - 1:
                    _emit({'type': 'thinking', 'content': f'LLM API error (retry {retry + 1}): {e.code} {body[:200]}'})
                    time.sleep(1 * (retry + 1))
                else:
                    raise Exception(f'LLM API error after {MAX_ITERATION_RETRIES} retries ({e.code}): {body[:500]}')
            except Exception as e:
                if retry < MAX_ITERATION_RETRIES - 1:
                    _emit({'type': 'thinking', 'content': f'Retry {retry + 1}: {str(e)[:200]}'})
                    time.sleep(1 * (retry + 1))
                else:
                    raise Exception(f'LLM request failed after {MAX_ITERATION_RETRIES} retries: {str(e)}')

        # Parse response
        choice = response.get('choices', [{}])[0]
        message = choice.get('message', {})
        content = message.get('content', '') or ''
        tool_calls_raw = message.get('tool_calls', [])

        # Stream text content
        if content:
            _emit({'type': 'text', 'content': content})
            final_content = content

        # If no tool calls, we're done
        if not tool_calls_raw:
            break

        # Add assistant message with tool_calls to context
        assistant_msg = {
            'role': 'assistant',
            'content': content or None,
            'tool_calls': tool_calls_raw,
            'time': datetime.now().isoformat(),
        }
        context.append(assistant_msg)

        # Execute each tool call
        for tc in tool_calls_raw:
            func = tc.get('function', {})
            tool_name = func.get('name', '')
            try:
                tool_args = json.loads(func.get('arguments', '{}'))
            except json.JSONDecodeError:
                tool_args = {}

            tool_call_id = tc.get('id', f'call_{tool_name}')
            all_tool_calls.append({'name': tool_name, 'args': tool_args})

            _emit({'type': 'tool_start', 'tool': tool_name, 'args': tool_args})

            ok, result_str, elapsed = execute_agent_tool(tool_name, tool_args)

            _emit({
                'type': 'tool_result',
                'tool': tool_name,
                'ok': ok,
                'result': _truncate(result_str, 30000),
                'elapsed': round(elapsed, 2),
            })

            # Add tool result to context
            context.append({
                'role': 'tool',
                'tool_call_id': tool_call_id,
                'name': tool_name,
                'content': result_str,
                'time': datetime.now().isoformat(),
            })

            # Re-check context size and compress if needed
            context = _compress_context(context, max_tokens=llm_config.get('max_tokens', 4096) * 10)

    # Build final assistant message for history
    final_assistant = {
        'role': 'assistant',
        'content': final_content,
        'tool_calls_made': all_tool_calls,
        'iterations': total_iterations,
        'time': datetime.now().isoformat(),
    }
    history.append(final_assistant)

    return {
        'content': final_content,
        'iterations': total_iterations,
        'tool_calls_made': all_tool_calls,
        'history': history,
    }

def run_agent_loop_stream(user_message, llm_config, conv_id=None):
    """Generator that runs the agent loop and yields SSE events."""
    # Load history from conversation if conv_id provided, otherwise from legacy chat_history
    if conv_id:
        conv = get_conversation(conv_id)
        history = list(conv.get('messages', [])) if conv else []
    else:
        history = load_chat_history()
    user_msg = {'role': 'user', 'content': user_message, 'time': datetime.now().isoformat()}
    history.append(user_msg)

    context = _compress_context(history, max_tokens=llm_config.get('max_tokens', 4096) * 10)

    final_content = ''
    total_iterations = 0
    accumulated_text = ''
    tool_calls_in_progress = []
    # Buffer for streaming tool_calls assembly
    current_tool_calls = []
    current_tool_call_idx = {}
    current_args_buffer = {}

    for iteration in range(MAX_AGENT_ITERATIONS):
        total_iterations = iteration + 1
        yield f"data: {json.dumps({'type': 'thinking', 'content': f'Iteration {iteration + 1}: Calling LLM...'})}\n\n"

        # Call LLM with streaming
        response_message = None
        for retry in range(MAX_ITERATION_RETRIES):
            try:
                current_tool_calls = []
                current_args_buffer = {}
                current_tool_call_idx = {}
                delta_content = ''
                delta_tool_calls = []

                for delta in _call_llm_stream_raw(context, llm_config):
                    # Handle text content
                    content_chunk = delta.get('content')
                    if content_chunk:
                        delta_content += content_chunk
                        accumulated_text += content_chunk
                        yield f"data: {json.dumps({'type': 'text', 'content': content_chunk})}\n\n"

                    # Handle tool_calls (assembled from streaming deltas)
                    tc_delta = delta.get('tool_calls')
                    if tc_delta:
                        for tc_part in tc_delta:
                            idx = tc_part.get('index', 0)
                            if idx not in current_tool_call_idx:
                                current_tool_call_idx[idx] = len(current_tool_calls)
                                tc_entry = {
                                    'id': tc_part.get('id', f'call_{idx}'),
                                    'type': 'function',
                                    'function': {'name': '', 'arguments': ''},
                                }
                                current_tool_calls.append(tc_entry)
                                current_args_buffer[idx] = ''

                            tc_entry = current_tool_calls[current_tool_call_idx[idx]]
                            if tc_part.get('id'):
                                tc_entry['id'] = tc_part['id']
                            func_delta = tc_part.get('function', {})
                            if func_delta.get('name'):
                                tc_entry['function']['name'] += func_delta['name']
                            if func_delta.get('arguments'):
                                current_args_buffer[idx] += func_delta['arguments']

                # Finalize tool call arguments
                for idx, tc_entry in enumerate(current_tool_calls):
                    if idx in current_args_buffer:
                        tc_entry['function']['arguments'] = current_args_buffer[idx]

                # Build the complete response message
                response_message = {
                    'role': 'assistant',
                    'content': delta_content or None,
                }
                if current_tool_calls:
                    response_message['tool_calls'] = current_tool_calls
                break

            except urllib.error.HTTPError as e:
                body = e.read().decode() if hasattr(e, 'read') else ''
                if retry < MAX_ITERATION_RETRIES - 1:
                    yield f"data: {json.dumps({'type': 'thinking', 'content': f'LLM API error (retry {retry + 1}): {e.code} {body[:200]}'})}\n\n"
                    time.sleep(1 * (retry + 1))
                else:
                    yield f"data: {json.dumps({'type': 'error', 'content': f'LLM API error after {MAX_ITERATION_RETRIES} retries ({e.code}): {body[:500]}'})}\n\n"
                    return
            except Exception as e:
                if retry < MAX_ITERATION_RETRIES - 1:
                    yield f"data: {json.dumps({'type': 'thinking', 'content': f'Retry {retry + 1}: {str(e)[:200]}'})}\n\n"
                    time.sleep(1 * (retry + 1))
                else:
                    yield f"data: {json.dumps({'type': 'error', 'content': f'LLM request failed: {str(e)}'})}\n\n"
                    return

        if response_message is None:
            response_message = {'role': 'assistant', 'content': accumulated_text or '(no response)'}

        content = response_message.get('content', '') or ''
        tool_calls_raw = response_message.get('tool_calls', [])

        if content:
            final_content = accumulated_text.strip() if accumulated_text.strip() else content

        # If no tool calls, we're done
        if not tool_calls_raw:
            break

        # Add assistant message to context
        context.append({
            'role': 'assistant',
            'content': content or None,
            'tool_calls': tool_calls_raw,
            'time': datetime.now().isoformat(),
        })

        # Reset accumulated text for next iteration
        accumulated_text = ''

        # Execute each tool call
        for tc in tool_calls_raw:
            func = tc.get('function', {})
            tool_name = func.get('name', '')
            try:
                tool_args = json.loads(func.get('arguments', '{}'))
            except json.JSONDecodeError:
                tool_args = {}

            tool_call_id = tc.get('id', f'call_{tool_name}')
            tool_calls_in_progress.append({'name': tool_name, 'args': tool_args})

            yield f"data: {json.dumps({'type': 'tool_start', 'tool': tool_name, 'args': tool_args})}\n\n"

            ok, result_str, elapsed = execute_agent_tool(tool_name, tool_args)

            yield f"data: {json.dumps({'type': 'tool_result', 'tool': tool_name, 'ok': ok, 'result': _truncate(result_str, 30000), 'elapsed': round(elapsed, 2)})}\n\n"

            context.append({
                'role': 'tool',
                'tool_call_id': tool_call_id,
                'name': tool_name,
                'content': result_str,
                'time': datetime.now().isoformat(),
            })

            # Compress context if needed
            context = _compress_context(context, max_tokens=llm_config.get('max_tokens', 4096) * 10)

    # Build final assistant message for history
    final_assistant = {
        'role': 'assistant',
        'content': final_content,
        'tool_calls_made': tool_calls_in_progress,
        'iterations': total_iterations,
        'time': datetime.now().isoformat(),
    }
    history.append(final_assistant)
    save_chat_history(history)
    # Also save to conversation if conv_id was provided
    if conv_id:
        save_conversation(conv_id, history)

    yield f"data: {json.dumps({'type': 'done', 'iterations': total_iterations, 'tool_calls': len(tool_calls_in_progress), 'completed': True})}\n\n"

# ==================== Chat Endpoints ====================
@bp.route('/api/chat/history', methods=['GET'])
def get_chat_history():
    history = load_chat_history()
    return jsonify({'messages': history})

@bp.route('/api/chat/clear', methods=['POST'])
def clear_chat_history():
    save_chat_history([])
    return jsonify({'ok': True})

# ==================== Conversations API ====================
@bp.route('/api/conversations', methods=['GET'])
def list_conversations():
    """List all conversations (summary, no messages)."""
    convs = load_conversations()
    result = []
    for c in convs:
        result.append({
            'id': c.get('id', ''),
            'title': c.get('title', 'New Chat'),
            'created_at': c.get('created_at', ''),
            'updated_at': c.get('updated_at', ''),
            'message_count': len(c.get('messages', [])),
        })
    return jsonify({'conversations': result})

@bp.route('/api/conversations/<conv_id>', methods=['GET'])
def get_conv(conv_id):
    """Get a single conversation with messages."""
    conv = get_conversation(conv_id)
    if not conv:
        return jsonify({'error': 'Conversation not found'}), 404
    return jsonify(conv)

@bp.route('/api/conversations/<conv_id>', methods=['DELETE'])
def delete_conv(conv_id):
    """Delete a conversation."""
    delete_conversation(conv_id)
    return jsonify({'ok': True})

@bp.route('/api/conversations/<conv_id>', methods=['PATCH'])
def update_conv(conv_id):
    """Update conversation title."""
    data = request.json or {}
    convs = load_conversations()
    for c in convs:
        if c.get('id') == conv_id:
            if 'title' in data:
                c['title'] = data['title']
            break
    else:
        return jsonify({'error': 'Conversation not found'}), 404
    save_conversations(convs)
    return jsonify({'ok': True})

@bp.route('/api/chat/send', methods=['POST'])
@handle_error
def send_chat_message():
    """Non-streaming agent endpoint. Returns complete result after agent loop finishes."""
    data = request.json
    message = data.get('message', '').strip()
    if not message:
        return jsonify({'error': 'Message required'}), 400

    llm_config = get_active_llm_config()

    try:
        events = []
        result = run_agent_loop(message, llm_config, stream_callback=lambda e: events.append(e))
        save_chat_history(result['history'])
        return jsonify({
            'response': {'role': 'assistant', 'content': result['content']},
            'iterations': result['iterations'],
            'tool_calls_made': result['tool_calls_made'],
            'events': events,
            'history': result['history'][-20:],
        })
    except Exception as e:
        return jsonify({'error': str(e), 'response': {'role': 'assistant', 'content': f'Error: {str(e)}'}}), 500

@bp.route('/api/chat/send/stream', methods=['POST'])
def send_chat_stream():
    """SSE streaming agent endpoint. Streams text, tool execution, and status in real-time."""
    data = request.json
    message = data.get('message', '').strip()
    conv_id = data.get('conv_id')  # optional conversation id
    if not message:
        return jsonify({'error': 'Message required'}), 400

    llm_config = get_active_llm_config()

    def generate():
        try:
            for sse_event in run_agent_loop_stream(message, llm_config, conv_id=conv_id):
                yield sse_event
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'content': str(e)})}\n\n"

    return Response(generate(), mimetype='text/event-stream',
                    headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})

@bp.route('/api/tools', methods=['GET'])
def list_agent_tools():
    """List all available agent tools with their schemas."""
    tools_info = []
    for t in AGENT_TOOLS:
        f = t.get('function', {})
        tools_info.append({
            'name': f.get('name', ''),
            'description': f.get('description', ''),
            'parameters': f.get('parameters', {}),
        })
    return jsonify({'tools': tools_info})

@bp.route('/api/llm/config', methods=['GET'])
@handle_error
def get_llm_config():
    cfg = load_llm_config()
    # Mask API keys in models
    for m in cfg.get('models', []):
        key = m.get('api_key', '')
        if key:
            m['api_key_masked'] = key[:8] + '...' + key[-4:] if len(key) > 12 else '***'
        else:
            m['api_key_masked'] = ''
    return jsonify(cfg)

@bp.route('/api/llm/config', methods=['POST'])
@handle_error
def update_llm_config():
    config = request.json
    save_llm_config(config)
    return jsonify({'ok': True})


@bp.route('/api/llm/test', methods=['POST'])
def test_llm_config():
    """Test a specific model configuration or the active one."""
    try:
        data = request.json or {}
        # If a model index is provided, test that specific model
        if data.get('model_index') is not None:
            all_config = load_llm_config()
            models = all_config.get('models', [])
            idx = int(data['model_index'])
            if 0 <= idx < len(models):
                llm_config = dict(models[idx])
                llm_config['system_prompt'] = all_config.get('system_prompt', '')
            else:
                return jsonify({'ok': False, 'error': f'Invalid model index: {idx}'})
        else:
            llm_config = get_active_llm_config()

        api_type = llm_config.get('api_type', 'openai')
        api_key = llm_config.get('api_key', '')
        api_base = (llm_config.get('api_base') or '').rstrip('/')
        model = llm_config.get('model', 'gpt-4o-mini')

        # Ollama local mode does not require an API key
        if not api_key and api_type != 'ollama':
            return jsonify({'ok': False, 'error': 'API key not configured'})

        # Build endpoint URL and headers based on api_type
        try:
            url, headers = _get_llm_endpoint(llm_config, model)
        except Exception as e:
            return jsonify({'ok': False, 'error': str(e)})

        payload = {
            'model': model,
            'messages': [{'role': 'user', 'content': 'Hi, reply with just "OK".'}],
            'max_tokens': 500,
            'stream': False,
        }

        req = urllib.request.Request(url, json.dumps(payload).encode(), headers=headers, method='POST')
        with urllib.request.urlopen(req, timeout=60) as resp:
            resp_body = resp.read().decode()
            try:
                result = json.loads(resp_body)
            except (json.JSONDecodeError, ValueError) as je:
                return jsonify({'ok': False, 'error': f'API returned non-JSON response (api_type={api_type}): {str(je)}'})

        model_used = model
        reply_content = ''
        try:
            model_used = result.get('model', model)
            usage = result.get('usage', {})
            tokens = usage.get('total_tokens', 0)
            # Extract actual reply content from the response
            choices = result.get('choices', [])
            if choices:
                msg = choices[0].get('message', {})
                reply_content = msg.get('content', '') or ''
        except Exception:
            tokens = 0

        # Warn if model returned empty content (may happen with reasoning models if max_tokens too low)
        if not reply_content:
            return jsonify({
                'ok': True,
                'model': model_used,
                'tokens': tokens,
                'reply': '',
                'warning': 'Model returned empty content. If using a reasoning model, the max_tokens setting may be too low.'
            })

        return jsonify({'ok': True, 'model': model_used, 'tokens': tokens, 'reply': reply_content[:200]})
    except urllib.error.HTTPError as e:
        body = ''
        try:
            body = e.read().decode()[:500]
        except Exception:
            pass
        # Try to extract JSON error message from the body
        try:
            err_data = json.loads(body)
            err_msg = err_data.get('error', {})
            if isinstance(err_msg, dict):
                err_msg = err_msg.get('message', body[:300])
            else:
                err_msg = str(err_msg) or body[:300]
        except Exception:
            err_msg = body[:300]
        return jsonify({'ok': False, 'error': f'HTTP {e.code}: {err_msg}'})
    except urllib.error.URLError as e:
        reason = str(e.reason)
        if 'refused' in reason.lower():
            hint = '. Check if the API server is running.'
        elif 'name or service not known' in reason.lower():
            hint = '. Check the API base URL.'
        else:
            hint = ''
        return jsonify({'ok': False, 'error': f'Connection failed: {reason}{hint}'})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)})
