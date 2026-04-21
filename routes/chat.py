"""
PhoneIDE - LLM Chat + AI Agent routes.
"""

import os
import json
import re
import time
import platform
import shutil
import tempfile
import subprocess
import hashlib
from routes.ast_index import (extract_definitions, find_references_ast, get_file_structure,
                               project_index)
import fnmatch
import threading
import queue
import glob as _glob
import concurrent.futures
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
    get_system_info, IS_WINDOWS, get_default_shell,
    log_write,
)
from routes.git import git_cmd
from routes.browser import create_browser_command, wait_browser_result

bp = Blueprint('chat', __name__)

# ==================== Global Active Task State ====================
_active_task = {
    'running': False,
    'cancelled': False,       # True when user requests cancellation
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

# ==================== System Prompt ====================
# Build system environment info for the system prompt
_SYSTEM_ENV_INFO = get_system_info()
_PLATFORM_NAME = 'Windows' if IS_WINDOWS else ('macOS' if platform.system() == 'Darwin' else 'Linux')
_DEFAULT_COMPILER = 'python' if IS_WINDOWS else 'python3'
_SERVER_DIR = SERVER_DIR

RING_BUFFER_SIZE = 100

DEFAULT_SYSTEM_PROMPT = f"""You are PhoneIDE AI Agent, a powerful coding assistant integrated in a mobile IDE.
You have access to tools that let you read/write files, execute code, search projects, manage git, and more.

## Available Tools
You have **33 tools** available. When you need to perform an action, call the appropriate tool using function calling.
For multi-step tasks, think step by step and use tools in sequence.

### File & Code Tools (25)
- `read_file` / `write_file` / `edit_file` -- Read, create, or modify files
- `list_directory` / `search_files` / `grep_code` / `glob_files` -- Browse and search the project
- `run_command` -- Execute shell commands (Python, bash, etc.)
- `file_info` / `create_directory` / `delete_path` / `move_file` -- File system operations
- `append_file` -- Append content to an existing file
- `file_structure` -- Get a tree-structured overview of a directory
- `find_definition` / `find_references` -- Jump to symbol definition or find all usages (AST-based)
- `git_status` / `git_diff` / `git_commit` / `git_log` / `git_checkout` -- Full Git workflow
- `install_package` / `list_packages` -- Python/npm package management
- `web_search` / `web_fetch` -- Search the web and fetch page content

### Task Planning & Tracking (2)
- `todo_write` -- Create or update a task plan (use BEFORE complex multi-step tasks)
- `todo_read` -- Read current todo list to check progress

### Sub-Agent Tools (2)
- `delegate_task` -- Launch a sub-agent for independent subtasks (supports "read" and "write" modes)
- `parallel_tasks` -- Launch 2-4 sub-agents simultaneously for independent parallel work

### Preview & Debugging Tools (4)
The IDE has a built-in **preview iframe** (bottom panel > "Preview" tab). You can:
- `browser_navigate` -- Navigate the preview iframe to a URL
- `browser_page_info` -- Get page title, URL, viewport info
- `browser_console` -- Get captured console.log/warn/error output
- `server_logs` -- Read IDE server logs to check for backend errors

**Preview Workflow:**
1. Use `browser_navigate` to open a page in the preview
2. Use `browser_page_info` to verify the page loaded
3. Use `browser_console` to check for JavaScript errors
4. Use `server_logs` to check for backend errors

## Task Planning Workflow (MANDATORY — CRITICAL)
**You MUST use `todo_write` to plan before starting ANY task with 3 or more steps.** This is not optional.

### When to use todo_write:
1. **User gives a complex request** (e.g., "implement feature X", "fix this bug", "refactor module Y") — ALWAYS create a todo list first
2. **You identify a multi-step approach** — Break it into specific, actionable todo items before executing
3. **During execution** — Update status: mark items `in_progress` when working, `completed` when done
4. **After completing all items** — Update the final status so the user sees real-time progress

### How to write good todos:
- Each item should be a single, specific, actionable step (not vague goals)
- Use `id` like "1", "2", "3" for ordering
- Set `priority`: `high` for critical path, `medium` for important, `low` for nice-to-have
- Example: `["id":"1", "content":"Read auth.py to understand current login flow", "status":"in_progress", "priority":"high"], ["id":"2", "content":"Add JWT token validation middleware", "status":"pending", "priority":"high"]`

### What NOT to do:
- NEVER start coding without first creating a todo plan for complex tasks
- NEVER mark everything as completed without actually doing the work

## Sub-Agent Delegation Workflow (IMPORTANT)
**Use `delegate_task` and `parallel_tasks` to handle complex, multi-step subtasks.** Sub-agents have their own tool loop and can work independently.

### When to use delegate_task:
1. **Search & analysis tasks** — Send a sub-agent to explore code, search patterns, analyze architecture
2. **Write tasks** — Use `mode: "write"` when the sub-agent needs to modify files (with its own tool loop)
3. **Research tasks** — Have a sub-agent search the web, read documentation, gather information

### When to use parallel_tasks:
1. **Multiple independent files to modify** — Run 2-4 sub-agents in parallel, each working on different files
2. **Simultaneous research + implementation** — One sub-agent researches while another implements
3. **Cross-module changes** — Different sub-agents handle different components concurrently

### Sub-Agent Best Practices:
- Give a CLEAR, detailed task description (sub-agents don't see your full context)
- Use "read" mode by default; only use "write" mode when the sub-agent must modify files
- NEVER have parallel sub-agents modify the same files (they run concurrently and will conflict)
- For write mode: specify exactly which files to create/modify and what changes to make

## Testing & Debugging Workflow (CRITICAL)
**After every code modification, you MUST test and verify your changes work correctly.** This is not optional — it is a required part of your workflow.

### Step-by-Step Testing Process:
1. **Modify Code** -- Make your changes using `edit_file` or `write_file`
2. **Run/Execute** -- For Python files: use `run_command` to execute the file and check output for errors. For web apps: start the server if not running
3. **Check Backend Errors** -- Use `server_logs` to check if the IDE server has any errors related to your changes
4. **Frontend Verification (for web apps)** -- Use `browser_navigate` to load the page in the preview, then:
   - Use `browser_page_info` to verify the page loaded correctly
   - Use `browser_console` to check for JavaScript errors
5. **Iterate** -- If errors are found, analyze them, fix the code, and re-test

### Error Handling Strategy:
- If `run_command` output shows a traceback/error → fix the code and re-run
- If `browser_console` shows JS errors → find and fix the frontend bug
- If `server_logs` shows server errors → investigate and fix the backend issue
- If `browser_page_info` returns an error or page fails to load → check server status, fix routing/code
- For complex bugs: add print/logging statements and re-run to narrow down the issue

### What NOT to do:
- NEVER modify code and report it as done without testing
- NEVER assume your changes work without verification
- NEVER skip error checking after running commands

## Important Rules
1. **ALWAYS use `todo_write` BEFORE starting any complex task (3+ steps)** — plan first, then execute
2. **Update todo status in real-time** — mark items in_progress when starting, completed when done
3. Always use absolute paths when referencing files
4. Before writing a file, read it first to understand existing content
5. When modifying code, use edit_file for targeted changes instead of rewriting entire files
6. After executing commands, check the output for errors before proceeding
7. For large files, use offset_line and limit_lines to read specific sections
8. When searching, use specific patterns rather than broad terms
9. If a tool fails, analyze the error and try a different approach
10. Always explain what you're doing and why before taking action
11. Respect the workspace boundary - all file operations are scoped to the workspace
12. When running shell commands, be cautious with destructive operations
13. For browser tools, the preview must be on the "Preview" tab with a page loaded
14. **ALWAYS test your code changes** — run the code, check for errors, and verify the fix works before reporting completion
15. **Use `server_logs` after backend changes** to check for server-side errors
16. **Use browser tools after frontend changes** to verify the UI renders and functions correctly
17. **Use `delegate_task` for complex subtasks** — don't try to do everything in one conversation turn
18. **Use `parallel_tasks` when 2+ subtasks are independent** — save time by running them concurrently

## Important: Platform Awareness
- Use the system environment info below (injected dynamically) to choose correct shell commands and paths.
- On Windows: use `cmd /c` or `powershell -Command` for shell commands. Paths use backslashes. Python is `python` not `python3`. Virtual env binaries are in `Scripts/` not `bin/`.
- On Linux/macOS: use `bash -c` for shell commands. Paths use forward slashes. Python is `python3`. Virtual env binaries are in `bin/`.
- When writing shell commands for `run_command`, always use the correct syntax for the current platform.
"""

# ==================== Tool Definitions ====================
AGENT_TOOLS = [
    # ── Task Planning & Tracking (PRIORITY: always first) ──
    {
        'type': 'function',
        'function': {
            'name': 'todo_write',
            'description': (
                'Create or update a task plan with a list of todo items. Each item has an id, content, status (pending/in_progress/completed), '
                'and priority (high/medium/low). Use this BEFORE starting any complex multi-step task to plan your approach. '
                'Update the status as you progress — mark items in_progress when working on them and completed when done. '
                'This helps you stay organized and avoid missing steps. The todo list is displayed to the user in real-time.'
            ),
            'parameters': {
                'type': 'object',
                'properties': {
                    'todos': {
                        'type': 'array',
                        'description': 'The updated todo list (replaces the entire list)',
                        'items': {
                            'type': 'object',
                            'properties': {
                                'id': {'type': 'string', 'description': 'Unique identifier for this todo item'},
                                'content': {'type': 'string', 'description': 'Description of the task'},
                                'status': {'type': 'string', 'enum': ['pending', 'in_progress', 'completed'], 'description': 'Task status'},
                                'priority': {'type': 'string', 'enum': ['high', 'medium', 'low'], 'description': 'Priority level'},
                            },
                            'required': ['id', 'content', 'status'],
                        },
                    },
                },
                'required': ['todos'],
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'todo_read',
            'description': (
                'Read the current todo list. Returns all todo items with their id, content, status, and priority. '
                'Use this to check your progress on a task plan.'
            ),
            'parameters': {
                'type': 'object',
                'properties': {},
                'required': [],
            },
        },
    },
    # ── File & Code Tools ──
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
                'specific with surrounding context to avoid unintended changes. Returns the number of replacements made. '
                'For multiple edits to the same file, use the "replacements" array parameter — all replacements are '
                'applied atomically (all succeed or all fail, no partial changes).'
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
                    'replacements': {
                        'type': 'array',
                        'description': 'Array of {old_text, new_text} objects for atomic multi-edit. All applied or none. Mutually exclusive with old_text/new_text.',
                        'items': {
                            'type': 'object',
                            'properties': {
                                'old_text': {'type': 'string', 'description': 'Exact text to search for'},
                                'new_text': {'type': 'string', 'description': 'Replacement text'},
                            },
                            'required': ['old_text', 'new_text'],
                        },
                    },
                },
                'required': ['path'],
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
    {
        'type': 'function',
        'function': {
            'name': 'move_file',
            'description': (
                'Move or rename a file or directory. Creates the destination parent directory if needed. '
                'Useful for reorganizing project structure, renaming files, or moving files between directories. '
                'Updates the AST index automatically for source code files.'
            ),
            'parameters': {
                'type': 'object',
                'properties': {
                    'source': {
                        'type': 'string',
                        'description': 'Absolute path of the file/directory to move',
                    },
                    'destination': {
                        'type': 'string',
                        'description': 'Absolute path of the destination (new name or new location)',
                    },
                },
                'required': ['source', 'destination'],
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'append_file',
            'description': (
                'Append content to an existing file. The content is added at the end of the file. '
                'A newline is automatically added if the content does not end with one. '
                'Useful for adding entries to log files, configuration files, or data files. '
                'For creating new files or replacing content, use write_file instead.'
            ),
            'parameters': {
                'type': 'object',
                'properties': {
                    'path': {
                        'type': 'string',
                        'description': 'Absolute path to the file to append to (must exist)',
                    },
                    'content': {
                        'type': 'string',
                        'description': 'Content to append to the file',
                    },
                },
                'required': ['path', 'content'],
            },
        },
    },
    # ── Preview & Debugging Tools ──
    {
        'type': 'function',
        'function': {
            'name': 'browser_navigate',
            'description': (
                'Navigate the built-in preview iframe to a URL. Returns success/error status.'
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
    # ── P0+P1 New Tools ──

    {
        'type': 'function',
        'function': {
            'name': 'glob_files',
            'description': (
                'Fast file pattern matching using glob syntax. Returns matching file paths sorted by modification time. '
                'Supports patterns like "**/*.py", "src/**/*.{ts,tsx}", "static/css/*.css". '
                'Much faster than search_files for finding files by name. Returns up to 100 results.'
            ),
            'parameters': {
                'type': 'object',
                'properties': {
                    'pattern': {
                        'type': 'string',
                        'description': 'Glob pattern to match files (e.g. "**/*.py", "src/**/*.ts")',
                    },
                    'path': {
                        'type': 'string',
                        'description': 'Base directory to search in. Defaults to workspace root.',
                    },
                },
                'required': ['pattern'],
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'find_definition',
            'description': (
                'Find the definition location of a function, class, or variable in source code. '
                'Uses AST (tree-sitter) semantic analysis for precise results — understands scopes, decorators, and nesting. '
                'Returns file path, line number, kind (function/class/method/constant), parent class, and surrounding context. '
                'Useful for understanding code structure and jumping to definitions.'
            ),
            'parameters': {
                'type': 'object',
                'properties': {
                    'symbol': {
                        'type': 'string',
                        'description': 'The symbol name to find (e.g. "execute_agent_tool", "MyClass", "API_KEY")',
                    },
                    'path': {
                        'type': 'string',
                        'description': 'Directory or file to search in. Defaults to workspace root.',
                    },
                },
                'required': ['symbol'],
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'find_references',
            'description': (
                'Find all references/usages of a symbol across the codebase using AST (tree-sitter) analysis. '
                'Automatically excludes definition lines, string literals, and comments for accurate results. '
                'Returns file paths, line numbers, and matching lines. Useful for refactoring and understanding dependencies.'
            ),
            'parameters': {
                'type': 'object',
                'properties': {
                    'symbol': {
                        'type': 'string',
                        'description': 'The symbol name to find references for (e.g. "execute_agent_tool", "WORKSPACE")',
                    },
                    'path': {
                        'type': 'string',
                        'description': 'Directory or file to search in. Defaults to workspace root.',
                    },
                    'include_tests': {
                        'type': 'boolean',
                        'description': 'Whether to include test files in the search. Default: true',
                    },
                },
                'required': ['symbol'],
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'file_structure',
            'description': (
                'Parse a source file using AST (tree-sitter) and return its structural outline: classes, functions, methods, imports, and top-level variables. '
                'Supports Python, JavaScript, TypeScript, and Go files. Shows full parameter lists and parent classes. '
                'Useful for quickly understanding file organization without reading the entire file.'
            ),
            'parameters': {
                'type': 'object',
                'properties': {
                    'path': {
                        'type': 'string',
                        'description': 'Path to the source file to analyze.',
                    },
                },
                'required': ['path'],
            },
        },
    },
    # ── Sub-Agent Tools ──
    {
        'type': 'function',
        'function': {
            'name': 'delegate_task',
            'description': (
                'Launch a sub-agent to handle an independent, well-defined subtask. Supports two modes:\n'
                '- "read" (default): Read-only exploration/research — can use read_file, glob_files, grep_code, search_files, '
                'list_directory, file_info, file_structure, find_definition, find_references, web_search, web_fetch. '
                'Use this for code analysis, architecture exploration, finding usages, summarizing code, etc.\n'
                '- "write": Full write-capable sub-agent — has access to ALL tools (read, write, edit, run commands, git, etc.). '
                'Use this for parallel code modifications (e.g. "create unit tests for module X", "add error handling to all API routes"). '
                'The sub-agent runs independently with its own context and returns a summary of what it did.\n'
                'Max 15 iterations. Returns a concise summary of findings or changes made.'
            ),
            'parameters': {
                'type': 'object',
                'properties': {
                    'task': {
                        'type': 'string',
                        'description': 'A clear description of the subtask to perform. Be specific about what to gather, analyze, or modify.',
                    },
                    'mode': {
                        'type': 'string',
                        'enum': ['read', 'write'],
                        'description': '"read" for exploration only (safe, default). "write" for tasks that need to modify files or run commands.',
                        'default': 'read',
                    },
                    'max_iterations': {
                        'type': 'integer',
                        'description': 'Max iterations for the sub-agent (1-15). Default: 8',
                    },
                    'context': {
                        'type': 'string',
                        'description': 'Optional context from the main agent (e.g. relevant file paths, current state, previous findings). This helps the sub-agent work more efficiently without re-discovering what you already know.',
                    },
                },
                'required': ['task'],
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'parallel_tasks',
            'description': (
                'Launch multiple sub-agents simultaneously to handle independent subtasks in parallel. '
                'Each task runs in its own thread with its own context. Supports both "read" and "write" modes per task. '
                'Use this when you have 2-4 independent subtasks that can be done concurrently (e.g. '
                'simultaneously analyze different modules, create tests for different files, or refactor different components). '
                'All tasks must be truly independent — do NOT have them modify the same files. '
                'Returns a combined summary of all results. Max 4 parallel tasks.'
            ),
            'parameters': {
                'type': 'object',
                'properties': {
                    'tasks': {
                        'type': 'array',
                        'description': 'List of tasks to run in parallel (max 4)',
                        'items': {
                            'type': 'object',
                            'properties': {
                                'task': {'type': 'string', 'description': 'Description of the subtask'},
                                'mode': {'type': 'string', 'enum': ['read', 'write'], 'description': '"read" or "write" (default: "read")'},
                                'max_iterations': {'type': 'integer', 'description': 'Max iterations (1-15). Default: 8'},
                                'context': {'type': 'string', 'description': 'Optional context from main agent'},
                            },
                            'required': ['task'],
                        },
                    },
                },
                'required': ['tasks'],
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

def _truncate(text, limit=30000, tail=3000):
    """Truncate text to limit characters, keeping head and tail for context."""
    if len(text) > limit:
        kept_head = text[:limit]
        kept_tail = text[-tail:] if tail > 0 else ''
        parts = [kept_head]
        parts.append(f'\n\n[... truncated: showing first {limit} of {len(text)} characters ...]')
        if kept_tail:
            parts.append(f'\n\n[... last {tail} characters ...]\n{kept_tail}')
        return ''.join(parts)
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
        # P2-3: Atomic write — write to temp file first, then rename
        # This prevents file corruption if the process crashes mid-write
        _dir = os.path.dirname(path) or '.'
        try:
            fd, tmp_path = tempfile.mkstemp(dir=_dir, suffix='.tmp', prefix='.phoneide_write_')
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                f.write(content)
            os.replace(tmp_path, path)  # atomic on POSIX, near-atomic on Windows
        except Exception:
            # Clean up temp file if replace failed
            if os.path.exists(tmp_path):
                try:
                    os.unlink(tmp_path)
                except Exception:
                    pass
            raise
        # Auto-update AST index for source files
        ext = os.path.splitext(path)[1].lower()
        if ext in ('.py', '.js', '.ts', '.tsx', '.jsx', '.go', '.mjs', '.cjs'):
            try:
                project_index.index_file(path, content.encode('utf-8'))
            except Exception:
                pass
        return f'File written successfully: {path} ({os.path.getsize(path)} bytes)'
    except Exception as e:
        return f'Error writing file {path}: {e}'

def _tool_edit_file(args):
    path = _validate_path(args['path'])
    replacements = args.get('replacements')
    try:
        if not os.path.isfile(path):
            return f'Error: File not found: {path}'
        with open(path, 'r', encoding='utf-8') as f:
            content = f.read()

        # MultiEdit: atomic multi-replacement mode
        if replacements is not None and isinstance(replacements, list) and len(replacements) > 0:
            original_content = content
            total_replacements = 0
            errors = []
            for i, rep in enumerate(replacements):
                old_text = rep.get('old_text', '')
                new_text = rep.get('new_text', '')
                if not old_text:
                    errors.append(f'Replacement {i+1}: missing old_text')
                    continue
                count = content.count(old_text)
                if count == 0:
                    errors.append(f'Replacement {i+1}: old_text not found')
                    continue
                if count > 1:
                    errors.append(f'Replacement {i+1}: old_text found {count} times (ambiguous)')
                content = content.replace(old_text, new_text, 1)
                total_replacements += 1

            if errors:
                return f'Error: MultiEdit failed — {"; ".join(errors)}\nNo changes were made (atomic rollback).'
            if total_replacements == 0:
                return 'Error: No valid replacements provided.'

            # Atomic write (same pattern as _tool_write_file)
            _dir = os.path.dirname(path) or '.'
            fd, tmp_path = tempfile.mkstemp(dir=_dir, suffix='.tmp', prefix='.phoneide_edit_')
            try:
                with os.fdopen(fd, 'w', encoding='utf-8') as f:
                    f.write(content)
                os.replace(tmp_path, path)
            except Exception:
                try:
                    os.unlink(tmp_path)
                except Exception:
                    pass
                raise
            # Auto-verify: check that new_text exists in written file
            verify_errors = []
            for i, rep in enumerate(replacements):
                new_text = rep.get('new_text', '')
                if new_text and new_text not in content:
                    verify_errors.append(f'Replacement {i+1}: new_text not found after edit (possible whitespace issue)')
            if verify_errors:
                # Rollback to original (atomic)
                fd2, tmp_path2 = tempfile.mkstemp(dir=_dir, suffix='.tmp', prefix='.phoneide_rollback_')
                try:
                    with os.fdopen(fd2, 'w', encoding='utf-8') as f:
                        f.write(original_content)
                    os.replace(tmp_path2, path)
                except Exception:
                    try:
                        os.unlink(tmp_path2)
                    except Exception:
                        pass
                return f'Error: Edit verification failed — {"; ".join(verify_errors)}. File rolled back to original. Check whitespace/indentation in your new_text.'
            # Auto-update AST index
            ext = os.path.splitext(path)[1].lower()
            if ext in ('.py', '.js', '.ts', '.tsx', '.jsx', '.go', '.mjs', '.cjs'):
                try:
                    project_index.index_file(path, content.encode('utf-8'))
                except Exception:
                    pass
            return f'MultiEdit applied to {path}: {total_replacements} replacement(s) made'

        # Legacy single-replacement mode
        old_text = args['old_text']
        new_text = args['new_text']
        count = content.count(old_text)
        if count == 0:
            return f'Error: old_text not found in file. Make sure the text matches exactly (including whitespace).'
        if count > 1:
            return f'Error: old_text found {count} times — ambiguous match. Provide more surrounding context to uniquely identify the target, or use the "replacements" array parameter for multiple specific edits.'
        new_content = content.replace(old_text, new_text)
        # Atomic write
        _dir = os.path.dirname(path) or '.'
        fd, tmp_path = tempfile.mkstemp(dir=_dir, suffix='.tmp', prefix='.phoneide_edit_')
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                f.write(new_content)
            os.replace(tmp_path, path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass
            raise
        # Auto-update AST index
        ext = os.path.splitext(path)[1].lower()
        if ext in ('.py', '.js', '.ts', '.tsx', '.jsx', '.go', '.mjs', '.cjs'):
            try:
                project_index.index_file(path, new_content.encode('utf-8'))
            except Exception:
                pass
        return f'Edited file: {path} ({count} replacement(s) made)'
    except Exception as e:
        return f'Error editing file {path}: {e}'

def _tool_list_directory(args):
    path = _validate_path(args.get('path', WORKSPACE))
    show_hidden = args.get('show_hidden', False)
    verbose = args.get('verbose', False)
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
            if verbose:
                ftype = 'dir' if is_dir else get_file_type(entry)
                perm = oct(st.st_mode)[-3:]
                mod_time = datetime.fromtimestamp(st.st_mtime).strftime('%Y-%m-%d %H:%M:%S')
                sz = st.st_size
                items.append(f'  {"[DIR]" if is_dir else "[FILE]"} {perm} {mod_time} {sz:>10}  {entry}  ({ftype})')
            else:
                sz = st.st_size
                sz_str = f'{sz}' if sz < 1024 else f'{sz/1024:.0f}K' if sz < 1048576 else f'{sz/1048576:.1f}M'
                items.append(f'  {"[DIR]" if is_dir else "[FILE]"} {sz_str:>8}  {entry}')
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
        # Auto-create workspace if it doesn't exist
        if not os.path.isdir(ws):
            os.makedirs(ws, exist_ok=True)
        project = config.get('project', None)
        if project:
            project_dir = os.path.join(ws, project)
            if os.path.isdir(project_dir):
                return project_dir
        return ws
    except Exception:
        return WORKSPACE


def _tool_run_command(args):
    from utils import IS_WINDOWS
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
        _bin_dir = 'Scripts' if IS_WINDOWS else 'bin'
        venv_bin = os.path.join(venv_path, _bin_dir)
        if os.path.exists(venv_bin):
            _path_sep = ';' if IS_WINDOWS else ':'
            env['PATH'] = venv_bin + _path_sep + env.get('PATH', '')
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
        full_output = (output or '(no output)') + exit_info
        # Return error status when command exits with non-zero code
        if result.returncode != 0:
            raise RuntimeError(full_output)
        return _truncate(full_output)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f'Command timed out after {timeout} seconds')
    except RuntimeError:
        raise  # Re-raise RuntimeError (non-zero exit code) without wrapping
    except Exception as e:
        raise RuntimeError(f'Error executing command: {str(e)}')

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
    from utils import IS_WINDOWS, get_default_compiler
    package_name = args['package_name']
    manager = args.get('manager', 'auto')
    config = load_config()
    if manager == 'auto':
        # Explicit parentheses: npm if it looks like an npm package AND package.json exists
        manager = 'npm' if (
            package_name.startswith('@') or
            (not re.search(r'[a-zA-Z]-[a-zA-Z]', package_name) and
             os.path.exists(os.path.join(WORKSPACE, 'package.json')))
        ) else 'pip'
    if manager == 'npm':
        cmd = f'npm install {shlex_quote(package_name)}'
    else:
        venv = config.get('venv_path', '')
        if IS_WINDOWS:
            pip = os.path.join(venv, 'Scripts', 'pip.exe') if venv and os.path.exists(os.path.join(venv, 'Scripts', 'pip.exe')) else get_default_compiler() + ' -m pip'
        else:
            pip = os.path.join(venv, 'bin', 'pip') if venv and os.path.exists(os.path.join(venv, 'bin', 'pip')) else get_default_compiler() + ' -m pip'
        cmd = f'{pip} install {shlex_quote(package_name)}'
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=100, cwd=WORKSPACE)
    output = r.stdout or ''
    if r.stderr:
        output += ('\n' if output else '') + r.stderr
    if r.returncode == 0:
        return _truncate(f'Package installed successfully: {package_name}\n{output}')
    return _truncate(f'Error installing {package_name} (exit code {r.returncode}):\n{output}')

def _tool_list_packages(args):
    from utils import IS_WINDOWS
    manager = args.get('manager', 'pip')
    config = load_config()
    if manager == 'npm':
        r = subprocess.run('npm list --depth=0 2>/dev/null', shell=True, capture_output=True, text=True, timeout=30, cwd=WORKSPACE)
        return r.stdout or 'No packages found'
    venv = config.get('venv_path', '')
    if IS_WINDOWS:
        pip = os.path.join(venv, 'Scripts', 'pip.exe') if venv and os.path.exists(os.path.join(venv, 'Scripts', 'pip.exe')) else 'pip'
    else:
        pip = os.path.join(venv, 'bin', 'pip') if venv and os.path.exists(os.path.join(venv, 'bin', 'pip')) else 'pip3'
    r = subprocess.run(f'{pip} list --format=json', shell=True, capture_output=True, text=True, timeout=30)
    if r.returncode == 0:
        try:
            pkgs = json.loads(r.stdout)
            lines = [f'  {p["name"]}=={p["version"]}' for p in pkgs]
            return f'Installed packages ({len(lines)}):\n' + '\n'.join(lines)
        except Exception:
            return r.stdout or r.stderr or 'No packages found'
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
    _grep_start = time.time()
    GREP_TIMEOUT = 30
    for root, dirs, files in os.walk(search_path):
        if time.time() - _grep_start > GREP_TIMEOUT:
            results.append(f'[Search timed out after {GREP_TIMEOUT}s]')
            break
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
    try:
        os.makedirs(path, exist_ok=True)
        return f'Directory created: {path}'
    except PermissionError:
        return f'Error: Permission denied creating directory: {path}'
    except OSError as e:
        return f'Error creating directory {path}: {e}'

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

def _tool_move_file(args):
    """Move or rename a file/directory."""
    src = _validate_path(args['source'])
    dst = _validate_path(args['destination'])
    if not os.path.exists(src):
        return f'Error: Source not found: {src}'
    try:
        # Create destination parent directory if needed
        dst_parent = os.path.dirname(dst)
        if dst_parent:
            os.makedirs(dst_parent, exist_ok=True)
        shutil.move(src, dst)
        # Update AST index: remove old, add new if it's a source file
        ext_src = os.path.splitext(src)[1].lower()
        ext_dst = os.path.splitext(dst)[1].lower()
        if ext_src in ('.py', '.js', '.ts', '.tsx', '.jsx', '.go', '.mjs', '.cjs'):
            try:
                project_index.remove_file(src)
            except Exception:
                pass
        if ext_dst in ('.py', '.js', '.ts', '.tsx', '.jsx', '.go', '.mjs', '.cjs'):
            try:
                if os.path.isfile(dst):
                    with open(dst, 'rb') as f:
                        project_index.index_file(dst, f.read())
            except Exception:
                pass
        src_type = 'directory' if os.path.isdir(dst) else 'file'
        return f'Moved {src_type}: {src} -> {dst}'
    except Exception as e:
        return f'Error moving {src} to {dst}: {e}'

def _tool_append_file(args):
    """Append content to an existing file."""
    path = _validate_path(args['path'])
    content = args['content']
    if not os.path.isfile(path):
        return f'Error: File not found: {path}. Use write_file to create new files.'
    try:
        with open(path, 'a', encoding='utf-8') as f:
            if not content.endswith('\n'):
                content += '\n'
            f.write(content)
        size = os.path.getsize(path)
        return f'Appended to file: {path} (now {size} bytes)'
    except Exception as e:
        return f'Error appending to {path}: {e}'

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

def _tool_browser_page_info(args):
    cmd_id = create_browser_command('page_info', {})
    result = wait_browser_result(cmd_id, timeout=30)
    return _format_browser_result(result)

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

# ── P0+P1 New Tool Implementations ──

def _tool_glob_files(args):
    """Fast file pattern matching using glob."""
    pattern = args['pattern']
    search_path = _validate_path(args.get('path', WORKSPACE))
    if not os.path.isdir(search_path):
        return f'Error: Directory not found: {search_path}'

    # Use pathlib-style recursive matching
    if '**' in pattern:
        full_pattern = os.path.join(search_path, pattern)
        matches = _glob.glob(full_pattern, recursive=True)
    else:
        full_pattern = os.path.join(search_path, pattern)
        matches = _glob.glob(full_pattern)

    # Filter to files only, resolve paths, deduplicate
    seen = set()
    files = []
    for f in matches:
        if os.path.isfile(f):
            real = os.path.realpath(f)
            if real not in seen:
                seen.add(real)
                files.append(f)

    # Sort by modification time (newest first)
    files.sort(key=lambda f: os.path.getmtime(f), reverse=True)

    # Limit results
    max_results = 100
    if len(files) > max_results:
        files = files[:max_results]

    if not files:
        return f'No files matching pattern "{pattern}" in {search_path}'

    lines = [f'Found {len(files)} file(s) matching "{pattern}":']
    for f in files:
        rel = os.path.relpath(f, search_path)
        size = os.path.getsize(f)
        mtime = datetime.fromtimestamp(os.path.getmtime(f)).strftime('%Y-%m-%d %H:%M')
        lines.append(f'  {rel}  ({size:,} bytes, {mtime})')

    if len(files) == max_results:
        lines.append(f'  [showing first {max_results} results, sorted by modification time]')
    return '\n'.join(lines)

def _tool_find_definition(args):
    """Find definition of a symbol using AST (tree-sitter) semantic analysis."""
    symbol = args['symbol']
    search_path = _validate_path(args.get('path', WORKSPACE))
    if not os.path.isdir(search_path) and not os.path.isfile(search_path):
        return f'Error: Path not found: {search_path}'

    skip_dirs = {'.git', '__pycache__', 'node_modules', '.venv', 'venv', '.idea', '.vscode', 'dist', 'build', '.next'}
    supported_ext = {'.py', '.js', '.ts', '.tsx', '.jsx', '.go', '.mjs', '.cjs'}
    results = []
    search_start = time.time()

    # Collect files to search
    if os.path.isfile(search_path):
        file_list = [search_path]
    else:
        file_list = []
        for root, dirs, files in os.walk(search_path):
            if time.time() - search_start > 20:
                break
            dirs[:] = [d for d in dirs if d not in skip_dirs]
            for fname in files:
                ext = os.path.splitext(fname)[1].lower()
                if ext in supported_ext:
                    file_list.append(os.path.join(root, fname))

    # Use AST to find definitions
    for fpath in file_list:
        if time.time() - search_start > 20 or len(results) >= 20:
            break
        try:
            defs = extract_definitions(fpath)
            for d in defs:
                if d['name'] == symbol:
                    rel = os.path.relpath(fpath, search_path)
                    try:
                        with open(fpath, 'r', encoding='utf-8', errors='ignore') as f:
                            lines = f.readlines()
                        line_idx = d['line'] - 1
                        ctx_start = max(0, line_idx - 2)
                        ctx_end = min(len(lines), line_idx + 6)
                        context = ''.join(
                            f'  {"→" if j == line_idx else " "} {j+1:>5}\t{lines[j].rstrip()}\n'
                            for j in range(ctx_start, ctx_end)
                        )
                    except Exception:
                        context = ''
                    parent_info = f' (in {d["parent"]})' if d.get('parent') else ''
                    results.append(f'{d["kind"]}{parent_info} in {rel}:{d["line"]}\n{context}')
        except (PermissionError, OSError):
            continue

    if not results:
        return f'No definition found for "{symbol}"'
    return f'Found {len(results)} definition(s) for "{symbol}":\n' + '\n---\n'.join(results[:20])

def _tool_find_references(args):
    """Find all references/usages of a symbol using AST (tree-sitter).
    Skips string literals and comments for accurate results.
    Falls back to regex for unsupported file types.
    """
    symbol = args['symbol']
    search_path = _validate_path(args.get('path', WORKSPACE))
    if not os.path.isdir(search_path) and not os.path.isfile(search_path):
        return f'Error: Path not found: {search_path}'

    include_tests = args.get('include_tests', True)
    skip_dirs = {'.git', '__pycache__', 'node_modules', '.venv', 'venv', '.idea', '.vscode', 'dist', 'build', '.next'}
    if not include_tests:
        skip_dirs.add('tests')
        skip_dirs.add('test')

    ast_ext = {'.py', '.js', '.ts', '.tsx', '.jsx', '.go', '.mjs', '.cjs'}
    fallback_ext = {'.json', '.yaml', '.yml', '.md', '.html', '.css', '.sh', '.toml', '.cfg', '.ini'}
    results = []
    search_start = time.time()
    max_results = 50

    if os.path.isfile(search_path):
        file_list = [search_path]
    else:
        file_list = []
        for root, dirs, files in os.walk(search_path):
            if time.time() - search_start > 20:
                break
            dirs[:] = [d for d in dirs if d not in skip_dirs]
            for fname in files:
                ext = os.path.splitext(fname)[1].lower()
                if ext in ast_ext or ext in fallback_ext:
                    file_list.append(os.path.join(root, fname))

    for fpath in file_list:
        if time.time() - search_start > 20 or len(results) >= max_results:
            break
        ext = os.path.splitext(fpath)[1].lower()
        rel = os.path.relpath(fpath, search_path)

        try:
            if ext in ast_ext:
                # Use AST for source code files — skips strings/comments
                refs = find_references_ast(fpath, symbol)
                for r in refs:
                    results.append(f'{rel}:{r["line"]}: {r["text"]}')
            else:
                # Fallback to regex for config/text files
                with open(fpath, 'r', encoding='utf-8', errors='ignore') as f:
                    lines = f.readlines()
                word_pattern = re.escape(symbol)
                regex = re.compile(r'\b' + word_pattern + r'\b')
                for i, line in enumerate(lines):
                    if regex.search(line):
                        results.append(f'{rel}:{i+1}: {line.rstrip()}')
                        if len(results) >= max_results:
                            break
        except (PermissionError, OSError):
            continue

    if not results:
        return f'No references found for "{symbol}"'
    output = f'Found {len(results)} reference(s) for "{symbol}":\n'
    output += '\n'.join(results)
    if len(results) >= max_results:
        output += f'\n[showing first {max_results} results]'
    return output

def _tool_file_structure(args):
    """Parse source file and return structural outline using AST (tree-sitter).
    Falls back to regex for unsupported file types.
    """
    path = _validate_path(args['path'])
    if not os.path.isfile(path):
        return f'Error: File not found: {path}'

    ext = os.path.splitext(path)[1].lower()
    rel = os.path.relpath(path, WORKSPACE)

    try:
        with open(path, 'r', encoding='utf-8', errors='ignore') as f:
            lines = f.readlines()
    except Exception as e:
        return f'Error reading file: {e}'

    # Try AST first
    struct = get_file_structure(path)
    if struct:
        outline = [f'File: {rel} ({len(lines)} lines) [AST]', '']
        if struct.get('imports'):
            outline.append(f'Imports ({len(struct["imports"])}):')
            for imp in struct['imports'][:30]:
                outline.append(f'  L{imp["line"]}: {imp["text"]}')
            if len(struct['imports']) > 30:
                outline.append(f'  ... and {len(struct["imports"])-30} more imports')
            outline.append('')
        if struct.get('classes'):
            outline.append(f'Classes/Types ({len(struct["classes"])}):')
            for cls in struct['classes']:
                outline.append(f'  L{cls["line"]}: {cls["text"]}')
            outline.append('')
        if struct.get('functions'):
            outline.append(f'Functions/Methods ({len(struct["functions"])}):')
            for fn in struct['functions'][:80]:
                outline.append(f'  L{fn["line"]}: {fn["text"]}')
            if len(struct['functions']) > 80:
                outline.append(f'  ... and {len(struct["functions"])-80} more functions')
            outline.append('')
        if struct.get('variables'):
            outline.append(f'Constants/Variables ({len(struct["variables"])}):')
            for var in struct['variables'][:20]:
                parent_info = f' (in {var["parent"]})' if var.get('parent') else ''
                outline.append(f'  L{var["line"]}: {var["text"]}{parent_info}')
            outline.append('')
        total = len(struct.get('imports', [])) + len(struct.get('classes', [])) + \
                len(struct.get('functions', [])) + len(struct.get('variables', []))
        outline.append(f'Total: {total} symbols')
        return '\n'.join(outline)

    # Fallback to regex for unsupported extensions
    return _file_structure_regex(path, ext, rel, lines)


def _file_structure_regex(path, ext, rel, lines):
    """Regex-based file structure fallback for unsupported file types."""
    outline = [f'File: {rel} ({len(lines)} lines)', '']
    if ext not in ('.py', '.js', '.ts', '.tsx', '.jsx', '.go'):
        return f'Unsupported file type: {ext}. Supported: .py, .js, .ts, .tsx, .jsx, .go'

    imports = []
    classes = []
    functions = []
    variables = []

    if ext == '.py':
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith('import ') or stripped.startswith('from '):
                imports.append(f'  L{i+1}: {stripped[:100]}')
            class_m = re.match(r'^(\s*)class\s+(\w+)', line)
            if class_m:
                name = class_m.group(2)
                paren = stripped.find('(')
                bases = stripped[paren:stripped.find(')')+1] if paren > 0 and ')' in stripped else ''
                classes.append(f'  L{i+1}: class {name}{bases}')
            func_m = re.match(r'^(\s*)(async\s+)?def\s+(\w+)', line)
            if func_m:
                name = func_m.group(3)
                prefix = 'async ' if func_m.group(2) else ''
                paren = stripped.find('(')
                params = stripped[paren:stripped.find(')', paren)+1] if paren > 0 and ')' in stripped[paren:] else '()'
                functions.append(f'  L{i+1}: {prefix}def {name}{params}')
    elif ext in ('.js', '.ts', '.tsx', '.jsx'):
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith('import ') or stripped.startswith('export '):
                imports.append(f'  L{i+1}: {stripped[:100]}')
            class_m = re.match(r'^(\s*)(export\s+)?(default\s+)?class\s+(\w+)', line)
            if class_m:
                classes.append(f'  L{i+1}: class {class_m.group(4)}')
            func_m = re.match(r'^(\s*)(export\s+)?(default\s+)?(async\s+)?function\s+(\w+)', line)
            if func_m:
                functions.append(f'  L{i+1}: function {func_m.group(5)}')
    elif ext == '.go':
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith('import '):
                imports.append(f'  L{i+1}: {stripped[:100]}')
            func_m = re.match(r'^\s*func\s+(?:\(\w+\s+\*?\w+\)\s+)?(\w+)', line)
            if func_m:
                functions.append(f'  L{i+1}: func {func_m.group(1)}')
            type_m = re.match(r'^\s*type\s+(\w+)\s+struct', line)
            if type_m:
                classes.append(f'  L{i+1}: type {type_m.group(1)} struct')

    if imports:
        outline.append(f'Imports ({len(imports)}):')
        outline.extend(imports[:30])
        outline.append('')
    if classes:
        outline.append(f'Classes ({len(classes)}):')
        outline.extend(classes)
        outline.append('')
    if functions:
        outline.append(f'Functions ({len(functions)}):')
        outline.extend(functions[:50])
        outline.append('')
    if variables:
        outline.append(f'Constants ({len(variables)}):')
        outline.extend(variables[:20])
        outline.append('')

    total_items = len(imports) + len(classes) + len(functions) + len(variables)
    if total_items == 0:
        return f'No structure found in {rel} (empty or unrecognized format)'
    outline.append(f'Total: {total_items} symbols')
    return '\n'.join(outline)

# Read-only tools available to sub-agents (read mode)
_SUBAGENT_TOOLS = {
    'read_file': _tool_read_file,
    'glob_files': _tool_glob_files,
    'grep_code': _tool_grep_code,
    'search_files': _tool_search_files,
    'list_directory': _tool_list_directory,
    'file_info': _tool_file_info,
    'file_structure': _tool_file_structure,
    'find_definition': _tool_find_definition,
    'find_references': _tool_find_references,
    'web_search': _tool_web_search,
    'web_fetch': _tool_web_fetch,
}

# Write-capable tools for sub-agents (write mode) — includes all read tools + write/edit/run
_WRITE_SUBAGENT_TOOLS = dict(_SUBAGENT_TOOLS)
_WRITE_SUBAGENT_TOOLS.update({
    'write_file': _tool_write_file,
    'edit_file': _tool_edit_file,
    'run_command': _tool_run_command,
    'install_package': _tool_install_package,
    'create_directory': _tool_create_directory,
    'delete_path': _tool_delete_path,
    'move_file': _tool_move_file,
    'append_file': _tool_append_file,
    'git_status': _tool_git_status,
    'git_diff': _tool_git_diff,
    'git_commit': _tool_git_commit,
    'git_log': _tool_git_log,
    'git_checkout': _tool_git_checkout,
})

# Tool definitions for sub-agent API calls (read mode)
_SUBAGENT_TOOL_DEFS = [t for t in AGENT_TOOLS if t['function']['name'] in _SUBAGENT_TOOLS]

# Tool definitions for write-mode sub-agents
_WRITE_SUBAGENT_TOOL_DEFS = [t for t in AGENT_TOOLS if t['function']['name'] in _WRITE_SUBAGENT_TOOLS]

# ==================== Todo Storage ====================
_phoneide_knowledge_cache = {}  # {cache_key_tuple: {content, files, time}}

def _get_phoneide_cache():
    """Return the module-level .phoneide/ knowledge cache."""
    return _phoneide_knowledge_cache

_active_todos = {
    'todos': [],
    'lock': threading.Lock(),
}

def _tool_todo_read(args):
    """Read the current todo list."""
    with _active_todos['lock']:
        todos = _active_todos['todos']
    if not todos:
        return 'No active todos.'
    lines = []
    for t in todos:
        status_icon = {'pending': '○', 'in_progress': '◐', 'completed': '●'}.get(t.get('status', ''), '○')
        priority_tag = {'high': '🔴', 'medium': '🟡', 'low': '🟢'}.get(t.get('priority', ''), '')
        lines.append(f'{status_icon} [{t.get("id", "?")}] {priority_tag} {t.get("content", "")} ({t.get("status", "pending")})')
    return '\n'.join(lines)

def _tool_todo_write(args):
    """Write/update the todo list."""
    todos = args.get('todos')
    if not isinstance(todos, list):
        return 'Error: todos must be an array of {id, content, status} objects'
    # Validate each item
    for i, t in enumerate(todos):
        if not isinstance(t, dict):
            return f'Error: todo[{i}] must be an object'
        if not t.get('id') or not t.get('content') or not t.get('status'):
            return f'Error: todo[{i}] missing required fields (id, content, status)'
        if t.get('status') not in ('pending', 'in_progress', 'completed'):
            return f'Error: todo[{i}] invalid status "{t.get("status")}", must be pending/in_progress/completed'
        if t.get('priority') and t.get('priority') not in ('high', 'medium', 'low'):
            return f'Error: todo[{i}] invalid priority "{t.get("priority")}", must be high/medium/low'
    with _active_todos['lock']:
        _active_todos['todos'] = todos
    # Build summary
    total = len(todos)
    completed = sum(1 for t in todos if t.get('status') == 'completed')
    in_progress = sum(1 for t in todos if t.get('status') == 'in_progress')
    pending = total - completed - in_progress
    return f'Todo list updated: {total} items ({completed} completed, {in_progress} in progress, {pending} pending)'

# ==================== Sub-Agent Engine ====================
def _run_subagent(task, mode='read', max_iterations=8, llm_config=None, context=None):
    """Core sub-agent execution engine. Used by both delegate_task and parallel_tasks.

    Args:
        task: Task description string.
        mode: 'read' for read-only tools, 'write' for full tools.
        max_iterations: Max agent loop iterations (1-15).
        llm_config: LLM config dict. If None, loads from default.

    Returns:
        Summary string of what the sub-agent found/did.
    """
    if not task:
        return 'Error: task description is required'
    max_iters = min(max(max_iterations, 1), 15)

    if llm_config is None:
        try:
            config = load_config()
            llm_config = get_active_llm_config(config)
        except Exception as e:
            return f'Error loading LLM config: {e}'

    is_write_mode = (mode == 'write')
    sub_tools = _WRITE_SUBAGENT_TOOLS if is_write_mode else _SUBAGENT_TOOLS
    sub_tool_defs = _WRITE_SUBAGENT_TOOL_DEFS if is_write_mode else _SUBAGENT_TOOL_DEFS

    # Build sub-agent system prompt based on mode
    # Include workspace/project path so sub-agent knows where to operate
    try:
        from utils import load_config
        _sub_cfg = load_config()
        _sub_ws = _sub_cfg.get('workspace', WORKSPACE)
        _sub_prj = _sub_cfg.get('project', None)
        _sub_project_dir = os.path.join(_sub_ws, _sub_prj) if _sub_prj else _sub_ws
        if not os.path.isdir(_sub_project_dir):
            _sub_project_dir = _sub_ws
    except Exception:
        _sub_project_dir = WORKSPACE

    if is_write_mode:
        system_prompt = (
            'You are a write-capable sub-agent. You can read files, write/edit files, run commands, and manage git.\n'
            'You have access to a full set of tools for code modification.\n'
            f'Project directory: {_sub_project_dir}\n'
            'IMPORTANT RULES:\n'
            '1. Always read a file before modifying it\n'
            '2. Test your changes with run_command when possible\n'
            '3. Use edit_file for targeted changes, write_file only for new files\n'
            '4. When done, provide a clear summary of ALL changes you made (files modified/created)\n'
            '5. If you encounter errors, try to fix them before reporting\n'
            '6. Be efficient — minimize unnecessary iterations'
        )
    else:
        system_prompt = (
            'You are a research sub-agent. Your job is to gather information and return a concise summary.\n'
            'You have access to read-only tools (read_file, glob_files, grep_code, search_files, list_directory, '
            'file_info, file_structure, find_definition, find_references, web_search, web_fetch).\n'
            f'Project directory: {_sub_project_dir}\n'
            'Be thorough but concise. Focus on factual findings.\n'
            'When done, provide a clear summary of what you found.'
        )

    # Build task message with optional context from main agent
    user_msg = task
    if context:
        user_msg = f'[Context from main agent]\n{context}\n\n[Sub-task]\n{task}'

    sub_context = [
        {'role': 'system', 'content': system_prompt},
        {'role': 'user', 'content': user_msg},
    ]

    tool_results_summary = []

    for iteration in range(max_iters):
        try:
            api_messages = _build_api_messages(sub_context, llm_config, skip_system_inject=True)
            payload = {
                'model': llm_config.get('model', 'gpt-4o-mini'),
                'messages': api_messages,
                'temperature': 0.3,
                'max_tokens': 4096,
                'tools': sub_tool_defs,
                'tool_choice': 'auto',
            }
            url, headers = _get_llm_endpoint(llm_config, payload['model'])
            headers = headers or {'Content-Type': 'application/json'}
            req = urllib.request.Request(url, json.dumps(payload).encode(), headers=headers, method='POST')
            with _urllib_opener.open(req, timeout=120) as resp:
                response = json.loads(resp.read().decode('utf-8'))
        except Exception as e:
            tool_results_summary.append(f'[Error iteration {iteration+1}] {str(e)}')
            break

        choice = response.get('choices', [{}])[0]
        message = choice.get('message', {})
        content = message.get('content', '') or ''
        tool_calls = message.get('tool_calls', [])

        if content:
            sub_context.append({'role': 'assistant', 'content': content})
            tool_results_summary.append(f'[Iteration {iteration+1}] {content[:500]}')

        if not tool_calls:
            break

        sub_context.append({'role': 'assistant', 'content': content or None, 'tool_calls': tool_calls})

        for tc in tool_calls:
            func = tc.get('function', {})
            tool_name = func.get('name', '')
            try:
                tool_args = json.loads(func.get('arguments', '{}'))
            except json.JSONDecodeError:
                tool_args = {}

            handler = sub_tools.get(tool_name)
            if handler:
                try:
                    result = handler(tool_args)
                except Exception as e:
                    result = f'Error: {e}'
            else:
                result = f'Error: Sub-agent cannot use tool "{tool_name}" (not available in {mode} mode)'

            tool_results_summary.append(f'[{tool_name}] {_truncate(result, 300)}')
            sub_context.append({
                'role': 'tool',
                'tool_call_id': tc.get('id', ''),
                'name': tool_name,
                'content': result,
            })

    mode_label = 'Write' if is_write_mode else 'Read'
    output = f'[{mode_label} sub-agent] Completed ({min(iteration+1, max_iters)}/{max_iters} iterations):\n\n'
    output += '\n'.join(tool_results_summary)
    return _truncate(output, 15000)

def _tool_delegate_task(args):
    """Launch a sub-agent for a subtask. Supports read and write modes."""
    task = args.get('task', '').strip()
    mode = args.get('mode', 'read').strip()
    max_iters = args.get('max_iterations', 8)
    context = args.get('context', '').strip() or None
    # Load the current active LLM config so sub-agent uses the same model
    try:
        _cfg = load_config()
        _llm_cfg = get_active_llm_config(_cfg)
    except Exception:
        _llm_cfg = None
    return _run_subagent(task, mode=mode, max_iterations=max_iters, llm_config=_llm_cfg, context=context)

def _tool_parallel_tasks(args):
    """Launch multiple sub-agents in parallel."""
    tasks = args.get('tasks', [])
    if not isinstance(tasks, list) or len(tasks) == 0:
        return 'Error: tasks must be a non-empty array of {task, mode?} objects'
    if len(tasks) > 4:
        return 'Error: max 4 parallel tasks supported'
    for i, t in enumerate(tasks):
        if not t.get('task'):
            return f'Error: tasks[{i}] missing required "task" field'

    # Load LLM config once
    try:
        config = load_config()
        llm_config = get_active_llm_config(config)
    except Exception as e:
        return f'Error loading LLM config: {e}'

    # Run sub-agents in parallel threads
    results = [None] * len(tasks)

    def _run_one(idx, task_def):
        task_text = task_def.get('task', '')
        mode = task_def.get('mode', 'read').strip()
        max_iters = task_def.get('max_iterations', 8)
        ctx = task_def.get('context', '').strip() or None
        results[idx] = _run_subagent(task_text, mode=mode, max_iterations=max_iters, llm_config=llm_config, context=ctx)

    threads = []
    for i, t in enumerate(tasks):
        thread = threading.Thread(target=_run_one, args=(i, t))
        threads.append(thread)
        thread.start()

    for thread in threads:
        thread.join(timeout=300)  # 5 min max per parallel batch

    # Combine results
    output_parts = [f'=== Parallel Tasks Results ({len(tasks)} tasks) ===']
    for i, result in enumerate(results):
        mode_label = tasks[i].get('mode', 'read')
        output_parts.append(f'\n--- Task {i+1} [{mode_label}]: {tasks[i].get("task", "")[:80]} ---')
        output_parts.append(result if result else '(task did not return a result)')
    return _truncate('\n'.join(output_parts), 15000)

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
    'move_file': _tool_move_file,
    'append_file': _tool_append_file,
    'web_search': _tool_web_search,
    'web_fetch': _tool_web_fetch,
    'browser_navigate': _tool_browser_navigate,
    'browser_console': _tool_browser_console,
    'browser_page_info': _tool_browser_page_info,
    'server_logs': _tool_server_logs,
    # P0+P1 new tools
    'glob_files': _tool_glob_files,
    'find_definition': _tool_find_definition,
    'find_references': _tool_find_references,
    'file_structure': _tool_file_structure,
    'delegate_task': _tool_delegate_task,
    'parallel_tasks': _tool_parallel_tasks,
    'todo_write': _tool_todo_write,
    'todo_read': _tool_todo_read,
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
    t0 = time.time()
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(execute_agent_tool, name, arguments)
        try:
            return future.result(timeout=TOOL_EXECUTION_TIMEOUT)
        except concurrent.futures.TimeoutError:
            elapsed = time.time() - t0
            return False, f'Error: Tool "{name}" timed out after {TOOL_EXECUTION_TIMEOUT}s', elapsed

# Read-only tools that can safely run in parallel (no side effects)
_READONLY_TOOLS = frozenset({
    'read_file', 'glob_files', 'grep_code', 'search_files', 'list_directory',
    'file_info', 'file_structure', 'find_definition', 'find_references',
    'list_packages', 'git_status', 'git_diff', 'git_log',
    'web_search', 'web_fetch',
    'browser_page_info', 'browser_console', 'server_logs',
})

def _execute_tools_parallel(tool_calls_raw, emit_fn=None):
    """Execute multiple read-only tools in parallel for speed.
    
    If ALL tools in the batch are read-only, execute them concurrently (max 8 threads).
    If ANY tool has side effects (write/delete/run), fall back to sequential execution.
    
    Returns list of (tool_name, ok, result_str, elapsed, tool_call_id) tuples.
    """
    # Check if all tools are read-only
    all_readonly = True
    for tc in tool_calls_raw:
        func = tc.get('function', {})
        name = func.get('name', '')
        if name not in _READONLY_TOOLS:
            all_readonly = False
            break

    if len(tool_calls_raw) < 2 or not all_readonly:
        return None  # Signal caller to use sequential execution

    # Parallel execution
    results = [None] * len(tool_calls_raw)

    def _run_one(idx, tc):
        func = tc.get('function', {})
        tool_name = func.get('name', '')
        try:
            tool_args = json.loads(func.get('arguments', '{}'))
        except json.JSONDecodeError:
            tool_args = {}
        tool_call_id = tc.get('id', f'call_{tool_name}')
        ok, result_str, elapsed = execute_agent_tool_with_timeout(tool_name, tool_args)
        return (idx, tool_name, ok, result_str, elapsed, tool_call_id)

    max_workers = min(len(tool_calls_raw), 8)
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_run_one, i, tc): i for i, tc in enumerate(tool_calls_raw)}
        for future in concurrent.futures.as_completed(futures):
            result = future.result()
            results[result[0]] = result

    return results

# ==================== System Prompt Caching & Budget ====================
_SYSTEM_PROMPT_CACHE = {}  # {cache_key: {'prompt': str, 'tokens': int, 'time': float}}
_SYSTEM_PROMPT_CACHE_TTL = 60  # seconds — cache the full system prompt for 60s
_SYSTEM_PROMPT_MAX_TOKENS = 4500  # max tokens for the system prompt


def _trim_system_prompt_to_budget(sys_prompt, max_tokens=_SYSTEM_PROMPT_MAX_TOKENS):
    """Trim system prompt to fit within token budget.

    Trimming priority (least important first):
    1. AST index / Project Symbols section
    2. .phoneide/ Project Knowledge section
    3. System Environment section
    If still too large, truncate the trailing sections.
    """
    estimated = _estimate_tokens(sys_prompt)
    if estimated <= max_tokens:
        return sys_prompt

    # Strategy: remove sections from least to most important
    sections = [
        ('## Project Symbols', '## Project Knowledge'),
        ('## Project Knowledge', '## Current Project'),
        ('## System Environment', '## Current Project'),
    ]

    for section_start, next_section in sections:
        if estimated <= max_tokens * 0.85:
            break
        start_idx = sys_prompt.find(section_start)
        if start_idx == -1:
            continue
        end_idx = sys_prompt.find(next_section, start_idx)
        if end_idx == -1:
            end_idx = len(sys_prompt)
        # Remove from section_start to just before next_section
        removed = sys_prompt[start_idx:end_idx]
        sys_prompt = sys_prompt[:start_idx] + sys_prompt[end_idx:]
        estimated = _estimate_tokens(sys_prompt)
        log_write(f'[phoneide] Trimmed system prompt section "{section_start}" ({len(removed)} chars removed, ~{estimated} tokens remaining)')

    # If still too large, hard-truncate the less critical trailing content
    if estimated > max_tokens:
        # Keep the core DEFAULT_SYSTEM_PROMPT (usually first ~3500 chars) and trim injections
        base_prompt_end = sys_prompt.find('\n\n## ')
        if base_prompt_end > 0 and base_prompt_end < len(sys_prompt) * 0.7:
            base = sys_prompt[:base_prompt_end]
            injections = sys_prompt[base_prompt_end:]
            inj_tokens = _estimate_tokens(injections)
            budget_left = max_tokens - _estimate_tokens(base)
            if inj_tokens > budget_left and budget_left > 200:
                # Keep workspace info (always first injection), trim the rest
                ws_end = injections.find('\n\n## ', injections.find('## Current') + 10 if '## Current' in injections else 20)
                if ws_end > 0:
                    ws_part = injections[:ws_end]
                    rest = injections[ws_end:]
                    rest_tokens = _estimate_tokens(rest)
                    if rest_tokens > budget_left - _estimate_tokens(ws_part):
                        rest = rest[:int((budget_left - _estimate_tokens(ws_part)) * 4)] + '\n[... trimmed due to token budget ...]\n'
                    sys_prompt = base + ws_part + rest
                else:
                    sys_prompt = base + injections[:int(budget_left * 4)] + '\n[... trimmed due to token budget ...]\n'
        else:
            sys_prompt = sys_prompt[:int(max_tokens * 4)] + '\n[... system prompt truncated due to token budget ...]\n'
        estimated = _estimate_tokens(sys_prompt)

    log_write(f'[phoneide] System prompt trimmed to ~{estimated} tokens (budget: {max_tokens})')
    return sys_prompt


def _get_system_prompt_cache_key(llm_config):
    """Build a cache key from workspace state and LLM config."""
    try:
        from utils import load_config
        config = load_config()
        ws = config.get('workspace', WORKSPACE)
        prj = config.get('project', '')
        # Include key parts that affect the system prompt
        raw = f'{ws}|{prj}|{SERVER_DIR}'
        # Include AST index state
        raw += f'|ast:{project_index.file_count}:{project_index.symbol_count}:{project_index.last_index_time}'
        # Include custom prompt
        raw += f'|{llm_config.get("system_prompt", "")}'
        return hashlib.md5(raw.encode()).hexdigest()
    except Exception:
        return 'error'


# ==================== LLM Integration ====================

def _build_cached_api_messages(static_prompt, dynamic_prompt, llm_config):
    """Build system message(s) with provider-level prompt caching support.

    Splits the system prompt into static (tool docs, rarely changes) and dynamic
    (workspace info, AST symbols, env — changes per request) parts, and applies
    the appropriate provider-specific caching strategy:

    - Anthropic: content blocks with cache_control ephemeral on static part
    - OpenAI: system role (compatible with all OpenAI-compatible APIs)
    - Others: single system message, no provider-level caching
    """
    provider = llm_config.get('provider', '')
    api_type = llm_config.get('api_type', '')
    _is_anthropic = (provider == 'anthropic' or api_type == 'anthropic')
    _is_openai = (provider == 'openai' or api_type == 'openai') and not _is_anthropic

    full_prompt = static_prompt + dynamic_prompt

    if _is_anthropic:
        sys_content = [
            {'type': 'text', 'text': static_prompt, 'cache_control': {'type': 'ephemeral'}},
            {'type': 'text', 'text': dynamic_prompt},
        ]
        return [{'role': 'system', 'content': sys_content}]
    elif _is_openai:
        return [{'role': 'system', 'content': full_prompt}]
    else:
        return [{'role': 'system', 'content': full_prompt}]


def _build_api_messages(messages, llm_config, skip_system_inject=False):
    """Convert chat history to API format with system prompt.

    Args:
        messages: Chat history list of {role, content, ...} dicts.
        llm_config: LLM configuration dict.
        skip_system_inject: If True, do NOT build DEFAULT_SYSTEM_PROMPT or inject
            .phoneide/ knowledge / AST index. Instead, use the first system message
            from `messages` as-is. This is used by sub-agents which have their own
            concise system prompt.
    """

    if skip_system_inject:
        # Sub-agent mode: use the system prompt from messages as-is (no injection)
        api_messages = []
        for msg in messages:
            role = msg.get('role', '')
            if role == 'system':
                api_messages.append({'role': 'system', 'content': msg.get('content', '')})
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

    # ── Main agent mode: build full system prompt with injections ──
    # P2-6: Check cache first (60s TTL) — fast path for repeated calls
    _sp_cache_key = _get_system_prompt_cache_key(llm_config)
    _sp_now = time.time()
    _sp_cached = _SYSTEM_PROMPT_CACHE.get(_sp_cache_key)
    if _sp_cached and (_sp_now - _sp_cached['time'] < _SYSTEM_PROMPT_CACHE_TTL):
        _static_sys_prompt = _sp_cached.get('static', _sp_cached['prompt'])
        _dynamic_sys_prompt = _sp_cached.get('dynamic', '')
        if not _dynamic_sys_prompt:
            # Legacy cache entry: whole prompt as static
            sys_prompt = _sp_cached['prompt']
            _static_sys_prompt = sys_prompt
            _dynamic_sys_prompt = ''
        log_write(f'[phoneide] Using cached system prompt (~{_sp_cached["tokens"]} tokens, age {_sp_now - _sp_cached["time"]:.0f}s)')
        # Use same provider-aware message building as cache miss path
        api_messages = _build_cached_api_messages(_static_sys_prompt, _dynamic_sys_prompt, llm_config)
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

    # Cache miss — build system prompt from scratch
    # Split into static (tool docs) and dynamic (workspace/env) parts
    # for provider-level prompt caching (Anthropic cache_control, OpenAI cache breakpoints)
    _static_sys_prompt = DEFAULT_SYSTEM_PROMPT
    _dynamic_sys_prompt = ''  # workspace, env, AST — changes per request

    custom_prompt = llm_config.get('system_prompt', '').strip()
    if custom_prompt and custom_prompt != DEFAULT_SYSTEM_PROMPT.strip():
        _static_sys_prompt += '\n\n## Additional Instructions from User\n' + custom_prompt

    # Inject project-aware workspace info and system environment
    # Pre-initialize fallback values in case config loading fails
    _ws = WORKSPACE
    _project = None
    _project_dir = os.path.realpath(WORKSPACE)

    try:
        from utils import load_config, get_system_info, IS_WINDOWS, get_default_shell, get_default_compiler
        config = load_config()
        _project = config.get('project', None)
        _ws = config.get('workspace', WORKSPACE)

        # Determine effective project directory (always defined, no scope issues)
        if _project:
            candidate = os.path.realpath(os.path.join(_ws, _project))
            if os.path.isdir(candidate):
                _project_dir = candidate
            else:
                _project_dir = os.path.realpath(_ws)
        else:
            _project_dir = os.path.realpath(_ws)

        # System environment info
        sys_env_info = f'## System Environment\n{get_system_info()}\nDefault shell: {get_default_shell()}\nDefault Python: {get_default_compiler()}\n'
        if IS_WINDOWS:
            sys_env_info += 'Note: This is a Windows system. Use Windows-compatible commands (cmd.exe/PowerShell). Use backslashes for paths in shell commands, forward slashes for file operations in code.\n'

        # Always show project directory and workspace root clearly
        if _project and os.path.isdir(os.path.join(_ws, _project)):
            workspace_info = (
                f'## Current Project & Workspace\n'
                f'- Project name: {_project}\n'
                f'- Project directory (absolute): {_project_dir}\n'
                f'- Workspace root: {_ws}\n'
                f'- Server directory: {SERVER_DIR}\n'
                f'- All file operations should be scoped to the project directory: {_project_dir}'
            )
        else:
            workspace_info = (
                f'## Current Workspace\n'
                f'- Project directory (absolute): {_project_dir}\n'
                f'- Workspace root: {_ws}\n'
                f'- Server directory: {SERVER_DIR}\n'
                f'- All file operations should be scoped to the project directory: {_project_dir}'
            )
    except Exception as e:
        log_write(f'[phoneide] Error loading workspace config: {e}')
        sys_env_info = '## System Environment\nOS: Unknown\n'
        workspace_info = (
            f'## Current Workspace\n'
            f'- Project directory (absolute): {_project_dir}\n'
            f'- Server directory: {SERVER_DIR}'
        )

    # Accumulate dynamic parts (workspace, env — changes per request)
    _dynamic_sys_prompt += f'\n\n{sys_env_info}\n\n{workspace_info}\n'

    # Inject project knowledge from .phoneide/ directory (like CLAUDE.md)
    # Semi-static: cached 30s, treated as dynamic for provider cache
    _knowledge_loaded = []  # track which files were loaded (for logging/SSE)
    _phoneide_dirs_to_check = []
    # 1. Project directory
    if _project_dir:
        _phoneide_dirs_to_check.append(_project_dir)
    # 2. Workspace root (if different from project_dir)
    if os.path.realpath(_ws) != _project_dir:
        _phoneide_dirs_to_check.append(os.path.realpath(_ws))
    # 3. SERVER_DIR's parent (for PhoneIDE self-development)
    _server_parent = os.path.dirname(SERVER_DIR)
    if _server_parent not in [os.path.realpath(d) for d in _phoneide_dirs_to_check]:
        _phoneide_dirs_to_check.append(_server_parent)

    # Check cache first (30s TTL)
    _now = time.time()
    _cache_key = tuple(_phoneide_dirs_to_check)
    _phoneide_cache = _get_phoneide_cache()
    if (_cache_key in _phoneide_cache and
            _now - _phoneide_cache[_cache_key]['time'] < 30):
        _dynamic_sys_prompt += _phoneide_cache[_cache_key]['content']
        _knowledge_loaded = _phoneide_cache[_cache_key]['files']
        log_write(f'[phoneide] Using cached .phoneide/ content ({len(_knowledge_loaded)} files)')
    else:
        for _check_dir in _phoneide_dirs_to_check:
            _phoneide_dir = os.path.join(_check_dir, '.phoneide')
            if os.path.isdir(_phoneide_dir):
                log_write(f'[phoneide] Found .phoneide/ at: {_phoneide_dir}')
                knowledge_files = [
                    ('rules.md', 'Project Rules & Guidelines'),
                    ('architecture.md', 'Project Architecture'),
                    ('conventions.md', 'Coding Conventions'),
                ]
                knowledge_parts = []
                for fname, title in knowledge_files:
                    fpath = os.path.join(_phoneide_dir, fname)
                    if os.path.isfile(fpath):
                        try:
                            with open(fpath, 'r', encoding='utf-8', errors='ignore') as f:
                                content = f.read().strip()
                            if content:
                                knowledge_parts.append(f'### {title}\n{content}')
                                _knowledge_loaded.append(fname)
                                log_write(f'[phoneide] Loaded {fname} ({len(content)} chars)')
                        except Exception as e:
                            log_write(f'[phoneide] Error reading {fpath}: {e}')
                if knowledge_parts:
                    _injected = '\n\n## Project Knowledge (from .phoneide/)\n'
                    _injected += 'The following project-specific context was loaded from .phoneide/ files. '
                    _injected += 'Use this information to follow project conventions and understand the architecture.\n\n'
                    _injected += '\n\n'.join(knowledge_parts) + '\n'
                    # Truncate to ~4000 chars to prevent system prompt bloat
                    if len(_injected) > 4000:
                        _injected = _injected[:4000] + '\n\n[... .phoneide/ content truncated — use read_file for full details ...]\n'
                        log_write(f'[phoneide] .phoneide/ content truncated to 4000 chars')
                    _dynamic_sys_prompt += _injected
                    # Store in cache
                    _phoneide_cache[_cache_key] = {
                        'content': _injected,
                        'files': list(_knowledge_loaded),
                        'time': time.time(),
                    }
                break  # Use the first .phoneide/ directory found

        if not _knowledge_loaded:
            log_write(f'[phoneide] No .phoneide/ found in: {_phoneide_dirs_to_check}')

    # Inject AST index summary if available (re-inject when index changes)
    try:
        _ast_cache_key = '_ast_injected_time'
        _last_injected_time = _get_phoneide_cache().get(_ast_cache_key, 0)
        if (not project_index.is_indexing and project_index.symbol_count > 0
                and project_index.last_index_time > _last_injected_time):
            symbols = project_index.get_all_symbols()
            # Show top-level symbols (no parent = module-level)
            top_symbols = {}
            for name, entries in symbols.items():
                for fp, d in entries:
                    if not d.get('parent'):
                        rel = os.path.relpath(fp, _ws)
                        top_symbols.setdefault(name, []).append((rel, d['kind'], d['line']))
            # Build compact summary — limit to 30 symbols to save tokens
            symbol_lines = []
            for name in sorted(top_symbols.keys())[:30]:
                locs = top_symbols[name]
                if len(locs) <= 3:
                    for rel, kind, line in locs:
                        symbol_lines.append(f'  {kind} {name} ({rel}:{line})')
                else:
                    symbol_lines.append(f'  {name} ({len(locs)} definitions)')
            if symbol_lines:
                _dynamic_sys_prompt += f'\n\n## Project Symbols ({project_index.file_count} files, {project_index.symbol_count} symbols)\n'
                _dynamic_sys_prompt += 'Use find_definition/find_references for detailed lookup.\n'
                _dynamic_sys_prompt += '\n'.join(symbol_lines) + '\n'
                if project_index.symbol_count > 30:
                    _dynamic_sys_prompt += f'  ... and {project_index.symbol_count - 30} more symbols (use find_definition to look up)\n'
                log_write(f'[phoneide] AST index injected: {project_index.file_count} files, {project_index.symbol_count} symbols')
                _get_phoneide_cache()[_ast_cache_key] = project_index.last_index_time
    except Exception as e:
        log_write(f'[phoneide] AST index injection error: {e}')

    # Merge for local cache and token estimation
    sys_prompt = _static_sys_prompt + _dynamic_sys_prompt

    # P2-1: Trim system prompt to token budget (trim dynamic part preferentially)
    sys_prompt = _trim_system_prompt_to_budget(sys_prompt)
    # Re-split after trimming in case dynamic was cut
    _dynamic_sys_prompt = sys_prompt[len(_static_sys_prompt):]

    # P2-6: Store in cache for subsequent calls (60s TTL)
    _sp_tokens = _estimate_tokens(sys_prompt)
    _SYSTEM_PROMPT_CACHE[_sp_cache_key] = {
        'prompt': sys_prompt,
        'static': _static_sys_prompt,
        'dynamic': _dynamic_sys_prompt,
        'tokens': _sp_tokens,
        'time': time.time(),
    }
    # Evict old entries (keep cache size bounded)
    if len(_SYSTEM_PROMPT_CACHE) > 10:
        oldest_key = min(_SYSTEM_PROMPT_CACHE, key=lambda k: _SYSTEM_PROMPT_CACHE[k]['time'])
        del _SYSTEM_PROMPT_CACHE[oldest_key]
    log_write(f'[phoneide] System prompt built and cached (~{_sp_tokens} tokens)')

    # Build api_messages with provider-level prompt caching support
    api_messages = _build_cached_api_messages(_static_sys_prompt, _dynamic_sys_prompt, llm_config)
    provider = llm_config.get('provider', '')
    api_type = llm_config.get('api_type', '')
    if provider == 'anthropic' or api_type == 'anthropic':
        log_write(f'[phoneide] Anthropic cache_control enabled (static: ~{_estimate_tokens(_static_sys_prompt)} tokens)')
    elif provider == 'openai' or api_type == 'openai':
        log_write(f'[phoneide] OpenAI system message mode (static: ~{_estimate_tokens(_static_sys_prompt)} tokens)')

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

        # OpenAI reasoning models (o1, o3, o4-mini, codex, etc.)
        if ('o1' in model_lower or 'o3' in model_lower or 'o4' in model_lower or
            'codex' in model_lower):
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

        # DeepSeek reasoning models (R1, etc.)
        elif 'deepseek' in model_lower or 'reasoner' in model_lower:
            payload.setdefault('temperature', 0.6)

        # Step (深度求索) reasoning models
        elif 'step' in model_lower:
            payload.setdefault('temperature', 0.6)

        # GLM (Z.ai) reasoning models
        elif 'glm' in model_lower:
            payload.setdefault('temperature', 0.6)

        # QwQ / Kimi / other reasoning models
        elif 'qwq' in model_lower or 'kimi' in model_lower or 'think' in model_lower or 'reasoning' in model_lower:
            pass  # These models reason by default, no special params needed

    try:
        url, headers = _get_llm_endpoint(llm_config, model)
    except Exception as e:
        raise Exception(f'LLM config error: {e}')

    headers = headers or {'Content-Type': 'application/json'}

    req = urllib.request.Request(url, json.dumps(payload).encode(), headers=headers, method='POST')
    print(f'[LLM] Calling: {url}')
    print(f'[LLM] Model: {model}, Temperature: {temperature}, MaxTokens: {max_tokens}, Reasoning: {reasoning}')
    if reasoning:
        # Log which reasoning branch was matched
        model_lower = model.lower()
        provider = llm_config.get('provider', '')
        if 'o1' in model_lower or 'o3' in model_lower or 'o4' in model_lower or 'codex' in model_lower:
            print(f'[LLM] Reasoning branch: OpenAI (reasoning_effort=high)')
        elif provider == 'anthropic' or 'anthropic' in llm_config.get('api_type', ''):
            print(f'[LLM] Reasoning branch: Anthropic thinking')
        elif 'deepseek' in model_lower or 'reasoner' in model_lower:
            print(f'[LLM] Reasoning branch: DeepSeek (temp=0.6)')
        elif 'step' in model_lower:
            print(f'[LLM] Reasoning branch: Step (temp=0.6)')
        elif 'glm' in model_lower:
            print(f'[LLM] Reasoning branch: GLM (temp=0.6)')
        elif 'qwq' in model_lower or 'kimi' in model_lower or 'think' in model_lower or 'reasoning' in model_lower:
            print(f'[LLM] Reasoning branch: Generic (no special params)')
        else:
            print(f'[LLM] Reasoning enabled but no model matched — model="{model}"')
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
                        # Pass through reasoning_content for DeepSeek/QwQ/Step/GLM/Kimi/reasoning models
                        # The delta dict may contain 'reasoning_content' field
                        if 'reasoning_content' in delta:
                            delta['_reasoning'] = True
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
                        if 'reasoning_content' in delta:
                            delta['_reasoning'] = True
                        yield delta
                except (json.JSONDecodeError, KeyError):
                    pass

# ==================== Context Window Management ====================
def _estimate_tokens(text):
    """Estimate token count. Uses tiktoken if available, otherwise heuristic.
    
    Chinese/CJK ~1.5 tokens per character, Latin ~0.25 tokens per character.
    Fallback is more accurate than len//4 for mixed-language content.
    """
    if not text:
        return 0
    try:
        import tiktoken
        _enc = tiktoken.get_encoding("cl100k_base")
        return len(_enc.encode(text))
    except Exception:
        pass
    # Heuristic: CJK chars cost ~1.5 tokens, other chars ~0.25 tokens
    cjk = sum(1 for c in text if '\u4e00' <= c <= '\u9fff' or '\u3400' <= c <= '\u4dbf')
    return int(cjk * 1.5 + (len(text) - cjk) * 0.25)


def _get_context_budget(llm_config):
    """Get the context window budget for compression.
    
    Uses the model's max_context (input context window size) minus safety margins,
    falling back to max_tokens * 10 if max_context is not configured.
    """
    max_context = llm_config.get('max_context', 0)
    if max_context > 0:
        max_output = llm_config.get('max_tokens', 4096)
        return max(max_context - max_output - 4000, 8000)  # safety margin + minimum floor
    return llm_config.get('max_tokens', 4096) * 10


# ==================== AI-Powered History Summarization ====================
def _ai_summarize_messages(messages, llm_config):
    """Use the LLM to generate a concise summary of conversation messages.
    
    P2-2: Supports incremental summarization — if an existing summary is found
    in the messages, it's used as a base context and only new messages since
    the summary are summarized and merged. This avoids re-summarizing the
    entire conversation each time.
    
    Returns summary text string, or None on failure.
    This is used by _compress_context when llm_config is provided.
    """
    if not messages or not llm_config or len(messages) < 3:
        return None
    
    # P2-2: Find existing summary to use as base context
    existing_summary = None
    summary_end_idx = 0
    for i, msg in enumerate(messages):
        role = msg.get('role', '')
        content = (msg.get('content') or '')
        if role == 'user' and (content.startswith('[Previous Conversation Summary]') or
                                content.startswith('[Conversation Summary]') or
                                content.startswith('Earlier conversation summary')):
            # Extract the summary text (after the prefix line)
            for prefix in ('[Previous Conversation Summary]\n', '[Conversation Summary]\n', 'Earlier conversation summary:\n'):
                if content.startswith(prefix):
                    existing_summary = content[len(prefix):].strip()
                    break
            else:
                existing_summary = content.strip()
            summary_end_idx = i + 1
            break
    
    # Only summarize messages after the existing summary (incremental)
    if existing_summary and summary_end_idx < len(messages) - 2:
        messages_to_summarize = messages[summary_end_idx:]
        if len(messages_to_summarize) < 3:
            # Not enough new messages to justify re-summarization
            return existing_summary
    elif existing_summary:
        # Existing summary covers all messages — return as-is
        return existing_summary
    else:
        messages_to_summarize = messages
    
    # Build compact representation of messages for summarization
    compact = []
    for msg in messages_to_summarize:
        role = msg.get('role', '')
        content = (msg.get('content') or '')
        name = msg.get('name', '')
        
        # Skip existing summary messages to avoid re-summarizing summaries
        if role == 'user' and (content.startswith('[Previous Conversation Summary]') or
                                content.startswith('[Conversation Summary]') or
                                content.startswith('Earlier conversation summary')):
            continue
        
        if role == 'user':
            compact.append(f'[User]: {content[:400]}')
        elif role == 'assistant':
            tool_calls = msg.get('tool_calls')
            if tool_calls:
                tools = ', '.join(t.get('function', {}).get('name', '') for t in tool_calls)
                text_part = f' "{content[:200]}"' if content else ''
                compact.append(f'[Assistant]: Called [{tools}]{text_part}')
            else:
                compact.append(f'[Assistant]: {content[:300]}')
        elif role == 'tool':
            compact.append(f'[Tool/{name}]: {content[:200]}')
    
    if not compact:
        return existing_summary
    
    conversation_text = '\n'.join(compact)
    
    # Build prompt based on whether we have an existing summary
    if existing_summary:
        summary_prompt = (
            "You are updating a conversation summary. Below is the existing summary followed by "
            "NEW conversation that happened after it. Update the summary to incorporate the new information.\n\n"
            "Focus on:\n"
            "1. What the user asked for (goals and requirements)\n"
            "2. Key files modified (full file paths and what was changed)\n"
            "3. Important commands run and their results\n"
            "4. Errors encountered and how they were resolved\n"
            "5. Current state of work (what is done, what remains)\n\n"
            "Preserve all file paths, code snippets, function names, and technical details. "
            "Be concise but complete. Keep relevant older context and add new information.\n\n"
            f"=== EXISTING SUMMARY ===\n{existing_summary}\n\n"
            f"=== NEW CONVERSATION (to incorporate) ===\n{conversation_text}\n\n"
            "Provide the UPDATED summary:"
        )
    else:
        summary_prompt = (
            "Summarize the following conversation between a user and an AI coding assistant. "
            "Focus on:\n"
            "1. What the user asked for (goals and requirements)\n"
            "2. Key files modified (full file paths and what was changed)\n"
            "3. Important commands run and their results\n"
            "4. Errors encountered and how they were resolved\n"
            "5. Current state of work (what is done, what remains)\n\n"
            "Preserve all file paths, code snippets, function names, and technical details. "
            "Be concise but complete. This summary will replace the original conversation.\n\n"
            f"Conversation to summarize:\n{conversation_text}"
        )
    
    try:
        url, headers = _get_llm_endpoint(llm_config, llm_config.get('model'))
        headers = headers or {'Content-Type': 'application/json'}
        
        payload = {
            'model': llm_config.get('model'),
            'messages': [{'role': 'user', 'content': summary_prompt}],
            'temperature': 0.3,  # Low temperature for factual summarization
            'max_tokens': min(2000, llm_config.get('max_tokens', 4096)),
        }
        
        req = urllib.request.Request(url, json.dumps(payload).encode(), headers=headers, method='POST')
        with _urllib_opener.open(req, timeout=60) as resp:
            resp_body = resp.read().decode()
            result = json.loads(resp_body)
            summary = result.get('choices', [{}])[0].get('message', {}).get('content', '').strip()
            if summary:
                mode = 'incremental' if existing_summary else 'full'
                print(f'[AI-SUMMARY] Generated {len(summary)} char {mode} summary from {len(messages_to_summarize)} messages')
            return summary
    except Exception as e:
        print(f'[AI-SUMMARY] Failed to generate summary: {e}')
        return existing_summary  # Return existing summary on failure instead of None


# ==================== Self-Correction Loop ====================
MAX_SELF_CORRECTION_RETRIES = 3

# Tools that should trigger self-correction on failure
_SELF_CORRECTION_TOOLS = frozenset({
    'write_file', 'edit_file', 'run_command', 'install_package',
})

# Error patterns to detect in tool results
_ERROR_PATTERNS = [
    'error:', 'error：', 'traceback (most recent call last)',
    'syntaxerror', 'typeerror', 'valueerror', 'nameerror',
    'importerror', 'modulenotfounderror', 'filenotfounderror',
    'keyerror', 'attributeerror', 'indexerror', 'runtimeerror',
    'permission denied', 'no such file or directory',
    'command not found', 'returned non-zero', 'non-zero exit', 'exit code',
    'segmentation fault', 'connectionrefusederror', 'connectionerror',
    'oserror', 'json.decoder.jsondecodeerror',
]


def _is_tool_result_error(tool_name, result_str):
    """Check if a tool result indicates a failure that should trigger self-correction."""
    if tool_name not in _SELF_CORRECTION_TOOLS:
        return False
    if not result_str:
        return False
    
    # Skip successful-looking results
    result_lower = result_str[:800].lower()
    
    # If result starts with 'Error' or 'error', it's definitely an error
    if result_lower.startswith('error'):
        return True
    
    # Check for error patterns
    for pattern in _ERROR_PATTERNS:
        if pattern in result_lower:
            return True
    
    return False


def _build_self_correction_hint(failed_tools):
    """Build a hint message for the LLM when self-correction is needed.
    
    Args:
        failed_tools: list of (tool_name, args, result_str) tuples
    """
    hint = "[Self-Correction Required] The following tool calls encountered errors:\n\n"
    for name, args, result in failed_tools:
        args_str = json.dumps(args, ensure_ascii=False)[:300] if args else 'N/A'
        # Extract the most relevant error info from the result
        error_excerpt = result[:500]
        hint += f"**{name}** (args: {args_str}):\n"
        hint += f"Error output: {error_excerpt}\n\n"
    
    hint += (
        "Please analyze these errors and retry with a corrected approach:\n"
        "1. Read the relevant file(s) to understand the current state before fixing\n"
        "2. Identify the root cause from the error message\n"
        "3. Apply a corrected approach (different edit, different command, fix imports, etc.)\n"
        "4. Re-run to verify the fix works\n"
    )
    return hint


def _has_tool_calls(msg):
    """Check if a message has tool calls."""
    return bool(msg and msg.get('tool_calls'))


def _check_self_correction(context, batch_results, self_corrections):
    """P2-5: Shared self-correction check used by both agent loops.

    Checks if any tool results indicate failures and, if so, builds a
    correction hint and appends it to context.

    Args:
        context: The conversation context list (modified in-place).
        batch_results: List of (tool_name, args, ok, result_str) tuples.
        self_corrections: Current self-correction counter.

    Returns:
        (updated_self_corrections, hint_or_None) tuple.
        If hint is not None, it has already been appended to context.
    """
    if self_corrections >= MAX_SELF_CORRECTION_RETRIES:
        return self_corrections, None

    failed = [(n, a, r) for n, a, ok, r in batch_results
              if not ok or _is_tool_result_error(n, r)]
    if not failed:
        return self_corrections, None

    self_corrections += 1
    hint = _build_self_correction_hint(failed)
    context.append({'role': 'user', 'content': hint, 'time': datetime.now().isoformat()})

    print(f'[SELF-CORRECT] #{self_corrections}: {len(failed)} tool(s) failed')
    return self_corrections, hint

def _compress_context(messages, max_tokens=None, llm_config=None):
    """Smart context compression with AI summarization and code-change preservation.
    
    Strategy:
    1. Preserve write_file/edit_file results as "KEY CODE CHANGES" 
    2. AI-powered summarization of older messages (when llm_config is provided)
    3. Differentiated compression limits by tool type
    4. Two-stage compression (gentle → aggressive)
    5. Informative size markers instead of silent truncation
    
    Args:
        messages: List of chat messages.
        max_tokens: Maximum token budget for the compressed context.
        llm_config: If provided, uses AI summarization for older messages.
                  Only effective when there are enough older messages to summarize.
    
    Returns (messages, was_compressed) tuple.
    """
    if not messages:
        return messages, False
    max_tokens = max_tokens or 60000
    total = sum(_estimate_tokens(m.get('content', '') or '') for m in messages)
    if total <= max_tokens:
        return messages, False

    was_compressed = True
    original_total = total

    # ── Stage 1: Extract key code changes (write_file/edit_file results) ──
    code_changes = []
    for msg in messages:
        if msg.get('role') == 'tool' and msg.get('name') in ('write_file', 'edit_file'):
            content = msg.get('content', '') or ''
            if content and 'Error' not in content[:20]:
                code_changes.append(content[:500])

    # ── Stage 2: Split into older/recent ──
    user_indices = [i for i, m in enumerate(messages) if m.get('role') == 'user']
    if len(user_indices) >= 2:
        split_idx = user_indices[-2]
    else:
        split_idx = max(0, len(messages) - 6)

    older = messages[:split_idx]
    recent = messages[split_idx:]

    # ── Stage 3: Build smart summary of older messages ──
    # Try AI-powered summarization first (only when llm_config is provided and enough messages)
    ai_summary = None
    if llm_config and len(older) >= 4:
        ai_summary = _ai_summarize_messages(older, llm_config)
    
    if ai_summary:
        # Use AI-generated summary — much better context preservation
        summary_content = f'[Previous Conversation Summary]\n{ai_summary}'
        if code_changes:
            summary_content += '\n\nKEY CODE CHANGES (preserved):\n' + '\n'.join(f'  - {c[:300]}' for c in code_changes[:5])
        summary_msg = {'role': 'user', 'content': summary_content}
        print(f'[CONTEXT] Using AI-generated summary ({len(ai_summary)} chars) for {len(older)} older messages')
    else:
        # Fallback to mechanical summary (original logic)
        summary_parts = []
        for msg in older:
            role = msg.get('role', '')
            content = msg.get('content') or ''
            if role == 'user':
                summary_parts.append(f'[User]: {content[:300]}')
            elif role == 'assistant':
                text = content[:300] if content else '(tool calls only)'
                summary_parts.append(f'[Assistant]: {text}')
            elif role == 'tool':
                name = msg.get('name', 'tool')
                # Differentiated limits by tool type
                if name in ('write_file', 'edit_file'):
                    summary_parts.append(f'[Tool {name}]: {_truncate(content, 200)}')
                elif name in ('read_file', 'grep_code', 'search_files', 'glob_files', 'find_definition', 'find_references'):
                    summary_parts.append(f'[Tool {name}]: {_truncate(content, 150)}')
                elif name in ('run_command', 'install_package'):
                    summary_parts.append(f'[Tool {name}]: {_truncate(content, 200)}')
                elif name in ('todo_write', 'todo_read'):
                    summary_parts.append(f'[Tool {name}]: {_truncate(content, 300)}')
                else:
                    summary_parts.append(f'[Tool {name}]: {_truncate(content, 100)}')
    
        summary = 'Earlier conversation summary:\n'
        if code_changes:
            summary += 'KEY CODE CHANGES (preserved from earlier):\n' + '\n'.join(f'  • {c}' for c in code_changes[:5]) + '\n\n'
        summary += '\n'.join(summary_parts[-15:])
        summary_msg = {'role': 'user', 'content': summary}

    # ── Stage 4: Gentle compression — differentiated tool limits ──
    TOOL_LIMITS_GENTLE = {
        'read_file': 5000, 'glob_files': 2000, 'grep_code': 3000,
        'search_files': 3000, 'file_structure': 3000,
        'find_definition': 3000, 'find_references': 2000,
        'run_command': 3000, 'web_fetch': 2000,
        'delegate_task': 3000,
        'parallel_tasks': 6000,
        'todo_write': 2000, 'todo_read': 2000,
    }
    TOOL_LIMITS_DEFAULT = 4000

    compressed_recent = []
    for msg in recent:
        # Shallow copy to avoid mutating original messages list
        msg = dict(msg)
        if msg.get('role') == 'tool':
            content = msg.get('content') or ''
            name = msg.get('name', '')
            limit = TOOL_LIMITS_GENTLE.get(name, TOOL_LIMITS_DEFAULT)
            if len(content) > limit:
                msg['content'] = content[:limit] + f'\n[compressed: {len(content)}→{limit} chars]'
        compressed_recent.append(msg)

    all_msgs = [summary_msg] + compressed_recent
    total2 = sum(_estimate_tokens(m.get('content', '') or '') for m in all_msgs)

    # ── Stage 5: Aggressive compression if still too large ──
    if total2 > max_tokens:
        for msg in all_msgs:
            if msg.get('role') == 'tool':
                content = msg.get('content', '')
                if len(content) > 1500:
                    msg['content'] = content[:1500] + f'\n[compressed: {len(content)}→1500 chars]'

    total3 = sum(_estimate_tokens(m.get('content', '') or '') for m in all_msgs)
    if total3 > max_tokens:
        user_indices2 = [i for i, m in enumerate(all_msgs) if m.get('role') == 'user']
        if len(user_indices2) >= 1:
            keep_from = user_indices2[-1]
            kept = all_msgs[keep_from:]
            for msg in kept:
                if msg.get('role') == 'tool':
                    content = msg.get('content', '')
                    if len(content) > 800:
                        msg['content'] = content[:800] + f'\n[compressed: {len(content)}→800 chars]'
            all_msgs = [summary_msg] + kept

    # ── Stage 6: Ultimate fallback ──
    total4 = sum(_estimate_tokens(m.get('content', '') or '') for m in all_msgs)
    if total4 > max_tokens:
        minimal = [summary_msg]
        for msg in all_msgs[-2:]:
            content = msg.get('content', '') or ''
            minimal.append(dict(msg, content=content[:200] + ('...' if len(content) > 200 else '')))
        all_msgs = minimal

    final_total = sum(_estimate_tokens(m.get('content', '') or '') for m in all_msgs)
    print(f'[CONTEXT] Compressed: {_estimate_tokens(str(original_total*4))}→{final_total} tokens '
          f'({len(messages)}→{len(all_msgs)} messages, saved code changes: {len(code_changes)})')
    return all_msgs, was_compressed

# ==================== Agent Loop ====================
MAX_AGENT_ITERATIONS = 100  # Increased from 15 for complex tasks
MAX_ITERATION_RETRIES = 10

# Valid tool names for fallback detection
_TOOL_NAMES = frozenset(
    f.get('function', {}).get('name', '')
    for f in AGENT_TOOLS
)

def _try_parse_tool_calls_from_content(content):
    """Try to parse tool calls from LLM text content.

    Some models (especially non-OpenAI-compatible ones) return tool calls as
    JSON text in the content field instead of using the proper tool_calls field.
    This function detects and converts them to the standard tool_calls format.

    Returns list of tool_call dicts (OpenAI format) or None if not detected.
    """
    if not content or not content.strip():
        return None

    import re
    parsed_calls = []

    # Pattern 1: Standalone JSON objects with "name" and "arguments"
    # Matches: {"name": "run_command", "arguments": {"command": "...", ...}}
    standalone_pattern = re.compile(
        r'\{\s*"name"\s*:\s*"(\w+)"\s*,\s*"arguments"\s*:\s*',
        re.DOTALL
    )
    matches = list(standalone_pattern.finditer(content))
    if matches:
        # Try to extract complete JSON objects for each match
        for match in matches:
            start = match.start()
            # Find the matching closing brace
            depth = 0
            end = start
            for i in range(start, len(content)):
                if content[i] == '{':
                    depth += 1
                elif content[i] == '}':
                    depth -= 1
                    if depth == 0:
                        end = i + 1
                        break
            json_str = content[start:end]
            try:
                obj = json.loads(json_str)
                name = obj.get('name', '')
                arguments = obj.get('arguments', {})
                if name and name in _TOOL_NAMES:
                    parsed_calls.append({
                        'id': f'call_parsed_{name}',
                        'type': 'function',
                        'function': {
                            'name': name,
                            'arguments': json.dumps(arguments, ensure_ascii=False) if isinstance(arguments, dict) else str(arguments),
                        },
                    })
            except (json.JSONDecodeError, KeyError, TypeError):
                continue

    # Pattern 2: Code-fenced tool calls: ```json\n{"name":...}\n```
    if not parsed_calls:
        fenced_pattern = re.compile(
            r'```(?:json|tool_calls?)\s*\n(\{[^`]*\})\s*\n```',
            re.DOTALL
        )
        for m in fenced_pattern.finditer(content):
            try:
                obj = json.loads(m.group(1))
                name = obj.get('name', '')
                arguments = obj.get('arguments', {})
                if name and name in _TOOL_NAMES:
                    parsed_calls.append({
                        'id': f'call_parsed_{name}',
                        'type': 'function',
                        'function': {
                            'name': name,
                            'arguments': json.dumps(arguments, ensure_ascii=False) if isinstance(arguments, dict) else str(arguments),
                        },
                    })
            except (json.JSONDecodeError, KeyError, TypeError):
                continue

    # Pattern 3: Markdown code block with tool_call (without json/lang marker)
    if not parsed_calls:
        # {"name": "...", "arguments": {...}} on its own line
        line_pattern = re.compile(
            r'^\s*\{"name"\s*:\s*"(\w+)"\s*,\s*"arguments"\s*:\s*\{.*\}\s*\}\s*$',
            re.MULTILINE | re.DOTALL
        )
        for m in line_pattern.finditer(content):
            try:
                obj = json.loads(m.group(0).strip())
                name = obj.get('name', '')
                arguments = obj.get('arguments', {})
                if name and name in _TOOL_NAMES:
                    parsed_calls.append({
                        'id': f'call_parsed_{name}',
                        'type': 'function',
                        'function': {
                            'name': name,
                            'arguments': json.dumps(arguments, ensure_ascii=False) if isinstance(arguments, dict) else str(arguments),
                        },
                    })
            except (json.JSONDecodeError, KeyError, TypeError):
                continue

    if parsed_calls:
        print(f'[AGENT] Parsed {len(parsed_calls)} tool call(s) from content text (model doesn\'t use tool_calls field)')
        return parsed_calls
    return None


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

    # Reset todo list for each new conversation (prevent cross-session leakage)
    with _active_todos['lock']:
        _active_todos['todos'] = []

    user_msg = {'role': 'user', 'content': user_message, 'time': datetime.now().isoformat()}
    history.append(user_msg)

    # Compress context if needed (with AI summarization for history-level compression)
    context, _ = _compress_context(history, max_tokens=_get_context_budget(llm_config), llm_config=llm_config)

    def _emit(event):
        if stream_callback:
            stream_callback(event)

    final_content = ''
    total_iterations = 0
    all_tool_calls = []
    self_corrections = 0  # Track self-correction retries

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

        # Try parallel execution for read-only tools
        parallel_results = _execute_tools_parallel(tool_calls_raw)

        if parallel_results is not None:
            # Parallel execution succeeded — emit all results
            _batch_results = []
            for idx, tool_name, ok, result_str, elapsed, tool_call_id in parallel_results:
                all_tool_calls.append({'name': tool_name})
                _emit({'type': 'tool_start', 'tool': tool_name})
                _emit({'type': 'tool_result', 'tool': tool_name, 'ok': ok,
                       'result': _truncate(result_str, 30000), 'elapsed': round(elapsed, 2)})
                context.append({'role': 'tool', 'tool_call_id': tool_call_id,
                                'name': tool_name, 'content': result_str,
                                'time': datetime.now().isoformat()})
                _batch_results.append((tool_name, {}, ok, result_str))
            context, _ = _compress_context(context, max_tokens=_get_context_budget(llm_config))
            
            # === Self-Correction Check (parallel batch) ===
            self_corrections, _hint = _check_self_correction(context, _batch_results, self_corrections)
            if _hint:
                _emit({'type': 'thinking', 'content': f'Self-correction #{self_corrections}: Detected errors, retrying...'})
        else:
            # Sequential execution (mixed read/write tools or single tool)
            _batch_results = []
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
                _batch_results.append((tool_name, tool_args, ok, result_str))

                # Re-check context size and compress if needed
                context, _ = _compress_context(context, max_tokens=_get_context_budget(llm_config))
            
            # === Self-Correction Check (sequential batch) ===
            self_corrections, _hint = _check_self_correction(context, _batch_results, self_corrections)
            if _hint:
                _emit({'type': 'thinking', 'content': f'Self-correction #{self_corrections}: Detected errors, retrying...'})

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

    # Compress context if needed (with AI summarization for history-level compression)
    context, _ = _compress_context(history, max_tokens=_get_context_budget(llm_config), llm_config=llm_config)

    # Trigger background AST index if stale or empty (runs in daemon thread)
    try:
        from utils import load_config
        cfg = load_config()
        ws = cfg.get('workspace', WORKSPACE)
        prj = cfg.get('project', None)
        project_root = os.path.join(ws, prj) if prj else ws
        if os.path.isdir(project_root) and (project_index.file_count == 0 or
                (time.time() - project_index.last_index_time > 300)):
            threading.Thread(target=project_index.index_project,
                           args=(project_root,), kwargs={'max_files': 1000, 'max_time': 10},
                           daemon=True).start()
    except Exception:
        pass

    # Reset todo list for each new conversation (prevent cross-session leakage)
    with _active_todos['lock']:
        _active_todos['todos'] = []

    # Pre-save history before starting the loop so retry can recover even if
    # the very first LLM call fails (before any tool execution).
    save_chat_history(history)
    if conv_id:
        save_conversation(conv_id, history)

    # Check and report .phoneide/ project knowledge loading status
    try:
        from utils import load_config as _load_cfg
        _cfg = _load_cfg()
        _ws_check = _cfg.get('workspace', WORKSPACE)
        _prj_check = _cfg.get('project', None)
        _pdir_check = os.path.join(_ws_check, _prj_check) if _prj_check else _ws_check
        if not os.path.isdir(_pdir_check):
            _pdir_check = _ws_check
        _phoneide_check = os.path.join(_pdir_check, '.phoneide')
        if not os.path.isdir(_phoneide_check):
            _phoneide_check = os.path.join(os.path.dirname(SERVER_DIR), '.phoneide')
        if os.path.isdir(_phoneide_check):
            _md_files = [f for f in ['rules.md', 'architecture.md', 'conventions.md'] if os.path.isfile(os.path.join(_phoneide_check, f))]
            if _md_files:
                yield f"data: {json.dumps({'type': 'thinking', 'content': f'\U0001f4c2 .phoneide/ loaded: {', '.join(_md_files)}'})}\n\n"
                log_write(f'[phoneide] SSE: .phoneide/ loaded from {_phoneide_check}: {_md_files}')
            else:
                yield f"data: {json.dumps({'type': 'thinking', 'content': '\u26a0\ufe0f .phoneide/ exists but has no content files'})}\n\n"
                log_write(f'[phoneide] SSE: .phoneide/ empty at {_phoneide_check}')
        else:
            yield f"data: {json.dumps({'type': 'thinking', 'content': '\u26a0\ufe0f .phoneide/ not found — no project knowledge loaded'})}\n\n"
            log_write(f'[phoneide] SSE: .phoneide/ not found, checked {_pdir_check} and {os.path.dirname(SERVER_DIR)}')
    except Exception as _e:
        log_write(f'[phoneide] SSE check error: {_e}')

    final_content = ''
    total_iterations = 0
    accumulated_text = ''
    tool_calls_in_progress = []
    loop_completed_normally = False
    self_corrections = 0  # Track self-correction retries
    # Buffer for streaming tool_calls assembly
    current_tool_calls = []
    current_tool_call_idx = {}
    current_args_buffer = {}

    for iteration in range(MAX_AGENT_ITERATIONS):
        total_iterations = iteration + 1

        # Check cancellation before each iteration
        if _active_task.get('cancelled'):
            yield f"data: {json.dumps({'type': 'error', 'content': 'Task cancelled by user.'})}\n\n"
            yield f"data: {json.dumps({'type': 'done', 'completed': False, 'iterations': total_iterations})}\n\n"
            return

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
                reasoning_text = ''  # accumulate reasoning/thinking content
                reasoning_ended = False
                for delta in _call_llm_stream_raw(context, llm_config):
                    # Check cancellation during LLM streaming
                    if _active_task.get('cancelled'):
                        yield f"data: {json.dumps({'type': 'error', 'content': 'Task cancelled by user.'})}\n\n"
                        yield f"data: {json.dumps({'type': 'done', 'completed': False, 'iterations': total_iterations})}\n\n"
                        return

                    # Capture finish_reason
                    fr = delta.get('_finish_reason')
                    if fr:
                        finish_reason = fr

                    # Handle reasoning_content (DeepSeek-R1, QwQ, Step, GLM, Kimi, etc.)
                    reasoning_chunk = delta.get('reasoning_content')
                    content_chunk = delta.get('content') or None

                    if reasoning_chunk:
                        reasoning_text += reasoning_chunk
                        yield f"data: {json.dumps({'type': 'reasoning', 'content': reasoning_chunk})}\n\n"
                        # Don't skip — also check for content below

                    # Signal reasoning_end when we transition from reasoning to content
                    if reasoning_text and not reasoning_ended and content_chunk:
                        yield f"data: {json.dumps({'type': 'reasoning_end'})}\n\n"
                        reasoning_ended = True

                    # Handle text content
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

                # If reasoning was accumulated but reasoning_end not yet signaled
                if reasoning_text and not reasoning_ended:
                    yield f"data: {json.dumps({'type': 'reasoning_end'})}\n\n"

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

        # ── Fallback: parse tool calls from content if model doesn't use tool_calls field ──
        # Some models (e.g. non-OpenAI-compatible) return tool calls as JSON in content instead
        # of using the proper tool_calls field. Detect and parse them.
        if not tool_calls_raw and content.strip():
            _parsed_from_content = _try_parse_tool_calls_from_content(content)
            if _parsed_from_content:
                tool_calls_raw = _parsed_from_content
                content = ''
                # Remove the leaked tool call text from accumulated display
                for _tc in _parsed_from_content:
                    _tc_json = json.dumps(_tc, ensure_ascii=False)
                    accumulated_text = accumulated_text.replace(_tc_json, '').strip()
                # Clean up common wrapper patterns
                import re as _re
                accumulated_text = _re.sub(r'```json\s*\n?\s*```', '', accumulated_text).strip()
                accumulated_text = _re.sub(r'```tool_calls?\s*\n?\s*```', '', accumulated_text).strip()
                if not accumulated_text.strip():
                    accumulated_text = ''

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

        # Check cancellation before tool execution
        if _active_task.get('cancelled'):
            yield f"data: {json.dumps({'type': 'error', 'content': 'Task cancelled by user.'})}\n\n"
            yield f"data: {json.dumps({'type': 'done', 'completed': False, 'iterations': total_iterations})}\n\n"
            return

        # Try parallel execution for read-only tools
        parallel_results = _execute_tools_parallel(tool_calls_raw)

        if parallel_results is not None:
            # Parallel execution
            _batch_results = []
            for idx, tool_name, ok, result_str, elapsed, tool_call_id in parallel_results:
                tool_calls_in_progress.append({'name': tool_name})
                yield f"data: {json.dumps({'type': 'tool_start', 'tool': tool_name})}\n\n"
                yield f"data: {json.dumps({'type': 'tool_result', 'tool': tool_name, 'ok': ok, 'result': _truncate(result_str, 30000), 'elapsed': round(elapsed, 2), 'max_iterations': MAX_AGENT_ITERATIONS})}\n\n"
                tool_msg = {'role': 'tool', 'tool_call_id': tool_call_id,
                            'name': tool_name, 'content': result_str,
                            'time': datetime.now().isoformat()}
                context.append(tool_msg)
                history.append(tool_msg)
                _batch_results.append((tool_name, {}, ok, result_str))
            context, _ = _compress_context(context, max_tokens=_get_context_budget(llm_config))
            
            # === Self-Correction Check (parallel batch) ===
            self_corrections, _hint = _check_self_correction(context, _batch_results, self_corrections)
            if _hint:
                yield f"data: {json.dumps({'type': 'thinking', 'content': f'Self-correction #{self_corrections}: Detected errors in {len(_batch_results)} tool(s), analyzing and retrying...'})}\n\n"
        else:
            # Sequential execution (mixed read/write tools or single tool)
            _batch_results = []
            for tc in tool_calls_raw:
                # Check cancellation before each tool
                if _active_task.get('cancelled'):
                    yield f"data: {json.dumps({'type': 'error', 'content': 'Task cancelled by user.'})}\n\n"
                    yield f"data: {json.dumps({'type': 'done', 'completed': False, 'iterations': total_iterations})}\n\n"
                    return

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
                _batch_results.append((tool_name, tool_args, ok, result_str))

                # Save after each tool so refresh mid-iteration preserves partial progress
                save_chat_history(history)
                if conv_id:
                    save_conversation(conv_id, history)

                # Compress context if needed
                context, _ = _compress_context(context, max_tokens=_get_context_budget(llm_config))
            
            # === Self-Correction Check (sequential batch) ===
            self_corrections, _hint = _check_self_correction(context, _batch_results, self_corrections)
            if _hint:
                yield f"data: {json.dumps({'type': 'thinking', 'content': f'Self-correction #{self_corrections}: Detected errors, analyzing and retrying...'})}\n\n"

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
        _active_task['cancelled'] = False
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
                # Check cancellation before enqueueing
                with _active_task['lock']:
                    if _active_task.get('cancelled'):
                        cancel_event = f"data: {json.dumps({'type': 'cancelled', 'content': 'Task cancelled by user.'})}\n\n"
                        event_queue.put(cancel_event)
                        _active_task['event_buffer'].append(cancel_event)
                        break
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
            # Signal completion and mark task as no longer running
            with _active_task['lock']:
                _active_task['running'] = False
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
                    # Full cleanup when last subscriber disconnects
                    _active_task['running'] = False
                    _active_task['cancelled'] = False
                    _active_task['conv_id'] = None
                    _active_task['message'] = None
                    _active_task['started_at'] = None
                    _active_task['event_queue'] = None
                    _active_task['event_buffer'] = None
                    _active_task['thread'] = None

    return Response(generate(), mimetype='text/event-stream',
                    headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})


@bp.route('/api/chat/task/stop', methods=['POST'])
def stop_task():
    """Request cancellation of the currently running AI task."""
    with _active_task['lock']:
        if not _active_task['running']:
            return jsonify({'error': 'No task is running'}), 404
        _active_task['cancelled'] = True

    # Also stop any running terminal process that was started by the agent
    # The agent loop will check _active_task['cancelled'] and break out
    # Force-stop any running processes in utils.running_processes
    from utils import running_processes, stop_process
    for pid, info in list(running_processes.items()):
        if info.get('running'):
            try:
                stop_process(pid)
            except Exception:
                pass

    return jsonify({'ok': True, 'message': 'Task cancellation requested'})


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
                if _active_task['subscribers'] <= 0:
                    # Full cleanup when last subscriber disconnects
                    _active_task['running'] = False
                    _active_task['cancelled'] = False
                    _active_task['conv_id'] = None
                    _active_task['message'] = None
                    _active_task['started_at'] = None
                    _active_task['event_queue'] = None
                    _active_task['event_buffer'] = None
                    _active_task['thread'] = None

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
