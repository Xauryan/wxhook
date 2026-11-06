#ifndef WX_BRIDGE_CONTACT_LOOKUP_H
#define WX_BRIDGE_CONTACT_LOOKUP_H

#include "wx_bridge_platform.h"

int wxb_is_internal_display_id(const char *s);
int wxb_is_displayable_name_for_id(const char *id, const char *name);
int wxb_lookup_contact_display_name(const char *wxid, char *out, int max);
int wxb_lookup_room_member_display_name(const char *roomid, const char *member_wxid,
                                        char *out, int max);

#define is_internal_display_id wxb_is_internal_display_id
#define is_displayable_name_for_id wxb_is_displayable_name_for_id
#define lookup_contact_display_name wxb_lookup_contact_display_name
#define lookup_room_member_display_name wxb_lookup_room_member_display_name

#endif /* WX_BRIDGE_CONTACT_LOOKUP_H */
