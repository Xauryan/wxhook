#include "../include/wx_bridge_paths.h"
#include "../include/wx_bridge_runtime.h"

static WxbPaths g_paths;
static WxbPaths g_empty_paths;
static volatile LONG g_paths_state = 0;

static LONG paths_state(void) {
    return InterlockedCompareExchange(&g_paths_state, 0, 0);
}

static int copy_root(WCHAR *out, const WCHAR *root) {
    DWORD len;
    if (!out || !root || !root[0]) return 0;
    len = (DWORD)lstrlenW(root);
    if (len == 0 || len >= MAX_PATH) return 0;
    while (len > 3 && (root[len - 1] == L'\\' || root[len - 1] == L'/')) len--;
    CopyMemory(out, root, len * sizeof(WCHAR));
    out[len] = 0;
    return 1;
}

static int module_directory(HMODULE module, WCHAR *out) {
    DWORD len;
    WCHAR *slash;
    if (!module || !out) return 0;
    len = GetModuleFileNameW(module, out, MAX_PATH);
    if (len == 0 || len >= MAX_PATH) return 0;
    slash = out + len;
    while (slash > out && slash[-1] != L'\\' && slash[-1] != L'/') slash--;
    if (slash == out) return 0;
    if (slash - out > 3) slash--;
    *slash = 0;
    return out[0] != 0;
}

static int join_root(WCHAR *out, const WCHAR *root, const WCHAR *suffix) {
    DWORD root_len;
    DWORD suffix_len;
    if (!out || !root || !suffix) return 0;
    root_len = (DWORD)lstrlenW(root);
    suffix_len = (DWORD)lstrlenW(suffix);
    if (root_len + suffix_len >= MAX_PATH) return 0;
    CopyMemory(out, root, root_len * sizeof(WCHAR));
    CopyMemory(out + root_len, suffix, (suffix_len + 1) * sizeof(WCHAR));
    return 1;
}

static int populate_paths(WxbPaths *paths, const WCHAR *root) {
#define SET_PATH(field, suffix) if (!join_root(paths->field, root, suffix)) return 0
    if (!copy_root(paths->work_root, root)) return 0;
    root = paths->work_root;
    SET_PATH(bridge_dir, L"\\bridge");
    SET_PATH(inbox_dir, L"\\bridge\\inbox");
    SET_PATH(outbox_dir, L"\\bridge\\outbox");
    SET_PATH(control_dir, L"\\bridge\\control");
    SET_PATH(realtime_queue_dir, L"\\bridge\\realtime_queue");
    SET_PATH(outbox_file, L"\\bridge\\outbox\\next.txt");
    SET_PATH(stop_file, L"\\bridge\\control\\stop.txt");
    SET_PATH(status_file, L"\\bridge\\control\\status.txt");
    SET_PATH(realtime_status_file, L"\\bridge\\control\\realtime_relay_status.json");
    SET_PATH(anti_revoke_state_file, L"\\bridge\\control\\anti_revoke_state.txt");
    SET_PATH(revoke_log, L"\\bridge\\control\\revoke_log.txt");
    SET_PATH(contact_names_file, L"\\bridge\\control\\contact_names.tsv");
    SET_PATH(forward_cache_file, L"\\bridge\\control\\forward_cache_ptrs.txt");
    SET_PATH(worker_owner_file, L"\\bridge\\control\\worker_owner.txt");
    SET_PATH(revoke_cache_dir, L"\\bridge\\control\\revoke_cache");
    SET_PATH(recv_log, L"\\bridge\\control\\recv_debug.txt");
    SET_PATH(quote_debug_log, L"\\bridge\\control\\quote_debug.txt");
    SET_PATH(forward_debug_log, L"\\bridge\\control\\forward_debug.txt");
    SET_PATH(voice_probe_enable_file, L"\\bridge\\control\\voice_probe.enable");
    SET_PATH(voice_probe_log, L"\\bridge\\control\\voice_probe_debug.txt");
    SET_PATH(voice_probe_last_silk_file, L"\\bridge\\control\\voice_probe_last.silk");
    SET_PATH(voice_probe_final_replay_once_file, L"\\bridge\\control\\voice_probe.final_replay_once");
    SET_PATH(voice_source_builder_shim_file, L"\\bridge\\control\\voice_source_builder_shim.enable");
    SET_PATH(voice_source_ctx_branch_file, L"\\bridge\\control\\voice_source_ctx_branch.enable");
    SET_PATH(a8key_task_enable_file, L"\\bridge\\control\\a8key_task.enable");
    SET_PATH(protocol_capture_enable_file, L"\\bridge\\control\\protocol_capture.enable");
    SET_PATH(protocol_capture_unsafe_dispatch_enable_file, L"\\bridge\\control\\protocol_capture.unsafe_dispatch.enable");
    SET_PATH(protocol_capture_log, L"\\bridge\\control\\protocol_capture.jsonl");
    SET_PATH(finder_userpage_capture_enable_file, L"\\bridge\\control\\finder_userpage_capture.enable");
    SET_PATH(finder_userpage_capture_log, L"\\bridge\\control\\finder_userpage_capture.jsonl");
    SET_PATH(sns_timeline_object_capture_log, L"\\bridge\\control\\sns_timeline_objects.jsonl");
    SET_PATH(sns_timeline_response_capture_log, L"\\bridge\\control\\sns_timeline_response.jsonl");
    SET_PATH(sns_media_decrypt_capture_log, L"\\bridge\\control\\sns_media_decrypt.jsonl");
    SET_PATH(sns_media_probe_enable_file, L"\\bridge\\control\\sns_media_probe.enable");
    SET_PATH(sns_media_probe_log, L"\\bridge\\control\\sns_media_probe.jsonl");
    SET_PATH(sns_upload_trace_enable_file, L"\\bridge\\control\\sns_upload_trace.enable");
    SET_PATH(sns_upload_trace_log, L"\\bridge\\control\\sns_upload_trace.jsonl");
    SET_PATH(sns_publish_trace_enable_file, L"\\bridge\\control\\sns_publish_trace.enable");
    SET_PATH(sns_publish_trace_log, L"\\bridge\\control\\sns_publish_trace.jsonl");
    SET_PATH(browser_event_log, L"\\bridge\\control\\browser_events.jsonl");
    SET_PATH(image_task_trace_enable_file, L"\\bridge\\control\\image_task_trace.enable");
    SET_PATH(image_task_trace_log, L"\\bridge\\control\\image_task_trace.jsonl");
    SET_PATH(hb_debug_log, L"\\bridge\\control\\hb_open_debug.txt");
    SET_PATH(hb_receive_log, L"\\bridge\\control\\hb_receive_debug.txt");
    SET_PATH(hb_route_log, L"\\bridge\\control\\hb_route_debug.txt");
    SET_PATH(hb_open_once_file, L"\\bridge\\control\\hb_open_once.txt");
    SET_PATH(hb_arm_file, L"\\bridge\\control\\hb_auto_open.enable");
    SET_PATH(hb_always_file, L"\\bridge\\control\\hb_auto_open.always");
    SET_PATH(hb_noclick_file, L"\\bridge\\control\\hb_noclick.enable");
    SET_PATH(hb_noclick_always_file, L"\\bridge\\control\\hb_noclick.always");
    SET_PATH(hb_noclick_direct_file, L"\\bridge\\control\\hb_noclick.direct");
    SET_PATH(hb_state_file, L"\\bridge\\control\\hb_open_state.txt");
    SET_PATH(hb_verbose_file, L"\\bridge\\control\\hb_verbose.enable");
    SET_PATH(hb_closed_loop_request_file, L"\\bridge\\control\\hb_closed_loop_request.json");
    SET_PATH(hb_closed_loop_state_file, L"\\bridge\\control\\hb_closed_loop_state.json");
    SET_PATH(hb_closed_loop_log_file, L"\\bridge\\control\\hb_closed_loop.jsonl");
    SET_PATH(hb_cold_disable_file, L"\\bridge\\control\\hb_cold.disable");
    SET_PATH(hb_cold_open_disable_file, L"\\bridge\\control\\hb_cold_open.disable");
    SET_PATH(hb_cold_fastlane_enable_file, L"\\bridge\\control\\hb_cold_fastlane.enable");
    SET_PATH(hb_cold_delay_file, L"\\bridge\\control\\hb_cold_delay.json");
    SET_PATH(sns_publish_upper_enable_file, L"\\bridge\\control\\sns_publish_upper.enable");
    SET_PATH(sns_video_manager_entry_enable_file, L"\\bridge\\control\\sns_video_manager_entry.enable");
    SET_PATH(sns_video_thumb_map_file, L"\\bridge\\control\\sns_publish_video_thumb_map.tsv");
    SET_PATH(sns_like_probe_enable_file, L"\\bridge\\control\\sns_like_probe.enable");
    SET_PATH(sns_like_probe_log_file, L"\\bridge\\control\\sns_like_probe.jsonl");
    SET_PATH(sns_model_lookup_probe_enable_file, L"\\bridge\\control\\sns_model_lookup_probe.enable");
    SET_PATH(sns_model_lookup_probe_log_file, L"\\bridge\\control\\sns_model_lookup_probe.jsonl");
    SET_PATH(sns_model_validate_capture_log_file, L"\\bridge\\control\\sns_model_validate_capture.jsonl");
    SET_PATH(voice_trans_result_log, L"\\bridge\\control\\voice_trans_result.jsonl");
    SET_PATH(sns_image_dl_param_file, L"\\bridge\\control\\sns_image_dl_param.bin");
    SET_PATH(sns_full_cdn_param_file, L"\\bridge\\control\\sns_full_cdn_param.bin");
    SET_PATH(image_dl_param_file, L"\\bridge\\control\\image_dl_param.bin");
#undef SET_PATH
    return 1;
}

int wxb_paths_init(HMODULE module, LPVOID startup_param) {
    const WxbStartupConfig *config = (const WxbStartupConfig *)startup_param;
    const WCHAR *requested_root = NULL;
    DWORD free_after_read = 0;
    WCHAR fallback_root[MAX_PATH] = {0};
    WxbPaths next = {0};
    int ok;
    LONG state;

    if (config && config->magic == WXB_STARTUP_CONFIG_MAGIC &&
        config->size == sizeof(WxbStartupConfig)) {
        free_after_read = config->flags & WXB_STARTUP_CONFIG_FREE_AFTER_READ;
        if (config->work_root[0]) requested_root = config->work_root;
    }
    state = paths_state();
    if (state != 0) {
        if (free_after_read) VirtualFree(startup_param, 0, MEM_RELEASE);
        while (state == 1) {
            Sleep(1);
            state = paths_state();
        }
        return wxb_paths_ready();
    }
    if (!requested_root) {
        if (!module_directory(module, fallback_root)) return 0;
        requested_root = fallback_root;
    }
    ok = populate_paths(&next, requested_root);
    if (free_after_read) VirtualFree(startup_param, 0, MEM_RELEASE);
    if (!ok) return 0;
    state = InterlockedCompareExchange(&g_paths_state, 1, 0);
    if (state != 0) {
        while (paths_state() == 1) Sleep(1);
        return wxb_paths_ready();
    }
    CopyMemory(&g_paths, &next, sizeof(g_paths));
    MemoryBarrier();
    InterlockedExchange(&g_paths_state, 2);
    return 1;
}

int wxb_paths_ready(void) {
    return paths_state() == 2;
}

const WxbPaths *wxb_paths(void) {
    return wxb_paths_ready() ? &g_paths : &g_empty_paths;
}
