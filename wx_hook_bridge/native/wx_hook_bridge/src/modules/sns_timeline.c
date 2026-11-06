/*
 * Module: SNS timeline fetch.
 *
 * WeChat 4.1.9.23:
 *   sub_183B24690(TimelineDataSource*) triggers the upper timeline fetch/merge
 *   path. The live TimelineDataSource is found by its vtable; a load-closure
 *   fallback is kept because sub_183B32CC0 stores the data-source at closure+8.
 */

#include "../include/wx_bridge_sns_timeline.h"
#include "../include/wx_bridge_file_io.h"
#include "../include/wx_bridge_memory.h"
#include "../include/wx_bridge_native_call.h"
#include "../include/wx_bridge_offsets.h"
#include "../include/wx_bridge_paths.h"
#include "../include/wx_bridge_protocol.h"
#include "../include/wx_bridge_runtime.h"
#include "../include/wx_bridge_send_runtime.h"
#include "../include/wx_bridge_text.h"

#define SNS_TIMELINE_STATE_OFF 0x108
#define SNS_TIMELINE_STATE_VEC_OFF 0x48
#define SNS_TIMELINE_ITEM_SIZE 0x10
#define SNS_TIMELINE_MAX_ITEMS 10000
#define SNS_TIMELINE_MAX_VEC_BYTES (SNS_TIMELINE_MAX_ITEMS * SNS_TIMELINE_ITEM_SIZE)
#define SNS_TIMELINE_RESULT_MAX_KEYS 64
#define SNS_TIMELINE_OBJECT_PATCH_LEN 19
#define SNS_DATAITEM_PATCH_LEN 13
#define SNS_RESPONSE_DTOR_PATCH_LEN 14

typedef int64_t (__fastcall *FnSnsTimelineFetch)(int64_t timeline_this);
typedef unsigned __int64 *(__fastcall *FnSnsTimelineObjectJson)(int64_t timeline_object, unsigned __int64 *out_string);
typedef unsigned __int64 *(__fastcall *FnSnsDataItemJson)(int64_t data_item, unsigned __int64 *out_string);
typedef int64_t (__fastcall *FnSnsResponseDtor)(int64_t response_body);

typedef struct SnsTimelineSnapshot {
    int64_t state;
    int64_t vec_begin;
    int64_t vec_end;
    int64_t vec_cap;
    int count;
} SnsTimelineSnapshot;

typedef struct SnsTimelineTask {
    volatile LONG status;
    int64_t ret;
    int64_t rich_ret;
    int64_t timeline;
    int64_t closure;
    int source_kind;
    int phase;
    int rich_mode;
    DWORD seh_code;
    uint64_t seh_addr;
    SnsTimelineSnapshot before;
    SnsTimelineSnapshot after;
} SnsTimelineTask;

typedef struct SnsTimelineObjectHook {
    void *trampoline;
    BYTE orig[SNS_TIMELINE_OBJECT_PATCH_LEN];
    volatile LONG installed;
    volatile LONG capture_count;
    volatile LONG reentry;
} SnsTimelineObjectHook;

static SnsTimelineObjectHook g_sns_timeline_object_hook = {0};
static SnsTimelineObjectHook g_sns_dataitem_hook = {0};
static SnsTimelineObjectHook g_sns_response_dtor_hook = {0};

static const BYTE WX_ORIG_SNS_TIMELINE_OBJECT_JSON_BYTES[SNS_TIMELINE_OBJECT_PATCH_LEN] = {
    0x55,0x41,0x57,0x41,0x56,0x41,0x55,0x41,0x54,0x56,0x57,0x53,0x48,0x81,0xEC,0x28,
    0x02,0x00,0x00
};
static const BYTE WX_ORIG_SNS_DATAITEM_JSON_BYTES[SNS_DATAITEM_PATCH_LEN] = {
    0x55,0x41,0x56,0x56,0x57,0x53,0x48,0x81,0xEC,0x20,0x02,0x00,0x00
};
static const BYTE WX_ORIG_SNS_RESPONSE_DTOR_BYTES[SNS_RESPONSE_DTOR_PATCH_LEN] = {
    0x41,0x57,0x41,0x56,0x56,0x57,0x53,0x48,0x83,0xEC,0x20,0x48,0x89,0xCE
};

static int sns_is_scan_readable(DWORD protect) {
    DWORD p = protect & 0xFF;
    if (!p || (protect & PAGE_GUARD) || p == PAGE_NOACCESS) return 0;
    return p == PAGE_READONLY ||
           p == PAGE_READWRITE ||
           p == PAGE_WRITECOPY ||
           p == PAGE_EXECUTE_READ ||
           p == PAGE_EXECUTE_READWRITE ||
           p == PAGE_EXECUTE_WRITECOPY;
}

static int sns_is_aligned_ptr(int64_t addr) {
    return addr >= 0x10000 && ((addr & 0xF) == 0);
}

static int sns_vector_count(int64_t begin, int64_t end, int64_t cap) {
    int64_t bytes;
    if (!begin && !end && !cap) return 0;
    if (!begin || !end || !cap) return -1;
    if ((begin & 0xF) || (end & 0xF) || (cap & 0xF)) return -1;
    if (begin > end || end > cap) return -1;
    bytes = end - begin;
    if ((bytes % SNS_TIMELINE_ITEM_SIZE) != 0) return -1;
    if ((cap - begin) > SNS_TIMELINE_MAX_VEC_BYTES) return -1;
    if (bytes > 0 && !is_readable_ptr(begin, (SIZE_T)bytes)) return -1;
    return (int)(bytes / SNS_TIMELINE_ITEM_SIZE);
}

static int sns_read_snapshot(int64_t timeline, SnsTimelineSnapshot *snap) {
    int count;
    if (!snap) return 0;
    if (!sns_is_aligned_ptr(timeline)) return 0;
    ZeroMemory(snap, sizeof(*snap));
    if (!is_readable_ptr(timeline, SNS_TIMELINE_STATE_OFF + 8)) return 0;
    snap->state = r64((void *)(timeline + SNS_TIMELINE_STATE_OFF));
    if (!sns_is_aligned_ptr(snap->state)) return 0;
    if (!is_readable_ptr(snap->state, SNS_TIMELINE_STATE_VEC_OFF + 24)) return 0;
    snap->vec_begin = r64((void *)(snap->state + SNS_TIMELINE_STATE_VEC_OFF));
    snap->vec_end = r64((void *)(snap->state + SNS_TIMELINE_STATE_VEC_OFF + 8));
    snap->vec_cap = r64((void *)(snap->state + SNS_TIMELINE_STATE_VEC_OFF + 16));
    count = sns_vector_count(snap->vec_begin, snap->vec_end, snap->vec_cap);
    if (count < 0) return 0;
    snap->count = count;
    return 1;
}

static int sns_validate_timeline(int64_t base, int64_t timeline, SnsTimelineSnapshot *snap) {
    if (!base || !timeline) return 0;
    if (!sns_is_aligned_ptr(timeline)) return 0;
    if (!is_readable_ptr(timeline, SNS_TIMELINE_STATE_OFF + 8)) return 0;
    if (r64((void *)timeline) != base + WX_OFF_SNS_TIMELINE_DATASOURCE_VT) return 0;
    {
        int64_t self_field = r64((void *)(timeline + 8));
        if ((self_field & ~0xFLL) != (timeline & ~0xFLL)) return 0;
    }
    return sns_read_snapshot(timeline, snap);
}

static int sns_accept_candidate(int64_t base,
                                int64_t candidate,
                                int direct,
                                int64_t *out_timeline,
                                int64_t *out_closure,
                                int *out_source,
                                SnsTimelineSnapshot *out_snap) {
    int64_t timeline = candidate;
    SnsTimelineSnapshot snap;
    if (!direct) {
        if (!is_readable_ptr(candidate, 16)) return 0;
        timeline = r64((void *)(candidate + 8));
    }
    if (!sns_validate_timeline(base, timeline, &snap)) return 0;
    if (out_timeline) *out_timeline = timeline;
    if (out_closure) *out_closure = direct ? 0 : candidate;
    if (out_source) *out_source = direct ? 1 : 2;
    if (out_snap) *out_snap = snap;
    return 1;
}

static int sns_scan_for_qword(int64_t base,
                              int64_t needle,
                              int direct,
                              int64_t *out_timeline,
                              int64_t *out_closure,
                              int *out_source,
                              SnsTimelineSnapshot *out_snap) {
    SYSTEM_INFO si;
    MEMORY_BASIC_INFORMATION mbi;
    uintptr_t addr;
    uintptr_t max_addr;

    GetSystemInfo(&si);
    addr = (uintptr_t)si.lpMinimumApplicationAddress;
    max_addr = (uintptr_t)si.lpMaximumApplicationAddress;
    if (addr < 0x10000) addr = 0x10000;

    while (addr < max_addr) {
        SIZE_T got = VirtualQuery((void *)addr, &mbi, sizeof(mbi));
        uintptr_t region_base;
        uintptr_t region_end;
        if (got != sizeof(mbi)) {
            addr += 0x10000;
            continue;
        }
        region_base = (uintptr_t)mbi.BaseAddress;
        region_end = region_base + (uintptr_t)mbi.RegionSize;
        if (region_end <= region_base) break;

        if (mbi.State == MEM_COMMIT &&
            sns_is_scan_readable(mbi.Protect) &&
            mbi.RegionSize <= 64 * 1024 * 1024) {
            uintptr_t p = region_base;
            uintptr_t last = region_end >= sizeof(int64_t) ? region_end - sizeof(int64_t) : region_base;
            for (; p <= last; p += sizeof(int64_t)) {
                int matched = 0;
                __try {
                    matched = (*(volatile int64_t *)p == needle);
                }
                __except (EXCEPTION_EXECUTE_HANDLER) {
                    matched = 0;
                }
                if (matched &&
                    sns_accept_candidate(base, (int64_t)p, direct,
                                         out_timeline, out_closure,
                                         out_source, out_snap)) {
                    return 1;
                }
            }
        }
        addr = region_end;
    }
    return 0;
}

static int sns_find_live_timeline(int64_t base, SnsTimelineTask *task) {
    int64_t timeline = 0;
    int64_t closure = 0;
    int source = 0;
    SnsTimelineSnapshot snap;

    ZeroMemory(&snap, sizeof(snap));
    if (sns_scan_for_qword(base,
                           base + WX_OFF_SNS_TIMELINE_DATASOURCE_VT,
                           1,
                           &timeline,
                           &closure,
                           &source,
                           &snap) ||
        sns_scan_for_qword(base,
                           base + WX_OFF_SNS_TIMELINE_LOAD_CLOSURE_VT,
                           0,
                           &timeline,
                           &closure,
                           &source,
                           &snap)) {
        task->timeline = timeline;
        task->closure = closure;
        task->source_kind = source;
        task->before = snap;
        return 1;
    }
    return 0;
}

static int build_sns_timeline_queue_asm(BYTE *p, int64_t task_addr, int64_t worker_addr) {
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

static void sns_timeline_worker(int64_t task_addr) {
    SnsTimelineTask *task = (SnsTimelineTask *)task_addr;
    int64_t base = (int64_t)wxb_weixin_module();
    if (!task || !base) return;
    if (InterlockedCompareExchange(&task->status, 2, 0) != 0) return;

    __try {
        task->phase = 1;
        if (!task->timeline && !sns_find_live_timeline(base, task)) {
            InterlockedExchange(&task->status, -3);
            __leave;
        }
        if (!sns_validate_timeline(base, task->timeline, &task->before)) {
            InterlockedExchange(&task->status, -4);
            __leave;
        }
        task->phase = 2;
        if (task->rich_mode) {
            task->ret = ((FnSnsTimelineFetch)(base + WX_OFF_SNS_TIMELINE_FETCH_RICH))(task->timeline);
            task->rich_ret = task->ret;
        } else {
            task->ret = ((FnSnsTimelineFetch)(base + WX_OFF_SNS_TIMELINE_FETCH))(task->timeline);
        }
        task->phase = 3;
        sns_read_snapshot(task->timeline, &task->after);
        InterlockedExchange(&task->status, 1);
    }
    __except (task->seh_code = GetExceptionCode(),
              task->seh_addr = (uint64_t)GetExceptionInformation()->ExceptionRecord->ExceptionAddress,
              EXCEPTION_EXECUTE_HANDLER) {
        task->ret = -1;
        InterlockedExchange(&task->status, -1);
    }
}

static void append_hex_qword(char **p, char *end, const char *key, int64_t value) {
    char num[64];
    append_raw(p, end, ",\"");
    append_raw(p, end, key);
    append_raw(p, end, "\":\"0x");
    wsprintfA(num, "%I64x", (unsigned __int64)value);
    append_raw(p, end, num);
    append_raw(p, end, "\"");
}

static void append_int_field(char **p, char *end, const char *key, int value) {
    char num[64];
    append_raw(p, end, ",\"");
    append_raw(p, end, key);
    append_raw(p, end, "\":");
    wsprintfA(num, "%d", value);
    append_raw(p, end, num);
}

static void append_i64_field(char **p, char *end, const char *key, int64_t value) {
    char num[64];
    append_raw(p, end, ",\"");
    append_raw(p, end, key);
    append_raw(p, end, "\":");
    wsprintfA(num, "%I64d", (long long)value);
    append_raw(p, end, num);
}

static void append_u64_hex_value(char **p, char *end, uint64_t value) {
    char num[64];
    append_raw(p, end, "\"0x");
    wsprintfA(num, "%I64x", (unsigned __int64)value);
    append_raw(p, end, num);
    append_raw(p, end, "\"");
}

static void append_snapshot(char **p, char *end, const char *prefix, const SnsTimelineSnapshot *snap) {
    char key[64];
    wsprintfA(key, "%sState", prefix);
    append_hex_qword(p, end, key, snap ? snap->state : 0);
    wsprintfA(key, "%sVecBegin", prefix);
    append_hex_qword(p, end, key, snap ? snap->vec_begin : 0);
    wsprintfA(key, "%sVecEnd", prefix);
    append_hex_qword(p, end, key, snap ? snap->vec_end : 0);
    wsprintfA(key, "%sVecCap", prefix);
    append_hex_qword(p, end, key, snap ? snap->vec_cap : 0);
    wsprintfA(key, "%sCount", prefix);
    append_int_field(p, end, key, snap ? snap->count : 0);
}

static void append_timeline_keys(char **p, char *end, const SnsTimelineSnapshot *snap) {
    int count;
    append_raw(p, end, ",\"timelineKeys\":[");
    if (!snap || snap->count <= 0 || !snap->vec_begin) {
        append_raw(p, end, "]");
        return;
    }
    count = snap->count;
    if (count > SNS_TIMELINE_RESULT_MAX_KEYS) count = SNS_TIMELINE_RESULT_MAX_KEYS;
    for (int i = 0; i < count; i++) {
        int64_t item = snap->vec_begin + (int64_t)i * SNS_TIMELINE_ITEM_SIZE;
        uint64_t q0 = 0;
        uint64_t q1 = 0;
        if (!is_readable_ptr(item, SNS_TIMELINE_ITEM_SIZE)) break;
        q0 = (uint64_t)r64((void *)item);
        q1 = (uint64_t)r64((void *)(item + 8));
        if (i) append_raw(p, end, ",");
        append_raw(p, end, "{\"index\":");
        {
            char num[32];
            wsprintfA(num, "%d", i);
            append_raw(p, end, num);
        }
        append_raw(p, end, ",\"q0\":");
        append_u64_hex_value(p, end, q0);
        append_raw(p, end, ",\"q1\":");
        append_u64_hex_value(p, end, q1);
        append_raw(p, end, ",\"localLow\":");
        {
            char num[32];
            wsprintfA(num, "%u", (unsigned int)(q0 & 0xFFFFFFFFu));
            append_raw(p, end, num);
        }
        append_raw(p, end, ",\"localHigh\":");
        {
            char num[32];
            wsprintfA(num, "%u", (unsigned int)((q0 >> 32) & 0xFFFFFFFFu));
            append_raw(p, end, num);
        }
        append_raw(p, end, "}");
    }
    append_raw(p, end, "]");
}

static void append_pointer_fields(char **p,
                                  char *end,
                                  const char *object_name,
                                  int64_t base,
                                  const int *offsets,
                                  const char **names,
                                  int count) {
    append_raw(p, end, ",\"");
    append_raw(p, end, object_name);
    append_raw(p, end, "\":{");
    for (int i = 0; i < count; i++) {
        int64_t value = 0;
        if (i) append_raw(p, end, ",");
        append_raw(p, end, "\"");
        append_raw(p, end, names[i]);
        append_raw(p, end, "\":");
        if (base && is_readable_ptr(base + offsets[i], 8)) {
            value = r64((void *)(base + offsets[i]));
        }
        append_u64_hex_value(p, end, (uint64_t)value);
    }
    append_raw(p, end, "}");
}

static int sns_copy_wx_string(int64_t slot, char *out, int max, int *truncated) {
    uint64_t len;
    uint64_t cap;
    int64_t data;
    int n;
    if (truncated) *truncated = 0;
    if (!out || max <= 0) return 0;
    out[0] = 0;
    if (!slot || !is_readable_ptr(slot, 32)) return 0;
    len = (uint64_t)r64((void *)(slot + 16));
    cap = (uint64_t)r64((void *)(slot + 24));
    if (len > 1024 * 1024 || cap > 1024 * 1024) return 0;
    data = (cap >= 0x10) ? r64((void *)slot) : slot;
    if (len && (!data || !is_readable_ptr(data, (SIZE_T)len))) return 0;
    n = (len >= (uint64_t)max) ? max - 1 : (int)len;
    if (truncated && len >= (uint64_t)max) *truncated = 1;
    if (n > 0) {
        __try {
            copy_bytes(out, (void *)data, n);
        }
        __except (EXCEPTION_EXECUTE_HANDLER) {
            out[0] = 0;
            return 0;
        }
    }
    out[n] = 0;
    return n;
}

static void append_wx_string_field(char **p, char *end, const char *key, int64_t slot, int max_chars) {
    char buf[4096];
    int truncated = 0;
    if (max_chars <= 0 || max_chars > (int)sizeof(buf)) max_chars = sizeof(buf);
    sns_copy_wx_string(slot, buf, max_chars, &truncated);
    append_raw(p, end, ",\"");
    append_raw(p, end, key);
    append_raw(p, end, "\":\"");
    append_escaped(p, end, buf);
    append_raw(p, end, "\"");
    if (truncated) {
        append_raw(p, end, ",\"");
        append_raw(p, end, key);
        append_raw(p, end, "Truncated\":true");
    }
}

static void append_wx_string_len_field(char **p, char *end, const char *key, int64_t slot) {
    char num[64];
    uint64_t len = 0;
    if (slot && is_readable_ptr(slot, 24)) len = (uint64_t)r64((void *)(slot + 16));
    append_raw(p, end, ",\"");
    append_raw(p, end, key);
    append_raw(p, end, "\":");
    wsprintfA(num, "%I64u", (unsigned __int64)len);
    append_raw(p, end, num);
}

static int sns_media_count_from_object(int64_t obj);

static void append_member_prefix(char **p, char *end, int *first, const char *key) {
    if (!first || !*first) append_raw(p, end, ",");
    if (first) *first = 0;
    append_raw(p, end, "\"");
    append_raw(p, end, key);
    append_raw(p, end, "\":");
}

static void append_string_member(char **p, char *end, int *first, const char *key, const char *value) {
    append_member_prefix(p, end, first, key);
    append_raw(p, end, "\"");
    append_escaped(p, end, value ? value : "");
    append_raw(p, end, "\"");
}

static void append_wx_string_member(char **p, char *end, int *first, const char *key, int64_t slot, int max_chars) {
    char buf[4096];
    int truncated = 0;
    if (max_chars <= 0 || max_chars > (int)sizeof(buf)) max_chars = sizeof(buf);
    sns_copy_wx_string(slot, buf, max_chars, &truncated);
    append_string_member(p, end, first, key, buf);
    if (truncated) {
        char trunc_key[128];
        wsprintfA(trunc_key, "%sTruncated", key);
        append_member_prefix(p, end, first, trunc_key);
        append_raw(p, end, "true");
    }
}

static void append_i64_member(char **p, char *end, int *first, const char *key, int64_t value) {
    char num[64];
    append_member_prefix(p, end, first, key);
    wsprintfA(num, "%I64d", (long long)value);
    append_raw(p, end, num);
}

static void append_u64_hex_member(char **p, char *end, int *first, const char *key, uint64_t value) {
    append_member_prefix(p, end, first, key);
    append_u64_hex_value(p, end, value);
}

static void append_sns_url_object_member(char **p, char *end, int *first, const char *key, int64_t obj) {
    int sub_first = 1;
    if (!obj || !is_readable_ptr(obj, 200)) return;
    append_member_prefix(p, end, first, key);
    append_raw(p, end, "{");
    append_wx_string_member(p, end, &sub_first, "value", obj + 0, 4096);
    append_i64_member(p, end, &sub_first, "type", r64((void *)(obj + 32)));
    append_wx_string_member(p, end, &sub_first, "md5", obj + 40, 512);
    append_u64_hex_member(p, end, &sub_first, "key", (uint64_t)r64((void *)(obj + 72)));
    append_i64_member(p, end, &sub_first, "encIdx", (int64_t)r32((void *)(obj + 80)));
    append_wx_string_member(p, end, &sub_first, "token", obj + 88, 1024);
    append_wx_string_member(p, end, &sub_first, "videoMd5", obj + 120, 512);
    append_wx_string_member(p, end, &sub_first, "aesKey", obj + 168, 1024);
    append_raw(p, end, "}");
}

static void append_sns_url_vector_member(char **p, char *end, int *first, const char *key, int64_t begin, int64_t vec_end) {
    int count;
    int limit;
    if (!begin && !vec_end) return;
    if (!begin || !vec_end || begin > vec_end) return;
    if (((vec_end - begin) % 16) != 0 || (vec_end - begin) > 16 * 128) return;
    if (!is_readable_ptr(begin, (SIZE_T)(vec_end - begin))) return;
    count = (int)((vec_end - begin) / 16);
    limit = count;
    if (limit > 8) limit = 8;
    append_member_prefix(p, end, first, key);
    append_raw(p, end, "[");
    for (int i = 0; i < limit; i++) {
        int64_t item = begin + (int64_t)i * 16;
        int64_t url_obj = r64((void *)item);
        int sub_first = 1;
        if (i) append_raw(p, end, ",");
        append_raw(p, end, "{");
        append_i64_member(p, end, &sub_first, "index", i);
        append_u64_hex_member(p, end, &sub_first, "object", (uint64_t)url_obj);
        append_sns_url_object_member(p, end, &sub_first, "url", url_obj);
        append_raw(p, end, "}");
    }
    append_raw(p, end, "]");
}

static void append_sns_media_object_member(char **p, char *end, int *first, const char *key, int64_t media) {
    int sub_first = 1;
    if (!media || !is_readable_ptr(media, 424)) return;
    append_member_prefix(p, end, first, key);
    append_raw(p, end, "{");
    append_u64_hex_member(p, end, &sub_first, "object", (uint64_t)media);
    append_u64_hex_member(p, end, &sub_first, "id64Hex", (uint64_t)r64((void *)media));
    append_i64_member(p, end, &sub_first, "id64", (int64_t)r64((void *)media));
    append_wx_string_member(p, end, &sub_first, "id", media + 8, 512);
    append_i64_member(p, end, &sub_first, "type", (int64_t)r32((void *)(media + 40)));
    append_i64_member(p, end, &sub_first, "subType", (int64_t)r32((void *)(media + 44)));
    append_wx_string_member(p, end, &sub_first, "title", media + 48, 1024);
    append_wx_string_member(p, end, &sub_first, "description", media + 80, 2048);
    append_wx_string_member(p, end, &sub_first, "userData", media + 112, 2048);
    append_sns_url_vector_member(p, end, &sub_first, "thumbs",
                                 r64((void *)(media + 144)),
                                 r64((void *)(media + 152)));
    append_sns_url_object_member(p, end, &sub_first, "url", r64((void *)(media + 168)));
    append_sns_url_object_member(p, end, &sub_first, "lowBandUrl", r64((void *)(media + 184)));
    append_sns_url_object_member(p, end, &sub_first, "attachUrl", r64((void *)(media + 200)));
    append_sns_url_object_member(p, end, &sub_first, "attachThumbUrl", r64((void *)(media + 216)));
    append_wx_string_member(p, end, &sub_first, "attachShareTitle", media + 264, 1024);
    append_sns_url_object_member(p, end, &sub_first, "songAlbumUrl", r64((void *)(media + 296)));
    append_wx_string_member(p, end, &sub_first, "songLyric", media + 312, 2048);
    append_i64_member(p, end, &sub_first, "width", (int64_t)r32((void *)(media + 344)));
    append_i64_member(p, end, &sub_first, "height", (int64_t)r32((void *)(media + 348)));
    append_i64_member(p, end, &sub_first, "attachTotalTime", (int64_t)r32((void *)(media + 352)));
    append_i64_member(p, end, &sub_first, "enc", (int64_t)(*(unsigned char *)(media + 356)));
    append_u64_hex_member(p, end, &sub_first, "encKey", (uint64_t)r64((void *)(media + 360)));
    append_i64_member(p, end, &sub_first, "predownloadPercent", (int64_t)r32((void *)(media + 372)));
    append_i64_member(p, end, &sub_first, "predownNetType", (int64_t)r32((void *)(media + 376)));
    append_wx_string_member(p, end, &sub_first, "noPreDownloadRange", media + 384, 1024);
    append_i64_member(p, end, &sub_first, "totalSize", (int64_t)r32((void *)(media + 416)));
    append_raw(p, end, "}");
}

static void append_sns_media_list_member(char **p, char *end, int *first, int64_t content_object) {
    int64_t begin;
    int64_t vec_end;
    int count;
    int limit;
    if (!content_object || !is_readable_ptr(content_object, 296)) return;
    begin = r64((void *)(content_object + 280));
    vec_end = r64((void *)(content_object + 288));
    if (!begin && !vec_end) return;
    if (!begin || !vec_end || begin > vec_end) return;
    if (((vec_end - begin) % 16) != 0 || (vec_end - begin) > 16 * 128) return;
    if (!is_readable_ptr(begin, (SIZE_T)(vec_end - begin))) return;
    count = (int)((vec_end - begin) / 16);
    limit = count;
    if (limit > 8) limit = 8;
    append_member_prefix(p, end, first, "mediaList");
    append_raw(p, end, "[");
    for (int i = 0; i < limit; i++) {
        int64_t item = begin + (int64_t)i * 16;
        int64_t media = r64((void *)item);
        int item_first = 1;
        if (i) append_raw(p, end, ",");
        append_raw(p, end, "{");
        append_i64_member(p, end, &item_first, "index", i);
        append_u64_hex_member(p, end, &item_first, "object", (uint64_t)media);
        append_sns_media_object_member(p, end, &item_first, "media", media);
        append_raw(p, end, "}");
    }
    append_raw(p, end, "]");
}

static void append_sns_content_object_guess(char **p, char *end, int64_t obj) {
    int first = 1;
    int media_count;
    if (!obj || !is_readable_ptr(obj, 424)) return;
    media_count = sns_media_count_from_object(obj);
    append_raw(p, end, ",\"contentObjectGuess\":{");
    append_u64_hex_member(p, end, &first, "object", (uint64_t)obj);
    append_wx_string_member(p, end, &first, "title", obj + 0, 2048);
    append_wx_string_member(p, end, &first, "description", obj + 32, 2048);
    append_wx_string_member(p, end, &first, "contentUrl", obj + 64, 4096);
    append_i64_member(p, end, &first, "type", (int64_t)r32((void *)(obj + 96)));
    append_i64_member(p, end, &first, "contentSubStyle", (int64_t)r32((void *)(obj + 100)));
    append_i64_member(p, end, &first, "mediaCount", (int64_t)media_count);
    append_sns_media_list_member(p, end, &first, obj);
    append_raw(p, end, "}");
}

static int sns_media_count_from_object(int64_t obj) {
    int64_t begin;
    int64_t end;
    if (!obj || !is_readable_ptr(obj, 296)) return -1;
    begin = r64((void *)(obj + 280));
    end = r64((void *)(obj + 288));
    if (!begin && !end) return 0;
    if (!begin || !end || begin > end) return -1;
    if (((end - begin) % 16) != 0) return -1;
    if ((end - begin) > 16 * 1024) return -1;
    return (int)((end - begin) / 16);
}

static void append_sns_serialized_json_field(char **p, char *end, unsigned __int64 *out_string) {
    char buf[32768];
    int truncated = 0;
    sns_copy_wx_string((int64_t)out_string, buf, sizeof(buf), &truncated);
    append_raw(p, end, ",\"serializedJson\":\"");
    append_escaped(p, end, buf);
    append_raw(p, end, "\"");
    append_wx_string_len_field(p, end, "serializedJsonLen", (int64_t)out_string);
    if (truncated) append_raw(p, end, ",\"serializedJsonTruncated\":true");
}

static void sns_log_timeline_object_capture(int64_t obj, unsigned __int64 *out_string, unsigned __int64 *ret) {
    char json[131072];
    char num[64];
    char *p = json;
    char *end = json + sizeof(json) - 1;
    int media_count = sns_media_count_from_object(obj);
    int64_t content_object = 0;
    if (obj && is_readable_ptr(obj, 640)) content_object = r64((void *)(obj + 632));

    append_raw(&p, end, "{\"event\":\"sns_timeline_object_capture\"");
    append_raw(&p, end, ",\"tick\":");
    wsprintfA(num, "%I64u", (unsigned __int64)GetTickCount64());
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"threadId\":");
    wsprintfA(num, "%lu", (unsigned long)GetCurrentThreadId());
    append_raw(&p, end, num);
    append_hex_qword(&p, end, "object", obj);
    append_hex_qword(&p, end, "outString", (int64_t)out_string);
    append_hex_qword(&p, end, "ret", (int64_t)ret);
    append_int_field(&p, end, "createTime", obj ? r32((void *)(obj + 56)) : 0);
    append_int_field(&p, end, "contentStyle", obj ? r32((void *)(obj + 48)) : 0);
    append_int_field(&p, end, "contentDescShowType", obj ? r32((void *)(obj + 128)) : 0);
    append_int_field(&p, end, "contentDescScene", obj ? r32((void *)(obj + 132)) : 0);
    append_int_field(&p, end, "private", obj ? (r32((void *)(obj + 552)) == 2) : 0);
    append_int_field(&p, end, "publicBrandContactType", obj ? r32((void *)(obj + 272)) : 0);
    append_int_field(&p, end, "mediaCount", media_count);
    append_hex_qword(&p, end, "contentObject", content_object);
    append_wx_string_field(&p, end, "contentDesc", obj + 96, 4096);
    append_wx_string_field(&p, end, "sourceUserName", obj + 144, 1024);
    append_wx_string_field(&p, end, "sourceNickName", obj + 176, 1024);
    append_wx_string_field(&p, end, "publicPhotoAccountUserName", obj + 208, 1024);
    append_wx_string_field(&p, end, "publicPhotoAccountNickName", obj + 240, 1024);
    append_wx_string_field(&p, end, "title", obj + 304, 2048);
    append_wx_string_field(&p, end, "contentUrl", obj + 336, 4096);
    append_sns_serialized_json_field(&p, end, out_string);
    append_raw(&p, end, ",\"layoutSource\":\"sub_183E2A5C0\"");
    append_raw(&p, end, "}\r\n");
    *p = 0;
    append_file_bytes(SNS_TIMELINE_OBJECT_CAPTURE_LOG, json, (DWORD)(p - json));
    InterlockedIncrement(&g_sns_timeline_object_hook.capture_count);
}

static void sns_log_dataitem_capture(int64_t obj, unsigned __int64 *out_string, unsigned __int64 *ret) {
    char json[131072];
    char num[64];
    char *p = json;
    char *end = json + sizeof(json) - 1;
    int64_t content_object = 0;
    if (obj && is_readable_ptr(obj, 776)) content_object = r64((void *)(obj + 768));

    append_raw(&p, end, "{\"event\":\"sns_dataitem_capture\"");
    append_raw(&p, end, ",\"tick\":");
    wsprintfA(num, "%I64u", (unsigned __int64)GetTickCount64());
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"threadId\":");
    wsprintfA(num, "%lu", (unsigned long)GetCurrentThreadId());
    append_raw(&p, end, num);
    append_hex_qword(&p, end, "object", obj);
    append_hex_qword(&p, end, "outString", (int64_t)out_string);
    append_hex_qword(&p, end, "ret", (int64_t)ret);
    append_hex_qword(&p, end, "snsId", obj ? r64((void *)obj) : 0);
    append_int_field(&p, end, "createTime", obj ? r32((void *)(obj + 436)) : 0);
    append_int_field(&p, end, "contentDescShowType", obj ? r32((void *)(obj + 472)) : 0);
    append_int_field(&p, end, "contentDescScene", obj ? r32((void *)(obj + 476)) : 0);
    append_int_field(&p, end, "sightFolded", obj ? r32((void *)(obj + 880)) : 0);
    append_int_field(&p, end, "showFlag", obj ? r32((void *)(obj + 648)) : 0);
    append_int_field(&p, end, "private", obj ? r32((void *)(obj + 432)) : 0);
    append_int_field(&p, end, "publicBrandContactType", obj ? r32((void *)(obj + 512)) : 0);
    append_int_field(&p, end, "isTop", obj ? r32((void *)(obj + 392)) : 0);
    append_int_field(&p, end, "guideTop", obj ? r32((void *)(obj + 396)) : 0);
    append_hex_qword(&p, end, "contentObject", content_object);
    append_wx_string_field(&p, end, "username", obj + 400, 1024);
    append_wx_string_field(&p, end, "contentDesc", obj + 440, 4096);
    append_wx_string_field(&p, end, "publicUserName", obj + 480, 1024);
    append_wx_string_field(&p, end, "sourceUserName", obj + 520, 1024);
    append_wx_string_field(&p, end, "sourceNickName", obj + 552, 1024);
    append_wx_string_field(&p, end, "publicPhotoAccountUserName", obj + 584, 1024);
    append_wx_string_field(&p, end, "publicPhotoAccountNickName", obj + 616, 1024);
    append_wx_string_field(&p, end, "statisticsData", obj + 656, 4096);
    append_wx_string_field(&p, end, "statExtStr", obj + 688, 4096);
    append_wx_string_field(&p, end, "canvasInfoXml", obj + 720, 4096);
    append_sns_serialized_json_field(&p, end, out_string);
    append_raw(&p, end, ",\"layoutSource\":\"sub_183DA0800/sub_183DA0BE0\"");
    append_raw(&p, end, "}\r\n");
    *p = 0;
    append_file_bytes(SNS_TIMELINE_OBJECT_CAPTURE_LOG, json, (DWORD)(p - json));
    InterlockedIncrement(&g_sns_dataitem_hook.capture_count);
}

static int sns_vector_count_bytes(int64_t begin, int64_t end, int64_t cap, int item_size, int64_t max_bytes) {
    int64_t bytes;
    if (!begin && !end && !cap) return 0;
    if (!begin || !end || !cap) return -1;
    if (begin > end || end > cap) return -1;
    bytes = end - begin;
    if (item_size <= 0 || (bytes % item_size) != 0) return -1;
    if ((cap - begin) > max_bytes) return -1;
    if (bytes > 0 && !is_readable_ptr(begin, (SIZE_T)bytes)) return -1;
    return (int)(bytes / item_size);
}

static void append_sns_dataitem_guess(char **p, char *end, int64_t obj) {
    int64_t content_object;
    if (!obj || !is_readable_ptr(obj, 888)) return;
    content_object = r64((void *)(obj + 768));
    append_raw(p, end, ",\"dataItemGuess\":{");
    append_raw(p, end, "\"snsId\":");
    append_u64_hex_value(p, end, (uint64_t)r64((void *)obj));
    append_int_field(p, end, "createTime", r32((void *)(obj + 436)));
    append_int_field(p, end, "private", r32((void *)(obj + 432)));
    append_wx_string_field(p, end, "username", obj + 400, 512);
    append_wx_string_field(p, end, "contentDesc", obj + 440, 1024);
    append_wx_string_field(p, end, "sourceUserName", obj + 520, 512);
    append_wx_string_field(p, end, "sourceNickName", obj + 552, 512);
    append_hex_qword(p, end, "contentObject", content_object);
    append_sns_content_object_guess(p, end, content_object);
    append_raw(p, end, "}");
}

static void append_response_vector(char **p,
                                   char *end,
                                   const char *name,
                                   int64_t owner,
                                   int off,
                                   int item_size,
                                   int guess_dataitem) {
    int64_t begin = 0;
    int64_t vec_end = 0;
    int64_t cap = 0;
    int count;
    int limit;
    if (owner && is_readable_ptr(owner + off, 24)) {
        begin = r64((void *)(owner + off));
        vec_end = r64((void *)(owner + off + 8));
        cap = r64((void *)(owner + off + 16));
    }
    count = sns_vector_count_bytes(begin, vec_end, cap, item_size, 1024 * 1024);
    append_raw(p, end, ",\"");
    append_raw(p, end, name);
    append_raw(p, end, "\":{\"begin\":");
    append_u64_hex_value(p, end, (uint64_t)begin);
    append_raw(p, end, ",\"end\":");
    append_u64_hex_value(p, end, (uint64_t)vec_end);
    append_raw(p, end, ",\"cap\":");
    append_u64_hex_value(p, end, (uint64_t)cap);
    append_int_field(p, end, "count", count);
    append_raw(p, end, ",\"items\":[");
    limit = count;
    if (limit > 16) limit = 16;
    if (count > 0 && begin) {
        for (int i = 0; i < limit; i++) {
            int64_t item = begin + (int64_t)i * item_size;
            uint64_t q0 = 0;
            uint64_t q1 = 0;
            char num[32];
            if (!is_readable_ptr(item, (SIZE_T)item_size)) break;
            q0 = (uint64_t)r64((void *)item);
            if (item_size >= 16) q1 = (uint64_t)r64((void *)(item + 8));
            if (i) append_raw(p, end, ",");
            append_raw(p, end, "{\"index\":");
            wsprintfA(num, "%d", i);
            append_raw(p, end, num);
            append_raw(p, end, ",\"q0\":");
            append_u64_hex_value(p, end, q0);
            if (item_size >= 16) {
                append_raw(p, end, ",\"q1\":");
                append_u64_hex_value(p, end, q1);
            }
            if (guess_dataitem) append_sns_dataitem_guess(p, end, (int64_t)q0);
            append_raw(p, end, "}");
        }
    }
    append_raw(p, end, "]}");
}

static void sns_log_response_capture(int64_t body) {
    char json[131072];
    char num[64];
    char *p = json;
    char *end = json + sizeof(json) - 1;
    append_raw(&p, end, "{\"event\":\"sns_timeline_response_dtor_capture\"");
    append_raw(&p, end, ",\"tick\":");
    wsprintfA(num, "%I64u", (unsigned __int64)GetTickCount64());
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"threadId\":");
    wsprintfA(num, "%lu", (unsigned long)GetCurrentThreadId());
    append_raw(&p, end, num);
    append_hex_qword(&p, end, "body", body);
    append_response_vector(&p, end, "vec0_items", body, 0x0, 16, 1);
    append_response_vector(&p, end, "vec18_items", body, 0x18, 16, 1);
    append_response_vector(&p, end, "vec1D8_items", body, 0x1D8, 16, 1);
    append_response_vector(&p, end, "vec1F8_keys", body, 0x1F8, 16, 0);
    append_raw(&p, end, ",\"layoutSource\":\"sub_182BC3930 before response body destructor\"}\r\n");
    *p = 0;
    append_file_bytes(SNS_TIMELINE_RESPONSE_CAPTURE_LOG, json, (DWORD)(p - json));
    InterlockedIncrement(&g_sns_response_dtor_hook.capture_count);
}

static unsigned __int64 *__fastcall sns_timeline_object_json_hook(int64_t obj, unsigned __int64 *out_string) {
    FnSnsTimelineObjectJson original = (FnSnsTimelineObjectJson)g_sns_timeline_object_hook.trampoline;
    unsigned __int64 *ret;
    if (!original) return out_string;
    if (InterlockedCompareExchange(&g_sns_timeline_object_hook.reentry, 1, 0) != 0) {
        return original(obj, out_string);
    }
    ret = original(obj, out_string);
    __try {
        if (obj && is_readable_ptr(obj, 640)) {
            sns_log_timeline_object_capture(obj, out_string, ret);
        }
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
    }
    InterlockedExchange(&g_sns_timeline_object_hook.reentry, 0);
    return ret;
}

static int64_t __fastcall sns_response_dtor_hook(int64_t body) {
    FnSnsResponseDtor original = (FnSnsResponseDtor)g_sns_response_dtor_hook.trampoline;
    if (!original) return 0;
    if (InterlockedCompareExchange(&g_sns_response_dtor_hook.reentry, 1, 0) == 0) {
        __try {
            if (body && is_readable_ptr(body, 0x210)) sns_log_response_capture(body);
        }
        __except (EXCEPTION_EXECUTE_HANDLER) {
        }
        InterlockedExchange(&g_sns_response_dtor_hook.reentry, 0);
    }
    return original(body);
}

static unsigned __int64 *__fastcall sns_dataitem_json_hook(int64_t obj, unsigned __int64 *out_string) {
    FnSnsDataItemJson original = (FnSnsDataItemJson)g_sns_dataitem_hook.trampoline;
    unsigned __int64 *ret;
    if (!original) return out_string;
    if (InterlockedCompareExchange(&g_sns_dataitem_hook.reentry, 1, 0) != 0) {
        return original(obj, out_string);
    }
    ret = original(obj, out_string);
    __try {
        if (obj && is_readable_ptr(obj, 888)) {
            sns_log_dataitem_capture(obj, out_string, ret);
        }
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
    }
    InterlockedExchange(&g_sns_dataitem_hook.reentry, 0);
    return ret;
}

static int build_sns_entry_trampoline(BYTE *p, BYTE *target, const BYTE *orig, int patch_len) {
    BYTE *start = p;
    copy_bytes(p, orig, patch_len);
    p += patch_len;
    *p++ = 0x48; *p++ = 0xB8;
    *(int64_t *)p = (int64_t)(target + patch_len); p += 8;
    *p++ = 0xFF; *p++ = 0xE0;
    return (int)(p - start);
}

static int write_sns_abs_jump(BYTE *target, int patch_len, void *hook) {
    BYTE patch[32];
    DWORD old = 0;
    int i;
    if (!target || patch_len < 12 || patch_len > (int)sizeof(patch)) return 0;
    for (i = 0; i < patch_len; i++) patch[i] = 0x90;
    patch[0] = 0x48; patch[1] = 0xB8;
    *(int64_t *)(patch + 2) = (int64_t)hook;
    patch[10] = 0xFF; patch[11] = 0xE0;
    if (!VirtualProtect(target, (SIZE_T)patch_len, PAGE_EXECUTE_READWRITE, &old)) return 0;
    copy_bytes(target, patch, patch_len);
    VirtualProtect(target, (SIZE_T)patch_len, old, &old);
    FlushInstructionCache(GetCurrentProcess(), target, (SIZE_T)patch_len);
    return 1;
}

void install_sns_timeline_object_hook(void) {
    BYTE *target;
    int tlen;
    DWORD oldp = 0;
    if (!wxb_weixin_module()) return;

    if (InterlockedCompareExchange(&g_sns_timeline_object_hook.installed, 0, 0) == 0) {
        target = (BYTE *)((int64_t)wxb_weixin_module() + WX_OFF_SNS_TIMELINE_OBJECT_JSON);
        if (target[0] == 0x48 && target[1] == 0xB8) {
            VirtualProtect(target, SNS_TIMELINE_OBJECT_PATCH_LEN, PAGE_EXECUTE_READWRITE, &oldp);
            copy_bytes(target, WX_ORIG_SNS_TIMELINE_OBJECT_JSON_BYTES, SNS_TIMELINE_OBJECT_PATCH_LEN);
            VirtualProtect(target, SNS_TIMELINE_OBJECT_PATCH_LEN, oldp, &oldp);
            FlushInstructionCache(GetCurrentProcess(), target, SNS_TIMELINE_OBJECT_PATCH_LEN);
            write_status("sns timeline object stale hook restored\n");
        }
        if (!bytes_equal(target, WX_ORIG_SNS_TIMELINE_OBJECT_JSON_BYTES, SNS_TIMELINE_OBJECT_PATCH_LEN)) {
            write_status("sns timeline object hook prologue mismatch; hook not installed\n");
        } else {
            copy_bytes(g_sns_timeline_object_hook.orig, target, SNS_TIMELINE_OBJECT_PATCH_LEN);
            g_sns_timeline_object_hook.trampoline = VirtualAlloc(NULL, 256, MEM_COMMIT | MEM_RESERVE, PAGE_EXECUTE_READWRITE);
            if (!g_sns_timeline_object_hook.trampoline) {
                write_status("sns timeline object hook trampoline alloc failed\n");
            } else {
                tlen = build_sns_entry_trampoline((BYTE *)g_sns_timeline_object_hook.trampoline,
                                                  target,
                                                  g_sns_timeline_object_hook.orig,
                                                  SNS_TIMELINE_OBJECT_PATCH_LEN);
                FlushInstructionCache(GetCurrentProcess(), g_sns_timeline_object_hook.trampoline, tlen);
                if (!write_sns_abs_jump(target, SNS_TIMELINE_OBJECT_PATCH_LEN, (void *)sns_timeline_object_json_hook)) {
                    VirtualFree(g_sns_timeline_object_hook.trampoline, 0, MEM_RELEASE);
                    g_sns_timeline_object_hook.trampoline = NULL;
                    write_status("sns timeline object hook patch failed\n");
                } else {
                    InterlockedExchange(&g_sns_timeline_object_hook.installed, 1);
                    write_status("sns timeline object hook installed\n");
                }
            }
        }
    }

    if (InterlockedCompareExchange(&g_sns_dataitem_hook.installed, 0, 0) == 0) {
        oldp = 0;
        target = (BYTE *)((int64_t)wxb_weixin_module() + WX_OFF_SNS_DATAITEM_JSON);
        if (target[0] == 0x48 && target[1] == 0xB8) {
            VirtualProtect(target, SNS_DATAITEM_PATCH_LEN, PAGE_EXECUTE_READWRITE, &oldp);
            copy_bytes(target, WX_ORIG_SNS_DATAITEM_JSON_BYTES, SNS_DATAITEM_PATCH_LEN);
            VirtualProtect(target, SNS_DATAITEM_PATCH_LEN, oldp, &oldp);
            FlushInstructionCache(GetCurrentProcess(), target, SNS_DATAITEM_PATCH_LEN);
            write_status("sns dataitem stale hook restored\n");
        }
        if (!bytes_equal(target, WX_ORIG_SNS_DATAITEM_JSON_BYTES, SNS_DATAITEM_PATCH_LEN)) {
            write_status("sns dataitem hook prologue mismatch; hook not installed\n");
            return;
        }
        copy_bytes(g_sns_dataitem_hook.orig, target, SNS_DATAITEM_PATCH_LEN);
        g_sns_dataitem_hook.trampoline = VirtualAlloc(NULL, 256, MEM_COMMIT | MEM_RESERVE, PAGE_EXECUTE_READWRITE);
        if (!g_sns_dataitem_hook.trampoline) {
            write_status("sns dataitem hook trampoline alloc failed\n");
            return;
        }
        tlen = build_sns_entry_trampoline((BYTE *)g_sns_dataitem_hook.trampoline,
                                          target,
                                          g_sns_dataitem_hook.orig,
                                          SNS_DATAITEM_PATCH_LEN);
        FlushInstructionCache(GetCurrentProcess(), g_sns_dataitem_hook.trampoline, tlen);
        if (!write_sns_abs_jump(target, SNS_DATAITEM_PATCH_LEN, (void *)sns_dataitem_json_hook)) {
            VirtualFree(g_sns_dataitem_hook.trampoline, 0, MEM_RELEASE);
            g_sns_dataitem_hook.trampoline = NULL;
            write_status("sns dataitem hook patch failed\n");
            return;
        }
        InterlockedExchange(&g_sns_dataitem_hook.installed, 1);
        write_status("sns dataitem hook installed\n");
    }

    if (InterlockedCompareExchange(&g_sns_response_dtor_hook.installed, 0, 0) == 0) {
        oldp = 0;
        target = (BYTE *)((int64_t)wxb_weixin_module() + WX_OFF_SNS_TIMELINE_RESPONSE_DTOR);
        if (target[0] == 0x48 && target[1] == 0xB8) {
            VirtualProtect(target, SNS_RESPONSE_DTOR_PATCH_LEN, PAGE_EXECUTE_READWRITE, &oldp);
            copy_bytes(target, WX_ORIG_SNS_RESPONSE_DTOR_BYTES, SNS_RESPONSE_DTOR_PATCH_LEN);
            VirtualProtect(target, SNS_RESPONSE_DTOR_PATCH_LEN, oldp, &oldp);
            FlushInstructionCache(GetCurrentProcess(), target, SNS_RESPONSE_DTOR_PATCH_LEN);
            write_status("sns response dtor stale hook restored\n");
        }
        if (!bytes_equal(target, WX_ORIG_SNS_RESPONSE_DTOR_BYTES, SNS_RESPONSE_DTOR_PATCH_LEN)) {
            write_status("sns response dtor hook prologue mismatch; hook not installed\n");
            return;
        }
        copy_bytes(g_sns_response_dtor_hook.orig, target, SNS_RESPONSE_DTOR_PATCH_LEN);
        g_sns_response_dtor_hook.trampoline = VirtualAlloc(NULL, 256, MEM_COMMIT | MEM_RESERVE, PAGE_EXECUTE_READWRITE);
        if (!g_sns_response_dtor_hook.trampoline) {
            write_status("sns response dtor hook trampoline alloc failed\n");
            return;
        }
        tlen = build_sns_entry_trampoline((BYTE *)g_sns_response_dtor_hook.trampoline,
                                          target,
                                          g_sns_response_dtor_hook.orig,
                                          SNS_RESPONSE_DTOR_PATCH_LEN);
        FlushInstructionCache(GetCurrentProcess(), g_sns_response_dtor_hook.trampoline, tlen);
        if (!write_sns_abs_jump(target, SNS_RESPONSE_DTOR_PATCH_LEN, (void *)sns_response_dtor_hook)) {
            VirtualFree(g_sns_response_dtor_hook.trampoline, 0, MEM_RELEASE);
            g_sns_response_dtor_hook.trampoline = NULL;
            write_status("sns response dtor hook patch failed\n");
            return;
        }
        InterlockedExchange(&g_sns_response_dtor_hook.installed, 1);
        write_status("sns response dtor hook installed\n");
    }
}

void uninstall_sns_timeline_object_hook(void) {
    BYTE *target;
    DWORD old = 0;
    if (!wxb_weixin_module()) return;

    if (InterlockedCompareExchange(&g_sns_timeline_object_hook.installed, 0, 0) != 0) {
        target = (BYTE *)((int64_t)wxb_weixin_module() + WX_OFF_SNS_TIMELINE_OBJECT_JSON);
        VirtualProtect(target, SNS_TIMELINE_OBJECT_PATCH_LEN, PAGE_EXECUTE_READWRITE, &old);
        copy_bytes(target, g_sns_timeline_object_hook.orig, SNS_TIMELINE_OBJECT_PATCH_LEN);
        VirtualProtect(target, SNS_TIMELINE_OBJECT_PATCH_LEN, old, &old);
        FlushInstructionCache(GetCurrentProcess(), target, SNS_TIMELINE_OBJECT_PATCH_LEN);
        InterlockedExchange(&g_sns_timeline_object_hook.installed, 0);
        if (g_sns_timeline_object_hook.trampoline) {
            VirtualFree(g_sns_timeline_object_hook.trampoline, 0, MEM_RELEASE);
            g_sns_timeline_object_hook.trampoline = NULL;
        }
        write_status("sns timeline object hook uninstalled\n");
    }

    if (InterlockedCompareExchange(&g_sns_dataitem_hook.installed, 0, 0) != 0) {
        old = 0;
        target = (BYTE *)((int64_t)wxb_weixin_module() + WX_OFF_SNS_DATAITEM_JSON);
        VirtualProtect(target, SNS_DATAITEM_PATCH_LEN, PAGE_EXECUTE_READWRITE, &old);
        copy_bytes(target, g_sns_dataitem_hook.orig, SNS_DATAITEM_PATCH_LEN);
        VirtualProtect(target, SNS_DATAITEM_PATCH_LEN, old, &old);
        FlushInstructionCache(GetCurrentProcess(), target, SNS_DATAITEM_PATCH_LEN);
        InterlockedExchange(&g_sns_dataitem_hook.installed, 0);
        if (g_sns_dataitem_hook.trampoline) {
            VirtualFree(g_sns_dataitem_hook.trampoline, 0, MEM_RELEASE);
            g_sns_dataitem_hook.trampoline = NULL;
        }
        write_status("sns dataitem hook uninstalled\n");
    }

    if (InterlockedCompareExchange(&g_sns_response_dtor_hook.installed, 0, 0) != 0) {
        old = 0;
        target = (BYTE *)((int64_t)wxb_weixin_module() + WX_OFF_SNS_TIMELINE_RESPONSE_DTOR);
        VirtualProtect(target, SNS_RESPONSE_DTOR_PATCH_LEN, PAGE_EXECUTE_READWRITE, &old);
        copy_bytes(target, g_sns_response_dtor_hook.orig, SNS_RESPONSE_DTOR_PATCH_LEN);
        VirtualProtect(target, SNS_RESPONSE_DTOR_PATCH_LEN, old, &old);
        FlushInstructionCache(GetCurrentProcess(), target, SNS_RESPONSE_DTOR_PATCH_LEN);
        InterlockedExchange(&g_sns_response_dtor_hook.installed, 0);
        if (g_sns_response_dtor_hook.trampoline) {
            VirtualFree(g_sns_response_dtor_hook.trampoline, 0, MEM_RELEASE);
            g_sns_response_dtor_hook.trampoline = NULL;
        }
        write_status("sns response dtor hook uninstalled\n");
    }
}

int sns_timeline_object_hook_installed(void) {
    return InterlockedCompareExchange(&g_sns_timeline_object_hook.installed, 0, 0) != 0 ||
           InterlockedCompareExchange(&g_sns_dataitem_hook.installed, 0, 0) != 0 ||
           InterlockedCompareExchange(&g_sns_response_dtor_hook.installed, 0, 0) != 0;
}

LONG sns_timeline_object_capture_count(void) {
    return InterlockedCompareExchange(&g_sns_timeline_object_hook.capture_count, 0, 0) +
           InterlockedCompareExchange(&g_sns_dataitem_hook.capture_count, 0, 0);
}

static void write_sns_timeline_result(const char *seq,
                                      int ok,
                                      const char *message,
                                      int wait_status,
                                      LONG queue_status,
                                      int poll_ms,
                                      const SnsTimelineTask *task) {
    WCHAR path[1024];
    char json[65536];
    char num[64];
    char *p = json;
    char *end = json + sizeof(json) - 1;
    int source_kind = task ? task->source_kind : 0;
    static const int state_handler_offsets[] = {0x60, 0x70, 0x78, 0x98, 0x80, 0x88, 0x90, 0xA0, 0xA8, 0xB0, 0xB8};
    static const char *state_handler_names[] = {
        "handler60_off18870F648",
        "handler70_off18870F728",
        "handler78_off18870F898",
        "handler98_off18870FA08",
        "handler80_off18870FB58",
        "handler88_off18870FE38",
        "handler90_off18870FEF8",
        "handlerA0_off18870FF98",
        "handlerA8_off188710198",
        "handlerB0_loadClosure",
        "handlerB8_off1887102E8"
    };
    static const int timeline_event_offsets[] = {0x48, 0x58, 0x68, 0x78, 0x88, 0xF8};
    static const char *timeline_event_names[] = {
        "event48",
        "event58_timelineKeyRange",
        "event68",
        "event78_objectPayload",
        "event88_refreshPayload",
        "eventF8_fetchComplete"
    };

    wsprintfW(path, L"%s\\result_%S.json", CONTROL_DIR, seq ? seq : "");
    append_raw(&p, end, "{\"ok\":");
    append_raw(&p, end, ok ? "true" : "false");
    append_raw(&p, end, ",\"seq\":\"");
    append_escaped(&p, end, seq ? seq : "");
    append_raw(&p, end, "\",\"command\":\"get_sns_timeline\",\"path\":\"native_sns_timeline_datasource_fetch\",\"message\":\"");
    append_escaped(&p, end, message ? message : "");
    append_raw(&p, end, "\"");
    append_raw(&p, end, ",\"upperCall\":\"");
    append_raw(&p, end, (task && task->rich_mode) ? "sub_183B5B4B0" : "sub_183B24690");
    append_raw(&p, end, "\",\"upperCallOffset\":\"");
    append_raw(&p, end, (task && task->rich_mode) ? "0x3b5b4b0" : "0x3b24690");
    append_raw(&p, end, "\"");
    append_raw(&p, end, ",\"higherLoadCall\":\"sub_183B32CE0\",\"higherLoadCallOffset\":\"0x3b32ce0\"");
    append_raw(&p, end, ",\"lowerCalls\":\"sub_182BC3F30/sub_182BC83B0\",\"lowerCallOffsets\":\"0x2bc3f30,0x2bc83b0\"");
    append_raw(&p, end, ",\"endpoint\":\"/cgi-bin/micromsg-bin/mmsnstimeline\"");
    append_raw(&p, end, ",\"requestProto\":\"micromsg.SnsTimeLineRequest\"");
    append_raw(&p, end, ",\"responseProto\":\"micromsg.SnsTimeLineResponse\"");
    append_raw(&p, end, ",\"richMode\":");
    append_raw(&p, end, (task && task->rich_mode) ? "true" : "false");
    append_raw(&p, end, ",\"richModeNote\":\"sub_183B5B4B0 can schedule SnsObjectDetail/BatchGetSnsObjectDetail after timeline key merge\"");
    append_raw(&p, end, ",\"objectSource\":\"");
    append_raw(&p, end, source_kind == 1 ? "datasource_vtable" : (source_kind == 2 ? "load_closure_plus_8" : "not_found"));
    append_raw(&p, end, "\"");
    append_raw(&p, end, ",\"captureHookInstalled\":");
    append_raw(&p, end, sns_timeline_object_hook_installed() ? "true" : "false");
    append_int_field(&p, end, "capturedObjectCount", (int)sns_timeline_object_capture_count());
    append_raw(&p, end, ",\"timelineObjectCaptureHookInstalled\":");
    append_raw(&p, end, InterlockedCompareExchange(&g_sns_timeline_object_hook.installed, 0, 0) ? "true" : "false");
    append_int_field(&p, end, "timelineObjectCaptureCount",
                     (int)InterlockedCompareExchange(&g_sns_timeline_object_hook.capture_count, 0, 0));
    append_raw(&p, end, ",\"dataItemCaptureHookInstalled\":");
    append_raw(&p, end, InterlockedCompareExchange(&g_sns_dataitem_hook.installed, 0, 0) ? "true" : "false");
    append_int_field(&p, end, "dataItemCaptureCount",
                     (int)InterlockedCompareExchange(&g_sns_dataitem_hook.capture_count, 0, 0));
    append_raw(&p, end, ",\"responseCaptureHookInstalled\":");
    append_raw(&p, end, InterlockedCompareExchange(&g_sns_response_dtor_hook.installed, 0, 0) ? "true" : "false");
    append_int_field(&p, end, "responseCaptureCount",
                     (int)InterlockedCompareExchange(&g_sns_response_dtor_hook.capture_count, 0, 0));
    append_raw(&p, end, ",\"captureLog\":\"bridge/control/sns_timeline_objects.jsonl\"");
    append_raw(&p, end, ",\"responseCaptureLog\":\"bridge/control/sns_timeline_response.jsonl\"");
    append_raw(&p, end, ",\"captureHook\":\"sub_183E2A5C0/sub_183DA0800\",\"captureHookOffset\":\"0x3e2a5c0,0x3da0800\"");
    append_hex_qword(&p, end, "timeline", task ? task->timeline : 0);
    append_hex_qword(&p, end, "closure", task ? task->closure : 0);
    append_hex_qword(&p, end, "dataSourceVtable", (int64_t)wxb_weixin_module() + WX_OFF_SNS_TIMELINE_DATASOURCE_VT);
    append_hex_qword(&p, end, "closureVtable", (int64_t)wxb_weixin_module() + WX_OFF_SNS_TIMELINE_LOAD_CLOSURE_VT);
    append_snapshot(&p, end, "before", task ? &task->before : 0);
    append_snapshot(&p, end, "after", task ? &task->after : 0);
    append_timeline_keys(&p, end, task ? &task->after : 0);
    append_pointer_fields(&p,
                          end,
                          "stateHandlers",
                          task ? task->after.state : 0,
                          state_handler_offsets,
                          state_handler_names,
                          sizeof(state_handler_offsets) / sizeof(state_handler_offsets[0]));
    append_pointer_fields(&p,
                          end,
                          "timelineEventSources",
                          task ? task->timeline : 0,
                          timeline_event_offsets,
                          timeline_event_names,
                          sizeof(timeline_event_offsets) / sizeof(timeline_event_offsets[0]));
    append_raw(&p, end, ",\"timelineObjectLayout\":{\"usernameOff\":\"0x190\",\"createTimeOff\":\"0x1b4\",\"contentDescOff\":\"0x1b8\",\"contentDescShowTypeOff\":\"0x1d8\",\"contentDescSceneOff\":\"0x1dc\",\"source\":\"sub_183DA0BE0/sub_183D9CBC0\"}");
    append_int_field(&p, end, "waitStatus", wait_status);
    append_int_field(&p, end, "queueStatus", (int)queue_status);
    append_int_field(&p, end, "phase", task ? task->phase : 0);
    append_int_field(&p, end, "pollMs", poll_ms);
    append_i64_field(&p, end, "dispatchResult", task ? task->ret : 0);
    append_i64_field(&p, end, "richDispatchResult", task ? task->rich_ret : 0);
    append_raw(&p, end, ",\"sehCode\":");
    wsprintfA(num, "%lu", (unsigned long)(task ? task->seh_code : 0));
    append_raw(&p, end, num);
    append_hex_qword(&p, end, "sehAddr", task ? (int64_t)task->seh_addr : 0);
    append_raw(&p, end, "}");
    *p = 0;
    write_file_bytes(path, json, (DWORD)(p - json));
}

int get_sns_timeline_to_result(const char *seq, int wait_ms, int rich_mode) {
    const SIZE_T arena_size = 0x8000;
    BYTE *arena;
    BYTE *asm_addr;
    SnsTimelineTask *task;
    int asm_len;
    LONG status;
    int loops;
    int ok;
    int poll_ms = 0;
    int before_count;

    if (!seq || !*seq) return 0;
    if (!wxb_ensure_weixin_module()) {
        write_sns_timeline_result(seq, 0, "weixin_module_not_found",
                                  WAIT_FAILED, -12, 0, 0);
        return 0;
    }
    install_sns_timeline_object_hook();

    arena = (BYTE *)VirtualAlloc(NULL, arena_size, MEM_COMMIT | MEM_RESERVE, PAGE_EXECUTE_READWRITE);
    if (!arena) {
        write_sns_timeline_result(seq, 0, "arena_alloc_failed",
                                  WAIT_FAILED, -13, 0, 0);
        return 0;
    }
    ZeroMemory(arena, arena_size);
    asm_addr = arena + 0x200;
    task = (SnsTimelineTask *)(arena + 0x1000);
    task->status = 0;
    task->rich_mode = rich_mode ? 1 : 0;

    if (!sns_find_live_timeline((int64_t)wxb_weixin_module(), task)) {
        task->status = -3;
        write_sns_timeline_result(seq, 0, "live_timeline_object_not_found",
                                  WAIT_FAILED, -3, 0, task);
        retain_arena(arena);
        return 0;
    }

    asm_len = build_sns_timeline_queue_asm(asm_addr,
                                           (int64_t)task,
                                           (int64_t)sns_timeline_worker);
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

    before_count = task->before.count;
    if (status == 1 && task->timeline) {
        int remaining_ms = wait_ms > 500 ? wait_ms - 500 : 0;
        int poll_loops = remaining_ms / 50;
        if (poll_loops > 200) poll_loops = 200;
        for (int i = 0; i < poll_loops; i++) {
            SnsTimelineSnapshot snap;
            if (sns_read_snapshot(task->timeline, &snap)) {
                task->after = snap;
                if (snap.count != before_count || snap.vec_end != task->before.vec_end) break;
            }
            Sleep(50);
            poll_ms += 50;
        }
    }

    ok = (status == 1);
    write_sns_timeline_result(
        seq,
        ok,
        ok ? "sns_timeline_fetch called" :
             (status == -3 ? "live_timeline_object_not_found" :
              status == -4 ? "live_timeline_object_invalid" :
              status == -2 ? "sns_timeline_fetch timeout" :
              status == -1 ? "sns_timeline_fetch exception" :
              "sns_timeline_fetch failed"),
        (status == -2) ? WAIT_TIMEOUT : (status == 1 ? WAIT_OBJECT_0 : WAIT_FAILED),
        status,
        poll_ms,
        task);
    retain_arena(arena);
    return ok;
}
