#ifndef WX_BRIDGE_HONGBAO_NOCLICK_H
#define WX_BRIDGE_HONGBAO_NOCLICK_H

#include "wx_bridge_platform.h"

void poll_hb_noclick_pending(void);
int hb_noclick_scan_source_vector(LONG hit, int64_t a1, int64_t vector_arg);
void hb_noclick_remember_red_send_item(int64_t send_item);

#endif /* WX_BRIDGE_HONGBAO_NOCLICK_H */
