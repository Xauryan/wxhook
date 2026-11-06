from __future__ import annotations

import asyncio
import html
import json
import logging
import os
import re
import sqlite3
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlsplit
from xml.etree import ElementTree

import httpx

from .config import Settings
from .fmessage_source import FMessageBatch, FMessageSource


logger = logging.getLogger(__name__)
SYSTEM_CONTACTS = {
    "fmessage",
    "filehelper",
    "medianote",
    "notifymessage",
    "weixin",
}


class FriendAutomationError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class RoomMemberSnapshot:
    members: tuple[dict[str, Any], ...]
    count: int


class BridgeClient(Protocol):
    async def set_auto_accept(self, enabled: bool) -> dict[str, Any]: ...

    async def accept_friend(
        self,
        encrypt_username: str,
        ticket: str,
        scene: int,
        friend_flag: int,
    ) -> dict[str, Any]: ...

    async def contacts(self) -> list[dict[str, Any]]: ...

    async def send_text(self, wxid: str, content: str) -> None: ...

    async def send_at_text(
        self,
        room_id: str,
        wxids: str,
        nickname: str,
        content: str,
    ) -> None: ...

    async def room_members(
        self, room_id: str
    ) -> RoomMemberSnapshot | list[dict[str, Any]] | dict[str, Any]: ...

    async def add_room_member(self, room_id: str, wxid: str) -> None: ...

    async def invite_room_member(self, room_id: str, wxid: str) -> None: ...

    async def remove_room_member(self, room_id: str, wxid: str) -> None: ...

    async def close(self) -> None: ...


class _AutomationLeaderLock:
    def __init__(self, path: Path) -> None:
        self.path = path.resolve()
        self._handle: Any | None = None

    def acquire(self) -> bool:
        if self._handle is not None:
            return True
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+b")
        if handle.seek(0, os.SEEK_END) == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl_module: Any = fcntl
                fcntl_module.flock(
                    handle.fileno(), fcntl_module.LOCK_EX | fcntl_module.LOCK_NB
                )
        except OSError:
            handle.close()
            return False
        self._handle = handle
        return True

    def release(self) -> None:
        handle = self._handle
        if handle is None:
            return
        try:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl_module: Any = fcntl
                fcntl_module.flock(handle.fileno(), fcntl_module.LOCK_UN)
        finally:
            handle.close()
            self._handle = None


def _now_ms() -> int:
    return time.time_ns() // 1_000_000


def _first(payload: dict[str, Any], *names: str) -> Any:
    nested = payload.get("payload")
    sources = (payload, nested) if isinstance(nested, dict) else (payload,)
    for source in sources:
        for name in names:
            value = source.get(name)
            if value is not None and value != "":
                return value
    return None


def _intish(value: Any) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _boolish(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value or "").strip().casefold() in {"1", "true", "yes", "on"}


def _valid_contact_id(value: str, encrypt_username: str = "") -> bool:
    normalized = value.strip()
    if not normalized or normalized == encrypt_username:
        return False
    if normalized.casefold() in SYSTEM_CONTACTS:
        return False
    if normalized.startswith("v3_") or normalized.endswith("@stranger"):
        return False
    return not normalized.endswith("@chatroom")


def _contact_state(item: dict[str, Any]) -> int | None:
    raw = item.get("state")
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw
    text = str(raw).strip()
    if not text:
        return None
    try:
        return int(text, 16)
    except ValueError:
        return None


def _is_established_friend(item: dict[str, Any]) -> bool:
    if str(item.get("category") or "").strip().casefold() != "friend":
        return False
    state = _contact_state(item)
    return state is None or (state & 1) != 0


def _contact_relationship_rank(item: dict[str, Any]) -> int:
    state = _contact_state(item)
    category = str(item.get("category") or "").strip().casefold()
    if state is not None and (state & 1) != 0:
        return 4 if category == "friend" else 3
    if state is None and category == "friend":
        return 2
    if state == 2 or category == "deleted":
        return 0
    return 1


def _merge_contact_rows(
    current: dict[str, Any], candidate: dict[str, Any]
) -> dict[str, Any]:
    if _contact_relationship_rank(candidate) > _contact_relationship_rank(current):
        preferred, fallback = candidate, current
    else:
        preferred, fallback = current, candidate
    merged = dict(fallback)
    merged.update(
        {
            key: value
            for key, value in preferred.items()
            if value is not None and value != ""
        }
    )
    return merged


def _index_contacts(
    contacts: list[dict[str, Any]], key_name: str
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for item in contacts:
        key = str(item.get(key_name) or "").strip()
        if not key:
            continue
        current = result.get(key)
        result[key] = item if current is None else _merge_contact_rows(current, item)
    return result


def _xml_attributes(content: str) -> dict[str, str]:
    text = content.strip()
    if not text:
        return {}
    start = text.find("<msg")
    if start > 0:
        text = text[start:]
    try:
        root = ElementTree.fromstring(text)
    except ElementTree.ParseError:
        match = re.search(r"<msg\b(?P<attrs>[^>]*)>", text, re.IGNORECASE | re.DOTALL)
        if not match:
            match = re.search(
                r"<msg\b(?P<attrs>[^>]*)/>", text, re.IGNORECASE | re.DOTALL
            )
        if not match:
            return {}
        pairs = re.findall(
            r"([A-Za-z_][\w:.-]*)\s*=\s*(?:\\?[\"'])(.*?)(?:\\?[\"'])",
            match.group("attrs"),
            re.DOTALL,
        )
        return {name.casefold(): html.unescape(value) for name, value in pairs}
    for element in root.iter():
        tag = str(element.tag).rsplit("}", 1)[-1].casefold()
        if tag == "msg":
            return {
                str(name).rsplit("}", 1)[-1].casefold(): str(value)
                for name, value in element.attrib.items()
            }
    return {}


def parse_friend_request(content: str) -> dict[str, Any] | None:
    attributes = _xml_attributes(content)
    encrypt_username = (
        attributes.get("encryptusername") or attributes.get("username") or ""
    ).strip()
    if not encrypt_username:
        return None
    candidate = ""
    for name in ("fromusername", "fromuser", "username"):
        value = str(attributes.get(name) or "").strip()
        if _valid_contact_id(value, encrypt_username):
            candidate = value
            break
    ticket = (
        attributes.get("ticket") or attributes.get("antispamticket") or ""
    ).strip()
    scene = _intish(attributes.get("scene"))
    friend_flag = _intish(attributes.get("friendflag"))
    return {
        "encrypt_username": encrypt_username,
        "candidate_username": candidate,
        "ticket": ticket,
        "scene": scene,
        "friend_flag": max(0, friend_flag or 0),
    }


def event_friend_request(event: dict[str, Any]) -> dict[str, Any] | None:
    payload = event.get("payload")
    if not isinstance(payload, dict):
        return None
    message_type = _intish(_first(payload, "message_type", "type", "msg_type"))
    incoming = (
        _boolish(_first(payload, "is_incoming", "isIncoming", "is_recv_msg"))
        or str(_first(payload, "direction") or "").casefold() == "incoming"
    )
    if message_type != 37 or not incoming:
        return None
    encrypt_username = str(
        _first(
            payload,
            "encrypt_username",
            "encryptUsername",
            "encryptusername",
            "v3",
        )
        or ""
    ).strip()
    if encrypt_username:
        candidate_username = str(
            _first(payload, "candidate_username", "candidateUsername", "user_name")
            or ""
        ).strip()
        if not _valid_contact_id(candidate_username, encrypt_username):
            candidate_username = ""
        direct_result: dict[str, Any] = {
            "encrypt_username": encrypt_username,
            "candidate_username": candidate_username,
            "ticket": str(
                _first(payload, "ticket", "antiSpamTicket", "antispamticket", "v4")
                or ""
            ).strip(),
            "scene": _intish(_first(payload, "scene")),
            "friend_flag": max(
                0,
                _intish(_first(payload, "friend_flag", "friendFlag", "friendflag"))
                or 0,
            ),
            "accept_required": _boolish(_first(payload, "accept_required"))
            or str(_first(payload, "source") or "").casefold()
            == "fmessage_table",
            "source_rowid": _intish(_first(payload, "fmessage_rowid")),
        }
        return direct_result
    result = parse_friend_request(str(_first(payload, "content") or ""))
    if result is not None:
        result["accept_required"] = _boolish(_first(payload, "accept_required"))
        result["source_rowid"] = _intish(_first(payload, "fmessage_rowid"))
    return result


def event_keyword(event: dict[str, Any], keyword: str) -> str | None:
    payload = event.get("payload")
    if not isinstance(payload, dict):
        return None
    message_type = _intish(_first(payload, "message_type", "type", "msg_type"))
    incoming = (
        _boolish(_first(payload, "is_incoming", "isIncoming", "is_recv_msg"))
        or str(_first(payload, "direction") or "").casefold() == "incoming"
    )
    room_id = str(_first(payload, "room_id", "roomId") or "").strip()
    content = str(_first(payload, "content") or "").strip()
    if message_type != 1 or not incoming or room_id or content != keyword:
        return None
    wxid = str(
        _first(
            payload,
            "sender_id",
            "senderWxId",
            "peer_id",
            "peerWxId",
            "raw_from",
            "rawFrom",
        )
        or ""
    ).strip()
    return wxid if _valid_contact_id(wxid) else None


class WeChatBridgeClient:
    def __init__(self, base_url: str) -> None:
        parsed = urlsplit(base_url)
        if (
            parsed.scheme != "http"
            or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise ValueError("friend automation bridge URL must be loopback HTTP")
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=httpx.Timeout(40.0, connect=3.0),
            trust_env=False,
        )

    async def _post(self, path: str, payload: dict[str, Any]) -> Any:
        try:
            response = await self._client.post(path, json=payload)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise FriendAutomationError(
                f"bridge request failed for {path}: {exc}"
            ) from exc
        try:
            response_text = response.content.decode("utf-8")
        except UnicodeDecodeError:
            # Some legacy contact profile fields contain local-codepage bytes.
            # Replacing only those invalid bytes preserves JSON structure and wxids.
            response_text = response.content.decode("utf-8", errors="replace")
        try:
            body = json.loads(response_text)
        except json.JSONDecodeError as exc:
            raise FriendAutomationError(
                f"bridge request failed for {path}: {exc}"
            ) from exc
        code = _intish(body.get("code")) if isinstance(body, dict) else None
        if not isinstance(body, dict) or code != 1:
            message = body.get("msg") if isinstance(body, dict) else "invalid response"
            data = body.get("data") if isinstance(body, dict) else None
            if isinstance(data, dict) and data.get("message"):
                message = data["message"]
            raise FriendAutomationError(f"bridge rejected {path}: {message}")
        data = body.get("data")
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except json.JSONDecodeError:
                pass
        return data

    async def set_auto_accept(self, enabled: bool) -> dict[str, Any]:
        data = await self._post("/api/autoAcceptFriend", {"enable": enabled})
        return data if isinstance(data, dict) else {}

    async def accept_friend(
        self,
        encrypt_username: str,
        ticket: str,
        scene: int,
        friend_flag: int,
    ) -> dict[str, Any]:
        data = await self._post(
            "/api/acceptFriend",
            {
                "encryptUsername": encrypt_username,
                "ticket": ticket,
                "scene": scene,
                "friendFlag": friend_flag,
                "timeout": 15_000,
            },
        )
        if not isinstance(data, dict) or data.get("ok") is not True:
            raise FriendAutomationError("friend acceptance response is malformed")
        return data

    async def contacts(self) -> list[dict[str, Any]]:
        data = await self._post("/api/getContactList", {"limit": 10_000})
        if not isinstance(data, list):
            raise FriendAutomationError("contact list response is not an array")
        contacts = [item for item in data if isinstance(item, dict)]
        return list(_index_contacts(contacts, "wxid").values())

    async def send_text(self, wxid: str, content: str) -> None:
        await self._post("/api/sendTextMsg", {"wxid": wxid, "msg": content})

    async def send_at_text(
        self,
        room_id: str,
        wxids: str,
        nickname: str,
        content: str,
    ) -> None:
        await self._post(
            "/api/sendAtText",
            {
                "chatRoomId": room_id,
                "wxids": wxids,
                "nickname": nickname,
                "msg": content,
            },
        )

    async def room_members(self, room_id: str) -> RoomMemberSnapshot:
        data = await self._post("/api/getMemberFromChatRoom", {"chatRoomId": room_id})
        if not isinstance(data, dict) or not isinstance(data.get("members"), list):
            raise FriendAutomationError("room member response is malformed")
        members = tuple(item for item in data["members"] if isinstance(item, dict))
        count = _intish(data.get("count"))
        if count is None or count < 0:
            raise FriendAutomationError("room member count is malformed")
        unique_ids = {
            str(item.get("wxid") or "").strip()
            for item in members
            if str(item.get("wxid") or "").strip()
        }
        return RoomMemberSnapshot(members=members, count=max(count, len(unique_ids)))

    async def add_room_member(self, room_id: str, wxid: str) -> None:
        await self._post(
            "/api/addMemberToChatRoom",
            {"chatRoomId": room_id, "memberIds": wxid, "timeout": 30_000},
        )

    async def invite_room_member(self, room_id: str, wxid: str) -> None:
        await self._post(
            "/api/InviteMemberToChatRoom",
            {"chatRoomId": room_id, "memberIds": wxid, "timeout": 30_000},
        )

    async def remove_room_member(self, room_id: str, wxid: str) -> None:
        await self._post(
            "/api/delMemberFromChatRoom",
            {"chatRoomId": room_id, "memberIds": wxid, "timeout": 30_000},
        )

    async def close(self) -> None:
        await self._client.aclose()


class FriendAutomationStore:
    def __init__(self, path: Path) -> None:
        self.path = path.resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=5000")
        return connection

    def initialize(
        self,
        enabled: bool,
        initial_cursor: int,
        initial_fmessage_cursor: int | None = None,
    ) -> None:
        now = _now_ms()
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS automation_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS friend_leads (
                    encrypt_username TEXT PRIMARY KEY,
                    request_event_id TEXT NOT NULL,
                    request_sequence INTEGER NOT NULL,
                    candidate_username TEXT NOT NULL DEFAULT '',
                    ticket TEXT NOT NULL DEFAULT '',
                    scene INTEGER,
                    friend_flag INTEGER NOT NULL DEFAULT 0,
                    source_rowid INTEGER,
                    wxid TEXT,
                    state TEXT NOT NULL,
                    room_id TEXT,
                    room_name TEXT,
                    welcome_sent_at_ms INTEGER,
                    group_welcome_state TEXT NOT NULL DEFAULT '',
                    group_welcome_sent_at_ms INTEGER,
                    group_welcome_retry_at_ms INTEGER NOT NULL DEFAULT 0,
                    group_welcome_attempts INTEGER NOT NULL DEFAULT 0,
                    group_welcome_error TEXT,
                    keyword_sequence INTEGER,
                    keyword_received_at_ms INTEGER,
                    retry_at_ms INTEGER NOT NULL DEFAULT 0,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT,
                    created_at_ms INTEGER NOT NULL,
                    updated_at_ms INTEGER NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS friend_leads_wxid
                    ON friend_leads(wxid) WHERE wxid IS NOT NULL;
                CREATE INDEX IF NOT EXISTS friend_leads_state_retry
                    ON friend_leads(state,retry_at_ms,request_sequence);
                CREATE TABLE IF NOT EXISTS friend_keyword_events (
                    wxid TEXT PRIMARY KEY,
                    sequence INTEGER NOT NULL,
                    received_at_ms INTEGER NOT NULL
                );
                """
            )
            columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(friend_leads)")
            }
            if "cleanup_room_id" not in columns:
                connection.execute(
                    "ALTER TABLE friend_leads ADD COLUMN cleanup_room_id TEXT"
                )
            if "cleanup_room_name" not in columns:
                connection.execute(
                    "ALTER TABLE friend_leads ADD COLUMN cleanup_room_name TEXT"
                )
            if "ticket" not in columns:
                connection.execute(
                    "ALTER TABLE friend_leads ADD COLUMN ticket TEXT NOT NULL DEFAULT ''"
                )
            if "scene" not in columns:
                connection.execute("ALTER TABLE friend_leads ADD COLUMN scene INTEGER")
            if "friend_flag" not in columns:
                connection.execute(
                    "ALTER TABLE friend_leads ADD COLUMN friend_flag INTEGER NOT NULL DEFAULT 0"
                )
            if "source_rowid" not in columns:
                connection.execute(
                    "ALTER TABLE friend_leads ADD COLUMN source_rowid INTEGER"
                )
            if "group_welcome_state" not in columns:
                connection.execute(
                    "ALTER TABLE friend_leads ADD COLUMN "
                    "group_welcome_state TEXT NOT NULL DEFAULT ''"
                )
            if "group_welcome_sent_at_ms" not in columns:
                connection.execute(
                    "ALTER TABLE friend_leads ADD COLUMN group_welcome_sent_at_ms INTEGER"
                )
            if "group_welcome_retry_at_ms" not in columns:
                connection.execute(
                    "ALTER TABLE friend_leads ADD COLUMN "
                    "group_welcome_retry_at_ms INTEGER NOT NULL DEFAULT 0"
                )
            if "group_welcome_attempts" not in columns:
                connection.execute(
                    "ALTER TABLE friend_leads ADD COLUMN "
                    "group_welcome_attempts INTEGER NOT NULL DEFAULT 0"
                )
            if "group_welcome_error" not in columns:
                connection.execute(
                    "ALTER TABLE friend_leads ADD COLUMN group_welcome_error TEXT"
                )
            previous_enabled = connection.execute(
                "SELECT value FROM automation_meta WHERE key='enabled'"
            ).fetchone()
            connection.execute(
                "INSERT OR IGNORE INTO automation_meta(key,value) VALUES ('enabled',?)",
                ("1" if enabled else "0",),
            )
            connection.execute(
                "INSERT OR IGNORE INTO automation_meta(key,value) VALUES ('cursor',?)",
                (str(max(0, initial_cursor)),),
            )
            if initial_fmessage_cursor is not None:
                connection.execute(
                    """
                    INSERT OR IGNORE INTO automation_meta(key,value)
                    VALUES ('fmessage_cursor',?)
                    """,
                    (str(max(0, initial_fmessage_cursor)),),
                )
            # Only a fresh store adopts the configured default. Runtime API toggles
            # remain authoritative across restarts and standby processes.
            if previous_enabled is None and not enabled:
                connection.execute(
                    "INSERT OR REPLACE INTO automation_meta(key,value) VALUES ('cursor',?)",
                    (str(max(0, initial_cursor)),),
                )
            connection.execute(
                """
                UPDATE friend_leads
                SET state=CASE state
                    WHEN 'accept_sending' THEN 'accept_verifying'
                    WHEN 'welcome_sending' THEN 'awaiting_keyword'
                    WHEN 'assigning' THEN 'join_pending'
                    WHEN 'add_sending' THEN 'add_pending'
                    WHEN 'invite_sending' THEN 'invite_pending'
                    WHEN 'remove_sending' THEN 'remove_pending'
                    ELSE state END,
                    welcome_sent_at_ms=CASE
                        WHEN state='welcome_sending'
                        THEN COALESCE(welcome_sent_at_ms,updated_at_ms)
                        ELSE welcome_sent_at_ms END,
                    group_welcome_state=CASE
                        WHEN group_welcome_state='sending' THEN 'sent'
                        ELSE group_welcome_state END,
                    group_welcome_sent_at_ms=CASE
                        WHEN group_welcome_state='sending'
                        THEN COALESCE(group_welcome_sent_at_ms,updated_at_ms)
                        ELSE group_welcome_sent_at_ms END,
                    group_welcome_retry_at_ms=CASE
                        WHEN group_welcome_state='sending' THEN 0
                        ELSE group_welcome_retry_at_ms END,
                    group_welcome_attempts=CASE
                        WHEN group_welcome_state='sending' THEN 0
                        ELSE group_welcome_attempts END,
                    group_welcome_error=CASE
                        WHEN group_welcome_state='sending'
                        THEN 'group welcome outcome was uncertain after restart; not resent'
                        ELSE group_welcome_error END,
                    retry_at_ms=CASE
                        WHEN state IN (
                            'accept_sending','add_sending','invite_sending','remove_sending'
                        ) THEN ?
                        ELSE 0 END,
                    last_error=CASE
                        WHEN state='accept_sending'
                        THEN 'friend acceptance outcome was uncertain after restart; verifying contact'
                        WHEN state='welcome_sending'
                        THEN 'welcome outcome was uncertain after restart; not resent'
                        WHEN state='add_sending'
                        THEN 'direct room-add outcome was uncertain after restart; verifying membership'
                        WHEN state='invite_sending'
                        THEN 'room action outcome was uncertain after restart; verifying membership'
                        WHEN state='remove_sending'
                        THEN 'duplicate-room removal outcome was uncertain after restart; verifying membership'
                        ELSE last_error END,
                    updated_at_ms=?
                WHERE state IN (
                    'accept_sending','welcome_sending','assigning','add_sending',
                    'invite_sending','remove_sending'
                ) OR group_welcome_state='sending'
                """,
                (now + 5_000, now),
            )
            connection.commit()

    def enabled(self) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT value FROM automation_meta WHERE key='enabled'"
            ).fetchone()
        return bool(row and row["value"] == "1")

    def set_enabled(self, enabled: bool, cursor: int | None = None) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO automation_meta(key,value) VALUES ('enabled',?)",
                ("1" if enabled else "0",),
            )
            if cursor is not None:
                connection.execute(
                    "INSERT OR REPLACE INTO automation_meta(key,value) VALUES ('cursor',?)",
                    (str(max(0, cursor)),),
                )
            connection.commit()

    def cursor(self) -> int:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT value FROM automation_meta WHERE key='cursor'"
            ).fetchone()
        return int(row["value"]) if row else 0

    def fmessage_cursor(self) -> int | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT value FROM automation_meta WHERE key='fmessage_cursor'"
            ).fetchone()
        return int(row["value"]) if row else None

    def set_fmessage_cursor(self, cursor: int) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO automation_meta(key,value)
                VALUES ('fmessage_cursor',?)
                """,
                (str(max(0, cursor)),),
            )
            connection.commit()

    def clear_fmessage_cursor(self) -> None:
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM automation_meta WHERE key='fmessage_cursor'"
            )
            connection.commit()

    def consume_event(
        self,
        event: dict[str, Any],
        request: dict[str, Any] | None,
        keyword_wxid: str | None,
    ) -> None:
        sequence = int(event["sequence"])
        now = _now_ms()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if request:
                encrypt_username = request["encrypt_username"]
                accept_required = bool(request.get("accept_required"))
                initial_state = "accept_pending" if accept_required else "pending_contact"
                existing = connection.execute(
                    "SELECT state FROM friend_leads WHERE encrypt_username=?",
                    (encrypt_username,),
                ).fetchone()
                if existing is None:
                    connection.execute(
                        """
                        INSERT INTO friend_leads(
                            encrypt_username,request_event_id,request_sequence,
                            candidate_username,ticket,scene,friend_flag,source_rowid,
                            state,retry_at_ms,created_at_ms,updated_at_ms
                        ) VALUES (?,?,?,?,?,?,?,?,?,0,?,?)
                        """,
                        (
                            encrypt_username,
                            str(event.get("event_id") or sequence),
                            sequence,
                            request.get("candidate_username") or "",
                            request.get("ticket") or "",
                            request.get("scene"),
                            max(0, int(request.get("friend_flag") or 0)),
                            request.get("source_rowid"),
                            initial_state,
                            now,
                            now,
                        ),
                    )
                elif str(existing["state"]) in {
                    "accept_pending",
                    "accept_verifying",
                    "pending_contact",
                }:
                    connection.execute(
                        """
                        UPDATE friend_leads
                        SET candidate_username=CASE
                                WHEN candidate_username='' THEN ?
                                ELSE candidate_username END,
                            ticket=CASE WHEN ticket='' THEN ? ELSE ticket END,
                            scene=COALESCE(scene,?),
                            friend_flag=CASE WHEN friend_flag=0 THEN ? ELSE friend_flag END,
                            source_rowid=COALESCE(source_rowid,?),
                            state=CASE
                                WHEN ? AND state='pending_contact' THEN 'accept_pending'
                                ELSE state END,
                            retry_at_ms=MIN(retry_at_ms,?),
                            updated_at_ms=?
                        WHERE encrypt_username=?
                        """,
                        (
                            request.get("candidate_username") or "",
                            request.get("ticket") or "",
                            request.get("scene"),
                            max(0, int(request.get("friend_flag") or 0)),
                            request.get("source_rowid"),
                            int(accept_required),
                            now,
                            now,
                            encrypt_username,
                        ),
                    )
            if keyword_wxid:
                connection.execute(
                    """
                    INSERT INTO friend_keyword_events(wxid,sequence,received_at_ms)
                    VALUES (?,?,?)
                    ON CONFLICT(wxid) DO UPDATE SET
                        sequence=excluded.sequence,received_at_ms=excluded.received_at_ms
                    WHERE excluded.sequence>friend_keyword_events.sequence
                    """,
                    (keyword_wxid, sequence, now),
                )
            connection.execute(
                "INSERT OR REPLACE INTO automation_meta(key,value) VALUES ('cursor',?)",
                (str(sequence),),
            )
            connection.commit()

    def pending_accepts(self, now_ms: int, limit: int = 5) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM friend_leads
                WHERE state='accept_pending' AND retry_at_ms<=?
                ORDER BY request_sequence LIMIT ?
                """,
                (now_ms, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def mark_accept_sending(self, encrypt_username: str) -> None:
        now = _now_ms()
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE friend_leads
                SET state='accept_sending',attempts=attempts+1,
                    last_error=NULL,updated_at_ms=?
                WHERE encrypt_username=? AND state='accept_pending'
                """,
                (now, encrypt_username),
            )
            connection.commit()

    def mark_accept_accepted(self, encrypt_username: str) -> None:
        now = _now_ms()
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE friend_leads
                SET state='pending_contact',retry_at_ms=0,last_error=NULL,
                    updated_at_ms=?
                WHERE encrypt_username=? AND state='accept_sending'
                """,
                (now, encrypt_username),
            )
            connection.commit()

    def mark_accept_uncertain(
        self, encrypt_username: str, error: str, delay_ms: int
    ) -> None:
        now = _now_ms()
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE friend_leads
                SET state='accept_verifying',retry_at_ms=?,last_error=?,updated_at_ms=?
                WHERE encrypt_username=? AND state='accept_sending'
                """,
                (now + delay_ms, error[:1000], now, encrypt_username),
            )
            connection.commit()

    def mark_accept_retry(
        self, encrypt_username: str, error: str, delay_ms: int
    ) -> None:
        now = _now_ms()
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE friend_leads
                SET state='accept_pending',retry_at_ms=?,last_error=?,updated_at_ms=?
                WHERE encrypt_username=? AND state='accept_verifying'
                """,
                (now + delay_ms, error[:1000], now, encrypt_username),
            )
            connection.commit()

    def pending_contacts(self, now_ms: int, limit: int = 20) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM friend_leads
                WHERE state IN ('pending_contact','accept_verifying')
                  AND retry_at_ms<=?
                ORDER BY request_sequence LIMIT ?
                """,
                (now_ms, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def pending_existing_friend_keywords(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT k.wxid,k.sequence,k.received_at_ms
                FROM friend_keyword_events k
                WHERE NOT EXISTS (
                    SELECT 1 FROM friend_leads l
                    WHERE l.wxid=k.wxid OR l.candidate_username=k.wxid
                ) OR EXISTS (
                    SELECT 1 FROM friend_leads l
                    WHERE l.wxid=k.wxid AND l.state IN (
                        'assigned','left_group','join_pending','waitlist',
                        'add_pending','invite_pending','remove_pending'
                    )
                      AND k.sequence>COALESCE(l.keyword_sequence,0)
                )
                ORDER BY CASE WHEN EXISTS (
                    SELECT 1 FROM friend_leads l
                    WHERE l.wxid=k.wxid AND l.state IN (
                        'assigned','left_group','join_pending','waitlist',
                        'add_pending','invite_pending','remove_pending'
                    )
                      AND k.sequence>COALESCE(l.keyword_sequence,0)
                ) THEN 0 ELSE 1 END,
                k.sequence LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def queue_existing_friend_keyword(
        self, wxid: str, sequence: int, received_at_ms: int
    ) -> bool:
        now = _now_ms()
        synthetic_username = f"existing_friend:{wxid}"
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            keyword = connection.execute(
                """
                SELECT sequence FROM friend_keyword_events
                WHERE wxid=? AND sequence=?
                """,
                (wxid, sequence),
            ).fetchone()
            if keyword is None:
                connection.commit()
                return False
            lead = connection.execute(
                """
                SELECT encrypt_username,state,keyword_sequence,wxid
                FROM friend_leads
                WHERE wxid=? OR candidate_username=?
                ORDER BY CASE WHEN wxid=? THEN 0 ELSE 1 END
                LIMIT 1
                """,
                (wxid, wxid, wxid),
            ).fetchone()
            if lead is None:
                connection.execute(
                    """
                    INSERT INTO friend_leads(
                        encrypt_username,request_event_id,request_sequence,
                        candidate_username,wxid,state,keyword_sequence,
                        keyword_received_at_ms,retry_at_ms,created_at_ms,updated_at_ms
                    ) VALUES (?,?,?,?,?,'join_pending',?,?,0,?,?)
                    """,
                    (
                        synthetic_username,
                        f"existing-keyword-{sequence}",
                        sequence,
                        wxid,
                        wxid,
                        sequence,
                        received_at_ms,
                        now,
                        now,
                    ),
                )
                connection.commit()
                return True
            if (
                str(lead["wxid"] or "") == wxid
                and str(lead["state"]) == "assigned"
                and sequence > int(lead["keyword_sequence"] or 0)
            ):
                connection.execute(
                    """
                    UPDATE friend_leads
                    SET state='join_pending',room_id=NULL,room_name=NULL,
                        keyword_sequence=?,keyword_received_at_ms=?,retry_at_ms=0,
                        attempts=0,last_error=NULL,updated_at_ms=?
                    WHERE encrypt_username=? AND state='assigned'
                    """,
                    (sequence, received_at_ms, now, str(lead["encrypt_username"])),
                )
                connection.commit()
                return True
            connection.commit()
        return False

    def queue_tracked_keyword(
        self, wxid: str, sequence: int, received_at_ms: int
    ) -> bool:
        now = _now_ms()
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE friend_leads
                SET state='join_pending',room_id=NULL,room_name=NULL,
                    cleanup_room_id=NULL,cleanup_room_name=NULL,
                    keyword_sequence=?,keyword_received_at_ms=?,retry_at_ms=0,
                    attempts=0,last_error=NULL,updated_at_ms=?
                WHERE wxid=? AND state IN (
                    'assigned','left_group','join_pending','waitlist',
                    'add_pending','invite_pending','remove_pending'
                )
                  AND ?>COALESCE(keyword_sequence,0)
                  AND EXISTS (
                      SELECT 1 FROM friend_keyword_events k
                      WHERE k.wxid=? AND k.sequence=?
                  )
                """,
                (sequence, received_at_ms, now, wxid, sequence, wxid, sequence),
            )
            connection.commit()
        return int(cursor.rowcount) == 1

    def queue_assigned_keyword(
        self, wxid: str, sequence: int, received_at_ms: int
    ) -> bool:
        return self.queue_tracked_keyword(wxid, sequence, received_at_ms)

    def discard_keyword(self, wxid: str, sequence: int) -> None:
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM friend_keyword_events WHERE wxid=? AND sequence=?",
                (wxid, sequence),
            )
            connection.commit()

    def bind_contact(self, encrypt_username: str, wxid: str) -> None:
        now = _now_ms()
        with self._connect() as connection:
            duplicate = connection.execute(
                "SELECT encrypt_username FROM friend_leads WHERE wxid=? AND encrypt_username<>?",
                (wxid, encrypt_username),
            ).fetchone()
            if duplicate:
                connection.execute(
                    """
                    UPDATE friend_leads SET state='duplicate',last_error=?,updated_at_ms=?
                    WHERE encrypt_username=?
                    """,
                    (
                        f"wxid already tracked by {duplicate['encrypt_username']}",
                        now,
                        encrypt_username,
                    ),
                )
            else:
                connection.execute(
                    """
                    UPDATE friend_leads
                    SET wxid=?,state='welcome_pending',retry_at_ms=0,
                        attempts=0,last_error=NULL,updated_at_ms=?
                    WHERE encrypt_username=?
                    """,
                    (wxid, now, encrypt_username),
                )
            connection.commit()

    def defer(self, encrypt_username: str, error: str, delay_ms: int) -> None:
        now = _now_ms()
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE friend_leads SET retry_at_ms=?,last_error=?,updated_at_ms=?
                WHERE encrypt_username=?
                """,
                (now + delay_ms, error[:1000], now, encrypt_username),
            )
            connection.commit()

    def due_welcomes(self, now_ms: int, limit: int = 5) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM friend_leads
                WHERE state='welcome_pending' AND wxid IS NOT NULL AND retry_at_ms<=?
                ORDER BY request_sequence LIMIT ?
                """,
                (now_ms, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def mark_welcome_sending(self, encrypt_username: str) -> None:
        now = _now_ms()
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE friend_leads SET state='welcome_sending',attempts=attempts+1,
                    updated_at_ms=? WHERE encrypt_username=?
                """,
                (now, encrypt_username),
            )
            connection.commit()

    def mark_welcome_sent(self, encrypt_username: str) -> None:
        now = _now_ms()
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE friend_leads SET state='awaiting_keyword',welcome_sent_at_ms=?,
                    retry_at_ms=0,attempts=0,last_error=NULL,updated_at_ms=?
                WHERE encrypt_username=?
                """,
                (now, now, encrypt_username),
            )
            connection.commit()

    def mark_welcome_failed(
        self, encrypt_username: str, error: str, delay_ms: int
    ) -> None:
        now = _now_ms()
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE friend_leads SET state='welcome_pending',retry_at_ms=?,
                    last_error=?,updated_at_ms=? WHERE encrypt_username=?
                """,
                (now + delay_ms, error[:1000], now, encrypt_username),
            )
            connection.commit()

    def promote_keywords(self) -> int:
        now = _now_ms()
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE friend_leads
                SET state='join_pending',
                    keyword_sequence=(SELECT sequence FROM friend_keyword_events k WHERE k.wxid=friend_leads.wxid),
                    keyword_received_at_ms=(SELECT received_at_ms FROM friend_keyword_events k WHERE k.wxid=friend_leads.wxid),
                    retry_at_ms=0,last_error=NULL,updated_at_ms=?
                WHERE state='awaiting_keyword' AND EXISTS (
                    SELECT 1 FROM friend_keyword_events k
                    WHERE k.wxid=friend_leads.wxid
                      AND k.sequence>friend_leads.request_sequence
                      AND k.received_at_ms>=COALESCE(friend_leads.welcome_sent_at_ms,0)
                )
                """,
                (now,),
            )
            connection.commit()
        return int(cursor.rowcount)

    def next_join(self, now_ms: int) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM friend_leads
                WHERE state IN ('join_pending','waitlist') AND retry_at_ms<=?
                ORDER BY COALESCE(keyword_sequence,request_sequence),request_sequence LIMIT 1
                """,
                (now_ms,),
            ).fetchone()
        return dict(row) if row else None

    def next_invite_check(self, now_ms: int) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM friend_leads
                WHERE state IN ('add_pending','invite_pending') AND retry_at_ms<=?
                ORDER BY updated_at_ms LIMIT 1
                """,
                (now_ms,),
            ).fetchone()
        return dict(row) if row else None

    def next_remove_check(self, now_ms: int) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM friend_leads
                WHERE state='remove_pending' AND retry_at_ms<=?
                ORDER BY updated_at_ms LIMIT 1
                """,
                (now_ms,),
            ).fetchone()
        return dict(row) if row else None

    def membership_audit_leads(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM friend_leads
                WHERE state IN ('assigned','left_group') AND wxid IS NOT NULL
                ORDER BY request_sequence
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def mark_assigning(
        self, encrypt_username: str, room_id: str, room_name: str
    ) -> None:
        now = _now_ms()
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE friend_leads SET state='assigning',room_id=?,room_name=?,
                    attempts=attempts+1,updated_at_ms=? WHERE encrypt_username=?
                """,
                (room_id, room_name, now, encrypt_username),
            )
            connection.commit()

    def mark_add_sending(
        self, encrypt_username: str, room_id: str, room_name: str
    ) -> None:
        now = _now_ms()
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE friend_leads SET state='add_sending',room_id=?,room_name=?,
                    retry_at_ms=?,last_error='submitting direct room add',updated_at_ms=?
                WHERE encrypt_username=?
                """,
                (room_id, room_name, now + 30_000, now, encrypt_username),
            )
            connection.commit()

    def mark_add_pending(
        self, encrypt_username: str, room_id: str, room_name: str, delay_ms: int
    ) -> None:
        now = _now_ms()
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE friend_leads SET state='add_pending',room_id=?,room_name=?,
                    retry_at_ms=?,last_error='direct room add outcome is uncertain; waiting for membership',
                    updated_at_ms=? WHERE encrypt_username=?
                """,
                (room_id, room_name, now + delay_ms, now, encrypt_username),
            )
            connection.commit()

    def mark_assigned(
        self,
        encrypt_username: str,
        room_id: str,
        room_name: str,
        *,
        queue_group_welcome: bool = False,
    ) -> None:
        now = _now_ms()
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE friend_leads SET state='assigned',room_id=?,room_name=?,
                    cleanup_room_id=NULL,cleanup_room_name=NULL,
                    retry_at_ms=0,attempts=0,last_error=NULL,updated_at_ms=?
                WHERE encrypt_username=?
                """,
                (room_id, room_name, now, encrypt_username),
            )
            if queue_group_welcome:
                connection.execute(
                    """
                    UPDATE friend_leads
                    SET group_welcome_state='pending',
                        group_welcome_sent_at_ms=NULL,
                        group_welcome_retry_at_ms=0,
                        group_welcome_attempts=0,
                        group_welcome_error=NULL,
                        updated_at_ms=?
                    WHERE encrypt_username=?
                    """,
                    (now, encrypt_username),
                )
            connection.commit()

    def due_group_welcomes(
        self, now_ms: int, limit: int = 5
    ) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM friend_leads
                WHERE state='assigned'
                  AND group_welcome_state='pending'
                  AND wxid IS NOT NULL
                  AND room_id IS NOT NULL
                  AND group_welcome_retry_at_ms<=?
                ORDER BY request_sequence LIMIT ?
                """,
                (now_ms, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def queue_group_welcome(self, encrypt_username: str) -> None:
        now = _now_ms()
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE friend_leads
                SET group_welcome_state='pending',
                    group_welcome_sent_at_ms=NULL,
                    group_welcome_retry_at_ms=0,
                    group_welcome_attempts=0,
                    group_welcome_error=NULL,
                    updated_at_ms=?
                WHERE encrypt_username=?
                """,
                (now, encrypt_username),
            )
            connection.commit()

    def mark_group_welcome_sending(self, encrypt_username: str) -> bool:
        now = _now_ms()
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE friend_leads
                SET group_welcome_state='sending',
                    group_welcome_attempts=group_welcome_attempts+1,
                    group_welcome_error=NULL,
                    updated_at_ms=?
                WHERE encrypt_username=?
                  AND state='assigned'
                  AND group_welcome_state='pending'
                """,
                (now, encrypt_username),
            )
            connection.commit()
        return int(cursor.rowcount) == 1

    def mark_group_welcome_sent(self, encrypt_username: str) -> None:
        now = _now_ms()
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE friend_leads
                SET group_welcome_state='sent',group_welcome_sent_at_ms=?,
                    group_welcome_retry_at_ms=0,group_welcome_attempts=0,
                    group_welcome_error=NULL,updated_at_ms=?
                WHERE encrypt_username=? AND group_welcome_state='sending'
                """,
                (now, now, encrypt_username),
            )
            connection.commit()

    def mark_group_welcome_failed(
        self, encrypt_username: str, error: str, delay_ms: int
    ) -> None:
        now = _now_ms()
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE friend_leads
                SET group_welcome_state='pending',group_welcome_retry_at_ms=?,
                    group_welcome_error=?,updated_at_ms=?
                WHERE encrypt_username=? AND group_welcome_state='sending'
                """,
                (now + delay_ms, error[:1000], now, encrypt_username),
            )
            connection.commit()

    def mark_left_group(self, encrypt_username: str) -> None:
        now = _now_ms()
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE friend_leads SET state='left_group',room_id=NULL,room_name=NULL,
                    cleanup_room_id=NULL,cleanup_room_name=NULL,retry_at_ms=0,
                    attempts=0,last_error='not currently in a target group',updated_at_ms=?
                WHERE encrypt_username=?
                """,
                (now, encrypt_username),
            )
            connection.commit()

    def mark_invite_pending(
        self, encrypt_username: str, room_id: str, room_name: str, delay_ms: int
    ) -> None:
        now = _now_ms()
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE friend_leads SET state='invite_pending',room_id=?,room_name=?,
                    retry_at_ms=?,last_error='room action submitted; waiting for membership',
                    updated_at_ms=? WHERE encrypt_username=?
                """,
                (room_id, room_name, now + delay_ms, now, encrypt_username),
            )
            connection.commit()

    def mark_invite_sending(
        self, encrypt_username: str, room_id: str, room_name: str
    ) -> None:
        now = _now_ms()
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE friend_leads SET state='invite_sending',room_id=?,room_name=?,
                    retry_at_ms=?,last_error='submitting invitation',updated_at_ms=?
                WHERE encrypt_username=?
                """,
                (room_id, room_name, now + 30_000, now, encrypt_username),
            )
            connection.commit()

    def mark_remove_sending(
        self,
        encrypt_username: str,
        canonical_room_id: str,
        canonical_room_name: str,
        cleanup_room_id: str,
        cleanup_room_name: str,
    ) -> None:
        now = _now_ms()
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE friend_leads
                SET state='remove_sending',room_id=?,room_name=?,
                    cleanup_room_id=?,cleanup_room_name=?,retry_at_ms=?,
                    attempts=attempts+1,
                    last_error='removing duplicate target-group membership',
                    updated_at_ms=?
                WHERE encrypt_username=?
                """,
                (
                    canonical_room_id,
                    canonical_room_name,
                    cleanup_room_id,
                    cleanup_room_name,
                    now + 30_000,
                    now,
                    encrypt_username,
                ),
            )
            connection.commit()

    def mark_remove_pending(
        self, encrypt_username: str, delay_ms: int, error: str | None = None
    ) -> None:
        now = _now_ms()
        detail = error or "duplicate-room removal submitted; verifying membership"
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE friend_leads
                SET state='remove_pending',retry_at_ms=?,last_error=?,updated_at_ms=?
                WHERE encrypt_username=?
                """,
                (now + delay_ms, detail[:1000], now, encrypt_username),
            )
            connection.commit()

    def mark_reconcile_pending(self, encrypt_username: str) -> None:
        now = _now_ms()
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE friend_leads
                SET state='join_pending',room_id=NULL,room_name=NULL,
                    cleanup_room_id=NULL,cleanup_room_name=NULL,retry_at_ms=0,
                    last_error=NULL,updated_at_ms=?
                WHERE encrypt_username=?
                """,
                (now, encrypt_username),
            )
            connection.commit()

    def mark_join_retry(self, encrypt_username: str, error: str, delay_ms: int) -> None:
        now = _now_ms()
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE friend_leads SET state='join_pending',room_id=NULL,room_name=NULL,
                    cleanup_room_id=NULL,cleanup_room_name=NULL,
                    retry_at_ms=?,last_error=?,updated_at_ms=? WHERE encrypt_username=?
                """,
                (now + delay_ms, error[:1000], now, encrypt_username),
            )
            connection.commit()

    def mark_waitlist(self, encrypt_username: str, delay_ms: int) -> None:
        now = _now_ms()
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE friend_leads SET state='waitlist',room_id=NULL,room_name=NULL,
                    cleanup_room_id=NULL,cleanup_room_name=NULL,
                    retry_at_ms=?,last_error='all target groups are full',updated_at_ms=?
                WHERE encrypt_username=?
                """,
                (now + delay_ms, now, encrypt_username),
            )
            connection.commit()

    def defer_invite_check(self, encrypt_username: str, delay_ms: int) -> None:
        now = _now_ms()
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE friend_leads SET retry_at_ms=?,updated_at_ms=?
                WHERE encrypt_username=? AND state IN ('add_pending','invite_pending')
                """,
                (now + delay_ms, now, encrypt_username),
            )
            connection.commit()

    def reservations(
        self,
        room_id: str,
        exclude_wxid: str,
        existing_member_ids: set[str] | None = None,
    ) -> int:
        existing = existing_member_ids or set()
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT wxid FROM friend_leads
                WHERE room_id=? AND state IN (
                    'assigning','add_sending','add_pending','invite_sending','invite_pending'
                )
                  AND COALESCE(wxid,'')<>?
                """,
                (room_id, exclude_wxid),
            ).fetchall()
        return sum(1 for row in rows if str(row["wxid"] or "").strip() not in existing)

    def status(self) -> dict[str, Any]:
        with self._connect() as connection:
            counts = {
                str(row["state"]): int(row["count"])
                for row in connection.execute(
                    "SELECT state,count(*) count FROM friend_leads GROUP BY state"
                )
            }
            recent = [
                dict(row)
                for row in connection.execute(
                    """
                    SELECT wxid,state,room_id,room_name,last_error,
                           group_welcome_state,group_welcome_error,updated_at_ms
                    FROM friend_leads ORDER BY updated_at_ms DESC LIMIT 20
                    """
                ).fetchall()
            ]
        return {
            "enabled": self.enabled(),
            "cursor": self.cursor(),
            "fmessage_cursor": self.fmessage_cursor(),
            "counts": counts,
            "recent": recent,
        }


class FriendAutomation:
    def __init__(
        self,
        repository: Any,
        settings: Settings,
        bridge: BridgeClient | None = None,
        *,
        friend_request_source: FMessageSource | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        verification_delays: tuple[float, ...] = (1.0, 2.0, 4.0, 8.0),
        membership_audit_interval: float = 30.0,
    ) -> None:
        self.repository = repository
        self.settings = settings
        self.store = FriendAutomationStore(settings.friend_automation_path)
        self.bridge = bridge or WeChatBridgeClient(
            settings.friend_automation_bridge_url
        )
        self.friend_request_source = friend_request_source or FMessageSource(
            settings.report_path,
            settings.repository_root,
            settings.friend_automation_path.with_name("friend_requests_snapshot.sqlite"),
        )
        self._owns_bridge = bridge is None
        self._sleep = sleep
        self._verification_delays = verification_delays
        self._membership_audit_interval = max(0.0, membership_audit_interval)
        self._stop = asyncio.Event()
        self._wake = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._standby_task: asyncio.Task[None] | None = None
        self._operation_lock = asyncio.Lock()
        self._contacts_cache: list[dict[str, Any]] = []
        self._contacts_cache_at = 0.0
        self._last_auto_accept_at = 0.0
        self._last_membership_audit_at = time.monotonic()
        self._last_error: str | None = None
        self._last_fmessage_error: str | None = None
        self._native_auto_accept_status: dict[str, Any] = {}
        self._leader_lock = _AutomationLeaderLock(
            settings.friend_automation_path.with_suffix(".leader.lock")
        )
        self._leader: bool | None = None

    def _newest_sequence(self) -> int:
        return int(self.repository.realtime.ledger_latest_sequence())

    async def _latest_fmessage_rowid(self) -> int | None:
        try:
            rowid = await asyncio.to_thread(self.friend_request_source.latest_rowid)
        except Exception as exc:
            self._last_fmessage_error = str(exc)
            logger.warning("friend request source is unavailable: %s", exc)
            return None
        self._last_fmessage_error = None
        return max(0, int(rowid))

    async def _set_native_auto_accept(self, enabled: bool) -> dict[str, Any]:
        status = await self.bridge.set_auto_accept(enabled)
        self._native_auto_accept_status = dict(status or {})
        return self._native_auto_accept_status

    async def start(self) -> None:
        if self._leader is not None:
            return
        self._leader = self._leader_lock.acquire()
        if not self._leader:
            try:
                self.store.status()
            except (OSError, sqlite3.Error) as exc:
                self._last_error = f"friend automation leader is initializing: {exc}"
            logger.info("friend automation started in standby mode")
            self._standby_task = asyncio.create_task(
                self._standby_run(), name="wechat-friend-automation-standby"
            )
            return
        initial_fmessage_cursor = await self._latest_fmessage_rowid()
        try:
            self.store.initialize(
                self.settings.friend_automation_enabled,
                self._newest_sequence(),
                initial_fmessage_cursor,
            )
        except Exception:
            if self._leader:
                self._leader_lock.release()
            self._leader = None
            raise
        self._last_auto_accept_at = time.monotonic()
        self._last_membership_audit_at = (
            time.monotonic() - self._membership_audit_interval
        )
        try:
            await self._set_native_auto_accept(self.store.enabled())
        except Exception as exc:
            self._last_error = str(exc)
            logger.warning("friend automation startup sync failed: %s", exc)
        self._task = asyncio.create_task(self._run(), name="wechat-friend-automation")
        self._wake.set()

    async def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        try:
            if self._task:
                await self._task
                self._task = None
            if self._standby_task:
                await self._standby_task
                self._standby_task = None
        finally:
            was_leader = self._leader is True
            try:
                if was_leader:
                    await self._set_native_auto_accept(False)
            except Exception as exc:
                self._last_error = str(exc)
                logger.warning("friend automation shutdown sync failed: %s", exc)
            finally:
                try:
                    if self._owns_bridge:
                        await self.bridge.close()
                finally:
                    if was_leader:
                        self._leader_lock.release()
                    self._leader = None

    async def set_enabled(self, enabled: bool) -> dict[str, Any]:
        if self._leader is False:
            raise FriendAutomationError(
                "friend automation is managed by another backend instance"
            )
        async with self._operation_lock:
            if enabled:
                was_enabled = self.store.enabled()
                cursor = self._newest_sequence() if not was_enabled else None
                fmessage_cursor = (
                    await self._latest_fmessage_rowid() if not was_enabled else None
                )
                self._last_auto_accept_at = time.monotonic()
                await self._set_native_auto_accept(True)
                self.store.set_enabled(True, cursor)
                if not was_enabled:
                    if fmessage_cursor is None:
                        self.store.clear_fmessage_cursor()
                    else:
                        self.store.set_fmessage_cursor(fmessage_cursor)
            else:
                self.store.set_enabled(False)
                self._last_auto_accept_at = time.monotonic()
                try:
                    await self._set_native_auto_accept(False)
                except Exception as exc:
                    self._last_error = str(exc)
            self._wake.set()
            return self.status()

    def status(self) -> dict[str, Any]:
        result = self.store.status()
        result.update(
            {
                "keyword": self.settings.friend_automation_keyword,
                "welcome": self.settings.friend_automation_welcome,
                "group_welcome": self.settings.friend_automation_group_welcome,
                "group_names": [
                    f"{self.settings.friend_automation_group_prefix}{index}群"
                    for index in range(
                        1, self.settings.friend_automation_group_count + 1
                    )
                ],
                "group_capacity": self.settings.friend_automation_group_capacity,
                "bridge_url": self.settings.friend_automation_bridge_url,
                "last_error": self._last_error,
                "native_auto_accept": dict(self._native_auto_accept_status),
                "fmessage_source": {
                    **self.friend_request_source.status(),
                    "cursor": result.get("fmessage_cursor"),
                    "last_error": self._last_fmessage_error
                    or self.friend_request_source.status().get("last_error"),
                },
                "running": bool(
                    (self._task and not self._task.done())
                    or (self._standby_task and not self._standby_task.done())
                ),
                "leader": self._leader is True,
            }
        )
        return result

    async def _standby_run(self) -> None:
        while not self._stop.is_set():
            if self._leader_lock.acquire():
                self._leader = True
                initial_fmessage_cursor = await self._latest_fmessage_rowid()
                try:
                    self.store.initialize(
                        self.settings.friend_automation_enabled,
                        self._newest_sequence(),
                        initial_fmessage_cursor,
                    )
                except Exception as exc:
                    self._last_error = str(exc)
                    logger.exception("friend automation standby takeover failed")
                    self._leader_lock.release()
                    self._leader = False
                    try:
                        await asyncio.wait_for(self._stop.wait(), timeout=1.0)
                    except TimeoutError:
                        pass
                    continue
                self._last_auto_accept_at = time.monotonic()
                self._last_membership_audit_at = time.monotonic()
                try:
                    await self._set_native_auto_accept(self.store.enabled())
                except Exception as exc:
                    self._last_error = str(exc)
                    logger.warning(
                        "friend automation standby startup sync failed: %s", exc
                    )
                logger.info("friend automation standby became leader")
                await self._run()
                return
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=1.0)
            except TimeoutError:
                pass

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                if self.store.enabled():
                    await self.run_once()
                elif time.monotonic() - self._last_auto_accept_at >= 30.0:
                    self._last_auto_accept_at = time.monotonic()
                    await self._set_native_auto_accept(False)
            except Exception as exc:
                self._last_error = str(exc)
                logger.exception("friend automation iteration failed")
            self._wake.clear()
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=1.0)
            except TimeoutError:
                pass

    async def run_once(self) -> None:
        if self._leader is False:
            return
        async with self._operation_lock:
            if not self.store.enabled():
                return
            if time.monotonic() - self._last_auto_accept_at >= 30.0:
                self._last_auto_accept_at = time.monotonic()
                await self._set_native_auto_accept(True)
            await self._poll_fmessage_requests()
            await self._consume_events()
            await self._accept_pending_requests()
            await self._resolve_pending_contacts()
            await self._send_welcomes()
            self.store.promote_keywords()
            await self._resolve_existing_friend_keywords()
            await self._verify_remove()
            await self._verify_invite()
            await self._audit_tracked_memberships()
            await self._assign_next()
            await self._send_group_welcomes()
            self._last_error = None

    async def _poll_fmessage_requests(self) -> None:
        cursor = self.store.fmessage_cursor()
        if cursor is None:
            latest = await self._latest_fmessage_rowid()
            if latest is not None:
                self.store.set_fmessage_cursor(latest)
            return

        for _ in range(8):
            try:
                batch: FMessageBatch = await asyncio.to_thread(
                    self.friend_request_source.scan, cursor, 256
                )
            except Exception as exc:
                self._last_fmessage_error = str(exc)
                logger.warning("friend request source poll failed: %s", exc)
                return
            self._last_fmessage_error = None
            for request in batch.requests:
                result = self.repository.realtime.ingest(
                    request.realtime_event(str(getattr(self.repository, "account", "")))
                )
                sequence = _intish(result.get("sequence"))
                if not result.get("duplicate") and sequence is not None:
                    publish = getattr(self.repository.realtime, "publish", None)
                    if callable(publish):
                        publish(sequence)
            if batch.high_watermark <= cursor:
                return
            cursor = int(batch.high_watermark)
            self.store.set_fmessage_cursor(cursor)
            if len(batch.requests) < 256:
                return

    async def _consume_events(self) -> None:
        for _ in range(8):
            events = self.repository.realtime.ledger_events_since(
                self.store.cursor(), 256
            )
            if not events:
                return
            for event in events:
                request = event_friend_request(event)
                keyword_wxid = event_keyword(
                    event, self.settings.friend_automation_keyword
                )
                self.store.consume_event(event, request, keyword_wxid)
            if len(events) < 256:
                return

    async def _accept_pending_requests(self) -> None:
        for lead in self.store.pending_accepts(_now_ms()):
            encrypt_username = str(lead["encrypt_username"])
            ticket = str(lead.get("ticket") or "")
            scene = _intish(lead.get("scene"))
            friend_flag = max(0, _intish(lead.get("friend_flag")) or 0)
            self.store.mark_accept_sending(encrypt_username)
            if not ticket or scene is None or scene < 0:
                self.store.mark_accept_uncertain(
                    encrypt_username,
                    "friend request is missing ticket or scene",
                    self._retry_delay(int(lead.get("attempts") or 0)),
                )
                continue
            try:
                result = await self.bridge.accept_friend(
                    encrypt_username,
                    ticket,
                    scene,
                    friend_flag,
                )
                if result.get("ok") is False:
                    raise FriendAutomationError(
                        str(result.get("message") or "friend acceptance was rejected")
                    )
            except Exception as exc:
                self.store.mark_accept_uncertain(
                    encrypt_username,
                    f"friend acceptance outcome is uncertain: {exc}",
                    3_000,
                )
            else:
                self.store.mark_accept_accepted(encrypt_username)

    async def _contacts(self, force: bool = False) -> list[dict[str, Any]]:
        if (
            force
            or not self._contacts_cache
            or time.monotonic() - self._contacts_cache_at >= 5.0
        ):
            self._contacts_cache = await self.bridge.contacts()
            self._contacts_cache_at = time.monotonic()
        return self._contacts_cache

    async def _resolve_pending_contacts(self) -> None:
        pending = self.store.pending_contacts(_now_ms())
        if not pending:
            return
        contacts = await self._contacts(force=True)
        by_wxid = _index_contacts(contacts, "wxid")
        by_v3 = _index_contacts(contacts, "v3")
        for lead in pending:
            contact = None
            candidate = str(lead.get("candidate_username") or "")
            if candidate:
                contact = by_wxid.get(candidate)
            if contact is None:
                contact = by_v3.get(str(lead["encrypt_username"]))
            wxid = str((contact or {}).get("wxid") or "").strip()
            if contact and _is_established_friend(contact) and _valid_contact_id(wxid):
                self.store.bind_contact(str(lead["encrypt_username"]), wxid)
            elif str(lead.get("state")) == "accept_verifying":
                self.store.mark_accept_retry(
                    str(lead["encrypt_username"]),
                    "friend acceptance is not visible in contacts; retrying",
                    self._retry_delay(int(lead.get("attempts") or 0)),
                )
            else:
                self.store.defer(
                    str(lead["encrypt_username"]),
                    "accepted contact is not visible yet",
                    3_000,
                )

    async def _resolve_existing_friend_keywords(self) -> None:
        pending = self.store.pending_existing_friend_keywords()
        if not pending:
            return
        unresolved: list[dict[str, Any]] = []
        for keyword in pending:
            wxid = str(keyword["wxid"])
            sequence = int(keyword["sequence"])
            if self.store.queue_tracked_keyword(
                wxid,
                sequence,
                int(keyword["received_at_ms"]),
            ):
                continue
            unresolved.append(keyword)
        if not unresolved:
            return
        try:
            contacts = await self._contacts(force=True)
        except Exception as exc:
            logger.warning("existing-friend contact lookup failed: %s", exc)
            return
        by_wxid = _index_contacts(contacts, "wxid")
        for keyword in unresolved:
            wxid = str(keyword["wxid"])
            sequence = int(keyword["sequence"])
            contact = by_wxid.get(wxid)
            if contact is None:
                continue
            if _is_established_friend(contact) and _valid_contact_id(wxid):
                self.store.queue_existing_friend_keyword(
                    wxid,
                    sequence,
                    int(keyword["received_at_ms"]),
                )
            else:
                self.store.discard_keyword(wxid, sequence)

    @staticmethod
    def _retry_delay(attempts: int, maximum_ms: int = 60_000) -> int:
        return min(maximum_ms, 1_000 * (2 ** min(max(attempts, 0), 6)))

    async def _send_welcomes(self) -> None:
        for lead in self.store.due_welcomes(_now_ms()):
            encrypt_username = str(lead["encrypt_username"])
            self.store.mark_welcome_sending(encrypt_username)
            try:
                await self.bridge.send_text(
                    str(lead["wxid"]), self.settings.friend_automation_welcome
                )
            except FriendAutomationError as exc:
                self.store.mark_welcome_failed(
                    encrypt_username,
                    str(exc),
                    self._retry_delay(int(lead.get("attempts") or 0)),
                )
            else:
                self.store.mark_welcome_sent(encrypt_username)

    async def _group_welcome_nickname(self, wxid: str) -> str:
        contact = _index_contacts(await self._contacts(), "wxid").get(wxid, {})
        for key in ("nickname", "display_name", "remark", "name"):
            nickname = str(contact.get(key) or "").strip()
            if nickname:
                return nickname
        return wxid

    async def _send_group_welcomes(self) -> None:
        for lead in self.store.due_group_welcomes(_now_ms()):
            encrypt_username = str(lead["encrypt_username"])
            if not self.store.mark_group_welcome_sending(encrypt_username):
                continue
            wxid = str(lead.get("wxid") or "").strip()
            room_id = str(lead.get("room_id") or "").strip()
            try:
                nickname = await self._group_welcome_nickname(wxid)
                await self.bridge.send_at_text(
                    room_id,
                    wxid,
                    nickname,
                    self.settings.friend_automation_group_welcome,
                )
            except Exception as exc:
                self.store.mark_group_welcome_failed(
                    encrypt_username,
                    str(exc),
                    self._retry_delay(int(lead.get("group_welcome_attempts") or 0)),
                )
                logger.warning("group welcome failed for %s in %s: %s", wxid, room_id, exc)
            else:
                self.store.mark_group_welcome_sent(encrypt_username)

    async def _target_groups(self) -> list[tuple[str, str]]:
        contacts = await self._contacts()
        rooms: dict[str, str] = {}
        for item in contacts:
            room_id = str(item.get("wxid") or "").strip()
            if not room_id.endswith("@chatroom"):
                continue
            for key in ("nickname", "remark", "display_name", "name"):
                label = str(item.get(key) or "").strip()
                if label:
                    rooms.setdefault(label, room_id)
        ordered: list[tuple[str, str]] = []
        missing: list[str] = []
        for index in range(1, self.settings.friend_automation_group_count + 1):
            name = f"{self.settings.friend_automation_group_prefix}{index}群"
            matched_room_id = rooms.get(name)
            if matched_room_id:
                ordered.append((name, matched_room_id))
            else:
                missing.append(name)
        if missing:
            raise FriendAutomationError(
                "target groups are missing from contact list: " + ", ".join(missing)
            )
        return ordered

    @staticmethod
    def _member_ids(members: tuple[dict[str, Any], ...]) -> set[str]:
        """Return only non-empty unique wxids for membership/capacity checks."""
        return {
            str(item.get("wxid") or "").strip()
            for item in members
            if str(item.get("wxid") or "").strip()
        }

    @classmethod
    def _room_snapshot(
        cls,
        value: RoomMemberSnapshot | list[dict[str, Any]] | dict[str, Any],
    ) -> RoomMemberSnapshot:
        if isinstance(value, RoomMemberSnapshot):
            if value.count < 0:
                raise FriendAutomationError("room member count is malformed")
            unique_count = len(cls._member_ids(value.members))
            return RoomMemberSnapshot(
                members=value.members,
                count=max(value.count, unique_count),
            )
        if isinstance(value, dict):
            raw_members = value.get("members")
            if not isinstance(raw_members, list):
                raise FriendAutomationError("room member response is malformed")
            members = tuple(item for item in raw_members if isinstance(item, dict))
            count = _intish(value.get("count"))
            if count is None or count < 0:
                raise FriendAutomationError("room member count is malformed")
            return RoomMemberSnapshot(
                members,
                max(count, len(cls._member_ids(members))),
            )
        if isinstance(value, list):
            members = tuple(item for item in value if isinstance(item, dict))
            return RoomMemberSnapshot(members, len(cls._member_ids(members)))
        raise FriendAutomationError("room member response is malformed")

    async def _room_members(self, room_id: str) -> RoomMemberSnapshot:
        return self._room_snapshot(await self.bridge.room_members(room_id))

    async def _is_member(self, room_id: str, wxid: str) -> bool:
        snapshot = await self._room_members(room_id)
        return wxid.strip() in self._member_ids(snapshot.members)

    async def _wait_for_membership(self, room_id: str, wxid: str) -> bool:
        for delay in self._verification_delays:
            if delay > 0:
                await self._sleep(delay)
            if await self._is_member(room_id, wxid):
                return True
        return False

    async def _wait_for_membership_absent(self, room_id: str, wxid: str) -> bool:
        for delay in self._verification_delays:
            if delay > 0:
                await self._sleep(delay)
            if not await self._is_member(room_id, wxid):
                return True
        return False

    async def _submit_duplicate_removal(
        self,
        lead: dict[str, Any],
        canonical_room_name: str,
        canonical_room_id: str,
        cleanup_room_name: str,
        cleanup_room_id: str,
    ) -> bool:
        encrypt_username = str(lead["encrypt_username"])
        wxid = str(lead.get("wxid") or "")
        try:
            cleanup_present = await self._is_member(cleanup_room_id, wxid)
            # Check the membership we intend to keep last, immediately before
            # submitting deletion for the duplicate room.
            canonical_present = await self._is_member(canonical_room_id, wxid)
        except Exception as exc:
            self.store.mark_remove_pending(
                encrypt_username,
                30_000,
                f"duplicate-room pre-removal verification failed: {exc}",
            )
            logger.warning(
                "duplicate-room pre-removal verification failed for %s: %s",
                wxid,
                exc,
            )
            return False
        if not cleanup_present or not canonical_present:
            # Never remove the user's only remaining target-group membership.
            # A fresh full scan will either promote the cleanup room or observe
            # that the duplicate membership has already disappeared.
            self.store.mark_reconcile_pending(encrypt_username)
            return True
        self.store.mark_remove_sending(
            encrypt_username,
            canonical_room_id,
            canonical_room_name,
            cleanup_room_id,
            cleanup_room_name,
        )
        removal_error: str | None = None
        try:
            await self.bridge.remove_room_member(cleanup_room_id, wxid)
        except Exception as exc:
            removal_error = f"duplicate-room removal outcome is uncertain: {exc}"
            logger.warning(
                "duplicate-room removal outcome is uncertain for %s in %s: %s",
                wxid,
                cleanup_room_id,
                exc,
            )
        self.store.mark_remove_pending(
            encrypt_username,
            0,
            removal_error,
        )
        try:
            removed = await self._wait_for_membership_absent(cleanup_room_id, wxid)
        except Exception as exc:
            self.store.mark_remove_pending(
                encrypt_username,
                30_000,
                f"duplicate-room membership verification failed: {exc}",
            )
            logger.warning(
                "duplicate-room membership verification failed for %s in %s: %s",
                wxid,
                cleanup_room_id,
                exc,
            )
            return False
        if removed:
            self.store.mark_reconcile_pending(encrypt_username)
            return True
        self.store.mark_remove_pending(
            encrypt_username,
            self._retry_delay(int(lead.get("attempts") or 0), 120_000),
            "duplicate target-group membership still present after removal request",
        )
        return False

    async def _scan_target_groups(
        self, groups: list[tuple[str, str]], wxid: str
    ) -> tuple[
        list[tuple[str, str, RoomMemberSnapshot, set[str]]],
        list[tuple[str, str]],
    ]:
        room_snapshots: list[tuple[str, str, RoomMemberSnapshot, set[str]]] = []
        memberships: list[tuple[str, str]] = []
        for room_name, room_id in groups:
            snapshot = await self._room_members(room_id)
            member_ids = self._member_ids(snapshot.members)
            if wxid in member_ids:
                memberships.append((room_name, room_id))
            room_snapshots.append((room_name, room_id, snapshot, member_ids))
        return room_snapshots, memberships

    async def _audit_tracked_memberships(self) -> None:
        now = time.monotonic()
        if now - self._last_membership_audit_at < self._membership_audit_interval:
            return
        self._last_membership_audit_at = now
        leads = self.store.membership_audit_leads()
        if not leads:
            return
        try:
            groups = await self._target_groups()
            snapshots: list[tuple[str, str, set[str]]] = []
            for room_name, room_id in groups:
                snapshot = await self._room_members(room_id)
                snapshots.append(
                    (room_name, room_id, self._member_ids(snapshot.members))
                )
        except Exception as exc:
            logger.warning("target-group membership audit failed: %s", exc)
            return
        for lead in leads:
            wxid = str(lead.get("wxid") or "").strip()
            if not wxid:
                continue
            memberships = [
                (room_name, room_id)
                for room_name, room_id, member_ids in snapshots
                if wxid in member_ids
            ]
            encrypt_username = str(lead["encrypt_username"])
            if len(memberships) > 1:
                # Reuse the durable join state machine so duplicate removals
                # remain crash-safe and are always rechecked against fresh data.
                self.store.mark_reconcile_pending(encrypt_username)
            elif len(memberships) == 1:
                room_name, room_id = memberships[0]
                if (
                    str(lead.get("state")) != "assigned"
                    or str(lead.get("room_id") or "") != room_id
                ):
                    self.store.mark_assigned(encrypt_username, room_id, room_name)
            elif str(lead.get("state")) != "left_group":
                # Do not automatically re-add a voluntary leaver. Their next
                # private keyword moves this lead back to join_pending at once.
                self.store.mark_left_group(encrypt_username)

    async def _reconcile_joined_memberships(
        self,
        lead: dict[str, Any],
        groups: list[tuple[str, str]],
        *,
        queue_group_welcome: bool = False,
    ) -> bool:
        encrypt_username = str(lead["encrypt_username"])
        wxid = str(lead.get("wxid") or "")
        for _ in range(len(groups)):
            _, memberships = await self._scan_target_groups(groups, wxid)
            if memberships and queue_group_welcome:
                # Persist the send intent before duplicate-room cleanup, which
                # may require retries or a process restart before assignment.
                self.store.queue_group_welcome(encrypt_username)
                queue_group_welcome = False
            if len(memberships) == 1:
                room_name, room_id = memberships[0]
                self.store.mark_assigned(encrypt_username, room_id, room_name)
                return True
            if not memberships:
                self.store.mark_reconcile_pending(encrypt_username)
                return False
            canonical_room_name, canonical_room_id = memberships[0]
            cleanup_room_name, cleanup_room_id = memberships[1]
            removed = await self._submit_duplicate_removal(
                lead,
                canonical_room_name,
                canonical_room_id,
                cleanup_room_name,
                cleanup_room_id,
            )
            if not removed:
                return False
        self.store.mark_join_retry(
            encrypt_username,
            "target-group membership reconciliation did not converge",
            30_000,
        )
        return False

    async def _verify_remove(self) -> None:
        lead = self.store.next_remove_check(_now_ms())
        if not lead:
            return
        encrypt_username = str(lead["encrypt_username"])
        wxid = str(lead.get("wxid") or "")
        canonical_room_id = str(lead.get("room_id") or "")
        canonical_room_name = str(lead.get("room_name") or "")
        cleanup_room_id = str(lead.get("cleanup_room_id") or "")
        cleanup_room_name = str(lead.get("cleanup_room_name") or "")
        if not wxid or not canonical_room_id or not cleanup_room_id:
            self.store.mark_reconcile_pending(encrypt_username)
            return
        try:
            still_present = await self._is_member(cleanup_room_id, wxid)
        except Exception as exc:
            self.store.mark_remove_pending(
                encrypt_username,
                30_000,
                f"duplicate-room membership verification failed: {exc}",
            )
            logger.warning(
                "duplicate-room membership verification failed for %s in %s: %s",
                wxid,
                cleanup_room_id,
                exc,
            )
            return
        if not still_present:
            self.store.mark_reconcile_pending(encrypt_username)
            return
        await self._submit_duplicate_removal(
            lead,
            canonical_room_name,
            canonical_room_id,
            cleanup_room_name,
            cleanup_room_id,
        )

    async def _verify_invite(self) -> None:
        lead = self.store.next_invite_check(_now_ms())
        if not lead:
            return
        encrypt_username = str(lead["encrypt_username"])
        room_id = str(lead.get("room_id") or "")
        room_name = str(lead.get("room_name") or "")
        wxid = str(lead.get("wxid") or "")
        try:
            joined = bool(room_id and wxid and await self._is_member(room_id, wxid))
        except Exception as exc:
            self.store.defer_invite_check(encrypt_username, 30_000)
            logger.warning("room membership verification failed for %s: %s", wxid, exc)
            return
        if joined:
            try:
                await self._reconcile_joined_memberships(
                    lead,
                    await self._target_groups(),
                    queue_group_welcome=True,
                )
            except Exception as exc:
                self.store.mark_reconcile_pending(encrypt_username)
                logger.warning(
                    "target-group reconciliation failed for %s: %s", wxid, exc
                )
            return
        if str(lead.get("state")) != "add_pending":
            self.store.defer_invite_check(encrypt_username, 30_000)
            return
        # The direct-add request may have completed while the worker was down.
        # Persist the invitation intent before touching the bridge so a crash
        # cannot cause a second invitation on restart.
        self.store.mark_invite_sending(encrypt_username, room_id, room_name)
        try:
            await self.bridge.invite_room_member(room_id, wxid)
        except Exception as exc:
            logger.warning("room invitation outcome is uncertain for %s: %s", wxid, exc)
        self.store.mark_invite_pending(encrypt_username, room_id, room_name, 30_000)

    async def _assign_next(self) -> None:
        lead = self.store.next_join(_now_ms())
        if not lead:
            return
        encrypt_username = str(lead["encrypt_username"])
        wxid = str(lead.get("wxid") or "")
        if not wxid:
            self.store.mark_join_retry(
                encrypt_username, "lead has no resolved wxid", 3_000
            )
            return
        try:
            groups = await self._target_groups()
            selected: tuple[str, str] | None = None
            room_snapshots, memberships = await self._scan_target_groups(groups, wxid)
            if len(memberships) == 1:
                room_name, room_id = memberships[0]
                self.store.mark_assigned(encrypt_username, room_id, room_name)
                return
            if len(memberships) > 1:
                canonical_room_name, canonical_room_id = memberships[0]
                cleanup_room_name, cleanup_room_id = memberships[1]
                removed = await self._submit_duplicate_removal(
                    lead,
                    canonical_room_name,
                    canonical_room_id,
                    cleanup_room_name,
                    cleanup_room_id,
                )
                if removed:
                    await self._reconcile_joined_memberships(lead, groups)
                return
            for room_name, room_id, snapshot, member_ids in room_snapshots:
                reserved = self.store.reservations(room_id, wxid, member_ids)
                if (
                    snapshot.count + reserved
                    < self.settings.friend_automation_group_capacity
                ):
                    selected = (room_name, room_id)
                    break
            if selected is None:
                self.store.mark_waitlist(encrypt_username, 60_000)
                return
            room_name, room_id = selected
            self.store.mark_assigning(encrypt_username, room_id, room_name)
            self.store.mark_add_sending(encrypt_username, room_id, room_name)
            try:
                await self.bridge.add_room_member(room_id, wxid)
            except Exception as exc:
                logger.warning(
                    "direct room-add outcome is uncertain for %s: %s", wxid, exc
                )
            # Whether the bridge returned success or an exception, the
            # operation may have reached WeChat. Keep it recoverable and do
            # not submit the fallback invitation until membership is checked.
            self.store.mark_add_pending(encrypt_username, room_id, room_name, 0)
            try:
                joined = await self._wait_for_membership(room_id, wxid)
            except Exception as exc:
                self.store.mark_add_pending(
                    encrypt_username, room_id, room_name, 30_000
                )
                logger.warning(
                    "room membership verification failed for %s: %s", wxid, exc
                )
                return
            if joined:
                await self._reconcile_joined_memberships(
                    lead, groups, queue_group_welcome=True
                )
                return
            self.store.mark_invite_sending(encrypt_username, room_id, room_name)
            try:
                await self.bridge.invite_room_member(room_id, wxid)
            except Exception as exc:
                self.store.mark_invite_pending(
                    encrypt_username, room_id, room_name, 30_000
                )
                logger.warning(
                    "room invitation outcome is uncertain for %s: %s", wxid, exc
                )
                return
            self.store.mark_invite_pending(encrypt_username, room_id, room_name, 0)
            try:
                joined = await self._wait_for_membership(room_id, wxid)
            except Exception as exc:
                self.store.mark_invite_pending(
                    encrypt_username, room_id, room_name, 30_000
                )
                logger.warning(
                    "room membership verification failed for %s: %s", wxid, exc
                )
                return
            if joined:
                await self._reconcile_joined_memberships(
                    lead, groups, queue_group_welcome=True
                )
            else:
                self.store.mark_invite_pending(
                    encrypt_username, room_id, room_name, 30_000
                )
        except FriendAutomationError as exc:
            self.store.mark_join_retry(
                encrypt_username,
                str(exc),
                self._retry_delay(int(lead.get("attempts") or 0), 120_000),
            )
