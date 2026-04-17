/**
 * FormatterManager - Code formatting integration for PhoneIDE
 * Supports Black, Prettier, Shfmt, Gofmt, Rustfmt, and more
 */
const FormatterManager = (() => {
    'use strict';

    // ── State ──
    let availableFormatters = [];
    let currentFilePath = null;
    let currentFileExt = null;
    let active = false;

    // ── DOM Elements ──
    let panel = null;
    let selectEl = null;
    let currentFileEl = null;
    let fileTypeEl = null;
    let descEl = null;
    let statusEl = null;
    let resultsEl = null;
    let currentBtn = null;
    let workspaceBtn = null;
    let dryRunBtn = null;

    // ── Init ──
    function init() {
        panel = document.getElementById('bpanel-format');
        if (!panel) return;

        selectEl = document.getElementById('format-select');
        currentFileEl = document.getElementById('format-current-file');
        fileTypeEl = document.getElementById('format-file-type');
        descEl = document.getElementById('format-desc');
        statusEl = document.getElementById('format-status');
        resultsEl = document.getElementById('format-results');
        currentBtn = document.getElementById('format-current-btn');
        workspaceBtn = document.getElementById('format-workspace-btn');
        dryRunBtn = document.getElementById('format-dry-run-btn');

        if (!selectEl || !currentFileEl || !resultsEl) return;

        // Bind events
        selectEl.addEventListener('change', onFormatterChange);
        if (currentBtn) currentBtn.addEventListener('click', formatCurrentFile);
        if (workspaceBtn) workspaceBtn.addEventListener('click', formatWorkspace);
        if (dryRunBtn) dryRunBtn.addEventListener('click', dryRunWorkspace);

        // Load available formatters on init
        loadAvailableFormatters();

        // Watch for file changes in editor
        if (window.FileManager) {
            // Hook into file selection
            const origSelectFile = window.FileManager.selectFile;
            if (origSelectFile) {
                window.FileManager.selectFile = function(path) {
                    const result = origSelectFile.apply(this, arguments);
                    updateCurrentFileInfo();
                    return result;
                };
            }
        }

        // Update when editor saves file
        if (window.EditorManager) {
            const origSave = window.EditorManager.save;
            if (origSave) {
                window.EditorManager.save = async function(path, content) {
                    const result = await origSave.apply(this, arguments);
                    updateCurrentFileInfo();
                    return result;
                };
            }
        }

        active = true;
        updateCurrentFileInfo();
    }

    // ── API Calls ──
    async function loadAvailableFormatters() {
        try {
            const resp = await fetch('/api/formatter/available');
            if (!resp.ok) throw new Error('Failed to load');
            const data = await resp.json();
            availableFormatters = data.formatters || [];

            // Populate select
            populateFormatterSelect();
            updateCurrentFileInfo();
        } catch (err) {
            console.error('[Formatter] Failed to load formatters:', err);
            setStatus('无法加载格式化工具列表', 'error');
        }
    }

    function populateFormatterSelect() {
        if (!selectEl) return;

        // Clear existing options (keep first "auto" option)
        selectEl.innerHTML = '<option value="">自动检测</option>';

        for (const fmt of availableFormatters) {
            const opt = document.createElement('option');
            opt.value = fmt.extension;
            opt.textContent = `${fmt.name} (${fmt.extension})`;
            if (!fmt.available) {
                opt.disabled = true;
                opt.textContent += ' ⚠️';
            }
            opt.dataset.install = fmt.install || '';
            selectEl.appendChild(opt);
        }
    }

    function getFormatterForExt(ext) {
        return availableFormatters.find(f => f.extension === ext);
    }

    // ── Current File Info ──
    function updateCurrentFileInfo() {
        if (!currentFileEl || !fileTypeEl) return;

        let filePath = null;
        let ext = null;

        if (window.EditorManager && window.EditorManager.currentFilePath) {
            filePath = window.EditorManager.currentFilePath;
        } else if (window.FileManager && window.FileManager.currentPath) {
            filePath = window.FileManager.currentPath;
        }

        if (filePath) {
            currentFilePath = filePath;
            ext = '.' + filePath.split('.').pop().toLowerCase();
            currentFileExt = ext;

            currentFileEl.textContent = filePath;
            fileTypeEl.textContent = getFormatterForExt(ext)
                ? `${getFormatterForExt(ext).name} - ${getFormatterForExt(ext).description}`
                : '此文件类型暂不支持格式化';

            // Auto-select formatter if available
            if (selectEl && !selectEl.value) {
                const fmt = getFormatterForExt(ext);
                if (fmt && fmt.available) {
                    selectEl.value = ext;
                    onFormatterChange();
                }
            }
        } else {
            currentFilePath = null;
            currentFileExt = null;
            currentFileEl.textContent = '未打开文件';
            fileTypeEl.textContent = '-';
        }
    }

    function onFormatterChange() {
        const ext = selectEl.value;
        if (!ext) {
            descEl.textContent = '自动检测：根据文件扩展名选择格式化工具';
            return;
        }

        const fmt = getFormatterForExt(ext);
        if (fmt) {
            const status = fmt.available ? '✅ 已安装' : `❌ 未安装 (安装: \`${fmt.install}\`)`;
            descEl.textContent = `${fmt.name}: ${fmt.description}\n状态: ${status}`;
        }
    }

    // ── Formatting Operations ──
    async function formatCurrentFile() {
        if (!currentFilePath) {
            setStatus('请先打开一个文件', 'error');
            return;
        }

        const ext = currentFileExt || '.' + currentFilePath.split('.').pop().toLowerCase();
        const formatterExt = selectEl.value || ext;
        const fmt = getFormatterForExt(formatterExt);

        if (!fmt) {
            setStatus(`不支持 ${ext} 文件的格式化`, 'error');
            return;
        }

        if (!fmt.available) {
            setStatus(`格式化工具未安装: ${fmt.name}`, 'error');
            return;
        }

        setStatus(`正在格式化 ${currentFilePath}...`, 'warning');

        try {
            // Get current content from editor
            let content = '';
            if (window.EditorManager && window.EditorManager.getContent) {
                content = window.EditorManager.getContent();
            }

            // Call API to format content
            const resp = await fetch('/api/formatter/format', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    content: content,
                    file_path: currentFilePath
                })
            });

            const data = await resp.json();

            if (!resp.ok) {
                setStatus(`格式化失败: ${data.error || '未知错误'}`, 'error');
                return;
            }

            if (data.ok) {
                // Update editor content
                if (window.EditorManager && window.EditorManager.setContent) {
                    window.EditorManager.setContent(data.formatted);
                }
                setStatus(`✅ 格式化完成: ${currentFilePath}`, 'success');
                addResultItem(currentFilePath, true, '已格式化');
            } else {
                setStatus(`格式化失败: ${data.error}`, 'error');
                addResultItem(currentFilePath, false, data.error);
            }
        } catch (err) {
            setStatus(`错误: ${err.message}`, 'error');
            addResultItem(currentFilePath, false, err.message);
        }
    }

    async function formatWorkspace(dryRun = false) {
        const config = loadConfig();
        const base = config.workspace || '/';
        const path = '';  // workspace root

        setStatus(dryRun ? '正在扫描工作区...' : '正在格式化工作区...', 'warning');
        clearResults();

        try {
            const resp = await fetch('/api/formatter/format-workspace', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    path: path,
                    dry_run: dryRun
                })
            });

            const data = await resp.json();

            if (!resp.ok) {
                setStatus(`失败: ${data.error}`, 'error');
                return;
            }

            // Display summary
            displaySummary(data, dryRun);

            if (dryRun) {
                setStatus(`预览完成: ${data.formatted} 个文件将更改`, 'success');
            } else {
                setStatus(`格式化完成: ${data.formatted}/${data.total} 个文件已更改`, 'success');
            }

            // Show details
            resultsEl.innerHTML = '';
            for (const detail of data.details || []) {
                addResultItem(detail.file, detail.changed, detail.error, detail.would_change, detail.skipped);
            }

            // Show errors if any
            if (data.errors && data.errors.length > 0) {
                const errDiv = document.createElement('div');
                errDiv.style.cssText = 'margin-top:8px;padding:8px;background:rgba(243,139,168,0.1);border-radius:4px;font-size:10px;';
                errDiv.innerHTML = `<strong style="color:var(--red);">错误 (${data.errors.length}):</strong><br>` + data.errors.slice(0, 10).join('<br>').replace(/\n/g, '<br>');
                resultsEl.appendChild(errDiv);
            }

        } catch (err) {
            setStatus(`错误: ${err.message}`, 'error');
        }
    }

    async function dryRunWorkspace() {
        await formatWorkspace(true);
    }

    // ── UI Helpers ──
    function setStatus(msg, type = 'info') {
        if (!statusEl) return;
        statusEl.textContent = msg;
        statusEl.className = 'format-status ' + type;
    }

    function clearResults() {
        if (resultsEl) resultsEl.innerHTML = '';
    }

    function addResultItem(file, changed, error, wouldChange = false, skipped = false) {
        if (!resultsEl) return;

        const div = document.createElement('div');
        div.className = 'format-result-item';

        let icon = '📄';
        let statusText = '';

        if (error) {
            div.classList.add('error');
            icon = '❌';
            statusText = error;
        } else if (skipped) {
            div.classList.add('unchanged');
            icon = '⏭️';
            statusText = '跳过';
        } else if (wouldChange) {
            div.classList.add('changed');
            icon = '✏️';
            statusText = '将更改';
        } else if (changed) {
            div.classList.add('changed');
            icon = '✅';
            statusText = '已更改';
        } else {
            div.classList.add('unchanged');
            icon = '➖';
            statusText = '无变化';
        }

        div.innerHTML = `
            <span class="format-icon">${icon}</span>
            <span class="format-file" title="${escapeHTML(file)}">${escapeHTML(file)}</span>
            <span class="format-reason" style="color:${error ? 'var(--red)' : 'var(--text-muted)'}">${escapeHTML(statusText)}</span>
        `;

        resultsEl.appendChild(div);
        resultsEl.scrollTop = resultsEl.scrollHeight;
    }

    function displaySummary(data, dryRun) {
        if (!resultsEl) return;

        const summary = document.createElement('div');
        summary.className = 'format-summary';
        summary.innerHTML = `
            <div class="format-stat">
                <span class="format-stat-value">${data.total}</span>
                <span class="format-stat-label">总计</span>
            </div>
            <div class="format-stat">
                <span class="format-stat-value" style="color:var(--green)">${data.formatted}</span>
                <span class="format-stat-label">${dryRun ? '将改' : '已改'}</span>
            </div>
            <div class="format-stat">
                <span class="format-stat-value" style="color:var(--text-muted)">${data.skipped}</span>
                <span class="format-stat-label">跳过</span>
            </div>
            <div class="format-stat">
                <span class="format-stat-value" style="color:var(--red)">${(data.errors || []).length}</span>
                <span class="format-stat-label">错误</span>
            </div>
        `;
        resultsEl.appendChild(summary);
    }

    function escapeHTML(str) {
        const div = document.createElement('div');
        div.textContent = str || '';
        return div.innerHTML;
    }

    function loadConfig() {
        // Try to get config from server or local storage
        if (window.App && window.App.config) {
            return window.App.config;
        }
        // Fallback
        return { workspace: '/' };
    }

    // ── Boot ──
    function boot() {
        if (document.readyState === 'loading') {
            document.addEventListener('DOMContentLoaded', init);
        } else {
            init();
        }
    }

    boot();

    return {
        init,
        refresh: updateCurrentFileInfo,
        get available() { return active; }
    };
})();

window.FormatterManager = FormatterManager;
