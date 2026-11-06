#ifndef WX_BRIDGE_FRIEND_ACCEPT_H
#define WX_BRIDGE_FRIEND_ACCEPT_H

#include "wx_bridge_platform.h"

typedef struct WxbFriendAcceptResult {
    int ok;
    int error_code;
    int opcode;
    DWORD exception_code;
    char message[512];
} WxbFriendAcceptResult;

typedef struct WxbFriendAutoStatus {
    int enabled;
    LONG queued;
    LONG succeeded;
    LONG failed;
    LONG parse_failed;
    LONG deduplicated;
} WxbFriendAutoStatus;

int wxb_friend_accept(const char *encrypt_username,
                      const char *ticket,
                      int scene,
                      int friend_flag,
                      int wait_ms,
                      WxbFriendAcceptResult *result);
int wxb_friend_accept_xml(const char *xml,
                          int wait_ms,
                          WxbFriendAcceptResult *result);
void wxb_friend_auto_handle_message(int64_t msg_id,
                                    int msg_type,
                                    int is_incoming,
                                    const char *content);
void wxb_friend_auto_set_enabled(int enabled);
int wxb_friend_auto_enabled(void);
void wxb_friend_auto_get_status(WxbFriendAutoStatus *status);

#endif /* WX_BRIDGE_FRIEND_ACCEPT_H */
