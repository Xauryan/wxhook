from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app import mp_metrics, mp_metrics_router, mp_tools_router
from backend.app.mp_metrics import (
    MAX_JSON_BYTES,
    MAX_PUBLIC_DETAIL_CHARS,
    ArticleAuth,
    ArticleContext,
    WeChatArticleUrlError,
    WeChatArticleMetricsClient,
    WeChatMetricsAuthError,
    WeChatMetricsUpstreamError,
    collect_article_metrics,
    extract_article_context,
    parse_appmsgext_payload,
    parse_comment_payload,
    parse_count,
)
from backend.app.mp_metrics_router import router
from backend.app.mp_tokens import MPTokenStore


ARTICLE_URL = (
    "https://mp.weixin.qq.com/s?__biz=MzA1&mid=2247484000&idx=2"
    "&sn=abc123&key=secret&uin=9988&pass_ticket=ticket"
)
ARTICLE_HTML = """
<!doctype html>
<html><head><meta property="og:title" content="备用标题"></head><body>
<script>
var biz = "" || "MzA1";
var mid = "2247484000";
var idx = "2";
var sn = "abc123";
var comment_id = "987654321";
var appmsg_token = "page-token";
var msg_title = "测试\\u6587章";
var nickname = "数据号";
var ct = "1710000000";
var appmsg_like_type = "2";
</script>
</body></html>
"""


def test_extract_article_context_redacts_tokens_and_decodes_script_values() -> None:
    context = extract_article_context(ARTICLE_URL, ARTICLE_HTML)

    assert context.biz == "MzA1"
    assert context.mid == "2247484000"
    assert context.idx == "2"
    assert context.comment_id == "987654321"
    assert context.appmsg_token == "page-token"
    assert context.title == "测试文章"
    assert context.author == "数据号"
    assert context.publish_time == 1_710_000_000
    assert context.url is not None
    assert "key=" not in context.url
    assert "uin=" not in context.url
    assert "pass_ticket=" not in context.url


def test_parse_metrics_uses_exact_read_wow_and_legacy_like_semantics() -> None:
    result = parse_appmsgext_payload(
        {
            "base_resp": {"ret": 0, "errmsg": "ok"},
            "appmsgstat": {
                "read_num": 100_000,
                "real_read_num": 128_764,
                "like_num": 431,
                "old_like_num": 982,
                "comment_count": 27,
            },
        }
    )

    assert result.read_count == 128_764
    assert result.display_read_count == 100_000
    assert result.wow_count == 431
    assert result.like_count == 982
    assert result.comment_count == 27
    assert result.basis["wow_count"] == "like_num"
    assert result.basis["like_count"] == "old_like_num"


def test_zero_exact_wow_falls_back_to_like_num() -> None:
    result = parse_appmsgext_payload(
        {
            "appmsgstat": {
                "read_num": 12,
                "real_like_num": 0,
                "like_num": 5,
                "old_like_num": 3,
            }
        }
    )

    assert result.wow_count == 5
    assert result.like_count == 3


def test_auth_can_be_recovered_from_authenticated_article_url() -> None:
    auth = ArticleAuth.from_url(ARTICLE_URL)

    assert auth is not None
    assert auth.key == "secret"
    assert auth.uin == "9988"
    assert auth.pass_ticket == "ticket"


def test_url_tokens_fill_only_missing_stored_fields() -> None:
    auth = ArticleAuth.from_url(
        "https://mp.weixin.qq.com/s?__biz=A&mid=1&idx=1"
        "&key=url-key&uin=&pass-ticket=url-ticket"
        "&app-msg-token=url-appmsg&wx-token=url-wx",
        {"uin": "stored-uin", "pass_ticket": "stored-ticket"},
    )

    assert auth is not None
    assert auth.key == "url-key"
    assert auth.uin == "stored-uin"
    assert auth.pass_ticket == "stored-ticket"
    assert auth.appmsg_token == "url-appmsg"
    assert auth.wxtoken == "url-wx"


def test_parse_jsonp_and_ui_count_fallback() -> None:
    payload = 'callback({"appmsgstat":{"read_num":"10万+","like_num":"1.2万","old_like_num":"321"}});'
    result = parse_appmsgext_payload(payload)

    assert result.read_count == 100_000
    assert result.wow_count == 12_000
    assert result.like_count == 321
    assert parse_count("2.5w+") == 25_000
    assert parse_count("9007199254740993") == 9_007_199_254_740_993
    assert parse_count("9" * 129) is None


@pytest.mark.parametrize("flag", [True, "true"])
def test_comment_boolean_fields_accept_json_and_text_true(flag: object) -> None:
    result = parse_comment_payload(
        {
            "elected_comment_total_cnt": 1,
            "elected_comment": [{"content": "ok", "is_top": flag}],
            "enabled": flag,
        }
    )

    assert result.enabled is True
    assert result.comments[0].is_top is True


def test_comment_boolean_fields_reject_fractional_values() -> None:
    result = parse_comment_payload(
        {
            "elected_comment": [{"content": "ok", "is_top": "1.5"}],
            "enabled": "0.5",
        }
    )

    assert result.enabled is None
    assert result.comments[0].is_top is False


def test_parse_comments_returns_total_and_deduplicated_rows() -> None:
    comment = {
        "content_id": "c1",
        "nick_name": "读者",
        "content": "信息密度很高",
        "create_time": 1710000100,
        "like_num": 8,
        "is_top": 1,
        "reply": {"reply_list": [{"content": "谢谢"}]},
    }
    result = parse_comment_payload(
        {
            "base_resp": {"ret": 0},
            "elected_comment_total_cnt": 12,
            "elected_comment": [comment],
            "friend_comment": [comment],
            "enabled": 1,
        }
    )

    assert result.total_count == 12
    assert result.basis == "elected_comment_total_cnt"
    assert len(result.comments) == 1
    assert result.comments[0].like_count == 8
    assert result.comments[0].is_top is True
    assert result.comments[0].reply_count == 1


def test_negative_base_response_code_is_an_auth_error() -> None:
    with pytest.raises(WeChatMetricsAuthError):
        parse_appmsgext_payload(
            {"base_resp": {"ret": -3, "errmsg": "session timeout"}}
        )


def test_live_client_loads_context_then_metrics_and_comment_pages() -> None:
    requests: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append((request.method, request.url.path))
        if request.method == "GET" and request.url.path == "/s":
            assert request.url.params["key"] == "fresh-key"
            assert request.url.params["pass_ticket"] == "fresh-ticket"
            return httpx.Response(200, text=ARTICLE_HTML)
        if request.method == "POST" and request.url.path == "/mp/getappmsgext":
            form = parse_qs(request.content.decode())
            assert form["__biz"] == ["MzA1"]
            assert form["mid"] == ["2247484000"]
            assert form["appmsg_token"] == ["page-token"]
            return httpx.Response(
                200,
                json={
                    "base_resp": {"ret": 0},
                    "appmsgstat": {
                        "read_num": 300,
                        "real_read_num": 345,
                        "like_num": 7,
                        "old_like_num": 9,
                    },
                },
            )
        if request.method == "GET" and request.url.path == "/mp/appmsg_comment":
            offset = int(request.url.params["offset"])
            content_id = "c1" if offset == 0 else "c2"
            return httpx.Response(
                200,
                json={
                    "base_resp": {"ret": 0},
                    "elected_comment_total_cnt": 2,
                    "elected_comment": [
                        {
                            "content_id": content_id,
                            "nick_name": "reader",
                            "content": f"comment-{content_id}",
                        }
                    ],
                    "enabled": 1,
                },
            )
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    http_client = httpx.Client(transport=httpx.MockTransport(handler))
    metric_client = WeChatArticleMetricsClient(http_client)
    result = collect_article_metrics(
        url=ARTICLE_URL,
        auth=ArticleAuth("fresh-key", "9988", "fresh-ticket"),
        fetch_missing=True,
        include_comments=True,
        comment_limit=1,
        max_comment_pages=3,
        client=metric_client,
    )

    assert result.read_count == 345
    assert result.wow_count == 7
    assert result.like_count == 9
    assert result.comment_count == 2
    assert [item.content_id for item in result.comments] == ["c1", "c2"]
    assert result.sources == ["live_getappmsgext", "live_comments"]
    assert requests == [
        ("GET", "/s"),
        ("POST", "/mp/getappmsgext"),
        ("GET", "/mp/appmsg_comment"),
        ("GET", "/mp/appmsg_comment"),
    ]
    http_client.close()


def test_live_client_resolves_copied_short_article_url_from_html() -> None:
    short_url = "https://mp.weixin.qq.com/s/copied-article-slug"
    requests: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append((request.method, request.url.path))
        if request.method == "GET" and request.url.path == "/s/copied-article-slug":
            assert request.url.params["key"] == "fresh-key"
            assert request.url.params["pass_ticket"] == "fresh-ticket"
            return httpx.Response(200, text=ARTICLE_HTML)
        if request.method == "POST" and request.url.path == "/mp/getappmsgext":
            form = parse_qs(request.content.decode())
            assert form["__biz"] == ["MzA1"]
            assert form["mid"] == ["2247484000"]
            assert form["idx"] == ["2"]
            return httpx.Response(
                200,
                json={
                    "base_resp": {"ret": 0},
                    "appmsgstat": {
                        "real_read_num": 456,
                        "like_num": 8,
                        "old_like_num": 5,
                    },
                },
            )
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    http_client = httpx.Client(transport=httpx.MockTransport(handler))
    result = collect_article_metrics(
        url=short_url,
        auth=ArticleAuth("fresh-key", "9988", "fresh-ticket"),
        fetch_missing=True,
        include_comments=False,
        client=WeChatArticleMetricsClient(http_client),
    )

    assert result.read_count == 456
    assert result.context.biz == "MzA1"
    assert result.context.mid == "2247484000"
    assert result.context.idx == "2"
    assert requests == [
        ("GET", "/s/copied-article-slug"),
        ("POST", "/mp/getappmsgext"),
    ]
    http_client.close()


def test_short_article_url_without_resolved_identity_stops_after_html() -> None:
    short_url = "https://mp.weixin.qq.com/s/copied-article-slug"
    requests: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append((request.method, request.url.path))
        return httpx.Response(200, text="<html><body>session shell</body></html>")

    http_client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(WeChatArticleUrlError, match="missing biz, mid, idx"):
        collect_article_metrics(
            url=short_url,
            auth=ArticleAuth("fresh-key", "9988", "fresh-ticket"),
            fetch_missing=True,
            include_comments=False,
            client=WeChatArticleMetricsClient(http_client),
        )

    assert requests == [("GET", "/s/copied-article-slug")]
    http_client.close()


def test_short_article_session_shell_uses_native_identity_hint() -> None:
    short_url = "https://mp.weixin.qq.com/s/copied-article-slug"
    requests: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append((request.method, request.url.path))
        if request.method == "GET":
            return httpx.Response(200, text="<html><body>session shell</body></html>")
        form = parse_qs(request.content.decode())
        assert form["__biz"] == ["NativeBiz"]
        assert form["mid"] == ["2247484999"]
        assert form["idx"] == ["3"]
        assert form["sn"] == ["native-sn"]
        assert form["scene"] == ["126"]
        return httpx.Response(
            200,
            json={
                "base_resp": {"ret": 0},
                "appmsgstat": {"real_read_num": 654, "like_num": 6},
            },
        )

    http_client = httpx.Client(transport=httpx.MockTransport(handler))
    result = collect_article_metrics(
        url=short_url,
        auth=ArticleAuth(
            "fresh-key",
            "9988",
            "fresh-ticket",
            appmsg_token="fresh-appmsg-token",
        ),
        context_hint={
            "biz": "NativeBiz",
            "mid": "2247484999",
            "idx": "3",
            "sn": "native-sn",
            "scene": "126",
        },
        fetch_missing=True,
        include_comments=False,
        client=WeChatArticleMetricsClient(http_client),
    )

    assert result.read_count == 654
    assert result.context.url == short_url
    assert result.sources == ["live_getappmsgext"]
    assert requests == [
        ("GET", "/s/copied-article-slug"),
        ("POST", "/mp/getappmsgext"),
    ]
    http_client.close()


def test_article_html_identity_takes_priority_over_native_hint() -> None:
    result = collect_article_metrics(
        url="https://mp.weixin.qq.com/s/copied-article-slug",
        html=ARTICLE_HTML,
        appmsgext={"appmsgstat": {"read_num": 1}},
        context_hint={"biz": "WrongBiz", "mid": "1", "idx": "1"},
    )

    assert result.context.biz == "MzA1"
    assert result.context.mid == "2247484000"
    assert result.context.idx == "2"


def test_partial_conflicting_html_identity_does_not_mix_native_hint() -> None:
    partial_html = '<script>var biz = "WrongBiz";</script>'
    http_client = httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(500))
    )
    try:
        with pytest.raises(WeChatArticleUrlError, match="missing mid, idx"):
            collect_article_metrics(
                url="https://mp.weixin.qq.com/s/copied-article-slug",
                html=partial_html,
                context_hint={"biz": "NativeBiz", "mid": "2", "idx": "1"},
                fetch_missing=True,
                auth=ArticleAuth("key", "uin", "ticket"),
                include_comments=False,
                client=WeChatArticleMetricsClient(http_client),
            )
    finally:
        http_client.close()


def test_live_client_follows_bounded_same_host_article_redirect() -> None:
    requests: list[str] = []
    redirected_url = (
        "https://mp.weixin.qq.com/s?__biz=MzA1&mid=2247484000&idx=2"
        "&sn=abc123"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        if len(requests) == 1:
            return httpx.Response(302, headers={"Location": redirected_url})
        return httpx.Response(200, text=ARTICLE_HTML)

    http_client = httpx.Client(transport=httpx.MockTransport(handler))
    metric_client = WeChatArticleMetricsClient(http_client)

    result = metric_client.fetch_html(
        ARTICLE_URL,
        ArticleAuth("fresh-key", "9988", "fresh-ticket"),
    )

    assert result == ARTICLE_HTML
    assert len(requests) == 2
    assert "key=fresh-key" in requests[0]
    assert "__biz=MzA1" in requests[1]
    http_client.close()


@pytest.mark.parametrize(
    "location",
    [
        "http://mp.weixin.qq.com/s?__biz=A&mid=1&idx=1",
        "https://example.test/s?__biz=A&mid=1&idx=1",
        "https://mp.weixin.qq.com/mp/getappmsgext",
    ],
)
def test_live_client_rejects_unsafe_article_redirect(location: str) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"Location": location})

    http_client = httpx.Client(transport=httpx.MockTransport(handler))
    metric_client = WeChatArticleMetricsClient(http_client)

    with pytest.raises(WeChatMetricsUpstreamError, match="redirect left"):
        metric_client.fetch_html(
            ARTICLE_URL,
            ArticleAuth("fresh-key", "9988", "fresh-ticket"),
        )

    http_client.close()


@pytest.mark.parametrize(
    "url",
    [
        "https://mp.weixin.qq.com/not-an-article",
        "https://mp.weixin.qq.com/mp/getappmsgext",
        "https://mp.weixin.qq.com/s/",
        "https://mp.weixin.qq.com/s/foo/bar",
        "https://mp.weixin.qq.com/s/../mp/getappmsgext",
        "https://mp.weixin.qq.com/s//foo",
    ],
)
def test_live_fetch_rejects_non_article_paths(url: str) -> None:
    with WeChatArticleMetricsClient() as metric_client:
        with pytest.raises(WeChatArticleUrlError, match="/s article URL"):
            metric_client.fetch_html(url, ArticleAuth("key", "uin", "ticket"))


def test_live_comments_create_a_client_when_metrics_were_intercepted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = SimpleNamespace(created=0, closed=0, comments=0)

    class FakeClient:
        def __init__(self) -> None:
            state.created += 1

        def fetch_comments(self, *_: object, **__: object):
            state.comments += 1
            return parse_comment_payload(
                {
                    "elected_comment_total_cnt": 1,
                    "elected_comment": [{"content_id": "c1", "content": "live"}],
                    "enabled": True,
                }
            )

        def close(self) -> None:
            state.closed += 1

    monkeypatch.setattr(mp_metrics, "WeChatArticleMetricsClient", FakeClient)
    result = collect_article_metrics(
        url=ARTICLE_URL,
        html=ARTICLE_HTML,
        appmsgext={"appmsgstat": {"read_num": 10, "like_num": 2}},
        auth=ArticleAuth("fresh-key", "9988", "fresh-ticket"),
        fetch_missing=True,
        include_comments=True,
    )

    assert [comment.content for comment in result.comments] == ["live"]
    assert result.sources == ["intercepted_getappmsgext", "live_comments"]
    assert (state.created, state.comments, state.closed) == (1, 1, 1)


def test_live_comment_warning_is_redacted_and_bounded() -> None:
    class FailingClient:
        def fetch_comments(self, *_: object, **__: object) -> None:
            raise WeChatMetricsUpstreamError(
                "failed key%3Durl-secret%26pass_ticket%3Dticket-secret "
                + "x" * 2_000
            )

    result = collect_article_metrics(
        url=ARTICLE_URL,
        html=ARTICLE_HTML,
        appmsgext={"appmsgstat": {"read_num": 10, "like_num": 2}},
        auth=ArticleAuth("fresh-key", "9988", "fresh-ticket"),
        fetch_missing=True,
        include_comments=True,
        client=FailingClient(),  # type: ignore[arg-type]
    )

    assert len(result.warnings) == 1
    assert len(result.warnings[0]) <= MAX_PUBLIC_DETAIL_CHARS
    assert "url-secret" not in result.warnings[0]
    assert "ticket-secret" not in result.warnings[0]
    assert "<redacted>" in result.warnings[0]


def test_live_response_stream_stops_after_json_limit() -> None:
    class CountingStream(httpx.SyncByteStream):
        def __init__(self) -> None:
            self.yielded = 0
            self.closed = False

        def __iter__(self):
            for _ in range(32):
                self.yielded += 1
                yield b"x" * (256 * 1024)

        def close(self) -> None:
            self.closed = True

    stream = CountingStream()

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=stream)

    http_client = httpx.Client(transport=httpx.MockTransport(handler))
    metric_client = WeChatArticleMetricsClient(http_client)
    with pytest.raises(WeChatMetricsUpstreamError, match="too large"):
        metric_client.fetch_appmsgext(
            ArticleContext(biz="A", mid="1", idx="1"),
            ArticleAuth("key", "uin", "ticket"),
            ARTICLE_URL,
        )

    assert stream.yielded * 256 * 1024 <= MAX_JSON_BYTES + 256 * 1024
    assert stream.yielded < 32
    assert stream.closed is True
    http_client.close()


@pytest.mark.parametrize(
    "url",
    [
        "https://user@mp.weixin.qq.com/s?__biz=A&mid=1&idx=1",
        "https://mp.weixin.qq.com:444/s?__biz=A&mid=1&idx=1",
    ],
)
def test_live_fetch_rejects_noncanonical_article_authority(url: str) -> None:
    with WeChatArticleMetricsClient() as metric_client:
        with pytest.raises(WeChatArticleUrlError):
            metric_client.fetch_html(url, ArticleAuth("key", "uin", "ticket"))


def test_router_accepts_intercepted_responses_without_live_tokens() -> None:
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        response = client.post(
            "/api/mp/articles/metrics",
            json={
                "url": ARTICLE_URL,
                "html": ARTICLE_HTML,
                "appmsgext": {
                    "appmsgstat": {
                        "read_num": 88,
                        "like_num": 6,
                        "old_like_num": 11,
                    }
                },
                "comments": {
                    "elected_comment_total_cnt": 1,
                    "elected_comment": [
                        {
                            "content_id": "r1",
                            "nick_name": "A",
                            "content": "收到",
                        }
                    ],
                },
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["read_count"] == 88
    assert payload["wow_count"] == 6
    assert payload["like_count"] == 11
    assert payload["comment_count"] == 1
    assert payload["comments"][0]["content"] == "收到"
    assert "key=" not in payload["url"]


def test_metrics_router_passes_native_short_link_context_hint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    short_url = "https://mp.weixin.qq.com/s/copied-article-slug"
    hint = {"biz": "NativeBiz", "mid": "2247484999", "idx": "3"}
    seen: dict[str, object] = {}

    class ContextStore:
        def resolve(self, url: str) -> dict[str, str] | None:
            assert url == short_url
            return hint

    def fake_collect(**kwargs: object) -> SimpleNamespace:
        seen.update(kwargs)
        return SimpleNamespace(
            as_dict=lambda: {
                "url": short_url,
                "read_count": 10,
                "comments": [],
            }
        )

    monkeypatch.setattr(mp_tools_router, "article_context_store", ContextStore())
    monkeypatch.setattr(mp_metrics_router, "collect_article_metrics", fake_collect)
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        response = client.post(
            "/api/mp/articles/metrics",
            json={"url": short_url, "fetch_missing": False},
        )

    assert response.status_code == 200
    assert seen["context_hint"] == hint


def test_metrics_router_passes_shared_appmsg_token_to_live_request(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    article_url = (
        "https://mp.weixin.qq.com/s?__biz=MzA1&mid=2247484000&idx=2&sn=abc123"
    )
    appmsg_token = "stored-appmsg-token"
    store = MPTokenStore(tmp_path / "tokens.json")
    store.update(
        {
            "key": "stored-key",
            "uin": "9988",
            "pass_ticket": "stored-ticket",
            "appmsg_token": appmsg_token,
        },
        source="test",
    )
    monkeypatch.setattr(mp_tools_router, "token_store", store)

    article_html = ARTICLE_HTML.replace('var appmsg_token = "page-token";', "")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/s":
            assert request.url.params["appmsg_token"] == appmsg_token
            return httpx.Response(200, text=article_html)
        if request.method == "POST" and request.url.path == "/mp/getappmsgext":
            form = parse_qs(request.content.decode())
            assert request.url.params["appmsg_token"] == appmsg_token
            assert form["appmsg_token"] == [appmsg_token]
            return httpx.Response(
                200,
                json={
                    "base_resp": {"ret": 0},
                    "appmsgstat": {"real_read_num": 321, "like_num": 4},
                },
            )
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    original_client = WeChatArticleMetricsClient

    class CapturingClient(original_client):
        def fetch_appmsgext(
            self,
            context: ArticleContext,
            auth: ArticleAuth,
            referer: str,
        ):
            assert auth.appmsg_token == appmsg_token
            return super().fetch_appmsgext(context, auth, referer)

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        monkeypatch.setattr(
            mp_metrics,
            "WeChatArticleMetricsClient",
            lambda: CapturingClient(http_client),
        )
        app = FastAPI()
        app.include_router(router)
        with TestClient(app) as client:
            response = client.post(
                "/api/mp/articles/metrics",
                json={"url": article_url, "include_comments": False},
            )

    assert response.status_code == 200
    assert response.json()["read_count"] == 321


def test_metrics_router_enforces_raw_request_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mp_metrics_router, "MAX_METRICS_BODY_BYTES", 64)
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        response = client.post(
            "/api/mp/articles/metrics",
            content=b'{"html":"' + b"x" * 128 + b'"}',
            headers={"content-type": "application/json"},
        )

    assert response.status_code == 413
