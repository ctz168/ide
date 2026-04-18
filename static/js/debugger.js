/**
 * CodeDebugger - Runtime Python debugger UI for the bottom-panel "调试" tab.
 *
 * Architecture:
 *   Backend (routes/debug.py) → SSE /api/debug/state/stream → frontend renders state
 *   Frontend buttons → POST /api/debug/continue|step|stop
 *   Editor gutter clicks → toggleBreakpoint() → sync to backend
 *
 * The debugger uses sys.settrace() – breakpoints and variable inspection
 * only work for Python files.
 */
const CodeDebugger = (() => {
    'use strict';

    // ── State ──
    let sseSource = null;
    let currentState = 'no_session';
    let breakpoints = new Map(); // key: "file:line" → true

    function escapeHTML(str) {
        const div = document.createElement('div');
        div.textContent = str || '';
        return div.innerHTML;
    }

    // ── SSE ────────────────────────────────────────────────────────

    function connectSSE() {
        disconnectSSE();
        try {
            sseSource = new EventSource('/api/debug/state/stream');
            sseSource.onmessage = (e) => {
                try { handleStateUpdate(JSON.parse(e.data)); }
                catch {}
            };
            sseSource.onerror = () => {
                disconnectSSE();
                setTimeout(connectSSE, 2000);
            };
        } catch {}
    }

    function disconnectSSE() {
        if (sseSource) { sseSource.close(); sseSource = null; }
    }

    // ── State Update ────────────────────────────────────────────────

    function handleStateUpdate(data) {
        currentState = data.state;

        // Status label
        const el = document.getElementById('debug-status');
        if (el) {
            const labels = {
                'no_session': '未启动', 'idle': '就绪',
                'running': '▶ 运行中…', 'paused': `⏸ ${data.currentFileShort}:${data.currentLine}`,
                'stopped': '⏹ 已停止', 'error': '❌ 错误',
            };
            el.textContent = labels[data.state] || data.state;
            el.style.color =
                data.state === 'paused' ? 'var(--yellow)' :
                data.state === 'error' ? 'var(--red)' :
                data.state === 'running' ? 'var(--green)' : 'var(--text-muted)';
        }

        updateButtons(data.state);
        renderBreakpoints(data.breakpoints || []);
        renderVariables(data.variables || []);
        renderStack(data.callStack || []);
        renderActivity(data.activity || []);

        // Forward output to terminal
        if (data.outputLines) {
            for (const line of data.outputLines) {
                if (window.TerminalManager) {
                    window.TerminalManager.appendOutput(line.text,
                        line.type === 'stderr' ? 'stderr' : 'stdout');
                }
            }
        }

        // Highlight line in editor
        highlightLine(data.currentFile, data.currentLine, data.state === 'paused');

        if (data.done) disconnectSSE();
    }

    function updateButtons(state) {
        const paused = state === 'paused';
        const active = state === 'running' || paused;
        const btns = {
            'debug-continue-btn': paused,
            'debug-stepover-btn': paused,
            'debug-stepinto-btn': paused,
            'debug-stepout-btn': paused,
            'debug-stop-btn': active,
        };
        for (const [id, enabled] of Object.entries(btns)) {
            const b = document.getElementById(id);
            if (b) b.disabled = !enabled;
        }
    }

    // ── Render ────────────────────────────────────────────────────────

    function renderBreakpoints(bps) {
        const list = document.getElementById('debug-bp-list');
        const count = document.getElementById('debug-bp-count');
        if (count) count.textContent = bps.length;
        if (!list) return;
        list.innerHTML = '';
        if (!bps.length) {
            list.innerHTML = '<div style="color:var(--text-muted);font-size:10px;padding:2px 0;">无断点 — 在编辑器行号上点击添加</div>';
            return;
        }
        for (const bp of bps) {
            const d = document.createElement('div');
            d.style.cssText = 'display:flex;align-items:center;gap:4px;padding:2px 0;font-size:11px;cursor:pointer;';
            d.innerHTML = `<span style="color:var(--red);">●</span>` +
                `<span style="color:var(--text-secondary);font-family:var(--font-mono);">${escapeHTML(bp.fileShort)}:${bp.line}</span>`;
            d.addEventListener('click', () => apiRemoveBreakpoint(bp.file, bp.line));
            list.appendChild(d);
        }
    }

    function renderVariables(variables) {
        const c = document.getElementById('debug-vars');
        if (!c) return;
        c.innerHTML = '';
        if (!variables.length) {
            c.innerHTML = '<div style="color:var(--text-muted);font-size:10px;padding:2px 0;">无变量（未在断点处暂停）</div>';
            return;
        }
        for (const v of variables) {
            const d = document.createElement('div');
            d.style.cssText = 'padding:3px 0;border-bottom:1px solid var(--border);';
            d.innerHTML = `<span style="color:var(--blue);white-space:nowrap;">${escapeHTML(v.name)}</span>` +
                `<span style="color:var(--text-muted);"> = </span>` +
                `<span style="color:var(--text-secondary);word-break:break-all;">${escapeHTML(v.value).substring(0, 300)}</span>`;
            c.appendChild(d);
        }
    }

    function renderStack(stack) {
        const c = document.getElementById('debug-stack');
        if (!c) return;
        c.innerHTML = '';
        if (!stack.length) {
            c.innerHTML = '<div style="color:var(--text-muted);font-size:10px;">空</div>';
            return;
        }
        for (const fr of stack) {
            const cur = fr.index === 0;
            const d = document.createElement('div');
            d.style.cssText = `padding:3px 0;border-bottom:1px solid var(--border);cursor:pointer;${cur ? 'color:var(--yellow);font-weight:bold;' : ''}`;
            d.innerHTML = `<div style="font-size:10px;">${cur ? '▸ ' : '  '}${escapeHTML(fr.func)}()</div>` +
                `<div style="font-size:9px;color:var(--text-muted);">${escapeHTML(fr.fileShort)}:${fr.line}</div>`;
            d.addEventListener('click', () => {
                if (window.EditorManager) {
                    window.EditorManager.openFileAtLine(fr.file, fr.line);
                }
            });
            c.appendChild(d);
        }
    }

    function renderActivity(items) {
        const c = document.getElementById('debug-activity');
        if (!c) return;
        c.innerHTML = '';
        const recent = items.slice(-50);
        const icons = {
            'start': { i: '🚀', cl: 'var(--green)' },
            'breakpoint': { i: '⚡', cl: 'var(--yellow)' },
            'step': { i: '⏭', cl: 'var(--blue)' },
            'continue': { i: '▶', cl: 'var(--green)' },
            'evaluate': { i: '💡', cl: 'var(--mauve)' },
            'error': { i: '❌', cl: 'var(--red)' },
            'stop': { i: '⏹', cl: 'var(--text-muted)' },
            'info': { i: 'ℹ', cl: 'var(--blue)' },
        };
        for (const act of recent) {
            const t = new Date(act.time * 1000).toLocaleTimeString();
            const ic = icons[act.action] || { i: '•', cl: 'var(--text-secondary)' };
            const d = document.createElement('div');
            d.style.cssText = 'padding:2px 0;border-bottom:1px solid var(--border);';
            d.innerHTML =
                `<span style="color:var(--text-muted);font-size:9px;margin-right:4px;">${escapeHTML(t)}</span>` +
                `<span style="color:${ic.cl};margin-right:4px;">${ic.i}</span>` +
                `<span style="color:var(--text-secondary);word-break:break-all;">${escapeHTML(act.detail).substring(0, 200)}</span>`;
            c.appendChild(d);
        }
        c.scrollTop = c.scrollHeight;
    }

    // ── Editor integration ─────────────────────────────────────────

    function highlightLine(filePath, lineNum, isPaused) {
        clearHighlight();
        if (!isPaused || !filePath || !lineNum) return;

        const cm = window.EditorManager && window.EditorManager.getEditor();
        if (!cm) return;

        const curFile = window.EditorManager.getCurrentFile();
        if (curFile !== filePath) {
            if (window.EditorManager.openFileAtLine) {
                window.EditorManager.openFileAtLine(filePath, lineNum);
            }
            return;
        }

        cm.addLineClass(Math.max(0, lineNum - 1), 'background', 'cm-debug-hl');
        cm.scrollIntoView({ line: Math.max(0, lineNum - 1), ch: 0 }, 30);
    }

    function clearHighlight() {
        const cm = window.EditorManager && window.EditorManager.getEditor();
        if (!cm) return;
        cm.eachLine((line) => {
            cm.removeLineClass(line.lineNo(), 'background', 'cm-debug-hl');
        });
    }

    // ── Breakpoint management ───────────────────────────────────────

    function toggleBreakpoint(filePath, line) {
        const key = filePath + ':' + line;
        if (breakpoints.has(key)) {
            breakpoints.delete(key);
        } else {
            breakpoints.set(key, true);
        }
        updateGutterMarkers();
        syncBreakpointsToBackend();
    }

    function hasBreakpoint(filePath, line) {
        return breakpoints.has(filePath + ':' + line);
    }

    function updateGutterMarkers() {
        const cm = window.EditorManager && window.EditorManager.getEditor();
        if (!cm) return;
        const fp = window.EditorManager.getCurrentFile();
        if (!fp) return;
        cm.eachLine((line) => {
            const ln = line.lineNo() + 1;
            const el = line.lineInfo();
            const marker = document.createElement('div');
            marker.style.cssText = 'width:12px;height:12px;display:flex;align-items:center;justify-content:center;cursor:pointer;';
            marker.textContent = '●';
            if (hasBreakpoint(fp, ln)) {
                marker.style.color = 'var(--red)';
                cm.setGutterMarker(line.lineNo(), 'CodeMirror-breakpoints', marker);
            } else {
                cm.setGutterMarker(line.lineNo(), 'CodeMirror-breakpoints', null);
            }
        });
    }

    function syncBreakpointsToBackend() {
        if (currentState === 'no_session') return;
        const bps = [];
        for (const [key] of breakpoints) {
            const [f, l] = key.split(':');
            bps.push({ file: f, line: parseInt(l, 10) });
        }
        fetch('/api/debug/breakpoints', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ add: bps }),
        }).catch(() => {});
    }

    // ── API helpers ──────────────────────────────────────────────────

    async function apiStart(filePath, bps) {
        try {
            const r = await fetch('/api/debug/start', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ file_path: filePath, breakpoints: bps || [] }),
            });
            const d = await r.json();
            if (d.ok) connectSSE();
            return d;
        } catch (e) { return { error: e.message }; }
    }

    async function apiStop() {
        disconnectSSE();
        try { return await (await fetch('/api/debug/stop', { method: 'POST' })).json(); }
        catch (e) { return { error: e.message }; }
    }

    async function apiContinue() {
        try { return await (await fetch('/api/debug/continue', { method: 'POST' })).json(); }
        catch (e) { return { error: e.message }; }
    }

    async function apiStep(mode) {
        try { return await (await fetch('/api/debug/step', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ mode }),
        })).json(); }
        catch (e) { return { error: e.message }; }
    }

    async function apiRemoveBreakpoint(file, line) {
        try { await fetch('/api/debug/breakpoints', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ remove: [{ file, line }] }),
        }); } catch {}
    }

    // ── Init ──────────────────────────────────────────────────────────

    function init() {
        wireUI();
    }

    function wireUI() {
        const on = (id, fn) => {
            const b = document.getElementById(id);
            if (b) b.addEventListener('click', fn);
        };
        on('debug-continue-btn', apiContinue);
        on('debug-stepover-btn', () => apiStep('step_over'));
        on('debug-stepinto-btn', () => apiStep('step'));
        on('debug-stepout-btn', () => apiStep('step_out'));
        on('debug-stop-btn', apiStop);
        on('debug-clear-btn', () => {
            const c = document.getElementById('debug-activity');
            if (c) c.innerHTML = '';
        });

        const hdr = document.getElementById('debug-bp-header');
        if (hdr) {
            hdr.addEventListener('click', () => {
                const list = document.getElementById('debug-bp-list');
                if (list) {
                    const hidden = list.style.display === 'none';
                    list.style.display = hidden ? '' : 'none';
                }
            });
        }
    }

    // ── Boot ──
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }

    return {
        init,
        toggleBreakpoint,
        hasBreakpoint,
        updateGutterMarkers,
        apiStart,
        apiStop,
        apiContinue,
        apiStep,
        get currentState() { return currentState; },
    };
})();

window.CodeDebugger = CodeDebugger;
