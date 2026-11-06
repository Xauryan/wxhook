import asyncio
import hashlib
import io
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from PIL import Image

from backend.app.active_roster import ActiveRosterError
from backend.app.chat_history import readonly
from backend.app.chat_media import MediaFailure
from backend.app.favorite_data import FavoriteFiles, parse_favorite
from backend.app.favorite_source import prepare_favorites
from backend.app.live_chat import LiveChat, create_live_router
from backend.app.sns_sync import SnsSnapshotSync


def png(color='red', size=(12, 8)):
    output = io.BytesIO()
    Image.new('RGB', size, color).save(output, format='PNG')
    return output.getvalue()


def image_xml(original, thumbnail=None):
    thumb = f'<thumbfullmd5>{hashlib.md5(thumbnail).hexdigest()}</thumbfullmd5><thumbfullsize>{len(thumbnail)}</thumbfullsize>' if thumbnail else ''
    return f'<favitem type="2"><source><fromusr>peer</fromusr></source><datalist><dataitem datatype="2" dataid="unrelated-id"><datafmt>png</datafmt><fullmd5>{hashlib.md5(original).hexdigest()}</fullmd5><fullsize>{len(original)}</fullsize>{thumb}<cdn_datakey>PRIVATE-KEY</cdn_datakey></dataitem></datalist></favitem>'


def favorite_db(path, rows):
    db = sqlite3.connect(path)
    db.executescript('''CREATE TABLE fav_db_item(local_id INTEGER PRIMARY KEY,server_id INTEGER,type INTEGER,flag INTEGER,update_time INTEGER,content TEXT,fromusr TEXT);
        CREATE TABLE fav_tag_db_item(local_id INTEGER PRIMARY KEY,name TEXT,seq INTEGER);
        CREATE TABLE fav_bind_tag_db_item(tag_local_id,tag_server_id,fav_local_id,fav_server_id,op_code);''')
    db.executemany('INSERT INTO fav_db_item VALUES (?,?,?,?,?,?,?)', rows)
    db.commit(); db.close()


@pytest.fixture
def fixture(tmp_path):
    service = LiveChat(tmp_path / 'ledger.sqlite', 'http://127.0.0.1:1', tmp_path / 'relay.json')
    async def check(account, force=False):
        if account != 'self':
            raise HTTPException(409, 'account switched')
        return {'username': account}
    service.check_account = check
    root = tmp_path / 'self_1234'
    original, thumb = png(), png('blue', (4, 4))
    for folder, name, content in [('data', 'opaque-original', original), ('mid', 'opaque-thumb', thumb)]:
        path = root / 'business/favorite' / folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    database = tmp_path / 'favorite.sqlite'
    favorite_db(database, [(1, 100, 2, 0, 1000, image_xml(original, thumb), 'peer'),
                           (2, 101, 1, 0, 1001, '<favitem type="1"><desc>Plain &lt;script&gt; text</desc></favitem>', 'peer')])
    snapshot = {'account': 'self', 'source': 'active_favorite_db', 'database': str(database), 'root': str(root), 'fingerprint': 'fixture', 'verified_at': 1000}
    async def prepare(account):
        return {**snapshot, 'account': account}
    service.favorites.prepare = prepare
    with service.db() as db:
        db.execute('INSERT INTO live_names VALUES (?,?,?)', ('self', 'peer', 'Source Friend'))
    service.favorites.apply('self', snapshot)
    return service, root, snapshot


def test_favorites_use_actual_rows_source_names_and_isolated_search(fixture):
    service, _, _ = fixture
    result = service.favorites.list('self')
    assert result['ready'] and result['counts'] == {'all': 2, 'media': 1}
    assert [item['id'] for item in result['items']] == ['2', '1']
    assert all(item['source_name'] == 'Source Friend' for item in result['items'])
    assert result['recorded_attachment_bytes'] > 0 and result['cloud_remaining_bytes'] is None
    assert not result['write_supported']
    assert service.favorites.list('self', q='PLAIN')['total'] == 1
    assert service.favorites.list('self', q='Source Friend', category='media')['total'] == 1
    assert service.favorites.list('other')['items'] == []
    assert not service.favorites.list('other')['ready']
    serialized = json.dumps(result)
    assert 'PRIVATE-KEY' not in serialized and 'opaque-original' not in serialized and 'candidates' not in serialized


def test_tags_only_come_from_known_source_bindings(fixture):
    service, _, snapshot = fixture
    db = sqlite3.connect(snapshot['database'])
    db.executescript("INSERT INTO fav_tag_db_item VALUES (1,'Work',1),(2,'Unknown binding',2); INSERT INTO fav_bind_tag_db_item VALUES (1,0,1,0,0),(2,0,2,0,99);")
    db.close()
    service.favorites.apply('self', snapshot)
    result = service.favorites.list('self', tag='1')
    assert result['total'] == 1 and result['items'][0]['id'] == '1'
    assert result['tags'][0]['count'] == 1 and result['binding_limited'] == 1
    assert service.favorites.list('self', tag='2')['items'] == []


def test_snapshot_mismatch_does_not_overwrite_current_favorites(fixture):
    service, _, snapshot = fixture
    with pytest.raises(ValueError):
        service.favorites.apply('other', snapshot)
    assert service.favorites.list('self')['counts']['all'] == 2


def test_media_verified_by_digest_and_wrong_candidates_never_leak(fixture):
    service, root, _ = fixture
    original = root / 'business/favorite/data/opaque-original'
    wrong = original.with_name('000-wrong')
    wrong.write_bytes(b'x' * original.stat().st_size)
    with service.db() as db:
        item = service.favorites.raw('self', 1)
        item['_media'][0]['candidates'].insert(0, wrong.relative_to(root).as_posix())
        db.execute('UPDATE favorite_items SET data=? WHERE account=? AND id=?', (json.dumps(item), 'self', 1))
    path, mime = service.favorites.resolve('self', 1, 0)
    assert mime == 'image/png' and path.read_bytes() == original.read_bytes()
    result = service.favorites.media('self', 1)
    assert result['items'][result['preferred']]['variant'] == 'thumbnail'
    assert result['items'][result['preferred']]['state'] == 'available'
    original.write_bytes(b'y' * original.stat().st_size)
    with pytest.raises(MediaFailure) as error:
        service.favorites.resolve('self', 1, 0)
    assert error.value.state == 'integrity_failed'


@pytest.mark.parametrize('candidate', ['../outside', 'business/favorite/data/../../../outside', 'msg/private.png', 'business/favorite/data/evil:stream'])
def test_favorite_resource_paths_cannot_escape_verified_roots(fixture, candidate):
    service, _, _ = fixture
    item = service.favorites.raw('self', 1)
    item['_media'][0]['candidates'] = [candidate]
    with service.db() as db:
        db.execute('UPDATE favorite_items SET data=? WHERE account=? AND id=?', (json.dumps(item), 'self', 1))
    with pytest.raises(MediaFailure):
        service.favorites.resolve('self', 1, 0)


def test_stale_tokens_and_other_accounts_rejected(fixture):
    service, _, _ = fixture
    with pytest.raises(HTTPException) as stale:
        service.favorites.resolve('self', 1, 0, '0' * 24)
    assert stale.value.status_code == 409
    with pytest.raises(HTTPException) as other:
        service.favorites.resolve('other', 1, 0)
    assert other.value.status_code == 404


def test_parser_does_not_execute_or_expose_xml_and_recognizes_safe_links(tmp_path):
    files = FavoriteFiles(tmp_path)
    body = '<favitem><title>&lt;script&gt;title&lt;/script&gt;</title><weburlitem><link>javascript:alert(1)</link></weburlitem></favitem>'
    item = parse_favorite({'local_id': 1, 'type': 5, 'content': body}, files)
    assert item['url'] == '' and item['title'] == '<script>title</script>'
    item = parse_favorite({'local_id': 2, 'type': 18, 'content': '<!DOCTYPE favitem [<!ENTITY x SYSTEM "file:///secret">]><favitem><desc>&x;</desc></favitem>'}, files)
    assert item['parse_error'] and item['content'] == ''
    assert parse_favorite({'local_id': 3, 'type': 2, 'content': '<broken'}, files)['parse_error']


@pytest.mark.parametrize('sender,recipient,expected', [('self', 'peer', 'peer'), ('peer', 'self', 'peer'), ('self', 'self', 'self')])
def test_source_attribution_uses_message_participants_not_always_the_collector(tmp_path, sender, recipient, expected):
    body = f'<favitem><source><fromusr>{sender}</fromusr><tousr>{recipient}</tousr></source><desc>text</desc></favitem>'
    item = parse_favorite({'local_id': 1, 'type': 1, 'fromusr': 'self', 'content': body}, FavoriteFiles(tmp_path), 'self')
    assert item['source_username'] == expected


def test_unrecognized_states_and_missing_assets_are_explicit(fixture):
    service, root, snapshot = fixture
    (root / 'business/favorite/mid/opaque-thumb').unlink()
    db = sqlite3.connect(snapshot['database'])
    db.execute("INSERT INTO fav_db_item VALUES (3,102,1,99,1002,'hidden-or-unknown','peer')")
    db.commit(); db.close()
    service.favorites.apply('self', snapshot)
    result = service.favorites.list('self')
    assert result['limited_count'] == 1 and result['counts']['all'] == 2
    media = service.favorites.media('self', 1)
    assert media['items'][media['preferred']]['variant'] == 'original'
    assert media['items'][1]['state'] == 'not_downloaded'


def test_refresh_failure_retains_verified_cache_without_claiming_success(fixture):
    service, _, _ = fixture
    async def fail(_):
        raise RuntimeError('fixture unavailable')
    service.favorites.prepare = fail
    async def run():
        await service.favorites.ensure('self', force=True)
    asyncio.run(run())
    result = service.favorites.list('self')
    assert result['error'] and result['ready'] and result['counts']['all'] == 2
    assert not service.favorites.list('other')['ready']


def test_plain_wal_snapshot_never_writes_source_and_switches_are_rejected(tmp_path):
    root = tmp_path / 'self_123'
    source = root / 'db_storage/favorite/favorite.db'
    source.parent.mkdir(parents=True)
    writer = sqlite3.connect(source)
    writer.execute('PRAGMA journal_mode=WAL'); writer.execute('CREATE TABLE evidence(value)'); writer.commit()
    writer.execute('INSERT INTO evidence VALUES (42)'); writer.commit()
    binding = {'account': 'self', 'source': root / 'db_storage/contact/contact.db', 'pid': 1}
    loader = SnsSnapshotSync(tmp_path / 'unused', tmp_path, tmp_path / 'cache')
    reader = SimpleNamespace(verify=lambda *a, **k: binding, loader=loader, cache_root=tmp_path / 'cache')
    before = [p.read_bytes() for p in [source, Path(str(source) + '-wal')]]
    result = prepare_favorites(reader, 'self')
    with readonly(result['database']) as db:
        assert db.execute('SELECT value FROM evidence').fetchone()[0] == 42
    assert before == [p.read_bytes() for p in [source, Path(str(source) + '-wal')]]
    calls = iter([binding, {**binding, 'pid': 2}])
    reader.verify = lambda *a, **k: next(calls)
    with pytest.raises(ActiveRosterError):
        prepare_favorites(reader, 'self')
    writer.close()


def test_live_routes_serve_only_verified_assets_and_have_no_write_endpoints(fixture):
    service, _, _ = fixture
    app = FastAPI(); app.include_router(create_live_router(service))
    with TestClient(app) as client:
        response = client.get('/api/chat/favorites?account=self')
        assert response.status_code == 200 and response.json()['counts']['all'] == 2
        item = service.favorites.raw('self', 1)
        url = service.favorites.asset_url('self', 1, 0, item['_media'][0])
        media = client.get(url)
        assert media.status_code == 200 and media.headers['content-type'] == 'image/png'
        assert media.headers['x-content-type-options'] == 'nosniff'
        assert client.get(url.replace('account=self', 'account=other')).status_code == 409
        assert client.post('/api/chat/favorites', json={'content': 'must not create'}).status_code == 405
