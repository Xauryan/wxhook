#include "../include/wx_bridge_forward_protocol.h"
#include "../include/wx_bridge_offsets.h"
#include "../include/wx_bridge_runtime.h"
#include "../include/wx_bridge_native_call.h"
#include "../include/wx_bridge_text.h"

static volatile LONG g_forward_local_seed = 0;
static volatile LONG64 g_forward_msg_seed = 0;

void wxb_forward_build_msgsource(char *buf, int max, int64_t source_msg_id,
                                 const char *source_sender_wxid,
                                 const char *self_wxid) {
    char id_s[32];
    const char *sender = (source_sender_wxid && source_sender_wxid[0]) ? source_sender_wxid : wxb_self_wxid();
    const char *self = (self_wxid && self_wxid[0]) ? self_wxid : wxb_self_wxid();
    if (!buf || max <= 0) return;
    wxb_i64toa10(source_msg_id, id_s);
    wsprintfA(buf,
              "<msgsource><alnode><fr>4</fr></alnode><tmp_node><publisher-id>msg_%s|%s|%s</publisher-id></tmp_node></msgsource>",
              id_s, sender ? sender : "", self ? self : "");
}

void wxb_forward_build_source_marker(char *buf, int max, int64_t source_msg_id,
                                     const char *source_sender_wxid,
                                     const char *source_from_wxid,
                                     const char *source_to_wxid) {
    char id_s[32];
    const char *self = wxb_self_wxid()[0] ? wxb_self_wxid() : source_to_wxid;
    const char *sender = (source_sender_wxid && source_sender_wxid[0]) ? source_sender_wxid : source_from_wxid;
    if (!buf || max <= 0) return;
    if (!self || !self[0]) self = source_from_wxid;
    if (!self) self = "";
    if (!sender || !sender[0]) sender = self;
    wxb_i64toa10(source_msg_id, id_s);
    wsprintfA(buf, "msg_%s|%s|%s", id_s, sender ? sender : "", self ? self : "");
}

int64_t wxb_parse_forward_source_orig_msgid(const char *msgsource) {
    const char *p = msgsource;
    if (!p || !p[0]) return 0;
    while ((p = wxb_str_find_sub(p, "msg_")) != NULL) {
        int64_t v = 0;
        p += 4;
        if (*p < '0' || *p > '9') continue;
        while (*p >= '0' && *p <= '9') {
            v = v * 10 + (int64_t)(*p - '0');
            p++;
        }
        return v;
    }
    return 0;
}

int wxb_forward_effective_subtype(int source_type, int source_subtype) {
    (void)source_type;
    return source_subtype;
}

int wxb_forward_effective_subtype_for_content(int source_type, int source_subtype,
                                              const char *content) {
    if (wxb_appmsg_content_is_file_like(source_type, source_subtype, content)) {
        return 6;
    }
    return wxb_forward_effective_subtype(source_type, source_subtype);
}

int64_t wxb_forward_submit_q8(int source_type, int submit_subtype) {
    if (source_type == 3 || source_type == 13 || source_type == 39) {
        return 0x300000064LL;
    }
    if (source_type == 49) {
        (void)submit_subtype;
        return 0x3100000000LL;
    }
    return 0x100000000LL;
}

int wxb_forward_submit_f128(int source_type, int submit_subtype) {
    submit_subtype = wxb_forward_effective_subtype(source_type, submit_subtype);
    if (source_type == 49 && (submit_subtype == 6 || submit_subtype == 130)) {
        return 1;
    }
    return 2;
}

int wxb_forward_submit_f12c(int source_type, int submit_subtype) {
    submit_subtype = wxb_forward_effective_subtype(source_type, submit_subtype);
    if (source_type == 49) {
        int64_t base = (int64_t)wxb_weixin_module();
        uint64_t packed = ((uint64_t)(uint32_t)submit_subtype << 32) | 0x31ULL;
        if (base) {
            __try {
                return (int)((FnCall1)(base + WX_OFF_APPMSG_KIND_QUERY))((int64_t)packed);
            }
            __except (EXCEPTION_EXECUTE_HANDLER) {
                /* fall through to conservative fallback below */
            }
        }
        if (submit_subtype == 6 || submit_subtype == 130) {
            return 1;
        }
    }
    return 0;
}

int32_t wxb_forward_next_local_id(int source_local_id) {
    LONG seed = InterlockedCompareExchange(&g_forward_local_seed, 0, 0);
    while (source_local_id > 0 && seed < source_local_id) {
        LONG prev = InterlockedCompareExchange(&g_forward_local_seed, source_local_id, seed);
        if (prev == seed) break;
        seed = prev;
    }

    {
        LONG next = InterlockedIncrement(&g_forward_local_seed);
        if (next <= 0) {
            InterlockedExchange(&g_forward_local_seed, source_local_id > 0 ? source_local_id : 1);
            next = InterlockedIncrement(&g_forward_local_seed);
        }
        return (int32_t)next;
    }
}

int64_t wxb_forward_next_msg_id(int64_t source_msg_id) {
    LARGE_INTEGER qpc;
    uint64_t seq = (uint64_t)InterlockedIncrement64(&g_forward_msg_seed);
    uint64_t src = (uint64_t)source_msg_id;
    uint64_t mix;
    QueryPerformanceCounter(&qpc);
    mix = ((uint64_t)GetTickCount64() << 21) ^
          ((uint64_t)time(NULL) << 32) ^
          (uint64_t)qpc.QuadPart ^
          ((uint64_t)GetCurrentProcessId() << 17) ^
          (seq * 0x9E3779B97F4A7C15ULL);
    mix ^= (src << 7) | (src >> 3);
    mix = 0x1000000000000000ULL | (mix & 0x0FFFFFFFFFFFFFFFULL);
    if ((int64_t)mix == source_msg_id) mix ^= 0x0008000000000001ULL;
    return (int64_t)mix;
}

int wxb_forward_variant_from_type(int source_type, int source_subtype) {
    if (source_type == 3 || source_type == 13 || source_type == 39) return 0x16;
    if (source_type == 43 || source_type == 44 || source_type == 62) return 0x0C;
    if (source_type == 49 && source_subtype != 0) return 0x20;
    return 1;
}

int wxb_forward_kind_from_type(int source_type, int source_subtype) {
    if (source_type == 43 || source_type == 44 || source_type == 62) return 4;
    if (source_type == 49 && source_subtype != 0) return wxb_forward_submit_f12c(source_type, source_subtype);
    return 0;
}
