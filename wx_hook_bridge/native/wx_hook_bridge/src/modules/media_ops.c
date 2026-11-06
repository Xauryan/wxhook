#include "../include/wx_bridge_media_ops.h"
#include "../include/wx_bridge_forward_retained.h"
#include "../include/wx_bridge_memory.h"
#include "../include/wx_bridge_offsets.h"
#include "../include/wx_bridge_protocol.h"
#include "../include/wx_bridge_send_runtime.h"
#include "../include/wx_bridge_runtime.h"
#include "../include/wx_bridge_native_call.h"
#include "../include/wx_bridge_text.h"
#include "../include/wx_bridge_wechat_string.h"
#include "../include/wx_bridge_file_io.h"
#include "../include/wx_bridge_paths.h"

typedef int64_t (__fastcall *FnImageDownloadHelper)(int64_t manager,
                                                    int64_t out_pair,
                                                    int64_t obj,
                                                    unsigned int type,
                                                    unsigned int flags,
                                                    int *opts);
typedef int64_t (__fastcall *FnImageDownloadAf500)(int64_t manager,
                                                   int64_t out_status,
                                                   int64_t key_sso,
                                                   unsigned int type,
                                                   int64_t path_sso,
                                                   int64_t request);
typedef int64_t (__fastcall *FnImageDownloadAc170)(int64_t manager,
                                                   int64_t obj,
                                                   unsigned int type);
typedef int64_t (__fastcall *FnImageDownloadAc720)(int64_t manager,
                                                   int64_t out_pair,
                                                   int64_t key_sso,
                                                   int64_t path_sso,
                                                   int marker);
typedef int64_t (__fastcall *FnImageDownloadQueueSubmit)(int64_t queue_mgr,
                                                         int64_t flags,
                                                         int64_t task);
typedef int64_t (__fastcall *FnImageDownloadB9330)(int64_t lookup_req,
                                                   int64_t out_pair);
typedef int64_t (__fastcall *FnImageDownloadB9700)(int64_t manager,
                                                   int64_t pair,
                                                   int64_t path_sso);
typedef int64_t (__fastcall *FnImageDownloadB9BF0)(int64_t manager,
                                                   int64_t out_pair,
                                                   int64_t pair,
                                                   int64_t path_sso);
typedef int64_t (__fastcall *FnImageDownloadD2ED60)(int64_t queue_mgr,
                                                    int64_t key_ptr,
                                                    int64_t path_sso,
                                                    int limit);
typedef int64_t (__fastcall *FnImageDownloadD2A250)(int64_t queue_mgr,
                                                    int64_t key_ptr,
                                                    int64_t pair);
typedef int64_t (__fastcall *FnImageDownloadD32A20)(int64_t queue_mgr,
                                                    int64_t out_pair,
                                                    int64_t key_ptr,
                                                    int64_t path_sso,
                                                    char remove_existing,
                                                    char log_detail);
typedef void (__fastcall *FnSharedCtlVFunc)(int64_t ctl);
typedef int64_t (__fastcall *FnNoArgs)(void);
typedef void (__fastcall *FnObjectOutPair)(int64_t obj, int64_t out_pair);
typedef void *(__fastcall *FnSnsDownloadImageUrl)(void *out_path, void *url, void *suffix);
typedef int64_t (__fastcall *FnSnsCdnWriteDecrypt)(int64_t cdn_file, int64_t buf,
                                                   uint64_t file_off, uint32_t len,
                                                   uint8_t final_block,
                                                   uint32_t *written_out,
                                                   uint32_t *err_out);
typedef int64_t (__fastcall *FnSnsCdnWriteRaw)(int64_t cdn_file, int64_t buf,
                                               int64_t file_off, int len,
                                               uint32_t *err_out);
typedef int64_t (__fastcall *FnImageTaskStageExec)(int64_t stage_obj, int64_t context_obj);
typedef int64_t (__fastcall *FnImageTaskBody)(int64_t task_obj);
typedef int64_t (__fastcall *FnSnsMediaItemJson)(unsigned int *media_item, int64_t json_node);
typedef int64_t (__fastcall *FnCdnTaskFactoryFull)(int64_t out_pair,
                                                   int64_t root_context,
                                                   int64_t manager_pair,
                                                   int64_t cdn_params,
                                                   int task_type);
typedef int64_t (__fastcall *FnCdnUploadLogicSubmit)(int64_t upload_param);
typedef int64_t (__fastcall *FnCdnUploadCore2)(int64_t manager, int64_t upload_param);
typedef HRESULT (WINAPI *MediaSHGetFolderPathWFn)(HWND hwnd, int csidl, HANDLE token,
                                                  DWORD flags, LPWSTR path);

typedef struct MediaInlineHook {
    volatile LONG installed;
    BYTE orig[64];
    void *trampoline;
    int patch_len;
} MediaInlineHook;

static MediaInlineHook g_sns_cdn_write_decrypt_hook = {0};
static MediaInlineHook g_sns_cdn_write_raw_hook = {0};
static MediaInlineHook g_image_stage_exec_hook = {0};
static MediaInlineHook g_image_task_body_hook = {0};
static MediaInlineHook g_image_download_helper_hook = {0};
static MediaInlineHook g_image_download_af500_hook = {0};
static MediaInlineHook g_image_download_ac170_hook = {0};
static MediaInlineHook g_image_download_ac720_hook = {0};
static MediaInlineHook g_image_download_queue_submit_hook = {0};
static MediaInlineHook g_image_download_b9330_hook = {0};
static MediaInlineHook g_image_download_b9700_hook = {0};
static MediaInlineHook g_image_download_b9bf0_hook = {0};
static MediaInlineHook g_image_download_d2ed60_hook = {0};
static MediaInlineHook g_image_download_d2a250_hook = {0};
static MediaInlineHook g_image_download_d32a20_hook = {0};
static MediaInlineHook g_sns_media_item_json_hook = {0};
static MediaInlineHook g_sns_upload_logic_submit_hook = {0};
static MediaInlineHook g_sns_upload_core2_hook = {0};
static volatile LONG g_sns_media_item_json_reentry = 0;
static volatile LONG g_sns_upload_trace_reentry = 0;

#define MEDIA_STAGE_WPATH_MAX 1024
#ifndef CSIDL_APPDATA
#define CSIDL_APPDATA 0x001a
#endif

#define WX_OFF_IMAGE_DOWNLOAD_AF500       0x16AF500
#define WX_OFF_IMAGE_DOWNLOAD_AC170       0x16AC170
#define WX_OFF_IMAGE_DOWNLOAD_AC720       0x16AC720
#define WX_OFF_IMAGE_DOWNLOAD_QUEUE_SUBMIT 0x0D32990
#define WX_OFF_IMAGE_DOWNLOAD_B9330       0x16B9330
#define WX_OFF_IMAGE_DOWNLOAD_B9700       0x16B9700
#define WX_OFF_IMAGE_DOWNLOAD_B9BF0       0x16B9BF0
#define WX_OFF_IMAGE_DOWNLOAD_D2ED60      0x0D2ED60
#define WX_OFF_IMAGE_DOWNLOAD_D2A250      0x0D2A250
#define WX_OFF_IMAGE_DOWNLOAD_D32A20      0x0D32A20
static const BYTE WX_ORIG_SNS_CDN_WRITE_DECRYPT_BYTES[] = {
    0x55, 0x41, 0x57, 0x41, 0x56, 0x41, 0x55, 0x41, 0x54, 0x56, 0x57, 0x53,
    0x48, 0x81, 0xEC, 0xD8, 0x02, 0x00, 0x00
};
static const BYTE WX_ORIG_SNS_CDN_WRITE_RAW_BYTES[] = {
    0x55, 0x41, 0x57, 0x41, 0x56, 0x56, 0x57, 0x53,
    0x48, 0x81, 0xEC, 0xC8, 0x02, 0x00, 0x00
};
static const BYTE WX_ORIG_IMAGE_STAGE_EXEC_BYTES[] = {
    0x55, 0x41, 0x57, 0x41, 0x56, 0x41, 0x54, 0x56, 0x57, 0x53,
    0x48, 0x81, 0xEC, 0x30, 0x06, 0x00, 0x00
};
static const BYTE WX_ORIG_IMAGE_TASK_BODY_BYTES[] = {
    0x55, 0x56, 0x57, 0x48, 0x81, 0xEC, 0xE0, 0x01, 0x00, 0x00,
    0x48, 0x8D, 0xAC, 0x24, 0x80, 0x00, 0x00, 0x00
};
static const BYTE WX_ORIG_IMAGE_DOWNLOAD_HELPER_BYTES[] = {
    0x55, 0x41, 0x57, 0x41, 0x56, 0x56, 0x57, 0x53,
    0x48, 0x83, 0xEC, 0x58
};
static const BYTE WX_ORIG_IMAGE_DOWNLOAD_AF500_BYTES[] = {
    0x55, 0x41, 0x57, 0x41, 0x56, 0x41, 0x54, 0x56, 0x57, 0x53,
    0x48, 0x81, 0xEC, 0x30, 0x02, 0x00, 0x00
};
static const BYTE WX_ORIG_IMAGE_DOWNLOAD_AC170_BYTES[] = {
    0x55, 0x41, 0x57, 0x41, 0x56, 0x56, 0x57, 0x53,
    0x48, 0x81, 0xEC, 0x78, 0x02, 0x00, 0x00
};
static const BYTE WX_ORIG_IMAGE_DOWNLOAD_AC720_BYTES[] = {
    0x55, 0x41, 0x57, 0x41, 0x56, 0x41, 0x55, 0x41, 0x54, 0x56, 0x57, 0x53,
    0x48, 0x83, 0xEC, 0x78
};
static const BYTE WX_ORIG_IMAGE_DOWNLOAD_QUEUE_SUBMIT_BYTES[] = {
    0x56, 0x57, 0x48, 0x83, 0xEC, 0x48, 0x4D, 0x89, 0xC1, 0x49, 0x89, 0xD0
};
static const BYTE WX_ORIG_IMAGE_DOWNLOAD_B9330_BYTES[] = {
    0x55, 0x41, 0x57, 0x41, 0x56, 0x56, 0x57, 0x53,
    0x48, 0x81, 0xEC, 0xE8, 0x00, 0x00, 0x00,
    0x48, 0x8D, 0xAC, 0x24, 0x80, 0x00, 0x00, 0x00
};
static const BYTE WX_ORIG_IMAGE_DOWNLOAD_B9700_BYTES[] = {
    0x55, 0x56, 0x57, 0x48, 0x81, 0xEC, 0x60, 0x02, 0x00, 0x00,
    0x48, 0x8D, 0xAC, 0x24, 0x80, 0x00, 0x00, 0x00
};
static const BYTE WX_ORIG_IMAGE_DOWNLOAD_B9BF0_BYTES[] = {
    0x55, 0x41, 0x57, 0x41, 0x56, 0x56, 0x57, 0x53,
    0x48, 0x81, 0xEC, 0x98, 0x02, 0x00, 0x00,
    0x48, 0x8D, 0xAC, 0x24, 0x80, 0x00, 0x00, 0x00
};
static const BYTE WX_ORIG_IMAGE_DOWNLOAD_D2ED60_BYTES[] = {
    0x55, 0x41, 0x56, 0x56, 0x57, 0x53,
    0x48, 0x81, 0xEC, 0x50, 0x02, 0x00, 0x00,
    0x48, 0x8D, 0xAC, 0x24, 0x80, 0x00, 0x00, 0x00
};
static const BYTE WX_ORIG_IMAGE_DOWNLOAD_D2A250_BYTES[] = {
    0x55, 0x41, 0x57, 0x41, 0x56, 0x41, 0x54, 0x56, 0x57, 0x53,
    0x48, 0x81, 0xEC, 0x80, 0x01, 0x00, 0x00,
    0x48, 0x8D, 0xAC, 0x24, 0x80, 0x00, 0x00, 0x00
};
static const BYTE WX_ORIG_IMAGE_DOWNLOAD_D32A20_BYTES[] = {
    0x41, 0x57, 0x41, 0x56, 0x41, 0x54, 0x56, 0x57, 0x55, 0x53,
    0x48, 0x83, 0xEC, 0x50
};
static const BYTE WX_ORIG_SNS_MEDIA_ITEM_JSON_BYTES[] = {
    0x55, 0x41, 0x57, 0x41, 0x56, 0x41, 0x55, 0x41, 0x54, 0x56, 0x57, 0x53,
    0x48, 0x81, 0xEC, 0x08, 0x02, 0x00, 0x00,
    0x48, 0x8D, 0xAC, 0x24, 0x80, 0x00, 0x00, 0x00
};
static const BYTE WX_ORIG_CDN_UPLOAD_LOGIC_SUBMIT_BYTES[] = {
    0x55, 0x56, 0x53, 0x48, 0x81, 0xEC, 0x80, 0x01, 0x00, 0x00,
    0x48, 0x8D, 0xAC, 0x24, 0x80, 0x00, 0x00, 0x00,
    0x48, 0xC7, 0x85, 0xF8, 0x00, 0x00, 0x00, 0xFE, 0xFF, 0xFF, 0xFF
};
static const BYTE WX_ORIG_CDN_UPLOAD_CORE2_BYTES[] = {
    0x55, 0x41, 0x57, 0x41, 0x56, 0x56, 0x57, 0x53,
    0x48, 0x81, 0xEC, 0xF8, 0x02, 0x00, 0x00,
    0x48, 0x8D, 0xAC, 0x24, 0x80, 0x00, 0x00, 0x00,
    0x48, 0xC7, 0x85, 0x70, 0x02, 0x00, 0x00, 0xFE, 0xFF, 0xFF, 0xFF
};

typedef struct MediaDownloadProbeTask {
    volatile LONG status; /* 0=queued, 2=running, 1=ok, negative=failed */
    int ok;
    int dl_type;
    int mode;
    int flags;
    int use_send_item;
    int wait_ms;
    char seq[64];
    char msgid[64];
} MediaDownloadProbeTask;

typedef struct MediaDownloadTaskProbeTask {
    volatile LONG status; /* 0=queued, 2=running, 1=ok, negative=failed */
    int ok;
    int wait_ms;
    int source_type;
    int source_subtype;
    int source_local_id;
    int32_t source_time;
    char seq[64];
    char msgid[64];
    char source_from[256];
    char source_to[256];
    char source_sender[256];
    char source_msgsource[1024];
    char content[8192];
} MediaDownloadTaskProbeTask;

typedef struct SnsMediaUrlDownloadTask {
    volatile LONG status; /* 0=queued, 2=running, 1=ok, negative=failed */
    int ok;
    int wait_ms;
    char seq[64];
    char url[8192];
    char suffix[512];
} SnsMediaUrlDownloadTask;

typedef struct SnsMediaTaskDownloadTask {
    volatile LONG status; /* 0=queued, 2=running, 1=ok, negative=failed */
    int ok;
    int wait_ms;
    int64_t total_size;
    char seq[64];
    char url[8192];
    char filekey[512];
    char md5[64];
    char savepath[1024];
} SnsMediaTaskDownloadTask;

typedef struct SnsMediaFullTaskDownloadTask {
    volatile LONG status; /* 0=queued, 2=running, 1=ok, negative=failed */
    int ok;
    int wait_ms;
    int task_type;
    int64_t total_size;
    char seq[64];
    char url[8192];
    char filekey[512];
    char md5[64];
    char aeskey[256];
    char savepath[1024];
} SnsMediaFullTaskDownloadTask;

static int media_probe_exception_filter(EXCEPTION_POINTERS *ep, DWORD *code, int64_t *addr) {
    if (code) *code = ep && ep->ExceptionRecord ? ep->ExceptionRecord->ExceptionCode : 0;
    if (addr) *addr = ep && ep->ExceptionRecord ? (int64_t)ep->ExceptionRecord->ExceptionAddress : 0;
    return EXCEPTION_EXECUTE_HANDLER;
}

static void media_append_hex_field(char **p, char *end, const char *name, int64_t value) {
    char num[64];
    append_raw(p, end, ",\"");
    append_raw(p, end, name);
    append_raw(p, end, "\":\"");
    wsprintfA(num, "%I64x", (unsigned __int64)value);
    append_raw(p, end, num);
    append_raw(p, end, "\"");
}

static void media_append_i64_field(char **p, char *end, const char *name, int64_t value) {
    char num[64];
    append_raw(p, end, ",\"");
    append_raw(p, end, name);
    append_raw(p, end, "\":");
    wsprintfA(num, "%I64d", (long long)value);
    append_raw(p, end, num);
}

static void media_append_int_field(char **p, char *end, const char *name, int value) {
    char num[32];
    append_raw(p, end, ",\"");
    append_raw(p, end, name);
    append_raw(p, end, "\":");
    wsprintfA(num, "%d", value);
    append_raw(p, end, num);
}

static void media_append_bool_field(char **p, char *end, const char *name, int value) {
    append_raw(p, end, ",\"");
    append_raw(p, end, name);
    append_raw(p, end, "\":");
    append_raw(p, end, value ? "true" : "false");
}

static int media_utf8_to_wide_path(const char *path, WCHAR *out, int max_chars) {
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

static void media_set_path_string_acp(int64_t header, const char *utf8,
                                      BYTE **pool, BYTE *pool_end) {
    WCHAR wide[1024];
    char acp[2048];
    int n;
    if (!utf8) utf8 = "";
    if (!media_utf8_to_wide_path(utf8, wide, 1024)) {
        wx_set_std_string_arena(header, utf8, -1, pool, pool_end);
        return;
    }
    n = WideCharToMultiByte(CP_ACP, 0, wide, -1, acp, sizeof(acp), NULL, NULL);
    if (n <= 0) {
        wx_set_std_string_arena(header, utf8, -1, pool, pool_end);
        return;
    }
    wx_set_std_string_arena(header, acp, n - 1, pool, pool_end);
}

static int media_append_wide(WCHAR *dst, int max_chars, const WCHAR *suffix) {
    int dst_len;
    int suffix_len;
    if (!dst || !suffix || max_chars <= 0) return 0;
    dst_len = lstrlenW(dst);
    suffix_len = lstrlenW(suffix);
    if (dst_len < 0 || suffix_len < 0 || dst_len + suffix_len >= max_chars) return 0;
    copy_bytes(dst + dst_len, suffix, (DWORD)((suffix_len + 1) * sizeof(WCHAR)));
    return 1;
}

static void media_ascii_safe_segment_w(const char *src, WCHAR *out, int max_chars) {
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

static const WCHAR *media_wide_basename(const WCHAR *path) {
    const WCHAR *base = path;
    const WCHAR *p;
    if (!path) return L"";
    for (p = path; *p; p++) {
        if (*p == L'\\' || *p == L'/') base = p + 1;
    }
    return base;
}

static int media_ensure_dir_w(const WCHAR *path) {
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

static int media_get_appdata_w(WCHAR *out, int max_chars) {
    HMODULE shell32;
    MediaSHGetFolderPathWFn sh_get_folder_path;
    DWORD env_len;
    if (!out || max_chars <= 0) return 0;
    out[0] = 0;

    shell32 = GetModuleHandleW(L"shell32.dll");
    if (!shell32) shell32 = LoadLibraryW(L"shell32.dll");
    if (shell32) {
        sh_get_folder_path = (MediaSHGetFolderPathWFn)GetProcAddress(shell32, "SHGetFolderPathW");
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

static WCHAR media_norm_path_ch(WCHAR c) {
    if (c == L'/') return L'\\';
    if (c >= L'A' && c <= L'Z') return (WCHAR)(c + (L'a' - L'A'));
    return c;
}

static int media_wide_has_xwechat_root(const WCHAR *path) {
    static const WCHAR needle[] = L"\\tencent\\xwechat\\";
    int i;
    int j;
    if (!path || !*path) return 0;
    for (i = 0; path[i]; i++) {
        for (j = 0; needle[j]; j++) {
            if (!path[i + j]) return 0;
            if (media_norm_path_ch(path[i + j]) != needle[j]) break;
        }
        if (!needle[j]) return 1;
    }
    return 0;
}

static int media_str_starts_with(const char *s, const char *prefix) {
    if (!s || !prefix) return 0;
    while (*prefix) {
        if (*s++ != *prefix++) return 0;
    }
    return 1;
}

static int media_stage_video_upload_file(const char *src_utf8,
                                          const char *client_id,
                                          char *out_utf8,
                                          int out_max) {
    WCHAR src[MEDIA_STAGE_WPATH_MAX];
    WCHAR dir[MEDIA_STAGE_WPATH_MAX];
    WCHAR dest[MEDIA_STAGE_WPATH_MAX];
    WCHAR safe_client[128];
    WCHAR suffix[64];
    const WCHAR *leaf;
    DWORD attr;
    int n;

    if (out_utf8 && out_max > 0) out_utf8[0] = 0;
    if (!src_utf8 || !*src_utf8 || !out_utf8 || out_max <= 0) return 0;
    if (strstr(src_utf8, "://")) return 0;
    if (!media_utf8_to_wide_path(src_utf8, src, MEDIA_STAGE_WPATH_MAX)) return 0;
    if (media_wide_has_xwechat_root(src)) return 0;

    attr = GetFileAttributesW(src);
    if (attr == INVALID_FILE_ATTRIBUTES || (attr & FILE_ATTRIBUTE_DIRECTORY)) return 0;
    leaf = media_wide_basename(src);
    if (!leaf || !*leaf) return 0;

    if (!media_get_appdata_w(dir, MEDIA_STAGE_WPATH_MAX)) return 0;
    if (!media_append_wide(dir, MEDIA_STAGE_WPATH_MAX, L"\\Tencent")) return 0;
    if (!media_ensure_dir_w(dir)) return 0;
    if (!media_append_wide(dir, MEDIA_STAGE_WPATH_MAX, L"\\xwechat")) return 0;
    if (!media_ensure_dir_w(dir)) return 0;
    if (!media_append_wide(dir, MEDIA_STAGE_WPATH_MAX, L"\\sns_publish_bridge")) return 0;
    if (!media_ensure_dir_w(dir)) return 0;

    media_ascii_safe_segment_w(client_id, safe_client,
                               (int)(sizeof(safe_client) / sizeof(safe_client[0])));
    wsprintfW(suffix, L"_%lu_%lu",
              (unsigned long)GetCurrentProcessId(),
              (unsigned long)GetTickCount());
    if (!media_append_wide(dir, MEDIA_STAGE_WPATH_MAX, L"\\")) return 0;
    if (!media_append_wide(dir, MEDIA_STAGE_WPATH_MAX, safe_client)) return 0;
    if (!media_append_wide(dir, MEDIA_STAGE_WPATH_MAX, suffix)) return 0;
    if (!media_ensure_dir_w(dir)) return 0;

    copy_bytes(dest, dir, (DWORD)((lstrlenW(dir) + 1) * sizeof(WCHAR)));
    if (!media_append_wide(dest, MEDIA_STAGE_WPATH_MAX, L"\\")) return 0;
    if (!media_append_wide(dest, MEDIA_STAGE_WPATH_MAX, leaf)) return 0;
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

static int media_find_sns_video_thumb_map(const char *client_id, char *out, int out_max) {
    char buf[65536];
    char *p;
    int n;
    int client_len;

    if (out && out_max > 0) out[0] = 0;
    if (!client_id || !*client_id || !out || out_max <= 0) return 0;
    client_len = lstrlenA(client_id);
    if (client_len <= 0) return 0;

    n = read_file_tail_bytes(SNS_VIDEO_THUMB_MAP_FILE, buf, sizeof(buf) - 1);
    if (n <= 0) return 0;
    buf[n] = 0;

    p = buf;
    while (*p) {
        char *line = p;
        char *tab;
        char *end = p;
        while (*end && *end != '\n') end++;
        if (*end == '\n') {
            *end = 0;
            p = end + 1;
        } else {
            p = end;
        }
        end = line + lstrlenA(line);
        if (end > line && *(end - 1) == '\r') *(end - 1) = 0;
        tab = line;
        while (*tab && *tab != '\t') tab++;
        if (*tab == '\t' && (tab - line) == client_len &&
            memcmp(line, client_id, (size_t)client_len) == 0) {
            const char *value = tab + 1;
            if (*value && lstrlenA(value) < out_max) {
                lstrcpynA(out, value, out_max);
            }
        }
    }
    return out[0] ? 1 : 0;
}

static int media_rewrite_sns_video_upload_main_file(int64_t upload_param) {
    int64_t base;
    char client_id[160];
    char src[2048];
    char staged[2048];

    if (!upload_param || !is_readable_ptr(upload_param, 0x310)) return 0;
    if (r32((void *)(upload_param + 196)) != 20202) return 0;
    if (!wx_read_sso_str(upload_param + 88, client_id, sizeof(client_id))) return 0;
    if (!media_str_starts_with(client_id, "publish_")) return 0;
    if (!wx_read_sso_str(upload_param + 288, src, sizeof(src))) return 0;
    if (!media_stage_video_upload_file(src, client_id, staged, sizeof(staged))) return 0;

    base = (int64_t)wxb_weixin_module();
    if (!base) return 0;
    wx_free_std_string_heap(base, upload_param + 288);
    wx_set_std_string_heap(base, upload_param + 288, staged);
    return 1;
}

static int media_rewrite_sns_video_upload_thumb_file(int64_t upload_param) {
    int64_t base;
    char client_id[160];
    char thumb[2048];
    char staged[2048];

    if (!upload_param || !is_readable_ptr(upload_param, 0x310)) return 0;
    if (r32((void *)(upload_param + 196)) != 20202) return 0;
    if (!wx_read_sso_str(upload_param + 88, client_id, sizeof(client_id))) return 0;
    if (!media_str_starts_with(client_id, "publish_")) return 0;
    if (!media_find_sns_video_thumb_map(client_id, thumb, sizeof(thumb))) return 0;
    if (!media_stage_video_upload_file(thumb, client_id, staged, sizeof(staged))) return 0;

    base = (int64_t)wxb_weixin_module();
    if (!base) return 0;
    wx_free_std_string_heap(base, upload_param + 416);
    wx_set_std_string_heap(base, upload_param + 416, staged);
    return 1;
}

static int media_extract_url_host(const char *url, char *out, int max) {
    const char *p;
    const char *authority_end;
    const char *scan;
    const char *host_start;
    const char *host_end;
    int len;

    if (!out || max <= 0) return 0;
    out[0] = 0;
    if (!url || !*url) return 0;

    p = url;
    scan = url;
    while (*scan) {
        if (scan[0] == ':' && scan[1] == '/' && scan[2] == '/') {
            p = scan + 3;
            break;
        }
        if (*scan == '/' || *scan == '?' || *scan == '#') break;
        scan++;
    }

    authority_end = p;
    while (*authority_end &&
           *authority_end != '/' &&
           *authority_end != '?' &&
           *authority_end != '#') {
        authority_end++;
    }

    host_start = p;
    scan = p;
    while (scan < authority_end) {
        if (*scan == '@') host_start = scan + 1;
        scan++;
    }

    host_end = authority_end;
    if (host_start < authority_end && *host_start == '[') {
        scan = host_start + 1;
        while (scan < authority_end) {
            if (*scan == ']') {
                host_end = scan + 1;
                break;
            }
            scan++;
        }
    } else {
        scan = host_start;
        while (scan < authority_end) {
            if (*scan == ':') {
                host_end = scan;
                break;
            }
            scan++;
        }
    }

    len = (int)(host_end - host_start);
    if (len <= 0) return 0;
    if (len >= max) len = max - 1;
    for (int i = 0; i < len; i++) out[i] = host_start[i];
    out[len] = 0;
    return len;
}


static int media_probe_file_utf8(const char *path, int64_t *size_out, char *head_hex, int head_hex_max) {
    WCHAR wpath[1024];
    WIN32_FILE_ATTRIBUTE_DATA fad;
    HANDLE h;
    BYTE head[16];
    DWORD read = 0;
    static const char hex[] = "0123456789abcdef";
    if (size_out) *size_out = 0;
    if (head_hex && head_hex_max > 0) head_hex[0] = 0;
    if (!media_utf8_to_wide_path(path, wpath, 1024)) return 0;
    if (!GetFileAttributesExW(wpath, GetFileExInfoStandard, &fad)) return 0;
    if (fad.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) return 0;
    if (size_out) {
        *size_out = ((int64_t)fad.nFileSizeHigh << 32) | (int64_t)fad.nFileSizeLow;
    }
    if (!head_hex || head_hex_max < 33) return 1;
    h = CreateFileW(wpath, GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE, NULL,
                    OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, NULL);
    if (h == INVALID_HANDLE_VALUE) return 1;
    if (ReadFile(h, head, sizeof(head), &read, NULL) && read > 0) {
        int pos = 0;
        for (DWORD i = 0; i < read && pos + 2 < head_hex_max; i++) {
            head_hex[pos++] = hex[(head[i] >> 4) & 0xF];
            head_hex[pos++] = hex[head[i] & 0xF];
        }
        head_hex[pos] = 0;
    }
    CloseHandle(h);
    return 1;
}

static int media_read_sso_wstr_utf8(int64_t s, char *out, int max) {
    int64_t len64, cap64;
    int len, n;
    const WCHAR *src;
    if (!s || !out || max <= 0) return 0;
    out[0] = 0;
    len64 = r64((void *)(s + 0x10));
    cap64 = r64((void *)(s + 0x18));
    if (len64 <= 0 || len64 > 0x8000 || cap64 <= 0) return 0;
    len = (int)len64;
    src = (cap64 >= 8) ? (const WCHAR *)r64((void *)s) : (const WCHAR *)s;
    if (!src) return 0;
    __try {
        n = WideCharToMultiByte(CP_UTF8, 0, src, len, out, max - 1, NULL, NULL);
        if (n <= 0) {
            out[0] = 0;
            return 0;
        }
        out[n] = 0;
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
        out[0] = 0;
        return 0;
    }
    return n;
}

static int media_read_sso_bytes_hex(int64_t s, char *text, int text_max, char *hex_out, int hex_max) {
    int64_t len64, cap64;
    int len, n = 0;
    const BYTE *src;
    static const char hex[] = "0123456789abcdef";
    if (text && text_max > 0) text[0] = 0;
    if (hex_out && hex_max > 0) hex_out[0] = 0;
    if (!s) return 0;
    len64 = r64((void *)(s + 0x10));
    cap64 = r64((void *)(s + 0x18));
    if (len64 <= 0 || len64 > 0x10000 || cap64 <= 0) return 0;
    len = (int)len64;
    src = (cap64 >= 0x10) ? (const BYTE *)r64((void *)s) : (const BYTE *)s;
    if (!src) return 0;
    __try {
        if (text && text_max > 0) {
            n = len < text_max - 1 ? len : text_max - 1;
            for (int i = 0; i < n; i++) {
                BYTE c = src[i];
                text[i] = (c >= 0x20 && c < 0x7F) ? (char)c : '.';
            }
            text[n] = 0;
        }
        if (hex_out && hex_max > 0) {
            int pos = 0;
            for (int i = 0; i < len && pos + 2 < hex_max; i++) {
                hex_out[pos++] = hex[(src[i] >> 4) & 0xF];
                hex_out[pos++] = hex[src[i] & 0xF];
            }
            hex_out[pos] = 0;
        }
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
        if (text && text_max > 0) text[0] = 0;
        if (hex_out && hex_max > 0) hex_out[0] = 0;
        return 0;
    }
    return len;
}

static int media_bytes_equal(const BYTE *a, const BYTE *b, int n) {
    for (int i = 0; i < n; i++) {
        if (a[i] != b[i]) return 0;
    }
    return 1;
}

static int media_write_abs_jump(BYTE *target, int patch_len, void *dst) {
    DWORD oldp;
    if (patch_len < 12) return 0;
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

static int media_build_trampoline(BYTE *tramp, BYTE *target, const BYTE *orig, int patch_len) {
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

static void media_append_hook_hex_ptr(char **p, char *end, const char *name, int64_t value) {
    media_append_hex_field(p, end, name, value);
}

static void media_log_sns_cdn_event(const char *fn_name, int64_t cdn_file,
                                    uint64_t file_off, uint32_t len,
                                    int final_block, int64_t ret,
                                    uint32_t written, uint32_t err) {
    char line[8192];
    char path[1024];
    char key_text[256];
    char key_hex[512];
    char *p = line;
    char *end = line + sizeof(line) - 1;
    int key_len = 0;
    path[0] = 0;
    key_text[0] = 0;
    key_hex[0] = 0;
    wx_read_sso_str(cdn_file + 0x10, path, sizeof(path));
    key_len = media_read_sso_bytes_hex(cdn_file + 0x30, key_text, sizeof(key_text), key_hex, sizeof(key_hex));

    append_raw(&p, end, "{\"event\":\"sns_cdn_file_write\",\"fn\":\"");
    append_escaped(&p, end, fn_name ? fn_name : "");
    append_raw(&p, end, "\"");
    media_append_hook_hex_ptr(&p, end, "cdnFile", cdn_file);
    append_raw(&p, end, ",\"path\":\"");
    append_escaped(&p, end, path);
    append_raw(&p, end, "\",\"keyText\":\"");
    append_escaped(&p, end, key_text);
    append_raw(&p, end, "\",\"keyHex\":\"");
    append_escaped(&p, end, key_hex);
    append_raw(&p, end, "\"");
    media_append_int_field(&p, end, "keyLen", key_len);
    media_append_hex_field(&p, end, "fileOff", (int64_t)file_off);
    media_append_int_field(&p, end, "inputLen", (int)len);
    media_append_int_field(&p, end, "finalBlock", final_block);
    media_append_i64_field(&p, end, "ret", ret);
    media_append_int_field(&p, end, "written", (int)written);
    media_append_hex_field(&p, end, "err", err);
    append_raw(&p, end, "}\r\n");
    *p = 0;
    append_file_bytes(SNS_MEDIA_DECRYPT_CAPTURE_LOG, line, (DWORD)(p - line));
}

static int64_t __fastcall sns_cdn_write_decrypt_hook(int64_t cdn_file, int64_t buf,
                                                     uint64_t file_off, uint32_t len,
                                                     uint8_t final_block,
                                                     uint32_t *written_out,
                                                     uint32_t *err_out) {
    FnSnsCdnWriteDecrypt orig = (FnSnsCdnWriteDecrypt)g_sns_cdn_write_decrypt_hook.trampoline;
    int64_t ret = 0;
    uint32_t written = 0;
    uint32_t err = 0;
    if (orig) ret = orig(cdn_file, buf, file_off, len, final_block, written_out, err_out);
    __try {
        if (written_out) written = *written_out;
        if (err_out) err = *err_out;
        media_log_sns_cdn_event("write_decrypt", cdn_file, file_off, len, final_block, ret, written, err);
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
    }
    return ret;
}

static int64_t __fastcall sns_cdn_write_raw_hook(int64_t cdn_file, int64_t buf,
                                                 int64_t file_off, int len,
                                                 uint32_t *err_out) {
    FnSnsCdnWriteRaw orig = (FnSnsCdnWriteRaw)g_sns_cdn_write_raw_hook.trampoline;
    int64_t ret = 0;
    uint32_t err = 0;
    if (orig) ret = orig(cdn_file, buf, file_off, len, err_out);
    __try {
        if (err_out) err = *err_out;
        media_log_sns_cdn_event("write_raw", cdn_file, (uint64_t)file_off, (uint32_t)len, 0, ret, (uint32_t)ret, err);
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
    }
    return ret;
}

static void install_sns_media_decrypt_capture_hooks(void) {
    int64_t base = (int64_t)wxb_weixin_module();
    BYTE *target;
    int tlen;
    if (!base) return;
    target = (BYTE *)(base + 0x426A5C0);
    if (InterlockedCompareExchange(&g_sns_cdn_write_decrypt_hook.installed, 0, 0) == 0) {
        tlen = (int)sizeof(WX_ORIG_SNS_CDN_WRITE_DECRYPT_BYTES);
        if (media_bytes_equal(target, WX_ORIG_SNS_CDN_WRITE_DECRYPT_BYTES, tlen)) {
            copy_bytes(g_sns_cdn_write_decrypt_hook.orig, target, tlen);
            g_sns_cdn_write_decrypt_hook.trampoline = VirtualAlloc(NULL, 128, MEM_COMMIT | MEM_RESERVE, PAGE_EXECUTE_READWRITE);
            if (g_sns_cdn_write_decrypt_hook.trampoline) {
                media_build_trampoline((BYTE *)g_sns_cdn_write_decrypt_hook.trampoline, target,
                                       g_sns_cdn_write_decrypt_hook.orig, tlen);
                if (media_write_abs_jump(target, tlen, (void *)sns_cdn_write_decrypt_hook)) {
                    g_sns_cdn_write_decrypt_hook.patch_len = tlen;
                    InterlockedExchange(&g_sns_cdn_write_decrypt_hook.installed, 1);
                }
            }
        }
    }
    target = (BYTE *)(base + 0x42691B0);
    if (InterlockedCompareExchange(&g_sns_cdn_write_raw_hook.installed, 0, 0) == 0) {
        tlen = (int)sizeof(WX_ORIG_SNS_CDN_WRITE_RAW_BYTES);
        if (media_bytes_equal(target, WX_ORIG_SNS_CDN_WRITE_RAW_BYTES, tlen)) {
            copy_bytes(g_sns_cdn_write_raw_hook.orig, target, tlen);
            g_sns_cdn_write_raw_hook.trampoline = VirtualAlloc(NULL, 128, MEM_COMMIT | MEM_RESERVE, PAGE_EXECUTE_READWRITE);
            if (g_sns_cdn_write_raw_hook.trampoline) {
                media_build_trampoline((BYTE *)g_sns_cdn_write_raw_hook.trampoline, target,
                                       g_sns_cdn_write_raw_hook.orig, tlen);
                if (media_write_abs_jump(target, tlen, (void *)sns_cdn_write_raw_hook)) {
                    g_sns_cdn_write_raw_hook.patch_len = tlen;
                    InterlockedExchange(&g_sns_cdn_write_raw_hook.installed, 1);
                }
            }
        }
    }
}

static void media_append_ptr_summary(char **p, char *end, const char *prefix, int64_t ptr) {
    char name[64];
    wsprintfA(name, "%s", prefix);
    media_append_hex_field(p, end, name, ptr);
    if (!ptr || !is_readable_ptr(ptr, 0x20)) return;
    wsprintfA(name, "%s_vt", prefix);
    media_append_hex_field(p, end, name, r64((void *)ptr));
    wsprintfA(name, "%s_i08", prefix);
    media_append_hex_field(p, end, name, r64((void *)(ptr + 0x08)));
    wsprintfA(name, "%s_i10", prefix);
    media_append_hex_field(p, end, name, r64((void *)(ptr + 0x10)));
    wsprintfA(name, "%s_i18", prefix);
    media_append_hex_field(p, end, name, r64((void *)(ptr + 0x18)));
}

static void media_append_send_item_summary(char **p, char *end, const char *prefix, int64_t send_item) {
    char name[64];
    wsprintfA(name, "%s", prefix);
    media_append_hex_field(p, end, name, send_item);
    if (!send_item || !is_readable_ptr(send_item, 0xB0)) return;
    wsprintfA(name, "%s_vt", prefix);
    media_append_hex_field(p, end, name, r64((void *)send_item));
    wsprintfA(name, "%s_type", prefix);
    media_append_int_field(p, end, name, r32((void *)(send_item + 0x08)));
    wsprintfA(name, "%s_subtype", prefix);
    media_append_int_field(p, end, name, r32((void *)(send_item + 0x0C)));
    wsprintfA(name, "%s_local10", prefix);
    media_append_int_field(p, end, name, r32((void *)(send_item + 0x10)));
    wsprintfA(name, "%s_svrid", prefix);
    media_append_hex_field(p, end, name, r64((void *)(send_item + 0x98)));
}

static void media_append_out_pair_summary(char **p, char *end, const char *prefix, int64_t pair) {
    char name[64];
    wsprintfA(name, "%s", prefix);
    media_append_hex_field(p, end, name, pair);
    if (!pair || !is_readable_ptr(pair, 0x20)) return;
    wsprintfA(name, "%s_0", prefix);
    media_append_hex_field(p, end, name, r64((void *)pair));
    wsprintfA(name, "%s_8", prefix);
    media_append_hex_field(p, end, name, r64((void *)(pair + 8)));
    wsprintfA(name, "%s_10", prefix);
    media_append_hex_field(p, end, name, r64((void *)(pair + 0x10)));
    wsprintfA(name, "%s_18", prefix);
    media_append_hex_field(p, end, name, r64((void *)(pair + 0x18)));
}

static void media_append_sso_summary(char **p, char *end, const char *prefix, int64_t sso) {
    char name[64];
    char text[256];
    char hex[512];
    int len = 0;
    wsprintfA(name, "%s", prefix);
    media_append_hex_field(p, end, name, sso);
    if (!sso || !is_readable_ptr(sso, 0x20)) return;
    text[0] = 0;
    hex[0] = 0;
    len = wx_read_sso_str(sso, text, sizeof(text));
    if (len <= 0) len = media_read_sso_bytes_hex(sso, text, sizeof(text), hex, sizeof(hex));
    wsprintfA(name, "%s_len", prefix);
    media_append_int_field(p, end, name, len);
    wsprintfA(name, "%s_text", prefix);
    append_raw(p, end, ",\"");
    append_raw(p, end, name);
    append_raw(p, end, "\":\"");
    append_escaped(p, end, text);
    append_raw(p, end, "\"");
    if (hex[0]) {
        wsprintfA(name, "%s_hex", prefix);
        append_raw(p, end, ",\"");
        append_raw(p, end, name);
        append_raw(p, end, "\":\"");
        append_escaped(p, end, hex);
        append_raw(p, end, "\"");
    }
}

static void media_append_status_summary(char **p, char *end, const char *prefix, int64_t status) {
    char name[64];
    wsprintfA(name, "%s", prefix);
    media_append_hex_field(p, end, name, status);
    if (!status || !is_readable_ptr(status, 0x40)) return;
    wsprintfA(name, "%s_i0", prefix);
    media_append_int_field(p, end, name, r32((void *)status));
    wsprintfA(name, "%s_i4", prefix);
    media_append_int_field(p, end, name, r32((void *)(status + 4)));
    media_append_sso_summary(p, end, "status_sso8", status + 8);
}

static void media_log_image_trace_prefix(char **p, char *end, const char *event, int64_t retaddr) {
    int64_t base = (int64_t)wxb_weixin_module();
    append_raw(p, end, "{\"event\":\"");
    append_escaped(p, end, event ? event : "");
    append_raw(p, end, "\"");
    media_append_i64_field(p, end, "tick", (int64_t)GetTickCount64());
    media_append_int_field(p, end, "tid", (int)GetCurrentThreadId());
    media_append_hex_field(p, end, "base", base);
    media_append_hex_field(p, end, "retaddr", retaddr);
    if (base && retaddr >= base && retaddr < base + 0x0B000000) {
        media_append_hex_field(p, end, "ret_rva", retaddr - base);
    }
}

static void sns_upload_trace_append_param(char **p, char *end, int64_t upload_param) {
    if (!upload_param || !is_readable_ptr(upload_param, 0x310)) return;
    media_append_hex_field(p, end, "callback_obj", r64((void *)(upload_param + 0)));
    media_append_hex_field(p, end, "callback_ctl", r64((void *)(upload_param + 8)));
    media_append_int_field(p, end, "biz_selector_80", r32((void *)(upload_param + 80)));
    media_append_int_field(p, end, "app_selector_84", r32((void *)(upload_param + 84)));
    media_append_sso_summary(p, end, "client_media_id_88", upload_param + 88);
    media_append_int_field(p, end, "file_type_196", r32((void *)(upload_param + 196)));
    media_append_sso_summary(p, end, "main_file_288", upload_param + 288);
    media_append_i64_field(p, end, "main_file_len_304", r64((void *)(upload_param + 304)));
    media_append_sso_summary(p, end, "thumb_file_416", upload_param + 416);
    media_append_i64_field(p, end, "thumb_file_len_432", r64((void *)(upload_param + 432)));
    media_append_int_field(p, end, "format_448", r32((void *)(upload_param + 448)));
    media_append_int_field(p, end, "format_452", r32((void *)(upload_param + 452)));
    media_append_int_field(p, end, "flag_592", *((BYTE *)(upload_param + 592)) ? 1 : 0);
    media_append_int_field(p, end, "flag_760", *((BYTE *)(upload_param + 760)) ? 1 : 0);
    media_append_int_field(p, end, "flag_761", *((BYTE *)(upload_param + 761)) ? 1 : 0);
    media_append_int_field(p, end, "flag_762", *((BYTE *)(upload_param + 762)) ? 1 : 0);
    media_append_int_field(p, end, "flag_763", *((BYTE *)(upload_param + 763)) ? 1 : 0);
}

static void sns_upload_trace_log(const char *event, int64_t manager,
                                 int64_t upload_param, int64_t retaddr,
                                 int64_t ret) {
    char line[32768];
    char *p = line;
    char *end = line + sizeof(line) - 1;
    if (!file_exists(SNS_UPLOAD_TRACE_ENABLE_FILE)) return;
    media_log_image_trace_prefix(&p, end, event, retaddr);
    media_append_hex_field(&p, end, "manager", manager);
    media_append_hex_field(&p, end, "upload_param", upload_param);
    sns_upload_trace_append_param(&p, end, upload_param);
    media_append_i64_field(&p, end, "ret", ret);
    append_raw(&p, end, "}\r\n");
    *p = 0;
    append_file_bytes(SNS_UPLOAD_TRACE_LOG, line, (DWORD)(p - line));
}

static int media_read_hook_target_bytes(DWORD rva, BYTE *out, int len) {
    int64_t base = (int64_t)wxb_weixin_module();
    BYTE *target;
    if (!base || !out || len <= 0) return 0;
    target = (BYTE *)(base + rva);
    __try {
        copy_bytes(out, target, len);
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
        return 0;
    }
    return 1;
}

static void media_append_bytes_field(char **p, char *end, const char *name,
                                     const BYTE *bytes, int len) {
    static const char hex[] = "0123456789abcdef";
    char tmp[3];
    int i;
    if (!p || !*p || !end || !name || !bytes || len <= 0) return;
    append_raw(p, end, ",\"");
    append_raw(p, end, name);
    append_raw(p, end, "\":\"");
    tmp[2] = 0;
    for (i = 0; i < len; ++i) {
        tmp[0] = hex[(bytes[i] >> 4) & 0x0F];
        tmp[1] = hex[bytes[i] & 0x0F];
        append_raw(p, end, tmp);
    }
    append_raw(p, end, "\"");
}

static int media_hook_expected_match(DWORD rva, const BYTE *expected, int len) {
    BYTE actual[64];
    if (!expected || len <= 0 || len > (int)sizeof(actual)) return 0;
    if (!media_read_hook_target_bytes(rva, actual, len)) return 0;
    return media_bytes_equal(actual, expected, len);
}

static void sns_upload_trace_install_log(int logic_ok,
                                         int logic_was_installed,
                                         int logic_match_before,
                                         int core2_ok,
                                         int core2_was_installed,
                                         int core2_match_before) {
    char line[4096];
    char status[256];
    char *p = line;
    char *end = line + sizeof(line) - 1;
    int64_t base = (int64_t)wxb_weixin_module();
    BYTE logic_actual[64];
    BYTE core2_actual[64];
    int logic_have_actual;
    int core2_have_actual;
    if (!file_exists(SNS_UPLOAD_TRACE_ENABLE_FILE)) return;

    logic_have_actual = media_read_hook_target_bytes(WX_OFF_CDN_UPLOAD_LOGIC_SUBMIT,
                                                     logic_actual,
                                                     (int)sizeof(WX_ORIG_CDN_UPLOAD_LOGIC_SUBMIT_BYTES));
    core2_have_actual = media_read_hook_target_bytes(WX_OFF_CDN_UPLOAD_CORE2,
                                                     core2_actual,
                                                     (int)sizeof(WX_ORIG_CDN_UPLOAD_CORE2_BYTES));

    append_raw(&p, end, "{\"event\":\"sns_upload_trace_install\"");
    media_append_i64_field(&p, end, "tick", (int64_t)GetTickCount64());
    media_append_int_field(&p, end, "tid", (int)GetCurrentThreadId());
    media_append_hex_field(&p, end, "base", base);
    media_append_hex_field(&p, end, "logic_rva", WX_OFF_CDN_UPLOAD_LOGIC_SUBMIT);
    media_append_int_field(&p, end, "logic_ok", logic_ok);
    media_append_int_field(&p, end, "logic_was_installed", logic_was_installed);
    media_append_int_field(&p, end, "logic_match_before", logic_match_before);
    media_append_int_field(&p, end, "logic_installed",
                           InterlockedCompareExchange(&g_sns_upload_logic_submit_hook.installed, 0, 0) ? 1 : 0);
    if (logic_have_actual) {
        media_append_bytes_field(&p, end, "logic_actual",
                                 logic_actual,
                                 (int)sizeof(WX_ORIG_CDN_UPLOAD_LOGIC_SUBMIT_BYTES));
    }
    media_append_bytes_field(&p, end, "logic_expected",
                             WX_ORIG_CDN_UPLOAD_LOGIC_SUBMIT_BYTES,
                             (int)sizeof(WX_ORIG_CDN_UPLOAD_LOGIC_SUBMIT_BYTES));
    media_append_hex_field(&p, end, "core2_rva", WX_OFF_CDN_UPLOAD_CORE2);
    media_append_int_field(&p, end, "core2_ok", core2_ok);
    media_append_int_field(&p, end, "core2_was_installed", core2_was_installed);
    media_append_int_field(&p, end, "core2_match_before", core2_match_before);
    media_append_int_field(&p, end, "core2_installed",
                           InterlockedCompareExchange(&g_sns_upload_core2_hook.installed, 0, 0) ? 1 : 0);
    if (core2_have_actual) {
        media_append_bytes_field(&p, end, "core2_actual",
                                 core2_actual,
                                 (int)sizeof(WX_ORIG_CDN_UPLOAD_CORE2_BYTES));
    }
    media_append_bytes_field(&p, end, "core2_expected",
                             WX_ORIG_CDN_UPLOAD_CORE2_BYTES,
                             (int)sizeof(WX_ORIG_CDN_UPLOAD_CORE2_BYTES));
    append_raw(&p, end, "}\r\n");
    *p = 0;
    append_file_bytes(SNS_UPLOAD_TRACE_LOG, line, (DWORD)(p - line));

    wsprintfA(status,
              "sns upload trace hooks logic=%d core2=%d logic_match=%d core2_match=%d\n",
              logic_ok,
              core2_ok,
              logic_match_before,
              core2_match_before);
    write_status(status);
}

static void sns_media_probe_append_float_summary(char **p, char *end,
                                                 const char *name, int64_t addr) {
    char field[64];
    if (!addr || !is_readable_ptr(addr, 4)) return;
    wsprintfA(field, "%s_bits", name);
    media_append_hex_field(p, end, field, (uint32_t)r32((void *)addr));
    wsprintfA(field, "%s_int", name);
    media_append_int_field(p, end, field, (int)(*(float *)addr));
}

static void sns_media_probe_append_url_object(char **p, char *end,
                                              const char *prefix, int64_t obj) {
    char name[96];
    wsprintfA(name, "%s_obj", prefix);
    media_append_hex_field(p, end, name, obj);
    if (!obj || !is_readable_ptr(obj, 0xC8)) return;

    wsprintfA(name, "%s_url", prefix);
    media_append_sso_summary(p, end, name, obj + 0);
    wsprintfA(name, "%s_type", prefix);
    media_append_i64_field(p, end, name, r64((void *)(obj + 32)));
    wsprintfA(name, "%s_md5", prefix);
    media_append_sso_summary(p, end, name, obj + 40);
    wsprintfA(name, "%s_key", prefix);
    media_append_sso_summary(p, end, name, obj + 72);
    wsprintfA(name, "%s_enc_idx", prefix);
    media_append_int_field(p, end, name, r32((void *)(obj + 80)));
    wsprintfA(name, "%s_token", prefix);
    media_append_sso_summary(p, end, name, obj + 88);
    wsprintfA(name, "%s_videomd5", prefix);
    media_append_sso_summary(p, end, name, obj + 120);
    wsprintfA(name, "%s_aeskey", prefix);
    media_append_sso_summary(p, end, name, obj + 168);
}

static void sns_media_probe_append_thumb_vector(char **p, char *end, int64_t media_item) {
    int64_t begin;
    int64_t vec_end;
    int count = 0;
    int64_t obj = 0;
    int64_t ctl = 0;
    if (!media_item || !is_readable_ptr(media_item + 384, 24)) return;
    begin = r64((void *)(media_item + 384));
    vec_end = r64((void *)(media_item + 392));
    media_append_hex_field(p, end, "thumb_vec_begin", begin);
    media_append_hex_field(p, end, "thumb_vec_end", vec_end);
    media_append_hex_field(p, end, "thumb_vec_cap", r64((void *)(media_item + 400)));
    if (begin && vec_end && vec_end >= begin && ((vec_end - begin) % 16) == 0 &&
        (vec_end - begin) <= 16 * 16 && is_readable_ptr(begin, (SIZE_T)(vec_end - begin))) {
        count = (int)((vec_end - begin) / 16);
        if (count > 0) {
            obj = r64((void *)begin);
            ctl = r64((void *)(begin + 8));
        }
    }
    media_append_int_field(p, end, "thumb_count", count);
    media_append_hex_field(p, end, "thumb0_ctl", ctl);
    sns_media_probe_append_url_object(p, end, "thumb0", obj);
}

static void sns_media_probe_log_item(const char *event, unsigned int *media_item,
                                     int64_t json_node, int64_t retaddr) {
    char line[32768];
    char *p = line;
    char *end = line + sizeof(line) - 1;
    int64_t item = (int64_t)media_item;

    media_log_image_trace_prefix(&p, end, event, retaddr);
    media_append_hex_field(&p, end, "media_item", item);
    media_append_hex_field(&p, end, "json_node", json_node);
    if (item && is_readable_ptr(item, 0x1A0)) {
        media_append_int_field(&p, end, "media_type", r32((void *)(item + 0)));
        media_append_sso_summary(&p, end, "client_id", item + 8);
        media_append_sso_summary(&p, end, "local_src_url", item + 40);
        media_append_sso_summary(&p, end, "local_data_url", item + 72);
        media_append_sso_summary(&p, end, "local_thumb_url", item + 104);
        sns_media_probe_append_float_summary(&p, end, "img_width", item + 136);
        sns_media_probe_append_float_summary(&p, end, "img_height", item + 140);
        media_append_int_field(&p, end, "field148", r32((void *)(item + 148)));
        media_append_sso_summary(&p, end, "media_md5", item + 152);
        sns_media_probe_append_url_object(&p, end, "buffer_url", item + 184);
        sns_media_probe_append_thumb_vector(&p, end, item);
        media_append_int_field(&p, end, "is_finished", *((BYTE *)(item + 408)) ? 1 : 0);
    }
    append_raw(&p, end, "}\r\n");
    *p = 0;
    append_file_bytes(SNS_MEDIA_PROBE_LOG, line, (DWORD)(p - line));
}

static void media_log_image_stage_event(const char *event, int64_t stage, int64_t ctx,
                                        int64_t retaddr, int64_t ret) {
    char line[8192];
    char *p = line;
    char *end = line + sizeof(line) - 1;
    media_log_image_trace_prefix(&p, end, event, retaddr);
    media_append_hex_field(&p, end, "stage", stage);
    if (stage && is_readable_ptr(stage, 0x120)) {
        media_append_hex_field(&p, end, "stage_vt", r64((void *)stage));
        media_append_hex_field(&p, end, "stage_target", r64((void *)(stage + 8)));
        media_append_send_item_summary(&p, end, "stage_send_item", stage + 16);
    }
    media_append_out_pair_summary(&p, end, "ctx", ctx);
    media_append_hex_field(&p, end, "ret", ret);
    append_raw(&p, end, "}\r\n");
    *p = 0;
    append_file_bytes(IMAGE_TASK_TRACE_LOG, line, (DWORD)(p - line));
}

static void media_log_image_body_event(const char *event, int64_t task, int64_t retaddr, int64_t ret) {
    char line[8192];
    char *p = line;
    char *end = line + sizeof(line) - 1;
    media_log_image_trace_prefix(&p, end, event, retaddr);
    media_append_hex_field(&p, end, "task", task);
    if (task && is_readable_ptr(task, 0x390)) {
        media_append_hex_field(&p, end, "task_vt", r64((void *)task));
        media_append_hex_field(&p, end, "task_target", r64((void *)(task + 8)));
        media_append_hex_field(&p, end, "task_target_ctl", r64((void *)(task + 16)));
        media_append_send_item_summary(&p, end, "task_send_item", task + 248);
    }
    media_append_hex_field(&p, end, "ret", ret);
    append_raw(&p, end, "}\r\n");
    *p = 0;
    append_file_bytes(IMAGE_TASK_TRACE_LOG, line, (DWORD)(p - line));
}

static void media_log_image_helper_event(const char *event, int64_t manager, int64_t out_pair,
                                         int64_t obj, unsigned int type, unsigned int flags,
                                         int *opts, int64_t retaddr, int64_t ret) {
    char line[8192];
    char *p = line;
    char *end = line + sizeof(line) - 1;
    media_log_image_trace_prefix(&p, end, event, retaddr);
    media_append_hex_field(&p, end, "manager", manager);
    media_append_out_pair_summary(&p, end, "out", out_pair);
    media_append_send_item_summary(&p, end, "obj_send_item", obj);
    media_append_ptr_summary(&p, end, "obj", obj);
    media_append_int_field(&p, end, "type", (int)type);
    media_append_hex_field(&p, end, "flags", (int64_t)flags);
    media_append_hex_field(&p, end, "opts", (int64_t)opts);
    if (opts && is_readable_ptr((int64_t)opts, 0x50)) {
        media_append_int_field(&p, end, "opts0", opts[0]);
        media_append_int_field(&p, end, "opts1", opts[1]);
        media_append_hex_field(&p, end, "opts8", r64((void *)((BYTE *)opts + 8)));
        media_append_hex_field(&p, end, "opts28", r64((void *)((BYTE *)opts + 0x28)));
    }
    media_append_hex_field(&p, end, "ret", ret);
    append_raw(&p, end, "}\r\n");
    *p = 0;
    append_file_bytes(IMAGE_TASK_TRACE_LOG, line, (DWORD)(p - line));
}

static void media_log_image_af500_event(const char *event, int64_t manager, int64_t out_status,
                                        int64_t key_sso, unsigned int type, int64_t path_sso,
                                        int64_t request, int64_t retaddr, int64_t ret) {
    char line[8192];
    char *p = line;
    char *end = line + sizeof(line) - 1;
    media_log_image_trace_prefix(&p, end, event, retaddr);
    media_append_hex_field(&p, end, "manager", manager);
    media_append_status_summary(&p, end, "out_status", out_status);
    media_append_sso_summary(&p, end, "key_sso", key_sso);
    media_append_int_field(&p, end, "type", (int)type);
    media_append_sso_summary(&p, end, "path_sso", path_sso);
    media_append_hex_field(&p, end, "request", request);
    if (request && is_readable_ptr(request, 0xA0)) {
        media_append_hex_field(&p, end, "request_vt", r64((void *)request));
        media_append_int_field(&p, end, "request_i20", r32((void *)(request + 0x20)));
        media_append_int_field(&p, end, "request_i24", r32((void *)(request + 0x24)));
        media_append_int_field(&p, end, "request_i28", r32((void *)(request + 0x28)));
    }
    media_append_hex_field(&p, end, "ret", ret);
    append_raw(&p, end, "}\r\n");
    *p = 0;
    append_file_bytes(IMAGE_TASK_TRACE_LOG, line, (DWORD)(p - line));
}

static void media_log_image_ac170_event(const char *event, int64_t manager, int64_t obj,
                                        unsigned int type, int64_t retaddr, int64_t ret) {
    char line[4096];
    char *p = line;
    char *end = line + sizeof(line) - 1;
    media_log_image_trace_prefix(&p, end, event, retaddr);
    media_append_hex_field(&p, end, "manager", manager);
    media_append_ptr_summary(&p, end, "obj", obj);
    media_append_int_field(&p, end, "type", (int)type);
    media_append_hex_field(&p, end, "ret", ret);
    append_raw(&p, end, "}\r\n");
    *p = 0;
    append_file_bytes(IMAGE_TASK_TRACE_LOG, line, (DWORD)(p - line));
}

static void media_log_image_ac720_event(const char *event, int64_t manager, int64_t out_pair,
                                        int64_t key_sso, int64_t path_sso, int marker,
                                        int64_t retaddr, int64_t ret) {
    char line[8192];
    char *p = line;
    char *end = line + sizeof(line) - 1;
    media_log_image_trace_prefix(&p, end, event, retaddr);
    media_append_hex_field(&p, end, "manager", manager);
    media_append_out_pair_summary(&p, end, "out", out_pair);
    media_append_sso_summary(&p, end, "key_sso", key_sso);
    media_append_sso_summary(&p, end, "path_sso", path_sso);
    media_append_int_field(&p, end, "marker", marker);
    media_append_hex_field(&p, end, "ret", ret);
    append_raw(&p, end, "}\r\n");
    *p = 0;
    append_file_bytes(IMAGE_TASK_TRACE_LOG, line, (DWORD)(p - line));
}

static void media_log_image_queue_submit_event(const char *event, int64_t queue_mgr,
                                               int64_t flags, int64_t task,
                                               int64_t retaddr, int64_t ret) {
    char line[4096];
    char *p = line;
    char *end = line + sizeof(line) - 1;
    media_log_image_trace_prefix(&p, end, event, retaddr);
    media_append_hex_field(&p, end, "queue_mgr", queue_mgr);
    media_append_ptr_summary(&p, end, "flags", flags);
    media_append_ptr_summary(&p, end, "task", task);
    media_append_hex_field(&p, end, "ret", ret);
    append_raw(&p, end, "}\r\n");
    *p = 0;
    append_file_bytes(IMAGE_TASK_TRACE_LOG, line, (DWORD)(p - line));
}

static void media_append_key_ptr_summary(char **p, char *end, const char *prefix, int64_t key_ptr) {
    char name[64];
    wsprintfA(name, "%s", prefix);
    media_append_hex_field(p, end, name, key_ptr);
    if (!key_ptr || !is_readable_ptr(key_ptr, 4)) return;
    wsprintfA(name, "%s_i0", prefix);
    media_append_int_field(p, end, name, r32((void *)key_ptr));
    wsprintfA(name, "%s_u0", prefix);
    media_append_hex_field(p, end, name, (uint32_t)r32((void *)key_ptr));
}

static void media_append_queue_summary(char **p, char *end, const char *prefix, int64_t queue_mgr) {
    char name[64];
    wsprintfA(name, "%s", prefix);
    media_append_hex_field(p, end, name, queue_mgr);
    if (!queue_mgr || !is_readable_ptr(queue_mgr, 0x248)) return;
    wsprintfA(name, "%s_ready240", prefix);
    media_append_int_field(p, end, name, *((BYTE *)(queue_mgr + 0x240)) ? 1 : 0);
    wsprintfA(name, "%s_cached230", prefix);
    media_append_hex_field(p, end, name, r64((void *)(queue_mgr + 0x230)));
    wsprintfA(name, "%s_cached238", prefix);
    media_append_hex_field(p, end, name, r64((void *)(queue_mgr + 0x238)));
}

static void media_log_image_b9330_event(const char *event, int64_t lookup_req,
                                        int64_t out_pair, int64_t retaddr, int64_t ret) {
    char line[8192];
    char *p = line;
    char *end = line + sizeof(line) - 1;
    media_log_image_trace_prefix(&p, end, event, retaddr);
    media_append_ptr_summary(&p, end, "lookup_req", lookup_req);
    if (lookup_req && is_readable_ptr(lookup_req, 0x40)) {
        media_append_hex_field(&p, end, "lookup_req_obj", r64((void *)lookup_req));
        media_append_sso_summary(&p, end, "lookup_req_key_sso", lookup_req + 8);
        media_append_int_field(&p, end, "lookup_req_i28", r32((void *)(lookup_req + 0x28)));
        media_append_int_field(&p, end, "lookup_req_i38", r32((void *)(lookup_req + 0x38)));
        media_append_int_field(&p, end, "lookup_req_i3c", r32((void *)(lookup_req + 0x3c)));
    }
    media_append_out_pair_summary(&p, end, "out", out_pair);
    media_append_hex_field(&p, end, "ret", ret);
    append_raw(&p, end, "}\r\n");
    *p = 0;
    append_file_bytes(IMAGE_TASK_TRACE_LOG, line, (DWORD)(p - line));
}

static void media_log_image_pair_path_event(const char *event, int64_t manager,
                                            int64_t out_pair, int64_t in_pair,
                                            int64_t path_sso, int64_t retaddr,
                                            int64_t ret) {
    char line[8192];
    char *p = line;
    char *end = line + sizeof(line) - 1;
    media_log_image_trace_prefix(&p, end, event, retaddr);
    media_append_hex_field(&p, end, "manager", manager);
    media_append_out_pair_summary(&p, end, "out", out_pair);
    media_append_out_pair_summary(&p, end, "in_pair", in_pair);
    media_append_sso_summary(&p, end, "path_sso", path_sso);
    media_append_hex_field(&p, end, "ret", ret);
    append_raw(&p, end, "}\r\n");
    *p = 0;
    append_file_bytes(IMAGE_TASK_TRACE_LOG, line, (DWORD)(p - line));
}

static void media_log_image_queue_lookup_event(const char *event, int64_t queue_mgr,
                                               int64_t out_pair, int64_t key_ptr,
                                               int64_t path_sso, int limit,
                                               int remove_existing, int log_detail,
                                               int64_t retaddr, int64_t ret) {
    char line[8192];
    char *p = line;
    char *end = line + sizeof(line) - 1;
    media_log_image_trace_prefix(&p, end, event, retaddr);
    media_append_queue_summary(&p, end, "queue_mgr", queue_mgr);
    media_append_out_pair_summary(&p, end, "out", out_pair);
    media_append_key_ptr_summary(&p, end, "key_ptr", key_ptr);
    media_append_sso_summary(&p, end, "path_sso", path_sso);
    media_append_int_field(&p, end, "limit", limit);
    media_append_int_field(&p, end, "remove_existing", remove_existing);
    media_append_int_field(&p, end, "log_detail", log_detail);
    media_append_hex_field(&p, end, "ret", ret);
    append_raw(&p, end, "}\r\n");
    *p = 0;
    append_file_bytes(IMAGE_TASK_TRACE_LOG, line, (DWORD)(p - line));
}

static void media_log_image_d2a250_event(const char *event, int64_t queue_mgr,
                                         int64_t key_ptr, int64_t pair,
                                         int64_t retaddr, int64_t ret) {
    char line[8192];
    char *p = line;
    char *end = line + sizeof(line) - 1;
    media_log_image_trace_prefix(&p, end, event, retaddr);
    media_append_queue_summary(&p, end, "queue_mgr", queue_mgr);
    media_append_key_ptr_summary(&p, end, "key_ptr", key_ptr);
    media_append_out_pair_summary(&p, end, "pair", pair);
    media_append_hex_field(&p, end, "ret", ret);
    append_raw(&p, end, "}\r\n");
    *p = 0;
    append_file_bytes(IMAGE_TASK_TRACE_LOG, line, (DWORD)(p - line));
}

static int64_t __fastcall sns_media_item_json_probe_hook(unsigned int *media_item,
                                                        int64_t json_node) {
    FnSnsMediaItemJson orig = (FnSnsMediaItemJson)g_sns_media_item_json_hook.trampoline;
    int64_t retaddr = (int64_t)_ReturnAddress();
    int64_t ret = 0;
    if (InterlockedCompareExchange(&g_sns_media_item_json_reentry, 1, 0) == 0) {
        __try {
            sns_media_probe_log_item("sns_media_item_json_enter", media_item, json_node, retaddr);
        }
        __except (EXCEPTION_EXECUTE_HANDLER) {
        }
        InterlockedExchange(&g_sns_media_item_json_reentry, 0);
    }
    if (orig) ret = orig(media_item, json_node);
    return ret;
}

static int64_t __fastcall sns_upload_logic_submit_trace_hook(int64_t upload_param) {
    FnCdnUploadLogicSubmit orig = (FnCdnUploadLogicSubmit)g_sns_upload_logic_submit_hook.trampoline;
    int64_t retaddr = (int64_t)_ReturnAddress();
    int64_t ret = 0;
    __try {
        media_rewrite_sns_video_upload_main_file(upload_param);
        media_rewrite_sns_video_upload_thumb_file(upload_param);
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
    }
    if (InterlockedCompareExchange(&g_sns_upload_trace_reentry, 1, 0) == 0) {
        __try {
            sns_upload_trace_log("sns_upload_logic_submit_enter", 0, upload_param, retaddr, 0);
        }
        __except (EXCEPTION_EXECUTE_HANDLER) {
        }
        InterlockedExchange(&g_sns_upload_trace_reentry, 0);
    }
    if (orig) ret = orig(upload_param);
    if (InterlockedCompareExchange(&g_sns_upload_trace_reentry, 1, 0) == 0) {
        __try {
            sns_upload_trace_log("sns_upload_logic_submit_leave", 0, upload_param, retaddr, ret);
        }
        __except (EXCEPTION_EXECUTE_HANDLER) {
        }
        InterlockedExchange(&g_sns_upload_trace_reentry, 0);
    }
    return ret;
}

static int64_t __fastcall sns_upload_core2_trace_hook(int64_t manager, int64_t upload_param) {
    FnCdnUploadCore2 orig = (FnCdnUploadCore2)g_sns_upload_core2_hook.trampoline;
    int64_t retaddr = (int64_t)_ReturnAddress();
    int64_t ret = 0;
    if (InterlockedCompareExchange(&g_sns_upload_trace_reentry, 1, 0) == 0) {
        __try {
            sns_upload_trace_log("sns_upload_core2_enter", manager, upload_param, retaddr, 0);
        }
        __except (EXCEPTION_EXECUTE_HANDLER) {
        }
        InterlockedExchange(&g_sns_upload_trace_reentry, 0);
    }
    if (orig) ret = orig(manager, upload_param);
    if (InterlockedCompareExchange(&g_sns_upload_trace_reentry, 1, 0) == 0) {
        __try {
            sns_upload_trace_log("sns_upload_core2_leave", manager, upload_param, retaddr, ret);
        }
        __except (EXCEPTION_EXECUTE_HANDLER) {
        }
        InterlockedExchange(&g_sns_upload_trace_reentry, 0);
    }
    return ret;
}

static int64_t __fastcall image_stage_exec_trace_hook(int64_t stage, int64_t ctx) {
    FnImageTaskStageExec orig = (FnImageTaskStageExec)g_image_stage_exec_hook.trampoline;
    int64_t retaddr = (int64_t)_ReturnAddress();
    int64_t ret = 0;
    __try {
        media_log_image_stage_event("image_stage_exec_enter", stage, ctx, retaddr, 0);
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
    }
    if (orig) ret = orig(stage, ctx);
    __try {
        media_log_image_stage_event("image_stage_exec_leave", stage, ctx, retaddr, ret);
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
    }
    return ret;
}

static int64_t __fastcall image_task_body_trace_hook(int64_t task) {
    FnImageTaskBody orig = (FnImageTaskBody)g_image_task_body_hook.trampoline;
    int64_t retaddr = (int64_t)_ReturnAddress();
    int64_t ret = 0;
    __try {
        media_log_image_body_event("image_task_body_enter", task, retaddr, 0);
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
    }
    if (orig) ret = orig(task);
    __try {
        media_log_image_body_event("image_task_body_leave", task, retaddr, ret);
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
    }
    return ret;
}

static int64_t __fastcall image_download_helper_trace_hook(int64_t manager,
                                                          int64_t out_pair,
                                                          int64_t obj,
                                                          unsigned int type,
                                                          unsigned int flags,
                                                          int *opts) {
    FnImageDownloadHelper orig = (FnImageDownloadHelper)g_image_download_helper_hook.trampoline;
    int64_t retaddr = (int64_t)_ReturnAddress();
    int64_t ret = 0;
    __try {
        media_log_image_helper_event("image_download_helper_enter", manager, out_pair, obj,
                                     type, flags, opts, retaddr, 0);
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
    }
    if (orig) ret = orig(manager, out_pair, obj, type, flags, opts);
    __try {
        media_log_image_helper_event("image_download_helper_leave", manager, out_pair, obj,
                                     type, flags, opts, retaddr, ret);
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
    }
    return ret;
}

static int64_t __fastcall image_download_af500_trace_hook(int64_t manager,
                                                          int64_t out_status,
                                                          int64_t key_sso,
                                                          unsigned int type,
                                                          int64_t path_sso,
                                                          int64_t request) {
    FnImageDownloadAf500 orig = (FnImageDownloadAf500)g_image_download_af500_hook.trampoline;
    int64_t retaddr = (int64_t)_ReturnAddress();
    int64_t ret = 0;
    __try {
        media_log_image_af500_event("image_download_af500_enter", manager, out_status,
                                    key_sso, type, path_sso, request, retaddr, 0);
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
    }
    if (orig) ret = orig(manager, out_status, key_sso, type, path_sso, request);
    __try {
        media_log_image_af500_event("image_download_af500_leave", manager, out_status,
                                    key_sso, type, path_sso, request, retaddr, ret);
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
    }
    return ret;
}

static int64_t __fastcall image_download_ac170_trace_hook(int64_t manager,
                                                          int64_t obj,
                                                          unsigned int type) {
    FnImageDownloadAc170 orig = (FnImageDownloadAc170)g_image_download_ac170_hook.trampoline;
    int64_t retaddr = (int64_t)_ReturnAddress();
    int64_t ret = 0;
    __try {
        media_log_image_ac170_event("image_download_ac170_enter", manager, obj, type, retaddr, 0);
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
    }
    if (orig) ret = orig(manager, obj, type);
    __try {
        media_log_image_ac170_event("image_download_ac170_leave", manager, obj, type, retaddr, ret);
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
    }
    return ret;
}

static int64_t __fastcall image_download_ac720_trace_hook(int64_t manager,
                                                          int64_t out_pair,
                                                          int64_t key_sso,
                                                          int64_t path_sso,
                                                          int marker) {
    FnImageDownloadAc720 orig = (FnImageDownloadAc720)g_image_download_ac720_hook.trampoline;
    int64_t retaddr = (int64_t)_ReturnAddress();
    int64_t ret = 0;
    __try {
        media_log_image_ac720_event("image_download_ac720_enter", manager, out_pair,
                                    key_sso, path_sso, marker, retaddr, 0);
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
    }
    if (orig) ret = orig(manager, out_pair, key_sso, path_sso, marker);
    __try {
        media_log_image_ac720_event("image_download_ac720_leave", manager, out_pair,
                                    key_sso, path_sso, marker, retaddr, ret);
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
    }
    return ret;
}

static int64_t __fastcall image_download_queue_submit_trace_hook(int64_t queue_mgr,
                                                                 int64_t flags,
                                                                 int64_t task) {
    FnImageDownloadQueueSubmit orig = (FnImageDownloadQueueSubmit)g_image_download_queue_submit_hook.trampoline;
    int64_t retaddr = (int64_t)_ReturnAddress();
    int64_t ret = 0;
    __try {
        media_log_image_queue_submit_event("image_download_queue_submit_enter",
                                           queue_mgr, flags, task, retaddr, 0);
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
    }
    if (orig) ret = orig(queue_mgr, flags, task);
    __try {
        media_log_image_queue_submit_event("image_download_queue_submit_leave",
                                           queue_mgr, flags, task, retaddr, ret);
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
    }
    return ret;
}

static int64_t __fastcall image_download_b9330_trace_hook(int64_t lookup_req,
                                                          int64_t out_pair) {
    FnImageDownloadB9330 orig = (FnImageDownloadB9330)g_image_download_b9330_hook.trampoline;
    int64_t retaddr = (int64_t)_ReturnAddress();
    int64_t ret = 0;
    __try {
        media_log_image_b9330_event("image_download_b9330_enter",
                                    lookup_req, out_pair, retaddr, 0);
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
    }
    if (orig) ret = orig(lookup_req, out_pair);
    __try {
        media_log_image_b9330_event("image_download_b9330_leave",
                                    lookup_req, out_pair, retaddr, ret);
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
    }
    return ret;
}

static int64_t __fastcall image_download_b9700_trace_hook(int64_t manager,
                                                          int64_t pair,
                                                          int64_t path_sso) {
    FnImageDownloadB9700 orig = (FnImageDownloadB9700)g_image_download_b9700_hook.trampoline;
    int64_t retaddr = (int64_t)_ReturnAddress();
    int64_t ret = 0;
    __try {
        media_log_image_pair_path_event("image_download_b9700_enter",
                                        manager, pair, pair, path_sso, retaddr, 0);
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
    }
    if (orig) ret = orig(manager, pair, path_sso);
    __try {
        media_log_image_pair_path_event("image_download_b9700_leave",
                                        manager, pair, pair, path_sso, retaddr, ret);
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
    }
    return ret;
}

static int64_t __fastcall image_download_b9bf0_trace_hook(int64_t manager,
                                                          int64_t out_pair,
                                                          int64_t pair,
                                                          int64_t path_sso) {
    FnImageDownloadB9BF0 orig = (FnImageDownloadB9BF0)g_image_download_b9bf0_hook.trampoline;
    int64_t retaddr = (int64_t)_ReturnAddress();
    int64_t ret = 0;
    __try {
        media_log_image_pair_path_event("image_download_b9bf0_enter",
                                        manager, out_pair, pair, path_sso, retaddr, 0);
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
    }
    if (orig) ret = orig(manager, out_pair, pair, path_sso);
    __try {
        media_log_image_pair_path_event("image_download_b9bf0_leave",
                                        manager, out_pair, pair, path_sso, retaddr, ret);
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
    }
    return ret;
}

static int64_t __fastcall image_download_d2ed60_trace_hook(int64_t queue_mgr,
                                                           int64_t key_ptr,
                                                           int64_t path_sso,
                                                           int limit) {
    FnImageDownloadD2ED60 orig = (FnImageDownloadD2ED60)g_image_download_d2ed60_hook.trampoline;
    int64_t retaddr = (int64_t)_ReturnAddress();
    int64_t ret = 0;
    __try {
        media_log_image_queue_lookup_event("image_download_d2ed60_enter",
                                           queue_mgr, 0, key_ptr, path_sso, limit,
                                           -1, -1, retaddr, 0);
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
    }
    if (orig) ret = orig(queue_mgr, key_ptr, path_sso, limit);
    __try {
        media_log_image_queue_lookup_event("image_download_d2ed60_leave",
                                           queue_mgr, 0, key_ptr, path_sso, limit,
                                           -1, -1, retaddr, ret);
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
    }
    return ret;
}

static int64_t __fastcall image_download_d2a250_trace_hook(int64_t queue_mgr,
                                                           int64_t key_ptr,
                                                           int64_t pair) {
    FnImageDownloadD2A250 orig = (FnImageDownloadD2A250)g_image_download_d2a250_hook.trampoline;
    int64_t retaddr = (int64_t)_ReturnAddress();
    int64_t ret = 0;
    __try {
        media_log_image_d2a250_event("image_download_d2a250_enter",
                                     queue_mgr, key_ptr, pair, retaddr, 0);
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
    }
    if (orig) ret = orig(queue_mgr, key_ptr, pair);
    __try {
        media_log_image_d2a250_event("image_download_d2a250_leave",
                                     queue_mgr, key_ptr, pair, retaddr, ret);
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
    }
    return ret;
}

static int64_t __fastcall image_download_d32a20_trace_hook(int64_t queue_mgr,
                                                           int64_t out_pair,
                                                           int64_t key_ptr,
                                                           int64_t path_sso,
                                                           char remove_existing,
                                                           char log_detail) {
    FnImageDownloadD32A20 orig = (FnImageDownloadD32A20)g_image_download_d32a20_hook.trampoline;
    int64_t retaddr = (int64_t)_ReturnAddress();
    int64_t ret = 0;
    __try {
        media_log_image_queue_lookup_event("image_download_d32a20_enter",
                                           queue_mgr, out_pair, key_ptr, path_sso, 1000,
                                           remove_existing ? 1 : 0, log_detail ? 1 : 0,
                                           retaddr, 0);
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
    }
    if (orig) ret = orig(queue_mgr, out_pair, key_ptr, path_sso, remove_existing, log_detail);
    __try {
        media_log_image_queue_lookup_event("image_download_d32a20_leave",
                                           queue_mgr, out_pair, key_ptr, path_sso, 1000,
                                           remove_existing ? 1 : 0, log_detail ? 1 : 0,
                                           retaddr, ret);
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
    }
    return ret;
}

static int media_install_inline_hook(MediaInlineHook *state, DWORD rva, const BYTE *expected,
                                     int len, void *hook_fn) {
    int64_t base = (int64_t)wxb_weixin_module();
    BYTE *target;
    if (!base || !state || !expected || len <= 0 || !hook_fn) return 0;
    if (InterlockedCompareExchange(&state->installed, 0, 0) != 0) return 1;
    target = (BYTE *)(base + rva);
    if (!media_bytes_equal(target, expected, len)) return 0;
    copy_bytes(state->orig, target, len);
    state->trampoline = VirtualAlloc(NULL, len + 16, MEM_COMMIT | MEM_RESERVE, PAGE_EXECUTE_READWRITE);
    if (!state->trampoline) return 0;
    media_build_trampoline((BYTE *)state->trampoline, target, state->orig, len);
    if (!media_write_abs_jump(target, len, hook_fn)) {
        VirtualFree(state->trampoline, 0, MEM_RELEASE);
        state->trampoline = NULL;
        return 0;
    }
    state->patch_len = len;
    InterlockedExchange(&state->installed, 1);
    return 1;
}

static void media_uninstall_inline_hook(MediaInlineHook *state, DWORD rva) {
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
}

static void install_image_task_trace_hooks(void) {
    media_install_inline_hook(&g_image_stage_exec_hook,
                              WX_OFF_IMAGE_TASK_STAGE_EXEC,
                              WX_ORIG_IMAGE_STAGE_EXEC_BYTES,
                              (int)sizeof(WX_ORIG_IMAGE_STAGE_EXEC_BYTES),
                              (void *)image_stage_exec_trace_hook);
    media_install_inline_hook(&g_image_task_body_hook,
                              0x16340C0,
                              WX_ORIG_IMAGE_TASK_BODY_BYTES,
                              (int)sizeof(WX_ORIG_IMAGE_TASK_BODY_BYTES),
                              (void *)image_task_body_trace_hook);
    media_install_inline_hook(&g_image_download_helper_hook,
                              WX_OFF_IMAGE_DOWNLOAD_HELPER,
                              WX_ORIG_IMAGE_DOWNLOAD_HELPER_BYTES,
                              (int)sizeof(WX_ORIG_IMAGE_DOWNLOAD_HELPER_BYTES),
                              (void *)image_download_helper_trace_hook);
    media_install_inline_hook(&g_image_download_af500_hook,
                              WX_OFF_IMAGE_DOWNLOAD_AF500,
                              WX_ORIG_IMAGE_DOWNLOAD_AF500_BYTES,
                              (int)sizeof(WX_ORIG_IMAGE_DOWNLOAD_AF500_BYTES),
                              (void *)image_download_af500_trace_hook);
    media_install_inline_hook(&g_image_download_ac170_hook,
                              WX_OFF_IMAGE_DOWNLOAD_AC170,
                              WX_ORIG_IMAGE_DOWNLOAD_AC170_BYTES,
                              (int)sizeof(WX_ORIG_IMAGE_DOWNLOAD_AC170_BYTES),
                              (void *)image_download_ac170_trace_hook);
    media_install_inline_hook(&g_image_download_ac720_hook,
                              WX_OFF_IMAGE_DOWNLOAD_AC720,
                              WX_ORIG_IMAGE_DOWNLOAD_AC720_BYTES,
                              (int)sizeof(WX_ORIG_IMAGE_DOWNLOAD_AC720_BYTES),
                              (void *)image_download_ac720_trace_hook);
    media_install_inline_hook(&g_image_download_queue_submit_hook,
                              WX_OFF_IMAGE_DOWNLOAD_QUEUE_SUBMIT,
                              WX_ORIG_IMAGE_DOWNLOAD_QUEUE_SUBMIT_BYTES,
                              (int)sizeof(WX_ORIG_IMAGE_DOWNLOAD_QUEUE_SUBMIT_BYTES),
                              (void *)image_download_queue_submit_trace_hook);
    media_install_inline_hook(&g_image_download_b9330_hook,
                              WX_OFF_IMAGE_DOWNLOAD_B9330,
                              WX_ORIG_IMAGE_DOWNLOAD_B9330_BYTES,
                              (int)sizeof(WX_ORIG_IMAGE_DOWNLOAD_B9330_BYTES),
                              (void *)image_download_b9330_trace_hook);
    media_install_inline_hook(&g_image_download_b9700_hook,
                              WX_OFF_IMAGE_DOWNLOAD_B9700,
                              WX_ORIG_IMAGE_DOWNLOAD_B9700_BYTES,
                              (int)sizeof(WX_ORIG_IMAGE_DOWNLOAD_B9700_BYTES),
                              (void *)image_download_b9700_trace_hook);
    media_install_inline_hook(&g_image_download_b9bf0_hook,
                              WX_OFF_IMAGE_DOWNLOAD_B9BF0,
                              WX_ORIG_IMAGE_DOWNLOAD_B9BF0_BYTES,
                              (int)sizeof(WX_ORIG_IMAGE_DOWNLOAD_B9BF0_BYTES),
                              (void *)image_download_b9bf0_trace_hook);
    media_install_inline_hook(&g_image_download_d2ed60_hook,
                              WX_OFF_IMAGE_DOWNLOAD_D2ED60,
                              WX_ORIG_IMAGE_DOWNLOAD_D2ED60_BYTES,
                              (int)sizeof(WX_ORIG_IMAGE_DOWNLOAD_D2ED60_BYTES),
                              (void *)image_download_d2ed60_trace_hook);
    media_install_inline_hook(&g_image_download_d2a250_hook,
                              WX_OFF_IMAGE_DOWNLOAD_D2A250,
                              WX_ORIG_IMAGE_DOWNLOAD_D2A250_BYTES,
                              (int)sizeof(WX_ORIG_IMAGE_DOWNLOAD_D2A250_BYTES),
                              (void *)image_download_d2a250_trace_hook);
    media_install_inline_hook(&g_image_download_d32a20_hook,
                              WX_OFF_IMAGE_DOWNLOAD_D32A20,
                              WX_ORIG_IMAGE_DOWNLOAD_D32A20_BYTES,
                              (int)sizeof(WX_ORIG_IMAGE_DOWNLOAD_D32A20_BYTES),
                              (void *)image_download_d32a20_trace_hook);
}

void wxb_uninstall_image_task_trace_hooks(void) {
    media_uninstall_inline_hook(&g_image_download_d32a20_hook, WX_OFF_IMAGE_DOWNLOAD_D32A20);
    media_uninstall_inline_hook(&g_image_download_d2a250_hook, WX_OFF_IMAGE_DOWNLOAD_D2A250);
    media_uninstall_inline_hook(&g_image_download_d2ed60_hook, WX_OFF_IMAGE_DOWNLOAD_D2ED60);
    media_uninstall_inline_hook(&g_image_download_b9bf0_hook, WX_OFF_IMAGE_DOWNLOAD_B9BF0);
    media_uninstall_inline_hook(&g_image_download_b9700_hook, WX_OFF_IMAGE_DOWNLOAD_B9700);
    media_uninstall_inline_hook(&g_image_download_b9330_hook, WX_OFF_IMAGE_DOWNLOAD_B9330);
    media_uninstall_inline_hook(&g_image_download_queue_submit_hook, WX_OFF_IMAGE_DOWNLOAD_QUEUE_SUBMIT);
    media_uninstall_inline_hook(&g_image_download_ac720_hook, WX_OFF_IMAGE_DOWNLOAD_AC720);
    media_uninstall_inline_hook(&g_image_download_ac170_hook, WX_OFF_IMAGE_DOWNLOAD_AC170);
    media_uninstall_inline_hook(&g_image_download_af500_hook, WX_OFF_IMAGE_DOWNLOAD_AF500);
    media_uninstall_inline_hook(&g_image_download_helper_hook, WX_OFF_IMAGE_DOWNLOAD_HELPER);
    media_uninstall_inline_hook(&g_image_task_body_hook, 0x16340C0);
    media_uninstall_inline_hook(&g_image_stage_exec_hook, WX_OFF_IMAGE_TASK_STAGE_EXEC);
}

void wxb_refresh_image_task_trace_hooks(void) {
    if (file_exists(IMAGE_TASK_TRACE_ENABLE_FILE)) install_image_task_trace_hooks();
    else wxb_uninstall_image_task_trace_hooks();
}

static void install_sns_media_probe_hook(void) {
    int was_installed = InterlockedCompareExchange(&g_sns_media_item_json_hook.installed, 0, 0) != 0;
    int ok;
    if (was_installed) return;
    ok = media_install_inline_hook(&g_sns_media_item_json_hook,
                                   WX_OFF_SNS_MEDIA_ITEM_JSON,
                                   WX_ORIG_SNS_MEDIA_ITEM_JSON_BYTES,
                                   (int)sizeof(WX_ORIG_SNS_MEDIA_ITEM_JSON_BYTES),
                                   (void *)sns_media_item_json_probe_hook);
    if (ok) write_status("sns media probe hook installed\n");
    else write_status("sns media probe hook prologue mismatch; hook not installed\n");
}

void wxb_uninstall_sns_media_probe_hook(void) {
    media_uninstall_inline_hook(&g_sns_media_item_json_hook, WX_OFF_SNS_MEDIA_ITEM_JSON);
}

void wxb_refresh_sns_media_probe_hook(void) {
    if (file_exists(SNS_MEDIA_PROBE_ENABLE_FILE)) install_sns_media_probe_hook();
    else wxb_uninstall_sns_media_probe_hook();
}

static void install_sns_upload_trace_hooks(void) {
    int ok1;
    int ok2;
    int logic_was_installed =
        InterlockedCompareExchange(&g_sns_upload_logic_submit_hook.installed, 0, 0) ? 1 : 0;
    int core2_was_installed =
        InterlockedCompareExchange(&g_sns_upload_core2_hook.installed, 0, 0) ? 1 : 0;
    if (logic_was_installed && core2_was_installed) return;
    int logic_match_before = logic_was_installed ? 1 :
        media_hook_expected_match(WX_OFF_CDN_UPLOAD_LOGIC_SUBMIT,
                                  WX_ORIG_CDN_UPLOAD_LOGIC_SUBMIT_BYTES,
                                  (int)sizeof(WX_ORIG_CDN_UPLOAD_LOGIC_SUBMIT_BYTES));
    int core2_match_before = core2_was_installed ? 1 :
        media_hook_expected_match(WX_OFF_CDN_UPLOAD_CORE2,
                                  WX_ORIG_CDN_UPLOAD_CORE2_BYTES,
                                  (int)sizeof(WX_ORIG_CDN_UPLOAD_CORE2_BYTES));
    ok1 = media_install_inline_hook(&g_sns_upload_logic_submit_hook,
                                    WX_OFF_CDN_UPLOAD_LOGIC_SUBMIT,
                                    WX_ORIG_CDN_UPLOAD_LOGIC_SUBMIT_BYTES,
                                    (int)sizeof(WX_ORIG_CDN_UPLOAD_LOGIC_SUBMIT_BYTES),
                                    (void *)sns_upload_logic_submit_trace_hook);
    ok2 = media_install_inline_hook(&g_sns_upload_core2_hook,
                                    WX_OFF_CDN_UPLOAD_CORE2,
                                    WX_ORIG_CDN_UPLOAD_CORE2_BYTES,
                                    (int)sizeof(WX_ORIG_CDN_UPLOAD_CORE2_BYTES),
                                    (void *)sns_upload_core2_trace_hook);
    sns_upload_trace_install_log(ok1,
                                 logic_was_installed,
                                 logic_match_before,
                                 ok2,
                                 core2_was_installed,
                                 core2_match_before);
    if (ok1 || ok2) write_status("sns upload trace hook installed\n");
    else write_status("sns upload trace hook prologue mismatch; hook not installed\n");
}

void wxb_uninstall_sns_upload_trace_hooks(void) {
    media_uninstall_inline_hook(&g_sns_upload_core2_hook, WX_OFF_CDN_UPLOAD_CORE2);
    media_uninstall_inline_hook(&g_sns_upload_logic_submit_hook, WX_OFF_CDN_UPLOAD_LOGIC_SUBMIT);
}

void wxb_install_sns_upload_support_hooks(void) {
    install_sns_upload_trace_hooks();
}

void wxb_refresh_sns_upload_trace_hooks(void) {
    if (file_exists(SNS_UPLOAD_TRACE_ENABLE_FILE)) install_sns_upload_trace_hooks();
    else wxb_uninstall_sns_upload_trace_hooks();
}

static void media_write_probe_result(const char *seq, int ok, const char *message,
                                     const char *msgid, int64_t parsed_msgid,
                                     int dl_type, int mode, int flags, int use_send_item,
                                     int wait_ms, const char *phase,
                                     int64_t base, int64_t manager,
                                     int64_t retained_model, int64_t model_vt,
                                     int model_type, int model_subtype,
                                     int64_t model_svrid,
                                     int64_t obj_arg, int64_t send_item,
                                     int64_t send_item_vt, int send_item_type,
                                     int send_item_subtype, int64_t send_item_svrid,
                                     int64_t helper_ret,
                                     int64_t out0, int64_t out8,
                                     int64_t out10, int64_t out18,
                                     DWORD seh_code, int64_t seh_addr,
                                     int cleanup_errors) {
    if (!seq || !*seq) return;
    WCHAR path[1024];
    char json[8192];
    char *p = json;
    char *end = json + sizeof(json) - 1;
    wsprintfW(path, L"%s\\result_%S.json", CONTROL_DIR, seq);

    append_raw(&p, end, "{\"ok\":");
    append_raw(&p, end, ok ? "true" : "false");
    append_raw(&p, end, ",\"seq\":\"");
    append_escaped(&p, end, seq);
    append_raw(&p, end, "\",\"command\":\"download_image_wechat_probe\",\"message\":\"");
    append_escaped(&p, end, message ? message : "");
    append_raw(&p, end, "\",\"msgId\":\"");
    append_escaped(&p, end, msgid ? msgid : "");
    append_raw(&p, end, "\"");
    media_append_i64_field(&p, end, "parsed_msgid", parsed_msgid);
    media_append_int_field(&p, end, "dl_type", dl_type);
    media_append_int_field(&p, end, "mode", mode);
    media_append_hex_field(&p, end, "flags", (int64_t)(unsigned int)flags);
    media_append_int_field(&p, end, "use_send_item", use_send_item);
    media_append_int_field(&p, end, "wait_ms", wait_ms);
    append_raw(&p, end, ",\"phase\":\"");
    append_escaped(&p, end, phase ? phase : "");
    append_raw(&p, end, "\"");
    media_append_hex_field(&p, end, "base", base);
    media_append_hex_field(&p, end, "manager", manager);
    media_append_hex_field(&p, end, "retained_model", retained_model);
    media_append_hex_field(&p, end, "model_vt", model_vt);
    media_append_int_field(&p, end, "model_type", model_type);
    media_append_int_field(&p, end, "model_subtype", model_subtype);
    media_append_hex_field(&p, end, "model_svrid", model_svrid);
    media_append_hex_field(&p, end, "obj_arg", obj_arg);
    media_append_hex_field(&p, end, "send_item", send_item);
    media_append_hex_field(&p, end, "send_item_vt", send_item_vt);
    media_append_int_field(&p, end, "send_item_type", send_item_type);
    media_append_int_field(&p, end, "send_item_subtype", send_item_subtype);
    media_append_hex_field(&p, end, "send_item_svrid", send_item_svrid);
    media_append_hex_field(&p, end, "helper_ret", helper_ret);
    media_append_hex_field(&p, end, "out0", out0);
    media_append_hex_field(&p, end, "out8", out8);
    media_append_hex_field(&p, end, "out10", out10);
    media_append_hex_field(&p, end, "out18", out18);
    media_append_hex_field(&p, end, "seh_code", (int64_t)seh_code);
    media_append_hex_field(&p, end, "seh_addr", seh_addr);
    media_append_int_field(&p, end, "cleanup_errors", cleanup_errors);
    media_append_bool_field(&p, end, "native_call_invoked", helper_ret != 0);
    append_raw(&p, end, "}");
    *p = 0;
    write_file_bytes(path, json, (DWORD)(p - json));
}

static void media_write_task_probe_result(const char *seq, int ok, const char *message,
                                          const char *msgid, int64_t parsed_msgid,
                                          int wait_ms, int route_source,
                                          const char *phase,
                                          int64_t base,
                                          int64_t retained_model, int64_t model_vt,
                                          int model_type, int model_subtype,
                                          int64_t model_svrid,
                                          int64_t send_item, int64_t send_item_vt,
                                          int send_item_type, int send_item_subtype,
                                          int64_t send_item_svrid,
                                          int64_t stage_obj, int64_t stage_vt,
                                          int64_t target_obj, int64_t target_ctl,
                                          int64_t task_ret,
                                          DWORD seh_code, int64_t seh_addr,
                                          int called_task, int cleanup_errors) {
    if (!seq || !*seq) return;
    WCHAR path[1024];
    char json[8192];
    char *p = json;
    char *end = json + sizeof(json) - 1;
    wsprintfW(path, L"%s\\result_%S.json", CONTROL_DIR, seq);

    append_raw(&p, end, "{\"ok\":");
    append_raw(&p, end, ok ? "true" : "false");
    append_raw(&p, end, ",\"seq\":\"");
    append_escaped(&p, end, seq);
    append_raw(&p, end, "\",\"command\":\"download_image_task_probe\",\"message\":\"");
    append_escaped(&p, end, message ? message : "");
    append_raw(&p, end, "\",\"msgId\":\"");
    append_escaped(&p, end, msgid ? msgid : "");
    append_raw(&p, end, "\"");
    media_append_i64_field(&p, end, "parsed_msgid", parsed_msgid);
    media_append_int_field(&p, end, "wait_ms", wait_ms);
    media_append_int_field(&p, end, "route_source", route_source);
    append_raw(&p, end, ",\"phase\":\"");
    append_escaped(&p, end, phase ? phase : "");
    append_raw(&p, end, "\"");
    media_append_hex_field(&p, end, "base", base);
    media_append_hex_field(&p, end, "retained_model", retained_model);
    media_append_hex_field(&p, end, "model_vt", model_vt);
    media_append_int_field(&p, end, "model_type", model_type);
    media_append_int_field(&p, end, "model_subtype", model_subtype);
    media_append_hex_field(&p, end, "model_svrid", model_svrid);
    media_append_hex_field(&p, end, "send_item", send_item);
    media_append_hex_field(&p, end, "send_item_vt", send_item_vt);
    media_append_int_field(&p, end, "send_item_type", send_item_type);
    media_append_int_field(&p, end, "send_item_subtype", send_item_subtype);
    media_append_hex_field(&p, end, "send_item_svrid", send_item_svrid);
    media_append_hex_field(&p, end, "stage_obj", stage_obj);
    media_append_hex_field(&p, end, "stage_vt", stage_vt);
    media_append_hex_field(&p, end, "target_obj", target_obj);
    media_append_hex_field(&p, end, "target_ctl", target_ctl);
    media_append_hex_field(&p, end, "task_ret", task_ret);
    media_append_hex_field(&p, end, "seh_code", (int64_t)seh_code);
    media_append_hex_field(&p, end, "seh_addr", seh_addr);
    media_append_int_field(&p, end, "cleanup_errors", cleanup_errors);
    media_append_bool_field(&p, end, "native_call_invoked", called_task);
    append_raw(&p, end, "}");
    *p = 0;
    write_file_bytes(path, json, (DWORD)(p - json));
}

static void media_write_sns_url_result(const char *seq, int ok, const char *message,
                                       const char *url, const char *suffix,
                                       const char *local_path, int wait_ms,
                                       int called, int exists, int64_t file_size,
                                       const char *head_hex,
                                       DWORD seh_code, int64_t seh_addr) {
    if (!seq || !*seq) return;
    WCHAR path[1024];
    char json[16384];
    char *p = json;
    char *end = json + sizeof(json) - 1;
    wsprintfW(path, L"%s\\result_%S.json", CONTROL_DIR, seq);

    append_raw(&p, end, "{\"ok\":");
    append_raw(&p, end, ok ? "true" : "false");
    append_raw(&p, end, ",\"seq\":\"");
    append_escaped(&p, end, seq);
    append_raw(&p, end, "\",\"command\":\"download_sns_media_url\",\"message\":\"");
    append_escaped(&p, end, message ? message : "");
    append_raw(&p, end, "\",\"url\":\"");
    append_escaped(&p, end, url ? url : "");
    append_raw(&p, end, "\",\"suffix\":\"");
    append_escaped(&p, end, suffix ? suffix : "");
    append_raw(&p, end, "\",\"localPath\":\"");
    append_escaped(&p, end, local_path ? local_path : "");
    append_raw(&p, end, "\"");
    media_append_int_field(&p, end, "wait_ms", wait_ms);
    media_append_bool_field(&p, end, "native_call_invoked", called);
    media_append_bool_field(&p, end, "exists", exists);
    media_append_i64_field(&p, end, "fileSize", file_size);
    append_raw(&p, end, ",\"headHex\":\"");
    append_escaped(&p, end, head_hex ? head_hex : "");
    append_raw(&p, end, "\"");
    media_append_hex_field(&p, end, "seh_code", (int64_t)seh_code);
    media_append_hex_field(&p, end, "seh_addr", seh_addr);
    append_raw(&p, end, "}");
    *p = 0;
    write_file_bytes(path, json, (DWORD)(p - json));
}

static void media_write_sns_task_result(const char *seq, int ok, const char *message,
                                        const char *url, const char *filekey,
                                        const char *md5, const char *savepath,
                                        int64_t total_size, int wait_ms,
                                        int called, int64_t submit_ret,
                                        int exists, int64_t file_size,
                                        const char *head_hex,
                                        DWORD seh_code, int64_t seh_addr) {
    if (!seq || !*seq) return;
    WCHAR path[1024];
    char json[20000];
    char *p = json;
    char *end = json + sizeof(json) - 1;
    wsprintfW(path, L"%s\\result_%S.json", CONTROL_DIR, seq);

    append_raw(&p, end, "{\"ok\":");
    append_raw(&p, end, ok ? "true" : "false");
    append_raw(&p, end, ",\"seq\":\"");
    append_escaped(&p, end, seq);
    append_raw(&p, end, "\",\"command\":\"download_sns_media_task\",\"message\":\"");
    append_escaped(&p, end, message ? message : "");
    append_raw(&p, end, "\",\"url\":\"");
    append_escaped(&p, end, url ? url : "");
    append_raw(&p, end, "\",\"filekey\":\"");
    append_escaped(&p, end, filekey ? filekey : "");
    append_raw(&p, end, "\",\"md5\":\"");
    append_escaped(&p, end, md5 ? md5 : "");
    append_raw(&p, end, "\",\"savePath\":\"");
    append_escaped(&p, end, savepath ? savepath : "");
    append_raw(&p, end, "\"");
    media_append_i64_field(&p, end, "totalSize", total_size);
    media_append_int_field(&p, end, "wait_ms", wait_ms);
    media_append_bool_field(&p, end, "native_call_invoked", called);
    media_append_i64_field(&p, end, "submitRet", submit_ret);
    media_append_bool_field(&p, end, "exists", exists);
    media_append_i64_field(&p, end, "fileSize", file_size);
    append_raw(&p, end, ",\"headHex\":\"");
    append_escaped(&p, end, head_hex ? head_hex : "");
    append_raw(&p, end, "\"");
    media_append_hex_field(&p, end, "seh_code", (int64_t)seh_code);
    media_append_hex_field(&p, end, "seh_addr", seh_addr);
    append_raw(&p, end, "}");
    *p = 0;
    write_file_bytes(path, json, (DWORD)(p - json));
}

static void media_write_sns_full_task_result(const char *seq, int ok, const char *message,
                                             const char *url, const char *host,
                                             const char *filekey,
                                             const char *md5, const char *savepath,
                                             int64_t total_size, int task_type,
                                             int wait_ms, int aeskey_len,
                                             int factory_called, int enqueue_called,
                                             int64_t manager_handle, int64_t cdn_manager,
                                             int64_t cdn_core, int64_t cdn_core_ctl,
                                             int64_t task_manager, int64_t task_manager_ctl,
                                             int64_t root_context, int64_t created_task,
                                             int64_t created_ctl, int64_t factory_ret,
                                             int64_t enqueue_ret, int exists,
                                             int64_t file_size, const char *head_hex,
                                             DWORD seh_code, int64_t seh_addr) {
    if (!seq || !*seq) return;
    WCHAR path[1024];
    char json[24000];
    char *p = json;
    char *end = json + sizeof(json) - 1;
    wsprintfW(path, L"%s\\result_%S.json", CONTROL_DIR, seq);

    append_raw(&p, end, "{\"ok\":");
    append_raw(&p, end, ok ? "true" : "false");
    append_raw(&p, end, ",\"seq\":\"");
    append_escaped(&p, end, seq);
    append_raw(&p, end, "\",\"command\":\"download_sns_media_full_task\",\"message\":\"");
    append_escaped(&p, end, message ? message : "");
    append_raw(&p, end, "\",\"url\":\"");
    append_escaped(&p, end, url ? url : "");
    append_raw(&p, end, "\",\"host\":\"");
    append_escaped(&p, end, host ? host : "");
    append_raw(&p, end, "\",\"filekey\":\"");
    append_escaped(&p, end, filekey ? filekey : "");
    append_raw(&p, end, "\",\"md5\":\"");
    append_escaped(&p, end, md5 ? md5 : "");
    append_raw(&p, end, "\",\"savePath\":\"");
    append_escaped(&p, end, savepath ? savepath : "");
    append_raw(&p, end, "\"");
    media_append_i64_field(&p, end, "totalSize", total_size);
    media_append_int_field(&p, end, "taskType", task_type);
    media_append_int_field(&p, end, "wait_ms", wait_ms);
    media_append_bool_field(&p, end, "aeskeyPresent", aeskey_len > 0);
    media_append_int_field(&p, end, "aeskeyLen", aeskey_len);
    media_append_bool_field(&p, end, "native_call_invoked", factory_called);
    media_append_bool_field(&p, end, "factoryCalled", factory_called);
    media_append_bool_field(&p, end, "enqueueCalled", enqueue_called);
    media_append_hex_field(&p, end, "managerHandle", manager_handle);
    media_append_hex_field(&p, end, "cdnManager", cdn_manager);
    media_append_hex_field(&p, end, "cdnCore", cdn_core);
    media_append_hex_field(&p, end, "cdnCoreCtl", cdn_core_ctl);
    media_append_hex_field(&p, end, "taskManager", task_manager);
    media_append_hex_field(&p, end, "taskManagerCtl", task_manager_ctl);
    media_append_hex_field(&p, end, "rootContext", root_context);
    media_append_hex_field(&p, end, "createdTask", created_task);
    media_append_hex_field(&p, end, "createdCtl", created_ctl);
    media_append_i64_field(&p, end, "factoryRet", factory_ret);
    media_append_i64_field(&p, end, "enqueueRet", enqueue_ret);
    media_append_bool_field(&p, end, "exists", exists);
    media_append_i64_field(&p, end, "fileSize", file_size);
    append_raw(&p, end, ",\"headHex\":\"");
    append_escaped(&p, end, head_hex ? head_hex : "");
    append_raw(&p, end, "\"");
    media_append_hex_field(&p, end, "seh_code", (int64_t)seh_code);
    media_append_hex_field(&p, end, "seh_addr", seh_addr);
    append_raw(&p, end, "}");
    *p = 0;
    write_file_bytes(path, json, (DWORD)(p - json));
}

static int media_download_sns_media_url_direct_to_result(const char *seq,
                                                         const char *url,
                                                         const char *suffix,
                                                         int wait_ms) {
    const SIZE_T arena_size = 0x20000;
    BYTE *arena = NULL;
    BYTE *pool = NULL;
    BYTE *pool_end = NULL;
    int64_t base = (int64_t)wxb_weixin_module();
    int64_t out_path = 0;
    int64_t url_string = 0;
    int64_t suffix_string = 0;
    char local_path[2048];
    char head_hex[64];
    int exists = 0;
    int64_t file_size = 0;
    int called = 0;
    DWORD seh_code = 0;
    int64_t seh_addr = 0;
    const char *message = "not_started";

    local_path[0] = 0;
    head_hex[0] = 0;
    if (!base) {
        media_write_sns_url_result(seq, 0, "weixin_module_not_found", url, suffix, "",
                                   wait_ms, 0, 0, 0, "", 0, 0);
        return 0;
    }
    if (!url || !*url) {
        media_write_sns_url_result(seq, 0, "missing_url", url, suffix, "",
                                   wait_ms, 0, 0, 0, "", 0, 0);
        return 0;
    }
    if (wait_ms < 0) wait_ms = 0;
    if (wait_ms > 60000) wait_ms = 60000;
    if (!suffix) suffix = "";

    arena = (BYTE *)VirtualAlloc(NULL, arena_size, MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE);
    if (!arena) {
        media_write_sns_url_result(seq, 0, "arena_alloc_failed", url, suffix, "",
                                   wait_ms, 0, 0, 0, "", 0, 0);
        return 0;
    }
    ZeroMemory(arena, arena_size);
    out_path = (int64_t)(arena + 0x0100);
    url_string = (int64_t)(arena + 0x0140);
    suffix_string = (int64_t)(arena + 0x0180);
    pool = arena + 0x1000;
    pool_end = arena + arena_size;

    __try {
        wx_set_std_string_arena(url_string, url, -1, &pool, pool_end);
        wx_set_std_string_arena(suffix_string, suffix, -1, &pool, pool_end);
        ((FnSnsDownloadImageUrl)(base + WX_OFF_SNS_DOWNLOAD_IMAGE_URL))(
            (void *)out_path,
            (void *)url_string,
            (void *)suffix_string);
        called = 1;
        if (!media_read_sso_wstr_utf8(out_path, local_path, sizeof(local_path)) &&
            !wx_read_sso_str(out_path, local_path, sizeof(local_path))) {
            wx_read_str(out_path, local_path, sizeof(local_path));
        }
        message = local_path[0] ? "sns_download_image_url_invoked" : "sns_download_image_url_empty_path";
    }
    __except (media_probe_exception_filter(GetExceptionInformation(), &seh_code, &seh_addr)) {
        message = "sns_download_image_url_seh";
    }

    if (called && local_path[0] && wait_ms > 0) {
        int last_size = -1;
        int stable = 0;
        int loops = wait_ms / 200;
        if (loops < 1) loops = 1;
        for (int i = 0; i < loops; i++) {
            exists = media_probe_file_utf8(local_path, &file_size, head_hex, sizeof(head_hex));
            if (exists && file_size > 0) {
                if ((int)file_size == last_size) {
                    stable++;
                    if (stable >= 3) break;
                } else {
                    stable = 0;
                    last_size = (int)file_size;
                }
            }
            Sleep(200);
        }
    }
    if (called && local_path[0]) {
        exists = media_probe_file_utf8(local_path, &file_size, head_hex, sizeof(head_hex));
    }

    media_write_sns_url_result(seq,
                               called && !seh_code && local_path[0],
                               message,
                               url,
                               suffix,
                               local_path,
                               wait_ms,
                               called,
                               exists,
                               file_size,
                               head_hex,
                               seh_code,
                               seh_addr);
    retain_arena(arena);
    return called && !seh_code && local_path[0];
}

static int media_download_sns_media_task_direct_to_result(const char *seq,
                                                          const char *url,
                                                          const char *filekey,
                                                          const char *md5,
                                                          const char *savepath,
                                                          int64_t total_size,
                                                          int wait_ms) {
    const SIZE_T arena_size = 0x30000;
    BYTE *arena = NULL;
    BYTE *pool = NULL;
    BYTE *pool_end = NULL;
    int64_t base = (int64_t)wxb_weixin_module();
    int64_t st = 0;
    int64_t submit_ret = -90003;
    int called = 0;
    int exists = 0;
    int64_t file_size = 0;
    char head_hex[64];
    DWORD seh_code = 0;
    int64_t seh_addr = 0;
    const char *message = "not_started";
    int ok = 0;

    head_hex[0] = 0;
    if (!base) {
        media_write_sns_task_result(seq, 0, "weixin_module_not_found", url, filekey, md5,
                                    savepath, total_size, wait_ms, 0, 0, 0, 0, "", 0, 0);
        return 0;
    }
    if (!url || !*url || !savepath || !*savepath) {
        media_write_sns_task_result(seq, 0, "missing_url_or_savepath", url, filekey, md5,
                                    savepath, total_size, wait_ms, 0, 0, 0, 0, "", 0, 0);
        return 0;
    }
    if (!filekey || !*filekey) filekey = md5 && *md5 ? md5 : "sns_media";
    if (!md5) md5 = "";
    if (wait_ms < 0) wait_ms = 0;
    if (wait_ms > 60000) wait_ms = 60000;

    arena = (BYTE *)VirtualAlloc(NULL, arena_size, MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE);
    if (!arena) {
        media_write_sns_task_result(seq, 0, "arena_alloc_failed", url, filekey, md5,
                                    savepath, total_size, wait_ms, 0, 0, 0, 0, "", 0, 0);
        return 0;
    }
    ZeroMemory(arena, arena_size);
    st = (int64_t)(arena + 0x0000);
    pool = arena + 0x4000;
    pool_end = arena + arena_size;

    __try {
        ((FnCall1)(base + WX_OFF_IMAGE_DL_INIT))(st);

        wx_set_std_string_arena(st + 0x00, filekey, -1, &pool, pool_end);
        wx_set_std_string_arena(st + 0x38, filekey, -1, &pool, pool_end);
        wx_set_std_string_arena(st + 0x50, filekey, -1, &pool, pool_end);
        wx_set_std_string_arena(st + 0x70, md5, -1, &pool, pool_end);
        wx_set_std_string_arena(st + 0x90, "", 0, &pool, pool_end);
        media_set_path_string_acp(st + 0xB0, savepath, &pool, pool_end);
        wx_set_std_string_arena(st + 0x380, url, -1, &pool, pool_end);
        wx_set_std_string_arena(st + 0x508, url, -1, &pool, pool_end);
        wx_set_std_string_arena(st + 0xB90, url, -1, &pool, pool_end);

        w32((void *)(st + 0x0D0), 7);
        w32((void *)(st + 0x0D4), 0);
        w8((void *)(st + 0x0DC), 1);
        if (total_size > 0) w64((void *)(st + 0x198), (uint64_t)total_size);
        w32((void *)(st + 0x30C), 7);
        w32((void *)(st + 0x310), 1);
        w32((void *)(st + 0x3E8), 1);
        w32((void *)(st + 0x4E8), (uint32_t)(total_size > 0 ? total_size : 0));
        w32((void *)(st + 0x4EC), 0);
        w32((void *)(st + 0x654), 1);

        write_file_bytes(SNS_IMAGE_DL_PARAM_FILE, (const char *)st, 0x1200);
        submit_ret = ((FnCall3)(base + WX_OFF_SNS_IMAGE_DL_SUBMIT))(st, 0);
        called = 1;
        message = "sns_image_dl_submit_called";
    }
    __except (media_probe_exception_filter(GetExceptionInformation(), &seh_code, &seh_addr)) {
        message = "sns_image_dl_submit_seh";
    }

    if (called && savepath[0] && wait_ms > 0) {
        int last_size = -1;
        int stable = 0;
        int loops = wait_ms / 200;
        if (loops < 1) loops = 1;
        for (int i = 0; i < loops; i++) {
            exists = media_probe_file_utf8(savepath, &file_size, head_hex, sizeof(head_hex));
            if (exists && file_size > 0) {
                if ((int)file_size == last_size) {
                    stable++;
                    if (stable >= 3) break;
                } else {
                    stable = 0;
                    last_size = (int)file_size;
                }
            }
            Sleep(200);
        }
    } else if (called && savepath[0]) {
        exists = media_probe_file_utf8(savepath, &file_size, head_hex, sizeof(head_hex));
    }

    ok = called && !seh_code && exists && file_size > 0;
    media_write_sns_task_result(seq, ok, message, url, filekey, md5, savepath,
                                total_size, wait_ms, called, submit_ret, exists,
                                file_size, head_hex, seh_code, seh_addr);
    retain_arena(arena);
    return ok;
}

static int media_release_shared_ctl_safe(int64_t ctl) {
    if (!ctl) return 1;
    __try {
        int64_t vt = r64((void *)ctl);
        if (!InterlockedDecrement((volatile LONG *)(ctl + 8))) {
            ((FnSharedCtlVFunc)r64((void *)vt))(ctl);
            if (!InterlockedDecrement((volatile LONG *)(ctl + 12))) {
                ((FnSharedCtlVFunc)r64((void *)(vt + 8)))(ctl);
            }
        }
        return 1;
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
        return 0;
    }
}

static int media_addref_shared_ctl_safe(int64_t ctl) {
    if (!ctl) return 1;
    __try {
        InterlockedIncrement((volatile LONG *)(ctl + 8));
        return 1;
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
        return 0;
    }
}

static int media_release_shared_pair_safe(int64_t pair_addr) {
    int ok = 1;
    if (!pair_addr) return 1;
    ok = media_release_shared_ctl_safe(r64((void *)(pair_addr + 8)));
    w64((void *)pair_addr, 0);
    w64((void *)(pair_addr + 8), 0);
    return ok;
}

static int media_download_sns_media_full_task_direct_to_result(const char *seq,
                                                               const char *url,
                                                               const char *filekey,
                                                               const char *md5,
                                                               const char *aeskey,
                                                               const char *savepath,
                                                               int64_t total_size,
                                                               int task_type,
                                                               int wait_ms) {
    const SIZE_T arena_size = 0x40000;
    BYTE *arena = NULL;
    BYTE *pool = NULL;
    BYTE *pool_end = NULL;
    int64_t base = (int64_t)wxb_weixin_module();
    int64_t st = 0;
    int64_t out_pair = 0;
    int64_t name_str = 0;
    int64_t mgr_pair = 0;
    int64_t manager_handle = 0;
    int64_t cdn_manager = 0;
    int64_t cdn_core = 0;
    int64_t cdn_core_ctl = 0;
    int64_t task_manager = 0;
    int64_t task_manager_ctl = 0;
    int64_t root_context = 0;
    int64_t created_task = 0;
    int64_t created_ctl = 0;
    int64_t factory_ret = -90003;
    int64_t enqueue_ret = -90003;
    int factory_called = 0;
    int enqueue_called = 0;
    int manager_pair_ref = 0;
    int exists = 0;
    int64_t file_size = 0;
    int aeskey_len = aeskey ? lstrlenA(aeskey) : 0;
    char head_hex[64];
    char host[512];
    DWORD seh_code = 0;
    int64_t seh_addr = 0;
    const char *message = "not_started";
    int ok = 0;

    head_hex[0] = 0;
    host[0] = 0;
    media_extract_url_host(url, host, sizeof(host));
    if (!base) {
        media_write_sns_full_task_result(seq, 0, "weixin_module_not_found", url, host, filekey, md5,
                                         savepath, total_size, task_type, wait_ms, aeskey_len,
                                         0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
                                         factory_ret, enqueue_ret, 0, 0, "", 0, 0);
        return 0;
    }
    if (!url || !*url || !savepath || !*savepath || !aeskey) {
        media_write_sns_full_task_result(seq, 0, "missing_url_or_savepath", url, host, filekey, md5,
                                         savepath, total_size, task_type, wait_ms, aeskey_len,
                                         0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
                                         factory_ret, enqueue_ret, 0, 0, "", 0, 0);
        return 0;
    }
    if (!filekey || !*filekey) filekey = md5 && *md5 ? md5 : "sns_media";
    if (!md5 || !*md5) md5 = filekey;
    if (task_type < 0) task_type = 0;
    if (wait_ms < 0) wait_ms = 0;
    if (wait_ms > 60000) wait_ms = 60000;
    aeskey_len = lstrlenA(aeskey);

    arena = (BYTE *)VirtualAlloc(NULL, arena_size, MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE);
    if (!arena) {
        media_write_sns_full_task_result(seq, 0, "arena_alloc_failed", url, host, filekey, md5,
                                         savepath, total_size, task_type, wait_ms, aeskey_len,
                                         0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
                                         factory_ret, enqueue_ret, 0, 0, "", 0, 0);
        return 0;
    }
    ZeroMemory(arena, arena_size);
    st = (int64_t)(arena + 0x0000);
    out_pair = (int64_t)(arena + 0x2000);
    name_str = (int64_t)(arena + 0x2040);
    mgr_pair = (int64_t)(arena + 0x2080);
    pool = arena + 0x4000;
    pool_end = arena + arena_size;

    __try {
        ((FnCall1)(base + WX_OFF_IMAGE_DL_INIT))(st);

        wx_set_std_string_arena(st + 0x50, filekey, -1, &pool, pool_end);
        wx_set_std_string_arena(st + 0x70, md5, -1, &pool, pool_end);
        wx_set_std_string_arena(st + 0x90, aeskey, -1, &pool, pool_end);
        media_set_path_string_acp(st + 0xB0, savepath, &pool, pool_end);
        wx_set_std_string_arena(st + 0x380, url, -1, &pool, pool_end);
        /* CdnParams+1120 maps to task_params+1320 and ImageData protobuf field 4. */
        wx_set_std_string_arena(st + 0x460, host, -1, &pool, pool_end);

        w32((void *)(st + 0x0D0), 7);
        w32((void *)(st + 0x0D4), 0);
        w32((void *)(st + 0x0D8), 7);
        w8((void *)(st + 0x0DC), 1);
        w32((void *)(st + 0x108), 60);
        w32((void *)(st + 0x10C), 0);
        if (total_size > 0) w64((void *)(st + 0x198), (uint64_t)total_size);
        w32((void *)(st + 0x370), 0);
        w32((void *)(st + 0x3E8), 1);
        w32((void *)(st + 0x4E8), (uint32_t)(total_size > 0 ? total_size : 0));

        wx_set_std_string_arena(name_str, "default", 7, &pool, pool_end);
        manager_handle = ((FnCall1)(base + WX_OFF_CDN_MANAGER_BY_NAME))(name_str);
        if (manager_handle) {
            cdn_manager = ((FnCall1)(base + WX_OFF_CDN_MANAGER_RESOLVE))(manager_handle);
        }
        if (!cdn_manager) {
            message = "cdn_manager_not_found";
        } else {
            cdn_core = r64((void *)(cdn_manager + 72));
            cdn_core_ctl = r64((void *)(cdn_manager + 80));
            if (cdn_core) {
                task_manager = r64((void *)(cdn_core + 24));
                task_manager_ctl = r64((void *)(cdn_core + 32));
            }
            if (task_manager) {
                root_context = r64((void *)(task_manager + 856));
            }
            if (!task_manager || !root_context) {
                message = "cdn_task_context_not_found";
            } else {
                w64((void *)mgr_pair, task_manager);
                w64((void *)(mgr_pair + 8), task_manager_ctl);
                manager_pair_ref = media_addref_shared_ctl_safe(task_manager_ctl);
                if (!manager_pair_ref) {
                    message = "cdn_task_manager_addref_failed";
                } else {
                    write_file_bytes(SNS_FULL_CDN_PARAM_FILE, (const char *)st, 0x1200);
                    factory_ret = ((FnCdnTaskFactoryFull)(base + WX_OFF_CDN_TASK_FACTORY_FULL))(
                        out_pair,
                        root_context,
                        mgr_pair,
                        st,
                        task_type);
                    factory_called = 1;
                    created_task = r64((void *)out_pair);
                    created_ctl = r64((void *)(out_pair + 8));
                    if (created_task) {
                        enqueue_ret = ((FnCall3)(base + WX_OFF_CDN_TASKMANAGER_ENQUEUE))(task_manager, out_pair);
                        enqueue_called = 1;
                        message = "cdn_full_task_enqueued";
                    } else {
                        message = "cdn_full_factory_empty_task";
                    }
                }
            }
        }
    }
    __except (media_probe_exception_filter(GetExceptionInformation(), &seh_code, &seh_addr)) {
        message = "cdn_full_task_seh";
    }

    if (manager_pair_ref) {
        media_release_shared_pair_safe(mgr_pair);
    }

    if (enqueue_called && savepath[0] && wait_ms > 0) {
        int last_size = -1;
        int stable = 0;
        int loops = wait_ms / 200;
        if (loops < 1) loops = 1;
        for (int i = 0; i < loops; i++) {
            exists = media_probe_file_utf8(savepath, &file_size, head_hex, sizeof(head_hex));
            if (exists && file_size > 0) {
                if ((int)file_size == last_size) {
                    stable++;
                    if (stable >= 3) break;
                } else {
                    stable = 0;
                    last_size = (int)file_size;
                }
            }
            Sleep(200);
        }
    } else if (enqueue_called && savepath[0]) {
        exists = media_probe_file_utf8(savepath, &file_size, head_hex, sizeof(head_hex));
    }

    ok = factory_called && enqueue_called && !seh_code && exists && file_size > 0;
    media_write_sns_full_task_result(seq, ok, message, url, host, filekey, md5, savepath,
                                     total_size, task_type, wait_ms, aeskey_len,
                                     factory_called, enqueue_called, manager_handle,
                                     cdn_manager, cdn_core, cdn_core_ctl, task_manager,
                                     task_manager_ctl, root_context, created_task,
                                     created_ctl, factory_ret, enqueue_ret, exists,
                                     file_size, head_hex, seh_code, seh_addr);
    retain_arena(arena);
    return ok;
}

static int media_call1_safe(int64_t fn, int64_t arg) {
    if (!fn || !arg) return 1;
    __try {
        ((FnCall1)fn)(arg);
        return 1;
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
        return 0;
    }
}

static int media_download_image_wechat_probe_direct_to_result(const char *seq, const char *msgid,
                                                              int dl_type, int mode,
                                                              int flags, int use_send_item,
                                                              int wait_ms) {
    const SIZE_T arena_size = 0x4000;
    BYTE *arena = NULL;
    int64_t base = (int64_t)wxb_weixin_module();
    int64_t parsed_msgid = msgid ? a2i64(msgid) : 0;
    int64_t retained_model = 0;
    int64_t model_vt = 0;
    int model_type = 0;
    int model_subtype = 0;
    int64_t model_svrid = 0;
    int64_t manager = 0;
    int64_t obj_arg = 0;
    int64_t send_item = 0;
    int64_t send_item_vt = 0;
    int send_item_type = 0;
    int send_item_subtype = 0;
    int64_t send_item_svrid = 0;
    int64_t helper_ret = 0;
    int64_t out0 = 0;
    int64_t out8 = 0;
    int64_t out10 = 0;
    int64_t out18 = 0;
    int64_t seh_addr = 0;
    DWORD seh_code = 0;
    int cleanup_errors = 0;
    int opts1_inited = 0;
    int opts2_inited = 0;
    int send_item_built = 0;
    int called_helper = 0;
    int ok = 0;
    const char *message = "not_started";
    char phase[64] = "start";
    int64_t root_pair[2] = {0, 0};
    int64_t mid_pair[2] = {0, 0};
    int64_t service_pair[2] = {0, 0};
    BYTE *opts = NULL;
    BYTE *out_pair = NULL;

    if (wait_ms < 0) wait_ms = 0;
    if (wait_ms > 60000) wait_ms = 60000;
    if (!dl_type) dl_type = 0x67;
    if (!mode) mode = 1;
    if (flags < 0) flags = 0x104;

    if (!base) {
        media_write_probe_result(seq, 0, "weixin_module_not_found", msgid, parsed_msgid,
                                 dl_type, mode, flags, use_send_item, wait_ms, phase,
                                 base, manager, retained_model, model_vt, model_type,
                                 model_subtype, model_svrid, obj_arg, send_item,
                                 send_item_vt, send_item_type, send_item_subtype,
                                 send_item_svrid, helper_ret, out0, out8, out10, out18,
                                 seh_code, seh_addr, cleanup_errors);
        return 0;
    }
    if (!parsed_msgid) {
        media_write_probe_result(seq, 0, "missing_or_invalid_msgid", msgid, parsed_msgid,
                                 dl_type, mode, flags, use_send_item, wait_ms, phase,
                                 base, manager, retained_model, model_vt, model_type,
                                 model_subtype, model_svrid, obj_arg, send_item,
                                 send_item_vt, send_item_type, send_item_subtype,
                                 send_item_svrid, helper_ret, out0, out8, out10, out18,
                                 seh_code, seh_addr, cleanup_errors);
        return 0;
    }

    retained_model = (int64_t)find_retained_model(parsed_msgid);
    if (retained_model && is_readable_ptr(retained_model, MODEL_ITEM_BYTES)) {
        model_vt = r64((void *)retained_model);
        model_type = r32((void *)(retained_model + 0x0C));
        model_subtype = r32((void *)(retained_model + 0x10));
        model_svrid = r64((void *)(retained_model + 0x108));
    }
    if (!retained_model || model_vt != base + WX_OFF_SRC_MSG_VT) {
        media_write_probe_result(seq, 0, "retained_model_not_found", msgid, parsed_msgid,
                                 dl_type, mode, flags, use_send_item, wait_ms, phase,
                                 base, manager, retained_model, model_vt, model_type,
                                 model_subtype, model_svrid, obj_arg, send_item,
                                 send_item_vt, send_item_type, send_item_subtype,
                                 send_item_svrid, helper_ret, out0, out8, out10, out18,
                                 seh_code, seh_addr, cleanup_errors);
        return 0;
    }

    arena = (BYTE *)VirtualAlloc(NULL, arena_size, MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE);
    if (!arena) {
        media_write_probe_result(seq, 0, "arena_alloc_failed", msgid, parsed_msgid,
                                 dl_type, mode, flags, use_send_item, wait_ms, phase,
                                 base, manager, retained_model, model_vt, model_type,
                                 model_subtype, model_svrid, obj_arg, send_item,
                                 send_item_vt, send_item_type, send_item_subtype,
                                 send_item_svrid, helper_ret, out0, out8, out10, out18,
                                 seh_code, seh_addr, cleanup_errors);
        return 0;
    }
    ZeroMemory(arena, arena_size);
    send_item = (int64_t)(arena + 0x0100);
    opts = arena + 0x0600;
    out_pair = arena + 0x0700;

    __try {
        lstrcpynA(phase, "resolve_root", sizeof(phase));
        ((FnCall1)(base + WX_OFF_ROOM_INIT1))((int64_t)root_pair);
        if (!root_pair[0]) {
            message = "root_service_init_failed";
        } else {
            lstrcpynA(phase, "resolve_mid", sizeof(phase));
            ((FnCall3)(base + WX_OFF_ROOM_INIT2))(root_pair[0], (int64_t)mid_pair);
            if (!mid_pair[0]) {
                message = "mid_service_init_failed";
            } else {
                lstrcpynA(phase, "resolve_manager", sizeof(phase));
                ((FnCall3)(base + WX_OFF_SEND_TEXT_SERVICE))(mid_pair[0], (int64_t)service_pair);
                manager = service_pair[0];
                if (!manager) {
                    message = "download_manager_resolver_failed";
                } else {
                    lstrcpynA(phase, "init_opts", sizeof(phase));
                    w32(opts, mode);
                    ((FnCall1)(base + WX_OFF_STD_STRING_INIT))((int64_t)(opts + 8));
                    opts1_inited = 1;
                    ((FnCall1)(base + WX_OFF_STD_STRING_INIT))((int64_t)(opts + 40));
                    opts2_inited = 1;

                    if (use_send_item) {
                        lstrcpynA(phase, "model_to_send_item", sizeof(phase));
                        ((FnCall3)(base + WX_OFF_MSG_TO_SEND_ITEM))(retained_model, send_item);
                        send_item_vt = r64((void *)send_item);
                        send_item_type = r32((void *)(send_item + 0x08));
                        send_item_subtype = r32((void *)(send_item + 0x0C));
                        send_item_svrid = r64((void *)(send_item + 0x98));
                        if (send_item_vt != base + WX_OFF_SEND_ITEM_VT) {
                            message = "model_to_send_item_bad_vtable";
                        } else {
                            send_item_built = 1;
                            obj_arg = send_item;
                        }
                    } else {
                        obj_arg = retained_model;
                    }

                    if (obj_arg) {
                        lstrcpynA(phase, "call_1816B11B0", sizeof(phase));
                        helper_ret = ((FnImageDownloadHelper)(base + WX_OFF_IMAGE_DOWNLOAD_HELPER))(
                            manager,
                            (int64_t)out_pair,
                            obj_arg,
                            (unsigned int)dl_type,
                            (unsigned int)flags,
                            (int *)opts);
                        called_helper = 1;
                        out0 = r64((void *)out_pair);
                        out8 = r64((void *)(out_pair + 8));
                        out10 = r64((void *)(out_pair + 0x10));
                        out18 = r64((void *)(out_pair + 0x18));
                        message = "wechat_download_probe_invoked";
                    }
                }
            }
        }
    }
    __except (media_probe_exception_filter(GetExceptionInformation(), &seh_code, &seh_addr)) {
        message = "wechat_download_probe_seh";
    }

    if (called_helper && wait_ms > 0) {
        lstrcpynA(phase, "post_wait", sizeof(phase));
        Sleep((DWORD)wait_ms);
    }

    if (!media_release_shared_pair_safe((int64_t)out_pair)) cleanup_errors++;
    if (send_item_built && !media_call1_safe(base + WX_OFF_SEND_ITEM_ROUTE_DTOR, send_item)) cleanup_errors++;
    if (opts2_inited && !media_call1_safe(base + WX_OFF_STD_STRING_DTOR, (int64_t)(opts + 40))) cleanup_errors++;
    if (opts1_inited && !media_call1_safe(base + WX_OFF_STD_STRING_DTOR, (int64_t)(opts + 8))) cleanup_errors++;
    if (!media_release_shared_pair_safe((int64_t)service_pair)) cleanup_errors++;
    if (!media_release_shared_pair_safe((int64_t)mid_pair)) cleanup_errors++;
    if (!media_release_shared_pair_safe((int64_t)root_pair)) cleanup_errors++;

    if (called_helper && !seh_code) {
        lstrcpynA(phase, cleanup_errors ? "done_cleanup_errors" : "done", sizeof(phase));
    }
    ok = called_helper && !seh_code;

    media_write_probe_result(seq, ok, message, msgid, parsed_msgid,
                             dl_type, mode, flags, use_send_item, wait_ms, phase,
                             base, manager, retained_model, model_vt, model_type,
                             model_subtype, model_svrid, obj_arg, send_item,
                             send_item_vt, send_item_type, send_item_subtype,
                             send_item_svrid, helper_ret, out0, out8, out10, out18,
                             seh_code, seh_addr, cleanup_errors);

    if (called_helper) retain_arena(arena);
    else VirtualFree(arena, 0, MEM_RELEASE);
    return ok;
}

static int media_download_image_task_probe_direct_to_result(const char *seq, const char *msgid,
                                                            int wait_ms,
                                                            const char *content,
                                                            int source_type,
                                                            int source_subtype,
                                                            int source_local_id,
                                                            int32_t source_time,
                                                            const char *source_from,
                                                            const char *source_to,
                                                            const char *source_sender,
                                                            const char *source_msgsource) {
    const SIZE_T arena_size = 0x6000;
    BYTE *arena = NULL;
    int64_t base = (int64_t)wxb_weixin_module();
    int64_t parsed_msgid = msgid ? a2i64(msgid) : 0;
    int64_t retained_model = 0;
    int64_t model_vt = 0;
    int model_type = 0;
    int model_subtype = 0;
    int64_t model_svrid = 0;
    int64_t send_item = 0;
    int64_t send_item_vt = 0;
    int send_item_type = 0;
    int send_item_subtype = 0;
    int64_t send_item_svrid = 0;
    int64_t stage_obj = 0;
    int64_t stage_vt = 0;
    int64_t target_obj = 0;
    int64_t target_ctl = 0;
    int64_t task_ret = 0;
    int64_t seh_addr = 0;
    DWORD seh_code = 0;
    int cleanup_errors = 0;
    int send_item_built = 0;
    int stage_route_built = 0;
    int called_task = 0;
    int ok = 0;
    int route_source = 0; /* 1=model, 2=retained send_item, 3=hydrated send_item */
    const char *message = "not_started";
    char phase[64] = "start";
    int64_t root_ctx = 0;
    int64_t root_pair[2] = {0, 0};
    int64_t mid_pair[2] = {0, 0};
    int64_t target_pair[2] = {0, 0};
    int64_t iface_tag = 0;
    BYTE *context_obj = NULL;
    void *retained_si = NULL;
    int hydrated_retained = 0;

    if (wait_ms < 0) wait_ms = 0;
    if (wait_ms > 60000) wait_ms = 60000;

    if (!base) {
        media_write_task_probe_result(seq, 0, "weixin_module_not_found", msgid, parsed_msgid,
                                      wait_ms, route_source, phase, base, retained_model, model_vt,
                                      model_type, model_subtype, model_svrid, send_item,
                                      send_item_vt, send_item_type, send_item_subtype,
                                      send_item_svrid, stage_obj, stage_vt,
                                      target_obj, target_ctl, task_ret,
                                      seh_code, seh_addr, called_task, cleanup_errors);
        return 0;
    }
    if (!parsed_msgid) {
        media_write_task_probe_result(seq, 0, "missing_or_invalid_msgid", msgid, parsed_msgid,
                                      wait_ms, route_source, phase, base, retained_model, model_vt,
                                      model_type, model_subtype, model_svrid, send_item,
                                      send_item_vt, send_item_type, send_item_subtype,
                                      send_item_svrid, stage_obj, stage_vt,
                                      target_obj, target_ctl, task_ret,
                                      seh_code, seh_addr, called_task, cleanup_errors);
        return 0;
    }

    retained_model = (int64_t)find_retained_model(parsed_msgid);
    if (retained_model && is_readable_ptr(retained_model, MODEL_ITEM_BYTES)) {
        model_vt = r64((void *)retained_model);
        model_type = r32((void *)(retained_model + 0x0C));
        model_subtype = r32((void *)(retained_model + 0x10));
        model_svrid = r64((void *)(retained_model + 0x108));
    }
    arena = (BYTE *)VirtualAlloc(NULL, arena_size, MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE);
    if (!arena) {
        media_write_task_probe_result(seq, 0, "arena_alloc_failed", msgid, parsed_msgid,
                                      wait_ms, route_source, phase, base, retained_model, model_vt,
                                      model_type, model_subtype, model_svrid, send_item,
                                      send_item_vt, send_item_type, send_item_subtype,
                                      send_item_svrid, stage_obj, stage_vt,
                                      target_obj, target_ctl, task_ret,
                                      seh_code, seh_addr, called_task, cleanup_errors);
        return 0;
    }
    ZeroMemory(arena, arena_size);
    send_item = (int64_t)(arena + 0x0100);
    context_obj = arena + 0x0500;
    stage_obj = (int64_t)(arena + 0x1000);

    __try {
        if (retained_model && model_vt == base + WX_OFF_SRC_MSG_VT) {
            lstrcpynA(phase, "model_to_send_item", sizeof(phase));
            ((FnCall3)(base + WX_OFF_MSG_TO_SEND_ITEM))(retained_model, send_item);
            route_source = 1;
        } else {
            lstrcpynA(phase, "find_retained_send_item", sizeof(phase));
            retained_si = find_retained_si(parsed_msgid);
            if (!retained_si && content && content[0]) {
                lstrcpynA(phase, "hydrate_send_item_from_fields", sizeof(phase));
                hydrated_retained = hydrate_retained_send_item_from_fields(parsed_msgid,
                                                                           content,
                                                                           source_type,
                                                                           source_subtype,
                                                                           source_local_id,
                                                                           source_time,
                                                                           source_from ? source_from : "",
                                                                           source_to ? source_to : "",
                                                                           source_sender ? source_sender : "",
                                                                           source_msgsource ? source_msgsource : "");
                if (hydrated_retained) retained_si = find_retained_si(parsed_msgid);
            }
            if (retained_si) {
                lstrcpynA(phase, "copy_retained_send_item", sizeof(phase));
                ((FnCall3)(base + WX_OFF_SEND_ITEM_COPY))(send_item, (int64_t)retained_si);
                route_source = hydrated_retained ? 3 : 2;
            } else {
                message = "retained_model_or_send_item_not_found";
            }
        }
        send_item_vt = r64((void *)send_item);
        send_item_type = r32((void *)(send_item + 0x08));
        send_item_subtype = r32((void *)(send_item + 0x0C));
        send_item_svrid = r64((void *)(send_item + 0x98));
        if (send_item_vt != base + WX_OFF_SEND_ITEM_VT) {
            if (route_source)
                message = "send_item_bad_vtable";
        } else {
            send_item_built = 1;
            lstrcpynA(phase, "resolve_task_target_root", sizeof(phase));
            root_ctx = ((FnNoArgs)(base + WX_OFF_GLOBAL_CONTEXT_ROOT))();
            if (!root_ctx) {
                message = "task_target_root_failed";
            } else {
                lstrcpynA(phase, "resolve_task_target_pair1", sizeof(phase));
                ((FnObjectOutPair)r64((void *)(r64((void *)root_ctx) + 96)))(root_ctx, (int64_t)root_pair);
                if (!root_pair[0]) {
                    message = "task_target_pair1_failed";
                } else {
                    lstrcpynA(phase, "resolve_task_target_pair2", sizeof(phase));
                    ((FnObjectOutPair)r64((void *)(r64((void *)root_pair[0]) + 48)))(root_pair[0], (int64_t)mid_pair);
                    if (!mid_pair[0]) {
                        message = "task_target_pair2_failed";
                    } else {
                        lstrcpynA(phase, "resolve_task_target_cast", sizeof(phase));
                        iface_tag = base + WX_OFF_IMAGE_TASK_TARGET_IFACE;
                        ((FnCall3Args)(base + WX_OFF_CONTEXT_INTERFACE_CAST))(mid_pair[0],
                                                                              (int64_t)target_pair,
                                                                              (int64_t)&iface_tag);
                        target_obj = target_pair[0];
                        target_ctl = target_pair[1];
                        if (!target_obj) {
                            message = "task_target_cast_failed";
                        } else {
                            lstrcpynA(phase, "build_stage_object", sizeof(phase));
                            w64((void *)stage_obj, base + WX_OFF_IMAGE_TASK_STAGE_VT);
                            w64((void *)(stage_obj + 8), target_obj);
                            ((FnCall3)(base + WX_OFF_SEND_ITEM_COPY))(stage_obj + 16, send_item);
                            stage_route_built = 1;
                            stage_vt = r64((void *)stage_obj);

                            lstrcpynA(phase, "call_181633880", sizeof(phase));
                            w64((void *)context_obj, target_obj);
                            w64((void *)(context_obj + 8), target_ctl);
                            called_task = 1;
                            task_ret = ((FnCall3)(base + WX_OFF_IMAGE_TASK_STAGE_EXEC))(stage_obj, (int64_t)context_obj);
                            message = "wechat_image_task_probe_invoked";
                        }
                    }
                }
            }
        }
    }
    __except (media_probe_exception_filter(GetExceptionInformation(), &seh_code, &seh_addr)) {
        message = "wechat_image_task_probe_seh";
    }

    if (called_task && wait_ms > 0) {
        lstrcpynA(phase, "post_wait", sizeof(phase));
        Sleep((DWORD)wait_ms);
    }

    if (stage_route_built && !media_call1_safe(base + WX_OFF_SEND_ITEM_ROUTE_DTOR, stage_obj + 16)) cleanup_errors++;
    if (send_item_built && !media_call1_safe(base + WX_OFF_SEND_ITEM_ROUTE_DTOR, send_item)) cleanup_errors++;
    if (!media_release_shared_pair_safe((int64_t)target_pair)) cleanup_errors++;
    if (!media_release_shared_pair_safe((int64_t)mid_pair)) cleanup_errors++;
    if (!media_release_shared_pair_safe((int64_t)root_pair)) cleanup_errors++;

    if (called_task && !seh_code) {
        lstrcpynA(phase, cleanup_errors ? "done_cleanup_errors" : "done", sizeof(phase));
    }
    ok = called_task && !seh_code;

    media_write_task_probe_result(seq, ok, message, msgid, parsed_msgid,
                                  wait_ms, route_source, phase, base, retained_model, model_vt,
                                  model_type, model_subtype, model_svrid, send_item,
                                  send_item_vt, send_item_type, send_item_subtype,
                                  send_item_svrid, stage_obj, stage_vt,
                                  target_obj, target_ctl, task_ret,
                                  seh_code, seh_addr, called_task, cleanup_errors);

    retain_arena(arena);
    return ok;
}

static int media_build_probe_queue_asm(BYTE *p, int64_t task_addr, int64_t worker_addr) {
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

static void media_download_image_wechat_probe_queue_worker(int64_t task_addr) {
    MediaDownloadProbeTask *task = (MediaDownloadProbeTask *)task_addr;
    int ok;
    if (!task) return;
    if (InterlockedCompareExchange(&task->status, 2, 0) != 0) return;
    ok = media_download_image_wechat_probe_direct_to_result(task->seq,
                                                           task->msgid,
                                                           task->dl_type,
                                                           task->mode,
                                                           task->flags,
                                                           task->use_send_item,
                                                           task->wait_ms);
    task->ok = ok;
    InterlockedExchange(&task->status, ok ? 1 : -1);
}

int wxb_download_image_wechat_probe_to_result(const char *seq, const char *msgid,
                                              int dl_type, int mode,
                                              int flags,
                                              int use_send_item, int wait_ms) {
    const SIZE_T arena_size = 0x3000;
    BYTE *arena;
    BYTE *asm_addr;
    MediaDownloadProbeTask *task;
    int asm_len;
    int timeout_ms;
    int status = 0;

    if (!seq || !*seq) return 0;
    if (!wxb_weixin_module()) {
        write_result(seq, 0, "download_image_wechat_probe", "weixin_module_not_found");
        return 0;
    }

    arena = (BYTE *)VirtualAlloc(NULL, arena_size, MEM_COMMIT | MEM_RESERVE, PAGE_EXECUTE_READWRITE);
    if (!arena) {
        write_result(seq, 0, "download_image_wechat_probe", "queue_arena_alloc_failed");
        return 0;
    }
    ZeroMemory(arena, arena_size);
    asm_addr = arena + 0x0200;
    task = (MediaDownloadProbeTask *)(arena + 0x1000);
    task->status = 0;
    task->dl_type = dl_type;
    task->mode = mode;
    task->flags = flags;
    task->use_send_item = use_send_item;
    task->wait_ms = wait_ms;
    safe_copy(task->seq, sizeof(task->seq), seq);
    safe_copy(task->msgid, sizeof(task->msgid), msgid ? msgid : "");

    asm_len = media_build_probe_queue_asm(asm_addr,
                                          (int64_t)task,
                                          (int64_t)media_download_image_wechat_probe_queue_worker);
    FlushInstructionCache(GetCurrentProcess(), asm_addr, asm_len);
    queue_send((int64_t)asm_addr);

    timeout_ms = wait_ms + 10000;
    if (timeout_ms < 5000) timeout_ms = 5000;
    if (timeout_ms > 30000) timeout_ms = 30000;
    for (int i = 0; i < timeout_ms / 50; i++) {
        status = InterlockedCompareExchange(&task->status, 0, 0);
        if (status == 1 || status < 0) break;
        Sleep(50);
    }
    status = InterlockedCompareExchange(&task->status, 0, 0);
    if (status == 0) {
        InterlockedCompareExchange(&task->status, -2, 0);
        write_result(seq, 0, "download_image_wechat_probe", "queue_worker_not_started");
    } else if (status == 2) {
        write_result(seq, 0, "download_image_wechat_probe", "queue_worker_timeout");
    }

    retain_arena(arena);
    return status == 1;
}

static void media_download_image_task_probe_queue_worker(int64_t task_addr) {
    MediaDownloadTaskProbeTask *task = (MediaDownloadTaskProbeTask *)task_addr;
    int ok;
    if (!task) return;
    if (InterlockedCompareExchange(&task->status, 2, 0) != 0) return;
    ok = media_download_image_task_probe_direct_to_result(task->seq,
                                                         task->msgid,
                                                         task->wait_ms,
                                                          task->content,
                                                          task->source_type,
                                                          task->source_subtype,
                                                          task->source_local_id,
                                                          task->source_time,
                                                          task->source_from,
                                                          task->source_to,
                                                          task->source_sender,
                                                          task->source_msgsource);
    task->ok = ok;
    InterlockedExchange(&task->status, ok ? 1 : -1);
}

int wxb_download_image_task_probe_to_result(const char *seq, const char *msgid,
                                            int wait_ms,
                                            const char *content,
                                            int source_type,
                                            int source_subtype,
                                            int source_local_id,
                                            int32_t source_time,
                                            const char *source_from,
                                            const char *source_to,
                                            const char *source_sender,
                                            const char *source_msgsource) {
    const SIZE_T arena_size = 0x8000;
    BYTE *arena;
    BYTE *asm_addr;
    MediaDownloadTaskProbeTask *task;
    int asm_len;
    int timeout_ms;
    int status = 0;

    if (!seq || !*seq) return 0;
    if (!wxb_weixin_module()) {
        write_result(seq, 0, "download_image_task_probe", "weixin_module_not_found");
        return 0;
    }

    arena = (BYTE *)VirtualAlloc(NULL, arena_size, MEM_COMMIT | MEM_RESERVE, PAGE_EXECUTE_READWRITE);
    if (!arena) {
        write_result(seq, 0, "download_image_task_probe", "queue_arena_alloc_failed");
        return 0;
    }
    ZeroMemory(arena, arena_size);
    asm_addr = arena + 0x0200;
    task = (MediaDownloadTaskProbeTask *)(arena + 0x1000);
    task->status = 0;
    task->wait_ms = wait_ms;
    task->source_type = source_type;
    task->source_subtype = source_subtype;
    task->source_local_id = source_local_id;
    task->source_time = source_time;
    safe_copy(task->seq, sizeof(task->seq), seq);
    safe_copy(task->msgid, sizeof(task->msgid), msgid ? msgid : "");
    safe_copy(task->content, sizeof(task->content), content ? content : "");
    safe_copy(task->source_from, sizeof(task->source_from), source_from ? source_from : "");
    safe_copy(task->source_to, sizeof(task->source_to), source_to ? source_to : "");
    safe_copy(task->source_sender, sizeof(task->source_sender), source_sender ? source_sender : "");
    safe_copy(task->source_msgsource, sizeof(task->source_msgsource), source_msgsource ? source_msgsource : "");

    asm_len = media_build_probe_queue_asm(asm_addr,
                                          (int64_t)task,
                                          (int64_t)media_download_image_task_probe_queue_worker);
    FlushInstructionCache(GetCurrentProcess(), asm_addr, asm_len);
    queue_send((int64_t)asm_addr);

    timeout_ms = wait_ms + 10000;
    if (timeout_ms < 5000) timeout_ms = 5000;
    if (timeout_ms > 30000) timeout_ms = 30000;
    for (int i = 0; i < timeout_ms / 50; i++) {
        status = InterlockedCompareExchange(&task->status, 0, 0);
        if (status == 1 || status < 0) break;
        Sleep(50);
    }
    status = InterlockedCompareExchange(&task->status, 0, 0);
    if (status == 0) {
        InterlockedCompareExchange(&task->status, -2, 0);
        write_result(seq, 0, "download_image_task_probe", "queue_worker_not_started");
    } else if (status == 2) {
        write_result(seq, 0, "download_image_task_probe", "queue_worker_timeout");
    }

    retain_arena(arena);
    return status == 1;
}

static void media_download_sns_media_url_queue_worker(int64_t task_addr) {
    SnsMediaUrlDownloadTask *task = (SnsMediaUrlDownloadTask *)task_addr;
    int ok;
    if (!task) return;
    if (InterlockedCompareExchange(&task->status, 2, 0) != 0) return;
    ok = media_download_sns_media_url_direct_to_result(task->seq,
                                                       task->url,
                                                       task->suffix,
                                                       task->wait_ms);
    task->ok = ok;
    InterlockedExchange(&task->status, ok ? 1 : -1);
}

static void media_download_sns_media_task_queue_worker(int64_t task_addr) {
    SnsMediaTaskDownloadTask *task = (SnsMediaTaskDownloadTask *)task_addr;
    int ok;
    if (!task) return;
    if (InterlockedCompareExchange(&task->status, 2, 0) != 0) return;
    ok = media_download_sns_media_task_direct_to_result(task->seq,
                                                        task->url,
                                                        task->filekey,
                                                        task->md5,
                                                        task->savepath,
                                                        task->total_size,
                                                        task->wait_ms);
    task->ok = ok;
    InterlockedExchange(&task->status, ok ? 1 : -1);
}

static void media_download_sns_media_full_task_queue_worker(int64_t task_addr) {
    SnsMediaFullTaskDownloadTask *task = (SnsMediaFullTaskDownloadTask *)task_addr;
    int ok;
    if (!task) return;
    if (InterlockedCompareExchange(&task->status, 2, 0) != 0) return;
    ok = media_download_sns_media_full_task_direct_to_result(task->seq,
                                                             task->url,
                                                             task->filekey,
                                                             task->md5,
                                                             task->aeskey,
                                                             task->savepath,
                                                             task->total_size,
                                                             task->task_type,
                                                             task->wait_ms);
    task->ok = ok;
    InterlockedExchange(&task->status, ok ? 1 : -1);
}

int wxb_download_sns_media_url_to_result(const char *seq, const char *url,
                                         const char *suffix, int wait_ms) {
    const SIZE_T arena_size = 0x5000;
    BYTE *arena;
    BYTE *asm_addr;
    SnsMediaUrlDownloadTask *task;
    int asm_len;
    int timeout_ms;
    int status = 0;

    if (!seq || !*seq) return 0;
    if (!wxb_weixin_module()) {
        write_result(seq, 0, "download_sns_media_url", "weixin_module_not_found");
        return 0;
    }
    if (!url || !*url) {
        write_result(seq, 0, "download_sns_media_url", "missing_url");
        return 0;
    }
    install_sns_media_decrypt_capture_hooks();

    arena = (BYTE *)VirtualAlloc(NULL, arena_size, MEM_COMMIT | MEM_RESERVE, PAGE_EXECUTE_READWRITE);
    if (!arena) {
        write_result(seq, 0, "download_sns_media_url", "queue_arena_alloc_failed");
        return 0;
    }
    ZeroMemory(arena, arena_size);
    asm_addr = arena + 0x0200;
    task = (SnsMediaUrlDownloadTask *)(arena + 0x1000);
    task->status = 0;
    task->wait_ms = wait_ms;
    safe_copy(task->seq, sizeof(task->seq), seq);
    safe_copy(task->url, sizeof(task->url), url ? url : "");
    safe_copy(task->suffix, sizeof(task->suffix), suffix ? suffix : "");

    asm_len = media_build_probe_queue_asm(asm_addr,
                                          (int64_t)task,
                                          (int64_t)media_download_sns_media_url_queue_worker);
    FlushInstructionCache(GetCurrentProcess(), asm_addr, asm_len);
    queue_send((int64_t)asm_addr);

    timeout_ms = wait_ms + 10000;
    if (timeout_ms < 5000) timeout_ms = 5000;
    if (timeout_ms > 70000) timeout_ms = 70000;
    for (int i = 0; i < timeout_ms / 50; i++) {
        status = InterlockedCompareExchange(&task->status, 0, 0);
        if (status == 1 || status < 0) break;
        Sleep(50);
    }
    status = InterlockedCompareExchange(&task->status, 0, 0);
    if (status == 0) {
        InterlockedCompareExchange(&task->status, -2, 0);
        write_result(seq, 0, "download_sns_media_url", "queue_worker_not_started");
    } else if (status == 2) {
        write_result(seq, 0, "download_sns_media_url", "queue_worker_timeout");
    }

    retain_arena(arena);
    return status == 1;
}

int wxb_download_sns_media_task_to_result(const char *seq, const char *url,
                                          const char *filekey,
                                          const char *md5,
                                          const char *savepath,
                                          int64_t total_size,
                                          int wait_ms) {
    const SIZE_T arena_size = 0x8000;
    BYTE *arena;
    BYTE *asm_addr;
    SnsMediaTaskDownloadTask *task;
    int asm_len;
    int timeout_ms;
    int status = 0;

    if (!seq || !*seq) return 0;
    if (!wxb_weixin_module()) {
        write_result(seq, 0, "download_sns_media_task", "weixin_module_not_found");
        return 0;
    }
    if (!url || !*url || !savepath || !*savepath) {
        write_result(seq, 0, "download_sns_media_task", "missing_url_or_savepath");
        return 0;
    }
    install_sns_media_decrypt_capture_hooks();

    arena = (BYTE *)VirtualAlloc(NULL, arena_size, MEM_COMMIT | MEM_RESERVE, PAGE_EXECUTE_READWRITE);
    if (!arena) {
        write_result(seq, 0, "download_sns_media_task", "queue_arena_alloc_failed");
        return 0;
    }
    ZeroMemory(arena, arena_size);
    asm_addr = arena + 0x0200;
    task = (SnsMediaTaskDownloadTask *)(arena + 0x1000);
    task->status = 0;
    task->wait_ms = wait_ms;
    task->total_size = total_size;
    safe_copy(task->seq, sizeof(task->seq), seq);
    safe_copy(task->url, sizeof(task->url), url ? url : "");
    safe_copy(task->filekey, sizeof(task->filekey), filekey ? filekey : "");
    safe_copy(task->md5, sizeof(task->md5), md5 ? md5 : "");
    safe_copy(task->savepath, sizeof(task->savepath), savepath ? savepath : "");

    asm_len = media_build_probe_queue_asm(asm_addr,
                                          (int64_t)task,
                                          (int64_t)media_download_sns_media_task_queue_worker);
    FlushInstructionCache(GetCurrentProcess(), asm_addr, asm_len);
    queue_send((int64_t)asm_addr);

    timeout_ms = wait_ms + 10000;
    if (timeout_ms < 5000) timeout_ms = 5000;
    if (timeout_ms > 70000) timeout_ms = 70000;
    for (int i = 0; i < timeout_ms / 50; i++) {
        status = InterlockedCompareExchange(&task->status, 0, 0);
        if (status == 1 || status < 0) break;
        Sleep(50);
    }
    status = InterlockedCompareExchange(&task->status, 0, 0);
    if (status == 0) {
        InterlockedCompareExchange(&task->status, -2, 0);
        write_result(seq, 0, "download_sns_media_task", "queue_worker_not_started");
    } else if (status == 2) {
        write_result(seq, 0, "download_sns_media_task", "queue_worker_timeout");
    }

    retain_arena(arena);
    return status == 1;
}

int wxb_download_sns_media_full_task_to_result(const char *seq, const char *url,
                                               const char *filekey,
                                               const char *md5,
                                               const char *aeskey,
                                               const char *savepath,
                                               int64_t total_size,
                                               int task_type,
                                               int wait_ms) {
    const SIZE_T arena_size = 0xA000;
    BYTE *arena;
    BYTE *asm_addr;
    SnsMediaFullTaskDownloadTask *task;
    int asm_len;
    int timeout_ms;
    int status = 0;

    if (!seq || !*seq) return 0;
    if (!wxb_weixin_module()) {
        write_result(seq, 0, "download_sns_media_full_task", "weixin_module_not_found");
        return 0;
    }
    if (!url || !*url || !savepath || !*savepath || !aeskey) {
        write_result(seq, 0, "download_sns_media_full_task", "missing_url_or_savepath");
        return 0;
    }
    install_sns_media_decrypt_capture_hooks();

    arena = (BYTE *)VirtualAlloc(NULL, arena_size, MEM_COMMIT | MEM_RESERVE, PAGE_EXECUTE_READWRITE);
    if (!arena) {
        write_result(seq, 0, "download_sns_media_full_task", "queue_arena_alloc_failed");
        return 0;
    }
    ZeroMemory(arena, arena_size);
    asm_addr = arena + 0x0200;
    task = (SnsMediaFullTaskDownloadTask *)(arena + 0x1000);
    task->status = 0;
    task->wait_ms = wait_ms;
    task->task_type = task_type;
    task->total_size = total_size;
    safe_copy(task->seq, sizeof(task->seq), seq);
    safe_copy(task->url, sizeof(task->url), url ? url : "");
    safe_copy(task->filekey, sizeof(task->filekey), filekey ? filekey : "");
    safe_copy(task->md5, sizeof(task->md5), md5 ? md5 : "");
    safe_copy(task->aeskey, sizeof(task->aeskey), aeskey ? aeskey : "");
    safe_copy(task->savepath, sizeof(task->savepath), savepath ? savepath : "");

    asm_len = media_build_probe_queue_asm(asm_addr,
                                          (int64_t)task,
                                          (int64_t)media_download_sns_media_full_task_queue_worker);
    FlushInstructionCache(GetCurrentProcess(), asm_addr, asm_len);
    queue_send((int64_t)asm_addr);

    timeout_ms = wait_ms + 10000;
    if (timeout_ms < 5000) timeout_ms = 5000;
    if (timeout_ms > 70000) timeout_ms = 70000;
    for (int i = 0; i < timeout_ms / 50; i++) {
        status = InterlockedCompareExchange(&task->status, 0, 0);
        if (status == 1 || status < 0) break;
        Sleep(50);
    }
    status = InterlockedCompareExchange(&task->status, 0, 0);
    if (status == 0) {
        InterlockedCompareExchange(&task->status, -2, 0);
        write_result(seq, 0, "download_sns_media_full_task", "queue_worker_not_started");
    } else if (status == 2) {
        write_result(seq, 0, "download_sns_media_full_task", "queue_worker_timeout");
    }

    retain_arena(arena);
    return status == 1;
}

int64_t wxb_download_image_internal(const char *msgid, const char *fileid, const char *aeskey, const char *savepath,
                                    int64_t filelen, int mode, int app_type, int file_type) {
    if (!wxb_weixin_module() || !msgid || !fileid || !aeskey || !savepath) return -90001;
    const SIZE_T arena_size = 0x10000;
    BYTE *arena = (BYTE *)VirtualAlloc(NULL, arena_size, MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE);
    if (!arena) return -90002;
    ZeroMemory(arena, arena_size);
    int64_t base = (int64_t)wxb_weixin_module();
    int64_t st = (int64_t)(arena + 0x0000);
    int64_t extra = (int64_t)(arena + 0x0600);
    BYTE *pool = arena + 0x1000;
    BYTE *pool_end = arena + arena_size;
    int64_t ret = -90003;

    __try {
        ((FnCall1)(base + WX_OFF_IMAGE_DL_INIT))(st);
        w64((void *)st, extra);
        set_utf8_ptr_fields(st, 0x50, 0x60, 0x68, msgid, &pool, pool_end);
        set_utf8_ptr_fields(st, 0x70, 0x80, 0x88, fileid, &pool, pool_end);
        set_utf8_ptr_fields(st, 0x90, 0xA0, 0xA8, aeskey, &pool, pool_end);
        set_utf8_ptr_fields(st, 0xB0, 0xC0, 0xC8, savepath, &pool, pool_end);
        if (mode >= 0) w32((void *)(st + 0xD0), (uint32_t)mode);
        if (app_type >= 0) w32((void *)(st + 0xD4), (uint32_t)app_type);
        if (file_type >= 0) w32((void *)(st + 0xD8), (uint32_t)file_type);
        w8((void *)(st + 0xDC), 1);
        if (filelen > 0) w64((void *)(st + 0x198), (uint64_t)filelen);
        w32((void *)(st + 0x28), 15);
        w32((void *)(st + 0x48), 15);
        write_file_bytes(IMAGE_DL_PARAM_FILE, (const char *)st, 0x600);
        ret = ((FnCall1)(base + WX_OFF_IMAGE_DL_SUBMIT))(st);
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
        ret = -90004;
    }

    retain_arena(arena);
    return ret;
}

static void wx_set_path_struct(int64_t header, const char *utf8, BYTE **pool, BYTE *pool_end) {
    int len = utf8 ? lstrlenA(utf8) : 0;
    if (!utf8) utf8 = "";
    if (len <= 15) {
        copy_bytes((void *)(header + 8), utf8, len);
        w32((void *)(header + 16), len);
        w32((void *)(header + 24), 15);
        w64((void *)header, header + 8);
        return;
    }
    if (*pool + len + 1 >= pool_end) len = (int)(pool_end - *pool - 1);
    if (len < 0) len = 0;
    copy_bytes(*pool, utf8, len);
    (*pool)[len] = 0;
    w32((void *)(header + 16), len);
    w32((void *)(header + 24), len);
    w64((void *)header, (int64_t)(*pool));
    *pool += len + 16;
}

int wxb_decode_image_internal(const char *input_path, const char *output_path) {
    if (!wxb_weixin_module() || !input_path || !output_path) return 0;
    const SIZE_T arena_size = 0x200000;
    BYTE *arena = (BYTE *)VirtualAlloc(NULL, arena_size, MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE);
    if (!arena) return 0;
    ZeroMemory(arena, arena_size);
    int64_t base = (int64_t)wxb_weixin_module();
    int64_t rcx = (int64_t)(arena + 0x0000);
    int64_t rdx = (int64_t)(arena + 0x0040);
    BYTE *pool = arena + 0x0100;
    BYTE *pool_end = arena + arena_size;
    int ok = 0;

    __try {
        wx_set_path_struct(rcx, input_path, &pool, pool_end);
        ((FnCall3)(base + WX_OFF_IMAGE_DECODE))(rcx, rdx);
        int len = r32((void *)(rdx + 0x10));
        int64_t data_ptr = r64((void *)rdx);
        if (len > 0 && len <= 64 * 1024 * 1024 && data_ptr) {
            WCHAR wout[1024];
            int wlen = MultiByteToWideChar(CP_UTF8, 0, output_path, -1, wout, 1024);
            if (wlen > 0) {
                HANDLE h = CreateFileW(wout, GENERIC_WRITE, FILE_SHARE_READ, NULL, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
                if (h != INVALID_HANDLE_VALUE) {
                    DWORD written = 0;
                    WriteFile(h, (void *)data_ptr, (DWORD)len, &written, NULL);
                    CloseHandle(h);
                    ok = written == (DWORD)len;
                }
            }
        }
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
        ok = 0;
    }

    retain_arena(arena);
    return ok;
}
