"""Keep the test run away from the live panel's state.

``backend.app.main`` builds its services at import time from
``backend.app.config.settings``, and ``TestClient(app)`` runs the full
lifespan (group Q&A executor, group reports, realtime/SNS loops, friend
automation).  Against the real settings that grabs the real
``.cache/group-replies/.worker.lock``, claims real queued runs, writes the real
AI usage log and talks to the real WeChat bridge, so a backend started during
the run cannot start its own workers.

This conftest is imported by pytest before any test module, so it rewrites the
settings before anything reads them:

* panel root, repository root and every ``.cache`` path move to a throw-away
  temp tree (``WECHAT_PANEL_TEST_ROOT`` overrides its location);
* the WeChat bridge and robot-dispatch URLs point at a closed loopback port,
  any ``Settings`` built by a test that names the live bridge port is
  redirected there too, and socket/event-loop connects to the live bridge
  (19088) or live panel API (8765) are refused and fail the test;
* the decrypted source snapshot, its report, the FTS tokenizer and
  ``Weixin.dll`` keep their real locations: they are only ever opened
  read-only.  Set ``WECHAT_DB_ROOT`` etc. to override as usual.

Tests that need the real snapshot (``real_snapshot`` marker, or the
``realtime_repository`` fixture) build a full analytics cache from it inside the
temp tree (15+ minutes, ~4 GB) and copy it per test, so they are skipped unless
``WECHAT_TEST_REAL_SNAPSHOT=1``; point ``WECHAT_PANEL_TEST_ROOT`` at a kept
directory to reuse the built cache between runs.
"""
from __future__ import annotations

import asyncio
import asyncio.base_events
import asyncio.proactor_events
import asyncio.selector_events
import os
import shutil
import socket
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

import pytest

if "backend.app.config" in sys.modules:  # pragma: no cover - import-order guard
    raise RuntimeError("backend.app.config was imported before the test isolation conftest")


def _closed_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


_TEST_ROOT = Path(
    os.environ.get("WECHAT_PANEL_TEST_ROOT") or tempfile.mkdtemp(prefix="wechat-panel-tests-")
).resolve()
_OWNS_TEST_ROOT = "WECHAT_PANEL_TEST_ROOT" not in os.environ
_DEAD_URL = f"http://127.0.0.1:{_closed_loopback_port()}"
_LIVE_BRIDGE_URL = os.environ.get("WECHAT_FRIEND_AUTOMATION_BRIDGE_URL") or "http://127.0.0.1:19088"
# Live WeChat bridge (19088 or as configured) and the live panel API (8765).
PROTECTED_PORTS = frozenset({19088, 8765, urlsplit(_LIVE_BRIDGE_URL).port or 80})
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "0.0.0.0"})

for _name in (
    "WECHAT_CHAT_ONLY",
    "WECHAT_SNS_LIVE_SNAPSHOT",
    "WECHAT_FRIEND_AUTOMATION_ENABLED",
    "YSH_ROBOT_DISPATCH_TOKEN",
    "YSH_DELIVERY_MANAGEMENT_TOKEN",
):
    os.environ.pop(_name, None)
os.environ.update({
    "WECHAT_FRIEND_AUTOMATION_BRIDGE_URL": _DEAD_URL,
    "YSH_ROBOT_DISPATCH_REST_URL": _DEAD_URL.replace("http://", "https://"),
    "YSH_ROBOT_DISPATCH_WSS_URL": _DEAD_URL.replace("http://", "wss://"),
    "YSH_ROBOT_DISPATCH_AUTOSTART": "0",
    "YSH_DELIVERY_NOTIFICATIONS_AUTOSTART": "0",
})

from backend.app import config as _config  # noqa: E402

_real = _config.settings
_panel_root = _TEST_ROOT / "wechat-db-web-panel"
_repository_root = _TEST_ROOT
(_panel_root / ".cache").mkdir(parents=True, exist_ok=True)

# Source data stays where it is (read-only); from_env() keeps returning it.
os.environ.setdefault("WECHAT_DB_ROOT", str(_real.database_root))
os.environ.setdefault("WECHAT_DECRYPTION_REPORT", str(_real.report_path))
os.environ.setdefault("WECHAT_FTS_EXTENSION", str(_real.fts_extension))
os.environ.setdefault("WEIXIN_DLL_PATH", str(_real.weixin_dll))
os.environ["WECHAT_ANALYTICS_CACHE"] = str(_panel_root / ".cache" / "analytics.sqlite")

_config.PANEL_ROOT = _panel_root
_config.REPOSITORY_ROOT = _repository_root
_config.settings = _config.Settings.from_env()

_REAL_CACHE_ROOT = (_real.panel_root / ".cache").resolve()
TEST_SETTINGS = _config.settings
assert TEST_SETTINGS.cache_path.is_relative_to(_TEST_ROOT)
assert not TEST_SETTINGS.cache_path.is_relative_to(_REAL_CACHE_ROOT)
assert TEST_SETTINGS.friend_automation_bridge_url == _DEAD_URL


# --- Guards against reaching the live bridge / API ------------------------
# Tests construct their own Settings (e.g. test_realtime.make_repository) and
# the dataclass default bridge URL is the real one; lifespan then hands those
# settings to FriendAutomation, which posts autoAcceptFriend to the bridge.
_original_post_init = _config.Settings.__post_init__


def _isolated_post_init(self) -> None:
    _original_post_init(self)
    if urlsplit(self.friend_automation_bridge_url).port in PROTECTED_PORTS:
        object.__setattr__(self, "friend_automation_bridge_url", _DEAD_URL)


_config.Settings.__post_init__ = _isolated_post_init

# Last line of defence: refuse any socket connection to the live bridge or the
# live panel API, whatever URL the code under test came up with.
_blocked_attempts: list[str] = []


def _protected(address) -> bool:
    try:
        host, port = address[0], int(address[1])
    except (TypeError, ValueError, IndexError):
        return False
    return port in PROTECTED_PORTS and str(host).casefold() in _LOOPBACK_HOSTS


def _refuse(address) -> None:
    _blocked_attempts.append(f"{address[0]}:{address[1]}")
    raise ConnectionRefusedError(f"test isolation blocked a connection to the live service at {address[0]}:{address[1]}")


_real_getaddrinfo = socket.getaddrinfo


def _guarded_getaddrinfo(host, port, *args, **kwargs):
    if _protected((host, port)):
        _refuse((host, port))
    return _real_getaddrinfo(host, port, *args, **kwargs)


def _guard_method(owner, name):
    original = getattr(owner, name)

    def guarded(self, *args, **kwargs):
        address = args[0] if owner is socket.socket else (args[1] if len(args) > 1 else kwargs.get("address"))
        if _protected(address):
            _refuse(address)
        return original(self, *args, **kwargs)

    setattr(owner, name, guarded)


def _guard_create_connection(owner):
    original = owner.create_connection

    async def guarded(self, protocol_factory, host=None, port=None, *args, **kwargs):
        if _protected((host, port)):
            _refuse((host, port))
        return await original(self, protocol_factory, host, port, *args, **kwargs)

    owner.create_connection = guarded


socket.getaddrinfo = _guarded_getaddrinfo
_guard_method(socket.socket, "connect")
_guard_method(socket.socket, "connect_ex")
_guard_method(asyncio.base_events.BaseEventLoop, "sock_connect")
_guard_method(asyncio.selector_events.BaseSelectorEventLoop, "sock_connect")
_guard_method(asyncio.proactor_events.BaseProactorEventLoop, "sock_connect")
_guard_create_connection(asyncio.base_events.BaseEventLoop)


@pytest.fixture(autouse=True)
def _no_live_service_connections():
    start = len(_blocked_attempts)
    yield
    attempts = _blocked_attempts[start:]
    if attempts:
        pytest.fail(f"test tried to reach the live WeChat bridge/API: {sorted(set(attempts))}")


REAL_SNAPSHOT_OPT_IN =os.environ.get("WECHAT_TEST_REAL_SNAPSHOT", "").strip().casefold() in {"1", "true", "yes", "on"}


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "real_snapshot: needs the local decrypted WeChat snapshot and a full analytics "
        "cache build (15+ min, ~4 GB in the temp tree); runs only with WECHAT_TEST_REAL_SNAPSHOT=1",
    )


def pytest_runtest_setup(item):
    needs_snapshot = item.get_closest_marker("real_snapshot") is not None or (
        "realtime_repository" in getattr(item, "fixturenames", ())
    )
    if needs_snapshot and not REAL_SNAPSHOT_OPT_IN:
        pytest.skip("real-snapshot test; set WECHAT_TEST_REAL_SNAPSHOT=1 (and optionally "
                    "WECHAT_PANEL_TEST_ROOT to reuse the built cache) to run it")


def pytest_report_header(config):
    mode = "real snapshot tests enabled" if REAL_SNAPSHOT_OPT_IN else "real snapshot tests skipped"
    return f"isolated panel state: {_TEST_ROOT} (bridge -> {_DEAD_URL}; {mode})"


def pytest_unconfigure(config):
    if _OWNS_TEST_ROOT:
        # Background threads may still hold sqlite handles on Windows.
        shutil.rmtree(_TEST_ROOT, ignore_errors=True)
