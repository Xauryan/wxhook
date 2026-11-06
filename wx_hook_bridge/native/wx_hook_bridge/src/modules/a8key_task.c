/*
 * Module: minimal Weixin protobuf task bridge for mp-geta8key.
 *
 * 4.1.9.23 notes:
 * - sub_1802D7830 is the mars/mmext task dispatcher. Hooking it captures the
 *   manager pointer used to submit later synthetic protobuf tasks.
 * - sub_180D0D5E0 initializes the 0x1C8 task base layout used by dispatcher.
 *   We then replace the task vtable with callbacks that provide request bytes
 *   and capture response bytes.
 */

#include "../include/wx_bridge_a8key_task.h"
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
#include <string.h>

#define A8_TASK_DISPATCH_PATCH_SIZE 17
#define A8_TASK_SIZE 0x1D0
#define A8_CALLBACK_HOLDER_SIZE 0x08
#define A8_REQ_MAX 8192
#define A8_RESP_MAX 65536
#define A8_CGI_TYPE_MP_GETA8KEY 0xEE
#define A8_ENDPOINT_MP_GETA8KEY "/cgi-bin/micromsg-bin/mp-geta8key"
#define OPLOG_CGI_TYPE 681
#define OPLOG_ENDPOINT "/cgi-bin/micromsg-bin/oplog"
/* IDA: micromsg.SetRemarkOplog has username/remark fields; WeChat oplog uses
 * CmdItem(cmd_id, SKBuiltinBuffer_t(serialized-oplog-message)).
 */
#define OPLOG_CMD_SET_REMARK 2
#define OPLOG_CMD_MOD_CHATROOM_TOPIC 27
#define OPLOG_CMD_MOD_CHATROOM_MEMBER_DISPLAYNAME 48
#define FINDER_CGI_TYPE_USERPAGE_PREVIEW 6611
#define FINDER_ENDPOINT_USERPAGE_PREVIEW "/cgi-bin/micromsg-bin/finderuserpagepreview"

typedef int64_t (__fastcall *A8TaskDispatchFn)(void *manager, void *task);

typedef struct A8HookState {
    volatile LONG installed;
    BYTE orig[A8_TASK_DISPATCH_PATCH_SIZE];
    void *trampoline;
    A8TaskDispatchFn original;
    void *manager;
    volatile LONG dispatch_count;
    volatile LONG submit_count;
    volatile LONG prepare_count;
    volatile LONG callback_count;
    volatile LONG last_response_size;
    volatile LONG64 last_dispatch_result;
} A8HookState;

typedef struct A8Pending {
    HANDLE event;
    BYTE request[A8_REQ_MAX];
    DWORD request_size;
    BYTE response[A8_RESP_MAX];
    DWORD response_size;
    volatile LONG done;
} A8Pending;

typedef struct PbWriter {
    BYTE *p;
    BYTE *end;
    int overflow;
} PbWriter;

static A8HookState g_a8_hook = {0};
static void * volatile g_a8_pending = NULL;

static const BYTE A8_TASK_DISPATCH_PROLOGUE[A8_TASK_DISPATCH_PATCH_SIZE] = {
    0x55, 0x41, 0x57, 0x41, 0x56, 0x41, 0x54, 0x56, 0x57, 0x53,
    0x48, 0x81, 0xEC, 0xA0, 0x03, 0x00, 0x00
};

static int a8_hex_val(char c) {
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    if (c >= 'A' && c <= 'F') return c - 'A' + 10;
    return -1;
}

static void a8_url_decode_copy(char *dst, int max, const char *src, int len) {
    int i = 0;
    int j = 0;
    if (!dst || max <= 0) return;
    dst[0] = 0;
    if (!src || len <= 0) return;
    while (i < len && j < max - 1) {
        if (src[i] == '%' && i + 2 < len) {
            int hi = a8_hex_val(src[i + 1]);
            int lo = a8_hex_val(src[i + 2]);
            if (hi >= 0 && lo >= 0) {
                dst[j++] = (char)((hi << 4) | lo);
                i += 3;
                continue;
            }
        }
        dst[j++] = (src[i] == '+') ? ' ' : src[i];
        i++;
    }
    dst[j] = 0;
}

static void a8_extract_query_value(const char *url, const char *name, char *dst, int max) {
    const char *p;
    int name_len;
    if (!dst || max <= 0) return;
    dst[0] = 0;
    if (!url || !name || !*name) return;
    name_len = lstrlenA(name);
    p = url;
    while (*p) {
        if ((*p == '?' || *p == '&') && !strncmp(p + 1, name, name_len) && p[1 + name_len] == '=') {
            const char *v = p + 2 + name_len;
            const char *e = v;
            while (*e && *e != '&' && *e != '#') e++;
            a8_url_decode_copy(dst, max, v, (int)(e - v));
            return;
        }
        p++;
    }
}

static void pb_init(PbWriter *w, BYTE *buf, DWORD cap) {
    w->p = buf;
    w->end = buf + cap;
    w->overflow = 0;
}

static DWORD pb_size(PbWriter *w, BYTE *buf) {
    return (DWORD)(w->p - buf);
}

static void pb_byte(PbWriter *w, BYTE v) {
    if (w->p >= w->end) {
        w->overflow = 1;
        return;
    }
    *w->p++ = v;
}

static void pb_varint(PbWriter *w, uint64_t v) {
    while (v >= 0x80) {
        pb_byte(w, (BYTE)(v | 0x80));
        v >>= 7;
    }
    pb_byte(w, (BYTE)v);
}

static void pb_tag(PbWriter *w, DWORD field, DWORD wire) {
    pb_varint(w, ((uint64_t)field << 3) | wire);
}

static void pb_varint_field(PbWriter *w, DWORD field, uint64_t value) {
    pb_tag(w, field, 0);
    pb_varint(w, value);
}

static void pb_bytes_field(PbWriter *w, DWORD field, const void *data, DWORD len) {
    const BYTE *p = (const BYTE *)data;
    pb_tag(w, field, 2);
    pb_varint(w, len);
    for (DWORD i = 0; i < len; i++) pb_byte(w, p[i]);
}

static void pb_string_field(PbWriter *w, DWORD field, const char *value) {
    if (!value) value = "";
    pb_bytes_field(w, field, value, (DWORD)lstrlenA(value));
}

static DWORD build_base_request(BYTE *buf, DWORD cap) {
    PbWriter w;
    const char *device = "Windows";
    pb_init(&w, buf, cap);
    pb_bytes_field(&w, 1, "", 0);
    pb_varint_field(&w, 2, 0);
    pb_string_field(&w, 3, device);
    pb_varint_field(&w, 4, 0);
    pb_string_field(&w, 5, device);
    pb_varint_field(&w, 6, 0);
    return w.overflow ? 0 : pb_size(&w, buf);
}

static DWORD build_builtin_string(const char *value, BYTE *buf, DWORD cap) {
    PbWriter w;
    pb_init(&w, buf, cap);
    pb_string_field(&w, 1, value ? value : "");
    return w.overflow ? 0 : pb_size(&w, buf);
}

static DWORD build_get_a8key_req(const char *url, int scene, int url_type, BYTE *buf, DWORD cap) {
    BYTE base[256];
    BYTE req_url[4096];
    DWORD base_len = build_base_request(base, sizeof(base));
    DWORD req_url_len = build_builtin_string(url, req_url, sizeof(req_url));
    PbWriter w;
    if (!base_len || !req_url_len) return 0;
    pb_init(&w, buf, cap);
    pb_bytes_field(&w, 1, base, base_len);
    pb_varint_field(&w, 2, 2);
    pb_bytes_field(&w, 7, req_url, req_url_len);
    pb_varint_field(&w, 10, (uint32_t)scene);
    pb_varint_field(&w, 14, 8);
    pb_varint_field(&w, 20, (uint32_t)url_type);
    pb_varint_field(&w, 31, (uint32_t)GetTickCount64());
    return w.overflow ? 0 : pb_size(&w, buf);
}

static DWORD build_set_remark_oplog(const char *wxid, const char *remark, BYTE *buf, DWORD cap) {
    PbWriter w;
    if (!wxid || !*wxid) return 0;
    pb_init(&w, buf, cap);
    pb_string_field(&w, 1, wxid);
    pb_string_field(&w, 2, remark ? remark : "");
    return w.overflow ? 0 : pb_size(&w, buf);
}

static DWORD build_skbuiltin_buffer(const BYTE *data, DWORD data_len, BYTE *buf, DWORD cap) {
    PbWriter w;
    if (!data || !data_len) return 0;
    pb_init(&w, buf, cap);
    pb_varint_field(&w, 1, data_len);
    pb_bytes_field(&w, 2, data, data_len);
    return w.overflow ? 0 : pb_size(&w, buf);
}

static DWORD build_oplog_cmd_item(int cmd_id,
                                  const BYTE *payload,
                                  DWORD payload_len,
                                  BYTE *buf,
                                  DWORD cap) {
    BYTE builtin[A8_REQ_MAX];
    DWORD builtin_len;
    PbWriter w;
    if (!payload || !payload_len) return 0;
    builtin_len = build_skbuiltin_buffer(payload, payload_len, builtin, sizeof(builtin));
    if (!builtin_len) return 0;
    pb_init(&w, buf, cap);
    pb_varint_field(&w, 1, (uint32_t)cmd_id);
    pb_bytes_field(&w, 2, builtin, builtin_len);
    return w.overflow ? 0 : pb_size(&w, buf);
}

static DWORD build_oplog_request_one(int cmd_id,
                                     const BYTE *payload,
                                     DWORD payload_len,
                                     BYTE *buf,
                                     DWORD cap) {
    BYTE cmd_item[A8_REQ_MAX];
    BYTE cmd_list[A8_REQ_MAX];
    DWORD cmd_item_len;
    DWORD cmd_list_len;
    PbWriter list;
    PbWriter req;
    cmd_item_len = build_oplog_cmd_item(cmd_id, payload, payload_len, cmd_item, sizeof(cmd_item));
    if (!cmd_item_len) return 0;
    pb_init(&list, cmd_list, sizeof(cmd_list));
    pb_varint_field(&list, 1, 1);
    pb_bytes_field(&list, 2, cmd_item, cmd_item_len);
    if (list.overflow) return 0;
    cmd_list_len = pb_size(&list, cmd_list);
    pb_init(&req, buf, cap);
    pb_bytes_field(&req, 1, cmd_list, cmd_list_len);
    return req.overflow ? 0 : pb_size(&req, buf);
}

static DWORD build_modify_remark_req(const char *wxid,
                                     const char *remark,
                                     BYTE *buf,
                                     DWORD cap) {
    BYTE set_remark[A8_REQ_MAX];
    DWORD set_remark_len = build_set_remark_oplog(wxid, remark, set_remark, sizeof(set_remark));
    if (!set_remark_len) return 0;
    return build_oplog_request_one(OPLOG_CMD_SET_REMARK, set_remark, set_remark_len, buf, cap);
}

static DWORD build_empty_message(BYTE *buf, DWORD cap) {
    (void)buf;
    (void)cap;
    return 0;
}

static DWORD build_finder_userpage_req(const char *finder_username,
                                       const char *extra_buffer,
                                       BYTE *buf,
                                       DWORD cap) {
    BYTE base[256];
    BYTE context[16];
    DWORD base_len = build_base_request(base, sizeof(base));
    DWORD context_len = build_empty_message(context, sizeof(context));
    PbWriter w;
    int has_extra = extra_buffer && *extra_buffer;
    if (!base_len || !finder_username || !*finder_username) return 0;
    pb_init(&w, buf, cap);
    pb_bytes_field(&w, 1, base, base_len);
    pb_bytes_field(&w, 2, context, context_len);
    pb_string_field(&w, 3, finder_username);
    pb_varint_field(&w, 4, has_extra ? 3 : 0);
    pb_varint_field(&w, 6, has_extra ? 6 : 1);
    if (has_extra) {
        pb_string_field(&w, 7, extra_buffer);
    }
    return w.overflow ? 0 : pb_size(&w, buf);
}

static int pb_read_varint(const BYTE *data, DWORD size, DWORD *pos, uint64_t *value) {
    uint64_t result = 0;
    int shift = 0;
    while (*pos < size && shift <= 63) {
        BYTE b = data[(*pos)++];
        result |= ((uint64_t)(b & 0x7F)) << shift;
        if (!(b & 0x80)) {
            *value = result;
            return 1;
        }
        shift += 7;
    }
    return 0;
}

static void a8_copy_bytes_text(char *dst, int max, const BYTE *src, uint64_t len) {
    int n;
    if (!dst || max <= 0) return;
    dst[0] = 0;
    if (!src || !len) return;
    n = (len < (uint64_t)(max - 1)) ? (int)len : max - 1;
    copy_bytes(dst, src, n);
    dst[n] = 0;
}

static void parse_a8key_resp(const BYTE *data, DWORD size, char *full_url, int full_url_max,
                             char *a8key, int a8key_max) {
    DWORD pos = 0;
    if (full_url && full_url_max > 0) full_url[0] = 0;
    if (a8key && a8key_max > 0) a8key[0] = 0;
    while (pos < size) {
        uint64_t tag = 0;
        DWORD field;
        DWORD wire;
        if (!pb_read_varint(data, size, &pos, &tag)) return;
        field = (DWORD)(tag >> 3);
        wire = (DWORD)(tag & 7);
        if (wire == 0) {
            uint64_t ignored = 0;
            if (!pb_read_varint(data, size, &pos, &ignored)) return;
        } else if (wire == 1) {
            if (pos + 8 > size) return;
            pos += 8;
        } else if (wire == 2) {
            uint64_t len = 0;
            const BYTE *p;
            if (!pb_read_varint(data, size, &pos, &len) || len > size - pos) return;
            p = data + pos;
            if (field == 2) a8_copy_bytes_text(full_url, full_url_max, p, len);
            else if (field == 3) a8_copy_bytes_text(a8key, a8key_max, p, len);
            pos += (DWORD)len;
        } else if (wire == 5) {
            if (pos + 4 > size) return;
            pos += 4;
        } else {
            return;
        }
    }
}

static int write_abs_jump(BYTE *target, SIZE_T patch_size, void *to) {
    DWORD oldp = 0;
    BYTE patch[32];
    if (!target || patch_size < 12 || patch_size > sizeof(patch)) return 0;
    if (!VirtualProtect(target, patch_size, PAGE_EXECUTE_READWRITE, &oldp)) return 0;
    for (SIZE_T i = 0; i < patch_size; i++) patch[i] = 0x90;
    patch[0] = 0x48;
    patch[1] = 0xB8;
    *(uint64_t *)(patch + 2) = (uint64_t)to;
    patch[10] = 0xFF;
    patch[11] = 0xE0;
    copy_bytes(target, patch, (int)patch_size);
    FlushInstructionCache(GetCurrentProcess(), target, patch_size);
    VirtualProtect(target, patch_size, oldp, &oldp);
    return 1;
}

static int build_trampoline(BYTE *target, SIZE_T patch_size, void **trampoline) {
    BYTE *mem;
    BYTE *tail;
    if (!target || !trampoline) return 0;
    mem = (BYTE *)VirtualAlloc(NULL, patch_size + 16, MEM_COMMIT | MEM_RESERVE, PAGE_EXECUTE_READWRITE);
    if (!mem) return 0;
    copy_bytes(mem, target, (int)patch_size);
    tail = mem + patch_size;
    tail[0] = 0x48;
    tail[1] = 0xB8;
    *(uint64_t *)(tail + 2) = (uint64_t)(target + patch_size);
    tail[10] = 0xFF;
    tail[11] = 0xE0;
    *trampoline = mem;
    return 1;
}

static int try_read_response_block(void **response_holder, const BYTE **data, DWORD *size) {
    __try {
        BYTE *block;
        if (!response_holder || !*response_holder) return 0;
        block = (BYTE *)(*response_holder);
        *data = *(const BYTE **)block;
        *size = *(DWORD *)(block + 12);
        return *data && *size > 0 && *size < A8_RESP_MAX;
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
        *data = NULL;
        *size = 0;
        return 0;
    }
}

static int64_t __fastcall a8_task_destructor(void *self, int64_t a2, int64_t a3) {
    (void)a2;
    (void)a3;
    return (int64_t)self;
}

static int64_t __fastcall a8_serialize_prepare(void *self, void **out_holder, int64_t a3, BYTE *out_size) {
    A8Pending *pending;
    void *holder;
    void *buf;
    (void)self;
    (void)a3;
    InterlockedIncrement(&g_a8_hook.prepare_count);
    pending = (A8Pending *)InterlockedCompareExchangePointer((PVOID volatile *)&g_a8_pending, NULL, NULL);
    if (!pending || !out_holder || !*out_holder || !pending->request_size) return 1;
    holder = *out_holder;
    buf = HeapAlloc(GetProcessHeap(), HEAP_ZERO_MEMORY, pending->request_size + 1);
    if (!buf) return 0;
    copy_bytes(buf, pending->request, (int)pending->request_size);
    *(void **)holder = buf;
    *(DWORD *)((BYTE *)holder + 12) = pending->request_size;
    *(DWORD *)((BYTE *)holder + 20) = pending->request_size;
    if (out_size) *out_size = (BYTE)(pending->request_size & 0xFF);
    return 1;
}

static int64_t __fastcall a8_response_callback(void *self, void **response_holder, int64_t a3) {
    A8Pending *pending;
    const BYTE *data = NULL;
    DWORD size = 0;
    (void)self;
    (void)a3;
    InterlockedIncrement(&g_a8_hook.callback_count);
    pending = (A8Pending *)InterlockedCompareExchangePointer((PVOID volatile *)&g_a8_pending, NULL, NULL);
    if (pending && try_read_response_block(response_holder, &data, &size)) {
        DWORD n = size < A8_RESP_MAX ? size : A8_RESP_MAX;
        copy_bytes(pending->response, data, (int)n);
        pending->response_size = n;
        InterlockedExchange(&pending->done, 1);
        InterlockedExchange(&g_a8_hook.last_response_size, (LONG)n);
        if (pending->event) SetEvent(pending->event);
    }
    return 1;
}

static int64_t __fastcall a8_get_inner(void *self) {
    return (int64_t)((BYTE *)self + 232);
}

static int64_t __fastcall a8_dummy(void *self, int64_t a2, int64_t a3, int64_t a4) {
    (void)self;
    (void)a2;
    (void)a3;
    (void)a4;
    return 1;
}

static void *g_a8_task_vtable[] = {
    (void *)a8_task_destructor,
    (void *)a8_serialize_prepare,
    (void *)a8_response_callback,
    (void *)a8_get_inner,
    (void *)a8_dummy,
    (void *)1
};

static void *g_a8_callback_vtable[] = {
    (void *)a8_dummy,
    (void *)a8_dummy,
    (void *)a8_dummy,
    (void *)a8_dummy
};

static int64_t __fastcall a8_task_dispatch_hook(void *manager, void *task) {
    if (manager && !g_a8_hook.manager) {
        InterlockedExchangePointer((PVOID volatile *)&g_a8_hook.manager, manager);
    }
    InterlockedIncrement(&g_a8_hook.dispatch_count);
    return g_a8_hook.original ? g_a8_hook.original(manager, task) : 0;
}

int a8key_dispatch_hook_installed(void) {
    return InterlockedCompareExchange(&g_a8_hook.installed, 0, 0) != 0;
}

int a8key_task_manager_captured(void) {
    return InterlockedCompareExchangePointer((PVOID volatile *)&g_a8_hook.manager, NULL, NULL) != NULL;
}

int a8key_task_experimental_enabled(void) {
    return file_exists(A8KEY_TASK_ENABLE_FILE) ? 1 : 0;
}

void install_a8key_dispatch_hook(void) {
    BYTE *target;
    HMODULE mod;
    if (!a8key_task_experimental_enabled()) return;
    if (a8key_dispatch_hook_installed()) return;
    mod = wxb_weixin_module();
    if (!mod) return;
    target = (BYTE *)mod + WX_OFF_TASK_DISPATCH;
    if (!bytes_equal(target, A8_TASK_DISPATCH_PROLOGUE, A8_TASK_DISPATCH_PATCH_SIZE)) {
        write_status("a8key task hook skipped: dispatch prologue mismatch\n");
        return;
    }
    copy_bytes(g_a8_hook.orig, target, A8_TASK_DISPATCH_PATCH_SIZE);
    if (!build_trampoline(target, A8_TASK_DISPATCH_PATCH_SIZE, &g_a8_hook.trampoline)) {
        write_status("a8key task hook failed: trampoline alloc\n");
        return;
    }
    g_a8_hook.original = (A8TaskDispatchFn)g_a8_hook.trampoline;
    if (!write_abs_jump(target, A8_TASK_DISPATCH_PATCH_SIZE, (void *)a8_task_dispatch_hook)) {
        g_a8_hook.original = NULL;
        write_status("a8key task hook failed: patch write\n");
        return;
    }
    InterlockedExchange(&g_a8_hook.installed, 1);
    write_status("a8key task hook installed\n");
}

void uninstall_a8key_dispatch_hook(void) {
    BYTE *target;
    DWORD oldp = 0;
    HMODULE mod;
    if (!a8key_dispatch_hook_installed()) return;
    mod = wxb_weixin_module();
    if (mod) {
        target = (BYTE *)mod + WX_OFF_TASK_DISPATCH;
        if (VirtualProtect(target, A8_TASK_DISPATCH_PATCH_SIZE, PAGE_EXECUTE_READWRITE, &oldp)) {
            copy_bytes(target, g_a8_hook.orig, A8_TASK_DISPATCH_PATCH_SIZE);
            FlushInstructionCache(GetCurrentProcess(), target, A8_TASK_DISPATCH_PATCH_SIZE);
            VirtualProtect(target, A8_TASK_DISPATCH_PATCH_SIZE, oldp, &oldp);
        }
    }
    InterlockedExchange(&g_a8_hook.installed, 0);
    g_a8_hook.original = NULL;
    g_a8_hook.manager = NULL;
    if (g_a8_hook.trampoline) {
        VirtualFree(g_a8_hook.trampoline, 0, MEM_RELEASE);
        g_a8_hook.trampoline = NULL;
    }
}

static void write_a8key_result(const char *seq, int ok, const char *message,
                               const char *url, int scene, int url_type,
                               const char *full_url, const char *export_key,
                               const char *a8key, DWORD raw_size,
                               int wait_status, int64_t dispatch_result) {
    WCHAR path[1024];
    char json[32768];
    char num[64];
    char *p = json;
    char *end = json + sizeof(json) - 1;
    wsprintfW(path, L"%s\\result_%S.json", CONTROL_DIR, seq ? seq : "");
    append_raw(&p, end, "{\"ok\":");
    append_raw(&p, end, ok ? "true" : "false");
    append_raw(&p, end, ",\"seq\":\"");
    append_escaped(&p, end, seq ? seq : "");
    append_raw(&p, end, "\",\"command\":\"get_a8key\",\"message\":\"");
    append_escaped(&p, end, message ? message : "");
    append_raw(&p, end, "\",\"url\":\"");
    append_escaped(&p, end, url ? url : "");
    append_raw(&p, end, "\",\"scene\":");
    wsprintfA(num, "%d", scene);
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"urlType\":");
    wsprintfA(num, "%d", url_type);
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"fullUrl\":\"");
    append_escaped(&p, end, full_url ? full_url : "");
    append_raw(&p, end, "\",\"exportKey\":\"");
    append_escaped(&p, end, export_key ? export_key : "");
    append_raw(&p, end, "\",\"a8Key\":\"");
    append_escaped(&p, end, a8key ? a8key : "");
    append_raw(&p, end, "\",\"rawSize\":");
    wsprintfA(num, "%lu", (unsigned long)raw_size);
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"waitStatus\":");
    wsprintfA(num, "%d", wait_status);
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"dispatchResult\":");
    wsprintfA(num, "%I64d", (long long)dispatch_result);
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"hookInstalled\":");
    append_raw(&p, end, a8key_dispatch_hook_installed() ? "true" : "false");
    append_raw(&p, end, ",\"managerCaptured\":");
    append_raw(&p, end, a8key_task_manager_captured() ? "true" : "false");
    append_raw(&p, end, ",\"dispatchCount\":");
    wsprintfA(num, "%ld", (long)InterlockedCompareExchange(&g_a8_hook.dispatch_count, 0, 0));
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"submitCount\":");
    wsprintfA(num, "%ld", (long)InterlockedCompareExchange(&g_a8_hook.submit_count, 0, 0));
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"prepareCount\":");
    wsprintfA(num, "%ld", (long)InterlockedCompareExchange(&g_a8_hook.prepare_count, 0, 0));
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"callbackCount\":");
    wsprintfA(num, "%ld", (long)InterlockedCompareExchange(&g_a8_hook.callback_count, 0, 0));
    append_raw(&p, end, num);
    append_raw(&p, end, "}");
    *p = 0;
    wxb_write_file_bytes(path, json, (DWORD)(p - json));
}

int get_a8key_task_to_result(const char *seq, const char *url, int scene, int url_type, int wait_ms) {
    HMODULE mod;
    int64_t base;
    void *manager;
    A8Pending pending;
    uint64_t *task = NULL;
    uint64_t *callback_holder = NULL;
    int64_t dispatch_result = 0;
    DWORD wait_status = WAIT_FAILED;
    char full_url[8192];
    char a8key[2048];
    char export_key[2048];
    int ok = 0;

    if (!seq || !*seq) return 0;
    if (!url || !*url) {
        write_a8key_result(seq, 0, "missing_url", url, scene, url_type, "", "", "", 0, WAIT_FAILED, 0);
        return 0;
    }
    if (wait_ms <= 0) wait_ms = 15000;
    if (wait_ms > 60000) wait_ms = 60000;

    if (!a8key_task_experimental_enabled()) {
        write_a8key_result(seq, 0, "a8key_task_disabled", url, scene, url_type, "", "", "", 0, WAIT_FAILED, 0);
        return 0;
    }

    mod = wxb_ensure_weixin_module();
    if (!mod) {
        write_a8key_result(seq, 0, "weixin_module_not_found", url, scene, url_type, "", "", "", 0, WAIT_FAILED, 0);
        return 0;
    }
    install_a8key_dispatch_hook();
    manager = InterlockedCompareExchangePointer((PVOID volatile *)&g_a8_hook.manager, NULL, NULL);
    if (!g_a8_hook.original || !manager) {
        write_a8key_result(seq, 0, "task_manager_not_captured", url, scene, url_type, "", "", "", 0, WAIT_FAILED, 0);
        return 0;
    }

    ZeroMemory(&pending, sizeof(pending));
    pending.event = CreateEventW(NULL, TRUE, FALSE, NULL);
    if (!pending.event) {
        write_a8key_result(seq, 0, "event_create_failed", url, scene, url_type, "", "", "", 0, WAIT_FAILED, 0);
        return 0;
    }
    pending.request_size = build_get_a8key_req(url, scene, url_type, pending.request, sizeof(pending.request));
    if (!pending.request_size) {
        CloseHandle(pending.event);
        write_a8key_result(seq, 0, "build_request_failed", url, scene, url_type, "", "", "", 0, WAIT_FAILED, 0);
        return 0;
    }

    if (InterlockedCompareExchangePointer((PVOID volatile *)&g_a8_pending, &pending, NULL) != NULL) {
        CloseHandle(pending.event);
        write_a8key_result(seq, 0, "a8key_task_busy", url, scene, url_type, "", "", "", 0, WAIT_FAILED, 0);
        return 0;
    }

    base = (int64_t)mod;
    task = (uint64_t *)HeapAlloc(GetProcessHeap(), HEAP_ZERO_MEMORY, A8_TASK_SIZE);
    callback_holder = (uint64_t *)HeapAlloc(GetProcessHeap(), HEAP_ZERO_MEMORY, A8_CALLBACK_HOLDER_SIZE);
    if (!task || !callback_holder) {
        InterlockedExchangePointer((PVOID volatile *)&g_a8_pending, NULL);
        if (pending.event) CloseHandle(pending.event);
        write_a8key_result(seq, 0, "task_alloc_failed", url, scene, url_type, "", "", "", 0, WAIT_FAILED, 0);
        return 0;
    }

    __try {
        ((FnCall1)(base + WX_OFF_TASK_BASE_INIT))((int64_t)task);
        task[0] = (uint64_t)g_a8_task_vtable;
        callback_holder[0] = (uint64_t)g_a8_callback_vtable;
        task[26] = (uint64_t)callback_holder;
        w32((BYTE *)task + 12, A8_CGI_TYPE_MP_GETA8KEY);
        wx_set_std_string_heap(base, (int64_t)((BYTE *)task + 24), A8_ENDPOINT_MP_GETA8KEY);
        InterlockedIncrement(&g_a8_hook.submit_count);
        dispatch_result = g_a8_hook.original(manager, task);
        InterlockedExchange64(&g_a8_hook.last_dispatch_result, dispatch_result);
        wait_status = WaitForSingleObject(pending.event, (DWORD)wait_ms);
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
        wait_status = WAIT_FAILED;
        dispatch_result = -1;
    }

    InterlockedExchangePointer((PVOID volatile *)&g_a8_pending, NULL);
    full_url[0] = 0;
    a8key[0] = 0;
    export_key[0] = 0;
    if (pending.response_size) {
        parse_a8key_resp(pending.response, pending.response_size, full_url, sizeof(full_url), a8key, sizeof(a8key));
        a8_extract_query_value(full_url, "exportkey", export_key, sizeof(export_key));
    }
    ok = (wait_status == WAIT_OBJECT_0) && (full_url[0] || a8key[0]);
    write_a8key_result(seq,
                       ok,
                       ok ? "get_a8key ok" : (wait_status == WAIT_TIMEOUT ? "get_a8key timeout" : "get_a8key failed"),
                       url,
                       scene,
                       url_type,
                       full_url,
                       export_key,
                       a8key,
                       pending.response_size,
                       (int)wait_status,
                       dispatch_result);
    if (pending.event) CloseHandle(pending.event);
    return ok;
}

static void write_modify_remark_result(const char *seq,
                                       int ok,
                                       const char *message,
                                       const char *wxid,
                                       const char *remark,
                                       DWORD request_size,
                                       DWORD response_size,
                                       int wait_status,
                                       int64_t dispatch_result) {
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
    append_raw(&p, end, "\",\"command\":\"modify_remark\",\"path\":\"native_contact_modify_remark\",\"message\":\"");
    append_escaped(&p, end, message ? message : "");
    append_raw(&p, end, "\",\"wxid\":\"");
    append_escaped(&p, end, wxid ? wxid : "");
    append_raw(&p, end, "\",\"remark\":\"");
    append_escaped(&p, end, remark ? remark : "");
    append_raw(&p, end, "\",\"cmdId\":");
    wsprintfA(num, "%d", OPLOG_CMD_SET_REMARK);
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"cgiType\":");
    wsprintfA(num, "%d", OPLOG_CGI_TYPE);
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"endpoint\":\"");
    append_escaped(&p, end, OPLOG_ENDPOINT);
    append_raw(&p, end, "\",\"requestSize\":");
    wsprintfA(num, "%lu", (unsigned long)request_size);
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"rawSize\":");
    wsprintfA(num, "%lu", (unsigned long)response_size);
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"waitStatus\":");
    wsprintfA(num, "%d", wait_status);
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"dispatchResult\":");
    wsprintfA(num, "%I64d", (long long)dispatch_result);
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"upperCall\":\"sub_180636C90\",\"upperCallOffset\":\"0x636c90\"");
    append_raw(&p, end, "}");
    *p = 0;
    wxb_write_file_bytes(path, json, (DWORD)(p - json));
}

int modify_remark_task_to_result(const char *seq, const char *wxid,
                                 const char *remark, int wait_ms) {
    HMODULE mod;
    int64_t base;
    BYTE username_arg[32];
    BYTE remark_arg[32];
    int64_t call_result = 0;
    DWORD wait_status = WAIT_FAILED;
    int ok = 0;

    (void)wait_ms;
    if (!seq || !*seq) return 0;
    if (!wxid || !*wxid) {
        write_modify_remark_result(seq, 0, "missing_wxid", wxid, remark, 0, 0, WAIT_FAILED, 0);
        return 0;
    }

    mod = wxb_ensure_weixin_module();
    if (!mod) {
        write_modify_remark_result(seq, 0, "weixin_module_not_found", wxid, remark, 0, 0, WAIT_FAILED, 0);
        return 0;
    }
    base = (int64_t)mod;
    ZeroMemory(username_arg, sizeof(username_arg));
    ZeroMemory(remark_arg, sizeof(remark_arg));
    wx_set_std_string_heap(base, (int64_t)username_arg, wxid);
    wx_set_std_string_heap(base, (int64_t)remark_arg, remark ? remark : "");

    __try {
        call_result = ((FnCall3Args)(base + WX_OFF_CONTACT_MODIFY_REMARK))(
            0,
            (int64_t)username_arg,
            (int64_t)remark_arg);
        wait_status = WAIT_OBJECT_0;
        ok = 1;
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
        wait_status = WAIT_FAILED;
        call_result = -1;
        ok = 0;
    }

    wx_free_std_string_heap(base, (int64_t)username_arg);
    wx_free_std_string_heap(base, (int64_t)remark_arg);
    write_modify_remark_result(
        seq,
        ok,
        ok ? "modify_remark submitted" : "modify_remark failed",
        wxid,
        remark,
        0,
        0,
        (int)wait_status,
        call_result);
    return ok;
}

static void write_modify_chatroom_self_display_name_result(const char *seq,
                                                           int ok,
                                                           const char *message,
                                                           const char *roomid,
                                                           const char *display_name,
                                                           DWORD request_size,
                                                           DWORD response_size,
                                                           int wait_status,
                                                           int64_t dispatch_result) {
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
    append_raw(&p, end, "\",\"command\":\"modify_room_nickname\",\"path\":\"native_chatroom_self_display_name\",\"message\":\"");
    append_escaped(&p, end, message ? message : "");
    append_raw(&p, end, "\",\"roomid\":\"");
    append_escaped(&p, end, roomid ? roomid : "");
    append_raw(&p, end, "\",\"displayName\":\"");
    append_escaped(&p, end, display_name ? display_name : "");
    append_raw(&p, end, "\",\"cmdId\":");
    wsprintfA(num, "%d", OPLOG_CMD_MOD_CHATROOM_MEMBER_DISPLAYNAME);
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"cgiType\":");
    wsprintfA(num, "%d", OPLOG_CGI_TYPE);
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"endpoint\":\"");
    append_escaped(&p, end, OPLOG_ENDPOINT);
    append_raw(&p, end, "\",\"requestSize\":");
    wsprintfA(num, "%lu", (unsigned long)request_size);
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"rawSize\":");
    wsprintfA(num, "%lu", (unsigned long)response_size);
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"waitStatus\":");
    wsprintfA(num, "%d", wait_status);
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"dispatchResult\":");
    wsprintfA(num, "%I64d", (long long)dispatch_result);
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"upperCall\":\"sub_180638060\",\"upperCallOffset\":\"0x638060\"}");
    *p = 0;
    wxb_write_file_bytes(path, json, (DWORD)(p - json));
}

int modify_chatroom_self_display_name_to_result(const char *seq, const char *roomid,
                                                const char *display_name, int wait_ms) {
    HMODULE mod;
    int64_t base;
    BYTE roomid_arg[32];
    BYTE display_name_arg[32];
    int64_t call_result = 0;
    DWORD wait_status = WAIT_FAILED;
    int ok = 0;

    (void)wait_ms;
    if (!seq || !*seq) return 0;
    if (!roomid || !*roomid) {
        write_modify_chatroom_self_display_name_result(seq, 0, "missing_roomid", roomid, display_name, 0, 0, WAIT_FAILED, 0);
        return 0;
    }

    mod = wxb_ensure_weixin_module();
    if (!mod) {
        write_modify_chatroom_self_display_name_result(seq, 0, "weixin_module_not_found", roomid, display_name, 0, 0, WAIT_FAILED, 0);
        return 0;
    }
    base = (int64_t)mod;
    ZeroMemory(roomid_arg, sizeof(roomid_arg));
    ZeroMemory(display_name_arg, sizeof(display_name_arg));
    wx_set_std_string_heap(base, (int64_t)roomid_arg, roomid);
    wx_set_std_string_heap(base, (int64_t)display_name_arg, display_name ? display_name : "");

    __try {
        call_result = ((FnCall3Args)(base + WX_OFF_CHATROOM_MOD_SELF_DISPLAY_NAME))(
            0,
            (int64_t)roomid_arg,
            (int64_t)display_name_arg);
        wait_status = WAIT_OBJECT_0;
        ok = 1;
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
        wait_status = WAIT_FAILED;
        call_result = -1;
        ok = 0;
    }

    wx_free_std_string_heap(base, (int64_t)roomid_arg);
    wx_free_std_string_heap(base, (int64_t)display_name_arg);
    write_modify_chatroom_self_display_name_result(
        seq,
        ok,
        ok ? "modify_room_nickname submitted" : "modify_room_nickname failed",
        roomid,
        display_name,
        0,
        0,
        (int)wait_status,
        call_result);
    return ok;
}

static void write_modify_chatroom_topic_result(const char *seq,
                                               int ok,
                                               const char *message,
                                               const char *roomid,
                                               const char *topic,
                                               DWORD request_size,
                                               DWORD response_size,
                                               int wait_status,
                                               int64_t dispatch_result) {
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
    append_raw(&p, end, "\",\"command\":\"modify_room_name\",\"path\":\"native_chatroom_topic\",\"message\":\"");
    append_escaped(&p, end, message ? message : "");
    append_raw(&p, end, "\",\"roomid\":\"");
    append_escaped(&p, end, roomid ? roomid : "");
    append_raw(&p, end, "\",\"topic\":\"");
    append_escaped(&p, end, topic ? topic : "");
    append_raw(&p, end, "\",\"cmdId\":");
    wsprintfA(num, "%d", OPLOG_CMD_MOD_CHATROOM_TOPIC);
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"cgiType\":");
    wsprintfA(num, "%d", OPLOG_CGI_TYPE);
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"endpoint\":\"");
    append_escaped(&p, end, OPLOG_ENDPOINT);
    append_raw(&p, end, "\",\"requestSize\":");
    wsprintfA(num, "%lu", (unsigned long)request_size);
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"rawSize\":");
    wsprintfA(num, "%lu", (unsigned long)response_size);
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"waitStatus\":");
    wsprintfA(num, "%d", wait_status);
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"dispatchResult\":");
    wsprintfA(num, "%I64d", (long long)dispatch_result);
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"upperCall\":\"sub_180636690\",\"upperCallOffset\":\"0x636690\"}");
    *p = 0;
    wxb_write_file_bytes(path, json, (DWORD)(p - json));
}

int modify_chatroom_topic_to_result(const char *seq, const char *roomid,
                                    const char *topic, int wait_ms) {
    HMODULE mod;
    int64_t base;
    BYTE roomid_arg[32];
    BYTE topic_arg[32];
    int64_t call_result = 0;
    DWORD wait_status = WAIT_FAILED;
    int ok = 0;

    (void)wait_ms;
    if (!seq || !*seq) return 0;
    if (!roomid || !*roomid) {
        write_modify_chatroom_topic_result(seq, 0, "missing_roomid", roomid, topic, 0, 0, WAIT_FAILED, 0);
        return 0;
    }

    mod = wxb_ensure_weixin_module();
    if (!mod) {
        write_modify_chatroom_topic_result(seq, 0, "weixin_module_not_found", roomid, topic, 0, 0, WAIT_FAILED, 0);
        return 0;
    }
    base = (int64_t)mod;
    ZeroMemory(roomid_arg, sizeof(roomid_arg));
    ZeroMemory(topic_arg, sizeof(topic_arg));
    wx_set_std_string_heap(base, (int64_t)roomid_arg, roomid);
    wx_set_std_string_heap(base, (int64_t)topic_arg, topic ? topic : "");

    __try {
        call_result = ((FnCall3Args)(base + WX_OFF_CHATROOM_MOD_TOPIC))(
            0,
            (int64_t)roomid_arg,
            (int64_t)topic_arg);
        wait_status = WAIT_OBJECT_0;
        ok = 1;
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
        wait_status = WAIT_FAILED;
        call_result = -1;
        ok = 0;
    }

    wx_free_std_string_heap(base, (int64_t)roomid_arg);
    wx_free_std_string_heap(base, (int64_t)topic_arg);
    write_modify_chatroom_topic_result(
        seq,
        ok,
        ok ? "modify_room_name submitted" : "modify_room_name failed",
        roomid,
        topic,
        0,
        0,
        (int)wait_status,
        call_result);
    return ok;
}

typedef unsigned int *(__fastcall *ChatroomAnnouncementSendFn)(
    unsigned int *out,
    int64_t cgi_ctx,
    int64_t req,
    int64_t empty_extra,
    int64_t opts);
typedef int64_t (__fastcall *ChatroomAnnouncementNoArgsFn)(void);
typedef void (__fastcall *ChatroomAnnouncementSharedCtlFn)(int64_t ctl);

#define CHATROOM_ANNOUNCEMENT_ROOMID_MAX 512
#define CHATROOM_ANNOUNCEMENT_TEXT_MAX 8192

typedef struct ChatroomAnnouncementTask {
    volatile LONG refs;
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
    char roomid[CHATROOM_ANNOUNCEMENT_ROOMID_MAX];
    char announcement[CHATROOM_ANNOUNCEMENT_TEXT_MAX];
} ChatroomAnnouncementTask;

static void * volatile g_chatroom_announcement_pending = NULL;

static int chatroom_announcement_set_string_field(int64_t base,
                                                  BYTE *req,
                                                  int field_off,
                                                  BYTE flag,
                                                  const char *value) {
    int64_t obj;
    if (!base || !req) return 0;
    obj = r64(req + field_off);
    if (!obj || obj == base + WX_OFF_PROTOBUF_EMPTY_STRING) {
        obj = ((FnCall1)(base + WX_OFF_WX_ALLOC))(32);
        if (!obj) return 0;
        ZeroMemory((void *)obj, 32);
        w64((void *)(obj + 0x18), 15);
        w64(req + field_off, obj);
    }
    req[80] |= flag;
    wx_set_std_string_heap(base, obj, value ? value : "");
    return 1;
}

static void chatroom_announcement_release_shared_ctl(int64_t ctl) {
    int64_t vt;
    volatile LONG *refs;
    if (!ctl) return;
    refs = (volatile LONG *)ctl;
    if (!_InterlockedDecrement(refs + 2)) {
        vt = r64((void *)ctl);
        if (vt) {
            ((ChatroomAnnouncementSharedCtlFn)r64((void *)vt))(ctl);
            if (!_InterlockedDecrement(refs + 3)) {
                ((ChatroomAnnouncementSharedCtlFn)r64((void *)(vt + 8)))(ctl);
            }
        }
    }
}

static void chatroom_announcement_cleanup_send_out(int64_t base, BYTE *out) {
    if (!base || !out) return;
    chatroom_announcement_release_shared_ctl(r64(out + 48));
    wx_free_std_string_heap(base, (int64_t)(out + 8));
}

static void chatroom_announcement_task_release(ChatroomAnnouncementTask *task) {
    if (!task) return;
    if (InterlockedDecrement(&task->refs) == 0) {
        HeapFree(GetProcessHeap(), 0, task);
    }
}

static void chatroom_announcement_worker(int64_t task_addr) {
    ChatroomAnnouncementTask *task = (ChatroomAnnouncementTask *)task_addr;
    int64_t base = (int64_t)wxb_weixin_module();
    BYTE req[88];
    BYTE extra[32];
    BYTE opts[48];
    BYTE out[64];
    int64_t root = 0;
    int64_t cgi_ctx = 0;
    int req_initialized = 0;
    int out_initialized = 0;
    int cleanup_failed = 0;
    LONG final_status = -1;

    if (!task) return;
    if (!base) {
        InterlockedCompareExchange(&task->status, -3, 0);
        return;
    }
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
        ((FnCall1)(base + WX_OFF_CHATROOM_ANNOUNCEMENT_REQ_INIT))((int64_t)req);
        req_initialized = 1;

        task->phase = 2;
        if (!chatroom_announcement_set_string_field(base, req, 16, 0x02, task->roomid) ||
            !chatroom_announcement_set_string_field(base, req, 24, 0x04, task->announcement) ||
            !chatroom_announcement_set_string_field(base, req, 32, 0x10, "")) {
            task->call_result = -2;
            final_status = -4;
            __leave;
        }
        req[80] |= 0x80;
        w32(req + 72, 1);

        task->phase = 3;
        root = ((ChatroomAnnouncementNoArgsFn)(base + WX_OFF_FINDER_USERPAGE_CGI_CONTEXT_ROOT))();
        task->phase = 4;
        if (root && r64((void *)root)) {
            cgi_ctx = ((FnCall1)r64((void *)(r64((void *)root) + 16)))(root);
        }
        if (!cgi_ctx) {
            task->call_result = -3;
            final_status = -5;
            __leave;
        }

        task->phase = 5;
        ((ChatroomAnnouncementSendFn)(base + WX_OFF_CHATROOM_ANNOUNCEMENT_SEND))(
            (unsigned int *)out,
            cgi_ctx,
            (int64_t)req,
            (int64_t)extra,
            (int64_t)opts);
        out_initialized = 1;
        task->call_result = r64(out);
        task->wait_status = WAIT_OBJECT_0;
        task->phase = 6;
        final_status = task->call_result == 0 ? 1 : -6;
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
        final_status = -1;
    }

    if (out_initialized) {
        __try {
            chatroom_announcement_cleanup_send_out(base, out);
        }
        __except (EXCEPTION_EXECUTE_HANDLER) {
            cleanup_failed = 1;
        }
    }
    if (req_initialized) {
        __try {
            ((FnCall1)(base + WX_OFF_CHATROOM_ANNOUNCEMENT_REQ_DTOR))((int64_t)req);
        }
        __except (EXCEPTION_EXECUTE_HANDLER) {
            cleanup_failed = 1;
        }
    }
    if (cleanup_failed) final_status = -7;
    InterlockedCompareExchange(&task->status, final_status, 2);
}

static void chatroom_announcement_queue_entry(void) {
    ChatroomAnnouncementTask *task = (ChatroomAnnouncementTask *)
        InterlockedExchangePointer((PVOID volatile *)&g_chatroom_announcement_pending, NULL);
    if (!task) return;
    chatroom_announcement_worker((int64_t)task);
    chatroom_announcement_task_release(task);
}

static void write_publish_chatroom_announcement_result(const char *seq,
                                                       int ok,
                                                       const char *message,
                                                       const char *roomid,
                                                       const char *announcement,
                                                       int wait_status,
                                                       int64_t dispatch_result,
                                                       int queue_status,
                                                       int phase,
                                                       DWORD exception_code,
                                                       uint64_t exception_address,
                                                       uint64_t exception_fault,
                                                       uint64_t ctx_rcx,
                                                       uint64_t ctx_rdx,
                                                       uint64_t ctx_r8,
                                                       uint64_t ctx_r9) {
    WCHAR path[1024];
    char json[65536];
    char num[64];
    char *p = json;
    char *end = json + sizeof(json) - 1;
    wsprintfW(path, L"%s\\result_%S.json", CONTROL_DIR, seq ? seq : "");
    append_raw(&p, end, "{\"ok\":");
    append_raw(&p, end, ok ? "true" : "false");
    append_raw(&p, end, ",\"seq\":\"");
    append_escaped(&p, end, seq ? seq : "");
    append_raw(&p, end, "\",\"command\":\"publish_room_announcement\",\"path\":\"native_set_chatroom_announcement\",\"message\":\"");
    append_escaped(&p, end, message ? message : "");
    append_raw(&p, end, "\",\"roomid\":\"");
    append_escaped(&p, end, roomid ? roomid : "");
    append_raw(&p, end, "\",\"announcement\":\"");
    append_escaped(&p, end, announcement ? announcement : "");
    append_raw(&p, end, "\",\"cgiType\":993,\"endpoint\":\"/cgi-bin/micromsg-bin/setchatroomannouncement\"");
    append_raw(&p, end, ",\"requestObjectSize\":88,\"roomidField\":2,\"announcementField\":3,\"announcementXmlField\":5");
    append_raw(&p, end, ",\"waitStatus\":");
    wsprintfA(num, "%d", wait_status);
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"dispatchResult\":");
    wsprintfA(num, "%I64d", (long long)dispatch_result);
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"netErr\":");
    wsprintfA(num, "%d", (int32_t)((uint64_t)dispatch_result & 0xFFFFFFFFu));
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"cgiErr\":");
    wsprintfA(num, "%d", (int32_t)((uint64_t)dispatch_result >> 32));
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"queueStatus\":");
    wsprintfA(num, "%d", queue_status);
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"phase\":");
    wsprintfA(num, "%d", phase);
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"exceptionCode\":");
    wsprintfA(num, "%lu", (unsigned long)exception_code);
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"exceptionAddress\":\"0x");
    wsprintfA(num, "%I64x", (unsigned long long)exception_address);
    append_raw(&p, end, num);
    append_raw(&p, end, "\"");
    append_raw(&p, end, ",\"exceptionFault\":\"0x");
    wsprintfA(num, "%I64x", (unsigned long long)exception_fault);
    append_raw(&p, end, num);
    append_raw(&p, end, "\"");
    append_raw(&p, end, ",\"ctxRcx\":\"0x");
    wsprintfA(num, "%I64x", (unsigned long long)ctx_rcx);
    append_raw(&p, end, num);
    append_raw(&p, end, "\",\"ctxRdx\":\"0x");
    wsprintfA(num, "%I64x", (unsigned long long)ctx_rdx);
    append_raw(&p, end, num);
    append_raw(&p, end, "\",\"ctxR8\":\"0x");
    wsprintfA(num, "%I64x", (unsigned long long)ctx_r8);
    append_raw(&p, end, num);
    append_raw(&p, end, "\",\"ctxR9\":\"0x");
    wsprintfA(num, "%I64x", (unsigned long long)ctx_r9);
    append_raw(&p, end, num);
    append_raw(&p, end, "\"");
    append_raw(&p, end, ",\"queuedOnWeixinThread\":true");
    append_raw(&p, end, ",\"uiCall\":\"sub_184B3F020 -> sub_184309380\"");
    append_raw(&p, end, ",\"upperCall\":\"sub_18430A170\",\"upperCallOffset\":\"0x430a170\"");
    append_raw(&p, end, ",\"requestInit\":\"sub_185845FA0\",\"requestInitOffset\":\"0x5845fa0\"");
    append_raw(&p, end, "}");
    *p = 0;
    wxb_write_file_bytes(path, json, (DWORD)(p - json));
}

int publish_chatroom_announcement_to_result(const char *seq, const char *roomid,
                                            const char *announcement, int wait_ms) {
    ChatroomAnnouncementTask *task;
    int loops;
    LONG status;
    int ok;
    const char *message;

    if (!seq || !*seq) return 0;
    if (!roomid || !*roomid) {
        write_publish_chatroom_announcement_result(seq, 0, "missing_roomid", roomid, announcement,
                                                   WAIT_FAILED, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0);
        return 0;
    }
    if (lstrlenA(roomid) >= CHATROOM_ANNOUNCEMENT_ROOMID_MAX) {
        write_publish_chatroom_announcement_result(seq, 0, "roomid_too_long", roomid, announcement,
                                                   WAIT_FAILED, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0);
        return 0;
    }
    if (announcement && lstrlenA(announcement) >= CHATROOM_ANNOUNCEMENT_TEXT_MAX) {
        write_publish_chatroom_announcement_result(seq, 0, "announcement_too_long", roomid, announcement,
                                                   WAIT_FAILED, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0);
        return 0;
    }
    if (!wxb_ensure_weixin_module()) {
        write_publish_chatroom_announcement_result(seq, 0, "weixin_module_not_found", roomid, announcement,
                                                   WAIT_FAILED, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0);
        return 0;
    }

    task = (ChatroomAnnouncementTask *)HeapAlloc(GetProcessHeap(),
                                                HEAP_ZERO_MEMORY,
                                                sizeof(ChatroomAnnouncementTask));
    if (!task) {
        write_publish_chatroom_announcement_result(seq, 0, "task_alloc_failed", roomid, announcement,
                                                   WAIT_FAILED, -4, 0, 0, 0, 0, 0, 0, 0, 0, 0);
        return 0;
    }
    task->refs = 2;
    task->status = 0;
    task->wait_status = WAIT_FAILED;
    safe_copy(task->roomid, sizeof(task->roomid), roomid);
    safe_copy(task->announcement, sizeof(task->announcement), announcement ? announcement : "");

    if (InterlockedCompareExchangePointer(
            (PVOID volatile *)&g_chatroom_announcement_pending,
            task,
            NULL) != NULL) {
        write_publish_chatroom_announcement_result(seq, 0, "publish_room_announcement busy",
                                                   roomid, announcement, WAIT_FAILED, -5,
                                                   -7, 0, 0, 0, 0, 0, 0, 0, 0);
        chatroom_announcement_task_release(task);
        chatroom_announcement_task_release(task);
        return 0;
    }
    queue_send((int64_t)chatroom_announcement_queue_entry);

    if (wait_ms <= 0) wait_ms = 15000;
    if (wait_ms > 60000) wait_ms = 60000;
    loops = wait_ms / 10;
    if (loops < 1) loops = 1;
    for (int i = 0; i < loops; i++) {
        status = InterlockedCompareExchange(&task->status, 0, 0);
        if (status == 1 || status < 0) break;
        Sleep(10);
    }
    status = InterlockedCompareExchange(&task->status, 0, 0);
    if (status == 0 || status == 2) {
        if (status == 0 &&
            InterlockedCompareExchangePointer(
                (PVOID volatile *)&g_chatroom_announcement_pending,
                NULL,
                task) == task) {
            chatroom_announcement_task_release(task);
        }
        status = -2;
    }

    ok = status == 1 && task->call_result == 0;
    message = ok ? "publish_room_announcement submitted" :
              (status == -2 ? "publish_room_announcement timeout" :
               status == -3 ? "weixin_module_not_found" :
               status == -4 ? "request_build_failed" :
               status == -5 ? "cgi_context_not_found" :
               status == -6 ? "publish_room_announcement rejected" :
               status == -7 ? "publish_room_announcement cleanup_failed" :
               "publish_room_announcement failed");
    write_publish_chatroom_announcement_result(
        seq,
        ok,
        message,
        roomid,
        announcement,
        (int)task->wait_status,
        task->call_result,
        (int)status,
        task->phase,
        task->seh_code,
        task->seh_addr,
        task->seh_fault,
        task->ctx_rcx,
        task->ctx_rdx,
        task->ctx_r8,
        task->ctx_r9);
    chatroom_announcement_task_release(task);
    return ok;
}

typedef void (__fastcall *FinderSharedCtlVFunc)(int64_t ctl);

static volatile LONG g_finder_userpage_outer_inflight = 0;

#define FINDER_USERPAGE_USERNAME_MAX 512
#define FINDER_USERPAGE_EXTRA_MAX 8192
#define FINDER_USERPAGE_ITEM_EMIT_MAX 64
#define FINDER_USERPAGE_CAPTURE_PATCH_LEN 19
#define FINDER_USERPAGE_CAPTURE_ITEM_MAX 16
#define FINDER_USERPAGE_WEIXIN_IMAGE_SIZE_LIMIT 0x0ABEE000LL

static const BYTE FINDER_USERPAGE_OUTER_PROLOGUE[FINDER_USERPAGE_CAPTURE_PATCH_LEN] = {
    0x55, 0x41, 0x57, 0x41, 0x56, 0x41, 0x55, 0x41, 0x54, 0x56,
    0x57, 0x53, 0x48, 0x81, 0xEC, 0x88, 0x03, 0x00, 0x00
};

typedef unsigned __int64 *(__fastcall *FinderUserpageOuterFn)(
    int64_t service_obj,
    int64_t model,
    int64_t *finder_username,
    int64_t *extra_buffer);

typedef struct FinderUserpageCaptureHook {
    volatile LONG installed;
    volatile LONG capture_count;
    volatile LONG stale_restored;
    BYTE orig[FINDER_USERPAGE_CAPTURE_PATCH_LEN];
    void *trampoline;
} FinderUserpageCaptureHook;

static FinderUserpageCaptureHook g_finder_userpage_capture = {0};

static void finder_userpage_capture_append_literal(const char *s) {
    if (!s) return;
    append_file_bytes(FINDER_USERPAGE_CAPTURE_LOG, s, (DWORD)lstrlenA(s));
}

typedef struct FinderUserpageModelTask {
    volatile LONG status; /* 0=pending, 2=running, 1=ok, negative=failure */
    int call_status;
    int wait_ms;
    int item_count;
    int item_emit_count;
    int lower_call_status;
    int lower_object_count;
    int lower_emit_count;
    DWORD total_count;
    DWORD lower_total_count;
    DWORD lower_base_count;
    DWORD lower_extra_count;
    DWORD lower_resp_field3;
    DWORD lower_resp_field5;
    DWORD lower_resp_field6;
    DWORD lower_resp_flags;
    int lower_req_field4;
    int lower_req_field6;
    int lower_net_err;
    int lower_cgi_err;
    int64_t call_ret;
    int64_t service_obj;
    int64_t model_addr;
    int64_t item_start;
    int64_t item_end;
    int64_t lower_call_ret;
    int64_t lower_error;
    int64_t lower_resp_obj;
    int64_t lower_resp_ctl;
    int64_t lower_object_list;
    int64_t lower_base_response;
    char finder_username[FINDER_USERPAGE_USERNAME_MAX];
    char extra_buffer[FINDER_USERPAGE_EXTRA_MAX];
    char primary[1024];
    char context_a[1024];
    char context_b[1024];
    char context_c[1024];
    char next_extra[4096];
    char lower_out_string[512];
    char lower_base_errmsg[512];
    char lower_next_extra[4096];
    int item_dwords[FINDER_USERPAGE_ITEM_EMIT_MAX];
    char item_strings[FINDER_USERPAGE_ITEM_EMIT_MAX][256];
    char lower_object_field1[FINDER_USERPAGE_ITEM_EMIT_MAX][256];
    int lower_object_field2[FINDER_USERPAGE_ITEM_EMIT_MAX];
    char lower_object_field3[FINDER_USERPAGE_ITEM_EMIT_MAX][256];
    int lower_base_ret;
    DWORD lower_base_flags;
} FinderUserpageModelTask;

static void finder_release_shared_ctl(int64_t ctl) {
    if (!ctl) return;
    __try {
        int64_t vt = r64((void *)ctl);
        if (!InterlockedDecrement((volatile LONG *)(ctl + 8))) {
            ((FinderSharedCtlVFunc)r64((void *)vt))(ctl);
            if (!InterlockedDecrement((volatile LONG *)(ctl + 12))) {
                ((FinderSharedCtlVFunc)r64((void *)(vt + 8)))(ctl);
            }
        }
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
    }
}

static void finder_release_shared_pair(int64_t *pair) {
    if (!pair) return;
    finder_release_shared_ctl(pair[1]);
    pair[0] = 0;
    pair[1] = 0;
}

static int finder_model_vector_count(int64_t start, int64_t end) {
    int64_t bytes;
    if (!start || !end || end < start) return 0;
    bytes = end - start;
    if (bytes % 40) return 0;
    if (bytes > 40LL * 512LL) return 0;
    if (!is_readable_ptr(start, (SIZE_T)bytes)) return 0;
    return (int)(bytes / 40);
}

static void finder_userpage_capture_restore_stale_if_present(void) {
    BYTE *target;
    DWORD oldp = 0;
    if (!wxb_weixin_module()) return;
    target = (BYTE *)((int64_t)wxb_weixin_module() + WX_OFF_FINDER_USERPAGE_OUTER);
    if (!(target[0] == 0x48 && target[1] == 0xB8 && target[10] == 0xFF && target[11] == 0xE0)) return;
    if (!VirtualProtect(target, FINDER_USERPAGE_CAPTURE_PATCH_LEN, PAGE_EXECUTE_READWRITE, &oldp)) return;
    copy_bytes(target, FINDER_USERPAGE_OUTER_PROLOGUE, FINDER_USERPAGE_CAPTURE_PATCH_LEN);
    VirtualProtect(target, FINDER_USERPAGE_CAPTURE_PATCH_LEN, oldp, &oldp);
    FlushInstructionCache(GetCurrentProcess(), target, FINDER_USERPAGE_CAPTURE_PATCH_LEN);
    if (InterlockedCompareExchange(&g_finder_userpage_capture.stale_restored, 1, 0) == 0) {
        finder_userpage_capture_append_literal("finder_userpage_capture: stale outer hook restored\r\n");
    }
}

static void finder_userpage_capture_log(int64_t caller,
                                        int64_t service_obj,
                                        int64_t model,
                                        int64_t ret,
                                        const char *username,
                                        const char *extra_buffer) {
    char json[32768];
    char num[64];
    char *p = json;
    char *end = json + sizeof(json) - 1;
    char primary[1024] = {0};
    char context_a[1024] = {0};
    char context_b[1024] = {0};
    char context_c[1024] = {0};
    char next_extra[4096] = {0};
    int64_t item_start = 0;
    int64_t item_end = 0;
    DWORD total_count = 0;
    int item_count = 0;
    int emit_count = 0;
    int64_t base = wxb_weixin_base();
    int64_t caller_rva = (base && caller >= base &&
                          caller - base < FINDER_USERPAGE_WEIXIN_IMAGE_SIZE_LIMIT)
        ? (caller - base)
        : 0;

    __try {
        if (is_readable_ptr(model, 0xC0)) {
            wx_read_sso_str(model + 0x00, primary, sizeof(primary));
            wx_read_sso_str(model + 0x20, context_a, sizeof(context_a));
            wx_read_sso_str(model + 0x40, context_b, sizeof(context_b));
            wx_read_sso_str(model + 0x60, context_c, sizeof(context_c));
            wx_read_sso_str(model + 0x80, next_extra, sizeof(next_extra));
            total_count = (DWORD)r32((void *)(model + 0xA0));
            item_start = r64((void *)(model + 0xA8));
            item_end = r64((void *)(model + 0xB0));
            item_count = finder_model_vector_count(item_start, item_end);
            emit_count = item_count < FINDER_USERPAGE_CAPTURE_ITEM_MAX
                ? item_count
                : FINDER_USERPAGE_CAPTURE_ITEM_MAX;
        }
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
        primary[0] = 0;
        context_a[0] = 0;
        context_b[0] = 0;
        context_c[0] = 0;
        next_extra[0] = 0;
        total_count = 0;
        item_count = 0;
        emit_count = 0;
    }

    append_raw(&p, end, "{\"event\":\"finder_userpage_outer_capture\",\"tick\":");
    wsprintfA(num, "%I64u", (unsigned __int64)GetTickCount64());
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"tid\":");
    wsprintfA(num, "%lu", (unsigned long)GetCurrentThreadId());
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"caller\":\"0x");
    wsprintfA(num, "%I64x", (unsigned __int64)caller);
    append_raw(&p, end, num);
    append_raw(&p, end, "\",\"callerRva\":\"0x");
    wsprintfA(num, "%I64x", (unsigned __int64)caller_rva);
    append_raw(&p, end, num);
    append_raw(&p, end, "\",\"serviceObj\":\"0x");
    wsprintfA(num, "%I64x", (unsigned __int64)service_obj);
    append_raw(&p, end, num);
    append_raw(&p, end, "\",\"model\":\"0x");
    wsprintfA(num, "%I64x", (unsigned __int64)model);
    append_raw(&p, end, num);
    append_raw(&p, end, "\",\"ret\":\"0x");
    wsprintfA(num, "%I64x", (unsigned __int64)ret);
    append_raw(&p, end, num);
    append_raw(&p, end, "\",\"username\":\"");
    append_escaped(&p, end, username ? username : "");
    append_raw(&p, end, "\",\"hasExtraBuffer\":");
    append_raw(&p, end, (extra_buffer && *extra_buffer) ? "true" : "false");
    append_raw(&p, end, ",\"extraBuffer\":\"");
    append_escaped(&p, end, extra_buffer ? extra_buffer : "");
    append_raw(&p, end, "\",\"primary\":\"");
    append_escaped(&p, end, primary);
    append_raw(&p, end, "\",\"contextA\":\"");
    append_escaped(&p, end, context_a);
    append_raw(&p, end, "\",\"contextB\":\"");
    append_escaped(&p, end, context_b);
    append_raw(&p, end, "\",\"contextC\":\"");
    append_escaped(&p, end, context_c);
    append_raw(&p, end, "\",\"nextExtraBuffer\":\"");
    append_escaped(&p, end, next_extra);
    append_raw(&p, end, "\",\"totalCount\":");
    wsprintfA(num, "%lu", (unsigned long)total_count);
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"itemCount\":");
    wsprintfA(num, "%d", item_count);
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"emittedItemCount\":");
    wsprintfA(num, "%d", emit_count);
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"items\":[");
    for (int i = 0; i < emit_count; i++) {
        int64_t item = item_start + (int64_t)i * 40LL;
        char item_string[256] = {0};
        if (i) append_raw(&p, end, ",");
        __try {
            wx_read_sso_str(item + 8, item_string, sizeof(item_string));
        }
        __except (EXCEPTION_EXECUTE_HANDLER) {
            item_string[0] = 0;
        }
        append_raw(&p, end, "{\"index\":");
        wsprintfA(num, "%d", i + 1);
        append_raw(&p, end, num);
        append_raw(&p, end, ",\"modelDword\":");
        wsprintfA(num, "%d", is_readable_ptr(item, 4) ? r32((void *)item) : 0);
        append_raw(&p, end, num);
        append_raw(&p, end, ",\"modelString\":\"");
        append_escaped(&p, end, item_string);
        append_raw(&p, end, "\"}");
    }
    append_raw(&p, end, "]}");
    append_raw(&p, end, "\r\n");
    *p = 0;
    append_file_bytes(FINDER_USERPAGE_CAPTURE_LOG, json, (DWORD)(p - json));
}

static unsigned __int64 *__fastcall finder_userpage_outer_capture_hook(int64_t service_obj,
                                                                       int64_t model,
                                                                       int64_t *finder_username,
                                                                       int64_t *extra_buffer) {
    FinderUserpageOuterFn original = (FinderUserpageOuterFn)g_finder_userpage_capture.trampoline;
    unsigned __int64 *ret = 0;
    int capture = file_exists(FINDER_USERPAGE_CAPTURE_ENABLE_FILE);
    int64_t caller = (int64_t)_ReturnAddress();
    char username[1024] = {0};
    char extra[4096] = {0};

    if (capture) {
        __try {
            if (is_readable_ptr((int64_t)finder_username, 32)) {
                wx_read_sso_str((int64_t)finder_username, username, sizeof(username));
            }
            if (is_readable_ptr((int64_t)extra_buffer, 32)) {
                wx_read_sso_str((int64_t)extra_buffer, extra, sizeof(extra));
            }
        }
        __except (EXCEPTION_EXECUTE_HANDLER) {
            username[0] = 0;
            extra[0] = 0;
        }
    }

    if (original) {
        ret = original(service_obj, model, finder_username, extra_buffer);
    }

    if (capture) {
        InterlockedIncrement(&g_finder_userpage_capture.capture_count);
        finder_userpage_capture_log(caller, service_obj, model, (int64_t)ret, username, extra);
    }
    return ret;
}

static void install_finder_userpage_capture_hook(void) {
    BYTE *target;
    if (InterlockedCompareExchange(&g_finder_userpage_capture.installed, 0, 0) != 0) return;
    if (!wxb_weixin_module()) return;
    target = (BYTE *)((int64_t)wxb_weixin_module() + WX_OFF_FINDER_USERPAGE_OUTER);
    if (target[0] == 0x48 && target[1] == 0xB8) {
        finder_userpage_capture_restore_stale_if_present();
    }
    if (!bytes_equal(target, FINDER_USERPAGE_OUTER_PROLOGUE, FINDER_USERPAGE_CAPTURE_PATCH_LEN)) {
        finder_userpage_capture_append_literal(
            "finder_userpage_capture: outer prologue mismatch; hook not installed\r\n");
        return;
    }
    copy_bytes(g_finder_userpage_capture.orig, target, FINDER_USERPAGE_CAPTURE_PATCH_LEN);
    if (!build_trampoline(target, FINDER_USERPAGE_CAPTURE_PATCH_LEN, &g_finder_userpage_capture.trampoline)) {
        finder_userpage_capture_append_literal("finder_userpage_capture: trampoline alloc failed\r\n");
        return;
    }
    if (!write_abs_jump(target, FINDER_USERPAGE_CAPTURE_PATCH_LEN, (void *)finder_userpage_outer_capture_hook)) {
        if (g_finder_userpage_capture.trampoline) {
            VirtualFree(g_finder_userpage_capture.trampoline, 0, MEM_RELEASE);
            g_finder_userpage_capture.trampoline = NULL;
        }
        finder_userpage_capture_append_literal("finder_userpage_capture: patch write failed\r\n");
        return;
    }
    InterlockedExchange(&g_finder_userpage_capture.installed, 1);
    finder_userpage_capture_append_literal("finder_userpage_capture: outer hook installed\r\n");
}

void uninstall_finder_userpage_capture_hook(void) {
    BYTE *target;
    DWORD oldp = 0;
    if (InterlockedCompareExchange(&g_finder_userpage_capture.installed, 0, 0) == 0) {
        finder_userpage_capture_restore_stale_if_present();
        return;
    }
    if (wxb_weixin_module()) {
        target = (BYTE *)((int64_t)wxb_weixin_module() + WX_OFF_FINDER_USERPAGE_OUTER);
        if (VirtualProtect(target, FINDER_USERPAGE_CAPTURE_PATCH_LEN, PAGE_EXECUTE_READWRITE, &oldp)) {
            copy_bytes(target, g_finder_userpage_capture.orig, FINDER_USERPAGE_CAPTURE_PATCH_LEN);
            VirtualProtect(target, FINDER_USERPAGE_CAPTURE_PATCH_LEN, oldp, &oldp);
            FlushInstructionCache(GetCurrentProcess(), target, FINDER_USERPAGE_CAPTURE_PATCH_LEN);
        }
    }
    InterlockedExchange(&g_finder_userpage_capture.installed, 0);
    if (g_finder_userpage_capture.trampoline) {
        VirtualFree(g_finder_userpage_capture.trampoline, 0, MEM_RELEASE);
        g_finder_userpage_capture.trampoline = NULL;
    }
    finder_userpage_capture_append_literal("finder_userpage_capture: outer hook uninstalled\r\n");
}

void refresh_finder_userpage_capture_hook(void) {
    if (file_exists(FINDER_USERPAGE_CAPTURE_ENABLE_FILE)) {
        install_finder_userpage_capture_hook();
    } else if (InterlockedCompareExchange(&g_finder_userpage_capture.installed, 0, 0) != 0) {
        uninstall_finder_userpage_capture_hook();
    } else {
        finder_userpage_capture_restore_stale_if_present();
    }
}

int finder_userpage_capture_hook_installed(void) {
    return InterlockedCompareExchange(&g_finder_userpage_capture.installed, 0, 0) != 0;
}

typedef int64_t (*FinderNoArgsFn)(void);

static int64_t finder_alloc_std_string_obj(int64_t base, const char *value) {
    int64_t obj = ((FnCall1)(base + WX_OFF_WX_ALLOC))(32);
    if (!obj) return 0;
    ZeroMemory((void *)obj, 32);
    w64((void *)(obj + 0x18), 15);
    wx_set_std_string_heap(base, obj, value ? value : "");
    return obj;
}

static int finder_build_userpage_request(int64_t base,
                                         BYTE *req,
                                         const char *finder_username,
                                         const char *extra_buffer,
                                         int req_field4,
                                         int req_field6) {
    int64_t msg1;
    int64_t msg2;
    int64_t s3;
    int64_t s7;
    BYTE flags;
    if (!base || !req || !finder_username || !*finder_username) return 0;
    memset(req, 0xAA, 0x58);
    ((FnCall1)(base + WX_OFF_FINDER_USERPAGE_REQ_INIT))((int64_t)req);

    flags = *(BYTE *)(req + 0x54);
    flags |= 1;
    w8(req + 0x54, flags);
    msg1 = r64(req + 0x08);
    if (!msg1) {
        msg1 = ((FnCall1)(base + WX_OFF_WX_ALLOC))(56);
        if (!msg1) return 0;
        ((FnCall1)(base + WX_OFF_FINDER_USERPAGE_BASE_REQ_INIT))(msg1);
        w64(req + 0x08, msg1);
    }
    w8((void *)(msg1 + 48), *(BYTE *)(msg1 + 48) | 0x20);
    w32((void *)(msg1 + 40), 0);

    flags = *(BYTE *)(req + 0x54);
    flags |= 2;
    w8(req + 0x54, flags);
    msg2 = r64(req + 0x10);
    if (!msg2) {
        msg2 = ((FnCall1)(base + WX_OFF_WX_ALLOC))(144);
        if (!msg2) return 0;
        ((FnCall1)(base + WX_OFF_FINDER_USERPAGE_REQ_CTX_INIT))(msg2);
        w64(req + 0x10, msg2);
    }
    w8((void *)(msg2 + 136), *(BYTE *)(msg2 + 136) | 1);
    w32((void *)(msg2 + 8), 0);
    w8((void *)(msg2 + 136), *(BYTE *)(msg2 + 136) | 0x20);
    w64((void *)(msg2 + 40), ((FnCall1)(base + WX_OFF_FINDER_USERPAGE_SESSION_ID))(0));

    s3 = finder_alloc_std_string_obj(base, finder_username);
    if (!s3) return 0;
    w64(req + 0x18, s3);
    w8(req + 0x54, *(BYTE *)(req + 0x54) | 4);

    if (req_field4 < 0) req_field4 = 0;
    if (req_field6 < 0) req_field6 = 1;

    /* Default values match sub_1817CA330's ordinary @finder branch: field4=0, field6=1. */
    w8(req + 0x54, *(BYTE *)(req + 0x54) | 0x28);
    w64(req + 0x28, ((uint64_t)(uint32_t)req_field4) | (((uint64_t)(uint32_t)req_field6) << 32));

    if (extra_buffer && *extra_buffer) {
        s7 = finder_alloc_std_string_obj(base, extra_buffer);
        if (s7) {
            w64(req + 0x30, s7);
            w8(req + 0x54, *(BYTE *)(req + 0x54) | 0x40);
        }
    }
    return 1;
}

static void finder_init_lower_options(BYTE *opt) {
    ZeroMemory(opt, 48);
    w64(opt, 0x1D4C000000000LL);
    w64(opt + 0x20, 15);
}

static void finder_parse_lower_response(FinderUserpageModelTask *task, BYTE *out) {
    int64_t resp;
    int64_t base_resp;
    int64_t base_err;
    DWORD count;
    if (!task || !out) return;
    task->lower_error = r64(out);
    task->lower_net_err = (int)r32(out);
    task->lower_cgi_err = (int)r32(out + 4);
    wx_read_sso_str((int64_t)(out + 8), task->lower_out_string, sizeof(task->lower_out_string));
    task->lower_resp_obj = r64(out + 0x28);
    task->lower_resp_ctl = r64(out + 0x30);
    resp = task->lower_resp_obj;
    if (!is_readable_ptr(resp, 0x88)) return;
    base_resp = r64((void *)(resp + 0x08));
    task->lower_base_response = base_resp;
    if (is_readable_ptr(base_resp, 0x20)) {
        task->lower_base_ret = (int)r32((void *)(base_resp + 0x10));
        task->lower_base_flags = (DWORD)r32((void *)(base_resp + 0x18));
        base_err = r64((void *)(base_resp + 0x08));
        if (is_readable_ptr(base_err, 32)) {
            wx_read_sso_str(base_err, task->lower_base_errmsg, sizeof(task->lower_base_errmsg));
        }
    }
    task->lower_resp_field3 = (DWORD)r32((void *)(resp + 0x30));
    task->lower_resp_field5 = (DWORD)r32((void *)(resp + 0x34));
    task->lower_resp_field6 = (DWORD)r32((void *)(resp + 0x78));
    task->lower_resp_flags = (DWORD)r32((void *)(resp + 0x80));
    task->lower_object_list = r64((void *)(resp + 72));
    count = (DWORD)r32((void *)(resp + 80));
    task->lower_object_count = (count <= 512) ? (int)count : 0;
    task->lower_base_count = (DWORD)r32((void *)(resp + 52));
    task->lower_total_count = task->lower_base_count;
    task->lower_emit_count = task->lower_object_count < FINDER_USERPAGE_ITEM_EMIT_MAX
        ? task->lower_object_count
        : FINDER_USERPAGE_ITEM_EMIT_MAX;
    if (is_readable_ptr(r64((void *)(resp + 112)), 32)) {
        wx_read_sso_str(r64((void *)(resp + 112)), task->lower_next_extra, sizeof(task->lower_next_extra));
    }
    if (task->lower_emit_count > 0 &&
        is_readable_ptr(task->lower_object_list, (SIZE_T)task->lower_emit_count * sizeof(int64_t))) {
        for (int i = 0; i < task->lower_emit_count; i++) {
            int64_t obj = r64((void *)(task->lower_object_list + (int64_t)i * 8LL));
            int64_t field1 = 0;
            int64_t field3 = 0;
            if (!is_readable_ptr(obj, 0x28)) continue;
            field1 = r64((void *)(obj + 8));
            field3 = r64((void *)(obj + 16));
            task->lower_object_field2[i] = (int)r32((void *)(obj + 24));
            if (is_readable_ptr(field1, 32)) {
                wx_read_sso_str(field1, task->lower_object_field1[i],
                                sizeof(task->lower_object_field1[i]));
            }
            if (is_readable_ptr(field3, 32)) {
                wx_read_sso_str(field3, task->lower_object_field3[i],
                                sizeof(task->lower_object_field3[i]));
            }
        }
    }
}

static void finder_cleanup_lower_response(int64_t base, BYTE *out) {
    if (!out) return;
    finder_release_shared_ctl(r64(out + 0x30));
    wx_free_std_string_heap(base, (int64_t)(out + 8));
}

static int build_finder_userpage_queue_asm(BYTE *p, int64_t task_addr, int64_t worker_addr) {
    BYTE *start = p;
    BYTE pro[] = {0x48,0x83,0xEC,0x28};          /* sub rsp, 0x28 */
    BYTE epi[] = {0x48,0x83,0xC4,0x28,0xC3};     /* add rsp, 0x28; ret */
    copy_bytes(p, pro, sizeof(pro)); p += sizeof(pro);
    *p++ = 0x48; *p++ = 0xB9;                    /* mov rcx, imm64 */
    *(int64_t *)p = task_addr; p += 8;
    *p++ = 0x48; *p++ = 0xB8;                    /* mov rax, imm64 */
    *(int64_t *)p = worker_addr; p += 8;
    *p++ = 0xFF; *p++ = 0xD0;                    /* call rax */
    copy_bytes(p, epi, sizeof(epi)); p += sizeof(epi);
    return (int)(p - start);
}

static void finder_userpage_model_queue_worker(int64_t task_addr) {
    FinderUserpageModelTask *task = (FinderUserpageModelTask *)task_addr;
    HMODULE mod;
    int64_t base;
    int64_t root_pair[2] = {0, 0};
    int64_t mid_pair[2] = {0, 0};
    int64_t service_pair[2] = {0, 0};
    BYTE username[32];
    BYTE extra[32];
    BYTE model[0xC0];
    int ok = 0;

    if (!task) return;
    if (InterlockedCompareExchange(&task->status, 2, 0) != 0) return;
    mod = wxb_weixin_module();
    if (!mod) {
        task->call_status = -20;
        InterlockedExchange(&task->status, -20);
        return;
    }
    base = (int64_t)mod;
    ZeroMemory(username, sizeof(username));
    ZeroMemory(extra, sizeof(extra));
    ZeroMemory(model, sizeof(model));
    task->model_addr = (int64_t)model;

    __try {
        task->call_status = -2;
        ((FnCall1)(base + WX_OFF_ROOM_INIT1))((int64_t)root_pair);
        if (!root_pair[0]) {
            task->call_status = -3;
        } else {
            task->call_status = -4;
            ((FnCall3)(base + WX_OFF_ROOM_INIT2))(root_pair[0], (int64_t)mid_pair);
            if (!mid_pair[0]) {
                task->call_status = -5;
            } else {
                task->call_status = -6;
                ((FnCall3)(base + WX_OFF_FINDER_USERPAGE_SERVICE))(mid_pair[0], (int64_t)service_pair);
                if (!service_pair[0]) {
                    task->call_status = -7;
                } else {
                    task->service_obj = service_pair[0];
                    task->call_status = -8;
                    wx_set_std_string_heap(base, (int64_t)username, task->finder_username);
                    wx_set_std_string_heap(base, (int64_t)extra, task->extra_buffer);
                    task->call_ret = ((FnCall4)(base + WX_OFF_FINDER_USERPAGE_OUTER))(
                        service_pair[0],
                        (int64_t)model,
                        (int64_t)username,
                        (int64_t)extra
                    );
                    task->call_status = 1;
                    wx_read_sso_str((int64_t)(model + 0x00), task->primary, sizeof(task->primary));
                    wx_read_sso_str((int64_t)(model + 0x20), task->context_a, sizeof(task->context_a));
                    wx_read_sso_str((int64_t)(model + 0x40), task->context_b, sizeof(task->context_b));
                    wx_read_sso_str((int64_t)(model + 0x60), task->context_c, sizeof(task->context_c));
                    wx_read_sso_str((int64_t)(model + 0x80), task->next_extra, sizeof(task->next_extra));
                    task->total_count = (DWORD)r32(model + 0xA0);
                    task->item_start = r64(model + 0xA8);
                    task->item_end = r64(model + 0xB0);
                    task->item_count = finder_model_vector_count(task->item_start, task->item_end);
                    task->item_emit_count = task->item_count < FINDER_USERPAGE_ITEM_EMIT_MAX
                        ? task->item_count
                        : FINDER_USERPAGE_ITEM_EMIT_MAX;
                    for (int i = 0; i < task->item_emit_count; i++) {
                        int64_t item = task->item_start + (int64_t)i * 40LL;
                        task->item_dwords[i] = r32((void *)item);
                        wx_read_sso_str(item + 8, task->item_strings[i], sizeof(task->item_strings[i]));
                    }
                    ((FnCall1)(base + WX_OFF_FINDER_USERPAGE_MODEL_DTOR))((int64_t)model);
                    ok = 1;
                }
            }
        }
        __try {
            BYTE req[0x58];
            BYTE lower_out[0x40];
            BYTE lower_str[32];
            BYTE lower_opt[48];
            int64_t ctx_root = 0;
            int64_t cgi_ctx = 0;
            int lower_req_ok = 0;
            ZeroMemory(lower_out, sizeof(lower_out));
            ZeroMemory(lower_str, sizeof(lower_str));
            w64(lower_str + 0x18, 15);
            finder_init_lower_options(lower_opt);
            task->lower_call_status = -2;
            lower_req_ok = finder_build_userpage_request(base,
                                                         req,
                                                         task->finder_username,
                                                         task->extra_buffer,
                                                         task->lower_req_field4,
                                                         task->lower_req_field6);
            if (!lower_req_ok) {
                task->lower_call_status = -3;
            } else {
                task->lower_call_status = -4;
                ctx_root = ((FinderNoArgsFn)(base + WX_OFF_FINDER_USERPAGE_CGI_CONTEXT_ROOT))();
                if (ctx_root && is_readable_ptr(ctx_root, 8) && is_readable_ptr(r64((void *)ctx_root), 24)) {
                    cgi_ctx = ((FnCall1)r64((void *)(r64((void *)ctx_root) + 16)))(ctx_root);
                }
                if (!cgi_ctx) {
                    task->lower_call_status = -5;
                } else {
                    task->lower_call_status = -6;
                    task->lower_call_ret = ((FnCall2)(base + WX_OFF_FINDER_USERPAGE_SEND))(
                        (int64_t)lower_out,
                        cgi_ctx,
                        (int64_t)req,
                        (int64_t)lower_str,
                        (int64_t)lower_opt
                    );
                    finder_parse_lower_response(task, lower_out);
                    task->lower_call_status = 1;
                    finder_cleanup_lower_response(base, lower_out);
                }
                ((FnCall1)(base + WX_OFF_FINDER_USERPAGE_REQ_DTOR))((int64_t)req);
            }
        }
        __except (EXCEPTION_EXECUTE_HANDLER) {
            task->lower_call_status = -1;
        }
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
        if (task->call_status >= 0) task->call_status = -1;
        task->call_ret = -1;
    }

    finder_release_shared_pair(service_pair);
    finder_release_shared_pair(mid_pair);
    finder_release_shared_pair(root_pair);
    InterlockedExchange(&task->status, ok ? 1 : (task->call_status < 0 ? task->call_status : -1));
}

static SIZE_T finder_userpage_json_string_cost(const char *value) {
    return value ? (SIZE_T)lstrlenA(value) * 6 + 64 : 64;
}

static int write_finder_userpage_model_result(const char *seq,
                                               int outer_ok,
                                               const char *message,
                                               const char *finder_username,
                                               const char *extra_buffer,
                                               int wait_ms,
                                               int call_status,
                                               int64_t call_ret,
                                               int64_t service_obj,
                                               int64_t model_addr,
                                               const char *primary,
                                               const char *context_a,
                                               const char *context_b,
                                               const char *context_c,
                                               const char *next_extra,
                                               DWORD total_count,
                                               int item_count,
                                               int item_emit_count,
                                               int64_t item_start,
                                               int64_t item_end,
                                               int item_dwords[],
                                               char item_strings[][256],
                                               int lower_call_status,
                                               int64_t lower_error,
                                               int lower_net_err,
                                               int lower_cgi_err,
                                               const char *lower_out_string,
                                               int64_t lower_resp_obj,
                                               int64_t lower_resp_ctl,
                                               int64_t lower_base_response,
                                               int lower_base_ret,
                                               DWORD lower_base_flags,
                                               const char *lower_base_errmsg,
                                               int64_t lower_object_list,
                                               int lower_object_count,
                                               int lower_emit_count,
                                               DWORD lower_total_count,
                                               DWORD lower_base_count,
                                               DWORD lower_resp_field3,
                                               DWORD lower_resp_field5,
                                               DWORD lower_resp_field6,
                                               DWORD lower_resp_flags,
                                               const char *lower_next_extra,
                                               char lower_object_field1[][256],
                                               int lower_object_field2[],
                                               char lower_object_field3[][256]) {
    WCHAR path[1024];
    SIZE_T cap = 65536;
    char *json;
    char num[64];
    char *p;
    char *end;
    const char *cursor;
    const char *result_message = message;
    int lower_transport_ok = (lower_call_status == 1 && lower_net_err == 0);
    int lower_cgi_ok = lower_transport_ok && lower_cgi_err == 0 && lower_base_ret == 0;
    int outer_transport_ok = outer_ok;
    int transport_ok = lower_transport_ok || outer_transport_ok;
    int lower_resp_readable = (lower_resp_obj != 0) &&
        (lower_object_count > 0 || lower_base_count != 0 || lower_resp_field3 != 0 ||
         lower_resp_field5 != 0 || lower_resp_field6 != 0 || lower_resp_flags != 0 ||
         lower_base_response != 0 || lower_base_ret != 0 || lower_base_flags != 0 ||
         (lower_base_errmsg && *lower_base_errmsg) ||
         (lower_next_extra && *lower_next_extra));
    int model_has_data = (item_count > 0) || (total_count != 0) ||
        (primary && *primary) || (context_a && *context_a) ||
        (context_b && *context_b) || (context_c && *context_c) ||
        (next_extra && *next_extra);
    int lower_has_data = lower_cgi_ok &&
        (lower_object_count > 0 || lower_total_count != 0 || lower_base_count != 0 ||
         (lower_next_extra && *lower_next_extra));
    int outer_has_data = outer_ok && model_has_data;
    int data_available = lower_has_data || outer_has_data;
    int result_ok = lower_cgi_ok || outer_has_data;
    int account_exact = result_ok;

    cursor = (lower_cgi_ok && lower_next_extra && *lower_next_extra)
        ? lower_next_extra
        : ((outer_ok && next_extra) ? next_extra : "");
    if (result_ok) {
        result_message = "finder_userpage_model ok";
    } else if (outer_ok) {
        result_message = "finder_userpage_model no usable data";
    }

    cap += finder_userpage_json_string_cost(seq);
    cap += finder_userpage_json_string_cost(result_message);
    cap += finder_userpage_json_string_cost(finder_username);
    cap += finder_userpage_json_string_cost(primary);
    cap += finder_userpage_json_string_cost(context_a);
    cap += finder_userpage_json_string_cost(context_b);
    cap += finder_userpage_json_string_cost(context_c);
    cap += finder_userpage_json_string_cost(next_extra);
    cap += finder_userpage_json_string_cost(lower_out_string);
    cap += finder_userpage_json_string_cost(lower_base_errmsg);
    cap += finder_userpage_json_string_cost(lower_next_extra);
    for (int i = 0; i < item_emit_count; i++) {
        cap += finder_userpage_json_string_cost(item_strings ? item_strings[i] : "") + 256;
    }
    for (int i = 0; i < lower_emit_count; i++) {
        cap += finder_userpage_json_string_cost(lower_object_field1 ? lower_object_field1[i] : "");
        cap += finder_userpage_json_string_cost(lower_object_field3 ? lower_object_field3[i] : "") + 384;
    }
    if (cap > 8 * 1024 * 1024) {
        write_result(seq, 0, "finder_userpage_model", "finder_userpage result too large");
        return 0;
    }
    json = (char *)VirtualAlloc(NULL, cap, MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE);
    if (!json) {
        write_result(seq, 0, "finder_userpage_model", "finder_userpage result alloc failed");
        return 0;
    }
    p = json;
    end = json + cap - 1;
    wsprintfW(path, L"%s\\result_%S.json", CONTROL_DIR, seq ? seq : "");
    append_raw(&p, end, "{\"ok\":");
    append_raw(&p, end, result_ok ? "true" : "false");
    append_raw(&p, end, ",\"seq\":\"");
    append_escaped(&p, end, seq ? seq : "");
    append_raw(&p, end, "\",\"command\":\"finder_userpage_model\",\"path\":\"native_finder_userpage_outer_model\",\"message\":\"");
    append_escaped(&p, end, result_message ? result_message : "");
    append_raw(&p, end, "\",\"finderUsername\":\"");
    append_escaped(&p, end, finder_username ? finder_username : "");
    append_raw(&p, end, "\",\"hasExtraBuffer\":");
    append_raw(&p, end, (extra_buffer && *extra_buffer) ? "true" : "false");
    append_raw(&p, end, ",\"waitMs\":");
    wsprintfA(num, "%d", wait_ms);
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"callStatus\":");
    wsprintfA(num, "%d", call_status);
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"callRet\":");
    wsprintfA(num, "%I64d", (long long)call_ret);
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"serviceObj\":\"0x");
    wsprintfA(num, "%I64x", (unsigned __int64)service_obj);
    append_raw(&p, end, num);
    append_raw(&p, end, "\",\"modelAddr\":\"0x");
    wsprintfA(num, "%I64x", (unsigned __int64)model_addr);
    append_raw(&p, end, num);
    append_raw(&p, end, "\",\"account_list_exact\":");
    append_raw(&p, end, account_exact ? "true" : "false");
    append_raw(&p, end, ",\"totalKnown\":");
    append_raw(&p, end, account_exact ? "true" : "false");
    append_raw(&p, end, ",\"statsExact\":false,\"statsSource\":\"not_in_outer_model\"");
    append_raw(&p, end, ",\"rawResponseAvailable\":");
    append_raw(&p, end, lower_resp_readable ? "true" : "false");
    append_raw(&p, end, ",\"homepage\":{\"username\":\"");
    append_escaped(&p, end, finder_username ? finder_username : "");
    append_raw(&p, end, "\",\"profile\":{\"username\":\"");
    append_escaped(&p, end, finder_username ? finder_username : "");
    append_raw(&p, end, "\",\"parsed\":false},\"feeds\":[");
    if (lower_cgi_ok && lower_emit_count > 0) {
        for (int i = 0; i < lower_emit_count; i++) {
            if (i) append_raw(&p, end, ",");
            append_raw(&p, end, "{\"objectId\":\"");
            append_escaped(&p, end, lower_object_field1 ? lower_object_field1[i] : "");
            append_raw(&p, end, "\",\"objectType\":");
            wsprintfA(num, "%d", lower_object_field2 ? lower_object_field2[i] : 0);
            append_raw(&p, end, num);
            append_raw(&p, end, ",\"objectNonceId\":\"");
            append_escaped(&p, end, lower_object_field3 ? lower_object_field3[i] : "");
            append_raw(&p, end, "\",\"source\":\"response\"}");
        }
    } else if (outer_ok) {
        for (int i = 0; i < item_emit_count; i++) {
            if (i) append_raw(&p, end, ",");
            append_raw(&p, end, "{\"objectId\":\"\",\"objectType\":");
            wsprintfA(num, "%d", item_dwords ? item_dwords[i] : 0);
            append_raw(&p, end, num);
            append_raw(&p, end, ",\"objectNonceId\":\"\",\"modelValue\":\"");
            append_escaped(&p, end, item_strings ? item_strings[i] : "");
            append_raw(&p, end, "\",\"source\":\"model\"}");
        }
    }
    append_raw(&p, end, "],\"cursor\":\"");
    append_escaped(&p, end, cursor);
    append_raw(&p, end, "\",\"hasMore\":");
    append_raw(&p, end, cursor[0] ? "true" : "false");
    append_raw(&p, end, ",\"transportOk\":");
    append_raw(&p, end, transport_ok ? "true" : "false");
    append_raw(&p, end, ",\"lowerTransportOk\":");
    append_raw(&p, end, lower_transport_ok ? "true" : "false");
    append_raw(&p, end, ",\"outerTransportOk\":");
    append_raw(&p, end, outer_transport_ok ? "true" : "false");
    append_raw(&p, end, ",\"dataAvailable\":");
    append_raw(&p, end, data_available ? "true" : "false");
    append_raw(&p, end, "}");
    append_raw(&p, end, ",\"primary\":\"");
    append_escaped(&p, end, primary ? primary : "");
    append_raw(&p, end, "\",\"contextA\":\"");
    append_escaped(&p, end, context_a ? context_a : "");
    append_raw(&p, end, "\",\"contextB\":\"");
    append_escaped(&p, end, context_b ? context_b : "");
    append_raw(&p, end, "\",\"contextC\":\"");
    append_escaped(&p, end, context_c ? context_c : "");
    append_raw(&p, end, "\",\"nextExtraBuffer\":\"");
    append_escaped(&p, end, next_extra ? next_extra : "");
    append_raw(&p, end, "\",\"totalCount\":");
    wsprintfA(num, "%lu", (unsigned long)total_count);
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"itemCount\":");
    wsprintfA(num, "%d", item_count);
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"emittedItemCount\":");
    wsprintfA(num, "%d", item_emit_count);
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"itemsTruncated\":");
    append_raw(&p, end, item_count > item_emit_count ? "true" : "false");
    append_raw(&p, end, ",\"itemVectorStart\":\"0x");
    wsprintfA(num, "%I64x", (unsigned __int64)item_start);
    append_raw(&p, end, num);
    append_raw(&p, end, "\",\"itemVectorEnd\":\"0x");
    wsprintfA(num, "%I64x", (unsigned __int64)item_end);
    append_raw(&p, end, num);
    append_raw(&p, end, "\",\"items\":[");
    for (int i = 0; i < item_emit_count; i++) {
        if (i) append_raw(&p, end, ",");
        append_raw(&p, end, "{\"index\":");
        wsprintfA(num, "%d", i + 1);
        append_raw(&p, end, num);
        append_raw(&p, end, ",\"modelDword\":");
        wsprintfA(num, "%d", item_dwords ? item_dwords[i] : 0);
        append_raw(&p, end, num);
        append_raw(&p, end, ",\"modelString\":\"");
        append_escaped(&p, end, item_strings ? item_strings[i] : "");
        append_raw(&p, end, "\"}");
    }
    append_raw(&p, end, "]");
    append_raw(&p, end, ",\"lower\":{\"callStatus\":");
    wsprintfA(num, "%d", lower_call_status);
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"cgiOk\":");
    append_raw(&p, end, lower_cgi_ok ? "true" : "false");
    append_raw(&p, end, ",\"error\":\"0x");
    wsprintfA(num, "%I64x", (unsigned __int64)lower_error);
    append_raw(&p, end, num);
    append_raw(&p, end, "\",\"netErr\":");
    wsprintfA(num, "%d", lower_net_err);
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"cgiErr\":");
    wsprintfA(num, "%d", lower_cgi_err);
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"outString\":\"");
    append_escaped(&p, end, lower_out_string ? lower_out_string : "");
    append_raw(&p, end, "\",\"respObj\":\"0x");
    wsprintfA(num, "%I64x", (unsigned __int64)lower_resp_obj);
    append_raw(&p, end, num);
    append_raw(&p, end, "\",\"respCtl\":\"0x");
    wsprintfA(num, "%I64x", (unsigned __int64)lower_resp_ctl);
    append_raw(&p, end, num);
    append_raw(&p, end, "\",\"baseResponse\":{\"ptr\":\"0x");
    wsprintfA(num, "%I64x", (unsigned __int64)lower_base_response);
    append_raw(&p, end, num);
    append_raw(&p, end, "\",\"ret\":");
    wsprintfA(num, "%d", lower_base_ret);
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"flags\":\"0x");
    wsprintfA(num, "%lx", (unsigned long)lower_base_flags);
    append_raw(&p, end, num);
    append_raw(&p, end, "\",\"errMsg\":\"");
    append_escaped(&p, end, lower_base_errmsg ? lower_base_errmsg : "");
    append_raw(&p, end, "\"}");
    append_raw(&p, end, ",\"objectList\":\"0x");
    wsprintfA(num, "%I64x", (unsigned __int64)lower_object_list);
    append_raw(&p, end, num);
    append_raw(&p, end, "\",\"objectCount\":");
    wsprintfA(num, "%d", lower_object_count);
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"emittedObjectCount\":");
    wsprintfA(num, "%d", lower_emit_count);
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"totalCount\":");
    wsprintfA(num, "%lu", (unsigned long)lower_total_count);
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"baseCount\":");
    wsprintfA(num, "%lu", (unsigned long)lower_base_count);
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"responseHeader\":{\"field3\":");
    wsprintfA(num, "%lu", (unsigned long)lower_resp_field3);
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"field5\":");
    wsprintfA(num, "%lu", (unsigned long)lower_resp_field5);
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"field6\":");
    wsprintfA(num, "%lu", (unsigned long)lower_resp_field6);
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"flags\":\"0x");
    wsprintfA(num, "%lx", (unsigned long)lower_resp_flags);
    append_raw(&p, end, num);
    append_raw(&p, end, "\"}");
    append_raw(&p, end, ",\"nextExtraBuffer\":\"");
    append_escaped(&p, end, lower_next_extra ? lower_next_extra : "");
    append_raw(&p, end, "\",\"objects\":[");
    for (int i = 0; i < lower_emit_count; i++) {
        if (i) append_raw(&p, end, ",");
        append_raw(&p, end, "{\"index\":");
        wsprintfA(num, "%d", i + 1);
        append_raw(&p, end, num);
        append_raw(&p, end, ",\"objectId\":\"");
        append_escaped(&p, end, lower_object_field1 ? lower_object_field1[i] : "");
        append_raw(&p, end, "\",\"objectType\":");
        wsprintfA(num, "%d", lower_object_field2 ? lower_object_field2[i] : 0);
        append_raw(&p, end, num);
        append_raw(&p, end, ",\"objectString\":\"");
        append_escaped(&p, end, lower_object_field3 ? lower_object_field3[i] : "");
        append_raw(&p, end, "\",\"field1\":\"");
        append_escaped(&p, end, lower_object_field1 ? lower_object_field1[i] : "");
        append_raw(&p, end, "\",\"field2\":");
        wsprintfA(num, "%d", lower_object_field2 ? lower_object_field2[i] : 0);
        append_raw(&p, end, num);
        append_raw(&p, end, ",\"field3\":\"");
        append_escaped(&p, end, lower_object_field3 ? lower_object_field3[i] : "");
        append_raw(&p, end, "\"}");
    }
    append_raw(&p, end, "]}");
    append_raw(&p, end, "}");
    *p = 0;
    wxb_write_file_bytes(path, json, (DWORD)(p - json));
    VirtualFree(json, 0, MEM_RELEASE);
    return result_ok;
}

int finder_userpage_model_to_result(const char *seq,
                                    const char *finder_username,
                                    const char *extra_buffer,
                                    int wait_ms,
                                    int req_field4,
                                    int req_field6) {
    const SIZE_T arena_size = 0x20000;
    BYTE *arena;
    BYTE *asm_addr;
    FinderUserpageModelTask *task;
    int asm_len;
    LONG status;
    int ok = 0;

    if (!seq || !*seq) return 0;
    if (!finder_username || !*finder_username) {
        write_finder_userpage_model_result(seq, 0, "missing_finder_username", finder_username, extra_buffer,
                                           wait_ms, 0, 0, 0, 0, "", "", "", "", "", 0, 0, 0, 0, 0, NULL, NULL,
                                           0, 0, 0, 0, "", 0, 0, 0, 0, 0, "", 0, 0, 0, 0, 0, 0, 0, 0, 0, "", NULL, NULL, NULL);
        return 0;
    }
    if (wait_ms <= 0) wait_ms = 15000;
    if (wait_ms > 60000) wait_ms = 60000;
    if (InterlockedCompareExchange(&g_finder_userpage_outer_inflight, 1, 0) != 0) {
        write_finder_userpage_model_result(seq, 0, "finder_userpage_model_busy", finder_username, extra_buffer,
                                           wait_ms, 0, 0, 0, 0, "", "", "", "", "", 0, 0, 0, 0, 0, NULL, NULL,
                                           0, 0, 0, 0, "", 0, 0, 0, 0, 0, "", 0, 0, 0, 0, 0, 0, 0, 0, 0, "", NULL, NULL, NULL);
        return 0;
    }

    if (!wxb_ensure_weixin_module()) {
        InterlockedExchange(&g_finder_userpage_outer_inflight, 0);
        write_finder_userpage_model_result(seq, 0, "weixin_module_not_found", finder_username, extra_buffer,
                                           wait_ms, 0, 0, 0, 0, "", "", "", "", "", 0, 0, 0, 0, 0, NULL, NULL,
                                           0, 0, 0, 0, "", 0, 0, 0, 0, 0, "", 0, 0, 0, 0, 0, 0, 0, 0, 0, "", NULL, NULL, NULL);
        return 0;
    }

    arena = (BYTE *)VirtualAlloc(NULL, arena_size, MEM_COMMIT | MEM_RESERVE, PAGE_EXECUTE_READWRITE);
    if (!arena) {
        InterlockedExchange(&g_finder_userpage_outer_inflight, 0);
        write_finder_userpage_model_result(seq, 0, "arena_alloc_failed", finder_username, extra_buffer,
                                           wait_ms, 0, 0, 0, 0, "", "", "", "", "", 0, 0, 0, 0, 0, NULL, NULL,
                                           0, 0, 0, 0, "", 0, 0, 0, 0, 0, "", 0, 0, 0, 0, 0, 0, 0, 0, 0, "", NULL, NULL, NULL);
        return 0;
    }
    ZeroMemory(arena, arena_size);
    asm_addr = arena + 0x200;
    task = (FinderUserpageModelTask *)(arena + 0x1000);
    task->status = 0;
    task->wait_ms = wait_ms;
    task->lower_req_field4 = req_field4;
    task->lower_req_field6 = req_field6;
    safe_copy(task->finder_username, sizeof(task->finder_username), finder_username);
    safe_copy(task->extra_buffer, sizeof(task->extra_buffer), extra_buffer ? extra_buffer : "");

    asm_len = build_finder_userpage_queue_asm(asm_addr, (int64_t)task, (int64_t)finder_userpage_model_queue_worker);
    FlushInstructionCache(GetCurrentProcess(), asm_addr, asm_len);
    queue_send((int64_t)asm_addr);
    for (int i = 0; i < wait_ms / 10 + 1; i++) {
        status = InterlockedCompareExchange(&task->status, 0, 0);
        if (status == 1 || status < 0) break;
        Sleep(10);
    }
    status = InterlockedCompareExchange(&task->status, 0, 0);
    if (status == 0) {
        InterlockedCompareExchange(&task->status, -2, 0);
        status = InterlockedCompareExchange(&task->status, 0, 0);
    }
    ok = (status == 1);
    ok = write_finder_userpage_model_result(
        seq,
        ok,
        ok ? "finder_userpage_model ok" : ((status == 2 || status == -2) ? "finder_userpage_model timeout" : "finder_userpage_model failed"),
        finder_username,
        extra_buffer,
        wait_ms,
        task->call_status,
        task->call_ret,
        task->service_obj,
        task->model_addr,
        task->primary,
        task->context_a,
        task->context_b,
        task->context_c,
        task->next_extra,
        task->total_count,
        task->item_count,
        task->item_emit_count,
        task->item_start,
        task->item_end,
        task->item_dwords,
        task->item_strings,
        task->lower_call_status,
        task->lower_error,
        task->lower_net_err,
        task->lower_cgi_err,
        task->lower_out_string,
        task->lower_resp_obj,
        task->lower_resp_ctl,
        task->lower_base_response,
        task->lower_base_ret,
        task->lower_base_flags,
        task->lower_base_errmsg,
        task->lower_object_list,
        task->lower_object_count,
        task->lower_emit_count,
        task->lower_total_count,
        task->lower_base_count,
        task->lower_resp_field3,
        task->lower_resp_field5,
        task->lower_resp_field6,
        task->lower_resp_flags,
        task->lower_next_extra,
        task->lower_object_field1,
        task->lower_object_field2,
        task->lower_object_field3);
    retain_arena(arena);
    InterlockedExchange(&g_finder_userpage_outer_inflight, 0);
    return ok;
}

int finder_userpage_raw_task_to_result(const char *seq,
                                       const char *finder_username,
                                       const char *extra_buffer,
                                       int wait_ms) {
    return finder_userpage_model_to_result(seq, finder_username, extra_buffer, wait_ms, -1, -1);
}
