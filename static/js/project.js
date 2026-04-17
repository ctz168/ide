/**
 * ProjectManager - Project management for PhoneIDE
 * Handles open/close/clone project operations
 */
const ProjectManager = (() => {
    'use strict';

    // ── State ──────────────────────────────────────────────────────
    let currentProject = null;  // { project: 'myrepo', name: 'myrepo' }
    let pickerPath = '';        // current path in folder picker (relative to workspace)

    // ── Helpers ────────────────────────────────────────────────────
    function safeToast(msg, type) {
        if (window.showToast) window.showToast(msg, type);
        else console.warn('[ProjectManager]', msg);
    }

    function escapeHTML(str) {
        const div = document.createElement('div');
        div.textContent = str || '';
        return div.innerHTML;
    }

    function escapeAttr(str) {
        return (str || '').replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/'/g, '&#39;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    }

    // ── API Calls ──────────────────────────────────────────────────

    /**
     * Load current project info from server
     */
    async function loadProjectInfo() {
        try {
            const resp = await fetch('/api/project/info');
            if (!resp.ok) return;
            const data = await resp.json();
            if (data.project) {
                currentProject = data;
                onProjectOpened(data);

                // Navigate FileManager into the project directory
                if (window.FileManager) {
                    await window.FileManager.loadFileList(data.project);
                }

                // Refresh git status in the project directory
                if (window.GitManager) {
                    await window.GitManager.refresh();
                }

                // If project is open on startup, switch to files tab
                switchToFilesTab();
            } else {
                currentProject = null;
                onProjectClosed();
            }
        } catch (err) {
            console.warn('[ProjectManager] Failed to load project info:', err);
        }
    }

    /**
     * Open a project, git init it, and switch to files tab
     */
    async function openProject(projectPath) {
        try {
            const resp = await fetch('/api/project/open', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ project: projectPath })
            });
            if (!resp.ok) {
                const err = await resp.json().catch(() => ({}));
                throw new Error(err.error || 'Failed to open project');
            }
            const data = await resp.json();
            currentProject = data;
            safeToast(`项目已打开: ${data.name}`, 'success');
            onProjectOpened(data);

            // Git init the project (safe to call even if already a git repo)
            try {
                await gitInitProject(projectPath);
            } catch (e) {
                console.warn('[ProjectManager] Git init skipped:', e.message);
            }

            // Navigate FileManager into the project directory
            if (window.FileManager) {
                await window.FileManager.loadFileList(projectPath);
            }

            // Refresh git status
            if (window.GitManager) {
                await window.GitManager.refresh();
            }

            // Switch to files tab
            switchToFilesTab();

            return data;
        } catch (err) {
            safeToast('打开项目失败: ' + err.message, 'error');
        }
    }

    /**
     * Git init a project directory
     */
    async function gitInitProject(projectPath) {
        try {
            const resp = await fetch('/api/git/init', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ path: projectPath })
            });
            if (!resp.ok) {
                const err = await resp.json().catch(() => ({}));
                // It's ok if git init fails (already a git repo)
                console.warn('[ProjectManager] Git init result:', err.error || 'non-ok');
                return;
            }
            const data = await resp.json();
            if (data.note) {
                safeToast(data.note, 'success');
            }
        } catch (err) {
            console.warn('[ProjectManager] Git init error:', err.message);
        }
    }

    /**
     * Close the current project
     */
    async function closeProject() {
        if (!currentProject) return;

        try {
            const resp = await fetch('/api/project/close', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' }
            });
            if (!resp.ok) throw new Error('Failed to close project');

            const projectName = currentProject.name;
            currentProject = null;

            // ── Reverse of openProject: full cleanup ──

            // 1. Clear editor search state (search scope changes)
            if (window.SearchManager) {
                window.SearchManager.clearResults();
            }

            // 2. Clear all open editor tabs (clean slate for workspace)
            if (window.EditorManager) {
                const tabList = window.EditorManager.getTabList();
                for (const tabPath of [...tabList]) {  // copy array since closeTab mutates it
                    window.EditorManager.closeTab(tabPath);
                }
            }

            // 3. Update UI: title, close button, project info panel, hide tabs
            //    This also dispatches project:closed event → FileManager resets projectRoot
            onProjectClosed();

            safeToast(`项目已关闭: ${projectName}`, 'success');

            // 4. Return FileManager to workspace root (undo navigation into project)
            if (window.FileManager) {
                await window.FileManager.loadFileList('');
            }

            // 5. Reset git status (workspace level, no git context)
            if (window.GitManager) {
                await window.GitManager.refresh();
            }

            // 6. Switch to project tab (reverse of switchToFilesTab in openProject)
            switchToProjectTab();
        } catch (err) {
            safeToast('关闭项目失败: ' + err.message, 'error');
        }
    }

    /**
     * Clone a project (clone in workspace, then open it as project with git init)
     */
    async function cloneProject() {
        // Use GitManager's clone dialog for URL input, then open as project
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

        let url = result.url;
        if (result.token) {
            // Save token
            try {
                await fetch('/api/config', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ github_token: result.token })
                });
            } catch (_e) {}
            // Inject token into URL
            if (result.token && url.includes('github.com') && !url.includes('@')) {
                url = url.replace('https://', `https://${result.token}@`);
            }
        }

        safeToast('正在克隆项目...', 'info');

        try {
            const resp = await fetch('/api/git/clone', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ url })
            });
            if (!resp.ok) {
                const errData = await resp.json().catch(() => ({}));
                throw new Error(errData.error || 'Clone failed');
            }
            const data = await resp.json();
            const clonePath = data.path;

            // Open as project (will git init and switch to files tab)
            safeToast('克隆成功，正在打开项目...', 'success');
            await openProject(clonePath);

            return data;
        } catch (err) {
            safeToast('克隆项目失败: ' + err.message, 'error');
        }
    }

    // ── Folder Picker ─────────────────────────────────────────────

    /**
     * Show folder picker for opening a project
     */
    async function showFolderPicker() {
        pickerPath = '';
        const pickerEl = document.getElementById('project-folder-picker');
        const infoEl = document.getElementById('project-info');
        if (pickerEl) pickerEl.classList.remove('hidden');
        if (infoEl) infoEl.classList.add('hidden');
        await loadPickerFolders('');
    }

    function hideFolderPicker() {
        const pickerEl = document.getElementById('project-folder-picker');
        const infoEl = document.getElementById('project-info');
        if (pickerEl) pickerEl.classList.add('hidden');
        if (infoEl) infoEl.classList.remove('hidden');
    }

    async function loadPickerFolders(path) {
        pickerPath = path;
        try {
            const params = path ? `?path=${encodeURIComponent(path)}` : '';
            const resp = await fetch(`/api/project/list_folders${params}`);
            if (!resp.ok) throw new Error('Failed to list folders');
            const data = await resp.json();

            // Update header path display
            const pathEl = document.getElementById('project-picker-path');
            if (pathEl) {
                pathEl.textContent = '/' + (data.current_path || '');
            }

            // Show/hide back button
            const backBtn = document.getElementById('project-picker-back');
            if (backBtn) {
                backBtn.style.display = pickerPath ? '' : 'none';
            }

            // Render folder list with "设为项目" button per entry
            const listEl = document.getElementById('project-picker-list');
            if (!listEl) return;

            if (data.folders.length === 0) {
                listEl.innerHTML = '<div style="text-align:center;padding:20px;color:var(--text-muted);font-size:12px;">此目录下没有文件夹</div>';
                return;
            }

            let html = '';
            for (const folder of data.folders) {
                const gitIcon = folder.has_git ? ' 🔀' : '';
                html += `
                    <div class="project-folder-item" data-path="${escapeAttr(folder.path)}">
                        <div class="project-folder-info" data-path="${escapeAttr(folder.path)}">
                            <span class="icon">📁</span>
                            <span class="name">${escapeHTML(folder.name)}</span>
                            <span class="git-badge">${gitIcon}</span>
                        </div>
                        <button class="project-folder-set-btn" data-path="${escapeAttr(folder.path)}" title="设为项目">设为项目</button>
                    </div>`;
            }

            listEl.innerHTML = html;

            // Bind click events for folder navigation (clicking the folder info area navigates in)
            listEl.querySelectorAll('.project-folder-info').forEach(item => {
                const handler = async () => {
                    const itemPath = item.dataset.path;
                    await loadPickerFolders(itemPath);
                };
                if (window.bindTouchButton) {
                    window.bindTouchButton(item, handler);
                } else {
                    item.addEventListener('click', handler);
                }
            });

            // Bind click events for "设为项目" buttons
            listEl.querySelectorAll('.project-folder-set-btn').forEach(btn => {
                const handler = async (e) => {
                    e.stopPropagation();
                    const itemPath = btn.dataset.path;
                    await openProject(itemPath);
                };
                if (window.bindTouchButton) {
                    window.bindTouchButton(btn, handler);
                } else {
                    btn.addEventListener('click', handler);
                }
            });
        } catch (err) {
            safeToast('加载文件夹失败: ' + err.message, 'error');
        }
    }

    async function pickerGoBack() {
        if (!pickerPath) return;
        const parts = pickerPath.split('/');
        parts.pop();
        const parentPath = parts.join('/');
        await loadPickerFolders(parentPath);
    }

    // ── Clone Dialog ──────────────────────────────────────────────

    function showCloneDialog(savedToken, tokenHint) {
        return new Promise((resolve) => {
            if (window.showDialog) {
                const bodyHTML = `
                    <div style="display:flex;flex-direction:column;gap:12px;">
                        <div>
                            <label style="display:block;font-size:12px;color:var(--text-muted);margin-bottom:4px;">仓库地址</label>
                            <input type="text" id="project-clone-url" placeholder="https://github.com/user/repo.git" autocomplete="off"
                                style="width:100%;padding:8px 10px;border-radius:6px;border:1px solid #555;background:#2a2a2a;color:#ddd;font-size:13px;box-sizing:border-box;">
                        </div>
                        <div>
                            <label style="display:block;font-size:12px;color:var(--text-muted);margin-bottom:4px;">GitHub Token (${tokenHint})</label>
                            <input type="password" id="project-clone-token" placeholder="${savedToken ? '已配置，留空使用已保存' : '公开仓库无需填写'}"
                                style="width:100%;padding:8px 10px;border-radius:6px;border:1px solid #555;background:#2a2a2a;color:#ddd;font-size:13px;box-sizing:border-box;">
                        </div>
                    </div>`;
                window.showDialog('📥 克隆项目', bodyHTML, [
                    { text: '取消', value: 'cancel', class: 'btn-cancel' },
                    { text: '克隆并打开', value: 'ok', class: 'btn-confirm' },
                ]).then(result => {
                    if (!result.confirmed) { resolve(null); return; }
                    const urlInput = document.getElementById('project-clone-url');
                    const tokenInput = document.getElementById('project-clone-token');
                    const url = urlInput ? urlInput.value.trim() : '';
                    const token = tokenInput ? tokenInput.value.trim() : '';
                    if (!url) { resolve(null); return; }
                    resolve({ url, token });
                });
                return;
            }
            const url = window.prompt('Clone Repository URL:', 'https://github.com/user/repo.git');
            if (url) resolve({ url, token: '' });
            else resolve(null);
        });
    }

    // ── UI Updates ─────────────────────────────────────────────────

    function onProjectOpened(data) {
        // Update header title
        const titleEl = document.getElementById('project-title');
        if (titleEl) {
            titleEl.textContent = ' - ' + data.name;
            titleEl.classList.remove('hidden');
        }

        // Update close button visibility
        const closeBtn = document.getElementById('btn-close-project');
        if (closeBtn) closeBtn.style.display = '';

        // Update project info panel
        const currentEl = document.getElementById('project-current');
        if (currentEl) {
            currentEl.className = 'project-active';
            const gitStatus = data.has_git ? '🔀 Git 仓库' : '';
            currentEl.innerHTML = `
                <div style="padding:12px;">
                    <div style="display:flex;align-items:center;gap:8px;margin-bottom:8px;">
                        <span style="font-size:20px;">📁</span>
                        <span style="font-size:14px;font-weight:600;color:var(--text-primary);">${escapeHTML(data.name)}</span>
                    </div>
                    <div style="font-size:11px;color:var(--text-muted);font-family:var(--font-mono);word-break:break-all;">${escapeHTML(data.path || data.project)}</div>
                    <div style="font-size:11px;color:var(--text-secondary);margin-top:4px;">${gitStatus}</div>
                </div>`;
        }

        // Show project-only tabs (Git, Debug)
        showProjectTabs(true);

        // Dispatch event for other modules (AI assistant, etc.)
        document.dispatchEvent(new CustomEvent('project:opened', { detail: data }));

        // Hide folder picker if open
        hideFolderPicker();
    }

    function onProjectClosed() {
        // Update header title
        const titleEl = document.getElementById('project-title');
        if (titleEl) {
            titleEl.textContent = '';
            titleEl.classList.add('hidden');
        }

        // Update close button visibility
        const closeBtn = document.getElementById('btn-close-project');
        if (closeBtn) closeBtn.style.display = 'none';

        // Update project info panel
        const currentEl = document.getElementById('project-current');
        if (currentEl) {
            currentEl.className = 'project-no-project';
            currentEl.innerHTML = `
                <div style="text-align:center;padding:30px 12px;">
                    <div style="font-size:36px;margin-bottom:10px;">📁</div>
                    <div style="font-size:13px;color:var(--text-secondary);margin-bottom:6px;">未打开项目</div>
                    <div style="font-size:11px;color:var(--text-muted);">点击「打开项目」选择一个文件夹，或点击「克隆项目」从远程克隆</div>
                </div>`;
        }

        // Hide project-only tabs (Git, Debug) - reverse of onProjectOpened
        showProjectTabs(false);

        // Switch away from hidden tabs if currently active
        switchAwayFromProjectTabs();

        // Dispatch event for other modules
        document.dispatchEvent(new CustomEvent('project:closed'));
    }

    function switchToFilesTab() {
        // Click the files tab
        const filesTab = document.querySelector('#left-tabs .tab[data-tab="files"]');
        if (filesTab) filesTab.click();
    }

    function switchToProjectTab() {
        // Switch to project tab (reverse of switchToFilesTab)
        const projectTab = document.querySelector('#left-tabs .tab[data-tab="project"]');
        if (projectTab) projectTab.click();
    }

    /**
     * Show/hide project-only tabs (Git, Debug)
     * @param {boolean} show - true to show, false to hide
     */
    function showProjectTabs(show) {
        document.querySelectorAll('#left-tabs .tab-project-only').forEach(tab => {
            tab.style.display = show ? '' : 'none';
        });
    }

    /**
     * If currently active tab is a project-only tab (git/debug),
     * switch to the project tab instead.
     */
    function switchAwayFromProjectTabs() {
        const activeTab = document.querySelector('#left-tabs .tab.active');
        if (activeTab && activeTab.classList.contains('tab-project-only')) {
            // Switch to project tab
            switchToProjectTab();
        }
    }

    // ── Wire Up Buttons ────────────────────────────────────────────

    function wireButtons() {
        const openBtn = document.getElementById('btn-open-project');
        if (openBtn) {
            const handler = () => showFolderPicker();
            if (window.bindTouchButton) {
                window.bindTouchButton(openBtn, handler);
            } else {
                openBtn.addEventListener('click', handler);
            }
        }

        const cloneBtn = document.getElementById('btn-clone-project');
        if (cloneBtn) {
            const handler = () => cloneProject();
            if (window.bindTouchButton) {
                window.bindTouchButton(cloneBtn, handler);
            } else {
                cloneBtn.addEventListener('click', handler);
            }
        }

        const closeBtn = document.getElementById('btn-close-project');
        if (closeBtn) {
            const handler = () => {
                if (window.showConfirmDialog) {
                    window.showConfirmDialog('关闭项目', '确定要关闭当前项目吗？文件视图将返回工作区。', (confirmed) => {
                        if (confirmed) closeProject();
                    });
                } else {
                    if (confirm('确定要关闭当前项目吗？')) closeProject();
                }
            };
            if (window.bindTouchButton) {
                window.bindTouchButton(closeBtn, handler);
            } else {
                closeBtn.addEventListener('click', handler);
            }
        }

        const backBtn = document.getElementById('project-picker-back');
        if (backBtn) {
            const handler = () => pickerGoBack();
            if (window.bindTouchButton) {
                window.bindTouchButton(backBtn, handler);
            } else {
                backBtn.addEventListener('click', handler);
            }
        }

        // "select" button removed - each folder now has its own "设为项目" button
    }

    // ── Initialize ─────────────────────────────────────────────────

    let _wired = false;
    function ensureWired() {
        if (_wired) return;
        if (window.bindTouchButton) {
            _wired = true;
            wireButtons();
            loadProjectInfo();
        } else {
            const check = setInterval(() => {
                if (window.bindTouchButton) {
                    clearInterval(check);
                    _wired = true;
                    wireButtons();
                    loadProjectInfo();
                }
            }, 10);
            setTimeout(() => {
                clearInterval(check);
                if (!_wired) {
                    _wired = true;
                    wireButtons();
                    loadProjectInfo();
                }
            }, 500);
        }
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', ensureWired);
    } else {
        ensureWired();
    }

    // ── Public API ─────────────────────────────────────────────────
    return {
        loadProjectInfo,
        openProject,
        closeProject,
        cloneProject,
        getCurrentProject: () => currentProject,
    };
})();

window.ProjectManager = ProjectManager;
