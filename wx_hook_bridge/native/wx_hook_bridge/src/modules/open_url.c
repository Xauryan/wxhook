/*
 * Module: WeChat built-in browser open-url bridge.
 *
 * This enters the same lower path as xweb_call_open_url without having to
 * synthesize the full JS variant object:
 *   sub_180A82790(request, url_std_string)
 *   sub_18020CB50(0, request)
 */

#include "../include/wx_bridge_open_url.h"
#include "../include/wx_bridge_browser_events.h"
#include "../include/wx_bridge_memory.h"
#include "../include/wx_bridge_offsets.h"
#include "../include/wx_bridge_protocol.h"
#include "../include/wx_bridge_runtime.h"
#include "../include/wx_bridge_text.h"
#include "../include/wx_bridge_wechat_string.h"

#include <string.h>

typedef int64_t (__fastcall *OpenUrlRequestInitFn)(void *request, void *url_std_string);
typedef int64_t (__fastcall *OpenUrlRouteFn)(int64_t context_pair_or_zero, void *request);
typedef int64_t (__fastcall *OpenUrlRequestDtorFn)(void *request);

int open_url_to_result(const char *seq, const char *url) {
    HMODULE mod;
    int64_t base;
    int ok = 0;
    int request_initialized = 0;
    DWORD exception_code = 0;
    int64_t route_ret = 0;
    char msg[256];
    char status[320];
    __declspec(align(16)) BYTE request[WX_OPEN_URL_REQUEST_SIZE];
    __declspec(align(16)) BYTE url_string[32];

    if (!url || !*url) {
        write_result(seq, 0, "open_url", "open_url missing url");
        return 0;
    }

    mod = wxb_ensure_weixin_module();
    if (!mod) {
        write_result(seq, 0, "open_url", "Weixin.dll not loaded");
        return 0;
    }
    base = (int64_t)mod;

    ZeroMemory(request, sizeof(request));
    ZeroMemory(url_string, sizeof(url_string));
    wx_set_std_string_heap(base, (int64_t)url_string, url);
    browser_note_open_url(seq, url);

    __try {
        ((OpenUrlRequestInitFn)(base + WX_OFF_OPEN_URL_REQUEST_INIT))(request, url_string);
        request_initialized = 1;
        route_ret = ((OpenUrlRouteFn)(base + WX_OFF_OPEN_URL_ROUTE))(0, request);
        ok = 1;
    }
    __except (exception_code = (DWORD)GetExceptionInformation()->ExceptionRecord->ExceptionCode,
              EXCEPTION_EXECUTE_HANDLER) {
        ok = 0;
    }

    if (request_initialized) {
        __try {
            ((OpenUrlRequestDtorFn)(base + WX_OFF_OPEN_URL_REQUEST_DTOR))(request);
        }
        __except (EXCEPTION_EXECUTE_HANDLER) {
        }
    }
    wx_free_std_string_heap(base, (int64_t)url_string);

    if (ok) {
        wsprintfA(msg, "open_url submitted ret=%I64d", route_ret);
    } else {
        wsprintfA(msg, "open_url failed exception=0x%08lX", (unsigned long)exception_code);
    }
    wsprintfA(status, "%s\n", msg);
    write_status(status);
    write_result(seq, ok, "open_url", msg);
    return ok;
}
