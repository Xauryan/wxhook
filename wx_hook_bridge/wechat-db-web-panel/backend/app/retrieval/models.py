from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import urlsplit
import ipaddress


class RetrievalError(RuntimeError):
    pass


def public_url(value: str) -> str:
    try:
        parsed = urlsplit(value)
        if parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError()
        host = parsed.hostname.lower()
        if host in {'localhost', 'localhost.localdomain'} or host.endswith(('.local', '.internal')):
            raise ValueError()
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            address = None
        if address and not address.is_global:
            raise ValueError()
        if parsed.port not in {None, 80, 443} or len(value) > 8192:
            raise ValueError()
    except (ValueError, TypeError):
        raise RetrievalError('来源网址无效') from None
    return value


@dataclass
class SearchHit:
    title: str
    url: str
    snippet: str = ''
    summary: str = ''
    content: str = ''
    authority: int = -1
    revision_as_of: str | None = None
    score: float = 0.0
    published_at: str | None = None


@dataclass
class Document:
    title: str
    url: str
    content: str
    provider: str
    source_kind: str
    published_at: str | None = None
    revision: str | None = None
    authority: int = -1
    revision_as_of: str | None = None


@dataclass
class RetrievalResult:
    documents: list[Document] = field(default_factory=list)
    searches: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    elapsed_ms: int = 0


def parsed_date(value):
    try:
        return datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except (ValueError, TypeError):
        return None
