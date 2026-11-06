#ifndef WX_BRIDGE_HONGBAO_RUNTIME_H
#define WX_BRIDGE_HONGBAO_RUNTIME_H

#include "wx_bridge_platform.h"

typedef struct HbPageCaptureSnapshot {
    int64_t page_ctx;
    int64_t receive_result;
    int64_t timing_obj;
    int64_t timing_ptr;
    LONG timing_len;
    LONG timing_cap;
    char last_sendid[128];
    char last_pagectx_sendid[128];
    LONG capture_hit;
    LONG open_attempt;
    LONG open_success;
    LONG capture_thread_id;
    ULONGLONG last_capture_tick;
} HbPageCaptureSnapshot;

typedef struct HbReceiveSnapshot {
    char sendid[128];
    char native_url[2048];
    char page_url[2048];
    char peer[256];
    char sender[256];
    int64_t msg_base;
    int64_t msgid;
    LONG local_id;
    int64_t tick;
    LONG hit;
} HbReceiveSnapshot;

typedef struct HbRouteSnapshot {
    int64_t receive_route_ctx;
    int64_t receive_mid_ctx;
    int64_t receive_mid_payload;
    int64_t receive_parent_ctx;
    int64_t receive_arg;
    int64_t route_desc;
    int64_t route_ctx;
    int64_t route_mgr;
    LONG route_type;
} HbRouteSnapshot;

void hb_runtime_get_page_snapshot(HbPageCaptureSnapshot *out);
int64_t hb_runtime_get_last_page_ctx(void);
int64_t hb_runtime_get_last_receive_result(void);
LONG hb_runtime_get_capture_hit(void);
ULONGLONG hb_runtime_get_last_capture_tick(void);
int hb_runtime_verbose_enabled(void);
void hb_runtime_get_receive_snapshot(HbReceiveSnapshot *out);
int64_t hb_runtime_get_receive_mid_ctx(void);
int64_t hb_runtime_get_receive_mid_payload(void);
int64_t hb_runtime_get_receive_parent_ctx(void);
int64_t hb_runtime_get_receive_arg(void);
int64_t hb_runtime_get_route_desc(void);
int64_t hb_runtime_get_route_ctx(void);
int64_t hb_runtime_get_route_mgr(void);
LONG hb_runtime_get_route_type(void);
void hb_runtime_get_route_snapshot(HbRouteSnapshot *out);

#endif /* WX_BRIDGE_HONGBAO_RUNTIME_H */
