#include "../include/wx_bridge_file_io.h"
#include "../include/wx_bridge_paths.h"

int wxb_file_exists(const WCHAR *path) {
    DWORD attr = GetFileAttributesW(path);
    return attr != INVALID_FILE_ATTRIBUTES && !(attr & FILE_ATTRIBUTE_DIRECTORY);
}

void wxb_ensure_dirs(void) {
    CreateDirectoryW(BRIDGE_DIR, NULL);
    CreateDirectoryW(INBOX_DIR, NULL);
    CreateDirectoryW(OUTBOX_DIR, NULL);
    CreateDirectoryW(CONTROL_DIR, NULL);
    CreateDirectoryW(REALTIME_QUEUE_DIR, NULL);
    CreateDirectoryW(REVOKE_CACHE_DIR, NULL);
}

void wxb_write_file_bytes(const WCHAR *path, const char *data, DWORD len) {
    HANDLE h = CreateFileW(path, GENERIC_WRITE, FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
                           NULL, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
    if (h == INVALID_HANDLE_VALUE) return;
    DWORD written = 0;
    WriteFile(h, data, len, &written, NULL);
    CloseHandle(h);
}

void wxb_append_file_bytes(const WCHAR *path, const char *data, DWORD len) {
    HANDLE h = CreateFileW(path, FILE_APPEND_DATA, FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
                           NULL, OPEN_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
    if (h == INVALID_HANDLE_VALUE) return;
    DWORD written = 0;
    WriteFile(h, data, len, &written, NULL);
    CloseHandle(h);
}

int wxb_read_file_bytes(const WCHAR *path, char *out, DWORD max) {
    HANDLE h = CreateFileW(path, GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
                           NULL, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, NULL);
    if (h == INVALID_HANDLE_VALUE) return -1;
    DWORD sz = GetFileSize(h, NULL);
    if (sz == INVALID_FILE_SIZE || sz == 0 || sz >= max) {
        CloseHandle(h);
        return -1;
    }
    DWORD rd = 0;
    BOOL ok = ReadFile(h, out, sz, &rd, NULL);
    CloseHandle(h);
    if (!ok || rd == 0 || rd >= max) return -1;
    out[rd] = 0;
    return (int)rd;
}

int wxb_read_file_tail_bytes(const WCHAR *path, char *out, DWORD max) {
    HANDLE h;
    DWORD sz, want, rd = 0;
    LARGE_INTEGER li;
    BOOL ok;
    if (!out || max < 2) return -1;
    h = CreateFileW(path, GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
                    NULL, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, NULL);
    if (h == INVALID_HANDLE_VALUE) return -1;
    sz = GetFileSize(h, NULL);
    if (sz == INVALID_FILE_SIZE || sz == 0) {
        CloseHandle(h);
        return -1;
    }
    want = sz >= max ? max - 1 : sz;
    li.QuadPart = (LONGLONG)(sz - want);
    if (!SetFilePointerEx(h, li, NULL, FILE_BEGIN)) {
        CloseHandle(h);
        return -1;
    }
    ok = ReadFile(h, out, want, &rd, NULL);
    CloseHandle(h);
    if (!ok || rd == 0 || rd >= max) return -1;
    out[rd] = 0;
    return (int)rd;
}
