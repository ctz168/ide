---
Task ID: 1
Agent: Main
Task: Fix proxy mode CSS/JS not loading + reduce editor font size + fix multi-line selection

Work Log:
- Diagnosed proxy issue: `_proxy_url()` produced root-relative URLs like `/api/browser/proxy?url=...`, but the injected `<base href="http://localhost:8767/">` tag caused the browser to resolve them against the target server instead of the PhoneIDE server (e.g., `http://localhost:8767/api/browser/proxy?...` → 404)
- Fixed by making `proxy_base` include the PhoneIDE server origin (`http://localhost:12345/api/browser/proxy?url=...`), and updating `_proxy_url()` to always return absolute URLs
- Changed `proxy()` function to construct `proxy_base` using `request.host_url.rstrip('/')` as origin prefix
- Updated `_proxy_url()` to extract origin from proxy_base and prepend it to all generated proxy URLs
- Applied same fix to the HTTPError fallback path
- Reduced CodeMirror font-size from 11px to 10px
- Reduced CodeMirror line number font-size from 10px to 9px
- Changed `touch-action` from `pan-y pan-x pinch-zoom` to `manipulation` to allow text selection gestures
- Verified breakpoint code is correctly wired (gutterClick → DebuggerUI.toggleBreakpoint → setGutterMarker)

Stage Summary:
- Proxy fix: routes/browser.py — all rewritten URLs now absolute, immune to <base> tag interference
- Font size: static/css/style.css — editor 10px, line numbers 9px
- Multi-line selection: touch-action changed to manipulation
- Breakpoints: confirmed working (gutter + DebuggerUI integration verified)
- Server restarted successfully on port 12345
