/*
 * Module: hongbao hook install/uninstall wrappers
 * Source: ../../../../wx_hook_bridge.dll.c lines 6713-7177
 */

#include "../include/wx_bridge_hongbao_hooks.h"
#include "../include/wx_bridge_debug_log.h"
#include "../include/wx_bridge_hongbao_entrypoints.h"
#include "../include/wx_bridge_memory.h"
#include "../include/wx_bridge_offsets.h"
#include "../include/wx_bridge_protocol.h"
#include "../include/wx_bridge_runtime.h"

typedef struct HbHookState {
    void *trampoline;
    BYTE orig[24];
    volatile LONG installed;
    volatile LONG hit;
} HbHookState;

static HbHookState g_hb_hook_state[HB_HOOK_COUNT] = {0};

static const BYTE WX_ORIG_HB_NULL_GUARD_BYTES[5] = {
    0x56,0x48,0x83,0xEC,0x20
};
static const BYTE WX_ORIG_HB_SETTER_BYTES[20] = {
    0x55,0x56,0x57,0x48,0x83,0xEC,0x30,0x48,0x8D,0x6C,
    0x24,0x30,0x48,0xC7,0x45,0xF8,0xFE,0xFF,0xFF,0xFF
};
static const BYTE WX_ORIG_HB_PAGECTX_BYTES[5] = {
    0xE9,0x7A,0xF6,0x01,0x00
};
static const BYTE WX_ORIG_HB_TASK_FACTORY_BYTES[15] = {
    0x55,0x41,0x57,0x41,0x56,0x56,0x57,0x53,0x48,0x81,
    0xEC,0x28,0x01,0x00,0x00
};
static const BYTE WX_ORIG_HB_RECEIVE_ROUTE_BUILDER_BYTES[18] = {
    0x55,0x56,0x57,0x48,0x81,0xEC,0xE0,0x01,0x00,0x00,
    0x48,0x8D,0xAC,0x24,0x80,0x00,0x00,0x00
};
static const BYTE WX_ORIG_HB_TASK_FACTORY_INNER_BYTES[19] = {
    0x55,0x41,0x57,0x41,0x56,0x41,0x55,0x41,0x54,0x56,
    0x57,0x53,0x48,0x81,0xEC,0xC8,0x02,0x00,0x00
};
static const BYTE WX_ORIG_HB_RECEIVE_MID_BUILDER_BYTES[13] = {
    0x55,0x41,0x56,0x56,0x57,0x53,0x48,0x81,0xEC,0x80,0x04,0x00,0x00
};
static const BYTE WX_ORIG_HB_RECEIVE_TASK_BUILDER_BYTES[13] = {
    0x55,0x41,0x56,0x56,0x57,0x53,0x48,0x81,0xEC,0x40,0x05,0x00,0x00
};
static const BYTE WX_ORIG_HB_SOURCE_SUBMIT_BYTES[19] = {
    0x55,0x41,0x57,0x41,0x56,0x41,0x55,0x41,0x54,0x56,
    0x57,0x53,0x48,0x81,0xEC,0xB8,0x03,0x00,0x00
};
static const BYTE WX_ORIG_HB_ROUTE_DISPATCHER_BYTES[19] = {
    0x55,0x41,0x57,0x41,0x56,0x41,0x55,0x41,0x54,0x56,
    0x57,0x53,0x48,0x81,0xEC,0xC8,0x04,0x00,0x00
};
static const BYTE WX_ORIG_HB_ROUTE_TEMP_VECTOR_BYTES[15] = {
    0x55,0x41,0x57,0x41,0x56,0x56,0x57,0x53,0x48,0x81,
    0xEC,0x18,0x01,0x00,0x00
};
static const BYTE WX_ORIG_HB_ROUTE_VECTOR_BUILDER_BYTES[19] = {
    0x55,0x41,0x57,0x41,0x56,0x41,0x55,0x41,0x54,0x56,
    0x57,0x53,0x48,0x81,0xEC,0xD8,0x00,0x00,0x00
};
static const BYTE WX_ORIG_HB_ROUTE_CTX_FACTORY_BYTES[13] = {
    0x55,0x56,0x57,0x53,0x48,0x83,0xEC,0x48,0x48,0x8D,0x6C,0x24,0x40
};
static const BYTE WX_ORIG_HB_ROUTE_CLASSIFIER_BYTES[18] = {
    0x55,0x56,0x57,0x48,0x81,0xEC,0xD0,0x01,0x00,0x00,
    0x48,0x8D,0xAC,0x24,0x80,0x00,0x00,0x00
};
static const BYTE WX_ORIG_HB_ROUTE_ITEM_DISPATCH_BYTES[23] = {
    0x55,0x41,0x57,0x41,0x56,0x56,0x57,0x53,0x48,0x81,
    0xEC,0xB8,0x00,0x00,0x00,0x48,0x8D,0xAC,0x24,0x80,
    0x00,0x00,0x00
};

static HbHookState *hb_hook_state(HbHookKind kind) {
    if ((int)kind < 0 || kind >= HB_HOOK_COUNT) return NULL;
    return &g_hb_hook_state[(int)kind];
}

static int build_hb_entry_trampoline(BYTE *p, BYTE *target, BYTE *orig, int len) {
    BYTE *start = p;
    copy_bytes(p, orig, len);
    p += len;
    *p++ = 0x48; *p++ = 0xB8;         /* mov rax, target+len */
    *(int64_t *)p = (int64_t)(target + len); p += 8;
    *p++ = 0xFF; *p++ = 0xE0;         /* jmp rax */
    return (int)(p - start);
}

static int install_hb_entry_hook(const char *tag, DWORD rva, HbHookKind kind, int len,
                                 const BYTE *expected, void *hook_proc) {
    HbHookState *state = hb_hook_state(kind);
    if (!wxb_weixin_module() || !state || !expected || !hook_proc) return 0;
    if (state->installed) return 1;
    BYTE *target = (BYTE *)((int64_t)wxb_weixin_module() + rva);
    BYTE patch[24];
    DWORD old = 0;
    char dbg[512];
    if (len <= 0 || len > (int)sizeof(patch) || len > (int)sizeof(state->orig)) return 0;
    restore_code_if_changed(target, expected, (SIZE_T)len, NULL, 1);
    copy_bytes(state->orig, target, len);
    if (!bytes_equal(state->orig, expected, len)) {
        wsprintfA(dbg, "[hb-planb] skip %s; unexpected bytes at target", tag ? tag : "");
        hb_route_dbg(dbg);
        return 0;
    }
    state->trampoline = alloc_near(target, 256);
    if (!state->trampoline) state->trampoline = VirtualAlloc(NULL, 256, MEM_COMMIT | MEM_RESERVE, PAGE_EXECUTE_READWRITE);
    if (!state->trampoline) {
        wsprintfA(dbg, "[hb-planb] trampoline alloc failed %s", tag ? tag : "");
        hb_route_dbg(dbg);
        return 0;
    }
    int tlen = build_hb_entry_trampoline((BYTE *)state->trampoline, target, state->orig, len);
    FlushInstructionCache(GetCurrentProcess(), state->trampoline, tlen);
    for (int i = 0; i < len; i++) patch[i] = 0x90;
    patch[0] = 0x48; patch[1] = 0xB8;
    *(int64_t *)(patch + 2) = (int64_t)hook_proc;
    patch[10] = 0xFF; patch[11] = 0xE0;
    VirtualProtect(target, len, PAGE_EXECUTE_READWRITE, &old);
    copy_bytes(target, patch, len);
    VirtualProtect(target, len, old, &old);
    FlushInstructionCache(GetCurrentProcess(), target, len);
    InterlockedExchange(&state->installed, 1);
    wsprintfA(dbg, "[hb-planb] hook installed %s", tag ? tag : "");
    hb_route_dbg(dbg);
    return 1;
}

static void uninstall_hb_entry_hook(const char *tag, DWORD rva, HbHookKind kind, int len) {
    HbHookState *state = hb_hook_state(kind);
    if (!wxb_weixin_module() || !state || !state->installed) return;
    BYTE *target = (BYTE *)((int64_t)wxb_weixin_module() + rva);
    DWORD old = 0;
    char dbg[512];
    if (len <= 0 || len > (int)sizeof(state->orig)) return;
    VirtualProtect(target, len, PAGE_EXECUTE_READWRITE, &old);
    copy_bytes(target, state->orig, len);
    VirtualProtect(target, len, old, &old);
    FlushInstructionCache(GetCurrentProcess(), target, len);
    InterlockedExchange(&state->installed, 0);
    if (state->trampoline) {
        VirtualFree(state->trampoline, 0, MEM_RELEASE);
        state->trampoline = NULL;
    }
    wsprintfA(dbg, "[hb-planb] hook removed %s", tag ? tag : "");
    hb_route_dbg(dbg);
}

void install_hb_receive_mid_builder_hook(void) {
    install_hb_entry_hook("receive_mid_builder_184A60500",
                          WX_OFF_HB_RECEIVE_MID_BUILDER,
                          HB_HOOK_RECEIVE_MID_BUILDER,
                          13,
                          WX_ORIG_HB_RECEIVE_MID_BUILDER_BYTES,
                          (void *)hb_receive_mid_builder_hook);
}

void uninstall_hb_receive_mid_builder_hook(void) {
    uninstall_hb_entry_hook("receive_mid_builder_184A60500",
                            WX_OFF_HB_RECEIVE_MID_BUILDER,
                            HB_HOOK_RECEIVE_MID_BUILDER,
                            13);
}

void install_hb_receive_route_builder_hook(void) {
    install_hb_entry_hook("receive_route_builder_185EA6140",
                          WX_OFF_HB_RECEIVE_ROUTE_BUILDER,
                          HB_HOOK_RECEIVE_ROUTE_BUILDER,
                          18,
                          WX_ORIG_HB_RECEIVE_ROUTE_BUILDER_BYTES,
                          (void *)hb_receive_route_builder_hook);
}

void uninstall_hb_receive_route_builder_hook(void) {
    uninstall_hb_entry_hook("receive_route_builder_185EA6140",
                            WX_OFF_HB_RECEIVE_ROUTE_BUILDER,
                            HB_HOOK_RECEIVE_ROUTE_BUILDER,
                            18);
}

void install_hb_route_dispatcher_hook(void) {
    install_hb_entry_hook("route_dispatcher_1840A3520",
                          WX_OFF_HB_ROUTE_DISPATCHER,
                          HB_HOOK_ROUTE_DISPATCHER,
                          19,
                          WX_ORIG_HB_ROUTE_DISPATCHER_BYTES,
                          (void *)hb_route_dispatcher_hook);
}

void uninstall_hb_route_dispatcher_hook(void) {
    uninstall_hb_entry_hook("route_dispatcher_1840A3520",
                            WX_OFF_HB_ROUTE_DISPATCHER,
                            HB_HOOK_ROUTE_DISPATCHER,
                            19);
}

void install_hb_route_temp_vector_hook(void) {
    install_hb_entry_hook("route_temp_vector_184120B60",
                          WX_OFF_HB_ROUTE_TEMP_VECTOR,
                          HB_HOOK_ROUTE_TEMP_VECTOR,
                          15,
                          WX_ORIG_HB_ROUTE_TEMP_VECTOR_BYTES,
                          (void *)hb_route_temp_vector_hook);
}

void uninstall_hb_route_temp_vector_hook(void) {
    uninstall_hb_entry_hook("route_temp_vector_184120B60",
                            WX_OFF_HB_ROUTE_TEMP_VECTOR,
                            HB_HOOK_ROUTE_TEMP_VECTOR,
                            15);
}

void install_hb_route_vector_builder_hook(void) {
    install_hb_entry_hook("route_vector_builder_185705820",
                          WX_OFF_HB_ROUTE_VECTOR_BUILDER,
                          HB_HOOK_ROUTE_VECTOR_BUILDER,
                          19,
                          WX_ORIG_HB_ROUTE_VECTOR_BUILDER_BYTES,
                          (void *)hb_route_vector_builder_hook);
}

void uninstall_hb_route_vector_builder_hook(void) {
    uninstall_hb_entry_hook("route_vector_builder_185705820",
                            WX_OFF_HB_ROUTE_VECTOR_BUILDER,
                            HB_HOOK_ROUTE_VECTOR_BUILDER,
                            19);
}

void install_hb_route_ctx_factory_hook(void) {
    install_hb_entry_hook("route_ctx_factory_18527C7E0",
                          WX_OFF_HB_ROUTE_CTX_FACTORY,
                          HB_HOOK_ROUTE_CTX_FACTORY,
                          13,
                          WX_ORIG_HB_ROUTE_CTX_FACTORY_BYTES,
                          (void *)hb_route_ctx_factory_hook);
}

void uninstall_hb_route_ctx_factory_hook(void) {
    uninstall_hb_entry_hook("route_ctx_factory_18527C7E0",
                            WX_OFF_HB_ROUTE_CTX_FACTORY,
                            HB_HOOK_ROUTE_CTX_FACTORY,
                            13);
}

void install_hb_route_classifier_hook(void) {
    install_hb_entry_hook("route_classifier_18527A1A0",
                          WX_OFF_HB_ROUTE_CLASSIFIER,
                          HB_HOOK_ROUTE_CLASSIFIER,
                          18,
                          WX_ORIG_HB_ROUTE_CLASSIFIER_BYTES,
                          (void *)hb_route_classifier_hook);
}

void uninstall_hb_route_classifier_hook(void) {
    uninstall_hb_entry_hook("route_classifier_18527A1A0",
                            WX_OFF_HB_ROUTE_CLASSIFIER,
                            HB_HOOK_ROUTE_CLASSIFIER,
                            18);
}

void install_hb_route_item_dispatch_hook(void) {
    install_hb_entry_hook("route_item_dispatch_185333E20",
                          WX_OFF_HB_ROUTE_ITEM_DISPATCH,
                          HB_HOOK_ROUTE_ITEM_DISPATCH,
                          23,
                          WX_ORIG_HB_ROUTE_ITEM_DISPATCH_BYTES,
                          (void *)hb_route_item_dispatch_hook);
}

void uninstall_hb_route_item_dispatch_hook(void) {
    uninstall_hb_entry_hook("route_item_dispatch_185333E20",
                            WX_OFF_HB_ROUTE_ITEM_DISPATCH,
                            HB_HOOK_ROUTE_ITEM_DISPATCH,
                            23);
}

void install_hb_source_submit_hook(void) {
    install_hb_entry_hook("source_submit_18166A3C0",
                          WX_OFF_HB_SOURCE_SUBMIT,
                          HB_HOOK_SOURCE_SUBMIT,
                          19,
                          WX_ORIG_HB_SOURCE_SUBMIT_BYTES,
                          (void *)hb_source_submit_hook);
}

void uninstall_hb_source_submit_hook(void) {
    uninstall_hb_entry_hook("source_submit_18166A3C0",
                            WX_OFF_HB_SOURCE_SUBMIT,
                            HB_HOOK_SOURCE_SUBMIT,
                            19);
}

void install_hb_task_factory_inner_hook(void) {
    install_hb_entry_hook("task_factory_inner_183A6A320",
                          WX_OFF_HB_TASK_FACTORY_INNER,
                          HB_HOOK_TASK_FACTORY_INNER,
                          19,
                          WX_ORIG_HB_TASK_FACTORY_INNER_BYTES,
                          (void *)hb_task_factory_inner_hook);
}

void uninstall_hb_task_factory_inner_hook(void) {
    uninstall_hb_entry_hook("task_factory_inner_183A6A320",
                            WX_OFF_HB_TASK_FACTORY_INNER,
                            HB_HOOK_TASK_FACTORY_INNER,
                            19);
}

void install_hb_receive_task_builder_hook(void) {
    install_hb_entry_hook("receive_task_builder_184A64BB0",
                          WX_OFF_HB_RECEIVE_TASK_BUILDER,
                          HB_HOOK_RECEIVE_TASK_BUILDER,
                          13,
                          WX_ORIG_HB_RECEIVE_TASK_BUILDER_BYTES,
                          (void *)hb_receive_task_builder_hook);
}

void uninstall_hb_receive_task_builder_hook(void) {
    uninstall_hb_entry_hook("receive_task_builder_184A64BB0",
                            WX_OFF_HB_RECEIVE_TASK_BUILDER,
                            HB_HOOK_RECEIVE_TASK_BUILDER,
                            13);
}

static int build_hb_table_trampoline(BYTE *p, BYTE *target, HbHookState *state, int len) {
    BYTE *start = p;
    copy_bytes(p, state->orig, len);
    p += len;
    *p++ = 0x48; *p++ = 0xB8;         /* mov rax, target+patch_len */
    *(int64_t *)p = (int64_t)(target + len); p += 8;
    *p++ = 0xFF; *p++ = 0xE0;         /* jmp rax */
    return (int)(p - start);
}

void install_hb_route_hook(void) {
    HbHookState *state = hb_hook_state(HB_HOOK_ROUTE);
    if (!state || state->installed || !wxb_weixin_module()) return;
    BYTE *target = (BYTE *)((int64_t)wxb_weixin_module() + WX_OFF_HB_ROUTE_CANDIDATE);
    copy_bytes(state->orig, target, 15);
    state->trampoline = VirtualAlloc(NULL, 256, MEM_COMMIT | MEM_RESERVE, PAGE_EXECUTE_READWRITE);
    if (!state->trampoline) {
        hb_route_dbg("[hb-route] trampoline alloc failed");
        return;
    }
    int tlen = build_hb_table_trampoline((BYTE *)state->trampoline, target, state, 15);
    FlushInstructionCache(GetCurrentProcess(), state->trampoline, tlen);

    BYTE patch[15];
    for (int i = 0; i < (int)sizeof(patch); i++) patch[i] = 0x90;
    patch[0] = 0x48; patch[1] = 0xB8; /* mov rax, hb_route_hook */
    *(int64_t *)(patch + 2) = (int64_t)hb_route_hook;
    patch[10] = 0xFF; patch[11] = 0xE0; /* jmp rax */

    DWORD old = 0;
    VirtualProtect(target, sizeof(patch), PAGE_EXECUTE_READWRITE, &old);
    copy_bytes(target, patch, sizeof(patch));
    VirtualProtect(target, sizeof(patch), old, &old);
    FlushInstructionCache(GetCurrentProcess(), target, sizeof(patch));
    InterlockedExchange(&state->installed, 1);
    hb_route_dbg("[hb-route] hook installed");
}

void uninstall_hb_route_hook(void) {
    HbHookState *state = hb_hook_state(HB_HOOK_ROUTE);
    if (!state || !state->installed || !wxb_weixin_module()) return;
    BYTE *target = (BYTE *)((int64_t)wxb_weixin_module() + WX_OFF_HB_ROUTE_CANDIDATE);
    DWORD old = 0;
    VirtualProtect(target, 15, PAGE_EXECUTE_READWRITE, &old);
    copy_bytes(target, state->orig, 15);
    VirtualProtect(target, 15, old, &old);
    FlushInstructionCache(GetCurrentProcess(), target, 15);
    InterlockedExchange(&state->installed, 0);
    if (state->trampoline) {
        VirtualFree(state->trampoline, 0, MEM_RELEASE);
        state->trampoline = NULL;
    }
    hb_route_dbg("[hb-route] hook removed");
}

void install_hb_task_factory_hook(void) {
    HbHookState *state = hb_hook_state(HB_HOOK_TASK_FACTORY);
    if (!state || state->installed || !wxb_weixin_module()) return;
    BYTE *target = (BYTE *)((int64_t)wxb_weixin_module() + WX_OFF_HB_TASK_FACTORY);
    BYTE patch[15];
    DWORD old = 0;

    restore_code_if_changed(target, WX_ORIG_HB_TASK_FACTORY_BYTES,
                            sizeof(WX_ORIG_HB_TASK_FACTORY_BYTES),
                            "[hb-task] stale hook restored", 1);
    copy_bytes(state->orig, target, 15);
    if (!bytes_equal(state->orig, WX_ORIG_HB_TASK_FACTORY_BYTES, 15)) {
        hb_route_dbg("[hb-task] skip; unexpected bytes at task factory");
        return;
    }

    state->trampoline = alloc_near(target, 256);
    if (!state->trampoline) {
        hb_route_dbg("[hb-task] trampoline alloc failed");
        return;
    }
    int tlen = build_hb_table_trampoline((BYTE *)state->trampoline, target, state, 15);
    FlushInstructionCache(GetCurrentProcess(), state->trampoline, tlen);

    for (int i = 0; i < (int)sizeof(patch); i++) patch[i] = 0x90;
    patch[0] = 0x48; patch[1] = 0xB8; /* mov rax, hb_task_factory_hook */
    *(int64_t *)(patch + 2) = (int64_t)hb_task_factory_hook;
    patch[10] = 0xFF; patch[11] = 0xE0; /* jmp rax */

    VirtualProtect(target, sizeof(patch), PAGE_EXECUTE_READWRITE, &old);
    copy_bytes(target, patch, sizeof(patch));
    VirtualProtect(target, sizeof(patch), old, &old);
    FlushInstructionCache(GetCurrentProcess(), target, sizeof(patch));
    InterlockedExchange(&state->installed, 1);
    hb_route_dbg("[hb-task] hook installed task_factory_183A68BF0");
}

void uninstall_hb_task_factory_hook(void) {
    HbHookState *state = hb_hook_state(HB_HOOK_TASK_FACTORY);
    if (!state || !state->installed || !wxb_weixin_module()) return;
    BYTE *target = (BYTE *)((int64_t)wxb_weixin_module() + WX_OFF_HB_TASK_FACTORY);
    DWORD old = 0;
    VirtualProtect(target, 15, PAGE_EXECUTE_READWRITE, &old);
    copy_bytes(target, state->orig, 15);
    VirtualProtect(target, 15, old, &old);
    FlushInstructionCache(GetCurrentProcess(), target, 15);
    InterlockedExchange(&state->installed, 0);
    if (state->trampoline) {
        VirtualFree(state->trampoline, 0, MEM_RELEASE);
        state->trampoline = NULL;
    }
    hb_route_dbg("[hb-task] hook removed task_factory_183A68BF0");
}

static int build_hb_null_guard_relay(BYTE *p, BYTE *target, HbHookState *state) {
    BYTE *start = p;
    *p++ = 0x48; *p++ = 0x85; *p++ = 0xC9; /* test rcx, rcx */
    *p++ = 0x75; *p++ = 0x01;              /* jnz +1 */
    *p++ = 0xC3;                            /* ret */
    copy_bytes(p, state->orig, 5); p += 5;
    *p++ = 0x48; *p++ = 0xB8;               /* mov rax, target+5 */
    *(int64_t *)p = (int64_t)(target + 5); p += 8;
    *p++ = 0xFF; *p++ = 0xE0;               /* jmp rax */
    return (int)(p - start);
}

void install_hb_null_guard_hook(void) {
    HbHookState *state = hb_hook_state(HB_HOOK_NULL_GUARD);
    if (!state || state->installed || !wxb_weixin_module()) return;
    BYTE *target = (BYTE *)((int64_t)wxb_weixin_module() + WX_OFF_HB_NULL_GUARD_HELPER);
    restore_code_if_changed(target, WX_ORIG_HB_NULL_GUARD_BYTES,
                            sizeof(WX_ORIG_HB_NULL_GUARD_BYTES),
                            "[hb-hook] null guard stale hook restored", 1);
    copy_bytes(state->orig, target, 5);
    state->trampoline = alloc_near(target, 256);
    if (!state->trampoline) {
        hb_dbg("[hb-hook] null guard trampoline alloc failed");
        return;
    }
    int tlen = build_hb_null_guard_relay((BYTE *)state->trampoline, target, state);
    FlushInstructionCache(GetCurrentProcess(), state->trampoline, tlen);

    int64_t rel32 = (int64_t)state->trampoline - ((int64_t)target + 5);
    if (rel32 < INT32_MIN || rel32 > INT32_MAX) {
        hb_dbg("[hb-hook] null guard hook out of rel32 range");
        VirtualFree(state->trampoline, 0, MEM_RELEASE);
        state->trampoline = NULL;
        return;
    }
    BYTE patch[5];
    patch[0] = 0xE9;
    *(int32_t *)(patch + 1) = (int32_t)rel32;
    DWORD old = 0;
    VirtualProtect(target, sizeof(patch), PAGE_EXECUTE_READWRITE, &old);
    copy_bytes(target, patch, sizeof(patch));
    VirtualProtect(target, sizeof(patch), old, &old);
    FlushInstructionCache(GetCurrentProcess(), target, sizeof(patch));
    InterlockedExchange(&state->installed, 1);
    hb_dbg("[hb-hook] null guard hook installed");
}

void uninstall_hb_null_guard_hook(void) {
    HbHookState *state = hb_hook_state(HB_HOOK_NULL_GUARD);
    if (!state || !state->installed || !wxb_weixin_module()) return;
    BYTE *target = (BYTE *)((int64_t)wxb_weixin_module() + WX_OFF_HB_NULL_GUARD_HELPER);
    DWORD old = 0;
    VirtualProtect(target, 5, PAGE_EXECUTE_READWRITE, &old);
    copy_bytes(target, state->orig, 5);
    VirtualProtect(target, 5, old, &old);
    FlushInstructionCache(GetCurrentProcess(), target, 5);
    InterlockedExchange(&state->installed, 0);
    if (state->trampoline) {
        VirtualFree(state->trampoline, 0, MEM_RELEASE);
        state->trampoline = NULL;
    }
    hb_dbg("[hb-hook] null guard hook removed");
}

static int build_hb_setter_trampoline(BYTE *p, BYTE *target, HbHookState *state) {
    BYTE *start = p;
    copy_bytes(p, state->orig, 20); p += 20;
    *p++ = 0x48; *p++ = 0xB8;         /* mov rax, target+len */
    *(int64_t *)p = (int64_t)(target + 20); p += 8;
    *p++ = 0xFF; *p++ = 0xE0;         /* jmp rax */
    return (int)(p - start);
}

void install_hb_setter_hook(void) {
    HbHookState *state = hb_hook_state(HB_HOOK_SETTER);
    if (!state || state->installed || !wxb_weixin_module()) return;
    BYTE *target = (BYTE *)((int64_t)wxb_weixin_module() + WX_OFF_HB_PAGECTX_SETTER);

    restore_code_if_changed(target, WX_ORIG_HB_SETTER_BYTES,
                            sizeof(WX_ORIG_HB_SETTER_BYTES),
                            "[hb-hook] setter stale hook restored", 1);
    copy_bytes(state->orig, target, 20);
    state->trampoline = VirtualAlloc(NULL, 256, MEM_COMMIT | MEM_RESERVE, PAGE_EXECUTE_READWRITE);
    if (!state->trampoline) {
        hb_dbg("[hb-hook] setter trampoline alloc failed");
        return;
    }
    int tlen = build_hb_setter_trampoline((BYTE *)state->trampoline, target, state);
    FlushInstructionCache(GetCurrentProcess(), state->trampoline, tlen);

    BYTE patch[20];
    patch[0] = 0x48; patch[1] = 0xB8; /* mov rax, hb_setter_hook */
    *(int64_t *)(patch + 2) = (int64_t)hb_setter_hook;
    patch[10] = 0xFF; patch[11] = 0xE0; /* jmp rax */
    for (int i = 12; i < (int)sizeof(patch); ++i) patch[i] = 0x90;

    DWORD old = 0;
    VirtualProtect(target, sizeof(patch), PAGE_EXECUTE_READWRITE, &old);
    copy_bytes(target, patch, sizeof(patch));
    VirtualProtect(target, sizeof(patch), old, &old);
    FlushInstructionCache(GetCurrentProcess(), target, sizeof(patch));
    InterlockedExchange(&state->installed, 1);
    hb_dbg("[hb-hook] setter hook installed");
}

void uninstall_hb_setter_hook(void) {
    HbHookState *state = hb_hook_state(HB_HOOK_SETTER);
    if (!state || !state->installed || !wxb_weixin_module()) return;
    BYTE *target = (BYTE *)((int64_t)wxb_weixin_module() + WX_OFF_HB_PAGECTX_SETTER);
    DWORD old = 0;
    VirtualProtect(target, 20, PAGE_EXECUTE_READWRITE, &old);
    copy_bytes(target, state->orig, 20);
    VirtualProtect(target, 20, old, &old);
    FlushInstructionCache(GetCurrentProcess(), target, 20);
    InterlockedExchange(&state->installed, 0);
    if (state->trampoline) {
        VirtualFree(state->trampoline, 0, MEM_RELEASE);
        state->trampoline = NULL;
    }
    hb_dbg("[hb-hook] setter hook removed");
}

void install_hb_pagectx_hook(void) {
    HbHookState *state = hb_hook_state(HB_HOOK_PAGECTX);
    if (!state || state->installed || !wxb_weixin_module()) return;
    BYTE *target = (BYTE *)((int64_t)wxb_weixin_module() + WX_OFF_HB_UI_PAGECTX_JMP);

    restore_code_if_changed(target, WX_ORIG_HB_PAGECTX_BYTES,
                            sizeof(WX_ORIG_HB_PAGECTX_BYTES),
                            "[hb-hook] page_ctx stale hook restored", 1);
    copy_bytes(state->orig, target, 5);
    state->trampoline = alloc_near(target, 256);
    if (!state->trampoline) {
        hb_dbg("[hb-hook] alloc_near failed");
        return;
    }
    int tlen = build_hb_pagectx_relay((BYTE *)state->trampoline);
    FlushInstructionCache(GetCurrentProcess(), state->trampoline, tlen);

    int64_t rel32 = (int64_t)state->trampoline - ((int64_t)target + 5);
    BYTE patch[5];
    if (rel32 >= INT32_MIN && rel32 <= INT32_MAX) {
        patch[0] = 0xE9;
        *(int32_t *)(patch + 1) = (int32_t)rel32;
    } else {
        hb_dbg("[hb-hook] hook out of rel32 range");
        VirtualFree(state->trampoline, 0, MEM_RELEASE);
        state->trampoline = NULL;
        return;
    }

    DWORD old = 0;
    VirtualProtect(target, sizeof(patch), PAGE_EXECUTE_READWRITE, &old);
    copy_bytes(target, patch, sizeof(patch));
    VirtualProtect(target, sizeof(patch), old, &old);
    FlushInstructionCache(GetCurrentProcess(), target, sizeof(patch));
    InterlockedExchange(&state->installed, 1);
    hb_dbg("[hb-hook] page_ctx hook installed");
}

void uninstall_hb_pagectx_hook(void) {
    HbHookState *state = hb_hook_state(HB_HOOK_PAGECTX);
    if (!state || !state->installed || !wxb_weixin_module()) return;
    BYTE *target = (BYTE *)((int64_t)wxb_weixin_module() + WX_OFF_HB_UI_PAGECTX_JMP);
    DWORD old = 0;
    VirtualProtect(target, 5, PAGE_EXECUTE_READWRITE, &old);
    copy_bytes(target, state->orig, 5);
    VirtualProtect(target, 5, old, &old);
    FlushInstructionCache(GetCurrentProcess(), target, 5);
    InterlockedExchange(&state->installed, 0);
    if (state->trampoline) {
        VirtualFree(state->trampoline, 0, MEM_RELEASE);
        state->trampoline = NULL;
    }
    hb_dbg("[hb-hook] page_ctx hook removed");
}

int hb_hook_installed(HbHookKind kind) {
    HbHookState *state = hb_hook_state(kind);
    if (!state) return 0;
    return InterlockedCompareExchange(&state->installed, 0, 0) != 0;
}

LONG hb_hook_record_hit(HbHookKind kind) {
    HbHookState *state = hb_hook_state(kind);
    if (!state) return 0;
    return InterlockedIncrement(&state->hit);
}

int64_t hb_call_setter_original(int64_t owner, int64_t shared_page_ctx) {
    HbHookState *state = hb_hook_state(HB_HOOK_SETTER);
    typedef int64_t (__fastcall *FnOrig)(int64_t owner, int64_t shared_page_ctx);
    FnOrig orig = state ? (FnOrig)state->trampoline : NULL;
    return orig ? orig(owner, shared_page_ctx) : 0;
}

int64_t hb_call_route_original(int64_t a1, int64_t a2, int64_t a3, int64_t a4) {
    HbHookState *state = hb_hook_state(HB_HOOK_ROUTE);
    typedef int64_t (__fastcall *FnOrig)(int64_t a1, int64_t a2, int64_t a3, int64_t a4);
    FnOrig orig = state ? (FnOrig)state->trampoline : NULL;
    return orig ? orig(a1, a2, a3, a4) : 0;
}

int64_t hb_call_task_factory_original(int64_t a1, int64_t a2, int64_t a3,
                                      int64_t a4, int64_t a5, int64_t a6) {
    HbHookState *state = hb_hook_state(HB_HOOK_TASK_FACTORY);
    typedef int64_t (__fastcall *FnOrig)(int64_t a1, int64_t a2, int64_t a3,
                                         int64_t a4, int64_t a5, int64_t a6);
    FnOrig orig = state ? (FnOrig)state->trampoline : NULL;
    return orig ? orig(a1, a2, a3, a4, a5, a6) : 0;
}

int64_t hb_call_receive_mid_builder_original(int64_t a1, int64_t holder,
                                             int64_t payload) {
    HbHookState *state = hb_hook_state(HB_HOOK_RECEIVE_MID_BUILDER);
    typedef int64_t (__fastcall *FnOrig)(int64_t a1, int64_t holder, int64_t payload);
    FnOrig orig = state ? (FnOrig)state->trampoline : NULL;
    return orig ? orig(a1, holder, payload) : 0;
}

int64_t hb_call_task_factory_inner_original(int64_t a1, int64_t a2, int64_t a3,
                                            int64_t a4, int64_t a5, int64_t a6,
                                            int64_t a7, int64_t a8) {
    HbHookState *state = hb_hook_state(HB_HOOK_TASK_FACTORY_INNER);
    typedef int64_t (__fastcall *FnOrig)(int64_t a1, int64_t a2, int64_t a3,
                                         int64_t a4, int64_t a5, int64_t a6,
                                         int64_t a7, int64_t a8);
    FnOrig orig = state ? (FnOrig)state->trampoline : NULL;
    return orig ? orig(a1, a2, a3, a4, a5, a6, a7, a8) : 0;
}

void hb_call_receive_task_builder_original(int64_t parent_ctx, int64_t arg) {
    HbHookState *state = hb_hook_state(HB_HOOK_RECEIVE_TASK_BUILDER);
    typedef void (__fastcall *FnOrig)(int64_t parent_ctx, int64_t arg);
    FnOrig orig = state ? (FnOrig)state->trampoline : NULL;
    if (orig) orig(parent_ctx, arg);
}

int64_t hb_call_receive_route_builder_original(int64_t route_ctx) {
    HbHookState *state = hb_hook_state(HB_HOOK_RECEIVE_ROUTE_BUILDER);
    typedef int64_t (__fastcall *FnOrig)(int64_t route_ctx);
    FnOrig orig = state ? (FnOrig)state->trampoline : NULL;
    return orig ? orig(route_ctx) : 0;
}

int64_t hb_call_source_submit_original(int64_t a1, int64_t vector_arg,
                                       int64_t a3, int64_t a4) {
    HbHookState *state = hb_hook_state(HB_HOOK_SOURCE_SUBMIT);
    typedef int64_t (__fastcall *FnOrig)(int64_t a1, int64_t vector_arg,
                                         int64_t a3, int64_t a4);
    FnOrig orig = state ? (FnOrig)state->trampoline : NULL;
    return orig ? orig(a1, vector_arg, a3, a4) : 0;
}

int64_t hb_call_route_dispatcher_original(int64_t a1, int64_t a2) {
    HbHookState *state = hb_hook_state(HB_HOOK_ROUTE_DISPATCHER);
    typedef int64_t (__fastcall *FnOrig)(int64_t a1, int64_t a2);
    FnOrig orig = state ? (FnOrig)state->trampoline : NULL;
    return orig ? orig(a1, a2) : 0;
}

int64_t hb_call_route_temp_vector_original(int64_t a1, int64_t vector_arg,
                                           int64_t a3) {
    HbHookState *state = hb_hook_state(HB_HOOK_ROUTE_TEMP_VECTOR);
    typedef int64_t (__fastcall *FnOrig)(int64_t a1, int64_t vector_arg, int64_t a3);
    FnOrig orig = state ? (FnOrig)state->trampoline : NULL;
    return orig ? orig(a1, vector_arg, a3) : 0;
}

int64_t hb_call_route_vector_builder_original(int64_t route_mgr,
                                              int64_t vector_arg) {
    HbHookState *state = hb_hook_state(HB_HOOK_ROUTE_VECTOR_BUILDER);
    typedef int64_t (__fastcall *FnOrig)(int64_t route_mgr, int64_t vector_arg);
    FnOrig orig = state ? (FnOrig)state->trampoline : NULL;
    return orig ? orig(route_mgr, vector_arg) : 0;
}

int64_t hb_call_route_ctx_factory_original(int64_t out_obj, int64_t route_mgr,
                                           int64_t route_desc, int64_t flag) {
    HbHookState *state = hb_hook_state(HB_HOOK_ROUTE_CTX_FACTORY);
    typedef int64_t (__fastcall *FnOrig)(int64_t out_obj, int64_t route_mgr,
                                         int64_t route_desc, int64_t flag);
    FnOrig orig = state ? (FnOrig)state->trampoline : NULL;
    return orig ? orig(out_obj, route_mgr, route_desc, flag) : 0;
}

int64_t hb_call_route_classifier_original(int64_t route_desc) {
    HbHookState *state = hb_hook_state(HB_HOOK_ROUTE_CLASSIFIER);
    typedef int64_t (__fastcall *FnOrig)(int64_t route_desc);
    FnOrig orig = state ? (FnOrig)state->trampoline : NULL;
    return orig ? orig(route_desc) : 0;
}

int64_t hb_call_route_item_dispatch_original(int64_t a1, int64_t a2) {
    HbHookState *state = hb_hook_state(HB_HOOK_ROUTE_ITEM_DISPATCH);
    typedef int64_t (__fastcall *FnOrig)(int64_t a1, int64_t a2);
    FnOrig orig = state ? (FnOrig)state->trampoline : NULL;
    return orig ? orig(a1, a2) : 0;
}
