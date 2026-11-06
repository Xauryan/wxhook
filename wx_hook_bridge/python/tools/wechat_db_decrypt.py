from __future__ import annotations

import argparse
import ctypes
import hashlib
import hmac
import json
import os
import re
import shutil
import uuid
import sqlite3
import struct
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

try:
    from Cryptodome.Cipher import AES
except ImportError:
    try:
        from Crypto.Cipher import AES
    except ImportError as exc:
        raise SystemExit("PyCryptodome is required: pip install pycryptodomex") from exc


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "runtime" / "db_decrypt"
DEFAULT_KEY_FILE = DEFAULT_OUTPUT_DIR / "keys.json"
DEFAULT_FTS_EXTENSION = PROJECT_ROOT / "dist" / "weixin_fts_tokenizer.dll"
DEFAULT_WEIXIN_DLL = Path(
    r"D:\Program Files\Tencent\Weixin\4.1.9.23\Weixin.dll"
)

SQLITE_HEADER = b"SQLite format 3\0"
DATABASE_SUFFIXES = {".db", ".kvdb"}
CODEC_PREFIX = bytes.fromhex(
    "0000000000e80300020000001000000020000000100000001000000000100000"
    "630000005000000040000000000000000200000002000000"
)
KEYSPEC_RE = re.compile(rb"x'[0-9a-f]{96}'")

PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_VM_READ = 0x0010
MEM_COMMIT = 0x1000
MEM_PRIVATE = 0x20000
PAGE_GUARD = 0x100
PAGE_NOACCESS = 0x01
READABLE_PAGE_TYPES = {0x02, 0x04, 0x08, 0x20, 0x40, 0x80}


class DecryptError(RuntimeError):
    pass


if os.name == "nt":
    from ctypes import wintypes

    SIZE_T = ctypes.c_size_t
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    class MEMORY_BASIC_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("BaseAddress", ctypes.c_void_p),
            ("AllocationBase", ctypes.c_void_p),
            ("AllocationProtect", wintypes.DWORD),
            ("PartitionId", wintypes.WORD),
            ("_padding", wintypes.WORD),
            ("RegionSize", SIZE_T),
            ("State", wintypes.DWORD),
            ("Protect", wintypes.DWORD),
            ("Type", wintypes.DWORD),
            ("_padding2", wintypes.DWORD),
        ]

    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.ReadProcessMemory.argtypes = [
        wintypes.HANDLE,
        ctypes.c_void_p,
        ctypes.c_void_p,
        SIZE_T,
        ctypes.POINTER(SIZE_T),
    ]
    kernel32.ReadProcessMemory.restype = wintypes.BOOL
    kernel32.VirtualQueryEx.argtypes = [
        wintypes.HANDLE,
        ctypes.c_void_p,
        ctypes.POINTER(MEMORY_BASIC_INFORMATION),
        SIZE_T,
    ]
    kernel32.VirtualQueryEx.restype = SIZE_T
    kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL


@dataclass(frozen=True)
class MemoryRegion:
    base: int
    size: int
    protect: int
    kind: int


class RemoteProcess:
    def __init__(self, pid: int) -> None:
        if os.name != "nt":
            raise DecryptError("process extraction is only supported on Windows")
        if ctypes.sizeof(ctypes.c_void_p) != 8:
            raise DecryptError("64-bit Python is required for 64-bit Weixin")
        if ctypes.sizeof(MEMORY_BASIC_INFORMATION) != 48:
            raise DecryptError("unexpected MEMORY_BASIC_INFORMATION layout")
        self.pid = pid
        self.handle: Any = None

    def __enter__(self) -> "RemoteProcess":
        self.handle = kernel32.OpenProcess(
            PROCESS_QUERY_INFORMATION | PROCESS_VM_READ,
            False,
            self.pid,
        )
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        if self.handle:
            kernel32.CloseHandle(self.handle)
            self.handle = None

    def image_path(self) -> str:
        buffer = ctypes.create_unicode_buffer(32768)
        size = wintypes.DWORD(len(buffer))
        if not kernel32.QueryFullProcessImageNameW(
            self.handle, 0, buffer, ctypes.byref(size)
        ):
            return ""
        return buffer.value

    def read(self, address: int, size: int) -> bytes:
        if address <= 0 or size <= 0 or address + size > 0x800000000000:
            return b""
        buffer = ctypes.create_string_buffer(size)
        read_size = SIZE_T(0)
        ok = kernel32.ReadProcessMemory(
            self.handle,
            ctypes.c_void_p(address),
            buffer,
            size,
            ctypes.byref(read_size),
        )
        if not ok and read_size.value == 0:
            return b""
        return buffer.raw[: read_size.value]

    def read_exact(self, address: int, size: int) -> bytes:
        data = self.read(address, size)
        if len(data) != size:
            raise DecryptError(f"short remote read at 0x{address:x}")
        return data

    def regions(self, private_only: bool) -> Iterator[MemoryRegion]:
        address = 0
        maximum = 0x7FFFFFFFFFFF
        while address < maximum:
            mbi = MEMORY_BASIC_INFORMATION()
            result = kernel32.VirtualQueryEx(
                self.handle,
                ctypes.c_void_p(address),
                ctypes.byref(mbi),
                ctypes.sizeof(mbi),
            )
            if not result:
                break
            base = int(mbi.BaseAddress or 0)
            size = int(mbi.RegionSize)
            next_address = base + size
            if size <= 0 or next_address <= address:
                break

            base_protect = int(mbi.Protect) & 0xFF
            readable = (
                mbi.State == MEM_COMMIT
                and not (mbi.Protect & PAGE_GUARD)
                and base_protect != PAGE_NOACCESS
                and base_protect in READABLE_PAGE_TYPES
                and (not private_only or mbi.Type == MEM_PRIVATE)
            )
            if readable:
                yield MemoryRegion(base, size, int(mbi.Protect), int(mbi.Type))
            address = next_address


def scan_pattern(
    process: RemoteProcess,
    pattern: bytes,
    chunk_size: int,
    private_only: bool,
) -> tuple[list[int], dict[str, int]]:
    hits: set[int] = set()
    stats = {
        "regions": 0,
        "bytes_read": 0,
        "read_failures": 0,
    }
    overlap_size = len(pattern) - 1

    for region in process.regions(private_only):
        stats["regions"] += 1
        offset = 0
        tail = b""
        while offset < region.size:
            requested = min(chunk_size, region.size - offset)
            data = process.read(region.base + offset, requested)
            if not data:
                stats["read_failures"] += 1
                offset += min(0x1000, region.size - offset)
                tail = b""
                continue

            stats["bytes_read"] += len(data)
            combined = tail + data
            combined_base = region.base + offset - len(tail)
            start = 0
            while True:
                index = combined.find(pattern, start)
                if index < 0:
                    break
                hits.add(combined_base + index)
                start = index + 1

            tail = combined[-overlap_size:] if overlap_size else b""
            offset += len(data)

    return sorted(hits), stats


def _u64(data: bytes, offset: int) -> int:
    return struct.unpack_from("<Q", data, offset)[0]


def parse_codec_context(process: RemoteProcess, address: int) -> list[dict[str, Any]]:
    prefix_before = process.read_exact(address, len(CODEC_PREFIX))
    if prefix_before != CODEC_PREFIX:
        return []

    codec = process.read_exact(address, 0x88)
    fields = struct.unpack_from("<15I", codec, 0)
    salt_ptr = _u64(codec, 0x48)
    hmac_salt_ptr = _u64(codec, 0x50)
    read_ctx_ptr = _u64(codec, 0x68)
    write_ctx_ptr = _u64(codec, 0x70)
    salt = process.read_exact(salt_ptr, fields[3])
    hmac_salt = process.read_exact(hmac_salt_ptr, fields[3])
    expected_hmac_salt = bytes(value ^ 0x3A for value in salt)
    if hmac_salt != expected_hmac_salt:
        return []

    observations: list[dict[str, Any]] = []
    seen_cipher_contexts: dict[int, dict[str, Any]] = {}
    for role, cipher_address in (("read", read_ctx_ptr), ("write", write_ctx_ptr)):
        if not cipher_address:
            continue
        if cipher_address in seen_cipher_contexts:
            seen_cipher_contexts[cipher_address]["roles"].append(role)
            continue

        cipher = process.read_exact(cipher_address, 0x28)
        derive_key, pass_size, key_ptr, hmac_key_ptr, _, keyspec_ptr = struct.unpack(
            "<iiQQQQ", cipher
        )
        key = process.read_exact(key_ptr, fields[4])
        remote_hmac_key = process.read_exact(hmac_key_ptr, fields[4])
        keyspec = process.read_exact(keyspec_ptr, fields[8])
        if not KEYSPEC_RE.fullmatch(keyspec):
            continue

        expected_keyspec = b"x'" + key.hex().encode("ascii") + salt.hex().encode("ascii") + b"'"
        if keyspec != expected_keyspec:
            continue
        expected_hmac_key = hashlib.pbkdf2_hmac(
            "sha512", key, hmac_salt, fields[2], dklen=fields[4]
        )
        if remote_hmac_key != expected_hmac_key:
            continue

        observation = {
            "codec_context": address,
            "cipher_context": cipher_address,
            "roles": [role],
            "derive_key": derive_key,
            "pass_size": pass_size,
            "salt": salt,
            "hmac_salt": hmac_salt,
            "key": key,
            "hmac_key": remote_hmac_key,
            "keyspec": keyspec.decode("ascii"),
            "flags": fields[14],
        }
        observations.append(observation)
        seen_cipher_contexts[cipher_address] = observation

    prefix_after = process.read_exact(address, len(CODEC_PREFIX))
    return observations if prefix_after == CODEC_PREFIX else []


def build_key_manifest(
    pid: int,
    chunk_size: int,
    private_only: bool,
) -> dict[str, Any]:
    with RemoteProcess(pid) as process:
        image_path = process.image_path()
        hits, scan_stats = scan_pattern(
            process,
            CODEC_PREFIX,
            chunk_size=chunk_size,
            private_only=private_only,
        )
        observations: list[dict[str, Any]] = []
        rejected: list[str] = []
        for address in hits:
            try:
                parsed = parse_codec_context(process, address)
            except (DecryptError, OSError, struct.error):
                parsed = []
            if parsed:
                observations.extend(parsed)
            else:
                rejected.append(f"0x{address:x}")

    groups: dict[tuple[bytes, bytes, bytes], dict[str, Any]] = {}
    salt_variants: dict[bytes, set[tuple[bytes, bytes]]] = {}
    for item in observations:
        group_id = (item["salt"], item["key"], item["hmac_key"])
        salt_variants.setdefault(item["salt"], set()).add(
            (item["key"], item["hmac_key"])
        )
        group = groups.setdefault(
            group_id,
            {
                "salt_hex": item["salt"].hex(),
                "key_hex": item["key"].hex(),
                "hmac_key_hex": item["hmac_key"].hex(),
                "hmac_kdf_salt_hex": item["hmac_salt"].hex(),
                "keyspec": item["keyspec"],
                "codec_contexts": set(),
                "cipher_contexts": {},
            },
        )
        group["codec_contexts"].add(item["codec_context"])
        context = group["cipher_contexts"].setdefault(
            item["cipher_context"],
            {
                "address": item["cipher_context"],
                "roles": set(),
                "derive_key": item["derive_key"],
                "pass_size": item["pass_size"],
            },
        )
        context["roles"].update(item["roles"])

    conflicts = [salt.hex() for salt, variants in salt_variants.items() if len(variants) > 1]
    keys: list[dict[str, Any]] = []
    for group in sorted(groups.values(), key=lambda value: value["salt_hex"]):
        cipher_contexts = []
        for context in sorted(
            group["cipher_contexts"].values(), key=lambda value: value["address"]
        ):
            cipher_contexts.append(
                {
                    "address": f"0x{context['address']:x}",
                    "roles": sorted(context["roles"]),
                    "derive_key": context["derive_key"],
                    "pass_size": context["pass_size"],
                }
            )
        keys.append(
            {
                "salt_hex": group["salt_hex"],
                "key_hex": group["key_hex"],
                "hmac_key_hex": group["hmac_key_hex"],
                "hmac_kdf_salt_hex": group["hmac_kdf_salt_hex"],
                "keyspec": group["keyspec"],
                "codec_contexts": [
                    f"0x{address:x}" for address in sorted(group["codec_contexts"])
                ],
                "cipher_contexts": cipher_contexts,
            }
        )

    validated_codec_contexts = {
        item["codec_context"] for item in observations
    }
    scan_stats.update(
        {
            "raw_hits": len(hits),
            "validated_codec_contexts": len(validated_codec_contexts),
            "validated_cipher_sides": sum(len(item["roles"]) for item in observations),
            "unique_keys": len(keys),
            "rejected": rejected,
            "private_only": private_only,
        }
    )
    manifest = {
        "schema_version": 1,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "process": {
            "pid": pid,
            "image_path": image_path,
            "architecture": "x64",
        },
        "sqlcipher": {
            "page_size": 4096,
            "kdf_algorithm": "PBKDF2-HMAC-SHA512",
            "kdf_iterations": 256000,
            "fast_kdf_iterations": 2,
            "salt_size": 16,
            "key_size": 32,
            "iv_size": 16,
            "reserve_size": 80,
            "hmac_algorithm": "HMAC-SHA512",
            "hmac_size": 64,
            "page_number_byte_order": "little",
        },
        "scan": scan_stats,
        "conflicting_salts": conflicts,
        "keys": keys,
    }
    if conflicts:
        raise DecryptError(
            "conflicting read/write keys detected for salts: " + ", ".join(conflicts)
        )
    if not keys:
        raise DecryptError("no validated SQLCipher contexts found")
    return manifest


def write_json_atomic(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, ensure_ascii=True, indent=2)
            stream.write("\n")
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def publish_directory_snapshot(
    staging_root: Path,
    published_root: Path,
    report_path: Path | None,
    report_factory: Callable[[], dict[str, Any]] | None,
    *,
    replace_existing: bool = False,
) -> None:
    published_root.parent.mkdir(parents=True, exist_ok=True)
    if published_root.exists() and not replace_existing:
        raise DecryptError(f"snapshot publication target already exists: {published_root}")
    backup_root: Path | None = None
    previous_moved = False
    new_published = False

    def remove_path(path: Path) -> None:
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path, ignore_errors=True)
        else:
            path.unlink(missing_ok=True)

    try:
        if published_root.exists():
            backup_root = published_root.with_name(
                f".{published_root.name}.{uuid.uuid4().hex}.backup"
            )
            os.replace(published_root, backup_root)
            previous_moved = True
        os.replace(staging_root, published_root)
        new_published = True
        if report_path is not None and report_factory is not None:
            write_json_atomic(report_path, report_factory())
    except BaseException:
        if new_published:
            remove_path(published_root)
        if previous_moved and backup_root is not None and backup_root.exists():
            try:
                os.replace(backup_root, published_root)
            except OSError:
                # Keep the prior snapshot in its backup location rather than
                # deleting it when a Windows reader still holds the directory.
                pass
        raise
    else:
        if backup_root is not None:
            # An active reader can keep the old tree undeletable on Windows;
            # publication is already complete, so cleanup is best effort.
            try:
                remove_path(backup_root)
            except OSError:
                pass



def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()



def infer_account(source: Path) -> str | None:
    candidates = [source if source.is_dir() else source.parent, *source.parents]
    for candidate in candidates:
        name = candidate.name
        if not name.startswith("wxid_"):
            continue
        account = re.sub(r"_[0-9a-fA-F]{4}$", "", name)
        if account.startswith("wxid_") and len(account) > len("wxid_"):
            return account
    return None



def validate_decrypt_paths(
    *,
    source: Path,
    key_manifest: Path,
    jobs: list[tuple[Path, Path]],
    report: Path | None,
) -> None:
    source = source.resolve()
    key_manifest = key_manifest.resolve()
    inputs = {input_path.resolve() for input_path, _ in jobs}
    outputs = [output_path.resolve() for _, output_path in jobs]

    if len(set(outputs)) != len(outputs):
        raise DecryptError("multiple encrypted inputs resolve to the same output path")
    for output in outputs:
        if output in inputs or output == key_manifest:
            raise DecryptError(f"output path collides with an input or key manifest: {output}")

    if report is not None:
        report = report.resolve()
        collisions = inputs | set(outputs) | {key_manifest}
        if report in collisions:
            raise DecryptError(f"report path collides with an input, output, or key manifest: {report}")

    source_boundary = source if source.is_dir() else source.parent
    nested_outputs = [path for path in outputs if path.is_relative_to(source_boundary)]
    if nested_outputs:
        raise DecryptError(
            "output path must be outside the encrypted source tree: "
            f"{nested_outputs[0]}"
        )
    if report is None:
        return
    if report.is_relative_to(source_boundary):
        raise DecryptError(f"report path must be outside the encrypted source tree: {report}")



def build_decryption_report(
    *,
    account: str,
    source_root: Path,
    key_manifest: Path,
    output_root: Path,
    results: list[dict[str, Any]],
    failures: list[dict[str, Any]],
    reserve_size: int,
) -> dict[str, Any]:
    files: list[dict[str, Any]] = []
    for result in results:
        output = Path(result["output"]).resolve()
        try:
            relative = output.relative_to(output_root.resolve()).as_posix()
        except ValueError:
            relative = output.name
        files.append(
            {
                "path": relative,
                "bytes": int(result["bytes"]),
                "pages": int(result["pages"]),
                "sha256": sha256_file(output),
                "sqlite_header": True,
                "reserve_size": reserve_size,
                "quick_check": result.get("quick_check"),
                "integrity_check": result.get("integrity_check"),
                "page_count": result.get("page_count"),
                "page_size": result.get("page_size"),
                "freelist_count": result.get("freelist_count"),
                "schema_objects": result.get("schema_objects"),
                "schema_preview": result.get("schema_preview", []),
                "source_wal": result.get("wal", {}),
                "fts_extension_loaded": bool(
                    result.get("fts_extension_loaded", False)
                ),
            }
        )

    unresolved = []
    for failure in failures:
        input_path = Path(failure["input"])
        try:
            relative = input_path.resolve().relative_to(source_root.resolve()).as_posix()
        except ValueError:
            relative = input_path.name
        unresolved.append({"path": relative, "error": failure["error"]})

    return {
        "schema_version": 1,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "account": account,
        "source_root": str(source_root.resolve()),
        "key_manifest": str(key_manifest.resolve()),
        "output_root": str(output_root.resolve()),
        "decrypted_database_count": len(files),
        "total_bytes": sum(int(item["bytes"]) for item in files),
        "unresolved": unresolved,
        "files": files,
    }



def wal_checksum(data: bytes, little_endian: bool, state: tuple[int, int]) -> tuple[int, int]:
    if len(data) % 8:
        raise DecryptError("WAL checksum input is not a multiple of 8 bytes")
    endian = "<" if little_endian else ">"
    words = struct.unpack(f"{endian}{len(data) // 4}I", data)
    first, second = state
    for index in range(0, len(words), 2):
        first = (first + words[index] + second) & 0xFFFFFFFF
        second = (second + words[index + 1] + first) & 0xFFFFFFFF
    return first, second


def inspect_wal(database_path: Path, page_size: int) -> dict[str, Any]:
    wal_path = Path(str(database_path) + "-wal")
    result: dict[str, Any] = {
        "path": str(wal_path),
        "exists": wal_path.exists(),
        "size": 0,
        "mtime_ns": None,
        "sha256": None,
        "header_valid": False,
        "physical_frames": 0,
        "trailing_bytes": 0,
        "valid_frames": 0,
        "commit_frames": 0,
        "last_commit_frame": None,
        "last_commit_database_size": None,
        "rolling_checksum": None,
        "stop_reason": "missing",
    }
    if not wal_path.exists():
        return result

    stat_before = wal_path.stat()
    data = wal_path.read_bytes()
    stat_after = wal_path.stat()
    result["size"] = len(data)
    result["mtime_ns"] = stat_after.st_mtime_ns
    result["sha256"] = hashlib.sha256(data).hexdigest()
    if (
        stat_before.st_size != stat_after.st_size
        or stat_before.st_mtime_ns != stat_after.st_mtime_ns
        or len(data) != stat_after.st_size
    ):
        result["stop_reason"] = "changed_during_read"
        return result
    if not data:
        result["stop_reason"] = "empty"
        return result
    if len(data) < 32:
        result["stop_reason"] = "short_header"
        return result

    header = data[:32]
    magic, version, wal_page_size, _, salt1, salt2, checksum1, checksum2 = struct.unpack(
        ">8I", header
    )
    if magic not in (0x377F0682, 0x377F0683):
        result["stop_reason"] = "bad_magic"
        return result
    if version != 3007000 or wal_page_size != page_size:
        result["stop_reason"] = "unsupported_header"
        return result
    little_endian = magic == 0x377F0682
    calculated = wal_checksum(header[:24], little_endian, (0, 0))
    if calculated != (checksum1, checksum2):
        result["stop_reason"] = "bad_header_checksum"
        return result

    result["header_valid"] = True
    frame_size = 24 + page_size
    payload_size = len(data) - 32
    result["physical_frames"] = payload_size // frame_size
    result["trailing_bytes"] = payload_size % frame_size
    result["stop_reason"] = "no_frames"
    rolling = (checksum1, checksum2)
    for frame_index in range(1, result["physical_frames"] + 1):
        offset = 32 + (frame_index - 1) * frame_size
        frame = data[offset : offset + frame_size]
        page_number, database_size, frame_salt1, frame_salt2, stored1, stored2 = struct.unpack(
            ">6I", frame[:24]
        )
        if page_number == 0:
            result["stop_reason"] = "zero_page_number"
            break
        if (frame_salt1, frame_salt2) != (salt1, salt2):
            result["stop_reason"] = "salt_mismatch"
            break
        calculated = wal_checksum(frame[:8] + frame[24:], little_endian, rolling)
        if calculated != (stored1, stored2):
            result["stop_reason"] = "frame_checksum_mismatch"
            break
        rolling = calculated
        result["valid_frames"] = frame_index
        result["rolling_checksum"] = [rolling[0], rolling[1]]
        if database_size:
            result["commit_frames"] += 1
            result["last_commit_frame"] = frame_index
            result["last_commit_database_size"] = database_size
        result["stop_reason"] = "complete"
    if result["stop_reason"] in ("complete", "no_frames") and result["trailing_bytes"]:
        result["stop_reason"] = "partial_tail"
    return result


def wal_snapshot_id(value: dict[str, Any]) -> tuple[Any, ...]:
    return (
        value.get("exists"),
        value.get("size"),
        value.get("sha256"),
    )


def verify_page_hmac(
    page: bytes,
    page_number: int,
    hmac_key: bytes,
    page_size: int,
    reserve_size: int,
    hmac_size: int,
    salt_size: int,
) -> bool:
    authenticated_end = page_size - hmac_size
    start = salt_size if page_number == 1 else 0
    calculated = hmac.new(
        hmac_key,
        page[start:authenticated_end] + struct.pack("<I", page_number),
        hashlib.sha512,
    ).digest()
    return hmac.compare_digest(calculated, page[authenticated_end:])


def choose_key(manifest: dict[str, Any], first_page: bytes) -> dict[str, Any]:
    params = manifest["sqlcipher"]
    salt_size = int(params["salt_size"])
    salt_hex = first_page[:salt_size].hex()
    candidates = [
        item for item in manifest.get("keys", []) if item.get("salt_hex") == salt_hex
    ]
    valid = []
    for item in candidates:
        hmac_key = bytes.fromhex(item["hmac_key_hex"])
        if verify_page_hmac(
            first_page,
            1,
            hmac_key,
            int(params["page_size"]),
            int(params["reserve_size"]),
            int(params["hmac_size"]),
            salt_size,
        ):
            valid.append(item)
    if len(valid) != 1:
        raise DecryptError(
            f"expected one key for salt {salt_hex}, found {len(valid)} valid candidates"
        )
    return valid[0]


def decrypt_page(
    encrypted: bytes,
    page_number: int,
    key: bytes,
    hmac_key: bytes,
    params: dict[str, Any],
) -> bytes:
    page_size = int(params["page_size"])
    reserve_size = int(params["reserve_size"])
    iv_size = int(params["iv_size"])
    hmac_size = int(params["hmac_size"])
    salt_size = int(params["salt_size"])
    if len(encrypted) != page_size:
        raise DecryptError(f"page {page_number}: short page")
    locking_page = 0x40000000 // page_size + 1
    if page_number == locking_page:
        return bytes(page_size)
    if reserve_size != iv_size + hmac_size:
        raise DecryptError("unsupported SQLCipher reserve layout")
    if not verify_page_hmac(
        encrypted,
        page_number,
        hmac_key,
        page_size,
        reserve_size,
        hmac_size,
        salt_size,
    ):
        raise DecryptError(f"page {page_number}: HMAC mismatch")

    ciphertext_start = salt_size if page_number == 1 else 0
    ciphertext_end = page_size - reserve_size
    iv_end = ciphertext_end + iv_size
    ciphertext = encrypted[ciphertext_start:ciphertext_end]
    if len(ciphertext) % AES.block_size:
        raise DecryptError(f"page {page_number}: invalid ciphertext length")
    plaintext = AES.new(
        key,
        AES.MODE_CBC,
        encrypted[ciphertext_end:iv_end],
    ).decrypt(ciphertext)

    if page_number == 1:
        plaintext = SQLITE_HEADER + plaintext
    output = plaintext + bytes(reserve_size)
    if len(output) != page_size:
        raise DecryptError(f"page {page_number}: invalid output page length")
    return output


def verify_plain_database(
    path: Path,
    fts_extension: Path | None = None,
    weixin_dll: Path | None = None,
    allow_unsupported_tokenizer: bool = False,
) -> dict[str, Any]:
    uri = path.resolve().as_uri() + "?mode=ro&immutable=1"
    connection = sqlite3.connect(uri, uri=True)
    try:
        requires_mm_tokenizer = connection.execute(
            "SELECT 1 FROM sqlite_master "
            "WHERE sql LIKE '%MMFtsTokenizer%' LIMIT 1"
        ).fetchone() is not None
        extension_loaded = False
        if requires_mm_tokenizer:
            extension = fts_extension
            if extension is None and DEFAULT_FTS_EXTENSION.is_file():
                extension = DEFAULT_FTS_EXTENSION
            if extension is not None and extension.is_file():
                configured_dll = os.getenv("WEIXIN_DLL_PATH")
                dll_path = weixin_dll or (Path(configured_dll) if configured_dll else DEFAULT_WEIXIN_DLL)
                os.environ["WEIXIN_DLL_PATH"] = str(dll_path.resolve())
                connection.enable_load_extension(True)
                connection.load_extension(str(extension.resolve()))
                connection.enable_load_extension(False)
                extension_loaded = True
        try:
            quick_check = [row[0] for row in connection.execute("PRAGMA quick_check")]
            if quick_check != ["ok"]:
                raise DecryptError(
                    "PRAGMA quick_check failed: " + " | ".join(quick_check)
                )
            quick_check_status = "ok"
            integrity_check = [
                row[0] for row in connection.execute("PRAGMA integrity_check")
            ]
            if integrity_check != ["ok"]:
                raise DecryptError(
                    "PRAGMA integrity_check failed: " + " | ".join(integrity_check)
                )
            integrity_check_status = "ok"
        except sqlite3.DatabaseError as exc:
            message = str(exc)
            missing_extension = (
                message.startswith("no such tokenizer:")
                or message.startswith("no such module:")
            )
            unsupported_tokenizer = (
                allow_unsupported_tokenizer and requires_mm_tokenizer
                and message == 'SQL logic error'
            )
            if not missing_extension and not unsupported_tokenizer:
                raise
            reason = 'skipped_unsupported_tokenizer' if unsupported_tokenizer else 'skipped_missing_extension'
            quick_check_status = f"{reason}: {message}"
            integrity_check_status = f"{reason}: {message}"
        page_count = connection.execute("PRAGMA page_count").fetchone()[0]
        page_size = connection.execute("PRAGMA page_size").fetchone()[0]
        freelist_count = connection.execute("PRAGMA freelist_count").fetchone()[0]
        file_pages = path.stat().st_size // page_size
        if page_count != file_pages:
            raise DecryptError(
                f"SQLite page_count {page_count} does not match file pages {file_pages}"
            )
        schema_objects = connection.execute(
            "SELECT count(*) FROM sqlite_master"
        ).fetchone()[0]
        schema_preview = connection.execute(
            "SELECT type, name FROM sqlite_master ORDER BY type, name LIMIT 20"
        ).fetchall()
        return {
            "quick_check": quick_check_status,
            "integrity_check": integrity_check_status,
            "page_count": page_count,
            "page_size": page_size,
            "freelist_count": freelist_count,
            "fts_extension_loaded": extension_loaded,
            "schema_objects": schema_objects,
            "schema_preview": [list(row) for row in schema_preview],
        }
    finally:
        connection.close()


def decrypt_database(
    source: Path,
    output: Path,
    manifest: dict[str, Any],
    force: bool,
    ignore_wal: bool,
    fts_extension: Path | None = None,
    weixin_dll: Path | None = None,
    progress_callback=None,
    allow_unsupported_tokenizer: bool = False,
) -> dict[str, Any]:
    source = source.resolve()
    output = output.resolve()
    if source == output:
        raise DecryptError("refusing to overwrite the encrypted source database")
    if output.exists() and not force:
        raise DecryptError(f"output already exists: {output}")
    for suffix in ("-wal", "-shm"):
        sidecar = Path(str(output) + suffix)
        if sidecar.exists():
            raise DecryptError(f"output sidecar already exists: {sidecar}")

    params = manifest["sqlcipher"]
    page_size = int(params["page_size"])
    reserve_size = int(params["reserve_size"])
    stat_before = source.stat()
    if stat_before.st_size < page_size or stat_before.st_size % page_size:
        raise DecryptError(
            f"encrypted file size is not a multiple of {page_size}: {source}"
        )

    wal = inspect_wal(source, page_size)
    uncertain_wal = wal["exists"] and wal["size"] and not wal["header_valid"]
    active_wal = bool(wal["commit_frames"])
    if (uncertain_wal or active_wal) and not ignore_wal:
        raise DecryptError(
            "database has an active or unverifiable WAL; close Weixin for checkpoint "
            "or pass --ignore-wal for a base-only snapshot"
        )

    with source.open("rb") as stream:
        first_page = stream.read(page_size)
    selected = choose_key(manifest, first_page)
    key = bytes.fromhex(selected["key_hex"])
    hmac_key = bytes.fromhex(selected["hmac_key_hex"])

    output.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{output.name}.", suffix=".part", dir=output.parent
    )
    temporary = Path(temporary_name)
    pages = stat_before.st_size // page_size
    try:
        with source.open("rb") as encrypted_stream, os.fdopen(fd, "wb") as plain_stream:
            for page_number in range(1, pages + 1):
                encrypted = encrypted_stream.read(page_size)
                plain = decrypt_page(
                    encrypted,
                    page_number,
                    key,
                    hmac_key,
                    params,
                )
                if page_number == 1:
                    if plain[:16] != SQLITE_HEADER:
                        raise DecryptError("decrypted SQLite header is invalid")
                    decoded_page_size = int.from_bytes(plain[16:18], "big")
                    if decoded_page_size != page_size:
                        raise DecryptError(
                            f"unexpected SQLite page size {decoded_page_size}"
                        )
                    if plain[20] != reserve_size:
                        raise DecryptError(
                            f"unexpected SQLite reserve byte {plain[20]}"
                        )
                    if plain[21:24] != bytes((64, 32, 32)):
                        raise DecryptError("unexpected SQLite payload fractions")
                    schema_format = int.from_bytes(plain[44:48], "big")
                    encoding = int.from_bytes(plain[56:60], "big")
                    if schema_format not in range(1, 5) or encoding not in range(1, 4):
                        raise DecryptError("invalid SQLite header metadata")
                plain_stream.write(plain)
                if progress_callback and (page_number % 256 == 0 or page_number == pages):
                    progress_callback(page_number * page_size, stat_before.st_size)
            plain_stream.flush()
            os.fsync(plain_stream.fileno())

        stat_after = source.stat()
        if (
            stat_after.st_size != stat_before.st_size
            or stat_after.st_mtime_ns != stat_before.st_mtime_ns
        ):
            raise DecryptError("encrypted database changed during decryption")
        wal_after = inspect_wal(source, page_size)
        if wal_snapshot_id(wal_after) != wal_snapshot_id(wal):
            raise DecryptError("encrypted database WAL changed during decryption")
        verification = verify_plain_database(
            temporary,
            fts_extension=fts_extension,
            weixin_dll=weixin_dll,
            allow_unsupported_tokenizer=allow_unsupported_tokenizer,
        )
        os.replace(temporary, output)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise

    return {
        "input": str(source),
        "output": str(output),
        "pages": pages,
        "bytes": stat_before.st_size,
        "salt_hex": selected["salt_hex"],
        "key_fingerprint": hashlib.sha256(key).hexdigest()[:16],
        "wal": wal,
        "wal_ignored": bool(ignore_wal and (uncertain_wal or active_wal)),
        **verification,
    }


def load_manifest(path: Path) -> dict[str, Any]:
    data = path.read_bytes()
    if data.startswith(b"WXDPAPI1\n"):
        data = _protect_manifest(data[9:], decrypt=True)
    manifest = json.loads(data.decode("utf-8"))
    if manifest.get("schema_version") != 1 or not manifest.get("keys"):
        raise DecryptError(f"invalid key manifest: {path}")
    return manifest


def _protect_manifest(data: bytes, *, decrypt: bool = False) -> bytes:
    """Seal unattended-job keys to the current Windows user; never log key material."""
    if os.name != "nt":
        raise DecryptError("protected manifests require Windows DPAPI")
    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_byte))]

    buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)))
    target = Blob()
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    function = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    function.restype = wintypes.BOOL
    if not function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target)):
        raise DecryptError("Windows could not protect/unprotect the snapshot key manifest")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    try:
        return ctypes.string_at(target.data, target.size)
    finally:
        kernel.LocalFree(target.data)


def write_protected_manifest(path: Path, manifest: dict[str, Any]) -> None:
    payload = b"WXDPAPI1\n" + _protect_manifest(json.dumps(manifest).encode("utf-8"))
    temporary = path.with_suffix(".part")
    temporary.write_bytes(payload)
    os.replace(temporary, path)


def command_extract(args: argparse.Namespace) -> int:
    manifest = build_key_manifest(
        args.pid,
        chunk_size=args.chunk_mib * 1024 * 1024,
        private_only=not args.all_memory,
    )
    write_json_atomic(args.output, manifest)
    scan = manifest["scan"]
    print(
        json.dumps(
            {
                "output": str(args.output.resolve()),
                "raw_hits": scan["raw_hits"],
                "validated_codec_contexts": scan["validated_codec_contexts"],
                "unique_keys": scan["unique_keys"],
                "bytes_read": scan["bytes_read"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def _legacy_command_decrypt(args: argparse.Namespace) -> int:
    manifest = load_manifest(args.keys)
    source = args.source.resolve()
    if not source.exists():
        raise DecryptError(f"source does not exist: {source}")

    if source.is_file():
        output = args.output.resolve() if args.output else args.output_dir / source.name
        jobs = [(source, output)]
    else:
        if args.output:
            raise DecryptError("--output is only valid for a single database file")
        inputs = sorted(
            path
            for path in source.rglob("*")
            if path.is_file() and path.suffix.lower() in DATABASE_SUFFIXES
        )
        jobs = [(path, args.output_dir / path.relative_to(source)) for path in inputs]
    if not jobs:
        raise DecryptError("no .db or .kvdb files found")

    results = []
    failures = []
    for input_path, output_path in jobs:
        try:
            results.append(
                decrypt_database(
                    input_path,
                    output_path,
                    manifest,
                    force=args.force,
                    ignore_wal=args.ignore_wal,
                    fts_extension=args.fts_extension,
                    weixin_dll=args.weixin_dll,
                )
            )
        except (DecryptError, OSError, sqlite3.DatabaseError) as exc:
            failures.append({"input": str(input_path), "error": str(exc)})

    print(
        json.dumps(
            {"decrypted": results, "failures": failures},
            ensure_ascii=False,
            indent=2,
        )
    )
    return 1 if failures else 0


def _snapshot_command_decrypt(args: argparse.Namespace) -> int:
    manifest = load_manifest(args.keys)
    source = args.source.resolve()
    output_dir = args.output_dir.resolve()
    if not source.exists():
        raise DecryptError(f"source does not exist: {source}")

    account = ""
    if not args.no_report:
        account = (args.account or infer_account(source) or "").strip()
        if not account:
            raise DecryptError(
                "could not infer the account ID; pass --account wxid_xxx "
                "or use --no-report for standalone decryption"
            )

    staging_root: Path | None = None
    published_root: Path | None = None
    if source.is_file():
        output = args.output.resolve() if args.output else output_dir / source.name
        final_jobs = [(source, output)]
        jobs = final_jobs
    else:
        if args.output:
            raise DecryptError("--output is only valid for a single database file")
        inputs = sorted(
            path
            for path in source.rglob("*")
            if path.is_file() and path.suffix.lower() in DATABASE_SUFFIXES
        )
        final_jobs = [(path, output_dir / path.relative_to(source)) for path in inputs]
        if not final_jobs:
            raise DecryptError("no .db or .kvdb files found")
        if args.no_report and output_dir.exists() and not args.force:
            raise DecryptError(f"output already exists: {output_dir}")
        jobs = final_jobs
    if not final_jobs:
        raise DecryptError("no .db or .kvdb files found")

    validate_decrypt_paths(
        source=source,
        key_manifest=args.keys,
        jobs=final_jobs,
        report=None if args.no_report else args.report,
    )

    if source.is_dir():
        output_dir.parent.mkdir(parents=True, exist_ok=True)
        published_root = (
            output_dir.with_name(f"{output_dir.name}.{uuid.uuid4().hex}")
            if not args.no_report
            else output_dir
        )
        staging_root = Path(
            tempfile.mkdtemp(
                prefix=f".{published_root.name}.",
                suffix=".staging",
                dir=published_root.parent,
            )
        )
        jobs = [
            (input_path, staging_root / output_path.relative_to(output_dir))
            for input_path, output_path in final_jobs
        ]

    results: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    report_path = None
    try:
        for input_path, output_path in jobs:
            try:
                results.append(
                    decrypt_database(
                        input_path,
                        output_path,
                        manifest,
                        force=args.force if staging_root is None else False,
                        ignore_wal=args.ignore_wal,
                        fts_extension=args.fts_extension,
                        weixin_dll=args.weixin_dll,
                    )
                )
            except (DecryptError, OSError, sqlite3.DatabaseError) as exc:
                failures.append({"input": str(input_path), "error": str(exc)})

        if failures:
            results = []
        elif staging_root is not None:
            published_results = []
            for result, (_, staged_output) in zip(results, jobs):
                relative = staged_output.resolve().relative_to(staging_root.resolve())
                published_output = (published_root or output_dir) / relative
                published_results.append(
                    {**result, "output": str(published_output.resolve())}
                )
            report_factory = None
            if not args.no_report:
                report_factory = lambda: build_decryption_report(
                    account=account,
                    source_root=source,
                    key_manifest=args.keys,
                    output_root=published_root or output_dir,
                    results=published_results,
                    failures=[],
                    reserve_size=int(manifest["sqlcipher"]["reserve_size"]),
                )
            publish_directory_snapshot(
                staging_root,
                published_root or output_dir,
                None if args.no_report else args.report,
                report_factory,
                replace_existing=args.no_report and args.force,
            )
            staging_root = None
            results = published_results
            if not args.no_report:
                report_path = str(args.report.resolve())
        elif not args.no_report:
            report_output_root = (
                Path(results[0]["output"]).resolve().parent
                if args.output
                else output_dir
            )
            report = build_decryption_report(
                account=account,
                source_root=source.parent,
                key_manifest=args.keys,
                output_root=report_output_root,
                results=results,
                failures=[],
                reserve_size=int(manifest["sqlcipher"]["reserve_size"]),
            )
            write_json_atomic(args.report, report)
            report_path = str(args.report.resolve())
    finally:
        if staging_root is not None:
            shutil.rmtree(staging_root, ignore_errors=True)

    print(
        json.dumps(
            {
                "decrypted": results,
                "failures": failures,
                "report": report_path,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 1 if failures else 0



def command_decrypt(args: argparse.Namespace) -> int:
    # Existing invocations retain this project's output and report behavior.
    if getattr(args, "report", None) is None or getattr(args, "no_report", False):
        return _legacy_command_decrypt(args)
    return _snapshot_command_decrypt(args)



def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Extract and use Weixin 4.1.9.23 SQLCipher 4 derived keys."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    extract = subparsers.add_parser(
        "extract", help="scan a live Weixin process for validated codec contexts"
    )
    extract.add_argument("--pid", type=int, required=True, help="Weixin process ID")
    extract.add_argument(
        "--output", type=Path, default=DEFAULT_KEY_FILE, help="key manifest path"
    )
    extract.add_argument(
        "--chunk-mib", type=int, default=4, choices=range(1, 65), metavar="N"
    )
    extract.add_argument(
        "--all-memory",
        action="store_true",
        help="scan all readable mappings instead of MEM_PRIVATE only",
    )
    extract.set_defaults(func=command_extract)

    decrypt = subparsers.add_parser(
        "decrypt", help="decrypt one database or a directory tree"
    )
    decrypt.add_argument(
        "source", type=Path, help="encrypted .db/.kvdb file or directory"
    )
    decrypt.add_argument(
        "--keys", type=Path, default=DEFAULT_KEY_FILE, help="key manifest path"
    )
    decrypt.add_argument(
        "--output", type=Path, help="explicit output path for one input file"
    )
    decrypt.add_argument(
        "--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR, help="output directory"
    )
    decrypt.add_argument("--account", help="account ID for a published snapshot")
    decrypt.add_argument("--report", type=Path, help="publish a validated versioned snapshot and panel report")
    decrypt.add_argument("--no-report", action="store_true", help="retain standalone decryption behavior")
    decrypt.add_argument("--force", action="store_true", help="replace existing output")
    decrypt.add_argument(
        "--ignore-wal",
        action="store_true",
        help="decrypt only the main database even when WAL has valid commits",
    )
    decrypt.add_argument(
        "--fts-extension",
        type=Path,
        help="MMFtsTokenizer extension; auto-detected from dist when built",
    )
    decrypt.add_argument(
        "--weixin-dll",
        type=Path,
        default=DEFAULT_WEIXIN_DLL,
        help="matching Weixin.dll used by the tokenizer extension",
    )
    decrypt.set_defaults(func=command_decrypt)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return args.func(args)
    except (DecryptError, OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
