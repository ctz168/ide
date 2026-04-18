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
import gzip
import zlib
import urllib.request
import urllib.error
import urllib.parse
from urllib.parse import urlparse, urljoin, urlencode

from flask import Blueprint, jsonify, request, Response, make_response
from utils import handle_error

# Try importing brotli (may not be installed)
try:
    import brotli
    _HAS_BROTLI = True
except ImportError:
    _HAS_BROTLI = False

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
# NOTE: content-encoding is NOT passed — we decompress on the server side
# so that we can rewrite URLs before sending to the browser.
_PASS_HEADERS = {
    'content-type',
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


def _decompress_body(raw_body, content_encoding):
    """Decompress the response body if it was compressed by the server.
    Some servers ignore Accept-Encoding: identity and send compressed content anyway."""
    if not content_encoding:
        return raw_body
    enc = content_encoding.lower().strip()
    try:
        if enc == 'gzip' or enc == 'x-gzip':
            return gzip.decompress(raw_body)
        elif enc == 'deflate':
            return zlib.decompress(raw_body)
        elif enc == 'br' and _HAS_BROTLI:
            return brotli.decompress(raw_body)
    except Exception:
        pass
    return raw_body


def _proxy_response(target_resp, proxy_base):
    """Build a Flask Response from a target response, rewriting URLs if needed."""
    content_type = target_resp.headers.get('content-type', '')
    raw_body = target_resp.content

    # Decompress if server sent compressed content despite Accept-Encoding: identity
    ce = target_resp.headers.get('content-encoding', '')
    if ce:
        raw_body = _decompress_body(raw_body, ce)

    # For HTML, rewrite URLs to route through our proxy
    if 'text/html' in content_type:
        try:
            text = raw_body.decode(target_resp.encoding or 'utf-8', errors='replace')
            text = _rewrite_html_urls(text, proxy_base)
            raw_body = text.encode('utf-8')
            content_type = 'text/html; charset=utf-8'
        except Exception:
            pass
    # For CSS, rewrite url() references and @import
    elif 'text/css' in content_type:
        try:
            text = raw_body.decode(target_resp.encoding or 'utf-8', errors='replace')
            text = _rewrite_css_urls(text, proxy_base)
            raw_body = text.encode('utf-8')
        except Exception:
            pass
    # For JavaScript, rewrite import statements to route through proxy
    elif 'javascript' in content_type or 'application/x-javascript' in content_type:
        try:
            text = raw_body.decode(target_resp.encoding or 'utf-8', errors='replace')
            text = _rewrite_js_urls(text, proxy_base)
            raw_body = text.encode('utf-8')
        except Exception:
            pass
    # For SVG, rewrite URLs (SVG is XML-based)
    elif 'image/svg+xml' in content_type:
        try:
            text = raw_body.decode(target_resp.encoding or 'utf-8', errors='replace')
            text = _rewrite_html_urls(text, proxy_base)
            raw_body = text.encode('utf-8')
        except Exception:
            pass

    resp = Response(raw_body, status=target_resp.status_code)

    # Set Content-Type (always)
    if content_type:
        resp.headers['Content-Type'] = content_type

    # Pass through safe headers (NOT content-encoding since we decompressed)
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
        (r'(<input\s[^>]*?src\s*=\s*["\'])([^"\']*)(["\'])', 2),
        (r'(<area\s[^>]*?href\s*=\s*["\'])([^"\']*)(["\'])', 2),
        (r'(<button\s[^>]*?formaction\s*=\s*["\'])([^"\']*)(["\'])', 2),
    ]

    def _replace(match):
        prefix = match.group(1)
        url = match.group(2)
        suffix = match.group(3)
        if url and not url.startswith(('javascript:', 'data:', '#', 'mailto:', 'tel:', 'blob:')):
            url = _proxy_url(url, proxy_base)
        return prefix + url + suffix

    for pattern, _ in url_attrs:
        html = re.sub(pattern, _replace, html, flags=re.IGNORECASE)

    # Rewrite srcset attributes (responsive images)
    def _rewrite_srcset(match):
        attr_prefix = match.group(1)
        srcset_val = match.group(2)
        quote = match.group(3)
        parts = srcset_val.split(',')
        new_parts = []
        for part in parts:
            part = part.strip()
            if not part:
                continue
            tokens = part.split(None, 1)
            url = tokens[0]
            descriptor = tokens[1] if len(tokens) > 1 else ''
            if url and not url.startswith(('data:', 'blob:')):
                url = _proxy_url(url, proxy_base)
            if descriptor:
                new_parts.append(url + ' ' + descriptor)
            else:
                new_parts.append(url)
        return attr_prefix + ', '.join(new_parts) + quote

    html = re.sub(
        r'((?:srcset|data-srcset)\s*=\s*["\'])([^"\']*)(["\'])',
        _rewrite_srcset, html, flags=re.IGNORECASE
    )

    # Rewrite inline style="...url(...)..."
    def _rewrite_inline_style(match):
        prefix = match.group(1)
        style_val = match.group(2)
        quote = match.group(3)
        style_val = _rewrite_css_urls(style_val, proxy_base)
        return prefix + style_val + quote

    html = re.sub(
        r'(<[^>]+\bstyle\s*=\s*["\'])([^"\']*)(["\'])',
        _rewrite_inline_style, html, flags=re.IGNORECASE
    )

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

    # Inject <base> tag if not present, so any URLs missed by regex
    # still resolve correctly against the target origin
    base_tag = re.search(r'<base\s', html, re.IGNORECASE)
    if not base_tag:
        base_match = re.search(r'base=([^&]+)', proxy_base)
        if base_match:
            target_base = urllib.parse.unquote(base_match.group(1))
            parsed = urlparse(target_base)
            base_href = f'{parsed.scheme}://{parsed.netloc}'
            html = re.sub(
                r'(<head[^>]*>)',
                lambda m: m.group(1) + f'<base href="{base_href}/">',
                html, count=1, flags=re.IGNORECASE
            )

    return html


def _rewrite_css_urls(css, proxy_base):
    """Rewrite url() references and @import in CSS to route through the proxy."""
    def _replace_url(match):
        url = match.group(1)
        if url and not url.startswith(('data:', 'blob:')):
            url = _proxy_url(url, proxy_base)
        return 'url(' + url + ')'

    css = re.sub(r'url\(\s*["\']?([^"\'\)\s]+)["\']?\s*\)', _replace_url, css, flags=re.IGNORECASE)

    # Rewrite @import "..." and @import '...' (without url())
    def _replace_import(match):
        quote = match.group(1)
        url = match.group(2)
        if url and not url.startswith(('data:', 'blob:')):
            url = _proxy_url(url, proxy_base)
        return '@import ' + quote + url + quote

    css = re.sub(r'@import\s+(["\'])([^"\']+)(["\'])', _replace_import, css, flags=re.IGNORECASE)

    return css


def _rewrite_js_urls(js, proxy_base):
    """Rewrite static import URLs in JavaScript to route through the proxy.
    This is best-effort — dynamic URLs constructed at runtime cannot be fully handled server-side."""
    # Rewrite import() dynamic imports
    def _replace_import(match):
        quote = match.group(1)
        url = match.group(2)
        if url and not url.startswith(('data:', 'blob:')):
            url = _proxy_url(url, proxy_base)
        return 'import(' + quote + url + quote + ')'

    js = re.sub(r'import\(\s*(["\'])([^"\']+)(["\'])\s*\)', _replace_import, js)

    # Rewrite static import ... from '...' and from "..."
    def _replace_static_import(match):
        prefix = match.group(1)
        quote = match.group(2)
        url = match.group(3)
        if url and not url.startswith(('.', '/', 'data:', 'blob:', 'http')):
            # Bare module specifier — don't rewrite
            return match.group(0)
        if url and not url.startswith(('data:', 'blob:')):
            url = _proxy_url(url, proxy_base)
        return prefix + quote + url + quote

    js = re.sub(r'(import\s+[^;\n]+?\s+from\s+)(["\'])([^"\']+)(["\'])', _replace_static_import, js)
    # Rewrite simple import "..." statements
    js = re.sub(r'(import\s*)(["\'])([^"\']+)(["\'])', _replace_static_import, js)

    return js


def _proxy_url(url, proxy_base):
    """Convert an absolute or relative URL to a proxy URL.

    proxy_base has the form:
      /api/browser/proxy?url=<encoded_target>&base=<encoded_dir>
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


@bp.route('/api/browser/proxy')
@handle_error
def proxy():
    """
    Reverse proxy for iframe preview.
    Fetches the target URL, strips X-Frame-Options / CSP headers,
    rewrites HTML/CSS/JS URLs to route through this proxy.

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

    parsed = urlparse(target_url)
    hostname = parsed.hostname or ''

    try:
        # Fetch target URL using urllib
        req = urllib.request.Request(target_url, method='GET')
        req.add_header('User-Agent', 'Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 Chrome/120.0.0.0 Mobile Safari/537.36')
        # Use */* for subresources (not the HTML-preferring Accept header)
        req.add_header('Accept', '*/*')
        req.add_header('Accept-Encoding', 'identity')  # avoid gzip to simplify URL rewriting
        # Forward Referer so target servers handle relative redirects correctly
        req.add_header('Referer', f'{parsed.scheme}://{parsed.netloc}/')

        raw_resp = urllib.request.urlopen(req, timeout=15, context=_SSL_CONTEXT)
        raw_body = raw_resp.read()
        status_code = raw_resp.status
        resp_headers = dict(raw_resp.headers.items())

        # Build proxy base URL for rewriting
        # Use the directory of the target URL as base, so relative URLs resolve correctly
        # e.g. for http://host:8080/app/page.html, base should be http://host:8080/app/
        path = parsed.path or '/'
        if '/' in path.rstrip('/'):
            dir_path = path.rsplit('/', 1)[0] + '/'
        else:
            dir_path = '/'
        dir_url = f'{parsed.scheme}://{parsed.netloc}{dir_path}'

        proxy_base = f'/api/browser/proxy?url={urllib.parse.quote(target_url, safe="")}'
        proxy_base += f'&base={urllib.parse.quote(dir_url, safe="")}'

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
