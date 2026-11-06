"""Batch availability hints for exact files inside one verified account root."""
from __future__ import annotations

import os
import stat
from pathlib import Path

from .sns_media import build_sns_cache_key


IMAGE_SUFFIXES = ('',)
POSTER_SUFFIXES = ('.jpg', '.jpeg', '.png', '.webp', '')
VIDEO_SUFFIXES = ('.mp4', '.tmp', '.m4v', '.mov', '')


def _directory(path, account_root):
    try:
        resolved = path.resolve()
        if resolved.is_relative_to(account_root) and resolved.is_dir():
            return resolved
    except (OSError, RuntimeError, ValueError):
        pass
    return None


def _media_roots(account_root):
    """Enumerate only cache's immediate children, never media or account trees."""
    cache = _directory(account_root / 'cache', account_root)
    sns = []
    if cache is not None:
        sns.append(cache / 'Sns')
        try:
            with os.scandir(cache) as entries:
                months = [Path(entry.path) for entry in entries if entry.is_dir()]
            sns.extend(month / 'Sns' for month in sorted(months, reverse=True))
        except OSError:
            pass
    sns.append(account_root / 'Sns')
    result = {'image': [], 'video': []}
    for candidate in sns:
        for kind, folder in (('image', 'Img'), ('video', 'Video')):
            directory = _directory(candidate / folder, account_root)
            if directory is not None and directory not in result[kind]:
                result[kind].append(directory)
    return result


def _probe(directory, key, suffix, account_root):
    candidate = directory / key[:2] / (key[2:] + suffix)
    try:
        # Missing files are common; resolve paths only after the exact file exists.
        if not stat.S_ISREG(os.stat(candidate, follow_symlinks=False).st_mode):
            return False
        resolved = candidate.resolve()
        return resolved.is_relative_to(directory) and resolved.is_relative_to(account_root)
    except (OSError, RuntimeError, ValueError):
        return False


def scan_media(items, root: Path):
    """Return {post_id: [{preview: bool, source: bool}]} without persistent hints."""
    result = {item['id']: [] for item in items}
    if not any(item['_media'] for item in items):
        return result
    try:
        account_root = Path(root).resolve()
        roots = _media_roots(account_root)
    except (OSError, RuntimeError, ValueError):
        roots = {'image': [], 'video': []}
        account_root = Path(root)
    checked, checked_paths = {}, {}

    def exists(directory, key, suffix):
        candidate = (directory, key, suffix)
        if candidate not in checked_paths:
            checked_paths[candidate] = _probe(directory, key, suffix, account_root)
        return checked_paths[candidate]

    def available(ident, media_id, variant, kind, suffixes):
        key = build_sns_cache_key(ident, media_id, variant)
        lookup = (kind, key, suffixes)
        if lookup not in checked:
            checked[lookup] = bool(key) and any(
                exists(directory, key, suffix)
                for directory in roots[kind] for suffix in suffixes
            )
        return checked[lookup]

    for item in items:
        for media in item['_media']:
            ident, media_id = item['id'], media['id']
            if media['type'] == 6:
                preview = available(ident, media_id, 3, 'video', POSTER_SUFFIXES) or any(
                    available(ident, media_id, variant, 'image', IMAGE_SUFFIXES) for variant in (1, 6, 2)
                )
                source = available(ident, media_id, 3, 'video', VIDEO_SUFFIXES)
            else:
                preview = any(available(ident, media_id, variant, 'image', IMAGE_SUFFIXES)
                              for variant in (6, 1, 2, 3))
                source = preview
            result[ident].append({'preview': preview, 'source': source})
    return result
