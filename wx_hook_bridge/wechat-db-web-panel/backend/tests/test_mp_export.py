from __future__ import annotations

import json
import zipfile
from pathlib import Path

import httpx
import pytest

from backend.app.mp_export import MPArticleExporter, MPExportError, scrub_token_query
from backend.app.mp_tokens import MPTokenCaptureService, MPTokenStore


def test_scrub_token_query_removes_dynamic_credentials() -> None:
    value = scrub_token_query(
        "https://mp.weixin.qq.com/s?__biz=x&key=secret&uin=7"
        "&pass_ticket=ticket&appmsg_token=metric#rd"
    )
    assert "secret" not in value
    assert "ticket" not in value
    assert "metric" not in value
    assert "__biz=x" in value


def test_export_localizes_images_and_videos_and_uses_auth_headers(tmp_path: Path) -> None:
    store = MPTokenStore()
    MPTokenCaptureService(store).ingest(
        {
            "url": "https://mp.weixin.qq.com/s?key=new-key&uin=99",
            "headers": {"Cookie": "pass_ticket=new-ticket"},
        },
        source="test",
    )
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/s":
            assert request.url.params.get("key") == "new-key"
            assert request.url.params.get("uin") == "99"
            assert request.url.params.get("pass_ticket") == "new-ticket"
            body = """
            <html><head>
              <meta property="og:title" content="Lossless article">
              <meta name="author" content="Account">
            </head><body><div id="js_content">
              <p>Intro <strong>text</strong></p>
              <img data-src="https://mmbiz.qpic.cn/a.png?x=1" alt="hero">
              <video data-src="https://mmbiz.qpic.cn/v.mp4" poster="https://mmbiz.qpic.cn/p.jpg"></video>
            </div></body></html>
            """
            return httpx.Response(
                200,
                headers={"content-type": "text/html; charset=utf-8"},
                text=body,
                request=request,
            )
        if request.url.path.endswith("a.png"):
            return httpx.Response(
                200,
                headers={"content-type": "image/png"},
                content=b"PNG-BYTES",
                request=request,
            )
        if request.url.path.endswith("v.mp4"):
            return httpx.Response(
                200,
                headers={"content-type": "video/mp4"},
                content=b"VIDEO-BYTES",
                request=request,
            )
        if request.url.path.endswith("p.jpg"):
            return httpx.Response(
                200,
                headers={"content-type": "image/jpeg"},
                content=b"POSTER-BYTES",
                request=request,
            )
        return httpx.Response(404, request=request)

    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
    with MPArticleExporter(
        store,
        client=client,
        pdf_renderer=lambda _html, output: (output.write_bytes(b"%PDF-1.4 test"), "test-pdf")[1],
    ) as exporter:
        result = exporter.export(
            "https://mp.weixin.qq.com/s?__biz=demo&key=stale&uin=1&pass_ticket=old",
            output_dir=tmp_path,
            formats=("md", "html", "pdf"),
        )

    assert result.pdf_backend == "test-pdf"
    assert set(result.files) == {"markdown", "html", "pdf", "manifest"}
    markdown = (tmp_path / result.files["markdown"]).read_text(encoding="utf-8")
    standalone = (tmp_path / result.files["html"]).read_text(encoding="utf-8")
    assert "assets/" in markdown
    assert "VIDEO-BYTES" not in markdown
    assert "new-key" not in markdown
    assert "new-ticket" not in standalone
    assert (tmp_path / result.files["pdf"]).read_bytes().startswith(b"%PDF")
    assert (tmp_path / "assets").is_dir()
    assert len(list((tmp_path / "assets").iterdir())) == 3
    assert all(request.headers.get("user-agent") for request in requests)
    asset_requests = [request for request in requests if request.url.path != "/s"]
    assert all(request.headers.get("referer", "").startswith("https://mp.weixin.qq.com/s") for request in asset_requests)

    with zipfile.ZipFile(tmp_path / result.archive) as archive:
        names = set(archive.namelist())
        assert result.files["markdown"] in names
        assert "assets/v-" in next(name for name in names if name.startswith("assets/v-"))
        manifest = json.loads(archive.read("manifest.json"))
    assert manifest["source_url"].find("key=") == -1


def test_failed_resource_is_kept_as_remote_reference(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/article":
            body = '<html><body><div id="js_content"><img src="https://mmbiz.qpic.cn/missing.png"></div></body></html>'
            return httpx.Response(200, headers={"content-type": "text/html"}, text=body, request=request)
        return httpx.Response(403, request=request)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with MPArticleExporter(
        client=client,
        pdf_renderer=lambda _html, output: (output.write_bytes(b"%PDF"), "test")[1],
    ) as exporter:
        result = exporter.export(
            "https://mp.weixin.qq.com/article",
            output_dir=tmp_path,
            formats=("markdown",),
        )
    markdown = (tmp_path / result.files["markdown"]).read_text(encoding="utf-8")
    assert "https://mmbiz.qpic.cn/missing.png" in markdown
    assert result.resources[0]["status"] == "failed"
    assert result.warnings


def test_article_redirect_to_literal_ip_is_rejected_before_second_request() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            302,
            headers={"location": "https://127.0.0.1/private"},
            request=request,
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with MPArticleExporter(client=client) as exporter:
        with pytest.raises(MPExportError, match="not allowed"):
            exporter.fetch_html("https://mp.weixin.qq.com/s?__biz=x")

    assert len(requests) == 1


def test_resource_redirect_to_private_host_is_not_followed(tmp_path: Path) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.host == "mp.weixin.qq.com":
            body = (
                '<html><body><div id="js_content">'
                '<img src="https://mmbiz.qpic.cn/redirect.png">'
                "</div></body></html>"
            )
            return httpx.Response(
                200,
                headers={"content-type": "text/html"},
                text=body,
                request=request,
            )
        return httpx.Response(
            302,
            headers={"location": "https://localhost/private.png"},
            request=request,
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with MPArticleExporter(client=client) as exporter:
        result = exporter.export(
            "https://mp.weixin.qq.com/s?__biz=x",
            output_dir=tmp_path,
            formats=("markdown",),
        )

    assert [request.url.host for request in requests] == [
        "mp.weixin.qq.com",
        "mmbiz.qpic.cn",
    ]
    assert result.resources[0]["status"] == "failed"


def test_export_removes_active_html_and_redacts_nonlocalized_poster(
    tmp_path: Path,
) -> None:
    html_text = """
    <html><head><style>body{background:red}</style><link rel="stylesheet" href="https://evil.test/x.css"></head>
    <body><div id="js_content">
      <iframe src="https://mp.weixin.qq.com/mp/videoplayer" srcdoc="<script>alert(1)</script>"></iframe>
      <svg><a xlink:href="javascript:alert(2)">x</a></svg>
      <video poster="https://mmbiz.qpic.cn/p.jpg?key=poster-secret&pass_ticket=poster-ticket"></video>
    </div></body></html>
    """
    with MPArticleExporter() as exporter:
        result = exporter.export_html_text(
            html_text,
            source_url="https://mp.weixin.qq.com/s?__biz=x",
            output_dir=tmp_path,
            formats=("html", "markdown"),
            localize_resources=False,
        )

    output = "\n".join(
        (tmp_path / result.files[name]).read_text(encoding="utf-8")
        for name in ("html", "markdown")
    )
    assert "srcdoc" not in output
    assert "xlink:href" not in output
    assert "javascript:" not in output
    assert "evil.test" not in output
    assert "poster-secret" not in output
    assert "poster-ticket" not in output


def test_signed_resource_request_keeps_signature_but_export_does_not(
    tmp_path: Path,
) -> None:
    seen_resource_url = ""

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal seen_resource_url
        if request.url.host == "mp.weixin.qq.com":
            body = (
                '<html><body><div id="js_content">'
                '<img src="https://mmbiz.qpic.cn/a.png?key=image-secret&amp;pass_ticket=image-ticket&amp;foo=1">'
                "</div></body></html>"
            )
            return httpx.Response(
                200,
                headers={"content-type": "text/html"},
                text=body,
                request=request,
            )
        seen_resource_url = str(request.url)
        return httpx.Response(
            200,
            headers={"content-type": "image/png"},
            content=b"image",
            request=request,
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with MPArticleExporter(client=client) as exporter:
        result = exporter.export(
            "https://mp.weixin.qq.com/s?__biz=x",
            output_dir=tmp_path,
            formats=("html", "markdown"),
        )

    assert "image-secret" in seen_resource_url
    assert "image-ticket" in seen_resource_url
    output = "\n".join(
        (tmp_path / result.files[name]).read_text(encoding="utf-8")
        for name in ("html", "markdown")
    )
    assert "image-secret" not in output
    assert "image-ticket" not in output


def test_pdf_renderer_receives_offline_html(tmp_path: Path) -> None:
    rendered_html = ""

    def render(html_path: Path, output: Path) -> str:
        nonlocal rendered_html
        rendered_html = html_path.read_text(encoding="utf-8")
        output.write_bytes(b"%PDF")
        return "test"

    html_text = """
    <html><body><div id="js_content">
      <img src="https://mmbiz.qpic.cn/remote.png?key=secret">
      <a href="https://example.test/story">story</a>
    </div></body></html>
    """
    with MPArticleExporter(pdf_renderer=render) as exporter:
        exporter.export_html_text(
            html_text,
            source_url="https://mp.weixin.qq.com/s?__biz=x",
            output_dir=tmp_path,
            formats=("pdf",),
            localize_resources=False,
        )

    assert "mmbiz.qpic.cn" not in rendered_html
    assert 'href="https://example.test/story"' not in rendered_html
    assert not list(tmp_path.glob(".mp-render-*.html"))


def test_article_response_is_bounded_while_streaming() -> None:
    class OversizedStream(httpx.SyncByteStream):
        def __init__(self) -> None:
            self.yielded = 0

        def __iter__(self):
            for _ in range(5):
                self.yielded += 1
                yield b"12345"

    stream = OversizedStream()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            stream=stream,
            request=request,
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with MPArticleExporter(client=client, max_article_bytes=10) as exporter:
        with pytest.raises(MPExportError, match="size limit"):
            exporter.fetch_html("https://mp.weixin.qq.com/s?__biz=x")

    assert stream.yielded == 3


def test_auth_cookie_is_only_attached_to_exact_article_host() -> None:
    store = MPTokenStore()
    store.update(
        {"key": "cookie-key", "uin": "9", "pass_ticket": "cookie-ticket"},
        source="test",
    )
    with MPArticleExporter(store) as exporter:
        article_headers = exporter._headers(
            "https://mp.weixin.qq.com/s",
            kind="article",
        )
        sibling_headers = exporter._headers(
            "https://evil.weixin.qq.com/resource",
            kind="image",
        )

    assert "Cookie" in article_headers
    assert "Cookie" not in sibling_headers
