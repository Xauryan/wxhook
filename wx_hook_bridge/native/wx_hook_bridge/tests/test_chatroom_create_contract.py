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


class ChatroomCreateContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.offsets = read("src/include/wx_bridge_offsets.h")
        cls.header = read("src/include/wx_bridge_chatroom_member.h")
        cls.chatroom = read("src/modules/chatroom_member.c")
        cls.http = read("src/modules/http_api.c")
        cls.outbox = read("src/modules/outbox_dispatch.c")

    def test_verified_offset_and_public_declaration(self) -> None:
        self.assertRegex(
            self.offsets,
            r"#define\s+WX_OFF_CHATROOM_CREATE\s+0x1544030\b",
        )
        self.assertRegex(
            self.header,
            r"int\s+create_chatroom_to_result\s*\(\s*"
            r"const char \*seq,\s*"
            r"const char \*member_ids,\s*"
            r"const char \*topic,\s*"
            r"int wait_ms\s*\);",
        )

    def test_worker_resolves_service_and_builds_contiguous_member_vector(self) -> None:
        worker = function_body(self.chatroom, "create_chatroom_worker")
        init1 = worker.index("WX_OFF_ROOM_INIT1")
        init2 = worker.index("WX_OFF_ROOM_INIT2")
        init3 = worker.index("WX_OFF_ROOM_INIT3")
        dispatch = worker.index("WX_OFF_CHATROOM_CREATE")
        self.assertLess(init1, init2)
        self.assertLess(init2, init3)
        self.assertLess(init3, dispatch)
        self.assertIn("BYTE member_strings[CREATE_CHATROOM_MAX_MEMBERS][32]", worker)
        self.assertIn("for (i = 0; i < task->member_count; i++)", worker)
        self.assertIn(
            "wx_set_std_string_arena((int64_t)member_strings[i]", worker
        )
        self.assertIn("members_vec[0] = (int64_t)member_strings", worker)
        self.assertIn(
            "members_vec[1] = (int64_t)(member_strings + task->member_count)",
            worker,
        )
        self.assertIn("members_vec[2] = members_vec[1]", worker)

    def test_worker_reads_and_releases_returned_roomid_string(self) -> None:
        worker = function_body(self.chatroom, "create_chatroom_worker")
        read_roomid = worker.index(
            "wx_read_sso_str((int64_t)roomid_out, task->roomid"
        )
        free_roomid = worker.index(
            "wx_free_std_string_heap(base, (int64_t)roomid_out)"
        )
        self.assertLess(read_roomid, free_roomid)

    def test_member_validation_enforces_two_to_forty_unique_non_room_ids(self) -> None:
        normalize = function_body(
            self.chatroom, "normalize_create_chatroom_members"
        )
        self.assertRegex(
            self.chatroom,
            r"#define\s+CREATE_CHATROOM_MAX_MEMBERS\s+40\b",
        )
        self.assertIn("if (count < 2)", normalize)
        self.assertIn("at_least_two_members_required", normalize)
        self.assertIn("if (count >= CREATE_CHATROOM_MAX_MEMBERS)", normalize)
        self.assertIn("too_many_members", normalize)
        self.assertIn("create_chatroom_token_equals", normalize)
        self.assertIn("duplicate = 1", normalize)
        self.assertIn("if (!duplicate)", normalize)
        self.assertIn('str_find_sub(start, "@chatroom")', normalize)
        self.assertIn("member_id_is_chatroom", normalize)

    def test_result_reports_cgi_endpoint_and_created_roomid(self) -> None:
        writer = function_body(self.chatroom, "write_create_chatroom_result")
        decoded = writer.replace('\\"', '"')
        self.assertIn('"roomid":"', decoded)
        self.assertIn('"cgiType":119', decoded)
        self.assertIn(
            '"endpoint":"/cgi-bin/micromsg-bin/createchatroom"', decoded
        )
        self.assertIn('"upperCallOffset":"0x1544030"', decoded)

    def test_http_route_accepts_string_or_array_and_returns_full_result(self) -> None:
        array_helper = function_body(self.http, "json_string_list")
        self.assertIn("json_str(hm, key, NULL)", array_helper)
        self.assertIn('wsprintfA(path, "$.%s[%d]", key, i)', array_helper)
        self.assertIn("mg_json_get(hm->body, path", array_helper)
        self.assertIn("mg_json_get_str(hm->body, path)", array_helper)
        self.assertIn("value[used++] = ','", array_helper)

        route = branch_between(
            self.http,
            'uri_match(hm, "/api/createChatRoom")',
            'uri_match(hm, "/api/modifyChatRoomName")',
        )
        self.assertIn('json_string_list(hm, "memberIds")', route)
        self.assertIn("create_chatroom_to_result(seq", route)
        self.assertIn("reply_seq_result(c, seq, ok", route)
        self.assertNotIn("reply_msg(c", route)

    def test_outbox_type_45_preserves_seq_members_topic_timeout_order(self) -> None:
        branch = branch_between(
            self.outbox,
            'str_eq(parts[1], "45")',
            'str_eq(parts[1], "18")',
        )
        self.assertRegex(
            branch,
            r"int\s+wait_ms\s*=\s*parts\[4\]\s*\?\s*"
            r"\(int\)a2i64\(parts\[4\]\)\s*:\s*15000",
        )
        self.assertRegex(
            branch,
            r"create_chatroom_to_result\s*\(\s*"
            r"parts\[0\],\s*"
            r"parts\[2\]\s*\?\s*parts\[2\]\s*:\s*\"\",\s*"
            r"parts\[3\]\s*\?\s*parts\[3\]\s*:\s*\"\",\s*"
            r"wait_ms\s*\)",
        )


if __name__ == "__main__":
    unittest.main()
