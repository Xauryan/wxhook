#include "../include/wx_bridge_debug_log.h"
#include "../include/wx_bridge_file_io.h"
#include "../include/wx_bridge_paths.h"

/*
 * Async debug logging.
 *
 * Hot paths (hongbao cold claim, receive hooks) must NOT open/write files
 * synchronously — bridge/control often sits on a network/mapped drive (Z:),
 * and each CreateFile/WriteFile can cost tens of ms. That alone produces the
 * ~1s "slow grab" feeling versus Frida (async pipe).
 *
 * All hb_dbg / hb_route_dbg / quote / forward lines go into a lock-free-ish
 * ring and are flushed by a background thread.
 */

#define DBG_LINE_MAX   1536
#define DBG_RING_SIZE  256

typedef enum DbgKind {
    DBG_KIND_HB = 0,
    DBG_KIND_HB_ROUTE,
    DBG_KIND_QUOTE,
    DBG_KIND_FORWARD
} DbgKind;

typedef struct DbgEntry {
    volatile LONG ready;
    DbgKind kind;
    char line[DBG_LINE_MAX];
} DbgEntry;

static DbgEntry g_dbg_ring[DBG_RING_SIZE];
static volatile LONG g_dbg_write = 0;
static volatile LONG g_dbg_read = 0;
static CRITICAL_SECTION g_dbg_cs;
static volatile LONG g_dbg_cs_ready = 0;
static HANDLE g_dbg_event = NULL;
static HANDLE g_dbg_thread = NULL;
static volatile LONG g_dbg_stop = 0;
static volatile LONG g_dbg_started = 0;
static volatile LONG g_dbg_stopping = 0;
static volatile LONG g_dbg_lifecycle_lock = 0;
static volatile LONG g_hb_bom_done = 0;
static volatile LONG g_route_bom_done = 0;
static volatile LONG g_quote_bom_done = 0;
static volatile LONG g_forward_bom_done = 0;

static int utf8_safe_copy(char *dst, int dst_max, const char *src) {
    int i = 0;
    int n;
    int full_len;
    if (!dst || dst_max <= 0) return 0;
    dst[0] = 0;
    if (!src) return 0;
    full_len = lstrlenA(src);
    if (full_len <= 0) return 0;
    n = full_len;
    if (n >= dst_max) {
        n = dst_max - 1;
        /* If the first excluded byte is a continuation byte, cut at the lead byte. */
        while (n > 0 && ((unsigned char)src[n] & 0xC0) == 0x80) n--;
    }
    for (i = 0; i < n; i++) dst[i] = src[i];
    dst[n] = 0;
    return n;
}

static void write_utf8_bom_once(const WCHAR *path, volatile LONG *flag) {
    HANDLE h;
    DWORD sz;
    DWORD written = 0;
    static const BYTE bom[3] = { 0xEF, 0xBB, 0xBF };
    if (!path || !flag) return;
    if (InterlockedCompareExchange(flag, 1, 0) != 0) return;
    h = CreateFileW(path, GENERIC_READ | GENERIC_WRITE,
                    FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
                    NULL, OPEN_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
    if (h == INVALID_HANDLE_VALUE) {
        InterlockedExchange(flag, 0);
        return;
    }
    sz = GetFileSize(h, NULL);
    if (sz == 0 || sz == INVALID_FILE_SIZE) {
        SetFilePointer(h, 0, NULL, FILE_BEGIN);
        WriteFile(h, bom, 3, &written, NULL);
    }
    CloseHandle(h);
}

static void append_line_sync(const WCHAR *path, volatile LONG *bom_flag, const char *line) {
    char buf[DBG_LINE_MAX + 4];
    int n;
    if (!path || !line) return;
    write_utf8_bom_once(path, bom_flag);
    n = lstrlenA(line);
    if (n <= 0) return;
    if (n > DBG_LINE_MAX - 3) n = DBG_LINE_MAX - 3;
    CopyMemory(buf, line, (SIZE_T)n);
    buf[n++] = '\r';
    buf[n++] = '\n';
    buf[n] = 0;
    wxb_append_file_bytes(path, buf, (DWORD)n);
}

static void dbg_ensure_worker(void);

static void dbg_lifecycle_acquire(void) {
    while (InterlockedCompareExchange(&g_dbg_lifecycle_lock, 1, 0) != 0) {
        SwitchToThread();
    }
}

static void dbg_lifecycle_release(void) {
    InterlockedExchange(&g_dbg_lifecycle_lock, 0);
}

static void dbg_enqueue(DbgKind kind, const char *line) {
    LONG w, next;
    int fallback_sync = 0;
    if (!line || !line[0]) return;
    if (InterlockedCompareExchange(&g_dbg_stopping, 0, 0)) return;
    dbg_ensure_worker();
    dbg_lifecycle_acquire();
    if (g_dbg_stopping) {
        dbg_lifecycle_release();
        return;
    }
    if (!g_dbg_cs_ready || !g_dbg_thread) {
        fallback_sync = 1;
    }
    if (fallback_sync) {
        /* Fallback sync if worker failed to start. */
        if (kind == DBG_KIND_HB) append_line_sync(HB_DEBUG_LOG, &g_hb_bom_done, line);
        else if (kind == DBG_KIND_HB_ROUTE) append_line_sync(HB_ROUTE_LOG, &g_route_bom_done, line);
        else if (kind == DBG_KIND_QUOTE) append_line_sync(QUOTE_DEBUG_LOG, &g_quote_bom_done, line);
        else append_line_sync(FORWARD_DEBUG_LOG, &g_forward_bom_done, line);
        dbg_lifecycle_release();
        return;
    }
    EnterCriticalSection(&g_dbg_cs);
    w = g_dbg_write;
    next = w + 1;
    if (next >= DBG_RING_SIZE) next = 0;
    if (next == g_dbg_read) {
        /* Ring full: drop oldest slot (advance read) to keep newest diagnostics. */
        LONG r = g_dbg_read + 1;
        if (r >= DBG_RING_SIZE) r = 0;
        g_dbg_read = r;
    }
    g_dbg_ring[w].kind = kind;
    utf8_safe_copy(g_dbg_ring[w].line, DBG_LINE_MAX, line);
    InterlockedExchange(&g_dbg_ring[w].ready, 1);
    g_dbg_write = next;
    LeaveCriticalSection(&g_dbg_cs);
    if (g_dbg_event) SetEvent(g_dbg_event);
    dbg_lifecycle_release();
}

static int dbg_dequeue(DbgEntry *out) {
    LONG r, next;
    if (!out || !g_dbg_cs_ready) return 0;
    EnterCriticalSection(&g_dbg_cs);
    r = g_dbg_read;
    if (r == g_dbg_write) {
        LeaveCriticalSection(&g_dbg_cs);
        return 0;
    }
    *out = g_dbg_ring[r];
    ZeroMemory(&g_dbg_ring[r], sizeof(g_dbg_ring[r]));
    next = r + 1;
    if (next >= DBG_RING_SIZE) next = 0;
    g_dbg_read = next;
    LeaveCriticalSection(&g_dbg_cs);
    return 1;
}

static DWORD WINAPI dbg_worker_main(LPVOID param) {
    (void)param;
    for (;;) {
        DbgEntry e;
        WaitForSingleObject(g_dbg_event, 100);
        while (dbg_dequeue(&e)) {
            if (e.kind == DBG_KIND_HB)
                append_line_sync(HB_DEBUG_LOG, &g_hb_bom_done, e.line);
            else if (e.kind == DBG_KIND_HB_ROUTE)
                append_line_sync(HB_ROUTE_LOG, &g_route_bom_done, e.line);
            else if (e.kind == DBG_KIND_QUOTE)
                append_line_sync(QUOTE_DEBUG_LOG, &g_quote_bom_done, e.line);
            else
                append_line_sync(FORWARD_DEBUG_LOG, &g_forward_bom_done, e.line);
        }
        if (InterlockedCompareExchange(&g_dbg_stop, 0, 0)) break;
    }
    return 0;
}

static void dbg_ensure_worker(void) {
    dbg_lifecycle_acquire();
    if (g_dbg_started || g_dbg_stopping) {
        dbg_lifecycle_release();
        return;
    }
    if (!InterlockedCompareExchange(&g_dbg_cs_ready, 0, 0)) {
        InitializeCriticalSection(&g_dbg_cs);
        InterlockedExchange(&g_dbg_cs_ready, 1);
    }
    g_dbg_event = CreateEventA(NULL, FALSE, FALSE, NULL);
    if (!g_dbg_event) {
        dbg_lifecycle_release();
        return;
    }
    InterlockedExchange(&g_dbg_stop, 0);
    g_dbg_thread = CreateThread(NULL, 0, dbg_worker_main, NULL, 0, NULL);
    if (!g_dbg_thread) {
        CloseHandle(g_dbg_event);
        g_dbg_event = NULL;
        dbg_lifecycle_release();
        return;
    }
    InterlockedExchange(&g_dbg_started, 1);
    SetThreadPriority(g_dbg_thread, THREAD_PRIORITY_BELOW_NORMAL);
    dbg_lifecycle_release();
}

void wxb_debug_log_shutdown(void) {
    DWORD wait_rc;
    dbg_lifecycle_acquire();
    /* Shutdown is terminal: late cold jobs must not restart a logger thread. */
    InterlockedExchange(&g_dbg_stopping, 1);
    if (!g_dbg_started || !g_dbg_thread) {
        dbg_lifecycle_release();
        return;
    }
    InterlockedExchange(&g_dbg_stop, 1);
    if (g_dbg_event) SetEvent(g_dbg_event);
    wait_rc = WaitForSingleObject(g_dbg_thread, 5000);
    if (wait_rc != WAIT_OBJECT_0) {
        dbg_lifecycle_release();
        return;
    }
    CloseHandle(g_dbg_thread);
    g_dbg_thread = NULL;
    if (g_dbg_event) {
        CloseHandle(g_dbg_event);
        g_dbg_event = NULL;
    }
    if (g_dbg_cs_ready) {
        EnterCriticalSection(&g_dbg_cs);
        ZeroMemory(g_dbg_ring, sizeof(g_dbg_ring));
        g_dbg_write = 0;
        g_dbg_read = 0;
        LeaveCriticalSection(&g_dbg_cs);
    }
    InterlockedExchange(&g_dbg_started, 0);
    dbg_lifecycle_release();
}

void wxb_debug_append_line(const WCHAR *path, const char *line) {
    /* Generic sync path kept for non-hot callers (status files etc. use file_io). */
    static volatile LONG s_generic_bom = 0;
    if (!path) return;
    append_line_sync(path, &s_generic_bom, line ? line : "");
}

void wxb_quote_dbg(const char *line) {
    char buf[DBG_LINE_MAX];
    utf8_safe_copy(buf, sizeof(buf), line ? line : "");
    dbg_enqueue(DBG_KIND_QUOTE, buf);
}

void wxb_forward_dbg(const char *line) {
    char buf[DBG_LINE_MAX];
    utf8_safe_copy(buf, sizeof(buf), line ? line : "");
    dbg_enqueue(DBG_KIND_FORWARD, buf);
}

void wxb_hb_dbg(const char *line) {
    char buf[DBG_LINE_MAX];
    char body[DBG_LINE_MAX - 32];
    int prefix;
    utf8_safe_copy(body, (int)sizeof(body), line ? line : "");
    prefix = wsprintfA(buf, "[%lu] ", (unsigned long)GetCurrentThreadId());
    if (prefix < 0) prefix = 0;
    if (prefix >= (int)sizeof(buf)) prefix = (int)sizeof(buf) - 1;
    utf8_safe_copy(buf + prefix, (int)sizeof(buf) - prefix, body);
    dbg_enqueue(DBG_KIND_HB, buf);
}

void wxb_hb_route_dbg(const char *line) {
    char buf[DBG_LINE_MAX];
    char body[DBG_LINE_MAX - 32];
    int prefix;
    utf8_safe_copy(body, (int)sizeof(body), line ? line : "");
    prefix = wsprintfA(buf, "[%lu] ", (unsigned long)GetCurrentThreadId());
    if (prefix < 0) prefix = 0;
    if (prefix >= (int)sizeof(buf)) prefix = (int)sizeof(buf) - 1;
    utf8_safe_copy(buf + prefix, (int)sizeof(buf) - prefix, body);
    dbg_enqueue(DBG_KIND_HB_ROUTE, buf);
}
