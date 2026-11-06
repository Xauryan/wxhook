#ifndef WX_BRIDGE_HONGBAO_RUNTIME_INTERNAL_H
#define WX_BRIDGE_HONGBAO_RUNTIME_INTERNAL_H

#include "../include/wx_bridge_hongbao_runtime.h"

void hb_runtime_record_page_capture(int64_t page_ctx,
                                    int64_t receive_result,
                                    int64_t timing_obj,
                                    int64_t timing_ptr,
                                    LONG timing_len,
                                    LONG timing_cap);
void hb_runtime_set_last_sendid(const char *sendid);
void hb_runtime_copy_last_pagectx_sendid(char *out, int max);

int hb_runtime_try_begin_open(void);
void hb_runtime_record_open_success(void);
void hb_runtime_end_open(void);
void hb_runtime_arm_same_stack_open(void);
int hb_runtime_consume_same_stack_open(void);
int hb_runtime_auto_page_seen(int64_t page_ctx);
void hb_runtime_mark_auto_page(int64_t page_ctx);

void hb_runtime_set_pending_page_ctx(int64_t page_ctx);
int64_t hb_runtime_exchange_pending_page_ctx(int64_t page_ctx);
void hb_runtime_set_pending_tick(int64_t tick);
int64_t hb_runtime_get_pending_tick(void);
UINT_PTR hb_runtime_exchange_pending_timer(UINT_PTR timer_id);
void hb_runtime_set_pending_auto(LONG state);
LONG hb_runtime_get_pending_auto(void);
LONG hb_runtime_compare_exchange_pending_auto(LONG exchange, LONG compare);
void hb_runtime_set_pending_thread_id(LONG thread_id);
LONG hb_runtime_get_pending_thread_id(void);

void hb_runtime_set_verbose_enabled(int enabled);

void hb_runtime_record_receive(const char *sendid,
                               int update_last_sendid,
                               const char *native_url,
                               const char *page_url,
                               const char *peer,
                               const char *sender,
                               int64_t msg_base,
                               int has_msgid,
                               int64_t msgid,
                               int has_local_id,
                               LONG local_id,
                               int reset_msg_ids,
                               int increment_hit);
void hb_runtime_copy_recv_sendid(char *out, int max);

void hb_runtime_set_receive_mid(int64_t mid_ctx, int64_t payload);
void hb_runtime_set_receive_parent_arg(int64_t parent_ctx, int64_t arg);
void hb_runtime_set_receive_route_ctx(int64_t route_ctx);
void hb_runtime_set_route_desc(int64_t route_desc);
void hb_runtime_set_route_ctx(int64_t route_ctx);
void hb_runtime_set_route_mgr_if_present(int64_t route_mgr);
void hb_runtime_set_route_type(LONG route_type);

#endif /* WX_BRIDGE_HONGBAO_RUNTIME_INTERNAL_H */
