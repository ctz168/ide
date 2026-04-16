/* PhoneIDE - Main Application Entry */
// ── App Manager ──
const AppManager = (() => {
    let initialized = false;

    // ── Toast Notification ──
    function showToast(message, type = 'info', duration = 2500) {
        const toast = document.getElementById('toast');
        if (!toast) return;
        toast.textContent = message;
        toast.className = 'show';
        if (type) toast.classList.add(type);
        clearTimeout(toast._timer);
        toast._timer = setTimeout(() => {
            toast.className = 'hidden';
        }, duration);
    }
    window.showToast = showToast;

    // ── Dialog ──
    function showDialog(title, bodyHTML, buttons = []) {
        return new Promise((resolve) => {
            const overlay = document.getElementById('dialog-overlay');
            const dialogTitle = document.getElementById('dialog-title');
            const dialogBody = document.getElementById('dialog-body');
            const dialogButtons = document.getElementById('dialog-buttons');

            dialogTitle.textContent = title;
            dialogBody.innerHTML = bodyHTML;
            dialogButtons.innerHTML = '';

            buttons.forEach(btn => {
                const el = document.createElement('button');
                el.textContent = btn.text;
                el.className = btn.class || '';
                const handleBtn = () => {
                    overlay.classList.add('hidden');
                    const input = dialogBody.querySelector('input, textarea, select');
                    resolve({ confirmed: btn.value === 'ok', value: input ? input.value : undefined });
                };
                // Use bindTouchButton for dialog buttons too (Android WebView fix)
                if (window.bindTouchButton) {
                    bindTouchButton(el, handleBtn);
                } else {
                    el.onclick = handleBtn;
                }
                dialogButtons.appendChild(el);
            });

            overlay.classList.remove('hidden');

            // Focus first input
            setTimeout(() => {
                const input = dialogBody.querySelector('input, textarea');
                if (input) input.focus();
            }, 150);

            // Close on overlay click
            const closeOverlay = (e) => {
                if (e.target === overlay) {
                    overlay.classList.add('hidden');
                    resolve({ confirmed: false });
                }
            };
            overlay.onclick = closeOverlay;
            // Also handle touchend on overlay for Android WebView
            overlay.addEventListener('touchend', (e) => {
                if (e.target === overlay) {
                    e.preventDefault();
                    closeOverlay(e);
                }
            });
        });
    }

    function showPromptDialog(title, placeholder = '', defaultValue = '', callback) {
        const promise = showDialog(title,
            `<input type="text" placeholder="${escapeHTML(placeholder)}" value="${escapeHTML(defaultValue)}" autocomplete="off">`,
            [
                { text: '取消', value: 'cancel', class: 'btn-cancel' },
                { text: '确定', value: 'ok', class: 'btn-confirm' },
            ]
        );
        // Support callback pattern for FileManager/GitManager
        if (typeof callback === 'function') {
            promise.then(result => {
                callback(result.confirmed ? (result.value || '') : null);
            });
        }
        return promise;
    }

    function showConfirmDialog(title, message, callback) {
        const promise = showDialog(title,
            `<p style="color:var(--text-secondary);font-size:13px;line-height:1.5;">${escapeHTML(message)}</p>`,
            [
                { text: '取消', value: 'cancel', class: 'btn-cancel' },
                { text: '确定', value: 'ok', class: 'btn-confirm' },
            ]
        );
        // Support callback pattern for FileManager/GitManager
        if (typeof callback === 'function') {
            promise.then(result => {
                callback(result.confirmed);
            });
        }
        return promise;
    }

    /**
     * Choice dialog - show a list of options for user to select
     * Supports callback pattern: showChoiceDialog(title, label, options, resolve)
     */
    function showChoiceDialog(title, label, options, callback) {
        const promise = new Promise((resolve) => {
            const overlay = document.getElementById('dialog-overlay');
            const dialogTitle = document.getElementById('dialog-title');
            const dialogBody = document.getElementById('dialog-body');
            const dialogButtons = document.getElementById('dialog-buttons');

            dialogTitle.textContent = title;
            let html = `<p style="color:var(--text-secondary);font-size:12px;margin-bottom:8px;">${escapeHTML(label)}</p>`;
            options.forEach(opt => {
                const val = (opt.value !== undefined) ? opt.value : opt;
                const lbl = opt.label || opt.value || opt;
                html += `<button class="choice-option" data-value="${escapeAttr(String(val))}" style="display:block;width:100%;padding:10px 12px;margin:4px 0;border:1px solid var(--border);background:var(--bg-surface);color:var(--text-primary);border-radius:var(--radius-sm);font-size:13px;text-align:left;cursor:pointer;font-family:var(--font-mono);">${escapeHTML(String(lbl))}</button>`;
            });
            dialogBody.innerHTML = html;
            dialogButtons.innerHTML = '';
            const cancelBtn = document.createElement('button');
            cancelBtn.textContent = '取消';
            cancelBtn.className = 'btn-cancel';
            const cancelChoice = () => { overlay.classList.add('hidden'); resolve(null); };
            if (window.bindTouchButton) {
                bindTouchButton(cancelBtn, cancelChoice);
            } else {
                cancelBtn.onclick = cancelChoice;
            }
            dialogButtons.appendChild(cancelBtn);

            overlay.classList.remove('hidden');

            // Bind choice clicks
            dialogBody.querySelectorAll('.choice-option').forEach(btn => {
                const handleChoice = () => {
                    const chosen = btn.dataset.value;
                    overlay.classList.add('hidden');
                    resolve(chosen);
                };
                if (window.bindTouchButton) {
                    bindTouchButton(btn, handleChoice);
                } else {
                    btn.addEventListener('click', handleChoice);
                }
                btn.addEventListener('touchstart', () => { btn.style.background = 'var(--bg-hover)'; }, { passive: true });
                btn.addEventListener('touchend', () => { btn.style.background = 'var(--bg-surface)'; }, { passive: true });
            });

            overlay.onclick = (e) => {
                if (e.target === overlay) { overlay.classList.add('hidden'); resolve(null); }
            };
        });
        // Support callback pattern for GitManager
        if (typeof callback === 'function') {
            promise.then(value => callback(value));
        }
        return promise;
    }

    function showInputDialog(title, fields) {
        // fields: [{name, label, type, placeholder, value, options}]
        let html = '';
        fields.forEach(f => {
            html += `<label style="display:block;font-size:12px;color:var(--text-secondary);margin-top:8px;">${escapeHTML(f.label)}</label>`;
            if (f.type === 'select' && f.options) {
                html += `<select name="${f.name}" style="width:100%;padding:8px;border:1px solid var(--border);background:var(--bg-tertiary);color:var(--text-primary);border-radius:var(--radius-sm);font-size:13px;margin-top:4px;">`;
                f.options.forEach(opt => {
                    const sel = opt.value === f.value ? ' selected' : '';
                    html += `<option value="${escapeHTML(opt.value)}"${sel}>${escapeHTML(opt.label || opt.value)}</option>`;
                });
                html += '</select>';
            } else if (f.type === 'textarea') {
                html += `<textarea name="${f.name}" placeholder="${escapeHTML(f.placeholder || '')}" rows="${f.rows || 3}" style="width:100%;padding:8px;border:1px solid var(--border);background:var(--bg-tertiary);color:var(--text-primary);border-radius:var(--radius-sm);font-size:13px;font-family:var(--font-mono);resize:vertical;margin-top:4px;">${escapeHTML(f.value || '')}</textarea>`;
            } else {
                html += `<input type="${f.type || 'text'}" name="${f.name}" placeholder="${escapeHTML(f.placeholder || '')}" value="${escapeHTML(f.value || '')}" style="width:100%;padding:8px;border:1px solid var(--border);background:var(--bg-tertiary);color:var(--text-primary);border-radius:var(--radius-sm);font-size:13px;font-family:var(--font-mono);margin-top:4px;" autocomplete="off">`;
            }
        });
        return showDialog(title, html, [
            { text: '取消', value: 'cancel', class: 'btn-cancel' },
            { text: '确定', value: 'ok', class: 'btn-confirm' },
        ]).then(result => {
            if (!result.confirmed) return null;
            const body = document.getElementById('dialog-body');
            const values = {};
            fields.forEach(f => {
                const el = body.querySelector(`[name="${f.name}"]`);
                values[f.name] = el ? el.value : '';
            });
            return values;
        });
    }

    window.showPromptDialog = showPromptDialog;
    window.showConfirmDialog = showConfirmDialog;
    window.showChoiceDialog = showChoiceDialog;
    window.showInputDialog = showInputDialog;
    window.showDialog = showDialog;

    // ── Touch-Friendly Button Binding (Android WebView fix) ──
    // In Android WebView, click events are unreliable on buttons inside
    // transform-animated sidebar panels. We use touchend as the primary
    // trigger with a flag to prevent the synthesized click from double-firing.
    function bindTouchButton(btn, handler) {
        if (!btn) return;
        let startTouch = null;
        let touchHandled = false;

        btn.addEventListener('touchstart', (e) => {
            touchHandled = false;
            startTouch = { x: e.touches[0].clientX, y: e.touches[0].clientY };
        }, { passive: true });

        btn.addEventListener('touchmove', () => {
            startTouch = null; // moved = scroll, not tap
        }, { passive: true });

        btn.addEventListener('touchend', (e) => {
            if (!startTouch) return; // was scrolling
            touchHandled = true;
            e.preventDefault(); // prevent browser from synthesizing a click event
            handler(e);
        });

        // Fallback click for non-touch devices (mouse/keyboard)
        btn.addEventListener('click', (e) => {
            if (touchHandled) {
                touchHandled = false; // reset for next interaction
                return; // already handled by touchend
            }
            handler(e);
        });
    }
    window.bindTouchButton = bindTouchButton;

    // ── Utility Functions ──
    function escapeHTML(str) {
        const div = document.createElement('div');
        div.textContent = str;
        return div.innerHTML;
    }
    window.escapeHTML = escapeHTML;

    function escapeAttr(str) {
        return str.replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/'/g, '&#39;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    }
    window.escapeAttr = escapeAttr;

    function debounce(fn, delay) {
        let timer;
        return function (...args) {
            clearTimeout(timer);
            timer = setTimeout(() => fn.apply(this, args), delay);
        };
    }
    window.debounce = debounce;

    function formatFileSize(bytes) {
        if (bytes === 0) return '';
        if (bytes < 1024) return bytes + 'B';
        if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + 'K';
        return (bytes / (1024 * 1024)).toFixed(1) + 'M';
    }
    window.formatFileSize = formatFileSize;

    function formatTime(isoString) {
        if (!isoString) return '';
        const d = new Date(isoString);
        return d.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' });
    }
    window.formatTime = formatTime;

    // ── Sidebar Management ──
    const sidebar = {
        left: { el: null, open: false },
        right: { el: null, open: false },
        overlay: null,
        touchStartX: 0,
        touchStartY: 0,
        swiping: false,
    };

    function initSidebars() {
        sidebar.left.el = document.getElementById('sidebar-left');
        sidebar.right.el = document.getElementById('sidebar-right');
        sidebar.overlay = document.getElementById('overlay');

        // Left sidebar toggle
        document.getElementById('btn-menu').addEventListener('click', () => toggleSidebar('left'));
        document.getElementById('close-left').addEventListener('click', () => closeSidebar('left'));

        // Right sidebar toggle
        document.getElementById('btn-chat').addEventListener('click', () => toggleSidebar('right'));
        document.getElementById('close-right').addEventListener('click', () => closeSidebar('right'));

        // Overlay click closes sidebars
        sidebar.overlay.addEventListener('click', () => {
            closeSidebar('left');
            closeSidebar('right');
        });

        // Swipe gestures on main area
        const mainArea = document.getElementById('main-area');
        mainArea.addEventListener('touchstart', onSwipeStart, { passive: true });
        mainArea.addEventListener('touchend', onSwipeEnd, { passive: true });
    }

    function toggleSidebar(side) {
        if (sidebar[side].open) {
            closeSidebar(side);
        } else {
            openSidebar(side);
        }
    }

    function openSidebar(side) {
        // Close other sidebar first
        if (side === 'left' && sidebar.right.open) closeSidebar('right');
        if (side === 'right' && sidebar.left.open) closeSidebar('left');

        sidebar[side].el.classList.add('open');
        sidebar[side].open = true;
        sidebar.overlay.classList.remove('hidden');

        // Refresh editor when sidebar opens/closes (delayed for animation)
        setTimeout(() => {
            if (window.EditorManager) EditorManager.resize();
        }, 300);
    }

    function closeSidebar(side) {
        sidebar[side].el.classList.remove('open');
        sidebar[side].open = false;
        if (!sidebar.left.open && !sidebar.right.open) {
            sidebar.overlay.classList.add('hidden');
        }
        setTimeout(() => {
            if (window.EditorManager) EditorManager.resize();
        }, 300);
    }

    function onSwipeStart(e) {
        const touch = e.touches[0];
        sidebar.touchStartX = touch.clientX;
        sidebar.touchStartY = touch.clientY;
        sidebar.swiping = true;
    }

    function onSwipeEnd(e) {
        if (!sidebar.swiping) return;
        sidebar.swiping = false;

        const touch = e.changedTouches[0];
        const dx = touch.clientX - sidebar.touchStartX;
        const dy = touch.clientY - sidebar.touchStartY;
        const width = window.innerWidth;

        // Only handle horizontal swipes
        if (Math.abs(dx) < 50 || Math.abs(dy) > Math.abs(dx)) return;

        // Swipe from left edge -> open left sidebar
        if (dx > 0 && sidebar.touchStartX < 30 && !sidebar.left.open) {
            openSidebar('left');
            return;
        }

        // Swipe from right edge -> open right sidebar
        if (dx < 0 && sidebar.touchStartX > width - 30 && !sidebar.right.open) {
            openSidebar('right');
            return;
        }

        // Swipe left on open left sidebar -> close
        if (dx < -60 && sidebar.left.open) {
            closeSidebar('left');
            return;
        }

        // Swipe right on open right sidebar -> close
        if (dx > 60 && sidebar.right.open) {
            closeSidebar('right');
            return;
        }
    }

    // ── Left Tab Management ──
    function initTabs() {
        const tabs = document.querySelectorAll('#left-tabs .tab');
        tabs.forEach(tab => {
            tab.addEventListener('click', () => {
                tabs.forEach(t => t.classList.remove('active'));
                tab.classList.add('active');
                const target = tab.dataset.tab;
                document.querySelectorAll('#left-panels .panel').forEach(p => p.classList.remove('active'));
                document.getElementById(`panel-${target}`).classList.add('active');
            });
        });
    }

    // ── Keyboard Shortcuts ──
    function initKeyboard() {
        document.addEventListener('keydown', (e) => {
            // Ctrl/Cmd + S - Save
            if ((e.ctrlKey || e.metaKey) && e.key === 's') {
                e.preventDefault();
                if (window.FileManager) FileManager.saveFile();
            }
            // Ctrl/Cmd + Shift + P - Command palette (placeholder)
            // Escape - close sidebars
            if (e.key === 'Escape') {
                closeSidebar('left');
                closeSidebar('right');
                const dialog = document.getElementById('dialog-overlay');
                if (dialog) dialog.classList.add('hidden');
            }
        });
    }

    // ── Bottom Panel ──
    function initBottomPanel() {
        const panel = document.getElementById('bottom-panel');
        const closeBtn = document.getElementById('bottom-panel-close');
        const clearBtn = document.getElementById('bottom-panel-clear');

        if (closeBtn) {
            closeBtn.addEventListener('click', () => {
                if (window.TerminalManager) TerminalManager.hidePanel();
            });
        }
        if (clearBtn) {
            clearBtn.addEventListener('click', () => {
                if (window.TerminalManager) TerminalManager.clearOutput();
            });
        }

        // Toggle bottom panel button in toolbar (add if not present)
        addBottomToggle();
    }

    function addBottomToggle() {
        const toolbar = document.getElementById('toolbar-actions');
        if (!toolbar) return;

        // Guard against duplicate buttons
        if (document.getElementById('btn-toggle-output')) return;

        // Add toggle button before chat button
        const toggleBtn = document.createElement('button');
        toggleBtn.id = 'btn-toggle-output';
        toggleBtn.title = '输出面板';
        toggleBtn.textContent = '🖥';
        toggleBtn.style.cssText = 'color:var(--teal);';
        toggleBtn.addEventListener('click', () => {
            if (window.TerminalManager) TerminalManager.togglePanel();
        });

        const chatBtn = document.getElementById('btn-chat');
        toolbar.insertBefore(toggleBtn, chatBtn);
    }

    // ── Editor Toolbar ──
    function initEditorToolbar() {
        const searchBtn = document.getElementById('editor-search-btn');
        const searchInput = document.getElementById('editor-search');
        const replaceInput = document.getElementById('editor-replace');

        if (searchBtn) {
            searchBtn.addEventListener('click', () => {
                if (window.EditorManager) {
                    // Directly open CodeMirror's built-in find dialog
                    EditorManager.search();
                }
            });
        }

        // Keyboard shortcut in search input - trigger CodeMirror find
        if (searchInput) {
            searchInput.addEventListener('keydown', (e) => {
                if (e.key === 'Enter') {
                    e.preventDefault();
                    const q = searchInput.value.trim();
                    if (q && window.EditorManager) {
                        EditorManager.search(q);
                    }
                }
                if (e.key === 'Escape') {
                    searchInput.style.display = 'none';
                    searchInput.value = '';
                    replaceInput.style.display = 'none';
                    replaceInput.value = '';
                }
            });
        }

        // Ctrl+F shortcut - focus custom search input or open CM dialog
        document.addEventListener('keydown', (e) => {
            if ((e.ctrlKey || e.metaKey) && e.key === 'f') {
                e.preventDefault();
                if (window.EditorManager) {
                    EditorManager.search();
                }
            }
        });
    }

    // ── Toolbar Buttons ──
    function initToolbar() {
        // Undo
        document.getElementById('btn-undo').addEventListener('click', () => {
            if (window.EditorManager) EditorManager.undo();
        });
        // Redo
        document.getElementById('btn-redo').addEventListener('click', () => {
            if (window.EditorManager) EditorManager.redo();
        });
        // Save
        document.getElementById('btn-save').addEventListener('click', () => {
            if (window.FileManager) FileManager.saveFile();
        });
        // Run
        document.getElementById('btn-run').addEventListener('click', async () => {
            if (window.TerminalManager) {
                let filePath = window.EditorManager ? EditorManager.getCurrentFile() : '';
                const compiler = document.getElementById('compiler-select');
                const compilerVal = compiler ? compiler.value : 'python3';
                
                if (filePath) {
                    TerminalManager.execute(filePath, compilerVal);
                } else {
                    // Show file picker to select a file to run
                    try {
                        const listResp = await fetch('/api/files/list');
                        if (listResp.ok) {
                            const listData = await listResp.json();
                            const items = listData.items || [];
                            const files = items.filter(i => !i.is_dir && i.is_dir !== true);
                            if (files.length > 0) {
                                const options = files.map(f => ({
                                    label: f.name,
                                    value: f.path
                                }));
                                const chosen = await showChoiceDialog('选择运行文件', '选择要运行的文件:', options);
                                if (chosen) {
                                    TerminalManager.execute(chosen, compilerVal);
                                }
                            } else {
                                const code = window.EditorManager ? EditorManager.getContent() : '';
                                if (code && code.trim()) {
                                    TerminalManager.executeCode(code, compilerVal);
                                } else {
                                    showToast('没有找到可运行的文件', 'warning');
                                }
                            }
                        }
                    } catch (err) {
                        showToast('获取文件列表失败', 'error');
                    }
                }
            }
        });
        // Stop
        document.getElementById('btn-stop').addEventListener('click', () => {
            if (window.TerminalManager) TerminalManager.stop();
        });
    }

    // ── File Panel Toolbar Buttons ──
    function initFileToolbar() {
        // Open Folder button
        const openFolderBtn = document.getElementById('btn-open-folder');
        if (openFolderBtn) {
            bindTouchButton(openFolderBtn, async () => {
                try {
                    const result = await showPromptDialog('打开文件夹', '输入文件夹路径:', FileManager ? FileManager.currentPath : '/workspace');
                    if (result) {
                        const resp = await fetch('/api/files/open_folder', {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ path: result })
                        });
                        if (!resp.ok) {
                            const err = await resp.json().catch(() => ({}));
                            throw new Error(err.error || resp.statusText);
                        }
                        const data = await resp.json();
                        if (data.workspace) {
                            document.getElementById('workspace-path').value = data.workspace;
                            if (window.FileManager) await FileManager.loadFileList();
                            showToast('工作区已切换', 'success');
                        }
                    }
                } catch (err) {
                    showToast('打开文件夹失败: ' + err.message, 'error');
                }
            });
        }

        // New File button
        const newFileBtn = document.getElementById('btn-new-file');
        if (newFileBtn) {
            bindTouchButton(newFileBtn, async () => {
                try {
                    if (window.FileManager) await FileManager.createFile();
                    else showToast('FileManager 未就绪', 'error');
                } catch (err) {
                    showToast('新建文件失败: ' + err.message, 'error');
                }
            });
        }

        // New Folder button
        const newFolderBtn = document.getElementById('btn-new-folder');
        if (newFolderBtn) {
            bindTouchButton(newFolderBtn, async () => {
                try {
                    if (window.FileManager) await FileManager.createFolder();
                    else showToast('FileManager 未就绪', 'error');
                } catch (err) {
                    showToast('新建文件夹失败: ' + err.message, 'error');
                }
            });
        }
    }

    // ── Venv Buttons ──
    function initVenv() {
        const venvBtn = document.getElementById('venv-btn');
        const createVenvBtn = document.getElementById('create-venv-btn');
        const installPkgBtn = document.getElementById('install-pkg-btn');

        if (venvBtn) {
            venvBtn.addEventListener('click', () => {
                if (window.TerminalManager && TerminalManager.loadVenvInfo) {
                    TerminalManager.loadVenvInfo().then(() => {
                        showToast('虚拟环境信息已刷新', 'info', 1500);
                    });
                }
            });
        }
        if (createVenvBtn) {
            createVenvBtn.addEventListener('click', () => {
                if (window.TerminalManager && TerminalManager.createVenv) {
                    TerminalManager.createVenv();
                }
            });
        }
        if (installPkgBtn) {
            installPkgBtn.addEventListener('click', () => {
                if (window.TerminalManager && TerminalManager.installPackage) {
                    TerminalManager.installPackage();
                }
            });
        }
        // Import requirements button
        const importReqBtn = document.getElementById('import-req-btn');
        if (importReqBtn) {
            importReqBtn.addEventListener('click', () => {
                if (window.TerminalManager && TerminalManager.importRequirements) {
                    TerminalManager.importRequirements();
                }
            });
        }
    }

    // ── Auto Save ──
    function initAutoSave() {
        let saveTimer = null;
        document.addEventListener('editor:change', () => {
            clearTimeout(saveTimer);
            saveTimer = setTimeout(() => {
                if (window.EditorManager && EditorManager.isDirty() && window.FileManager) {
                    // Only auto-save if file was previously saved
                    const currentFile = EditorManager.getCurrentFile();
                    if (currentFile) {
                        FileManager.saveFile().then(() => {
                            showToast('已自动保存', 'success', 1000);
                        }).catch(() => {});
                    }
                }
            }, 5000);
        });
    }

    // ── Window Resize ──
    function initResize() {
        window.addEventListener('resize', debounce(() => {
            if (window.EditorManager) EditorManager.resize();
        }, 200));

        // Handle visual viewport changes (mobile keyboard)
        if (window.visualViewport) {
            window.visualViewport.addEventListener('resize', debounce(() => {
                if (window.EditorManager) EditorManager.resize();
            }, 100));
        }
    }

    // ── Theme Management ──
    let currentTheme = 'dark';
    const sunIcon = `<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="5"/><line x1="12" y1="1" x2="12" y2="3"/><line x1="12" y1="21" x2="12" y2="23"/><line x1="4.22" y1="4.22" x2="5.64" y2="5.64"/><line x1="18.36" y1="18.36" x2="19.78" y2="19.78"/><line x1="1" y1="12" x2="3" y2="12"/><line x1="21" y1="12" x2="23" y2="12"/><line x1="4.22" y1="19.78" x2="5.64" y2="18.36"/><line x1="18.36" y1="5.64" x2="19.78" y2="4.22"/></svg>`;
    const moonIcon = `<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/></svg>`;

    function initTheme() {
        const toolbar = document.getElementById('toolbar-actions');
        if (!toolbar) return;

        const themeBtn = document.createElement('button');
        themeBtn.id = 'btn-theme';
        themeBtn.title = '切换主题';
        themeBtn.innerHTML = moonIcon;

        themeBtn.addEventListener('click', () => {
            setTheme(currentTheme === 'dark' ? 'claude' : 'dark');
        });

        toolbar.insertBefore(themeBtn, toolbar.firstChild);
    }

    function setTheme(themeId) {
        currentTheme = themeId;
        const btn = document.getElementById('btn-theme');
        if (btn) btn.innerHTML = themeId === 'dark' ? moonIcon : sunIcon;

        if (themeId === 'dark') {
            document.documentElement.removeAttribute('data-theme');
        } else {
            document.documentElement.setAttribute('data-theme', themeId);
        }

        // Persist to localStorage
        try { localStorage.setItem('theme', themeId); } catch (e) {}

        // Also save to server config
        fetch('/api/config', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ theme: themeId }),
        }).then(r => r.json()).catch(() => {});

        // Refresh CodeMirror
        if (window.EditorManager && EditorManager.getEditor) {
            const ed = EditorManager.getEditor();
            if (ed) ed.refresh();
        }
    }

    function loadTheme() {
        // Priority: localStorage > server config > default (dark)
        let saved = null;
        try { saved = localStorage.getItem('theme'); } catch (e) {}

        if (saved && (saved === 'dark' || saved === 'claude')) {
            setTheme(saved);
        } else {
            // Fall back to server config
            fetch('/api/config')
                .then(r => r.json())
                .then(config => {
                    if (config.theme && (config.theme === 'dark' || config.theme === 'claude')) {
                        setTheme(config.theme);
                    } else {
                        setTheme('dark');
                    }
                })
                .catch(() => setTheme('dark'));
        }
    }

    // ── Server Management ──
    let serverStatusTimer = null;
    let logViewerEventSource = null;
    let logViewerOpen = false;

    function initServerManagement() {
        // Wire up server management bar buttons
        const restartBtn = document.getElementById('btn-server-restart');
        const logsBtn = document.getElementById('btn-server-logs');
        const updatesBtn = document.getElementById('btn-check-updates');

        if (restartBtn) {
            restartBtn.addEventListener('click', () => restartServer());
        }
        if (logsBtn) {
            logsBtn.addEventListener('click', () => toggleLogViewer());
        }
        if (updatesBtn) {
            updatesBtn.addEventListener('click', () => checkUpdates());
        }

        // Log viewer panel buttons
        const logCloseBtn = document.getElementById('log-viewer-close');
        const logClearBtn = document.getElementById('log-viewer-clear');
        const logRefreshBtn = document.getElementById('log-viewer-refresh');

        if (logCloseBtn) {
            logCloseBtn.addEventListener('click', () => toggleLogViewer());
        }
        if (logClearBtn) {
            logClearBtn.addEventListener('click', () => {
                const content = document.getElementById('log-viewer-content');
                if (content) content.textContent = '';
            });
        }
        if (logRefreshBtn) {
            logRefreshBtn.addEventListener('click', () => {
                const content = document.getElementById('log-viewer-content');
                if (content) content.textContent = '';
                connectLogViewerSSE();
            });
        }

        // Update dialog buttons
        const updateCheckBtn = document.getElementById('update-check-btn');
        const updateApplyBtn = document.getElementById('update-apply-btn');
        const updateCloseBtn = document.getElementById('update-close-btn');
        const updateSaveTokenBtn = document.getElementById('update-save-token');

        if (updateCheckBtn) {
            updateCheckBtn.addEventListener('click', () => checkUpdates());
        }
        if (updateApplyBtn) {
            updateApplyBtn.addEventListener('click', () => applyUpdate());
        }
        const updateDiagnoseBtn = document.getElementById('update-diagnose-btn');
        if (updateDiagnoseBtn) {
            updateDiagnoseBtn.addEventListener('click', () => diagnoseUpdate());
        }
        if (updateCloseBtn) {
            updateCloseBtn.addEventListener('click', () => {
                document.getElementById('update-dialog-overlay').classList.add('hidden');
            });
        }
        // Save GitHub token to server config
        if (updateSaveTokenBtn) {
            updateSaveTokenBtn.addEventListener('click', async () => {
                const input = document.getElementById('update-github-token');
                if (!input) return;
                const token = input.value.trim();
                try {
                    const resp = await fetch('/api/config', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ github_token: token })
                    });
                    if (resp.ok) {
                        showToast('Token 已保存', 'success', 2000);
                    } else {
                        showToast('保存失败', 'error', 2000);
                    }
                } catch (e) {
                    showToast('保存失败: ' + e.message, 'error', 2000);
                }
            });
        }

        // Close update dialog on overlay click
        const updateOverlay = document.getElementById('update-dialog-overlay');
        if (updateOverlay) {
            updateOverlay.addEventListener('click', (e) => {
                if (e.target === updateOverlay) {
                    updateOverlay.classList.add('hidden');
                }
            });
        }

        // Start polling server status
        pollServerStatus();
        serverStatusTimer = setInterval(pollServerStatus, 10000);

        // Close log viewer + update dialog on Escape
        const origKeyHandler = document.onkeydown;
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape') {
                if (logViewerOpen) toggleLogViewer();
                const updateDialog = document.getElementById('update-dialog-overlay');
                if (updateDialog && !updateDialog.classList.contains('hidden')) {
                    updateDialog.classList.add('hidden');
                }
            }
        });
    }

    /**
     * Poll /api/server/status and update the indicator
     */
    async function pollServerStatus() {
        const dot = document.getElementById('server-status-dot');
        const text = document.getElementById('server-status-text');
        if (!dot || !text) return;

        try {
            const resp = await fetch('/api/server/status');
            if (!resp.ok) throw new Error('Server unreachable');

            const data = await resp.json();
            const running = data.status === 'running' || data.running === true;

            dot.className = 'status-dot ' + (running ? 'running' : 'stopped');
            dot.title = running ? 'Server running' : 'Server stopped';
            text.textContent = running
                ? (data.uptime ? `Running (${formatUptime(data.uptime)})` : 'Running')
                : 'Stopped';
        } catch (err) {
            dot.className = 'status-dot stopped';
            dot.title = 'Server unreachable';
            text.textContent = 'Unreachable';
        }
    }

    /**
     * Format uptime seconds into human-readable string
     */
    function formatUptime(seconds) {
        if (!seconds || seconds < 0) return '';
        if (seconds < 60) return Math.round(seconds) + 's';
        if (seconds < 3600) return Math.round(seconds / 60) + 'm';
        const h = Math.floor(seconds / 3600);
        const m = Math.round((seconds % 3600) / 60);
        return h + 'h' + (m > 0 ? m + 'm' : '');
    }

    /**
     * Restart the server via API, then poll until back online
     */
    async function restartServer() {
        const dot = document.getElementById('server-status-dot');
        const text = document.getElementById('server-status-text');

        if (dot) {
            dot.className = 'status-dot checking';
            text.textContent = 'Restarting...';
        }

        showToast('Restarting server...', 'info', 2000);

        try {
            const resp = await fetch('/api/server/restart', { method: 'POST' });
            if (!resp.ok) {
                const errText = await resp.text().catch(() => 'Unknown error');
                throw new Error(errText);
            }
        } catch (err) {
            showToast('Restart failed: ' + err.message, 'error', 3000);
            if (dot) {
                dot.className = 'status-dot stopped';
                text.textContent = 'Error';
            }
            return;
        }

        // Poll health until back online
        let attempts = 0;
        const maxAttempts = 30; // 30 seconds

        const check = setInterval(async () => {
            attempts++;
            try {
                const resp = await fetch('/api/server/status');
                if (resp.ok) {
                    const data = await resp.json();
                    const running = data.status === 'running' || data.running === true;
                    if (running) {
                        clearInterval(check);
                        if (dot) {
                            dot.className = 'status-dot running';
                            text.textContent = 'Running';
                        }
                        showToast('Server restarted successfully', 'success', 2000);
                        pollServerStatus();
                    }
                }
            } catch (_) {
                // Still waiting
            }

            if (attempts >= maxAttempts) {
                clearInterval(check);
                if (dot) {
                    dot.className = 'status-dot stopped';
                    text.textContent = 'Timeout';
                }
                showToast('Server restart timed out', 'error', 3000);
            }
        }, 1000);
    }

    /**
     * Toggle the log viewer panel
     */
    function toggleLogViewer() {
        const panel = document.getElementById('log-viewer-panel');
        if (!panel) return;

        logViewerOpen = !logViewerOpen;

        if (logViewerOpen) {
            panel.classList.remove('hidden');
            connectLogViewerSSE();
        } else {
            panel.classList.add('hidden');
            disconnectLogViewerSSE();
        }
    }

    /**
     * Connect to the SSE log stream endpoint
     */
    function connectLogViewerSSE() {
        disconnectLogViewerSSE();

        const content = document.getElementById('log-viewer-content');
        if (!content) return;

        try {
            logViewerEventSource = new EventSource('/api/server/logs/stream');

            logViewerEventSource.addEventListener('message', (e) => {
                const line = e.data || '';
                if (!line.trim()) return;

                // Auto-scroll if already at bottom
                const atBottom = content.scrollHeight - content.scrollTop - content.clientHeight < 60;

                const lineEl = document.createElement('div');
                lineEl.className = 'log-line';

                // Colorize log levels
                if (line.includes(' ERROR ') || line.includes('[ERROR]')) {
                    lineEl.style.color = 'var(--red, #ff5555)';
                } else if (line.includes(' WARNING ') || line.includes('[WARN]')) {
                    lineEl.style.color = 'var(--yellow, #f1fa8c)';
                } else if (line.includes(' INFO ') || line.includes('[INFO]')) {
                    lineEl.style.color = 'var(--text-secondary, #aaa)';
                } else if (line.includes(' DEBUG ') || line.includes('[DEBUG]')) {
                    lineEl.style.color = 'var(--text-muted, #666)';
                }

                lineEl.textContent = line;
                content.appendChild(lineEl);

                // Keep max 2000 lines
                while (content.children.length > 2000) {
                    content.removeChild(content.firstChild);
                }

                if (atBottom) {
                    content.scrollTop = content.scrollHeight;
                }
            });

            logViewerEventSource.onerror = () => {
                // EventSource auto-reconnects, but we can show status
                console.warn('Log SSE connection error, will retry...');
            };

        } catch (err) {
            console.warn('Failed to connect log SSE:', err.message);
            // Fallback: no real-time logs
            const lineEl = document.createElement('div');
            lineEl.style.color = 'var(--text-muted, #666)';
            lineEl.style.fontStyle = 'italic';
            lineEl.textContent = 'Log streaming unavailable: ' + err.message;
            content.appendChild(lineEl);
        }
    }

    /**
     * Disconnect the log viewer SSE connection
     */
    function disconnectLogViewerSSE() {
        if (logViewerEventSource) {
            logViewerEventSource.close();
            logViewerEventSource = null;
        }
    }

    /**
     * Check for updates via API and show update dialog
     */
    async function checkUpdates() {
        const overlay = document.getElementById('update-dialog-overlay');
        const statusEl = document.getElementById('update-status');
        const infoEl = document.getElementById('update-info');
        const versionEl = document.getElementById('update-current-version');
        const applyBtn = document.getElementById('update-apply-btn');
        const checkBtn = document.getElementById('update-check-btn');

        if (!overlay) return;

        // Show dialog
        overlay.classList.remove('hidden');
        if (statusEl) statusEl.textContent = '正在检查更新...';
        if (infoEl) { infoEl.classList.add('hidden'); infoEl.textContent = ''; }
        if (applyBtn) applyBtn.classList.add('hidden');
        if (checkBtn) checkBtn.disabled = true;

        // Load saved GitHub token (masked)
        try {
            const cfgResp = await fetch('/api/config');
            if (cfgResp.ok) {
                const cfg = await cfgResp.json();
                const tokenInput = document.getElementById('update-github-token');
                if (tokenInput && cfg.github_token) {
                    tokenInput.value = cfg.github_token;
                    tokenInput.placeholder = 'ghp_****' + cfg.github_token.slice(-4);
                }
            }
        } catch (_e) {}

        try {
            const resp = await fetch('/api/update/check', { method: 'POST' });
            if (!resp.ok) {
                const errText = await resp.text().catch(() => 'Unknown error');
                throw new Error(errText);
            }

            const data = await resp.json();

            // Show current version
            if (versionEl) {
                versionEl.textContent = '当前版本: ' + (data.current_version || 'unknown') + '  本地 commit: ' + (data.local_sha || 'unknown');
            }

            if (data.update_available) {
                // Update available
                let info = '';
                if (data.apk_update) {
                    info += 'APK Update Available!\n';
                    info += 'New version: ' + (data.new_version || data.latest_tag || 'unknown') + '\n';
                    info += 'APK size: ' + (data.apk_size_human || 'unknown') + '\n';
                    if (data.release_body) info += '\n' + data.release_body + '\n';
                    // Show "Update Now" button for APK install
                    if (applyBtn) {
                        applyBtn.textContent = '下载安装 APK';
                        applyBtn.classList.remove('hidden');
                        applyBtn.dataset.apkUrl = data.apk_url || '';
                        applyBtn.dataset.version = data.new_version || data.latest_tag || '';
                    }
                } else if (data.code_update) {
                    info += 'Code Update Available\n';
                    info += 'Commits behind: ' + (data.commits_behind || '?') + '\n';
                    if (data.remote_message) info += 'Latest: ' + data.remote_message + '\n';
                    // Show "Update Now" button for code pull
                    if (applyBtn) {
                        applyBtn.textContent = 'Pull & Restart Server';
                        applyBtn.classList.remove('hidden');
                        applyBtn.dataset.apkUrl = '';
                        applyBtn.dataset.version = '';
                    }
                }
                if (statusEl) statusEl.textContent = '有可用更新！';
                if (infoEl && info) {
                    infoEl.textContent = info;
                    infoEl.classList.remove('hidden');
                }
            } else {
                if (statusEl) statusEl.textContent = '代码已是最新';
                if (versionEl) {
                    versionEl.textContent = 'Current version: ' + (data.current_version || data.latest_tag || 'latest');
                }
            }
        } catch (err) {
            if (statusEl) statusEl.textContent = 'Error: ' + err.message;
            if (versionEl) versionEl.textContent = 'Version check failed';
        } finally {
            if (checkBtn) checkBtn.disabled = false;
        }
    }

    /**
     * Apply the pending update via API or trigger APK download
     */
    async function applyUpdate() {
        const statusEl = document.getElementById('update-status');
        const applyBtn = document.getElementById('update-apply-btn');
        const checkBtn = document.getElementById('update-check-btn');

        if (!statusEl) return;
        if (!applyBtn) return;

        const apkUrl = applyBtn.dataset.apkUrl;
        const version = applyBtn.dataset.version;

        // If APK URL is available, trigger native APK download and install
        if (apkUrl && version) {
            if (typeof window.UpdateBridge !== 'undefined') {
                // Native bridge: download and install via Android
                if (applyBtn) applyBtn.disabled = true;
                if (checkBtn) checkBtn.disabled = true;

                try {
                    statusEl.textContent = '正在下载 APK...';
                    window.UpdateBridge.downloadAndInstallApk(apkUrl, version);
                    statusEl.textContent = 'APK 下载已开始，请按提示安装。';
                } catch (err) {
                    statusEl.textContent = '下载出错: ' + err.message;
                } finally {
                    if (applyBtn) applyBtn.disabled = false;
                    if (checkBtn) checkBtn.disabled = false;
                }
                return;
            } else {
                // Fallback: open APK URL in browser for manual download
                statusEl.textContent = '正在打开下载页面...';
                window.open(apkUrl, '_blank');
                return;
            }
        }

        // Fallback: server-side git pull + restart (for code-only updates)
        if (applyBtn) applyBtn.disabled = true;
        if (checkBtn) checkBtn.disabled = true;

        statusEl.innerHTML = 'Updating server...\n<div class="update-progress-bar"><div class="update-progress-fill" id="update-progress"></div></div>';

        const progressEl = document.getElementById('update-progress');

        try {
            const resp = await fetch('/api/update/apply', { method: 'POST' });
            const respText = await resp.text().catch(() => 'Unknown error');

            if (!resp.ok) {
                // Parse error JSON and show full details
                let errMsg = respText;
                let diagInfo = '';
                try {
                    const errJson = JSON.parse(respText);
                    errMsg = errJson.error || respText;
                    // Show diagnostics if available
                    if (errJson.diagnostics) {
                        const d = errJson.diagnostics;
                        diagInfo = '\n\n── 诊断信息 ──';
                        if (d.SERVER_DIR) diagInfo += `\n目录: ${d.SERVER_DIR}`;
                        if (d.write_test !== undefined) diagInfo += `\n写权限: ${d.write_test ? '✅' : '❌ ' + (d.write_error || '')}`;
                        if (d.write_ok_after_fix !== undefined) diagInfo += `\n修复后写权限: ${d.write_ok_after_fix ? '✅' : '❌'}`;
                        if (d.tmp_writable !== undefined) diagInfo += `\n/tmp写权限: ${d.tmp_writable ? '✅' : '❌'}`;
                        if (d.network_ok !== undefined) diagInfo += `\n网络: ${d.network_ok ? '✅' : '❌ ' + (d.network_error || '')}`;
                        if (d.disk_free_mb !== undefined) diagInfo += `\n剩余空间: ${d.disk_free_mb}MB`;
                    }
                    if (errJson.traceback) {
                        diagInfo += '\n\n── 完整错误 ──\n' + errJson.traceback;
                    }
                } catch (parseErr) {
                    // not JSON, use raw text
                }
                throw new Error(errMsg + diagInfo);
            }

            const data = JSON.parse(respText);
            const method = data.method || 'zip';

            if (progressEl) progressEl.style.width = '100%';
            statusEl.innerHTML = `✅ 更新完成 (${method})! 页面将在几秒后刷新...`;

            showToast(`更新完成, 正在重启...`, 'success', 3000);

            // Reload page after delay
            setTimeout(() => {
                window.location.reload();
            }, 3000);

        } catch (err) {
            statusEl.textContent = '❌ ' + err.message;
            statusEl.style.whiteSpace = 'pre-wrap';
            statusEl.style.wordBreak = 'break-all';
            statusEl.style.fontSize = '12px';
            statusEl.style.maxHeight = '300px';
            statusEl.style.overflowY = 'auto';
            if (progressEl) progressEl.style.width = '0%';
            showToast('更新失败', 'error', 5000);
        } finally {
            if (applyBtn) applyBtn.disabled = false;
            if (checkBtn) checkBtn.disabled = false;
        }
    }

    // ── Diagnose Update Environment ──
    async function diagnoseUpdate() {
        const statusEl = document.getElementById('update-status');
        if (!statusEl) return;

        statusEl.innerHTML = '正在运行诊断...';
        statusEl.style.whiteSpace = 'pre-wrap';
        statusEl.style.fontSize = '11px';

        try {
            const resp = await fetch('/api/update/diagnose');
            if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
            const data = await resp.json();

            let report = '── 更新诊断报告 ──\n\n';
            report += `版本: ${data.APP_VERSION || '?'}\n`;
            report += `进程: PID=${data.pid}, UID=${data.uid}, GID=${data.gid}\n`;
            report += `工作目录: ${data.cwd}\n`;
            report += `HOME: ${data.user_home}\n`;
            report += `临时目录: ${data.tempdir}\n\n`;

            report += `── SERVER_DIR ──\n`;
            report += `路径: ${data.SERVER_DIR}\n`;
            report += `存在: ${data.SERVER_DIR_exists ? '✅' : '❌'}\n`;
            if (data.SERVER_DIR_stat) {
                const s = data.SERVER_DIR_stat;
                report += `权限: ${s.mode} (可写:${s.writable ? '✅' : '❌'} 可读:${s.readable ? '✅' : '❌'})\n`;
            }
            report += `写文件测试: ${data.SERVER_DIR_write ? '✅' : '❌ ' + (data.SERVER_DIR_write_error || '')}\n`;
            report += `/tmp写测试: ${data.tmp_write ? '✅' : '❌ ' + (data.tmp_write_error || '')}\n`;

            // Disk
            report += '\n── 磁盘空间 ──\n';
            for (const [k, v] of Object.entries(data)) {
                if (k.startsWith('disk_') && k.endsWith('_free_mb')) {
                    const path = k.replace('disk_', '').replace('_free_mb', '');
                    report += `${path}: ${v}MB\n`;
                }
            }

            // Network
            report += '\n── 网络 ──\n';
            report += `GitHub API: ${data.github_api || '?'}\n`;
            if (data.github_latest_sha) report += `最新提交: ${data.github_latest_sha} ${data.github_latest_msg || ''}\n`;
            report += `GitHub ZIP: ${data.github_zip || '?'}\n`;
            if (data.github_zip_size) report += `ZIP大小: ${data.github_zip_size}\n`;

            // Git
            report += '\n── Git ──\n';
            report += `.git目录: ${data.git_dir_exists ? '✅' : '❌'}\n`;
            if (data.git_remote) report += `远程: ${data.git_remote}\n`;
            if (data.git_error) report += `错误: ${data.git_error}\n`;

            // Config
            if (data.config_workspace) report += `\n── 配置 ──\n工作区: ${data.config_workspace}\n`;
            if (data.config_has_token !== undefined) report += `Token: ${data.config_has_token ? '已配置' : '未配置'}\n`;

            // Server log
            if (data.server_log_tail && data.server_log_tail.length > 0) {
                report += '\n── 服务器日志 (最后20行) ──\n';
                report += data.server_log_tail.join('\n');
            }

            statusEl.textContent = report;
            showToast('诊断完成', 'info', 2000);

        } catch (err) {
            statusEl.textContent = '诊断失败: ' + err.message;
            showToast('诊断失败', 'error');
        }
    }

    // ── Prevent unwanted behaviors ──
    function initMobileFixes() {
        // Prevent pull-to-refresh
        document.body.addEventListener('touchmove', (e) => {
            if (e.target.closest('.sidebar') || e.target.closest('#output-content') ||
                e.target.closest('#chat-messages') || e.target.closest('#file-tree') ||
                e.target.closest('#search-results') || e.target.closest('#git-changes-list') ||
                e.target.closest('#git-log-list')) {
                return;
            }
        }, { passive: true });

        // Prevent double-tap zoom via CSS touch-action (see style.css)
        // No JS double-tap prevention needed — avoids blocking legitimate taps

        // Prevent context menu on long press (except for our custom handling)
        document.addEventListener('contextmenu', (e) => {
            if (e.target.closest('.file-item') || e.target.closest('.search-result-item') ||
                e.target.closest('.git-change-item')) {
                e.preventDefault();
            }
        });
    }

    // ── Initialize Everything ──
    async function init() {
        if (initialized) return;
        initialized = true;

        console.log('[PhoneIDE] Initializing...');

        // Init UI components
        initSidebars();
        initTabs();
        initKeyboard();
        initBottomPanel();
        initEditorToolbar();
        initToolbar();
        initFileToolbar();
        initVenv();
        initAutoSave();
        initResize();
        initMobileFixes();
        initTheme();
        initServerManagement();
        await loadTheme();

        // Init modules (order matters)
        try {
            // Editor first
            if (window.EditorManager) await EditorManager.init();

            // Load config
            const configResp = await fetch('/api/config');
            if (configResp.ok) {
                const config = await configResp.json();
                if (config.workspace) {
                    document.getElementById('workspace-path').value = config.workspace;
                }
                if (config.font_size && window.EditorManager) {
                    EditorManager.setFontSize(config.font_size);
                }
                // Theme already loaded in loadTheme() above
            }

            // Load compilers
            if (window.TerminalManager) await TerminalManager.loadCompilers();

            // Load file tree (pass no path to use workspace root)
            if (window.FileManager) await FileManager.loadFileList();

            // Load git status
            if (window.GitManager) await GitManager.refresh();

            // Load chat history
            if (window.ChatManager) await ChatManager.loadHistory();

            // Load venv info (already called in TerminalManager.init, but ensure loaded)
            if (window.TerminalManager && typeof TerminalManager.loadVenvInfo === 'function') {
                await TerminalManager.loadVenvInfo();
            }

            // Wire up chat settings button - use ChatManager's full settings dialog (with Test button)
            const chatSettingsBtn = document.getElementById('chat-settings-btn');
            if (chatSettingsBtn && window.ChatManager && ChatManager.showSettingsDialog) {
                chatSettingsBtn.addEventListener('click', () => ChatManager.showSettingsDialog());
            }

            showToast('PhoneIDE 就绪', 'success', 1500);
            console.log('[PhoneIDE] Ready!');
        } catch (err) {
            console.error('[PhoneIDE] Init error:', err);
            showToast('初始化失败: ' + err.message, 'error', 3000);
        }
    }

    // ── LLM Settings Dialog ──
    async function showLLMSettingsDialog() {
        let currentConfig = {};
        try {
            const resp = await fetch('/api/llm/config');
            if (resp.ok) currentConfig = await resp.json();
        } catch (_e) {}

        const values = await showInputDialog('⚙️ LLM 设置', [
            { name: 'api_type', label: 'API 类型', type: 'select', placeholder: 'openai', value: currentConfig.api_type || 'openai',
              options: [
                  { value: 'openai', label: 'OpenAI' },
                  { value: 'anthropic', label: 'Anthropic' },
                  { value: 'ollama', label: 'Ollama' },
                  { value: 'custom', label: 'Custom' }
              ]},
            { name: 'api_base', label: 'API Base URL', type: 'text', placeholder: 'https://api.openai.com/v1', value: currentConfig.api_base || '' },
            { name: 'api_key', label: 'API Key', type: 'password', placeholder: 'sk-...', value: currentConfig.api_key || '' },
            { name: 'model', label: '模型', type: 'text', placeholder: 'gpt-4o-mini', value: currentConfig.model || '' },
            { name: 'temperature', label: 'Temperature', type: 'text', placeholder: '0.7', value: String(currentConfig.temperature || '0.7') },
        ]);
        if (!values) return;

        try {
            values.temperature = parseFloat(values.temperature) || 0.7;
            const resp = await fetch('/api/llm/config', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(values)
            });
            if (resp.ok) {
                showToast('LLM 设置已保存', 'success');
            } else {
                showToast('保存失败: ' + (await resp.text()), 'error');
            }
        } catch (e) {
            showToast('保存失败: ' + e.message, 'error');
        }
    }

    // ── LLM Test Connection ──
    async function testLLMConnection() {
        showToast('正在测试 LLM 连接...', 'info', 2000);
        try {
            const resp = await fetch('/api/llm/test', { method: 'POST' });
            const data = await resp.json();
            if (resp.ok && data.ok) {
                showToast('LLM 连接成功 ✓', 'success');
            } else {
                showToast('LLM 连接失败: ' + (data.error || 'Unknown error'), 'error');
            }
        } catch (e) {
            showToast('连接失败: ' + e.message, 'error');
        }
    }

    // ── Boot ──
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }

    return {
        init, showToast, showDialog, showPromptDialog, showConfirmDialog, showInputDialog,
        restartServer, toggleLogViewer, checkUpdates, applyUpdate, pollServerStatus
    };
})();

window.AppManager = AppManager;
