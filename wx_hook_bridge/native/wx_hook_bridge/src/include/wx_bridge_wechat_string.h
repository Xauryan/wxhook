#ifndef WX_BRIDGE_WECHAT_STRING_H
#define WX_BRIDGE_WECHAT_STRING_H

#include "wx_bridge_platform.h"

int wxb_wx_read_str(int64_t s, char *out, int max);
int wxb_wx_read_sso_str(int64_t s, char *out, int max);
int64_t wxb_resolve_revoke_payload(int64_t a3);
void wxb_wx_set_str_arena(int64_t header, const char *utf8, int len, BYTE **pool, BYTE *pool_end);
void wxb_wx_set_std_string_arena(int64_t header, const char *utf8, int len, BYTE **pool, BYTE *pool_end);
void wxb_wx_set_std_string_heap(int64_t base, int64_t header, const char *utf8);
void wxb_wx_free_std_string_heap(int64_t base, int64_t header);
void wxb_wx_set_wstr_arena(int64_t header, const char *utf8, int len, BYTE **pool, BYTE *pool_end);
void wxb_set_utf8_ptr_fields(int64_t st, int off_ptr, int off_chars, int off_bytes,
                             const char *s, BYTE **pool, BYTE *pool_end);

#define wx_read_str wxb_wx_read_str
#define wx_read_sso_str wxb_wx_read_sso_str
#define resolve_revoke_payload wxb_resolve_revoke_payload
#define wx_set_str_arena wxb_wx_set_str_arena
#define wx_set_std_string_arena wxb_wx_set_std_string_arena
#define wx_set_std_string_heap wxb_wx_set_std_string_heap
#define wx_free_std_string_heap wxb_wx_free_std_string_heap
#define wx_set_wstr_arena wxb_wx_set_wstr_arena
#define set_utf8_ptr_fields wxb_set_utf8_ptr_fields

#endif /* WX_BRIDGE_WECHAT_STRING_H */
