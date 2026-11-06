/*
 * Layer: revoke notice event bridge.
 * Reusable WeChat string readers/writers live in ../modules/wechat_string.c.
 */

#include "../include/wx_bridge_revoke_forward.h"
#include "../include/wx_bridge_file_io.h"
#include "../include/wx_bridge_memory.h"
#include "../include/wx_bridge_paths.h"
#include "../include/wx_bridge_protocol.h"
#include "../include/wx_bridge_runtime.h"
#include "../include/wx_bridge_text.h"

int emit_revoke_notice_event(int64_t orig_msgid, const char *talker, int64_t revoketime,
                                    const char *notice_prefix, const char *signature) {
    char orig_s[32], time_s[32], notice_id[96], room_id[256];
    char xml[2048], json[8192];
    char *xp, *xend, *p, *end;
    WCHAR path[1024];
    if (!orig_msgid || !talker || !talker[0]) return 0;

    i64toa10(orig_msgid, orig_s);
    i64toa10(revoketime > 0 ? revoketime : 0, time_s);
    wsprintfA(notice_id, "%s_%s_%s",
              notice_prefix && notice_prefix[0] ? notice_prefix : "revoke_real",
              orig_s, time_s);
    safe_copy(room_id, sizeof(room_id), is_chatroom_id(talker) ? talker : "");

    xp = xml;
    xend = xml + sizeof(xml) - 1;
    append_raw(&xp, xend, "<sysmsg type=\"revokemsg\"><revokemsg><session>");
    append_xml_escaped(&xp, xend, talker);
    append_raw(&xp, xend, "</session><newmsgid>");
    append_xml_escaped(&xp, xend, orig_s);
    append_raw(&xp, xend, "</newmsgid><fromusr>");
    append_xml_escaped(&xp, xend, talker);
    append_raw(&xp, xend, "</fromusr><replacemsg>撤回了一条消息</replacemsg><revoketime>");
    append_xml_escaped(&xp, xend, time_s);
    append_raw(&xp, xend, "</revoketime></revokemsg></sysmsg>");
    *xp = 0;

    p = json;
    end = json + sizeof(json) - 1;
    append_raw(&p, end, "{\"msgId\":\""); append_escaped(&p, end, notice_id);
    append_raw(&p, end, "\",\"type\":\"10000\"");
    append_raw(&p, end, ",\"timestamp\":\""); append_escaped(&p, end, time_s);
    append_raw(&p, end, "\",\"from\":\""); append_escaped(&p, end, talker);
    append_raw(&p, end, "\",\"fromWxId\":\""); append_escaped(&p, end, wxb_self_wxid());
    append_raw(&p, end, "\",\"rawFrom\":\""); append_escaped(&p, end, talker);
    append_raw(&p, end, "\",\"rawTo\":\""); append_escaped(&p, end, wxb_self_wxid());
    append_raw(&p, end, "\",\"isSendMsg\":false,\"isIncoming\":true");
    append_raw(&p, end, ",\"direction\":\"incoming\"");
    append_raw(&p, end, ",\"peerWxId\":\""); append_escaped(&p, end, talker);
    append_raw(&p, end, "\",\"roomId\":\""); append_escaped(&p, end, room_id);
    append_raw(&p, end, "\",\"senderWxId\":\""); append_escaped(&p, end, talker);
    append_raw(&p, end, "\",\"selfWxId\":\""); append_escaped(&p, end, wxb_self_wxid());
    append_raw(&p, end, "\",\"content\":\""); append_escaped(&p, end, xml);
    append_raw(&p, end, "\",\"signature\":\"");
    append_escaped(&p, end, signature && signature[0] ? signature : "revoke_real");
    append_raw(&p, end, "\"");
    append_raw(&p, end, ",\"revokeMsgId\":\""); append_escaped(&p, end, orig_s);
    append_raw(&p, end, "\",\"revokeSession\":\""); append_escaped(&p, end, talker);
    append_raw(&p, end, "\",\"revokeTime\":\""); append_escaped(&p, end, time_s);
    append_raw(&p, end, "\"}");
    *p = 0;

    if (!post_json_to_python(json, (DWORD)(p - json))) {
        wsprintfW(path, L"%s\\%S.txt", INBOX_DIR, notice_id);
        write_file_bytes(path, json, (DWORD)(p - json));
    }
    return 1;
}
