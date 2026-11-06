#ifndef WX_BRIDGE_REVOKE_PROTOCOL_H
#define WX_BRIDGE_REVOKE_PROTOCOL_H

#include "wx_bridge_platform.h"

int64_t wxb_parse_revoke_msgid(const char *xml);
void wxb_extract_tag_value(const char *xml, const char *tag, char *out, int max);
int64_t wxb_parse_revoke_time(const char *xml);
int wxb_is_revoke_msg(const char *content, int msg_type_int);
int wxb_is_revocable_msg(int msg_type_int);
int wxb_is_normal_msg(int msg_type_int);
const char *wxb_revoke_msg_type_label(int msg_type, int msg_subtype);
const char *wxb_revoke_msg_type_label_for_content(int msg_type, int msg_subtype,
                                                  const char *content);

#define parse_revoke_msgid wxb_parse_revoke_msgid
#define extract_tag_value wxb_extract_tag_value
#define parse_revoke_time wxb_parse_revoke_time
#define is_revoke_msg wxb_is_revoke_msg
#define is_revocable_msg wxb_is_revocable_msg
#define is_normal_msg wxb_is_normal_msg
#define revoke_msg_type_label wxb_revoke_msg_type_label
#define revoke_msg_type_label_for_content wxb_revoke_msg_type_label_for_content

#endif /* WX_BRIDGE_REVOKE_PROTOCOL_H */
