/*
 * Module: receive processing and core hook installation/uninstallation
 * Source: ../../../../wx_hook_bridge.dll.c lines 8511-9359
 */

#include "../include/wx_bridge_receive_hooks.h"
#include "../include/wx_bridge_anti_revoke.h"
#include "../include/wx_bridge_a8key_task.h"
#include "../include/wx_bridge_account_info.h"
#include "../include/wx_bridge_debug_log.h"
#include "../include/wx_bridge_file_io.h"
#include "../include/wx_bridge_forward_hooks.h"
#include "../include/wx_bridge_forward_retained.h"
#include "../include/wx_bridge_friend_accept.h"
#include "../include/wx_bridge_hongbao_cold.h"
#include "../include/wx_bridge_hongbao_hooks.h"
#include "../include/wx_bridge_hongbao_noclick.h"
#include "../include/wx_bridge_hongbao_status.h"
#include "../include/wx_bridge_media_ops.h"
#include "../include/wx_bridge_memory.h"
#include "../include/wx_bridge_offsets.h"
#include "../include/wx_bridge_paths.h"
#include "../include/wx_bridge_protocol.h"
#include "../include/wx_bridge_realtime_relay.h"
#include "../include/wx_bridge_revoke_cache.h"
#include "../include/wx_bridge_revoke_forward.h"
#include "../include/wx_bridge_revoke_protocol.h"
#include "../include/wx_bridge_room_contacts.h"
#include "../include/wx_bridge_runtime.h"
#include "../include/wx_bridge_sns_publish.h"
#include "../include/wx_bridge_text.h"
#include "../include/wx_bridge_voice_probe.h"
#include "../include/wx_bridge_wechat_string.h"

#define REALTIME_CONTENT_BUFFER 65536
#define REALTIME_ENVELOPE_BUFFER 131072
#define REALTIME_JSON_TAIL_RESERVE 8192

static volatile LONG64 g_last_event_key = 0;

/* FNV-1a is used only to make a compact, deterministic session component for
 * the native event id.  The complete peer/room ids remain in the envelope. */
static ULONGLONG realtime_hash_string(const char *value) {
    ULONGLONG hash = 14695981039346656037ULL;
    const unsigned char *cursor = (const unsigned char *)(value ? value : "");
    while (*cursor) {
        hash ^= (ULONGLONG)*cursor++;
        hash *= 1099511628211ULL;
    }
    return hash;
}

static ULONGLONG realtime_event_key(int64_t msg_id, int32_t local_id,
                                    int32_t create_time, const char *session,
                                    const char *from, const char *to) {
    ULONGLONG hash = realtime_hash_string(session);
    hash ^= (ULONGLONG)msg_id;
    hash *= 1099511628211ULL;
    hash ^= (ULONGLONG)(uint32_t)local_id;
    hash *= 1099511628211ULL;
    hash ^= (ULONGLONG)(uint32_t)create_time;
    hash *= 1099511628211ULL;
    hash ^= realtime_hash_string(from);
    hash *= 1099511628211ULL;
    hash ^= realtime_hash_string(to);
    return hash;
}

/* append_escaped() intentionally stops at the buffer edge, which can leave a
 * JSON string unterminated.  Realtime envelopes reserve a tail for metadata
 * and use this bounded variant so a long message is truncated before the
 * closing quote rather than producing a poison queue file. */
static int realtime_append_escaped_bounded(char **p, char *end, const char *value) {
    const unsigned char *cursor = (const unsigned char *)(value ? value : "");
    int truncated = 0;
    while (*cursor) {
        unsigned char c = *cursor;
        int needed = 1;
        int sequence = 1;
        if (c == '\\' || c == '"') needed = 2;
        else if (c == '\n' || c == '\r' || c == '\t') needed = 2;
        else if (c < 0x20) needed = 6;
        else if (c >= 0x80) {
            if ((c & 0xE0) == 0xC0 && (cursor[1] & 0xC0) == 0x80) sequence = 2;
            else if ((c & 0xF0) == 0xE0 && (cursor[1] & 0xC0) == 0x80 &&
                     (cursor[2] & 0xC0) == 0x80) sequence = 3;
            else if ((c & 0xF8) == 0xF0 && (cursor[1] & 0xC0) == 0x80 &&
                     (cursor[2] & 0xC0) == 0x80 && (cursor[3] & 0xC0) == 0x80) sequence = 4;
            else { needed = 6; sequence = 0; }
            if (sequence) needed = sequence;
        }
        if (end - *p < needed) { truncated = 1; break; }
        if (!sequence) {
            append_raw(p, end, "\\uFFFD");
        } else if (c == '\\' || c == '"') {
            *(*p)++ = '\\'; *(*p)++ = (char)c;
        } else if (c == '\n') {
            *(*p)++ = '\\'; *(*p)++ = 'n';
        } else if (c == '\r') {
            *(*p)++ = '\\'; *(*p)++ = 'r';
        } else if (c == '\t') {
            *(*p)++ = '\\'; *(*p)++ = 't';
        } else if (c < 0x20) {
            static const char hex[] = "0123456789abcdef";
            *(*p)++ = '\\'; *(*p)++ = 'u'; *(*p)++ = '0'; *(*p)++ = '0';
            *(*p)++ = hex[(c >> 4) & 0xF]; *(*p)++ = hex[c & 0xF];
        } else {
            for (int i = 0; i < sequence; ++i) *(*p)++ = (char)cursor[i];
        }
        cursor += sequence ? sequence : 1;
    }
    return truncated || *cursor != 0;
}

typedef struct ReceiveHookState {
    volatile LONG recv_installed;
    volatile LONG async_queue_installed;
    BYTE orig_doaddmsg[7];
    BYTE orig_queue_hook[18];
    void *recv_trampoline;
    void *queue_trampoline;
} ReceiveHookState;

static ReceiveHookState g_receive_hook_state = {0};

static const BYTE WX_ORIG_QUEUE_HOOK_BYTES[18] = {
    0x55,0x56,0x57,0x48,0x81,0xEC,0x00,0x01,0x00,0x00,
    0x48,0x8D,0xAC,0x24,0x80,0x00,0x00,0x00
};
static void handle_revoke_forward(const char *content, int msg_type_int,
                                    int64_t struct_orig_msgid) {
    if (msg_type_int >= 10000 && content && str_has(content, "revoke")) {
        char dbg[1024], sid[32];
        i64toa10(struct_orig_msgid, sid);
        wsprintfA(dbg, "revoke_debug: type=%d has_revoke=%d has_newmsgid=%d has_msgid=%d struct_orig=%s\r\n",
                  msg_type_int,
                  str_has(content, "<revokemsg") ? 1 : 0,
                  str_has(content, "<newmsgid>") ? 1 : 0,
                  str_has(content, "<msgid>") ? 1 : 0,
                  sid);
        write_status(dbg);
    }
    if (!is_revoke_msg(content, msg_type_int)) return;

    write_status("revoke_forward: detected revoke msg");
    int64_t orig_msgid = struct_orig_msgid;
    if (!orig_msgid) {
        orig_msgid = parse_revoke_msgid(content);
    }
    if (!orig_msgid) {
        write_status("revoke_forward: msgid parse failed (struct=0, xml=0)");
        return;
    }

    char dbg2[128], orig_s[32];
    i64toa10(orig_msgid, orig_s);
    wsprintfA(dbg2, "revoke_forward: looking for orig_msgid=%s", orig_s);
    write_status(dbg2);

    handle_revoke_notice_by_id(orig_msgid, "", parse_revoke_time(content), "revoke_sysmsg");
}

/* The hook thread only copies into a preallocated lock-free relay slot. */

static void process_message(int64_t msg_base) {
    if (!msg_base) return;
    int64_t msg_id = r64((void *)(msg_base + WX_MSG_ID_OFF));
    ULONGLONG pm_t0 = GetTickCount64();

    int32_t local_id = r32((void *)(msg_base + WX_MSG_LOCAL_ID_OFF));
    char id_s[32], local_id_s[32], type_s[32], subtype_s[32], ts_s[32];
    char from[512] = {0}, self_id[512] = {0};
    char content[REALTIME_CONTENT_BUFFER] = {0}, sig[1024] = {0};
    i64toa10(msg_id, id_s);
    i64toa10(local_id, local_id_s);
    int msg_type_int = r32((void *)(msg_base + 0xC));
    int msg_subtype_int = r32((void *)(msg_base + WX_MSG_SUBTYPE_OFF));
    i64toa10(msg_type_int, type_s);
    i64toa10(msg_subtype_int, subtype_s);
    int32_t msg_createtime = read_msg_createtime(msg_base);
    i64toa10(msg_createtime, ts_s);

    wx_read_str(msg_base + WX_MSG_FROM_OFF, from, sizeof(from));
    wx_read_str(msg_base + WX_MSG_SELF_OFF, self_id, sizeof(self_id));
    int content_read = wx_read_str(msg_base + WX_MSG_CONTENT_OFF, content, sizeof(content));
    int content_original_bytes = r32((void *)(msg_base + WX_MSG_CONTENT_OFF + 0x10));
    int content_source_truncated = content_original_bytes > content_read;
    wx_read_str(msg_base + WX_MSG_SIGNATURE_OFF, sig, sizeof(sig));

    ULONGLONG pm_claim = pm_t0;
    ULONGLONG pm_retain = pm_t0, pm_revoke = pm_t0, pm_push = pm_t0;

    int from_is_room = is_chatroom_id(from);
    int to_is_room = is_chatroom_id(self_id);
    int is_group = from_is_room || to_is_room;
    int is_outgoing = 0;
    int is_incoming = 0;
    char room_id[512] = {0};
    char peer_wxid[512] = {0};
    char sender_wxid[512] = {0};
    char direction[16] = "unknown";

    if (is_group) {
        safe_copy(room_id, sizeof(room_id), from_is_room ? from : self_id);
        if (from_is_room) {
            extract_group_sender(content, sender_wxid, sizeof(sender_wxid));
            wxb_learn_self_wxid(self_id);
        } else {
            safe_copy(sender_wxid, sizeof(sender_wxid), from);
            wxb_learn_self_wxid(from);
        }
        safe_copy(peer_wxid, sizeof(peer_wxid), room_id);
        if (wxb_self_wxid()[0] && str_eq(sender_wxid, wxb_self_wxid())) {
            is_outgoing = 1;
            safe_copy(direction, sizeof(direction), "outgoing");
        } else if (sender_wxid[0]) {
            is_incoming = 1;
            safe_copy(direction, sizeof(direction), "incoming");
        }
    } else {
        if (is_wxid_like(from) && (!is_wxid_like(self_id) || str_eq(self_id, "filehelper"))) {
            wxb_learn_self_wxid(from);
            is_outgoing = 1;
            safe_copy(direction, sizeof(direction), "outgoing");
            safe_copy(sender_wxid, sizeof(sender_wxid), from);
            safe_copy(peer_wxid, sizeof(peer_wxid), self_id);
        } else if (wxb_self_wxid()[0] && str_eq(from, wxb_self_wxid())) {
            is_outgoing = 1;
            safe_copy(direction, sizeof(direction), "outgoing");
            safe_copy(sender_wxid, sizeof(sender_wxid), from);
            safe_copy(peer_wxid, sizeof(peer_wxid), self_id);
        } else if (wxb_self_wxid()[0] && str_eq(self_id, wxb_self_wxid())) {
            is_incoming = 1;
            safe_copy(direction, sizeof(direction), "incoming");
            safe_copy(sender_wxid, sizeof(sender_wxid), from);
            safe_copy(peer_wxid, sizeof(peer_wxid), from);
        } else if (!wxb_self_wxid()[0] && !is_wxid_like(from) && is_wxid_like(self_id)) {
            wxb_learn_self_wxid(self_id);
            is_incoming = 1;
            safe_copy(direction, sizeof(direction), "incoming");
            safe_copy(sender_wxid, sizeof(sender_wxid), from);
            safe_copy(peer_wxid, sizeof(peer_wxid), from);
        } else {
            /* With two plausible wxids and no primed account identity, guessing can
             * poison direction for the entire process. Preserve raw endpoints and
             * let the scoped backend ledger retain the event as pending instead. */
            safe_copy(sender_wxid, sizeof(sender_wxid), from);
        }
    }

    {
        const char *session_key = room_id[0] ? room_id : peer_wxid;
        ULONGLONG event_key = realtime_event_key(
            msg_id, local_id, msg_createtime, session_key, from, self_id);
        if ((LONG64)event_key == InterlockedCompareExchange64(&g_last_event_key, 0, 0)) return;
        InterlockedExchange64(&g_last_event_key, (LONG64)event_key);
    }

    /* Cold grab stays ahead of disk and network work, but after composite event
     * dedupe so server_id=0 and cross-session id reuse are not discarded. */
    if (hb_is_receive_candidate(msg_type_int, msg_subtype_int, content)) {
        hb_cold_try_claim_local_msg(msg_base); /* build only, no flush */
    }
    pm_claim = GetTickCount64();
    pm_retain = pm_claim;
    pm_revoke = pm_claim;
    pm_push = pm_claim;

    if (is_group && from_is_room) {
        schedule_auto_room_members(room_id, sender_wxid, "recv");
    }
    wxb_friend_auto_handle_message(msg_id, msg_type_int, is_incoming, content);

    /* write a lightweight debug marker every ~64 messages */
    static volatile LONG dbg_count = 0;
    LONG cur = InterlockedIncrement(&dbg_count);
    if ((cur & 7) == 0 || cur <= 3) {
        char mc[64];
        wsprintfA(mc, "proc_msg: count=%d last_type=%d\r\n", cur, msg_type_int);
        write_status(mc);
    }

    if (is_outgoing && str_eq(peer_wxid, "filehelper")) {
        cache_filehelper_outgoing_copy(msg_id, local_id, msg_type_int, msg_subtype_int,
                                       msg_createtime,
                                       content, sender_wxid, self_id, sig);
    }

    if (is_revocable_msg(msg_type_int) && is_incoming) {
        cache_msg(msg_id, local_id, msg_type_int, msg_subtype_int,
                  msg_createtime,
                  from, peer_wxid, self_id, content, lstrlenA(content),
                  sender_wxid, sig);
    }

    /* Diagnostic only — never block cold claim. Skip heavy dump when cold on. */
    if (!hb_cold_enabled()) {
        hb_log_receive_candidate(msg_base, id_s, local_id_s, msg_type_int, msg_subtype_int,
                                 from, self_id, peer_wxid, sender_wxid, direction,
                                 is_incoming, is_outgoing, content);
    }
    voice_probe_log_msg_model(msg_base, id_s, msg_type_int, msg_subtype_int, is_outgoing);

    /* Native forward (Phase 1): on the WeChat receive thread the live message-model is in
     * hand, so materialise + retain the REAL send_item now for any forwardable media type. This
     * is what lets image/video/file forwards carry their MD5/CDN linkage (impossible to hand-build).
     * Scope it deliberately so the (now 256-slot) media ring buffer is NOT churned out from under a
     * pending forward:
     *   - is_incoming only  -> our own forward echoes (outgoing) never evict retained media; the
     *                          bot forwards messages it RECEIVED, which is exactly is_incoming.
     *   - skip type 1 (text) -> text forwards fine hand-built (gate accepts it), so it must not
     *                          consume media slots; chat is overwhelmingly text.
     *   - skip system types  -> 0 / 10000 (sys) / 10002 (sysmsg) / 51 (status-notify) carry no media.
     * Net effect: a received image/video/file/app message stays retained across hundreds of
     * subsequent text messages, so it can be forwarded repeatedly, not just once. */
    if (is_incoming && msg_type_int != 0 && msg_type_int != 1 &&
        msg_type_int != 10000 && msg_type_int != 10002 && msg_type_int != 51) {
        retain_send_item(msg_id, msg_base);
        retain_model(msg_id, msg_base);   /* for the submit/dispatch forward path (appmsg cards/links) */
    }
    pm_retain = GetTickCount64();

    /* no-click receive experiment: cache the retained send_item for diagnostics/gated direct tests.
       This is not sufficient for auto-open; 185EA6140 only reaches receivewxhb without page_ctx. */
    if (is_incoming && hb_is_receive_candidate(msg_type_int, msg_subtype_int, content)) {
        void *red_si = find_retained_si(msg_id);
        if (red_si && r64(red_si) == (int64_t)wxb_weixin_module() + WX_OFF_SEND_ITEM_VT) {
            hb_noclick_remember_red_send_item((int64_t)red_si);
        }
    }

    int64_t revoke_orig_msgid = r64((void *)(msg_base + WX_MSG_REVOKE_ORIGID_OFF));
    handle_revoke_forward(content, msg_type_int, revoke_orig_msgid);
    pm_revoke = GetTickCount64();

    char json[REALTIME_ENVELOPE_BUFFER];
    char event_id[128];
    char *p = json;
    char *end = json + sizeof(json) - 1;
    char *content_end = end - REALTIME_JSON_TAIL_RESERVE;
    const char *event_session = room_id[0] ? room_id : (peer_wxid[0] ? peer_wxid : from);
    ULONGLONG session_hash = realtime_hash_string(event_session);
    int content_json_truncated;
    wsprintfA(event_id, "wechat-message:%016I64x:%s:%s:%s",
              session_hash, id_s, local_id_s, ts_s);
    append_raw(&p, end, "{\"event_id\":\""); append_escaped(&p, end, event_id);
    append_raw(&p, end, "\",\"event_type\":\"message\",\"source\":\"wx_hook_bridge\",\"schema_version\":1");
    append_raw(&p, end, ",\"message_id\":\""); append_escaped(&p, end, id_s);
    append_raw(&p, end, "\",\"local_id\":\""); append_escaped(&p, end, local_id_s);
    append_raw(&p, end, "\",\"message_type\":"); append_raw(&p, end, type_s);
    append_raw(&p, end, ",\"message_subtype\":"); append_raw(&p, end, subtype_s);
    append_raw(&p, end, ",\"timestamp_unix\":"); append_raw(&p, end, ts_s);
    append_raw(&p, end, ",\"direction\":\""); append_escaped(&p, end, direction);
    append_raw(&p, end, "\",\"is_incoming\":"); append_raw(&p, end, is_incoming ? "true" : "false");
    append_raw(&p, end, ",\"is_outgoing\":"); append_raw(&p, end, is_outgoing ? "true" : "false");
    append_raw(&p, end, ",\"peer_id\":\""); append_escaped(&p, end, peer_wxid);
    append_raw(&p, end, "\",\"room_id\":\""); append_escaped(&p, end, room_id);
    append_raw(&p, end, "\",\"sender_id\":\""); append_escaped(&p, end, sender_wxid);
    append_raw(&p, end, "\",\"self_id\":\""); append_escaped(&p, end, wxb_self_wxid());
    append_raw(&p, end, "\",\"raw_from\":\""); append_escaped(&p, end, from);
    append_raw(&p, end, "\",\"raw_to\":\""); append_escaped(&p, end, self_id);
    append_raw(&p, end, "\",\"content\":\"");
    content_json_truncated = realtime_append_escaped_bounded(&p, content_end, content);
    append_raw(&p, end, "\",\"signature\":\""); append_escaped(&p, end, sig);
    append_raw(&p, end, "\",\"content_truncated\":");
    append_raw(&p, end, (content_source_truncated || content_json_truncated) ? "true" : "false");
    append_raw(&p, end, ",\"content_original_bytes\":");
    {
        char original_size_s[32];
        i64toa10(content_original_bytes > 0 ? content_original_bytes : content_read, original_size_s);
        append_raw(&p, end, original_size_s);
    }
    append_raw(&p, end, ",\"self_identity_known\":");
    append_raw(&p, end, wxb_self_wxid_known() ? "true" : "false");
    /* Legacy aliases remain in the same envelope for older bridge consumers. */
    append_raw(&p, end, ",\"msgId\":\""); append_escaped(&p, end, id_s);
    append_raw(&p, end, "\",\"localId\":\""); append_escaped(&p, end, local_id_s);
    append_raw(&p, end, "\",\"msgLocalId\":\""); append_escaped(&p, end, local_id_s);
    append_raw(&p, end, "\",\"type\":\""); append_escaped(&p, end, type_s);
    append_raw(&p, end, "\",\"subType\":\""); append_escaped(&p, end, subtype_s);
    append_raw(&p, end, "\",\"subtype\":\""); append_escaped(&p, end, subtype_s);
    append_raw(&p, end, "\",\"timestamp\":\""); append_escaped(&p, end, ts_s);
    append_raw(&p, end, "\",\"from\":\""); append_escaped(&p, end, from);
    append_raw(&p, end, "\",\"fromWxId\":\""); append_escaped(&p, end, self_id);
    append_raw(&p, end, "\",\"rawFrom\":\""); append_escaped(&p, end, from);
    append_raw(&p, end, "\",\"rawTo\":\""); append_escaped(&p, end, self_id);
    append_raw(&p, end, "\",\"isSendMsg\":"); append_raw(&p, end, is_outgoing ? "true" : "false");
    append_raw(&p, end, ",\"isIncoming\":"); append_raw(&p, end, is_incoming ? "true" : "false");
    append_raw(&p, end, ",\"peerWxId\":\""); append_escaped(&p, end, peer_wxid);
    append_raw(&p, end, "\",\"roomId\":\""); append_escaped(&p, end, room_id);
    append_raw(&p, end, "\",\"senderWxId\":\""); append_escaped(&p, end, sender_wxid);
    append_raw(&p, end, "\",\"selfWxId\":\""); append_escaped(&p, end, wxb_self_wxid());
    append_raw(&p, end, "\"}");
    *p = 0;
    int pushed = wxb_realtime_relay_enqueue(event_id, json, (DWORD)(p - json)) ? -2 : -3;
    pm_push = GetTickCount64();
    {
        char from_short[96], self_short[96], peer_short[96], sender_short[96], dbg[768];
        safe_copy(from_short, sizeof(from_short), from);
        safe_copy(self_short, sizeof(self_short), self_id);
        safe_copy(peer_short, sizeof(peer_short), peer_wxid);
        safe_copy(sender_short, sizeof(sender_short), sender_wxid);
        wsprintfA(dbg,
                  "recv: id=%s local=%s type=%d/%d from=%s to=%s peer=%s sender=%s dir=%s in=%d out=%d content_len=%d sig_len=%d push=%d\r\n",
                  id_s, local_id_s, msg_type_int, msg_subtype_int,
                  from_short, self_short, peer_short, sender_short, direction,
                  is_incoming, is_outgoing, lstrlenA(content), lstrlenA(sig), pushed);
        append_file_bytes(RECV_LOG, dbg, (DWORD)lstrlenA(dbg));
    }

    if (hb_cold_enabled()) {
        ULONGLONG pm_end = GetTickCount64();
        ULONGLONG pm_dt = pm_end - pm_t0;
        if (pm_dt > 100) {
            char tdbg[256];
            wsprintfA(tdbg,
                      "[procmsg-time] tid=%lu type=%d total=%lums | reads+claim=%lums retain=%lums revoke=%lums json+push=%lums tail=%lums\r\n",
                      (unsigned long)GetCurrentThreadId(), msg_type_int, (unsigned long)pm_dt,
                      (unsigned long)(pm_claim - pm_t0),
                      (unsigned long)(pm_retain - pm_claim),
                      (unsigned long)(pm_revoke - pm_retain),
                      (unsigned long)(pm_push - pm_revoke),
                      (unsigned long)(pm_end - pm_push));
            hb_dbg(tdbg);
        }
    }
}

static int build_trampoline(BYTE *p, int64_t return_addr) {
    BYTE *start = p;
    BYTE save[] = {
        0x9C,                         /* pushfq */
        0x50,0x51,0x52,               /* push rax, rcx, rdx */
        0x41,0x50,0x41,0x51,          /* push r8, r9 */
        0x41,0x52,0x41,0x53,          /* push r10, r11 */
        0x48,0x81,0xEC,0x80,0x00,0x00,0x00, /* sub rsp, 0x80 */
        0xF3,0x0F,0x7F,0x44,0x24,0x20, /* movdqu [rsp+0x20], xmm0 */
        0xF3,0x0F,0x7F,0x4C,0x24,0x30, /* movdqu [rsp+0x30], xmm1 */
        0xF3,0x0F,0x7F,0x54,0x24,0x40, /* movdqu [rsp+0x40], xmm2 */
        0xF3,0x0F,0x7F,0x5C,0x24,0x50, /* movdqu [rsp+0x50], xmm3 */
        0xF3,0x0F,0x7F,0x64,0x24,0x60, /* movdqu [rsp+0x60], xmm4 */
        0xF3,0x0F,0x7F,0x6C,0x24,0x70  /* movdqu [rsp+0x70], xmm5 */
    };
    copy_bytes(p, save, sizeof(save)); p += sizeof(save);

    BYTE load[] = {
        0x48,0x8B,0x46,0x18,          /* mov rax, [rsi+0x18] */
        0x48,0x85,0xC0,               /* test rax, rax */
        0x74,0x0F,                    /* jz +15 */
        0x48,0x89,0xC1,               /* mov rcx, rax */
        0x48,0xB8                     /* mov rax, imm64 */
    };
    copy_bytes(p, load, sizeof(load)); p += sizeof(load);
    *(int64_t *)p = (int64_t)process_message; p += 8;
    *p++ = 0xFF; *p++ = 0xD0;          /* call rax */

    BYTE restore[] = {
        0xF3,0x0F,0x6F,0x44,0x24,0x20, /* movdqu xmm0, [rsp+0x20] */
        0xF3,0x0F,0x6F,0x4C,0x24,0x30, /* movdqu xmm1, [rsp+0x30] */
        0xF3,0x0F,0x6F,0x54,0x24,0x40, /* movdqu xmm2, [rsp+0x40] */
        0xF3,0x0F,0x6F,0x5C,0x24,0x50, /* movdqu xmm3, [rsp+0x50] */
        0xF3,0x0F,0x6F,0x64,0x24,0x60, /* movdqu xmm4, [rsp+0x60] */
        0xF3,0x0F,0x6F,0x6C,0x24,0x70, /* movdqu xmm5, [rsp+0x70] */
        0x48,0x81,0xC4,0x80,0x00,0x00,0x00,
        0x41,0x5B,0x41,0x5A,0x41,0x59,0x41,0x58,
        0x5A,0x59,0x58,
        0x9D
    };
    copy_bytes(p, restore, sizeof(restore)); p += sizeof(restore);

    copy_bytes(p, g_receive_hook_state.orig_doaddmsg, 7); p += 7;
    *p++ = 0x48; *p++ = 0xB8;
    *(int64_t *)p = return_addr; p += 8;
    *p++ = 0xFF; *p++ = 0xE0;
    return (int)(p - start);
}

void install_recv_hook(void) {
    if (g_receive_hook_state.recv_installed || !wxb_weixin_module()) return;

    BYTE expected[7] = {0x48,0x89,0x85,0xF8,0x01,0x00,0x00};
    BYTE *target = (BYTE *)((int64_t)wxb_weixin_module() + WX_OFF_DOADDMSG);
    int mismatch = 0;
    for (int i = 0; i < 7; i++) {
        if (target[i] != expected[i]) mismatch = 1;
    }
    if (mismatch) {
        /* restore original bytes (may have been corrupted by prev trampoline) */
        DWORD oldp = 0;
        VirtualProtect(target, 7, PAGE_EXECUTE_READWRITE, &oldp);
        copy_bytes(target, expected, 7);
        VirtualProtect(target, 7, oldp, &oldp);
        FlushInstructionCache(GetCurrentProcess(), target, 7);
        write_status("DoAddMsg bytes restored\n");
    }

    copy_bytes(g_receive_hook_state.orig_doaddmsg, target, 7);
    g_receive_hook_state.recv_trampoline = alloc_near(target, 256);
    if (!g_receive_hook_state.recv_trampoline) {
        write_status("alloc_near failed; receive hook not installed\n");
        return;
    }

    int tramp_len = build_trampoline((BYTE *)g_receive_hook_state.recv_trampoline, (int64_t)(target + 7));
    FlushInstructionCache(GetCurrentProcess(), g_receive_hook_state.recv_trampoline, tramp_len);

    int64_t rel64 = (int64_t)g_receive_hook_state.recv_trampoline - ((int64_t)target + 5);
    if (rel64 < INT32_MIN || rel64 > INT32_MAX) {
        write_status("trampoline out of rel32 range\n");
        VirtualFree(g_receive_hook_state.recv_trampoline, 0, MEM_RELEASE);
        g_receive_hook_state.recv_trampoline = NULL;
        return;
    }

    BYTE patch[7];
    patch[0] = 0xE9;
    *(int32_t *)(patch + 1) = (int32_t)rel64;
    patch[5] = 0x90;
    patch[6] = 0x90;

    DWORD old = 0;
    VirtualProtect(target, 7, PAGE_EXECUTE_READWRITE, &old);
    copy_bytes(target, patch, 7);
    VirtualProtect(target, 7, old, &old);
    FlushInstructionCache(GetCurrentProcess(), target, 7);
    InterlockedExchange(&g_receive_hook_state.recv_installed, 1);
    write_status("bridge hook active\n");
}

void install_async_queue_hook(void) {
    if (g_receive_hook_state.async_queue_installed || !wxb_weixin_module()) return;

    BYTE *target = (BYTE *)((int64_t)wxb_weixin_module() + WX_OFF_QUEUE_HOOK);
    if (target[0] == 0x48 && target[1] == 0xB8) {
        DWORD oldp = 0;
        VirtualProtect(target, sizeof(WX_ORIG_QUEUE_HOOK_BYTES), PAGE_EXECUTE_READWRITE, &oldp);
        copy_bytes(target, WX_ORIG_QUEUE_HOOK_BYTES, sizeof(WX_ORIG_QUEUE_HOOK_BYTES));
        VirtualProtect(target, sizeof(WX_ORIG_QUEUE_HOOK_BYTES), oldp, &oldp);
        FlushInstructionCache(GetCurrentProcess(), target, sizeof(WX_ORIG_QUEUE_HOOK_BYTES));
        write_status("async queue stale hook restored\n");
    }

    copy_bytes(g_receive_hook_state.orig_queue_hook, target, 18);
    g_receive_hook_state.queue_trampoline = VirtualAlloc(NULL, 512, MEM_COMMIT | MEM_RESERVE, PAGE_EXECUTE_READWRITE);
    if (!g_receive_hook_state.queue_trampoline) {
        write_status("async queue hook alloc failed\n");
        return;
    }

    BYTE prefix[] = {
        0x53,                         /* push rbx */
        0x48,0x89,0xCB,               /* mov rbx, rcx */
        0x48,0x83,0xC3,0x20,          /* add rbx, 0x20 */
        0x48,0x8B,0x1B,               /* mov rbx, [rbx] */
        0x48,0x81,0xFB,0x94,0x01,0x00,0x00, /* cmp rbx, 0x194 */
        0x75,0x0D,                    /* jne original_path */
        0x5B,                         /* pop rbx */
        0x53,0x55,0x56,               /* push rbx/rbp/rsi */
        0x90,
        0xFF,0x51,0x60,               /* call qword ptr [rcx+0x60] */
        0x90,
        0x5E,0x5D,0x5B,               /* pop rsi/rbp/rbx */
        0xC3,                         /* ret */
        0x5B                          /* original_path: pop rbx */
    };
    BYTE *p = (BYTE *)g_receive_hook_state.queue_trampoline;
    copy_bytes(p, prefix, sizeof(prefix)); p += sizeof(prefix);
    copy_bytes(p, g_receive_hook_state.orig_queue_hook, 18); p += 18;
    *p++ = 0x50;                      /* push rax */
    *p++ = 0x48; *p++ = 0xB8;         /* mov rax, imm64 */
    *(int64_t *)p = (int64_t)(target + 12); p += 8;
    *p++ = 0xFF; *p++ = 0xE0;         /* jmp rax */
    FlushInstructionCache(GetCurrentProcess(), g_receive_hook_state.queue_trampoline, (SIZE_T)(p - (BYTE *)g_receive_hook_state.queue_trampoline));

    BYTE patch[18];
    for (int i = 0; i < 18; i++) patch[i] = 0x90;
    patch[0] = 0x48; patch[1] = 0xB8;
    *(int64_t *)(patch + 2) = (int64_t)g_receive_hook_state.queue_trampoline;
    patch[10] = 0xFF; patch[11] = 0xE0;
    patch[12] = 0x58;

    DWORD old = 0;
    VirtualProtect(target, 18, PAGE_EXECUTE_READWRITE, &old);
    copy_bytes(target, patch, 18);
    VirtualProtect(target, 18, old, &old);
    FlushInstructionCache(GetCurrentProcess(), target, 18);
    InterlockedExchange(&g_receive_hook_state.async_queue_installed, 1);
}


void uninstall_async_queue_hook(void) {
    if (!g_receive_hook_state.async_queue_installed || !wxb_weixin_module()) return;
    BYTE *target = (BYTE *)((int64_t)wxb_weixin_module() + WX_OFF_QUEUE_HOOK);
    DWORD old = 0;
    VirtualProtect(target, 18, PAGE_EXECUTE_READWRITE, &old);
    copy_bytes(target, g_receive_hook_state.orig_queue_hook, 18);
    VirtualProtect(target, 18, old, &old);
    FlushInstructionCache(GetCurrentProcess(), target, 18);
    InterlockedExchange(&g_receive_hook_state.async_queue_installed, 0);
    if (g_receive_hook_state.queue_trampoline) {
        VirtualFree(g_receive_hook_state.queue_trampoline, 0, MEM_RELEASE);
        g_receive_hook_state.queue_trampoline = NULL;
    }
}

void uninstall_recv_hook(void) {
    set_anti_revoke(0);
    clear_room_task();
    wxb_hb_cold_shutdown();
    uninstall_async_queue_hook();
    uninstall_forward_ui_entry_trace_hook();
    uninstall_forward_submit_trace_hook();
    uninstall_forward_dispatch_hook();
    uninstall_hb_setter_hook();
    uninstall_hb_pagectx_hook();
    uninstall_hb_null_guard_hook();
    uninstall_hb_route_hook();
    uninstall_hb_route_dispatcher_hook();
    uninstall_hb_route_temp_vector_hook();
    uninstall_hb_route_vector_builder_hook();
    uninstall_hb_route_ctx_factory_hook();
    uninstall_hb_route_classifier_hook();
    uninstall_hb_receive_route_builder_hook();
    uninstall_hb_receive_mid_builder_hook();
    uninstall_hb_task_factory_hook();
    uninstall_hb_task_factory_inner_hook();
    uninstall_hb_receive_task_builder_hook();
    uninstall_hb_parser_hook();
    uninstall_hb_source_submit_hook();
    uninstall_hb_sentinel_hooks();
    uninstall_voice_probe_hook();
    uninstall_a8key_dispatch_hook();
    uninstall_sns_media_probe_hook();
    uninstall_sns_upload_trace_hooks();
    uninstall_sns_publish_trace_hooks();
    if (!g_receive_hook_state.recv_installed || !wxb_weixin_module()) return;
    BYTE *target = (BYTE *)((int64_t)wxb_weixin_module() + WX_OFF_DOADDMSG);
    DWORD old = 0;
    VirtualProtect(target, 7, PAGE_EXECUTE_READWRITE, &old);
    copy_bytes(target, g_receive_hook_state.orig_doaddmsg, 7);
    VirtualProtect(target, 7, old, &old);
    FlushInstructionCache(GetCurrentProcess(), target, 7);
    InterlockedExchange(&g_receive_hook_state.recv_installed, 0);
    write_status("bridge hook stopped\n");
}

int recv_hook_installed(void) {
    return InterlockedCompareExchange(&g_receive_hook_state.recv_installed, 0, 0) != 0;
}

int async_queue_hook_installed(void) {
    return InterlockedCompareExchange(&g_receive_hook_state.async_queue_installed, 0, 0) != 0;
}
