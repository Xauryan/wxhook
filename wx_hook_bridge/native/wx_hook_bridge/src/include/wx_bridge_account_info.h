#ifndef WX_BRIDGE_ACCOUNT_INFO_H
#define WX_BRIDGE_ACCOUNT_INFO_H

#include "wx_bridge_platform.h"

int wxb_self_profile_json(char *out, int max);
char *wxb_contact_list_json(int limit);

#endif /* WX_BRIDGE_ACCOUNT_INFO_H */
