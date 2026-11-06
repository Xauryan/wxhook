from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from contextlib import closing
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATABASE_ROOT = PROJECT_ROOT / "runtime" / "db_decrypt" / "all"
DEFAULT_EXTENSION = PROJECT_ROOT / "dist" / "weixin_fts_tokenizer.dll"
DEFAULT_WEIXIN_DLL = Path(
    r"D:\Program Files\Tencent\Weixin\4.1.9.23\Weixin.dll"
)

DATABASE_PATHS = {
    "contact": Path("contact/contact_fts.db"),
    "favorite": Path("favorite/favorite_fts.db"),
    "message": Path("message/message_fts.db"),
}


class QueryError(RuntimeError):
    pass


def parse_limit(value: str) -> int:
    try:
        limit = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("limit must be an integer") from exc
    if not 1 <= limit <= 500:
        raise argparse.ArgumentTypeError("limit must be between 1 and 500")
    return limit


def json_default(value: Any) -> Any:
    if isinstance(value, bytes):
        return {"hex": value.hex()}
    raise TypeError(f"cannot serialize {type(value).__name__}")


def escape_unencodable(text: str, encoding: str | None) -> str:
    if not encoding:
        return text
    escaped = []
    for character in text:
        try:
            character.encode(encoding)
        except UnicodeEncodeError:
            codepoint = ord(character)
            if codepoint <= 0xFFFF:
                escaped.append(f"\\u{codepoint:04x}")
            else:
                codepoint -= 0x10000
                high = 0xD800 + (codepoint >> 10)
                low = 0xDC00 + (codepoint & 0x3FF)
                escaped.append(f"\\u{high:04x}\\u{low:04x}")
        else:
            escaped.append(character)
    return "".join(escaped)


def print_json(value: Any) -> None:
    rendered = json.dumps(
        value,
        ensure_ascii=False,
        indent=2,
        default=json_default,
    )
    print(escape_unencodable(rendered, sys.stdout.encoding))


def open_database(
    path: Path,
    extension: Path,
    weixin_dll: Path,
) -> sqlite3.Connection:
    if not path.is_file():
        raise QueryError(f"database does not exist: {path}")
    if not extension.is_file():
        raise QueryError(
            "tokenizer extension does not exist; run "
            "native\\weixin_fts_tokenizer\\build.ps1"
        )
    if not weixin_dll.is_file():
        raise QueryError(f"Weixin.dll does not exist: {weixin_dll}")

    os.environ["WEIXIN_DLL_PATH"] = str(weixin_dll.resolve())
    uri = path.resolve().as_uri() + "?mode=ro&immutable=1"
    connection = sqlite3.connect(uri, uri=True)
    try:
        connection.enable_load_extension(True)
        connection.load_extension(str(extension.resolve()))
        connection.enable_load_extension(False)
        connection.execute("PRAGMA query_only=ON")
        return connection
    except BaseException:
        connection.close()
        raise


def row_to_dict(cursor: sqlite3.Cursor, row: tuple[Any, ...]) -> dict[str, Any]:
    return {
        column[0]: value for column, value in zip(cursor.description, row, strict=True)
    }


def query_table(
    connection: sqlite3.Connection,
    table: str,
    columns: str,
    query: str,
    limit: int,
) -> list[dict[str, Any]]:
    sql = (
        f'SELECT {columns}, rank FROM "{table}" '
        f'WHERE "{table}" MATCH ? ORDER BY rank LIMIT ?'
    )
    cursor = connection.execute(sql, (query, limit))
    return [row_to_dict(cursor, row) for row in cursor.fetchall()]


def search_message(
    connection: sqlite3.Connection, query: str, limit: int
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    columns = (
        "rowid, acontent, message_local_id, sort_seq, local_type, "
        "session_id, sender_id, create_time"
    )
    for shard in range(4):
        table = f"message_fts_v4_{shard}"
        for row in query_table(connection, table, columns, query, limit):
            row["shard"] = shard
            results.append(row)
    results.sort(key=lambda row: float(row.get("rank") or 0.0))
    return results[:limit]


def search_contact(
    connection: sqlite3.Connection, query: str, limit: int
) -> list[dict[str, Any]]:
    results = query_table(
        connection,
        "contact_fts_v4",
        "rowid, search_key, local_type",
        query,
        limit,
    )
    for row in results:
        row["mode"] = "origin"

    # The pinyin index uses contact_fts_v4 as external content. Asking SQLite
    # 3.45 for its content columns or rank fails, so resolve rowids separately.
    hit_rows = connection.execute(
        'SELECT rowid FROM "contact_fts_pinyin_v4" '
        'WHERE "contact_fts_pinyin_v4" MATCH ? LIMIT ?',
        (query, limit),
    ).fetchall()
    rowids = [int(row[0]) for row in hit_rows]
    if rowids:
        placeholders = ",".join("?" for _ in rowids)
        cursor = connection.execute(
            "SELECT id AS rowid, c0 AS search_key, c1 AS local_type "
            f"FROM contact_fts_v4_content WHERE id IN ({placeholders})",
            rowids,
        )
        content_by_rowid = {
            int(row["rowid"]): row
            for row in (row_to_dict(cursor, item) for item in cursor.fetchall())
        }
        for rowid in rowids:
            row = content_by_rowid.get(rowid)
            if row is None:
                continue
            row["rank"] = None
            row["mode"] = "pinyin"
            results.append(row)

    results.sort(
        key=lambda row: (
            row.get("rank") is None,
            float(row.get("rank") or 0.0),
        )
    )
    return results[:limit]


def search_favorite(
    connection: sqlite3.Connection, query: str, limit: int
) -> list[dict[str, Any]]:
    return query_table(
        connection,
        "fav_fts_v1",
        "rowid, content, local_id, update_time, type",
        query,
        limit,
    )


def command_search(args: argparse.Namespace) -> int:
    database_path = args.database_root / DATABASE_PATHS[args.database]
    with closing(
        open_database(database_path, args.extension, args.weixin_dll)
    ) as connection:
        if args.database == "message":
            rows = search_message(connection, args.query, args.limit)
        elif args.database == "contact":
            rows = search_contact(connection, args.query, args.limit)
        else:
            rows = search_favorite(connection, args.query, args.limit)
    print_json(
        {
            "database": args.database,
            "query": args.query,
            "count": len(rows),
            "rows": rows,
        }
    )
    return 0


def command_validate(args: argparse.Namespace) -> int:
    results = {}
    for name, relative_path in DATABASE_PATHS.items():
        path = args.database_root / relative_path
        with closing(open_database(path, args.extension, args.weixin_dll)) as connection:
            quick_check = [row[0] for row in connection.execute("PRAGMA quick_check")]
            integrity_check = [
                row[0] for row in connection.execute("PRAGMA integrity_check")
            ]
            results[name] = {
                "path": str(path.resolve()),
                "quick_check": quick_check,
                "integrity_check": integrity_check,
            }
    print_json(results)
    return 0 if all(
        value["quick_check"] == ["ok"]
        and value["integrity_check"] == ["ok"]
        for value in results.values()
    ) else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Query decrypted Weixin FTS5 databases with MMFtsTokenizer."
    )
    parser.add_argument(
        "--database-root", type=Path, default=DEFAULT_DATABASE_ROOT
    )
    parser.add_argument("--extension", type=Path, default=DEFAULT_EXTENSION)
    parser.add_argument("--weixin-dll", type=Path, default=DEFAULT_WEIXIN_DLL)
    subparsers = parser.add_subparsers(dest="command", required=True)

    search = subparsers.add_parser("search", help="run an FTS5 MATCH query")
    search.add_argument("database", choices=sorted(DATABASE_PATHS))
    search.add_argument("query")
    search.add_argument("--limit", type=parse_limit, default=20, metavar="1..500")
    search.set_defaults(func=command_search)

    validate = subparsers.add_parser(
        "validate", help="run quick_check and integrity_check on all FTS databases"
    )
    validate.set_defaults(func=command_validate)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        return args.func(args)
    except (QueryError, OSError, sqlite3.DatabaseError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
