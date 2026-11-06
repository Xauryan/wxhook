"""Supervise only the owned frontend, independently of the caller's terminal."""
from __future__ import annotations

import argparse
from contextlib import contextmanager, redirect_stderr, redirect_stdout
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener
from uuid import uuid4

import psutil


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class AlreadyRunningError(RuntimeError):
    pass


@contextmanager
def exclusive_lock(path):
    """An OS-held lock, released even when a supervisor is terminated."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+b') as handle:
        if handle.tell() == 0:
            handle.write(b'0')
            handle.flush()
        handle.seek(0)
        try:
            if sys.platform == 'win32':
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise AlreadyRunningError('Another web supervisor owns this runtime lock') from error
        try:
            yield
        finally:
            if sys.platform == 'win32':
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


class WebSupervisor:
    def __init__(self, port, api_port, runtime_root, command, *, cwd=None,
                 interval=2, startup_grace=30, stable_period=30, retry_delay=1,
                 max_failures=5):
        if any(not 1024 <= value <= 65535 for value in (port, api_port)) or port == api_port:
            raise ValueError('invalid local service ports')
        self.port, self.api_port = port, api_port
        self.root = Path(runtime_root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.command = command
        self.cwd = Path(cwd or Path(__file__).resolve().parent.parent)
        self.interval, self.startup_grace = interval, startup_grace
        self.stable_period, self.retry_delay = stable_period, retry_delay
        self.max_failures = max_failures
        self.opener = build_opener(ProxyHandler({}), NoRedirect())
        self.worker = self.worker_root = None
        self.worker_created_at = None
        self.instance = ''
        self.restart_count = 0
        self.started_at = 0.0
        self.last_exit_code = None
        self.status = 'starting'
        self.reason = ''
        self.supervisor_created_at = psutil.Process().create_time()

    def record(self, status, reason='', *, event=True):
        self.status, self.reason = status, reason
        state = {
            'status': status, 'reason': reason, 'updated_at': time.time(),
            'supervisor_pid': os.getpid(),
            'supervisor_created_at': self.supervisor_created_at,
            'worker_pid': self.worker.pid if self.worker else None,
            'worker_created_at': self.worker_created_at,
            'instance': self.instance, 'port': self.port, 'api_port': self.api_port,
            'restart_count': self.restart_count, 'last_exit_code': self.last_exit_code,
            'web_url': f'http://127.0.0.1:{self.port}/chats',
        }
        temporary = self.root / f'state-{os.getpid()}.tmp'
        temporary.write_text(json.dumps(state, indent=2) + '\n', encoding='utf-8')
        os.replace(temporary, self.root / 'state.json')
        if event:
            with (self.root / 'events.jsonl').open('a', encoding='utf-8') as handle:
                handle.write(json.dumps(state) + '\n')
            print(json.dumps(state), flush=True)

    def start_worker(self):
        self.instance = str(uuid4())
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
        environment = {**os.environ, 'JQR_WEB_INSTANCE': self.instance,
                       'PYTHONUTF8': '1', 'PYTHONIOENCODING': 'utf-8'}
        with (self.root / 'web.out.log').open('ab') as output, (self.root / 'web.err.log').open('ab') as errors:
            self.worker = subprocess.Popen(self.command, cwd=self.cwd, env=environment,
                stdin=subprocess.DEVNULL, stdout=output, stderr=errors, creationflags=flags)
        self.worker_root = psutil.Process(self.worker.pid)
        self.worker_created_at = self.worker_root.create_time()
        self.started_at = time.monotonic()
        self.record('starting', 'worker_started')

    def healthy(self):
        if self.worker is None or self.worker.poll() is not None:
            return False
        try:
            request = Request(f'http://127.0.0.1:{self.port}/__jqr_web_health',
                              headers={'Accept': 'application/json'})
            with self.opener.open(request, timeout=1) as response:
                data = json.loads(response.read(4096))
            if data.get('ok') is not True or data.get('instance') != self.instance:
                return False
            owner = data.get('pid')
            owned = [self.worker_root, *self.worker_root.children(recursive=True)]
            return any(process.pid == owner and process.is_running() for process in owned)
        except (OSError, ValueError, AttributeError, psutil.Error):
            return False

    def port_available(self):
        # Never replace or terminate a listener belonging to another application.
        import socket
        with socket.socket() as probe:
            if sys.platform == 'win32':
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            try:
                probe.bind(('127.0.0.1', self.port))
                return True
            except OSError:
                return False

    def stop_worker(self):
        if self.worker is None:
            return
        try:
            root = self.worker_root
            owned = [*reversed(root.children(recursive=True)), root] if root and root.is_running() else []
        except psutil.NoSuchProcess:
            owned = []
        for process in owned:
            try:
                process.terminate()
            except psutil.NoSuchProcess:
                pass
        _, alive = psutil.wait_procs(owned, timeout=2)
        for process in alive:
            try:
                process.kill()
            except psutil.NoSuchProcess:
                pass
        self.worker.wait(timeout=3)
        self.worker = self.worker_root = None
        self.worker_created_at = None

    def run(self, stop=None):
        stop = stop or threading.Event()
        failures, misses = 0, 0
        ready_since = None
        heartbeat_at = 0.0
        try:
            if not self.port_available():
                self.record('failed', 'port_already_in_use')
                return 1
            self.start_worker()
            while not stop.is_set():
                now = time.monotonic()
                if self.healthy():
                    misses = 0
                    ready_since = ready_since or now
                    if now - ready_since >= self.stable_period:
                        failures = 0
                    if self.status != 'ready' or now - heartbeat_at >= 10:
                        self.record('ready', 'health_verified', event=self.status != 'ready')
                        heartbeat_at = now
                else:
                    dead = self.worker.poll() is not None
                    was_ready = ready_since is not None
                    ready_since = None
                    if dead or was_ready or self.status == 'unhealthy' or now - self.started_at >= self.startup_grace:
                        misses += 1
                        self.record('unhealthy', 'worker_exited' if dead else 'health_check_failed', event=misses == 1)
                    if dead or misses >= 3:
                        self.last_exit_code = self.worker.poll()
                        reason = 'worker_exited' if dead else 'health_check_failed'
                        self.record('recovering', reason)
                        self.stop_worker()
                        failures += 1
                        if failures > self.max_failures:
                            self.record('failed', 'repeated_startup_failure')
                            return 1
                        if not self.port_available():
                            self.record('failed', 'port_taken_by_another_process')
                            return 1
                        if stop.wait(min(10, failures * self.retry_delay)):
                            break
                        self.restart_count += 1
                        self.start_worker()
                        misses = 0
                stop.wait(self.interval)
            self.record('stopping', 'stop_requested')
            return 0
        except Exception as error:
            self.record('failed', f'{type(error).__name__}: {error}')
            raise
        finally:
            self.stop_worker()
            self.record('failed' if self.status == 'failed' else 'stopped', self.reason)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=5173)
    parser.add_argument('--api-port', type=int, default=8765)
    parser.add_argument('--runtime-root', type=Path, required=True)
    parser.add_argument('--node', type=Path, required=True)
    options = parser.parse_args()
    panel = Path(__file__).resolve().parent.parent
    frontend = panel / 'frontend-next'
    for required in (options.node, frontend / 'dist/index.html', frontend / 'scripts/serve-web.mjs'):
        if not required.is_file():
            parser.error(f'Missing existing local file: {required}')
    options.runtime_root.mkdir(parents=True, exist_ok=True)
    with (options.runtime_root / 'runner.out.log').open('a', encoding='utf-8', buffering=1) as output, \
         (options.runtime_root / 'runner.err.log').open('a', encoding='utf-8', buffering=1) as errors, \
         redirect_stdout(output), redirect_stderr(errors):
        try:
            with exclusive_lock(options.runtime_root / 'supervisor.lock'):
                command = [str(options.node), str(frontend / 'scripts/serve-web.mjs'),
                           '--port', str(options.port), '--api-port', str(options.api_port)]
                return WebSupervisor(options.port, options.api_port, options.runtime_root,
                                     command, cwd=frontend).run()
        except AlreadyRunningError:
            print('Another web supervisor owns this runtime lock.', file=sys.stderr, flush=True)
            return 2


if __name__ == '__main__':
    sys.dont_write_bytecode = True
    raise SystemExit(main())
