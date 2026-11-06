import json
import os
from pathlib import Path
import unittest
import urllib.error
import urllib.parse
from unittest import mock

from python.message import finder_payload


class FinderPayloadSearchTests(unittest.TestCase):
    def test_best_payload_prefers_valid_live_result(self) -> None:
        invalid_video = {
            "objectId": "14929828060974024712",
            "objectNonceId": "video-nonce",
            "feedType": "4",
            "nickname": "Video Host",
            "avatar": "https://example.test/video-avatar.jpg",
            "mediaCount": "1",
            "sourceCommentScene": "20",
            "media": {
                "url": "https://example.test/video.mp4",
                "thumbUrl": "https://example.test/video-thumb.jpg",
                "coverUrl": "https://example.test/video-cover.jpg",
                "width": "1920",
                "height": "1080",
                "videoPlayDuration": "30",
            },
        }
        valid_live = {
            "kind": "live",
            "objectId": "export/UzFfBgAAxOiCcF9WCSPmk8zT4DCKGlZeihK3JE2WkedfP3X5tg",
            "objectNonceId": "7717552142406512493",
            "liveId": "2078975264166813879",
        }

        best = finder_payload._best_payload([invalid_video, valid_live])

        self.assertIs(best, valid_live)

    def test_real_live_card_nonce_and_export_username_are_normalized(self) -> None:
        export_id = "export/UzFfBgAAxOiCcF9WCSPmk8zT4DCKGlZeihK3JE2WkedfP3X5tg"
        export_username = "export/UzFfAgtgekIEAQAAAAAABd8DbzTkLwAAAAstQy6ubaLX4KHWvLEZgBPEwvcXVh40eXJTzNvxPtruaLxEZWJ9YjaUH5qFgg"
        response = {
            "data": [
                {
                    "docID": "finderobjv0KLLtiBtlOJYGzA8XYRkjEMYGxUTtYUUWdIMGs41Na78=",
                    "exportId": export_id,
                    "nonceId": "7717552142406512493",
                    "imageUrl": "https://example.test/live-cover.jpg",
                    "source": {
                        "title": "Live Host",
                        "iconUrl": "https://example.test/live-avatar.jpg",
                    },
                    "jumpInfo": {
                        "extInfo": json.dumps({"exportId": export_id}),
                    },
                    "report_extinfo_str": urllib.parse.quote(
                        json.dumps(
                            {
                                "live_id": "2078975264166813879",
                                "export_username": export_username,
                            }
                        )
                    ),
                }
            ]
        }

        live_results = finder_payload.finder_live_results_from_text(json.dumps(response))

        self.assertEqual(len(live_results), 1)
        live = live_results[0]
        self.assertEqual(live["objectId"], export_id)
        self.assertEqual(live["encryptedObjectId"], export_id)
        self.assertEqual(live["objectNonceId"], "7717552142406512493")
        self.assertEqual(live["liveId"], "2078975264166813879")
        self.assertEqual(live["username"], export_username)
        self.assertIn("/web/pages/live?", live["liveUrl"])
        self.assertIn("eid=export%2F", live["liveUrl"])
        self.assertNotIn("oid=", live["liveUrl"])
        self.assertNotIn("nid=", live["liveUrl"])

    def test_numeric_live_route_uses_uint64_base64url(self) -> None:
        payload = {
            "kind": "live",
            "objectId": "14990114941639658087",
            "objectNonceId": "9868890007127663708_0_142_0_0",
            "liveId": "2042946423631635975",
            "entranceId": "88",
        }

        finder_payload.normalize_payload(payload)

        self.assertIn("oid=0AeWHk61Cmc", payload["liveUrl"])
        self.assertIn("nid=iPVXLII_JFw", payload["liveUrl"])
        parsed = finder_payload.parse_finder_url_params(payload["liveUrl"])
        self.assertEqual(parsed["objectId"], "14990114941639658087")
        self.assertEqual(parsed["objectNonceId"], "9868890007127663708")

    def test_export_live_route_round_trips_as_encrypted_object_id(self) -> None:
        export_id = "export/UzFfBgAAxLOnJEt0Lz_mk8zT4DCKDRYV78Y4XAU1fPoimIxVsQ"
        url = (
            "https://channels.weixin.qq.com/web/pages/live?"
            + urllib.parse.urlencode({"eid": export_id, "entrance_id": "88"})
        )

        parsed = finder_payload.parse_finder_url_params(url)

        self.assertEqual(parsed["objectId"], export_id)
        self.assertEqual(parsed["encryptedObjectId"], export_id)

    def test_search_prefers_feed_id_and_result_username(self) -> None:
        response = {
            "data": [
                {
                    "docID": "finderacct_same_name",
                    "title": "Same Name",
                    "thumbUrl": "https://example.test/account-avatar.jpg",
                    "jumpInfo": {"userName": "fallback@finder"},
                },
                {
                    "docID": "finderobj_search_document",
                    "title": "Video title",
                    "videoUrl": "https://example.test/video.mp4",
                    "image": "https://example.test/cover.jpg",
                    "width": 1080,
                    "height": 1920,
                    "duration": 12,
                    "source": {
                        "title": "Same Name",
                        "iconUrl": "https://example.test/result-avatar.jpg",
                    },
                    "jumpInfo": {
                        "userName": "exact@finder",
                        "extInfo": json.dumps(
                            {
                                "feedId": "14929828060974024711",
                                "feedNonceId": "nonce-1",
                            }
                        ),
                    },
                },
            ]
        }

        result = finder_payload.resolve_finder_from_text(json.dumps(response))

        self.assertTrue(result["ok"])
        self.assertEqual(result["payload"]["objectId"], "14929828060974024711")
        self.assertEqual(result["payload"]["searchDocId"], "finderobj_search_document")
        self.assertEqual(result["payload"]["searchObjectId"], "finderobj_search_document")
        self.assertEqual(result["payload"]["username"], "exact@finder")

    def test_live_search_result_does_not_require_video_url(self) -> None:
        response = {
            "data": [
                {
                    "docID": "finderobj_live_document",
                    "title": "Live room",
                    "image": "https://example.test/live-cover.jpg",
                    "source": {
                        "title": "Live Host",
                        "iconUrl": "https://example.test/live-avatar.jpg",
                    },
                    "jumpInfo": {
                        "userName": "live@finder",
                        "extInfo": json.dumps(
                            {
                                "feedId": "14929828060974024712",
                                "feedNonceId": "live-nonce",
                                "contextId": "live-context",
                                "reportScene": "88",
                                "liveInfo": {"liveId": "9988776655"},
                            }
                        ),
                    },
                }
            ]
        }

        live_results = finder_payload.finder_live_results_from_text(json.dumps(response))

        self.assertEqual(len(live_results), 1)
        live = live_results[0]
        self.assertEqual(live["liveId"], "9988776655")
        self.assertEqual(live["objectId"], "14929828060974024712")
        self.assertEqual(live["searchDocId"], "finderobj_live_document")
        self.assertEqual(live["username"], "live@finder")
        self.assertEqual(live["contextId"], "live-context")
        self.assertIn("/web/pages/live?", live["liveUrl"])
        self.assertIn("context_id=live-context", live["liveUrl"])
        self.assertFalse(live["media"].get("url"))
        self.assertEqual(live["media"]["coverUrl"], "https://example.test/live-cover.jpg")

    def test_nested_live_search_response_survives_json_scan_prefix(self) -> None:
        fixture = Path(__file__).parent / "fixtures" / "finder_search_live_nested.json"
        text = fixture.read_text(encoding="utf-8")

        # Endpoint wrappers can prepend many small JSON log fragments. The
        # actual live card is nested below resp_json -> Json and must still be
        # discovered after those fragments.
        text = "".join(json.dumps({"noise": index}) for index in range(300)) + text

        live_results = finder_payload.finder_live_results_from_text(text)

        self.assertEqual(len(live_results), 1)
        live = live_results[0]
        self.assertEqual(live["objectId"], "export/UzFfBgAAxPeHeHEtbyLmk8zT4DCKOvXCoCUQ-Vam97VeA1wpqg")
        self.assertEqual(live["objectNonceId"], "4884801140923635608")
        self.assertEqual(live["liveId"], "2078975260375074184")
        self.assertEqual(live["nickname"], "央视频")
        self.assertTrue(live["username"].startswith("export/"))
        self.assertIn("eid=export%2F", live["liveUrl"])

    def test_live_search_endpoint_result_is_ready_to_enter(self) -> None:
        response = {
            "data": [
                {
                    "docID": "finderobj_live_document",
                    "title": "Live room",
                    "jumpInfo": {
                        "extInfo": json.dumps(
                            {
                                "feedId": "14929828060974024712",
                                "feedNonceId": "live-nonce",
                                "liveInfo": {"liveId": "9988776655"},
                            }
                        )
                    },
                }
            ]
        }
        http_response = mock.MagicMock()
        http_response.read.return_value = json.dumps(response).encode("utf-8")
        http_response.geturl.return_value = "http://127.0.0.1:19088/api/searchFinder"
        http_response.status = 200
        http_response.__enter__.return_value = http_response

        with mock.patch("urllib.request.urlopen", return_value=http_response):
            result = finder_payload._resolve_with_endpoint(
                "http://127.0.0.1:19088/api/searchFinder",
                {"query": "Live room"},
                15.0,
            )

        self.assertTrue(result["ok"])
        self.assertNotIn("xml", result)
        ready = finder_payload.send_ready_result(result)
        self.assertEqual(ready["liveId"], "9988776655")
        self.assertEqual(ready["objectNonceId"], "live-nonce")
        self.assertIn("/web/pages/live?", ready["liveUrl"])

    def test_search_doc_id_is_not_used_as_feed_object_id(self) -> None:
        response = {
            "data": [
                {
                    "docID": "finderobj_search_only",
                    "title": "Video title",
                    "videoUrl": "https://example.test/video.mp4",
                    "image": "https://example.test/cover.jpg",
                    "jumpInfo": {"userName": "video@finder"},
                }
            ]
        }

        payloads = finder_payload.payloads_from_text(json.dumps(response))

        self.assertTrue(payloads)
        self.assertTrue(all(payload.get("objectId") != "finderobj_search_only" for payload in payloads))
        self.assertTrue(any(payload.get("searchDocId") == "finderobj_search_only" for payload in payloads))

    def test_duplicate_account_nickname_is_not_guessed(self) -> None:
        response = {
            "data": [
                {
                    "docID": "finderacct_a",
                    "title": "Same Name",
                    "thumbUrl": "https://example.test/a.jpg",
                    "jumpInfo": {"userName": "a@finder"},
                },
                {
                    "docID": "finderacct_b",
                    "title": "Same Name",
                    "thumbUrl": "https://example.test/b.jpg",
                    "jumpInfo": {"userName": "b@finder"},
                },
                {
                    "docID": "finderobj_unlinked",
                    "title": "Video title",
                    "videoUrl": "https://example.test/video.mp4",
                    "image": "https://example.test/cover.jpg",
                    "width": 1080,
                    "height": 1920,
                    "duration": 12,
                    "source": {"title": "Same Name"},
                    "jumpInfo": {
                        "extInfo": json.dumps(
                            {"feedId": "14929828060974024711", "feedNonceId": "nonce-1"}
                        )
                    },
                },
            ]
        }

        payloads = finder_payload.payloads_from_text(json.dumps(response))
        payload = next(p for p in payloads if p.get("searchDocId") == "finderobj_unlinked")

        self.assertFalse(payload.get("username"))
        self.assertFalse(payload.get("avatar"))

    def test_search_endpoint_uses_environment_and_forwards_options(self) -> None:
        endpoint = "http://127.0.0.1:19088/api/searchFinder"
        with mock.patch.dict(os.environ, {"WX_FINDER_SEARCH_ENDPOINT": endpoint}):
            with mock.patch.object(
                finder_payload,
                "_resolve_with_endpoint",
                return_value={"ok": False, "error": "no_search_payload"},
            ) as resolver:
                result = finder_payload.resolve_finder_from_search(
                    query="live host",
                    offset=20,
                    scene=88,
                    business_type=9,
                    sub_type=9,
                    current_page=2,
                    session_id="session-1",
                    request_id="request-2",
                )

        resolver.assert_called_once_with(
            endpoint,
            {
                "query": "live host",
                "offset": 20,
                "scene": 88,
                "businessType": 9,
                "subType": 9,
                "currentPage": 2,
                "sessionId": "session-1",
                "requestId": "request-2",
            },
            15.0,
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["endpoint_error"], "no_search_payload")

    def test_search_endpoint_network_error_is_structured(self) -> None:
        endpoint = "http://127.0.0.1:19088/api/searchFinder"
        with mock.patch.object(
            finder_payload,
            "_resolve_with_endpoint",
            side_effect=urllib.error.URLError("offline"),
        ):
            result = finder_payload.resolve_finder_from_search(
                query="test",
                search_endpoint=endpoint,
            )

        self.assertFalse(result["ok"])
        self.assertIn("offline", result["endpoint_error"])

    def test_search_offset_requires_explicit_current_page(self) -> None:
        with mock.patch.object(finder_payload, "_resolve_with_endpoint") as resolver:
            result = finder_payload.resolve_finder_from_search(
                query="test",
                search_endpoint="http://127.0.0.1:19088/api/searchFinder",
                offset=20,
            )

        resolver.assert_not_called()
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "missing_current_page_for_offset")


if __name__ == "__main__":
    unittest.main()
