"""
PhoneIDE - File management API routes.
"""

import os
import re
import fnmatch
from pathlib import Path
from datetime import datetime
from flask import Blueprint, jsonify, request
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

    target = os.path.join(base, path) if path else base
    target = os.path.realpath(target)

    # Security: must be under workspace
    if not target.startswith(os.path.realpath(base)):
        return jsonify({'error': 'Access denied'}), 403

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
                        'path': os.path.relpath(full, base),
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

    return jsonify({'items': items, 'path': path, 'base': base})


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
    data = request.json
    path = data.get('path', '')
    is_dir = data.get('is_dir', False)
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
    config = load_config()
    base = config.get('workspace', WORKSPACE)

    # Validate base
    real_base = os.path.realpath(base)
    if not os.path.isdir(real_base):
        return jsonify({'results': [], 'total': 0})

    results = []
    search_text = pattern if pattern else query

    try:
        flags = 0 if case_sensitive else re.IGNORECASE
        if use_regex:
            regex = re.compile(search_text, flags)
        else:
            regex = re.compile(re.escape(search_text), flags)

        for root, dirs, files in os.walk(real_base):
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
                                rel = os.path.relpath(fpath, real_base)
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
