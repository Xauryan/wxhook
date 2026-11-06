from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


def readonly_uri(path: Path) -> str:
    if not path.is_file():
        raise FileNotFoundError(path)
    return path.resolve().as_uri() + "?mode=ro&immutable=1"


@contextmanager
def open_readonly(path: Path) -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(readonly_uri(path), uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    try:
        yield connection
    finally:
        connection.close()


@contextmanager
def open_fts(
    path: Path, extension: Path, weixin_dll: Path
) -> Iterator[sqlite3.Connection]:
    if not extension.is_file():
        raise FileNotFoundError(extension)
    if not weixin_dll.is_file():
        raise FileNotFoundError(weixin_dll)
    os.environ["WEIXIN_DLL_PATH"] = str(weixin_dll.resolve())
    connection = sqlite3.connect(readonly_uri(path), uri=True)
    connection.row_factory = sqlite3.Row
    try:
        connection.enable_load_extension(True)
        connection.load_extension(str(extension.resolve()))
        connection.enable_load_extension(False)
        connection.execute("PRAGMA query_only=ON")
        yield connection
    finally:
        connection.close()


def quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'
