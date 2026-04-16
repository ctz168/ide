# PhoneIDE Web Server (IDE)

Mobile-optimized Web IDE backend — Flask server + frontend.

This is the **web server component** of PhoneIDE. It can run standalone on Termux/Ubuntu, or be bundled inside the [PhoneIDE Android APK](https://github.com/ctz168/phoneide).

## Quick Start (Termux)

```bash
pkg install python python-pip
pip install flask flask-cors
git clone https://github.com/ctz168/ide.git
cd ide
python3 server.py
# Open http://localhost:1239 in your browser
```

## Quick Start (Ubuntu/WSL)

```bash
pip3 install flask flask-cors
git clone https://github.com/ctz168/ide.git
cd ide
python3 server.py
# Open http://localhost:1239 in your browser
```

## One-liner Install (Termux)

```bash
curl -fsSL https://raw.githubusercontent.com/ctz168/ide/main/install.sh | bash
```

## Features

- Code editor with syntax highlighting (CodeMirror 5)
- File manager with create/rename/delete/search
- Terminal with Python/Shell execution (streaming output)
- Git operations (clone, pull, push, commit, branch, log, diff)
- LLM AI chat (OpenAI-compatible API, agent mode with 19 tools)
- Virtual environment management
- Responsive mobile UI (Catppuccin dark theme)

## Project Structure

```
server.py              # Flask entry point
utils.py               # Shared utilities, constants
requirements.txt       # Python dependencies
routes/
  files.py             # File CRUD API
  run.py               # Code execution API
  git.py               # Git operations API
  chat.py              # LLM chat + AI Agent API
  venv.py              # Virtual environment API
  server_mgmt.py       # Server management API
  update.py            # Self-update API (code from ctz168/ide, APK from ctz168/phoneide)
static/
  index.html           # Single-page IDE
  css/style.css        # Theme styles
  js/                  # Frontend JavaScript
  vendor/              # CodeMirror 5, marked.js
```

## API Endpoints

| Endpoint | Description |
|---|---|
| `POST /api/files/list` | List files in directory |
| `POST /api/files/read` | Read file contents |
| `POST /api/files/save` | Save file |
| `POST /api/files/create` | Create file/folder |
| `POST /api/files/delete` | Delete file/folder |
| `POST /api/files/search` | Global search/replace |
| `POST /api/run/execute` | Run code |
| `POST /api/run/stop` | Stop process |
| `GET /api/run/output/<id>` | SSE output stream |
| `POST /api/git/status` | Git status |
| `POST /api/git/commit` | Git commit |
| `POST /api/git/push` | Git push |
| `POST /api/git/pull` | Git pull |
| `POST /api/git/clone` | Git clone |
| `POST /api/chat/send` | LLM chat |
| `GET /api/chat/stream` | SSE chat stream |
| `POST /api/venv/create` | Create venv |
| `POST /api/server/restart` | Restart server |
| `GET /api/server/logs` | SSE log stream |

## Configuration

Config stored in `~/.phoneide/config.json`:

```json
{
  "workspace": "~/phoneide_workspace",
  "compiler": "python3",
  "theme": "dark",
  "font_size": 14,
  "tab_size": 4
}
```

LLM config in `~/.phoneide/llm_config.json`:

```json
{
  "provider": "openai",
  "api_key": "",
  "api_base": "",
  "model": "gpt-4o-mini",
  "temperature": 0.7,
  "max_tokens": 4096
}
```

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `PHONEIDE_PORT` | `1239` | Server port |
| `PHONEIDE_HOST` | `0.0.0.0` | Bind address |
| `PHONEIDE_WORKSPACE` | `~/phoneide_workspace` | Default workspace |

## Update Mechanism

When running inside the PhoneIDE APK:
- **Code updates**: fetched from `ctz168/ide` (this repo)
- **APK updates**: checked against `ctz168/phoneide` GitHub Releases

When running standalone (git clone):
- Just `git pull` to update

## Related Repositories

| Repository | Description |
|---|---|
| [ctz168/ide](https://github.com/ctz168/ide) | IDE web server (this repo) |
| [ctz168/phoneide](https://github.com/ctz168/phoneide) | Android APK wrapper |

## License

MIT
