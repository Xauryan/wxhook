from __future__ import annotations

import copy
import hashlib
import html as stdlib_html
import io
import ipaddress
import json
import mimetypes
import os
import re
import shutil
import subprocess
import tempfile
import zipfile
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, quote, urlencode, urljoin, urlsplit, urlunsplit

import httpx
from lxml import etree, html

from .mp_tokens import MPTokenStore, SENSITIVE_TOKEN_QUERY_NAMES, TOKEN_ALIASES


DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/132.0.0.0 Safari/537.36 MicroMessenger/4.1.9"
)
SUPPORTED_FORMATS = {"markdown", "html", "pdf"}
FORMAT_ALIASES = {"md": "markdown", "markdown": "markdown", "html": "html", "pdf": "pdf"}
_ACTIVE_TAGS = {
    "script",
    "noscript",
    "object",
    "embed",
    "form",
    "input",
    "button",
    "style",
    "link",
    "base",
    "meta",
    "svg",
    "math",
}
_IMAGE_ATTRS = ("data-src", "data-original", "data-backsrc", "data-croporisrc", "src")
_VIDEO_ATTRS = ("data-src", "data-video-url", "src")
_STYLE_URL = re.compile(r"url\(\s*(['\"]?)(.*?)\1\s*\)", re.IGNORECASE)
_UNSAFE_STYLE = re.compile(r"expression\s*\(|javascript\s*:|vbscript\s*:", re.IGNORECASE)
_SPACE = re.compile(r"[ \t\f\v]+")
_BLANKS = re.compile(r"\n{3,}")
_REDIRECT_STATUSES = {301, 302, 303, 307, 308}
_MAX_REDIRECTS = 5
_ALLOWED_FETCH_SUFFIXES = (".qq.com", ".qpic.cn", ".qlogo.cn")


class MPExportError(RuntimeError):
    pass


@dataclass(slots=True)
class MPResource:
    kind: str
    source_url: str
    local_path: str = ""
    mime_type: str = ""
    size: int = 0
    status: str = "pending"
    error: str = ""


@dataclass(slots=True)
class MPArticle:
    title: str
    author: str
    published_at: str
    source_url: str
    content: html.HtmlElement
    fetched_at: str
    resources: list[MPResource] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class MPExportResult:
    export_id: str
    output_dir: str
    title: str
    source_url: str
    files: dict[str, str]
    archive: str
    resources: tuple[dict[str, Any], ...]
    warnings: tuple[str, ...]
    pdf_backend: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "export_id": self.export_id,
            "output_dir": self.output_dir,
            "title": self.title,
            "source_url": self.source_url,
            "files": dict(self.files),
            "archive": self.archive,
            "resources": list(self.resources),
            "warnings": list(self.warnings),
            "pdf_backend": self.pdf_backend,
        }


def _clean_text(value: Any) -> str:
    return _SPACE.sub(" ", str(value or "").replace("\xa0", " ")).strip()


def _slug(value: str, fallback: str = "article") -> str:
    value = re.sub(r"[<>:\"/\\|?*\x00-\x1f]", "-", _clean_text(value))
    value = re.sub(r"\s+", "-", value).strip(" .-")
    return (value[:80] or fallback).rstrip(" .")


def scrub_token_query(raw_url: str) -> str:
    """Remove dynamic request credentials before a URL reaches an export file."""

    try:
        parsed = urlsplit(raw_url)
    except ValueError:
        return ""
    pairs = [
        (name, value)
        for name, value in parse_qsl(parsed.query, keep_blank_values=True)
        if name.casefold().replace("-", "_") not in SENSITIVE_TOKEN_QUERY_NAMES
    ]
    return urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path, urlencode(pairs, doseq=True), parsed.fragment)
    )


def _safe_remote_url(raw_url: str, base_url: str) -> str:
    value = stdlib_html.unescape(str(raw_url or "")).strip()
    if not value or value.startswith(("#", "data:")):
        return value
    try:
        resolved = urljoin(base_url, value)
        parsed = urlsplit(resolved)
        port = parsed.port
    except ValueError:
        return ""
    scheme = parsed.scheme.casefold()
    default_port = 443 if scheme == "https" else 80
    if (
        scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, default_port}
    ):
        return ""
    return resolved


def _is_public_fetch_host(hostname: str | None, *, article: bool = False) -> bool:
    host = (hostname or "").casefold().rstrip(".")
    if not host:
        return False
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        # Resource fetching is intentionally domain allowlisted. Literal IPs
        # are never needed for an official-account backup.
        return False
    if article:
        return host == "mp.weixin.qq.com"
    return any(host.endswith(suffix) for suffix in _ALLOWED_FETCH_SUFFIXES)


def _safe_fetch_url(raw_url: str, base_url: str, *, article: bool = False) -> str:
    resolved = _safe_remote_url(raw_url, base_url)
    if not resolved or resolved.startswith("data:"):
        return resolved if resolved.startswith("data:") else ""
    try:
        parsed = urlsplit(resolved)
        port = parsed.port
    except ValueError:
        return ""
    if parsed.scheme.casefold() != "https" or not _is_public_fetch_host(
        parsed.hostname, article=article
    ) or parsed.username is not None or parsed.password is not None or port not in {
        None,
        443,
    }:
        return ""
    return resolved


def _is_wechat_host(hostname: str | None) -> bool:
    host = (hostname or "").casefold().rstrip(".")
    return host == "mp.weixin.qq.com"


def _is_direct_video_url(raw_url: str) -> bool:
    path = urlsplit(raw_url).path.casefold()
    return path.endswith((".mp4", ".m4v", ".mov", ".webm", ".m3u8"))


def _first_xpath_text(document: html.HtmlElement, expressions: Sequence[str]) -> str:
    for expression in expressions:
        try:
            matches = document.xpath(expression)
        except etree.XPathError:
            continue
        for match in matches:
            if isinstance(match, etree._Element):
                value = match.text_content()
            else:
                value = str(match)
            if cleaned := _clean_text(value):
                return cleaned
    return ""


def _normalize_formats(formats: Iterable[str]) -> tuple[str, ...]:
    normalized: list[str] = []
    for value in formats:
        name = FORMAT_ALIASES.get(str(value).casefold())
        if not name:
            raise MPExportError(f"unsupported export format: {value}")
        if name not in normalized:
            normalized.append(name)
    if not normalized:
        raise MPExportError("at least one export format is required")
    return tuple(normalized)


class MPArticleExporter:
    def __init__(
        self,
        token_store: MPTokenStore | None = None,
        *,
        client: httpx.Client | None = None,
        timeout: float = 30.0,
        max_article_bytes: int = 8 * 1024 * 1024,
        max_resource_bytes: int = 256 * 1024 * 1024,
        max_resources: int = 256,
        max_total_resource_bytes: int = 512 * 1024 * 1024,
        user_agent: str = DEFAULT_USER_AGENT,
        pdf_renderer: Callable[[Path, Path], str | None] | None = None,
    ) -> None:
        self.token_store = token_store
        self.user_agent = user_agent
        self.max_article_bytes = max_article_bytes
        self.max_resource_bytes = max_resource_bytes
        self.max_resources = max(1, max_resources)
        self.max_total_resource_bytes = max(1, max_total_resource_bytes)
        self.pdf_renderer = pdf_renderer
        self._owns_client = client is None
        self.client = client or httpx.Client(
            timeout=httpx.Timeout(timeout),
            follow_redirects=False,
            limits=httpx.Limits(max_connections=8, max_keepalive_connections=4),
        )

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    def __enter__(self) -> "MPArticleExporter":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _token_values(self) -> dict[str, str]:
        return self.token_store.current_values() if self.token_store else {}

    def _authorize_article_url(self, raw_url: str) -> str:
        parsed = urlsplit(raw_url)
        if parsed.scheme.casefold() != "https" or not parsed.netloc:
            raise MPExportError("article URL must use HTTPS")
        if not _is_public_fetch_host(parsed.hostname, article=True):
            raise MPExportError("article URL must point to mp.weixin.qq.com")
        pairs = parse_qsl(parsed.query, keep_blank_values=True)
        token_names = set(self._token_values())
        # Replace stale credentials rather than retaining an expired query
        # value supplied by a copied article URL.
        pairs = [
            (name, value)
            for name, value in pairs
            if TOKEN_ALIASES.get(name.casefold().replace("-", "_"), name.casefold().replace("-", "_"))
            not in token_names
        ]
        for name, value in self._token_values().items():
            if value:
                pairs.append((name, value))
        return urlunsplit(
            (parsed.scheme, parsed.netloc, parsed.path, urlencode(pairs), parsed.fragment)
        )

    def _get_with_redirects(
        self,
        raw_url: str,
        *,
        referer: str = "",
        kind: str = "article",
        article: bool = False,
        stream: bool = False,
    ) -> tuple[httpx.Response, str]:
        current = raw_url
        for _ in range(_MAX_REDIRECTS + 1):
            target = _safe_fetch_url(current, raw_url, article=article)
            if not target or target.startswith("data:"):
                raise MPExportError("remote resource host or scheme is not allowed")
            request = self.client.build_request(
                "GET",
                target,
                headers=self._headers(target, referer=referer, kind=kind),
            )
            response = self.client.send(request, stream=stream, follow_redirects=False)
            if response.status_code not in _REDIRECT_STATUSES:
                return response, target
            location = response.headers.get("location")
            response.close()
            if not location:
                raise MPExportError("redirect response did not include a location")
            current = urljoin(target, location)
        raise MPExportError("too many redirects while fetching article resource")

    def _headers(self, url: str, *, referer: str = "", kind: str = "article") -> dict[str, str]:
        headers = {
            "User-Agent": self.user_agent,
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.7",
            "Cache-Control": "no-cache",
        }
        if kind == "article":
            headers["Accept"] = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
            headers["Referer"] = "https://mp.weixin.qq.com/"
        elif kind == "image":
            headers["Accept"] = "image/avif,image/webp,image/apng,image/*,*/*;q=0.8"
        elif kind == "video":
            headers["Accept"] = "video/*,*/*;q=0.8"
        else:
            headers["Accept"] = "*/*"
        if referer:
            safe_referer = scrub_token_query(referer)
            headers["Referer"] = safe_referer
            origin = urlsplit(safe_referer)
            if origin.scheme and origin.netloc:
                headers["Origin"] = f"{origin.scheme}://{origin.netloc}"
        parsed_url = urlsplit(url)
        if parsed_url.scheme.casefold() == "https" and _is_wechat_host(parsed_url.hostname):
            tokens = self._token_values()
            if tokens:
                headers["Cookie"] = "; ".join(
                    f"{name}={quote(value, safe='%+/_=-')}"
                    for name, value in tokens.items()
                )
        return headers

    @staticmethod
    def _read_bounded_response(
        response: httpx.Response,
        limit: int,
        *,
        label: str,
    ) -> bytes:
        try:
            declared_size = int(response.headers.get("content-length") or 0)
        except ValueError:
            declared_size = 0
        if declared_size > limit:
            raise MPExportError(f"{label} exceeds configured size limit")
        payload = bytearray()
        for chunk in response.iter_bytes():
            payload.extend(chunk)
            if len(payload) > limit:
                raise MPExportError(f"{label} exceeds configured size limit")
        return bytes(payload)

    @staticmethod
    def _decode_response(response: httpx.Response, payload: bytes) -> str:
        encoding = response.encoding or response.charset_encoding or "utf-8"
        try:
            return payload.decode(encoding, errors="replace")
        except LookupError:
            return payload.decode("utf-8", errors="replace")

    def fetch_html(self, raw_url: str) -> tuple[str, str]:
        request_url = self._authorize_article_url(raw_url)
        response: httpx.Response | None = None
        try:
            response, final_url = self._get_with_redirects(
                request_url,
                kind="article",
                article=True,
                stream=True,
            )
            response.raise_for_status()
            content_type = response.headers.get("content-type", "").casefold()
            if content_type and not any(
                expected in content_type
                for expected in (
                    "text/html",
                    "application/xhtml+xml",
                    "text/plain",
                )
            ):
                raise MPExportError(f"article response is not HTML: {content_type}")
            payload = self._read_bounded_response(
                response,
                self.max_article_bytes,
                label="article response",
            )
            text = self._decode_response(response, payload)
        except httpx.HTTPStatusError as exc:
            raise MPExportError(
                f"article request returned HTTP {exc.response.status_code}"
            ) from exc
        except httpx.RequestError as exc:
            raise MPExportError(
                f"article request failed ({type(exc).__name__})"
            ) from exc
        finally:
            if response is not None:
                response.close()
        return text, final_url

    def parse_article(self, html_text: str, source_url: str) -> MPArticle:
        parser = html.HTMLParser(encoding="utf-8", recover=True, remove_comments=True)
        try:
            document = html.document_fromstring(html_text, parser=parser, base_url=source_url)
        except (etree.ParserError, ValueError) as exc:
            raise MPExportError(f"cannot parse article HTML: {exc}") from exc

        title = _first_xpath_text(
            document,
            (
                "//meta[@property='og:title']/@content",
                "//meta[@name='twitter:title']/@content",
                "//*[@id='activity-name']",
                "//h1[1]",
                "//title",
            ),
        ) or "WeChat article"
        author = _first_xpath_text(
            document,
            (
                "//meta[@name='author']/@content",
                "//meta[@property='article:author']/@content",
                "//*[@id='js_name']",
                "//*[contains(concat(' ', normalize-space(@class), ' '), ' account_nickname ')]",
            ),
        )
        published_at = _first_xpath_text(
            document,
            (
                "//meta[@property='article:published_time']/@content",
                "//*[@id='publish_time']",
                "//*[contains(concat(' ', normalize-space(@class), ' '), ' publish_time ')]",
            ),
        )
        if not published_at:
            match = re.search(r"\bct\s*=\s*[\"'](\d{9,13})[\"']", html_text)
            if match:
                timestamp = int(match.group(1))
                if timestamp > 10_000_000_000:
                    timestamp //= 1000
                try:
                    published_at = datetime.fromtimestamp(timestamp, UTC).isoformat()
                except (OSError, OverflowError, ValueError):
                    pass

        content_candidate: etree._Element | None = None
        for expression in ("//*[@id='js_content']", "//article", "//main", "//body"):
            candidates = document.xpath(expression)
            if candidates:
                content_candidate = candidates[0]
                break
        if content_candidate is None:
            raise MPExportError("article HTML does not contain a content body")
        content = copy.deepcopy(content_candidate)
        public_source = scrub_token_query(source_url)
        article = MPArticle(
            title=title,
            author=author,
            published_at=published_at,
            source_url=public_source,
            content=content,
            fetched_at=datetime.now(UTC).isoformat(),
        )
        self._prepare_content(article)
        return article

    def _prepare_content(self, article: MPArticle) -> None:
        content = article.content
        for element in list(content.iter()):
            tag = (
                str(element.tag).rsplit("}", 1)[-1].casefold()
                if isinstance(element.tag, str)
                else ""
            )
            if tag in _ACTIVE_TAGS:
                parent = element.getparent()
                if parent is not None:
                    parent.remove(element)
                continue
            for attribute in list(element.attrib):
                normalized = attribute.casefold()
                local_name = normalized.rsplit("}", 1)[-1].rsplit(":", 1)[-1]
                value = element.attrib.get(attribute, "")
                if (
                    local_name.startswith("on")
                    or local_name
                    in {
                        "nonce",
                        "integrity",
                        "srcdoc",
                        "action",
                        "formaction",
                        "manifest",
                    }
                ):
                    del element.attrib[attribute]
                elif local_name in {"href", "src", "poster"} and attribute != local_name:
                    # Drop namespaced URL attributes such as SVG xlink:href.
                    del element.attrib[attribute]
                elif local_name == "style" and _UNSAFE_STYLE.search(value):
                    del element.attrib[attribute]
                elif local_name == "style":
                    def _scrub_style(match: re.Match[str]) -> str:
                        resolved_style_url = _safe_fetch_url(
                            match.group(2), article.source_url
                        )
                        if not resolved_style_url or resolved_style_url.startswith("data:"):
                            return ""
                        return f"url({resolved_style_url})"

                    element.attrib[attribute] = _STYLE_URL.sub(_scrub_style, value)
            if tag == "img":
                source = next(
                    (element.get(name, "") for name in _IMAGE_ATTRS if element.get(name)),
                    "",
                )
                resolved = _safe_fetch_url(source, article.source_url)
                if resolved:
                    element.set("src", resolved)
                else:
                    element.attrib.pop("src", None)
                for name in _IMAGE_ATTRS:
                    if name != "src":
                        element.attrib.pop(name, None)
                element.attrib.pop("srcset", None)
                element.set("loading", "lazy")
            elif tag in {"video", "mp-common-videosnap", "mpvideo"}:
                if tag != "video":
                    # WeChat uses custom elements for its inline player. A
                    # standard video element is portable in an offline backup.
                    element.tag = "video"
                source = next(
                    (element.get(name, "") for name in _VIDEO_ATTRS if element.get(name)),
                    "",
                )
                resolved = _safe_fetch_url(source, article.source_url)
                if resolved:
                    element.set("src", resolved)
                else:
                    element.attrib.pop("src", None)
                poster = _safe_fetch_url(
                    element.get("poster", ""), article.source_url
                )
                if poster:
                    element.set("poster", poster)
                else:
                    element.attrib.pop("poster", None)
                element.set("controls", "controls")
                element.attrib.pop("autoplay", None)
                for name in _VIDEO_ATTRS:
                    if name != "src":
                        element.attrib.pop(name, None)
            elif tag == "source":
                resolved = _safe_fetch_url(
                    element.get("data-src") or element.get("src") or "",
                    article.source_url,
                )
                if resolved:
                    element.set("src", resolved)
                else:
                    element.attrib.pop("src", None)
                element.attrib.pop("data-src", None)
            elif tag == "iframe":
                resolved = _safe_fetch_url(
                    element.get("data-src") or element.get("src") or "",
                    article.source_url,
                )
                if resolved:
                    element.set("src", resolved)
                else:
                    element.attrib.pop("src", None)
                element.attrib.pop("data-src", None)
            if href := element.get("href"):
                if href.startswith("#"):
                    resolved_href = href
                else:
                    resolved_href = _safe_remote_url(href, article.source_url)
                if resolved_href and not resolved_href.startswith("data:"):
                    element.set("href", scrub_token_query(resolved_href))
                    element.set("rel", "noreferrer noopener")
                else:
                    element.attrib.pop("href", None)

    def _resource_references(
        self, article: MPArticle
    ) -> list[tuple[html.HtmlElement, str, str, str]]:
        references: list[tuple[html.HtmlElement, str, str, str]] = []
        for element in article.content.iter():
            if not isinstance(element.tag, str):
                continue
            tag = element.tag.casefold()
            if tag == "img" and element.get("src"):
                references.append((element, "src", element.get("src", ""), "image"))
            elif tag in {"video", "mp-common-videosnap", "mpvideo"} and element.get("src"):
                references.append((element, "src", element.get("src", ""), "video"))
            elif tag == "source" and element.get("src"):
                references.append((element, "src", element.get("src", ""), "video"))
            elif tag == "iframe" and element.get("src") and _is_direct_video_url(element.get("src", "")):
                references.append((element, "src", element.get("src", ""), "video"))
            if element.get("poster"):
                resolved = _safe_fetch_url(element.get("poster", ""), article.source_url)
                if resolved:
                    element.set("poster", resolved)
                    references.append((element, "poster", resolved, "image"))
            style = element.get("style", "")
            for match in _STYLE_URL.finditer(style):
                resolved = _safe_fetch_url(match.group(2), article.source_url)
                if resolved:
                    references.append((element, "style", resolved, "image"))
        return references

    def _discover_embedded_video(self, raw_url: str, *, referer: str) -> str:
        """Resolve WeChat's video-player iframe to a direct media URL."""

        if _is_direct_video_url(raw_url):
            return raw_url
        parsed = urlsplit(raw_url)
        marker = f"{parsed.path}?{parsed.query}".casefold()
        if not any(token in marker for token in ("videoplayer", "video_player", "mpvideo")):
            return ""
        request_url = self._safe_fetch_url_for_video(raw_url, referer)
        if not request_url:
            return ""
        response: httpx.Response | None = None
        try:
            response, _ = self._get_with_redirects(
                request_url,
                referer=referer,
                kind="video",
                stream=True,
            )
            response.raise_for_status()
            payload = self._read_bounded_response(
                response,
                2 * 1024 * 1024,
                label="embedded video response",
            )
            text = self._decode_response(response, payload).replace("\\/", "/").replace(
                "\\u002F", "/"
            )
        except (httpx.HTTPError, MPExportError, ValueError):
            return ""
        finally:
            if response is not None:
                response.close()
        patterns = (
            r"(?:video_url|videoUrl|mp4_url|mp4Url|url_info)\s*[:=]\s*[\"']([^\"']+)",
            r"<(?:video|source)[^>]+(?:src|data-src)=[\"']([^\"']+)",
        )
        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                candidate = _safe_fetch_url(match.group(1), raw_url)
                if candidate:
                    return candidate
        return ""

    @staticmethod
    def _safe_fetch_url_for_video(raw_url: str, referer: str) -> str:
        return _safe_fetch_url(raw_url, referer)

    def _prepare_embedded_videos(
        self, article: MPArticle, *, resolve: bool = True
    ) -> None:
        for element in list(article.content.iter("iframe")):
            source = element.get("src", "")
            if not source:
                parent = element.getparent()
                if parent is not None:
                    parent.remove(element)
                continue
            direct = (
                self._discover_embedded_video(source, referer=article.source_url)
                if resolve
                else ""
            )
            if not direct:
                element.tag = "a"
                element.attrib.clear()
                element.set("href", scrub_token_query(source))
                element.set("rel", "noreferrer noopener")
                element.text = "Embedded video"
                continue
            element.tag = "video"
            for attribute in list(element.attrib):
                if attribute not in {"class", "poster", "src"}:
                    del element.attrib[attribute]
            element.set("src", direct)
            element.set("controls", "controls")

    @staticmethod
    def _extension(url: str, mime_type: str, kind: str) -> str:
        content_type = mime_type.split(";", 1)[0].strip().casefold()
        extension = (
            mimetypes.guess_extension(content_type)
            if content_type
            and content_type not in {"application/octet-stream", "binary/octet-stream"}
            else None
        )
        if extension == ".jpe":
            extension = ".jpg"
        if extension:
            return extension
        suffix = Path(urlsplit(url).path).suffix.casefold()
        if 1 < len(suffix) <= 8 and re.fullmatch(r"\.[a-z0-9]+", suffix):
            return suffix
        return ".mp4" if kind == "video" else ".bin"

    def _save_data_url(
        self,
        raw_url: str,
        assets_dir: Path,
        kind: str,
        *,
        max_bytes: int | None = None,
    ) -> MPResource:
        import base64

        header, separator, payload = raw_url.partition(",")
        resource = MPResource(kind=kind, source_url="data:")
        if not separator:
            resource.status = "failed"
            resource.error = "invalid data URL"
            return resource
        mime_type = header[5:].split(";", 1)[0] or "application/octet-stream"
        try:
            data = base64.b64decode(payload, validate=True) if ";base64" in header else payload.encode()
        except (ValueError, TypeError):
            resource.status = "failed"
            resource.error = "invalid data URL payload"
            return resource
        limit = min(self.max_resource_bytes, max_bytes or self.max_resource_bytes)
        if len(data) > limit:
            resource.status = "failed"
            resource.error = "resource exceeds configured size limit"
            return resource
        digest = hashlib.sha256(data).hexdigest()
        extension = self._extension("", mime_type, kind)
        output = assets_dir / f"inline-{digest[:12]}{extension}"
        if not output.exists():
            output.write_bytes(data)
        resource.local_path = output.relative_to(assets_dir.parent).as_posix()
        resource.mime_type = mime_type
        resource.size = len(data)
        resource.status = "saved"
        return resource

    def _download_resource(
        self,
        raw_url: str,
        assets_dir: Path,
        *,
        referer: str,
        kind: str,
        max_bytes: int | None = None,
    ) -> MPResource:
        public_url = scrub_token_query(raw_url)
        if raw_url.startswith("data:"):
            return self._save_data_url(
                raw_url, assets_dir, kind, max_bytes=max_bytes
            )
        resource = MPResource(kind=kind, source_url=public_url)
        limit = min(self.max_resource_bytes, max_bytes or self.max_resource_bytes)
        response: httpx.Response | None = None
        try:
            response, _ = self._get_with_redirects(
                raw_url,
                referer=referer,
                kind=kind,
                stream=True,
            )
            response.raise_for_status()
            declared_size = int(response.headers.get("content-length") or 0)
            if declared_size > limit:
                raise MPExportError("resource exceeds configured size limit")
            buffer = io.BytesIO()
            for chunk in response.iter_bytes():
                buffer.write(chunk)
                if buffer.tell() > limit:
                    raise MPExportError("resource exceeds configured size limit")
            data = buffer.getvalue()
            mime_type = response.headers.get("content-type", "").split(";", 1)[0]
        except httpx.HTTPStatusError as exc:
            resource.status = "failed"
            resource.error = f"HTTP {exc.response.status_code}"
            return resource
        except httpx.RequestError as exc:
            resource.status = "failed"
            resource.error = f"request failed ({type(exc).__name__})"
            return resource
        except (MPExportError, ValueError) as exc:
            resource.status = "failed"
            resource.error = str(exc)
            return resource
        finally:
            if response is not None:
                response.close()
        digest = hashlib.sha256(raw_url.encode("utf-8")).hexdigest()
        base_name = _slug(Path(urlsplit(public_url).path).stem, kind)[:48]
        extension = self._extension(public_url, mime_type, kind)
        output = assets_dir / f"{base_name}-{digest[:12]}{extension}"
        if not output.exists():
            output.write_bytes(data)
        resource.local_path = output.relative_to(assets_dir.parent).as_posix()
        resource.mime_type = mime_type
        resource.size = len(data)
        resource.status = "saved"
        return resource

    def localize_resources(self, article: MPArticle, output_dir: Path | str) -> None:
        output_dir = Path(output_dir)
        assets_dir = output_dir / "assets"
        assets_dir.mkdir(parents=True, exist_ok=True)
        cache: dict[tuple[str, str], MPResource] = {}
        references = self._resource_references(article)
        if len(references) > self.max_resources:
            article.warnings.append(
                f"resource count exceeded {self.max_resources}; remaining remote references were kept"
            )
            references = references[: self.max_resources]
        remaining_bytes = self.max_total_resource_bytes
        for element, attribute, raw_url, kind in references:
            key = (raw_url, kind)
            resource = cache.get(key)
            if resource is None:
                if remaining_bytes <= 0:
                    article.warnings.append(
                        "resource byte budget exhausted; remaining remote references were kept"
                    )
                    break
                resource = self._download_resource(
                    raw_url,
                    assets_dir,
                    referer=article.source_url,
                    kind=kind,
                    max_bytes=remaining_bytes,
                )
                cache[key] = resource
                article.resources.append(resource)
                if resource.status == "saved":
                    remaining_bytes -= resource.size
                if resource.status != "saved":
                    article.warnings.append(
                        f"failed to localize {kind}: {resource.source_url or 'inline resource'}"
                    )
            replacement = resource.local_path if resource.status == "saved" else resource.source_url
            if attribute == "style":
                style = element.get("style", "")
                # ``raw_url`` is resolved against the article URL, while the
                # style declaration may still contain a relative spelling.
                # Replace both forms so background images are actually local.
                rewritten = style.replace(raw_url, replacement)
                for match in _STYLE_URL.finditer(style):
                    candidate = _safe_remote_url(match.group(2), article.source_url)
                    if candidate == raw_url:
                        rewritten = rewritten.replace(match.group(2), replacement)
                element.set("style", rewritten)
            elif replacement:
                element.set(attribute, replacement)

    @staticmethod
    def _serialize_content(content: html.HtmlElement) -> str:
        return html.tostring(content, encoding="unicode", method="html")

    @staticmethod
    def _public_content(content: html.HtmlElement) -> html.HtmlElement:
        public = copy.deepcopy(content)
        for element in public.iter():
            if not isinstance(element.tag, str):
                continue
            tag = element.tag.rsplit("}", 1)[-1].casefold()
            for attribute in list(element.attrib):
                local_name = attribute.casefold().rsplit("}", 1)[-1].rsplit(":", 1)[-1]
                value = element.attrib.get(attribute, "")
                if local_name in {"src", "poster"}:
                    if value.startswith(("http://", "https://")):
                        element.attrib[attribute] = scrub_token_query(value)
                elif local_name == "href":
                    if value.startswith(("http://", "https://")):
                        element.attrib[attribute] = scrub_token_query(value)
                    elif value.startswith("data:") and tag == "a":
                        del element.attrib[attribute]
                elif local_name == "style":
                    element.attrib[attribute] = _STYLE_URL.sub(
                        lambda match: (
                            f"url({scrub_token_query(match.group(2))})"
                            if match.group(2).startswith(("http://", "https://"))
                            else match.group(0)
                        ),
                        value,
                    )
        return public

    @staticmethod
    def _remove_remote_references(content: html.HtmlElement) -> None:
        """Keep PDF rendering offline even when HTML retains remote fallbacks."""

        for element in content.iter():
            if not isinstance(element.tag, str):
                continue
            for attribute in list(element.attrib):
                local_name = attribute.casefold().rsplit("}", 1)[-1].rsplit(":", 1)[-1]
                value = element.attrib.get(attribute, "").strip()
                if local_name in {"src", "poster", "href"}:
                    if value.startswith(("http://", "https://", "//")):
                        del element.attrib[attribute]
                elif local_name == "style":
                    element.attrib[attribute] = _STYLE_URL.sub(
                        lambda match: (
                            ""
                            if match.group(2).strip().startswith(
                                ("http://", "https://", "//")
                            )
                            else match.group(0)
                        ),
                        value,
                    )

    def _standalone_html(self, article: MPArticle, *, offline: bool = False) -> str:
        title = stdlib_html.escape(article.title)
        author = stdlib_html.escape(article.author)
        published = stdlib_html.escape(article.published_at)
        source = stdlib_html.escape(article.source_url, quote=True)
        public_content = self._public_content(article.content)
        if offline:
            self._remove_remote_references(public_content)
        content = self._serialize_content(public_content)
        metadata = ""
        if author:
            metadata += f'<span class="author">{author}</span>'
        if published:
            metadata += f'<time>{published}</time>'
        source_markup = (
            f'<span class="source">{source}</span>'
            if offline
            else f'<a class="source" href="{source}">{source}</a>'
        )
        return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="source" content="{source}">
  <title>{title}</title>
  <style>
    :root {{ color-scheme: light; --ink:#202124; --muted:#6f746f; --paper:#f3f0e8; }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; color:var(--ink); background:var(--paper); font-family:"Noto Serif SC","Source Han Serif SC",serif; line-height:1.8; }}
    .page {{ width:min(780px, calc(100% - 32px)); margin:32px auto; padding:clamp(24px,6vw,64px); background:#fff; box-shadow:0 18px 60px rgba(31,35,31,.12); }}
    h1 {{ margin:.2em 0 .35em; font-size:clamp(30px,6vw,48px); line-height:1.2; }}
    .meta {{ display:flex; gap:16px; flex-wrap:wrap; color:var(--muted); margin-bottom:32px; }}
    .source {{ color:#48654d; overflow-wrap:anywhere; }}
    img,video,iframe {{ display:block; max-width:100%; height:auto; margin:1.4em auto; }}
    video {{ width:100%; background:#111; }}
    blockquote {{ margin:1.5em 0; padding:.2em 1.2em; border-left:4px solid #688c6d; color:#4f5650; }}
    pre {{ overflow:auto; padding:16px; background:#f5f5f2; }}
    table {{ width:100%; border-collapse:collapse; overflow:auto; }}
    th,td {{ border:1px solid #ddd; padding:8px; }}
    @media print {{ body {{ background:#fff; }} .page {{ width:auto; margin:0; padding:0; box-shadow:none; }} }}
  </style>
</head>
<body>
  <article class="page">
    <header><h1>{title}</h1><div class="meta">{metadata}</div></header>
    {content}
    <footer><p>Source: {source_markup}</p></footer>
  </article>
</body>
</html>
"""

    def _markdown_node(self, element: etree._Element, depth: int = 0) -> str:
        if not isinstance(element.tag, str):
            return ""
        tag = element.tag.casefold()
        text = element.text or ""
        children = "".join(
            self._markdown_node(child, depth + 1) + (child.tail or "")
            for child in element
        )
        inner = text + children
        if tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            return f"\n{'#' * int(tag[1])} {_clean_text(inner)}\n\n"
        if tag in {"p", "section", "article", "main", "header", "footer"}:
            return f"\n{inner.strip()}\n\n" if inner.strip() else ""
        if tag in {"div", "figure", "figcaption"}:
            return f"\n{inner.strip()}\n" if inner.strip() else ""
        if tag == "br":
            return "  \n"
        if tag in {"strong", "b"}:
            return f"**{inner.strip()}**"
        if tag in {"em", "i"}:
            return f"*{inner.strip()}*"
        if tag in {"del", "s"}:
            return f"~~{inner.strip()}~~"
        if tag == "code" and element.getparent() is not None and str(element.getparent().tag).casefold() != "pre":
            code = inner.strip().replace("`", "\\`")
            return f"`{code}`"
        if tag == "pre":
            return f"\n```\n{element.text_content().rstrip()}\n```\n\n"
        if tag == "blockquote":
            lines = inner.strip().splitlines()
            return "\n" + "\n".join(f"> {line}" for line in lines) + "\n\n"
        if tag == "a":
            label = _clean_text(inner) or element.get("href", "")
            href = element.get("href", "")
            return f"[{label}]({href})" if href else label
        if tag == "img":
            return f"\n![{_clean_text(element.get('alt', ''))}]({element.get('src', '')})\n"
        if tag in {"video", "mp-common-videosnap", "mpvideo"}:
            source = element.get("src", "")
            nested_sources = [item.get("src", "") for item in element.xpath(".//source[@src]")]
            sources = [value for value in (source, *nested_sources) if value]
            poster = element.get("poster", "")
            attributes = " controls"
            if poster:
                attributes += f' poster="{stdlib_html.escape(poster, quote=True)}"'
            if sources:
                attributes += f' src="{stdlib_html.escape(sources[0], quote=True)}"'
            return f"\n<video{attributes}></video>\n\n"
        if tag == "source":
            return ""
        if tag == "iframe":
            source = element.get("src", "")
            return f"\n[Embedded video]({source})\n\n" if source else ""
        if tag == "li":
            return f"\n- {inner.strip()}"
        if tag in {"ul", "ol"}:
            return f"\n{inner.strip()}\n\n"
        if tag == "hr":
            return "\n---\n\n"
        if tag == "table":
            return "\n" + html.tostring(element, encoding="unicode", method="html") + "\n\n"
        return inner

    def _markdown(self, article: MPArticle) -> str:
        frontmatter = [
            "---",
            f"title: {json.dumps(article.title, ensure_ascii=False)}",
            f"author: {json.dumps(article.author, ensure_ascii=False)}",
            f"published_at: {json.dumps(article.published_at, ensure_ascii=False)}",
            f"source: {json.dumps(article.source_url, ensure_ascii=False)}",
            f"fetched_at: {json.dumps(article.fetched_at, ensure_ascii=False)}",
            "---",
            "",
            f"# {article.title}",
            "",
        ]
        body = self._markdown_node(self._public_content(article.content))
        body = _BLANKS.sub("\n\n", body).strip()
        return "\n".join(frontmatter) + body + "\n"

    @staticmethod
    def _minimal_pdf(text: str, output: Path) -> None:
        safe_lines = []
        for line in text.splitlines()[:80]:
            ascii_line = line.encode("latin-1", errors="replace").decode("latin-1")
            ascii_line = ascii_line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            safe_lines.append(ascii_line[:100])
        commands = ["BT", "/F1 10 Tf", "50 790 Td", "14 TL"]
        for line in safe_lines or ["WeChat article backup"]:
            commands.append(f"({line}) Tj")
            commands.append("T*")
        commands.append("ET")
        stream = "\n".join(commands).encode("latin-1", errors="replace")
        objects = [
            b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
            b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        ]
        payload = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
        offsets = [0]
        for index, obj in enumerate(objects, 1):
            offsets.append(len(payload))
            payload.extend(f"{index} 0 obj\n".encode())
            payload.extend(obj)
            payload.extend(b"\nendobj\n")
        xref = len(payload)
        payload.extend(f"xref\n0 {len(objects) + 1}\n".encode())
        payload.extend(b"0000000000 65535 f \n")
        for offset in offsets[1:]:
            payload.extend(f"{offset:010d} 00000 n \n".encode())
        payload.extend(
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
        )
        output.write_bytes(payload)

    @staticmethod
    def _reportlab_pdf(text: str, output: Path) -> bool:
        """Render a readable Unicode fallback when browser PDF is unavailable."""

        try:
            from reportlab.lib.enums import TA_LEFT
            from reportlab.lib.pagesizes import A4
            from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
            from reportlab.lib.units import mm
            from reportlab.pdfbase import pdfmetrics
            from reportlab.pdfbase.ttfonts import TTFont
            from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer
        except ImportError:
            return False

        font_name = "Helvetica"
        font_candidates = (
            Path(r"C:\Windows\Fonts\msyh.ttc"),
            Path(r"C:\Windows\Fonts\simhei.ttf"),
            Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        )
        for font_path in font_candidates:
            if not font_path.is_file():
                continue
            try:
                # ReportLab supports TTC files with subfontIndex on newer
                # releases; retry as a regular TTF for older installations.
                try:
                    font = TTFont("MPUnicode", str(font_path), subfontIndex=0)
                except TypeError:
                    font = TTFont("MPUnicode", str(font_path))
                pdfmetrics.registerFont(font)
                font_name = "MPUnicode"
                break
            except Exception:
                continue

        styles = getSampleStyleSheet()
        body = ParagraphStyle(
            "MPBody",
            parent=styles["BodyText"],
            fontName=font_name,
            fontSize=9.5,
            leading=14,
            alignment=TA_LEFT,
            spaceAfter=5,
        )
        story = []
        for line in text.splitlines():
            cleaned = stdlib_html.escape(line.strip())
            if cleaned:
                story.append(Paragraph(cleaned, body))
            else:
                story.append(Spacer(1, 3 * mm))
        if not story:
            story.append(Paragraph("WeChat article backup", body))
        try:
            document = SimpleDocTemplate(
                str(output),
                pagesize=A4,
                rightMargin=16 * mm,
                leftMargin=16 * mm,
                topMargin=16 * mm,
                bottomMargin=16 * mm,
                title="WeChat article backup",
            )
            document.build(story)
            return output.is_file() and output.stat().st_size > 0
        except Exception:
            return False

    def _render_pdf(self, html_path: Path, pdf_path: Path, plain_text: str) -> str:
        if self.pdf_renderer:
            backend = self.pdf_renderer(html_path, pdf_path)
            return str(backend or "custom")
        try:
            from playwright.sync_api import sync_playwright

            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                page = browser.new_page(viewport={"width": 1080, "height": 1440})
                page.route(re.compile(r"^https?://", re.IGNORECASE), lambda route: route.abort())
                page.goto(
                    html_path.resolve().as_uri(),
                    wait_until="load",
                    timeout=30_000,
                )
                page.wait_for_timeout(200)
                page.emulate_media(media="print")
                page.pdf(
                    path=str(pdf_path),
                    format="A4",
                    print_background=True,
                    margin={"top": "16mm", "right": "14mm", "bottom": "16mm", "left": "14mm"},
                )
                browser.close()
            return "playwright"
        except Exception:
            pdf_path.unlink(missing_ok=True)
        executable = shutil.which("wkhtmltopdf")
        if executable:
            try:
                subprocess.run(
                    [
                        executable,
                        "--enable-local-file-access",
                        "--disable-javascript",
                        "--disable-external-links",
                        "--quiet",
                        str(html_path),
                        str(pdf_path),
                    ],
                    check=True,
                    timeout=120,
                    capture_output=True,
                )
                return "wkhtmltopdf"
            except (OSError, subprocess.SubprocessError):
                pdf_path.unlink(missing_ok=True)
        if self._reportlab_pdf(plain_text, pdf_path):
            return "reportlab"
        self._minimal_pdf(plain_text, pdf_path)
        return "minimal"

    @staticmethod
    def _write_manifest(
        article: MPArticle,
        files: Mapping[str, str],
        output: Path,
        *,
        pdf_backend: str,
    ) -> Path:
        manifest = {
            "schema_version": 1,
            "title": article.title,
            "author": article.author,
            "published_at": article.published_at,
            "source_url": article.source_url,
            "fetched_at": article.fetched_at,
            "files": dict(files),
            "resources": [asdict(resource) for resource in article.resources],
            "warnings": article.warnings,
            "pdf_backend": pdf_backend,
        }
        path = output / "manifest.json"
        path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    @staticmethod
    def _archive(output_dir: Path) -> Path:
        archive_path = output_dir / "backup.zip"
        with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(output_dir.rglob("*")):
                if path.is_file() and not path.is_symlink() and path != archive_path:
                    archive.write(path, path.relative_to(output_dir).as_posix())
        return archive_path

    def export_html_text(
        self,
        html_text: str,
        *,
        source_url: str,
        output_dir: Path | str,
        formats: Iterable[str] = ("markdown", "html", "pdf"),
        localize_resources: bool = True,
    ) -> MPExportResult:
        normalized_formats = _normalize_formats(formats)
        output_dir = Path(output_dir).resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        article = self.parse_article(html_text, source_url)
        self._prepare_embedded_videos(article, resolve=localize_resources)
        if localize_resources:
            self.localize_resources(article, output_dir)

        base_name = _slug(article.title)
        files: dict[str, str] = {}
        standalone = self._standalone_html(article)
        markdown = self._markdown(article)
        if "markdown" in normalized_formats:
            markdown_path = output_dir / f"{base_name}.md"
            markdown_path.write_text(markdown, encoding="utf-8")
            files["markdown"] = markdown_path.name
        if "html" in normalized_formats:
            html_path = output_dir / f"{base_name}.html"
            html_path.write_text(standalone, encoding="utf-8")
            files["html"] = html_path.name

        pdf_backend = ""
        if "pdf" in normalized_formats:
            handle, temporary_name = tempfile.mkstemp(
                prefix=".mp-render-", suffix=".html", dir=output_dir
            )
            os.close(handle)
            temporary_html = Path(temporary_name)
            temporary_html.write_text(
                self._standalone_html(article, offline=True),
                encoding="utf-8",
            )
            pdf_path = output_dir / f"{base_name}.pdf"
            try:
                pdf_backend = self._render_pdf(
                    temporary_html,
                    pdf_path,
                    article.content.text_content(),
                )
            finally:
                temporary_html.unlink(missing_ok=True)
            if not pdf_path.is_file() or pdf_path.stat().st_size == 0:
                raise MPExportError("PDF renderer did not produce an output file")
            files["pdf"] = pdf_path.name
            if pdf_backend in {"minimal", "reportlab"}:
                article.warnings.append(
                    "Chromium/wkhtmltopdf was unavailable; PDF used the text-only fallback renderer"
                )
        manifest_path = self._write_manifest(
            article,
            files,
            output_dir,
            pdf_backend=pdf_backend,
        )
        files["manifest"] = manifest_path.name
        archive = self._archive(output_dir)
        export_id = output_dir.name
        return MPExportResult(
            export_id=export_id,
            output_dir=str(output_dir),
            title=article.title,
            source_url=article.source_url,
            files=files,
            archive=archive.name,
            resources=tuple(asdict(resource) for resource in article.resources),
            warnings=tuple(article.warnings),
            pdf_backend=pdf_backend,
        )

    def export(
        self,
        url: str,
        *,
        output_dir: Path | str,
        formats: Iterable[str] = ("markdown", "html", "pdf"),
        localize_resources: bool = True,
    ) -> MPExportResult:
        html_text, final_url = self.fetch_html(url)
        return self.export_html_text(
            html_text,
            source_url=final_url,
            output_dir=output_dir,
            formats=formats,
            localize_resources=localize_resources,
        )


def allocate_export_directory(root: Path, source_url: str) -> Path:
    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    digest = hashlib.sha256(scrub_token_query(source_url).encode("utf-8")).hexdigest()[:10]
    base = f"{timestamp}-{digest}"
    candidate = root / base
    counter = 1
    while True:
        try:
            candidate.mkdir()
            return candidate
        except FileExistsError:
            candidate = root / f"{base}-{counter}"
            counter += 1


# Public aliases keep the service convenient to embed outside the FastAPI
# router while retaining the explicit MP names used by the application.
ArticleExporter = MPArticleExporter
ExportResult = MPExportResult
