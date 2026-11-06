#ifndef WX_BRIDGE_OFFSETS_H
#define WX_BRIDGE_OFFSETS_H

/* ===== WeChat 4.1.9.23 offsets ===== */
#define WX_OFF_DOADDMSG          0x166A6BF
#define WX_OFF_SEND_FACTORY_TEXT 0x63DF30
#define WX_OFF_SEND_TEXT_SERVICE 0x63E050
#define WX_OFF_SEND_CALL1_TEXT   0x6A35B0
#define WX_OFF_SEND_CALL1_IMAGE  0x18514F0
#define WX_OFF_SEND_HC1_TEXT     0x805E4E8
#define WX_OFF_SEND_HC1_IMAGE    0x805E4E8
#define WX_OFF_SEND_CALL2        0xDB40
#define WX_OFF_SEND_CALL2_HC1    0x83661E8
#define WX_OFF_SEND_CALL2_HC2    0x8366128
#define WX_OFF_SEND_CALL2_HC3    0x8366068
#define WX_OFF_SEND_CALL3        0x160EBE0
#define WX_OFF_QUEUE_HC          0x83553C8
#define WX_OFF_QUEUE_SUBMIT      0x3664EE0
#define WX_OFF_ROOM_INIT1        0x20960
#define WX_OFF_ROOM_INIT2        0x2FF1B0
#define WX_OFF_ROOM_INIT3        0x6278F0
#define WX_OFF_ROOM_GET          0x1532C80
#define WX_OFF_QUEUE_ID          0x9E11D10
#define WX_OFF_SEND_ITEM_CTOR    0x2337CF0  /* sub_182337CF0: default-construct optional send_item at refermsg+0x208 */
#define WX_OFF_SEND_ITEM_VT      0x7FB6B58  /* send_item vtable; written by WX_OFF_SEND_ITEM_CTOR */
#define WX_OFF_SEND_ITEM_READY_CHECK 0x99EDA0 /* sub_18099EDA0: synthetic send_item diagnostic gate used by the raw high path */
#define WX_OFF_MSG_TO_SEND_ITEM  0x9D8F80  /* sub_1809D8F80(model 0x7FB7B08, out send_item 0x7FB6B58): WeChat's real message-model -> send_item converter (media MD5/CDN carried from model sub-objects) */
#define WX_OFF_SEND_ITEM_COPY    0x1AD0E0  /* sub_1801AD0E0(dst, src): send_item copy-constructor (904 bytes, deep-copies strings + shared_ptr refs) */
#define WX_OFF_MODEL_COPY        0x1CA4C0  /* sub_1801CA4C0(dst, src): message-MODEL copy-constructor (~0x290 bytes, deep copy; used by build_task) */
#define WX_OFF_MODEL_DTOR        0x1CB270  /* sub_1801CB270(model): destructor paired with WX_OFF_MODEL_COPY */
#define WX_OFF_SRC_MSG_CTL_VT    0x7FB7A78  /* real source-message shared_ptr control-block vtable, from UI quote dump */
#define WX_OFF_SRC_MSG_VT        0x7FB7B08  /* real source-message object vtable, from UI quote dump */
#define WX_OFF_CONTACT_COMPACT_CTL_VT 0x7FB7B98 /* type=42 compact contact-card payload shared_ptr control block; object starts at ctl+0x10 */
#define WX_OFF_FORWARD_HIGH_ENTRY 0x1656D10 /* raw high-level gate: controller,out,high_variant_pair,mode */
#define WX_OFF_SEND_TEXT_HIGH_WRAP 0x1657E70 /* text wrapper: service,out,text_pair -> raw high gate mode 0 */
#define WX_OFF_SEND_ONECALL_DISPATCH 0x526C760 /* sub_18526C760(record,out,ctx): internal 0x278-byte send task dispatcher; type 5 -> text task */
#define WX_OFF_SEND_ONECALL_TEXT_TASK 0x526EDF0 /* sub_18526EDF0(record): text task body; builds text object and enters 18160DCD0 */
#define WX_OFF_SOURCE_SEND       0x20291D0 /* sub_1820291D0(out, selection_ctx, source_vec, extra, callback): generic MessageSendSource vector sender */
#define WX_OFF_SOURCE_SEND_PRECHECK 0x2029EE0 /* sub_182029EE0(selection_ctx): source-send target/context precheck */
#define WX_OFF_SOURCE_ROUTE_CONTAINER_BUILD 0x5FE160 /* sub_1805FE160(ctx,out,ctx+0x28 tag,source_vec): builds off_18804ECB8 route container */
#define WX_OFF_SOURCE_DISPATCH_CORE 0x128690 /* sub_180128690(task_ctx,out,cb9,cb17,cb25,scratch): dispatches source task */
#define WX_OFF_SOURCE_WXWORK_PACK_A 0x6034C0 /* sub_1806034C0(route_container,ctx): packages off_18804ED58 async route object */
#define WX_OFF_SOURCE_WXWORK_PACK_B 0x603D50 /* sub_180603D50(off_18804ECB8): route/batch adapter entry */
#define WX_OFF_SOURCE_ROUTE_KEY_LOOKUP 0x14CE590 /* sub_1814CE590(controller,tag,state): route-key map lookup; may enter wxwork forwardmsg */
#define WX_OFF_SOURCE_BATCH_ADAPTER 0x14CF830 /* sub_1814CF830(controller,out,tag,source_vec): controller-side batch adapter */
#define WX_OFF_WXWORK_FORWARDMSG_URL 0x32F2E40 /* sub_1832F2E40(out,tag,data): builds wxwork://forwardmsg URL */
#define WX_OFF_SOURCE_IMAGE_FACTORY 0x1C264C0 /* sub_181C264C0(out_shared_ptr): MessageSendSource image factory, type filled by caller */
#define WX_OFF_SOURCE_FILE_FACTORY 0x2161930 /* sub_182161930(out_shared_ptr): generic local-file MessageSendSource factory; caller sets type 0x600000031 */
#define WX_OFF_SOURCE_FILE_SPECIAL_FACTORY 0x1AFE710 /* sub_181AFE710(out_shared_ptr): special file/emoticon-like source factory, not used by generic file test */
#define WX_OFF_SOURCE_APPMSG_MUSIC_FACTORY 0x431B940 /* sub_18431B940(out_shared_ptr): appmsg/music MessageSendSource factory; sub_184317570 sets type 0x300000031 */
#define WX_OFF_SOURCE_CONTACT_FACTORY 0x37DCCB0 /* sub_1837DCCB0(out_shared_ptr): ContactMessageSendSource factory; type=42 path uses full payload at source+0x660 */
#define WX_OFF_SOURCE_APPMSG_FINDER_FACTORY 0x18A48E0 /* sub_1818A48E0(out_shared_ptr): appmsg/finder MessageSendSource factory; case 0x16 sets type 0x3300000031 */
#define WX_OFF_SOURCE_FINDER_FEED_PAYLOAD_FACTORY 0x431BCB0 /* sub_18431BCB0(out_shared_ptr): finderFeed payload factory, ctl vtable off_187FB7248 */
#define WX_OFF_FINDER_FEED_PAYLOAD_FILL 0x20B7BD0 /* sub_1820B7BD0(dst_finderFeed_payload, src_model_payload): reference copier, not used by XML source path */
#define WX_OFF_FINDER_FEED_PAYLOAD_CTL_VT 0x7FB7248 /* off_187FB7248: finderFeed payload control block; object starts at ctl+0x10 */
#define WX_OFF_FINDER_MEDIA_VECTOR_PUSH 0x22401D0 /* sub_1822401D0(vector, insert_pos, media_item): grows finderFeed+0x228 media vector with 0x120-byte media items */
#define WX_OFF_FINDER_MEDIA_ITEM_DTOR 0x1BCB00 /* sub_1801BCB00(media_item): destroys 0x120-byte finder media item strings */
#define WX_OFF_SOURCE_VOICE_FACTORY 0x5EF9010 /* sub_185EF9010(out_shared_ptr,target_str,voice_data_str,&duration_ms,voice_meta): voice MessageSendSource factory */
#define WX_OFF_SOURCE_VOICE_SEND_WRAP 0x5EF8B90 /* sub_185EF8B90(selection,out_ctx,voice_data,target,duration,meta): official voice MessageSendSource wrapper */
#define WX_OFF_SOURCE_TASK_BUILD_VOICE 0x15E22C0 /* sub_1815E22C0(ctx,out,source_vec): builds voice task container off_1882C02B8 */
#define WX_OFF_SOURCE_TASK_BUILD_GENERIC 0x15E25D0 /* sub_1815E25D0(ctx,out,source_vec): builds generic task container off_1882C0438 */
#define WX_OFF_SOURCE_EXTRA_APPLY 0x41E18F0 /* sub_1841E18F0(source_pair, extra): copies extra/closure metadata into MessageSendSource */
#define WX_OFF_SOURCE_INJECT_CONVERT 0x41E1A60 /* sub_1841E1A60(out_vec, in_vec, route_map, ctx_vec): converts source vector before generic task build */
#define WX_OFF_SOURCE_DISPATCH_WRAP 0x202A260 /* sub_18202A260(task_ctx,out,callbacks): invokes closure callbacks then dispatches source task */
#define WX_OFF_VOICE_STAGE_SEND_FINAL 0x52F0F20 /* sub_1852F0F20(a1,a2): prepares final voice source-send wrapper args */
#define WX_OFF_VOICE_CACHED_FLUSH     0x52F0CA0 /* sub_1852F0CA0(state): flushes cached state+0x170 VoiceBuffer or arms pending send */
#define WX_OFF_VOICE_STAGE_BUFFER     0x52F1740 /* sub_1852F1740(a1,a2): voice buffer state machine; may call final send */
#define WX_OFF_VOICE_HOLDER_FLUSH     0x52F8460 /* sub_1852F8460(holder): calls 1852F0CA0(*(holder+0x168)) from UI callback */
#define WX_OFF_VOICE_STAGE_UPPER_A    0x52FBB00 /* sub_1852FBB00(a1,a2): upstream voice-data producer */
#define WX_OFF_VOICE_STAGE_UPPER_B    0x52FC160 /* sub_1852FC160(a1,a2): upstream voice-data producer */
#define WX_OFF_VOICE_PREPARE_SEND     0x52EEA40 /* sub_1852EEA40(state): prepares/binds state before voice send */
#define WX_OFF_VOICE_CONTROLLER_BIND  0x52F0020 /* sub_1852F0020(state): creates/binds state+0x198 voice controller */
#define WX_OFF_VOICE_HOLDER_INIT      0x52F2F30 /* sub_1852F2F30(holder): constructs holder and installs default VoiceState at holder+0x168 */
#define WX_OFF_VOICE_TARGET_SET       0x52F65A0 /* sub_1852F65A0(holder,target): replaces holder+0x168 with target-bound VoiceState */
#define WX_OFF_VOICE_STATE_REBUILD    0x52F6880 /* sub_1852F6880(holder): rebuilds holder+0x168 while preserving target */
#define WX_OFF_VOICE_HOLDER_DTOR      0x52F5FF0 /* sub_1852F5FF0(holder): holder destructor; destroys holder+0x168 */
#define WX_OFF_SOURCE_VOICE_CTL_VT  0x8B654D8 /* off_188B654D8: voice MessageSendSource control block; object starts at ctl+0x10 */
#define WX_OFF_SOURCE_VOICE_OBJ_VT  0x8B65568 /* off_188B65568: voice MessageSendSource object vtable */
#define WX_OFF_VOICE_STATE_VT       0x89CACD8 /* off_1889CACD8: VoiceState vtable */
#define WX_OFF_VOICE_HOLDER_VT      0x89CAD38 /* off_1889CAD38: voice holder vtable */
#define WX_OFF_VOICE_CONTROLLER_VT  0x8B63818 /* off_188B63818: voice backend controller vtable at state+0x198 */
#define WX_OFF_TASK_DISPATCH     0x2D7830 /* sub_1802D7830(manager, task): mars/mmext task dispatcher; hook captures manager for protobuf tasks */
#define WX_OFF_TASK_BASE_INIT    0xD0D5E0 /* sub_180D0D5E0(task_base): initializes 0x1C8 task base and assigns a transient task id */
#define WX_OFF_TASK_ID_GLOBAL    0x9EE8980 /* dword_189EE8980 used by WX_OFF_TASK_BASE_INIT/dispatcher for task ids */
#define WX_OFF_SOURCE_EMOJI_META_BUILDER 0x484770 /* sub_180484770(meta, std::wstring FileName): builds 32-byte special metadata used by 1839986E0 before source+1664; Hex-Rays drops RDX */
#define WX_OFF_SOURCE_EMOJI_META_DTOR 0x43DAB0 /* sub_18043DAB0(meta+8): destroys metadata helper after source+1664 copy */
#define WX_OFF_EMOJI_XML_PARSED_CTL_VT 0x7FB73F8 /* parsed XML emoji model control block seen at 180A34810; not the source-send payload */
#define WX_OFF_EMOJI_PAYLOAD_CTL_VT 0x83DCC28 /* combined shared_ptr control block for EmoticonDataInfo payload; object starts at ctl+0x10 */
#define WX_OFF_EMOJI_PAYLOAD_BUILD  0x404A2F0 /* sub_18404A2F0(payload_obj, emoji_model): builds off_188755528 EmoticonDataInfo from 10 string slots */
#define WX_OFF_CONTACT_FULL_CTL_VT 0x7FB7638 /* off_187FB7638: full type=42 contact-card payload control block; object starts at ctl+0x10 */
#define WX_OFF_FORWARD_HIGH_WRAP_A 0x1659D10 /* wrapper A: shared_ptr high item -> raw high gate */
#define WX_OFF_FORWARD_HIGH_WRAP_B 0x1659DD0 /* wrapper B: tries 18165A320/18165A4C0 helper path before raw high gate */
#define WX_OFF_FORWARD_CONTROLLER_VT 0x82C74F8 /* real UI forward controller vtable */
#define WX_OFF_FORWARD_HIGH_VARIANT_VT 0x82C0848 /* high-level forward item vtable, vfunc+0x18 returns 2 */
#define WX_OFF_FORWARD_HIGH_VARIANT_CTL_VT 0x82C0898 /* shared_ptr control block vtable observed beside high_variant */
#define WX_OFF_FORWARD_CONTAINER_VT 0x865DEC8 /* prepare-built downstream container vtable */
#define WX_OFF_FORWARD_ENTRY     0x34E1480  /* single forward path wrapper; text-like objects flow to sub_1834E73F0(...,1,1) */
#define WX_OFF_FORWARD_SUBMIT    0x34E73F0  /* submit path captured by real UI single-forward */
#define WX_OFF_FORWARD_MANAGER_VT 0x864DEE8 /* live manager object vtable seen at rcx during real UI single-forward submit */
#define WX_OFF_FORWARD_UI_MANAGER_VT 0x864D218 /* real UI forward manager vtable seen during manual forward submit */
#define WX_OFF_FORWARD_PREPARE_MSG 0x9D8970  /* prepares source/submit msg extension at msg+0x240 */
#define WX_OFF_FORWARD_BUILD_TASK 0x34176F0  /* builds internal forward task shared_ptr from msg */
#define WX_OFF_FORWARD_TASK_CAST  0x167A9D0  /* casts internal shared_ptr to forward task body */
#define WX_OFF_FORWARD_DISPATCH   0x34DC500  /* dispatches a ready forward payload through manager signal */
#define WX_OFF_FORWARD_UI_ENTRY   0x353B140  /* real single-forward UI controller path; calls WX_OFF_FORWARD_SUBMIT later */
#define WX_OFF_APPMSG_KIND_QUERY  0x224C8D0  /* sub_18224C8D0(packed=((subtype<<32)|0x31)): maps appmsg subtype to forward/category kind */
#define WX_OFF_WX_ALLOC           0x64A7D1C  /* WeChat heap allocation used by std::string buffers */
#define WX_OFF_WX_DELETE          0x64A7D60  /* WeChat operator delete thunk used by H5ExtTransfer callback holder */
#define WX_OFF_WX_FREE            0x64A7D68  /* WeChat heap free paired with WX_OFF_WX_ALLOC */
#define WX_OFF_FRIEND_SERVICE     0x693050 /* sub_180693050(stage,out shared_ptr): resolves VerifyUser/friend service */
#define WX_OFF_VERIFY_USER_REQUEST 0x1810920 /* wx_verify_user_request: sends /cgi-bin/micromsg-bin/verifyuser, CGI 137 */
#define WX_OFF_ACCEPT_FRIEND_REQUEST 0x1815800 /* wx_accept_friend_request: verify-user opcode 3 plus local contact sync */
#define WX_OFF_H5_TRANSFER_MANAGER 0x18C8E10 /* sub_1818C8E10(): returns H5ExtTransfer manager */
#define WX_OFF_H5_TRANSFER_SUBMIT 0x18C9490 /* sub_1818C9490(manager, 392-byte request, callback holder) */
#define WX_OFF_H5_REQ_DTOR        0x18CBE20 /* sub_1818CBE20(request): destroys H5ExtTransfer request object */
#define WX_OFF_OPEN_URL_REQUEST_INIT 0xA82790 /* sub_180A82790(out_request, url std::string): builds xweb/open-url request */
#define WX_OFF_OPEN_URL_ROUTE        0x20CB50 /* sub_18020CB50(context, request): routes request into WeChat browser */
#define WX_OFF_OPEN_URL_REQUEST_DTOR 0x20AB60 /* sub_18020AB60(request): destroys 400-byte open-url request */
#define WX_OPEN_URL_REQUEST_SIZE     400      /* stack request size used by handle_xweb_call_open_url */
#define WX_OFF_XWEB_EVENT_DISPATCH   0x7C55A0 /* sub_1807C55A0(widget,event,payload,payload_len): xweb/wechat browser JS callback dispatcher */
#define WX_OFF_JSVAR_TO_JSON      0x592AD0  /* sub_180592AD0(out std::string, js-like variant) */
#define WX_OFF_JSVAR_DTOR         0x00A510  /* sub_18000A510(variant+8, tag) */
#define WX_OFF_PAY_ACCEPT_BUILD_REQ 0x4A49080 /* sub_184A49080(out, shared_pay, group_username, mode): TenPay request builder */
#define WX_OFF_PAY_CGI_SEND       0x4A4D9A0 /* sub_184A4D9A0(out_resp, account_uin, req, out_json, route): /cgi-bin/micromsg-bin/tenpay */
#define WX_OFF_PAY_REQ_DTOR       0x5B96D10 /* sub_185B96D10(req): request object destructor used after TenPay send */
#define WX_OFF_PAY_ACCEPT_TASK_RUN 0x4A59D00 /* sub_184A59D00(outer_task, callback_ctx): schedules CheckAndAcceptPayTransfer */
#define WX_OFF_PAY_REFUSE_TASK_RUN 0x4A56180 /* sub_184A56180(outer_task, callback_ctx): schedules CoRefusePayTransfer task */
#define WX_OFF_FINDER_USERPAGE_CGI_CONTEXT_ROOT 0x1F460 /* sub_18001F460(): root whose vfunc+0x10 returns CoCgiSendRequest context */
#define WX_OFF_FINDER_USERPAGE_SESSION_ID 0x8FDE20 /* sub_1808FDE20(): request context session/token qword */
#define WX_OFF_FINDER_USERPAGE_BASE_REQ_INIT 0x5CEB80 /* sub_1805CEB80: init request field1 message, size 56 */
#define WX_OFF_FINDER_USERPAGE_REQ_CTX_INIT 0x35B1550 /* sub_1835B1550: init request field2 context message, size 144 */
#define WX_OFF_FINDER_USERPAGE_SERVICE 0x67B580 /* sub_18067B580(mid_service,out): resolves service object passed as sub_1817CA330 a1 */
#define WX_OFF_FINDER_USERPAGE_OUTER 0x17CA330 /* sub_1817CA330: real finderuserpagepreview high-level path; builds req, sends, converts local model */
#define WX_OFF_FINDER_USERPAGE_SEND  0x17CC030 /* sub_1817CC030: CoCgiSendRequest wrapper for /cgi-bin/micromsg-bin/finderuserpagepreview cmd 6611 */
#define WX_OFF_FINDER_USERPAGE_TASK_FACTORY 0x17D66A0 /* sub_1817D66A0: real 464-byte finderuserpagepreview task factory, vtable off_1882EB828 */
#define WX_OFF_FINDER_USERPAGE_MODEL_DTOR 0x17CD6E0 /* sub_1817CD6E0: destroys local model built by sub_1817CA330 */
#define WX_OFF_FINDER_USERPAGE_REQ_INIT 0x35A8DF0 /* sub_1835A8DF0: init micromsg.FinderUserPagePreviewRequest, size 0x58 */
#define WX_OFF_FINDER_USERPAGE_REQ_COPY 0x35ADEC0 /* sub_1835ADEC0: request copy wrapper */
#define WX_OFF_FINDER_USERPAGE_REQ_DTOR 0x35ACD60 /* sub_1835ACD60: request destructor */
#define WX_OFF_FINDER_USERPAGE_RESP_PARSE 0x35AEA20 /* sub_1835AEA20: parse micromsg.FinderUserPagePreviewResponse */
#define WX_OFF_FINDER_USERPAGE_RESP_COPY 0x35ADF90 /* sub_1835ADF90: response copy/assign */
#define WX_OFF_FINDER_OBJECT_PARSE 0x510A1C0 /* sub_18510A1C0: parse micromsg.FinderObject, object size 456 */
#define WX_OFF_CONTACT_MODIFY_REMARK 0x636C90 /* sub_180636C90(unused, username_str, remark_str): upper contact remark modification path */
#define WX_OFF_CHATROOM_MOD_SELF_DISPLAY_NAME 0x638060 /* sub_180638060(unused, roomid_str, self_display_name_str): upper chatroom self-display-name path */
#define WX_OFF_CHATROOM_MOD_TOPIC 0x636690 /* sub_180636690(unused, roomid_str, topic_str): upper chatroom name/topic modification path */
#define WX_OFF_CHATROOM_CREATE 0x1544030 /* sub_181544030(service,out_roomid,members_vec,topic_str): business createchatroom path, CGI 119 */
#define WX_OFF_CHATROOM_ADD_MEMBER 0x1540AA0 /* sub_181540AA0(service, members_vec, roomid_str, ext_obj, optional_str): business addchatroommember path */
#define WX_OFF_CHATROOM_ADD_MEMBER_EXT_INIT 0x17DE230 /* sub_1817DE230: empty AddChatRoomMember extension/context object init, size 0x50 */
#define WX_OFF_CHATROOM_ADD_MEMBER_EXT_DTOR 0x17E7E30 /* sub_1817E7E30: extension/context object destructor paired with 1817DE230 */
#define WX_OFF_CHATROOM_INVITE_MEMBER 0x154E270 /* sub_18154E270(service, members_vec, roomid_str, ext_obj): business invitechatroommember path */
#define WX_OFF_CHATROOM_DEL_MEMBER 0x154AB60 /* sub_18154AB60(service,out,members_vec,roomid_str,flag): business delchatroommember path */
#define WX_OFF_CHATROOM_QUIT_TASK 0x68D0E0 /* sub_18068D0E0(task): upper delete-and-exit path; task+8 roomid std::string, task+40 flag */
#define WX_OFF_CHATROOM_QUIT_OPLOG_WRAP 0xECE2F0 /* sub_180ECE2F0(service,roomid_str,flag): builds QuitChatRoom oplog */
#define WX_OFF_CHATROOM_QUIT_OPLOG 0x17F4220 /* sub_1817F4220(oplog_manager,roomid_str): serializes micromsg.QuitChatRoom cmd 0x10 */
#define WX_OFF_CHATROOM_QUIT_OPLOG_MANAGER 0x650B20 /* sub_180650B20: resolves OplogManager used by sub_180ECE2F0 */
#define WX_OFF_CHATROOM_QUIT_SERVICE 0x36E850 /* sub_18036E850: resolves service object passed to sub_180ECE2F0 by sub_18068D0E0 */
#define WX_OFF_CHATROOM_ANNOUNCEMENT_SEND 0x430A170 /* sub_18430A170(out, cgi_ctx, SetChatRoomAnnouncementRequest, empty_str, opts): business send path for /setchatroomannouncement */
#define WX_OFF_CHATROOM_ANNOUNCEMENT_REQ_INIT 0x5845FA0 /* sub_185845FA0: micromsg.SetChatRoomAnnouncementRequest constructor, size 0x58 */
#define WX_OFF_CHATROOM_ANNOUNCEMENT_REQ_DTOR 0x58466D0 /* sub_1858466D0: micromsg.SetChatRoomAnnouncementRequest destructor */
#define WX_OFF_SNS_TIMELINE_FETCH 0x3B24690 /* sub_183B24690(TimelineDataSource*): upper SNS timeline fetch/merge path */
#define WX_OFF_SNS_TIMELINE_FETCH_RICH 0x3B5B4B0 /* sub_183B5B4B0(TimelineDataSource*): timeline fetch mirror that also schedules SNS detail paths */
#define WX_OFF_SNS_TIMELINE_RESPONSE_DTOR 0x2BC3930 /* sub_182BC3930(response body): SnsTimeLineResponse wrapper body destructor */
#define WX_OFF_SNS_TIMELINE_LOAD_UPPER 0x3B32CE0 /* sub_183B32CE0: UI/data-source load path, calls SNS_TIMELINE_FETCH */
#define WX_OFF_SNS_TIMELINE_DATASOURCE_VT 0x870D4D8 /* off_18870D4D8: live TimelineDataSource vtable */
#define WX_OFF_SNS_TIMELINE_LOAD_CLOSURE_VT 0x870FCC8 /* off_18870FCC8: load closure; closure+8 points to TimelineDataSource */
#define WX_OFF_SNS_TIMELINE_OBJECT_JSON 0x3E2A5C0 /* sub_183E2A5C0(TimelineObject*, out string): rich SNS TimelineObject serializer */
#define WX_OFF_SNS_DATAITEM_JSON 0x3DA0800 /* sub_183DA0800(SnsDataItem*, out string): wraps TimelineObject + LocalExtraInfo */
#define WX_OFF_SNS_MEDIA_ITEM_JSON 0x3DA41D0 /* sub_183DA41D0(SnsMediaItem*, json node): single media serializer */
#define WX_OFF_SNS_DOWNLOAD_IMAGE_URL 0x3D654D0 /* sub_183D654D0(out std::string path, std::string* url, std::string* suffix): kernel::sns::DownloadImageUrl */
#define WX_OFF_SNS_POST_SEND      0x4CB2CC0 /* sub_184CB2CC0: CoCgiSendRequest wrapper for /cgi-bin/micromsg-bin/mmsnspost, cgi type 209 */
#define WX_OFF_SNS_POST_BUILD_SEND 0x4CB0920 /* sub_184CB0920: serializes TimelineObject then calls SNS post send wrapper */
#define WX_OFF_SNS_POST_RUN       0x4CAD900 /* sub_184CAD900(SnsPostObject*, phase): SNS post state machine; phase 3 reaches build/send */
#define WX_OFF_SNS_POST_CONTINUE  0x4CAFF00 /* sub_184CAFF00(SnsPostObject*): official service-side continue gate before POST_RUN */
#define WX_OFF_SNS_PUBLISH_ENTRY  0x2BE2100 /* sub_182BE2100(SnsPublishManager*, shared_ptr<PublishModel>*): upper SNS publish entry */
#define WX_OFF_SNS_RETRY_PANEL_OPEN 0x1C8BEB0 /* sub_181C8BEB0(TimelineViewController*, shared_ptr<PublishModel>*): opens the publish retry panel */
#define WX_OFF_SNS_PUBLISH_SUBMIT_HANDLER 0x3E43E80 /* sub_183E43E80(PublishController*): validates and submits the publish model */
#define WX_OFF_SNS_PUBLISH_IMMEDIATE_SCHEDULE 0x3B1B290 /* sub_183B1B290(context, shared_ptr<PublishModel>*): model+24 != 0 first-publish scheduler */
#define WX_OFF_SNS_PUBLISH_IMMEDIATE_WORKER 0x3B26140 /* sub_183B26140(off_18870D648 closure): prepares session/registration state then enters SNS_PUBLISH_ENTRY */
#define WX_OFF_SNS_PUBLISH_IMMEDIATE_CLOSURE_VT 0x870D648 /* off_18870D648: immediate worker closure vtable; +16 is sub_183B26140 */
#define WX_OFF_SNS_PUBLISH_ACTION 0x555BB30 /* sub_18555BB30: model+24 == 0 observable publish branch */
#define WX_OFF_SNS_PUBLISH_WORKER_CLOSURE 0x3B1AEB0 /* sub_183B1AEB0: model+24 == 0 observable worker closure builder */
#define WX_OFF_SNS_PUBLISH_WORKER_SCHEDULE 0x3B542D0 /* sub_183B542D0: model+24 == 0 observable worker scheduler */
#define WX_OFF_SNS_PUBLISH_WORKER_RUN 0x3B547B0 /* sub_183B547B0: model+24 == 0 observable worker */
#define WX_OFF_SNS_PUBLISH_WORKER_CLOSURE_VT 0x8712548 /* off_188712548: model+24 == 0 observable closure vtable */
#define WX_OFF_SNS_PUBLISH_WORKER_CONTEXT_IFACE 0x83735D8 /* off_1883735D8: interface function table; pass its address to sub_1801A7A30 */
#define WX_OFF_SNS_PUBLISH_PREPARE_MODEL 0x2BE2380 /* sub_182BE2380(manager, shared_ptr<PublishModel>*, mode): publish-model service indexing */
#define WX_OFF_SNS_PUBLISH_BUILD_POST 0x2BE1BA0 /* sub_182BE1BA0(out shared_ptr<PostObject>, shared_ptr<PublishModel>*): official post object builder */
#define WX_OFF_SNS_PUBLISH_REGISTER_POST 0x2BE1D10 /* sub_182BE1D10(service queue, shared_ptr<PostObject>*): register post callbacks/map */
#define WX_OFF_SNS_PUBLISH_MODEL_CTOR 0x3E28F90 /* sub_183E28F90(PublishModel*, seed): official high-level SNS publish model constructor */
#define WX_OFF_SNS_UI_PUBLISH_ASYNC_A 0x1C948E0 /* sub_181C948E0: UI/async publish wrapper; schedules sub_181C95370 or enters sub_183E336C0 */
#define WX_OFF_SNS_UI_PUBLISH_ASYNC_B 0x1C95650 /* sub_181C95650: UI/async publish/retry wrapper; calls sub_183E1DEC0 and sub_181C955E0 callback */
#define WX_OFF_SNS_UI_PUBLISH_CB_A    0x1C95370 /* sub_181C95370: async publish callback used by 181C948E0 */
#define WX_OFF_SNS_UI_PUBLISH_CB_B    0x1C955E0 /* sub_181C955E0: async publish callback used by 181C95650 */
#define WX_OFF_SNS_PUBLISH_CTX_ENTER  0x3E1DEC0 /* sub_183E1DEC0(context, shared_model): creates/binds publish context object */
#define WX_OFF_SNS_PUBLISH_ASYNC_RUN  0x3E336C0 /* sub_183E336C0(wrapper, ctx, cb_a, cb_b): official async publish runner */
#define WX_OFF_SNS_PUBLISH_WRAP_INIT  0x3E30040 /* sub_183E30040(wrapper, shared_model): initializes publish wrapper from model */
#define WX_OFF_SNS_VIDEO_SCAN         0x3E375E0 /* legacy experimental video scan path; not the local-video publish entry */
#define WX_OFF_SNS_VIDEO_REGISTER     0x3E36C30 /* legacy experimental video register path; stage 2 official upload is under 184CAD900 */
#define WX_OFF_SNS_VIDEO_REGISTER_PREP 0x3B191A0 /* legacy upload registration helper */
#define WX_OFF_SNS_VIDEO_ASYNC_BUILD  0x3E37330 /* legacy wrapper that reaches 183E3F1B0 */
#define WX_OFF_SNS_VIDEO_TASK_BUILD   0x3E3F1B0 /* legacy async task build; bypasses official stage-2 backfill when used directly */
#define WX_OFF_QOBJECT_REF_TOKEN      0x264580  /* sub_180264580(QObject*): returns the ref token used as ctx_pair[0] by WX_OFF_SNS_PUBLISH_ASYNC_RUN */
#define WX_OFF_GLOBAL_SERVICE_LOCATOR 0xA530278 /* qword_18A530278: process service locator passed to sub_180169070 */
#define WX_OFF_SNS_PUBLISH_CTX_SERVICE 0x169070 /* sub_180169070(qword_18A530278): service whose vtable+112 returns SNS publish context */
#define WX_OFF_SNS_PUBLISH_MODEL_VT   0x8392188  /* off_188392188: shared_ptr control block vtable for PublishModel */
#define WX_OFF_SNS_PUBLISH_WRAP_CTL_VT 0x8392218 /* off_188392218: shared_ptr control block vtable for SNS publish wrapper */
#define WX_OFF_SNS_MEDIA_ITEM_VT      0x8527268  /* off_188527268: shared_ptr control block vtable for kernel::SnsMediaItem */
#define WX_OFF_SNS_THUMB_URL_VT       0x8526678  /* off_188526678: shared_ptr control block vtable for SNS media url/thumb object */
#define WX_OFF_SNS_MANAGER_ROOT   0x20960   /* sub_180020960(out shared_ptr): resolves application service root */
#define WX_OFF_SNS_MANAGER_STAGE1 0x2FF1B0  /* sub_1802FF1B0(root, out shared_ptr): extracts stage object at +40/+48 */
#define WX_OFF_SNS_MANAGER_STAGE2 0xEFB5F0  /* sub_180EFB5F0(stage, out shared_ptr): resolves SNS publish manager */
#define WX_OFF_SNS_POST_REQ_INIT  0x4BC91A0 /* sub_184BC91A0: micromsg.SnsPostRequest default constructor, size 416 */
#define WX_OFF_SNS_POST_REQ_COPY  0x4BE0310 /* sub_184BE0310: micromsg.SnsPostRequest copy constructor, dst in RCX, src in RDX */
#define WX_OFF_SNS_POST_REQ_DTOR  0x4BE1600 /* sub_184BE1600: micromsg.SnsPostRequest destructor */
#define WX_OFF_SNS_COMMENT_SEND   0x2BCF740 /* sub_182BCF740: CoCgiSendRequest wrapper for /cgi-bin/micromsg-bin/mmsnscomment, cgi type 213 */
#define WX_OFF_SNS_COMMENT_ENTRY  0x4CA29F0 /* sub_184CA29F0: upper SNS comment model -> SnsCommentRequest -> send wrapper */
#define WX_OFF_SNS_COMMENT_REQ_INIT 0x4BC9750 /* sub_184BC9750: micromsg.SnsCommentRequest stack/default init, size 40 */
#define WX_OFF_SNS_COMMENT_REQ_DTOR 0x4BFB7B0 /* sub_184BFB7B0: micromsg.SnsCommentRequest destructor */
#define WX_OFF_SNS_COMMENT_CONTENT_REQ_INIT 0x4BC9710 /* sub_184BC9710: micromsg.SnsCommentContentRequest init, size 64 */
#define WX_OFF_SNS_COMMENT_CONTENT_INIT 0x4BC9680 /* sub_184BC9680: micromsg.SnsCommentContent init, size 184 */
#define WX_OFF_SNS_LIKE_ENTRY     0x2BCCC70 /* sub_182BCCC70: official UI like/unlike entry; lookup live SNS model then sends mmsnscomment */
#define WX_OFF_SNS_DELETE_ENTRY   0x2BDF480 /* sub_182BDF480: official SNS feed delete entry; calls sub_184C79A60 -> mmsnsobjectop */
#define WX_OFF_SNS_DELETE_OBJECT_OP_ENTRY 0x4C79A60 /* sub_184C79A60(manager, shared_ptr-like model): builds op=1 SnsObjectOp from **model and sends mmsnsobjectop */
#define WX_OFF_SNS_SET_PRIVATE_ENTRY 0x2BDE1F0 /* sub_182BDE1F0(manager, shared_ptr-like model, state): official UI visibility-state path; calls sub_184C79BF0 -> op=4/state payload, then sub_182BB5CC0 */
#define WX_OFF_SNS_SET_PRIVATE_OBJECT_OP_ENTRY 0x4C79BF0 /* sub_184C79BF0(op_service, out_sp, model_sp, state): builds op=4 SnsObjectOp from **model and serializes visibility state payload */
#define WX_SNS_VISIBILITY_PUBLIC_STATE 1 /* ContentObject privacyType parser maps 0 -> 1; normal/public timeline state. */
#define WX_SNS_VISIBILITY_PRIVATE_STATE 2 /* model+552 == 2 is the only-self/private marker used by XML/post serialization. */
#define WX_SNS_SET_PRIVATE_STATE WX_SNS_VISIBILITY_PRIVATE_STATE
#define WX_OFF_SNS_SET_PRIVATE_LOCAL_UPDATE 0x2BB5CC0 /* sub_182BB5CC0(manager, model_sp, 1): local SNS list update used after op=5 objectop */
#define WX_OFF_SNS_SET_PRIVATE_OP5_ENTRY 0x2BDC880 /* sub_182BDC880: older op=5 visibility path retained for analysis; not used by bridge set-private. */
#define WX_OFF_SNS_SET_PRIVATE_OP5_OBJECT_OP_ENTRY 0x4C78960 /* sub_184C78960: older op=5 SnsObjectOp builder retained for analysis. */
#define WX_OFF_SNS_SET_PRIVATE_OLD_ENTRY 0x2BDB000 /* sub_182BDB000: op=9 path; confirmed not sufficient for only-self-visible on 4.1.9.23 */
#define WX_OFF_SNS_SET_PRIVATE_OLD_OBJECT_OP_ENTRY 0x4C79490 /* sub_184C79490: builds op=9 SnsObjectOp; retained for analysis only */
#define WX_OFF_SNS_SET_PRIVATE_UI_MODEL_COPY 0x3D9A450 /* sub_183D9A450(dst, source): UI path copies selected SNS item into the 944-byte model passed to visibility/delete entries */
#define WX_OFF_SNS_UI_MODEL_VALIDATE 0x3D9CB50 /* sub_183D9CB50(model): official UI SNS model validator; used to capture non-guessed model pointers by snsId */
#define WX_OFF_SNS_SET_PRIVATE_UI_MODEL_CTL_VT 0x849D478 /* off_18849D478: shared_ptr control block vtable used by sub_183B6A180 before sub_183D9A450 */
#define WX_OFF_SNS_LIKE_CTX_LOOKUP 0x2BD43F0 /* sub_182BD43F0(manager, out_ctx, key): fills 280-byte SNS like/comment UI context */
#define WX_OFF_SNS_LIKE_CTX_COPY  0x2BD46B0 /* sub_182BD46B0(out_ctx, src_ctx): copy-constructs 280-byte SNS like/comment UI context */
#define WX_OFF_SNS_LIKE_CTX_DTOR  0x2BC80C0 /* sub_182BC80C0(ctx): destroys 280-byte SNS like/comment UI context */
#define WX_OFF_SNS_MODEL_SERVICE  0x36E850  /* sub_18036E850: resolves SNS model/comment service used before sub_180EA6C10 */
#define WX_OFF_SNS_MODEL_LOOKUP   0xEA6C10  /* sub_180EA6C10: lookup live SNS model shared_ptr by SNS timeline key */
#define WX_OFF_SNS_COMMENT_AUX_STRING 0xEBC8B0 /* sub_180EBC8B0(service,out,key,flag): official SnsCommentContent field3 helper used by sub_184CA29F0 */
#define WX_OFF_SKBuiltinBuffer_INIT 0x5C5C20 /* sub_1805C5C20: micromsg.SKBuiltinBuffer_t constructor, size 32 */
#define WX_OFF_PROTOBUF_EMPTY_STRING 0xA533488 /* xmmword_18A533488: shared protobuf empty std::string sentinel */
#define WX_OFF_REVOKE_PATCH      0x2189067
#define WX_OFF_REVOKE_DISPATCH   0x2188DC0
#define WX_OFF_REVOKE_HANDLER    0x2188E40
#define WX_OFF_REVOKE_REAL       0x22040B0
#define WX_OFF_RT_DYNAMIC_CAST   0x664D9FC
#define WX_OFF_REVOKE_RTTI_SRC   0x9E0EE40
#define WX_OFF_REVOKE_RTTI_DST   0x9EC0960
#define WX_OFF_IMAGE_DL_INIT     0x18DBD0
#define WX_OFF_IMAGE_DL_CALL     0x2075330
#define WX_OFF_IMAGE_DL_SUBMIT   0x208AA80  /* sub_18208AA80(param): validates fullpath/root then enters WX_OFF_IMAGE_DL_CALL */
#define WX_OFF_SNS_IMAGE_DL_SUBMIT 0x208A8B0 /* sub_18208A8B0(param,flag): SNS download path -> CdnManager::StartSNSDownload */
#define WX_OFF_CDN_MANAGER_BY_NAME      0x0C91B60 /* sub_180C91B60(std::string* name): resolves CDN manager handle by name */
#define WX_OFF_CDN_MANAGER_RESOLVE      0x2074130 /* sub_182074130(handle): resolves CDN manager object */
#define WX_OFF_CDN_UPLOAD_LOGIC_SUBMIT  0x208AF70 /* sub_18208AF70(upload_param): validates local file then enters SNS CDN upload */
#define WX_OFF_CDN_UPLOAD_MANAGER_WRAP  0x2075160 /* sub_182075160(upload_param): resolves default CDN manager then uploads */
#define WX_OFF_CDN_UPLOAD_PARAM_CTOR    0x27EF2E0 /* sub_1827EF2E0(upload_param): large upload-param constructor */
#define WX_OFF_CDN_UPLOAD_PARAM_DTOR    0x27F01F0 /* sub_1827F01F0(upload_param): large upload-param destructor */
#define WX_OFF_CDN_UPLOAD_CORE          0x27E7530 /* sub_1827E7530(manager,param): CDN upload core wrapper */
#define WX_OFF_CDN_UPLOAD_CORE2         0x27EA450 /* sub_1827EA450(manager,param): validates upload param and schedules task */
#define WX_OFF_CDN_SNS_UPLOAD_TASK_SMALL 0x282F5D0 /* sub_18282F5D0: SNS small-file upload task path */
#define WX_OFF_CDN_TASK_FACTORY_FULL    0x28393A0 /* sub_1828393A0(out,root,manager_pair,CdnParams*,task_type): full CDN task factory */
#define WX_OFF_CDN_TASKMANAGER_ENQUEUE  0x4790FC0 /* sub_184790FC0(task_manager, task_pair): enqueue full CDN task */
#define WX_OFF_IMAGE_DOWNLOAD_HELPER 0x16B11B0 /* sub_1816B11B0(manager,out,obj,type,flags,opts): wraps obj into shared_ptr then enters 1816B12A0 image/media download path. */
#define WX_OFF_IMAGE_TASK_STAGE_EXEC 0x1633880 /* sub_181633880(stage_obj,ctx_pair): constructs/schedules the higher image download task whose body is 1816340C0. */
#define WX_OFF_IMAGE_TASK_STAGE_VT   0x82C38B8 /* off_1882C38B8: 920-byte stage object vtable used by WX_OFF_IMAGE_TASK_STAGE_EXEC. */
#define WX_OFF_GLOBAL_CONTEXT_ROOT   0x1F460   /* sub_18001F460(): root object used by UI/task callers before sub_1801A7A30 interface casts. */
#define WX_OFF_CONTEXT_INTERFACE_CAST 0x1A7A30 /* sub_1801A7A30(src,out,iface_tag): resolves a shared_ptr for a requested interface tag. */
#define WX_OFF_IMAGE_TASK_TARGET_IFACE 0x805B9F8 /* off_18805B9F8: interface tag used by real callers before 1815E9F90. */
#define WX_OFF_STD_STRING_INIT   0x94080   /* sub_180094080(std::string-like slot): initializes empty string/vector slot used by download opts. */
#define WX_OFF_STD_WSTRING_COPY  0x94200   /* sub_180094200(dst, src): std::wstring copy used by the official SNS video UI adapter. */
#define WX_OFF_STD_STRING_DTOR   0x7580    /* sub_180007580(std::string-like slot): destructor paired with WX_OFF_STD_STRING_INIT. */
#define WX_OFF_SEND_ITEM_ROUTE_DTOR 0x1B5DA0 /* sub_1801B5DA0(send_item/route_desc): destructor used after sub_1809D8F80 output. */
#define WX_OFF_IMAGE_DECODE      0x972880
#define WX_OFF_QUEUE_HOOK        0x3665250
#define WX_OFF_APPMSG_CTOR       0x9DE610   /* sub_1809DE610(inner): 直接构造富 appmsg payload 对象(vtable 0x80D1278) + 各 std::string + 空 refer-dict 壳@+0x5F8 */
#define WX_OFF_APPMSG_XML_PARSE  0xA1B540   /* sub_180A1B540(appmsg_inner, src_msg): parse msg+appmsg XML from src_msg+0x160 into rich appmsg fields */
#define WX_OFF_APPMSG_HOLDER_VT  0x80D11E8  /* off_1880D11E8: 富 appmsg holder vtable(inner = holder+0x10) */
#define WX_OFF_APPMSG_INNER_VT   0x80D1278  /* 富 appmsg payload/content vtable;序列化器 sub_180A19440 直接读它的结构化字段 */
#define WX_OFF_VOICE_TRANS_MENU_SUBMIT_WRAP 0x16C6390 /* 1816C6390: official menu wrapper; send_item -> msg shared_ptr(+656) -> 1816C65F0. */
#define WX_OFF_VOICE_TRANS_SUBMIT_OR_DOWNLOAD 0x16C65F0 /* 1816C65F0: UI/service voice trans submit or voice-download wrapper. */
#define WX_OFF_AUTO_VOICE_TRANS_TASK_SUBMIT   0x3522DE0 /* 183522DE0: lower auto voice-transcribe task submit. */
#define WX_OFF_VOICE_TRANS_APPLY_RESULT       0x3523940 /* 183523940: applies final voice trans text to message/UI. */
#define WX_OFF_VOICE_TRANS_REPORT_RESULT      0x507F550 /* 18507F550: VoiceTrans business result callback, code 7 carries final text. */
#define WX_OFF_VOICE_EXT_RTTI_SRC             0x9E0EE40 /* off_189E0EE40, message extension source RTTI. */
#define WX_OFF_VOICE_EXT_RTTI_DST             0x9EC08D0 /* off_189EC08D0, voice-message extension RTTI. */

/* Red envelope open bypass, Weixin.dll 4.1.9.23. */
#define WX_OFF_HB_UI_PAGECTX_JMP  0x3A49021  /* 183A49021: RCX is page_ctx, original tail-jumps to producer. */
#define WX_OFF_HB_OPEN_PRODUCER   0x3A686A0  /* 183A686A0(page_ctx): builds/schedules open task. */
#define WX_OFF_HB_PAGECTX_SETTER  0x3A452B0  /* 183A452B0(owner, shared_page_ctx): writes owner+0x188=page_ctx before Open click. */
#define WX_OFF_HB_NULL_GUARD_HELPER 0x4EF4C0 /* 1804EF4C0(rcx): UI cleanup helper; crashes if called with rcx=NULL. */
#define WX_OFF_HB_ROUTE_CANDIDATE 0x5365DD0  /* 185365DD0: URL/route-like candidate; contains sendid and calls 183A68BF0. Probe only. */
#define WX_OFF_HB_TASK_FACTORY    0x3A68BF0  /* 183A68BF0: shared route/page task factory used by card, URL route, and open producer paths. */
#define WX_OFF_HB_RECEIVE_ROUTE_BUILDER 0x5EA6140 /* 185EA6140(a1): upper receive route/task factory; calls 182974A70 then 183A68BF0. */
#define WX_OFF_HB_ROUTE_DISPATCHER 0x40A3520 /* 1840A3520(a1,a2): appmsg route bundle lookup/copy before temporary route vector. Probe only. */
#define WX_OFF_HB_ROUTE_TEMP_VECTOR 0x4120B60 /* 184120B60(a1,route_desc_vector,a3): temporary vector owner; calls 185705820. Probe only. */
#define WX_OFF_HB_ROUTE_VECTOR_BUILDER 0x5705820 /* 185705820(route_mgr,route_desc_vector): iterates 0x388-byte route_desc entries. Probe only. */
#define WX_OFF_HB_ROUTE_CTX_FACTORY 0x527C7E0 /* 18527C7E0(out,route_mgr,route_desc,flag): classifies route_desc and builds route_ctx. Probe only. */
#define WX_OFF_HB_ROUTE_CLASSIFIER 0x527A1A0 /* 18527A1A0(route_desc): returns route type; 49/2001 hongbao receive => 39. Probe only. */
#define WX_OFF_HB_ROUTE_ITEM_DISPATCH 0x5333E20 /* 185333E20(route_mgr_item?,event): official route item dispatcher; for non-27/29 route types calls route_ctx vfunc+0xE0, which is 185EA6140 for type-39. Probe only. */
#define WX_OFF_HB_CARD_ROUTE_OUTER 0x5EA85A0 /* 185EA85A0(a1, shared_payload): earliest confirmed card-click route outer. */
#define WX_OFF_HB_CARD_ROUTE_DISPATCH 0x5EA6830 /* 185EA6830(a1, shared_payload, weak/ref pair): dispatches toward page_ctx builder. */
#define WX_OFF_HB_WEAKPTR_FORWARD 0x3A0B780 /* 183A0B780(a1, a2, a3_shared): weak_ptr forwarder to page_ctx builder. */
#define WX_OFF_HB_PAGECTX_BUILDER 0x1B32480 /* 181B32480(a1, a2, a3_shared): builds page_ctx before setter callback. */
#define WX_HB_CONTROLLER_SENDITEM_OFF 0x120 /* controller(18527C7E0 case-39 建)内嵌 send_item(vtable 0x7FB6B58)。185EA6140 经 hb_build_receive_mid_ctx_candidate 从这里读红包数据(sendid/url/sign)发起 receivewxhb。 */
#define WX_OFF_HB_MESSAGE_PARSE 0x2A48A50 /* 182A48A50(a1, vector): no-click hongbao message parser/update-db path. */
#define WX_OFF_HB_NEWSYNC_ADDMSG_BATCH 0x2AE0890 /* 182AE0890(raw_addmsg_vector, ok, flags): earliest filtered AddMsg batch; elements are 0x78 bytes. */
#define WX_OFF_HB_RAW_TO_LOCAL_MSG 0x09D2F00 /* 1809D2F00(dst_0x290, raw_0x78): official newsync AddMsg -> LocalMsg constructor. */
#define WX_OFF_HB_LOCAL_MSG_FINALIZE 0x09D6440 /* 1809D6440(local_msg, 1): same post-construction step used by 18166C6A0. */
#define WX_OFF_HB_LOCAL_MSG_DTOR 0x01CB270 /* 1801CB270(local_msg): destructor paired with WX_OFF_HB_RAW_TO_LOCAL_MSG. */
#define WX_OFF_HB_SOURCE_SUBMIT 0x166A3C0 /* 18166A3C0(service, parsed_source_vector, ...): AddMessageListToDB + parsed 656-byte sources. */
#define WX_OFF_HB_ROUTE_DESC_FROM_SOURCE 0x16D86A0 /* 1816D86A0(out_route_desc, source): source -> one 0x388 route_desc. */
#define WX_OFF_HB_ROUTE_DESC_DTOR 0x1B5DA0 /* 1801B5DA0(route_desc): destroys one route_desc element. */
#define WX_OFF_HB_TASK_FACTORY_INNER 0x3A6A320 /* 183A6A320: builds route task args; vcall at +0x38 object triggers receive task. */
#define WX_OFF_HB_RECEIVE_MID_BUILDER 0x4A60500 /* 184A60500(a1, holder, payload): stores receive mid ctx in holder+0x38. Factory_receive(cgiTable,out,msgCtx). */
#define WX_OFF_HB_RECEIVE_TASK_BUILDER 0x4A64BB0 /* 184A64BB0(parent_ctx,arg): builds receive_ctx and schedules receivewxhb. Launch_receive → 1581. */
#define WX_OFF_HB_RECEIVEWXHB_ENTRY 0x4A65050 /* 184A65050: receivewxhb request constructor/path entry. */
#define WX_OFF_HB_RECEIVE_REQUEST_LOOP 0x4A68430 /* 184A68430: receivewxhb request loop; config={retry_count,timeout_ms}. */
#define WX_OFF_HB_RECEIVE_ATTEMPT 0x4A68E80 /* 184A68E80: one receivewxhb CGI attempt and result wait. */
#define WX_OFF_HB_MID_CTX_FROM_TIMING 0x4A607B0 /* 184A607B0: Factory_open(cgiTable,out,msgCtx,timing*) copies timing into mid context. */
#define WX_OFF_HB_OPEN_CTX_BUILDER 0x4A6C0A0 /* 184A6C0A0: Launch_openwxhb(parent,a2) → 1685. */
#define WX_OFF_HB_OPENWXHB_ENTRY 0x4A6CA70 /* 184A6CA70: openwxhb request constructor/path entry. */
/* Cold path (frida_cold_hongbao_auto.py): no UI preheat, no parent reuse. */
#define WX_OFF_HB_SERVICE_START_RECV 0x2974A70 /* Service_StartReceive(service,out,msgCtx) → Factory_receive (UI-near). */
#define WX_OFF_HB_SERVICE_START_OPEN 0x2974A80 /* Service_StartOpen(service,out,msgCtx,timing*). */
#define WX_OFF_HB_PARSE_EXTRA        0x5B9C2A0 /* wx_pb_HongBaoRes_ParseExtra; sendId@+0x30 timing@+0x188 status@+0x10. */
#define WX_OFF_HB_PARSE_OPEN_RESULT  0x5B9D2C0 /* Open result parser; retcode+8, receiveStatus+0x98, own amount+0x158. */
#define WX_OFF_SVC_GET_LOCATOR       0x01F460  /* wx_svc_GetGlobalSvcLocator() -> locator*. */
#define WX_OFF_SVC_RESOLVE_BY_TYPE   0x1A7A30  /* wx_svc_ResolveByType(resolver, out_sp, &typeKey). */
#define WX_OFF_HB_TYPE_KEY_PTR       0x813AD78 /* &descriptor for HongBao DI typeKey. */
#define WX_OFF_HB_SVC_VTABLE         0x84E2988 /* HongBao service vtable (validation). */
#define WX_OFF_HB_MSGCTX_VTABLE      0x7FB6B58 /* msgCtx/send_item vtable (validation); same as WX_OFF_SEND_ITEM_VT. */
#define WX_HB_PAGE_RECEIVE_RESULT_OFF 0x3C0
#define WX_HB_RECEIVE_TIMING_OFF  0x188
#define WX_HB_RES_STATUS_OFF      0x10
#define WX_HB_RES_SENDID_OFF      0x30
#define WX_HB_RES_TIMING_OFF      0x188
#define WX_HB_OPEN_PARENT_TIMING_OFF 0x390
#define WX_HB_OPEN_PARENT_CGI_OFF    0x3B0
#define WX_HB_MSGCTX_SIZE         0x400
#define WX_HB_COLD_OUT_SIZE       0x60
#define WX_HB_COLD_A2_SIZE        0x100
#define WX_HB_MSGCTX_APPEXT_OFF   0x270
#define WX_SVC_LOCATOR_VT_GET_CONTAINER 0x60
#define WX_SVC_CONTAINER_VT_GET_RESOLVER 0x30

/* (引用旧 Strategy-B / CGI 路线的偏移定义已删除 — 字段表见 send_quote_v2 实测注释) */

/* Source-confirmed receive field offsets. _Lib0Cmd666("108") means hex. */
#define WX_MSG_ID_OFF            0x108
#define WX_MSG_LOCAL_ID_OFF      0x104
#define WX_MSG_TYPE_OFF          0x008
#define WX_MSG_SUBTYPE_OFF       0x010
#define WX_MSG_TS_OFF            0x110
#define WX_MSG_FROM_OFF          0x018
#define WX_MSG_SELF_OFF          0x038
#define WX_MSG_CONTENT_OFF       0x160
#define WX_MSG_SIGNATURE_OFF     0x180
#define WX_MSG_REVOKE_ORIGID_OFF 0x1A8

#define WX_REVOKE_CTX_OBJ_OFF      0x230
#define WX_REVOKE_PAYLOAD_MSGID_OFF 0x1A8
#define WX_REVOKE_PAYLOAD_TALKER_OFF 0x1D0
#define WX_REVOKE_PAYLOAD_TIME_OFF 0x290

#endif /* WX_BRIDGE_OFFSETS_H */
