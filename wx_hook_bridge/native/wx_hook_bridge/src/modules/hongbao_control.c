/*
 * Module: hongbao control, page context capture, parser hook
 * Source: ../../../../wx_hook_bridge.dll.c lines 4452-5085
 */

#include "../include/wx_bridge_hongbao_hooks.h"
#include "../include/wx_bridge_hongbao_closed_loop.h"
#include "../include/wx_bridge_hongbao_cold.h"
#include "../include/wx_bridge_hongbao_entrypoints.h"
#include "../include/wx_bridge_hongbao_noclick.h"
#include "../include/wx_bridge_hongbao_runtime.h"
#include "wx_bridge_hongbao_runtime_internal.h"
#include "../include/wx_bridge_debug_log.h"
#include "../include/wx_bridge_file_io.h"
#include "../include/wx_bridge_hongbao_status.h"
#include "../include/wx_bridge_hongbao_trace.h"
#include "../include/wx_bridge_memory.h"
#include "../include/wx_bridge_offsets.h"
#include "../include/wx_bridge_paths.h"
#include "../include/wx_bridge_protocol.h"
#include "../include/wx_bridge_runtime.h"
#include "../include/wx_bridge_text.h"
#include "../include/wx_bridge_wechat_string.h"

typedef struct HbParserHookState {
    void *trampoline;
    BYTE orig[19];
    volatile LONG installed;
    volatile LONG hit;
} HbParserHookState;

static const BYTE HB_ORIG_MESSAGE_PARSE_BYTES[19] = {
    0x55,0x41,0x57,0x41,0x56,0x41,0x55,0x41,0x54,0x56,
    0x57,0x53,0x48,0x81,0xEC,0x18,0x04,0x00,0x00
};
static HbParserHookState g_hb_parser_hook_state = {0};

static void hb_capture_page_ctx(int64_t page_ctx) {
    int64_t receive_result = 0;
    int64_t timing_obj = 0;
    int64_t timing_ptr = 0;
    int64_t timing_len = 0;
    int64_t timing_cap = 0;
    char dbg[1024];

    if (!page_ctx) return;
    receive_result = r64((void *)(page_ctx + WX_HB_PAGE_RECEIVE_RESULT_OFF));
    if (receive_result) {
        timing_obj = receive_result + WX_HB_RECEIVE_TIMING_OFF;
        timing_ptr = r64((void *)timing_obj);
        timing_len = r64((void *)(timing_obj + 0x10));
        timing_cap = r64((void *)(timing_obj + 0x18));
    }

    hb_runtime_record_page_capture(page_ctx,
                                   receive_result,
                                   timing_obj,
                                   timing_ptr,
                                   (LONG)timing_len,
                                   (LONG)timing_cap);

    wsprintfA(dbg,
              "[hb-capture] hit=%ld page_ctx=%I64x receive=%I64x timing_obj=%I64x ptr=%I64x len=%I64x cap=%I64x",
              (long)hb_runtime_get_capture_hit(), page_ctx, receive_result, timing_obj, timing_ptr, timing_len, timing_cap);
    hb_dbg(dbg);
    hb_write_state();
}

typedef int64_t (__fastcall *FnHbOpenProducer)(int64_t page_ctx);

static int hb_open_last_page_ctx(const char *reason) {
    int64_t page_ctx = hb_runtime_get_last_page_ctx();
    int64_t receive_result = page_ctx ? r64((void *)(page_ctx + WX_HB_PAGE_RECEIVE_RESULT_OFF)) : 0;
    int64_t timing_obj = receive_result ? receive_result + WX_HB_RECEIVE_TIMING_OFF : 0;
    int64_t timing_ptr = timing_obj ? r64((void *)timing_obj) : 0;
    int64_t timing_len = timing_obj ? r64((void *)(timing_obj + 0x10)) : 0;
    int64_t timing_cap = timing_obj ? r64((void *)(timing_obj + 0x18)) : 0;
    char dbg[1024];

    if (!wxb_weixin_module() || !page_ctx || !receive_result || !timing_obj || !timing_ptr || timing_len <= 0 || timing_len > 0x100 || timing_cap < timing_len) {
        wsprintfA(dbg,
                  "[hb-open] rejected reason=%s page_ctx=%I64x receive=%I64x timing_obj=%I64x ptr=%I64x len=%I64x cap=%I64x",
                  reason ? reason : "", page_ctx, receive_result, timing_obj, timing_ptr, timing_len, timing_cap);
        hb_dbg(dbg);
        hb_write_state();
        return 0;
    }

    if (!hb_runtime_try_begin_open()) return 0;
    __try {
        FnHbOpenProducer fn = (FnHbOpenProducer)((int64_t)wxb_weixin_module() + WX_OFF_HB_OPEN_PRODUCER);
        wsprintfA(dbg,
                  "[hb-open] call reason=%s page_ctx=%I64x receive=%I64x timing_obj=%I64x ptr=%I64x len=%I64x cap=%I64x",
                  reason ? reason : "", page_ctx, receive_result, timing_obj, timing_ptr, timing_len, timing_cap);
        hb_dbg(dbg);
        fn(page_ctx);
        hb_runtime_record_open_success();
        hb_dbg("[hb-open] producer returned");
        hb_write_state();
        hb_runtime_end_open();
        return 1;
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        hb_dbg("[hb-open] EXCEPTION while calling producer");
        hb_write_state();
        hb_runtime_end_open();
        return 0;
    }
}

void poll_hb_control(void) {
    hb_runtime_set_verbose_enabled(1);
    hb_runtime_arm_same_stack_open();
}

int64_t __fastcall hb_pagectx_hook(int64_t page_ctx) {
    FnHbOpenProducer fn = (FnHbOpenProducer)((int64_t)wxb_weixin_module() + WX_OFF_HB_OPEN_PRODUCER);
    char dbg[512];
    hb_capture_page_ctx(page_ctx);
    if (hb_runtime_consume_same_stack_open()) {
        if (!hb_runtime_try_begin_open()) {
            hb_dbg("[hb-open] same-stack armed but call already in progress");
            return fn(page_ctx);
        }
        __try {
            wsprintfA(dbg, "[hb-open] same-stack call page_ctx=%I64x", page_ctx);
            hb_dbg(dbg);
            int64_t ret = fn(page_ctx);
            hb_runtime_record_open_success();
            hb_dbg("[hb-open] same-stack producer returned");
            hb_write_state();
            hb_runtime_end_open();
            return ret;
        } __except (EXCEPTION_EXECUTE_HANDLER) {
            hb_dbg("[hb-open] EXCEPTION in same-stack producer call");
            hb_write_state();
            hb_runtime_end_open();
            return 0;
        }
    }
    return fn(page_ctx);
}

int build_hb_pagectx_relay(BYTE *p) {
    BYTE *start = p;
    *p++ = 0x48; *p++ = 0xB8;         /* mov rax, hb_pagectx_hook */
    *(int64_t *)p = (int64_t)hb_pagectx_hook; p += 8;
    *p++ = 0xFF; *p++ = 0xE0;         /* jmp rax */
    return (int)(p - start);
}

static int hb_try_open_page_ctx(int64_t page_ctx, const char *reason) {
    int64_t receive_result = page_ctx ? r64((void *)(page_ctx + WX_HB_PAGE_RECEIVE_RESULT_OFF)) : 0;
    int64_t timing_obj = receive_result ? receive_result + WX_HB_RECEIVE_TIMING_OFF : 0;
    int64_t timing_ptr = timing_obj ? r64((void *)timing_obj) : 0;
    int64_t timing_len = timing_obj ? r64((void *)(timing_obj + 0x10)) : 0;
    int64_t timing_cap = timing_obj ? r64((void *)(timing_obj + 0x18)) : 0;
    char dbg[1024];

    if (!wxb_weixin_module() || !page_ctx || !receive_result || !timing_obj || !timing_ptr || timing_len != 0x20 || timing_cap < timing_len) {
        wsprintfA(dbg,
                  "[hb-auto] rejected reason=%s page_ctx=%I64x receive=%I64x timing_obj=%I64x ptr=%I64x len=%I64x cap=%I64x",
                  reason ? reason : "", page_ctx, receive_result, timing_obj, timing_ptr, timing_len, timing_cap);
        hb_dbg(dbg);
        hb_write_state();
        return 0;
    }

    if (hb_runtime_auto_page_seen(page_ctx)) {
        wsprintfA(dbg, "[hb-auto] duplicate page_ctx skipped reason=%s page_ctx=%I64x", reason ? reason : "", page_ctx);
        hb_dbg(dbg);
        return 0;
    }

    if (!hb_runtime_try_begin_open()) return 0;
    hb_runtime_mark_auto_page(page_ctx);
    __try {
        FnHbOpenProducer fn = (FnHbOpenProducer)((int64_t)wxb_weixin_module() + WX_OFF_HB_OPEN_PRODUCER);
        wsprintfA(dbg,
                  "[hb-auto] call reason=%s page_ctx=%I64x receive=%I64x timing_obj=%I64x ptr=%I64x len=%I64x cap=%I64x",
                  reason ? reason : "", page_ctx, receive_result, timing_obj, timing_ptr, timing_len, timing_cap);
        hb_dbg(dbg);
        fn(page_ctx);
        hb_runtime_record_open_success();
        if (hb_auto_one_shot_enabled()) DeleteFileW(HB_ARM_FILE);
        hb_dbg("[hb-auto] producer returned");
        hb_write_state();
        hb_runtime_end_open();
        return 1;
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        hb_dbg("[hb-auto] EXCEPTION while calling producer");
        hb_write_state();
        hb_runtime_end_open();
        return 0;
    }
}

static void hb_arm_deferred_auto(int64_t page_ctx, const char *reason) {
    UINT_PTR timer_id = 0;
    UINT_PTR old_timer = 0;
    char dbg[512];
    if (!page_ctx) return;
    if (hb_runtime_auto_page_seen(page_ctx)) {
        wsprintfA(dbg, "[hb-auto] duplicate pending skipped reason=%s page_ctx=%I64x",
                  reason ? reason : "", page_ctx);
        hb_dbg(dbg);
        return;
    }
    hb_runtime_set_pending_page_ctx(page_ctx);
    hb_runtime_set_pending_tick((LONG64)GetTickCount64());
    hb_runtime_set_pending_thread_id((LONG)GetCurrentThreadId());
    hb_runtime_set_pending_auto(1);
    old_timer = hb_runtime_exchange_pending_timer(0);
    if (old_timer) KillTimer(NULL, old_timer);
    timer_id = SetTimer(NULL, 0, HB_DEFERRED_OPEN_DELAY_MS, hb_deferred_timer_proc);
    hb_runtime_exchange_pending_timer(timer_id);
    wsprintfA(dbg, "[hb-auto] pending reason=%s page_ctx=%I64x tid=%lu delay_ms=%d timer=%I64x",
              reason ? reason : "", page_ctx, (unsigned long)GetCurrentThreadId(),
              HB_DEFERRED_OPEN_DELAY_MS, (int64_t)timer_id);
    hb_dbg(dbg);
}

void poll_hb_deferred_auto(void) {
    LONG state = hb_runtime_get_pending_auto();
    int64_t page_ctx = 0;
    ULONGLONG tick = 0;
    UINT_PTR timer_id = 0;
    DWORD tid = GetCurrentThreadId();
    char dbg[512];

    if (state != 1) return;
    if (!hb_auto_enabled()) {
        hb_runtime_set_pending_auto(0);
        hb_runtime_set_pending_page_ctx(0);
        timer_id = hb_runtime_exchange_pending_timer(0);
        if (timer_id) KillTimer(NULL, timer_id);
        hb_dbg("[hb-auto] pending cancelled; auto file missing");
        return;
    }
    if ((LONG)tid != hb_runtime_get_pending_thread_id()) return;
    tick = (ULONGLONG)hb_runtime_get_pending_tick();
    if (tick && GetTickCount64() - tick < HB_DEFERRED_OPEN_DELAY_MS) return;
    if (hb_runtime_compare_exchange_pending_auto(2, 1) != 1) return;
    timer_id = hb_runtime_exchange_pending_timer(0);
    if (timer_id) KillTimer(NULL, timer_id);

    page_ctx = hb_runtime_exchange_pending_page_ctx(0);
    if (!page_ctx) {
        hb_runtime_set_pending_auto(0);
        return;
    }
    wsprintfA(dbg, "[hb-auto] deferred call page_ctx=%I64x tid=%lu", page_ctx, (unsigned long)tid);
    hb_dbg(dbg);
    hb_try_open_page_ctx(page_ctx, "setter-deferred");
    hb_runtime_set_pending_auto(0);
}

void CALLBACK hb_deferred_timer_proc(HWND hwnd, UINT msg, UINT_PTR id, DWORD time_ms) {
    (void)hwnd;
    (void)msg;
    (void)id;
    (void)time_ms;
    poll_hb_deferred_auto();
}

int64_t __fastcall hb_setter_hook(int64_t owner, int64_t shared_page_ctx) {
    int64_t entry_ret = (int64_t)_ReturnAddress();
    int64_t *rsp = (int64_t *)_AddressOfReturnAddress();
    int64_t ret = hb_call_setter_original(owner, shared_page_ctx);
    int64_t page_ctx = owner ? r64((void *)(owner + 0x188)) : 0;
    int auto_enabled = hb_auto_enabled() ? 1 : 0;
    int64_t stack1 = rsp ? r64((void *)(rsp + 1)) : 0;
    int64_t stack2 = rsp ? r64((void *)(rsp + 2)) : 0;
    int64_t stack3 = rsp ? r64((void *)(rsp + 3)) : 0;
    int64_t stack4 = rsp ? r64((void *)(rsp + 4)) : 0;
    int64_t shared_obj = shared_page_ctx ? r64((void *)shared_page_ctx) : 0;
    int64_t shared_ctl = shared_page_ctx ? r64((void *)(shared_page_ctx + 8)) : 0;
    char dbg[1024];

    wsprintfA(dbg,
              "[hb-setter] owner=%I64x shared=%I64x shared_obj=%I64x shared_ctl=%I64x page_ctx=%I64x auto=%d ret=%I64x stk=%I64x,%I64x,%I64x,%I64x",
              owner, shared_page_ctx, shared_obj, shared_ctl, page_ctx, auto_enabled,
              entry_ret, stack1, stack2, stack3, stack4);
    hb_dbg(dbg);
    if (page_ctx) hb_capture_page_ctx(page_ctx);
    if (page_ctx) wxb_hb_closed_loop_maybe_open_page_ctx(page_ctx, "setter");
    if (page_ctx && auto_enabled) {
        hb_arm_deferred_auto(page_ctx, "setter");
    }
    return ret;
}

typedef int64_t (__fastcall *FnHbMessageParser)(int64_t a1, int64_t a2);

static int hb_log_parser_record(const char *phase,
                                LONG hit,
                                int64_t parser_a1,
                                int64_t vector_arg,
                                int64_t begin,
                                int64_t endp,
                                int index,
                                int64_t rec,
                                int64_t parser_ret) {
    static const int offsets[] = {
        0x18, 0x38, 0x58, 0x78, 0x98, 0xB8, 0xD8, 0xF8,
        0x118, 0x138, 0x160, 0x180, 0x1A0, 0x1C0, 0x1E0,
        0x208, 0x240, 0x270
    };
    char fields[18][512];
    char content[8192] = {0};
    char native_url[2048] = {0};
    char page_url[2048] = {0};
    char sendid[128] = {0};
    char channelid[32] = {0};
    char msgtype[32] = {0};
    char sendusername[256] = {0};
    char sign[512] = {0};
    char total_num[32] = {0};
    char peer[256] = {0};
    char sender[256] = {0};
    char snippet[768] = {0};
    char line[20000];
    char *p = line;
    char *jend = line + sizeof(line) - 1;
    char num[64];
    int found = 0;
    int field_count = (int)(sizeof(offsets) / sizeof(offsets[0]));
    int64_t page_ctx = hb_runtime_get_last_page_ctx();

    if (!rec || !is_readable_ptr(rec, 0x290)) return 0;
    for (int i = 0; i < field_count; i++) {
        fields[i][0] = 0;
        read_probe_string(rec + offsets[i], fields[i], sizeof(fields[i]));
        if (!content[0] &&
            (str_has(fields[i], "receivehongbao") ||
             str_has(fields[i], "wxhb_personalreceive") ||
             (str_has(fields[i], "<nativeurl") && str_has(fields[i], "sendid=")))) {
            safe_copy(content, sizeof(content), fields[i]);
        }
    }

    if (!content[0]) return 0;
    hb_extract_urls_from_text(content, native_url, sizeof(native_url), page_url, sizeof(page_url));
    hb_fill_query_fields(native_url, page_url,
                         sendid, sizeof(sendid),
                         channelid, sizeof(channelid),
                         msgtype, sizeof(msgtype),
                         sendusername, sizeof(sendusername),
                         sign, sizeof(sign),
                         total_num, sizeof(total_num));
    if (!sendid[0]) extract_query_value(content, "sendid", sendid, sizeof(sendid));
    if (!channelid[0]) extract_query_value(content, "channelid", channelid, sizeof(channelid));
    if (!msgtype[0]) extract_query_value(content, "msgtype", msgtype, sizeof(msgtype));
    if (!sendusername[0]) extract_query_value(content, "sendusername", sendusername, sizeof(sendusername));

    if (field_count > 0) safe_copy(peer, sizeof(peer), fields[0]);
    if (sendusername[0]) safe_copy(sender, sizeof(sender), sendusername);
    else if (field_count > 2 && is_wxid_like(fields[2])) safe_copy(sender, sizeof(sender), fields[2]);
    else if (field_count > 1 && is_wxid_like(fields[1])) safe_copy(sender, sizeof(sender), fields[1]);
    safe_copy(snippet, sizeof(snippet), content);

    hb_runtime_record_receive(sendid[0] ? sendid : NULL,
                              1,
                              native_url,
                              page_url,
                              peer,
                              sender,
                              rec,
                              0,
                              0,
                              0,
                              0,
                              0,
                              1);
    wxb_hb_closed_loop_note_message(sendid, "", native_url, peer, sender);
    found = 1;

    append_raw(&p, jend, "{\"event\":\"hb_parser_recv\"");
    append_raw(&p, jend, ",\"phase\":\""); append_escaped(&p, jend, phase ? phase : "");
    append_raw(&p, jend, "\",\"hit\":"); wsprintfA(num, "%ld", (long)hit); append_raw(&p, jend, num);
    append_raw(&p, jend, ",\"tid\":"); wsprintfA(num, "%lu", (unsigned long)GetCurrentThreadId()); append_raw(&p, jend, num);
    append_raw(&p, jend, ",\"index\":"); wsprintfA(num, "%d", index); append_raw(&p, jend, num);
    append_hex_field(&p, jend, "parser_ret", parser_ret);
    append_hex_field(&p, jend, "a1", parser_a1);
    append_hex_field(&p, jend, "vector", vector_arg);
    append_hex_field(&p, jend, "begin", begin);
    append_hex_field(&p, jend, "end", endp);
    append_hex_field(&p, jend, "rec", rec);
    append_hex_field(&p, jend, "last_page_ctx", page_ctx);
    append_raw(&p, jend, ",\"sendId\":\""); append_escaped(&p, jend, sendid);
    append_raw(&p, jend, "\",\"channelId\":\""); append_escaped(&p, jend, channelid);
    append_raw(&p, jend, "\",\"msgType\":\""); append_escaped(&p, jend, msgtype);
    append_raw(&p, jend, "\",\"sendUserName\":\""); append_escaped(&p, jend, sendusername);
    append_raw(&p, jend, "\",\"sign\":\""); append_escaped(&p, jend, sign);
    append_raw(&p, jend, "\",\"totalNum\":\""); append_escaped(&p, jend, total_num);
    append_raw(&p, jend, "\",\"nativeUrl\":\""); append_escaped(&p, jend, native_url);
    append_raw(&p, jend, "\",\"pageUrl\":\""); append_escaped(&p, jend, page_url);
    append_raw(&p, jend, "\",\"peer\":\""); append_escaped(&p, jend, peer);
    append_raw(&p, jend, "\",\"sender\":\""); append_escaped(&p, jend, sender);
    append_raw(&p, jend, "\",\"fields\":{");
    for (int i = 0; i < field_count; i++) {
        if (i) append_raw(&p, jend, ",");
        append_raw(&p, jend, "\"o");
        wsprintfA(num, "%x", offsets[i]);
        append_raw(&p, jend, num);
        append_raw(&p, jend, "\":\"");
        append_escaped(&p, jend, fields[i]);
        append_raw(&p, jend, "\"");
    }
    append_raw(&p, jend, "}");
    append_raw(&p, jend, ",\"contentSnippet\":\""); append_escaped(&p, jend, snippet);
    append_raw(&p, jend, "\"}\r\n");
    *p = 0;
    /* Receive candidate dump is diagnostic-only; skip on hot path when cold
     * grab is active (avoids multi-KB disk write before/around claim). */
    if (!wxb_hb_cold_enabled()) {
        append_file_bytes(HB_RECEIVE_LOG, line, (DWORD)(p - line));
        if (hb_auto_enabled() && !page_ctx) {
            hb_dbg("[hb-parser] receive-only hongbao seen; live page_ctx is still absent");
        }
    }
    return found;
}

int hb_scan_parser_vector(const char *phase,
                          LONG hit,
                          int64_t parser_a1,
                          int64_t vector_arg,
                          int64_t parser_ret) {
    int64_t begin = 0;
    int64_t endp = 0;
    int64_t delta = 0;
    int count = 0;
    int found = 0;
    char dbg[512];
    if (!hb_runtime_verbose_enabled()) return 0;
    if (!vector_arg || !is_readable_ptr(vector_arg, 0x10)) return 0;
    begin = r64((void *)vector_arg);
    endp = r64((void *)(vector_arg + 8));
    if (!begin || !endp || endp <= begin) return 0;
    delta = endp - begin;
    if (delta <= 0 || delta > 0x290 * 64) {
        wsprintfA(dbg, "[hb-parser] skip phase=%s hit=%ld vector=%I64x begin=%I64x end=%I64x delta=%I64x",
                  phase ? phase : "", (long)hit, vector_arg, begin, endp, delta);
        hb_dbg(dbg);
        return 0;
    }
    count = (int)(delta / 0x290);
    if (count <= 0) count = 1;
    if (count > 64) count = 64;
    for (int i = 0; i < count; i++) {
        int64_t rec = begin + (int64_t)i * 0x290;
        if (rec + 0x20 > endp) break;
        if (hb_log_parser_record(phase, hit, parser_a1, vector_arg, begin, endp, i, rec, parser_ret)) {
            found = 1;
        }
    }
    return found;
}

int hb_noclick_scan_source_vector(LONG hit, int64_t a1, int64_t vector_arg);

int64_t __fastcall hb_parser_hook(int64_t a1, int64_t a2) {
    FnHbMessageParser orig = (FnHbMessageParser)g_hb_parser_hook_state.trampoline;
    LONG hit = InterlockedIncrement(&g_hb_parser_hook_state.hit);
    int found_before = 0;
    int64_t ret = 0;
    /*
     * Cold backup path: some sessions skip source_submit.
     * Frida parity: build BEFORE original parser body, Launch AFTER.
     */
    (void)found_before;
    ULONGLONG pt0 = GetTickCount64();
    hb_cold_scan_source_vector(hit, a1, a2);    /* onEnter: Fill + Service → build parent */
    ULONGLONG pt1 = GetTickCount64();
    if (orig) ret = orig(a1, a2);               /* original parser body */
    ULONGLONG pt2 = GetTickCount64();
    hb_cold_flush_launches();                   /* setTimeout(0): Launch_receive */
    ULONGLONG pt3 = GetTickCount64();
    if (hb_cold_enabled() && (pt3 - pt0) > 50) {
        char dbg[192];
        wsprintfA(dbg, "[parser-time] tid=%lu scan=%lums orig=%lums flush=%lums total=%lums\r\n",
                  (unsigned long)GetCurrentThreadId(),
                  (unsigned long)(pt1 - pt0), (unsigned long)(pt2 - pt1),
                  (unsigned long)(pt3 - pt2), (unsigned long)(pt3 - pt0));
        hb_dbg(dbg);
    }
    return ret;
}

static int build_hb_parser_trampoline(BYTE *p, BYTE *target) {
    BYTE *start = p;
    copy_bytes(p, g_hb_parser_hook_state.orig, sizeof(g_hb_parser_hook_state.orig));
    p += sizeof(g_hb_parser_hook_state.orig);
    *p++ = 0x48; *p++ = 0xB8;         /* mov rax, target+patch_len */
    *(int64_t *)p = (int64_t)(target + sizeof(g_hb_parser_hook_state.orig)); p += 8;
    *p++ = 0xFF; *p++ = 0xE0;         /* jmp rax */
    return (int)(p - start);
}

void install_hb_parser_hook(void) {
    if (g_hb_parser_hook_state.installed || !wxb_weixin_module()) return;
    BYTE *target = (BYTE *)((int64_t)wxb_weixin_module() + WX_OFF_HB_MESSAGE_PARSE);
    BYTE patch[sizeof(g_hb_parser_hook_state.orig)];
    DWORD old = 0;

    restore_code_if_changed(target, HB_ORIG_MESSAGE_PARSE_BYTES,
                            sizeof(HB_ORIG_MESSAGE_PARSE_BYTES),
                            "[hb-parser] stale hook restored", 1);
    copy_bytes(g_hb_parser_hook_state.orig, target, sizeof(g_hb_parser_hook_state.orig));
    if (!bytes_equal(g_hb_parser_hook_state.orig, HB_ORIG_MESSAGE_PARSE_BYTES,
                     sizeof(g_hb_parser_hook_state.orig))) {
        hb_dbg("[hb-parser] skip; unexpected bytes at message parser");
        return;
    }

    g_hb_parser_hook_state.trampoline = VirtualAlloc(NULL, 256, MEM_COMMIT | MEM_RESERVE, PAGE_EXECUTE_READWRITE);
    if (!g_hb_parser_hook_state.trampoline) {
        hb_dbg("[hb-parser] trampoline alloc failed");
        return;
    }
    int tlen = build_hb_parser_trampoline((BYTE *)g_hb_parser_hook_state.trampoline, target);
    FlushInstructionCache(GetCurrentProcess(), g_hb_parser_hook_state.trampoline, tlen);

    for (int i = 0; i < (int)sizeof(patch); i++) patch[i] = 0x90;
    patch[0] = 0x48; patch[1] = 0xB8; /* mov rax, hb_parser_hook */
    *(int64_t *)(patch + 2) = (int64_t)hb_parser_hook;
    patch[10] = 0xFF; patch[11] = 0xE0; /* jmp rax */

    VirtualProtect(target, sizeof(patch), PAGE_EXECUTE_READWRITE, &old);
    copy_bytes(target, patch, sizeof(patch));
    VirtualProtect(target, sizeof(patch), old, &old);
    FlushInstructionCache(GetCurrentProcess(), target, sizeof(patch));
    InterlockedExchange(&g_hb_parser_hook_state.installed, 1);
    hb_dbg("[hb-parser] hook installed message_parse_182A48A50");
}

void uninstall_hb_parser_hook(void) {
    if (!g_hb_parser_hook_state.installed || !wxb_weixin_module()) return;
    BYTE *target = (BYTE *)((int64_t)wxb_weixin_module() + WX_OFF_HB_MESSAGE_PARSE);
    DWORD old = 0;
    VirtualProtect(target, sizeof(g_hb_parser_hook_state.orig), PAGE_EXECUTE_READWRITE, &old);
    copy_bytes(target, g_hb_parser_hook_state.orig, sizeof(g_hb_parser_hook_state.orig));
    VirtualProtect(target, sizeof(g_hb_parser_hook_state.orig), old, &old);
    FlushInstructionCache(GetCurrentProcess(), target, sizeof(g_hb_parser_hook_state.orig));
    InterlockedExchange(&g_hb_parser_hook_state.installed, 0);
    if (g_hb_parser_hook_state.trampoline) {
        VirtualFree(g_hb_parser_hook_state.trampoline, 0, MEM_RELEASE);
        g_hb_parser_hook_state.trampoline = NULL;
    }
    hb_dbg("[hb-parser] hook removed message_parse_182A48A50");
}
