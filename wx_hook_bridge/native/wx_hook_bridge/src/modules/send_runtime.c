#include "../include/wx_bridge_send_runtime.h"
#include "../include/wx_bridge_memory.h"
#include "../include/wx_bridge_offsets.h"
#include "../include/wx_bridge_runtime.h"
#include "../include/wx_bridge_native_call.h"

#define RETAINED_ARENAS 16

static void *g_retained_arenas[RETAINED_ARENAS] = {0};
static int g_retained_index = 0;

const char *wxb_basename_utf8(const char *path) {
    const char *p = path;
    const char *base = path;
    if (!path) return "";
    while (*p) {
        if (*p == '\\' || *p == '/') base = p + 1;
        p++;
    }
    return base;
}

int64_t wxb_file_size_utf8(const char *path) {
    WCHAR wpath[1024];
    WIN32_FILE_ATTRIBUTE_DATA info;
    int wlen;
    if (!path || !*path) return 0;
    wlen = MultiByteToWideChar(CP_UTF8, 0, path, -1, wpath, 1024);
    if (wlen <= 0) return 0;
    if (!GetFileAttributesExW(wpath, GetFileExInfoStandard, &info)) return 0;
    return ((int64_t)info.nFileSizeHigh << 32) | info.nFileSizeLow;
}

void wxb_retain_arena(void *arena) {
    void *old = g_retained_arenas[g_retained_index];
    if (old) VirtualFree(old, 0, MEM_RELEASE);
    g_retained_arenas[g_retained_index] = arena;
    g_retained_index = (g_retained_index + 1) % RETAINED_ARENAS;
}

int wxb_queue_send(int64_t asm_addr) {
    int64_t base = (int64_t)wxb_weixin_module();
    int64_t vxid;
    BYTE *arena;
    int64_t rcx, rdx, r8, r9, rsp20, a1;
    int64_t jg;

    if (!base || !asm_addr) return 0;
    vxid = r64((void *)(base + WX_OFF_QUEUE_ID)) + 1;
    w64((void *)(base + WX_OFF_QUEUE_ID), vxid);
    arena = (BYTE *)VirtualAlloc(NULL, 0x1000, MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE);
    if (!arena) return 0;
    ZeroMemory(arena, 0x1000);

    rcx = (int64_t)(arena + 0x000);
    rdx = (int64_t)(arena + 0x200);
    r8 = (int64_t)(arena + 0x300);
    r9 = (int64_t)(arena + 0x400);
    rsp20 = (int64_t)(arena + 0x500);
    a1 = (int64_t)(arena + 0x600);

    w64((void *)rdx, base + WX_OFF_QUEUE_HC);
    w64((void *)(rdx + 8), 404);
    w64((void *)(rdx + 56), rdx);
    w64((void *)r8, base + WX_OFF_QUEUE_HC);
    w64((void *)(r8 + 8), asm_addr);
    w64((void *)(r8 + 56), r8);

    jg = ((FnCall6)(base + WX_OFF_SEND_CALL2))(rcx, rdx, r8, r9, rsp20, vxid);
    (void)jg;
    ((FnCall3)(base + WX_OFF_QUEUE_SUBMIT))(a1, rcx);
    wxb_retain_arena(arena);
    return 1;
}
