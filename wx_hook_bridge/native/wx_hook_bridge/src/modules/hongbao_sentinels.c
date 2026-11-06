/*
 * Module: hongbao sentinel probe hook table and route candidate probe
 */

#include "../include/wx_bridge_debug_log.h"
#include "../include/wx_bridge_hongbao_closed_loop.h"
#include "../include/wx_bridge_hongbao_entrypoints.h"
#include "../include/wx_bridge_hongbao_hooks.h"
#include "../include/wx_bridge_hongbao_runtime.h"
#include "../include/wx_bridge_hongbao_trace.h"
#include "../include/wx_bridge_memory.h"
#include "../include/wx_bridge_offsets.h"
#include "../include/wx_bridge_protocol.h"
#include "../include/wx_bridge_runtime.h"
#include "../include/wx_bridge_text.h"
#define HB_SENTINEL_MAX_PATCH 24
typedef struct HbSentinelHook {
    const char *name;
    DWORD rva;
    int patch_len;
    BYTE expected[HB_SENTINEL_MAX_PATCH];
    BYTE orig[HB_SENTINEL_MAX_PATCH];
    void *trampoline;
    volatile LONG installed;
    volatile LONG hit;
} HbSentinelHook;

static HbSentinelHook g_hb_sentinels[] = {
    {
        "card_route_outer_185EA85A0", WX_OFF_HB_CARD_ROUTE_OUTER, 13,
        {0x55,0x41,0x56,0x56,0x57,0x53,0x48,0x81,0xEC,0x90,0x00,0x00,0x00},
        {0}, NULL, 0, 0
    },
    {
        "card_route_dispatch_185EA6830", WX_OFF_HB_CARD_ROUTE_DISPATCH, 18,
        {0x55,0x56,0x57,0x48,0x81,0xEC,0x30,0x02,0x00,0x00,0x48,0x8D,0xAC,0x24,0x80,0x00,0x00,0x00},
        {0}, NULL, 0, 0
    },
    {
        "weakptr_forward_183A0B780", WX_OFF_HB_WEAKPTR_FORWARD, 13,
        {0x55,0x56,0x57,0x53,0x48,0x83,0xEC,0x58,0x48,0x8D,0x6C,0x24,0x50},
        {0}, NULL, 0, 0
    },
    {
        "pagectx_builder_181B32480", WX_OFF_HB_PAGECTX_BUILDER, 15,
        {0x55,0x41,0x56,0x56,0x57,0x53,0x48,0x83,0xEC,0x70,0x48,0x8D,0x6C,0x24,0x70},
        {0}, NULL, 0, 0
    },
    {
        "receivewxhb_entry_184A65050", WX_OFF_HB_RECEIVEWXHB_ENTRY, 19,
        {0x55,0x41,0x57,0x41,0x56,0x41,0x55,0x41,0x54,0x56,0x57,0x53,0x48,0x81,0xEC,0xD8,0x06,0x00,0x00},
        {0}, NULL, 0, 0
    },
    {
        "mid_ctx_from_timing_184A607B0", WX_OFF_HB_MID_CTX_FROM_TIMING, 17,
        {0x55,0x41,0x57,0x41,0x56,0x41,0x54,0x56,0x57,0x53,0x48,0x81,0xEC,0xB0,0x04,0x00,0x00},
        {0}, NULL, 0, 0
    },
    {
        "open_ctx_builder_184A6C0A0", WX_OFF_HB_OPEN_CTX_BUILDER, 19,
        {0x55,0x41,0x57,0x41,0x56,0x41,0x55,0x41,0x54,0x56,0x57,0x53,0x48,0x81,0xEC,0x68,0x05,0x00,0x00},
        {0}, NULL, 0, 0
    },
    {
        "openwxhb_entry_184A6CA70", WX_OFF_HB_OPENWXHB_ENTRY, 19,
        {0x55,0x41,0x57,0x41,0x56,0x41,0x55,0x41,0x54,0x56,0x57,0x53,0x48,0x81,0xEC,0xE8,0x06,0x00,0x00},
        {0}, NULL, 0, 0
    }
};

static int hb_sentinel_count(void) {
    return (int)(sizeof(g_hb_sentinels) / sizeof(g_hb_sentinels[0]));
}

static void hb_log_sentinel(HbSentinelHook *h, int64_t a1, int64_t a2, int64_t a3, int64_t a4) {
    char line[16384];
    char *p = line;
    char *end = line + sizeof(line) - 1;
    char num[64];
    char s_a1[256] = {0}, s_a2[256] = {0}, s_a3[256] = {0}, s_a4[256] = {0};
    char s_payload70[256] = {0}, s_payload98[256] = {0}, s_payloadc0[256] = {0};
    int64_t retaddr = (int64_t)_ReturnAddress();
    int64_t payload = a2 ? r64((void *)a2) : 0;
    int64_t page_ctx = (int64_t)hb_runtime_get_last_page_ctx();
    int64_t page_receive = (page_ctx && is_readable_ptr(page_ctx, WX_HB_PAGE_RECEIVE_RESULT_OFF + 8)) ?
        r64((void *)(page_ctx + WX_HB_PAGE_RECEIVE_RESULT_OFF)) : 0;
    HbReceiveSnapshot recv;
    int64_t recv_tick = 0;
    ULONGLONG now_tick = GetTickCount64();
    LONG hit = h ? InterlockedIncrement(&h->hit) : 0;
    int64_t route_ctx = 0;
    hb_runtime_get_receive_snapshot(&recv);
    recv_tick = recv.tick;

    if (h) {
        if (h->rva == WX_OFF_HB_CARD_ROUTE_OUTER) {
            route_ctx = a1 ? r64((void *)(a1 + 8)) : 0;
        } else if (h->rva == WX_OFF_HB_CARD_ROUTE_DISPATCH ||
                   h->rva == WX_OFF_HB_RECEIVE_ROUTE_BUILDER) {
            route_ctx = a1;
        }
    }

    read_probe_string(a1, s_a1, sizeof(s_a1));
    read_probe_string(a2, s_a2, sizeof(s_a2));
    read_probe_string(a3, s_a3, sizeof(s_a3));
    read_probe_string(a4, s_a4, sizeof(s_a4));
    if (payload) {
        read_probe_string(payload + 0x70, s_payload70, sizeof(s_payload70));
        read_probe_string(payload + 0x98, s_payload98, sizeof(s_payload98));
        read_probe_string(payload + 0xC0, s_payloadc0, sizeof(s_payloadc0));
    }

    append_raw(&p, end, "{\"event\":\"hb_sentinel\"");
    append_raw(&p, end, ",\"name\":\""); append_escaped(&p, end, h ? h->name : "");
    append_raw(&p, end, "\",\"hit\":"); wsprintfA(num, "%ld", (long)hit); append_raw(&p, end, num);
    append_raw(&p, end, ",\"tid\":"); wsprintfA(num, "%lu", (unsigned long)GetCurrentThreadId()); append_raw(&p, end, num);
    append_hex_field(&p, end, "ret", retaddr);
    append_hex_field(&p, end, "a1", a1);
    append_hex_field(&p, end, "a2", a2);
    append_hex_field(&p, end, "a3", a3);
    append_hex_field(&p, end, "a4", a4);
    append_hex_field(&p, end, "q_a1", a1 ? r64((void *)a1) : 0);
    append_hex_field(&p, end, "q_a1_8", a1 ? r64((void *)(a1 + 8)) : 0);
    append_hex_field(&p, end, "q_a1_20", a1 ? r64((void *)(a1 + 0x20)) : 0);
    append_hex_field(&p, end, "q_a1_d0", a1 ? r64((void *)(a1 + 0xD0)) : 0);
    append_hex_field(&p, end, "q_a1_2a0", a1 ? r64((void *)(a1 + 0x2A0)) : 0);
    append_hex_field(&p, end, "q_a1_2a8", a1 ? r64((void *)(a1 + 0x2A8)) : 0);
    append_hex_field(&p, end, "q_a2", a2 ? r64((void *)a2) : 0);
    append_hex_field(&p, end, "q_a2_8", a2 ? r64((void *)(a2 + 8)) : 0);
    append_hex_field(&p, end, "q_a3", a3 ? r64((void *)a3) : 0);
    append_hex_field(&p, end, "q_a3_8", a3 ? r64((void *)(a3 + 8)) : 0);
    append_hex_field(&p, end, "payload", payload);
    append_hex_field(&p, end, "payload_70", payload ? r64((void *)(payload + 0x70)) : 0);
    append_hex_field(&p, end, "payload_98", payload ? r64((void *)(payload + 0x98)) : 0);
    append_hex_field(&p, end, "payload_c0", payload ? r64((void *)(payload + 0xC0)) : 0);
    append_hex_field(&p, end, "payload_3c0", payload ? r64((void *)(payload + WX_HB_PAGE_RECEIVE_RESULT_OFF)) : 0);
    append_hex_field(&p, end, "route_ctx", route_ctx);
    if (route_ctx && is_readable_ptr(route_ctx, 0x540)) {
        append_hex_field(&p, end, "route_390_obj", r64((void *)(route_ctx + 0x390)));
        append_hex_field(&p, end, "route_398_ctl", r64((void *)(route_ctx + 0x398)));
        append_hex_field(&p, end, "route_4a8", r64((void *)(route_ctx + 0x4A8)));
        append_hex_field(&p, end, "route_500_obj", r64((void *)(route_ctx + 0x500)));
        append_hex_field(&p, end, "route_508_ctl", r64((void *)(route_ctx + 0x508)));
        append_hex_field(&p, end, "route_510_state", r64((void *)(route_ctx + 0x510)));
        append_hex_field(&p, end, "route_518_obj", r64((void *)(route_ctx + 0x518)));
        append_hex_field(&p, end, "route_520_ctl", r64((void *)(route_ctx + 0x520)));
        append_hex_field(&p, end, "route_528", r64((void *)(route_ctx + 0x528)));
    }
    if (h && h->rva == WX_OFF_HB_RECEIVEWXHB_ENTRY) {
        int64_t receive_ctx = a1;
        append_hex_field(&p, end, "receive_ctx", receive_ctx);
        append_hex_field(&p, end, "receive_ctx_8", receive_ctx ? r64((void *)(receive_ctx + 0x08)) : 0);
        append_hex_field(&p, end, "receive_ctx_f0", receive_ctx ? r64((void *)(receive_ctx + 0xF0)) : 0);
        append_hex_field(&p, end, "receive_ctx_478", receive_ctx ? r64((void *)(receive_ctx + 0x478)) : 0);
        hb_append_std_string_field(&p, end, "receive_ctx_478", receive_ctx ? receive_ctx + 0x478 : 0);
        append_hex_field(&p, end, "last_mid_ctx", (int64_t)hb_runtime_get_receive_mid_ctx());
        append_hex_field(&p, end, "last_parent_ctx", (int64_t)hb_runtime_get_receive_parent_ctx());
        append_hex_field(&p, end, "last_arg", (int64_t)hb_runtime_get_receive_arg());
        hb_append_probe_string_field(&p, end, "receive_ctx_8_s", receive_ctx + 0x08);
        hb_append_probe_string_field(&p, end, "receive_ctx_f0_s", receive_ctx + 0xF0);
    }
    if (h && h->rva == WX_OFF_HB_MID_CTX_FROM_TIMING) {
        append_hex_field(&p, end, "mid_input_senditem", a3);
        hb_append_std_string_field(&p, end, "mid_input_timing", a4);
    }
    if (h && h->rva == WX_OFF_HB_OPEN_CTX_BUILDER) {
        int64_t mid_ctx = a1;
        append_hex_field(&p, end, "open_mid_ctx", mid_ctx);
        append_hex_field(&p, end, "open_mid_aux_3b0", mid_ctx ? r64((void *)(mid_ctx + 0x3B0)) : 0);
        hb_append_std_string_field(&p, end, "open_mid_timing_390", mid_ctx ? mid_ctx + 0x390 : 0);
    }
    if (h && h->rva == WX_OFF_HB_OPENWXHB_ENTRY) {
        int64_t open_ctx = a1;
        append_hex_field(&p, end, "open_ctx", open_ctx);
        append_hex_field(&p, end, "open_ctx_aux_498", open_ctx ? r64((void *)(open_ctx + 0x498)) : 0);
        hb_append_std_string_field(&p, end, "open_ctx_timing_478", open_ctx ? open_ctx + 0x478 : 0);
    }
    append_hex_field(&p, end, "last_page_ctx", page_ctx);
    append_hex_field(&p, end, "last_page_receive", page_receive);
    hb_append_std_string_field(&p, end, "last_page_timing", page_receive ? page_receive + WX_HB_RECEIVE_TIMING_OFF : 0);
    append_hex_field(&p, end, "recv_msg_base", recv.msg_base);
    append_dec_field(&p, end, "recv_msgId", recv.msgid);
    append_dec_field(&p, end, "recv_localId", recv.local_id);
    append_dec_field(&p, end, "recv_age_ms", recv_tick ? (int64_t)(now_tick - (ULONGLONG)recv_tick) : -1);
    append_raw(&p, end, ",\"recv_sendId\":\""); append_escaped(&p, end, recv.sendid);
    append_raw(&p, end, "\",\"recv_nativeUrl\":\""); append_escaped(&p, end, recv.native_url);
    append_raw(&p, end, "\",\"recv_pageUrl\":\""); append_escaped(&p, end, recv.page_url);
    append_raw(&p, end, "\",\"recv_peer\":\""); append_escaped(&p, end, recv.peer);
    append_raw(&p, end, "\",\"recv_sender\":\""); append_escaped(&p, end, recv.sender);
    append_raw(&p, end, "\",\"s_a1\":\""); append_escaped(&p, end, s_a1);
    append_raw(&p, end, "\",\"s_a2\":\""); append_escaped(&p, end, s_a2);
    append_raw(&p, end, "\",\"s_a3\":\""); append_escaped(&p, end, s_a3);
    append_raw(&p, end, "\",\"s_a4\":\""); append_escaped(&p, end, s_a4);
    append_raw(&p, end, "\",\"payload_70_s\":\""); append_escaped(&p, end, s_payload70);
    append_raw(&p, end, "\",\"payload_98_s\":\""); append_escaped(&p, end, s_payload98);
    append_raw(&p, end, "\",\"payload_c0_s\":\""); append_escaped(&p, end, s_payloadc0);
    append_raw(&p, end, "\"}");
    *p = 0;
    hb_route_dbg(line);
    if (h) wxb_hb_closed_loop_note_sentinel(h->name, h->rva, a1);
}

static int64_t hb_sentinel_dispatch(int idx, int64_t a1, int64_t a2, int64_t a3, int64_t a4) {
    HbSentinelHook *h = (idx >= 0 && idx < hb_sentinel_count()) ? &g_hb_sentinels[idx] : NULL;
    FnHbGeneric4 orig = h ? (FnHbGeneric4)h->trampoline : NULL;
    if (h) hb_log_sentinel(h, a1, a2, a3, a4);
    if (!orig) return 0;
    return orig(a1, a2, a3, a4);
}

static int64_t __fastcall hb_sentinel0_hook(int64_t a1, int64_t a2, int64_t a3, int64_t a4) { return hb_sentinel_dispatch(0, a1, a2, a3, a4); }
static int64_t __fastcall hb_sentinel1_hook(int64_t a1, int64_t a2, int64_t a3, int64_t a4) { return hb_sentinel_dispatch(1, a1, a2, a3, a4); }
static int64_t __fastcall hb_sentinel2_hook(int64_t a1, int64_t a2, int64_t a3, int64_t a4) { return hb_sentinel_dispatch(2, a1, a2, a3, a4); }
static int64_t __fastcall hb_sentinel3_hook(int64_t a1, int64_t a2, int64_t a3, int64_t a4) { return hb_sentinel_dispatch(3, a1, a2, a3, a4); }
static int64_t __fastcall hb_sentinel4_hook(int64_t a1, int64_t a2, int64_t a3, int64_t a4) { return hb_sentinel_dispatch(4, a1, a2, a3, a4); }
static int64_t __fastcall hb_sentinel5_hook(int64_t a1, int64_t a2, int64_t a3, int64_t a4) { return hb_sentinel_dispatch(5, a1, a2, a3, a4); }
static int64_t __fastcall hb_sentinel6_hook(int64_t a1, int64_t a2, int64_t a3, int64_t a4) { return hb_sentinel_dispatch(6, a1, a2, a3, a4); }
static int64_t __fastcall hb_sentinel7_hook(int64_t a1, int64_t a2, int64_t a3, int64_t a4) { return hb_sentinel_dispatch(7, a1, a2, a3, a4); }

static void *hb_sentinel_hook_proc(int idx) {
    switch (idx) {
    case 0: return (void *)hb_sentinel0_hook;
    case 1: return (void *)hb_sentinel1_hook;
    case 2: return (void *)hb_sentinel2_hook;
    case 3: return (void *)hb_sentinel3_hook;
    case 4: return (void *)hb_sentinel4_hook;
    case 5: return (void *)hb_sentinel5_hook;
    case 6: return (void *)hb_sentinel6_hook;
    case 7: return (void *)hb_sentinel7_hook;
    default: return NULL;
    }
}

static int build_hb_sentinel_trampoline(BYTE *p, HbSentinelHook *h, BYTE *target) {
    BYTE *start = p;
    copy_bytes(p, h->orig, h->patch_len);
    p += h->patch_len;
    *p++ = 0x48; *p++ = 0xB8;         /* mov rax, target+patch_len */
    *(int64_t *)p = (int64_t)(target + h->patch_len); p += 8;
    *p++ = 0xFF; *p++ = 0xE0;         /* jmp rax */
    return (int)(p - start);
}

void install_hb_sentinel_hooks(void) {
    if (!wxb_weixin_module()) return;
    for (int i = 0; i < hb_sentinel_count(); i++) {
        HbSentinelHook *h = &g_hb_sentinels[i];
        BYTE *target = (BYTE *)((int64_t)wxb_weixin_module() + h->rva);
        void *hook_proc = hb_sentinel_hook_proc(i);
        BYTE patch[HB_SENTINEL_MAX_PATCH];
        DWORD old = 0;
        char dbg[512];
        if (h->installed || !hook_proc || h->patch_len <= 0 || h->patch_len > HB_SENTINEL_MAX_PATCH) continue;
        if (!bytes_equal(target, h->expected, h->patch_len)) {
            if (target[0] == 0x48 && target[1] == 0xB8 && target[10] == 0xFF && target[11] == 0xE0) {
                restore_code_if_changed(target, h->expected, h->patch_len,
                                        "[hb-sentinel] stale hook restored", 1);
            }
        }
        copy_bytes(h->orig, target, h->patch_len);
        if (!bytes_equal(h->orig, h->expected, h->patch_len)) {
            wsprintfA(dbg, "[hb-sentinel] skip %s; unexpected bytes at target", h->name);
            hb_route_dbg(dbg);
            continue;
        }
        h->trampoline = VirtualAlloc(NULL, 256, MEM_COMMIT | MEM_RESERVE, PAGE_EXECUTE_READWRITE);
        if (!h->trampoline) {
            wsprintfA(dbg, "[hb-sentinel] trampoline alloc failed %s", h->name);
            hb_route_dbg(dbg);
            continue;
        }
        int tlen = build_hb_sentinel_trampoline((BYTE *)h->trampoline, h, target);
        FlushInstructionCache(GetCurrentProcess(), h->trampoline, tlen);

        for (int j = 0; j < h->patch_len; j++) patch[j] = 0x90;
        patch[0] = 0x48; patch[1] = 0xB8; /* mov rax, hook */
        *(int64_t *)(patch + 2) = (int64_t)hook_proc;
        patch[10] = 0xFF; patch[11] = 0xE0; /* jmp rax */
        VirtualProtect(target, h->patch_len, PAGE_EXECUTE_READWRITE, &old);
        copy_bytes(target, patch, h->patch_len);
        VirtualProtect(target, h->patch_len, old, &old);
        FlushInstructionCache(GetCurrentProcess(), target, h->patch_len);
        InterlockedExchange(&h->installed, 1);
        wsprintfA(dbg, "[hb-sentinel] hook installed %s", h->name);
        hb_route_dbg(dbg);
    }
}

void uninstall_hb_sentinel_hooks(void) {
    if (!wxb_weixin_module()) return;
    for (int i = 0; i < hb_sentinel_count(); i++) {
        HbSentinelHook *h = &g_hb_sentinels[i];
        BYTE *target = (BYTE *)((int64_t)wxb_weixin_module() + h->rva);
        DWORD old = 0;
        char dbg[512];
        if (!h->installed) continue;
        VirtualProtect(target, h->patch_len, PAGE_EXECUTE_READWRITE, &old);
        copy_bytes(target, h->orig, h->patch_len);
        VirtualProtect(target, h->patch_len, old, &old);
        FlushInstructionCache(GetCurrentProcess(), target, h->patch_len);
        InterlockedExchange(&h->installed, 0);
        if (h->trampoline) {
            VirtualFree(h->trampoline, 0, MEM_RELEASE);
            h->trampoline = NULL;
        }
        wsprintfA(dbg, "[hb-sentinel] hook removed %s", h->name);
        hb_route_dbg(dbg);
    }
}

int64_t __fastcall hb_route_hook(int64_t a1, int64_t a2, int64_t a3, int64_t a4) {
    LONG hit = hb_hook_record_hit(HB_HOOK_ROUTE);
    int64_t retaddr = (int64_t)_ReturnAddress();
    int64_t page_ctx = (int64_t)hb_runtime_get_last_page_ctx();
    char s1[256] = {0}, s2[512] = {0}, s4[512] = {0};
    char a1_70[512] = {0}, a2_70[512] = {0}, a4_70[512] = {0};
    char dbg[4096];

    read_probe_string(a1, s1, sizeof(s1));
    read_probe_string(a2, s2, sizeof(s2));
    read_probe_string(a4, s4, sizeof(s4));
    if (is_readable_ptr(a1 + 0x70, 0x20)) read_probe_string(a1 + 0x70, a1_70, sizeof(a1_70));
    if (is_readable_ptr(a2 + 0x70, 0x20)) read_probe_string(a2 + 0x70, a2_70, sizeof(a2_70));
    if (is_readable_ptr(a4 + 0x70, 0x20)) read_probe_string(a4 + 0x70, a4_70, sizeof(a4_70));

    wsprintfA(dbg,
              "[hb-route] hit=%ld ret=%I64x a1=%I64x a2=%I64x a3=%I64x a4=%I64x last_page_ctx=%I64x s1=\"%s\" s2=\"%s\" s4=\"%s\" a1+70=\"%s\" a2+70=\"%s\" a4+70=\"%s\"",
              (long)hit, retaddr, a1, a2, a3, a4, page_ctx,
              s1, s2, s4, a1_70, a2_70, a4_70);
    hb_route_dbg(dbg);

    return hb_call_route_original(a1, a2, a3, a4);
}

