#ifndef WX_BRIDGE_CHATROOM_MEMBER_H
#define WX_BRIDGE_CHATROOM_MEMBER_H

#include "wx_bridge_platform.h"

int create_chatroom_to_result(const char *seq,
                              const char *member_ids,
                              const char *topic,
                              int wait_ms);

int add_chatroom_member_to_result(const char *seq,
                                  const char *roomid,
                                  const char *member_wxid,
                                  int wait_ms);

int invite_chatroom_member_to_result(const char *seq,
                                     const char *roomid,
                                     const char *member_wxid,
                                     int wait_ms);

int del_chatroom_member_to_result(const char *seq,
                                  const char *roomid,
                                  const char *member_wxid,
                                  int wait_ms);

int leave_chatroom_to_result(const char *seq,
                             const char *roomid,
                             const char *self_wxid,
                             int wait_ms,
                             int flag);

#endif /* WX_BRIDGE_CHATROOM_MEMBER_H */
