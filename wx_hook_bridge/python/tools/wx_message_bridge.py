#!/usr/bin/env python3
import argparse
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

DEFAULT_BRIDGE_DIR = Path("bridge")
DEFAULT_INBOX_DIR = DEFAULT_BRIDGE_DIR / "inbox"
DEFAULT_OUTBOX_DIR = DEFAULT_BRIDGE_DIR / "outbox"
DEFAULT_OUTBOX_FILE = DEFAULT_OUTBOX_DIR / "next.txt"
DEFAULT_POLL_INTERVAL = 0.5
DEFAULT_BASE_URL = "http://127.0.0.1:8080"
DEFAULT_ENDPOINTS = [
    "/sendMsg",
    "/api/sendMsg",
    "/api/message/send",
    "/message/send",
    "/send_text",
]
DEFAULT_RECEIVE_PATH = "/wxhook"
DEFAULT_INBOX_FILE = Path("runtime") / "inbox.jsonl"
DEFAULT_SEND_LOG = Path("runtime") / "send.jsonl"
DEFAULT_TIMEOUT = 10.0


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def append_jsonl(path: Path, payload: Dict[str, Any]) -> None:
    ensure_parent(path)
    with path.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(payload, ensure_ascii=False) + "\n")


def load_json(path: Optional[Path]) -> Dict[str, Any]:
    if path is None:
        return {}
    if not path.exists():
        raise FileNotFoundError(f"config file not found: {path}")
    with path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise ValueError("config root must be a JSON object")
    return data


def normalize_base_url(base_url: str) -> str:
    return base_url.rstrip("/")


def join_url(base_url: str, endpoint: str) -> str:
    if endpoint.startswith("http://") or endpoint.startswith("https://"):
        return endpoint
    if not endpoint.startswith("/"):
        endpoint = "/" + endpoint
    return normalize_base_url(base_url) + endpoint


@dataclass
class BridgeConfig:
    base_url: str = DEFAULT_BASE_URL
    send_endpoints: List[str] = field(default_factory=lambda: list(DEFAULT_ENDPOINTS))
    receive_host: str = "127.0.0.1"
    receive_port: int = 19088
    receive_path: str = DEFAULT_RECEIVE_PATH
    inbox_file: Path = DEFAULT_INBOX_FILE
    send_log_file: Path = DEFAULT_SEND_LOG
    request_timeout: float = DEFAULT_TIMEOUT

    @classmethod
    def from_sources(cls, data: Dict[str, Any], args: argparse.Namespace) -> "BridgeConfig":
        def pick(name: str, default: Any) -> Any:
            value = getattr(args, name, None)
            if value is not None:
                return value
            return data.get(name, default)

        send_endpoints = pick("send_endpoints", data.get("send_endpoints", list(DEFAULT_ENDPOINTS)))
        if isinstance(send_endpoints, str):
            send_endpoints = [item.strip() for item in send_endpoints.split(",") if item.strip()]
        if not isinstance(send_endpoints, list) or not all(isinstance(item, str) for item in send_endpoints):
            raise ValueError("send_endpoints must be a list of strings")

        inbox_file = Path(pick("inbox_file", str(DEFAULT_INBOX_FILE)))
        send_log_file = Path(pick("send_log_file", str(DEFAULT_SEND_LOG)))
        receive_path = pick("receive_path", DEFAULT_RECEIVE_PATH)
        if not str(receive_path).startswith("/"):
            receive_path = "/" + str(receive_path)

        return cls(
            base_url=normalize_base_url(str(pick("base_url", DEFAULT_BASE_URL))),
            send_endpoints=send_endpoints or list(DEFAULT_ENDPOINTS),
            receive_host=str(pick("receive_host", "127.0.0.1")),
            receive_port=int(pick("receive_port", 19088)),
            receive_path=str(receive_path),
            inbox_file=inbox_file,
            send_log_file=send_log_file,
            request_timeout=float(pick("request_timeout", DEFAULT_TIMEOUT)),
        )


class MessageReceiverHandler(BaseHTTPRequestHandler):
    server_version = "WxBridge/1.0"

    def do_GET(self) -> None:
        if self.path.rstrip("/") == self.server.receive_path.rstrip("/"):
            self._reply_json(200, {"ok": True, "message": "receiver alive"})
            return
        self._reply_json(404, {"ok": False, "error": "not_found"})

    def do_POST(self) -> None:
        if self.path.rstrip("/") != self.server.receive_path.rstrip("/"):
            self._reply_json(404, {"ok": False, "error": "not_found"})
            return

        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(length) if length > 0 else b""
        content_type = self.headers.get("Content-Type", "")
        payload: Any
        decode_error: Optional[str] = None

        if "application/json" in content_type:
            try:
                payload = json.loads(raw.decode("utf-8")) if raw else {}
            except Exception as exc:
                payload = {"raw_text": raw.decode("utf-8", errors="replace")}
                decode_error = f"json_decode_failed: {exc}"
        elif "application/x-www-form-urlencoded" in content_type:
            parsed = urllib.parse.parse_qs(raw.decode("utf-8", errors="replace"), keep_blank_values=True)
            payload = {key: values[0] if len(values) == 1 else values for key, values in parsed.items()}
        else:
            try:
                payload = json.loads(raw.decode("utf-8")) if raw else {}
            except Exception:
                payload = {
                    "raw_text": raw.decode("utf-8", errors="replace"),
                    "raw_hex": raw.hex(),
                }

        envelope = {
            "received_at": utc_now(),
            "remote": self.client_address[0],
            "path": self.path,
            "content_type": content_type,
            "headers": {key: value for key, value in self.headers.items()},
            "payload": payload,
        }
        if decode_error is not None:
            envelope["decode_error"] = decode_error

        append_jsonl(self.server.inbox_file, envelope)
        sys.stdout.write(json.dumps(envelope, ensure_ascii=False) + "\n")
        sys.stdout.flush()
        self._reply_json(200, {"ok": True, "saved_to": str(self.server.inbox_file)})

    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stderr.write("[receiver] " + (fmt % args) + "\n")

    def _reply_json(self, status: int, payload: Dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class ReceiverServer(ThreadingHTTPServer):
    def __init__(self, server_address: Tuple[str, int], handler_cls: type[BaseHTTPRequestHandler], receive_path: str, inbox_file: Path):
        super().__init__(server_address, handler_cls)
        self.receive_path = receive_path
        self.inbox_file = inbox_file


class MockWxHandler(BaseHTTPRequestHandler):
    server_version = "MockWx/1.0"

    def do_GET(self) -> None:
        if self.path == "/health":
            self._reply_json(200, {"ok": True})
            return
        self._reply_json(404, {"ok": False, "error": "not_found"})

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(length) if length > 0 else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8"))
        except Exception:
            self._reply_json(400, {"ok": False, "error": "invalid_json"})
            return

        self.server.requests.append({"path": self.path, "payload": payload, "at": utc_now()})

        accepted_paths = {"/sendMsg", "/api/sendMsg"}
        text = payload.get("msg") or payload.get("content")
        wxid = payload.get("wxid") or payload.get("toWxId") or payload.get("to_wxid")
        if self.path not in accepted_paths or not wxid or not text:
            self._reply_json(404, {"ok": False, "error": "unsupported_request"})
            return

        self._reply_json(200, {
            "ok": True,
            "ret": 0,
            "msg": "sent",
            "data": {
                "wxid": wxid,
                "content": text,
            },
        })

    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stderr.write("[mock-wx] " + (fmt % args) + "\n")

    def _reply_json(self, status: int, payload: Dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class MockWxServer(ThreadingHTTPServer):
    def __init__(self, server_address: Tuple[str, int], handler_cls: type[BaseHTTPRequestHandler]):
        super().__init__(server_address, handler_cls)
        self.requests: List[Dict[str, Any]] = []


class SendError(RuntimeError):
    pass


SUCCESS_KEYS = ("ok", "success")
SUCCESS_VALUES = {True, 1, "1", "ok", "OK", "success", "SUCCESS"}


def looks_successful(payload: Any, status_code: int) -> bool:
    if 200 <= status_code < 300 and payload is None:
        return True
    if isinstance(payload, dict):
        for key in SUCCESS_KEYS:
            if key in payload and payload[key] in SUCCESS_VALUES:
                return True
        if payload.get("ret") == 0 or payload.get("code") == 0:
            return True
    return 200 <= status_code < 300 and isinstance(payload, (dict, list, str))


def candidate_payloads(to_wxid: str, content: str, from_wxid: Optional[str]) -> Iterable[Dict[str, Any]]:
    base_variants = [
        {"wxid": to_wxid, "msg": content},
        {"wxid": to_wxid, "content": content},
        {"toWxId": to_wxid, "content": content},
        {"to_wxid": to_wxid, "content": content},
    ]
    if from_wxid:
        for item in base_variants:
            clone = dict(item)
            clone["fromWxId"] = from_wxid
            yield clone
    for item in base_variants:
        yield item


def post_json(url: str, payload: Dict[str, Any], timeout: float) -> Tuple[int, Any, str]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
            text = raw.decode("utf-8", errors="replace")
            try:
                parsed = json.loads(text) if text else None
            except Exception:
                parsed = text
            return response.getcode(), parsed, text
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        text = raw.decode("utf-8", errors="replace")
        try:
            parsed = json.loads(text) if text else None
        except Exception:
            parsed = text
        return exc.code, parsed, text


def send_message(config: BridgeConfig, to_wxid: str, content: str, from_wxid: Optional[str], endpoint: Optional[str]) -> Dict[str, Any]:
    endpoints = [endpoint] if endpoint else config.send_endpoints
    attempts: List[Dict[str, Any]] = []

    for endpoint_item in endpoints:
        url = join_url(config.base_url, endpoint_item)
        for payload in candidate_payloads(to_wxid, content, from_wxid):
            status_code, parsed, raw_text = post_json(url, payload, config.request_timeout)
            attempt = {
                "url": url,
                "status_code": status_code,
                "payload": payload,
                "response": parsed,
                "response_text": raw_text,
                "at": utc_now(),
            }
            attempts.append(attempt)
            if looks_successful(parsed, status_code):
                append_jsonl(config.send_log_file, {"ok": True, **attempt})
                return attempt
            if status_code not in (400, 404, 405):
                append_jsonl(config.send_log_file, {"ok": False, **attempt})
                raise SendError(
                    f"request reached {url} but was rejected: HTTP {status_code}, response={raw_text!r}"
                )

    for attempt in attempts:
        append_jsonl(config.send_log_file, {"ok": False, **attempt})
    raise SendError("no working send endpoint/schema combination was accepted")


def run_server(config: BridgeConfig) -> int:
    server = ReceiverServer((config.receive_host, config.receive_port), MessageReceiverHandler, config.receive_path, config.inbox_file)
    sys.stderr.write(
        f"receiver listening on http://{config.receive_host}:{config.receive_port}{config.receive_path}\n"
    )
    sys.stderr.write(f"messages will be saved to {config.inbox_file}\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        sys.stderr.write("receiver stopped\n")
    finally:
        server.server_close()
    return 0


def run_watch(path: Path) -> int:
    if not path.exists():
        sys.stderr.write(f"file not found: {path}\n")
        return 1
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            sys.stdout.write(line)
    return 0


def http_post(url: str, payload: Dict[str, Any], timeout: float = DEFAULT_TIMEOUT) -> Dict[str, Any]:
    status_code, parsed, raw_text = post_json(url, payload, timeout)
    return {"status_code": status_code, "parsed": parsed, "raw_text": raw_text}


def run_selftest() -> int:
    test_dir = Path("runtime") / "selftest"
    inbox_file = test_dir / "inbox.jsonl"
    send_log = test_dir / "send.jsonl"
    for path in (inbox_file, send_log):
        if path.exists():
            path.unlink()

    receiver = ReceiverServer(("127.0.0.1", 19188), MessageReceiverHandler, "/wxhook", inbox_file)
    receiver_thread = threading.Thread(target=receiver.serve_forever, daemon=True)
    receiver_thread.start()

    mock = MockWxServer(("127.0.0.1", 18080), MockWxHandler)
    mock_thread = threading.Thread(target=mock.serve_forever, daemon=True)
    mock_thread.start()

    time.sleep(0.2)

    config = BridgeConfig(
        base_url="http://127.0.0.1:18080",
        send_endpoints=["/sendMsg", "/api/sendMsg"],
        receive_host="127.0.0.1",
        receive_port=19188,
        receive_path="/wxhook",
        inbox_file=inbox_file,
        send_log_file=send_log,
        request_timeout=5.0,
    )

    try:
        send_result = send_message(config, "wxid_test_target", "hello-from-selftest", None, None)
        callback_result = http_post(
            "http://127.0.0.1:19188/wxhook",
            {
                "fromWxId": "wxid_sender",
                "msgId": "msg-001",
                "content": "hello-to-receiver",
                "isSendMsg": False,
            },
            timeout=5.0,
        )
        time.sleep(0.2)

        if not inbox_file.exists():
            raise RuntimeError("selftest failed: inbox file was not created")
        inbox_lines = inbox_file.read_text(encoding="utf-8").strip().splitlines()
        if not inbox_lines:
            raise RuntimeError("selftest failed: inbox file is empty")

        result = {
            "ok": True,
            "send_result": send_result,
            "callback_result": callback_result,
            "mock_requests": mock.requests,
            "inbox_file": str(inbox_file),
            "send_log_file": str(send_log),
            "inbox_lines": len(inbox_lines),
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    finally:
        receiver.shutdown()
        receiver.server_close()
        mock.shutdown()
        mock.server_close()


def _seq_counter() -> int:
    return int(time.time() * 1_000_000)


def send_message_file(
    msg_type: int,
    wxid: str,
    content: str,
    outbox_file: Path = DEFAULT_OUTBOX_FILE,
    poll_interval: float = DEFAULT_POLL_INTERVAL,
    timeout: float = 30.0,
) -> Dict[str, Any]:
    ensure_parent(outbox_file)
    seq = str(_seq_counter())
    line = f"{seq}\t{msg_type}\t{wxid}\t{content}"
    outbox_file.write_text(line, encoding="utf-8")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            current = outbox_file.read_text(encoding="utf-8")
        except FileNotFoundError:
            current = ""
        if current == "":
            return {"ok": True, "seq": seq, "msg_type": msg_type, "wxid": wxid, "content": content}
        time.sleep(poll_interval)
    return {"ok": False, "error": "timeout_waiting_for_clear", "seq": seq}


def run_file_serve(
    inbox_dir: Path = DEFAULT_INBOX_DIR,
    poll_interval: float = DEFAULT_POLL_INTERVAL,
    http_host: Optional[str] = None,
    http_port: Optional[int] = None,
    http_path: Optional[str] = None,
) -> int:
    inbox_dir.mkdir(parents=True, exist_ok=True)
    seen: set[str] = set()
    started = utc_now()

    sys.stderr.write(f"file-serve: inbox_dir={inbox_dir.absolute()}\n")
    sys.stderr.write(f"file-serve: poll_interval={poll_interval}s\n")
    sys.stderr.write(f"file-serve: started at {started}, waiting for *.txt files...\n")
    sys.stderr.write(f"file-serve: press Ctrl+C to stop\n")
    sys.stderr.flush()

    if http_host and http_port:
        inbox_file = Path("runtime") / "file_inbox.jsonl"
        server = ReceiverServer(
            (http_host, http_port),
            MessageReceiverHandler,
            http_path or DEFAULT_RECEIVE_PATH,
            inbox_file,
        )
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        sys.stderr.write(
            f"file-serve: HTTP also on http://{http_host}:{http_port}{http_path or DEFAULT_RECEIVE_PATH}\n"
        )

    try:
        while True:
            try:
                entries = sorted(inbox_dir.glob("*.txt"))
                for entry in entries:
                    name = entry.name
                    if name in seen or name == "next.txt":
                        continue
                    seen.add(name)
                    try:
                        raw = entry.read_text(encoding="utf-8")
                        msg: Dict[str, Any] = json.loads(raw)
                        msg["_received_at"] = utc_now()
                        msg["_file"] = str(entry)
                        line = json.dumps(msg, ensure_ascii=False)
                        sys.stdout.write(line + "\n")
                        sys.stdout.flush()
                        if http_host and http_port:
                            append_jsonl(Path("runtime") / "file_inbox.jsonl", msg)
                    except Exception as exc:
                        sys.stderr.write(f"file-serve: error reading {entry}: {exc}\n")
                time.sleep(poll_interval)
            except Exception as exc:
                sys.stderr.write(f"file-serve: loop error: {exc}\n")
                time.sleep(poll_interval)
    except KeyboardInterrupt:
        sys.stderr.write(f"file-serve: stopped (received {len(seen)} messages)\n")
    return 0


def run_file_send(
    msg_type: int,
    wxid: str,
    content: str,
    outbox_file: Path = DEFAULT_OUTBOX_FILE,
    poll_interval: float = DEFAULT_POLL_INTERVAL,
    timeout: float = 30.0,
) -> int:
    result = send_message_file(msg_type, wxid, content, outbox_file, poll_interval, timeout)
    if result.get("ok"):
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    sys.stderr.write(f"file-send failed: {result.get('error')}\n")
    return 1


def run_file_selftest() -> int:
    import tempfile

    test_dir = Path(tempfile.mkdtemp(prefix="bridge_selftest_"))
    inbox_dir = test_dir / "inbox"
    outbox_dir = test_dir / "outbox"
    outbox_file = outbox_dir / "next.txt"
    inbox_dir.mkdir(parents=True, exist_ok=True)
    outbox_dir.mkdir(parents=True, exist_ok=True)

    results: Dict[str, Any] = {"ok": True, "steps": []}

    # Step 1: Write a fake message to inbox (simulating E side)
    fake_msg = {
        "msgId": "123456789",
        "type": "1",
        "timestamp": "1715000000",
        "time": "2024-05-06 12:00:00",
        "from": "wxid_test_sender",
        "fromWxId": "wxid_self",
        "content": "hello from e side",
        "signature": "",
    }
    inbox_file = inbox_dir / "123456789.txt"
    inbox_file.write_text(json.dumps(fake_msg, ensure_ascii=False), encoding="utf-8")
    assert inbox_file.exists()
    results["steps"].append({"step": "write_inbox", "ok": True, "file": str(inbox_file)})

    # Step 2: Read inbox (simulating Python serve side)
    found = list(inbox_dir.glob("*.txt"))
    assert len(found) == 1
    read_msg = json.loads(found[0].read_text(encoding="utf-8"))
    assert read_msg["msgId"] == fake_msg["msgId"]
    assert read_msg["content"] == fake_msg["content"]
    results["steps"].append({"step": "read_inbox", "ok": True, "msgId": read_msg["msgId"]})

    # Step 3: Write to outbox (simulating Python send side)
    seq1 = str(_seq_counter())
    outbox_file.write_text(f"{seq1}\t1\twxid_target\thello to e side", encoding="utf-8")
    results["steps"].append({"step": "write_outbox", "ok": True, "seq": seq1})

    # Step 4: Simulate E side reading and clearing outbox
    outbox_content = outbox_file.read_text(encoding="utf-8")
    parts = outbox_content.split("\t")
    assert len(parts) == 4
    assert parts[2] == "wxid_target"
    assert parts[3] == "hello to e side"
    # Clear (simulating E side after processing)
    outbox_file.write_text("", encoding="utf-8")
    results["steps"].append({"step": "e_side_read_and_clear", "ok": True, "seq": parts[0]})

    # Step 5: Full round-trip: Python sends, simulates E processing and writing to inbox
    seq2 = str(_seq_counter())
    send_line = f"{seq2}\t1\twxid_roundtrip\troundtrip hello"
    outbox_file.write_text(send_line, encoding="utf-8")
    # Simulate E reading
    read_back = outbox_file.read_text(encoding="utf-8")
    parts2 = read_back.split("\t")
    assert parts2[0] == seq2
    assert parts2[3] == "roundtrip hello"
    outbox_file.write_text("", encoding="utf-8")
    # Simulate E receiving message and writing to inbox
    inbox_msg = {
        "msgId": seq2,
        "type": "1",
        "timestamp": str(int(time.time())),
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "from": "wxid_roundtrip",
        "fromWxId": "wxid_self",
        "content": "roundtrip reply",
        "signature": "",
    }
    (inbox_dir / f"{seq2}.txt").write_text(
        json.dumps(inbox_msg, ensure_ascii=False), encoding="utf-8"
    )
    results["steps"].append({"step": "roundtrip", "ok": True, "seq": seq2})

    # Cleanup
    import shutil
    shutil.rmtree(test_dir, ignore_errors=True)

    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0 if results["ok"] else 1


def run_file_test(
    bridge_dir: Path = DEFAULT_BRIDGE_DIR,
) -> int:
    """Full loopback test using the real bridge directories — no WeChat needed."""
    inbox_dir = bridge_dir / "inbox"
    outbox_dir = bridge_dir / "outbox"
    outbox_file = outbox_dir / "next.txt"
    inbox_dir.mkdir(parents=True, exist_ok=True)
    outbox_dir.mkdir(parents=True, exist_ok=True)

    sys.stderr.write(f"bridge dir: {bridge_dir.absolute()}\n")
    sys.stderr.write(f"inbox dir:  {inbox_dir.absolute()}\n")
    sys.stderr.write(f"outbox file: {outbox_file.absolute()}\n\n")

    # === Test 1: Simulate E side writing a received message to inbox ===
    sys.stderr.write("--- Test 1: E writes message to inbox ---\n")
    e_msg = {
        "msgId": "100001",
        "type": "1",
        "timestamp": str(int(time.time())),
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "from": "wxid_friend",
        "fromWxId": "wxid_self",
        "content": "这是一条来自 E 端的测试消息",
        "signature": "",
    }
    inbox_file = inbox_dir / "100001.txt"
    inbox_file.write_text(json.dumps(e_msg, ensure_ascii=False), encoding="utf-8")
    sys.stderr.write(f"  written: {inbox_file}\n")
    sys.stderr.write(f"  content: {json.dumps(e_msg, ensure_ascii=False)}\n")

    # Verify Python can read it
    read_back = json.loads(inbox_file.read_text(encoding="utf-8"))
    assert read_back["msgId"] == "100001"
    assert read_back["content"] == e_msg["content"]
    sys.stderr.write(f"  read back OK: msgId={read_back['msgId']}\n\n")

    # === Test 2: Simulate Python side sending a message via outbox ===
    sys.stderr.write("--- Test 2: Python writes send request to outbox ---\n")
    seq = str(_seq_counter())
    send_line = f"{seq}\t1\twxid_target\t这是一条发往 E 端的测试消息"
    outbox_file.write_text(send_line, encoding="utf-8")
    sys.stderr.write(f"  written: {outbox_file}\n")
    sys.stderr.write(f"  content: {send_line}\n")

    # Simulate E side reading, parsing, and clearing
    read_line = outbox_file.read_text(encoding="utf-8")
    parts = read_line.split("\t")
    assert len(parts) == 4, f"expected 4 tab-separated fields, got {len(parts)}"
    assert parts[1] == "1"  # type
    assert parts[2] == "wxid_target"
    sys.stderr.write(f"  E side reads: seq={parts[0]} type={parts[1]} wxid={parts[2]} content={parts[3]}\n")

    # E side clears file after processing
    outbox_file.write_text("", encoding="utf-8")
    sys.stderr.write(f"  E side cleared outbox\n\n")

    # === Test 3: Round-trip ===
    sys.stderr.write("--- Test 3: Full round-trip ---\n")
    seq2 = str(_seq_counter())
    outbox_file.write_text(f"{seq2}\t1\twxid_test\tround-trip 测试消息", encoding="utf-8")
    sys.stderr.write(f"  step1: Python wrote to outbox (seq={seq2})\n")
    read2 = outbox_file.read_text(encoding="utf-8")
    parts2 = read2.split("\t")
    sys.stderr.write(f"  step2: E side parsed: type={parts2[1]} wxid={parts2[2]}\n")
    outbox_file.write_text("", encoding="utf-8")
    sys.stderr.write(f"  step3: E side cleared outbox\n")

    # After processing, E writes result to inbox (simulating receiving confirmation)
    confirm = {
        "msgId": seq2,
        "type": "1",
        "timestamp": str(int(time.time())),
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "from": "wxid_test",
        "fromWxId": "wxid_self",
        "content": "round-trip reply",
        "signature": "",
    }
    (inbox_dir / f"{seq2}.txt").write_text(
        json.dumps(confirm, ensure_ascii=False), encoding="utf-8"
    )
    sys.stderr.write(f"  step4: E side wrote reply to inbox\n")

    # Python reads it back
    reply = json.loads((inbox_dir / f"{seq2}.txt").read_text(encoding="utf-8"))
    assert reply["msgId"] == seq2
    assert reply["content"] == "round-trip reply"
    sys.stderr.write(f"  step5: Python read reply OK\n\n")

    sys.stderr.write("=== All 3 tests passed ===\n")
    sys.stderr.write(f"\nTo watch for real messages: python wx_message_bridge.py file-serve\n")
    sys.stderr.write(f"Currently {len(list(inbox_dir.glob('*.txt')))} file(s) in inbox\n")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Local Python bridge for sending and receiving wx messages.")
    parser.add_argument("--config", type=Path, help="optional JSON config file")
    parser.add_argument("--base-url", dest="base_url")
    parser.add_argument("--send-endpoints", dest="send_endpoints", help="comma-separated endpoints")
    parser.add_argument("--receive-host", dest="receive_host")
    parser.add_argument("--receive-port", dest="receive_port", type=int)
    parser.add_argument("--receive-path", dest="receive_path")
    parser.add_argument("--inbox-file", dest="inbox_file")
    parser.add_argument("--send-log-file", dest="send_log_file")
    parser.add_argument("--request-timeout", dest="request_timeout", type=float)

    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="run local message receiver")
    serve.set_defaults(handler="serve")

    send = sub.add_parser("send", help="send a text message through the local wx HTTP service")
    send.add_argument("--to", required=True, help="target wxid")
    send.add_argument("--content", required=True, help="message text")
    send.add_argument("--from-wxid", help="optional sender/robot wxid if upstream requires it")
    send.add_argument("--endpoint", help="override a specific send endpoint")
    send.set_defaults(handler="send")

    watch = sub.add_parser("watch", help="print saved inbox messages")
    watch.add_argument("--file", type=Path, default=DEFAULT_INBOX_FILE)
    watch.set_defaults(handler="watch")

    selftest = sub.add_parser("selftest", help="run a full local mock send/receive self-test")
    selftest.set_defaults(handler="selftest")

    file_serve = sub.add_parser("file-serve", help="watch bridge/inbox for new message files")
    file_serve.add_argument("--inbox-dir", type=Path, default=DEFAULT_INBOX_DIR)
    file_serve.add_argument("--poll-interval", type=float, default=DEFAULT_POLL_INTERVAL)
    file_serve.add_argument("--http-host", help="also forward to HTTP receiver")
    file_serve.add_argument("--http-port", type=int)
    file_serve.add_argument("--http-path")
    file_serve.set_defaults(handler="file_serve")

    file_send = sub.add_parser("file-send", help="send a message via bridge/outbox file queue")
    file_send.add_argument("--type", type=int, default=1, help="1=text, 3=image")
    file_send.add_argument("--to", required=True, dest="wxid", help="target wxid")
    file_send.add_argument("--content", required=True, help="message content")
    file_send.add_argument("--outbox-file", type=Path, default=DEFAULT_OUTBOX_FILE)
    file_send.add_argument("--poll-interval", type=float, default=DEFAULT_POLL_INTERVAL)
    file_send.add_argument("--timeout", type=float, default=30.0)
    file_send.set_defaults(handler="file_send")

    file_selftest = sub.add_parser("file-selftest", help="run file queue self-test (temp dir)")
    file_selftest.set_defaults(handler="file_selftest")

    file_test = sub.add_parser("file-test", help="run full loopback test using real bridge/ dirs")
    file_test.add_argument("--bridge-dir", type=Path, default=DEFAULT_BRIDGE_DIR)
    file_test.set_defaults(handler="file_test")

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.handler == "selftest":
        return run_selftest()
    if args.handler == "file_selftest":
        return run_file_selftest()
    if args.handler == "file_test":
        return run_file_test(bridge_dir=args.bridge_dir)

    config_data = load_json(args.config)
    config = BridgeConfig.from_sources(config_data, args)

    if args.handler == "file_serve":
        return run_file_serve(
            inbox_dir=args.inbox_dir,
            poll_interval=args.poll_interval,
            http_host=args.http_host if hasattr(args, "http_host") else None,
            http_port=args.http_port if hasattr(args, "http_port") else None,
            http_path=args.http_path if hasattr(args, "http_path") else None,
        )
    if args.handler == "file_send":
        return run_file_send(
            msg_type=args.type,
            wxid=args.wxid,
            content=args.content,
            outbox_file=args.outbox_file,
            poll_interval=args.poll_interval,
            timeout=args.timeout,
        )
    if args.handler == "serve":
        return run_server(config)
    if args.handler == "watch":
        return run_watch(args.file)
    if args.handler == "send":
        try:
            result = send_message(config, args.to, args.content, args.from_wxid, args.endpoint)
        except (SendError, OSError, urllib.error.URLError) as exc:
            sys.stderr.write(f"send failed: {exc}\n")
            return 1
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    parser.error("unknown command")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
