/**
 * EditorManager - CodeMirror 5 editor instance manager for PhoneIDE
 * Provides code editing, syntax highlighting, mode switching, and IDE integration
 */
const EditorManager = (() => {
    'use strict';

    // ── State ──────────────────────────────────────────────────────
    let editor = null;               // CodeMirror instance
    let currentFilePath = null;      // path of the open file
    let currentMode = 'text/plain';  // current language mode
    let dirty = false;               // unsaved changes flag
    let statusBar = null;            // cursor position status bar element
    let _historySize = 0;            // last known history size for dirty detection
    let _switching = false;          // guard: suppress change events during tab switch
    
    // ── Multi-Select State ──────────────────────────────────────────
    let multiSelectMode = false;     // whether multi-select is active
    let multiCursors = [];            // array of cursor positions {line, ch}
    let selectionRanges = [];        // array of selection ranges {anchor, head}

    // ── Selection Mode State ─────────────────────────────────────────
    let selectionMode = false;         // whether selection mode is active
    let selHandleStart = null;         // start cursor handle DOM element (fixed-positioned)
    let selHandleEnd = null;           // end cursor handle DOM element (fixed-positioned)
    let selOverlay = null;             // transparent overlay that captures all touches
    let longPressTimer = null;         // long press detection timer
    let contextMenuEl = null;          // context menu popup element
    let selDragging = null;            // which handle is being dragged: 'start' | 'end' | null
    let selLastCopiedText = '';        // track last auto-copied text to avoid duplicate toasts
    let selAutoScrollRAF = null;       // requestAnimationFrame id for auto-scroll
    let selContextMenuEl = null;       // selection context menu (copy/cut/paste)

    // ── Tab State ─────────────────────────────────────────────────
    let tabs = {};                   // path -> { name, content, mode, cursor, scroll, history }
    let tabOrder = [];               // ordered array of open tab paths
    let activeTab = null;            // path of the currently active tab
    const tabContainer = null;       // will resolve on init

    // ── Config ─────────────────────────────────────────────────────
    const config = {
        fontSize: 12,
        tabSize: 4,
        indentUnit: 4,
        indentWithTabs: false,
        lineWrapping: false,
        theme: 'dracula',
        // Multi-Select config
        multiSelect: {
            enabled: true,
            modifierKey: 'Alt',           // 'Alt' for desktop, 'Ctrl' for mobile
            rectangular: true,            // enable rectangular selection
            maxCursors: 50                // maximum number of cursors
        }
    };

    // ── Language Mode Mapping ──────────────────────────────────────

    /**
     * Map of file extensions to CodeMirror MIME types / mode names
     */
    const extensionModeMap = {
        // Python
        'py': 'python',
        'pyw': 'python',

        // JavaScript / TypeScript
        'js': 'javascript',
        'jsx': 'javascript',
        'mjs': 'javascript',
        'cjs': 'javascript',
        'ts': { name: 'javascript', typescript: true },
        'tsx': { name: 'javascript', typescript: true, jsx: true },

        // HTML
        'html': 'htmlmixed',
        'htm': 'htmlmixed',
        'xhtml': 'htmlmixed',
        'svg': 'htmlmixed',

        // CSS
        'css': 'css',
        'scss': 'css',
        'sass': 'css',
        'less': 'css',

        // JSON
        'json': { name: 'javascript', json: true },
        'jsonc': { name: 'javascript', json: true },
        'json5': { name: 'javascript', json: true },

        // Markdown
        'md': 'markdown',
        'markdown': 'markdown',
        'mdx': 'markdown',

        // Shell
        'sh': 'shell',
        'bash': 'shell',
        'zsh': 'shell',
        'fish': 'shell',

        // C / C++
        'c': 'text/x-csrc',
        'h': 'text/x-csrc',
        'cpp': 'text/x-c++src',
        'cc': 'text/x-c++src',
        'cxx': 'text/x-c++src',
        'hpp': 'text/x-c++src',
        'hh': 'text/x-c++src',
        'hxx': 'text/x-c++src',

        // Java
        'java': 'text/x-java',

        // Go
        'go': 'go',

        // Rust
        'rs': 'rust',

        // SQL
        'sql': 'sql',

        // XML
        'xml': 'xml',
        'xsl': 'xml',
        'xslt': 'xml',
        'xsd': 'xml',
        'kml': 'xml',
        'svg': 'xml'
    };

    /**
     * Detect the CodeMirror mode from a file extension
     * @param {string} filename - file name or path
     * @returns {string|object} CodeMirror mode specification
     */
    function getModeForFilename(filename) {
        if (!filename) return 'text/plain';

        // Handle "shell" as a special filename
        const lower = filename.toLowerCase();

        // Extract the extension
        const dotIdx = lower.lastIndexOf('.');
        if (dotIdx < 0) return 'text/plain';

        const ext = lower.substring(dotIdx + 1);
        return extensionModeMap[ext] || 'text/plain';
    }

    // ── Initialization ─────────────────────────────────────────────

    /**
     * Initialize the CodeMirror editor instance on #code-editor
     */
    function init() {
        if (typeof CodeMirror === 'undefined') {
            console.error('CodeMirror is not loaded. Make sure the CDN script is included.');
            return;
        }

        const textarea = document.getElementById('code-editor');
        if (!textarea) {
            console.error('Textarea #code-editor not found in the DOM.');
            return;
        }

        editor = CodeMirror.fromTextArea(textarea, {
            // Appearance
            theme: config.theme,
            lineNumbers: true,
            lineWrapping: config.lineWrapping,
            viewportMargin: Infinity,        // render full doc for mobile perf

            // Mobile-friendly input — textarea mode for search dialog compatibility
            inputStyle: 'textarea',

            // Indentation
            tabSize: config.tabSize,
            indentUnit: config.indentUnit,
            indentWithTabs: config.indentWithTabs,

            // Editing features
            matchBrackets: true,
            autoCloseBrackets: true,
            styleActiveLine: true,
            foldGutter: true,

            // Multi-Select support
            cursorBlinkRate: 530,

            // Gutters: breakpoints + line numbers + code folding
            gutters: ['breakpoints', 'CodeMirror-linenumbers', 'CodeMirror-foldgutter'],

            // Placeholder for empty editor
            placeholder: '// Start coding...',

            // Mode (default plain text)
            mode: 'text/plain',

            // Font size
            extraKeys: {
                'Tab': (cm) => {
                    // Indent with spaces if selection, else insert tab-width spaces
                    if (cm.somethingSelected()) {
                        cm.indentSelection('add');
                    } else {
                        cm.replaceSelection(
                            Array(cm.getOption('indentUnit') + 1).join(' '),
                            'end'
                        );
                    }
                },
                'Shift-Tab': (cm) => {
                    cm.indentSelection('subtract');
                },
                'Ctrl-S': () => {
                    if (window.FileManager && typeof window.FileManager.saveFile === 'function') {
                        window.FileManager.saveFile();
                    }
                    return false;
                },
                'Cmd-S': () => {
                    if (window.FileManager && typeof window.FileManager.saveFile === 'function') {
                        window.FileManager.saveFile();
                    }
                    return false;
                },
                'Ctrl-Shift-R': () => {
                    if (window.TerminalManager && typeof window.TerminalManager.execute === 'function') {
                        const filePath = window.FileManager ? window.FileManager.currentFilePath : null;
                        window.TerminalManager.execute(filePath);
                    }
                    return false;
                },
                'F5': () => {
                    if (window.TerminalManager && typeof window.TerminalManager.execute === 'function') {
                        const filePath = window.FileManager ? window.FileManager.currentFilePath : null;
                        window.TerminalManager.execute(filePath);
                    }
                    return false;
                },
                'Ctrl-/': (cm) => {
                    cm.toggleComment();
                },
                'Cmd-/': (cm) => {
                    cm.toggleComment();
                },
                // Multi-Select key bindings
                'Alt-Click': (cm, event) => {
                    if (config.multiSelect.enabled) {
                        event.preventDefault();
                        handleMultiSelectClick(event);
                    }
                },
                'Ctrl-Click': (cm, event) => {
                    if (config.multiSelect.enabled && isMobile()) {
                        event.preventDefault();
                        handleMultiSelectClick(event);
                    }
                },
                'Alt-A': (cm) => {
                    if (config.multiSelect.enabled) {
                        selectAllOccurrences();
                    }
                },
                'Escape': (cm) => {
                    if (multiSelectMode) {
                        exitMultiSelect();
                    }
                },
                'Shift-Alt-Up': (cm) => {
                    if (config.multiSelect.enabled && multiSelectMode) {
                        addCursorAbove();
                    }
                },
                'Shift-Alt-Down': (cm) => {
                    if (config.multiSelect.enabled && multiSelectMode) {
                        addCursorBelow();
                    }
                }
            }
        });

        // Apply initial font size
        applyFontSize(config.fontSize);

        // Set breakpoint gutter width
        if (editor) {
            const gutters = editor.getWrapperElement().querySelectorAll('.CodeMirror-gutter');
            if (gutters.length > 0) {
                gutters[0].style.width = '18px';
                gutters[0].style.minWidth = '18px';
            }
        }

        // Create status bar
        createStatusBar();

        // ── Event Listeners ────────────────────────────────────────

        // Track cursor position
        editor.on('cursorActivity', () => {
            updateCursorPos();
        });

        // Track changes for dirty state
        editor.on('change', () => {
            // Suppress events during programmatic content loads (tab switch)
            if (_switching) return;
            if (!dirty) {
                markDirty();
            }
            // Dispatch custom event for auto-save
            document.dispatchEvent(new CustomEvent('editor:change'));
            // Live markdown preview update (re-render + sync scroll to cursor)
            if (mdPreviewMode && isMarkdownFile()) {
                clearTimeout(window._mdPreviewTimer);
                window._mdPreviewTimer = setTimeout(function() {
                    renderMarkdownPreview();
                    scrollPreviewToCursor();
                }, 300);
            }
        });

        // Track history for clean detection (CodeMirror clearHistory)
        editor.on('historyDone', () => {
            _historySize = editor.historySize().done;
        });

        // Initial history snapshot
        _historySize = editor.historySize().done;

        // Window resize
        window.addEventListener('resize', debounce(() => {
            resize();
        }, 150));

        // Goto line button
        const gotoLineBtn = document.getElementById('editor-goto-line-btn');
        if (gotoLineBtn) {
            gotoLineBtn.addEventListener('click', () => {
                if (window.showPromptDialog) {
                    window.showPromptDialog('跳转到行', '输入行号:', '', (val) => {
                        if (val) goToLine(parseInt(val));
                    });
                } else {
                    const line = prompt('Go to line:');
                    if (line) goToLine(parseInt(line));
                }
            });
        }

        // Browser preview button (for HTML/HTM/MD files)
        const previewBtn = document.getElementById('editor-preview-btn');
        if (previewBtn) {
            previewBtn.addEventListener('click', previewInBrowser);
        }

        // ── Selection Mode: Exit button ────────────────────────────
        const exitSelBtn = document.getElementById('editor-exit-selection-btn');
        if (exitSelBtn) {
            exitSelBtn.addEventListener('click', () => {
                exitSelectionMode();
            });
        }

        // ── Selection Mode: Long press detection ───────────────────
        setupSelectionModeListeners();

        // ── Breakpoint Gutter Click ──────────────────────────────
        editor.on('gutterClick', (cm, n, gutterId) => {
            const filePath = currentFilePath;
            if (!filePath) return;
            // Only toggle breakpoint when clicking the breakpoints gutter
            if (gutterId === 'breakpoints') {
                const line = n + 1;
                if (window.DebuggerUI && DebuggerUI.toggleBreakpoint) {
                    DebuggerUI.toggleBreakpoint(filePath, line);
                }
            }
        });

        console.log('EditorManager initialized');
    }

    // ── Status Bar ─────────────────────────────────────────────────

    /**
     * Create the cursor-position status bar beneath the editor
     */
    function createStatusBar() {
        const wrapper = document.querySelector('.CodeMirror');
        if (!wrapper) return;

        statusBar = document.createElement('div');
        statusBar.className = 'editor-status-bar';
        statusBar.innerHTML = '<span class="status-pos">Ln 1, Col 1</span>'
                            + '<span class="status-sep"> | </span>'
                            + '<span class="status-lines">Lines: 1</span>'
                            + '<span class="status-sep"> | </span>'
                            + '<span class="status-mode">Plain Text</span>';

        wrapper.appendChild(statusBar);
    }

    /**
     * Update the cursor position display in the status bar
     */
    function updateCursorPos() {
        if (!editor || !statusBar) return;

        const cursor = editor.getCursor();
        const line = cursor.line + 1;
        const col = cursor.ch + 1;
        const totalLines = editor.lineCount();

        const posEl = statusBar.querySelector('.status-pos');
        const linesEl = statusBar.querySelector('.status-lines');
        const modeEl = statusBar.querySelector('.status-mode');

        if (posEl) posEl.textContent = `Ln ${line}, Col ${col}`;
        if (linesEl) linesEl.textContent = `Lines: ${totalLines}`;
        if (modeEl) modeEl.textContent = getModeLabel(currentMode);
    }

    /**
     * Get a human-readable label for the current mode
     * @param {string|object} mode
     * @returns {string}
     */
    function getModeLabel(mode) {
        if (typeof mode === 'object') {
            if (mode.json) return 'JSON';
            if (mode.typescript) return mode.jsx ? 'TSX' : 'TypeScript';
            return 'JavaScript';
        }
        const labels = {
            'python': 'Python',
            'javascript': 'JavaScript',
            'htmlmixed': 'HTML',
            'css': 'CSS',
            'markdown': 'Markdown',
            'shell': 'Shell',
            'text/x-csrc': 'C',
            'text/x-c++src': 'C++',
            'text/x-java': 'Java',
            'go': 'Go',
            'rust': 'Rust',
            'sql': 'SQL',
            'xml': 'XML',
            'text/plain': 'Plain Text'
        };
        return labels[mode] || 'Plain Text';
    }

    // ── Tab Management ─────────────────────────────────────────────

    /**
     * Get the DOM element for the tab bar
     */
    function getTabBar() {
        return document.getElementById('editor-tabs');
    }

    /**
     * Save current editor state into the active tab
     */
    function saveCurrentTabState() {
        if (!editor || !activeTab) return;
        const tab = tabs[activeTab];
        if (!tab) return;

        tab.content = editor.getValue();
        tab.mode = currentMode;
        tab.cursor = editor.getCursor();
        tab.scroll = editor.getScrollInfo();
        tab.history = editor.getHistory ? editor.getHistory() : null;
        tab.dirty = dirty;
    }

    /**
     * Render the tab bar UI
     */
    function renderTabs() {
        const bar = getTabBar();
        if (!bar) return;

        bar.innerHTML = '';

        // Hide tab bar when no tabs are open
        if (tabOrder.length === 0) {
            bar.style.display = 'none';
            document.getElementById('main-area').style.top = 'var(--toolbar-height)';
            updateFileName();
            if (editor) editor.refresh();
            return;
        }

        bar.style.display = '';
        document.getElementById('main-area').style.top = 'calc(var(--toolbar-height) + 34px)';

        for (const path of tabOrder) {
            const tab = tabs[path];
            if (!tab) continue;

            const btn = document.createElement('button');
            btn.className = 'editor-tab' + (path === activeTab ? ' active' : '');
            btn.dataset.path = path;

            const nameSpan = document.createElement('span');
            nameSpan.className = 'tab-name';
            nameSpan.textContent = tab.name;
            nameSpan.title = path;

            // Modified indicator
            if (tab.dirty) {
                const dot = document.createElement('span');
                dot.className = 'tab-modified';
                btn.appendChild(dot);
            }

            btn.appendChild(nameSpan);

            // Close button
            const closeBtn = document.createElement('span');
            closeBtn.className = 'tab-close';
            closeBtn.textContent = '×';
            closeBtn.addEventListener('click', (e) => {
                e.stopPropagation();
                closeTab(path);
            });
            btn.appendChild(closeBtn);

            // Click to switch tab
            btn.addEventListener('click', () => {
                if (path !== activeTab) {
                    switchTab(path);
                }
            });

            bar.appendChild(btn);
        }

        // Update toolbar file name
        updateFileName();
    }

    /**
     * Update the #file-name span in the toolbar
     */
    function updateFileName() {
        const el = document.getElementById('file-name');
        if (!el) return;
        if (activeTab && tabs[activeTab]) {
            el.textContent = tabs[activeTab].name;
        } else {
            el.textContent = '未打开文件';
        }
    }

    /**
     * Open a new tab or switch to existing tab
     * @param {string} path - file path
     * @param {string} content - file content
     * @param {object} [modeOrPath] - optional mode override, or path to detect from
     */
    function openTab(path, content, modeOrPath) {
        if (!path) return;

        const name = path.split('/').pop();

        // If tab already exists, switch to it
        if (tabs[path]) {
            // Update content if provided (file was reloaded from disk)
            if (content !== undefined) {
                tabs[path].content = content;
                if (path === activeTab) {
                    // Tab is active, update editor content directly
                    if (editor) {
                        _switching = true;
                        editor.setValue(content);
                        _switching = false;
                        editor.clearHistory();
                        _historySize = 0;
                        markClean();
                    }
                }
            }
            switchTab(path);
            return;
        }

        // Save current tab state before opening new one
        saveCurrentTabState();

        // Auto-save current dirty file to disk before opening new tab
        if (dirty && activeTab) {
            const savePath = activeTab;
            const saveContent = editor ? editor.getValue() : '';
            fetch('/api/files/save', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    path: savePath.replace(/^\/workspace\/?/, ''),
                    content: saveContent
                })
            }).then(resp => {
                if (resp.ok && tabs[savePath]) {
                    tabs[savePath].dirty = false;
                    renderTabs();
                    if (window.GitManager && typeof window.GitManager.refreshStatus === 'function') {
                        window.GitManager.refreshStatus().catch(() => {});
                    }
                }
            }).catch(() => {});
        }

        // Determine mode
        let mode = currentMode;
        if (modeOrPath) {
            if (typeof modeOrPath === 'string' && (modeOrPath.includes('/') || modeOrPath.includes('.'))) {
                mode = getModeForFilename(modeOrPath.split('/').pop());
            } else {
                mode = modeOrPath;
            }
        } else {
            mode = getModeForFilename(name);
        }

        // Create tab state
        tabs[path] = {
            name: name,
            content: (content !== undefined && content !== null) ? String(content) : '',
            mode: mode,
            cursor: { line: 0, ch: 0 },
            scroll: { left: 0, top: 0 },
            history: null,
            dirty: false
        };

        // Add to tab order (if switching from another tab, place after it)
        if (activeTab && tabOrder.indexOf(activeTab) >= 0) {
            const idx = tabOrder.indexOf(activeTab);
            tabOrder.splice(idx + 1, 0, path);
        } else {
            tabOrder.push(path);
        }

        // Load content into editor
        currentFilePath = path;
        currentMode = mode;
        activeTab = path;

        if (editor) {
            _switching = true;
            editor.setValue(tabs[path].content);
            _switching = false;
            editor.clearHistory();
            _historySize = 0;
            setMode(mode);
            markClean();
            updateCursorPos();
            updateMarkdownButton();
            editor.focus();

            if (mdPreviewMode && isMarkdownFile()) {
                renderMarkdownPreview();
                scrollPreviewToCursor();
            }
        }

        renderTabs();
    }

    /**
     * Switch to an existing tab
     * @param {string} path - file path of the tab to switch to
     */
    function switchTab(path) {
        if (!path || !tabs[path] || path === activeTab) return;

        // Save current tab state (captures content for tab restoration)
        saveCurrentTabState();

        // Auto-save current dirty file to disk before switching (fire-and-forget)
        // Must capture filePath and content HERE before we change activeTab/currentFilePath
        if (dirty && activeTab) {
            const savePath = activeTab;
            const saveContent = editor ? editor.getValue() : '';
            // Fire async save — don't await, don't affect tab switching
            fetch('/api/files/save', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    path: savePath.replace(/^\/workspace\/?/, ''),
                    content: saveContent
                })
            }).then(resp => {
                if (resp.ok) {
                    // Mark tab as clean after successful disk save
                    if (tabs[savePath]) {
                        tabs[savePath].dirty = false;
                        // Update tab UI if still visible
                        if (savePath === activeTab && window.EditorManager) {
                            window.EditorManager.markClean();
                        }
                        renderTabs();
                    }
                    // Refresh git status
                    if (window.GitManager && typeof window.GitManager.refreshStatus === 'function') {
                        window.GitManager.refreshStatus().catch(() => {});
                    }
                }
            }).catch(() => {});
        }

        // Load new tab state
        const tab = tabs[path];
        activeTab = path;
        currentFilePath = path;
        currentMode = tab.mode;

        if (editor) {
            _switching = true;
            editor.setValue(tab.content);
            _switching = false;
            if (tab.history) {
                editor.setHistory(tab.history);
            } else {
                editor.clearHistory();
                _historySize = 0;
            }
            setMode(tab.mode);
            editor.setCursor(tab.cursor);
            editor.scrollTo(tab.scroll.left, tab.scroll.top);
            dirty = !!tab.dirty;
            updateTitle();
            updateCursorPos();
            updateMarkdownButton();
            editor.focus();

            if (mdPreviewMode && isMarkdownFile()) {
                renderMarkdownPreview();
                scrollPreviewToCursor();
            }
        }

        renderTabs();
    }

    /**
     * Close a tab
     * @param {string} path - file path of the tab to close
     */
    function closeTab(path) {
        if (!path || !tabs[path]) return;

        // Remove from state
        delete tabs[path];
        const idx = tabOrder.indexOf(path);
        if (idx >= 0) tabOrder.splice(idx, 1);

        // If it was the active tab, switch to adjacent
        if (path === activeTab) {
            activeTab = null;
            currentFilePath = null;

            // Find adjacent tab
            let nextPath = null;
            if (tabOrder.length > 0) {
                // Try tab at same index, or previous
                nextPath = tabOrder[Math.min(idx, tabOrder.length - 1)];
            }

            if (nextPath) {
                switchTab(nextPath);
            } else {
                // No more tabs - show empty editor
                if (editor) {
                    _switching = true;
                    editor.setValue('');
                    _switching = false;
                    editor.clearHistory();
                    _historySize = 0;
                    currentMode = 'text/plain';
                    setMode('text/plain');
                    markClean();
                    updateMarkdownButton();
                }
                renderTabs();
            }
        } else {
            renderTabs();
        }
    }

    /**
     * Get list of all open tab paths
     * @returns {string[]}
     */
    function getTabList() {
        return [...tabOrder];
    }

    /**
     * Check if a tab is open
     * @param {string} path
     * @returns {boolean}
     */
    function hasTab(path) {
        return !!tabs[path];
    }

    /**
     * Get the active tab path
     * @returns {string|null}
     */
    function getActiveTab() {
        return activeTab;
    }

    /**
     * Update the dirty state of a specific tab
     * @param {string} path
     * @param {boolean} isDirty
     */
    function setTabDirty(path, isDirty) {
        if (tabs[path]) {
            tabs[path].dirty = isDirty;
            renderTabs();
        }
    }

    // ── Content Management ─────────────────────────────────────────

    /**
     * Set editor content and optionally switch language mode
     * @param {string} content - the text to set
     * @param {string} [modeOrPath] - CodeMirror mode string, or a file path to detect mode from
     */
    function setContent(content, modeOrPath) {
        if (!editor) return;

        const value = (content !== undefined && content !== null) ? String(content) : '';

        // Determine if modeOrPath is a file path or a mode string
        if (modeOrPath) {
            if (modeOrPath.includes('/') || modeOrPath.includes('.')) {
                // Looks like a file path — detect mode from it
                currentFilePath = modeOrPath;
                const mode = getModeForFilename(modeOrPath.split('/').pop());
                setMode(mode);
            } else {
                // Treat as mode
                setMode(modeOrPath);
            }
        }

        // Preserve scroll position where possible
        const scrollInfo = editor.getScrollInfo();

        _switching = true;
        editor.setValue(value);
        _switching = false;
        editor.clearHistory();
        _historySize = 0;
        markClean();
        editor.scrollTo(scrollInfo.left, scrollInfo.top);

        updateCursorPos();
        updateMarkdownButton();

        // Re-render markdown preview if active
        if (mdPreviewMode && isMarkdownFile()) {
            renderMarkdownPreview();
            scrollPreviewToCursor();
        }
    }

    /**
     * Get the current editor content
     * @returns {string}
     */
    function getContent() {
        if (!editor) return '';
        return editor.getValue();
    }

    // ── Mode Management ────────────────────────────────────────────

    /**
     * Switch the editor's language mode
     * @param {string|object} mode - CodeMirror mode specification
     */
    function setMode(mode) {
        if (!editor) return;

        currentMode = mode || 'text/plain';
        editor.setOption('mode', currentMode);
        updateCursorPos();
    }

    /**
     * Get the current mode
     * @returns {string|object}
     */
    function getMode() {
        return currentMode;
    }

    // ── File Tracking ──────────────────────────────────────────────

    /**
     * Get the current file path
     * @returns {string|null}
     */
    function getCurrentFile() {
        return currentFilePath;
    }

    /**
     * Set the current file path
     * @param {string} path
     */
    function setCurrentFile(path) {
        currentFilePath = path;
    }

    /**
     * Detect language from a filename and set the editor mode
     * @param {string} filename - file name or path
     */
    function setLanguageForFile(filename) {
        const mode = getModeForFilename(filename);
        setMode(mode);
    }

    // ── Dirty State ────────────────────────────────────────────────

    /**
     * Mark the editor as clean (no unsaved changes)
     */
    function markClean() {
        dirty = false;
        updateTitle();
        if (activeTab && tabs[activeTab]) {
            tabs[activeTab].dirty = false;
            renderTabs();
        }
    }

    /**
     * Mark the editor as dirty (unsaved changes present)
     */
    function markDirty() {
        dirty = true;
        updateTitle();
        if (activeTab && tabs[activeTab]) {
            tabs[activeTab].dirty = true;
            renderTabs();
        }
    }

    /**
     * Check if the editor has unsaved changes
     * @returns {boolean}
     */
    function isDirty() {
        return dirty;
    }

    /**
     * Update the page title to reflect dirty state
     */
    function updateTitle() {
        const filename = currentFilePath ? currentFilePath.split('/').pop() : 'untitled';
        const indicator = dirty ? ' ● ' : ' ';
        document.title = `${indicator}${filename} - PhoneIDE`;
    }

    // ── Focus ──────────────────────────────────────────────────────

    /**
     * Focus the editor
     */
    function focus() {
        if (editor) {
            editor.focus();
        }
    }

    // ── Search & Replace (Custom Mobile-Friendly) ───────────────

    let searchState = {
        query: '',
        caseSensitive: false,
        regex: false,
        cursor: null,
        matches: 0,
        currentMatch: 0,
        overlay: null,
        isVisible: false,
    };

    /**
     * Toggle the inline search bar
     * @param {string} [query] - initial search query
     */
    function search(query) {
        if (!editor) return;

        const searchInput = document.getElementById('editor-search');
        const replaceInput = document.getElementById('editor-replace');

        if (!searchInput) {
            // Fallback to CodeMirror built-in search dialog
            if (typeof editor.execCommand === 'function') {
                editor.execCommand('find');
            }
            return;
        }

        // Toggle search bar visibility
        if (searchState.isVisible && !query) {
            closeSearchBar();
            return;
        }

        searchInput.style.display = '';
        searchState.isVisible = true;

        if (query) {
            searchInput.value = query;
            doSearch(query);
        } else {
            searchInput.focus();
            // Select all text for easy replacement
            searchInput.select();
        }
    }

    /**
     * Close the search bar and clear highlights
     */
    function closeSearchBar() {
        const searchInput = document.getElementById('editor-search');
        const replaceInput = document.getElementById('editor-replace');

        if (searchInput) {
            searchInput.style.display = 'none';
            searchInput.value = '';
        }
        if (replaceInput) {
            replaceInput.style.display = 'none';
            replaceInput.value = '';
        }

        // Clear search highlights
        if (editor && searchState.overlay) {
            editor.removeOverlay(searchState.overlay);
            searchState.overlay = null;
        }
        searchState.query = '';
        searchState.cursor = null;
        searchState.matches = 0;
        searchState.currentMatch = 0;
        searchState.isVisible = false;

        // Dispatch for external UI (restore search icon)
        document.dispatchEvent(new CustomEvent('editor:searchClose'));

        if (editor) editor.focus();
    }

    /**
     * Perform a search and highlight all matches
     */
    function doSearch(query) {
        if (!editor || !query) return;

        // Clear previous overlay
        if (searchState.overlay) {
            editor.removeOverlay(searchState.overlay);
            searchState.overlay = null;
        }

        searchState.query = query;

        // Build regex for highlighting
        let flags = searchState.caseSensitive ? 'g' : 'gi';
        let pattern;
        try {
            if (searchState.regex) {
                pattern = new RegExp(query, flags);
            } else {
                const escaped = query.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
                pattern = new RegExp(escaped, flags);
            }
        } catch (e) {
            return;
        }

        // Count matches
        const content = editor.getValue();
        const allMatches = content.match(pattern);
        searchState.matches = allMatches ? allMatches.length : 0;
        searchState.currentMatch = 0;

        // Add highlight overlay
        if (searchState.matches > 0) {
            searchState.overlay = {
                token: function(stream) {
                    pattern.lastIndex = stream.pos;
                    const match = pattern.exec(stream.string);
                    if (match && match.index === stream.pos) {
                        stream.pos += match[0].length;
                        return 'searching match'; // 'searching' class + 'match' class
                    } else if (match) {
                        stream.pos = match.index;
                    } else {
                        stream.skipToEnd();
                    }
                }
            };
            editor.addOverlay(searchState.overlay);
        }

        // Jump to first match
        findNext();

        // Dispatch event for external UI (app.js toolbar buttons)
        document.dispatchEvent(new CustomEvent('editor:search', {
            detail: { query, matches: searchState.matches, currentMatch: searchState.currentMatch }
        }));

        // Update search input placeholder with count
        const searchInput = document.getElementById('editor-search');
        if (searchInput) {
            searchInput.placeholder = `${searchState.matches > 0 ? searchState.currentMatch + '/' + searchState.matches : '无匹配'} | ${query}`;
        }
    }

    /**
     * Find the next match and jump to it
     */
    function findNext() {
        if (!editor || !searchState.query) return;

        const cmCursor = editor.getSearchCursor(
            searchState.regex ? new RegExp(searchState.query, searchState.caseSensitive ? '' : 'i') : searchState.query,
            editor.getCursor('to'),
            { caseFold: !searchState.caseSensitive }
        );

        if (cmCursor.findNext()) {
            editor.setSelection(cmCursor.from(), cmCursor.to());
            editor.scrollIntoView({ from: cmCursor.from(), to: cmCursor.to() }, 50);
            searchState.currentMatch++;
        } else {
            // Wrap around to beginning
            const wrapCursor = editor.getSearchCursor(
                searchState.regex ? new RegExp(searchState.query, searchState.caseSensitive ? '' : 'i') : searchState.query,
                { line: 0, ch: 0 },
                { caseFold: !searchState.caseSensitive }
            );
            if (wrapCursor.findNext()) {
                editor.setSelection(wrapCursor.from(), wrapCursor.to());
                editor.scrollIntoView({ from: wrapCursor.from(), to: wrapCursor.to() }, 50);
                searchState.currentMatch = 1;
            }
        }

        // Update placeholder
        const searchInput = document.getElementById('editor-search');
        if (searchInput) {
            searchInput.placeholder = `${searchState.matches > 0 ? searchState.currentMatch + '/' + searchState.matches : '无匹配'} | ${searchState.query}`;
        }

        // Dispatch for external count display
        document.dispatchEvent(new CustomEvent('editor:search', {
            detail: { query: searchState.query, matches: searchState.matches, currentMatch: searchState.currentMatch }
        }));
    }

    /**
     * Find the previous match and jump to it
     */
    function findPrev() {
        if (!editor || !searchState.query) return;

        const cmCursor = editor.getSearchCursor(
            searchState.regex ? new RegExp(searchState.query, searchState.caseSensitive ? '' : 'i') : searchState.query,
            editor.getCursor('from'),
            { caseFold: !searchState.caseSensitive }
        );

        if (cmCursor.findPrevious()) {
            editor.setSelection(cmCursor.from(), cmCursor.to());
            editor.scrollIntoView({ from: cmCursor.from(), to: cmCursor.to() }, 50);
            if (searchState.currentMatch > 1) searchState.currentMatch--;
        } else {
            // Wrap around to end
            const wrapCursor = editor.getSearchCursor(
                searchState.regex ? new RegExp(searchState.query, searchState.caseSensitive ? '' : 'i') : searchState.query,
                { line: editor.lastLine(), ch: editor.getLine(editor.lastLine()).length },
                { caseFold: !searchState.caseSensitive }
            );
            if (wrapCursor.findPrevious()) {
                editor.setSelection(wrapCursor.from(), wrapCursor.to());
                editor.scrollIntoView({ from: wrapCursor.from(), to: wrapCursor.to() }, 50);
                searchState.currentMatch = searchState.matches;
            }
        }

        // Update placeholder
        const searchInput = document.getElementById('editor-search');
        if (searchInput) {
            searchInput.placeholder = `${searchState.matches > 0 ? searchState.currentMatch + '/' + searchState.matches : '无匹配'} | ${searchState.query}`;
        }

        // Dispatch for external count display
        document.dispatchEvent(new CustomEvent('editor:search', {
            detail: { query: searchState.query, matches: searchState.matches, currentMatch: searchState.currentMatch }
        }));
    }

    /**
     * Get current search state info (for external UI updates)
     * @returns {{query: string, matches: number, currentMatch: number}}
     */
    function getSearchInfo() {
        return {
            query: searchState.query,
            matches: searchState.matches,
            currentMatch: searchState.currentMatch,
        };
    }

    /**
     * Replace current match and advance to next
     */
    function replaceCurrent(replaceText) {
        if (!editor || !searchState.query) return;

        const sel = editor.getSelection();
        if (sel && sel.length > 0) {
            editor.replaceSelection(replaceText);
            searchState.matches--;
            findNext();
        }
    }

    /**
     * Replace all matches
     */
    function replaceAll(replaceText) {
        if (!editor || !searchState.query) return;

        const cmCursor = editor.getSearchCursor(
            searchState.regex ? new RegExp(searchState.query, searchState.caseSensitive ? 'g' : 'gi') : searchState.query,
            { line: 0, ch: 0 },
            { caseFold: !searchState.caseSensitive }
        );

        let count = 0;
        editor.operation(function() {
            while (cmCursor.findNext()) {
                cmCursor.replace(replaceText);
                count++;
            }
        });

        // Re-run search to update highlights
        if (searchState.query) {
            doSearch(searchState.query);
        }

        return count;
    }

    // ── Navigation ─────────────────────────────────────────────────

    /**
     * Jump the cursor to a specific line and column
     * @param {number} line - 1-based line number
     * @param {number} [col=1] - 1-based column number
     */
    function goToLine(line, col) {
        if (!editor) return;

        line = parseInt(line, 10) || 1;
        col = parseInt(col, 10) || 1;

        // Convert to 0-based
        const targetLine = Math.max(0, Math.min(line - 1, editor.lineCount() - 1));
        const targetCol = Math.max(0, col - 1);

        editor.setCursor({ line: targetLine, ch: targetCol });
        editor.scrollIntoView({ line: targetLine, ch: targetCol }, 50); // 50px margin
        focus();
    }

    /**
     * Open a file (via FileManager) and then jump to a specific line
     * @param {string} filePath - path of the file to open
     * @param {number} [line] - 1-based line number
     * @param {number} [col] - 1-based column number
     */
    async function openFileAtLine(filePath, line, col) {
        if (!filePath) return;

        // Open the file through FileManager
        if (window.FileManager && typeof window.FileManager.openFile === 'function') {
            await window.FileManager.openFile(filePath);
        }

        // Jump to the specified line after content is loaded
        if (typeof line === 'number') {
            goToLine(line, col);
        }
    }

    // ── Undo / Redo ────────────────────────────────────────────────

    /**
     * Undo the last editor change
     */
    function undo() {
        if (editor) editor.undo();
    }

    /**
     * Redo the last undone editor change
     */
    function redo() {
        if (editor) editor.redo();
    }

    // ── Resize ─────────────────────────────────────────────────────

    /**
     * Refresh the editor layout (call after container size changes)
     */
    function resize() {
        if (editor) {
            editor.refresh();
        }
    }

    // ── Configuration ──────────────────────────────────────────────

    /**
     * Get the current editor configuration
     * @returns {object}
     */
    function getConfig() {
        return {
            fontSize: config.fontSize,
            tabSize: config.tabSize,
            indentUnit: config.indentUnit,
            indentWithTabs: config.indentWithTabs,
            lineWrapping: config.lineWrapping,
            theme: config.theme,
            mode: currentMode,
            inputStyle: 'textarea',
            viewportMargin: Infinity
        };
    }

    /**
     * Change the editor font size
     * @param {number} size - font size in pixels
     */
    function setFontSize(size) {
        size = parseInt(size, 10);
        if (isNaN(size) || size < 8 || size > 40) return;

        config.fontSize = size;
        applyFontSize(size);
    }

    /**
     * Apply a font size to the CodeMirror instance
     * @param {number} size - font size in pixels
     */
    function applyFontSize(size) {
        if (!editor) return;

        const wrapper = editor.getWrapperElement();
        if (wrapper) {
            wrapper.style.fontSize = size + 'px';
        }
    }

    /**
     * Change the editor tab size
     * @param {number} size - number of spaces per tab
     */
    function setTabSize(size) {
        size = parseInt(size, 10);
        if (isNaN(size) || size < 1 || size > 16) return;

        config.tabSize = size;
        config.indentUnit = size;

        if (editor) {
            editor.setOption('tabSize', size);
            editor.setOption('indentUnit', size);
        }
    }

    // ── Utilities ──────────────────────────────────────────────────

    /**
     * Simple debounce helper
     * @param {Function} fn
     * @param {number} delay
     * @returns {Function}
     */
    function debounce(fn, delay) {
        let timer;
        return function (...args) {
            clearTimeout(timer);
            timer = setTimeout(() => fn.apply(this, args), delay);
        };
    }

    // ── Expose the raw CodeMirror instance ─────────────────────────

    /**
     * Get the underlying CodeMirror instance (for advanced usage)
     * @returns {CodeMirror|null}
     */
    function getEditor() {
        return editor;
    }

    // ── Markdown Preview ─────────────────────────────────────────
    let mdPreviewMode = false;

    /**
     * Check if the current file is a markdown file
     * @returns {boolean}
     */
    function isMarkdownFile() {
        if (!currentFilePath) return false;
        return currentFilePath.toLowerCase().endsWith('.md') || currentFilePath.toLowerCase().endsWith('.markdown');
    }

    /**
     * Render markdown content into the preview div
     */
    function renderMarkdownPreview() {
        const previewEl = document.getElementById('markdown-preview');
        if (!previewEl || !editor) return;

        // Save current scroll position before re-rendering (for live updates)
        var savedScrollTop = previewEl.scrollTop;

        var mdRaw = editor.getValue();
        var originalContent = editor.getValue(); // unmodified content for line mapping

        if (typeof marked === 'undefined') {
            previewEl.innerHTML = '<p style="color:var(--text-muted)">Markdown 渲染器未加载</p>';
            return;
        }

        // --- Step 0: Build source-line mapping from lexer tokens ---
        // We lex the ORIGINAL content (before math/code protection) to get
        // accurate line numbers for each block-level token.
        var tokens = marked.lexer(originalContent);
        var blockLineQueue = [];
        var lineOffset = 0;
        for (var ti = 0; ti < tokens.length; ti++) {
            var token = tokens[ti];
            var newLines = (token.raw.match(/\n/g) || []).length;
            var startLine = lineOffset;
            if (token.type !== 'space') {
                blockLineQueue.push({ type: token.type, startLine: startLine });
            }
            lineOffset += newLines;
        }

        // --- Step 0.5: Protect fenced code blocks and inline code from math regex ---
        var codeStore = [];
        var codeIdx = 0;
        function storeCode(match) {
            var id = 'CODEBLK' + (codeIdx++) + 'KLBC';
            codeStore.push({ id: id, code: match });
            return id;
        }
        // Fenced code blocks (``` ... ```)
        mdRaw = mdRaw.replace(/```[\s\S]*?```/g, storeCode);
        // Inline code (` ... `)
        mdRaw = mdRaw.replace(/`[^`]+`/g, storeCode);

        // --- Step 1: Protect math expressions from marked processing ---
        // marked would otherwise interpret _ as <em> and split $$ blocks across paragraphs
        var mathStore = [];
        var mathIdx = 0;
        function storeMath(match) {
            var id = 'MATHPH' + (mathIdx++) + 'XHPM';
            mathStore.push({ id: id, math: match });
            return id;
        }
        // Protect display math $$...$$ (multi-line allowed) — must be before $...$
        mdRaw = mdRaw.replace(/\$\$([\s\S]*?)\$\$/g, function(m) { return storeMath(m); });
        // Protect inline math $...$ (single line only, content cannot be empty)
        mdRaw = mdRaw.replace(/\$([^\$\n]+?)\$/g, function(m) { return storeMath(m); });
        // Protect \(...\) inline math
        mdRaw = mdRaw.replace(/\\\(([\s\S]*?)\\\)/g, function(m) { return storeMath(m); });
        // Protect \[...\] display math
        mdRaw = mdRaw.replace(/\\\[([\s\S]*?)\\\]/g, function(m) { return storeMath(m); });

        // --- Step 1.5: Restore code blocks so marked can process them properly ---
        for (var ci = 0; ci < codeStore.length; ci++) {
            mdRaw = mdRaw.replace(codeStore[ci].id, codeStore[ci].code);
        }

        // --- Step 2: Configure marked v12 with custom code highlighter + data-source-line ---
        // In marked v12, renderer.code receives (code_string, lang_string, escaped_bool)
        var renderer = new marked.Renderer();
        var queueIdx = 0;

        function nextSourceLine(type) {
            // Check if the next queue entry matches this type
            if (queueIdx < blockLineQueue.length && blockLineQueue[queueIdx].type === type) {
                var line = blockLineQueue[queueIdx].startLine;
                queueIdx++;
                return line;
            }
            // Type mismatch → this is a nested render call (e.g. paragraph inside blockquote).
            return -1;
        }

        function injectLine(html, line) {
            if (line < 0) return html; // nested call — don't inject
            return html.replace(/^<(\w+)/, '<$1 data-source-line="' + line + '"');
        }

        // Code renderer with highlight.js + data-source-line
        renderer.code = function(code, lang) {
            var line = nextSourceLine('code');
            var codeHtml;
            if (typeof hljs !== 'undefined') {
                if (lang && hljs.getLanguage(lang)) {
                    try { codeHtml = '<pre><code class="hljs language-' + lang + '">' +
                                hljs.highlight(code, { language: lang }).value + '</code></pre>'; }
                    catch(e) { codeHtml = '<pre><code>' + code + '</code></pre>'; }
                } else {
                    try { codeHtml = '<pre><code class="hljs">' + hljs.highlightAuto(code).value + '</code></pre>'; }
                    catch(e) { codeHtml = '<pre><code>' + code + '</code></pre>'; }
                }
            } else {
                codeHtml = '<pre><code>' + code + '</code></pre>';
            }
            return injectLine(codeHtml, line);
        };

        // Heading renderer with data-source-line
        var origHeading = renderer.heading.bind(renderer);
        renderer.heading = function(text, depth, raw) {
            var line = nextSourceLine('heading');
            return injectLine(origHeading(text, depth, raw), line);
        };

        // Paragraph renderer with data-source-line
        var origParagraph = renderer.paragraph.bind(renderer);
        renderer.paragraph = function(text) {
            var line = nextSourceLine('paragraph');
            return injectLine(origParagraph(text), line);
        };

        // List renderer with data-source-line
        var origList = renderer.list.bind(renderer);
        renderer.list = function(body, ordered, start) {
            var line = nextSourceLine('list');
            return injectLine(origList(body, ordered, start), line);
        };

        // Blockquote renderer with data-source-line
        var origBlockquote = renderer.blockquote.bind(renderer);
        renderer.blockquote = function(body) {
            var line = nextSourceLine('blockquote');
            return injectLine(origBlockquote(body), line);
        };

        // Table renderer with data-source-line
        var origTable = renderer.table.bind(renderer);
        renderer.table = function(header, body) {
            var line = nextSourceLine('table');
            return injectLine(origTable(header, body), line);
        };

        // HR renderer with data-source-line
        var origHr = renderer.hr.bind(renderer);
        renderer.hr = function() {
            var line = nextSourceLine('hr');
            return injectLine(origHr(), line);
        };

        marked.setOptions({
            gfm: true,
            breaks: true,
            renderer: renderer
        });

        // --- Step 3: Parse markdown ---
        var html = marked.parse(mdRaw);

        // --- Step 4: Restore math expressions ---
        for (var i = 0; i < mathStore.length; i++) {
            html = html.replace(mathStore[i].id, mathStore[i].math);
        }

        previewEl.innerHTML = html;

        // --- Step 5: Render math with KaTeX ---
        if (typeof renderMathInElement !== 'undefined') {
            renderMathInElement(previewEl, {
                delimiters: [
                    {left: "$$", right: "$$", display: true},
                    {left: "$", right: "$", display: false},
                    {left: "\\(", right: "\\)", display: false},
                    {left: "\\[", right: "\\]", display: true}
                ],
                throwOnError: false
            });
        }

        // --- Step 6: Restore scroll position after re-render ---
        // This is now handled by scrollPreviewToCursor() called after renderMarkdownPreview(),
        // so we skip the old savedScrollTop logic here.
    }

    /**
     * Scroll the preview to the position corresponding to the current
     * cursor line in the CodeMirror editor.
     */
    function scrollPreviewToCursor() {
        var previewEl = document.getElementById('markdown-preview');
        if (!previewEl || !editor) return;

        var cursorLine = editor.getCursor().line;

        // Find all elements with data-source-line
        var blocks = previewEl.querySelectorAll('[data-source-line]');
        if (blocks.length === 0) {
            // No line annotations — fall back to proportional scroll
            var totalLines = editor.lineCount();
            var ratio = totalLines > 0 ? cursorLine / totalLines : 0;
            previewEl.scrollTop = ratio * (previewEl.scrollHeight - previewEl.clientHeight);
            return;
        }

        // Find the block whose source line is closest to (but <=) the cursor line
        var bestEl = null;
        var bestLine = -1;
        blocks.forEach(function(el) {
            var elLine = parseInt(el.getAttribute('data-source-line'), 10);
            if (elLine <= cursorLine && elLine > bestLine) {
                bestLine = elLine;
                bestEl = el;
            }
        });

        if (bestEl) {
            // Scroll the element into view, near the top of the preview pane
            var elTop = bestEl.offsetTop;
            var viewHeight = previewEl.clientHeight;
            previewEl.scrollTop = Math.max(0, elTop - viewHeight * 0.1);
        } else {
            previewEl.scrollTop = 0;
        }
    }

    /**
     * Scroll the CodeMirror editor to the source line corresponding
     * to the current scroll position in the preview.
     */
    function scrollEditorToPreviewPosition() {
        var previewEl = document.getElementById('markdown-preview');
        if (!previewEl || !editor) return;

        var scrollTop = previewEl.scrollTop;
        var viewHeight = previewEl.clientHeight;
        var scrollMid = scrollTop + viewHeight * 0.3; // use 30% from top as reference

        // Find all elements with data-source-line
        var blocks = previewEl.querySelectorAll('[data-source-line]');
        if (blocks.length === 0) {
            // Fall back to proportional mapping
            var ratio = previewEl.scrollHeight > previewEl.clientHeight
                ? scrollTop / (previewEl.scrollHeight - previewEl.clientHeight)
                : 0;
            var totalLines = editor.lineCount();
            var targetLine = Math.round(ratio * totalLines);
            editor.setCursor({ line: Math.min(targetLine, totalLines - 1), ch: 0 });
            editor.scrollIntoView(null, 50);
            return;
        }

        // Find the block at the current scroll position
        var bestEl = null;
        var bestLine = 0;
        blocks.forEach(function(el) {
            if (el.offsetTop <= scrollMid) {
                var elLine = parseInt(el.getAttribute('data-source-line'), 10);
                if (elLine >= bestLine) {
                    bestLine = elLine;
                    bestEl = el;
                }
            }
        });

        if (bestEl) {
            editor.setCursor({ line: bestLine, ch: 0 });
            editor.scrollIntoView(null, 50);
        }
    }

    /**
     * Toggle markdown preview mode
     */
    function toggleMarkdownPreview() {
        if (!isMarkdownFile()) return;

        mdPreviewMode = !mdPreviewMode;
        const previewEl = document.getElementById('markdown-preview');
        const cmWrapper = editor ? editor.getWrapperElement() : null;
        const toggleBtn = document.getElementById('btn-md-toggle');

        if (mdPreviewMode) {
            // Entering preview: render, then scroll to cursor position
            renderMarkdownPreview();
            if (cmWrapper) cmWrapper.style.display = 'none';
            if (previewEl) previewEl.style.display = '';
            if (toggleBtn) { toggleBtn.textContent = '📝'; toggleBtn.title = '切换编辑'; }
            // Scroll preview to where the cursor is in the source code
            requestAnimationFrame(function() { scrollPreviewToCursor(); });
        } else {
            // Exiting preview: scroll editor to the position visible in preview
            scrollEditorToPreviewPosition();
            if (cmWrapper) cmWrapper.style.display = '';
            if (previewEl) previewEl.style.display = 'none';
            if (toggleBtn) { toggleBtn.textContent = '📖'; toggleBtn.title = '切换预览'; }
            setTimeout(() => resize(), 50);
        }
    }

    /**
     * Update the markdown toggle button visibility based on current file
     */
    function updateMarkdownButton() {
        const btn = document.getElementById('btn-md-toggle');
        if (btn) {
            btn.style.display = isMarkdownFile() ? '' : 'none';
        }
        // If switching away from markdown, reset preview mode
        if (!isMarkdownFile() && mdPreviewMode) {
            mdPreviewMode = false;
            const previewEl = document.getElementById('markdown-preview');
            const cmWrapper = editor ? editor.getWrapperElement() : null;
            if (previewEl) previewEl.style.display = 'none';
            if (cmWrapper) cmWrapper.style.display = '';
        }
        // Update the browser preview button visibility
        updatePreviewButton();
    }

    /**
     * Check if the current file is previewable in the browser (HTML, HTM, MD)
     */
    function isPreviewableFile() {
        if (!currentFilePath) return false;
        const ext = currentFilePath.toLowerCase();
        return ext.endsWith('.html') || ext.endsWith('.htm') || ext.endsWith('.md') || ext.endsWith('.markdown');
    }

    /**
     * Update the browser preview button visibility based on current file
     */
    function updatePreviewButton() {
        const btn = document.getElementById('editor-preview-btn');
        if (btn) {
            btn.style.display = isPreviewableFile() ? '' : 'none';
        }
    }

    /**
     * Preview the current file in the browser panel
     */
    function previewInBrowser() {
        if (!currentFilePath) return;

        // If the file has unsaved changes, auto-save first
        if (editor && !editor.isClean()) {
            if (window.EditorManager && window.EditorManager.saveCurrentFile) {
                window.EditorManager.saveCurrentFile();
            }
        }

        // Build the preview URL relative to workspace
        let relPath = currentFilePath;
        // currentFilePath might be absolute or relative — we need it relative to workspace
        if (window.FileManager && window.FileManager.currentFilePath) {
            relPath = window.FileManager.currentFilePath;
        }
        // Strip /workspace/ prefix if present
        relPath = relPath.replace(/^\/workspace\/?/, '');

        // Use /preview/<path> route so that the <base> tag injected in HTML
        // makes relative CSS/JS paths resolve correctly via /preview/<dir>/
        let previewUrl = '/preview/' + relPath;

        // For Markdown files, pass scroll ratio so the preview can auto-scroll
        // to the position corresponding to the current editor view
        if (isMarkdownFile() && editor) {
            var scrollInfo = editor.getScrollInfo();
            var scrollRatio = 0;
            // Calculate how far down the user has scrolled (0 = top, 1 = bottom)
            var maxScroll = scrollInfo.height - scrollInfo.clientHeight;
            if (maxScroll > 0) {
                scrollRatio = scrollInfo.top / maxScroll;
            }
            // Also pass the cursor line for more precise positioning
            var cursorLine = editor.getCursor().line;
            var totalLines = editor.lineCount();
            var lineRatio = totalLines > 0 ? cursorLine / totalLines : 0;
            // Use lineRatio as the primary position indicator (more accurate for MD mapping)
            previewUrl += '?scroll=' + encodeURIComponent(lineRatio.toFixed(4));
        }

        // Switch to the browser tab in the bottom panel
        const browserTab = document.querySelector('[data-btab="browser"]');
        if (browserTab) {
            browserTab.click();
        }

        // Make sure the bottom panel is visible
        const bottomPanel = document.getElementById('bottom-panel');
        if (bottomPanel && bottomPanel.classList.contains('hidden')) {
            bottomPanel.classList.remove('hidden');
        }

        // Navigate the preview iframe to the file
        const iframe = document.getElementById('preview-frame');
        if (iframe) {
            iframe.src = previewUrl;
        }

        // Update the URL input to show what's being previewed
        const urlInput = document.getElementById('browser-url-input');
        if (urlInput) {
            const filename = currentFilePath.split('/').pop();
            urlInput.value = 'preview: ' + filename;
            urlInput.dataset.originalUrl = previewUrl;
        }
    }

    // ── Git Diff View ────────────────────────────────────────────

    /**
     * Show a git diff view with red/green line highlighting
     * @param {string} diffText - unified diff text
     * @param {string} title - diff title (filename or 'All changes')
     */
    function showDiff(diffText, title, options) {
        if (!diffText) {
            showToast('No diff to display', 'info');
            return;
        }

        title = title || 'Diff';
        options = options || {};
        // options.readOnly = true → commit diff (no rollback buttons)
        // options.commitHash → if set, rollback restores from this commit instead of HEAD
        const isReadOnly = options.readOnly || false;
        const commitHash = options.commitHash || null;

        // Create diff overlay
        const overlay = document.createElement('div');
        overlay.className = 'diff-overlay';
        overlay.id = 'diff-overlay';

        const container = document.createElement('div');
        container.className = 'diff-container';

        // Header — no restore button here anymore (moved to per-file sections)
        const header = document.createElement('div');
        header.className = 'diff-header';
        header.innerHTML = `
            <span class="diff-title">🔀 ${escapeHTML(title)}</span>
            <div class="diff-actions">
                <button class="diff-close-btn" title="Close">✕</button>
            </div>
        `;
        container.appendChild(header);

        // Parse diff text into file groups
        const fileGroups = parseDiffIntoFileGroups(diffText);

        // Diff content
        const content = document.createElement('div');
        content.className = 'diff-content';

        if (fileGroups.length === 0) {
            // Fallback: raw diff without file grouping
            content.innerHTML = renderRawDiff(diffText);
        } else {
            for (const group of fileGroups) {
                const fileSection = document.createElement('div');
                fileSection.className = 'diff-file-section';

                // File header with path and rollback button
                const fileHeader = document.createElement('div');
                fileHeader.className = 'diff-file-header';

                const filePath = group.filePath;
                const hasChanges = group.lines.some(l => l.type === 'add' || l.type === 'del');

                let fileHeaderHTML = `<span class="diff-file-path">${escapeHTML(filePath)}</span>`;
                if (hasChanges && !isReadOnly) {
                    fileHeaderHTML += `<button class="diff-hunk-rollback-btn" data-filepath="${escapeHTML(filePath)}" ${commitHash ? `data-commit="${escapeHTML(commitHash)}"` : ''} title="回滚此文件的修改">⏪ 回滚</button>`;
                }
                fileHeader.innerHTML = fileHeaderHTML;
                fileSection.appendChild(fileHeader);

                // Diff lines for this file
                const linesContainer = document.createElement('div');
                linesContainer.className = 'diff-file-lines';
                let linesHTML = '';
                for (const line of group.lines) {
                    linesHTML += renderDiffLine(line);
                }
                linesContainer.innerHTML = linesHTML;
                fileSection.appendChild(linesContainer);

                content.appendChild(fileSection);
            }
        }

        container.appendChild(content);
        overlay.appendChild(container);
        document.body.appendChild(overlay);

        // Close handler
        const closeBtn = header.querySelector('.diff-close-btn');
        closeBtn.addEventListener('click', () => {
            overlay.remove();
        });
        overlay.addEventListener('click', (e) => {
            if (e.target === overlay) overlay.remove();
        });

        // Rollback button handlers — one per file section
        const rollbackBtns = content.querySelectorAll('.diff-hunk-rollback-btn');
        rollbackBtns.forEach(btn => {
            btn.addEventListener('click', async () => {
                const filepath = btn.dataset.filepath;
                const commit = btn.dataset.commit || null;
                if (!filepath) return;

                if (window.GitManager) {
                    if (commit) {
                        // Restore from specific commit
                        await window.GitManager.restoreFileFromCommit(filepath, commit);
                    } else {
                        // Restore from HEAD
                        await window.GitManager.restoreFile(filepath);
                    }
                    // After restore, refresh and close diff overlay
                    const diffOverlay = document.getElementById('diff-overlay');
                    if (diffOverlay) diffOverlay.remove();
                }
            });
        });

        // Escape key
        const escHandler = (e) => {
            if (e.key === 'Escape') {
                overlay.remove();
                document.removeEventListener('keydown', escHandler);
            }
        };
        document.addEventListener('keydown', escHandler);
    }

    /**
     * Parse unified diff text into file groups.
     * Each group has: { filePath, lines: [{text, type}] }
     * Types: 'meta', 'hunk', 'add', 'del', 'ctx', 'empty', 'file-header'
     */
    function parseDiffIntoFileGroups(diffText) {
        const groups = [];
        let currentGroup = null;
        const lines = diffText.split('\n');

        for (const line of lines) {
            // File boundary: "diff --git a/path b/path"
            if (line.startsWith('diff --git ')) {
                // Extract file path from "diff --git a/path b/path"
                const match = line.match(/^diff --git (?:a\/.+? )?b\/(.+)$/);
                const filePath = match ? match[1] : line.replace(/^diff --git /, '');
                currentGroup = { filePath, lines: [] };
                groups.push(currentGroup);
                currentGroup.lines.push({ text: line, type: 'file-header' });
                continue;
            }

            // If we haven't found a file header yet, create a default group
            if (!currentGroup) {
                // Check if this looks like it starts with --- / +++ (single file diff)
                if (line.startsWith('--- a/') || line.startsWith('--- ')) {
                    const match = line.match(/^--- (?:a\/)?(.+)$/);
                    const filePath = match ? match[1] : 'unknown';
                    currentGroup = { filePath, lines: [] };
                    groups.push(currentGroup);
                } else {
                    // No file grouping possible, return empty to trigger raw fallback
                    return [];
                }
            }

            // Categorize line
            if (line === '') {
                currentGroup.lines.push({ text: line, type: 'empty' });
            } else if (line.startsWith('@@')) {
                currentGroup.lines.push({ text: line, type: 'hunk' });
            } else if (line.startsWith('--- ') || line.startsWith('+++ ')) {
                currentGroup.lines.push({ text: line, type: 'meta' });
            } else if (line.startsWith('+')) {
                currentGroup.lines.push({ text: line, type: 'add' });
            } else if (line.startsWith('-')) {
                currentGroup.lines.push({ text: line, type: 'del' });
            } else if (line.startsWith('index ') || line.startsWith('new file ') || line.startsWith('deleted ') || line.startsWith('old mode') || line.startsWith('new mode') || line.startsWith('Binary files') || line.startsWith('similarity ')) {
                currentGroup.lines.push({ text: line, type: 'meta' });
            } else {
                currentGroup.lines.push({ text: line, type: 'ctx' });
            }
        }

        return groups;
    }

    /**
     * Render a single diff line as HTML
     */
    function renderDiffLine(lineObj) {
        const escaped = escapeHTML(lineObj.text);
        switch (lineObj.type) {
            case 'empty':
                return '<div class="diff-line diff-empty"></div>';
            case 'hunk':
                return `<div class="diff-line diff-hunk">${escaped}</div>`;
            case 'meta':
                return `<div class="diff-line diff-meta">${escaped}</div>`;
            case 'file-header':
                return `<div class="diff-line diff-file-header-line">${escaped}</div>`;
            case 'add': {
                const code = escaped.substring(1);
                return `<div class="diff-line diff-add"><span class="diff-sign">+</span>${code || ' '}</div>`;
            }
            case 'del': {
                const code = escaped.substring(1);
                return `<div class="diff-line diff-del"><span class="diff-sign">-</span>${code || ' '}</div>`;
            }
            case 'ctx':
            default:
                return `<div class="diff-line diff-ctx"><span class="diff-sign"> </span>${escaped}</div>`;
        }
    }

    /**
     * Fallback: render raw diff without file grouping
     */
    function renderRawDiff(diffText) {
        const lines = diffText.split('\n');
        let html = '';
        for (const line of lines) {
            const escaped = escapeHTML(line);
            if (escaped === '') {
                html += '<div class="diff-line diff-empty"></div>';
            } else if (escaped.startsWith('@@')) {
                html += `<div class="diff-line diff-hunk">${escaped}</div>`;
            } else if (escaped.startsWith('---') || escaped.startsWith('+++')) {
                html += `<div class="diff-line diff-meta">${escaped}</div>`;
            } else if (escaped.startsWith('+')) {
                const code = escaped.substring(1);
                html += `<div class="diff-line diff-add"><span class="diff-sign">+</span>${code || ' '}</div>`;
            } else if (escaped.startsWith('-')) {
                const code = escaped.substring(1);
                html += `<div class="diff-line diff-del"><span class="diff-sign">-</span>${code || ' '}</div>`;
            } else {
                html += `<div class="diff-line diff-ctx"><span class="diff-sign"> </span>${escaped}</div>`;
            }
        }
        return html;
    }

    /**
     * Escape HTML for safe rendering
     */
    function escapeHTML(str) {
        const div = document.createElement('div');
        div.textContent = str || '';
        return div.innerHTML;
    }

    // ── Selection Mode ──────────────────────────────────────────────

    /**
     * Set up long-press detection on the CodeMirror wrapper for selection mode
     */
    function setupSelectionModeListeners() {
        if (!editor) return;
        const wrapper = editor.getWrapperElement();
        if (!wrapper) return;

        let touchStartPos = null;

        wrapper.addEventListener('touchstart', (e) => {
            // Only handle single finger
            if (e.touches.length !== 1) return;
            // Don't interfere if already in selection mode
            if (selectionMode) return;

            const touch = e.touches[0];
            touchStartPos = { x: touch.clientX, y: touch.clientY };

            // Clear any existing timer
            if (longPressTimer) {
                clearTimeout(longPressTimer);
                longPressTimer = null;
            }

            // Start long press timer (500ms)
            longPressTimer = setTimeout(() => {
                longPressTimer = null;
                // Verify finger hasn't moved much
                if (!touchStartPos) return;
                showContextMenu(touchStartPos.x, touchStartPos.y);
            }, 500);
        }, { passive: true });

        wrapper.addEventListener('touchmove', (e) => {
            // Cancel long press if finger moves
            if (longPressTimer) {
                // Check if moved too far
                if (e.touches.length === 1 && touchStartPos) {
                    const touch = e.touches[0];
                    const dx = Math.abs(touch.clientX - touchStartPos.x);
                    const dy = Math.abs(touch.clientY - touchStartPos.y);
                    if (dx > 10 || dy > 10) {
                        clearTimeout(longPressTimer);
                        longPressTimer = null;
                    }
                }
            }
        }, { passive: true });

        wrapper.addEventListener('touchend', () => {
            touchStartPos = null;
            if (longPressTimer) {
                clearTimeout(longPressTimer);
                longPressTimer = null;
            }
        }, { passive: true });

        wrapper.addEventListener('touchcancel', () => {
            touchStartPos = null;
            if (longPressTimer) {
                clearTimeout(longPressTimer);
                longPressTimer = null;
            }
        }, { passive: true });
    }

    /**
     * Show the context menu at the given screen coordinates
     */
    function showContextMenu(x, y) {
        // Remove any existing context menu
        removeContextMenu();

        const menu = document.createElement('div');
        menu.className = 'editor-context-menu';
        menu.style.left = x + 'px';
        menu.style.top = y + 'px';

        const item = document.createElement('button');
        item.className = 'editor-context-menu-item';
        item.textContent = '进入选择模式';
        item.addEventListener('click', (e) => {
            e.preventDefault();
            e.stopPropagation();
            removeContextMenu();
            // Convert screen coords to editor position
            const pos = editor.coordsChar({ left: x, top: y }, 'window');
            if (pos) {
                enterSelectionMode(pos);
            }
        });

        menu.appendChild(item);
        document.body.appendChild(menu);
        contextMenuEl = menu;

        // Adjust position if menu overflows viewport
        requestAnimationFrame(() => {
            if (!contextMenuEl) return;
            const rect = contextMenuEl.getBoundingClientRect();
            if (rect.right > window.innerWidth) {
                contextMenuEl.style.left = (window.innerWidth - rect.width - 8) + 'px';
            }
            if (rect.bottom > window.innerHeight) {
                contextMenuEl.style.top = (y - rect.height) + 'px';
            }
        });

        // Dismiss on any touch outside the menu
        const dismissHandler = (e) => {
            if (contextMenuEl && !contextMenuEl.contains(e.target)) {
                removeContextMenu();
            }
        };
        setTimeout(() => {
            document.addEventListener('touchstart', dismissHandler, { once: true, passive: true });
            document.addEventListener('mousedown', dismissHandler, { once: true });
        }, 100);
    }

    /**
     * Remove the context menu popup
     */
    function removeContextMenu() {
        if (contextMenuEl) {
            contextMenuEl.remove();
            contextMenuEl = null;
        }
    }

    /**
     * Enter selection mode at the given CodeMirror position
     * @param {Object} pos - {line, ch} CodeMirror position
     */
    function enterSelectionMode(pos) {
        if (!editor || selectionMode) return;

        selectionMode = true;
        selLastCopiedText = '';

        // Select the word at the given position
        const wordRange = editor.findWordAt(pos);
        editor.setSelection(wordRange.anchor, wordRange.head);

        // Show exit button
        const exitBtn = document.getElementById('editor-exit-selection-btn');
        if (exitBtn) exitBtn.style.display = '';

        // Add CSS class for enhanced selection visibility
        const wrapper = editor.getWrapperElement();
        if (wrapper) wrapper.classList.add('sel-mode-active');

        // ── Create transparent overlay on top of editor ──
        // This overlay captures ALL touch events, preventing CodeMirror from
        // handling them. This is more reliable than capture-phase interception.
        const editorContainer = document.getElementById('editor-container');
        if (editorContainer) {
            selOverlay = document.createElement('div');
            selOverlay.className = 'sel-overlay';
            editorContainer.appendChild(selOverlay);
        }

        // ── Create fixed-position handles ──
        createSelectionHandles();

        // ── Update handle positions (initial) ──
        updateSelectionHandlePositions();

        // ── Listen for editor scroll to update handle positions ──
        editor.on('scroll', updateSelectionHandlePositions);
        editor.on('cursorActivity', updateSelectionHandlePositions);

        console.log('Selection mode entered');
    }

    /**
     * Exit selection mode and clean up
     */
    function exitSelectionMode() {
        if (!selectionMode) return;

        selectionMode = false;
        selDragging = null;
        selLastCopiedText = '';

        // Remove selection mode CSS class
        const wrapper = editor.getWrapperElement();
        if (wrapper) wrapper.classList.remove('sel-mode-active');

        // Remove overlay
        if (selOverlay) {
            selOverlay.remove();
            selOverlay = null;
        }

        // Cancel auto-scroll
        if (selAutoScrollRAF) {
            cancelAnimationFrame(selAutoScrollRAF);
            selAutoScrollRAF = null;
        }

        // Clear selection
        if (editor) {
            editor.setCursor(editor.getCursor()); // collapse selection
        }

        // Hide exit button
        const exitBtn = document.getElementById('editor-exit-selection-btn');
        if (exitBtn) exitBtn.style.display = 'none';

        // Remove cursor handles
        removeSelectionHandles();

        // Remove scroll/cursorActivity listeners
        if (editor) {
            editor.off('scroll', updateSelectionHandlePositions);
            editor.off('cursorActivity', updateSelectionHandlePositions);
        }

        // Remove context menu if visible
        removeContextMenu();
        removeSelectionContextMenu();

        console.log('Selection mode exited');
    }

    /**
     * Create the start and end cursor handle DOM elements (fixed-positioned)
     * Handles use SVG teardrop/droplet shape (like iOS native selection handles).
     * The "hammer head" is a large droplet at top, the "stem" is a thin line
     * going down to the exact text character position.
     */
    function createSelectionHandles() {
        removeSelectionHandles();

        // SVG for a teardrop/droplet shape (hammer head)
        // The droplet points DOWN — the tip of the teardrop touches the text.
        // Width: 22, Height: 28 (droplet) + variable stem height
        const dropletSVG = `<svg viewBox="0 0 22 28" width="22" height="28" xmlns="http://www.w3.org/2000/svg">
            <path d="M11 0C4.9 0 0 4.9 0 10.5c0 6 11 17.5 11 17.5s11-11.5 11-17.5C22 4.9 17.1 0 11 0z" fill="#4a9eff" stroke="#2a7adf" stroke-width="0.5"/>
        </svg>`;

        // Start handle
        selHandleStart = document.createElement('div');
        selHandleStart.className = 'sel-handle sel-handle-start';
        const startHead = document.createElement('div');
        startHead.className = 'sel-handle-head';
        startHead.innerHTML = dropletSVG;
        selHandleStart.appendChild(startHead);
        const startStem = document.createElement('div');
        startStem.className = 'sel-handle-stem';
        selHandleStart.appendChild(startStem);

        // End handle
        selHandleEnd = document.createElement('div');
        selHandleEnd.className = 'sel-handle sel-handle-end';
        const endHead = document.createElement('div');
        endHead.className = 'sel-handle-head';
        endHead.innerHTML = dropletSVG;
        selHandleEnd.appendChild(endHead);
        const endStem = document.createElement('div');
        endStem.className = 'sel-handle-stem';
        selHandleEnd.appendChild(endStem);

        // Append to body (fixed-positioned)
        document.body.appendChild(selHandleStart);
        document.body.appendChild(selHandleEnd);

        // ── Overlay touch handlers ──
        if (selOverlay) {
            selOverlay.addEventListener('touchstart', onSelTouchStart, { passive: false });
            selOverlay.addEventListener('touchmove', onSelTouchMove, { passive: false });
            selOverlay.addEventListener('touchend', onSelTouchEnd, { passive: false });
            selOverlay.addEventListener('touchcancel', onSelTouchEnd, { passive: false });
        }

        // Direct touches on the handle heads start dragging that handle
        startHead.addEventListener('touchstart', (e) => {
            e.preventDefault();
            e.stopPropagation();
            selDragging = 'start';
        }, { passive: false });
        endHead.addEventListener('touchstart', (e) => {
            e.preventDefault();
            e.stopPropagation();
            selDragging = 'end';
        }, { passive: false });

        // Also catch touches on the full handle container
        selHandleStart.addEventListener('touchstart', (e) => {
            e.preventDefault();
            e.stopPropagation();
            selDragging = 'start';
        }, { passive: false });
        selHandleEnd.addEventListener('touchstart', (e) => {
            e.preventDefault();
            e.stopPropagation();
            selDragging = 'end';
        }, { passive: false });

        // Document-level for when finger moves off handle during drag
        document.addEventListener('touchmove', onSelHandleTouchMove, { passive: false });
        document.addEventListener('touchend', onSelHandleTouchEnd, { passive: false });
    }

    /**
     * Remove cursor handle DOM elements and touch listeners
     */
    function removeSelectionHandles() {
        // Remove document-level touch handlers for handles
        document.removeEventListener('touchmove', onSelHandleTouchMove, { passive: false });
        document.removeEventListener('touchend', onSelHandleTouchEnd, { passive: false });

        if (selHandleStart) { selHandleStart.remove(); selHandleStart = null; }
        if (selHandleEnd) { selHandleEnd.remove(); selHandleEnd = null; }
    }

    /**
     * Update positions of cursor handles based on current selection.
     * Handles use position:fixed with window coordinates from charCoords.
     *
     * Layout of each handle (top to bottom):
     *   .sel-handle-head: padding 11px left + 0 top → SVG droplet 22×28
     *                     Touch target = 44×28 (padding expands it)
     *   .sel-handle-stem: 2px wide blue line, height = one line of text
     *
     * The droplet tip (bottom-center of SVG) aligns with the char position.
     * SVG is 22×28, with padding-left:11px on the head.
     * So the droplet tip is at: container.left + 11 + 11 = container.left + 22 (center of 22px SVG)
     *                           container.top + 0 + 28 = container.top + 28 (bottom of SVG)
     */
    function updateSelectionHandlePositions() {
        if (!editor || !selectionMode || !selHandleStart || !selHandleEnd) return;

        const sel = editor.getSelection();
        if (!sel) {
            selHandleStart.style.display = 'none';
            selHandleEnd.style.display = 'none';
            return;
        }

        selHandleStart.style.display = '';
        selHandleEnd.style.display = '';

        const from = editor.getCursor('from');
        const to = editor.getCursor('to');

        // Use 'window' coordinates — works with position:fixed
        const startCoords = editor.charCoords(from, 'window');
        const endCoords = editor.charCoords(to, 'window');

        // Droplet dimensions & offsets
        const svgW = 22;       // SVG viewBox width
        const svgH = 28;       // SVG viewBox height
        const headPadL = 11;   // CSS padding-left on .sel-handle-head

        // ── Start handle ──
        // Droplet tip should be at (startCoords.left, startCoords.top)
        // Tip is at: container.left + headPadL + svgW/2, container.top + svgH
        // So: container.left = charX - headPadL - svgW/2
        //     container.top  = charY - svgH
        const sx = startCoords.left - headPadL - svgW / 2;
        const sy = startCoords.top - svgH;
        selHandleStart.style.left = sx + 'px';
        selHandleStart.style.top = sy + 'px';

        const startStem = selHandleStart.querySelector('.sel-handle-stem');
        if (startStem) {
            const lineHeight = startCoords.bottom - startCoords.top;
            startStem.style.height = lineHeight + 'px';
        }

        // ── End handle ──
        const ex = endCoords.left - headPadL - svgW / 2;
        const ey = endCoords.top - svgH;
        selHandleEnd.style.left = ex + 'px';
        selHandleEnd.style.top = ey + 'px';

        const endStem = selHandleEnd.querySelector('.sel-handle-stem');
        if (endStem) {
            const lineHeight = endCoords.bottom - endCoords.top;
            endStem.style.height = lineHeight + 'px';
        }
    }

    // ── Overlay touch event handlers ──────────────────────────────

    /**
     * Overlay touchstart: determine which handle to drag and start dragging
     */
    function onSelTouchStart(e) {
        if (!selectionMode || !editor) return;

        // Allow pinch-zoom (2+ fingers) — don't interfere
        if (e.touches.length >= 2) return;

        e.preventDefault(); // prevent CodeMirror from getting single-finger events

        // Remove any visible selection context menu when starting a new drag
        removeSelectionContextMenu();

        const touch = e.touches[0];
        if (!touch) return;

        // Convert touch to editor position
        const touchPos = editor.coordsChar({
            left: touch.clientX,
            top: touch.clientY
        }, 'window');

        if (!touchPos) return;

        // Determine which end of the selection is closer to the touch point
        const from = editor.getCursor('from');
        const to = editor.getCursor('to');

        const distToFrom = Math.abs(touchPos.line - from.line) * 1000 + Math.abs(touchPos.ch - from.ch);
        const distToTo = Math.abs(touchPos.line - to.line) * 1000 + Math.abs(touchPos.ch - to.ch);

        selDragging = distToFrom <= distToTo ? 'start' : 'end';

        // Move that handle to the touch position immediately
        applyDragPosition(touchPos);
    }

    /**
     * Overlay touchmove: update selection as finger moves
     */
    function onSelTouchMove(e) {
        if (!selDragging || !selectionMode || !editor) return;
        e.preventDefault();

        const touch = e.touches[0];
        if (!touch) return;

        const pos = editor.coordsChar({
            left: touch.clientX,
            top: touch.clientY
        }, 'window');

        if (!pos) return;

        applyDragPosition(pos);

        // Auto-scroll when finger is near the edge
        startAutoScroll(touch.clientY);
    }

    /**
     * Overlay touchend: stop dragging and auto-copy
     */
    function onSelTouchEnd(e) {
        e.preventDefault();
        finishDrag();
    }

    /**
     * Document-level touchmove for when user drags directly on a handle
     * (finger may move off the handle while dragging)
     */
    function onSelHandleTouchMove(e) {
        if (!selDragging || !selectionMode || !editor) return;
        e.preventDefault();

        const touch = e.touches[0];
        if (!touch) return;

        const pos = editor.coordsChar({
            left: touch.clientX,
            top: touch.clientY
        }, 'window');

        if (!pos) return;

        applyDragPosition(pos);
        startAutoScroll(touch.clientY);
    }

    /**
     * Document-level touchend for handle drags
     */
    function onSelHandleTouchEnd(e) {
        if (!selDragging || !selectionMode) return;
        e.preventDefault();
        finishDrag();
    }

    /**
     * Apply the drag position to the selection
     * @param {Object} pos - CodeMirror {line, ch} position
     */
    function applyDragPosition(pos) {
        if (!selDragging || !editor) return;

        const from = editor.getCursor('from');
        const to = editor.getCursor('to');

        if (selDragging === 'start') {
            if (CodeMirror.cmpPos(pos, to) > 0) {
                // Flipped: start went past end
                editor.setSelection(to, pos);
                selDragging = 'end';
            } else {
                editor.setSelection(pos, to);
            }
        } else {
            if (CodeMirror.cmpPos(pos, from) < 0) {
                // Flipped: end went before start
                editor.setSelection(pos, from);
                selDragging = 'start';
            } else {
                editor.setSelection(from, pos);
            }
        }

        updateSelectionHandlePositions();
    }

    /**
     * Finish a drag: stop dragging and show context menu
     */
    function finishDrag() {
        // Stop auto-scroll
        if (selAutoScrollRAF) {
            cancelAnimationFrame(selAutoScrollRAF);
            selAutoScrollRAF = null;
        }

        selDragging = null;

        // Show context menu with copy/cut/paste options
        if (editor && editor.somethingSelected()) {
            showSelectionContextMenu();
        }
    }

    /**
     * Show the selection context menu (copy/cut/paste) near the selection
     */
    function showSelectionContextMenu() {
        removeSelectionContextMenu();

        if (!editor || !editor.somethingSelected()) return;

        // Position the menu near the end of the selection
        const selTo = editor.getCursor('to');
        const coords = editor.charCoords(selTo, 'window');

        const menu = document.createElement('div');
        menu.className = 'editor-context-menu sel-context-menu';

        // Copy button
        const copyBtn = document.createElement('button');
        copyBtn.className = 'editor-context-menu-item';
        copyBtn.textContent = '复制';
        copyBtn.addEventListener('click', (e) => {
            e.preventDefault();
            e.stopPropagation();
            const text = editor.getSelection();
            if (text) {
                selLastCopiedText = text;
                copyToClipboard(text);
                showEditorToast('已复制到剪贴板');
            }
            removeSelectionContextMenu();
        });
        menu.appendChild(copyBtn);

        // Cut button
        const cutBtn = document.createElement('button');
        cutBtn.className = 'editor-context-menu-item';
        cutBtn.textContent = '剪切';
        cutBtn.addEventListener('click', (e) => {
            e.preventDefault();
            e.stopPropagation();
            const text = editor.getSelection();
            if (text) {
                selLastCopiedText = text;
                copyToClipboard(text);
                editor.replaceSelection('');
                showEditorToast('已剪切到剪贴板');
            }
            removeSelectionContextMenu();
            // Exit selection mode after cut since content is removed
            exitSelectionMode();
        });
        menu.appendChild(cutBtn);

        // Paste button — uses a dialog with textarea for native paste
        const pasteBtn = document.createElement('button');
        pasteBtn.className = 'editor-context-menu-item';
        pasteBtn.textContent = '粘贴';
        pasteBtn.addEventListener('click', (e) => {
            e.preventDefault();
            e.stopPropagation();
            removeSelectionContextMenu();
            showPasteDialog();
        });
        menu.appendChild(pasteBtn);

        document.body.appendChild(menu);
        selContextMenuEl = menu;

        // Position: place below the end of the selection
        let menuX = coords.left;
        let menuY = coords.bottom + 4;

        // Adjust if overflows viewport
        requestAnimationFrame(() => {
            if (!selContextMenuEl) return;
            const rect = selContextMenuEl.getBoundingClientRect();
            if (rect.right > window.innerWidth) {
                selContextMenuEl.style.left = Math.max(4, window.innerWidth - rect.width - 8) + 'px';
            }
            if (rect.bottom > window.innerHeight) {
                // Move above the selection instead
                const selFrom = editor.getCursor('from');
                const fromCoords = editor.charCoords(selFrom, 'window');
                selContextMenuEl.style.top = Math.max(4, fromCoords.top - rect.height - 4) + 'px';
            }
        });

        menuX = Math.max(8, menuX - 40); // slightly offset left
        menu.style.left = menuX + 'px';
        menu.style.top = menuY + 'px';

        // Dismiss on touch outside
        const dismissHandler = (e) => {
            if (selContextMenuEl && !selContextMenuEl.contains(e.target)) {
                removeSelectionContextMenu();
            }
        };
        setTimeout(() => {
            document.addEventListener('touchstart', dismissHandler, { once: true, passive: true });
            document.addEventListener('mousedown', dismissHandler, { once: true });
        }, 100);
    }

    /**
     * Remove the selection context menu
     */
    function removeSelectionContextMenu() {
        if (selContextMenuEl) {
            selContextMenuEl.remove();
            selContextMenuEl = null;
        }
    }

    /**
     * Show a paste dialog with a textarea for native browser paste.
     * On mobile, navigator.clipboard.readText() is often blocked by security
     * restrictions, so we use a textarea where the user can long-press → paste
     * using the browser's native clipboard functionality.
     */
    function showPasteDialog() {
        if (!editor) return;

        // Save current selection range so we can replace it after paste
        const selFrom = editor.getCursor('from');
        const selTo = editor.getCursor('to');

        // Create overlay
        const overlay = document.createElement('div');
        overlay.className = 'sel-paste-overlay';

        // Create dialog
        const dialog = document.createElement('div');
        dialog.className = 'sel-paste-dialog';

        // Title
        const title = document.createElement('div');
        title.className = 'sel-paste-title';
        title.textContent = '粘贴';
        dialog.appendChild(title);

        // Hint
        const hint = document.createElement('div');
        hint.className = 'sel-paste-hint';
        hint.textContent = '长按下方输入框粘贴内容';
        dialog.appendChild(hint);

        // Textarea for paste input
        const textarea = document.createElement('textarea');
        textarea.className = 'sel-paste-input';
        textarea.placeholder = '在此处粘贴...';
        textarea.rows = 4;
        dialog.appendChild(textarea);

        // Button row
        const btnRow = document.createElement('div');
        btnRow.className = 'sel-paste-btn-row';

        // Cancel button
        const cancelBtn = document.createElement('button');
        cancelBtn.className = 'sel-paste-btn sel-paste-btn-cancel';
        cancelBtn.textContent = '取消';
        cancelBtn.addEventListener('click', () => {
            overlay.remove();
        });
        btnRow.appendChild(cancelBtn);

        // Confirm button
        const confirmBtn = document.createElement('button');
        confirmBtn.className = 'sel-paste-btn sel-paste-btn-confirm';
        confirmBtn.textContent = '粘贴';
        confirmBtn.addEventListener('click', () => {
            const text = textarea.value;
            if (text) {
                // Replace selection with pasted content
                editor.setSelection(selFrom, selTo);
                editor.replaceSelection(text);
                showEditorToast('已粘贴');
            }
            overlay.remove();
            exitSelectionMode();
        });
        btnRow.appendChild(confirmBtn);

        dialog.appendChild(btnRow);
        overlay.appendChild(dialog);
        document.body.appendChild(overlay);

        // Focus the textarea after a small delay (for mobile keyboard)
        setTimeout(() => textarea.focus(), 100);
    }

    /**
     * Start auto-scrolling when the touch is near the edge of the editor
     * @param {number} clientY - the Y coordinate of the touch
     */
    function startAutoScroll(clientY) {
        if (selAutoScrollRAF) {
            cancelAnimationFrame(selAutoScrollRAF);
            selAutoScrollRAF = null;
        }

        if (!editor || !selDragging) return;

        const wrapperRect = editor.getWrapperElement().getBoundingClientRect();
        const edgeMargin = 50;

        if (clientY < wrapperRect.top + edgeMargin || clientY > wrapperRect.bottom - edgeMargin) {
            const scrollInfo = editor.getScrollInfo();
            const step = editor.defaultTextHeight();
            const direction = clientY < wrapperRect.top + edgeMargin ? -step : step;

            const doScroll = () => {
                if (!selDragging || !selectionMode) return;
                editor.scrollTo(null, editor.getScrollInfo().top + direction);
                updateSelectionHandlePositions();
                selAutoScrollRAF = requestAnimationFrame(doScroll);
            };
            selAutoScrollRAF = requestAnimationFrame(doScroll);
        }
    }

    /**
     * Copy text to clipboard
     * @param {string} text
     */
    function copyToClipboard(text) {
        if (navigator.clipboard && navigator.clipboard.writeText) {
            navigator.clipboard.writeText(text).catch(() => {
                fallbackCopyToClipboard(text);
            });
        } else {
            fallbackCopyToClipboard(text);
        }
    }

    /**
     * Fallback copy to clipboard using textarea trick
     * @param {string} text
     */
    function fallbackCopyToClipboard(text) {
        const ta = document.createElement('textarea');
        ta.value = text;
        ta.style.position = 'fixed';
        ta.style.left = '-9999px';
        ta.style.top = '-9999px';
        ta.style.opacity = '0';
        document.body.appendChild(ta);
        ta.select();
        try {
            document.execCommand('copy');
        } catch (err) {
            console.warn('Clipboard copy failed:', err);
        }
        ta.remove();
    }

    /**
     * Show a toast notification in the editor area
     * @param {string} message
     */
    function showEditorToast(message) {
        const container = document.getElementById('editor-container');
        if (!container) return;

        const toast = document.createElement('div');
        toast.className = 'editor-toast';
        toast.textContent = message;
        container.appendChild(toast);

        // Auto-remove after animation completes
        setTimeout(() => {
            toast.remove();
        }, 1500);
    }

    // ── Selection Mode: No global listeners needed ──────────────
    // All touch handling is done via the overlay and handle elements.

    // ── Auto-init when DOM is ready ────────────────────────────────

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }

    // ── Public API ─────────────────────────────────────────────────
    return {
        init,
        getEditor,

        // Content
        setContent,
        getContent,

        // Mode
        setMode,
        getMode,
        setLanguageForFile,

        // File tracking
        getCurrentFile,
        setCurrentFile,

        // Dirty state
        markClean,
        markDirty,
        isDirty,

        // Focus
        focus,

        // Search
        search,
        closeSearchBar,
        findNext,
        findPrev,
        getSearchInfo,
        replaceCurrent,
        replaceAll,

        // Navigation
        goToLine,
        openFileAtLine,

        // Undo / Redo
        undo,
        redo,

        // Layout
        resize,

        // Configuration
        getConfig,
        setFontSize,
        setTabSize,

        // Markdown
        isMarkdownFile,
        toggleMarkdownPreview,
        renderMarkdownPreview,

        // Tab management
        openTab,
        closeTab,
        switchTab,
        getTabList,
        hasTab,
        getActiveTab,
        setTabDirty,

        // Diff view
        showDiff,
        
        // Multi-Select API
        isMultiSelectMode,
        enterMultiSelect,
        exitMultiSelect,
        addCursorAt,
        selectAllOccurrences,
        getMultiCursors,

        // Selection Mode API
        enterSelectionMode,
        exitSelectionMode
    };
})();

// ── Multi-Select Implementation ──────────────────────────────────────

/**
 * Check if running on mobile device
 */
function isMobile() {
    return /Android|webOS|iPhone|iPad|iPod|BlackBerry|IEMobile|Opera Mini/i.test(navigator.userAgent);
}

/**
 * Handle multi-select click events
 */
function handleMultiSelectClick(event) {
    if (!editor) return;
    
    const pos = editor.coordsChar({
        left: event.clientX,
        top: event.clientY
    });
    
    if (multiSelectMode) {
        // Add new cursor
        addCursorAt(pos.line, pos.ch);
    } else {
        // Start multi-select mode
        enterMultiSelect(pos.line, pos.ch);
    }
}

/**
 * Enter multi-select mode with initial cursor
 */
function enterMultiSelect(line, ch) {
    if (!editor) return;
    
    multiSelectMode = true;
    multiCursors = [{line, ch}];
    selectionRanges = [];
    
    // Update cursor display
    updateMultiCursorDisplay();
    
    // Update status bar
    updateMultiSelectStatus();
    
    // Dispatch event
    document.dispatchEvent(new CustomEvent('editor:multiselect:enter'));
}

/**
 * Exit multi-select mode
 */
function exitMultiSelect() {
    if (!editor) return;
    
    multiSelectMode = false;
    multiCursors = [];
    selectionRanges = [];
    
    // Clear multi-cursor display
    editor.refresh();
    
    // Update status bar
    updateCursorPos();
    
    // Dispatch event
    document.dispatchEvent(new CustomEvent('editor:multiselect:exit'));
}

/**
 * Add cursor at specific position
 */
function addCursorAt(line, ch) {
    if (!editor || !multiSelectMode) return;
    
    // Check if cursor limit reached
    if (multiCursors.length >= config.multiSelect.maxCursors) {
        showNotification(`Maximum ${config.multiSelect.maxCursors} cursors allowed`);
        return;
    }
    
    // Check if cursor already exists at this position
    const exists = multiCursors.some(cursor => cursor.line === line && cursor.ch === ch);
    if (exists) return;
    
    // Add new cursor
    multiCursors.push({line, ch});
    
    // Update display
    updateMultiCursorDisplay();
    updateMultiSelectStatus();
}

/**
 * Add cursor above current active cursor
 */
function addCursorAbove() {
    if (!editor || !multiSelectMode || multiCursors.length === 0) return;
    
    const activeCursor = multiCursors[multiCursors.length - 1];
    const newLine = Math.max(0, activeCursor.line - 1);
    
    addCursorAt(newLine, activeCursor.ch);
}

/**
 * Add cursor below current active cursor
 */
function addCursorBelow() {
    if (!editor || !multiSelectMode || multiCursors.length === 0) return;
    
    const activeCursor = multiCursors[multiCursors.length - 1];
    const newLine = Math.min(editor.lineCount() - 1, activeCursor.line + 1);
    
    addCursorAt(newLine, activeCursor.ch);
}

/**
 * Select all occurrences of current word/selection
 */
function selectAllOccurrences() {
    if (!editor) return;
    
    // Get current selection or word under cursor
    let selection = editor.getSelection();
    let search_term = selection;
    
    if (!selection) {
        // Get word under cursor
        const cursor = editor.getCursor();
        const line = editor.getLine(cursor.line);
        const word = getWordAt(line, cursor.ch);
        search_term = word;
    }
    
    if (!search_term) return;
    
    // Find all occurrences
    const occurrences = [];
    const doc = editor.getDoc();
    
    for (let i = 0; i < doc.lineCount(); i++) {
        const line = doc.getLine(i);
        let pos = 0;
        
        while (pos < line.length) {
            const index = line.indexOf(search_term, pos);
            if (index === -1) break;
            
            occurrences.push({line: i, ch: index});
            pos = index + 1;
        }
    }
    
    // Start multi-select with all occurrences
    if (occurrences.length > 0) {
        multiSelectMode = true;
        multiCursors = occurrences;
        selectionRanges = occurrences.map(cursor => ({
            anchor: cursor,
            head: {line: cursor.line, ch: cursor.ch + search_term.length}
        }));
        
        updateMultiCursorDisplay();
        updateMultiSelectStatus();
        
        document.dispatchEvent(new CustomEvent('editor:multiselect:enter'));
    }
}

/**
 * Get word at position in line
 */
function getWordAt(line, pos) {
    const left = line.slice(0, pos);
    const right = line.slice(pos);
    
    const leftMatch = left.match(/\w*$/);
    const rightMatch = right.match(/^\w*/);
    
    if (leftMatch && rightMatch) {
        return leftMatch[0] + rightMatch[0];
    }
    
    return '';
}

/**
 * Update multi-cursor display
 */
function updateMultiCursorDisplay() {
    if (!editor) return;
    
    // Clear existing cursors (CodeMirror will handle this)
    editor.refresh();
    
    // Note: CodeMirror 5 doesn't support true multiple cursors
    // This is a simulation - we'll show the last cursor as active
    // In a real implementation, you'd need to extend CodeMirror or use overlays
}

/**
 * Update multi-select status bar
 */
function updateMultiSelectStatus() {
    if (!statusBar) return;
    
    const posEl = statusBar.querySelector('.status-pos');
    const modeEl = statusBar.querySelector('.status-mode');
    
    if (posEl) {
        posEl.textContent = `多选: ${multiCursors.length} 个光标`;
    }
    
    if (modeEl) {
        modeEl.textContent = multiSelectMode ? '多选模式' : getModeLabel(currentMode);
    }
}

/**
 * Check if currently in multi-select mode
 */
function isMultiSelectMode() {
    return multiSelectMode;
}

/**
 * Get current multi-cursor positions
 */
function getMultiCursors() {
    return [...multiCursors];
}

/**
 * Show notification to user
 */
function showNotification(message) {
    // Create a simple notification element
    const notification = document.createElement('div');
    notification.className = 'multi-select-notification';
    notification.textContent = message;
    notification.style.cssText = `
        position: fixed;
        top: 20px;
        right: 20px;
        background: #333;
        color: white;
        padding: 10px 15px;
        border-radius: 5px;
        font-size: 14px;
        z-index: 10000;
        box-shadow: 0 2px 10px rgba(0,0,0,0.3);
    `;
    
    document.body.appendChild(notification);
    
    // Auto-remove after 3 seconds
    setTimeout(() => {
        if (notification.parentNode) {
            notification.parentNode.removeChild(notification);
        }
    }, 3000);
}

// Also expose as window.EditorManager for external access
window.EditorManager = EditorManager;
