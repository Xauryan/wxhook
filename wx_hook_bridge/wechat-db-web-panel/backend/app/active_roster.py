from __future__ import annotations

import json
import re
import asyncio
import sqlite3
import tempfile
import time
import sys
from pathlib import Path
from urllib.parse import urlsplit

import psutil

from .sns_sync import SnsSnapshotSync
from .contact_data import read_directory
from .integration_data import read_automation_directory


class ActiveRosterError(RuntimeError):
    pass


def contact_relation(username, kind, deleted, in_room, flag, account):
    if deleted or username == account:
        return None
    if username.endswith('@chatroom'):
        return 'group' if kind in {1, 2} and in_room == 1 else None
    if kind == 1 and flag & 1:
        return 'friend'
    return 'system' if username == 'filehelper' else None


class ActiveWechatRoster:
    """Resolve the bridge-owning process and read only its active address book."""

    def __init__(self, bridge_url: str, repository_root: Path, key_manifest: Path, cache_root: Path):
        self.port = urlsplit(bridge_url).port
        self.bridge_url = bridge_url
        self.repository_root = repository_root.resolve()
        self.key_manifest = key_manifest
        self.cache_root = cache_root
        self.loader = SnsSnapshotSync(Path('unused'), repository_root, cache_root / 'unused')
        self._binding = None
        self._binding_at = 0.0

    async def _worker(self, action: str, account: str) -> dict:
        # Windows handle enumeration can hold the GIL; keep it out of the API process.
        process = await asyncio.create_subprocess_exec(
            sys.executable, '-m', 'backend.app.active_roster', action, account,
            self.bridge_url, str(self.repository_root), str(self.key_manifest.resolve()), str(self.cache_root.resolve()),
            cwd=str(Path(__file__).resolve().parents[2]),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            creationflags=0x08000000 if sys.platform == 'win32' else 0,
        )
        try:
            stdout, _ = await asyncio.wait_for(process.communicate(), timeout=30)
        except BaseException:
            if process.returncode is None:
                process.kill()
            await process.wait()
            raise
        if process.returncode or len(stdout) > 16 * 1024 * 1024:
            raise ActiveRosterError('当前微信账号或通讯录验证失败')
        return json.loads(stdout)

    async def read_async(self, account: str) -> dict:
        return await self._worker('read', account)

    async def verify_async(self, account: str) -> dict:
        return await self._worker('verify', account)

    def verify(self, account: str, force: bool = False) -> dict:
        if not force and self._binding and self._binding['account'] == account and time.monotonic() - self._binding_at < 10:
            try:
                if psutil.Process(self._binding['pid']).create_time() == self._binding['created_at']:
                    return self._binding
            except psutil.Error:
                pass
        owners = {c.pid for c in psutil.net_connections(kind='tcp') if c.status == psutil.CONN_LISTEN and c.laddr.port == self.port and c.laddr.ip in {'127.0.0.1','::1'} and c.pid}
        if len(owners) != 1:
            raise ActiveRosterError('无法唯一确认微信桥接进程')
        process = psutil.Process(owners.pop())
        if not re.fullmatch(r'(?:wechat\d*|weixin\d*|weixi\d+)\.exe', process.name().casefold()):
            raise ActiveRosterError('桥接端口不属于微信进程')
        paths = {Path(f.path) for f in process.open_files() if f.path.replace('\\','/').endswith('/db_storage/contact/contact.db')}
        if len(paths) != 1:
            raise ActiveRosterError('当前微信通讯录句柄不唯一，拒绝混合多个账号')
        source = paths.pop()
        folder = source.parents[2].name
        if folder != account and not folder.startswith(account + '_'):
            raise ActiveRosterError('桥接账号与微信实际打开的通讯录不一致')
        binding = {'account':account,'pid':process.pid,'created_at':process.create_time(),'source':source}
        self._binding = binding
        self._binding_at = time.monotonic()
        return binding

    def read(self, account: str) -> dict:
        before = self.verify(account, force=True)
        source = before['source']
        tool = self.loader._load_decrypt_tool()
        manifest = tool.load_manifest(self.key_manifest)
        fingerprint = self.loader._source_fingerprint(source)
        self.cache_root.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='active-roster-', dir=self.cache_root) as directory:
            output = Path(directory) / 'contact.sqlite'
            result = tool.decrypt_database(source,output,manifest,force=False,ignore_wal=True)
            wal = result['wal']
            self.loader._validate_wal_status(wal)
            if wal.get('commit_frames'):
                self.loader._apply_encrypted_wal(source,output,manifest,wal,tool)
            if fingerprint != self.loader._source_fingerprint(source):
                raise ActiveRosterError('通讯录正在更新，请稍后重试')
            db = sqlite3.connect(output.as_uri() + '?mode=ro',uri=True)
            try:
                db.execute('PRAGMA query_only=ON')
                if db.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                    raise ActiveRosterError('当前通讯录完整性校验失败')
                rows = db.execute('SELECT username,remark,nick_name,local_type,delete_flag,is_in_chat_room,flag FROM contact').fetchall()
                directory = read_directory(db, account)
                automation = read_automation_directory(db, account)
            finally:
                db.close()
        after = self.verify(account, force=True)
        if before != after:
            raise ActiveRosterError('读取期间微信账号或进程已切换')
        contacts, names = [], []
        for username,remark,nickname,kind,deleted,in_room,flag in rows:
            name = remark or nickname or username
            names.append({'username':username,'name':name})
            relation = contact_relation(username,kind,deleted,in_room,flag,account)
            if relation:
                contacts.append({'username':username,'name':'文件传输助手' if username == 'filehelper' else name,'relation':relation})
        return {'account':account,'contacts':contacts,'names':names,'directory':directory,'automation':automation,'source':'active_contact_db','verified_at':time.time()}


if __name__ == '__main__':
    try:
        action, account, bridge, repository, manifest, cache = sys.argv[1:]
        reader = ActiveWechatRoster(bridge,Path(repository),Path(manifest),Path(cache))
        result = reader.read(account) if action == 'read' else reader.verify(account,force=True)
        sys.stdout.buffer.write(json.dumps(result,ensure_ascii=False,default=str).encode('utf-8'))
    except Exception:
        sys.stderr.write('Active WeChat address-book verification failed.\n')
        sys.exit(1)
