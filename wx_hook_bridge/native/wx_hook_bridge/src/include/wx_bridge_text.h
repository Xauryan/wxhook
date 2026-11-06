#ifndef WX_BRIDGE_TEXT_H
#define WX_BRIDGE_TEXT_H

#include "wx_bridge_platform.h"

void wxb_trim_trailing_crlf(char *s);
void wxb_i64toa10(int64_t v, char *out);
int64_t wxb_a2i64(const char *s);
void wxb_append_raw(char **p, char *end, const char *s);
void wxb_append_escaped(char **p, char *end, const char *s);
void wxb_append_xml_escaped(char **p, char *end, const char *s);
int wxb_str_eq(const char *a, const char *b);
int wxb_str_has(const char *s, const char *needle);
const char *wxb_str_find_sub(const char *s, const char *needle);
int wxb_copy_until_any(char *dst, int max, const char *src, const char *stops);
int wxb_extract_between_markers(const char *src, const char *begin, const char *end_marker, char *dst, int max);
int wxb_appmsg_content_is_file_like(int msg_type, int msg_subtype, const char *content);
int wxb_extract_query_value(const char *src, const char *key, char *dst, int max);
int wxb_is_chatroom_id(const char *s);
int wxb_is_wxid_like(const char *s);
void wxb_safe_copy(char *dst, int max, const char *src);
int wxb_extract_group_sender(const char *content, char *sender, int max);

#define trim_trailing_crlf wxb_trim_trailing_crlf
#define i64toa10 wxb_i64toa10
#define a2i64 wxb_a2i64
#define append_raw wxb_append_raw
#define append_escaped wxb_append_escaped
#define append_xml_escaped wxb_append_xml_escaped
#define str_eq wxb_str_eq
#define str_has wxb_str_has
#define str_find_sub wxb_str_find_sub
#define copy_until_any wxb_copy_until_any
#define extract_between_markers wxb_extract_between_markers
#define appmsg_content_is_file_like wxb_appmsg_content_is_file_like
#define extract_query_value wxb_extract_query_value
#define is_chatroom_id wxb_is_chatroom_id
#define is_wxid_like wxb_is_wxid_like
#define safe_copy wxb_safe_copy
#define extract_group_sender wxb_extract_group_sender

#endif /* WX_BRIDGE_TEXT_H */
