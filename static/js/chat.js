/**
 * ChatManager - LLM chat interface with SSE streaming and agent tool execution for PhoneIDE
 * Works with Flask backend on port 1239
 */
const ChatManager = (() => {
    'use strict';

    // ── State ──────────────────────────────────────────────────────
    let isProcessing = false;
    let messages = [];                // local cache of chat history
    let lastUserMessage = null;       // for re-send
    let settingsDialogEl = null;      // cached settings dialog
    let currentAbortController = null; // for aborting SSE streams
    let currentStreamEl = null;       // current streaming message element
    let streamBuffer = '';            // buffer for accumulating streamed text
    let autoScrollEnabled = true;     // auto-scroll state
    let turnIndicator = null;         // turn X/Y element reference
    let iterationCount = 0;           // agent iteration counter
    let streamingStartTime = null;    // for execution time tracking
    let chatMode = 'execute';           // 'plan' or 'execute'
    let planContent = '';               // stored plan markdown for editing
    let lastPlanMsgEl = null;           // reference to plan message element for actions
    let currentConvId = null;           // current conversation id (null = unsaved new chat)

    // ── Constants ──────────────────────────────────────────────────
    const KNOWN_TOOLS = [
        'read_file', 'write_file', 'execute_code', 'search_files',
        'list_files', 'git_status', 'git_diff', 'terminal', 'install_package',
        'web_search', 'web_fetch', 'git_commit', 'git_log', 'git_checkout',
        'edit_file', 'create_directory', 'delete_path', 'file_info', 'grep_code', 'list_packages',
        'browser_navigate', 'browser_evaluate', 'browser_inspect', 'browser_query_all',
        'browser_click', 'browser_input', 'browser_console', 'browser_cookies', 'browser_page_info',
        'debug_start', 'debug_stop', 'debug_set_breakpoints', 'debug_continue',
        'debug_step', 'debug_inspect', 'debug_evaluate', 'debug_stack',
    ];

    const TOOL_ICONS = {
        read_file:     '📖',
        write_file:    '✏️',
        edit_file:     '✏️',
        execute_code:  '▶️',
        search_files:  '🔍',
        list_files:    '📁',
        create_directory: '📁',
        delete_path:   '🗑️',
        git_status:    '🔀',
        git_diff:      '📝',
        git_commit:    '📝',
        git_log:       '📋',
        git_checkout:  '🔀',
        terminal:      '💻',
        install_package: '📦',
        list_packages: '📦',
        file_info:     'ℹ️',
        grep_code:     '🔎',
        web_search:    '🌐',
        web_fetch:     '📄',
        browser_navigate:    '🌐',
        browser_evaluate:    '⚡',
        browser_inspect:    '🔍',
        browser_query_all:    '📋',
        browser_click:       '👆',
        browser_input:       '⌨️',
        browser_console:     '📋',
        browser_cookies:     '🍪',
        browser_page_info:   'ℹ️',
        debug_start:         '🐛',
        debug_stop:          '⏹',
        debug_set_breakpoints: '🔴',
        debug_continue:      '▶️',
        debug_step:          '⏭',
        debug_inspect:       '🔎',
        debug_evaluate:      '⚡',
        debug_stack:         '📚',
    };

    const COLLAPSE_THRESHOLD = 500; // chars before showing "Show more"

    // Plan mode system prompt
    const PLAN_MODE_PROMPT = `[PLAN MODE] Please analyze the request and create a detailed execution plan in Markdown format. Include:
1. **Analysis** - What needs to be done
2. **Files to modify** - List specific files
3. **Step-by-step approach** - Detailed steps
4. **Expected outcome** - What the result should look like

Do NOT execute any tools. Only generate the plan.\n\nUser request: `;

    // ── Helpers ────────────────────────────────────────────────────

    function escapeHTML(str) {
        const div = document.createElement('div');
        div.textContent = str || '';
        return div.innerHTML;
    }

    function escapeAttr(str) {
        return (str || '').replace(/&/g, '&amp;').replace(/"/g, '&quot;')
                          .replace(/'/g, '&#39;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    }

    function formatTime(time) {
        let d;
        if (time instanceof Date) {
            d = time;
        } else if (typeof time === 'number') {
            d = new Date(time);
        } else if (typeof time === 'string') {
            d = new Date(time);
            if (isNaN(d.getTime())) d = new Date();
        } else {
            d = new Date();
        }
        return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    }

    /**
     * Play a completion notification sound using Web Audio API
     */
    function playCompletionSound() {
        try {
            const audioContext = new (window.AudioContext || window.webkitAudioContext)();
            const oscillator = audioContext.createOscillator();
            const gainNode = audioContext.createGain();

            oscillator.connect(gainNode);
            gainNode.connect(audioContext.destination);

            // Pleasant "ding" sound - two tone
            oscillator.frequency.setValueAtTime(523.25, audioContext.currentTime); // C5
            oscillator.frequency.setValueAtTime(659.25, audioContext.currentTime + 0.1); // E5
            oscillator.frequency.setValueAtTime(783.99, audioContext.currentTime + 0.2); // G5

            gainNode.gain.setValueAtTime(0.3, audioContext.currentTime);
            gainNode.gain.linearRampToValueAtTime(0.001, audioContext.currentTime + 0.4);

            oscillator.start(audioContext.currentTime);
            oscillator.stop(audioContext.currentTime + 0.4);
        } catch (e) {
            console.warn('Failed to play completion sound:', e);
        }
    }

    function truncate(str, maxLen) {
        if (!str) return '';
        if (str.length <= maxLen) return str;
        return str.substring(0, maxLen) + '...';
    }

    function msgId() {
        return 'chat-msg-' + Date.now().toString(36) + Math.random().toString(36).slice(2, 7);
    }

    /**
     * Estimate token count from text (rough heuristic: ~4 chars per token)
     */
    function estimateTokens(text) {
        if (!text) return 0;
        return Math.ceil(text.length / 4);
    }

    // ── Markdown-Lite Rendering ────────────────────────────────────

    /**
     * Render markdown-lite formatting with extended support for:
     *  - Code blocks: ```lang\n...\n```
     *  - Inline code: `...`
     *  - Bold: **...** or __...__
     *  - Italic: *...* or _..._
     *  - Links: [text](url) → clickable anchor tags
     *  - Headings: # heading, ## heading, ### heading
     *  - Horizontal rules: --- or ***
     *  - Blockquotes: > text
     *  - Unordered lists: - item or * item
     *  - Ordered lists: 1. item
     *  - Line breaks: double newline
     *
     * Returns HTML string. Input is raw text (will be HTML-escaped internally).
     */
    function renderMarkdownLite(text) {
        if (!text) return '';

        let html = escapeHTML(text);

        // Extract and protect fenced code blocks first
        const codeBlocks = [];
        html = html.replace(/```(\w*)\n([\s\S]*?)```/g, (_, lang, code) => {
            const idx = codeBlocks.length;
            codeBlocks.push({ lang: lang || '', code: code.replace(/\n$/, '') });
            return `\x00CODEBLOCK_${idx}\x00`;
        });

        // Handle ```...``` without explicit newlines (inline code blocks)
        html = html.replace(/```([\s\S]*?)```/g, (_, code) => {
            const idx = codeBlocks.length;
            codeBlocks.push({ lang: '', code: code.replace(/^\n/, '').replace(/\n$/, '') });
            return `\x00CODEBLOCK_${idx}\x00`;
        });

        // Inline code: `...`
        const inlineCodes = [];
        html = html.replace(/`([^`\n]+)`/g, (_, code) => {
            const idx = inlineCodes.length;
            inlineCodes.push(code);
            return `\x00INLINE_${idx}\x00`;
        });

        // Links: [text](url) — must be done before bold/italic
        html = html.replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');

        // Headings: ### heading, ## heading, # heading (at start of line)
        html = html.replace(/^### (.+)$/gm, '<strong class="md-h3">$1</strong>');
        html = html.replace(/^## (.+)$/gm, '<strong class="md-h2">$1</strong>');
        html = html.replace(/^# (.+)$/gm, '<strong class="md-h1">$1</strong>');

        // Horizontal rules: --- or *** (on their own line)
        html = html.replace(/^[-*]{3,}$/gm, '<hr>');

        // Blockquotes: > text
        html = html.replace(/^&gt;\s?(.+)$/gm, '<blockquote>$1</blockquote>');
        // Merge consecutive blockquotes
        html = html.replace(/<\/blockquote>\n<blockquote>/g, '\n');

        // Bold: **...** or __...__
        html = html.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
        html = html.replace(/__(.+?)__/g, '<strong>$1</strong>');

        // Italic: *...* (but not inside already processed tags)
        html = html.replace(/(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)/g, '<em>$1</em>');

        // Restore inline code
        html = html.replace(/\x00INLINE_(\d+)\x00/g, (_, idx) => {
            const code = inlineCodes[parseInt(idx, 10)];
            return `<code>${code}</code>`;
        });

        // Restore code blocks with copy button
        html = html.replace(/\x00CODEBLOCK_(\d+)\x00/g, (_, idx) => {
            const block = codeBlocks[parseInt(idx, 10)];
            const escapedCode = block.code.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
            const copyBtn = `<button class="code-copy-btn" data-code="${escapeAttr(block.code)}" title="Copy">📋</button>`;
            const langLabel = block.lang ? `<span class="code-lang">${escapeHTML(block.lang)}</span>` : '';
            return `<div class="code-block-wrapper">${langLabel}${copyBtn}<pre><code>${escapedCode}</code></pre></div>`;
        });

        // Unordered lists: lines starting with - or * followed by space
        html = html.replace(/^(\s*)([-*])\s+(.+)$/gm, '$1<li>$3</li>');
        html = html.replace(/((?:<li>.*<\/li>\n?)+)/g, '<ul>$1</ul>');

        // Ordered lists: lines starting with \d+.
        html = html.replace(/^(\s*)(\d+)\.\s+(.+)$/gm, '$1<li>$3</li>');

        // Paragraphs: double newlines
        html = html.replace(/\n{2,}/g, '</p><p>');

        // Single newlines -> <br>
        html = html.replace(/\n/g, '<br>');

        // Wrap in paragraphs if not already wrapped
        if (!html.startsWith('<')) {
            html = '<p>' + html + '</p>';
        }

        return html;
    }

    // ── Code Copy Handler ──────────────────────────────────────────

    function bindCopyButtons(container) {
        const btns = (container || document).querySelectorAll('.code-copy-btn');
        btns.forEach(btn => {
            if (btn._bound) return;
            btn._bound = true;
            btn.addEventListener('click', (e) => {
                e.stopPropagation();
                const code = btn.dataset.code || '';
                navigator.clipboard.writeText(code).then(() => {
                    const original = btn.textContent;
                    btn.textContent = '✅';
                    setTimeout(() => { btn.textContent = original; }, 1500);
                }).catch(() => {
                    const ta = document.createElement('textarea');
                    ta.value = code;
                    ta.style.position = 'fixed';
                    ta.style.opacity = '0';
                    document.body.appendChild(ta);
                    ta.select();
                    try { document.execCommand('copy'); } catch (_) {}
                    document.body.removeChild(ta);
                    const original = btn.textContent;
                    btn.textContent = '✅';
                    setTimeout(() => { btn.textContent = original; }, 1500);
                });
            });
        });
    }

    // ── Auto-Scroll with User Detection ────────────────────────────

    function initAutoScroll() {
        const container = document.getElementById('chat-messages');
        if (!container || container._autoScrollInit) return;
        container._autoScrollInit = true;

        container.addEventListener('scroll', () => {
            const threshold = 80;
            const atBottom = container.scrollHeight - container.scrollTop - container.clientHeight < threshold;
            autoScrollEnabled = atBottom;
        });
    }

    function scrollToBottom() {
        if (!autoScrollEnabled) return;
        const container = document.getElementById('chat-messages');
        if (container) {
            container.scrollTop = container.scrollHeight;
        }
    }

    function forceScrollToBottom() {
        autoScrollEnabled = true;
        const container = document.getElementById('chat-messages');
        if (container) {
            container.scrollTop = container.scrollHeight;
        }
    }

    // ── Message Rendering ──────────────────────────────────────────

    function createMessageEl(role, content, extra) {
        extra = extra || {};

        const div = document.createElement('div');
        div.className = 'chat-msg';
        div.id = extra.id || msgId();

        if (role === 'user') div.classList.add('user');
        else if (role === 'assistant') div.classList.add('assistant');
        else if (role === 'tool') div.classList.add('tool');
        else if (role === 'error') div.classList.add('error');
        else if (role === 'system') div.classList.add('system');

        // Role badge for assistant
        if (role === 'assistant') {
            const badge = document.createElement('div');
            badge.className = 'chat-role-badge';
            badge.textContent = '🤖 Assistant';
            div.appendChild(badge);
        }

        // Tool execution details
        if (role === 'tool') {
            const toolName = extra.tool || 'unknown';
            const icon = TOOL_ICONS[toolName] || '🔧';
            const argsStr = extra.args ? formatToolArgs(extra.args) : '';
            const ok = extra.ok !== false;

            const header = document.createElement('div');
            header.className = 'tool-header';
            header.innerHTML = `<span class="tool-name">${icon} ${escapeHTML(toolName)}</span>`
                + (ok ? '<span class="tool-status tool-ok">✓</span>' : '<span class="tool-status tool-fail">✗</span>');
            div.appendChild(header);

            if (extra.duration) {
                const durEl = document.createElement('span');
                durEl.className = 'tool-duration';
                durEl.textContent = formatDuration(extra.duration);
                header.appendChild(durEl);
            }

            if (argsStr) {
                const argsEl = document.createElement('div');
                argsEl.className = 'tool-args';
                argsEl.textContent = argsStr;
                if (argsStr.length > 120) {
                    argsEl.classList.add('collapsible', 'collapsed');
                    argsEl.addEventListener('click', () => {
                        argsEl.classList.toggle('collapsed');
                        argsEl.classList.toggle('expanded');
                    });
                }
                div.appendChild(argsEl);
            }

            if (content) {
                const resultEl = document.createElement('div');
                resultEl.className = 'tool-result';
                const resultStr = typeof content === 'string' ? content : JSON.stringify(content, null, 2);

                if (resultStr.length > COLLAPSE_THRESHOLD) {
                    const shortContent = resultStr.substring(0, COLLAPSE_THRESHOLD);
                    const fullContent = resultStr;

                    const shortSpan = document.createElement('div');
                    shortSpan.className = 'tool-result-short';
                    shortSpan.textContent = shortContent + '...';

                    const fullSpan = document.createElement('div');
                    fullSpan.className = 'tool-result-full';
                    fullSpan.style.display = 'none';
                    fullSpan.textContent = fullContent;

                    const toggleBtn = document.createElement('button');
                    toggleBtn.className = 'tool-toggle-btn';
                    toggleBtn.textContent = 'Show more';
                    toggleBtn.addEventListener('click', () => {
                        const expanded = fullSpan.style.display !== 'none';
                        fullSpan.style.display = expanded ? 'none' : '';
                        toggleBtn.textContent = expanded ? 'Show more' : 'Show less';
                    });

                    resultEl.appendChild(shortSpan);
                    resultEl.appendChild(fullSpan);
                    resultEl.appendChild(toggleBtn);
                } else {
                    resultEl.textContent = resultStr;
                }
                div.appendChild(resultEl);
            }

            // Timestamp
            if (extra.time) {
                const timeEl = document.createElement('div');
                timeEl.className = 'chat-time';
                timeEl.textContent = formatTime(extra.time);
                div.appendChild(timeEl);
            }

            return div;
        }

        // Error messages
        if (role === 'error') {
            const icon = document.createElement('span');
            icon.textContent = '⚠️ ';
            div.appendChild(icon);
            const textEl = document.createElement('span');
            textEl.innerHTML = renderMarkdownLite(content);
            div.appendChild(textEl);

            // Retry button
            if (extra.retryable) {
                const retryBtn = document.createElement('button');
                retryBtn.className = 'chat-retry-btn';
                retryBtn.textContent = '🔄 Retry';
                retryBtn.addEventListener('click', () => {
                    if (lastUserMessage) {
                        sendMessage(lastUserMessage);
                    }
                });
                div.appendChild(retryBtn);
            }
        } else if (role === 'system') {
            // System / status messages (thinking, done, etc.)
            const textEl = document.createElement('div');
            textEl.className = 'chat-system-msg';
            textEl.innerHTML = renderMarkdownLite(content);
            div.appendChild(textEl);
        } else {
            // Regular text content with markdown-lite
            const textEl = document.createElement('div');
            textEl.className = 'chat-content';
            textEl.innerHTML = renderMarkdownLite(content);
            div.appendChild(textEl);
        }

        // Timestamp
        const timeStr = extra.time || (Date.now());
        const timeEl = document.createElement('div');
        timeEl.className = 'chat-time';
        timeEl.textContent = formatTime(timeStr);
        div.appendChild(timeEl);

        // Bind copy buttons inside this message
        bindCopyButtons(div);

        return div;
    }

    function formatToolArgs(args) {
        if (!args) return '';
        if (typeof args === 'string') {
            try { args = JSON.parse(args); } catch (_) { return truncate(args, 200); }
        }
        if (typeof args !== 'object') return String(args);
        const parts = [];
        for (const [key, val] of Object.entries(args)) {
            const valStr = typeof val === 'string' ? truncate(val, 80) : JSON.stringify(val);
            parts.push(`${key}: ${valStr}`);
        }
        return parts.join(', ');
    }

    function formatDuration(ms) {
        if (!ms) return '';
        if (ms < 1000) return ms + 'ms';
        return (ms / 1000).toFixed(1) + 's';
    }

    function renderMessages(msgs) {
        const container = document.getElementById('chat-messages');
        if (!container) return;

        container.innerHTML = '';
        messages = Array.isArray(msgs) ? msgs : [];

        if (messages.length === 0) {
            container.innerHTML = '<div class="empty-state"><div class="emoji">🤖</div><div>Ask me anything about your code</div></div>';
            return;
        }

        for (const msg of messages) {
            const el = createMessageEl(msg.role, msg.content, msg);
            container.appendChild(el);
        }

        bindCopyButtons(container);
        forceScrollToBottom();
    }

    function addMessage(role, content, extra) {
        const container = document.getElementById('chat-messages');
        if (!container) return;

        const emptyState = container.querySelector('.empty-state');
        if (emptyState) emptyState.remove();

        extra = extra || {};
        extra.time = extra.time || new Date();

        const el = createMessageEl(role, content, extra);
        container.appendChild(el);

        messages.push({ role, content, time: extra.time, ...extra });

        bindCopyButtons(container);
        forceScrollToBottom();

        return el;
    }

    // ── Typing / Status Indicators ─────────────────────────────────

    function showTyping() {
        const container = document.getElementById('chat-messages');
        if (!container) return null;
        if (container.querySelector('.chat-typing')) return null;

        const indicator = document.createElement('div');
        indicator.className = 'chat-typing';
        indicator.textContent = 'Thinking';
        container.appendChild(indicator);
        forceScrollToBottom();
        return indicator;
    }

    function hideTyping() {
        const container = document.getElementById('chat-messages');
        if (!container) return;
        const indicator = container.querySelector('.chat-typing');
        if (indicator) indicator.remove();
    }

    /**
     * Create or update the turn indicator (Turn X/Y)
     */
    function updateTurnIndicator(current, total) {
        const container = document.getElementById('chat-messages');
        if (!container) return;

        if (!turnIndicator) {
            turnIndicator = document.createElement('div');
            turnIndicator.className = 'chat-turn-indicator';
            turnIndicator.id = 'chat-turn-indicator';
        }

        turnIndicator.textContent = total > 0
            ? `Turn ${current}/${total}`
            : `Turn ${current}`;
        turnIndicator.style.display = '';

        // Insert at the top of the messages container (after empty state if any)
        const existing = container.querySelector('#chat-turn-indicator');
        if (!existing) {
            const first = container.firstChild;
            container.insertBefore(turnIndicator, first);
        }
    }

    function hideTurnIndicator() {
        if (turnIndicator) {
            turnIndicator.style.display = 'none';
        }
    }

    // ── Tool Progress Visualization ────────────────────────────────

    /**
     * Show a tool execution in progress with spinning indicator
     * @returns {HTMLElement} the tool element for later updating
     */
    function showToolProgress(toolName, args) {
        const container = document.getElementById('chat-messages');
        if (!container) return null;

        const el = document.createElement('div');
        el.className = 'chat-msg tool tool-progress';
        el.id = msgId();

        const icon = TOOL_ICONS[toolName] || '🔧';
        const argsStr = args ? formatToolArgs(args) : '';

        el.innerHTML = `
            <div class="tool-header">
                <span class="tool-name">${icon} ${escapeHTML(toolName)}</span>
                <span class="tool-spinner" role="status" aria-label="Running">⏳</span>
            </div>
            ${argsStr ? `<div class="tool-args">${escapeHTML(argsStr)}</div>` : ''}
            <div class="tool-result tool-waiting">Executing...</div>
        `;

        el._toolStartTime = Date.now();
        container.appendChild(el);
        forceScrollToBottom();
        return el;
    }

    /**
     * Update a tool progress element with the result
     */
    function finalizeToolResult(toolEl, result, ok) {
        if (!toolEl) return;

        const duration = Date.now() - (toolEl._toolStartTime || Date.now());

        // Update spinner to status
        const spinner = toolEl.querySelector('.tool-spinner');
        if (spinner) {
            spinner.className = ok ? 'tool-status tool-ok' : 'tool-status tool-fail';
            spinner.textContent = ok ? '✓' : '✗';
            spinner.setAttribute('role', '');
            spinner.removeAttribute('aria-label');
        }

        // Update result
        const resultEl = toolEl.querySelector('.tool-result');
        if (resultEl) {
            resultEl.className = 'tool-result';
            resultEl.classList.remove('tool-waiting');
            const resultStr = typeof result === 'string' ? result : JSON.stringify(result, null, 2);

            if (resultStr.length > COLLAPSE_THRESHOLD) {
                resultEl.innerHTML = '';
                const shortSpan = document.createElement('div');
                shortSpan.className = 'tool-result-short';
                shortSpan.textContent = resultStr.substring(0, COLLAPSE_THRESHOLD) + '...';

                const fullSpan = document.createElement('div');
                fullSpan.className = 'tool-result-full';
                fullSpan.style.display = 'none';
                fullSpan.textContent = resultStr;

                const toggleBtn = document.createElement('button');
                toggleBtn.className = 'tool-toggle-btn';
                toggleBtn.textContent = 'Show more';
                toggleBtn.addEventListener('click', () => {
                    const expanded = fullSpan.style.display !== 'none';
                    fullSpan.style.display = expanded ? 'none' : '';
                    toggleBtn.textContent = expanded ? 'Show more' : 'Show less';
                });

                resultEl.appendChild(shortSpan);
                resultEl.appendChild(fullSpan);
                resultEl.appendChild(toggleBtn);
            } else {
                resultEl.textContent = resultStr;
            }
        }

        // Add duration to header
        const header = toolEl.querySelector('.tool-header');
        if (header && duration) {
            const durEl = document.createElement('span');
            durEl.className = 'tool-duration';
            durEl.textContent = formatDuration(duration);
            header.appendChild(durEl);
        }

        // Add timestamp
        const timeEl = document.createElement('div');
        timeEl.className = 'chat-time';
        timeEl.textContent = formatTime(new Date());
        toolEl.appendChild(timeEl);

        toolEl.classList.remove('tool-progress');
        bindCopyButtons(toolEl);
    }

    // ── Streaming Message Display ──────────────────────────────────

    /**
     * Create a new streaming message element and start accumulating text
     * @returns {HTMLElement} the message element
     */
    function startStreamingMessage() {
        const container = document.getElementById('chat-messages');
        if (!container) return null;

        const el = document.createElement('div');
        el.className = 'chat-msg assistant streaming';
        el.id = msgId();

        const badge = document.createElement('div');
        badge.className = 'chat-role-badge';
        badge.textContent = '🤖 Assistant';
        el.appendChild(badge);

        const contentEl = document.createElement('div');
        contentEl.className = 'chat-content chat-streaming';
        el.appendChild(contentEl);

        container.appendChild(el);
        currentStreamEl = el;
        streamBuffer = '';

        forceScrollToBottom();
        return el;
    }

    /**
     * Append a chunk of text to the current streaming message
     */
    function appendStreamChunk(chunk) {
        if (!currentStreamEl || !chunk) return;

        streamBuffer += chunk;

        const contentEl = currentStreamEl.querySelector('.chat-content');
        if (contentEl) {
            contentEl.innerHTML = renderMarkdownLite(streamBuffer);
            bindCopyButtons(contentEl);
            scrollToBottom();
        }
    }

    /**
     * Finalize the current streaming message
     */
    function finalizeStreamMessage() {
        if (!currentStreamEl) return;

        const contentEl = currentStreamEl.querySelector('.chat-content');
        if (contentEl) {
            // Final render of accumulated text
            contentEl.innerHTML = renderMarkdownLite(streamBuffer);
            contentEl.classList.remove('chat-streaming');
            bindCopyButtons(contentEl);
        }

        currentStreamEl.classList.remove('streaming');

        // Add timestamp
        const timeEl = document.createElement('div');
        timeEl.className = 'chat-time';
        timeEl.textContent = formatTime(new Date());
        currentStreamEl.appendChild(timeEl);

        // Cache the message
        messages.push({
            role: 'assistant',
            content: streamBuffer,
            time: new Date()
        });

        const el = currentStreamEl;
        currentStreamEl = null;
        streamBuffer = '';

        forceScrollToBottom();
        return el;
    }

    // ── Stop Button ────────────────────────────────────────────────

    /**
     * Create and show the stop button during generation
     * @returns {HTMLElement} the stop button
     */
    function showStopButton() {
        hideStopButton();

        const inputArea = document.getElementById('chat-input-area');
        if (!inputArea) return null;

        const btn = document.createElement('button');
        btn.id = 'chat-stop';
        btn.className = 'chat-stop-btn';
        btn.textContent = '⏹ Stop';
        btn.addEventListener('click', abortGeneration);

        inputArea.insertBefore(btn, inputArea.firstChild);
        return btn;
    }

    /**
     * Hide the stop button
     */
    function hideStopButton() {
        const btn = document.getElementById('chat-stop');
        if (btn) btn.remove();
    }

    /**
     * Abort the current SSE stream
     */
    function abortGeneration() {
        if (currentAbortController) {
            currentAbortController.abort();
            currentAbortController = null;
        }
    }

    // ── Send / Processing State ─────────────────────────────────────

    function setProcessing(processing) {
        isProcessing = processing;

        const sendBtn = document.getElementById('chat-send');
        const input = document.getElementById('chat-input');
        const statusEl = document.getElementById('chat-execute-status');

        if (sendBtn) {
            sendBtn.disabled = processing;
            sendBtn.textContent = processing ? 'Thinking...' : 'Send';
            sendBtn.style.display = processing ? 'none' : '';
        }

        if (input) {
            input.disabled = processing;
            if (!processing) {
                input.focus();
            }
        }

        if (processing) {
            showStopButton();
            if (statusEl) statusEl.textContent = '';
        } else {
            hideStopButton();
            hideTyping();
        }
    }

    function setExecuteStatus(text) {
        const el = document.getElementById('chat-execute-status');
        if (el) {
            el.textContent = text || '';
        }
    }

    // ── API: Load History ──────────────────────────────────────────

    async function loadHistory() {
        try {
            const resp = await fetch('/api/chat/history');
            if (!resp.ok) throw new Error(`Failed to load history: ${resp.statusText}`);

            const data = await resp.json();
            const msgs = data.messages || [];
            renderMessages(msgs);
            return msgs;
        } catch (err) {
            console.warn('ChatManager: loadHistory error:', err.message);
            renderMessages([]);
            return [];
        }
    }

    // ── API: Send Message (SSE Streaming) ──────────────────────────

    /**
     * Send a message to the chat API using SSE streaming
     * @param {string} [text] - message text (defaults to input field value)
     */
    async function sendMessage(text) {
        if (isProcessing) return;

        const input = document.getElementById('chat-input');
        const message = text || (input ? input.value : '').trim();

        if (!message) {
            showToast('Please enter a message', 'warning');
            return;
        }

        // Ensure we have a conversation id
        if (!currentConvId) {
            currentConvId = 'conv_' + Date.now().toString(36) + Math.random().toString(36).slice(2, 7);
        }

        // Clear input
        if (input) input.value = '';
        autoResizeInput();
        lastUserMessage = message;

        // Add user message to display
        addMessage('user', message);
        setProcessing(true);
        hideTurnIndicator();

        // Plan mode: prepend plan instruction to message
        let actualMessage = message;
        if (chatMode === 'plan') {
            actualMessage = PLAN_MODE_PROMPT + message;
        }

        streamingStartTime = Date.now();
        iterationCount = 0;
        let currentToolEl = null;
        let lastToolName = null;
        let hasError = false;

        // Create abort controller
        currentAbortController = new AbortController();
        const signal = currentAbortController.signal;

        try {
            const reqUrl = '/api/chat/send/stream';
            const modelSelect = document.getElementById('chat-model-select');
            const reqBody = { message: actualMessage, conv_id: currentConvId };
            if (modelSelect && modelSelect.value !== '') {
                reqBody.model_index = parseInt(modelSelect.value);
            }

            const resp = await fetch(reqUrl, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(reqBody),
                signal
            });

            if (!resp.ok) {
                const errBody = await resp.text().catch(() => '');
                // Build detailed error info
                const detail = [
                    `Status: ${resp.status} ${resp.statusText}`,
                    `Type: ${resp.type}`,
                    `Redirected: ${resp.redirected}`,
                    `URL: ${resp.url}`,
                    `Request: POST ${reqUrl}`,
                ].join('\n');
                const fullMsg = errBody ? `${detail}\n\n${errBody}` : detail;
                throw new Error(fullMsg);
            }

            // Read the SSE stream
            const reader = resp.body.getReader();
            const decoder = new TextDecoder();
            let buffer = '';

            while (true) {
                const { done, value } = await reader.read();
                if (done) break;

                buffer += decoder.decode(value, { stream: true });

                // Parse SSE events from buffer
                const lines = buffer.split('\n');
                buffer = lines.pop() || ''; // keep incomplete line in buffer

                for (const line of lines) {
                    if (!line.startsWith('data: ')) continue;
                    const rawData = line.substring(6).trim();
                    if (!rawData || rawData === '[DONE]') continue;

                    // Parse JSON and use 'type' field to determine event type
                    let parsed;
                    try {
                        parsed = JSON.parse(rawData);
                    } catch (_) {
                        continue;
                    }

                    const eventType = parsed.type || '';

                    if (eventType === 'text') {
                        // Text chunk from assistant
                        hideTyping();
                        if (!currentStreamEl) {
                            startStreamingMessage();
                        }
                        appendStreamChunk(parsed.content || parsed.text || '');
                    } else if (eventType === 'tool_start') {
                        // Tool execution starting
                        hideTyping();
                        if (currentStreamEl && streamBuffer) {
                            finalizeStreamMessage();
                        }
                        currentToolEl = showToolProgress(
                            parsed.tool || parsed.name || 'unknown',
                            parsed.args
                        );
                        lastToolName = parsed.tool || parsed.name || null;
                        setExecuteStatus(`Running ${parsed.tool || parsed.name || 'tool'}...`);
                    } else if (eventType === 'tool_result') {
                        // Tool execution completed
                        const ok = parsed.ok !== false && parsed.error === undefined;
                        finalizeToolResult(
                            currentToolEl,
                            parsed.result || parsed.output || parsed.error || '',
                            ok
                        );
                        iterationCount++;
                        updateTurnIndicator(iterationCount, parsed.max_iterations || 0);
                        setExecuteStatus(`Turn ${iterationCount}${parsed.max_iterations ? '/' + parsed.max_iterations : ''}`);
                        currentToolEl = null;
                        // Refresh file tree after file-related tool calls
                        if (window.FileManager && lastToolName) {
                            const fileTools = ['write_file', 'edit_file', 'create_directory', 'delete_path', 'install_package'];
                            if (fileTools.includes(lastToolName)) {
                                window.FileManager.refresh();
                            }
                        }
                        lastToolName = null;
                        forceScrollToBottom();
                    } else if (eventType === 'thinking') {
                        // Status / thinking message
                        setExecuteStatus(parsed.message || parsed.text || parsed.content || 'Thinking...');
                        hideTyping();
                        showTyping();
                    } else if (eventType === 'error') {
                        // Error occurred
                        hasError = true;
                        hideTyping();
                        setExecuteStatus('');  // clear retry status
                        if (currentStreamEl && streamBuffer) {
                            finalizeStreamMessage();
                        }
                        const errMsg = parsed.content || parsed.message || parsed.error || rawData;
                        addMessage('error', errMsg, { retryable: true });
                        showToast('Chat error: ' + errMsg, 'error');
                        // Also log to console error tab
                        if (window.DebugManager && window.DebugManager.addError) {
                            window.DebugManager.addError('LLM Error: ' + errMsg, 'chat');
                        }
                    } else if (eventType === 'done') {
                        // Generation complete
                        hideTyping();
                        let finalizedEl = null;
                        if (currentStreamEl && streamBuffer) {
                            finalizedEl = finalizeStreamMessage();
                        }
                        const totalDuration = Date.now() - streamingStartTime;
                        const tokensUsed = estimateTokens(streamBuffer);
                        let summary = `Completed in ${formatDuration(totalDuration)}`;
                        if (parsed.iterations) {
                            summary += ` · ${parsed.iterations} iteration(s)`;
                        }
                        summary += ` · ~${tokensUsed} tokens`;
                        setExecuteStatus(summary);
                        
                        // Play completion sound and show notification
                        playCompletionSound();
                        showToast('✅ Task completed successfully!', 'success', 3000);
                        
                        // In plan mode, inject action buttons
                        if (chatMode === 'plan' && finalizedEl && streamBuffer) {
                            lastPlanMsgEl = finalizedEl;
                            planContent = streamBuffer;
                            injectPlanActions(finalizedEl, streamBuffer);
                        }
                    }
                }
            }

        } catch (err) {
            if (err.name === 'AbortError') {
                // User aborted
                hideTyping();
                if (currentStreamEl && streamBuffer) {
                    finalizeStreamMessage();
                }
                addMessage('system', 'Generation stopped by user.');
                showToast('Generation stopped', 'info');
            } else {
                hideTyping();
                if (currentStreamEl && streamBuffer) {
                    finalizeStreamMessage();
                }
                addMessage('error', err.message, { retryable: true });
                showToast('Chat error: ' + err.message, 'error');
                hasError = true;
            }
        } finally {
            currentAbortController = null;
            isProcessing = false;
            hideStopButton();
            hideTyping();
            hideTurnIndicator();
            autoResizeInput();

            const sendBtn = document.getElementById('chat-send');
            if (sendBtn) {
                sendBtn.disabled = false;
                sendBtn.textContent = 'Send';
                sendBtn.style.display = '';
            }
            const inputEl = document.getElementById('chat-input');
            if (inputEl) {
                inputEl.disabled = false;
            }
        }
    }

    // ── API: Clear History ─────────────────────────────────────────

    async function clearHistory() {
        try {
            const resp = await fetch('/api/chat/clear', { method: 'POST' });
            if (!resp.ok) throw new Error(`Failed to clear: ${resp.statusText}`);
            await resp.json();

            messages = [];
            lastUserMessage = null;
            renderMessages([]);
            showToast('Chat history cleared', 'success');
        } catch (err) {
            showToast('Error clearing chat: ' + err.message, 'error');
        }
    }

    // ── API: New Chat ───────────────────────────────────────────────

    async function newChat() {
        if (isProcessing) {
            showToast('Please wait for current generation to finish', 'warning');
            return;
        }
        // Reset conversation
        currentConvId = null;
        messages = [];
        lastUserMessage = null;
        renderMessages([]);

        // Focus input
        const input = document.getElementById('chat-input');
        if (input) {
            input.value = '';
            input.focus();
        }
        showToast('New conversation started', 'success');
    }

    // ── Re-Send Last Message ───────────────────────────────────────

    async function resendLastMessage() {
        if (isProcessing) return;
        if (!lastUserMessage) {
            showToast('No previous message to resend', 'warning');
            return;
        }
        await sendMessage(lastUserMessage);
    }

    // ── LLM Settings ───────────────────────────────────────────────

    async function loadLLMConfig() {
        try {
            const resp = await fetch('/api/llm/config');
            if (!resp.ok) throw new Error(`Failed to load config: ${resp.statusText}`);
            return await resp.json();
        } catch (err) {
            console.warn('ChatManager: loadLLMConfig error:', err.message);
            return null;
        }
    }

    // ── Model Selector ─────────────────────────────────────────────

    async function refreshModelSelector() {
        const select = document.getElementById('chat-model-select');
        if (!select) return;
        const config = await loadLLMConfig();
        if (!config) return;
        const models = config.models || [];
        const currentVal = select.value;
        select.innerHTML = '';
        for (let i = 0; i < models.length; i++) {
            const m = models[i];
            const opt = document.createElement('option');
            opt.value = i;
            opt.textContent = m.name || m.model || ('Model ' + (i + 1));
            opt.disabled = !m.enabled;
            if (opt.disabled) opt.textContent += ' (disabled)';
            select.appendChild(opt);
        }
        // Restore previous selection or pick first enabled
        if (currentVal !== '' && select.querySelector(`option[value="${currentVal}"]`)) {
            select.value = currentVal;
        } else {
            const firstEnabled = select.querySelector('option:not([disabled])');
            if (firstEnabled) select.value = firstEnabled.value;
        }
    }

    async function saveLLMConfig(config) {
        try {
            const resp = await fetch('/api/llm/config', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(config)
            });
            if (!resp.ok) throw new Error(`Failed to save config: ${resp.statusText}`);
            return await resp.json();
        } catch (err) {
            showToast('Error saving LLM config: ' + err.message, 'error');
            return null;
        }
    }

    async function showSettingsDialog() {
        try {
        removeSettingsDialog();

        const config = await loadLLMConfig() || {};
        const models = config.models || [];

        const overlay = document.createElement('div');
        overlay.className = 'chat-settings-overlay';
        overlay.id = 'chat-settings-overlay';

        // Store working models data
        let workingModels = JSON.parse(JSON.stringify(models));
        // Ensure api_key_masked is available from server response
        for (const m of workingModels) {
            const serverModel = models.find(sm => sm.name === m.name);
            if (serverModel) m.api_key_masked = serverModel.api_key_masked || '';
        }

        overlay.innerHTML = `
            <div class="chat-settings-dialog" style="max-width:520px">
                <div class="chat-settings-header">
                    <span>⚙️ LLM 模型配置</span>
                    <button class="chat-settings-close" title="Close">✕</button>
                </div>
                <div class="chat-settings-body" id="llm-settings-body">
                    <div class="llm-models-list" id="llm-models-list"></div>
                    <button class="llm-add-model-btn" id="llm-add-model-btn">+ 添加模型</button>
                    <label class="full-width" style="margin-top:4px">
                        <span>全局 System Prompt</span>
                        <textarea id="llm-system-prompt" rows="3" placeholder="You are a helpful coding assistant...">${escapeHTML(config.system_prompt || '')}</textarea>
                    </label>
                </div>
                <div class="chat-settings-footer">
                    <button class="btn-cancel" id="llm-settings-cancel">Cancel</button>
                    <button class="btn-confirm" id="llm-settings-save">Save</button>
                </div>
            </div>
        `;

        document.body.appendChild(overlay);
        settingsDialogEl = overlay;
        injectSettingsStyles();

        // Render model cards
        function renderModelList() {
            const list = overlay.querySelector('#llm-models-list');
            list.innerHTML = '';
            workingModels.forEach((m, idx) => {
                const card = document.createElement('div');
                card.className = 'llm-model-card' + (m.enabled ? ' llm-model-enabled' : '');
                card.innerHTML = `
                    <div class="llm-model-card-header">
                        <div class="llm-model-toggle-area">
                            <button class="llm-enable-btn ${m.enabled ? 'active' : ''}" data-idx="${idx}" title="${m.enabled ? '点击禁用' : '点击启用'}">
                                ${m.enabled ? '✅' : '⬜'}
                            </button>
                            <input class="llm-model-name" data-idx="${idx}" value="${escapeAttr(m.name || '')}" placeholder="模型名称">
                        </div>
                        <div class="llm-model-card-actions">
                            <button class="llm-test-model-btn" data-idx="${idx}" title="测试此模型">🔗</button>
                            <button class="llm-del-model-btn" data-idx="${idx}" title="删除此模型">🗑</button>
                        </div>
                    </div>
                    <div class="llm-model-card-body" style="display:${m.enabled ? '' : 'none'}">
                        <div class="llm-model-fields">
                            <label><span>Provider</span>
                                <select class="llm-provider" data-idx="${idx}">
                                    <option value="openai"${m.provider === 'openai' ? ' selected' : ''}>OpenAI</option>
                                    <option value="anthropic"${m.provider === 'anthropic' ? ' selected' : ''}>Anthropic</option>
                                    <option value="ollama"${m.provider === 'ollama' ? ' selected' : ''}>Ollama</option>
                                    <option value="modelscope"${m.provider === 'modelscope' ? ' selected' : ''}>ModelScope</option>
                                    <option value="custom"${m.provider === 'custom' ? ' selected' : ''}>Custom</option>
                                </select>
                            </label>
                            <label><span>API Type</span>
                                <select class="llm-api-type" data-idx="${idx}">
                                    <option value="openai"${(m.api_type || 'openai') === 'openai' ? ' selected' : ''}>OpenAI Compatible</option>
                                    <option value="azure"${m.api_type === 'azure' ? ' selected' : ''}>Azure OpenAI</option>
                                    <option value="ollama"${m.api_type === 'ollama' ? ' selected' : ''}>Ollama</option>
                                    <option value="custom"${m.api_type === 'custom' ? ' selected' : ''}>Custom</option>
                                </select>
                            </label>
                            <label><span>API Key</span>
                                <input type="password" class="llm-api-key" data-idx="${idx}" placeholder="sk-..." value="${escapeAttr(m.api_key || '')}">
                                ${m.api_key_masked ? `<span class="hint">Current: ${escapeHTML(m.api_key_masked)}</span>` : ''}
                            </label>
                            <label><span>API Base URL</span>
                                <input type="text" class="llm-api-base" data-idx="${idx}" placeholder="https://api.openai.com/v1" value="${escapeAttr(m.api_base || '')}">
                            </label>
                            <label><span>Model</span>
                                <input type="text" class="llm-model" data-idx="${idx}" placeholder="gpt-4o-mini" value="${escapeAttr(m.model || '')}">
                            </label>
                            <div class="llm-model-params">
                                <label><span>Temperature</span>
                                    <input type="number" class="llm-temperature" data-idx="${idx}" min="0" max="2" step="0.1" value="${m.temperature !== undefined ? m.temperature : '0.7'}">
                                </label>
                                <label><span>Max Tokens</span>
                                    <input type="number" class="llm-max-tokens" data-idx="${idx}" min="256" max="128000" step="256" value="${m.max_tokens || '4096'}">
                                </label>
                            </div>
                        </div>
                    </div>
                `;
                list.appendChild(card);
            });

            // Bind events
            list.querySelectorAll('.llm-enable-btn').forEach(btn => {
                btn.addEventListener('click', () => {
                    const idx = parseInt(btn.dataset.idx);
                    workingModels[idx].enabled = !workingModels[idx].enabled;
                    renderModelList();
                });
            });
            list.querySelectorAll('.llm-del-model-btn').forEach(btn => {
                btn.addEventListener('click', () => {
                    const idx = parseInt(btn.dataset.idx);
                    workingModels.splice(idx, 1);
                    renderModelList();
                });
            });
            list.querySelectorAll('.llm-test-model-btn').forEach(btn => {
                btn.addEventListener('click', async () => {
                    const idx = parseInt(btn.dataset.idx);
                    btn.textContent = '⏳';
                    btn.disabled = true;
                    try {
                        // Sync latest field values before testing
                        syncFieldsFromDOM();
                        // Save current models first so test endpoint can read them
                        const saveConfig = { models: workingModels, system_prompt: overlay.querySelector('#llm-system-prompt').value.trim() };
                        await saveLLMConfig(saveConfig);
                        const resp = await fetch('/api/llm/test', {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ model_index: idx })
                        });
                        let data;
                        try { data = await resp.json(); } catch {
                            const text = await resp.text().catch(() => '');
                            throw new Error(text ? `Server error: ${text.substring(0, 200)}` : 'Invalid server response');
                        }
                        if (data.ok) {
                            let msg = `✅ ${workingModels[idx].name} 连接成功: ${data.model || ''}`;
                            if (data.tokens) msg += ` (${data.tokens} tokens)`;
                            if (data.reply) msg += `\n💬 ${data.reply}`;
                            if (data.warning) msg += `\n⚠️ ${data.warning}`;
                            showToast(msg, data.warning ? 'warning' : 'success', 5000);
                        } else {
                            showToast(`❌ ${workingModels[idx].name} 连接失败: ${data.error || 'Unknown'}`, 'error', 5000);
                        }
                    } catch (err) {
                        showToast('❌ ' + err.message, 'error');
                    } finally {
                        btn.textContent = '🔗';
                        btn.disabled = false;
                    }
                });
            });
            // Bind field changes
            list.querySelectorAll('.llm-model-name').forEach(input => {
                input.addEventListener('change', () => { workingModels[parseInt(input.dataset.idx)].name = input.value.trim(); });
            });
            list.querySelectorAll('.llm-provider').forEach(sel => {
                sel.addEventListener('change', () => { workingModels[parseInt(sel.dataset.idx)].provider = sel.value; });
            });
            list.querySelectorAll('.llm-api-type').forEach(sel => {
                sel.addEventListener('change', () => { workingModels[parseInt(sel.dataset.idx)].api_type = sel.value; });
            });
            list.querySelectorAll('.llm-api-key').forEach(input => {
                input.addEventListener('change', () => { workingModels[parseInt(input.dataset.idx)].api_key = input.value; });
            });
            list.querySelectorAll('.llm-api-base').forEach(input => {
                input.addEventListener('change', () => { workingModels[parseInt(input.dataset.idx)].api_base = input.value.trim(); });
            });
            list.querySelectorAll('.llm-model').forEach(input => {
                input.addEventListener('change', () => { workingModels[parseInt(input.dataset.idx)].model = input.value.trim(); });
            });
            list.querySelectorAll('.llm-temperature').forEach(input => {
                input.addEventListener('change', () => { workingModels[parseInt(input.dataset.idx)].temperature = parseFloat(input.value) || 0.7; });
            });
            list.querySelectorAll('.llm-max-tokens').forEach(input => {
                input.addEventListener('change', () => { workingModels[parseInt(input.dataset.idx)].max_tokens = parseInt(input.value, 10) || 4096; });
            });
        }

        // Sync all DOM field values into workingModels (call before save/test)
        function syncFieldsFromDOM() {
            overlay.querySelectorAll('.llm-model-name').forEach(input => {
                workingModels[parseInt(input.dataset.idx)].name = input.value.trim();
            });
            overlay.querySelectorAll('.llm-provider').forEach(sel => {
                workingModels[parseInt(sel.dataset.idx)].provider = sel.value;
            });
            overlay.querySelectorAll('.llm-api-type').forEach(sel => {
                workingModels[parseInt(sel.dataset.idx)].api_type = sel.value;
            });
            overlay.querySelectorAll('.llm-api-key').forEach(input => {
                workingModels[parseInt(input.dataset.idx)].api_key = input.value;
            });
            overlay.querySelectorAll('.llm-api-base').forEach(input => {
                workingModels[parseInt(input.dataset.idx)].api_base = input.value.trim();
            });
            overlay.querySelectorAll('.llm-model').forEach(input => {
                workingModels[parseInt(input.dataset.idx)].model = input.value.trim();
            });
            overlay.querySelectorAll('.llm-temperature').forEach(input => {
                workingModels[parseInt(input.dataset.idx)].temperature = parseFloat(input.value) || 0.7;
            });
            overlay.querySelectorAll('.llm-max-tokens').forEach(input => {
                workingModels[parseInt(input.dataset.idx)].max_tokens = parseInt(input.value, 10) || 4096;
            });
        }

        renderModelList();

        // Add model button
        overlay.querySelector('#llm-add-model-btn').addEventListener('click', () => {
            workingModels.push({
                name: '新模型',
                provider: 'openai',
                api_type: 'openai',
                api_key: '',
                api_base: '',
                model: '',
                enabled: false,
                temperature: 0.7,
                max_tokens: 4096,
            });
            renderModelList();
        });

        // Close / Cancel / Save
        overlay.querySelector('.chat-settings-close').addEventListener('click', removeSettingsDialog);
        overlay.querySelector('#llm-settings-cancel').addEventListener('click', removeSettingsDialog);
        overlay.querySelector('#llm-settings-save').addEventListener('click', async () => {
            // Sync latest field values from DOM
            syncFieldsFromDOM();

            const newConfig = {
                models: workingModels,
                system_prompt: overlay.querySelector('#llm-system-prompt').value.trim()
            };
            const result = await saveLLMConfig(newConfig);
            if (result) {
                const enabledCount = workingModels.filter(m => m.enabled).length;
                showToast(`已保存 ${workingModels.length} 个模型配置 (${enabledCount} 个启用)`, 'success');
                removeSettingsDialog();
                refreshModelSelector();
            }
        });

        overlay.addEventListener('click', (e) => {
            if (e.target === overlay) removeSettingsDialog();
        });

        const firstInput = overlay.querySelector('input, select, textarea');
        if (firstInput) setTimeout(() => firstInput.focus(), 100);
        } catch (err) {
            console.error('ChatManager: showSettingsDialog error:', err);
            throw err;
        }
    }

    function removeSettingsDialog() {
        if (settingsDialogEl) {
            settingsDialogEl.remove();
            settingsDialogEl = null;
        }
    }

    // ── Inject Styles ──────────────────────────────────────────────

    let _settingsStylesInjected = false;
    function injectSettingsStyles() {
        if (_settingsStylesInjected) return;
        _settingsStylesInjected = true;

        const style = document.createElement('style');
        style.textContent = `
            /* ── Settings Dialog ── */
            .chat-settings-overlay {
                position: fixed;
                inset: 0;
                background: rgba(0, 0, 0, 0.6);
                z-index: 500;
                display: flex;
                align-items: center;
                justify-content: center;
                padding: 16px;
                animation: fadeIn 0.2s ease;
            }
            @keyframes fadeIn { from { opacity: 0; } to { opacity: 1; } }

            .chat-settings-dialog {
                background: var(--bg-surface);
                border: 1px solid var(--border);
                border-radius: var(--radius);
                width: 100%;
                max-width: 380px;
                max-height: 85vh;
                display: flex;
                flex-direction: column;
                box-shadow: 0 16px 48px rgba(0,0,0,0.5);
                overflow: hidden;
            }
            .chat-settings-header {
                display: flex;
                align-items: center;
                justify-content: space-between;
                padding: 14px 16px 8px;
                font-weight: 600;
                font-size: 15px;
                color: var(--text-primary);
                flex-shrink: 0;
            }
            .chat-settings-close {
                width: 28px;
                height: 28px;
                border: none;
                background: none;
                color: var(--text-muted);
                font-size: 16px;
                cursor: pointer;
                border-radius: var(--radius-sm);
                display: flex;
                align-items: center;
                justify-content: center;
            }
            .chat-settings-close:active { background: var(--bg-hover); }
            .chat-settings-body {
                padding: 8px 16px 14px;
                overflow-y: auto;
                flex: 1;
                display: flex;
                flex-direction: column;
                gap: 10px;
            }
            .chat-settings-body label {
                display: flex;
                flex-direction: column;
                gap: 4px;
                font-size: 12px;
                color: var(--text-secondary);
            }
            .chat-settings-body label.full-width { flex: 1; }
            .chat-settings-body label span {
                font-weight: 500;
                color: var(--text-primary);
                font-size: 12px;
            }
            .chat-settings-body label .hint {
                font-size: 10px;
                color: var(--text-muted);
                font-style: italic;
            }
            .chat-settings-body input,
            .chat-settings-body select,
            .chat-settings-body textarea {
                width: 100%;
                padding: 8px 10px;
                border: 1px solid var(--border);
                background: var(--bg-tertiary);
                color: var(--text-primary);
                border-radius: var(--radius-sm);
                font-size: 13px;
                font-family: var(--font-mono);
            }
            .chat-settings-body input:focus,
            .chat-settings-body select:focus,
            .chat-settings-body textarea:focus {
                border-color: var(--accent);
                outline: none;
            }
            .chat-settings-body textarea {
                resize: vertical;
                min-height: 80px;
            }
            .chat-settings-footer {
                display: flex;
                justify-content: flex-end;
                gap: 8px;
                padding: 8px 16px 14px;
                flex-shrink: 0;
            }
            .chat-settings-footer button {
                padding: 8px 16px;
                border: none;
                border-radius: var(--radius-sm);
                cursor: pointer;
                font-size: 13px;
                font-weight: 500;
            }
            .chat-settings-footer .btn-cancel {
                background: var(--bg-hover);
                color: var(--text-secondary);
            }
            .chat-settings-footer .btn-cancel:active { background: var(--bg-active); }
            .chat-settings-footer .btn-confirm {
                background: var(--accent);
                color: var(--bg-primary);
            }
            .chat-settings-footer .btn-confirm:active { background: var(--accent-hover); }

            /* ── Multi-Model Card Styles ── */
            .llm-models-list {
                display: flex;
                flex-direction: column;
                gap: 8px;
            }
            .llm-model-card {
                border: 1px solid var(--border);
                border-radius: var(--radius-sm);
                background: var(--bg-tertiary);
                overflow: hidden;
                transition: border-color 0.15s;
            }
            .llm-model-enabled {
                border-color: var(--accent);
                border-width: 1.5px;
            }
            .llm-model-card-header {
                display: flex;
                align-items: center;
                justify-content: space-between;
                padding: 8px 10px;
                gap: 8px;
            }
            .llm-model-toggle-area {
                display: flex;
                align-items: center;
                gap: 8px;
                flex: 1;
                min-width: 0;
            }
            .llm-enable-btn {
                border: none;
                background: none;
                cursor: pointer;
                font-size: 16px;
                padding: 0;
                line-height: 1;
                flex-shrink: 0;
            }
            .llm-model-name {
                flex: 1;
                min-width: 0;
                border: none;
                background: transparent;
                color: var(--text-primary);
                font-size: 13px;
                font-weight: 600;
                padding: 2px 4px;
                border-bottom: 1px solid transparent;
                font-family: var(--font-mono);
            }
            .llm-model-name:focus {
                outline: none;
                border-bottom-color: var(--accent);
            }
            .llm-model-card-actions {
                display: flex;
                gap: 4px;
                flex-shrink: 0;
            }
            .llm-model-card-actions button {
                border: none;
                background: none;
                cursor: pointer;
                font-size: 14px;
                padding: 2px 4px;
                border-radius: var(--radius-sm);
                opacity: 0.6;
            }
            .llm-model-card-actions button:hover { opacity: 1; }
            .llm-model-card-actions button:active { background: var(--bg-hover); }
            .llm-model-card-body {
                padding: 0 10px 10px;
            }
            .llm-model-fields {
                display: flex;
                flex-direction: column;
                gap: 8px;
            }
            .llm-model-params {
                display: flex;
                gap: 8px;
            }
            .llm-model-params label {
                flex: 1;
            }
            .llm-add-model-btn {
                width: 100%;
                padding: 8px;
                border: 1px dashed var(--border);
                border-radius: var(--radius-sm);
                background: none;
                color: var(--text-secondary);
                font-size: 12px;
                cursor: pointer;
                font-family: var(--font-mono);
            }
            .llm-add-model-btn:hover { background: var(--bg-hover); color: var(--text-primary); }
            .llm-add-model-btn:active { background: var(--bg-active); }
            .llm-test-model-btn:disabled { opacity: 0.4; }

            /* ── Code Block Wrapper ── */
            .code-block-wrapper {
                position: relative;
                margin: 6px 0;
                border-radius: var(--radius-sm);
                overflow: hidden;
            }
            .code-block-wrapper pre {
                margin: 0 !important;
                border-radius: var(--radius-sm) !important;
            }
            .code-block-wrapper .code-lang {
                position: absolute;
                top: 4px;
                left: 8px;
                font-size: 10px;
                color: var(--text-muted);
                font-family: var(--font-mono);
                z-index: 1;
                pointer-events: none;
            }
            .code-copy-btn {
                position: absolute;
                top: 4px;
                right: 4px;
                border: none;
                background: var(--bg-active);
                color: var(--text-secondary);
                font-size: 12px;
                padding: 2px 6px;
                border-radius: var(--radius-sm);
                cursor: pointer;
                opacity: 0.7;
                z-index: 1;
                line-height: 1;
            }
            .code-copy-btn:hover { opacity: 1; }
            .code-copy-btn:active { transform: scale(0.9); }

            /* ── Tool Messages ── */
            .tool-header {
                display: flex;
                align-items: center;
                justify-content: space-between;
                gap: 8px;
            }
            .tool-name {
                font-weight: 500;
                font-size: 12px;
            }
            .tool-status {
                font-size: 12px;
                font-weight: bold;
            }
            .tool-ok { color: var(--green); }
            .tool-fail { color: var(--red); }
            .tool-duration {
                font-size: 10px;
                color: var(--text-muted);
                font-family: var(--font-mono);
            }
            .tool-spinner {
                animation: spin 1s linear infinite;
                font-size: 14px;
            }
            @keyframes spin {
                from { transform: rotate(0deg); }
                to { transform: rotate(360deg); }
            }
            .tool-args {
                font-family: var(--font-mono);
                font-size: 11px;
                color: var(--text-muted);
                margin-top: 4px;
                padding: 4px 6px;
                background: rgba(0,0,0,0.2);
                border-radius: var(--radius-sm);
                word-break: break-all;
                cursor: default;
            }
            .tool-args.collapsed {
                max-height: 40px;
                overflow: hidden;
                position: relative;
            }
            .tool-args.expanded {
                max-height: none;
            }
            .tool-args.collapsed::after {
                content: '...';
                position: absolute;
                bottom: 0;
                right: 4px;
                background: linear-gradient(transparent, rgba(0,0,0,0.3));
                padding-left: 20px;
            }
            .tool-result {
                font-family: var(--font-mono);
                font-size: 11px;
                color: var(--text-secondary);
                margin-top: 4px;
                padding: 6px 8px;
                background: rgba(0,0,0,0.15);
                border-radius: var(--radius-sm);
                white-space: pre-wrap;
                word-break: break-word;
                max-height: 300px;
                overflow-y: auto;
            }
            .tool-result.tool-waiting {
                color: var(--text-muted);
                font-style: italic;
            }
            .tool-toggle-btn {
                display: inline-block;
                margin-top: 4px;
                padding: 2px 8px;
                border: none;
                background: var(--bg-hover);
                color: var(--accent);
                font-size: 11px;
                cursor: pointer;
                border-radius: var(--radius-sm);
            }
            .tool-toggle-btn:hover { background: var(--bg-active); }
            .tool-toggle-btn:active { transform: scale(0.95); }
            .tool-progress {
                border-left: 2px solid var(--accent);
            }

            /* ── Chat Role Badge ── */
            .chat-role-badge {
                font-size: 10px;
                color: var(--mauve);
                margin-bottom: 4px;
                font-weight: 500;
            }

            /* ── Turn Indicator ── */
            .chat-turn-indicator {
                font-size: 10px;
                color: var(--text-muted);
                text-align: center;
                padding: 4px 8px;
                font-family: var(--font-mono);
            }

            /* ── Stop Button ── */
            .chat-stop-btn {
                width: 100%;
                padding: 8px 0;
                border: 1px solid var(--red);
                background: rgba(255, 59, 48, 0.1);
                color: var(--red);
                font-size: 13px;
                font-weight: 600;
                cursor: pointer;
                border-radius: var(--radius-sm);
                margin-bottom: 6px;
                transition: background 0.15s ease;
            }
            .chat-stop-btn:hover { background: rgba(255, 59, 48, 0.2); }
            .chat-stop-btn:active { background: rgba(255, 59, 48, 0.3); transform: scale(0.98); }

            /* ── Retry Button ── */
            .chat-retry-btn {
                display: inline-flex;
                align-items: center;
                gap: 4px;
                border: none;
                background: none;
                color: var(--accent);
                font-size: 12px;
                cursor: pointer;
                padding: 4px 8px;
                border-radius: var(--radius-sm);
                margin-top: 6px;
            }
            .chat-retry-btn:hover { background: var(--bg-hover); }
            .chat-retry-btn:active { background: var(--bg-active); transform: scale(0.95); }

            /* ── System Message ── */
            .chat-system-msg {
                font-size: 12px;
                color: var(--text-muted);
                font-style: italic;
            }

            /* ── Streaming ── */
            .chat-streaming {
                min-height: 1em;
            }
            .chat-streaming::after {
                content: '▊';
                animation: blink 0.8s steps(2) infinite;
                color: var(--accent);
                font-size: 0.9em;
            }
            @keyframes blink {
                0%, 100% { opacity: 1; }
                50% { opacity: 0; }
            }

            /* ── Markdown headings ── */
            .md-h1 { font-size: 16px; display: block; margin: 8px 0 4px; }
            .md-h2 { font-size: 14px; display: block; margin: 6px 0 3px; }
            .md-h3 { font-size: 13px; display: block; margin: 4px 0 2px; }

            /* ── Markdown links ── */
            .chat-content a,
            .chat-msg a {
                color: var(--accent);
                text-decoration: underline;
                text-underline-offset: 2px;
            }
            .chat-content a:hover,
            .chat-msg a:hover {
                opacity: 0.85;
            }

            /* ── Markdown blockquote ── */
            blockquote {
                border-left: 3px solid var(--accent);
                margin: 6px 0;
                padding: 4px 10px;
                color: var(--text-muted);
                font-style: italic;
            }

            /* ── Markdown hr ── */
            hr {
                border: none;
                border-top: 1px solid var(--border);
                margin: 8px 0;
            }

            /* ── Re-send button ── */
            .chat-resend-btn {
                display: inline-flex;
                align-items: center;
                gap: 4px;
                border: none;
                background: none;
                color: var(--text-muted);
                font-size: 11px;
                cursor: pointer;
                padding: 2px 6px;
                border-radius: var(--radius-sm);
                margin-top: 4px;
            }
            .chat-resend-btn:hover { color: var(--accent); }
            .chat-resend-btn:active { background: var(--bg-hover); }

            /* ── Mode Toggle ── */
            .chat-mode-toggle {
                display: flex;
                align-items: center;
                gap: 6px;
                margin: 4px 0;
                flex-shrink: 0;
            }
            .mode-label {
                color: var(--text-muted);
                font-size: 11px;
                font-weight: 500;
                white-space: nowrap;
            }
            .mode-select {
                padding: 3px 8px;
                border: 1px solid var(--border);
                border-radius: 6px;
                background: var(--bg-tertiary);
                color: var(--text-primary);
                font-size: 11px;
                font-weight: 500;
                cursor: pointer;
                outline: none;
                transition: border-color 0.2s;
            }
            .mode-select:focus {
                border-color: var(--accent);
            }

            /* ── Plan Actions ── */
            .plan-actions {
                display: flex;
                gap: 8px;
                margin-top: 8px;
                flex-wrap: wrap;
            }
            .plan-btn {
                padding: 6px 14px;
                border: none;
                border-radius: var(--radius-sm);
                font-size: 12px;
                font-weight: 500;
                cursor: pointer;
                transition: all 0.15s ease;
                white-space: nowrap;
            }
            .plan-btn:active { transform: scale(0.96); }
            .plan-btn-edit {
                background: var(--bg-hover);
                color: var(--text-secondary);
                border: 1px solid var(--border);
            }
            .plan-btn-edit:hover { background: var(--bg-active); color: var(--text-primary); }
            .plan-btn-approve {
                background: var(--green);
                color: #fff;
            }
            .plan-btn-approve:hover { opacity: 0.85; }
            .plan-btn-save {
                background: var(--accent);
                color: var(--bg-primary);
            }
            .plan-btn-save:hover { opacity: 0.85; }
            .plan-btn-cancel {
                background: var(--bg-hover);
                color: var(--text-muted);
                border: 1px solid var(--border);
            }
            .plan-btn-cancel:hover { background: var(--bg-active); }

            /* ── Plan Editor ── */
            .plan-edit-area {
                margin-top: 8px;
            }
            .plan-textarea {
                width: 100%;
                min-height: 150px;
                padding: 8px 10px;
                border: 1px solid var(--border);
                background: var(--bg-tertiary);
                color: var(--text-primary);
                border-radius: var(--radius-sm);
                font-size: 12px;
                font-family: var(--font-mono);
                resize: vertical;
                line-height: 1.5;
                box-sizing: border-box;
            }
            .plan-textarea:focus {
                border-color: var(--accent);
                outline: none;
            }
            .plan-editor-btns {
                display: flex;
                gap: 8px;
                margin-top: 6px;
            }

            /* ── Conversation History Dialog ── */
            .conv-empty {
                text-align: center;
                color: var(--text-muted);
                padding: 40px 16px;
                font-size: 13px;
            }
            .conv-item {
                display: flex;
                align-items: center;
                justify-content: space-between;
                padding: 10px 12px;
                border-bottom: 1px solid var(--border);
                gap: 8px;
            }
            .conv-item:last-child { border-bottom: none; }
            .conv-info {
                flex: 1;
                min-width: 0;
            }
            .conv-title {
                font-size: 13px;
                font-weight: 500;
                color: var(--text-primary);
                white-space: nowrap;
                overflow: hidden;
                text-overflow: ellipsis;
            }
            .conv-meta {
                font-size: 11px;
                color: var(--text-muted);
                margin-top: 2px;
            }
            .conv-actions {
                display: flex;
                gap: 4px;
                flex-shrink: 0;
            }
            .conv-actions button {
                border: none;
                background: none;
                cursor: pointer;
                font-size: 14px;
                padding: 4px 6px;
                border-radius: var(--radius-sm);
                opacity: 0.7;
            }
            .conv-actions button:hover { opacity: 1; }
            .conv-actions button:active { background: var(--bg-hover); }
            .conv-delete-btn:hover { color: var(--red); }
        `;
        document.head.appendChild(style);
    }

    // ── Input Auto-Resize ──────────────────────────────────────────

    function autoResizeInput() {
        const input = document.getElementById('chat-input');
        if (!input) return;
        input.style.height = 'auto';
        input.style.height = Math.min(input.scrollHeight, 120) + 'px';
    }

    // ── Plan Mode Actions ──────────────────────────────────────────

    /**
     * Inject plan action buttons (Edit Plan / Approve & Execute) below a plan message
     */
    function injectPlanActions(msgEl, planMarkdown) {
        // Remove any existing plan actions
        const existing = msgEl.querySelector('.plan-actions');
        if (existing) existing.remove();

        // Also remove any existing textarea for editing
        const existingTa = msgEl.querySelector('.plan-edit-area');
        if (existingTa) existingTa.remove();

        const actionsDiv = document.createElement('div');
        actionsDiv.className = 'plan-actions';

        const editBtn = document.createElement('button');
        editBtn.className = 'plan-btn plan-btn-edit';
        editBtn.textContent = '✏️ 修改计划';
        editBtn.addEventListener('click', () => {
            showPlanEditor(msgEl, planContent);
        });

        const approveBtn = document.createElement('button');
        approveBtn.className = 'plan-btn plan-btn-approve';
        approveBtn.textContent = '✅ 批准并执行';
        approveBtn.addEventListener('click', () => {
            // Get the current plan content (may have been edited)
            const editTa = msgEl.querySelector('.plan-edit-area textarea');
            const currentPlan = editTa ? editTa.value : planContent;
            // Remove plan action buttons
            const actions = msgEl.querySelector('.plan-actions');
            if (actions) actions.remove();
            // Remove editor if present
            const editor = msgEl.querySelector('.plan-edit-area');
            if (editor) editor.remove();
            // Switch to execute mode and send the plan
            chatMode = 'execute';
            updateModeToggleUI();
            addMessage('system', '📋 计划已批准，开始执行...');
            sendMessage('Please execute the following plan:\n\n' + currentPlan);
        });

        actionsDiv.appendChild(editBtn);
        actionsDiv.appendChild(approveBtn);

        // Insert before the timestamp
        const timeEl = msgEl.querySelector('.chat-time');
        if (timeEl) {
            msgEl.insertBefore(actionsDiv, timeEl);
        } else {
            msgEl.appendChild(actionsDiv);
        }

        forceScrollToBottom();
    }

    /**
     * Show a textarea editor for the plan content inside the message element
     */
    function showPlanEditor(msgEl, markdown) {
        // Remove existing editor if any
        const existingTa = msgEl.querySelector('.plan-edit-area');
        if (existingTa) existingTa.remove();

        // Hide the rendered content temporarily
        const contentEl = msgEl.querySelector('.chat-content');
        if (contentEl) contentEl.style.display = 'none';

        // Hide action buttons
        const actions = msgEl.querySelector('.plan-actions');
        if (actions) actions.style.display = 'none';

        const editorDiv = document.createElement('div');
        editorDiv.className = 'plan-edit-area';

        const textarea = document.createElement('textarea');
        textarea.className = 'plan-textarea';
        textarea.value = markdown || '';
        textarea.placeholder = 'Edit the plan...';
        textarea.rows = 12;

        const editorBtns = document.createElement('div');
        editorBtns.className = 'plan-editor-btns';

        const saveBtn = document.createElement('button');
        saveBtn.className = 'plan-btn plan-btn-save';
        saveBtn.textContent = '💾 保存修改';
        saveBtn.addEventListener('click', () => {
            planContent = textarea.value;
            // Update the rendered content
            if (contentEl) {
                contentEl.innerHTML = renderMarkdownLite(planContent);
                contentEl.style.display = '';
                bindCopyButtons(contentEl);
            }
            editorDiv.remove();
            // Show actions again
            if (actions) actions.style.display = '';
            // Re-inject plan actions with updated content
            injectPlanActions(msgEl, planContent);
        });

        const cancelBtn = document.createElement('button');
        cancelBtn.className = 'plan-btn plan-btn-cancel';
        cancelBtn.textContent = '❌ 取消';
        cancelBtn.addEventListener('click', () => {
            if (contentEl) contentEl.style.display = '';
            editorDiv.remove();
            if (actions) actions.style.display = '';
        });

        editorBtns.appendChild(saveBtn);
        editorBtns.appendChild(cancelBtn);

        editorDiv.appendChild(textarea);
        editorDiv.appendChild(editorBtns);

        // Insert before the actions
        if (actions) {
            msgEl.insertBefore(editorDiv, actions);
        } else {
            const timeEl = msgEl.querySelector('.chat-time');
            if (timeEl) {
                msgEl.insertBefore(editorDiv, timeEl);
            } else {
                msgEl.appendChild(editorDiv);
            }
        }

        forceScrollToBottom();
    }

    // ── Conversation Management ────────────────────────────────────

    let historyDialogEl = null;

    async function loadConversation(convId) {
        try {
            const resp = await fetch(`/api/conversations/${convId}`);
            if (!resp.ok) throw new Error(`Failed to load conversation: ${resp.statusText}`);
            const conv = await resp.json();
            currentConvId = conv.id;
            messages = conv.messages || [];
            renderMessages(messages);
            showToast('已加载会话: ' + truncate(conv.title || 'New Chat', 30), 'success');
        } catch (err) {
            showToast('加载会话失败: ' + err.message, 'error');
        }
    }

    async function deleteConversation(convId) {
        try {
            const resp = await fetch(`/api/conversations/${convId}`, { method: 'DELETE' });
            if (!resp.ok) throw new Error(`Failed: ${resp.statusText}`);
            await resp.json();
            // If deleted the current conversation, clear
            if (currentConvId === convId) {
                currentConvId = null;
                messages = [];
                renderMessages([]);
            }
            showToast('会话已删除', 'success');
            // Refresh the list if dialog is open
            if (historyDialogEl) {
                historyDialogEl.remove();
                historyDialogEl = null;
                showHistoryDialog();
            }
        } catch (err) {
            showToast('删除失败: ' + err.message, 'error');
        }
    }

    async function showHistoryDialog() {
        if (historyDialogEl) {
            historyDialogEl.remove();
            historyDialogEl = null;
            return;
        }
        try {
            const resp = await fetch('/api/conversations');
            const data = await resp.json();
            const convs = data.conversations || [];

            const overlay = document.createElement('div');
            overlay.className = 'chat-settings-overlay';
            overlay.id = 'chat-history-overlay';

            let listHTML = '';
            if (convs.length === 0) {
                listHTML = '<div class="conv-empty">暂无历史会话</div>';
            } else {
                for (const c of convs) {
                    const dateStr = c.updated_at ? formatTime(c.updated_at) : '';
                    listHTML += `
                        <div class="conv-item" data-id="${escapeAttr(c.id)}">
                            <div class="conv-info">
                                <div class="conv-title">${escapeHTML(c.title || 'New Chat')}</div>
                                <div class="conv-meta">${dateStr} · ${c.message_count} 条消息</div>
                            </div>
                            <div class="conv-actions">
                                <button class="conv-continue-btn" data-id="${escapeAttr(c.id)}" title="继续此会话">▶</button>
                                <button class="conv-delete-btn" data-id="${escapeAttr(c.id)}" title="删除此会话">🗑</button>
                            </div>
                        </div>`;
                }
            }

            overlay.innerHTML = `
                <div class="chat-settings-dialog" style="max-width:420px">
                    <div class="chat-settings-header">
                        <span>📋 历史会话</span>
                        <button class="chat-settings-close" title="Close">✕</button>
                    </div>
                    <div class="chat-settings-body" id="conv-list-body" style="max-height:60vh">
                        ${listHTML}
                    </div>
                </div>
            `;

            document.body.appendChild(overlay);
            historyDialogEl = overlay;
            injectSettingsStyles();

            // Bind close
            overlay.querySelector('.chat-settings-close').addEventListener('click', () => {
                overlay.remove();
                historyDialogEl = null;
            });
            overlay.addEventListener('click', (e) => {
                if (e.target === overlay) {
                    overlay.remove();
                    historyDialogEl = null;
                }
            });

            // Bind continue buttons
            overlay.querySelectorAll('.conv-continue-btn').forEach(btn => {
                btn.addEventListener('click', async () => {
                    const id = btn.dataset.id;
                    overlay.remove();
                    historyDialogEl = null;
                    await loadConversation(id);
                });
            });

            // Bind delete buttons
            overlay.querySelectorAll('.conv-delete-btn').forEach(btn => {
                btn.addEventListener('click', () => {
                    const id = btn.dataset.id;
                    deleteConversation(id);
                });
            });

        } catch (err) {
            showToast('加载历史会话失败: ' + err.message, 'error');
        }
    }

    // ── Wire Up Events ─────────────────────────────────────────────

    function wireEvents() {
        // Send button
        const sendBtn = document.getElementById('chat-send');
        if (sendBtn) {
            sendBtn.addEventListener('click', (e) => {
                e.preventDefault();
                sendMessage();
            });
        }

        // Chat input
        const input = document.getElementById('chat-input');
        if (input) {
            input.addEventListener('keydown', (e) => {
                if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
                    e.preventDefault();
                    sendMessage();
                }
            });
            input.addEventListener('input', autoResizeInput);
        }

        // New chat button
        const newChatBtn = document.getElementById('chat-new-chat');
        if (newChatBtn) {
            newChatBtn.addEventListener('click', (e) => {
                e.preventDefault();
                newChat();
            });
        }

        // History button (replaces clear button)
        const historyBtn = document.getElementById('chat-history-btn');
        if (historyBtn) {
            historyBtn.addEventListener('click', (e) => {
                e.preventDefault();
                showHistoryDialog();
            });
        }

        // Mode toggle
        wireModeToggle();

        // Model selector — init on load and refresh after settings save
        refreshModelSelector();

        // Delegate click events on chat messages
        const msgContainer = document.getElementById('chat-messages');
        if (msgContainer) {
            msgContainer.addEventListener('click', (e) => {
                if (e.target.classList.contains('code-copy-btn')) return;
                if (e.target.closest('.chat-resend-btn')) {
                    resendLastMessage();
                }
                if (e.target.closest('.chat-retry-btn')) {
                    // Handled by event listener on the button itself
                }
                // Toggle tool args expand/collapse
                if (e.target.closest('.tool-args.collapsed')) {
                    e.target.closest('.tool-args').classList.toggle('collapsed');
                    e.target.closest('.tool-args').classList.toggle('expanded');
                }
            });
        }

        // Initialize auto-scroll behavior
        initAutoScroll();
    }

    function wireModeToggle() {
        const header = document.getElementById('sidebar-right-header');
        if (!header) return;
        if (header.querySelector('.chat-mode-toggle')) return;

        // Find the title element and insert toggle after it
        const titleEl = header.querySelector('h3, .sidebar-title');
        if (!titleEl) return;

        const wrapper = document.createElement('div');
        wrapper.className = 'chat-mode-toggle';
        wrapper.id = 'chat-mode-toggle';

        const label = document.createElement('span');
        label.className = 'mode-label';
        label.textContent = '模式';

        const select = document.createElement('select');
        select.id = 'chat-mode-select';
        select.className = 'mode-select';

        const planOpt = document.createElement('option');
        planOpt.value = 'plan';
        planOpt.textContent = '📋 计划';
        if (chatMode === 'plan') planOpt.selected = true;

        const execOpt = document.createElement('option');
        execOpt.value = 'execute';
        execOpt.textContent = '⚡ 执行';
        if (chatMode === 'execute') execOpt.selected = true;

        select.appendChild(planOpt);
        select.appendChild(execOpt);

        select.addEventListener('change', () => {
            chatMode = select.value;
        });

        wrapper.appendChild(label);
        wrapper.appendChild(select);

        // Insert after the title
        titleEl.parentNode.insertBefore(wrapper, titleEl.nextSibling);
    }

    function updateModeToggleUI() {
        const select = document.getElementById('chat-mode-select');
        if (select) select.value = chatMode;
    }

    // ── Initialize ─────────────────────────────────────────────────

    async function init() {
        wireEvents();
        await loadHistory();
        autoResizeInput();
        console.log('ChatManager: initialized (SSE streaming enabled)');
    }

    // Auto-init when DOM is ready
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }

    // ── Public API ─────────────────────────────────────────────────
    return {
        renderMessages,
        addMessage,
        sendMessage,
        clearHistory,
        newChat,
        loadHistory,
        scrollToBottom: forceScrollToBottom,
        showSettingsDialog,
        removeSettingsDialog,
        loadLLMConfig,
        saveLLMConfig,
        resendLastMessage,
        showTyping,
        hideTyping,
        setProcessing,
        setExecuteStatus,
        renderMarkdownLite,
        bindCopyButtons,
        autoResizeInput,
        abortGeneration,
        injectPlanActions,
        showSettingsDialog,
        showPlanEditor,
        showHistoryDialog,
        loadConversation,

        // Getters
        get isProcessing() { return isProcessing; },
        get messages() { return messages.slice(); },
        get lastUserMessage() { return lastUserMessage; },
        get currentConvId() { return currentConvId; },

        // Mode control
        get chatMode() { return chatMode; },
        set chatMode(mode) { chatMode = mode; updateModeToggleUI(); },
    };
})();

window.ChatManager = ChatManager;
