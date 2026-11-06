/*
 * Module: initialization lifecycle and exported DLL entrypoints
 * Source: ../../../../wx_hook_bridge.dll.c lines 9360-9704
 */
#include "../include/wx_bridge_anti_revoke.h"
#include "../include/wx_bridge_account_info.h"
#include "../include/wx_bridge_a8key_task.h"
#include "../include/wx_bridge_browser_events.h"
#include "../include/wx_bridge_debug_log.h"
#include "../include/wx_bridge_file_io.h"
#include "../include/wx_bridge_forward_hooks.h"
#include "../include/wx_bridge_hongbao_closed_loop.h"
#include "../include/wx_bridge_hongbao_cold.h"
/* closed_loop_refresh now installs cold-only hooks (source_submit/parser/mid). */
#include "../include/wx_bridge_hongbao_hooks.h"
#include "../include/wx_bridge_hongbao_noclick.h"
#include "../include/wx_bridge_http_server.h"
#include "../include/wx_bridge_media_ops.h"
#include "../include/wx_bridge_memory.h"
#include "../include/wx_bridge_offsets.h"
#include "../include/wx_bridge_outbox_dispatch.h"
#include "../include/wx_bridge_paths.h"
#include "../include/wx_bridge_protocol.h"
#include "../include/wx_bridge_protocol_capture.h"
#include "../include/wx_bridge_receive_hooks.h"
#include "../include/wx_bridge_realtime_relay.h"
#include "../include/wx_bridge_revoke_hooks.h"
#include "../include/wx_bridge_runtime.h"
#include "../include/wx_bridge_sns_comment.h"
#include "../include/wx_bridge_sns_publish.h"
#include "../include/wx_bridge_sns_timeline.h"
#include "../include/wx_bridge_voice_probe.h"
#include "../include/wx_bridge_voice_transcribe.h"
#include "../include/wx_bridge_worker_control.h"

static volatile LONG g_initialized = 0;
static volatile LONG g_worker_active = 0;

static int worker_active(void) {
    return InterlockedCompareExchange(&g_worker_active, 0, 0) == 1;
}

static void set_worker_active(int active) {
    InterlockedExchange(&g_worker_active, active ? 1 : 0);
}

static int startup_nonce_char_valid(WCHAR ch) {
    return (ch >= L'0' && ch <= L'9') ||
           (ch >= L'a' && ch <= L'f') ||
           (ch >= L'A' && ch <= L'F');
}

static int copy_startup_nonce(WCHAR *out, LPVOID param) {
    const WxbStartupConfig *config = (const WxbStartupConfig *)param;
    if (!out) return 0;
    out[0] = 0;
    if (!config || config->magic != WXB_STARTUP_CONFIG_MAGIC ||
        config->size != sizeof(WxbStartupConfig)) {
        return 0;
    }
    for (DWORD i = 0; i < WXB_STARTUP_NONCE_CHARS; i++) {
        WCHAR ch = config->startup_nonce[i];
        if (!startup_nonce_char_valid(ch)) {
            out[0] = 0;
            return 0;
        }
        out[i] = ch;
    }
    out[WXB_STARTUP_NONCE_CHARS] = 0;
    return config->startup_nonce[WXB_STARTUP_NONCE_CHARS] == 0;
}

static int startup_file_path(WCHAR *path, const WCHAR *startup_nonce, const WCHAR *suffix) {
    if (!path || !startup_nonce || !startup_nonce[0] || !suffix || !suffix[0]) return 0;
    if ((DWORD)lstrlenW(WORK_ROOT) + WXB_PATH_MAX_SUFFIX_CHARS >= MAX_PATH) return 0;
    wsprintfW(path, L"%s\\worker_start_%s.%s", CONTROL_DIR, startup_nonce, suffix);
    return 1;
}

static void write_startup_file(const WCHAR *startup_nonce, const WCHAR *suffix, const char *text) {
    WCHAR path[MAX_PATH];
    if (!startup_file_path(path, startup_nonce, suffix)) return;
    write_file_bytes(path, text, (DWORD)lstrlenA(text));
}

static int startup_file_exists(const WCHAR *startup_nonce, const WCHAR *suffix) {
    WCHAR path[MAX_PATH];
    if (!startup_file_path(path, startup_nonce, suffix)) return 0;
    return file_exists(path);
}

static int handoff_file_path(WCHAR *path) {
    if (!path) return 0;
    if ((DWORD)lstrlenW(WORK_ROOT) + 34 >= MAX_PATH) return 0;
    wsprintfW(path, L"%s\\worker_handoff.txt", CONTROL_DIR);
    return 1;
}

static void write_handoff_nonce(const WCHAR *startup_nonce) {
    WCHAR path[MAX_PATH];
    char nonce[WXB_STARTUP_NONCE_CHARS + 1];
    if (!startup_nonce || !startup_nonce[0]) return;
    if (!handoff_file_path(path)) return;
    for (DWORD i = 0; i < WXB_STARTUP_NONCE_CHARS; i++) nonce[i] = (char)startup_nonce[i];
    nonce[WXB_STARTUP_NONCE_CHARS] = 0;
    write_file_bytes(path, nonce, WXB_STARTUP_NONCE_CHARS);
}

static int read_handoff_nonce(WCHAR *out) {
    WCHAR path[MAX_PATH];
    char nonce[WXB_STARTUP_NONCE_CHARS + 4] = {0};
    int n;
    if (!out) return 0;
    out[0] = 0;
    if (!handoff_file_path(path)) return 0;
    n = read_file_bytes(path, nonce, sizeof(nonce));
    if (n < (int)WXB_STARTUP_NONCE_CHARS) return 0;
    for (DWORD i = 0; i < WXB_STARTUP_NONCE_CHARS; i++) {
        WCHAR ch = (WCHAR)(unsigned char)nonce[i];
        if (!startup_nonce_char_valid(ch)) {
            out[0] = 0;
            return 0;
        }
        out[i] = ch;
    }
    out[WXB_STARTUP_NONCE_CHARS] = 0;
    return 1;
}

static void clear_handoff_nonce(void) {
    WCHAR path[MAX_PATH];
    if (!handoff_file_path(path)) return;
    write_file_bytes(path, "", 0);
}

static int current_owner_present(void) {
    char cur[160] = {0};
    DWORD pid = 0;
    char *p = cur;
    int n = read_file_bytes(WORKER_OWNER_FILE, cur, sizeof(cur));
    if (n <= 0) return 0;
    while (*p >= '0' && *p <= '9') {
        pid = pid * 10 + (DWORD)(*p - '0');
        p++;
    }
    if (*p != ':') return 0;
    return pid == GetCurrentProcessId();
}

static void cleanup_superseded_hooks(void) {
    uninstall_recv_hook();
    wxb_realtime_relay_stop();
    uninstall_image_task_trace_hooks();
    uninstall_sns_upload_trace_hooks();
    uninstall_sns_publish_trace_hooks();
    uninstall_sns_model_validate_capture_hook();
    uninstall_sns_like_probe_hook();
    uninstall_sns_model_lookup_probe_hook();
    uninstall_sns_timeline_object_hook();
    uninstall_finder_userpage_capture_hook();
    uninstall_protocol_capture_hook();
    uninstall_browser_event_hook();
}

static void cleanup_superseded_for_handoff(void) {
    WCHAR handoff_nonce[WXB_STARTUP_NONCE_CHARS + 1] = {0};
    HANDLE lock;
    if (!read_handoff_nonce(handoff_nonce)) return;
    lock = acquire_worker_handoff_lock();
    if (!lock) return;
    cleanup_superseded_hooks();
    write_startup_file(handoff_nonce, L"cleanup", "worker cleanup\n");
    release_worker_handoff_lock(lock);
}

static int wait_handoff_cleanup(const WCHAR *startup_nonce) {
    DWORD start;
    if (!startup_nonce || !startup_nonce[0]) return 1;
    start = GetTickCount();
    for (;;) {
        if (startup_file_exists(startup_nonce, L"cleanup")) return 1;
        if (GetTickCount() - start > 10000) return 0;
        Sleep(50);
    }
}

static int switch_worker_owner_after_cleanup(const WCHAR *startup_nonce) {
    HANDLE lock = acquire_worker_handoff_lock();
    int had_owner;
    if (!lock) return 0;
    had_owner = current_owner_present();
    if (had_owner) write_handoff_nonce(startup_nonce);
    write_worker_owner_token();
    release_worker_handoff_lock(lock);
    if (had_owner && !wait_handoff_cleanup(startup_nonce)) {
        clear_worker_owner_token_if_current();
        clear_handoff_nonce();
        return 0;
    }
    clear_handoff_nonce();
    return 1;
}

static int wait_startup_ack(const WCHAR *startup_nonce) {
    DWORD start;
    if (!startup_nonce || !startup_nonce[0]) return 1;
    write_startup_file(startup_nonce, L"ready", "worker ready\n");
    start = GetTickCount();
    for (;;) {
        if (startup_file_exists(startup_nonce, L"cancel")) return 0;
        if (startup_file_exists(startup_nonce, L"ack")) return 1;
        if (GetTickCount() - start > 30000) return 0;
        Sleep(50);
    }
}

static void ensure_initialized(void) {
    char self_profile[4096];
    ensure_dirs();
    wxb_ensure_weixin_module();
    if (!wxb_realtime_relay_start()) {
        write_status("realtime relay start failed\n");
    }
    /* Direction inference must not learn the peer as self when the first hooked
     * one-to-one message is outgoing.  Prime the runtime wxid from the account
     * manager before exposing DoAddMsg; failure remains explicit/unknown. */
    if (wxb_weixin_module() && !wxb_self_wxid_known()) {
        wxb_self_profile_json(self_profile, sizeof(self_profile));
    }
    /* Prime cached cold controls before exposing the receive hook. */
    if (wxb_weixin_module() && !recv_hook_installed()) wxb_hb_cold_refresh();
    if (wxb_weixin_module() && !async_queue_hook_installed()) install_async_queue_hook();
    if (wxb_weixin_module() && !forward_ui_entry_trace_hook_installed()) install_forward_ui_entry_trace_hook();
    if (wxb_weixin_module() && !forward_submit_trace_hook_installed()) install_forward_submit_trace_hook();
    if (wxb_weixin_module() && !forward_dispatch_hook_installed()) install_forward_dispatch_hook();
    if (wxb_weixin_module() && !recv_hook_installed()) install_recv_hook();
    if (wxb_weixin_module()) restore_revoke_dispatch_site_if_stale();
    if (wxb_weixin_module() && !revoke_entry_hook_installed()) install_revoke_entry_hook();
    if (wxb_weixin_module() && !revoke_real_hook_installed()) install_revoke_real_hook();
    if (wxb_weixin_module()) restore_anti_revoke_state();

    if (wxb_weixin_module()) {
        /* Cold-only: no UI auto-open / route probes / sentinels / noclick. */
        wxb_hb_closed_loop_refresh();
    }
    if (wxb_weixin_module()) refresh_image_task_trace_hooks();
    if (wxb_weixin_module()) refresh_sns_upload_trace_hooks();
    if (wxb_weixin_module()) refresh_sns_publish_trace_hooks();
    if (wxb_weixin_module()) refresh_protocol_capture_hook();
    if (wxb_weixin_module()) install_sns_model_validate_capture_hook();
    if (wxb_weixin_module()) refresh_sns_like_probe_hook();
    if (wxb_weixin_module()) refresh_sns_model_lookup_probe_hook();
    if (wxb_weixin_module()) refresh_voice_probe_hook();
    if (wxb_weixin_module()) refresh_finder_userpage_capture_hook();
    if (wxb_weixin_module() && !browser_event_hook_installed()) install_browser_event_hook();
    InterlockedExchange(&g_initialized, 1);
}

BOOL WINAPI DllMain(HINSTANCE hinst, DWORD reason, LPVOID reserved) {
    (void)reserved;
    if (reason == DLL_PROCESS_ATTACH) {
        wxb_set_bridge_instance(hinst);
        DisableThreadLibraryCalls(hinst);
    }
    return TRUE;
}

__declspec(dllexport) DWORD WINAPI WxBridgeWorker(LPVOID param) {
    WCHAR startup_nonce[WXB_STARTUP_NONCE_CHARS + 1] = {0};
    copy_startup_nonce(startup_nonce, param);
    if (!wxb_paths_init(wxb_bridge_instance(), param)) {
        write_status("worker path init failed\n");
        return 1;
    }
    if (!wait_startup_ack(startup_nonce)) {
        return 2;
    }
    if (!switch_worker_owner_after_cleanup(startup_nonce)) {
        write_status("worker handoff cleanup timeout\n");
        return 3;
    }
    {
        HANDLE hook_lock = acquire_worker_handoff_lock();
        if (!hook_lock) {
            clear_worker_owner_token_if_current();
            write_status("worker handoff lock failed\n");
            return 4;
        }
        ensure_initialized();
        release_worker_handoff_lock(hook_lock);
    }
    write_status("worker started\n");
    wxb_http_server_start();
    set_worker_active(1);
    write_startup_file(startup_nonce, L"active", "worker active\n");
    for (;;) {
        if (file_exists(STOP_FILE)) {
            set_worker_active(0);
            wxb_http_server_stop();
            uninstall_recv_hook();
            wxb_realtime_relay_stop();
            uninstall_forward_submit_trace_hook();
            uninstall_forward_ui_entry_trace_hook();
            uninstall_forward_dispatch_hook();
            uninstall_async_queue_hook();
            uninstall_revoke_entry_hook();
            uninstall_revoke_real_hook();
            uninstall_revoke_dispatch_hook();
            uninstall_hb_setter_hook();
            uninstall_hb_pagectx_hook();
            uninstall_hb_null_guard_hook();
            uninstall_hb_route_hook();
            uninstall_hb_route_dispatcher_hook();
            uninstall_hb_route_temp_vector_hook();
            uninstall_hb_route_vector_builder_hook();
            uninstall_hb_route_ctx_factory_hook();
            uninstall_hb_route_classifier_hook();
            uninstall_hb_receive_route_builder_hook();
            uninstall_hb_receive_mid_builder_hook();
            uninstall_hb_task_factory_hook();
            uninstall_hb_task_factory_inner_hook();
            uninstall_hb_receive_task_builder_hook();
            uninstall_hb_parser_hook();
            uninstall_hb_source_submit_hook();
            uninstall_hb_sentinel_hooks();
            uninstall_voice_probe_hook();
            uninstall_voice_trans_result_hook();
            uninstall_image_task_trace_hooks();
            uninstall_sns_upload_trace_hooks();
            uninstall_sns_publish_trace_hooks();
            uninstall_sns_model_validate_capture_hook();
            uninstall_sns_like_probe_hook();
            uninstall_sns_model_lookup_probe_hook();
            uninstall_sns_timeline_object_hook();
            uninstall_finder_userpage_capture_hook();
            uninstall_protocol_capture_hook();
            uninstall_a8key_dispatch_hook();
            uninstall_browser_event_hook();
            clear_worker_owner_token_if_current();
            write_status("worker stopped\n");
            wxb_debug_log_shutdown();
            break;
        }
        if (!worker_owner_still_current()) {
            set_worker_active(0);
            wxb_http_server_stop();
            cleanup_superseded_for_handoff();
            write_status("worker superseded\n");
            break;
        }
        if (!wxb_weixin_module()) {
            wxb_ensure_weixin_module();
            if (wxb_weixin_module()) ensure_initialized();
        }
        if (wxb_weixin_module()) {
            refresh_image_task_trace_hooks();
            refresh_sns_upload_trace_hooks();
            refresh_sns_publish_trace_hooks();
            refresh_protocol_capture_hook();
            install_sns_model_validate_capture_hook();
            refresh_sns_like_probe_hook();
            refresh_sns_model_lookup_probe_hook();
            refresh_voice_probe_hook();
            refresh_finder_userpage_capture_hook();
            if (!browser_event_hook_installed()) install_browser_event_hook();
            wxb_hb_closed_loop_poll();
            process_revoke_forward_jobs();
            poll_outbox();
        }
        Sleep(100);
    }
    return 0;
}

__declspec(dllexport) LRESULT CALLBACK WxGetMsgHook(int code, WPARAM wparam, LPARAM lparam) {
    if (code >= 0 && wxb_paths_ready() && worker_active()) {
        HANDLE hook_lock = acquire_worker_handoff_lock();
        if (hook_lock) {
            if (worker_active() && worker_owner_still_current()) {
                if (file_exists(STOP_FILE)) {
                    uninstall_recv_hook();
                    wxb_realtime_relay_stop();
                    uninstall_hb_setter_hook();
                    uninstall_hb_pagectx_hook();
                    uninstall_hb_null_guard_hook();
                    uninstall_hb_route_hook();
                    uninstall_hb_route_dispatcher_hook();
                    uninstall_hb_route_temp_vector_hook();
                    uninstall_hb_route_vector_builder_hook();
                    uninstall_hb_route_ctx_factory_hook();
                    uninstall_hb_route_classifier_hook();
                    uninstall_hb_receive_route_builder_hook();
                    uninstall_hb_receive_mid_builder_hook();
                    uninstall_hb_task_factory_hook();
                    uninstall_hb_task_factory_inner_hook();
                    uninstall_hb_receive_task_builder_hook();
                    uninstall_hb_parser_hook();
                    uninstall_hb_source_submit_hook();
                    uninstall_hb_sentinel_hooks();
                    uninstall_voice_probe_hook();
                    uninstall_voice_trans_result_hook();
                    uninstall_image_task_trace_hooks();
                    uninstall_sns_upload_trace_hooks();
                    uninstall_sns_publish_trace_hooks();
                    uninstall_sns_model_validate_capture_hook();
                    uninstall_sns_like_probe_hook();
                    uninstall_sns_model_lookup_probe_hook();
                    uninstall_sns_timeline_object_hook();
                    uninstall_finder_userpage_capture_hook();
                    uninstall_protocol_capture_hook();
                    uninstall_a8key_dispatch_hook();
                    uninstall_browser_event_hook();
                } else {
                    ensure_initialized();
                    if (wxb_weixin_module()) {
                        refresh_image_task_trace_hooks();
                        refresh_sns_upload_trace_hooks();
                        refresh_sns_publish_trace_hooks();
                        refresh_protocol_capture_hook();
                        install_sns_model_validate_capture_hook();
                        refresh_sns_like_probe_hook();
                        refresh_sns_model_lookup_probe_hook();
                        refresh_voice_probe_hook();
                        refresh_finder_userpage_capture_hook();
                        if (!browser_event_hook_installed()) install_browser_event_hook();
                        process_revoke_forward_jobs();
                        poll_outbox();
                    }
                }
            }
            release_worker_handoff_lock(hook_lock);
        }
    }
    return CallNextHookEx(NULL, code, wparam, lparam);
}

__declspec(dllexport) DWORD WINAPI WxBridgeVersion(void) {
    return 0x04192301;
}
