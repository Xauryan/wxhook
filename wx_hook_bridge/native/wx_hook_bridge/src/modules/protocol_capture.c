/*
 * Module: passive protocol-send capture.
 *
 * This module records real task-dispatch metadata while WeChat handles UI
 * sends. It is gated by bridge/control/protocol_capture.enable and stays
 * inactive unless that file exists.
 */

#include "../include/wx_bridge_protocol_capture.h"
#include "../include/wx_bridge_a8key_task.h"
#include "../include/wx_bridge_file_io.h"
#include "../include/wx_bridge_memory.h"
#include "../include/wx_bridge_offsets.h"
#include "../include/wx_bridge_paths.h"
#include "../include/wx_bridge_protocol.h"
#include "../include/wx_bridge_runtime.h"
#include "../include/wx_bridge_text.h"
#include <intrin.h>

#define PROTOCOL_CAPTURE_PATCH_SIZE 17
#define PROTOCOL_CAPTURE_LOG_BASENAME "protocol_capture.jsonl"

typedef int64_t (__fastcall *ProtocolTaskDispatchFn)(void *manager, void *task);

typedef struct ProtocolCaptureHookState {
    void *trampoline;
    ProtocolTaskDispatchFn original;
    BYTE orig[PROTOCOL_CAPTURE_PATCH_SIZE];
    volatile LONG installed;
    volatile LONG dispatch_count;
    volatile LONG64 last_result;
} ProtocolCaptureHookState;

typedef struct ProtocolOffsetCheck {
    const char *name;
    DWORD offset;
} ProtocolOffsetCheck;

static ProtocolCaptureHookState s_protocol_capture = {0};

int64_t __fastcall protocol_capture_task_dispatch_hook(void *manager, void *task);

static const BYTE PROTOCOL_TASK_DISPATCH_PROLOGUE[PROTOCOL_CAPTURE_PATCH_SIZE] = {
    0x55, 0x41, 0x57, 0x41, 0x56, 0x41, 0x54, 0x56, 0x57, 0x53,
    0x48, 0x81, 0xEC, 0xA0, 0x03, 0x00, 0x00
};

static const ProtocolOffsetCheck PROTOCOL_NATIVE_SEND_CHECKS[] = {
    {"WX_OFF_SEND_ONECALL_TEXT_TASK", WX_OFF_SEND_ONECALL_TEXT_TASK},
    {"WX_OFF_SEND_TEXT_SERVICE", WX_OFF_SEND_TEXT_SERVICE},
    {"WX_OFF_SEND_CALL1_TEXT", WX_OFF_SEND_CALL1_TEXT},
    {"WX_OFF_SOURCE_SEND", WX_OFF_SOURCE_SEND},
    {"WX_OFF_VOICE_STAGE_SEND_FINAL", WX_OFF_VOICE_STAGE_SEND_FINAL}
};

static int unsafe_dispatch_enabled(void) {
    return wxb_file_exists(PROTOCOL_CAPTURE_UNSAFE_DISPATCH_ENABLE_FILE) ? 1 : 0;
}

static void protocol_append_u64(char **p, char *end, unsigned __int64 value) {
    char tmp[32];
    char out[32];
    int i = 0;
    int j = 0;
    if (!value) {
        wxb_append_raw(p, end, "0");
        return;
    }
    while (value && i < (int)sizeof(tmp)) {
        tmp[i++] = (char)('0' + (value % 10));
        value /= 10;
    }
    while (i) out[j++] = tmp[--i];
    out[j] = 0;
    wxb_append_raw(p, end, out);
}

static void protocol_append_hex64(char **p, char *end, unsigned __int64 value) {
    char num[32];
    wsprintfA(num, "%I64x", value);
    wxb_append_raw(p, end, "\"0x");
    wxb_append_raw(p, end, num);
    wxb_append_raw(p, end, "\"");
}

static int protocol_is_abs_jump_patch(BYTE *target) {
    __try {
        return target &&
               target[0] == 0x48 &&
               target[1] == 0xB8 &&
               target[10] == 0xFF &&
               target[11] == 0xE0;
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
        return 0;
    }
}

static int protocol_code_shape_readable(BYTE *target) {
    __try {
        if (!target) return 0;
        if (protocol_is_abs_jump_patch(target)) return 0;
        if (target[0] == 0x00 || target[0] == 0xCC) return 0;
        return 1;
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
        return 0;
    }
}

static int protocol_validate_offsets(int emit_log) {
    HMODULE mod = wxb_weixin_module();
    BYTE *base = (BYTE *)mod;
    int ok = 1;
    char line[4096];
    char *p = line;
    char *end = line + sizeof(line) - 1;
    if (!mod) {
        if (emit_log) wxb_write_status("protocol capture skipped: Weixin.dll not loaded\n");
        return 0;
    }

    wxb_append_raw(&p, end, "{\"event\":\"offset_validation\",\"log\":\"");
    wxb_append_escaped(&p, end, PROTOCOL_CAPTURE_LOG_BASENAME);
    wxb_append_raw(&p, end, "\",\"moduleBase\":");
    protocol_append_hex64(&p, end, (unsigned __int64)base);

    if (!wxb_bytes_equal(base + WX_OFF_TASK_DISPATCH,
                         PROTOCOL_TASK_DISPATCH_PROLOGUE,
                         PROTOCOL_CAPTURE_PATCH_SIZE)) {
        ok = 0;
        wxb_append_raw(&p, end, ",\"taskDispatcher\":\"prologue_mismatch\"");
    } else {
        wxb_append_raw(&p, end, ",\"taskDispatcher\":\"ok\"");
    }

    wxb_append_raw(&p, end, ",\"nativeSendChecks\":[");
    for (int i = 0; i < (int)(sizeof(PROTOCOL_NATIVE_SEND_CHECKS) / sizeof(PROTOCOL_NATIVE_SEND_CHECKS[0])); i++) {
        const ProtocolOffsetCheck *check = &PROTOCOL_NATIVE_SEND_CHECKS[i];
        BYTE *target = base + check->offset;
        int shape_ok = protocol_code_shape_readable(target);
        if (i) wxb_append_raw(&p, end, ",");
        wxb_append_raw(&p, end, "{\"name\":\"");
        wxb_append_escaped(&p, end, check->name);
        wxb_append_raw(&p, end, "\",\"offset\":");
        protocol_append_hex64(&p, end, check->offset);
        wxb_append_raw(&p, end, ",\"ok\":");
        wxb_append_raw(&p, end, shape_ok ? "true" : "false");
        wxb_append_raw(&p, end, "}");
        if (!shape_ok) ok = 0;
    }
    wxb_append_raw(&p, end, "]");
    wxb_append_raw(&p, end, ",\"packEncryptBoundaryChecks\":[]");
    wxb_append_raw(&p, end, ",\"ok\":");
    wxb_append_raw(&p, end, ok ? "true" : "false");
    wxb_append_raw(&p, end, "}\r\n");
    *p = 0;
    if (emit_log) wxb_append_file_bytes(PROTOCOL_CAPTURE_LOG, line, (DWORD)(p - line));
    return ok;
}

int wxb_protocol_capture_offsets_valid(void) {
    return protocol_validate_offsets(0);
}

static int build_protocol_capture_trampoline(BYTE *p, BYTE *target) {
    BYTE *start = p;
    wxb_copy_bytes(p, s_protocol_capture.orig, sizeof(s_protocol_capture.orig));
    p += sizeof(s_protocol_capture.orig);
    *p++ = 0x48;
    *p++ = 0xB8;
    *(int64_t *)p = (int64_t)(target + sizeof(s_protocol_capture.orig));
    p += 8;
    *p++ = 0xFF;
    *p++ = 0xE0;
    return (int)(p - start);
}

static int protocol_capture_patch_target(BYTE *target) {
    BYTE patch[PROTOCOL_CAPTURE_PATCH_SIZE];
    DWORD old = 0;
    for (int i = 0; i < (int)sizeof(patch); i++) patch[i] = 0x90;
    patch[0] = 0x48;
    patch[1] = 0xB8;
    *(int64_t *)(patch + 2) = (int64_t)protocol_capture_task_dispatch_hook;
    patch[10] = 0xFF;
    patch[11] = 0xE0;
    if (!VirtualProtect(target, sizeof(patch), PAGE_EXECUTE_READWRITE, &old)) return 0;
    wxb_copy_bytes(target, patch, sizeof(patch));
    VirtualProtect(target, sizeof(patch), old, &old);
    FlushInstructionCache(GetCurrentProcess(), target, sizeof(patch));
    return 1;
}

static int protocol_capture_read_task_qword(void *task, unsigned __int64 *value) {
    __try {
        if (!task || !value) return 0;
        if (!wxb_is_readable_ptr((int64_t)task, 8)) return 0;
        *value = (unsigned __int64)wxb_r64(task);
        return 1;
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
        return 0;
    }
}

static void protocol_capture_log_dispatch(void *manager,
                                          void *task,
                                          void *return_addr,
                                          int64_t result) {
    char line[4096];
    char *p = line;
    char *end = line + sizeof(line) - 1;
    unsigned __int64 task_vtable = 0;
    int task_vtable_ok = protocol_capture_read_task_qword(task, &task_vtable);
    LONG dispatch_count = InterlockedCompareExchange(&s_protocol_capture.dispatch_count, 0, 0);

    wxb_append_raw(&p, end, "{\"event\":\"task_dispatch\"");
    wxb_append_raw(&p, end, ",\"pid\":");
    protocol_append_u64(&p, end, GetCurrentProcessId());
    wxb_append_raw(&p, end, ",\"tid\":");
    protocol_append_u64(&p, end, GetCurrentThreadId());
    wxb_append_raw(&p, end, ",\"tickMs\":");
    protocol_append_u64(&p, end, GetTickCount64());
    wxb_append_raw(&p, end, ",\"dispatchCount\":");
    protocol_append_u64(&p, end, (unsigned __int64)dispatch_count);
    wxb_append_raw(&p, end, ",\"manager\":");
    protocol_append_hex64(&p, end, (unsigned __int64)manager);
    wxb_append_raw(&p, end, ",\"task\":");
    protocol_append_hex64(&p, end, (unsigned __int64)task);
    wxb_append_raw(&p, end, ",\"taskVtable\":");
    if (task_vtable_ok) protocol_append_hex64(&p, end, task_vtable);
    else wxb_append_raw(&p, end, "null");
    wxb_append_raw(&p, end, ",\"returnAddress\":");
    protocol_append_hex64(&p, end, (unsigned __int64)return_addr);
    wxb_append_raw(&p, end, ",\"result\":");
    protocol_append_hex64(&p, end, (unsigned __int64)result);
    wxb_append_raw(&p, end, "}\r\n");
    *p = 0;
    wxb_append_file_bytes(PROTOCOL_CAPTURE_LOG, line, (DWORD)(p - line));
}

int64_t __fastcall protocol_capture_task_dispatch_hook(void *manager, void *task) {
    ProtocolTaskDispatchFn original = s_protocol_capture.original;
    void *return_addr = _ReturnAddress();
    int64_t result = 0;
    InterlockedIncrement(&s_protocol_capture.dispatch_count);
    if (original) result = original(manager, task);
    InterlockedExchange64(&s_protocol_capture.last_result, result);
    __try {
        protocol_capture_log_dispatch(manager, task, return_addr, result);
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
    }
    return result;
}

static void protocol_capture_install_hook(void) {
    BYTE *target;
    int tlen;
    if (wxb_protocol_capture_hook_installed()) return;
    if (!unsafe_dispatch_enabled()) {
        wxb_write_status("protocol capture skipped: unsafe dispatch hook not armed\n");
        return;
    }
    if (a8key_dispatch_hook_installed()) {
        wxb_write_status("protocol capture skipped: a8key dispatch hook already installed\n");
        return;
    }
    if (!protocol_validate_offsets(1)) {
        wxb_write_status("protocol capture skipped: offset validation failed\n");
        return;
    }
    target = (BYTE *)wxb_weixin_module() + WX_OFF_TASK_DISPATCH;
    wxb_copy_bytes(s_protocol_capture.orig, target, sizeof(s_protocol_capture.orig));
    s_protocol_capture.trampoline = wxb_alloc_near(target, 256);
    if (!s_protocol_capture.trampoline) {
        s_protocol_capture.trampoline = VirtualAlloc(NULL, 256, MEM_COMMIT | MEM_RESERVE, PAGE_EXECUTE_READWRITE);
    }
    if (!s_protocol_capture.trampoline) {
        wxb_write_status("protocol capture hook failed: trampoline alloc\n");
        return;
    }
    tlen = build_protocol_capture_trampoline((BYTE *)s_protocol_capture.trampoline, target);
    FlushInstructionCache(GetCurrentProcess(), s_protocol_capture.trampoline, tlen);
    s_protocol_capture.original = (ProtocolTaskDispatchFn)s_protocol_capture.trampoline;
    if (!protocol_capture_patch_target(target)) {
        s_protocol_capture.original = NULL;
        VirtualFree(s_protocol_capture.trampoline, 0, MEM_RELEASE);
        s_protocol_capture.trampoline = NULL;
        wxb_write_status("protocol capture hook failed: patch write\n");
        return;
    }
    InterlockedExchange(&s_protocol_capture.dispatch_count, 0);
    InterlockedExchange(&s_protocol_capture.installed, 1);
    wxb_write_status("protocol capture hook installed\n");
}

void wxb_refresh_protocol_capture_hook(void) {
    if (!wxb_weixin_module()) return;
    if (wxb_file_exists(PROTOCOL_CAPTURE_ENABLE_FILE)) {
        protocol_capture_install_hook();
    } else {
        wxb_uninstall_protocol_capture_hook();
    }
}

void wxb_uninstall_protocol_capture_hook(void) {
    BYTE *target;
    DWORD old = 0;
    if (!wxb_protocol_capture_hook_installed() || !wxb_weixin_module()) return;
    target = (BYTE *)wxb_weixin_module() + WX_OFF_TASK_DISPATCH;
    if (VirtualProtect(target, sizeof(s_protocol_capture.orig), PAGE_EXECUTE_READWRITE, &old)) {
        wxb_copy_bytes(target, s_protocol_capture.orig, sizeof(s_protocol_capture.orig));
        VirtualProtect(target, sizeof(s_protocol_capture.orig), old, &old);
        FlushInstructionCache(GetCurrentProcess(), target, sizeof(s_protocol_capture.orig));
    }
    InterlockedExchange(&s_protocol_capture.installed, 0);
    s_protocol_capture.original = NULL;
    if (s_protocol_capture.trampoline) {
        VirtualFree(s_protocol_capture.trampoline, 0, MEM_RELEASE);
        s_protocol_capture.trampoline = NULL;
    }
    wxb_write_status("protocol capture hook uninstalled\n");
}

int wxb_protocol_capture_hook_installed(void) {
    return InterlockedCompareExchange(&s_protocol_capture.installed, 0, 0) != 0;
}
