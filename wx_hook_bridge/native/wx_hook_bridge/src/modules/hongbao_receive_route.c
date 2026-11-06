/*
 * Module: hongbao receive route tracing and route descriptor hooks
 */

#include "../include/wx_bridge_debug_log.h"
#include "../include/wx_bridge_hongbao_closed_loop.h"
#include "../include/wx_bridge_hongbao_cold.h"
#include "../include/wx_bridge_hongbao_entrypoints.h"
#include "../include/wx_bridge_hongbao_hooks.h"
#include "../include/wx_bridge_hongbao_runtime.h"
#include "wx_bridge_hongbao_runtime_internal.h"
#include "../include/wx_bridge_hongbao_trace.h"
#include "../include/wx_bridge_memory.h"
#include "../include/wx_bridge_text.h"
static void hb_log_task_factory_args(const char *name,
                                     LONG hit,
                                     int64_t retaddr,
                                     int64_t a1,
                                     int64_t a2,
                                     int64_t a3,
                                     int64_t a4,
                                     int64_t a5,
                                     int64_t a6) {
    char line[8192];
    char *p = line;
    char *end = line + sizeof(line) - 1;
    char num[64];
    char s2[512] = {0}, s3[256] = {0}, s4[256] = {0}, s5[256] = {0}, s6[256] = {0};
    int64_t a3_slot = a3 ? r64((void *)(a3 + 0x38)) : 0;
    int64_t a4_slot = a4 ? r64((void *)(a4 + 0x38)) : 0;
    int64_t a5_slot = a5 ? r64((void *)(a5 + 0x38)) : 0;
    int64_t a3_obj = a3 ? r64((void *)a3) : 0;
    int64_t a3_ctl = a3 ? r64((void *)(a3 + 8)) : 0;
    int64_t a4_obj = a4 ? r64((void *)a4) : 0;
    int64_t a4_ctl = a4 ? r64((void *)(a4 + 8)) : 0;
    int64_t a5_obj = a5 ? r64((void *)a5) : 0;
    int64_t a5_ctl = a5 ? r64((void *)(a5 + 8)) : 0;
    HbReceiveSnapshot recv;
    int64_t recv_tick = 0;
    ULONGLONG now_tick = GetTickCount64();
    hb_runtime_get_receive_snapshot(&recv);
    recv_tick = recv.tick;

    read_probe_string(a2, s2, sizeof(s2));
    read_probe_string(a3, s3, sizeof(s3));
    read_probe_string(a4, s4, sizeof(s4));
    read_probe_string(a5, s5, sizeof(s5));
    read_probe_string(a6, s6, sizeof(s6));

    append_raw(&p, end, "{\"event\":\"hb_task_factory\"");
    append_raw(&p, end, ",\"name\":\""); append_escaped(&p, end, name ? name : "");
    append_raw(&p, end, "\",\"hit\":"); wsprintfA(num, "%ld", (long)hit); append_raw(&p, end, num);
    append_raw(&p, end, ",\"tid\":"); wsprintfA(num, "%lu", (unsigned long)GetCurrentThreadId()); append_raw(&p, end, num);
    append_hex_field(&p, end, "ret", retaddr);
    append_hex_field(&p, end, "a1", a1);
    append_hex_field(&p, end, "a2", a2);
    append_hex_field(&p, end, "a3", a3);
    append_hex_field(&p, end, "a4", a4);
    append_hex_field(&p, end, "a5", a5);
    append_hex_field(&p, end, "a6", a6);
    append_hex_field(&p, end, "a3_obj", a3_obj);
    append_hex_field(&p, end, "a3_ctl", a3_ctl);
    append_hex_field(&p, end, "a3_slot38", a3_slot);
    append_hex_field(&p, end, "a4_obj", a4_obj);
    append_hex_field(&p, end, "a4_ctl", a4_ctl);
    append_hex_field(&p, end, "a4_slot38", a4_slot);
    append_hex_field(&p, end, "a5_obj", a5_obj);
    append_hex_field(&p, end, "a5_ctl", a5_ctl);
    append_hex_field(&p, end, "a5_slot38", a5_slot);
    append_hex_field(&p, end, "last_page_ctx", (int64_t)hb_runtime_get_last_page_ctx());
    append_hex_field(&p, end, "recv_msg_base", recv.msg_base);
    append_dec_field(&p, end, "recv_msgId", recv.msgid);
    append_dec_field(&p, end, "recv_localId", recv.local_id);
    append_dec_field(&p, end, "recv_age_ms", recv_tick ? (int64_t)(now_tick - (ULONGLONG)recv_tick) : -1);
    append_raw(&p, end, ",\"recv_sendId\":\""); append_escaped(&p, end, recv.sendid);
    append_raw(&p, end, "\",\"recv_nativeUrl\":\""); append_escaped(&p, end, recv.native_url);
    append_raw(&p, end, "\",\"recv_pageUrl\":\""); append_escaped(&p, end, recv.page_url);
    append_raw(&p, end, "\",\"recv_peer\":\""); append_escaped(&p, end, recv.peer);
    append_raw(&p, end, "\",\"recv_sender\":\""); append_escaped(&p, end, recv.sender);
    append_raw(&p, end, "\",\"s_a2\":\""); append_escaped(&p, end, s2);
    append_raw(&p, end, "\",\"s_a3\":\""); append_escaped(&p, end, s3);
    append_raw(&p, end, "\",\"s_a4\":\""); append_escaped(&p, end, s4);
    append_raw(&p, end, "\",\"s_a5\":\""); append_escaped(&p, end, s5);
    append_raw(&p, end, "\",\"s_a6\":\""); append_escaped(&p, end, s6);
    append_raw(&p, end, "\"}");
    *p = 0;
    hb_route_dbg(line);
}



int64_t __fastcall hb_task_factory_hook(int64_t a1, int64_t a2, int64_t a3, int64_t a4) {
    int64_t *rsp = (int64_t *)_AddressOfReturnAddress();
    int64_t retaddr = rsp ? r64((void *)rsp) : (int64_t)_ReturnAddress();
    int64_t a5 = rsp ? r64((void *)(rsp + 5)) : 0;
    int64_t a6 = rsp ? r64((void *)(rsp + 6)) : 0;
    LONG hit = hb_hook_record_hit(HB_HOOK_TASK_FACTORY);
    hb_log_task_factory_args("task_factory_183A68BF0", hit, retaddr, a1, a2, a3, a4, a5, a6);
    return hb_call_task_factory_original(a1, a2, a3, a4, a5, a6);
}

static void hb_log_receive_mid_builder(const char *phase, LONG hit, int64_t retaddr,
                                       int64_t a1, int64_t holder, int64_t payload) {
    char line[16384];
    char *p = line;
    char *end = line + sizeof(line) - 1;
    char num[64];
    int64_t mid_ctx = holder ? r64((void *)(holder + 0x38)) : 0;
    int64_t mid_parent = mid_ctx ? r64((void *)(mid_ctx + 0x390)) : 0;

    append_raw(&p, end, "{\"event\":\"hb_receive_mid_builder\"");
    append_raw(&p, end, ",\"phase\":\""); append_escaped(&p, end, phase ? phase : "");
    append_raw(&p, end, "\",\"hit\":"); wsprintfA(num, "%ld", (long)hit); append_raw(&p, end, num);
    append_raw(&p, end, ",\"tid\":"); wsprintfA(num, "%lu", (unsigned long)GetCurrentThreadId()); append_raw(&p, end, num);
    append_hex_field(&p, end, "ret", retaddr);
    append_hex_field(&p, end, "a1_aux", a1);
    append_hex_field(&p, end, "holder", holder);
    append_hex_field(&p, end, "payload", payload);
    append_hex_field(&p, end, "holder_38", mid_ctx);
    append_hex_field(&p, end, "mid_ctx_390", mid_parent);
    hb_append_recv_context(&p, end);
    hb_append_obj_probe(&p, end, "payload", payload);
    hb_append_obj_probe(&p, end, "mid_ctx", mid_ctx);
    hb_append_obj_string_hits(&p, end, "payload_string_hits", payload);
    hb_append_obj_string_hits(&p, end, "mid_ctx_string_hits", mid_ctx);
    append_raw(&p, end, "}");
    *p = 0;
    hb_route_dbg(line);
}

int64_t __fastcall hb_receive_mid_builder_hook(int64_t a1, int64_t holder, int64_t payload) {
    int64_t ret = 0;
    /* Cold-only: passive cgiTable cache, no route probe JSON. */
    if (a1) hb_cold_note_factory_cgi(a1);
    (void)hb_hook_record_hit(HB_HOOK_RECEIVE_MID_BUILDER);
    ret = hb_call_receive_mid_builder_original(a1, holder, payload);
    hb_runtime_set_receive_mid(holder ? r64((void *)(holder + 0x38)) : 0, payload);
    return ret;
}

static void hb_log_task_factory_inner(const char *phase, LONG hit, int64_t retaddr,
                                      int64_t a1, int64_t a2, int64_t a3, int64_t a4,
                                      int64_t a5, int64_t a6, int64_t a7, int64_t a8) {
    char line[16384];
    char *p = line;
    char *end = line + sizeof(line) - 1;
    char num[64];
    int64_t parent_candidate = a1 ? r64((void *)(a1 + 0x38)) : 0;
    int64_t a1_shared_obj = a1 ? r64((void *)(a1 + 0x40)) : 0;
    int64_t a1_shared_ctl = a1 ? r64((void *)(a1 + 0x48)) : 0;
    int64_t h3 = a3 ? r64((void *)(a3 + 0x38)) : 0;
    int64_t h4 = a4 ? r64((void *)(a4 + 0x38)) : 0;
    int64_t h5 = a5 ? r64((void *)(a5 + 0x38)) : 0;

    append_raw(&p, end, "{\"event\":\"hb_task_factory_inner\"");
    append_raw(&p, end, ",\"phase\":\""); append_escaped(&p, end, phase ? phase : "");
    append_raw(&p, end, "\",\"hit\":"); wsprintfA(num, "%ld", (long)hit); append_raw(&p, end, num);
    append_raw(&p, end, ",\"tid\":"); wsprintfA(num, "%lu", (unsigned long)GetCurrentThreadId()); append_raw(&p, end, num);
    append_hex_field(&p, end, "ret", retaddr);
    append_hex_field(&p, end, "a1", a1);
    append_hex_field(&p, end, "a2_out", a2);
    append_hex_field(&p, end, "a3_holder", a3);
    append_hex_field(&p, end, "a4_holder", a4);
    append_hex_field(&p, end, "a5_holder", a5);
    append_hex_field(&p, end, "a6_result", a6);
    append_hex_field(&p, end, "a7_payload_or_tag", a7);
    append_hex_field(&p, end, "a8_task_id", a8);
    append_hex_field(&p, end, "a1_parent_candidate_38", parent_candidate);
    append_hex_field(&p, end, "a1_shared_obj_40", a1_shared_obj);
    append_hex_field(&p, end, "a1_shared_ctl_48", a1_shared_ctl);
    append_hex_field(&p, end, "a3_slot38", h3);
    append_hex_field(&p, end, "a4_slot38", h4);
    append_hex_field(&p, end, "a5_slot38", h5);
    append_hex_field(&p, end, "out_task_id", a2 ? r64((void *)a2) : 0);
    append_hex_field(&p, end, "out_shared_obj", a2 ? r64((void *)(a2 + 8)) : 0);
    append_hex_field(&p, end, "out_shared_ctl", a2 ? r64((void *)(a2 + 0x10)) : 0);
    append_hex_field(&p, end, "last_mid_ctx", (int64_t)hb_runtime_get_receive_mid_ctx());
    append_hex_field(&p, end, "last_parent_ctx", (int64_t)hb_runtime_get_receive_parent_ctx());
    append_hex_field(&p, end, "last_arg", (int64_t)hb_runtime_get_receive_arg());
    hb_append_recv_context(&p, end);
    hb_append_obj_probe(&p, end, "parent", parent_candidate);
    hb_append_obj_probe(&p, end, "h3", h3);
    hb_append_obj_probe(&p, end, "h4", h4);
    hb_append_obj_probe(&p, end, "h5", h5);
    hb_append_obj_string_hits(&p, end, "parent_string_hits", parent_candidate);
    hb_append_obj_string_hits(&p, end, "h3_string_hits", h3);
    hb_append_obj_string_hits(&p, end, "h4_string_hits", h4);
    hb_append_obj_string_hits(&p, end, "h5_string_hits", h5);
    append_raw(&p, end, "}");
    *p = 0;
    hb_route_dbg(line);
}

int64_t __fastcall hb_task_factory_inner_hook(int64_t a1, int64_t a2, int64_t a3, int64_t a4,
                                                     int64_t a5, int64_t a6, int64_t a7, int64_t a8) {
    LONG hit = hb_hook_record_hit(HB_HOOK_TASK_FACTORY_INNER);
    int64_t retaddr = (int64_t)_ReturnAddress();
    int64_t ret = 0;
    hb_log_task_factory_inner("before", hit, retaddr, a1, a2, a3, a4, a5, a6, a7, a8);
    ret = hb_call_task_factory_inner_original(a1, a2, a3, a4, a5, a6, a7, a8);
    hb_log_task_factory_inner("after", hit, retaddr, a1, a2, a3, a4, a5, a6, a7, a8);
    return ret;
}

static void hb_log_receive_task_builder(const char *phase, LONG hit, int64_t retaddr,
                                        int64_t parent_ctx, int64_t arg) {
    char line[16384];
    char *p = line;
    char *end = line + sizeof(line) - 1;
    char num[64];
    int64_t parent_aux_390 = parent_ctx ? r64((void *)(parent_ctx + 0x390)) : 0;

    append_raw(&p, end, "{\"event\":\"hb_receive_task_builder\"");
    append_raw(&p, end, ",\"phase\":\""); append_escaped(&p, end, phase ? phase : "");
    append_raw(&p, end, "\",\"hit\":"); wsprintfA(num, "%ld", (long)hit); append_raw(&p, end, num);
    append_raw(&p, end, ",\"tid\":"); wsprintfA(num, "%lu", (unsigned long)GetCurrentThreadId()); append_raw(&p, end, num);
    append_hex_field(&p, end, "ret", retaddr);
    append_hex_field(&p, end, "parent_ctx", parent_ctx);
    append_hex_field(&p, end, "arg", arg);
    append_hex_field(&p, end, "parent_ctx_390", parent_aux_390);
    append_hex_field(&p, end, "last_mid_ctx", (int64_t)hb_runtime_get_receive_mid_ctx());
    append_hex_field(&p, end, "last_mid_payload", (int64_t)hb_runtime_get_receive_mid_payload());
    hb_append_recv_context(&p, end);
    hb_append_obj_probe(&p, end, "parent", parent_ctx);
    hb_append_obj_probe(&p, end, "arg", arg);
    hb_append_obj_string_hits(&p, end, "parent_string_hits", parent_ctx);
    hb_append_obj_string_hits(&p, end, "arg_string_hits", arg);
    append_raw(&p, end, "}");
    *p = 0;
    hb_route_dbg(line);
}

void __fastcall hb_receive_task_builder_hook(int64_t parent_ctx, int64_t arg) {
    LONG hit = hb_hook_record_hit(HB_HOOK_RECEIVE_TASK_BUILDER);
    int64_t retaddr = (int64_t)_ReturnAddress();
    hb_runtime_set_receive_parent_arg(parent_ctx, arg);
    wxb_hb_closed_loop_note_receive_task(parent_ctx, arg);
    hb_log_receive_task_builder("before", hit, retaddr, parent_ctx, arg);
    hb_call_receive_task_builder_original(parent_ctx, arg);
    hb_log_receive_task_builder("after", hit, retaddr, parent_ctx, arg);
}

static void hb_log_receive_route_builder(const char *phase, LONG hit, int64_t retaddr,
                                         int64_t route_ctx) {
    char line[16384];
    char *p = line;
    char *end = line + sizeof(line) - 1;
    char num[64];
    int64_t payload = route_ctx ? route_ctx + 0x120 : 0;

    append_raw(&p, end, "{\"event\":\"hb_receive_route_builder\"");
    append_raw(&p, end, ",\"phase\":\""); append_escaped(&p, end, phase ? phase : "");
    append_raw(&p, end, "\",\"hit\":"); wsprintfA(num, "%ld", (long)hit); append_raw(&p, end, num);
    append_raw(&p, end, ",\"tid\":"); wsprintfA(num, "%lu", (unsigned long)GetCurrentThreadId()); append_raw(&p, end, num);
    append_hex_field(&p, end, "ret", retaddr);
    append_hex_field(&p, end, "route_ctx", route_ctx);
    append_hex_field(&p, end, "payload", payload);
    append_hex_field(&p, end, "route_944", route_ctx ? r64((void *)(route_ctx + 944)) : 0);
    append_hex_field(&p, end, "route_960", route_ctx ? r64((void *)(route_ctx + 960)) : 0);
    append_hex_field(&p, end, "route_976", route_ctx ? r64((void *)(route_ctx + 976)) : 0);
    append_hex_field(&p, end, "route_984", route_ctx ? r64((void *)(route_ctx + 984)) : 0);
    append_hex_field(&p, end, "route_992", route_ctx ? r64((void *)(route_ctx + 992)) : 0);
    append_hex_field(&p, end, "route_payload_obj_390", route_ctx ? r64((void *)(route_ctx + 0x390)) : 0);
    append_hex_field(&p, end, "route_payload_ctl_398", route_ctx ? r64((void *)(route_ctx + 0x398)) : 0);
    append_hex_field(&p, end, "route_payload_struct_500", route_ctx ? r64((void *)(route_ctx + 0x500)) : 0);
    append_hex_field(&p, end, "route_payload_struct_ctl_508", route_ctx ? r64((void *)(route_ctx + 0x508)) : 0);
    append_dec_field(&p, end, "route_payload_has_text_510", route_ctx ? (int64_t)r32((void *)(route_ctx + 0x510)) : 0);
    append_hex_field(&p, end, "route_1192", route_ctx ? r64((void *)(route_ctx + 1192)) : 0);
    append_hex_field(&p, end, "route_1256", route_ctx ? r64((void *)(route_ctx + 1256)) : 0);
    append_hex_field(&p, end, "route_1320", route_ctx ? r64((void *)(route_ctx + 1320)) : 0);
    append_hex_field(&p, end, "last_mid_ctx", (int64_t)hb_runtime_get_receive_mid_ctx());
    append_hex_field(&p, end, "last_parent_ctx", (int64_t)hb_runtime_get_receive_parent_ctx());
    append_hex_field(&p, end, "last_arg", (int64_t)hb_runtime_get_receive_arg());
    hb_append_recv_context(&p, end);
    hb_append_obj_probe(&p, end, "payload", payload);
    hb_append_obj_string_hits(&p, end, "payload_string_hits", payload);
    append_raw(&p, end, "}");
    *p = 0;
    hb_route_dbg(line);
}

int64_t __fastcall hb_receive_route_builder_hook(int64_t route_ctx) {
    LONG hit = hb_hook_record_hit(HB_HOOK_RECEIVE_ROUTE_BUILDER);
    int64_t retaddr = (int64_t)_ReturnAddress();
    int64_t ret = 0;
    hb_runtime_set_receive_route_ctx(route_ctx);
    hb_log_receive_route_builder("before", hit, retaddr, route_ctx);
    ret = hb_call_receive_route_builder_original(route_ctx);
    hb_log_receive_route_builder("after", hit, retaddr, route_ctx);
    return ret;
}

#define HB_ROUTE_VECTOR_LOG_MAX 32

static int hb_route_vector_bounds(int64_t vector_arg,
                                  int64_t *begin_out,
                                  int64_t *end_out,
                                  int64_t *delta_out,
                                  int *count_out) {
    int64_t begin = 0;
    int64_t endp = 0;
    int64_t delta = 0;
    int count = 0;
    if (!vector_arg || !is_readable_ptr(vector_arg, 0x18)) return 0;
    begin = r64((void *)vector_arg);
    endp = r64((void *)(vector_arg + 8));
    if (!begin || !endp || endp < begin) return 0;
    delta = endp - begin;
    if (delta < 0 || delta > (int64_t)HB_ROUTE_DESC_SIZE * 512) return 0;
    count = (int)(delta / HB_ROUTE_DESC_SIZE);
    if (begin_out) *begin_out = begin;
    if (end_out) *end_out = endp;
    if (delta_out) *delta_out = delta;
    if (count_out) *count_out = count;
    return 1;
}

static void hb_log_route_dispatcher_probe(const char *phase, LONG hit, int64_t retaddr,
                                          int64_t a1, int64_t a2, int64_t ret_value) {
    char line[8192];
    char *p = line;
    char *end = line + sizeof(line) - 1;
    char num[64];
    char s_a1[256] = {0}, s_a2[256] = {0}, s_owner[256] = {0};
    int64_t owner = a2 ? r64((void *)a2) : 0;
    read_probe_string(a1, s_a1, sizeof(s_a1));
    read_probe_string(a2, s_a2, sizeof(s_a2));
    read_probe_string(owner, s_owner, sizeof(s_owner));

    append_raw(&p, end, "{\"event\":\"hb_route_dispatcher\"");
    append_raw(&p, end, ",\"phase\":\""); append_escaped(&p, end, phase ? phase : "");
    append_raw(&p, end, "\",\"hit\":"); wsprintfA(num, "%ld", (long)hit); append_raw(&p, end, num);
    append_raw(&p, end, ",\"tid\":"); wsprintfA(num, "%lu", (unsigned long)GetCurrentThreadId()); append_raw(&p, end, num);
    append_hex_field(&p, end, "ret", retaddr);
    append_hex_field(&p, end, "a1", a1);
    append_hex_field(&p, end, "a2", a2);
    append_hex_field(&p, end, "a2_owner", owner);
    append_hex_field(&p, end, "a2_ctl", a2 ? r64((void *)(a2 + 8)) : 0);
    append_hex_field(&p, end, "ret_value", ret_value);
    hb_append_recv_context(&p, end);
    hb_append_str_field(&p, end, "s_a1", s_a1);
    hb_append_str_field(&p, end, "s_a2", s_a2);
    hb_append_str_field(&p, end, "s_owner", s_owner);
    append_raw(&p, end, "}");
    *p = 0;
    hb_route_dbg(line);
}

int64_t __fastcall hb_route_dispatcher_hook(int64_t a1, int64_t a2) {
    LONG hit = hb_hook_record_hit(HB_HOOK_ROUTE_DISPATCHER);
    int64_t retaddr = (int64_t)_ReturnAddress();
    int64_t ret = 0;
    hb_log_route_dispatcher_probe("before", hit, retaddr, a1, a2, 0);
    ret = hb_call_route_dispatcher_original(a1, a2);
    hb_log_route_dispatcher_probe("after", hit, retaddr, a1, a2, ret);
    return ret;
}

static void hb_log_route_vector_probe(const char *event, const char *phase, LONG hit,
                                      int64_t retaddr, int64_t a1, int64_t vector_arg,
                                      int64_t a3, int64_t ret_value) {
    char line[32768];
    char *p = line;
    char *end = line + sizeof(line) - 1;
    char num[64];
    int64_t begin = 0;
    int64_t endp = 0;
    int64_t delta = 0;
    int count = 0;
    int ok = hb_route_vector_bounds(vector_arg, &begin, &endp, &delta, &count);
    int limit = ok && count < HB_ROUTE_VECTOR_LOG_MAX ? count : HB_ROUTE_VECTOR_LOG_MAX;

    append_raw(&p, end, "{\"event\":\""); append_escaped(&p, end, event ? event : "hb_route_vector");
    append_raw(&p, end, "\",\"phase\":\""); append_escaped(&p, end, phase ? phase : "");
    append_raw(&p, end, "\",\"hit\":"); wsprintfA(num, "%ld", (long)hit); append_raw(&p, end, num);
    append_raw(&p, end, ",\"tid\":"); wsprintfA(num, "%lu", (unsigned long)GetCurrentThreadId()); append_raw(&p, end, num);
    append_hex_field(&p, end, "ret", retaddr);
    append_hex_field(&p, end, "a1", a1);
    append_hex_field(&p, end, "vector", vector_arg);
    append_hex_field(&p, end, "a3", a3);
    append_hex_field(&p, end, "ret_value", ret_value);
    append_hex_field(&p, end, "begin", begin);
    append_hex_field(&p, end, "end", endp);
    append_dec_field(&p, end, "delta", delta);
    append_dec_field(&p, end, "count", count);
    append_dec_field(&p, end, "last_route_type", (int64_t)hb_runtime_get_route_type());
    append_hex_field(&p, end, "last_route_desc", (int64_t)hb_runtime_get_route_desc());
    append_hex_field(&p, end, "last_route_mgr", (int64_t)hb_runtime_get_route_mgr());
    hb_append_recv_context(&p, end);
    append_raw(&p, end, ",\"items\":[");
    if (ok && begin && count > 0) {
        for (int i = 0; i < limit; i++) {
            int64_t desc = begin + (int64_t)i * HB_ROUTE_DESC_SIZE;
            if (i) append_raw(&p, end, ",");
            append_raw(&p, end, "{\"idx\":"); wsprintfA(num, "%d", i); append_raw(&p, end, num);
            hb_append_route_desc_fields(&p, end, "desc", desc, 0);
            append_raw(&p, end, "}");
        }
    }
    append_raw(&p, end, "]}");
    *p = 0;
    hb_route_dbg(line);
}

int64_t __fastcall hb_route_temp_vector_hook(int64_t a1, int64_t vector_arg, int64_t a3) {
    LONG hit = hb_hook_record_hit(HB_HOOK_ROUTE_TEMP_VECTOR);
    int64_t retaddr = (int64_t)_ReturnAddress();
    int64_t ret = 0;
    hb_log_route_vector_probe("hb_route_temp_vector", "before", hit, retaddr, a1, vector_arg, a3, 0);
    ret = hb_call_route_temp_vector_original(a1, vector_arg, a3);
    hb_log_route_vector_probe("hb_route_temp_vector", "after", hit, retaddr, a1, vector_arg, a3, ret);
    return ret;
}

int64_t __fastcall hb_route_vector_builder_hook(int64_t route_mgr, int64_t vector_arg) {
    LONG hit = hb_hook_record_hit(HB_HOOK_ROUTE_VECTOR_BUILDER);
    int64_t retaddr = (int64_t)_ReturnAddress();
    int64_t ret = 0;
    hb_runtime_set_route_mgr_if_present(route_mgr);
    hb_log_route_vector_probe("hb_route_vector_builder", "before", hit, retaddr, route_mgr, vector_arg, 0, 0);
    ret = hb_call_route_vector_builder_original(route_mgr, vector_arg);
    hb_log_route_vector_probe("hb_route_vector_builder", "after", hit, retaddr, route_mgr, vector_arg, 0, ret);
    return ret;
}

static void hb_log_route_ctx_factory_probe(const char *phase, LONG hit, int64_t retaddr,
                                           int64_t out_obj, int64_t route_mgr,
                                           int64_t route_desc, int64_t flag, int64_t ret_value) {
    char line[32768];
    char *p = line;
    char *end = line + sizeof(line) - 1;
    char num[64];
    int64_t built_obj = out_obj ? r64((void *)out_obj) : 0;
    int64_t built_ctl = out_obj ? r64((void *)(out_obj + 8)) : 0;

    append_raw(&p, end, "{\"event\":\"hb_route_ctx_factory\"");
    append_raw(&p, end, ",\"phase\":\""); append_escaped(&p, end, phase ? phase : "");
    append_raw(&p, end, "\",\"hit\":"); wsprintfA(num, "%ld", (long)hit); append_raw(&p, end, num);
    append_raw(&p, end, ",\"tid\":"); wsprintfA(num, "%lu", (unsigned long)GetCurrentThreadId()); append_raw(&p, end, num);
    append_hex_field(&p, end, "ret", retaddr);
    append_hex_field(&p, end, "out", out_obj);
    append_hex_field(&p, end, "route_mgr", route_mgr);
    append_hex_field(&p, end, "route_desc", route_desc);
    append_dec_field(&p, end, "flag", flag);
    append_hex_field(&p, end, "ret_value", ret_value);
    append_hex_field(&p, end, "built_obj", built_obj);
    append_hex_field(&p, end, "built_ctl", built_ctl);
    append_dec_field(&p, end, "last_route_type", (int64_t)hb_runtime_get_route_type());
    append_hex_field(&p, end, "last_route_ctx", (int64_t)hb_runtime_get_route_ctx());
    append_hex_field(&p, end, "last_route_mgr", (int64_t)hb_runtime_get_route_mgr());
    hb_append_recv_context(&p, end);
    hb_append_route_desc_fields(&p, end, "desc", route_desc, 1);
    hb_append_obj_probe(&p, end, "built", built_obj);
    hb_append_obj_string_hits(&p, end, "built_string_hits", built_obj);
    append_raw(&p, end, "}");
    *p = 0;
    hb_route_dbg(line);
}

int64_t __fastcall hb_route_ctx_factory_hook(int64_t out_obj, int64_t route_mgr, int64_t route_desc, int64_t flag) {
    LONG hit = hb_hook_record_hit(HB_HOOK_ROUTE_CTX_FACTORY);
    int64_t retaddr = (int64_t)_ReturnAddress();
    int64_t ret = 0;
    hb_runtime_set_route_desc(route_desc);
    hb_runtime_set_route_mgr_if_present(route_mgr);
    hb_log_route_ctx_factory_probe("before", hit, retaddr, out_obj, route_mgr, route_desc, flag, 0);
    ret = hb_call_route_ctx_factory_original(out_obj, route_mgr, route_desc, flag);
    hb_runtime_set_route_ctx(out_obj ? r64((void *)out_obj) : 0);
    hb_log_route_ctx_factory_probe("after", hit, retaddr, out_obj, route_mgr, route_desc, flag, ret);
    return ret;
}

static void hb_log_route_item_dispatch_probe(const char *phase, LONG hit, int64_t retaddr,
                                             int64_t a1, int64_t a2, int64_t ret_value) {
    char line[16384];
    char *p = line;
    char *end = line + sizeof(line) - 1;
    char num[64];
    int64_t owner = a1 ? a1 - 392 : 0;
    int64_t event_40 = a2 ? r64((void *)(a2 + 40)) : 0;

    append_raw(&p, end, "{\"event\":\"hb_route_item_dispatch\"");
    append_raw(&p, end, ",\"phase\":\""); append_escaped(&p, end, phase ? phase : "");
    append_raw(&p, end, "\",\"hit\":"); wsprintfA(num, "%ld", (long)hit); append_raw(&p, end, num);
    append_raw(&p, end, ",\"tid\":"); wsprintfA(num, "%lu", (unsigned long)GetCurrentThreadId()); append_raw(&p, end, num);
    append_hex_field(&p, end, "ret", retaddr);
    append_hex_field(&p, end, "a1", a1);
    append_hex_field(&p, end, "a2", a2);
    append_hex_field(&p, end, "ret_value", ret_value);
    append_hex_field(&p, end, "owner_a1_minus_392", owner);
    append_hex_field(&p, end, "a1_plus_600", a1 ? r64((void *)(a1 + 600)) : 0);
    append_hex_field(&p, end, "a1_plus_608", a1 ? r64((void *)(a1 + 608)) : 0);
    append_hex_field(&p, end, "event_plus_40", event_40);
    append_dec_field(&p, end, "last_route_type", (int64_t)hb_runtime_get_route_type());
    append_hex_field(&p, end, "last_route_ctx", (int64_t)hb_runtime_get_route_ctx());
    append_hex_field(&p, end, "last_route_mgr", (int64_t)hb_runtime_get_route_mgr());
    hb_append_recv_context(&p, end);
    hb_append_obj_probe(&p, end, "owner", owner);
    hb_append_obj_probe(&p, end, "event", a2);
    hb_append_obj_probe(&p, end, "event_40", event_40);
    append_raw(&p, end, "}");
    *p = 0;
    hb_route_dbg(line);
}

int64_t __fastcall hb_route_item_dispatch_hook(int64_t a1, int64_t a2) {
    LONG hit = hb_hook_record_hit(HB_HOOK_ROUTE_ITEM_DISPATCH);
    int64_t retaddr = (int64_t)_ReturnAddress();
    int64_t ret = 0;
    hb_log_route_item_dispatch_probe("before", hit, retaddr, a1, a2, 0);
    ret = hb_call_route_item_dispatch_original(a1, a2);
    hb_log_route_item_dispatch_probe("after", hit, retaddr, a1, a2, ret);
    return ret;
}

static void hb_log_route_classifier_probe(LONG hit, int64_t retaddr, int64_t route_desc, int64_t route_type) {
    char line[32768];
    char *p = line;
    char *end = line + sizeof(line) - 1;
    char num[64];

    append_raw(&p, end, "{\"event\":\"hb_route_classifier\"");
    append_raw(&p, end, ",\"hit\":"); wsprintfA(num, "%ld", (long)hit); append_raw(&p, end, num);
    append_raw(&p, end, ",\"tid\":"); wsprintfA(num, "%lu", (unsigned long)GetCurrentThreadId()); append_raw(&p, end, num);
    append_hex_field(&p, end, "ret", retaddr);
    append_hex_field(&p, end, "route_desc", route_desc);
    append_dec_field(&p, end, "route_type", route_type);
    hb_append_recv_context(&p, end);
    hb_append_route_desc_fields(&p, end, "desc", route_desc, 1);
    append_raw(&p, end, "}");
    *p = 0;
    hb_route_dbg(line);
}

int64_t __fastcall hb_route_classifier_hook(int64_t route_desc) {
    LONG hit = hb_hook_record_hit(HB_HOOK_ROUTE_CLASSIFIER);
    int64_t retaddr = (int64_t)_ReturnAddress();
    int32_t msg_type = route_desc ? r32((void *)(route_desc + 0x08)) : 0;
    int32_t subtype = route_desc ? r32((void *)(route_desc + 0x0C)) : 0;
    int64_t ret = hb_call_route_classifier_original(route_desc);
    hb_runtime_set_route_desc(route_desc);
    hb_runtime_set_route_type((LONG)ret);
    if (ret == 39 || ret == 37 || (msg_type == 49 && subtype == 2001)) {
        hb_log_route_classifier_probe(hit, retaddr, route_desc, ret);
    }
    return ret;
}

