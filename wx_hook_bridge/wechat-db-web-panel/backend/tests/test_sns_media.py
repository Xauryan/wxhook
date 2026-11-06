from __future__ import annotations

import hashlib
import io
import struct
from pathlib import Path

import pytest
from Crypto.Cipher import AES
from PIL import Image

from backend.app.sns_media import (
    SIG_V1,
    SIG_V2,
    V1_AES_KEY,
    V4_RAW_MARKER,
    MediaDecodeError,
    MediaResolver,
    build_sns_cache_key,
    decode_media_bytes,
    find_sns_cache_file,
    sniff_media,
)


SNS_ID = "18446744073709551601"
MEDIA_ID = "18446744073709551500"


def _jpeg() -> bytes:
    stream = io.BytesIO()
    Image.new("RGB", (3, 2), (28, 126, 214)).save(stream, format="JPEG", quality=90)
    return stream.getvalue()


def _v4_fixture(
    image: bytes,
    *,
    signature: bytes = SIG_V2,
    aes_key: bytes = b"0123456789abcdef",
    xor_key: int = 0xA6,
) -> bytes:
    # Keep 31 bytes of plaintext in a 32-byte AES segment so valid PKCS#7
    # padding exists without adding an unreported extra AES block.
    prefix = image[:31] + b"\x01"
    tail_size = 32
    middle = image[31:-tail_size]
    tail = image[-tail_size:]
    encrypted = AES.new(aes_key, AES.MODE_ECB).encrypt(prefix)
    xor_tail = bytes(value ^ xor_key for value in tail)
    header = struct.pack("<6sLLx", signature, len(encrypted), len(xor_tail))
    return header + encrypted + V4_RAW_MARKER + middle + xor_tail


def _cache_path(
    account_root: Path,
    sns_id: str,
    media_id: str,
    variant: int,
    *,
    kind: str = "Img",
    suffix: str = "",
) -> Path:
    key = build_sns_cache_key(sns_id, media_id, variant)
    path = account_root / "cache" / "2026-08" / "Sns" / kind / key[:2] / f"{key[2:]}{suffix}"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def test_build_cache_key_uses_unsigned_sns_id() -> None:
    expected = hashlib.md5(f"{SNS_ID}_{MEDIA_ID}_6".encode()).hexdigest()

    assert build_sns_cache_key(-15, MEDIA_ID, 6) == expected
    assert build_sns_cache_key(SNS_ID, MEDIA_ID, "preview") == expected
    assert build_sns_cache_key("", MEDIA_ID, 6) == ""


def test_find_exact_image_and_tmp_video_cache_files(tmp_path: Path) -> None:
    image = _cache_path(tmp_path, SNS_ID, MEDIA_ID, 6)
    image.write_bytes(b"image")
    video = _cache_path(tmp_path, SNS_ID, MEDIA_ID, 3, kind="Video", suffix=".tmp")
    video.write_bytes(b"\x00\x00\x00\x1cftypisom")

    assert find_sns_cache_file(tmp_path, SNS_ID, MEDIA_ID, 6) == image
    assert find_sns_cache_file(tmp_path, SNS_ID, MEDIA_ID, 3, kind="video") == video


def test_v2_decoder_removes_marker_and_xor_tail() -> None:
    image = _jpeg()
    encrypted = _v4_fixture(image)

    decoded = decode_media_bytes(encrypted, aes_key="0123456789abcdef", require_kind="image")

    assert decoded.payload == image
    assert decoded.mime_type == "image/jpeg"
    assert decoded.extension == ".jpg"
    assert decoded.encrypted is True


def test_v1_uses_built_in_key_and_invalid_v2_key_is_rejected() -> None:
    image = _jpeg()
    v1 = _v4_fixture(image, signature=SIG_V1, aes_key=V1_AES_KEY)
    v2 = _v4_fixture(image)

    assert decode_media_bytes(v1, require_kind="image").payload == image
    with pytest.raises(MediaDecodeError):
        decode_media_bytes(v2, aes_key="fedcba9876543210", require_kind="image")


def test_resolver_prefers_medium_preview_and_materializes_outside_source(tmp_path: Path) -> None:
    account_root = tmp_path / "wxid_example"
    decode_root = tmp_path / "panel" / ".cache" / "sns-media"
    original = _jpeg()
    thumbnail = _cache_path(account_root, SNS_ID, MEDIA_ID, 1)
    thumbnail.write_bytes(_v4_fixture(original))
    medium = _cache_path(account_root, SNS_ID, MEDIA_ID, 6)
    medium.write_bytes(_v4_fixture(original))
    source_stat = medium.stat()
    resolver = MediaResolver(
        account_root,
        decode_root=decode_root,
        aes_key="0123456789abcdef",
    )

    result = resolver.resolve(SNS_ID, MEDIA_ID, variant="preview")

    assert result is not None
    assert result.cache_key == build_sns_cache_key(SNS_ID, MEDIA_ID, 6)
    assert result.source_path == medium
    assert result.path.is_relative_to(decode_root)
    assert result.path.read_bytes() == original
    assert result.mime_type == "image/jpeg"
    assert result.width == 3
    assert result.height == 2
    assert medium.stat().st_mtime_ns == source_stat.st_mtime_ns
    assert medium.read_bytes().startswith(SIG_V2)


def test_resolver_serves_tmp_video_as_mp4(tmp_path: Path) -> None:
    account_root = tmp_path / "wxid_example"
    decode_root = tmp_path / "panel" / ".cache" / "sns-media"
    mp4 = b"\x00\x00\x00\x1cftypisom\x00\x00\x02\x00isomiso2avc1mp41"
    source = _cache_path(account_root, SNS_ID, MEDIA_ID, 3, kind="Video", suffix=".tmp")
    source.write_bytes(mp4)
    resolver = MediaResolver(account_root, decode_root=decode_root)

    result = resolver.resolve(
        SNS_ID,
        MEDIA_ID,
        media_type=6,
        variant="source",
        media_meta={"duration": 8},
    )

    assert result is not None
    assert result.path == source
    assert result.mime_type == "video/mp4"
    assert result.browser_supported is True
    assert sniff_media(mp4, file_name="download.tmp") == ("video/mp4", ".mp4")


def test_decode_root_cannot_modify_source_account_tree(tmp_path: Path) -> None:
    account_root = tmp_path / "wxid_example"
    account_root.mkdir()

    with pytest.raises(ValueError, match="outside"):
        MediaResolver(account_root, decode_root=account_root / "resource")
