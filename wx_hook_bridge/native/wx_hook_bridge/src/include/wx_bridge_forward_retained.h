#ifndef WX_BRIDGE_FORWARD_RETAINED_H
#define WX_BRIDGE_FORWARD_RETAINED_H

#include "wx_bridge_platform.h"

#define MODEL_ITEM_BYTES 0x800

void *find_retained_si(int64_t msgid);
int retained_si_origin(int64_t msgid);
void retain_send_item(int64_t msgid, int64_t msg_base);
int hydrate_retained_send_item_from_fields(int64_t msgid,
                                           const char *content,
                                           int source_type,
                                           int source_subtype,
                                           int source_local_id,
                                           int32_t source_time,
                                           const char *source_from_wxid,
                                           const char *source_to_wxid,
                                           const char *source_sender_wxid,
                                           const char *source_msgsource);
void *find_retained_model(int64_t msgid);
int retained_model_in_memory(int64_t msgid);
void *find_retained_model_by_local_id(int32_t local_id);
void retain_model(int64_t msgid, int64_t msg_base);

#endif /* WX_BRIDGE_FORWARD_RETAINED_H */
