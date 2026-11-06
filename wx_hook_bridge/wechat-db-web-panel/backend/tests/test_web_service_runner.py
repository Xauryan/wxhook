import json
from pathlib import Path
import socket
import sys
import threading
import time

import psutil
import pytest

from backend.web_service_runner import AlreadyRunningError, WebSupervisor, exclusive_lock


def wait_until(check, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return
        time.sleep(.05)
    raise AssertionError('web supervisor did not reach the expected state')


def unused_port():
    with socket.socket() as reader:
        reader.bind(('127.0.0.1', 0))
        return reader.getsockname()[1]


@pytest.fixture
def worker(tmp_path):
    source = tmp_path / 'web_worker.py'
    source.write_text('''import json,os,sys,threading,time
from http.server import BaseHTTPRequestHandler,HTTPServer
from pathlib import Path
class Handler(BaseHTTPRequestHandler):
 def do_GET(self):
  with Path(sys.argv[3]).open('a') as f:f.write(self.path+'\\n')
  data=json.dumps({'ok':True,'instance':os.environ['JQR_WEB_INSTANCE'],'pid':0 if Path(sys.argv[4]).exists() else os.getpid()}).encode()
  self.send_response(200);self.end_headers();self.wfile.write(data)
 def log_message(self,*args):pass
server=HTTPServer(('127.0.0.1',int(sys.argv[1])),Handler)
threading.Thread(target=server.serve_forever,daemon=True).start()
closed=False
while True:
 if Path(sys.argv[2]).exists() and not closed:
  server.shutdown();server.server_close();closed=True
 time.sleep(.02)
''', encoding='utf-8')
    port, api_port = unused_port(), unused_port()
    while api_port == port:
        api_port = unused_port()
    drop, requests = tmp_path / 'close-listener', tmp_path / 'requests.txt'
    manager = WebSupervisor(port, api_port, tmp_path / 'runtime',
        [sys.executable, str(source), str(port), str(drop), str(requests), str(tmp_path / 'fake-pid')],
        interval=.1, startup_grace=.8, retry_delay=.05, stable_period=.3)
    return manager, drop, requests


def test_health_checks_current_instance_and_owned_pid(worker):
    manager, _, _ = worker
    try:
        manager.start_worker()
        wait_until(manager.healthy)
        instance = manager.instance
        manager.instance = 'another-worker'
        assert not manager.healthy()
        manager.instance = instance
        assert manager.healthy()
        (manager.root.parent / 'fake-pid').touch()
        assert not manager.healthy()
    finally:
        manager.stop_worker()


def test_recovers_child_exit_and_records_identity(worker):
    manager, _, requests = worker
    stop, results = threading.Event(), []
    task = threading.Thread(target=lambda: results.append(manager.run(stop)))
    task.start()
    try:
        wait_until(manager.healthy)
        first_pid, first_instance = manager.worker.pid, manager.instance
        root = manager.worker_root
        children = root.children(recursive=True)
        (children[-1] if children else root).kill()
        wait_until(lambda: manager.restart_count == 1 and manager.healthy())
        assert manager.instance != first_instance
        assert manager.worker.pid != first_pid
        events = [json.loads(line) for line in (manager.root / 'events.jsonl').read_text().splitlines()]
        recovery = next(event for event in events if event['status'] == 'recovering')
        assert recovery['last_exit_code'] is not None
        assert recovery['reason'] == 'worker_exited'
        assert all(path == '/__jqr_web_health' for path in requests.read_text().splitlines())
    finally:
        stop.set()
        task.join(8)
    assert not task.is_alive()
    assert results == [0]
    assert json.loads((manager.root / 'state.json').read_text())['status'] == 'stopped'


def test_recovers_lost_listener_while_worker_is_alive(worker):
    manager, drop, _ = worker
    stop = threading.Event()
    task = threading.Thread(target=manager.run, args=(stop,))
    task.start()
    try:
        wait_until(manager.healthy)
        first_instance = manager.instance
        drop.touch()
        wait_until(lambda: not manager.healthy())
        assert manager.worker.poll() is None
        drop.unlink()
        wait_until(lambda: manager.restart_count == 1 and manager.healthy())
        assert manager.instance != first_instance
    finally:
        stop.set()
        task.join(8)
    assert not task.is_alive()


def test_foreign_listener_is_preserved(worker):
    manager, _, _ = worker
    with socket.socket() as foreign:
        foreign.bind(('127.0.0.1', manager.port))
        foreign.listen()
        assert manager.run() == 1
        assert foreign.getsockname()[1] == manager.port
        state = json.loads((manager.root / 'state.json').read_text())
        assert state['reason'] == 'port_already_in_use'
        assert state['worker_pid'] is None


def test_repeated_start_failure_has_a_limit(worker):
    manager, _, _ = worker
    manager.command = [sys.executable, '-c', 'raise SystemExit(9)']
    manager.max_failures = 2
    assert manager.run() == 1
    state = json.loads((manager.root / 'state.json').read_text())
    assert state['status'] == 'failed'
    assert state['reason'] == 'repeated_startup_failure'
    assert state['restart_count'] == 2
    assert state['last_exit_code'] == 9


def test_runtime_lock_rejects_duplicates_and_releases(tmp_path):
    path = tmp_path / 'supervisor.lock'
    with exclusive_lock(path):
        with pytest.raises(AlreadyRunningError):
            with exclusive_lock(path):
                pytest.fail('duplicate acquired the supervisor lock')
    with exclusive_lock(path):
        pass


@pytest.mark.parametrize('port,api_port', [(0,8765),(5173,0),(5173,5173),(65536,8765)])
def test_invalid_ports_rejected(tmp_path, port, api_port):
    with pytest.raises(ValueError):
        WebSupervisor(port, api_port, tmp_path, [])
