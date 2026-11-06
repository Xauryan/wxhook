#include "../include/wx_bridge_hongbao_runtime.h"
#include "wx_bridge_hongbao_runtime_internal.h"
#include "../include/wx_bridge_text.h"

typedef struct HbRuntimeState {
    volatile LONG64 last_receive_route_ctx;
    volatile LONG64 last_receive_mid_ctx;
    volatile LONG64 last_receive_mid_payload;
    volatile LONG64 last_receive_parent_ctx;
    volatile LONG64 last_receive_arg;
    volatile LONG64 last_route_desc;
    volatile LONG64 last_route_ctx;
    volatile LONG64 last_route_mgr;
    volatile LONG last_route_type;
    volatile LONG64 last_page_ctx;
    volatile LONG64 last_receive_result;
    volatile LONG64 last_timing_obj;
    volatile LONG64 last_timing_ptr;
    volatile LONG last_timing_len;
    volatile LONG last_timing_cap;
    char last_sendid[128];
    char last_pagectx_sendid[128];
    volatile LONG capture_hit;
    volatile LONG open_attempt;
    volatile LONG open_success;
    volatile LONG open_in_progress;
    volatile LONG open_same_stack_arm;
    volatile LONG64 last_auto_page_ctx;
    volatile LONG64 pending_page_ctx;
    volatile LONG64 pending_tick;
    volatile LONG64 pending_timer_id;
    volatile LONG pending_auto;
    volatile LONG pending_thread_id;
    volatile LONG capture_thread_id;
    ULONGLONG last_capture_tick;
    char recv_sendid[128];
    char recv_native_url[2048];
    char recv_page_url[2048];
    char recv_peer[256];
    char recv_sender[256];
    volatile LONG verbose;
    volatile LONG64 recv_msg_base;
    volatile LONG64 recv_msgid;
    volatile LONG recv_local_id;
    volatile LONG64 recv_tick;
    volatile LONG recv_hit;
} HbRuntimeState;

static HbRuntimeState g_hb_runtime_state = {0};

static int64_t hb_load64(volatile LONG64 *value) {
    return (int64_t)InterlockedCompareExchange64(value, 0, 0);
}

static LONG hb_load32(volatile LONG *value) {
    return InterlockedCompareExchange(value, 0, 0);
}

void hb_runtime_record_page_capture(int64_t page_ctx,
                                    int64_t receive_result,
                                    int64_t timing_obj,
                                    int64_t timing_ptr,
                                    LONG timing_len,
                                    LONG timing_cap) {
    HbRuntimeState *s = &g_hb_runtime_state;
    InterlockedExchange64(&s->last_page_ctx, page_ctx);
    InterlockedExchange64(&s->last_receive_result, receive_result);
    InterlockedExchange64(&s->last_timing_obj, timing_obj);
    InterlockedExchange64(&s->last_timing_ptr, timing_ptr);
    InterlockedExchange(&s->last_timing_len, timing_len);
    InterlockedExchange(&s->last_timing_cap, timing_cap);
    InterlockedExchange(&s->capture_thread_id, (LONG)GetCurrentThreadId());
    s->last_capture_tick = GetTickCount64();
    safe_copy(s->last_pagectx_sendid, sizeof(s->last_pagectx_sendid), s->last_sendid);
    InterlockedIncrement(&s->capture_hit);
}

void hb_runtime_get_page_snapshot(HbPageCaptureSnapshot *out) {
    HbRuntimeState *s = &g_hb_runtime_state;
    if (!out) return;
    out->page_ctx = hb_load64(&s->last_page_ctx);
    out->receive_result = hb_load64(&s->last_receive_result);
    out->timing_obj = hb_load64(&s->last_timing_obj);
    out->timing_ptr = hb_load64(&s->last_timing_ptr);
    out->timing_len = hb_load32(&s->last_timing_len);
    out->timing_cap = hb_load32(&s->last_timing_cap);
    safe_copy(out->last_sendid, sizeof(out->last_sendid), s->last_sendid);
    safe_copy(out->last_pagectx_sendid, sizeof(out->last_pagectx_sendid), s->last_pagectx_sendid);
    out->capture_hit = hb_load32(&s->capture_hit);
    out->open_attempt = hb_load32(&s->open_attempt);
    out->open_success = hb_load32(&s->open_success);
    out->capture_thread_id = hb_load32(&s->capture_thread_id);
    out->last_capture_tick = s->last_capture_tick;
}

int64_t hb_runtime_get_last_page_ctx(void) {
    return hb_load64(&g_hb_runtime_state.last_page_ctx);
}

int64_t hb_runtime_get_last_receive_result(void) {
    return hb_load64(&g_hb_runtime_state.last_receive_result);
}

LONG hb_runtime_get_capture_hit(void) {
    return hb_load32(&g_hb_runtime_state.capture_hit);
}

ULONGLONG hb_runtime_get_last_capture_tick(void) {
    return g_hb_runtime_state.last_capture_tick;
}

void hb_runtime_set_last_sendid(const char *sendid) {
    safe_copy(g_hb_runtime_state.last_sendid,
              sizeof(g_hb_runtime_state.last_sendid),
              sendid ? sendid : "");
}

void hb_runtime_copy_last_pagectx_sendid(char *out, int max) {
    safe_copy(out, max, g_hb_runtime_state.last_pagectx_sendid);
}

int hb_runtime_try_begin_open(void) {
    HbRuntimeState *s = &g_hb_runtime_state;
    if (InterlockedCompareExchange(&s->open_in_progress, 1, 0) != 0) return 0;
    InterlockedIncrement(&s->open_attempt);
    return 1;
}

void hb_runtime_record_open_success(void) {
    InterlockedIncrement(&g_hb_runtime_state.open_success);
}

void hb_runtime_end_open(void) {
    InterlockedExchange(&g_hb_runtime_state.open_in_progress, 0);
}

void hb_runtime_arm_same_stack_open(void) {
    InterlockedExchange(&g_hb_runtime_state.open_same_stack_arm, 1);
}

int hb_runtime_consume_same_stack_open(void) {
    return InterlockedCompareExchange(&g_hb_runtime_state.open_same_stack_arm, 0, 1) == 1;
}

int hb_runtime_auto_page_seen(int64_t page_ctx) {
    return hb_load64(&g_hb_runtime_state.last_auto_page_ctx) == page_ctx;
}

void hb_runtime_mark_auto_page(int64_t page_ctx) {
    InterlockedExchange64(&g_hb_runtime_state.last_auto_page_ctx, page_ctx);
}

void hb_runtime_set_pending_page_ctx(int64_t page_ctx) {
    InterlockedExchange64(&g_hb_runtime_state.pending_page_ctx, page_ctx);
}

int64_t hb_runtime_exchange_pending_page_ctx(int64_t page_ctx) {
    return (int64_t)InterlockedExchange64(&g_hb_runtime_state.pending_page_ctx, page_ctx);
}

void hb_runtime_set_pending_tick(int64_t tick) {
    InterlockedExchange64(&g_hb_runtime_state.pending_tick, tick);
}

int64_t hb_runtime_get_pending_tick(void) {
    return hb_load64(&g_hb_runtime_state.pending_tick);
}

UINT_PTR hb_runtime_exchange_pending_timer(UINT_PTR timer_id) {
    return (UINT_PTR)InterlockedExchange64(&g_hb_runtime_state.pending_timer_id,
                                           (LONG64)timer_id);
}

void hb_runtime_set_pending_auto(LONG state) {
    InterlockedExchange(&g_hb_runtime_state.pending_auto, state);
}

LONG hb_runtime_get_pending_auto(void) {
    return hb_load32(&g_hb_runtime_state.pending_auto);
}

LONG hb_runtime_compare_exchange_pending_auto(LONG exchange, LONG compare) {
    return InterlockedCompareExchange(&g_hb_runtime_state.pending_auto, exchange, compare);
}

void hb_runtime_set_pending_thread_id(LONG thread_id) {
    InterlockedExchange(&g_hb_runtime_state.pending_thread_id, thread_id);
}

LONG hb_runtime_get_pending_thread_id(void) {
    return hb_load32(&g_hb_runtime_state.pending_thread_id);
}

void hb_runtime_set_verbose_enabled(int enabled) {
    InterlockedExchange(&g_hb_runtime_state.verbose, enabled ? 1 : 0);
}

int hb_runtime_verbose_enabled(void) {
    return hb_load32(&g_hb_runtime_state.verbose) != 0;
}

void hb_runtime_record_receive(const char *sendid,
                               int update_last_sendid,
                               const char *native_url,
                               const char *page_url,
                               const char *peer,
                               const char *sender,
                               int64_t msg_base,
                               int has_msgid,
                               int64_t msgid,
                               int has_local_id,
                               LONG local_id,
                               int reset_msg_ids,
                               int increment_hit) {
    HbRuntimeState *s = &g_hb_runtime_state;
    if (sendid) {
        if (update_last_sendid) {
            safe_copy(s->last_sendid, sizeof(s->last_sendid), sendid);
        }
        safe_copy(s->recv_sendid, sizeof(s->recv_sendid), sendid);
    }
    if (native_url) safe_copy(s->recv_native_url, sizeof(s->recv_native_url), native_url);
    if (page_url) safe_copy(s->recv_page_url, sizeof(s->recv_page_url), page_url);
    if (peer) safe_copy(s->recv_peer, sizeof(s->recv_peer), peer);
    if (sender) safe_copy(s->recv_sender, sizeof(s->recv_sender), sender);
    InterlockedExchange64(&s->recv_msg_base, msg_base);
    if (reset_msg_ids) {
        InterlockedExchange64(&s->recv_msgid, 0);
        InterlockedExchange(&s->recv_local_id, 0);
    } else {
        if (has_msgid) InterlockedExchange64(&s->recv_msgid, msgid);
        if (has_local_id) InterlockedExchange(&s->recv_local_id, local_id);
    }
    InterlockedExchange64(&s->recv_tick, (LONG64)GetTickCount64());
    if (increment_hit) InterlockedIncrement(&s->recv_hit);
}

void hb_runtime_get_receive_snapshot(HbReceiveSnapshot *out) {
    HbRuntimeState *s = &g_hb_runtime_state;
    if (!out) return;
    safe_copy(out->sendid, sizeof(out->sendid), s->recv_sendid);
    safe_copy(out->native_url, sizeof(out->native_url), s->recv_native_url);
    safe_copy(out->page_url, sizeof(out->page_url), s->recv_page_url);
    safe_copy(out->peer, sizeof(out->peer), s->recv_peer);
    safe_copy(out->sender, sizeof(out->sender), s->recv_sender);
    out->msg_base = hb_load64(&s->recv_msg_base);
    out->msgid = hb_load64(&s->recv_msgid);
    out->local_id = hb_load32(&s->recv_local_id);
    out->tick = hb_load64(&s->recv_tick);
    out->hit = hb_load32(&s->recv_hit);
}

void hb_runtime_copy_recv_sendid(char *out, int max) {
    safe_copy(out, max, g_hb_runtime_state.recv_sendid);
}

void hb_runtime_set_receive_mid(int64_t mid_ctx, int64_t payload) {
    HbRuntimeState *s = &g_hb_runtime_state;
    InterlockedExchange64(&s->last_receive_mid_ctx, mid_ctx);
    InterlockedExchange64(&s->last_receive_mid_payload, payload);
}

void hb_runtime_set_receive_parent_arg(int64_t parent_ctx, int64_t arg) {
    HbRuntimeState *s = &g_hb_runtime_state;
    InterlockedExchange64(&s->last_receive_parent_ctx, parent_ctx);
    InterlockedExchange64(&s->last_receive_arg, arg);
}

void hb_runtime_set_receive_route_ctx(int64_t route_ctx) {
    InterlockedExchange64(&g_hb_runtime_state.last_receive_route_ctx, route_ctx);
}

void hb_runtime_set_route_desc(int64_t route_desc) {
    InterlockedExchange64(&g_hb_runtime_state.last_route_desc, route_desc);
}

void hb_runtime_set_route_ctx(int64_t route_ctx) {
    InterlockedExchange64(&g_hb_runtime_state.last_route_ctx, route_ctx);
}

void hb_runtime_set_route_mgr_if_present(int64_t route_mgr) {
    if (route_mgr) InterlockedExchange64(&g_hb_runtime_state.last_route_mgr, route_mgr);
}

void hb_runtime_set_route_type(LONG route_type) {
    InterlockedExchange(&g_hb_runtime_state.last_route_type, route_type);
}

int64_t hb_runtime_get_receive_mid_ctx(void) {
    return hb_load64(&g_hb_runtime_state.last_receive_mid_ctx);
}

int64_t hb_runtime_get_receive_mid_payload(void) {
    return hb_load64(&g_hb_runtime_state.last_receive_mid_payload);
}

int64_t hb_runtime_get_receive_parent_ctx(void) {
    return hb_load64(&g_hb_runtime_state.last_receive_parent_ctx);
}

int64_t hb_runtime_get_receive_arg(void) {
    return hb_load64(&g_hb_runtime_state.last_receive_arg);
}

int64_t hb_runtime_get_route_desc(void) {
    return hb_load64(&g_hb_runtime_state.last_route_desc);
}

int64_t hb_runtime_get_route_ctx(void) {
    return hb_load64(&g_hb_runtime_state.last_route_ctx);
}

int64_t hb_runtime_get_route_mgr(void) {
    return hb_load64(&g_hb_runtime_state.last_route_mgr);
}

LONG hb_runtime_get_route_type(void) {
    return hb_load32(&g_hb_runtime_state.last_route_type);
}

void hb_runtime_get_route_snapshot(HbRouteSnapshot *out) {
    HbRuntimeState *s = &g_hb_runtime_state;
    if (!out) return;
    out->receive_route_ctx = hb_load64(&s->last_receive_route_ctx);
    out->receive_mid_ctx = hb_load64(&s->last_receive_mid_ctx);
    out->receive_mid_payload = hb_load64(&s->last_receive_mid_payload);
    out->receive_parent_ctx = hb_load64(&s->last_receive_parent_ctx);
    out->receive_arg = hb_load64(&s->last_receive_arg);
    out->route_desc = hb_load64(&s->last_route_desc);
    out->route_ctx = hb_load64(&s->last_route_ctx);
    out->route_mgr = hb_load64(&s->last_route_mgr);
    out->route_type = hb_load32(&s->last_route_type);
}
