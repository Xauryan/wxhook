from __future__ import annotations

import hashlib
import io
import json
import sqlite3
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

import backend.app.sns_backup as sns_backup_module
from backend.app.sns_backup import (
    SnsBackupService,
    create_router,
    parse_timeline_xml,
    validate_backup_id,
)
from backend.app.sns_media import build_sns_cache_key


SNS_ID = "18446744073709551601"
SIGNED_TID = -15


XML = f"""<SnsDataItem>
<TimelineObject>
  <id>{SNS_ID}</id><username>wxid_owner</username><createTime>1710000000</createTime>
  <contentDesc>正文 with keyword</contentDesc><private>1</private><isTop>1</isTop>
  <location city="深圳" latitude="22.5" longitude="114.1" poiName="人才公园" poiAddress="南山区"/>
  <ContentObject><type>1</type><contentSubStyle>3</contentSubStyle><mediaList>
    <media><id>18446744073709551500</id><type>2</type><sub_type>0</sub_type>
      <thumb key="900">https://example.test/thumb</thumb>
      <url md5="abc" key="900">https://example.test/full</url>
      <size width="1080" height="1440" totalSize="1234"/><videoDuration>0</videoDuration>
    </media>
  </mediaList></ContentObject>
</TimelineObject>
<LocalExtraInfo><tid>{SNS_ID}</tid><nickname>测试用户</nickname>
  <comment_user_list><user_comment><comment_id>9</comment_id><comment_64id>18446744073709551000</comment_64id>
    <username>wxid_comment</username><nickname>评论者</nickname><content>一条评论</content>
    <type>2</type><create_time>1710000010</create_time><ref_comment_id>0</ref_comment_id>
  </user_comment></comment_user_list>
  <like_user_list><user_comment><comment_id>0</comment_id><comment_64id>0</comment_64id>
    <username>wxid_like</username><nickname>点赞者</nickname><type>1</type><create_time>1710000020</create_time>
  </user_comment></like_user_list>
  <like_flag>1</like_flag><is_rich_text>0</is_rich_text>
</LocalExtraInfo></SnsDataItem>"""


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def make_service(tmp_path: Path) -> tuple[SnsBackupService, Path]:
    database_root = tmp_path / "databases"
    sns_dir = database_root / "sns"
    sns_dir.mkdir(parents=True)
    database_path = sns_dir / "sns.db"
    with sqlite3.connect(database_path) as connection:
        connection.executescript(
            """
            CREATE TABLE SnsTimeLine(
                tid INTEGER PRIMARY KEY DESC,
                user_name TEXT,
                content TEXT,
                pack_info_buf TEXT
            );
            CREATE TABLE SnsMessage_tmp3(
                local_id INTEGER PRIMARY KEY,
                create_time INTEGER,
                type INTEGER,
                feed_id INTEGER,
                is_unread INTEGER,
                from_username TEXT,
                from_nickname TEXT,
                to_username TEXT,
                to_nickname TEXT,
                content TEXT,
                serialized_comment_buf BLOB,
                serialized_ref_buf BLOB,
                comment_id INTEGER,
                client_id TEXT,
                comment64_id INTEGER,
                comment_flag INTEGER,
                del_status INTEGER,
                is_relative_me INTEGER
            );
            """
        )
        connection.execute(
            "INSERT INTO SnsTimeLine VALUES(?,?,?,?)",
            (SIGNED_TID, "wxid_owner", XML, b"\x0a\x00"),
        )
        connection.execute(
            "INSERT INTO SnsMessage_tmp3 VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                7,
                1710000010,
                2,
                SIGNED_TID,
                0,
                "wxid_comment",
                "评论者",
                "wxid_owner",
                "测试用户",
                "一条评论",
                b"\x00\xffcomment",
                b"\x01ref",
                9,
                "client-9",
                0,
                0,
                0,
                1,
            ),
        )
    report = tmp_path / "report.json"
    report.write_text(
        json.dumps(
            {
                "account": "wxid_owner",
                "created_utc": "2026-08-20T00:00:00+00:00",
                "source_root": str(database_root),
                "files": [{"path": "sns/sns.db", "sha256": "reported-source-hash"}],
            }
        ),
        encoding="utf-8",
    )
    settings = SimpleNamespace(
        database_root=database_root,
        panel_root=tmp_path / "panel",
        repository_root=tmp_path,
        report_path=report,
    )
    return SnsBackupService(settings), database_path  # type: ignore[arg-type]


def test_parse_timeline_xml_preserves_large_ids_and_nested_data() -> None:
    post = parse_timeline_xml(XML, row_tid=SIGNED_TID, row_username="fallback")

    assert post["sns_id"] == SNS_ID
    assert post["tid"] == SNS_ID
    assert post["raw_tid"] == str(SIGNED_TID)
    assert isinstance(post["sns_id"], str)
    assert post["content"] == "正文 with keyword"
    assert post["private"] is True
    assert post["visibility"] == "private"
    assert post["location"]["name"] == "人才公园"
    assert post["location"]["address"] == "南山区"
    assert post["media"][0]["id"] == "18446744073709551500"
    assert post["media"][0]["remote_only"] is True
    assert post["comments"][0]["comment_64id"] == "18446744073709551000"
    assert post["likes"][0]["username"] == "wxid_like"
    assert post["raw_xml"] == XML


def test_list_detail_and_unsigned_id_lookup(tmp_path: Path) -> None:
    service, _ = make_service(tmp_path)

    items, total = service.search_posts(limit=10, q="keyword")
    assert total == 1
    assert items[0]["sns_id"] == SNS_ID
    assert items[0]["create_timestamp"] == 1710000000 * 1000
    assert items[0]["is_owner"] is True
    assert "raw_xml" not in items[0]
    assert service.get_post(SNS_ID)["raw_xml"] == XML
    assert service.get_post(str(SIGNED_TID))["sns_id"] == SNS_ID


def test_zip_manifest_snapshot_jsonl_and_raw_interaction(tmp_path: Path) -> None:
    service, database_path = make_service(tmp_path)
    source_before = database_path.read_bytes()

    metadata = service.create_backup(
        "backup-1", include_local_media=False, include_runtime_artifacts=False
    )

    assert database_path.read_bytes() == source_before
    assert metadata["download_url"] == "/api/sns/backups/backup-1/download"
    assert metadata["bytes"] > 0
    assert metadata["sha256"] == _sha256(service.backup_path("backup-1").read_bytes())
    with zipfile.ZipFile(service.backup_path("backup-1")) as archive:
        assert set(archive.namelist()) == {
            "sns.db",
            "posts.jsonl",
            "comments.jsonl",
            "likes.jsonl",
            "media.jsonl",
            "interactions.jsonl",
            "manifest.json",
            "schema.json",
        }
        manifest = json.loads(archive.read("manifest.json"))
        schema = json.loads(archive.read("schema.json"))
        assert manifest["media_policy"] == "remote_metadata_only"
        assert manifest["remote_only"] is True
        assert manifest["account"] == "wxid_owner"
        assert manifest["source_fingerprint"]["reported_sha256"] == "reported-source-hash"
        assert manifest["source_fingerprint"]["basis"] == "sqlite_online_backup"
        assert manifest["source_fingerprint"]["sha256"] == _sha256(
            archive.read("sns.db")
        )
        assert manifest["source_row_counts"]["SnsTimeLine"] == 1
        assert manifest["source_row_counts"]["SnsMessage_tmp3"] == 1
        assert manifest["counts"]["raw_interactions"] == 1
        assert "SnsMessage_tmp3" in schema["source_tables"]
        for name, info in manifest["files"].items():
            assert _sha256(archive.read(name)) == info["sha256"]

        posts = [json.loads(line) for line in archive.read("posts.jsonl").splitlines()]
        interactions = [
            json.loads(line) for line in archive.read("interactions.jsonl").splitlines()
        ]
        raw = next(item for item in interactions if item["record_source"] == "SnsMessage_tmp3")
        assert posts[0]["sns_id"] == SNS_ID
        assert isinstance(posts[0]["sns_id"], str)
        assert raw["feed_id"] == str(SIGNED_TID)
        assert raw["post_id"] == SNS_ID
        assert raw["serialized_comment_buf"]["hex"] == "00ff636f6d6d656e74"

        snapshot = tmp_path / "snapshot.db"
        snapshot.write_bytes(archive.read("sns.db"))
        with sqlite3.connect(snapshot) as connection:
            assert connection.execute("SELECT count(*) FROM SnsTimeLine").fetchone() == (1,)


def test_list_and_delete_backup_with_safe_id(tmp_path: Path) -> None:
    service, _ = make_service(tmp_path)
    service.create_backup(
        "backup-delete", include_local_media=False, include_runtime_artifacts=False
    )

    backups = service.list_backups()
    assert backups[0]["backup_id"] == "backup-delete"
    assert backups[0]["download_url"].endswith("/backup-delete/download")
    assert service.delete_backup("backup-delete") is True
    assert service.list_backups() == []
    assert service.delete_backup("backup-delete") is False

    for unsafe in ("../outside", "..", "a/b", "a\\b", "a..b", ""):
        with pytest.raises(ValueError):
            validate_backup_id(unsafe)
        with pytest.raises(ValueError):
            service.delete_backup(unsafe)


def test_existing_archive_is_not_replaced_and_symlinks_are_not_served(
    tmp_path: Path,
) -> None:
    service, _ = make_service(tmp_path)
    service.create_backup(
        "backup-safe", include_local_media=False, include_runtime_artifacts=False
    )
    archive = service.backup_path("backup-safe")
    archive_before = archive.read_bytes()

    with pytest.raises(FileExistsError):
        service.create_backup(
            "backup-safe", include_local_media=False, include_runtime_artifacts=False
        )
    assert archive.read_bytes() == archive_before

    outside = tmp_path / "outside.zip"
    outside.write_bytes(b"outside")
    link = service.backup_root / "linked.zip"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlink creation is unavailable on this Windows host")

    assert all(item["backup_id"] != "linked" for item in service.list_backups())
    with pytest.raises(FileNotFoundError):
        service.backup_path("linked")
    assert service.delete_backup("linked") is True
    assert outside.read_bytes() == b"outside"


def test_runtime_artifact_limits_apply_to_copied_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, _ = make_service(tmp_path)
    repository_root = tmp_path / "repository"
    control_root = repository_root / "bridge" / "control"
    control_root.mkdir(parents=True)
    first, second = sns_backup_module.SNS_RUNTIME_ARTIFACTS[:2]
    (control_root / first).write_bytes(b"12345678")
    (control_root / second).write_bytes(b"abcde")
    service.settings.repository_root = repository_root
    monkeypatch.setattr(sns_backup_module, "RUNTIME_ARTIFACT_LIMIT", 8)
    monkeypatch.setattr(sns_backup_module, "RUNTIME_ARTIFACT_TOTAL_LIMIT", 10)

    records = service._copy_runtime_artifacts(tmp_path / "payload")

    assert records[0]["status"] == "copied"
    assert records[0]["size"] == 8
    assert records[1] == {
        "path": second,
        "size": 5,
        "status": "skipped_size_limit",
    }
    assert (tmp_path / "payload" / "runtime_artifacts" / first).read_bytes() == b"12345678"
    assert not (tmp_path / "payload" / "runtime_artifacts" / second).exists()


def test_router_exposes_paginated_posts_and_backup_lifecycle(tmp_path: Path) -> None:
    service, baseline_database = make_service(tmp_path)
    baseline_before = baseline_database.read_bytes()
    assert service.database_path == (tmp_path / "panel" / ".cache" / "sns-live.sqlite")
    app = FastAPI()
    app.include_router(create_router(service))

    with TestClient(app) as client:
        listing = client.get("/api/sns", params={"limit": 1, "q": "keyword"})
        assert listing.status_code == 200
        payload = listing.json()
        assert payload["total"] == 1
        assert payload["items"][0]["sns_id"] == SNS_ID
        assert payload["stats"]["raw_interactions"] == 1
        assert payload["sync"]["enabled"] is True

        sync_status = client.get("/api/sns/sync/status")
        assert sync_status.status_code == 200
        assert sync_status.json()["configured"] is True
        refreshed = client.post("/api/sns/sync")
        assert refreshed.status_code == 200
        assert refreshed.json()["generation"] == 1
        assert refreshed.json()["changed"] is True
        assert service.database_path.is_file()
        assert baseline_database.read_bytes() == baseline_before

        detail = client.get(f"/api/sns/{SNS_ID}")
        assert detail.status_code == 200
        assert detail.json()["raw_interaction_count"] == 1

        created = client.post(
            "/api/sns/backups",
            json={
                "backup_id": "router-backup",
                "include_local_media": False,
                "include_runtime_artifacts": False,
            },
        )
        assert created.status_code == 201
        assert created.json()["download_url"].endswith("/router-backup/download")
        assert client.get(created.json()["download_url"]).status_code == 200

        backups = client.get("/api/sns/backups").json()
        assert backups["total"] == 1
        assert backups["items"][0]["backup_id"] == "router-backup"
        assert client.delete("/api/sns/backups/router-backup").json() == {
            "deleted": True,
            "backup_id": "router-backup",
        }


def test_router_serves_exact_local_media_with_opaque_token(tmp_path: Path) -> None:
    initial, _ = make_service(tmp_path)
    account_root = tmp_path / "account"
    (account_root / "db_storage").mkdir(parents=True)
    report_path = Path(initial.settings.report_path)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["source_root"] = str(account_root / "db_storage")
    report_path.write_text(json.dumps(report), encoding="utf-8")

    image_stream = io.BytesIO()
    Image.new("RGB", (4, 3), (34, 139, 94)).save(image_stream, format="JPEG")
    jpeg = image_stream.getvalue()
    for variant in (2, 6):
        key = build_sns_cache_key(SNS_ID, "18446744073709551500", variant)
        cache_path = (
            account_root
            / "cache"
            / "2026-08"
            / "Sns"
            / "Img"
            / key[:2]
            / key[2:]
        )
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_bytes(jpeg)

    service = SnsBackupService(initial.settings)
    app = FastAPI()
    app.include_router(create_router(service))

    with TestClient(app) as client:
        media = client.get("/api/sns", params={"limit": 1}).json()["items"][0][
            "media"
        ][0]
        assert media["available"] is True
        assert media["remote_only"] is False
        assert media["mime_type"] == "image/jpeg"
        assert media["preview_url"].startswith("/api/sns/media/")
        assert media["media_url"].startswith("/api/sns/media/")
        assert "local_path" not in media

        preview = client.get(media["preview_url"])
        assert preview.status_code == 200
        assert preview.headers["content-type"] == "image/jpeg"
        assert preview.content.startswith(b"\xff\xd8\xff")

        tampered_path = media["preview_url"].split("?", 1)[0] + "x?variant=preview"
        assert client.get(tampered_path).status_code == 404
