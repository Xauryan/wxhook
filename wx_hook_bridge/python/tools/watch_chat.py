#!/usr/bin/env python3
"""Print native WeChat Hook events retained by the local panel, read-only."""
from __future__ import annotations

import argparse
import html
import json
import math
import sqlite3
import sys
import time
import xml.etree.ElementTree as ET
from contextlib import closing
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATABASE = PROJECT_ROOT / "wechat-db-web-panel/.cache/live_chat.sqlite"
MESSAGE_TYPES = {
    1: "文本", 3: "图片", 34: "语音", 42: "名片", 43: "视频",
    47: "表情", 48: "位置", 49: "应用消息", 10000: "系统消息",
    10002: "系统事件",
}


def console_text(value: object) -> str:
    """Keep Unicode and line breaks, but do not execute terminal controls."""
    text = str(value if value is not None else "")
    return "".join(
        char if char in "\n\t" or (ord(char) >= 32 and not 127 <= ord(char) < 160)
        else f"\\x{ord(char):02x}" for char in text
    )


def truthy(value: object) -> bool:
    return value is True or value == 1 or str(value).lower() == "true"


def direction(payload: dict) -> str:
    explicit = str(payload.get("direction") or "").lower()
    if explicit in {"incoming", "outgoing"}:
        return explicit
    if truthy(payload.get("is_incoming", payload.get("isIncoming"))):
        return "incoming"
    if truthy(payload.get("is_outgoing", payload.get("isSendMsg"))):
        return "outgoing"
    return "unknown"


def timestamp(payload: dict) -> str:
    value = payload.get("timestamp_unix", payload.get("timestamp"))
    try:
        return datetime.fromtimestamp(float(value)).astimezone().strftime("%Y-%m-%d %H:%M:%S %z")
    except (TypeError, ValueError, OverflowError, OSError):
        return "未知时间"


def display_name(db: sqlite3.Connection, account: str, username: str, names_available: bool) -> str:
    if not username:
        return "未知"
    if username == "filehelper":
        return "文件传输助手 (filehelper)"
    if names_available and account:
        row = db.execute(
            "SELECT name FROM live_names WHERE account=? AND username=?",
            (account, username),
        ).fetchone()
        if row and row[0] and row[0] != username:
            return console_text(f"{row[0]} ({username})")
    return console_text(username)


def nonnegative_number(value: object) -> float | None:
    try:
        number = float(value)
        return number if math.isfinite(number) and number >= 0 else None
    except (TypeError, ValueError, OverflowError):
        return None


def file_size(value: object) -> str:
    number = nonnegative_number(value)
    if number is None:
        return ""
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if number < 1024 or unit == "TiB":
            return f"{number:g} B" if unit == "B" else f"{number:.1f} {unit}"
        number /= 1024
    return ""


def xml_text(root: ET.Element, path: str) -> str:
    node = root.find(path)
    return "".join(node.itertext()).strip() if node is not None else ""


def message_body(content: str, kind: object, *, raw_content: bool = False, depth: int = 0) -> str:
    """Present typed XML as readable fields without fetching/decrypting media."""
    if not content:
        return "[无文本正文]"
    try:
        message_type = int(kind)
    except (TypeError, ValueError):
        return content
    base_type, subtype = message_type & 0xFFFFFFFF, (message_type & 0xFFFFFFFFFFFFFFFF) >> 32
    # Literal text (including markup and entity spellings) must stay lossless.
    if raw_content or base_type == 1 or depth > 3:
        return content
    candidate = content.strip()
    if candidate.startswith("&lt;"):
        candidate = html.unescape(candidate)
    if not candidate.startswith("<"):
        return content
    try:
        if "<!DOCTYPE" in candidate.upper() or "<!ENTITY" in candidate.upper():
            raise ET.ParseError("XML declarations are not supported")
        root = ET.fromstring(candidate)
    except (ET.ParseError, ValueError):
        return f"[XML 解析失败，保留原始正文]\n{content}"

    media_types = {3: ("img", "图片"), 34: ("voicemsg", "语音"),
                   43: ("videomsg", "视频"), 47: ("emoji", "表情")}
    if base_type in media_types:
        tag, label = media_types[base_type]
        node = next(root.iter(tag), None)
        if node is None:
            return content
        details = []
        duration = nonnegative_number(node.get("voicelength" if base_type == 34 else "playlength"))
        if base_type in {34, 43} and duration is not None:
            details.append(f"时长: {duration / 1000 if base_type == 34 else duration:g} 秒")
        size = next((node.get(key) for key in ("length", "cdnmidimglength", "cdnbigimglength")
                     if node.get(key)), None)
        if size is not None and (rendered_size := file_size(size)):
            details.append(f"大小: {rendered_size}")
        body = f"[{label}]" + (" " + " | ".join(details) if details else "")
        width, height = node.get("cdnthumbwidth"), node.get("cdnthumbheight")
        if width and height:
            body += f"\n缩略图: {width}×{height}"
        return body
    if base_type == 42:
        node = next((node for node in root.iter() if node.get("nickname") or node.get("username")), None)
        if node is not None:
            return f"[名片] {node.get('nickname') or node.get('username')}" + (
                f" ({node.get('username')})" if node.get("nickname") and node.get("username") else "")
    elif base_type == 48:
        node = next(root.iter("location"), None)
        if node is not None:
            places = list(dict.fromkeys(value for key in ("poiname", "label") if (value := node.get(key))))
            body = "[位置]" + (" " + " | ".join(places) if places else "")
            if node.get("x") and node.get("y"):
                body += f"\n坐标: {node.get('x')}, {node.get('y')}"
            return body
    elif base_type == 49:
        app = next(root.iter("appmsg"), None)
        if app is not None:
            app_type = xml_text(app, "type") or str(subtype)
            labels = {"3": "音乐", "5": "链接", "6": "文件", "19": "合并聊天记录",
                      "24": "收藏", "33": "小程序", "36": "小程序", "57": "引用回复"}
            label = labels.get(app_type, f"应用消息 type={app_type}")
            title = xml_text(app, "appattach/filename") or xml_text(app, "title")
            lines = [f"[{label}]" + (f" {title}" if title else "")]
            description = xml_text(app, "des")
            if description and description != title:
                lines.append(description)
            url = xml_text(app, "url")
            if url:
                lines.append(f"链接: {url}")
            if app_type == "6" and (size := file_size(xml_text(app, "appattach/totallen"))):
                lines.append(f"大小: {size}")
            refer = app.find("refermsg")
            if refer is not None:
                author = xml_text(refer, "displayname") or xml_text(refer, "fromusr") or "未知"
                quoted = message_body(xml_text(refer, "content"), xml_text(refer, "type") or 1, depth=depth + 1)
                lines.append(f"引用 {author}: {quoted}")
            return "\n".join(lines)
    elif base_type in {10000, 10002}:
        for path in (".//replacemsg", ".//plain", ".//content"):
            if text := xml_text(root, path):
                return text
    # Unrecognized envelopes remain visible instead of silently losing fields.
    return content


def format_message(db: sqlite3.Connection, payload: dict, account: str, names_available: bool,
                   *, raw_content: bool = False) -> str:
    peer = str(payload.get("peer_id") or payload.get("peerWxId") or "")
    room = str(payload.get("room_id") or payload.get("roomId") or "")
    sender = str(payload.get("sender_id") or payload.get("senderWxId") or "")
    account = str(payload.get("self_id") or payload.get("selfWxId") or account)
    content = str(payload.get("content") or "")
    if room and sender and content.startswith(sender + ":\n"):
        content = content[len(sender) + 2:]
    kind = payload.get("message_type", payload.get("type", "?"))
    try:
        label = MESSAGE_TYPES.get(int(kind) & 0xFFFFFFFF, f"类型 {kind}")
    except (TypeError, ValueError):
        label = f"类型 {kind}"
    action = "收到" if direction(payload) == "incoming" else "发出"
    session = display_name(db, account, room or peer, names_available)
    author = display_name(db, account, sender, names_available)
    message_id = payload.get("message_id") or payload.get("msgId") or payload.get("event_id") or "未知"
    truncated = " [Hook 正文已截断]" if truthy(payload.get("content_truncated")) else ""
    return (
        f"[{timestamp(payload)}] {action} · {'群聊' if room else '私聊'} · {console_text(label)}{truncated}\n"
        f"会话: {session}\n"
        f"发送者: {author} | 账号: {console_text(account)} | 消息ID: {console_text(message_id)}\n"
        f"正文: {console_text(message_body(content, kind, raw_content=raw_content))}\n"
        + "-" * 72
    )


def positive_seconds(value: str) -> float:
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("must be a finite number greater than zero")
    return number


def watch(database: Path, *, from_start: bool, include_outgoing: bool,
          poll_interval: float, duration: float | None, raw_content: bool = False) -> int:
    # mode=ro fails closed instead of silently creating an empty ledger.
    with closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True, timeout=1)) as db:
        db.execute("PRAGMA query_only=ON")
        cursor = 0 if from_start else db.execute("SELECT coalesce(max(sequence),0) FROM live_messages").fetchone()[0]
        names_available = bool(db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='live_names'"
        ).fetchone())
        print(f"监听原生 Hook 消息: {database.resolve()}", file=sys.stderr, flush=True)
        print("默认只打印收到的聊天消息；Ctrl+C 停止。", file=sys.stderr, flush=True)
        started, printed = time.monotonic(), 0
        while duration is None or time.monotonic() - started < duration:
            try:
                rows = db.execute(
                    "SELECT sequence,raw,account FROM live_messages WHERE sequence>? ORDER BY sequence LIMIT 256",
                    (cursor,),
                ).fetchall()
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc).lower() and "busy" not in str(exc).lower():
                    raise
                print(f"消息账本暂忙，稍后重试: {exc}", file=sys.stderr, flush=True)
                time.sleep(poll_interval)
                continue
            for sequence, raw, account in rows:
                try:
                    payload = json.loads(raw)
                    if not isinstance(payload, dict):
                        raise ValueError("event payload is not an object")
                    if payload.get("source") != "wx_hook_bridge" or payload.get("event_type") != "message":
                        cursor = sequence
                        continue
                    flow = direction(payload)
                    if flow != "incoming" and not (include_outgoing and flow == "outgoing"):
                        cursor = sequence
                        continue
                    rendered = format_message(db, payload, account, names_available, raw_content=raw_content)
                except (ValueError, TypeError) as exc:
                    print(f"跳过无法解析的 Hook 事件 sequence={sequence}: {exc}", file=sys.stderr, flush=True)
                    cursor = sequence
                    continue
                print(rendered, flush=True)
                printed += 1
                cursor = sequence
            if not rows:
                remaining = poll_interval if duration is None else min(poll_interval, max(0, duration - (time.monotonic() - started)))
                time.sleep(remaining)
        print(f"监听结束，共打印 {printed} 条聊天消息。", file=sys.stderr, flush=True)
        return 0


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="实时打印本地桥接收到的微信聊天消息（只读）。")
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE, help="面板的 live_chat.sqlite 路径")
    parser.add_argument("--from-start", action="store_true", help="先打印账本中已有的 Hook 消息，再继续监听")
    parser.add_argument("--include-outgoing", action="store_true", help="也打印 Hook 已捕获的发出消息")
    parser.add_argument("--raw-content", action="store_true", help="显示 Hook 原始正文/XML，默认解析为可读内容")
    parser.add_argument("--poll-interval", type=positive_seconds, default=.5, help="检查新事件的间隔秒数，默认 0.5")
    parser.add_argument("--duration", type=positive_seconds, help="监听指定秒数后退出，默认持续运行")
    args = parser.parse_args(argv)
    try:
        return watch(args.database, from_start=args.from_start, include_outgoing=args.include_outgoing,
                     poll_interval=args.poll_interval, duration=args.duration, raw_content=args.raw_content)
    except KeyboardInterrupt:
        print("\n已停止监听。", file=sys.stderr, flush=True)
        return 0
    except (sqlite3.Error, OSError) as exc:
        print(f"无法读取 Hook 消息账本: {exc}\n请先登录微信并启动 inject_bridge.py 和面板后端。", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
