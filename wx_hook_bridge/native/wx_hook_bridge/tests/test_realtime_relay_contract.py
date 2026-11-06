import re
import unittest
from pathlib import Path


BRIDGE_ROOT = Path(__file__).resolve().parents[1]
SRC = BRIDGE_ROOT / "src"


def read(relative_path: str) -> str:
    return (BRIDGE_ROOT / relative_path).read_text(encoding="utf-8")


def function_body(source: str, name: str) -> str:
    match = re.search(rf"\b{name}\s*\([^;]*\)\s*\{{", source)
    if not match:
        raise AssertionError(f"function not found: {name}")
    start = match.start()
    depth = 0
    for index in range(match.end() - 1, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[start:index + 1]
    raise AssertionError(f"unterminated function: {name}")


class RealtimeRelayContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.receive = read("src/modules/receive_hooks.c")
        cls.relay = read("src/modules/realtime_relay.c")
        cls.protocol = read("src/modules/protocol.c")
        cls.paths_header = read("src/include/wx_bridge_paths.h")
        cls.paths = read("src/modules/paths.c")
        cls.lifecycle = read("src/modules/lifecycle_exports.c")
        cls.build = read("build.ps1")

    def test_receive_hook_emits_stable_message_envelope(self) -> None:
        source = self.receive.replace('\\"', '"')
        required_fragments = (
            'wsprintfA(event_id, "wechat-message:%016I64x:%s:%s:%s"',
            '"event_id"',
            '"event_type":"message"',
            '"source":"wx_hook_bridge"',
            '"schema_version":1',
            '"message_id"',
            '"message_type"',
            '"message_subtype"',
            '"timestamp_unix"',
            '"direction"',
            '"is_incoming"',
            '"is_outgoing"',
            '"peer_id"',
            '"room_id"',
            '"sender_id"',
            '"self_id"',
            '"raw_from"',
            '"raw_to"',
            '"content_truncated"',
            '"content_original_bytes"',
            'wxb_realtime_relay_enqueue(event_id, json',
        )
        for fragment in required_fragments:
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, source)

    def test_envelope_does_not_repeat_canonical_keys(self) -> None:
        process_message = function_body(self.receive, "process_message")
        for key in ("direction", "content", "signature"):
            with self.subTest(key=key):
                self.assertEqual(process_message.count(f'\\"{key}\\"'), 1)
        self.assertIn('append_raw(&p, end, ",\\"peerWxId\\":\\"")', process_message)

    def test_receive_hook_has_no_synchronous_transport_fallback(self) -> None:
        self.assertNotIn("recv_push_async", self.receive)
        self.assertNotIn("QueueUserWorkItem", self.receive)
        self.assertNotIn("post_json_to_python", self.receive)
        self.assertNotIn("WinHttp", self.receive)

    def test_receive_identity_covers_session_and_zero_server_ids(self) -> None:
        process_message = function_body(self.receive, "process_message")
        self.assertNotIn("if (!msg_id", process_message)
        self.assertIn("realtime_hash_string(event_session)", process_message)
        self.assertIn("local_id_s, ts_s", process_message)
        self.assertIn("realtime_event_key(", process_message)

    def test_long_content_keeps_a_valid_envelope_tail(self) -> None:
        self.assertIn("REALTIME_CONTENT_BUFFER 65536", self.receive)
        self.assertIn("REALTIME_ENVELOPE_BUFFER 131072", self.receive)
        self.assertIn("REALTIME_JSON_TAIL_RESERVE 8192", self.receive)
        self.assertIn("realtime_append_escaped_bounded", self.receive)
        self.assertIn("REALTIME_JSON_MAX 131072", self.relay)

    def test_self_identity_is_primed_before_receive_hook(self) -> None:
        init = function_body(self.lifecycle, "ensure_initialized")
        self.assertLess(init.index("wxb_self_profile_json"), init.index("install_recv_hook()"))

    def test_enqueue_is_fixed_pool_and_transport_free(self) -> None:
        enqueue = function_body(self.relay, "wxb_realtime_relay_enqueue")
        self.assertIn("REALTIME_JOB_POOL_SIZE 256", self.relay)
        self.assertIn("InterlockedPopEntrySList", enqueue)
        self.assertIn("InterlockedPushEntrySList", enqueue)
        self.assertIn("SetEvent", enqueue)
        for forbidden in (
            "HeapAlloc", "malloc(", "CreateThread", "WaitForSingleObject",
            "Sleep(", "CreateFile", "WriteFile", "post_json_to_python", "WinHttp",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, enqueue)

    def test_pending_batch_is_restored_to_fifo_order(self) -> None:
        drain = function_body(self.relay, "realtime_drain_memory")
        self.assertIn("realtime_reverse_entries(InterlockedFlushSList", drain)

    def test_disk_queue_is_atomic_and_persisted_before_delivery(self) -> None:
        self.assertIn("FlushFileBuffers(file)", self.relay)
        self.assertIn("MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH", self.relay)
        self.assertIn('L"%s\\\\*.json"', self.relay)
        process_job = function_body(self.relay, "realtime_process_job")
        self.assertLess(process_job.index("realtime_persist("), process_job.index("realtime_deliver_file("))
        self.assertIn("realtime_retry_disk_queue", self.relay)
        self.assertIn("REALTIME_RETRY_INITIAL_MS 1000", self.relay)
        self.assertIn("REALTIME_RETRY_MAX_MS 30000", self.relay)

    def test_permanent_rejections_and_corrupt_files_do_not_poison_queue(self) -> None:
        retry = function_body(self.relay, "realtime_retry_disk_queue")
        deliver = function_body(self.relay, "realtime_deliver_file")
        self.assertIn('realtime_quarantine_file(path, L".invalid")', retry)
        self.assertIn('realtime_quarantine_file(path, L".rejected")', deliver)
        self.assertIn("wxb_post_json_to_python_status", deliver)
        self.assertIn("status != 408", self.relay)
        self.assertIn("status != 429", self.relay)

    def test_transport_is_loopback_only_with_short_timeout(self) -> None:
        self.assertIn("DIRECT_PUSH_PORT 8765", self.paths_header)
        self.assertIn('DIRECT_PUSH_PATH L"/api/realtime/ingest"', self.paths_header)
        self.assertIn("static DWORD g_push_timeout_ms = 250", self.protocol)
        self.assertIn('lstrcmpiW(host, L"127.0.0.1")', self.protocol)
        self.assertIn('lstrcmpiW(host, L"::1")', self.protocol)
        self.assertIn("!wxb_is_loopback_host_w(host)", self.protocol)
        self.assertIn("!wxb_is_loopback_host_a(host)", self.protocol)

    def test_queue_and_status_stay_under_work_root(self) -> None:
        self.assertIn('SET_PATH(realtime_queue_dir, L"\\\\bridge\\\\realtime_queue")', self.paths)
        self.assertIn(
            'SET_PATH(realtime_status_file, L"\\\\bridge\\\\control\\\\realtime_relay_status.json")',
            self.paths,
        )
        self.assertIn("CreateDirectoryW(REALTIME_QUEUE_DIR", read("src/modules/file_io.c"))

    def test_lifecycle_starts_before_hook_and_stops_after_unhook(self) -> None:
        init = function_body(self.lifecycle, "ensure_initialized")
        self.assertLess(init.index("wxb_realtime_relay_start()"), init.index("install_recv_hook()"))
        self.assertRegex(
            self.lifecycle,
            r"uninstall_recv_hook\(\);\s*wxb_realtime_relay_stop\(\);",
        )

    def test_build_manifest_compiles_relay(self) -> None:
        self.assertIn('src\\modules\\realtime_relay.c', self.build)


if __name__ == "__main__":
    unittest.main()
