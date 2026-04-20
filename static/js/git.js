/**
 * GitManager - Git operations for PhoneIDE
 * Works with Flask backend on port 1239
 */
const GitManager = (() => {
    'use strict';

    // ── State ──────────────────────────────────────────────────────
    let currentBranch = '';
    let statusData = null;
    let logData = [];
    let branchData = [];
    let _logLoading = false;       // prevent concurrent log loads
    let _logAllLoaded = false;     // true when server returns fewer than requested

    /**
     * Get the current git working directory.
     * Prefers the project path from ProjectManager (always correct),
     * falls back to FileManager.currentPath.
     * Returns a path relative to WORKSPACE (e.g. 'myrepo'), or '' if no project.
     */
    function getGitCwd() {
        // Always use project root as git working directory.
        // Falling back to FileManager.currentPath is dangerous because:
        // 1. Subdirectories may have their own .git (submodules / nested repos)
        // 2. Navigating into a subdirectory would change the git context unexpectedly
        if (window.ProjectManager) {
            const proj = window.ProjectManager.getCurrentProject();
            if (proj && proj.project) {
                return proj.project.replace(/^\//, '');
            }
        }
        // No project open — use workspace root
        return '';
    }

    // ── Console Logging Helper ─────────────────────────────────────

    /**
     * Log git operation command and result to the terminal console.
     * Shows the actual git command being executed and its output.
     */
    function gitLog(cmd, result) {
        if (!window.TerminalManager) return;
        const T = window.TerminalManager;
        T.appendOutput(`$ git ${cmd}`, 'system');
        if (result && result.stdout) {
            result.stdout.trim().split('\n').forEach(line => {
                if (line) T.appendOutput(line, 'stdout');
            });
        }
        if (result && result.stderr) {
            result.stderr.trim().split('\n').forEach(line => {
                if (line) T.appendOutput(line, 'stderr');
            });
        }
        if (result && result.ok === false && (!result.stdout || !result.stderr)) {
            T.appendOutput('(no output)', 'stderr');
        }
    }

    function gitLogSimple(cmd, error) {
        if (!window.TerminalManager) return;
        window.TerminalManager.appendOutput(`$ git ${cmd}`, 'system');
        if (error) {
            window.TerminalManager.appendOutput(error, 'stderr');
        }
    }

    /**
     * Parse error from HTTP response, preferring server JSON body over statusText.
     */
    async function parseError(resp, context) {
        let errorMsg = `${context}: ${resp.statusText}`;
        try {
            const err = await resp.json();
            errorMsg = err.error || err.message || errorMsg;
        } catch (_e) {
            errorMsg = `${context} (${resp.status})`;
        }
        return errorMsg;
    }

    // ── API: Status ────────────────────────────────────────────────

    /**
     * Initialize git in the current directory
     */
    async function gitInit() {
        try {
            const gitCwd = getGitCwd();
            console.log('[GitManager] gitInit called, cwd:', gitCwd);
            const resp = await fetch('/api/git/init', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ path: gitCwd })
            });
            if (!resp.ok) {
                const errorMsg = await parseError(resp, '初始化失败');
                throw new Error(errorMsg);
            }
            const data = await resp.json();
            gitLog(`init ${gitCwd || '.'}`, data);
            showToast(data.note || 'Git 仓库已初始化', 'success');
            await refresh();
        } catch (err) {
            gitLogSimple(`init ${getGitCwd() || '.'}`, err.message);
            showToast('初始化失败: ' + err.message, 'error');
        }
    }

    /**
     * Refresh git status and update UI
     */
    async function refreshStatus() {
        try {
            const cwd = getGitCwd();
            const params = cwd ? `?path=${encodeURIComponent(cwd)}` : '';
            const resp = await fetch(`/api/git/status${params}`);
            if (!resp.ok) throw new Error(`Failed to get status: ${resp.statusText}`);
            const data = await resp.json();
            statusData = data;

            // If not a git repo, show init prompt instead of empty list
            if (data.not_a_repo) {
                showNotARepoPrompt();
                return data;
            }

            renderChangesList(data);
            updateStatusBar(data);
            return data;
        } catch (err) {
            showToast(`Git status error: ${err.message}`, 'error');
            return null;
        }
    }

    /**
     * Show 'not a git repo' prompt with init button
     */
    function showNotARepoPrompt() {
        const el = document.getElementById('git-changes-list');
        if (!el) return;
        el.innerHTML = `
            <div class="git-no-changes" style="text-align:center;padding:20px 12px;">
                <div style="font-size:24px;margin-bottom:8px;">📦</div>
                <div style="font-size:13px;color:var(--text-secondary);margin-bottom:12px;">此目录不是 Git 仓库</div>
                <button class="git-init-prompt-btn" style="padding:8px 20px;border:1px solid var(--accent);background:var(--accent);color:#fff;border-radius:6px;font-size:13px;cursor:pointer;">初始化 Git 仓库</button>
            </div>`;
        const btn = el.querySelector('.git-init-prompt-btn');
        if (btn) {
            // Use bindTouchButton for mobile compatibility if available
            if (window.bindTouchButton) {
                window.bindTouchButton(btn, () => gitInit());
            } else {
                btn.addEventListener('click', () => gitInit());
            }
        }

        const countEl = document.getElementById('git-status-count');
        if (countEl) { countEl.textContent = 'no repo'; countEl.className = 'git-dirty'; }

        const branchEl = document.getElementById('git-current-branch');
        if (branchEl) { branchEl.textContent = '-'; }
    }

    // ── API: Log ───────────────────────────────────────────────────

    /**
     * Refresh commit log (reset and load first batch)
     */
    async function refreshLog() {
        _logAllLoaded = false;
        logData = [];
        return await loadMoreLog();
    }

    /**
     * Load more commits (incremental / infinite scroll)
     * Appends to existing logData and renders.
     */
    async function loadMoreLog() {
        if (_logLoading || _logAllLoaded) return logData;
        _logLoading = true;

        try {
            const cwd = getGitCwd();
            const offset = logData.length;
            const params = new URLSearchParams();
            if (cwd) params.set('path', cwd);
            params.set('count', 20);
            params.set('offset', offset);

            const resp = await fetch(`/api/git/log?${params}`);
            if (!resp.ok) throw new Error(`Failed to get log: ${resp.statusText}`);
            const data = await resp.json();
            const newCommits = Array.isArray(data) ? data : (data.commits || []);

            if (newCommits.length < 20) {
                _logAllLoaded = true;
            }

            if (newCommits.length > 0) {
                logData = logData.concat(newCommits);
                renderLogList(logData, /* append */ offset > 0);
            }

            return logData;
        } catch (err) {
            showToast(`Git log error: ${err.message}`, 'error');
            return logData;
        } finally {
            _logLoading = false;
        }
    }

    // ── API: Branches ──────────────────────────────────────────────

    /**
     * Refresh branch info
     */
    async function refreshBranches() {
        try {
            const cwd = getGitCwd();
            const params = cwd ? `?path=${encodeURIComponent(cwd)}` : '';
            const resp = await fetch(`/api/git/branch${params}`);
            if (!resp.ok) throw new Error(`Failed to get branches: ${resp.statusText}`);
            const data = await resp.json();
            branchData = Array.isArray(data) ? data : (data.branches || []);
            currentBranch = data.current || data.current_branch || '';
            updateBranchDisplay();
            return data;
        } catch (err) {
            showToast(`Git branch error: ${err.message}`, 'error');
            return [];
        }
    }

    // ── API: Clone ─────────────────────────────────────────────────

    /**
     * Clone a repository
     */
    async function clone(url) {
        if (!url) {
            // Load saved token from config
            let savedToken = '';
            try {
                const cfgResp = await fetch('/api/config');
                if (cfgResp.ok) {
                    const cfg = await cfgResp.json();
                    savedToken = cfg.github_token || '';
                }
            } catch (_e) {}

            const tokenHint = savedToken ? '已配置' : '公开仓库无需填写';
            const result = await showCloneDialog(savedToken, tokenHint);
            if (!result) return;
            url = result.url;
            if (result.token) {
                // Save token to config
                try {
                    await fetch('/api/config', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ github_token: result.token })
                    });
                } catch (_e) {}
                // Inject token into URL if it's a GitHub HTTPS URL
                if (result.token && url.includes('github.com') && !url.includes('@')) {
                    url = url.replace('https://', `https://${result.token}@`);
                }
            }
        }

        showToast('正在克隆仓库...', 'info');

        try {
            const resp = await fetch('/api/git/clone', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ url })
            });
            if (!resp.ok) {
                const errData = await resp.json().catch(() => ({}));
                throw new Error(errData.error || `Clone failed: ${resp.statusText}`);
            }
            const data = await resp.json();

            showToast('克隆成功', 'success');
            gitLog(`clone ${url}`, data);

            // Navigate into cloned folder
            const clonePath = data.path;
            if (window.FileManager && clonePath) {
                // clonePath is relative from server, prepend /workspace for FileManager
                const fullPath = '/workspace/' + clonePath.replace(/^\//, '');
                await window.FileManager.openFolder(fullPath);
            }

            // No need to git init — cloned repos already have .git

            // Refresh file list
            if (window.FileManager) {
                await window.FileManager.refresh();
            }
            await refresh();

            return data;
        } catch (err) {
            showToast('克隆失败: ' + err.message, 'error');
            gitLogSimple(`clone ${url}`, err.message);
        }
    }

    /**
     * Show clone dialog with URL + token fields
     */
    function showCloneDialog(savedToken, tokenHint) {
        return new Promise((resolve) => {
            if (window.showDialog) {
                const bodyHTML = `
                    <div style="display:flex;flex-direction:column;gap:12px;">
                        <div>
                            <label style="display:block;font-size:12px;color:var(--text-muted);margin-bottom:4px;">仓库地址</label>
                            <input type="text" id="clone-url-input" placeholder="https://github.com/user/repo.git" autocomplete="off"
                                style="width:100%;padding:8px 10px;border-radius:6px;border:1px solid #555;background:#2a2a2a;color:#ddd;font-size:13px;box-sizing:border-box;">
                        </div>
                        <div>
                            <label style="display:block;font-size:12px;color:var(--text-muted);margin-bottom:4px;">GitHub Token (${tokenHint})</label>
                            <input type="password" id="clone-token-input" placeholder="${savedToken ? '已配置，留空使用已保存' : '公开仓库无需填写'}"
                                style="width:100%;padding:8px 10px;border-radius:6px;border:1px solid #555;background:#2a2a2a;color:#ddd;font-size:13px;box-sizing:border-box;">
                        </div>
                    </div>`;
                window.showDialog('📥 克隆仓库', bodyHTML, [
                    { text: '取消', value: 'cancel', class: 'btn-cancel' },
                    { text: '克隆', value: 'ok', class: 'btn-confirm' },
                ]).then(result => {
                    if (!result.confirmed) { resolve(null); return; }
                    const urlInput = document.getElementById('clone-url-input');
                    const tokenInput = document.getElementById('clone-token-input');
                    const url = urlInput ? urlInput.value.trim() : '';
                    const token = tokenInput ? tokenInput.value.trim() : '';
                    if (!url) { resolve(null); return; }
                    resolve({ url, token });
                });
                return;
            }
            // Fallback
            const url = window.prompt('Clone Repository URL:', 'https://github.com/user/repo.git');
            if (url) resolve({ url, token: '' });
            else resolve(null);
        });
    }

    /**
     * Show token config dialog
     */
    function showTokenConfig() {
        (async () => {
            let savedToken = '';
            try {
                const cfgResp = await fetch('/api/config');
                if (cfgResp.ok) {
                    const cfg = await cfgResp.json();
                    savedToken = cfg.github_token || '';
                }
            } catch (_e) {}

            const bodyHTML = `
                <div style="display:flex;flex-direction:column;gap:8px;">
                    <p style="font-size:12px;color:var(--text-muted);line-height:1.4;">
                        Token 用于克隆/拉取私有仓库，公开仓库无需配置。
                    </p>
                    <input type="password" id="token-config-input" placeholder="ghp_xxxxxxxxxxxx"
                        style="width:100%;padding:8px 10px;border-radius:6px;border:1px solid #555;background:#2a2a2a;color:#ddd;font-size:13px;box-sizing:border-box;"
                        value="${window.escapeHTML ? window.escapeHTML(savedToken) : savedToken}">
                </div>`;

            if (window.showDialog) {
                const result = await window.showDialog('🔑 配置 GitHub Token', bodyHTML, [
                    { text: '取消', value: 'cancel', class: 'btn-cancel' },
                    { text: '保存', value: 'ok', class: 'btn-confirm' },
                ]);
                if (!result.confirmed) return;
                const input = document.getElementById('token-config-input');
                const token = input ? input.value.trim() : '';
                try {
                    await fetch('/api/config', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ github_token: token })
                    });
                    window.showToast('Token 已保存', 'success', 2000);
                } catch (_e) {
                    window.showToast('保存失败', 'error', 2000);
                }
            }
        })();
    }

    // ── API: Pull ──────────────────────────────────────────────────

    /**
     * Pull from remote
     */
    async function pull() {
        showToast('Pulling changes...', 'info');

        try {
            const gitCwd = getGitCwd();
            const resp = await fetch('/api/git/pull', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ path: gitCwd })
            });
            if (!resp.ok) {
                const errorMsg = await parseError(resp, 'Pull failed');
                throw new Error(errorMsg);
            }
            const data = await resp.json();
            gitLog(`pull`, data);
            showToast('Pull successful', 'success');
            await refresh();

            // Refresh file list
            if (window.FileManager) {
                await window.FileManager.refresh();
            }

            return data;
        } catch (err) {
            showToast(`Pull error: ${err.message}`, 'error');
            gitLogSimple(`pull`, err.message);
        }
    }

    // ── API: Push ──────────────────────────────────────────────────

    /**
     * Push to remote
     */
    async function push(setUpstream) {
        showToast('Pushing changes...', 'info');

        try {
            const gitCwd = getGitCwd();
            const body = { path: gitCwd };
            if (setUpstream !== undefined) {
                body.set_upstream = setUpstream;
            }

            const resp = await fetch('/api/git/push', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body)
            });
            if (!resp.ok) {
                const errorMsg = await parseError(resp, 'Push failed');
                throw new Error(errorMsg);
            }
            const data = await resp.json();
            gitLog(`push${setUpstream ? ' -u' : ''}`, data);
            showToast('Push successful', 'success');
            await refresh();

            return data;
        } catch (err) {
            // If push fails because there's no upstream, offer to set it
            if (err.message.includes('no upstream') || err.message.includes('403') || err.message.includes('500')) {
                const shouldSetUp = await confirmDialog(
                    'Push Failed',
                    'No upstream branch set. Push and set upstream?'
                );
                if (shouldSetUp) {
                    return push(true);
                }
            } else {
                showToast(`Push error: ${err.message}`, 'error');
                gitLogSimple(`push`, err.message);
            }
        }
    }

    // ── API: Sync (pull + push) ────────────────────────────────────

    /**
     * Pull then push
     */
    async function sync() {
        showToast('Syncing...', 'info');
        await pull();
        await push();
        showToast('Sync complete', 'success');
    }

    // ── API: Add ───────────────────────────────────────────────────

    /**
     * Stage files for commit
     * @param {string|string[]} paths - file path(s) to add
     */
    async function addFiles(paths) {
        if (!paths) {
            showToast('No files specified to add', 'warning');
            return;
        }

        // Normalize to array
        if (typeof paths === 'string') paths = [paths];

        try {
            const gitCwd = getGitCwd();
            const resp = await fetch('/api/git/add', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ paths, path: gitCwd })
            });
            if (!resp.ok) {
                const errorMsg = await parseError(resp, 'Git add failed');
                throw new Error(errorMsg);
            }
            const data = await resp.json();
            gitLog(`add ${paths.join(' ')}`, data);
            showToast(`${paths.length} file(s) staged`, 'success');
            await refreshStatus();
            return data;
        } catch (err) {
            showToast(`Git add error: ${err.message}`, 'error');
        }
    }

    /**
     * Stage all changes
     */
    async function addAll() {
        try {
            const gitCwd = getGitCwd();
            const resp = await fetch('/api/git/add', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ paths: ['.'], path: gitCwd })
            });
            if (!resp.ok) {
                const errorMsg = await parseError(resp, 'Git add all failed');
                throw new Error(errorMsg);
            }
            const data = await resp.json();
            gitLog('add -A', data);
            showToast('All changes staged', 'success');
            await refreshStatus();
            return data;
        } catch (err) {
            showToast(`Git add error: ${err.message}`, 'error');
        }
    }

    // ── API: Commit ────────────────────────────────────────────────

    /**
     * Stage all changes and commit
     * @param {string} message - commit message
     */
    async function commit(message) {
        if (!message) {
            const msgEl = document.getElementById('git-commit-msg');
            message = msgEl ? msgEl.value.trim() : '';
        }

        if (!message) {
            message = await promptDialog('Commit', 'Enter commit message:', 'Update');
            if (!message) return;
        }

        try {
            // Auto stage all changes before commit
            await addAll();

            const gitCwd = getGitCwd();
            const resp = await fetch('/api/git/commit', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ message, path: gitCwd })
            });
            if (!resp.ok) {
                const errorMsg = await parseError(resp, 'Commit failed');
                throw new Error(errorMsg);
            }
            const data = await resp.json();
            gitLog(`commit -m ${message}`, data);

            // Clear commit message input
            const msgEl = document.getElementById('git-commit-msg');
            if (msgEl) msgEl.value = '';

            showToast('Committed successfully', 'success');
            await refresh();

            return data;
        } catch (err) {
            gitLogSimple(`commit -m ${message || ''}`, err.message);
            showToast(`Commit error: ${err.message}`, 'error');
        }
    }

    // ── API: Checkout ──────────────────────────────────────────────

    /**
     * Checkout a branch
     * @param {string} branch - branch name
     */
    async function checkout(branch) {
        if (!branch) {
            if (!branchData.length) {
                showToast('No branches available', 'warning');
                return;
            }
            const options = branchData.map(b => {
                const name = typeof b === 'string' ? b : b.name || b;
                const isCurrent = name.includes('*') || name === currentBranch;
                return { label: name.replace(/^\* /, ''), value: name.replace(/^\* /, '') };
            });

            const chosen = await choiceDialog('Checkout Branch', 'Select a branch:', options);
            if (!chosen) return;
            branch = chosen;
        }

        showToast(`Checking out ${branch}...`, 'info');

        try {
            const gitCwd = getGitCwd();
            const resp = await fetch('/api/git/checkout', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ branch, path: gitCwd })
            });
            if (!resp.ok) {
                const errorMsg = await parseError(resp, 'Checkout failed');
                throw new Error(errorMsg);
            }
            const data = await resp.json();
            gitLog(`checkout ${branch}`, data);
            showToast(`Switched to ${branch}`, 'success');
            await refresh();

            // Refresh file list
            if (window.FileManager) {
                await window.FileManager.refresh();
            }

            return data;
        } catch (err) {
            showToast(`Checkout error: ${err.message}`, 'error');
        }
    }

    // ── API: Stash ─────────────────────────────────────────────────

    /**
     * Stash operations: push, pop, apply, list, drop
     * @param {string} action - stash action (push|pop|apply|list|drop)
     * @param {object} options - additional options
     */
    async function stash(action, options = {}) {
        if (!action) {
            const actions = [
                { label: 'Stash Changes', value: 'push' },
                { label: 'Pop Stash', value: 'pop' },
                { label: 'Apply Stash', value: 'apply' },
                { label: 'List Stashes', value: 'list' }
            ];
            action = await choiceDialog('Stash', 'Select action:', actions);
            if (!action) return;
        }

        try {
            const gitCwd = getGitCwd();
            const resp = await fetch('/api/git/stash', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ action, path: gitCwd, ...options })
            });
            if (!resp.ok) {
                const errorMsg = await parseError(resp, `Stash ${action} failed`);
                throw new Error(errorMsg);
            }
            const data = await resp.json();
            gitLog(`stash ${action}`, data);

            if (action === 'list') {
                // Display stash list
                const stashList = Array.isArray(data) ? data : (data.stashes || []);
                const msg = stashList.length ? stashList.map(s => typeof s === 'string' ? s : JSON.stringify(s)).join('\n') : 'No stashes';
                showToast(msg, 'info');
            } else {
                showToast(`Stash ${action} successful`, 'success');
            }

            await refreshStatus();
            return data;
        } catch (err) {
            showToast(`Stash error: ${err.message}`, 'error');
        }
    }

    // ── API: Restore File ──────────────────────────────────────

    /**
     * Restore a file to its HEAD state (discard working/staged changes)
     * @param {string} filepath - file path to restore
     */
    async function restoreFile(filepath) {
        const confirmed = await confirmDialog(
            '恢复文件',
            `确定要恢复 "${filepath}" 到 HEAD 版本吗？未提交的修改将被丢弃。`
        );
        if (!confirmed) return;

        try {
            const gitCwd = getGitCwd();
            const resp = await fetch('/api/git/restore', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ filepath, path: gitCwd })
            });
            if (!resp.ok) {
                const errorMsg = await parseError(resp, '恢复失败');
                throw new Error(errorMsg);
            }
            const data = await resp.json();
            gitLog(`restore -- ${filepath}`, data);
            showToast('文件已恢复到 HEAD 版本', 'success');
            await refresh();

            // Close the diff overlay if open
            const diffOverlay = document.getElementById('diff-overlay');
            if (diffOverlay) diffOverlay.remove();

            // Refresh file list
            if (window.FileManager) await window.FileManager.refresh();
            return data;
        } catch (err) {
            showToast('恢复失败: ' + err.message, 'error');
        }
    }

    // ── API: Checkout / Reset to Commit ────────────────────────────

    /**
     * Reset the current branch to a specific commit (true rollback).
     * Uses git reset --hard so the branch pointer actually moves.
     * @param {string} hash - full or short commit hash
     */
    async function checkoutCommit(hash) {
        const shortHash = hash.substring(0, 7);
        const branchName = currentBranch || '当前分支';

        const confirmed = await confirmDialog(
            '⚠ 回滚到该版本',
            `确定要将 ${branchName} 回滚到版本 ${shortHash} 吗？\n\n` +
            `此操作会将分支指针移动到该版本，\n` +
            `${shortHash} 之后的提交将从分支历史中移除。\n\n` +
            `⚠ 工作区未提交的修改将全部丢失！\n` +
            `建议先用 git stash 暂存修改。`
        );
        if (!confirmed) return;

        showToast(`正在回滚到 ${shortHash}...`, 'info');

        try {
            const gitCwd = getGitCwd();
            const resp = await fetch('/api/git/checkout-commit', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ ref: hash, path: gitCwd, mode: 'reset-hard' })
            });
            if (!resp.ok) {
                const errorMsg = await parseError(resp, '回滚失败');
                throw new Error(errorMsg);
            }
            const data = await resp.json();
            gitLog(`reset --hard ${shortHash}`, data);
            showToast(`${branchName} 已回滚到 ${shortHash}`, 'success');
            await refresh();

            // Refresh file list
            if (window.FileManager) await window.FileManager.refresh();
            return data;
        } catch (err) {
            showToast('回滚失败: ' + err.message, 'error');
            gitLogSimple(`reset --hard ${shortHash}`, err.message);
        }
    }

    // ── API: Diff ──────────────────────────────────────────────────

    /**
     * Get diff for a file
     * @param {string} filepath - optional; if omitted shows all changes
     */
    async function diff(filepath) {
        try {
            const gitCwd = getGitCwd();
            // Send cwd as 'path' query param, file as 'file' query param
            let url = `/api/git/diff?path=${encodeURIComponent(gitCwd)}`;
            if (filepath) {
                url += `&file=${encodeURIComponent(filepath)}`;
            }
            const resp = await fetch(url);
            if (!resp.ok) {
                const errorMsg = await parseError(resp, 'Diff failed');
                throw new Error(errorMsg);
            }
            const data = await resp.json();
            gitLog(`diff${filepath ? ' ' + filepath : ''}`, { stdout: data.diff || data.content || '' });

            const diffText = data.diff || data.content || '';
            if (!diffText.trim()) {
                showToast('没有差异', 'info');
                return '';
            }

            if (window.EditorManager && typeof window.EditorManager.showDiff === 'function') {
                window.EditorManager.showDiff(diffText, filepath || 'All changes');
            } else if (window.EditorManager && typeof window.EditorManager.setContent === 'function') {
                window.EditorManager.setContent(diffText, filepath || 'diff');
            } else {
                showToast(`Diff:\n${diffText.substring(0, 500)}`, 'info');
            }

            return diffText;
        } catch (err) {
            showToast(`Diff error: ${err.message}`, 'error');
        }
    }

    // ── Refresh All ────────────────────────────────────────────────

    /**
     * Refresh status + log + branches
     */
    async function refresh() {
        await Promise.all([
            refreshStatus(),
            refreshLog(),
            refreshBranches()
        ]);
    }

    // ── Rendering ──────────────────────────────────────────────────

    /**
     * Render the git changes list (modified, added, deleted, untracked files)
     */
    function renderChangesList(data) {
        const el = document.getElementById('git-changes-list');
        if (!el) return;

        let changes = [];

        // Normalize different possible response formats
        if (data.staged && Array.isArray(data.staged)) {
            changes.push(...data.staged.map(f => ({ ...f, category: 'staged' })));
        }
        if (data.changed && Array.isArray(data.changed)) {
            changes.push(...data.changed.map(f => ({ ...f, category: 'modified' })));
        }
        if (data.modified && Array.isArray(data.modified)) {
            changes.push(...data.modified.map(f => ({ ...f, category: 'modified' })));
        }
        if (data.untracked && Array.isArray(data.untracked)) {
            changes.push(...data.untracked.map(f => ({ ...f, category: 'untracked' })));
        }
        if (data.deleted && Array.isArray(data.deleted)) {
            changes.push(...data.deleted.map(f => ({ ...f, category: 'deleted' })));
        }
        if (data.renamed && Array.isArray(data.renamed)) {
            changes.push(...data.renamed.map(f => ({ ...f, category: 'renamed' })));
        }

        // Also handle flat array format: [{path, status, ...}, ...]
        if (changes.length === 0 && Array.isArray(data.changes)) {
            changes = data.changes;
        }
        if (changes.length === 0 && Array.isArray(data.files)) {
            changes = data.files;
        }
        if (changes.length === 0 && Array.isArray(data)) {
            changes = data;
        }

        if (changes.length === 0) {
            el.innerHTML = '<div class="git-no-changes">No changes</div>';
            return;
        }

        let html = '';
        for (const change of changes) {
            const path = change.path || change.file || change.name || '';
            const status = change.status || change.category || '?';
            const icon = getStatusIcon(status);
            const escapedPath = escapeHTML(path);
            const statusLower = (status || '').toLowerCase();
            const isUntracked = statusLower.includes('untracked') || statusLower === 'untracked' || status === '?';
            const isDeleted = statusLower.includes('deleted');

            // Detect binary file extensions
            const ext = (path.split('.').pop() || '').toLowerCase();
            const binaryExts = ['pdf', 'png', 'jpg', 'jpeg', 'gif', 'webp', 'ico', 'svg', 'mp3', 'mp4', 'wav', 'zip', 'tar', 'gz', '7z', 'rar', 'exe', 'dll', 'so', 'dylib', 'bin', 'dat', 'woff', 'woff2', 'ttf', 'eot', 'otf'];
            const isBinary = binaryExts.includes(ext);

            html += `
                <div class="git-change-item" data-path="${escapeAttr(path)}" data-status="${escapeAttr(status)}">
                    <div class="git-change-row">
                        <span class="git-change-icon">${icon}</span>
                        <span class="git-change-path">${escapedPath}</span>
                    </div>
                    <div class="git-change-actions">
                        ${!isBinary ? `<button class="git-action-btn" data-action="diff" data-path="${escapeAttr(path)}" title="差异">📋</button>` : ''}
                        ${!isDeleted ? `<button class="git-action-btn" data-action="open" data-path="${escapeAttr(path)}" title="打开文件">📄</button>` : ''}
                        ${!isUntracked && !isDeleted ? `<button class="git-action-btn" data-action="restore" data-path="${escapeAttr(path)}" title="回退修改">↩</button>` : ''}
                        <button class="git-action-btn" data-action="ignore" data-path="${escapeAttr(path)}" title="添加到 .gitignore">🚫</button>
                        ${!isDeleted ? `<button class="git-action-btn git-action-danger" data-action="delete" data-path="${escapeAttr(path)}" title="删除文件">🗑</button>` : ''}
                    </div>
                </div>`;
        }

        el.innerHTML = html;

        // Bind action button clicks
        el.querySelectorAll('.git-action-btn').forEach(btn => {
            const handler = (e) => {
                e.stopPropagation();
                const action = btn.dataset.action;
                const path = btn.dataset.path;
                if (action === 'diff') diff(path);
                else if (action === 'open') openGitFile(path);
                else if (action === 'restore') restoreFile(path);
                else if (action === 'ignore') addToGitignore(path);
                else if (action === 'delete') deleteGitFile(path);
            };
            btn.addEventListener('click', handler);
            btn.addEventListener('touchend', (e) => {
                e.preventDefault();
                e.stopPropagation();
                handler(e);
            });
        });

        // Bind click on change item row (for diff)
        el.querySelectorAll('.git-change-row').forEach(row => {
            const item = row.closest('.git-change-item');
            row.addEventListener('click', () => {
                const path = item.dataset.path;
                diff(path);
            });

            // Long-press context menu
            let timer = null;
            item.addEventListener('touchstart', (e) => {
                timer = setTimeout(() => {
                    e.preventDefault();
                    showChangeContextMenu(e.touches[0].clientX, e.touches[0].clientY, item.dataset.path, item.dataset.status);
                }, 500);
            }, { passive: false });
            item.addEventListener('touchend', () => clearTimeout(timer));
            item.addEventListener('touchmove', () => clearTimeout(timer));
            item.addEventListener('contextmenu', (e) => {
                e.preventDefault();
                showChangeContextMenu(e.clientX, e.clientY, item.dataset.path, item.dataset.status);
            });
        });
    }

    /**
     * Render commit log (with expandable file list per commit)
     */
    function renderLogList(commits, append) {
        const el = document.getElementById('git-log-list');
        if (!el) return;

        if (!commits || commits.length === 0) {
            if (!append) el.innerHTML = '<div class="git-no-log">No commits yet</div>';
            return;
        }

        // Build HTML for new commits only
        const startIndex = append ? (el.querySelectorAll('.git-log-item').length) : 0;
        let html = '';
        for (let i = startIndex; i < commits.length; i++) {
            const commit = commits[i];
            const hash = commit.hash || commit.oid || commit.id || '';
            const fullHash = commit.full_hash || hash;
            const shortHash = hash.substring(0, 7);
            const message = commit.message || commit.msg || '';
            const author = commit.author || '';
            const date = commit.date || commit.timestamp || '';

            html += `
                <div class="git-log-item" data-hash="${escapeAttr(hash)}" data-full-hash="${escapeAttr(fullHash)}">
                    <div class="git-log-header">
                        <span class="git-log-expand-icon" data-expanded="false">▶</span>
                        <span class="git-log-hash">${escapeHTML(shortHash)}</span>
                        <span class="git-log-author">${escapeHTML(author)}</span>
                        <button class="git-log-checkout-btn" data-full-hash="${escapeAttr(fullHash)}" title="回到该版本">⏪</button>
                    </div>
                    <div class="git-log-message">${escapeHTML(message.split('\n')[0])}</div>
                    <div class="git-log-date">${escapeHTML(date)}</div>
                    <div class="git-log-files" style="display:none;"></div>
                </div>`;
        }

        if (append) {
            // Remove old loading indicator if exists
            const oldLoader = el.querySelector('.git-log-loader');
            if (oldLoader) oldLoader.remove();
            el.insertAdjacentHTML('beforeend', html);
        } else {
            el.innerHTML = html;
        }

        // Add loading indicator at the bottom (for infinite scroll)
        if (!_logAllLoaded) {
            const loader = document.createElement('div');
            loader.className = 'git-log-loader';
            loader.textContent = _logLoading ? '加载中...' : '↓ 下滑加载更多';
            el.appendChild(loader);
        }

        // Bind events only for newly added items
        const items = el.querySelectorAll('.git-log-item');
        for (let i = startIndex; i < items.length; i++) {
            const item = items[i];
            if (item._bound) continue;
            item._bound = true;

            // ── Click header to expand/collapse file list ──
            const header = item.querySelector('.git-log-header');
            const expandIcon = item.querySelector('.git-log-expand-icon');
            const filesContainer = item.querySelector('.git-log-files');

            const handler = (e) => {
                if (e.target.closest('.git-log-checkout-btn')) return;
                e.stopPropagation();
                toggleCommitFiles(item);
            };
            header.addEventListener('click', handler);
            header.addEventListener('touchend', (e) => {
                if (e.target.closest('.git-log-checkout-btn')) return;
                e.preventDefault();
                e.stopPropagation();
                handler(e);
            });

            // ── Checkout button ──
            const checkoutBtn = item.querySelector('.git-log-checkout-btn');
            const checkoutHandler = (e) => {
                e.stopPropagation();
                const fullHash = checkoutBtn.dataset.fullHash;
                if (fullHash) checkoutCommit(fullHash);
            };
            checkoutBtn.addEventListener('click', checkoutHandler);
            checkoutBtn.addEventListener('touchend', (e) => {
                e.preventDefault();
                e.stopPropagation();
                checkoutHandler(e);
            });
        }
    }

    /**
     * Set up infinite scroll on git log list
     */
    function initLogInfiniteScroll() {
        const el = document.getElementById('git-log-list');
        if (!el) return;

        el.addEventListener('scroll', () => {
            if (_logLoading || _logAllLoaded) return;
            // Trigger load when within 50px of bottom
            const threshold = 50;
            if (el.scrollHeight - el.scrollTop - el.clientHeight < threshold) {
                loadMoreLog();
            }
        }, { passive: true });
    }

    // Track which commits have loaded their file lists
    const _commitFilesCache = {};

    /**
     * Toggle the file list under a commit item.
     * First click: fetch file list from server, then expand.
     * Second click: collapse.
     */
    async function toggleCommitFiles(item) {
        const fullHash = item.dataset.fullHash;
        const filesContainer = item.querySelector('.git-log-files');
        const expandIcon = item.querySelector('.git-log-expand-icon');
        const isExpanded = expandIcon.dataset.expanded === 'true';

        if (isExpanded) {
            // Collapse
            filesContainer.style.display = 'none';
            expandIcon.dataset.expanded = 'false';
            expandIcon.textContent = '▶';
            return;
        }

        // Expand
        expandIcon.dataset.expanded = 'true';
        expandIcon.textContent = '▼';
        filesContainer.style.display = 'block';

        // If we haven't loaded the file list yet, fetch it
        if (!_commitFilesCache[fullHash]) {
            filesContainer.innerHTML = '<div style="padding:6px 12px;color:var(--text-muted);font-size:11px;">Loading...</div>';
            try {
                const gitCwd = getGitCwd();
                const params = `?ref=${encodeURIComponent(fullHash)}&path=${encodeURIComponent(gitCwd)}`;
                const resp = await fetch(`/api/git/commit-diff${params}`);
                if (!resp.ok) throw new Error('Failed to load commit files');
                const data = await resp.json();
                _commitFilesCache[fullHash] = data.files || [];
            } catch (err) {
                filesContainer.innerHTML = `<div style="padding:6px 12px;color:#e74c3c;font-size:11px;">${escapeHTML(err.message)}</div>`;
                return;
            }
        }

        const files = _commitFilesCache[fullHash];
        if (!files || files.length === 0) {
            filesContainer.innerHTML = '<div style="padding:6px 12px;color:var(--text-muted);font-size:11px;">No file changes (possibly merge commit)</div>';
            return;
        }

        // Render file list
        let filesHtml = '<div class="git-log-files-list">';
        for (const f of files) {
            const fpath = f.path || f.file || '';
            const additions = f.additions || 0;
            const deletions = f.deletions || 0;
            // Detect binary
            const ext = (fpath.split('.').pop() || '').toLowerCase();
            const binaryExts = ['pdf','png','jpg','jpeg','gif','webp','ico','svg','mp3','mp4','wav','zip','tar','gz','7z','rar','exe','dll','so','dylib','bin','dat','woff','woff2','ttf','eot','otf'];
            const isBinary = binaryExts.includes(ext);

            filesHtml += `
                <div class="git-log-file-item" data-path="${escapeAttr(fpath)}" data-hash="${escapeAttr(fullHash)}">
                    <span class="git-log-file-icon">${isBinary ? '📦' : '📄'}</span>
                    <span class="git-log-file-path" title="${escapeAttr(fpath)}">${escapeHTML(fpath)}</span>
                    ${!isBinary ? `<span class="git-log-file-stat">+${additions} -${deletions}</span>` : '<span class="git-log-file-stat binary">binary</span>'}
                    ${!isBinary ? `<button class="git-log-file-diff-btn" data-path="${escapeAttr(fpath)}" data-hash="${escapeAttr(fullHash)}" title="查看差异">📋</button>` : ''}
                </div>`;
        }
        filesHtml += '</div>';
        filesContainer.innerHTML = filesHtml;

        // Bind diff buttons
        filesContainer.querySelectorAll('.git-log-file-diff-btn').forEach(btn => {
            const btnHandler = (e) => {
                e.stopPropagation();
                const fpath = btn.dataset.path;
                const hash = btn.dataset.hash;
                if (fpath && hash) showCommitFileDiff(hash, fpath);
            };
            btn.addEventListener('click', btnHandler);
            btn.addEventListener('touchend', (e) => {
                e.preventDefault();
                e.stopPropagation();
                btnHandler(e);
            });
        });

        // Also bind click on file row (for diff)
        filesContainer.querySelectorAll('.git-log-file-item').forEach(row => {
            row.addEventListener('click', () => {
                const fpath = row.dataset.path;
                const hash = row.dataset.hash;
                if (fpath && hash && !row.querySelector('.git-log-file-stat.binary')) {
                    showCommitFileDiff(hash, fpath);
                }
            });
        });
    }

    /**
     * Show the diff for a specific file in a specific commit.
     */
    async function showCommitFileDiff(hash, filepath) {
        try {
            const gitCwd = getGitCwd();
            const params = `?ref=${encodeURIComponent(hash)}&file=${encodeURIComponent(filepath)}&path=${encodeURIComponent(gitCwd)}`;
            const resp = await fetch(`/api/git/commit-diff${params}`);
            if (!resp.ok) {
                const errorMsg = await parseError(resp, 'Commit diff failed');
                throw new Error(errorMsg);
            }
            const data = await resp.json();
            const diffText = data.diff || '';
            if (!diffText.trim()) {
                showToast('No diff available', 'info');
                return;
            }

            if (window.EditorManager && typeof window.EditorManager.showDiff === 'function') {
                const label = `${hash.substring(0, 7)} ${filepath}`;
                window.EditorManager.showDiff(diffText, label);
            } else {
                showToast(diffText.substring(0, 500), 'info');
            }
        } catch (err) {
            showToast('Diff error: ' + err.message, 'error');
        }
    }

    /**
     * Update the branch display
     */
    function updateBranchDisplay() {
        const branchEl = document.getElementById('git-current-branch');
        if (branchEl) {
            branchEl.textContent = currentBranch || 'no branch';
            branchEl.title = currentBranch || 'no branch';
        }
    }

    /**
     * Update the status count badge
     */
    function updateStatusBar(data) {
        const countEl = document.getElementById('git-status-count');
        if (!countEl) return;

        let count = 0;
        if (data.staged) count += data.staged.length;
        if (data.changed) count += data.changed.length;
        if (data.modified) count += data.modified.length;
        if (data.untracked) count += data.untracked.length;
        if (data.deleted) count += data.deleted.length;
        if (data.changes) count += data.changes.length;
        if (data.files) count += data.files.length;

        countEl.textContent = count > 0 ? `${count} change${count !== 1 ? 's' : ''}` : 'clean';
        countEl.className = count > 0 ? 'git-dirty' : 'git-clean';
    }

    // ── Context Menu for Changes ───────────────────────────────────

    function showChangeContextMenu(x, y, path, status) {
        removeChangeContextMenu();

        const menu = document.createElement('div');
        menu.className = 'context-menu visible';
        menu.style.left = `${Math.min(x, window.innerWidth - 200)}px`;
        menu.style.top = `${Math.min(y, window.innerHeight - 300)}px`;

        const items = [];
        const statusLower = (status || '').toLowerCase();
        const isUntracked = statusLower.includes('untracked') || statusLower === 'untracked' || status === '?';
        const isDeleted = statusLower.includes('deleted');

        items.push({ label: '查看差异', action: () => diff(path) });
        items.push({ label: '暂存文件', action: () => addFiles(path) });
        items.push({ label: '打开文件', action: () => openGitFile(path) });

        // Restore/Revert — only for modified/staged files (not untracked or deleted)
        if (!isUntracked && !isDeleted) {
            items.push({ label: '回退修改', action: () => restoreFile(path) });
        }

        // Add to .gitignore — mainly for untracked files, but allow for any
        items.push({ label: '添加到 .gitignore', action: () => addToGitignore(path) });

        // Delete file — only for untracked files or tracked modified files
        // (not for files already marked as deleted by git)
        if (!isDeleted) {
            items.push({ label: '🗑 删除文件', danger: true, action: () => deleteGitFile(path) });
        }

        menu.innerHTML = items.map(item => {
            const cls = item.danger ? 'context-menu-item danger' : 'context-menu-item';
            return `<button class="${cls}">${escapeHTML(item.label)}</button>`;
        }).join('');

        const buttons = menu.querySelectorAll('.context-menu-item');
        items.forEach((item, i) => {
            buttons[i].addEventListener('click', () => {
                item.action();
                removeChangeContextMenu();
            });
        });

        document.body.appendChild(menu);

        setTimeout(() => {
            document.addEventListener('click', dismissChangeContextMenu, { once: true });
            document.addEventListener('touchstart', dismissChangeContextMenu, { once: true });
        }, 10);
    }

    function dismissChangeContextMenu(e) {
        if (!e.target.closest('.context-menu')) {
            removeChangeContextMenu();
        }
    }

    function removeChangeContextMenu() {
        document.querySelectorAll('.context-menu').forEach(m => m.remove());
    }

    // ── API: Open File from Git Changes ─────────────────────────

    /**
     * Open a file from the git changes list.
     * Resolves the path relative to the git root (project dir) and opens it via FileManager.
     */
    async function openGitFile(filepath) {
        const gitCwd = getGitCwd();
        // Build path relative to workspace: project_dir/file_path
        const fullPath = gitCwd ? (gitCwd + '/' + filepath) : filepath;

        // Detect binary files
        const ext = (filepath.split('.').pop() || '').toLowerCase();
        const binaryExts = ['pdf', 'png', 'jpg', 'jpeg', 'gif', 'webp', 'ico', 'svg', 'mp3', 'mp4', 'wav', 'zip', 'tar', 'gz', '7z', 'rar', 'exe', 'dll', 'so', 'dylib', 'bin', 'dat', 'woff', 'woff2', 'ttf', 'eot', 'otf'];
        if (binaryExts.includes(ext)) {
            showToast(`无法预览二进制文件: ${filepath.split('/').pop()}`, 'info');
            return;
        }

        if (window.FileManager && typeof window.FileManager.openFile === 'function') {
            try {
                await window.FileManager.openFile(fullPath);
            } catch (err) {
                showToast('打开文件失败: ' + err.message, 'error');
            }
        } else {
            showToast('文件管理器不可用', 'error');
        }
    }

    // ── API: Add to .gitignore ────────────────────────────────────

    /**
     * Add a file/directory pattern to .gitignore
     * @param {string} filepath - file path to ignore
     */
    async function addToGitignore(filepath) {
        const gitCwd = getGitCwd();
        if (!gitCwd) {
            showToast('未打开项目目录', 'error');
            return;
        }

        // Resolve paths relative to project dir (for /api/files/* which uses WORKSPACE as base)
        const gitignorePath = gitCwd ? (gitCwd + '/.gitignore') : '.gitignore';

        try {
            const resp = await fetch(`/api/files/read?path=${encodeURIComponent(gitignorePath)}`);
            let content = '';
            if (resp.ok) {
                const data = await resp.json();
                content = data.content || '';
            }

            // Check if already ignored
            const lines = content.split('\n');
            const pattern = filepath.replace(/^\//, '');
            if (lines.some(l => l.trim() === pattern || l.trim() === '/' + pattern)) {
                showToast('已在 .gitignore 中', 'info');
                return;
            }

            // Append new entry
            if (content && !content.endsWith('\n')) content += '\n';
            content += pattern + '\n';

            const writeResp = await fetch('/api/files/save', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ path: gitignorePath, content })
            });
            if (!writeResp.ok) {
                throw new Error('写入 .gitignore 失败');
            }

            gitLogSimple(`update .gitignore (+${pattern})`, null);
            showToast(`已添加 ${filepath} 到 .gitignore`, 'success');
            await refreshStatus();
        } catch (err) {
            showToast('添加 .gitignore 失败: ' + err.message, 'error');
        }
    }

    // ── API: Delete File ──────────────────────────────────────────

    /**
     * Delete a file from the filesystem (with confirmation)
     * @param {string} filepath - file path to delete
     */
    async function deleteGitFile(filepath) {
        const confirmed = await confirmDialog(
            '删除文件',
            `确定要删除 "${filepath.split('/').pop()}" 吗？\n路径: ${filepath}\n\n此操作不可撤销。`
        );
        if (!confirmed) return;

        // Use git-specific delete endpoint which resolves paths relative to git root
        try {
            const resp = await fetch('/api/git/delete-file', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ filepath })
            });
            if (!resp.ok) {
                const errData = await resp.json().catch(() => ({}));
                throw new Error(errData.error || '删除失败');
            }

            gitLogSimple(`rm ${filepath}`, null);
            showToast(`已删除 ${filepath.split('/').pop()}`, 'success');
            await refresh();

            // Refresh file list
            if (window.FileManager) await window.FileManager.refresh();
        } catch (err) {
            showToast('删除失败: ' + err.message, 'error');
        }
    }

    // ── UI Helpers ─────────────────────────────────────────────────

    function getStatusIcon(status) {
        const s = (status || '').toLowerCase();
        if (s.includes('added') || s.includes('new') || s.includes('a ')) return '🟢';
        if (s.includes('modified') || s.includes('m ') || s.includes('changed')) return '🟡';
        if (s.includes('deleted') || s.includes('d ')) return '🔴';
        if (s.includes('renamed') || s.includes('r ')) return '🔵';
        if (s.includes('untracked') || s.includes('?')) return '⚪';
        if (s.includes('staged') || s === 'staged') return '🟢';
        return '⚪';
    }

    function escapeHTML(str) {
        const div = document.createElement('div');
        div.textContent = str || '';
        return div.innerHTML;
    }

    function escapeAttr(str) {
        return (str || '').replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/'/g, '&#39;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    }

    /**
     * Simple prompt dialog (replaces window.prompt for mobile)
     */
    function promptDialog(title, label, defaultValue) {
        return new Promise((resolve) => {
            if (window.showPromptDialog) {
                window.showPromptDialog(title, label, defaultValue, resolve);
                return;
            }
            const result = window.prompt(`${title}\n${label}`, defaultValue);
            resolve(result);
        });
    }

    /**
     * Simple confirm dialog
     */
    function confirmDialog(title, message) {
        return new Promise((resolve) => {
            if (window.showConfirmDialog) {
                window.showConfirmDialog(title, message, resolve);
                return;
            }
            const result = window.confirm(`${title}\n${message}`);
            resolve(result);
        });
    }

    /**
     * Choice dialog — shows a list of options
     * Returns a promise resolving to the chosen value or null.
     */
    function choiceDialog(title, label, options) {
        return new Promise((resolve) => {
            if (window.showChoiceDialog) {
                window.showChoiceDialog(title, label, options, resolve);
                return;
            }
            // Fallback: join options and let user type one
            const optStr = options.map(o => o.value || o).join(', ');
            const result = window.prompt(`${title}\n${label}\n\nOptions: ${optStr}`, '');
            if (!result) return resolve(null);

            // Match input to an option
            const match = options.find(o => {
                const val = o.value || o;
                return val.toLowerCase() === result.trim().toLowerCase();
            });
            resolve(match ? (match.value || match) : result.trim());
        });
    }

    // ── Wire Up Buttons ────────────────────────────────────────────

    function wireButtons() {
        const buttonMap = {
            'git-init-btn': () => gitInit(),
            'git-clone': () => clone(),
            'git-pull': () => pull(),
            'git-push': () => push(),
            'git-sync': () => sync(),
            'git-diff-all-btn': () => diff(),
            'git-refresh': () => refresh(),
            'git-token-btn': () => showTokenConfig(),
            'git-commit-btn': () => commit(),
            'git-add-all-btn': () => addAll(),
            'git-stash-btn': () => stash(),
            'git-checkout-btn': () => checkout(),
        };

        for (const [id, handler] of Object.entries(buttonMap)) {
            const btn = document.getElementById(id);
            if (btn) {
                // Wrap handler with try/catch for error visibility
                const safeHandler = async () => {
                    try {
                        await handler();
                    } catch (err) {
                        console.error('[GitManager] Button error (' + id + '):', err);
                        if (window.showToast) window.showToast('操作失败: ' + err.message, 'error');
                    }
                };

                // Use bindTouchButton for reliable Android WebView tap handling
                if (window.bindTouchButton) {
                    window.bindTouchButton(btn, () => safeHandler());
                } else {
                    // Fallback: standard click
                    btn.addEventListener('click', () => safeHandler());
                }
            }
        }

        // Commit on Ctrl/Cmd+Enter in message input
        const msgEl = document.getElementById('git-commit-msg');
        if (msgEl) {
            msgEl.addEventListener('keydown', (e) => {
                if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
                    e.preventDefault();
                    commit();
                }
            });
        }
    }

    // ── Initialize ─────────────────────────────────────────────────

    function init() {
        wireButtons();
        initLogInfiniteScroll();
        // Initial refresh
        refresh();
    }

    // Delay wireButtons to ensure bindTouchButton is available from app.js.
    // git.js loads before app.js, so its DOMContentLoaded handler fires first.
    let _wired = false;
    function ensureWired() {
        if (_wired) return;
        if (window.bindTouchButton) {
            // app.js already loaded, use touch-friendly binding
            _wired = true;
            wireButtons();
            initLogInfiniteScroll();
            refresh();
        } else {
            // app.js hasn't registered bindTouchButton yet, poll for it
            const check = setInterval(() => {
                if (window.bindTouchButton) {
                    clearInterval(check);
                    _wired = true;
                    wireButtons();
                    initLogInfiniteScroll();
                    refresh();
                }
            }, 10);
            // Safety timeout: wire buttons after 500ms regardless (fallback to click)
            setTimeout(() => {
                clearInterval(check);
                if (!_wired) {
                    _wired = true;
                    wireButtons();
                    refresh();
                }
            }, 500);
        }
    }

    // Listen for directory changes from FileManager
    document.addEventListener('filemanager:navigate', () => {
        // Debounce: don't refresh on every navigation event
        clearTimeout(window._gitNavTimer);
        window._gitNavTimer = setTimeout(() => refresh(), 200);
    });

    // Auto-init when DOM is ready
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', ensureWired);
    } else {
        ensureWired();
    }

    // ── Public API ─────────────────────────────────────────────────
    return {
        refreshStatus,
        refreshLog,
        refreshBranches,
        refresh,
        clone,
        pull,
        push,
        sync,
        addFiles,
        addAll,
        commit,
        checkout,
        stash,
        diff,
        gitInit,
        restoreFile,
        checkoutCommit,
        showCommitFileDiff,
        openGitFile,
        addToGitignore,
        deleteGitFile,

        // Getters
        get currentBranch() { return currentBranch; },
        get status() { return statusData; },
        get log() { return logData; },
        get branches() { return branchData; }
    };
})();

window.GitManager = GitManager;
