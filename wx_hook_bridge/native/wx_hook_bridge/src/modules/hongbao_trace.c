#include "../include/wx_bridge_hongbao_trace.h"
#include "../include/wx_bridge_hongbao_runtime.h"
#include "../include/wx_bridge_memory.h"
#include "../include/wx_bridge_text.h"
#include "../include/wx_bridge_wechat_string.h"

void append_hex_field(char **p, char *end, const char *name, int64_t value) {
    char num[64];
    append_raw(p, end, ",\"");
    append_raw(p, end, name);
    append_raw(p, end, "\":\"");
    wsprintfA(num, "%I64x", value);
    append_raw(p, end, num);
    append_raw(p, end, "\"");
}

void append_dec_field(char **p, char *end, const char *name, int64_t value) {
    char num[64];
    append_raw(p, end, ",\"");
    append_raw(p, end, name);
    append_raw(p, end, "\":");
    wsprintfA(num, "%I64d", value);
    append_raw(p, end, num);
}

void read_probe_string(int64_t addr, char *out, int max) {
    if (!out || max <= 0) return;
    out[0] = 0;
    if (!hb_runtime_verbose_enabled()) return;  /* Avoid UI-thread string probing unless explicitly requested. */
    if (!addr || !is_readable_ptr(addr, 0x20)) return;
    if (!wx_read_sso_str(addr, out, max)) {
        wx_read_str(addr, out, max);
    }
}

void hb_read_functional_string(int64_t addr, char *out, int max) {
    if (!out || max <= 0) return;
    out[0] = 0;
    if (!addr || !is_readable_ptr(addr, 0x20)) return;
    if (!wx_read_sso_str(addr, out, max)) {
        wx_read_str(addr, out, max);
    }
}

void hb_append_str_field(char **p, char *end, const char *name, const char *value) {
    append_raw(p, end, ",\"");
    append_raw(p, end, name ? name : "");
    append_raw(p, end, "\":\"");
    append_escaped(p, end, value ? value : "");
    append_raw(p, end, "\"");
}

void hb_append_probe_string_field(char **p, char *end, const char *name, int64_t addr) {
    char s[512] = {0};
    if (!addr || !is_readable_ptr(addr, 0x20)) return;
    read_probe_string(addr, s, sizeof(s));
    if (!s[0]) return;
    hb_append_str_field(p, end, name, s);
}

void hb_append_std_string_field(char **p, char *end, const char *prefix, int64_t addr) {
    char name[96];
    char s[512] = {0};
    int64_t len = 0;
    int64_t cap = 0;

    if (!prefix) prefix = "str";
    wsprintfA(name, "%s_addr", prefix);
    append_hex_field(p, end, name, addr);
    if (!addr || !is_readable_ptr(addr, 0x20)) return;

    len = r64((void *)(addr + 0x10));
    cap = r64((void *)(addr + 0x18));
    wsprintfA(name, "%s_ptr", prefix);
    append_hex_field(p, end, name, r64((void *)addr));
    wsprintfA(name, "%s_len", prefix);
    append_dec_field(p, end, name, len);
    wsprintfA(name, "%s_cap", prefix);
    append_dec_field(p, end, name, cap);

    hb_read_functional_string(addr, s, sizeof(s));
    if (s[0]) {
        wsprintfA(name, "%s_s", prefix);
        hb_append_str_field(p, end, name, s);
    }
}

void hb_extract_urls_from_text(const char *text,
                               char *native_url,
                               int native_max,
                               char *page_url,
                               int page_max) {
    const char *p;
    if (native_url && native_max > 0) native_url[0] = 0;
    if (page_url && page_max > 0) page_url[0] = 0;
    if (!text) return;

    if (native_url && native_max > 0) {
        extract_between_markers(text, "<nativeurl><![CDATA[", "]]></nativeurl>", native_url, native_max);
        if (!native_url[0]) extract_between_markers(text, "<nativeurl>", "</nativeurl>", native_url, native_max);
        if (!native_url[0]) {
            p = str_find_sub(text, "wxpay://c2cbizmessagehandler/hongbao/receivehongbao");
            if (p) copy_until_any(native_url, native_max, p, "\"'<>\r\n\t ");
        }
    }

    if (page_url && page_max > 0) {
        extract_between_markers(text, "<url><![CDATA[", "]]></url>", page_url, page_max);
        if (!page_url[0]) extract_between_markers(text, "<url>", "</url>", page_url, page_max);
    }
}

void hb_fill_query_fields(const char *native_url,
                          const char *page_url,
                          char *sendid,
                          int sendid_max,
                          char *channelid,
                          int channelid_max,
                          char *msgtype,
                          int msgtype_max,
                          char *sendusername,
                          int sendusername_max,
                          char *sign,
                          int sign_max,
                          char *total_num,
                          int total_num_max) {
    if (sendid && sendid_max > 0) sendid[0] = 0;
    if (channelid && channelid_max > 0) channelid[0] = 0;
    if (msgtype && msgtype_max > 0) msgtype[0] = 0;
    if (sendusername && sendusername_max > 0) sendusername[0] = 0;
    if (sign && sign_max > 0) sign[0] = 0;
    if (total_num && total_num_max > 0) total_num[0] = 0;

    if (native_url && native_url[0]) {
        extract_query_value(native_url, "sendid", sendid, sendid_max);
        extract_query_value(native_url, "channelid", channelid, channelid_max);
        extract_query_value(native_url, "msgtype", msgtype, msgtype_max);
        extract_query_value(native_url, "sendusername", sendusername, sendusername_max);
        extract_query_value(native_url, "sign", sign, sign_max);
        extract_query_value(native_url, "total_num", total_num, total_num_max);
    }
    if ((!sendid || !sendid[0]) && page_url && page_url[0]) extract_query_value(page_url, "sendid", sendid, sendid_max);
    if ((!channelid || !channelid[0]) && page_url && page_url[0]) extract_query_value(page_url, "channelid", channelid, channelid_max);
    if ((!msgtype || !msgtype[0]) && page_url && page_url[0]) extract_query_value(page_url, "msgtype", msgtype, msgtype_max);
    if ((!sendusername || !sendusername[0]) && page_url && page_url[0]) extract_query_value(page_url, "sendusername", sendusername, sendusername_max);
    if ((!sign || !sign[0]) && page_url && page_url[0]) extract_query_value(page_url, "sign", sign, sign_max);
    if ((!total_num || !total_num[0]) && page_url && page_url[0]) extract_query_value(page_url, "total_num", total_num, total_num_max);
}

void hb_append_obj_probe(char **p, char *end, const char *prefix, int64_t obj) {
    static const int offs[] = {
        0x08, 0x18, 0x38, 0x58, 0x68, 0x70, 0x78, 0x98,
        0xC0, 0xF0, 0x120, 0x160, 0x180, 0x1A0, 0x1A8,
        0x390, 0x398, 0x478, 0x500, 0x508, 0x510
    };
    char name[64];
    if (!hb_runtime_verbose_enabled()) return;
    if (!prefix) prefix = "obj";
    wsprintfA(name, "%s_vt", prefix);
    append_hex_field(p, end, name, obj ? r64((void *)obj) : 0);
    for (int i = 0; i < (int)(sizeof(offs) / sizeof(offs[0])); i++) {
        int off = offs[i];
        wsprintfA(name, "%s_%x", prefix, off);
        append_hex_field(p, end, name, obj ? r64((void *)(obj + off)) : 0);
        wsprintfA(name, "%s_%x_s", prefix, off);
        if (obj) hb_append_probe_string_field(p, end, name, obj + off);
    }
}

static int hb_trace_string_is_relevant(const char *s) {
    HbReceiveSnapshot recv;
    if (!s || !s[0]) return 0;
    hb_runtime_get_receive_snapshot(&recv);
    if (recv.sendid[0] && str_has(s, recv.sendid)) return 1;
    if (recv.peer[0] && str_has(s, recv.peer)) return 1;
    if (recv.sender[0] && str_has(s, recv.sender)) return 1;
    if (str_has(s, "receivehongbao")) return 1;
    if (str_has(s, "wxhb_personalreceive")) return 1;
    if (str_has(s, "<appmsg")) return 1;
    if (str_has(s, "sendid=")) return 1;
    return 0;
}

void hb_append_obj_string_hits(char **p, char *end, const char *name, int64_t obj) {
    static const int offs[] = {
        0x08, 0x18, 0x38, 0x58, 0x70, 0x78, 0x98, 0xB8,
        0xC0, 0xD8, 0xF0, 0x118, 0x138, 0x160, 0x180,
        0x1A0, 0x1A8, 0x1C0, 0x1E0, 0x208, 0x240, 0x270,
        0x288, 0x2A8, 0x2C0, 0x2E0, 0x320, 0x390, 0x398,
        0x478, 0x500, 0x508
    };
    char s[768];
    char num[32];
    int first = 1;
    if (!hb_runtime_verbose_enabled()) return;
    append_raw(p, end, ",\"");
    append_raw(p, end, name ? name : "string_hits");
    append_raw(p, end, "\":[");
    if (obj) {
        for (int i = 0; i < (int)(sizeof(offs) / sizeof(offs[0])); i++) {
            s[0] = 0;
            read_probe_string(obj + offs[i], s, sizeof(s));
            if (!hb_trace_string_is_relevant(s)) continue;
            if (!first) append_raw(p, end, ",");
            first = 0;
            append_raw(p, end, "{\"off\":\"");
            wsprintfA(num, "%x", offs[i]);
            append_raw(p, end, num);
            append_raw(p, end, "\",\"s\":\"");
            append_escaped(p, end, s);
            append_raw(p, end, "\"}");
        }
    }
    append_raw(p, end, "]");
}

void hb_append_recv_context(char **p, char *end) {
    HbReceiveSnapshot recv;
    int64_t recv_tick = 0;
    ULONGLONG now_tick = GetTickCount64();
    hb_runtime_get_receive_snapshot(&recv);
    recv_tick = recv.tick;
    append_hex_field(p, end, "recv_msg_base", recv.msg_base);
    append_dec_field(p, end, "recv_msgId", recv.msgid);
    append_dec_field(p, end, "recv_localId", recv.local_id);
    append_dec_field(p, end, "recv_age_ms", recv_tick ? (int64_t)(now_tick - (ULONGLONG)recv_tick) : -1);
    hb_append_str_field(p, end, "recv_sendId", recv.sendid);
    hb_append_str_field(p, end, "recv_nativeUrl", recv.native_url);
    hb_append_str_field(p, end, "recv_pageUrl", recv.page_url);
    hb_append_str_field(p, end, "recv_peer", recv.peer);
    hb_append_str_field(p, end, "recv_sender", recv.sender);
}

void hb_append_route_desc_fields(char **p, char *end, const char *prefix,
                                 int64_t desc, int include_payload_detail) {
    static const int str_offs[] = {0x10, 0x18, 0x50, 0x70, 0x98, 0xC0, 0x160, 0x180};
    char name[96];
    int32_t msg_type = 0;
    int32_t subtype = 0;
    int64_t packed_key = 0;
    int64_t payload_obj = 0;
    int64_t payload_ctl = 0;
    if (!hb_runtime_verbose_enabled()) return;
    if (!prefix) prefix = "desc";
    wsprintfA(name, "%s", prefix);
    append_hex_field(p, end, name, desc);
    if (!desc || !is_readable_ptr(desc, 0x280)) return;
    msg_type = r32((void *)(desc + 0x08));
    subtype = r32((void *)(desc + 0x0C));
    packed_key = (msg_type == 49) ? ((((int64_t)(uint32_t)subtype) << 32) | 0x31) : (uint32_t)msg_type;
    payload_obj = r64((void *)(desc + 0x270));
    payload_ctl = r64((void *)(desc + 0x278));
    wsprintfA(name, "%s_msg_type", prefix);
    append_dec_field(p, end, name, msg_type);
    wsprintfA(name, "%s_subtype", prefix);
    append_dec_field(p, end, name, subtype);
    wsprintfA(name, "%s_packed_key", prefix);
    append_hex_field(p, end, name, packed_key);
    wsprintfA(name, "%s_payload_obj", prefix);
    append_hex_field(p, end, name, payload_obj);
    wsprintfA(name, "%s_payload_ctl", prefix);
    append_hex_field(p, end, name, payload_ctl);
    for (int i = 0; i < (int)(sizeof(str_offs) / sizeof(str_offs[0])); i++) {
        wsprintfA(name, "%s_%x_s", prefix, str_offs[i]);
        hb_append_probe_string_field(p, end, name, desc + str_offs[i]);
    }
    if (include_payload_detail && payload_obj) {
        wsprintfA(name, "%s_payload", prefix);
        hb_append_obj_probe(p, end, name, payload_obj);
        wsprintfA(name, "%s_payload_string_hits", prefix);
        hb_append_obj_string_hits(p, end, name, payload_obj);
    }
}

int hb_source_extract_hongbao_context(int64_t source,
                                      char *sendid,
                                      int sendid_max,
                                      char *native_url,
                                      int native_max,
                                      char *page_url,
                                      int page_max,
                                      char *peer,
                                      int peer_max,
                                      char *sender,
                                      int sender_max,
                                      char *snippet,
                                      int snippet_max) {
    static const int offsets[] = {
        0x18, 0x38, 0x58, 0x78, 0x98, 0xB8, 0xD8, 0xF8,
        0x118, 0x138, 0x160, 0x180, 0x1A0, 0x1C0, 0x1E0,
        0x208, 0x240, 0x270
    };
    char fields[18][512];
    char content[8192] = {0};
    char parsed_native_url[2048] = {0};
    char parsed_page_url[2048] = {0};
    char channelid[32] = {0};
    char msgtype[32] = {0};
    char sendusername[256] = {0};
    char sign[512] = {0};
    char total_num[32] = {0};
    int field_count = (int)(sizeof(offsets) / sizeof(offsets[0]));

    if (sendid && sendid_max > 0) sendid[0] = 0;
    if (native_url && native_max > 0) native_url[0] = 0;
    if (page_url && page_max > 0) page_url[0] = 0;
    if (peer && peer_max > 0) peer[0] = 0;
    if (sender && sender_max > 0) sender[0] = 0;
    if (snippet && snippet_max > 0) snippet[0] = 0;
    if (!source || !is_readable_ptr(source, HB_SOURCE_SIZE)) return 0;

    for (int i = 0; i < field_count; i++) {
        fields[i][0] = 0;
        hb_read_functional_string(source + offsets[i], fields[i], sizeof(fields[i]));
        if (str_has(fields[i], "receivehongbao") ||
            str_has(fields[i], "wxhb_personalreceive") ||
            (str_has(fields[i], "<nativeurl") && str_has(fields[i], "sendid="))) {
            safe_copy(content, sizeof(content), fields[i]);
            break;
        }
    }
    if (!content[0]) return 0;

    hb_extract_urls_from_text(content,
                              parsed_native_url, sizeof(parsed_native_url),
                              parsed_page_url, sizeof(parsed_page_url));
    hb_fill_query_fields(parsed_native_url, parsed_page_url,
                         sendid, sendid_max,
                         channelid, sizeof(channelid),
                         msgtype, sizeof(msgtype),
                         sendusername, sizeof(sendusername),
                         sign, sizeof(sign),
                         total_num, sizeof(total_num));
    if (sendid && sendid_max > 0 && !sendid[0]) extract_query_value(content, "sendid", sendid, sendid_max);
    if (!sendusername[0]) extract_query_value(content, "sendusername", sendusername, sizeof(sendusername));
    if (native_url && native_max > 0) safe_copy(native_url, native_max, parsed_native_url);
    if (page_url && page_max > 0) safe_copy(page_url, page_max, parsed_page_url);
    if (peer && peer_max > 0 && field_count > 0) safe_copy(peer, peer_max, fields[0]);
    if (sender && sender_max > 0) {
        if (sendusername[0]) safe_copy(sender, sender_max, sendusername);
        else if (field_count > 2 && is_wxid_like(fields[2])) safe_copy(sender, sender_max, fields[2]);
        else if (field_count > 1 && is_wxid_like(fields[1])) safe_copy(sender, sender_max, fields[1]);
        else extract_group_sender(content, sender, sender_max);
    }
    if (snippet && snippet_max > 0) safe_copy(snippet, snippet_max, content);
    return sendid && sendid[0];
}

int hb_source_extract_hongbao(int64_t source, char *sendid, int sendid_max,
                              char *snippet, int snippet_max) {
    return hb_source_extract_hongbao_context(source,
                                             sendid,
                                             sendid_max,
                                             NULL,
                                             0,
                                             NULL,
                                             0,
                                             NULL,
                                             0,
                                             NULL,
                                             0,
                                             snippet,
                                             snippet_max);
}
