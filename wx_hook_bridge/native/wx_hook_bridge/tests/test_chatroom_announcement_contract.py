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


class ChatroomAnnouncementContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.task = read("src/modules/a8key_task.c")
        cls.http = read("src/modules/http_api.c")

    def test_cgi_send_runs_only_inside_the_queued_worker(self) -> None:
        worker = function_body(self.task, "chatroom_announcement_worker")
        publish = function_body(
            self.task, "publish_chatroom_announcement_to_result"
        )
        entry = function_body(
            self.task, "chatroom_announcement_queue_entry"
        )

        self.assertIn("WX_OFF_CHATROOM_ANNOUNCEMENT_SEND", worker)
        self.assertNotIn("WX_OFF_CHATROOM_ANNOUNCEMENT_SEND", publish)
        self.assertIn("chatroom_announcement_worker", entry)
        self.assertIn(
            "queue_send((int64_t)chatroom_announcement_queue_entry)",
            publish,
        )

    def test_timeout_does_not_cancel_or_free_a_running_worker(self) -> None:
        publish = function_body(
            self.task, "publish_chatroom_announcement_to_result"
        )
        self.assertIn("if (status == 0 || status == 2)", publish)
        self.assertIn("status = -2", publish)
        self.assertNotIn(
            "InterlockedCompareExchange(&task->status, -2", publish
        )
        self.assertIn(
            "InterlockedCompareExchangePointer(", publish
        )
        self.assertIn("g_chatroom_announcement_pending", publish)

    def test_task_lifetime_has_caller_and_queue_references(self) -> None:
        publish = function_body(
            self.task, "publish_chatroom_announcement_to_result"
        )
        entry = function_body(
            self.task, "chatroom_announcement_queue_entry"
        )
        release = function_body(
            self.task, "chatroom_announcement_task_release"
        )

        self.assertIn("task->refs = 2", publish)
        self.assertIn("chatroom_announcement_task_release(task)", publish)
        self.assertIn("chatroom_announcement_task_release(task)", entry)
        self.assertIn("InterlockedDecrement(&task->refs)", release)
        self.assertIn("HeapFree(GetProcessHeap(), 0, task)", release)

    def test_result_contains_queue_and_exception_diagnostics(self) -> None:
        writer = function_body(
            self.task, "write_publish_chatroom_announcement_result"
        ).replace('\\"', '"')
        for field in (
            '"queueStatus":',
            '"netErr":',
            '"cgiErr":',
            '"exceptionFault":"0x',
            '"ctxRcx":"0x',
            '"ctxRdx":"0x',
            '"ctxR8":"0x',
            '"ctxR9":"0x',
            '"queuedOnWeixinThread":true',
        ):
            with self.subTest(field=field):
                self.assertIn(field, writer)

    def test_http_route_returns_seq_for_sidecar_correlation(self) -> None:
        route = branch_between(
            self.http,
            'uri_match(hm, "/api/publishChatRoomAnnouncement")',
            'uri_match(hm, "/api/addMemberToChatRoom")',
        )
        self.assertIn("publish_chatroom_announcement_to_result", route)
        self.assertIn("reply_seq_result(c, seq, ok", route)
        self.assertNotIn("reply_msg(c", route)


if __name__ == "__main__":
    unittest.main()
