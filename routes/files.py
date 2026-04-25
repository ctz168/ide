"""
PhoneIDE - File management API routes.
"""

import json
import os
import re
import fnmatch
from pathlib import Path
from datetime import datetime
from flask import Blueprint, jsonify, request, send_file, Response
from utils import (
    handle_error, load_config, save_config, WORKSPACE,
    get_icon_for_file, get_file_type,
)

bp = Blueprint('files', __name__)


@bp.route('/api/files/list', methods=['GET'])
@handle_error
def list_files():
    path = request.args.get('path', '')
    config = load_config()
    base = config.get('workspace', WORKSPACE)
    project = config.get('project', None)

    target = os.path.join(base, path) if path else base
    target = os.path.realpath(target)

    # Security: must be under workspace
    real_base = os.path.realpath(base)
    if not target.startswith(real_base):
        return jsonify({'error': 'Access denied'}), 403

    # Project boundary enforcement:
    # When a project is open, the file tree is confined to the project directory.
    # If the requested path is above the project root, redirect to the project root.
    if project:
        project_dir = os.path.realpath(os.path.join(base, project))
        if os.path.isdir(project_dir) and not target.startswith(project_dir):
            # User tried to navigate above the project — redirect to project root
            target = project_dir
            path = project

    # Auto-create workspace root if it doesn't exist
    if not os.path.exists(target) and target == os.path.realpath(base):
        try:
            os.makedirs(target, exist_ok=True)
        except OSError:
            return jsonify({'error': f'Cannot create workspace directory: {target}'}), 500

    if not os.path.exists(target):
        return jsonify({'error': 'Path not found'}), 404

    items = []
    if os.path.isdir(target):
        try:
            for entry in sorted(os.listdir(target)):
                full = os.path.join(target, entry)
                try:
                    st = os.stat(full)
                    is_dir = os.path.isdir(full)
                    items.append({
                        'name': entry,
                        'path': os.path.relpath(full, base).replace(os.sep, '/'),
                        'is_dir': is_dir,
                        'size': st.st_size if not is_dir else 0,
                        'modified': datetime.fromtimestamp(st.st_mtime).isoformat(),
                        'icon': get_icon_for_file(entry),
                    })
                except (PermissionError, OSError):
                    pass
        except PermissionError:
            return jsonify({'error': 'Permission denied'}), 403
    else:
        items.append({
            'name': os.path.basename(target),
            'path': os.path.relpath(target, base),
            'is_dir': False,
            'size': os.path.getsize(target),
            'modified': datetime.fromtimestamp(os.path.getmtime(target)).isoformat(),
            'icon': get_icon_for_file(os.path.basename(target)),
        })

    return jsonify({'items': items, 'path': path, 'base': base, 'project': project})


@bp.route('/api/files/read', methods=['GET'])
@handle_error
def read_file():
    path = request.args.get('path', '')
    config = load_config()
    base = config.get('workspace', WORKSPACE)

    target = os.path.realpath(os.path.join(base, path))

    if not target.startswith(os.path.realpath(base)):
        return jsonify({'error': 'Access denied'}), 403

    if not os.path.isfile(target):
        return jsonify({'error': 'File not found'}), 404

    # Limit file size (10MB)
    size = os.path.getsize(target)
    if size > 10 * 1024 * 1024:
        return jsonify({'error': 'File too large (>10MB)', 'size': size}), 413

    try:
        # Try to detect encoding
        with open(target, 'rb') as f:
            raw = f.read()

        encodings = ['utf-8', 'utf-8-sig', 'gbk', 'gb2312', 'latin-1']
        content = None
        used_encoding = 'utf-8'
        for enc in encodings:
            try:
                content = raw.decode(enc)
                used_encoding = enc
                break
            except (UnicodeDecodeError, LookupError):
                continue

        if content is None:
            content = raw.decode('utf-8', errors='replace')
            used_encoding = 'utf-8'

        return jsonify({
            'content': content,
            'path': path,
            'encoding': used_encoding,
            'type': get_file_type(os.path.basename(target)),
            'size': size,
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# MIME type mapping for preview
_PREVIEW_MIME_TYPES = {
    '.html': 'text/html; charset=utf-8',
    '.htm': 'text/html; charset=utf-8',
    '.md': 'text/markdown; charset=utf-8',
    '.markdown': 'text/markdown; charset=utf-8',
    '.css': 'text/css; charset=utf-8',
    '.js': 'application/javascript; charset=utf-8',
    '.json': 'application/json; charset=utf-8',
    '.xml': 'application/xml; charset=utf-8',
    '.svg': 'image/svg+xml; charset=utf-8',
    '.txt': 'text/plain; charset=utf-8',
    '.py': 'text/plain; charset=utf-8',
    '.png': 'image/png',
    '.jpg': 'image/jpeg',
    '.jpeg': 'image/jpeg',
    '.gif': 'image/gif',
    '.ico': 'image/x-icon',
    '.webp': 'image/webp',
    '.pdf': 'application/pdf',
    '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    '.pptx': 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
    '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
}


@bp.route('/api/files/preview', methods=['GET'])
@handle_error
def preview_file():
    """Serve a local file for browser preview (HTML, MD, images, etc.).
    This route returns raw file content with proper Content-Type so that
    the browser's iframe can render it correctly."""
    path = request.args.get('path', '')
    config = load_config()
    base = config.get('workspace', WORKSPACE)

    target = os.path.realpath(os.path.join(base, path))

    if not target.startswith(os.path.realpath(base)):
        return jsonify({'error': 'Access denied'}), 403

    if not os.path.isfile(target):
        return jsonify({'error': 'File not found'}), 404

    # Determine MIME type from extension
    ext = os.path.splitext(target)[1].lower()
    mime_type = _PREVIEW_MIME_TYPES.get(ext, 'application/octet-stream')

    # For Markdown files, convert to HTML before serving
    if ext in ('.md', '.markdown'):
        try:
            with open(target, 'r', encoding='utf-8', errors='replace') as f:
                md_content = f.read()
            # Safely encode markdown content as JSON string to prevent XSS
            md_json = json.dumps(md_content)
            html_content = f'''<!DOCTYPE html>
<html><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
       max-width: 900px; margin: 0 auto; padding: 20px; line-height: 1.6; color: #333; }}
h1,h2,h3,h4,h5,h6 {{ margin-top: 1.5em; margin-bottom: 0.5em; }}
code {{ background: #f5f5f5; padding: 2px 6px; border-radius: 3px; font-size: 0.9em; }}
pre {{ background: #f5f5f5; padding: 12px; border-radius: 6px; overflow-x: auto; }}
pre code {{ background: none; padding: 0; }}
blockquote {{ border-left: 4px solid #ddd; margin: 0; padding-left: 16px; color: #666; }}
table {{ border-collapse: collapse; width: 100%; margin: 1em 0; }}
th,td {{ border: 1px solid #ddd; padding: 8px 12px; text-align: left; }}
th {{ background: #f5f5f5; }}
img {{ max-width: 100%; }}
a {{ color: #0066cc; }}
</style>
<script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
</head><body>
<div id="content"></div>
<script>
document.getElementById('content').innerHTML = marked.parse({md_json});
</script>
</body></html>'''
            return Response(html_content, mimetype='text/html; charset=utf-8')
        except Exception as e:
            return jsonify({'error': f'Markdown render error: {e}'}), 500

    # For binary file types (images, PDF), use send_file
    if mime_type.startswith('image/') or mime_type == 'application/pdf':
        return send_file(target, mimetype=mime_type)

    # For text-based files, serve with proper encoding
    try:
        with open(target, 'rb') as f:
            raw = f.read()
        # Try to decode as text
        for enc in ['utf-8', 'utf-8-sig', 'gbk', 'latin-1']:
            try:
                content = raw.decode(enc)
                break
            except (UnicodeDecodeError, LookupError):
                continue
        else:
            return Response(raw, mimetype=mime_type)

        # For HTML files: inject <base> tag so relative CSS/JS paths resolve correctly
        # Set base to the full file path (not just directory) so that:
        #   - Relative paths like "style.css" resolve to /preview/<dir>/style.css
        #   - Anchor links like "#section" resolve to /preview/<dir>/index.html#section
        #   (If base were just /preview/<dir>/, #links would load the directory, not the file)
        if ext in ('.html', '.htm'):
            base_href = f'/preview/{path}'
            # Inject <base> tag right after <head> or at the start of the document
            if '<head>' in content:
                content = content.replace('<head>', f'<head><base href="{base_href}">', 1)
            elif '<HEAD>' in content:
                content = content.replace('<HEAD>', f'<HEAD><base href="{base_href}">', 1)
            elif '<html>' in content:
                content = content.replace('<html>', f'<html><head><base href="{base_href}"></head>', 1)
            else:
                # No <head> tag at all — prepend it
                content = f'<head><base href="{base_href}"></head>' + content

        return Response(content, mimetype=mime_type)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@bp.route('/preview/<path:subpath>', methods=['GET'])
@handle_error
def serve_preview_file(subpath):
    """Serve static files for browser preview with correct relative path resolution.
    
    When an HTML file is previewed with a <base href="/preview/project_dir/"> tag,
    relative paths like "style.css" will resolve to /preview/project_dir/style.css.
    This route serves those files from the workspace.
    """
    config = load_config()
    base = config.get('workspace', WORKSPACE)

    # Security: prevent directory traversal
    target = os.path.realpath(os.path.join(base, subpath))
    if not target.startswith(os.path.realpath(base)):
        return jsonify({'error': 'Access denied'}), 403

    if not os.path.isfile(target):
        return jsonify({'error': 'File not found'}), 404

    # Determine MIME type
    ext = os.path.splitext(target)[1].lower()
    mime_type = _PREVIEW_MIME_TYPES.get(ext, 'application/octet-stream')

    # For binary file types, use send_file
    if mime_type.startswith('image/') or mime_type == 'application/pdf':
        return send_file(target, mimetype=mime_type)

    # For text-based files, serve with proper encoding
    try:
        with open(target, 'rb') as f:
            raw = f.read()
        content = None
        for enc in ['utf-8', 'utf-8-sig', 'gbk', 'latin-1']:
            try:
                content = raw.decode(enc)
                break
            except (UnicodeDecodeError, LookupError):
                continue
        if content is None:
            return Response(raw, mimetype=mime_type)

        # For HTML files: inject <base> tag so relative CSS/JS paths resolve correctly
        # Set base to the full file path (not just directory) so that:
        #   - Relative paths like "style.css" resolve to /preview/<dir>/style.css
        #   - Anchor links like "#section" resolve to /preview/<dir>/index.html#section
        if ext in ('.html', '.htm'):
            base_href = f'/preview/{subpath}'
            if '<head>' in content:
                content = content.replace('<head>', f'<head><base href="{base_href}">', 1)
            elif '<HEAD>' in content:
                content = content.replace('<HEAD>', f'<HEAD><base href="{base_href}">', 1)
            elif '<html>' in content:
                content = content.replace('<html>', f'<html><head><base href="{base_href}"></head>', 1)
            else:
                content = f'<head><base href="{base_href}"></head>' + content

        return Response(content, mimetype=mime_type)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@bp.route('/api/files/save', methods=['POST'])
@handle_error
def save_file():
    data = request.json
    path = data.get('path', '')
    content = data.get('content', '')
    create = data.get('create', False)
    config = load_config()
    base = config.get('workspace', WORKSPACE)

    target = os.path.realpath(os.path.join(base, path))
    if not target.startswith(os.path.realpath(base)):
        return jsonify({'error': 'Access denied'}), 403

    if not os.path.exists(target) and not create:
        # Auto-create file if it doesn't exist (IDE behavior)
        os.makedirs(os.path.dirname(target), exist_ok=True)

    os.makedirs(os.path.dirname(target), exist_ok=True)
    with open(target, 'w', encoding='utf-8') as f:
        f.write(content)

    return jsonify({'ok': True, 'path': path, 'saved_at': datetime.now().isoformat()})


@bp.route('/api/files/create', methods=['POST'])
@handle_error
def create_file():
    data = request.json or {}
    path = data.get('path', '')
    is_dir = data.get('is_dir', False) or data.get('type', '') == 'directory'
    config = load_config()
    base = config.get('workspace', WORKSPACE)

    target = os.path.realpath(os.path.join(base, path))
    if not target.startswith(os.path.realpath(base)):
        return jsonify({'error': 'Access denied'}), 403

    if is_dir:
        os.makedirs(target, exist_ok=True)
    else:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        if not os.path.exists(target):
            Path(target).touch()

    return jsonify({'ok': True, 'path': path})


@bp.route('/api/files/delete', methods=['POST'])
@handle_error
def delete_file():
    import shutil

    data = request.json
    path = data.get('path', '')
    config = load_config()
    base = config.get('workspace', WORKSPACE)

    target = os.path.realpath(os.path.join(base, path))
    if not target.startswith(os.path.realpath(base)):
        return jsonify({'error': 'Access denied'}), 403

    if not os.path.exists(target):
        return jsonify({'error': 'Not found'}), 404

    if os.path.isdir(target):
        shutil.rmtree(target)
    else:
        os.remove(target)

    return jsonify({'ok': True})


@bp.route('/api/files/rename', methods=['POST'])
@handle_error
def rename_file():
    data = request.json
    old_path = data.get('old_path', '')
    new_path = data.get('new_path', '')
    config = load_config()
    base = config.get('workspace', WORKSPACE)

    old_target = os.path.realpath(os.path.join(base, old_path))
    new_target = os.path.realpath(os.path.join(base, new_path))

    if not old_target.startswith(os.path.realpath(base)):
        return jsonify({'error': 'Access denied'}), 403
    if not new_target.startswith(os.path.realpath(base)):
        return jsonify({'error': 'Access denied'}), 403

    if not os.path.exists(old_target):
        return jsonify({'error': 'Source not found'}), 404

    os.makedirs(os.path.dirname(new_target), exist_ok=True)
    os.rename(old_target, new_target)

    return jsonify({'ok': True})


@bp.route('/api/files/open_folder', methods=['POST'])
@handle_error
def open_folder():
    data = request.json
    path = data.get('path', WORKSPACE)
    if path and os.path.isdir(path):
        config = load_config()
        config['workspace'] = path
        save_config(config)
        return jsonify({'ok': True, 'workspace': path})
    return jsonify({'error': 'Invalid folder path'}), 400


# ==================== Workspace Root Selection ====================

@bp.route('/api/workspace/info', methods=['GET'])
@handle_error
def workspace_info():
    """Get current workspace information."""
    config = load_config()
    ws = config.get('workspace', WORKSPACE)
    exists = os.path.isdir(ws)
    if not exists:
        try:
            os.makedirs(ws, exist_ok=True)
            exists = True
        except OSError:
            pass
    return jsonify({
        'workspace': ws,
        'exists': exists,
        'is_default': ws == WORKSPACE,
    })


@bp.route('/api/workspace/browse', methods=['GET'])
@handle_error
def workspace_browse():
    """Browse directories for workspace selection.
    Unlike /api/files/list, this is NOT restricted to the current workspace —
    it allows navigating the whole filesystem to pick a root directory.
    Only directories are listed (no files)."""
    path = request.args.get('path', '/')
    path = os.path.realpath(path)

    if not os.path.isdir(path):
        return jsonify({'error': 'Directory not found'}, 404)

    folders = []
    try:
        for entry in sorted(os.listdir(path)):
            full = os.path.join(path, entry)
            if os.path.isdir(full) and not entry.startswith('.'):
                folders.append({
                    'name': entry,
                    'path': full,
                })
    except PermissionError:
        return jsonify({'error': 'Permission denied'}, 403)

    return jsonify({
        'folders': folders,
        'current_path': path,
        'can_go_up': path != '/',
    })


@bp.route('/api/workspace/set', methods=['POST'])
@handle_error
def workspace_set():
    """Set the workspace directory and persist it in config."""
    data = request.json
    path = data.get('path', '')
    if not path or not os.path.isdir(path):
        return jsonify({'error': 'Invalid directory path'}), 400

    config = load_config()
    config['workspace'] = path
    save_config(config)
    return jsonify({'ok': True, 'workspace': path})


# ==================== Project Management ====================

def get_project_path():
    """Get the current project path (relative to workspace) or None."""
    config = load_config()
    return config.get('project', None)


def get_effective_base():
    """Get the effective base directory for file operations.
    When a project is open, returns the project directory.
    Otherwise returns the workspace root."""
    config = load_config()
    base = config.get('workspace', WORKSPACE)
    project = config.get('project', None)
    if project:
        project_dir = os.path.join(base, project)
        if os.path.isdir(project_dir):
            return project_dir
    return base


@bp.route('/api/project/info', methods=['GET'])
@handle_error
def project_info():
    """Get current project information."""
    config = load_config()
    project = config.get('project', None)
    base = config.get('workspace', WORKSPACE)
    if project:
        project_dir = os.path.join(base, project)
        if os.path.isdir(project_dir):
            return jsonify({
                'project': project,
                'name': os.path.basename(project),
                'path': project_dir,
                'has_git': os.path.exists(os.path.join(project_dir, '.git')),
            })
    return jsonify({'project': None, 'name': None, 'path': None})


@bp.route('/api/project/open', methods=['POST'])
@handle_error
def project_open():
    """Open a project by setting its directory as the project root."""
    data = request.json
    project_rel = data.get('project', '')
    config = load_config()
    base = config.get('workspace', WORKSPACE)

    if not project_rel:
        return jsonify({'error': 'Project path required'}), 400

    # Security: must be under workspace
    target = os.path.realpath(os.path.join(base, project_rel))
    if not target.startswith(os.path.realpath(base)):
        return jsonify({'error': 'Access denied'}), 403

    if not os.path.isdir(target):
        return jsonify({'error': 'Directory not found'}), 404

    config['project'] = project_rel
    save_config(config)

    return jsonify({
        'ok': True,
        'project': project_rel,
        'name': os.path.basename(project_rel),
    })


@bp.route('/api/project/create', methods=['POST'])
@handle_error
def project_create():
    """Create a new project folder in the workspace and return its relative path."""
    data = request.json
    name = data.get('name', '').strip()

    if not name:
        return jsonify({'error': '项目名称不能为空'}), 400

    # Validate name: no path separators, no leading dots
    if '/' in name or '\\' in name:
        return jsonify({'error': '项目名称不能包含路径分隔符'}), 400
    if name.startswith('.'):
        return jsonify({'error': '项目名称不能以点号开头'}), 400

    config = load_config()
    base = config.get('workspace', WORKSPACE)

    if not base or not os.path.isdir(base):
        return jsonify({'error': '工作目录未设置或不存在，请先设置工作目录'}), 400

    target = os.path.realpath(os.path.join(base, name))

    # Security: must be under workspace
    if not target.startswith(os.path.realpath(base)):
        return jsonify({'error': 'Access denied'}), 403

    # Check if already exists
    if os.path.exists(target):
        return jsonify({'error': f'文件夹已存在: {name}'}), 409

    try:
        os.makedirs(target, exist_ok=True)
    except OSError as e:
        return jsonify({'error': f'创建文件夹失败: {e}'}), 500

    project_rel = name
    return jsonify({
        'ok': True,
        'project': project_rel,
        'name': name,
        'path': target,
    })


@bp.route('/api/project/close', methods=['POST'])
@handle_error
def project_close():
    """Close the current project, returning to workspace view."""
    config = load_config()
    config['project'] = None
    # Clear venv_path when closing project to prevent cross-project contamination.
    # When a new project is opened, autoActivateVenv() will re-detect the correct venv.
    config['venv_path'] = ''
    save_config(config)
    return jsonify({'ok': True})


@bp.route('/api/project/list_folders', methods=['GET'])
@handle_error
def project_list_folders():
    """List folders in the workspace root for the project picker."""
    config = load_config()
    base = config.get('workspace', WORKSPACE)
    path = request.args.get('path', '')

    if path:
        target = os.path.realpath(os.path.join(base, path))
    else:
        target = os.path.realpath(base)

    # Security: must be under workspace
    if not target.startswith(os.path.realpath(base)):
        return jsonify({'error': 'Access denied'}), 403

    if not os.path.isdir(target):
        return jsonify({'folders': []})

    folders = []
    try:
        for entry in sorted(os.listdir(target)):
            full = os.path.join(target, entry)
            if os.path.isdir(full) and not entry.startswith('.'):
                rel = os.path.relpath(full, base).replace(os.sep, '/')
                has_git = os.path.exists(os.path.join(full, '.git'))
                folders.append({
                    'name': entry,
                    'path': rel,
                    'has_git': has_git,
                })
    except PermissionError:
        pass

    return jsonify({
        'folders': folders,
        'current_path': os.path.relpath(target, base).replace(os.sep, '/'),
    })


@bp.route('/api/search', methods=['POST'])
@handle_error
def search_files():
    data = request.json
    query = data.get('query', '')
    pattern = data.get('pattern', '')
    file_pattern = data.get('file_pattern', '*')
    case_sensitive = data.get('case_sensitive', False)
    use_regex = data.get('use_regex', False)
    max_results = data.get('max_results', 500)
    search_path = data.get('path', '')  # optional: limit search to a subdirectory (e.g. project dir)
    config = load_config()
    base = config.get('workspace', WORKSPACE)

    # Determine the search root: if a project is open, default to project dir
    project = config.get('project', None)
    if search_path:
        search_root = os.path.realpath(os.path.join(base, search_path))
    elif project:
        search_root = os.path.realpath(os.path.join(base, project))
    else:
        search_root = os.path.realpath(base)

    # Security: must be under workspace
    real_base = os.path.realpath(base)
    if not search_root.startswith(real_base):
        search_root = real_base
    if not os.path.isdir(search_root):
        return jsonify({'results': [], 'total': 0})

    results = []
    search_text = pattern if pattern else query

    try:
        flags = 0 if case_sensitive else re.IGNORECASE
        if use_regex:
            regex = re.compile(search_text, flags)
        else:
            regex = re.compile(re.escape(search_text), flags)

        for root, dirs, files in os.walk(search_root):
            # Skip common ignore dirs
            dirs[:] = [d for d in dirs if d not in {'.git', '__pycache__', 'node_modules', '.venv', 'venv', '.idea', '.vscode'}]
            if len(results) >= max_results:
                break

            for fname in files:
                if len(results) >= max_results:
                    break
                # Filter by file pattern
                if file_pattern != '*' and not fnmatch.fnmatch(fname, file_pattern):
                    continue
                fpath = os.path.join(root, fname)
                try:
                    with open(fpath, 'r', encoding='utf-8', errors='ignore') as f:
                        for i, line in enumerate(f, 1):
                            if regex.search(line):
                                rel = os.path.relpath(fpath, real_base).replace(os.sep, '/')
                                results.append({
                                    'file': rel,
                                    'line': i,
                                    'col': line.lower().find(search_text.lower()) if not case_sensitive else line.find(search_text),
                                    'text': line.rstrip()[:500],
                                    'match': regex.search(line).group() if regex.search(line) else '',
                                })
                                if len(results) >= max_results:
                                    break
                except (PermissionError, OSError):
                    continue
    except re.error as e:
        return jsonify({'error': f'Invalid regex: {str(e)}'}), 400

    return jsonify({'results': results, 'total': len(results)})


@bp.route('/api/search/replace', methods=['POST'])
@handle_error
def replace_in_files():
    data = request.json
    search = data.get('search', '')
    replace = data.get('replace', '')
    file_path = data.get('file_path', '')
    case_sensitive = data.get('case_sensitive', False)
    use_regex = data.get('use_regex', False)
    config = load_config()
    base = config.get('workspace', WORKSPACE)

    if not search:
        return jsonify({'error': 'Search text required'}), 400

    real_base = os.path.realpath(base)
    target = os.path.realpath(os.path.join(base, file_path))

    if not target.startswith(real_base):
        return jsonify({'error': 'Access denied'}), 403

    if not os.path.isfile(target):
        return jsonify({'error': 'File not found'}), 404

    try:
        with open(target, 'r', encoding='utf-8') as f:
            content = f.read()

        flags = 0 if case_sensitive else re.IGNORECASE
        if use_regex:
            new_content = re.sub(search, replace, content, flags=flags)
        else:
            new_content = re.sub(re.escape(search), replace.replace('\\', '\\\\'), content, flags=flags)

        if new_content == content:
            return jsonify({'ok': True, 'replacements': 0})

        with open(target, 'w', encoding='utf-8') as f:
            f.write(new_content)

        return jsonify({'ok': True, 'replacements': len(re.findall(search if use_regex else re.escape(search), content, flags=flags))})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ==================== File Download & Project Archive ====================

import tempfile
import zipfile
import time as _time

@bp.route('/api/files/download', methods=['GET'])
@handle_error
def download_file():
    """Download a single file or directory as zip."""
    path = request.args.get('path', '')
    config = load_config()
    base = config.get('workspace', WORKSPACE)

    target = os.path.realpath(os.path.join(base, path))
    if not target.startswith(os.path.realpath(base)):
        return jsonify({'error': 'Access denied'}), 403

    if not os.path.exists(target):
        return jsonify({'error': 'Path not found'}), 404

    # If it's a file, serve directly
    if os.path.isfile(target):
        ext = os.path.splitext(target)[1].lower()
        mime = _PREVIEW_MIME_TYPES.get(ext, 'application/octet-stream')
        return send_file(target, mimetype=mime, as_attachment=True,
                         download_name=os.path.basename(target))

    # If it's a directory, create a zip on-the-fly
    dirname = os.path.basename(target) or 'project'
    zip_name = f'{dirname}.zip'

    # Create temp zip
    tmp_fd, tmp_path = tempfile.mkstemp(suffix='.zip', prefix='phoneide_dl_')
    try:
        with os.fdopen(tmp_fd, 'wb') as zf:
            with zipfile.ZipFile(zf, 'w', zipfile.ZIP_DEFLATED) as z:
                for root, dirs, files in os.walk(target):
                    # Skip hidden dirs and __pycache__
                    dirs[:] = [d for d in dirs if not d.startswith('.') and d != '__pycache__' and d != 'node_modules' and d != '.git']
                    for f in files:
                        if f.startswith('.') and f != '.env':
                            continue
                        fp = os.path.join(root, f)
                        arcname = os.path.relpath(fp, target)
                        # Skip files > 50MB
                        if os.path.getsize(fp) > 50 * 1024 * 1024:
                            continue
                        z.write(fp, arcname)
        return send_file(tmp_path, mimetype='application/zip', as_attachment=True,
                         download_name=zip_name)
    except Exception as e:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        return jsonify({'error': str(e)}), 500


@bp.route('/api/files/project-archive', methods=['POST'])
@handle_error
def create_project_archive():
    """Create a zip archive of the project and return a download URL.
    Used by the AI agent's project_download tool."""
    data = request.json or {}
    path = data.get('path', '')
    config = load_config()
    base = config.get('workspace', WORKSPACE)
    project = config.get('project', None)

    # Determine target directory
    if path:
        target = os.path.realpath(os.path.join(base, path))
    elif project:
        target = os.path.realpath(os.path.join(base, project))
    else:
        target = os.path.realpath(base)

    if not target.startswith(os.path.realpath(base)):
        return jsonify({'ok': False, 'error': 'Access denied'}), 403

    if not os.path.isdir(target):
        return jsonify({'ok': False, 'error': 'Target is not a directory'}), 400

    dirname = os.path.basename(target) or 'project'
    timestamp = _time.strftime('%Y%m%d_%H%M%S')
    zip_filename = f'{dirname}_{timestamp}.zip'

    # Store archives in a temp directory
    archive_dir = os.path.join(base, '.phoneide_archives')
    os.makedirs(archive_dir, exist_ok=True)
    zip_path = os.path.join(archive_dir, zip_filename)

    file_count = 0
    total_size = 0
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as z:
        for root, dirs, files in os.walk(target):
            dirs[:] = [d for d in dirs if not d.startswith('.') and d != '__pycache__' and d != 'node_modules' and d != '.git' and d != '.phoneide_archives']
            for f in files:
                if f.startswith('.') and f != '.env':
                    continue
                fp = os.path.join(root, f)
                arcname = os.path.relpath(fp, target)
                if os.path.getsize(fp) > 50 * 1024 * 1024:
                    continue
                z.write(fp, arcname)
                file_count += 1
                total_size += os.path.getsize(fp)

    zip_size = os.path.getsize(zip_path)
    # Return a download URL relative to the workspace
    rel_path = os.path.relpath(zip_path, base).replace(os.sep, '/')

    return jsonify({
        'ok': True,
        'filename': zip_filename,
        'path': rel_path,
        'download_url': f'/api/files/download?path={rel_path}',
        'file_count': file_count,
        'total_size': total_size,
        'zip_size': zip_size,
    })


@bp.route('/api/files/office-preview', methods=['GET'])
@handle_error
def office_preview():
    """Convert Office/PDF documents (docx/pptx/xlsx/pdf) to HTML for in-browser preview."""
    path = request.args.get('path', '')
    config = load_config()
    base = config.get('workspace', WORKSPACE)

    target = os.path.realpath(os.path.join(base, path))
    if not target.startswith(os.path.realpath(base)):
        return jsonify({'error': 'Access denied'}), 403

    if not os.path.isfile(target):
        return jsonify({'error': 'File not found'}), 404

    ext = os.path.splitext(target)[1].lower()

    try:
        if ext == '.docx':
            return _docx_to_html(target)
        elif ext == '.pptx':
            return _pptx_to_html(target)
        elif ext == '.xlsx':
            return _xlsx_to_html(target)
        elif ext == '.pdf':
            return _pdf_to_html(target)
        else:
            return jsonify({'error': f'Unsupported file type: {ext}'}), 400
    except Exception as e:
        return jsonify({'error': f'Preview failed: {str(e)}'}), 500


@bp.route('/api/files/pdf-preview', methods=['GET'])
@handle_error
def pdf_preview():
    """Render a PDF file in-browser using PDF.js for a rich reading experience.
    Provides page navigation, zoom, and text extraction — much better than
    raw send_file which relies on the browser's built-in PDF viewer (unreliable in iframes)."""
    path = request.args.get('path', '')
    config = load_config()
    base = config.get('workspace', WORKSPACE)

    target = os.path.realpath(os.path.join(base, path))
    if not target.startswith(os.path.realpath(base)):
        return jsonify({'error': 'Access denied'}), 403

    if not os.path.isfile(target):
        return jsonify({'error': 'File not found'}), 404

    ext = os.path.splitext(target)[1].lower()
    if ext != '.pdf':
        return jsonify({'error': 'Not a PDF file'}), 400

    # Build a PDF.js-based viewer page that loads the raw PDF via /api/files/preview
    rel_path = os.path.relpath(target, os.path.realpath(base)).replace(os.sep, '/')
    from urllib.parse import quote as _url_quote
    pdf_url = f'/api/files/preview?path={_url_quote(rel_path)}'
    file_name = _html_escape(os.path.basename(target))

    html = f'''<!DOCTYPE html>
<html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{file_name} - PDF Preview</title>
<script src="https://cdn.jsdelivr.net/npm/pdfjs-dist@4.0.379/build/pdf.min.mjs" type="module"></script>
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
       background: #525659; color: #333; overflow: hidden; display: flex; flex-direction: column; height: 100vh; }}
.toolbar {{ background: #323639; padding: 8px 12px; display: flex; align-items: center; gap: 10px;
            flex-shrink: 0; border-bottom: 1px solid #1e1e1e; }}
.toolbar button {{ background: #4a4d50; color: #e0e0e0; border: none; border-radius: 4px;
                   padding: 6px 12px; cursor: pointer; font-size: 13px; }}
.toolbar button:hover {{ background: #5a5d60; }}
.toolbar button:disabled {{ opacity: 0.4; cursor: default; }}
.toolbar span {{ color: #ccc; font-size: 13px; min-width: 80px; text-align: center; }}
.toolbar select {{ background: #4a4d50; color: #e0e0e0; border: none; border-radius: 4px;
                   padding: 4px 8px; font-size: 13px; }}
.toolbar .file-name {{ color: #aaa; font-size: 12px; margin-left: auto; max-width: 200px;
                       overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
.viewer {{ flex: 1; overflow: auto; padding: 10px; display: flex; flex-direction: column; align-items: center; }}
.page-container {{ margin: 5px 0; background: white; box-shadow: 0 1px 4px rgba(0,0,0,0.3); }}
.page-container canvas {{ display: block; }}
.loading {{ color: #aaa; padding: 40px; text-align: center; font-size: 16px; }}
</style>
</head><body>
<div class="toolbar">
  <button id="btnPrev" title="Previous Page">&#9664; Prev</button>
  <span id="pageInfo">- / -</span>
  <button id="btnNext" title="Next Page">Next &#9654;</button>
  <select id="zoomSelect" title="Zoom Level">
    <option value="0.5">50%</option>
    <option value="0.75">75%</option>
    <option value="1" selected>100%</option>
    <option value="1.25">125%</option>
    <option value="1.5">150%</option>
    <option value="2">200%</option>
  </select>
  <button id="btnText" title="Extract Text">Extract Text</button>
  <span class="file-name">{file_name}</span>
</div>
<div class="viewer" id="viewer">
  <div class="loading" id="loadingMsg">Loading PDF...</div>
</div>
<script type="module">
const pdfUrl = "{pdf_url}";
let pdfDoc = null;
let currentPage = 1;
let scale = 1.0;
let textMode = false;

const viewer = document.getElementById('viewer');
const pageInfo = document.getElementById('pageInfo');
const btnPrev = document.getElementById('btnPrev');
const btnNext = document.getElementById('btnNext');
const zoomSelect = document.getElementById('zoomSelect');
const btnText = document.getElementById('btnText');

async function loadPDF() {{
  try {{
    const pdfjsLib = await import('https://cdn.jsdelivr.net/npm/pdfjs-dist@4.0.379/build/pdf.min.mjs');
    pdfjsLib.GlobalWorkerOptions.workerSrc = 'https://cdn.jsdelivr.net/npm/pdfjs-dist@4.0.379/build/pdf.worker.min.mjs';
    pdfDoc = await pdfjsLib.getDocument(pdfUrl).promise;
    document.getElementById('loadingMsg').style.display = 'none';
    updatePageInfo();
    renderAllPages();
  }} catch(e) {{
    document.getElementById('loadingMsg').textContent = 'Failed to load PDF: ' + e.message;
    console.error(e);
  }}
}}

async function renderAllPages() {{
  viewer.innerHTML = '';
  for (let i = 1; i <= pdfDoc.numPages; i++) {{
    const page = await pdfDoc.getPage(i);
    const viewport = page.getViewport({{ scale }});
    const container = document.createElement('div');
    container.className = 'page-container';
    container.id = 'page-' + i;
    if (textMode) {{
      const textContent = await page.getTextContent();
      const div = document.createElement('div');
      div.style.cssText = 'padding:20px;max-width:900px;font-size:14px;line-height:1.8;white-space:pre-wrap;';
      let lastY = null;
      let lines = [];
      let currentLine = '';
      for (const item of textContent.items) {{
        if (lastY !== null && Math.abs(item.transform[5] - lastY) > 5) {{
          lines.push(currentLine);
          currentLine = '';
        }}
        currentLine += item.str;
        lastY = item.transform[5];
      }}
      if (currentLine) lines.push(currentLine);
      div.textContent = lines.join('\\n');
      container.appendChild(div);
    }} else {{
      const canvas = document.createElement('canvas');
      canvas.width = viewport.width;
      canvas.height = viewport.height;
      container.appendChild(canvas);
      const ctx = canvas.getContext('2d');
      await page.render({{ canvasContext: ctx, viewport }}).promise;
    }}
    viewer.appendChild(container);
  }}
  // Scroll to current page
  const target = document.getElementById('page-' + currentPage);
  if (target) target.scrollIntoView({{ behavior: 'auto' }});
}}

function updatePageInfo() {{
  if (!pdfDoc) return;
  pageInfo.textContent = currentPage + ' / ' + pdfDoc.numPages;
  btnPrev.disabled = currentPage <= 1;
  btnNext.disabled = currentPage >= pdfDoc.numPages;
}}

btnPrev.addEventListener('click', () => {{
  if (currentPage > 1) {{ currentPage--; updatePageInfo(); scrollToPage(); }}
}});
btnNext.addEventListener('click', () => {{
  if (pdfDoc && currentPage < pdfDoc.numPages) {{ currentPage++; updatePageInfo(); scrollToPage(); }}
}});
zoomSelect.addEventListener('change', (e) => {{
  scale = parseFloat(e.target.value);
  if (pdfDoc) renderAllPages();
}});
btnText.addEventListener('click', () => {{
  textMode = !textMode;
  btnText.style.background = textMode ? '#0078d4' : '#4a4d50';
  if (pdfDoc) renderAllPages();
}});
function scrollToPage() {{
  const target = document.getElementById('page-' + currentPage);
  if (target) target.scrollIntoView({{ behavior: 'smooth' }});
}}
// Intersection observer to update current page number on scroll
const observer = new IntersectionObserver((entries) => {{
  for (const entry of entries) {{
    if (entry.isIntersecting && entry.intersectionRatio > 0.3) {{
      const pageNum = parseInt(entry.target.id.replace('page-', ''));
      if (!isNaN(pageNum)) {{ currentPage = pageNum; updatePageInfo(); }}
    }}
  }}
}}, {{ root: viewer, threshold: 0.3 }});
// Re-observe after render
const origRender = renderAllPages;
renderAllPages = async function() {{
  await origRender();
  for (let i = 1; i <= pdfDoc.numPages; i++) {{
    const el = document.getElementById('page-' + i);
    if (el) observer.observe(el);
  }}
}};
loadPDF();
</script>
</body></html>'''
    return Response(html, mimetype='text/html; charset=utf-8')


def _pdf_to_html(filepath):
    """Convert PDF to HTML for preview (text extraction mode).
    Uses PyPDF2 to extract text content from each page and renders
    it as a styled HTML document with page separators."""
    from PyPDF2 import PdfReader

    reader = PdfReader(filepath)
    pages_html = []
    total_pages = len(reader.pages)

    for i, page in enumerate(reader.pages):
        try:
            text = page.extract_text() or ''
        except Exception:
            text = '(text extraction failed for this page)'

        # Escape HTML entities
        text = _html_escape(text)

        if text.strip():
            # Preserve line breaks from PDF text extraction
            formatted_text = text.replace('\n', '<br/>\n')
        else:
            formatted_text = '<p style="color:#999;font-style:italic">(This page has no extractable text — it may contain only images or scanned content)</p>'

        pages_html.append(f'''<div class="page">
<div class="page-header">Page {i + 1} of {total_pages}</div>
<div class="page-content">{formatted_text}</div>
</div>''')

    body = '\n'.join(pages_html)
    html = f'''<!DOCTYPE html>
<html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
       margin: 0; padding: 20px; background: #525659; color: #333; }}
.page {{ background: white; margin: 20px auto; max-width: 800px; padding: 40px 50px;
        border-radius: 2px; box-shadow: 0 2px 8px rgba(0,0,0,0.3); min-height: 200px; }}
.page-header {{ color: #999; font-size: 11px; text-align: center; margin-bottom: 20px;
               border-bottom: 1px solid #eee; padding-bottom: 8px; }}
.page-content {{ font-size: 13px; line-height: 1.8; white-space: pre-wrap; word-wrap: break-word; }}
.page-nav {{ position: fixed; bottom: 20px; right: 20px; display: flex; gap: 8px; z-index: 100; }}
.page-nav button {{ background: rgba(0,0,0,0.6); color: white; border: none; border-radius: 4px;
                    padding: 8px 14px; cursor: pointer; font-size: 13px; }}
.page-nav button:hover {{ background: rgba(0,0,0,0.8); }}
.page-nav span {{ color: white; font-size: 13px; line-height: 36px; }}
.info-bar {{ background: #323639; color: #aaa; padding: 8px 16px; font-size: 12px;
             text-align: center; position: sticky; top: 0; z-index: 50; }}
.info-bar a {{ color: #6db3f2; text-decoration: none; }}
.info-bar a:hover {{ text-decoration: underline; }}
</style></head><body>
<div class="info-bar">
  PDF Document &mdash; {total_pages} page{"s" if total_pages != 1 else ""} &mdash;
  <a href="/api/files/preview?path={_html_escape(os.path.relpath(filepath, os.path.realpath(load_config().get('workspace', WORKSPACE))).replace(os.sep, '/'))}">Download Original PDF</a>
</div>
{body}
</body></html>'''
    return Response(html, mimetype='text/html; charset=utf-8')


def _docx_to_html(filepath):
    """Convert DOCX to HTML for preview."""
    from docx import Document
    import json as _json

    doc = Document(filepath)
    sections = []

    for para in doc.paragraphs:
        text = para.text.strip()
        if not text:
            sections.append('<br/>')
            continue
        style = para.style.name if para.style else ''
        if 'Heading 1' in style:
            sections.append(f'<h1>{_html_escape(text)}</h1>')
        elif 'Heading 2' in style:
            sections.append(f'<h2>{_html_escape(text)}</h2>')
        elif 'Heading 3' in style:
            sections.append(f'<h3>{_html_escape(text)}</h3>')
        elif 'Heading 4' in style:
            sections.append(f'<h4>{_html_escape(text)}</h4>')
        else:
            # Process runs for bold/italic
            runs_html = []
            for run in para.runs:
                rtext = _html_escape(run.text)
                if run.bold:
                    rtext = f'<strong>{rtext}</strong>'
                if run.italic:
                    rtext = f'<em>{rtext}</em>'
                if run.underline:
                    rtext = f'<u>{rtext}</u>'
                runs_html.append(rtext)
            line = ''.join(runs_html) if runs_html else _html_escape(text)
            sections.append(f'<p>{line}</p>')

    # Process tables
    for table in doc.tables:
        rows_html = []
        for i, row in enumerate(table.rows):
            cells = []
            for cell in row.cells:
                cells.append(f'<td>{_html_escape(cell.text)}</td>')
            tag = 'th' if i == 0 else 'td'
            cells_str = ''.join(f'<{tag}>{_html_escape(cell.text)}</{tag}>' for cell in row.cells)
            rows_html.append(f'<tr>{cells_str}</tr>')
        sections.append(f'<table border="1" cellpadding="6" cellspacing="0">{"".join(rows_html)}</table>')

    body = '\n'.join(sections)
    html = f'''<!DOCTYPE html>
<html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
       max-width: 900px; margin: 0 auto; padding: 20px; line-height: 1.6; color: #333; }}
h1,h2,h3,h4 {{ margin-top: 1.5em; margin-bottom: 0.5em; }}
table {{ border-collapse: collapse; width: 100%; margin: 1em 0; }}
th,td {{ border: 1px solid #ddd; padding: 8px 12px; text-align: left; }}
th {{ background: #f5f5f5; }}
img {{ max-width: 100%; }}
</style></head><body>{body}</body></html>'''
    return Response(html, mimetype='text/html; charset=utf-8')


def _pptx_to_html(filepath):
    """Convert PPTX to HTML for preview with backgrounds, shapes, images, and styling."""
    import base64
    import io
    from pptx import Presentation
    from pptx.util import Inches, Pt, Emu
    from pptx.enum.shapes import MSO_SHAPE_TYPE
    from pptx.dml.color import RGBColor
    from lxml import etree

    prs = Presentation(filepath)
    slide_width_emu = prs.slide_width
    slide_height_emu = prs.slide_height

    # EMU to px conversion (1 inch = 914400 EMU, assume 96 DPI)
    def emu_to_px(emu):
        if emu is None:
            return 0
        return round(int(emu) / 914400 * 96)

    slide_w_px = emu_to_px(slide_width_emu)
    slide_h_px = emu_to_px(slide_height_emu)

    def _color_to_css(color):
        """Convert a python-pptx color to CSS color string."""
        if color is None:
            return None
        try:
            if color.type is not None and color.type == 1:  # RGB
                return f'#{color.rgb}'
            elif color.type is not None and color.type == 2:  # Theme
                # Theme colors — map to approximate CSS colors
                theme_map = {
                    0: '#000000',   # dk1
                    1: '#FFFFFF',   # lt1
                    2: '#44546A',   # dk2
                    3: '#E7E6E6',   # lt2
                    4: '#4472C4',   # accent1
                    5: '#ED7D31',   # accent2
                    6: '#A5A5A5',   # accent3
                    7: '#FFC000',   # accent4
                    8: '#5B9BD5',   # accent5
                    9: '#70AD47',   # accent6
                }
                idx = color.theme
                return theme_map.get(idx, '#333333')
            elif color.type is not None and color.type == 3:  # Scheme
                return '#333333'
        except Exception:
            pass
        try:
            if hasattr(color, 'rgb') and color.rgb:
                return f'#{color.rgb}'
        except Exception:
            pass
        return None

    def _get_fill_css(fill, default_color=None):
        """Get CSS background from a fill object."""
        try:
            if fill.type is None:
                return default_color or 'transparent'
            from pptx.oxml.ns import qn
            # type 1 = solid
            if fill.type == 1:  # SOLID
                color = _color_to_css(fill.fore_color)
                return color or default_color or 'transparent'
            # type 2 = gradient — use first stop color
            elif fill.type == 2:  # GRADIENT
                try:
                    color = _color_to_css(fill.fore_color)
                    return color or default_color or 'transparent'
                except Exception:
                    return default_color or 'transparent'
            # type 3 = patterned
            elif fill.type == 3:
                try:
                    color = _color_to_css(fill.fore_color)
                    return color or default_color or 'transparent'
                except Exception:
                    return default_color or 'transparent'
            # type 5 = background (inherit)
            elif fill.type == 5:
                return 'transparent'
        except Exception:
            pass
        return default_color or 'transparent'

    def _get_slide_bg_css(slide):
        """Get CSS background for a slide from its background fill."""
        bg = slide.background
        try:
            fill = bg.fill
            bg_css = _get_fill_css(fill)
            if bg_css and bg_css != 'transparent':
                return bg_css
        except Exception:
            pass

        # Try reading XML directly for more background options
        try:
            nsmap = {
                'a': 'http://schemas.openxmlformats.org/drawingml/2006/main',
                'p': 'http://schemas.openxmlformats.org/presentationml/2006/main',
                'r': 'http://schemas.openxmlformats.org/officeDocument/2006/relationships',
            }
            cSld = slide._element.find(qn('p:cSld'))
            if cSld is not None:
                bg_el = cSld.find(qn('p:bg'))
                if bg_el is not None:
                    # Check for bgRef (reference to theme)
                    bgRef = bg_el.find(qn('p:bgRef'))
                    if bgRef is not None:
                        idx = int(bgRef.get('idx', '0'))
                        theme_map = {
                            0: '#000000', 1: '#FFFFFF', 2: '#44546A', 3: '#E7E6E6',
                            4: '#4472C4', 5: '#ED7D31', 6: '#A5A5A5', 7: '#FFC000',
                            8: '#5B9BD5', 9: '#70AD47',
                        }
                        if idx in theme_map:
                            return theme_map[idx]
                    # Check for solid fill in bgPr
                    bgPr = bg_el.find(qn('p:bgPr'))
                    if bgPr is not None:
                        solidFill = bgPr.find(qn('a:solidFill'))
                        if solidFill is not None:
                            srgbClr = solidFill.find(qn('a:srgbClr'))
                            if srgbClr is not None:
                                return f'#{srgbClr.get("val", "FFFFFF")}'
                            schemeClr = solidFill.find(qn('a:schemeClr'))
                            if schemeClr is not None:
                                val = schemeClr.get('val', '')
                                scheme_map = {
                                    'dk1': '#000000', 'lt1': '#FFFFFF', 'dk2': '#44546A',
                                    'lt2': '#E7E6E6', 'accent1': '#4472C4', 'accent2': '#ED7D31',
                                    'accent3': '#A5A5A5', 'accent4': '#FFC000', 'accent5': '#5B9BD5',
                                    'accent6': '#70AD47', 'hlink': '#0563C1',
                                }
                                return scheme_map.get(val, '#FFFFFF')
                        # Check for gradient fill
                        gradFill = bgPr.find(qn('a:gradFill'))
                        if gradFill is not None:
                            gsLst = gradFill.find(qn('a:gsLst'))
                            if gsLst is not None:
                                first_gs = gsLst.find(qn('a:gs'))
                                if first_gs is not None:
                                    srgb = first_gs.find(qn('a:srgbClr'))
                                    if srgb is not None:
                                        return f'#{srgb.get("val", "FFFFFF")}'
        except Exception:
            pass

        return '#FFFFFF'  # Default white

    def _get_image_base64(slide, rId):
        """Extract image from PPTX by relationship ID and return base64 data URI."""
        try:
            part = slide.part
            rel = part.rels[rId]
            image_part = rel.target_part
            image_bytes = image_part.blob
            content_type = image_part.content_type
            if not content_type:
                # Guess from extension
                ext = image_part.partname.split('.')[-1].lower()
                ct_map = {'png': 'image/png', 'jpg': 'image/jpeg', 'jpeg': 'image/jpeg',
                          'gif': 'image/gif', 'bmp': 'image/bmp', 'svg': 'image/svg+xml',
                          'tiff': 'image/tiff', 'tif': 'image/tiff'}
                content_type = ct_map.get(ext, 'image/png')
            b64 = base64.b64encode(image_bytes).decode('ascii')
            return f'data:{content_type};base64,{b64}'
        except Exception:
            return None

    def _render_shape(slide, shape):
        """Render a single shape to HTML with positioning and styling."""
        parts = []

        left_px = emu_to_px(shape.left)
        top_px = emu_to_px(shape.top)
        width_px = emu_to_px(shape.width)
        height_px = emu_to_px(shape.height)

        style_parts = [f'position:absolute', f'left:{left_px}px', f'top:{top_px}px',
                       f'width:{width_px}px', f'height:{height_px}px']

        # Shape fill
        try:
            fill = shape.fill
            fill_css = _get_fill_css(fill)
            if fill_css and fill_css != 'transparent':
                style_parts.append(f'background:{fill_css}')
        except Exception:
            pass

        # Shape border/line
        try:
            line = shape.line
            if line.fill.type is not None and line.fill.type == 1:  # solid
                line_color = _color_to_css(line.color)
                line_width = line.width
                if line_color and line_width:
                    w_pt = int(line_width) / 12700  # EMU to pt
                    style_parts.append(f'border:{w_pt:.1f}pt solid {line_color}')
            elif line.fill.type is not None and line.fill.type == 5:  # no line
                style_parts.append('border:none')
        except Exception:
            pass

        # Rotation
        try:
            rotation = shape.rotation
            if rotation:
                style_parts.append(f'transform:rotate({rotation}deg)')
        except Exception:
            pass

        # Shadow (basic)
        try:
            shadow = shape.shadow
            if shadow.inherit is not None:
                style_parts.append('box-shadow:2px 2px 6px rgba(0,0,0,0.2)')
        except Exception:
            pass

        # Border radius for rounded shapes
        shape_type = None
        try:
            shape_type = shape.shape_type
        except Exception:
            pass

        content = ''

        # Picture shape
        if shape_type == MSO_SHAPE_TYPE.PICTURE or shape_type == MSO_SHAPE_TYPE.LINKED_PICTURE:
            try:
                # Get image from shape element
                nsmap_a = 'http://schemas.openxmlformats.org/drawingml/2006/main'
                nsmap_r = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
                sp = shape._element
                blipFill = sp.find('.//' + '{http://schemas.openxmlformats.org/drawingml/2006/main}blip')
                if blipFill is not None:
                    rId = blipFill.get('{http://schemas.openxmlformats.org/officeDocument/2006/relationships}embed')
                    if rId:
                        data_uri = _get_image_base64(slide, rId)
                        if data_uri:
                            content = f'<img src="{data_uri}" style="width:100%;height:100%;object-fit:contain">'
            except Exception:
                pass

        # Group shape — recurse into children
        elif shape_type == MSO_SHAPE_TYPE.GROUP:
            try:
                group_html = []
                for child in shape.shapes:
                    group_html.append(_render_shape(slide, child))
                content = ''.join(group_html)
                style_parts.append('overflow:visible')
            except Exception:
                pass

        # Text frame
        if not content and shape.has_text_frame:
            text_parts = []
            for para in shape.text_frame.paragraphs:
                runs_html = []
                para_style = []

                # Paragraph alignment
                try:
                    align = para.alignment
                    align_map = {1: 'left', 2: 'center', 3: 'right', 4: 'justify', 5: 'center', 6: 'left', 7: 'right'}
                    if align in align_map:
                        para_style.append(f'text-align:{align_map[align]}')
                except Exception:
                    pass

                for run in para.runs:
                    rtext = _html_escape(run.text)
                    # Font color
                    try:
                        color_css = _color_to_css(run.font.color)
                        if color_css:
                            rtext = f'<span style="color:{color_css}">{rtext}</span>'
                    except Exception:
                        pass
                    # Font size
                    try:
                        if run.font.size:
                            sz_pt = int(run.font.size) / 12700  # EMU to pt
                            rtext = f'<span style="font-size:{sz_pt:.0f}pt">{rtext}</span>'
                    except Exception:
                        pass
                    # Bold
                    if run.font.bold:
                        rtext = f'<strong>{rtext}</strong>'
                    # Italic
                    if run.font.italic:
                        rtext = f'<em>{rtext}</em>'
                    # Underline
                    try:
                        if run.font.underline:
                            rtext = f'<u>{rtext}</u>'
                    except Exception:
                        pass
                    runs_html.append(rtext)

                text = ''.join(runs_html) if runs_html else _html_escape(para.text)
                pstyle = f' style="{";".join(para_style)}"' if para_style else ''
                text_parts.append(f'<p{pstyle}>{text}</p>')

            content = ''.join(text_parts)
            # Add padding for text shapes
            if 'background' not in ' '.join(style_parts):
                style_parts.append('padding:4px 8px')

        # Table
        elif not content and shape.has_table:
            table = shape.table
            rows_html = []
            for ri, row in enumerate(table.rows):
                cells = []
                for cell in row.cells:
                    cell_text = _html_escape(cell.text)
                    cell_style = []
                    # Cell fill
                    try:
                        cell_fill = cell.fill
                        cell_bg = _get_fill_css(cell_fill)
                        if cell_bg and cell_bg != 'transparent':
                            cell_style.append(f'background:{cell_bg}')
                    except Exception:
                        pass
                    cstyle = f' style="{";".join(cell_style)}"' if cell_style else ''
                    tag = 'th' if ri == 0 else 'td'
                    cells.append(f'<{tag}{cstyle}>{cell_text}</{tag}>')
                rows_html.append(f'<tr>{"".join(cells)}</tr>')
            content = f'<table border="1" cellpadding="6" cellspacing="0" style="width:100%;border-collapse:collapse;font-size:14px">{"".join(rows_html)}</table>'

        # Fallback for shapes with no content
        if not content:
            # Just render as a colored rectangle
            if 'background' not in ' '.join(style_parts):
                content = ''

        style_str = ';'.join(style_parts)
        parts.append(f'<div class="shape" style="{style_str}">{content}</div>')
        return ''.join(parts)

    # Build each slide
    slides_html = []
    for i, slide in enumerate(prs.slides):
        bg_css = _get_slide_bg_css(slide)

        # Render all shapes with absolute positioning
        shapes_html = []
        for shape in slide.shapes:
            try:
                shapes_html.append(_render_shape(slide, shape))
            except Exception:
                # Fallback: extract text at least
                try:
                    if shape.has_text_frame:
                        text = _html_escape(shape.text)
                        shapes_html.append(f'<div class="shape" style="position:absolute;left:{emu_to_px(shape.left)}px;top:{emu_to_px(shape.top)}px;width:{emu_to_px(shape.width)}px;font-size:18px">{text}</div>')
                except Exception:
                    pass

        shapes_content = '\n'.join(shapes_html)
        slides_html.append(f'''<div class="slide" style="background:{bg_css}">
<div class="slide-number">Slide {i + 1} / {len(prs.slides)}</div>
<div class="slide-canvas" style="position:relative;width:{slide_w_px}px;height:{slide_h_px}px">
{shapes_content}
</div>
</div>''')

    body = '\n'.join(slides_html)
    html = f'''<!DOCTYPE html>
<html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
       margin: 0; padding: 20px; background: #f0f0f0; color: #333; }}
.slide {{ margin: 20px auto; max-width: {slide_w_px + 40}px; padding: 0;
          border-radius: 8px; box-shadow: 0 2px 8px rgba(0,0,0,0.15); overflow: hidden; }}
.slide-number {{ color: rgba(255,255,255,0.7); font-size: 11px; padding: 4px 12px; text-align: right; background: rgba(0,0,0,0.3); position: relative; z-index: 10; }}
.slide-canvas {{ transform-origin: top left; margin: 0 auto; }}
.shape {{ box-sizing: border-box; overflow: hidden; }}
.shape p {{ margin: 2px 0; line-height: 1.4; }}
.shape img {{ display: block; }}
table {{ border-collapse: collapse; width: 100%; margin: 4px 0; }}
th,td {{ border: 1px solid #ddd; padding: 6px 10px; text-align: left; font-size: 13px; }}
th {{ background: #f5f5f5; font-weight: bold; }}
@media (max-width: 1000px) {{
    .slide-canvas {{ transform: scale(calc((100vw - 80px) / {slide_w_px})); }}
    .slide {{ max-width: calc(100vw - 40px); }}
}}
</style></head><body>{body}</body></html>'''
    return Response(html, mimetype='text/html; charset=utf-8')


def _xlsx_to_html(filepath):
    """Convert XLSX to HTML for preview."""
    from openpyxl import load_workbook

    wb = load_workbook(filepath, read_only=True, data_only=True)
    sheets_html = []

    for sheet_name in wb.sheetnames:
        ws = wb[sheet_name]
        rows_html = []
        for ri, row in enumerate(ws.iter_rows(max_row=200, max_col=50, values_only=False)):
            cells = []
            for cell in row:
                val = cell.value if cell.value is not None else ''
                val_str = _html_escape(str(val))
                tag = 'th' if ri == 0 else 'td'
                cells.append(f'<{tag}>{val_str}</{tag}>')
            if cells:
                rows_html.append(f'<tr>{"".join(cells)}</tr>')

        table_html = f'<table border="1" cellpadding="6" cellspacing="0">{"".join(rows_html)}</table>'
        sheet_label = sheet_name if sheet_name == wb.sheetnames[0] else sheet_name
        sheets_html.append(f'''<div class="sheet">
<h3 class="sheet-title">{_html_escape(sheet_label)}</h3>
{table_html}
</div>''')

    wb.close()
    body = '\n'.join(sheets_html)

    # If multiple sheets, add tab navigation
    if len(wb.sheetnames) > 1:
        tabs = ''.join(f'<button class="sheet-tab" onclick="showSheet({i})">{_html_escape(name)}</button>'
                       for i, name in enumerate(wb.sheetnames))
        nav = f'<div class="sheet-nav">{tabs}</div>'
    else:
        nav = ''

    html = f'''<!DOCTYPE html>
<html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
       max-width: 1200px; margin: 0 auto; padding: 20px; line-height: 1.4; color: #333; }}
.sheet {{ margin: 16px 0; }}
.sheet-title {{ margin-bottom: 8px; color: #555; }}
.sheet-nav {{ display: flex; gap: 8px; margin-bottom: 16px; flex-wrap: wrap; }}
.sheet-tab {{ padding: 6px 16px; border: 1px solid #ccc; border-radius: 4px; background: #f8f8f8;
              cursor: pointer; font-size: 14px; }}
.sheet-tab.active {{ background: #0078d4; color: white; border-color: #0078d4; }}
table {{ border-collapse: collapse; width: 100%; margin: 0 0 1em; font-size: 13px; }}
th,td {{ border: 1px solid #ddd; padding: 4px 8px; text-align: left; white-space: nowrap; }}
th {{ background: #f5f5f5; font-weight: 600; }}
tr:nth-child(even) {{ background: #fafafa; }}
</style></head><body>
{nav}
{body}
<script>
(function() {{
    const sheets = document.querySelectorAll('.sheet');
    const tabs = document.querySelectorAll('.sheet-tab');
    if (sheets.length > 1) {{
        sheets.forEach((s, i) => {{ if (i > 0) s.style.display = 'none'; }});
        if (tabs[0]) tabs[0].classList.add('active');
    }}
    window.showSheet = function(idx) {{
        sheets.forEach((s, i) => {{ s.style.display = i === idx ? '' : 'none'; }});
        tabs.forEach((t, i) => {{ t.classList.toggle('active', i === idx); }});
    }};
}})();
</script>
</body></html>'''
    return Response(html, mimetype='text/html; charset=utf-8')


def _html_escape(text):
    """Simple HTML entity escaping."""
    if not isinstance(text, str):
        text = str(text)
    return text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('"', '&quot;')
