"""Independent, account-verified favorites snapshots; never open source SQLite writable."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

from .active_roster import ActiveWechatRoster, ActiveRosterError


def prepare_favorites(reader, account):
    before = reader.verify(account, force=True)
    root = before['source'].parents[2].resolve()
    source = root / 'db_storage/favorite/favorite.db'
    if not source.is_file() or not source.resolve().is_relative_to(root):
        raise ActiveRosterError('当前账号本地收藏库尚未就绪')
    cache = reader.cache_root.resolve() / 'favorite_sources' / hashlib.sha256(account.encode()).hexdigest()[:24]
    if cache.is_relative_to(root):
        raise ActiveRosterError('收藏缓存不能位于微信源目录')
    cache.mkdir(parents=True, exist_ok=True)
    for attempt in range(3):
        fingerprint = reader.loader._source_fingerprint(source)
        stamp = hashlib.sha256(json.dumps(fingerprint, sort_keys=True).encode()).hexdigest()
        output = cache / (stamp + '.sqlite')
        if not output.is_file():
            with tempfile.TemporaryDirectory(prefix='snapshot-', dir=cache) as temporary:
                target = Path(temporary) / 'favorite.sqlite'
                if reader.loader._is_plain_sqlite(source):
                    copied = Path(temporary) / 'source.sqlite'
                    shutil.copyfile(source, copied)
                    wal = Path(str(source) + '-wal')
                    if wal.is_file():
                        shutil.copyfile(wal, Path(str(copied) + '-wal'))
                    reader.loader._backup_plain_database(copied, target)
                else:
                    tool = reader.loader._load_decrypt_tool()
                    manifest = tool.load_manifest(reader.key_manifest)
                    result = tool.decrypt_database(source, target, manifest, force=False, ignore_wal=True)
                    reader.loader._validate_wal_status(result['wal'])
                    if result['wal'].get('commit_frames'):
                        reader.loader._apply_encrypted_wal(source, target, manifest, result['wal'], tool)
                if fingerprint != reader.loader._source_fingerprint(source):
                    if attempt == 2:
                        raise ActiveRosterError('收藏库读取期间更新，请稍后重试')
                    continue
                db = sqlite3.connect(target.as_uri() + '?mode=ro&immutable=1', uri=True)
                try:
                    if db.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                        raise ActiveRosterError('收藏快照完整性校验失败')
                finally:
                    db.close()
                os.replace(target, output)
        if fingerprint != reader.loader._source_fingerprint(source):
            continue
        if reader.verify(account, force=True) != before:
            raise ActiveRosterError('读取收藏时微信账号或进程已切换')
        return {'account': account, 'root': str(root), 'database': str(output),
                'fingerprint': stamp, 'verified_at': time.time(), 'source': 'active_favorite_db'}
    raise ActiveRosterError('收藏快照暂未取得一致版本')


async def prepare_worker(reader, account):
    process = await asyncio.create_subprocess_exec(
        sys.executable, '-m', 'backend.app.favorite_source', account, reader.bridge_url,
        str(reader.repository_root), str(reader.key_manifest.resolve()), str(reader.cache_root.resolve()),
        cwd=str(Path(__file__).resolve().parents[2]), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        creationflags=0x08000000 if sys.platform == 'win32' else 0)
    try:
        stdout, _ = await asyncio.wait_for(process.communicate(), timeout=45)
    except BaseException:
        if process.returncode is None:
            process.kill()
        await process.wait()
        raise
    if process.returncode or len(stdout) > 16384:
        raise ActiveRosterError('当前账号收藏快照暂未通过校验，请刷新重试')
    return json.loads(stdout)


if __name__ == '__main__':
    try:
        account, bridge, repository, manifest, cache = sys.argv[1:]
        reader = ActiveWechatRoster(bridge, Path(repository), Path(manifest), Path(cache))
        sys.stdout.buffer.write(json.dumps(prepare_favorites(reader, account), ensure_ascii=False).encode('utf-8'))
    except Exception:
        sys.stderr.write('Current-account favorites snapshot verification failed.\n')
        sys.exit(1)
