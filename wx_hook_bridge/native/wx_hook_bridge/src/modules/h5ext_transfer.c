/*
 * Module: H5ExtTransfer bridge for Finder/Search autonomous payload fetching.
 *
 * This uses Weixin's own H5 transfer manager:
 *   sub_1818C8E10() -> manager
 *   sub_1818C9490(manager, request, callback_holder)
 *
 * The callback receives the JS-like response variant. We immediately convert it
 * to JSON through sub_180592AD0 and write the full payload to a sidecar file.
 */

#include "../include/wx_bridge_h5ext_transfer.h"
#include "../include/wx_bridge_file_io.h"
#include "../include/wx_bridge_memory.h"
#include "../include/wx_bridge_native_call.h"
#include "../include/wx_bridge_offsets.h"
#include "../include/wx_bridge_paths.h"
#include "../include/wx_bridge_protocol.h"
#include "../include/wx_bridge_runtime.h"
#include "../include/wx_bridge_text.h"
#include "../include/wx_bridge_wechat_string.h"

#include <string.h>

#define H5_REQ_SIZE 392
#define H5_PREVIEW_MAX 384
#define H5_DEFAULT_CMD_URL "/cgi-bin/mmsearch-bin/searchwebquery"
#define H5_DEFAULT_SCOPE "search"
#define H5_DEFAULT_H5_URL "weixin://resourceid/Search"
#define H5_TRANSFER_CGI_URL ""
#define H5_DEFAULT_JSAPI_CONTROL_BYTE 400

typedef void *(__fastcall *H5ManagerFn)(void);
typedef int (__fastcall *H5SubmitFn)(void *manager, void *request, void *callback_holder);
typedef void (__fastcall *H5ReqDtorFn)(void *request);
typedef int64_t (__fastcall *JsVarToJsonFn)(void *out_string, void *variant);

typedef struct H5Pending {
    HANDLE event;
    volatile LONG done;
    volatile LONG ok;
    volatile LONG json_size;
    volatile LONG submit_ret;
    char seq[64];
    WCHAR json_path[1024];
    char preview[H5_PREVIEW_MAX];
} H5Pending;

typedef struct H5CallbackObject {
    volatile LONG refcount;
    LONG reserved;
    void (__fastcall *invoke)(struct H5CallbackObject *self, void *variant);
    void (__fastcall *destroy)(void);
    H5Pending *pending;
} H5CallbackObject;

static volatile LONG g_h5ext_inflight = 0;

static void h5_init_empty_std_string(void *header) {
    ZeroMemory(header, 32);
    w64((BYTE *)header + 0x18, 15);
}

static void h5_free_std_string(int64_t base, void *header) {
    uint64_t cap;
    int64_t ptr;
    if (!base || !header) return;
    cap = (uint64_t)r64((BYTE *)header + 0x18);
    if (cap < 0x10) return;
    ptr = r64(header);
    if (!ptr) return;
    ((FnCall1)(base + WX_OFF_WX_FREE))(ptr);
    h5_init_empty_std_string(header);
}

static const char *h5_std_string_data(void *header, DWORD *len_out) {
    uint64_t len;
    uint64_t cap;
    const char *src;
    if (len_out) *len_out = 0;
    if (!header) return "";
    len = (uint64_t)r64((BYTE *)header + 0x10);
    cap = (uint64_t)r64((BYTE *)header + 0x18);
    if (len > 32 * 1024 * 1024) return "";
    src = (cap >= 0x10) ? (const char *)r64(header) : (const char *)header;
    if (!src) return "";
    if (len_out) *len_out = (DWORD)len;
    return src;
}

static DWORD h5_safe_utf8_prefix_len(const char *data, DWORD len) {
    DWORD n = len;
    int i;
    if (!data || len == 0) return 0;
    for (i = 0; i < 4 && n > 0; i++, n--) {
        if (MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, data, (int)n, NULL, 0) > 0) {
            return n;
        }
    }
    return n;
}

static void h5_build_sidecar_path(const char *seq, WCHAR *out, int max_chars) {
    if (!out || max_chars <= 0) return;
    wsprintfW(out, L"%s\\h5ext_%S.json", CONTROL_DIR, seq ? seq : "");
}

static void h5_write_command_result(const char *seq,
                                    int ok,
                                    const char *message,
                                    const WCHAR *json_path,
                                    DWORD json_size,
                                    const char *preview,
                                    int wait_status,
                                    int submit_ret) {
    WCHAR path[1024];
    char json[4096];
    char num[64];
    char json_path_utf8[1024];
    char *p = json;
    char *end = json + sizeof(json) - 1;
    if (!seq || !*seq) return;
    json_path_utf8[0] = 0;
    if (json_path && json_path[0]) {
        WideCharToMultiByte(CP_UTF8, 0, json_path, -1, json_path_utf8, sizeof(json_path_utf8), NULL, NULL);
    }
    wsprintfW(path, L"%s\\result_%S.json", CONTROL_DIR, seq);
    append_raw(&p, end, "{\"ok\":");
    append_raw(&p, end, ok ? "true" : "false");
    append_raw(&p, end, ",\"seq\":\"");
    append_escaped(&p, end, seq);
    append_raw(&p, end, "\",\"command\":\"h5ext_transfer\",\"message\":\"");
    append_escaped(&p, end, message ? message : "");
    append_raw(&p, end, "\",\"responseFile\":\"");
    append_escaped(&p, end, json_path_utf8);
    append_raw(&p, end, "\",\"responseSize\":");
    wsprintfA(num, "%lu", (unsigned long)json_size);
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"submitRet\":");
    wsprintfA(num, "%d", submit_ret);
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"waitStatus\":");
    wsprintfA(num, "%d", wait_status);
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"preview\":\"");
    append_escaped(&p, end, preview ? preview : "");
    append_raw(&p, end, "\"}");
    *p = 0;
    wxb_write_file_bytes(path, json, (DWORD)(p - json));
}

static void __fastcall h5_callback_destroy_noop(void) {
}

static void __fastcall h5_callback_invoke(H5CallbackObject *self, void *variant) {
    H5Pending *pending;
    HMODULE mod;
    int64_t base;
    BYTE out_string[32];
    const char *json_data = "";
    DWORD json_len = 0;
    DWORD preview_len;
    JsVarToJsonFn to_json;

    if (!self || !variant) return;
    pending = self->pending;
    if (!pending) return;
    if (InterlockedCompareExchange(&pending->done, 1, 0) != 0) return;
    mod = wxb_weixin_module();
    if (!mod) return;
    base = (int64_t)mod;
    to_json = (JsVarToJsonFn)(base + WX_OFF_JSVAR_TO_JSON);

    memset(out_string, 0xAA, sizeof(out_string));
    __try {
        to_json(out_string, variant);
        json_data = h5_std_string_data(out_string, &json_len);
        if (json_data && json_len > 0) {
            wxb_write_file_bytes(pending->json_path, json_data, json_len);
            preview_len = json_len < (H5_PREVIEW_MAX - 1) ? json_len : (H5_PREVIEW_MAX - 1);
            preview_len = h5_safe_utf8_prefix_len(json_data, preview_len);
            copy_bytes(pending->preview, json_data, (int)preview_len);
            pending->preview[preview_len] = 0;
            InterlockedExchange(&pending->json_size, (LONG)json_len);
            InterlockedExchange(&pending->ok, 1);
        } else {
            pending->preview[0] = 0;
            InterlockedExchange(&pending->json_size, 0);
            InterlockedExchange(&pending->ok, 0);
        }
        /* Do not free out_string here. sub_180592AD0 constructs a Weixin/C++
         * string into our raw buffer; large H5 responses have crashed before
         * the waiter can write result_*.json when freed from this callback.
         * The leak is bounded to one response buffer per synthetic H5 request.
         */
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
        pending->preview[0] = 0;
        InterlockedExchange(&pending->ok, 0);
    }

    if (pending->event) SetEvent(pending->event);
}

static void h5_init_request(int64_t base,
                            BYTE *req,
                            const char *seq,
                            const char *h5_url,
                            const char *cmd_url,
                            int cmd_id,
                            const char *req_json,
                            const char *scope,
                            int jsapi_control_byte,
                            int report_security,
                            const char *transfer_cgi_url) {
    char id[128];
    ZeroMemory(req, H5_REQ_SIZE);
    wsprintfA(id, "bridge_%s_%lu", seq ? seq : "0", (unsigned long)GetTickCount());
    wx_set_std_string_heap(base, (int64_t)(req + 0x000), id);
    wx_set_std_string_heap(base, (int64_t)(req + 0x020), (h5_url && *h5_url) ? h5_url : H5_DEFAULT_H5_URL);
    wx_set_std_string_heap(base, (int64_t)(req + 0x040), (cmd_url && *cmd_url) ? cmd_url : H5_DEFAULT_CMD_URL);
    wx_set_std_string_heap(base, (int64_t)(req + 0x060), req_json ? req_json : "");
    wx_set_std_string_heap(base, (int64_t)(req + 0x080), (scope && *scope) ? scope : H5_DEFAULT_SCOPE);
    w8(req + 0x0A0, report_security ? 1 : 0);
    w32(req + 0x0A4, cmd_id > 0 ? cmd_id : 2975);
    /* +0xA8 is pub_key_token, not h5_auth_token. Captured Search requests
     * initialize it empty before the manager handles the request.
     */
    wx_set_std_string_heap(base, (int64_t)(req + 0x0A8), "");
    /* +0xC8 is cgi_id; captured Search requests initialize it to zero. */
    w32(req + 0x0C8, 0);
    /* Real Search H5ExtTransfer leaves this empty; the manager maps the
     * transfer CGI internally from the WebView h5_url/scope context.
     */
    wx_set_std_string_heap(base, (int64_t)(req + 0x0D0),
                           (transfer_cgi_url && *transfer_cgi_url) ? transfer_cgi_url : H5_TRANSFER_CGI_URL);
    w32(req + 0x0F0, jsapi_control_byte > 0
                           ? jsapi_control_byte
                           : H5_DEFAULT_JSAPI_CONTROL_BYTE);
    w32(req + 0x0F4, 0);
    w32(req + 0x0F8, 0);
    w32(req + 0x100, 0);
    w32(req + 0x104, 0);
    wx_set_std_string_heap(base, (int64_t)(req + 0x120), "");
    w32(req + 0x140, 0);
    wx_set_std_string_heap(base, (int64_t)(req + 0x148), "");
    w8(req + 0x168, 0);
}

int h5ext_transfer_to_result(const char *seq,
                             const char *cmd_url,
                             int cmd_id,
                             const char *req_json,
                             const char *scope,
                             int wait_ms,
                             int jsapi_control_byte,
                             const char *h5_url,
                             int report_security,
                             const char *transfer_cgi_url) {
    HMODULE mod;
    int64_t base;
    H5ManagerFn manager_fn;
    H5SubmitFn submit_fn;
    H5ReqDtorFn req_dtor;
    void *manager;
    BYTE req[H5_REQ_SIZE];
    H5Pending *pending = NULL;
    H5CallbackObject *callback = NULL;
    void **holder = NULL;
    DWORD wait_status = WAIT_FAILED;
    int submit_ret = 0;
    int ok = 0;

    if (!seq || !*seq) return 0;
    if (!req_json || !*req_json) {
        h5_write_command_result(seq, 0, "missing_req_json", NULL, 0, "", WAIT_FAILED, 0);
        return 0;
    }
    if (wait_ms <= 0) wait_ms = 15000;
    if (wait_ms > 60000) wait_ms = 60000;
    if (InterlockedCompareExchange(&g_h5ext_inflight, 1, 0) != 0) {
        h5_write_command_result(seq, 0, "h5ext_transfer_busy", NULL, 0, "", WAIT_FAILED, 0);
        return 0;
    }

    mod = wxb_ensure_weixin_module();
    if (!mod) {
        InterlockedExchange(&g_h5ext_inflight, 0);
        h5_write_command_result(seq, 0, "weixin_module_not_found", NULL, 0, "", WAIT_FAILED, 0);
        return 0;
    }
    base = (int64_t)mod;
    manager_fn = (H5ManagerFn)(base + WX_OFF_H5_TRANSFER_MANAGER);
    submit_fn = (H5SubmitFn)(base + WX_OFF_H5_TRANSFER_SUBMIT);
    req_dtor = (H5ReqDtorFn)(base + WX_OFF_H5_REQ_DTOR);

    pending = (H5Pending *)HeapAlloc(GetProcessHeap(), HEAP_ZERO_MEMORY, sizeof(H5Pending));
    callback = (H5CallbackObject *)HeapAlloc(GetProcessHeap(), HEAP_ZERO_MEMORY, sizeof(H5CallbackObject));
    holder = (void **)((FnCall1)(base + WX_OFF_WX_ALLOC))(sizeof(void *));
    if (!pending || !callback || !holder) {
        if (holder) ((FnCall1)(base + WX_OFF_WX_DELETE))((int64_t)holder);
        if (callback) HeapFree(GetProcessHeap(), 0, callback);
        if (pending) HeapFree(GetProcessHeap(), 0, pending);
        InterlockedExchange(&g_h5ext_inflight, 0);
        h5_write_command_result(seq, 0, "alloc_failed", NULL, 0, "", WAIT_FAILED, 0);
        return 0;
    }

    safe_copy(pending->seq, sizeof(pending->seq), seq);
    h5_build_sidecar_path(seq, pending->json_path, 1024);
    pending->event = CreateEventW(NULL, TRUE, FALSE, NULL);
    if (!pending->event) {
        ((FnCall1)(base + WX_OFF_WX_DELETE))((int64_t)holder);
        HeapFree(GetProcessHeap(), 0, callback);
        HeapFree(GetProcessHeap(), 0, pending);
        InterlockedExchange(&g_h5ext_inflight, 0);
        h5_write_command_result(seq, 0, "event_create_failed", NULL, 0, "", WAIT_FAILED, 0);
        return 0;
    }

    callback->refcount = 2;
    callback->invoke = h5_callback_invoke;
    callback->destroy = h5_callback_destroy_noop;
    callback->pending = pending;
    *holder = callback;

    h5_init_request(base, req, seq, h5_url, cmd_url, cmd_id, req_json, scope,
                    jsapi_control_byte, report_security, transfer_cgi_url);
    __try {
        manager = manager_fn();
        if (manager) {
            submit_ret = submit_fn(manager, req, holder);
            InterlockedExchange(&pending->submit_ret, submit_ret);
            wait_status = WaitForSingleObject(pending->event, (DWORD)wait_ms);
        } else {
            wait_status = WAIT_FAILED;
        }
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
        wait_status = WAIT_FAILED;
        submit_ret = -1;
    }
    req_dtor(req);

    ok = (wait_status == WAIT_OBJECT_0) && InterlockedCompareExchange(&pending->ok, 0, 0);
    h5_write_command_result(
        seq,
        ok,
        ok ? "h5ext_transfer ok" : (wait_status == WAIT_TIMEOUT ? "h5ext_transfer timeout" : "h5ext_transfer failed"),
        pending->json_path,
        (DWORD)InterlockedCompareExchange(&pending->json_size, 0, 0),
        pending->preview,
        (int)wait_status,
        submit_ret);

    /* Keep callback and pending alive even after success. Some H5 transfers
     * (notably mmpcwebsearch) can deliver late/secondary callbacks after the
     * first response. Freeing pending here turns that into a Weixin UAF crash.
     */
    InterlockedExchange(&g_h5ext_inflight, 0);
    return ok ? 1 : 0;
}
