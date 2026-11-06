/*
 * Module: forward trace hook installation and teardown.
 */

#include "../include/wx_bridge_forward_hooks.h"
#include "../include/wx_bridge_forward_entrypoints.h"
#include "../include/wx_bridge_memory.h"
#include "../include/wx_bridge_offsets.h"
#include "../include/wx_bridge_protocol.h"
#include "../include/wx_bridge_runtime.h"
#include "../include/wx_bridge_native_call.h"

typedef struct ForwardHookState {
    void *dispatch_trampoline;
    BYTE orig_dispatch[13];
    volatile LONG dispatch_installed;
    void *submit_trampoline;
    BYTE orig_submit[15];
    volatile LONG submit_installed;
    void *ui_entry_trampoline;
    BYTE orig_ui_entry[19];
    volatile LONG ui_entry_installed;
} ForwardHookState;

static ForwardHookState g_forward_hook_state = {0};

static const BYTE WX_ORIG_FORWARD_DISPATCH_BYTES[13] = {
    0x55,0x41,0x56,0x56,0x57,0x53,0x48,0x81,0xEC,0x90,0x02,0x00,0x00
};
static const BYTE WX_ORIG_FORWARD_SUBMIT_BYTES[15] = {
    0x55,0x41,0x57,0x41,0x56,0x56,0x57,0x53,0x48,0x81,0xEC,0x68,0x02,0x00,0x00
};
static const BYTE WX_ORIG_FORWARD_UI_ENTRY_BYTES[19] = {
    0x55,0x41,0x57,0x41,0x56,0x41,0x55,0x41,0x54,0x56,0x57,0x53,0x48,0x81,0xEC,0x38,
    0x02,0x00,0x00
};

static int build_forward_dispatch_entry_trampoline(BYTE *p, BYTE *target) {
    BYTE *start = p;
    copy_bytes(p, g_forward_hook_state.orig_dispatch, sizeof(g_forward_hook_state.orig_dispatch));
    p += sizeof(g_forward_hook_state.orig_dispatch);
    *p++ = 0x48; *p++ = 0xB8;         /* mov rax, target+patch_len */
    *(int64_t *)p = (int64_t)(target + sizeof(g_forward_hook_state.orig_dispatch)); p += 8;
    *p++ = 0xFF; *p++ = 0xE0;         /* jmp rax */
    return (int)(p - start);
}

static int build_forward_submit_entry_trampoline(BYTE *p, BYTE *target) {
    BYTE *start = p;
    copy_bytes(p, g_forward_hook_state.orig_submit, sizeof(g_forward_hook_state.orig_submit));
    p += sizeof(g_forward_hook_state.orig_submit);
    *p++ = 0x48; *p++ = 0xB8;         /* mov rax, target+patch_len */
    *(int64_t *)p = (int64_t)(target + sizeof(g_forward_hook_state.orig_submit)); p += 8;
    *p++ = 0xFF; *p++ = 0xE0;         /* jmp rax */
    return (int)(p - start);
}

static int build_forward_ui_entry_trampoline(BYTE *p, BYTE *target) {
    BYTE *start = p;
    copy_bytes(p, g_forward_hook_state.orig_ui_entry, sizeof(g_forward_hook_state.orig_ui_entry));
    p += sizeof(g_forward_hook_state.orig_ui_entry);
    *p++ = 0x48; *p++ = 0xB8;         /* mov rax, target+patch_len */
    *(int64_t *)p = (int64_t)(target + sizeof(g_forward_hook_state.orig_ui_entry)); p += 8;
    *p++ = 0xFF; *p++ = 0xE0;         /* jmp rax */
    return (int)(p - start);
}

void install_forward_ui_entry_trace_hook(void) {
    if (g_forward_hook_state.ui_entry_installed || !wxb_weixin_module()) return;
    BYTE *target = (BYTE *)((int64_t)wxb_weixin_module() + WX_OFF_FORWARD_UI_ENTRY);
    if (target[0] == 0x48 && target[1] == 0xB8) {
        DWORD oldp = 0;
        VirtualProtect(target, sizeof(WX_ORIG_FORWARD_UI_ENTRY_BYTES), PAGE_EXECUTE_READWRITE, &oldp);
        copy_bytes(target, WX_ORIG_FORWARD_UI_ENTRY_BYTES, sizeof(WX_ORIG_FORWARD_UI_ENTRY_BYTES));
        VirtualProtect(target, sizeof(WX_ORIG_FORWARD_UI_ENTRY_BYTES), oldp, &oldp);
        FlushInstructionCache(GetCurrentProcess(), target, sizeof(WX_ORIG_FORWARD_UI_ENTRY_BYTES));
        write_status("forward ui entry stale hook restored\n");
    }

    copy_bytes(g_forward_hook_state.orig_ui_entry, target, sizeof(g_forward_hook_state.orig_ui_entry));
    g_forward_hook_state.ui_entry_trampoline = VirtualAlloc(NULL, 256, MEM_COMMIT | MEM_RESERVE, PAGE_EXECUTE_READWRITE);
    if (!g_forward_hook_state.ui_entry_trampoline) {
        write_status("forward ui entry trace hook alloc failed\n");
        return;
    }
    int tlen = build_forward_ui_entry_trampoline((BYTE *)g_forward_hook_state.ui_entry_trampoline, target);
    FlushInstructionCache(GetCurrentProcess(), g_forward_hook_state.ui_entry_trampoline, tlen);

    BYTE patch[19];
    for (int i = 0; i < (int)sizeof(patch); i++) patch[i] = 0x90;
    patch[0] = 0x48; patch[1] = 0xB8;
    *(int64_t *)(patch + 2) = (int64_t)forward_ui_entry_hook;
    patch[10] = 0xFF; patch[11] = 0xE0;

    DWORD old = 0;
    VirtualProtect(target, sizeof(patch), PAGE_EXECUTE_READWRITE, &old);
    copy_bytes(target, patch, sizeof(patch));
    VirtualProtect(target, sizeof(patch), old, &old);
    FlushInstructionCache(GetCurrentProcess(), target, sizeof(patch));
    InterlockedExchange(&g_forward_hook_state.ui_entry_installed, 1);
    write_status("forward ui entry trace hook installed\n");
}

void install_forward_submit_trace_hook(void) {
    if (g_forward_hook_state.submit_installed || !wxb_weixin_module()) return;
    BYTE *target = (BYTE *)((int64_t)wxb_weixin_module() + WX_OFF_FORWARD_SUBMIT);
    if (target[0] == 0x48 && target[1] == 0xB8) {
        DWORD oldp = 0;
        VirtualProtect(target, sizeof(WX_ORIG_FORWARD_SUBMIT_BYTES), PAGE_EXECUTE_READWRITE, &oldp);
        copy_bytes(target, WX_ORIG_FORWARD_SUBMIT_BYTES, sizeof(WX_ORIG_FORWARD_SUBMIT_BYTES));
        VirtualProtect(target, sizeof(WX_ORIG_FORWARD_SUBMIT_BYTES), oldp, &oldp);
        FlushInstructionCache(GetCurrentProcess(), target, sizeof(WX_ORIG_FORWARD_SUBMIT_BYTES));
        write_status("forward submit stale hook restored\n");
    }

    copy_bytes(g_forward_hook_state.orig_submit, target, sizeof(g_forward_hook_state.orig_submit));
    g_forward_hook_state.submit_trampoline = VirtualAlloc(NULL, 256, MEM_COMMIT | MEM_RESERVE, PAGE_EXECUTE_READWRITE);
    if (!g_forward_hook_state.submit_trampoline) {
        write_status("forward submit trace hook alloc failed\n");
        return;
    }
    int tlen = build_forward_submit_entry_trampoline((BYTE *)g_forward_hook_state.submit_trampoline, target);
    FlushInstructionCache(GetCurrentProcess(), g_forward_hook_state.submit_trampoline, tlen);

    BYTE patch[15];
    for (int i = 0; i < (int)sizeof(patch); i++) patch[i] = 0x90;
    patch[0] = 0x48; patch[1] = 0xB8;
    *(int64_t *)(patch + 2) = (int64_t)forward_submit_hook;
    patch[10] = 0xFF; patch[11] = 0xE0;

    DWORD old = 0;
    VirtualProtect(target, sizeof(patch), PAGE_EXECUTE_READWRITE, &old);
    copy_bytes(target, patch, sizeof(patch));
    VirtualProtect(target, sizeof(patch), old, &old);
    FlushInstructionCache(GetCurrentProcess(), target, sizeof(patch));
    InterlockedExchange(&g_forward_hook_state.submit_installed, 1);
    write_status("forward submit trace hook installed\n");
}

void install_forward_dispatch_hook(void) {
    if (g_forward_hook_state.dispatch_installed || !wxb_weixin_module()) return;
    BYTE *target = (BYTE *)((int64_t)wxb_weixin_module() + WX_OFF_FORWARD_DISPATCH);
    if (target[0] == 0x48 && target[1] == 0xB8) {
        DWORD oldp = 0;
        VirtualProtect(target, sizeof(WX_ORIG_FORWARD_DISPATCH_BYTES), PAGE_EXECUTE_READWRITE, &oldp);
        copy_bytes(target, WX_ORIG_FORWARD_DISPATCH_BYTES, sizeof(WX_ORIG_FORWARD_DISPATCH_BYTES));
        VirtualProtect(target, sizeof(WX_ORIG_FORWARD_DISPATCH_BYTES), oldp, &oldp);
        FlushInstructionCache(GetCurrentProcess(), target, sizeof(WX_ORIG_FORWARD_DISPATCH_BYTES));
        write_status("forward dispatch stale hook restored\n");
    }

    copy_bytes(g_forward_hook_state.orig_dispatch, target, sizeof(g_forward_hook_state.orig_dispatch));
    g_forward_hook_state.dispatch_trampoline = VirtualAlloc(NULL, 256, MEM_COMMIT | MEM_RESERVE, PAGE_EXECUTE_READWRITE);
    if (!g_forward_hook_state.dispatch_trampoline) {
        write_status("forward dispatch hook alloc failed\n");
        return;
    }
    int tlen = build_forward_dispatch_entry_trampoline((BYTE *)g_forward_hook_state.dispatch_trampoline, target);
    FlushInstructionCache(GetCurrentProcess(), g_forward_hook_state.dispatch_trampoline, tlen);

    BYTE patch[13];
    for (int i = 0; i < (int)sizeof(patch); i++) patch[i] = 0x90;
    patch[0] = 0x48; patch[1] = 0xB8;
    *(int64_t *)(patch + 2) = (int64_t)forward_dispatch_hook;
    patch[10] = 0xFF; patch[11] = 0xE0;

    DWORD old = 0;
    VirtualProtect(target, sizeof(patch), PAGE_EXECUTE_READWRITE, &old);
    copy_bytes(target, patch, sizeof(patch));
    VirtualProtect(target, sizeof(patch), old, &old);
    FlushInstructionCache(GetCurrentProcess(), target, sizeof(patch));
    InterlockedExchange(&g_forward_hook_state.dispatch_installed, 1);
    write_status("forward dispatch hook installed\n");
}

void uninstall_forward_submit_trace_hook(void) {
    if (!g_forward_hook_state.submit_installed || !wxb_weixin_module()) return;
    BYTE *target = (BYTE *)((int64_t)wxb_weixin_module() + WX_OFF_FORWARD_SUBMIT);
    DWORD old = 0;
    VirtualProtect(target, sizeof(g_forward_hook_state.orig_submit), PAGE_EXECUTE_READWRITE, &old);
    copy_bytes(target, g_forward_hook_state.orig_submit, sizeof(g_forward_hook_state.orig_submit));
    VirtualProtect(target, sizeof(g_forward_hook_state.orig_submit), old, &old);
    FlushInstructionCache(GetCurrentProcess(), target, sizeof(g_forward_hook_state.orig_submit));
    InterlockedExchange(&g_forward_hook_state.submit_installed, 0);
    if (g_forward_hook_state.submit_trampoline) {
        VirtualFree(g_forward_hook_state.submit_trampoline, 0, MEM_RELEASE);
        g_forward_hook_state.submit_trampoline = NULL;
    }
}

void uninstall_forward_ui_entry_trace_hook(void) {
    if (!g_forward_hook_state.ui_entry_installed || !wxb_weixin_module()) return;
    BYTE *target = (BYTE *)((int64_t)wxb_weixin_module() + WX_OFF_FORWARD_UI_ENTRY);
    DWORD old = 0;
    VirtualProtect(target, sizeof(g_forward_hook_state.orig_ui_entry), PAGE_EXECUTE_READWRITE, &old);
    copy_bytes(target, g_forward_hook_state.orig_ui_entry, sizeof(g_forward_hook_state.orig_ui_entry));
    VirtualProtect(target, sizeof(g_forward_hook_state.orig_ui_entry), old, &old);
    FlushInstructionCache(GetCurrentProcess(), target, sizeof(g_forward_hook_state.orig_ui_entry));
    InterlockedExchange(&g_forward_hook_state.ui_entry_installed, 0);
    if (g_forward_hook_state.ui_entry_trampoline) {
        VirtualFree(g_forward_hook_state.ui_entry_trampoline, 0, MEM_RELEASE);
        g_forward_hook_state.ui_entry_trampoline = NULL;
    }
}

void uninstall_forward_dispatch_hook(void) {
    if (!g_forward_hook_state.dispatch_installed || !wxb_weixin_module()) return;
    BYTE *target = (BYTE *)((int64_t)wxb_weixin_module() + WX_OFF_FORWARD_DISPATCH);
    DWORD old = 0;
    forward_reset_runtime_patch_state();
    VirtualProtect(target, sizeof(g_forward_hook_state.orig_dispatch), PAGE_EXECUTE_READWRITE, &old);
    copy_bytes(target, g_forward_hook_state.orig_dispatch, sizeof(g_forward_hook_state.orig_dispatch));
    VirtualProtect(target, sizeof(g_forward_hook_state.orig_dispatch), old, &old);
    FlushInstructionCache(GetCurrentProcess(), target, sizeof(g_forward_hook_state.orig_dispatch));
    InterlockedExchange(&g_forward_hook_state.dispatch_installed, 0);
    if (g_forward_hook_state.dispatch_trampoline) {
        VirtualFree(g_forward_hook_state.dispatch_trampoline, 0, MEM_RELEASE);
        g_forward_hook_state.dispatch_trampoline = NULL;
    }
}

int forward_ui_entry_trace_hook_installed(void) {
    return InterlockedCompareExchange(&g_forward_hook_state.ui_entry_installed, 0, 0) != 0;
}

int forward_submit_trace_hook_installed(void) {
    return InterlockedCompareExchange(&g_forward_hook_state.submit_installed, 0, 0) != 0;
}

int forward_dispatch_hook_installed(void) {
    return InterlockedCompareExchange(&g_forward_hook_state.dispatch_installed, 0, 0) != 0;
}

int64_t forward_call_dispatch_original(int64_t manager, int64_t seq, int64_t obj) {
    if (!g_forward_hook_state.dispatch_trampoline) return 0;
    return ((FnCall3Args)g_forward_hook_state.dispatch_trampoline)(manager, seq, obj);
}

int64_t forward_call_submit_original(int64_t manager, int64_t pair,
                                     int64_t variant, int64_t do_dispatch) {
    if (!g_forward_hook_state.submit_trampoline) return 0;
    return ((FnCall4)g_forward_hook_state.submit_trampoline)(manager, pair, variant, do_dispatch);
}

int64_t forward_call_ui_entry_original(int64_t ctx, int64_t arg2,
                                       int64_t arg3, int64_t arg4) {
    if (!g_forward_hook_state.ui_entry_trampoline) return 0;
    return ((FnCall4)g_forward_hook_state.ui_entry_trampoline)(ctx, arg2, arg3, arg4);
}
