/*
 * Module: WeChat/XWeb browser event capture.
 *
 * The dispatcher at WX_OFF_XWEB_EVENT_DISPATCH receives the raw event name and
 * JSON-like payload before WeChat routes it to handlers. Logging that input is
 * the reliable evidence source for page-ready, network-error, and safety-page
 * decisions; opening a URL only proves command submission.
 */

#include "../include/wx_bridge_browser_events.h"
#include "../include/wx_bridge_file_io.h"
#include "../include/wx_bridge_memory.h"
#include "../include/wx_bridge_offsets.h"
#include "../include/wx_bridge_paths.h"
#include "../include/wx_bridge_protocol.h"
#include "../include/wx_bridge_runtime.h"
#include "../include/wx_bridge_text.h"

#define BROWSER_EVENT_PATCH_SIZE 19
#define BROWSER_EVENT_PAYLOAD_LIMIT 12288

typedef int64_t (__fastcall *BrowserEventDispatchFn)(unsigned int widget,
                                                     const char *event,
                                                     const char *payload,
                                                     unsigned int payload_len);

typedef struct BrowserEventHookState {
    void *trampoline;
    BYTE orig[BROWSER_EVENT_PATCH_SIZE];
    volatile LONG installed;
} BrowserEventHookState;

static BrowserEventHookState s_browser_event_hook = {0};
static char s_active_seq[80] = {0};
static char s_active_url[2048] = {0};
static ULONGLONG s_active_tick = 0;

static int browser_event_interesting(const char *event) {
    if (!event || !*event) return 0;
    /* Keep the native side evidence-only. During field discovery we need the
     * full browser callback surface, otherwise page-ready/current-url events
     * can be filtered out before Python ever sees them.
     */
    return wxb_str_has(event, "xweb_call_") ||
           wxb_str_has(event, "wechat_call_") ||
           wxb_str_has(event, "xworker_call_");
}

static void browser_append_escaped_n(char **p, char *end, const char *s, unsigned int n) {
    unsigned int i = 0;
    if (!s) return;
    while (i < n && *p < end - 8) {
        unsigned char c = (unsigned char)s[i++];
        if (!c) break;
        if (c == '\\' || c == '"') {
            *(*p)++ = '\\';
            *(*p)++ = (char)c;
        } else if (c == '\n') {
            *(*p)++ = '\\'; *(*p)++ = 'n';
        } else if (c == '\r') {
            *(*p)++ = '\\'; *(*p)++ = 'r';
        } else if (c == '\t') {
            *(*p)++ = '\\'; *(*p)++ = 't';
        } else if (c < 0x20) {
            static const char hex[] = "0123456789abcdef";
            *(*p)++ = '\\'; *(*p)++ = 'u'; *(*p)++ = '0'; *(*p)++ = '0';
            *(*p)++ = hex[(c >> 4) & 0xF];
            *(*p)++ = hex[c & 0xF];
        } else {
            *(*p)++ = (char)c;
        }
    }
}

static void browser_append_u64(char **p, char *end, unsigned __int64 value) {
    char tmp[32];
    char out[32];
    int i = 0;
    int j = 0;
    if (!value) {
        wxb_append_raw(p, end, "0");
        return;
    }
    while (value && i < (int)sizeof(tmp)) {
        tmp[i++] = (char)('0' + (value % 10));
        value /= 10;
    }
    while (i) out[j++] = tmp[--i];
    out[j] = 0;
    wxb_append_raw(p, end, out);
}

static void browser_log_event(unsigned int widget, const char *event,
                              const char *payload, unsigned int payload_len) {
    char line[15000];
    char *p = line;
    char *end = line + sizeof(line) - 1;
    unsigned int emit_len = payload_len;
    ULONGLONG now = GetTickCount64();

    if (!browser_event_interesting(event)) return;
    if (emit_len > BROWSER_EVENT_PAYLOAD_LIMIT) emit_len = BROWSER_EVENT_PAYLOAD_LIMIT;

    wxb_append_raw(&p, end, "{\"event\":\"");
    wxb_append_escaped(&p, end, event ? event : "");
    wxb_append_raw(&p, end, "\",\"widget\":");
    browser_append_u64(&p, end, widget);
    wxb_append_raw(&p, end, ",\"pid\":");
    browser_append_u64(&p, end, GetCurrentProcessId());
    wxb_append_raw(&p, end, ",\"tid\":");
    browser_append_u64(&p, end, GetCurrentThreadId());
    wxb_append_raw(&p, end, ",\"tickMs\":");
    browser_append_u64(&p, end, now);
    wxb_append_raw(&p, end, ",\"payloadLen\":");
    browser_append_u64(&p, end, payload_len);
    wxb_append_raw(&p, end, ",\"truncated\":");
    wxb_append_raw(&p, end, emit_len < payload_len ? "true" : "false");
    wxb_append_raw(&p, end, ",\"activeSeq\":\"");
    wxb_append_escaped(&p, end, s_active_seq);
    wxb_append_raw(&p, end, "\",\"activeUrl\":\"");
    wxb_append_escaped(&p, end, s_active_url);
    wxb_append_raw(&p, end, "\",\"activeAgeMs\":");
    browser_append_u64(&p, end, s_active_tick ? (now - s_active_tick) : 0);
    wxb_append_raw(&p, end, ",\"payload\":\"");
    browser_append_escaped_n(&p, end, payload, emit_len);
    wxb_append_raw(&p, end, "\"}\r\n");
    *p = 0;
    wxb_append_file_bytes(BROWSER_EVENT_LOG, line, (DWORD)(p - line));
}

void wxb_browser_note_open_url(const char *seq, const char *url) {
    wxb_safe_copy(s_active_seq, sizeof(s_active_seq), seq ? seq : "");
    wxb_safe_copy(s_active_url, sizeof(s_active_url), url ? url : "");
    s_active_tick = GetTickCount64();
    browser_log_event(0, "bridge_open_url_submitted", url ? url : "", (unsigned int)lstrlenA(url ? url : ""));
}

static int build_browser_event_trampoline(BYTE *p, BYTE *target) {
    BYTE *start = p;
    wxb_copy_bytes(p, s_browser_event_hook.orig, sizeof(s_browser_event_hook.orig));
    p += sizeof(s_browser_event_hook.orig);
    *p++ = 0x48; *p++ = 0xB8;
    *(int64_t *)p = (int64_t)(target + sizeof(s_browser_event_hook.orig)); p += 8;
    *p++ = 0xFF; *p++ = 0xE0;
    return (int)(p - start);
}

static int browser_event_target_shape_ok(BYTE *target) {
    if (!target) return 0;
    if (target[0] == 0x48 && target[1] == 0xB8) return 0;
    if (target[0] != 0x55) return 0;
    if (!(target[12] == 0x48 && target[13] == 0x81 && target[14] == 0xEC)) return 0;
    return 1;
}

int64_t __fastcall browser_event_dispatch_hook(unsigned int widget,
                                               const char *event,
                                               const char *payload,
                                               unsigned int payload_len) {
    BrowserEventDispatchFn original = (BrowserEventDispatchFn)s_browser_event_hook.trampoline;
    __try {
        browser_log_event(widget, event, payload, payload_len);
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
    }
    if (!original) return 0;
    return original(widget, event, payload, payload_len);
}

void wxb_install_browser_event_hook(void) {
    BYTE *target;
    BYTE patch[BROWSER_EVENT_PATCH_SIZE];
    DWORD old = 0;
    int tlen;
    if (s_browser_event_hook.installed || !wxb_weixin_module()) return;
    target = (BYTE *)((int64_t)wxb_weixin_module() + WX_OFF_XWEB_EVENT_DISPATCH);
    if (!browser_event_target_shape_ok(target)) {
        write_status("browser event hook skipped: unexpected target prologue\n");
        return;
    }
    wxb_copy_bytes(s_browser_event_hook.orig, target, sizeof(s_browser_event_hook.orig));
    s_browser_event_hook.trampoline = wxb_alloc_near(target, 256);
    if (!s_browser_event_hook.trampoline) {
        s_browser_event_hook.trampoline = VirtualAlloc(NULL, 256, MEM_COMMIT | MEM_RESERVE, PAGE_EXECUTE_READWRITE);
    }
    if (!s_browser_event_hook.trampoline) {
        write_status("browser event hook alloc failed\n");
        return;
    }
    tlen = build_browser_event_trampoline((BYTE *)s_browser_event_hook.trampoline, target);
    FlushInstructionCache(GetCurrentProcess(), s_browser_event_hook.trampoline, tlen);

    for (int i = 0; i < (int)sizeof(patch); i++) patch[i] = 0x90;
    patch[0] = 0x48; patch[1] = 0xB8;
    *(int64_t *)(patch + 2) = (int64_t)browser_event_dispatch_hook;
    patch[10] = 0xFF; patch[11] = 0xE0;

    VirtualProtect(target, sizeof(patch), PAGE_EXECUTE_READWRITE, &old);
    wxb_copy_bytes(target, patch, sizeof(patch));
    VirtualProtect(target, sizeof(patch), old, &old);
    FlushInstructionCache(GetCurrentProcess(), target, sizeof(patch));
    InterlockedExchange(&s_browser_event_hook.installed, 1);
    write_status("browser event hook installed\n");
}

void wxb_uninstall_browser_event_hook(void) {
    BYTE *target;
    DWORD old = 0;
    if (!s_browser_event_hook.installed || !wxb_weixin_module()) return;
    target = (BYTE *)((int64_t)wxb_weixin_module() + WX_OFF_XWEB_EVENT_DISPATCH);
    VirtualProtect(target, sizeof(s_browser_event_hook.orig), PAGE_EXECUTE_READWRITE, &old);
    wxb_copy_bytes(target, s_browser_event_hook.orig, sizeof(s_browser_event_hook.orig));
    VirtualProtect(target, sizeof(s_browser_event_hook.orig), old, &old);
    FlushInstructionCache(GetCurrentProcess(), target, sizeof(s_browser_event_hook.orig));
    InterlockedExchange(&s_browser_event_hook.installed, 0);
    if (s_browser_event_hook.trampoline) {
        VirtualFree(s_browser_event_hook.trampoline, 0, MEM_RELEASE);
        s_browser_event_hook.trampoline = NULL;
    }
}

int wxb_browser_event_hook_installed(void) {
    return InterlockedCompareExchange(&s_browser_event_hook.installed, 0, 0) != 0;
}
