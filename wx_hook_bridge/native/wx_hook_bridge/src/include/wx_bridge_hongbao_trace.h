#ifndef WX_BRIDGE_HONGBAO_TRACE_H
#define WX_BRIDGE_HONGBAO_TRACE_H

#include "wx_bridge_platform.h"

#define HB_ROUTE_DESC_SIZE 0x388
#define HB_SOURCE_SIZE 0x290

typedef int64_t (__fastcall *FnHbRouteCandidate)(int64_t a1, int64_t a2, int64_t a3, int64_t a4);
typedef int64_t (__fastcall *FnHbGeneric4)(int64_t a1, int64_t a2, int64_t a3, int64_t a4);

void append_hex_field(char **p, char *end, const char *name, int64_t value);
void append_dec_field(char **p, char *end, const char *name, int64_t value);

void read_probe_string(int64_t addr, char *out, int max);
void hb_read_functional_string(int64_t addr, char *out, int max);

void hb_append_str_field(char **p, char *end, const char *name, const char *value);
void hb_append_probe_string_field(char **p, char *end, const char *name, int64_t addr);
void hb_append_std_string_field(char **p, char *end, const char *prefix, int64_t addr);
void hb_append_obj_probe(char **p, char *end, const char *prefix, int64_t obj);
void hb_append_obj_string_hits(char **p, char *end, const char *name, int64_t obj);
void hb_append_recv_context(char **p, char *end);
void hb_append_route_desc_fields(char **p, char *end, const char *prefix,
                                 int64_t desc, int include_payload_detail);

void hb_extract_urls_from_text(const char *text,
                               char *native_url,
                               int native_max,
                               char *page_url,
                               int page_max);
void hb_fill_query_fields(const char *native_url,
                          const char *page_url,
                          char *sendid,
                          int sendid_max,
                          char *channelid,
                          int channelid_max,
                          char *msgtype,
                          int msgtype_max,
                          char *sendusername,
                          int sendusername_max,
                          char *sign,
                          int sign_max,
                          char *total_num,
                          int total_num_max);
int hb_source_extract_hongbao(int64_t source, char *sendid, int sendid_max,
                              char *snippet, int snippet_max);
int hb_source_extract_hongbao_context(int64_t source,
                                      char *sendid,
                                      int sendid_max,
                                      char *native_url,
                                      int native_max,
                                      char *page_url,
                                      int page_max,
                                      char *peer,
                                      int peer_max,
                                      char *sender,
                                      int sender_max,
                                      char *snippet,
                                      int snippet_max);

#endif /* WX_BRIDGE_HONGBAO_TRACE_H */
