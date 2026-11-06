#ifndef WX_BRIDGE_WORKER_CONTROL_H
#define WX_BRIDGE_WORKER_CONTROL_H

#include "wx_bridge_platform.h"

HANDLE wxb_acquire_worker_handoff_lock(void);
void wxb_release_worker_handoff_lock(HANDLE h);
void wxb_write_worker_owner_token(void);
int wxb_worker_owner_still_current(void);
void wxb_clear_worker_owner_token_if_current(void);
void wxb_clear_outbox(void);

#define acquire_worker_handoff_lock wxb_acquire_worker_handoff_lock
#define release_worker_handoff_lock wxb_release_worker_handoff_lock
#define write_worker_owner_token wxb_write_worker_owner_token
#define worker_owner_still_current wxb_worker_owner_still_current
#define clear_worker_owner_token_if_current wxb_clear_worker_owner_token_if_current
#define clear_outbox wxb_clear_outbox

#endif /* WX_BRIDGE_WORKER_CONTROL_H */
