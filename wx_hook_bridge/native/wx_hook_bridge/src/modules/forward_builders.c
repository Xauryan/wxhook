/*
 * Layer: forward diagnostics, manager lookup, fake model/send-item builders
 * Source: ../../../../wx_hook_bridge.dll.c lines 2380-2923
 * This file is included by ../wx_hook_bridge.c; do not compile it as a separate translation unit yet.
 */

#include "../include/wx_bridge_forward_builders.h"
#include "../include/wx_bridge_debug_log.h"
#include "../include/wx_bridge_forward_protocol.h"
#include "../include/wx_bridge_memory.h"
#include "../include/wx_bridge_offsets.h"
#include "../include/wx_bridge_runtime.h"
#include "../include/wx_bridge_native_call.h"
#include "../include/wx_bridge_text.h"
#include "../include/wx_bridge_wechat_string.h"
#include <string.h>

typedef struct ForwardLookupState {
    volatile LONG64 manager;
    volatile LONG64 controller;
} ForwardLookupState;

static ForwardLookupState g_forward_lookup_state = {0};

static void forward_xml_attr_value(const char *xml, const char *attr, char *out, int out_sz) {
    const char *p;
    int attr_len;
    int n = 0;
    if (!out || out_sz <= 0) return;
    out[0] = 0;
    if (!xml || !attr || !attr[0]) return;
    attr_len = lstrlenA(attr);
    for (p = xml; *p; p++) {
        if ((p == xml || p[-1] == ' ' || p[-1] == '<' || p[-1] == '\t' || p[-1] == '\r' || p[-1] == '\n') &&
            !strncmp(p, attr, attr_len) && p[attr_len] == '=') {
            char q;
            if (p[attr_len + 1] == '\\' && (p[attr_len + 2] == '"' || p[attr_len + 2] == '\'')) {
                q = p[attr_len + 2];
                p += attr_len + 3;
            } else if (p[attr_len + 1] == '"' || p[attr_len + 1] == '\'') {
                q = p[attr_len + 1];
                p += attr_len + 2;
            } else {
                continue;
            }
            while (*p && *p != q && n + 1 < out_sz) {
                if (p[0] == '\\' && p[1] == q) break;
                out[n++] = *p++;
            }
            out[n] = 0;
            return;
        }
    }
}

static void forward_xml_decode_basic_entities_inplace(char *s) {
    char *r;
    char *w;
    if (!s || !s[0]) return;
    r = s;
    w = s;
    while (*r) {
        if (r[0] == '\\' && (r[1] == '"' || r[1] == '\'' || r[1] == '\\')) {
            *w++ = r[1];
            r += 2;
        } else if (!strncmp(r, "&amp;", 5)) {
            *w++ = '&';
            r += 5;
        } else if (!strncmp(r, "&lt;", 4)) {
            *w++ = '<';
            r += 4;
        } else if (!strncmp(r, "&gt;", 4)) {
            *w++ = '>';
            r += 4;
        } else if (!strncmp(r, "&quot;", 6)) {
            *w++ = '"';
            r += 6;
        } else if (!strncmp(r, "&apos;", 6)) {
            *w++ = '\'';
            r += 6;
        } else {
            *w++ = *r++;
        }
    }
    *w = 0;
}

static BYTE *forward_pool_alloc_aligned(BYTE **pool, BYTE *pool_end, SIZE_T size, SIZE_T align) {
    uintptr_t p;
    uintptr_t aligned;
    if (!pool || !*pool || !pool_end || !size) return NULL;
    if (!align) align = 16;
    p = (uintptr_t)(*pool);
    aligned = (p + align - 1) & ~(uintptr_t)(align - 1);
    if ((BYTE *)aligned + size >= pool_end) return NULL;
    *pool = (BYTE *)aligned + size;
    return (BYTE *)aligned;
}

static int forward_attach_contact_compact_payload(int64_t base, int64_t msg,
                                                  const char *content,
                                                  BYTE **pool, BYTE *pool_end) {
    char username[512] = {0};
    char nickname[512] = {0};
    char avatar[1024] = {0};
    char certflag[64] = {0};
    char brand_type[32] = {0};
    BYTE *ctl_mem;
    int64_t ctl;
    int64_t obj;
    if (!base || !msg || !content || !content[0] || !pool || !*pool) return 0;
    if (!str_has(content, "<msg") || !str_has(content, "username=")) return 0;

    forward_xml_attr_value(content, "username", username, sizeof(username));
    forward_xml_attr_value(content, "nickname", nickname, sizeof(nickname));
    forward_xml_attr_value(content, "avatar", avatar, sizeof(avatar));
    if (!avatar[0]) forward_xml_attr_value(content, "smallheadimgurl", avatar, sizeof(avatar));
    if (!avatar[0]) forward_xml_attr_value(content, "bigheadimgurl", avatar, sizeof(avatar));
    forward_xml_attr_value(content, "certflag", certflag, sizeof(certflag));
    forward_xml_attr_value(content, "brandType", brand_type, sizeof(brand_type));

    forward_xml_decode_basic_entities_inplace(username);
    forward_xml_decode_basic_entities_inplace(nickname);
    forward_xml_decode_basic_entities_inplace(avatar);
    forward_xml_decode_basic_entities_inplace(certflag);
    forward_xml_decode_basic_entities_inplace(brand_type);
    if (!username[0]) return 0;
    if (!nickname[0]) safe_copy(nickname, sizeof(nickname), username);
    if (!certflag[0]) safe_copy(certflag, sizeof(certflag), "0");

    ctl_mem = forward_pool_alloc_aligned(pool, pool_end, 0x98, 16);
    if (!ctl_mem) return 0;
    ZeroMemory(ctl_mem, 0x98);
    ctl = (int64_t)ctl_mem;
    obj = ctl + 0x10;

    w64((void *)ctl, base + WX_OFF_CONTACT_COMPACT_CTL_VT);
    w32((void *)(ctl + 0x8), 0x100000);
    w32((void *)(ctl + 0xC), 0x100000);
    wx_set_std_string_arena(obj + 0x00, username, lstrlenA(username), pool, pool_end);
    wx_set_std_string_arena(obj + 0x20, nickname, lstrlenA(nickname), pool, pool_end);
    wx_set_std_string_arena(obj + 0x40, avatar, lstrlenA(avatar), pool, pool_end);
    wx_set_std_string_arena(obj + 0x60, certflag, lstrlenA(certflag), pool, pool_end);
    w32((void *)(obj + 0x80), brand_type[0] ? (int)a2i64(brand_type) : 0);

    w64((void *)(msg + 0x6C0), obj);
    w64((void *)(msg + 0x6C8), ctl);
    {
        char dbg[512];
        wsprintfA(dbg,
                  "[forward-contact-card] compact payload attached msg=%I64x slot={%I64x,%I64x} username='%.120s' nickname='%.120s' avatar=%d brandType=%d",
                  (unsigned __int64)msg,
                  (unsigned __int64)obj,
                  (unsigned __int64)ctl,
                  username,
                  nickname,
                  avatar[0] ? 1 : 0,
                  brand_type[0] ? (int)a2i64(brand_type) : 0);
        forward_dbg(dbg);
    }
    return 1;
}

static int is_forward_manager_candidate(int64_t cand, int64_t vt) {
    int64_t inline_obj;
    int64_t inline_ctl;
    int ctl_ref;
    if (!is_readable_ptr(cand, 936)) return 0;
    if (r64((void *)cand) != vt) return 0;
    inline_obj = r64((void *)(cand + 920));
    inline_ctl = r64((void *)(cand + 928));
    if (!is_readable_ptr(inline_obj, 8) || !is_readable_ptr(inline_ctl, 16)) return 0;
    if (!r64((void *)inline_obj) || !r64((void *)inline_ctl)) return 0;
    ctl_ref = r32((void *)(inline_ctl + 8));
    if (ctl_ref <= 0 || ctl_ref > 0x100000) return 0;
    return 1;
}

int is_forward_manager_candidate_any(int64_t base, int64_t cand) {
    if (!base || !cand) return 0;
    return is_forward_manager_candidate(cand, base + WX_OFF_FORWARD_UI_MANAGER_VT) ||
           is_forward_manager_candidate(cand, base + WX_OFF_FORWARD_MANAGER_VT);
}

void forward_remember_manager(int64_t base, int64_t manager) {
    if (base && is_forward_manager_candidate_any(base, manager)) {
        InterlockedExchange64(&g_forward_lookup_state.manager, manager);
    }
}

int64_t find_forward_manager_by_vt(int64_t vt) {
    MEMORY_BASIC_INFORMATION mbi;
    BYTE *addr = 0;
    while (VirtualQuery(addr, &mbi, sizeof(mbi)) == sizeof(mbi)) {
        if (mbi.State == MEM_COMMIT &&
            !(mbi.Protect & PAGE_GUARD) &&
            mbi.Protect != PAGE_NOACCESS &&
            mbi.RegionSize) {
            BYTE *region = (BYTE *)mbi.BaseAddress;
            SIZE_T size = mbi.RegionSize;
            __try {
                for (SIZE_T off = 0; off + sizeof(int64_t) <= size; off += sizeof(void *)) {
                    int64_t cand = (int64_t)(region + off);
                    if (*(volatile int64_t *)(region + off) == vt && is_forward_manager_candidate(cand, vt)) {
                        return cand;
                    }
                }
            }
            __except (EXCEPTION_EXECUTE_HANDLER) {}
        }
        if ((uint64_t)mbi.BaseAddress + (uint64_t)mbi.RegionSize <= (uint64_t)addr) break;
        addr = (BYTE *)mbi.BaseAddress + mbi.RegionSize;
    }
    return 0;
}

int64_t find_forward_manager(int64_t base) {
    int64_t cached = (int64_t)InterlockedCompareExchange64(&g_forward_lookup_state.manager, 0, 0);
    int64_t manager;
    if (cached && is_forward_manager_candidate_any(base, cached)) {
        return cached;
    }

    manager = find_forward_manager_by_vt(base + WX_OFF_FORWARD_UI_MANAGER_VT);
    if (!manager) {
        manager = find_forward_manager_by_vt(base + WX_OFF_FORWARD_MANAGER_VT);
    }
    if (manager) {
        InterlockedExchange64(&g_forward_lookup_state.manager, manager);
        return manager;
    }
    return 0;
}

static int is_forward_controller_candidate(int64_t base, int64_t cand) {
    int64_t holder;
    if (!base || !cand) return 0;
    if (!is_readable_ptr(cand, 0xB80)) return 0;
    if (r64((void *)cand) != base + WX_OFF_FORWARD_CONTROLLER_VT) return 0;
    holder = r64((void *)(cand + 0xB78));
    if (!is_readable_ptr(holder, 0xA0)) return 0;
    if (!is_readable_ptr(holder + 0x90, 0x10)) return 0;
    return 1;
}

int64_t find_forward_controller_by_vt(int64_t base, int64_t vt) {
    MEMORY_BASIC_INFORMATION mbi;
    BYTE *addr = 0;
    while (VirtualQuery(addr, &mbi, sizeof(mbi)) == sizeof(mbi)) {
        if (mbi.State == MEM_COMMIT &&
            !(mbi.Protect & PAGE_GUARD) &&
            mbi.Protect != PAGE_NOACCESS &&
            mbi.RegionSize) {
            BYTE *region = (BYTE *)mbi.BaseAddress;
            SIZE_T size = mbi.RegionSize;
            __try {
                for (SIZE_T off = 0; off + sizeof(int64_t) <= size; off += sizeof(void *)) {
                    int64_t cand = (int64_t)(region + off);
                    if (*(volatile int64_t *)(region + off) == vt &&
                        is_forward_controller_candidate(base, cand)) {
                        return cand;
                    }
                }
            }
            __except (EXCEPTION_EXECUTE_HANDLER) {}
        }
        if ((uint64_t)mbi.BaseAddress + (uint64_t)mbi.RegionSize <= (uint64_t)addr) break;
        addr = (BYTE *)mbi.BaseAddress + mbi.RegionSize;
    }
    return 0;
}

int64_t find_forward_controller(int64_t base) {
    int64_t cached = (int64_t)InterlockedCompareExchange64(&g_forward_lookup_state.controller, 0, 0);
    int64_t controller;
    if (cached && is_forward_controller_candidate(base, cached)) {
        return cached;
    }

    controller = find_forward_controller_by_vt(base, base + WX_OFF_FORWARD_CONTROLLER_VT);
    if (controller) {
        InterlockedExchange64(&g_forward_lookup_state.controller, controller);
        return controller;
    }
    return 0;
}

void forward_build_fake_request_msg(int64_t base, int64_t src_ctl, int64_t src_msg,
                                           const char *source_from_wxid, const char *source_to_wxid,
                                           const char *source_sender_wxid, const char *content,
                                           const char *source_msgsource, int source_type,
                                           int source_subtype, int source_local_id,
                                           int64_t source_msg_id, int32_t source_time,
                                           BYTE **pool, BYTE *pool_end) {
    char msgsource[768];
    const char *self_wxid = wxb_self_wxid()[0] ? wxb_self_wxid() : "";
    const char *from_wxid = (source_from_wxid && source_from_wxid[0]) ? source_from_wxid : source_sender_wxid;
    if (!self_wxid[0] && source_to_wxid && source_to_wxid[0] &&
        !str_eq(source_to_wxid, "filehelper") && !is_chatroom_id(source_to_wxid)) {
        self_wxid = source_to_wxid;
    }
    if (!self_wxid[0] && source_from_wxid && source_from_wxid[0] &&
        !str_eq(source_from_wxid, "filehelper") && !is_chatroom_id(source_from_wxid)) {
        self_wxid = source_from_wxid;
    }
    const char *to_wxid = (source_to_wxid && source_to_wxid[0]) ? source_to_wxid : self_wxid;
    const char *sender_wxid = (source_sender_wxid && source_sender_wxid[0]) ? source_sender_wxid : from_wxid;
    if (!from_wxid || !from_wxid[0]) from_wxid = sender_wxid;
    if (!sender_wxid || !sender_wxid[0]) sender_wxid = from_wxid;
    if (!source_type) source_type = 1;
    if (!source_time) source_time = (int32_t)time(NULL);
    ZeroMemory(msgsource, sizeof(msgsource));
    if (source_msgsource && source_msgsource[0] && str_has(source_msgsource, "msg_")) {
        safe_copy(msgsource, sizeof(msgsource), source_msgsource);
    } else {
        forward_build_msgsource(msgsource, sizeof(msgsource), source_msg_id, sender_wxid, self_wxid);
    }

    w64((void *)src_ctl, base + WX_OFF_SRC_MSG_CTL_VT);
    w32((void *)(src_ctl + 0x8), 0x100000);
    w32((void *)(src_ctl + 0xC), 0x100000);

    w64((void *)src_msg, base + WX_OFF_SRC_MSG_VT);
    w64((void *)(src_msg + 0x8), 0x100000000LL);
    w32((void *)(src_msg + 0xC), source_type);
    w32((void *)(src_msg + 0x10), source_subtype);
    wx_set_std_string_arena(src_msg + 0x18, from_wxid, lstrlenA(from_wxid), pool, pool_end);
    wx_set_std_string_arena(src_msg + 0x38, to_wxid, lstrlenA(to_wxid), pool, pool_end);
    wx_set_std_string_arena(src_msg + 0x58, sender_wxid, lstrlenA(sender_wxid), pool, pool_end);
    w32((void *)(src_msg + 0x104), source_local_id);
    w64((void *)(src_msg + 0x108), source_msg_id);
    w64((void *)(src_msg + 0x110), source_time);
    w32((void *)(src_msg + 0x124), source_time);
    w32((void *)(src_msg + 0x128), 2);
    w32((void *)(src_msg + 0x12C), 0);
    w8((void *)(src_msg + 0x138), 1);
    wx_set_std_string_arena(src_msg + 0x160, content, lstrlenA(content), pool, pool_end);
    wx_set_std_string_arena(src_msg + 0x180, msgsource, lstrlenA(msgsource), pool, pool_end);
}

void forward_build_submit_msg(int64_t base, int64_t msg,
                                     const char *to_wxid, const char *content,
                                     int source_type, int source_subtype,
                                     int source_local_id, int64_t source_msg_id,
                                     int32_t source_time,
                                      const char *source_from_wxid,
                                      const char *source_to_wxid,
                                      const char *source_sender_wxid,
                                      const char *source_msgsource,
                                      int target_fields,
                                      BYTE **pool, BYTE *pool_end) {
    char msgsource[768];
    int submit_subtype = source_subtype;
    const char *self_wxid = wxb_self_wxid()[0] ? wxb_self_wxid() : "";
    const char *sender_wxid = (source_sender_wxid && source_sender_wxid[0]) ? source_sender_wxid : source_from_wxid;
    const char *from_field;
    const char *to_field;
    const char *sender_field;
    int32_t forward_time = (int32_t)time(NULL);
    int32_t forward_local_id = forward_next_local_id(source_local_id);
    int64_t forward_msg_id = forward_next_msg_id(source_msg_id);
    int64_t forward_time_field = (((int64_t)2) << 32) | (uint32_t)forward_time;
    int32_t msg_local_id = forward_local_id;
    int64_t msg_svr_id = forward_msg_id;
    int64_t msg_time_field = forward_time_field;
    if (!self_wxid[0] && source_to_wxid && source_to_wxid[0] &&
        !str_eq(source_to_wxid, "filehelper") && !is_chatroom_id(source_to_wxid)) {
        self_wxid = source_to_wxid;
    }
    if (!self_wxid[0] && source_from_wxid && source_from_wxid[0] &&
        !str_eq(source_from_wxid, "filehelper") && !is_chatroom_id(source_from_wxid)) {
        self_wxid = source_from_wxid;
    }
    if (!sender_wxid || !sender_wxid[0]) sender_wxid = self_wxid;
    if (!source_type) source_type = 1;
    submit_subtype = forward_effective_subtype_for_content(source_type, source_subtype, content);

    from_field = self_wxid;
    to_field = to_wxid;
    sender_field = self_wxid;
    if (!target_fields && source_type != 1 && source_from_wxid && source_from_wxid[0]) {
        from_field = source_from_wxid;
        to_field = (source_to_wxid && source_to_wxid[0]) ? source_to_wxid : self_wxid;
        sender_field = sender_wxid && sender_wxid[0] ? sender_wxid : source_from_wxid;
        msg_local_id = source_local_id ? source_local_id : forward_local_id;
        msg_svr_id = source_msg_id ? source_msg_id : forward_msg_id;
        msg_time_field = source_time ? source_time : forward_time_field;
    }

    ZeroMemory(msgsource, sizeof(msgsource));
    if (source_msgsource && source_msgsource[0] && str_has(source_msgsource, "msg_")) {
        safe_copy(msgsource, sizeof(msgsource), source_msgsource);
    } else {
        forward_build_msgsource(msgsource, sizeof(msgsource), source_msg_id, sender_wxid, self_wxid);
    }

    w64((void *)msg, base + WX_OFF_SRC_MSG_VT);
    w64((void *)(msg + 0x8), target_fields ? forward_submit_q8(source_type, submit_subtype) : 0x100000000LL);
    w32((void *)(msg + 0xC), source_type);
    w32((void *)(msg + 0x10), submit_subtype);
    wx_set_std_string_arena(msg + 0x18, from_field, lstrlenA(from_field), pool, pool_end);
    wx_set_std_string_arena(msg + 0x38, to_field, lstrlenA(to_field), pool, pool_end);
    wx_set_std_string_arena(msg + 0x58, sender_field, lstrlenA(sender_field), pool, pool_end);
    w32((void *)(msg + 0x104), msg_local_id);
    w64((void *)(msg + 0x108), msg_svr_id);
    w64((void *)(msg + 0x110), msg_time_field);
    w32((void *)(msg + 0x118), source_type);
    w32((void *)(msg + 0x120), 2);
    w32((void *)(msg + 0x124), target_fields ? forward_time : (source_time ? source_time : forward_time));
    w32((void *)(msg + 0x128), target_fields ? forward_submit_f128(source_type, submit_subtype) : 5);
    w32((void *)(msg + 0x12C), target_fields ? forward_submit_f12c(source_type, submit_subtype) : forward_kind_from_type(source_type, submit_subtype));
    w8((void *)(msg + 0x138), 1);
    wx_set_std_string_arena(msg + 0x160, content, lstrlenA(content), pool, pool_end);
    wx_set_std_string_arena(msg + 0x180, msgsource, lstrlenA(msgsource), pool, pool_end);
    if (source_type == 42) {
        forward_attach_contact_compact_payload(base, msg, content, pool, pool_end);
    }
}

int forward_fill_high_send_item(int64_t base, int64_t item,
                                       const char *content,
                                       int source_type, int source_subtype,
                                       int source_local_id, int64_t source_msg_id,
                                       int32_t source_time,
                                       const char *source_from_wxid,
                                       const char *source_to_wxid,
                                       const char *source_sender_wxid,
                                       const char *source_msgsource,
                                       int64_t src_msg, int64_t src_ctl,
                                       BYTE **pool, BYTE *pool_end) {
    char msgsource[768];
    const char *self_wxid = wxb_self_wxid()[0] ? wxb_self_wxid() : "";
    const char *from_wxid = (source_from_wxid && source_from_wxid[0]) ? source_from_wxid : source_sender_wxid;
    const char *to_wxid = (source_to_wxid && source_to_wxid[0]) ? source_to_wxid : self_wxid;
    const char *sender_wxid = (source_sender_wxid && source_sender_wxid[0]) ? source_sender_wxid : from_wxid;
    int32_t ts = source_time ? source_time : (int32_t)time(NULL);
    if (!self_wxid[0] && to_wxid && to_wxid[0] && !str_eq(to_wxid, "filehelper") && !is_chatroom_id(to_wxid)) {
        self_wxid = to_wxid;
    }
    if (!self_wxid[0] && from_wxid && from_wxid[0] && !str_eq(from_wxid, "filehelper") && !is_chatroom_id(from_wxid)) {
        self_wxid = from_wxid;
    }
    if (!from_wxid || !from_wxid[0]) from_wxid = sender_wxid;
    if (!sender_wxid || !sender_wxid[0]) sender_wxid = from_wxid;
    if (!to_wxid || !to_wxid[0]) to_wxid = self_wxid;
    if (!from_wxid) from_wxid = "";
    if (!to_wxid) to_wxid = "";
    if (!sender_wxid) sender_wxid = "";
    if (!content) content = "";
    if (!source_type) source_type = 1;
    int effective_subtype = forward_effective_subtype_for_content(source_type, source_subtype, content);

    ((FnCall1)(base + WX_OFF_SEND_ITEM_CTOR))(item);
    if (r64((void *)item) != base + WX_OFF_SEND_ITEM_VT) {
        return 0;
    }

    ZeroMemory(msgsource, sizeof(msgsource));
    if (source_msgsource && source_msgsource[0] && str_has(source_msgsource, "msg_")) {
        safe_copy(msgsource, sizeof(msgsource), source_msgsource);
    } else {
        forward_build_msgsource(msgsource, sizeof(msgsource), source_msg_id, sender_wxid, self_wxid);
    }

    /*
     * Evidence-based gate fields only:
     *
     * 18099EE00 / 18099EF20 / 18099F020 / 1809A6BD0 read:
     *   +0xA8 as the integer input checked by 1809A67B0
     *   +0xAC as the state family:
     *         5/11 => 18099EE00 fast-fail family for generic items
     *         9/10 => media/file-forward family that can continue into 18099F020
     *   +0xB4 as media-kind:
     *         4 => image-like (3/13/39)
     *         6 => av/file-forward families (43/44/62, 49/6, 49/130)
     *
     * The rest of the full send_item source object is still produced by the real
     * WeChat factory path; do not guess beyond the fields proven by static analysis.
     */
    int send_state = 5;
    int media_kind = 0;
    if (source_type == 3 || source_type == 13 || source_type == 39) {
        send_state = 9;
        media_kind = 4;
    } else if (source_type == 43 || source_type == 44 || source_type == 62) {
        send_state = 9;
        media_kind = 6;
    } else if (source_type == 49 && effective_subtype == 57) {
        /* Observed retained quote/reply source items reach the gate as 49/57 with
         * state=5, media=0. */
        send_state = 5;
        media_kind = 0;
    } else if (source_type == 49) {
        /* Observed retained appmsg source items such as 49/3 and 49/6 reach the
         * gate as appmsg subtype-preserving items with state=3, media=0. */
        send_state = 3;
        media_kind = 0;
    }

    /*
     * sub_18099EDA0/sub_18099F020 read this send_item directly at high_obj+0xD8.
     * This is still incomplete versus the real source object copied by 181611CB0,
     * but these offsets are now limited to values justified by static analysis.
     */
    w32((void *)(item + 0x08), source_type);
    w32((void *)(item + 0x0C), effective_subtype);
    wx_set_std_string_arena(item + 0x10, from_wxid, lstrlenA(from_wxid), pool, pool_end);
    wx_set_std_string_arena(item + 0x30, to_wxid, lstrlenA(to_wxid), pool, pool_end);
    wx_set_std_string_arena(item + 0x50, sender_wxid, lstrlenA(sender_wxid), pool, pool_end);
    wx_set_std_string_arena(item + 0x70, from_wxid, lstrlenA(from_wxid), pool, pool_end);
    w32((void *)(item + 0x94), source_local_id);
    w64((void *)(item + 0x98), source_msg_id);
    w64((void *)(item + 0xA0), (int64_t)ts);
    w32((void *)(item + 0xA8), ts);
    w32((void *)(item + 0xAC), send_state);
    w32((void *)(item + 0xB0), 0);
    w32((void *)(item + 0xB4), media_kind);
    wx_set_std_string_arena(item + 0xB8, content, lstrlenA(content), pool, pool_end);
    wx_set_std_string_arena(item + 0xD8, msgsource, lstrlenA(msgsource), pool, pool_end);
    wx_set_std_string_arena(item + 0x118, "", 0, pool, pool_end);
    wx_set_std_string_arena(item + 0x1C0, "", 0, pool, pool_end);
    w64((void *)(item + 0x270), src_msg);
    w64((void *)(item + 0x278), src_ctl);
    w64((void *)(item + 0x290), src_msg);
    w64((void *)(item + 0x298), src_ctl);
    return 1;
}

void quote_build_fake_source_msg(int64_t base, int64_t src_ctl, int64_t src_msg,
                                 int64_t quoted_svrid, int32_t quoted_local_id,
                                 const char *quoted_content, const char *quoted_from,
                                 int quoted_type, int32_t createtime,
                                 const char *refer_chat, const char *source_to,
                                 const char *quoted_msgsource,
                                 BYTE **pool, BYTE *pool_end) {
    const int fake_refs = 0x100000;

    w64((void *)src_ctl, base + WX_OFF_SRC_MSG_CTL_VT);
    w32((void *)(src_ctl + 0x8), fake_refs);
    w32((void *)(src_ctl + 0xC), fake_refs);

    w64((void *)src_msg, base + WX_OFF_SRC_MSG_VT);
    w32((void *)(src_msg + 0xC), quoted_type);
    wx_set_str_arena(src_msg + WX_MSG_FROM_OFF, refer_chat, lstrlenA(refer_chat), pool, pool_end);
    wx_set_str_arena(src_msg + WX_MSG_SELF_OFF, source_to, lstrlenA(source_to), pool, pool_end);
    wx_set_str_arena(src_msg + 0x58, quoted_from, lstrlenA(quoted_from), pool, pool_end);
    w32((void *)(src_msg + WX_MSG_LOCAL_ID_OFF), quoted_local_id);
    w64((void *)(src_msg + WX_MSG_ID_OFF), quoted_svrid);
    w64((void *)(src_msg + WX_MSG_TS_OFF), (int64_t)createtime);
    wx_set_str_arena(src_msg + WX_MSG_CONTENT_OFF, quoted_content, lstrlenA(quoted_content), pool, pool_end);
    wx_set_str_arena(src_msg + WX_MSG_SIGNATURE_OFF,
                     quoted_msgsource ? quoted_msgsource : "",
                     quoted_msgsource ? lstrlenA(quoted_msgsource) : 0,
                     pool, pool_end);
    wx_set_str_arena(src_msg + 0x270, refer_chat, lstrlenA(refer_chat), pool, pool_end);
}

/* Real appmsg forwards captured in forward_debug now cover 49/3, 49/5, 49/33,
 * 49/74 in addition to 49/6 and 49/130, and they all arrive at submit with
 * variant=32 while msg+0x118/+0x120 are normalised to 49/2. Keep the entire
 * non-zero appmsg family on the appmsg forward variant here. */
