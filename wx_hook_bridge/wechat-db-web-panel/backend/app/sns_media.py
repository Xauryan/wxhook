"""Local WeChat Moments media resolution and decoding.

WeChat 4.x stores Moments media below ``cache/<month>/Sns``.  Image files
usually have no extension and use the V4 ``V1``/``V2`` container, while video
files are ordinary ISO-BMFF streams (some are temporarily named ``.tmp``).
This module deliberately keeps the source tree read-only: decoded images are
materialized below a caller supplied panel cache directory.
"""

from __future__ import annotations

import hashlib
import io
import json
import mimetypes
import os
import re
import struct
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from Crypto.Cipher import AES

try:  # Pillow is optional for embedders, but strengthens image validation.
    from PIL import Image
except ImportError:  # pragma: no cover - requirements include Pillow.
    Image = None  # type: ignore[assignment,misc]


SIG_V1 = b"\x07\x08V1\x08\x07"
SIG_V2 = b"\x07\x08V2\x08\x07"
V1_AES_KEY = b"cfcd208495d565ef"
V4_RAW_MARKER = bytes.fromhex("e8f45b657f6d049de701465372f40b47")
AES_BLOCK = 16
_HEX32 = re.compile(r"^[0-9a-f]{32}$", re.IGNORECASE)
_VIDEO_TYPES = {"6", "video", "movie", "video_message"}
_IMAGE_MIMES = {"image/jpeg", "image/png", "image/gif", "image/webp"}
_VIDEO_EXTENSIONS = (".mp4", ".tmp", ".m4v", ".mov")


class MediaError(RuntimeError):
    """Base error for malformed or undecodable local media."""


class MediaNotFound(MediaError):
    """Raised by strict callers when no matching cache file exists."""


class MediaDecodeError(MediaError):
    """Raised when a cache file cannot be decoded with the supplied keys."""


@dataclass(frozen=True, slots=True)
class DecodedMedia:
    """Decoded bytes and their browser-facing metadata."""

    payload: bytes
    mime_type: str
    extension: str
    encrypted: bool = False
    browser_supported: bool = True
    error: str | None = None

    @property
    def size(self) -> int:
        return len(self.payload)


@dataclass(frozen=True, slots=True)
class ResolvedMedia:
    """A safe local media result suitable for a ``FileResponse``."""

    path: Path
    source_path: Path
    mime_type: str
    variant: str
    cache_key: str
    media_id: str
    sns_id: str
    available: bool = True
    browser_supported: bool = True
    encrypted: bool = False
    size: int = 0
    width: int | None = None
    height: int | None = None
    error: str | None = None

    @property
    def local_path(self) -> str:
        return str(self.path)

    @property
    def file_name(self) -> str:
        return self.path.name


def normalize_identifier(value: Any) -> str:
    """Normalize an SNS identifier while preserving unsigned 64-bit values."""

    if value is None:
        return ""
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    text = str(value).strip()
    if not text:
        return ""
    try:
        number = int(text, 10)
    except (TypeError, ValueError, OverflowError):
        return text
    if number < 0:
        number += 1 << 64
    return str(number)


def build_sns_cache_key(sns_id: Any, media_id: Any, variant: Any = 2) -> str:
    """Return the exact 32-character key used by the Moments cache."""

    left = normalize_identifier(sns_id)
    right = normalize_identifier(media_id)
    if not left or not right:
        return ""
    if isinstance(variant, str):
        variant_text = variant.strip().casefold()
        aliases = {
            "thumbnail": "1",
            "thumb": "1",
            "preview": "6",
            "medium": "6",
            "large": "2",
            "original": "2",
            "source": "3",
            "video": "3",
            "poster": "1",
        }
        variant_text = aliases.get(variant_text, variant_text)
    else:
        variant_text = str(variant)
    return hashlib.md5(f"{left}_{right}_{variant_text}".encode("utf-8")).hexdigest()


def _safe_relative(path: Path, root: Path) -> bool:
    """Check a resolved path remains inside a trusted root."""

    try:
        path.resolve(strict=False).relative_to(root.resolve(strict=False))
        return True
    except (OSError, RuntimeError, ValueError):
        return False


def _account_cache_roots(account_root: Path | str) -> tuple[Path, ...]:
    """Accept an account root, a ``cache`` root, or a direct ``Sns`` root."""

    supplied = Path(account_root).expanduser()
    try:
        supplied = supplied.resolve()
    except (OSError, RuntimeError):
        supplied = supplied.absolute()
    candidates: list[Path] = []
    name = supplied.name.casefold()
    if name == "sns":
        candidates.append(supplied)
    elif name == "cache":
        candidates.append(supplied / "Sns")
    else:
        candidates.append(supplied / "cache")
        candidates.append(supplied / "cache" / "Sns")
        # A few imported backups expose the Sns directory directly.
        candidates.append(supplied / "Sns")
    result: list[Path] = []
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except (OSError, RuntimeError):
            continue
        if resolved not in result:
            result.append(resolved)
    return tuple(result)


def _iter_sns_roots(account_root: Path | str, kind: str) -> Iterable[Path]:
    wanted = "Video" if kind == "video" else "Img"
    seen: set[str] = set()
    for base in _account_cache_roots(account_root):
        # Direct Sns root.
        direct = base / wanted
        candidates = [direct]
        # If base is cache/Sns, direct is already right; if it is account/cache,
        # month folders sit between cache and Sns.
        if base.name.casefold() == "sns":
            candidates = [base / wanted]
        elif base.name.casefold() == "cache":
            candidates = [base / "Sns" / wanted]
        if base.is_dir() and base.name.casefold() not in {"sns", "cache"}:
            candidates.append(base / wanted)
        for candidate in candidates:
            if candidate.is_dir():
                key = str(candidate).casefold()
                if key not in seen:
                    seen.add(key)
                    yield candidate
        # Month directories are intentionally shallow; avoid a broad recursive
        # scan of the user's account tree.
        parent = base
        if parent.name.casefold() == "sns":
            continue
        if parent.name.casefold() != "cache":
            parent = parent / "cache"
        if not parent.is_dir():
            continue
        try:
            months = sorted((p for p in parent.iterdir() if p.is_dir()), reverse=True)
        except OSError:
            months = []
        for month in months:
            candidate = month / "Sns" / wanted
            if candidate.is_dir():
                key = str(candidate).casefold()
                if key not in seen:
                    seen.add(key)
                    yield candidate


def _candidate_path(root: Path, key: str, suffix: str = "") -> Path | None:
    if not _HEX32.fullmatch(key):
        return None
    candidate = root / key[:2] / f"{key[2:]}{suffix}"
    if not _safe_relative(candidate, root):
        return None
    if candidate.is_symlink() or not candidate.is_file():
        return None
    return candidate


def find_sns_cache_file(
    account_root: Path | str,
    sns_id: Any,
    media_id: Any,
    variant: Any = 2,
    *,
    kind: str = "image",
    extensions: Iterable[str] | None = None,
) -> Path | None:
    """Locate one exact Moments cache entry without following unsafe links.

    ``kind='image'`` looks in ``Sns/Img`` and accepts the extensionless cache
    layout. ``kind='video'`` looks in ``Sns/Video`` and checks common stream
    suffixes, including the transient ``.tmp`` suffix.
    """

    normalized_kind = "video" if str(kind).casefold() == "video" else "image"
    key = build_sns_cache_key(sns_id, media_id, variant)
    if not key:
        return None
    suffixes = list(extensions or (() if normalized_kind == "image" else _VIDEO_EXTENSIONS))
    if normalized_kind == "image" and not suffixes:
        suffixes = [""]
    if normalized_kind == "video" and "" not in suffixes:
        # Some builds omit the extension while a download is in progress.
        suffixes.append("")
    for root in _iter_sns_roots(account_root, normalized_kind):
        if normalized_kind == "image":
            hit = _candidate_path(root, key)
            if hit is not None:
                return hit
            for suffix in suffixes:
                hit = _candidate_path(root, key, suffix)
                if hit is not None:
                    return hit
        else:
            for suffix in suffixes:
                hit = _candidate_path(root, key, suffix)
                if hit is not None:
                    return hit
    return None


def sniff_media(payload: bytes, *, file_name: str = "") -> tuple[str, str]:
    """Return ``(MIME type, extension)`` from bytes, never from extension alone."""

    if payload.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png", ".png"
    if payload.startswith(b"\xff\xd8\xff"):
        return "image/jpeg", ".jpg"
    if payload.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif", ".gif"
    if len(payload) >= 12 and payload[:4] == b"RIFF" and payload[8:12] == b"WEBP":
        return "image/webp", ".webp"
    if len(payload) >= 8 and payload[4:8] == b"ftyp":
        return "video/mp4", ".mp4"
    guessed = mimetypes.guess_type(file_name)[0] if file_name else None
    if guessed in _IMAGE_MIMES or guessed == "video/mp4":
        return guessed, "." + (mimetypes.guess_extension(guessed) or "bin").lstrip(".")
    return "application/octet-stream", ".bin"


def _image_valid(payload: bytes, mime_type: str) -> bool:
    if mime_type not in _IMAGE_MIMES or not payload:
        return False
    if Image is None:
        return True
    try:
        with Image.open(io.BytesIO(payload)) as image:
            image.load()
        return True
    except Exception:
        return False


def _normalise_aes_key(value: Any) -> bytes | None:
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip().encode("ascii", errors="ignore")
    elif isinstance(value, bytearray):
        value = bytes(value)
    if not isinstance(value, bytes) or len(value) < AES_BLOCK:
        return None
    return value[:AES_BLOCK]


def _normalise_xor_key(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        if isinstance(value, str):
            text = value.strip().lower()
            value = int(text[2:], 16) if text.startswith("0x") else int(text, 0)
        number = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if 0 <= number <= 255 else None


def _derive_xor_key(payload: bytes) -> int | None:
    if len(payload) >= 2:
        key = payload[-2] ^ 0xFF
        if payload[-1] ^ key == 0xD9:
            return key
    return None


def _try_xor_candidates(payload: bytes, expected_kind: str | None = None) -> int | None:
    """Infer a key for non-JPEG V4 files from a strong file signature."""

    signatures: tuple[tuple[int, bytes], ...] = (
        (0, b"\x89PNG\r\n\x1a\n"),
        (0, b"GIF89a"),
        (0, b"GIF87a"),
        (0, b"RIFF"),
        (4, b"ftyp"),
    )
    for offset, signature in signatures:
        if len(payload) < offset + len(signature):
            continue
        key = payload[offset] ^ signature[0]
        if all((payload[offset + i] ^ key) == byte for i, byte in enumerate(signature)):
            return key
    return None


def _decrypt_v4(payload: bytes, *, xor_key: int | None, aes_key: bytes | None) -> bytes:
    if len(payload) < 15:
        raise MediaDecodeError("微信 V4 媒体头部不完整")
    signature, aes_size_raw, xor_size = struct.unpack("<6sLLx", payload[:15])
    if signature not in {SIG_V1, SIG_V2}:
        raise MediaDecodeError("不是微信 V4 媒体")
    key = V1_AES_KEY if signature == SIG_V1 else aes_key
    key = _normalise_aes_key(key)
    if key is None:
        raise MediaDecodeError("V2 图片缺少账号 AES 密钥")
    aes_size = aes_size_raw + ((AES_BLOCK - aes_size_raw % AES_BLOCK) % AES_BLOCK)
    rest = payload[15:]
    if aes_size > len(rest):
        raise MediaDecodeError("AES 段超出文件长度")
    if xor_size > len(rest) - aes_size:
        raise MediaDecodeError("XOR 段超出文件长度")
    aes_data = rest[:aes_size]
    raw_end = len(rest) - xor_size if xor_size else len(rest)
    raw_data = rest[aes_size:raw_end]
    xor_data = rest[raw_end:] if xor_size else b""
    if raw_data.startswith(V4_RAW_MARKER):
        raw_data = raw_data[len(V4_RAW_MARKER):]
    if aes_data:
        try:
            decrypted_prefix = AES.new(key, AES.MODE_ECB).decrypt(aes_data)
        except ValueError as exc:
            raise MediaDecodeError("AES 段不是完整块") from exc
        # PKCS#7 is used by most files, but some clients leave an unpadded
        # plaintext block. Keep it intact when unpadding is not valid.
        pad = decrypted_prefix[-1] if decrypted_prefix else 0
        if 0 < pad <= AES_BLOCK and decrypted_prefix.endswith(bytes([pad]) * pad):
            decrypted_prefix = decrypted_prefix[:-pad]
    else:
        decrypted_prefix = b""
    key_xor = xor_key if xor_key is not None else _derive_xor_key(payload)
    if xor_data and key_xor is None:
        key_xor = _try_xor_candidates(xor_data, None)
    if xor_data and key_xor is None:
        raise MediaDecodeError("无法推断图片 XOR 密钥")
    decrypted_xor = bytes(byte ^ (key_xor or 0) for byte in xor_data)
    result = decrypted_prefix + raw_data + decrypted_xor
    if result.startswith(b"\xff\xd8\xff"):
        end = result.rfind(b"\xff\xd9")
        if end >= 0:
            result = result[: end + 2]
    return result


def decode_media_bytes(
    payload: bytes,
    *,
    file_name: str = "",
    aes_key: Any = None,
    xor_key: Any = None,
    require_kind: str | None = None,
) -> DecodedMedia:
    """Decode ordinary or WeChat V4 bytes and validate browser support."""

    if not payload:
        raise MediaDecodeError("媒体文件为空")
    signature = payload[:6]
    encrypted = signature in {SIG_V1, SIG_V2}
    decoded = payload
    if encrypted:
        decoded = _decrypt_v4(
            payload,
            xor_key=_normalise_xor_key(xor_key),
            aes_key=_normalise_aes_key(aes_key),
        )
    mime_type, extension = sniff_media(decoded, file_name=file_name)
    if mime_type.startswith("image/") and not _image_valid(decoded, mime_type):
        raise MediaDecodeError("图片解密结果无法由 Pillow 解码")
    if require_kind == "image" and not mime_type.startswith("image/"):
        raise MediaDecodeError("缓存文件不是图片")
    if require_kind == "video" and mime_type != "video/mp4":
        raise MediaDecodeError("缓存文件不是 MP4 视频")
    browser_supported = mime_type in _IMAGE_MIMES or mime_type == "video/mp4"
    return DecodedMedia(
        payload=decoded,
        mime_type=mime_type,
        extension=extension,
        encrypted=encrypted,
        browser_supported=browser_supported,
    )


def decode_media_file(
    path: Path | str,
    *,
    aes_key: Any = None,
    xor_key: Any = None,
    require_kind: str | None = None,
) -> DecodedMedia:
    source = Path(path)
    try:
        payload = source.read_bytes()
    except OSError as exc:
        raise MediaDecodeError(f"无法读取媒体: {source.name}") from exc
    return decode_media_bytes(
        payload,
        file_name=source.name,
        aes_key=aes_key,
        xor_key=xor_key,
        require_kind=require_kind,
    )


def _key_from_json(account_root: Path, names: tuple[str, ...]) -> Any:
    candidates = (
        account_root / "_media_keys.json",
        account_root / "config" / "_media_keys.json",
        account_root / "resource" / "_media_keys.json",
    )
    for path in candidates:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        if not isinstance(data, Mapping):
            continue
        for name in names:
            value = data.get(name)
            if value not in (None, ""):
                return value
        nested = data.get("image_keys")
        if isinstance(nested, Mapping):
            for name in names:
                value = nested.get(name)
                if value not in (None, ""):
                    return value
    return None


def _media_kind(media_type: Any = None, media_meta: Mapping[str, Any] | None = None) -> str:
    meta = media_meta or {}
    mime = str(meta.get("mime_type") or meta.get("mime") or "").casefold()
    typ = str(meta.get("type") if meta.get("type") is not None else media_type).casefold()
    try:
        duration = float(meta.get("duration") or 0)
    except (TypeError, ValueError):
        duration = 0
    if mime.startswith("video/") or typ in _VIDEO_TYPES or duration > 0:
        return "video"
    return "image"


def _variant_orders(kind: str, variant: str) -> tuple[int, ...]:
    name = variant.casefold().strip()
    if kind == "video":
        return (3,) if name in {"source", "original", "video"} else (1,)
    if name in {"source", "original", "full"}:
        return (2, 6, 1)
    if name in {"poster", "video"}:
        return (1, 6, 2)
    return (6, 1, 2)


class MediaResolver:
    """Resolve and decode local Moments media for browser endpoints."""

    def __init__(
        self,
        account_root: Path | str,
        *,
        decode_root: Path | str | None = None,
        aes_key: Any = None,
        xor_key: Any = None,
        key_provider: Callable[[], Mapping[str, Any] | None] | None = None,
    ) -> None:
        self.account_root = Path(account_root).expanduser().resolve()
        if not self.account_root.exists():
            raise FileNotFoundError(self.account_root)
        self.decode_root = (
            Path(decode_root).expanduser().resolve()
            if decode_root is not None
            else (self.account_root.parent / ".sns-media-decoded").resolve()
        )
        if _safe_relative(self.decode_root, self.account_root):
            raise ValueError("decoded SNS media must be stored outside the source account tree")
        self.decode_root.mkdir(parents=True, exist_ok=True)
        self.aes_key = aes_key
        self.xor_key = xor_key
        self.key_provider = key_provider

    def _keys(self) -> tuple[Any, Any]:
        aes = self.aes_key
        xor = self.xor_key
        if self.key_provider is not None:
            try:
                supplied = self.key_provider() or {}
            except Exception:
                supplied = {}
            if isinstance(supplied, Mapping):
                aes = aes or supplied.get("aes") or supplied.get("image_aes_key") or supplied.get("aes_key")
                xor = xor if xor is not None else supplied.get("xor") or supplied.get("image_xor_key") or supplied.get("xor_key")
        aes = aes or os.getenv("WECHAT_SNS_AES_KEY") or os.getenv("WECHAT_IMAGE_AES_KEY")
        xor = xor if xor is not None else os.getenv("WECHAT_SNS_XOR_KEY") or os.getenv("WECHAT_IMAGE_XOR_KEY")
        aes = aes or _key_from_json(self.account_root, ("aes", "image_aes_key", "aes_key", "image_key"))
        xor = xor if xor is not None else _key_from_json(self.account_root, ("xor", "image_xor_key", "xor_key"))
        return aes, xor

    def _materialize(self, source: Path, decoded: DecodedMedia, *, variant: str) -> Path:
        stat = source.stat()
        aes, xor = self._keys()
        key_fingerprint = hashlib.sha256(
            (str(aes or "") + "|" + str(xor or "")).encode("utf-8", errors="replace")
        ).hexdigest()[:16]
        identity = f"{source.resolve()}|{stat.st_size}|{stat.st_mtime_ns}|{variant}|{key_fingerprint}"
        stem = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        target = self.decode_root / stem[:2] / f"{stem}{decoded.extension}"
        if target.is_file() and not target.is_symlink():
            return target
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary_name = tempfile.mkstemp(prefix=f".{stem}.", suffix=".tmp", dir=target.parent)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(decoded.payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_name, target)
        finally:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass
        return target

    @staticmethod
    def _dimensions(payload: bytes, mime_type: str) -> tuple[int | None, int | None]:
        if Image is None or not mime_type.startswith("image/"):
            return None, None
        try:
            with Image.open(io.BytesIO(payload)) as image:
                return int(image.width), int(image.height)
        except Exception:
            return None, None

    def resolve(
        self,
        sns_id: Any,
        media_id: Any,
        *,
        media_type: Any = 2,
        variant: str = "preview",
        media_meta: Mapping[str, Any] | None = None,
    ) -> ResolvedMedia | None:
        kind = _media_kind(media_type, media_meta)
        requested_variant = variant.casefold().strip() or "preview"
        aes_key, xor_key = self._keys()
        if kind == "video" and requested_variant not in {"source", "original", "video"}:
            poster_candidates = (
                (3, "video", (".jpg", ".jpeg", ".png", ".webp")),
                (1, "image", None),
                (6, "image", None),
                (2, "image", None),
            )
            for cache_variant, cache_kind, extensions in poster_candidates:
                source = find_sns_cache_file(
                    self.account_root,
                    sns_id,
                    media_id,
                    cache_variant,
                    kind=cache_kind,
                    extensions=extensions,
                )
                if source is None:
                    continue
                try:
                    decoded = decode_media_file(
                        source,
                        aes_key=aes_key,
                        xor_key=xor_key,
                        require_kind="image",
                    )
                except MediaDecodeError:
                    continue
                target = source
                if decoded.encrypted or source.suffix == "":
                    try:
                        target = self._materialize(
                            source, decoded, variant=requested_variant
                        )
                    except OSError:
                        continue
                width, height = self._dimensions(decoded.payload, decoded.mime_type)
                return ResolvedMedia(
                    path=target,
                    source_path=source,
                    mime_type=decoded.mime_type,
                    variant=requested_variant,
                    cache_key=build_sns_cache_key(
                        sns_id, media_id, cache_variant
                    ),
                    media_id=normalize_identifier(media_id),
                    sns_id=normalize_identifier(sns_id),
                    browser_supported=decoded.browser_supported,
                    encrypted=decoded.encrypted,
                    size=decoded.size,
                    width=width,
                    height=height,
                )
            return None
        orders = _variant_orders(kind, requested_variant)
        for cache_variant in orders:
            # A video preview is an image poster when available. Look in the
            # image tree first for variant 1, then fall back to a video stream
            # only if no poster exists.
            lookup_kinds = (
                ("image", "video")
                if kind == "video"
                and requested_variant not in {"source", "original", "video"}
                and cache_variant == 1
                else (("video", "image") if kind == "video" else ("image",))
            )
            sources: list[Path] = []
            for lookup_kind in lookup_kinds:
                source_candidate = find_sns_cache_file(
                    self.account_root,
                    sns_id,
                    media_id,
                    cache_variant,
                    kind=lookup_kind,
                    extensions=("", ".jpg", ".jpeg", ".png", ".webp")
                    if lookup_kind == "image" and kind == "video"
                    else None,
                )
                if source_candidate is not None:
                    sources.append(source_candidate)
            for source in sources:
                expected_kind = None
                if kind == "image":
                    expected_kind = "image"
                elif requested_variant in {"source", "original", "video"}:
                    expected_kind = "video"
                try:
                    decoded = decode_media_file(
                        source,
                        aes_key=aes_key,
                        xor_key=xor_key,
                        require_kind=expected_kind,
                    )
                except MediaDecodeError:
                    continue
                if kind == "image" and not decoded.mime_type.startswith("image/"):
                    continue
                if kind == "video" and requested_variant in {"source", "original", "video"} and decoded.mime_type != "video/mp4":
                    continue
                if kind == "video" and requested_variant not in {"source", "original", "video"} and not (
                    decoded.mime_type.startswith("image/") or decoded.mime_type == "video/mp4"
                ):
                    continue
                target = source
                if decoded.encrypted or decoded.mime_type == "application/octet-stream" or source.suffix == "":
                    try:
                        target = self._materialize(source, decoded, variant=requested_variant)
                    except OSError:
                        continue
                width, height = self._dimensions(decoded.payload, decoded.mime_type)
                return ResolvedMedia(
                    path=target,
                    source_path=source,
                    mime_type=decoded.mime_type,
                    variant=requested_variant,
                    cache_key=build_sns_cache_key(sns_id, media_id, cache_variant),
                    media_id=normalize_identifier(media_id),
                    sns_id=normalize_identifier(sns_id),
                    browser_supported=decoded.browser_supported,
                    encrypted=decoded.encrypted,
                    size=decoded.size,
                    width=width,
                    height=height,
                )
        return None

    def resolve_media(self, *args: Any, **kwargs: Any) -> ResolvedMedia | None:
        return self.resolve(*args, **kwargs)


def resolve_media(
    account_root: Path | str,
    sns_id: Any,
    media_id: Any,
    *,
    media_type: Any = 2,
    variant: str = "preview",
    media_meta: Mapping[str, Any] | None = None,
    decode_root: Path | str | None = None,
    aes_key: Any = None,
    xor_key: Any = None,
) -> ResolvedMedia | None:
    """Convenience wrapper for one-off resolution."""

    return MediaResolver(
        account_root,
        decode_root=decode_root,
        aes_key=aes_key,
        xor_key=xor_key,
    ).resolve(
        sns_id,
        media_id,
        media_type=media_type,
        variant=variant,
        media_meta=media_meta,
    )


__all__ = [
    "DecodedMedia",
    "MediaDecodeError",
    "MediaError",
    "MediaNotFound",
    "MediaResolver",
    "ResolvedMedia",
    "SIG_V1",
    "SIG_V2",
    "V1_AES_KEY",
    "V4_RAW_MARKER",
    "build_sns_cache_key",
    "decode_media_bytes",
    "decode_media_file",
    "find_sns_cache_file",
    "normalize_identifier",
    "resolve_media",
    "sniff_media",
]
