import hashlib
import io
import json
import sqlite3
from contextlib import contextmanager

import pytest
from fastapi import HTTPException
from PIL import Image

from backend.app.chat_media import ChatMedia
from backend.app.chat_media_index import MessageResources
from backend.app.chat_messages import ChatMessages


@pytest.fixture
def local_media(tmp_path):
    store = ChatMessages(tmp_path / 'chat.sqlite')
    root = tmp_path / 'account'
    folder = root / 'msg' / 'attach' / hashlib.md5(b'peer').hexdigest() / '2026-10' / 'Img'
    folder.mkdir(parents=True)
    hardlink = tmp_path / 'hardlink.sqlite'
    hint = 'a' * 32
    with sqlite3.connect(hardlink) as db:
        db.execute('CREATE TABLE image_hardlink_info_v4(md5,file_name,file_size,type)')
        db.execute('CREATE INDEX md5_index ON image_hardlink_info_v4(md5)')
        db.execute('INSERT INTO image_hardlink_info_v4 VALUES (?,?,?,?)', (hint, 'cached.dat', 1, 0))
    with store.db() as db:
        db.execute('INSERT INTO chat_history_state VALUES (?,?)', ('self', json.dumps({
            'root': str(root), 'databases': {'hardlink/hardlink.db': str(hardlink)}})))
        ident, _ = store.upsert(db, 'self', {'session_username': 'peer', 'sender_username': 'peer',
            'server_id': '123', 'local_id': 1, 'create_time': 1, 'source': 'history',
            'category': 'image', 'media_hints': [hint]})
    media = ChatMedia(store, tmp_path / 'cache', 'http://127.0.0.1:1')
    return media, store, ident, folder / 'cached.dat', root


def write_image(path):
    output = io.BytesIO()
    Image.new('RGB', (4, 4), 'white').save(output, format='PNG')
    path.write_bytes(output.getvalue())


def test_recent_message_finds_local_attachment_without_full_archive_index(local_media, monkeypatch):
    media, store, ident, path, _ = local_media
    write_image(path)
    revision = store.revision('self')
    from backend.app.chat_history import HistoryResources
    monkeypatch.setattr(HistoryResources, '__init__', lambda *a, **k: pytest.fail('full index must not run'))
    result = media.describe('self', ident)
    assert result['items'][0]['state'] == 'available'
    assert result['items'][0]['mime'] == 'image/png'
    assert 'path' not in result['items'][0]
    assert store.revision('self') == revision
    spec = media.specs('self', ident)[0]
    source, mime = media.resolve('self', ident, 0, media.spec_key(spec))
    assert source.read_bytes() == path.read_bytes()


def test_explicit_recheck_discovers_new_download_without_switching_chat(local_media):
    media, store, ident, path, _ = local_media
    first = media.describe('self', ident)
    assert first['items'][0]['state'] == 'not_downloaded'
    assert '微信尚未下载' not in first['items'][0]['detail']
    write_image(path)
    assert media.describe('self', ident)['items'][0]['url'] is None
    assert media.describe('self', ident, refresh=True)['items'][0]['state'] == 'available'
    assert media.describe('self', ident)['items'][0]['state'] == 'available'
    with pytest.raises(HTTPException):
        media.describe('other', ident, refresh=True)


def test_recheck_never_searches_another_chat_directory(local_media):
    media, _, ident, path, root = local_media
    other = root / 'msg' / 'attach' / hashlib.md5(b'other').hexdigest() / '2026-10' / 'Img'
    other.mkdir(parents=True)
    write_image(other / path.name)
    assert media.describe('self', ident, refresh=True)['items'][0]['url'] is None


def test_single_message_scan_is_bounded(local_media):
    media, store, ident, path, root = local_media
    write_image(path)
    with store.db() as db:
        message = json.loads(db.execute('SELECT data FROM chat_messages WHERE id=?', (ident,)).fetchone()[0])
    state = store.state('self')
    resources = MessageResources(root, state['databases'], message, file_limit=1)
    assert resources.for_message(message)[0]['state'] != 'local'


def test_single_message_lookup_rejects_external_symlink(local_media, tmp_path):
    media, _, ident, path, _ = local_media
    outside = tmp_path / 'outside.png'
    write_image(outside)
    try:
        path.symlink_to(outside)
    except OSError:
        pytest.skip('symlinks unavailable')
    assert media.describe('self', ident, refresh=True)['items'][0]['url'] is None


def test_message_month_is_checked_before_old_folders_exhaust_budget(local_media):
    media, store, ident, path, root = local_media
    write_image(path)
    old = path.parents[2] / '2020-01' / 'Img'
    old.mkdir(parents=True)
    for index in range(30):
        (old / str(index)).write_bytes(b'old')
    with store.db() as db:
        message = json.loads(db.execute('SELECT data FROM chat_messages WHERE id=?', (ident,)).fetchone()[0])
    message['create_time'] = 1792038312
    resources = MessageResources(root, store.state('self')['databases'], message, file_limit=10)
    assert resources.for_message(message)[0]['state'] == 'local'


def test_new_sticker_index_retains_all_message_integrity_hints(local_media):
    media, store, ident, path, _ = local_media
    write_image(path)
    with store.db() as db:
        message = json.loads(db.execute('SELECT data FROM chat_messages WHERE id=?', (ident,)).fetchone()[0])
        message.update(category='sticker', media_hints=['a' * 32, 'b' * 32])
        store.upsert(db, 'self', message)
    assert media.describe('self', ident)['items'][0]['state'] == 'available'
    assert media.specs('self', ident)[0]['md5s'] == ['a' * 32, 'b' * 32]
