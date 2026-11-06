#include "../include/wx_bridge_http_server.h"
#include "../include/wx_bridge_debug_log.h"
#include "../include/wx_bridge_http_api.h"
#include "../include/wx_bridge_protocol.h"
#include "../third_party/mongoose.h"

typedef struct HttpServerState {
    volatile LONG running;
    volatile LONG stop_requested;
    HANDLE thread;
} HttpServerState;

static HttpServerState g_http_server = {0};

static DWORD WINAPI http_server_thread(LPVOID param) {
    struct mg_mgr mgr;
    struct mg_connection *listener;
    (void)param;
    mg_mgr_init(&mgr);
    listener = mg_http_listen(&mgr, "http://127.0.0.1:19088", wxb_http_event_handler, NULL);
    if (!listener) {
        write_status("http server listen failed: 127.0.0.1:19088\n");
        InterlockedExchange(&g_http_server.running, 0);
        mg_mgr_free(&mgr);
        return 1;
    }
    write_status("http server started: 127.0.0.1:19088\n");
    while (!InterlockedCompareExchange(&g_http_server.stop_requested, 0, 0)) {
        mg_mgr_poll(&mgr, 100);
    }
    mg_mgr_free(&mgr);
    InterlockedExchange(&g_http_server.running, 0);
    write_status("http server stopped\n");
    return 0;
}

int wxb_http_server_start(void) {
    HANDLE thread;
    if (InterlockedCompareExchange(&g_http_server.running, 1, 0) != 0) return 1;
    InterlockedExchange(&g_http_server.stop_requested, 0);
    thread = CreateThread(NULL, 0, http_server_thread, NULL, 0, NULL);
    if (!thread) {
        InterlockedExchange(&g_http_server.running, 0);
        return 0;
    }
    g_http_server.thread = thread;
    return 1;
}

void wxb_http_server_stop(void) {
    HANDLE thread = g_http_server.thread;
    if (!thread) return;
    InterlockedExchange(&g_http_server.stop_requested, 1);
    WaitForSingleObject(thread, INFINITE);
    CloseHandle(thread);
    g_http_server.thread = NULL;
}

int wxb_http_server_running(void) {
    return InterlockedCompareExchange(&g_http_server.running, 0, 0) != 0;
}
