"""Keep the owned loopback API available without retrying business requests."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from urllib.error import URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener
from uuid import uuid4

import psutil


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class ApiSupervisor:
    def __init__(self, port, log_root, command=None, *, interval=1, startup_grace=120):
        if not 1024 <= port <= 65535:
            raise ValueError('invalid local API port')
        self.port = port
        self.log_root = Path(log_root).resolve()
        self.log_root.mkdir(parents=True, exist_ok=True)
        self.panel_root = Path(__file__).resolve().parent.parent
        self.command = command or [sys.executable, '-m', 'uvicorn', 'backend.app.main:app',
                                   '--host', '127.0.0.1', '--port', str(port)]
        self.interval, self.startup_grace = interval, startup_grace
        self.opener = build_opener(ProxyHandler({}), NoRedirect())
        self.worker = None
        self.worker_root = None
        self.instance = ''
        self.started_at = 0.0
        self.restart_count = 0

    def start_worker(self):
        self.instance = str(uuid4())
        environment = {**os.environ, 'JQR_API_INSTANCE': self.instance,
                       'PYTHONUTF8': '1', 'PYTHONIOENCODING': 'utf-8',
                       'PYTHONDONTWRITEBYTECODE': '1'}
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
        with (self.log_root / 'api.out.log').open('ab') as output, (self.log_root / 'api.err.log').open('ab') as errors:
            self.worker = subprocess.Popen(self.command, cwd=self.panel_root, env=environment,
                stdin=subprocess.DEVNULL, stdout=output, stderr=errors, creationflags=flags)
        try:
            self.worker_root = psutil.Process(self.worker.pid)
        except psutil.NoSuchProcess:
            self.worker_root = None
        self.started_at = time.monotonic()
        print(f'API worker started: PID {self.worker.pid}', flush=True)

    def healthy(self):
        worker = self.worker
        if worker is None or worker.poll() is not None:
            return False
        try:
            request = Request(f'http://127.0.0.1:{self.port}/api/runtime/health', headers={'Accept': 'application/json'})
            with self.opener.open(request, timeout=.8) as response:
                data = json.loads(response.read(4096))
            return data.get('ok') is True and data.get('instance') == self.instance
        except (OSError, URLError, ValueError, AttributeError):
            return False

    def stop_worker(self):
        if self.worker is None:
            return
        try:
            root = self.worker_root
            processes = (list(reversed(root.children(recursive=True))) + [root]) if root and root.is_running() else []
        except psutil.NoSuchProcess:
            processes = []
        for process in processes:
            try:
                process.terminate()
            except psutil.NoSuchProcess:
                pass
        _, alive = psutil.wait_procs(processes, timeout=2)
        for process in alive:
            try:
                process.kill()
            except psutil.NoSuchProcess:
                pass
        try:
            self.worker.wait(timeout=2)
        except subprocess.TimeoutExpired:
            raise RuntimeError('owned API worker did not stop')
        self.worker = None
        self.worker_root = None

    def run(self, stop=None):
        stop = stop or threading.Event()
        consecutive_restarts = 0
        misses = 0
        was_ready = False
        healthy_since = None
        try:
            self.start_worker()
            while not stop.is_set():
                now = time.monotonic()
                if self.healthy():
                    was_ready, misses = True, 0
                    healthy_since = healthy_since or now
                    if now - healthy_since >= 30:
                        consecutive_restarts = 0
                else:
                    healthy_since = None
                    dead = self.worker.poll() is not None
                    if dead or was_ready or now - self.started_at >= self.startup_grace:
                        misses += 1
                    if dead or misses >= 3:
                        print('Owned API worker is unavailable; restarting it. No chat request is replayed.', flush=True)
                        self.stop_worker()
                        consecutive_restarts += 1
                        if consecutive_restarts > 3:
                            print('API recovery stopped after repeated failures; inspect api.err.log.', flush=True)
                            return 1
                        self.restart_count += 1
                        if stop.wait(min(5, consecutive_restarts)):
                            break
                        self.start_worker()
                        was_ready, misses = False, 0
                stop.wait(self.interval)
            return 0
        finally:
            self.stop_worker()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--log-root', type=Path, required=True)
    options = parser.parse_args()
    return ApiSupervisor(options.port, options.log_root).run()


if __name__ == '__main__':
    raise SystemExit(main())
