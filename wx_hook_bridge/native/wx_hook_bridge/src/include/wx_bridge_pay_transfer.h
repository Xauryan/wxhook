#ifndef WX_BRIDGE_PAY_TRANSFER_H
#define WX_BRIDGE_PAY_TRANSFER_H

#include "wx_bridge_platform.h"

int accept_transfer_to_result(const char *seq,
                              const char *trans_id,
                              const char *transfer_attach,
                              int32_t invalid_time,
                              const char *group_username);

int refuse_transfer_to_result(const char *seq,
                              const char *trans_id,
                              int32_t invalid_time,
                              const char *group_username);

#endif /* WX_BRIDGE_PAY_TRANSFER_H */
