/*
 * Module: cold red-packet claim (冷抢)
 *
 * Port of RE/frida_cold_hongbao_auto.py (MODE=cold, OPEN=true).
 * Pipeline:
 *   LocalMsg vector → FillFromLocalMsg → Service_StartReceive/Factory_receive
 *     → deferred Launch_receive (0x4A64BB0) → 1581
 *   ParseExtra → timing@+0x188 → Service_StartOpen/Factory_open
 *     → deferred Launch_open (0x4A6C0A0) → 1685
 *
 * Stability rules (live-proven):
 *   - Defer Launch (and all open build) off RECV/ParseExtra stacks.
 *   - Never hook SendCgi / CoCgi in cold mode.
 *   - Per-claim pin bag for msgCtx/out/a2/timing heap buffers.
 *   - Safety: writeStdString parent+0x390 if Factory left timing empty.
 *   - Gate: appExt@msgCtx+0x270 must be heap; HB-RES status prefer "ok".
 */

#include <stdarg.h>
#include <string.h>

#include "../include/wx_bridge_debug_log.h"
#include "../include/wx_bridge_file_io.h"
#include "../include/wx_bridge_hongbao_cold.h"
#include "../include/wx_bridge_hongbao_trace.h"
#include "../include/wx_bridge_memory.h"
#include "../include/wx_bridge_native_call.h"
#include "../include/wx_bridge_offsets.h"
#include "../include/wx_bridge_paths.h"
#include "../include/wx_bridge_runtime.h"
#include "../include/wx_bridge_realtime_relay.h"
#include "../include/wx_bridge_text.h"
#include "../include/wx_bridge_wechat_string.h"

#define COLD_CLAIM_SLOTS   32
/* Slot may be reused after this; pin *memory* is intentionally leaked (see release). */
#define COLD_PIN_TTL_MS    300000ULL
/*
 * Frida parity (measured ~200ms 1581 RTT on same host; native was ~2100ms):
 *   Frida builds parent in RECV onEnter, Launch_receive via setTimeout(0)
 *   AFTER the intercepted source_submit returns. Launch before original RECV
 *   correlated with a stable ~2.0s stall — keep build onEnter, Launch on flush
 *   after original (or pool if delay_ms>0 / open path).
 */
#define COLD_OPEN_DELAY_MS 0
/* Only controls when the claim *slot* can be reused — not when WeChat stops reading pins. */
#define COLD_RELEASE_RECV_MS 120000ULL
#define COLD_RELEASE_OPEN_MS 180000ULL
#define COLD_RESOLVE_RETRY_MS 5000ULL
#define COLD_JOB_STOP_WAIT_MS 5000
#define COLD_NEWSYNC_RAW_SIZE 0x78
#define COLD_NEWSYNC_MAX_ITEMS 256
#define COLD_NEWSYNC_PATCH_LEN 15
#define COLD_RECV_TRACE_HOOKS 3
#define COLD_RECV_TRACE_MAX_PATCH 24
#define COLD_RECV_TRACE_MATCH_MS 15000ULL
#define COLD_RECV_REQUEST_RETURN_RVA 0x4A65D73

typedef int64_t (__fastcall *FnFillFromLocalMsg)(int64_t src_local, int64_t dst_ctx);
typedef int64_t (__fastcall *FnServiceRecv)(int64_t service, int64_t out, int64_t msg_ctx);
typedef int64_t (__fastcall *FnServiceOpen)(int64_t service, int64_t out, int64_t msg_ctx, int64_t timing);
typedef int64_t (__fastcall *FnFactoryRecv)(int64_t cgi, int64_t out, int64_t msg_ctx);
typedef int64_t (__fastcall *FnFactoryOpen)(int64_t cgi, int64_t out, int64_t msg_ctx, int64_t timing);
typedef int64_t (__fastcall *FnLaunch)(int64_t parent, int64_t a2);
typedef int64_t (__fastcall *FnGetLocator)(void);
typedef int64_t (__fastcall *FnResolveByType)(int64_t resolver, int64_t out_sp, int64_t type_key);
typedef char (__fastcall *FnParseExtra)(int64_t obj, BYTE *json);
typedef int64_t (__fastcall *FnVcall2)(int64_t self, int64_t out);
typedef int64_t (__fastcall *FnNewSyncAddMsgBatch)(int64_t *vector, BYTE *ok,
                                                   char flag1, char flag2);
typedef int64_t (__fastcall *FnRawToLocalMsg)(int64_t local_msg, int64_t raw_addmsg);
typedef void (__fastcall *FnLocalMsgFinalize)(int64_t local_msg, char force);
typedef void (__fastcall *FnLocalMsgDtor)(int64_t local_msg);
typedef int64_t (__fastcall *FnReceiveTask)(int64_t receive_ctx);
typedef int64_t (__fastcall *FnReceiveRequestLoop)(int64_t result, int64_t a2,
                                                   int64_t a3, int a4,
                                                   int64_t a5, int64_t a6,
                                                   int *config);
typedef int64_t (__fastcall *FnReceiveAttempt)(int64_t result, int64_t request,
                                               int64_t task);

typedef struct ColdReceiveTraceHook {
    const char *name;
    DWORD rva;
    int patch_len;
    BYTE expected[COLD_RECV_TRACE_MAX_PATCH];
    BYTE original[COLD_RECV_TRACE_MAX_PATCH];
    void *trampoline;
    void *hook_proc;
    volatile LONG installed;
} ColdReceiveTraceHook;

typedef struct ColdReceiveTraceScope {
    char sendid[128];
    int64_t receive_ctx;
    ULONGLONG claimed_tick;
    ULONGLONG launch_begin_tick;
    ULONGLONG task_begin_tick;
    DWORD launch_tid;
    DWORD task_tid;
    LONG attempt_count;
    LONG request_depth;
    int64_t request_result;
} ColdReceiveTraceScope;

typedef enum ColdPhase {
    COLD_PHASE_FREE = 0,
    COLD_PHASE_BUILT,
    COLD_PHASE_RECV_LAUNCHING,
    COLD_PHASE_RECV_LAUNCHED,
    COLD_PHASE_PENDING_OPEN,
    COLD_PHASE_OPENING,
    COLD_PHASE_OPEN_LAUNCHED,
    COLD_PHASE_DONE
} ColdPhase;

typedef struct ColdClaim {
    volatile LONG in_use;
    volatile LONG phase;
    volatile LONG generation;
    volatile LONG refs;
    char sendid[128];
    char from[128];
    char to[128];
    char talker[128];
    char account[128];
    BYTE *msgctx;              /* 0x400 pin */
    BYTE *recv_out;            /* receive 0x60 pin */
    BYTE *recv_a2;             /* receive 0x100 pin */
    BYTE *open_out;            /* open 0x60 pin */
    BYTE *open_a2;             /* open 0x100 pin */
    BYTE *timing_hdr;          /* 0x20 std::string header pin */
    BYTE *timing_arg_data;     /* Service_StartOpen input string data */
    BYTE *timing_parent_data;  /* parent+timing replacement data */
    int64_t parent_recv;
    int64_t parent_open;
    char timing[48];
    ULONGLONG claimed_tick;
    ULONGLONG launch_begin_tick;
    ULONGLONG launch_tick;   /* when Launch_receive returned */
    ULONGLONG opened_tick;
    ULONGLONG expire_after_tick;
    ULONGLONG release_after_tick;
    volatile LONG64 receive_task_ctx;
    DWORD launch_thread_id;
} ColdClaim;

typedef struct ColdDeferredJob {
    int kind; /* 1=launch_recv, 2=open_build */
    int claim_index;
    LONG claim_generation;
    LONG64 epoch;
    char sendid[128];
    char timing[48];
    ULONGLONG due_tick;
} ColdDeferredJob;

static ColdClaim g_claims[COLD_CLAIM_SLOTS];
static SRWLOCK g_claim_lock = SRWLOCK_INIT;
static volatile LONG g_claim_generation_seq = 0;
static CRITICAL_SECTION g_job_cs;
static volatile LONG g_job_cs_ready = 0;
static volatile LONG g_job_init_lock = 0;
static HANDLE g_job_cancel_event = NULL;
static HANDLE g_job_idle_event = NULL;
static volatile LONG g_job_count = 0;
static volatile LONG g_job_accepting = 0;
static volatile LONG g_worker_started = 0;
static volatile LONG64 g_job_epoch = 1;
static ULONGLONG g_tick0 = 0;

static volatile LONG64 g_hb_service = 0;
static volatile LONG64 g_cgi_table = 0;
static char g_cgi_src[32] = {0};
static ULONGLONG g_resolve_fail_at = 0;
static volatile LONG g_verbose_log = 0; /* 1 = full step logs (slower; disk IO) */
static volatile LONG g_cold_enabled = 1;
static volatile LONG g_cold_open_enabled = 1;
static volatile LONG g_cold_fastlane_enabled = 0;

/* Claim delay (ms): applied before Launch_receive only. min==max => fixed; max>min => uniform range. */
static volatile LONG g_delay_min_ms = 0;
static volatile LONG g_delay_max_ms = 0;
static volatile LONG g_delay_cfg_loaded = 0;

/* ParseExtra hook state (private; not in HB_HOOK_COUNT). */
static void *g_parse_extra_tramp = NULL;
static BYTE g_parse_extra_orig[24];
static int g_parse_extra_orig_len = 0;
static volatile LONG g_parse_extra_installed = 0;
static volatile LONG g_parse_extra_hit = 0;
static SRWLOCK g_parse_extra_lock = SRWLOCK_INIT;

/* newsync AddMsg batch hook (private; default-off experiment). */
static void *g_newsync_addmsg_tramp = NULL;
static BYTE g_newsync_addmsg_orig[COLD_NEWSYNC_PATCH_LEN];
static volatile LONG g_newsync_addmsg_installed = 0;
static volatile LONG g_newsync_addmsg_hit = 0;
static SRWLOCK g_newsync_addmsg_lock = SRWLOCK_INIT;

/* receivewxhb task/request/attempt timing hooks (fastlane experiment only). */
static ColdReceiveTraceHook g_recv_trace_hooks[COLD_RECV_TRACE_HOOKS] = {
    {
        "task", WX_OFF_HB_RECEIVEWXHB_ENTRY, 19,
        {0x55,0x41,0x57,0x41,0x56,0x41,0x55,0x41,0x54,0x56,
         0x57,0x53,0x48,0x81,0xEC,0xD8,0x06,0x00,0x00},
        {0}, NULL, NULL, 0
    },
    {
        "request-loop", WX_OFF_HB_RECEIVE_REQUEST_LOOP, 19,
        {0x55,0x41,0x57,0x41,0x56,0x41,0x55,0x41,0x54,0x56,
         0x57,0x53,0x48,0x81,0xEC,0x88,0x01,0x00,0x00},
        {0}, NULL, NULL, 0
    },
    {
        "attempt", WX_OFF_HB_RECEIVE_ATTEMPT, 17,
        {0x55,0x41,0x57,0x41,0x56,0x41,0x54,0x56,0x57,0x53,
         0x48,0x81,0xEC,0x80,0x01,0x00,0x00},
        {0}, NULL, NULL, 0
    }
};
static SRWLOCK g_recv_trace_lock = SRWLOCK_INIT;
static DWORD g_recv_trace_tls = TLS_OUT_OF_INDEXES;
static ColdReceiveTraceHook g_open_result_hook = {
    "open-result", WX_OFF_HB_PARSE_OPEN_RESULT, 12,
    {0x55,0x41,0x57,0x41,0x56,0x41,0x55,0x41,0x54,0x56,0x57,0x53},
    {0}, NULL, NULL, 0
};
static SRWLOCK g_open_result_lock = SRWLOCK_INIT;
static volatile LONG64 g_result_event_sequence = 0;

/* ------------------------------------------------------------------ helpers */

static int cold_is_heap(int64_t p) {
    /* Match Frida isHeap: range-only, no VirtualQuery (was hot-path cost). */
    if (!p) return 0;
    if (p < 0x10000) return 0;
    if (p < 0) return 0; /* high half / kernel */
    return 1;
}

static int cold_in_module(int64_t p) {
    BYTE *base = (BYTE *)wxb_weixin_module();
    if (!base || !p) return 0;
    /* Weixin.dll is multi-hundred MB; generous upper bound is fine for vtable checks. */
    return p >= (int64_t)base && p < (int64_t)base + 0x20000000LL;
}

static int cold_str_sane(int64_t addr) {
    int64_t sz, cap, data;
    /* SEH only — avoid VirtualQuery (is_readable_ptr) on hot path. */
    if (!addr) return 0;
    __try {
        sz = r64((void *)(addr + 0x10));
        cap = r64((void *)(addr + 0x18));
        if (sz < 0 || cap < 0) return 0;
        if (sz > cap) return 0;
        if (cap > 0x4000000) return 0;
        if (cap > 15) {
            data = r64((void *)addr);
            return cold_is_heap(data);
        }
        return 1;
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        return 0;
    }
}

static ULONGLONG cold_ms(void) {
    ULONGLONG now = GetTickCount64();
    if (!g_tick0) g_tick0 = now;
    return now - g_tick0;
}

static void cold_log(const char *tag, const char *text) {
    char line[2048];
    char *p = line;
    char *end = line + sizeof(line) - 1;
    char tbuf[32];
    /* +t=ms since first cold log — measure real gaps (async write may reorder slightly). */
    wsprintfA(tbuf, "%I64u", cold_ms());
    append_raw(&p, end, "[cold:");
    append_raw(&p, end, tag ? tag : "?");
    append_raw(&p, end, " t=");
    append_raw(&p, end, tbuf);
    append_raw(&p, end, "] ");
    append_raw(&p, end, text ? text : "");
    *p = 0;
    hb_dbg(line);
}

static void cold_logf(const char *tag, const char *fmt, ...) {
    char body[1600];
    va_list ap;
    /* Prefer CRT _vsnprintf: user32 wvsprintfA is capped (~1KB) and truncates mid-UTF-8. */
    va_start(ap, fmt);
    _vsnprintf(body, sizeof(body) - 1, fmt, ap);
    va_end(ap);
    body[sizeof(body) - 1] = 0;
    cold_log(tag, body);
}

/* Milestone-only on hot path; verbose when hb_verbose.enable present. */
static void cold_log_v(const char *tag, const char *fmt, ...) {
    char body[1600];
    va_list ap;
    if (!InterlockedCompareExchange(&g_verbose_log, 0, 0)) return;
    va_start(ap, fmt);
    _vsnprintf(body, sizeof(body) - 1, fmt, ap);
    va_end(ap);
    body[sizeof(body) - 1] = 0;
    cold_log(tag, body);
}

static void cold_refresh_verbose_flag(void) {
    InterlockedExchange(&g_verbose_log, file_exists(HB_VERBOSE_FILE) ? 1 : 0);
}

/* Use the existing durable relay; never do filesystem/network IO on a hook. */
static void cold_record(ColdClaim *c, const char *stage, int amount_fen,
                        int retcode, int receive_status, const char *detail) {
    char json[2048], event_id[128], number[64];
    char *p = json, *end = json + sizeof(json) - 1;
    FILETIME ft;
    ULARGE_INTEGER ticks;
    ULONGLONG milliseconds;
    LONG64 sequence = InterlockedIncrement64(&g_result_event_sequence);
    if (c && !c->account[0]) return;
    GetSystemTimeAsFileTime(&ft);
    ticks.LowPart = ft.dwLowDateTime;
    ticks.HighPart = ft.dwHighDateTime;
    milliseconds = (ticks.QuadPart - 116444736000000000ULL) / 10000ULL;
    wsprintfA(event_id, "hongbao:%lu:%I64x:%I64u:%I64d", GetCurrentProcessId(),
              (int64_t)&g_open_result_hook, milliseconds, sequence);
    append_raw(&p, end, "{\"schema_version\":1,\"source\":\"native_hongbao\",\"event_type\":\"hongbao.claim\",\"event_id\":\"");
    append_escaped(&p, end, event_id);
    append_raw(&p, end, "\",\"stage\":\""); append_escaped(&p, end, stage);
    append_raw(&p, end, "\",\"self_id\":\""); append_escaped(&p, end, c ? c->account : "");
    append_raw(&p, end, "\",\"send_id\":\""); append_escaped(&p, end, c ? c->sendid : "");
    append_raw(&p, end, "\",\"session\":\"");
    append_escaped(&p, end, c ? (str_eq(c->from, c->account) ? c->to : c->from) : "");
    append_raw(&p, end, "\",\"sender\":\""); append_escaped(&p, end, c ? c->talker : "");
    append_raw(&p, end, "\",\"detail\":\""); append_escaped(&p, end, detail ? detail : "");
    append_raw(&p, end, "\",\"timestamp_ms\":"); wsprintfA(number, "%I64u", milliseconds); append_raw(&p, end, number);
    append_raw(&p, end, ",\"pid\":"); wsprintfA(number, "%lu", GetCurrentProcessId()); append_raw(&p, end, number);
    append_raw(&p, end, ",\"amount_fen\":");
    if (amount_fen >= 0) { wsprintfA(number, "%d", amount_fen); append_raw(&p, end, number); }
    else append_raw(&p, end, "null");
    append_raw(&p, end, ",\"retcode\":"); wsprintfA(number, "%d", retcode); append_raw(&p, end, number);
    append_raw(&p, end, ",\"receive_status\":"); wsprintfA(number, "%d", receive_status); append_raw(&p, end, number);
    append_raw(&p, end, ",\"observer_ready\":"); append_raw(&p, end, g_open_result_hook.installed ? "true" : "false");
    append_raw(&p, end, "}");
    *p = 0;
    if (!wxb_realtime_relay_enqueue(event_id, json, (DWORD)(p - json)))
        cold_log("record-error", "red-packet result enqueue failed; check relay dropped count");
}

/* Skip JSON whitespace. */
static const char *cold_json_skip_ws(const char *p) {
    while (p && (*p == ' ' || *p == '\t' || *p == '\r' || *p == '\n')) p++;
    return p;
}

/* Parse unsigned integer at *pp; advances *pp. Returns 1 on success. */
static int cold_parse_u32(const char **pp, DWORD *out) {
    const char *p;
    DWORD v = 0;
    int digits = 0;
    if (!pp || !*pp || !out) return 0;
    p = *pp;
    while (*p >= '0' && *p <= '9') {
        v = v * 10u + (DWORD)(*p - '0');
        p++;
        digits++;
        if (v > 600000u) v = 600000u; /* cap while parsing */
    }
    if (!digits) return 0;
    *out = v;
    *pp = p;
    return 1;
}

/*
 * Parse delay_ms — value MUST be a JSON string (always quoted):
 *   "0"           → fixed 0
 *   "200"         → fixed 200
 *   "10-200"      → range [10, 200]
 *   "10 - 200"    → range (spaces allowed around '-')
 * Bare numbers are rejected (keep type consistent).
 * Returns 1 if delay_ms key present and parsed.
 */
static int cold_json_get_delay_ms(const char *json, DWORD *lo_out, DWORD *hi_out) {
    const char *p;
    DWORD a = 0, b = 0;
    if (!json || !lo_out || !hi_out) return 0;
    p = str_find_sub(json, "\"delay_ms\"");
    if (!p) return 0;
    p += 10; /* "\"delay_ms\"" length */
    p = cold_json_skip_ws(p);
    if (*p != ':') return 0;
    p++;
    p = cold_json_skip_ws(p);
    /* require string value */
    if (*p != '"') return 0;
    p++;
    p = cold_json_skip_ws(p);
    if (!cold_parse_u32(&p, &a)) return 0;
    p = cold_json_skip_ws(p);
    if (*p == '-') {
        p++;
        p = cold_json_skip_ws(p);
        if (!cold_parse_u32(&p, &b)) return 0;
    } else {
        b = a; /* "200" fixed */
    }
    if (b < a) {
        DWORD t = a;
        a = b;
        b = t;
    }
    *lo_out = a;
    *hi_out = b;
    return 1;
}

static void cold_reload_delay_config(void) {
    char buf[512];
    int n;
    DWORD lo = 0, hi = 0;

    n = read_file_bytes(HB_COLD_DELAY_FILE, buf, (DWORD)sizeof(buf) - 1);
    if (n <= 0) {
        /* No file → no delay (default). */
        InterlockedExchange(&g_delay_min_ms, 0);
        InterlockedExchange(&g_delay_max_ms, 0);
        InterlockedExchange(&g_delay_cfg_loaded, 1);
        return;
    }
    buf[n] = 0;

    /*
     * Only supported format (delay_ms always a string):
     *   {"delay_ms": "0"}
     *   {"delay_ms": "200"}
     *   {"delay_ms": "10-200"}
     */
    if (!cold_json_get_delay_ms(buf, &lo, &hi)) {
        lo = 0;
        hi = 0;
    }
    InterlockedExchange(&g_delay_min_ms, (LONG)lo);
    InterlockedExchange(&g_delay_max_ms, (LONG)hi);
    InterlockedExchange(&g_delay_cfg_loaded, 1);
}

/* Pick delay for this claim: fixed if min==max, else uniform integer in [min, max]. */
static DWORD cold_pick_delay_ms(void) {
    LONG lo = InterlockedCompareExchange(&g_delay_min_ms, 0, 0);
    LONG hi = InterlockedCompareExchange(&g_delay_max_ms, 0, 0);
    DWORD span;
    DWORD r;
    if (lo < 0) lo = 0;
    if (hi < 0) hi = 0;
    if (hi < lo) {
        LONG t = lo;
        lo = hi;
        hi = t;
    }
    if (hi == lo) return (DWORD)lo;
    span = (DWORD)(hi - lo) + 1u;
    r = (DWORD)(GetTickCount64() ^ ((ULONGLONG)GetCurrentThreadId() << 17) ^ (ULONGLONG)(uintptr_t)&span);
    return (DWORD)lo + (r % span);
}

static int cold_is_hex32(const char *s) {
    int i;
    if (!s || lstrlenA(s) != 32) return 0;
    for (i = 0; i < 32; i++) {
        char c = s[i];
        if (!((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f') || (c >= 'A' && c <= 'F')))
            return 0;
    }
    return 1;
}

/* ------------------------------------------------------------------ control */

static void cold_refresh_control_flags(void) {
    int enabled = file_exists(HB_COLD_DISABLE_FILE) ? 0 : 1;
    int open_enabled = enabled && !file_exists(HB_COLD_OPEN_DISABLE_FILE);
    int fastlane_enabled = enabled && file_exists(HB_COLD_FASTLANE_ENABLE_FILE);
    if (!enabled) {
        InterlockedExchange(&g_cold_enabled, 0);
        InterlockedExchange(&g_cold_open_enabled, 0);
        InterlockedExchange(&g_cold_fastlane_enabled, 0);
        return;
    }
    InterlockedExchange(&g_cold_open_enabled, open_enabled ? 1 : 0);
    InterlockedExchange(&g_cold_fastlane_enabled, fastlane_enabled ? 1 : 0);
    InterlockedExchange(&g_cold_enabled, 1);
}

int wxb_hb_cold_enabled(void) {
    return InterlockedCompareExchange(&g_cold_enabled, 0, 0) != 0;
}

int wxb_hb_cold_open_enabled(void) {
    if (!wxb_hb_cold_enabled()) return 0;
    return InterlockedCompareExchange(&g_cold_open_enabled, 0, 0) != 0;
}

static int cold_fastlane_enabled(void) {
    if (!wxb_hb_cold_enabled()) return 0;
    return InterlockedCompareExchange(&g_cold_fastlane_enabled, 0, 0) != 0;
}

/* ------------------------------------------------------------------ pin bag / claims */

/* HeapAlloc is far cheaper than VirtualAlloc for 0x20..0x400 pin bags. */
static BYTE *cold_zalloc(SIZE_T size) {
    BYTE *p = (BYTE *)HeapAlloc(GetProcessHeap(), HEAP_ZERO_MEMORY, size);
    return p;
}

static void cold_free(BYTE *p) {
    if (p) HeapFree(GetProcessHeap(), 0, p);
}

/*
 * CRITICAL (crash-after-a-while):
 * msgCtx / timing heap / parent are still read by WeChat async coroutines after
 * Launch_receive/open returns. HeapFree here was a classic delayed UAF:
 *   COLD-OPEN ok → ~60s pin release → WeChat still touches freed buffer → crash.
 * Frida keeps pin bags without returning them to a free that races WeChat.
 * Policy: drop references and LEAK small pins (~1–2KB per claim). Slot reuse
 * allocates new buffers; old bytes remain valid until process exit.
 */
static void cold_claim_release_buffers(ColdClaim *c) {
    if (!c) return;
    c->msgctx = NULL;
    c->recv_out = NULL;
    c->recv_a2 = NULL;
    c->open_out = NULL;
    c->open_a2 = NULL;
    c->timing_hdr = NULL;
    c->timing_arg_data = NULL;
    c->timing_parent_data = NULL;
    c->parent_recv = 0;
    c->parent_open = 0;
    (void)cold_free; /* keep helper for future non-WeChat-visible temps */
}

static void cold_claim_free_locked(ColdClaim *c) {
    if (!c) return;
    cold_claim_release_buffers(c);
    ZeroMemory(c->sendid, sizeof(c->sendid));
    ZeroMemory(c->from, sizeof(c->from));
    ZeroMemory(c->to, sizeof(c->to));
    ZeroMemory(c->talker, sizeof(c->talker));
    ZeroMemory(c->account, sizeof(c->account));
    ZeroMemory(c->timing, sizeof(c->timing));
    c->claimed_tick = 0;
    c->launch_begin_tick = 0;
    c->launch_tick = 0;
    c->opened_tick = 0;
    c->expire_after_tick = 0;
    c->release_after_tick = 0;
    InterlockedExchange64(&c->receive_task_ctx, 0);
    c->launch_thread_id = 0;
    InterlockedExchange(&c->phase, COLD_PHASE_FREE);
    InterlockedExchange(&c->generation, 0);
    InterlockedExchange(&c->refs, 0);
    InterlockedExchange(&c->in_use, 0);
}

static ColdClaim *cold_find_by_sendid_locked(const char *sendid) {
    int i;
    if (!sendid || !sendid[0]) return NULL;
    for (i = 0; i < COLD_CLAIM_SLOTS; i++) {
        if (g_claims[i].in_use &&
            g_claims[i].sendid[0] && str_eq(g_claims[i].sendid, sendid)) {
            return &g_claims[i];
        }
    }
    return NULL;
}

static int cold_claim_exists(const char *sendid) {
    int found;
    AcquireSRWLockShared(&g_claim_lock);
    found = cold_find_by_sendid_locked(sendid) != NULL;
    ReleaseSRWLockShared(&g_claim_lock);
    return found;
}

static ColdClaim *cold_claim_acquire_by_sendid(const char *sendid) {
    ColdClaim *c;
    AcquireSRWLockShared(&g_claim_lock);
    c = cold_find_by_sendid_locked(sendid);
    if (c) InterlockedIncrement(&c->refs);
    ReleaseSRWLockShared(&g_claim_lock);
    return c;
}

static int cold_claim_add_ref(ColdClaim *c, LONG generation) {
    int ok = 0;
    if (!c) return 0;
    AcquireSRWLockShared(&g_claim_lock);
    if (c->in_use && c->generation == generation) {
        InterlockedIncrement(&c->refs);
        ok = 1;
    }
    ReleaseSRWLockShared(&g_claim_lock);
    return ok;
}

static void cold_claim_release(ColdClaim *c) {
    if (c) InterlockedDecrement(&c->refs);
}

static void cold_claim_discard(ColdClaim *c) {
    LONG refs;
    if (!c) return;
    AcquireSRWLockExclusive(&g_claim_lock);
    refs = InterlockedCompareExchange(&c->refs, 0, 0);
    if (c->in_use && refs == 1) {
        InterlockedExchange(&c->refs, 0);
        cold_claim_free_locked(c);
    } else if (c->in_use && refs > 1) {
        c->release_after_tick = GetTickCount64();
        InterlockedDecrement(&c->refs);
    }
    ReleaseSRWLockExclusive(&g_claim_lock);
}

static ColdClaim *cold_alloc_claim(const char *sendid) {
    int i;
    ColdClaim *c = NULL;
    /* Serialize alloc so parser + source_submit cannot double-claim one sendId. */
    AcquireSRWLockExclusive(&g_claim_lock);
    if (cold_find_by_sendid_locked(sendid)) {
        ReleaseSRWLockExclusive(&g_claim_lock);
        return NULL; /* already claimed */
    }
    for (i = 0; i < COLD_CLAIM_SLOTS; i++) {
        if (!g_claims[i].in_use) {
            LONG generation = InterlockedIncrement(&g_claim_generation_seq);
            if (!generation) generation = InterlockedIncrement(&g_claim_generation_seq);
            c = &g_claims[i];
            ZeroMemory(c->sendid, sizeof(c->sendid));
            ZeroMemory(c->from, sizeof(c->from));
            ZeroMemory(c->to, sizeof(c->to));
            ZeroMemory(c->talker, sizeof(c->talker));
            ZeroMemory(c->account, sizeof(c->account));
            ZeroMemory(c->timing, sizeof(c->timing));
            c->msgctx = NULL;
            c->recv_out = NULL;
            c->recv_a2 = NULL;
            c->open_out = NULL;
            c->open_a2 = NULL;
            c->timing_hdr = NULL;
            c->timing_arg_data = NULL;
            c->timing_parent_data = NULL;
            c->parent_recv = 0;
            c->parent_open = 0;
            c->claimed_tick = GetTickCount64();
            c->launch_begin_tick = 0;
            c->launch_tick = 0;
            c->opened_tick = 0;
            c->expire_after_tick = c->claimed_tick + COLD_PIN_TTL_MS;
            c->release_after_tick = 0;
            InterlockedExchange64(&c->receive_task_ctx, 0);
            c->launch_thread_id = 0;
            safe_copy(c->sendid, sizeof(c->sendid), sendid);
            InterlockedExchange(&c->phase, COLD_PHASE_FREE);
            InterlockedExchange(&c->generation, generation);
            InterlockedExchange(&c->refs, 1); /* caller reference */
            InterlockedExchange(&c->in_use, 1);
            break;
        }
    }
    ReleaseSRWLockExclusive(&g_claim_lock);
    return c;
}

static int cold_claim_index(ColdClaim *c) {
    if (!c) return -1;
    return (int)(c - g_claims);
}

static void cold_sweep_claims(void) {
    int i;
    ULONGLONG now = GetTickCount64();
    AcquireSRWLockExclusive(&g_claim_lock);
    for (i = 0; i < COLD_CLAIM_SLOTS; i++) {
        ColdClaim *c = &g_claims[i];
        if (!c->in_use || InterlockedCompareExchange(&c->refs, 0, 0) != 0) continue;
        if (c->release_after_tick && now >= c->release_after_tick) {
            cold_logf("pin", "release slot sendId=%s (buffers leaked intentionally)", c->sendid);
            cold_claim_free_locked(c);
            continue;
        }
        if (c->expire_after_tick && now >= c->expire_after_tick) {
            cold_logf("pin", "TTL drop slot sendId=%s (buffers leaked intentionally)", c->sendid);
            cold_claim_free_locked(c);
        }
    }
    ReleaseSRWLockExclusive(&g_claim_lock);
}

static void cold_drop_idle_claims(void) {
    int i;
    AcquireSRWLockExclusive(&g_claim_lock);
    for (i = 0; i < COLD_CLAIM_SLOTS; i++) {
        ColdClaim *c = &g_claims[i];
        if (c->in_use && InterlockedCompareExchange(&c->refs, 0, 0) == 0) {
            cold_claim_free_locked(c);
        }
    }
    ReleaseSRWLockExclusive(&g_claim_lock);
}

/* ------------------------------------------------------------------ receive timing */

static ColdReceiveTraceScope *cold_receive_trace_scope(void) {
    if (g_recv_trace_tls == TLS_OUT_OF_INDEXES) return NULL;
    return (ColdReceiveTraceScope *)TlsGetValue(g_recv_trace_tls);
}

static int cold_receive_trace_match_claim(int64_t receive_ctx,
                                          ColdReceiveTraceScope *scope) {
    ColdClaim *best = NULL;
    ULONGLONG best_score = ~0ULL;
    ULONGLONG now = GetTickCount64();
    DWORD tid = GetCurrentThreadId();
    int i;

    if (!receive_ctx || !scope) return 0;
    AcquireSRWLockExclusive(&g_claim_lock);
    for (i = 0; i < COLD_CLAIM_SLOTS; i++) {
        ColdClaim *c = &g_claims[i];
        LONG phase;
        LONG64 bound_ctx;
        ULONGLONG age;
        ULONGLONG score;
        if (!c->in_use || !c->sendid[0] || !c->launch_begin_tick ||
            now < c->launch_begin_tick) continue;
        age = now - c->launch_begin_tick;
        if (age > COLD_RECV_TRACE_MATCH_MS) continue;
        phase = InterlockedCompareExchange(&c->phase, 0, 0);
        if (phase < COLD_PHASE_RECV_LAUNCHING || phase > COLD_PHASE_DONE) continue;
        bound_ctx = InterlockedCompareExchange64(&c->receive_task_ctx, 0, 0);
        if (bound_ctx && bound_ctx != receive_ctx) continue;
        score = age;
        if (c->launch_thread_id != tid) score += COLD_RECV_TRACE_MATCH_MS;
        if (bound_ctx == receive_ctx) score = 0;
        if (!best || score < best_score) {
            best = c;
            best_score = score;
        }
    }
    if (best) {
        if (!InterlockedCompareExchange64(&best->receive_task_ctx,
                                          receive_ctx, 0) ||
            InterlockedCompareExchange64(&best->receive_task_ctx, 0, 0) == receive_ctx) {
            safe_copy(scope->sendid, sizeof(scope->sendid), best->sendid);
            scope->claimed_tick = best->claimed_tick;
            scope->launch_begin_tick = best->launch_begin_tick;
            scope->launch_tid = best->launch_thread_id;
        } else {
            best = NULL;
        }
    }
    ReleaseSRWLockExclusive(&g_claim_lock);
    return best != NULL;
}

static int64_t __fastcall cold_receive_task_trace_hook(int64_t receive_ctx) {
    FnReceiveTask orig = (FnReceiveTask)g_recv_trace_hooks[0].trampoline;
    ColdReceiveTraceScope scope;
    ColdReceiveTraceScope *previous = NULL;
    ULONGLONG now;
    int matched;
    int64_t ret = 0;

    if (!orig) return 0;
    if (!cold_fastlane_enabled()) return orig(receive_ctx);
    ZeroMemory(&scope, sizeof(scope));
    scope.receive_ctx = receive_ctx;
    scope.task_begin_tick = GetTickCount64();
    scope.task_tid = GetCurrentThreadId();
    matched = cold_receive_trace_match_claim(receive_ctx, &scope);
    if (g_recv_trace_tls != TLS_OUT_OF_INDEXES) {
        previous = (ColdReceiveTraceScope *)TlsGetValue(g_recv_trace_tls);
        TlsSetValue(g_recv_trace_tls, &scope);
    }
    now = scope.task_begin_tick;
    cold_logf("TASK-START",
              "sendId=%s ctx=%I64x matched=%d task_tid=%lu launch_tid=%lu since_claim_ms=%I64u launch_to_task_ms=%I64u",
              scope.sendid[0] ? scope.sendid : "?", receive_ctx, matched,
              (unsigned long)scope.task_tid, (unsigned long)scope.launch_tid,
              scope.claimed_tick ? now - scope.claimed_tick : 0ULL,
              scope.launch_begin_tick ? now - scope.launch_begin_tick : 0ULL);
    __try {
        ret = orig(receive_ctx);
    } __finally {
        now = GetTickCount64();
        cold_logf("TASK-END",
                  "sendId=%s ctx=%I64x total_ms=%I64u attempts=%ld abnormal=%d",
                  scope.sendid[0] ? scope.sendid : "?", receive_ctx,
                  now - scope.task_begin_tick, (long)scope.attempt_count,
                  AbnormalTermination() ? 1 : 0);
        if (g_recv_trace_tls != TLS_OUT_OF_INDEXES)
            TlsSetValue(g_recv_trace_tls, previous);
    }
    return ret;
}

static int64_t __fastcall cold_receive_request_loop_trace_hook(
        int64_t result, int64_t a2, int64_t a3, int a4,
        int64_t a5, int64_t a6, int *config) {
    FnReceiveRequestLoop orig =
        (FnReceiveRequestLoop)g_recv_trace_hooks[1].trampoline;
    ColdReceiveTraceScope *scope = cold_receive_trace_scope();
    int64_t base = (int64_t)wxb_weixin_module();
    int64_t retaddr = (int64_t)_ReturnAddress();
    ULONGLONG started;
    int retry_count = -1;
    int timeout_ms = -1;
    int64_t previous_result;
    int64_t ret = 0;

    if (!orig) return 0;
    if (!scope || !scope->sendid[0] || !base ||
        retaddr != base + COLD_RECV_REQUEST_RETURN_RVA)
        return orig(result, a2, a3, a4, a5, a6, config);
    __try {
        if (config) {
            retry_count = config[0];
            timeout_ms = config[1];
        }
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        retry_count = -1;
        timeout_ms = -1;
    }
    started = GetTickCount64();
    previous_result = scope->request_result;
    scope->request_result = result;
    scope->request_depth++;
    cold_logf("REQUEST-BEGIN",
              "sendId=%s retry_count=%d timeout_ms=%d task_to_request_ms=%I64u",
              scope->sendid, retry_count, timeout_ms,
              started - scope->task_begin_tick);
    __try {
        ret = orig(result, a2, a3, a4, a5, a6, config);
    } __finally {
        cold_logf("REQUEST-END",
                  "sendId=%s total_ms=%I64u attempts=%ld abnormal=%d",
                  scope->sendid, GetTickCount64() - started,
                  (long)scope->attempt_count, AbnormalTermination() ? 1 : 0);
        scope->request_depth--;
        scope->request_result = previous_result;
    }
    return ret;
}

static int64_t __fastcall cold_receive_attempt_trace_hook(int64_t result,
                                                          int64_t request,
                                                          int64_t task) {
    FnReceiveAttempt orig = (FnReceiveAttempt)g_recv_trace_hooks[2].trampoline;
    ColdReceiveTraceScope *scope = cold_receive_trace_scope();
    ULONGLONG started;
    LONG attempt;
    int64_t ret = 0;

    if (!orig) return 0;
    if (!scope || !scope->sendid[0] || scope->request_depth <= 0 ||
        request != scope->request_result)
        return orig(result, request, task);
    attempt = ++scope->attempt_count;
    started = GetTickCount64();
    cold_logf("ATTEMPT-BEGIN",
              "sendId=%s attempt=%ld task_to_attempt_ms=%I64u",
              scope->sendid, (long)attempt,
              started - scope->task_begin_tick);
    __try {
        ret = orig(result, request, task);
    } __finally {
        cold_logf("ATTEMPT-END",
                  "sendId=%s attempt=%ld total_ms=%I64u abnormal=%d",
                  scope->sendid, (long)attempt, GetTickCount64() - started,
                  AbnormalTermination() ? 1 : 0);
    }
    return ret;
}

/* ------------------------------------------------------------------ deferred jobs
 *
 * CRITICAL: jobs MUST run in parallel. A single serial worker is wrong because
 * Launch_receive (kind=1) may block or keep the worker busy for the full 1581
 * RTT; then open (kind=2) after HB-RES waits behind it → user sees 6060→16020
 * gap of ~1s. Frida setTimeout schedules independent tasks on the JS event loop.
 * We use QueueUserWorkItem (thread pool) per job so open never waits on receive.
 */

/* Forward decls: job callbacks run before full definitions below. */
static void cold_do_launch_recv(ColdClaim *c);
static void cold_do_open_build_and_launch(ColdClaim *c, const char *timing);
static void cold_do_launch_open(ColdClaim *c, int64_t parent, const char *timing);
static void cold_mark_done(ColdClaim *c, ULONGLONG release_delay_ms);

static int cold_job_epoch_current(const ColdDeferredJob *job) {
    if (!job) return 0;
    return InterlockedCompareExchange64(&g_job_epoch, 0, 0) == job->epoch;
}

static int cold_wait_job_due(const ColdDeferredJob *job) {
    ULONGLONG now;
    DWORD wait_ms;
    DWORD wait_rc;
    if (!cold_job_epoch_current(job)) return 0;
    if (!job->due_tick) return wxb_hb_cold_enabled();
    now = GetTickCount64();
    if (now >= job->due_tick) return wxb_hb_cold_enabled();
    wait_ms = (DWORD)(job->due_tick - now);
    if (g_job_cancel_event) {
        wait_rc = WaitForSingleObject(g_job_cancel_event, wait_ms);
        if (wait_rc != WAIT_TIMEOUT) return 0;
    } else {
        Sleep(wait_ms);
    }
    return cold_job_epoch_current(job) && wxb_hb_cold_enabled();
}

static void cold_run_one_job(const ColdDeferredJob *job) {
    ColdClaim *c;
    if (!job) return;
    if (!cold_wait_job_due(job)) return;
    if (job->claim_index < 0 || job->claim_index >= COLD_CLAIM_SLOTS) return;
    c = &g_claims[job->claim_index];
    if (!InterlockedCompareExchange(&c->in_use, 0, 0) ||
        InterlockedCompareExchange(&c->generation, 0, 0) != job->claim_generation ||
        !str_eq(c->sendid, job->sendid)) return;
    if (job->kind == 1) {
        if (InterlockedCompareExchange(&c->phase,
                                       COLD_PHASE_RECV_LAUNCHING,
                                       COLD_PHASE_BUILT) != COLD_PHASE_BUILT) return;
        cold_do_launch_recv(c);
    } else if (job->kind == 2) {
        if (!wxb_hb_cold_open_enabled()) {
            cold_mark_done(c, COLD_RELEASE_RECV_MS);
            return;
        }
        if (InterlockedCompareExchange(&c->phase, 0, 0) != COLD_PHASE_OPENING) return;
        /* Open build + Launch_open in one shot (no second serial hop). */
        cold_do_open_build_and_launch(c, job->timing[0] ? job->timing : c->timing);
    }
}

static DWORD WINAPI cold_pool_job_cb(LPVOID param) {
    ColdDeferredJob *job = (ColdDeferredJob *)param;
    ColdClaim *c = NULL;
    if (!job) return 0;
    if (job->claim_index >= 0 && job->claim_index < COLD_CLAIM_SLOTS)
        c = &g_claims[job->claim_index];
    cold_run_one_job(job);
    if (c) cold_claim_release(c);
    HeapFree(GetProcessHeap(), 0, job);
    if (InterlockedDecrement(&g_job_count) == 0 && g_job_idle_event)
        SetEvent(g_job_idle_event);
    return 0;
}

static void cold_job_init_acquire(void) {
    while (InterlockedCompareExchange(&g_job_init_lock, 1, 0) != 0)
        SwitchToThread();
}

static void cold_job_init_release(void) {
    InterlockedExchange(&g_job_init_lock, 0);
}

static int cold_ensure_worker(void) {
    int ready = 0;
    if (!wxb_hb_cold_enabled()) return 0;
    cold_job_init_acquire();
    if (!g_job_cs_ready) {
        InitializeCriticalSection(&g_job_cs);
        InterlockedExchange(&g_job_cs_ready, 1);
    }
    if (!g_job_cancel_event)
        g_job_cancel_event = CreateEventA(NULL, TRUE, FALSE, NULL);
    if (!g_job_idle_event)
        g_job_idle_event = CreateEventA(NULL, TRUE, TRUE, NULL);
    if (g_job_cancel_event && g_job_idle_event) {
        EnterCriticalSection(&g_job_cs);
        if (g_worker_started) {
            ready = 1;
        } else if (InterlockedCompareExchange(&g_job_count, 0, 0) == 0) {
            ResetEvent(g_job_cancel_event);
            InterlockedExchange(&g_job_accepting, 1);
            InterlockedExchange(&g_worker_started, 1);
            ready = 1;
        }
        LeaveCriticalSection(&g_job_cs);
    }
    cold_job_init_release();
    return ready;
}

static void cold_stop_worker(void) {
    DWORD wait_rc = WAIT_OBJECT_0;
    if (!g_job_cs_ready) {
        cold_drop_idle_claims();
        return;
    }
    EnterCriticalSection(&g_job_cs);
    InterlockedExchange(&g_job_accepting, 0);
    InterlockedExchange(&g_worker_started, 0);
    InterlockedIncrement64(&g_job_epoch);
    if (g_job_cancel_event) SetEvent(g_job_cancel_event);
    LeaveCriticalSection(&g_job_cs);
    if (g_job_idle_event && InterlockedCompareExchange(&g_job_count, 0, 0) != 0)
        wait_rc = WaitForSingleObject(g_job_idle_event, COLD_JOB_STOP_WAIT_MS);
    if (wait_rc != WAIT_OBJECT_0)
        cold_logf("stop-warn", "timed out waiting for %ld cold jobs",
                  (long)InterlockedCompareExchange(&g_job_count, 0, 0));
    cold_drop_idle_claims();
}

static int cold_enqueue_job(int kind, ColdClaim *c,
                            const char *timing, ULONGLONG delay_ms) {
    ColdDeferredJob *job;
    BOOL ok;
    HANDLE th = NULL;
    LONG generation;
    if (!c || !cold_ensure_worker()) return 0;
    job = (ColdDeferredJob *)HeapAlloc(GetProcessHeap(), HEAP_ZERO_MEMORY, sizeof(*job));
    if (!job) return 0;
    generation = InterlockedCompareExchange(&c->generation, 0, 0);
    if (!generation || !cold_claim_add_ref(c, generation)) {
        HeapFree(GetProcessHeap(), 0, job);
        return 0;
    }
    EnterCriticalSection(&g_job_cs);
    if (!g_job_accepting || !wxb_hb_cold_enabled()) {
        LeaveCriticalSection(&g_job_cs);
        cold_claim_release(c);
        HeapFree(GetProcessHeap(), 0, job);
        return 0;
    }
    job->kind = kind;
    job->claim_index = cold_claim_index(c);
    job->claim_generation = generation;
    job->epoch = InterlockedCompareExchange64(&g_job_epoch, 0, 0);
    safe_copy(job->sendid, sizeof(job->sendid), c->sendid);
    job->due_tick = delay_ms ? (GetTickCount64() + delay_ms) : 0;
    if (timing) safe_copy(job->timing, sizeof(job->timing), timing);
    InterlockedIncrement(&g_job_count);
    if (g_job_idle_event) ResetEvent(g_job_idle_event);
    /* WT_EXECUTEDEFAULT: run on pool ASAP, parallel with other cold jobs. */
    ok = QueueUserWorkItem(cold_pool_job_cb, job, WT_EXECUTEDEFAULT);
    if (!ok) {
        /* Fallback: fire a short-lived thread (still parallel, not serial). */
        th = CreateThread(NULL, 0, cold_pool_job_cb, job, 0, NULL);
        if (!th) {
            if (InterlockedDecrement(&g_job_count) == 0 && g_job_idle_event)
                SetEvent(g_job_idle_event);
            LeaveCriticalSection(&g_job_cs);
            cold_claim_release(c);
            HeapFree(GetProcessHeap(), 0, job);
            return 0;
        }
        SetThreadPriority(th, THREAD_PRIORITY_ABOVE_NORMAL);
        CloseHandle(th);
    }
    LeaveCriticalSection(&g_job_cs);
    return 1;
}

static void cold_process_jobs(void) {
    /* No serial drain needed when using the thread pool. */
}

static int cold_write_std_string(int64_t addr, const char *s, ColdClaim *claim) {
    int n;
    if (!addr || !s || !is_readable_ptr(addr, 0x20)) return 0;
    n = lstrlenA(s);
    __try {
        if (n <= 15) {
            ZeroMemory((void *)addr, 16);
            if (n > 0) copy_bytes((void *)addr, s, n);
            w8((void *)(addr + n), 0);
            w64((void *)(addr + 0x10), n);
            w64((void *)(addr + 0x18), 15);
            return 1;
        }
        {
            BYTE *data = cold_zalloc((SIZE_T)n + 1);
            if (!data) return 0;
            copy_bytes(data, s, n);
            data[n] = 0;
            if (claim) claim->timing_parent_data = data;
            w64((void *)addr, (int64_t)data);
            w64((void *)(addr + 0x10), n);
            w64((void *)(addr + 0x18), n);
            return 1;
        }
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        return 0;
    }
}

static BYTE *cold_make_std_string(const char *s, ColdClaim *claim) {
    int n;
    BYTE *sp;
    if (!s) s = "";
    n = lstrlenA(s);
    sp = cold_zalloc(0x20);
    if (!sp) return NULL;
    if (claim) claim->timing_hdr = sp;
    if (n <= 15) {
        if (n > 0) copy_bytes(sp, s, n);
        sp[n] = 0;
        w64(sp + 0x10, n);
        w64(sp + 0x18, 15);
        return sp;
    }
    {
        BYTE *data = cold_zalloc((SIZE_T)n + 1);
        if (!data) {
            if (claim && claim->timing_hdr == sp) claim->timing_hdr = NULL;
            cold_free(sp);
            return NULL;
        }
        copy_bytes(data, s, n);
        data[n] = 0;
        if (claim) claim->timing_arg_data = data;
        w64(sp, (int64_t)data);
        w64(sp + 0x10, n);
        w64(sp + 0x18, n);
        return sp;
    }
}

/* ------------------------------------------------------------------ DI service resolve */

static int64_t cold_resolve_hb_service(int64_t *out_cgi) {
    int64_t base = (int64_t)wxb_weixin_module();
    FnGetLocator get_locator;
    FnResolveByType resolve_by_type;
    int64_t locator = 0, lvt = 0, container = 0, cvt = 0, resolver = 0;
    int64_t service = 0, svt = 0, cgi = 0;
    BYTE csp[0x10], rsp[0x10], out_sp[0x10], tk[8];
    FnVcall2 get_container, get_resolver;

    if (out_cgi) *out_cgi = 0;
    if (!base) return 0;

    get_locator = (FnGetLocator)(base + WX_OFF_SVC_GET_LOCATOR);
    resolve_by_type = (FnResolveByType)(base + WX_OFF_SVC_RESOLVE_BY_TYPE);

    __try {
        locator = get_locator();
        if (!cold_is_heap(locator)) {
            cold_log("RESOLVE", "locator null (not logged in yet?)");
            return 0;
        }
        lvt = r64((void *)locator);
        if (!cold_in_module(lvt)) {
            cold_logf("RESOLVE", "locator vtable oob %I64x", lvt);
            return 0;
        }
        ZeroMemory(csp, sizeof(csp));
        get_container = (FnVcall2)r64((void *)(lvt + WX_SVC_LOCATOR_VT_GET_CONTAINER));
        if (!get_container) return 0;
        get_container(locator, (int64_t)csp);
        container = r64(csp);
        if (!cold_is_heap(container)) {
            cold_log("RESOLVE", "container null");
            return 0;
        }
        cvt = r64((void *)container);
        if (!cold_in_module(cvt)) {
            cold_logf("RESOLVE", "container vtable oob %I64x", cvt);
            return 0;
        }
        ZeroMemory(rsp, sizeof(rsp));
        get_resolver = (FnVcall2)r64((void *)(cvt + WX_SVC_CONTAINER_VT_GET_RESOLVER));
        if (!get_resolver) return 0;
        get_resolver(container, (int64_t)rsp);
        resolver = r64(rsp);
        if (!cold_is_heap(resolver)) {
            cold_log("RESOLVE", "resolver null");
            return 0;
        }

        /* *tk = &descriptor (the ADDRESS, not its contents) */
        w64(tk, base + WX_OFF_HB_TYPE_KEY_PTR);
        ZeroMemory(out_sp, sizeof(out_sp));
        resolve_by_type(resolver, (int64_t)out_sp, (int64_t)tk);
        service = r64(out_sp);
        if (!cold_is_heap(service)) {
            cold_log("RESOLVE", "service null");
            return 0;
        }
        svt = r64((void *)service);
        if (svt != base + WX_OFF_HB_SVC_VTABLE) {
            cold_logf("RESOLVE", "service vtable mismatch (%I64x) — refusing", svt);
            return 0;
        }
        cgi = r64((void *)(service + 0x40));
        if (!cold_is_heap(cgi)) {
            cold_log("RESOLVE", "cgiTable null");
            return 0;
        }
        cold_logf("RESOLVE", "service=%I64x vtableOk=1 cgiTable=%I64x", service, cgi);
        if (out_cgi) *out_cgi = cgi;
        return service;
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        cold_log("RESOLVE", "exception in DI resolve");
        return 0;
    }
}

static int64_t cold_ensure_service(void) {
    int64_t svc = (int64_t)InterlockedCompareExchange64(&g_hb_service, 0, 0);
    int64_t cgi = (int64_t)InterlockedCompareExchange64(&g_cgi_table, 0, 0);
    ULONGLONG now;
    int64_t new_cgi = 0, new_svc = 0;

    if (svc && cgi) return svc;
    now = GetTickCount64();
    if (g_resolve_fail_at && now - g_resolve_fail_at < COLD_RESOLVE_RETRY_MS) return svc;
    new_svc = cold_resolve_hb_service(&new_cgi);
    if (new_svc && new_cgi) {
        InterlockedExchange64(&g_hb_service, new_svc);
        InterlockedExchange64(&g_cgi_table, new_cgi);
        safe_copy(g_cgi_src, sizeof(g_cgi_src), "DI-resolve");
        g_resolve_fail_at = 0;
        cold_logf("cgiTable", "resolved cold via DI: service=%I64x cgi=%I64x", new_svc, new_cgi);
        return new_svc;
    }
    g_resolve_fail_at = now;
    cold_log("cgiTable-warn", "DI resolve failed — retry later; passive cache if available");
    return (int64_t)InterlockedCompareExchange64(&g_hb_service, 0, 0);
}

void wxb_hb_cold_note_factory_cgi(int64_t cgi_table) {
    if (!cold_is_heap(cgi_table)) return;
    if (InterlockedCompareExchange64(&g_cgi_table, 0, 0)) return;
    InterlockedExchange64(&g_cgi_table, cgi_table);
    safe_copy(g_cgi_src, sizeof(g_cgi_src), "passive-factory");
    cold_logf("cgiTable", "cached from Factory a1: %I64x", cgi_table);
}

/* ------------------------------------------------------------------ LocalMsg validate / parse */

static const int COLD_STR_OFFS[] = { 0x18, 0x38, 0x58, 0xC0, 0xE0, 0x160 };
static const int COLD_PTR_OFFS[] = { 0x1C8, 0x1D0, 0x1D8, 0x1E0, 0x1F0, 0x1F8, 0x230, 0x238 };

static const char *cold_validate_local_msg(int64_t msg) {
    int i;
    if (!msg) return "unreadable LocalMsg";
    __try {
        (void)r64((void *)msg); /* touch */
        for (i = 0; i < (int)(sizeof(COLD_STR_OFFS) / sizeof(COLD_STR_OFFS[0])); i++) {
            if (!cold_str_sane(msg + COLD_STR_OFFS[i])) return "bad std::string";
        }
        for (i = 0; i < (int)(sizeof(COLD_PTR_OFFS) / sizeof(COLD_PTR_OFFS[0])); i++) {
            int64_t p = r64((void *)(msg + COLD_PTR_OFFS[i]));
            if (p && !cold_is_heap(p)) return "garbage ptr";
        }
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        return "LocalMsg access fault";
    }
    return NULL;
}

static int cold_parse_hb_msg(int64_t msg,
                             char *sendid, int sendid_max,
                             char *from, int from_max,
                             char *to, int to_max,
                             char *talker, int talker_max,
                             char *native_url, int native_max) {
    char content[4096] = {0};
    char body[4096] = {0};
    char page_url[512] = {0};
    char channelid[64] = {0}, msgtype[32] = {0};
    char sendusername[128] = {0}, sign[128] = {0}, total_num[32] = {0};
    const char *p;
    int type_num = 0;

    if (sendid && sendid_max > 0) sendid[0] = 0;
    if (from && from_max > 0) from[0] = 0;
    if (to && to_max > 0) to[0] = 0;
    if (talker && talker_max > 0) talker[0] = 0;
    if (native_url && native_max > 0) native_url[0] = 0;

    if (!msg || !is_readable_ptr(msg, HB_SOURCE_SIZE)) return 0;

    /* Prefer shared extractor used by noclick path when available. */
    if (hb_source_extract_hongbao_context(msg,
                                          sendid, sendid_max,
                                          native_url, native_max,
                                          page_url, sizeof(page_url),
                                          from, from_max,
                                          talker, talker_max,
                                          content, sizeof(content))) {
        if (to && to_max > 0) hb_read_functional_string(msg + 0x38, to, to_max);
        if (from && from_max > 0 && !from[0]) hb_read_functional_string(msg + 0x18, from, from_max);
        if (talker && talker_max > 0 && !talker[0]) hb_read_functional_string(msg + 0x58, talker, talker_max);
        return sendid && sendid[0] ? 1 : 0;
    }

    hb_read_functional_string(msg + 0x160, content, sizeof(content));
    if (!content[0]) return 0;
    /* strip "wxid_xxx:\n" group prefix */
    p = content;
    if (str_has(content, ":\n") || str_has(content, ":\r\n")) {
        const char *nl = str_find_sub(content, ":\n");
        if (!nl) nl = str_find_sub(content, ":\r\n");
        if (nl && nl - content < 80) p = nl + ((nl[1] == '\r') ? 3 : 2);
    }
    safe_copy(body, sizeof(body), p);

    if (str_has(body, "<type>")) {
        char tbuf[16] = {0};
        extract_between_markers(body, "<type>", "</type>", tbuf, sizeof(tbuf));
        if (!tbuf[0]) extract_between_markers(body, "<type><![CDATA[", "]]></type>", tbuf, sizeof(tbuf));
        type_num = (int)a2i64(tbuf);
    }
    if (type_num != 2001 && !str_has(body, "wcpayinfo")) return 0;

    hb_extract_urls_from_text(body, native_url, native_max, page_url, sizeof(page_url));
    hb_fill_query_fields(native_url, page_url,
                         sendid, sendid_max,
                         channelid, sizeof(channelid),
                         msgtype, sizeof(msgtype),
                         sendusername, sizeof(sendusername),
                         sign, sizeof(sign),
                         total_num, sizeof(total_num));
    if (sendid && sendid_max > 0 && !sendid[0])
        extract_between_markers(body, "<sendid>", "</sendid>", sendid, sendid_max);

    if (from && from_max > 0) hb_read_functional_string(msg + 0x18, from, from_max);
    if (to && to_max > 0) hb_read_functional_string(msg + 0x38, to, to_max);
    if (talker && talker_max > 0) hb_read_functional_string(msg + 0x58, talker, talker_max);
    return sendid && sendid[0] ? 1 : 0;
}

/* ------------------------------------------------------------------ cold receive / open */

static void cold_mark_done(ColdClaim *c, ULONGLONG release_delay_ms) {
    if (!c) return;
    InterlockedExchange(&c->phase, COLD_PHASE_DONE);
    c->expire_after_tick = 0;
    c->release_after_tick = GetTickCount64() + release_delay_ms;
}

static int cold_try_begin_open(ColdClaim *c) {
    LONG phase;
    if (!c) return 0;
    for (;;) {
        phase = InterlockedCompareExchange(&c->phase, 0, 0);
        if (phase == COLD_PHASE_OPENING || phase == COLD_PHASE_OPEN_LAUNCHED ||
            phase == COLD_PHASE_DONE) return 0;
        if (phase != COLD_PHASE_BUILT && phase != COLD_PHASE_RECV_LAUNCHING &&
            phase != COLD_PHASE_RECV_LAUNCHED && phase != COLD_PHASE_PENDING_OPEN)
            return 0;
        if (InterlockedCompareExchange(&c->phase, COLD_PHASE_OPENING, phase) == phase)
            return 1;
    }
}

static void cold_do_launch_recv(ColdClaim *c) {
    int64_t base = (int64_t)wxb_weixin_module();
    FnLaunch launch;
    if (!c || !base || !c->parent_recv) return;
    if (!c->recv_a2) c->recv_a2 = cold_zalloc(WX_HB_COLD_A2_SIZE);
    if (!c->recv_a2) {
        cold_log("launch-error", "a2 alloc failed");
        cold_mark_done(c, COLD_RELEASE_RECV_MS);
        return;
    }
    ZeroMemory(c->recv_a2, WX_HB_COLD_A2_SIZE);
    launch = (FnLaunch)(base + WX_OFF_HB_RECEIVE_TASK_BUILDER);
    c->launch_begin_tick = GetTickCount64();
    c->launch_thread_id = GetCurrentThreadId();
    InterlockedExchange64(&c->receive_task_ctx, 0);
    __try {
        launch(c->parent_recv, (int64_t)c->recv_a2);
        c->launch_tick = GetTickCount64();
        if (wxb_hb_cold_open_enabled()) {
            (void)InterlockedCompareExchange(&c->phase,
                                             COLD_PHASE_PENDING_OPEN,
                                             COLD_PHASE_RECV_LAUNCHING);
            c->expire_after_tick = c->launch_tick + COLD_PIN_TTL_MS;
            cold_logf("COLD-RECEIVE",
                      "Launch_receivewxhb(0x4A64BB0) → 1581 sendId=%s claim_to_launch_ms=%I64u launch_call_ms=%I64u (awaiting timing)",
                      c->sendid,
                      c->claimed_tick ? (c->launch_tick - c->claimed_tick) : 0ULL,
                      c->launch_begin_tick ? (c->launch_tick - c->launch_begin_tick) : 0ULL);
        } else {
            if (InterlockedCompareExchange(&c->phase,
                                           COLD_PHASE_DONE,
                                           COLD_PHASE_RECV_LAUNCHING) == COLD_PHASE_RECV_LAUNCHING) {
                c->expire_after_tick = 0;
                c->release_after_tick = c->launch_tick + COLD_RELEASE_RECV_MS;
            }
            cold_logf("COLD-RECEIVE",
                      "Launch_receivewxhb(0x4A64BB0) → 1581 sendId=%s",
                      c->sendid);
        }
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        cold_log("launch-error", "exception in Launch_receive");
        cold_mark_done(c, COLD_RELEASE_RECV_MS);
    }
}

static void cold_do_open_build_and_launch(ColdClaim *c, const char *timing) {
    int64_t base = (int64_t)wxb_weixin_module();
    int64_t svc, cgi, parent = 0;
    FnServiceOpen service_open;
    FnFactoryOpen factory_open;
    BYTE *tstr = NULL;
    char parent_timing[48] = {0};
    char rb[48] = {0};

    if (!c || !base || !timing || !timing[0] || !wxb_hb_cold_open_enabled()) {
        cold_mark_done(c, COLD_RELEASE_RECV_MS);
        return;
    }
    if (!c->msgctx) {
        cold_log("ABORT", "open: msgCtx gone");
        cold_mark_done(c, COLD_RELEASE_RECV_MS);
        return;
    }
    svc = (int64_t)InterlockedCompareExchange64(&g_hb_service, 0, 0);
    cgi = (int64_t)InterlockedCompareExchange64(&g_cgi_table, 0, 0);
    if (!svc) svc = cold_ensure_service();
    cgi = (int64_t)InterlockedCompareExchange64(&g_cgi_table, 0, 0);
    if (!svc && !cgi) {
        cold_log("ABORT", "open: no service/cgiTable");
        cold_mark_done(c, COLD_RELEASE_RECV_MS);
        return;
    }

    if (!c->open_out) c->open_out = cold_zalloc(WX_HB_COLD_OUT_SIZE);
    if (!c->open_out) {
        cold_log("ABORT", "open: out alloc failed");
        cold_mark_done(c, COLD_RELEASE_RECV_MS);
        return;
    }
    ZeroMemory(c->open_out, WX_HB_COLD_OUT_SIZE);

    tstr = cold_make_std_string(timing, c);
    if (!tstr) {
        cold_log("ABORT", "makeStdString failed");
        cold_mark_done(c, COLD_RELEASE_RECV_MS);
        return;
    }
    hb_read_functional_string((int64_t)tstr, rb, sizeof(rb));
    if (!str_eq(rb, timing)) {
        cold_logf("ABORT", "makeStdString read-back failed: %s", rb);
        cold_mark_done(c, COLD_RELEASE_RECV_MS);
        return;
    }
    cold_logf("OPEN-BUILD", "sendId=%s timing=%s via=%s",
              c->sendid, timing, svc ? "Service_StartOpen" : "Factory_open");

    service_open = (FnServiceOpen)(base + WX_OFF_HB_SERVICE_START_OPEN);
    factory_open = (FnFactoryOpen)(base + WX_OFF_HB_MID_CTX_FROM_TIMING);
    __try {
        if (svc) service_open(svc, (int64_t)c->open_out, (int64_t)c->msgctx, (int64_t)tstr);
        else factory_open(cgi, (int64_t)c->open_out, (int64_t)c->msgctx, (int64_t)tstr);
        parent = r64(c->open_out + 0x38);
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        cold_log("open-error", "exception in Service/Factory open");
        cold_mark_done(c, COLD_RELEASE_RECV_MS);
        return;
    }
    if (!cold_is_heap(parent)) {
        cold_log("ABORT", "open factory parent null");
        cold_mark_done(c, COLD_RELEASE_RECV_MS);
        return;
    }
    c->parent_open = parent;

    hb_read_functional_string(parent + WX_HB_OPEN_PARENT_TIMING_OFF, parent_timing, sizeof(parent_timing));
    cold_logf("FACTORY-OPEN", "sendId=%s parent=%I64x parentTiming=%s ok=%d",
              c->sendid, parent, parent_timing, str_eq(parent_timing, timing) ? 1 : 0);

    /* Safety net: if Factory left timing empty, write parent+0x390 ourselves. */
    if (!str_eq(parent_timing, timing)) {
        int ok = cold_write_std_string(parent + WX_HB_OPEN_PARENT_TIMING_OFF, timing, c);
        ZeroMemory(parent_timing, sizeof(parent_timing));
        hb_read_functional_string(parent + WX_HB_OPEN_PARENT_TIMING_OFF, parent_timing, sizeof(parent_timing));
        cold_logf("TIMING-PATCH", "writeOk=%d parentTimingNow=%s ok=%d",
                  ok, parent_timing, str_eq(parent_timing, timing) ? 1 : 0);
        if (!str_eq(parent_timing, timing)) {
            cold_log("ABORT", "timing patch failed — not launching open");
            cold_mark_done(c, COLD_RELEASE_RECV_MS);
            return;
        }
    }

    /* Same pool thread: ServiceOpen then Launch_open (no second queue hop). */
    cold_do_launch_open(c, parent, timing);
}

static void cold_do_launch_open(ColdClaim *c, int64_t parent, const char *timing) {
    int64_t base = (int64_t)wxb_weixin_module();
    FnLaunch launch_open;
    char tnow[48] = {0};
    if (!c || !base || !parent) return;
    if (!c->open_a2) c->open_a2 = cold_zalloc(WX_HB_COLD_A2_SIZE);
    if (!c->open_a2) {
        cold_mark_done(c, COLD_RELEASE_RECV_MS);
        return;
    }
    ZeroMemory(c->open_a2, WX_HB_COLD_A2_SIZE);
    hb_read_functional_string(parent + WX_HB_OPEN_PARENT_TIMING_OFF, tnow, sizeof(tnow));
    if (timing && timing[0] && !str_eq(tnow, timing)) {
        cold_logf("ABORT", "timing lost before Launch: %s", tnow);
        cold_mark_done(c, COLD_RELEASE_RECV_MS);
        return;
    }
    launch_open = (FnLaunch)(base + WX_OFF_HB_OPEN_CTX_BUILDER);
    __try {
        launch_open(parent, (int64_t)c->open_a2);
        InterlockedExchange(&c->phase, COLD_PHASE_OPEN_LAUNCHED);
        c->opened_tick = GetTickCount64();
        c->release_after_tick = c->opened_tick + COLD_RELEASE_OPEN_MS;
        cold_logf("COLD-OPEN",
                  "deferred Launch_openwxhb(0x4A6C0A0) → 1685 sendId=%s timing=%s",
                  c->sendid, timing ? timing : "");
        cold_record(c, "submitted", -1, -1, -1, "open_request_submitted");
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        cold_log("open-launch-error", "exception");
        cold_mark_done(c, COLD_RELEASE_RECV_MS);
    }
}

static void cold_open_from_timing(const char *sendid, const char *timing, const char *status) {
    ColdClaim *c;
    if (!sendid || !sendid[0] || !timing || !timing[0]) return;
    if (!cold_is_hex32(timing)) {
        c = cold_claim_acquire_by_sendid(sendid);
        if (c) cold_logf("open-skip", "no 32-hex timing; status=%s sendId=%s",
                         status ? status : "", sendid);
        cold_claim_release(c);
        return;
    }
    if (status && status[0] && !str_eq(status, "ok")) {
        c = cold_claim_acquire_by_sendid(sendid);
        if (c) {
            cold_logf("open-skip", "receive status not ok: %s sendId=%s", status, sendid);
            if (cold_try_begin_open(c)) cold_mark_done(c, COLD_RELEASE_RECV_MS);
        }
        cold_claim_release(c);
        return;
    }
    c = cold_claim_acquire_by_sendid(sendid);
    if (!c) {
        cold_logf("open-skip", "no pending ctx for sendId=%s", sendid);
        return;
    }
    if (!cold_try_begin_open(c)) {
        LONG phase = InterlockedCompareExchange(&c->phase, 0, 0);
        cold_logf("open-skip", "claim phase=%ld sendId=%s", (long)phase, sendid);
        cold_claim_release(c);
        return;
    }
    safe_copy(c->timing, sizeof(c->timing), timing);
    /* Frida: setTimeout(30) then open; we use 0ms pool hop to leave ParseExtra fully. */
    if (!cold_enqueue_job(2, c, timing, COLD_OPEN_DELAY_MS)) {
        cold_logf("open-skip", "failed to queue open job sendId=%s", sendid);
        cold_mark_done(c, COLD_RELEASE_RECV_MS);
    }
    cold_claim_release(c);
}

static void cold_receive(int64_t msg, const char *sendid,
                         const char *from, const char *to, const char *talker) {
    int64_t base = (int64_t)wxb_weixin_module();
    int64_t svc, cgi, parent = 0, app_ext = 0, vt = 0;
    ColdClaim *c;
    const char *bad;
    FnFillFromLocalMsg fill;
    FnServiceRecv service_recv;
    FnFactoryRecv factory_recv;
    char dbg[512];

    if (!base || !msg || !sendid || !sendid[0]) return;

    /* Fast reject before any alloc/IO. */
    if (cold_claim_exists(sendid)) return;

    /* Prefer cached service; DI resolve only if needed (pre-warmed in refresh). */
    svc = (int64_t)InterlockedCompareExchange64(&g_hb_service, 0, 0);
    cgi = (int64_t)InterlockedCompareExchange64(&g_cgi_table, 0, 0);
    if (!svc && !cgi) {
        svc = cold_ensure_service();
        cgi = (int64_t)InterlockedCompareExchange64(&g_cgi_table, 0, 0);
    }
    if (!svc && !cgi) {
        cold_logf("ABORT", "no service/cgiTable sendId=%s", sendid);
        return;
    }

    bad = cold_validate_local_msg(msg);
    if (bad) {
        cold_logf("ABORT", "LocalMsg invalid: %s sendId=%s", bad, sendid);
        return;
    }

    c = cold_alloc_claim(sendid);
    if (!c) return; /* already claimed or no slot — silent on dup */
    if (from) safe_copy(c->from, sizeof(c->from), from);
    if (to) safe_copy(c->to, sizeof(c->to), to);
    if (talker) safe_copy(c->talker, sizeof(c->talker), talker);
    if (wxb_self_wxid_known() &&
        (str_eq(wxb_self_wxid(), c->to) || str_eq(wxb_self_wxid(), c->from)))
        safe_copy(c->account, sizeof(c->account), wxb_self_wxid());
    else if (is_chatroom_id(c->from) && is_wxid_like(c->to))
        safe_copy(c->account, sizeof(c->account), c->to);
    cold_record(c, "detected", -1, -1, -1, "automatic_claim_detected");

    c->msgctx = cold_zalloc(WX_HB_MSGCTX_SIZE);
    c->recv_out = cold_zalloc(WX_HB_COLD_OUT_SIZE);
    if (!c->msgctx || !c->recv_out) {
        cold_log("fill-error", "pin alloc failed");
        cold_claim_discard(c);
        return;
    }

    fill = (FnFillFromLocalMsg)(base + WX_OFF_MSG_TO_SEND_ITEM);
    __try {
        fill(msg, (int64_t)c->msgctx);
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        cold_log("fill-error", "exception in FillFromLocalMsg");
        cold_claim_discard(c);
        return;
    }

    app_ext = r64(c->msgctx + WX_HB_MSGCTX_APPEXT_OFF);
    vt = r64(c->msgctx);
    cold_log_v("MSGCTX", "vtable=%I64x vtableOk=%d appExt=%s",
               vt, vt == base + WX_OFF_HB_MSGCTX_VTABLE ? 1 : 0,
               cold_is_heap(app_ext) ? "heap" : "bad");
    if (!cold_is_heap(app_ext)) {
        cold_logf("ABORT",
                  "msgCtx app_ext@+0x270 not heap — skip sendId=%s",
                  sendid);
        cold_claim_discard(c);
        return;
    }

    service_recv = (FnServiceRecv)(base + WX_OFF_HB_SERVICE_START_RECV);
    factory_recv = (FnFactoryRecv)(base + WX_OFF_HB_RECEIVE_MID_BUILDER);
    ZeroMemory(c->recv_out, WX_HB_COLD_OUT_SIZE);
    __try {
        if (svc) service_recv(svc, (int64_t)c->recv_out, (int64_t)c->msgctx);
        else factory_recv(cgi, (int64_t)c->recv_out, (int64_t)c->msgctx);
        parent = r64(c->recv_out + 0x38);
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        cold_log("factory-error", "exception in Service/Factory receive");
        cold_claim_discard(c);
        return;
    }
    if (!cold_is_heap(parent)) {
        cold_log("ABORT", "factory parent null");
        cold_claim_discard(c);
        return;
    }
    c->parent_recv = parent;
    InterlockedExchange(&c->phase, COLD_PHASE_BUILT);
    c->expire_after_tick = GetTickCount64() + COLD_PIN_TTL_MS;

    /*
     * Fire 1581 NOW on a pool thread, concurrent with the source_submit body
     * (AddMessageListToDB) — true Frida parity (setTimeout(0) on its own JS
     * thread at +0). Measured: the receive server does NOT require the message
     * committed first (Frida launches pre-commit and still gets status=ok in
     * ~160ms), so overlapping the launch with the DB write removes the ~15-90ms
     * orig wait from the critical path → since_claim ~160ms instead of ~250ms.
     *
     * The old "launch early → 2s stall" was NOT this: it was the response being
     * starved on the logic thread while a synchronous failed HTTP push blocked it
     * ~2s (now offloaded — see recv_push_async). With that gone, the completion
     * (pinned to the logic thread) is serviced promptly. flush_launches() remains
     * the fallback for any claim whose eager enqueue lost the pool; the atomic
     * BUILT→RECV_LAUNCHING CAS guarantees a single launch.
     */
    {
        DWORD delay_ms = cold_pick_delay_ms();
        int queued = cold_enqueue_job(1, c, NULL, (ULONGLONG)delay_ms);
        cold_logf("FACTORY-RECV", "sendId=%s parent=%I64x via=%s (%s)",
                  sendid, parent, svc ? "Service" : "Factory",
                  queued ? "eager pool launch" : "await flush");
    }
    cold_claim_release(c);
    (void)dbg;
}

/* Launch all claims left in BUILT after original RECV returns (Frida setTimeout(0)). */
void wxb_hb_cold_flush_launches(void) {
    int i;
    DWORD delay_ms;
    if (!wxb_hb_cold_enabled()) return;
    delay_ms = cold_pick_delay_ms();

    for (i = 0; i < COLD_CLAIM_SLOTS; i++) {
        ColdClaim *c = &g_claims[i];
        LONG gen;
        if (!InterlockedCompareExchange(&c->in_use, 0, 0)) continue;
        if (InterlockedCompareExchange(&c->phase, 0, 0) != COLD_PHASE_BUILT) continue;
        gen = InterlockedCompareExchange(&c->generation, 0, 0);
        if (!cold_claim_add_ref(c, gen)) continue;

        if (InterlockedCompareExchange(&c->phase,
                                       COLD_PHASE_RECV_LAUNCHING,
                                       COLD_PHASE_BUILT) != COLD_PHASE_BUILT) {
            cold_claim_release(c);
            continue;
        }

        cold_logf("FLUSH-LAUNCH", "sendId=%s delay_ms=%lu cfg=[%ld,%ld]",
                  c->sendid,
                  (unsigned long)delay_ms,
                  (long)InterlockedCompareExchange(&g_delay_min_ms, 0, 0),
                  (long)InterlockedCompareExchange(&g_delay_max_ms, 0, 0));

        if (delay_ms == 0) {
            /* After original RECV (AddMessageListToDB) returns — Frida setTimeout(0). */
            cold_do_launch_recv(c);
            cold_claim_release(c);
        } else {
            /* Delayed: hand to the pool. Undo the pre-transition first so the
             * job's own BUILT→RECV_LAUNCHING CAS succeeds (else it would no-op). */
            InterlockedCompareExchange(&c->phase, COLD_PHASE_BUILT, COLD_PHASE_RECV_LAUNCHING);
            if (!cold_enqueue_job(1, c, NULL, (ULONGLONG)delay_ms)) {
                cold_logf("launch-error", "failed to queue receive job sendId=%s", c->sendid);
                cold_mark_done(c, COLD_RELEASE_RECV_MS);
            }
            cold_claim_release(c);
        }
    }
}

/* ------------------------------------------------------------------ single-msg / vector claim */

static int cold_try_claim_local_msg_via(int64_t msg, const char *via) {
    char sendid[128] = {0};
    char from[128] = {0}, to[128] = {0}, talker[128] = {0};
    char native_url[1024] = {0};
    if (!wxb_hb_cold_enabled()) return 0;
    if (!msg) return 0;
    /* Fast path: no VirtualQuery; SEH inside parse/receive. */
    __try {
        if (!cold_parse_hb_msg(msg, sendid, sizeof(sendid),
                               from, sizeof(from), to, sizeof(to),
                               talker, sizeof(talker),
                               native_url, sizeof(native_url))) return 0;
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        return 0;
    }
    if (!sendid[0]) return 0;
    if (cold_claim_exists(sendid)) return 0; /* already in-flight / done */
    cold_logf("RECV-HB", "sendId=%s from=%s via=%s",
              sendid, from, via ? via : "unknown");
    cold_receive(msg, sendid, from, to, talker);
    return 1;
}

int wxb_hb_cold_try_claim_local_msg(int64_t msg) {
    return cold_try_claim_local_msg_via(msg, "doaddmsg");
}

int wxb_hb_cold_scan_source_vector(LONG hit, int64_t a1, int64_t vector_arg) {
    int64_t begin = 0, endp = 0, delta = 0;
    int count = 0, triggered = 0;
    int i;
    (void)a1;
    (void)hit;

    if (!wxb_hb_cold_enabled()) return 0;
    if (!vector_arg || !is_readable_ptr(vector_arg, 0x18)) return 0;
    begin = r64((void *)vector_arg);
    endp = r64((void *)(vector_arg + 8));
    if (!begin || !endp || endp <= begin) return 0;
    delta = endp - begin;
    if (delta <= 0 || delta > 0x290 * 64 || (delta % 0x290) != 0) return 0;
    count = (int)(delta / 0x290);
    if (count < 1) count = 1;
    if (count > 64) count = 64;

    for (i = 0; i < count; i++) {
        int64_t msg = begin + (int64_t)i * 0x290;
        char sendid[128] = {0};
        char from[128] = {0}, to[128] = {0}, talker[128] = {0};
        char native_url[1024] = {0};
        if (!is_readable_ptr(msg, HB_SOURCE_SIZE)) continue;
        if (!cold_parse_hb_msg(msg, sendid, sizeof(sendid),
                               from, sizeof(from), to, sizeof(to),
                               talker, sizeof(talker),
                               native_url, sizeof(native_url))) continue;
        if (!sendid[0]) continue;
        /* Only log when this is a NEW claim — duplicate vector scans used to
         * print a second RECV-HB next to HB-RES and looked like a late start. */
        if (cold_claim_exists(sendid)) continue;
        cold_logf("RECV-HB", "sendId=%s from=%s via=vector", sendid, from);
        cold_receive(msg, sendid, from, to, talker);
        triggered = 1;
    }
    return triggered;
}

/* ------------------------------------------------------------------ newsync fast lane */

static int cold_raw_addmsg_extract_hongbao(int64_t raw,
                                           char *sendid, int sendid_max) {
    int64_t content_holder;
    int64_t content_string;
    char content[4096] = {0};
    char native_url[1024] = {0};
    char page_url[512] = {0};
    char channelid[64] = {0}, msgtype[32] = {0};
    char sendusername[128] = {0}, sign[128] = {0}, total_num[32] = {0};
    const char *body;
    const char *nl;

    if (!raw || !sendid || sendid_max <= 0) return 0;
    sendid[0] = 0;
    __try {
        /* IDA 1809D1810: raw+0x20 -> holder, holder+0x08 -> content std::string. */
        content_holder = r64((void *)(raw + 0x20));
        if (!cold_is_heap(content_holder)) return 0;
        content_string = r64((void *)(content_holder + 0x08));
        if (!cold_is_heap(content_string)) return 0;
        hb_read_functional_string(content_string, content, sizeof(content));
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        return 0;
    }
    if (!content[0]) return 0;

    body = content;
    nl = str_find_sub(content, ":\n");
    if (!nl) nl = str_find_sub(content, ":\r\n");
    if (nl && nl - content < 80) body = nl + ((nl[1] == '\r') ? 3 : 2);

    if (!str_has(body, "<type>2001</type>") &&
        !str_has(body, "<type><![CDATA[2001]]></type>") &&
        !str_has(body, "wcpayinfo") &&
        !str_has(body, "receivehongbao")) return 0;

    hb_extract_urls_from_text(body, native_url, sizeof(native_url),
                              page_url, sizeof(page_url));
    hb_fill_query_fields(native_url, page_url,
                         sendid, sendid_max,
                         channelid, sizeof(channelid),
                         msgtype, sizeof(msgtype),
                         sendusername, sizeof(sendusername),
                         sign, sizeof(sign),
                         total_num, sizeof(total_num));
    if (!sendid[0])
        extract_between_markers(body, "<sendid>", "</sendid>", sendid, sendid_max);
    if (!sendid[0])
        extract_between_markers(body, "<sendid><![CDATA[", "]]></sendid>",
                                sendid, sendid_max);
    return sendid[0] ? 1 : 0;
}

static int cold_fastlane_claim_raw(int64_t raw, const char *expected_sendid) {
    int64_t base = (int64_t)wxb_weixin_module();
    FnRawToLocalMsg raw_to_local;
    FnLocalMsgFinalize finalize;
    FnLocalMsgDtor dtor;
    BYTE *local_msg;
    int constructed = 0;
    int triggered = 0;

    if (!base || !raw || !expected_sendid || !expected_sendid[0]) return 0;
    if (cold_claim_exists(expected_sendid)) return 0;
    local_msg = (BYTE *)HeapAlloc(GetProcessHeap(), 0, HB_SOURCE_SIZE);
    if (!local_msg) return 0;
    FillMemory(local_msg, HB_SOURCE_SIZE, 0xAA);

    raw_to_local = (FnRawToLocalMsg)(base + WX_OFF_HB_RAW_TO_LOCAL_MSG);
    finalize = (FnLocalMsgFinalize)(base + WX_OFF_HB_LOCAL_MSG_FINALIZE);
    dtor = (FnLocalMsgDtor)(base + WX_OFF_HB_LOCAL_MSG_DTOR);
    __try {
        raw_to_local((int64_t)local_msg, raw);
        constructed = 1;
        finalize((int64_t)local_msg, 1);
        triggered = cold_try_claim_local_msg_via((int64_t)local_msg, "newsync");
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        cold_logf("fast-error", "raw conversion/claim fault sendId=%s", expected_sendid);
    }

    if (constructed) {
        __try {
            dtor((int64_t)local_msg);
        } __except (EXCEPTION_EXECUTE_HANDLER) {
            cold_logf("fast-error", "LocalMsg destructor fault sendId=%s", expected_sendid);
        }
    }
    HeapFree(GetProcessHeap(), 0, local_msg);
    return triggered;
}

static int cold_fastlane_scan_raw_vector(int64_t *vector) {
    int64_t begin;
    int64_t endp;
    int64_t delta;
    int count;
    int i;
    int triggered = 0;

    if (!cold_fastlane_enabled() || !vector ||
        !is_readable_ptr((int64_t)vector, 0x18)) return 0;
    __try {
        begin = vector[0];
        endp = vector[1];
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        return 0;
    }
    if (!begin || endp <= begin) return 0;
    delta = endp - begin;
    if (delta <= 0 || (delta % COLD_NEWSYNC_RAW_SIZE) != 0 ||
        delta > (int64_t)COLD_NEWSYNC_RAW_SIZE * COLD_NEWSYNC_MAX_ITEMS) return 0;
    count = (int)(delta / COLD_NEWSYNC_RAW_SIZE);
    if (!is_readable_ptr(begin, (SIZE_T)delta)) return 0;

    for (i = 0; i < count; i++) {
        int64_t raw = begin + (int64_t)i * COLD_NEWSYNC_RAW_SIZE;
        char sendid[128] = {0};
        ULONGLONG started;
        if (!cold_raw_addmsg_extract_hongbao(raw, sendid, sizeof(sendid))) continue;
        if (cold_claim_exists(sendid)) continue;
        started = GetTickCount64();
        if (cold_fastlane_claim_raw(raw, sendid)) {
            cold_logf("FASTLANE", "sendId=%s item=%d/%d build_ms=%I64u",
                      sendid, i + 1, count, GetTickCount64() - started);
            triggered++;
        }
    }
    return triggered;
}

static int64_t __fastcall cold_newsync_addmsg_hook(int64_t *vector, BYTE *ok,
                                                   char flag1, char flag2) {
    FnNewSyncAddMsgBatch orig = (FnNewSyncAddMsgBatch)g_newsync_addmsg_tramp;
    InterlockedIncrement(&g_newsync_addmsg_hit);
    if (cold_fastlane_enabled()) {
        __try {
            (void)cold_fastlane_scan_raw_vector(vector);
        } __except (EXCEPTION_EXECUTE_HANDLER) {
            cold_log("fast-error", "unexpected newsync scan fault");
        }
    }
    return orig ? orig(vector, ok, flag1, flag2) : 0;
}

static void install_hb_cold_newsync_hook(void) {
    static const BYTE expected[COLD_NEWSYNC_PATCH_LEN] = {
        0x55, 0x41, 0x56, 0x56, 0x57, 0x53, 0x48, 0x83,
        0xEC, 0x60, 0x48, 0x8D, 0x6C, 0x24, 0x60
    };
    BYTE *target;
    BYTE patch[COLD_NEWSYNC_PATCH_LEN];
    DWORD old = 0;
    int i;

    AcquireSRWLockExclusive(&g_newsync_addmsg_lock);
    if (g_newsync_addmsg_installed || !wxb_weixin_module() ||
        !cold_fastlane_enabled()) goto done;
    target = (BYTE *)((int64_t)wxb_weixin_module() + WX_OFF_HB_NEWSYNC_ADDMSG_BATCH);
    if (!is_readable_ptr((int64_t)target, COLD_NEWSYNC_PATCH_LEN) ||
        !bytes_equal(target, expected, COLD_NEWSYNC_PATCH_LEN)) {
        cold_log("fast-ready", "newsync entry bytes mismatch; hook refused");
        goto done;
    }

    copy_bytes(g_newsync_addmsg_orig, target, COLD_NEWSYNC_PATCH_LEN);
    if (!g_newsync_addmsg_tramp)
        g_newsync_addmsg_tramp = VirtualAlloc(NULL, 128, MEM_COMMIT | MEM_RESERVE,
                                              PAGE_EXECUTE_READWRITE);
    if (!g_newsync_addmsg_tramp) {
        cold_log("fast-ready", "newsync trampoline alloc failed");
        goto done;
    }
    {
        BYTE *p = (BYTE *)g_newsync_addmsg_tramp;
        copy_bytes(p, g_newsync_addmsg_orig, COLD_NEWSYNC_PATCH_LEN);
        p += COLD_NEWSYNC_PATCH_LEN;
        *p++ = 0x48; *p++ = 0xB8;
        *(int64_t *)p = (int64_t)(target + COLD_NEWSYNC_PATCH_LEN); p += 8;
        *p++ = 0xFF; *p++ = 0xE0;
        FlushInstructionCache(GetCurrentProcess(), g_newsync_addmsg_tramp, 64);
    }

    for (i = 0; i < COLD_NEWSYNC_PATCH_LEN; i++) patch[i] = 0x90;
    patch[0] = 0x48; patch[1] = 0xB8;
    *(int64_t *)(patch + 2) = (int64_t)cold_newsync_addmsg_hook;
    patch[10] = 0xFF; patch[11] = 0xE0;
    if (!VirtualProtect(target, COLD_NEWSYNC_PATCH_LEN,
                        PAGE_EXECUTE_READWRITE, &old)) {
        cold_log("fast-ready", "newsync VirtualProtect failed");
        goto done;
    }
    copy_bytes(target, patch, COLD_NEWSYNC_PATCH_LEN);
    VirtualProtect(target, COLD_NEWSYNC_PATCH_LEN, old, &old);
    FlushInstructionCache(GetCurrentProcess(), target, COLD_NEWSYNC_PATCH_LEN);
    InterlockedExchange(&g_newsync_addmsg_installed, 1);
    cold_log("fast-ready",
             "newsync fast lane installed at 0x2AE0890; source_submit fallback retained");
done:
    ReleaseSRWLockExclusive(&g_newsync_addmsg_lock);
}

static void uninstall_hb_cold_newsync_hook(void) {
    BYTE *target;
    DWORD old = 0;
    AcquireSRWLockExclusive(&g_newsync_addmsg_lock);
    if (!g_newsync_addmsg_installed || !wxb_weixin_module()) goto done;
    target = (BYTE *)((int64_t)wxb_weixin_module() + WX_OFF_HB_NEWSYNC_ADDMSG_BATCH);
    if (VirtualProtect(target, COLD_NEWSYNC_PATCH_LEN,
                       PAGE_EXECUTE_READWRITE, &old)) {
        copy_bytes(target, g_newsync_addmsg_orig, COLD_NEWSYNC_PATCH_LEN);
        VirtualProtect(target, COLD_NEWSYNC_PATCH_LEN, old, &old);
        FlushInstructionCache(GetCurrentProcess(), target, COLD_NEWSYNC_PATCH_LEN);
    }
    InterlockedExchange(&g_newsync_addmsg_installed, 0);
    /* Retain the trampoline: an in-flight hook may still call through it. */
done:
    ReleaseSRWLockExclusive(&g_newsync_addmsg_lock);
}

static int cold_install_receive_trace_one(ColdReceiveTraceHook *h) {
    BYTE *target;
    BYTE patch[COLD_RECV_TRACE_MAX_PATCH];
    DWORD old = 0;
    int i;

    if (!h || h->installed || !h->hook_proc || !wxb_weixin_module() ||
        h->patch_len < 12 || h->patch_len > COLD_RECV_TRACE_MAX_PATCH) return 0;
    target = (BYTE *)((int64_t)wxb_weixin_module() + h->rva);
    if (!is_readable_ptr((int64_t)target, (SIZE_T)h->patch_len) ||
        !bytes_equal(target, h->expected, h->patch_len)) return 0;
    copy_bytes(h->original, target, h->patch_len);
    if (!h->trampoline)
        h->trampoline = VirtualAlloc(NULL, 128, MEM_COMMIT | MEM_RESERVE,
                                     PAGE_EXECUTE_READWRITE);
    if (!h->trampoline) return 0;
    {
        BYTE *p = (BYTE *)h->trampoline;
        copy_bytes(p, h->original, h->patch_len);
        p += h->patch_len;
        *p++ = 0x48; *p++ = 0xB8;
        *(int64_t *)p = (int64_t)(target + h->patch_len); p += 8;
        *p++ = 0xFF; *p++ = 0xE0;
        FlushInstructionCache(GetCurrentProcess(), h->trampoline, 64);
    }
    for (i = 0; i < h->patch_len; i++) patch[i] = 0x90;
    patch[0] = 0x48; patch[1] = 0xB8;
    *(int64_t *)(patch + 2) = (int64_t)h->hook_proc;
    patch[10] = 0xFF; patch[11] = 0xE0;
    if (!VirtualProtect(target, h->patch_len, PAGE_EXECUTE_READWRITE, &old))
        return 0;
    copy_bytes(target, patch, h->patch_len);
    VirtualProtect(target, h->patch_len, old, &old);
    FlushInstructionCache(GetCurrentProcess(), target, h->patch_len);
    InterlockedExchange(&h->installed, 1);
    return 1;
}

static void cold_uninstall_receive_trace_one(ColdReceiveTraceHook *h) {
    BYTE *target;
    DWORD old = 0;
    int ours;

    if (!h || !h->installed || !wxb_weixin_module()) return;
    target = (BYTE *)((int64_t)wxb_weixin_module() + h->rva);
    ours = is_readable_ptr((int64_t)target, (SIZE_T)h->patch_len) &&
           target[0] == 0x48 && target[1] == 0xB8 &&
           *(int64_t *)(target + 2) == (int64_t)h->hook_proc &&
           target[10] == 0xFF && target[11] == 0xE0;
    if (!ours) {
        cold_logf("trace-ready", "%s hook ownership changed; restore refused", h->name);
        InterlockedExchange(&h->installed, 0);
        return;
    }
    if (!VirtualProtect(target, h->patch_len, PAGE_EXECUTE_READWRITE, &old)) {
        cold_logf("trace-ready", "%s VirtualProtect restore failed; hook remains installed",
                  h->name);
        return;
    }
    copy_bytes(target, h->original, h->patch_len);
    VirtualProtect(target, h->patch_len, old, &old);
    FlushInstructionCache(GetCurrentProcess(), target, h->patch_len);
    InterlockedExchange(&h->installed, 0);
    /* Retain trampoline and TLS: in-flight receive tasks may still return through them. */
}

static char __fastcall cold_open_result_observer(int64_t obj, BYTE *json) {
    FnParseExtra original = (FnParseExtra)g_open_result_hook.trampoline;
    char result = original ? original(obj, json) : 0;
    ColdClaim *claim = NULL;
    /* Observe the completed native parser. Do not invoke receive/open/query APIs. */
    __try {
        if (result && obj && is_readable_ptr(obj, 0x1C0)) {
            char sendid[128] = {0}, sender[128] = {0}, detail[192] = {0};
            int retcode, receive_status, amount = -1;
            int64_t begin, finish, entry;
            hb_read_functional_string(obj + 0x30, sendid, sizeof(sendid));
            claim = sendid[0] ? cold_claim_acquire_by_sendid(sendid) : NULL;
            if (claim && claim->account[0] && claim->parent_open) {
                retcode = *(int *)(obj + 0x08);
                receive_status = *(int *)(obj + 0x98);
                hb_read_functional_string(obj + 0x78, sender, sizeof(sender));
                hb_read_functional_string(obj + 0xA0, detail, sizeof(detail));
                if (!detail[0]) hb_read_functional_string(obj + 0x10, detail, sizeof(detail));
                if (sender[0]) safe_copy(claim->talker, sizeof(claim->talker), sender);
                if (retcode == 0 && receive_status == 2) {
                    int own_amount = *(int *)(obj + 0x158);
                    /* amount is this receiver's cents, NOT totalAmount/recAmount.
                     * Missing JSON fields default to zero; only a matching record
                     * can certify an actual zero-cent amount. */
                    if (own_amount > 0) amount = own_amount;
                    begin = r64((void *)(obj + 0x1A8));
                    finish = r64((void *)(obj + 0x1B0));
                    if (begin && finish >= begin && (finish - begin) % 0x110 == 0 &&
                        finish - begin <= 1000 * 0x110 && is_readable_ptr(begin, (SIZE_T)(finish - begin))) {
                        for (entry = begin; entry < finish; entry += 0x110) {
                            char user[128] = {0};
                            int received;
                            hb_read_functional_string(entry + 0xB0, user, sizeof(user));
                            if (!str_eq(user, claim->account)) continue;
                            received = *(int *)(entry + 0x40);
                            if (received >= 0 && (amount < 0 || amount == received)) amount = received;
                            else { amount = -1; safe_copy(detail, sizeof(detail), "amount_evidence_conflict"); }
                            break;
                        }
                    }
                }
                cold_record(claim, "result", amount, retcode, receive_status, detail);
            }
        }
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        cold_log("record-error", "open-result read failed; amount not certified");
    }
    cold_claim_release(claim);
    return result;
}

static void cold_refresh_result_observer(void) {
    static ULONGLONG last_report = 0;
    ULONGLONG now = GetTickCount64();
    WxbRealtimeRelayStatus relay;
    AcquireSRWLockExclusive(&g_open_result_lock);
    g_open_result_hook.hook_proc = (void *)cold_open_result_observer;
    if (!g_open_result_hook.installed && wxb_weixin_module()) {
        if (cold_install_receive_trace_one(&g_open_result_hook))
            cold_log("result-ready", "v1 passive open-result observer installed; amounts in cents");
    }
    ReleaseSRWLockExclusive(&g_open_result_lock);
    if (!last_report || now - last_report >= 10000ULL) {
        wxb_realtime_relay_get_status(&relay);
        if (relay.running) {
            cold_record(NULL, "observer", -1, -1, -1,
                        g_open_result_hook.installed ? "ready" : "unsupported_parser_bytes");
            last_report = now;
        }
    }
}

static void install_hb_cold_receive_trace_hooks(void) {
    int i;
    int ok = 1;
    int missing = 0;

    AcquireSRWLockExclusive(&g_recv_trace_lock);
    if (!cold_fastlane_enabled() || !wxb_weixin_module()) goto done;
    g_recv_trace_hooks[0].hook_proc = (void *)cold_receive_task_trace_hook;
    g_recv_trace_hooks[1].hook_proc = (void *)cold_receive_request_loop_trace_hook;
    g_recv_trace_hooks[2].hook_proc = (void *)cold_receive_attempt_trace_hook;
    for (i = 0; i < COLD_RECV_TRACE_HOOKS; i++) {
        if (!g_recv_trace_hooks[i].installed) {
            missing = 1;
            break;
        }
    }
    if (!missing) goto done;
    if (g_recv_trace_tls == TLS_OUT_OF_INDEXES)
        g_recv_trace_tls = TlsAlloc();
    if (g_recv_trace_tls == TLS_OUT_OF_INDEXES) {
        cold_log("trace-ready", "receive timing TLS allocation failed; hooks refused");
        goto done;
    }
    for (i = 0; i < COLD_RECV_TRACE_HOOKS; i++) {
        if (g_recv_trace_hooks[i].installed) continue;
        if (!cold_install_receive_trace_one(&g_recv_trace_hooks[i])) {
            cold_logf("trace-ready", "%s entry bytes mismatch or install failed; timing hooks refused",
                      g_recv_trace_hooks[i].name);
            ok = 0;
            break;
        }
    }
    if (!ok) {
        for (i = COLD_RECV_TRACE_HOOKS - 1; i >= 0; i--)
            cold_uninstall_receive_trace_one(&g_recv_trace_hooks[i]);
    } else {
        cold_log("trace-ready",
                 "receive timing installed: task 0x4A65050 | request-loop 0x4A68430 | attempt 0x4A68E80");
    }
done:
    ReleaseSRWLockExclusive(&g_recv_trace_lock);
}

static void uninstall_hb_cold_receive_trace_hooks(void) {
    int i;
    AcquireSRWLockExclusive(&g_recv_trace_lock);
    for (i = COLD_RECV_TRACE_HOOKS - 1; i >= 0; i--)
        cold_uninstall_receive_trace_one(&g_recv_trace_hooks[i]);
    ReleaseSRWLockExclusive(&g_recv_trace_lock);
}

/* ------------------------------------------------------------------ ParseExtra hook */

static char __fastcall cold_parse_extra_hook(int64_t obj, BYTE *json) {
    FnParseExtra orig = (FnParseExtra)g_parse_extra_tramp;
    char ret = 0;
    LONG hit = InterlockedIncrement(&g_parse_extra_hit);
    (void)hit;

    __try {
        if (orig) ret = orig(obj, json);
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        cold_log("CRASH", "exception in ParseExtra original — returning 0");
        return 0;
    }

    /* ONLY read + schedule open. Never call Service/Factory/Launch here.
     * Frida uses onLeave — same: after ParseExtra fills fields. */
    if (wxb_hb_cold_enabled() && wxb_hb_cold_open_enabled() && obj) {
        char sendid[128] = {0};
        char timing[48] = {0};
        char status[64] = {0};
        __try {
            hb_read_functional_string(obj + WX_HB_RES_SENDID_OFF, sendid, sizeof(sendid));
            hb_read_functional_string(obj + WX_HB_RES_TIMING_OFF, timing, sizeof(timing));
            hb_read_functional_string(obj + WX_HB_RES_STATUS_OFF, status, sizeof(status));
            {
                ColdClaim *pc = sendid[0] ? cold_claim_acquire_by_sendid(sendid) : NULL;
                ULONGLONG now = GetTickCount64();
                ULONGLONG since_claim = 0, since_launch = 0;
                if (pc && pc->claimed_tick) since_claim = now - pc->claimed_tick;
                if (pc && pc->launch_tick) since_launch = now - pc->launch_tick;
                cold_logf("HB-RES",
                          "sendId=%s timing=%s status=%s since_claim_ms=%I64u since_launch_ms=%I64u",
                          sendid, timing, status, since_claim, since_launch);
                cold_claim_release(pc);
            }
            if (sendid[0]) cold_open_from_timing(sendid, timing, status);
        } __except (EXCEPTION_EXECUTE_HANDLER) {
            cold_log("res-error", "exception reading HB-RES / scheduling open");
        }
    }
    return ret;
}

void install_hb_cold_parse_extra_hook(void) {
    BYTE *target;
    BYTE patch[16];
    DWORD old = 0;
    int len = 12;
    AcquireSRWLockExclusive(&g_parse_extra_lock);
    if (g_parse_extra_installed || !wxb_weixin_module()) goto done;
    if (!wxb_hb_cold_enabled()) goto done;

    target = (BYTE *)((int64_t)wxb_weixin_module() + WX_OFF_HB_PARSE_EXTRA);
    if (!is_readable_ptr((int64_t)target, 24)) {
        cold_log("ready", "ParseExtra target unreadable");
        goto done;
    }
    /* Already our hook? */
    if (target[0] == 0x48 && target[1] == 0xB8 && target[10] == 0xFF && target[11] == 0xE0)
        goto done;

    copy_bytes(g_parse_extra_orig, target, len);
    g_parse_extra_orig_len = len;
    if (!g_parse_extra_tramp)
        g_parse_extra_tramp = VirtualAlloc(NULL, 256, MEM_COMMIT | MEM_RESERVE,
                                           PAGE_EXECUTE_READWRITE);
    if (!g_parse_extra_tramp) {
        cold_log("ready", "ParseExtra trampoline alloc failed");
        goto done;
    }
    {
        BYTE *p = (BYTE *)g_parse_extra_tramp;
        copy_bytes(p, g_parse_extra_orig, len);
        p += len;
        *p++ = 0x48; *p++ = 0xB8;
        *(int64_t *)p = (int64_t)(target + len); p += 8;
        *p++ = 0xFF; *p++ = 0xE0;
        FlushInstructionCache(GetCurrentProcess(), g_parse_extra_tramp, 64);
    }

    {
        int pi;
        for (pi = 0; pi < len; pi++) patch[pi] = 0x90;
    }
    patch[0] = 0x48; patch[1] = 0xB8;
    *(int64_t *)(patch + 2) = (int64_t)cold_parse_extra_hook;
    patch[10] = 0xFF; patch[11] = 0xE0;

    VirtualProtect(target, len, PAGE_EXECUTE_READWRITE, &old);
    copy_bytes(target, patch, len);
    VirtualProtect(target, len, old, &old);
    FlushInstructionCache(GetCurrentProcess(), target, len);
    InterlockedExchange(&g_parse_extra_installed, 1);
    cold_log("ready",
             "cold path installed. MODE=cold OPEN=1 POST-RECV=1"
             " | original RECV first → build+Launch_receive 0x4A64BB0 (1581)"
             " | ParseExtra → pool Launch_open 0x4A6C0A0 (1685)"
             " | avoids 2s stall from pre-RECV ServiceRecv");
done:
    ReleaseSRWLockExclusive(&g_parse_extra_lock);
}

void uninstall_hb_cold_parse_extra_hook(void) {
    BYTE *target;
    DWORD old = 0;
    AcquireSRWLockExclusive(&g_parse_extra_lock);
    if (!g_parse_extra_installed || !wxb_weixin_module()) goto done;
    target = (BYTE *)((int64_t)wxb_weixin_module() + WX_OFF_HB_PARSE_EXTRA);
    if (g_parse_extra_orig_len > 0) {
        VirtualProtect(target, g_parse_extra_orig_len, PAGE_EXECUTE_READWRITE, &old);
        copy_bytes(target, g_parse_extra_orig, g_parse_extra_orig_len);
        VirtualProtect(target, g_parse_extra_orig_len, old, &old);
        FlushInstructionCache(GetCurrentProcess(), target, g_parse_extra_orig_len);
    }
    InterlockedExchange(&g_parse_extra_installed, 0);
    /* A thread may already have jumped here; retain and reuse the 256-byte page. */
done:
    ReleaseSRWLockExclusive(&g_parse_extra_lock);
}

void wxb_hb_cold_refresh(void) {
    cold_refresh_result_observer();
    cold_refresh_control_flags();
    cold_refresh_verbose_flag();
    cold_reload_delay_config();
    if (!wxb_hb_cold_enabled()) {
        uninstall_hb_cold_newsync_hook();
        uninstall_hb_cold_receive_trace_hooks();
        uninstall_hb_cold_parse_extra_hook();
        cold_stop_worker();
        return;
    }
    if (!wxb_weixin_module()) return;
    (void)cold_ensure_worker();
    /* Pre-warm DI so first red packet does not pay resolve cost on the RECV stack. */
    (void)cold_ensure_service();
    if (cold_fastlane_enabled()) {
        install_hb_cold_newsync_hook();
        install_hb_cold_receive_trace_hooks();
    } else {
        uninstall_hb_cold_newsync_hook();
        uninstall_hb_cold_receive_trace_hooks();
    }
    install_hb_cold_parse_extra_hook();
}

void wxb_hb_cold_shutdown(void) {
    AcquireSRWLockExclusive(&g_open_result_lock);
    cold_uninstall_receive_trace_one(&g_open_result_hook);
    ReleaseSRWLockExclusive(&g_open_result_lock);
    InterlockedExchange(&g_cold_enabled, 0);
    InterlockedExchange(&g_cold_open_enabled, 0);
    InterlockedExchange(&g_cold_fastlane_enabled, 0);
    uninstall_hb_cold_newsync_hook();
    uninstall_hb_cold_receive_trace_hooks();
    uninstall_hb_cold_parse_extra_hook();
    cold_stop_worker();
}

void wxb_hb_cold_poll(void) {
    static ULONGLONG s_last_sweep = 0;
    ULONGLONG now;
    wxb_hb_cold_refresh();
    cold_process_jobs();
    now = GetTickCount64();
    /* Sweep pins every 5s — not on every RECV (was on hot path before). */
    if (!s_last_sweep || now - s_last_sweep > 5000ULL) {
        cold_sweep_claims();
        s_last_sweep = now;
    }
}
