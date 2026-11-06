#ifndef WX_BRIDGE_FORWARD_BUILDERS_H
#define WX_BRIDGE_FORWARD_BUILDERS_H

#include "wx_bridge_platform.h"

int is_forward_manager_candidate_any(int64_t base, int64_t cand);
void forward_remember_manager(int64_t base, int64_t manager);
int64_t find_forward_manager(int64_t base);
int64_t find_forward_controller(int64_t base);
void forward_build_fake_request_msg(int64_t base, int64_t src_ctl, int64_t src_msg,
                                    const char *source_from_wxid,
                                    const char *source_to_wxid,
                                    const char *source_sender_wxid,
                                    const char *content,
                                    const char *source_msgsource,
                                    int source_type,
                                    int source_subtype,
                                    int source_local_id,
                                    int64_t source_msg_id,
                                    int32_t source_time,
                                    BYTE **pool,
                                    BYTE *pool_end);
void forward_build_submit_msg(int64_t base, int64_t msg,
                              const char *to_wxid,
                              const char *content,
                              int source_type,
                              int source_subtype,
                              int source_local_id,
                              int64_t source_msg_id,
                              int32_t source_time,
                              const char *source_from_wxid,
                              const char *source_to_wxid,
                              const char *source_sender_wxid,
                              const char *source_msgsource,
                              int target_fields,
                              BYTE **pool,
                              BYTE *pool_end);
int forward_fill_high_send_item(int64_t base, int64_t item,
                                const char *content,
                                int source_type,
                                int source_subtype,
                                int source_local_id,
                                int64_t source_msg_id,
                                int32_t source_time,
                                const char *source_from_wxid,
                                const char *source_to_wxid,
                                const char *source_sender_wxid,
                                const char *source_msgsource,
                                int64_t src_msg,
                                int64_t src_ctl,
                                BYTE **pool,
                                BYTE *pool_end);
void quote_build_fake_source_msg(int64_t base,
                                 int64_t src_ctl,
                                 int64_t src_msg,
                                 int64_t quoted_svrid,
                                 int32_t quoted_local_id,
                                 const char *quoted_content,
                                 const char *quoted_from,
                                 int quoted_type,
                                 int32_t createtime,
                                 const char *refer_chat,
                                 const char *source_to,
                                 const char *quoted_msgsource,
                                 BYTE **pool,
                                 BYTE *pool_end);

#endif /* WX_BRIDGE_FORWARD_BUILDERS_H */
