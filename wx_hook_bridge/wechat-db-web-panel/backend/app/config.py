from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit


PANEL_ROOT = Path(__file__).resolve().parents[2]
REPOSITORY_ROOT = PANEL_ROOT.parent


def _path_from_env(name: str, default: Path) -> Path:
    value = os.getenv(name)
    return Path(value).expanduser().resolve() if value else default.resolve()


def _bool_from_env(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().casefold() in {"1", "true", "yes", "on"}


@dataclass(frozen=True, slots=True)
class Settings:
    panel_root: Path
    repository_root: Path
    database_root: Path
    report_path: Path
    cache_path: Path
    fts_extension: Path
    weixin_dll: Path
    timezone: str = "Asia/Shanghai"
    episode_gap_seconds: int = 30 * 60
    response_max_seconds: int = 24 * 60 * 60
    friend_automation_enabled: bool = False
    friend_automation_bridge_url: str = "http://127.0.0.1:19088"
    friend_automation_keyword: str = "进群"
    friend_automation_welcome: str = "回复“进群”加入群聊"
    friend_automation_group_welcome: str = "请先查看群公告"
    friend_automation_group_prefix: str = "47驿站"
    friend_automation_group_count: int = 5
    friend_automation_group_capacity: int = 500

    def __post_init__(self) -> None:
        cache_path = self.cache_path.resolve()
        cache_root = (self.panel_root / ".cache").resolve()
        database_root = self.database_root.resolve()
        if cache_path == database_root or cache_path.is_relative_to(database_root):
            raise ValueError("analytics cache must not be stored in the source database tree")
        if cache_path == cache_root or not cache_path.is_relative_to(cache_root):
            raise ValueError("analytics cache must be a file inside the project .cache directory")
        if not self.friend_automation_keyword:
            raise ValueError("friend automation keyword must not be empty")
        if not self.friend_automation_welcome:
            raise ValueError("friend automation welcome must not be empty")
        if not self.friend_automation_group_welcome.strip():
            raise ValueError("friend automation group welcome must not be empty")
        if not self.friend_automation_group_prefix:
            raise ValueError("friend automation group prefix must not be empty")
        if not 1 <= self.friend_automation_group_count <= 100:
            raise ValueError("friend automation group count must be between 1 and 100")
        if not 1 <= self.friend_automation_group_capacity <= 1000:
            raise ValueError("friend automation group capacity must be between 1 and 1000")
        bridge = urlsplit(self.friend_automation_bridge_url)
        if (
            bridge.scheme != "http"
            or bridge.hostname not in {"127.0.0.1", "localhost", "::1"}
            or bridge.username is not None
            or bridge.password is not None
        ):
            raise ValueError("friend automation bridge URL must be loopback HTTP")

    @property
    def realtime_path(self) -> Path:
        return self.cache_path.with_name("realtime.sqlite")

    @property
    def friend_automation_path(self) -> Path:
        return self.cache_path.with_name("friend_automation.sqlite")

    @classmethod
    def from_env(cls) -> "Settings":
        database_root = _path_from_env(
            "WECHAT_DB_ROOT", REPOSITORY_ROOT / "runtime" / "db_decrypt" / "all"
        )
        return cls(
            panel_root=PANEL_ROOT,
            repository_root=REPOSITORY_ROOT,
            database_root=database_root,
            report_path=_path_from_env(
                "WECHAT_DECRYPTION_REPORT",
                REPOSITORY_ROOT / "runtime" / "db_decrypt" / "decryption_report.json",
            ),
            cache_path=_path_from_env(
                "WECHAT_ANALYTICS_CACHE", PANEL_ROOT / ".cache" / "analytics.sqlite"
            ),
            fts_extension=_path_from_env(
                "WECHAT_FTS_EXTENSION",
                REPOSITORY_ROOT / "dist" / "weixin_fts_tokenizer.dll",
            ),
            weixin_dll=_path_from_env(
                "WEIXIN_DLL_PATH",
                Path(r"D:\Program Files\Tencent\Weixin\4.1.9.23\Weixin.dll"),
            ),
            timezone=os.getenv("WECHAT_TIMEZONE", "Asia/Shanghai"),
            episode_gap_seconds=int(os.getenv("WECHAT_EPISODE_GAP_SECONDS", "1800")),
            response_max_seconds=int(os.getenv("WECHAT_RESPONSE_MAX_SECONDS", "86400")),
            friend_automation_enabled=_bool_from_env(
                "WECHAT_FRIEND_AUTOMATION_ENABLED"
            ),
            friend_automation_bridge_url=os.getenv(
                "WECHAT_FRIEND_AUTOMATION_BRIDGE_URL", "http://127.0.0.1:19088"
            ).rstrip("/"),
            friend_automation_keyword=os.getenv(
                "WECHAT_FRIEND_AUTOMATION_KEYWORD", "进群"
            ).strip(),
            friend_automation_welcome=os.getenv(
                "WECHAT_FRIEND_AUTOMATION_WELCOME", "回复“进群”加入群聊"
            ),
            friend_automation_group_welcome=os.getenv(
                "WECHAT_FRIEND_AUTOMATION_GROUP_WELCOME",
                "请先查看群公告",
            ).strip(),
            friend_automation_group_prefix=os.getenv(
                "WECHAT_FRIEND_AUTOMATION_GROUP_PREFIX", "47驿站"
            ).strip(),
            friend_automation_group_count=max(
                1, int(os.getenv("WECHAT_FRIEND_AUTOMATION_GROUP_COUNT", "5"))
            ),
            friend_automation_group_capacity=max(
                1, int(os.getenv("WECHAT_FRIEND_AUTOMATION_GROUP_CAPACITY", "500"))
            ),
        )


settings = Settings.from_env()
