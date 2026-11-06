#ifndef WX_BRIDGE_REVOKE_FORWARD_H
#define WX_BRIDGE_REVOKE_FORWARD_H

#include "wx_bridge_platform.h"

int emit_revoke_notice_event(int64_t orig_msgid, const char *talker,
                             int64_t revoketime,
                             const char *notice_prefix,
                             const char *signature);
int handle_revoke_notice_by_id(int64_t orig_msgid, const char *talker,
                               int64_t revoketime, const char *source_tag);
void process_revoke_forward_jobs(void);

#endif /* WX_BRIDGE_REVOKE_FORWARD_H */
