#include "../include/wx_bridge_hongbao_status.h"
#include "../include/wx_bridge_hongbao_closed_loop.h"
#include "../include/wx_bridge_hongbao_cold.h"
#include "../include/wx_bridge_hongbao_runtime.h"
#include "wx_bridge_hongbao_runtime_internal.h"
#include "../include/wx_bridge_debug_log.h"
#include "../include/wx_bridge_file_io.h"
#include "../include/wx_bridge_memory.h"
#include "../include/wx_bridge_offsets.h"
#include "../include/wx_bridge_paths.h"
#include "../include/wx_bridge_protocol.h"
#include "../include/wx_bridge_runtime.h"
#include "../include/wx_bridge_text.h"
void wxb_hb_write_state(void) {
    char json[2048];
    char *p = json;
    char *end = json + sizeof(json) - 1;
    append_raw(&p, end, "{\"ok\":true,\"reason\":\"hongbao automation default enabled\"}");
    *p = 0;
    write_file_bytes(HB_STATE_FILE, json, (DWORD)(p - json));
}

int wxb_hb_is_receive_candidate(int msg_type_int, int msg_subtype_int, const char *content) {
    if (msg_type_int == 49 && msg_subtype_int == 2001) return 1;
    if (!content || !content[0]) return 0;
    if (str_has(content, "receivehongbao")) return 1;
    if (str_has(content, "wxhb_personalreceive")) return 1;
    if (str_has(content, "<nativeurl") && str_has(content, "sendid=")) return 1;
    return 0;
}

void wxb_hb_log_receive_candidate(int64_t msg_base,
                                     const char *id_s,
                                     const char *local_id_s,
                                     int msg_type_int,
                                     int msg_subtype_int,
                                     const char *from,
                                     const char *self_id,
                                     const char *peer_wxid,
                                     const char *sender_wxid,
                                     const char *direction,
                                     int is_incoming,
                                     int is_outgoing,
                                     const char *content) {
    char native_url[2048] = {0};
    char page_url[2048] = {0};
    char sendid[128] = {0};
    char channelid[32] = {0};
    char msgtype[32] = {0};
    char sendusername[256] = {0};
    char sign[512] = {0};
    char total_num[32] = {0};
    char snippet[512] = {0};
    void *bt[16] = {0};
    USHORT bt_count = 0;
    ULONGLONG now_tick = GetTickCount64();
    HbPageCaptureSnapshot page;
    ULONGLONG last_tick = 0;
    int last_match_current_sendid = 0;
    int last_pagectx_stale = 1;
    char line[16384];
    char *p = line;
    char *end = line + sizeof(line) - 1;
    char num[64];
    int64_t page_ctx = (int64_t)hb_runtime_get_last_page_ctx();
    int64_t receive_result = page_ctx ? r64((void *)(page_ctx + WX_HB_PAGE_RECEIVE_RESULT_OFF)) : 0;
    int64_t timing_obj = receive_result ? receive_result + WX_HB_RECEIVE_TIMING_OFF : 0;
    int64_t timing_ptr = timing_obj ? r64((void *)timing_obj) : 0;
    int64_t timing_len = timing_obj ? r64((void *)(timing_obj + 0x10)) : 0;
    int64_t timing_cap = timing_obj ? r64((void *)(timing_obj + 0x18)) : 0;

    if (!hb_is_receive_candidate(msg_type_int, msg_subtype_int, content)) return;
    /* Cold path does not need page_ctx; skip multi-KB sync dump + misleading log. */
    if (wxb_hb_cold_enabled()) return;
    hb_runtime_get_page_snapshot(&page);
    last_tick = page.last_capture_tick;

    extract_between_markers(content, "<nativeurl><![CDATA[", "]]></nativeurl>", native_url, sizeof(native_url));
    if (!native_url[0]) {
        extract_between_markers(content, "<nativeurl>", "</nativeurl>", native_url, sizeof(native_url));
    }
    extract_between_markers(content, "<url><![CDATA[", "]]></url>", page_url, sizeof(page_url));
    if (!page_url[0]) {
        extract_between_markers(content, "<url>", "</url>", page_url, sizeof(page_url));
    }
    if (native_url[0]) {
        extract_query_value(native_url, "sendid", sendid, sizeof(sendid));
        extract_query_value(native_url, "channelid", channelid, sizeof(channelid));
        extract_query_value(native_url, "msgtype", msgtype, sizeof(msgtype));
        extract_query_value(native_url, "sendusername", sendusername, sizeof(sendusername));
        extract_query_value(native_url, "sign", sign, sizeof(sign));
        extract_query_value(native_url, "total_num", total_num, sizeof(total_num));
    }
    if (!sendid[0]) extract_query_value(page_url, "sendid", sendid, sizeof(sendid));
    if (!channelid[0]) extract_query_value(page_url, "channelid", channelid, sizeof(channelid));
    if (!msgtype[0]) extract_query_value(page_url, "msgtype", msgtype, sizeof(msgtype));
    if (!sign[0]) extract_query_value(page_url, "sign", sign, sizeof(sign));
    if (!sendusername[0] && sender_wxid) safe_copy(sendusername, sizeof(sendusername), sender_wxid);
    safe_copy(snippet, sizeof(snippet), content ? content : "");
    hb_runtime_record_receive(sendid,
                              1,
                              native_url,
                              page_url,
                              peer_wxid ? peer_wxid : "",
                              sender_wxid ? sender_wxid : "",
                              msg_base,
                              1,
                              id_s ? a2i64(id_s) : 0,
                              1,
                              local_id_s ? (LONG)a2i64(local_id_s) : 0,
                              0,
                              1);
    wxb_hb_closed_loop_note_message(sendid,
                                    id_s ? id_s : "",
                                    native_url,
                                    peer_wxid ? peer_wxid : "",
                                    sender_wxid ? sender_wxid : "");
    if (sendid[0] && page.last_pagectx_sendid[0] && str_eq(sendid, page.last_pagectx_sendid)) {
        last_match_current_sendid = 1;
        last_pagectx_stale = 0;
    }
    bt_count = CaptureStackBackTrace(0, 16, bt, NULL);

    append_raw(&p, end, "{\"event\":\"hb_recv_candidate\"");
    append_raw(&p, end, ",\"tid\":"); wsprintfA(num, "%lu", (unsigned long)GetCurrentThreadId()); append_raw(&p, end, num);
    append_raw(&p, end, ",\"msg_base\":\""); wsprintfA(num, "%I64x", msg_base); append_raw(&p, end, num);
    append_raw(&p, end, "\",\"msgId\":\""); append_escaped(&p, end, id_s ? id_s : "");
    append_raw(&p, end, "\",\"localId\":\""); append_escaped(&p, end, local_id_s ? local_id_s : "");
    append_raw(&p, end, "\",\"type\":"); wsprintfA(num, "%d", msg_type_int); append_raw(&p, end, num);
    append_raw(&p, end, ",\"subtype\":"); wsprintfA(num, "%d", msg_subtype_int); append_raw(&p, end, num);
    append_raw(&p, end, ",\"incoming\":"); append_raw(&p, end, is_incoming ? "true" : "false");
    append_raw(&p, end, ",\"outgoing\":"); append_raw(&p, end, is_outgoing ? "true" : "false");
    append_raw(&p, end, ",\"direction\":\""); append_escaped(&p, end, direction ? direction : "");
    append_raw(&p, end, "\",\"from\":\""); append_escaped(&p, end, from ? from : "");
    append_raw(&p, end, "\",\"to\":\""); append_escaped(&p, end, self_id ? self_id : "");
    append_raw(&p, end, "\",\"peer\":\""); append_escaped(&p, end, peer_wxid ? peer_wxid : "");
    append_raw(&p, end, "\",\"sender\":\""); append_escaped(&p, end, sender_wxid ? sender_wxid : "");
    append_raw(&p, end, "\",\"sendId\":\""); append_escaped(&p, end, sendid);
    append_raw(&p, end, "\",\"channelId\":\""); append_escaped(&p, end, channelid);
    append_raw(&p, end, "\",\"msgType\":\""); append_escaped(&p, end, msgtype);
    append_raw(&p, end, "\",\"sendUserName\":\""); append_escaped(&p, end, sendusername);
    append_raw(&p, end, "\",\"sign\":\""); append_escaped(&p, end, sign);
    append_raw(&p, end, "\",\"totalNum\":\""); append_escaped(&p, end, total_num);
    append_raw(&p, end, "\",\"nativeUrl\":\""); append_escaped(&p, end, native_url);
    append_raw(&p, end, "\",\"pageUrl\":\""); append_escaped(&p, end, page_url);
    append_raw(&p, end, "\",\"last_pagectx_sendId\":\""); append_escaped(&p, end, page.last_pagectx_sendid);
    append_raw(&p, end, "\",\"last_match_current_sendid\":"); append_raw(&p, end, last_match_current_sendid ? "true" : "false");
    append_raw(&p, end, ",\"last_pagectx_stale\":"); append_raw(&p, end, last_pagectx_stale ? "true" : "false");
    append_raw(&p, end, ",\"last_capture_age_ms\":"); wsprintfA(num, "%I64u", (unsigned __int64)(last_tick ? (now_tick - last_tick) : 0)); append_raw(&p, end, num);
    append_raw(&p, end, ",\"last_page_ctx\":\""); wsprintfA(num, "%I64x", page_ctx); append_raw(&p, end, num);
    append_raw(&p, end, "\",\"last_receive_result\":\""); wsprintfA(num, "%I64x", receive_result); append_raw(&p, end, num);
    append_raw(&p, end, "\",\"last_timing_obj\":\""); wsprintfA(num, "%I64x", timing_obj); append_raw(&p, end, num);
    append_raw(&p, end, "\",\"last_timing_ptr\":\""); wsprintfA(num, "%I64x", timing_ptr); append_raw(&p, end, num);
    append_raw(&p, end, "\",\"last_timing_len\":"); wsprintfA(num, "%I64x", timing_len); append_raw(&p, end, num);
    append_raw(&p, end, ",\"last_timing_cap\":"); wsprintfA(num, "%I64x", timing_cap); append_raw(&p, end, num);
    append_raw(&p, end, ",\"msg_probe\":{");
    append_raw(&p, end, "\"p0\":\""); wsprintfA(num, "%I64x", r64((void *)(msg_base + 0x00))); append_raw(&p, end, num);
    append_raw(&p, end, "\",\"p8\":\""); wsprintfA(num, "%I64x", r64((void *)(msg_base + 0x08))); append_raw(&p, end, num);
    append_raw(&p, end, "\",\"p18\":\""); wsprintfA(num, "%I64x", r64((void *)(msg_base + 0x18))); append_raw(&p, end, num);
    append_raw(&p, end, "\",\"p38\":\""); wsprintfA(num, "%I64x", r64((void *)(msg_base + 0x38))); append_raw(&p, end, num);
    append_raw(&p, end, "\",\"p160\":\""); wsprintfA(num, "%I64x", r64((void *)(msg_base + 0x160))); append_raw(&p, end, num);
    append_raw(&p, end, "\",\"p180\":\""); wsprintfA(num, "%I64x", r64((void *)(msg_base + 0x180))); append_raw(&p, end, num);
    append_raw(&p, end, "\",\"p1a8\":\""); wsprintfA(num, "%I64x", r64((void *)(msg_base + 0x1A8))); append_raw(&p, end, num);
    append_raw(&p, end, "\",\"p208\":\""); wsprintfA(num, "%I64x", r64((void *)(msg_base + 0x208))); append_raw(&p, end, num);
    append_raw(&p, end, "\",\"p240\":\""); wsprintfA(num, "%I64x", r64((void *)(msg_base + 0x240))); append_raw(&p, end, num);
    append_raw(&p, end, "\",\"p270\":\""); wsprintfA(num, "%I64x", r64((void *)(msg_base + 0x270))); append_raw(&p, end, num);
    append_raw(&p, end, "\"}");
    append_raw(&p, end, ",\"stack\":[");
    for (USHORT i = 0; i < bt_count; i++) {
        if (i) append_raw(&p, end, ",");
        append_raw(&p, end, "\"");
        wsprintfA(num, "%I64x", (int64_t)bt[i]);
        append_raw(&p, end, num);
        append_raw(&p, end, "\"");
    }
    append_raw(&p, end, "]");
    append_raw(&p, end, ",\"contentSnippet\":\""); append_escaped(&p, end, snippet);
    append_raw(&p, end, "\"}\r\n");
    *p = 0;
    append_file_bytes(HB_RECEIVE_LOG, line, (DWORD)(p - line));

    hb_dbg("[hb-recv] candidate seen but no live page_ctx yet; need page-entry/receivewxhb trigger before open producer");
}
