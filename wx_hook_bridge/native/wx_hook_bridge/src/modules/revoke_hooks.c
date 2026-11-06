/*
 * Module: revoke entry/dispatch hook installation and teardown.
 */

#include "../include/wx_bridge_revoke_hooks.h"
#include "../include/wx_bridge_file_io.h"
#include "../include/wx_bridge_memory.h"
#include "../include/wx_bridge_offsets.h"
#include "../include/wx_bridge_paths.h"
#include "../include/wx_bridge_protocol.h"
#include "../include/wx_bridge_revoke_cache.h"
#include "../include/wx_bridge_revoke_forward.h"
#include "../include/wx_bridge_revoke_protocol.h"
#include "../include/wx_bridge_room_contacts.h"
#include "../include/wx_bridge_runtime.h"
#include "../include/wx_bridge_text.h"
#include "../include/wx_bridge_wechat_string.h"

typedef struct RevokeEntryHookState {
    void *trampoline;
    BYTE orig_entry[5];
    volatile LONG installed;
} RevokeEntryHookState;

typedef struct RevokeDispatchHookState {
    void *trampoline;
    BYTE orig_dispatch[5];
    volatile LONG installed;
} RevokeDispatchHookState;

static RevokeEntryHookState g_revoke_entry_hook_state = {0};
static RevokeDispatchHookState g_revoke_dispatch_hook_state = {0};
static void *g_revoke_real_trampoline = NULL;
static BYTE g_orig_revoke_real[5] = {0};
static volatile LONG g_revoke_real_hook_installed = 0;

static const BYTE WX_ORIG_REVOKE_DISPATCH_BYTES[5] = {
    0xE8,0x7B,0x00,0x00,0x00
};

static const BYTE WX_ORIG_REVOKE_REAL_BYTES[5] = {
    0x55,0x41,0x57,0x41,0x56
};

static volatile LONG g_revoke_entry_hit = 0;
static volatile LONG g_revoke_real_hit = 0;

static void handle_revoke_entry(int64_t msg_base) {
    InterlockedIncrement(&g_revoke_entry_hit);

    char content[8192] = {0};
    wx_read_str(msg_base + WX_MSG_CONTENT_OFF, content, sizeof(content));
    if (content[0] == 0) return;
    if (!str_has(content, "<revokemsg")) return;

    char dbg[512];
    wsprintfA(dbg, "entry: REVOKE DETECTED count=%d\r\n", (int)g_revoke_entry_hit);
    write_file_bytes(REVOKE_LOG, dbg, lstrlenA(dbg));

    int64_t orig_msgid = parse_revoke_msgid(content);
    if (!orig_msgid) {
        write_file_bytes(REVOKE_LOG, "entry: parse msgid FAILED\r\n", 26);
        return;
    }

    char orig_s[32];
    i64toa10(orig_msgid, orig_s);
    wsprintfA(dbg, "entry: searching cache for msgid=%s\r\n", orig_s);
    write_file_bytes(REVOKE_LOG, dbg, lstrlenA(dbg));

    handle_revoke_notice_by_id(orig_msgid, "", 0, "revoke_entry");
}

static int build_revoke_entry_trampoline(BYTE *p, int64_t return_addr, int64_t handler_addr) {
    BYTE *start = p;
    BYTE save[] = {
        0x9C,
        0x50,0x51,0x52,
        0x41,0x50,0x41,0x51,
        0x41,0x52,0x41,0x53,
        0x48,0x83,0xEC,0x28,
    };
    copy_bytes(p, save, sizeof(save)); p += sizeof(save);

    *p++ = 0x48; *p++ = 0x89; *p++ = 0xD1;  /* mov rcx, rdx */
    *p++ = 0x48; *p++ = 0xB8;
    *(int64_t *)p = handler_addr; p += 8;
    *p++ = 0xFF; *p++ = 0xD0;

    BYTE restore[] = {
        0x48,0x83,0xC4,0x28,
        0x41,0x5B,0x41,0x5A,
        0x41,0x59,0x41,0x58,
        0x5A,0x59,0x58,
        0x9D,
    };
    copy_bytes(p, restore, sizeof(restore)); p += sizeof(restore);

    *p++ = 0x55;
    *p++ = 0x41; *p++ = 0x57;
    *p++ = 0x41; *p++ = 0x56;

    *p++ = 0x48; *p++ = 0xB8;
    *(int64_t *)p = return_addr; p += 8;
    *p++ = 0xFF; *p++ = 0xE0;

    return (int)(p - start);
}

void install_revoke_entry_hook(void) {
    if (g_revoke_entry_hook_state.installed || !wxb_weixin_module()) return;

    BYTE *target = (BYTE *)((int64_t)wxb_weixin_module() + WX_OFF_REVOKE_HANDLER);
    BYTE expected[5] = {0x55, 0x41, 0x57, 0x41, 0x56};
    for (int i = 0; i < 5; i++) {
        if (target[i] != expected[i]) {
            DWORD oldp = 0;
            VirtualProtect(target, 5, PAGE_EXECUTE_READWRITE, &oldp);
            copy_bytes(target, expected, 5);
            VirtualProtect(target, 5, oldp, &oldp);
            FlushInstructionCache(GetCurrentProcess(), target, 5);
            break;
        }
    }

    copy_bytes(g_revoke_entry_hook_state.orig_entry, target, 5);

    g_revoke_entry_hook_state.trampoline = alloc_near(target, 256);
    if (!g_revoke_entry_hook_state.trampoline) return;

    int tlen = build_revoke_entry_trampoline(
        (BYTE *)g_revoke_entry_hook_state.trampoline,
        (int64_t)target + 5,
        (int64_t)handle_revoke_entry);
    FlushInstructionCache(GetCurrentProcess(), g_revoke_entry_hook_state.trampoline, tlen);

    int64_t rel32 = (int64_t)g_revoke_entry_hook_state.trampoline - ((int64_t)target + 5);
    if (rel32 < INT32_MIN || rel32 > INT32_MAX) {
        VirtualFree(g_revoke_entry_hook_state.trampoline, 0, MEM_RELEASE);
        g_revoke_entry_hook_state.trampoline = NULL;
        return;
    }

    BYTE patch[5];
    patch[0] = 0xE9;
    *(int32_t *)(patch + 1) = (int32_t)rel32;

    DWORD old = 0;
    VirtualProtect(target, 5, PAGE_EXECUTE_READWRITE, &old);
    copy_bytes(target, patch, 5);
    VirtualProtect(target, 5, old, &old);
    FlushInstructionCache(GetCurrentProcess(), target, 5);
    InterlockedExchange(&g_revoke_entry_hook_state.installed, 1);
    write_status("revoke entry hook installed\n");
}

void uninstall_revoke_entry_hook(void) {
    if (!g_revoke_entry_hook_state.installed || !wxb_weixin_module()) return;
    BYTE *target = (BYTE *)((int64_t)wxb_weixin_module() + WX_OFF_REVOKE_HANDLER);
    DWORD old = 0;
    VirtualProtect(target, sizeof(g_revoke_entry_hook_state.orig_entry), PAGE_EXECUTE_READWRITE, &old);
    copy_bytes(target, g_revoke_entry_hook_state.orig_entry, sizeof(g_revoke_entry_hook_state.orig_entry));
    VirtualProtect(target, sizeof(g_revoke_entry_hook_state.orig_entry), old, &old);
    FlushInstructionCache(GetCurrentProcess(), target, sizeof(g_revoke_entry_hook_state.orig_entry));
    InterlockedExchange(&g_revoke_entry_hook_state.installed, 0);
    if (g_revoke_entry_hook_state.trampoline) {
        VirtualFree(g_revoke_entry_hook_state.trampoline, 0, MEM_RELEASE);
        g_revoke_entry_hook_state.trampoline = NULL;
    }
}

static void handle_dispatch_revoke(int64_t msg_base) {
    (void)msg_base;
    write_file_bytes(REVOKE_LOG, "dispatch_hit", 12);
}

static int build_dispatch_trampoline(BYTE *p, int64_t orig_call_target,
                                     int64_t return_addr, int64_t handler_addr) {
    BYTE *start = p;
    BYTE save[] = {
        0x9C,
        0x50,0x51,0x52,
        0x41,0x50,0x41,0x51,
        0x41,0x52,0x41,0x53,
        0x48,0x83,0xEC,0x28,
    };
    copy_bytes(p, save, sizeof(save)); p += sizeof(save);

    *p++ = 0x48; *p++ = 0x89; *p++ = 0xF9;  /* mov rcx, rdi */
    *p++ = 0x48; *p++ = 0xB8;
    *(int64_t *)p = handler_addr; p += 8;
    *p++ = 0xFF; *p++ = 0xD0;

    BYTE restore[] = {
        0x48,0x83,0xC4,0x28,
        0x41,0x5B,0x41,0x5A,
        0x41,0x59,0x41,0x58,
        0x5A,0x59,0x58,
        0x9D,
    };
    copy_bytes(p, restore, sizeof(restore)); p += sizeof(restore);

    *p++ = 0x48; *p++ = 0x89; *p++ = 0xF1;  /* mov rcx, rsi */
    *p++ = 0x48; *p++ = 0x89; *p++ = 0xFA;  /* mov rdx, rdi */
    *p++ = 0xE8;
    int64_t rel32 = orig_call_target - ((int64_t)p + 4);
    *(int32_t *)p = (int32_t)rel32; p += 4;
    *p++ = 0x48; *p++ = 0xB8;
    *(int64_t *)p = return_addr; p += 8;
    *p++ = 0xFF; *p++ = 0xE0;

    return (int)(p - start);
}

void install_revoke_dispatch_hook(void) {
    if (g_revoke_dispatch_hook_state.installed || !wxb_weixin_module()) return;

    BYTE *target = (BYTE *)((int64_t)wxb_weixin_module() + WX_OFF_REVOKE_DISPATCH);
    restore_code_if_changed(target, WX_ORIG_REVOKE_DISPATCH_BYTES,
                            sizeof(WX_ORIG_REVOKE_DISPATCH_BYTES),
                            "revoke dispatch stale hook restored\n", 0);
    if (target[0] != 0xE8) return;

    copy_bytes(g_revoke_dispatch_hook_state.orig_dispatch, target, 5);

    int32_t orig_rel32 = *(int32_t *)(g_revoke_dispatch_hook_state.orig_dispatch + 1);
    int64_t orig_call_target = (int64_t)target + 5 + orig_rel32;
    int64_t return_addr = (int64_t)target + 5;

    g_revoke_dispatch_hook_state.trampoline = alloc_near(target, 256);
    if (!g_revoke_dispatch_hook_state.trampoline) return;

    int tlen = build_dispatch_trampoline(
        (BYTE *)g_revoke_dispatch_hook_state.trampoline,
        orig_call_target,
        return_addr,
        (int64_t)handle_dispatch_revoke);
    FlushInstructionCache(GetCurrentProcess(), g_revoke_dispatch_hook_state.trampoline, tlen);

    int64_t jmp_rel32 = (int64_t)g_revoke_dispatch_hook_state.trampoline - ((int64_t)target + 5);
    if (jmp_rel32 < INT32_MIN || jmp_rel32 > INT32_MAX) {
        VirtualFree(g_revoke_dispatch_hook_state.trampoline, 0, MEM_RELEASE);
        g_revoke_dispatch_hook_state.trampoline = NULL;
        return;
    }

    BYTE patch[5];
    patch[0] = 0xE9;
    *(int32_t *)(patch + 1) = (int32_t)jmp_rel32;

    DWORD old = 0;
    VirtualProtect(target, 5, PAGE_EXECUTE_READWRITE, &old);
    copy_bytes(target, patch, 5);
    VirtualProtect(target, 5, old, &old);
    FlushInstructionCache(GetCurrentProcess(), target, 5);
    InterlockedExchange(&g_revoke_dispatch_hook_state.installed, 1);
    write_status("revoke dispatch hook installed\n");
}

void uninstall_revoke_dispatch_hook(void) {
    if (!g_revoke_dispatch_hook_state.installed || !wxb_weixin_module()) return;
    BYTE *target = (BYTE *)((int64_t)wxb_weixin_module() + WX_OFF_REVOKE_DISPATCH);
    DWORD old = 0;
    VirtualProtect(target, sizeof(g_revoke_dispatch_hook_state.orig_dispatch), PAGE_EXECUTE_READWRITE, &old);
    copy_bytes(target, g_revoke_dispatch_hook_state.orig_dispatch, sizeof(g_revoke_dispatch_hook_state.orig_dispatch));
    VirtualProtect(target, sizeof(g_revoke_dispatch_hook_state.orig_dispatch), old, &old);
    FlushInstructionCache(GetCurrentProcess(), target, sizeof(g_revoke_dispatch_hook_state.orig_dispatch));
    InterlockedExchange(&g_revoke_dispatch_hook_state.installed, 0);
    if (g_revoke_dispatch_hook_state.trampoline) {
        VirtualFree(g_revoke_dispatch_hook_state.trampoline, 0, MEM_RELEASE);
        g_revoke_dispatch_hook_state.trampoline = NULL;
    }
}

static void handle_revoke_real(int64_t a1, int64_t a2, int64_t a3) {
    LONG hit = InterlockedIncrement(&g_revoke_real_hit);
    int64_t payload = resolve_revoke_payload(a3);
    int64_t orig_msgid = 0;
    int32_t revoketime = 0;
    char talker[512] = {0};
    char dbg[1024];
    char ids[32], time_s[32];
    int pos = 0;

    if (!payload) {
        pos = wsprintfA(dbg, "revoke_real: hit=%d payload_resolve_failed a1=%p a2=%p a3=%p\r\n",
                        (int)hit, (void *)a1, (void *)a2, (void *)a3);
        write_file_bytes(REVOKE_LOG, dbg, pos);
        return;
    }

    orig_msgid = r64((void *)(payload + WX_REVOKE_PAYLOAD_MSGID_OFF));
    revoketime = r32((void *)(payload + WX_REVOKE_PAYLOAD_TIME_OFF));
    wx_read_sso_str(payload + WX_REVOKE_PAYLOAD_TALKER_OFF, talker, sizeof(talker));

    if (!talker[0] && orig_msgid) {
        RevokeCachedMsgSnapshot orig;
        if (find_cached_msg_snapshot(orig_msgid, &orig) && orig.from[0]) {
            safe_copy(talker, sizeof(talker), orig.from);
        }
    }
    if (!orig_msgid || !talker[0]) {
        RevokeCachedMsgSnapshot fallback;
        if (find_recent_cached_revocable_msg_snapshot(talker, &fallback)) {
            orig_msgid = fallback.msgid;
            if (!talker[0]) {
                safe_copy(talker, sizeof(talker),
                          fallback.peer[0] ? fallback.peer : fallback.from);
            }
            if (!revoketime) revoketime = fallback.createtime;
            pos = wsprintfA(dbg,
                            "revoke_real_fallback: hit=%d orig=%I64d local=%d type=%d/%d peer=%s sender=%s\r\n",
                            (int)hit,
                            (long long)fallback.msgid,
                            (int)fallback.local_id,
                            (int)fallback.msg_type,
                            (int)fallback.msg_subtype,
                            fallback.peer,
                            fallback.sender);
            write_file_bytes(REVOKE_LOG, dbg, pos);
        }
    }

    i64toa10(orig_msgid, ids);
    i64toa10(revoketime > 0 ? revoketime : 0, time_s);
    pos = wsprintfA(dbg, "revoke_real: hit=%d payload=%p orig_msgid=%s talker=%s revoketime=%s\r\n",
                    (int)hit, (void *)payload, ids, talker[0] ? talker : "(empty)", time_s);
    write_file_bytes(REVOKE_LOG, dbg, pos);

    if (!orig_msgid || !talker[0]) {
        write_status("revoke_real missing msgid/talker");
        return;
    }

    if (is_chatroom_id(talker)) {
        RevokeCachedMsgSnapshot orig;
        const char *sender = "";
        if (find_cached_msg_snapshot(orig_msgid, &orig)) {
            sender = orig.sender;
        }
        schedule_auto_room_members(talker, sender, "revoke_real");
    }
    handle_revoke_notice_by_id(orig_msgid, talker, revoketime, "revoke_real");
    emit_revoke_notice_event(orig_msgid, talker, revoketime, "revoke_real", "revoke_real");
}

static int build_revoke_real_trampoline(BYTE *p, int64_t return_addr, int64_t handler_addr) {
    BYTE *start = p;
    BYTE save[] = {
        0x9C, 0x50,0x51,0x52, 0x41,0x50,0x41,0x51, 0x41,0x52,0x41,0x53,
        0x48,0x83,0xEC,0x28,
    };
    copy_bytes(p, save, sizeof(save)); p += sizeof(save);
    *p++ = 0x48; *p++ = 0xB8;
    *(int64_t *)p = handler_addr; p += 8;
    *p++ = 0xFF; *p++ = 0xD0;
    BYTE restore[] = {
        0x48,0x83,0xC4,0x28, 0x41,0x5B,0x41,0x5A, 0x41,0x59,0x41,0x58,
        0x5A,0x59,0x58, 0x9D,
    };
    copy_bytes(p, restore, sizeof(restore)); p += sizeof(restore);
    copy_bytes(p, g_orig_revoke_real, 5); p += 5;
    *p++ = 0x48; *p++ = 0xB8;
    *(int64_t *)p = return_addr; p += 8;
    *p++ = 0xFF; *p++ = 0xE0;
    return (int)(p - start);
}

void install_revoke_real_hook(void) {
    if (g_revoke_real_hook_installed || !wxb_weixin_module()) return;
    BYTE *target = (BYTE *)((int64_t)wxb_weixin_module() + WX_OFF_REVOKE_REAL);
    if (!bytes_equal(target, WX_ORIG_REVOKE_REAL_BYTES, sizeof(WX_ORIG_REVOKE_REAL_BYTES))) {
        DWORD oldp = 0;
        VirtualProtect(target, sizeof(WX_ORIG_REVOKE_REAL_BYTES), PAGE_EXECUTE_READWRITE, &oldp);
        copy_bytes(target, WX_ORIG_REVOKE_REAL_BYTES, sizeof(WX_ORIG_REVOKE_REAL_BYTES));
        VirtualProtect(target, sizeof(WX_ORIG_REVOKE_REAL_BYTES), oldp, &oldp);
        FlushInstructionCache(GetCurrentProcess(), target, sizeof(WX_ORIG_REVOKE_REAL_BYTES));
        write_status("revoke real stale hook restored\n");
    }
    copy_bytes(g_orig_revoke_real, target, 5);
    g_revoke_real_trampoline = alloc_near(target, 256);
    if (!g_revoke_real_trampoline) return;
    int tlen = build_revoke_real_trampoline(
        (BYTE *)g_revoke_real_trampoline,
        (int64_t)target + 5,
        (int64_t)handle_revoke_real);
    FlushInstructionCache(GetCurrentProcess(), g_revoke_real_trampoline, tlen);
    int64_t rel32 = (int64_t)g_revoke_real_trampoline - ((int64_t)target + 5);
    if (rel32 < INT32_MIN || rel32 > INT32_MAX) {
        VirtualFree(g_revoke_real_trampoline, 0, MEM_RELEASE);
        g_revoke_real_trampoline = NULL;
        return;
    }
    BYTE patch[5];
    patch[0] = 0xE9;
    *(int32_t *)(patch + 1) = (int32_t)rel32;
    DWORD old = 0;
    VirtualProtect(target, 5, PAGE_EXECUTE_READWRITE, &old);
    copy_bytes(target, patch, 5);
    VirtualProtect(target, 5, old, &old);
    FlushInstructionCache(GetCurrentProcess(), target, 5);
    InterlockedExchange(&g_revoke_real_hook_installed, 1);
    write_status("revoke real hook installed\n");
}

void uninstall_revoke_real_hook(void) {
    if (!g_revoke_real_hook_installed || !wxb_weixin_module()) return;
    BYTE *target = (BYTE *)((int64_t)wxb_weixin_module() + WX_OFF_REVOKE_REAL);
    DWORD old = 0;
    VirtualProtect(target, sizeof(g_orig_revoke_real), PAGE_EXECUTE_READWRITE, &old);
    copy_bytes(target, g_orig_revoke_real, sizeof(g_orig_revoke_real));
    VirtualProtect(target, sizeof(g_orig_revoke_real), old, &old);
    FlushInstructionCache(GetCurrentProcess(), target, sizeof(g_orig_revoke_real));
    InterlockedExchange(&g_revoke_real_hook_installed, 0);
    if (g_revoke_real_trampoline) {
        VirtualFree(g_revoke_real_trampoline, 0, MEM_RELEASE);
        g_revoke_real_trampoline = NULL;
    }
}

void restore_revoke_dispatch_site_if_stale(void) {
    if (g_revoke_dispatch_hook_state.installed || !wxb_weixin_module()) return;
    BYTE *target = (BYTE *)((int64_t)wxb_weixin_module() + WX_OFF_REVOKE_DISPATCH);
    restore_code_if_changed(target, WX_ORIG_REVOKE_DISPATCH_BYTES,
                            sizeof(WX_ORIG_REVOKE_DISPATCH_BYTES),
                            "revoke dispatch stale hook restored\n", 0);
}

int revoke_entry_hook_installed(void) {
    return InterlockedCompareExchange(&g_revoke_entry_hook_state.installed, 0, 0) != 0;
}

int revoke_dispatch_hook_installed(void) {
    return InterlockedCompareExchange(&g_revoke_dispatch_hook_state.installed, 0, 0) != 0;
}

int revoke_real_hook_installed(void) {
    return InterlockedCompareExchange(&g_revoke_real_hook_installed, 0, 0) != 0;
}
