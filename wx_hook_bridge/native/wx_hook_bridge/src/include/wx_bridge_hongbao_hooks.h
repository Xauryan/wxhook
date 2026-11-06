#ifndef WX_BRIDGE_HONGBAO_HOOKS_H
#define WX_BRIDGE_HONGBAO_HOOKS_H

#include "wx_bridge_platform.h"

typedef enum HbHookKind {
    HB_HOOK_PAGECTX = 0,
    HB_HOOK_SETTER,
    HB_HOOK_NULL_GUARD,
    HB_HOOK_ROUTE,
    HB_HOOK_TASK_FACTORY,
    HB_HOOK_RECEIVE_ROUTE_BUILDER,
    HB_HOOK_TASK_FACTORY_INNER,
    HB_HOOK_RECEIVE_MID_BUILDER,
    HB_HOOK_RECEIVE_TASK_BUILDER,
    HB_HOOK_SOURCE_SUBMIT,
    HB_HOOK_ROUTE_DISPATCHER,
    HB_HOOK_ROUTE_TEMP_VECTOR,
    HB_HOOK_ROUTE_VECTOR_BUILDER,
    HB_HOOK_ROUTE_CTX_FACTORY,
    HB_HOOK_ROUTE_CLASSIFIER,
    HB_HOOK_ROUTE_ITEM_DISPATCH,
    HB_HOOK_COUNT
} HbHookKind;

void install_hb_pagectx_hook(void);
void uninstall_hb_pagectx_hook(void);
void install_hb_setter_hook(void);
void uninstall_hb_setter_hook(void);
void install_hb_null_guard_hook(void);
void uninstall_hb_null_guard_hook(void);
void install_hb_route_hook(void);
void uninstall_hb_route_hook(void);
void install_hb_task_factory_hook(void);
void uninstall_hb_task_factory_hook(void);
void install_hb_receive_route_builder_hook(void);
void uninstall_hb_receive_route_builder_hook(void);
void install_hb_task_factory_inner_hook(void);
void uninstall_hb_task_factory_inner_hook(void);
void install_hb_receive_mid_builder_hook(void);
void uninstall_hb_receive_mid_builder_hook(void);
void install_hb_receive_task_builder_hook(void);
void uninstall_hb_receive_task_builder_hook(void);
void install_hb_parser_hook(void);
void uninstall_hb_parser_hook(void);
void install_hb_source_submit_hook(void);
void uninstall_hb_source_submit_hook(void);
void install_hb_route_dispatcher_hook(void);
void uninstall_hb_route_dispatcher_hook(void);
void install_hb_route_temp_vector_hook(void);
void uninstall_hb_route_temp_vector_hook(void);
void install_hb_route_vector_builder_hook(void);
void uninstall_hb_route_vector_builder_hook(void);
void install_hb_route_ctx_factory_hook(void);
void uninstall_hb_route_ctx_factory_hook(void);
void install_hb_route_classifier_hook(void);
void uninstall_hb_route_classifier_hook(void);
void install_hb_route_item_dispatch_hook(void);
void uninstall_hb_route_item_dispatch_hook(void);
void install_hb_sentinel_hooks(void);
void uninstall_hb_sentinel_hooks(void);
void poll_hb_control(void);
void poll_hb_deferred_auto(void);
void CALLBACK hb_deferred_timer_proc(HWND hwnd, UINT msg,
                                     UINT_PTR id, DWORD time_ms);
int hb_hook_installed(HbHookKind kind);
LONG hb_hook_record_hit(HbHookKind kind);
int64_t hb_call_setter_original(int64_t owner, int64_t shared_page_ctx);
int64_t hb_call_route_original(int64_t a1, int64_t a2, int64_t a3, int64_t a4);
int64_t hb_call_task_factory_original(int64_t a1, int64_t a2, int64_t a3,
                                      int64_t a4, int64_t a5, int64_t a6);
int64_t hb_call_receive_mid_builder_original(int64_t a1, int64_t holder,
                                             int64_t payload);
int64_t hb_call_task_factory_inner_original(int64_t a1, int64_t a2, int64_t a3,
                                            int64_t a4, int64_t a5, int64_t a6,
                                            int64_t a7, int64_t a8);
void hb_call_receive_task_builder_original(int64_t parent_ctx, int64_t arg);
int64_t hb_call_receive_route_builder_original(int64_t route_ctx);
int64_t hb_call_source_submit_original(int64_t a1, int64_t vector_arg,
                                       int64_t a3, int64_t a4);
int64_t hb_call_route_dispatcher_original(int64_t a1, int64_t a2);
int64_t hb_call_route_temp_vector_original(int64_t a1, int64_t vector_arg,
                                           int64_t a3);
int64_t hb_call_route_vector_builder_original(int64_t route_mgr,
                                              int64_t vector_arg);
int64_t hb_call_route_ctx_factory_original(int64_t out_obj, int64_t route_mgr,
                                           int64_t route_desc, int64_t flag);
int64_t hb_call_route_classifier_original(int64_t route_desc);
int64_t hb_call_route_item_dispatch_original(int64_t a1, int64_t a2);

#endif /* WX_BRIDGE_HONGBAO_HOOKS_H */
