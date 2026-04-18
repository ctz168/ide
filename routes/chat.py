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
import threading
import queue
import urllib.request
import urllib.error
import urllib.parse
from collections import deque

# Custom redirect handler that follows 307/308 for POST requests
class _PostRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if code in (307, 308):
            # Preserve POST method and body
            return urllib.request.Request(newurl, data=req.data, headers=req.headers,
                                          method=req.method, origin_req_host=req.origin_req_host)
        return super().redirect_request(req, fp, code, msg, headers, newurl)

_urllib_opener = urllib.request.build_opener(_PostRedirectHandler)
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
from routes.browser import create_browser_command, wait_browser_result

bp = Blueprint('chat', __name__)

# ==================== Global Active Task State ====================
_active_task = {
    'running': False,
    'conv_id': None,
    'message': None,
    'model_index': None,
    'started_at': None,
    'event_queue': None,       # queue.Queue for broadcasting events to subscribers
    'event_buffer': None,      # deque-based ring buffer of last 100 raw SSE event strings
    'subscribers': 0,          # count of active SSE subscribers
    'lock': threading.Lock(),
    'thread': None,            # background thread running the agent loop
}

RING_BUFFER_SIZE = 100

# ==================== System Prompt ====================
DEFAULT_SYSTEM_PROMPT = f"""You are PhoneIDE AI Agent, a powerful coding assistant integrated in a mobile IDE.
You have access to tools that let you read/write files, execute code, search projects, manage git, **debug web pages in the built-in preview**, and more.

## Available Tools
You have **38 tools** available. When you need to perform an action, call the appropriate tool using function calling.
For multi-step tasks, think step by step and use tools in sequence.

### File & Code Tools (19)
- `read_file` / `write_file` / `edit_file` -- Read, create, or modify files
- `list_directory` / `search_files` / `grep_code` -- Browse and search the project
- `run_command` -- Execute shell commands (Python, bash, etc.)
- `file_info` / `create_directory` / `delete_path` -- File system operations
- `git_status` / `git_diff` / `git_commit` / `git_log` / `git_checkout` -- Full Git workflow
- `install_package` / `list_packages` -- Python/npm package management
- `web_search` / `web_fetch` -- Search the web and fetch page content

### Browser Debugging Tools (10)
The IDE has a built-in **preview iframe** (bottom panel > "Preview" tab). You can:
- `browser_navigate` -- Navigate the preview iframe to any URL (same-origin pages allow full DOM inspection; cross-origin pages load if permitted but DOM access is blocked)
- `browser_evaluate` -- Execute JavaScript expressions in the page and get results (same-origin only)
- `browser_inspect` -- Get detailed info about a DOM element (same-origin only)
- `browser_query_all` -- List all elements matching a CSS selector with summary info (same-origin only)
- `browser_click` -- Simulate clicking an element (same-origin only)
- `browser_input` -- Simulate typing text into an input/textarea (same-origin only)
- `browser_console` -- Get captured console.log/warn/error output from the page (same-origin only)
- `browser_cookies` -- Read cookies of the preview page (same-origin only)
- `browser_page_info` -- Get page title, URL, viewport, and scroll position (same-origin only)
- `browser_open_external` -- Open a URL in the system/default browser (works for any URL including cross-origin)

**Browser Debugging Workflow:**
1. Use `browser_navigate` to open the target page (same-origin for debugging, cross-origin for preview)
2. If the page is cross-origin and you need full interaction, use `browser_open_external` instead
3. Use `browser_page_info` to verify the page loaded
4. For same-origin pages: use `browser_inspect`/`browser_query_all` to examine, `browser_click`/`browser_input` to interact
5. Use `browser_evaluate` for custom JS (e.g. get scroll position, check state)
6. Use `browser_console` to check for errors after interactions

### Python Runtime Debugging Tools (8)
You can debug Python code execution in real-time:
- `debug_start` -- Start a debugging session for a Python file
- `debug_stop` -- Stop the current debug session
- `debug_set_breakpoints` -- Set breakpoints (list of line numbers) for a file
- `debug_continue` -- Continue execution after a pause
- `debug_step` -- Step through code (step_in, step_over, step_out)
- `debug_inspect` -- Get current variable values and call stack
- `debug_evaluate` -- Evaluate a Python expression in the current frame
- `debug_stack` -- Get the current call stack

**Debugging Workflow:**
1. Use `debug_start` with the file path to begin debugging
2. Use `debug_set_breakpoints` to set breakpoints at specific lines
3. Use `debug_continue` to run until a breakpoint is hit
4. Use `debug_inspect` to see variables and call stack at the current line
5. Use `debug_evaluate` to evaluate expressions (e.g. check variable values)
6. Use `debug_step` with action "step_in"/"step_over"/"step_out" to step through code
7. Use `debug_stop` when done debugging

### Server & Environment Tool (1)
- `server_logs` -- Read IDE server logs to check for backend errors, startup issues, or runtime exceptions

## Testing & Debugging Workflow (CRITICAL)
**After every code modification, you MUST test and verify your changes work correctly.** This is not optional — it is a required part of your workflow.

### Step-by-Step Testing Process:
1. **Modify Code** -- Make your changes using `edit_file` or `write_file`
2. **Run/Execute** -- For Python files: use `run_command` to execute the file and check output for errors. For web apps: start the server if not running
3. **Check Backend Errors** -- Use `server_logs` to check if the IDE server has any errors related to your changes
4. **Frontend Verification (for web apps)** -- Use `browser_navigate` to load the page in the preview, then:
   - Use `browser_page_info` to verify the page loaded correctly
   - Use `browser_console` to check for JavaScript errors
   - Use `browser_inspect` / `browser_query_all` to verify UI elements exist and are correct
   - Use `browser_click` / `browser_input` to test interactive functionality
5. **Iterate** -- If errors are found, analyze them, fix the code, and re-test

### Error Handling Strategy:
- If `run_command` output shows a traceback/error → fix the code and re-run
- If `browser_console` shows JS errors → find and fix the frontend bug
- If `server_logs` shows server errors → investigate and fix the backend issue
- If `browser_page_info` returns an error or page fails to load → check server status, fix routing/code
- For complex bugs: use Python debugger (`debug_start` → `debug_set_breakpoints` → `debug_continue` → `debug_inspect`)

### What NOT to do:
- NEVER modify code and report it as done without testing
- NEVER assume your changes work without verification
- NEVER skip error checking after running commands

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
11. For browser tools, the preview must be on the "Preview" tab with a page loaded
12. Browser DOM tools (inspect, click, input, evaluate, etc.) only work with same-origin pages (localhost). Cross-origin pages will load visually but DOM access is blocked by the browser's security policy
13. Use `browser_open_external` to open any URL in the system browser when iframe access is not needed
14. **ALWAYS test your code changes** — run the code, check for errors, and verify the fix works before reporting completion
15. **Use `server_logs` after backend changes** to check for server-side errors
16. **Use browser tools after frontend changes** to verify the UI renders and functions correctly

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
    # ── Browser Debugging Tools ──
    {
        'type': 'function',
        'function': {
            'name': 'browser_navigate',
            'description': (
                'Navigate the built-in preview iframe to a URL. Supports any URL (http/https). '
                'Same-origin pages (localhost) allow full DOM inspection via other browser tools. '
                'Cross-origin pages will load if the server permits, but DOM access tools will fail. '
                'Returns success/error status. Use this first before other browser tools.'
            ),
            'parameters': {
                'type': 'object',
                'properties': {
                    'url': {
                        'type': 'string',
                        'description': 'URL to navigate to (e.g. "http://localhost:8080", "https://example.com")',
                    },
                },
                'required': ['url'],
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'browser_evaluate',
            'description': (
                'Execute a JavaScript expression in the preview page and return the result. '
                'Useful for getting page state, checking variables, or running custom queries. '
                'The expression runs in the page context with full DOM access.'
            ),
            'parameters': {
                'type': 'object',
                'properties': {
                    'expression': {
                        'type': 'string',
                        'description': 'JavaScript expression to evaluate (e.g. "document.title", "window.scrollY", "JSON.stringify(performance.timing)")',
                    },
                },
                'required': ['expression'],
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'browser_inspect',
            'description': (
                'Inspect a DOM element in the preview page. Returns detailed info: tag name, id, class, '
                'attributes, text content, computed styles, position/size, visibility status, child count. '
                'Use CSS selector to target elements (e.g. "#login-btn", ".nav-item", "form input[name=email]")'
            ),
            'parameters': {
                'type': 'object',
                'properties': {
                    'selector': {
                        'type': 'string',
                        'description': 'CSS selector of the element to inspect',
                    },
                },
                'required': ['selector'],
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'browser_query_all',
            'description': (
                'List all elements matching a CSS selector in the preview page. Returns up to 50 results '
                'with tag name, id, class, text preview, visibility, and position. '
                'Useful for discovering what elements exist on a page.'
            ),
            'parameters': {
                'type': 'object',
                'properties': {
                    'selector': {
                        'type': 'string',
                        'description': 'CSS selector (e.g. "button", ".card", "a[href]")',
                    },
                },
                'required': ['selector'],
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'browser_click',
            'description': (
                'Simulate a mouse click on an element in the preview page. '
                'Useful for testing button clicks, link navigation, form submissions, etc.'
            ),
            'parameters': {
                'type': 'object',
                'properties': {
                    'selector': {
                        'type': 'string',
                        'description': 'CSS selector of the element to click',
                    },
                },
                'required': ['selector'],
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'browser_input',
            'description': (
                'Simulate typing text into an input, textarea, or contenteditable element. '
                'Compatible with React/Vue (uses native value setter). '
                'Triggers input and change events.'
            ),
            'parameters': {
                'type': 'object',
                'properties': {
                    'selector': {
                        'type': 'string',
                        'description': 'CSS selector of the input/textarea element',
                    },
                    'text': {
                        'type': 'string',
                        'description': 'Text to type into the element',
                    },
                },
                'required': ['selector', 'text'],
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'browser_console',
            'description': (
                'Get captured console output (log, warn, error, info) from the preview page. '
                'The console interceptor is auto-injected when a page loads in the preview. '
                'Returns the last 100 log entries with timestamps and types.'
            ),
            'parameters': {
                'type': 'object',
                'properties': {},
                'required': [],
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'browser_cookies',
            'description': (
                'Read cookies from the preview page. Returns parsed cookie name-value pairs. '
                'Only works for same-origin pages. Returns empty if no cookies are set.'
            ),
            'parameters': {
                'type': 'object',
                'properties': {},
                'required': [],
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'browser_page_info',
            'description': (
                'Get basic information about the currently loaded page in the preview. '
                'Returns title, URL, character set, viewport size, scroll position, and body element count.'
            ),
            'parameters': {
                'type': 'object',
                'properties': {},
                'required': [],
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'browser_open_external',
            'description': (
                'Open a URL in the system/default browser. Use this for cross-origin pages '
                'that cannot be inspected in the preview iframe, or when you want the user to see '
                'a page in their actual browser. Works with any URL.'
            ),
            'parameters': {
                'type': 'object',
                'properties': {
                    'url': {
                        'type': 'string',
                        'description': 'URL to open in the system browser',
                    },
                },
                'required': ['url'],
            },
        },
    },
    # ── Python Runtime Debugging Tools ──
    {
        'type': 'function',
        'function': {
            'name': 'debug_start',
            'description': 'Start a debugging session for a Python file. Accepts both absolute paths and paths relative to the workspace (e.g. "myagent/main.py"). This begins tracing execution with sys.settrace().',
            'parameters': {
                'type': 'object',
                'properties': {
                    'file_path': {'type': 'string', 'description': 'Path to the Python file to debug (absolute or relative to workspace)'},
                    'breakpoints': {'type': 'array', 'items': {'type': 'integer'}, 'description': 'Optional list of line numbers to set as initial breakpoints', 'default': []},
                },
                'required': ['file_path'],
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'debug_stop',
            'description': 'Stop the current debug session.',
            'parameters': {'type': 'object', 'properties': {}, 'required': []},
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'debug_set_breakpoints',
            'description': 'Set breakpoints for a file. Replaces all existing breakpoints for that file.',
            'parameters': {
                'type': 'object',
                'properties': {
                    'file_path': {'type': 'string', 'description': 'Absolute path to the file'},
                    'lines': {'type': 'array', 'items': {'type': 'integer'}, 'description': 'List of line numbers to set as breakpoints'},
                },
                'required': ['file_path', 'lines'],
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'debug_continue',
            'description': 'Continue execution after a pause. Runs until the next breakpoint or program end.',
            'parameters': {'type': 'object', 'properties': {}, 'required': []},
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'debug_step',
            'description': 'Step through code. Actions: step_in (next line, enter functions), step_over (next line in same function), step_out (run until returning from current function).',
            'parameters': {
                'type': 'object',
                'properties': {
                    'action': {'type': 'string', 'enum': ['step_in', 'step_over', 'step_out'], 'description': 'Stepping action', 'default': 'step_in'},
                },
                'required': [],
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'debug_inspect',
            'description': 'Get current debug state including file, line number, function name, local variables, and call stack.',
            'parameters': {'type': 'object', 'properties': {}, 'required': []},
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'debug_evaluate',
            'description': 'Evaluate a Python expression in the current debug frame context. Returns the result as a string. Only works when paused.',
            'parameters': {
                'type': 'object',
                'properties': {
                    'expression': {'type': 'string', 'description': 'Python expression to evaluate (e.g. "len(data)", "x + y", "type(result)")'},
                },
                'required': ['expression'],
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'debug_stack',
            'description': 'Get the current call stack as a list of (filename, line, function_name) entries.',
            'parameters': {'type': 'object', 'properties': {}, 'required': []},
        },
    },
    # ── Server & Environment Tools ──
    {
        'type': 'function',
        'function': {
            'name': 'server_logs',
            'description': (
                'Read IDE server logs to check for backend errors, startup issues, request failures, '
                'or runtime exceptions. Returns the most recent log lines. Essential for debugging '
                'backend problems after code modifications.'
            ),
            'parameters': {
                'type': 'object',
                'properties': {
                    'count': {
                        'type': 'integer',
                        'description': 'Number of most recent log lines to return. Default: 50',
                        'default': 50,
                    },
                },
                'required': [],
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
    try:
        if create_dirs:
            parent = os.path.dirname(path)
            if parent:
                os.makedirs(parent, exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            f.write(content)
        return f'File written successfully: {path} ({os.path.getsize(path)} bytes)'
    except Exception as e:
        return f'Error writing file {path}: {e}'

def _tool_edit_file(args):
    path = _validate_path(args['path'])
    old_text = args['old_text']
    new_text = args['new_text']
    try:
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
    except Exception as e:
        return f'Error editing file {path}: {e}'

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
    skip_dirs = {'.git', '__pycache__', 'node_modules', '.venv', 'venv', '.idea', '.vscode', '.svn', 'bower_components', '.next', 'dist', 'build'}
    search_start = time.time()
    SEARCH_TIMEOUT = 30  # seconds
    for root, dirs, files in os.walk(search_path):
        if time.time() - search_start > SEARCH_TIMEOUT:
            results.append(f'[Search timed out after {SEARCH_TIMEOUT}s, showing {len(results)} of potentially more results]')
            break
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

def _get_effective_cwd():
    """Get the effective working directory for tool execution.
    When a project is open, returns the project directory.
    Otherwise returns WORKSPACE."""
    try:
        config = load_config()
        ws = config.get('workspace', WORKSPACE)
        project = config.get('project', None)
        if project:
            project_dir = os.path.join(ws, project)
            if os.path.isdir(project_dir):
                return project_dir
        return ws
    except Exception:
        return WORKSPACE


def _tool_run_command(args):
    command = args['command']
    timeout = args.get('timeout', 120)
    cwd = args.get('cwd', None) or _get_effective_cwd()
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
    repo_path = args.get('repo_path', None) or _get_effective_cwd()
    r = git_cmd('status --porcelain -b', cwd=repo_path)
    if not r['ok']:
        return f'Error: {r["stderr"]}'
    return r['stdout'] or 'Clean working tree (no changes)'

def _tool_git_diff(args):
    repo_path = args.get('repo_path', None) or _get_effective_cwd()
    staged = args.get('staged', False)
    file_path = args.get('file_path', '')
    cmd = 'diff --cached' if staged else 'diff'
    if file_path:
        cmd += f' -- {shlex_quote(file_path)}'
    r = git_cmd(cmd, cwd=repo_path)
    return r['stdout'] or 'No changes to display'

def _tool_git_commit(args):
    message = args['message']
    repo_path = args.get('repo_path', None) or _get_effective_cwd()
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
    repo_path = args.get('repo_path', None) or _get_effective_cwd()
    r = git_cmd(f'log --oneline -n {count}', cwd=repo_path)
    if not r['ok']:
        return f'Error: {r["stderr"]}'
    return r['stdout'] or 'No commits found'

def _tool_git_checkout(args):
    branch = args.get('branch', '')
    repo_path = args.get('repo_path', None) or _get_effective_cwd()
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

# ==================== Browser Debugging Tools ====================

def _format_browser_result(result):
    """Format a browser command result dict into a readable string."""
    if not isinstance(result, dict):
        return str(result) if result else '(no result)'
    if result.get('error'):
        # Downgrade timeout errors to warnings so the model keeps trying
        if 'timed out' in result['error']:
            return f"Warning: {result['error']} The preview panel may not be active. Try browser_page_info to check."
        return f"Error: {result['error']}"
    # Remove 'ok' key for cleaner output
    info = {k: v for k, v in result.items() if k != 'ok'}
    try:
        return json.dumps(info, indent=2, ensure_ascii=False)
    except Exception:
        return str(info)

def _tool_browser_navigate(args):
    url = args.get('url', '')
    if not url:
        return 'Error: URL is required'
    if not url.startswith('http://') and not url.startswith('https://'):
        url = 'http://' + url
    cmd_id = create_browser_command('navigate', {'url': url})
    result = wait_browser_result(cmd_id, timeout=30)
    # If timed out, it likely means the preview tab is not active — return a helpful message instead of error
    if isinstance(result, dict) and result.get('error') and 'timed out' in result.get('error', ''):
        return f'Warning: Preview panel may not be active. The page may still be navigating to: {url}\nUse browser_page_info to verify the page loaded.'
    return _format_browser_result(result)

def _tool_browser_evaluate(args):
    expression = args.get('expression', '')
    if not expression:
        return 'Error: expression is required'
    cmd_id = create_browser_command('evaluate', {'expression': expression})
    result = wait_browser_result(cmd_id, timeout=30)
    return _format_browser_result(result)

def _tool_browser_inspect(args):
    selector = args.get('selector', 'body')
    cmd_id = create_browser_command('inspect', {'selector': selector})
    result = wait_browser_result(cmd_id, timeout=30)
    return _format_browser_result(result)

def _tool_browser_query_all(args):
    selector = args.get('selector', '*')
    cmd_id = create_browser_command('query_all', {'selector': selector})
    result = wait_browser_result(cmd_id, timeout=30)
    return _format_browser_result(result)

def _tool_browser_click(args):
    selector = args.get('selector', '')
    if not selector:
        return 'Error: selector is required'
    cmd_id = create_browser_command('click', {'selector': selector})
    result = wait_browser_result(cmd_id, timeout=30)
    return _format_browser_result(result)

def _tool_browser_input(args):
    selector = args.get('selector', '')
    text = args.get('text', '')
    if not selector:
        return 'Error: selector is required'
    cmd_id = create_browser_command('input', {'selector': selector, 'text': text})
    result = wait_browser_result(cmd_id, timeout=30)
    return _format_browser_result(result)

def _tool_browser_console(args):
    cmd_id = create_browser_command('console', {})
    result = wait_browser_result(cmd_id, timeout=30)
    if not isinstance(result, dict):
        return str(result)
    if result.get('ok'):
        logs = result.get('logs', [])
        if not logs:
            return '(no console output captured yet - ensure the Bridge is injected)'
        lines = []
        for log in logs:
            lines.append(f"  [{log.get('type','log')}] {log.get('time','')}  {log.get('text','')}")
        return f"Console output ({result.get('count', len(logs))} entries):\n" + '\n'.join(lines[-100:])
    return _format_browser_result(result)

def _tool_browser_cookies(args):
    cmd_id = create_browser_command('cookies', {})
    result = wait_browser_result(cmd_id, timeout=30)
    if not isinstance(result, dict):
        return str(result)
    if result.get('ok'):
        cookies = result.get('cookies', [])
        raw = result.get('raw', '')
        if isinstance(cookies, list) and cookies:
            lines = [f"  {c.get('name','')}: {c.get('value','')}" for c in cookies]
            return f"Cookies ({len(cookies)} total):\n" + '\n'.join(lines)
        elif isinstance(cookies, str):
            return cookies
        return '(no cookies)'
    return _format_browser_result(result)

def _tool_browser_page_info(args):
    cmd_id = create_browser_command('page_info', {})
    result = wait_browser_result(cmd_id, timeout=30)
    return _format_browser_result(result)

def _tool_browser_open_external(args):
    import urllib.request as _urllib_req
    url = args.get('url', '')
    if not url:
        return 'Error: URL is required'
    if not url.startswith('http://') and not url.startswith('https://'):
        url = 'https://' + url
    try:
        result = _urllib_req.urlopen(
            _urllib_req.Request('http://localhost:' + str(os.environ.get('PORT', 1239)) + '/api/browser/open-external',
                               data=json.dumps({'url': url}).encode(),
                               headers={'Content-Type': 'application/json'},
                               method='POST'),
            timeout=10
        )
        data = json.loads(result.read())
        if data.get('ok'):
            return f'Opened in system browser: {url}'
        return f'Error: {data.get("error", "unknown")}'
    except Exception as e:
        return f'Error: {e}'

def _tool_debug_start(args):
    from routes.debug import get_session
    file_path = args.get('file_path', '')
    breakpoints = args.get('breakpoints', [])
    if not file_path:
        return 'Error: file_path is required'
    # Resolve relative paths against workspace
    if not os.path.isabs(file_path):
        resolved = os.path.join(WORKSPACE, file_path)
        if os.path.isfile(resolved):
            file_path = resolved
    if not os.path.isfile(file_path):
        return f'Error: File not found: {file_path}'
    session = get_session()
    if breakpoints:
        session.set_breakpoints(file_path, breakpoints)
    ok, msg = session.start(file_path)
    if not ok:
        return f'Error: {msg}'
    # Wait up to 10s for the session to reach paused/stopped/idle state
    for _ in range(50):
        time.sleep(0.2)
        state = session.get_state().get('state', '')
        if state in ('paused', 'stopped', 'idle'):
            break
    state = session.get_state()
    state_name = state.get('state', 'unknown')
    if state_name == 'paused':
        return f'{msg}\nPaused at {os.path.basename(state.get("file", "?"))}:{state.get("line", 0)} in {state.get("func", "?")}()'
    if state_name == 'stopped':
        return f'{msg}\nProgram finished. Check output for results.'
    return f'{msg}\nSession is {state_name}. Use debug_inspect to check status.'

def _tool_debug_stop(args):
    from routes.debug import get_session
    session = get_session()
    state = session.get_state()
    if state['state'] in ('idle', 'stopped'):
        return 'No active debug session to stop'
    session.stop()
    return 'Debug session stopped'

def _tool_debug_set_breakpoints(args):
    from routes.debug import get_session
    file_path = args.get('file_path', '')
    lines = args.get('lines', [])
    if not file_path:
        return 'Error: file_path is required'
    # Resolve relative paths against workspace
    if not os.path.isabs(file_path):
        resolved = os.path.join(WORKSPACE, file_path)
        if os.path.isfile(resolved):
            file_path = resolved
    session = get_session()
    session.set_breakpoints(file_path, lines)
    return f'Breakpoints set for {os.path.basename(file_path)}: lines {lines}'

def _tool_debug_continue(args):
    from routes.debug import get_session
    session = get_session()
    ok = session.resume()
    if not ok:
        return 'Error: Not paused'
    # Wait up to 30s for next pause/stop
    for _ in range(150):
        time.sleep(0.2)
        state = session.get_state().get('state', '')
        if state in ('paused', 'stopped', 'idle'):
            break
    state = session.get_state()
    state_name = state.get('state', 'running')
    if state_name == 'paused':
        return f'Paused at {os.path.basename(state.get("file", "?"))}:{state.get("line", 0)} in {state.get("func", "?")}()'
    if state_name == 'stopped':
        return 'Program finished. Check output for results.'
    return f'Still running after 30s. Use debug_inspect to check status.'

def _tool_debug_step(args):
    from routes.debug import get_session
    action = args.get('action', 'step_in')
    session = get_session()
    if action == 'step_over':
        ok = session.step_over()
    elif action == 'step_out':
        ok = session.step_out()
    else:
        ok = session.step_in()
    if not ok:
        return 'Error: Not paused'
    # Wait up to 10s for next pause
    for _ in range(50):
        time.sleep(0.2)
        state = session.get_state().get('state', '')
        if state in ('paused', 'stopped', 'idle'):
            break
    state = session.get_state()
    state_name = state.get('state', 'running')
    if state_name == 'paused':
        return f'Stepped ({action}). Now at {os.path.basename(state.get("file", "?"))}:{state.get("line", 0)} in {state.get("func", "?")}()'
    if state_name == 'stopped':
        return 'Program finished after step.'
    return f'Step ({action}) executed. Session is {state_name}.'

def _tool_debug_inspect(args):
    from routes.debug import get_session
    session = get_session()
    state = session.get_state()
    if state['state'] != 'paused':
        return f'Session is {state["state"]}, not paused. Use debug_continue or debug_step first.'
    result = f'File: {os.path.basename(state.get("file", "?"))}\n'
    result += f'Line: {state.get("line", 0)} in {state.get("func", "?")}()\n\n'
    result += 'Local Variables:\n'
    variables = state.get('local_vars', {})
    if variables:
        for name, value in variables.items():
            result += f'  {name} = {value}\n'
    else:
        result += '  (no variables)\n'
    result += f'\nCall Stack ({len(state.get("call_stack", []))} frames):\n'
    for i, entry in enumerate(reversed(state.get('call_stack', []))):
        fname = os.path.basename(entry[0]) if entry[0] else '?'
        result += f'  [{i}] {entry[2]}() at {fname}:{entry[1]}\n'
    return result

def _tool_debug_evaluate(args):
    from routes.debug import get_session
    expression = args.get('expression', '')
    if not expression:
        return 'Error: expression is required'
    session = get_session()
    result, error = session.evaluate(expression)
    if error:
        return f'Error: {error}'
    return str(result)

def _tool_debug_stack(args):
    from routes.debug import get_session
    session = get_session()
    state = session.get_state()
    stack = state.get('call_stack', [])
    if not stack:
        return 'Call stack is empty'
    result = f'Call Stack ({len(stack)} frames):\n'
    for i, entry in enumerate(reversed(stack)):
        fname = os.path.basename(entry[0]) if entry[0] else '?'
        result += f'  [{i}] {entry[2]}() at {fname}:{entry[1]}\n'
    return result

def _tool_server_logs(args):
    count = args.get('count', 50)
    try:
        import urllib.request as _urllib_req
        port = os.environ.get('PORT', '1239')
        req_data = json.dumps({'count': count}).encode()
        req = _urllib_req.Request(
            f'http://localhost:{port}/api/server/logs',
            data=req_data,
            headers={'Content-Type': 'application/json'},
            method='POST'
        )
        with _urllib_req.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode())
        lines = data.get('lines', [])
        source = data.get('source', 'unknown')
        total = data.get('total', 0)
        if not lines:
            return f'No server logs found (source: {source}, total in file: {total})'
        # Highlight error lines
        result_lines = []
        for line in lines:
            if 'ERROR' in line or 'Traceback' in line or 'Exception' in line:
                result_lines.append(f'  >> {line}')
            elif 'WARNING' in line or 'WARN' in line:
                result_lines.append(f'  !> {line}')
            else:
                result_lines.append(f'     {line}')
        header = f'Server logs (last {len(lines)} of {total} lines, source: {source}):'
        error_count = sum(1 for l in lines if 'ERROR' in l or 'Traceback' in l or 'Exception' in l)
        if error_count:
            header += f' [! {error_count} error(s) found]'
        return header + '\n' + '\n'.join(result_lines)
    except Exception as e:
        return f'Error reading server logs: {e}'

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
    'browser_navigate': _tool_browser_navigate,
    'browser_evaluate': _tool_browser_evaluate,
    'browser_inspect': _tool_browser_inspect,
    'browser_query_all': _tool_browser_query_all,
    'browser_click': _tool_browser_click,
    'browser_input': _tool_browser_input,
    'browser_console': _tool_browser_console,
    'browser_cookies': _tool_browser_cookies,
    'browser_page_info': _tool_browser_page_info,
    'browser_open_external': _tool_browser_open_external,
    'debug_start': _tool_debug_start,
    'debug_stop': _tool_debug_stop,
    'debug_set_breakpoints': _tool_debug_set_breakpoints,
    'debug_continue': _tool_debug_continue,
    'debug_step': _tool_debug_step,
    'debug_inspect': _tool_debug_inspect,
    'debug_evaluate': _tool_debug_evaluate,
    'debug_stack': _tool_debug_stack,
    'server_logs': _tool_server_logs,
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

# Global tool execution timeout (prevents any single tool from hanging the agent loop)
TOOL_EXECUTION_TIMEOUT = 120  # seconds

def execute_agent_tool_with_timeout(name, arguments):
    """Execute a tool with a global timeout to prevent hanging the agent loop."""
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(execute_agent_tool, name, arguments)
        try:
            return future.result(timeout=TOOL_EXECUTION_TIMEOUT)
        except concurrent.futures.TimeoutError:
            elapsed = time.time() - (executor._work_items and list(executor._work_items.keys())[0] or time.time())
            return False, f'Error: Tool "{name}" timed out after {TOOL_EXECUTION_TIMEOUT}s', TOOL_EXECUTION_TIMEOUT

# ==================== LLM Integration ====================
def _build_api_messages(messages, llm_config):
    """Convert chat history to API format with system prompt."""
    custom_prompt = llm_config.get('system_prompt', '').strip()
    if custom_prompt and custom_prompt != DEFAULT_SYSTEM_PROMPT.strip():
        # User has a custom system prompt — prepend the default tool documentation
        sys_prompt = DEFAULT_SYSTEM_PROMPT + '\n\n## Additional Instructions from User\n' + custom_prompt
    else:
        sys_prompt = DEFAULT_SYSTEM_PROMPT

    # Inject project-aware workspace info
    try:
        from utils import load_config
        config = load_config()
        project = config.get('project', None)
        ws = config.get('workspace', WORKSPACE)
        if project:
            project_dir = os.path.join(ws, project)
            if os.path.isdir(project_dir):
                workspace_info = f'Current project: {project}\nProject directory: {project_dir}\nWorkspace root: {ws}\nServer directory: {SERVER_DIR}'
            else:
                workspace_info = f'Current workspace: {ws}\nServer directory: {SERVER_DIR}'
        else:
            workspace_info = f'Current workspace: {ws}\nServer directory: {SERVER_DIR}'
    except Exception:
        workspace_info = f'Current workspace: {WORKSPACE}\nServer directory: {SERVER_DIR}'

    # Replace or append workspace info
    if 'Current workspace:' in sys_prompt or 'Current project:' in sys_prompt:
        # Remove old workspace lines
        import re as _re
        sys_prompt = _re.sub(r'Current (workspace|project):[^\n]*\n(Project directory|Workspace root|Server directory):[^\n]*\n?', '', sys_prompt)
        sys_prompt += f'\n\n{workspace_info}\n'
    else:
        sys_prompt += f'\n\n{workspace_info}\n'

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
        # Remove trailing slash to avoid 307 redirects from some providers (e.g. ModelScope)
        if api_base.endswith('/v1'):
            url = api_base.rstrip('/') + '/chat/completions'
        else:
            url = api_base.rstrip('/') + '/v1/chat/completions'
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

    with _urllib_opener.open(req, timeout=180) as resp:
        resp_body = resp.read().decode()
        try:
            result = json.loads(resp_body)
        except (json.JSONDecodeError, ValueError) as je:
            raise Exception(f'Invalid JSON response from LLM API: {str(je)}')
    return result

def _rewrite_for_reasoning_model(payload, api_messages):
    """Rewrite messages for OpenAI reasoning models which don't support system messages natively.
    Moves system prompt content into the first user message."""
    system_msgs = []
    other_msgs = []
    for m in api_messages:
        if m.get('role') == 'system':
            system_msgs.append(m.get('content', ''))
        else:
            other_msgs.append(m)

    if system_msgs:
        system_text = '\n\n'.join(system_msgs)
        if other_msgs and other_msgs[0].get('role') == 'user':
            other_msgs[0]['content'] = f"[System Instructions]\n{system_text}\n\n[User Message]\n{other_msgs[0].get('content', '')}"
        else:
            other_msgs.insert(0, {'role': 'user', 'content': f"[System Instructions]\n{system_text}"})

    payload['messages'] = other_msgs


def _call_llm_stream_raw(messages, llm_config):
    """Stream LLM response as raw SSE data chunks. Yields parsed delta objects."""
    import urllib.request

    model = llm_config.get('model', 'gpt-4o-mini')
    temperature = llm_config.get('temperature', 0.7)
    max_tokens = llm_config.get('max_tokens', 4096)
    reasoning = llm_config.get('reasoning', True)

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

    # Add reasoning/thinking support for providers that support it
    if reasoning:
        provider = llm_config.get('provider', '')
        api_type = llm_config.get('api_type', '')
        model_lower = model.lower()

        # OpenAI reasoning models (o1, o3, o4-mini, etc.)
        if ('o1' in model_lower or 'o3' in model_lower or 'o4' in model_lower or
            'reasoning' in model_lower or 'codex' in model_lower):
            payload['reasoning_effort'] = 'high'
            # OpenAI reasoning models don't support temperature and system messages in the usual way
            payload.pop('temperature', None)
            # Move system messages to the first user message for reasoning models
            _rewrite_for_reasoning_model(payload, api_messages)

        # Anthropic extended thinking (Claude 3.5+)
        elif provider == 'anthropic' or api_type == 'anthropic':
            if 'claude-3-5' in model_lower or 'claude-sonnet-4' in model_lower or 'claude-opus-4' in model_lower:
                payload['thinking'] = {
                    'type': 'enabled',
                    'budget_tokens': min(max_tokens * 4, 10000),
                }
                # Claude thinking requires temperature=1
                payload['temperature'] = 1

        # DeepSeek reasoning models
        elif 'deepseek' in model_lower or 'reasoner' in model_lower:
            payload.setdefault('temperature', 0.6)

        # QwQ / other reasoning models
        elif 'qwq' in model_lower or 'think' in model_lower:
            pass  # These models reason by default, no special params needed

    try:
        url, headers = _get_llm_endpoint(llm_config, model)
    except Exception as e:
        raise Exception(f'LLM config error: {e}')

    headers = headers or {'Content-Type': 'application/json'}

    req = urllib.request.Request(url, json.dumps(payload).encode(), headers=headers, method='POST')
    print(f'[LLM] Calling: {url}')
    print(f'[LLM] Model: {model}, Temperature: {temperature}, MaxTokens: {max_tokens}, Reasoning: {reasoning}')
    print(f'[LLM] Headers: {dict((k, v[:20]+"..." if len(v)>20 else v) for k,v in headers.items())}')
    print(f'[LLM] Messages count: {len(api_messages)}')

    with _urllib_opener.open(req, timeout=300) as resp:
        byte_buffer = b''
        accumulated_finish_reason = None
        while True:
            chunk = resp.read(4096)
            if not chunk:
                break
            byte_buffer += chunk
            while b'\n' in byte_buffer:
                line_bytes, byte_buffer = byte_buffer.split(b'\n', 1)
                try:
                    line = line_bytes.decode('utf-8').strip()
                except UnicodeDecodeError:
                    continue
                if not line.startswith('data: '):
                    continue
                data_str = line[6:]
                if data_str == '[DONE]':
                    return
                try:
                    data = json.loads(data_str)
                    choices = data.get('choices', [])
                    if choices:
                        delta = choices[0].get('delta', {})
                        fr = choices[0].get('finish_reason')
                        if fr:
                            delta['_finish_reason'] = fr
                            accumulated_finish_reason = fr
                        yield delta
                except json.JSONDecodeError:
                    continue
        # Process any remaining partial data in buffer
        if byte_buffer.strip():
            line = byte_buffer.decode('utf-8', errors='replace').strip()
            if line.startswith('data: ') and line[6:] != '[DONE]':
                try:
                    data = json.loads(line[6:])
                    choices = data.get('choices', [])
                    if choices:
                        delta = choices[0].get('delta', {})
                        fr = choices[0].get('finish_reason')
                        if fr:
                            delta['_finish_reason'] = fr
                        yield delta
                except (json.JSONDecodeError, KeyError):
                    pass

# ==================== Context Window Management ====================
def _estimate_tokens(text):
    """Rough token estimation: ~4 characters per token."""
    return len(text) // 4

def _has_tool_calls(msg):
    """Check if a message has tool calls."""
    return bool(msg and msg.get('tool_calls'))

def _compress_context(messages, max_tokens=None):
    """Compress conversation history to fit within context window.
    Keeps: system prompt, last 2 user messages, last 4 tool results, summary of older messages.
    
    Returns (messages, was_compressed) tuple.
    """
    if not messages:
        return messages, False
    max_tokens = max_tokens or 60000
    total = sum(_estimate_tokens(m.get('content', '') or '') for m in messages)
    if total <= max_tokens:
        return messages, False

    was_compressed = True

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
        content = msg.get('content') or ''
        if role == 'user':
            summary_parts.append(f'[User]: {content[:200]}')
        elif role == 'assistant':
            summary_parts.append(f'[Assistant]: {content[:200]}')
        elif role == 'tool':
            name = msg.get('name', 'tool')
            summary_parts.append(f'[Tool {name}]: {_truncate(content, 100)}')

    summary = 'Earlier conversation summary:\n' + '\n'.join(summary_parts[-10:])
    summary_msg = {'role': 'user', 'content': summary}

    # Compress recent tool results if still too large
    compressed_recent = []
    for msg in recent:
        if msg.get('role') == 'tool':
            content = msg.get('content') or ''
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

    # Final check: if STILL too large, do aggressive compression
    total3 = sum(_estimate_tokens(m.get('content', '') or '') for m in all_msgs)
    if total3 > max_tokens:
        # Only keep the very last user message, assistant response, and summary
        # This is a drastic measure to ensure the request fits
        user_indices2 = [i for i, m in enumerate(all_msgs) if m.get('role') == 'user']
        if len(user_indices2) >= 1:
            keep_from = user_indices2[-1]
            kept = all_msgs[keep_from:]
            # But also aggressively trim tool results in kept messages
            for msg in kept:
                if msg.get('role') == 'tool':
                    content = msg.get('content', '')
                    if len(content) > 500:
                        msg['content'] = content[:500] + '\n[truncated]'
            all_msgs = [summary_msg] + kept

    # ULTIMATE fallback: if STILL too large, trim everything to bare minimum
    total4 = sum(_estimate_tokens(m.get('content', '') or '') for m in all_msgs)
    if total4 > max_tokens:
        # Keep only system prompt + last 2 messages, aggressively trimmed
        minimal = [summary_msg]
        for msg in all_msgs[-2:]:
            content = msg.get('content', '') or ''
            minimal.append(dict(msg, content=content[:200] + ('...' if len(content) > 200 else '')))
        all_msgs = minimal

    return all_msgs, was_compressed

# ==================== Agent Loop ====================
MAX_AGENT_ITERATIONS = 100  # Increased from 15 for complex tasks
MAX_ITERATION_RETRIES = 10

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
    context, _ = _compress_context(history, max_tokens=llm_config.get('max_tokens', 4096) * 10)

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

            ok, result_str, elapsed = execute_agent_tool_with_timeout(tool_name, tool_args)

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
            context, _ = _compress_context(context, max_tokens=llm_config.get('max_tokens', 4096) * 10)

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

def run_agent_loop_stream(user_message, llm_config, conv_id=None, is_retry=False):
    """Generator that runs the agent loop and yields SSE events.
    
    Args:
        user_message: The user's message text.
        llm_config: LLM configuration dict.
        conv_id: Optional conversation ID for persistence.
        is_retry: If True, this is a retry of a failed turn. The conversation
                  history already contains the user message and partial progress,
                  so we don't add the user message again.
    """
    # Load history from conversation if conv_id provided, otherwise from legacy chat_history
    if conv_id:
        conv = get_conversation(conv_id)
        history = list(conv.get('messages', [])) if conv else []
    else:
        history = load_chat_history()

    # Only add user message if this is NOT a retry
    # On retry, the history already has the user message from the failed run
    if not is_retry:
        user_msg = {'role': 'user', 'content': user_message, 'time': datetime.now().isoformat()}
        history.append(user_msg)

    context, _ = _compress_context(history, max_tokens=llm_config.get('max_tokens', 4096) * 10)

    # Pre-save history before starting the loop so retry can recover even if
    # the very first LLM call fails (before any tool execution).
    save_chat_history(history)
    if conv_id:
        save_conversation(conv_id, history)

    final_content = ''
    total_iterations = 0
    accumulated_text = ''
    tool_calls_in_progress = []
    loop_completed_normally = False
    # Buffer for streaming tool_calls assembly
    current_tool_calls = []
    current_tool_call_idx = {}
    current_args_buffer = {}

    for iteration in range(MAX_AGENT_ITERATIONS):
        total_iterations = iteration + 1
        yield f"data: {json.dumps({'type': 'thinking', 'content': f'Iteration {iteration + 1}: Calling LLM...'})}\n\n"

        # Call LLM with streaming
        response_message = None
        finish_reason = None
        retry = 0
        context_retries = 0
        MAX_CONTEXT_RETRIES = 20
        while retry < MAX_ITERATION_RETRIES:
            try:
                current_tool_calls = []
                current_args_buffer = {}
                current_tool_call_idx = {}
                delta_content = ''
                delta_tool_calls = []
                saved_accumulated = accumulated_text  # save for rollback on failure
                # Pre-compute LLM URL for error reporting
                try:
                    current_llm_url, _ = _get_llm_endpoint(llm_config, llm_config.get('model', 'gpt-4o-mini'))
                except Exception:
                    current_llm_url = '(unknown)'

                finish_reason = None
                for delta in _call_llm_stream_raw(context, llm_config):
                    # Capture finish_reason
                    fr = delta.get('_finish_reason')
                    if fr:
                        finish_reason = fr

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
                # Filter out empty/invalid tool calls (some models send blanks)
                valid_tool_calls = [tc for tc in current_tool_calls
                                    if tc.get('function', {}).get('name', '').strip()]
                if valid_tool_calls:
                    response_message['tool_calls'] = valid_tool_calls
                break  # success — exit retry loop

            except urllib.error.HTTPError as e:
                accumulated_text = saved_accumulated  # rollback on failure
                body = e.read().decode() if hasattr(e, 'read') else ''
                err_detail = f'URL: {current_llm_url}\nHTTP {e.code}: {body[:300]}'
                
                # Detect context length exceeded errors and aggressively compress context
                is_context_error = (
                    e.code in (400, 413, 422) and
                    any(kw in body.lower() for kw in [
                        'context', 'token', 'max_length', 'maximum context',
                        'too many tokens', 'input too large', 'prompt is too long',
                        'request too large', 'exceeds the model', 'context_length',
                        'max_tokens', 'too long', '超出', '上下文',
                    ])
                )
                
                if is_context_error:
                    context_retries += 1
                    if context_retries >= MAX_CONTEXT_RETRIES:
                        yield f"data: {json.dumps({'type': 'error', 'content': f'Context still too large after {MAX_CONTEXT_RETRIES} compression attempts. Please start a new conversation.'})}\n\n"
                        yield f"data: {json.dumps({'type': 'done', 'completed': False, 'iterations': total_iterations})}\n\n"
                        return
                    # Aggressively compress context and retry (don't consume normal retry counter)
                    budget = llm_config.get('max_tokens', 4096) * 10
                    context, was_compressed = _compress_context(context, max_tokens=max(budget // 2, 4000))
                    yield f"data: {json.dumps({'type': 'thinking', 'content': f'Context too large, compressing and retrying ({context_retries}/{MAX_CONTEXT_RETRIES})...'})}\n\n"
                    print(f'[LLM] Context overflow detected (HTTP {e.code}), compressed to {sum(_estimate_tokens(m.get("content","") or "") for m in context)} tokens (budget: {budget // 2})')
                    time.sleep(0.5)
                    continue
                
                retry += 1
                if retry < MAX_ITERATION_RETRIES:
                    yield f"data: {json.dumps({'type': 'thinking', 'content': f'LLM API error (retry {retry}): {err_detail[:200]}'})}\n\n"
                    time.sleep(1 * retry)
                else:
                    yield f"data: {json.dumps({'type': 'error', 'content': f'LLM API error after {MAX_ITERATION_RETRIES} retries:\n{err_detail}'})}\n\n"
                    yield f"data: {json.dumps({'type': 'done', 'completed': False, 'iterations': total_iterations})}\n\n"
                    return
            except Exception as e:
                accumulated_text = saved_accumulated  # rollback on failure
                err_detail = f'URL: {current_llm_url}\nError: {str(e)}'
                
                # Also check for context errors in generic exceptions
                err_lower = str(e).lower()
                is_context_error = any(kw in err_lower for kw in [
                    'context', 'token', 'max_length', 'too many tokens',
                    'prompt is too long', 'too long',
                ])
                
                if is_context_error:
                    context_retries += 1
                    if context_retries >= MAX_CONTEXT_RETRIES:
                        yield f"data: {json.dumps({'type': 'error', 'content': f'Context still too large after {MAX_CONTEXT_RETRIES} compression attempts.'})}\n\n"
                        yield f"data: {json.dumps({'type': 'done', 'completed': False, 'iterations': total_iterations})}\n\n"
                        return
                    budget = llm_config.get('max_tokens', 4096) * 10
                    context, was_compressed = _compress_context(context, max_tokens=max(budget // 2, 4000))
                    yield f"data: {json.dumps({'type': 'thinking', 'content': f'Context error, compressing and retrying ({context_retries}/{MAX_CONTEXT_RETRIES})...'})}\n\n"
                    print(f'[LLM] Context overflow exception, compressed and retrying')
                    time.sleep(0.5)
                    continue
                
                retry += 1
                if retry < MAX_ITERATION_RETRIES:
                    yield f"data: {json.dumps({'type': 'thinking', 'content': f'Retry {retry}: {err_detail[:200]}'})}\n\n"
                    time.sleep(1 * retry)
                else:
                    yield f"data: {json.dumps({'type': 'error', 'content': f'LLM request failed after {MAX_ITERATION_RETRIES} retries:\n{err_detail}'})}\n\n"
                    yield f"data: {json.dumps({'type': 'done', 'completed': False, 'iterations': total_iterations})}\n\n"
                    return

        if response_message is None:
            # All retries exhausted without success
            yield f"data: {json.dumps({'type': 'error', 'content': 'All retries failed: LLM did not return a valid response.'})}\n\n"
            yield f"data: {json.dumps({'type': 'done', 'completed': False, 'iterations': total_iterations})}\n\n"
            return

        # Warn if model hit max_tokens (finish_reason == 'length')
        if finish_reason == 'length' and not _has_tool_calls(response_message):
            yield f"data: {json.dumps({'type': 'warning', 'content': 'Response was truncated (max_tokens reached). Consider increasing Max Tokens in settings for longer responses.'})}\n\n"

        content = response_message.get('content', '') or ''
        tool_calls_raw = response_message.get('tool_calls', [])

        # Handle completely empty response (no content, no tool calls)
        if not content.strip() and not tool_calls_raw:
            # Try auto-retry for empty responses (up to 3 times)
            empty_retries = getattr(run_agent_loop_stream, '_empty_retry', 0) + 1
            run_agent_loop_stream._empty_retry = empty_retries
            if empty_retries <= 3:
                yield f"data: {json.dumps({'type': 'thinking', 'content': f'Model returned empty response, auto-retrying ({empty_retries}/3)...'})}\n\n"
                print(f'[LLM] Empty response detected (iteration {total_iterations}), retrying ({empty_retries}/3)')
                time.sleep(1)
                continue  # retry the same iteration
            else:
                run_agent_loop_stream._empty_retry = 0
                yield f"data: {json.dumps({'type': 'error', 'content': 'Model returned empty response 3 times in a row. This may be caused by: 1) max_tokens too low for reasoning models, 2) API issue, 3) Model overloaded. Please try again or adjust settings.'})}\n\n"
                yield f"data: {json.dumps({'type': 'done', 'completed': False, 'iterations': total_iterations})}\n\n"
                return
        # Reset empty retry counter on successful response
        run_agent_loop_stream._empty_retry = 0

        if content:
            final_content = accumulated_text.strip() if accumulated_text.strip() else content

        # If no tool calls, we're done
        if not tool_calls_raw:
            # Don't save empty assistant message to history
            if not final_content.strip():
                yield f"data: {json.dumps({'type': 'error', 'content': 'Model returned an empty final response.'})}\n\n"
                yield f"data: {json.dumps({'type': 'done', 'completed': False, 'iterations': total_iterations})}\n\n"
                return
            loop_completed_normally = True
            break

        # Add assistant message to context AND history for progressive save
        assistant_msg = {
            'role': 'assistant',
            'content': content or None,
            'tool_calls': tool_calls_raw,
            'time': datetime.now().isoformat(),
        }
        context.append(assistant_msg)
        history.append(assistant_msg)

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

            ok, result_str, elapsed = execute_agent_tool_with_timeout(tool_name, tool_args)

            yield f"data: {json.dumps({'type': 'tool_result', 'tool': tool_name, 'ok': ok, 'result': _truncate(result_str, 30000), 'elapsed': round(elapsed, 2), 'max_iterations': MAX_AGENT_ITERATIONS})}\n\n"

            tool_msg = {
                'role': 'tool',
                'tool_call_id': tool_call_id,
                'name': tool_name,
                'content': result_str,
                'time': datetime.now().isoformat(),
            }
            context.append(tool_msg)
            history.append(tool_msg)

            # Save after each tool so refresh mid-iteration preserves partial progress
            save_chat_history(history)
            if conv_id:
                save_conversation(conv_id, history)

            # Compress context if needed
            context, _ = _compress_context(context, max_tokens=llm_config.get('max_tokens', 4096) * 10)

        # Progressive save: persist history after each iteration so retry can resume
        save_chat_history(history)
        if conv_id:
            save_conversation(conv_id, history)

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

    if not loop_completed_normally:
        yield f"data: {json.dumps({'type': 'warning', 'content': f'Agent loop reached max iterations ({MAX_AGENT_ITERATIONS}). Task may be incomplete.'})}\n\n"

    yield f"data: {json.dumps({'type': 'done', 'iterations': total_iterations, 'tool_calls': len(tool_calls_in_progress), 'completed': loop_completed_normally})}\n\n"

# ==================== Chat Endpoints ====================
@bp.route('/api/chat/history', methods=['GET'])
def get_chat_history():
    history = load_chat_history()
    # Also return the most recent conv_id so the frontend can resume the conversation
    convs = load_conversations()
    latest_conv_id = convs[0]['id'] if convs else None
    return jsonify({'messages': history, 'conv_id': latest_conv_id})

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

    # Allow frontend to specify which model to use by index
    model_index = data.get('model_index')
    if model_index is not None:
        all_config = load_llm_config()
        models = all_config.get('models', [])
        idx = int(model_index)
        if 0 <= idx < len(models):
            llm_config = dict(models[idx])
            llm_config['system_prompt'] = llm_config.get('system_prompt') or all_config.get('system_prompt', '')
        else:
            return jsonify({'error': f'Invalid model index: {idx}'}), 400
    else:
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
    """SSE streaming agent endpoint. Runs agent in background thread, broadcasts events."""
    data = request.json
    message = data.get('message', '').strip()
    conv_id = data.get('conv_id')  # optional conversation id
    is_retry = data.get('retry', False)  # if True, continue from failed state instead of restart
    if not message and not is_retry:
        return jsonify({'error': 'Message required'}), 400

    # Allow frontend to specify which model to use by index
    model_index = data.get('model_index')
    if model_index is not None:
        all_config = load_llm_config()
        models = all_config.get('models', [])
        idx = int(model_index)
        if 0 <= idx < len(models):
            llm_config = dict(models[idx])
            llm_config['system_prompt'] = llm_config.get('system_prompt') or all_config.get('system_prompt', '')
        else:
            return jsonify({'error': f'Invalid model index: {idx}'}), 400
    else:
        llm_config = get_active_llm_config()

    print(f'[CHAT] send_chat_stream called')
    print(f'[CHAT] LLM config: name={llm_config.get("name")}, api_type={llm_config.get("api_type")}, model={llm_config.get("model")}, api_base={llm_config.get("api_base")}, api_key={"***"+llm_config.get("api_key","")[-6:] if llm_config.get("api_key") else "EMPTY"}')

    # Set up the global active task state
    with _active_task['lock']:
        if _active_task['running']:
            return jsonify({'error': 'A task is already running'}), 409

        event_queue = queue.Queue()
        event_buffer = deque(maxlen=RING_BUFFER_SIZE)

        _active_task['running'] = True
        _active_task['conv_id'] = conv_id
        _active_task['message'] = message
        _active_task['model_index'] = model_index
        _active_task['started_at'] = time.time()
        _active_task['event_queue'] = event_queue
        _active_task['event_buffer'] = event_buffer
        _active_task['subscribers'] = 1

    def _run_agent():
        """Background thread: runs the agent loop and puts events into queue + buffer."""
        try:
            for sse_event in run_agent_loop_stream(message, llm_config, conv_id=conv_id, is_retry=is_retry):
                event_queue.put(sse_event)
                with _active_task['lock']:
                    _active_task['event_buffer'].append(sse_event)
        except Exception as e:
            err_event = f"data: {json.dumps({'type': 'error', 'content': str(e)})}\n\n"
            event_queue.put(err_event)
            with _active_task['lock']:
                _active_task['event_buffer'].append(err_event)
            done_event = f"data: {json.dumps({'type': 'done', 'completed': False, 'error': True})}\n\n"
            event_queue.put(done_event)
            with _active_task['lock']:
                _active_task['event_buffer'].append(done_event)
        finally:
            # Signal completion
            event_queue.put(None)

    # Start the agent in a background thread
    agent_thread = threading.Thread(target=_run_agent, daemon=True)
    agent_thread.start()
    with _active_task['lock']:
        _active_task['thread'] = agent_thread

    def generate():
        """Read from the shared queue and yield SSE events to this client."""
        q = None
        try:
            with _active_task['lock']:
                q = _active_task['event_queue']
            while True:
                try:
                    event = q.get(timeout=30)
                except queue.Empty:
                    yield "data: {\"type\":\"keepalive\"}\n\n"
                    continue
                if event is None:  # sentinel = done
                    break
                yield event
        except GeneratorExit:
            pass
        finally:
            with _active_task['lock']:
                _active_task['subscribers'] -= 1
                if _active_task['subscribers'] <= 0:
                    _active_task['running'] = False
                    _active_task['conv_id'] = None
                    _active_task['message'] = None
                    _active_task['started_at'] = None
                    _active_task['event_queue'] = None
                    _active_task['event_buffer'] = None
                    _active_task['thread'] = None

    return Response(generate(), mimetype='text/event-stream',
                    headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})


@bp.route('/api/chat/task/status', methods=['GET'])
def get_task_status():
    """Check if there is an active running task."""
    with _active_task['lock']:
        if _active_task['running']:
            return jsonify({
                'running': True,
                'conv_id': _active_task['conv_id'],
                'started_at': _active_task['started_at'],
                'elapsed': time.time() - _active_task['started_at'] if _active_task['started_at'] else 0,
            })
        return jsonify({'running': False})


@bp.route('/api/chat/task/stream', methods=['GET'])
def task_reconnect_stream():
    """Reconnect to a running task. First sends buffered events, then subscribes to live events."""
    with _active_task['lock']:
        if not _active_task['running']:
            return jsonify({'error': 'No active task'}), 404

        q = _active_task['event_queue']
        # Snapshot the ring buffer for catch-up
        buffered = list(_active_task['event_buffer'])
        _active_task['subscribers'] += 1

    def generate():
        try:
            # First replay all buffered (historical) events
            for event in buffered:
                yield event

            # Then subscribe to live events
            while True:
                try:
                    event = q.get(timeout=30)
                except queue.Empty:
                    yield "data: {\"type\":\"keepalive\"}\n\n"
                    continue
                if event is None:  # sentinel = done
                    break
                # Only yield if it's not already in the buffer (i.e., it's a new event)
                yield event
        except GeneratorExit:
            pass
        finally:
            with _active_task['lock']:
                _active_task['subscribers'] -= 1
                if _active_task['subscribers'] <= 0 and not _active_task['running']:
                    # Clean up if no more subscribers and task ended
                    _active_task['event_queue'] = None
                    _active_task['event_buffer'] = None

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
        with _urllib_opener.open(req, timeout=60) as resp:
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
