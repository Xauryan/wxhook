import json
import os
from pathlib import Path
import socket
import sys
import threading
import time

import httpx
import psutil
import pytest
from fastapi import FastAPI

from backend.app.runtime_health import router
from backend.service_runner import ApiSupervisor


def wait_until(check, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return
        time.sleep(.05)
    raise AssertionError('fixture API did not reach the expected state')


@pytest.fixture
def worker(tmp_path):
    path = tmp_path / 'fixture_worker.py'
    path.write_text('''import json,os,sys,threading,time
from http.server import BaseHTTPRequestHandler,HTTPServer
from pathlib import Path
class Handler(BaseHTTPRequestHandler):
 def do_GET(self):
  data=json.dumps({'ok':True,'instance':os.environ['JQR_API_INSTANCE'],'pid':os.getpid()}).encode()
  self.send_response(200);self.send_header('Content-Type','application/json');self.end_headers();self.wfile.write(data)
 def log_message(self,*args):pass
server=HTTPServer(('127.0.0.1',int(sys.argv[1])),Handler)
threading.Thread(target=server.serve_forever,daemon=True).start()
closed=False
while True:
 if Path(sys.argv[2]).exists() and not closed:
  server.shutdown();server.server_close();closed=True
 time.sleep(.02)
''', encoding='utf-8')
    with socket.socket() as port_reader:
        port_reader.bind(('127.0.0.1', 0))
        port = port_reader.getsockname()[1]
    drop = tmp_path / 'close-listener'
    manager = ApiSupervisor(port, tmp_path / 'logs', [sys.executable, str(path), str(port), str(drop)], interval=.3, startup_grace=3)
    return manager, drop


def test_health_requires_the_current_worker_instance(worker):
    manager, _ = worker
    try:
        manager.start_worker()
        wait_until(manager.healthy)
        instance = manager.instance
        manager.instance = 'unrelated-instance'
        assert not manager.healthy()
        manager.instance = instance
        assert manager.healthy()
    finally:
        manager.stop_worker()


def test_recovers_a_lost_listener_without_replaying_requests(worker):
    manager, drop = worker
    stop = threading.Event()
    results = []
    task = threading.Thread(target=lambda: results.append(manager.run(stop)))
    task.start()
    try:
        wait_until(manager.healthy)
        first_pid = manager.worker.pid
        first_instance = manager.instance
        first_tree = [psutil.Process(first_pid), *psutil.Process(first_pid).children(recursive=True)]
        drop.touch()
        wait_until(lambda: not manager.healthy())
        # The fixture stays alive after closing its listener, as the reported API did.
        assert manager.worker.poll() is None
        drop.unlink()
        wait_until(lambda: manager.restart_count == 1 and manager.healthy(), timeout=20)
        assert manager.worker.pid != first_pid
        assert manager.instance != first_instance
        assert all(not process.is_running() for process in first_tree)
    finally:
        stop.set()
        task.join(8)
    assert not task.is_alive()
    assert results == [0]


def test_child_exit_is_recovered(worker):
    manager, _ = worker
    stop = threading.Event()
    task = threading.Thread(target=manager.run, args=(stop,))
    task.start()
    try:
        wait_until(manager.healthy)
        first_instance = manager.instance
        children = psutil.Process(manager.worker.pid).children(recursive=True)
        if children:
            children[-1].kill()
        else:
            manager.worker.kill()
        # Restarting is checked through a live child exit, not a business API retry.
        wait_until(lambda: manager.restart_count == 1 and manager.healthy(), timeout=20)
        assert manager.instance != first_instance
    finally:
        stop.set()
        task.join(8)
    assert not task.is_alive()


def test_invalid_local_port_is_rejected(tmp_path):
    with pytest.raises(ValueError):
        ApiSupervisor(0, tmp_path)


def test_runtime_health_is_read_only_and_loopback_only(monkeypatch):
    import asyncio
    app = FastAPI()
    app.include_router(router)
    monkeypatch.setenv('JQR_API_INSTANCE', 'fixture-instance')
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=('127.0.0.1', 4000)), base_url='http://testserver') as client:
            result = await client.get('/api/runtime/health')
            assert result.json() == {'ok': True, 'instance': 'fixture-instance', 'pid': os.getpid()}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=('203.0.113.10', 4000)), base_url='http://testserver') as client:
            assert (await client.get('/api/runtime/health')).status_code == 403
    asyncio.run(scenario())
