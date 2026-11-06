#ifndef WX_BRIDGE_FORWARD_SEND_H
#define WX_BRIDGE_FORWARD_SEND_H

#include "wx_bridge_platform.h"

int send_forward_any_native(const char *to_wxid, const char *content,
                            int source_type, int source_subtype,
                            int source_local_id, int64_t source_msg_id,
                            int32_t source_time,
                            const char *source_from_wxid,
                            const char *source_to_wxid,
                            const char *source_sender_wxid,
                            const char *source_msgsource,
                            int *out_variant,
                            int *out_queue_status,
                            int probe_only);
int send_forward_high_native(const char *to_wxid, const char *content,
                             int source_type, int source_subtype,
                             int source_local_id, int64_t source_msg_id,
                             int32_t source_time,
                             const char *source_from_wxid,
                             const char *source_to_wxid,
                             const char *source_sender_wxid,
                             const char *source_msgsource,
                             int *out_variant,
                             int *out_queue_status,
                             int *out_retained,
                             int *out_fallback_kind,
                             int probe_only);

#endif /* WX_BRIDGE_FORWARD_SEND_H */
