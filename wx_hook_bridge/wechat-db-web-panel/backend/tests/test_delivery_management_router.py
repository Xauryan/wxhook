import sqlite3
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app import delivery_management_router as module


@pytest.fixture(autouse=True)
def isolated_remote_client(monkeypatch):
    monkeypatch.setattr(module,'_remote_client_instance',None)


def app():
    application = FastAPI()
    application.include_router(module.router)
    return application


def test_real_image_send_rejects_merely_associated_call_before_bridge(tmp_path, monkeypatch):
    from backend.app.delivery_notifications import DeliveryNotificationStore
    store = DeliveryNotificationStore(tmp_path / 'delivery.sqlite'); store.initialize()
    monkeypatch.setattr(module, 'notification_service', SimpleNamespace(store=store))
    dialogues = SimpleNamespace(image=lambda _: ({'call_id': 99, 'version': 'fixture'}, tmp_path / 'fixture.png'),
                                receipt=lambda *args: None, send=AsyncMock())
    monkeypatch.setattr(module, 'dialogue_service', dialogues)
    monkeypatch.setattr(module, 'get_archive_link', AsyncMock(return_value={'url':'https://fixture.invalid/d/abc'}))
    monkeypatch.setattr(module, 'fetch_remote', AsyncMock(return_value={'id':38,'notification_calls':[{'id':99,'role':'delivery'}]}))
    with TestClient(app()) as client:
        response = client.post('/api/delivery-management/cases/38/dialogues/send', json={
            'image_ids':['a' * 32], 'room_ids':['fixture-room@chatroom'], 'simulate':False})
    assert response.status_code == 422
    assert '业务用途' in response.json()['detail']
    dialogues.send.assert_not_awaited()


def test_missing_credentials_and_local_scope(monkeypatch):
    monkeypatch.setattr(module, "integration_token", lambda: "")
    with TestClient(app()) as client:
        assert client.get("/api/delivery-management/overview").status_code == 503
    with TestClient(app(), client=("203.0.113.1", 1234)) as client:
        assert client.get("/api/delivery-management/overview").status_code == 403


def test_reads_credentials_without_touching_queue(tmp_path, monkeypatch):
    monkeypatch.delenv("YSH_DELIVERY_MANAGEMENT_TOKEN", raising=False)
    monkeypatch.delenv("YSH_ROBOT_DISPATCH_TOKEN", raising=False)
    monkeypatch.setattr(module, "settings", SimpleNamespace(cache_path=tmp_path / "analytics.sqlite"))
    path = tmp_path / "intention_notifications.sqlite"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE intention_notification_meta(key TEXT,value TEXT)")
        connection.execute("INSERT INTO intention_notification_meta VALUES('api_token','fixture-secret')")
    before = path.read_bytes()
    assert module.integration_token() == "fixture-secret"
    assert path.read_bytes() == before


def test_proxy_passes_auth_only_to_fixed_server(monkeypatch):
    monkeypatch.setattr(module,"integration_token",lambda:"fixture-secret")
    seen = []
    class FakeClient:
        def __init__(self, **kwargs):
            assert not kwargs["follow_redirects"]
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        async def get(self,url,**kwargs):
            seen.append((url,kwargs))
            return httpx.Response(200,json={"items":[]},request=httpx.Request("GET",url))
    monkeypatch.setattr(module.httpx,"AsyncClient",FakeClient)
    with TestClient(app()) as client:
        response=client.get("/api/delivery-management/calls?day=2026-09-19&limit=10")
        assert response.status_code == 200
        assert response.json() == {"items":[]}
        assert "fixture-secret" not in response.text
        assert client.get("/api/delivery-management/calls?limit=201").status_code == 422
    assert seen[0][0] == module.REMOTE + "/calls"
    assert seen[0][1]["headers"]["Authorization"] == "Bearer fixture-secret"


def test_group_settings_are_local_persisted_and_do_not_send(tmp_path, monkeypatch):
    from backend.app.delivery_notifications import DeliveryNotificationService, DeliveryNotificationStore
    group = {"room_id": "fixture-delivery@chatroom", "name": "Fixture group"}
    bridge = SimpleNamespace(groups=AsyncMock(return_value=[SimpleNamespace(**group)]), send_text=AsyncMock())
    service = DeliveryNotificationService(DeliveryNotificationStore(tmp_path / "delivery.sqlite"), bridge, AsyncMock())
    monkeypatch.setattr(module, "notification_service", service)
    monkeypatch.setattr(module, "integration_token", lambda: "fixture-token")
    with TestClient(app()) as client:
        assert client.get("/api/delivery-management/notifications").json()["config"]["enabled"] is False
        assert client.get("/api/delivery-management/groups").json()["items"] == [group]
        payload = {"enabled": True, "target_groups": [group]}
        rejected = client.post("/api/delivery-management/notifications/config", json=payload, headers={"origin": "https://untrusted.invalid"})
        assert rejected.status_code == 403
        response = client.post("/api/delivery-management/notifications/config", json=payload)
        assert response.status_code == 200
        assert response.json()["config"]["target_groups"] == [group]
        assert client.get("/api/delivery-management/notifications").json()["config"]["enabled"] is True
        assert client.post("/api/delivery-management/notifications/config", json={"enabled": True, "target_groups": [group, group]}).status_code == 422
        assert client.post("/api/delivery-management/notifications/send-overview", json={"day": "2026-09-20", "request_id": "00000000-0000-4000-8000-000000000001"}).status_code == 503
    bridge.send_text.assert_not_awaited()
    assert not (tmp_path / "intention_notifications.sqlite").exists()


@pytest.mark.asyncio
async def test_read_retries_transport_failure_but_write_does_not(monkeypatch):
    monkeypatch.setattr(module,'integration_token',lambda:'fixture')
    response=httpx.Response(200,json={'items':[]},request=httpx.Request('GET',module.REMOTE+'/alerts'))
    client=SimpleNamespace(get=AsyncMock(side_effect=[httpx.ConnectTimeout('fixture'),response]),
                           post=AsyncMock(side_effect=httpx.ReadTimeout('fixture')))
    monkeypatch.setattr(module,'remote_client',lambda:client)
    assert await module.fetch_remote('/alerts',{})=={'items':[]}
    assert client.get.await_count==2
    with pytest.raises(module.HTTPException):
        await module.fetch_remote('/cases/1/update',{},method='POST',payload={})
    assert client.post.await_count==1


def test_saved_stage_preview_reads_the_immutable_message(tmp_path,monkeypatch):
    from datetime import datetime,timezone
    from backend.app.delivery_notifications import DeliveryNotificationService,DeliveryNotificationStore
    store=DeliveryNotificationStore(tmp_path/'delivery.sqlite');store.initialize()
    store.persist_stage('delivery.stage:fixture',datetime(2026,9,20,12,tzinfo=timezone.utc),{'fixture':True},
                        [{'room_id':'fixture-room@chatroom','name':'Fixture'}],'Fixture saved report')
    service=DeliveryNotificationService(store,SimpleNamespace(),AsyncMock())
    monkeypatch.setattr(module,'notification_service',service)
    with TestClient(app()) as client:
        response=client.get('/api/delivery-management/notifications/stages/delivery.stage:fixture')
        assert response.status_code==200
        assert response.json()['message']=='Fixture saved report'
        assert response.json()['payload']=={'fixture':True}
        assert client.get('/api/delivery-management/notifications/stages/missing').status_code==404
    assert store.recent()[0]['status']=='queued'
