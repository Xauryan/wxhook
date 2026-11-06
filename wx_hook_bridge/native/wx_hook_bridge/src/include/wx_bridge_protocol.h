#ifndef WX_BRIDGE_PROTOCOL_H
#define WX_BRIDGE_PROTOCOL_H

#include "wx_bridge_platform.h"

int wxb_post_json_to_python(const char *json, DWORD len);
int wxb_post_json_to_python_status(const char *json, DWORD len, DWORD *http_status);
int wxb_configure_push(const WCHAR *host, INTERNET_PORT port,
                       const WCHAR *path, DWORD timeout_ms);
int wxb_configure_push_ex(const WCHAR *host, INTERNET_PORT port,
                          const WCHAR *path, DWORD timeout_ms, int secure);
int wxb_configure_push_tcp(const char *host, INTERNET_PORT port,
                           DWORD timeout_ms);
void wxb_set_receive_push_enabled(int enabled);
int wxb_receive_push_enabled(void);
void wxb_write_status(const char *text);
int wxb_hb_auto_enabled(void);
int wxb_hb_auto_one_shot_enabled(void);
int wxb_hb_noclick_enabled(void);
int wxb_hb_noclick_one_shot_enabled(void);
int wxb_hb_noclick_direct_enabled(void);
int wxb_restore_code_if_changed(BYTE *target, const BYTE *expected, SIZE_T len, const char *msg, int hb_log);
void wxb_write_result(const char *seq, int ok, const char *command, const char *message);
void wxb_write_forward_result(const char *seq, int ok, const char *message, int source_type, int source_subtype, int variant, int queue_status, int retained, int fallback_kind);

#define post_json_to_python wxb_post_json_to_python
#define configure_push wxb_configure_push
#define configure_push_ex wxb_configure_push_ex
#define configure_push_tcp wxb_configure_push_tcp
#define set_receive_push_enabled wxb_set_receive_push_enabled
#define receive_push_enabled wxb_receive_push_enabled
#define write_status wxb_write_status
#define hb_auto_enabled wxb_hb_auto_enabled
#define hb_auto_one_shot_enabled wxb_hb_auto_one_shot_enabled
#define hb_noclick_enabled wxb_hb_noclick_enabled
#define hb_noclick_one_shot_enabled wxb_hb_noclick_one_shot_enabled
#define hb_noclick_direct_enabled wxb_hb_noclick_direct_enabled
#define restore_code_if_changed wxb_restore_code_if_changed
#define write_result wxb_write_result
#define write_forward_result wxb_write_forward_result

#endif /* WX_BRIDGE_PROTOCOL_H */
