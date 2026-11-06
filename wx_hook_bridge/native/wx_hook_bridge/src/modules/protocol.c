#include "../include/wx_bridge_protocol.h"
#include "../include/wx_bridge_debug_log.h"
#include "../include/wx_bridge_file_io.h"
#include "../include/wx_bridge_memory.h"
#include "../include/wx_bridge_paths.h"
#include "../include/wx_bridge_text.h"

static WCHAR g_push_host[256] = DIRECT_PUSH_HOST;
static WCHAR g_push_path[1024] = DIRECT_PUSH_PATH;
static char g_push_tcp_host[256] = "127.0.0.1";
static INTERNET_PORT g_push_port = DIRECT_PUSH_PORT;
static DWORD g_push_timeout_ms = 250;
static LONG g_push_secure = 0;
static LONG g_push_transport = 1; /* 1=http, 2=tcp */
static LONG g_receive_push_enabled = 1;

/*
 * Push circuit breaker. When the Python receive-backend is down, every message
 * would otherwise spawn a background worker that stalls ~2s on a doomed connect.
 * Under a busy group that floods the thread pool / socket layer and can delay
 * unrelated WeChat network callbacks (the 1581 red-packet response). After a
 * short streak of failures we fast-fail all pushes for a cooldown, then probe.
 */
static volatile LONG g_push_fail_streak = 0;
static volatile LONG64 g_push_circuit_until = 0; /* GetTickCount64 value; 0 = closed */
#define PUSH_CIRCUIT_TRIP_STREAK 4
#define PUSH_CIRCUIT_COOLDOWN_MS 5000ULL

static int wxb_is_loopback_host_w(const WCHAR *host) {
    if (!host) return 0;
    return lstrcmpiW(host, L"127.0.0.1") == 0 || lstrcmpiW(host, L"::1") == 0;
}

static int wxb_is_loopback_host_a(const char *host) {
    if (!host) return 0;
    return lstrcmpiA(host, "127.0.0.1") == 0;
}

static int wxb_socket_send_all(SOCKET client, const char *data, DWORD len) {
    DWORD sent = 0;
    while (sent < len) {
        int chunk = send(client, data + sent, (int)(len - sent), 0);
        if (chunk == SOCKET_ERROR || chunk == 0) return 0;
        sent += (DWORD)chunk;
    }
    return 1;
}

int wxb_configure_push_ex(const WCHAR *host, INTERNET_PORT port,
                          const WCHAR *path, DWORD timeout_ms, int secure) {
    int host_len;
    int path_len;
    if (!host || !host[0] || !path || path[0] != L'/' || port == 0 ||
        !wxb_is_loopback_host_w(host)) return 0;
    host_len = lstrlenW(host);
    path_len = lstrlenW(path);
    if (host_len >= (int)(sizeof(g_push_host) / sizeof(g_push_host[0])) ||
        path_len >= (int)(sizeof(g_push_path) / sizeof(g_push_path[0]))) {
        return 0;
    }
    CopyMemory(g_push_host, host, (host_len + 1) * sizeof(WCHAR));
    CopyMemory(g_push_path, path, (path_len + 1) * sizeof(WCHAR));
    g_push_port = port;
    g_push_timeout_ms = timeout_ms ? timeout_ms : 250;
    InterlockedExchange(&g_push_secure, secure ? 1 : 0);
    InterlockedExchange(&g_push_transport, 1);
    InterlockedExchange(&g_push_fail_streak, 0);
    InterlockedExchange64(&g_push_circuit_until, 0);
    return 1;
}

int wxb_configure_push(const WCHAR *host, INTERNET_PORT port,
                       const WCHAR *path, DWORD timeout_ms) {
    return wxb_configure_push_ex(host, port, path, timeout_ms, 0);
}

int wxb_configure_push_tcp(const char *host, INTERNET_PORT port,
                           DWORD timeout_ms) {
    size_t host_len;
    if (!host || !host[0] || port == 0 || !wxb_is_loopback_host_a(host)) return 0;
    host_len = lstrlenA(host);
    if (host_len >= sizeof(g_push_tcp_host)) return 0;
    CopyMemory(g_push_tcp_host, host, host_len + 1);
    g_push_port = port;
    g_push_timeout_ms = timeout_ms ? timeout_ms : 250;
    InterlockedExchange(&g_push_transport, 2);
    InterlockedExchange(&g_push_fail_streak, 0);
    InterlockedExchange64(&g_push_circuit_until, 0);
    return 1;
}

void wxb_set_receive_push_enabled(int enabled) {
    InterlockedExchange(&g_receive_push_enabled, enabled ? 1 : 0);
}

int wxb_receive_push_enabled(void) {
    return InterlockedCompareExchange(&g_receive_push_enabled, 0, 0) != 0;
}

static int wxb_post_json_tcp(const char *json, DWORD len) {
    int ok = 0;
    WSADATA wsa_data;
    SOCKET client = INVALID_SOCKET;
    struct sockaddr_in addr;
    DWORD timeout_ms = g_push_timeout_ms;

    if (WSAStartup(MAKEWORD(2, 2), &wsa_data) != 0) return 0;
    client = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
    if (client == INVALID_SOCKET) goto cleanup;
    setsockopt(client, SOL_SOCKET, SO_SNDTIMEO, (const char *)&timeout_ms, sizeof(timeout_ms));
    setsockopt(client, SOL_SOCKET, SO_RCVTIMEO, (const char *)&timeout_ms, sizeof(timeout_ms));

    ZeroMemory(&addr, sizeof(addr));
    addr.sin_family = AF_INET;
    addr.sin_port = htons(g_push_port);
    if (InetPtonA(AF_INET, g_push_tcp_host, &addr.sin_addr) != 1) goto cleanup;
    if (connect(client, (struct sockaddr *)&addr, sizeof(addr)) == SOCKET_ERROR) goto cleanup;
    if (!wxb_socket_send_all(client, json, len)) goto cleanup;
    if (!wxb_socket_send_all(client, "\n", 1)) goto cleanup;
    ok = 1;

cleanup:
    if (client != INVALID_SOCKET) closesocket(client);
    WSACleanup();
    return ok;
}

static int post_json_impl(const char *json, DWORD len, DWORD *http_status) {
    int ok = 0;
    HINTERNET session = NULL, connect = NULL, request = NULL;
    DWORD timeout_ms = g_push_timeout_ms;

    if (http_status) *http_status = 0;
    if (!wxb_receive_push_enabled()) return 0;
    if (InterlockedCompareExchange(&g_push_transport, 0, 0) == 2) {
        ok = wxb_post_json_tcp(json, len);
        if (ok && http_status) *http_status = 200;
        return ok;
    }

    session = WinHttpOpen(L"wx_hook_bridge/1.0", WINHTTP_ACCESS_TYPE_NO_PROXY,
                          WINHTTP_NO_PROXY_NAME, WINHTTP_NO_PROXY_BYPASS, 0);
    if (!session) goto cleanup;
    WinHttpSetTimeouts(session, timeout_ms, timeout_ms, timeout_ms, timeout_ms);

    connect = WinHttpConnect(session, g_push_host, g_push_port, 0);
    if (!connect) goto cleanup;

    request = WinHttpOpenRequest(connect, L"POST", g_push_path, NULL,
                                 WINHTTP_NO_REFERER, WINHTTP_DEFAULT_ACCEPT_TYPES,
                                 InterlockedCompareExchange(&g_push_secure, 0, 0)
                                     ? WINHTTP_FLAG_SECURE
                                     : 0);
    if (!request) goto cleanup;
    WinHttpSetTimeouts(request, timeout_ms, timeout_ms, timeout_ms, timeout_ms);

    if (!WinHttpSendRequest(request, L"Content-Type: application/json\r\n", 32,
                            (LPVOID)json, len, len, 0)) {
        goto cleanup;
    }
    if (!WinHttpReceiveResponse(request, NULL)) goto cleanup;

    DWORD status = 0, status_len = sizeof(status);
    if (WinHttpQueryHeaders(request, WINHTTP_QUERY_STATUS_CODE | WINHTTP_QUERY_FLAG_NUMBER,
                            WINHTTP_HEADER_NAME_BY_INDEX, &status, &status_len,
                            WINHTTP_NO_HEADER_INDEX)) {
        if (http_status) *http_status = status;
        ok = status >= 200 && status < 300;
    }

cleanup:
    if (request) WinHttpCloseHandle(request);
    if (connect) WinHttpCloseHandle(connect);
    if (session) WinHttpCloseHandle(session);
    return ok;
}

int wxb_post_json_to_python_status(const char *json, DWORD len, DWORD *http_status) {
    ULONGLONG now;
    LONG64 until;
    int ok;

    if (http_status) *http_status = 0;
    if (!wxb_receive_push_enabled()) return 0;

    /* Circuit open → skip the doomed connect entirely (returns fast, no worker stall). */
    now = GetTickCount64();
    until = InterlockedCompareExchange64(&g_push_circuit_until, 0, 0);
    if (until && (LONG64)now < until) return 0;

    ok = post_json_impl(json, len, http_status);

    if (ok) {
        InterlockedExchange(&g_push_fail_streak, 0);
        if (until) InterlockedExchange64(&g_push_circuit_until, 0);
    } else if (http_status && *http_status >= 400 && *http_status < 500 &&
               *http_status != 408 && *http_status != 425 && *http_status != 429) {
        /* The backend answered, so this is not a transport outage and must not
         * trip the circuit breaker for unrelated events. */
        InterlockedExchange(&g_push_fail_streak, 0);
        if (until) InterlockedExchange64(&g_push_circuit_until, 0);
    } else if (InterlockedIncrement(&g_push_fail_streak) >= PUSH_CIRCUIT_TRIP_STREAK) {
        InterlockedExchange64(&g_push_circuit_until, (LONG64)(now + PUSH_CIRCUIT_COOLDOWN_MS));
    }
    return ok;
}

int wxb_post_json_to_python(const char *json, DWORD len) {
    return wxb_post_json_to_python_status(json, len, NULL);
}

void wxb_write_status(const char *text) {
    wxb_write_file_bytes(STATUS_FILE, text, (DWORD)lstrlenA(text));
}

int wxb_hb_auto_enabled(void) {
    /* Legacy UI page_ctx auto-open — disabled. Production grab is cold-only. */
    return 0;
}

int wxb_hb_auto_one_shot_enabled(void) {
    return 0;
}

int wxb_hb_noclick_enabled(void) {
    /* Legacy noclick route experiment — disabled. */
    return 0;
}

int wxb_hb_noclick_one_shot_enabled(void) {
    return 0;
}

int wxb_hb_noclick_direct_enabled(void) {
    return 0;
}

int wxb_restore_code_if_changed(BYTE *target, const BYTE *expected, SIZE_T len,
                                const char *msg, int hb_log) {
    DWORD oldp = 0;
    if (!target || !expected || !len) return 0;
    if (wxb_bytes_equal(target, expected, (int)len)) return 0;
    VirtualProtect(target, len, PAGE_EXECUTE_READWRITE, &oldp);
    wxb_copy_bytes(target, expected, (int)len);
    VirtualProtect(target, len, oldp, &oldp);
    FlushInstructionCache(GetCurrentProcess(), target, len);
    if (msg && msg[0]) {
        if (hb_log) wxb_hb_dbg(msg);
        else wxb_write_status(msg);
    }
    return 1;
}

void wxb_write_result(const char *seq, int ok, const char *command, const char *message) {
    if (!seq || !*seq) return;
    WCHAR path[1024];
    char json[4096];
    char *p = json;
    char *end = json + sizeof(json) - 1;
    wsprintfW(path, L"%s\\result_%S.json", CONTROL_DIR, seq);
    wxb_append_raw(&p, end, "{\"ok\":");
    wxb_append_raw(&p, end, ok ? "true" : "false");
    wxb_append_raw(&p, end, ",\"seq\":\"");
    wxb_append_escaped(&p, end, seq);
    wxb_append_raw(&p, end, "\",\"command\":\"");
    wxb_append_escaped(&p, end, command ? command : "");
    wxb_append_raw(&p, end, "\",\"message\":\"");
    wxb_append_escaped(&p, end, message ? message : "");
    wxb_append_raw(&p, end, "\"}");
    *p = 0;
    wxb_write_file_bytes(path, json, (DWORD)(p - json));
}

void wxb_write_forward_result(const char *seq, int ok, const char *message,
                              int source_type, int source_subtype,
                              int variant, int queue_status,
                              int retained, int fallback_kind) {
    if (!seq || !*seq) return;
    WCHAR path[1024];
    char json[4096];
    char num[64];
    const char *fallback = "unknown";
    char *p = json;
    char *end = json + sizeof(json) - 1;
    if (fallback_kind == 1) fallback = "retained";
    else if (fallback_kind == 2) fallback = "hand_built";
    else if (fallback_kind == 3) fallback = "hand_built_blocked";
    else if (fallback_kind == 4) fallback = "retained_model";
    else if (fallback_kind == 5) fallback = "hydrated_retained";
    wsprintfW(path, L"%s\\result_%S.json", CONTROL_DIR, seq);
    wxb_append_raw(&p, end, "{\"ok\":");
    wxb_append_raw(&p, end, ok ? "true" : "false");
    wxb_append_raw(&p, end, ",\"seq\":\"");
    wxb_append_escaped(&p, end, seq);
    wxb_append_raw(&p, end, "\",\"command\":\"forward_text\",\"message\":\"");
    wxb_append_escaped(&p, end, message ? message : "");
    wxb_append_raw(&p, end, "\",\"source_type\":");
    wsprintfA(num, "%d", source_type);
    wxb_append_raw(&p, end, num);
    wxb_append_raw(&p, end, ",\"source_subtype\":");
    wsprintfA(num, "%d", source_subtype);
    wxb_append_raw(&p, end, num);
    wxb_append_raw(&p, end, ",\"variant\":");
    wsprintfA(num, "%d", variant);
    wxb_append_raw(&p, end, num);
    wxb_append_raw(&p, end, ",\"queue_status\":");
    wsprintfA(num, "%d", queue_status);
    wxb_append_raw(&p, end, num);
    wxb_append_raw(&p, end, ",\"retained\":");
    wxb_append_raw(&p, end, retained ? "true" : "false");
    wxb_append_raw(&p, end, ",\"fallback\":\"");
    wxb_append_escaped(&p, end, fallback);
    wxb_append_raw(&p, end, "\",\"fallback_kind\":");
    wsprintfA(num, "%d", fallback_kind);
    wxb_append_raw(&p, end, num);
    if (fallback_kind == 1) {
        wxb_append_raw(&p, end, ",\"retained_source\":\"receive_hook\"");
    } else if (fallback_kind == 5) {
        wxb_append_raw(&p, end, ",\"retained_source\":\"persistent_xml\"");
    }
    wxb_append_raw(&p, end, ",\"native_verified\":");
    wxb_append_raw(&p, end, (ok && (retained || source_type == 1)) ? "true" : "false");
    wxb_append_raw(&p, end, "}");
    *p = 0;
    wxb_write_file_bytes(path, json, (DWORD)(p - json));
}
