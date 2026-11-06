/*
 * Module: message cache, retained models/send-items, revoke notices, and revoke forward jobs.
 */

#include "../include/wx_bridge_revoke_cache.h"
#include "../include/wx_bridge_revoke_forward.h"
#include "../include/wx_bridge_contact_lookup.h"
#include "../include/wx_bridge_debug_log.h"
#include "../include/wx_bridge_file_io.h"
#include "../include/wx_bridge_forward_protocol.h"
#include "../include/wx_bridge_forward_send.h"
#include "../include/wx_bridge_memory.h"
#include "../include/wx_bridge_paths.h"
#include "../include/wx_bridge_protocol.h"
#include "../include/wx_bridge_revoke_protocol.h"
#include "../include/wx_bridge_room_contacts.h"
#include "../include/wx_bridge_send_api.h"
#include "../include/wx_bridge_runtime.h"
#include "../include/wx_bridge_text.h"

#define MSG_CACHE_SIZE 512
#define FILEHELPER_COPY_CACHE_SIZE 128
#define REVOKE_PENDING_QUOTE_SIZE 16
#define REVOKE_FORWARD_JOB_SIZE 16
#define REVOKE_NOTICE_DEDUPE_SIZE 32

typedef struct CachedMsg {
    int64_t msgid;
    int32_t local_id;
    int32_t msg_type;
    int32_t msg_subtype;
    int32_t createtime;
    char from[256];
    char peer[256];
    char self[256];
    char content[4096];
    char msgsource[1024];
    char sender[256];
    int content_len;
    volatile LONG used;
} CachedMsg;

typedef struct FileHelperCopyMsg {
    int64_t msgid;
    int64_t source_orig_msgid;
    int32_t local_id;
    int32_t msg_type;
    int32_t msg_subtype;
    int32_t createtime;
    ULONGLONG tick;
    char content[4096];
    char sender[256];
    char self[256];
    char msgsource[1024];
    volatile LONG used;
} FileHelperCopyMsg;

typedef struct PendingRevokeQuote {
    int64_t orig_msgid;
    int32_t msg_type;
    int32_t msg_subtype;
    int allow_markerless_text_copy;
    ULONGLONG start_tick;
    char orig_content[4096];
    char notice[2048];
    volatile LONG used;
} PendingRevokeQuote;

typedef struct RevokeForwardJob {
    int64_t orig_msgid;
    int64_t revoketime;
    char talker[512];
    char source_tag[64];
    volatile LONG state; /* 0=free, 1=queued, 2=running */
} RevokeForwardJob;

typedef struct RevokeCacheState {
    CachedMsg msg_cache[MSG_CACHE_SIZE];
    volatile LONG msg_cache_pos;
    FileHelperCopyMsg filehelper_copy_cache[FILEHELPER_COPY_CACHE_SIZE];
    volatile LONG filehelper_copy_pos;
    PendingRevokeQuote revoke_pending_quotes[REVOKE_PENDING_QUOTE_SIZE];
    volatile LONG revoke_pending_quote_pos;
    RevokeForwardJob revoke_forward_jobs[REVOKE_FORWARD_JOB_SIZE];
    volatile LONG revoke_forward_job_pos;
    volatile LONG64 revoke_notice_ids[REVOKE_NOTICE_DEDUPE_SIZE];
    volatile LONG revoke_notice_pos;
} RevokeCacheState;

static RevokeCacheState g_revoke_cache_state = {0};

void cache_msg(int64_t msgid, int32_t local_id, int32_t msg_type, int32_t msg_subtype,
                       int32_t createtime, const char *from, const char *peer,
                       const char *self_id, const char *content, int content_len,
                       const char *sender, const char *msgsource) {
    int pos = InterlockedIncrement(&g_revoke_cache_state.msg_cache_pos) % MSG_CACHE_SIZE;
    CachedMsg *e = &g_revoke_cache_state.msg_cache[pos];
    e->msgid = msgid;
    e->local_id = local_id;
    e->msg_type = msg_type;
    e->msg_subtype = msg_subtype;
    e->createtime = createtime;
    safe_copy(e->from, sizeof(e->from), from ? from : "");
    safe_copy(e->peer, sizeof(e->peer), peer ? peer : "");
    safe_copy(e->self, sizeof(e->self), self_id ? self_id : "");
    safe_copy(e->sender, sizeof(e->sender), sender ? sender : "");
    safe_copy(e->msgsource, sizeof(e->msgsource), msgsource ? msgsource : "");
    if (content && content_len > 0) {
        int n = content_len < (int)sizeof(e->content) - 1 ? content_len : (int)sizeof(e->content) - 1;
        __try {
            for (int i = 0; i < n; i++) e->content[i] = content[i];
            e->content[n] = 0;
            e->content_len = n;
        } __except (EXCEPTION_EXECUTE_HANDLER) {
            e->content[0] = 0;
            e->content_len = 0;
        }
    } else {
        e->content[0] = 0;
        e->content_len = 0;
    }
    InterlockedExchange(&e->used, 1);
    {
        WCHAR path[1024];
        char id_s[32];
        i64toa10(msgid, id_s);
        wsprintfW(path, L"%s\\%S.bin", REVOKE_CACHE_DIR, id_s);
        write_file_bytes(path, (const char *)e, (DWORD)sizeof(*e));
    }
}

static CachedMsg *load_cached_msg_from_file(int64_t msgid) {
    WCHAR path[1024];
    char id_s[32];
    char raw[sizeof(CachedMsg) + 1];
    int n;
    int pos;
    CachedMsg *e;
    if (!msgid) return NULL;
    i64toa10(msgid, id_s);
    wsprintfW(path, L"%s\\%S.bin", REVOKE_CACHE_DIR, id_s);
    n = read_file_bytes(path, raw, sizeof(raw));
    if (n != (int)sizeof(CachedMsg)) return NULL;
    pos = InterlockedIncrement(&g_revoke_cache_state.msg_cache_pos) % MSG_CACHE_SIZE;
    e = &g_revoke_cache_state.msg_cache[pos];
    InterlockedExchange(&e->used, 0);
    copy_bytes(e, raw, (int)sizeof(CachedMsg));
    if (e->msgid != msgid || InterlockedCompareExchange(&e->used, 0, 0) != 1) {
        InterlockedExchange(&e->used, 0);
        return NULL;
    }
    return e;
}

static CachedMsg *find_cached_msg(int64_t msgid) {
    for (int i = 0; i < MSG_CACHE_SIZE; i++) {
        if (g_revoke_cache_state.msg_cache[i].used && g_revoke_cache_state.msg_cache[i].msgid == msgid) {
            return &g_revoke_cache_state.msg_cache[i];
        }
    }
    return load_cached_msg_from_file(msgid);
}

static int cached_msg_matches_revoke_talker(const CachedMsg *e, const char *talker) {
    if (!e || !e->msgid || !is_revocable_msg(e->msg_type)) return 0;
    if (InterlockedCompareExchange((volatile LONG *)&e->used, 0, 0) != 1) return 0;
    if (!talker || !talker[0]) return 1;
    return str_eq(talker, e->peer) ||
           str_eq(talker, e->from) ||
           str_eq(talker, e->sender);
}

static CachedMsg *store_loaded_cached_msg(const CachedMsg *src) {
    int pos;
    CachedMsg *e;
    if (!src || !src->msgid) return NULL;
    pos = InterlockedIncrement(&g_revoke_cache_state.msg_cache_pos) % MSG_CACHE_SIZE;
    e = &g_revoke_cache_state.msg_cache[pos];
    InterlockedExchange(&e->used, 0);
    copy_bytes(e, src, (int)sizeof(CachedMsg));
    InterlockedExchange(&e->used, 1);
    return e;
}

static CachedMsg *find_recent_cached_revocable_msg_on_disk(const char *talker) {
    WCHAR pattern[1024];
    WCHAR path[1024];
    WIN32_FIND_DATAW fd;
    HANDLE h;
    FILETIME best_time = {0, 0};
    CachedMsg best;
    int found = 0;
    ZeroMemory(&best, sizeof(best));
    wsprintfW(pattern, L"%s\\*.bin", REVOKE_CACHE_DIR);
    h = FindFirstFileW(pattern, &fd);
    if (h == INVALID_HANDLE_VALUE) return NULL;
    do {
        char raw[sizeof(CachedMsg) + 1];
        int n;
        CachedMsg *candidate;
        if (fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) continue;
        wsprintfW(path, L"%s\\%s", REVOKE_CACHE_DIR, fd.cFileName);
        n = read_file_bytes(path, raw, (DWORD)sizeof(raw));
        if (n != (int)sizeof(CachedMsg)) continue;
        candidate = (CachedMsg *)raw;
        if (!cached_msg_matches_revoke_talker(candidate, talker)) continue;
        if (!found || CompareFileTime(&fd.ftLastWriteTime, &best_time) > 0) {
            copy_bytes(&best, candidate, (int)sizeof(CachedMsg));
            best_time = fd.ftLastWriteTime;
            found = 1;
        }
    } while (FindNextFileW(h, &fd));
    FindClose(h);
    return found ? store_loaded_cached_msg(&best) : NULL;
}

static CachedMsg *find_recent_cached_revocable_msg(const char *talker) {
    LONG cur = InterlockedCompareExchange(&g_revoke_cache_state.msg_cache_pos, 0, 0);
    for (int i = 0; i < MSG_CACHE_SIZE; i++) {
        int slot = (int)((cur - i) % MSG_CACHE_SIZE);
        CachedMsg *e;
        if (slot < 0) slot += MSG_CACHE_SIZE;
        e = &g_revoke_cache_state.msg_cache[slot];
        if (cached_msg_matches_revoke_talker(e, talker)) return e;
    }
    return find_recent_cached_revocable_msg_on_disk(talker);
}

static int copy_cached_msg_snapshot(const CachedMsg *src, RevokeCachedMsgSnapshot *out) {
    int64_t msgid;
    if (!src || !out) return 0;
    if (InterlockedCompareExchange((volatile LONG *)&src->used, 0, 0) != 1) return 0;
    msgid = src->msgid;
    out->msgid = src->msgid;
    out->local_id = src->local_id;
    out->msg_type = src->msg_type;
    out->msg_subtype = src->msg_subtype;
    out->createtime = src->createtime;
    safe_copy(out->from, sizeof(out->from), src->from);
    safe_copy(out->peer, sizeof(out->peer), src->peer);
    safe_copy(out->self, sizeof(out->self), src->self);
    safe_copy(out->content, sizeof(out->content), src->content);
    safe_copy(out->msgsource, sizeof(out->msgsource), src->msgsource);
    safe_copy(out->sender, sizeof(out->sender), src->sender);
    out->content_len = src->content_len;
    return msgid == src->msgid &&
           InterlockedCompareExchange((volatile LONG *)&src->used, 0, 0) == 1;
}

int find_cached_msg_snapshot(int64_t msgid, RevokeCachedMsgSnapshot *out) {
    return copy_cached_msg_snapshot(find_cached_msg(msgid), out);
}

int find_recent_cached_revocable_msg_snapshot(const char *talker,
                                              RevokeCachedMsgSnapshot *out) {
    return copy_cached_msg_snapshot(find_recent_cached_revocable_msg(talker), out);
}

static const char *strip_group_sender_prefix(const char *s) {
    const char *p;
    if (!s || !*s) return "";
    p = s;
    while (*p && *p != '\n') p++;
    if (*p == '\n') {
        const char *colon = p;
        while (colon > s && *colon != ':') colon--;
        if (*colon == ':' && colon < p) return p + 1;
    }
    return s;
}

static int forward_copy_content_match(const char *orig, const char *copy) {
    const char *orig_body;
    const char *copy_body;
    if (!orig || !orig[0]) return 1;
    if (!copy) copy = "";
    if (str_eq(orig, copy)) return 1;
    orig_body = strip_group_sender_prefix(orig);
    copy_body = strip_group_sender_prefix(copy);
    if (orig_body != orig && str_eq(orig_body, copy)) return 1;
    if (copy_body != copy && str_eq(orig, copy_body)) return 1;
    if (str_eq(orig_body, copy_body)) return 1;
    return 0;
}

static int pending_matches_filehelper_copy(PendingRevokeQuote *pending,
                                           const FileHelperCopyMsg *copy) {
    int pending_subtype;
    int copy_subtype;
    if (!pending || !copy) return 0;
    if (copy->source_orig_msgid) {
        if (copy->source_orig_msgid != pending->orig_msgid) return 0;
    } else {
        if (!pending->allow_markerless_text_copy) return 0;
        if (pending->msg_type != 1 || copy->msg_type != 1) return 0;
        if (copy->tick < pending->start_tick) return 0;
    }
    if (InterlockedCompareExchange(&pending->used, 0, 0) != 1 ||
        InterlockedCompareExchange((volatile LONG *)&copy->used, 0, 0) != 1) return 0;
    if (!copy->msgid || copy->msgid == pending->orig_msgid) return 0;
    if (copy->tick + 1000ULL < pending->start_tick) return 0;
    if (copy->tick > pending->start_tick + 60000ULL) return 0;
    if (copy->msg_type != pending->msg_type) return 0;
    pending_subtype = forward_effective_subtype_for_content(
        pending->msg_type, pending->msg_subtype, pending->orig_content);
    copy_subtype = forward_effective_subtype_for_content(
        copy->msg_type, copy->msg_subtype, copy->content);
    if (pending_subtype != copy_subtype) return 0;
    if (pending->msg_type != 1) return 1;
    return forward_copy_content_match(pending->orig_content, copy->content);
}

static int quote_pending_with_filehelper_copy(PendingRevokeQuote *pending,
                                              const FileHelperCopyMsg *copy,
                                              const char *match_source) {
    const char *quote_content;
    const char *quote_sender;
    const char *source_to;
    int32_t createtime;
    int ok;
    char dbg[512];
    if (!pending_matches_filehelper_copy(pending, copy)) return 0;
    if (InterlockedCompareExchange(&pending->used, 0, 1) != 1) return 0;

    quote_content = copy->content[0] ? copy->content : "[非文本消息]";
    quote_sender = copy->sender[0] ? copy->sender : wxb_self_wxid();
    source_to = copy->self[0] ? copy->self : "filehelper";
    createtime = copy->createtime ? copy->createtime : (int32_t)time(NULL);
    ok = send_quote_v2("filehelper",
                       pending->notice,
                       copy->msgid,
                       copy->local_id,
                       quote_content,
                       quote_sender,
                       copy->msg_type,
                       createtime,
                       "filehelper",
                       copy->msgsource,
                       source_to);
    wsprintfA(dbg,
              "revoke_notice_quote_copy: orig=%I64d copy=%I64d source_orig=%I64d local=%d ok=%d source=%s\r\n",
              (long long)pending->orig_msgid,
              (long long)copy->msgid,
              (long long)copy->source_orig_msgid,
              (int)copy->local_id,
              ok,
              match_source ? match_source : "");
    append_file_bytes(REVOKE_LOG, dbg, (DWORD)lstrlenA(dbg));
    write_status(ok ? "revoke notice quoted forwarded copy" : "revoke notice copy quote failed");
    return ok;
}

static int try_quote_pending_revoke_with_copy(const FileHelperCopyMsg *copy) {
    int ok = 0;
    for (int i = 0; i < REVOKE_PENDING_QUOTE_SIZE; i++) {
        if (InterlockedCompareExchange(&g_revoke_cache_state.revoke_pending_quotes[i].used, 0, 0) == 1 &&
            copy->tick > g_revoke_cache_state.revoke_pending_quotes[i].start_tick + 60000ULL) {
            InterlockedCompareExchange(&g_revoke_cache_state.revoke_pending_quotes[i].used, 0, 1);
        }
        if (quote_pending_with_filehelper_copy(&g_revoke_cache_state.revoke_pending_quotes[i], copy, "new_copy")) {
            ok = 1;
        }
    }
    return ok;
}

static void clear_pending_revoke_quote(int64_t orig_msgid) {
    if (!orig_msgid) return;
    for (int i = 0; i < REVOKE_PENDING_QUOTE_SIZE; i++) {
        if (InterlockedCompareExchange(&g_revoke_cache_state.revoke_pending_quotes[i].used, 0, 0) == 1 &&
            g_revoke_cache_state.revoke_pending_quotes[i].orig_msgid == orig_msgid) {
            InterlockedExchange(&g_revoke_cache_state.revoke_pending_quotes[i].used, 0);
        }
    }
}

static int add_pending_revoke_quote(CachedMsg *orig, const char *notice, ULONGLONG start_tick,
                                    int allow_markerless_text_copy) {
    int pos;
    PendingRevokeQuote *pending;
    char dbg[512];
    if (!orig || !notice || !notice[0]) return 0;
    pos = InterlockedIncrement(&g_revoke_cache_state.revoke_pending_quote_pos) % REVOKE_PENDING_QUOTE_SIZE;
    pending = &g_revoke_cache_state.revoke_pending_quotes[pos];
    InterlockedExchange(&pending->used, 0);
    pending->orig_msgid = orig->msgid;
    pending->msg_type = orig->msg_type;
    pending->msg_subtype = orig->msg_subtype;
    pending->allow_markerless_text_copy = allow_markerless_text_copy ? 1 : 0;
    pending->start_tick = start_tick;
    safe_copy(pending->orig_content, sizeof(pending->orig_content), orig->content);
    safe_copy(pending->notice, sizeof(pending->notice), notice);
    InterlockedExchange(&pending->used, 1);

    wsprintfA(dbg, "revoke_notice_pending_quote: orig=%I64d slot=%d type=%d/%d markerless_text=%d\r\n",
              (long long)orig->msgid, pos, (int)orig->msg_type, (int)orig->msg_subtype,
              allow_markerless_text_copy ? 1 : 0);
    append_file_bytes(REVOKE_LOG, dbg, (DWORD)lstrlenA(dbg));

    for (int i = 0; i < FILEHELPER_COPY_CACHE_SIZE; i++) {
        if (quote_pending_with_filehelper_copy(pending, &g_revoke_cache_state.filehelper_copy_cache[i], "existing_copy")) {
            return 1;
        }
    }
    return 0;
}

void cache_filehelper_outgoing_copy(int64_t msgid, int32_t local_id,
                                           int32_t msg_type, int32_t msg_subtype,
                                           int32_t createtime, const char *content,
                                           const char *sender, const char *self_id,
                                           const char *msgsource) {
    int pos;
    FileHelperCopyMsg *copy;
    char dbg[384];
    if (!msgid || !local_id) return;
    pos = InterlockedIncrement(&g_revoke_cache_state.filehelper_copy_pos) % FILEHELPER_COPY_CACHE_SIZE;
    copy = &g_revoke_cache_state.filehelper_copy_cache[pos];
    InterlockedExchange(&copy->used, 0);
    copy->msgid = msgid;
    copy->local_id = local_id;
    copy->msg_type = msg_type;
    copy->msg_subtype = msg_subtype;
    copy->source_orig_msgid = parse_forward_source_orig_msgid(msgsource);
    copy->createtime = createtime;
    copy->tick = GetTickCount64();
    safe_copy(copy->content, sizeof(copy->content), content ? content : "");
    safe_copy(copy->sender, sizeof(copy->sender), sender ? sender : "");
    safe_copy(copy->self, sizeof(copy->self), self_id ? self_id : "");
    safe_copy(copy->msgsource, sizeof(copy->msgsource), msgsource ? msgsource : "");
    InterlockedExchange(&copy->used, 1);

    wsprintfA(dbg, "filehelper_out_copy: msgid=%I64d source_orig=%I64d local=%d slot=%d type=%d/%d\r\n",
              (long long)msgid, (long long)copy->source_orig_msgid,
              (int)local_id, pos, (int)msg_type, (int)msg_subtype);
    append_file_bytes(REVOKE_LOG, dbg, (DWORD)lstrlenA(dbg));
    try_quote_pending_revoke_with_copy(copy);
}

static int enqueue_revoke_forward_job(int64_t orig_msgid, const char *talker,
                                      int64_t revoketime, const char *source_tag) {
    int pos;
    RevokeForwardJob *job;
    char dbg[256];
    if (!orig_msgid) return 0;
    pos = InterlockedIncrement(&g_revoke_cache_state.revoke_forward_job_pos) % REVOKE_FORWARD_JOB_SIZE;
    job = &g_revoke_cache_state.revoke_forward_jobs[pos];
    InterlockedExchange(&job->state, 0);
    job->orig_msgid = orig_msgid;
    job->revoketime = revoketime;
    safe_copy(job->talker, sizeof(job->talker), talker ? talker : "");
    safe_copy(job->source_tag, sizeof(job->source_tag), source_tag ? source_tag : "");
    InterlockedExchange(&job->state, 1);
    wsprintfA(dbg, "revoke_forward_job: queued slot=%d orig=%I64d source=%s\r\n",
              pos, (long long)orig_msgid, source_tag ? source_tag : "");
    append_file_bytes(REVOKE_LOG, dbg, (DWORD)lstrlenA(dbg));
    return 1;
}

static void format_unix_time_local(int32_t ts, char *out, int max) {
    FILETIME ft_utc, ft_local;
    SYSTEMTIME st;
    unsigned __int64 t;
    if (!out || max <= 0) return;
    out[0] = 0;
    if (ts <= 0) {
        safe_copy(out, max, "unknown-time");
        return;
    }
    t = ((unsigned __int64)(uint32_t)ts + 11644473600ULL) * 10000000ULL;
    ft_utc.dwLowDateTime = (DWORD)t;
    ft_utc.dwHighDateTime = (DWORD)(t >> 32);
    if (FileTimeToLocalFileTime(&ft_utc, &ft_local) &&
        FileTimeToSystemTime(&ft_local, &st)) {
        wsprintfA(out, "%04u-%02u-%02u %02u:%02u:%02u",
                  (unsigned)st.wYear, (unsigned)st.wMonth, (unsigned)st.wDay,
                  (unsigned)st.wHour, (unsigned)st.wMinute, (unsigned)st.wSecond);
    } else {
        i64toa10(ts, out);
    }
}

static void normalize_preview_text(char *s) {
    int i;
    if (!s) return;
    for (i = 0; s[i]; i++) {
        if (s[i] == '\r' || s[i] == '\n' || s[i] == '\t') s[i] = ' ';
    }
}

static void extract_first_tag_preview(const char *content, const char *tag,
                                      char *out, int max) {
    char begin[64];
    char end_tag[64];
    if (!out || max <= 0) return;
    out[0] = 0;
    if (!content || !tag || !tag[0]) return;

    wsprintfA(begin, "<%s><![CDATA[", tag);
    wsprintfA(end_tag, "]]></%s>", tag);
    if (extract_between_markers(content, begin, end_tag, out, max)) return;

    extract_tag_value(content, tag, out, max);
}

static int revoke_is_group_chat(const CachedMsg *orig) {
    if (!orig) return 0;
    return is_chatroom_id(orig->peer) || is_chatroom_id(orig->from) || is_chatroom_id(orig->self);
}

static const char *revoke_room_id(const CachedMsg *orig) {
    if (!orig) return "";
    if (is_chatroom_id(orig->peer)) return orig->peer;
    if (is_chatroom_id(orig->from)) return orig->from;
    if (is_chatroom_id(orig->self)) return orig->self;
    return "";
}

static void revoke_sender_display_name(const CachedMsg *orig, const char *sender_wxid,
                                       char *out, int max) {
    const char *id;
    if (!out || max <= 0) return;
    out[0] = 0;
    id = (sender_wxid && sender_wxid[0]) ? sender_wxid : "";
    if (!id[0] && orig) id = orig->sender[0] ? orig->sender : orig->from;
    if (wxb_self_wxid()[0] && str_eq(id, wxb_self_wxid())) {
        safe_copy(out, max, "我");
        return;
    }
    if (lookup_room_member_display_name(revoke_room_id(orig), id, out, max)) return;
    if (lookup_contact_display_name(id, out, max)) return;
    if (is_displayable_name_for_id(NULL, id)) {
        safe_copy(out, max, id);
        return;
    }
    safe_copy(out, max, revoke_is_group_chat(orig) ? "群成员" : "对方");
}

static void revoke_chat_display_name(const CachedMsg *orig, char *out, int max) {
    const char *id = "";
    char name[256] = {0};
    char *p;
    char *end;
    int is_group;
    if (!out || max <= 0) return;
    out[0] = 0;
    if (!orig) {
        safe_copy(out, max, "未知会话");
        return;
    }
    is_group = revoke_is_group_chat(orig);
    if (is_group) {
        id = revoke_room_id(orig);
    } else {
        id = orig->peer[0] ? orig->peer : orig->from;
        if (wxb_self_wxid()[0] && str_eq(id, wxb_self_wxid()) && orig->sender[0]) id = orig->sender;
    }
    if (!lookup_contact_display_name(id, name, sizeof(name))) {
        if (is_displayable_name_for_id(NULL, id)) safe_copy(name, sizeof(name), id);
        else safe_copy(name, sizeof(name), is_group ? "群聊" : "对方");
    }
    p = out;
    end = out + max - 1;
    append_raw(&p, end, is_group ? "群聊：" : "私聊：");
    append_raw(&p, end, name);
    *p = 0;
}

static void build_revoke_fallback_summary(const CachedMsg *orig, char *out, int max) {
    char title[512] = {0};
    char desc[512] = {0};
    char url[512] = {0};
    char fileext[64] = {0};
    char totallen[64] = {0};
    char md5[128] = {0};
    char preview[900] = {0};
    char *p;
    char *end;
    int file_like;

    if (!out || max <= 0) return;
    out[0] = 0;
    if (!orig) return;
    file_like = appmsg_content_is_file_like(orig->msg_type, orig->msg_subtype, orig->content);

    p = out;
    end = out + max - 1;

    append_raw(&p, end, "类型：");
    append_raw(&p, end, revoke_msg_type_label_for_content(orig->msg_type, orig->msg_subtype, orig->content));

    if (orig->content[0]) {
        if (orig->msg_type == 49) {
            extract_first_tag_preview(orig->content, "title", title, sizeof(title));
            normalize_preview_text(title);
            if (file_like) {
                extract_first_tag_preview(orig->content, "fileext", fileext, sizeof(fileext));
                extract_first_tag_preview(orig->content, "totallen", totallen, sizeof(totallen));
                extract_first_tag_preview(orig->content, "md5", md5, sizeof(md5));
                normalize_preview_text(fileext);
                normalize_preview_text(totallen);
                normalize_preview_text(md5);
                if (title[0]) {
                    append_raw(&p, end, "\n文件名：");
                    append_raw(&p, end, title);
                }
                if (totallen[0]) {
                    append_raw(&p, end, "\n大小：");
                    append_raw(&p, end, totallen);
                    append_raw(&p, end, " 字节");
                }
                if (fileext[0]) {
                    append_raw(&p, end, "\n扩展名：");
                    append_raw(&p, end, fileext);
                }
                if (md5[0]) {
                    append_raw(&p, end, "\nMD5：");
                    append_raw(&p, end, md5);
                }
                *p = 0;
                return;
            } else {
                extract_first_tag_preview(orig->content, "des", desc, sizeof(desc));
                extract_first_tag_preview(orig->content, "url", url, sizeof(url));
                normalize_preview_text(desc);
                normalize_preview_text(url);
                if (title[0]) {
                    append_raw(&p, end, "\n标题：");
                    append_raw(&p, end, title);
                }
                if (desc[0]) {
                    append_raw(&p, end, "\n描述：");
                    append_raw(&p, end, desc);
                }
                if (url[0]) {
                    append_raw(&p, end, "\n链接：");
                    append_raw(&p, end, url);
                }
            }
        }

        safe_copy(preview, sizeof(preview), strip_group_sender_prefix(orig->content));
        normalize_preview_text(preview);
        if (preview[0]) {
            append_raw(&p, end, "\n内容摘要：");
            append_raw(&p, end, preview);
        }
    } else {
        append_raw(&p, end, "\n内容摘要：[空内容/媒体内容]");
    }
    *p = 0;
}

static void build_revoke_success_notice(const CachedMsg *orig, const char *sender,
                                        const char *time_s, char *out, int max) {
    char sender_name[256];
    char chat[320];
    const char *type_s;
    char *p;
    char *end;
    if (!out || max <= 0) return;
    out[0] = 0;
    if (!orig) return;

    revoke_sender_display_name(orig, sender, sender_name, sizeof(sender_name));
    revoke_chat_display_name(orig, chat, sizeof(chat));
    type_s = revoke_msg_type_label_for_content(orig->msg_type, orig->msg_subtype, orig->content);

    p = out;
    end = out + max - 1;
    append_raw(&p, end, "【撤回消息 | 原文已转发】\n");
    append_raw(&p, end, "发送人：");
    append_raw(&p, end, sender_name[0] ? sender_name : "未知");
    append_raw(&p, end, "\n会话：");
    append_raw(&p, end, chat);
    append_raw(&p, end, "\n时间：");
    append_raw(&p, end, time_s && time_s[0] ? time_s : "unknown-time");
    append_raw(&p, end, "\n类型：");
    append_raw(&p, end, type_s);
    *p = 0;
}

static int mark_revoke_notice_once(int64_t orig_msgid) {
    if (!orig_msgid) return 0;
    for (int i = 0; i < REVOKE_NOTICE_DEDUPE_SIZE; i++) {
        if (InterlockedCompareExchange64(&g_revoke_cache_state.revoke_notice_ids[i], 0, 0) == orig_msgid) {
            return 0;
        }
    }
    int pos = InterlockedIncrement(&g_revoke_cache_state.revoke_notice_pos) % REVOKE_NOTICE_DEDUPE_SIZE;
    InterlockedExchange64(&g_revoke_cache_state.revoke_notice_ids[pos], orig_msgid);
    return 1;
}

int handle_revoke_notice_by_id(int64_t orig_msgid, const char *talker,
                                      int64_t revoketime, const char *source_tag) {
    CachedMsg *orig;
    char dbg[512];
    if (!orig_msgid) return 0;
    for (int i = 0; i < REVOKE_NOTICE_DEDUPE_SIZE; i++) {
        if (InterlockedCompareExchange64(&g_revoke_cache_state.revoke_notice_ids[i], 0, 0) == orig_msgid) {
            return 0;
        }
    }
    orig = find_cached_msg(orig_msgid);
    if (!orig || !orig->used) {
        char id_s[32];
        i64toa10(orig_msgid, id_s);
        wsprintfA(dbg, "revoke_notice: original msg not cached id=%s source=%s\r\n",
                  id_s, source_tag ? source_tag : "");
        append_file_bytes(REVOKE_LOG, dbg, (DWORD)lstrlenA(dbg));
        return 0;
    }
    if (revoke_is_group_chat(orig)) {
        const char *roomid = is_chatroom_id(orig->peer) ? orig->peer :
                             (is_chatroom_id(orig->from) ? orig->from : orig->self);
        const char *sender = orig->sender[0] ? orig->sender :
                             (talker && talker[0] ? talker : orig->from);
        schedule_auto_room_members(roomid, sender, "revoke");
    }
    if (!mark_revoke_notice_once(orig_msgid)) return 0;
    return enqueue_revoke_forward_job(orig_msgid, talker, revoketime, source_tag);
}

static int process_revoke_forward_job_now(int64_t orig_msgid, const char *talker,
                                          int64_t revoketime, const char *source_tag) {
    CachedMsg *orig;
    char dbg[512];
    char time_s[64];
    char notice[2048];
    char failure_notice[3072];
    char fallback_summary[2048];
    const char *sender;
    int variant = 0;
    int queue_status = 0;
    int retained = 0;
    int fallback_kind = 0;
    int forward_ok = 0;
    int quote_ok = 0;
    int orig_text_ok = 0;
    int text_notice_ok = 0;
    ULONGLONG forward_start_tick;

    if (!orig_msgid) return 0;
    orig = find_cached_msg(orig_msgid);
    if (!orig || !orig->used) {
        char id_s[32];
        i64toa10(orig_msgid, id_s);
        wsprintfA(dbg, "revoke_forward_job: original msg not cached id=%s source=%s\r\n",
                  id_s, source_tag ? source_tag : "");
        append_file_bytes(REVOKE_LOG, dbg, (DWORD)lstrlenA(dbg));
        return 0;
    }

    poll_room_task();
    sender = orig->sender[0] ? orig->sender : (talker && talker[0] ? talker : orig->from);
    if (revoke_is_group_chat(orig)) {
        const char *roomid = is_chatroom_id(orig->peer) ? orig->peer :
                             (is_chatroom_id(orig->from) ? orig->from : orig->self);
        char room_name[256] = {0};
        schedule_auto_room_members(roomid, sender, "revoke_job");
        if (!lookup_contact_display_name(roomid, room_name, sizeof(room_name))) {
            schedule_auto_contact_names("revoke_job_room_name");
            poll_auto_contact_names();
        }
    }
    format_unix_time_local(orig->createtime ? orig->createtime : (int32_t)revoketime,
                           time_s, sizeof(time_s));

    forward_start_tick = GetTickCount64();
    forward_ok = send_forward_high_native("filehelper",
                                          orig->content,
                                          orig->msg_type,
                                          orig->msg_subtype,
                                          orig->local_id,
                                          orig->msgid,
                                          orig->createtime,
                                          orig->from,
                                          orig->self,
                                          sender,
                                           orig->msgsource,
                                           &variant,
                                           &queue_status,
                                           &retained,
                                           &fallback_kind,
                                           0);
    if (!forward_ok) {
        forward_ok = send_forward_any_native("filehelper",
                                             orig->content,
                                             orig->msg_type,
                                             orig->msg_subtype,
                                             orig->local_id,
                                             orig->msgid,
                                             orig->createtime,
                                             orig->from,
                                             orig->self,
                                             sender,
                                             orig->msgsource,
                                             &variant,
                                             &queue_status,
                                             0);
    }

    if (forward_ok) {
        build_revoke_success_notice(orig, sender, time_s, notice, sizeof(notice));
        quote_ok = add_pending_revoke_quote(orig, notice, forward_start_tick, 0);
    }
    if (!forward_ok && orig->msg_type == 1 && orig->content[0]) {
        build_revoke_success_notice(orig, sender, time_s, notice, sizeof(notice));
        quote_ok = add_pending_revoke_quote(orig, notice, GetTickCount64(), 1);
        orig_text_ok = send_text("filehelper", orig->content);
        if (!orig_text_ok) {
            clear_pending_revoke_quote(orig->msgid);
            quote_ok = 0;
        }
    }
    if (!forward_ok && !orig_text_ok) {
        char sender_name[256];
        char chat[320];
        char *fp = failure_notice;
        char *fend = failure_notice + sizeof(failure_notice) - 1;
        revoke_sender_display_name(orig, sender, sender_name, sizeof(sender_name));
        revoke_chat_display_name(orig, chat, sizeof(chat));
        build_revoke_fallback_summary(orig, fallback_summary, sizeof(fallback_summary));
        append_raw(&fp, fend, "【撤回消息 | 原文转发失败】\n");
        append_raw(&fp, fend, "发送人：");
        append_raw(&fp, fend, sender_name[0] ? sender_name : "未知");
        append_raw(&fp, fend, "\n会话：");
        append_raw(&fp, fend, chat);
        append_raw(&fp, fend, "\n时间：");
        append_raw(&fp, fend, time_s);
        append_raw(&fp, fend, "\n");
        append_raw(&fp, fend, fallback_summary[0] ? fallback_summary : "原消息摘要生成失败，请查看 revoke_log/forward_debug。");
        append_raw(&fp, fend, "\n说明：原文转发失败，已发送可读摘要；详细原因见 revoke_log/forward_debug。");
        *fp = 0;
        text_notice_ok = send_text("filehelper", failure_notice);
    }

    wsprintfA(dbg,
              "revoke_forward_job: id=%I64d source=%s forward_ok=%d qstatus=%d text_orig_ok=%d quote_copy_ok=%d text_notice_ok=%d sender=%s time=%s\r\n",
              (long long)orig_msgid,
              source_tag ? source_tag : "",
              forward_ok,
              queue_status,
              orig_text_ok,
              quote_ok,
              text_notice_ok,
              sender,
              time_s);
    append_file_bytes(REVOKE_LOG, dbg, (DWORD)lstrlenA(dbg));
    write_status((forward_ok || orig_text_ok)
                 ? "revoke original sent; waiting copy quote"
                 : (text_notice_ok ? "revoke notice text sent (original send failed)" : "revoke notice failed"));
    return forward_ok || orig_text_ok || quote_ok || text_notice_ok;
}

void process_revoke_forward_jobs(void) {
    poll_auto_contact_names();
    for (int i = 0; i < REVOKE_FORWARD_JOB_SIZE; i++) {
        RevokeForwardJob *job = &g_revoke_cache_state.revoke_forward_jobs[i];
        if (InterlockedCompareExchange(&job->state, 2, 1) == 1) {
            process_revoke_forward_job_now(job->orig_msgid, job->talker, job->revoketime, job->source_tag);
            InterlockedExchange(&job->state, 0);
        }
    }
}
