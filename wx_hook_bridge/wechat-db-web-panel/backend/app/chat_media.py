"""Lazy, local-only media resolution. Clients never supply a filesystem path."""
from __future__ import annotations

from collections import OrderedDict

import hashlib
import codecs
import json
import os
import sqlite3
import shutil
import subprocess
import threading
import time
import wave
from pathlib import Path
from urllib.parse import urlencode

import httpx
from Crypto.Cipher import AES
from Crypto.Util.Padding import unpad
from fastapi import HTTPException

from .chat_media_index import MessageResources
from .chat_history import readonly
from .sns_media import SIG_V1, SIG_V2, MediaDecodeError, decode_media_bytes, sniff_media
from .sticker_key import StickerKeyUnavailable, read_sticker_key


STATES = {
    'missing': '本地资源已缺失',
    'not_downloaded': '暂未找到可读取的本地资源',
    'decrypt_failed': '本地资源解密失败',
    'local_key_unavailable': '当前账号的本地表情缓存密钥不可用',
    'unsupported': '当前格式无法在浏览器播放或预览',
    'conversion_failed': '本地格式转换失败',
    'unavailable': '本地资源暂不可读取',
    'local': '本地文件存在，打开时验证格式',
    'available': '可用',
    'decode_limited': '本次本地解码已达到保护上限，未继续调用微信内存解码器',
    'integrity_failed': '本地附件内容与收藏记录不匹配，未展示该文件',
}


class MediaFailure(Exception):
    def __init__(self, state):
        self.state = state


def safe_path(root: Path, relative: str) -> Path:
    # Resolve junctions/symlinks too, and reject NTFS alternate data streams.
    if not relative or '\x00' in relative or ':' in relative or relative.startswith(('/', '\\')):
        raise MediaFailure('unavailable')
    root = root.resolve()
    candidate = root / relative
    try:
        resolved = candidate.resolve()
        if not resolved.is_relative_to(root) or any(part == '..' for part in Path(relative.replace('\\', '/')).parts):
            raise MediaFailure('unavailable')
        if not resolved.is_file():
            raise MediaFailure('missing')
    except OSError:
        raise MediaFailure('unavailable') from None
    return resolved


class ChatMedia:
    def __init__(self, store, cache_root, bridge_url):
        self.store, self.cache_root, self.bridge_url = store, cache_root / 'chat_media', bridge_url
        self.cache_root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self.native_decodes = 0
        self.failures = {}
        self.sticker_key_reader = read_sticker_key
        self.index_lock = threading.Lock()
        self.local_specs = OrderedDict()

    def specs(self, account, ident, refresh=False, record=None):
        with self.store.db() as db:
            row = db.execute('SELECT data FROM chat_messages WHERE account=? AND id=?', (account, ident)).fetchone()
        if not row:
            raise HTTPException(404, '当前账号没有这条消息')
        message = json.loads(row[0])
        if record is not None:
            import re
            if not re.fullmatch(r'\d{1,2}(?:\.\d{1,2}){0,2}', record):
                raise HTTPException(422, '记录位置无效')
            values = message.get('records') or []
            entry = None
            for part in record.split('.'):
                index = int(part)
                if index >= len(values):
                    raise HTTPException(404, '记录不存在')
                entry = values[index]
                values = entry.get('children') or []
            if not entry or entry.get('kind') not in {'image','sticker','voice','video','file'}:
                raise HTTPException(404, '此记录没有本地附件')
            message = {**message, **entry, 'category':entry['kind'], 'server_id':'0', '_media':[]}
        if record is not None and message['category'] == 'voice':
            # Forwarded records have no verified original chat identity. Never
            # match an unrelated VoiceInfo row whose pending server id is zero.
            return [{'kind': 'voice', 'state': 'not_downloaded'}]
        specs = message.get('_media') or []
        # Cache filenames use the original MD5 even when their bytes are the
        # alternate wxgf representation. This also upgrades existing indices.
        specs = [dict(spec, md5s=message.get('media_hints') or [])
                 if spec.get('kind') == 'sticker' else spec for spec in specs]
        if not specs and message.get('category') in {'image', 'sticker', 'voice', 'video', 'file'}:
            specs = [{'kind': message['category'], 'state': 'not_downloaded', 'name': message.get('file_name'), 'size': message.get('file_size')}]
        if message.get('category') not in {'image', 'sticker', 'voice', 'video', 'file'}:
            return specs
        # Recent rows skip the archive media index; look up only this message.
        cache_key = (account, ident, record, hashlib.sha256(row[0].encode()).hexdigest())
        with self.index_lock:
            cached = self.local_specs.get(cache_key)
            if cached and not refresh and time.monotonic() - cached[0] < 15:
                self.local_specs.move_to_end(cache_key)
                return cached[1]
            if not refresh and any(spec['state'] == 'local' for spec in specs):
                return specs
            history = self.store.state(account)
            if history.get('root') and history.get('databases'):
                try:
                    found = MessageResources(Path(history['root']), history['databases'], message).for_message(message)
                    if any(spec['state'] == 'local' for spec in found):
                        specs = [dict(spec, md5s=message.get('media_hints') or [])
                                 if spec.get('kind') == 'sticker' else spec for spec in found]
                except (OSError, sqlite3.DatabaseError):
                    pass
            self.local_specs[cache_key] = (time.monotonic(), specs)
            self.local_specs.move_to_end(cache_key)
            while len(self.local_specs) > 256:
                self.local_specs.popitem(last=False)
        return specs

    @staticmethod
    def spec_key(spec):
        return hashlib.sha256(json.dumps(spec,sort_keys=True).encode()).hexdigest()[:24]

    def asset_url(self, account, ident, index, spec, record=None):
        return f'/api/chat/media/{ident}/{index}?' + urlencode({'account': account, 'asset':self.spec_key(spec), **({'record':record} if record is not None else {})})

    def describe(self, account, ident, refresh=False, record=None):
        started = time.perf_counter()
        specs = self.specs(account, ident, refresh=refresh, record=record)
        playable_kind = next((s['kind'] for s in specs if s['kind'] in {'voice', 'video', 'file'}), None)
        order = sorted(range(len(specs)), key=lambda i: (
            specs[i]['kind'] != playable_kind if playable_kind else False,
            {'thumbnail': 0, 'preview': 1, 'original': 2}.get(specs[i].get('variant'), 3)))
        preferred = order[0] if order else 0
        resolved = {}
        for index in order:
            spec = specs[index]
            if spec['state'] != 'local' or playable_kind and spec['kind'] != playable_kind:
                continue
            try:
                path, mime = self.resolve(account, ident, index, record=record)
                resolved[index] = ('available', mime, path.stat().st_size)
                preferred = index
                break
            except MediaFailure as exc:
                resolved[index] = (exc.state, None, spec.get('size'))
        items = []
        for index, spec in enumerate(specs):
            state = spec['state']
            mime = None
            size = spec.get('size')
            if index in resolved:
                state, mime, size = resolved[index]
            url = self.asset_url(account, ident, index, spec, record=record) if state in {'available', 'local'} else None
            items.append({'index': index, 'kind': spec['kind'], 'variant': spec.get('variant', 'original'),
                          'name': spec.get('name'), 'size': size, 'state': state,
                          'detail': STATES[state], 'url': url, 'mime': mime})
        return {'items': items, 'preferred': preferred, 'elapsed_ms': round((time.perf_counter() - started) * 1000, 2)}

    def resolve(self, account, ident, index, expected_key=None, record=None):
        specs = self.specs(account, ident, record=record)
        if index < 0 or index >= len(specs):
            raise HTTPException(404, '资源不存在')
        spec = specs[index]
        if expected_key is not None and self.spec_key(spec) != expected_key:
            raise HTTPException(409, '资源索引已更新，请重新检查本地资源')
        if spec['state'] != 'local':
            raise MediaFailure(spec['state'])
        history = self.store.state(account)
        if not history.get('root'):
            raise MediaFailure('unavailable')
        root = Path(history['root'])
        if 'path' in spec:
            source = safe_path(root, spec['path'])
        else:
            source = Path(spec['database']).resolve()
            # Voice blobs can only come from the verified snapshot manifest.
            if str(source) not in history['databases'].values() or not source.is_relative_to(self.cache_root.parent / 'chat_sources'):
                raise MediaFailure('unavailable')
        return self._resolve_source(account, source, spec)

    def _resolve_source(self, account, source, spec):
        """Internal callers must verify account ownership and path containment first."""
        try:
            stat = source.stat()
        except FileNotFoundError:
            raise MediaFailure('missing') from None
        except OSError:
            raise MediaFailure('unavailable') from None
        key = hashlib.sha256(json.dumps([account, spec, stat.st_size, stat.st_mtime_ns], sort_keys=True).encode()).hexdigest()
        destination = self.cache_root / hashlib.sha256(account.encode()).hexdigest()[:24]
        destination.mkdir(exist_ok=True)
        output = destination / (key + '.decoded')
        meta = destination / (key + '.json')
        if spec['kind'] == 'file':
            return source, self.file_mime(source, spec.get('name'))
        with self.lock:
            if output.is_file() and meta.is_file():
                return output, json.loads(meta.read_text())['mime']
            failure = self.failures.get(key)
            if failure and time.monotonic() - failure[1] < 60:
                raise MediaFailure(failure[0])
            try:
                path, mime = self._convert(source, output, spec, account)
                if path == output:
                    meta.write_text(json.dumps({'mime': mime}), encoding='utf-8')
                return path, mime
            except MediaFailure as exc:
                self.failures[key] = (exc.state, time.monotonic())
                raise
            except (OSError, ValueError, sqlite3.DatabaseError) as exc:
                self.failures[key] = ('conversion_failed', time.monotonic())
                raise MediaFailure('conversion_failed') from exc

    @staticmethod
    def file_mime(source, file_name=None):
        with source.open('rb') as stream:
            header = stream.read(8192)
        mime, _ = sniff_media(header)
        if mime in {'image/jpeg', 'image/png', 'image/gif', 'image/webp'}:
            return mime
        if header.startswith(b'%PDF-'):
            return 'application/pdf'
        if Path(file_name or source.name).suffix.lower() in {'.txt', '.md', '.csv', '.json', '.log', '.xml', '.yaml', '.yml'}:
            try:
                codecs.getincrementaldecoder('utf-8-sig')().decode(header, final=False)
                if b'\x00' not in header:
                    return 'text/plain'
            except UnicodeDecodeError:
                pass
        return 'application/octet-stream'

    def _convert(self, source, output, spec, account):
        kind = spec['kind']
        if kind == 'video':
            with source.open('rb') as stream:
                header = stream.read(16)
            # Do not let an attachment masquerading as video become a playlist
            # that asks FFmpeg to open other local paths.
            if header[4:8] != b'ftyp' and not header.startswith(b'\x1a\x45\xdf\xa3'):
                raise MediaFailure('unsupported')
            payload = b''
        elif 'database' in spec:
            with readonly(source) as db:
                row = db.execute('SELECT voice_data FROM VoiceInfo WHERE rowid=?', (spec['rowid'],)).fetchone()
            if not row or not row[0]:
                raise MediaFailure('missing')
            payload = bytes(row[0])
        else:
            if source.stat().st_size > 128 * 1024 * 1024:
                raise MediaFailure('unsupported')
            payload = source.read_bytes()
        if kind in {'image', 'sticker'}:
            if kind == 'sticker':
                payload = self._sticker_bytes(payload, spec, account)
            if payload.startswith(b'wxgf'):
                return self._wxgf(payload, output, kind == 'sticker')
            try:
                decoded = decode_media_bytes(payload, file_name=source.name, require_kind='image')
            except MediaDecodeError:
                if payload[:6] == SIG_V2:
                    self._native_decode(source, output, account)
                    try:
                        plain = output.read_bytes()
                        if plain.startswith(b'wxgf'):
                            return self._wxgf(plain, output, kind == 'sticker')
                        decoded = decode_media_bytes(plain, require_kind='image')
                    except (MediaDecodeError, OSError):
                        raise MediaFailure('unsupported') from None
                elif payload[:6] == SIG_V1:
                    raise MediaFailure('decrypt_failed') from None
                else:
                    decoded = None
                    for header in (b'\xff\xd8\xff', b'\x89PNG\r\n\x1a\n', b'GIF89a'):
                        if not payload:
                            break
                        xor = payload[0] ^ header[0]
                        candidate = bytes(byte ^ xor for byte in payload)
                        if candidate.startswith(header):
                            try:
                                decoded = decode_media_bytes(candidate, require_kind='image')
                                break
                            except MediaDecodeError:
                                pass
                    if decoded is None:
                        raise MediaFailure('unsupported') from None
            if not decoded.browser_supported:
                raise MediaFailure('unsupported')
            output.write_bytes(decoded.payload)
            return output, decoded.mime_type
        if kind == 'voice':
            if b'#!SILK_V3' in payload[:12]:
                try:
                    import pilk
                except ImportError:
                    raise MediaFailure('unsupported') from None
                silk, pcm = output.with_suffix('.silk'), output.with_suffix('.pcm')
                silk.write_bytes(payload)
                try:
                    pilk.decode(str(silk), str(pcm), pcm_rate=24000)
                    with wave.open(str(output), 'wb') as writer:
                        writer.setparams((1, 2, 24000, 0, 'NONE', 'NONE'))
                        writer.writeframes(pcm.read_bytes())
                except Exception:
                    raise MediaFailure('conversion_failed') from None
                finally:
                    silk.unlink(missing_ok=True)
                    pcm.unlink(missing_ok=True)
                return output, 'audio/wav'
            raw = output.with_suffix('.audio')
            if not (payload.startswith((b'#!AMR',b'RIFF',b'OggS',b'fLaC',b'ID3')) or len(payload)>1 and payload[0]==255 and payload[1]&224==224):
                raise MediaFailure('unsupported')
            raw.write_bytes(payload)
            try:
                self._ffmpeg(raw, output, video=False)
            finally:
                raw.unlink(missing_ok=True)
            return output, 'audio/wav'
        if kind == 'video':
            # Probe codecs, not just .mp4: HEVC/unsupported audio is transcoded.
            probe = shutil.which('ffprobe')
            if probe:
                try:
                    result = subprocess.run([probe, '-v', 'error', '-protocol_whitelist', 'file,pipe', '-show_entries', 'stream=codec_name,codec_type', '-of', 'json', str(source)], capture_output=True, timeout=15, creationflags=0x08000000 if os.name == 'nt' else 0)
                    streams = json.loads(result.stdout).get('streams', [])
                    if streams and all(s['codec_name'] in {'h264', 'aac', 'mp3'} for s in streams):
                        return source, 'video/mp4'
                except (OSError, ValueError, subprocess.TimeoutExpired):
                    pass
            self._ffmpeg(source, output, video=True)
            return output, 'video/mp4'
        raise MediaFailure('unsupported')

    def _sticker_bytes(self, payload, spec, account):
        if payload.startswith((b'GIF8', b'\x89PNG', b'\xff\xd8\xff', b'RIFF', b'wxgf', SIG_V1, SIG_V2)):
            return payload
        if not payload or len(payload) % 16:
            return payload
        hashes = {value.lower() for value in [spec.get('md5'), *(spec.get('md5s') or [])]
                  if isinstance(value, str) and len(value) == 32
                  and all(char in '0123456789abcdefABCDEF' for char in value)}
        if not hashes:
            raise MediaFailure('decrypt_failed')

        def verified(plain):
            if hashlib.md5(plain).hexdigest() in hashes:
                return plain
            try:
                plain = unpad(plain, 16)
            except ValueError:
                return None
            return plain if hashlib.md5(plain).hexdigest() in hashes else None

        try:
            key = bytes.fromhex(spec.get('aes_key') or '')
            if len(key) in {16, 24, 32}:
                for mode, args in ((AES.MODE_CBC, {'iv': key[:16]}),
                                   (AES.MODE_CBC, {'iv': bytes(16)}), (AES.MODE_ECB, {})):
                    plain = verified(AES.new(key, mode, **args).decrypt(payload))
                    if plain is not None:
                        return plain
        except (TypeError, ValueError):
            pass
        # The on-disk cache has account-key encryption, independent of the
        # CDN key carried in the message. Never persist or expose this key.
        try:
            key = self.sticker_key_reader(account, self.bridge_url)
        except StickerKeyUnavailable:
            raise MediaFailure('local_key_unavailable') from None
        plain = verified(AES.new(key, AES.MODE_CBC, iv=key[:16]).decrypt(payload))
        if plain is not None:
            return plain
        raise MediaFailure('decrypt_failed')

    @staticmethod
    def _wxgf(payload, output, animated=False):
        executable = shutil.which('ffmpeg')
        start = payload.find(b'\x00\x00\x00\x01\x40', 4)
        if not executable or start < 0:
            raise MediaFailure('unsupported')
        raw = output.with_suffix('.hevc')
        raw.write_bytes(payload[start:])
        args = [executable, '-nostdin','-y','-v','error','-protocol_whitelist','file,pipe','-f','hevc','-i',str(raw)]
        args += ['-t','30','-f','gif'] if animated else ['-frames:v','1','-c:v','png','-f','image2']
        try:
            result = subprocess.run(args + [str(output)], capture_output=True, timeout=30, creationflags=0x08000000 if os.name=='nt' else 0)
            if result.returncode or not output.is_file():
                raise MediaFailure('conversion_failed')
            decoded = decode_media_bytes(output.read_bytes(), require_kind='image')
            return output, decoded.mime_type
        except (OSError, subprocess.TimeoutExpired, MediaDecodeError):
            raise MediaFailure('conversion_failed') from None
        finally:
            raw.unlink(missing_ok=True)

    def _native_decode(self, source, output, account):
        # This endpoint only reads a local image and writes the requested cache
        # file. Native code retains a 2 MiB arena per call: serialize and cap it.
        if self.native_decodes >= 64:
            raise MediaFailure('decode_limited')
        try:
            with httpx.Client(base_url=self.bridge_url, trust_env=False, timeout=15) as client:
                profile = client.post('/api/userInfo', json={}).json()
                if profile.get('data', {}).get('wxid') != account:
                    raise MediaFailure('unavailable')
                self.native_decodes += 1
                result = client.post('/api/decodeImage', json={'filePath': str(source), 'storeDir': str(output)}).json()
                if result.get('code') != 1 or not output.is_file():
                    raise MediaFailure('decrypt_failed')
                if client.post('/api/userInfo', json={}).json().get('data', {}).get('wxid') != account:
                    raise MediaFailure('unavailable')
        except (httpx.HTTPError, ValueError):
            raise MediaFailure('decrypt_failed') from None

    @staticmethod
    def _ffmpeg(source, output, video):
        executable = shutil.which('ffmpeg')
        if not executable:
            raise MediaFailure('unsupported')
        args = [executable, '-nostdin', '-y', '-v', 'error', '-protocol_whitelist', 'file,pipe', '-i', str(source)]
        args += ['-c:v', 'libx264', '-preset', 'fast', '-crf', '23', '-pix_fmt','yuv420p','-threads','2','-c:a', 'aac', '-movflags', '+faststart', '-f', 'mp4'] if video else ['-vn', '-ac', '1', '-ar', '24000', '-f', 'wav']
        try:
            result = subprocess.run(args + [str(output)], capture_output=True, timeout=90, creationflags=0x08000000 if os.name == 'nt' else 0)
        except (OSError, subprocess.TimeoutExpired):
            raise MediaFailure('conversion_failed') from None
        if result.returncode or not output.is_file() or not output.stat().st_size:
            raise MediaFailure('conversion_failed')

    def avatar(self, account, username):
        state = self.store.state(account)
        path = state.get('databases', {}).get('head_image/head_image.db')
        if not path:
            raise HTTPException(404, '当前账号本地头像尚未就绪')
        with readonly(path) as db:
            row = db.execute('SELECT image_buffer FROM head_image WHERE username=?', (username,)).fetchone()
        if not row or not row[0]:
            raise HTTPException(404, '本地没有该头像')
        payload = bytes(row[0])
        mime, _ = sniff_media(payload)
        if mime not in {'image/jpeg', 'image/png', 'image/gif', 'image/webp'}:
            raise HTTPException(415, '头像格式不支持')
        return payload, mime
