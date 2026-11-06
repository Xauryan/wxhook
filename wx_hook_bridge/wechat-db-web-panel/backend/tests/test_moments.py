import asyncio
import io
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

import pytest
import zstandard
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from PIL import Image

from backend.app.active_roster import ActiveRosterError
from backend.app.chat_media import MediaFailure
from backend.app.live_chat import LiveChat
from backend.app.moments import MAX_RECORD_BYTES, Moments, bounded_content, create_moments_router, parse_post
from backend.app.moments_source import prepare_moments
from backend.app.sns_media import build_sns_cache_key
from backend.app.sns_sync import SnsSnapshotSync


def timeline(ident, username='peer', timestamp=1000, media=True):
    attachment = '<mediaList><media><id>99</id><type>2</type><url key="PRIVATE-KEY">https://remote.invalid/full</url><size width="12" height="8"/></media></mediaList>' if media else ''
    return f'''<SnsDataItem><TimelineObject><id>{ident}</id><username>{username}</username>
        <createTime>{timestamp}</createTime><contentDesc>Real cached content {ident}</contentDesc>
        <ContentObject><type>1</type>{attachment}</ContentObject></TimelineObject>
        <LocalExtraInfo><nickname>Source Name</nickname>
        <like_user_list><user_comment><username>liker</username><nickname>Cached Liker</nickname></user_comment></like_user_list>
        <comment_user_list><user_comment><comment_id>7</comment_id><username>commenter</username><nickname>Cached Commenter</nickname><content>Visible comment</content><create_time>1001</create_time></user_comment>
        <user_comment><username>deleted</username><content>Deleted comment</content><b_deleted>1</b_deleted></user_comment></comment_user_list>
        </LocalExtraInfo></SnsDataItem>'''


def make_sns(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as db, db:
        db.execute('CREATE TABLE SnsTimeLine(tid INTEGER PRIMARY KEY,user_name TEXT,content BLOB)')
        db.executemany('INSERT INTO SnsTimeLine VALUES(?,?,?)', rows)


@pytest.fixture
def fixture(tmp_path):
    service = LiveChat(tmp_path / 'ledger.sqlite', 'http://127.0.0.1:1', tmp_path / 'relay.json')
    service.moments = Moments(service)
    async def check(account, force=False):
        if account != 'self':
            raise HTTPException(409, 'account switched')
        return {'username': account}
    service.check_account = check
    root = tmp_path / 'self_1234'
    root.mkdir()
    database = tmp_path / 'sns.sqlite'
    make_sns(database, [(-15, 'peer', timeline('18446744073709551601')),
                        (2, 'self', timeline('2', 'self', 2000, False)),
                        (3, 'peer', timeline('3', timestamp=1000, media=False))])
    snapshot = {'account': 'self', 'root': str(root), 'database': str(database),
                'fingerprint': 'fixture', 'verified_at': 1000, 'source': 'active_sns_db'}
    async def prepare(_):
        return snapshot
    service.moments.prepare = prepare
    with service.db() as db:
        db.executemany('INSERT INTO live_names VALUES(?,?,?)', [('self', 'peer', 'Current Remark'), ('self', 'liker', 'Known Liker')])
    service.moments.apply('self', snapshot)
    stream = io.BytesIO()
    Image.new('RGB', (12, 8), 'blue').save(stream, format='PNG')
    key = build_sns_cache_key('18446744073709551601', '99', 1)
    image = root / 'cache/2026-09/Sns/Img' / key[:2] / key[2:]
    image.parent.mkdir(parents=True)
    image.write_bytes(stream.getvalue())
    service.moments.apply('self', snapshot)
    return service, root, snapshot, image


def test_account_scoped_real_posts_and_interactions(fixture):
    service, _, _, _ = fixture
    result = service.moments.list('self')
    assert result['ready'] and result['total'] == 3
    assert [item['id'] for item in result['items']] == ['2', '18446744073709551601', '3']
    post = result['items'][1]
    assert post['display_name'] == 'Current Remark'
    assert post['likes'][0]['display_name'] == 'Known Liker'
    assert post['comment_count'] == 1 and post['comments'][0]['content'] == 'Visible comment'
    assert post['interactions_limited'] and result['interactions_limited']
    assert post['media'][0]['state'] == 'local'
    assert not result['write_supported']
    assert service.moments.list('self', scope='self')['total'] == 1
    assert service.moments.list('self', q='CURRENT REMARK')['total'] == 2
    assert not service.moments.list('other')['ready']
    assert service.moments.list('other')['items'] == []
    serialized = json.dumps(result)
    assert all(value not in serialized for value in ('PRIVATE-KEY', 'remote.invalid', 'raw_xml', 'source.sqlite', '_media'))


@pytest.mark.parametrize('location,expected', [
    ('<location city="深圳" poiName="人才公园" poiAddress="南山区" latitude="22.5" longitude="114.1"/>', '深圳 · 人才公园'),
    ('<location city="深圳"/>', '深圳'),
    ('<location city="深圳" poiName="深圳"/>', '深圳'),
    ('<location latitude="0" longitude="0"/>', ''),
    ('', ''),
])
def test_location_is_always_human_readable_scalar(location, expected):
    content = timeline('1').replace('<ContentObject>', location + '<ContentObject>')
    post = parse_post({'tid': 1, 'user_name': 'peer', 'content': content}, 'self')
    assert post['location'] == expected and isinstance(post['location'], str)
    assert all(isinstance(post[key], str) for key in ('id', 'username', 'nickname', 'content'))
    assert all(isinstance(post[key], int) for key in ('create_time', 'content_type', 'like_count', 'comment_count'))
    assert isinstance(post['is_owner'], bool) and isinstance(post['interactions_limited'], bool)


@pytest.mark.parametrize('timestamp,expected', [(99999999999999999, 0), (-1, 0), (1710000000, 1710000000)])
def test_invalid_timestamps_are_explicitly_unknown(timestamp, expected):
    post = parse_post({'tid': 1, 'user_name': 'peer', 'content': timeline('1', timestamp=timestamp)}, 'self')
    assert post['create_time'] == expected


def test_legacy_cached_locations_are_normalized_before_refresh(fixture):
    service, _, _, _ = fixture
    item = service.moments.raw('self', '2')
    item.update(location={'city': '深圳', 'poiName': '人才公园'}, create_time=99999999999999999)
    with service.db() as db:
        db.execute('UPDATE moments_items SET data=? WHERE account=? AND id=?', (json.dumps(item), 'self', '2'))
    result = next(item for item in service.moments.list('self')['items'] if item['id'] == '2')
    assert result['location'] == '深圳 · 人才公园' and result['create_time'] == 0


def test_compressed_records_are_bounded_before_decoding(monkeypatch):
    normal = timeline('1').encode()
    assert bounded_content(zstandard.ZstdCompressor().compress(normal)) == normal.decode()
    oversized = b'x' * (MAX_RECORD_BYTES + 1)
    with pytest.raises(ValueError):
        bounded_content(oversized)
    unknown_frame = zstandard.ZstdCompressor(write_content_size=False).compress(oversized)
    with pytest.raises(ValueError):
        bounded_content(unknown_frame)
    declared_frame = zstandard.ZstdCompressor().compress(oversized)
    def must_not_decompress(*args, **kwargs):
        pytest.fail('oversized declared frame must be rejected before decompressor allocation')
    monkeypatch.setattr(zstandard, 'ZstdDecompressor', must_not_decompress)
    with pytest.raises(ValueError):
        bounded_content(declared_frame)


def test_unsigned_ids_are_prioritized_before_import_limit(fixture):
    service, _, snapshot, _ = fixture
    with closing(sqlite3.connect(snapshot['database'])) as db, db:
        db.executemany('INSERT INTO SnsTimeLine VALUES(?,?,?)', ((ident, 'peer', '<broken>') for ident in range(4, 20003)))
    service.moments.apply('self', {**snapshot, 'fingerprint': 'expanded-source'})
    result = service.moments.list('self')
    assert any(item['id'] == '18446744073709551601' for item in result['items'])
    assert result['limited_count'] > 0


def test_cursor_pagination_preserves_unsigned_id_order_and_rejects_stale(fixture):
    service, _, snapshot, _ = fixture
    first = service.moments.list('self', limit=1)
    second = service.moments.list('self', cursor=first['next_cursor'], limit=1)
    third = service.moments.list('self', cursor=second['next_cursor'], limit=1)
    assert [item['id'] for page in (first, second, third) for item in page['items']] == ['2', '18446744073709551601', '3']
    assert not third['has_more'] and third['next_cursor'] is None
    for kwargs in ({'q': 'other'}, {'scope': 'self'}, {'account': 'other'}):
        with pytest.raises(HTTPException) as error:
            service.moments.list(**{'account': 'self', 'cursor': first['next_cursor'], **kwargs})
        assert error.value.status_code == 409
    service.moments.apply('self', {**snapshot, 'fingerprint': 'changed'})
    with pytest.raises(HTTPException) as error:
        service.moments.list('self', cursor=first['next_cursor'])
    assert error.value.status_code == 409
    with pytest.raises(HTTPException) as invalid:
        service.moments.list('self', cursor='%%%')
    assert invalid.value.status_code == 400


def test_media_exact_local_asset_and_account_revision_boundaries(fixture):
    service, _, _, image = fixture
    ident = '18446744073709551601'
    token = service.moments.raw('self', ident)['revision']
    path, mime = service.moments.resolve('self', ident, 0, token)
    assert mime == 'image/png' and path.read_bytes() == image.read_bytes()
    with pytest.raises(HTTPException) as stale:
        service.moments.resolve('self', ident, 0, '0' * 24)
    assert stale.value.status_code == 409
    with pytest.raises(HTTPException) as other:
        service.moments.resolve('other', ident, 0, token)
    assert other.value.status_code == 404
    image.unlink()
    with pytest.raises(MediaFailure) as absent:
        service.moments.resolve('self', ident, 0, token)
    assert absent.value.state == 'not_downloaded'
    item = next(item for item in service.moments.list('self')['items'] if item['id'] == ident)
    assert item['media'][0]['url'] is None


def test_media_cache_root_cannot_follow_outside_account(fixture, monkeypatch, tmp_path):
    service, _, _, image = fixture
    outside = tmp_path / 'outside.png'
    outside.write_bytes(image.read_bytes())
    monkeypatch.setattr('backend.app.moments.find_sns_cache_file', lambda *args, **kwargs: outside)
    ident = '18446744073709551601'
    with pytest.raises(MediaFailure) as error:
        service.moments.resolve('self', ident, 0, service.moments.raw('self', ident)['revision'])
    assert error.value.state == 'not_downloaded'


def test_unsupported_rows_are_explicit_and_wrong_account_never_replaces_cache(fixture):
    service, _, snapshot, _ = fixture
    with pytest.raises(ValueError):
        service.moments.apply('other', snapshot)
    with pytest.raises(ValueError):
        service.moments.apply('self', {**snapshot, 'source': 'legacy_sns_snapshot'})
    with closing(sqlite3.connect(snapshot['database'])) as db, db:
        db.execute('INSERT INTO SnsTimeLine VALUES(4,?,?)', ('peer', '<broken>'))
    service.moments.apply('self', {**snapshot, 'fingerprint': 'malformed-source'})
    result = service.moments.list('self')
    assert result['total'] == 3 and result['limited_count'] == 1
    with pytest.raises(ValueError):
        parse_post({'tid': 9, 'user_name': 'peer', 'content': '<!DOCTYPE SnsDataItem [<!ENTITY x SYSTEM "file:///secret">]><SnsDataItem>&x;</SnsDataItem>'}, 'self')


def test_failed_refresh_retains_only_verified_account_cache(fixture):
    service, _, _, _ = fixture
    async def fail(_):
        raise RuntimeError('fixture failure')
    service.moments.prepare = fail
    async def run():
        await service.moments.ensure('self', force=True)
    asyncio.run(run())
    result = service.moments.list('self')
    assert result['ready'] and result['error'] and result['total'] == 3
    assert not service.moments.list('other')['ready']


def test_routes_guard_accounts_and_have_no_social_write_endpoints(fixture):
    service, _, _, _ = fixture
    app = FastAPI()
    app.include_router(create_moments_router(service), prefix='/api/chat')
    with TestClient(app) as client:
        result = client.get('/api/chat/moments?account=self')
        assert result.status_code == 200 and result.json()['total'] == 3
        assert client.get('/api/chat/moments?account=other').status_code == 409
        image_url = result.json()['items'][1]['media'][0]['thumbnail_url']
        image = client.get(image_url)
        assert image.status_code == 200 and image.headers['content-type'] == 'image/png'
        assert image.headers['cache-control'] == 'private, no-store'
        assert client.get(image_url.replace('account=self', 'account=other')).status_code == 409
        assert client.post('/api/chat/moments/refresh?account=self').status_code == 200
        assert client.post('/api/chat/moments/like?account=self').status_code == 404
        assert client.post('/api/chat/moments/comment?account=self').status_code == 404
        assert client.post('/api/chat/moments/publish?account=self').status_code == 404


def test_routes_recheck_account_after_read_and_svg_media_is_rejected(fixture):
    service, _, _, image = fixture
    ident = '18446744073709551601'
    image.write_bytes(b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>')
    with pytest.raises(MediaFailure) as error:
        service.moments.resolve('self', ident, 0, service.moments.raw('self', ident)['revision'])
    assert error.value.state == 'unsupported'
    service.moments.ensure = lambda *args, **kwargs: None
    calls = []
    async def switched(account, force=False):
        calls.append(account)
        if len(calls) > 1:
            raise HTTPException(409, 'account switched during read')
        return {'username': account}
    service.check_account = switched
    app = FastAPI()
    app.include_router(create_moments_router(service), prefix='/api/chat')
    with TestClient(app) as client:
        response = client.get('/api/chat/moments?account=self')
        assert response.status_code == 409 and 'items' not in response.json()


def test_cached_page_uses_persisted_media_index_without_filesystem_scans(fixture, monkeypatch):
    service, _, _, _ = fixture
    def unexpected_scan(*args, **kwargs):
        pytest.fail('cached listing must not scan the WeChat cache directory')
    monkeypatch.setattr(Moments, 'candidates', unexpected_scan)
    for _ in range(2):
        service.moments = Moments(service)
        result = service.moments.list('self')
        assert result['items'][1]['media'][0]['state'] == 'local'
        assert service.moments.list('other')['items'] == []


def test_media_index_is_root_bound_and_refresh_updates_unchanged_snapshot(fixture, tmp_path):
    service, _, snapshot, image = fixture
    image.unlink()
    service.moments.apply('self', snapshot)
    assert service.moments.list('self')['items'][1]['media'][0]['state'] == 'not_downloaded'
    other_root = tmp_path / 'other-root'
    other_root.mkdir()
    with service.db() as db:
        state = service.moments.state('self')
        state['root'] = str(other_root)
        db.execute('UPDATE moments_state SET data=? WHERE account=?', (json.dumps(state), 'self'))
        db.execute('UPDATE moments_media_index SET data=? WHERE account=?', ('[{"preview":true,"source":true}]', 'self'))
    assert service.moments.list('self')['items'][1]['media'][0]['state'] == 'not_downloaded'


def test_unchanged_snapshot_does_not_reparse_source_records(fixture, monkeypatch):
    service, _, snapshot, _ = fixture
    def unexpected_parse(*args, **kwargs):
        pytest.fail('same verified source fingerprint must not reparse XML')
    monkeypatch.setattr('backend.app.moments.parse_post', unexpected_parse)
    service.moments.apply('self', {**snapshot, 'verified_at': 2000})
    result = service.moments.list('self')
    assert result['updated_at'] == 2000 and result['total'] == 3


def test_page_decodes_only_requested_rows_and_uses_covering_order_index(fixture, monkeypatch):
    service, _, snapshot, _ = fixture
    with closing(sqlite3.connect(snapshot['database'])) as db, db:
        db.executemany('INSERT INTO SnsTimeLine VALUES(?,?,?)',
                       ((ident, 'peer', timeline(str(ident), timestamp=ident, media=False)) for ident in range(4, 2004)))
    service.moments.apply('self', {**snapshot, 'fingerprint': 'large-page-fixture'})
    original_loads = json.loads
    decoded = []
    def count_posts(value, *args, **kwargs):
        parsed = original_loads(value, *args, **kwargs)
        if isinstance(parsed, dict) and '_media' in parsed:
            decoded.append(parsed['id'])
        return parsed
    monkeypatch.setattr('backend.app.moments.json.loads', count_posts)
    result = service.moments.list('self', limit=7)
    assert result['total'] == 2003 and len(result['items']) == 7 and len(decoded) == 7
    second = service.moments.list('self', cursor=result['next_cursor'], limit=7)
    assert len(decoded) == 14
    assert not set(item['id'] for item in result['items']) & set(item['id'] for item in second['items'])
    with service.db() as db:
        plan = db.execute('''EXPLAIN QUERY PLAN SELECT data FROM moments_items WHERE account=?
            AND (json_extract(data,'$.create_time'),length(id),id)<(?,?,?)
            ORDER BY json_extract(data,'$.create_time') DESC,length(id) DESC,id DESC LIMIT 8''',
            ('self', 2000, 4, '2000')).fetchall()
    details = ' '.join(row[3] for row in plan)
    assert 'moments_page' in details and 'TEMP B-TREE' not in details


def test_indexed_search_preserves_unicode_casefold_and_current_contact_names(fixture):
    service, _, _, _ = fixture
    with service.db() as db:
        db.execute('UPDATE live_names SET name=? WHERE account=? AND username=?', ('Straße', 'self', 'peer'))
    result = service.moments.list('self', q='STRASSE', limit=1)
    assert result['total'] == 2 and result['has_more']
    assert result['items'][0]['display_name'] == 'Straße'
    assert service.moments.list('self', q='STRASSE', scope='self')['total'] == 0


def test_refresh_returns_cached_page_without_waiting_and_coalesces_requests(fixture):
    service, _, snapshot, _ = fixture
    async def run():
        release, started = asyncio.Event(), asyncio.Event()
        attempts = []
        async def slow_prepare(account):
            attempts.append(account)
            started.set()
            await release.wait()
            return snapshot
        service.moments.prepare = slow_prepare
        router = create_moments_router(service)
        refresh = next(route.endpoint for route in router.routes if route.path == '/moments/refresh')
        responses = await asyncio.wait_for(asyncio.gather(*(refresh('self') for _ in range(8))), timeout=2)
        await asyncio.wait_for(started.wait(), timeout=2)
        assert attempts == ['self'] and not release.is_set()
        assert all(result['ready'] and result['syncing'] and result['total'] == 3 for result in responses)
        release.set()
        await service.moments.tasks['self']
        assert not service.moments.list('self')['syncing']
    asyncio.run(run())


def test_failed_background_refresh_keeps_verified_revision_and_reports_error(fixture):
    service, _, _, _ = fixture
    before = service.moments.list('self')
    async def run():
        release = asyncio.Event()
        async def fail(_):
            await release.wait()
            raise RuntimeError('do not expose private source paths')
        service.moments.prepare = fail
        router = create_moments_router(service)
        refresh = next(route.endpoint for route in router.routes if route.path == '/moments/refresh')
        result = await refresh('self')
        assert result['syncing'] and result['revision'] == before['revision']
        release.set()
        await service.moments.tasks['self']
    asyncio.run(run())
    after = service.moments.list('self')
    assert after['revision'] == before['revision'] and after['updated_at'] == before['updated_at']
    assert after['error'] and 'private source' not in after['error'] and not after['syncing']
    assert after['error_stage'] == 'prepare'


def test_account_switch_during_background_refresh_does_not_apply_snapshot(fixture):
    service, _, snapshot, _ = fixture
    async def switched(account, force=False):
        raise HTTPException(409, 'account switched')
    service.check_account = switched
    async def run():
        await service.moments.ensure('self', force=True)
    asyncio.run(run())
    assert service.moments.state('self')['fingerprint'] == snapshot['fingerprint']
    assert service.moments.list('self')['error']
    assert not service.moments.list('other')['ready']


def snapshot_reader(tmp_path):
    root = tmp_path / 'self_1234'
    contact = root / 'db_storage/contact/contact.db'
    contact.parent.mkdir(parents=True)
    contact.write_bytes(b'fixture')
    source = root / 'db_storage/sns/sns.db'
    make_sns(source, [(1, 'self', timeline('1'))])
    binding = {'account': 'self', 'pid': 1, 'created_at': 1, 'source': contact}
    reader = SimpleNamespace(verify=lambda account, force=False: binding, cache_root=tmp_path / 'cache',
                             loader=SnsSnapshotSync(Path('unused'), tmp_path, tmp_path / 'unused'))
    return reader, source, binding


def test_snapshot_private_copy_preserves_plain_source_and_wal(tmp_path):
    reader, source, _ = snapshot_reader(tmp_path)
    writer = sqlite3.connect(source)
    writer.execute('PRAGMA journal_mode=WAL')
    writer.execute('INSERT INTO SnsTimeLine VALUES(2,?,?)', ('self', timeline('2')))
    writer.commit()
    before = {p.name: (p.stat().st_mtime_ns, p.read_bytes()) for p in source.parent.iterdir()}
    snapshot = prepare_moments(reader, 'self')
    after = {p.name: (p.stat().st_mtime_ns, p.read_bytes()) for p in source.parent.iterdir()}
    assert before == after
    with closing(sqlite3.connect(snapshot['database'])) as db, db:
        assert db.execute('SELECT count(*) FROM SnsTimeLine').fetchone()[0] == 2
    assert snapshot['account'] == 'self' and snapshot['source'] == 'active_sns_db'
    writer.close()


def test_snapshot_rejects_account_switch_missing_source_and_unsafe_cache(tmp_path):
    reader, source, binding = snapshot_reader(tmp_path)
    calls = []
    def verify(account, force=False):
        calls.append(account)
        return binding if len(calls) == 1 else {**binding, 'pid': 2}
    reader.verify = verify
    with pytest.raises(ActiveRosterError):
        prepare_moments(reader, 'self')
    reader.verify = lambda account, force=False: binding
    reader.cache_root = source.parents[2] / 'bad-cache'
    with pytest.raises(ActiveRosterError):
        prepare_moments(reader, 'self')
    source.unlink()
    with pytest.raises(ActiveRosterError):
        prepare_moments(reader, 'self')
