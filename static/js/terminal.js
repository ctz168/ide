/**
 * TerminalManager - Code execution and output display for PhoneIDE
 * Works with Flask backend on port 1239
 */
const TerminalManager = (() => {
    'use strict';

    // ── State ──────────────────────────────────────────────────────
    let currentProcId = null;
    let isRunning = false;
    let eventSource = null;         // SSE connection
    let pollTimer = null;           // polling fallback timer
    let pollSince = 0;              // last line index seen
    let compilers = [];             // cached compiler list
    let panelHeight = 250;          // current panel height in px
    let isDragging = false;         // resize drag state
    let dragStartY = 0;            // touch/mouse Y at drag start
    let dragStartHeight = 0;        // panel height at drag start

    // ── Constants ──────────────────────────────────────────────────
    const MIN_PANEL_HEIGHT = 100;
    const MAX_PANEL_HEIGHT = window.innerHeight ? Math.floor(window.innerHeight * 0.8) : 500;
    const POLL_INTERVAL = 500;      // ms between poll requests
    const MAX_OUTPUT_LINES = 5000;  // max lines before trimming

    // ── Helpers ────────────────────────────────────────────────────

    /**
     * Escape HTML entities for safe insertion into output
     */
    function escapeHTML(str) {
        const div = document.createElement('div');
        div.textContent = str || '';
        return div.innerHTML;
    }

    /**
     * Get current timestamp string for log lines
     */
    function timestamp() {
        const now = new Date();
        return now.toLocaleTimeString();
    }

    /**
     * Trim old output lines if we exceed the maximum
     */
    function trimOutput() {
        const container = document.getElementById('output-content');
        if (!container) return;

        const lines = container.querySelectorAll('.output-line');
        if (lines.length > MAX_OUTPUT_LINES) {
            const removeCount = lines.length - MAX_OUTPUT_LINES;
            for (let i = 0; i < removeCount; i++) {
                lines[i].remove();
            }
        }
    }

    // ── API: Compilers ─────────────────────────────────────────────

    /**
     * Load available compilers from the backend and populate the select dropdown
     * @returns {Promise<Array>} list of compiler objects
     */
    async function loadCompilers() {
        try {
            const resp = await fetch('/api/compilers');
            if (!resp.ok) throw new Error(`Failed to load compilers: ${resp.statusText}`);
            const data = await resp.json();

            compilers = Array.isArray(data) ? data : (data.compilers || []);

            // Populate the compiler select
            const select = document.getElementById('compiler-select');
            if (select) {
                let html = '';
                for (const compiler of compilers) {
                    const name = compiler.name || compiler.label || compiler.id || compiler;
                    const value = compiler.id || compiler.value || compiler.name || compiler;
                    const selected = compiler.default ? ' selected' : '';
                    html += `<option value="${escapeHTML(value)}"${selected}>${escapeHTML(name)}</option>`;
                }
                // If no compilers loaded, add a default option
                if (compilers.length === 0) {
                    html = '<option value="auto">Auto-detect</option>';
                }
                select.innerHTML = html;
            }

            return compilers;
        } catch (err) {
            showToast(`Failed to load compilers: ${err.message}`, 'error');
            return [];
        }
    }

    /**
     * Get the currently selected compiler
     * @returns {string} compiler identifier
     */
    function getSelectedCompiler() {
        const select = document.getElementById('compiler-select');
        return select ? select.value : 'auto';
    }

    // ── API: Execute ───────────────────────────────────────────────

    /**
     * Execute a file with the given compiler and arguments
     * @param {string} file_path - path to the file to execute
     * @param {string} [compiler] - compiler to use (defaults to selected)
     * @param {string} [args] - command-line arguments
     * @returns {Promise<object>} execution result
     */
    async function execute(file_path, compiler, args) {
        if (isRunning) {
            showToast('A process is already running', 'warning');
            return { error: 'Process already running' };
        }

        if (!file_path) {
            showToast('No file specified for execution', 'warning');
            return { error: 'No file specified' };
        }

        compiler = compiler || getSelectedCompiler();

        // Ensure panel is visible
        showPanel();

        try {
            appendOutput(`─────────────────────────────────────────`, 'status');
            appendOutput(`$ ${compiler} ${file_path}${args ? ' ' + args : ''}`, 'system');
            appendOutput(`[info] PID: pending... | CWD: workspace | Time: ${new Date().toLocaleString()}`, 'info');

            const body = { file_path, compiler };
            if (args) body.args = args;

            const resp = await fetch('/api/run/execute', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body)
            });

            if (!resp.ok) throw new Error(`Execution failed: ${resp.statusText}`);

            const data = await resp.json();

            currentProcId = data.proc_id || data.process_id || data.id || null;
            pollSince = 0;

            if (currentProcId) {
                appendOutput(`[info] PID: ${currentProcId} | Streaming output...`, 'info');
                setRunningState(true);
                streamOutput(currentProcId);
            } else {
                // No process ID — output may be included directly
                if (data.output) {
                    appendOutput(data.output, 'stdout');
                }
                if (data.stderr) {
                    appendOutput(data.stderr, 'stderr');
                }
                if (data.error) {
                    appendOutput(data.error, 'error');
                }
                if (data.exit_code !== undefined) {
                    const code = data.exit_code;
                    const type = code === 0 ? 'success' : 'error';
                    appendOutput(`[exit] Code: ${code} (${type === 'success' ? 'OK' : 'FAIL'})`, type);
                }
            }

            return data;
        } catch (err) {
            appendOutput(`[error] Execution failed: ${err.message}`, 'error');
            appendOutput(`[info] Check network connection and try again.`, 'info');
            showToast(`Execution error: ${err.message}`, 'error');
            return { error: err.message };
        }
    }

    /**
     * Execute code directly (without saving to a file first)
     * @param {string} code - source code to execute
     * @param {string} [compiler] - compiler/language to use
     * @returns {Promise<object>} execution result
     */
    async function executeCode(code, compiler) {
        if (isRunning) {
            showToast('A process is already running', 'warning');
            return { error: 'Process already running' };
        }

        if (!code || !code.trim()) {
            showToast('No code to execute', 'warning');
            return { error: 'No code provided' };
        }

        compiler = compiler || getSelectedCompiler();

        showPanel();

        try {
            const displayCode = code.length > 120 ? code.substring(0, 120) + '...' : code;
            appendOutput(`$ [${compiler}] ${displayCode}`, 'system');
            appendOutput(`[info] Shell exec | Time: ${new Date().toLocaleString()}`, 'info');

            const body = { code, compiler };

            const resp = await fetch('/api/run/execute', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body)
            });

            if (!resp.ok) throw new Error(`Execution failed: ${resp.statusText}`);

            const data = await resp.json();

            currentProcId = data.proc_id || data.process_id || data.id || null;
            pollSince = 0;

            if (currentProcId) {
                appendOutput(`[info] PID: ${currentProcId} | Streaming output...`, 'info');
                setRunningState(true);
                streamOutput(currentProcId);
            } else {
                if (data.output) appendOutput(data.output, 'stdout');
                if (data.stderr) appendOutput(data.stderr, 'stderr');
                if (data.error) appendOutput(data.error, 'error');
                if (data.exit_code !== undefined) {
                    const code2 = data.exit_code;
                    const type = code2 === 0 ? 'success' : 'error';
                    appendOutput(`[exit] Code: ${code2} (${type === 'success' ? 'OK' : 'FAIL'})`, type);
                }
                // Auto-focus back to input after non-streaming execution
                const si = document.getElementById('shell-input');
                if (si) si.focus();
            }

            return data;
        } catch (err) {
            appendOutput(`[error] Execution failed: ${err.message}`, 'error');
            appendOutput(`[info] Check network connection and try again.`, 'info');
            showToast(`Execution error: ${err.message}`, 'error');
            return { error: err.message };
        }
    }

    // ── API: Stop ──────────────────────────────────────────────────

    /**
     * Stop the currently running process
     * @param {string} [procId] - process ID (defaults to current)
     * @returns {Promise<object>} stop result
     */
    async function stop(procId) {
        procId = procId || currentProcId;

        if (!procId) {
            showToast('No process running', 'warning');
            return { error: 'No process to stop' };
        }

        try {
            appendOutput(`[system] Sending SIGTERM to process ${procId}...`, 'system');

            const resp = await fetch('/api/run/stop', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ proc_id: procId })
            });

            if (!resp.ok) throw new Error(`Stop failed: ${resp.statusText}`);

            const data = await resp.json();
            appendOutput(`[success] Process ${procId} stopped.`, 'success');
            appendOutput(`─────────────────────────────────────────`, 'status');
            cleanupProcess();
            showToast('Process stopped', 'info');

            return data;
        } catch (err) {
            appendOutput(`Stop error: ${err.message}`, 'error');
            showToast(`Stop error: ${err.message}`, 'error');
            return { error: err.message };
        }
    }

    // ── Output Streaming ───────────────────────────────────────────

    /**
     * Stream output using Server-Sent Events (SSE)
     * Falls back to polling if SSE is not available or fails
     * @param {string} procId - process ID to stream output for
     */
    function streamOutput(procId) {
        // Close any existing SSE connection
        closeEventSource();
        stopPolling();

        try {
            const url = `/api/run/output/stream?proc_id=${encodeURIComponent(procId)}`;
            eventSource = new EventSource(url);

            eventSource.onopen = () => {
                // SSE connection established
            };

            eventSource.addEventListener('stdout', (e) => {
                appendOutput(e.data, 'stdout');
            });

            eventSource.addEventListener('stderr', (e) => {
                appendOutput(e.data, 'stderr');
            });

            eventSource.addEventListener('error', (e) => {
                if (e.data) {
                    appendOutput(e.data, 'error');
                }
            });

            eventSource.addEventListener('status', (e) => {
                appendOutput(e.data, 'status');
            });

            eventSource.addEventListener('exit', (e) => {
                const exitCode = e.data;
                const type = parseInt(exitCode, 10) === 0 ? 'success' : 'error';
                const statusText = parseInt(exitCode, 10) === 0 ? 'completed successfully' : 'failed';
                appendOutput(`─────────────────────────────────────────`, 'status');
                appendOutput(`[exit] Process ${statusText} (code: ${exitCode})`, type);
                cleanupProcess();
            });

            eventSource.addEventListener('done', (e) => {
                appendOutput(e.data || 'Done.', 'success');
                appendOutput(`─────────────────────────────────────────`, 'status');
                cleanupProcess();
            });

            eventSource.onerror = () => {
                // SSE failed — fall back to polling
                closeEventSource();
                if (isRunning && currentProcId) {
                    startPolling(currentProcId);
                }
            };

        } catch (err) {
            // EventSource not supported or failed to connect
            closeEventSource();
            if (isRunning && currentProcId) {
                startPolling(currentProcId);
            }
        }
    }

    /**
     * Close the SSE EventSource connection
     */
    function closeEventSource() {
        if (eventSource) {
            eventSource.close();
            eventSource = null;
        }
    }

    // ── Output Polling (Fallback) ──────────────────────────────────

    /**
     * Start polling for output (fallback when SSE is unavailable)
     * @param {string} procId - process ID to poll
     */
    function startPolling(procId) {
        stopPolling();
        pollOutput(procId);
    }

    /**
     * Poll the output endpoint for new lines
     * @param {string} procId - process ID
     */
    async function pollOutput(procId) {
        if (!procId || !isRunning) return;

        try {
            const url = `/api/run/output?proc_id=${encodeURIComponent(procId)}&since=${pollSince}`;
            const resp = await fetch(url);

            if (!resp.ok) {
                // Process may have ended
                if (resp.status === 404 || resp.status === 410) {
                    appendOutput('Process completed', 'info');
                    cleanupProcess();
                    return;
                }
                throw new Error(`Poll failed: ${resp.statusText}`);
            }

            const data = await resp.json();

            // Process output lines
            const lines = data.lines || data.output || [];
            if (Array.isArray(lines)) {
                for (const line of lines) {
                    const text = typeof line === 'string' ? line : (line.text || line.content || '');
                    const rawType = typeof line === 'object' ? (line.type || line.stream || 'stdout') : 'stdout';
                    // Map server types to display types with better colors
                    const type = rawType === 'error' ? 'error' :
                                rawType === 'status' ? 'system' :
                                rawType === 'stderr' ? 'stderr' : 'stdout';
                    appendOutput(text, type);
                    pollSince++;
                }
            } else if (typeof lines === 'string' && lines.trim()) {
                appendOutput(lines, 'stdout');
                pollSince++;
            }

            // Check if process has exited
            if (data.exited || data.done || data.finished) {
                const exitCode = data.exit_code !== undefined ? data.exit_code : 0;
                const type = parseInt(exitCode, 10) === 0 ? 'info' : 'error';
                appendOutput(`Process exited with code ${exitCode}`, type);
                cleanupProcess();
                return;
            }

            // Continue polling if still running
            if (isRunning && currentProcId) {
                pollTimer = setTimeout(() => pollOutput(procId), POLL_INTERVAL);
            }

        } catch (err) {
            // On error, retry a few times then give up
            console.warn('Output poll error:', err.message);
            if (isRunning && currentProcId) {
                pollTimer = setTimeout(() => pollOutput(procId), POLL_INTERVAL * 2);
            }
        }
    }

    /**
     * Stop the polling timer
     */
    function stopPolling() {
        if (pollTimer) {
            clearTimeout(pollTimer);
            pollTimer = null;
        }
    }

    // ── Process State Management ───────────────────────────────────

    /**
     * Set the running state and update UI buttons
     * @param {boolean} running - true if a process is running
     */
    function setRunningState(running) {
        isRunning = running;

        const runBtn = document.getElementById('btn-run');
        const stopBtn = document.getElementById('btn-stop');

        if (runBtn) {
            runBtn.style.display = running ? 'none' : '';
        }
        if (stopBtn) {
            stopBtn.style.display = running ? '' : 'none';
        }
    }

    /**
     * Clean up after a process finishes
     */
    function cleanupProcess() {
        closeEventSource();
        stopPolling();
        currentProcId = null;
        pollSince = 0;
        setRunningState(false);
        // Auto-focus back to input after process completes
        const si = document.getElementById('shell-input');
        if (si) si.focus();
    }

    // ── Keyboard / Viewport Handling (Mobile) ──────────────────

    let keyboardOpen = false;
    let savedPanelHeight = 250;

    /**
     * Initialize visualViewport listener to handle soft keyboard
     * On Android WebView, when the keyboard appears, visualViewport shrinks.
     * We detect this and make the bottom panel float above the keyboard.
     */
    function initKeyboardHandler() {
        if (!window.visualViewport) return;

        const vv = window.visualViewport;

        const onResize = () => {
            const keyboardH = window.innerHeight - vv.height;
            const isKeyboard = keyboardH > 100 && document.activeElement &&
                (document.activeElement.id === 'shell-input' || document.activeElement.closest('#shell-input-bar'));

            const panel = document.getElementById('bottom-panel');
            if (!panel) return;

            if (isKeyboard) {
                keyboardOpen = true;
                // Save current height before keyboard override
                savedPanelHeight = panelHeight;
                // Expand panel to fill most of the visible area
                const targetH = vv.height - 44; // leave toolbar visible
                panel.classList.add('keyboard-open');
                panel.style.height = Math.max(targetH, 200) + 'px';
                // Scroll output to bottom so user sees latest
                const outputEl = document.getElementById('output-content');
                if (outputEl) setTimeout(() => outputEl.scrollTop = outputEl.scrollHeight, 50);
            } else if (keyboardOpen) {
                keyboardOpen = false;
                panel.classList.remove('keyboard-open');
                panel.style.height = savedPanelHeight + 'px';
            }
        };

        vv.addEventListener('resize', onResize);
        vv.addEventListener('scroll', onResize);

        // Also listen for focus/blur on shell-input as a fallback
        const shellInput = document.getElementById('shell-input');
        if (shellInput) {
            shellInput.addEventListener('focus', () => {
                // Delay to let keyboard animation start
                setTimeout(onResize, 100);
                setTimeout(onResize, 300);
            });
            shellInput.addEventListener('blur', () => {
                setTimeout(() => {
                    if (keyboardOpen) {
                        keyboardOpen = false;
                        const panel = document.getElementById('bottom-panel');
                        if (panel) {
                            panel.classList.remove('keyboard-open');
                            panel.style.height = savedPanelHeight + 'px';
                        }
                    }
                }, 100);
            });
        }
    }

    // ── Output Display ─────────────────────────────────────────────

    /**
     * Append a line of text to the output panel
     * @param {string} text - the text to append
     * @param {string} [type='stdout'] - type class: stdout, stderr, error, status, info, success, system
     */
    function appendOutput(text, type) {
        const container = document.getElementById('output-content');
        if (!container) return;

        type = type || 'stdout';

        const line = document.createElement('div');
        line.className = `output-line ${type}`;

        // Add timestamp for important lines (not stdout to avoid clutter)
        if (type !== 'stdout') {
            const ts = document.createElement('span');
            ts.className = 'log-time';
            ts.textContent = timestamp();
            line.appendChild(ts);
        }

        const textSpan = document.createTextNode(text || '');
        line.appendChild(textSpan);
        container.appendChild(line);

        // Trim if too many lines
        trimOutput();

        // Auto-scroll to bottom
        container.scrollTop = container.scrollHeight;
    }

    /**
     * Clear all output from the output panel
     */
    function clearOutput() {
        const container = document.getElementById('output-content');
        if (container) {
            container.innerHTML = '';
        }
        pollSince = 0;
    }

    // ── Panel Management ───────────────────────────────────────────

    /**
     * Show the bottom panel
     */
    function showPanel() {
        const panel = document.getElementById('bottom-panel');
        if (panel) {
            panel.style.display = '';
            panel.classList.add('visible');
            panel.style.height = panelHeight + 'px';
        }
    }

    /**
     * Hide the bottom panel
     */
    function hidePanel() {
        const panel = document.getElementById('bottom-panel');
        if (panel) {
            panel.style.display = 'none';
            panel.classList.remove('visible');
        }
    }

    /**
     * Toggle the bottom panel visibility
     */
    function togglePanel() {
        const panel = document.getElementById('bottom-panel');
        if (!panel) return;

        // Remove 'hidden' class first (CSS has display:none !important)
        panel.classList.remove('hidden');

        if (panel.style.display === 'none' || !panel.classList.contains('visible')) {
            showPanel();
        } else {
            hidePanel();
 }
    }

    /**
     * Set the panel height and persist it
     * @param {number} height - new height in pixels
     */
    function setPanelHeight(height) {
        height = Math.max(MIN_PANEL_HEIGHT, Math.min(MAX_PANEL_HEIGHT, height));
        panelHeight = height;

        const panel = document.getElementById('bottom-panel');
        if (panel) {
            panel.style.height = panelHeight + 'px';
        }
    }

    // ── Resize Handling ────────────────────────────────────────────

    /**
     * Initialize touch/mouse drag resize for the bottom panel
     */
    function initResize() {
        const handle = document.getElementById('bottom-panel-resize');
        if (!handle) return;

        // ── Touch events ──
        handle.addEventListener('touchstart', (e) => {
            e.preventDefault();
            isDragging = true;
            dragStartY = e.touches[0].clientY;
            dragStartHeight = panelHeight;
            handle.classList.add('active');
            document.body.classList.add('panel-resizing');
        }, { passive: false });

        document.addEventListener('touchmove', (e) => {
            if (!isDragging) return;
            e.preventDefault();
            const currentY = e.touches[0].clientY;
            const delta = dragStartY - currentY; // Dragging up increases height
            setPanelHeight(dragStartHeight + delta);
        }, { passive: false });

        document.addEventListener('touchend', () => {
            if (isDragging) {
                isDragging = false;
                const handle = document.getElementById('bottom-panel-resize');
                if (handle) handle.classList.remove('active');
                document.body.classList.remove('panel-resizing');
            }
        });

        // ── Mouse events (desktop) ──
        handle.addEventListener('mousedown', (e) => {
            e.preventDefault();
            isDragging = true;
            dragStartY = e.clientY;
            dragStartHeight = panelHeight;
            handle.classList.add('active');
            document.body.classList.add('panel-resizing');
        });

        document.addEventListener('mousemove', (e) => {
            if (!isDragging) return;
            e.preventDefault();
            const currentY = e.clientY;
            const delta = dragStartY - currentY;
            setPanelHeight(dragStartHeight + delta);
        });

        document.addEventListener('mouseup', () => {
            if (isDragging) {
                isDragging = false;
                const handle = document.getElementById('bottom-panel-resize');
                if (handle) handle.classList.remove('active');
                document.body.classList.remove('panel-resizing');
            }
        });
    }

    // ── Wire Up ────────────────────────────────────────────────────

    function wireEvents() {
        // Expose for DebugManager keyboard-open state
        window.addEventListener('shell:focus', () => {
            const panel = document.getElementById('bottom-panel');
            if (panel) panel.style.height = savedPanelHeight + 'px';
        });

        // Close panel button
        const closeBtn = document.getElementById('bottom-panel-close');
        if (closeBtn) {
            closeBtn.addEventListener('click', (e) => {
                e.preventDefault();
                hidePanel();
            });
        }

        // Clear output button
        const clearBtn = document.getElementById('bottom-panel-clear');
        if (clearBtn) {
            clearBtn.addEventListener('click', (e) => {
                e.preventDefault();
                clearOutput();
            });
        }

        // Run button is handled by AppManager (with file picker), not here.
        // Stop button
        const stopBtn = document.getElementById('btn-stop');
        if (stopBtn) {
            stopBtn.addEventListener('click', (e) => {
                e.preventDefault();
                stop();
            });
            // Initially hidden (no process running)
            stopBtn.style.display = 'none';
        }

        // Initialize resize handle
        initResize();

        // ── Shell Input Bar ──
        initShellInput();

        // ── Terminal Extra Keys ──
        initExtraKeys();
    }

    // ── Shell Input Bar ──────────────────────────────────────────

    let shellHistory = [];
    let shellHistoryIndex = -1;

    function initShellInput() {
        const shellInput = document.getElementById('shell-input');
        if (!shellInput) return;

        function handleShellEnter() {
            const cmd = shellInput.value.trim();
            if (!cmd) return;

            // Debounce: prevent double-fire from keydown+keyup+keypress
            if (handleShellEnter._busy) return;
            handleShellEnter._busy = true;
            setTimeout(() => { handleShellEnter._busy = false; }, 300);

            // Add to history
            shellHistory.push(cmd);
            shellHistoryIndex = shellHistory.length;

            // Show the command in output
            appendOutput(`─────────────────────────────────────────`, 'status');
            appendOutput(`$ ${cmd}`, 'system');
            appendOutput(`[info] Shell command | Time: ${new Date().toLocaleString()}`, 'info');

            // Execute via API
            executeCode(cmd, 'bash');
            shellInput.value = '';
        }

        shellInput.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') {
                e.preventDefault();
                handleShellEnter();
            } else if (e.key === 'ArrowUp') {
                e.preventDefault();
                if (shellHistoryIndex > 0) {
                    shellHistoryIndex--;
                    shellInput.value = shellHistory[shellHistoryIndex] || '';
                }
            } else if (e.key === 'ArrowDown') {
                e.preventDefault();
                if (shellHistoryIndex < shellHistory.length - 1) {
                    shellHistoryIndex++;
                    shellInput.value = shellHistory[shellHistoryIndex] || '';
                } else {
                    shellHistoryIndex = shellHistory.length;
                    shellInput.value = '';
                }
            } else if (e.key === 'Tab') {
                e.preventDefault();
                // Simple tab completion - not full, but inserts a tab character
                const start = shellInput.selectionStart;
                const end = shellInput.selectionEnd;
                shellInput.value = shellInput.value.substring(0, start) + '\t' + shellInput.value.substring(end);
                shellInput.selectionStart = shellInput.selectionEnd = start + 1;
            } else if (e.key === 'c' && e.ctrlKey) {
                e.preventDefault();
                appendOutput('^C', 'status');
                stop();
            } else if (e.key === 'l' && e.ctrlKey) {
                e.preventDefault();
                clearOutput();
            }
        });

        // Fallback: some Android WebViews / soft keyboards fire keypress or keyup
        // instead of keydown for the Enter key. Listen for them too.
        shellInput.addEventListener('keypress', (e) => {
            if (e.key === 'Enter') {
                e.preventDefault();
                handleShellEnter();
            }
        });

        shellInput.addEventListener('keyup', (e) => {
            if (e.key === 'Enter') {
                e.preventDefault();
                handleShellEnter();
            }
        });

        // Fallback: some mobile keyboards submit the parent form instead
        const inputBar = document.getElementById('shell-input-bar');
        if (inputBar) {
            inputBar.addEventListener('submit', (e) => {
                e.preventDefault();
                handleShellEnter();
            });
        }

        // Focus shell input when clicking on output area
        const outputContent = document.getElementById('output-content');
        if (outputContent) {
            outputContent.addEventListener('click', () => {
                shellInput.focus();
            });
        }
    }

    // ── Terminal Extra Keys ──────────────────────────────────────

    let ctrlActive = false;

    function initExtraKeys() {
        const keysBar = document.getElementById('terminal-extra-keys');
        if (!keysBar) return;

        const keyMap = {
            'esc': '\x1b',
            'tab': '\t',
            'up': '\x1b[A',
            'down': '\x1b[B',
            'left': '\x1b[D',
            'right': '\x1b[C',
            'home': '\x1b[H',
            'end': '\x1b[F',
            'pgup': '\x1b[5~',
            'pgdn': '\x1b[6~',
            'pipe': '|',
            'slash': '/',
            'tilde': '~',
            'minus': '-',
            'enter': '\r',
        };

        keysBar.querySelectorAll('.tkey').forEach(btn => {
            btn.addEventListener('click', (e) => {
                e.preventDefault();
                const key = btn.dataset.key;
                const shellInput = document.getElementById('shell-input');
                if (!shellInput) return;

                if (key === 'ctrl') {
                    ctrlActive = !ctrlActive;
                    btn.classList.toggle('active', ctrlActive);
                    return;
                }

                // If CTRL is active and key is a single letter, send Ctrl+key
                if (ctrlActive && key.length === 1 && key >= 'a' && key <= 'z') {
                    // Insert the control character into the shell input
                    const ctrlChar = String.fromCharCode(key.charCodeAt(0) - 96);
                    const pos = shellInput.selectionStart;
                    shellInput.value = shellInput.value.substring(0, pos) + ctrlChar + shellInput.value.substring(shellInput.selectionEnd);
                    shellInput.selectionStart = shellInput.selectionEnd = pos + 1;
                    ctrlActive = false;
                    const ctrlBtn = keysBar.querySelector('[data-key="ctrl"]');
                    if (ctrlBtn) ctrlBtn.classList.remove('active');
                    return;
                }

                // For escape sequences that should trigger shell input behaviors
                if (key === 'up' || key === 'down') {
                    // Simulate arrow key for history navigation
                    const event = new KeyboardEvent('keydown', {
                        key: key === 'up' ? 'ArrowUp' : 'ArrowDown',
                        bubbles: true
                    });
                    shellInput.dispatchEvent(event);
                    return;
                }

                if (key === 'enter') {
                    const event = new KeyboardEvent('keydown', {
                        key: 'Enter',
                        bubbles: true
                    });
                    shellInput.dispatchEvent(event);
                    return;
                }

                // For other keys, insert the character
                const char = keyMap[key];
                if (char) {
                    const pos = shellInput.selectionStart;
                    shellInput.value = shellInput.value.substring(0, pos) + char + shellInput.value.substring(shellInput.selectionEnd);
                    shellInput.selectionStart = shellInput.selectionEnd = pos + char.length;
                    shellInput.focus();
                }
            });
        });
    }

    // ── Initialize ─────────────────────────────────────────────────

    function init() {
        wireEvents();
        loadCompilers();
        loadVenvInfo();
        setRunningState(false);
        initKeyboardHandler();

        // Print startup banner with system info
        printStartupBanner();

        // Auto-detect venv when workspace changes
        window.addEventListener('workspace:changed', () => {
            loadVenvInfo();
            appendOutput('[system] Workspace changed: ' + (window.FileManager ? window.FileManager.workspacePath : 'unknown'), 'system');
        });
    }

    /**
     * Print a startup banner with useful system info
     */
    async function printStartupBanner() {
        const lines = [];
        lines.push('╔══════════════════════════════════════╗');
        lines.push('║         PhoneIDE Terminal v3.0        ║');
        lines.push('╚══════════════════════════════════════╝');
        lines.push('');

        // System info
        const ua = navigator.userAgent;
        const isAndroid = /Android/i.test(ua);
        const isIOS = /iPhone|iPad|iPod/i.test(ua);
        const platform = isAndroid ? 'Android' : (isIOS ? 'iOS' : navigator.platform);
        const screenInfo = `${window.innerWidth}x${window.innerHeight}`;
        const viewportInfo = window.visualViewport ? `${window.visualViewport.width}x${window.visualViewport.height}` : screenInfo;

        lines.push(`[system] Platform: ${platform}`);
        lines.push(`[system] Screen: ${screenInfo} | Viewport: ${viewportInfo}`);
        lines.push(`[system] UA: ${ua.substring(0, 80)}...`);
        lines.push('');

        // Fetch server info
        try {
            const resp = await fetch('/api/health');
            if (resp.ok) {
                const data = await resp.json();
                lines.push(`[system] Server: OK (v${data.version || '?'}) on port ${data.port || '?'}`);
            }
        } catch (e) {
            lines.push(`[system] Server: Connection failed - ${e.message}`);
        }

        // Fetch config info
        try {
            const resp = await fetch('/api/config');
            if (resp.ok) {
                const cfg = await resp.json();
                lines.push(`[system] Workspace: ${cfg.workspace || '?'}`);
                if (cfg.venv_path) {
                    lines.push(`[system] Venv: ${cfg.venv_path}`);
                }
                lines.push(`[system] Compiler: ${cfg.compiler || 'auto'}`);
            }
        } catch (e) {
            lines.push(`[system] Config: unavailable`);
        }

        // Fetch compilers
        try {
            const resp = await fetch('/api/compilers');
            if (resp.ok) {
                const data = await resp.json();
                const comps = data.compilers || [];
                if (comps.length > 0) {
                    lines.push(`[system] Available: ${comps.map(c => c.id).join(', ')}`);
                }
            }
        } catch (e) {}

        lines.push('');
        lines.push('[info] Type commands below and press Enter to execute.');
        lines.push('[info] Use Run button (▶) to execute the current file.');
        lines.push('');

        for (const l of lines) {
            const type = l.startsWith('[system]') ? 'system' :
                         l.startsWith('[info]') ? 'info' :
                         l.startsWith('╔') || l.startsWith('║') || l.startsWith('╚') ? 'status' : 'stdout';
            appendOutput(l, type);
        }
    }

    // Auto-init when DOM is ready
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }

    // ── API: Venv ─────────────────────────────────────────────────

    /**
     * Load venv info from backend and update UI
     */
    async function loadVenvInfo() {
        try {
            const resp = await fetch('/api/venv/list');
            if (!resp.ok) throw new Error(`Failed to load venv: ${resp.statusText}`);
            const data = await resp.json();

            const currentVenvEl = document.getElementById('current-venv');
            if (currentVenvEl) {
                if (data.current) {
                    const name = data.current.split('/').pop();
                    currentVenvEl.textContent = name;
                } else {
                    currentVenvEl.textContent = '未设置';
                }
            }

            // Auto-activate first found venv if none is active
            if (!data.current && data.venvs && data.venvs.length > 0) {
                const firstVenv = data.venvs[0];
                if (firstVenv.path) {
                    try {
                        await fetch('/api/venv/activate', {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ path: firstVenv.path })
                        });
                        if (currentVenvEl) {
                            currentVenvEl.textContent = firstVenv.name;
                        }
                    } catch (_e) {}
                }
            }

            // Show/hide venv packages button
            const venvPackagesDiv = document.getElementById('venv-packages');
            if (venvPackagesDiv && data.current) {
                // Load and show packages
                try {
                    const pkgResp = await fetch('/api/venv/packages');
                    if (pkgResp.ok) {
                        const pkgData = await pkgResp.json();
                        const pkgList = document.getElementById('venv-pkg-list');
                        if (pkgList && pkgData.packages) {
                            pkgList.innerHTML = pkgData.packages.map(p => 
                                `<div style="padding:2px 8px;">${p.name || p.key} ${p.version || ''}</div>`
                            ).join('');
                            venvPackagesDiv.style.display = '';
                        }
                    }
                } catch (_e) {}
            }

            return data;
        } catch (err) {
            console.warn('Failed to load venv info:', err.message);
            return null;
        }
    }

    /**
     * Create a virtual environment
     */
    async function createVenv(path) {
        if (!path) {
            if (window.showPromptDialog) {
                path = await new Promise(resolve => {
                    window.showPromptDialog('创建虚拟环境', '输入路径 (留空使用默认 .venv):', '.venv', resolve);
                });
            } else {
                path = prompt('Enter venv path:', '.venv');
            }
        }
        if (!path) return;

        showPanel();
        appendOutput('$ Creating virtual environment...', 'status');

        try {
            const resp = await fetch('/api/venv/create', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ path })
            });
            if (!resp.ok) throw new Error(`Failed to create venv: ${resp.statusText}`);

            const data = await resp.json();
            if (data.proc_id) {
                currentProcId = data.proc_id;
                pollSince = 0;
                setRunningState(true);
                streamOutput(data.proc_id);
            } else {
                appendOutput('Virtual environment created.', 'info');
                showToast('虚拟环境已创建', 'success');
            }

            // Refresh venv info
            await loadVenvInfo();
            return data;
        } catch (err) {
            appendOutput(`Error: ${err.message}`, 'error');
            showToast(`创建失败: ${err.message}`, 'error');
            return { error: err.message };
        }
    }

    /**
     * Install a Python package
     */
    async function installPackage(packageName) {
        if (!packageName) {
            if (window.showPromptDialog) {
                packageName = await new Promise(resolve => {
                    window.showPromptDialog('安装包', '输入包名:', '', resolve);
                });
            } else {
                packageName = prompt('Enter package name:');
            }
        }
        if (!packageName) return;

        showPanel();
        appendOutput(`$ pip install ${packageName}...`, 'status');

        try {
            const resp = await fetch('/api/run/execute', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    code: `import subprocess; result = subprocess.run(['pip', 'install', '${packageName}'], capture_output=True, text=True); print(result.stdout); print(result.stderr)`,
                    compiler: 'python3'
                })
            });
            if (!resp.ok) throw new Error(`Failed: ${resp.statusText}`);

            const data = await resp.json();
            if (data.proc_id) {
                currentProcId = data.proc_id;
                pollSince = 0;
                setRunningState(true);
                streamOutput(data.proc_id);
            }
            return data;
        } catch (err) {
            appendOutput(`Error: ${err.message}`, 'error');
            showToast(`安装失败: ${err.message}`, 'error');
            return { error: err.message };
        }
    }

    /**
     * Import requirements.txt
     */
    async function importRequirements() {
        showPanel();
        appendOutput('$ pip install -r requirements.txt...', 'status');
        try {
            const resp = await fetch('/api/run/execute', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    code: 'import subprocess; result = subprocess.run(["pip", "install", "-r", "requirements.txt"], capture_output=True, text=True); print(result.stdout); print(result.stderr)',
                    compiler: 'python3'
                })
            });
            if (!resp.ok) throw new Error(`Failed: ${resp.statusText}`);
            const data = await resp.json();
            if (data.proc_id) {
                currentProcId = data.proc_id;
                pollSince = 0;
                setRunningState(true);
                streamOutput(data.proc_id);
            }
        } catch (err) {
            appendOutput(`Error: ${err.message}`, 'error');
            showToast(`安装失败: ${err.message}`, 'error');
        }
    }

    // ── Public API ─────────────────────────────────────────────────
    return {
        execute,
        executeCode,
        stop,
        togglePanel,
        clearOutput,
        streamOutput,
        appendOutput,
        pollOutput,
        loadCompilers,
        getSelectedCompiler,
        showPanel,
        hidePanel,
        loadVenvInfo,
        createVenv,
        installPackage,
        importRequirements,

        // Getters
        get currentProcId() { return currentProcId; },
        get isRunning() { return isRunning; },
        get compilers() { return compilers; },
        get panelHeight() { return panelHeight; },
        set panelHeight(v) { setPanelHeight(v); }
    };
})();

// Also expose as window.TerminalManager for external access
window.TerminalManager = TerminalManager;
