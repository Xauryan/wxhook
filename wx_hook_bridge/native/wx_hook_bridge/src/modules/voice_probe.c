/*
 * Probe the official PC voice send path without changing behavior.
 *
 * sub_185EF8B90 is the Weixin 4.1.9.23 wrapper that builds the voice
 * MessageSendSource and enters the generic source-send chain. Logging its
 * arguments tells us what the real voice_data std::string contains.
 */

#include "../include/wx_bridge_voice_probe.h"
#include "../include/wx_bridge_file_io.h"
#include "../include/wx_bridge_memory.h"
#include "../include/wx_bridge_offsets.h"
#include "../include/wx_bridge_paths.h"
#include "../include/wx_bridge_runtime.h"
#include "../include/wx_bridge_wechat_string.h"

#define VOICE_PROBE_PATCH_LEN 13
#define VOICE_PROBE_MAX_PATCH_LEN 32

typedef int64_t (__fastcall *FnVoiceProbeCall2)(int64_t a1, int64_t a2);
typedef int64_t (__fastcall *FnVoiceProbeCall3)(int64_t a1, int64_t a2,
                                                int64_t a3);
typedef int64_t (__fastcall *FnVoiceProbeCall4)(int64_t a1, int64_t a2,
                                                int64_t a3, int64_t a4);
typedef int64_t (__fastcall *FnVoiceProbeCall5)(int64_t a1, int64_t a2,
                                                int64_t a3, int64_t a4,
                                                int64_t a5);
typedef int64_t (__fastcall *FnVoiceProbeCall6)(int64_t a1, int64_t a2,
                                                int64_t a3, int64_t a4,
                                                int64_t a5, int64_t a6);
typedef int64_t (__fastcall *FnVoiceProbeCall1)(int64_t a1);

typedef int64_t (__fastcall *FnVoiceSendWrap)(int64_t a1, int64_t a2,
                                              int64_t voice_data,
                                              int64_t target,
                                              int64_t duration,
                                              int64_t meta);

typedef struct VoiceProbeHookState {
    void *trampoline;
    BYTE orig[VOICE_PROBE_PATCH_LEN];
    volatile LONG installed;
    volatile LONG stale_restored;
} VoiceProbeHookState;

static VoiceProbeHookState g_voice_probe_hook_state = {0};
static volatile LONG g_voice_final_replay_once_fired = 0;
static volatile LONG g_voice_source_builder_shim_active = 0;
static volatile LONG g_source_send_interesting_depth = 0;
static volatile LONG64 g_voice_cached_holder = 0;
static volatile LONG64 g_voice_cached_state = 0;

static void voice_probe_cache_controller_state(const char *reason, int64_t state);

typedef struct VoiceProbeSite {
    const char *name;
    DWORD rva;
    int len;
    const BYTE *expected;
    BYTE orig[VOICE_PROBE_MAX_PATCH_LEN];
    void *trampoline;
    volatile LONG installed;
    volatile LONG stale_restored;
} VoiceProbeSite;

static const BYTE WX_ORIG_VOICE_SEND_WRAP_BYTES[VOICE_PROBE_PATCH_LEN] = {
    0x55, 0x41, 0x56, 0x56, 0x57, 0x53, 0x48, 0x81, 0xEC, 0xB0, 0x00, 0x00, 0x00
};

static const BYTE WX_ORIG_VOICE_STAGE_BUFFER_BYTES[15] = {
    0x55, 0x41, 0x57, 0x41, 0x56, 0x56, 0x57, 0x53, 0x48, 0x81, 0xEC, 0x58, 0x02, 0x00, 0x00
};

static const BYTE WX_ORIG_VOICE_STAGE_SEND_FINAL_BYTES[19] = {
    0x55, 0x41, 0x57, 0x41, 0x56, 0x41, 0x55, 0x41, 0x54, 0x56,
    0x57, 0x53, 0x48, 0x81, 0xEC, 0x78, 0x02, 0x00, 0x00
};

static const BYTE WX_ORIG_VOICE_CACHED_FLUSH_BYTES[29] = {
    0x55, 0x56, 0x57, 0x48, 0x81, 0xEC, 0x80, 0x01, 0x00, 0x00,
    0x48, 0x8D, 0xAC, 0x24, 0x80, 0x00, 0x00, 0x00, 0x48, 0xC7,
    0x85, 0xF8, 0x00, 0x00, 0x00, 0xFE, 0xFF, 0xFF, 0xFF
};

static const BYTE WX_ORIG_VOICE_STAGE_UPPER_A_BYTES[15] = {
    0x55, 0x41, 0x57, 0x41, 0x56, 0x56, 0x57, 0x53, 0x48, 0x81, 0xEC, 0x58, 0x02, 0x00, 0x00
};

static const BYTE WX_ORIG_VOICE_STAGE_UPPER_B_BYTES[17] = {
    0x55, 0x41, 0x57, 0x41, 0x56, 0x41, 0x54, 0x56, 0x57, 0x53,
    0x48, 0x81, 0xEC, 0x80, 0x00, 0x00, 0x00
};

static const BYTE WX_ORIG_VOICE_HOLDER_FLUSH_BYTES[31] = {
    0x55, 0x56, 0x48, 0x81, 0xEC, 0x28, 0x01, 0x00, 0x00, 0x48,
    0x8D, 0xAC, 0x24, 0x80, 0x00, 0x00, 0x00, 0x48, 0xC7, 0x85,
    0xA0, 0x00, 0x00, 0x00, 0xFE, 0xFF, 0xFF, 0xFF, 0x48, 0x89,
    0xCE
};

static const BYTE WX_ORIG_VOICE_HOLDER_INIT_BYTES[18] = {
    0x55, 0x56, 0x57, 0x48, 0x81, 0xEC, 0x30, 0x06, 0x00, 0x00,
    0x48, 0x8D, 0xAC, 0x24, 0x80, 0x00, 0x00, 0x00
};

static const BYTE WX_ORIG_VOICE_CONTROLLER_BIND_BYTES[19] = {
    0x55, 0x56, 0x57, 0x53, 0x48, 0x81, 0xEC, 0x28, 0x05, 0x00,
    0x00, 0x48, 0x8D, 0xAC, 0x24, 0x80, 0x00, 0x00, 0x00
};

static const BYTE WX_ORIG_VOICE_TARGET_SET_BYTES[21] = {
    0x55, 0x41, 0x56, 0x56, 0x57, 0x53, 0x48, 0x81, 0xEC, 0x80,
    0x01, 0x00, 0x00, 0x48, 0x8D, 0xAC, 0x24, 0x80, 0x00, 0x00,
    0x00
};

static const BYTE WX_ORIG_VOICE_STATE_REBUILD_BYTES[18] = {
    0x55, 0x56, 0x57, 0x48, 0x81, 0xEC, 0x20, 0x01, 0x00, 0x00,
    0x48, 0x8D, 0xAC, 0x24, 0x80, 0x00, 0x00, 0x00
};

static const BYTE WX_ORIG_VOICE_HOLDER_DTOR_BYTES[29] = {
    0x55, 0x56, 0x57, 0x48, 0x81, 0xEC, 0xD0, 0x01, 0x00, 0x00,
    0x48, 0x8D, 0xAC, 0x24, 0x80, 0x00, 0x00, 0x00, 0x48, 0xC7,
    0x85, 0x48, 0x01, 0x00, 0x00, 0xFE, 0xFF, 0xFF, 0xFF
};

static const BYTE WX_ORIG_SUBMIT_18160DCD0_BYTES[15] = {
    0x55, 0x41, 0x57, 0x41, 0x56, 0x56, 0x57, 0x53,
    0x48, 0x81, 0xEC, 0x28, 0x01, 0x00, 0x00
};

static const BYTE WX_ORIG_SOURCE_TASK_BUILD_GENERIC_BYTES[15] = {
    0x55, 0x41, 0x56, 0x56, 0x57, 0x53, 0x48, 0x83,
    0xEC, 0x50, 0x48, 0x8D, 0x6C, 0x24, 0x50
};

static const BYTE WX_ORIG_SOURCE_EXTRA_APPLY_BYTES[13] = {
    0x55, 0x56, 0x57, 0x53, 0x48, 0x83, 0xEC, 0x58,
    0x48, 0x8D, 0x6C, 0x24, 0x50
};

static const BYTE WX_ORIG_SOURCE_INJECT_CONVERT_BYTES[19] = {
    0x55, 0x41, 0x57, 0x41, 0x56, 0x41, 0x55, 0x41, 0x54, 0x56,
    0x57, 0x53, 0x48, 0x81, 0xEC, 0x88, 0x02, 0x00, 0x00
};

static const BYTE WX_ORIG_SOURCE_DISPATCH_WRAP_BYTES[21] = {
    0x55, 0x41, 0x56, 0x56, 0x57, 0x53, 0x48, 0x81, 0xEC, 0x20,
    0x01, 0x00, 0x00, 0x48, 0x8D, 0xAC, 0x24, 0x80, 0x00, 0x00,
    0x00
};

static const BYTE WX_ORIG_SOURCE_SEND_BYTES[17] = {
    0x55, 0x41, 0x57, 0x41, 0x56, 0x41, 0x54, 0x56, 0x57, 0x53,
    0x48, 0x81, 0xEC, 0x80, 0x01, 0x00, 0x00
};

static const BYTE WX_ORIG_SOURCE_SEND_PRECHECK_BYTES[18] = {
    0x55, 0x56, 0x57, 0x48, 0x81, 0xEC, 0xC0, 0x00, 0x00, 0x00,
    0x48, 0x8D, 0xAC, 0x24, 0x80, 0x00, 0x00, 0x00
};

static const BYTE WX_ORIG_SOURCE_ROUTE_CONTAINER_BUILD_BYTES[19] = {
    0x55, 0x41, 0x57, 0x41, 0x56, 0x41, 0x54, 0x56, 0x57, 0x53,
    0x48, 0x83, 0xEC, 0x70, 0x48, 0x8D, 0x6C, 0x24, 0x70
};

static const BYTE WX_ORIG_SOURCE_DISPATCH_CORE_BYTES[23] = {
    0x55, 0x41, 0x57, 0x41, 0x56, 0x56, 0x57, 0x53, 0x48, 0x81,
    0xEC, 0x28, 0x01, 0x00, 0x00, 0x48, 0x8D, 0xAC, 0x24, 0x80,
    0x00, 0x00, 0x00
};

static const BYTE WX_ORIG_SOURCE_WXWORK_PACK_A_BYTES[19] = {
    0x55, 0x41, 0x57, 0x41, 0x56, 0x41, 0x55, 0x41, 0x54, 0x56,
    0x57, 0x53, 0x48, 0x81, 0xEC, 0xF8, 0x01, 0x00, 0x00
};

static const BYTE WX_ORIG_SOURCE_WXWORK_PACK_B_BYTES[19] = {
    0x55, 0x56, 0x57, 0x53, 0x48, 0x81, 0xEC, 0xA8, 0x00, 0x00,
    0x00, 0x48, 0x8D, 0xAC, 0x24, 0x80, 0x00, 0x00, 0x00
};

static const BYTE WX_ORIG_SOURCE_BATCH_ADAPTER_BYTES[19] = {
    0x55, 0x41, 0x57, 0x41, 0x56, 0x41, 0x55, 0x41, 0x54, 0x56,
    0x57, 0x53, 0x48, 0x81, 0xEC, 0xA8, 0x02, 0x00, 0x00
};

static const BYTE WX_ORIG_SOURCE_ROUTE_KEY_LOOKUP_BYTES[19] = {
    0x55, 0x41, 0x57, 0x41, 0x56, 0x41, 0x55, 0x41, 0x54, 0x56,
    0x57, 0x53, 0x48, 0x81, 0xEC, 0x28, 0x02, 0x00, 0x00
};

static const BYTE WX_ORIG_WXWORK_FORWARDMSG_URL_BYTES[23] = {
    0x55, 0x41, 0x57, 0x41, 0x56, 0x56, 0x57, 0x53, 0x48, 0x81,
    0xEC, 0xF8, 0x01, 0x00, 0x00, 0x48, 0x8D, 0xAC, 0x24, 0x80,
    0x00, 0x00, 0x00
};

static VoiceProbeSite g_voice_stage_buffer = {
    "stage_1852F1740", WX_OFF_VOICE_STAGE_BUFFER, 15, WX_ORIG_VOICE_STAGE_BUFFER_BYTES, {0}, NULL, 0, 0
};
static VoiceProbeSite g_voice_stage_send_final = {
    "stage_1852F0F20", WX_OFF_VOICE_STAGE_SEND_FINAL, 19, WX_ORIG_VOICE_STAGE_SEND_FINAL_BYTES, {0}, NULL, 0, 0
};
static VoiceProbeSite g_voice_cached_flush = {
    "voice_cached_flush_1852F0CA0", WX_OFF_VOICE_CACHED_FLUSH, 29,
    WX_ORIG_VOICE_CACHED_FLUSH_BYTES, {0}, NULL, 0, 0
};
static VoiceProbeSite g_voice_stage_upper_a = {
    "stage_1852FBB00", WX_OFF_VOICE_STAGE_UPPER_A, 15, WX_ORIG_VOICE_STAGE_UPPER_A_BYTES, {0}, NULL, 0, 0
};
static VoiceProbeSite g_voice_stage_upper_b = {
    "stage_1852FC160", WX_OFF_VOICE_STAGE_UPPER_B, 17, WX_ORIG_VOICE_STAGE_UPPER_B_BYTES, {0}, NULL, 0, 0
};
static VoiceProbeSite g_voice_holder_flush = {
    "voice_holder_flush_1852F8460", WX_OFF_VOICE_HOLDER_FLUSH, 31,
    WX_ORIG_VOICE_HOLDER_FLUSH_BYTES, {0}, NULL, 0, 0
};
static VoiceProbeSite g_voice_holder_init = {
    "voice_holder_init_1852F2F30", WX_OFF_VOICE_HOLDER_INIT, 18,
    WX_ORIG_VOICE_HOLDER_INIT_BYTES, {0}, NULL, 0, 0
};
static VoiceProbeSite g_voice_controller_bind = {
    "voice_controller_bind_1852F0020", WX_OFF_VOICE_CONTROLLER_BIND, 19,
    WX_ORIG_VOICE_CONTROLLER_BIND_BYTES, {0}, NULL, 0, 0
};
static VoiceProbeSite g_voice_target_set = {
    "voice_target_set_1852F65A0", WX_OFF_VOICE_TARGET_SET, 21,
    WX_ORIG_VOICE_TARGET_SET_BYTES, {0}, NULL, 0, 0
};
static VoiceProbeSite g_voice_state_rebuild = {
    "voice_state_rebuild_1852F6880", WX_OFF_VOICE_STATE_REBUILD, 18,
    WX_ORIG_VOICE_STATE_REBUILD_BYTES, {0}, NULL, 0, 0
};
static VoiceProbeSite g_voice_holder_dtor = {
    "voice_holder_dtor_1852F5FF0", WX_OFF_VOICE_HOLDER_DTOR, 29,
    WX_ORIG_VOICE_HOLDER_DTOR_BYTES, {0}, NULL, 0, 0
};
static VoiceProbeSite g_voice_submit_18160DCD0 = {
    "submit_18160DCD0", 0x160DCD0, 15, WX_ORIG_SUBMIT_18160DCD0_BYTES, {0}, NULL, 0, 0
};
static VoiceProbeSite g_source_task_build_generic = {
    "source_task_build_1815E25D0", WX_OFF_SOURCE_TASK_BUILD_GENERIC, 15,
    WX_ORIG_SOURCE_TASK_BUILD_GENERIC_BYTES, {0}, NULL, 0, 0
};
static VoiceProbeSite g_source_extra_apply = {
    "source_extra_apply_1841E18F0", WX_OFF_SOURCE_EXTRA_APPLY, 13,
    WX_ORIG_SOURCE_EXTRA_APPLY_BYTES, {0}, NULL, 0, 0
};
static VoiceProbeSite g_source_inject_convert = {
    "source_inject_convert_1841E1A60", WX_OFF_SOURCE_INJECT_CONVERT, 19,
    WX_ORIG_SOURCE_INJECT_CONVERT_BYTES, {0}, NULL, 0, 0
};
static VoiceProbeSite g_source_dispatch_wrap = {
    "source_dispatch_wrap_18202A260", WX_OFF_SOURCE_DISPATCH_WRAP, 21,
    WX_ORIG_SOURCE_DISPATCH_WRAP_BYTES, {0}, NULL, 0, 0
};
static VoiceProbeSite g_source_send = {
    "source_send_1820291D0", WX_OFF_SOURCE_SEND, 17,
    WX_ORIG_SOURCE_SEND_BYTES, {0}, NULL, 0, 0
};
static VoiceProbeSite g_source_send_precheck = {
    "source_send_precheck_182029EE0", WX_OFF_SOURCE_SEND_PRECHECK, 18,
    WX_ORIG_SOURCE_SEND_PRECHECK_BYTES, {0}, NULL, 0, 0
};
static VoiceProbeSite g_source_route_container_build = {
    "source_route_container_build_1805FE160", WX_OFF_SOURCE_ROUTE_CONTAINER_BUILD, 19,
    WX_ORIG_SOURCE_ROUTE_CONTAINER_BUILD_BYTES, {0}, NULL, 0, 0
};
static VoiceProbeSite g_source_dispatch_core = {
    "source_dispatch_core_180128690", WX_OFF_SOURCE_DISPATCH_CORE, 23,
    WX_ORIG_SOURCE_DISPATCH_CORE_BYTES, {0}, NULL, 0, 0
};
static VoiceProbeSite g_source_wxwork_pack_a = {
    "source_wxwork_pack_a_1806034C0", WX_OFF_SOURCE_WXWORK_PACK_A, 19,
    WX_ORIG_SOURCE_WXWORK_PACK_A_BYTES, {0}, NULL, 0, 0
};
static VoiceProbeSite g_source_wxwork_pack_b = {
    "source_wxwork_pack_b_180603D50", WX_OFF_SOURCE_WXWORK_PACK_B, 19,
    WX_ORIG_SOURCE_WXWORK_PACK_B_BYTES, {0}, NULL, 0, 0
};
static VoiceProbeSite g_source_batch_adapter = {
    "source_batch_adapter_1814CF830", WX_OFF_SOURCE_BATCH_ADAPTER, 19,
    WX_ORIG_SOURCE_BATCH_ADAPTER_BYTES, {0}, NULL, 0, 0
};
static VoiceProbeSite g_source_route_key_lookup = {
    "source_route_key_lookup_1814CE590", WX_OFF_SOURCE_ROUTE_KEY_LOOKUP, 19,
    WX_ORIG_SOURCE_ROUTE_KEY_LOOKUP_BYTES, {0}, NULL, 0, 0
};
static VoiceProbeSite g_wxwork_forwardmsg_url = {
    "wxwork_forwardmsg_url_1832F2E40", WX_OFF_WXWORK_FORWARDMSG_URL, 23,
    WX_ORIG_WXWORK_FORWARDMSG_URL_BYTES, {0}, NULL, 0, 0
};

static void append_voice_probe_literal(const char *s) {
    if (!s) return;
    append_file_bytes(VOICE_PROBE_LOG, s, (DWORD)lstrlenA(s));
}

void voice_source_builder_shim_enter(void) {
    InterlockedIncrement(&g_voice_source_builder_shim_active);
}

void voice_source_builder_shim_leave(void) {
    LONG v = InterlockedDecrement(&g_voice_source_builder_shim_active);
    if (v < 0) InterlockedExchange(&g_voice_source_builder_shim_active, 0);
}

int voice_source_builder_shim_active(void) {
    return InterlockedCompareExchange(&g_voice_source_builder_shim_active, 0, 0) > 0;
}

static void voice_probe_cache_holder_state(const char *reason, int64_t holder) {
    int64_t base = (int64_t)wxb_weixin_module();
    int64_t holder_vt = 0;
    int64_t state = 0;
    int64_t state_vt = 0;
    char target[256] = {0};
    char line[1024];
    if (!base || !holder) return;
    __try {
        holder_vt = r64((void *)holder);
        state = r64((void *)(holder + 0x168));
        if (state) {
            state_vt = r64((void *)state);
            wx_read_sso_str(state + 0x1B8, target, sizeof(target));
        }
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
        return;
    }
    if (!state || state_vt != base + WX_OFF_VOICE_STATE_VT) {
        wsprintfA(line,
                  "voice_probe_state_cache_skip: reason=%s holder=%I64x holder_vt=%I64x state=%I64x state_vt=%I64x\r\n",
                  reason ? reason : "",
                  (unsigned __int64)holder,
                  (unsigned __int64)holder_vt,
                  (unsigned __int64)state,
                  (unsigned __int64)state_vt);
        append_file_bytes(VOICE_PROBE_LOG, line, (DWORD)lstrlenA(line));
        return;
    }
    InterlockedExchange64(&g_voice_cached_holder, holder);
    InterlockedExchange64(&g_voice_cached_state, state);
    wsprintfA(line,
              "voice_probe_state_cache: reason=%s holder=%I64x holder_vt=%I64x state=%I64x target='%.180s'\r\n",
              reason ? reason : "",
              (unsigned __int64)holder,
              (unsigned __int64)holder_vt,
              (unsigned __int64)state,
              target);
    append_file_bytes(VOICE_PROBE_LOG, line, (DWORD)lstrlenA(line));
}

static int voice_probe_state_has_controller(int64_t base, int64_t state,
                                            int64_t *controller_out,
                                            int64_t *controller_vt_out) {
    int64_t controller = 0;
    int64_t controller_vt = 0;
    if (controller_out) *controller_out = 0;
    if (controller_vt_out) *controller_vt_out = 0;
    if (!base || !state) return 0;
    controller = r64((void *)(state + 0x198));
    if (!controller) return 0;
    controller_vt = r64((void *)controller);
    if (controller_out) *controller_out = controller;
    if (controller_vt_out) *controller_vt_out = controller_vt;
    return controller_vt == base + WX_OFF_VOICE_CONTROLLER_VT;
}

int voice_probe_prepare_cached_state_for_target(const char *target) {
    int64_t base = (int64_t)wxb_weixin_module();
    int64_t holder = InterlockedCompareExchange64(&g_voice_cached_holder, 0, 0);
    int64_t holder_vt = 0;
    int64_t state = 0;
    int64_t state_vt = 0;
    int64_t controller = 0;
    int64_t controller_vt = 0;
    char current_target[256] = {0};
    char line[1024];
    if (!base || !target || !target[0] || !holder) return 0;

    __try {
        holder_vt = r64((void *)holder);
        if (holder_vt != base + WX_OFF_VOICE_HOLDER_VT) {
            wsprintfA(line,
                      "voice_probe_prepare_target_skip: bad_holder target='%.180s' holder=%I64x holder_vt=%I64x\r\n",
                      target,
                      (unsigned __int64)holder,
                      (unsigned __int64)holder_vt);
            append_file_bytes(VOICE_PROBE_LOG, line, (DWORD)lstrlenA(line));
            return 0;
        }

        state = r64((void *)(holder + 0x168));
        if (state) {
            state_vt = r64((void *)state);
            if (state_vt == base + WX_OFF_VOICE_STATE_VT) {
                wx_read_sso_str(state + 0x1B8, current_target, sizeof(current_target));
            }
        }

        if (!state || state_vt != base + WX_OFF_VOICE_STATE_VT ||
            lstrcmpA(current_target, target) != 0) {
            BYTE target_arg[32];
            BYTE target_pool[1024];
            BYTE *pool = target_pool;
            ZeroMemory(target_arg, sizeof(target_arg));
            ZeroMemory(target_pool, sizeof(target_pool));
            wx_set_std_string_arena((int64_t)target_arg, target, lstrlenA(target),
                                    &pool, target_pool + sizeof(target_pool));
            ((FnVoiceProbeCall2)(base + WX_OFF_VOICE_TARGET_SET))(holder, (int64_t)target_arg);
            state = r64((void *)(holder + 0x168));
            state_vt = state ? r64((void *)state) : 0;
            current_target[0] = 0;
            if (state_vt == base + WX_OFF_VOICE_STATE_VT) {
                wx_read_sso_str(state + 0x1B8, current_target, sizeof(current_target));
            }
        }

        if (!state || state_vt != base + WX_OFF_VOICE_STATE_VT ||
            lstrcmpA(current_target, target) != 0) {
            wsprintfA(line,
                      "voice_probe_prepare_target_skip: no_target_state target='%.180s' holder=%I64x state=%I64x state_vt=%I64x current='%.180s'\r\n",
                      target,
                      (unsigned __int64)holder,
                      (unsigned __int64)state,
                      (unsigned __int64)state_vt,
                      current_target);
            append_file_bytes(VOICE_PROBE_LOG, line, (DWORD)lstrlenA(line));
            return 0;
        }

        if (!voice_probe_state_has_controller(base, state, &controller, &controller_vt)) {
            /*
             * Do not call the full 1852EEA40 prepare wrapper here.  On an
             * artificially retargeted group state it can enter 1852EEBF0,
             * create the controller, then fault while registering the wider
             * async observer chain.  The final send path only needs the
             * controller at state+0x198, so bind it directly with 1852F0020.
             */
            wsprintfA(line,
                      "voice_probe_prepare_target_bind_controller: target='%.180s' holder=%I64x state=%I64x\r\n",
                      target,
                      (unsigned __int64)holder,
                      (unsigned __int64)state);
            append_file_bytes(VOICE_PROBE_LOG, line, (DWORD)lstrlenA(line));
            ((FnVoiceProbeCall1)(base + WX_OFF_VOICE_CONTROLLER_BIND))(state);
        }

        if (!voice_probe_state_has_controller(base, state, &controller, &controller_vt)) {
            wsprintfA(line,
                      "voice_probe_prepare_target_skip: no_controller target='%.180s' holder=%I64x state=%I64x controller=%I64x controller_vt=%I64x\r\n",
                      target,
                      (unsigned __int64)holder,
                      (unsigned __int64)state,
                      (unsigned __int64)controller,
                      (unsigned __int64)controller_vt);
            append_file_bytes(VOICE_PROBE_LOG, line, (DWORD)lstrlenA(line));
            return 0;
        }
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
        wsprintfA(line,
                  "voice_probe_prepare_target_skip: exception code=%08lx target='%.180s' holder=%I64x state=%I64x current='%.180s'\r\n",
                  (unsigned long)GetExceptionCode(),
                  target,
                  (unsigned __int64)holder,
                  (unsigned __int64)state,
                  current_target);
        append_file_bytes(VOICE_PROBE_LOG, line, (DWORD)lstrlenA(line));
        return 0;
    }

    voice_probe_cache_controller_state("prepare_target", state);
    return 1;
}

static void voice_probe_cache_controller_state(const char *reason, int64_t state) {
    int64_t base = (int64_t)wxb_weixin_module();
    int64_t state_vt = 0;
    int64_t controller = 0;
    int64_t controller_vt = 0;
    int64_t holder = InterlockedCompareExchange64(&g_voice_cached_holder, 0, 0);
    int64_t holder_state = 0;
    char target[256] = {0};
    char line[1024];
    if (!base || !state) return;
    __try {
        state_vt = r64((void *)state);
        if (holder) holder_state = r64((void *)(holder + 0x168));
        wx_read_sso_str(state + 0x1B8, target, sizeof(target));
        if (state_vt != base + WX_OFF_VOICE_STATE_VT ||
            !voice_probe_state_has_controller(base, state, &controller, &controller_vt)) {
            wsprintfA(line,
                      "voice_probe_controller_cache_skip: reason=%s state=%I64x state_vt=%I64x controller=%I64x controller_vt=%I64x target='%.180s'\r\n",
                      reason ? reason : "",
                      (unsigned __int64)state,
                      (unsigned __int64)state_vt,
                      (unsigned __int64)controller,
                      (unsigned __int64)controller_vt,
                      target);
            append_file_bytes(VOICE_PROBE_LOG, line, (DWORD)lstrlenA(line));
            return;
        }
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
        return;
    }
    if (holder_state != state) holder = 0;
    InterlockedExchange64(&g_voice_cached_holder, holder);
    InterlockedExchange64(&g_voice_cached_state, state);
    wsprintfA(line,
              "voice_probe_controller_cache: reason=%s holder=%I64x state=%I64x controller=%I64x controller_vt=%I64x target='%.180s'\r\n",
              reason ? reason : "",
              (unsigned __int64)holder,
              (unsigned __int64)state,
              (unsigned __int64)controller,
              (unsigned __int64)controller_vt,
              target);
    append_file_bytes(VOICE_PROBE_LOG, line, (DWORD)lstrlenA(line));
}

static void voice_probe_invalidate_holder_state(const char *reason, int64_t holder) {
    int64_t cached_holder = InterlockedCompareExchange64(&g_voice_cached_holder, 0, 0);
    int64_t cached_state = InterlockedCompareExchange64(&g_voice_cached_state, 0, 0);
    char line[512];
    if (!holder || holder != cached_holder) return;
    InterlockedExchange64(&g_voice_cached_holder, 0);
    InterlockedExchange64(&g_voice_cached_state, 0);
    wsprintfA(line,
              "voice_probe_state_cache_invalidate: reason=%s holder=%I64x state=%I64x\r\n",
              reason ? reason : "",
              (unsigned __int64)cached_holder,
              (unsigned __int64)cached_state);
    append_file_bytes(VOICE_PROBE_LOG, line, (DWORD)lstrlenA(line));
}

int voice_probe_copy_cached_state(int64_t *holder_out, int64_t *state_out,
                                  char *target_out, int target_out_sz) {
    int64_t base = (int64_t)wxb_weixin_module();
    int64_t holder = InterlockedCompareExchange64(&g_voice_cached_holder, 0, 0);
    int64_t state = InterlockedCompareExchange64(&g_voice_cached_state, 0, 0);
    int64_t state_vt = 0;
    if (holder_out) *holder_out = 0;
    if (state_out) *state_out = 0;
    if (target_out && target_out_sz > 0) target_out[0] = 0;
    if (!base || !state) return 0;
    __try {
        state_vt = r64((void *)state);
        if (state_vt != base + WX_OFF_VOICE_STATE_VT) return 0;
        if (!voice_probe_state_has_controller(base, state, NULL, NULL)) return 0;
        if (target_out && target_out_sz > 0) {
            wx_read_sso_str(state + 0x1B8, target_out, target_out_sz);
        }
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
        return 0;
    }
    if (holder_out) *holder_out = holder;
    if (state_out) *state_out = state;
    return 1;
}

static void hex_sample_from_ptr(int64_t ptr, int64_t len, char *out, int out_sz) {
    static const char hx[] = "0123456789abcdef";
    int want;
    int pos = 0;
    if (!out || out_sz <= 0) return;
    out[0] = 0;
    if (!ptr || len <= 0) return;
    want = (len < 64) ? (int)len : 64;
    __try {
        const BYTE *p = (const BYTE *)ptr;
        for (int i = 0; i < want && pos + 3 < out_sz; i++) {
            BYTE b = p[i];
            out[pos++] = hx[(b >> 4) & 0xF];
            out[pos++] = hx[b & 0xF];
            if (i + 1 < want && pos + 1 < out_sz) out[pos++] = ' ';
        }
        out[pos] = 0;
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
        out[0] = 0;
    }
}

static int64_t std_string_data_ptr(int64_t s, int64_t *len_out, int64_t *cap_out) {
    int64_t len;
    int64_t cap;
    int64_t ptr;
    if (len_out) *len_out = 0;
    if (cap_out) *cap_out = 0;
    if (!s) return 0;
    len = r64((void *)(s + 0x10));
    cap = r64((void *)(s + 0x18));
    if (len_out) *len_out = len;
    if (cap_out) *cap_out = cap;
    if (len <= 0 || len > 0x100000 || cap <= 0) return 0;
    ptr = (cap >= 0x10) ? r64((void *)s) : s;
    return ptr;
}

static void log_voice_probe_args(const char *phase, int64_t ret,
                                 int64_t a1, int64_t a2,
                                 int64_t voice_data, int64_t target,
                                 int64_t duration, int64_t meta) {
    char target_preview[512] = {0};
    char voice_preview[256] = {0};
    char meta_preview[256] = {0};
    char voice_hex[256] = {0};
    char line[2048];
    int64_t voice_len = 0;
    int64_t voice_cap = 0;
    int64_t voice_ptr = 0;
    int64_t target_len = 0;
    int64_t target_cap = 0;
    int64_t meta_len = 0;
    int64_t meta_cap = 0;
    int64_t meta0 = 0;
    int64_t meta8 = 0;
    int meta10 = 0;

    voice_ptr = std_string_data_ptr(voice_data, &voice_len, &voice_cap);
    (void)std_string_data_ptr(target, &target_len, &target_cap);
    if (meta) {
        meta0 = r64((void *)meta);
        meta8 = r64((void *)(meta + 8));
        meta10 = (int)r32((void *)(meta + 0x10));
        (void)std_string_data_ptr(meta + 0x18, &meta_len, &meta_cap);
    }

    wx_read_sso_str(target, target_preview, sizeof(target_preview));
    wx_read_sso_str(voice_data, voice_preview, sizeof(voice_preview));
    if (meta) wx_read_sso_str(meta + 0x18, meta_preview, sizeof(meta_preview));
    hex_sample_from_ptr(voice_ptr, voice_len, voice_hex, sizeof(voice_hex));
    if (phase && lstrcmpA(phase, "entry") == 0 && voice_ptr && voice_len > 0 && voice_len < 1024 * 1024) {
        __try {
            write_file_bytes(VOICE_PROBE_LAST_SILK_FILE, (const char *)voice_ptr, (DWORD)voice_len);
        }
        __except (EXCEPTION_EXECUTE_HANDLER) {
        }
    }

    wsprintfA(line,
              "voice_probe_%s: tid=%lu ret=%I64x a1=%I64x a2=%I64x voice_hdr=%I64x target_hdr=%I64x duration=%I64d meta=%I64x "
              "voice_len=%I64d voice_cap=%I64d voice_ptr=%I64x target_len=%I64d target_cap=%I64d "
              "meta0=%I64x meta8=%I64d meta10=%d meta_len=%I64d meta_cap=%I64d "
              "target='%.180s' voice_text='%.180s' meta_text='%.120s' voice_hex64=%s\r\n",
              phase ? phase : "event",
              GetCurrentThreadId(),
              (unsigned __int64)ret,
              (unsigned __int64)a1,
              (unsigned __int64)a2,
              (unsigned __int64)voice_data,
              (unsigned __int64)target,
              (int64_t)duration,
              (unsigned __int64)meta,
              (int64_t)voice_len,
              (int64_t)voice_cap,
              (unsigned __int64)voice_ptr,
              (int64_t)target_len,
              (int64_t)target_cap,
              (unsigned __int64)meta0,
              (int64_t)meta8,
              meta10,
              (int64_t)meta_len,
              (int64_t)meta_cap,
              target_preview,
              voice_preview,
              meta_preview,
              voice_hex);
    append_file_bytes(VOICE_PROBE_LOG, line, (DWORD)lstrlenA(line));
}

static void log_voice_stage_args(const char *stage, int64_t ret,
                                 int64_t a1, int64_t a2, int64_t a3, int64_t a4) {
    char target_440[256] = {0};
    char pending_368[256] = {0};
    char voice_preview[256] = {0};
    char voice_hex[256] = {0};
    char line[2048];
    int32_t n0 = 0;
    int32_t n1 = 0;
    int64_t product = 0;
    int64_t voice_len = 0;
    int64_t voice_cap = 0;
    int64_t voice_ptr = 0;
    int64_t q0 = 0;
    int flag320 = 0;
    int flag321 = 0;
    int flag322 = 0;
    int64_t mode288 = 0;

    if (a2) {
        n0 = r32((void *)a2);
        n1 = r32((void *)(a2 + 4));
        q0 = r64((void *)a2);
        product = (int64_t)(uint32_t)n0 * (int64_t)(uint32_t)n1;
        voice_ptr = std_string_data_ptr(a2 + 8, &voice_len, &voice_cap);
        wx_read_sso_str(a2 + 8, voice_preview, sizeof(voice_preview));
        hex_sample_from_ptr(voice_ptr, voice_len, voice_hex, sizeof(voice_hex));
    }
    if (a1) {
        flag320 = (int)(BYTE)r32((void *)(a1 + 320));
        flag321 = (int)(BYTE)r32((void *)(a1 + 321));
        flag322 = (int)(BYTE)r32((void *)(a1 + 322));
        mode288 = r64((void *)(a1 + 288));
        wx_read_sso_str(a1 + 440, target_440, sizeof(target_440));
        wx_read_sso_str(a1 + 368, pending_368, sizeof(pending_368));
    }

    wsprintfA(line,
              "voice_probe_%s: tid=%lu ret=%I64x a1=%I64x a2=%I64x a3=%I64x a4=%I64x "
              "n0=%d n1=%d product=%I64d q0=%I64x voice_len=%I64d voice_cap=%I64d voice_ptr=%I64x "
              "mode288=%I64x flags320=%d/%d/%d target440='%.120s' pending368='%.120s' "
              "voice_text='%.180s' voice_hex64=%s\r\n",
              stage ? stage : "stage",
              GetCurrentThreadId(),
              (unsigned __int64)ret,
              (unsigned __int64)a1,
              (unsigned __int64)a2,
              (unsigned __int64)a3,
              (unsigned __int64)a4,
              n0,
              n1,
              (int64_t)product,
              (unsigned __int64)q0,
              (int64_t)voice_len,
              (int64_t)voice_cap,
              (unsigned __int64)voice_ptr,
              (unsigned __int64)mode288,
              flag320,
              flag321,
              flag322,
              target_440,
              pending_368,
              voice_preview,
              voice_hex);
    append_file_bytes(VOICE_PROBE_LOG, line, (DWORD)lstrlenA(line));
}

static void log_voice_flush_args(const char *stage, int64_t ret,
                                 int64_t holder, int64_t state,
                                 int64_t a2, int64_t a3, int64_t a4) {
    char target[256] = {0};
    char cached_preview[256] = {0};
    char cached_hex[256] = {0};
    char line[2048];
    int64_t base = (int64_t)wxb_weixin_module();
    int64_t holder_vt = 0;
    int64_t state_vt = 0;
    int64_t cached_ptr = 0;
    int64_t cached_len = 0;
    int64_t cached_cap = 0;
    int32_t n0 = 0;
    int32_t n1 = 0;
    int64_t product = 0;
    int flag140 = 0;
    int flag141 = 0;
    int flag142 = 0;
    int64_t mode120 = 0;
    int64_t active130 = 0;

    __try {
        if (holder) holder_vt = r64((void *)holder);
        if (!state && holder) state = r64((void *)(holder + 0x168));
        if (state) {
            state_vt = r64((void *)state);
            n0 = r32((void *)(state + 0x170));
            n1 = r32((void *)(state + 0x174));
            product = (int64_t)(uint32_t)n0 * (int64_t)(uint32_t)n1;
            cached_ptr = std_string_data_ptr(state + 0x178, &cached_len, &cached_cap);
            wx_read_sso_str(state + 0x178, cached_preview, sizeof(cached_preview));
            hex_sample_from_ptr(cached_ptr, cached_len, cached_hex, sizeof(cached_hex));
            wx_read_sso_str(state + 0x1B8, target, sizeof(target));
            mode120 = r64((void *)(state + 0x120));
            active130 = r64((void *)(state + 0x130));
            flag140 = (int)(BYTE)r32((void *)(state + 0x140));
            flag141 = (int)(BYTE)r32((void *)(state + 0x141));
            flag142 = (int)(BYTE)r32((void *)(state + 0x142));
        }
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
    }

    wsprintfA(line,
              "voice_probe_%s: tid=%lu ret=%I64x holder=%I64x holder_vt=%I64x state=%I64x state_vt=%I64x "
              "state_vt_ok=%d a2=%I64x a3=%I64x a4=%I64x n0=%d n1=%d product=%I64d "
              "cached_len=%I64d cached_cap=%I64d cached_ptr=%I64x mode120=%I64x active130=%I64x "
              "flags140/141/142=%d/%d/%d target='%.120s' cached_text='%.180s' cached_hex64=%s\r\n",
              stage ? stage : "flush",
              GetCurrentThreadId(),
              (unsigned __int64)ret,
              (unsigned __int64)holder,
              (unsigned __int64)holder_vt,
              (unsigned __int64)state,
              (unsigned __int64)state_vt,
              (base && state_vt == base + WX_OFF_VOICE_STATE_VT) ? 1 : 0,
              (unsigned __int64)a2,
              (unsigned __int64)a3,
              (unsigned __int64)a4,
              n0,
              n1,
              (int64_t)product,
              (int64_t)cached_len,
              (int64_t)cached_cap,
              (unsigned __int64)cached_ptr,
              (unsigned __int64)mode120,
              (unsigned __int64)active130,
              flag140,
              flag141,
              flag142,
              target,
              cached_preview,
              cached_hex);
    append_file_bytes(VOICE_PROBE_LOG, line, (DWORD)lstrlenA(line));
}

static void log_voice_submit_args(const char *phase, int64_t ret,
                                  int64_t a1, int64_t a2, int64_t a3,
                                  int64_t a4, int64_t a5, int64_t a6) {
    char line[2048];
    char target_a3[256] = {0};
    char target_a4[256] = {0};
    int64_t task = 0;
    int64_t task_vt = 0;
    int64_t out0 = 0;
    int64_t out8 = 0;
    int64_t out10 = 0;
    int64_t out18 = 0;
    int64_t cb3 = 0;
    int64_t cb4 = 0;
    int64_t cb5 = 0;
    int64_t cb6 = 0;
    int64_t a1_10 = 0;
    int64_t a1_18 = 0;
    int64_t a1_38 = 0;
    int64_t a1_40 = 0;
    int64_t a1_48 = 0;

    __try {
        if (a1) {
            a1_10 = r64((void *)(a1 + 0x10));
            a1_18 = r64((void *)(a1 + 0x18));
            a1_38 = r64((void *)(a1 + 0x38));
            a1_40 = r64((void *)(a1 + 0x40));
            a1_48 = r64((void *)(a1 + 0x48));
            task = a1_38;
            if (task) task_vt = r64((void *)task);
        }
        if (a2) {
            out0 = r64((void *)a2);
            out8 = r64((void *)(a2 + 8));
            out10 = r64((void *)(a2 + 0x10));
            out18 = r64((void *)(a2 + 0x18));
        }
        if (a3) {
            cb3 = r64((void *)a3);
            wx_read_sso_str(a3 + 0x10, target_a3, sizeof(target_a3));
        }
        if (a4) {
            cb4 = r64((void *)a4);
            wx_read_sso_str(a4 + 0x10, target_a4, sizeof(target_a4));
        }
        if (a5) cb5 = r64((void *)a5);
        if (a6) cb6 = r64((void *)a6);
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
    }

    wsprintfA(line,
              "voice_probe_%s: tid=%lu ret=%I64x a1=%I64x a2=%I64x a3=%I64x a4=%I64x a5=%I64x a6=%I64x "
              "a1_slots={10:%I64x,18:%I64x,38:%I64x,40:%I64x,48:%I64x} task_vt=%I64x "
              "closures={%I64x,%I64x,%I64x,%I64x} out={%I64x,%I64x,%I64x,%I64x} "
              "target_a3='%.120s' target_a4='%.120s'\r\n",
              phase ? phase : "submit_18160DCD0",
              GetCurrentThreadId(),
              (unsigned __int64)ret,
              (unsigned __int64)a1,
              (unsigned __int64)a2,
              (unsigned __int64)a3,
              (unsigned __int64)a4,
              (unsigned __int64)a5,
              (unsigned __int64)a6,
              (unsigned __int64)a1_10,
              (unsigned __int64)a1_18,
              (unsigned __int64)a1_38,
              (unsigned __int64)a1_40,
              (unsigned __int64)a1_48,
              (unsigned __int64)task_vt,
              (unsigned __int64)cb3,
              (unsigned __int64)cb4,
              (unsigned __int64)cb5,
              (unsigned __int64)cb6,
              (unsigned __int64)out0,
              (unsigned __int64)out8,
              (unsigned __int64)out10,
              (unsigned __int64)out18,
              target_a3,
              target_a4);
    append_file_bytes(VOICE_PROBE_LOG, line, (DWORD)lstrlenA(line));
}

static int source_vec_first_voice(int64_t source_vec,
                                  int64_t *source_out, int64_t *ctl_out,
                                  int64_t *source_vt_out, int64_t *ctl_vt_out,
                                  int64_t *type_out) {
    int64_t base = (int64_t)wxb_weixin_module();
    int64_t begin = 0;
    int64_t end = 0;
    int64_t source = 0;
    int64_t ctl = 0;
    int64_t source_vt = 0;
    int64_t ctl_vt = 0;
    int64_t type = 0;
    if (source_out) *source_out = 0;
    if (ctl_out) *ctl_out = 0;
    if (source_vt_out) *source_vt_out = 0;
    if (ctl_vt_out) *ctl_vt_out = 0;
    if (type_out) *type_out = 0;
    if (!base || !source_vec) return 0;
    __try {
        begin = r64((void *)source_vec);
        end = r64((void *)(source_vec + 8));
        if (!begin || begin == end) return 0;
        source = r64((void *)begin);
        ctl = r64((void *)(begin + 8));
        if (!source || !ctl) return 0;
        source_vt = r64((void *)source);
        ctl_vt = r64((void *)ctl);
        type = (int64_t)(int32_t)r32((void *)(source + 0xD8));
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
        return 0;
    }
    if (source_out) *source_out = source;
    if (ctl_out) *ctl_out = ctl;
    if (source_vt_out) *source_vt_out = source_vt;
    if (ctl_vt_out) *ctl_vt_out = ctl_vt;
    if (type_out) *type_out = type;
    return type == 34 &&
           source_vt == base + WX_OFF_SOURCE_VOICE_OBJ_VT &&
           ctl_vt == base + WX_OFF_SOURCE_VOICE_CTL_VT;
}

static void log_source_task_builder(const char *phase, int64_t ret,
                                    int64_t a1, int64_t a2, int64_t a3,
                                    int shimmed,
                                    int64_t source, int64_t ctl,
                                    int64_t source_vt, int64_t ctl_vt,
                                    int64_t type) {
    char line[1536];
    int64_t task = 0;
    int64_t task_vt = 0;
    int64_t a2_38 = 0;
    int64_t a2_40 = 0;
    int64_t a2_48 = 0;
    __try {
        if (a2) {
            a2_38 = r64((void *)(a2 + 0x38));
            a2_40 = r64((void *)(a2 + 0x40));
            a2_48 = r64((void *)(a2 + 0x48));
            task = a2_38;
            if (task) task_vt = r64((void *)task);
        }
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
    }
    wsprintfA(line,
              "voice_probe_source_task_builder_%s: tid=%lu ret=%I64x shimmed=%d "
              "a1=%I64x a2=%I64x a3=%I64x source={%I64x,%I64x} "
              "source_vt=%I64x ctl_vt=%I64x type=%I64d a2_slots={38:%I64x,40:%I64x,48:%I64x} task_vt=%I64x\r\n",
              phase ? phase : "event",
              GetCurrentThreadId(),
              (unsigned __int64)ret,
              shimmed,
              (unsigned __int64)a1,
              (unsigned __int64)a2,
              (unsigned __int64)a3,
              (unsigned __int64)source,
              (unsigned __int64)ctl,
              (unsigned __int64)source_vt,
              (unsigned __int64)ctl_vt,
              (int64_t)type,
              (unsigned __int64)a2_38,
              (unsigned __int64)a2_40,
              (unsigned __int64)a2_48,
              (unsigned __int64)task_vt);
    append_file_bytes(VOICE_PROBE_LOG, line, (DWORD)lstrlenA(line));
}

static int source_pair_type(int64_t source, int64_t *source_vt_out, int64_t *type_out) {
    int64_t source_vt = 0;
    int64_t type = 0;
    if (source_vt_out) *source_vt_out = 0;
    if (type_out) *type_out = 0;
    if (!source) return 0;
    __try {
        source_vt = r64((void *)source);
        type = (int64_t)(int32_t)r32((void *)(source + 0xD8));
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
        return 0;
    }
    if (source_vt_out) *source_vt_out = source_vt;
    if (type_out) *type_out = type;
    return type == 34;
}

static int source_vec_has_type34(int64_t source_vec) {
    int64_t begin = 0;
    int64_t end = 0;
    int n = 0;
    if (!source_vec) return 0;
    __try {
        begin = r64((void *)source_vec);
        end = r64((void *)(source_vec + 8));
        if (!begin || end < begin || end - begin > 0x10000) return 0;
        for (int64_t p = begin; p != end && n < 8; p += 16, n++) {
            int64_t source = r64((void *)p);
            if (source_pair_type(source, NULL, NULL)) return 1;
        }
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
        return 0;
    }
    return 0;
}

static void log_source_vec_dump(const char *phase, const char *label, int64_t source_vec) {
    char line[2304];
    char s38[160];
    char s78[160];
    char sb0[160];
    char s660[160];
    char s6a0[160];
    int64_t begin = 0;
    int64_t end = 0;
    int64_t cap = 0;
    int64_t count = 0;
    int emitted = 0;
    __try {
        if (source_vec) {
            begin = r64((void *)source_vec);
            end = r64((void *)(source_vec + 8));
            cap = r64((void *)(source_vec + 0x10));
            if (begin && end >= begin && end - begin <= 0x10000) count = (end - begin) / 16;
        }
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
        begin = 0;
        end = 0;
        cap = 0;
        count = 0;
    }
    wsprintfA(line,
              "voice_probe_source_vec_%s_%s: tid=%lu vec=%I64x begin=%I64x end=%I64x cap=%I64x count=%I64d\r\n",
              phase ? phase : "event",
              label ? label : "vec",
              GetCurrentThreadId(),
              (unsigned __int64)source_vec,
              (unsigned __int64)begin,
              (unsigned __int64)end,
              (unsigned __int64)cap,
              (int64_t)count);
    append_file_bytes(VOICE_PROBE_LOG, line, (DWORD)lstrlenA(line));
    if (!begin || count <= 0) return;
    for (int64_t p = begin; p != end && emitted < 8; p += 16, emitted++) {
        int64_t source = 0;
        int64_t ctl = 0;
        int64_t source_vt = 0;
        int64_t ctl_vt = 0;
        int64_t type = 0;
        int64_t d8_q = 0;
        int64_t slot98 = 0;
        int64_t slota0 = 0;
        s38[0] = 0;
        s78[0] = 0;
        sb0[0] = 0;
        s660[0] = 0;
        s6a0[0] = 0;
        __try {
            source = r64((void *)p);
            ctl = r64((void *)(p + 8));
            if (source) {
                source_vt = r64((void *)source);
                type = (int64_t)(int32_t)r32((void *)(source + 0xD8));
                d8_q = r64((void *)(source + 0xD8));
                slot98 = r64((void *)(source + 0x98));
                slota0 = r64((void *)(source + 0xA0));
                wx_read_sso_str(source + 0x38, s38, sizeof(s38));
                wx_read_sso_str(source + 0x78, s78, sizeof(s78));
                wx_read_sso_str(source + 0xB0, sb0, sizeof(sb0));
                wx_read_sso_str(source + 0x660, s660, sizeof(s660));
                wx_read_sso_str(source + 0x6A0, s6a0, sizeof(s6a0));
            }
            if (ctl) ctl_vt = r64((void *)ctl);
        }
        __except (EXCEPTION_EXECUTE_HANDLER) {
        }
        wsprintfA(line,
                  "voice_probe_source_vec_item_%s_%s: idx=%d pair=%I64x source=%I64x ctl=%I64x "
                  "source_vt=%I64x ctl_vt=%I64x type=%I64d d8q=%I64x slots={98:%I64x,a0:%I64x} "
                  "s38='%.80s' s78='%.80s' sb0='%.80s' s660='%.100s' s6a0='%.80s'\r\n",
                  phase ? phase : "event",
                  label ? label : "vec",
                  emitted,
                  (unsigned __int64)p,
                  (unsigned __int64)source,
                  (unsigned __int64)ctl,
                  (unsigned __int64)source_vt,
                  (unsigned __int64)ctl_vt,
                  (int64_t)type,
                  (unsigned __int64)d8_q,
                  (unsigned __int64)slot98,
                  (unsigned __int64)slota0,
                  s38,
                  s78,
                  sb0,
                  s660,
                  s6a0);
        append_file_bytes(VOICE_PROBE_LOG, line, (DWORD)lstrlenA(line));
    }
}

static int std_string_preview_safe(int64_t s, char *out, int out_sz,
                                   int64_t *len_out, int64_t *cap_out) {
    int64_t len = 0;
    int64_t cap = 0;
    if (out && out_sz > 0) out[0] = 0;
    if (len_out) *len_out = 0;
    if (cap_out) *cap_out = 0;
    if (!s) return 0;
    __try {
        len = r64((void *)(s + 0x10));
        cap = r64((void *)(s + 0x18));
        if (len_out) *len_out = len;
        if (cap_out) *cap_out = cap;
        if (out && out_sz > 0 && len >= 0 && len < 0x100000) {
            wx_read_sso_str(s, out, out_sz);
        }
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
        if (out && out_sz > 0) out[0] = 0;
        if (len_out) *len_out = 0;
        if (cap_out) *cap_out = 0;
        return 0;
    }
    return len > 0;
}

static int selection_ctx_tag_nonempty(int64_t ctx) {
    int64_t len = 0;
    if (!ctx) return 0;
    __try {
        len = r64((void *)(ctx + 0x38));
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
        len = 0;
    }
    return len > 0 && len < 0x100000;
}

static int std_string_nonempty_safe(int64_t s) {
    int64_t len = 0;
    if (!s) return 0;
    __try {
        len = r64((void *)(s + 0x10));
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
        len = 0;
    }
    return len > 0 && len < 0x100000;
}

static void log_selection_ctx_dump(const char *phase, int64_t ctx) {
    char line[4096];
    char tag[256] = {0};
    int64_t q[17];
    int64_t tag_len = 0;
    int64_t tag_cap = 0;
    for (int i = 0; i < 17; i++) q[i] = 0;
    __try {
        if (ctx) {
            for (int i = 0; i < 17; i++) q[i] = r64((void *)(ctx + i * 8));
            std_string_preview_safe(ctx + 0x28, tag, sizeof(tag), &tag_len, &tag_cap);
        }
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
    }
    wsprintfA(line,
              "voice_probe_selection_ctx_%s: tid=%lu ctx=%I64x "
              "tag28_len=%I64d tag28_cap=%I64d tag28='%.180s' "
              "q00=%I64x q08=%I64x q10=%I64x q18=%I64x q20=%I64x q28=%I64x q30=%I64x q38=%I64x "
              "q40=%I64x q48=%I64x q50=%I64x q58=%I64x q60=%I64x q68=%I64x q70=%I64x q78=%I64x q80=%I64x\r\n",
              phase ? phase : "event",
              GetCurrentThreadId(),
              (unsigned __int64)ctx,
              (int64_t)tag_len,
              (int64_t)tag_cap,
              tag,
              (unsigned __int64)q[0],
              (unsigned __int64)q[1],
              (unsigned __int64)q[2],
              (unsigned __int64)q[3],
              (unsigned __int64)q[4],
              (unsigned __int64)q[5],
              (unsigned __int64)q[6],
              (unsigned __int64)q[7],
              (unsigned __int64)q[8],
              (unsigned __int64)q[9],
              (unsigned __int64)q[10],
              (unsigned __int64)q[11],
              (unsigned __int64)q[12],
              (unsigned __int64)q[13],
              (unsigned __int64)q[14],
              (unsigned __int64)q[15],
              (unsigned __int64)q[16]);
    append_file_bytes(VOICE_PROBE_LOG, line, (DWORD)lstrlenA(line));
}

static void log_extra_dump(const char *phase, int64_t extra) {
    char line[2048];
    char s8[160] = {0};
    int64_t q0 = 0, q8 = 0, q10 = 0, q18 = 0, q20 = 0, q28 = 0;
    int64_t q90 = 0, q98 = 0, qa0 = 0;
    int64_t s8_len = 0, s8_cap = 0;
    __try {
        if (extra) {
            q0 = r64((void *)(extra + 0x00));
            q8 = r64((void *)(extra + 0x08));
            q10 = r64((void *)(extra + 0x10));
            q18 = r64((void *)(extra + 0x18));
            q20 = r64((void *)(extra + 0x20));
            q28 = r64((void *)(extra + 0x28));
            q90 = r64((void *)(extra + 0x90));
            q98 = r64((void *)(extra + 0x98));
            qa0 = r64((void *)(extra + 0xA0));
            std_string_preview_safe(extra + 0x08, s8, sizeof(s8), &s8_len, &s8_cap);
        }
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
    }
    wsprintfA(line,
              "voice_probe_extra_%s: tid=%lu extra=%I64x "
              "q00=%I64x q08=%I64x q10=%I64x q18=%I64x q20=%I64x q28=%I64x "
              "q90=%I64x q98=%I64x qA0=%I64x s8_len=%I64d s8_cap=%I64d s8='%.100s'\r\n",
              phase ? phase : "event",
              GetCurrentThreadId(),
              (unsigned __int64)extra,
              (unsigned __int64)q0,
              (unsigned __int64)q8,
              (unsigned __int64)q10,
              (unsigned __int64)q18,
              (unsigned __int64)q20,
              (unsigned __int64)q28,
              (unsigned __int64)q90,
              (unsigned __int64)q98,
              (unsigned __int64)qa0,
              (int64_t)s8_len,
              (int64_t)s8_cap,
              s8);
    append_file_bytes(VOICE_PROBE_LOG, line, (DWORD)lstrlenA(line));
}

static void log_callback_dump(const char *phase, int64_t cb) {
    char line[2048];
    char nz[1024];
    int64_t cb9 = 0, cb17 = 0, cb25 = 0;
    int64_t vt9 = 0, vt17 = 0, vt25 = 0;
    int pos = 0;
    nz[0] = 0;
    __try {
        if (cb) {
            cb9 = r64((void *)(cb + 9 * 8));
            cb17 = r64((void *)(cb + 17 * 8));
            cb25 = r64((void *)(cb + 25 * 8));
            if (cb9) vt9 = r64((void *)cb9);
            if (cb17) vt17 = r64((void *)cb17);
            if (cb25) vt25 = r64((void *)cb25);
            for (int i = 0; i < 32 && pos + 80 < (int)sizeof(nz); i++) {
                int64_t slot = r64((void *)(cb + i * 8));
                int64_t vt = 0;
                char tmp[96];
                if (!slot) continue;
                __try { vt = r64((void *)slot); } __except (EXCEPTION_EXECUTE_HANDLER) { vt = 0; }
                wsprintfA(tmp, "%d:%I64x/%I64x ", i, (unsigned __int64)slot, (unsigned __int64)vt);
                lstrcpynA(nz + pos, tmp, (int)sizeof(nz) - pos);
                pos = lstrlenA(nz);
            }
        }
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
    }
    wsprintfA(line,
              "voice_probe_callbacks_%s: tid=%lu cb=%I64x "
              "slot9=%I64x vt=%I64x slot17=%I64x vt=%I64x slot25=%I64x vt=%I64x nonzero='%s'\r\n",
              phase ? phase : "event",
              GetCurrentThreadId(),
              (unsigned __int64)cb,
              (unsigned __int64)cb9,
              (unsigned __int64)vt9,
              (unsigned __int64)cb17,
              (unsigned __int64)vt17,
              (unsigned __int64)cb25,
              (unsigned __int64)vt25,
              nz);
    append_file_bytes(VOICE_PROBE_LOG, line, (DWORD)lstrlenA(line));
}

static void log_source_send_call(const char *phase, int64_t ret,
                                 int64_t a1, int64_t a2, int64_t a3,
                                 int64_t a4, int64_t a5) {
    char line[1536];
    char tag[256] = {0};
    int64_t tag_len = 0, tag_cap = 0;
    int64_t out0 = 0, out8 = 0, out10 = 0, out18 = 0, out20 = 0;
    int64_t vec_begin = 0, vec_end = 0, vec_cap = 0, vec_count = 0;
    const char *branch = "unknown";
    __try {
        if (a1) {
            out0 = r64((void *)(a1 + 0x00));
            out8 = r64((void *)(a1 + 0x08));
            out10 = r64((void *)(a1 + 0x10));
            out18 = r64((void *)(a1 + 0x18));
            out20 = r64((void *)(a1 + 0x20));
        }
        if (a2) {
            std_string_preview_safe(a2 + 0x28, tag, sizeof(tag), &tag_len, &tag_cap);
            branch = (tag_len > 0) ? "route_1805FE160" : "convert_1841E1A60";
        }
        if (a3) {
            vec_begin = r64((void *)(a3 + 0x00));
            vec_end = r64((void *)(a3 + 0x08));
            vec_cap = r64((void *)(a3 + 0x10));
            if (vec_begin && vec_end >= vec_begin && vec_end - vec_begin <= 0x10000) {
                vec_count = (vec_end - vec_begin) / 16;
            }
        }
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
    }
    wsprintfA(line,
              "voice_probe_source_send_%s: tid=%lu ret=%I64x "
              "RCX/out=%I64x RDX/ctx=%I64x R8/source_vec=%I64x R9/extra=%I64x stack5/cb=%I64x "
              "branch=%s ctx_tag_len=%I64d ctx_tag_cap=%I64d ctx_tag='%.180s' "
              "vec={%I64x,%I64x,%I64x} count=%I64d out={%I64x,%I64x,%I64x,%I64x,%I64x}\r\n",
              phase ? phase : "event",
              GetCurrentThreadId(),
              (unsigned __int64)ret,
              (unsigned __int64)a1,
              (unsigned __int64)a2,
              (unsigned __int64)a3,
              (unsigned __int64)a4,
              (unsigned __int64)a5,
              branch,
              (int64_t)tag_len,
              (int64_t)tag_cap,
              tag,
              (unsigned __int64)vec_begin,
              (unsigned __int64)vec_end,
              (unsigned __int64)vec_cap,
              (int64_t)vec_count,
              (unsigned __int64)out0,
              (unsigned __int64)out8,
              (unsigned __int64)out10,
              (unsigned __int64)out18,
              (unsigned __int64)out20);
    append_file_bytes(VOICE_PROBE_LOG, line, (DWORD)lstrlenA(line));
}

static void log_route_tag_vec(const char *phase, int64_t tag_str, int64_t source_vec) {
    char line[1024];
    char tag[256] = {0};
    int64_t tag_len = 0, tag_cap = 0;
    std_string_preview_safe(tag_str, tag, sizeof(tag), &tag_len, &tag_cap);
    wsprintfA(line,
              "voice_probe_route_tag_%s: tid=%lu tag_str=%I64x tag_len=%I64d tag_cap=%I64d tag='%.180s' vec=%I64x\r\n",
              phase ? phase : "event",
              GetCurrentThreadId(),
              (unsigned __int64)tag_str,
              (int64_t)tag_len,
              (int64_t)tag_cap,
              tag,
              (unsigned __int64)source_vec);
    append_file_bytes(VOICE_PROBE_LOG, line, (DWORD)lstrlenA(line));
    log_source_vec_dump(phase, "route_vec", source_vec);
}

static void log_route_container_obj(const char *phase, int64_t obj) {
    char line[1536];
    char tag[256] = {0};
    int64_t tag_len = 0, tag_cap = 0;
    int64_t vt = 0;
    int64_t vec_begin = 0, vec_end = 0, vec_cap = 0, vec_count = 0;
    __try {
        if (obj) {
            vt = r64((void *)obj);
            std_string_preview_safe(obj + 0x08, tag, sizeof(tag), &tag_len, &tag_cap);
            vec_begin = r64((void *)(obj + 0x28));
            vec_end = r64((void *)(obj + 0x30));
            vec_cap = r64((void *)(obj + 0x38));
            if (vec_begin && vec_end >= vec_begin && vec_end - vec_begin <= 0x10000) {
                vec_count = (vec_end - vec_begin) / 16;
            }
        }
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
    }
    wsprintfA(line,
              "voice_probe_route_container_%s: tid=%lu obj=%I64x vt=%I64x "
              "tag_len=%I64d tag_cap=%I64d tag='%.180s' vec={%I64x,%I64x,%I64x} count=%I64d\r\n",
              phase ? phase : "event",
              GetCurrentThreadId(),
              (unsigned __int64)obj,
              (unsigned __int64)vt,
              (int64_t)tag_len,
              (int64_t)tag_cap,
              tag,
              (unsigned __int64)vec_begin,
              (unsigned __int64)vec_end,
              (unsigned __int64)vec_cap,
              (int64_t)vec_count);
    append_file_bytes(VOICE_PROBE_LOG, line, (DWORD)lstrlenA(line));
    if (obj) {
        log_source_vec_dump(phase, "route_container_vec", obj + 0x28);
    }
}

static void log_source_extra_apply(const char *phase, int64_t a1, int64_t a2, int64_t a3) {
    char line[1536];
    char extra_s8[160] = {0};
    char source_s38[160] = {0};
    char source_s78[160] = {0};
    char source_sb0[160] = {0};
    int64_t source = 0;
    int64_t ctl = 0;
    int64_t source_vt = 0;
    int64_t ctl_vt = 0;
    int64_t type = 0;
    int extra_flag = 0;
    int extra_dword0 = 0;
    __try {
        if (a1) {
            source = r64((void *)a1);
            ctl = r64((void *)(a1 + 8));
        }
        if (source) {
            source_vt = r64((void *)source);
            type = (int64_t)(int32_t)r32((void *)(source + 0xD8));
            wx_read_sso_str(source + 0x38, source_s38, sizeof(source_s38));
            wx_read_sso_str(source + 0x78, source_s78, sizeof(source_s78));
            wx_read_sso_str(source + 0xB0, source_sb0, sizeof(source_sb0));
        }
        if (ctl) ctl_vt = r64((void *)ctl);
        if (a2) {
            extra_dword0 = (int)r32((void *)a2);
            extra_flag = (int)(BYTE)r32((void *)(a2 + 0x98));
            wx_read_sso_str(a2 + 8, extra_s8, sizeof(extra_s8));
        }
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
    }
    wsprintfA(line,
              "voice_probe_source_extra_apply_%s: tid=%lu a1=%I64x a2=%I64x a3=%I64x "
              "source={%I64x,%I64x} source_vt=%I64x ctl_vt=%I64x type=%I64d "
              "extra_dword0=%d extra_flag98=%d extra_s8='%.100s' "
              "source_s38='%.80s' source_s78='%.80s' source_sb0='%.100s'\r\n",
              phase ? phase : "event",
              GetCurrentThreadId(),
              (unsigned __int64)a1,
              (unsigned __int64)a2,
              (unsigned __int64)a3,
              (unsigned __int64)source,
              (unsigned __int64)ctl,
              (unsigned __int64)source_vt,
              (unsigned __int64)ctl_vt,
              (int64_t)type,
              extra_dword0,
              extra_flag,
              extra_s8,
              source_s38,
              source_s78,
              source_sb0);
    append_file_bytes(VOICE_PROBE_LOG, line, (DWORD)lstrlenA(line));
}

static int source_extra_apply_interesting(int64_t a1) {
    int64_t source = 0;
    __try {
        if (a1) source = r64((void *)a1);
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
        return 0;
    }
    return source_pair_type(source, NULL, NULL);
}

static int64_t __fastcall voice_send_wrap_hook(int64_t a1, int64_t a2,
                                               int64_t voice_data,
                                               int64_t target,
                                               int64_t duration,
                                               int64_t meta) {
    FnVoiceSendWrap original = (FnVoiceSendWrap)g_voice_probe_hook_state.trampoline;
    int64_t ret;
    log_voice_probe_args("entry", 0, a1, a2, voice_data, target, duration, meta);
    if (!original) return 0;
    ret = original(a1, a2, voice_data, target, duration, meta);
    log_voice_probe_args("return", ret, a1, a2, voice_data, target, duration, meta);
    return ret;
}

static int64_t __fastcall voice_stage_buffer_hook(int64_t a1, int64_t a2, int64_t a3, int64_t a4) {
    FnVoiceProbeCall4 original = (FnVoiceProbeCall4)g_voice_stage_buffer.trampoline;
    int64_t ret;
    log_voice_stage_args("stage_1852F1740_entry", 0, a1, a2, a3, a4);
    if (!original) return 0;
    ret = original(a1, a2, a3, a4);
    log_voice_stage_args("stage_1852F1740_return", ret, a1, a2, a3, a4);
    return ret;
}

static int64_t __fastcall voice_stage_send_final_hook(int64_t a1, int64_t a2, int64_t a3, int64_t a4) {
    FnVoiceProbeCall4 original = (FnVoiceProbeCall4)g_voice_stage_send_final.trampoline;
    int64_t ret = 0;
    log_voice_stage_args("stage_1852F0F20_entry", 0, a1, a2, a3, a4);
    if (original) ret = original(a1, a2, a3, a4);
    log_voice_stage_args("stage_1852F0F20_return", ret, a1, a2, a3, a4);
    if (original && file_exists(VOICE_PROBE_FINAL_REPLAY_ONCE_FILE) &&
        InterlockedCompareExchange(&g_voice_final_replay_once_fired, 1, 0) == 0) {
        int64_t replay_ret = 0;
        int replay_status = 0;
        DeleteFileW(VOICE_PROBE_FINAL_REPLAY_ONCE_FILE);
        append_voice_probe_literal("voice_probe_final_replay_once: begin trampoline replay after official return\r\n");
        log_voice_stage_args("stage_1852F0F20_replay_before", 0, a1, a2, a3, a4);
        __try {
            replay_ret = original(a1, a2, a3, a4);
            replay_status = 1;
        }
        __except (EXCEPTION_EXECUTE_HANDLER) {
            replay_status = -1;
        }
        log_voice_stage_args("stage_1852F0F20_replay_after", replay_ret, a1, a2, a3, a4);
        if (replay_status == 1) {
            append_voice_probe_literal("voice_probe_final_replay_once: replay returned\r\n");
        } else {
            append_voice_probe_literal("voice_probe_final_replay_once: replay exception\r\n");
        }
    }
    return ret;
}

static int64_t __fastcall voice_cached_flush_hook(int64_t a1, int64_t a2, int64_t a3, int64_t a4) {
    FnVoiceProbeCall4 original = (FnVoiceProbeCall4)g_voice_cached_flush.trampoline;
    int64_t ret = 0;
    log_voice_flush_args("cached_flush_1852F0CA0_entry", 0, 0, a1, a2, a3, a4);
    if (original) ret = original(a1, a2, a3, a4);
    log_voice_flush_args("cached_flush_1852F0CA0_return", ret, 0, a1, a2, a3, a4);
    return ret;
}

static int64_t __fastcall voice_stage_upper_a_hook(int64_t a1, int64_t a2) {
    FnVoiceProbeCall2 original = (FnVoiceProbeCall2)g_voice_stage_upper_a.trampoline;
    int64_t ret;
    log_voice_stage_args("stage_1852FBB00_entry", 0, a1, a2, 0, 0);
    if (!original) return 0;
    ret = original(a1, a2);
    log_voice_stage_args("stage_1852FBB00_return", ret, a1, a2, 0, 0);
    return ret;
}

static int64_t __fastcall voice_stage_upper_b_hook(int64_t a1, int64_t a2, int64_t a3, int64_t a4) {
    FnVoiceProbeCall4 original = (FnVoiceProbeCall4)g_voice_stage_upper_b.trampoline;
    int64_t ret;
    log_voice_stage_args("stage_1852FC160_entry", 0, a1, a2, a3, a4);
    if (!original) return 0;
    ret = original(a1, a2, a3, a4);
    log_voice_stage_args("stage_1852FC160_return", ret, a1, a2, a3, a4);
    return ret;
}

static int64_t __fastcall voice_holder_flush_hook(int64_t a1, int64_t a2, int64_t a3, int64_t a4) {
    FnVoiceProbeCall4 original = (FnVoiceProbeCall4)g_voice_holder_flush.trampoline;
    int64_t ret = 0;
    int64_t state = 0;
    __try {
        if (a1) state = r64((void *)(a1 + 0x168));
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
        state = 0;
    }
    log_voice_flush_args("holder_flush_1852F8460_entry", 0, a1, state, a2, a3, a4);
    if (original) ret = original(a1, a2, a3, a4);
    state = 0;
    __try {
        if (a1) state = r64((void *)(a1 + 0x168));
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
        state = 0;
    }
    log_voice_flush_args("holder_flush_1852F8460_return", ret, a1, state, a2, a3, a4);
    return ret;
}

static int64_t __fastcall voice_holder_init_hook(int64_t a1) {
    FnVoiceProbeCall1 original = (FnVoiceProbeCall1)g_voice_holder_init.trampoline;
    int64_t ret = 0;
    if (original) ret = original(a1);
    voice_probe_cache_holder_state("1852F2F30_return", a1);
    return ret;
}

static int64_t __fastcall voice_controller_bind_hook(int64_t a1) {
    FnVoiceProbeCall1 original = (FnVoiceProbeCall1)g_voice_controller_bind.trampoline;
    int64_t ret = 0;
    if (original) ret = original(a1);
    voice_probe_cache_controller_state("1852F0020_return", a1);
    return ret;
}

static int64_t __fastcall voice_target_set_hook(int64_t a1, int64_t a2) {
    FnVoiceProbeCall2 original = (FnVoiceProbeCall2)g_voice_target_set.trampoline;
    int64_t ret = 0;
    if (original) ret = original(a1, a2);
    voice_probe_cache_holder_state("1852F65A0_return", a1);
    return ret;
}

static int64_t __fastcall voice_state_rebuild_hook(int64_t a1) {
    FnVoiceProbeCall1 original = (FnVoiceProbeCall1)g_voice_state_rebuild.trampoline;
    int64_t ret = 0;
    if (original) ret = original(a1);
    voice_probe_cache_holder_state("1852F6880_return", a1);
    return ret;
}

static int64_t __fastcall voice_holder_dtor_hook(int64_t a1) {
    FnVoiceProbeCall1 original = (FnVoiceProbeCall1)g_voice_holder_dtor.trampoline;
    int64_t ret = 0;
    voice_probe_invalidate_holder_state("1852F5FF0_entry", a1);
    if (original) ret = original(a1);
    return ret;
}

static int64_t __fastcall voice_submit_18160DCD0_hook(int64_t a1, int64_t a2,
                                                      int64_t a3, int64_t a4,
                                                      int64_t a5, int64_t a6) {
    FnVoiceProbeCall6 original = (FnVoiceProbeCall6)g_voice_submit_18160DCD0.trampoline;
    int64_t ret;
    log_voice_submit_args("submit_18160DCD0_entry", 0, a1, a2, a3, a4, a5, a6);
    if (!original) return 0;
    ret = original(a1, a2, a3, a4, a5, a6);
    log_voice_submit_args("submit_18160DCD0_return", ret, a1, a2, a3, a4, a5, a6);
    return ret;
}

static int64_t __fastcall source_task_build_generic_hook(int64_t a1, int64_t a2, int64_t a3) {
    FnVoiceProbeCall3 original = (FnVoiceProbeCall3)g_source_task_build_generic.trampoline;
    int64_t base = (int64_t)wxb_weixin_module();
    int64_t source = 0;
    int64_t ctl = 0;
    int64_t source_vt = 0;
    int64_t ctl_vt = 0;
    int64_t type = 0;
    int64_t ret = 0;
    int is_voice = source_vec_first_voice(a3, &source, &ctl, &source_vt, &ctl_vt, &type);
    int is_type34 = type == 34;
    int force_voice_builder = file_exists(VOICE_SOURCE_BUILDER_SHIM_FILE) &&
                              voice_source_builder_shim_active() &&
                              base;
    if (force_voice_builder) {
        FnVoiceProbeCall3 voice_builder = (FnVoiceProbeCall3)(base + WX_OFF_SOURCE_TASK_BUILD_VOICE);
        log_source_task_builder(is_voice ? "voice_shim_before" : "forced_shim_before",
                                0, a1, a2, a3, 1,
                                source, ctl, source_vt, ctl_vt, type);
        ret = voice_builder(a1, a2, a3);
        log_source_task_builder(is_voice ? "voice_shim_after" : "forced_shim_after",
                                ret, a1, a2, a3, 1,
                                source, ctl, source_vt, ctl_vt, type);
        return ret;
    }
    if (is_voice || is_type34) {
        log_source_task_builder(is_voice ? "voice_passthrough_before" : "type34_passthrough_before",
                                0, a1, a2, a3, 0,
                                source, ctl, source_vt, ctl_vt, type);
        log_source_vec_dump("task_builder_before", "a3", a3);
    }
    ret = original ? original(a1, a2, a3) : 0;
    if (is_voice || is_type34) {
        log_source_task_builder(is_voice ? "voice_passthrough_after" : "type34_passthrough_after",
                                ret, a1, a2, a3, 0,
                                source, ctl, source_vt, ctl_vt, type);
    }
    return ret;
}

static int64_t __fastcall source_extra_apply_hook(int64_t a1, int64_t a2, int64_t a3) {
    FnVoiceProbeCall3 original = (FnVoiceProbeCall3)g_source_extra_apply.trampoline;
    int interesting = source_extra_apply_interesting(a1);
    int64_t ret = 0;
    if (interesting) log_source_extra_apply("entry", a1, a2, a3);
    if (original) ret = original(a1, a2, a3);
    if (interesting || source_extra_apply_interesting(a1)) {
        log_source_extra_apply("return", a1, a2, a3);
    }
    return ret;
}

static int64_t __fastcall source_inject_convert_hook(int64_t a1, int64_t a2,
                                                     int64_t a3, int64_t a4) {
    FnVoiceProbeCall4 original = (FnVoiceProbeCall4)g_source_inject_convert.trampoline;
    int interesting = source_vec_has_type34(a2) || source_vec_has_type34(a4);
    int64_t ret = 0;
    if (interesting) {
        char line[512];
        int64_t map_count = 0;
        __try {
            if (a3) map_count = r64((void *)(a3 + 8));
        }
        __except (EXCEPTION_EXECUTE_HANDLER) {
            map_count = 0;
        }
        wsprintfA(line,
                  "voice_probe_source_inject_convert_entry: tid=%lu out=%I64x in=%I64x route_map=%I64x ctx_vec=%I64x map_count=%I64d\r\n",
                  GetCurrentThreadId(),
                  (unsigned __int64)a1,
                  (unsigned __int64)a2,
                  (unsigned __int64)a3,
                  (unsigned __int64)a4,
                  (int64_t)map_count);
        append_file_bytes(VOICE_PROBE_LOG, line, (DWORD)lstrlenA(line));
        log_source_vec_dump("convert_entry", "in", a2);
        log_source_vec_dump("convert_entry", "ctx", a4);
    }
    if (original) ret = original(a1, a2, a3, a4);
    if (interesting || source_vec_has_type34(a1)) {
        char line[512];
        wsprintfA(line,
                  "voice_probe_source_inject_convert_return: tid=%lu ret=%I64x out=%I64x in=%I64x route_map=%I64x ctx_vec=%I64x\r\n",
                  GetCurrentThreadId(),
                  (unsigned __int64)ret,
                  (unsigned __int64)a1,
                  (unsigned __int64)a2,
                  (unsigned __int64)a3,
                  (unsigned __int64)a4);
        append_file_bytes(VOICE_PROBE_LOG, line, (DWORD)lstrlenA(line));
        log_source_vec_dump("convert_return", "out", a1);
    }
    return ret;
}

static int source_send_interesting(int64_t selection_ctx, int64_t source_vec) {
    return file_exists(VOICE_PROBE_ENABLE_FILE) ||
           source_vec_has_type34(source_vec) ||
           selection_ctx_tag_nonempty(selection_ctx) ||
           voice_source_builder_shim_active() ||
           file_exists(VOICE_SOURCE_CTX_BRANCH_FILE);
}

static int64_t __fastcall source_send_hook(int64_t a1, int64_t a2, int64_t a3,
                                           int64_t a4, int64_t a5) {
    FnVoiceProbeCall5 original = (FnVoiceProbeCall5)g_source_send.trampoline;
    int interesting = source_send_interesting(a2, a3);
    int entered = 0;
    int64_t ret = 0;
    if (interesting) {
        entered = 1;
        InterlockedIncrement(&g_source_send_interesting_depth);
        log_source_send_call("entry", 0, a1, a2, a3, a4, a5);
        log_selection_ctx_dump("source_send_entry", a2);
        log_source_vec_dump("source_send_entry", "source_vec", a3);
        log_extra_dump("source_send_entry", a4);
        log_callback_dump("source_send_entry", a5);
    }
    if (original) ret = original(a1, a2, a3, a4, a5);
    if (interesting) {
        log_source_send_call("return", ret, a1, a2, a3, a4, a5);
        log_selection_ctx_dump("source_send_return", a2);
        log_callback_dump("source_send_return", a5);
    }
    if (entered) InterlockedDecrement(&g_source_send_interesting_depth);
    return ret;
}

static int64_t __fastcall source_send_precheck_hook(int64_t a1) {
    FnVoiceProbeCall1 original = (FnVoiceProbeCall1)g_source_send_precheck.trampoline;
    int interesting = selection_ctx_tag_nonempty(a1) ||
                      InterlockedCompareExchange(&g_source_send_interesting_depth, 0, 0) > 0;
    int64_t ret = 0;
    if (interesting) log_selection_ctx_dump("precheck_182029EE0_entry", a1);
    if (original) ret = original(a1);
    if (interesting) log_selection_ctx_dump("precheck_182029EE0_return", a1);
    return ret;
}

static int64_t __fastcall source_route_container_build_hook(int64_t a1, int64_t a2,
                                                            int64_t a3, int64_t a4) {
    FnVoiceProbeCall4 original = (FnVoiceProbeCall4)g_source_route_container_build.trampoline;
    int interesting = source_vec_has_type34(a4) || std_string_nonempty_safe(a3) ||
                      InterlockedCompareExchange(&g_source_send_interesting_depth, 0, 0) > 0;
    int64_t ret = 0;
    if (interesting) {
        log_route_tag_vec("route_build_1805FE160_entry", a3, a4);
    }
    if (original) ret = original(a1, a2, a3, a4);
    if (interesting) {
        char line[1024];
        int64_t task = 0, task_vt = 0, slot40 = 0, slot48 = 0, slot50 = 0;
        __try {
            if (a2) {
                task = r64((void *)(a2 + 0x38));
                slot40 = r64((void *)(a2 + 0x40));
                slot48 = r64((void *)(a2 + 0x48));
                slot50 = r64((void *)(a2 + 0x50));
                if (task) task_vt = r64((void *)task);
            }
        }
        __except (EXCEPTION_EXECUTE_HANDLER) {
        }
        wsprintfA(line,
                  "voice_probe_route_build_1805FE160_return: tid=%lu ret=%I64x a1=%I64x out_ctx=%I64x "
                  "route_obj=%I64x route_vt=%I64x out_slots={40:%I64x,48:%I64x,50:%I64x}\r\n",
                  GetCurrentThreadId(),
                  (unsigned __int64)ret,
                  (unsigned __int64)a1,
                  (unsigned __int64)a2,
                  (unsigned __int64)task,
                  (unsigned __int64)task_vt,
                  (unsigned __int64)slot40,
                  (unsigned __int64)slot48,
                  (unsigned __int64)slot50);
        append_file_bytes(VOICE_PROBE_LOG, line, (DWORD)lstrlenA(line));
        log_route_container_obj("route_build_1805FE160_return_obj", task);
    }
    return ret;
}

static void log_dispatch_core_call(const char *phase, int64_t ret,
                                   int64_t a1, int64_t a2, int64_t a3,
                                   int64_t a4, int64_t a5, int64_t a6) {
    char line[2048];
    int64_t task = 0, task_vt = 0;
    int64_t out0 = 0, out8 = 0, out10 = 0, out18 = 0;
    int64_t c3_38 = 0, c4_38 = 0, c5_38 = 0, scratch0 = 0;
    __try {
        if (a1) {
            task = r64((void *)(a1 + 0x38));
            if (task) task_vt = r64((void *)task);
        }
        if (a2) {
            out0 = r64((void *)(a2 + 0x00));
            out8 = r64((void *)(a2 + 0x08));
            out10 = r64((void *)(a2 + 0x10));
            out18 = r64((void *)(a2 + 0x18));
        }
        if (a3) c3_38 = r64((void *)(a3 + 0x38));
        if (a4) c4_38 = r64((void *)(a4 + 0x38));
        if (a5) c5_38 = r64((void *)(a5 + 0x38));
        if (a6) scratch0 = r64((void *)a6);
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
    }
    wsprintfA(line,
              "voice_probe_dispatch_core_180128690_%s: tid=%lu ret=%I64x "
              "RCX/task_ctx=%I64x RDX/out=%I64x R8/cb9obj=%I64x R9/cb17obj=%I64x stack5/cb25obj=%I64x stack6/scratch=%I64x "
              "task=%I64x task_vt=%I64x out={%I64x,%I64x,%I64x,%I64x} closure38={%I64x,%I64x,%I64x} scratch0=%I64x\r\n",
              phase ? phase : "event",
              GetCurrentThreadId(),
              (unsigned __int64)ret,
              (unsigned __int64)a1,
              (unsigned __int64)a2,
              (unsigned __int64)a3,
              (unsigned __int64)a4,
              (unsigned __int64)a5,
              (unsigned __int64)a6,
              (unsigned __int64)task,
              (unsigned __int64)task_vt,
              (unsigned __int64)out0,
              (unsigned __int64)out8,
              (unsigned __int64)out10,
              (unsigned __int64)out18,
              (unsigned __int64)c3_38,
              (unsigned __int64)c4_38,
              (unsigned __int64)c5_38,
              (unsigned __int64)scratch0);
    append_file_bytes(VOICE_PROBE_LOG, line, (DWORD)lstrlenA(line));
}

static int64_t __fastcall source_dispatch_core_hook(int64_t a1, int64_t a2,
                                                    int64_t a3, int64_t a4,
                                                    int64_t a5, int64_t a6) {
    FnVoiceProbeCall6 original = (FnVoiceProbeCall6)g_source_dispatch_core.trampoline;
    int interesting = InterlockedCompareExchange(&g_source_send_interesting_depth, 0, 0) > 0;
    int64_t ret = 0;
    if (interesting) log_dispatch_core_call("entry", 0, a1, a2, a3, a4, a5, a6);
    if (original) ret = original(a1, a2, a3, a4, a5, a6);
    if (interesting) log_dispatch_core_call("return", ret, a1, a2, a3, a4, a5, a6);
    return ret;
}

static int64_t __fastcall source_wxwork_pack_a_hook(int64_t a1, int64_t a2) {
    FnVoiceProbeCall2 original = (FnVoiceProbeCall2)g_source_wxwork_pack_a.trampoline;
    int interesting = source_vec_has_type34(a1 + 0x28) || std_string_nonempty_safe(a1 + 0x08);
    int64_t ret = 0;
    if (interesting) {
        log_route_container_obj("wxwork_pack_a_1806034C0_entry", a1);
        log_selection_ctx_dump("wxwork_pack_a_1806034C0_ctx", a2);
    }
    if (original) ret = original(a1, a2);
    if (interesting) {
        char line[512];
        wsprintfA(line,
                  "voice_probe_wxwork_pack_a_1806034C0_return: tid=%lu ret=%I64x a1=%I64x a2=%I64x\r\n",
                  GetCurrentThreadId(),
                  (unsigned __int64)ret,
                  (unsigned __int64)a1,
                  (unsigned __int64)a2);
        append_file_bytes(VOICE_PROBE_LOG, line, (DWORD)lstrlenA(line));
    }
    return ret;
}

static int64_t __fastcall source_wxwork_pack_b_hook(int64_t a1) {
    FnVoiceProbeCall1 original = (FnVoiceProbeCall1)g_source_wxwork_pack_b.trampoline;
    int interesting = source_vec_has_type34(a1 + 0x28) || std_string_nonempty_safe(a1 + 0x08);
    int64_t ret = 0;
    if (interesting) log_route_container_obj("wxwork_pack_b_180603D50_entry", a1);
    if (original) ret = original(a1);
    if (interesting) {
        char line[512];
        wsprintfA(line,
                  "voice_probe_wxwork_pack_b_180603D50_return: tid=%lu ret=%I64x a1=%I64x\r\n",
                  GetCurrentThreadId(),
                  (unsigned __int64)ret,
                  (unsigned __int64)a1);
        append_file_bytes(VOICE_PROBE_LOG, line, (DWORD)lstrlenA(line));
    }
    return ret;
}

static int64_t __fastcall source_route_key_lookup_hook(int64_t a1, int64_t a2, int64_t a3) {
    FnVoiceProbeCall3 original = (FnVoiceProbeCall3)g_source_route_key_lookup.trampoline;
    char tag[256] = {0};
    int64_t tag_len = 0, tag_cap = 0;
    int64_t ret = 0;
    int interesting;
    std_string_preview_safe(a2, tag, sizeof(tag), &tag_len, &tag_cap);
    interesting = tag_len > 0 || InterlockedCompareExchange(&g_source_send_interesting_depth, 0, 0) > 0;
    if (interesting) {
        char line[2048];
        int64_t map_a_head = 0, map_a_buckets = 0, map_a_mask = 0;
        int64_t map_b_head = 0, map_b_buckets = 0, map_b_mask = 0;
        int64_t wxwork_mgr = 0;
        int busy = 0;
        int64_t s0 = 0, s8 = 0, s10 = 0, s38 = 0, s40 = 0, s48 = 0, s50 = 0;
        __try {
            if (a1) {
                map_a_head = r64((void *)(a1 + 304));
                map_a_buckets = r64((void *)(a1 + 320));
                map_a_mask = r64((void *)(a1 + 344));
                map_b_head = r64((void *)(a1 + 496));
                map_b_buckets = r64((void *)(a1 + 512));
                map_b_mask = r64((void *)(a1 + 536));
                wxwork_mgr = r64((void *)(a1 + 552));
                busy = (int)(BYTE)r32((void *)(a1 + 632));
            }
            if (a3) {
                s0 = r64((void *)(a3 + 0x00));
                s8 = r64((void *)(a3 + 0x08));
                s10 = r64((void *)(a3 + 0x10));
                s38 = r64((void *)(a3 + 0x38));
                s40 = r64((void *)(a3 + 0x40));
                s48 = r64((void *)(a3 + 0x48));
                s50 = r64((void *)(a3 + 0x50));
            }
        }
        __except (EXCEPTION_EXECUTE_HANDLER) {
        }
        wsprintfA(line,
                  "voice_probe_route_key_lookup_1814CE590_entry: tid=%lu controller=%I64x tag_str=%I64x state=%I64x "
                  "tag_len=%I64d tag_cap=%I64d tag='%.180s' busy632=%d wxwork_mgr=%I64x "
                  "mapA={head:%I64x buckets:%I64x mask:%I64x} mapB={head:%I64x buckets:%I64x mask:%I64x} "
                  "state_slots={00:%I64x,08:%I64x,10:%I64x,38:%I64x,40:%I64x,48:%I64x,50:%I64x}\r\n",
                  GetCurrentThreadId(),
                  (unsigned __int64)a1,
                  (unsigned __int64)a2,
                  (unsigned __int64)a3,
                  (int64_t)tag_len,
                  (int64_t)tag_cap,
                  tag,
                  busy,
                  (unsigned __int64)wxwork_mgr,
                  (unsigned __int64)map_a_head,
                  (unsigned __int64)map_a_buckets,
                  (unsigned __int64)map_a_mask,
                  (unsigned __int64)map_b_head,
                  (unsigned __int64)map_b_buckets,
                  (unsigned __int64)map_b_mask,
                  (unsigned __int64)s0,
                  (unsigned __int64)s8,
                  (unsigned __int64)s10,
                  (unsigned __int64)s38,
                  (unsigned __int64)s40,
                  (unsigned __int64)s48,
                  (unsigned __int64)s50);
        append_file_bytes(VOICE_PROBE_LOG, line, (DWORD)lstrlenA(line));
    }
    if (original) ret = original(a1, a2, a3);
    if (interesting) {
        char line[768];
        wsprintfA(line,
                  "voice_probe_route_key_lookup_1814CE590_return: tid=%lu ret=%I64x controller=%I64x tag='%.180s'\r\n",
                  GetCurrentThreadId(),
                  (unsigned __int64)ret,
                  (unsigned __int64)a1,
                  tag);
        append_file_bytes(VOICE_PROBE_LOG, line, (DWORD)lstrlenA(line));
    }
    return ret;
}

static int64_t __fastcall source_batch_adapter_hook(int64_t a1, int64_t a2,
                                                    int64_t a3, int64_t a4) {
    FnVoiceProbeCall4 original = (FnVoiceProbeCall4)g_source_batch_adapter.trampoline;
    int interesting = source_vec_has_type34(a4) || std_string_nonempty_safe(a3);
    int64_t ret = 0;
    if (interesting) {
        char line[768];
        int busy = 0;
        __try { if (a1) busy = (int)(BYTE)r32((void *)(a1 + 632)); } __except (EXCEPTION_EXECUTE_HANDLER) { busy = 0; }
        wsprintfA(line,
                  "voice_probe_batch_adapter_1814CF830_entry: tid=%lu controller=%I64x out=%I64x tag=%I64x vec=%I64x busy632=%d\r\n",
                  GetCurrentThreadId(),
                  (unsigned __int64)a1,
                  (unsigned __int64)a2,
                  (unsigned __int64)a3,
                  (unsigned __int64)a4,
                  busy);
        append_file_bytes(VOICE_PROBE_LOG, line, (DWORD)lstrlenA(line));
        log_route_tag_vec("batch_adapter_1814CF830_entry", a3, a4);
    }
    if (original) ret = original(a1, a2, a3, a4);
    if (interesting) {
        char line[512];
        wsprintfA(line,
                  "voice_probe_batch_adapter_1814CF830_return: tid=%lu ret=%I64x controller=%I64x out=%I64x tag=%I64x vec=%I64x\r\n",
                  GetCurrentThreadId(),
                  (unsigned __int64)ret,
                  (unsigned __int64)a1,
                  (unsigned __int64)a2,
                  (unsigned __int64)a3,
                  (unsigned __int64)a4);
        append_file_bytes(VOICE_PROBE_LOG, line, (DWORD)lstrlenA(line));
    }
    return ret;
}

static int64_t __fastcall wxwork_forwardmsg_url_hook(int64_t a1, int64_t a2, int64_t a3) {
    FnVoiceProbeCall3 original = (FnVoiceProbeCall3)g_wxwork_forwardmsg_url.trampoline;
    char tag[256] = {0};
    int64_t tag_len = 0, tag_cap = 0;
    int64_t ret = 0;
    std_string_preview_safe(a2, tag, sizeof(tag), &tag_len, &tag_cap);
    if (tag_len > 0) {
        char line[1024];
        wsprintfA(line,
                  "voice_probe_wxwork_forwardmsg_url_1832F2E40_entry: tid=%lu out=%I64x tag_str=%I64x data=%I64x "
                  "url='wxwork://forwardmsg' key_len=%I64d key_cap=%I64d key='%.180s'\r\n",
                  GetCurrentThreadId(),
                  (unsigned __int64)a1,
                  (unsigned __int64)a2,
                  (unsigned __int64)a3,
                  (int64_t)tag_len,
                  (int64_t)tag_cap,
                  tag);
        append_file_bytes(VOICE_PROBE_LOG, line, (DWORD)lstrlenA(line));
    }
    if (original) ret = original(a1, a2, a3);
    if (tag_len > 0) {
        char line[768];
        wsprintfA(line,
                  "voice_probe_wxwork_forwardmsg_url_1832F2E40_return: tid=%lu ret=%I64x out=%I64x key='%.180s'\r\n",
                  GetCurrentThreadId(),
                  (unsigned __int64)ret,
                  (unsigned __int64)a1,
                  tag);
        append_file_bytes(VOICE_PROBE_LOG, line, (DWORD)lstrlenA(line));
    }
    return ret;
}

static void log_source_dispatch_wrap(const char *phase, int64_t ret,
                                     int64_t a1, int64_t a2, int64_t a3) {
    char line[2048];
    char cb9_s10[120] = {0};
    char cb17_s10[120] = {0};
    char cb25_s10[120] = {0};
    int64_t out0 = 0;
    int64_t out8 = 0;
    int64_t out10 = 0;
    int64_t out18 = 0;
    int64_t cb9 = 0;
    int64_t cb17 = 0;
    int64_t cb25 = 0;
    int64_t cb9_vt = 0;
    int64_t cb17_vt = 0;
    int64_t cb25_vt = 0;
    __try {
        if (a2) {
            out0 = r64((void *)a2);
            out8 = r64((void *)(a2 + 8));
            out10 = r64((void *)(a2 + 0x10));
            out18 = r64((void *)(a2 + 0x18));
        }
        if (a3) {
            cb9 = r64((void *)(a3 + 9 * 8));
            cb17 = r64((void *)(a3 + 17 * 8));
            cb25 = r64((void *)(a3 + 25 * 8));
            if (cb9) {
                cb9_vt = r64((void *)cb9);
                wx_read_sso_str(cb9 + 0x10, cb9_s10, sizeof(cb9_s10));
            }
            if (cb17) {
                cb17_vt = r64((void *)cb17);
                wx_read_sso_str(cb17 + 0x10, cb17_s10, sizeof(cb17_s10));
            }
            if (cb25) {
                cb25_vt = r64((void *)cb25);
                wx_read_sso_str(cb25 + 0x10, cb25_s10, sizeof(cb25_s10));
            }
        }
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
    }
    wsprintfA(line,
              "voice_probe_source_dispatch_wrap_%s: tid=%lu ret=%I64x a1=%I64x a2=%I64x a3=%I64x "
              "out={%I64x,%I64x,%I64x,%I64x} "
              "cb9=%I64x vt=%I64x s10='%.80s' cb17=%I64x vt=%I64x s10='%.80s' cb25=%I64x vt=%I64x s10='%.80s'\r\n",
              phase ? phase : "event",
              GetCurrentThreadId(),
              (unsigned __int64)ret,
              (unsigned __int64)a1,
              (unsigned __int64)a2,
              (unsigned __int64)a3,
              (unsigned __int64)out0,
              (unsigned __int64)out8,
              (unsigned __int64)out10,
              (unsigned __int64)out18,
              (unsigned __int64)cb9,
              (unsigned __int64)cb9_vt,
              cb9_s10,
              (unsigned __int64)cb17,
              (unsigned __int64)cb17_vt,
              cb17_s10,
              (unsigned __int64)cb25,
              (unsigned __int64)cb25_vt,
              cb25_s10);
    append_file_bytes(VOICE_PROBE_LOG, line, (DWORD)lstrlenA(line));
}

static int64_t __fastcall source_dispatch_wrap_hook(int64_t a1, int64_t a2, int64_t a3) {
    FnVoiceProbeCall3 original = (FnVoiceProbeCall3)g_source_dispatch_wrap.trampoline;
    int interesting = voice_source_builder_shim_active();
    int64_t ret = 0;
    if (interesting) log_source_dispatch_wrap("entry", 0, a1, a2, a3);
    if (original) ret = original(a1, a2, a3);
    if (interesting) log_source_dispatch_wrap("return", ret, a1, a2, a3);
    return ret;
}

static int build_voice_probe_trampoline(BYTE *p, BYTE *target) {
    BYTE *start = p;
    copy_bytes(p, g_voice_probe_hook_state.orig, VOICE_PROBE_PATCH_LEN);
    p += VOICE_PROBE_PATCH_LEN;
    *p++ = 0x48; *p++ = 0xB8;         /* mov rax, target+patch_len */
    *(int64_t *)p = (int64_t)(target + VOICE_PROBE_PATCH_LEN); p += 8;
    *p++ = 0xFF; *p++ = 0xE0;         /* jmp rax */
    return (int)(p - start);
}

static int build_voice_site_trampoline(VoiceProbeSite *site, BYTE *p, BYTE *target) {
    BYTE *start = p;
    copy_bytes(p, site->orig, site->len);
    p += site->len;
    *p++ = 0x48; *p++ = 0xB8;         /* mov rax, target+patch_len */
    *(int64_t *)p = (int64_t)(target + site->len); p += 8;
    *p++ = 0xFF; *p++ = 0xE0;         /* jmp rax */
    return (int)(p - start);
}

static void restore_voice_probe_stale_if_present(void) {
    BYTE *target;
    DWORD oldp = 0;
    if (!wxb_weixin_module()) return;
    target = (BYTE *)((int64_t)wxb_weixin_module() + WX_OFF_SOURCE_VOICE_SEND_WRAP);
    if (!(target[0] == 0x48 && target[1] == 0xB8 && target[10] == 0xFF && target[11] == 0xE0)) return;
    VirtualProtect(target, VOICE_PROBE_PATCH_LEN, PAGE_EXECUTE_READWRITE, &oldp);
    copy_bytes(target, WX_ORIG_VOICE_SEND_WRAP_BYTES, VOICE_PROBE_PATCH_LEN);
    VirtualProtect(target, VOICE_PROBE_PATCH_LEN, oldp, &oldp);
    FlushInstructionCache(GetCurrentProcess(), target, VOICE_PROBE_PATCH_LEN);
    if (InterlockedCompareExchange(&g_voice_probe_hook_state.stale_restored, 1, 0) == 0) {
        append_voice_probe_literal("voice_probe: stale hook restored\r\n");
    }
}

static void restore_voice_site_stale_if_present(VoiceProbeSite *site) {
    BYTE *target;
    DWORD oldp = 0;
    char line[256];
    if (!site || !wxb_weixin_module()) return;
    target = (BYTE *)((int64_t)wxb_weixin_module() + site->rva);
    if (!(target[0] == 0x48 && target[1] == 0xB8 && target[10] == 0xFF && target[11] == 0xE0)) return;
    VirtualProtect(target, site->len, PAGE_EXECUTE_READWRITE, &oldp);
    copy_bytes(target, site->expected, site->len);
    VirtualProtect(target, site->len, oldp, &oldp);
    FlushInstructionCache(GetCurrentProcess(), target, site->len);
    if (InterlockedCompareExchange(&site->stale_restored, 1, 0) == 0) {
        wsprintfA(line, "voice_probe: stale hook restored %s\r\n", site->name);
        append_file_bytes(VOICE_PROBE_LOG, line, (DWORD)lstrlenA(line));
    }
}

static void install_voice_probe_site(VoiceProbeSite *site, void *hook_fn) {
    BYTE *target;
    BYTE patch[VOICE_PROBE_MAX_PATCH_LEN];
    DWORD old = 0;
    int tlen;
    char line[256];
    if (!site || !hook_fn || site->installed || !wxb_weixin_module()) return;
    target = (BYTE *)((int64_t)wxb_weixin_module() + site->rva);
    if (target[0] == 0x48 && target[1] == 0xB8) {
        restore_voice_site_stale_if_present(site);
    }
    if (!bytes_equal(target, site->expected, site->len)) {
        wsprintfA(line, "voice_probe: target bytes mismatch; %s not installed\r\n", site->name);
        append_file_bytes(VOICE_PROBE_LOG, line, (DWORD)lstrlenA(line));
        return;
    }

    copy_bytes(site->orig, target, site->len);
    site->trampoline = VirtualAlloc(NULL, 128, MEM_COMMIT | MEM_RESERVE, PAGE_EXECUTE_READWRITE);
    if (!site->trampoline) {
        wsprintfA(line, "voice_probe: trampoline alloc failed %s\r\n", site->name);
        append_file_bytes(VOICE_PROBE_LOG, line, (DWORD)lstrlenA(line));
        return;
    }
    tlen = build_voice_site_trampoline(site, (BYTE *)site->trampoline, target);
    FlushInstructionCache(GetCurrentProcess(), site->trampoline, tlen);

    for (int i = 0; i < site->len && i < VOICE_PROBE_MAX_PATCH_LEN; i++) patch[i] = 0x90;
    patch[0] = 0x48; patch[1] = 0xB8;
    *(int64_t *)(patch + 2) = (int64_t)hook_fn;
    patch[10] = 0xFF; patch[11] = 0xE0;

    VirtualProtect(target, site->len, PAGE_EXECUTE_READWRITE, &old);
    copy_bytes(target, patch, site->len);
    VirtualProtect(target, site->len, old, &old);
    FlushInstructionCache(GetCurrentProcess(), target, site->len);
    InterlockedExchange(&site->installed, 1);
    wsprintfA(line, "voice_probe: hook installed %s\r\n", site->name);
    append_file_bytes(VOICE_PROBE_LOG, line, (DWORD)lstrlenA(line));
}

static void uninstall_voice_probe_site(VoiceProbeSite *site) {
    BYTE *target;
    DWORD old = 0;
    char line[256];
    if (!site || !site->installed || !wxb_weixin_module()) return;
    target = (BYTE *)((int64_t)wxb_weixin_module() + site->rva);
    VirtualProtect(target, site->len, PAGE_EXECUTE_READWRITE, &old);
    copy_bytes(target, site->orig, site->len);
    VirtualProtect(target, site->len, old, &old);
    FlushInstructionCache(GetCurrentProcess(), target, site->len);
    InterlockedExchange(&site->installed, 0);
    if (site->trampoline) {
        VirtualFree(site->trampoline, 0, MEM_RELEASE);
        site->trampoline = NULL;
    }
    wsprintfA(line, "voice_probe: hook uninstalled %s\r\n", site->name);
    append_file_bytes(VOICE_PROBE_LOG, line, (DWORD)lstrlenA(line));
}

void install_voice_probe_hook(void) {
    BYTE *target;
    BYTE patch[VOICE_PROBE_PATCH_LEN];
    DWORD old = 0;
    int tlen;

    if (!wxb_weixin_module()) return;
    if (!g_voice_probe_hook_state.installed) {
        target = (BYTE *)((int64_t)wxb_weixin_module() + WX_OFF_SOURCE_VOICE_SEND_WRAP);
        if (target[0] == 0x48 && target[1] == 0xB8) {
            restore_voice_probe_stale_if_present();
        }
        if (!bytes_equal(target, WX_ORIG_VOICE_SEND_WRAP_BYTES, VOICE_PROBE_PATCH_LEN)) {
            append_voice_probe_literal("voice_probe: target bytes mismatch; wrapper not installed\r\n");
        } else {
            copy_bytes(g_voice_probe_hook_state.orig, target, VOICE_PROBE_PATCH_LEN);
            g_voice_probe_hook_state.trampoline = VirtualAlloc(NULL, 128, MEM_COMMIT | MEM_RESERVE, PAGE_EXECUTE_READWRITE);
            if (!g_voice_probe_hook_state.trampoline) {
                append_voice_probe_literal("voice_probe: wrapper trampoline alloc failed\r\n");
            } else {
                tlen = build_voice_probe_trampoline((BYTE *)g_voice_probe_hook_state.trampoline, target);
                FlushInstructionCache(GetCurrentProcess(), g_voice_probe_hook_state.trampoline, tlen);

                for (int i = 0; i < VOICE_PROBE_PATCH_LEN; i++) patch[i] = 0x90;
                patch[0] = 0x48; patch[1] = 0xB8;
                *(int64_t *)(patch + 2) = (int64_t)voice_send_wrap_hook;
                patch[10] = 0xFF; patch[11] = 0xE0;

                VirtualProtect(target, VOICE_PROBE_PATCH_LEN, PAGE_EXECUTE_READWRITE, &old);
                copy_bytes(target, patch, VOICE_PROBE_PATCH_LEN);
                VirtualProtect(target, VOICE_PROBE_PATCH_LEN, old, &old);
                FlushInstructionCache(GetCurrentProcess(), target, VOICE_PROBE_PATCH_LEN);
                InterlockedExchange(&g_voice_probe_hook_state.installed, 1);
                append_voice_probe_literal("voice_probe: hook installed wrapper_185EF8B90\r\n");
            }
        }
    }
    install_voice_probe_site(&g_voice_stage_upper_a, (void *)voice_stage_upper_a_hook);
    install_voice_probe_site(&g_voice_stage_upper_b, (void *)voice_stage_upper_b_hook);
    install_voice_probe_site(&g_voice_stage_buffer, (void *)voice_stage_buffer_hook);
    install_voice_probe_site(&g_voice_stage_send_final, (void *)voice_stage_send_final_hook);
    install_voice_probe_site(&g_voice_cached_flush, (void *)voice_cached_flush_hook);
    install_voice_probe_site(&g_voice_holder_flush, (void *)voice_holder_flush_hook);
    install_voice_probe_site(&g_voice_holder_init, (void *)voice_holder_init_hook);
    install_voice_probe_site(&g_voice_controller_bind, (void *)voice_controller_bind_hook);
    install_voice_probe_site(&g_voice_target_set, (void *)voice_target_set_hook);
    install_voice_probe_site(&g_voice_state_rebuild, (void *)voice_state_rebuild_hook);
    install_voice_probe_site(&g_voice_holder_dtor, (void *)voice_holder_dtor_hook);
    install_voice_probe_site(&g_voice_submit_18160DCD0, (void *)voice_submit_18160DCD0_hook);
    install_voice_probe_site(&g_source_send, (void *)source_send_hook);
    install_voice_probe_site(&g_source_send_precheck, (void *)source_send_precheck_hook);
    install_voice_probe_site(&g_source_route_container_build, (void *)source_route_container_build_hook);
    install_voice_probe_site(&g_source_dispatch_core, (void *)source_dispatch_core_hook);
    install_voice_probe_site(&g_source_wxwork_pack_a, (void *)source_wxwork_pack_a_hook);
    install_voice_probe_site(&g_source_wxwork_pack_b, (void *)source_wxwork_pack_b_hook);
    install_voice_probe_site(&g_source_route_key_lookup, (void *)source_route_key_lookup_hook);
    install_voice_probe_site(&g_source_batch_adapter, (void *)source_batch_adapter_hook);
    install_voice_probe_site(&g_wxwork_forwardmsg_url, (void *)wxwork_forwardmsg_url_hook);
    install_voice_probe_site(&g_source_extra_apply, (void *)source_extra_apply_hook);
    install_voice_probe_site(&g_source_inject_convert, (void *)source_inject_convert_hook);
    install_voice_probe_site(&g_source_dispatch_wrap, (void *)source_dispatch_wrap_hook);
}

void uninstall_voice_probe_hook(void) {
    BYTE *target;
    DWORD old = 0;
    uninstall_voice_probe_site(&g_source_dispatch_wrap);
    uninstall_voice_probe_site(&g_source_inject_convert);
    uninstall_voice_probe_site(&g_source_extra_apply);
    uninstall_voice_probe_site(&g_wxwork_forwardmsg_url);
    uninstall_voice_probe_site(&g_source_batch_adapter);
    uninstall_voice_probe_site(&g_source_route_key_lookup);
    uninstall_voice_probe_site(&g_source_wxwork_pack_b);
    uninstall_voice_probe_site(&g_source_wxwork_pack_a);
    uninstall_voice_probe_site(&g_source_dispatch_core);
    uninstall_voice_probe_site(&g_source_route_container_build);
    uninstall_voice_probe_site(&g_source_send_precheck);
    uninstall_voice_probe_site(&g_source_send);
    uninstall_voice_probe_site(&g_source_task_build_generic);
    uninstall_voice_probe_site(&g_voice_submit_18160DCD0);
    uninstall_voice_probe_site(&g_voice_holder_dtor);
    uninstall_voice_probe_site(&g_voice_state_rebuild);
    uninstall_voice_probe_site(&g_voice_target_set);
    uninstall_voice_probe_site(&g_voice_controller_bind);
    uninstall_voice_probe_site(&g_voice_holder_init);
    uninstall_voice_probe_site(&g_voice_holder_flush);
    uninstall_voice_probe_site(&g_voice_cached_flush);
    uninstall_voice_probe_site(&g_voice_stage_send_final);
    uninstall_voice_probe_site(&g_voice_stage_buffer);
    uninstall_voice_probe_site(&g_voice_stage_upper_b);
    uninstall_voice_probe_site(&g_voice_stage_upper_a);
    if (g_voice_probe_hook_state.installed && wxb_weixin_module()) {
        target = (BYTE *)((int64_t)wxb_weixin_module() + WX_OFF_SOURCE_VOICE_SEND_WRAP);
        VirtualProtect(target, VOICE_PROBE_PATCH_LEN, PAGE_EXECUTE_READWRITE, &old);
        copy_bytes(target, g_voice_probe_hook_state.orig, VOICE_PROBE_PATCH_LEN);
        VirtualProtect(target, VOICE_PROBE_PATCH_LEN, old, &old);
        FlushInstructionCache(GetCurrentProcess(), target, VOICE_PROBE_PATCH_LEN);
        InterlockedExchange(&g_voice_probe_hook_state.installed, 0);
        if (g_voice_probe_hook_state.trampoline) {
            VirtualFree(g_voice_probe_hook_state.trampoline, 0, MEM_RELEASE);
            g_voice_probe_hook_state.trampoline = NULL;
        }
        append_voice_probe_literal("voice_probe: hook uninstalled wrapper_185EF8B90\r\n");
    }
}

void refresh_voice_probe_hook(void) {
    if (!file_exists(VOICE_PROBE_FINAL_REPLAY_ONCE_FILE)) {
        InterlockedExchange(&g_voice_final_replay_once_fired, 0);
    }
    if (file_exists(VOICE_SOURCE_BUILDER_SHIM_FILE) || file_exists(VOICE_PROBE_ENABLE_FILE)) {
        install_voice_probe_site(&g_source_task_build_generic, (void *)source_task_build_generic_hook);
    } else {
        if (InterlockedCompareExchange(&g_source_task_build_generic.installed, 0, 0) != 0) {
            uninstall_voice_probe_site(&g_source_task_build_generic);
        } else {
            restore_voice_site_stale_if_present(&g_source_task_build_generic);
        }
    }
    if (file_exists(VOICE_PROBE_ENABLE_FILE)) {
        install_voice_probe_hook();
    } else {
        if (g_voice_probe_hook_state.installed) {
            uninstall_voice_probe_hook();
        } else {
            restore_voice_probe_stale_if_present();
            restore_voice_site_stale_if_present(&g_voice_stage_send_final);
            restore_voice_site_stale_if_present(&g_voice_stage_buffer);
            restore_voice_site_stale_if_present(&g_voice_stage_upper_b);
            restore_voice_site_stale_if_present(&g_voice_stage_upper_a);
            restore_voice_site_stale_if_present(&g_voice_cached_flush);
            restore_voice_site_stale_if_present(&g_voice_holder_flush);
            restore_voice_site_stale_if_present(&g_voice_holder_init);
            restore_voice_site_stale_if_present(&g_voice_controller_bind);
            restore_voice_site_stale_if_present(&g_voice_target_set);
            restore_voice_site_stale_if_present(&g_voice_state_rebuild);
            restore_voice_site_stale_if_present(&g_voice_holder_dtor);
            restore_voice_site_stale_if_present(&g_voice_submit_18160DCD0);
            restore_voice_site_stale_if_present(&g_source_send);
            restore_voice_site_stale_if_present(&g_source_send_precheck);
            restore_voice_site_stale_if_present(&g_source_route_container_build);
            restore_voice_site_stale_if_present(&g_source_dispatch_core);
            restore_voice_site_stale_if_present(&g_source_wxwork_pack_a);
            restore_voice_site_stale_if_present(&g_source_wxwork_pack_b);
            restore_voice_site_stale_if_present(&g_source_route_key_lookup);
            restore_voice_site_stale_if_present(&g_source_batch_adapter);
            restore_voice_site_stale_if_present(&g_wxwork_forwardmsg_url);
            restore_voice_site_stale_if_present(&g_source_extra_apply);
            restore_voice_site_stale_if_present(&g_source_inject_convert);
            restore_voice_site_stale_if_present(&g_source_dispatch_wrap);
        }
    }
}

int voice_probe_hook_installed(void) {
    return InterlockedCompareExchange(&g_voice_probe_hook_state.installed, 0, 0) != 0 ||
           InterlockedCompareExchange(&g_voice_stage_send_final.installed, 0, 0) != 0 ||
           InterlockedCompareExchange(&g_voice_stage_buffer.installed, 0, 0) != 0 ||
           InterlockedCompareExchange(&g_voice_stage_upper_a.installed, 0, 0) != 0 ||
           InterlockedCompareExchange(&g_voice_stage_upper_b.installed, 0, 0) != 0 ||
           InterlockedCompareExchange(&g_voice_cached_flush.installed, 0, 0) != 0 ||
           InterlockedCompareExchange(&g_voice_holder_flush.installed, 0, 0) != 0 ||
           InterlockedCompareExchange(&g_voice_holder_init.installed, 0, 0) != 0 ||
           InterlockedCompareExchange(&g_voice_controller_bind.installed, 0, 0) != 0 ||
           InterlockedCompareExchange(&g_voice_target_set.installed, 0, 0) != 0 ||
           InterlockedCompareExchange(&g_voice_state_rebuild.installed, 0, 0) != 0 ||
           InterlockedCompareExchange(&g_voice_holder_dtor.installed, 0, 0) != 0 ||
           InterlockedCompareExchange(&g_source_task_build_generic.installed, 0, 0) != 0 ||
           InterlockedCompareExchange(&g_source_send.installed, 0, 0) != 0 ||
           InterlockedCompareExchange(&g_source_send_precheck.installed, 0, 0) != 0 ||
           InterlockedCompareExchange(&g_source_route_container_build.installed, 0, 0) != 0 ||
           InterlockedCompareExchange(&g_source_dispatch_core.installed, 0, 0) != 0 ||
           InterlockedCompareExchange(&g_source_wxwork_pack_a.installed, 0, 0) != 0 ||
           InterlockedCompareExchange(&g_source_wxwork_pack_b.installed, 0, 0) != 0 ||
           InterlockedCompareExchange(&g_source_route_key_lookup.installed, 0, 0) != 0 ||
           InterlockedCompareExchange(&g_source_batch_adapter.installed, 0, 0) != 0 ||
           InterlockedCompareExchange(&g_wxwork_forwardmsg_url.installed, 0, 0) != 0 ||
           InterlockedCompareExchange(&g_source_extra_apply.installed, 0, 0) != 0 ||
           InterlockedCompareExchange(&g_source_inject_convert.installed, 0, 0) != 0 ||
           InterlockedCompareExchange(&g_source_dispatch_wrap.installed, 0, 0) != 0 ||
           InterlockedCompareExchange(&g_voice_submit_18160DCD0.installed, 0, 0) != 0;
}

static int is_mostly_printable(const char *s, int n) {
    int printable = 0;
    if (!s || n <= 0) return 0;
    for (int i = 0; i < n; i++) {
        unsigned char c = (unsigned char)s[i];
        if ((c >= 0x20 && c < 0x7F) || c == '\r' || c == '\n' || c == '\t') printable++;
    }
    return printable * 4 >= n * 3;
}

void voice_probe_log_msg_model(int64_t msg_base, const char *msg_id,
                               int msg_type, int msg_subtype, int is_outgoing) {
    char line[1536];
    int emitted = 0;
    if (!file_exists(VOICE_PROBE_ENABLE_FILE)) return;
    if (!msg_base || msg_type != 34) return;

    wsprintfA(line,
              "voice_probe_msg_model: id=%s type=%d/%d outgoing=%d msg_base=%I64x\r\n",
              msg_id ? msg_id : "",
              msg_type,
              msg_subtype,
              is_outgoing,
              (unsigned __int64)msg_base);
    append_file_bytes(VOICE_PROBE_LOG, line, (DWORD)lstrlenA(line));

    for (int off = 0; off <= 0x360 && emitted < 48; off += 8) {
        int64_t hdr = msg_base + off;
        int64_t len = 0;
        int64_t cap = 0;
        int64_t ptr = std_string_data_ptr(hdr, &len, &cap);
        char preview[256] = {0};
        char hex[256] = {0};
        int got;
        if (!ptr || len <= 0 || len > 8192 || cap < 0 || cap > 0x200000) continue;
        got = wx_read_sso_str(hdr, preview, sizeof(preview));
        if (got <= 0) continue;
        if (!is_mostly_printable(preview, got < 128 ? got : 128)) continue;
        hex_sample_from_ptr(ptr, len, hex, sizeof(hex));
        wsprintfA(line,
                  "voice_probe_msg_model_str: id=%s off=%03X hdr=%I64x len=%I64d cap=%I64d ptr=%I64x text='%.180s' hex64=%s\r\n",
                  msg_id ? msg_id : "",
                  off,
                  (unsigned __int64)hdr,
                  (int64_t)len,
                  (int64_t)cap,
                  (unsigned __int64)ptr,
                  preview,
                  hex);
        append_file_bytes(VOICE_PROBE_LOG, line, (DWORD)lstrlenA(line));
        emitted++;
    }
}
