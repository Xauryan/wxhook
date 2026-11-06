#include "../include/wx_bridge_realtime_relay.h"

#include "../include/wx_bridge_file_io.h"
#include "../include/wx_bridge_paths.h"
#include "../include/wx_bridge_protocol.h"

#define REALTIME_JOB_POOL_SIZE 256
#define REALTIME_JSON_MAX 131072
#define REALTIME_EVENT_ID_MAX 96
#define REALTIME_RETRY_INITIAL_MS 1000
#define REALTIME_RETRY_MAX_MS 30000
#define REALTIME_RETRY_BATCH 16

typedef struct DECLSPEC_ALIGN(MEMORY_ALLOCATION_ALIGNMENT) RealtimeJob {
    SLIST_ENTRY entry;
    DWORD len;
    char event_id[REALTIME_EVENT_ID_MAX];
    char json[REALTIME_JSON_MAX];
} RealtimeJob;

static SLIST_HEADER g_realtime_free;
static SLIST_HEADER g_realtime_pending;
static RealtimeJob g_realtime_jobs[REALTIME_JOB_POOL_SIZE];
static HANDLE g_realtime_wake = NULL;
static HANDLE g_realtime_thread = NULL;
static volatile LONG g_realtime_running = 0;
static volatile LONG g_realtime_stop_requested = 0;
static volatile LONG g_realtime_stop_owner = 0;
static volatile LONG g_realtime_pending_memory = 0;
static volatile LONG64 g_realtime_enqueued = 0;
static volatile LONG64 g_realtime_persisted = 0;
static volatile LONG64 g_realtime_delivered = 0;
static volatile LONG64 g_realtime_failed = 0;
static volatile LONG64 g_realtime_dropped = 0;
static volatile LONG64 g_realtime_retried = 0;
static ULONGLONG g_realtime_retry_at = 0;
static DWORD g_realtime_retry_delay = REALTIME_RETRY_INITIAL_MS;
static volatile LONG g_realtime_file_seq = 0;

static int realtime_stopping(void) {
    return InterlockedCompareExchange(&g_realtime_stop_requested, 0, 0) != 0;
}

static void realtime_safe_event_id(char *out, DWORD out_len, const char *event_id) {
    DWORD i = 0;
    if (!out || out_len == 0) return;
    if (!event_id) event_id = "event";
    while (*event_id && i + 1 < out_len) {
        unsigned char ch = (unsigned char)*event_id++;
        if ((ch >= 'a' && ch <= 'z') || (ch >= 'A' && ch <= 'Z') ||
            (ch >= '0' && ch <= '9') || ch == '-' || ch == '_') {
            out[i++] = (char)ch;
        } else {
            out[i++] = '_';
        }
    }
    if (i == 0 && out_len > 1) {
        out[i++] = 'e';
    }
    out[i] = 0;
}

static int realtime_queue_path(WCHAR *out, const char *event_id) {
    char safe_id[REALTIME_EVENT_ID_MAX];
    if (!out || !event_id || !event_id[0]) return 0;
    realtime_safe_event_id(safe_id, sizeof(safe_id), event_id);
    if ((DWORD)lstrlenW(REALTIME_QUEUE_DIR) + (DWORD)lstrlenA(safe_id) + 8 >= MAX_PATH) {
        return 0;
    }
    wsprintfW(out, L"%s\\%S.json", REALTIME_QUEUE_DIR, safe_id);
    return 1;
}

static int realtime_write_atomic(const WCHAR *path, const char *data, DWORD len) {
    WCHAR temp[MAX_PATH];
    HANDLE file;
    DWORD written = 0;
    LONG seq;
    int ok = 0;
    if (!path || !data || len == 0) return 0;
    seq = InterlockedIncrement(&g_realtime_file_seq);
    if ((DWORD)lstrlenW(path) + 40 >= MAX_PATH) return 0;
    wsprintfW(temp, L"%s.tmp.%lu.%ld", path, (unsigned long)GetCurrentProcessId(), seq);
    file = CreateFileW(temp, GENERIC_WRITE, FILE_SHARE_READ, NULL, CREATE_ALWAYS,
                       FILE_ATTRIBUTE_NORMAL, NULL);
    if (file == INVALID_HANDLE_VALUE) return 0;
    if (WriteFile(file, data, len, &written, NULL) && written == len && FlushFileBuffers(file)) {
        ok = 1;
    }
    CloseHandle(file);
    if (!ok || !MoveFileExW(temp, path, MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH)) {
        DeleteFileW(temp);
        return 0;
    }
    return 1;
}

static int realtime_persist(const char *event_id, const char *json, DWORD len,
                            WCHAR *path_out) {
    DWORD attr;
    if (!realtime_queue_path(path_out, event_id)) return 0;
    attr = GetFileAttributesW(path_out);
    if (attr != INVALID_FILE_ATTRIBUTES && !(attr & FILE_ATTRIBUTE_DIRECTORY)) return 1;
    if (!realtime_write_atomic(path_out, json, len)) return 0;
    InterlockedIncrement64(&g_realtime_persisted);
    return 1;
}

static void realtime_mirror_failed_event(const char *event_id, const char *json, DWORD len) {
    WCHAR path[MAX_PATH];
    char safe_id[REALTIME_EVENT_ID_MAX];
    realtime_safe_event_id(safe_id, sizeof(safe_id), event_id);
    if ((DWORD)lstrlenW(INBOX_DIR) + (DWORD)lstrlenA(safe_id) + 7 >= MAX_PATH) return;
    wsprintfW(path, L"%s\\%S.txt", INBOX_DIR, safe_id);
    if (!file_exists(path)) write_file_bytes(path, json, len);
}

static void realtime_note_delivery_success(void) {
    InterlockedIncrement64(&g_realtime_delivered);
    g_realtime_retry_at = 0;
    g_realtime_retry_delay = REALTIME_RETRY_INITIAL_MS;
}

static void realtime_note_delivery_failure(void) {
    ULONGLONG now = GetTickCount64();
    InterlockedIncrement64(&g_realtime_failed);
    g_realtime_retry_at = now + g_realtime_retry_delay;
    if (g_realtime_retry_delay < REALTIME_RETRY_MAX_MS / 2) {
        g_realtime_retry_delay *= 2;
    } else {
        g_realtime_retry_delay = REALTIME_RETRY_MAX_MS;
    }
}

static int realtime_delivery_due(void) {
    ULONGLONG now = GetTickCount64();
    return g_realtime_retry_at == 0 || now >= g_realtime_retry_at;
}

static int realtime_quarantine_file(const WCHAR *path, const WCHAR *suffix) {
    WCHAR quarantined[MAX_PATH];
    if (!path || !suffix ||
        (DWORD)lstrlenW(path) + (DWORD)lstrlenW(suffix) + 1 >= MAX_PATH) return 0;
    wsprintfW(quarantined, L"%s%s", path, suffix);
    return MoveFileExW(path, quarantined,
                       MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH) != 0;
}

static int realtime_permanent_http_status(DWORD status) {
    return status >= 400 && status < 500 &&
           status != 408 && status != 425 && status != 429;
}

/* Returns 2 when a permanent backend rejection was quarantined, 1 on delivery,
 * 0 on transient failure, and -1 when backoff skipped the attempt. */
static int realtime_deliver_file(const WCHAR *path, const char *json, DWORD len,
                                 int is_retry) {
    DWORD http_status = 0;
    if (!realtime_delivery_due()) return -1;
    if (is_retry) InterlockedIncrement64(&g_realtime_retried);
    if (wxb_post_json_to_python_status(json, len, &http_status)) {
        DeleteFileW(path);
        realtime_note_delivery_success();
        return 1;
    }
    if (realtime_permanent_http_status(http_status) &&
        realtime_quarantine_file(path, L".rejected")) {
        InterlockedIncrement64(&g_realtime_failed);
        g_realtime_retry_at = 0;
        g_realtime_retry_delay = REALTIME_RETRY_INITIAL_MS;
        return 2;
    }
    realtime_note_delivery_failure();
    return 0;
}

static int realtime_read_file(const WCHAR *path, char *out, DWORD out_len, DWORD *len_out) {
    HANDLE file;
    DWORD size;
    DWORD read = 0;
    if (!path || !out || out_len < 2 || !len_out) return 0;
    file = CreateFileW(path, GENERIC_READ,
                       FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
                       NULL, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, NULL);
    if (file == INVALID_HANDLE_VALUE) return 0;
    size = GetFileSize(file, NULL);
    if (size == INVALID_FILE_SIZE || size == 0 || size >= out_len) {
        CloseHandle(file);
        return 0;
    }
    if (!ReadFile(file, out, size, &read, NULL) || read != size) {
        CloseHandle(file);
        return 0;
    }
    CloseHandle(file);
    out[read] = 0;
    *len_out = read;
    return 1;
}

static LONG realtime_count_disk_queue(void) {
    WCHAR pattern[MAX_PATH];
    WIN32_FIND_DATAW found;
    HANDLE find;
    LONG count = 0;
    if ((DWORD)lstrlenW(REALTIME_QUEUE_DIR) + 8 >= MAX_PATH) return 0;
    wsprintfW(pattern, L"%s\\*.json", REALTIME_QUEUE_DIR);
    find = FindFirstFileW(pattern, &found);
    if (find == INVALID_HANDLE_VALUE) return 0;
    do {
        if (!(found.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY)) count++;
    } while (FindNextFileW(find, &found));
    FindClose(find);
    return count;
}

static void realtime_write_status(void) {
    WxbRealtimeRelayStatus status;
    char json[1024];
    wxb_realtime_relay_get_status(&status);
    wsprintfA(json,
              "{\"running\":%s,\"endpoint\":\"http://127.0.0.1:8765/api/realtime/ingest\","
              "\"enqueued\":%I64d,\"persisted\":%I64d,\"delivered\":%I64d,"
              "\"failed\":%I64d,\"dropped\":%I64d,\"retried\":%I64d,"
              "\"pending_memory\":%ld,\"pending_disk\":%ld}",
              status.running ? "true" : "false",
              status.enqueued, status.persisted, status.delivered,
              status.failed, status.dropped, status.retried,
              status.pending_memory, status.pending_disk);
    write_file_bytes(REALTIME_STATUS_FILE, json, (DWORD)lstrlenA(json));
}

static void realtime_process_job(RealtimeJob *job, int allow_network) {
    WCHAR path[MAX_PATH];
    int delivery = -1;
    if (!realtime_persist(job->event_id, job->json, job->len, path)) {
        InterlockedIncrement64(&g_realtime_failed);
        InterlockedPushEntrySList(&g_realtime_pending, &job->entry);
        InterlockedIncrement(&g_realtime_pending_memory);
        g_realtime_retry_at = GetTickCount64() + REALTIME_RETRY_INITIAL_MS;
        return;
    }
    if (allow_network) delivery = realtime_deliver_file(path, job->json, job->len, 0);
    if (allow_network && delivery != 1) {
        realtime_mirror_failed_event(job->event_id, job->json, job->len);
    }
    InterlockedPushEntrySList(&g_realtime_free, &job->entry);
}

static PSLIST_ENTRY realtime_reverse_entries(PSLIST_ENTRY entry) {
    PSLIST_ENTRY previous = NULL;
    while (entry) {
        PSLIST_ENTRY next = entry->Next;
        entry->Next = previous;
        previous = entry;
        entry = next;
    }
    return previous;
}

static void realtime_drain_memory(int allow_network) {
    /* SLIST pushes are LIFO; reverse the detached batch to preserve hook order. */
    PSLIST_ENTRY entry = realtime_reverse_entries(InterlockedFlushSList(&g_realtime_pending));
    while (entry) {
        PSLIST_ENTRY next = entry->Next;
        RealtimeJob *job = CONTAINING_RECORD(entry, RealtimeJob, entry);
        InterlockedDecrement(&g_realtime_pending_memory);
        realtime_process_job(job, allow_network);
        entry = next;
    }
}

static void realtime_retry_disk_queue(void) {
    WCHAR pattern[MAX_PATH];
    WCHAR path[MAX_PATH];
    WIN32_FIND_DATAW found;
    HANDLE find;
    int processed = 0;
    if (!realtime_delivery_due() || realtime_stopping()) return;
    if ((DWORD)lstrlenW(REALTIME_QUEUE_DIR) + 8 >= MAX_PATH) return;
    wsprintfW(pattern, L"%s\\*.json", REALTIME_QUEUE_DIR);
    find = FindFirstFileW(pattern, &found);
    if (find == INVALID_HANDLE_VALUE) return;
    do {
        char json[REALTIME_JSON_MAX];
        DWORD len = 0;
        int delivered;
        if (realtime_stopping() || processed >= REALTIME_RETRY_BATCH) break;
        if (found.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) continue;
        if ((DWORD)lstrlenW(REALTIME_QUEUE_DIR) + (DWORD)lstrlenW(found.cFileName) + 2 >= MAX_PATH) {
            continue;
        }
        wsprintfW(path, L"%s\\%s", REALTIME_QUEUE_DIR, found.cFileName);
        if (!realtime_read_file(path, json, sizeof(json), &len)) {
            if (realtime_quarantine_file(path, L".invalid")) {
                InterlockedIncrement64(&g_realtime_failed);
                continue;
            }
            realtime_note_delivery_failure();
            break;
        }
        delivered = realtime_deliver_file(path, json, len, 1);
        processed++;
        if (delivered == 2) continue;
        if (delivered != 1) break;
    } while (FindNextFileW(find, &found));
    FindClose(find);
}

static DWORD realtime_wait_ms(void) {
    ULONGLONG now;
    ULONGLONG remaining;
    if (!g_realtime_retry_at) return 1000;
    now = GetTickCount64();
    if (now >= g_realtime_retry_at) return 0;
    remaining = g_realtime_retry_at - now;
    return remaining > 1000 ? 1000 : (DWORD)remaining;
}

static DWORD WINAPI realtime_worker(LPVOID param) {
    ULONGLONG last_status = 0;
    (void)param;
    for (;;) {
        ULONGLONG now;
        realtime_drain_memory(!realtime_stopping());
        if (realtime_stopping()) {
            realtime_drain_memory(0);
            break;
        }
        realtime_retry_disk_queue();
        now = GetTickCount64();
        if (now - last_status >= 1000) {
            realtime_write_status();
            last_status = now;
        }
        WaitForSingleObject(g_realtime_wake, realtime_wait_ms());
    }
    InterlockedExchange(&g_realtime_running, 0);
    realtime_write_status();
    return 0;
}

int wxb_realtime_relay_start(void) {
    int i;
    HANDLE thread;
    if (InterlockedCompareExchange(&g_realtime_running, 1, 0) != 0) return 1;
    InterlockedExchange(&g_realtime_stop_requested, 0);
    InterlockedExchange(&g_realtime_stop_owner, 0);
    InterlockedExchange(&g_realtime_pending_memory, 0);
    InitializeSListHead(&g_realtime_free);
    InitializeSListHead(&g_realtime_pending);
    for (i = 0; i < REALTIME_JOB_POOL_SIZE; i++) {
        InterlockedPushEntrySList(&g_realtime_free, &g_realtime_jobs[i].entry);
    }
    g_realtime_retry_at = 0;
    g_realtime_retry_delay = REALTIME_RETRY_INITIAL_MS;
    g_realtime_wake = CreateEventW(NULL, FALSE, FALSE, NULL);
    if (!g_realtime_wake) {
        InterlockedExchange(&g_realtime_running, 0);
        return 0;
    }
    thread = CreateThread(NULL, 0, realtime_worker, NULL, 0, NULL);
    if (!thread) {
        CloseHandle(g_realtime_wake);
        g_realtime_wake = NULL;
        InterlockedExchange(&g_realtime_running, 0);
        return 0;
    }
    g_realtime_thread = thread;
    realtime_write_status();
    return 1;
}

void wxb_realtime_relay_stop(void) {
    HANDLE thread;
    DWORD wait_result;
    if (InterlockedCompareExchange(&g_realtime_stop_owner, 1, 0) != 0) return;
    thread = g_realtime_thread;
    if (!thread) {
        InterlockedExchange(&g_realtime_stop_owner, 0);
        return;
    }
    InterlockedExchange(&g_realtime_stop_requested, 1);
    if (g_realtime_wake) SetEvent(g_realtime_wake);
    wait_result = WaitForSingleObject(thread, 5000);
    if (wait_result != WAIT_OBJECT_0) {
        write_status("realtime relay stop timeout\n");
        InterlockedExchange(&g_realtime_stop_owner, 0);
        return;
    }
    CloseHandle(thread);
    g_realtime_thread = NULL;
    if (g_realtime_wake) {
        CloseHandle(g_realtime_wake);
        g_realtime_wake = NULL;
    }
    InterlockedExchange(&g_realtime_running, 0);
    InterlockedExchange(&g_realtime_stop_owner, 0);
}

int wxb_realtime_relay_enqueue(const char *event_id, const char *json, DWORD len) {
    PSLIST_ENTRY entry;
    RealtimeJob *job;
    if (!event_id || !event_id[0] || !json || len == 0 || len >= REALTIME_JSON_MAX) {
        InterlockedIncrement64(&g_realtime_dropped);
        return 0;
    }
    if (InterlockedCompareExchange(&g_realtime_running, 0, 0) == 0 || realtime_stopping()) {
        InterlockedIncrement64(&g_realtime_dropped);
        return 0;
    }
    entry = InterlockedPopEntrySList(&g_realtime_free);
    if (!entry) {
        InterlockedIncrement64(&g_realtime_dropped);
        return 0;
    }
    job = CONTAINING_RECORD(entry, RealtimeJob, entry);
    realtime_safe_event_id(job->event_id, sizeof(job->event_id), event_id);
    job->len = len;
    CopyMemory(job->json, json, len);
    job->json[len] = 0;
    InterlockedPushEntrySList(&g_realtime_pending, &job->entry);
    InterlockedIncrement(&g_realtime_pending_memory);
    InterlockedIncrement64(&g_realtime_enqueued);
    if (g_realtime_wake) SetEvent(g_realtime_wake);
    return 1;
}

void wxb_realtime_relay_get_status(WxbRealtimeRelayStatus *status) {
    if (!status) return;
    ZeroMemory(status, sizeof(*status));
    status->enqueued = InterlockedCompareExchange64(&g_realtime_enqueued, 0, 0);
    status->persisted = InterlockedCompareExchange64(&g_realtime_persisted, 0, 0);
    status->delivered = InterlockedCompareExchange64(&g_realtime_delivered, 0, 0);
    status->failed = InterlockedCompareExchange64(&g_realtime_failed, 0, 0);
    status->dropped = InterlockedCompareExchange64(&g_realtime_dropped, 0, 0);
    status->retried = InterlockedCompareExchange64(&g_realtime_retried, 0, 0);
    status->pending_memory = InterlockedCompareExchange(&g_realtime_pending_memory, 0, 0);
    status->pending_disk = realtime_count_disk_queue();
    status->running = InterlockedCompareExchange(&g_realtime_running, 0, 0) != 0;
}
