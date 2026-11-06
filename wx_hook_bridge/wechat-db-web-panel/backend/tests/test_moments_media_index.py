import os
from pathlib import Path

import pytest

from backend.app import moments_media_index as index
from backend.app.moments import Moments
from backend.app.sns_media import build_sns_cache_key


def post(ident='1', media_id='2', kind=2):
    return {'id': ident, '_media': [{'id': media_id, 'type': kind}]}


def cache_file(root, item, variant, kind='Img', suffix='', layout='cache/2026-09/Sns'):
    media = item['_media'][0]
    key = build_sns_cache_key(item['id'], media['id'], variant)
    path = root / layout / kind / key[:2] / (key[2:] + suffix)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b'fixture')
    return path


def old_availability(root, item):
    moments = object.__new__(Moments)
    return [{variant: bool(moments.candidates(root, item['id'], media, variant))
             for variant in ('preview', 'source')} for media in item['_media']]


@pytest.mark.parametrize('layout', ['cache/2026-09/Sns', 'cache/Sns', 'Sns'])
@pytest.mark.parametrize('kind,variant,folder,suffix,preview,source', [
    (2, 1, 'Img', '', True, True),
    (2, 2, 'Img', '', True, True),
    (2, 3, 'Img', '', True, True),
    (2, 6, 'Img', '', True, True),
    (2, 4, 'Img', '', False, False),
    (2, 6, 'Img', '.jpg', False, False),
    (6, 1, 'Img', '', True, False),
    (6, 2, 'Img', '', True, False),
    (6, 6, 'Img', '', True, False),
    (6, 3, 'Img', '', False, False),
    (6, 3, 'Video', '.jpg', True, False),
    (6, 3, 'Video', '.jpeg', True, False),
    (6, 3, 'Video', '.png', True, False),
    (6, 3, 'Video', '.webp', True, False),
    (6, 3, 'Video', '', True, True),
    (6, 3, 'Video', '.mp4', False, True),
    (6, 3, 'Video', '.tmp', False, True),
    (6, 3, 'Video', '.m4v', False, True),
    (6, 3, 'Video', '.mov', False, True),
    (6, 3, 'Video', '.avi', False, False),
])
def test_exact_variant_and_layout_parity(tmp_path, layout, kind, variant, folder, suffix, preview, source):
    root = tmp_path / 'verified-account'
    item = post(kind=kind)
    cache_file(root, item, variant, folder, suffix, layout)
    actual = index.scan_media([item], root)['1']
    assert actual == [{'preview': preview, 'source': source}]
    assert actual == old_availability(root, item)


def test_refresh_detects_new_month_files_and_removal(tmp_path):
    root = tmp_path / 'verified-account'
    item = post()
    missing = {'1': [{'preview': False, 'source': False}]}
    assert index.scan_media([item], root) == missing
    path = cache_file(root, item, 6, layout='cache/2026-10/Sns')
    assert index.scan_media([item], root) == {'1': [{'preview': True, 'source': True}]}
    path.unlink()
    assert index.scan_media([item], root) == missing


def test_does_not_read_foreign_accounts_or_recursive_fallback(tmp_path):
    root = tmp_path / 'verified-account'
    item = post()
    cache_file(tmp_path / 'other-account', item, 6)
    cache_file(root, item, 6, layout='backup/cache/2026-09/Sns')
    cache_file(root, item, 6, layout='cache/2026-09/nested/Sns')
    assert index.scan_media([item], root) == {'1': [{'preview': False, 'source': False}]}


def symlink_or_skip(link, target, is_directory=False):
    try:
        link.symlink_to(target, target_is_directory=is_directory)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f'Symlinks unavailable: {exc}')


@pytest.mark.parametrize('level', ['cache', 'month', 'media', 'bucket', 'file'])
def test_rejects_symlink_escape(tmp_path, level):
    root = tmp_path / 'verified-account'
    foreign = tmp_path / 'other-account'
    item = post()
    source = cache_file(foreign, item, 6)
    if level == 'cache':
        link, target = root / 'cache', foreign / 'cache'
    elif level == 'month':
        link, target = root / 'cache/2026-09', foreign / 'cache/2026-09'
    elif level == 'media':
        link, target = root / 'cache/2026-09/Sns/Img', source.parent.parent
    elif level == 'bucket':
        link, target = root / 'cache/2026-09/Sns/Img' / source.parent.name, source.parent
    else:
        link, target = root / 'cache/2026-09/Sns/Img' / source.parent.name / source.name, source
    link.parent.mkdir(parents=True, exist_ok=True)
    symlink_or_skip(link, target, level != 'file')
    assert index.scan_media([item], root) == {'1': [{'preview': False, 'source': False}]}


def test_internal_month_link_preserves_account_bound_parity(tmp_path):
    root = tmp_path / 'verified-account'
    item = post()
    cache_file(root, item, 6, layout='relocated-month/Sns')
    link = root / 'cache/2026-09'
    link.parent.mkdir()
    symlink_or_skip(link, root / 'relocated-month', True)
    result = index.scan_media([item], root)['1']
    assert result == [{'preview': True, 'source': True}]
    assert result == old_availability(root, item)


def test_file_symlinks_remain_unavailable_even_inside_account(tmp_path):
    root = tmp_path / 'verified-account'
    item = post()
    source = cache_file(root, item, 6)
    target = source.with_name('different-file')
    source.rename(target)
    symlink_or_skip(source, target)
    result = index.scan_media([item], root)['1']
    assert result == [{'preview': False, 'source': False}]
    assert result == old_availability(root, item)


def test_enumerates_roots_once_and_only_probes_exact_candidates(tmp_path, monkeypatch):
    root = tmp_path / 'verified-account'
    items = [post(str(ident), str(ident + 100)) for ident in range(44)]
    for item in items:
        cache_file(root, item, 6)
    scans, probes = [], []
    original_scan, original_probe = os.scandir, index._probe

    def scandir(path):
        scans.append(Path(path))
        return original_scan(path)

    def probe(*args):
        probes.append(args)
        return original_probe(*args)

    monkeypatch.setattr(index.os, 'scandir', scandir)
    monkeypatch.setattr(index, '_probe', probe)
    result = index.scan_media(items, root)
    assert scans == [root.resolve() / 'cache']
    assert len(probes) == len(items)
    assert all(value == [{'preview': True, 'source': True}] for value in result.values())
    assert all(len(key) == 32 and suffix == '' for _, key, suffix, _ in probes)


def test_missing_candidate_budget_and_batch_deduplication(tmp_path, monkeypatch):
    root = tmp_path / 'verified-account'
    (root / 'cache/2026-09/Sns/Img').mkdir(parents=True)
    (root / 'cache/2026-09/Sns/Video').mkdir()
    item = post(kind=6)
    item['_media'] *= 2
    probes = []
    original_probe = index._probe

    def probe(*args):
        probes.append(args)
        return original_probe(*args)

    monkeypatch.setattr(index, '_probe', probe)
    assert index.scan_media([item], root) == {'1': [{'preview': False, 'source': False}] * 2}
    # Five poster, three image, five video paths; extensionless video is shared.
    assert len(probes) == 12


def test_empty_media_does_not_enumerate_roots(tmp_path, monkeypatch):
    def forbidden(_):
        raise AssertionError('No directory enumeration needed')

    monkeypatch.setattr(index, '_media_roots', forbidden)
    assert index.scan_media([], tmp_path) == {}
    assert index.scan_media([{'id': '1', '_media': []}], tmp_path) == {'1': []}
