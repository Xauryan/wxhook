#include "../include/wx_bridge_http_api.h"
#include "../include/wx_bridge_account_info.h"
#include "../include/wx_bridge_a8key_task.h"
#include "../include/wx_bridge_anti_revoke.h"
#include "../include/wx_bridge_chatroom_member.h"
#include "../include/wx_bridge_contact_lookup.h"
#include "../include/wx_bridge_file_io.h"
#include "../include/wx_bridge_finder_ops.h"
#include "../include/wx_bridge_forward_send.h"
#include "../include/wx_bridge_friend_accept.h"
#include "../include/wx_bridge_h5ext_transfer.h"
#include "../include/wx_bridge_media_ops.h"
#include "../include/wx_bridge_open_url.h"
#include "../include/wx_bridge_paths.h"
#include "../include/wx_bridge_pay_transfer.h"
#include "../include/wx_bridge_protocol.h"
#include "../include/wx_bridge_receive_hooks.h"
#include "../include/wx_bridge_room_contacts.h"
#include "../include/wx_bridge_runtime.h"
#include "../include/wx_bridge_send_api.h"
#include "../include/wx_bridge_sns_comment.h"
#include "../include/wx_bridge_sns_publish.h"
#include "../include/wx_bridge_sns_timeline.h"
#include "../include/wx_bridge_text.h"
#include "../include/wx_bridge_voice_transcribe.h"

static volatile LONG g_http_seq = 0;

static int uri_match(struct mg_http_message *hm, const char *uri) {
    return mg_http_match_uri(hm, uri) ? 1 : 0;
}

/*
 * mongoose 的 json_unescape 只肯解 \u00XX,注释里写明"More complex chars would
 * require dragging in a UTF8 library, which is too much for us",遇到中文直接
 * return false,于是 mg_json_get_str 返回 NULL、json_str 回退成 def(通常是空串)。
 * 而 Python 的 json.dumps 默认 ensure_ascii=True,发过来的正好是 \uXXXX —— 中文
 * 图片路径/内容会静默变成空串。这里补一层解码,不改编 third_party/mongoose.c。
 */
static int json_hex4(const char *p) {
    int v = 0, i;
    for (i = 0; i < 4; i++) {
        char c = p[i];
        v <<= 4;
        if (c >= '0' && c <= '9') v |= (c - '0');
        else if (c >= 'a' && c <= 'f') v |= (c - 'a' + 10);
        else if (c >= 'A' && c <= 'F') v |= (c - 'A' + 10);
        else return -1;
    }
    return v;
}

static void json_utf8_put(char **p, char *end, unsigned cp) {
    char *o = *p;
    if (cp < 0x80) {
        if (o < end) *o++ = (char)cp;
    } else if (cp < 0x800) {
        if (o + 1 < end) { *o++ = (char)(0xC0 | (cp >> 6)); *o++ = (char)(0x80 | (cp & 0x3F)); }
    } else if (cp < 0x10000) {
        if (o + 2 < end) {
            *o++ = (char)(0xE0 | (cp >> 12));
            *o++ = (char)(0x80 | ((cp >> 6) & 0x3F));
            *o++ = (char)(0x80 | (cp & 0x3F));
        }
    } else {
        if (o + 3 < end) {
            *o++ = (char)(0xF0 | (cp >> 18));
            *o++ = (char)(0x80 | ((cp >> 12) & 0x3F));
            *o++ = (char)(0x80 | ((cp >> 6) & 0x3F));
            *o++ = (char)(0x80 | (cp & 0x3F));
        }
    }
    *p = o;
}

/* 解码一个完整的 JSON 字符串 token(含两侧引号)。失败返回 NULL。 */
static char *json_unescape_token(const char *tok, int toklen) {
    char *out, *p, *end;
    int i;
    if (toklen < 2 || tok[0] != '"') return NULL;
    out = (char *)calloc(1, (size_t)toklen + 1);
    if (!out) return NULL;
    p = out;
    end = out + toklen;
    for (i = 1; i < toklen - 1; i++) {
        char c = tok[i];
        if (c != '\\') {
            if (p < end) *p++ = c;
            continue;
        }
        if (++i >= toklen - 1) break;
        c = tok[i];
        if (c == 'u') {
            int cp;
            if (i + 4 >= toklen) { free(out); return NULL; }
            cp = json_hex4(tok + i + 1);
            if (cp < 0) { free(out); return NULL; }
            i += 4;
            if (cp >= 0xD800 && cp <= 0xDBFF && i + 6 < toklen &&
                tok[i + 1] == '\\' && tok[i + 2] == 'u') {
                int lo = json_hex4(tok + i + 3);
                if (lo >= 0xDC00 && lo <= 0xDFFF) {
                    cp = 0x10000 + ((cp - 0xD800) << 10) + (lo - 0xDC00);
                    i += 6;
                }
            }
            json_utf8_put(&p, end, (unsigned)cp);
            continue;
        }
        switch (c) {
            case 'n': c = '\n'; break;
            case 't': c = '\t'; break;
            case 'r': c = '\r'; break;
            case 'b': c = '\b'; break;
            case 'f': c = '\f'; break;
            case '/': c = '/';  break;
            case '"': c = '"';  break;
            case '\\': c = '\\'; break;
            default: break;
        }
        if (p < end) *p++ = c;
    }
    *p = 0;
    return out;
}

static char *json_str(struct mg_http_message *hm, const char *key, const char *def) {
    char path[96];
    char *s;
    wsprintfA(path, "$.%s", key);
    s = mg_json_get_str(hm->body, path);
    if (!s) {
        /* 回退:自己解 \uXXXX(非 ASCII),修中文路径/内容被吞成空串的问题。 */
        int toklen = 0;
        int off = mg_json_get(hm->body, path, &toklen);
        if (off >= 0 && toklen >= 2 && toklen <= 1024 * 1024 &&
            hm->body.ptr[off] == '"') {
            s = json_unescape_token(hm->body.ptr + off, toklen);
        }
    }
    if (!s && def) {
        size_t n = lstrlenA(def);
        s = (char *)calloc(1, n + 1);
        if (s) CopyMemory(s, def, n + 1);
    }
    return s;
}

static char *json_scalar_str(struct mg_http_message *hm, const char *key, const char *def) {
    char path[96];
    char *s = json_str(hm, key, NULL);
    int toklen = 0;
    int off;
    if (s) return s;
    wsprintfA(path, "$.%s", key);
    off = mg_json_get(hm->body, path, &toklen);
    if (off >= 0 && toklen > 0 && toklen <= 4096 &&
        hm->body.ptr[off] != '{' && hm->body.ptr[off] != '[' &&
        hm->body.ptr[off] != '"') {
        s = (char *)calloc(1, (size_t)toklen + 1);
        if (s) CopyMemory(s, hm->body.ptr + off, toklen);
        return s;
    }
    if (def) {
        size_t n = lstrlenA(def);
        s = (char *)calloc(1, n + 1);
        if (s) CopyMemory(s, def, n + 1);
    }
    return s;
}

static char *json_raw_value(struct mg_http_message *hm, const char *key) {
    char path[96];
    int toklen = 0;
    int off;
    char *value;
    wsprintfA(path, "$.%s", key);
    off = mg_json_get(hm->body, path, &toklen);
    if (off < 0 || toklen <= 0 || toklen > 1024 * 1024) return NULL;
    value = (char *)calloc(1, (size_t)toklen + 1);
    if (value) CopyMemory(value, hm->body.ptr + off, toklen);
    return value;
}

static char *json_first_scalar(struct mg_http_message *hm,
                               const char *const *keys,
                               int key_count) {
    char *value;
    int i;
    for (i = 0; i < key_count; i++) {
        value = json_scalar_str(hm, keys[i], NULL);
        if (value && value[0]) return value;
        if (value) free(value);
    }
    return (char *)calloc(1, 1);
}

static char *json_first_raw(struct mg_http_message *hm,
                            const char *const *keys,
                            int key_count) {
    char *value;
    int i;
    for (i = 0; i < key_count; i++) {
        value = json_raw_value(hm, keys[i]);
        if (value && value[0]) return value;
        if (value) free(value);
    }
    return (char *)calloc(1, 1);
}

static long json_long(struct mg_http_message *hm, const char *key, long def) {
    char path[96];
    wsprintfA(path, "$.%s", key);
    return mg_json_get_long(hm->body, path, def);
}

static int64_t json_i64(struct mg_http_message *hm, const char *key, int64_t def) {
    char path[96];
    char tmp[96];
    int toklen = 0;
    int off;
    wsprintfA(path, "$.%s", key);
    off = mg_json_get(hm->body, path, &toklen);
    if (off < 0 || toklen <= 0 || toklen >= (int)sizeof(tmp)) return def;
    if (hm->body.ptr[off] == '"' && toklen >= 2) {
        CopyMemory(tmp, hm->body.ptr + off + 1, toklen - 2);
        tmp[toklen - 2] = 0;
    } else {
        CopyMemory(tmp, hm->body.ptr + off, toklen);
        tmp[toklen] = 0;
    }
    return a2i64(tmp);
}

static int json_boolish(struct mg_http_message *hm, const char *key, int def) {
    char path[96];
    bool b = false;
    char *s;
    wsprintfA(path, "$.%s", key);
    if (mg_json_get_bool(hm->body, path, &b)) return b ? 1 : 0;
    s = json_str(hm, key, NULL);
    if (s) {
        int ret = (s[0] == '1' || s[0] == 't' || s[0] == 'T' ||
                   s[0] == 'y' || s[0] == 'Y') ? 1 : 0;
        free(s);
        return ret;
    }
    if (mg_json_get(hm->body, path, NULL) >= 0) return mg_json_get_long(hm->body, path, def) ? 1 : 0;
    return def;
}

static char *json_first_nonempty(struct mg_http_message *hm,
                                 const char *const *keys,
                                 int key_count) {
    char *value;
    int i;
    for (i = 0; i < key_count; i++) {
        value = json_str(hm, keys[i], NULL);
        if (value && value[0]) return value;
        if (value) free(value);
    }
    return (char *)calloc(1, 1);
}

static char *json_string_list(struct mg_http_message *hm, const char *key) {
    char path[96];
    char *value = json_str(hm, key, NULL);
    size_t capacity;
    size_t used = 0;
    int i;

    if (value) return value;
    capacity = hm->body.len + 1;
    if (capacity < 2) capacity = 2;
    value = (char *)calloc(1, capacity);
    if (!value) return NULL;

    for (i = 0; i < 4096; i++) {
        char *item;
        size_t item_len;
        int token_len = 0;
        int offset;

        wsprintfA(path, "$.%s[%d]", key, i);
        offset = mg_json_get(hm->body, path, &token_len);
        if (offset < 0) break;
        item = mg_json_get_str(hm->body, path);
        if (!item) {
            free(value);
            return (char *)calloc(1, 1);
        }
        item_len = (size_t)lstrlenA(item);
        if (used + (i ? 1 : 0) + item_len >= capacity) {
            free(item);
            free(value);
            return (char *)calloc(1, 1);
        }
        if (i) value[used++] = ',';
        if (item_len) CopyMemory(value + used, item, item_len);
        used += item_len;
        value[used] = 0;
        free(item);
    }
    return value;
}

static void make_seq(char *seq, int max) {
    DWORD pid = GetCurrentProcessId();
    LONG n = InterlockedIncrement(&g_http_seq);
    wsprintfA(seq, "%lu%lu", (unsigned long)GetTickCount64(), (unsigned long)((pid ^ n) & 0xFFFF));
    if (max > 0) seq[max - 1] = 0;
}

static void reply_raw(struct mg_connection *c, int code, const char *msg, const char *data) {
    const char *body = data ? data : "{}";
    mg_http_reply(c, 200, "Content-Type: application/json; charset=utf-8\r\n",
                  "{\"code\":%d,\"msg\":\"%s\",\"data\":%s}\n",
                  code, msg ? msg : (code > 0 ? "success" : "fail"), body);
}

static void reply_msg(struct mg_connection *c, int code, const char *msg) {
    char data[512];
    char *p = data;
    char *end = data + sizeof(data) - 1;
    append_raw(&p, end, "{\"message\":\"");
    append_escaped(&p, end, msg ? msg : "");
    append_raw(&p, end, "\"}");
    *p = 0;
    reply_raw(c, code, code > 0 ? "success" : "fail", data);
}

static void reply_friend_result(struct mg_connection *c,
                                const WxbFriendAcceptResult *result) {
    char data[2048];
    char number[64];
    char *p = data;
    char *end = data + sizeof(data) - 1;
    append_raw(&p, end, "{\"ok\":");
    append_raw(&p, end, result->ok ? "true" : "false");
    append_raw(&p, end, ",\"errorCode\":");
    wsprintfA(number, "%d", result->error_code);
    append_raw(&p, end, number);
    append_raw(&p, end, ",\"opcode\":");
    wsprintfA(number, "%d", result->opcode);
    append_raw(&p, end, number);
    append_raw(&p, end, ",\"exceptionCode\":");
    wsprintfA(number, "%lu", (unsigned long)result->exception_code);
    append_raw(&p, end, number);
    append_raw(&p, end, ",\"message\":\"");
    append_escaped(&p, end, result->message);
    append_raw(&p, end, "\"}");
    *p = 0;
    reply_raw(c, result->ok ? 1 : 0, result->ok ? "success" : "fail", data);
}

static void handle_accept_friend(struct mg_connection *c,
                                 struct mg_http_message *hm) {
    static const char *const username_keys[] = {
        "encryptUsername", "encryptusername", "v3", "wxid"
    };
    static const char *const ticket_keys[] = {
        "ticket", "antiSpamTicket", "antispamticket", "v4"
    };
    WxbFriendAcceptResult result;
    char *xml = json_str(hm, "xml", NULL);
    int timeout = (int)json_long(hm, "timeout", 15000);
    if (xml && xml[0]) {
        wxb_friend_accept_xml(xml, timeout, &result);
    } else {
        char *username = json_first_nonempty(
            hm, username_keys, (int)(sizeof(username_keys) / sizeof(username_keys[0])));
        char *ticket = json_first_nonempty(
            hm, ticket_keys, (int)(sizeof(ticket_keys) / sizeof(ticket_keys[0])));
        int friend_flag = (int)json_long(
            hm, "friendFlag", json_long(hm, "friendflag", 0));
        wxb_friend_accept(username, ticket,
                          (int)json_long(hm, "scene", -1),
                          friend_flag, timeout, &result);
        if (username) free(username);
        if (ticket) free(ticket);
    }
    if (xml) free(xml);
    reply_friend_result(c, &result);
}

static void handle_auto_accept_friend(struct mg_connection *c,
                                      struct mg_http_message *hm) {
    WxbFriendAutoStatus status;
    char data[512];
    int enable = json_boolish(hm, "enable", wxb_friend_auto_enabled());
    int ok = 1;
    if (enable) {
        if (!wxb_ensure_weixin_module()) {
            ok = 0;
        } else {
            if (!recv_hook_installed()) install_recv_hook();
            if (!recv_hook_installed()) ok = 0;
        }
    }
    wxb_friend_auto_set_enabled(enable && ok);
    wxb_friend_auto_get_status(&status);
    wsprintfA(data,
              "{\"enabled\":%s,\"queued\":%ld,\"succeeded\":%ld,"
              "\"failed\":%ld,\"parseFailed\":%ld,\"deduplicated\":%ld}",
              status.enabled ? "true" : "false",
              status.queued, status.succeeded, status.failed,
              status.parse_failed, status.deduplicated);
    reply_raw(c, ok ? 1 : 0, ok ? "success" : "receive hook unavailable", data);
}

#define HTTP_GENERATED_JSON_MAX (32 * 1024 * 1024)

static int read_generated_json(const char *prefix, const char *seq, char **out) {
    WCHAR path[1024];
    HANDLE file;
    LARGE_INTEGER size;
    char *buf;
    DWORD read = 0;
    BOOL read_ok;
    if (!out) return 0;
    *out = NULL;
    if (str_eq(prefix, "room")) wsprintfW(path, L"%s\\room_%S.json", CONTROL_DIR, seq);
    else wsprintfW(path, L"%s\\result_%S.json", CONTROL_DIR, seq);
    file = CreateFileW(path, GENERIC_READ,
                       FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
                       NULL, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, NULL);
    if (file == INVALID_HANDLE_VALUE) return 0;
    if (!GetFileSizeEx(file, &size) || size.QuadPart <= 0 ||
        size.QuadPart > HTTP_GENERATED_JSON_MAX) {
        CloseHandle(file);
        return 0;
    }
    buf = (char *)VirtualAlloc(NULL, (SIZE_T)size.QuadPart + 1,
                               MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE);
    if (!buf) {
        CloseHandle(file);
        return 0;
    }
    read_ok = ReadFile(file, buf, (DWORD)size.QuadPart, &read, NULL);
    CloseHandle(file);
    if (!read_ok || read != (DWORD)size.QuadPart) {
        VirtualFree(buf, 0, MEM_RELEASE);
        return 0;
    }
    buf[read] = 0;
    *out = buf;
    return 1;
}

static void reply_seq_result(struct mg_connection *c, const char *seq, int ok, const char *fallback_msg) {
    char *json = NULL;
    if (read_generated_json("result", seq, &json)) {
        reply_raw(c, ok ? 1 : 0, ok ? "success" : "fail", json);
        VirtualFree(json, 0, MEM_RELEASE);
    } else {
        reply_msg(c, ok ? 1 : 0, fallback_msg ? fallback_msg : (ok ? "success" : "fail"));
    }
}

static int json_object_for_wxid(const char *json, const char *wxid, char *out, int out_max) {
    char needle[640];
    char escaped[512];
    char *ep = escaped;
    char *eend = escaped + sizeof(escaped) - 1;
    const char *hit;
    const char *start;
    const char *end;
    int depth = 0;
    int in_string = 0;
    int escaped_char = 0;
    int n = 0;
    if (!json || !wxid || !wxid[0] || !out || out_max <= 0) return 0;
    out[0] = 0;
    append_escaped(&ep, eend, wxid);
    *ep = 0;
    wsprintfA(needle, "\"wxid\":\"%s\"", escaped);
    hit = str_find_sub(json, needle);
    if (!hit) return 0;
    start = hit;
    while (start > json && *start != '{') start--;
    if (*start != '{') return 0;
    end = start;
    while (*end) {
        char ch = *end;
        if (in_string) {
            if (escaped_char) escaped_char = 0;
            else if (ch == '\\') escaped_char = 1;
            else if (ch == '"') in_string = 0;
        } else if (ch == '"') {
            in_string = 1;
        } else if (ch == '{') {
            depth++;
        } else if (ch == '}') {
            depth--;
            if (depth == 0) break;
            if (depth < 0) return 0;
        }
        end++;
    }
    if (*end != '}' || depth != 0 || in_string) return 0;
    while (start <= end && n < out_max - 1) out[n++] = *start++;
    out[n] = 0;
    return n > 0;
}

static void handle_contact_profile(struct mg_connection *c, struct mg_http_message *hm) {
    char *wxid = json_str(hm, "wxid", "");
    int limit = (int)json_long(hm, "limit", 10000);
    char *contacts = NULL;
    char item[8192];
    if (!wxid || !wxid[0]) {
        reply_msg(c, 0, "missing wxid");
        goto cleanup;
    }
    contacts = wxb_contact_list_json(limit);
    if (!contacts) {
        reply_msg(c, 0, "contact_pattern_not_found");
        goto cleanup;
    }
    if (json_object_for_wxid(contacts, wxid, item, sizeof(item))) {
        reply_raw(c, 1, "success", item);
    } else {
        reply_msg(c, 0, "contact_not_found");
    }
cleanup:
    if (contacts) VirtualFree(contacts, 0, MEM_RELEASE);
    if (wxid) free(wxid);
}

static void handle_forward_public_msg(struct mg_connection *c, struct mg_http_message *hm) {
    char *wxid = json_str(hm, "wxid", "");
    char *title = json_str(hm, "title", "");
    char *digest = json_str(hm, "digest", "");
    char *url = json_str(hm, "url", "");
    char *thumb = json_str(hm, "thumbUrl", "");
    char *user_name = json_str(hm, "userName", "");
    char *app_name = json_str(hm, "appName", "");
    int ok = send_card(wxid, title, digest, (int)json_long(hm, "subtype", 5),
                       "view", url, "", "", thumb, "", user_name, app_name);
    reply_msg(c, ok, ok ? "success" : "forward_public_msg failed");
    free(wxid); free(title); free(digest); free(url); free(thumb); free(user_name); free(app_name);
}

static void handle_forward_msg_by_id(struct mg_connection *c, struct mg_http_message *hm, const char *failure_msg) {
    char *wxid = json_str(hm, "wxid", "");
    char *content = json_str(hm, "content", "");
    char *source_from = json_str(hm, "sourceFrom", "");
    char *source_to = json_str(hm, "sourceTo", "");
    char *source_sender = json_str(hm, "sourceSender", "");
    char *msgsource = json_str(hm, "msgSource", "");
    int variant = 0, queue_status = 0;
    int ok = send_forward_any_native(wxid, content,
                                     (int)json_long(hm, "sourceType", 1),
                                     (int)json_long(hm, "sourceSubtype", 0),
                                     (int)json_long(hm, "sourceLocalId", 0),
                                     json_i64(hm, "msgId", 0),
                                     (int32_t)json_long(hm, "sourceTime", 0),
                                     source_from, source_to, source_sender, msgsource,
                                     &variant, &queue_status, 0);
    reply_msg(c, ok, ok ? "success" : failure_msg);
    free(wxid); free(content); free(source_from); free(source_to); free(source_sender); free(msgsource);
}

static int configure_http_url(const char *url, DWORD timeout_ms) {
    WCHAR wide[2048];
    WCHAR host[256] = {0};
    WCHAR path[1024] = {0};
    WCHAR extra[512] = {0};
    URL_COMPONENTS comp;
    int wlen;
    if (!url || !url[0]) return 0;
    wlen = MultiByteToWideChar(CP_UTF8, 0, url, -1, wide, (int)(sizeof(wide) / sizeof(wide[0])));
    if (wlen <= 0) return 0;
    ZeroMemory(&comp, sizeof(comp));
    comp.dwStructSize = sizeof(comp);
    comp.lpszHostName = host;
    comp.dwHostNameLength = sizeof(host) / sizeof(host[0]);
    comp.lpszUrlPath = path;
    comp.dwUrlPathLength = sizeof(path) / sizeof(path[0]);
    comp.lpszExtraInfo = extra;
    comp.dwExtraInfoLength = sizeof(extra) / sizeof(extra[0]);
    if (!WinHttpCrackUrl(wide, 0, 0, &comp)) return 0;
    if (comp.nScheme != INTERNET_SCHEME_HTTP && comp.nScheme != INTERNET_SCHEME_HTTPS) return 0;
    if (!path[0]) lstrcpyW(path, L"/");
    if (extra[0] && lstrlenW(path) + lstrlenW(extra) < (int)(sizeof(path) / sizeof(path[0]))) {
        lstrcatW(path, extra);
    }
    return configure_push_ex(host, comp.nPort, path, timeout_ms,
                             comp.nScheme == INTERNET_SCHEME_HTTPS);
}

static void handle_hook_sync(struct mg_connection *c, struct mg_http_message *hm) {
    int enable_http = json_boolish(hm, "enableHttp", 0);
    DWORD timeout = (DWORD)json_long(hm, "timeout", 250);
    int ok = 0;
    if (enable_http) {
        char *url = json_str(hm, "url", "");
        ok = configure_http_url(url, timeout);
        if (url) free(url);
    } else {
        char *ip = json_str(hm, "ip", "127.0.0.1");
        int port = (int)json_long(hm, "port", 19099);
        ok = configure_push_tcp(ip, (INTERNET_PORT)port, timeout);
        if (ip) free(ip);
    }
    if (ok) {
        set_receive_push_enabled(1);
        if (wxb_weixin_module() && !async_queue_hook_installed()) install_async_queue_hook();
        if (wxb_weixin_module() && !recv_hook_installed()) install_recv_hook();
        reply_msg(c, 1, "success");
    } else {
        reply_msg(c, 0, "invalid hookSyncMsg parameters");
    }
}

static void handle_room_members(struct mg_connection *c, struct mg_http_message *hm) {
    char seq[64];
    char *room = json_str(hm, "chatRoomId", NULL);
    ULONGLONG start;
    char *room_json = NULL;
    if (!room) room = json_str(hm, "roomId", NULL);
    if (!room || !room[0]) {
        reply_msg(c, 0, "missing chatRoomId");
        goto cleanup;
    }
    make_seq(seq, sizeof(seq));
    if (!start_room_list(seq, room)) {
        reply_msg(c, 0, "get_room_list busy_or_failed");
        goto cleanup;
    }
    start = GetTickCount64();
    while (GetTickCount64() - start < 31000ULL) {
        if (read_generated_json("room", seq, &room_json)) {
            reply_raw(c, 1, "success", room_json);
            goto cleanup;
        }
        Sleep(50);
    }
    reply_msg(c, 0, "get_room_list timeout");
cleanup:
    if (room_json) VirtualFree(room_json, 0, MEM_RELEASE);
    if (room) free(room);
}

static void handle_user_info(struct mg_connection *c) {
    char data[4096];
    if (wxb_self_profile_json(data, sizeof(data))) reply_raw(c, 1, "success", data);
    else reply_msg(c, 0, "not_logged_in");
}

static void handle_contact_list(struct mg_connection *c, struct mg_http_message *hm) {
    int limit = (int)json_long(hm, "limit", 5000);
    char *data = wxb_contact_list_json(limit);
    if (data) {
        reply_raw(c, 1, "success", data);
        VirtualFree(data, 0, MEM_RELEASE);
    } else {
        reply_msg(c, 0, "contact_pattern_not_found");
    }
}

static void handle_http_request(struct mg_connection *c, struct mg_http_message *hm) {
    if (mg_vcasecmp(&hm->method, "POST") != 0 && !uri_match(hm, "/api/checkLogin")) {
        reply_msg(c, 0, "not support get method,use post.");
        return;
    }
    if (uri_match(hm, "/api/checkLogin")) {
        reply_raw(c, wxb_weixin_module() ? 1 : 0, wxb_weixin_module() ? "success" : "fail", "{}");
    } else if (uri_match(hm, "/api/userInfo")) {
        handle_user_info(c);
    } else if (uri_match(hm, "/api/getContactList")) {
        handle_contact_list(c, hm);
    } else if (uri_match(hm, "/api/getContactProfile")) {
        handle_contact_profile(c, hm);
    } else if (uri_match(hm, "/api/getMemberFromChatRoom")) {
        handle_room_members(c, hm);
    } else if (uri_match(hm, "/api/hookSyncMsg")) {
        handle_hook_sync(c, hm);
    } else if (uri_match(hm, "/api/unhookSyncMsg")) {
        set_receive_push_enabled(0);
        reply_msg(c, 1, "success");
    } else if (uri_match(hm, "/api/acceptFriend")) {
        handle_accept_friend(c, hm);
    } else if (uri_match(hm, "/api/autoAcceptFriend")) {
        handle_auto_accept_friend(c, hm);
    } else if (uri_match(hm, "/api/sendTextMsg")) {
        char *wxid = json_str(hm, "wxid", "");
        char *msg = json_str(hm, "msg", "");
        int ok = send_text(wxid, msg);
        reply_msg(c, ok, ok ? "success" : "send_text failed");
        free(wxid); free(msg);
    } else if (uri_match(hm, "/api/sendAtText")) {
        char *room_id = json_str(hm, "chatRoomId", "");
        char *wxids = json_str(hm, "wxids", "");
        char *nickname = json_str(hm, "nickname", "");
        char *msg = json_str(hm, "msg", "");
        int ok = send_at_text(room_id, wxids, nickname, msg);
        reply_msg(c, ok, ok ? "success" : "send_at_text failed");
        free(room_id); free(wxids); free(nickname); free(msg);
    } else if (uri_match(hm, "/api/sendImagesMsg")) {
        char *wxid = json_str(hm, "wxid", "");
        char *path = json_str(hm, "imagePath", "");
        int ok = send_image(wxid, path);
        reply_msg(c, ok, ok ? "success" : "send_image failed");
        free(wxid); free(path);
    } else if (uri_match(hm, "/api/sendFileMsg")) {
        char *wxid = json_str(hm, "wxid", "");
        char *path = json_str(hm, "filePath", "");
        char prefixed[4096];
        char *p = prefixed;
        char *end = prefixed + sizeof(prefixed) - 1;
        append_raw(&p, end, "[[wx_source_file]]");
        append_raw(&p, end, path ? path : "");
        *p = 0;
        {
            int ok = send_image(wxid, prefixed);
            reply_msg(c, ok, ok ? "success" : "send_file failed");
        }
        free(wxid); free(path);
    } else if (uri_match(hm, "/api/sendQuoteMsg")) {
        char *wxid = json_str(hm, "wxid", "");
        char *msg = json_str(hm, "msg", "");
        char *quoted_content = json_str(hm, "quotedContent", "");
        char *quoted_from = json_str(hm, "quotedFrom", "");
        char *quoted_chat = json_str(hm, "quotedChat", "");
        char *msg_source = json_str(hm, "msgSource", "");
        char *source_to = json_str(hm, "sourceToWxid", "");
        int ok = send_quote_v2(wxid, msg,
                               json_i64(hm, "quotedMsgId", 0),
                               (int32_t)json_long(hm, "quotedLocalId", 0),
                               quoted_content, quoted_from,
                               (int)json_long(hm, "quotedType", 1),
                               (int32_t)json_long(hm, "createTime", 0),
                               quoted_chat, msg_source, source_to);
        reply_msg(c, ok, ok ? "success" : "send_quote failed");
        free(wxid); free(msg); free(quoted_content); free(quoted_from);
        free(quoted_chat); free(msg_source); free(source_to);
    } else if (uri_match(hm, "/api/sendImageQuoteMsg")) {
        /* 图片消息本身引用一条文字消息。
         * 校验失败直接拒绝,绝不静默降级成普通图片;发送路径不返回微信消息 ID,
         * 因此 messageId 一律为 null,不做任何编造。 */
        char *wxid = json_str(hm, "wxid", "");
        char *image_path = json_str(hm, "imagePath", "");
        char *quoted_content = json_str(hm, "quotedContent", "");
        char *quoted_from = json_str(hm, "quotedFrom", "");
        char *quoted_chat = json_str(hm, "quotedChat", "");
        char *msg_source = json_str(hm, "msgSource", "");
        char *source_to = json_str(hm, "sourceToWxid", "");
        const int64_t q_svrid = json_i64(hm, "quotedMsgId", 0);
        const int32_t q_local = (int32_t)json_long(hm, "quotedLocalId", 0);
        const int32_t q_ctime = (int32_t)json_long(hm, "createTime", 0);
        const int q_type = (int)json_long(hm, "quotedType", 1);
        const char *err = NULL;
        if (!wxid[0] || !image_path[0]) {
            err = "validation: wxid and imagePath are required";
        } else if (!q_svrid || !q_local || !q_ctime) {
            err = "validation: quotedMsgId / quotedLocalId / createTime are required and must be non-zero";
        } else if (!quoted_from[0]) {
            err = "validation: quotedFrom is required (source message sender)";
        } else if (q_type != 1) {
            err = "validation: quotedType must be 1 (text) for an image quoting a text message";
        }
        if (err) {
            reply_msg(c, -1, err);
        } else {
            char data[384];
            int ok = send_image_quote(wxid, image_path, q_svrid, q_local,
                                      quoted_content, quoted_from, q_type, q_ctime,
                                      quoted_chat, msg_source, source_to);
            wsprintfA(data,
                      "{\"accepted\":%d,\"messageId\":null,\"stage\":\"%s\",\"note\":\"%s\"}",
                      ok ? 1 : 0,
                      ok ? "bridge-accepted" : "send-rejected",
                      ok ? "handed to WeChat send path; this path does not return a message id"
                         : "WeChat send path refused; nothing was sent, no plain image fallback");
            reply_raw(c, ok ? 1 : -1, ok ? "accepted" : "send failed, nothing sent", data);
        }
        free(wxid); free(image_path); free(quoted_content); free(quoted_from);
        free(quoted_chat); free(msg_source); free(source_to);
    } else if (uri_match(hm, "/api/sendCardMsg")) {
        char *wxid = json_str(hm, "wxid", "");
        char *title = json_str(hm, "title", "");
        char *digest = json_str(hm, "digest", "");
        char *url = json_str(hm, "url", "");
        char *thumb = json_str(hm, "thumbUrl", "");
        char *appid = json_str(hm, "appid", "");
        char *src_user = json_str(hm, "sourceUsername", "");
        char *src_name = json_str(hm, "sourceDisplayName", "");
        int ok = send_card(wxid, title, digest, (int)json_long(hm, "subtype", 0),
                           "view", url, "", "", thumb, appid, src_user, src_name);
        reply_msg(c, ok, ok ? "success" : "send_card failed");
        free(wxid); free(title); free(digest); free(url); free(thumb); free(appid); free(src_user); free(src_name);
    } else if (uri_match(hm, "/api/forwardPublicMsg")) {
        handle_forward_public_msg(c, hm);
    } else if (uri_match(hm, "/api/sendXmlMsg")) {
        char *wxid = json_str(hm, "wxid", "");
        char *xml = json_str(hm, "xml", "");
        int ok = send_appmsg_xml(wxid, xml, (int)json_long(hm, "type", 0), "", "", "");
        reply_msg(c, ok, ok ? "success" : "send_appmsg_xml failed");
        free(wxid); free(xml);
    } else if (uri_match(hm, "/api/sendCustomEmotion")) {
        char *wxid = json_str(hm, "wxid", "");
        char *path = json_str(hm, "filePath", "");
        char prefixed[4096];
        char *p = prefixed;
        char *end = prefixed + sizeof(prefixed) - 1;
        append_raw(&p, end, "[[wx_source_emoji]]");
        append_raw(&p, end, path ? path : "");
        *p = 0;
        {
            int ok = send_image(wxid, prefixed);
            reply_msg(c, ok, ok ? "success" : "send_custom_emotion failed");
        }
        free(wxid); free(path);
    } else if (uri_match(hm, "/api/antiRevoke")) {
        int ok = set_anti_revoke(json_boolish(hm, "enable", 1));
        reply_msg(c, ok, ok ? "success" : "anti_revoke failed");
    } else if (uri_match(hm, "/api/openUrl")) {
        char seq[64];
        char *url = json_str(hm, "url", "");
        make_seq(seq, sizeof(seq));
        reply_msg(c, open_url_to_result(seq, url), "success");
        free(url);
    } else if (uri_match(hm, "/api/modifyRemark")) {
        char seq[64];
        char *wxid = json_str(hm, "wxid", "");
        char *remark = json_str(hm, "remark", "");
        make_seq(seq, sizeof(seq));
        reply_msg(c, modify_remark_task_to_result(seq, wxid, remark, (int)json_long(hm, "timeout", 15000)), "success");
        free(wxid); free(remark);
    } else if (uri_match(hm, "/api/createChatRoom")) {
        static const char *const topic_keys[] = {"name", "topic", "chatRoomName"};
        char seq[64];
        char *members = json_string_list(hm, "memberIds");
        char *topic = json_first_nonempty(hm, topic_keys, 3);
        int ok;
        make_seq(seq, sizeof(seq));
        ok = create_chatroom_to_result(seq,
                                       members ? members : "",
                                       topic ? topic : "",
                                       (int)json_long(hm, "timeout", 15000));
        reply_seq_result(c, seq, ok, ok ? "success" : "create_chatroom failed");
        if (members) free(members);
        if (topic) free(topic);
    } else if (uri_match(hm, "/api/modifyChatRoomName")) {
        char seq[64];
        char *room = json_str(hm, "chatRoomId", "");
        char *name = json_str(hm, "name", "");
        make_seq(seq, sizeof(seq));
        reply_msg(c, modify_chatroom_topic_to_result(seq, room, name, (int)json_long(hm, "timeout", 15000)), "success");
        free(room); free(name);
    } else if (uri_match(hm, "/api/publishChatRoomAnnouncement")) {
        char seq[64];
        char *room = json_str(hm, "chatRoomId", "");
        char *announcement = json_str(hm, "announcement", "");
        int ok;
        make_seq(seq, sizeof(seq));
        ok = publish_chatroom_announcement_to_result(
            seq,
            room,
            announcement,
            (int)json_long(hm, "timeout", 15000));
        reply_seq_result(c, seq, ok, ok ? "success" : "publish_chatroom_announcement failed");
        free(room); free(announcement);
    } else if (uri_match(hm, "/api/addMemberToChatRoom")) {
        char seq[64];
        char *room = json_str(hm, "chatRoomId", "");
        char *member = json_str(hm, "memberIds", "");
        make_seq(seq, sizeof(seq));
        reply_msg(c, add_chatroom_member_to_result(seq, room, member, (int)json_long(hm, "timeout", 15000)), "success");
        free(room); free(member);
    } else if (uri_match(hm, "/api/InviteMemberToChatRoom")) {
        char seq[64];
        char *room = json_str(hm, "chatRoomId", "");
        char *member = json_str(hm, "memberIds", "");
        make_seq(seq, sizeof(seq));
        reply_msg(c, invite_chatroom_member_to_result(seq, room, member, (int)json_long(hm, "timeout", 15000)), "success");
        free(room); free(member);
    } else if (uri_match(hm, "/api/delMemberFromChatRoom")) {
        char seq[64];
        char *room = json_str(hm, "chatRoomId", "");
        char *member = json_str(hm, "memberIds", "");
        make_seq(seq, sizeof(seq));
        reply_msg(c, del_chatroom_member_to_result(seq, room, member, (int)json_long(hm, "timeout", 15000)), "success");
        free(room); free(member);
    } else if (uri_match(hm, "/api/quitChatRoom")) {
        char seq[64];
        char *room = json_str(hm, "chatRoomId", "");
        char *self = json_str(hm, "selfWxid", "");
        make_seq(seq, sizeof(seq));
        reply_msg(c, leave_chatroom_to_result(seq, room, self, (int)json_long(hm, "timeout", 15000), 0), "success");
        free(room); free(self);
    } else if (uri_match(hm, "/api/modifyNickname")) {
        char seq[64];
        char *room = json_str(hm, "chatRoomId", "");
        char *name = json_str(hm, "nickName", "");
        int ok;
        make_seq(seq, sizeof(seq));
        ok = modify_chatroom_self_display_name_to_result(seq, room, name, (int)json_long(hm, "timeout", 15000));
        reply_seq_result(c, seq, ok, ok ? "success" : "modify_room_nickname failed");
        free(room); free(name);
    } else if (uri_match(hm, "/api/getA8Key")) {
        char seq[64];
        char *url = json_str(hm, "url", "");
        int ok;
        make_seq(seq, sizeof(seq));
        ok = get_a8key_task_to_result(seq, url,
                                      (int)json_long(hm, "scene", 3),
                                      (int)json_long(hm, "urlType", 0),
                                      (int)json_long(hm, "timeout", 15000));
        reply_seq_result(c, seq, ok, ok ? "success" : "get_a8key failed");
        free(url);
    } else if (uri_match(hm, "/api/h5ExtTransfer")) {
        char seq[64];
        char *url = json_str(hm, "url", "");
        char *req = json_str(hm, "reqData", "");
        char *args = json_str(hm, "jsapiArgs", "");
        char *session = json_str(hm, "sessionId", "");
        char *cgi = json_str(hm, "transferCgiUrl", "");
        int ok;
        make_seq(seq, sizeof(seq));
        ok = h5ext_transfer_to_result(seq, url,
                                      (int)json_long(hm, "cmdId", 2975),
                                      req, args,
                                      (int)json_long(hm, "timeout", 15000),
                                      (int)json_long(hm, "jsapiControlByte", 400),
                                      session,
                                      (int)json_long(hm, "reportSecurity", 1),
                                      cgi);
        reply_seq_result(c, seq, ok, ok ? "success" : "h5ext_transfer failed");
        free(url); free(req); free(args); free(session); free(cgi);
    } else if (uri_match(hm, "/api/searchFinder")) {
        char seq[64];
        char *query = json_str(hm, "query", "");
        char *session_id = json_str(hm, "sessionId", "");
        char *request_id = json_str(hm, "requestId", "");
        int ok;
        make_seq(seq, sizeof(seq));
        ok = finder_search_to_result(seq,
                                     query,
                                     (int)json_long(hm, "offset", 0),
                                     (int)json_long(hm, "scene", 88),
                                     (int)json_long(hm, "businessType", 0),
                                     (int)json_long(hm, "subType", 0),
                                     (int)json_long(hm, "currentPage", 0),
                                     session_id,
                                     request_id,
                                     (int)json_long(hm, "timeout", 15000));
        reply_seq_result(c, seq, ok, ok ? "success" : "finder_search failed");
        free(query); free(session_id); free(request_id);
    } else if (uri_match(hm, "/api/enterFinderLive")) {
        static const char *const url_keys[] = {"liveUrl", "url"};
        static const char *const object_id_keys[] = {"objectId", "object_id"};
        static const char *const nonce_keys[] = {
            "objectNonceId", "nonceId", "object_nonce_id", "nonce_id"
        };
        static const char *const context_keys[] = {"contextId", "context_id"};
        static const char *const entrance_keys[] = {"entranceId", "entrance_id"};
        static const char *const sub_entrance_keys[] = {
            "subEntranceId", "sub_entranceid", "sub_entrance_id"
        };
        char seq[64];
        char scene_buf[32];
        char *url = json_first_nonempty(hm, url_keys, 2);
        char *object_id = json_first_scalar(hm, object_id_keys, 2);
        char *nonce_id = json_first_scalar(hm, nonce_keys, 4);
        char *context_id = json_first_nonempty(hm, context_keys, 2);
        char *entrance_id = json_first_nonempty(hm, entrance_keys, 2);
        char *sub_entrance_id = json_first_nonempty(hm, sub_entrance_keys, 3);
        const char *effective_entrance_id;
        int ok;
        wsprintfA(scene_buf, "%ld", json_long(hm, "scene", 88));
        effective_entrance_id = entrance_id[0] ? entrance_id : scene_buf;
        make_seq(seq, sizeof(seq));
        ok = finder_enter_live_to_result(seq, url, object_id, nonce_id,
                                         context_id, effective_entrance_id,
                                         sub_entrance_id,
                                         json_boolish(hm, "muted", 0));
        reply_seq_result(c, seq, ok, ok ? "success" : "finder_live_enter failed");
        free(url); free(object_id); free(nonce_id); free(context_id);
        free(entrance_id); free(sub_entrance_id);
    } else if (uri_match(hm, "/api/sendFinderLiveComment") ||
               uri_match(hm, "/api/postFinderLiveComment")) {
        static const char *const content_keys[] = {"content", "message", "text"};
        static const char *const live_id_keys[] = {"liveId", "live_id"};
        static const char *const object_id_keys[] = {"objectId", "object_id"};
        static const char *const nonce_keys[] = {
            "objectNonceId", "nonceId", "object_nonce_id", "nonce_id"
        };
        static const char *const cookie_keys[] = {"liveCookies", "live_cookies"};
        static const char *const identity_keys[] = {
            "identityUsername", "senderFinderUsername", "commentFinderUsername"
        };
        static const char *const context_keys[] = {"contextId", "context_id"};
        static const char *const entrance_keys[] = {"entranceId", "entrance_id"};
        static const char *const sub_entrance_keys[] = {
            "subEntranceId", "sub_entranceid", "sub_entrance_id"
        };
        static const char *const url_keys[] = {"liveUrl", "h5Url", "url"};
        static const char *const userver_keys[] = {"userverForH5", "userver_for_h5"};
        char seq[64];
        char *content = json_first_nonempty(hm, content_keys, 3);
        char *live_id = json_first_scalar(hm, live_id_keys, 2);
        char *object_id = json_first_scalar(hm, object_id_keys, 2);
        char *object_nonce_id = json_first_scalar(hm, nonce_keys, 4);
        char *live_cookies = json_first_scalar(hm, cookie_keys, 2);
        char *finder_username = json_first_nonempty(hm, identity_keys, 3);
        char *client_msg_id = json_str(hm, "clientMsgId", "");
        char *context_id = json_first_nonempty(hm, context_keys, 2);
        char *entrance_id = json_first_nonempty(hm, entrance_keys, 2);
        char *sub_entrance_id = json_first_nonempty(hm, sub_entrance_keys, 3);
        char *h5_url = json_first_nonempty(hm, url_keys, 3);
        char *userver_for_h5 = json_first_raw(hm, userver_keys, 2);
        int ok;
        if (!finder_username[0] && json_boolish(hm, "useFinderIdentity", 0)) {
            free(finder_username);
            finder_username = json_str(hm, "finderUsername", "");
        }
        make_seq(seq, sizeof(seq));
        ok = finder_post_live_comment_to_result(
            seq, content, live_id, object_id, object_nonce_id, live_cookies,
            finder_username, client_msg_id, context_id, entrance_id,
            sub_entrance_id, h5_url, userver_for_h5,
            (int)json_long(hm, "liveIdentity", 0),
            (int)json_long(hm, "timeout", 15000));
        reply_seq_result(c, seq, ok, ok ? "success" : "finder_live_comment failed");
        free(content); free(live_id); free(object_id); free(object_nonce_id);
        free(live_cookies); free(finder_username); free(client_msg_id);
        free(context_id); free(entrance_id); free(sub_entrance_id);
        free(h5_url); free(userver_for_h5);
    } else if (uri_match(hm, "/api/getFinderUserPage")) {
        static const char *const object_id_keys[] = {"objectId", "object_id"};
        static const char *const context_keys[] = {"contextId", "context_id"};
        static const char *const entrance_keys[] = {"entranceId", "entrance_id"};
        static const char *const finder_identity_keys[] = {
            "finderUsername", "finder_username"
        };
        static const char *const h5_url_keys[] = {"h5Url", "h5_url", "url"};
        char seq[64];
        char *username = json_str(hm, "username", "");
        char *object_id = json_first_scalar(
            hm, object_id_keys, (int)(sizeof(object_id_keys) / sizeof(object_id_keys[0])));
        char *context_id = json_first_nonempty(
            hm, context_keys, (int)(sizeof(context_keys) / sizeof(context_keys[0])));
        char *entrance_id = json_first_nonempty(
            hm, entrance_keys, (int)(sizeof(entrance_keys) / sizeof(entrance_keys[0])));
        char *finder_identity = json_first_nonempty(
            hm, finder_identity_keys,
            (int)(sizeof(finder_identity_keys) / sizeof(finder_identity_keys[0])));
        char *h5_url = json_first_nonempty(
            hm, h5_url_keys, (int)(sizeof(h5_url_keys) / sizeof(h5_url_keys[0])));
        int ok;
        /* `finderUsername` is the optional identity field.  If the caller
         * omitted the required `username`, accept it as a target alias but do
         * not send it back as the identity in the same request. */
        if (!username[0] && finder_identity[0]) {
            free(username);
            username = finder_identity;
            finder_identity = json_str(hm, "username", "");
        }
        make_seq(seq, sizeof(seq));
        if (json_boolish(hm, "useLegacyModel", 0)) {
            /* Retain an explicit escape hatch for the old 6611 diagnostic path. */
            char *extra_buffer = json_str(hm, "extraBuffer", NULL);
            if (!extra_buffer) extra_buffer = json_str(hm, "sceneNote", "");
            ok = finder_userpage_model_to_result(
                seq, username, extra_buffer,
                (int)json_long(hm, "timeout", 15000),
                (int)json_long(hm, "reqField4", -1),
                (int)json_long(hm, "reqField6", -1));
            free(extra_buffer);
        } else {
            ok = finder_userpage_h5_to_result(
                seq, username, object_id, context_id,
                entrance_id, finder_identity, h5_url,
                (int)json_long(hm, "scene", 140),
                (int)json_long(hm, "timeout", 15000));
        }
        reply_seq_result(c, seq, ok, ok ? "success" : "finder_userpage failed");
        free(username); free(object_id); free(context_id); free(entrance_id);
        free(finder_identity); free(h5_url);
    } else if (uri_match(hm, "/api/getSnsTimeline") ||
               uri_match(hm, "/api/getSNSFirstPage") ||
               uri_match(hm, "/api/getSNSNextPage")) {
        char seq[64];
        int ok;
        make_seq(seq, sizeof(seq));
        ok = get_sns_timeline_to_result(seq,
                                        (int)json_long(hm, "timeout", 15000),
                                        (int)json_long(hm, "richMode", 0));
        reply_seq_result(c, seq, ok, ok ? "success" : "get_sns_timeline failed");
    } else if (uri_match(hm, "/api/publishSnsText")) {
        char seq[64];
        char *content = json_str(hm, "content", "");
        int ok;
        make_seq(seq, sizeof(seq));
        ok = publish_sns_text_to_result(seq, content, (int)json_long(hm, "timeout", 15000));
        reply_seq_result(c, seq, ok, ok ? "success" : "publish_sns_text failed");
        free(content);
    } else if (uri_match(hm, "/api/publishSnsImage")) {
        char seq[64];
        char *content = json_str(hm, "content", "");
        char *image = json_str(hm, "imagePath", "");
        char *md5 = json_str(hm, "md5", "");
        char *cdn_url = json_str(hm, "cdnUrl", "");
        char *thumb = json_str(hm, "thumbUrl", NULL);
        int ok;
        if (!thumb) thumb = json_str(hm, "thumbPath", "");
        make_seq(seq, sizeof(seq));
        ok = publish_sns_image_to_result(seq, content, image, md5,
                                         (int)json_long(hm, "width", 0),
                                         (int)json_long(hm, "height", 0),
                                         (int)json_long(hm, "timeout", 15000),
                                         cdn_url, thumb);
        reply_seq_result(c, seq, ok, ok ? "success" : "publish_sns_image failed");
        free(content); free(image); free(md5); free(cdn_url); free(thumb);
    } else if (uri_match(hm, "/api/publishSnsVideo")) {
        char seq[64];
        char *content = json_str(hm, "content", "");
        char *video = json_str(hm, "videoPath", "");
        char *md5 = json_str(hm, "md5", "");
        char *thumb = json_str(hm, "thumbUrl", NULL);
        int ok;
        if (!thumb) thumb = json_str(hm, "thumbPath", "");
        make_seq(seq, sizeof(seq));
        ok = publish_sns_video_to_result(seq, content, video, md5,
                                         (int)json_long(hm, "width", 0),
                                         (int)json_long(hm, "height", 0),
                                         (int)json_long(hm, "timeout", 60000),
                                         thumb,
                                         (int)json_long(hm, "durationMs", 0));
        reply_seq_result(c, seq, ok, ok ? "success" : "publish_sns_video failed");
        free(content); free(video); free(md5); free(thumb);
    } else if (uri_match(hm, "/api/snsComment")) {
        char seq[64];
        char *sns_id = json_str(hm, "snsId", "");
        char *username = json_str(hm, "username", "");
        char *content = json_str(hm, "content", "");
        char *client_id = json_str(hm, "clientId", "");
        char *nonce = json_str(hm, "objectNonceId", "");
        char *parent = json_str(hm, "parentId", "");
        char *reply_id = json_str(hm, "replyCommentId", "");
        int ok;
        make_seq(seq, sizeof(seq));
        ok = sns_comment_to_result(seq, sns_id, username, content,
                                   client_id, nonce, parent, reply_id,
                                   (int)json_long(hm, "replySource", 0),
                                   (int)json_long(hm, "replyType", 0),
                                   (int)json_long(hm, "replyCreateTime", 0),
                                   (int)json_long(hm, "timeout", 15000));
        reply_seq_result(c, seq, ok, ok ? "success" : "sns_comment failed");
        free(sns_id); free(username); free(content); free(client_id); free(nonce); free(parent); free(reply_id);
    } else if (uri_match(hm, "/api/snsLike")) {
        char seq[64];
        char *sns_id = json_str(hm, "snsId", "");
        char *username = json_str(hm, "username", "");
        char *nonce = json_str(hm, "objectNonceId", "");
        int ok;
        make_seq(seq, sizeof(seq));
        ok = sns_like_to_result(seq, sns_id, username, nonce, (int)json_long(hm, "timeout", 15000));
        reply_seq_result(c, seq, ok, ok ? "success" : "sns_like failed");
        free(sns_id); free(username); free(nonce);
    } else if (uri_match(hm, "/api/snsDelete")) {
        char seq[64];
        char *sns_id = json_str(hm, "snsId", "");
        char *username = json_str(hm, "username", "");
        char *nonce = json_str(hm, "objectNonceId", "");
        int ok;
        make_seq(seq, sizeof(seq));
        ok = sns_delete_to_result(seq, sns_id, username, nonce, (int)json_long(hm, "timeout", 15000));
        reply_seq_result(c, seq, ok, ok ? "success" : "sns_delete failed");
        free(sns_id); free(username); free(nonce);
    } else if (uri_match(hm, "/api/snsSetVisibility")) {
        char seq[64];
        char *sns_id = json_str(hm, "snsId", "");
        char *username = json_str(hm, "username", "");
        char *nonce = json_str(hm, "objectNonceId", "");
        char *client_id = json_str(hm, "clientId", "");
        char *object_xml = json_str(hm, "objectXml", "");
        unsigned int visibility = (unsigned int)json_long(hm, "visibilityState", 2);
        int ok;
        make_seq(seq, sizeof(seq));
        if (visibility) {
            ok = sns_set_visibility_to_result(seq, sns_id, username, nonce, client_id,
                                              object_xml, visibility,
                                              (int)json_long(hm, "timeout", 15000));
        } else {
            ok = sns_set_private_to_result(seq, sns_id, username, nonce, client_id,
                                           object_xml,
                                           (int)json_long(hm, "timeout", 15000));
        }
        reply_seq_result(c, seq, ok, ok ? "success" : "sns_set_visibility failed");
        free(sns_id); free(username); free(nonce); free(client_id); free(object_xml);
    } else if (uri_match(hm, "/api/acceptTransfer")) {
        char seq[64];
        char *transfer_id = json_str(hm, "transferId", "");
        char *transaction_id = json_str(hm, "transactionId", "");
        char *sender = json_str(hm, "sender", "");
        int ok;
        make_seq(seq, sizeof(seq));
        ok = accept_transfer_to_result(seq, transfer_id, transaction_id,
                                       (int32_t)json_long(hm, "invalidTime", 0),
                                       sender);
        reply_seq_result(c, seq, ok, ok ? "success" : "accept_transfer failed");
        free(transfer_id); free(transaction_id); free(sender);
    } else if (uri_match(hm, "/api/refuseTransfer")) {
        char seq[64];
        char *transfer_id = json_str(hm, "transferId", "");
        char *sender = json_str(hm, "sender", "");
        int ok;
        make_seq(seq, sizeof(seq));
        ok = refuse_transfer_to_result(seq, transfer_id,
                                       (int32_t)json_long(hm, "invalidTime", 0),
                                       sender);
        reply_seq_result(c, seq, ok, ok ? "success" : "refuse_transfer failed");
        free(transfer_id); free(sender);
    } else if (uri_match(hm, "/api/voiceTranscribe")) {
        char seq[64];
        int ok;
        make_seq(seq, sizeof(seq));
        ok = voice_transcribe_to_result(seq,
                                        json_i64(hm, "msgId", 0),
                                        (int32_t)json_long(hm, "localId", 0),
                                        (int)json_long(hm, "activeSubmit", 0));
        reply_seq_result(c, seq, ok, ok ? "success" : "voice_transcribe failed");
    } else if (uri_match(hm, "/api/downloadSnsMediaUrl")) {
        char seq[64];
        char *url = json_str(hm, "url", "");
        char *save = json_str(hm, "savePath", "");
        int ok;
        make_seq(seq, sizeof(seq));
        ok = download_sns_media_url_to_result(seq, url, save, (int)json_long(hm, "timeout", 15000));
        reply_seq_result(c, seq, ok, ok ? "success" : "download_sns_media_url failed");
        free(url); free(save);
    } else if (uri_match(hm, "/api/downloadSnsMediaTask")) {
        char seq[64];
        char *sns_id = json_str(hm, "snsId", "");
        char *media_id = json_str(hm, "mediaId", "");
        char *url = json_str(hm, "url", "");
        char *save = json_str(hm, "savePath", "");
        int ok;
        make_seq(seq, sizeof(seq));
        ok = download_sns_media_task_to_result(seq, url, sns_id, media_id, save,
                                               json_i64(hm, "totalSize", 0),
                                               (int)json_long(hm, "timeout", 15000));
        reply_seq_result(c, seq, ok, ok ? "success" : "download_sns_media_task failed");
        free(sns_id); free(media_id); free(url); free(save);
    } else if (uri_match(hm, "/api/downloadSnsMediaFullTask")) {
        char seq[64];
        char *sns_id = json_str(hm, "snsId", "");
        char *media_id = json_str(hm, "mediaId", "");
        char *url = json_str(hm, "url", "");
        char *thumb = json_str(hm, "thumbUrl", "");
        char *save = json_str(hm, "savePath", "");
        int ok;
        make_seq(seq, sizeof(seq));
        ok = download_sns_media_full_task_to_result(seq, url, sns_id, media_id, thumb,
                                                    save, json_i64(hm, "totalSize", 0),
                                                    (int)json_long(hm, "taskType", 0),
                                                    (int)json_long(hm, "timeout", 15000));
        reply_seq_result(c, seq, ok, ok ? "success" : "download_sns_media_full_task failed");
        free(sns_id); free(media_id); free(url); free(thumb); free(save);
    } else if (uri_match(hm, "/api/decodeImage")) {
        char *input = json_str(hm, "filePath", "");
        char *output = json_str(hm, "storeDir", "");
        int ok = decode_image_internal(input, output);
        reply_msg(c, ok, ok ? "success" : "decode_image failed");
        free(input); free(output);
    } else if (uri_match(hm, "/api/downloadAttach")) {
        char seq[64];
        char msgid[64];
        char *content = json_str(hm, "content", "");
        char *source_from = json_str(hm, "sourceFrom", "");
        char *source_to = json_str(hm, "sourceTo", "");
        char *source_sender = json_str(hm, "sourceSender", "");
        char *msgsource = json_str(hm, "msgSource", "");
        int source_type = (int)json_long(hm, "sourceType", 0);
        int ok;
        if (source_type != 0 && source_type != 3) {
            reply_msg(c, 0, "attachment download is not implemented for this sourceType");
            free(content); free(source_from); free(source_to); free(source_sender); free(msgsource);
            return;
        }
        make_seq(seq, sizeof(seq));
        i64toa10(json_i64(hm, "msgId", 0), msgid);
        ok = download_image_task_probe_to_result(seq, msgid,
                                                 (int)json_long(hm, "timeout", 5000),
                                                 content,
                                                 source_type,
                                                 (int)json_long(hm, "sourceSubtype", 0),
                                                 (int)json_long(hm, "sourceLocalId", 0),
                                                 (int32_t)json_long(hm, "sourceTime", 0),
                                                 source_from, source_to, source_sender, msgsource);
        reply_seq_result(c, seq, ok, ok ? "success" : "download_attach failed");
        free(content); free(source_from); free(source_to); free(source_sender); free(msgsource);
    } else if (uri_match(hm, "/api/forwardMsg")) {
        handle_forward_msg_by_id(c, hm, "forward_msg failed");
    } else if (uri_match(hm, "/api/forwardPublicMsgByMsgId")) {
        handle_forward_msg_by_id(c, hm, "forward_public_msg_by_msgid failed");
    } else {
        reply_msg(c, 0, "invalid url, please check url");
    }
}

void wxb_http_event_handler(struct mg_connection *c, int ev, void *ev_data, void *fn_data) {
    (void)fn_data;
    if (ev == MG_EV_HTTP_MSG) {
        struct mg_http_message *hm = (struct mg_http_message *)ev_data;
        if (mg_http_match_uri(hm, "/api/*")) {
            handle_http_request(c, hm);
        } else {
            reply_msg(c, 0, "invalid url, please check url");
        }
    }
}
