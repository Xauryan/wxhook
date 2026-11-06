#ifndef WX_BRIDGE_HONGBAO_ENTRYPOINTS_H
#define WX_BRIDGE_HONGBAO_ENTRYPOINTS_H

#include "wx_bridge_platform.h"

int64_t __fastcall hb_pagectx_hook(int64_t page_ctx);
int build_hb_pagectx_relay(BYTE *p);
int64_t __fastcall hb_setter_hook(int64_t owner, int64_t shared_page_ctx);
int64_t __fastcall hb_task_factory_hook(int64_t a1, int64_t a2,
                                        int64_t a3, int64_t a4);
int64_t __fastcall hb_receive_mid_builder_hook(int64_t a1,
                                               int64_t holder,
                                               int64_t payload);
int64_t __fastcall hb_task_factory_inner_hook(int64_t a1, int64_t a2,
                                             int64_t a3, int64_t a4,
                                             int64_t a5, int64_t a6,
                                             int64_t a7, int64_t a8);
void __fastcall hb_receive_task_builder_hook(int64_t parent_ctx,
                                             int64_t arg);
int64_t __fastcall hb_receive_route_builder_hook(int64_t route_ctx);
int64_t __fastcall hb_parser_hook(int64_t a1, int64_t a2);
int64_t __fastcall hb_route_dispatcher_hook(int64_t a1, int64_t a2);
int64_t __fastcall hb_route_temp_vector_hook(int64_t a1,
                                            int64_t vector_arg,
                                            int64_t a3);
int64_t __fastcall hb_route_vector_builder_hook(int64_t route_mgr,
                                               int64_t vector_arg);
int64_t __fastcall hb_route_ctx_factory_hook(int64_t out_obj,
                                            int64_t route_mgr,
                                            int64_t route_desc,
                                            int64_t flag);
int64_t __fastcall hb_route_classifier_hook(int64_t route_desc);
int64_t __fastcall hb_route_item_dispatch_hook(int64_t a1, int64_t a2);
int64_t __fastcall hb_route_hook(int64_t a1, int64_t a2,
                                int64_t a3, int64_t a4);
int hb_scan_parser_vector(const char *phase,
                          LONG hit,
                          int64_t parser_a1,
                          int64_t vector_arg,
                          int64_t parser_ret);
int64_t __fastcall hb_source_submit_hook(int64_t a1, int64_t vector_arg,
                                        int64_t a3, int64_t a4);

#endif /* WX_BRIDGE_HONGBAO_ENTRYPOINTS_H */
