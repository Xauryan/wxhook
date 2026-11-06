#ifndef WX_BRIDGE_REALTIME_RELAY_H
#define WX_BRIDGE_REALTIME_RELAY_H

#include "wx_bridge_platform.h"

typedef struct WxbRealtimeRelayStatus {
    LONG64 enqueued;
    LONG64 persisted;
    LONG64 delivered;
    LONG64 failed;
    LONG64 dropped;
    LONG64 retried;
    LONG pending_memory;
    LONG pending_disk;
    int running;
} WxbRealtimeRelayStatus;

int wxb_realtime_relay_start(void);
void wxb_realtime_relay_stop(void);
int wxb_realtime_relay_enqueue(const char *event_id, const char *json, DWORD len);
void wxb_realtime_relay_get_status(WxbRealtimeRelayStatus *status);

#endif /* WX_BRIDGE_REALTIME_RELAY_H */
