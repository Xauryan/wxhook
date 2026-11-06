#include "../include/wx_bridge_revoke_protocol.h"
#include "../include/wx_bridge_text.h"

int64_t wxb_parse_revoke_msgid(const char *xml) {
    const char *p;
    if (!xml || !*xml) return 0;
    p = xml;
    while (*p) {
        /* Match <newmsgid> (WeChat 4.x) or <msgid> for the original message ID. */
        int is_new = 0;
        if (p[0] == '<' && p[1] == 'n' && p[2] == 'e' && p[3] == 'w' &&
            p[4] == 'm' && p[5] == 's' && p[6] == 'g' &&
            p[7] == 'i' && p[8] == 'd' && p[9] == '>') {
            p += 10;
            is_new = 1;
        } else if (p[0] == '<' && p[1] == 'm' && p[2] == 's' && p[3] == 'g' &&
                   p[4] == 'i' && p[5] == 'd' && p[6] == '>') {
            p += 7;
            is_new = 1;
        }
        if (is_new) {
            int64_t v = 0;
            while (*p >= '0' && *p <= '9') {
                v = v * 10 + (int64_t)(*p - '0');
                p++;
            }
            if (v > 0) return v;
        }
        p++;
    }
    return 0;
}

void wxb_extract_tag_value(const char *xml, const char *tag, char *out, int max) {
    int i = 0;
    int tag_len = 0;
    const char *p;
    if (!xml || !tag || !out || max <= 0) return;
    out[0] = 0;
    while (tag[tag_len]) tag_len++;
    for (p = xml; *p; p++) {
        int j;
        if (*p != '<') continue;
        if (p[1] == '/' || p[1] != tag[0]) continue;
        j = 0;
        while (j < tag_len && p[1 + j] == tag[j]) j++;
        if (j != tag_len || p[1 + j] != '>') continue;
        p += 1 + tag_len + 1;
        while (*p && !(p[0] == '<' && p[1] == '/')) {
            if (i < max - 1) out[i++] = *p;
            p++;
        }
        out[i] = 0;
        return;
    }
}

int64_t wxb_parse_revoke_time(const char *xml) {
    char buf[64] = {0};
    int i;
    int64_t v = 0;
    wxb_extract_tag_value(xml, "revoketime", buf, sizeof(buf));
    if (!buf[0]) return 0;
    for (i = 0; buf[i] >= '0' && buf[i] <= '9'; i++) {
        v = v * 10 + (int64_t)(buf[i] - '0');
    }
    return v;
}

int wxb_is_revoke_msg(const char *content, int msg_type_int) {
    if (msg_type_int < 10000) return 0;
    if (!content || !*content) return 0;
    return wxb_str_has(content, "<revokemsg");
}

int wxb_is_revocable_msg(int msg_type_int) {
    return msg_type_int == 1 || msg_type_int == 3 || msg_type_int == 34 ||
           msg_type_int == 42 || msg_type_int == 43 || msg_type_int == 44 ||
           msg_type_int == 47 || msg_type_int == 48 || msg_type_int == 49 ||
           msg_type_int == 62;
}

int wxb_is_normal_msg(int msg_type_int) {
    return msg_type_int == 1;
}

const char *wxb_revoke_msg_type_label(int msg_type, int msg_subtype) {
    if (msg_type == 1) return "文本";
    if (msg_type == 3) return "图片";
    if (msg_type == 34) return "语音";
    if (msg_type == 42) return "名片";
    if (msg_type == 43 || msg_type == 44 || msg_type == 62) return "视频";
    if (msg_type == 47) return "表情";
    if (msg_type == 48) return "位置";
    if (msg_type == 49) {
        if (msg_subtype == 3) return "音乐卡片";
        if (msg_subtype == 5) return "链接卡片";
        if (msg_subtype == 6) return "文件";
        if (msg_subtype == 33) return "小程序";
        if (msg_subtype == 57) return "引用/回复";
        if (msg_subtype == 74) return "视频号/卡片";
        if (msg_subtype == 2001) return "红包";
        return "应用消息";
    }
    return "未知类型";
}

const char *wxb_revoke_msg_type_label_for_content(int msg_type, int msg_subtype,
                                                  const char *content) {
    if (wxb_appmsg_content_is_file_like(msg_type, msg_subtype, content)) return "文件";
    return wxb_revoke_msg_type_label(msg_type, msg_subtype);
}
