/*
 * Module: hongbao closed-loop controller
 *
 * This controller only arms and observes the official hongbao route/task
 * chain. It never constructs receivewxhb/openwxhb CGI payloads.
 */

#include "../include/wx_bridge_debug_log.h"
#include "../include/wx_bridge_file_io.h"
#include "../include/wx_bridge_hongbao_closed_loop.h"
#include "../include/wx_bridge_hongbao_cold.h"
#include "../include/wx_bridge_hongbao_hooks.h"
#include "../include/wx_bridge_hongbao_runtime.h"
#include "wx_bridge_hongbao_runtime_internal.h"
#include "../include/wx_bridge_memory.h"
#include "../include/wx_bridge_offsets.h"
#include "../include/wx_bridge_paths.h"
#include "../include/wx_bridge_runtime.h"
#include "../include/wx_bridge_text.h"

typedef void (__fastcall *FnHbOpenProducer)(int64_t page_ctx);

typedef struct HbClosedLoopState {
    char seq[64];
    char sendid[128];
    char msgid[64];
    char wxid[256];
    char native_url[1024];
    char peer[256];
    char sender[256];
    volatile LONG auto_open;
    volatile LONG message_seen;
    volatile LONG receive_task_submitted;
    volatile LONG receive_entry_seen;
    volatile LONG page_ctx_ready;
    volatile LONG open_attempted;
    volatile LONG open_producer_returned;
    volatile LONG open_ctx_built;
    volatile LONG open_task_submitted;
    volatile LONG confirmed;
    volatile LONG64 last_page_ctx;
    volatile LONG64 last_receive_result;
    volatile LONG64 last_timing_obj;
    volatile LONG64 last_timing_ptr;
    volatile LONG last_timing_len;
    volatile LONG last_timing_cap;
    volatile LONG64 last_parent_ctx;
    volatile LONG64 last_arg;
    volatile LONG64 last_open_ctx;
    ULONGLONG updated_tick;
    char last_event[64];
    char last_error[160];
} HbClosedLoopState;

static HbClosedLoopState g_hb_closed_loop = {0};

static int hb_json_get_string(const char *json, const char *key, char *out, int max) {
    char pat[80];
    const char *p;
    int n = 0;
    if (!out || max <= 0) return 0;
    out[0] = 0;
    if (!json || !key) return 0;
    wsprintfA(pat, "\"%s\"", key);
    p = str_find_sub(json, pat);
    if (!p) return 0;
    p += lstrlenA(pat);
    while (*p == ' ' || *p == '\t' || *p == '\r' || *p == '\n') p++;
    if (*p != ':') return 0;
    p++;
    while (*p == ' ' || *p == '\t' || *p == '\r' || *p == '\n') p++;
    if (*p != '"') return 0;
    p++;
    while (*p && *p != '"' && n < max - 1) {
        if (*p == '\\' && p[1]) p++;
        out[n++] = *p++;
    }
    out[n] = 0;
    return n;
}

static int hb_json_get_bool(const char *json, const char *key, int def_value) {
    char pat[80];
    const char *p;
    if (!json || !key) return def_value;
    wsprintfA(pat, "\"%s\"", key);
    p = str_find_sub(json, pat);
    if (!p) return def_value;
    p += lstrlenA(pat);
    while (*p == ' ' || *p == '\t' || *p == '\r' || *p == '\n') p++;
    if (*p != ':') return def_value;
    p++;
    while (*p == ' ' || *p == '\t' || *p == '\r' || *p == '\n') p++;
    if (p[0] == 't' && p[1] == 'r' && p[2] == 'u' && p[3] == 'e') return 1;
    if (p[0] == 'f' && p[1] == 'a' && p[2] == 'l' && p[3] == 's' && p[4] == 'e') return 0;
    return def_value;
}

static LONG hb_load32(volatile LONG *value) {
    return InterlockedCompareExchange(value, 0, 0);
}

static int64_t hb_load64(volatile LONG64 *value) {
    return (int64_t)InterlockedCompareExchange64(value, 0, 0);
}

static void hb_closed_loop_update_request(void) {
    // Python script control file removed; default enabled cold grabbing
    // seq/sendid/msgid/wxid now set via native API calls from Python side if needed
    char seq[64] = {0};
    char sendid[128] = {0};
    char msgid[64] = {0};
    char wxid[256] = {0};
    // TODO: add native API for seq etc if Python side still needs to send seq
    if (g_hb_closed_loop.seq[0]) {
        safe_copy(seq, sizeof(seq), g_hb_closed_loop.seq);
    }
    // auto_open always true by default
}

static int hb_closed_loop_matches(const char *sendid, const char *msgid, const char *peer) {
    hb_closed_loop_update_request();
    if (g_hb_closed_loop.sendid[0] && sendid && sendid[0] &&
        str_eq(g_hb_closed_loop.sendid, sendid)) return 1;
    if (g_hb_closed_loop.msgid[0] && msgid && msgid[0] &&
        str_eq(g_hb_closed_loop.msgid, msgid)) {
        if (!g_hb_closed_loop.wxid[0]) return 1;
        return peer && peer[0] && str_eq(g_hb_closed_loop.wxid, peer);
    }
    return !g_hb_closed_loop.sendid[0] && !g_hb_closed_loop.msgid[0];
}

static void hb_closed_loop_log(const char *event) {
    char line[4096];
    char *p = line;
    char *end = line + sizeof(line) - 1;
    char num[64];
    append_raw(&p, end, "{\"event\":\""); append_escaped(&p, end, event ? event : "");
    append_raw(&p, end, "\",\"seq\":\""); append_escaped(&p, end, g_hb_closed_loop.seq);
    append_raw(&p, end, "\",\"sendId\":\""); append_escaped(&p, end, g_hb_closed_loop.sendid);
    append_raw(&p, end, "\",\"msgId\":\""); append_escaped(&p, end, g_hb_closed_loop.msgid);
    append_raw(&p, end, "\",\"wxid\":\""); append_escaped(&p, end, g_hb_closed_loop.wxid);
    append_raw(&p, end, "\",\"peer\":\""); append_escaped(&p, end, g_hb_closed_loop.peer);
    append_raw(&p, end, "\",\"sender\":\""); append_escaped(&p, end, g_hb_closed_loop.sender);
    append_raw(&p, end, "\",\"message_seen\":"); append_raw(&p, end, hb_load32(&g_hb_closed_loop.message_seen) ? "true" : "false");
    append_raw(&p, end, ",\"receive_task_submitted\":"); append_raw(&p, end, hb_load32(&g_hb_closed_loop.receive_task_submitted) ? "true" : "false");
    append_raw(&p, end, ",\"receive_entry_seen\":"); append_raw(&p, end, hb_load32(&g_hb_closed_loop.receive_entry_seen) ? "true" : "false");
    append_raw(&p, end, ",\"page_ctx_ready\":"); append_raw(&p, end, hb_load32(&g_hb_closed_loop.page_ctx_ready) ? "true" : "false");
    append_raw(&p, end, ",\"open_attempted\":"); append_raw(&p, end, hb_load32(&g_hb_closed_loop.open_attempted) ? "true" : "false");
    append_raw(&p, end, ",\"open_producer_returned\":"); append_raw(&p, end, hb_load32(&g_hb_closed_loop.open_producer_returned) ? "true" : "false");
    append_raw(&p, end, ",\"open_ctx_built\":"); append_raw(&p, end, hb_load32(&g_hb_closed_loop.open_ctx_built) ? "true" : "false");
    append_raw(&p, end, ",\"open_task_submitted\":"); append_raw(&p, end, hb_load32(&g_hb_closed_loop.open_task_submitted) ? "true" : "false");
    append_raw(&p, end, ",\"confirmed\":"); append_raw(&p, end, hb_load32(&g_hb_closed_loop.confirmed) ? "true" : "false");
    append_raw(&p, end, ",\"page_ctx\":\""); wsprintfA(num, "%I64x", hb_load64(&g_hb_closed_loop.last_page_ctx)); append_raw(&p, end, num);
    append_raw(&p, end, "\",\"receive_result\":\""); wsprintfA(num, "%I64x", hb_load64(&g_hb_closed_loop.last_receive_result)); append_raw(&p, end, num);
    append_raw(&p, end, "\",\"timing_obj\":\""); wsprintfA(num, "%I64x", hb_load64(&g_hb_closed_loop.last_timing_obj)); append_raw(&p, end, num);
    append_raw(&p, end, "\",\"timing_ptr\":\""); wsprintfA(num, "%I64x", hb_load64(&g_hb_closed_loop.last_timing_ptr)); append_raw(&p, end, num);
    append_raw(&p, end, "\",\"timing_len\":"); wsprintfA(num, "%ld", (long)hb_load32(&g_hb_closed_loop.last_timing_len)); append_raw(&p, end, num);
    append_raw(&p, end, ",\"timing_cap\":"); wsprintfA(num, "%ld", (long)hb_load32(&g_hb_closed_loop.last_timing_cap)); append_raw(&p, end, num);
    append_raw(&p, end, ",\"last_error\":\""); append_escaped(&p, end, g_hb_closed_loop.last_error);
    append_raw(&p, end, "\"}\r\n");
    *p = 0;
    append_file_bytes(HB_CLOSED_LOOP_LOG_FILE, line, (DWORD)(p - line));
}

static void hb_closed_loop_write_state(void) {
    char json[4096];
    char *p = json;
    char *end = json + sizeof(json) - 1;
    char num[64];
    append_raw(&p, end, "{\"ok\":true,\"command\":\"open-hongbao\"");
    append_raw(&p, end, ",\"seq\":\""); append_escaped(&p, end, g_hb_closed_loop.seq);
    append_raw(&p, end, "\",\"sendId\":\""); append_escaped(&p, end, g_hb_closed_loop.sendid);
    append_raw(&p, end, "\",\"msgId\":\""); append_escaped(&p, end, g_hb_closed_loop.msgid);
    append_raw(&p, end, "\",\"wxid\":\""); append_escaped(&p, end, g_hb_closed_loop.wxid);
    append_raw(&p, end, "\",\"peer\":\""); append_escaped(&p, end, g_hb_closed_loop.peer);
    append_raw(&p, end, "\",\"sender\":\""); append_escaped(&p, end, g_hb_closed_loop.sender);
    append_raw(&p, end, "\",\"native_task_submitted\":"); append_raw(&p, end, hb_load32(&g_hb_closed_loop.receive_task_submitted) ? "true" : "false");
    append_raw(&p, end, ",\"receipt_status_confirmed\":"); append_raw(&p, end, hb_load32(&g_hb_closed_loop.confirmed) ? "true" : "false");
    append_raw(&p, end, ",\"message_seen\":"); append_raw(&p, end, hb_load32(&g_hb_closed_loop.message_seen) ? "true" : "false");
    append_raw(&p, end, ",\"receive_entry_seen\":"); append_raw(&p, end, hb_load32(&g_hb_closed_loop.receive_entry_seen) ? "true" : "false");
    append_raw(&p, end, ",\"open_task_submitted\":"); append_raw(&p, end, hb_load32(&g_hb_closed_loop.open_task_submitted) ? "true" : "false");
    append_raw(&p, end, ",\"page_ctx_ready\":"); append_raw(&p, end, hb_load32(&g_hb_closed_loop.page_ctx_ready) ? "true" : "false");
    append_raw(&p, end, ",\"last_event\":\""); append_escaped(&p, end, g_hb_closed_loop.last_event);
    append_raw(&p, end, "\",\"last_error\":\""); append_escaped(&p, end, g_hb_closed_loop.last_error);
    append_raw(&p, end, "\",\"last_page_ctx\":\""); wsprintfA(num, "%I64x", hb_load64(&g_hb_closed_loop.last_page_ctx)); append_raw(&p, end, num);
    append_raw(&p, end, "\",\"last_receive_result\":\""); wsprintfA(num, "%I64x", hb_load64(&g_hb_closed_loop.last_receive_result)); append_raw(&p, end, num);
    append_raw(&p, end, "\",\"last_timing_len\":"); wsprintfA(num, "%ld", (long)hb_load32(&g_hb_closed_loop.last_timing_len)); append_raw(&p, end, num);
    append_raw(&p, end, "}");
    *p = 0;
    write_file_bytes(HB_CLOSED_LOOP_STATE_FILE, json, (DWORD)(p - json));
}

static void hb_closed_loop_set_event(const char *event) {
    safe_copy(g_hb_closed_loop.last_event, sizeof(g_hb_closed_loop.last_event), event ? event : "");
    g_hb_closed_loop.updated_tick = GetTickCount64();
    hb_closed_loop_log(event);
    hb_closed_loop_write_state();
}

int wxb_hb_closed_loop_enabled(void) {
    /* Legacy UI closed-loop observe/open-producer — permanently off.
     * Production red-packet claim is hongbao_cold only. */
    return 0;
}

/*
 * Cold-only hook set (keep):
 *   - source_submit 0x166A3C0  → vector claim
 *   - message parser 0x2A48A50 → backup claim
 *   - receive_mid_builder      → passive cgiTable cache for cold DI fallback
 *   - ParseExtra               → installed by wxb_hb_cold_poll
 *
 * Everything else (route probes, UI setter/pagectx, sentinels, task factory,
 * receive_route/task builders) is legacy and uninstalled.
 */
static void hb_install_cold_only_hooks(void) {
    if (!wxb_weixin_module()) return;
    uninstall_hb_route_dispatcher_hook();
    uninstall_hb_route_temp_vector_hook();
    uninstall_hb_route_vector_builder_hook();
    uninstall_hb_route_ctx_factory_hook();
    uninstall_hb_route_classifier_hook();
    uninstall_hb_route_item_dispatch_hook();
    uninstall_hb_receive_route_builder_hook();
    uninstall_hb_task_factory_hook();
    uninstall_hb_task_factory_inner_hook();
    uninstall_hb_receive_task_builder_hook();
    uninstall_hb_setter_hook();
    uninstall_hb_pagectx_hook();
    uninstall_hb_null_guard_hook();
    uninstall_hb_route_hook();
    uninstall_hb_sentinel_hooks();

    install_hb_source_submit_hook();
    install_hb_parser_hook();
    install_hb_receive_mid_builder_hook();
}

void wxb_hb_closed_loop_refresh(void) {
    if (!wxb_weixin_module()) return;
    /* Always force cold-only surface; closed-loop UI path stays disabled. */
    hb_install_cold_only_hooks();
}

void wxb_hb_closed_loop_poll(void) {
    wxb_hb_closed_loop_refresh();
    wxb_hb_cold_poll();
}

void wxb_hb_closed_loop_note_message(const char *sendid,
                                     const char *msgid,
                                     const char *native_url,
                                     const char *peer,
                                     const char *sender) {
    if (!wxb_hb_closed_loop_enabled()) return;
    if (!hb_closed_loop_matches(sendid, msgid, peer)) return;
    if (sendid && sendid[0]) safe_copy(g_hb_closed_loop.sendid, sizeof(g_hb_closed_loop.sendid), sendid);
    if (msgid && msgid[0]) safe_copy(g_hb_closed_loop.msgid, sizeof(g_hb_closed_loop.msgid), msgid);
    if (peer && peer[0]) safe_copy(g_hb_closed_loop.wxid, sizeof(g_hb_closed_loop.wxid), peer);
    safe_copy(g_hb_closed_loop.native_url, sizeof(g_hb_closed_loop.native_url), native_url ? native_url : "");
    safe_copy(g_hb_closed_loop.peer, sizeof(g_hb_closed_loop.peer), peer ? peer : "");
    safe_copy(g_hb_closed_loop.sender, sizeof(g_hb_closed_loop.sender), sender ? sender : "");
    InterlockedExchange(&g_hb_closed_loop.message_seen, 1);
    hb_closed_loop_set_event("message_seen");
}

void wxb_hb_closed_loop_note_receive_task(int64_t parent_ctx, int64_t arg) {
    char current_sendid[128] = {0};
    if (!wxb_hb_closed_loop_enabled()) return;
    hb_runtime_copy_recv_sendid(current_sendid, sizeof(current_sendid));
    if (!hb_closed_loop_matches(current_sendid, NULL, NULL)) return;
    InterlockedExchange64(&g_hb_closed_loop.last_parent_ctx, parent_ctx);
    InterlockedExchange64(&g_hb_closed_loop.last_arg, arg);
    InterlockedExchange(&g_hb_closed_loop.receive_task_submitted, 1);
    hb_closed_loop_set_event("receive_task_submitted");
}

void wxb_hb_closed_loop_note_sentinel(const char *name, DWORD rva, int64_t a1) {
    char current_sendid[128] = {0};
    if (!wxb_hb_closed_loop_enabled()) return;
    hb_runtime_copy_recv_sendid(current_sendid, sizeof(current_sendid));
    if (!hb_closed_loop_matches(current_sendid, NULL, NULL)) return;
    if (rva == WX_OFF_HB_RECEIVEWXHB_ENTRY) {
        InterlockedExchange(&g_hb_closed_loop.receive_entry_seen, 1);
        hb_closed_loop_set_event("receivewxhb_entry");
    } else if (rva == WX_OFF_HB_OPEN_CTX_BUILDER) {
        InterlockedExchange64(&g_hb_closed_loop.last_open_ctx, a1);
        InterlockedExchange(&g_hb_closed_loop.open_ctx_built, 1);
        hb_closed_loop_set_event("open_ctx_built");
    } else if (rva == WX_OFF_HB_OPENWXHB_ENTRY) {
        InterlockedExchange64(&g_hb_closed_loop.last_open_ctx, a1);
        InterlockedExchange(&g_hb_closed_loop.open_task_submitted, 1);
        hb_closed_loop_set_event("openwxhb_entry");
    } else if (name && *name) {
        hb_closed_loop_set_event(name);
    }
}

void wxb_hb_closed_loop_maybe_open_page_ctx(int64_t page_ctx, const char *reason) {
    /* Legacy UI open-producer path disabled (cold-only mode). */
    (void)page_ctx;
    (void)reason;
}
