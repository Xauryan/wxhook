#ifndef WX_BRIDGE_DEBUG_LOG_H
#define WX_BRIDGE_DEBUG_LOG_H

#include "wx_bridge_platform.h"

void wxb_debug_append_line(const WCHAR *path, const char *line);
void wxb_quote_dbg(const char *line);
void wxb_forward_dbg(const char *line);
void wxb_hb_dbg(const char *line);
void wxb_hb_route_dbg(const char *line);
void wxb_debug_log_shutdown(void);

#define debug_append_line wxb_debug_append_line
#define quote_dbg wxb_quote_dbg
#define forward_dbg wxb_forward_dbg
#define hb_dbg wxb_hb_dbg
#define hb_route_dbg wxb_hb_route_dbg
#define debug_log_shutdown wxb_debug_log_shutdown

#endif /* WX_BRIDGE_DEBUG_LOG_H */
