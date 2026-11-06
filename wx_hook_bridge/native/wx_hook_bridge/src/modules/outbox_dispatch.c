/*
 * Module: bridge outbox parser and command dispatch.
 */

#include "../include/wx_bridge_outbox_dispatch.h"
#include "../include/wx_bridge_a8key_task.h"
#include "../include/wx_bridge_anti_revoke.h"
#include "../include/wx_bridge_chatroom_member.h"
#include "../include/wx_bridge_file_io.h"
#include "../include/wx_bridge_finder_ops.h"
#include "../include/wx_bridge_forward_send.h"
#include "../include/wx_bridge_h5ext_transfer.h"
#include "../include/wx_bridge_media_ops.h"
#include "../include/wx_bridge_open_url.h"
#include "../include/wx_bridge_offsets.h"
#include "../include/wx_bridge_pay_transfer.h"
#include "../include/wx_bridge_paths.h"
#include "../include/wx_bridge_protocol.h"
#include "../include/wx_bridge_room_contacts.h"
#include "../include/wx_bridge_send_api.h"
#include "../include/wx_bridge_sns_comment.h"
#include "../include/wx_bridge_sns_publish.h"
#include "../include/wx_bridge_sns_timeline.h"
#include "../include/wx_bridge_text.h"
#include "../include/wx_bridge_voice_transcribe.h"
#include "../include/wx_bridge_worker_control.h"

static volatile LONG g_outbox_processing = 0;

void poll_outbox(void) {
    if (InterlockedCompareExchange(&g_outbox_processing, 1, 0) != 0) return;

    refresh_sns_media_probe_hook();
    refresh_sns_upload_trace_hooks();
    refresh_sns_publish_trace_hooks();
    poll_room_task();

    char data[65536];
    int n = read_file_bytes(OUTBOX_FILE, data, sizeof(data));
    if (n > 0) {
        char *parts[18] = {data};
        int pi = 0;
        for (int i = 0; i < n && pi < 17; i++) {
            if (data[i] == '\t') {
                data[i] = 0;
                parts[++pi] = data + i + 1;
            }
        }
        if (parts[1] && parts[2] && (parts[3] || str_eq(parts[1], "25"))) {
            trim_trailing_crlf(parts[2]);
            trim_trailing_crlf(parts[3]);
            trim_trailing_crlf(parts[4]);
            trim_trailing_crlf(parts[5]);
            trim_trailing_crlf(parts[6]);
            trim_trailing_crlf(parts[7]);
            trim_trailing_crlf(parts[8]);
            trim_trailing_crlf(parts[9]);
            trim_trailing_crlf(parts[10]);
            trim_trailing_crlf(parts[11]);
            trim_trailing_crlf(parts[12]);
            trim_trailing_crlf(parts[13]);
            trim_trailing_crlf(parts[14]);
            trim_trailing_crlf(parts[15]);
            trim_trailing_crlf(parts[16]);
            trim_trailing_crlf(parts[17]);
            if (str_eq(parts[1], "1")) {
                int ok = send_text(parts[2], parts[3]);
                write_status(ok ? "send_text ok\n" : "send_text failed\n");
                write_result(parts[0], ok, "send_text", ok ? "send_text ok" : "send_text failed");
            } else if (str_eq(parts[1], "6") || str_eq(parts[1], "7") ||
                       str_eq(parts[1], "10") || str_eq(parts[1], "11")) {
                int source_type = parts[4] ? (int)a2i64(parts[4]) : 1;
                int source_subtype = parts[5] ? (int)a2i64(parts[5]) : 0;
                int source_local_id = parts[6] ? (int)a2i64(parts[6]) : 0;
                int64_t source_msg_id = parts[7] ? a2i64(parts[7]) : 0;
                int32_t source_time = parts[8] ? (int32_t)a2i64(parts[8]) : 0;
                int native_variant = 0;
                int queue_status = 0;
                int native_retained = 0;
                int native_fallback_kind = 0;
                int high_entry = str_eq(parts[1], "10") || str_eq(parts[1], "11");
                int probe_only = str_eq(parts[1], "7") || str_eq(parts[1], "11");
                int ok = high_entry
                    ? send_forward_high_native(
                        parts[2],                  /* target */
                        parts[3],                  /* original content/xml, logged only for high-entry */
                        source_type,
                        source_subtype,
                        source_local_id,
                        source_msg_id,
                        source_time,
                        parts[9] ? parts[9] : "",  /* source from/chat */
                        parts[10] ? parts[10] : "", /* source to/self */
                        parts[11] ? parts[11] : "", /* source sender */
                        parts[12] ? parts[12] : "", /* source msgsource */
                        &native_variant,
                        &queue_status,
                        &native_retained,
                        &native_fallback_kind,
                        probe_only
                    )
                    : send_forward_any_native(
                        parts[2],                  /* target */
                        parts[3],                  /* original content/xml */
                        source_type,
                        source_subtype,
                        source_local_id,
                        source_msg_id,
                        source_time,
                        parts[9] ? parts[9] : "",  /* source from/chat */
                        parts[10] ? parts[10] : "", /* source to/self */
                        parts[11] ? parts[11] : "", /* source sender */
                        parts[12] ? parts[12] : "", /* source msgsource */
                        &native_variant,
                        &queue_status,
                        probe_only
                    );
                if (!high_entry) {
                    native_fallback_kind = 3;
                }
                if (ok) {
                    const char *msg = high_entry
                        ? (probe_only ? "forward_high_probe native ok" : "forward_high_entry native ok")
                        : (probe_only ? "forward_probe native ok" : "forward_text native ok");
                    write_status(high_entry
                                 ? (probe_only ? "forward_high_probe native ok\n" : "forward_high_entry native ok\n")
                                 : (probe_only ? "forward_probe native ok\n" : "forward_text native ok\n"));
                    write_forward_result(parts[0], 1, msg,
                                          source_type, source_subtype,
                                          native_variant, queue_status,
                                          native_retained, native_fallback_kind);
                } else {
                    const char *msg = high_entry
                        ? (probe_only ? "forward_high_probe native failed" : "forward_high_entry native failed")
                        : (probe_only ? "forward_probe native failed" : "forward_text native failed");
                    write_status(high_entry
                                 ? (probe_only ? "forward_high_probe native failed\n" : "forward_high_entry native failed\n")
                                 : (probe_only ? "forward_probe native failed\n" : "forward_text native failed\n"));
                    write_forward_result(parts[0], 0, msg,
                                          source_type, source_subtype,
                                          native_variant, queue_status,
                                          native_retained, native_fallback_kind);
                }
            } else if (str_eq(parts[1], "5")) {
                if (parts[4] && parts[5] && parts[6] && parts[7] && parts[8]) {
                    int ok = send_quote_v2(parts[2], parts[3], a2i64(parts[4]), (int32_t)a2i64(parts[5]), parts[6],
                                               parts[7], (int)a2i64(parts[8]), (int32_t)a2i64(parts[9]),
                                               parts[10] ? parts[10] : "", parts[11] ? parts[11] : "",
                                               parts[12] ? parts[12] : "");
                    write_status(ok ? "send_quote ok\n" : "send_quote failed\n");
                    write_result(parts[0], ok, "send_quote", ok ? "send_quote ok" : "send_quote failed");
                } else {
                    write_status("send_quote missing args\n");
                    write_result(parts[0], 0, "send_quote", "send_quote missing args");
                }
            } else if (str_eq(parts[1], "3")) {
                int ok = send_image(parts[2], parts[3]);
                write_status(ok ? "send_image ok\n" : "send_image failed\n");
                write_result(parts[0], ok, "send_image", ok ? "send_image ok" : "send_image failed");
            } else if (str_eq(parts[1], "4")) {
                int ok = start_room_list(parts[0], parts[2]);
                write_status(ok ? "get_room_list started\n" : "get_room_list busy_or_failed\n");
                if (!ok) write_result(parts[0], 0, "room_members", "get_room_list busy_or_failed");
            } else if (str_eq(parts[1], "13")) {
                int ok = set_anti_revoke(parts[2][0] == '1' || parts[2][0] == 't' || parts[2][0] == 'T');
                if (!ok) write_status("anti_revoke failed\n");
                write_result(parts[0], ok, "anti_revoke", ok ? "anti_revoke ok" : "anti_revoke failed");
            } else if (str_eq(parts[1], "14")) {
                int appmsg_subtype = parts[4] ? (int)a2i64(parts[4]) : 0;
                int ok = send_appmsg_xml(parts[2],
                                         parts[3],
                                         appmsg_subtype,
                                         parts[5] ? parts[5] : "",
                                         parts[6] ? parts[6] : "",
                                         parts[7] ? parts[7] : "");
                write_status(ok ? "send_appmsg_xml ok\n" : "send_appmsg_xml failed\n");
                write_result(parts[0], ok, "send_appmsg_xml", ok ? "send_appmsg_xml ok" : "send_appmsg_xml failed");
            } else if (str_eq(parts[1], "15")) {
                int scene = parts[3] ? (int)a2i64(parts[3]) : 3;
                int url_type = parts[4] ? (int)a2i64(parts[4]) : 0;
                int wait_ms = parts[5] ? (int)a2i64(parts[5]) : 15000;
                int ok = get_a8key_task_to_result(parts[0], parts[2] ? parts[2] : "", scene, url_type, wait_ms);
                write_status(ok ? "get_a8key ok\n" : "get_a8key failed\n");
            } else if (str_eq(parts[1], "16")) {
                int cmd_id = parts[3] ? (int)a2i64(parts[3]) : 2975;
                int wait_ms = parts[6] ? (int)a2i64(parts[6]) : 15000;
                int jsapi_control_byte = parts[7] ? (int)a2i64(parts[7]) : 400;
                int report_security = parts[9] ? (int)a2i64(parts[9]) : 1;
                const char *transfer_cgi_url = parts[10] ? parts[10] : "";
                /* H5ExtTransfer can produce large async callbacks. Clear the
                 * one-shot outbox before invoking it so a crash after sidecar
                 * capture does not replay the same high-risk command forever.
                 */
                clear_outbox();
                int ok = h5ext_transfer_to_result(parts[0],
                                                   parts[2] ? parts[2] : "",
                                                   cmd_id,
                                                   parts[4] ? parts[4] : "",
                                                   parts[5] ? parts[5] : "",
                                                   wait_ms,
                                                   jsapi_control_byte,
                                                   parts[8] ? parts[8] : "",
                                                   report_security,
                                                   transfer_cgi_url);
                write_status(ok ? "h5ext_transfer ok\n" : "h5ext_transfer failed\n");
            } else if (str_eq(parts[1], "17")) {
                int wait_ms = parts[4] ? (int)a2i64(parts[4]) : 15000;
                int req_field4 = parts[5] ? (int)a2i64(parts[5]) : -1;
                int req_field6 = parts[6] ? (int)a2i64(parts[6]) : -1;
                clear_outbox();
                int ok = finder_userpage_model_to_result(parts[0],
                                                         parts[2] ? parts[2] : "",
                                                         parts[3] ? parts[3] : "",
                                                         wait_ms,
                                                         req_field4,
                                                         req_field6);
                write_status(ok ? "finder_userpage_model ok\n" : "finder_userpage_model failed\n");
            } else if (str_eq(parts[1], "42")) {
                int offset = parts[3] ? (int)a2i64(parts[3]) : 0;
                int scene = parts[4] ? (int)a2i64(parts[4]) : 88;
                int business_type = parts[5] ? (int)a2i64(parts[5]) : 0;
                int wait_ms = parts[6] ? (int)a2i64(parts[6]) : 15000;
                int sub_type = parts[7] ? (int)a2i64(parts[7]) : 0;
                int current_page = parts[8] ? (int)a2i64(parts[8]) : 0;
                clear_outbox();
                int ok = finder_search_to_result(parts[0],
                                                 parts[2] ? parts[2] : "",
                                                 offset,
                                                 scene,
                                                 business_type,
                                                 sub_type,
                                                 current_page,
                                                 parts[9] ? parts[9] : "",
                                                 parts[10] ? parts[10] : "",
                                                 wait_ms);
                write_status(ok ? "finder_search ok\n" : "finder_search failed\n");
            } else if (str_eq(parts[1], "43")) {
                clear_outbox();
                int ok = finder_enter_live_to_result(parts[0],
                                                      parts[8] ? parts[8] : "",
                                                      parts[2] ? parts[2] : "",
                                                      parts[3] ? parts[3] : "",
                                                      parts[4] ? parts[4] : "",
                                                      parts[5] ? parts[5] : "88",
                                                      parts[6] ? parts[6] : "",
                                                      parts[7] && (parts[7][0] == '1' ||
                                                                   parts[7][0] == 't' ||
                                                                   parts[7][0] == 'T'));
                write_status(ok ? "finder_live_enter ok\n" : "finder_live_enter failed\n");
            } else if (str_eq(parts[1], "44")) {
                int live_identity = parts[14] ? (int)a2i64(parts[14]) : 0;
                int wait_ms = parts[15] ? (int)a2i64(parts[15]) : 15000;
                clear_outbox();
                int ok = finder_post_live_comment_to_result(
                    parts[0],
                    parts[2] ? parts[2] : "",
                    parts[3] ? parts[3] : "",
                    parts[4] ? parts[4] : "",
                    parts[5] ? parts[5] : "",
                    parts[6] ? parts[6] : "",
                    parts[7] ? parts[7] : "",
                    parts[8] ? parts[8] : "",
                    parts[9] ? parts[9] : "",
                    parts[10] ? parts[10] : "88",
                    parts[11] ? parts[11] : "",
                    parts[12] ? parts[12] : "",
                    parts[13] ? parts[13] : "",
                    live_identity,
                    wait_ms);
                write_status(ok ? "finder_live_comment ok\n" : "finder_live_comment failed\n");
            } else if (str_eq(parts[1], "45")) {
                int wait_ms = parts[4] ? (int)a2i64(parts[4]) : 15000;
                clear_outbox();
                int ok = create_chatroom_to_result(parts[0],
                                                    parts[2] ? parts[2] : "",
                                                    parts[3] ? parts[3] : "",
                                                    wait_ms);
                write_status(ok ? "create_chatroom ok\n" : "create_chatroom failed\n");
            } else if (str_eq(parts[1], "18")) {
                int wait_ms = parts[4] ? (int)a2i64(parts[4]) : 15000;
                clear_outbox();
                int ok = modify_remark_task_to_result(parts[0],
                                                       parts[2] ? parts[2] : "",
                                                       parts[3] ? parts[3] : "",
                                                       wait_ms);
                write_status(ok ? "modify_remark ok\n" : "modify_remark failed\n");
            } else if (str_eq(parts[1], "19")) {
                int wait_ms = parts[4] ? (int)a2i64(parts[4]) : 15000;
                clear_outbox();
                int ok = modify_chatroom_self_display_name_to_result(parts[0],
                                                                      parts[2] ? parts[2] : "",
                                                                      parts[3] ? parts[3] : "",
                                                                      wait_ms);
                write_status(ok ? "modify_room_nickname ok\n" : "modify_room_nickname failed\n");
            } else if (str_eq(parts[1], "20")) {
                int wait_ms = parts[4] ? (int)a2i64(parts[4]) : 15000;
                clear_outbox();
                int ok = modify_chatroom_topic_to_result(parts[0],
                                                         parts[2] ? parts[2] : "",
                                                         parts[3] ? parts[3] : "",
                                                         wait_ms);
                write_status(ok ? "modify_room_name ok\n" : "modify_room_name failed\n");
            } else if (str_eq(parts[1], "21")) {
                int wait_ms = parts[4] ? (int)a2i64(parts[4]) : 15000;
                clear_outbox();
                int ok = publish_chatroom_announcement_to_result(parts[0],
                                                                 parts[2] ? parts[2] : "",
                                                                 parts[3] ? parts[3] : "",
                                                                 wait_ms);
                write_status(ok ? "publish_room_announcement ok\n" : "publish_room_announcement failed\n");
            } else if (str_eq(parts[1], "22")) {
                int wait_ms = parts[4] ? (int)a2i64(parts[4]) : 15000;
                clear_outbox();
                int ok = add_chatroom_member_to_result(parts[0],
                                                       parts[2] ? parts[2] : "",
                                                       parts[3] ? parts[3] : "",
                                                       wait_ms);
                write_status(ok ? "add_room_member ok\n" : "add_room_member failed\n");
            } else if (str_eq(parts[1], "23")) {
                int wait_ms = parts[4] ? (int)a2i64(parts[4]) : 15000;
                clear_outbox();
                int ok = del_chatroom_member_to_result(parts[0],
                                                       parts[2] ? parts[2] : "",
                                                       parts[3] ? parts[3] : "",
                                                       wait_ms);
                write_status(ok ? "del_room_member ok\n" : "del_room_member failed\n");
            } else if (str_eq(parts[1], "24")) {
                int wait_ms = parts[4] ? (int)a2i64(parts[4]) : 15000;
                int flag = parts[5] ? (int)a2i64(parts[5]) : 0;
                clear_outbox();
                int ok = leave_chatroom_to_result(parts[0],
                                                   parts[2] ? parts[2] : "",
                                                   parts[3] ? parts[3] : "",
                                                   wait_ms,
                                                   flag);
                write_status(ok ? "leave_chatroom ok\n" : "leave_chatroom failed\n");
            } else if (str_eq(parts[1], "25")) {
                clear_outbox();
                int ok = open_url_to_result(parts[0], parts[2] ? parts[2] : "");
                write_status(ok ? "open_url ok\n" : "open_url failed\n");
            } else if (str_eq(parts[1], "26")) {
                int wait_ms = parts[2] ? (int)a2i64(parts[2]) : 15000;
                int rich_mode = parts[3] ? (int)a2i64(parts[3]) : 0;
                clear_outbox();
                int ok = get_sns_timeline_to_result(parts[0], wait_ms, rich_mode);
                write_status(ok ? "get_sns_timeline ok\n" : "get_sns_timeline failed\n");
            } else if (str_eq(parts[1], "27")) {
                int dl_type = parts[3] ? (int)a2i64(parts[3]) : 0x67;
                int mode = parts[4] ? (int)a2i64(parts[4]) : 1;
                int use_send_item = parts[5] ? (int)a2i64(parts[5]) : 1;
                int wait_ms = parts[6] ? (int)a2i64(parts[6]) : 1000;
                int flags = parts[7] ? (int)a2i64(parts[7]) : 0x104;
                clear_outbox();
                int ok = download_image_wechat_probe_to_result(parts[0],
                                                               parts[2] ? parts[2] : "",
                                                               dl_type,
                                                               mode,
                                                               flags,
                                                               use_send_item,
                                                               wait_ms);
                write_status(ok ? "download_image_wechat_probe invoked\n" : "download_image_wechat_probe failed\n");
            } else if (str_eq(parts[1], "28")) {
                int wait_ms = parts[3] ? (int)a2i64(parts[3]) : 15000;
                clear_outbox();
                int ok = publish_sns_text_to_result(parts[0],
                                                    parts[2] ? parts[2] : "",
                                                    wait_ms);
                write_status(ok ? "publish_sns_text ok\n" : "publish_sns_text failed\n");
            } else if (str_eq(parts[1], "32")) {
                int width = parts[5] ? (int)a2i64(parts[5]) : 0;
                int height = parts[6] ? (int)a2i64(parts[6]) : 0;
                int wait_ms = parts[7] ? (int)a2i64(parts[7]) : 60000;
                clear_outbox();
                int ok = publish_sns_image_to_result(parts[0],
                                                     parts[2] ? parts[2] : "",
                                                     parts[3] ? parts[3] : "",
                                                     parts[4] ? parts[4] : "",
                                                     width,
                                                     height,
                                                     wait_ms,
                                                     parts[8] ? parts[8] : "",
                                                     parts[9] ? parts[9] : "");
                write_status(ok ? "publish_sns_image ok\n" : "publish_sns_image failed\n");
            } else if (str_eq(parts[1], "34")) {
                int width = parts[5] ? (int)a2i64(parts[5]) : 0;
                int height = parts[6] ? (int)a2i64(parts[6]) : 0;
                int wait_ms = parts[7] ? (int)a2i64(parts[7]) : 15000;
                int duration_ms = parts[9] ? (int)a2i64(parts[9]) : 0;
                clear_outbox();
                int ok = publish_sns_video_to_result(parts[0],
                                                     parts[2] ? parts[2] : "",
                                                     parts[3] ? parts[3] : "",
                                                     parts[4] ? parts[4] : "",
                                                     width,
                                                     height,
                                                     wait_ms,
                                                     parts[8] ? parts[8] : "",
                                                     duration_ms);
                write_status(ok ? "publish_sns_video ok\n" : "publish_sns_video failed\n");
            } else if (str_eq(parts[1], "35")) {
                int wait_ms = parts[5] ? (int)a2i64(parts[5]) : 15000;
                int reply_source = parts[10] ? (int)a2i64(parts[10]) : 0;
                int reply_type = parts[11] ? (int)a2i64(parts[11]) : 0;
                int reply_create_time = parts[12] ? (int)a2i64(parts[12]) : 0;
                clear_outbox();
                int ok = sns_comment_to_result(parts[0],
                                               parts[2] ? parts[2] : "",
                                               parts[3] ? parts[3] : "",
                                               parts[4] ? parts[4] : "",
                                               parts[6] ? parts[6] : "",
                                               parts[7] ? parts[7] : "",
                                               parts[8] ? parts[8] : "",
                                               parts[9] ? parts[9] : "",
                                               reply_source,
                                               reply_type,
                                               reply_create_time,
                                               wait_ms);
                write_status(ok ? "sns_comment ok\n" : "sns_comment failed\n");
            } else if (str_eq(parts[1], "36")) {
                int wait_ms = parts[4] ? (int)a2i64(parts[4]) : 15000;
                clear_outbox();
                int ok = sns_like_to_result(parts[0],
                                            parts[2] ? parts[2] : "",
                                            parts[3] ? parts[3] : "",
                                            parts[5] ? parts[5] : "",
                                            wait_ms);
                write_status(ok ? "sns_like ok\n" : "sns_like failed\n");
            } else if (str_eq(parts[1], "39")) {
                int wait_ms = parts[4] ? (int)a2i64(parts[4]) : 15000;
                clear_outbox();
                int ok = sns_delete_to_result(parts[0],
                                              parts[2] ? parts[2] : "",
                                              parts[3] ? parts[3] : "",
                                              parts[5] ? parts[5] : "",
                                              wait_ms);
                write_status(ok ? "sns_delete ok\n" : "sns_delete failed\n");
            } else if (str_eq(parts[1], "40")) {
                int wait_ms = parts[4] ? (int)a2i64(parts[4]) : 15000;
                unsigned int visibility_state = parts[8] ? (unsigned int)a2i64(parts[8]) : 0;
                clear_outbox();
                int ok = visibility_state
                    ? sns_set_visibility_to_result(parts[0],
                                                   parts[2] ? parts[2] : "",
                                                   parts[3] ? parts[3] : "",
                                                   parts[5] ? parts[5] : "",
                                                   parts[6] ? parts[6] : "",
                                                   parts[7] ? parts[7] : "",
                                                   visibility_state,
                                                   wait_ms)
                    : sns_set_private_to_result(parts[0],
                                                parts[2] ? parts[2] : "",
                                                parts[3] ? parts[3] : "",
                                                parts[5] ? parts[5] : "",
                                                parts[6] ? parts[6] : "",
                                                 parts[7] ? parts[7] : "",
                                                 wait_ms);
                write_status(ok ? "sns_set_visibility ok\n" : "sns_set_visibility failed\n");
            } else if (str_eq(parts[1], "41")) {
                int64_t voice_msgid = parts[2] ? a2i64(parts[2]) : 0;
                int32_t voice_local_id = parts[3] ? (int32_t)a2i64(parts[3]) : 0;
                int active_submit = parts[4] ? (int)a2i64(parts[4]) : 0;
                int ok = voice_transcribe_to_result(parts[0], voice_msgid, voice_local_id, active_submit);
                write_status(ok ? "voice_transcribe ok\n" : "voice_transcribe failed\n");
            } else if (str_eq(parts[1], "37")) {
                clear_outbox();
                int ok = accept_transfer_to_result(parts[0],
                                                   parts[2] ? parts[2] : "",
                                                   parts[3] ? parts[3] : "",
                                                   parts[4] ? (int32_t)a2i64(parts[4]) : 0,
                                                   parts[5] ? parts[5] : "");
                write_status(ok ? "accept_transfer submitted\n" : "accept_transfer failed\n");
            } else if (str_eq(parts[1], "38")) {
                clear_outbox();
                int ok = refuse_transfer_to_result(parts[0],
                                                   parts[2] ? parts[2] : "",
                                                   parts[3] ? (int32_t)a2i64(parts[3]) : 0,
                                                   parts[4] ? parts[4] : "");
                write_status(ok ? "refuse_transfer submitted\n" : "refuse_transfer failed\n");
            } else if (str_eq(parts[1], "29")) {
                int wait_ms = parts[3] ? (int)a2i64(parts[3]) : 5000;
                int source_type = parts[5] ? (int)a2i64(parts[5]) : 0;
                int source_subtype = parts[6] ? (int)a2i64(parts[6]) : 0;
                int source_local_id = parts[7] ? (int)a2i64(parts[7]) : 0;
                int32_t source_time = parts[8] ? (int32_t)a2i64(parts[8]) : 0;
                clear_outbox();
                int ok = download_image_task_probe_to_result(parts[0],
                                                             parts[2] ? parts[2] : "",
                                                             wait_ms,
                                                             parts[4] ? parts[4] : "",
                                                             source_type,
                                                             source_subtype,
                                                             source_local_id,
                                                             source_time,
                                                             parts[9] ? parts[9] : "",
                                                             parts[10] ? parts[10] : "",
                                                             parts[11] ? parts[11] : "",
                                                             parts[12] ? parts[12] : "");
                write_status(ok ? "download_image_task_probe invoked\n" : "download_image_task_probe failed\n");
            } else if (str_eq(parts[1], "30")) {
                int wait_ms = parts[4] ? (int)a2i64(parts[4]) : 0;
                clear_outbox();
                int ok = download_sns_media_url_to_result(parts[0],
                                                          parts[2] ? parts[2] : "",
                                                          parts[3] ? parts[3] : "",
                                                          wait_ms);
                write_status(ok ? "download_sns_media_url invoked\n" : "download_sns_media_url failed\n");
            } else if (str_eq(parts[1], "31")) {
                int64_t total_size = parts[6] ? a2i64(parts[6]) : 0;
                int wait_ms = parts[7] ? (int)a2i64(parts[7]) : 0;
                clear_outbox();
                int ok = download_sns_media_task_to_result(parts[0],
                                                           parts[2] ? parts[2] : "",
                                                           parts[3] ? parts[3] : "",
                                                           parts[4] ? parts[4] : "",
                                                           parts[5] ? parts[5] : "",
                                                           total_size,
                                                           wait_ms);
                write_status(ok ? "download_sns_media_task invoked\n" : "download_sns_media_task failed\n");
            } else if (str_eq(parts[1], "33")) {
                int64_t total_size = parts[7] ? a2i64(parts[7]) : 0;
                int task_type = parts[8] ? (int)a2i64(parts[8]) : 0;
                int wait_ms = parts[9] ? (int)a2i64(parts[9]) : 0;
                clear_outbox();
                int ok = download_sns_media_full_task_to_result(parts[0],
                                                                parts[2] ? parts[2] : "",
                                                                parts[3] ? parts[3] : "",
                                                                parts[4] ? parts[4] : "",
                                                                parts[5] ? parts[5] : "",
                                                                parts[6] ? parts[6] : "",
                                                                total_size,
                                                                task_type,
                                                                wait_ms);
                write_status(ok ? "download_sns_media_full_task invoked\n" : "download_sns_media_full_task failed\n");
            } else if (str_eq(parts[1], "8")) {
                if (parts[4] && parts[5]) {
                    int64_t filelen = parts[6] ? a2i64(parts[6]) : 0;
                    int mode = parts[7] ? (int)a2i64(parts[7]) : 1;
                    int app_type = parts[8] ? (int)a2i64(parts[8]) : -1;
                    int file_type = parts[9] ? (int)a2i64(parts[9]) : 0;
                    int64_t dl_ret = download_image_internal(parts[2], parts[3], parts[4], parts[5],
                                                             filelen, mode, app_type, file_type);
                    int ok = dl_ret == 0;
                    char msg[128];
                    char status_msg[160];
                    wsprintfA(msg, "download_image_internal ret=%I64d", dl_ret);
                    wsprintfA(status_msg, "%s\n", ok ? "download_image_internal ok" : msg);
                    write_status(status_msg);
                    write_result(parts[0], ok, "download_image_internal", msg);
                } else {
                    write_status("download_image_internal missing args\n");
                    write_result(parts[0], 0, "download_image_internal", "download_image_internal missing args");
                }
            } else if (str_eq(parts[1], "9")) {
                if (parts[3]) {
                    int ok = decode_image_internal(parts[2], parts[3]);
                    write_status(ok ? "decode_image_internal ok\n" : "decode_image_internal failed\n");
                    write_result(parts[0], ok, "decode_image_internal",
                                 ok ? "decode_image_internal ok" : "decode_image_internal failed");
                } else {
                    write_status("decode_image_internal missing args\n");
                    write_result(parts[0], 0, "decode_image_internal", "decode_image_internal missing args");
                }
            } else if (str_eq(parts[1], "12")) {
                /* 原生 appmsg 卡片(音乐/链接等)发送/转发。Python 已解析源 <appmsg> XML 出各字段。
                 * parts: [2]=to [3]=title [4]=des [5]=appmsg subtype [6]=url [7]=dataurl
                 *        [8]=songalbumurl [9]=thumburl [10]=appid [11]=action [12]=sourceusername [13]=sourcedisplayname */
                int subtype = parts[5] ? (int)a2i64(parts[5]) : 0;
                int ok = send_card(parts[2],
                                   parts[3] ? parts[3] : "",
                                   parts[4] ? parts[4] : "",
                                   subtype,
                                   parts[11] ? parts[11] : "view",
                                   parts[6] ? parts[6] : "",
                                   parts[7] ? parts[7] : "",
                                   parts[8] ? parts[8] : "",
                                   parts[9] ? parts[9] : "",
                                   parts[10] ? parts[10] : "",
                                   parts[12] ? parts[12] : "",
                                   parts[13] ? parts[13] : "");
                write_status(ok ? "send_card ok\n" : "send_card failed\n");
                write_result(parts[0], ok, "send_card", ok ? "send_card ok" : "send_card failed");
            } else {
                write_status("unsupported msg type\n");
                write_result(parts[0], 0, "unknown", "unsupported msg type");
            }
        } else if (parts[0] && *parts[0]) {
            write_status("malformed outbox command\n");
            write_result(parts[0], 0, "unknown", "malformed outbox command");
        }
        clear_outbox();
    }

    poll_auto_room_members();
    poll_auto_contact_names();
    InterlockedExchange(&g_outbox_processing, 0);
}
