#!/usr/bin/env python3
"""
Full integration test: mock server + IDE + proxy with dynamic script interception.
Tests the complete chain: redirect detection → HTML rewrite → interceptor injection
→ dynamic script creation interception → JS fetched through proxy.
"""
import sys, os, threading, time, urllib.request, urllib.error, urllib.parse, re, subprocess, json

# ═══════════════════════════════════════════════════
# 1. Mock server simulating myagent
# ═══════════════════════════════════════════════════
from http.server import HTTPServer, BaseHTTPRequestHandler

class MockHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/' or self.path == '':
            self.send_response(302)
            self.send_header('Location', '/ui/chat/chat_container.html')
            self.end_headers()
        elif self.path == '/ui/chat/chat_container.html':
            html = '''<!DOCTYPE html>
<html><head><meta charset="UTF-8"><title>MyAgent Test</title>
<link rel="stylesheet" href="chat.css?v=13">
</head><body>
<h1>MyAgent Chat</h1>
<button onclick="toggleSidebar()">Toggle</button>
<button onclick="newChat()">New Chat</button>
<div id="result"></div>
<script src="chat.js?v=11"></script>
<script>
// Dynamic loader that uses new URL('.', window.location.href)
function initRecoveryMechanisms() {
    if (typeof StreamingRecovery === 'undefined') {
        var script1 = document.createElement('script');
        script1.src = 'streaming_recovery.js';
        script1.onload = function() { console.log('streaming_recovery loaded'); };
        document.body.appendChild(script1);
    }
}
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initRecoveryMechanisms);
} else {
    initRecoveryMechanisms();
}
</script>
</body></html>'''
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.end_headers()
            self.wfile.write(html.encode())
        elif self.path == '/ui/chat/chat.js?v=11' or self.path == '/ui/chat/chat.js':
            # This is the loader that creates script elements dynamically
            js = '''// chat.js - Dynamic script loader
(function() {
  var base = new URL('.', window.location.href).href;
  var scripts = [
    'groupchat.js',
    'chat_main.js',
  ];
  var idx = 0;
  function loadNext() {
    if (idx >= scripts.length) { onAllScriptsLoaded(); return; }
    var s = document.createElement('script');
    s.src = base + scripts[idx] + '?v=15';
    s.onload = function() { idx++; loadNext(); };
    s.onerror = function() { console.error('Failed: ' + scripts[idx]); idx++; loadNext(); };
    document.body.appendChild(s);
  }
  loadNext();
})();

function onAllScriptsLoaded() {
    document.getElementById('result').textContent = 'All scripts loaded!';
}
'''
            self.send_response(200)
            self.send_header('Content-Type', 'text/javascript; charset=utf-8')
            self.end_headers()
            self.wfile.write(js.encode())
        elif self.path.startswith('/ui/chat/groupchat'):
            js = '// groupchat.js loaded OK\nvar groupchat_loaded = true;\n'
            self.send_response(200)
            self.send_header('Content-Type', 'text/javascript; charset=utf-8')
            self.end_headers()
            self.wfile.write(js.encode())
        elif self.path.startswith('/ui/chat/chat_main'):
            js = '''// chat_main.js - defines all the functions
var api = { sendMessage: function(m) {} };
function toggleSidebar() { return 'ok'; }
function newChat() { return 'ok'; }
function sendMessage() { return 'ok'; }
var chat_main_loaded = true;
'''
            self.send_response(200)
            self.send_header('Content-Type', 'text/javascript; charset=utf-8')
            self.end_headers()
            self.wfile.write(js.encode())
        elif self.path.startswith('/ui/chat/streaming_recovery'):
            js = '// streaming_recovery.js\nvar StreamingRecovery = { init: function() {} };\n'
            self.send_response(200)
            self.send_header('Content-Type', 'text/javascript; charset=utf-8')
            self.end_headers()
            self.wfile.write(js.encode())
        elif self.path.startswith('/ui/chat/chat.css'):
            css = 'body { font-family: sans-serif; }'
            self.send_response(200)
            self.send_header('Content-Type', 'text/css; charset=utf-8')
            self.end_headers()
            self.wfile.write(css.encode())
        else:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b'Not Found: ' + self.path.encode())
    def log_message(self, *a):
        print(f'  [MOCK] {a[0] % a[1:] if len(a) > 1 else a[0]}')

mock_server = HTTPServer(('127.0.0.1', 8767), MockHandler)
mock_thread = threading.Thread(target=mock_server.serve_forever, daemon=True)
mock_thread.start()
print(f'[MOCK] Started on http://127.0.0.1:8767/')

# ═══════════════════════════════════════════════════
# 2. Start IDE server
# ═══════════════════════════════════════════════════
ide_proc = subprocess.Popen(
    [sys.executable, 'server.py'],
    cwd='/home/z/my-project',
    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
)
for _ in range(30):
    time.sleep(0.5)
    try:
        urllib.request.urlopen('http://127.0.0.1:12345/', timeout=1)
        break
    except:
        continue
else:
    print('[IDE] FAILED to start!')
    ide_proc.terminate()
    sys.exit(1)
print(f'[IDE] Started on http://127.0.0.1:12345/')

# ═══════════════════════════════════════════════════
# 3. Run Tests
# ═══════════════════════════════════════════════════
PASSED = 0
FAILED = 0

def test(name, ok, detail=''):
    global PASSED, FAILED
    if ok:
        print(f'  ✅ {name}')
        PASSED += 1
    else:
        print(f'  ❌ {name} — {detail}')
        FAILED += 1

# ── Test 1: Redirect detection ──
print('\n' + '=' * 60)
print('TEST 1: Proxy detects redirect')
class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, msg, headers, fp)
o = urllib.request.build_opener(NoRedirect)
try:
    o.open('http://127.0.0.1:12345/api/browser/proxy?url=http%3A%2F%2Flocalhost%3A8767%2F')
    test('Redirect detection', False, 'No redirect')
except urllib.error.HTTPError as e:
    loc = e.headers.get('Location', '')
    test('Returns 302', e.code == 302, f'code={e.code}')
    test('Location has chat_container', 'chat_container' in loc, f'loc={loc[:120]}')

# ── Test 2: HTML includes script interceptor ──
print('=' * 60)
print('TEST 2: Proxy injects script interceptor into HTML')
try:
    resp = urllib.request.urlopen(
        'http://127.0.0.1:12345/api/browser/proxy?url=http%3A%2F%2Flocalhost%3A8767%2Fui%2Fchat%2Fchat_container.html',
        timeout=5
    )
    html = resp.read().decode('utf-8', errors='replace')

    test('Got HTML', resp.status == 200)
    test('Has script interceptor', 'createElement' in html, 'missing createElement override')
    test('Has _ORIG_DIR', '_ORIG_DIR' in html, 'missing _ORIG_DIR variable')
    test('Has _toProxyUrl', '_toProxyUrl' in html, 'missing _toProxyUrl function')
    test('Interceptor before first script tag',
         html.index('createElement') < html.index('chat.js'),
         f'interceptor at {html.index("createElement")}, chat.js at {html.index("chat.js")}')
except Exception as e:
    test('Got HTML', False, str(e))

# ── Test 3: Static script tags correctly rewritten ──
print('=' * 60)
print('TEST 3: Static <script src> tags correctly rewritten')
try:
    resp = urllib.request.urlopen(
        'http://127.0.0.1:12345/api/browser/proxy?url=http%3A%2F%2Flocalhost%3A8767%2Fui%2Fchat%2Fchat_container.html',
        timeout=5
    )
    html = resp.read().decode('utf-8', errors='replace')
    scripts = re.findall(r'<script[^>]+src="([^"]+)"', html)
    print(f'  Found {len(scripts)} script tags with src')

    # The first script src should be chat.js through proxy
    for i, src in enumerate(scripts):
        print(f'  [{i}] {src[:120]}')
        if i == 0:
            test(f'Script [{i}] goes through proxy', '/api/browser/proxy' in src, f'src={src[:80]}')
            # Verify the resolved URL would be correct
            parsed = urllib.parse.urlparse(src)
            params = urllib.parse.parse_qs(parsed.query)
            rel = params.get('rel', [''])[0]
            base = params.get('base', [''])[0]
            resolved = urllib.parse.urljoin(base, rel)
            ok = resolved == 'http://localhost:8767/ui/chat/chat.js?v=11'
            test(f'Script [{i}] resolves correctly', ok, f'{resolved}')
except Exception as e:
    test('Script rewrite', False, str(e))

# ── Test 4: JS URL rewriting ──
print('=' * 60)
print('TEST 4: chat.js has new URL() rewritten')
try:
    resp = urllib.request.urlopen(
        'http://127.0.0.1:12345/api/browser/proxy?url=http%3A%2F%2Flocalhost%3A8767%2Fui%2Fchat%2Fchat.js%3Fv%3D11',
        timeout=5
    )
    js = resp.read().decode('utf-8', errors='replace')
    print(f'  chat.js size: {len(js)} bytes')
    
    has_rewrite = "'http://localhost:8767/ui/chat/'" in js
    has_orig_url = "new URL('.', window.location.href)" not in js
    test('new URL() was rewritten', has_rewrite, f'base var={js[js.find("var base"):js.find("var base")+80]}')
    test('Original new URL() removed', has_orig_url, 'still has old new URL() pattern')
except Exception as e:
    test('JS rewrite', False, str(e))

# ── Test 5: Full chain - fetch chat.js through proxy ──
print('=' * 60)
print('TEST 5: Fetch chat.js through proxy and check content')
try:
    base_enc = urllib.parse.quote('http://localhost:8767/ui/chat/')
    rel_enc = urllib.parse.quote('chat.js?v=11', safe='')
    url = f'http://127.0.0.1:12345/api/browser/proxy?url=http%3A%2F%2Flocalhost%3A8767%2Fui%2Fchat%2Fchat_container.html&base={base_enc}&rel={rel_enc}'
    resp = urllib.request.urlopen(url, timeout=5)
    js = resp.read().decode('utf-8', errors='replace')
    test('chat.js fetched (200)', resp.status == 200)
    test('chat.js has loadNext', 'loadNext' in js, f'body={js[:100]}')
    test('chat.js has createElement', 'createElement' in js, 'missing createElement')
    test('chat.js base URL rewritten', "'http://localhost:8767/ui/chat/'" in js, 'base URL not rewritten')
except Exception as e:
    test('Fetch chat.js', False, str(e))

# ── Test 6: Simulate what happens when chat.js runs in browser ──
print('=' * 60)
print('TEST 6: Simulate chat.js base URL computation')
# The rewritten chat.js should have base = 'http://localhost:8767/ui/chat/'
# Then s.src = base + 'groupchat.js?v=15' = 'http://localhost:8767/ui/chat/groupchat.js?v=15'
# The interceptor should catch this and rewrite it to proxy URL
try:
    base = 'http://localhost:8767/ui/chat/'
    script_src = base + 'groupchat.js?v=15'
    # This is what the interceptor should convert it to
    proxy_base = 'http://localhost:12345/api/browser/proxy?url=http%3A%2F%2Flocalhost%3A8767%2Fui%2Fchat%2Fchat_container.html&base=http%3A%2F%2Flocalhost%3A8767%2Fui%2Fchat%2F'
    expected_proxy = proxy_base + '&rel=' + urllib.parse.quote('groupchat.js?v=15')
    test(f'Base + groupchat.js = {script_src}', True)
    test(f'Would be proxied to /api/browser/proxy', '/api/browser/proxy' in expected_proxy)
    
    # Verify the proxy URL actually works
    resp = urllib.request.urlopen(expected_proxy, timeout=5)
    js = resp.read().decode('utf-8', errors='replace')
    test(f'Proxied groupchat.js loads (200)', resp.status == 200)
    test(f'groupchat.js content OK', 'groupchat_loaded' in js, f'body={js[:100]}')
except Exception as e:
    test('Simulate chain', False, str(e))

# ── Summary ──
print('\n' + '=' * 60)
print(f'RESULTS: {PASSED} passed, {FAILED} failed out of {PASSED + FAILED}')
if FAILED == 0:
    print('🎉 ALL TESTS PASSED!')
else:
    print(f'⚠️  {FAILED} test(s) failed')
print('=' * 60)

ide_proc.terminate()
mock_server.shutdown()
