/*
 * Module: retained forward send-item/model cache.
 */

#include "../include/wx_bridge_forward_retained.h"
#include "../include/wx_bridge_forward_builders.h"
#include "../include/wx_bridge_debug_log.h"
#include "../include/wx_bridge_file_io.h"
#include "../include/wx_bridge_memory.h"
#include "../include/wx_bridge_native_call.h"
#include "../include/wx_bridge_offsets.h"
#include "../include/wx_bridge_paths.h"
#include "../include/wx_bridge_runtime.h"
#include "../include/wx_bridge_text.h"

#define SI_CACHE_SIZE 1024
#define SI_ITEM_BYTES 904
#define MODEL_CACHE_SIZE 512

typedef struct RetainedSI {
    int64_t msgid;
    BYTE item[SI_ITEM_BYTES];
    void *owned_arena;
    volatile LONG origin; /* 1=receive hook, 2=persistent XML hydrate */
    volatile LONG used;
} RetainedSI;

typedef struct RetainedModel {
    int64_t msgid;
    BYTE model[MODEL_ITEM_BYTES];
    int32_t local_id;
    int32_t msg_type;
    int32_t msg_subtype;
    volatile LONG used;
} RetainedModel;

typedef struct ForwardRetainedState {
    RetainedSI si_cache[SI_CACHE_SIZE];
    volatile LONG si_cache_pos;
    RetainedModel model_cache[MODEL_CACHE_SIZE];
    volatile LONG model_cache_pos;
} ForwardRetainedState;

static ForwardRetainedState g_forward_retained_state = {0};

static void release_retained_si_slot(RetainedSI *slot) {
    void *arena;
    if (!slot) return;
    arena = slot->owned_arena;
    slot->owned_arena = NULL;
    if (arena) VirtualFree(arena, 0, MEM_RELEASE);
}

static unsigned __int64 a2u64_hex(const char *s) {
    unsigned __int64 v = 0;
    if (!s) return 0;
    while (*s == ' ') s++;
    if (s[0] == '0' && (s[1] == 'x' || s[1] == 'X')) s += 2;
    while (*s) {
        char c = *s++;
        if (c >= '0' && c <= '9') v = (v << 4) | (unsigned __int64)(c - '0');
        else if (c >= 'a' && c <= 'f') v = (v << 4) | (unsigned __int64)(c - 'a' + 10);
        else if (c >= 'A' && c <= 'F') v = (v << 4) | (unsigned __int64)(c - 'A' + 10);
        else break;
    }
    return v;
}

static int split_tabs(char *line, char **fields, int max_fields) {
    int n = 0;
    char *p = line;
    if (!line || !fields || max_fields <= 0) return 0;
    while (n < max_fields) {
        fields[n++] = p;
        while (*p && *p != '\t') p++;
        if (!*p) break;
        *p++ = 0;
    }
    return n;
}

static void persist_retained_ptr(char kind, int64_t msgid, int64_t ptr) {
    char line[256];
    int n;
    if (!msgid || !ptr || !wxb_weixin_module()) return;
    n = wsprintfA(line, "%c\t%lu\t%I64x\t%I64d\t%I64x\r\n",
                  kind,
                  (unsigned long)GetCurrentProcessId(),
                  (unsigned __int64)(int64_t)wxb_weixin_module(),
                  (long long)msgid,
                  (unsigned __int64)ptr);
    if (n > 0) append_file_bytes(FORWARD_CACHE_FILE, line, (DWORD)n);
}

static void *find_persisted_retained_ptr(char kind, int64_t msgid,
                                         int64_t expected_vt, SIZE_T need,
                                         int id_off,
                                         const char *label) {
    const DWORD max = 262144;
    char *buf;
    int n;
    void *candidate = NULL;
    if (!msgid || !wxb_weixin_module()) return NULL;
    buf = (char *)VirtualAlloc(NULL, max, MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE);
    if (!buf) return NULL;
    n = read_file_tail_bytes(FORWARD_CACHE_FILE, buf, max);
    if (n > 0) {
        char *line = buf;
        char *end = buf + n;
        while (line < end && *line) {
            char *e = line;
            char *fields[5] = {0};
            while (e < end && *e && *e != '\r' && *e != '\n') e++;
            if (e < end) *e++ = 0;
            while (e < end && (*e == '\r' || *e == '\n')) e++;
            if (line[0] == kind && line[1] == '\t' && split_tabs(line, fields, 5) >= 5) {
                DWORD pid = (DWORD)a2i64(fields[1]);
                int64_t base = (int64_t)a2u64_hex(fields[2]);
                int64_t id = a2i64(fields[3]);
                int64_t ptr = (int64_t)a2u64_hex(fields[4]);
                if (pid == GetCurrentProcessId() && base == (int64_t)wxb_weixin_module() && id == msgid &&
                    ptr && is_readable_ptr(ptr, need) && r64((void *)ptr) == expected_vt &&
                    (!id_off || r64((void *)(ptr + id_off)) == msgid)) {
                    candidate = (void *)ptr;
                }
            }
            line = e;
        }
    }
    if (candidate) {
        char dbg[256];
        wsprintfA(dbg, "[forward-cache] restored %s msgid=%I64d ptr=%I64x from persisted index",
                  label ? label : "ptr", (long long)msgid, (unsigned __int64)(int64_t)candidate);
        forward_dbg(dbg);
    }
    VirtualFree(buf, 0, MEM_RELEASE);
    return candidate;
}

void *find_retained_si(int64_t msgid) {
    ForwardRetainedState *s = &g_forward_retained_state;
    if (!msgid) return NULL;
    for (int i = 0; i < SI_CACHE_SIZE; i++) {
        if (InterlockedCompareExchange(&s->si_cache[i].used, 0, 0) == 1 &&
            s->si_cache[i].msgid == msgid) {
            return s->si_cache[i].item;
        }
    }
    return find_persisted_retained_ptr('S', msgid,
                                       (int64_t)wxb_weixin_module() + WX_OFF_SEND_ITEM_VT,
                                       SI_ITEM_BYTES, 0x98, "send_item");
}

int retained_si_origin(int64_t msgid) {
    ForwardRetainedState *s = &g_forward_retained_state;
    if (!msgid) return 0;
    for (int i = 0; i < SI_CACHE_SIZE; i++) {
        if (InterlockedCompareExchange(&s->si_cache[i].used, 0, 0) == 1 &&
            s->si_cache[i].msgid == msgid) {
            return (int)InterlockedCompareExchange(&s->si_cache[i].origin, 0, 0);
        }
    }
    return 0;
}

static void clear_synthetic_retained_si(int64_t msgid) {
    ForwardRetainedState *s = &g_forward_retained_state;
    if (!msgid) return;
    for (int i = 0; i < SI_CACHE_SIZE; i++) {
        RetainedSI *slot = &s->si_cache[i];
        if (InterlockedCompareExchange(&slot->used, 0, 0) == 1 &&
            slot->msgid == msgid &&
            InterlockedCompareExchange(&slot->origin, 0, 0) != 1) {
            InterlockedExchange(&slot->used, 0);
            InterlockedExchange(&slot->origin, 0);
            release_retained_si_slot(slot);
        }
    }
}

static int retain_send_item_from_model(int64_t msgid, int64_t msg_base, int origin, void *owned_arena, const char *label) {
    ForwardRetainedState *s = &g_forward_retained_state;
    int64_t base = (int64_t)wxb_weixin_module();
    if (!base || !msg_base || !msgid) return 0;
    if (origin == 1 && retained_si_origin(msgid) == 1 && find_retained_si(msgid)) return 1;
    int pos = InterlockedIncrement(&s->si_cache_pos) % SI_CACHE_SIZE;
    RetainedSI *slot = &s->si_cache[pos];
    __try {
        InterlockedExchange(&slot->used, 0);
        InterlockedExchange(&slot->origin, 0);
        release_retained_si_slot(slot);
        ZeroMemory(slot->item, sizeof(slot->item));
        ((FnCall3)(base + WX_OFF_MSG_TO_SEND_ITEM))((int64_t)msg_base, (int64_t)slot->item);
        if (r64(slot->item) != base + WX_OFF_SEND_ITEM_VT) {
            forward_dbg("[forward-retain] converter did not write send_item vtable");
            return 0;
        }
        slot->msgid = msgid;
        slot->owned_arena = owned_arena;
        InterlockedExchange(&slot->origin, origin);
        InterlockedExchange(&slot->used, 1);
        persist_retained_ptr('S', msgid, (int64_t)slot->item);
        {
            char dbg[256];
            wsprintfA(dbg,
                      "[forward-retain] msgid=%I64d slot=%d origin=%d label=%s svrid=%I64x type=%d/%d ok",
                      (long long)msgid, pos, origin, label ? label : "",
                      (unsigned __int64)r64((void *)((int64_t)slot->item + 0x98)),
                      r32((void *)((int64_t)slot->item + 0x08)),
                      r32((void *)((int64_t)slot->item + 0x0C)));
            forward_dbg(dbg);
        }
        return 1;
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
        InterlockedExchange(&slot->used, 0);
        InterlockedExchange(&slot->origin, 0);
        forward_dbg("[forward-retain] EXCEPTION in converter sub_1809D8F80");
        return 0;
    }
}

void retain_send_item(int64_t msgid, int64_t msg_base) {
    if (retained_si_origin(msgid) != 1) clear_synthetic_retained_si(msgid);
    (void)retain_send_item_from_model(msgid, msg_base, 1, NULL, "receive");
}

int hydrate_retained_send_item_from_fields(int64_t msgid,
                                           const char *content,
                                           int source_type,
                                           int source_subtype,
                                           int source_local_id,
                                           int32_t source_time,
                                           const char *source_from_wxid,
                                           const char *source_to_wxid,
                                           const char *source_sender_wxid,
                                           const char *source_msgsource) {
    int64_t base = (int64_t)wxb_weixin_module();
    const SIZE_T arena_size = 0x30000;
    BYTE *arena;
    int64_t src_ctl;
    int64_t src_msg;
    BYTE *pool;
    BYTE *pool_end;
    int ok = 0;
    if (!base || !msgid || !content || !content[0]) return 0;
    if (retained_si_origin(msgid) == 1 && find_retained_si(msgid)) return 1;

    clear_synthetic_retained_si(msgid);
    arena = (BYTE *)VirtualAlloc(NULL, arena_size, MEM_COMMIT | MEM_RESERVE, PAGE_EXECUTE_READWRITE);
    if (!arena) return 0;
    ZeroMemory(arena, arena_size);
    src_ctl = (int64_t)(arena + 0x0800);
    src_msg = (int64_t)(arena + 0x1000);
    pool = arena + 0x4000;
    pool_end = arena + arena_size;

    __try {
        forward_build_fake_request_msg(base, src_ctl, src_msg,
                                       source_from_wxid, source_to_wxid, source_sender_wxid,
                                       content, source_msgsource,
                                       source_type, source_subtype, source_local_id,
                                       msgid, source_time, &pool, pool_end);
        ok = retain_send_item_from_model(msgid, src_msg, 2, arena, "persistent_xml");
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
        ok = 0;
        forward_dbg("[forward-hydrate] EXCEPTION while building persistent XML model");
    }

    if (ok) {
        char dbg[256];
        wsprintfA(dbg, "[forward-hydrate] msgid=%I64d type=%d/%d persistent XML -> retained send_item ok",
                  (long long)msgid, source_type, source_subtype);
        forward_dbg(dbg);
        return 1;
    }
    VirtualFree(arena, 0, MEM_RELEASE);
    {
        char dbg[256];
        wsprintfA(dbg, "[forward-hydrate] msgid=%I64d type=%d/%d persistent XML hydrate failed",
                  (long long)msgid, source_type, source_subtype);
        forward_dbg(dbg);
    }
    return 0;
}

void *find_retained_model(int64_t msgid) {
    ForwardRetainedState *s = &g_forward_retained_state;
    if (!msgid) return NULL;
    for (int i = 0; i < MODEL_CACHE_SIZE; i++) {
        if (InterlockedCompareExchange(&s->model_cache[i].used, 0, 0) == 1 &&
            s->model_cache[i].msgid == msgid) {
            return s->model_cache[i].model;
        }
    }
    return find_persisted_retained_ptr('M', msgid,
                                       (int64_t)wxb_weixin_module() + WX_OFF_SRC_MSG_VT,
                                       MODEL_ITEM_BYTES, 0x108, "model");
}

int retained_model_in_memory(int64_t msgid) {
    ForwardRetainedState *s = &g_forward_retained_state;
    if (!msgid) return 0;
    for (int i = 0; i < MODEL_CACHE_SIZE; i++) {
        if (InterlockedCompareExchange(&s->model_cache[i].used, 0, 0) == 1 &&
            s->model_cache[i].msgid == msgid) {
            return 1;
        }
    }
    return 0;
}

void *find_retained_model_by_local_id(int32_t local_id) {
    ForwardRetainedState *s = &g_forward_retained_state;
    if (!local_id) return NULL;
    for (int i = 0; i < MODEL_CACHE_SIZE; i++) {
        if (InterlockedCompareExchange(&s->model_cache[i].used, 0, 0) == 1 &&
            s->model_cache[i].local_id == local_id) {
            return s->model_cache[i].model;
        }
    }
    return NULL;
}

void retain_model(int64_t msgid, int64_t msg_base) {
    ForwardRetainedState *s = &g_forward_retained_state;
    int64_t base = (int64_t)wxb_weixin_module();
    if (!base || !msg_base || !msgid) return;
    if (r64((void *)msg_base) != base + WX_OFF_SRC_MSG_VT) return;
    if (find_retained_model(msgid)) return;
    int pos = InterlockedIncrement(&s->model_cache_pos) % MODEL_CACHE_SIZE;
    RetainedModel *slot = &s->model_cache[pos];
    __try {
        InterlockedExchange(&slot->used, 0);
        slot->msgid = 0;
        slot->local_id = 0;
        slot->msg_type = 0;
        slot->msg_subtype = 0;
        ZeroMemory(slot->model, MODEL_ITEM_BYTES);
        ((FnCall3)(base + WX_OFF_MODEL_COPY))((int64_t)slot->model, (int64_t)msg_base);
        if (r64(slot->model) != base + WX_OFF_SRC_MSG_VT) {
            forward_dbg("[forward-retain-model] copy did not write model vtable");
            return;
        }
        slot->msgid = msgid;
        slot->local_id = r32((void *)(msg_base + WX_MSG_LOCAL_ID_OFF));
        slot->msg_type = r32((void *)(msg_base + 0x0C));
        slot->msg_subtype = r32((void *)(msg_base + WX_MSG_SUBTYPE_OFF));
        InterlockedExchange(&slot->used, 1);
        persist_retained_ptr('M', msgid, (int64_t)slot->model);
        {
            char dbg[256];
            wsprintfA(dbg,
                      "[forward-retain-model] msgid=%I64d slot=%d type=%d/%d svrid=%I64x ok",
                      (long long)msgid, pos,
                      r32((void *)((int64_t)slot->model + 0x0C)),
                      r32((void *)((int64_t)slot->model + 0x10)),
                      (unsigned __int64)r64((void *)((int64_t)slot->model + 0x108)));
            forward_dbg(dbg);
        }
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
        InterlockedExchange(&slot->used, 0);
        forward_dbg("[forward-retain-model] EXCEPTION in copy-ctor sub_1801CA4C0");
    }
}
