"""
PhoneIDE - Code Formatter API routes.
Provides automatic code formatting for multiple languages using external tools.
"""

import os
import subprocess
import json
import shutil
from flask import Blueprint, jsonify, request
from utils import handle_error, load_config, WORKSPACE, shlex_quote

bp = Blueprint('formatter', __name__)

# ==================== Formatter Config ====================

# Mapping of file extensions to formatter commands
# Each entry: (check_cmd, format_cmd, [args])
# check_cmd: returns 0 if formatter is available
# format_cmd: command to format the file in-place
FORMATTERS = {
    # Python - Black
    '.py': {
        'name': 'Black',
        'check': ['black', '--version'],
        'format': ['black', '-'],
        'format_file': ['black', '--quiet', '--target-version', 'py38', '{path}'],
        'install': 'pip install black',
        'description': 'Python code formatter (PEP 8 compliant)',
    },
    # JavaScript/TypeScript - Prettier
    '.js': {
        'name': 'Prettier',
        'check': ['prettier', '--version'],
        'format': ['prettier', '--stdin-filepath', 'file.js'],
        'format_file': ['prettier', '--write', '{path}'],
        'install': 'npm install -g prettier',
        'description': 'JavaScript/TypeScript/CSS/HTML formatter',
    },
    '.jsx': {
        'name': 'Prettier',
        'check': ['prettier', '--version'],
        'format': ['prettier', '--stdin-filepath', 'file.jsx'],
        'format_file': ['prettier', '--write', '{path}'],
        'install': 'npm install -g prettier',
        'description': 'JavaScript/TypeScript/CSS/HTML formatter',
    },
    '.ts': {
        'name': 'Prettier',
        'check': ['prettier', '--version'],
        'format': ['prettier', '--stdin-filepath', 'file.ts'],
        'format_file': ['prettier', '--write', '{path}'],
        'install': 'npm install -g prettier',
        'description': 'JavaScript/TypeScript/CSS/HTML formatter',
    },
    '.tsx': {
        'name': 'Prettier',
        'check': ['prettier', '--version'],
        'format': ['prettier', '--stdin-filepath', 'file.tsx'],
        'format_file': ['prettier', '--write', '{path}'],
        'install': 'npm install -g prettier',
        'description': 'JavaScript/TypeScript/CSS/HTML formatter',
    },
    # HTML
    '.html': {
        'name': 'Prettier',
        'check': ['prettier', '--version'],
        'format': ['prettier', '--stdin-filepath', 'file.html'],
        'format_file': ['prettier', '--write', '{path}'],
        'install': 'npm install -g prettier',
        'description': 'HTML formatter',
    },
    '.htm': {
        'name': 'Prettier',
        'check': ['prettier', '--version'],
        'format': ['prettier', '--stdin-filepath', 'file.htm'],
        'format_file': ['prettier', '--write', '{path}'],
        'install': 'npm install -g prettier',
        'description': 'HTML formatter',
    },
    # CSS/SCSS
    '.css': {
        'name': 'Prettier',
        'check': ['prettier', '--version'],
        'format': ['prettier', '--stdin-filepath', 'file.css'],
        'format_file': ['prettier', '--write', '{path}'],
        'install': 'npm install -g prettier',
        'description': 'CSS formatter',
    },
    '.scss': {
        'name': 'Prettier',
        'check': ['prettier', '--version'],
        'format': ['prettier', '--stdin-filepath', 'file.scss'],
        'format_file': ['prettier', '--write', '{path}'],
        'install': 'npm install -g prettier',
        'description': 'SCSS formatter',
    },
    '.less': {
        'name': 'Prettier',
        'check': ['prettier', '--version'],
        'format': ['prettier', '--stdin-filepath', 'file.less'],
        'format_file': ['prettier', '--write', '{path}'],
        'install': 'npm install -g prettier',
        'description': 'LESS formatter',
    },
    # JSON/YAML
    '.json': {
        'name': 'Prettier',
        'check': ['prettier', '--version'],
        'format': ['prettier', '--stdin-filepath', 'file.json'],
        'format_file': ['prettier', '--write', '{path}'],
        'install': 'npm install -g prettier',
        'description': 'JSON formatter',
    },
    '.yaml': {
        'name': 'Prettier',
        'check': ['prettier', '--version'],
        'format': ['prettier', '--stdin-filepath', 'file.yaml'],
        'format_file': ['prettier', '--write', '{path}'],
        'install': 'npm install -g prettier',
        'description': 'YAML formatter',
    },
    '.yml': {
        'name': 'Prettier',
        'check': ['prettier', '--version'],
        'format': ['prettier', '--stdin-filepath', 'file.yml'],
        'format_file': ['prettier', '--write', '{path}'],
        'install': 'npm install -g prettier',
        'description': 'YAML formatter',
    },
    # Markdown
    '.md': {
        'name': 'Prettier',
        'check': ['prettier', '--version'],
        'format': ['prettier', '--stdin-filepath', 'file.md'],
        'format_file': ['prettier', '--write', '{path}'],
        'install': 'npm install -g prettier',
        'description': 'Markdown formatter',
    },
    # Shell
    '.sh': {
        'name': 'Shfmt',
        'check': ['shfmt', '-version'],
        'format': ['shfmt'],
        'format_file': ['shfmt', '-w', '{path}'],
        'install': 'go install mvdan.cc/sh/v3/cmd/shfmt@latest',
        'description': 'Shell script formatter',
    },
    '.bash': {
        'name': 'Shfmt',
        'check': ['shfmt', '-version'],
        'format': ['shfmt'],
        'format_file': ['shfmt', '-w', '{path}'],
        'install': 'go install mvdan.cc/sh/v3/cmd/shfmt@latest',
        'description': 'Shell script formatter',
    },
    # Go
    '.go': {
        'name': 'Gofmt',
        'check': ['gofmt', '-version'],
        'format': ['gofmt'],
        'format_file': ['gofmt', '-w', '{path}'],
        'install': 'go install (included with Go)',
        'description': 'Go code formatter',
    },
    # Rust
    '.rs': {
        'name': 'Rustfmt',
        'check': ['rustfmt', '--version'],
        'format': ['rustfmt'],
        'format_file': ['rustfmt', '{path}'],
        'install': 'rustup component add rustfmt',
        'description': 'Rust code formatter',
    },
    # SQL
    '.sql': {
        'name': 'sqlfmt',
        'check': ['sqlfmt', '--version'],
        'format': ['sqlfmt'],
        'format_file': ['sqlfmt', '-w', '{path}'],
        'install': 'go install github.com/jackc/sqlfmt/cmd/sqlfmt@latest',
        'description': 'SQL formatter',
    },
    # C/C++
    '.c': {
        'name': 'Clang-Format',
        'check': ['clang-format', '--version'],
        'format': ['clang-format'],
        'format_file': ['clang-format', '-i', '{path}'],
        'install': 'apt install clang-format / brew install clang-format',
        'description': 'C/C++ formatter',
    },
    '.cpp': {
        'name': 'Clang-Format',
        'check': ['clang-format', '--version'],
        'format': ['clang-format'],
        'format_file': ['clang-format', '-i', '{path}'],
        'install': 'apt install clang-format / brew install clang-format',
        'description': 'C/C++ formatter',
    },
    '.h': {
        'name': 'Clang-Format',
        'check': ['clang-format', '--version'],
        'format': ['clang-format'],
        'format_file': ['clang-format', '-i', '{path}'],
        'install': 'apt install clang-format / brew install clang-format',
        'description': 'C/C++ formatter',
    },
    '.hpp': {
        'name': 'Clang-Format',
        'check': ['clang-format', '--version'],
        'format': ['clang-format'],
        'format_file': ['clang-format', '-i', '{path}'],
        'install': 'apt install clang-format / brew install clang-format',
        'description': 'C/C++ formatter',
    },
    # Java
    '.java': {
        'name': 'Google Java Format',
        'check': ['google-java-format', '--version'],
        'format': ['google-java-format', '-'],
        'format_file': ['google-java-format', '-i', '{path}'],
        'install': 'Download from https://github.com/google/google-java-format',
        'description': 'Java code formatter',
    },
    # XML
    '.xml': {
        'name': 'Prettier',
        'check': ['prettier', '--version'],
        'format': ['prettier', '--stdin-filepath', 'file.xml'],
        'format_file': ['prettier', '--write', '{path}'],
        'install': 'npm install -g prettier',
        'description': 'XML formatter',
    },
}

# ==================== Helper Functions ====================

def get_formatter_for_ext(ext):
    """Get formatter config for a file extension."""
    return FORMATTERS.get(ext.lower())


def is_formatter_available(formatter_config):
    """Check if a formatter is installed and available in PATH."""
    try:
        result = subprocess.run(
            formatter_config['check'],
            capture_output=True,
            text=True,
            timeout=2
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def format_content(content, formatter_config):
    """Format content via stdin using the formatter."""
    try:
        proc = subprocess.run(
            formatter_config['format'],
            input=content,
            capture_output=True,
            text=True,
            timeout=30
        )
        if proc.returncode == 0:
            return {'ok': True, 'formatted': proc.stdout}
        else:
            return {'ok': False, 'error': proc.stderr or 'Formatting failed'}
    except subprocess.TimeoutExpired:
        return {'ok': False, 'error': 'Formatting timed out'}
    except Exception as e:
        return {'ok': False, 'error': str(e)}


def format_file_path(file_path, formatter_config):
    """Format a file in-place."""
    try:
        # Backup original file
        backup_path = file_path + '.backup'
        shutil.copy2(file_path, backup_path)

        # Run formatter
        cmd = [arg.format(path=file_path) for arg in formatter_config['format_file']]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)

        if result.returncode == 0:
            # Verify file changed
            with open(file_path, 'r') as f:
                new_content = f.read()
            with open(backup_path, 'r') as f:
                old_content = f.read()

            if new_content != old_content:
                os.remove(backup_path)
                return {'ok': True, 'changed': True}
            else:
                # No changes needed
                os.remove(backup_path)
                return {'ok': True, 'changed': False}
        else:
            # Restore backup
            shutil.copy2(backup_path, file_path)
            os.remove(backup_path)
            return {'ok': False, 'error': result.stderr or 'Formatting failed'}
    except subprocess.TimeoutExpired:
        # Restore backup
        if os.path.exists(backup_path):
            shutil.copy2(backup_path, file_path)
            os.remove(backup_path)
        return {'ok': False, 'error': 'Formatting timed out'}
    except Exception as e:
        # Restore backup
        if os.path.exists(backup_path):
            shutil.copy2(backup_path, file_path)
            os.remove(backup_path)
        return {'ok': False, 'error': str(e)}


# ==================== API Routes ====================

@bp.route('/api/formatter/available', methods=['GET'])
@handle_error
def list_available_formatters():
    """
    List all supported formatters and their availability status.
    Returns: {
        'formatters': [
            {
                'extension': '.py',
                'name': 'Black',
                'available': true/false,
                'description': '...',
                'install_cmd': 'pip install black'
            },
            ...
        ]
    }
    """
    config = load_config()
    base = config.get('workspace', WORKSPACE)

    result = []
    for ext, fmt in sorted(FORMATTERS.items()):
        available = is_formatter_available(fmt)
        result.append({
            'extension': ext,
            'name': fmt['name'],
            'available': available,
            'description': fmt.get('description', ''),
            'install': fmt.get('install', ''),
        })

    return jsonify({'formatters': result})


@bp.route('/api/formatter/format', methods=['POST'])
@handle_error
def format_content_endpoint():
    """
    Format code content in-memory (without saving to file).
    Expects JSON: {
        'content': 'code content',
        'file_path': '/path/to/file.py'  # used to determine formatter
    }
    Returns: {
        'ok': true/false,
        'formatted': 'formatted code' (if ok),
        'error': 'error message' (if not ok)
    }
    """
    data = request.json or {}
    content = data.get('content', '')
    file_path = data.get('file_path', '')

    if not content:
        return jsonify({'error': 'Content required'}), 400

    if not file_path:
        return jsonify({'error': 'File path required to detect language'}), 400

    ext = os.path.splitext(file_path)[1].lower()
    formatter = get_formatter_for_ext(ext)

    if not formatter:
        return jsonify({
            'error': f'No formatter available for extension "{ext}"',
            'supported': list(FORMATTERS.keys())
        }), 400

    if not is_formatter_available(formatter):
        return jsonify({
            'error': f'Formatter "{formatter["name"]}" is not installed',
            'install': formatter.get('install', '')
        }), 400

    result = format_content(content, formatter)
    return jsonify(result)


@bp.route('/api/formatter/format-file', methods=['POST'])
@handle_error
def format_file_endpoint():
    """
    Format a file on disk in-place.
    Expects JSON: {
        'path': 'relative/path/to/file.py'
    }
    Returns: {
        'ok': true/false,
        'changed': true/false,
        'error': 'error message' (if not ok),
        'backup_restored': true/false
    }
    """
    data = request.json or {}
    rel_path = data.get('path', '')

    if not rel_path:
        return jsonify({'error': 'File path required'}), 400

    config = load_config()
    base = config.get('workspace', WORKSPACE)
    target = os.path.realpath(os.path.join(base, rel_path))

    if not target.startswith(os.path.realpath(base)):
        return jsonify({'error': 'Access denied'}), 403

    if not os.path.isfile(target):
        return jsonify({'error': 'File not found'}), 404

    ext = os.path.splitext(target)[1].lower()
    formatter = get_formatter_for_ext(ext)

    if not formatter:
        return jsonify({
            'error': f'No formatter available for extension "{ext}"',
            'supported': list(FORMATTERS.keys())
        }), 400

    if not is_formatter_available(formatter):
        return jsonify({
            'error': f'Formatter "{formatter["name"]}" is not installed',
            'install': formatter.get('install', ''),
            'hint': f'Run: {formatter.get("install", "")}'
        }), 400

    result = format_file_path(target, formatter)
    result['formatter'] = formatter['name']
    result['file'] = rel_path
    return jsonify(result)


@bp.route('/api/formatter/format-workspace', methods=['POST'])
@handle_error
def format_workspace_endpoint():
    """
    Format all supported files in a directory (recursively).
    Expects JSON: {
        'path': 'relative/path/to/dir',  # optional, defaults to workspace root
        'extensions': ['.py', '.js', '.ts'],  # optional, defaults to all supported
        'dry_run': false  # if true, only report what would change
    }
    Returns: {
        'ok': true,
        'total': 100,
        'formatted': 45,
        'skipped': 55,
        'errors': [...],
        'details': [{'file': '...', 'changed': true/false, 'error': '...'}]
    }
    """
    data = request.json or {}
    rel_path = data.get('path', '')
    extensions = data.get('extensions', list(FORMATTERS.keys()))
    dry_run = data.get('dry_run', False)

    config = load_config()
    base = config.get('workspace', WORKSPACE)
    target_dir = os.path.realpath(os.path.join(base, rel_path)) if rel_path else os.path.realpath(base)

    if not target_dir.startswith(os.path.realpath(base)):
        return jsonify({'error': 'Access denied'}), 403

    if not os.path.isdir(target_dir):
        return jsonify({'error': 'Directory not found'}), 404

    results = {
        'ok': True,
        'total': 0,
        'formatted': 0,
        'skipped': 0,
        'errors': [],
        'details': []
    }

    for root, dirs, files in os.walk(target_dir):
        # Skip common ignore dirs
        dirs[:] = [d for d in dirs if d not in {'.git', '__pycache__', 'node_modules', '.venv', 'venv', '.idea', '.vscode', 'dist', 'build', 'target'}]

        for fname in sorted(files):
            fpath = os.path.join(root, fname)
            ext = os.path.splitext(fname)[1].lower()

            if ext not in extensions:
                continue

            results['total'] += 1
            rel = os.path.relpath(fpath, base)

            # Check if formatter exists for this extension
            formatter = get_formatter_for_ext(ext)
            if not formatter:
                results['skipped'] += 1
                results['details'].append({
                    'file': rel,
                    'skipped': True,
                    'reason': f'No formatter for {ext}'
                })
                continue

            if not is_formatter_available(formatter):
                results['errors'].append(f'{rel}: Formatter "{formatter["name"]}" not installed')
                results['details'].append({
                    'file': rel,
                    'error': f'Formatter not installed: {formatter["name"]}',
                    'install': formatter.get('install', '')
                })
                continue

            if dry_run:
                # Just check if file would change
                try:
                    with open(fpath, 'r') as f:
                        content = f.read()
                    fmt_result = format_content(content, formatter)
                    if fmt_result['ok']:
                        changed = fmt_result['formatted'] != content
                        results['details'].append({
                            'file': rel,
                            'would_change': changed
                        })
                        if changed:
                            results['formatted'] += 1
                        else:
                            results['skipped'] += 1
                    else:
                        results['errors'].append(f'{rel}: {fmt_result.get("error", "Format check failed")}')
                        results['details'].append({
                            'file': rel,
                            'error': fmt_result.get('error', 'Format check failed')
                        })
                except Exception as e:
                    results['errors'].append(f'{rel}: {str(e)}')
                    results['details'].append({'file': rel, 'error': str(e)})
            else:
                # Actually format the file
                fmt_result = format_file_path(fpath, formatter)
                if fmt_result['ok']:
                    if fmt_result.get('changed', False):
                        results['formatted'] += 1
                        results['details'].append({
                            'file': rel,
                            'changed': True,
                            'formatter': formatter['name']
                        })
                    else:
                        results['skipped'] += 1
                        results['details'].append({
                            'file': rel,
                            'changed': False
                        })
                else:
                    results['errors'].append(f'{rel}: {fmt_result.get("error", "Format failed")}')
                    results['details'].append({
                        'file': rel,
                        'error': fmt_result.get('error', 'Format failed')
                    })

    return jsonify(results)
