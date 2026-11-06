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


class FriendAcceptContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.offsets = read("src/include/wx_bridge_offsets.h")
        cls.friend = read("src/modules/friend_accept.c")
        cls.account = read("src/modules/account_info.c")

    def test_verified_friend_service_offset_is_declared(self) -> None:
        self.assertRegex(
            self.offsets,
            r"#define\s+WX_OFF_FRIEND_SERVICE\s+0x693050\b",
        )

    def test_native_call_resolves_official_service_chain(self) -> None:
        worker = function_body(self.friend, "friend_run_native")
        root = worker.index("WX_OFF_ROOM_INIT1")
        stage = worker.index("WX_OFF_ROOM_INIT2")
        service = worker.index("WX_OFF_FRIEND_SERVICE")
        accept = worker.index("accept_fn(service_pair[0]")
        self.assertLess(root, stage)
        self.assertLess(stage, service)
        self.assertLess(service, accept)
        self.assertIn("accept_fn(service_pair[0]", worker)
        self.assertIn("friend_release_shared_pair(service_pair)", worker)
        self.assertIn("friend_release_shared_pair(mid_pair)", worker)
        self.assertIn("friend_release_shared_pair(root_pair)", worker)

    def test_native_result_uses_official_sentinel_and_guards_message(self) -> None:
        worker = function_body(self.friend, "friend_run_native")
        validator = function_body(self.friend, "friend_native_string_valid")
        cleanup = function_body(self.friend, "friend_free_native_string")
        self.assertIn(
            "memset(&native_result, 0xAA, sizeof(native_result))",
            worker,
        )
        self.assertNotIn("ZeroMemory(&native_result", worker)
        self.assertIn(
            "friend_native_string_valid(&native_result.message)",
            worker,
        )
        self.assertIn("capacity > 0x100000", validator)
        self.assertIn("length > capacity", validator)
        self.assertIn("capacity == 15", validator)
        self.assertIn("is_readable_ptr(data, (SIZE_T)length + 1)", validator)
        self.assertIn("friend_native_string_valid(value)", cleanup)

    def test_submission_runs_on_weixin_queue_not_windows_pool(self) -> None:
        submit = function_body(self.friend, "friend_submit_job")
        self.assertIn("async_queue_hook_installed()", submit)
        self.assertIn("install_async_queue_hook()", submit)
        self.assertIn("queue_send((int64_t)entry)", submit)
        self.assertNotIn("QueueUserWorkItem", submit)
        self.assertNotIn("CreateThread", submit)

    def test_timeout_keeps_queue_reference_alive(self) -> None:
        accept = function_body(self.friend, "wxb_friend_accept")
        worker = function_body(self.friend, "friend_accept_worker")
        release = function_body(self.friend, "friend_job_release")
        self.assertIn("job->refs = 2", accept)
        self.assertIn("friend_job_release(job)", accept)
        self.assertIn("friend_job_release(job)", worker)
        self.assertIn("InterlockedDecrement(&job->refs)", release)

    def test_exception_diagnostics_are_persisted(self) -> None:
        capture = function_body(self.friend, "friend_capture_exception")
        log = function_body(self.friend, "friend_log_job")
        self.assertIn("ExceptionAddress", capture)
        self.assertIn("ExceptionInformation[0]", capture)
        self.assertIn("ExceptionInformation[1]", capture)
        self.assertIn("friend_accept_debug.txt", log)
        self.assertIn("worker_thread_id", log)
        self.assertIn("exception_fault_address", log)

    def test_only_established_relationship_states_are_reported_as_friend(self) -> None:
        category = function_body(self.account, "contact_category")
        self.assertIn('if (state == 2) return "deleted"', category)
        self.assertIn('if ((state & 1) != 0) return "friend"', category)
        self.assertIn('return "stranger"', category)
        self.assertIn("contact_category(wxid, state)", self.account)


if __name__ == "__main__":
    unittest.main()
