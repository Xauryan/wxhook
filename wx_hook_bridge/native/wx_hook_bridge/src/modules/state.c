/*
 * Process runtime state for wx_hook_bridge.
 */

#include "../include/wx_bridge_runtime.h"
#include "../include/wx_bridge_text.h"

static HINSTANCE g_runtime_instance = NULL;
static HMODULE g_runtime_weixin = NULL;
static char g_runtime_self_wxid[128] = {0};

HINSTANCE wxb_bridge_instance(void) {
    return g_runtime_instance;
}

void wxb_set_bridge_instance(HINSTANCE instance) {
    g_runtime_instance = instance;
}

HMODULE wxb_weixin_module(void) {
    return g_runtime_weixin;
}

HMODULE wxb_ensure_weixin_module(void) {
    if (!g_runtime_weixin) {
        g_runtime_weixin = GetModuleHandleW(L"Weixin.dll");
    }
    if (!g_runtime_weixin) {
        /* BetterWX coexistence copies name the main DLL after the executable. */
        WCHAR path[MAX_PATH];
        DWORD length = GetModuleFileNameW(NULL, path, MAX_PATH);
        if (length > 4 && length < MAX_PATH &&
            lstrcmpiW(path + length - 4, L".exe") == 0) {
            WCHAR *name = path;
            for (DWORD i = 0; i < length; i++) {
                if (path[i] == L'\\') name = path + i + 1;
            }
            lstrcpyW(path + length - 4, L".dll");
            g_runtime_weixin = GetModuleHandleW(name);
        }
    }
    return g_runtime_weixin;
}

int64_t wxb_weixin_base(void) {
    return (int64_t)wxb_weixin_module();
}

BYTE *wxb_weixin_addr(DWORD rva) {
    HMODULE module = wxb_weixin_module();
    return module ? (BYTE *)module + rva : NULL;
}

const char *wxb_self_wxid(void) {
    return g_runtime_self_wxid;
}

int wxb_self_wxid_known(void) {
    return g_runtime_self_wxid[0] != 0;
}

void wxb_copy_self_wxid(char *out, int max) {
    safe_copy(out, max, g_runtime_self_wxid);
}

void wxb_learn_self_wxid(const char *wxid) {
    if (g_runtime_self_wxid[0] || !is_wxid_like(wxid) || is_chatroom_id(wxid)) return;
    safe_copy(g_runtime_self_wxid, sizeof(g_runtime_self_wxid), wxid);
}
