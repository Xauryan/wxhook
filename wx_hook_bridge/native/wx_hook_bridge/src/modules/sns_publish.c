/*
 * Module: SNS publish.
 *
 * WeChat 4.1.9.23:
 *   media models set PublishModel+24, so the normal first-publish branch is
 *   sub_183B1B290 -> sub_183B26140 -> sub_182BE2100. A new post then advances
 *   through sub_184CAD900 -> sub_184CB0920 -> sub_184CB2CC0.
 */

#include "../include/wx_bridge_sns_publish.h"
#include "../include/wx_bridge_file_io.h"
#include "../include/wx_bridge_memory.h"
#include "../include/wx_bridge_media_ops.h"
#include "../include/wx_bridge_native_call.h"
#include "../include/wx_bridge_offsets.h"
#include "../include/wx_bridge_paths.h"
#include "../include/wx_bridge_protocol.h"
#include "../include/wx_bridge_runtime.h"
#include "../include/wx_bridge_send_runtime.h"
#include "../include/wx_bridge_text.h"
#include "../include/wx_bridge_wechat_string.h"
#include <stdint.h>
#include <string.h>
#include <time.h>

#define SNS_POST_REQ_SIZE 416
#define SNS_POST_OUT_SIZE 64
#define SNS_POST_EXTRA_SIZE 32
#define SNS_POST_OPTS_SIZE 48
#define SNS_POST_XML_MAX 32768
#define SNS_PUBLISH_MODEL_CTL_SIZE 664
#define SNS_PUBLISH_WRAP_CTL_SIZE 216
#define SNS_MEDIA_ITEM_CTL_SIZE 432
#define SNS_THUMB_URL_CTL_SIZE 216
#define SNS_POST_BUILD_SEND_PATCH_LEN 27
#define SNS_STAGE_WPATH_MAX 1024
#ifndef CSIDL_APPDATA
#define CSIDL_APPDATA 0x001a
#endif

typedef int64_t (__fastcall *SnsNoArgsFn)(void);
typedef void (__fastcall *SnsSharedCtlFn)(int64_t ctl);
typedef unsigned int *(__fastcall *SnsPostSendFn)(unsigned int *out,
                                                  int64_t cgi_ctx,
                                                  int64_t req,
                                                  int64_t extra,
                                                  int64_t opts);
typedef int64_t (__fastcall *SnsOutFn)(int64_t out);
typedef int64_t (__fastcall *SnsOutFromFn)(int64_t in, int64_t out);
typedef unsigned char (__fastcall *SnsPublishEntryFn)(int64_t manager, int64_t shared_model);
typedef int64_t (__fastcall *SnsPublishPrepareModelFn)(int64_t manager, int64_t shared_model, int mode);
typedef int64_t (__fastcall *SnsPublishBuildPostFn)(int64_t out_post, int64_t shared_model);
typedef int64_t (__fastcall *SnsPublishRegisterPostFn)(int64_t service_queue, int64_t shared_post);
typedef double (__fastcall *SnsPostRunFn)(int64_t post_obj, int phase);
typedef int64_t (__fastcall *SnsPublishModelCtorFn)(int64_t model, unsigned int seed);
typedef int64_t (__fastcall *SnsPublishWrapInitFn)(int64_t wrapper, int64_t shared_model);
typedef int64_t (__fastcall *SnsVideoScanFn)(int64_t wrapper);
typedef int64_t (__fastcall *SnsQObjectRefTokenFn)(int64_t ctx);
typedef int64_t (__fastcall *SnsPublishAsyncRunFn)(int64_t wrapper, int64_t ctx_pair, int64_t cb_a, int64_t cb_b);
typedef int64_t (__fastcall *SnsGlobalServiceFn)(int64_t global);
typedef int64_t (__fastcall *SnsCtxGetterFn)(int64_t self);
typedef int64_t (__fastcall *SnsPublishCtxEnterFn)(int64_t ctx, int64_t shared_model);
typedef int64_t (__fastcall *SnsSharedOutMethodFn)(int64_t self, int64_t out_pair);
typedef int64_t (__fastcall *SnsContextInterfaceCastFn)(int64_t source,
                                                       int64_t out_pair,
                                                       int64_t interface_tag);
typedef void (__fastcall *SnsPublishImmediateScheduleFn)(int64_t context,
                                                         int64_t shared_model);
typedef void (__fastcall *SnsTraceVoidFn2)(int64_t a1, int64_t a2);
typedef int64_t (__fastcall *SnsStdStringInitFn)(int64_t slot);
typedef int64_t (__fastcall *SnsStdWStringCopyFn)(int64_t dst, int64_t src);
typedef int64_t (__fastcall *SnsPostBuildSendFn)(int64_t post_obj, int64_t out);
typedef int64_t (__fastcall *SnsTraceFn4)(int64_t a1, int64_t a2, int64_t a3, int64_t a4);
typedef int64_t (__fastcall *SnsTraceFn1)(int64_t a1);
typedef int64_t (__fastcall *SnsTraceFn2)(int64_t a1, int64_t a2);
typedef int64_t (__fastcall *SnsTraceFn3)(int64_t a1, int64_t a2, int64_t a3);
typedef int64_t (__fastcall *SnsTraceFn6)(int64_t a1, int64_t a2, int64_t a3, int64_t a4,
                                          int64_t a5, int64_t a6);
typedef int64_t (__fastcall *SnsTraceFn8)(int64_t a1, int64_t a2, int64_t a3, int64_t a4,
                                          int64_t a5, int64_t a6, int64_t a7, int64_t a8);
typedef HRESULT (WINAPI *SnsSHGetFolderPathWFn)(HWND hwnd, int csidl, HANDLE token, DWORD flags, LPWSTR path);

typedef struct SnsInlineHook {
    volatile LONG installed;
    BYTE orig[32];
    void *trampoline;
    int patch_len;
} SnsInlineHook;

typedef struct SnsPublishTask {
    volatile LONG status;
    int64_t call_result;
    int phase;
    DWORD wait_status;
    DWORD xml_size;
    DWORD seh_code;
    uint64_t seh_addr;
    uint64_t seh_fault;
    uint64_t ctx_rdi;
    uint64_t ctx_rcx;
    uint64_t ctx_rdx;
    uint64_t ctx_r8;
    uint64_t ctx_r9;
    int64_t model;
    int64_t model_ctl;
    int64_t shared_model[2];
    int64_t wrapper;
    int64_t wrapper_ctl;
    int64_t video_scan_ret;
    int64_t video_async_ret;
    int64_t video_ctx_token;
    int64_t video_publish_ctx;
    int64_t video_ctx_enter_ret;
    int64_t final_asm_addr;
    int64_t post_obj;
    int64_t post_ctl;
    int64_t manager;
    int model_flag24;
    int model_type48;
    int entry_calls;
    int64_t first_call_result;
    int has_image;
    int is_video;
    int video_upload_ready;
    int video_upload_wait_ms;
    volatile LONG phase1_seen;
    volatile LONG phase2_seen;
    volatile LONG phase3_seen;
    volatile LONG post_build_send_seen;
    volatile LONG cgi209_seen;
    volatile LONG cgi_sync_error;
    volatile LONG official_result_seen;
    volatile LONG official_result_code;
    volatile LONG response_seen;
    volatile LONG accept_results;
    volatile LONG video_worker_scheduled;
    DWORD phase3_tid;
    int64_t video_worker_context;
    int64_t video_worker_context_ctl;
    volatile LONG video_worker_completed;
    uint64_t cgi_out_error;
    uint64_t cgi_out_response;
    uint64_t cgi_out_response_ctl;
    uint64_t build_out_response;
    uint64_t build_out_response_ctl;
    uint64_t model_response;
    uint64_t model_response_ctl;
    int post_state456;
    int post_error460;
    int post_state464;
    int phase1_result_code;
    int phase2_result_code;
    int phase3_result_code;
    int post_paths_set;
    DWORD post_video_attr;
    DWORD post_thumb_attr;
    DWORD post_output_attr;
    int image_width;
    int image_height;
    int image_duration_ms;
    char content[4096];
    char image_path[2048];
    char image_md5[96];
    char image_cdn_url[1024];
    char image_thumb_url[1024];
    char video_output_path[2048];
    char client_id[128];
    char username[256];
} SnsPublishTask;

static SnsInlineHook g_sns_post_build_send_hook = {0};
static SnsInlineHook g_sns_post_run_hook = {0};
static SnsInlineHook g_sns_post_send_hook = {0};
static SnsInlineHook g_sns_publish_build_post_hook = {0};
static SnsInlineHook g_sns_trace_ui_async_a_hook = {0};
static SnsInlineHook g_sns_trace_ui_async_b_hook = {0};
static SnsInlineHook g_sns_trace_cb_a_hook = {0};
static SnsInlineHook g_sns_trace_cb_b_hook = {0};
static SnsInlineHook g_sns_trace_ctx_enter_hook = {0};
static SnsInlineHook g_sns_trace_async_run_hook = {0};
static SnsInlineHook g_sns_trace_wrap_init_hook = {0};
static SnsInlineHook g_sns_trace_video_scan_hook = {0};
static SnsInlineHook g_sns_trace_video_register_hook = {0};
static SnsInlineHook g_sns_trace_video_register_prep_hook = {0};
static SnsInlineHook g_sns_trace_video_async_build_hook = {0};
static SnsInlineHook g_sns_trace_video_task_build_hook = {0};
static SnsInlineHook g_sns_trace_retry_panel_hook = {0};
static SnsInlineHook g_sns_trace_submit_handler_hook = {0};
static SnsInlineHook g_sns_trace_publish_action_hook = {0};
static SnsInlineHook g_sns_trace_immediate_schedule_hook = {0};
static SnsInlineHook g_sns_trace_immediate_worker_hook = {0};
static SnsInlineHook g_sns_trace_worker_closure_hook = {0};
static SnsInlineHook g_sns_trace_worker_schedule_hook = {0};
static SnsInlineHook g_sns_trace_worker_run_hook = {0};
static SnsInlineHook g_sns_trace_publish_entry_hook = {0};
static volatile LONG g_sns_image_type_patch_enabled = 0;
static volatile LONG g_sns_image_type_patch_attempts = 0;
static volatile LONG g_sns_image_type_patch_hits = 0;
static volatile LONG g_sns_image_type_patch_last_type = 0;
static volatile LONG64 g_sns_image_type_patch_last_timeline = 0;
static volatile LONG g_sns_publish_trace_reentry = 0;
static volatile LONG g_sns_video_deep_trace_count = 0;
static PVOID volatile g_sns_publish_active_task = NULL;
static char g_sns_image_type_patch_content[256] = {0};

static const BYTE WX_ORIG_SNS_POST_BUILD_SEND_BYTES[SNS_POST_BUILD_SEND_PATCH_LEN] = {
    0x55, 0x41, 0x57, 0x41, 0x56, 0x41, 0x55, 0x41, 0x54, 0x56, 0x57, 0x53,
    0x48, 0x81, 0xEC, 0x78, 0x04, 0x00, 0x00,
    0x48, 0x8D, 0xAC, 0x24, 0x80, 0x00, 0x00, 0x00
};
static const BYTE WX_ORIG_SNS_POST_RUN_BYTES[] = {
    0x55, 0x41, 0x57, 0x41, 0x56, 0x41, 0x55, 0x41, 0x54, 0x56, 0x57, 0x53,
    0x48, 0x81, 0xEC, 0xF8, 0x02, 0x00, 0x00
};
static const BYTE WX_ORIG_SNS_POST_SEND_BYTES[] = {
    0x55, 0x41, 0x57, 0x41, 0x56, 0x41, 0x55, 0x41, 0x54, 0x56, 0x57, 0x53,
    0x48, 0x81, 0xEC, 0xA8, 0x04, 0x00, 0x00
};
static const BYTE WX_ORIG_SNS_PUBLISH_BUILD_POST_BYTES[] = {
    0x55, 0x41, 0x56, 0x56, 0x57, 0x53,
    0x48, 0x83, 0xEC, 0x40,
    0x48, 0x8D, 0x6C, 0x24, 0x40,
    0x48, 0xC7, 0x45, 0xF8, 0xFE, 0xFF, 0xFF, 0xFF
};

static const BYTE WX_ORIG_SNS_UI_ASYNC_A_BYTES[] = {
    0x55, 0x41, 0x57, 0x41, 0x56, 0x41, 0x54, 0x56, 0x57, 0x53,
    0x48, 0x81, 0xEC, 0x50, 0x01, 0x00, 0x00,
    0x48, 0x8D, 0xAC, 0x24, 0x80, 0x00, 0x00, 0x00
};
static const BYTE WX_ORIG_SNS_UI_ASYNC_B_BYTES[] = {
    0x55, 0x41, 0x56, 0x56, 0x57, 0x53,
    0x48, 0x81, 0xEC, 0x30, 0x01, 0x00, 0x00,
    0x48, 0x8D, 0xAC, 0x24, 0x80, 0x00, 0x00, 0x00
};
static const BYTE WX_ORIG_SNS_UI_CB_A_BYTES[] = {
    0x56, 0x57, 0x53, 0x48, 0x81, 0xEC, 0xA0, 0x00, 0x00, 0x00,
    0x48, 0x89, 0xD7
};
static const BYTE WX_ORIG_SNS_UI_CB_B_BYTES[] = {
    0x56, 0x48, 0x83, 0xEC, 0x20, 0x83, 0xF9, 0x01, 0x74, 0x1C,
    0x85, 0xC9, 0x75, 0x12
};
static const BYTE WX_ORIG_SNS_CTX_ENTER_BYTES[] = {
    0x55, 0x41, 0x56, 0x56, 0x57, 0x53,
    0x48, 0x81, 0xEC, 0x40, 0x01, 0x00, 0x00,
    0x48, 0x8D, 0xAC, 0x24, 0x80, 0x00, 0x00, 0x00
};
static const BYTE WX_ORIG_SNS_ASYNC_RUN_BYTES[] = {
    0x55, 0x41, 0x57, 0x41, 0x56, 0x41, 0x55, 0x41, 0x54, 0x56, 0x57, 0x53,
    0x48, 0x81, 0xEC, 0x18, 0x03, 0x00, 0x00,
    0x48, 0x8D, 0xAC, 0x24, 0x80, 0x00, 0x00, 0x00
};
static const BYTE WX_ORIG_SNS_WRAP_INIT_BYTES[] = {
    0x55, 0x56, 0x57, 0x48, 0x81, 0xEC, 0x80, 0x00, 0x00, 0x00,
    0x48, 0x8D, 0xAC, 0x24, 0x80, 0x00, 0x00, 0x00
};
static const BYTE WX_ORIG_SNS_VIDEO_SCAN_BYTES[] = {
    0x55, 0x41, 0x57, 0x41, 0x56, 0x41, 0x55, 0x41, 0x54, 0x56, 0x57, 0x53,
    0x48, 0x81, 0xEC, 0xF8, 0x02, 0x00, 0x00,
    0x48, 0x8D, 0xAC, 0x24, 0x80, 0x00, 0x00, 0x00
};
static const BYTE WX_ORIG_SNS_VIDEO_REGISTER_BYTES[] = {
    0x55, 0x56, 0x57, 0x53,
    0x48, 0x81, 0xEC, 0xA8, 0x02, 0x00, 0x00,
    0x48, 0x8D, 0xAC, 0x24, 0x80, 0x00, 0x00, 0x00,
    0x48, 0xC7, 0x85, 0x20, 0x02, 0x00, 0x00, 0xFE, 0xFF, 0xFF, 0xFF
};
static const BYTE WX_ORIG_SNS_VIDEO_REGISTER_PREP_BYTES[] = {
    0x55, 0x41, 0x57, 0x41, 0x56, 0x56, 0x57, 0x53,
    0x48, 0x81, 0xEC, 0xC8, 0x00, 0x00, 0x00,
    0x48, 0x8D, 0xAC, 0x24, 0x80, 0x00, 0x00, 0x00
};
static const BYTE WX_ORIG_SNS_VIDEO_ASYNC_BUILD_BYTES[] = {
    0x55, 0x41, 0x57, 0x41, 0x56, 0x56, 0x57, 0x53,
    0x48, 0x81, 0xEC, 0x28, 0x01, 0x00, 0x00,
    0x48, 0x8D, 0xAC, 0x24, 0x80, 0x00, 0x00, 0x00
};
static const BYTE WX_ORIG_SNS_VIDEO_TASK_BUILD_BYTES[] = {
    0x55, 0x41, 0x57, 0x41, 0x56, 0x41, 0x55, 0x41, 0x54, 0x56, 0x57, 0x53,
    0x48, 0x81, 0xEC, 0xC8, 0x02, 0x00, 0x00,
    0x48, 0x8D, 0xAC, 0x24, 0x80, 0x00, 0x00, 0x00
};
static const BYTE WX_ORIG_SNS_RETRY_PANEL_BYTES[] = {
    0x55, 0x56, 0x57, 0x53,
    0x48, 0x81, 0xEC, 0x88, 0x00, 0x00, 0x00,
    0x48, 0x8D, 0xAC, 0x24, 0x80, 0x00, 0x00, 0x00
};
static const BYTE WX_ORIG_SNS_SUBMIT_HANDLER_BYTES[] = {
    0x55, 0x41, 0x57, 0x41, 0x56, 0x41, 0x55, 0x41, 0x54, 0x56, 0x57, 0x53,
    0x48, 0x81, 0xEC, 0x88, 0x02, 0x00, 0x00,
    0x48, 0x8D, 0xAC, 0x24, 0x80, 0x00, 0x00, 0x00
};
static const BYTE WX_ORIG_SNS_PUBLISH_ACTION_BYTES[] = {
    0x55, 0x56, 0x57, 0x53,
    0x48, 0x81, 0xEC, 0x78, 0x03, 0x00, 0x00,
    0x48, 0x8D, 0xAC, 0x24, 0x80, 0x00, 0x00, 0x00
};
static const BYTE WX_ORIG_SNS_IMMEDIATE_SCHEDULE_BYTES[] = {
    0x55, 0x41, 0x57, 0x41, 0x56, 0x41, 0x55, 0x41, 0x54, 0x56, 0x57, 0x53,
    0x48, 0x81, 0xEC, 0xF8, 0x09, 0x00, 0x00,
    0x48, 0x8D, 0xAC, 0x24, 0x80, 0x00, 0x00, 0x00
};
static const BYTE WX_ORIG_SNS_IMMEDIATE_WORKER_BYTES[] = {
    0x55, 0x41, 0x56, 0x56, 0x57, 0x53,
    0x48, 0x81, 0xEC, 0x80, 0x02, 0x00, 0x00,
    0x48, 0x8D, 0xAC, 0x24, 0x80, 0x00, 0x00, 0x00
};
static const BYTE WX_ORIG_SNS_WORKER_CLOSURE_BYTES[] = {
    0x55, 0x41, 0x56, 0x56, 0x57, 0x53,
    0x48, 0x81, 0xEC, 0x30, 0x02, 0x00, 0x00,
    0x48, 0x8D, 0xAC, 0x24, 0x80, 0x00, 0x00, 0x00
};
static const BYTE WX_ORIG_SNS_WORKER_SCHEDULE_BYTES[] = {
    0x55, 0x41, 0x56, 0x56, 0x57, 0x53,
    0x48, 0x81, 0xEC, 0xD0, 0x01, 0x00, 0x00,
    0x48, 0x8D, 0xAC, 0x24, 0x80, 0x00, 0x00, 0x00
};
static const BYTE WX_ORIG_SNS_WORKER_RUN_BYTES[] = {
    0x55, 0x41, 0x57, 0x41, 0x56, 0x41, 0x54, 0x56, 0x57, 0x53,
    0x48, 0x81, 0xEC, 0xA0, 0x01, 0x00, 0x00,
    0x48, 0x8D, 0xAC, 0x24, 0x80, 0x00, 0x00, 0x00
};
static const BYTE WX_ORIG_SNS_PUBLISH_ENTRY_BYTES[] = {
    0x55, 0x56, 0x57,
    0x48, 0x81, 0xEC, 0x80, 0x00, 0x00, 0x00,
    0x48, 0x8D, 0xAC, 0x24, 0x80, 0x00, 0x00, 0x00
};

static int sns_capture_exception(DWORD *code,
                                 uint64_t *addr,
                                 uint64_t *fault,
                                 uint64_t *ctx_rdi,
                                 uint64_t *ctx_rcx,
                                 uint64_t *ctx_rdx,
                                 uint64_t *ctx_r8,
                                 uint64_t *ctx_r9,
                                 EXCEPTION_POINTERS *ep);
static void sns_delete_video_gate_thumbs_for_scan(int64_t scan_obj);
static int sns_publish_trace_enabled(void);
static int64_t __fastcall sns_post_build_send_hook(int64_t post_obj, int64_t out);
static double __fastcall sns_post_run_hook(int64_t post_obj, int phase);
static unsigned int *__fastcall sns_post_send_hook(unsigned int *out,
                                                   int64_t cgi_ctx,
                                                   int64_t req,
                                                   int64_t extra,
                                                   int64_t opts);
static int64_t __fastcall sns_publish_build_post_hook(int64_t out_post, int64_t shared_model);
static int sns_utf8_to_wide_path(const char *path, WCHAR *out, int max_chars);
static void sns_release_shared_ctl(int64_t ctl);
static SnsPublishTask *sns_publish_active_task(void);

static int sns_bytes_equal(const BYTE *a, const BYTE *b, int n) {
    for (int i = 0; i < n; i++) {
        if (a[i] != b[i]) return 0;
    }
    return 1;
}

static int sns_write_abs_jump(BYTE *target, int patch_len, void *dst) {
    DWORD oldp;
    if (!target || patch_len < 12) return 0;
    if (!VirtualProtect(target, patch_len, PAGE_EXECUTE_READWRITE, &oldp)) return 0;
    target[0] = 0x48;
    target[1] = 0xB8;
    *(uint64_t *)(target + 2) = (uint64_t)dst;
    target[10] = 0xFF;
    target[11] = 0xE0;
    for (int i = 12; i < patch_len; i++) target[i] = 0x90;
    VirtualProtect(target, patch_len, oldp, &oldp);
    FlushInstructionCache(GetCurrentProcess(), target, patch_len);
    return 1;
}

static int sns_build_trampoline(BYTE *tramp, BYTE *target, const BYTE *orig, int patch_len) {
    BYTE *p = tramp;
    copy_bytes(p, orig, patch_len);
    p += patch_len;
    p[0] = 0x48;
    p[1] = 0xB8;
    *(uint64_t *)(p + 2) = (uint64_t)(target + patch_len);
    p[10] = 0xFF;
    p[11] = 0xE0;
    return patch_len + 12;
}

static int sns_install_inline_hook(SnsInlineHook *state, DWORD rva, const BYTE *expected,
                                   int len, void *hook_fn) {
    int64_t base = (int64_t)wxb_weixin_module();
    BYTE *target;
    if (!base || !state || !expected || len <= 0 || !hook_fn) return 0;
    if (InterlockedCompareExchange(&state->installed, 0, 0) != 0) return 1;
    target = (BYTE *)(base + rva);
    if (!sns_bytes_equal(target, expected, len)) return 0;
    copy_bytes(state->orig, target, len);
    state->trampoline = VirtualAlloc(NULL, len + 16, MEM_COMMIT | MEM_RESERVE, PAGE_EXECUTE_READWRITE);
    if (!state->trampoline) return 0;
    sns_build_trampoline((BYTE *)state->trampoline, target, state->orig, len);
    if (!sns_write_abs_jump(target, len, hook_fn)) {
        VirtualFree(state->trampoline, 0, MEM_RELEASE);
        state->trampoline = NULL;
        return 0;
    }
    state->patch_len = len;
    InterlockedExchange(&state->installed, 1);
    return 1;
}

static void sns_uninstall_inline_hook(SnsInlineHook *state, DWORD rva) {
    int64_t base = (int64_t)wxb_weixin_module();
    BYTE *target;
    DWORD oldp;
    if (!base || !state) return;
    if (InterlockedCompareExchange(&state->installed, 0, 0) == 0) return;
    target = (BYTE *)(base + rva);
    if (VirtualProtect(target, state->patch_len, PAGE_EXECUTE_READWRITE, &oldp)) {
        copy_bytes(target, state->orig, state->patch_len);
        VirtualProtect(target, state->patch_len, oldp, &oldp);
        FlushInstructionCache(GetCurrentProcess(), target, state->patch_len);
    }
    InterlockedExchange(&state->installed, 0);
    if (state->trampoline) {
        VirtualFree(state->trampoline, 0, MEM_RELEASE);
        state->trampoline = NULL;
    }
}

static void sns_trace_append_i64(char **p, char *end, const char *name, int64_t value) {
    char num[64];
    append_raw(p, end, ",\"");
    append_raw(p, end, name);
    append_raw(p, end, "\":");
    wsprintfA(num, "%I64d", (long long)value);
    append_raw(p, end, num);
}

static void sns_trace_append_int(char **p, char *end, const char *name, int value) {
    char num[32];
    append_raw(p, end, ",\"");
    append_raw(p, end, name);
    append_raw(p, end, "\":");
    wsprintfA(num, "%d", value);
    append_raw(p, end, num);
}

static void sns_trace_append_hex(char **p, char *end, const char *name, int64_t value) {
    char num[64];
    append_raw(p, end, ",\"");
    append_raw(p, end, name);
    append_raw(p, end, "\":\"0x");
    wsprintfA(num, "%I64x", (unsigned __int64)value);
    append_raw(p, end, num);
    append_raw(p, end, "\"");
}

static void sns_trace_append_sso(char **p, char *end, const char *name, int64_t s) {
    char buf[768];
    if (!s || !is_readable_ptr(s, 32)) return;
    buf[0] = 0;
    __try {
        if (!wx_read_sso_str(s, buf, sizeof(buf))) return;
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
        return;
    }
    append_raw(p, end, ",\"");
    append_raw(p, end, name);
    append_raw(p, end, "\":\"");
    append_escaped(p, end, buf);
    append_raw(p, end, "\"");
}

static void sns_trace_append_sso_shape(char **p, char *end, const char *name,
                                       int64_t s, int wide_sso_threshold) {
    char field[128];
    int64_t len64;
    int64_t cap64;
    int64_t data;
    if (!name || !s || !is_readable_ptr(s, 32)) return;
    __try {
        len64 = r64((void *)(s + 0x10));
        cap64 = r64((void *)(s + 0x18));
        data = s;
        if (cap64 >= wide_sso_threshold) data = r64((void *)s);
        wsprintfA(field, "%s_len", name);
        sns_trace_append_i64(p, end, field, len64);
        wsprintfA(field, "%s_cap", name);
        sns_trace_append_i64(p, end, field, cap64);
        wsprintfA(field, "%s_data", name);
        sns_trace_append_hex(p, end, field, data);
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
    }
}

static int sns_trace_append_sso_wide(char **p, char *end, const char *name, int64_t s) {
    char buf[1024];
    int64_t len64;
    int64_t cap64;
    int len;
    int n;
    const WCHAR *src;
    if (!s || !is_readable_ptr(s, 32) || !name) return 0;
    buf[0] = 0;
    __try {
        len64 = r64((void *)(s + 0x10));
        cap64 = r64((void *)(s + 0x18));
        if (len64 <= 0 || len64 > 0x8000 || cap64 <= 0) return 0;
        len = (int)len64;
        src = (cap64 >= 8) ? (const WCHAR *)r64((void *)s) : (const WCHAR *)s;
        if (!src) return 0;
        n = WideCharToMultiByte(CP_UTF8, 0, src, len, buf, sizeof(buf) - 1, NULL, NULL);
        if (n <= 0) return 0;
        buf[n] = 0;
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
        return 0;
    }
    append_raw(p, end, ",\"");
    append_raw(p, end, name);
    append_raw(p, end, "\":\"");
    append_escaped(p, end, buf);
    append_raw(p, end, "\"");
    return n;
}

static void sns_trace_append_media(char **p, char *end, const char *prefix, int64_t media) {
    char name[96];
    if (!prefix) prefix = "media";
    wsprintfA(name, "%s_ptr", prefix);
    sns_trace_append_hex(p, end, name, media);
    if (!media || !is_readable_ptr(media, 416)) return;
    __try {
        wsprintfA(name, "%s_type", prefix);
        sns_trace_append_int(p, end, name, r32((void *)(media + 0)));
        wsprintfA(name, "%s_client_id", prefix);
        sns_trace_append_sso(p, end, name, media + 8);
        wsprintfA(name, "%s_local_src", prefix);
        sns_trace_append_sso(p, end, name, media + 40);
        wsprintfA(name, "%s_local_data", prefix);
        sns_trace_append_sso(p, end, name, media + 72);
        wsprintfA(name, "%s_local_thumb", prefix);
        sns_trace_append_sso(p, end, name, media + 104);
        wsprintfA(name, "%s_md5", prefix);
        sns_trace_append_sso(p, end, name, media + 152);
        wsprintfA(name, "%s_cdn", prefix);
        sns_trace_append_sso(p, end, name, media + 184);
        wsprintfA(name, "%s_cdn_flag216", prefix);
        sns_trace_append_i64(p, end, name, r64((void *)(media + 216)));
        wsprintfA(name, "%s_is_finished", prefix);
        sns_trace_append_int(p, end, name, *((BYTE *)(media + 408)) ? 1 : 0);
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
    }
}

static void sns_trace_append_model(char **p, char *end, const char *prefix, int64_t model) {
    char name[96];
    int64_t media_begin = 0;
    int64_t media_end = 0;
    int media_count = -1;
    if (!prefix) prefix = "model";
    wsprintfA(name, "%s_ptr", prefix);
    sns_trace_append_hex(p, end, name, model);
    if (!model || !is_readable_ptr(model, 632)) return;
    __try {
        wsprintfA(name, "%s_flag24", prefix);
        sns_trace_append_int(p, end, name, *((BYTE *)(model + 24)) ? 1 : 0);
        wsprintfA(name, "%s_type48", prefix);
        sns_trace_append_int(p, end, name, r32((void *)(model + 48)));
        wsprintfA(name, "%s_ui_type52", prefix);
        sns_trace_append_int(p, end, name, r32((void *)(model + 52)));
        wsprintfA(name, "%s_client_id", prefix);
        sns_trace_append_sso(p, end, name, model + 64);
        wsprintfA(name, "%s_content", prefix);
        sns_trace_append_sso(p, end, name, model + 96);
        wsprintfA(name, "%s_timeline_obj", prefix);
        sns_trace_append_hex(p, end, name, r64((void *)(model + 8)));
        wsprintfA(name, "%s_timeline_ctl", prefix);
        sns_trace_append_hex(p, end, name, r64((void *)(model + 16)));
        wsprintfA(name, "%s_media_begin", prefix);
        media_begin = r64((void *)(model + 280));
        sns_trace_append_hex(p, end, name, media_begin);
        wsprintfA(name, "%s_media_end", prefix);
        media_end = r64((void *)(model + 288));
        sns_trace_append_hex(p, end, name, media_end);
        if (media_begin && media_end >= media_begin && (media_end - media_begin) <= 16 * 16) {
            media_count = (int)((media_end - media_begin) / 16);
        }
        wsprintfA(name, "%s_media_count", prefix);
        sns_trace_append_int(p, end, name, media_count);
        wsprintfA(name, "%s_entrance624", prefix);
        sns_trace_append_int(p, end, name, r32((void *)(model + 624)));
        wsprintfA(name, "%s_scene628", prefix);
        sns_trace_append_int(p, end, name, r32((void *)(model + 628)));
        if (media_count > 0 && is_readable_ptr(media_begin, 16)) {
            sns_trace_append_media(p, end, "first_media", r64((void *)media_begin));
            sns_trace_append_hex(p, end, "first_media_ctl", r64((void *)(media_begin + 8)));
        }
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
    }
}

static void sns_trace_append_pair(char **p, char *end, const char *prefix, int64_t pair) {
    char name[96];
    int64_t model = 0;
    int64_t ctl = 0;
    if (!prefix) prefix = "pair";
    wsprintfA(name, "%s_pair", prefix);
    sns_trace_append_hex(p, end, name, pair);
    if (!pair || !is_readable_ptr(pair, 16)) return;
    __try {
        model = r64((void *)pair);
        ctl = r64((void *)(pair + 8));
        wsprintfA(name, "%s_model", prefix);
        sns_trace_append_hex(p, end, name, model);
        wsprintfA(name, "%s_ctl", prefix);
        sns_trace_append_hex(p, end, name, ctl);
        wsprintfA(name, "%s_model", prefix);
        sns_trace_append_model(p, end, name, model);
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
    }
}

static void sns_publish_trace_log(const char *event, int64_t a1, int64_t a2,
                                  int64_t a3, int64_t a4, int64_t retaddr,
                                  int64_t ret, int is_leave) {
    char line[32768];
    char *p = line;
    char *end = line + sizeof(line) - 1;
    int64_t base = (int64_t)wxb_weixin_module();
    if (!sns_publish_trace_enabled()) return;
    append_raw(&p, end, "{\"event\":\"");
    append_escaped(&p, end, event ? event : "");
    append_raw(&p, end, "\"");
    sns_trace_append_i64(&p, end, "tick", (int64_t)GetTickCount64());
    sns_trace_append_int(&p, end, "tid", (int)GetCurrentThreadId());
    sns_trace_append_hex(&p, end, "base", base);
    sns_trace_append_hex(&p, end, "retaddr", retaddr);
    if (base && retaddr >= base && retaddr < base + 0x0B000000) {
        sns_trace_append_hex(&p, end, "ret_rva", retaddr - base);
    }
    sns_trace_append_hex(&p, end, "a1", a1);
    sns_trace_append_hex(&p, end, "a2", a2);
    sns_trace_append_hex(&p, end, "a3", a3);
    sns_trace_append_hex(&p, end, "a4", a4);
    sns_trace_append_pair(&p, end, "arg2", a2);
    sns_trace_append_pair(&p, end, "arg3", a3);
    sns_trace_append_pair(&p, end, "arg4", a4);
    sns_trace_append_model(&p, end, "arg1_as_model", a1);
    if (is_leave) sns_trace_append_i64(&p, end, "ret", ret);
    append_raw(&p, end, "}\r\n");
    *p = 0;
    append_file_bytes(SNS_PUBLISH_TRACE_LOG, line, (DWORD)(p - line));
}

static int64_t sns_publish_trace_call(SnsInlineHook *state, const char *name,
                                      int64_t a1, int64_t a2, int64_t a3, int64_t a4,
                                      int64_t retaddr) {
    SnsTraceFn4 orig = state ? (SnsTraceFn4)state->trampoline : 0;
    int64_t ret = 0;
    char event[96];
    if (InterlockedCompareExchange(&g_sns_publish_trace_reentry, 1, 0) == 0) {
        __try {
            wsprintfA(event, "%s_enter", name ? name : "sns_publish_trace");
            sns_publish_trace_log(event, a1, a2, a3, a4, retaddr, 0, 0);
        }
        __except (EXCEPTION_EXECUTE_HANDLER) {
        }
        InterlockedExchange(&g_sns_publish_trace_reentry, 0);
    }
    if (orig) ret = orig(a1, a2, a3, a4);
    if (InterlockedCompareExchange(&g_sns_publish_trace_reentry, 1, 0) == 0) {
        __try {
            wsprintfA(event, "%s_leave", name ? name : "sns_publish_trace");
            sns_publish_trace_log(event, a1, a2, a3, a4, retaddr, ret, 1);
        }
        __except (EXCEPTION_EXECUTE_HANDLER) {
        }
        InterlockedExchange(&g_sns_publish_trace_reentry, 0);
    }
    return ret;
}

static void sns_trace_append_video_object(char **p, char *end, const char *prefix, int64_t obj) {
    char name[96];
    if (!prefix) prefix = "video";
    wsprintfA(name, "%s_ptr", prefix);
    sns_trace_append_hex(p, end, name, obj);
    if (!obj || !is_readable_ptr(obj, 0x98)) return;
    __try {
        wsprintfA(name, "%s_id", prefix);
        sns_trace_append_sso(p, end, name, obj + 0);
        wsprintfA(name, "%s_src", prefix);
        sns_trace_append_sso_shape(p, end, name, obj + 32, 8);
        if (!sns_trace_append_sso_wide(p, end, name, obj + 32)) {
            sns_trace_append_sso(p, end, name, obj + 32);
        }
        wsprintfA(name, "%s_data", prefix);
        sns_trace_append_sso_shape(p, end, name, obj + 64, 8);
        if (!sns_trace_append_sso_wide(p, end, name, obj + 64)) {
            sns_trace_append_sso(p, end, name, obj + 64);
        }
        wsprintfA(name, "%s_thumb", prefix);
        sns_trace_append_sso_shape(p, end, name, obj + 96, 8);
        if (!sns_trace_append_sso_wide(p, end, name, obj + 96)) {
            sns_trace_append_sso(p, end, name, obj + 96);
        }
        wsprintfA(name, "%s_thumb_obj", prefix);
        sns_trace_append_hex(p, end, name, r64((void *)(obj + 128)));
        wsprintfA(name, "%s_dims136", prefix);
        sns_trace_append_hex(p, end, name, r64((void *)(obj + 136)));
        wsprintfA(name, "%s_duration144", prefix);
        sns_trace_append_int(p, end, name, r32((void *)(obj + 144)));
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
    }
}

static void sns_video_flow_trace_log(const char *event,
                                     int64_t a1,
                                     int64_t a2,
                                     int64_t retaddr,
                                     int64_t ret,
                                     int is_leave) {
    char line[32768];
    char *p = line;
    char *end = line + sizeof(line) - 1;
    int64_t base = (int64_t)wxb_weixin_module();
    int64_t scan_model = 0;
    int64_t video_obj = 0;
    if (!sns_publish_trace_enabled()) return;
    append_raw(&p, end, "{\"event\":\"");
    append_escaped(&p, end, event ? event : "");
    append_raw(&p, end, "\"");
    sns_trace_append_i64(&p, end, "tick", (int64_t)GetTickCount64());
    sns_trace_append_int(&p, end, "tid", (int)GetCurrentThreadId());
    sns_trace_append_hex(&p, end, "base", base);
    sns_trace_append_hex(&p, end, "retaddr", retaddr);
    if (base && retaddr >= base && retaddr < base + 0x0B000000) {
        sns_trace_append_hex(&p, end, "ret_rva", retaddr - base);
    }
    sns_trace_append_hex(&p, end, "a1", a1);
    sns_trace_append_hex(&p, end, "a2", a2);
    if (a1 && is_readable_ptr(a1 + 88, 8)) {
        __try {
            scan_model = r64((void *)(a1 + 88));
        }
        __except (EXCEPTION_EXECUTE_HANDLER) {
            scan_model = 0;
        }
        sns_trace_append_model(&p, end, "a1_plus88_model", scan_model);
    }
    sns_trace_append_pair(&p, end, "arg2", a2);
    if (a2 && is_readable_ptr(a2, 16)) {
        __try {
            video_obj = r64((void *)a2);
        }
        __except (EXCEPTION_EXECUTE_HANDLER) {
            video_obj = 0;
        }
        sns_trace_append_video_object(&p, end, "arg2_video_obj", video_obj);
    }
    if (is_leave) sns_trace_append_i64(&p, end, "ret", ret);
    append_raw(&p, end, "}\r\n");
    *p = 0;
    append_file_bytes(SNS_PUBLISH_TRACE_LOG, line, (DWORD)(p - line));
}

static void sns_trace_append_raw_qwords(char **p, char *end, const char *prefix,
                                        int64_t obj, int qwords) {
    char name[96];
    int i;
    if (!prefix) prefix = "obj";
    sns_trace_append_hex(p, end, prefix, obj);
    if (!obj || qwords <= 0 || qwords > 12 || !is_readable_ptr(obj, qwords * 8)) return;
    __try {
        for (i = 0; i < qwords; ++i) {
            wsprintfA(name, "%s_q%02d", prefix, i);
            sns_trace_append_hex(p, end, name, r64((void *)(obj + i * 8)));
        }
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
    }
}

static void sns_trace_append_ref_object(char **p, char *end, const char *prefix, int64_t obj) {
    char name[96];
    if (!prefix) prefix = "ref";
    sns_trace_append_hex(p, end, prefix, obj);
    if (!obj || !is_readable_ptr(obj, 24)) return;
    __try {
        wsprintfA(name, "%s_vt", prefix);
        sns_trace_append_hex(p, end, name, r64((void *)obj));
        wsprintfA(name, "%s_ref8", prefix);
        sns_trace_append_int(p, end, name, r32((void *)(obj + 8)));
        wsprintfA(name, "%s_ref12", prefix);
        sns_trace_append_int(p, end, name, r32((void *)(obj + 12)));
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
    }
}

static void sns_trace_append_video_task_scheduler(char **p, char *end, int64_t task_builder) {
    int64_t scheduler = 0;
    int64_t scheduler_vt = 0;
    int64_t scheduler_run = 0;
    int64_t ctl8 = 0;
    int64_t ctl9 = 0;
    int64_t base = (int64_t)wxb_weixin_module();
    if (!task_builder || !is_readable_ptr(task_builder, 80)) return;
    __try {
        scheduler = r64((void *)(task_builder + 56));
        ctl8 = r64((void *)(task_builder + 64));
        ctl9 = r64((void *)(task_builder + 72));
        sns_trace_append_hex(p, end, "task_builder_scheduler", scheduler);
        sns_trace_append_hex(p, end, "task_builder_slot8", ctl8);
        sns_trace_append_hex(p, end, "task_builder_slot9", ctl9);
        if (scheduler && is_readable_ptr(scheduler, 8)) {
            scheduler_vt = r64((void *)scheduler);
            sns_trace_append_hex(p, end, "task_builder_scheduler_vt", scheduler_vt);
            if (scheduler_vt && is_readable_ptr(scheduler_vt + 16, 8)) {
                scheduler_run = r64((void *)(scheduler_vt + 16));
                sns_trace_append_hex(p, end, "task_builder_scheduler_run", scheduler_run);
                if (base && scheduler_run >= base && scheduler_run < base + 0x0B000000) {
                    sns_trace_append_hex(p, end, "task_builder_scheduler_run_rva", scheduler_run - base);
                }
            }
        }
        sns_trace_append_ref_object(p, end, "task_builder_slot9_ref", ctl9);
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
    }
}

static void sns_trace_append_video_task_out(char **p, char *end, int64_t out) {
    int64_t task_id = 0;
    int64_t scheduled_task = 0;
    int64_t scheduled_ctl = 0;
    int64_t fallback_task = 0;
    int64_t fallback_ctl = 0;
    if (!out || !is_readable_ptr(out, 40)) return;
    __try {
        task_id = r64((void *)out);
        scheduled_task = r64((void *)(out + 8));
        scheduled_ctl = r64((void *)(out + 16));
        fallback_task = r64((void *)(out + 24));
        fallback_ctl = r64((void *)(out + 32));
        sns_trace_append_hex(p, end, "task_out_id", task_id);
        sns_trace_append_hex(p, end, "task_out_scheduled_task", scheduled_task);
        sns_trace_append_ref_object(p, end, "task_out_scheduled_ctl", scheduled_ctl);
        sns_trace_append_hex(p, end, "task_out_fallback_task", fallback_task);
        sns_trace_append_ref_object(p, end, "task_out_fallback_ctl", fallback_ctl);
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
    }
}

static void sns_video_deep_trace_log(const char *event,
                                     int64_t a1, int64_t a2, int64_t a3, int64_t a4,
                                     int64_t a5, int64_t a6, int64_t a7, int64_t a8,
                                     int64_t retaddr, int64_t ret, int is_leave) {
    char line[32768];
    char *p = line;
    char *end = line + sizeof(line) - 1;
    int64_t base = (int64_t)wxb_weixin_module();
    if (!sns_publish_trace_enabled()) return;
    LONG n = InterlockedIncrement(&g_sns_video_deep_trace_count);
    if (n > 240) return;
    append_raw(&p, end, "{\"event\":\"");
    append_escaped(&p, end, event ? event : "");
    append_raw(&p, end, "\"");
    sns_trace_append_i64(&p, end, "tick", (int64_t)GetTickCount64());
    sns_trace_append_int(&p, end, "tid", (int)GetCurrentThreadId());
    sns_trace_append_hex(&p, end, "base", base);
    sns_trace_append_hex(&p, end, "retaddr", retaddr);
    if (base && retaddr >= base && retaddr < base + 0x0B000000) {
        sns_trace_append_hex(&p, end, "ret_rva", retaddr - base);
    }
    sns_trace_append_hex(&p, end, "a1", a1);
    sns_trace_append_hex(&p, end, "a2", a2);
    sns_trace_append_hex(&p, end, "a3", a3);
    sns_trace_append_hex(&p, end, "a4", a4);
    sns_trace_append_hex(&p, end, "a5", a5);
    sns_trace_append_hex(&p, end, "a6", a6);
    sns_trace_append_hex(&p, end, "a7", a7);
    sns_trace_append_hex(&p, end, "a8", a8);
    sns_trace_append_pair(&p, end, "arg2_pair", a2);
    sns_trace_append_pair(&p, end, "arg3_pair", a3);
    sns_trace_append_video_task_scheduler(&p, end, a1);
    sns_trace_append_video_task_out(&p, end, a2);
    if (a4) {
        sns_trace_append_sso_shape(&p, end, "arg4_sso", a4, 8);
        if (!sns_trace_append_sso_wide(&p, end, "arg4_sso", a4)) {
            sns_trace_append_sso(&p, end, "arg4_sso", a4);
        }
    }
    if (a5) {
        sns_trace_append_sso_shape(&p, end, "arg5_sso", a5, 8);
        if (!sns_trace_append_sso_wide(&p, end, "arg5_sso", a5)) {
            sns_trace_append_sso(&p, end, "arg5_sso", a5);
        }
    }
    if (a6) {
        sns_trace_append_sso_shape(&p, end, "arg6_sso", a6, 8);
        if (!sns_trace_append_sso_wide(&p, end, "arg6_sso", a6)) {
            sns_trace_append_sso(&p, end, "arg6_sso", a6);
        }
    }
    sns_trace_append_raw_qwords(&p, end, "arg1_obj", a1, 10);
    sns_trace_append_raw_qwords(&p, end, "arg2_obj", a2, 6);
    sns_trace_append_raw_qwords(&p, end, "arg3_obj", a3, 8);
    sns_trace_append_raw_qwords(&p, end, "arg6_obj", a6, 8);
    sns_trace_append_raw_qwords(&p, end, "arg7_obj", a7, 8);
    if (is_leave) sns_trace_append_i64(&p, end, "ret", ret);
    append_raw(&p, end, "}\r\n");
    *p = 0;
    append_file_bytes(SNS_PUBLISH_TRACE_LOG, line, (DWORD)(p - line));
}

static void sns_video_deep_trace_install_log(int prep_ok, int async_ok, int task_ok) {
    char line[2048];
    char *p = line;
    char *end = line + sizeof(line) - 1;
    int64_t base = (int64_t)wxb_weixin_module();
    if (!sns_publish_trace_enabled()) return;
    append_raw(&p, end, "{\"event\":\"video_deep_trace_install\"");
    sns_trace_append_i64(&p, end, "tick", (int64_t)GetTickCount64());
    sns_trace_append_int(&p, end, "tid", (int)GetCurrentThreadId());
    sns_trace_append_hex(&p, end, "base", base);
    sns_trace_append_hex(&p, end, "prep_rva", WX_OFF_SNS_VIDEO_REGISTER_PREP);
    sns_trace_append_int(&p, end, "prep_ok", prep_ok);
    sns_trace_append_int(&p, end, "prep_installed",
                         InterlockedCompareExchange(&g_sns_trace_video_register_prep_hook.installed, 0, 0) ? 1 : 0);
    sns_trace_append_hex(&p, end, "async_rva", WX_OFF_SNS_VIDEO_ASYNC_BUILD);
    sns_trace_append_int(&p, end, "async_ok", async_ok);
    sns_trace_append_int(&p, end, "async_installed",
                         InterlockedCompareExchange(&g_sns_trace_video_async_build_hook.installed, 0, 0) ? 1 : 0);
    sns_trace_append_hex(&p, end, "task_rva", WX_OFF_SNS_VIDEO_TASK_BUILD);
    sns_trace_append_int(&p, end, "task_ok", task_ok);
    sns_trace_append_int(&p, end, "task_installed",
                         InterlockedCompareExchange(&g_sns_trace_video_task_build_hook.installed, 0, 0) ? 1 : 0);
    append_raw(&p, end, "}\r\n");
    *p = 0;
    append_file_bytes(SNS_PUBLISH_TRACE_LOG, line, (DWORD)(p - line));
}

static void sns_trace_append_bytes(char **p, char *end, const char *name,
                                   const BYTE *bytes, int len) {
    static const char hex[] = "0123456789abcdef";
    char tmp[3];
    append_raw(p, end, ",\"");
    append_raw(p, end, name);
    append_raw(p, end, "\":\"");
    tmp[2] = 0;
    for (int i = 0; bytes && i < len; i++) {
        tmp[0] = hex[(bytes[i] >> 4) & 0x0F];
        tmp[1] = hex[bytes[i] & 0x0F];
        append_raw(p, end, tmp);
    }
    append_raw(p, end, "\"");
}

static int sns_read_hook_target_bytes(DWORD rva, BYTE *out, int len) {
    int64_t base = (int64_t)wxb_weixin_module();
    if (!base || !out || len <= 0) return 0;
    __try {
        copy_bytes(out, (void *)(base + rva), len);
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
        return 0;
    }
    return 1;
}

static int sns_hook_expected_match(DWORD rva, const BYTE *expected, int len) {
    BYTE actual[32];
    if (!expected || len <= 0 || len > (int)sizeof(actual)) return 0;
    if (!sns_read_hook_target_bytes(rva, actual, len)) return 0;
    return sns_bytes_equal(actual, expected, len);
}

static int sns_publish_trace_enabled(void) {
    return file_exists(SNS_PUBLISH_TRACE_ENABLE_FILE) ? 1 : 0;
}

static void sns_publish_trace_install_log(const char *event,
                                          int scan_ok,
                                          int scan_was_installed,
                                          int scan_match_before,
                                          int register_ok,
                                          int register_was_installed,
                                          int register_match_before) {
    char line[4096];
    char status[256];
    char *p = line;
    char *end = line + sizeof(line) - 1;
    int64_t base = (int64_t)wxb_weixin_module();
    BYTE scan_actual[32];
    BYTE register_actual[32];
    int scan_have_actual;
    int register_have_actual;
    if (!sns_publish_trace_enabled()) return;

    scan_have_actual = sns_read_hook_target_bytes(WX_OFF_SNS_VIDEO_SCAN,
                                                  scan_actual,
                                                  (int)sizeof(WX_ORIG_SNS_VIDEO_SCAN_BYTES));
    register_have_actual = sns_read_hook_target_bytes(WX_OFF_SNS_VIDEO_REGISTER,
                                                      register_actual,
                                                      (int)sizeof(WX_ORIG_SNS_VIDEO_REGISTER_BYTES));

    append_raw(&p, end, "{\"event\":\"");
    append_escaped(&p, end, event ? event : "video_flow_trace_install");
    append_raw(&p, end, "\"");
    sns_trace_append_i64(&p, end, "tick", (int64_t)GetTickCount64());
    sns_trace_append_int(&p, end, "tid", (int)GetCurrentThreadId());
    sns_trace_append_hex(&p, end, "base", base);
    sns_trace_append_hex(&p, end, "scan_rva", WX_OFF_SNS_VIDEO_SCAN);
    sns_trace_append_int(&p, end, "scan_ok", scan_ok);
    sns_trace_append_int(&p, end, "scan_was_installed", scan_was_installed);
    sns_trace_append_int(&p, end, "scan_match_before", scan_match_before);
    sns_trace_append_int(&p, end, "scan_installed",
                         InterlockedCompareExchange(&g_sns_trace_video_scan_hook.installed, 0, 0) ? 1 : 0);
    if (scan_have_actual) {
        sns_trace_append_bytes(&p, end, "scan_actual",
                               scan_actual,
                               (int)sizeof(WX_ORIG_SNS_VIDEO_SCAN_BYTES));
    }
    sns_trace_append_bytes(&p, end, "scan_expected",
                           WX_ORIG_SNS_VIDEO_SCAN_BYTES,
                           (int)sizeof(WX_ORIG_SNS_VIDEO_SCAN_BYTES));
    sns_trace_append_hex(&p, end, "register_rva", WX_OFF_SNS_VIDEO_REGISTER);
    sns_trace_append_int(&p, end, "register_ok", register_ok);
    sns_trace_append_int(&p, end, "register_was_installed", register_was_installed);
    sns_trace_append_int(&p, end, "register_match_before", register_match_before);
    sns_trace_append_int(&p, end, "register_installed",
                         InterlockedCompareExchange(&g_sns_trace_video_register_hook.installed, 0, 0) ? 1 : 0);
    if (register_have_actual) {
        sns_trace_append_bytes(&p, end, "register_actual",
                               register_actual,
                               (int)sizeof(WX_ORIG_SNS_VIDEO_REGISTER_BYTES));
    }
    sns_trace_append_bytes(&p, end, "register_expected",
                           WX_ORIG_SNS_VIDEO_REGISTER_BYTES,
                           (int)sizeof(WX_ORIG_SNS_VIDEO_REGISTER_BYTES));
    append_raw(&p, end, "}\r\n");
    *p = 0;
    append_file_bytes(SNS_PUBLISH_TRACE_LOG, line, (DWORD)(p - line));

    wsprintfA(status,
              "sns video flow trace hooks scan=%d register=%d scan_match=%d register_match=%d\n",
              scan_ok,
              register_ok,
              scan_match_before,
              register_match_before);
    write_status(status);
}

static int64_t __fastcall sns_trace_video_scan_hook(int64_t a1) {
    SnsTraceFn1 orig = (SnsTraceFn1)g_sns_trace_video_scan_hook.trampoline;
    int64_t retaddr = (int64_t)_ReturnAddress();
    int64_t ret = 0;
    __try {
        sns_delete_video_gate_thumbs_for_scan(a1);
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
    }
    if (InterlockedCompareExchange(&g_sns_publish_trace_reentry, 1, 0) == 0) {
        __try {
            sns_video_flow_trace_log("video_scan_183E375E0_enter", a1, 0, retaddr, 0, 0);
        }
        __except (EXCEPTION_EXECUTE_HANDLER) {
        }
        InterlockedExchange(&g_sns_publish_trace_reentry, 0);
    }
    if (orig) ret = orig(a1);
    if (InterlockedCompareExchange(&g_sns_publish_trace_reentry, 1, 0) == 0) {
        __try {
            sns_video_flow_trace_log("video_scan_183E375E0_leave", a1, 0, retaddr, ret, 1);
        }
        __except (EXCEPTION_EXECUTE_HANDLER) {
        }
        InterlockedExchange(&g_sns_publish_trace_reentry, 0);
    }
    return ret;
}

static int64_t __fastcall sns_trace_video_register_hook(int64_t a1, int64_t a2) {
    SnsTraceFn2 orig = (SnsTraceFn2)g_sns_trace_video_register_hook.trampoline;
    int64_t retaddr = (int64_t)_ReturnAddress();
    int64_t ret = 0;
    if (InterlockedCompareExchange(&g_sns_publish_trace_reentry, 1, 0) == 0) {
        __try {
            sns_video_flow_trace_log("video_register_183E36C30_enter", a1, a2, retaddr, 0, 0);
        }
        __except (EXCEPTION_EXECUTE_HANDLER) {
        }
        InterlockedExchange(&g_sns_publish_trace_reentry, 0);
    }
    if (orig) ret = orig(a1, a2);
    if (InterlockedCompareExchange(&g_sns_publish_trace_reentry, 1, 0) == 0) {
        __try {
            sns_video_flow_trace_log("video_register_183E36C30_leave", a1, a2, retaddr, ret, 1);
        }
        __except (EXCEPTION_EXECUTE_HANDLER) {
        }
        InterlockedExchange(&g_sns_publish_trace_reentry, 0);
    }
    return ret;
}

static int64_t __fastcall sns_trace_video_register_prep_hook(int64_t a1, int64_t a2, int64_t a3,
                                                             int64_t a4, int64_t a5, int64_t a6) {
    SnsTraceFn6 orig = (SnsTraceFn6)g_sns_trace_video_register_prep_hook.trampoline;
    int64_t retaddr = (int64_t)_ReturnAddress();
    int64_t ret = 0;
    if (InterlockedCompareExchange(&g_sns_publish_trace_reentry, 1, 0) == 0) {
        __try {
            sns_video_deep_trace_log("video_register_prep_183B191A0_enter",
                                     a1, a2, a3, a4, a5, a6, 0, 0, retaddr, 0, 0);
        }
        __except (EXCEPTION_EXECUTE_HANDLER) {
        }
        InterlockedExchange(&g_sns_publish_trace_reentry, 0);
    }
    if (orig) ret = orig(a1, a2, a3, a4, a5, a6);
    if (InterlockedCompareExchange(&g_sns_publish_trace_reentry, 1, 0) == 0) {
        __try {
            sns_video_deep_trace_log("video_register_prep_183B191A0_leave",
                                     a1, a2, a3, a4, a5, a6, 0, 0, retaddr, ret, 1);
        }
        __except (EXCEPTION_EXECUTE_HANDLER) {
        }
        InterlockedExchange(&g_sns_publish_trace_reentry, 0);
    }
    return ret;
}

static int64_t __fastcall sns_trace_video_async_build_hook(int64_t a1, int64_t a2, int64_t a3,
                                                           int64_t a4, int64_t a5, int64_t a6) {
    SnsTraceFn6 orig = (SnsTraceFn6)g_sns_trace_video_async_build_hook.trampoline;
    int64_t retaddr = (int64_t)_ReturnAddress();
    int64_t ret = 0;
    if (InterlockedCompareExchange(&g_sns_publish_trace_reentry, 1, 0) == 0) {
        __try {
            sns_video_deep_trace_log("video_async_build_183E37330_enter",
                                     a1, a2, a3, a4, a5, a6, 0, 0, retaddr, 0, 0);
        }
        __except (EXCEPTION_EXECUTE_HANDLER) {
        }
        InterlockedExchange(&g_sns_publish_trace_reentry, 0);
    }
    if (orig) ret = orig(a1, a2, a3, a4, a5, a6);
    if (InterlockedCompareExchange(&g_sns_publish_trace_reentry, 1, 0) == 0) {
        __try {
            sns_video_deep_trace_log("video_async_build_183E37330_leave",
                                     a1, a2, a3, a4, a5, a6, 0, 0, retaddr, ret, 1);
        }
        __except (EXCEPTION_EXECUTE_HANDLER) {
        }
        InterlockedExchange(&g_sns_publish_trace_reentry, 0);
    }
    return ret;
}

static int64_t __fastcall sns_trace_video_task_build_hook(int64_t a1, int64_t a2, int64_t a3,
                                                          int64_t a4, int64_t a5, int64_t a6,
                                                          int64_t a7, int64_t a8) {
    SnsTraceFn8 orig = (SnsTraceFn8)g_sns_trace_video_task_build_hook.trampoline;
    int64_t retaddr = (int64_t)_ReturnAddress();
    int64_t ret = 0;
    if (InterlockedCompareExchange(&g_sns_publish_trace_reentry, 1, 0) == 0) {
        __try {
            sns_video_deep_trace_log("video_task_build_183E3F1B0_enter",
                                     a1, a2, a3, a4, a5, a6, a7, a8, retaddr, 0, 0);
        }
        __except (EXCEPTION_EXECUTE_HANDLER) {
        }
        InterlockedExchange(&g_sns_publish_trace_reentry, 0);
    }
    if (orig) ret = orig(a1, a2, a3, a4, a5, a6, a7, a8);
    if (InterlockedCompareExchange(&g_sns_publish_trace_reentry, 1, 0) == 0) {
        __try {
            sns_video_deep_trace_log("video_task_build_183E3F1B0_leave",
                                     a1, a2, a3, a4, a5, a6, a7, a8, retaddr, ret, 1);
        }
        __except (EXCEPTION_EXECUTE_HANDLER) {
        }
        InterlockedExchange(&g_sns_publish_trace_reentry, 0);
    }
    return ret;
}

static void sns_trace_official_publish_event(const char *name,
                                             const char *phase,
                                             int64_t owner,
                                             int64_t shared_model,
                                             int64_t context,
                                             int64_t aux,
                                             int64_t retaddr,
                                             int64_t ret,
                                             int leaving) {
    char event[128];
    if (InterlockedCompareExchange(&g_sns_publish_trace_reentry, 1, 0) != 0) return;
    __try {
        wsprintfA(event, "%s_%s", name ? name : "official_publish", phase ? phase : "trace");
        sns_publish_trace_log(event, owner, shared_model, context, aux, retaddr, ret, leaving);
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
    }
    InterlockedExchange(&g_sns_publish_trace_reentry, 0);
}

static void sns_trace_worker_fields(int64_t worker,
                                    int64_t pair[2],
                                    int64_t *context) {
    pair[0] = 0;
    pair[1] = 0;
    if (context) *context = 0;
    if (!worker || !is_readable_ptr(worker, 32)) return;
    __try {
        pair[0] = r64((void *)(worker + 8));
        pair[1] = r64((void *)(worker + 16));
        if (context) *context = r64((void *)(worker + 24));
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
        pair[0] = 0;
        pair[1] = 0;
        if (context) *context = 0;
    }
}

static int64_t __fastcall sns_trace_retry_panel_hook(int64_t a1, int64_t a2) {
    SnsTraceFn2 orig = (SnsTraceFn2)g_sns_trace_retry_panel_hook.trampoline;
    int64_t retaddr = (int64_t)_ReturnAddress();
    int64_t ret = 0;
    sns_trace_official_publish_event("retry_panel_181C8BEB0", "enter",
                                     a1, a2, 0, 0, retaddr, 0, 0);
    if (orig) ret = orig(a1, a2);
    sns_trace_official_publish_event("retry_panel_181C8BEB0", "leave",
                                     a1, a2, 0, 0, retaddr, ret, 1);
    return ret;
}

static int64_t __fastcall sns_trace_submit_handler_hook(int64_t a1) {
    SnsTraceFn1 orig = (SnsTraceFn1)g_sns_trace_submit_handler_hook.trampoline;
    int64_t retaddr = (int64_t)_ReturnAddress();
    int64_t controller = 0;
    int64_t shared_model = 0;
    int64_t ret = 0;
    __try {
        if (a1 && is_readable_ptr(a1 + 440, 8)) controller = r64((void *)(a1 + 440));
        if (controller && is_readable_ptr(controller + 104, 8)) shared_model = controller + 88;
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
        controller = 0;
        shared_model = 0;
    }
    sns_trace_official_publish_event("submit_handler_183E43E80", "enter",
                                     controller, shared_model, a1, 0, retaddr, 0, 0);
    if (orig) ret = orig(a1);
    sns_trace_official_publish_event("submit_handler_183E43E80", "leave",
                                     controller, shared_model, a1, 0, retaddr, ret, 1);
    return ret;
}

static int64_t __fastcall sns_trace_publish_action_hook(int64_t a1, int64_t a2) {
    SnsTraceFn2 orig = (SnsTraceFn2)g_sns_trace_publish_action_hook.trampoline;
    int64_t retaddr = (int64_t)_ReturnAddress();
    int64_t controller = 0;
    int64_t shared_model = 0;
    int64_t ret = 0;
    __try {
        if (a1 && is_readable_ptr(a1 + 8, 8)) controller = r64((void *)(a1 + 8));
        if (controller && is_readable_ptr(controller + 104, 8)) shared_model = controller + 88;
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
        controller = 0;
        shared_model = 0;
    }
    sns_trace_official_publish_event("publish_action_18555BB30", "enter",
                                     controller, shared_model, a1, a2, retaddr, 0, 0);
    if (orig) ret = orig(a1, a2);
    sns_trace_official_publish_event("publish_action_18555BB30", "leave",
                                     controller, shared_model, a1, a2, retaddr, ret, 1);
    return ret;
}

static void __fastcall sns_trace_immediate_schedule_hook(int64_t context,
                                                         int64_t shared_model) {
    SnsTraceVoidFn2 orig = (SnsTraceVoidFn2)g_sns_trace_immediate_schedule_hook.trampoline;
    int64_t retaddr = (int64_t)_ReturnAddress();
    int64_t model = 0;
    __try {
        if (shared_model && is_readable_ptr(shared_model, 16)) {
            model = r64((void *)shared_model);
        }
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
        model = 0;
    }
    sns_trace_official_publish_event("immediate_schedule_183B1B290", "enter",
                                     model, shared_model, context, 0, retaddr, 0, 0);
    if (orig) orig(context, shared_model);
    sns_trace_official_publish_event("immediate_schedule_183B1B290", "leave",
                                     model, shared_model, context, 0, retaddr, 0, 1);
}

static int64_t __fastcall sns_trace_immediate_worker_hook(int64_t worker) {
    SnsTraceFn1 orig = (SnsTraceFn1)g_sns_trace_immediate_worker_hook.trampoline;
    SnsPublishTask *task = sns_publish_active_task();
    int64_t pair[2];
    int64_t context = 0;
    int64_t retaddr = (int64_t)_ReturnAddress();
    int64_t ret = 0;
    int matched;

    sns_trace_worker_fields(worker, pair, &context);
    matched = task &&
              InterlockedCompareExchange(&task->accept_results, 0, 0) &&
              pair[0] == task->model;
    sns_trace_official_publish_event("immediate_worker_183B26140", "enter",
                                     pair[0], (int64_t)pair, context, worker,
                                     retaddr, 0, 0);
    if (orig) ret = orig(worker);
    if (matched) InterlockedExchange(&task->video_worker_completed, 1);
    sns_trace_official_publish_event("immediate_worker_183B26140", "leave",
                                     pair[0], (int64_t)pair, context, worker,
                                     retaddr, ret, 1);
    return ret;
}

static int64_t __fastcall sns_trace_worker_closure_hook(int64_t a1, int64_t a2, int64_t a3) {
    SnsTraceFn3 orig = (SnsTraceFn3)g_sns_trace_worker_closure_hook.trampoline;
    int64_t retaddr = (int64_t)_ReturnAddress();
    int64_t ret = 0;
    int64_t model = 0;
    __try {
        if (a3 && is_readable_ptr(a3, 16)) model = r64((void *)a3);
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
        model = 0;
    }
    sns_trace_official_publish_event("worker_closure_183B1AEB0", "enter",
                                     model, a3, a1, a2, retaddr, 0, 0);
    if (orig) ret = orig(a1, a2, a3);
    sns_trace_official_publish_event("worker_closure_183B1AEB0", "leave",
                                     model, a3, a1, a2, retaddr, ret, 1);
    return ret;
}

static int64_t __fastcall sns_trace_worker_schedule_hook(int64_t a1, int64_t a2) {
    SnsTraceFn2 orig = (SnsTraceFn2)g_sns_trace_worker_schedule_hook.trampoline;
    int64_t pair[2];
    int64_t context = 0;
    int64_t retaddr = (int64_t)_ReturnAddress();
    int64_t ret = 0;
    sns_trace_worker_fields(a1, pair, &context);
    sns_trace_official_publish_event("worker_schedule_183B542D0", "enter",
                                     pair[0], (int64_t)pair, context, a2, retaddr, 0, 0);
    if (orig) ret = orig(a1, a2);
    sns_trace_official_publish_event("worker_schedule_183B542D0", "leave",
                                     pair[0], (int64_t)pair, context, a2, retaddr, ret, 1);
    return ret;
}

static int64_t __fastcall sns_trace_worker_run_hook(int64_t a1) {
    SnsTraceFn1 orig = (SnsTraceFn1)g_sns_trace_worker_run_hook.trampoline;
    int64_t pair[2];
    int64_t context = 0;
    int64_t retaddr = (int64_t)_ReturnAddress();
    int64_t ret = 0;
    sns_trace_worker_fields(a1, pair, &context);
    sns_trace_official_publish_event("worker_run_183B547B0", "enter",
                                     pair[0], (int64_t)pair, context, a1, retaddr, 0, 0);
    if (orig) ret = orig(a1);
    sns_trace_official_publish_event("worker_run_183B547B0", "leave",
                                     pair[0], (int64_t)pair, context, a1, retaddr, ret, 1);
    return ret;
}

static int64_t __fastcall sns_trace_publish_entry_hook(int64_t a1, int64_t a2) {
    SnsTraceFn2 orig = (SnsTraceFn2)g_sns_trace_publish_entry_hook.trampoline;
    int64_t retaddr = (int64_t)_ReturnAddress();
    int64_t ret = 0;
    int64_t model = 0;
    __try {
        if (a2 && is_readable_ptr(a2, 16)) model = r64((void *)a2);
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
        model = 0;
    }
    sns_trace_official_publish_event("publish_entry_182BE2100", "enter",
                                     model, a2, a1, 0, retaddr, 0, 0);
    if (orig) ret = orig(a1, a2);
    sns_trace_official_publish_event("publish_entry_182BE2100", "leave",
                                     model, a2, a1, 0, retaddr, ret, 1);
    return ret;
}

static int64_t __fastcall sns_trace_ui_async_a_hook(int64_t a1, int64_t a2, int64_t a3, int64_t a4) {
    return sns_publish_trace_call(&g_sns_trace_ui_async_a_hook, "ui_async_a_181C948E0",
                                  a1, a2, a3, a4, (int64_t)_ReturnAddress());
}

static int64_t __fastcall sns_trace_ui_async_b_hook(int64_t a1, int64_t a2, int64_t a3, int64_t a4) {
    return sns_publish_trace_call(&g_sns_trace_ui_async_b_hook, "ui_async_b_181C95650",
                                  a1, a2, a3, a4, (int64_t)_ReturnAddress());
}

static int64_t __fastcall sns_trace_cb_a_hook(int64_t a1, int64_t a2, int64_t a3, int64_t a4) {
    return sns_publish_trace_call(&g_sns_trace_cb_a_hook, "ui_cb_a_181C95370",
                                  a1, a2, a3, a4, (int64_t)_ReturnAddress());
}

static int64_t __fastcall sns_trace_cb_b_hook(int64_t a1, int64_t a2, int64_t a3, int64_t a4) {
    return sns_publish_trace_call(&g_sns_trace_cb_b_hook, "ui_cb_b_181C955E0",
                                  a1, a2, a3, a4, (int64_t)_ReturnAddress());
}

static int64_t __fastcall sns_trace_ctx_enter_hook(int64_t a1, int64_t a2, int64_t a3, int64_t a4) {
    return sns_publish_trace_call(&g_sns_trace_ctx_enter_hook, "ctx_enter_183E1DEC0",
                                  a1, a2, a3, a4, (int64_t)_ReturnAddress());
}

static int64_t __fastcall sns_trace_async_run_hook(int64_t a1, int64_t a2, int64_t a3, int64_t a4) {
    return sns_publish_trace_call(&g_sns_trace_async_run_hook, "async_run_183E336C0",
                                  a1, a2, a3, a4, (int64_t)_ReturnAddress());
}

static int64_t __fastcall sns_trace_wrap_init_hook(int64_t a1, int64_t a2, int64_t a3, int64_t a4) {
    return sns_publish_trace_call(&g_sns_trace_wrap_init_hook, "wrap_init_183E30040",
                                  a1, a2, a3, a4, (int64_t)_ReturnAddress());
}

static void install_sns_video_flow_trace_hooks(void);

static int sns_install_publish_completion_hooks(void) {
    int builder_ok = sns_install_inline_hook(&g_sns_publish_build_post_hook,
                                             WX_OFF_SNS_PUBLISH_BUILD_POST,
                                             WX_ORIG_SNS_PUBLISH_BUILD_POST_BYTES,
                                             (int)sizeof(WX_ORIG_SNS_PUBLISH_BUILD_POST_BYTES),
                                             (void *)sns_publish_build_post_hook);
    int build_ok = sns_install_inline_hook(&g_sns_post_build_send_hook,
                                           WX_OFF_SNS_POST_BUILD_SEND,
                                           WX_ORIG_SNS_POST_BUILD_SEND_BYTES,
                                           SNS_POST_BUILD_SEND_PATCH_LEN,
                                           (void *)sns_post_build_send_hook);
    int run_ok = sns_install_inline_hook(&g_sns_post_run_hook,
                                         WX_OFF_SNS_POST_RUN,
                                         WX_ORIG_SNS_POST_RUN_BYTES,
                                         (int)sizeof(WX_ORIG_SNS_POST_RUN_BYTES),
                                         (void *)sns_post_run_hook);
    int send_ok = sns_install_inline_hook(&g_sns_post_send_hook,
                                           WX_OFF_SNS_POST_SEND,
                                           WX_ORIG_SNS_POST_SEND_BYTES,
                                           (int)sizeof(WX_ORIG_SNS_POST_SEND_BYTES),
                                           (void *)sns_post_send_hook);
    int immediate_worker_ok = sns_install_inline_hook(
        &g_sns_trace_immediate_worker_hook,
        WX_OFF_SNS_PUBLISH_IMMEDIATE_WORKER,
        WX_ORIG_SNS_IMMEDIATE_WORKER_BYTES,
        (int)sizeof(WX_ORIG_SNS_IMMEDIATE_WORKER_BYTES),
        (void *)sns_trace_immediate_worker_hook);
    return builder_ok && build_ok && run_ok && send_ok && immediate_worker_ok;
}

static void install_sns_publish_trace_hooks(void) {
    sns_install_inline_hook(&g_sns_trace_retry_panel_hook, WX_OFF_SNS_RETRY_PANEL_OPEN,
                            WX_ORIG_SNS_RETRY_PANEL_BYTES,
                            (int)sizeof(WX_ORIG_SNS_RETRY_PANEL_BYTES),
                            (void *)sns_trace_retry_panel_hook);
    sns_install_inline_hook(&g_sns_trace_submit_handler_hook, WX_OFF_SNS_PUBLISH_SUBMIT_HANDLER,
                            WX_ORIG_SNS_SUBMIT_HANDLER_BYTES,
                            (int)sizeof(WX_ORIG_SNS_SUBMIT_HANDLER_BYTES),
                            (void *)sns_trace_submit_handler_hook);
    sns_install_inline_hook(&g_sns_trace_publish_action_hook, WX_OFF_SNS_PUBLISH_ACTION,
                            WX_ORIG_SNS_PUBLISH_ACTION_BYTES,
                            (int)sizeof(WX_ORIG_SNS_PUBLISH_ACTION_BYTES),
                            (void *)sns_trace_publish_action_hook);
    sns_install_inline_hook(&g_sns_trace_immediate_schedule_hook,
                            WX_OFF_SNS_PUBLISH_IMMEDIATE_SCHEDULE,
                            WX_ORIG_SNS_IMMEDIATE_SCHEDULE_BYTES,
                            (int)sizeof(WX_ORIG_SNS_IMMEDIATE_SCHEDULE_BYTES),
                            (void *)sns_trace_immediate_schedule_hook);
    sns_install_inline_hook(&g_sns_trace_immediate_worker_hook,
                            WX_OFF_SNS_PUBLISH_IMMEDIATE_WORKER,
                            WX_ORIG_SNS_IMMEDIATE_WORKER_BYTES,
                            (int)sizeof(WX_ORIG_SNS_IMMEDIATE_WORKER_BYTES),
                            (void *)sns_trace_immediate_worker_hook);
    sns_install_inline_hook(&g_sns_trace_worker_closure_hook, WX_OFF_SNS_PUBLISH_WORKER_CLOSURE,
                            WX_ORIG_SNS_WORKER_CLOSURE_BYTES,
                            (int)sizeof(WX_ORIG_SNS_WORKER_CLOSURE_BYTES),
                            (void *)sns_trace_worker_closure_hook);
    sns_install_inline_hook(&g_sns_trace_worker_schedule_hook, WX_OFF_SNS_PUBLISH_WORKER_SCHEDULE,
                            WX_ORIG_SNS_WORKER_SCHEDULE_BYTES,
                            (int)sizeof(WX_ORIG_SNS_WORKER_SCHEDULE_BYTES),
                            (void *)sns_trace_worker_schedule_hook);
    sns_install_inline_hook(&g_sns_trace_worker_run_hook, WX_OFF_SNS_PUBLISH_WORKER_RUN,
                            WX_ORIG_SNS_WORKER_RUN_BYTES,
                            (int)sizeof(WX_ORIG_SNS_WORKER_RUN_BYTES),
                            (void *)sns_trace_worker_run_hook);
    sns_install_inline_hook(&g_sns_trace_publish_entry_hook, WX_OFF_SNS_PUBLISH_ENTRY,
                            WX_ORIG_SNS_PUBLISH_ENTRY_BYTES,
                            (int)sizeof(WX_ORIG_SNS_PUBLISH_ENTRY_BYTES),
                            (void *)sns_trace_publish_entry_hook);
    sns_install_inline_hook(&g_sns_trace_ui_async_a_hook, WX_OFF_SNS_UI_PUBLISH_ASYNC_A,
                            WX_ORIG_SNS_UI_ASYNC_A_BYTES, (int)sizeof(WX_ORIG_SNS_UI_ASYNC_A_BYTES),
                            (void *)sns_trace_ui_async_a_hook);
    sns_install_inline_hook(&g_sns_trace_ui_async_b_hook, WX_OFF_SNS_UI_PUBLISH_ASYNC_B,
                            WX_ORIG_SNS_UI_ASYNC_B_BYTES, (int)sizeof(WX_ORIG_SNS_UI_ASYNC_B_BYTES),
                            (void *)sns_trace_ui_async_b_hook);
    /*
     * Do not hook 181C955E0/181C95370 with the generic trampoline. 181C955E0
     * starts with short conditional branches; copying those bytes without
     * relocation makes the trampoline jump into the wrong address on retry.
     */
    sns_install_inline_hook(&g_sns_trace_ctx_enter_hook, WX_OFF_SNS_PUBLISH_CTX_ENTER,
                            WX_ORIG_SNS_CTX_ENTER_BYTES, (int)sizeof(WX_ORIG_SNS_CTX_ENTER_BYTES),
                            (void *)sns_trace_ctx_enter_hook);
    sns_install_inline_hook(&g_sns_trace_async_run_hook, WX_OFF_SNS_PUBLISH_ASYNC_RUN,
                            WX_ORIG_SNS_ASYNC_RUN_BYTES, (int)sizeof(WX_ORIG_SNS_ASYNC_RUN_BYTES),
                            (void *)sns_trace_async_run_hook);
    sns_install_inline_hook(&g_sns_trace_wrap_init_hook, WX_OFF_SNS_PUBLISH_WRAP_INIT,
                            WX_ORIG_SNS_WRAP_INIT_BYTES, (int)sizeof(WX_ORIG_SNS_WRAP_INIT_BYTES),
                            (void *)sns_trace_wrap_init_hook);
    install_sns_video_flow_trace_hooks();
}

static void install_sns_video_flow_trace_hooks(void) {
    int prep_ok;
    int async_ok;
    int task_ok;
    int scan_was_installed =
        InterlockedCompareExchange(&g_sns_trace_video_scan_hook.installed, 0, 0) ? 1 : 0;
    int register_was_installed =
        InterlockedCompareExchange(&g_sns_trace_video_register_hook.installed, 0, 0) ? 1 : 0;
    int prep_was_installed =
        InterlockedCompareExchange(&g_sns_trace_video_register_prep_hook.installed, 0, 0) ? 1 : 0;
    int async_was_installed =
        InterlockedCompareExchange(&g_sns_trace_video_async_build_hook.installed, 0, 0) ? 1 : 0;
    int task_was_installed =
        InterlockedCompareExchange(&g_sns_trace_video_task_build_hook.installed, 0, 0) ? 1 : 0;
    if (scan_was_installed && register_was_installed &&
        prep_was_installed && async_was_installed && task_was_installed) {
        return;
    }
    int scan_match_before = scan_was_installed ? 1 :
        sns_hook_expected_match(WX_OFF_SNS_VIDEO_SCAN,
                                WX_ORIG_SNS_VIDEO_SCAN_BYTES,
                                (int)sizeof(WX_ORIG_SNS_VIDEO_SCAN_BYTES));
    int register_match_before = register_was_installed ? 1 :
        sns_hook_expected_match(WX_OFF_SNS_VIDEO_REGISTER,
                                WX_ORIG_SNS_VIDEO_REGISTER_BYTES,
                                (int)sizeof(WX_ORIG_SNS_VIDEO_REGISTER_BYTES));
    int scan_ok = sns_install_inline_hook(&g_sns_trace_video_scan_hook, WX_OFF_SNS_VIDEO_SCAN,
                                          WX_ORIG_SNS_VIDEO_SCAN_BYTES,
                                          (int)sizeof(WX_ORIG_SNS_VIDEO_SCAN_BYTES),
                                          (void *)sns_trace_video_scan_hook);
    int register_ok = sns_install_inline_hook(&g_sns_trace_video_register_hook,
                                              WX_OFF_SNS_VIDEO_REGISTER,
                                              WX_ORIG_SNS_VIDEO_REGISTER_BYTES,
                                              (int)sizeof(WX_ORIG_SNS_VIDEO_REGISTER_BYTES),
                                              (void *)sns_trace_video_register_hook);
    sns_publish_trace_install_log("video_flow_trace_install",
                                  scan_ok,
                                  scan_was_installed,
                                  scan_match_before,
                                  register_ok,
                                  register_was_installed,
                                  register_match_before);
    prep_ok = sns_install_inline_hook(&g_sns_trace_video_register_prep_hook,
                                      WX_OFF_SNS_VIDEO_REGISTER_PREP,
                                      WX_ORIG_SNS_VIDEO_REGISTER_PREP_BYTES,
                                      (int)sizeof(WX_ORIG_SNS_VIDEO_REGISTER_PREP_BYTES),
                                      (void *)sns_trace_video_register_prep_hook);
    async_ok = sns_install_inline_hook(&g_sns_trace_video_async_build_hook,
                                       WX_OFF_SNS_VIDEO_ASYNC_BUILD,
                                       WX_ORIG_SNS_VIDEO_ASYNC_BUILD_BYTES,
                                       (int)sizeof(WX_ORIG_SNS_VIDEO_ASYNC_BUILD_BYTES),
                                       (void *)sns_trace_video_async_build_hook);
    task_ok = sns_install_inline_hook(&g_sns_trace_video_task_build_hook,
                                      WX_OFF_SNS_VIDEO_TASK_BUILD,
                                      WX_ORIG_SNS_VIDEO_TASK_BUILD_BYTES,
                                      (int)sizeof(WX_ORIG_SNS_VIDEO_TASK_BUILD_BYTES),
                                      (void *)sns_trace_video_task_build_hook);
    sns_video_deep_trace_install_log(prep_ok, async_ok, task_ok);
}

static void sns_uninstall_publish_trace_only_hooks(void) {
    sns_uninstall_inline_hook(&g_sns_trace_video_task_build_hook, WX_OFF_SNS_VIDEO_TASK_BUILD);
    sns_uninstall_inline_hook(&g_sns_trace_video_async_build_hook, WX_OFF_SNS_VIDEO_ASYNC_BUILD);
    sns_uninstall_inline_hook(&g_sns_trace_video_register_prep_hook, WX_OFF_SNS_VIDEO_REGISTER_PREP);
    sns_uninstall_inline_hook(&g_sns_trace_video_register_hook, WX_OFF_SNS_VIDEO_REGISTER);
    sns_uninstall_inline_hook(&g_sns_trace_video_scan_hook, WX_OFF_SNS_VIDEO_SCAN);
    sns_uninstall_inline_hook(&g_sns_trace_wrap_init_hook, WX_OFF_SNS_PUBLISH_WRAP_INIT);
    sns_uninstall_inline_hook(&g_sns_trace_async_run_hook, WX_OFF_SNS_PUBLISH_ASYNC_RUN);
    sns_uninstall_inline_hook(&g_sns_trace_ctx_enter_hook, WX_OFF_SNS_PUBLISH_CTX_ENTER);
    sns_uninstall_inline_hook(&g_sns_trace_cb_b_hook, WX_OFF_SNS_UI_PUBLISH_CB_B);
    sns_uninstall_inline_hook(&g_sns_trace_cb_a_hook, WX_OFF_SNS_UI_PUBLISH_CB_A);
    sns_uninstall_inline_hook(&g_sns_trace_ui_async_b_hook, WX_OFF_SNS_UI_PUBLISH_ASYNC_B);
    sns_uninstall_inline_hook(&g_sns_trace_ui_async_a_hook, WX_OFF_SNS_UI_PUBLISH_ASYNC_A);
    sns_uninstall_inline_hook(&g_sns_trace_publish_entry_hook, WX_OFF_SNS_PUBLISH_ENTRY);
    sns_uninstall_inline_hook(&g_sns_trace_immediate_worker_hook,
                              WX_OFF_SNS_PUBLISH_IMMEDIATE_WORKER);
    sns_uninstall_inline_hook(&g_sns_trace_immediate_schedule_hook,
                              WX_OFF_SNS_PUBLISH_IMMEDIATE_SCHEDULE);
    sns_uninstall_inline_hook(&g_sns_trace_worker_run_hook, WX_OFF_SNS_PUBLISH_WORKER_RUN);
    sns_uninstall_inline_hook(&g_sns_trace_worker_schedule_hook, WX_OFF_SNS_PUBLISH_WORKER_SCHEDULE);
    sns_uninstall_inline_hook(&g_sns_trace_worker_closure_hook, WX_OFF_SNS_PUBLISH_WORKER_CLOSURE);
    sns_uninstall_inline_hook(&g_sns_trace_publish_action_hook, WX_OFF_SNS_PUBLISH_ACTION);
    sns_uninstall_inline_hook(&g_sns_trace_submit_handler_hook, WX_OFF_SNS_PUBLISH_SUBMIT_HANDLER);
    sns_uninstall_inline_hook(&g_sns_trace_retry_panel_hook, WX_OFF_SNS_RETRY_PANEL_OPEN);
}

void wxb_uninstall_sns_publish_trace_hooks(void) {
    sns_uninstall_publish_trace_only_hooks();
    sns_uninstall_inline_hook(&g_sns_post_send_hook, WX_OFF_SNS_POST_SEND);
    sns_uninstall_inline_hook(&g_sns_post_run_hook, WX_OFF_SNS_POST_RUN);
    sns_uninstall_inline_hook(&g_sns_post_build_send_hook, WX_OFF_SNS_POST_BUILD_SEND);
    sns_uninstall_inline_hook(&g_sns_publish_build_post_hook, WX_OFF_SNS_PUBLISH_BUILD_POST);
}

void wxb_refresh_sns_publish_trace_hooks(void) {
    if (file_exists(SNS_PUBLISH_TRACE_ENABLE_FILE)) install_sns_publish_trace_hooks();
    else sns_uninstall_publish_trace_only_hooks();
    sns_install_publish_completion_hooks();
}

static int sns_std_string_equals(int64_t s, const char *expected) {
    char buf[512];
    if (!expected || !*expected) return 0;
    if (!wx_read_sso_str(s, buf, sizeof(buf))) return 0;
    return str_eq(buf, expected);
}

static SnsPublishTask *sns_publish_active_task(void) {
    return (SnsPublishTask *)InterlockedCompareExchangePointer(
        (PVOID volatile *)&g_sns_publish_active_task, NULL, NULL);
}

static int sns_publish_claim_task(SnsPublishTask *task) {
    if (!task) return 0;
    return InterlockedCompareExchangePointer(
               (PVOID volatile *)&g_sns_publish_active_task, task, NULL) == NULL;
}

static void sns_publish_release_task(SnsPublishTask *task) {
    if (!task) return;
    InterlockedCompareExchangePointer(
        (PVOID volatile *)&g_sns_publish_active_task, NULL, task);
}

static int sns_build_video_output_path(const char *video_path, char *out, int max) {
    static const char suffix[] = ".publish.mp4";
    int len;
    int suffix_len = (int)sizeof(suffix) - 1;
    if (!video_path || !*video_path || !out || max <= 1) return 0;
    len = lstrlenA(video_path);
    if (len <= 0 || len + suffix_len >= max) return 0;
    copy_bytes(out, video_path, (DWORD)len);
    copy_bytes(out + len, suffix, (DWORD)(suffix_len + 1));
    return 1;
}

static int sns_assign_post_wstring(int64_t base, int64_t dst, const char *utf8) {
    uint64_t temp[4];
    WCHAR pool[SNS_STAGE_WPATH_MAX];
    int len = utf8 ? lstrlenA(utf8) : 0;
    int wlen;
    WCHAR *data;
    if (!base || !dst || !utf8) return 0;
    ZeroMemory(temp, sizeof(temp));
    wlen = len ? MultiByteToWideChar(CP_UTF8, 0, utf8, len, NULL, 0) : 0;
    if (wlen < 0 || wlen >= SNS_STAGE_WPATH_MAX) return 0;
    data = wlen < 8 ? (WCHAR *)temp : pool;
    if (wlen && MultiByteToWideChar(CP_UTF8, 0, utf8, len, data, wlen) != wlen) return 0;
    data[wlen] = 0;
    if (wlen >= 8) temp[0] = (uint64_t)data;
    temp[2] = (uint64_t)wlen;
    temp[3] = (uint64_t)(wlen < 8 ? 7 : wlen);
    ((SnsStdWStringCopyFn)(base + WX_OFF_STD_WSTRING_COPY))(dst, (int64_t)temp);
    return 1;
}

static void sns_trace_post_video_paths(SnsPublishTask *task, int64_t post_obj,
                                       const char *event) {
    char line[8192];
    char *p = line;
    char *end = line + sizeof(line) - 1;
    if (!task || !post_obj || !sns_publish_trace_enabled()) return;
    append_raw(&p, end, "{\"event\":\"");
    append_escaped(&p, end, event ? event : "native_video_post_paths");
    append_raw(&p, end, "\"");
    sns_trace_append_i64(&p, end, "tick", (int64_t)GetTickCount64());
    sns_trace_append_int(&p, end, "tid", (int)GetCurrentThreadId());
    sns_trace_append_hex(&p, end, "post", post_obj);
    sns_trace_append_sso_shape(&p, end, "post_video", post_obj + 320, 8);
    sns_trace_append_sso_wide(&p, end, "post_video", post_obj + 320);
    sns_trace_append_sso_shape(&p, end, "post_thumb", post_obj + 352, 8);
    sns_trace_append_sso_wide(&p, end, "post_thumb", post_obj + 352);
    sns_trace_append_sso_shape(&p, end, "post_output", post_obj + 384, 8);
    sns_trace_append_sso_wide(&p, end, "post_output", post_obj + 384);
    sns_trace_append_int(&p, end, "paths_set", task->post_paths_set);
    sns_trace_append_hex(&p, end, "video_attr", task->post_video_attr);
    sns_trace_append_hex(&p, end, "thumb_attr", task->post_thumb_attr);
    sns_trace_append_hex(&p, end, "output_attr", task->post_output_attr);
    append_raw(&p, end, "}\r\n");
    *p = 0;
    append_file_bytes(SNS_PUBLISH_TRACE_LOG, line, (DWORD)(p - line));
}

static int64_t __fastcall sns_publish_build_post_hook(int64_t out_post, int64_t shared_model) {
    SnsPublishBuildPostFn orig =
        (SnsPublishBuildPostFn)g_sns_publish_build_post_hook.trampoline;
    SnsPublishTask *task = sns_publish_active_task();
    int64_t ret = 0;

    if (orig) ret = orig(out_post, shared_model);

    if (task && task->is_video &&
        InterlockedCompareExchange(&task->accept_results, 0, 0) &&
        out_post && shared_model &&
        is_readable_ptr(shared_model, 16) &&
        is_readable_ptr(out_post, 16)) {
        __try {
            int64_t model = r64((void *)shared_model);
            int64_t post_obj = r64((void *)out_post);
            int64_t post_ctl = r64((void *)(out_post + 8));
            if (model == task->model && post_obj && post_ctl) {
                int64_t base = (int64_t)wxb_weixin_module();
                WCHAR path_w[SNS_STAGE_WPATH_MAX];
                int video_set = sns_assign_post_wstring(
                    base, post_obj + 320, task->image_path);
                int thumb_set = sns_assign_post_wstring(
                    base, post_obj + 352, task->image_thumb_url);
                int output_set = sns_assign_post_wstring(
                    base, post_obj + 384, task->video_output_path);
                task->post_paths_set = video_set && thumb_set && output_set;
                /*
                 * The model+24 immediate branch creates one new post and lets
                 * sub_182BE2100 set its normal phase-advance state. This hook
                 * only supplies the local wide paths consumed by upload setup.
                 */
                task->post_video_attr = INVALID_FILE_ATTRIBUTES;
                task->post_thumb_attr = INVALID_FILE_ATTRIBUTES;
                task->post_output_attr = INVALID_FILE_ATTRIBUTES;
                if (sns_utf8_to_wide_path(task->image_path, path_w, SNS_STAGE_WPATH_MAX)) {
                    task->post_video_attr = GetFileAttributesW(path_w);
                }
                if (task->image_thumb_url[0] &&
                    sns_utf8_to_wide_path(task->image_thumb_url, path_w, SNS_STAGE_WPATH_MAX)) {
                    task->post_thumb_attr = GetFileAttributesW(path_w);
                }
                if (sns_utf8_to_wide_path(task->video_output_path, path_w, SNS_STAGE_WPATH_MAX)) {
                    task->post_output_attr = GetFileAttributesW(path_w);
                }
                /* Retain the official builder result beyond BE2100's local shared_ptr. */
                _InterlockedIncrement((volatile LONG *)(post_ctl + 8));
                InterlockedExchange64((volatile LONG64 *)&task->post_ctl, post_ctl);
                MemoryBarrier();
                InterlockedExchange64((volatile LONG64 *)&task->post_obj, post_obj);
                task->phase = 26;
                sns_trace_post_video_paths(task, post_obj, "native_video_post_paths_set");
                sns_publish_trace_log("native_video_official_post_built",
                                      task->model, shared_model,
                                      0, 0, 0, post_obj, 1);
            }
        }
        __except (EXCEPTION_EXECUTE_HANDLER) {
        }
    }
    return ret;
}

static int64_t __fastcall sns_post_build_send_hook(int64_t post_obj, int64_t out) {
    SnsPostBuildSendFn orig = (SnsPostBuildSendFn)g_sns_post_build_send_hook.trampoline;
    SnsPublishTask *task = sns_publish_active_task();
    int64_t timeline = 0;
    int patched = 0;
    int64_t ret = 0;

    __try {
        if (InterlockedCompareExchange(&g_sns_image_type_patch_enabled, 0, 0) &&
            post_obj && is_readable_ptr(post_obj + 600, 8)) {
            InterlockedIncrement(&g_sns_image_type_patch_attempts);
            timeline = r64((void *)(post_obj + 600));
            if (timeline && is_readable_ptr(timeline, 304) &&
                sns_std_string_equals(timeline + 96, g_sns_image_type_patch_content)) {
                int current_type = r32((void *)(timeline + 48));
                InterlockedExchange(&g_sns_image_type_patch_last_type, current_type);
                InterlockedExchange64(&g_sns_image_type_patch_last_timeline, timeline);
                if (current_type == 15) {
                    w32((void *)(timeline + 48), 1);
                    InterlockedIncrement(&g_sns_image_type_patch_hits);
                    patched = 1;
                }
            }
        }
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
        patched = 0;
    }

    if (task && task->post_obj == post_obj) {
        InterlockedExchange(&task->post_build_send_seen, 1);
    }

    if (orig) ret = orig(post_obj, out);

    if (task && task->post_obj == post_obj && out && is_readable_ptr(out, 56)) {
        __try {
            task->official_result_code = (LONG)r32((void *)out);
            task->build_out_response = (uint64_t)r64((void *)(out + 40));
            task->build_out_response_ctl = (uint64_t)r64((void *)(out + 48));
            InterlockedExchange(&task->official_result_seen, 1);
        }
        __except (EXCEPTION_EXECUTE_HANDLER) {
        }
    }
    (void)patched;
    return ret;
}

static unsigned int *__fastcall sns_post_send_hook(unsigned int *out,
                                                   int64_t cgi_ctx,
                                                   int64_t req,
                                                   int64_t extra,
                                                   int64_t opts) {
    SnsPostSendFn orig = (SnsPostSendFn)g_sns_post_send_hook.trampoline;
    SnsPublishTask *task = sns_publish_active_task();
    unsigned int *ret = out;
    int matched = task &&
                  InterlockedCompareExchange(&task->accept_results, 0, 0) &&
                  InterlockedCompareExchange(&task->phase3_seen, 0, 0) &&
                  task->phase3_tid == GetCurrentThreadId();

    if (matched) InterlockedExchange(&task->cgi209_seen, 1);
    if (orig) ret = orig(out, cgi_ctx, req, extra, opts);

    if (matched && out && is_readable_ptr((int64_t)out, 56)) {
        __try {
            task->cgi_out_error = (uint64_t)r64((void *)out);
            task->cgi_out_response = (uint64_t)r64((void *)((BYTE *)out + 40));
            task->cgi_out_response_ctl = (uint64_t)r64((void *)((BYTE *)out + 48));
            InterlockedExchange(&task->cgi_sync_error, task->cgi_out_error ? 1 : 0);
        }
        __except (EXCEPTION_EXECUTE_HANDLER) {
            InterlockedExchange(&task->cgi_sync_error, 1);
        }
    }
    return ret;
}

static double __fastcall sns_post_run_hook(int64_t post_obj, int phase) {
    SnsPostRunFn orig = (SnsPostRunFn)g_sns_post_run_hook.trampoline;
    SnsPublishTask *task = sns_publish_active_task();
    double ret = 0.0;
    int matched = task &&
                  InterlockedCompareExchange(&task->accept_results, 0, 0) &&
                  task->post_obj == post_obj;

    if (matched) {
        if (phase == 1) InterlockedExchange(&task->phase1_seen, 1);
        if (phase == 2) InterlockedExchange(&task->phase2_seen, 1);
        if (phase == 3) {
            task->phase3_tid = GetCurrentThreadId();
            InterlockedExchange(&task->phase3_seen, 1);
            task->phase = 43;
        }
    }

    if (orig) ret = orig(post_obj, phase);

    if (matched && phase >= 1 && phase <= 3) {
        __try {
            if (is_readable_ptr(post_obj + 468, 4)) {
                task->post_state456 = r32((void *)(post_obj + 456));
                task->post_error460 = r32((void *)(post_obj + 460));
                task->post_state464 = r32((void *)(post_obj + 464));
                if (phase == 1) task->phase1_result_code = task->post_error460;
                if (phase == 2) task->phase2_result_code = task->post_error460;
                if (phase == 3) task->phase3_result_code = task->post_error460;
            }
            if (phase == 3 && task->model && is_readable_ptr(task->model + 616, 8)) {
                task->model_response = (uint64_t)r64((void *)(task->model + 608));
                task->model_response_ctl = (uint64_t)r64((void *)(task->model + 616));
            }
        }
        __except (EXCEPTION_EXECUTE_HANDLER) {
        }

        if (phase == 3 && task->model_response && task->model_response_ctl) {
            InterlockedExchange(&task->response_seen, 1);
        }
    }

    if (matched && phase == 3) {
        task->wait_status = WAIT_OBJECT_0;
        task->phase = 44;
        if (InterlockedCompareExchange(&task->post_build_send_seen, 0, 0) &&
            InterlockedCompareExchange(&task->cgi209_seen, 0, 0) &&
            !InterlockedCompareExchange(&task->cgi_sync_error, 0, 0) &&
            InterlockedCompareExchange(&task->official_result_seen, 0, 0) &&
            InterlockedCompareExchange(&task->official_result_code, 0, 0) == 0 &&
            InterlockedCompareExchange(&task->response_seen, 0, 0)) {
            task->call_result = 0;
            InterlockedCompareExchange(&task->status, 1, 3);
        } else {
            task->call_result = InterlockedCompareExchange(
                &task->official_result_code, 0, 0);
            InterlockedCompareExchange(&task->status, -16, 3);
        }
    }
    return ret;
}

static int sns_install_post_build_send_patch(const char *content) {
    safe_copy(g_sns_image_type_patch_content, sizeof(g_sns_image_type_patch_content), content ? content : "");
    InterlockedExchange(&g_sns_image_type_patch_attempts, 0);
    InterlockedExchange(&g_sns_image_type_patch_hits, 0);
    InterlockedExchange(&g_sns_image_type_patch_last_type, 0);
    InterlockedExchange64(&g_sns_image_type_patch_last_timeline, 0);
    InterlockedExchange(&g_sns_image_type_patch_enabled, 1);
    return sns_install_inline_hook(&g_sns_post_build_send_hook,
                                   WX_OFF_SNS_POST_BUILD_SEND,
                                   WX_ORIG_SNS_POST_BUILD_SEND_BYTES,
                                   SNS_POST_BUILD_SEND_PATCH_LEN,
                                   (void *)sns_post_build_send_hook);
}

static void sns_uninstall_post_build_send_patch(void) {
    InterlockedExchange(&g_sns_image_type_patch_enabled, 0);
    g_sns_image_type_patch_content[0] = 0;
}

static void sns_init_model_string(int64_t base, int64_t slot, const char *value) {
    ((SnsStdStringInitFn)(base + WX_OFF_STD_STRING_INIT))(slot);
    wx_set_std_string_heap(base, slot, value ? value : "");
}

static int sns_utf8_to_wide_path(const char *path, WCHAR *out, int max_chars) {
    int n;
    if (!path || !*path || !out || max_chars <= 0) return 0;
    n = MultiByteToWideChar(CP_UTF8, 0, path, -1, out, max_chars);
    if (n <= 0) {
        n = MultiByteToWideChar(CP_ACP, 0, path, -1, out, max_chars);
    }
    if (n <= 0) {
        out[0] = 0;
        return 0;
    }
    out[max_chars - 1] = 0;
    return 1;
}

static void sns_set_local_path_std_string_heap(int64_t base, int64_t header, const char *utf8_path) {
    if (!utf8_path) utf8_path = "";
    /*
     * The official video scanner converts these narrow path fields with
     * sub_180098700 before calling the wide file-existence check.  Keep the
     * bridge input as UTF-8; pre-converting to ACP makes Chinese local paths
     * fail the later official conversion and causes CDN submit to return
     * 0xfff26fc8 (-888888) from sub_18208AF70.
     */
    wx_set_std_string_heap(base, header, utf8_path);
}

static int sns_append_wide(WCHAR *dst, int max_chars, const WCHAR *suffix) {
    int dst_len;
    int suffix_len;
    if (!dst || !suffix || max_chars <= 0) return 0;
    dst_len = lstrlenW(dst);
    suffix_len = lstrlenW(suffix);
    if (dst_len < 0 || suffix_len < 0 || dst_len + suffix_len >= max_chars) return 0;
    copy_bytes(dst + dst_len, suffix, (DWORD)((suffix_len + 1) * sizeof(WCHAR)));
    return 1;
}

static void sns_ascii_safe_segment_w(const char *src, WCHAR *out, int max_chars) {
    int pos = 0;
    const char *p;
    if (!out || max_chars <= 0) return;
    if (src) {
        for (p = src; *p && pos < max_chars - 1; p++) {
            char c = *p;
            if ((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') ||
                (c >= '0' && c <= '9') || c == '_' || c == '-') {
                out[pos++] = (WCHAR)c;
            } else {
                out[pos++] = L'_';
            }
        }
    }
    if (pos <= 0) out[pos++] = L'v';
    out[pos] = 0;
}

static const WCHAR *sns_wide_basename(const WCHAR *path) {
    const WCHAR *base = path;
    const WCHAR *p;
    if (!path) return L"";
    for (p = path; *p; p++) {
        if (*p == L'\\' || *p == L'/') base = p + 1;
    }
    return base;
}

static int sns_wide_starts_with_exact(const WCHAR *s, const WCHAR *prefix) {
    if (!s || !prefix) return 0;
    while (*prefix) {
        if (*s != *prefix) return 0;
        s++;
        prefix++;
    }
    return 1;
}

static int sns_is_video_gate_thumb_path_w(const WCHAR *path) {
    int prefix_len;
    const WCHAR *base;
    if (!path || !*path) return 0;
    prefix_len = lstrlenW(CONTROL_DIR);
    if (prefix_len <= 0) return 0;
    if (!sns_wide_starts_with_exact(path, CONTROL_DIR)) return 0;
    if (path[prefix_len] && path[prefix_len] != L'\\' && path[prefix_len] != L'/') return 0;
    base = sns_wide_basename(path);
    return sns_wide_starts_with_exact(base, L"sns_video_thumb_gate_");
}

static void sns_delete_video_gate_thumbs_for_scan(int64_t scan_obj) {
    int64_t model;
    int64_t media_begin;
    int64_t media_end;
    int count;

    if (!scan_obj || !is_readable_ptr(scan_obj + 88, 8)) return;
    model = r64((void *)(scan_obj + 88));
    if (!model || !is_readable_ptr(model + 296, 8)) return;

    media_begin = r64((void *)(model + 280));
    media_end = r64((void *)(model + 288));
    if (!media_begin || media_end < media_begin || (media_end - media_begin) > 16 * 16) return;

    count = (int)((media_end - media_begin) / 16);
    for (int i = 0; i < count; i++) {
        int64_t entry = media_begin + (i * 16);
        int64_t media;
        char thumb[1024];
        WCHAR thumb_w[SNS_STAGE_WPATH_MAX];

        if (!is_readable_ptr(entry, 16)) continue;
        media = r64((void *)entry);
        if (!media || !is_readable_ptr(media, 416)) continue;
        if (r32((void *)(media + 0)) != 6) continue;
        if (!wx_read_sso_str(media + 104, thumb, sizeof(thumb))) continue;
        if (!sns_utf8_to_wide_path(thumb, thumb_w, SNS_STAGE_WPATH_MAX)) continue;
        if (!sns_is_video_gate_thumb_path_w(thumb_w)) continue;
        DeleteFileW(thumb_w);
    }
}

static int sns_ensure_dir_w(const WCHAR *path) {
    DWORD attr;
    if (!path || !*path) return 0;
    attr = GetFileAttributesW(path);
    if (attr != INVALID_FILE_ATTRIBUTES) return (attr & FILE_ATTRIBUTE_DIRECTORY) != 0;
    if (CreateDirectoryW(path, NULL)) return 1;
    if (GetLastError() == ERROR_ALREADY_EXISTS) {
        attr = GetFileAttributesW(path);
        return (attr != INVALID_FILE_ATTRIBUTES && (attr & FILE_ATTRIBUTE_DIRECTORY));
    }
    return 0;
}

static int sns_get_appdata_w(WCHAR *out, int max_chars) {
    HMODULE shell32;
    SnsSHGetFolderPathWFn sh_get_folder_path;
    DWORD env_len;
    if (!out || max_chars <= 0) return 0;
    out[0] = 0;

    shell32 = GetModuleHandleW(L"shell32.dll");
    if (!shell32) shell32 = LoadLibraryW(L"shell32.dll");
    if (shell32) {
        sh_get_folder_path = (SnsSHGetFolderPathWFn)GetProcAddress(shell32, "SHGetFolderPathW");
        if (sh_get_folder_path && sh_get_folder_path(NULL, CSIDL_APPDATA, NULL, 0, out) >= 0 && out[0]) {
            out[max_chars - 1] = 0;
            return 1;
        }
    }

    env_len = GetEnvironmentVariableW(L"APPDATA", out, (DWORD)max_chars);
    if (env_len > 0 && env_len < (DWORD)max_chars) {
        out[max_chars - 1] = 0;
        return 1;
    }
    out[0] = 0;
    return 0;
}

static int sns_stage_video_under_xwechat(const char *src_utf8,
                                          const char *stage_id,
                                          char *out_utf8,
                                          int out_max) {
    WCHAR src[SNS_STAGE_WPATH_MAX];
    WCHAR dir[SNS_STAGE_WPATH_MAX];
    WCHAR dest[SNS_STAGE_WPATH_MAX];
    WCHAR safe_client[128];
    const WCHAR *leaf;
    DWORD attr;
    int n;

    if (out_utf8 && out_max > 0) out_utf8[0] = 0;
    if (!src_utf8 || !*src_utf8 || !out_utf8 || out_max <= 0) return 0;
    if (strstr(src_utf8, "://")) return 0;
    if (!sns_utf8_to_wide_path(src_utf8, src, SNS_STAGE_WPATH_MAX)) return 0;

    attr = GetFileAttributesW(src);
    if (attr == INVALID_FILE_ATTRIBUTES || (attr & FILE_ATTRIBUTE_DIRECTORY)) return 0;
    leaf = sns_wide_basename(src);
    if (!leaf || !*leaf) return 0;

    if (!sns_get_appdata_w(dir, SNS_STAGE_WPATH_MAX)) return 0;
    if (!sns_append_wide(dir, SNS_STAGE_WPATH_MAX, L"\\Tencent")) return 0;
    if (!sns_ensure_dir_w(dir)) return 0;
    if (!sns_append_wide(dir, SNS_STAGE_WPATH_MAX, L"\\xwechat")) return 0;
    if (!sns_ensure_dir_w(dir)) return 0;
    if (!sns_append_wide(dir, SNS_STAGE_WPATH_MAX, L"\\sns_publish_bridge")) return 0;
    if (!sns_ensure_dir_w(dir)) return 0;

    sns_ascii_safe_segment_w(stage_id, safe_client, (int)(sizeof(safe_client) / sizeof(safe_client[0])));
    if (!sns_append_wide(dir, SNS_STAGE_WPATH_MAX, L"\\")) return 0;
    if (!sns_append_wide(dir, SNS_STAGE_WPATH_MAX, safe_client)) return 0;
    if (!sns_ensure_dir_w(dir)) return 0;

    copy_bytes(dest, dir, (DWORD)((lstrlenW(dir) + 1) * sizeof(WCHAR)));
    if (!sns_append_wide(dest, SNS_STAGE_WPATH_MAX, L"\\")) return 0;
    if (!sns_append_wide(dest, SNS_STAGE_WPATH_MAX, leaf)) return 0;

    if (lstrcmpiW(src, dest) != 0 && !CopyFileW(src, dest, FALSE)) return 0;
    attr = GetFileAttributesW(dest);
    if (attr == INVALID_FILE_ATTRIBUTES || (attr & FILE_ATTRIBUTE_DIRECTORY)) return 0;

    n = WideCharToMultiByte(CP_UTF8, 0, dest, -1, out_utf8, out_max, NULL, NULL);
    if (n <= 0 || n >= out_max) {
        out_utf8[0] = 0;
        return 0;
    }
    out_utf8[out_max - 1] = 0;
    return 1;
}

static void sns_record_video_thumb_map(const char *client_id, const char *thumb_url) {
    char line[4096];
    int n;
    if (!client_id || !*client_id || !thumb_url || !*thumb_url) return;
    if (strstr(thumb_url, "://")) return;
    n = wsprintfA(line, "%s_0\t%s\r\n", client_id, thumb_url);
    if (n > 0 && n < (int)sizeof(line)) {
        append_file_bytes(SNS_VIDEO_THUMB_MAP_FILE, line, (DWORD)n);
    }
}

static void sns_trace_video_stage_paths(const char *client_id,
                                        const char *src_video,
                                        const char *staged_video,
                                        const char *src_thumb,
                                        const char *staged_thumb,
                                        int video_ok,
                                        int thumb_ok) {
    char line[8192];
    char *p = line;
    char *end = line + sizeof(line) - 1;
    if (!sns_publish_trace_enabled()) return;

    append_raw(&p, end, "{\"event\":\"native_video_stage_paths\"");
    sns_trace_append_i64(&p, end, "tick", (int64_t)GetTickCount64());
    sns_trace_append_int(&p, end, "tid", (int)GetCurrentThreadId());
    append_raw(&p, end, ",\"client_id\":\"");
    append_escaped(&p, end, client_id ? client_id : "");
    append_raw(&p, end, "\",\"src_video\":\"");
    append_escaped(&p, end, src_video ? src_video : "");
    append_raw(&p, end, "\",\"staged_video\":\"");
    append_escaped(&p, end, staged_video ? staged_video : "");
    append_raw(&p, end, "\",\"src_thumb\":\"");
    append_escaped(&p, end, src_thumb ? src_thumb : "");
    append_raw(&p, end, "\",\"staged_thumb\":\"");
    append_escaped(&p, end, staged_thumb ? staged_thumb : "");
    append_raw(&p, end, "\",\"video_ok\":");
    append_raw(&p, end, video_ok ? "1" : "0");
    append_raw(&p, end, ",\"thumb_ok\":");
    append_raw(&p, end, thumb_ok ? "1" : "0");
    append_raw(&p, end, "}\r\n");
    *p = 0;
    append_file_bytes(SNS_PUBLISH_TRACE_LOG, line, (DWORD)(p - line));
}

static int sns_build_video_pending_thumb_path(const char *thumb_url,
                                               const char *client_id,
                                               char *out,
                                               int max) {
    const char *p;
    char safe_id[96];
    WCHAR safe_id_w[96];
    WCHAR src_w[SNS_STAGE_WPATH_MAX];
    WCHAR gate_w[SNS_STAGE_WPATH_MAX];
    DWORD src_attr;
    int pos = 0;
    int n;

    if (!thumb_url || !*thumb_url || !out || max <= 0) return 0;
    out[0] = 0;
    if (strstr(thumb_url, "://")) return 0;

    if (client_id && *client_id) {
        for (p = client_id; *p && pos < (int)sizeof(safe_id) - 1; p++) {
            char c = *p;
            if ((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') ||
                (c >= '0' && c <= '9') || c == '_' || c == '-') {
                safe_id[pos++] = c;
            } else {
                safe_id[pos++] = '_';
            }
        }
    }
    if (pos <= 0) safe_id[pos++] = 'v';
    safe_id[pos] = 0;
    for (int i = 0; i <= pos; i++) safe_id_w[i] = (WCHAR)(unsigned char)safe_id[i];

    /*
     * sub_183E375E0 gates video upload registration on local_thumb_url being
     * non-empty and currently missing.  Keep the gate under CONTROL_DIR, the
     * same existing directory used by the earlier working registration traces.
     */
    if (!sns_ensure_dir_w(CONTROL_DIR)) return 0;
    wsprintfW(gate_w,
              L"%s\\sns_video_thumb_gate_%lu_%lu_%s.jpg",
              CONTROL_DIR,
              (unsigned long)GetCurrentProcessId(),
              (unsigned long)GetTickCount(),
              safe_id_w);
    n = WideCharToMultiByte(CP_UTF8, 0, gate_w, -1, out, max, NULL, NULL);
    if (n <= 0 || n >= max) {
        out[0] = 0;
        return 0;
    }
    out[max - 1] = 0;
    DeleteFileW(gate_w);
    if (sns_utf8_to_wide_path(thumb_url, src_w, SNS_STAGE_WPATH_MAX)) {
        src_attr = GetFileAttributesW(src_w);
        if (src_attr != INVALID_FILE_ATTRIBUTES && !(src_attr & FILE_ATTRIBUTE_DIRECTORY)) {
            CopyFileW(src_w, gate_w, FALSE);
        }
    }
    return 1;
}

static int sns_media_path_count(const char *image_paths) {
    int count = 0;
    const char *p = image_paths;
    int in_item = 0;
    if (!p || !*p) return 0;
    while (*p) {
        if (*p == ';') {
            if (in_item) count++;
            in_item = 0;
        } else {
            in_item = 1;
        }
        p++;
    }
    if (in_item) count++;
    return count;
}

static int64_t sns_alloc_thumb_url(int64_t base,
                                   const char *url,
                                   const char *md5,
                                   int local_path_encoding,
                                   int64_t *out_ctl) {
    int64_t ctl;
    int64_t obj;
    if (out_ctl) *out_ctl = 0;
    if (!base || !url || !*url || !out_ctl) return 0;

    ctl = ((FnCall1)(base + WX_OFF_WX_ALLOC))(SNS_THUMB_URL_CTL_SIZE);
    if (!ctl) return 0;
    ZeroMemory((void *)ctl, SNS_THUMB_URL_CTL_SIZE);
    w64((void *)(ctl + 8), 0x100000001LL);
    w64((void *)ctl, base + WX_OFF_SNS_THUMB_URL_VT);
    obj = ctl + 16;

    /* sub_183D72BA0/sub_183D72EC0 layout: +0 url, +32 type, +40 md5, +72 key,
       +80 enc_idx, +88 token, +120 videomd5, +168 aeskey. */
    w64((void *)(obj + 24), 15);
    w64((void *)(obj + 64), 15);
    w64((void *)(obj + 112), 15);
    w64((void *)(obj + 144), 15);
    w64((void *)(obj + 192), 15);
    if (local_path_encoding && !strstr(url, "://")) {
        sns_set_local_path_std_string_heap(base, obj + 0, url);
    } else {
        wx_set_std_string_heap(base, obj + 0, url);
    }
    w64((void *)(obj + 32), 1);
    wx_set_std_string_heap(base, obj + 40, md5 ? md5 : "");
    w64((void *)(obj + 72), 0);
    w32((void *)(obj + 80), 0);
    *out_ctl = ctl;
    return obj;
}

static int64_t sns_alloc_media_item(int64_t base,
                                    const char *client_id,
                                    const char *image_path,
                                    const char *local_data_path,
                                    const char *md5,
                                    const char *cdn_url,
                                    const char *thumb_url,
                                    int media_type,
                                    int width,
                                    int height,
                                    int duration_ms,
                                    int64_t *out_ctl) {
    int64_t ctl;
    int64_t obj;
    int64_t thumb_ctl = 0;
    int64_t thumb_obj;
    int64_t thumb_vec;
    const char *local_thumb_url = (media_type == 6) ? "" : image_path;
    const char *thumb_object_url;
    if (out_ctl) *out_ctl = 0;
    if (!base || !client_id || !image_path || !*image_path || !out_ctl) return 0;

    ctl = ((FnCall1)(base + WX_OFF_WX_ALLOC))(SNS_MEDIA_ITEM_CTL_SIZE);
    if (!ctl) return 0;
    ZeroMemory((void *)ctl, SNS_MEDIA_ITEM_CTL_SIZE);
    w64((void *)(ctl + 8), 0x100000001LL);
    w64((void *)ctl, base + WX_OFF_SNS_MEDIA_ITEM_VT);
    obj = ctl + 16;

    /*
     * sub_183DA3630 parses upload_media:
     * +0 type, +8 client_id, +40 local_src_url, +72 local_data_url,
     * +104 local_thumb_url, +136/+140 image size, +152 md5,
     * +384/+392/+400 vector<shared_ptr<thumb_url>>, +408 is_finished.
     */
    w64((void *)(obj + 32), 15);
    w64((void *)(obj + 64), 15);
    w64((void *)(obj + 96), 15);
    w64((void *)(obj + 128), 15);
    w64((void *)(obj + 176), 15);
    w64((void *)(obj + 208), 15);
    w64((void *)(obj + 248), 15);
    w64((void *)(obj + 296), 15);
    w64((void *)(obj + 328), 15);
    w64((void *)(obj + 376), 15);
    w32((void *)(obj + 148), -1);

    w32((void *)(obj + 0), media_type);
    wx_set_std_string_heap(base, obj + 8, client_id);
    if (media_type == 6) {
        /*
         * Phase 1 transcodes local_src(+40) into PostObject+384. Keep
         * local_data(+72) on that same, initially absent output path so the
         * scanner takes its fresh-source branch and phase 2 uploads the
         * completed transcode rather than the raw input.
         */
        sns_set_local_path_std_string_heap(base, obj + 40, image_path);
        sns_set_local_path_std_string_heap(
            base,
            obj + 72,
            (local_data_path && *local_data_path) ? local_data_path : image_path);
    } else {
        wx_set_std_string_heap(base, obj + 40, image_path);
        wx_set_std_string_heap(base, obj + 72, image_path);
    }
    if (media_type == 6 && thumb_url && *thumb_url && !strstr(thumb_url, "://")) {
        local_thumb_url = thumb_url;
    }
    if (media_type == 6 && local_thumb_url[0] && !strstr(local_thumb_url, "://")) {
        sns_set_local_path_std_string_heap(base, obj + 104, local_thumb_url);
    } else {
        wx_set_std_string_heap(base, obj + 104, local_thumb_url);
    }
    if (width > 0) *(float *)(obj + 136) = (float)width;
    if (height > 0) *(float *)(obj + 140) = (float)height;
    if (duration_ms > 0) *(float *)(obj + 144) = ((float)duration_ms) / 1000.0f;
    wx_set_std_string_heap(base, obj + 152, md5 ? md5 : "");
    if (cdn_url && *cdn_url) {
        wx_set_std_string_heap(base, obj + 184, cdn_url);
        w64((void *)(obj + 216), 1);
    }
    w8((void *)(obj + 408), (media_type == 6 && (!cdn_url || !*cdn_url)) ? 0 : 1);

    thumb_object_url = (thumb_url && *thumb_url) ? thumb_url : ((media_type == 6) ? "" : image_path);
    /*
     * For local video, media+384 is the CDN thumbnail vector. 185C54DF0
     * appends it after upload success; pre-seeding a local path makes the XML
     * builder see a thumb before media+184/media+408 are ready.
     */
    if (thumb_object_url && *thumb_object_url && !(media_type == 6 && (!cdn_url || !*cdn_url))) {
        thumb_obj = sns_alloc_thumb_url(base,
                                        thumb_object_url,
                                        md5,
                                        media_type == 6,
                                        &thumb_ctl);
        if (!thumb_obj || !thumb_ctl) return 0;
        thumb_vec = ((FnCall1)(base + WX_OFF_WX_ALLOC))(16);
        if (!thumb_vec) return 0;
        ZeroMemory((void *)thumb_vec, 16);
        w64((void *)thumb_vec, thumb_obj);
        w64((void *)(thumb_vec + 8), thumb_ctl);
        w64((void *)(obj + 384), thumb_vec);
        w64((void *)(obj + 392), thumb_vec + 16);
        w64((void *)(obj + 400), thumb_vec + 16);
    }

    *out_ctl = ctl;
    return obj;
}

static int sns_attach_single_media(int64_t base,
                                   int64_t model,
                                   const char *client_id,
                                   const char *image_path,
                                   const char *md5,
                                   const char *cdn_url,
                                   const char *thumb_url,
                                   int media_type,
                                   int width,
                                   int height,
                                   int duration_ms) {
    int64_t media_ctl = 0;
    int64_t media_obj;
    int64_t media_vec;
    if (!base || !model || !image_path || !*image_path) return 0;

    media_obj = sns_alloc_media_item(base, client_id, image_path, image_path,
                                     md5, cdn_url, thumb_url, media_type,
                                     width, height, duration_ms, &media_ctl);
    if (!media_obj || !media_ctl) return 0;
    media_vec = ((FnCall1)(base + WX_OFF_WX_ALLOC))(16);
    if (!media_vec) return 0;
    ZeroMemory((void *)media_vec, 16);
    w64((void *)media_vec, media_obj);
    w64((void *)(media_vec + 8), media_ctl);
    w64((void *)(model + 280), media_vec);
    w64((void *)(model + 288), media_vec + 16);
    w64((void *)(model + 296), media_vec + 16);
    return 1;
}

static int sns_attach_media_list(int64_t base,
                                 int64_t model,
                                 const char *client_id,
                                 const char *image_paths,
                                 const char *local_data_path,
                                 const char *md5,
                                 const char *cdn_url,
                                 const char *thumb_url,
                                 int media_type,
                                 int width,
                                 int height,
                                 int duration_ms) {
    char paths[2048];
    char media_client_id[160];
    char *items[9];
    int count = 0;
    char *p;
    int64_t media_vec;
    if (!base || !model || !client_id || !image_paths || !*image_paths) return 0;

    safe_copy(paths, sizeof(paths), image_paths);
    p = paths;
    while (*p && count < 9) {
        char *start = p;
        while (*p && *p != ';') p++;
        if (*p == ';') *p++ = 0;
        if (*start) items[count++] = start;
    }
    if (count <= 0) return 0;

    media_vec = ((FnCall1)(base + WX_OFF_WX_ALLOC))(16 * count);
    if (!media_vec) return 0;
    ZeroMemory((void *)media_vec, 16 * count);
    for (int i = 0; i < count; i++) {
        int64_t media_ctl = 0;
        int64_t media_obj;
        wsprintfA(media_client_id, "%s_%d", client_id, i);
        const char *item_data_path =
            (media_type == 6 && local_data_path && *local_data_path) ? local_data_path : items[i];
        media_obj = sns_alloc_media_item(base, media_client_id, items[i], item_data_path,
                                         md5, cdn_url, thumb_url, media_type,
                                         width, height, duration_ms, &media_ctl);
        if (!media_obj || !media_ctl) return 0;
        w64((void *)(media_vec + (16 * i)), media_obj);
        w64((void *)(media_vec + (16 * i) + 8), media_ctl);
    }
    w64((void *)(model + 280), media_vec);
    w64((void *)(model + 288), media_vec + (16 * count));
    w64((void *)(model + 296), media_vec + (16 * count));
    return 1;
}

static int64_t sns_alloc_publish_model(int64_t base,
                                       const char *content,
                                       const char *client_id,
                                       const char *image_path,
                                       const char *video_output_path,
                                       const char *md5,
                                       const char *cdn_url,
                                       const char *thumb_url,
                                       int is_video,
                                       int width,
                                       int height,
                                       int duration_ms,
                                       int64_t *out_ctl) {
    int64_t ctl;
    int64_t obj;
    DWORD now = (DWORD)time(NULL);
    if (out_ctl) *out_ctl = 0;
    if (!base || !content || !client_id || !out_ctl) return 0;

    /*
     * Static chain:
     *   callers allocate 664 bytes, set off_188392188, object starts at +16,
     *   then call sub_183E28F90. sub_182C0FBD0 later lazily calls
     *   sub_183E2B180(model) to create the real TimelineObject at model+8/+16.
     */
    ctl = ((FnCall1)(base + WX_OFF_WX_ALLOC))(SNS_PUBLISH_MODEL_CTL_SIZE);
    if (!ctl) return 0;
    ZeroMemory((void *)ctl, SNS_PUBLISH_MODEL_CTL_SIZE);
    w64((void *)(ctl + 8), 0x100000001LL);
    w64((void *)ctl, base + WX_OFF_SNS_PUBLISH_MODEL_VT);
    obj = ctl + 16;
    ((SnsPublishModelCtorFn)(base + WX_OFF_SNS_PUBLISH_MODEL_CTOR))(obj, 0);

    /*
     * +24 is read by sub_185559170; nonzero selects the normal first-publish
     * sub_185558B70 -> sub_183B1B290 branch. +48 belongs to the existing-post
     * continue gate; +52 is ui_type, +64 client_id, and +96 content_desc.
     */
    if (image_path && *image_path) {
        w8((void *)(obj + 24), 1);
    }
    /*
     * model+48 becomes TimelineObject/ContentObject contentStyle.
     * Static UI enum: img=1, text=2, littleVideo=15. Marking text-only as
     * 15 makes the Moments renderer reserve a non-clickable media placeholder.
     */
    w32((void *)(obj + 48), (image_path && *image_path) ? (is_video ? 15 : 1) : 2);
    w32((void *)(obj + 52), 0);
    w32((void *)(obj + 56), (int32_t)now);
    wx_set_std_string_heap(base, obj + 64, client_id);
    wx_set_std_string_heap(base, obj + 96, content);
    if (is_video) {
        wx_set_std_string_heap(base, obj + 304, "微信小视频");
        wx_set_std_string_heap(base, obj + 336, "https://support.weixin.qq.com/cgi-bin/mmsupport-bin/readtemplate?t=page/common_page__upgrade&v=1");
        /*
         * The official UI wrappers write these before publish context creation:
         * sub_181C948E0: model+624 = entrance, model+628 = sub_181C8DE20(...)
         * Captured video publish/register path uses entrance=5, scene=2.
         */
        w32((void *)(obj + 624), 5);
        w32((void *)(obj + 628), 2);
    }
    w32((void *)(obj + 128), 0);
    w32((void *)(obj + 132), 0);
    w32((void *)(obj + 136), 0);
    /*
     * IDA 0x184CB0920 reads model+552 and sets the mmsnspost private bit only
     * when the value is 2; 0x183E2A5C0 also serializes <private> from == 2.
     */
    w32((void *)(obj + 552), WX_SNS_VISIBILITY_PUBLIC_STATE);
    if (image_path && *image_path) {
        if (!sns_attach_media_list(base, obj, client_id, image_path, video_output_path, md5,
                                   cdn_url, thumb_url, is_video ? 6 : 2, width, height, duration_ms)) {
            return 0;
        }
    }
    *out_ctl = ctl;
    return obj;
}

static int64_t sns_resolve_publish_manager(int64_t base) {
    BYTE root_sp[16];
    BYTE stage_sp[16];
    BYTE manager_sp[16];
    int64_t root;
    int64_t stage;
    int64_t manager;

    ZeroMemory(root_sp, sizeof(root_sp));
    ZeroMemory(stage_sp, sizeof(stage_sp));
    ZeroMemory(manager_sp, sizeof(manager_sp));

    ((SnsOutFn)(base + WX_OFF_SNS_MANAGER_ROOT))((int64_t)root_sp);
    root = r64(root_sp);
    if (!root) return 0;
    ((SnsOutFromFn)(base + WX_OFF_SNS_MANAGER_STAGE1))(root, (int64_t)stage_sp);
    stage = r64(stage_sp);
    if (!stage) return 0;
    ((SnsOutFromFn)(base + WX_OFF_SNS_MANAGER_STAGE2))(stage, (int64_t)manager_sp);
    manager = r64(manager_sp);
    return manager;
}

static void sns_video_ctx_enter_trace(const char *event,
                                      int64_t ctx,
                                      int64_t service,
                                      int64_t global,
                                      int64_t getter,
                                      int64_t shared_model,
                                      int64_t ret,
                                      int ok) {
    char line[32768];
    char *p = line;
    char *end = line + sizeof(line) - 1;
    int64_t base = (int64_t)wxb_weixin_module();
    if (!sns_publish_trace_enabled()) return;

    append_raw(&p, end, "{\"event\":\"");
    append_escaped(&p, end, event ? event : "native_video_ctx_enter");
    append_raw(&p, end, "\"");
    sns_trace_append_i64(&p, end, "tick", (int64_t)GetTickCount64());
    sns_trace_append_int(&p, end, "tid", (int)GetCurrentThreadId());
    sns_trace_append_hex(&p, end, "base", base);
    sns_trace_append_hex(&p, end, "global_slot", base ? base + WX_OFF_GLOBAL_SERVICE_LOCATOR : 0);
    sns_trace_append_hex(&p, end, "global_slot_rva", WX_OFF_GLOBAL_SERVICE_LOCATOR);
    sns_trace_append_hex(&p, end, "ctx_service_rva", WX_OFF_SNS_PUBLISH_CTX_SERVICE);
    sns_trace_append_hex(&p, end, "ctx_enter_rva", WX_OFF_SNS_PUBLISH_CTX_ENTER);
    sns_trace_append_hex(&p, end, "global", global);
    sns_trace_append_hex(&p, end, "service", service);
    sns_trace_append_hex(&p, end, "getter", getter);
    sns_trace_append_hex(&p, end, "ctx", ctx);
    sns_trace_append_hex(&p, end, "shared_model", shared_model);
    sns_trace_append_pair(&p, end, "shared_model", shared_model);
    sns_trace_append_hex(&p, end, "ret", ret);
    sns_trace_append_int(&p, end, "ok", ok);
    append_raw(&p, end, "}\r\n");
    *p = 0;
    append_file_bytes(SNS_PUBLISH_TRACE_LOG, line, (DWORD)(p - line));
}

static int64_t sns_resolve_publish_context(int64_t base,
                                           int64_t *out_global,
                                           int64_t *out_service,
                                           int64_t *out_getter) {
    int64_t global = 0;
    int64_t service = 0;
    int64_t vt = 0;
    int64_t getter = 0;
    int64_t ctx = 0;

    if (out_global) *out_global = 0;
    if (out_service) *out_service = 0;
    if (out_getter) *out_getter = 0;
    if (!base || !is_readable_ptr(base + WX_OFF_GLOBAL_SERVICE_LOCATOR, 8)) return 0;

    __try {
        global = r64((void *)(base + WX_OFF_GLOBAL_SERVICE_LOCATOR));
        if (out_global) *out_global = global;
        if (!global) return 0;

        service = ((SnsGlobalServiceFn)(base + WX_OFF_SNS_PUBLISH_CTX_SERVICE))(global);
        if (out_service) *out_service = service;
        if (!service || !is_readable_ptr(service, 8)) return 0;

        vt = r64((void *)service);
        if (!vt || !is_readable_ptr(vt + 112, 8)) return 0;
        getter = r64((void *)(vt + 112));
        if (out_getter) *out_getter = getter;
        if (!getter || !is_readable_ptr(getter, 1)) return 0;

        ctx = ((SnsCtxGetterFn)getter)(service);
        if (!ctx || !is_readable_ptr(ctx, 8)) return 0;
        return ctx;
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
        return 0;
    }
}

static int sns_video_try_ctx_enter(int64_t base, SnsPublishTask *task) {
    int64_t global = 0;
    int64_t service = 0;
    int64_t getter = 0;
    int64_t ctx = 0;
    int64_t ret = 0;
    int64_t entry_sp[2];

    if (!base || !task || !task->model || !task->model_ctl) {
        sns_video_ctx_enter_trace("native_video_ctx_enter_invalid_args",
                                  0, 0, 0, 0,
                                  task ? (int64_t)task->shared_model : 0,
                                  0, 0);
        return 0;
    }

    ctx = sns_resolve_publish_context(base, &global, &service, &getter);
    task->video_publish_ctx = ctx;
    sns_video_ctx_enter_trace("native_video_ctx_enter_resolve",
                              ctx, service, global, getter,
                              (int64_t)task->shared_model,
                              0, ctx ? 1 : 0);
    if (!ctx) return 0;

    entry_sp[0] = task->model;
    entry_sp[1] = task->model_ctl;

    __try {
        /*
         * sub_183E1DEC0 owns/releases its shared_ptr argument. Pass a fresh
         * strong reference and keep task->shared_model alive for final publish.
         */
        _InterlockedIncrement((volatile LONG *)(task->model_ctl + 8));
        sns_video_ctx_enter_trace("native_video_ctx_enter_call",
                                  ctx, service, global, getter,
                                  (int64_t)entry_sp,
                                  0, 1);
        ret = ((SnsPublishCtxEnterFn)(base + WX_OFF_SNS_PUBLISH_CTX_ENTER))(
            ctx,
            (int64_t)entry_sp);
        task->video_ctx_enter_ret = ret;
        sns_video_ctx_enter_trace("native_video_ctx_enter_done",
                                  ctx, service, global, getter,
                                  (int64_t)task->shared_model,
                                  ret, ret ? 1 : 0);
        return ret ? 1 : 0;
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
        task->video_ctx_enter_ret = 0;
        sns_video_ctx_enter_trace("native_video_ctx_enter_exception",
                                  ctx, service, global, getter,
                                  (int64_t)task->shared_model,
                                  0, 0);
        return 0;
    }
}

static int64_t sns_first_media_from_model(int64_t model) {
    int64_t media_begin;
    int64_t media_end;
    int64_t media;
    if (!model || !is_readable_ptr(model + 296, 8)) return 0;
    __try {
        media_begin = r64((void *)(model + 280));
        media_end = r64((void *)(model + 288));
        if (!media_begin || media_end < media_begin || (media_end - media_begin) < 16) return 0;
        if ((media_end - media_begin) > 16 * 16) return 0;
        if (!is_readable_ptr(media_begin, 16)) return 0;
        media = r64((void *)media_begin);
        if (!media || !is_readable_ptr(media, 416)) return 0;
        return media;
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
        return 0;
    }
}

static int sns_video_media_upload_ready(int64_t model) {
    int64_t media = sns_first_media_from_model(model);
    char cdn[768];
    if (!media) return 0;
    cdn[0] = 0;
    __try {
        if (r32((void *)(media + 0)) != 6) return 1;
        if (!*((BYTE *)(media + 408))) return 0;
        if (!wx_read_sso_str(media + 184, cdn, sizeof(cdn))) return 0;
        return cdn[0] ? 1 : 0;
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
        return 0;
    }
}

static int sns_wait_video_media_upload_ready(int64_t model, int wait_ms) {
    int loops;
    if (sns_video_media_upload_ready(model)) return 1;
    if (wait_ms <= 0) wait_ms = 20000;
    loops = wait_ms / 200;
    if (loops < 1) loops = 1;
    if (loops > 600) loops = 600;
    for (int i = 0; i < loops; i++) {
        Sleep(200);
        if (sns_video_media_upload_ready(model)) return 1;
    }
    return 0;
}

static int64_t sns_alloc_publish_wrapper(int64_t base,
                                         int64_t model,
                                         int64_t model_ctl,
                                         int64_t *out_ctl) {
    int64_t ctl;
    int64_t obj;
    int64_t model_sp[2];
    if (out_ctl) *out_ctl = 0;
    if (!base || !model || !model_ctl || !out_ctl) return 0;

    ctl = ((FnCall1)(base + WX_OFF_WX_ALLOC))(SNS_PUBLISH_WRAP_CTL_SIZE);
    if (!ctl) return 0;
    ZeroMemory((void *)ctl, SNS_PUBLISH_WRAP_CTL_SIZE);
    w64((void *)(ctl + 8), 0x100000001LL);
    w64((void *)ctl, base + WX_OFF_SNS_PUBLISH_WRAP_CTL_VT);
    obj = ctl + 16;

    /*
     * sub_183E30040 consumes/releases the input shared_ptr while also copying
     * it into wrapper+88/+96. Give that temporary argument its own strong ref.
     */
    _InterlockedIncrement((volatile LONG *)(model_ctl + 8));
    model_sp[0] = model;
    model_sp[1] = model_ctl;
    ((SnsPublishWrapInitFn)(base + WX_OFF_SNS_PUBLISH_WRAP_INIT))(obj, (int64_t)model_sp);

    *out_ctl = ctl;
    return obj;
}

static void sns_video_async_run_trace(const char *event,
                                      int64_t wrapper,
                                      int64_t ctx,
                                      int64_t token,
                                      int64_t cb_a,
                                      int64_t cb_b,
                                      int64_t ret,
                                      int ok) {
    char line[4096];
    char *p = line;
    char *end = line + sizeof(line) - 1;
    int64_t base = (int64_t)wxb_weixin_module();
    int64_t dptr = 0;
    int64_t stored_token = 0;
    if (!sns_publish_trace_enabled()) return;

    append_raw(&p, end, "{\"event\":\"");
    append_escaped(&p, end, event ? event : "native_video_async_run");
    append_raw(&p, end, "\"");
    sns_trace_append_i64(&p, end, "tick", (int64_t)GetTickCount64());
    sns_trace_append_int(&p, end, "tid", (int)GetCurrentThreadId());
    sns_trace_append_hex(&p, end, "base", base);
    sns_trace_append_hex(&p, end, "async_run_rva", WX_OFF_SNS_PUBLISH_ASYNC_RUN);
    sns_trace_append_hex(&p, end, "ref_token_rva", WX_OFF_QOBJECT_REF_TOKEN);
    sns_trace_append_hex(&p, end, "wrapper", wrapper);
    sns_trace_append_hex(&p, end, "ctx", ctx);
    sns_trace_append_hex(&p, end, "ctx_token", token);
    sns_trace_append_hex(&p, end, "cb_a", cb_a);
    sns_trace_append_hex(&p, end, "cb_b", cb_b);
    sns_trace_append_i64(&p, end, "ret", ret);
    sns_trace_append_int(&p, end, "ok", ok);
    __try {
        if (ctx && is_readable_ptr(ctx + 8, 8)) {
            dptr = r64((void *)(ctx + 8));
            sns_trace_append_hex(&p, end, "ctx_dptr", dptr);
            if (dptr && is_readable_ptr(dptr + 80, 8)) {
                stored_token = r64((void *)(dptr + 80));
                sns_trace_append_hex(&p, end, "ctx_dptr_token80", stored_token);
            }
        }
        if (token && is_readable_ptr(token, 8)) {
            sns_trace_append_int(&p, end, "token_ref0", r32((void *)token));
            sns_trace_append_int(&p, end, "token_ref4", r32((void *)(token + 4)));
        }
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
        sns_trace_append_int(&p, end, "trace_exception", 1);
    }
    append_raw(&p, end, "}\r\n");
    *p = 0;
    append_file_bytes(SNS_PUBLISH_TRACE_LOG, line, (DWORD)(p - line));
}

static int sns_video_dispatch_async_run(int64_t base,
                                        int64_t wrapper,
                                        int64_t *out_ret,
                                        int64_t *out_token) {
    BYTE cb_a[64];
    BYTE cb_b[64];
    int64_t ctx_pair[2];
    int64_t token = 0;
    int64_t ret = 0;
    int token_ok = 0;

    if (out_ret) *out_ret = 0;
    if (out_token) *out_token = 0;
    if (!base || !wrapper) {
        sns_video_async_run_trace("native_video_async_run_invalid_args",
                                  wrapper, wrapper, 0, 0, 0, 0, 0);
        return 0;
    }

    ZeroMemory(cb_a, sizeof(cb_a));
    ZeroMemory(cb_b, sizeof(cb_b));
    ctx_pair[0] = 0;
    ctx_pair[1] = wrapper;

    __try {
        /*
         * Official callers pass { sub_180264580(ctx), ctx } to 183E336C0.
         * The publish wrapper is a QObject-derived object after 183E30040.
         */
        if (!is_readable_ptr(wrapper + 8, 8)) {
            sns_video_async_run_trace("native_video_async_run_bad_ctx",
                                      wrapper, wrapper, 0,
                                      (int64_t)cb_a, (int64_t)cb_b, 0, 0);
            return 0;
        }
        token = ((SnsQObjectRefTokenFn)(base + WX_OFF_QOBJECT_REF_TOKEN))(wrapper);
        ctx_pair[0] = token;
        if (out_token) *out_token = token;
        if (token && is_readable_ptr(token, 8) && r32((void *)(token + 4))) {
            token_ok = 1;
        }

        sns_video_async_run_trace("native_video_async_run_enter",
                                  wrapper, wrapper, token,
                                  (int64_t)cb_a, (int64_t)cb_b, 0, token_ok);
        ret = ((SnsPublishAsyncRunFn)(base + WX_OFF_SNS_PUBLISH_ASYNC_RUN))(
            wrapper,
            (int64_t)ctx_pair,
            (int64_t)cb_a,
            (int64_t)cb_b);
        if (out_ret) *out_ret = ret;
        sns_video_async_run_trace(token_ok ?
                                      "native_video_async_run_leave" :
                                      "native_video_async_run_token_not_ready",
                                  wrapper, wrapper, token,
                                  (int64_t)cb_a, (int64_t)cb_b, ret, token_ok);
        return token_ok;
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
        sns_video_async_run_trace("native_video_async_run_exception",
                                  wrapper, wrapper, token,
                                  (int64_t)cb_a, (int64_t)cb_b, ret, 0);
        return 0;
    }
}

static int build_sns_publish_queue_asm(BYTE *p, int64_t task_addr, int64_t worker_addr) {
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

static int64_t sns_resolve_publish_worker_context(int64_t base, int64_t *out_ctl) {
    int64_t root = 0;
    int64_t stage1[2] = {0, 0};
    int64_t stage2[2] = {0, 0};
    int64_t context_sp[2] = {0, 0};
    int64_t interface_tag[2] = {0, 0};
    int64_t context = 0;
    int64_t context_ctl = 0;
    int valid = 0;

    if (out_ctl) *out_ctl = 0;
    if (!base) return 0;

    __try {
        int64_t vt;
        int64_t method;

        root = ((SnsNoArgsFn)(base + WX_OFF_GLOBAL_CONTEXT_ROOT))();
        if (!root || !is_readable_ptr(root, 8)) __leave;

        vt = r64((void *)root);
        if (!vt || !is_readable_ptr(vt + 96, 8)) __leave;
        method = r64((void *)(vt + 96));
        if (!method || !is_readable_ptr(method, 1)) __leave;
        ((SnsSharedOutMethodFn)method)(root, (int64_t)stage1);
        if (!stage1[0] || !stage1[1] || !is_readable_ptr(stage1[0], 8)) __leave;

        vt = r64((void *)stage1[0]);
        if (!vt || !is_readable_ptr(vt + 48, 8)) __leave;
        method = r64((void *)(vt + 48));
        if (!method || !is_readable_ptr(method, 1)) __leave;
        ((SnsSharedOutMethodFn)method)(stage1[0], (int64_t)stage2);
        if (!stage2[0] || !stage2[1] || !is_readable_ptr(stage2[0], 8)) __leave;

        interface_tag[0] = base + WX_OFF_SNS_PUBLISH_WORKER_CONTEXT_IFACE;
        if (!is_readable_ptr(interface_tag[0], 32)) __leave;
        ((SnsContextInterfaceCastFn)(base + WX_OFF_CONTEXT_INTERFACE_CAST))(
            stage2[0], (int64_t)context_sp, (int64_t)interface_tag);
        if (!context_sp[0] || !context_sp[1] ||
            !is_readable_ptr(context_sp[0], 280) ||
            !is_readable_ptr(context_sp[1], 16)) {
            __leave;
        }

        context = context_sp[0];
        context_ctl = context_sp[1];
        context_sp[1] = 0;
        valid = 1;
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
        valid = 0;
        context = 0;
        context_ctl = 0;
        stage1[1] = 0;
        stage2[1] = 0;
        context_sp[1] = 0;
    }

    if (context_sp[1] && is_readable_ptr(context_sp[1], 16)) {
        sns_release_shared_ctl(context_sp[1]);
    }
    if (stage2[1] && is_readable_ptr(stage2[1], 16)) {
        sns_release_shared_ctl(stage2[1]);
    }
    if (stage1[1] && is_readable_ptr(stage1[1], 16)) {
        sns_release_shared_ctl(stage1[1]);
    }

    if (!valid) {
        if (context_ctl && is_readable_ptr(context_ctl, 16)) {
            sns_release_shared_ctl(context_ctl);
        }
        return 0;
    }
    if (out_ctl) {
        *out_ctl = context_ctl;
    } else {
        sns_release_shared_ctl(context_ctl);
    }
    return context;
}

static int sns_publish_start_local_video(int64_t base, SnsPublishTask *task) {
    int64_t context_ctl = 0;
    int64_t context = 0;
    int64_t context_state = 0;
    int scheduled = 0;

    if (!base || !task || !task->model || !task->model_ctl) return 0;

    task->entry_calls = 0;
    task->phase = 28;
    task->call_result = 0;
    task->first_call_result = 0;
    task->wait_status = WAIT_TIMEOUT;
    InterlockedExchange(&task->status, 3);

    context = sns_resolve_publish_worker_context(base, &context_ctl);
    task->video_worker_context = context;
    task->video_worker_context_ctl = context_ctl;
    __try {
        if (context && is_readable_ptr(context + 264, 8)) {
            context_state = r64((void *)(context + 264));
            if (!context_state || !is_readable_ptr(context_state, 8)) {
                context = 0;
            }
        } else {
            context = 0;
        }
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
        context = 0;
        context_state = 0;
    }

    sns_publish_trace_log(context ? "native_video_worker_context_resolved" :
                                    "native_video_worker_context_failed",
                          task->model, (int64_t)task->shared_model,
                          task->video_worker_context, context_ctl,
                          0, context_state, context ? 1 : 0);
    if (!context) {
        if (context_ctl) sns_release_shared_ctl(context_ctl);
        task->video_worker_context_ctl = 0;
        task->call_result = -28;
        task->wait_status = WAIT_FAILED;
        InterlockedExchange(&task->status, -28);
        return 0;
    }

    sns_publish_trace_log("native_video_immediate_schedule_enter",
                          task->model, (int64_t)task->shared_model,
                          context, context_state,
                          0, 0, 0);
    __try {
        ((SnsPublishImmediateScheduleFn)(base + WX_OFF_SNS_PUBLISH_IMMEDIATE_SCHEDULE))(
            context, (int64_t)task->shared_model);
        scheduled = 1;
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
        task->seh_code = GetExceptionCode();
        scheduled = 0;
    }

    sns_release_shared_ctl(context_ctl);
    task->video_worker_context_ctl = 0;
    InterlockedExchange(&task->video_worker_scheduled, scheduled ? 1 : 0);
    sns_publish_trace_log(scheduled ? "native_video_immediate_schedule_leave" :
                                    "native_video_immediate_schedule_exception",
                          task->model, (int64_t)task->shared_model,
                          context, context_state,
                          0, 0, 1);
    if (!scheduled) {
        task->call_result = -29;
        task->wait_status = WAIT_FAILED;
        InterlockedExchange(&task->status, -29);
        return 0;
    }

    task->entry_calls = 1;
    if (InterlockedCompareExchange(&task->status, 0, 0) == 3) {
        task->phase = 29;
    }
    return 1;
}

static void sns_publish_finish_ready(int64_t base, SnsPublishTask *task) {
    if (!base || !task) return;

    task->phase = 30;
    if (task->has_image && !task->is_video) {
        int64_t post_sp[2] = {0, 0};
        task->entry_calls = 0;
        task->phase = 33;
        ((SnsPublishBuildPostFn)(base + WX_OFF_SNS_PUBLISH_BUILD_POST))(
            (int64_t)post_sp,
            (int64_t)task->shared_model);
        task->post_obj = post_sp[0];
        task->post_ctl = post_sp[1];
        if (!task->post_obj) {
            task->call_result = -7;
            task->wait_status = WAIT_FAILED;
            InterlockedExchange(&task->status, -7);
            return;
        }
        task->phase = 34;
        task->call_result = ((SnsPostRunFn)(base + WX_OFF_SNS_POST_RUN))(task->post_obj, 3);
    } else if (task->has_image && task->is_video && !task->image_cdn_url[0]) {
        sns_publish_start_local_video(base, task);
        return;
    } else {
        task->entry_calls = 1;
        task->call_result = ((SnsPublishEntryFn)(base + WX_OFF_SNS_PUBLISH_ENTRY))(
            task->manager,
            (int64_t)task->shared_model);
    }
    task->first_call_result = task->call_result;
    if (task->has_image && (!task->is_video || !task->image_cdn_url[0])) {
        task->call_result = 1;
    } else {
        task->call_result &= 0xFF;
        task->first_call_result = task->call_result;
    }
    task->phase = 31;
    task->wait_status = WAIT_OBJECT_0;
    InterlockedExchange(&task->status, task->call_result ? 1 : -6);
}

static void sns_publish_upper_worker(int64_t task_addr) {
    SnsPublishTask *task = (SnsPublishTask *)task_addr;
    int64_t base = (int64_t)wxb_weixin_module();
    if (!task || !base) return;
    if (InterlockedCompareExchange(&task->status, 2, 0) != 0) return;

    __try {
        task->phase = 10;
        task->manager = sns_resolve_publish_manager(base);
        if (!task->manager) {
            task->call_result = -4;
            task->wait_status = WAIT_FAILED;
            InterlockedExchange(&task->status, -4);
            __leave;
        }

        task->phase = 20;
        task->model = sns_alloc_publish_model(base,
                                              task->content,
                                              task->client_id,
                                              task->has_image ? task->image_path : "",
                                              task->is_video ? task->video_output_path : "",
                                              task->image_md5,
                                              task->image_cdn_url,
                                              task->image_thumb_url,
                                              task->is_video,
                                              task->image_width,
                                              task->image_height,
                                              task->image_duration_ms,
                                              &task->model_ctl);
        if (!task->model) {
            task->call_result = -5;
            task->wait_status = WAIT_FAILED;
            InterlockedExchange(&task->status, -5);
            __leave;
        }
        task->model_flag24 = is_readable_ptr(task->model + 24, 1) ? (int)*(BYTE *)(task->model + 24) : -1;
        task->model_type48 = is_readable_ptr(task->model + 48, 4) ? (int)r32((void *)(task->model + 48)) : -1;

        task->shared_model[0] = task->model;
        task->shared_model[1] = task->model_ctl;
        if (task->model_ctl) {
            /*
             * 182BE2100 consumes/releases the input shared_ptr. Keep one
             * native-owned strong reference so the async video scan/upload
             * path cannot observe a freed publish model after this worker
             * returns.
             */
            _InterlockedIncrement((volatile LONG *)(task->model_ctl + 8));
        }
        sns_publish_trace_log("native_model_allocated",
                              task->model,
                              (int64_t)task->shared_model,
                              0,
                              0,
                              0,
                              0,
                              0);
        /*
         * Local video must enter the official publish-model queue.  Static IDA
         * closure shows 182BE2100 creates/reuses SnsPostObject, then
         * 184CAD900(stage 2) builds the CDN upload task and backfills
         * media+184/+384/+408 before stage 3 posts /mmsnspost.
         */
        sns_publish_finish_ready(base, task);
    }
    __except (sns_capture_exception(&task->seh_code,
                                    &task->seh_addr,
                                    &task->seh_fault,
                                    &task->ctx_rdi,
                                    &task->ctx_rcx,
                                    &task->ctx_rdx,
                                    &task->ctx_r8,
                                    &task->ctx_r9,
                                    GetExceptionInformation())) {
        task->wait_status = WAIT_FAILED;
        task->call_result = -1;
        InterlockedExchange(&task->status, -1);
    }
}

static int sns_alloc_std_string_obj(int64_t base, const char *value, int64_t *out) {
    int64_t obj;
    if (!out) return 0;
    *out = 0;
    obj = ((FnCall1)(base + WX_OFF_WX_ALLOC))(32);
    if (!obj) return 0;
    ZeroMemory((void *)obj, 32);
    w64((void *)(obj + 0x18), 15);
    wx_set_std_string_heap(base, obj, value ? value : "");
    *out = obj;
    return 1;
}

static int sns_set_request_string(int64_t base,
                                  BYTE *req,
                                  int field_off,
                                  DWORD flag,
                                  const char *value) {
    int64_t obj;
    if (!base || !req) return 0;
    obj = r64(req + field_off);
    if (!obj || obj == base + WX_OFF_PROTOBUF_EMPTY_STRING) {
        if (!sns_alloc_std_string_obj(base, value, &obj)) return 0;
        w64(req + field_off, obj);
    } else {
        wx_set_std_string_heap(base, obj, value ? value : "");
    }
    w32(req + 404, r32(req + 404) | (int32_t)flag);
    return 1;
}

static int sns_set_object_desc(int64_t base, BYTE *req, const char *xml) {
    int64_t msg;
    int64_t data;
    if (!base || !req || !xml) return 0;
    msg = r64(req + 16);
    if (!msg) {
        msg = ((FnCall1)(base + WX_OFF_WX_ALLOC))(32);
        if (!msg) return 0;
        ((FnCall1)(base + WX_OFF_SKBuiltinBuffer_INIT))(msg);
        w64(req + 16, msg);
    }
    if (!sns_alloc_std_string_obj(base, xml, &data)) return 0;
    w64((void *)(msg + 8), data);
    w32((void *)(msg + 16), lstrlenA(xml));
    w8((void *)(msg + 24), (BYTE)(*(BYTE *)(msg + 24) | 0x03));
    w32(req + 404, r32(req + 404) | 0x02);
    return 1;
}

static void sns_release_shared_ctl(int64_t ctl) {
    int64_t vt;
    volatile LONG *refs;
    if (!ctl) return;
    refs = (volatile LONG *)ctl;
    if (!_InterlockedDecrement(refs + 2)) {
        vt = r64((void *)ctl);
        if (vt) {
            ((SnsSharedCtlFn)r64((void *)vt))(ctl);
            if (!_InterlockedDecrement(refs + 3)) {
                ((SnsSharedCtlFn)r64((void *)(vt + 8)))(ctl);
            }
        }
    }
}

static void sns_cleanup_send_out(int64_t base, BYTE *out) {
    if (!base || !out) return;
    sns_release_shared_ctl(r64(out + 48));
    wx_free_std_string_heap(base, (int64_t)(out + 8));
}

static void sns_append_xml_escaped(char **p, char *end, const char *s) {
    unsigned char c;
    if (!s) return;
    while (*s && *p < end) {
        c = (unsigned char)*s++;
        if (c == '&') append_raw(p, end, "&amp;");
        else if (c == '<') append_raw(p, end, "&lt;");
        else if (c == '>') append_raw(p, end, "&gt;");
        else if (c == '"') append_raw(p, end, "&quot;");
        else if (c == '\'') append_raw(p, end, "&apos;");
        else if (*p < end) *(*p)++ = (char)c;
    }
}

static int sns_capture_exception(DWORD *code,
                                 uint64_t *addr,
                                 uint64_t *fault,
                                 uint64_t *ctx_rdi,
                                 uint64_t *ctx_rcx,
                                 uint64_t *ctx_rdx,
                                 uint64_t *ctx_r8,
                                 uint64_t *ctx_r9,
                                 EXCEPTION_POINTERS *ep) {
    if (code) *code = ep && ep->ExceptionRecord ? ep->ExceptionRecord->ExceptionCode : 0;
    if (addr) *addr = (uint64_t)(ep && ep->ExceptionRecord ? ep->ExceptionRecord->ExceptionAddress : 0);
    if (fault) {
        *fault = 0;
        if (ep && ep->ExceptionRecord && ep->ExceptionRecord->NumberParameters > 1) {
            *fault = (uint64_t)ep->ExceptionRecord->ExceptionInformation[1];
        }
    }
#if defined(_M_X64)
    if (ctx_rdi) *ctx_rdi = ep && ep->ContextRecord ? (uint64_t)ep->ContextRecord->Rdi : 0;
    if (ctx_rcx) *ctx_rcx = ep && ep->ContextRecord ? (uint64_t)ep->ContextRecord->Rcx : 0;
    if (ctx_rdx) *ctx_rdx = ep && ep->ContextRecord ? (uint64_t)ep->ContextRecord->Rdx : 0;
    if (ctx_r8) *ctx_r8 = ep && ep->ContextRecord ? (uint64_t)ep->ContextRecord->R8 : 0;
    if (ctx_r9) *ctx_r9 = ep && ep->ContextRecord ? (uint64_t)ep->ContextRecord->R9 : 0;
#else
    if (ctx_rdi) *ctx_rdi = 0;
    if (ctx_rcx) *ctx_rcx = 0;
    if (ctx_rdx) *ctx_rdx = 0;
    if (ctx_r8) *ctx_r8 = 0;
    if (ctx_r9) *ctx_r9 = 0;
#endif
    return EXCEPTION_EXECUTE_HANDLER;
}

static void sns_get_self_username(int64_t base, char *out, int max) {
    int64_t root;
    int64_t str_obj;
    if (!out || max <= 0) return;
    out[0] = 0;
    if (!base) return;
    __try {
        root = ((SnsNoArgsFn)(base + WX_OFF_FINDER_USERPAGE_CGI_CONTEXT_ROOT))();
        if (root && r64((void *)root)) {
            str_obj = ((FnCall1)r64((void *)(r64((void *)root) + 32)))(root);
            if (str_obj) wx_read_sso_str(str_obj, out, max);
        }
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
        out[0] = 0;
    }
}

static DWORD sns_get_self_uin(int64_t base) {
    int64_t root;
    if (!base) return 0;
    __try {
        root = ((SnsNoArgsFn)(base + WX_OFF_FINDER_USERPAGE_CGI_CONTEXT_ROOT))();
        if (root && r64((void *)root)) {
            return (DWORD)((FnCall1)r64((void *)(r64((void *)root) + 72)))(root);
        }
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
        return 0;
    }
    return 0;
}

static DWORD sns_build_timeline_xml(const char *username,
                                    const char *content,
                                    char *xml,
                                    DWORD cap) {
    char *p = xml;
    char *end = xml + cap - 1;
    if (!xml || cap == 0) return 0;
    append_raw(&p, end, "<TimelineObject>");
    append_raw(&p, end, "<id>0</id><username>");
    sns_append_xml_escaped(&p, end, username ? username : "");
    append_raw(&p, end, "</username><createTime>0</createTime><contentDesc>");
    sns_append_xml_escaped(&p, end, content ? content : "");
    append_raw(&p, end, "</contentDesc>");
    append_raw(&p, end, "<contentDescShowType>0</contentDescShowType>");
    append_raw(&p, end, "<contentDescScene>0</contentDescScene>");
    append_raw(&p, end, "<private>2</private><privacyType>2</privacyType>");
    append_raw(&p, end, "<sourceUserName></sourceUserName>");
    append_raw(&p, end, "<sourceNickName></sourceNickName>");
    append_raw(&p, end, "<publicPhotoAccountUserName></publicPhotoAccountUserName>");
    append_raw(&p, end, "<publicPhotoAccountNickName></publicPhotoAccountNickName>");
    append_raw(&p, end, "<sightFolded>0</sightFolded><showFlag>0</showFlag>");
    append_raw(&p, end, "<ContentObject><contentStyle>0</contentStyle>");
    append_raw(&p, end, "<title></title><description></description>");
    append_raw(&p, end, "<mediaList></mediaList></ContentObject>");
    append_raw(&p, end, "</TimelineObject>");
    *p = 0;
    return (DWORD)(p - xml);
}

static void write_sns_publish_result(const char *seq,
                                     int ok,
                                     const char *message,
                                     const char *content,
                                     const char *image_path,
                                     const char *cdn_url,
                                     const char *thumb_url,
                                     int image_width,
                                     int image_height,
                                     const char *client_id,
                                     const char *username,
                                     int model_flag24,
                                     int model_type48,
                                     int entry_calls,
                                     int64_t first_call_result,
                                     int wait_status,
                                     int64_t call_result,
                                     int phase,
                                     DWORD xml_size,
                                     DWORD seh_code,
                                     uint64_t seh_addr,
                                     uint64_t seh_fault,
                                     uint64_t ctx_rdi,
                                     uint64_t ctx_rcx,
                                     uint64_t ctx_rdx,
                                     uint64_t ctx_r8,
                                     uint64_t ctx_r9,
                                     const SnsPublishTask *task) {
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
    append_raw(&p, end, (image_path && *image_path)
        ? (((task && task->is_video) || (!task && message && strstr(message, "video")))
               ? "publish_sns_video" : "publish_sns_image")
        : "publish_sns_text");
    append_raw(&p, end, "\"");
    append_raw(&p, end, ",\"path\":\"native_sns_publish_upper\"");
    append_raw(&p, end, ",\"endpoint\":\"/cgi-bin/micromsg-bin/mmsnspost\"");
    append_raw(&p, end, ",\"cgiType\":209");
    append_raw(&p, end, ",\"requestProto\":\"micromsg.SnsExtStatData\"");
    append_raw(&p, end, ",\"sendWrapper\":\"sub_183B1B290->sub_183B26140->sub_182BE2100->sub_184CAD900->sub_184CB2CC0\"");
    append_raw(&p, end, ",\"message\":\"");
    append_escaped(&p, end, message ? message : "");
    append_raw(&p, end, "\",\"content\":\"");
    append_escaped(&p, end, content ? content : "");
    append_raw(&p, end, "\",\"imagePath\":\"");
    append_escaped(&p, end, image_path ? image_path : "");
    append_raw(&p, end, "\",\"imageCdnUrl\":\"");
    append_escaped(&p, end, cdn_url ? cdn_url : "");
    append_raw(&p, end, "\",\"imageThumbUrl\":\"");
    append_escaped(&p, end, thumb_url ? thumb_url : "");
    append_raw(&p, end, "\",\"mediaCount\":");
    wsprintfA(num, "%d", sns_media_path_count(image_path));
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"imageWidth\":");
    wsprintfA(num, "%d", image_width);
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"imageHeight\":");
    wsprintfA(num, "%d", image_height);
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"clientId\":\"");
    append_escaped(&p, end, client_id ? client_id : "");
    append_raw(&p, end, "\",\"username\":\"");
    append_escaped(&p, end, username ? username : "");
    append_raw(&p, end, "\",\"modelFlag24\":");
    wsprintfA(num, "%d", model_flag24);
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"modelType48\":");
    wsprintfA(num, "%d", model_type48);
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"entryCalls\":");
    wsprintfA(num, "%d", entry_calls);
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"firstCallResult\":");
    wsprintfA(num, "%I64d", (long long)first_call_result);
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"xmlSize\":");
    wsprintfA(num, "%lu", (unsigned long)xml_size);
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"waitStatus\":");
    wsprintfA(num, "%d", wait_status);
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"callResult\":");
    wsprintfA(num, "%I64d", (long long)call_result);
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"phase\":");
    wsprintfA(num, "%d", phase);
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"phase1Seen\":");
    append_raw(&p, end, task && task->phase1_seen ? "true" : "false");
    append_raw(&p, end, ",\"phase2Seen\":");
    append_raw(&p, end, task && task->phase2_seen ? "true" : "false");
    append_raw(&p, end, ",\"phase3Seen\":");
    append_raw(&p, end, task && task->phase3_seen ? "true" : "false");
    append_raw(&p, end, ",\"videoWorkerScheduled\":");
    append_raw(&p, end, task && task->video_worker_scheduled ? "true" : "false");
    append_raw(&p, end, ",\"videoWorkerCompleted\":");
    append_raw(&p, end, task && task->video_worker_completed ? "true" : "false");
    append_raw(&p, end, ",\"videoWorkerContext\":\"0x");
    wsprintfA(num, "%I64x", (unsigned __int64)(task ? task->video_worker_context : 0));
    append_raw(&p, end, num);
    append_raw(&p, end, "\"");
    append_raw(&p, end, ",\"postBuildSendSeen\":");
    append_raw(&p, end, task && task->post_build_send_seen ? "true" : "false");
    append_raw(&p, end, ",\"cgi209Seen\":");
    append_raw(&p, end, task && task->cgi209_seen ? "true" : "false");
    append_raw(&p, end, ",\"cgiSyncError\":");
    append_raw(&p, end, task && task->cgi_sync_error ? "true" : "false");
    append_raw(&p, end, ",\"officialResultSeen\":");
    append_raw(&p, end, task && task->official_result_seen ? "true" : "false");
    append_raw(&p, end, ",\"officialResultCode\":");
    wsprintfA(num, "%ld", task ? (long)task->official_result_code : 0L);
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"responseSeen\":");
    append_raw(&p, end, task && task->response_seen ? "true" : "false");
    append_raw(&p, end, ",\"postPathsSet\":");
    append_raw(&p, end, task && task->post_paths_set ? "true" : "false");
    append_raw(&p, end, ",\"postVideoAttr\":");
    wsprintfA(num, "%lu", (unsigned long)(task ? task->post_video_attr : INVALID_FILE_ATTRIBUTES));
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"postThumbAttr\":");
    wsprintfA(num, "%lu", (unsigned long)(task ? task->post_thumb_attr : INVALID_FILE_ATTRIBUTES));
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"postOutputAttr\":");
    wsprintfA(num, "%lu", (unsigned long)(task ? task->post_output_attr : INVALID_FILE_ATTRIBUTES));
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"videoOutputPath\":\"");
    append_escaped(&p, end, task ? task->video_output_path : "");
    append_raw(&p, end, "\"");
    append_raw(&p, end, ",\"postState456\":");
    wsprintfA(num, "%d", task ? task->post_state456 : 0);
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"postError460\":");
    wsprintfA(num, "%d", task ? task->post_error460 : 0);
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"phase1ResultCode\":");
    wsprintfA(num, "%d", task ? task->phase1_result_code : 0);
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"phase2ResultCode\":");
    wsprintfA(num, "%d", task ? task->phase2_result_code : 0);
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"phase3ResultCode\":");
    wsprintfA(num, "%d", task ? task->phase3_result_code : 0);
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"postState464\":");
    wsprintfA(num, "%d", task ? task->post_state464 : 0);
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"cgiOutError\":\"0x");
    wsprintfA(num, "%I64x", (unsigned __int64)(task ? task->cgi_out_error : 0));
    append_raw(&p, end, num);
    append_raw(&p, end, "\",\"buildResponse\":\"0x");
    wsprintfA(num, "%I64x", (unsigned __int64)(task ? task->build_out_response : 0));
    append_raw(&p, end, num);
    append_raw(&p, end, "\",\"modelResponse\":\"0x");
    wsprintfA(num, "%I64x", (unsigned __int64)(task ? task->model_response : 0));
    append_raw(&p, end, num);
    append_raw(&p, end, "\"");
    append_raw(&p, end, ",\"sehCode\":");
    wsprintfA(num, "%lu", (unsigned long)seh_code);
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"sehAddr\":\"0x");
    wsprintfA(num, "%I64x", (unsigned __int64)seh_addr);
    append_raw(&p, end, num);
    append_raw(&p, end, "\",\"sehFault\":\"0x");
    wsprintfA(num, "%I64x", (unsigned __int64)seh_fault);
    append_raw(&p, end, num);
    append_raw(&p, end, "\",\"ctxRdi\":\"0x");
    wsprintfA(num, "%I64x", (unsigned __int64)ctx_rdi);
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
    append_raw(&p, end, "\",\"imageTypePatchAttempts\":");
    wsprintfA(num, "%ld", (long)InterlockedCompareExchange(&g_sns_image_type_patch_attempts, 0, 0));
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"imageTypePatchHits\":");
    wsprintfA(num, "%ld", (long)InterlockedCompareExchange(&g_sns_image_type_patch_hits, 0, 0));
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"imageTypePatchLastType\":");
    wsprintfA(num, "%ld", (long)InterlockedCompareExchange(&g_sns_image_type_patch_last_type, 0, 0));
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"imageTypePatchLastTimeline\":\"0x");
    wsprintfA(num, "%I64x", (unsigned __int64)InterlockedCompareExchange64(&g_sns_image_type_patch_last_timeline, 0, 0));
    append_raw(&p, end, num);
    append_raw(&p, end, "\"}");
    *p = 0;
    write_file_bytes(path, json, (DWORD)(p - json));
}

static int publish_sns_to_result(const char *seq,
                                 const char *content,
                                 const char *image_path,
                                 const char *md5,
                                 const char *cdn_url,
                                 const char *thumb_url,
                                 int is_video,
                                 int width,
                                 int height,
                                 int duration_ms,
                                 int wait_ms) {
    const SIZE_T arena_size = 0x12000;
    HMODULE mod;
    int64_t base;
    BYTE *arena;
    BYTE *asm_addr;
    SnsPublishTask *task;
    int asm_len;
    LONG status;
    int loops;
    char username[256];
    char client_id[128];
    char video_stage_id[192];
    char staged_video[2048];
    char staged_thumb[2048];
    DWORD uin;
    DWORD now;
    char xml[SNS_POST_XML_MAX];
    DWORD xml_size = 0;
    int64_t call_result = 0;
    DWORD wait_status = WAIT_FAILED;
    DWORD seh_code = 0;
    uint64_t seh_addr = 0;
    uint64_t seh_fault = 0;
    uint64_t ctx_rdi = 0;
    uint64_t ctx_rcx = 0;
    uint64_t ctx_rdx = 0;
    uint64_t ctx_r8 = 0;
    uint64_t ctx_r9 = 0;
    int phase = 0;
    int ok = 0;
    int type_patch_installed = 0;
    const char *model_image_path = image_path;
    const char *model_thumb_url = thumb_url;
    if (!seq || !*seq) return 0;
    if (!content) content = "";
    if (!*content && !(is_video && image_path && *image_path)) {
        write_sns_publish_result(seq, 0, "missing_content", content, image_path, cdn_url, thumb_url, width, height,
                                 "", "", 0, 0, 0, 0, WAIT_FAILED, 0, phase, 0, 0, 0, 0, 0, 0, 0, 0, 0, NULL);
        return 0;
    }

    mod = wxb_ensure_weixin_module();
    if (!mod) {
        write_sns_publish_result(seq, 0, "weixin_module_not_found", content, image_path, cdn_url, thumb_url, width, height,
                                 "", "", 0, 0, 0, 0, WAIT_FAILED, 0, phase, 0, 0, 0, 0, 0, 0, 0, 0, 0, NULL);
        return 0;
    }
    base = (int64_t)mod;

    username[0] = 0;
    sns_get_self_username(base, username, sizeof(username));
    uin = sns_get_self_uin(base);
    now = (DWORD)time(NULL);
    wsprintfA(client_id, "publish_%lu_%lu",
              (unsigned long)(uin ? uin : GetCurrentProcessId()),
              (unsigned long)now);
    staged_video[0] = 0;
    staged_thumb[0] = 0;
    video_stage_id[0] = 0;
    if (is_video) {
        int video_ok = 0;
        int thumb_ok = (!thumb_url || !*thumb_url) ? 1 : 0;
        const char *failure = NULL;
        int64_t failure_code = 0;

        wsprintfA(video_stage_id, "%s_%lu_%lu",
                  client_id,
                  (unsigned long)GetCurrentProcessId(),
                  (unsigned long)GetTickCount());
        if (!image_path || !*image_path) {
            failure = "missing_video_path";
            failure_code = -22;
        } else if (strstr(image_path, "://")) {
            failure = "remote_video_url_not_supported";
            failure_code = -23;
        } else {
            video_ok = sns_stage_video_under_xwechat(image_path,
                                                      video_stage_id,
                                                      staged_video,
                                                      (int)sizeof(staged_video));
            if (!video_ok) {
                failure = "video_file_not_found_or_stage_failed";
                failure_code = -24;
            }
        }

        if (!failure && thumb_url && *thumb_url) {
            if (strstr(thumb_url, "://")) {
                failure = "remote_video_thumb_url_not_supported";
                failure_code = -25;
            } else {
                thumb_ok = sns_stage_video_under_xwechat(thumb_url,
                                                          video_stage_id,
                                                          staged_thumb,
                                                          (int)sizeof(staged_thumb));
                if (!thumb_ok) {
                    failure = "video_thumb_not_found_or_stage_failed";
                    failure_code = -26;
                }
            }
        }

        sns_trace_video_stage_paths(client_id,
                                    image_path,
                                    staged_video,
                                    thumb_url,
                                    staged_thumb,
                                    video_ok,
                                    thumb_ok);
        if (failure) {
            write_sns_publish_result(seq, 0, failure,
                                     content, image_path, cdn_url, thumb_url,
                                     width, height, client_id, username,
                                     0, 0, 0, 0, WAIT_FAILED, failure_code,
                                     phase, 0, 0, 0, 0, 0, 0, 0, 0, 0, NULL);
            return 0;
        }
        model_image_path = staged_video;
        model_thumb_url = (thumb_url && *thumb_url) ? staged_thumb : "";
    }
    xml_size = sns_build_timeline_xml(username, content, xml, sizeof(xml));
    if (!xml_size || xml_size >= sizeof(xml) - 1) {
        write_sns_publish_result(seq, 0, "timeline_xml_build_failed", content, image_path, cdn_url, thumb_url, width, height,
                                 client_id, username,
                                 0, 0, 0, 0, WAIT_FAILED, 0, phase, xml_size, 0, 0, 0, 0, 0, 0, 0, 0, NULL);
        return 0;
    }
    if (GetFileAttributesW(SNS_PUBLISH_UPPER_ENABLE_FILE) == INVALID_FILE_ATTRIBUTES) {
        write_sns_publish_result(seq, 0, "sns_publish_upper_disabled_after_crash_incomplete_model",
                                 content, image_path, cdn_url, thumb_url, width, height, client_id, username,
                                 0, 0, 0, 0, WAIT_FAILED, -20, phase, xml_size, 0, 0, 0, 0, 0, 0, 0, 0, NULL);
        return 0;
    }
    if (!is_video && image_path && *image_path && cdn_url && *cdn_url) {
        type_patch_installed = sns_install_post_build_send_patch(content);
        if (!type_patch_installed) {
            write_sns_publish_result(seq, 0, "sns_image_content_type_patch_failed",
                                     content, image_path, cdn_url, thumb_url, width, height,
                                     client_id, username,
                                     0, 0, 0, 0, WAIT_FAILED, -21, phase, xml_size, 0, 0, 0, 0, 0, 0, 0, 0, NULL);
            return 0;
        }
    }
    arena = (BYTE *)VirtualAlloc(NULL, arena_size, MEM_COMMIT | MEM_RESERVE, PAGE_EXECUTE_READWRITE);
    if (!arena) {
        if (type_patch_installed) sns_uninstall_post_build_send_patch();
        write_sns_publish_result(seq, 0, "arena_alloc_failed", content, image_path, cdn_url, thumb_url, width, height,
                                 client_id, username,
                                 0, 0, 0, 0, WAIT_FAILED, -13, phase, xml_size, 0, 0, 0, 0, 0, 0, 0, 0, NULL);
        return 0;
    }
    ZeroMemory(arena, arena_size);
    asm_addr = arena + 0x200;
    task = (SnsPublishTask *)(arena + 0x1000);
    task->status = 0;
    task->wait_status = WAIT_FAILED;
    task->xml_size = xml_size;
    task->final_asm_addr = (int64_t)(arena + 0x500);
    task->has_image = (model_image_path && *model_image_path) ? 1 : 0;
    task->is_video = is_video ? 1 : 0;
    task->video_upload_wait_ms = wait_ms > 0 ? wait_ms : (is_video ? 60000 : 20000);
    task->image_width = width;
    task->image_height = height;
    task->image_duration_ms = duration_ms;
    safe_copy(task->content, sizeof(task->content), content);
    safe_copy(task->image_path, sizeof(task->image_path), model_image_path ? model_image_path : "");
    safe_copy(task->image_md5, sizeof(task->image_md5), md5 ? md5 : "");
    safe_copy(task->image_cdn_url, sizeof(task->image_cdn_url), cdn_url ? cdn_url : "");
    safe_copy(task->image_thumb_url, sizeof(task->image_thumb_url), model_thumb_url ? model_thumb_url : "");
    safe_copy(task->client_id, sizeof(task->client_id), client_id);
    safe_copy(task->username, sizeof(task->username), username);
    if (is_video) {
        sns_record_video_thumb_map(client_id, model_thumb_url);
        if (!sns_build_video_output_path(task->image_path,
                                         task->video_output_path,
                                         (int)sizeof(task->video_output_path))) {
            task->call_result = -27;
            task->wait_status = WAIT_FAILED;
            task->phase = 0;
            InterlockedExchange(&task->status, -27);
        } else if (!sns_install_publish_completion_hooks()) {
            task->call_result = -17;
            task->wait_status = WAIT_FAILED;
            task->phase = 0;
            InterlockedExchange(&task->status, -17);
        } else if (!sns_publish_claim_task(task)) {
            task->call_result = -18;
            task->wait_status = WAIT_FAILED;
            task->phase = 0;
            InterlockedExchange(&task->status, -18);
        } else {
            InterlockedExchange(&task->accept_results, 1);
        }
    }

    if (InterlockedCompareExchange(&task->status, 0, 0) == 0) {
        asm_len = build_sns_publish_queue_asm(asm_addr, (int64_t)task, (int64_t)sns_publish_upper_worker);
        FlushInstructionCache(GetCurrentProcess(), asm_addr, asm_len);
        queue_send((int64_t)asm_addr);
    }

    if (wait_ms <= 0) wait_ms = is_video ? 60000 : 20000;
    loops = wait_ms / 10;
    if (loops < 1) loops = 1;
    for (int i = 0; i < loops; i++) {
        status = InterlockedCompareExchange(&task->status, 0, 0);
        if (status == 1 || status < 0) break;
        Sleep(10);
    }
    status = InterlockedCompareExchange(&task->status, 0, 0);
    if (status == 0 || status == 2 || status == 3 || status == 4) {
        InterlockedCompareExchange(&task->status, -2, status);
        status = InterlockedCompareExchange(&task->status, 0, 0);
    }
    InterlockedExchange(&task->accept_results, 0);
    if (is_video) sns_publish_release_task(task);

    ok = (status == 1);
    call_result = task->call_result;
    wait_status = (status == -2) ? WAIT_TIMEOUT : task->wait_status;
    phase = task->phase;
    seh_code = task->seh_code;
    seh_addr = task->seh_addr;
    seh_fault = task->seh_fault;
    ctx_rdi = task->ctx_rdi;
    ctx_rcx = task->ctx_rcx;
    ctx_rdx = task->ctx_rdx;
    ctx_r8 = task->ctx_r8;
    ctx_r9 = task->ctx_r9;
    if (type_patch_installed && ok) {
        int patch_loops = wait_ms / 10;
        if (patch_loops < 1) patch_loops = 1;
        for (int i = 0; i < patch_loops; i++) {
            if (InterlockedCompareExchange(&g_sns_image_type_patch_attempts, 0, 0) > 0 ||
                InterlockedCompareExchange(&g_sns_image_type_patch_hits, 0, 0) > 0) {
                Sleep(200);
                break;
            }
            Sleep(10);
        }
    }
    if (type_patch_installed) sns_uninstall_post_build_send_patch();

    write_sns_publish_result(
        seq,
        ok,
        ok ? (task->has_image ? (task->is_video ? (!task->image_cdn_url[0] ?
                                                       "publish_sns_video server confirmed" :
                                                       "publish_sns_video server confirmed") :
                                                  "publish_sns_image upper entry submitted") :
                                "publish_sns_text upper entry submitted") :
             (status == -2 ? "publish_sns server confirmation timeout" :
              status == -1 ? "publish_sns upper entry exception" :
              status == -6 ? "publish_sns upper entry returned false" :
              status == -15 ? "sns_publish_register_queue_not_found" :
              status == -16 ? "sns_post_server_confirmation_failed" :
              status == -17 ? "sns_publish_completion_hook_failed" :
              status == -18 ? "sns_publish_video_busy" :
              status == -27 ? "sns_video_output_path_failed" :
              status == -28 ? "sns_video_worker_context_not_found" :
              status == -29 ? "sns_video_worker_schedule_failed" :
              (call_result == -4 ? "sns_publish_manager_not_found" :
               call_result == -5 ? "timeline_model_alloc_failed" :
               call_result == -7 ? "sns_post_object_build_failed" :
               "publish_sns failed")),
        content,
        task->image_path,
        task->image_cdn_url,
        task->image_thumb_url,
        task->image_width,
        task->image_height,
        client_id,
        username,
        task->model_flag24,
        task->model_type48,
        task->entry_calls,
        task->first_call_result,
        (int)wait_status,
        call_result,
        phase,
        xml_size,
        seh_code,
        seh_addr,
        seh_fault,
        ctx_rdi,
        ctx_rcx,
        ctx_rdx,
        ctx_r8,
        ctx_r9,
        task);
    retain_arena(arena);
    return ok;
}

int publish_sns_text_to_result(const char *seq, const char *content, int wait_ms) {
    return publish_sns_to_result(seq, content, "", "", "", "", 0, 0, 0, 0, wait_ms);
}

int publish_sns_image_to_result(const char *seq, const char *content, const char *image_path,
                                const char *md5, int width, int height, int wait_ms,
                                const char *cdn_url, const char *thumb_url) {
    return publish_sns_to_result(seq, content, image_path, md5, cdn_url, thumb_url, 0,
                                 width, height, 0, wait_ms);
}

int publish_sns_video_to_result(const char *seq, const char *content, const char *video_url,
                                const char *md5, int width, int height, int wait_ms,
                                const char *thumb_url, int duration_ms) {
    return publish_sns_to_result(seq, content, video_url, md5, "", thumb_url, 1,
                                 width, height, duration_ms, wait_ms);
}
