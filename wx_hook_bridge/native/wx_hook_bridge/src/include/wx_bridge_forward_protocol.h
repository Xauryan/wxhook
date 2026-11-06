#ifndef WX_BRIDGE_FORWARD_PROTOCOL_H
#define WX_BRIDGE_FORWARD_PROTOCOL_H

#include "wx_bridge_platform.h"

void wxb_forward_build_msgsource(char *buf, int max, int64_t source_msg_id,
                                 const char *source_sender_wxid,
                                 const char *self_wxid);
void wxb_forward_build_source_marker(char *buf, int max, int64_t source_msg_id,
                                     const char *source_sender_wxid,
                                     const char *source_from_wxid,
                                     const char *source_to_wxid);
int64_t wxb_parse_forward_source_orig_msgid(const char *msgsource);
int wxb_forward_effective_subtype(int source_type, int source_subtype);
int wxb_forward_effective_subtype_for_content(int source_type, int source_subtype,
                                              const char *content);
int64_t wxb_forward_submit_q8(int source_type, int submit_subtype);
int wxb_forward_submit_f128(int source_type, int submit_subtype);
int wxb_forward_submit_f12c(int source_type, int submit_subtype);
int32_t wxb_forward_next_local_id(int source_local_id);
int64_t wxb_forward_next_msg_id(int64_t source_msg_id);
int wxb_forward_variant_from_type(int source_type, int source_subtype);
int wxb_forward_kind_from_type(int source_type, int source_subtype);

#define forward_build_msgsource wxb_forward_build_msgsource
#define forward_build_source_marker wxb_forward_build_source_marker
#define parse_forward_source_orig_msgid wxb_parse_forward_source_orig_msgid
#define forward_effective_subtype wxb_forward_effective_subtype
#define forward_effective_subtype_for_content wxb_forward_effective_subtype_for_content
#define forward_submit_q8 wxb_forward_submit_q8
#define forward_submit_f128 wxb_forward_submit_f128
#define forward_submit_f12c wxb_forward_submit_f12c
#define forward_next_local_id wxb_forward_next_local_id
#define forward_next_msg_id wxb_forward_next_msg_id
#define forward_variant_from_type wxb_forward_variant_from_type
#define forward_kind_from_type wxb_forward_kind_from_type

#endif /* WX_BRIDGE_FORWARD_PROTOCOL_H */
