#ifndef WX_BRIDGE_SEND_API_H
#define WX_BRIDGE_SEND_API_H

#include "wx_bridge_platform.h"

int send_quote_v2(const char *to_wxid, const char *reply_text,
                  int64_t quoted_svrid, int32_t quoted_local_id,
                  const char *quoted_content, const char *quoted_from,
                  int quoted_type, int32_t createtime,
                  const char *quoted_chat, const char *quoted_msgsource,
                  const char *source_to_wxid);
int send_image_quote(const char *to_wxid, const char *image_path,
                     int64_t quoted_svrid, int32_t quoted_local_id,
                     const char *quoted_content, const char *quoted_from,
                     int quoted_type, int32_t createtime,
                     const char *quoted_chat, const char *quoted_msgsource,
                     const char *source_to_wxid);
int send_text(const char *wxid, const char *content);
int send_at_text(const char *room_id, const char *wxids,
                 const char *nickname, const char *content);
int send_image(const char *wxid, const char *path);
int send_card(const char *to_wxid, const char *title, const char *des,
              int appmsg_subtype, const char *action,
              const char *url, const char *dataurl,
              const char *songalbumurl, const char *thumburl,
              const char *appid, const char *sourceusername,
              const char *sourcedisplayname);
int send_appmsg_xml(const char *to_wxid, const char *xml, int appmsg_subtype,
                    const char *source_from_wxid,
                    const char *source_to_wxid,
                    const char *source_sender_wxid);

#endif /* WX_BRIDGE_SEND_API_H */
