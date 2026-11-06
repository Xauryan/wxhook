#ifndef WX_BRIDGE_ANTI_REVOKE_H
#define WX_BRIDGE_ANTI_REVOKE_H

#include "wx_bridge_platform.h"

int wxb_set_anti_revoke(int enable);
void wxb_restore_anti_revoke_state(void);

#define set_anti_revoke wxb_set_anti_revoke
#define restore_anti_revoke_state wxb_restore_anti_revoke_state

#endif /* WX_BRIDGE_ANTI_REVOKE_H */
