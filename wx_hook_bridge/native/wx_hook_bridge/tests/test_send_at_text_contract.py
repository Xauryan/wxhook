import re
import unittest
from pathlib import Path


BRIDGE_ROOT = Path(__file__).resolve().parents[1]


def read(relative_path: str) -> str:
    return (BRIDGE_ROOT / relative_path).read_text(encoding="utf-8")


def function_body(source: str, name: str) -> str:
    match = re.search(rf"\b{re.escape(name)}\s*\([^;]*\)\s*\{{", source)
    if not match:
        raise AssertionError(f"function not found: {name}")
    depth = 0
    for index in range(match.end() - 1, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[match.start():index + 1]
    raise AssertionError(f"unterminated function: {name}")


def branch_between(source: str, start: str, end: str) -> str:
    start_index = source.find(start)
    if start_index < 0:
        raise AssertionError(f"branch start not found: {start}")
    end_index = source.find(end, start_index + len(start))
    if end_index < 0:
        raise AssertionError(f"branch end not found: {end}")
    return source[start_index:end_index]


class SendAtTextContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.header = read("src/include/wx_bridge_send_api.h")
        cls.send = read("src/modules/send_api.c")
        cls.http = read("src/modules/http_api.c")

    def test_public_declaration_and_real_mention_metadata_are_present(self) -> None:
        self.assertRegex(
            self.header,
            r"int\s+send_at_text\s*\(\s*"
            r"const char \*room_id,\s*const char \*wxids,\s*"
            r"const char \*nickname,\s*const char \*content\s*\);",
        )
        inner = function_body(self.send, "send_message_inner")
        self.assertIn("msg + 0x630", inner)
        self.assertIn("at_user_list", inner)
        self.assertIn("msg + 0x670", inner)

    def test_visible_text_prefixes_the_nickname_and_plain_text_has_no_mentions(self) -> None:
        send_at = function_body(self.send, "send_at_text")
        send_text = function_body(self.send, "send_text")
        self.assertIn("body[0] = '@'", send_at)
        self.assertIn("body[1 + nickname_len] = ' '", send_at)
        self.assertIn("send_message_inner(1, room_id, body, wxids)", send_at)
        self.assertIn("send_message_inner(1, wxid, content, NULL)", send_text)

    def test_http_route_accepts_room_member_nickname_and_message(self) -> None:
        route = branch_between(
            self.http,
            'uri_match(hm, "/api/sendAtText")',
            'uri_match(hm, "/api/sendImagesMsg")',
        )
        self.assertIn('json_str(hm, "chatRoomId", "")', route)
        self.assertIn('json_str(hm, "wxids", "")', route)
        self.assertIn('json_str(hm, "nickname", "")', route)
        self.assertIn('json_str(hm, "msg", "")', route)
        self.assertIn("send_at_text(room_id, wxids, nickname, msg)", route)


if __name__ == "__main__":
    unittest.main()
