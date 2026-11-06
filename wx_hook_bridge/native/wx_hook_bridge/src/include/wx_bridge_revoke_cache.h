#ifndef WX_BRIDGE_REVOKE_CACHE_H
#define WX_BRIDGE_REVOKE_CACHE_H

#include "wx_bridge_platform.h"

typedef struct RevokeCachedMsgSnapshot {
    int64_t msgid;
    int32_t local_id;
    int32_t msg_type;
    int32_t msg_subtype;
    int32_t createtime;
    char from[256];
    char peer[256];
    char self[256];
    char content[4096];
    char msgsource[1024];
    char sender[256];
    int content_len;
} RevokeCachedMsgSnapshot;

void cache_msg(int64_t msgid, int32_t local_id, int32_t msg_type,
               int32_t msg_subtype, int32_t createtime,
               const char *from, const char *peer,
               const char *self_id, const char *content, int content_len,
               const char *sender, const char *msgsource);
int find_cached_msg_snapshot(int64_t msgid, RevokeCachedMsgSnapshot *out);
int find_recent_cached_revocable_msg_snapshot(const char *talker,
                                              RevokeCachedMsgSnapshot *out);
void cache_filehelper_outgoing_copy(int64_t msgid, int32_t local_id,
                                    int32_t msg_type, int32_t msg_subtype,
                                    int32_t createtime, const char *content,
                                    const char *sender, const char *self_id,
                                    const char *msgsource);

#endif /* WX_BRIDGE_REVOKE_CACHE_H */
