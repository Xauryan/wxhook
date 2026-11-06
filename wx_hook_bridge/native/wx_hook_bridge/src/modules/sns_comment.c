/*
 * Module: SNS comment.
 *
 * Static basis, WeChat 4.1.9.23:
 *   sub_184CA29F0 builds micromsg.SnsCommentRequest and calls
 *   sub_182BCF740 for /cgi-bin/micromsg-bin/mmsnscomment, cgi type 213.
 *   Ordinary text comments use SnsCommentContent.CommentType = 2.
 */

#include "../include/wx_bridge_sns_comment.h"
#include "../include/wx_bridge_file_io.h"
#include "../include/wx_bridge_memory.h"
#include "../include/wx_bridge_native_call.h"
#include "../include/wx_bridge_offsets.h"
#include "../include/wx_bridge_paths.h"
#include "../include/wx_bridge_protocol.h"
#include "../include/wx_bridge_runtime.h"
#include "../include/wx_bridge_send_runtime.h"
#include "../include/wx_bridge_text.h"
#include "../include/wx_bridge_wechat_string.h"
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#define SNS_COMMENT_REQ_SIZE 40
#define SNS_COMMENT_CONTENT_REQ_SIZE 64
#define SNS_COMMENT_CONTENT_SIZE 184
#define SNS_COMMENT_OUT_SIZE 64
#define SNS_COMMENT_EXTRA_SIZE 32
#define SNS_COMMENT_OPTS_SIZE 48
#define SNS_COMMENT_TYPE_UNLIKE 1
#define SNS_COMMENT_TYPE_TEXT 2
#define SNS_COMMENT_TYPE_LIKE 5
#define SNS_COMMENT_NO_REPLY_RESULT_ARGS 0, "", "", 0, 0, 0, 0, 0
#define SNS_LIKE_ENTRY_PATCH_LEN 19
#define SNS_MODEL_LOOKUP_PATCH_LEN 17
#define SNS_MODEL_VALIDATE_PATCH_LEN 14
#define SNS_MODEL_VALIDATE_CAPTURE_SLOTS 64

typedef int64_t (__fastcall *SnsCommentNoArgsFn)(void);
typedef unsigned int *(__fastcall *SnsCommentSendFn)(unsigned int *out,
                                                     int64_t cgi_ctx,
                                                     int64_t req,
                                                     int64_t extra,
                                                     int64_t opts);
typedef void (__fastcall *SnsCommentSharedCtlFn)(int64_t ctl);
typedef int64_t (__fastcall *SnsCommentOutFn)(int64_t out);
typedef int64_t (__fastcall *SnsCommentOutFromFn)(int64_t in, int64_t out);
typedef int64_t (__fastcall *SnsLikeCtxLookupFn)(int64_t manager, int64_t out_ctx, int64_t key);
typedef unsigned __int64 (__fastcall *SnsLikeCtxDtorFn)(int64_t ctx);
typedef unsigned int (__fastcall *SnsLikeEntryFn)(int64_t manager, int64_t key, int64_t ctx);
typedef unsigned int (__fastcall *SnsDeleteEntryFn)(int64_t manager, int64_t *model_sp);
typedef unsigned int (__fastcall *SnsDeleteObjectOpEntryFn)(int64_t manager, int64_t *model_sp);
typedef unsigned int (__fastcall *SnsSetPrivateEntryFn)(int64_t manager, int64_t *model_sp, unsigned int state);
typedef void (__fastcall *SnsSetPrivateUiModelCopyFn)(int64_t dst, int64_t src, int64_t unused);
typedef int64_t (__fastcall *SnsModelLookupFn)(int64_t service, int64_t out_sp, int64_t key);
typedef int64_t (__fastcall *SnsCommentAuxStringFn)(int64_t service,
                                                    int64_t out_str,
                                                    int64_t key,
                                                    unsigned int flag);

typedef struct SnsLikeProbeHook {
    volatile LONG installed;
    volatile LONG reentry;
    volatile LONG capture_count;
    int patch_len;
    BYTE orig[32];
    void *trampoline;
} SnsLikeProbeHook;

static SnsLikeProbeHook g_sns_like_probe_hook = {0};
static SnsLikeProbeHook g_sns_model_lookup_probe_hook = {0};
static SnsLikeProbeHook g_sns_model_validate_capture_hook = {0};

typedef struct SnsValidatedModelCapture {
    unsigned __int64 sns_id;
    uint64_t model;
    uint64_t tick;
} SnsValidatedModelCapture;

static SnsValidatedModelCapture g_sns_validated_model_captures[SNS_MODEL_VALIDATE_CAPTURE_SLOTS];

static const BYTE WX_ORIG_SNS_LIKE_ENTRY_BYTES[SNS_LIKE_ENTRY_PATCH_LEN] = {
    0x55, 0x41, 0x57, 0x41, 0x56, 0x41, 0x55, 0x41,
    0x54, 0x56, 0x57, 0x53, 0x48, 0x81, 0xEC, 0x48,
    0x03, 0x00, 0x00
};

static const BYTE WX_ORIG_SNS_MODEL_LOOKUP_BYTES[SNS_MODEL_LOOKUP_PATCH_LEN] = {
    0x55, 0x41, 0x57, 0x41, 0x56, 0x41, 0x54, 0x56,
    0x57, 0x53, 0x48, 0x81, 0xEC, 0xB0, 0x05, 0x00,
    0x00
};

static const BYTE WX_ORIG_SNS_MODEL_VALIDATE_BYTES[SNS_MODEL_VALIDATE_PATCH_LEN] = {
    0x48, 0x83, 0x39, 0x00, 0x74, 0x66, 0x48, 0x83,
    0xB9, 0xA0, 0x01, 0x00, 0x00, 0x00
};

typedef struct SnsCommentTask {
    volatile LONG status;
    int64_t call_result;
    DWORD wait_status;
    int phase;
    DWORD seh_code;
    uint64_t seh_addr;
    uint64_t seh_fault;
    uint64_t ctx_rcx;
    uint64_t ctx_rdx;
    uint64_t ctx_r8;
    uint64_t ctx_r9;
    unsigned __int64 feed_id;
    int action_type;
    DWORD self_uin;
    int high_path;
    int model_found;
    int model_like_flag;
    int model_source;
    int ctx_found;
    int ctx_key_len;
    int already_liked;
    int entry_skipped;
    int direct_objectop_used;
    int captured_model_used;
    int captured_model_invalid;
    int model_sp_consumed;
    int model_lookup_attempts;
    unsigned int visibility_state;
    int reply_enabled;
    unsigned int reply_comment_id;
    unsigned __int64 reply_comment_64id;
    uint64_t captured_model_ptr;
    uint64_t captured_model_ctl;
    int reply_source;
    int reply_type;
    int reply_create_time;
    unsigned int high_entry_result;
    unsigned int objectop_out_model;
    unsigned int local_update_result;
    char lookup_variant[32];
    char lookup_key[512];
    char sns_key[512];
    char sns_id[64];
    char username[256];
    char content[4096];
    char reply_username[256];
    char reply_nickname[256];
    char client_id[128];
    char self_username[256];
} SnsCommentTask;

static unsigned __int64 sns_comment_parse_id(const char *s) {
    char *endp = 0;
    if (!s) return 0;
    while (*s == ' ' || *s == '\t') s++;
    if ((s[0] == '0') && (s[1] == 'x' || s[1] == 'X')) {
        return _strtoui64(s + 2, &endp, 16);
    }
    return _strtoui64(s, &endp, 10);
}

static void sns_like_join_key(char *out,
                              int max,
                              const char *a,
                              const char *sep,
                              const char *b) {
    int n;
    if (!out || max <= 0) return;
    out[0] = 0;
    safe_copy(out, max, a ? a : "");
    n = lstrlenA(out);
    if (sep && *sep) {
        while (*sep && n < max - 1) out[n++] = *sep++;
        out[n] = 0;
    }
    if (!b) b = "";
    while (*b && n < max - 1) out[n++] = *b++;
    out[n] = 0;
}

static int sns_comment_set_pb_string(int64_t base,
                                     BYTE *obj,
                                     int field_off,
                                     int hasbits_off,
                                     DWORD flag,
                                     const char *value) {
    int64_t str_obj;
    if (!base || !obj) return 0;
    str_obj = r64(obj + field_off);
    if (!str_obj || str_obj == base + WX_OFF_PROTOBUF_EMPTY_STRING) {
        str_obj = ((FnCall1)(base + WX_OFF_WX_ALLOC))(32);
        if (!str_obj) return 0;
        ZeroMemory((void *)str_obj, 32);
        w64((void *)(str_obj + 0x18), 15);
        w64(obj + field_off, str_obj);
    }
    wx_set_std_string_heap(base, str_obj, value ? value : "");
    w32(obj + hasbits_off, r32(obj + hasbits_off) | (int32_t)flag);
    return 1;
}

static void sns_comment_init_std_string(BYTE *s) {
    if (!s) return;
    ZeroMemory(s, 32);
    w64(s + 0x18, 15);
}

static int sns_comment_copy_std_string(int64_t base, int64_t dst, int64_t src) {
    int64_t len64, cap64, src_ptr, dst_ptr;
    int cap;
    if (!base || !dst || !src) return 0;
    len64 = r64((void *)(src + 0x10));
    cap64 = r64((void *)(src + 0x18));
    if (len64 < 0 || len64 > 0x100000 || cap64 < 0) return 0;
    src_ptr = (cap64 >= 0x10) ? r64((void *)src) : src;
    if (!src_ptr && len64) return 0;

    wx_free_std_string_heap(base, dst);
    if (len64 <= 15) {
        ZeroMemory((void *)dst, 16);
        if (len64) copy_bytes((void *)dst, (const void *)src_ptr, (SIZE_T)len64);
        w8((void *)(dst + len64), 0);
        w64((void *)(dst + 0x10), len64);
        w64((void *)(dst + 0x18), 15);
        return 1;
    }

    cap = (int)len64 | 0xF;
    if (cap < 22) cap = 22;
    dst_ptr = ((FnCall1)(base + WX_OFF_WX_ALLOC))(cap + 1);
    if (!dst_ptr) return 0;
    copy_bytes((void *)dst_ptr, (const void *)src_ptr, (SIZE_T)len64);
    w8((void *)(dst_ptr + len64), 0);
    w64((void *)dst, dst_ptr);
    w64((void *)(dst + 0x10), len64);
    w64((void *)(dst + 0x18), cap);
    return 1;
}

static int sns_comment_set_pb_string_from_std(int64_t base,
                                              BYTE *obj,
                                              int field_off,
                                              int hasbits_off,
                                              DWORD flag,
                                              int64_t src_str) {
    int64_t str_obj;
    if (!base || !obj || !src_str) return 0;
    str_obj = r64(obj + field_off);
    if (!str_obj || str_obj == base + WX_OFF_PROTOBUF_EMPTY_STRING) {
        str_obj = ((FnCall1)(base + WX_OFF_WX_ALLOC))(32);
        if (!str_obj) return 0;
        sns_comment_init_std_string((BYTE *)str_obj);
        w64(obj + field_off, str_obj);
    }
    if (!sns_comment_copy_std_string(base, str_obj, src_str)) return 0;
    w32(obj + hasbits_off, r32(obj + hasbits_off) | (int32_t)flag);
    return 1;
}

static int sns_comment_apply_reply_target(int64_t base,
                                          int64_t content_req,
                                          int64_t comment,
                                          const SnsCommentTask *task) {
    int64_t refer;
    if (!base || !content_req || !comment || !task) return 0;
    if (!task->reply_enabled) return 1;

    /*
     * Static evidence:
     * sub_184CA29F0 reads reply_model +0/+8/+0x10/+0x30/+0x70/+0x74/+0x78.
     * sub_183D76F00/sub_183D77D70 map those offsets to
     * comment_id/comment_64id/username/nickname/source/type/create_time.
     */
    w32((void *)(comment + 60), (int32_t)task->reply_comment_id);
    w64((void *)(comment + 72), (int64_t)task->reply_comment_64id);
    w32((void *)(comment + 56), (int32_t)task->reply_create_time);
    w32((void *)(comment + 176), r32((void *)(comment + 176)) | 0x940);

    refer = r64((void *)(content_req + 32));
    if (!refer) {
        refer = ((FnCall1)(base + WX_OFF_WX_ALLOC))(SNS_COMMENT_CONTENT_SIZE);
        if (!refer) return 0;
        ((FnCall1)(base + WX_OFF_SNS_COMMENT_CONTENT_INIT))(refer);
        w64((void *)(content_req + 32), refer);
    }
    w32((void *)(content_req + 56), r32((void *)(content_req + 56)) | 0x08);

    if (!sns_comment_set_pb_string(base, (BYTE *)refer, 8, 176, 0x01, task->reply_username) ||
        !sns_comment_set_pb_string(base, (BYTE *)refer, 24, 176, 0x04, task->reply_nickname)) {
        return 0;
    }
    w32((void *)(refer + 40), (int32_t)task->reply_type);
    w32((void *)(refer + 44), (int32_t)task->reply_source);
    w32((void *)(refer + 176), r32((void *)(refer + 176)) | 0x30);
    return 1;
}

static void sns_comment_release_shared_ctl(int64_t ctl) {
    int64_t vt;
    volatile LONG *refs;
    if (!ctl) return;
    refs = (volatile LONG *)ctl;
    if (!_InterlockedDecrement(refs + 2)) {
        vt = r64((void *)ctl);
        if (vt) {
            ((SnsCommentSharedCtlFn)r64((void *)vt))(ctl);
            if (!_InterlockedDecrement(refs + 3)) {
                ((SnsCommentSharedCtlFn)r64((void *)(vt + 8)))(ctl);
            }
        }
    }
}

static void sns_comment_release_sp(int64_t *sp) {
    if (!sp) return;
    sns_comment_release_shared_ctl(sp[1]);
    sp[0] = 0;
    sp[1] = 0;
}

static int64_t sns_comment_resolve_manager(int64_t base) {
    int64_t root_sp[2] = {0, 0};
    int64_t stage_sp[2] = {0, 0};
    int64_t manager_sp[2] = {0, 0};
    int64_t manager = 0;
    if (!base) return 0;
    ((SnsCommentOutFn)(base + WX_OFF_SNS_MANAGER_ROOT))((int64_t)root_sp);
    if (root_sp[0]) {
        ((SnsCommentOutFromFn)(base + WX_OFF_SNS_MANAGER_STAGE1))(root_sp[0], (int64_t)stage_sp);
    }
    if (stage_sp[0]) {
        ((SnsCommentOutFromFn)(base + WX_OFF_SNS_MANAGER_STAGE2))(stage_sp[0], (int64_t)manager_sp);
        manager = manager_sp[0];
    }
    return manager;
}

static int64_t sns_comment_resolve_model_service(int64_t base) {
    int64_t root_sp[2] = {0, 0};
    int64_t stage_sp[2] = {0, 0};
    int64_t service_sp[2] = {0, 0};
    int64_t service = 0;
    if (!base) return 0;
    ((SnsCommentOutFn)(base + WX_OFF_SNS_MANAGER_ROOT))((int64_t)root_sp);
    if (root_sp[0]) {
        ((SnsCommentOutFromFn)(base + WX_OFF_SNS_MANAGER_STAGE1))(root_sp[0], (int64_t)stage_sp);
    }
    if (stage_sp[0]) {
        ((SnsCommentOutFromFn)(base + WX_OFF_SNS_MODEL_SERVICE))(stage_sp[0], (int64_t)service_sp);
        service = service_sp[0];
    }
    return service;
}

static void sns_comment_cleanup_send_out(int64_t base, BYTE *out) {
    if (!base || !out) return;
    sns_comment_release_shared_ctl(r64(out + 48));
    wx_free_std_string_heap(base, (int64_t)(out + 8));
}

static void sns_comment_get_self_username(int64_t base, char *out, int max) {
    int64_t root;
    int64_t str_obj;
    if (!out || max <= 0) return;
    out[0] = 0;
    if (!base) return;
    __try {
        root = ((SnsCommentNoArgsFn)(base + WX_OFF_FINDER_USERPAGE_CGI_CONTEXT_ROOT))();
        if (root && r64((void *)root)) {
            str_obj = ((FnCall1)r64((void *)(r64((void *)root) + 32)))(root);
            if (str_obj) wx_read_sso_str(str_obj, out, max);
        }
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
        out[0] = 0;
    }
}

static DWORD sns_comment_get_self_uin(int64_t base) {
    int64_t root;
    if (!base) return 0;
    __try {
        root = ((SnsCommentNoArgsFn)(base + WX_OFF_FINDER_USERPAGE_CGI_CONTEXT_ROOT))();
        if (root && r64((void *)root)) {
            return (DWORD)((FnCall1)r64((void *)(r64((void *)root) + 72)))(root);
        }
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
        return 0;
    }
    return 0;
}

static int64_t sns_comment_get_self_key(int64_t base) {
    int64_t root;
    if (!base) return 0;
    __try {
        root = ((SnsCommentNoArgsFn)(base + WX_OFF_FINDER_USERPAGE_CGI_CONTEXT_ROOT))();
        if (root && r64((void *)root)) {
            return ((FnCall1)r64((void *)(r64((void *)root) + 32)))(root);
        }
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
        return 0;
    }
    return 0;
}

static int sns_comment_build_aux_field3(int64_t base, BYTE *out_str) {
    int64_t service;
    int64_t key;
    if (!out_str) return 0;
    sns_comment_init_std_string(out_str);
    if (!base) return 0;
    service = sns_comment_resolve_model_service(base);
    key = sns_comment_get_self_key(base);
    if (!service || !key) {
        return 1;
    }
    ((SnsCommentAuxStringFn)(base + WX_OFF_SNS_COMMENT_AUX_STRING))(
        service,
        (int64_t)out_str,
        key,
        1);
    return 1;
}

static void sns_like_probe_append_hex(char **p, char *end, const char *name, int64_t value) {
    char num[64];
    append_raw(p, end, ",\"");
    append_raw(p, end, name ? name : "");
    append_raw(p, end, "\":\"0x");
    wsprintfA(num, "%I64x", (unsigned __int64)value);
    append_raw(p, end, num);
    append_raw(p, end, "\"");
}

static void sns_like_probe_append_i64(char **p, char *end, const char *name, int64_t value) {
    char num[64];
    append_raw(p, end, ",\"");
    append_raw(p, end, name ? name : "");
    append_raw(p, end, "\":");
    wsprintfA(num, "%I64d", (long long)value);
    append_raw(p, end, num);
}

static void sns_like_probe_append_u64(char **p, char *end, const char *name, unsigned __int64 value) {
    char num[64];
    append_raw(p, end, ",\"");
    append_raw(p, end, name ? name : "");
    append_raw(p, end, "\":");
    wsprintfA(num, "%I64u", value);
    append_raw(p, end, num);
}

static void sns_like_probe_append_sso(char **p, char *end, const char *name, int64_t s) {
    char field[96];
    char buf[768];
    unsigned __int64 len = 0;
    unsigned __int64 cap = 0;

    if (!name) name = "str";
    wsprintfA(field, "%sPtr", name);
    sns_like_probe_append_hex(p, end, field, s);
    if (!s || !is_readable_ptr(s, 32)) return;

    __try {
        len = (unsigned __int64)r64((void *)(s + 0x10));
        cap = (unsigned __int64)r64((void *)(s + 0x18));
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
        return;
    }

    wsprintfA(field, "%sLen", name);
    sns_like_probe_append_u64(p, end, field, len);
    wsprintfA(field, "%sCap", name);
    sns_like_probe_append_u64(p, end, field, cap);
    if (len > 0x100000 || cap > 0x100000) return;

    buf[0] = 0;
    if (len > 0) {
        __try {
            wx_read_sso_str(s, buf, sizeof(buf));
        }
        __except (EXCEPTION_EXECUTE_HANDLER) {
            buf[0] = 0;
        }
    }
    append_raw(p, end, ",\"");
    append_raw(p, end, name);
    append_raw(p, end, "\":\"");
    append_escaped(p, end, buf);
    append_raw(p, end, "\"");
}

static void sns_like_probe_log_entry(int64_t manager,
                                     int64_t key,
                                     int64_t ctx,
                                     unsigned int ret) {
    char json[12288];
    char num[64];
    char *p = json;
    char *end = json + sizeof(json) - 1;
    static const int ctx_string_offsets[] = {0, 32, 64, 112, 144, 176, 208, 248};
    static const int ctx_int_offsets[] = {96, 104, 240};

    append_raw(&p, end, "{\"event\":\"sns_like_entry_probe\"");
    append_raw(&p, end, ",\"tick\":");
    wsprintfA(num, "%I64u", (unsigned __int64)GetTickCount64());
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"threadId\":");
    wsprintfA(num, "%lu", (unsigned long)GetCurrentThreadId());
    append_raw(&p, end, num);
    sns_like_probe_append_hex(&p, end, "manager", manager);
    sns_like_probe_append_sso(&p, end, "key", key);
    sns_like_probe_append_hex(&p, end, "ctx", ctx);
    sns_like_probe_append_u64(&p, end, "entryResult", (unsigned __int64)ret);

    if (ctx && is_readable_ptr(ctx, 280)) {
        for (int i = 0; i < (int)(sizeof(ctx_string_offsets) / sizeof(ctx_string_offsets[0])); i++) {
            char name[32];
            wsprintfA(name, "ctxS%d", ctx_string_offsets[i]);
            sns_like_probe_append_sso(&p, end, name, ctx + ctx_string_offsets[i]);
        }
        for (int i = 0; i < (int)(sizeof(ctx_int_offsets) / sizeof(ctx_int_offsets[0])); i++) {
            char name[32];
            wsprintfA(name, "ctxQ%d", ctx_int_offsets[i]);
            sns_like_probe_append_hex(&p, end, name, r64((void *)(ctx + ctx_int_offsets[i])));
            wsprintfA(name, "ctxI%d", ctx_int_offsets[i]);
            sns_like_probe_append_i64(&p, end, name, r64((void *)(ctx + ctx_int_offsets[i])));
        }
    }

    append_raw(&p, end, ",\"source\":\"sub_182BCCC70(manager,key,ctx)\"}\r\n");
    *p = 0;
    append_file_bytes(SNS_LIKE_PROBE_LOG_FILE, json, (DWORD)(p - json));
    InterlockedIncrement(&g_sns_like_probe_hook.capture_count);
}

static unsigned int __fastcall sns_like_entry_probe_hook(int64_t manager,
                                                         int64_t key,
                                                         int64_t ctx) {
    SnsLikeEntryFn original = (SnsLikeEntryFn)g_sns_like_probe_hook.trampoline;
    unsigned int ret;
    if (!original) return 0;
    if (InterlockedCompareExchange(&g_sns_like_probe_hook.reentry, 1, 0) != 0) {
        return original(manager, key, ctx);
    }
    ret = original(manager, key, ctx);
    __try {
        sns_like_probe_log_entry(manager, key, ctx, ret);
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
    }
    InterlockedExchange(&g_sns_like_probe_hook.reentry, 0);
    return ret;
}

static int sns_like_probe_build_trampoline(BYTE *p, BYTE *target, const BYTE *orig, int patch_len) {
    BYTE *start = p;
    copy_bytes(p, orig, patch_len);
    p += patch_len;
    *p++ = 0x48; *p++ = 0xB8;
    *(int64_t *)p = (int64_t)(target + patch_len); p += 8;
    *p++ = 0xFF; *p++ = 0xE0;
    return (int)(p - start);
}

static int sns_like_probe_write_abs_jump(BYTE *target, int patch_len, void *hook) {
    BYTE patch[32];
    DWORD old = 0;
    if (!target || patch_len < 12 || patch_len > (int)sizeof(patch)) return 0;
    for (int i = 0; i < patch_len; i++) patch[i] = 0x90;
    patch[0] = 0x48; patch[1] = 0xB8;
    *(int64_t *)(patch + 2) = (int64_t)hook;
    patch[10] = 0xFF; patch[11] = 0xE0;
    if (!VirtualProtect(target, (SIZE_T)patch_len, PAGE_EXECUTE_READWRITE, &old)) return 0;
    copy_bytes(target, patch, patch_len);
    VirtualProtect(target, (SIZE_T)patch_len, old, &old);
    FlushInstructionCache(GetCurrentProcess(), target, (SIZE_T)patch_len);
    return 1;
}

static char sns_ui_model_validate_replay(int64_t model) {
    int64_t extra;
    int64_t begin;
    int64_t end;
    int64_t item;
    int64_t nested;

    if (!model || !is_readable_ptr(model, 944)) return 0;
    __try {
        if (!r64((void *)model)) return 0;
        if (!r64((void *)(model + 416))) return 0;
        extra = r64((void *)(model + 768));
        if (!extra || !is_readable_ptr(extra, 344)) return 0;

        begin = r64((void *)(extra + 280));
        end = r64((void *)(extra + 288));
        if (begin != end) {
            while (begin != end) {
                if (!is_readable_ptr(begin, 8)) return 0;
                item = r64((void *)begin);
                if (!item || !is_readable_ptr(item + 24, 8)) return 0;
                if (!r64((void *)(item + 24))) return 0;
                begin += 16;
            }
        }

        if (!r64((void *)(extra + 128))) return 0;
        nested = r64((void *)(extra + 336));
        if (!nested) return 1;
        if (!is_readable_ptr(nested + 232, 8)) return 0;
        return r64((void *)(nested + 224)) != r64((void *)(nested + 232));
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
        return 0;
    }
}

static void sns_model_validate_capture_store(int64_t model) {
    unsigned __int64 sns_id;
    LONG n;
    int slot;
    char json[1024];
    char num[64];
    char *p = json;
    char *end = json + sizeof(json) - 1;
    if (!model || !is_readable_ptr(model, 8)) return;
    __try {
        sns_id = (unsigned __int64)r64((void *)model);
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
        return;
    }
    if (!sns_id) return;
    n = InterlockedIncrement(&g_sns_model_validate_capture_hook.capture_count);
    slot = (int)(n % SNS_MODEL_VALIDATE_CAPTURE_SLOTS);
    g_sns_validated_model_captures[slot].sns_id = sns_id;
    g_sns_validated_model_captures[slot].model = (uint64_t)model;
    g_sns_validated_model_captures[slot].tick = (uint64_t)GetTickCount64();

    append_raw(&p, end, "{\"event\":\"sns_model_validate_capture\"");
    append_raw(&p, end, ",\"tick\":");
    wsprintfA(num, "%I64u", (unsigned __int64)GetTickCount64());
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"threadId\":");
    wsprintfA(num, "%lu", (unsigned long)GetCurrentThreadId());
    append_raw(&p, end, num);
    sns_like_probe_append_hex(&p, end, "model", model);
    sns_like_probe_append_hex(&p, end, "modelFirstQword", (int64_t)sns_id);
    if (is_readable_ptr(model + 416, 8)) {
        sns_like_probe_append_hex(&p, end, "modelQ52", r64((void *)(model + 416)));
    }
    if (is_readable_ptr(model + 768, 8)) {
        int64_t extra = r64((void *)(model + 768));
        sns_like_probe_append_hex(&p, end, "modelQ96", extra);
        if (extra && is_readable_ptr(extra, 344)) {
            sns_like_probe_append_hex(&p, end, "extraVec35Begin", r64((void *)(extra + 280)));
            sns_like_probe_append_hex(&p, end, "extraVec35End", r64((void *)(extra + 288)));
            sns_like_probe_append_hex(&p, end, "extraQ16", r64((void *)(extra + 128)));
            sns_like_probe_append_hex(&p, end, "extraQ42", r64((void *)(extra + 336)));
        }
    }
    append_raw(&p, end, "}\r\n");
    *p = 0;
    append_file_bytes(SNS_MODEL_VALIDATE_CAPTURE_LOG_FILE, json, (DWORD)(p - json));
}

static char __fastcall sns_model_validate_capture_hook(int64_t model) {
    char ok;
    if (InterlockedCompareExchange(&g_sns_model_validate_capture_hook.reentry, 1, 0) != 0) {
        return sns_ui_model_validate_replay(model);
    }
    ok = sns_ui_model_validate_replay(model);
    if (ok) {
        sns_model_validate_capture_store(model);
    }
    InterlockedExchange(&g_sns_model_validate_capture_hook.reentry, 0);
    return ok;
}

static int sns_model_validate_capture_find(unsigned __int64 sns_id, uint64_t *model_out) {
    LONG count;
    int max;
    if (model_out) *model_out = 0;
    if (!sns_id) return 0;
    count = InterlockedCompareExchange(&g_sns_model_validate_capture_hook.capture_count, 0, 0);
    max = count < SNS_MODEL_VALIDATE_CAPTURE_SLOTS ? (int)count : SNS_MODEL_VALIDATE_CAPTURE_SLOTS;
    for (int i = 0; i < max; i++) {
        int slot = (int)((count - i) % SNS_MODEL_VALIDATE_CAPTURE_SLOTS);
        SnsValidatedModelCapture *cap = &g_sns_validated_model_captures[slot];
        if (cap->sns_id == sns_id &&
            cap->model &&
            is_readable_ptr((int64_t)cap->model, 944) &&
            sns_ui_model_validate_replay((int64_t)cap->model) &&
            (unsigned __int64)r64((void *)cap->model) == sns_id) {
            if (model_out) *model_out = cap->model;
            return 1;
        }
    }
    return 0;
}

static int sns_build_minimal_validated_model(int64_t base,
                                             unsigned __int64 sns_id,
                                             int64_t *model_sp) {
    int64_t model;
    int64_t extra;
    if (!base || !sns_id || !model_sp) return 0;
    model = ((FnCall1)(base + WX_OFF_WX_ALLOC))(944);
    extra = ((FnCall1)(base + WX_OFF_WX_ALLOC))(344);
    if (!model || !extra) return 0;
    ZeroMemory((void *)model, 944);
    ZeroMemory((void *)extra, 344);

    /*
     * Exact sub_183D9CB50 contract:
     * model[0] is the snsId consumed later by sub_184C79BF0, model[52] must
     * be non-zero, model[96] points to an extra block whose vector at
     * +280/+288 may be empty, +128 must be non-zero, and +336 may be null.
     */
    w64((void *)model, (int64_t)sns_id);
    w64((void *)(model + 416), model);
    w64((void *)(model + 768), extra);
    w64((void *)(extra + 128), 1);
    w64((void *)(extra + 280), 0);
    w64((void *)(extra + 288), 0);
    w64((void *)(extra + 336), 0);
    if (!sns_ui_model_validate_replay(model)) return 0;
    model_sp[0] = model;
    model_sp[1] = 0;
    return 1;
}

void install_sns_model_validate_capture_hook(void) {
    BYTE *target;
    DWORD old = 0;
    if (!wxb_weixin_module()) return;
    if (InterlockedCompareExchange(&g_sns_model_validate_capture_hook.installed, 0, 0) != 0) return;

    target = (BYTE *)((int64_t)wxb_weixin_module() + WX_OFF_SNS_UI_MODEL_VALIDATE);
    if (target[0] == 0x48 && target[1] == 0xB8) {
        if (VirtualProtect(target, SNS_MODEL_VALIDATE_PATCH_LEN, PAGE_EXECUTE_READWRITE, &old)) {
            copy_bytes(target, WX_ORIG_SNS_MODEL_VALIDATE_BYTES, SNS_MODEL_VALIDATE_PATCH_LEN);
            VirtualProtect(target, SNS_MODEL_VALIDATE_PATCH_LEN, old, &old);
            FlushInstructionCache(GetCurrentProcess(), target, SNS_MODEL_VALIDATE_PATCH_LEN);
            write_status("sns model validate capture stale hook restored\n");
        }
    }
    if (!bytes_equal(target, WX_ORIG_SNS_MODEL_VALIDATE_BYTES, SNS_MODEL_VALIDATE_PATCH_LEN)) {
        write_status("sns model validate capture hook prologue mismatch; hook not installed\n");
        return;
    }

    copy_bytes(g_sns_model_validate_capture_hook.orig, target, SNS_MODEL_VALIDATE_PATCH_LEN);
    if (!sns_like_probe_write_abs_jump(target,
                                       SNS_MODEL_VALIDATE_PATCH_LEN,
                                       (void *)sns_model_validate_capture_hook)) {
        write_status("sns model validate capture hook patch failed\n");
        return;
    }
    g_sns_model_validate_capture_hook.patch_len = SNS_MODEL_VALIDATE_PATCH_LEN;
    InterlockedExchange(&g_sns_model_validate_capture_hook.installed, 1);
    write_status("sns model validate capture hook installed\n");
}

void uninstall_sns_model_validate_capture_hook(void) {
    BYTE *target;
    DWORD old = 0;
    if (!wxb_weixin_module()) return;
    if (InterlockedCompareExchange(&g_sns_model_validate_capture_hook.installed, 0, 0) == 0) return;
    target = (BYTE *)((int64_t)wxb_weixin_module() + WX_OFF_SNS_UI_MODEL_VALIDATE);
    if (VirtualProtect(target,
                       (SIZE_T)g_sns_model_validate_capture_hook.patch_len,
                       PAGE_EXECUTE_READWRITE,
                       &old)) {
        copy_bytes(target,
                   g_sns_model_validate_capture_hook.orig,
                   g_sns_model_validate_capture_hook.patch_len);
        VirtualProtect(target, (SIZE_T)g_sns_model_validate_capture_hook.patch_len, old, &old);
        FlushInstructionCache(GetCurrentProcess(),
                              target,
                              (SIZE_T)g_sns_model_validate_capture_hook.patch_len);
    }
    InterlockedExchange(&g_sns_model_validate_capture_hook.installed, 0);
    write_status("sns model validate capture hook uninstalled\n");
}

void install_sns_like_probe_hook(void) {
    BYTE *target;
    int tlen;
    DWORD old = 0;
    if (!wxb_weixin_module()) return;
    if (InterlockedCompareExchange(&g_sns_like_probe_hook.installed, 0, 0) != 0) return;

    target = (BYTE *)((int64_t)wxb_weixin_module() + WX_OFF_SNS_LIKE_ENTRY);
    if (target[0] == 0x48 && target[1] == 0xB8) {
        if (VirtualProtect(target, SNS_LIKE_ENTRY_PATCH_LEN, PAGE_EXECUTE_READWRITE, &old)) {
            copy_bytes(target, WX_ORIG_SNS_LIKE_ENTRY_BYTES, SNS_LIKE_ENTRY_PATCH_LEN);
            VirtualProtect(target, SNS_LIKE_ENTRY_PATCH_LEN, old, &old);
            FlushInstructionCache(GetCurrentProcess(), target, SNS_LIKE_ENTRY_PATCH_LEN);
            write_status("sns like probe stale hook restored\n");
        }
    }
    if (!bytes_equal(target, WX_ORIG_SNS_LIKE_ENTRY_BYTES, SNS_LIKE_ENTRY_PATCH_LEN)) {
        write_status("sns like probe hook prologue mismatch; hook not installed\n");
        return;
    }

    copy_bytes(g_sns_like_probe_hook.orig, target, SNS_LIKE_ENTRY_PATCH_LEN);
    g_sns_like_probe_hook.trampoline = VirtualAlloc(NULL, 256, MEM_COMMIT | MEM_RESERVE,
                                                    PAGE_EXECUTE_READWRITE);
    if (!g_sns_like_probe_hook.trampoline) {
        write_status("sns like probe hook trampoline alloc failed\n");
        return;
    }
    tlen = sns_like_probe_build_trampoline((BYTE *)g_sns_like_probe_hook.trampoline,
                                           target,
                                           g_sns_like_probe_hook.orig,
                                           SNS_LIKE_ENTRY_PATCH_LEN);
    FlushInstructionCache(GetCurrentProcess(), g_sns_like_probe_hook.trampoline, tlen);
    if (!sns_like_probe_write_abs_jump(target,
                                       SNS_LIKE_ENTRY_PATCH_LEN,
                                       (void *)sns_like_entry_probe_hook)) {
        VirtualFree(g_sns_like_probe_hook.trampoline, 0, MEM_RELEASE);
        g_sns_like_probe_hook.trampoline = NULL;
        write_status("sns like probe hook patch failed\n");
        return;
    }
    g_sns_like_probe_hook.patch_len = SNS_LIKE_ENTRY_PATCH_LEN;
    InterlockedExchange(&g_sns_like_probe_hook.installed, 1);
    write_status("sns like probe hook installed\n");
}

void uninstall_sns_like_probe_hook(void) {
    BYTE *target;
    DWORD old = 0;
    if (!wxb_weixin_module()) return;
    if (InterlockedCompareExchange(&g_sns_like_probe_hook.installed, 0, 0) == 0) return;
    target = (BYTE *)((int64_t)wxb_weixin_module() + WX_OFF_SNS_LIKE_ENTRY);
    if (VirtualProtect(target,
                       (SIZE_T)g_sns_like_probe_hook.patch_len,
                       PAGE_EXECUTE_READWRITE,
                       &old)) {
        copy_bytes(target, g_sns_like_probe_hook.orig, g_sns_like_probe_hook.patch_len);
        VirtualProtect(target, (SIZE_T)g_sns_like_probe_hook.patch_len, old, &old);
        FlushInstructionCache(GetCurrentProcess(), target, (SIZE_T)g_sns_like_probe_hook.patch_len);
    }
    InterlockedExchange(&g_sns_like_probe_hook.installed, 0);
    if (g_sns_like_probe_hook.trampoline) {
        VirtualFree(g_sns_like_probe_hook.trampoline, 0, MEM_RELEASE);
        g_sns_like_probe_hook.trampoline = NULL;
    }
    write_status("sns like probe hook uninstalled\n");
}

void refresh_sns_like_probe_hook(void) {
    if (file_exists(SNS_LIKE_PROBE_ENABLE_FILE)) {
        install_sns_like_probe_hook();
    } else {
        uninstall_sns_like_probe_hook();
    }
}

int sns_like_probe_hook_installed(void) {
    return InterlockedCompareExchange(&g_sns_like_probe_hook.installed, 0, 0) != 0;
}

LONG sns_like_probe_capture_count(void) {
    return InterlockedCompareExchange(&g_sns_like_probe_hook.capture_count, 0, 0);
}

static void sns_model_lookup_probe_log(int64_t service,
                                       int64_t out_sp,
                                       int64_t key,
                                       int64_t ret) {
    char json[12288];
    char num[64];
    char *p = json;
    char *end = json + sizeof(json) - 1;
    int64_t model = 0;
    int64_t ctl = 0;

    if (out_sp && is_readable_ptr(out_sp, 16)) {
        __try {
            model = r64((void *)out_sp);
            ctl = r64((void *)(out_sp + 8));
        }
        __except (EXCEPTION_EXECUTE_HANDLER) {
            model = 0;
            ctl = 0;
        }
    }

    append_raw(&p, end, "{\"event\":\"sns_model_lookup_probe\"");
    append_raw(&p, end, ",\"tick\":");
    wsprintfA(num, "%I64u", (unsigned __int64)GetTickCount64());
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"threadId\":");
    wsprintfA(num, "%lu", (unsigned long)GetCurrentThreadId());
    append_raw(&p, end, num);
    sns_like_probe_append_hex(&p, end, "service", service);
    sns_like_probe_append_hex(&p, end, "outSp", out_sp);
    sns_like_probe_append_sso(&p, end, "key", key);
    sns_like_probe_append_hex(&p, end, "ret", ret);
    sns_like_probe_append_hex(&p, end, "model", model);
    sns_like_probe_append_hex(&p, end, "modelCtl", ctl);
    if (model && is_readable_ptr(model, 8)) {
        sns_like_probe_append_hex(&p, end, "modelSnsId", r64((void *)model));
    }
    if (model && is_readable_ptr(model + 108, 4)) {
        sns_like_probe_append_i64(&p, end, "modelLikeFlag", (int64_t)r32((void *)(model + 108)));
    }
    if (model && is_readable_ptr(model + 580, 4)) {
        sns_like_probe_append_i64(&p, end, "modelSource", (int64_t)r32((void *)(model + 580)));
    }
    append_raw(&p, end, ",\"source\":\"sub_180EA6C10(service,out,key)\"");
    append_raw(&p, end, ",\"staticEvidence\":\"sub_180EA4EF0 passes official timeline item key string to sub_180EA6C10; sub_182BDF480 consumes the returned model shared_ptr\"");
    append_raw(&p, end, "}\r\n");
    *p = 0;
    append_file_bytes(SNS_MODEL_LOOKUP_PROBE_LOG_FILE, json, (DWORD)(p - json));
    InterlockedIncrement(&g_sns_model_lookup_probe_hook.capture_count);
}

static int64_t __fastcall sns_model_lookup_probe_hook(int64_t service,
                                                      int64_t out_sp,
                                                      int64_t key) {
    SnsModelLookupFn original = (SnsModelLookupFn)g_sns_model_lookup_probe_hook.trampoline;
    int64_t ret;
    if (!original) return 0;
    if (InterlockedCompareExchange(&g_sns_model_lookup_probe_hook.reentry, 1, 0) != 0) {
        return original(service, out_sp, key);
    }
    ret = original(service, out_sp, key);
    __try {
        sns_model_lookup_probe_log(service, out_sp, key, ret);
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
    }
    InterlockedExchange(&g_sns_model_lookup_probe_hook.reentry, 0);
    return ret;
}

void install_sns_model_lookup_probe_hook(void) {
    BYTE *target;
    int tlen;
    DWORD old = 0;
    if (!wxb_weixin_module()) return;
    if (InterlockedCompareExchange(&g_sns_model_lookup_probe_hook.installed, 0, 0) != 0) return;

    target = (BYTE *)((int64_t)wxb_weixin_module() + WX_OFF_SNS_MODEL_LOOKUP);
    if (target[0] == 0x48 && target[1] == 0xB8) {
        if (VirtualProtect(target, SNS_MODEL_LOOKUP_PATCH_LEN, PAGE_EXECUTE_READWRITE, &old)) {
            copy_bytes(target, WX_ORIG_SNS_MODEL_LOOKUP_BYTES, SNS_MODEL_LOOKUP_PATCH_LEN);
            VirtualProtect(target, SNS_MODEL_LOOKUP_PATCH_LEN, old, &old);
            FlushInstructionCache(GetCurrentProcess(), target, SNS_MODEL_LOOKUP_PATCH_LEN);
            write_status("sns model lookup probe stale hook restored\n");
        }
    }
    if (!bytes_equal(target, WX_ORIG_SNS_MODEL_LOOKUP_BYTES, SNS_MODEL_LOOKUP_PATCH_LEN)) {
        write_status("sns model lookup probe hook prologue mismatch; hook not installed\n");
        return;
    }

    copy_bytes(g_sns_model_lookup_probe_hook.orig, target, SNS_MODEL_LOOKUP_PATCH_LEN);
    g_sns_model_lookup_probe_hook.trampoline = VirtualAlloc(NULL, 256, MEM_COMMIT | MEM_RESERVE,
                                                            PAGE_EXECUTE_READWRITE);
    if (!g_sns_model_lookup_probe_hook.trampoline) {
        write_status("sns model lookup probe hook trampoline alloc failed\n");
        return;
    }
    tlen = sns_like_probe_build_trampoline((BYTE *)g_sns_model_lookup_probe_hook.trampoline,
                                           target,
                                           g_sns_model_lookup_probe_hook.orig,
                                           SNS_MODEL_LOOKUP_PATCH_LEN);
    FlushInstructionCache(GetCurrentProcess(), g_sns_model_lookup_probe_hook.trampoline, tlen);
    if (!sns_like_probe_write_abs_jump(target,
                                       SNS_MODEL_LOOKUP_PATCH_LEN,
                                       (void *)sns_model_lookup_probe_hook)) {
        VirtualFree(g_sns_model_lookup_probe_hook.trampoline, 0, MEM_RELEASE);
        g_sns_model_lookup_probe_hook.trampoline = NULL;
        write_status("sns model lookup probe hook patch failed\n");
        return;
    }
    g_sns_model_lookup_probe_hook.patch_len = SNS_MODEL_LOOKUP_PATCH_LEN;
    InterlockedExchange(&g_sns_model_lookup_probe_hook.installed, 1);
    write_status("sns model lookup probe hook installed\n");
}

void uninstall_sns_model_lookup_probe_hook(void) {
    BYTE *target;
    DWORD old = 0;
    if (!wxb_weixin_module()) return;
    if (InterlockedCompareExchange(&g_sns_model_lookup_probe_hook.installed, 0, 0) == 0) return;
    target = (BYTE *)((int64_t)wxb_weixin_module() + WX_OFF_SNS_MODEL_LOOKUP);
    if (VirtualProtect(target,
                       (SIZE_T)g_sns_model_lookup_probe_hook.patch_len,
                       PAGE_EXECUTE_READWRITE,
                       &old)) {
        copy_bytes(target,
                   g_sns_model_lookup_probe_hook.orig,
                   g_sns_model_lookup_probe_hook.patch_len);
        VirtualProtect(target, (SIZE_T)g_sns_model_lookup_probe_hook.patch_len, old, &old);
        FlushInstructionCache(GetCurrentProcess(),
                              target,
                              (SIZE_T)g_sns_model_lookup_probe_hook.patch_len);
    }
    InterlockedExchange(&g_sns_model_lookup_probe_hook.installed, 0);
    if (g_sns_model_lookup_probe_hook.trampoline) {
        VirtualFree(g_sns_model_lookup_probe_hook.trampoline, 0, MEM_RELEASE);
        g_sns_model_lookup_probe_hook.trampoline = NULL;
    }
    write_status("sns model lookup probe hook uninstalled\n");
}

void refresh_sns_model_lookup_probe_hook(void) {
    if (file_exists(SNS_MODEL_LOOKUP_PROBE_ENABLE_FILE)) {
        install_sns_model_lookup_probe_hook();
    } else {
        uninstall_sns_model_lookup_probe_hook();
    }
}

int sns_model_lookup_probe_hook_installed(void) {
    return InterlockedCompareExchange(&g_sns_model_lookup_probe_hook.installed, 0, 0) != 0;
}

LONG sns_model_lookup_probe_capture_count(void) {
    return InterlockedCompareExchange(&g_sns_model_lookup_probe_hook.capture_count, 0, 0);
}

static int build_sns_comment_queue_asm(BYTE *p, int64_t task_addr, int64_t worker_addr) {
    BYTE *start = p;
    BYTE pro[] = {0x48,0x83,0xEC,0x28};
    BYTE epi[] = {0x48,0x83,0xC4,0x28,0xC3};
    copy_bytes(p, pro, sizeof(pro)); p += sizeof(pro);
    *p++ = 0x48; *p++ = 0xB9;
    *(int64_t *)p = task_addr; p += 8;
    *p++ = 0x48; *p++ = 0xB8;
    *(int64_t *)p = worker_addr; p += 8;
    *p++ = 0xFF; *p++ = 0xD0;
    copy_bytes(p, epi, sizeof(epi)); p += sizeof(epi);
    return (int)(p - start);
}

static void sns_comment_worker(int64_t task_addr) {
    SnsCommentTask *task = (SnsCommentTask *)task_addr;
    int64_t base = (int64_t)wxb_weixin_module();
    BYTE req[SNS_COMMENT_REQ_SIZE];
    BYTE extra[SNS_COMMENT_EXTRA_SIZE];
    BYTE opts[SNS_COMMENT_OPTS_SIZE];
    BYTE out[SNS_COMMENT_OUT_SIZE];
    BYTE aux_field3[32];
    int64_t root = 0;
    int64_t cgi_ctx = 0;
    int64_t content_req = 0;
    int64_t comment = 0;
    int req_initialized = 0;
    int out_initialized = 0;
    int aux_field3_initialized = 0;
    DWORD uin;

    if (!task || !base) return;
    if (InterlockedCompareExchange(&task->status, 2, 0) != 0) return;

    ZeroMemory(req, sizeof(req));
    ZeroMemory(extra, sizeof(extra));
    ZeroMemory(opts, sizeof(opts));
    ZeroMemory(out, sizeof(out));
    w64(extra + 0x18, 15);
    w64(opts, 0x1D4C000000000LL);
    w64(opts + 0x20, 15);

    __try {
        task->phase = 1;
        sns_comment_get_self_username(base, task->self_username, sizeof(task->self_username));
        uin = sns_comment_get_self_uin(base);
        task->self_uin = uin;
        wsprintfA(task->client_id, "%s_%lu_%lu",
                  task->action_type == SNS_COMMENT_TYPE_LIKE ? "like" : "comment",
                  (unsigned long)(uin ? uin : GetCurrentProcessId()),
                  (unsigned long)time(NULL));

        task->phase = 2;
        ((FnCall1)(base + WX_OFF_SNS_COMMENT_REQ_INIT))((int64_t)req);
        req_initialized = 1;

        task->phase = 3;
        content_req = ((FnCall1)(base + WX_OFF_WX_ALLOC))(SNS_COMMENT_CONTENT_REQ_SIZE);
        comment = ((FnCall1)(base + WX_OFF_WX_ALLOC))(SNS_COMMENT_CONTENT_SIZE);
        if (!content_req || !comment) {
            task->call_result = -2;
            InterlockedExchange(&task->status, -3);
            __leave;
        }
        ((FnCall1)(base + WX_OFF_SNS_COMMENT_CONTENT_REQ_INIT))(content_req);
        ((FnCall1)(base + WX_OFF_SNS_COMMENT_CONTENT_INIT))(comment);

        w64(req + 16, content_req);
        w32(req + 36, r32(req + 36) | 0x02);
        if (!sns_comment_set_pb_string(base, req, 24, 36, 0x04, task->client_id)) {
            task->call_result = -3;
            InterlockedExchange(&task->status, -4);
            __leave;
        }

        task->phase = 4;
        w64((void *)(content_req + 8), (int64_t)task->feed_id);
        w64((void *)(content_req + 24), comment);
        w32((void *)(content_req + 56), r32((void *)(content_req + 56)) | 0x05);

        if (!sns_comment_set_pb_string(base, (BYTE *)comment, 8, 176, 0x01,
                                       task->self_username[0] ? task->self_username : "")) {
            task->call_result = -4;
            InterlockedExchange(&task->status, -5);
            __leave;
        }
        if (task->action_type == SNS_COMMENT_TYPE_LIKE) {
            if (!sns_comment_set_pb_string(base, (BYTE *)comment, 16, 176, 0x02, task->username)) {
                task->call_result = -4;
                InterlockedExchange(&task->status, -5);
                __leave;
            }
        } else {
            /*
             * sub_184CA29F0 fills SnsAction fields 2/4 from the target
             * model metadata, then the model vcall contributes the comment
             * body. SnsAction::Serialize writes field 8 from +0x30, so the
             * visible comment text must be present there.
             */
            aux_field3_initialized = 1;
            if (!sns_comment_build_aux_field3(base, aux_field3)) {
                task->call_result = -4;
                InterlockedExchange(&task->status, -5);
                __leave;
            }
            if (!sns_comment_set_pb_string(base, (BYTE *)comment, 16, 176, 0x02, task->username) ||
                !sns_comment_set_pb_string_from_std(base, (BYTE *)comment, 24, 176, 0x04, (int64_t)aux_field3) ||
                !sns_comment_set_pb_string(base, (BYTE *)comment, 32, 176, 0x08, task->username) ||
                !sns_comment_set_pb_string(base, (BYTE *)comment, 48, 176, 0x80, task->content)) {
                task->call_result = -4;
                InterlockedExchange(&task->status, -5);
                __leave;
            }
        }
        w32((void *)(comment + 40),
            task->action_type ? task->action_type : SNS_COMMENT_TYPE_TEXT);
        w32((void *)(comment + 176), r32((void *)(comment + 176)) | 0x10);
        w32((void *)(comment + 44), 0);
        w32((void *)(comment + 176), r32((void *)(comment + 176)) | 0x20);
        w32((void *)(comment + 56),
            task->action_type == SNS_COMMENT_TYPE_LIKE ? uin : (DWORD)time(NULL));
        w32((void *)(comment + 176), r32((void *)(comment + 176)) | 0x40);
        if (task->action_type != SNS_COMMENT_TYPE_LIKE) {
            /*
             * sub_184CA29F0 always marks field +0x8c present for ordinary
             * comments, even when sub_183E1D440 returns the default value 0.
             */
            w32((void *)(comment + 140), 0);
            w32((void *)(comment + 176), r32((void *)(comment + 176)) | 0x80000);
            if (!sns_comment_apply_reply_target(base, content_req, comment, task)) {
                task->call_result = -4;
                InterlockedExchange(&task->status, -5);
                __leave;
            }
        }

        task->phase = 5;
        root = ((SnsCommentNoArgsFn)(base + WX_OFF_FINDER_USERPAGE_CGI_CONTEXT_ROOT))();
        if (root && r64((void *)root)) {
            cgi_ctx = ((FnCall1)r64((void *)(r64((void *)root) + 16)))(root);
        }
        if (!cgi_ctx) {
            task->call_result = -5;
            InterlockedExchange(&task->status, -6);
            __leave;
        }

        task->phase = 6;
        ((SnsCommentSendFn)(base + WX_OFF_SNS_COMMENT_SEND))(
            (unsigned int *)out,
            cgi_ctx,
            (int64_t)req,
            (int64_t)extra,
            (int64_t)opts);
        out_initialized = 1;
        task->call_result = r64(out);
        task->wait_status = WAIT_OBJECT_0;
        task->phase = 7;
        InterlockedExchange(&task->status, task->call_result == 0 ? 1 : -7);
    }
    __except (task->seh_code = GetExceptionCode(),
#if defined(_M_X64)
              task->ctx_rcx = (uint64_t)GetExceptionInformation()->ContextRecord->Rcx,
              task->ctx_rdx = (uint64_t)GetExceptionInformation()->ContextRecord->Rdx,
              task->ctx_r8 = (uint64_t)GetExceptionInformation()->ContextRecord->R8,
              task->ctx_r9 = (uint64_t)GetExceptionInformation()->ContextRecord->R9,
#endif
              task->seh_addr = (uint64_t)GetExceptionInformation()->ExceptionRecord->ExceptionAddress,
              task->seh_fault = (GetExceptionInformation()->ExceptionRecord->NumberParameters > 1)
                                    ? (uint64_t)GetExceptionInformation()->ExceptionRecord->ExceptionInformation[1]
                                    : 0,
              EXCEPTION_EXECUTE_HANDLER) {
        task->wait_status = WAIT_FAILED;
        task->call_result = -1;
        InterlockedExchange(&task->status, -1);
    }

    if (out_initialized) {
        sns_comment_cleanup_send_out(base, out);
    }
    if (aux_field3_initialized) {
        wx_free_std_string_heap(base, (int64_t)aux_field3);
    }
    if (req_initialized) {
        ((FnCall1)(base + WX_OFF_SNS_COMMENT_REQ_DTOR))((int64_t)req);
    }
}

static int sns_like_try_model_key(int64_t base,
                                  int64_t service,
                                  int64_t key,
                                  SnsCommentTask *task,
                                  const char *candidate,
                                  const char *variant) {
    int64_t model_sp[2] = {0, 0};
    if (!base || !service || !key || !task || !candidate || !*candidate) {
        return 0;
    }

    wx_free_std_string_heap(base, key);
    wx_set_std_string_heap(base, key, candidate);
    safe_copy(task->lookup_key, sizeof(task->lookup_key), candidate);
    safe_copy(task->lookup_variant, sizeof(task->lookup_variant), variant ? variant : "");
    task->model_lookup_attempts++;
    task->model_found = 0;
    task->model_like_flag = 0;
    task->model_source = 0;
    task->already_liked = 0;

    ((SnsModelLookupFn)(base + WX_OFF_SNS_MODEL_LOOKUP))(service,
                                                         (int64_t)model_sp,
                                                         key);
    if (model_sp[0]) {
        task->model_found = 1;
        if (is_readable_ptr(model_sp[0] + 108, 4)) {
            task->model_like_flag = (int)r32((void *)(model_sp[0] + 108));
            task->already_liked = (task->model_like_flag & 1) ? 1 : 0;
        }
        if (is_readable_ptr(model_sp[0] + 580, 4)) {
            task->model_source = (int)r32((void *)(model_sp[0] + 580));
        }
    }
    sns_comment_release_sp(model_sp);
    return task->model_found;
}

static int sns_delete_try_model_key(int64_t base,
                                    int64_t service,
                                    int64_t key,
                                    SnsCommentTask *task,
                                    int64_t *model_sp,
                                    const char *candidate,
                                    const char *variant) {
    if (!base || !service || !key || !task || !model_sp || !candidate || !*candidate) {
        return 0;
    }

    if (model_sp[0] || model_sp[1]) {
        sns_comment_release_sp(model_sp);
    }
    wx_free_std_string_heap(base, key);
    wx_set_std_string_heap(base, key, candidate);
    safe_copy(task->lookup_key, sizeof(task->lookup_key), candidate);
    safe_copy(task->lookup_variant, sizeof(task->lookup_variant), variant ? variant : "");
    task->model_lookup_attempts++;
    task->model_found = 0;
    task->model_source = 0;

    ((SnsModelLookupFn)(base + WX_OFF_SNS_MODEL_LOOKUP))(service,
                                                         (int64_t)model_sp,
                                                         key);
    if (model_sp[0]) {
        task->model_found = 1;
        if (is_readable_ptr(model_sp[0] + 580, 4)) {
            task->model_source = (int)r32((void *)(model_sp[0] + 580));
        }
    }
    return task->model_found;
}

static void sns_like_high_worker(int64_t task_addr) {
    SnsCommentTask *task = (SnsCommentTask *)task_addr;
    int64_t base = (int64_t)wxb_weixin_module();
    BYTE key[32];
    BYTE ctx[280];
    int64_t manager = 0;
    int64_t service = 0;
    int ctx_initialized = 0;
    char unsigned_key[64];
    char signed_key[64];
    char hex_key[64];
    char prefixed_hex_key[64];
    char combo_key[512];

    if (!task || !base) return;
    if (InterlockedCompareExchange(&task->status, 2, 0) != 0) return;

    ZeroMemory(key, sizeof(key));
    w64(key + 0x18, 15);
    ZeroMemory(ctx, sizeof(ctx));

    __try {
        task->high_path = 1;
        task->phase = 10;
        sns_comment_get_self_username(base, task->self_username, sizeof(task->self_username));
        task->self_uin = sns_comment_get_self_uin(base);

        task->phase = 11;
        manager = sns_comment_resolve_manager(base);
        service = sns_comment_resolve_model_service(base);
        if (!manager || !service) {
            task->call_result = -10;
            task->wait_status = WAIT_FAILED;
            InterlockedExchange(&task->status, -10);
            __leave;
        }

        task->phase = 12;
        wsprintfA(unsigned_key, "%I64u", (unsigned __int64)task->feed_id);
        wsprintfA(signed_key, "%I64d", (__int64)task->feed_id);
        wsprintfA(hex_key, "%I64x", (unsigned __int64)task->feed_id);
        wsprintfA(prefixed_hex_key, "0x%I64x", (unsigned __int64)task->feed_id);

        task->phase = 13;
        if (task->sns_key[0]) {
            sns_like_try_model_key(base, service, (int64_t)key, task,
                                   task->sns_key, "explicit_sns_key");
        }
        if (!task->model_found) {
            sns_like_try_model_key(base, service, (int64_t)key, task,
                                   unsigned_key, "unsigned_decimal");
        }
        if (!task->model_found && lstrcmpA(signed_key, unsigned_key) != 0) {
            sns_like_try_model_key(base, service, (int64_t)key, task,
                                   signed_key, "signed_decimal");
        }
        if (!task->model_found && task->sns_id[0] &&
            lstrcmpA(task->sns_id, task->lookup_key) != 0) {
            sns_like_try_model_key(base, service, (int64_t)key, task,
                                   task->sns_id, "input");
        }
        if (!task->model_found && lstrcmpA(hex_key, task->lookup_key) != 0) {
            sns_like_try_model_key(base, service, (int64_t)key, task,
                                   hex_key, "hex");
        }
        if (!task->model_found && lstrcmpA(prefixed_hex_key, task->lookup_key) != 0) {
            sns_like_try_model_key(base, service, (int64_t)key, task,
                                   prefixed_hex_key, "prefixed_hex");
        }
        if (!task->model_found && task->username[0]) {
            sns_like_join_key(combo_key, sizeof(combo_key), unsigned_key, "", task->username);
            sns_like_try_model_key(base, service, (int64_t)key, task,
                                   combo_key, "unsigned_decimal_plus_username");
        }
        if (!task->model_found && task->username[0]) {
            sns_like_join_key(combo_key, sizeof(combo_key), task->username, "", unsigned_key);
            sns_like_try_model_key(base, service, (int64_t)key, task,
                                   combo_key, "username_plus_unsigned_decimal");
        }
        if (!task->model_found && task->username[0]) {
            sns_like_join_key(combo_key, sizeof(combo_key), unsigned_key, "_", task->username);
            sns_like_try_model_key(base, service, (int64_t)key, task,
                                   combo_key, "unsigned_decimal_underscore_username");
        }
        if (!task->model_found && task->username[0]) {
            sns_like_join_key(combo_key, sizeof(combo_key), task->username, "_", unsigned_key);
            sns_like_try_model_key(base, service, (int64_t)key, task,
                                   combo_key, "username_underscore_unsigned_decimal");
        }
        if (!task->model_found && task->username[0]) {
            sns_like_join_key(combo_key, sizeof(combo_key), hex_key, "", task->username);
            sns_like_try_model_key(base, service, (int64_t)key, task,
                                   combo_key, "hex_plus_username");
        }
        if (!task->model_found && task->username[0]) {
            sns_like_join_key(combo_key, sizeof(combo_key), task->username, "", hex_key);
            sns_like_try_model_key(base, service, (int64_t)key, task,
                                   combo_key, "username_plus_hex");
        }
        if (!task->model_found && task->username[0]) {
            sns_like_join_key(combo_key, sizeof(combo_key), prefixed_hex_key, "", task->username);
            sns_like_try_model_key(base, service, (int64_t)key, task,
                                   combo_key, "prefixed_hex_plus_username");
        }
        if (!task->model_found && task->username[0]) {
            sns_like_join_key(combo_key, sizeof(combo_key), task->username, "", prefixed_hex_key);
            sns_like_try_model_key(base, service, (int64_t)key, task,
                                   combo_key, "username_plus_prefixed_hex");
        }
        if (!task->model_found) {
            task->call_result = 0;
            task->wait_status = WAIT_OBJECT_0;
            InterlockedExchange(&task->status, -8);
            __leave;
        }

        task->phase = 14;
        ((SnsLikeCtxLookupFn)(base + WX_OFF_SNS_LIKE_CTX_LOOKUP))(
            manager,
            (int64_t)ctx,
            (int64_t)key);
        ctx_initialized = 1;
        if (is_readable_ptr((int64_t)ctx + 16, 8)) {
            unsigned __int64 key_len = r64(ctx + 16);
            task->ctx_key_len = key_len > 0x7fffffff ? 0x7fffffff : (int)key_len;
            task->ctx_found = task->ctx_key_len > 0;
        }

        task->phase = 15;
        if (task->already_liked) {
            task->entry_skipped = 1;
            task->call_result = 0;
            task->wait_status = WAIT_OBJECT_0;
            task->phase = 16;
            InterlockedExchange(&task->status, 1);
            __leave;
        }

        task->high_entry_result = ((SnsLikeEntryFn)(base + WX_OFF_SNS_LIKE_ENTRY))(
            manager,
            (int64_t)key,
            (int64_t)ctx);
        task->call_result = (int64_t)task->high_entry_result;
        task->wait_status = WAIT_OBJECT_0;
        task->phase = 16;
        InterlockedExchange(&task->status, 1);
    }
    __except (task->seh_code = GetExceptionCode(),
#if defined(_M_X64)
              task->ctx_rcx = (uint64_t)GetExceptionInformation()->ContextRecord->Rcx,
              task->ctx_rdx = (uint64_t)GetExceptionInformation()->ContextRecord->Rdx,
              task->ctx_r8 = (uint64_t)GetExceptionInformation()->ContextRecord->R8,
              task->ctx_r9 = (uint64_t)GetExceptionInformation()->ContextRecord->R9,
#endif
              task->seh_addr = (uint64_t)GetExceptionInformation()->ExceptionRecord->ExceptionAddress,
              task->seh_fault = (GetExceptionInformation()->ExceptionRecord->NumberParameters > 1)
                                    ? (uint64_t)GetExceptionInformation()->ExceptionRecord->ExceptionInformation[1]
                                    : 0,
              EXCEPTION_EXECUTE_HANDLER) {
        task->wait_status = WAIT_FAILED;
        task->call_result = -1;
        InterlockedExchange(&task->status, -1);
    }

    if (ctx_initialized) {
        __try {
            ((SnsLikeCtxDtorFn)(base + WX_OFF_SNS_LIKE_CTX_DTOR))((int64_t)ctx);
        }
        __except (EXCEPTION_EXECUTE_HANDLER) {
        }
    }
    wx_free_std_string_heap(base, (int64_t)key);
}

static void sns_delete_worker(int64_t task_addr) {
    SnsCommentTask *task = (SnsCommentTask *)task_addr;
    int64_t base = (int64_t)wxb_weixin_module();
    BYTE key[32];
    int64_t model_sp[2] = {0, 0};
    int64_t manager = 0;
    int64_t service = 0;
    char unsigned_key[64];
    char signed_key[64];
    char hex_key[64];
    char prefixed_hex_key[64];
    char combo_key[512];
    int set_visibility = 0;

    if (!task || !base) return;
    if (InterlockedCompareExchange(&task->status, 2, 0) != 0) return;
    set_visibility = (task->action_type == 9);

    ZeroMemory(key, sizeof(key));
    w64(key + 0x18, 15);

    __try {
        task->high_path = 1;
        task->phase = 20;
        sns_comment_get_self_username(base, task->self_username, sizeof(task->self_username));
        task->self_uin = sns_comment_get_self_uin(base);

        task->phase = 21;
        manager = sns_comment_resolve_manager(base);
        service = sns_comment_resolve_model_service(base);
        if (!manager || !service) {
            task->call_result = -10;
            task->wait_status = WAIT_FAILED;
            InterlockedExchange(&task->status, -10);
            __leave;
        }

        task->phase = 22;
        wsprintfA(unsigned_key, "%I64u", (unsigned __int64)task->feed_id);
        wsprintfA(signed_key, "%I64d", (__int64)task->feed_id);
        wsprintfA(hex_key, "%I64x", (unsigned __int64)task->feed_id);
        wsprintfA(prefixed_hex_key, "0x%I64x", (unsigned __int64)task->feed_id);

        task->phase = 23;
        if (task->sns_key[0]) {
            sns_delete_try_model_key(base, service, (int64_t)key, task, model_sp,
                                     task->sns_key, "explicit_sns_key");
        }
        if (!task->model_found) {
            sns_delete_try_model_key(base, service, (int64_t)key, task, model_sp,
                                     unsigned_key, "unsigned_decimal");
        }
        if (!task->model_found && lstrcmpA(signed_key, unsigned_key) != 0) {
            sns_delete_try_model_key(base, service, (int64_t)key, task, model_sp,
                                     signed_key, "signed_decimal");
        }
        if (!task->model_found && task->sns_id[0] &&
            lstrcmpA(task->sns_id, task->lookup_key) != 0) {
            sns_delete_try_model_key(base, service, (int64_t)key, task, model_sp,
                                     task->sns_id, "input");
        }
        if (!task->model_found && lstrcmpA(hex_key, task->lookup_key) != 0) {
            sns_delete_try_model_key(base, service, (int64_t)key, task, model_sp,
                                     hex_key, "hex");
        }
        if (!task->model_found && lstrcmpA(prefixed_hex_key, task->lookup_key) != 0) {
            sns_delete_try_model_key(base, service, (int64_t)key, task, model_sp,
                                     prefixed_hex_key, "prefixed_hex");
        }
        if (!task->model_found && task->username[0]) {
            sns_like_join_key(combo_key, sizeof(combo_key), unsigned_key, "", task->username);
            sns_delete_try_model_key(base, service, (int64_t)key, task, model_sp,
                                     combo_key, "unsigned_decimal_plus_username");
        }
        if (!task->model_found && task->username[0]) {
            sns_like_join_key(combo_key, sizeof(combo_key), task->username, "", unsigned_key);
            sns_delete_try_model_key(base, service, (int64_t)key, task, model_sp,
                                     combo_key, "username_plus_unsigned_decimal");
        }
        if (!task->model_found && task->username[0]) {
            sns_like_join_key(combo_key, sizeof(combo_key), unsigned_key, "_", task->username);
            sns_delete_try_model_key(base, service, (int64_t)key, task, model_sp,
                                     combo_key, "unsigned_decimal_underscore_username");
        }
        if (!task->model_found && task->username[0]) {
            sns_like_join_key(combo_key, sizeof(combo_key), task->username, "_", unsigned_key);
            sns_delete_try_model_key(base, service, (int64_t)key, task, model_sp,
                                     combo_key, "username_underscore_unsigned_decimal");
        }
        if (!task->model_found && task->username[0]) {
            sns_like_join_key(combo_key, sizeof(combo_key), hex_key, "", task->username);
            sns_delete_try_model_key(base, service, (int64_t)key, task, model_sp,
                                     combo_key, "hex_plus_username");
        }
        if (!task->model_found && task->username[0]) {
            sns_like_join_key(combo_key, sizeof(combo_key), task->username, "", hex_key);
            sns_delete_try_model_key(base, service, (int64_t)key, task, model_sp,
                                     combo_key, "username_plus_hex");
        }
        if (!task->model_found && task->username[0]) {
            sns_like_join_key(combo_key, sizeof(combo_key), prefixed_hex_key, "", task->username);
            sns_delete_try_model_key(base, service, (int64_t)key, task, model_sp,
                                     combo_key, "prefixed_hex_plus_username");
        }
        if (!task->model_found && task->username[0]) {
            sns_like_join_key(combo_key, sizeof(combo_key), task->username, "", prefixed_hex_key);
            sns_delete_try_model_key(base, service, (int64_t)key, task, model_sp,
                                     combo_key, "username_plus_prefixed_hex");
        }
        if (set_visibility && !task->model_found) {
            uint64_t validated_model = 0;
            install_sns_model_validate_capture_hook();
            if (sns_model_validate_capture_find(task->feed_id, &validated_model)) {
                task->captured_model_ptr = validated_model;
                task->captured_model_ctl = 0;
                task->model_source = 92;
                safe_copy(task->lookup_variant,
                          sizeof(task->lookup_variant),
                          "validated_ui_model_capture");
                wsprintfA(task->lookup_key,
                          "validatedModel=0x%I64x snsId=0x%I64x",
                          (unsigned __int64)validated_model,
                          (unsigned __int64)task->feed_id);
            }
        }
        if (!task->model_found && task->captured_model_ptr) {
            task->phase = 24;
            if (!is_readable_ptr((int64_t)task->captured_model_ptr, 8)) {
                task->captured_model_invalid = 1;
            } else if (set_visibility && task->captured_model_ctl &&
                       is_readable_ptr((int64_t)task->captured_model_ctl, 16)) {
                _InterlockedIncrement((volatile LONG *)(task->captured_model_ctl + 8));
                model_sp[0] = (int64_t)task->captured_model_ptr;
                model_sp[1] = (int64_t)task->captured_model_ctl;
                task->model_found = 1;
                task->model_source = 91;
                task->captured_model_used = 1;
                safe_copy(task->lookup_variant, sizeof(task->lookup_variant), "captured_timeline_ui_model_shared_ptr");
                wsprintfA(task->lookup_key, "model=0x%I64x ctl=0x%I64x",
                          (unsigned __int64)model_sp[0],
                          (unsigned __int64)model_sp[1]);
            } else {
                int64_t ctl = ((FnCall1)(base + WX_OFF_WX_ALLOC))(944);
                if (!ctl) {
                    task->captured_model_invalid = 1;
                } else {
                    ZeroMemory((void *)ctl, 944);
                    *(int64_t *)ctl = base + WX_OFF_SNS_SET_PRIVATE_UI_MODEL_CTL_VT;
                    *(int64_t *)(ctl + 8) = 0x100000001LL;
                    model_sp[0] = ctl + 16;
                    model_sp[1] = ctl;
                    ((SnsSetPrivateUiModelCopyFn)(base + WX_OFF_SNS_SET_PRIVATE_UI_MODEL_COPY))(
                        model_sp[0],
                        (int64_t)task->captured_model_ptr,
                        0);
                    task->model_found = 1;
                    if (task->model_source != 92) task->model_source = 90;
                    task->captured_model_used = 1;
                    if (task->model_source == 92) {
                        safe_copy(task->lookup_variant,
                                  sizeof(task->lookup_variant),
                                  "validated_ui_model_copy");
                    } else {
                        safe_copy(task->lookup_variant,
                                  sizeof(task->lookup_variant),
                                  "captured_timeline_ui_model_copy");
                    }
                    wsprintfA(task->lookup_key, "src=0x%I64x model=0x%I64x/0x%I64x",
                              (unsigned __int64)task->captured_model_ptr,
                              (unsigned __int64)model_sp[0],
                              (unsigned __int64)model_sp[1]);
                }
            }
        }
        if (!task->model_found || !model_sp[0]) {
            if (set_visibility) {
                task->phase = 24;
                task->call_result = 0;
                task->wait_status = WAIT_OBJECT_0;
                InterlockedExchange(&task->status, -8);
                __leave;
            }
            int64_t sns_id_value = (int64_t)task->feed_id;
            int64_t direct_model_sp[2] = {(int64_t)&sns_id_value, 0};
            task->phase = 24;
            task->direct_objectop_used = 1;
            task->high_entry_result =
                ((SnsDeleteObjectOpEntryFn)(base + WX_OFF_SNS_DELETE_OBJECT_OP_ENTRY))(
                    manager,
                    direct_model_sp);
            task->call_result = (int64_t)task->high_entry_result;
            task->wait_status = WAIT_OBJECT_0;
            task->phase = 25;
            InterlockedExchange(&task->status, task->high_entry_result ? 1 : -9);
            __leave;
        }

        task->phase = 24;
        if (set_visibility) {
            task->high_entry_result =
                ((SnsSetPrivateEntryFn)(base + WX_OFF_SNS_SET_PRIVATE_ENTRY))(
                    manager,
                    model_sp,
                    task->visibility_state);
        } else {
            task->high_entry_result =
                ((SnsDeleteEntryFn)(base + WX_OFF_SNS_DELETE_ENTRY))(
                    manager,
                    model_sp);
        }
        if (set_visibility && task->captured_model_used) {
            task->model_sp_consumed = 1;
            model_sp[0] = 0;
            model_sp[1] = 0;
        }
        task->call_result = (int64_t)task->high_entry_result;
        task->wait_status = WAIT_OBJECT_0;
        task->phase = 25;
        InterlockedExchange(&task->status, task->high_entry_result ? 1 : -9);
    }
    __except (task->seh_code = GetExceptionCode(),
#if defined(_M_X64)
              task->ctx_rcx = (uint64_t)GetExceptionInformation()->ContextRecord->Rcx,
              task->ctx_rdx = (uint64_t)GetExceptionInformation()->ContextRecord->Rdx,
              task->ctx_r8 = (uint64_t)GetExceptionInformation()->ContextRecord->R8,
              task->ctx_r9 = (uint64_t)GetExceptionInformation()->ContextRecord->R9,
#endif
              task->seh_addr = (uint64_t)GetExceptionInformation()->ExceptionRecord->ExceptionAddress,
              task->seh_fault = (GetExceptionInformation()->ExceptionRecord->NumberParameters > 1)
                                    ? (uint64_t)GetExceptionInformation()->ExceptionRecord->ExceptionInformation[1]
                                    : 0,
              EXCEPTION_EXECUTE_HANDLER) {
        task->wait_status = WAIT_FAILED;
        task->call_result = -1;
        InterlockedExchange(&task->status, -1);
    }

    sns_comment_release_sp(model_sp);
    wx_free_std_string_heap(base, (int64_t)key);
}

static void write_sns_comment_result(const char *seq,
                                     int ok,
                                     const char *message,
                                     const char *sns_id,
                                     unsigned __int64 feed_id,
                                     const char *username,
                                     const char *content,
                                     const char *client_id,
                                     const char *self_username,
                                     int wait_status,
                                     int64_t call_result,
                                     int phase,
                                     DWORD seh_code,
                                     uint64_t seh_addr,
                                     uint64_t seh_fault,
                                     uint64_t ctx_rcx,
                                     uint64_t ctx_rdx,
                                     uint64_t ctx_r8,
                                     uint64_t ctx_r9,
                                     int reply_enabled,
                                     const char *reply_username,
                                     const char *reply_nickname,
                                     unsigned int reply_comment_id,
                                     unsigned __int64 reply_comment_64id,
                                     int reply_source,
                                     int reply_type,
                                     int reply_create_time) {
    WCHAR path[1024];
    char json[8192];
    char num[64];
    char *p = json;
    char *end = json + sizeof(json) - 1;
    wsprintfW(path, L"%s\\result_%S.json", CONTROL_DIR, seq ? seq : "");
    append_raw(&p, end, "{\"ok\":");
    append_raw(&p, end, ok ? "true" : "false");
    append_raw(&p, end, ",\"seq\":\"");
    append_escaped(&p, end, seq ? seq : "");
    append_raw(&p, end, "\",\"command\":\"sns_comment\"");
    append_raw(&p, end, ",\"path\":\"native_sns_comment_request_send\"");
    append_raw(&p, end, ",\"endpoint\":\"/cgi-bin/micromsg-bin/mmsnscomment\"");
    append_raw(&p, end, ",\"cgiType\":213");
    append_raw(&p, end, ",\"requestProto\":\"micromsg.SnsCommentRequest\"");
    append_raw(&p, end, ",\"contentRequestProto\":\"micromsg.SnsCommentContentRequest\"");
    append_raw(&p, end, ",\"commentContentProto\":\"micromsg.SnsCommentContent\"");
    append_raw(&p, end, ",\"upperEntry\":\"sub_184CA29F0\"");
    append_raw(&p, end, ",\"sendWrapper\":\"sub_182BCF740\"");
    append_raw(&p, end, ",\"requestInit\":\"sub_184BC9750\"");
    append_raw(&p, end, ",\"requestDtor\":\"sub_184BFB7B0\"");
    append_raw(&p, end, ",\"commentType\":2,\"opCode\":0");
    append_raw(&p, end, ",\"message\":\"");
    append_escaped(&p, end, message ? message : "");
    append_raw(&p, end, "\",\"snsId\":\"");
    append_escaped(&p, end, sns_id ? sns_id : "");
    append_raw(&p, end, "\",\"feedId\":\"0x");
    wsprintfA(num, "%I64x", (unsigned __int64)feed_id);
    append_raw(&p, end, num);
    append_raw(&p, end, "\",\"username\":\"");
    append_escaped(&p, end, username ? username : "");
    append_raw(&p, end, "\",\"content\":\"");
    append_escaped(&p, end, content ? content : "");
    append_raw(&p, end, "\",\"clientId\":\"");
    append_escaped(&p, end, client_id ? client_id : "");
    append_raw(&p, end, "\",\"selfUsername\":\"");
    append_escaped(&p, end, self_username ? self_username : "");
    append_raw(&p, end, "\",\"replyEnabled\":");
    append_raw(&p, end, reply_enabled ? "true" : "false");
    if (reply_enabled) {
        append_raw(&p, end, ",\"replyTarget\":{\"commentId\":");
        wsprintfA(num, "%u", (unsigned int)reply_comment_id);
        append_raw(&p, end, num);
        append_raw(&p, end, ",\"comment64Id\":\"0x");
        wsprintfA(num, "%I64x", (unsigned __int64)reply_comment_64id);
        append_raw(&p, end, num);
        append_raw(&p, end, "\",\"username\":\"");
        append_escaped(&p, end, reply_username ? reply_username : "");
        append_raw(&p, end, "\",\"nickname\":\"");
        append_escaped(&p, end, reply_nickname ? reply_nickname : "");
        append_raw(&p, end, "\",\"source\":");
        wsprintfA(num, "%d", reply_source);
        append_raw(&p, end, num);
        append_raw(&p, end, ",\"type\":");
        wsprintfA(num, "%d", reply_type);
        append_raw(&p, end, num);
        append_raw(&p, end, ",\"createTime\":");
        wsprintfA(num, "%d", reply_create_time);
        append_raw(&p, end, num);
        append_raw(&p, end, "}");
    }
    append_raw(&p, end, ",\"staticEvidence\":\"reply target offsets: id +0x00, id64 +0x08, username +0x10, nickname +0x30, source +0x70, type +0x74, create_time +0x78; send path sub_184CA29F0\"");
    append_raw(&p, end, ",\"waitStatus\":");
    wsprintfA(num, "%d", wait_status);
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"callResult\":");
    wsprintfA(num, "%I64d", (long long)call_result);
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"phase\":");
    wsprintfA(num, "%d", phase);
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"sehCode\":");
    wsprintfA(num, "%lu", (unsigned long)seh_code);
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"sehAddr\":\"0x");
    wsprintfA(num, "%I64x", (unsigned __int64)seh_addr);
    append_raw(&p, end, num);
    append_raw(&p, end, "\",\"sehFault\":\"0x");
    wsprintfA(num, "%I64x", (unsigned __int64)seh_fault);
    append_raw(&p, end, num);
    append_raw(&p, end, "\",\"ctxRcx\":\"0x");
    wsprintfA(num, "%I64x", (unsigned __int64)ctx_rcx);
    append_raw(&p, end, num);
    append_raw(&p, end, "\",\"ctxRdx\":\"0x");
    wsprintfA(num, "%I64x", (unsigned __int64)ctx_rdx);
    append_raw(&p, end, num);
    append_raw(&p, end, "\",\"ctxR8\":\"0x");
    wsprintfA(num, "%I64x", (unsigned __int64)ctx_r8);
    append_raw(&p, end, num);
    append_raw(&p, end, "\",\"ctxR9\":\"0x");
    wsprintfA(num, "%I64x", (unsigned __int64)ctx_r9);
    append_raw(&p, end, num);
    append_raw(&p, end, "\"}");
    *p = 0;
    write_file_bytes(path, json, (DWORD)(p - json));
}

static void write_sns_like_result(const char *seq,
                                  int ok,
                                  const char *message,
                                  const SnsCommentTask *task,
                                  const char *sns_id,
                                  const char *username,
                                  unsigned __int64 feed_id,
                                  int wait_status) {
    WCHAR path[1024];
    char json[8192];
    char num[64];
    char *p = json;
    char *end = json + sizeof(json) - 1;
    wsprintfW(path, L"%s\\result_%S.json", CONTROL_DIR, seq ? seq : "");
    append_raw(&p, end, "{\"ok\":");
    append_raw(&p, end, ok ? "true" : "false");
    append_raw(&p, end, ",\"seq\":\"");
    append_escaped(&p, end, seq ? seq : "");
    append_raw(&p, end, "\",\"command\":\"sns_like\"");
    append_raw(&p, end, ",\"path\":\"");
    append_raw(&p, end, (task && task->high_path) ? "native_sns_like_official_entry" : "native_sns_like_request_send");
    append_raw(&p, end, "\"");
    append_raw(&p, end, ",\"endpoint\":\"/cgi-bin/micromsg-bin/mmsnscomment\"");
    append_raw(&p, end, ",\"cgiType\":213");
    append_raw(&p, end, ",\"requestProto\":\"micromsg.SnsCommentRequest\"");
    append_raw(&p, end, ",\"contentRequestProto\":\"micromsg.SnsCommentContentRequest\"");
    append_raw(&p, end, ",\"commentContentProto\":\"micromsg.SnsCommentContent\"");
    append_raw(&p, end, ",\"upperEntry\":\"sub_182BCCC70\"");
    append_raw(&p, end, ",\"ctxLookup\":\"sub_182BD43F0\"");
    append_raw(&p, end, ",\"ctxCopy\":\"sub_182BD46B0\"");
    append_raw(&p, end, ",\"ctxDtor\":\"sub_182BC80C0\"");
    append_raw(&p, end, ",\"likeProbeHookInstalled\":");
    append_raw(&p, end, sns_like_probe_hook_installed() ? "true" : "false");
    append_raw(&p, end, ",\"likeProbeCaptureCount\":");
    wsprintfA(num, "%ld", (long)sns_like_probe_capture_count());
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"likeProbeEnable\":\"bridge/control/sns_like_probe.enable\"");
    append_raw(&p, end, ",\"likeProbeLog\":\"bridge/control/sns_like_probe.jsonl\"");
    append_raw(&p, end, ",\"modelLookup\":\"sub_180EA6C10\"");
    append_raw(&p, end, ",\"alternateModelEntry\":\"sub_184CA29F0\"");
    append_raw(&p, end, ",\"sendWrapper\":\"sub_182BCF740\"");
    append_raw(&p, end, ",\"requestInit\":\"sub_184BC9750\"");
    append_raw(&p, end, ",\"requestDtor\":\"sub_184BFB7B0\"");
    append_raw(&p, end, ",\"commentType\":");
    wsprintfA(num, "%d", SNS_COMMENT_TYPE_LIKE);
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"unlikeCommentType\":");
    wsprintfA(num, "%d", SNS_COMMENT_TYPE_UNLIKE);
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"opCode\":0");
    append_raw(&p, end, ",\"message\":\"");
    append_escaped(&p, end, message ? message : "");
    append_raw(&p, end, "\",\"snsId\":\"");
    append_escaped(&p, end, task ? task->sns_id : (sns_id ? sns_id : ""));
    append_raw(&p, end, "\",\"feedId\":\"0x");
    wsprintfA(num, "%I64x", (unsigned __int64)(task ? task->feed_id : feed_id));
    append_raw(&p, end, num);
    append_raw(&p, end, "\",\"lookupKey\":\"");
    append_escaped(&p, end, task ? task->lookup_key : "");
    append_raw(&p, end, "\",\"lookupVariant\":\"");
    append_escaped(&p, end, task ? task->lookup_variant : "");
    append_raw(&p, end, "\",\"snsKey\":\"");
    append_escaped(&p, end, task ? task->sns_key : "");
    append_raw(&p, end, "\",\"username\":\"");
    append_escaped(&p, end, task ? task->username : (username ? username : ""));
    append_raw(&p, end, "\",\"clientId\":\"");
    append_escaped(&p, end, task ? task->client_id : "");
    append_raw(&p, end, "\",\"selfUsername\":\"");
    append_escaped(&p, end, task ? task->self_username : "");
    append_raw(&p, end, "\",\"selfUin\":");
    wsprintfA(num, "%lu", (unsigned long)(task ? task->self_uin : 0));
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"modelFound\":");
    append_raw(&p, end, (task && task->model_found) ? "true" : "false");
    append_raw(&p, end, ",\"directObjectOpUsed\":");
    append_raw(&p, end, (task && task->direct_objectop_used) ? "true" : "false");
    append_raw(&p, end, ",\"directObjectOpEntry\":\"sub_184C79A60\"");
    append_raw(&p, end, ",\"modelLookupAttempts\":");
    wsprintfA(num, "%d", task ? task->model_lookup_attempts : 0);
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"modelLikeFlag\":");
    wsprintfA(num, "%d", task ? task->model_like_flag : 0);
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"modelSource\":");
    wsprintfA(num, "%d", task ? task->model_source : 0);
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"alreadyLiked\":");
    append_raw(&p, end, (task && task->already_liked) ? "true" : "false");
    append_raw(&p, end, ",\"entrySkipped\":");
    append_raw(&p, end, (task && task->entry_skipped) ? "true" : "false");
    append_raw(&p, end, ",\"ctxFound\":");
    append_raw(&p, end, (task && task->ctx_found) ? "true" : "false");
    append_raw(&p, end, ",\"ctxKeyLen\":");
    wsprintfA(num, "%d", task ? task->ctx_key_len : 0);
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"highEntryResult\":");
    wsprintfA(num, "%u", (unsigned int)(task ? task->high_entry_result : 0));
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"waitStatus\":");
    wsprintfA(num, "%d", wait_status);
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"callResult\":");
    wsprintfA(num, "%I64d", (long long)(task ? task->call_result : 0));
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"phase\":");
    wsprintfA(num, "%d", task ? task->phase : 0);
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"sehCode\":");
    wsprintfA(num, "%lu", (unsigned long)(task ? task->seh_code : 0));
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"sehAddr\":\"0x");
    wsprintfA(num, "%I64x", (unsigned __int64)(task ? task->seh_addr : 0));
    append_raw(&p, end, num);
    append_raw(&p, end, "\",\"sehFault\":\"0x");
    wsprintfA(num, "%I64x", (unsigned __int64)(task ? task->seh_fault : 0));
    append_raw(&p, end, num);
    append_raw(&p, end, "\",\"ctxRcx\":\"0x");
    wsprintfA(num, "%I64x", (unsigned __int64)(task ? task->ctx_rcx : 0));
    append_raw(&p, end, num);
    append_raw(&p, end, "\",\"ctxRdx\":\"0x");
    wsprintfA(num, "%I64x", (unsigned __int64)(task ? task->ctx_rdx : 0));
    append_raw(&p, end, num);
    append_raw(&p, end, "\",\"ctxR8\":\"0x");
    wsprintfA(num, "%I64x", (unsigned __int64)(task ? task->ctx_r8 : 0));
    append_raw(&p, end, num);
    append_raw(&p, end, "\",\"ctxR9\":\"0x");
    wsprintfA(num, "%I64x", (unsigned __int64)(task ? task->ctx_r9 : 0));
    append_raw(&p, end, num);
    append_raw(&p, end, "\"}");
    *p = 0;
    write_file_bytes(path, json, (DWORD)(p - json));
}

static void write_sns_delete_result(const char *seq,
                                    int ok,
                                    const char *message,
                                    const SnsCommentTask *task,
                                    const char *sns_id,
                                    const char *username,
                                    unsigned __int64 feed_id,
                                    int wait_status) {
    WCHAR path[1024];
    char json[8192];
    char num[64];
    char *p = json;
    char *end = json + sizeof(json) - 1;
    wsprintfW(path, L"%s\\result_%S.json", CONTROL_DIR, seq ? seq : "");
    append_raw(&p, end, "{\"ok\":");
    append_raw(&p, end, ok ? "true" : "false");
    append_raw(&p, end, ",\"seq\":\"");
    append_escaped(&p, end, seq ? seq : "");
    append_raw(&p, end, "\",\"command\":\"sns_delete\"");
    append_raw(&p, end, ",\"path\":\"native_sns_delete_official_entry\"");
    append_raw(&p, end, ",\"endpoint\":\"/cgi-bin/micromsg-bin/mmsnsobjectop\"");
    append_raw(&p, end, ",\"cgiType\":218");
    append_raw(&p, end, ",\"requestProto\":\"micromsg.SnsObjectOpRequest\"");
    append_raw(&p, end, ",\"objectOpProto\":\"micromsg.SnsObjectOp\"");
    append_raw(&p, end, ",\"upperEntry\":\"sub_182BDF480\"");
    append_raw(&p, end, ",\"objectOpEntry\":\"sub_184C79A60\"");
    append_raw(&p, end, ",\"sendWrapper\":\"sub_184C78B70\"");
    append_raw(&p, end, ",\"staticEvidence\":\"sub_184C79A60 writes SnsObjectOp +8 from model snsId and +24=1, then sub_184C78B70 sends SnsObjectOpRequest field3 to mmsnsobjectop; serializer sub_184BFF2C0 proves +8 field1 and +24 field2\"");
    append_raw(&p, end, ",\"message\":\"");
    append_escaped(&p, end, message ? message : "");
    append_raw(&p, end, "\",\"snsId\":\"");
    append_escaped(&p, end, task ? task->sns_id : (sns_id ? sns_id : ""));
    append_raw(&p, end, "\",\"feedId\":\"0x");
    wsprintfA(num, "%I64x", (unsigned __int64)(task ? task->feed_id : feed_id));
    append_raw(&p, end, num);
    append_raw(&p, end, "\",\"lookupKey\":\"");
    append_escaped(&p, end, task ? task->lookup_key : "");
    append_raw(&p, end, "\",\"lookupVariant\":\"");
    append_escaped(&p, end, task ? task->lookup_variant : "");
    append_raw(&p, end, "\",\"snsKey\":\"");
    append_escaped(&p, end, task ? task->sns_key : "");
    append_raw(&p, end, "\",\"username\":\"");
    append_escaped(&p, end, task ? task->username : (username ? username : ""));
    append_raw(&p, end, "\",\"selfUsername\":\"");
    append_escaped(&p, end, task ? task->self_username : "");
    append_raw(&p, end, "\",\"selfUin\":");
    wsprintfA(num, "%lu", (unsigned long)(task ? task->self_uin : 0));
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"modelFound\":");
    append_raw(&p, end, (task && task->model_found) ? "true" : "false");
    append_raw(&p, end, ",\"modelLookupAttempts\":");
    wsprintfA(num, "%d", task ? task->model_lookup_attempts : 0);
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"modelSource\":");
    wsprintfA(num, "%d", task ? task->model_source : 0);
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"deleteEntryResult\":");
    wsprintfA(num, "%u", (unsigned int)(task ? task->high_entry_result : 0));
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"waitStatus\":");
    wsprintfA(num, "%d", wait_status);
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"callResult\":");
    wsprintfA(num, "%I64d", (long long)(task ? task->call_result : 0));
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"phase\":");
    wsprintfA(num, "%d", task ? task->phase : 0);
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"sehCode\":");
    wsprintfA(num, "%lu", (unsigned long)(task ? task->seh_code : 0));
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"sehAddr\":\"0x");
    wsprintfA(num, "%I64x", (unsigned __int64)(task ? task->seh_addr : 0));
    append_raw(&p, end, num);
    append_raw(&p, end, "\",\"sehFault\":\"0x");
    wsprintfA(num, "%I64x", (unsigned __int64)(task ? task->seh_fault : 0));
    append_raw(&p, end, num);
    append_raw(&p, end, "\",\"ctxRcx\":\"0x");
    wsprintfA(num, "%I64x", (unsigned __int64)(task ? task->ctx_rcx : 0));
    append_raw(&p, end, num);
    append_raw(&p, end, "\",\"ctxRdx\":\"0x");
    wsprintfA(num, "%I64x", (unsigned __int64)(task ? task->ctx_rdx : 0));
    append_raw(&p, end, num);
    append_raw(&p, end, "\",\"ctxR8\":\"0x");
    wsprintfA(num, "%I64x", (unsigned __int64)(task ? task->ctx_r8 : 0));
    append_raw(&p, end, num);
    append_raw(&p, end, "\",\"ctxR9\":\"0x");
    wsprintfA(num, "%I64x", (unsigned __int64)(task ? task->ctx_r9 : 0));
    append_raw(&p, end, num);
    append_raw(&p, end, "\"}");
    *p = 0;
    write_file_bytes(path, json, (DWORD)(p - json));
}

static void write_sns_set_visibility_result(const char *seq,
                                            int ok,
                                            const char *command,
                                            unsigned int visibility_state,
                                            const char *message,
                                            const SnsCommentTask *task,
                                            const char *sns_id,
                                            const char *username,
                                            unsigned __int64 feed_id,
                                            int wait_status) {
    WCHAR path[1024];
    char json[8192];
    char num[64];
    char *p = json;
    char *end = json + sizeof(json) - 1;
    wsprintfW(path, L"%s\\result_%S.json", CONTROL_DIR, seq ? seq : "");
    append_raw(&p, end, "{\"ok\":");
    append_raw(&p, end, ok ? "true" : "false");
    append_raw(&p, end, ",\"seq\":\"");
    append_escaped(&p, end, seq ? seq : "");
    append_raw(&p, end, "\",\"command\":\"");
    append_escaped(&p, end, command ? command : "sns_set_visibility");
    append_raw(&p, end, "\"");
    append_raw(&p, end, ",\"path\":\"native_sns_set_visibility_state_entry\"");
    append_raw(&p, end, ",\"endpoint\":\"/cgi-bin/micromsg-bin/mmsnsobjectop\"");
    append_raw(&p, end, ",\"cgiType\":218");
    append_raw(&p, end, ",\"requestProto\":\"micromsg.SnsObjectOpRequest\"");
    append_raw(&p, end, ",\"objectOpProto\":\"micromsg.SnsObjectOp\"");
    append_raw(&p, end, ",\"upperEntry\":\"sub_182BDE1F0\"");
    append_raw(&p, end, ",\"upperEntryOffset\":\"0x2bde1f0\"");
    append_raw(&p, end, ",\"objectOpEntry\":\"sub_184C79BF0\"");
    append_raw(&p, end, ",\"objectOpEntryOffset\":\"0x4c79bf0\"");
    append_raw(&p, end, ",\"sendWrapper\":\"sub_184C78B70\"");
    append_raw(&p, end, ",\"opCode\":4");
    append_raw(&p, end, ",\"visibilityState\":");
    wsprintfA(num, "%u", (unsigned int)(task ? task->visibility_state : visibility_state));
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"modelValidateEntry\":\"sub_183D9CB50\"");
    append_raw(&p, end, ",\"modelValidateEntryOffset\":\"0x3d9cb50\"");
    append_raw(&p, end, ",\"modelValidateCaptureHookInstalled\":");
    append_raw(&p, end, InterlockedCompareExchange(&g_sns_model_validate_capture_hook.installed, 0, 0) ? "true" : "false");
    append_raw(&p, end, ",\"modelValidateCaptureCount\":");
    wsprintfA(num, "%ld", (long)InterlockedCompareExchange(&g_sns_model_validate_capture_hook.capture_count, 0, 0));
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"staticEvidence\":\"sub_183D9CB50 is the official UI model validator used by sub_183B6DB30 and sub_182BDE1F0; the bridge captures only models that pass this validator and whose first qword equals the target snsId, then sub_183D9A450 copies that model into the same off_18849D478 shared_ptr layout used by UI. sub_183DE2D00 builds the UI visibility-change task, sub_183B20240 writes that request state to ctx+32, and sub_183B6DB30 calls sub_182BDE1F0(manager,copied_model,*(ctx+32)); sub_182BDE1F0 passes *(*(manager+1064)+16) to sub_184C79BF0; sub_184C79BF0 reads **model as snsId, writes SnsObjectOp +24=4, serializes the request state payload, then sub_184C78B70 sends mmsnsobjectop. ContentObject privacyType parser maps 0 to 1 and model+552==2 is the private marker, so state 1 is public/normal and state 2 is only-self/private.\"");
    append_raw(&p, end, ",\"message\":\"");
    append_escaped(&p, end, message ? message : "");
    append_raw(&p, end, "\",\"snsId\":\"");
    append_escaped(&p, end, task ? task->sns_id : (sns_id ? sns_id : ""));
    append_raw(&p, end, "\",\"feedId\":\"0x");
    wsprintfA(num, "%I64x", (unsigned __int64)(task ? task->feed_id : feed_id));
    append_raw(&p, end, num);
    append_raw(&p, end, "\",\"lookupKey\":\"");
    append_escaped(&p, end, task ? task->lookup_key : "");
    append_raw(&p, end, "\",\"lookupVariant\":\"");
    append_escaped(&p, end, task ? task->lookup_variant : "");
    append_raw(&p, end, "\",\"snsKey\":\"");
    append_escaped(&p, end, task ? task->sns_key : "");
    append_raw(&p, end, "\",\"username\":\"");
    append_escaped(&p, end, task ? task->username : (username ? username : ""));
    append_raw(&p, end, "\",\"selfUsername\":\"");
    append_escaped(&p, end, task ? task->self_username : "");
    append_raw(&p, end, "\",\"selfUin\":");
    wsprintfA(num, "%lu", (unsigned long)(task ? task->self_uin : 0));
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"modelFound\":");
    append_raw(&p, end, (task && task->model_found) ? "true" : "false");
    append_raw(&p, end, ",\"modelLookupAttempts\":");
    wsprintfA(num, "%d", task ? task->model_lookup_attempts : 0);
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"modelSource\":");
    wsprintfA(num, "%d", task ? task->model_source : 0);
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"directObjectOpUsed\":");
    append_raw(&p, end, (task && task->direct_objectop_used) ? "true" : "false");
    append_raw(&p, end, ",\"capturedModelUsed\":");
    append_raw(&p, end, (task && task->captured_model_used) ? "true" : "false");
    append_raw(&p, end, ",\"capturedModelInvalid\":");
    append_raw(&p, end, (task && task->captured_model_invalid) ? "true" : "false");
    append_raw(&p, end, ",\"capturedModelPtr\":\"0x");
    wsprintfA(num, "%I64x", (unsigned __int64)(task ? task->captured_model_ptr : 0));
    append_raw(&p, end, num);
    append_raw(&p, end, "\",\"capturedModelCtl\":\"0x");
    wsprintfA(num, "%I64x", (unsigned __int64)(task ? task->captured_model_ctl : 0));
    append_raw(&p, end, num);
    append_raw(&p, end, "\",\"modelSpConsumed\":");
    append_raw(&p, end, (task && task->model_sp_consumed) ? "true" : "false");
    append_raw(&p, end, ",\"setPrivateEntryResult\":");
    wsprintfA(num, "%u", (unsigned int)(task ? task->high_entry_result : 0));
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"objectOpOutModel\":");
    wsprintfA(num, "%u", (unsigned int)(task ? task->objectop_out_model : 0));
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"localUpdateResult\":");
    wsprintfA(num, "%u", (unsigned int)(task ? task->local_update_result : 0));
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"waitStatus\":");
    wsprintfA(num, "%d", wait_status);
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"callResult\":");
    wsprintfA(num, "%I64d", (long long)(task ? task->call_result : 0));
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"phase\":");
    wsprintfA(num, "%d", task ? task->phase : 0);
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"sehCode\":");
    wsprintfA(num, "%lu", (unsigned long)(task ? task->seh_code : 0));
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"sehAddr\":\"0x");
    wsprintfA(num, "%I64x", (unsigned __int64)(task ? task->seh_addr : 0));
    append_raw(&p, end, num);
    append_raw(&p, end, "\",\"sehFault\":\"0x");
    wsprintfA(num, "%I64x", (unsigned __int64)(task ? task->seh_fault : 0));
    append_raw(&p, end, num);
    append_raw(&p, end, "\",\"ctxRcx\":\"0x");
    wsprintfA(num, "%I64x", (unsigned __int64)(task ? task->ctx_rcx : 0));
    append_raw(&p, end, num);
    append_raw(&p, end, "\",\"ctxRdx\":\"0x");
    wsprintfA(num, "%I64x", (unsigned __int64)(task ? task->ctx_rdx : 0));
    append_raw(&p, end, num);
    append_raw(&p, end, "\",\"ctxR8\":\"0x");
    wsprintfA(num, "%I64x", (unsigned __int64)(task ? task->ctx_r8 : 0));
    append_raw(&p, end, num);
    append_raw(&p, end, "\",\"ctxR9\":\"0x");
    wsprintfA(num, "%I64x", (unsigned __int64)(task ? task->ctx_r9 : 0));
    append_raw(&p, end, num);
    append_raw(&p, end, "\"}");
    *p = 0;
    write_file_bytes(path, json, (DWORD)(p - json));
}

int sns_comment_to_result(const char *seq,
                          const char *sns_id,
                          const char *username,
                          const char *content,
                          const char *reply_username,
                          const char *reply_nickname,
                          const char *reply_comment_id,
                          const char *reply_comment_64id,
                          int reply_source,
                          int reply_type,
                          int reply_create_time,
                          int wait_ms) {
    const SIZE_T arena_size = 0x9000;
    BYTE *arena;
    BYTE *asm_addr;
    SnsCommentTask *task;
    int asm_len;
    LONG status;
    int loops;
    int ok;
    int phase = 0;
    unsigned __int64 feed_id;
    int reply_enabled;
    unsigned __int64 parsed_reply_id;
    unsigned __int64 parsed_reply_64id;

    if (!seq || !*seq) return 0;
    reply_enabled = ((reply_username && *reply_username) ||
                     (reply_comment_id && *reply_comment_id) ||
                     (reply_comment_64id && *reply_comment_64id) ||
                     reply_type || reply_create_time);
    parsed_reply_id = sns_comment_parse_id(reply_comment_id ? reply_comment_id : "");
    parsed_reply_64id = sns_comment_parse_id(reply_comment_64id ? reply_comment_64id : "");
    if (!sns_id || !*sns_id) {
        write_sns_comment_result(seq, 0, "missing_sns_id", sns_id, 0, username, content,
                                 "", "", WAIT_FAILED, 0, phase, 0, 0, 0, 0, 0, 0, 0,
                                 SNS_COMMENT_NO_REPLY_RESULT_ARGS);
        return 0;
    }
    if (!username || !*username) {
        write_sns_comment_result(seq, 0, "missing_username", sns_id, 0, username, content,
                                 "", "", WAIT_FAILED, 0, phase, 0, 0, 0, 0, 0, 0, 0,
                                 SNS_COMMENT_NO_REPLY_RESULT_ARGS);
        return 0;
    }
    if (!content || !*content) {
        write_sns_comment_result(seq, 0, "missing_content", sns_id, 0, username, content,
                                 "", "", WAIT_FAILED, 0, phase, 0, 0, 0, 0, 0, 0, 0,
                                 SNS_COMMENT_NO_REPLY_RESULT_ARGS);
        return 0;
    }
    feed_id = sns_comment_parse_id(sns_id);
    if (!feed_id) {
        write_sns_comment_result(seq, 0, "invalid_sns_id", sns_id, 0, username, content,
                                 "", "", WAIT_FAILED, 0, phase, 0, 0, 0, 0, 0, 0, 0,
                                 SNS_COMMENT_NO_REPLY_RESULT_ARGS);
        return 0;
    }
    if (reply_enabled && (!reply_username || !*reply_username)) {
        write_sns_comment_result(seq, 0, "missing_reply_username", sns_id, feed_id, username, content,
                                 "", "", WAIT_FAILED, 0, phase, 0, 0, 0, 0, 0, 0, 0,
                                 SNS_COMMENT_NO_REPLY_RESULT_ARGS);
        return 0;
    }
    if (reply_enabled && !parsed_reply_id && !parsed_reply_64id) {
        write_sns_comment_result(seq, 0, "invalid_reply_comment_id", sns_id, feed_id, username, content,
                                 "", "", WAIT_FAILED, 0, phase, 0, 0, 0, 0, 0, 0, 0,
                                 SNS_COMMENT_NO_REPLY_RESULT_ARGS);
        return 0;
    }
    if (reply_enabled && reply_type <= 0) {
        write_sns_comment_result(seq, 0, "missing_reply_type", sns_id, feed_id, username, content,
                                 "", "", WAIT_FAILED, 0, phase, 0, 0, 0, 0, 0, 0, 0,
                                 SNS_COMMENT_NO_REPLY_RESULT_ARGS);
        return 0;
    }
    if (reply_enabled && reply_create_time <= 0) {
        write_sns_comment_result(seq, 0, "missing_reply_create_time", sns_id, feed_id, username, content,
                                 "", "", WAIT_FAILED, 0, phase, 0, 0, 0, 0, 0, 0, 0,
                                 SNS_COMMENT_NO_REPLY_RESULT_ARGS);
        return 0;
    }
    if (!wxb_ensure_weixin_module()) {
        write_sns_comment_result(seq, 0, "weixin_module_not_found", sns_id, feed_id, username, content,
                                 "", "", WAIT_FAILED, 0, 0, 0, 0, 0, 0, 0, 0, 0,
                                 SNS_COMMENT_NO_REPLY_RESULT_ARGS);
        return 0;
    }

    arena = (BYTE *)VirtualAlloc(NULL, arena_size, MEM_COMMIT | MEM_RESERVE, PAGE_EXECUTE_READWRITE);
    if (!arena) {
        write_sns_comment_result(seq, 0, "arena_alloc_failed", sns_id, feed_id, username, content,
                                 "", "", WAIT_FAILED, -8, 0, 0, 0, 0, 0, 0, 0, 0,
                                 SNS_COMMENT_NO_REPLY_RESULT_ARGS);
        return 0;
    }
    ZeroMemory(arena, arena_size);
    asm_addr = arena + 0x200;
    task = (SnsCommentTask *)(arena + 0x1000);
    task->status = 0;
    task->wait_status = WAIT_FAILED;
    task->feed_id = feed_id;
    task->action_type = SNS_COMMENT_TYPE_TEXT;
    safe_copy(task->sns_id, sizeof(task->sns_id), sns_id);
    safe_copy(task->username, sizeof(task->username), username);
    safe_copy(task->content, sizeof(task->content), content);
    if (reply_enabled) {
        task->reply_enabled = 1;
        task->reply_comment_id = (unsigned int)parsed_reply_id;
        task->reply_comment_64id = parsed_reply_64id;
        task->reply_source = reply_source;
        task->reply_type = reply_type;
        task->reply_create_time = reply_create_time;
        safe_copy(task->reply_username, sizeof(task->reply_username), reply_username);
        safe_copy(task->reply_nickname, sizeof(task->reply_nickname), reply_nickname ? reply_nickname : "");
    }

    asm_len = build_sns_comment_queue_asm(asm_addr, (int64_t)task, (int64_t)sns_comment_worker);
    FlushInstructionCache(GetCurrentProcess(), asm_addr, asm_len);
    queue_send((int64_t)asm_addr);

    if (wait_ms <= 0) wait_ms = 20000;
    loops = wait_ms / 10;
    if (loops < 1) loops = 1;
    for (int i = 0; i < loops; i++) {
        status = InterlockedCompareExchange(&task->status, 0, 0);
        if (status == 1 || status < 0) break;
        Sleep(10);
    }
    status = InterlockedCompareExchange(&task->status, 0, 0);
    if (status == 0 || status == 2) {
        InterlockedCompareExchange(&task->status, -2, status);
        status = InterlockedCompareExchange(&task->status, 0, 0);
    }
    ok = (status == 1);

    write_sns_comment_result(seq,
                             ok,
                             ok ? "sns_comment submitted" :
                                  (status == -2 ? "sns_comment timeout" :
                                   status == -6 ? "cgi_context_not_found" :
                                   status == -5 ? "comment_content_build_failed" :
                                   status == -4 ? "request_client_id_build_failed" :
                                   status == -3 ? "request_alloc_failed" :
                                   status == -1 ? "sns_comment exception" :
                                   "sns_comment failed"),
                             task->sns_id,
                             task->feed_id,
                             task->username,
                             task->content,
                             task->client_id,
                             task->self_username,
                             (status == -2) ? WAIT_TIMEOUT : (int)task->wait_status,
                             task->call_result,
                             task->phase,
                             task->seh_code,
                             task->seh_addr,
                             task->seh_fault,
                             task->ctx_rcx,
                             task->ctx_rdx,
                             task->ctx_r8,
                             task->ctx_r9,
                             task->reply_enabled,
                             task->reply_username,
                             task->reply_nickname,
                             task->reply_comment_id,
                             task->reply_comment_64id,
                             task->reply_source,
                             task->reply_type,
                             task->reply_create_time);
    retain_arena(arena);
    return ok;
}

int sns_like_to_result(const char *seq,
                       const char *sns_id,
                       const char *username,
                       const char *sns_key,
                       int wait_ms) {
    const SIZE_T arena_size = 0x9000;
    BYTE *arena;
    BYTE *asm_addr;
    SnsCommentTask *task;
    int asm_len;
    LONG status;
    int loops;
    int ok;
    int phase = 0;
    unsigned __int64 feed_id;

    if (!seq || !*seq) return 0;
    if (!sns_id || !*sns_id) {
        write_sns_like_result(seq, 0, "missing_sns_id", 0, sns_id, username, 0, WAIT_FAILED);
        return 0;
    }
    if (!username || !*username) {
        write_sns_like_result(seq, 0, "missing_username", 0, sns_id, username, 0, WAIT_FAILED);
        return 0;
    }
    feed_id = sns_comment_parse_id(sns_id);
    if (!feed_id) {
        write_sns_like_result(seq, 0, "invalid_sns_id", 0, sns_id, username, 0, WAIT_FAILED);
        return 0;
    }
    if (!wxb_ensure_weixin_module()) {
        write_sns_like_result(seq, 0, "weixin_module_not_found", 0, sns_id, username, feed_id, WAIT_FAILED);
        return 0;
    }

    arena = (BYTE *)VirtualAlloc(NULL, arena_size, MEM_COMMIT | MEM_RESERVE, PAGE_EXECUTE_READWRITE);
    if (!arena) {
        write_sns_like_result(seq, 0, "arena_alloc_failed", 0, sns_id, username, feed_id, WAIT_FAILED);
        return 0;
    }
    ZeroMemory(arena, arena_size);
    asm_addr = arena + 0x200;
    task = (SnsCommentTask *)(arena + 0x1000);
    task->status = 0;
    task->wait_status = WAIT_FAILED;
    task->feed_id = feed_id;
    task->action_type = SNS_COMMENT_TYPE_LIKE;
    task->high_path = 1;
    wsprintfA(task->lookup_key, "%I64u", (unsigned __int64)feed_id);
    safe_copy(task->sns_key, sizeof(task->sns_key), sns_key ? sns_key : "");
    safe_copy(task->sns_id, sizeof(task->sns_id), sns_id);
    safe_copy(task->username, sizeof(task->username), username);
    task->content[0] = 0;

    asm_len = build_sns_comment_queue_asm(asm_addr, (int64_t)task, (int64_t)sns_like_high_worker);
    FlushInstructionCache(GetCurrentProcess(), asm_addr, asm_len);
    queue_send((int64_t)asm_addr);

    if (wait_ms <= 0) wait_ms = 20000;
    loops = wait_ms / 10;
    if (loops < 1) loops = 1;
    for (int i = 0; i < loops; i++) {
        status = InterlockedCompareExchange(&task->status, 0, 0);
        if (status == 1 || status < 0) break;
        Sleep(10);
    }
    status = InterlockedCompareExchange(&task->status, 0, 0);
    if (status == 0 || status == 2) {
        InterlockedCompareExchange(&task->status, -2, status);
        status = InterlockedCompareExchange(&task->status, 0, 0);
    }
    ok = (status == 1);

    write_sns_like_result(seq,
                          ok,
                          ok ? (task->entry_skipped ?
                                "sns_like already liked; official entry skipped" :
                                "sns_like official entry invoked") :
                               (status == -2 ? "sns_like timeout" :
                                status == -10 ? "sns_like_manager_or_service_not_found" :
                                status == -8 ? "model_not_found_for_sns_id" :
                                status == -6 ? "cgi_context_not_found" :
                                status == -5 ? "like_content_build_failed" :
                                status == -4 ? "request_client_id_build_failed" :
                                status == -3 ? "request_alloc_failed" :
                                status == -1 ? "sns_like exception" :
                                "sns_like failed"),
                          task,
                          sns_id,
                          username,
                          feed_id,
                          (status == -2) ? WAIT_TIMEOUT : (int)task->wait_status);
    retain_arena(arena);
    return ok;
}

int sns_delete_to_result(const char *seq,
                         const char *sns_id,
                         const char *username,
                         const char *sns_key,
                         int wait_ms) {
    const SIZE_T arena_size = 0x9000;
    BYTE *arena;
    BYTE *asm_addr;
    SnsCommentTask *task;
    int asm_len;
    LONG status;
    int loops;
    int ok;
    unsigned __int64 feed_id;

    if (!seq || !*seq) return 0;
    if (!sns_id || !*sns_id) {
        write_sns_delete_result(seq, 0, "missing_sns_id", 0, sns_id, username, 0, WAIT_FAILED);
        return 0;
    }
    feed_id = sns_comment_parse_id(sns_id);
    if (!feed_id) {
        write_sns_delete_result(seq, 0, "invalid_sns_id", 0, sns_id, username, 0, WAIT_FAILED);
        return 0;
    }
    if (!wxb_ensure_weixin_module()) {
        write_sns_delete_result(seq, 0, "weixin_module_not_found", 0, sns_id, username, feed_id, WAIT_FAILED);
        return 0;
    }

    arena = (BYTE *)VirtualAlloc(NULL, arena_size, MEM_COMMIT | MEM_RESERVE, PAGE_EXECUTE_READWRITE);
    if (!arena) {
        write_sns_delete_result(seq, 0, "arena_alloc_failed", 0, sns_id, username, feed_id, WAIT_FAILED);
        return 0;
    }
    ZeroMemory(arena, arena_size);
    asm_addr = arena + 0x200;
    task = (SnsCommentTask *)(arena + 0x1000);
    task->status = 0;
    task->wait_status = WAIT_FAILED;
    task->feed_id = feed_id;
    task->action_type = 0;
    task->high_path = 1;
    wsprintfA(task->lookup_key, "%I64u", (unsigned __int64)feed_id);
    safe_copy(task->sns_key, sizeof(task->sns_key), sns_key ? sns_key : "");
    safe_copy(task->sns_id, sizeof(task->sns_id), sns_id);
    safe_copy(task->username, sizeof(task->username), username ? username : "");

    asm_len = build_sns_comment_queue_asm(asm_addr, (int64_t)task, (int64_t)sns_delete_worker);
    FlushInstructionCache(GetCurrentProcess(), asm_addr, asm_len);
    queue_send((int64_t)asm_addr);

    if (wait_ms <= 0) wait_ms = 20000;
    loops = wait_ms / 10;
    if (loops < 1) loops = 1;
    for (int i = 0; i < loops; i++) {
        status = InterlockedCompareExchange(&task->status, 0, 0);
        if (status == 1 || status < 0) break;
        Sleep(10);
    }
    status = InterlockedCompareExchange(&task->status, 0, 0);
    if (status == 0 || status == 2) {
        InterlockedCompareExchange(&task->status, -2, status);
        status = InterlockedCompareExchange(&task->status, 0, 0);
    }
    ok = (status == 1);

    write_sns_delete_result(seq,
                            ok,
                            ok ? "sns_delete official entry invoked" :
                                 (status == -2 ? "sns_delete timeout" :
                                  status == -10 ? "sns_delete_manager_or_service_not_found" :
                                  status == -9 ? "sns_delete_entry_returned_false" :
                                  status == -8 ? "model_not_found_for_sns_id" :
                                  status == -1 ? "sns_delete exception" :
                                  "sns_delete failed"),
                            task,
                            sns_id,
                            username,
                            feed_id,
                            (status == -2) ? WAIT_TIMEOUT : (int)task->wait_status);
    retain_arena(arena);
    return ok;
}

static int sns_set_visibility_to_result_as(const char *command,
                                           const char *seq,
                                           const char *sns_id,
                                           const char *username,
                                           const char *sns_key,
                                           const char *model_ptr,
                                           const char *model_ctl,
                                           unsigned int visibility_state,
                                           int wait_ms) {
    const SIZE_T arena_size = 0x9000;
    BYTE *arena;
    BYTE *asm_addr;
    SnsCommentTask *task;
    int asm_len;
    LONG status;
    int loops;
    int ok;
    unsigned __int64 feed_id;

    if (!seq || !*seq) return 0;
    if (visibility_state != WX_SNS_VISIBILITY_PUBLIC_STATE &&
        visibility_state != WX_SNS_VISIBILITY_PRIVATE_STATE) {
        write_sns_set_visibility_result(seq, 0, command, visibility_state,
                                        "invalid_visibility_state", 0, sns_id, username, 0, WAIT_FAILED);
        return 0;
    }
    if (!sns_id || !*sns_id) {
        write_sns_set_visibility_result(seq, 0, command, visibility_state,
                                        "missing_sns_id", 0, sns_id, username, 0, WAIT_FAILED);
        return 0;
    }
    feed_id = sns_comment_parse_id(sns_id);
    if (!feed_id) {
        write_sns_set_visibility_result(seq, 0, command, visibility_state,
                                        "invalid_sns_id", 0, sns_id, username, 0, WAIT_FAILED);
        return 0;
    }
    if (!wxb_ensure_weixin_module()) {
        write_sns_set_visibility_result(seq, 0, command, visibility_state,
                                        "weixin_module_not_found", 0, sns_id, username, feed_id, WAIT_FAILED);
        return 0;
    }

    arena = (BYTE *)VirtualAlloc(NULL, arena_size, MEM_COMMIT | MEM_RESERVE, PAGE_EXECUTE_READWRITE);
    if (!arena) {
        write_sns_set_visibility_result(seq, 0, command, visibility_state,
                                        "arena_alloc_failed", 0, sns_id, username, feed_id, WAIT_FAILED);
        return 0;
    }
    ZeroMemory(arena, arena_size);
    asm_addr = arena + 0x200;
    task = (SnsCommentTask *)(arena + 0x1000);
    task->status = 0;
    task->wait_status = WAIT_FAILED;
    task->feed_id = feed_id;
    task->action_type = 9;
    task->visibility_state = visibility_state;
    task->high_path = 1;
    wsprintfA(task->lookup_key, "%I64u", (unsigned __int64)feed_id);
    safe_copy(task->sns_key, sizeof(task->sns_key), sns_key ? sns_key : "");
    safe_copy(task->sns_id, sizeof(task->sns_id), sns_id);
    safe_copy(task->username, sizeof(task->username), username ? username : "");
    task->captured_model_ptr = sns_comment_parse_id(model_ptr ? model_ptr : "");
    task->captured_model_ctl = sns_comment_parse_id(model_ctl ? model_ctl : "");

    asm_len = build_sns_comment_queue_asm(asm_addr, (int64_t)task, (int64_t)sns_delete_worker);
    FlushInstructionCache(GetCurrentProcess(), asm_addr, asm_len);
    queue_send((int64_t)asm_addr);

    if (wait_ms <= 0) wait_ms = 20000;
    loops = wait_ms / 10;
    if (loops < 1) loops = 1;
    for (int i = 0; i < loops; i++) {
        status = InterlockedCompareExchange(&task->status, 0, 0);
        if (status == 1 || status < 0) break;
        Sleep(10);
    }
    status = InterlockedCompareExchange(&task->status, 0, 0);
    if (status == 0 || status == 2) {
        InterlockedCompareExchange(&task->status, -2, status);
        status = InterlockedCompareExchange(&task->status, 0, 0);
    }
    ok = (status == 1);

    write_sns_set_visibility_result(seq,
                                    ok,
                                    command,
                                    visibility_state,
                                    ok ? "sns_set_visibility official entry invoked" :
                                         (status == -2 ? "sns_set_visibility timeout" :
                                          status == -10 ? "sns_set_visibility_manager_or_service_not_found" :
                                          status == -9 ? "sns_set_visibility_entry_returned_false" :
                                          status == -8 ? "model_not_found_for_sns_id" :
                                          status == -1 ? "sns_set_visibility exception" :
                                          "sns_set_visibility failed"),
                                    task,
                                    sns_id,
                                    username,
                                    feed_id,
                                    (status == -2) ? WAIT_TIMEOUT : (int)task->wait_status);
    retain_arena(arena);
    return ok;
}

int sns_set_visibility_to_result(const char *seq,
                                 const char *sns_id,
                                 const char *username,
                                 const char *sns_key,
                                 const char *model_ptr,
                                 const char *model_ctl,
                                 unsigned int visibility_state,
                                 int wait_ms) {
    return sns_set_visibility_to_result_as("sns_set_visibility",
                                           seq,
                                           sns_id,
                                           username,
                                           sns_key,
                                           model_ptr,
                                           model_ctl,
                                           visibility_state,
                                           wait_ms);
}

int sns_set_private_to_result(const char *seq,
                              const char *sns_id,
                              const char *username,
                              const char *sns_key,
                              const char *model_ptr,
                              const char *model_ctl,
                              int wait_ms) {
    return sns_set_visibility_to_result_as("sns_set_private",
                                           seq,
                                           sns_id,
                                           username,
                                           sns_key,
                                           model_ptr,
                                           model_ctl,
                                           WX_SNS_VISIBILITY_PRIVATE_STATE,
                                           wait_ms);
}
