/*
 * Module: hongbao no-click receive experiments and pending-route queue
 */

#include <string.h>

#include "../include/wx_bridge_debug_log.h"
#include "../include/wx_bridge_hongbao_entrypoints.h"
#include "../include/wx_bridge_hongbao_hooks.h"
#include "../include/wx_bridge_hongbao_runtime.h"
#include "wx_bridge_hongbao_runtime_internal.h"
#include "../include/wx_bridge_hongbao_cold.h"
#include "../include/wx_bridge_hongbao_noclick.h"
#include "../include/wx_bridge_hongbao_trace.h"
#include "../include/wx_bridge_memory.h"
#include "../include/wx_bridge_offsets.h"
#include "../include/wx_bridge_paths.h"
#include "../include/wx_bridge_protocol.h"
#include "../include/wx_bridge_runtime.h"
#include "../include/wx_bridge_native_call.h"
#include "../include/wx_bridge_text.h"

typedef int64_t (__fastcall *FnHbReceiveRouteBuilder)(int64_t route_ctx);
typedef int64_t (__fastcall *FnHbRouteCtxFactory)(int64_t out_obj, int64_t route_mgr, int64_t route_desc, int64_t flag);

typedef struct HbNoclickState {
    volatile LONG attempt;
    volatile LONG success;
    volatile LONG skipped;
    volatile LONG in_progress;
    char last_sendid[128];
    int click_state_stub[16];
    volatile LONG64 last_red_send_item;
} HbNoclickState;

static HbNoclickState g_hb_noclick_state = {
    0, 0, 0, 0,
    {0},
    {2},
    0
};

static __declspec(align(16)) BYTE g_hb_pending_route_desc[HB_ROUTE_DESC_SIZE];
static volatile LONG g_hb_pending_route_valid = 0;
static volatile LONG g_hb_pending_route_lock = 0;
static volatile LONG g_hb_pending_route_constructed = 0;
static volatile LONG g_hb_pending_route_hit = 0;
static volatile LONG64 g_hb_pending_route_source = 0;
static volatile LONG64 g_hb_pending_route_tick = 0;
static char g_hb_pending_route_sendid[128] = {0};

static __declspec(align(16)) BYTE g_hb_pending_source[HB_SOURCE_SIZE];
static volatile LONG g_hb_pending_source_valid = 0;
static volatile LONG g_hb_pending_source_lock = 0;
static volatile LONG g_hb_pending_source_hit = 0;
static volatile LONG64 g_hb_pending_source_tick = 0;
static char g_hb_pending_source_sendid[128] = {0};

static int hb_noclick_try_pending_route(const char *reason);

void hb_noclick_remember_red_send_item(int64_t send_item) {
    InterlockedExchange64(&g_hb_noclick_state.last_red_send_item, (LONG64)send_item);
}

static void hb_log_noclick_event(const char *phase, LONG hit, int64_t source,
                                 int64_t route_mgr, int64_t route_desc,
                                 int64_t route_ret, const char *sendid,
                                 const char *detail) {
    char line[16384];
    char *p = line;
    char *end = line + sizeof(line) - 1;
    char num[64];
    append_raw(&p, end, "{\"event\":\"hb_noclick\"");
    append_raw(&p, end, ",\"phase\":\""); append_escaped(&p, end, phase ? phase : "");
    append_raw(&p, end, "\",\"hit\":"); wsprintfA(num, "%ld", (long)hit); append_raw(&p, end, num);
    append_raw(&p, end, ",\"tid\":"); wsprintfA(num, "%lu", (unsigned long)GetCurrentThreadId()); append_raw(&p, end, num);
    append_hex_field(&p, end, "source", source);
    append_hex_field(&p, end, "route_mgr", route_mgr);
    append_hex_field(&p, end, "route_desc", route_desc);
    append_hex_field(&p, end, "route_ret", route_ret);
    append_dec_field(&p, end, "attempt", (int64_t)InterlockedCompareExchange(&g_hb_noclick_state.attempt, 0, 0));
    append_dec_field(&p, end, "success", (int64_t)InterlockedCompareExchange(&g_hb_noclick_state.success, 0, 0));
    append_dec_field(&p, end, "skipped", (int64_t)InterlockedCompareExchange(&g_hb_noclick_state.skipped, 0, 0));
    append_raw(&p, end, ",\"sendId\":\""); append_escaped(&p, end, sendid ? sendid : "");
    append_raw(&p, end, "\",\"lastSendId\":\""); append_escaped(&p, end, g_hb_noclick_state.last_sendid);
    append_raw(&p, end, "\",\"detail\":\""); append_escaped(&p, end, detail ? detail : "");
    append_raw(&p, end, "\"");
    if (route_desc) hb_append_route_desc_fields(&p, end, "desc", route_desc, 1);
    hb_append_recv_context(&p, end);
    append_raw(&p, end, "}");
    *p = 0;
    hb_route_dbg(line);
}

typedef int64_t (__fastcall *FnHbRouteDescFromSource)(int64_t out_desc, int64_t source);
typedef int64_t (__fastcall *FnHbRouteDescCopy)(int64_t dst_desc, int64_t src_desc);
typedef void (__fastcall *FnHbRouteDescDtor)(int64_t route_desc);

static void hb_noclick_free_temp_route_desc(int64_t temp_desc, int constructed) {
    int64_t base = (int64_t)wxb_weixin_module();
    if (!base || !temp_desc) return;
    __try {
        if (constructed) {
            FnHbRouteDescDtor desc_dtor = (FnHbRouteDescDtor)(base + WX_OFF_HB_ROUTE_DESC_DTOR);
            desc_dtor(temp_desc);
        }
        ((FnCall1)(base + WX_OFF_WX_FREE))(temp_desc);
    } __except (EXCEPTION_EXECUTE_HANDLER) {
    }
}

/* SEH fault capture for the receive-route direct call (logs faulting address for IDA mapping) */
static volatile LONG64 g_hb_recv_fault_addr = 0;
static int hb_recv_seh_filter(struct _EXCEPTION_POINTERS *ep) {
    if (ep && ep->ExceptionRecord)
        InterlockedExchange64(&g_hb_recv_fault_addr, (LONG64)(uintptr_t)ep->ExceptionRecord->ExceptionAddress);
    return EXCEPTION_EXECUTE_HANDLER;
}

/*
 * No-click receive trigger (2026-06-06 rewrite).
 *
 * Old path called 184120B60 (route_temp_vector): it built the route_ctx into a throwaway
 * temporary route_mgr that is destroyed on return, so the receive route (185EA6140) was
 * never driven -> receivewxhb never fired (success=1 was a false positive).
 *
 * Current status after IDA/static + live-log comparison:
 *   1. 18527C7E0(&out, route_mgr, route_desc, 0) builds a standalone type-39 route_ctx, but it
 *      is not inserted into route_mgr+0x68 by WeChat's normal 185705820 route-vector path.
 *   2. 185EA6140(route_ctx) only drives the receive route -> 182974A70/183A68BF0 -> receivewxhb.
 *      It does not create the card_route/page_ctx/openwxhb chain seen after a real card click.
 *   3. Worker-thread direct calls can race route_ctx+0x4A8/+0x528 and the global task scheduler,
 *      which matches the observed WeChat UI freeze.
 *
 * Therefore this direct path is disabled unless bridge/control/hb_noclick.direct exists. The
 * default path is capture/log-only while we look for a real route/UI scheduler entry.
 */
static int hb_noclick_route_desc_with_mgr(LONG hit, int64_t source, const char *sendid,
                                          int64_t route_mgr, int64_t route_desc,
                                          const char *reason) {
    int64_t base = (int64_t)wxb_weixin_module();
    FnHbRouteCtxFactory ctx_factory;
    FnHbReceiveRouteBuilder recv_route;
    __declspec(align(16)) int64_t out_holder[2];
    int64_t route_ctx = 0;
    int called = 0;

    if (!hb_noclick_direct_enabled()) {
        InterlockedIncrement(&g_hb_noclick_state.skipped);
        hb_log_noclick_event("skip", hit, source, route_mgr, route_desc, 0, sendid,
                             "direct_disabled");
        return 0;
    }
    if (!route_mgr || !is_readable_ptr(route_mgr, 0x100)) {
        InterlockedIncrement(&g_hb_noclick_state.skipped);
        hb_log_noclick_event("skip", hit, source, route_mgr, route_desc, 0, sendid,
                             reason ? reason : "no_route_mgr");
        return 0;
    }
    if (!base || !route_desc || !is_readable_ptr(route_desc, HB_ROUTE_DESC_SIZE)) {
        InterlockedIncrement(&g_hb_noclick_state.skipped);
        hb_log_noclick_event("skip", hit, source, route_mgr, route_desc, 0, sendid,
                             "bad_route_desc");
        return 0;
    }

    ctx_factory = (FnHbRouteCtxFactory)(base + WX_OFF_HB_ROUTE_CTX_FACTORY);
    recv_route  = (FnHbReceiveRouteBuilder)(base + WX_OFF_HB_RECEIVE_ROUTE_BUILDER);
    out_holder[0] = 0;
    out_holder[1] = 0;

    __try {
        /* 1) build a type-39 route_ctx from our route_desc */
        ctx_factory((int64_t)out_holder, route_mgr, route_desc, 0);
        route_ctx = out_holder[0];
        if (!route_ctx || !is_readable_ptr(route_ctx, 0x540)) {
            InterlockedIncrement(&g_hb_noclick_state.skipped);
            hb_log_noclick_event("skip", hit, source, route_mgr, route_desc, route_ctx, sendid,
                                 "route_ctx_build_failed");
            __leave;
        }
        hb_log_noclick_event("route_ctx", hit, source, route_mgr, route_desc, route_ctx, sendid,
                             "route_ctx_factory_built");

        /* 2) force the controller/delegate slot (route_ctx+0xD0) null so 185EA6140 takes its
              safe no-controller branch. route_mgr (a2 of 18527C7E0) is the route MANAGER, NOT
              the route's controller delegate -- wiring it here made 185EA6140 dispatch
              route_mgr->vtable[+0x110] to the wrong method. The receive build
              (182974A70 -> 184A60500 -> receivewxhb) does not need the controller; the else
              branch builds the default request context. */
        w64((void *)(route_ctx + 0xD0), 0);

        /* 2b) route_ctx+0x518 (= red_envelope_open element[@+0x4E8] +0x30) is the element's
               page/click-state back-pointer. 185EA6140's FIRST action is an UNCONDITIONAL vcall to
               that element's telemetry method sub_185EA6E90 ("element_click"), which derefs
               *(route_ctx+0x518) twice (dword[0]/dword[4]) for click classification. The factory
               leaves it NULL on a no-click route -> AV at RVA 0x5EA6F5A (the receive_route_exception
               we kept hitting; the crash is at func ENTRY, before the +0xD0 branch -- so the old
               "+0xD0 null avoids it" note was wrong). It is pure telemetry (report sink is a static
               singleton), so a readable >=8B stub suffices to reach receivewxhb. It is not proof of
               a successful open; successful open requires card_route/pagectx/openwxhb or equivalent. */
        w64((void *)(route_ctx + 0x518), (int64_t)g_hb_noclick_state.click_state_stub);

        /* 2c) route_ctx is the case-39 controller shell. Copying the retained red-packet send_item
               into controller+0x120 gives 185EA6140 enough data to build receivewxhb parameters.
               It still does not bind a live page_ctx or schedule openwxhb; logs must not treat
               receive_route_builder_returned as a successful grab.
               red_si 来自 DoAddMsg 用 sub_1809D8F80 转换+retain 的真实 send_item。 */
        {
            int64_t red_si = (int64_t)InterlockedCompareExchange64(&g_hb_noclick_state.last_red_send_item, 0, 0);
            if (red_si && is_readable_ptr(red_si, 0x98) &&
                r64((void *)red_si) == base + WX_OFF_SEND_ITEM_VT) {
                __try {
                    ((FnCall3)(base + WX_OFF_SEND_ITEM_COPY))(route_ctx + WX_HB_CONTROLLER_SENDITEM_OFF, red_si);
                    hb_log_noclick_event("send_item", hit, source, route_mgr, route_desc, route_ctx,
                                         sendid, "filled_controller_senditem");
                } __except (EXCEPTION_EXECUTE_HANDLER) {
                    hb_log_noclick_event("skip", hit, source, route_mgr, route_desc, route_ctx,
                                         sendid, "senditem_copy_exception");
                }
            } else {
                hb_log_noclick_event("skip", hit, source, route_mgr, route_desc, route_ctx,
                                     sendid, "no_retained_red_si");
            }
        }

        /* 3) drive the receive route; SEH-guarded so a fault logs its address */
        InterlockedExchange64(&g_hb_recv_fault_addr, 0);
        hb_log_noclick_event("receive_call", hit, source, route_mgr, route_desc, route_ctx, sendid,
                             "receive_route_builder");
        __try {
            recv_route(route_ctx);
            InterlockedIncrement(&g_hb_noclick_state.success);
            if (sendid && sendid[0]) safe_copy(g_hb_noclick_state.last_sendid, sizeof(g_hb_noclick_state.last_sendid), sendid);
            hb_log_noclick_event("done", hit, source, route_mgr, route_desc, route_ctx, sendid,
                                 "receive_route_builder_returned");
            if (hb_noclick_one_shot_enabled()) DeleteFileW(HB_NOCLICK_FILE);
            called = 1;
        } __except (hb_recv_seh_filter(GetExceptionInformation())) {
            InterlockedIncrement(&g_hb_noclick_state.skipped);
            hb_log_noclick_event("skip", hit, source, route_mgr, route_desc,
                                 (int64_t)InterlockedCompareExchange64(&g_hb_recv_fault_addr, 0, 0),
                                 sendid, "receive_route_exception");
        }
        /* NOTE: out_holder (route_ctx shared_ptr) intentionally NOT released during validation,
           to avoid use-after-free if the scheduled async receive task references it.
           Bounded leak: one 0x540 type-39 route_ctx per attempt. */
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        InterlockedIncrement(&g_hb_noclick_state.skipped);
        hb_log_noclick_event("skip", hit, source, route_mgr, route_desc, route_ctx, sendid,
                             "route_ctx_build_exception");
    }
    return called;
}

static int hb_noclick_store_pending_route(LONG hit, int64_t source, const char *sendid,
                                          int64_t route_desc) {
    FnHbRouteDescCopy desc_copy = (FnHbRouteDescCopy)((int64_t)wxb_weixin_module() + WX_OFF_SEND_ITEM_COPY);
    FnHbRouteDescDtor desc_dtor = (FnHbRouteDescDtor)((int64_t)wxb_weixin_module() + WX_OFF_HB_ROUTE_DESC_DTOR);
    int queued = 0;

    if (!route_desc || !is_readable_ptr(route_desc, HB_ROUTE_DESC_SIZE)) {
        InterlockedIncrement(&g_hb_noclick_state.skipped);
        hb_log_noclick_event("skip", hit, source, 0, route_desc, 0, sendid, "bad_route_desc_for_queue");
        return 0;
    }
    if (sendid && sendid[0] && g_hb_noclick_state.last_sendid[0] && str_eq(sendid, g_hb_noclick_state.last_sendid)) {
        InterlockedIncrement(&g_hb_noclick_state.skipped);
        hb_log_noclick_event("skip", hit, source, 0, route_desc, 0, sendid, "duplicate_sendid");
        return 0;
    }
    if (InterlockedCompareExchange(&g_hb_pending_route_lock, 1, 0) != 0) {
        InterlockedIncrement(&g_hb_noclick_state.skipped);
        hb_log_noclick_event("skip", hit, source, 0, route_desc, 0, sendid, "pending_route_locked");
        return 0;
    }

    __try {
        if (InterlockedCompareExchange(&g_hb_pending_route_valid, 0, 0) == 1) {
            if (sendid && sendid[0] && g_hb_pending_route_sendid[0] &&
                str_eq(sendid, g_hb_pending_route_sendid)) {
                InterlockedIncrement(&g_hb_noclick_state.skipped);
                hb_log_noclick_event("skip", hit, source, 0, route_desc, 0, sendid,
                                     "pending_route_duplicate");
                __leave;
            }
            InterlockedExchange(&g_hb_pending_route_valid, 0);
        }
        if (InterlockedCompareExchange(&g_hb_pending_route_constructed, 0, 0) == 1) {
            desc_dtor((int64_t)g_hb_pending_route_desc);
            InterlockedExchange(&g_hb_pending_route_constructed, 0);
        }
        memset(g_hb_pending_route_desc, 0, sizeof(g_hb_pending_route_desc));
        desc_copy((int64_t)g_hb_pending_route_desc, route_desc);
        safe_copy(g_hb_pending_route_sendid, sizeof(g_hb_pending_route_sendid), sendid ? sendid : "");
        InterlockedExchange(&g_hb_pending_route_hit, hit);
        InterlockedExchange64(&g_hb_pending_route_source, source);
        InterlockedExchange64(&g_hb_pending_route_tick, (LONG64)GetTickCount64());
        InterlockedExchange(&g_hb_pending_route_constructed, 1);
        InterlockedExchange(&g_hb_pending_route_valid, 1);
        hb_log_noclick_event("pending", hit, source, 0, (int64_t)g_hb_pending_route_desc, 0, sendid,
                             "pending_worker_route_desc");
        queued = 1;
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        InterlockedExchange(&g_hb_pending_route_valid, 0);
        InterlockedIncrement(&g_hb_noclick_state.skipped);
        hb_log_noclick_event("skip", hit, source, 0, route_desc, 0, sendid, "pending_route_copy_exception");
        InterlockedExchange(&g_hb_pending_route_constructed, 0);
    }
    InterlockedExchange(&g_hb_pending_route_lock, 0);
    return queued;
}

static int hb_noclick_try_pending_route(const char *reason) {
    FnHbRouteDescDtor desc_dtor = (FnHbRouteDescDtor)((int64_t)wxb_weixin_module() + WX_OFF_HB_ROUTE_DESC_DTOR);
    int64_t route_mgr = (int64_t)hb_runtime_get_route_mgr();
    int64_t source = 0;
    LONG hit = 0;
    char sendid[128] = {0};
    int called = 0;

    if (!hb_noclick_enabled()) return 0;
    if (!route_mgr || !is_readable_ptr(route_mgr, 0x100)) return 0;
    if (!hb_noclick_direct_enabled()) return 0;
    if (InterlockedCompareExchange(&g_hb_pending_route_lock, 1, 0) != 0) return 0;
    if (InterlockedCompareExchange(&g_hb_pending_route_valid, 0, 0) != 1) {
        InterlockedExchange(&g_hb_pending_route_lock, 0);
        return 0;
    }
    if (InterlockedCompareExchange(&g_hb_noclick_state.in_progress, 1, 0) != 0) {
        InterlockedExchange(&g_hb_pending_route_lock, 0);
        return 0;
    }

    source = (int64_t)InterlockedCompareExchange64(&g_hb_pending_route_source, 0, 0);
    hit = InterlockedCompareExchange(&g_hb_pending_route_hit, 0, 0);
    safe_copy(sendid, sizeof(sendid), g_hb_pending_route_sendid);
    InterlockedExchange(&g_hb_pending_route_valid, 0);
    InterlockedIncrement(&g_hb_noclick_state.attempt);
    hb_log_noclick_event("worker", hit, source, route_mgr, (int64_t)g_hb_pending_route_desc,
                         0, sendid, reason ? reason : "pending_route_dequeue");
    __try {
        called = hb_noclick_route_desc_with_mgr(hit, source, sendid, route_mgr,
                                                (int64_t)g_hb_pending_route_desc,
                                                reason ? reason : "pending_route_mgr_seen");
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        InterlockedIncrement(&g_hb_noclick_state.skipped);
        hb_log_noclick_event("skip", hit, source, route_mgr, (int64_t)g_hb_pending_route_desc,
                             0, sendid, "pending_route_exception");
    }

    if (called) {
        /* 185EA6140 schedules async receive work. Keep the route_desc alive because
           the route_ctx built from it appears to retain shallow refs into the desc payload. It is
           released when the next pending route overwrites it, or when the process exits. */
        hb_log_noclick_event("pending", hit, source, route_mgr, (int64_t)g_hb_pending_route_desc,
                             0, sendid, "pending_route_retained_after_receive");
    } else {
        __try {
            if (InterlockedCompareExchange(&g_hb_pending_route_constructed, 0, 0) == 1) {
                desc_dtor((int64_t)g_hb_pending_route_desc);
                InterlockedExchange(&g_hb_pending_route_constructed, 0);
            }
        } __except (EXCEPTION_EXECUTE_HANDLER) {
            hb_log_noclick_event("skip", hit, source, route_mgr, (int64_t)g_hb_pending_route_desc,
                                 0, sendid, "pending_route_dtor_exception");
        }
        ZeroMemory(g_hb_pending_route_desc, sizeof(g_hb_pending_route_desc));
        g_hb_pending_route_sendid[0] = 0;
        InterlockedExchange64(&g_hb_pending_route_source, 0);
        InterlockedExchange64(&g_hb_pending_route_tick, 0);
        InterlockedExchange(&g_hb_pending_route_hit, 0);
    }
    InterlockedExchange(&g_hb_noclick_state.in_progress, 0);
    InterlockedExchange(&g_hb_pending_route_lock, 0);
    return called;
}

static int hb_noclick_route_source(LONG hit, int64_t source, const char *sendid) {
    FnHbRouteDescFromSource build_desc;
    FnHbRouteDescDtor desc_dtor;
    __declspec(align(16)) BYTE route_desc_buf[HB_ROUTE_DESC_SIZE];
    int64_t route_mgr = (int64_t)hb_runtime_get_route_mgr();
    int32_t msg_type = 0;
    int32_t subtype = 0;
    int64_t payload_obj = 0;
    int built_desc = 0;
    int called_receive = 0;

    if (!hb_noclick_enabled()) return 0;
    if (!source || !is_readable_ptr(source, 0x290)) {
        InterlockedIncrement(&g_hb_noclick_state.skipped);
        hb_log_noclick_event("skip", hit, source, route_mgr, 0, 0, sendid, "bad_source");
        return 0;
    }
    if (sendid && sendid[0] && g_hb_noclick_state.last_sendid[0] && str_eq(sendid, g_hb_noclick_state.last_sendid)) {
        InterlockedIncrement(&g_hb_noclick_state.skipped);
        hb_log_noclick_event("skip", hit, source, route_mgr, 0, 0, sendid, "duplicate_sendid");
        return 0;
    }
    if (InterlockedCompareExchange(&g_hb_noclick_state.in_progress, 1, 0) != 0) {
        InterlockedIncrement(&g_hb_noclick_state.skipped);
        hb_log_noclick_event("skip", hit, source, route_mgr, 0, 0, sendid, "busy");
        return 0;
    }

    InterlockedIncrement(&g_hb_noclick_state.attempt);
    build_desc = (FnHbRouteDescFromSource)((int64_t)wxb_weixin_module() + WX_OFF_HB_ROUTE_DESC_FROM_SOURCE);
    desc_dtor = (FnHbRouteDescDtor)((int64_t)wxb_weixin_module() + WX_OFF_HB_ROUTE_DESC_DTOR);
    memset(route_desc_buf, 0xAA, sizeof(route_desc_buf));

    __try {
        build_desc((int64_t)route_desc_buf, source);
        built_desc = 1;
        msg_type = r32(route_desc_buf + 0x08);
        subtype = r32(route_desc_buf + 0x0C);
        payload_obj = r64(route_desc_buf + 0x270);
        if (msg_type == 49 && subtype == 2001 && payload_obj) {
            if (!route_mgr || !is_readable_ptr(route_mgr, 0x100)) {
                InterlockedIncrement(&g_hb_noclick_state.skipped);
                hb_log_noclick_event("skip", hit, source, route_mgr, (int64_t)route_desc_buf, 0,
                                     sendid, "no_route_mgr");
            } else {
                called_receive = hb_noclick_route_desc_with_mgr(hit, source, sendid, route_mgr,
                                                                (int64_t)route_desc_buf,
                                                                "route_ctx_factory");
            }
        } else {
            InterlockedIncrement(&g_hb_noclick_state.skipped);
            hb_log_noclick_event("skip", hit, source, route_mgr, (int64_t)route_desc_buf, 0, sendid, "route_desc_not_hongbao");
        }
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        InterlockedIncrement(&g_hb_noclick_state.skipped);
        hb_log_noclick_event("skip", hit, source, route_mgr, built_desc ? (int64_t)route_desc_buf : 0,
                             0, sendid, "exception");
    }
    if (built_desc) {
        __try {
            desc_dtor((int64_t)route_desc_buf);
        } __except (EXCEPTION_EXECUTE_HANDLER) {
            hb_log_noclick_event("skip", hit, source, route_mgr, (int64_t)route_desc_buf, 0, sendid, "route_desc_dtor_exception");
        }
    }
    InterlockedExchange(&g_hb_noclick_state.in_progress, 0);
    return called_receive;
}

static void hb_noclick_clear_pending_source_locked(int destroy_constructed,
                                                   LONG hit,
                                                   const char *sendid,
                                                   const char *reason) {
    int64_t base = (int64_t)wxb_weixin_module();
    int dtor_failed = 0;

    if (destroy_constructed && base) {
        __try {
            ((FnCall1)(base + WX_OFF_MODEL_DTOR))((int64_t)g_hb_pending_source);
        } __except (EXCEPTION_EXECUTE_HANDLER) {
            dtor_failed = 1;
        }
    }
    if (dtor_failed) {
        hb_log_noclick_event("skip", hit, (int64_t)g_hb_pending_source, 0, 0, 0,
                             sendid, "pending_source_dtor_exception");
    } else if (reason && reason[0]) {
        hb_log_noclick_event("pending", hit, (int64_t)g_hb_pending_source, 0, 0, 0,
                             sendid, reason);
    }
    ZeroMemory(g_hb_pending_source, sizeof(g_hb_pending_source));
    g_hb_pending_source_sendid[0] = 0;
    InterlockedExchange(&g_hb_pending_source_hit, 0);
    InterlockedExchange64(&g_hb_pending_source_tick, 0);
    InterlockedExchange(&g_hb_pending_source_valid, 0);
}

static int hb_noclick_queue_source(LONG hit, int64_t source, const char *sendid) {
    int64_t base = (int64_t)wxb_weixin_module();
    FnHbRouteDescFromSource build_desc;
    FnHbRouteDescDtor desc_dtor;
    __declspec(align(16)) BYTE route_desc_buf[HB_ROUTE_DESC_SIZE];
    int32_t msg_type = 0;
    int32_t subtype = 0;
    int64_t payload_obj = 0;
    int built_desc = 0;
    int queued = 0;

    if (!hb_noclick_enabled()) return 0;
    if (!base || !source || !is_readable_ptr(source, HB_SOURCE_SIZE)) {
        InterlockedIncrement(&g_hb_noclick_state.skipped);
        hb_log_noclick_event("skip", hit, source, 0, 0, 0, sendid, "bad_source_for_queue");
        return 0;
    }
    if (sendid && sendid[0] && g_hb_noclick_state.last_sendid[0] && str_eq(sendid, g_hb_noclick_state.last_sendid)) {
        InterlockedIncrement(&g_hb_noclick_state.skipped);
        hb_log_noclick_event("skip", hit, source, 0, 0, 0, sendid, "duplicate_sendid");
        return 0;
    }

    build_desc = (FnHbRouteDescFromSource)(base + WX_OFF_HB_ROUTE_DESC_FROM_SOURCE);
    desc_dtor = (FnHbRouteDescDtor)(base + WX_OFF_HB_ROUTE_DESC_DTOR);
    memset(route_desc_buf, 0xAA, sizeof(route_desc_buf));
    __try {
        build_desc((int64_t)route_desc_buf, source);
        built_desc = 1;
        msg_type = r32(route_desc_buf + 0x08);
        subtype = r32(route_desc_buf + 0x0C);
        payload_obj = r64(route_desc_buf + 0x270);
        if (msg_type == 49 && subtype == 2001 && payload_obj) {
            queued = hb_noclick_store_pending_route(hit, source, sendid, (int64_t)route_desc_buf);
        } else {
            InterlockedIncrement(&g_hb_noclick_state.skipped);
            hb_log_noclick_event("skip", hit, source, 0, (int64_t)route_desc_buf, 0,
                                 sendid, "route_desc_not_hongbao");
        }
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        InterlockedIncrement(&g_hb_noclick_state.skipped);
        hb_log_noclick_event("skip", hit, source, 0, built_desc ? (int64_t)route_desc_buf : 0,
                             0, sendid, "route_desc_build_exception");
    }
    if (built_desc) {
        __try {
            desc_dtor((int64_t)route_desc_buf);
        } __except (EXCEPTION_EXECUTE_HANDLER) {
            hb_log_noclick_event("skip", hit, source, 0, (int64_t)route_desc_buf, 0,
                                 sendid, "route_desc_dtor_exception");
        }
    }
    return queued;
}

void poll_hb_noclick_pending(void) {
    int64_t route_mgr = 0;
    LONG hit = 0;
    LONG64 tick = 0;
    char sendid[128] = {0};

    if (!hb_noclick_enabled()) return;
    if (InterlockedCompareExchange(&g_hb_pending_route_valid, 0, 0) != 1) return;
    if (InterlockedCompareExchange(&g_hb_noclick_state.in_progress, 0, 0) != 0) return;
    if (!hb_noclick_direct_enabled()) {
        static char s_direct_disabled_last_sendid[128] = {0};
        if (InterlockedCompareExchange(&g_hb_pending_route_lock, 1, 0) == 0) {
            int64_t source = (int64_t)InterlockedCompareExchange64(&g_hb_pending_route_source, 0, 0);
            hit = InterlockedCompareExchange(&g_hb_pending_route_hit, 0, 0);
            safe_copy(sendid, sizeof(sendid), g_hb_pending_route_sendid);
            if (!sendid[0] || !str_eq(sendid, s_direct_disabled_last_sendid)) {
                hb_log_noclick_event("pending", hit, source, 0, (int64_t)g_hb_pending_route_desc,
                                     0, sendid, "direct_disabled_pending_capture_only");
                safe_copy(s_direct_disabled_last_sendid, sizeof(s_direct_disabled_last_sendid), sendid);
            }
            InterlockedExchange(&g_hb_pending_route_lock, 0);
        }
        return;
    }

    route_mgr = (int64_t)hb_runtime_get_route_mgr();
    if (!route_mgr || !is_readable_ptr(route_mgr, 0x100)) {
        tick = InterlockedCompareExchange64(&g_hb_pending_route_tick, 0, 0);
        if (tick && GetTickCount64() - (ULONGLONG)tick > 10000ULL &&
            InterlockedCompareExchange(&g_hb_pending_route_lock, 1, 0) == 0) {
            if (InterlockedCompareExchange(&g_hb_pending_route_valid, 0, 0) == 1) {
                FnHbRouteDescDtor desc_dtor = (FnHbRouteDescDtor)((int64_t)wxb_weixin_module() + WX_OFF_HB_ROUTE_DESC_DTOR);
                int64_t source = (int64_t)InterlockedCompareExchange64(&g_hb_pending_route_source, 0, 0);
                hit = InterlockedCompareExchange(&g_hb_pending_route_hit, 0, 0);
                safe_copy(sendid, sizeof(sendid), g_hb_pending_route_sendid);
                InterlockedIncrement(&g_hb_noclick_state.skipped);
                hb_log_noclick_event("skip", hit, source, 0, (int64_t)g_hb_pending_route_desc,
                                     0, sendid, "pending_route_no_route_mgr_timeout");
                __try {
                    desc_dtor((int64_t)g_hb_pending_route_desc);
                } __except (EXCEPTION_EXECUTE_HANDLER) {
                    hb_log_noclick_event("skip", hit, source, 0, (int64_t)g_hb_pending_route_desc,
                                         0, sendid, "pending_route_timeout_dtor_exception");
                }
                ZeroMemory(g_hb_pending_route_desc, sizeof(g_hb_pending_route_desc));
                g_hb_pending_route_sendid[0] = 0;
                InterlockedExchange64(&g_hb_pending_route_source, 0);
                InterlockedExchange64(&g_hb_pending_route_tick, 0);
                InterlockedExchange(&g_hb_pending_route_hit, 0);
                InterlockedExchange(&g_hb_pending_route_valid, 0);
            }
            InterlockedExchange(&g_hb_pending_route_lock, 0);
        }
        return;
    }

    hb_noclick_try_pending_route("pending_route_dequeue");
}

int hb_noclick_scan_source_vector(LONG hit, int64_t a1, int64_t vector_arg) {
    int64_t begin = 0;
    int64_t endp = 0;
    int64_t delta = 0;
    int count = 0;
    int triggered = 0;
    char dbg[512];
    (void)a1;

    if (!hb_noclick_enabled()) return 0;
    if (!vector_arg || !is_readable_ptr(vector_arg, 0x18)) return 0;
    begin = r64((void *)vector_arg);
    endp = r64((void *)(vector_arg + 8));
    if (!begin || !endp || endp <= begin) return 0;
    delta = endp - begin;
    if (delta <= 0 || delta > 0x290 * 256 || (delta % 0x290) != 0) {
        wsprintfA(dbg, "[hb-noclick] skip source vector hit=%ld vector=%I64x begin=%I64x end=%I64x delta=%I64x",
                  (long)hit, vector_arg, begin, endp, delta);
        hb_route_dbg(dbg);
        return 0;
    }

    count = (int)(delta / 0x290);
    if (count > 256) count = 256;
    for (int i = 0; i < count; i++) {
        char sendid[128] = {0};
        char native_url[2048] = {0};
        char page_url[2048] = {0};
        char peer[256] = {0};
        char sender[256] = {0};
        char snippet[512] = {0};
        char previous_sendid[128] = {0};
        int64_t source = begin + (int64_t)i * 0x290;
        if (!hb_source_extract_hongbao_context(source,
                                               sendid,
                                               sizeof(sendid),
                                               native_url,
                                               sizeof(native_url),
                                               page_url,
                                               sizeof(page_url),
                                               peer,
                                               sizeof(peer),
                                               sender,
                                               sizeof(sender),
                                               snippet,
                                               sizeof(snippet))) continue;
        hb_runtime_copy_recv_sendid(previous_sendid, sizeof(previous_sendid));
        hb_runtime_record_receive(sendid,
                                  0,
                                  native_url,
                                  page_url,
                                  peer,
                                  sender,
                                  source,
                                  0,
                                  0,
                                  0,
                                  0,
                                  !previous_sendid[0] || !str_eq(previous_sendid, sendid),
                                  0);
        if (hb_noclick_queue_source(hit, source, sendid)) triggered = 1;
    }
    return triggered;
}

typedef int64_t (__fastcall *FnHbSourceSubmit)(int64_t a1, int64_t vector_arg, int64_t a3, int64_t a4);

int64_t __fastcall hb_source_submit_hook(int64_t a1, int64_t vector_arg, int64_t a3, int64_t a4) {
    LONG hit = hb_hook_record_hit(HB_HOOK_SOURCE_SUBMIT);
    int64_t ret = 0;
    /*
     * Frida parity: build parent (Fill + ServiceRecv) in onEnter BEFORE original,
     * then Launch_receive AFTER original returns (setTimeout(0) equivalent).
     * Build is safe pre-original (pure data transform); only Launch must be
     * deferred — launching inside the RECV body correlates with a 2s stall.
     * since_launch_ms should be ~170ms (pure 1581 CGI RTT) once build cost is
     * paid before the 2s WeChat body.
     */
    ULONGLONG t0 = GetTickCount64();
    hb_cold_scan_source_vector(hit, a1, vector_arg);          /* onEnter: Fill + Service → build parent */
    ULONGLONG t1 = GetTickCount64();
    ret = hb_call_source_submit_original(a1, vector_arg, a3, a4); /* original RECV body (AddMessageListToDB) */
    ULONGLONG t2 = GetTickCount64();
    hb_cold_flush_launches();                                 /* fallback launch (mostly no-op now) */
    ULONGLONG t3 = GetTickCount64();
    if (hb_cold_enabled() && (t3 - t0) > 100) {
        char dbg[192];
        wsprintfA(dbg, "[submit-time] tid=%lu scan=%lums orig(AddMessageListToDB)=%lums flush=%lums total=%lums\r\n",
                  (unsigned long)GetCurrentThreadId(),
                  (unsigned long)(t1 - t0), (unsigned long)(t2 - t1),
                  (unsigned long)(t3 - t2), (unsigned long)(t3 - t0));
        hb_dbg(dbg);
    }
    return ret;
}
