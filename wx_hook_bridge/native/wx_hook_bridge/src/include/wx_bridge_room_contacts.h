#ifndef WX_BRIDGE_ROOM_CONTACTS_H
#define WX_BRIDGE_ROOM_CONTACTS_H

#include "wx_bridge_platform.h"

void poll_auto_contact_names(void);
int schedule_auto_contact_names(const char *reason);
void clear_room_task(void);
void poll_room_task(void);
int start_room_list(const char *seq, const char *roomid);
int schedule_auto_room_members(const char *roomid, const char *sender_wxid,
                               const char *reason);
void poll_auto_room_members(void);

#endif /* WX_BRIDGE_ROOM_CONTACTS_H */
