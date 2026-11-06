#include "../include/wx_bridge_friend_accept.h"
#include "../include/wx_bridge_file_io.h"
#include "../include/wx_bridge_memory.h"
#include "../include/wx_bridge_native_call.h"
#include "../include/wx_bridge_offsets.h"
#include "../include/wx_bridge_paths.h"
#include "../include/wx_bridge_protocol.h"
#include "../include/wx_bridge_receive_hooks.h"
#include "../include/wx_bridge_runtime.h"
#include "../include/wx_bridge_send_runtime.h"
#include "../include/wx_bridge_text.h"
#include "../include/wx_bridge_wechat_string.h"
#include <limits.h>
#include <string.h>

#define FRIEND_USERNAME_MAX 1024
#define FRIEND_TICKET_MAX 4096
#define FRIEND_SEEN_COUNT 128

typedef struct FriendNativeString {
    BYTE storage[16];
    uint64_t length;
    uint64_t capacity;
} FriendNativeString;

typedef struct FriendNativeResult {
    BYTE success;
    BYTE reserved0[3];
    int32_t error_code;
    FriendNativeString message;
    int32_t opcode;
    BYTE reserved1[4];
} FriendNativeResult;

typedef FriendNativeResult *(__fastcall *FnAcceptFriendRequest)(
    int64_t service,
    FriendNativeResult *out,
    FriendNativeString *encrypt_username,
    unsigned int scene,
    int friend_flag,
    FriendNativeString *ticket,
    FriendNativeString *post_accept_field1,
    FriendNativeString *post_accept_field2);

typedef struct FriendParsedRequest {
    char encrypt_username[FRIEND_USERNAME_MAX];
    char ticket[FRIEND_TICKET_MAX];
    int scene;
    int friend_flag;
} FriendParsedRequest;

typedef struct FriendAcceptJob {
    volatile LONG refs;
    HANDLE event;
    volatile LONG done;
    int is_auto;
    int64_t msg_id;
    char encrypt_username[FRIEND_USERNAME_MAX];
    char ticket[FRIEND_TICKET_MAX];
    int scene;
    int friend_flag;
    void *queue_arena;
    DWORD worker_thread_id;
    int phase;
    uint64_t native_function;
    uint64_t context_arg;
    uint64_t out_arg;
    uint64_t username_arg;
    uint64_t ticket_arg;
    uint64_t field1_arg;
    uint64_t field2_arg;
    uint64_t exception_address;
    uint64_t exception_operation;
    uint64_t exception_fault_address;
    WxbFriendAcceptResult result;
} FriendAcceptJob;

typedef char FriendNativeStringSizeCheck[(sizeof(FriendNativeString) == 32) ? 1 : -1];
typedef char FriendNativeResultSizeCheck[(sizeof(FriendNativeResult) == 48) ? 1 : -1];

static volatile LONG g_friend_auto_enabled = 0;
static volatile LONG g_friend_queued = 0;
static volatile LONG g_friend_succeeded = 0;
static volatile LONG g_friend_failed = 0;
static volatile LONG g_friend_parse_failed = 0;
static volatile LONG g_friend_deduplicated = 0;
static SRWLOCK g_friend_call_lock = SRWLOCK_INIT;
static SRWLOCK g_friend_submit_lock = SRWLOCK_INIT;
static SRWLOCK g_friend_seen_lock = SRWLOCK_INIT;
static int64_t g_friend_seen[FRIEND_SEEN_COUNT] = {0};
static unsigned int g_friend_seen_next = 0;

static int friend_ascii_lower(int ch) {
    if (ch >= 'A' && ch <= 'Z') return ch + ('a' - 'A');
    return ch;
}

static int friend_name_equal(const char *a, int a_len, const char *b) {
    int b_len;
    if (!a || !b || a_len < 0) return 0;
    b_len = lstrlenA(b);
    if (a_len != b_len) return 0;
    for (int i = 0; i < a_len; i++) {
        if (friend_ascii_lower((unsigned char)a[i]) !=
            friend_ascii_lower((unsigned char)b[i])) return 0;
    }
    return 1;
}

static int friend_xml_name_char(char ch) {
    return (ch >= 'a' && ch <= 'z') || (ch >= 'A' && ch <= 'Z') ||
           (ch >= '0' && ch <= '9') || ch == '_' || ch == '-' ||
           ch == ':' || ch == '.';
}

static int friend_utf8_put(char *out, int out_max, int *out_len, unsigned int cp) {
    BYTE bytes[4];
    int count;
    if (cp <= 0x7F) {
        bytes[0] = (BYTE)cp;
        count = 1;
    } else if (cp <= 0x7FF) {
        bytes[0] = (BYTE)(0xC0 | (cp >> 6));
        bytes[1] = (BYTE)(0x80 | (cp & 0x3F));
        count = 2;
    } else if (cp <= 0xFFFF && (cp < 0xD800 || cp > 0xDFFF)) {
        bytes[0] = (BYTE)(0xE0 | (cp >> 12));
        bytes[1] = (BYTE)(0x80 | ((cp >> 6) & 0x3F));
        bytes[2] = (BYTE)(0x80 | (cp & 0x3F));
        count = 3;
    } else if (cp <= 0x10FFFF) {
        bytes[0] = (BYTE)(0xF0 | (cp >> 18));
        bytes[1] = (BYTE)(0x80 | ((cp >> 12) & 0x3F));
        bytes[2] = (BYTE)(0x80 | ((cp >> 6) & 0x3F));
        bytes[3] = (BYTE)(0x80 | (cp & 0x3F));
        count = 4;
    } else {
        return 0;
    }
    if (*out_len + count >= out_max) return 0;
    for (int i = 0; i < count; i++) out[(*out_len)++] = (char)bytes[i];
    return 1;
}

static int friend_decode_xml_value(const char *begin, const char *end,
                                   char *out, int out_max) {
    const char *p = begin;
    int n = 0;
    if (!out || out_max <= 0) return 0;
    out[0] = 0;
    while (p < end) {
        if (*p == '\\' && p + 1 < end &&
            (p[1] == '"' || p[1] == '\'' || p[1] == '\\')) {
            if (n + 1 >= out_max) return -1;
            out[n++] = p[1];
            p += 2;
            continue;
        }
        if (*p == '&') {
            if (end - p >= 5 && !strncmp(p, "&amp;", 5)) {
                if (n + 1 >= out_max) return -1;
                out[n++] = '&'; p += 5; continue;
            }
            if (end - p >= 4 && !strncmp(p, "&lt;", 4)) {
                if (n + 1 >= out_max) return -1;
                out[n++] = '<'; p += 4; continue;
            }
            if (end - p >= 4 && !strncmp(p, "&gt;", 4)) {
                if (n + 1 >= out_max) return -1;
                out[n++] = '>'; p += 4; continue;
            }
            if (end - p >= 6 && !strncmp(p, "&quot;", 6)) {
                if (n + 1 >= out_max) return -1;
                out[n++] = '"'; p += 6; continue;
            }
            if (end - p >= 6 && !strncmp(p, "&apos;", 6)) {
                if (n + 1 >= out_max) return -1;
                out[n++] = '\''; p += 6; continue;
            }
            if (p + 3 < end && p[1] == '#') {
                const char *q = p + 2;
                unsigned int cp = 0;
                int base = 10;
                int digits = 0;
                if (q < end && (*q == 'x' || *q == 'X')) {
                    base = 16;
                    q++;
                }
                while (q < end && *q != ';') {
                    int v = -1;
                    if (*q >= '0' && *q <= '9') v = *q - '0';
                    else if (base == 16 && *q >= 'a' && *q <= 'f') v = *q - 'a' + 10;
                    else if (base == 16 && *q >= 'A' && *q <= 'F') v = *q - 'A' + 10;
                    if (v < 0 || v >= base || cp > 0x10FFFFu / (unsigned int)base) break;
                    cp = cp * (unsigned int)base + (unsigned int)v;
                    digits++;
                    q++;
                }
                if (q < end && *q == ';' && digits > 0 && friend_utf8_put(out, out_max, &n, cp)) {
                    p = q + 1;
                    continue;
                }
            }
        }
        if (n + 1 >= out_max) return -1;
        out[n++] = *p++;
    }
    out[n] = 0;
    return 1;
}

static const char *friend_find_msg_tag(const char *xml) {
    const char *p;
    if (!xml) return NULL;
    for (p = xml; *p; p++) {
        const char *name;
        int name_len;
        if (*p != '<') continue;
        name = p + 1;
        while (*name == ' ' || *name == '\t' || *name == '\r' || *name == '\n') name++;
        if (*name == '/' || *name == '?' || *name == '!') continue;
        name_len = 0;
        while (friend_xml_name_char(name[name_len])) name_len++;
        if (friend_name_equal(name, name_len, "msg")) return name + name_len;
    }
    return NULL;
}

/* Returns 1 when found, 0 when absent, and -1 for malformed/truncated input. */
static int friend_xml_attr(const char *xml, const char *wanted, char *out, int out_max) {
    const char *p = friend_find_msg_tag(xml);
    if (!out || out_max <= 0) return -1;
    out[0] = 0;
    if (!p || !wanted || !wanted[0]) return 0;
    for (;;) {
        const char *name;
        const char *value;
        const char *value_end;
        int name_len;
        char quote = 0;
        int escaped_quote = 0;
        while (*p == ' ' || *p == '\t' || *p == '\r' || *p == '\n') p++;
        if (!*p || *p == '>' || (*p == '/' && p[1] == '>')) return 0;
        name = p;
        while (friend_xml_name_char(*p)) p++;
        name_len = (int)(p - name);
        if (!name_len) return -1;
        while (*p == ' ' || *p == '\t' || *p == '\r' || *p == '\n') p++;
        if (*p != '=') {
            while (*p && *p != '>' && *p != ' ' && *p != '\t' && *p != '\r' && *p != '\n') p++;
            continue;
        }
        p++;
        while (*p == ' ' || *p == '\t' || *p == '\r' || *p == '\n') p++;
        if (*p == '\\' && (p[1] == '"' || p[1] == '\'')) {
            escaped_quote = 1;
            quote = p[1];
            p += 2;
        } else if (*p == '"' || *p == '\'') {
            quote = *p++;
        }
        value = p;
        if (quote) {
            if (escaped_quote) {
                while (*p && !(*p == '\\' && p[1] == quote)) p++;
                value_end = p;
                if (!*p) return -1;
                p += 2;
            } else {
                while (*p && *p != quote) {
                    if (*p == '\\' && p[1]) p += 2;
                    else p++;
                }
                value_end = p;
                if (!*p) return -1;
                p++;
            }
        } else {
            while (*p && *p != '>' && *p != '/' &&
                   *p != ' ' && *p != '\t' && *p != '\r' && *p != '\n') p++;
            value_end = p;
        }
        if (friend_name_equal(name, name_len, wanted)) {
            return friend_decode_xml_value(value, value_end, out, out_max);
        }
    }
}

static int friend_parse_int(const char *text, int min_value, int *out) {
    int64_t value = 0;
    int negative = 0;
    const char *p = text;
    if (!p || !*p || !out) return 0;
    if (*p == '-') {
        negative = 1;
        p++;
    }
    if (!*p) return 0;
    while (*p) {
        if (*p < '0' || *p > '9') return 0;
        value = value * 10 + (*p - '0');
        if (value > (int64_t)INT_MAX + (negative ? 1 : 0)) return 0;
        p++;
    }
    if (negative) value = -value;
    if (value < min_value || value > INT_MAX) return 0;
    *out = (int)value;
    return 1;
}

static int friend_parse_xml_request(const char *xml, FriendParsedRequest *request,
                                    char *error, int error_max) {
    char number[64];
    int rc;
    if (error && error_max > 0) error[0] = 0;
    if (!request) return 0;
    ZeroMemory(request, sizeof(*request));
    if (!xml || !xml[0]) {
        safe_copy(error, error_max, "missing_xml");
        return 0;
    }
    rc = friend_xml_attr(xml, "encryptusername", request->encrypt_username,
                         sizeof(request->encrypt_username));
    if (rc == 0) rc = friend_xml_attr(xml, "username", request->encrypt_username,
                                      sizeof(request->encrypt_username));
    if (rc <= 0 || !request->encrypt_username[0]) {
        safe_copy(error, error_max, rc < 0 ? "malformed_encryptusername" : "missing_encryptusername");
        return 0;
    }
    rc = friend_xml_attr(xml, "ticket", request->ticket, sizeof(request->ticket));
    if (rc == 0 || !request->ticket[0]) {
        rc = friend_xml_attr(xml, "antispamticket", request->ticket, sizeof(request->ticket));
    }
    if (rc <= 0 || !request->ticket[0]) {
        safe_copy(error, error_max, rc < 0 ? "malformed_ticket" : "missing_ticket");
        return 0;
    }
    rc = friend_xml_attr(xml, "scene", number, sizeof(number));
    if (rc <= 0 || !friend_parse_int(number, 0, &request->scene)) {
        safe_copy(error, error_max, rc < 0 ? "malformed_scene" : "missing_or_invalid_scene");
        return 0;
    }
    request->friend_flag = 0;
    rc = friend_xml_attr(xml, "friendflag", number, sizeof(number));
    if (rc < 0 || (rc > 0 && !friend_parse_int(number, 0, &request->friend_flag))) {
        safe_copy(error, error_max, "invalid_friendflag");
        return 0;
    }
    return 1;
}

static void friend_result_init(WxbFriendAcceptResult *result) {
    if (!result) return;
    ZeroMemory(result, sizeof(*result));
    result->error_code = -1;
    result->opcode = 3;
}

static int friend_capture_exception(FriendAcceptJob *job,
                                    EXCEPTION_POINTERS *information) {
    EXCEPTION_RECORD *record;
    if (!job || !information || !information->ExceptionRecord) {
        return EXCEPTION_EXECUTE_HANDLER;
    }
    record = information->ExceptionRecord;
    job->result.exception_code = record->ExceptionCode;
    job->exception_address = (uint64_t)record->ExceptionAddress;
    if ((record->ExceptionCode == EXCEPTION_ACCESS_VIOLATION ||
         record->ExceptionCode == EXCEPTION_IN_PAGE_ERROR) &&
        record->NumberParameters >= 2) {
        job->exception_operation = (uint64_t)record->ExceptionInformation[0];
        job->exception_fault_address = (uint64_t)record->ExceptionInformation[1];
    }
    return EXCEPTION_EXECUTE_HANDLER;
}

typedef void (__fastcall *FriendSharedCtlVFunc)(int64_t ctl);

static void friend_release_shared_ctl(int64_t ctl) {
    if (!ctl) return;
    __try {
        int64_t vt = r64((void *)ctl);
        if (!InterlockedDecrement((volatile LONG *)(ctl + 8))) {
            ((FriendSharedCtlVFunc)r64((void *)vt))(ctl);
            if (!InterlockedDecrement((volatile LONG *)(ctl + 12))) {
                ((FriendSharedCtlVFunc)r64((void *)(vt + 8)))(ctl);
            }
        }
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
    }
}

static void friend_release_shared_pair(int64_t *pair) {
    if (!pair) return;
    friend_release_shared_ctl(pair[1]);
    pair[0] = 0;
    pair[1] = 0;
}

static int friend_build_queue_entry(BYTE *entry,
                                    int64_t job_addr,
                                    int64_t worker_addr) {
    BYTE *p = entry;
    static const BYTE prologue[] = {0x48, 0x83, 0xEC, 0x28};
    static const BYTE epilogue[] = {0x48, 0x83, 0xC4, 0x28, 0xC3};
    if (!entry || !job_addr || !worker_addr) return 0;
    copy_bytes(p, prologue, sizeof(prologue)); p += sizeof(prologue);
    *p++ = 0x48; *p++ = 0xB9;
    *(int64_t *)p = job_addr; p += 8;
    *p++ = 0x48; *p++ = 0xB8;
    *(int64_t *)p = worker_addr; p += 8;
    *p++ = 0xFF; *p++ = 0xD0;
    copy_bytes(p, epilogue, sizeof(epilogue)); p += sizeof(epilogue);
    return (int)(p - entry);
}

static int friend_native_string_valid(const FriendNativeString *value) {
    uint64_t length;
    uint64_t capacity;
    int64_t data;
    if (!value) return 0;
    __try {
        length = value->length;
        capacity = value->capacity;
        if (capacity > 0x100000 || length > capacity) return 0;
        if (capacity < 0x10) return capacity == 15 && length <= 15;
        data = r64((void *)value);
        return data && is_readable_ptr(data, (SIZE_T)length + 1);
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
        return 0;
    }
}

static void friend_free_native_string(int64_t base, FriendNativeString *value) {
    if (!base || !value || !friend_native_string_valid(value)) return;
    __try {
        wx_free_std_string_heap(base, (int64_t)value);
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
    }
}

static void friend_run_native(FriendAcceptJob *job) {
    int64_t base;
    int64_t root_pair[2] = {0, 0};
    int64_t mid_pair[2] = {0, 0};
    int64_t service_pair[2] = {0, 0};
    FriendNativeString username;
    FriendNativeString ticket;
    FriendNativeString empty1;
    FriendNativeString empty2;
    FriendNativeResult native_result;
    if (!job) return;
    friend_result_init(&job->result);
    job->worker_thread_id = GetCurrentThreadId();
    base = wxb_weixin_base();
    if (!base) {
        safe_copy(job->result.message, sizeof(job->result.message), "Weixin.dll not loaded");
        return;
    }
    ZeroMemory(&username, sizeof(username));
    ZeroMemory(&ticket, sizeof(ticket));
    ZeroMemory(&empty1, sizeof(empty1));
    ZeroMemory(&empty2, sizeof(empty2));
    memset(&native_result, 0xAA, sizeof(native_result));

    job->native_function = (uint64_t)(base + WX_OFF_ACCEPT_FRIEND_REQUEST);
    job->out_arg = (uint64_t)&native_result;
    job->username_arg = (uint64_t)&username;
    job->ticket_arg = (uint64_t)&ticket;
    job->field1_arg = (uint64_t)&empty1;
    job->field2_arg = (uint64_t)&empty2;

    AcquireSRWLockExclusive(&g_friend_call_lock);
    __try {
        FnAcceptFriendRequest accept_fn =
            (FnAcceptFriendRequest)(base + WX_OFF_ACCEPT_FRIEND_REQUEST);
        job->phase = 1;
        ((FnCall1)(base + WX_OFF_ROOM_INIT1))((int64_t)root_pair);
        if (!root_pair[0]) {
            job->result.error_code = -6;
            safe_copy(job->result.message, sizeof(job->result.message),
                      "friend root service resolution failed");
            __leave;
        }
        job->phase = 2;
        ((FnCall3)(base + WX_OFF_ROOM_INIT2))(root_pair[0], (int64_t)mid_pair);
        if (!mid_pair[0]) {
            job->result.error_code = -7;
            safe_copy(job->result.message, sizeof(job->result.message),
                      "friend stage service resolution failed");
            __leave;
        }
        job->phase = 3;
        ((FnCall3)(base + WX_OFF_FRIEND_SERVICE))(mid_pair[0], (int64_t)service_pair);
        if (!service_pair[0]) {
            job->result.error_code = -8;
            safe_copy(job->result.message, sizeof(job->result.message),
                      "friend service resolution failed");
            __leave;
        }
        job->context_arg = (uint64_t)service_pair[0];
        job->phase = 4;
        wx_set_std_string_heap(base, (int64_t)&username, job->encrypt_username);
        wx_set_std_string_heap(base, (int64_t)&ticket, job->ticket);
        wx_set_std_string_heap(base, (int64_t)&empty1, "");
        wx_set_std_string_heap(base, (int64_t)&empty2, "");
        if (username.length != (uint64_t)lstrlenA(job->encrypt_username) ||
            ticket.length != (uint64_t)lstrlenA(job->ticket)) {
            job->result.error_code = -2;
            safe_copy(job->result.message, sizeof(job->result.message), "WeChat string allocation failed");
        } else {
            job->phase = 5;
            accept_fn(service_pair[0], &native_result, &username,
                      (unsigned int)job->scene, job->friend_flag,
                      &ticket, &empty1, &empty2);
            job->phase = 6;
            job->result.ok = native_result.success == 1 ? 1 : 0;
            job->result.error_code = native_result.error_code;
            job->result.opcode = native_result.opcode;
            if (friend_native_string_valid(&native_result.message)) {
                wx_read_sso_str((int64_t)&native_result.message,
                                job->result.message,
                                sizeof(job->result.message));
            }
            if (!job->result.message[0]) {
                safe_copy(job->result.message, sizeof(job->result.message),
                          job->result.ok ? "friend request accepted" : "verifyuser rejected");
            }
        }
    }
    __except (friend_capture_exception(job, GetExceptionInformation())) {
        job->result.ok = 0;
        job->result.error_code = -3;
        safe_copy(job->result.message, sizeof(job->result.message),
                  "exception in wx_accept_friend_request");
    }
    friend_free_native_string(base, &native_result.message);
    friend_free_native_string(base, &empty2);
    friend_free_native_string(base, &empty1);
    friend_free_native_string(base, &ticket);
    friend_free_native_string(base, &username);
    friend_release_shared_pair(service_pair);
    friend_release_shared_pair(mid_pair);
    friend_release_shared_pair(root_pair);
    ReleaseSRWLockExclusive(&g_friend_call_lock);
}

static void friend_log_job(const FriendAcceptJob *job) {
    char msg_id[32];
    char username[128];
    char detail[2048];
    WCHAR path[1024];
    if (!job) return;
    i64toa10(job->msg_id, msg_id);
    safe_copy(username, sizeof(username), job->encrypt_username);
    wsprintfA(detail,
              "friend_accept: auto=%d msgid=%s tid=%lu phase=%d scene=%d flag=%d ok=%d err=%d opcode=%d seh=0x%08lx exception=%I64x access=%I64u fault=%I64x fn=%I64x ctx=%I64x out=%I64x username_arg=%I64x ticket_arg=%I64x field1=%I64x field2=%I64x user=%s detail=%.400s\r\n",
              job->is_auto, msg_id,
              (unsigned long)job->worker_thread_id, job->phase,
              job->scene, job->friend_flag,
              job->result.ok, job->result.error_code, job->result.opcode,
              (unsigned long)job->result.exception_code,
              (unsigned __int64)job->exception_address,
              (unsigned __int64)job->exception_operation,
              (unsigned __int64)job->exception_fault_address,
              (unsigned __int64)job->native_function,
              (unsigned __int64)job->context_arg,
              (unsigned __int64)job->out_arg,
              (unsigned __int64)job->username_arg,
              (unsigned __int64)job->ticket_arg,
              (unsigned __int64)job->field1_arg,
              (unsigned __int64)job->field2_arg,
              username, job->result.message);
    wsprintfW(path, L"%s\\friend_accept_debug.txt", CONTROL_DIR);
    append_file_bytes(path, detail, (DWORD)lstrlenA(detail));
    write_status(detail);
}

static void friend_job_release(FriendAcceptJob *job) {
    if (!job) return;
    if (InterlockedDecrement(&job->refs) == 0) {
        if (job->event) CloseHandle(job->event);
        HeapFree(GetProcessHeap(), 0, job);
    }
}

static void friend_accept_worker(FriendAcceptJob *job) {
    void *queue_arena;
    if (!job) return;
    queue_arena = job->queue_arena;
    friend_run_native(job);
    if (job->is_auto) {
        if (job->result.ok) InterlockedIncrement(&g_friend_succeeded);
        else InterlockedIncrement(&g_friend_failed);
    }
    friend_log_job(job);
    InterlockedExchange(&job->done, 1);
    if (job->event) SetEvent(job->event);
    friend_job_release(job);
    if (queue_arena) retain_arena(queue_arena);
}

static int friend_submit_job(FriendAcceptJob *job) {
    BYTE *arena;
    BYTE *entry;
    int entry_len;
    int queued;
    if (!job) return 0;
    AcquireSRWLockExclusive(&g_friend_submit_lock);
    if (!async_queue_hook_installed()) install_async_queue_hook();
    if (!async_queue_hook_installed()) {
        ReleaseSRWLockExclusive(&g_friend_submit_lock);
        return 0;
    }
    arena = (BYTE *)VirtualAlloc(NULL, 0x1000,
                                 MEM_COMMIT | MEM_RESERVE,
                                 PAGE_EXECUTE_READWRITE);
    if (!arena) {
        ReleaseSRWLockExclusive(&g_friend_submit_lock);
        return 0;
    }
    ZeroMemory(arena, 0x1000);
    entry = arena + 0x100;
    entry_len = friend_build_queue_entry(entry,
                                         (int64_t)job,
                                         (int64_t)friend_accept_worker);
    if (!entry_len) {
        VirtualFree(arena, 0, MEM_RELEASE);
        ReleaseSRWLockExclusive(&g_friend_submit_lock);
        return 0;
    }
    job->queue_arena = arena;
    FlushInstructionCache(GetCurrentProcess(), entry, (SIZE_T)entry_len);
    queued = queue_send((int64_t)entry);
    if (!queued) {
        job->queue_arena = NULL;
        VirtualFree(arena, 0, MEM_RELEASE);
    }
    ReleaseSRWLockExclusive(&g_friend_submit_lock);
    return queued;
}

static FriendAcceptJob *friend_create_job(const char *encrypt_username,
                                          const char *ticket,
                                          int scene,
                                          int friend_flag,
                                          int is_auto,
                                          int64_t msg_id) {
    FriendAcceptJob *job;
    if (!encrypt_username || !encrypt_username[0] ||
        !ticket || !ticket[0] || scene < 0 || friend_flag < 0) return NULL;
    if (lstrlenA(encrypt_username) >= FRIEND_USERNAME_MAX ||
        lstrlenA(ticket) >= FRIEND_TICKET_MAX) return NULL;
    job = (FriendAcceptJob *)HeapAlloc(GetProcessHeap(), HEAP_ZERO_MEMORY, sizeof(*job));
    if (!job) return NULL;
    job->refs = 1;
    job->is_auto = is_auto ? 1 : 0;
    job->msg_id = msg_id;
    job->scene = scene;
    job->friend_flag = friend_flag;
    safe_copy(job->encrypt_username, sizeof(job->encrypt_username), encrypt_username);
    safe_copy(job->ticket, sizeof(job->ticket), ticket);
    friend_result_init(&job->result);
    return job;
}

int wxb_friend_accept(const char *encrypt_username,
                      const char *ticket,
                      int scene,
                      int friend_flag,
                      int wait_ms,
                      WxbFriendAcceptResult *result) {
    FriendAcceptJob *job;
    DWORD wait_status;
    if (!result) return 0;
    friend_result_init(result);
    if (!encrypt_username || !encrypt_username[0]) {
        safe_copy(result->message, sizeof(result->message), "missing encryptUsername/v3");
        return 0;
    }
    if (!ticket || !ticket[0]) {
        safe_copy(result->message, sizeof(result->message), "missing ticket/v4");
        return 0;
    }
    if (scene < 0 || friend_flag < 0) {
        safe_copy(result->message, sizeof(result->message), "invalid scene/friendFlag");
        return 0;
    }
    if (!wxb_ensure_weixin_module()) {
        safe_copy(result->message, sizeof(result->message), "Weixin.dll not loaded");
        return 0;
    }
    job = friend_create_job(encrypt_username, ticket, scene, friend_flag, 0, 0);
    if (!job) {
        safe_copy(result->message, sizeof(result->message), "friend request input too long or allocation failed");
        return 0;
    }
    job->event = CreateEventW(NULL, TRUE, FALSE, NULL);
    if (!job->event) {
        safe_copy(result->message, sizeof(result->message), "friend accept event creation failed");
        friend_job_release(job);
        return 0;
    }
    job->refs = 2;
    if (!friend_submit_job(job)) {
        safe_copy(result->message, sizeof(result->message), "friend accept worker creation failed");
        job->refs = 1;
        friend_job_release(job);
        return 0;
    }
    if (wait_ms <= 0) wait_ms = 15000;
    if (wait_ms > 60000) wait_ms = 60000;
    wait_status = WaitForSingleObject(job->event, (DWORD)wait_ms);
    if (wait_status == WAIT_OBJECT_0 && InterlockedCompareExchange(&job->done, 0, 0)) {
        *result = job->result;
    } else if (wait_status == WAIT_TIMEOUT) {
        result->error_code = -4;
        safe_copy(result->message, sizeof(result->message),
                  "friend accept timeout; request may still complete");
    } else {
        result->error_code = -5;
        safe_copy(result->message, sizeof(result->message), "friend accept wait failed");
    }
    friend_job_release(job);
    return result->ok;
}

int wxb_friend_accept_xml(const char *xml,
                          int wait_ms,
                          WxbFriendAcceptResult *result) {
    FriendParsedRequest request;
    char error[128];
    if (!result) return 0;
    friend_result_init(result);
    if (!friend_parse_xml_request(xml, &request, error, sizeof(error))) {
        safe_copy(result->message, sizeof(result->message), error);
        return 0;
    }
    return wxb_friend_accept(request.encrypt_username, request.ticket,
                             request.scene, request.friend_flag,
                             wait_ms, result);
}

static int friend_seen_reserve(int64_t msg_id) {
    int found = 0;
    if (!msg_id) return 0;
    AcquireSRWLockExclusive(&g_friend_seen_lock);
    for (int i = 0; i < FRIEND_SEEN_COUNT; i++) {
        if (g_friend_seen[i] == msg_id) {
            found = 1;
            break;
        }
    }
    if (!found) {
        g_friend_seen[g_friend_seen_next % FRIEND_SEEN_COUNT] = msg_id;
        g_friend_seen_next = (g_friend_seen_next + 1) % FRIEND_SEEN_COUNT;
    }
    ReleaseSRWLockExclusive(&g_friend_seen_lock);
    return found ? 0 : 1;
}

static void friend_seen_remove(int64_t msg_id) {
    AcquireSRWLockExclusive(&g_friend_seen_lock);
    for (int i = 0; i < FRIEND_SEEN_COUNT; i++) {
        if (g_friend_seen[i] == msg_id) g_friend_seen[i] = 0;
    }
    ReleaseSRWLockExclusive(&g_friend_seen_lock);
}

void wxb_friend_auto_handle_message(int64_t msg_id,
                                    int msg_type,
                                    int is_incoming,
                                    const char *content) {
    FriendParsedRequest request;
    FriendAcceptJob *job;
    char error[128];
    char log_line[256];
    if (!wxb_friend_auto_enabled() || msg_type != 37 || !is_incoming) return;
    if (!friend_parse_xml_request(content, &request, error, sizeof(error))) {
        InterlockedIncrement(&g_friend_parse_failed);
        wsprintfA(log_line, "friend_accept: auto parse failed msgid=%I64d error=%s\r\n",
                  msg_id, error);
        write_status(log_line);
        return;
    }
    if (!friend_seen_reserve(msg_id)) {
        InterlockedIncrement(&g_friend_deduplicated);
        return;
    }
    job = friend_create_job(request.encrypt_username, request.ticket,
                            request.scene, request.friend_flag, 1, msg_id);
    if (!job) {
        friend_seen_remove(msg_id);
        InterlockedIncrement(&g_friend_failed);
        write_status("friend_accept: auto job creation failed\r\n");
        return;
    }
    InterlockedIncrement(&g_friend_queued);
    if (!friend_submit_job(job)) {
        InterlockedDecrement(&g_friend_queued);
        friend_seen_remove(msg_id);
        InterlockedIncrement(&g_friend_failed);
        friend_job_release(job);
        write_status("friend_accept: auto worker submission failed\r\n");
        return;
    }
}

void wxb_friend_auto_set_enabled(int enabled) {
    InterlockedExchange(&g_friend_auto_enabled, enabled ? 1 : 0);
}

int wxb_friend_auto_enabled(void) {
    return InterlockedCompareExchange(&g_friend_auto_enabled, 0, 0) != 0;
}

void wxb_friend_auto_get_status(WxbFriendAutoStatus *status) {
    if (!status) return;
    status->enabled = wxb_friend_auto_enabled();
    status->queued = InterlockedCompareExchange(&g_friend_queued, 0, 0);
    status->succeeded = InterlockedCompareExchange(&g_friend_succeeded, 0, 0);
    status->failed = InterlockedCompareExchange(&g_friend_failed, 0, 0);
    status->parse_failed = InterlockedCompareExchange(&g_friend_parse_failed, 0, 0);
    status->deduplicated = InterlockedCompareExchange(&g_friend_deduplicated, 0, 0);
}
