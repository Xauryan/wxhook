/*
 * Module: room/contact scanning, room-member tasks, and private auto-scan state.
 *
 * Room member list uses a queued C worker (same pattern as chatroom_member.c):
 *   ROOM_INIT1/2/3 + ROOM_GET under SEH, with arena retained after completion.
 * This avoids process-killing AVs inside Weixin.dll and use-after-free from
 * freeing the call arena while WeChat still holds service/string refs.
 */

#include "../include/wx_bridge_room_contacts.h"
#include "../include/wx_bridge_contact_lookup.h"
#include "../include/wx_bridge_debug_log.h"
#include "../include/wx_bridge_file_io.h"
#include "../include/wx_bridge_memory.h"
#include "../include/wx_bridge_native_call.h"
#include "../include/wx_bridge_offsets.h"
#include "../include/wx_bridge_paths.h"
#include "../include/wx_bridge_protocol.h"
#include "../include/wx_bridge_revoke_forward.h"
#include "../include/wx_bridge_revoke_hooks.h"
#include "../include/wx_bridge_send_api.h"
#include "../include/wx_bridge_send_runtime.h"
#include "../include/wx_bridge_runtime.h"
#include "../include/wx_bridge_text.h"
#include "../include/wx_bridge_wechat_string.h"

#define AUTO_ROOM_SCAN_CACHE_SIZE 64
#define AUTO_ROOM_SCAN_COOLDOWN_MS (10ULL * 60ULL * 1000ULL)
#define AUTO_CONTACT_SCAN_COOLDOWN_MS (10ULL * 60ULL * 1000ULL)
#define ROOM_LIST_TIMEOUT_MS 30000ULL
#define ROOM_MEMBER_STRIDE 0x120
#define ROOM_MEMBER_MAX_COUNT 2000
#define ROOM_MEMBER_JSON_BASE_SIZE (64ULL * 1024ULL)
#define ROOM_MEMBER_JSON_PER_MEMBER (48ULL * 1024ULL)
#define ROOM_MEMBER_JSON_MAX_SIZE (32ULL * 1024ULL * 1024ULL)

typedef int64_t (__fastcall *FnRoomGetMembers)(int64_t service,
                                               int64_t roomid_str,
                                               int64_t out_vec);

/* Native work package lives inside the retained arena for the whole lifetime. */
typedef struct RoomListNativeTask {
    volatile LONG status; /* 0=pending 2=running 1=ok <0=error */
    int phase;
    DWORD seh_code;
    uint64_t seh_addr;
    int64_t root_pair[2];
    int64_t mid_pair[2];
    int64_t service_pair[2];
    int64_t out_vec[3];
    BYTE roomid_str[32];
    BYTE pool[1024];
    char roomid[256];
} RoomListNativeTask;

typedef struct RoomTask {
    LONG active;
    char seq[64];
    char roomid[256];
    void *arena;
    RoomListNativeTask *native;
    ULONGLONG started_ms;
    int auto_write_names;
} RoomTask;

typedef struct AutoRoomScanCache {
    char roomid[256];
    ULONGLONG last_ms;
} AutoRoomScanCache;

typedef struct RoomContactsState {
    RoomTask room_task;
    AutoRoomScanCache auto_room_scan_cache[AUTO_ROOM_SCAN_CACHE_SIZE];
    volatile LONG auto_room_scan_pos;
    volatile LONG auto_room_pending_lock;
    int auto_room_pending;
    char auto_room_pending_roomid[256];
    char auto_room_pending_sender[256];
    char auto_room_pending_reason[64];
    volatile LONG auto_contact_scan_pending;
    volatile LONG auto_contact_scan_running;
    volatile LONG64 auto_contact_scan_last_ms;
} RoomContactsState;

typedef struct RoomJsonWriter {
    char *cursor;
    char *end;
    int complete;
} RoomJsonWriter;

static RoomContactsState g_room_contacts_state = {0};

static SIZE_T room_json_escaped_length(const char *s) {
    const unsigned char *p = (const unsigned char *)(s ? s : "");
    SIZE_T length = 0;
    while (*p) {
        unsigned char c = *p++;
        if (c == '\\' || c == '"' || c == '\n' || c == '\r' || c == '\t') length += 2;
        else if (c < 0x20) length += 6;
        else length++;
    }
    return length;
}

static void room_json_append_raw(RoomJsonWriter *writer, const char *s) {
    char *before;
    SIZE_T expected;
    if (!writer || !writer->complete) return;
    if (!writer->cursor || !writer->end || writer->cursor > writer->end) {
        writer->complete = 0;
        return;
    }
    if (!s) s = "";
    before = writer->cursor;
    expected = (SIZE_T)lstrlenA(s);
    append_raw(&writer->cursor, writer->end, s);
    if ((SIZE_T)(writer->cursor - before) != expected) writer->complete = 0;
}

static void room_json_append_escaped(RoomJsonWriter *writer, const char *s) {
    char *before;
    SIZE_T expected;
    if (!writer || !writer->complete) return;
    if (!writer->cursor || !writer->end || writer->cursor > writer->end) {
        writer->complete = 0;
        return;
    }
    if (!s) s = "";
    before = writer->cursor;
    expected = room_json_escaped_length(s);
    append_escaped(&writer->cursor, writer->end, s);
    if ((SIZE_T)(writer->cursor - before) != expected) writer->complete = 0;
}

static int build_room_list_queue_asm(BYTE *p, int64_t task_addr, int64_t worker_addr) {
    BYTE *start = p;
    BYTE pro[] = {0x48, 0x83, 0xEC, 0x28};
    BYTE epi[] = {0x48, 0x83, 0xC4, 0x28, 0xC3};
    copy_bytes(p, pro, sizeof(pro));
    p += sizeof(pro);
    *p++ = 0x48;
    *p++ = 0xB9;
    *(int64_t *)p = task_addr;
    p += 8;
    *p++ = 0x48;
    *p++ = 0xB8;
    *(int64_t *)p = worker_addr;
    p += 8;
    *p++ = 0xFF;
    *p++ = 0xD0;
    copy_bytes(p, epi, sizeof(epi));
    p += sizeof(epi);
    return (int)(p - start);
}

static void room_list_worker(int64_t task_addr) {
    RoomListNativeTask *task = (RoomListNativeTask *)task_addr;
    int64_t base = (int64_t)wxb_weixin_module();
    BYTE *pool_ptr;
    BYTE *pool_end;

    if (!task || !base) return;
    if (InterlockedCompareExchange(&task->status, 2, 0) != 0) return;

    pool_ptr = task->pool;
    pool_end = task->pool + sizeof(task->pool);
    ZeroMemory(task->root_pair, sizeof(task->root_pair));
    ZeroMemory(task->mid_pair, sizeof(task->mid_pair));
    ZeroMemory(task->service_pair, sizeof(task->service_pair));
    ZeroMemory(task->out_vec, sizeof(task->out_vec));
    ZeroMemory(task->roomid_str, sizeof(task->roomid_str));
    ZeroMemory(task->pool, sizeof(task->pool));
    task->phase = 0;
    task->seh_code = 0;
    task->seh_addr = 0;

    __try {
        task->phase = 1;
        ((FnCall1)(base + WX_OFF_ROOM_INIT1))((int64_t)task->root_pair);
        if (!task->root_pair[0]) {
            InterlockedExchange(&task->status, -3);
            __leave;
        }

        task->phase = 2;
        ((FnCall3)(base + WX_OFF_ROOM_INIT2))(task->root_pair[0], (int64_t)task->mid_pair);
        if (!task->mid_pair[0]) {
            InterlockedExchange(&task->status, -4);
            __leave;
        }

        task->phase = 3;
        ((FnCall3)(base + WX_OFF_ROOM_INIT3))(task->mid_pair[0], (int64_t)task->service_pair);
        if (!task->service_pair[0]) {
            InterlockedExchange(&task->status, -5);
            __leave;
        }

        task->phase = 4;
        wx_set_str_arena((int64_t)task->roomid_str,
                         task->roomid,
                         lstrlenA(task->roomid),
                         &pool_ptr,
                         pool_end);

        task->phase = 5;
        ((FnRoomGetMembers)(base + WX_OFF_ROOM_GET))(
            task->service_pair[0],
            (int64_t)task->roomid_str,
            (int64_t)task->out_vec);

        task->phase = 6;
        InterlockedExchange(&task->status, 1);
    }
    __except (task->seh_code = GetExceptionCode(),
              task->seh_addr = (uint64_t)GetExceptionInformation()->ExceptionRecord->ExceptionAddress,
              EXCEPTION_EXECUTE_HANDLER) {
        /* Catch AVs inside Weixin.dll ROOM_* so the process stays alive. */
        InterlockedExchange(&task->status, -1);
    }
}

static int room_member_range_valid(int64_t begin, int64_t endp, int *out_count) {
    int64_t bytes;
    int count;
    if (out_count) *out_count = 0;
    if (!begin && !endp) return 1; /* empty success */
    if (begin <= 0 || endp <= 0) return 0;
    if (endp < begin) return 0;
    bytes = endp - begin;
    if (bytes % ROOM_MEMBER_STRIDE) return 0;
    count = (int)(bytes / ROOM_MEMBER_STRIDE);
    if (count < 0 || count > ROOM_MEMBER_MAX_COUNT) return 0;
    if (count > 0) {
        SIZE_T need = (SIZE_T)((count > 4 ? 4 : count) * ROOM_MEMBER_STRIDE);
        if (!is_readable_ptr(begin, need)) return 0;
        if (count > 4 && !is_readable_ptr(endp - ROOM_MEMBER_STRIDE, ROOM_MEMBER_STRIDE)) return 0;
    }
    if (out_count) *out_count = count;
    return 1;
}

static void sanitize_tsv_field(char *s) {
    if (!s) return;
    for (int i = 0; s[i]; i++) {
        if (s[i] == '\t' || s[i] == '\r' || s[i] == '\n') s[i] = ' ';
    }
}

static int pick_room_member_display_name(const char *wxid, const char *bz,
                                         const char *name, char *out, int max) {
    const char *candidates[2];
    if (!out || max <= 0) return 0;
    out[0] = 0;
    candidates[0] = bz;
    candidates[1] = name;
    for (int i = 0; i < 2; i++) {
        const char *candidate = candidates[i];
        if (is_displayable_name_for_id(wxid, candidate)) {
            safe_copy(out, max, candidate);
            sanitize_tsv_field(out);
            return out[0] != 0;
        }
    }
    return 0;
}

static void append_contact_display_name_tsv(const char *wxid, const char *name) {
    char id[512];
    char display[512];
    char line[1200];
    char *p = line;
    char *end = line + sizeof(line) - 1;
    if (!is_displayable_name_for_id(wxid, name)) return;
    safe_copy(id, sizeof(id), wxid);
    safe_copy(display, sizeof(display), name);
    sanitize_tsv_field(id);
    sanitize_tsv_field(display);
    if (!id[0] || !display[0]) return;
    append_raw(&p, end, id);
    append_raw(&p, end, "\t");
    append_raw(&p, end, display);
    append_raw(&p, end, "\r\n");
    *p = 0;
    append_file_bytes(CONTACT_NAMES_FILE, line, (DWORD)(p - line));
}

static DWORD weixin_image_size(void) {
    DWORD size = 0;
    BYTE *base = (BYTE *)wxb_weixin_module();
    if (!base) return 0;
    __try {
        IMAGE_DOS_HEADER *dos = (IMAGE_DOS_HEADER *)base;
        IMAGE_NT_HEADERS64 *nt;
        if (dos->e_magic != IMAGE_DOS_SIGNATURE) return 0;
        nt = (IMAGE_NT_HEADERS64 *)(base + dos->e_lfanew);
        if (nt->Signature != IMAGE_NT_SIGNATURE) return 0;
        size = nt->OptionalHeader.SizeOfImage;
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
        size = 0;
    }
    return size;
}

static int find_contact_object_vt(int64_t *out) {
    BYTE *base = (BYTE *)wxb_weixin_module();
    DWORD size = weixin_image_size();
    if (!out) return 0;
    *out = 0;
    if (!base || size < 32) return 0;
    __try {
        for (DWORD i = 0; i + 17 < size; i++) {
            BYTE *p = base + i;
            if (p[0] == 0x48 && p[1] == 0x8D && p[2] == 0x05 &&
                p[7] == 0x49 && p[8] == 0x89 && p[9] == 0x06 &&
                p[10] == 0x4C && p[11] == 0x89 && p[12] == 0xB5 &&
                p[13] == 0x58 && p[14] == 0x01 && p[15] == 0x00 && p[16] == 0x00) {
                int32_t rel = *(int32_t *)(p + 3);
                *out = (int64_t)(p + 7 + rel);
                return *out != 0;
            }
        }
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
        *out = 0;
    }
    return 0;
}

static int is_scan_readable_protect(DWORD protect) {
    if (!protect || (protect & PAGE_GUARD) || (protect & PAGE_NOACCESS)) return 0;
    return (protect & (PAGE_READONLY | PAGE_READWRITE | PAGE_WRITECOPY |
                       PAGE_EXECUTE_READ | PAGE_EXECUTE_READWRITE |
                       PAGE_EXECUTE_WRITECOPY)) != 0;
}

static int scan_contact_room_names_now(const char *reason) {
    int64_t target_vt = 0;
    MEMORY_BASIC_INFORMATION mbi;
    BYTE *addr = (BYTE *)0x10000;
    BYTE *max_addr = (BYTE *)0x00007FFFFFFFFFFFULL;
    int hits = 0;
    int rooms = 0;
    int names = 0;
    char dbg[768];
    if (!wxb_weixin_module()) return 0;
    if (!find_contact_object_vt(&target_vt) || !target_vt) {
        int n = wsprintfA(dbg, "contacts_auto_scan: contact pattern not found reason=%s\r\n",
                          reason ? reason : "");
        append_file_bytes(RECV_LOG, dbg, (DWORD)n);
        return 0;
    }

    while (addr < max_addr && hits < 20000 && rooms < 2000) {
        SIZE_T got = VirtualQuery(addr, &mbi, sizeof(mbi));
        BYTE *base;
        SIZE_T size;
        if (got != sizeof(mbi)) {
            addr += 0x10000;
            continue;
        }
        base = (BYTE *)mbi.BaseAddress;
        size = mbi.RegionSize;
        if (size > 0 &&
            mbi.State == MEM_COMMIT &&
            is_scan_readable_protect(mbi.Protect) &&
            size <= 64ULL * 1024ULL * 1024ULL) {
            __try {
                for (SIZE_T off = 0; off + sizeof(int64_t) <= size && hits < 20000 && rooms < 2000; off += sizeof(void *)) {
                    int64_t *slot = (int64_t *)(base + off);
                    if (*slot == target_vt) {
                        int64_t cand = (int64_t)slot;
                        char wxid[512] = {0};
                        char remark[512] = {0};
                        char nickname[512] = {0};
                        char display[512] = {0};
                        hits++;
                        if (!is_readable_ptr(cand, 0x250)) continue;
                        wx_read_str(cand + 0x18, wxid, sizeof(wxid));
                        if (!is_chatroom_id(wxid)) continue;
                        rooms++;
                        wx_read_str(cand + 0x88, remark, sizeof(remark));
                        wx_read_str(cand + 0xE8, nickname, sizeof(nickname));
                        if (pick_room_member_display_name(wxid, remark, nickname, display, sizeof(display))) {
                            append_contact_display_name_tsv(wxid, display);
                            names++;
                        }
                    }
                }
            }
            __except (EXCEPTION_EXECUTE_HANDLER) {
            }
        }
        if (!size || base + size <= addr) break;
        addr = base + size;
    }

    {
        int n = wsprintfA(dbg, "contacts_auto_scan: finished reason=%s hits=%d rooms=%d names=%d vt=%p\r\n",
                          reason ? reason : "",
                          hits,
                          rooms,
                          names,
                          (void *)target_vt);
        append_file_bytes(RECV_LOG, dbg, (DWORD)n);
    }
    return names;
}

int schedule_auto_contact_names(const char *reason) {
    ULONGLONG now = GetTickCount64();
    LONG64 last = InterlockedCompareExchange64(&g_room_contacts_state.auto_contact_scan_last_ms, 0, 0);
    if (last && now >= (ULONGLONG)last &&
        now - (ULONGLONG)last < AUTO_CONTACT_SCAN_COOLDOWN_MS) {
        return 0;
    }
    if (InterlockedExchange(&g_room_contacts_state.auto_contact_scan_pending, 1) == 0) {
        char dbg[512];
        int n = wsprintfA(dbg, "contacts_auto_scan: queued reason=%s\r\n",
                          reason ? reason : "");
        append_file_bytes(RECV_LOG, dbg, (DWORD)n);
    }
    return 1;
}

void poll_auto_contact_names(void) {
    if (InterlockedCompareExchange(&g_room_contacts_state.auto_contact_scan_pending, 0, 1) != 1) return;
    if (InterlockedCompareExchange(&g_room_contacts_state.auto_contact_scan_running, 1, 0) != 0) {
        InterlockedExchange(&g_room_contacts_state.auto_contact_scan_pending, 1);
        return;
    }
    InterlockedExchange64(&g_room_contacts_state.auto_contact_scan_last_ms, (LONG64)GetTickCount64());
    scan_contact_room_names_now("auto");
    InterlockedExchange(&g_room_contacts_state.auto_contact_scan_running, 0);
}

static int json_room_members(const char *seq, int64_t start, int64_t endp,
                             int auto_write_names, const char *roomid) {
    WCHAR path[1024];
    char *json = NULL;
    RoomJsonWriter writer;
    SIZE_T json_size;
    int count = 0;
    int names_written = 0;
    int ok = 0;

    if (!seq || !*seq) return 0;
    if (!room_member_range_valid(start, endp, &count)) return 0;

    json_size = ROOM_MEMBER_JSON_BASE_SIZE +
                (SIZE_T)(count > 1000 ? 1000 : count) * ROOM_MEMBER_JSON_PER_MEMBER;
    if (json_size > ROOM_MEMBER_JSON_MAX_SIZE) json_size = ROOM_MEMBER_JSON_MAX_SIZE;
    json = (char *)VirtualAlloc(NULL, json_size, MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE);
    if (!json) return 0;
    writer.cursor = json;
    writer.end = json + json_size - 1;
    writer.complete = 1;

    wsprintfW(path, L"%s\\room_%S.json", CONTROL_DIR, seq);

    __try {
        room_json_append_raw(&writer, "{\"ok\":true,\"members\":[");
        for (int i = 0; i < count && i < 1000; i++) {
            int64_t cur = start + (int64_t)i * ROOM_MEMBER_STRIDE;
            char raw[9][512] = {{0}};
            char display_name[512] = {0};
            const int raw_offsets[9] = {0x00, 0x20, 0x40, 0x60, 0x80, 0xA0, 0xC0, 0xE0, 0x100};
            const char *wxid = raw[0];
            const char *group_nickname = raw[1];
            const char *wxh = raw[2];
            const char *bz = raw[3];
            const char *contact_nickname = raw[4];
            const char *dq = raw[6];
            if (!is_readable_ptr(cur, ROOM_MEMBER_STRIDE)) break;
            for (int j = 0; j < 9; j++) {
                wx_read_str(cur + raw_offsets[j], raw[j], sizeof(raw[j]));
            }
            if (group_nickname[0]) safe_copy(display_name, sizeof(display_name), group_nickname);
            else if (bz[0]) safe_copy(display_name, sizeof(display_name), bz);
            else if (contact_nickname[0]) safe_copy(display_name, sizeof(display_name), contact_nickname);
            else safe_copy(display_name, sizeof(display_name), wxid);
            if (auto_write_names) {
                char display[512] = {0};
                if (pick_room_member_display_name(wxid, group_nickname[0] ? group_nickname : bz,
                                                  contact_nickname, display, sizeof(display))) {
                    append_contact_display_name_tsv(wxid, display);
                    if (roomid && roomid[0]) {
                        char scoped[900];
                        char *sp = scoped;
                        char *send = scoped + sizeof(scoped) - 1;
                        append_raw(&sp, send, roomid);
                        append_raw(&sp, send, "/");
                        append_raw(&sp, send, wxid);
                        *sp = 0;
                        append_contact_display_name_tsv(scoped, display);
                    }
                    names_written++;
                }
            }
            if (i) room_json_append_raw(&writer, ",");
            room_json_append_raw(&writer, "{\"wxid\":\""); room_json_append_escaped(&writer, wxid);
            room_json_append_raw(&writer, "\",\"group_nickname\":\""); room_json_append_escaped(&writer, group_nickname);
            room_json_append_raw(&writer, "\",\"contact_nickname\":\""); room_json_append_escaped(&writer, contact_nickname);
            room_json_append_raw(&writer, "\",\"contact_remark\":\""); room_json_append_escaped(&writer, bz);
            room_json_append_raw(&writer, "\",\"account\":\""); room_json_append_escaped(&writer, wxh);
            room_json_append_raw(&writer, "\",\"region\":\""); room_json_append_escaped(&writer, dq);
            room_json_append_raw(&writer, "\",\"display_name\":\""); room_json_append_escaped(&writer, display_name);
            room_json_append_raw(&writer, "\",\"name\":\""); room_json_append_escaped(&writer, group_nickname);
            room_json_append_raw(&writer, "\",\"nickname\":\""); room_json_append_escaped(&writer, contact_nickname);
            room_json_append_raw(&writer, "\",\"wxh\":\""); room_json_append_escaped(&writer, wxh);
            room_json_append_raw(&writer, "\",\"bz\":\""); room_json_append_escaped(&writer, bz);
            room_json_append_raw(&writer, "\",\"dq\":\""); room_json_append_escaped(&writer, dq);
            room_json_append_raw(&writer, "\",\"raw_offsets\":{");
            for (int j = 0; j < 9; j++) {
                char off_key[16];
                if (j) room_json_append_raw(&writer, ",");
                wsprintfA(off_key, "\"0x%02X\":\"", raw_offsets[j]);
                room_json_append_raw(&writer, off_key);
                room_json_append_escaped(&writer, raw[j]);
                room_json_append_raw(&writer, "\"");
            }
            room_json_append_raw(&writer, "}");
            room_json_append_raw(&writer, "}");
        }
        room_json_append_raw(&writer, "],\"count\":\"");
        {
            char count_s[32];
            i64toa10(count, count_s);
            room_json_append_escaped(&writer, count_s);
        }
        room_json_append_raw(&writer, "\"}");
        if (writer.complete) {
            *writer.cursor = 0;
            write_file_bytes(path, json, (DWORD)(writer.cursor - json));
            ok = 1;
        }
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
        ok = 0;
    }

    VirtualFree(json, 0, MEM_RELEASE);

    if (ok && auto_write_names) {
        char dbg[768];
        int n = wsprintfA(dbg, "room_members_auto_scan: saved room=%s count=%d names=%d\r\n",
                          roomid && roomid[0] ? roomid : "",
                          count,
                          names_written);
        append_file_bytes(RECV_LOG, dbg, (DWORD)n);
        if (roomid && roomid[0]) {
            char room_name[256] = {0};
            if (!lookup_contact_display_name(roomid, room_name, sizeof(room_name))) {
                schedule_auto_contact_names("room_name_missing_after_members");
            }
        }
    }
    return ok;
}

void clear_room_task(void) {
    /* Keep call arena alive: WeChat may still hold service/string refs after return. */
    if (g_room_contacts_state.room_task.arena) {
        retain_arena(g_room_contacts_state.room_task.arena);
    }
    ZeroMemory(&g_room_contacts_state.room_task, sizeof(g_room_contacts_state.room_task));
}

static void finish_room_task_fail(const char *message) {
    char dbg[512];
    int n;
    RoomListNativeTask *native = g_room_contacts_state.room_task.native;
    if (native) {
        n = wsprintfA(dbg,
                      "room_members: fail seq=%s room=%s status=%d phase=%d seh=0x%lx addr=0x%I64x\r\n",
                      g_room_contacts_state.room_task.seq,
                      g_room_contacts_state.room_task.roomid,
                      (int)InterlockedCompareExchange(&native->status, 0, 0),
                      native->phase,
                      (unsigned long)native->seh_code,
                      (unsigned __int64)native->seh_addr);
        append_file_bytes(RECV_LOG, dbg, (DWORD)n);
    }
    write_status(message && message[0] ? message : "get_room_list failed\n");
    write_result(g_room_contacts_state.room_task.seq, 0, "room_members",
                 message && message[0] ? message : "get_room_list failed");
    clear_room_task();
}

void poll_room_task(void) {
    RoomListNativeTask *native;
    LONG status;
    int64_t begin;
    int64_t endp;
    int count = 0;

    if (!g_room_contacts_state.room_task.active) return;
    native = g_room_contacts_state.room_task.native;
    if (!native) {
        finish_room_task_fail("get_room_list missing_task\n");
        return;
    }

    status = InterlockedCompareExchange(&native->status, 0, 0);
    if (status == 1) {
        begin = native->out_vec[0];
        endp = native->out_vec[1];
        if (!room_member_range_valid(begin, endp, &count)) {
            finish_room_task_fail("get_room_list invalid_result\n");
            return;
        }
        if (!json_room_members(g_room_contacts_state.room_task.seq, begin, endp,
                               g_room_contacts_state.room_task.auto_write_names,
                               g_room_contacts_state.room_task.roomid)) {
            finish_room_task_fail("get_room_list serialize_failed\n");
            return;
        }
        write_status("get_room_list ok\n");
        write_result(g_room_contacts_state.room_task.seq, 1, "room_members", "get_room_list ok");
        clear_room_task();
        return;
    }

    if (status < 0) {
        if (status == -1) finish_room_task_fail("get_room_list exception\n");
        else if (status == -2) finish_room_task_fail("get_room_list timeout\n");
        else if (status == -3) finish_room_task_fail("get_room_list root_init_failed\n");
        else if (status == -4) finish_room_task_fail("get_room_list mid_init_failed\n");
        else if (status == -5) finish_room_task_fail("get_room_list service_init_failed\n");
        else finish_room_task_fail("get_room_list failed\n");
        return;
    }

    if (GetTickCount64() - g_room_contacts_state.room_task.started_ms > ROOM_LIST_TIMEOUT_MS) {
        /* Mark timed out if still pending/running; do not free mid-call aggressively. */
        InterlockedCompareExchange(&native->status, -2, 0);
        InterlockedCompareExchange(&native->status, -2, 2);
        finish_room_task_fail("get_room_list timeout\n");
    }
}

int start_room_list_ex(const char *seq, const char *roomid, int auto_write_names) {
    const SIZE_T arena_size = 0x8000;
    BYTE *arena;
    BYTE *asm_addr;
    RoomListNativeTask *native;
    int asm_len;

    if (!wxb_weixin_module() || !seq || !roomid || !roomid[0]) return 0;
    if (g_room_contacts_state.room_task.active) return 0;

    arena = (BYTE *)VirtualAlloc(NULL, arena_size, MEM_COMMIT | MEM_RESERVE, PAGE_EXECUTE_READWRITE);
    if (!arena) return 0;
    ZeroMemory(arena, arena_size);

    asm_addr = arena + 0x200;
    native = (RoomListNativeTask *)(arena + 0x1000);
    native->status = 0;
    safe_copy(native->roomid, sizeof(native->roomid), roomid);

    asm_len = build_room_list_queue_asm(asm_addr,
                                        (int64_t)native,
                                        (int64_t)room_list_worker);
    FlushInstructionCache(GetCurrentProcess(), asm_addr, asm_len);
    queue_send((int64_t)asm_addr);

    safe_copy(g_room_contacts_state.room_task.seq, sizeof(g_room_contacts_state.room_task.seq), seq);
    safe_copy(g_room_contacts_state.room_task.roomid, sizeof(g_room_contacts_state.room_task.roomid), roomid);
    g_room_contacts_state.room_task.arena = arena;
    g_room_contacts_state.room_task.native = native;
    g_room_contacts_state.room_task.started_ms = GetTickCount64();
    g_room_contacts_state.room_task.auto_write_names = auto_write_names ? 1 : 0;
    InterlockedExchange(&g_room_contacts_state.room_task.active, 1);
    return 1;
}

int start_room_list(const char *seq, const char *roomid) {
    return start_room_list_ex(seq, roomid, 0);
}

static int auto_room_scan_recent(const char *roomid, ULONGLONG now) {
    if (!roomid || !roomid[0]) return 1;
    for (int i = 0; i < AUTO_ROOM_SCAN_CACHE_SIZE; i++) {
        AutoRoomScanCache *item = &g_room_contacts_state.auto_room_scan_cache[i];
        if (item->roomid[0] && str_eq(item->roomid, roomid)) {
            ULONGLONG last = item->last_ms;
            if (last && (now >= last ? (now - last) : 0) < AUTO_ROOM_SCAN_COOLDOWN_MS) return 1;
        }
    }
    return 0;
}

static void remember_auto_room_scan(const char *roomid, ULONGLONG now) {
    if (!roomid || !roomid[0]) return;
    for (int i = 0; i < AUTO_ROOM_SCAN_CACHE_SIZE; i++) {
        AutoRoomScanCache *item = &g_room_contacts_state.auto_room_scan_cache[i];
        if (item->roomid[0] && str_eq(item->roomid, roomid)) {
            item->last_ms = now;
            return;
        }
    }
    {
        LONG raw = InterlockedIncrement(&g_room_contacts_state.auto_room_scan_pos);
        int pos = (int)((raw < 0 ? -raw : raw) % AUTO_ROOM_SCAN_CACHE_SIZE);
        safe_copy(g_room_contacts_state.auto_room_scan_cache[pos].roomid, sizeof(g_room_contacts_state.auto_room_scan_cache[pos].roomid), roomid);
        g_room_contacts_state.auto_room_scan_cache[pos].last_ms = now;
    }
}

int schedule_auto_room_members(const char *roomid, const char *sender_wxid,
                                      const char *reason) {
    ULONGLONG now = GetTickCount64();
    int queued = 0;
    char room_name[256] = {0};
    if (!roomid || !is_chatroom_id(roomid)) return 0;
    if (!lookup_contact_display_name(roomid, room_name, sizeof(room_name))) {
        schedule_auto_contact_names("room_name_missing");
    }
    if (auto_room_scan_recent(roomid, now)) return 0;
    if (InterlockedCompareExchange(&g_room_contacts_state.auto_room_pending_lock, 1, 0) != 0) return 0;
    if (!g_room_contacts_state.auto_room_pending) {
        safe_copy(g_room_contacts_state.auto_room_pending_roomid, sizeof(g_room_contacts_state.auto_room_pending_roomid), roomid);
        safe_copy(g_room_contacts_state.auto_room_pending_sender, sizeof(g_room_contacts_state.auto_room_pending_sender), sender_wxid ? sender_wxid : "");
        safe_copy(g_room_contacts_state.auto_room_pending_reason, sizeof(g_room_contacts_state.auto_room_pending_reason), reason ? reason : "");
        g_room_contacts_state.auto_room_pending = 1;
        remember_auto_room_scan(roomid, now);
        queued = 1;
    }
    InterlockedExchange(&g_room_contacts_state.auto_room_pending_lock, 0);
    if (queued) {
        char dbg[768];
        int n = wsprintfA(dbg, "room_members_auto_scan: queued room=%s sender=%s reason=%s\r\n",
                          roomid,
                          sender_wxid ? sender_wxid : "",
                          reason ? reason : "");
        append_file_bytes(RECV_LOG, dbg, (DWORD)n);
    }
    return queued;
}

void poll_auto_room_members(void) {
    char roomid[256] = {0};
    char sender[256] = {0};
    char reason[64] = {0};
    char seq[64];
    int has_pending = 0;
    if (g_room_contacts_state.room_task.active || !g_room_contacts_state.auto_room_pending) return;
    if (InterlockedCompareExchange(&g_room_contacts_state.auto_room_pending_lock, 1, 0) != 0) return;
    if (g_room_contacts_state.auto_room_pending) {
        safe_copy(roomid, sizeof(roomid), g_room_contacts_state.auto_room_pending_roomid);
        safe_copy(sender, sizeof(sender), g_room_contacts_state.auto_room_pending_sender);
        safe_copy(reason, sizeof(reason), g_room_contacts_state.auto_room_pending_reason);
        g_room_contacts_state.auto_room_pending = 0;
        g_room_contacts_state.auto_room_pending_roomid[0] = 0;
        g_room_contacts_state.auto_room_pending_sender[0] = 0;
        g_room_contacts_state.auto_room_pending_reason[0] = 0;
        has_pending = 1;
    }
    InterlockedExchange(&g_room_contacts_state.auto_room_pending_lock, 0);
    if (!has_pending || !roomid[0]) return;
    wsprintfA(seq, "auto_%lu_%I64u", (unsigned long)GetCurrentProcessId(),
              (unsigned __int64)GetTickCount64());
    if (start_room_list_ex(seq, roomid, 1)) {
        char dbg[768];
        int n = wsprintfA(dbg, "room_members_auto_scan: started room=%s sender=%s reason=%s seq=%s\r\n",
                          roomid, sender, reason, seq);
        append_file_bytes(RECV_LOG, dbg, (DWORD)n);
        write_status("room_members auto scan started\n");
    } else {
        char dbg[768];
        int n = wsprintfA(dbg, "room_members_auto_scan: start_failed room=%s sender=%s reason=%s\r\n",
                          roomid, sender, reason);
        append_file_bytes(RECV_LOG, dbg, (DWORD)n);
    }
}
