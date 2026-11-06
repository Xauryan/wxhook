from datetime import datetime
from typing import Literal
import re
from urllib.parse import urlsplit, urlunsplit
from pydantic import BaseModel, ConfigDict, Field, field_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)


class TaskFields(StrictModel):
    title: str = Field(min_length=1, max_length=200)
    month: str = Field(pattern=r'^\d{4}-(0[1-9]|1[0-2])$')
    objective: str = Field(min_length=1, max_length=8000)
    criteria: str = Field(min_length=1, max_length=8000)
    priority: Literal['low', 'medium', 'high'] = 'medium'
    deadline: str

    @field_validator('deadline')
    @classmethod
    def valid_deadline(cls, value):
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if parsed.tzinfo is None:
            raise ValueError('Deadline requires a timezone')
        return parsed.isoformat()


class EditTask(TaskFields):
    expected_version: int = Field(ge=1)


class Command(StrictModel):
    expected_version: int = Field(ge=1)
    reason: str = Field(default='', max_length=2000)
    target_id: str = Field(default='', max_length=36)
    item_id: str = Field(default='', max_length=36)
    text: str = Field(default='', max_length=8000)
    title: str = Field(default='', max_length=200)
    pr_links: list[str] = Field(default_factory=list, max_length=20)
    work_state: Literal['todo', 'doing', 'done'] = 'todo'
    month: str = Field(default='', pattern=r'^(\d{4}-(0[1-9]|1[0-2]))?$')

    @field_validator('pr_links')
    @classmethod
    def valid_links(cls, values):
        result = []
        for value in values:
            url = urlsplit(value.strip())
            if len(value) > 500 or url.scheme != 'https' or not url.hostname or url.username or url.password or url.query or url.fragment or not re.search(r'/(pull|pulls|merge_requests)/[1-9][0-9]*/?$', url.path):
                raise ValueError('Use a full HTTPS PR URL without credentials or query strings')
            normalized = urlunsplit((url.scheme, url.netloc, url.path.rstrip('/'), '', ''))
            if normalized not in result:
                result.append(normalized)
        return result


class AccountCredentials(BaseModel):
    model_config = ConfigDict(extra='forbid')
    login: str = Field(pattern=r'^[a-z][a-z0-9_.-]{2,49}$')
    password: str = Field(min_length=12, max_length=128)


class MemberState(StrictModel):
    enabled: bool
    reason: str = Field(min_length=1, max_length=500)


class GitIdentity(StrictModel):
    email: str = Field(min_length=3, max_length=254)

    @field_validator('email')
    @classmethod
    def valid_email(cls, value):
        value = value.strip().lower()
        if not re.fullmatch(r"[a-z0-9!#$%&'*+/=?^_`{|}~.-]+@[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)+", value):
            raise ValueError('Enter a Git email address')
        local, _ = value.split('@')
        if len(local) > 64 or local.startswith('.') or local.endswith('.') or '..' in local:
            raise ValueError('Invalid email local part')
        return value
