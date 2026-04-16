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

    // ── Config ─────────────────────────────────────────────────────
    const config = {
        fontSize: 12,
        tabSize: 4,
        indentUnit: 4,
        indentWithTabs: false,
        lineWrapping: false,
        theme: 'dracula'
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

            // Gutters: line numbers + code folding
            gutters: ['CodeMirror-linenumbers', 'CodeMirror-foldgutter'],

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
                }
            }
        });

        // Apply initial font size
        applyFontSize(config.fontSize);

        // Create status bar
        createStatusBar();

        // ── Event Listeners ────────────────────────────────────────

        // Track cursor position
        editor.on('cursorActivity', () => {
            updateCursorPos();
        });

        // Track changes for dirty state
        editor.on('change', () => {
            if (!dirty) {
                markDirty();
            }
            // Live markdown preview update
            if (mdPreviewMode && isMarkdownFile()) {
                clearTimeout(window._mdPreviewTimer);
                window._mdPreviewTimer = setTimeout(renderMarkdownPreview, 300);
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

        // Markdown preview toggle
        const mdToggleBtn = document.getElementById('btn-md-toggle');
        if (mdToggleBtn) {
            mdToggleBtn.addEventListener('click', toggleMarkdownPreview);
        }

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

        editor.setValue(value);
        editor.clearHistory();
        _historySize = 0;
        markClean();
        editor.scrollTo(scrollInfo.left, scrollInfo.top);

        updateCursorPos();
        updateMarkdownButton();

        // Re-render markdown preview if active
        if (mdPreviewMode && isMarkdownFile()) {
            renderMarkdownPreview();
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
    }

    /**
     * Mark the editor as dirty (unsaved changes present)
     */
    function markDirty() {
        dirty = true;
        updateTitle();
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

    // ── Search & Replace ───────────────────────────────────────────

    /**
     * Open the CodeMirror search dialog
     * @param {string} [query] - initial search query
     */
    function search(query) {
        if (!editor) return;
        if (typeof editor.execCommand === 'function') {
            if (query) {
                // Set search query then open dialog
                editor.execCommand('find');
                const searchInput = editor.getWrapperElement().querySelector('.CodeMirror-search-field');
                if (searchInput) {
                    searchInput.value = query;
                    searchInput.focus();
                }
            } else {
                editor.execCommand('find');
            }
        }
    }

    /**
     * Open the CodeMirror replace dialog
     * @param {string} [query] - search query
     * @param {string} [replacement] - replacement text
     */
    function replace(query, replacement) {
        if (!editor) return;
        if (typeof editor.execCommand === 'function') {
            editor.execCommand('replace');
            if (query) {
                const inputs = editor.getWrapperElement().querySelectorAll('.CodeMirror-dialog input');
                if (inputs.length >= 1) {
                    inputs[0].value = query;
                }
                if (inputs.length >= 2 && replacement) {
                    inputs[1].value = replacement;
                }
            }
        }
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

        const content = editor.getValue();
        if (typeof marked !== 'undefined') {
            previewEl.innerHTML = marked.parse(content, { breaks: true, gfm: true });
        } else {
            previewEl.innerHTML = '<p style="color:var(--text-muted)">Markdown 渲染器未加载</p>';
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
            renderMarkdownPreview();
            if (cmWrapper) cmWrapper.style.display = 'none';
            if (previewEl) previewEl.style.display = '';
            if (toggleBtn) { toggleBtn.textContent = '📝'; toggleBtn.title = '切换编辑'; }
        } else {
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
    }

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
        replace,

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
        renderMarkdownPreview
    };
})();

// Also expose as window.EditorManager for external access
window.EditorManager = EditorManager;
