"""Locate the Node.js executable used by the image and PDF renderers.

The backend is often started from a shell whose PATH lacks Node (fnm only adds it to
interactive shells), so look in the usual install locations too.
"""
from functools import lru_cache
import os
from pathlib import Path
import re
import shutil


def _version(path):
    match = re.search(r'v(\d+)\.(\d+)\.(\d+)', str(path))
    return tuple(int(part) for part in match.groups()) if match else (0, 0, 0)


@lru_cache(maxsize=1)
def node_executable():
    configured = os.environ.get('WXHOOK_NODE', '').strip()
    if configured and Path(configured).is_file():
        return configured
    found = shutil.which('node')
    if found:
        return found
    name = 'node.exe' if os.name == 'nt' else 'node'
    candidates = []
    for base in filter(None, (os.environ.get('FNM_DIR'), os.environ.get('APPDATA') and str(Path(os.environ['APPDATA']) / 'fnm'),
                              str(Path.home() / '.local/share/fnm'))):
        root = Path(base)
        default = root / 'aliases/default' / name
        if default.is_file():
            return str(default)
        candidates += root.glob(f'node-versions/*/installation/{name}')
    if os.name == 'nt':
        candidates += [Path(os.environ.get('ProgramFiles', r'C:\Program Files')) / 'nodejs' / name]
    candidates = [path for path in candidates if path.is_file()]
    return str(max(candidates, key=_version)) if candidates else 'node'


def failure_tail(result, limit=240):
    """Last meaningful line of a renderer's output, for showing why it failed."""
    output = result.stderr or result.stdout or ''
    if isinstance(output, bytes):
        output = output.decode('utf-8', 'replace')
    lines = [line.strip() for line in output.splitlines() if line.strip() and not set(line.strip()) <= set('║╔╗╚╝═|<3 ')]
    return (lines[0] if lines else f'退出码 {result.returncode}')[:limit]
