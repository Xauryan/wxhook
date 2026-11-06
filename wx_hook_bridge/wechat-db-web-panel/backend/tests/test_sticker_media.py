import hashlib
import io
import json
import struct
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad
from PIL import Image

from backend.app import sticker_key
from backend.app.chat_media import ChatMedia, MediaFailure
from backend.app.chat_messages import ChatMessages


KEY = b'fixture-only-key'
OTHER_KEY = b'other-fixturekey'


def gif_bytes():
    output = io.BytesIO()
    Image.new('RGB', (4, 4), 'white').save(output, format='GIF', save_all=True,
        append_images=[Image.new('RGB', (4, 4), 'black')], duration=[80, 120], loop=0)
    return output.getvalue()


def setup_media(tmp_path, payload, *, expected=None, aes_key=None, alternate=False):
    store = ChatMessages(tmp_path / 'ledger.sqlite')
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'sticker').write_bytes(payload)
    expected = expected or hashlib.md5(gif_bytes()).hexdigest()
    spec = {'kind': 'sticker', 'state': 'local', 'path': 'sticker',
            'md5': '0' * 32 if alternate else expected}
    if aes_key is not None:
        spec['aes_key'] = aes_key
    message = {'session_username': 'peer', 'sender_username': 'peer', 'server_id': '1',
               'local_id': 1, 'create_time': 1, 'category': 'sticker',
               'media_hints': [spec['md5'], expected], '_media': [spec]}
    with store.db() as db:
        db.execute('INSERT INTO chat_history_state VALUES (?,?)', ('self', json.dumps({'root': str(source)})))
        ident, _ = store.upsert(db, 'self', message)
    media = ChatMedia(store, tmp_path / 'cache', 'http://127.0.0.1:1')
    calls = []

    def local_key(account, bridge):
        calls.append((account, bridge))
        return KEY

    media.sticker_key_reader = local_key
    return media, ident, calls, source / 'sticker'


@pytest.mark.parametrize('aes_key', [None, '', 'invalid', OTHER_KEY.hex()])
def test_account_cache_key_and_alternate_hash_restore_sticker(tmp_path, aes_key):
    plain = gif_bytes()
    encrypted = AES.new(KEY, AES.MODE_CBC, iv=KEY).encrypt(pad(plain, 16))
    media, ident, calls, source = setup_media(tmp_path, encrypted, aes_key=aes_key, alternate=True)
    result = media.describe('self', ident)
    assert result['items'][0]['state'] == 'available'
    assert result['items'][0]['mime'] == 'image/gif'
    path, _ = media.resolve('self', ident, 0)
    assert path.read_bytes() == plain
    with Image.open(path) as image:
        assert image.n_frames == 2
        assert image.info['duration'] == 80
    assert calls == [('self', 'http://127.0.0.1:1')]
    assert source.read_bytes() == encrypted
    assert KEY.hex() not in json.dumps(result)
    assert 'aes_key' not in json.dumps(result)
    assert all(KEY not in file.read_bytes() for file in media.cache_root.rglob('*') if file.is_file())


@pytest.mark.parametrize('mode,iv', [(AES.MODE_CBC, KEY), (AES.MODE_CBC, bytes(16)), (AES.MODE_ECB, None)])
def test_message_key_formats_do_not_read_process_memory(tmp_path, mode, iv):
    encrypted = AES.new(KEY, mode, **({'iv': iv} if iv else {})).encrypt(pad(gif_bytes(), 16))
    media, ident, calls, _ = setup_media(tmp_path, encrypted, aes_key=KEY.hex())
    assert media.describe('self', ident)['items'][0]['state'] == 'available'
    assert not calls


def test_plain_sticker_needs_no_key(tmp_path):
    media, ident, calls, _ = setup_media(tmp_path, gif_bytes())
    assert media.describe('self', ident)['items'][0]['state'] == 'available'
    assert not calls


def test_aligned_unpadded_message_payload_is_not_shortened(tmp_path):
    plain = b'fixture payload' + b'\x01'
    media, _, calls, _ = setup_media(tmp_path, b'')
    encrypted = AES.new(KEY, AES.MODE_ECB).encrypt(plain)
    assert media._sticker_bytes(encrypted, {'aes_key': KEY.hex(), 'md5': hashlib.md5(plain).hexdigest()}, 'self') == plain
    assert not calls


@pytest.mark.parametrize('corrupted', [False, True])
def test_wrong_hash_or_corrupted_ciphertext_never_becomes_available(tmp_path, corrupted):
    encrypted = AES.new(KEY, AES.MODE_CBC, iv=KEY).encrypt(pad(gif_bytes(), 16))
    if corrupted:
        encrypted = bytes([encrypted[0] ^ 1]) + encrypted[1:]
    media, ident, _, _ = setup_media(tmp_path, encrypted, expected=None if corrupted else 'f' * 32)
    item = media.describe('self', ident)['items'][0]
    assert item['state'] == 'decrypt_failed' and item['url'] is None
    assert not list(media.cache_root.rglob('*.decoded'))


def test_no_expected_hash_does_not_read_account_key(tmp_path):
    media, _, calls, _ = setup_media(tmp_path, b'')
    with pytest.raises(MediaFailure) as error:
        media._sticker_bytes(b'unknown'.ljust(16, b'!'), {}, 'self')
    assert error.value.state == 'decrypt_failed' and not calls


def test_missing_local_key_is_explicit_and_not_cached_as_success(tmp_path):
    media, ident, _, _ = setup_media(tmp_path, b'ciphertext'.ljust(32, b'!'))

    def unavailable(*args):
        raise sticker_key.StickerKeyUnavailable()

    media.sticker_key_reader = unavailable
    item = media.describe('self', ident)['items'][0]
    assert item['state'] == 'local_key_unavailable' and item['url'] is None
    assert not list(media.cache_root.rglob('*.decoded'))


def memory_fixture(monkeypatch):
    base, manager, user, key_address = 0x180000000, 0x100000, 0x200000, 0x400000
    signature = b'known-test-accessor'
    monkeypatch.setattr(sticker_key, 'ACCESSORS', ((0x100, len(signature), hashlib.sha256(signature).hexdigest()),))
    masked = bytes(value ^ sticker_key.KEY_MASK[index] for index, value in enumerate(KEY))
    memory = {
        base + 0x100: signature,
        base + sticker_key.LOGIN_MANAGER_RVA: struct.pack('<Q', manager),
        manager + 0x68: struct.pack('<Q', user),
        user + 0x48: b'self'.ljust(16, b'\0') + struct.pack('<QQ', 4, 15),
        user + 0x298: struct.pack('<QQQQ', key_address, 0, 16, 31),
        key_address: masked,
    }
    reads = []

    def read(address, size):
        reads.append((address, size))
        return memory.get(address, b'')[:size]

    return base, manager, user, memory, read, reads


def test_local_key_reader_is_account_scoped_and_bounded(monkeypatch):
    base, _, _, _, read, reads = memory_fixture(monkeypatch)
    assert sticker_key.account_key(read, base, 'self') == KEY
    assert len(reads) < 20 and sum(size for _, size in reads) < 512
    with pytest.raises(sticker_key.StickerKeyUnavailable):
        sticker_key.account_key(read, base, 'other')


def test_unknown_native_build_stops_before_following_pointers(monkeypatch):
    base, _, _, memory, read, reads = memory_fixture(monkeypatch)
    memory[base + 0x100] = b'unknown-build'
    with pytest.raises(sticker_key.StickerKeyUnavailable):
        sticker_key.account_key(read, base, 'self')
    assert len(reads) == 1


@pytest.mark.parametrize('change', ['account', 'manager', 'key'])
def test_mid_read_identity_or_key_change_is_rejected(monkeypatch, change):
    base, manager, user, memory, read, _ = memory_fixture(monkeypatch)

    def switched(address, size):
        result = read(address, size)
        if address == 0x400000:
            if change == 'account':
                memory[user + 0x48] = b'else'.ljust(16, b'\0') + struct.pack('<QQ', 4, 15)
            elif change == 'manager':
                memory[base + sticker_key.LOGIN_MANAGER_RVA] = struct.pack('<Q', manager + 4096)
            else:
                memory[0x400000] = b'changed-key!!!!!'
        return result

    with pytest.raises(sticker_key.StickerKeyUnavailable):
        sticker_key.account_key(switched, base, 'self')


@pytest.mark.parametrize('length,capacity,pointer', [(129, 255, 0x400000), (16, 8, 0x400000),
    (17, 31, 0x400000), (16, 31, 0), (16, 31, 0x800000000000)])
def test_invalid_string_lengths_and_pointers_are_rejected(monkeypatch, length, capacity, pointer):
    base, _, user, memory, read, _ = memory_fixture(monkeypatch)
    memory[user + 0x298] = struct.pack('<QQQQ', pointer, 0, length, capacity)
    with pytest.raises(sticker_key.StickerKeyUnavailable):
        sticker_key.account_key(read, base, 'self')


def test_process_replacement_closes_handle_and_returns_no_key(monkeypatch):
    base, _, _, _, read, _ = memory_fixture(monkeypatch)
    bindings = iter([(12, 100), (12, 200)])
    closed = []
    monkeypatch.setattr(sticker_key, 'os', SimpleNamespace(name='nt'))
    monkeypatch.setattr(sticker_key, 'bridge_process', lambda url: next(bindings))

    @contextmanager
    def memory(pid):
        try:
            yield read, base
        finally:
            closed.append(pid)

    monkeypatch.setattr(sticker_key, 'process_memory', memory)
    with pytest.raises(sticker_key.StickerKeyUnavailable):
        sticker_key.read_sticker_key('self', 'http://127.0.0.1:19088')
    assert closed == [12]


@pytest.mark.parametrize('owners,name', [([], 'Weixin.exe'), ([1, 2], 'Weixin.exe'), ([1], 'other.exe')])
def test_bridge_owner_must_be_unique_and_weixin(monkeypatch, owners, name):
    connections = [SimpleNamespace(pid=pid, status=sticker_key.psutil.CONN_LISTEN,
        laddr=SimpleNamespace(ip='127.0.0.1', port=19088)) for pid in owners]
    monkeypatch.setattr(sticker_key.psutil, 'net_connections', lambda **kw: connections)
    monkeypatch.setattr(sticker_key.psutil, 'Process', lambda pid: SimpleNamespace(name=lambda: name))
    with pytest.raises(sticker_key.StickerKeyUnavailable):
        sticker_key.bridge_process('http://127.0.0.1:19088')
