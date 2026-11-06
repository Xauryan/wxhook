from datetime import datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, model_validator

TIMEZONE = ZoneInfo('Asia/Shanghai')


class RuleConfig(BaseModel):
    model_config = ConfigDict(extra='forbid')
    enabled: bool = False
    ai_profile_id: str = Field(min_length=1, max_length=64)
    schedule_time: str = Field(default='01:00', pattern=r'^(?:[01]\d|2[0-3]):[0-5]\d$')
    window_hours: int = Field(default=24, ge=1, le=168)
    output_format: Literal['image', 'text', 'pdf'] = 'image'
    skin: Literal['md3', 'anime', 'newspaper', 'art', 'scrapbook'] = 'md3'
    theme: Literal['light', 'dark', 'auto'] = 'auto'
    manual_enabled: bool = False
    admin_ids: list[str] = Field(default_factory=list, max_length=100)
    max_messages: int = Field(default=2000, ge=100, le=20000)
    min_messages: int = Field(default=10, ge=1, le=1000)
    max_topics: int = Field(default=5, ge=1, le=15)
    max_quotes: int = Field(default=3, ge=0, le=10)
    max_titles: int = Field(default=6, ge=0, le=15)
    prompt_topics: str = Field(min_length=1, max_length=20000)
    prompt_titles: str = Field(min_length=1, max_length=20000)
    prompt_quotes: str = Field(min_length=1, max_length=20000)

    @model_validator(mode='after')
    def validate_admins(self):
        self.admin_ids = list(dict.fromkeys(value.strip() for value in self.admin_ids))
        if any(not value or len(value) > 240 for value in self.admin_ids):
            raise ValueError('指令管理员配置无效')
        if self.manual_enabled and not self.admin_ids:
            raise ValueError('开启群指令前请选择指令管理员')
        return self


class RuleEdit(BaseModel):
    model_config = ConfigDict(extra='forbid')
    account: str = Field(min_length=1, max_length=240)
    room: str = Field(min_length=1, max_length=240, pattern=r'@chatroom$')
    revision: int = Field(ge=0)
    config: RuleConfig


class RunRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    account: str = Field(min_length=1, max_length=240)
    room: str = Field(min_length=1, max_length=240, pattern=r'@chatroom$')
    request_id: str = Field(pattern=r'^[a-f0-9]{32}$')
    start: datetime
    end: datetime
    send: bool = False

    @model_validator(mode='after')
    def validate_window(self):
        if self.start.tzinfo is None or self.end.tzinfo is None:
            raise ValueError('时间须包含时区')
        if not timedelta(0) < self.end - self.start <= timedelta(days=7):
            raise ValueError('统计时间范围须大于 0 且不超过 7 天')
        if self.end > datetime.now(TIMEZONE) + timedelta(minutes=1):
            raise ValueError('结束时间须为当前或过去时间')
        return self


def scheduled_window(now: datetime, clock: str, hours: int):
    now = now.astimezone(TIMEZONE)
    hour, minute = map(int, clock.split(':'))
    end = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if end > now:
        end -= timedelta(days=1)
    return end - timedelta(hours=hours), end


def next_run(now: datetime, clock: str) -> float:
    return (scheduled_window(now, clock, 24)[1] + timedelta(days=1)).timestamp()
