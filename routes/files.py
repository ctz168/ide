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
    """Convert Office documents (docx/pptx/xlsx) to HTML for in-browser preview."""
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
        else:
            return jsonify({'error': f'Unsupported file type: {ext}'}), 400
    except Exception as e:
        return jsonify({'error': f'Preview failed: {str(e)}'}), 500


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
    """Convert PPTX to HTML for preview."""
    from pptx import Presentation
    from pptx.util import Inches, Pt

    prs = Presentation(filepath)
    slides_html = []

    for i, slide in enumerate(prs.slides):
        shapes_html = []
        for shape in slide.shapes:
            if shape.has_text_frame:
                for para in shape.text_frame.paragraphs:
                    runs_html = []
                    for run in para.runs:
                        rtext = _html_escape(run.text)
                        if run.font.bold:
                            rtext = f'<strong>{rtext}</strong>'
                        if run.font.italic:
                            rtext = f'<em>{rtext}</em>'
                        runs_html.append(rtext)
                    text = ''.join(runs_html) if runs_html else _html_escape(para.text)
                    if text.strip():
                        shapes_html.append(f'<p>{text}</p>')
            elif shape.has_table:
                table = shape.table
                rows_html = []
                for ri, row in enumerate(table.rows):
                    cells_str = ''.join(f'<td>{_html_escape(cell.text)}</td>' for cell in row.cells)
                    rows_html.append(f'<tr>{cells_str}</tr>')
                shapes_html.append(f'<table border="1" cellpadding="6" cellspacing="0">{"".join(rows_html)}</table>')

        slide_content = '\n'.join(shapes_html) if shapes_html else '<p style="color:#999">(empty slide)</p>'
        slides_html.append(f'''<div class="slide">
<div class="slide-number">Slide {i + 1} / {len(prs.slides)}</div>
<div class="slide-content">{slide_content}</div>
</div>''')

    body = '\n'.join(slides_html)
    html = f'''<!DOCTYPE html>
<html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
       margin: 0; padding: 20px; background: #f0f0f0; color: #333; }}
.slide {{ background: white; margin: 20px auto; max-width: 960px; padding: 40px;
          border-radius: 8px; box-shadow: 0 2px 8px rgba(0,0,0,0.15); min-height: 200px; }}
.slide-number {{ color: #999; font-size: 12px; margin-bottom: 16px; border-bottom: 1px solid #eee; padding-bottom: 8px; }}
.slide-content {{ font-size: 18px; line-height: 1.6; }}
.slide-content h1 {{ font-size: 28px; }}
.slide-content h2 {{ font-size: 24px; }}
table {{ border-collapse: collapse; width: 100%; margin: 1em 0; }}
th,td {{ border: 1px solid #ddd; padding: 8px 12px; text-align: left; }}
th {{ background: #f5f5f5; }}
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
