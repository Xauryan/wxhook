/*
 * Module: chatroom member management.
 *
 * Uses Weixin 4.1.9.23's business wrappers for chatroom members:
 *   sub_181544030(+0x1544030)
 *   sub_181540AA0(+0x1540AA0)
 *   sub_18154E270(+0x154E270)
 *   sub_18154AB60(+0x154AB60)
 * These wrappers build Create/Add/Invite/DelChatRoomMemberRequest and submit
 * CGI 119/120/610/179.
 */

#include "../include/wx_bridge_chatroom_member.h"
#include "../include/wx_bridge_file_io.h"
#include "../include/wx_bridge_memory.h"
#include "../include/wx_bridge_native_call.h"
#include "../include/wx_bridge_offsets.h"
#include "../include/wx_bridge_paths.h"
#include "../include/wx_bridge_protocol.h"
#include "../include/wx_bridge_runtime.h"
#include "../include/wx_bridge_send_runtime.h"
#include "../include/wx_bridge_text.h"
#include "../include/wx_bridge_wechat_string.h"

#define ADD_MEMBER_ROOMID_MAX 256
#define ADD_MEMBER_WXID_MAX 256
#define CREATE_CHATROOM_MAX_MEMBERS 40
#define CREATE_CHATROOM_MEMBER_MAX 256
#define CREATE_CHATROOM_MEMBER_IDS_MAX (CREATE_CHATROOM_MAX_MEMBERS * CREATE_CHATROOM_MEMBER_MAX)
#define CREATE_CHATROOM_TOPIC_MAX 512

typedef int64_t (__fastcall *FnSharedCtlVFunc)(int64_t ctl);
typedef int64_t (__fastcall *FnCreateChatroom)(int64_t service,
                                              int64_t out_roomid,
                                              int64_t members_vec,
                                              int64_t topic_str);
typedef int64_t (__fastcall *FnAddChatroomMember)(int64_t service,
                                                  int64_t members_vec,
                                                  int64_t roomid_str,
                                                  int64_t ext_obj,
                                                  int64_t optional_str);
typedef BYTE (__fastcall *FnInviteChatroomMember)(int64_t service,
                                                  int64_t members_vec,
                                                  int64_t roomid_str,
                                                  int64_t ext_obj);
typedef int64_t (__fastcall *FnDelChatroomMember)(int64_t service,
                                                  int64_t out_obj,
                                                  int64_t members_vec,
                                                  int64_t roomid_str,
                                                  int flag);
typedef int64_t (__fastcall *FnQuitChatroomTask)(int64_t task_obj);

typedef struct CreateChatroomTask {
    volatile LONG status;
    int64_t native_return;
    int64_t service;
    int phase;
    int member_count;
    DWORD seh_code;
    uint64_t seh_addr;
    char roomid[ADD_MEMBER_ROOMID_MAX];
    char member_ids[CREATE_CHATROOM_MEMBER_IDS_MAX];
    char topic[CREATE_CHATROOM_TOPIC_MAX];
} CreateChatroomTask;

typedef struct AddChatroomMemberTask {
    volatile LONG status;
    int64_t ret;
    int64_t service;
    int phase;
    DWORD seh_code;
    uint64_t seh_addr;
    char roomid[ADD_MEMBER_ROOMID_MAX];
    char member_wxid[ADD_MEMBER_WXID_MAX];
} AddChatroomMemberTask;

typedef AddChatroomMemberTask InviteChatroomMemberTask;

typedef struct DelChatroomMemberTask {
    volatile LONG status;
    int64_t ret;
    int64_t service;
    int phase;
    int flag;
    BYTE out_success;
    DWORD seh_code;
    uint64_t seh_addr;
    char out_text[512];
    char roomid[ADD_MEMBER_ROOMID_MAX];
    char member_wxid[ADD_MEMBER_WXID_MAX];
} DelChatroomMemberTask;

typedef struct QuitChatroomTask {
    volatile LONG status;
    int64_t ret;
    int phase;
    int flag;
    DWORD seh_code;
    uint64_t seh_addr;
    char roomid[ADD_MEMBER_ROOMID_MAX];
    BYTE scratch[1024];
} QuitChatroomTask;

static void add_member_release_shared_ctl(int64_t ctl) {
    if (!ctl) return;
    __try {
        int64_t vt = r64((void *)ctl);
        if (!InterlockedDecrement((volatile LONG *)(ctl + 8))) {
            ((FnSharedCtlVFunc)r64((void *)vt))(ctl);
            if (!InterlockedDecrement((volatile LONG *)(ctl + 12))) {
                ((FnSharedCtlVFunc)r64((void *)(vt + 8)))(ctl);
            }
        }
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
    }
}

static void add_member_release_shared_pair(int64_t pair_addr) {
    if (!pair_addr) return;
    add_member_release_shared_ctl(r64((void *)(pair_addr + 8)));
    w64((void *)pair_addr, 0);
    w64((void *)(pair_addr + 8), 0);
}

static int build_add_member_queue_asm(BYTE *p, int64_t task_addr, int64_t worker_addr) {
    BYTE *start = p;
    BYTE pro[] = {0x48,0x83,0xEC,0x28};
    BYTE epi[] = {0x48,0x83,0xC4,0x28,0xC3};
    copy_bytes(p, pro, sizeof(pro)); p += sizeof(pro);
    *p++ = 0x48; *p++ = 0xB9;
    *(int64_t *)p = task_addr; p += 8;
    *p++ = 0x48; *p++ = 0xB8;
    *(int64_t *)p = worker_addr; p += 8;
    *p++ = 0xFF; *p++ = 0xD0;
    copy_bytes(p, epi, sizeof(epi)); p += sizeof(epi);
    return (int)(p - start);
}

static int create_chatroom_is_space(char ch) {
    return ch == ' ' || ch == '\t' || ch == '\r' || ch == '\n';
}

static int create_chatroom_token_equals(const char *a, int a_len,
                                        const char *b, int b_len) {
    int i;
    if (a_len != b_len) return 0;
    for (i = 0; i < a_len; i++) {
        if (a[i] != b[i]) return 0;
    }
    return 1;
}

static int normalize_create_chatroom_members(const char *member_ids,
                                             char *normalized,
                                             int normalized_max,
                                             int *member_count,
                                             const char **error_message) {
    int offsets[CREATE_CHATROOM_MAX_MEMBERS];
    int lengths[CREATE_CHATROOM_MAX_MEMBERS];
    const char *cursor;
    int out_len = 0;
    int count = 0;

    if (member_count) *member_count = 0;
    if (error_message) *error_message = NULL;
    if (!normalized || normalized_max <= 0) return 0;
    normalized[0] = 0;
    if (!member_ids || !member_ids[0]) {
        if (error_message) *error_message = "missing_member_ids";
        return 0;
    }

    cursor = member_ids;
    for (;;) {
        const char *start = cursor;
        const char *end;
        int len;
        int duplicate = 0;
        int i;

        while (*cursor && *cursor != ',') cursor++;
        end = cursor;
        while (start < end && create_chatroom_is_space(*start)) start++;
        while (end > start && create_chatroom_is_space(end[-1])) end--;
        len = (int)(end - start);

        if (len <= 0) {
            if (error_message) *error_message = "empty_member_id";
            return 0;
        }
        if (len >= CREATE_CHATROOM_MEMBER_MAX) {
            if (error_message) *error_message = "member_id_too_long";
            return 0;
        }
        for (i = 0; i < len; i++) {
            if (create_chatroom_is_space(start[i])) {
                if (error_message) *error_message = "invalid_member_id";
                return 0;
            }
        }
        if (str_find_sub(start, "@chatroom") &&
            str_find_sub(start, "@chatroom") < end) {
            if (error_message) *error_message = "member_id_is_chatroom";
            return 0;
        }

        for (i = 0; i < count; i++) {
            if (create_chatroom_token_equals(start, len,
                                             normalized + offsets[i], lengths[i])) {
                duplicate = 1;
                break;
            }
        }
        if (!duplicate) {
            if (count >= CREATE_CHATROOM_MAX_MEMBERS) {
                if (error_message) *error_message = "too_many_members";
                return 0;
            }
            if (out_len + (count ? 1 : 0) + len >= normalized_max) {
                if (error_message) *error_message = "member_ids_too_long";
                return 0;
            }
            if (count) normalized[out_len++] = ',';
            offsets[count] = out_len;
            lengths[count] = len;
            CopyMemory(normalized + out_len, start, len);
            out_len += len;
            normalized[out_len] = 0;
            count++;
        }

        if (!*cursor) break;
        cursor++;
    }

    if (count < 2) {
        if (error_message) *error_message = "at_least_two_members_required";
        return 0;
    }
    if (member_count) *member_count = count;
    return 1;
}

static void create_chatroom_worker(int64_t task_addr) {
    CreateChatroomTask *task = (CreateChatroomTask *)task_addr;
    int64_t base = (int64_t)wxb_weixin_module();
    int64_t root_pair[2] = {0, 0};
    int64_t mid_pair[2] = {0, 0};
    int64_t service_pair[2] = {0, 0};
    BYTE roomid_out[32];
    BYTE topic_str[32];
    BYTE member_strings[CREATE_CHATROOM_MAX_MEMBERS][32];
    int64_t members_vec[3] = {0, 0, 0};
    BYTE pool[16384];
    BYTE *pool_ptr = pool;
    BYTE *pool_end = pool + sizeof(pool);

    if (!task || !base) return;
    if (InterlockedCompareExchange(&task->status, 2, 0) != 0) return;

    ZeroMemory(roomid_out, sizeof(roomid_out));
    ZeroMemory(topic_str, sizeof(topic_str));
    ZeroMemory(member_strings, sizeof(member_strings));
    ZeroMemory(pool, sizeof(pool));

    __try {
        const char *member_cursor;
        int i;

        task->phase = 1;
        ((FnCall1)(base + WX_OFF_ROOM_INIT1))((int64_t)root_pair);
        if (!root_pair[0]) {
            InterlockedExchange(&task->status, -3);
            __leave;
        }

        task->phase = 2;
        ((FnCall3)(base + WX_OFF_ROOM_INIT2))(root_pair[0], (int64_t)mid_pair);
        if (!mid_pair[0]) {
            InterlockedExchange(&task->status, -4);
            __leave;
        }

        task->phase = 3;
        ((FnCall3)(base + WX_OFF_ROOM_INIT3))(mid_pair[0], (int64_t)service_pair);
        task->service = service_pair[0];
        if (!service_pair[0]) {
            InterlockedExchange(&task->status, -5);
            __leave;
        }

        task->phase = 4;
        member_cursor = task->member_ids;
        for (i = 0; i < task->member_count; i++) {
            int len = 0;
            while (member_cursor[len] && member_cursor[len] != ',') len++;
            wx_set_std_string_arena((int64_t)member_strings[i],
                                    member_cursor,
                                    len,
                                    &pool_ptr,
                                    pool_end);
            member_cursor += len;
            if (*member_cursor == ',') member_cursor++;
        }
        members_vec[0] = (int64_t)member_strings;
        members_vec[1] = (int64_t)(member_strings + task->member_count);
        members_vec[2] = members_vec[1];
        wx_set_std_string_arena((int64_t)topic_str,
                                task->topic,
                                lstrlenA(task->topic),
                                &pool_ptr,
                                pool_end);

        task->phase = 5;
        task->native_return = ((FnCreateChatroom)(base + WX_OFF_CHATROOM_CREATE))(
            service_pair[0],
            (int64_t)roomid_out,
            (int64_t)members_vec,
            (int64_t)topic_str);
        task->phase = 6;
        wx_read_sso_str((int64_t)roomid_out, task->roomid, (int)sizeof(task->roomid));
        InterlockedExchange(&task->status, 1);
    }
    __except (task->seh_code = GetExceptionCode(),
              task->seh_addr = (uint64_t)GetExceptionInformation()->ExceptionRecord->ExceptionAddress,
              EXCEPTION_EXECUTE_HANDLER) {
        task->native_return = 0;
        InterlockedExchange(&task->status, -1);
    }

    __try {
        wx_free_std_string_heap(base, (int64_t)roomid_out);
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
    }
    add_member_release_shared_pair((int64_t)service_pair);
    add_member_release_shared_pair((int64_t)mid_pair);
    add_member_release_shared_pair((int64_t)root_pair);
}

static void write_create_chatroom_result(const char *seq,
                                         int ok,
                                         const char *message,
                                         const char *roomid,
                                         const char *member_ids,
                                         int member_count,
                                         const char *topic,
                                         int wait_status,
                                         LONG queue_status,
                                         int64_t native_return,
                                         int64_t service,
                                         int phase,
                                         DWORD seh_code,
                                         uint64_t seh_addr) {
    WCHAR path[1024];
    char json[32768];
    char num[64];
    char *p = json;
    char *end = json + sizeof(json) - 1;

    wsprintfW(path, L"%s\\result_%S.json", CONTROL_DIR, seq ? seq : "");
    append_raw(&p, end, "{\"ok\":");
    append_raw(&p, end, ok ? "true" : "false");
    append_raw(&p, end, ",\"seq\":\"");
    append_escaped(&p, end, seq ? seq : "");
    append_raw(&p, end, "\",\"command\":\"create_chatroom\",\"path\":\"native_chatroom_create\",\"message\":\"");
    append_escaped(&p, end, message ? message : "");
    append_raw(&p, end, "\",\"roomid\":\"");
    append_escaped(&p, end, roomid ? roomid : "");
    append_raw(&p, end, "\",\"memberIds\":\"");
    append_escaped(&p, end, member_ids ? member_ids : "");
    append_raw(&p, end, "\",\"memberCount\":");
    wsprintfA(num, "%d", member_count);
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"topic\":\"");
    append_escaped(&p, end, topic ? topic : "");
    append_raw(&p, end, "\",\"cgiType\":119,\"endpoint\":\"/cgi-bin/micromsg-bin/createchatroom\"");
    append_raw(&p, end, ",\"upperCall\":\"sub_181544030\",\"upperCallOffset\":\"0x1544030\"");
    append_raw(&p, end, ",\"serviceResolve\":\"sub_180020960 -> sub_1802FF1B0 -> sub_1806278F0\"");
    append_raw(&p, end, ",\"serviceOffsets\":\"0x20960,0x2ff1b0,0x6278f0\"");
    append_raw(&p, end, ",\"waitStatus\":");
    wsprintfA(num, "%d", wait_status);
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"queueStatus\":");
    wsprintfA(num, "%ld", queue_status);
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"dispatchResult\":\"0x");
    wsprintfA(num, "%I64x", (unsigned __int64)native_return);
    append_raw(&p, end, num);
    append_raw(&p, end, "\",\"dispatchResultKind\":\"out_roomid_pointer\"");
    append_raw(&p, end, ",\"service\":\"0x");
    wsprintfA(num, "%I64x", (unsigned __int64)service);
    append_raw(&p, end, num);
    append_raw(&p, end, "\",\"phase\":");
    wsprintfA(num, "%d", phase);
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"sehCode\":");
    wsprintfA(num, "%lu", (unsigned long)seh_code);
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"sehAddr\":\"0x");
    wsprintfA(num, "%I64x", (unsigned __int64)seh_addr);
    append_raw(&p, end, num);
    append_raw(&p, end, "\"}");
    *p = 0;
    write_file_bytes(path, json, (DWORD)(p - json));
}

int create_chatroom_to_result(const char *seq,
                              const char *member_ids,
                              const char *topic,
                              int wait_ms) {
    const SIZE_T arena_size = 0x10000;
    char normalized[CREATE_CHATROOM_MEMBER_IDS_MAX];
    const char *validation_error = NULL;
    BYTE *arena;
    BYTE *asm_addr;
    CreateChatroomTask *task;
    int member_count = 0;
    int asm_len;
    LONG status;
    int loops;
    int ok;
    const char *message;

    if (!seq || !seq[0]) return 0;
    normalized[0] = 0;
    if (!normalize_create_chatroom_members(member_ids,
                                           normalized,
                                           (int)sizeof(normalized),
                                           &member_count,
                                           &validation_error)) {
        write_create_chatroom_result(seq, 0,
                                     validation_error ? validation_error : "invalid_member_ids",
                                     "", normalized, member_count, topic,
                                     WAIT_FAILED, -10, 0, 0, 0, 0, 0);
        return 0;
    }
    if (topic && lstrlenA(topic) >= CREATE_CHATROOM_TOPIC_MAX) {
        write_create_chatroom_result(seq, 0, "topic_too_long",
                                     "", normalized, member_count, topic,
                                     WAIT_FAILED, -11, 0, 0, 0, 0, 0);
        return 0;
    }
    if (!wxb_ensure_weixin_module()) {
        write_create_chatroom_result(seq, 0, "weixin_module_not_found",
                                     "", normalized, member_count, topic,
                                     WAIT_FAILED, -12, 0, 0, 0, 0, 0);
        return 0;
    }

    arena = (BYTE *)VirtualAlloc(NULL, arena_size,
                                 MEM_COMMIT | MEM_RESERVE,
                                 PAGE_EXECUTE_READWRITE);
    if (!arena) {
        write_create_chatroom_result(seq, 0, "arena_alloc_failed",
                                     "", normalized, member_count, topic,
                                     WAIT_FAILED, -13, 0, 0, 0, 0, 0);
        return 0;
    }
    ZeroMemory(arena, arena_size);
    asm_addr = arena + 0x200;
    task = (CreateChatroomTask *)(arena + 0x1000);
    task->status = 0;
    task->member_count = member_count;
    safe_copy(task->member_ids, sizeof(task->member_ids), normalized);
    safe_copy(task->topic, sizeof(task->topic), topic ? topic : "");

    asm_len = build_add_member_queue_asm(asm_addr,
                                         (int64_t)task,
                                         (int64_t)create_chatroom_worker);
    FlushInstructionCache(GetCurrentProcess(), asm_addr, asm_len);
    queue_send((int64_t)asm_addr);

    if (wait_ms <= 0) wait_ms = 15000;
    loops = wait_ms / 10;
    if (loops < 1) loops = 1;
    for (int i = 0; i < loops; i++) {
        status = InterlockedCompareExchange(&task->status, 0, 0);
        if (status == 1 || status < 0) break;
        Sleep(10);
    }
    status = InterlockedCompareExchange(&task->status, 0, 0);
    if (status == 0 || status == 2) {
        InterlockedCompareExchange(&task->status, -2, status);
        status = InterlockedCompareExchange(&task->status, 0, 0);
    }

    ok = (status == 1) && is_chatroom_id(task->roomid);
    message = ok ? "create_chatroom completed" :
              (status == -2 ? "create_chatroom timeout" :
               status == -3 ? "root_service_init_failed" :
               status == -4 ? "mid_service_init_failed" :
               status == -5 ? "chatroom_service_resolve_failed" :
               status == -1 ? "create_chatroom exception" :
               status == 1 ? "create_chatroom returned no roomid" :
               "create_chatroom failed");
    write_create_chatroom_result(
        seq,
        ok,
        message,
        task->roomid,
        task->member_ids,
        task->member_count,
        task->topic,
        (status == -2) ? WAIT_TIMEOUT : (status == 1 ? WAIT_OBJECT_0 : WAIT_FAILED),
        status,
        task->native_return,
        task->service,
        task->phase,
        task->seh_code,
        task->seh_addr);
    retain_arena(arena);
    return ok;
}

static void add_chatroom_member_worker(int64_t task_addr) {
    AddChatroomMemberTask *task = (AddChatroomMemberTask *)task_addr;
    int64_t base = (int64_t)wxb_weixin_module();
    int64_t root_pair[2] = {0, 0};
    int64_t mid_pair[2] = {0, 0};
    int64_t service_pair[2] = {0, 0};
    BYTE ext_obj[80];
    BYTE roomid_str[32];
    BYTE optional_str[32];
    BYTE member_str[32];
    int64_t members_vec[3] = {0, 0, 0};
    BYTE pool[1024];
    BYTE *pool_ptr = pool;
    BYTE *pool_end = pool + sizeof(pool);
    int ext_initialized = 0;

    if (!task || !base) return;
    if (InterlockedCompareExchange(&task->status, 2, 0) != 0) return;

    ZeroMemory(ext_obj, sizeof(ext_obj));
    ZeroMemory(roomid_str, sizeof(roomid_str));
    ZeroMemory(optional_str, sizeof(optional_str));
    ZeroMemory(member_str, sizeof(member_str));
    ZeroMemory(pool, sizeof(pool));

    __try {
        task->phase = 1;
        ((FnCall1)(base + WX_OFF_ROOM_INIT1))((int64_t)root_pair);
        if (!root_pair[0]) {
            InterlockedExchange(&task->status, -3);
            __leave;
        }

        task->phase = 2;
        ((FnCall3)(base + WX_OFF_ROOM_INIT2))(root_pair[0], (int64_t)mid_pair);
        if (!mid_pair[0]) {
            InterlockedExchange(&task->status, -4);
            __leave;
        }

        task->phase = 3;
        ((FnCall3)(base + WX_OFF_ROOM_INIT3))(mid_pair[0], (int64_t)service_pair);
        task->service = service_pair[0];
        if (!service_pair[0]) {
            InterlockedExchange(&task->status, -5);
            __leave;
        }

        task->phase = 4;
        ((FnCall1)(base + WX_OFF_CHATROOM_ADD_MEMBER_EXT_INIT))((int64_t)ext_obj);
        ext_initialized = 1;
        wx_set_std_string_arena((int64_t)roomid_str,
                                task->roomid,
                                lstrlenA(task->roomid),
                                &pool_ptr,
                                pool_end);
        wx_set_std_string_arena((int64_t)member_str,
                                task->member_wxid,
                                lstrlenA(task->member_wxid),
                                &pool_ptr,
                                pool_end);
        wx_set_std_string_arena((int64_t)optional_str, "", 0, &pool_ptr, pool_end);
        members_vec[0] = (int64_t)member_str;
        members_vec[1] = (int64_t)(member_str + 32);
        members_vec[2] = (int64_t)(member_str + 32);

        task->phase = 5;
        task->ret = ((FnAddChatroomMember)(base + WX_OFF_CHATROOM_ADD_MEMBER))(
            service_pair[0],
            (int64_t)members_vec,
            (int64_t)roomid_str,
            (int64_t)ext_obj,
            (int64_t)optional_str);
        InterlockedExchange(&task->status, 1);
    }
    __except (task->seh_code = GetExceptionCode(),
              task->seh_addr = (uint64_t)GetExceptionInformation()->ExceptionRecord->ExceptionAddress,
              EXCEPTION_EXECUTE_HANDLER) {
        task->ret = -1;
        InterlockedExchange(&task->status, -1);
    }

    if (ext_initialized) {
        __try {
            ((FnCall1)(base + WX_OFF_CHATROOM_ADD_MEMBER_EXT_DTOR))((int64_t)ext_obj);
        }
        __except (EXCEPTION_EXECUTE_HANDLER) {
        }
    }
    add_member_release_shared_pair((int64_t)service_pair);
    add_member_release_shared_pair((int64_t)mid_pair);
    add_member_release_shared_pair((int64_t)root_pair);
}

static void write_add_chatroom_member_result(const char *seq,
                                             int ok,
                                             const char *message,
                                             const char *roomid,
                                             const char *member_wxid,
                                             int wait_status,
                                             LONG queue_status,
                                             int64_t ret,
                                             int64_t service,
                                             int phase,
                                             DWORD seh_code,
                                             uint64_t seh_addr) {
    WCHAR path[1024];
    char json[8192];
    char num[64];
    char *p = json;
    char *end = json + sizeof(json) - 1;

    wsprintfW(path, L"%s\\result_%S.json", CONTROL_DIR, seq ? seq : "");
    append_raw(&p, end, "{\"ok\":");
    append_raw(&p, end, ok ? "true" : "false");
    append_raw(&p, end, ",\"seq\":\"");
    append_escaped(&p, end, seq ? seq : "");
    append_raw(&p, end, "\",\"command\":\"add_room_member\",\"path\":\"native_chatroom_add_member\",\"message\":\"");
    append_escaped(&p, end, message ? message : "");
    append_raw(&p, end, "\",\"roomid\":\"");
    append_escaped(&p, end, roomid ? roomid : "");
    append_raw(&p, end, "\",\"memberWxid\":\"");
    append_escaped(&p, end, member_wxid ? member_wxid : "");
    append_raw(&p, end, "\",\"cgiType\":120,\"endpoint\":\"/cgi-bin/micromsg-bin/addchatroommember\"");
    append_raw(&p, end, ",\"upperCall\":\"sub_181540AA0\",\"upperCallOffset\":\"0x1540aa0\"");
    append_raw(&p, end, ",\"serviceResolve\":\"sub_180020960 -> sub_1802FF1B0 -> sub_1806278F0\"");
    append_raw(&p, end, ",\"serviceOffsets\":\"0x20960,0x2ff1b0,0x6278f0\"");
    append_raw(&p, end, ",\"waitStatus\":");
    wsprintfA(num, "%d", wait_status);
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"queueStatus\":");
    wsprintfA(num, "%ld", queue_status);
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"dispatchResult\":");
    wsprintfA(num, "%I64d", (long long)ret);
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"service\":\"0x");
    wsprintfA(num, "%I64x", (unsigned __int64)service);
    append_raw(&p, end, num);
    append_raw(&p, end, "\",\"phase\":");
    wsprintfA(num, "%d", phase);
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"sehCode\":");
    wsprintfA(num, "%lu", (unsigned long)seh_code);
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"sehAddr\":\"0x");
    wsprintfA(num, "%I64x", (unsigned __int64)seh_addr);
    append_raw(&p, end, num);
    append_raw(&p, end, "\"}");
    *p = 0;
    write_file_bytes(path, json, (DWORD)(p - json));
}

int add_chatroom_member_to_result(const char *seq,
                                  const char *roomid,
                                  const char *member_wxid,
                                  int wait_ms) {
    const SIZE_T arena_size = 0x8000;
    BYTE *arena;
    BYTE *asm_addr;
    AddChatroomMemberTask *task;
    int asm_len;
    LONG status;
    int loops;
    int ok;

    if (!seq || !*seq) return 0;
    if (!roomid || !*roomid) {
        write_add_chatroom_member_result(seq, 0, "missing_roomid", roomid, member_wxid,
                                         WAIT_FAILED, -10, 0, 0, 0, 0, 0);
        return 0;
    }
    if (!member_wxid || !*member_wxid) {
        write_add_chatroom_member_result(seq, 0, "missing_member_wxid", roomid, member_wxid,
                                         WAIT_FAILED, -11, 0, 0, 0, 0, 0);
        return 0;
    }
    if (!wxb_ensure_weixin_module()) {
        write_add_chatroom_member_result(seq, 0, "weixin_module_not_found", roomid, member_wxid,
                                         WAIT_FAILED, -12, 0, 0, 0, 0, 0);
        return 0;
    }

    arena = (BYTE *)VirtualAlloc(NULL, arena_size, MEM_COMMIT | MEM_RESERVE, PAGE_EXECUTE_READWRITE);
    if (!arena) {
        write_add_chatroom_member_result(seq, 0, "arena_alloc_failed", roomid, member_wxid,
                                         WAIT_FAILED, -13, 0, 0, 0, 0, 0);
        return 0;
    }
    ZeroMemory(arena, arena_size);
    asm_addr = arena + 0x200;
    task = (AddChatroomMemberTask *)(arena + 0x1000);
    task->status = 0;
    safe_copy(task->roomid, sizeof(task->roomid), roomid);
    safe_copy(task->member_wxid, sizeof(task->member_wxid), member_wxid);

    asm_len = build_add_member_queue_asm(asm_addr,
                                         (int64_t)task,
                                         (int64_t)add_chatroom_member_worker);
    FlushInstructionCache(GetCurrentProcess(), asm_addr, asm_len);
    queue_send((int64_t)asm_addr);

    if (wait_ms <= 0) wait_ms = 15000;
    loops = wait_ms / 10;
    if (loops < 1) loops = 1;
    for (int i = 0; i < loops; i++) {
        status = InterlockedCompareExchange(&task->status, 0, 0);
        if (status == 1 || status < 0) break;
        Sleep(10);
    }
    status = InterlockedCompareExchange(&task->status, 0, 0);
    if (status == 0 || status == 2) {
        InterlockedCompareExchange(&task->status, -2, status);
        status = InterlockedCompareExchange(&task->status, 0, 0);
    }

    ok = (status == 1) && ((task->ret & 0xFF) != 0);
    write_add_chatroom_member_result(
        seq,
        ok,
        ok ? "add_room_member submitted" :
             (status == -2 ? "add_room_member timeout" :
              status == -3 ? "root_service_init_failed" :
              status == -4 ? "mid_service_init_failed" :
              status == -5 ? "chatroom_service_resolve_failed" :
              status == -1 ? "add_room_member exception" :
              "add_room_member failed"),
        task->roomid,
        task->member_wxid,
        (status == -2) ? WAIT_TIMEOUT : (status == 1 ? WAIT_OBJECT_0 : WAIT_FAILED),
        status,
        task->ret,
        task->service,
        task->phase,
        task->seh_code,
        task->seh_addr);
    retain_arena(arena);
    return ok;
}

static void invite_chatroom_member_worker(int64_t task_addr) {
    InviteChatroomMemberTask *task = (InviteChatroomMemberTask *)task_addr;
    int64_t base = (int64_t)wxb_weixin_module();
    int64_t root_pair[2] = {0, 0};
    int64_t mid_pair[2] = {0, 0};
    int64_t service_pair[2] = {0, 0};
    BYTE ext_obj[80];
    BYTE roomid_str[32];
    BYTE member_str[32];
    int64_t members_vec[3] = {0, 0, 0};
    BYTE pool[1024];
    BYTE *pool_ptr = pool;
    BYTE *pool_end = pool + sizeof(pool);
    int ext_initialized = 0;

    if (!task || !base) return;
    if (InterlockedCompareExchange(&task->status, 2, 0) != 0) return;

    ZeroMemory(ext_obj, sizeof(ext_obj));
    ZeroMemory(roomid_str, sizeof(roomid_str));
    ZeroMemory(member_str, sizeof(member_str));
    ZeroMemory(pool, sizeof(pool));

    __try {
        task->phase = 1;
        ((FnCall1)(base + WX_OFF_ROOM_INIT1))((int64_t)root_pair);
        if (!root_pair[0]) {
            InterlockedExchange(&task->status, -3);
            __leave;
        }

        task->phase = 2;
        ((FnCall3)(base + WX_OFF_ROOM_INIT2))(root_pair[0], (int64_t)mid_pair);
        if (!mid_pair[0]) {
            InterlockedExchange(&task->status, -4);
            __leave;
        }

        task->phase = 3;
        ((FnCall3)(base + WX_OFF_ROOM_INIT3))(mid_pair[0], (int64_t)service_pair);
        task->service = service_pair[0];
        if (!service_pair[0]) {
            InterlockedExchange(&task->status, -5);
            __leave;
        }

        task->phase = 4;
        ((FnCall1)(base + WX_OFF_CHATROOM_ADD_MEMBER_EXT_INIT))((int64_t)ext_obj);
        ext_initialized = 1;
        wx_set_std_string_arena((int64_t)roomid_str,
                                task->roomid,
                                lstrlenA(task->roomid),
                                &pool_ptr,
                                pool_end);
        wx_set_std_string_arena((int64_t)member_str,
                                task->member_wxid,
                                lstrlenA(task->member_wxid),
                                &pool_ptr,
                                pool_end);
        members_vec[0] = (int64_t)member_str;
        members_vec[1] = (int64_t)(member_str + 32);
        members_vec[2] = (int64_t)(member_str + 32);

        task->phase = 5;
        task->ret = ((FnInviteChatroomMember)(base + WX_OFF_CHATROOM_INVITE_MEMBER))(
            service_pair[0],
            (int64_t)members_vec,
            (int64_t)roomid_str,
            (int64_t)ext_obj);
        InterlockedExchange(&task->status, 1);
    }
    __except (task->seh_code = GetExceptionCode(),
              task->seh_addr = (uint64_t)GetExceptionInformation()->ExceptionRecord->ExceptionAddress,
              EXCEPTION_EXECUTE_HANDLER) {
        task->ret = -1;
        InterlockedExchange(&task->status, -1);
    }

    if (ext_initialized) {
        __try {
            ((FnCall1)(base + WX_OFF_CHATROOM_ADD_MEMBER_EXT_DTOR))((int64_t)ext_obj);
        }
        __except (EXCEPTION_EXECUTE_HANDLER) {
        }
    }
    add_member_release_shared_pair((int64_t)service_pair);
    add_member_release_shared_pair((int64_t)mid_pair);
    add_member_release_shared_pair((int64_t)root_pair);
}

static void write_invite_chatroom_member_result(const char *seq,
                                                int ok,
                                                const char *message,
                                                const char *roomid,
                                                const char *member_wxid,
                                                int wait_status,
                                                LONG queue_status,
                                                int64_t ret,
                                                int64_t service,
                                                int phase,
                                                DWORD seh_code,
                                                uint64_t seh_addr) {
    WCHAR path[1024];
    char json[8192];
    char num[64];
    char *p = json;
    char *end = json + sizeof(json) - 1;

    wsprintfW(path, L"%s\\result_%S.json", CONTROL_DIR, seq ? seq : "");
    append_raw(&p, end, "{\"ok\":");
    append_raw(&p, end, ok ? "true" : "false");
    append_raw(&p, end, ",\"seq\":\"");
    append_escaped(&p, end, seq ? seq : "");
    append_raw(&p, end, "\",\"command\":\"invite_room_member\",\"path\":\"native_chatroom_invite_member\",\"message\":\"");
    append_escaped(&p, end, message ? message : "");
    append_raw(&p, end, "\",\"roomid\":\"");
    append_escaped(&p, end, roomid ? roomid : "");
    append_raw(&p, end, "\",\"memberWxid\":\"");
    append_escaped(&p, end, member_wxid ? member_wxid : "");
    append_raw(&p, end, "\",\"cgiType\":610,\"endpoint\":\"/cgi-bin/micromsg-bin/invitechatroommember\"");
    append_raw(&p, end, ",\"upperCall\":\"sub_18154E270\",\"upperCallOffset\":\"0x154e270\"");
    append_raw(&p, end, ",\"cgiCall\":\"sub_1815501A0\",\"cgiCallOffset\":\"0x15501a0\"");
    append_raw(&p, end, ",\"serviceResolve\":\"sub_180020960 -> sub_1802FF1B0 -> sub_1806278F0\"");
    append_raw(&p, end, ",\"serviceOffsets\":\"0x20960,0x2ff1b0,0x6278f0\"");
    append_raw(&p, end, ",\"waitStatus\":");
    wsprintfA(num, "%d", wait_status);
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"queueStatus\":");
    wsprintfA(num, "%ld", queue_status);
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"dispatchResult\":");
    wsprintfA(num, "%I64d", (long long)ret);
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"service\":\"0x");
    wsprintfA(num, "%I64x", (unsigned __int64)service);
    append_raw(&p, end, num);
    append_raw(&p, end, "\",\"phase\":");
    wsprintfA(num, "%d", phase);
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"sehCode\":");
    wsprintfA(num, "%lu", (unsigned long)seh_code);
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"sehAddr\":\"0x");
    wsprintfA(num, "%I64x", (unsigned __int64)seh_addr);
    append_raw(&p, end, num);
    append_raw(&p, end, "\"}");
    *p = 0;
    write_file_bytes(path, json, (DWORD)(p - json));
}

int invite_chatroom_member_to_result(const char *seq,
                                     const char *roomid,
                                     const char *member_wxid,
                                     int wait_ms) {
    const SIZE_T arena_size = 0x8000;
    BYTE *arena;
    BYTE *asm_addr;
    InviteChatroomMemberTask *task;
    int asm_len;
    LONG status;
    int loops;
    int ok;

    if (!seq || !*seq) return 0;
    if (!roomid || !*roomid) {
        write_invite_chatroom_member_result(seq, 0, "missing_roomid", roomid, member_wxid,
                                            WAIT_FAILED, -10, 0, 0, 0, 0, 0);
        return 0;
    }
    if (!member_wxid || !*member_wxid) {
        write_invite_chatroom_member_result(seq, 0, "missing_member_wxid", roomid, member_wxid,
                                            WAIT_FAILED, -11, 0, 0, 0, 0, 0);
        return 0;
    }
    if (!wxb_ensure_weixin_module()) {
        write_invite_chatroom_member_result(seq, 0, "weixin_module_not_found", roomid, member_wxid,
                                            WAIT_FAILED, -12, 0, 0, 0, 0, 0);
        return 0;
    }

    arena = (BYTE *)VirtualAlloc(NULL, arena_size, MEM_COMMIT | MEM_RESERVE, PAGE_EXECUTE_READWRITE);
    if (!arena) {
        write_invite_chatroom_member_result(seq, 0, "arena_alloc_failed", roomid, member_wxid,
                                            WAIT_FAILED, -13, 0, 0, 0, 0, 0);
        return 0;
    }
    ZeroMemory(arena, arena_size);
    asm_addr = arena + 0x200;
    task = (InviteChatroomMemberTask *)(arena + 0x1000);
    task->status = 0;
    safe_copy(task->roomid, sizeof(task->roomid), roomid);
    safe_copy(task->member_wxid, sizeof(task->member_wxid), member_wxid);

    asm_len = build_add_member_queue_asm(asm_addr,
                                         (int64_t)task,
                                         (int64_t)invite_chatroom_member_worker);
    FlushInstructionCache(GetCurrentProcess(), asm_addr, asm_len);
    queue_send((int64_t)asm_addr);

    if (wait_ms <= 0) wait_ms = 15000;
    loops = wait_ms / 10;
    if (loops < 1) loops = 1;
    for (int i = 0; i < loops; i++) {
        status = InterlockedCompareExchange(&task->status, 0, 0);
        if (status == 1 || status < 0) break;
        Sleep(10);
    }
    status = InterlockedCompareExchange(&task->status, 0, 0);
    if (status == 0 || status == 2) {
        InterlockedCompareExchange(&task->status, -2, status);
        status = InterlockedCompareExchange(&task->status, 0, 0);
    }

    ok = (status == 1) && ((task->ret & 0xFF) != 0);
    write_invite_chatroom_member_result(
        seq,
        ok,
        ok ? "invite_room_member submitted" :
             (status == -2 ? "invite_room_member timeout" :
              status == -3 ? "root_service_init_failed" :
              status == -4 ? "mid_service_init_failed" :
              status == -5 ? "chatroom_service_resolve_failed" :
              status == -1 ? "invite_room_member exception" :
              "invite_room_member failed"),
        task->roomid,
        task->member_wxid,
        (status == -2) ? WAIT_TIMEOUT : (status == 1 ? WAIT_OBJECT_0 : WAIT_FAILED),
        status,
        task->ret,
        task->service,
        task->phase,
        task->seh_code,
        task->seh_addr);
    retain_arena(arena);
    return ok;
}

static void del_chatroom_member_worker(int64_t task_addr) {
    DelChatroomMemberTask *task = (DelChatroomMemberTask *)task_addr;
    int64_t base = (int64_t)wxb_weixin_module();
    int64_t root_pair[2] = {0, 0};
    int64_t mid_pair[2] = {0, 0};
    int64_t service_pair[2] = {0, 0};
    BYTE out_obj[48];
    BYTE roomid_str[32];
    BYTE member_str[32];
    int64_t members_vec[3] = {0, 0, 0};
    BYTE pool[1024];
    BYTE *pool_ptr = pool;
    BYTE *pool_end = pool + sizeof(pool);

    if (!task || !base) return;
    if (InterlockedCompareExchange(&task->status, 2, 0) != 0) return;

    ZeroMemory(out_obj, sizeof(out_obj));
    ZeroMemory(roomid_str, sizeof(roomid_str));
    ZeroMemory(member_str, sizeof(member_str));
    ZeroMemory(pool, sizeof(pool));

    __try {
        task->phase = 1;
        ((FnCall1)(base + WX_OFF_ROOM_INIT1))((int64_t)root_pair);
        if (!root_pair[0]) {
            InterlockedExchange(&task->status, -3);
            __leave;
        }

        task->phase = 2;
        ((FnCall3)(base + WX_OFF_ROOM_INIT2))(root_pair[0], (int64_t)mid_pair);
        if (!mid_pair[0]) {
            InterlockedExchange(&task->status, -4);
            __leave;
        }

        task->phase = 3;
        ((FnCall3)(base + WX_OFF_ROOM_INIT3))(mid_pair[0], (int64_t)service_pair);
        task->service = service_pair[0];
        if (!service_pair[0]) {
            InterlockedExchange(&task->status, -5);
            __leave;
        }

        task->phase = 4;
        wx_set_std_string_arena((int64_t)roomid_str,
                                task->roomid,
                                lstrlenA(task->roomid),
                                &pool_ptr,
                                pool_end);
        wx_set_std_string_arena((int64_t)member_str,
                                task->member_wxid,
                                lstrlenA(task->member_wxid),
                                &pool_ptr,
                                pool_end);
        members_vec[0] = (int64_t)member_str;
        members_vec[1] = (int64_t)(member_str + 32);
        members_vec[2] = (int64_t)(member_str + 32);

        task->phase = 5;
        task->ret = ((FnDelChatroomMember)(base + WX_OFF_CHATROOM_DEL_MEMBER))(
            service_pair[0],
            (int64_t)out_obj,
            (int64_t)members_vec,
            (int64_t)roomid_str,
            task->flag);
        task->out_success = out_obj[32];
        wx_read_sso_str((int64_t)out_obj, task->out_text, (int)sizeof(task->out_text));
        InterlockedExchange(&task->status, 1);
    }
    __except (task->seh_code = GetExceptionCode(),
              task->seh_addr = (uint64_t)GetExceptionInformation()->ExceptionRecord->ExceptionAddress,
              EXCEPTION_EXECUTE_HANDLER) {
        task->ret = -1;
        InterlockedExchange(&task->status, -1);
    }

    __try {
        wx_free_std_string_heap(base, (int64_t)out_obj);
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
    }
    add_member_release_shared_pair((int64_t)service_pair);
    add_member_release_shared_pair((int64_t)mid_pair);
    add_member_release_shared_pair((int64_t)root_pair);
}

static void quit_chatroom_worker(int64_t task_addr) {
    QuitChatroomTask *task = (QuitChatroomTask *)task_addr;
    int64_t base = (int64_t)wxb_weixin_module();
    BYTE quit_obj[64];
    BYTE *pool_ptr;
    BYTE *pool_end;

    if (!task || !base) return;
    if (InterlockedCompareExchange(&task->status, 2, 0) != 0) return;

    ZeroMemory(quit_obj, sizeof(quit_obj));
    ZeroMemory(task->scratch, sizeof(task->scratch));
    pool_ptr = task->scratch;
    pool_end = task->scratch + sizeof(task->scratch);

    __try {
        task->phase = 1;
        wx_set_std_string_arena((int64_t)(quit_obj + 8),
                                task->roomid,
                                lstrlenA(task->roomid),
                                &pool_ptr,
                                pool_end);
        quit_obj[40] = (BYTE)task->flag;

        task->phase = 2;
        task->ret = ((FnQuitChatroomTask)(base + WX_OFF_CHATROOM_QUIT_TASK))((int64_t)quit_obj);
        InterlockedExchange(&task->status, 1);
    }
    __except (task->seh_code = GetExceptionCode(),
              task->seh_addr = (uint64_t)GetExceptionInformation()->ExceptionRecord->ExceptionAddress,
              EXCEPTION_EXECUTE_HANDLER) {
        task->ret = -1;
        InterlockedExchange(&task->status, -1);
    }
}

static void write_del_chatroom_member_result(const char *seq,
                                             int ok,
                                             const char *message,
                                             const char *roomid,
                                             const char *member_wxid,
                                             int wait_status,
                                             LONG queue_status,
                                             int64_t ret,
                                             int64_t service,
                                             int phase,
                                             int flag,
                                             BYTE out_success,
                                             const char *out_text,
                                             DWORD seh_code,
                                             uint64_t seh_addr) {
    WCHAR path[1024];
    char json[8192];
    char num[64];
    char *p = json;
    char *end = json + sizeof(json) - 1;

    wsprintfW(path, L"%s\\result_%S.json", CONTROL_DIR, seq ? seq : "");
    append_raw(&p, end, "{\"ok\":");
    append_raw(&p, end, ok ? "true" : "false");
    append_raw(&p, end, ",\"seq\":\"");
    append_escaped(&p, end, seq ? seq : "");
    append_raw(&p, end, "\",\"command\":\"del_room_member\",\"path\":\"native_chatroom_del_member\",\"message\":\"");
    append_escaped(&p, end, message ? message : "");
    append_raw(&p, end, "\",\"roomid\":\"");
    append_escaped(&p, end, roomid ? roomid : "");
    append_raw(&p, end, "\",\"memberWxid\":\"");
    append_escaped(&p, end, member_wxid ? member_wxid : "");
    append_raw(&p, end, "\",\"cgiType\":179,\"endpoint\":\"/cgi-bin/micromsg-bin/delchatroommember\"");
    append_raw(&p, end, ",\"upperCall\":\"sub_18154AB60\",\"upperCallOffset\":\"0x154ab60\"");
    append_raw(&p, end, ",\"lowerCall\":\"sub_18154D310\",\"lowerCallOffset\":\"0x154d310\"");
    append_raw(&p, end, ",\"cgiCall\":\"sub_18156F080\",\"cgiCallOffset\":\"0x156f080\"");
    append_raw(&p, end, ",\"serviceResolve\":\"sub_180020960 -> sub_1802FF1B0 -> sub_1806278F0\"");
    append_raw(&p, end, ",\"serviceOffsets\":\"0x20960,0x2ff1b0,0x6278f0\"");
    append_raw(&p, end, ",\"flag\":");
    wsprintfA(num, "%d", flag);
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"outSuccess\":");
    append_raw(&p, end, out_success ? "true" : "false");
    append_raw(&p, end, ",\"outText\":\"");
    append_escaped(&p, end, out_text ? out_text : "");
    append_raw(&p, end, "\",\"waitStatus\":");
    wsprintfA(num, "%d", wait_status);
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"queueStatus\":");
    wsprintfA(num, "%ld", queue_status);
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"dispatchResult\":");
    wsprintfA(num, "%I64d", (long long)ret);
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"service\":\"0x");
    wsprintfA(num, "%I64x", (unsigned __int64)service);
    append_raw(&p, end, num);
    append_raw(&p, end, "\",\"phase\":");
    wsprintfA(num, "%d", phase);
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"sehCode\":");
    wsprintfA(num, "%lu", (unsigned long)seh_code);
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"sehAddr\":\"0x");
    wsprintfA(num, "%I64x", (unsigned __int64)seh_addr);
    append_raw(&p, end, num);
    append_raw(&p, end, "\"}");
    *p = 0;
    write_file_bytes(path, json, (DWORD)(p - json));
}

int del_chatroom_member_to_result(const char *seq,
                                  const char *roomid,
                                  const char *member_wxid,
                                  int wait_ms) {
    const SIZE_T arena_size = 0x8000;
    BYTE *arena;
    BYTE *asm_addr;
    DelChatroomMemberTask *task;
    int asm_len;
    LONG status;
    int loops;
    int ok;
    const char *message;

    if (!seq || !*seq) return 0;
    if (!roomid || !*roomid) {
        write_del_chatroom_member_result(seq, 0, "missing_roomid", roomid, member_wxid,
                                         WAIT_FAILED, -10, 0, 0, 0, 0, 0, "", 0, 0);
        return 0;
    }
    if (!member_wxid || !*member_wxid) {
        write_del_chatroom_member_result(seq, 0, "missing_member_wxid", roomid, member_wxid,
                                         WAIT_FAILED, -11, 0, 0, 0, 0, 0, "", 0, 0);
        return 0;
    }
    if (!wxb_ensure_weixin_module()) {
        write_del_chatroom_member_result(seq, 0, "weixin_module_not_found", roomid, member_wxid,
                                         WAIT_FAILED, -12, 0, 0, 0, 0, 0, "", 0, 0);
        return 0;
    }

    arena = (BYTE *)VirtualAlloc(NULL, arena_size, MEM_COMMIT | MEM_RESERVE, PAGE_EXECUTE_READWRITE);
    if (!arena) {
        write_del_chatroom_member_result(seq, 0, "arena_alloc_failed", roomid, member_wxid,
                                         WAIT_FAILED, -13, 0, 0, 0, 0, 0, "", 0, 0);
        return 0;
    }
    ZeroMemory(arena, arena_size);
    asm_addr = arena + 0x200;
    task = (DelChatroomMemberTask *)(arena + 0x1000);
    task->status = 0;
    task->flag = 0;
    safe_copy(task->roomid, sizeof(task->roomid), roomid);
    safe_copy(task->member_wxid, sizeof(task->member_wxid), member_wxid);

    asm_len = build_add_member_queue_asm(asm_addr,
                                         (int64_t)task,
                                         (int64_t)del_chatroom_member_worker);
    FlushInstructionCache(GetCurrentProcess(), asm_addr, asm_len);
    queue_send((int64_t)asm_addr);

    if (wait_ms <= 0) wait_ms = 15000;
    loops = wait_ms / 10;
    if (loops < 1) loops = 1;
    for (int i = 0; i < loops; i++) {
        status = InterlockedCompareExchange(&task->status, 0, 0);
        if (status == 1 || status < 0) break;
        Sleep(10);
    }
    status = InterlockedCompareExchange(&task->status, 0, 0);
    if (status == 0 || status == 2) {
        InterlockedCompareExchange(&task->status, -2, status);
        status = InterlockedCompareExchange(&task->status, 0, 0);
    }

    ok = (status == 1) && (task->out_success != 0);
    message = ok ? "del_room_member submitted" :
              (status == -2 ? "del_room_member timeout" :
               status == -3 ? "root_service_init_failed" :
               status == -4 ? "mid_service_init_failed" :
               status == -5 ? "chatroom_service_resolve_failed" :
               status == -1 ? "del_room_member exception" :
               (task->out_text[0] ? task->out_text : "del_room_member failed"));
    write_del_chatroom_member_result(
        seq,
        ok,
        message,
        task->roomid,
        task->member_wxid,
        (status == -2) ? WAIT_TIMEOUT : (status == 1 ? WAIT_OBJECT_0 : WAIT_FAILED),
        status,
        task->ret,
        task->service,
        task->phase,
        task->flag,
        task->out_success,
        task->out_text,
        task->seh_code,
        task->seh_addr);
    retain_arena(arena);
    return ok;
}

static void write_leave_chatroom_result(const char *seq,
                                        int ok,
                                        const char *message,
                                        const char *roomid,
                                        const char *self_wxid,
                                        const char *self_source,
                                        int wait_status,
                                        LONG queue_status,
                                        int64_t ret,
                                        int64_t service,
                                        int phase,
                                        int flag,
                                        BYTE out_success,
                                        const char *out_text,
                                        DWORD seh_code,
                                        uint64_t seh_addr) {
    WCHAR path[1024];
    char json[8192];
    char num[64];
    char *p = json;
    char *end = json + sizeof(json) - 1;

    wsprintfW(path, L"%s\\result_%S.json", CONTROL_DIR, seq ? seq : "");
    append_raw(&p, end, "{\"ok\":");
    append_raw(&p, end, ok ? "true" : "false");
    append_raw(&p, end, ",\"seq\":\"");
    append_escaped(&p, end, seq ? seq : "");
    append_raw(&p, end, "\",\"command\":\"leave_chatroom\",\"path\":\"native_chatroom_leave\",\"message\":\"");
    append_escaped(&p, end, message ? message : "");
    append_raw(&p, end, "\",\"roomid\":\"");
    append_escaped(&p, end, roomid ? roomid : "");
    append_raw(&p, end, "\",\"selfWxid\":\"");
    append_escaped(&p, end, self_wxid ? self_wxid : "");
    append_raw(&p, end, "\",\"memberWxid\":\"");
    append_escaped(&p, end, self_wxid ? self_wxid : "");
    append_raw(&p, end, "\",\"selfWxidSource\":\"");
    append_escaped(&p, end, self_source ? self_source : "");
    append_raw(&p, end, "\",\"cgiType\":681,\"endpoint\":\"/cgi-bin/micromsg-bin/oplog\"");
    append_raw(&p, end, ",\"upperCall\":\"sub_18068D0E0\",\"upperCallOffset\":\"0x68d0e0\"");
    append_raw(&p, end, ",\"oplogWrapper\":\"sub_180ECE2F0\",\"oplogWrapperOffset\":\"0xece2f0\"");
    append_raw(&p, end, ",\"quitCall\":\"sub_1817F4220\",\"quitCallOffset\":\"0x17f4220\"");
    append_raw(&p, end, ",\"lowerCall\":\"sub_1817F1EE0\",\"lowerCallOffset\":\"0x17f1ee0\"");
    append_raw(&p, end, ",\"cgiCall\":\"sub_1817F9970\",\"cgiCallOffset\":\"0x17f9970\"");
    append_raw(&p, end, ",\"serviceResolve\":\"sub_180020960 -> sub_1802FF1B0 -> sub_18036E850; OplogManager via sub_180650B20\"");
    append_raw(&p, end, ",\"serviceOffsets\":\"0x20960,0x2ff1b0,0x36e850,0x650b20\"");
    append_raw(&p, end, ",\"requestProto\":\"micromsg.QuitChatRoom\"");
    append_raw(&p, end, ",\"oplogCmd\":16");
    append_raw(&p, end, ",\"semantic\":\"quit_chatroom_by_oplog_cmd_0x10\"");
    append_raw(&p, end, ",\"flag\":");
    wsprintfA(num, "%d", flag);
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"outSuccess\":");
    append_raw(&p, end, out_success ? "true" : "false");
    append_raw(&p, end, ",\"outText\":\"");
    append_escaped(&p, end, out_text ? out_text : "");
    append_raw(&p, end, "\",\"waitStatus\":");
    wsprintfA(num, "%d", wait_status);
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"queueStatus\":");
    wsprintfA(num, "%ld", queue_status);
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"dispatchResult\":");
    wsprintfA(num, "%I64d", (long long)ret);
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"service\":\"0x");
    wsprintfA(num, "%I64x", (unsigned __int64)service);
    append_raw(&p, end, num);
    append_raw(&p, end, "\",\"phase\":");
    wsprintfA(num, "%d", phase);
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"sehCode\":");
    wsprintfA(num, "%lu", (unsigned long)seh_code);
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"sehAddr\":\"0x");
    wsprintfA(num, "%I64x", (unsigned __int64)seh_addr);
    append_raw(&p, end, num);
    append_raw(&p, end, "\"}");
    *p = 0;
    write_file_bytes(path, json, (DWORD)(p - json));
}

int leave_chatroom_to_result(const char *seq,
                             const char *roomid,
                             const char *self_wxid,
                             int wait_ms,
                             int flag) {
    const SIZE_T arena_size = 0x8000;
    BYTE *arena;
    BYTE *asm_addr;
    QuitChatroomTask *task;
    int asm_len;
    LONG status;
    int loops;
    int ok;
    const char *message;
    char effective_self[ADD_MEMBER_WXID_MAX];
    const char *self_source = "argument";

    if (!seq || !*seq) return 0;
    effective_self[0] = 0;
    if (self_wxid && *self_wxid && !str_eq(self_wxid, "-") &&
        !str_eq(self_wxid, "0") && !str_eq(self_wxid, "auto")) {
        safe_copy(effective_self, sizeof(effective_self), self_wxid);
    } else if (wxb_self_wxid()[0]) {
        safe_copy(effective_self, sizeof(effective_self), wxb_self_wxid());
        self_source = "runtime";
    } else {
        self_source = "unknown";
    }

    if (!roomid || !*roomid) {
        write_leave_chatroom_result(seq, 0, "missing_roomid", roomid, effective_self, self_source,
                                    WAIT_FAILED, -10, 0, 0, 0, 0, 0, "", 0, 0);
        return 0;
    }
    if (!wxb_ensure_weixin_module()) {
        write_leave_chatroom_result(seq, 0, "weixin_module_not_found", roomid, effective_self, self_source,
                                    WAIT_FAILED, -12, 0, 0, 0, 0, 0, "", 0, 0);
        return 0;
    }

    arena = (BYTE *)VirtualAlloc(NULL, arena_size, MEM_COMMIT | MEM_RESERVE, PAGE_EXECUTE_READWRITE);
    if (!arena) {
        write_leave_chatroom_result(seq, 0, "arena_alloc_failed", roomid, effective_self, self_source,
                                    WAIT_FAILED, -13, 0, 0, 0, 0, 0, "", 0, 0);
        return 0;
    }
    ZeroMemory(arena, arena_size);
    asm_addr = arena + 0x200;
    task = (QuitChatroomTask *)(arena + 0x1000);
    task->status = 0;
    task->flag = flag;
    safe_copy(task->roomid, sizeof(task->roomid), roomid);

    asm_len = build_add_member_queue_asm(asm_addr,
                                         (int64_t)task,
                                         (int64_t)quit_chatroom_worker);
    FlushInstructionCache(GetCurrentProcess(), asm_addr, asm_len);
    queue_send((int64_t)asm_addr);

    if (wait_ms <= 0) wait_ms = 15000;
    loops = wait_ms / 10;
    if (loops < 1) loops = 1;
    for (int i = 0; i < loops; i++) {
        status = InterlockedCompareExchange(&task->status, 0, 0);
        if (status == 1 || status < 0) break;
        Sleep(10);
    }
    status = InterlockedCompareExchange(&task->status, 0, 0);
    if (status == 0 || status == 2) {
        InterlockedCompareExchange(&task->status, -2, status);
        status = InterlockedCompareExchange(&task->status, 0, 0);
    }

    ok = (status == 1);
    message = ok ? "leave_chatroom submitted" :
              (status == -2 ? "leave_chatroom timeout" :
               status == -1 ? "leave_chatroom exception" :
               "leave_chatroom failed");
    write_leave_chatroom_result(
        seq,
        ok,
        message,
        task->roomid,
        effective_self,
        self_source,
        (status == -2) ? WAIT_TIMEOUT : (status == 1 ? WAIT_OBJECT_0 : WAIT_FAILED),
        status,
        task->ret,
        0,
        task->phase,
        task->flag,
        (BYTE)ok,
        "",
        task->seh_code,
        task->seh_addr);
    retain_arena(arena);
    return ok;
}
