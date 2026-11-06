#include "../include/wx_bridge_worker_control.h"
#include "../include/wx_bridge_file_io.h"
#include "../include/wx_bridge_paths.h"
#include "../include/wx_bridge_runtime.h"
#include "../include/wx_bridge_text.h"

static char g_worker_owner_token[128] = {0};

HANDLE wxb_acquire_worker_handoff_lock(void) {
    HANDLE h = CreateMutexW(NULL, FALSE, L"Local\\wx_hook_bridge_hook_handoff_4_1_9_23");
    DWORD wait_rc;
    if (!h) return NULL;
    wait_rc = WaitForSingleObject(h, 10000);
    if (wait_rc != WAIT_OBJECT_0 && wait_rc != WAIT_ABANDONED) {
        CloseHandle(h);
        return NULL;
    }
    return h;
}

void wxb_release_worker_handoff_lock(HANDLE h) {
    if (!h) return;
    ReleaseMutex(h);
    CloseHandle(h);
}

void wxb_write_worker_owner_token(void) {
    HANDLE lock = wxb_acquire_worker_handoff_lock();
    char token[128];
    if (!lock) return;
    wsprintfA(token, "%lu:%I64x:%I64x",
              (unsigned long)GetCurrentProcessId(),
              (unsigned __int64)(int64_t)wxb_weixin_module(),
              (unsigned __int64)(int64_t)wxb_bridge_instance());
    wxb_safe_copy(g_worker_owner_token, sizeof(g_worker_owner_token), token);
    wxb_write_file_bytes(WORKER_OWNER_FILE, token, (DWORD)lstrlenA(token));
    wxb_release_worker_handoff_lock(lock);
}

int wxb_worker_owner_still_current(void) {
    HANDLE lock = wxb_acquire_worker_handoff_lock();
    char cur[160] = {0};
    int n;
    int ok;
    if (!lock) return 0;
    if (!g_worker_owner_token[0]) {
        wxb_release_worker_handoff_lock(lock);
        return 1;
    }
    n = wxb_read_file_bytes(WORKER_OWNER_FILE, cur, sizeof(cur));
    if (n <= 0) {
        wxb_release_worker_handoff_lock(lock);
        return 1;
    }
    wxb_trim_trailing_crlf(cur);
    ok = wxb_str_eq(cur, g_worker_owner_token);
    wxb_release_worker_handoff_lock(lock);
    return ok;
}

void wxb_clear_worker_owner_token_if_current(void) {
    HANDLE lock = wxb_acquire_worker_handoff_lock();
    char cur[160] = {0};
    int n;
    if (!lock) return;
    if (!g_worker_owner_token[0]) {
        wxb_release_worker_handoff_lock(lock);
        return;
    }
    n = wxb_read_file_bytes(WORKER_OWNER_FILE, cur, sizeof(cur));
    if (n <= 0) {
        wxb_release_worker_handoff_lock(lock);
        return;
    }
    wxb_trim_trailing_crlf(cur);
    if (!wxb_str_eq(cur, g_worker_owner_token)) {
        wxb_release_worker_handoff_lock(lock);
        return;
    }
    wxb_write_file_bytes(WORKER_OWNER_FILE, "", 0);
    g_worker_owner_token[0] = 0;
    wxb_release_worker_handoff_lock(lock);
}

void wxb_clear_outbox(void) {
    wxb_write_file_bytes(OUTBOX_FILE, "", 0);
}
