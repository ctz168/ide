"""
PhoneIDE - Browser preview and inspection API.
Provides command queue for AI tools to control the preview iframe.
"""

import json
import uuid
import time
import ssl
import threading
import webbrowser
import subprocess
import re
import urllib.request
import urllib.error
import urllib.parse
from urllib.parse import urlparse, urljoin, urlencode

from flask import Blueprint, jsonify, request, Response, make_response
from utils import handle_error

bp = Blueprint('browser', __name__)

# ── In-memory command queue ──
# AI tool creates command → frontend polls & executes → frontend posts result → tool returns
_commands = {}  # cmd_id -> {action, params, status, result, event, created}
_lock = threading.Lock()
COMMAND_TIMEOUT = 20  # seconds


def _cleanup_old_commands():
    """Remove commands older than 60 seconds."""
    now = time.time()
    expired = [cid for cid, cmd in _commands.items() if now - cmd.get('created', 0) > 60]
    for cid in expired:
        cmd = _commands.pop(cid, None)
        if cmd and cmd.get('event'):
            cmd['event'].set()


def create_browser_command(action, params):
    """Create a pending browser command. Returns cmd_id."""
    _cleanup_old_commands()
    cmd_id = uuid.uuid4().hex[:8]
    with _lock:
        _commands[cmd_id] = {
            'action': action,
            'params': params,
            'status': 'pending',  # pending -> claimed -> done
            'result': None,
            'error': None,
            'event': threading.Event(),
            'created': time.time(),
        }
    return cmd_id


def wait_browser_result(cmd_id, timeout=COMMAND_TIMEOUT):
    """Wait for the frontend to execute a command and return the result."""
    with _lock:
        cmd = _commands.get(cmd_id)
    if not cmd:
        return {'error': 'Command not found (may have expired)'}
    ok = cmd['event'].wait(timeout=timeout)
    with _lock:
        result = cmd.get('result')
        error = cmd.get('error')
    if not ok:
        return {'error': f'Browser command timed out after {timeout}s. Is the preview tab active with a loaded page?'}
    if error:
        return {'error': error}
    return result


def set_browser_result(cmd_id, result):
    """Frontend posts command execution result."""
    with _lock:
        cmd = _commands.get(cmd_id)
        if not cmd:
            return False
        cmd['result'] = result
        cmd['status'] = 'done'
        cmd['event'].set()
    return True


def set_browser_error(cmd_id, error):
    """Frontend posts command execution error."""
    with _lock:
        cmd = _commands.get(cmd_id)
        if not cmd:
            return False
        cmd['error'] = error
        cmd['status'] = 'done'
        cmd['event'].set()
    return True


# ── API Routes ──

@bp.route('/api/browser/poll')
@handle_error
def poll_command():
    """Frontend polls for a pending browser command to execute.
    Returns the next pending command and marks it as 'claimed'."""
    with _lock:
        # Find oldest pending command
        pending = [
            (cid, cmd) for cid, cmd in _commands.items()
            if cmd['status'] == 'pending'
        ]
        if pending:
            pending.sort(key=lambda x: x[1]['created'])
            cid, cmd = pending[0]
            cmd['status'] = 'claimed'
            return jsonify({
                'cmd_id': cid,
                'action': cmd['action'],
                'params': cmd['params'],
            })
    return jsonify({'cmd_id': None})


@bp.route('/api/browser/result', methods=['POST'])
@handle_error
def post_result():
    """Frontend posts the result of executing a browser command."""
    data = request.json or {}
    cmd_id = data.get('cmd_id', '')
    result = data.get('result')
    error = data.get('error')

    if not cmd_id:
        return jsonify({'error': 'cmd_id required'}), 400

    if error:
        set_browser_error(cmd_id, error)
    else:
        set_browser_result(cmd_id, result)

    return jsonify({'ok': True})


@bp.route('/api/browser/status')
@handle_error
def browser_status():
    """Return current browser command queue status."""
    with _lock:
        pending = sum(1 for c in _commands.values() if c['status'] == 'pending')
        claimed = sum(1 for c in _commands.values() if c['status'] == 'claimed')
    return jsonify({
        'pending': pending,
        'claimed': claimed,
        'total': pending + claimed,
    })


@bp.route('/api/browser/open-external', methods=['POST'])
@handle_error
def open_external():
    """Open a URL in the system/default browser."""
    data = request.json or {}
    url = data.get('url', '').strip()
    if not url:
        return jsonify({'error': 'URL is required'}), 400
    if not url.startswith('http://') and not url.startswith('https://'):
        url = 'https://' + url
    try:
        # Try webbrowser first (works on desktop)
        opened = webbrowser.open(url)
        if opened:
            return jsonify({'ok': True, 'message': f'Opened in browser: {url}'})
        # Fallback: subprocess (works on Termux / Android)
        try:
            subprocess.Popen(['xdg-open', url], stderr=subprocess.DEVNULL)
        except Exception:
            try:
                subprocess.Popen(['termux-open-url', url], stderr=subprocess.DEVNULL)
            except Exception:
                pass
        return jsonify({'ok': True, 'message': f'Opened: {url}'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ── Proxy Endpoint ──

# Headers from the target that should be stripped to allow iframe embedding
_STRIP_HEADERS = {
    'x-frame-options',
    'content-security-policy',
    'content-security-policy-report-only',
    'x-content-type-options',  # strip nosniff so we can proxy any content type
}

# Headers from the target that should be passed through
_PASS_HEADERS = {
    'content-type',
    'content-encoding',
    'transfer-encoding',
    'cache-control',
    'etag',
    'last-modified',
    'set-cookie',
    'access-control-allow-origin',
    'access-control-allow-credentials',
}

# SSL context that doesn't verify certs (for local dev with self-signed certs)
_SSL_CONTEXT = ssl.create_default_context()
_SSL_CONTEXT.check_hostname = False
_SSL_CONTEXT.verify_mode = ssl.CERT_NONE


class _ProxyResponse:
    """Lightweight wrapper to mimic requests.Response interface for _proxy_response."""
    def __init__(self, raw_body, status_code, headers):
        self.content = raw_body
        self.status_code = status_code
        self.headers = _CaseInsensitiveDict(headers)
        # Try to detect encoding from content-type
        ct = headers.get('Content-Type', '')
        self.encoding = 'utf-8'
        if 'charset=' in ct:
            for part in ct.split(';'):
                part = part.strip()
                if part.lower().startswith('charset='):
                    self.encoding = part.split('=', 1)[1].strip().strip('"').strip("'")
                    break


class _CaseInsensitiveDict:
    """Minimal case-insensitive dict for response headers."""
    def __init__(self, source=None):
        self._store = {}
        if source:
            for k, v in source.items():
                self._store[k.lower()] = (k, v)

    def get(self, key, default=None):
        item = self._store.get(key.lower())
        return item[1] if item else default

    def items(self):
        return [(k, v) for k, (_, v) in self._store.items()]


def _proxy_response(target_resp, proxy_base):
    """Build a Flask Response from a target response, rewriting HTML if needed."""
    content_type = target_resp.headers.get('content-type', '')
    raw_body = target_resp.content

    # For HTML, rewrite URLs to route through our proxy
    if 'text/html' in content_type:
        try:
            text = raw_body.decode(target_resp.encoding or 'utf-8', errors='replace')
            text = _rewrite_html_urls(text, proxy_base)
            raw_body = text.encode('utf-8')
            content_type = 'text/html; charset=utf-8'
        except Exception:
            pass
    # For CSS, rewrite url() references
    elif 'text/css' in content_type:
        try:
            text = raw_body.decode(target_resp.encoding or 'utf-8', errors='replace')
            text = _rewrite_css_urls(text, proxy_base)
            raw_body = text.encode('utf-8')
        except Exception:
            pass

    resp = Response(raw_body, status=target_resp.status_code)

    # Set Content-Type (always)
    if content_type:
        resp.headers['Content-Type'] = content_type

    # Pass through safe headers
    for key in _PASS_HEADERS:
        val = target_resp.headers.get(key)
        if val:
            resp.headers[key] = val

    # Allow embedding from any origin
    resp.headers['X-Frame-Options'] = 'ALLOWALL'
    resp.headers['Access-Control-Allow-Origin'] = '*'
    resp.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
    resp.headers['Access-Control-Allow-Headers'] = '*'

    return resp


def _rewrite_html_urls(html, proxy_base):
    """Rewrite URLs in HTML attributes to route through the proxy."""
    # Rewrite <a href>, <link href>, <script src>, <img src>, <iframe src>,
    # <form action>, <source src>, <video src>, <audio src>, <track src>
    url_attrs = [
        (r'(<a\s[^>]*?href\s*=\s*["\'])([^"\']*)(["\'])', 2),
        (r'(<link\s[^>]*?href\s*=\s*["\'])([^"\']*)(["\'])', 2),
        (r'(<script\s[^>]*?src\s*=\s*["\'])([^"\']*)(["\'])', 2),
        (r'(<img\s[^>]*?src\s*=\s*["\'])([^"\']*)(["\'])', 2),
        (r'(<iframe\s[^>]*?src\s*=\s*["\'])([^"\']*)(["\'])', 2),
        (r'(<form\s[^>]*?action\s*=\s*["\'])([^"\']*)(["\'])', 2),
        (r'(<source\s[^>]*?src\s*=\s*["\'])([^"\']*)(["\'])', 2),
        (r'(<video\s[^>]*?src\s*=\s*["\'])([^"\']*)(["\'])', 2),
        (r'(<audio\s[^>]*?src\s*=\s*["\'])([^"\']*)(["\'])', 2),
        (r'(<track\s[^>]*?src\s*=\s*["\'])([^"\']*)(["\'])', 2),
        (r'(<embed\s[^>]*?src\s*=\s*["\'])([^"\']*)(["\'])', 2),
        (r'(<object\s[^>]*?data\s*=\s*["\'])([^"\']*)(["\'])', 2),
    ]

    def _replace(match):
        prefix = match.group(1)
        url = match.group(2)
        suffix = match.group(3)
        # Skip javascript:, data:, #, mailto:, tel:
        if url and not url.startswith(('javascript:', 'data:', '#', 'mailto:', 'tel:', 'blob:')):
            url = _proxy_url(url, proxy_base)
        return prefix + url + suffix

    for pattern, _ in url_attrs:
        html = re.sub(pattern, _replace, html, flags=re.IGNORECASE)

    # Rewrite <base href> if present
    html = re.sub(
        r'(<base\s[^>]*?href\s*=\s*["\'])([^"\']*)(["\'])',
        lambda m: m.group(1) + _proxy_url(m.group(2), proxy_base) + m.group(3),
        html, flags=re.IGNORECASE
    )

    # Remove CSP meta tags that could block embedding
    html = re.sub(
        r'<meta\s+[^>]*?http-equiv\s*=\s*["\']Content-Security-Policy["\'][^>]*?>',
        '', html, flags=re.IGNORECASE
    )

    return html


def _rewrite_css_urls(css, proxy_base):
    """Rewrite url() references in CSS to route through the proxy."""
    def _replace(match):
        url = match.group(1)
        if url and not url.startswith(('data:', 'blob:')):
            url = _proxy_url(url, proxy_base)
        return 'url(' + url + ')'

    return re.sub(r'url\(\s*["\']?([^"\')\s]+)["\']?\s*\)', _replace, css, flags=re.IGNORECASE)


def _proxy_url(url, proxy_base):
    """Convert an absolute or relative URL to a proxy URL.

    proxy_base has the form:
      /api/browser/proxy?url=<encoded_target>&base=<encoded_target>
    For relative URLs we need to preserve the base and pass rel separately.
    """
    if not url:
        return url
    # Already proxied
    if '/api/browser/proxy' in url:
        return url
    # Absolute URL — independent proxy request with its own base
    if url.startswith('http://') or url.startswith('https://'):
        return '/api/browser/proxy?url=' + urllib.parse.quote(url, safe='') + '&base=' + urllib.parse.quote(url, safe='')
    # Protocol-relative
    if url.startswith('//'):
        full = 'https:' + url
        return '/api/browser/proxy?url=' + urllib.parse.quote(full, safe='') + '&base=' + urllib.parse.quote(full, safe='')
    # Relative URL — keep existing base from proxy_base, pass as rel param
    return proxy_base + '&rel=' + urllib.parse.quote(url, safe='')


def _get_original_base(proxy_base):
    """Extract the original target URL from the proxy base URL."""
    return proxy_base


@bp.route('/api/browser/proxy')
@handle_error
def proxy():
    """
    Reverse proxy for iframe preview.
    Fetches the target URL, strips X-Frame-Options / CSP headers,
    rewrites HTML/CSS URLs to route through this proxy.

    Query params:
      url  — the target URL to fetch
      rel  — (optional) relative URL path to append to the target URL
      base — (optional) override the base URL for resolving relative paths
    """
    target_url = request.args.get('url', '').strip()
    if not target_url:
        return jsonify({'error': 'url parameter required'}), 400

    # Normalize protocol
    if target_url.startswith('//'):
        target_url = 'https:' + target_url

    # Handle relative URLs (rel param is set when HTML references are rewritten)
    rel_path = request.args.get('rel', '')
    base_url = request.args.get('base', '')

    if rel_path:
        # Resolve relative URL against the base
        resolve_against = base_url or target_url
        target_url = urljoin(resolve_against, rel_path)

    # Security: only allow http/https
    if not target_url.startswith('http://') and not target_url.startswith('https://'):
        return jsonify({'error': 'Only http/https URLs are allowed'}), 400

    # Security: prevent SSRF — only allow localhost, private IPs, and known domains
    parsed = urlparse(target_url)
    hostname = parsed.hostname or ''
    # Allow all hostnames for development use (IDE is typically local-only)

    try:
        # Fetch target URL using urllib
        req = urllib.request.Request(target_url, method='GET')
        req.add_header('User-Agent', 'PhoneIDE-Proxy/1.0')
        req.add_header('Accept', 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8')
        req.add_header('Accept-Encoding', 'identity')  # avoid gzip to simplify URL rewriting

        raw_resp = urllib.request.urlopen(req, timeout=15, context=_SSL_CONTEXT)
        raw_body = raw_resp.read()
        status_code = raw_resp.status
        resp_headers = dict(raw_resp.headers.items())

        # Build proxy base URL for rewriting
        proxy_base = f'/api/browser/proxy?url={urllib.parse.quote(target_url, safe="")}'
        proxy_base += f'&base={urllib.parse.quote(target_url, safe="")}'

        wrapped = _ProxyResponse(raw_body, status_code, resp_headers)
        return _proxy_response(wrapped, proxy_base)

    except urllib.error.HTTPError as e:
        # For HTTP errors, still try to return the error page through our proxy
        try:
            raw_body = e.read()
            resp_headers = dict(e.headers.items()) if e.headers else {}
            proxy_base = f'/api/browser/proxy?url={urllib.parse.quote(target_url, safe="")}'
            proxy_base += f'&base={urllib.parse.quote(target_url, safe="")}'
            wrapped = _ProxyResponse(raw_body, e.code, resp_headers)
            return _proxy_response(wrapped, proxy_base)
        except Exception:
            return jsonify({'error': f'HTTP {e.code} for {target_url}'}), e.code
    except urllib.error.URLError as e:
        reason = str(e.reason) if e.reason else 'Unknown error'
        return jsonify({'error': f'Cannot connect to {target_url}: {reason[:200]}'}), 502
    except Exception as e:
        return jsonify({'error': f'Proxy error: {str(e)[:200]}'}), 502
