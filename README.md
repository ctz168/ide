# PhoneIDE IDE

一款轻量级的移动端 Web IDE，专为 Termux/Ubuntu 环境设计。

基于 Python Flask 后端 + CodeMirror 5 前端，提供完整的代码编辑、文件管理、Git 操作、代码运行、AI 编程助手等功能。

> 这是 [PhoneIDE](https://github.com/ctz168/phoneide) 的 **Web 服务组件**，可以独立运行，也可以被打包进 Android APK 中使用。

## 快速开始

### 一行命令安装（推荐）

自动检测平台、安装 Python 依赖、克隆仓库，支持 Termux / Ubuntu / Debian / Fedora / CentOS / macOS / Alpine / Arch：

```bash
curl -fsSL https://raw.githubusercontent.com/ctz168/ide/main/install.sh | bash
```

安装完成后：

```bash
cd ~/phoneide-ide && python3 server.py
```

浏览器打开 `http://localhost:1239` 即可使用。

**自定义安装目录：**
```bash
PHONEIDE_INSTALL_DIR=~/my-ide curl -fsSL https://raw.githubusercontent.com/ctz168/ide/main/install.sh | bash
```

**安装并自动启动：**
```bash
PHONEIDE_AUTO_START=1 curl -fsSL https://raw.githubusercontent.com/ctz168/ide/main/install.sh | bash
```

### 手动安装

**Termux：**
```bash
pkg install python python-pip
pip install flask flask-cors
git clone https://github.com/ctz168/ide.git && cd ide
python3 server.py
```

**Ubuntu / Debian / WSL：**
```bash
sudo apt install python3 python3-pip python3-venv
pip3 install --break-system-packages flask flask-cors
git clone https://github.com/ctz168/ide.git && cd ide
python3 server.py
```

**macOS：**
```bash
brew install python
pip3 install flask flask-cors
git clone https://github.com/ctz168/ide.git && cd ide
python3 server.py
```

**Fedora / CentOS：**
```bash
sudo dnf install python3 python3-pip
pip3 install flask flask-cors
git clone https://github.com/ctz168/ide.git && cd ide
python3 server.py
```

**Alpine：**
```bash
sudo apk add python3 py3-pip
pip3 install --break-system-packages flask flask-cors
git clone https://github.com/ctz168/ide.git && cd ide
python3 server.py
```

### Docker

```bash
docker run -d -p 1239:1239 -v ~/phoneide_workspace:/workspace python:3.12-slim bash -c \
  "pip install flask flask-cors && git clone --depth 1 https://github.com/ctz168/ide.git /ide && cd /ide && PHONEIDE_WORKSPACE=/workspace python3 server.py"
```

启动后浏览器打开 `http://localhost:1239` 即可使用。

## 功能特性

### 代码编辑器

基于 CodeMirror 5 内核，支持 30+ 种编程语言的语法高亮、自动补全、括号匹配、代码折叠、行号显示。支持 Python、JavaScript、TypeScript、Go、Rust、Java、C/C++、Shell、SQL 等主流语言。编辑器内置搜索替换（正则表达式），可快速定位和批量修改代码。

### 文件管理

完整的文件树浏览体验，支持打开任意文件夹作为工作空间。可以新建文件和目录、重命名、删除。文件列表自动识别文件类型并显示对应图标。

### Git 集成

内置全套 Git 操作界面：查看状态（status）、提交日志（log）、分支切换（checkout）、暂存区管理（add）、提交（commit）、远程推送（push）、拉取（pull）、仓库克隆（clone）、Diff 查看、Stash 暂存。

### 代码运行

支持直接在 IDE 中运行代码。自动检测系统中已安装的编译器和运行时（Python、Node.js、GCC、G++、Go、Rust、Ruby、Lua、Bash 等）。运行输出通过 SSE 实时流式推送，运行中随时可以终止进程。

### 虚拟环境

内置 Python 虚拟环境管理，可以一键创建、切换 venv。激活后运行代码自动使用 venv 中的 Python 和已安装的包。

### AI 编程助手

右侧滑出面板集成 LLM 对话功能，支持配置任意 OpenAI 兼容 API（自定义 API 地址）。AI 内置 Agent 工具能力：读写文件、执行命令、全局搜索、Git 操作等。对话历史自动保存。

### 全局搜索

支持在整个项目范围内搜索文本，包括正则表达式、大小写敏感、文件类型过滤。搜索结果点击即可跳转到对应文件。支持跨文件批量替换。

### 移动端优化

专为手机触屏设计：从左侧边缘右滑打开文件侧边栏，从右侧边缘左滑打开 AI 对话面板。深色 Catppuccin 配色方案。

## 项目结构

```
ctz168/ide/
├── server.py              # Flask 入口，注册 7 个 Blueprint
├── utils.py               # 共享工具函数、常量、配置管理
├── requirements.txt       # Python 依赖 (flask, flask-cors)
├── install.sh             # 跨平台一键安装（Termux/Ubuntu/macOS/Fedora/Alpine/Arch）
├── start.sh               # 启动脚本（处理端口占用）
├── routes/
│   ├── __init__.py
│   ├── files.py           # 文件 CRUD：列表、读取、保存、创建、删除、重命名、搜索
│   ├── run.py             # 代码执行：运行、停止、进程列表、SSE 输出流
│   ├── git.py             # Git 操作：status/log/branch/checkout/add/commit/push/pull/clone/diff/stash
│   ├── chat.py            # LLM 对话 + AI Agent：19 种工具，OpenAI 兼容协议
│   ├── venv.py            # 虚拟环境：创建、激活、包列表、编译器检测
│   ├── server_mgmt.py     # 服务管理：健康检查、配置、状态、重启、日志（SSE）
│   └── update.py          # 更新检查：代码更新(ctz168/ide)、APK更新(ctz168/phoneide)
└── static/
    ├── index.html         # 单页 IDE（工具栏 + 侧边栏 + 编辑器 + 底部面板 + AI面板）
    ├── css/
    │   └── style.css      # Catppuccin 深色主题 (~1560行)，响应式布局
    ├── js/
    │   ├── app.js         # 主入口：手势控制、侧边栏切换、键盘快捷键
    │   ├── editor.js      # CodeMirror 5 编辑器管理
    │   ├── files.js       # 文件树浏览与管理
    │   ├── git.js         # Git 操作界面
    │   ├── search.js      # 全局搜索与替换
    │   ├── terminal.js    # 代码运行与输出
    │   ├── chat.js        # LLM 对话与 Agent 工具执行
    │   └── debug.js       # 调试面板：编译器选择、venv 管理
    └── vendor/
        ├── codemirror/    # CodeMirror 5 核心 + 11 种语言模式 + 插件
        └── marked/        # Markdown 渲染器
```

## API 接口

服务端运行在 `http://localhost:1239`，所有 API 均返回 JSON。

### 文件管理

| 方法 | 接口 | 说明 |
|------|------|------|
| GET | `/api/files/list?path=<dir>` | 列出目录文件 |
| GET | `/api/files/read?path=<file>` | 读取文件内容 |
| POST | `/api/files/save` | 保存文件 |
| POST | `/api/files/create` | 创建文件/目录 |
| POST | `/api/files/delete` | 删除文件/目录 |
| POST | `/api/files/rename` | 重命名文件/目录 |
| POST | `/api/files/open_folder` | 打开文件夹为工作空间 |
| POST | `/api/search` | 全局搜索 |
| POST | `/api/search/replace` | 全局替换 |

### 代码执行

| 方法 | 接口 | 说明 |
|------|------|------|
| POST | `/api/run/execute` | 执行代码 |
| POST | `/api/run/stop` | 终止运行 |
| GET | `/api/run/processes` | 列出运行中进程 |
| GET | `/api/run/output` | 获取进程输出 |
| GET | `/api/run/output/stream` | SSE 实时输出流 |

### Git 操作

| 方法 | 接口 | 说明 |
|------|------|------|
| GET | `/api/git/status` | Git 状态 |
| GET | `/api/git/log` | 提交日志 |
| GET | `/api/git/branch` | 分支列表 |
| GET | `/api/git/diff` | 查看 Diff |
| GET | `/api/git/remote` | 远程仓库信息 |
| POST | `/api/git/checkout` | 切换分支 |
| POST | `/api/git/add` | 暂存文件 |
| POST | `/api/git/commit` | 提交 |
| POST | `/api/git/push` | 推送 |
| POST | `/api/git/pull` | 拉取 |
| POST | `/api/git/clone` | 克隆仓库 |
| POST | `/api/git/stash` | Stash |
| POST | `/api/git/reset` | Reset |

### AI 对话

| 方法 | 接口 | 说明 |
|------|------|------|
| POST | `/api/chat/send` | 发送消息（非流式） |
| POST | `/api/chat/send/stream` | 发送消息（SSE 流式） |
| GET | `/api/chat/history` | 获取对话历史 |
| POST | `/api/chat/clear` | 清除对话历史 |
| GET | `/api/llm/config` | 获取 LLM 配置 |
| POST | `/api/llm/config` | 更新 LLM 配置 |

### 虚拟环境 & 编译器

| 方法 | 接口 | 说明 |
|------|------|------|
| GET | `/api/compilers` | 检测可用编译器 |
| POST | `/api/venv/create` | 创建虚拟环境 |
| POST | `/api/venv/activate` | 激活虚拟环境 |
| GET | `/api/venv/list` | 列出虚拟环境 |
| GET | `/api/venv/packages` | 查看已安装包 |

### 服务管理

| 方法 | 接口 | 说明 |
|------|------|------|
| GET | `/api/health` | 健康检查 |
| GET | `/api/config` | 获取配置 |
| POST | `/api/config` | 更新配置 |
| GET | `/api/server/status` | 服务器状态（端口、内存、进程数） |
| POST | `/api/server/restart` | 重启服务器 |
| POST | `/api/server/logs` | 获取日志 |
| GET | `/api/server/logs/stream` | SSE 实时日志流 |
| GET | `/api/system/info` | 系统信息 |

### 更新

| 方法 | 接口 | 说明 |
|------|------|------|
| POST | `/api/update/check` | 检查更新（APK + 代码） |
| POST | `/api/update/apply` | 应用代码更新 |

## 配置说明

IDE 配置存储在 `~/.phoneide/config.json`：

```json
{
  "workspace": "~/phoneide_workspace",
  "venv_path": "",
  "compiler": "python3",
  "theme": "dark",
  "font_size": 14,
  "tab_size": 4,
  "show_line_numbers": true
}
```

LLM API 配置存储在 `~/.phoneide/llm_config.json`：

```json
{
  "provider": "openai",
  "api_key": "sk-xxx",
  "api_base": "https://api.openai.com/v1",
  "model": "gpt-4o-mini",
  "temperature": 0.7,
  "max_tokens": 4096,
  "system_prompt": "You are a helpful coding assistant."
}
```

## 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `PHONEIDE_PORT` | `1239` | 服务监听端口 |
| `PHONEIDE_HOST` | `0.0.0.0` | 绑定地址 |
| `PHONEIDE_WORKSPACE` | `~/phoneide_workspace` | 默认工作空间路径 |

## 手势操作（移动端）

| 手势 | 功能 |
|------|------|
| 左侧边缘右滑 | 打开文件侧边栏 |
| 右侧边缘左滑 | 打开 AI 对话面板 |
| 在已打开的侧栏上左滑 | 关闭侧栏 |
| 长按文件 | 弹出上下文菜单（重命名、删除等） |

## 更新机制

**独立运行时（git clone）：**
```bash
cd ide
git pull
# 重启 server.py 即可
```

**在 PhoneIDE APK 内运行时：**
- 代码更新 → 自动从 `ctz168/ide` 拉取
- APK 更新 → 检查 `ctz168/phoneide` GitHub Releases

## 环境要求

| 项目 | 最低要求 |
|------|----------|
| Python | 3.8+ |
| 依赖包 | flask >= 3.0.0, flask-cors >= 4.0.0 |
| 浏览器 | Chrome / Firefox / Safari（近两年版本） |

## 相关仓库

| 仓库 | 说明 |
|------|------|
| **ctz168/ide** (本仓库) | IDE 网页服务（Flask 后端 + 前端） |
| [ctz168/phoneide](https://github.com/ctz168/phoneide) | Android APK 封装（proot Ubuntu + WebView） |

## 技术栈

- **后端**: Python Flask + Flask-CORS
- **前端**: 原生 HTML/CSS/JavaScript（无框架）
- **编辑器**: CodeMirror 5（11 种语言模式 + 插件）
- **实时通信**: Server-Sent Events (SSE)
- **AI 集成**: OpenAI 兼容 API 协议
- **主题**: Catppuccin Mocha 深色配色

## 许可证

MIT License
