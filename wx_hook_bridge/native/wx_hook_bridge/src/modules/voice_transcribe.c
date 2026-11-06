/*
 * Module: VoiceTrans retained-model diagnostics.
 */

#include "../include/wx_bridge_voice_transcribe.h"
#include "../include/wx_bridge_file_io.h"
#include "../include/wx_bridge_forward_retained.h"
#include "../include/wx_bridge_memory.h"
#include "../include/wx_bridge_native_call.h"
#include "../include/wx_bridge_offsets.h"
#include "../include/wx_bridge_paths.h"
#include "../include/wx_bridge_runtime.h"
#include "../include/wx_bridge_send_runtime.h"
#include "../include/wx_bridge_text.h"
#include "../include/wx_bridge_wechat_string.h"

typedef void *(__fastcall *FnVoiceTransSubmit)(int64_t voice_ext,
                                               void *out_obj,
                                               int64_t *msg_pair,
                                               int64_t cb1,
                                               int64_t cb2);
typedef void *(__fastcall *FnVoiceTransMenuSubmit)(int64_t service,
                                                   void *out_obj,
                                                   int64_t *send_item_pair,
                                                   int64_t cb1,
                                                   int64_t cb2);
typedef int64_t (__fastcall *FnVoiceTransReportResult)(int64_t task, unsigned int code, int64_t text);

#define VOICE_TRANS_RESULT_PATCH_LEN 23

static const BYTE WX_ORIG_VOICE_TRANS_REPORT_RESULT_BYTES[VOICE_TRANS_RESULT_PATCH_LEN] = {
    0x55, 0x41, 0x57, 0x41, 0x56, 0x56, 0x57, 0x53,
    0x48, 0x81, 0xEC, 0xC8, 0x02, 0x00, 0x00,
    0x48, 0x8D, 0xAC, 0x24, 0x80, 0x00, 0x00, 0x00
};

typedef struct VoiceTransResultHookState {
    BYTE orig[VOICE_TRANS_RESULT_PATCH_LEN];
    void *trampoline;
    volatile LONG installed;
    volatile LONG hit_count;
} VoiceTransResultHookState;

static VoiceTransResultHookState g_vt_result_hook = {0};

static void vt_release_shared_pair(int64_t *pair) {
    int64_t ctl;
    int64_t vt;
    if (!pair || !pair[1]) return;
    ctl = pair[1];
    __try {
        vt = r64((void *)ctl);
        if (!InterlockedDecrement((volatile LONG *)(ctl + 8))) {
            ((void (__fastcall *)(int64_t))r64((void *)vt))(ctl);
            if (!InterlockedDecrement((volatile LONG *)(ctl + 12))) {
                ((void (__fastcall *)(int64_t))r64((void *)(vt + 8)))(ctl);
            }
        }
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
    }
    pair[0] = 0;
    pair[1] = 0;
}

static void vt_result_log(int64_t task, unsigned int code, int64_t text_obj, const char *text) {
    WCHAR path[1024];
    char json[4096];
    char num[64];
    char voice_id[512] = {0};
    char *p = json;
    char *end = json + sizeof(json) - 4;
    wsprintfW(path, L"%s", VOICE_TRANS_RESULT_LOG);
    __try {
        if (task) wx_read_sso_str(task + 32, voice_id, sizeof(voice_id));
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
        voice_id[0] = 0;
    }
    append_raw(&p, end, "{\"event\":\"voice_trans_result\"");
    append_raw(&p, end, ",\"task\":\"");
    wsprintfA(num, "%I64x", task);
    append_raw(&p, end, num);
    append_raw(&p, end, "\",\"code\":");
    wsprintfA(num, "%u", code);
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"text_obj\":\"");
    wsprintfA(num, "%I64x", text_obj);
    append_raw(&p, end, num);
    append_raw(&p, end, "\",\"voice_id\":\"");
    append_escaped(&p, end, voice_id);
    append_raw(&p, end, "\",\"text\":\"");
    append_escaped(&p, end, text ? text : "");
    append_raw(&p, end, "\"}\r\n");
    *p = 0;
    append_file_bytes(path, json, (DWORD)(p - json));
}

static int build_vt_result_trampoline(BYTE *p, BYTE *target) {
    BYTE *start = p;
    copy_bytes(p, g_vt_result_hook.orig, VOICE_TRANS_RESULT_PATCH_LEN);
    p += VOICE_TRANS_RESULT_PATCH_LEN;
    *p++ = 0x48; *p++ = 0xB8;
    *(int64_t *)p = (int64_t)(target + VOICE_TRANS_RESULT_PATCH_LEN); p += 8;
    *p++ = 0xFF; *p++ = 0xE0;
    return (int)(p - start);
}

static int64_t __fastcall voice_trans_result_hook(int64_t task, unsigned int code, int64_t text_obj) {
    FnVoiceTransReportResult original = (FnVoiceTransReportResult)g_vt_result_hook.trampoline;
    char text[2048] = {0};
    InterlockedIncrement(&g_vt_result_hook.hit_count);
    __try {
        if (text_obj) wx_read_sso_str(text_obj, text, sizeof(text));
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
        text[0] = 0;
    }
    if (code == 7 || code == 5 || text[0]) {
        vt_result_log(task, code, text_obj, text);
    }
    return original ? original(task, code, text_obj) : 0;
}

int voice_trans_result_hook_installed(void) {
    return InterlockedCompareExchange(&g_vt_result_hook.installed, 0, 0) != 0;
}

void install_voice_trans_result_hook(void) {
    BYTE *target;
    BYTE patch[VOICE_TRANS_RESULT_PATCH_LEN];
    DWORD old = 0;
    int tlen;
    if (!wxb_weixin_module() || voice_trans_result_hook_installed()) return;
    target = (BYTE *)((int64_t)wxb_weixin_module() + WX_OFF_VOICE_TRANS_REPORT_RESULT);
    if (target[0] == 0x48 && target[1] == 0xB8) {
        VirtualProtect(target, VOICE_TRANS_RESULT_PATCH_LEN, PAGE_EXECUTE_READWRITE, &old);
        copy_bytes(target, WX_ORIG_VOICE_TRANS_REPORT_RESULT_BYTES, VOICE_TRANS_RESULT_PATCH_LEN);
        VirtualProtect(target, VOICE_TRANS_RESULT_PATCH_LEN, old, &old);
        FlushInstructionCache(GetCurrentProcess(), target, VOICE_TRANS_RESULT_PATCH_LEN);
    }
    if (!bytes_equal(target, WX_ORIG_VOICE_TRANS_REPORT_RESULT_BYTES, VOICE_TRANS_RESULT_PATCH_LEN)) {
        vt_result_log(0, 0xFFFFFFFFu, 0, "install skipped: report-result prologue mismatch");
        return;
    }
    copy_bytes(g_vt_result_hook.orig, target, VOICE_TRANS_RESULT_PATCH_LEN);
    g_vt_result_hook.trampoline = VirtualAlloc(NULL, 128, MEM_COMMIT | MEM_RESERVE, PAGE_EXECUTE_READWRITE);
    if (!g_vt_result_hook.trampoline) {
        vt_result_log(0, 0xFFFFFFFFu, 0, "install failed: trampoline alloc");
        return;
    }
    tlen = build_vt_result_trampoline((BYTE *)g_vt_result_hook.trampoline, target);
    FlushInstructionCache(GetCurrentProcess(), g_vt_result_hook.trampoline, tlen);
    for (int i = 0; i < VOICE_TRANS_RESULT_PATCH_LEN; i++) patch[i] = 0x90;
    patch[0] = 0x48; patch[1] = 0xB8;
    *(int64_t *)(patch + 2) = (int64_t)voice_trans_result_hook;
    patch[10] = 0xFF; patch[11] = 0xE0;
    VirtualProtect(target, VOICE_TRANS_RESULT_PATCH_LEN, PAGE_EXECUTE_READWRITE, &old);
    copy_bytes(target, patch, VOICE_TRANS_RESULT_PATCH_LEN);
    VirtualProtect(target, VOICE_TRANS_RESULT_PATCH_LEN, old, &old);
    FlushInstructionCache(GetCurrentProcess(), target, VOICE_TRANS_RESULT_PATCH_LEN);
    InterlockedExchange(&g_vt_result_hook.installed, 1);
    vt_result_log(0, 0, 0, "hook installed");
}

void uninstall_voice_trans_result_hook(void) {
    BYTE *target;
    DWORD old = 0;
    if (!wxb_weixin_module() || !voice_trans_result_hook_installed()) return;
    target = (BYTE *)((int64_t)wxb_weixin_module() + WX_OFF_VOICE_TRANS_REPORT_RESULT);
    VirtualProtect(target, VOICE_TRANS_RESULT_PATCH_LEN, PAGE_EXECUTE_READWRITE, &old);
    copy_bytes(target, g_vt_result_hook.orig, VOICE_TRANS_RESULT_PATCH_LEN);
    VirtualProtect(target, VOICE_TRANS_RESULT_PATCH_LEN, old, &old);
    FlushInstructionCache(GetCurrentProcess(), target, VOICE_TRANS_RESULT_PATCH_LEN);
    InterlockedExchange(&g_vt_result_hook.installed, 0);
    if (g_vt_result_hook.trampoline) {
        VirtualFree(g_vt_result_hook.trampoline, 0, MEM_RELEASE);
        g_vt_result_hook.trampoline = NULL;
    }
    vt_result_log(0, 0, 0, "hook uninstalled");
}

static void vt_append_hex_field(char **p, char *end, const char *name, int64_t value) {
    char num[64];
    append_raw(p, end, ",\"");
    append_raw(p, end, name);
    append_raw(p, end, "\":\"");
    wsprintfA(num, "%I64x", value);
    append_raw(p, end, num);
    append_raw(p, end, "\"");
}

static void vt_append_dec_field(char **p, char *end, const char *name, int64_t value) {
    char num[64];
    append_raw(p, end, ",\"");
    append_raw(p, end, name);
    append_raw(p, end, "\":");
    wsprintfA(num, "%I64d", value);
    append_raw(p, end, num);
}

static void vt_append_bool_field(char **p, char *end, const char *name, int value) {
    append_raw(p, end, ",\"");
    append_raw(p, end, name);
    append_raw(p, end, "\":");
    append_raw(p, end, value ? "true" : "false");
}

static void vt_write_result(const char *seq, int ok, const char *message,
                            int64_t requested_msgid, int32_t requested_local_id,
                            int64_t resolved_msgid, int32_t resolved_local_id,
                            int model_found, int model_vt_ok,
                            int32_t msg_type, int32_t msg_subtype,
                            int64_t model_ptr, int64_t model_vt,
                            int64_t voice_ext_source, int64_t voice_ext,
                            int from_persisted,
                            int active_submit, int submit_called,
                            int submit_ok, unsigned long seh_code,
                            int64_t arena_ptr, int64_t model_copy_ptr,
                            int64_t ctl_ptr, int64_t pair_ptr,
                            int64_t out_ptr, int64_t submit_ret) {
    if (!seq || !*seq) return;
    WCHAR path[1024];
    char json[4096];
    char *p = json;
    char *end = json + sizeof(json) - 1;
    wsprintfW(path, L"%s\\result_%S.json", CONTROL_DIR, seq);
    append_raw(&p, end, "{\"ok\":");
    append_raw(&p, end, ok ? "true" : "false");
    append_raw(&p, end, ",\"seq\":\"");
    append_escaped(&p, end, seq);
    append_raw(&p, end, "\",\"command\":\"voice_transcribe\",\"message\":\"");
    append_escaped(&p, end, message ? message : "");
    append_raw(&p, end, "\"");
    vt_append_dec_field(&p, end, "requested_msgid", requested_msgid);
    vt_append_dec_field(&p, end, "requested_local_id", requested_local_id);
    vt_append_dec_field(&p, end, "msgid", resolved_msgid);
    vt_append_dec_field(&p, end, "local_id", resolved_local_id);
    vt_append_bool_field(&p, end, "model_found", model_found);
    vt_append_bool_field(&p, end, "model_vt_ok", model_vt_ok);
    vt_append_dec_field(&p, end, "msg_type", msg_type);
    vt_append_dec_field(&p, end, "msg_subtype", msg_subtype);
    vt_append_hex_field(&p, end, "model", model_ptr);
    vt_append_hex_field(&p, end, "model_vt", model_vt);
    vt_append_hex_field(&p, end, "voice_ext_source", voice_ext_source);
    vt_append_hex_field(&p, end, "voice_ext", voice_ext);
    vt_append_bool_field(&p, end, "from_persisted_ptr", from_persisted);
    vt_append_bool_field(&p, end, "active_submit", active_submit);
    vt_append_bool_field(&p, end, "submit_called", submit_called);
    vt_append_bool_field(&p, end, "submit_ok", submit_ok);
    vt_append_hex_field(&p, end, "seh_code", (int64_t)seh_code);
    vt_append_hex_field(&p, end, "arena", arena_ptr);
    vt_append_hex_field(&p, end, "model_copy", model_copy_ptr);
    vt_append_hex_field(&p, end, "msg_ctl", ctl_ptr);
    vt_append_hex_field(&p, end, "msg_pair", pair_ptr);
    vt_append_hex_field(&p, end, "out_obj", out_ptr);
    vt_append_hex_field(&p, end, "submit_ret", submit_ret);
    append_raw(&p, end, active_submit
        ? ",\"path\":\"retained_send_item_voice_trans_menu_wrapper\""
        : ",\"path\":\"retained_model_voice_trans_diagnostic\"");
    if (!active_submit) {
        append_raw(&p, end, ",\"next\":\"rerun with --submit to call 1816C6390 official VoiceTrans menu wrapper\"");
    }
    append_raw(&p, end, "}");
    *p = 0;
    write_file_bytes(path, json, (DWORD)(p - json));
}

int voice_transcribe_to_result(const char *seq, int64_t msgid, int32_t local_id, int active_submit) {
    int64_t base = (int64_t)wxb_weixin_module();
    void *model = NULL;
    int from_persisted = 0;
    int64_t model_ptr = 0;
    int64_t model_vt = 0;
    int64_t resolved_msgid = 0;
    int32_t resolved_local_id = 0;
    int32_t msg_type = 0;
    int32_t msg_subtype = 0;
    int model_vt_ok = 0;
    int64_t voice_ext_source = 0;
    int64_t voice_ext = 0;
    FnRTDynamicCast cast_fn = NULL;
    int submit_called = 0;
    int submit_ok = 0;
    unsigned long submit_seh = 0;
    int64_t arena_ptr = 0;
    int64_t model_copy_ptr = 0;
    int64_t ctl_ptr = 0;
    int64_t pair_ptr = 0;
    int64_t out_ptr = 0;
    int64_t submit_ret = 0;

    if (!msgid && !local_id) {
        vt_write_result(seq, 0, "missing msgid/local_id",
                        msgid, local_id, 0, 0, 0, 0, 0, 0,
                        0, 0, 0, 0, 0,
                        active_submit, 0, 0, 0, 0, 0, 0, 0, 0, 0);
        return 0;
    }

    if (active_submit && local_id) {
        model = find_retained_model_by_local_id(local_id);
        from_persisted = 0;
    }
    if (!model && msgid) {
        int in_memory = retained_model_in_memory(msgid);
        if (!active_submit || in_memory) {
            model = find_retained_model(msgid);
            from_persisted = (model && !in_memory) ? 1 : 0;
        }
    }
    if (!model && local_id) {
        model = find_retained_model_by_local_id(local_id);
        from_persisted = 0;
    }

    if (!model) {
        vt_write_result(seq, 0, active_submit
                        ? "live retained voice model not found; active submit refuses persisted/stale model"
                        : "retained voice model not found",
                        msgid, local_id, 0, 0, 0, 0, 0, 0,
                        0, 0, 0, 0, 0,
                        active_submit, 0, 0, 0, 0, 0, 0, 0, 0, 0);
        return 0;
    }

    if (active_submit && from_persisted) {
        vt_write_result(seq, 0, "active submit refused persisted/stale retained model",
                        msgid, local_id, 0, 0, 1, 0, 0, 0,
                        (int64_t)model, 0, 0, 0, from_persisted,
                        active_submit, 0, 0, 0, 0, 0, 0, 0, 0, 0);
        return 0;
    }

    model_ptr = (int64_t)model;
    if (!is_readable_ptr(model_ptr, 0x238)) {
        vt_write_result(seq, 0, "retained model pointer is not readable",
                        msgid, local_id, 0, 0, 1, 0, 0, 0,
                        model_ptr, 0, 0, 0, from_persisted,
                        active_submit, 0, 0, 0, 0, 0, 0, 0, 0, 0);
        return 0;
    }

    __try {
        model_vt = r64(model);
        resolved_msgid = r64((void *)(model_ptr + WX_MSG_ID_OFF));
        resolved_local_id = r32((void *)(model_ptr + WX_MSG_LOCAL_ID_OFF));
        msg_type = r32((void *)(model_ptr + 0x0C));
        msg_subtype = r32((void *)(model_ptr + WX_MSG_SUBTYPE_OFF));
        voice_ext_source = r64((void *)(model_ptr + 0x230));
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
        vt_write_result(seq, 0, "exception while reading retained model",
                        msgid, local_id, 0, 0, 1, 0, 0, 0,
                        model_ptr, model_vt, 0, 0, from_persisted,
                        active_submit, 0, 0, GetExceptionCode(), 0, 0, 0, 0, 0, 0);
        return 0;
    }

    model_vt_ok = (base && model_vt == base + WX_OFF_SRC_MSG_VT) ? 1 : 0;
    if (!model_vt_ok) {
        vt_write_result(seq, 0, "retained model vtable mismatch",
                        msgid, local_id, resolved_msgid, resolved_local_id,
                        1, 0, msg_type, msg_subtype,
                        model_ptr, model_vt, voice_ext_source, 0, from_persisted,
                        active_submit, 0, 0, 0, 0, 0, 0, 0, 0, 0);
        return 0;
    }

    if (msg_type != 34) {
        vt_write_result(seq, 0, "retained model is not a voice message",
                        msgid, local_id, resolved_msgid, resolved_local_id,
                        1, 1, msg_type, msg_subtype,
                        model_ptr, model_vt, voice_ext_source, 0, from_persisted,
                        active_submit, 0, 0, 0, 0, 0, 0, 0, 0, 0);
        return 0;
    }

    if (base && voice_ext_source) {
        cast_fn = (FnRTDynamicCast)(base + WX_OFF_RT_DYNAMIC_CAST);
        __try {
            voice_ext = cast_fn(
                voice_ext_source,
                0,
                base + WX_OFF_VOICE_EXT_RTTI_SRC,
                base + WX_OFF_VOICE_EXT_RTTI_DST,
                0);
        }
        __except (EXCEPTION_EXECUTE_HANDLER) {
            voice_ext = 0;
        }
    }

    if (!voice_ext && !active_submit) {
        vt_write_result(seq, 0, "voice extension dynamic_cast failed",
                        msgid, local_id, resolved_msgid, resolved_local_id,
                        1, 1, msg_type, msg_subtype,
                        model_ptr, model_vt, voice_ext_source, 0, from_persisted,
                        active_submit, 0, 0, 0, 0, 0, 0, 0, 0, 0);
        return 0;
    }

    if (active_submit) {
        BYTE *arena = NULL;
        int64_t root_pair[2] = {0, 0};
        int64_t mid_pair[2] = {0, 0};
        int64_t service_pair[2] = {0, 0};
        int64_t send_item_pair[2] = {0, 0};
        BYTE *out_obj = NULL;
        BYTE *cb1 = NULL;
        BYTE *cb2 = NULL;
        void *send_item = NULL;
        int send_item_origin = 0;

        arena = (BYTE *)VirtualAlloc(NULL, 0x4000, MEM_COMMIT | MEM_RESERVE, PAGE_EXECUTE_READWRITE);
        if (!arena) {
            vt_write_result(seq, 0, "VirtualAlloc failed before submit",
                            msgid, local_id, resolved_msgid, resolved_local_id,
                            1, 1, msg_type, msg_subtype,
                            model_ptr, model_vt, voice_ext_source, voice_ext, from_persisted,
                            active_submit, 0, 0, 0, 0, 0, 0, 0, 0, 0);
            return 0;
        }
        ZeroMemory(arena, 0x4000);
        arena_ptr = (int64_t)arena;
        out_obj = arena + 0x000;
        cb1 = arena + 0x100;
        cb2 = arena + 0x180;
        out_ptr = (int64_t)out_obj;

        __try {
            send_item = find_retained_si(resolved_msgid);
            if (!send_item && model_ptr) {
                retain_send_item(resolved_msgid, model_ptr);
                send_item = find_retained_si(resolved_msgid);
            }
            send_item_origin = retained_si_origin(resolved_msgid);
            model_copy_ptr = (int64_t)send_item;
            ctl_ptr = send_item_origin;

            if (!send_item || !is_readable_ptr((int64_t)send_item, 904)) {
                submit_seh = 0xBAD10001;
            } else if (r64(send_item) != base + WX_OFF_SEND_ITEM_VT) {
                submit_seh = 0xBAD10002;
            } else {
                send_item_pair[0] = (int64_t)send_item;
                send_item_pair[1] = 0;
                pair_ptr = (int64_t)send_item_pair;

                ((FnCall1)(base + WX_OFF_ROOM_INIT1))((int64_t)root_pair);
                if (!root_pair[0]) {
                    submit_seh = 0xBAD20001;
                } else {
                    ((FnCall3)(base + WX_OFF_ROOM_INIT2))(root_pair[0], (int64_t)mid_pair);
                    if (!mid_pair[0]) {
                        submit_seh = 0xBAD20002;
                    } else {
                        ((FnCall3)(base + WX_OFF_SEND_TEXT_SERVICE))(mid_pair[0], (int64_t)service_pair);
                        if (!service_pair[0]) {
                            submit_seh = 0xBAD20003;
                        } else {
                            install_voice_trans_result_hook();
                            submit_called = 1;
                            submit_ret = (int64_t)((FnVoiceTransMenuSubmit)(base + WX_OFF_VOICE_TRANS_MENU_SUBMIT_WRAP))(
                                service_pair[0],
                                out_obj,
                                send_item_pair,
                                (int64_t)cb1,
                                (int64_t)cb2);
                            submit_ok = 1;
                        }
                    }
                }
            }
        }
        __except (EXCEPTION_EXECUTE_HANDLER) {
            submit_seh = GetExceptionCode();
            submit_ok = 0;
        }

        vt_release_shared_pair(service_pair);
        vt_release_shared_pair(mid_pair);
        vt_release_shared_pair(root_pair);
        retain_arena(arena);
        vt_write_result(seq, submit_ok ? 1 : 0,
                        submit_ok ? "official VoiceTrans menu wrapper called" : "official VoiceTrans menu wrapper failed/exception",
                        msgid, local_id, resolved_msgid, resolved_local_id,
                        1, 1, msg_type, msg_subtype,
                        model_ptr, model_vt, voice_ext_source, voice_ext, from_persisted,
                        active_submit, submit_called, submit_ok, submit_seh,
                        arena_ptr, model_copy_ptr, ctl_ptr, pair_ptr, out_ptr, submit_ret);
        return submit_ok ? 1 : 0;
    }

    vt_write_result(seq, 1, "retained voice model resolved; active submit gated",
                    msgid, local_id, resolved_msgid, resolved_local_id,
                    1, 1, msg_type, msg_subtype,
                    model_ptr, model_vt, voice_ext_source, voice_ext, from_persisted,
                    active_submit, submit_called, submit_ok, submit_seh,
                    arena_ptr, model_copy_ptr, ctl_ptr, pair_ptr, out_ptr, submit_ret);
    return 1;
}
