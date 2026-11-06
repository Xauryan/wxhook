#ifndef WX_BRIDGE_HONGBAO_STATUS_H
#define WX_BRIDGE_HONGBAO_STATUS_H

#include "wx_bridge_platform.h"

void wxb_hb_write_state(void);
int wxb_hb_is_receive_candidate(int msg_type_int, int msg_subtype_int, const char *content);
void wxb_hb_log_receive_candidate(int64_t msg_base,
                                  const char *id_s,
                                  const char *local_id_s,
                                  int msg_type_int,
                                  int msg_subtype_int,
                                  const char *from,
                                  const char *self_id,
                                  const char *peer_wxid,
                                  const char *sender_wxid,
                                  const char *direction,
                                  int is_incoming,
                                  int is_outgoing,
                                  const char *content);

#define hb_write_state wxb_hb_write_state
#define hb_is_receive_candidate wxb_hb_is_receive_candidate
#define hb_log_receive_candidate wxb_hb_log_receive_candidate

#endif /* WX_BRIDGE_HONGBAO_STATUS_H */
