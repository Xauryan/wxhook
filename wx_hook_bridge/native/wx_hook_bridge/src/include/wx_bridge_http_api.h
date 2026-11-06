#ifndef WX_BRIDGE_HTTP_API_H
#define WX_BRIDGE_HTTP_API_H

#include "wx_bridge_platform.h"
#include "../third_party/mongoose.h"

void wxb_http_event_handler(struct mg_connection *c, int ev, void *ev_data, void *fn_data);

#endif /* WX_BRIDGE_HTTP_API_H */
