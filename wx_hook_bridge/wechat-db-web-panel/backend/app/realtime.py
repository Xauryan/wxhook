from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
from collections import deque
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .parsing import (
    GROUP_PREFIX,
    _parse_xml,
    decode_wcdb_value,
    message_quality_score,
    parse_message_content,
    split_local_type,
)


REALTIME_SCHEMA_VERSION = 1
REALTIME_LEDGER_SCHEMA_VERSION = 3
REALTIME_BODY_LIMIT = 256 * 1024
REALTIME_LIVE_QUEUE_LIMIT = 1024
REALTIME_REPLAY_BATCH = 256


class RealtimeValidationError(ValueError):
    pass


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _first(payload: dict[str, Any], *names: str) -> Any:
    for name in names:
        value = payload.get(name)
        if value is not None and value != "":
            return value
    return None


def _text(value: Any, limit: int = 131_072) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value[:limit]
    if isinstance(value, (dict, list)):
        return _json(value)[:limit]
    return str(value)[:limit]


def _boolish(value: Any) -> bool | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    normalized = str(value).strip().casefold()
    if normalized in {"1", "true", "yes", "on", "outgoing", "sent"}:
        return True
    if normalized in {"0", "false", "no", "off", "incoming", "received"}:
        return False
    return None


def _intish(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(str(value).strip(), 10)
    except (TypeError, ValueError):
        return None


def _timestamp(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
        number = parsed.timestamp()
    if number > 32_503_680_000:
        number /= 1000.0
    result = int(number)
    return result if 0 < result < 32_503_680_000 else None


def _stable_negative(value: str) -> int:
    return -(int(hashlib.sha256(value.encode("utf-8")).hexdigest()[:8], 16) or 1)


def _flatten_payload(raw: dict[str, Any]) -> dict[str, Any]:
    nested = raw.get("payload")
    flattened = dict(nested) if isinstance(nested, dict) else {}
    flattened.update({key: value for key, value in raw.items() if key != "payload"})
    return flattened


def normalize_event(
    raw: dict[str, Any], account: str, timezone: ZoneInfo
) -> dict[str, Any]:
    payload = _flatten_payload(raw)
    source = _text(_first(payload, "source") or "wx_hook_bridge", 80)
    message_id_text = _text(
        _first(payload, "message_id", "messageId", "msgId", "msg_id"), 80
    )
    local_id_text = _text(
        _first(payload, "local_id", "localId", "msgLocalId", "msg_local_id"), 80
    )
    timestamp_value = _first(
        payload, "timestamp_unix", "timestampUnix", "timestamp", "create_time"
    )
    signature = _text(_first(payload, "signature"), 256)
    provided_event_id = _text(_first(payload, "event_id", "eventId"))
    if len(provided_event_id) > 200:
        raise RealtimeValidationError("event_id exceeds 200 characters")
    direction = _text(_first(payload, "direction"), 24).casefold()
    outgoing = _boolish(
        _first(payload, "is_outgoing", "isOutgoing", "isSendMsg", "is_send_msg")
    )
    incoming = _boolish(
        _first(payload, "is_incoming", "isIncoming", "is_recv_msg")
    )
    if outgoing is None and direction in {"outgoing", "sent", "send"}:
        outgoing = True
    if incoming is None and direction in {"incoming", "received", "receive"}:
        incoming = True

    content = _text(_first(payload, "content"))
    message_type_value = _first(
        payload, "message_type", "messageType", "type", "msg_type"
    )
    message_subtype_value = _first(
        payload, "message_subtype", "messageSubtype", "subType", "subtype"
    )
    raw_type = _intish(message_type_value)
    explicit_subtype = _intish(message_subtype_value)
    timestamp = _timestamp(timestamp_value)
    timestamp_source = 'message'
    # The native revoke hook can emit a verified notice with revokeTime=0.
    # Keep event identity based on the original payload, while dating its chat
    # presentation by receipt time. The recall ledger separately labels the
    # actual recall time as unknown; zero must not discard the entire notice.
    if timestamp is None and raw_type in {10000, 10002}:
        root = _parse_xml(content)
        if root is not None and root.find('.//revokemsg') is not None:
            timestamp = int(time.time())
            timestamp_source = 'observed_at'
    peer_id = _text(_first(payload, "peer_id", "peerId", "peerWxId"), 240)
    room_id = _text(_first(payload, "room_id", "roomId"), 240)
    sender_id = _text(
        _first(payload, "sender_id", "senderId", "senderWxId"), 240
    )
    self_id = _text(_first(payload, "self_id", "selfId", "selfWxId"), 240) or account
    self_identity_known = _boolish(
        _first(payload, "self_identity_known", "selfIdentityKnown")
    )
    raw_from = _text(_first(payload, "raw_from", "rawFrom", "from"), 240)
    raw_to = _text(_first(payload, "raw_to", "rawTo", "to"), 240)
    undirected = not bool(outgoing) and not bool(incoming) and direction in {
        "",
        "unknown",
    }
    if undirected and self_identity_known is not True:
        if raw_from and raw_from == account and raw_to != account:
            outgoing = True
        elif raw_to and raw_to == account and raw_from != account:
            incoming = True
    if outgoing is None and raw_from and raw_from == self_id and raw_to != self_id:
        outgoing = True
    if incoming is None and raw_to and raw_to == self_id and raw_from != self_id:
        incoming = True
    if outgoing is None and incoming is True:
        outgoing = False
    if incoming is None and outgoing is True:
        incoming = False
    outgoing = bool(outgoing)
    incoming = bool(incoming)
    if not direction:
        direction = "outgoing" if outgoing else "incoming" if incoming else "unknown"
    session_username = room_id or peer_id
    if not session_username:
        session_username = raw_to if outgoing and raw_to != self_id else raw_from
    if not session_username and incoming:
        session_username = raw_from if raw_from != self_id else raw_to
    # In the native envelope fromWxId is a compatibility field containing the
    # logged-in account, not the author. In particular custom WeChat usernames
    # may be missing from sender_id while still present in the group prefix.
    prefix = GROUP_PREFIX.match(content) if session_username.endswith('@chatroom') else None
    if prefix:
        sender_username = prefix.group(1)
        outgoing = sender_username == self_id
        incoming = not outgoing
        direction = 'outgoing' if outgoing else 'incoming'
    else:
        legacy_sender = _text(_first(payload, 'fromWxId'), 240) if not any(
            key in payload for key in ('sender_id', 'senderId', 'senderWxId', 'raw_from', 'rawFrom', 'from')
        ) else ''
        sender_username = sender_id or (self_id if outgoing else raw_from or peer_id or legacy_sender)

    event_id = provided_event_id.strip()
    event_id_derived = False
    if not event_id:
        if not (message_id_text or local_id_text) or timestamp_value is None:
            raise RealtimeValidationError(
                "event_id is required when message identity and timestamp are unavailable"
            )
        basis = "\0".join(
            (
                source,
                session_username,
                message_id_text,
                local_id_text,
                str(timestamp_value),
                signature,
            )
        )
        event_id = "derived-" + hashlib.sha256(basis.encode("utf-8")).hexdigest()
        event_id_derived = True
    if any(ord(character) < 32 for character in event_id):
        raise RealtimeValidationError("event_id contains control characters")

    identity_key = None
    if session_username and (message_id_text or local_id_text):
        identity_key = hashlib.sha256(
            (
                f"{source}\0{session_username}\0{message_id_text}\0"
                f"{local_id_text}"
            ).encode("utf-8")
        ).hexdigest()

    input_event_type = _text(
        _first(payload, "event_type", "eventType") or "message", 80
    )
    event_type = input_event_type
    if input_event_type == "message":
        if raw_type in {10000, 10002} and "revokemsg" in content:
            event_type = "message.recalled"
        elif outgoing:
            event_type = "message.sent"
        elif incoming:
            event_type = "message.received"

    errors: list[str] = []
    warnings: list[str] = []
    if not session_username:
        errors.append("session identity is unavailable")
    if timestamp is None:
        errors.append("timestamp is invalid or unavailable")
    if raw_type is None:
        errors.append("message type is invalid or unavailable")
    if not sender_username:
        warnings.append("sender identity is unavailable")

    normalized = None
    decoded_ok = False
    if not errors:
        assert raw_type is not None and timestamp is not None
        if raw_type > 0xFFFFFFFF:
            base_type, encoded_subtype = split_local_type(raw_type)
            app_subtype = explicit_subtype if explicit_subtype is not None else encoded_subtype
            local_type = raw_type
        else:
            base_type = raw_type & 0xFFFFFFFF
            app_subtype = max(0, explicit_subtype or 0)
            local_type = base_type | (app_subtype << 32)
        decoded = decode_wcdb_value(content)
        parsed = parse_message_content(
            decoded.text, base_type, app_subtype, session_username.endswith("@chatroom")
        )
        decoded_ok = bool(decoded.decoded)
        numeric_local_id = _intish(local_id_text)
        if numeric_local_id is None:
            numeric_local_id = _stable_negative(event_id + ":local")
            warnings.append("local_id was derived from event_id")
        numeric_server_id = _intish(message_id_text)
        if numeric_server_id is None:
            numeric_server_id = 0
            if message_id_text:
                warnings.append("non-numeric message_id retained only in event payload")
        local_dt = datetime.fromtimestamp(timestamp, timezone)
        message_key = hashlib.sha1(
            f"{session_username}\0{numeric_local_id}\0{numeric_server_id}".encode(
                "utf-8"
            )
        ).hexdigest()
        normalized = {
            "message_key": message_key,
            "session_username": session_username,
            "session_id": None,
            "session_name": session_username,
            "local_id": numeric_local_id,
            "server_id": numeric_server_id,
            "local_type": local_type,
            "base_type": base_type,
            "app_subtype": app_subtype,
            "app_type": parsed.app_type,
            "sort_seq": numeric_local_id,
            "sender_id": _intish(sender_id),
            "sender_username": sender_username,
            "sender_name": sender_username or session_username,
            "is_self": int(bool(outgoing or sender_username == self_id)),
            "create_time": timestamp,
            "timestamp_source": timestamp_source,
            "local_date": local_dt.date().isoformat(),
            "local_hour": local_dt.hour,
            "category": parsed.category,
            "decoded": int(decoded.decoded),
            "decode_error": decoded.error,
            "content": parsed.content,
            "summary": parsed.summary,
            "title": parsed.title,
            "description": parsed.description,
            "url": parsed.url,
            "file_name": parsed.file_name,
            "file_size": parsed.file_size,
            "reference_server_id": parsed.reference_server_id,
            "reference_from_username": parsed.reference_from_username,
            "reference_chat_username": parsed.reference_chat_username,
            "reference_display_name": parsed.reference_display_name,
            "reference_content": parsed.reference_content,
            "quote_count": 0,
            "quality_score": message_quality_score(parsed.content, parsed.category),
        }

    quality = {
        "state": "pending" if errors else "ready",
        "decoded": decoded_ok,
        "event_id_derived": event_id_derived,
        "errors": errors,
        "warnings": warnings,
    }
    return {
        "event_id": event_id,
        "session_key": session_username,
        "identity_key": identity_key,
        "event_type": event_type,
        "source": source,
        "schema_version": _intish(_first(payload, "schema_version", "schemaVersion"))
        or REALTIME_SCHEMA_VERSION,
        "payload": raw,
        "normalized": normalized,
        "quality": quality,
    }


class RealtimeStore:
    def __init__(
        self,
        path: Path,
        account: str,
        snapshot: str | None,
        database_root: Path,
        timezone: ZoneInfo,
    ) -> None:
        self.path = path.resolve()
        self.account = account
        self.snapshot = snapshot or "unknown"
        self.database_key = hashlib.sha256(
            str(database_root.resolve()).encode("utf-8")
        ).hexdigest()
        self.scope_key = self._scope_for(self.account, self.database_key)
        self.timezone = timezone
        self._lock = threading.RLock()
        self._recent: deque[int] = deque(maxlen=REALTIME_LIVE_QUEUE_LIMIT)
        self._subscribers = 0
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=5000")
        return connection

    @contextmanager
    def _connection(self):
        connection = self._connect()
        try:
            yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._lock, self._connection() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=NORMAL")
            existing = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='realtime_events'"
            ).fetchone()
            if existing:
                columns = {
                    str(row[1])
                    for row in connection.execute("PRAGMA table_info(realtime_events)")
                }
                if not {
                    "scope_key",
                    "database_key",
                    "account",
                    "snapshot",
                    "session_key",
                }.issubset(columns):
                    self._migrate_legacy_events(connection)
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS realtime_events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    scope_key TEXT NOT NULL,
                    database_key TEXT NOT NULL,
                    account TEXT NOT NULL,
                    snapshot TEXT NOT NULL,
                    session_key TEXT NOT NULL,
                    event_id TEXT NOT NULL,
                    identity_key TEXT,
                    event_type TEXT NOT NULL,
                    source TEXT NOT NULL,
                    schema_version INTEGER NOT NULL,
                    received_at INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    quality_json TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    normalized_json TEXT,
                    error TEXT
                );
                CREATE UNIQUE INDEX IF NOT EXISTS realtime_events_event_scope
                    ON realtime_events(scope_key,session_key,event_id);
                CREATE UNIQUE INDEX IF NOT EXISTS realtime_events_identity
                    ON realtime_events(scope_key,session_key,identity_key)
                    WHERE identity_key IS NOT NULL;
                CREATE INDEX IF NOT EXISTS realtime_events_status
                    ON realtime_events(scope_key,status,sequence);
                CREATE TABLE IF NOT EXISTS realtime_meta (
                    key TEXT PRIMARY KEY,
                    value INTEGER NOT NULL
                );
                """
            )
            connection.execute(
                "INSERT OR IGNORE INTO realtime_meta(key,value) VALUES (?,0)",
                (self._duplicates_key,),
            )
            connection.execute(
                "INSERT OR REPLACE INTO realtime_meta(key,value) VALUES (?,?)",
                (f"schema_version:{self.scope_key}", REALTIME_LEDGER_SCHEMA_VERSION),
            )
            connection.commit()

    @property
    def _duplicates_key(self) -> str:
        return f"duplicates:{self.scope_key}"

    @staticmethod
    def _scope_for(account: str, database_key: str) -> str:
        return hashlib.sha256(f"{account}\0{database_key}".encode("utf-8")).hexdigest()

    def _migrate_legacy_events(self, connection: sqlite3.Connection) -> None:
        rows = connection.execute(
            """
            SELECT * FROM realtime_events
            ORDER BY CASE WHEN status='applied' THEN 0 ELSE 1 END,sequence
            """
        ).fetchall()
        connection.executescript(
            """
            DROP TABLE IF EXISTS realtime_events_migrated;
            CREATE TABLE realtime_events_migrated (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                scope_key TEXT NOT NULL,
                database_key TEXT NOT NULL,
                account TEXT NOT NULL,
                snapshot TEXT NOT NULL,
                session_key TEXT NOT NULL,
                event_id TEXT NOT NULL,
                identity_key TEXT,
                event_type TEXT NOT NULL,
                source TEXT NOT NULL,
                schema_version INTEGER NOT NULL,
                received_at INTEGER NOT NULL,
                status TEXT NOT NULL,
                quality_json TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                normalized_json TEXT,
                error TEXT
            );
            """
        )
        event_keys: set[tuple[str, str, str]] = set()
        identity_keys: set[tuple[str, str, str]] = set()
        for row in rows:
            item = dict(row)
            normalized_json = item.get("normalized_json")
            session_key = str(item.get("session_key") or "")
            if normalized_json:
                try:
                    normalized = json.loads(str(normalized_json))
                except (TypeError, json.JSONDecodeError):
                    normalized = None
                if isinstance(normalized, dict):
                    session_key = session_key or str(
                        normalized.get("session_username") or ""
                    )
            account = str(item.get("account") or self.account)
            database_key = str(item.get("database_key") or self.database_key)
            scope_key = self._scope_for(account, database_key)
            event_key = (scope_key, session_key, str(item["event_id"]))
            identity_key = str(item.get("identity_key") or "")
            scoped_identity = (scope_key, session_key, identity_key)
            if event_key in event_keys or (
                identity_key and scoped_identity in identity_keys
            ):
                continue
            event_keys.add(event_key)
            if identity_key:
                identity_keys.add(scoped_identity)
            connection.execute(
                """
                INSERT OR IGNORE INTO realtime_events_migrated(
                    sequence,scope_key,database_key,account,snapshot,session_key,
                    event_id,identity_key,event_type,source,schema_version,
                    received_at,status,quality_json,payload_json,normalized_json,error
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    item["sequence"],
                    scope_key,
                    database_key,
                    account,
                    str(item.get("snapshot") or self.snapshot),
                    session_key,
                    item["event_id"],
                    item.get("identity_key"),
                    item["event_type"],
                    item["source"],
                    item["schema_version"],
                    item["received_at"],
                    item["status"],
                    item["quality_json"],
                    item["payload_json"],
                    normalized_json,
                    item.get("error"),
                ),
            )
        connection.executescript(
            """
            DROP TABLE realtime_events;
            ALTER TABLE realtime_events_migrated RENAME TO realtime_events;
            """
        )

    @staticmethod
    def _row(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        for internal in (
            "scope_key",
            "database_key",
            "account",
            "snapshot",
            "session_key",
        ):
            result.pop(internal, None)
        result["quality"] = json.loads(result.pop("quality_json"))
        result["payload"] = json.loads(result.pop("payload_json"))
        normalized = result.pop("normalized_json")
        result["normalized"] = json.loads(normalized) if normalized else None
        return result

    def ingest(self, raw: dict[str, Any]) -> dict[str, Any]:
        prepared = normalize_event(raw, self.account, self.timezone)
        normalized = prepared["normalized"]
        status = "accepted" if normalized is not None else "pending"
        with self._lock, self._connection() as connection:
            try:
                cursor = connection.execute(
                    """
                    INSERT INTO realtime_events(
                        scope_key,database_key,account,snapshot,session_key,event_id,
                        identity_key,event_type,source,schema_version,received_at,
                        status,quality_json,payload_json,normalized_json,error
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,NULL)
                    """,
                    (
                        self.scope_key,
                        self.database_key,
                        self.account,
                        self.snapshot,
                        prepared["session_key"],
                        prepared["event_id"],
                        prepared["identity_key"],
                        prepared["event_type"],
                        prepared["source"],
                        prepared["schema_version"],
                        int(time.time()),
                        status,
                        _json(prepared["quality"]),
                        _json(prepared["payload"]),
                        _json(normalized) if normalized is not None else None,
                    ),
                )
                sequence = int(cursor.lastrowid)
                connection.commit()
            except sqlite3.IntegrityError:
                row = connection.execute(
                    """
                    SELECT * FROM realtime_events
                    WHERE scope_key=? AND session_key=? AND
                          (event_id=? OR (identity_key IS NOT NULL AND identity_key=?))
                    ORDER BY CASE WHEN event_id=? THEN 0 ELSE 1 END LIMIT 1
                    """,
                    (
                        self.scope_key,
                        prepared["session_key"],
                        prepared["event_id"],
                        prepared["identity_key"],
                        prepared["event_id"],
                    ),
                ).fetchone()
                connection.execute(
                    "UPDATE realtime_meta SET value=value+1 WHERE key=?",
                    (self._duplicates_key,),
                )
                connection.commit()
                if row is None:
                    raise
                result = self._row(row)
                result.update(
                    {
                        "accepted": True,
                        "duplicate": True,
                        "dedupe_basis": "event_id"
                        if row["event_id"] == prepared["event_id"]
                        else "message_identity",
                        "duplicate_of_event_id": row["event_id"],
                    }
                )
                return result
        return {
            "accepted": True,
            "duplicate": False,
            "sequence": sequence,
            "event_id": prepared["event_id"],
            "event_type": prepared["event_type"],
            "source": prepared["source"],
            "schema_version": prepared["schema_version"],
            "status": status,
            "quality": prepared["quality"],
            "payload": prepared["payload"],
            "normalized": normalized,
            "error": None,
        }

    def mark(self, sequence: int, status: str, error: str | None = None) -> None:
        with self._lock, self._connection() as connection:
            connection.execute(
                """
                UPDATE realtime_events SET status=?,error=?
                WHERE scope_key=? AND sequence=?
                """,
                (status, error, self.scope_key, sequence),
            )
            connection.commit()

    def publish(self, sequence: int) -> None:
        with self._lock:
            self._recent.append(sequence)

    def normalized_messages(self) -> list[tuple[int, dict[str, Any]]]:
        with self._lock, self._connection() as connection:
            rows = connection.execute(
                """
                SELECT sequence,normalized_json FROM realtime_events
                WHERE scope_key=? AND normalized_json IS NOT NULL AND status!='rejected'
                ORDER BY sequence
                """,
                (self.scope_key,),
            ).fetchall()
        return [(int(row["sequence"]), json.loads(row["normalized_json"])) for row in rows]

    def pending_messages(self) -> list[tuple[int, dict[str, Any]]]:
        with self._lock, self._connection() as connection:
            rows = connection.execute(
                """
                SELECT sequence,normalized_json FROM realtime_events
                WHERE scope_key=? AND normalized_json IS NOT NULL
                  AND status IN ('accepted','pending')
                ORDER BY sequence
                """,
                (self.scope_key,),
            ).fetchall()
        return [(int(row["sequence"]), json.loads(row["normalized_json"])) for row in rows]

    def events_since(self, after: int, limit: int = REALTIME_REPLAY_BATCH) -> list[dict[str, Any]]:
        limit = max(1, min(limit, REALTIME_REPLAY_BATCH))
        with self._lock, self._connection() as connection:
            barrier = connection.execute(
                """
                SELECT min(sequence) FROM realtime_events
                WHERE scope_key=? AND sequence>? AND normalized_json IS NOT NULL
                  AND status IN ('accepted','pending')
                """,
                (self.scope_key, max(0, after)),
            ).fetchone()[0]
            parameters: list[Any] = [self.scope_key, max(0, after)]
            upper_bound = ""
            if barrier is not None:
                upper_bound = " AND sequence<?"
                parameters.append(int(barrier))
            parameters.append(limit)
            rows = connection.execute(
                f"""
                SELECT * FROM realtime_events
                WHERE scope_key=? AND sequence>? AND status='applied'{upper_bound}
                ORDER BY sequence LIMIT ?
                """,
                parameters,
            ).fetchall()
        return [self._row(row) for row in rows]

    def ledger_events_since(
        self, after: int, limit: int = REALTIME_REPLAY_BATCH
    ) -> list[dict[str, Any]]:
        """Return every durable event, including pending friend-request records."""
        limit = max(1, min(limit, REALTIME_REPLAY_BATCH))
        with self._lock, self._connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM realtime_events
                WHERE scope_key=? AND sequence>?
                ORDER BY sequence LIMIT ?
                """,
                (self.scope_key, max(0, after), limit),
            ).fetchall()
        return [self._row(row) for row in rows]

    def ledger_latest_sequence(self) -> int:
        with self._lock, self._connection() as connection:
            value = connection.execute(
                "SELECT max(sequence) FROM realtime_events WHERE scope_key=?",
                (self.scope_key,),
            ).fetchone()[0]
        return int(value or 0)

    def event(self, sequence: int) -> dict[str, Any] | None:
        with self._lock, self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM realtime_events WHERE scope_key=? AND sequence=?",
                (self.scope_key, sequence),
            ).fetchone()
        return self._row(row) if row else None

    def status(self) -> dict[str, Any]:
        with self._lock, self._connection() as connection:
            counts = {
                row["status"]: int(row["count"])
                for row in connection.execute(
                    """
                    SELECT status,count(*) count FROM realtime_events
                    WHERE scope_key=? GROUP BY status
                    """,
                    (self.scope_key,),
                )
            }
            bounds = connection.execute(
                """
                SELECT min(sequence) oldest,max(sequence) newest,count(*) total
                FROM realtime_events WHERE scope_key=?
                """,
                (self.scope_key,),
            ).fetchone()
            visible_bounds = connection.execute(
                """
                SELECT min(sequence) oldest,max(sequence) newest,count(*) total
                FROM realtime_events WHERE scope_key=? AND status='applied'
                  AND sequence < coalesce((
                      SELECT min(sequence) FROM realtime_events
                      WHERE scope_key=? AND normalized_json IS NOT NULL
                        AND status IN ('accepted','pending')
                  ), 9223372036854775807)
                """,
                (self.scope_key, self.scope_key),
            ).fetchone()
            duplicate_row = connection.execute(
                "SELECT value FROM realtime_meta WHERE key=?",
                (self._duplicates_key,),
            ).fetchone()
        return {
            "enabled": True,
            "cache_path": str(self.path),
            "total": int(bounds["total"] or 0),
            "oldest_sequence": int(visible_bounds["oldest"] or 0),
            "newest_sequence": int(visible_bounds["newest"] or 0),
            "visible_total": int(visible_bounds["total"] or 0),
            "counts": counts,
            "duplicate_attempts": int(duplicate_row[0] if duplicate_row else 0),
            "live_queue_size": len(self._recent),
            "live_queue_limit": REALTIME_LIVE_QUEUE_LIMIT,
            "replay_batch_limit": REALTIME_REPLAY_BATCH,
            "subscribers": self._subscribers,
            "source_read_only": True,
            "account": self.account,
            "snapshot": self.snapshot,
            "scope": self.scope_key[:12],
        }

    def subscriber_opened(self) -> None:
        with self._lock:
            self._subscribers += 1

    def subscriber_closed(self) -> None:
        with self._lock:
            self._subscribers = max(0, self._subscribers - 1)
