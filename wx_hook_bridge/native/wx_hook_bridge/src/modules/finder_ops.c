/*
 * Module: Finder search and live operations.
 *
 * Finder search is transported through Weixin's H5ExtTransfer manager using
 * the exact mmpcwebsearch request shape observed from Search/app.html.
 */

#include "../include/wx_bridge_finder_ops.h"
#include "../include/wx_bridge_file_io.h"
#include "../include/wx_bridge_h5ext_transfer.h"
#include "../include/wx_bridge_open_url.h"
#include "../include/wx_bridge_paths.h"
#include "../include/wx_bridge_protocol.h"
#include "../include/wx_bridge_text.h"
#include "../third_party/mongoose.h"

#include <string.h>

#define FINDER_SEARCH_CGI "/cgi-bin/mmsearch-bin/mmpcwebsearch"
#define FINDER_SEARCH_CMD_ID 6423
#define FINDER_SEARCH_SCOPE "search"
#define FINDER_SEARCH_H5_URL "weixin://resourceid/Search/app.html"
#define FINDER_SEARCH_H5_VERSION 80213977
#define FINDER_SEARCH_WIDGET_VERSION 1023022
#define FINDER_SEARCH_MAX_RESPONSE (32 * 1024 * 1024)
#define FINDER_SEARCH_USER_AGENT \
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 " \
    "(KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36 NetType/WIFI " \
    "MicroMessenger/7.0.20.1781(0x6700143B) WindowsWechat(0x63090a13) " \
    "UnifiedPCWindowsWechat(0xf2541917) XWEB/19841"
#define FINDER_LIVE_URL_PREFIX "https://channels.weixin.qq.com/web/pages/live"
#define FINDER_INIT_CGI "/cgi-bin/micromsg-bin/pc_finderinit"
#define FINDER_INIT_CMD_ID 5831
#define FINDER_GET_COMMENT_DETAIL_CGI "/cgi-bin/micromsg-bin/pc_findergetcommentdetail"
#define FINDER_GET_COMMENT_DETAIL_CMD_ID 5259
#define FINDER_JOIN_LIVE_CGI "/cgi-bin/micromsg-bin/pc_finderjoinlive"
#define FINDER_JOIN_LIVE_CMD_ID 4233
#define FINDER_POST_LIVE_MSG_CGI "/cgi-bin/micromsg-bin/pc_finderpostlivemsg"
#define FINDER_POST_LIVE_MSG_CMD_ID 4145
#define FINDER_LIVE_SCOPE "finderLive"
#define FINDER_LIVE_COMMENT_SCENE 142
#define FINDER_USERPAGE_H5_CGI "/cgi-bin/micromsg-bin/pc_finderuserpage"
#define FINDER_USERPAGE_H5_CMD_ID 6624
#define FINDER_USERPAGE_H5_SCOPE "finderLive"
#define FINDER_USERPAGE_H5_URL "https://channels.weixin.qq.com/web/pages/home"
#define FINDER_USERPAGE_H5_MAX_RESPONSE (32 * 1024 * 1024)

static volatile LONG g_finder_live_client_msg_seed = 0;
static volatile LONG g_finder_detail_request_seed = 0;

static int finder_read_entire_file(const WCHAR *path, char **out, DWORD *size_out) {
    HANDLE file;
    LARGE_INTEGER size;
    char *buffer;
    DWORD read = 0;
    BOOL ok;
    if (!out || !size_out) return 0;
    *out = NULL;
    *size_out = 0;
    file = CreateFileW(path, GENERIC_READ,
                       FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
                       NULL, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, NULL);
    if (file == INVALID_HANDLE_VALUE) return 0;
    if (!GetFileSizeEx(file, &size) || size.QuadPart <= 0 ||
        size.QuadPart > FINDER_SEARCH_MAX_RESPONSE) {
        CloseHandle(file);
        return 0;
    }
    buffer = (char *)VirtualAlloc(NULL, (SIZE_T)size.QuadPart + 1,
                                  MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE);
    if (!buffer) {
        CloseHandle(file);
        return 0;
    }
    ok = ReadFile(file, buffer, (DWORD)size.QuadPart, &read, NULL);
    CloseHandle(file);
    if (!ok || read != (DWORD)size.QuadPart) {
        VirtualFree(buffer, 0, MEM_RELEASE);
        return 0;
    }
    buffer[read] = 0;
    *out = buffer;
    *size_out = read;
    return 1;
}

static const char *finder_skip_json_space(const char *value, DWORD size) {
    const unsigned char *p = (const unsigned char *)value;
    const unsigned char *end;
    if (!p) return "";
    end = p + size;
    if (size >= 3 && p[0] == 0xEF && p[1] == 0xBB && p[2] == 0xBF) p += 3;
    while (p < end && (*p == ' ' || *p == '\t' || *p == '\r' || *p == '\n')) p++;
    return (const char *)p;
}

static int finder_json_value_start(char c) {
    return c == '{' || c == '[' || c == '"' || c == '-' ||
           (c >= '0' && c <= '9') || c == 't' || c == 'f' || c == 'n';
}

static int finder_json_rets_ok(struct mg_str json,
                               const char *const paths[],
                               int path_count) {
    int i;
    for (i = 0; i < path_count; i++) {
        if (mg_json_get(json, paths[i], NULL) >= 0 &&
            mg_json_get_long(json, paths[i], -1) != 0) {
            return 0;
        }
    }
    return 1;
}

static int finder_search_response_business_ok(const char *response_json,
                                               DWORD response_size) {
    static const char *const outer_ret_paths[] = {
        "$.jsapi_resp.ret", "$.jsapiResp.ret", "$.ret", "$.Ret",
        "$.base_resp.ret", "$.base_response.ret", "$.BaseResponse.Ret",
        "$.err_code", "$.errCode"
    };
    static const char *const inner_ret_paths[] = {
        "$.BaseResponse.Ret", "$.base_response.ret", "$.base_resp.ret",
        "$.ret", "$.Ret", "$.err_code", "$.errCode"
    };
    struct mg_str outer;
    char *inner;
    int ok;
    if (!response_json || response_size == 0) return 0;
    outer = mg_str_n(response_json, response_size);
    if (!finder_json_rets_ok(
            outer, outer_ret_paths,
            (int)(sizeof(outer_ret_paths) / sizeof(outer_ret_paths[0])))) {
        return 0;
    }
    inner = mg_json_get_str(outer, "$.jsapi_resp.resp_json");
    if (!inner) inner = mg_json_get_str(outer, "$.jsapiResp.respJson");
    if (!inner) inner = mg_json_get_str(outer, "$.resp_json");
    if (!inner || !inner[0]) {
        if (inner) free(inner);
        return 1;
    }
    ok = finder_json_value_start(finder_skip_json_space(inner, (DWORD)lstrlenA(inner))[0]);
    if (ok) {
        const char *inner_json = finder_skip_json_space(inner, (DWORD)lstrlenA(inner));
        struct mg_str parsed = mg_str_n(inner_json, lstrlenA(inner_json));
        ok = finder_json_rets_ok(
            parsed, inner_ret_paths,
            (int)(sizeof(inner_ret_paths) / sizeof(inner_ret_paths[0])));
    }
    free(inner);
    return ok;
}

static int finder_write_search_result(const char *seq,
                                      int ok,
                                      const char *query,
                                      int offset,
                                      int scene,
                                      int business_type,
                                      int sub_type,
                                      int current_page,
                                      const char *session_id,
                                      const char *request_id) {
    WCHAR result_path[1024];
    WCHAR response_path[1024];
    char response_path_utf8[2048];
    char *response = NULL;
    const char *response_json = "";
    DWORD response_size = 0;
    SIZE_T query_len = query ? (SIZE_T)lstrlenA(query) : 0;
    SIZE_T cap;
    char *json;
    char *p;
    char *end;
    char num[64];
    int response_valid;
    int transport_ok = ok;
    int business_ok = 0;
    DWORD response_json_size = 0;

    wsprintfW(result_path, L"%s\\result_%S.json", CONTROL_DIR, seq ? seq : "");
    wsprintfW(response_path, L"%s\\h5ext_%S.json", CONTROL_DIR, seq ? seq : "");
    response_path_utf8[0] = 0;
    WideCharToMultiByte(CP_UTF8, 0, response_path, -1,
                        response_path_utf8, sizeof(response_path_utf8), NULL, NULL);
    if (finder_read_entire_file(response_path, &response, &response_size)) {
        response_json = finder_skip_json_space(response, response_size);
        response_json_size = response_size - (DWORD)(response_json - response);
    }
    response_valid = response_size > 0 && finder_json_value_start(response_json[0]);
    business_ok = response_valid &&
                  finder_search_response_business_ok(response_json, response_json_size);
    ok = transport_ok && business_ok;

    cap = (SIZE_T)response_size + query_len * 6 +
          (session_id ? (SIZE_T)lstrlenA(session_id) * 6 : 0) +
          (request_id ? (SIZE_T)lstrlenA(request_id) * 6 : 0) + 4096;
    if (cap < 8192) cap = 8192;
    json = (char *)VirtualAlloc(NULL, cap, MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE);
    if (!json) {
        if (response) VirtualFree(response, 0, MEM_RELEASE);
        return 0;
    }
    p = json;
    end = json + cap - 1;
    append_raw(&p, end, "{\"ok\":");
    append_raw(&p, end, ok ? "true" : "false");
    append_raw(&p, end, ",\"transportOk\":");
    append_raw(&p, end, transport_ok ? "true" : "false");
    append_raw(&p, end, ",\"businessOk\":");
    append_raw(&p, end, business_ok ? "true" : "false");
    append_raw(&p, end, ",\"seq\":\"");
    append_escaped(&p, end, seq ? seq : "");
    append_raw(&p, end, "\",\"command\":\"finder_search\",\"message\":\"");
    append_raw(&p, end, ok ? "finder_search ok" : "finder_search failed");
    append_raw(&p, end, "\",\"query\":\"");
    append_escaped(&p, end, query ? query : "");
    append_raw(&p, end, "\",\"offset\":");
    wsprintfA(num, "%d", offset);
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"scene\":");
    wsprintfA(num, "%d", scene);
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"businessType\":");
    wsprintfA(num, "%d", business_type);
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"subType\":");
    wsprintfA(num, "%d", sub_type);
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"currentPage\":");
    wsprintfA(num, "%d", current_page);
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"sessionId\":\"");
    append_escaped(&p, end, session_id ? session_id : "");
    append_raw(&p, end, "\",\"requestId\":\"");
    append_escaped(&p, end, request_id ? request_id : "");
    append_raw(&p, end, "\"");
    append_raw(&p, end, ",\"responseSize\":");
    wsprintfA(num, "%lu", (unsigned long)response_size);
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"responseFile\":\"");
    append_escaped(&p, end, response_path_utf8);
    append_raw(&p, end, "\",\"response\":");
    append_raw(&p, end, response_valid ? response_json : "null");
    append_raw(&p, end, "}");
    *p = 0;
    write_file_bytes(result_path, json, (DWORD)(p - json));
    VirtualFree(json, 0, MEM_RELEASE);
    if (response) VirtualFree(response, 0, MEM_RELEASE);
    return ok;
}

static int finder_build_search_request(const char *query,
                                       int offset,
                                       int scene,
                                       int business_type,
                                       int sub_type,
                                       int current_page,
                                       const char *session_id,
                                       const char *request_id,
                                       char *out,
                                       int out_max) {
    char num[64];
    char *p = out;
    char *end = out + out_max - 1;
    int first_request;
    if (!out || out_max < 2 || !query || !query[0]) return 0;
    if (offset < 0) offset = 0;
    if (offset > 1000000) offset = 1000000;
    if (current_page <= 0) {
        if (offset > 0) return 0;
        current_page = 1;
    }
    first_request = offset == 0 && current_page <= 1;

    append_raw(&p, end, "{\"IsHomePage\":1,\"Offset\":");
    wsprintfA(num, "%d", offset);
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"Keyword\":\"");
    append_escaped(&p, end, query);
    append_raw(&p, end, "\",\"CliVersion\":");
    wsprintfA(num, "%d", FINDER_SEARCH_H5_VERSION);
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"Scene\":");
    wsprintfA(num, "%d", scene);
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"SceneActionType\":6,\"Language\":\"zh_CN\",\"SessionID\":\"");
    append_escaped(&p, end, session_id);
    append_raw(&p, end, "\",\"RequestId\":\"");
    append_escaped(&p, end, request_id);
    append_raw(&p, end, "\",\"ExtReqParams\":[");
    append_raw(&p, end, "{\"key\":\"netType\",\"textValue\":\"wifi\"},");
    append_raw(&p, end, "{\"key\":\"subType\",\"uint_value\":");
    wsprintfA(num, "%d", sub_type);
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"text_value\":\"");
    append_raw(&p, end, num);
    append_raw(&p, end, "\"},");
    append_raw(&p, end, "{\"key\":\"currentPage\",\"uint_value\":");
    wsprintfA(num, "%d", current_page);
    append_raw(&p, end, num);
    append_raw(&p, end, "},{\"key\":\"requestId\",\"text_value\":\"");
    append_escaped(&p, end, request_id);
    append_raw(&p, end, "\"},{\"key\":\"widgetVersion\",\"uint_value\":");
    wsprintfA(num, "%d", FINDER_SEARCH_WIDGET_VERSION);
    append_raw(&p, end, num);
    append_raw(&p, end, "},{\"key\":\"windowWidth\",\"uint_value\":0},");
    append_raw(&p, end, "{\"key\":\"AdPassThroughInfo\",\"text_value\":\"");
    append_escaped(&p, end, FINDER_SEARCH_USER_AGENT);
    append_raw(&p, end, "\"},{\"key\":\"AdCommonDeviceId\",\"text_value\":\"\"},");
    append_raw(&p, end, "{\"key\":\"firstSearchRequest\",\"uint_value\":");
    append_raw(&p, end, first_request ? "1" : "0");
    append_raw(&p, end, "},{\"key\":\"firstSearchQuery\",\"text_value\":\"");
    append_escaped(&p, end, first_request ? query : "");
    append_raw(&p, end, "\"},{\"key\":\"TemplateNightModeType\",\"uint_value\":0},");
    append_raw(&p, end, "{\"key\":\"fontRatio\",\"uint_value\":100},");
    append_raw(&p, end, "{\"key\":\"realH5Version\",\"uint_value\":");
    wsprintfA(num, "%d", FINDER_SEARCH_H5_VERSION);
    append_raw(&p, end, num);
    append_raw(&p, end, "},{\"key\":\"parentSearchID\",\"text_value\":\"7::::\"},");
    append_raw(&p, end, "{\"key\":\"enableTopqaConversationMode\",\"uint_value\":1}],");
    append_raw(&p, end, "\"BusinessType\":\"");
    wsprintfA(num, "%d", business_type);
    append_raw(&p, end, num);
    append_raw(&p, end, "\"}");
    *p = 0;
    return p < end;
}

int finder_search_to_result(const char *seq,
                            const char *query,
                            int offset,
                            int scene,
                            int business_type,
                            int sub_type,
                            int current_page,
                            const char *session_id,
                            const char *request_id,
                            int wait_ms) {
    char request_json[32768];
    char effective_session_id[128];
    char effective_request_id[128];
    int transport_ok;
    if (!seq || !seq[0]) return 0;
    if (offset < 0) offset = 0;
    if (offset > 1000000) offset = 1000000;
    if (scene <= 0) scene = 88;
    if (business_type < 0) business_type = 0;
    if (sub_type < 0) sub_type = 0;
    if (current_page <= 0) {
        if (offset > 0) {
            return finder_write_search_result(seq, 0, query, offset, scene,
                                              business_type, sub_type, 0,
                                              session_id, request_id);
        }
        current_page = 1;
    }
    if (current_page > 1000001) current_page = 1000001;
    safe_copy(effective_session_id, sizeof(effective_session_id), session_id ? session_id : "");
    safe_copy(effective_request_id, sizeof(effective_request_id), request_id ? request_id : "");
    if (!effective_session_id[0]) {
        wsprintfA(effective_session_id, "bridge-session-%lu-%s",
                  (unsigned long)GetCurrentProcessId(), seq);
    }
    if (!effective_request_id[0]) {
        wsprintfA(effective_request_id, "bridge-request-%s", seq);
    }
    if (!query || !query[0]) {
        return finder_write_search_result(seq, 0, query, offset, scene, business_type,
                                          sub_type, current_page,
                                          effective_session_id, effective_request_id);
    }
    ZeroMemory(request_json, sizeof(request_json));
    if (!finder_build_search_request(query, offset, scene,
                                     business_type, sub_type, current_page,
                                     effective_session_id, effective_request_id,
                                     request_json, sizeof(request_json))) {
        return finder_write_search_result(seq, 0, query, offset, scene, business_type,
                                          sub_type, current_page,
                                          effective_session_id, effective_request_id);
    }
    transport_ok = h5ext_transfer_to_result(seq,
                                            FINDER_SEARCH_CGI,
                                            FINDER_SEARCH_CMD_ID,
                                            request_json,
                                            FINDER_SEARCH_SCOPE,
                                            wait_ms,
                                            400,
                                            FINDER_SEARCH_H5_URL,
                                            1,
                                            "");
    return finder_write_search_result(seq, transport_ok, query, offset, scene, business_type,
                                      sub_type, current_page,
                                      effective_session_id, effective_request_id);
}

static int finder_append_url_encoded(char **p, char *end, const char *value) {
    static const char hex[] = "0123456789ABCDEF";
    const unsigned char *s = (const unsigned char *)(value ? value : "");
    while (*s) {
        unsigned char c = *s++;
        if ((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') ||
            (c >= '0' && c <= '9') || c == '-' || c == '_' || c == '.' || c == '~') {
            if (*p >= end) return 0;
            *(*p)++ = (char)c;
        } else if (end - *p >= 3) {
            *(*p)++ = '%';
            *(*p)++ = hex[(c >> 4) & 0x0F];
            *(*p)++ = hex[c & 0x0F];
        } else {
            return 0;
        }
    }
    return 1;
}

static int finder_is_decimal_object_id(const char *value) {
    int len = 0;
    if (!value || !value[0] || value[0] == '0') return 0;
    while (value[len]) {
        if (value[len] < '0' || value[len] > '9') return 0;
        len++;
        if (len > 24) return 0;
    }
    return len >= 8;
}

static int finder_is_export_object_id(const char *value) {
    int i;
    int len;
    if (!value || strncmp(value, "export/", 7) != 0) return 0;
    len = lstrlenA(value);
    if (len < 16 || len > 1024) return 0;
    for (i = 7; i < len; i++) {
        char c = value[i];
        if (!((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') ||
              (c >= '0' && c <= '9') || c == '-' || c == '_' ||
              c == '+' || c == '/' || c == '=')) {
            return 0;
        }
    }
    return 1;
}

static int finder_is_valid_object_id(const char *value) {
    return finder_is_decimal_object_id(value) || finder_is_export_object_id(value);
}

static int finder_decimal_prefix(const char *value, char *out, int out_max) {
    int len = 0;
    if (!value || !value[0] || !out || out_max < 2) return 0;
    while (value[len] >= '0' && value[len] <= '9') {
        if (len + 1 >= out_max) return 0;
        out[len] = value[len];
        len++;
    }
    out[len] = 0;
    return len >= 8;
}

static int finder_parse_uint64_decimal(const char *value, ULONGLONG *out) {
    ULONGLONG result = 0;
    const ULONGLONG max_value = ~(ULONGLONG)0;
    const unsigned char *p = (const unsigned char *)value;
    if (!p || !*p || !out) return 0;
    while (*p) {
        unsigned int digit;
        if (*p < '0' || *p > '9') return 0;
        digit = (unsigned int)(*p - '0');
        if (result > (max_value - digit) / 10) return 0;
        result = result * 10 + digit;
        p++;
    }
    *out = result;
    return 1;
}

static int finder_encode_uint64_base64url(const char *value, char *out, int out_max) {
    static const char alphabet[] =
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_";
    ULONGLONG number;
    unsigned char bytes[8];
    int i;
    int o = 0;
    if (!out || out_max < 12 || !finder_parse_uint64_decimal(value, &number)) return 0;
    for (i = 7; i >= 0; i--) {
        bytes[i] = (unsigned char)(number & 0xFF);
        number >>= 8;
    }
    for (i = 0; i + 3 <= 8; i += 3) {
        unsigned int block = ((unsigned int)bytes[i] << 16) |
                             ((unsigned int)bytes[i + 1] << 8) |
                             (unsigned int)bytes[i + 2];
        out[o++] = alphabet[(block >> 18) & 0x3F];
        out[o++] = alphabet[(block >> 12) & 0x3F];
        out[o++] = alphabet[(block >> 6) & 0x3F];
        out[o++] = alphabet[block & 0x3F];
    }
    if (i < 8) {
        unsigned int block = (unsigned int)bytes[i] << 16;
        out[o++] = alphabet[(block >> 18) & 0x3F];
        if (i + 1 < 8) {
            block |= (unsigned int)bytes[i + 1] << 8;
            out[o++] = alphabet[(block >> 12) & 0x3F];
            out[o++] = alphabet[(block >> 6) & 0x3F];
        } else {
            out[o++] = alphabet[(block >> 12) & 0x3F];
        }
    }
    out[o] = 0;
    return 1;
}

static int finder_build_live_h5_url(char *out,
                                    int out_max,
                                    const char *object_id,
                                    const char *object_nonce_id,
                                    const char *context_id,
                                    const char *entrance_id,
                                    const char *sub_entrance_id) {
    char nonce_prefix[64];
    char encoded_object_id[16];
    char encoded_nonce_id[16];
    char *p;
    char *end;
    int ok = 1;
    if (!out || out_max < 2 || !finder_is_valid_object_id(object_id)) return 0;
    p = out;
    end = out + out_max - 1;
    if (finder_is_export_object_id(object_id)) {
        append_raw(&p, end, FINDER_LIVE_URL_PREFIX "?eid=");
        ok = finder_append_url_encoded(&p, end, object_id);
    } else {
        if (!object_nonce_id || !object_nonce_id[0] ||
            !finder_decimal_prefix(object_nonce_id, nonce_prefix, sizeof(nonce_prefix)) ||
            !finder_encode_uint64_base64url(object_id, encoded_object_id,
                                            sizeof(encoded_object_id)) ||
            !finder_encode_uint64_base64url(nonce_prefix, encoded_nonce_id,
                                            sizeof(encoded_nonce_id))) {
            return 0;
        }
        append_raw(&p, end, FINDER_LIVE_URL_PREFIX "?oid=");
        append_raw(&p, end, encoded_object_id);
        append_raw(&p, end, "&nid=");
        append_raw(&p, end, encoded_nonce_id);
    }
    if (context_id && context_id[0]) {
        append_raw(&p, end, "&context_id=");
        if (ok) ok = finder_append_url_encoded(&p, end, context_id);
    }
    append_raw(&p, end, "&entrance_id=");
    if (ok) {
        ok = finder_append_url_encoded(&p, end,
                                       (entrance_id && entrance_id[0]) ? entrance_id : "88");
    }
    if (sub_entrance_id && sub_entrance_id[0]) {
        append_raw(&p, end, "&sub_entranceid=");
        if (ok) ok = finder_append_url_encoded(&p, end, sub_entrance_id);
    }
    *p = 0;
    return ok && p < end;
}

static void finder_write_live_enter_result(const char *seq,
                                           int ok,
                                           const char *message,
                                           const char *url) {
    WCHAR path[1024];
    SIZE_T cap = (url ? (SIZE_T)lstrlenA(url) * 6 : 0) + 2048;
    char *json = (char *)VirtualAlloc(NULL, cap, MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE);
    char *p;
    char *end;
    if (!json) return;
    p = json;
    end = json + cap - 1;
    wsprintfW(path, L"%s\\result_%S.json", CONTROL_DIR, seq ? seq : "");
    append_raw(&p, end, "{\"ok\":");
    append_raw(&p, end, ok ? "true" : "false");
    append_raw(&p, end, ",\"seq\":\"");
    append_escaped(&p, end, seq ? seq : "");
    append_raw(&p, end, "\",\"command\":\"finder_live_enter\",\"message\":\"");
    append_escaped(&p, end, message ? message : "");
    append_raw(&p, end, "\",\"url\":\"");
    append_escaped(&p, end, url ? url : "");
    append_raw(&p, end, "\"}");
    *p = 0;
    write_file_bytes(path, json, (DWORD)(p - json));
    VirtualFree(json, 0, MEM_RELEASE);
}

int finder_enter_live_to_result(const char *seq,
                                const char *url,
                                const char *object_id,
                                const char *nonce_id,
                                const char *context_id,
                                const char *entrance_id,
                                const char *sub_entrance_id,
                                int muted) {
    char built_url[8192];
    const char *live_url = url;
    int ok;
    if (!seq || !seq[0]) return 0;

    if (live_url && live_url[0]) {
        SIZE_T prefix_len = sizeof(FINDER_LIVE_URL_PREFIX) - 1;
        int live_url_len = lstrlenA(live_url);
        char suffix = live_url_len >= (int)prefix_len ? live_url[prefix_len] : 0;
        if (live_url_len < (int)prefix_len || live_url_len >= (int)sizeof(built_url) ||
            strncmp(live_url, FINDER_LIVE_URL_PREFIX, prefix_len) != 0 ||
            (suffix && suffix != '?' && suffix != '#')) {
            finder_write_live_enter_result(seq, 0, "invalid finder live url", live_url);
            return 0;
        }
    } else {
        if (!object_id || !object_id[0]) {
            finder_write_live_enter_result(seq, 0, "missing objectId", "");
            return 0;
        }
        if (!finder_build_live_h5_url(built_url, sizeof(built_url), object_id, nonce_id,
                                      context_id, entrance_id, sub_entrance_id)) {
            finder_write_live_enter_result(seq, 0,
                                           "invalid finder objectId or nonceId", "");
            return 0;
        }
        if (muted) {
            int built_len = lstrlenA(built_url);
            if (built_len + 11 >= (int)sizeof(built_url)) {
                finder_write_live_enter_result(seq, 0, "finder live url too long", "");
                return 0;
            }
            lstrcatA(built_url, "&muted=true");
        }
        live_url = built_url;
    }

    ok = open_url_to_result(seq, live_url);
    finder_write_live_enter_result(seq,
                                   ok,
                                   ok ? "finder live enter submitted" : "finder live enter failed",
                                   live_url);
    return ok;
}

static int finder_is_valid_live_url(const char *url) {
    SIZE_T prefix_len = sizeof(FINDER_LIVE_URL_PREFIX) - 1;
    int url_len;
    char suffix;
    if (!url || !url[0]) return 0;
    url_len = lstrlenA(url);
    suffix = url_len >= (int)prefix_len ? url[prefix_len] : 0;
    return url_len >= (int)prefix_len && url_len < 8192 &&
           strncmp(url, FINDER_LIVE_URL_PREFIX, prefix_len) == 0 &&
           (!suffix || suffix == '?' || suffix == '#');
}

typedef struct FinderH5Response {
    char *file_json;
    DWORD file_size;
    char *payload_json;
    DWORD payload_size;
    int payload_owned;
} FinderH5Response;

static char *finder_heap_copy(const char *value) {
    SIZE_T len = value ? (SIZE_T)lstrlenA(value) : 0;
    char *copy = (char *)malloc(len + 1);
    if (!copy) return NULL;
    if (len) CopyMemory(copy, value, len);
    copy[len] = 0;
    return copy;
}

static void finder_free_h5_response(FinderH5Response *response) {
    if (!response) return;
    if (response->payload_owned && response->payload_json) free(response->payload_json);
    if (response->file_json) VirtualFree(response->file_json, 0, MEM_RELEASE);
    ZeroMemory(response, sizeof(*response));
}

static int finder_load_h5_response(const char *seq, FinderH5Response *response) {
    WCHAR path[1024];
    const char *payload;
    SIZE_T offset;
    struct mg_str outer;
    char *inner = NULL;
    if (!seq || !seq[0] || !response) return 0;
    ZeroMemory(response, sizeof(*response));
    wsprintfW(path, L"%s\\h5ext_%S.json", CONTROL_DIR, seq);
    if (!finder_read_entire_file(path, &response->file_json, &response->file_size)) return 0;
    payload = finder_skip_json_space(response->file_json, response->file_size);
    offset = (SIZE_T)(payload - response->file_json);
    if (offset >= response->file_size || !finder_json_value_start(payload[0])) return 0;
    response->payload_json = (char *)payload;
    response->payload_size = response->file_size - (DWORD)offset;
    outer = mg_str_n(response->payload_json, response->payload_size);
    inner = mg_json_get_str(outer, "$.jsapi_resp.resp_json");
    if (!inner) inner = mg_json_get_str(outer, "$.resp_json");
    if (inner && inner[0]) {
        const char *inner_json = finder_skip_json_space(inner, (DWORD)lstrlenA(inner));
        if (finder_json_value_start(inner_json[0])) {
            if (inner_json != inner) MoveMemory(inner, inner_json, lstrlenA(inner_json) + 1);
            response->payload_json = inner;
            response->payload_size = (DWORD)lstrlenA(inner);
            response->payload_owned = 1;
            return 1;
        }
    }
    if (inner) free(inner);
    return 1;
}

static char *finder_json_first_raw(const FinderH5Response *response,
                                   const char *const paths[],
                                   int path_count) {
    struct mg_str json;
    int i;
    if (!response || !response->payload_json || response->payload_size == 0) return NULL;
    json = mg_str_n(response->payload_json, response->payload_size);
    for (i = 0; i < path_count; i++) {
        int token_len = 0;
        int offset = mg_json_get(json, paths[i], &token_len);
        char *value;
        if (offset < 0 || token_len <= 0) continue;
        value = (char *)malloc((SIZE_T)token_len + 1);
        if (!value) return NULL;
        CopyMemory(value, response->payload_json + offset, token_len);
        value[token_len] = 0;
        return value;
    }
    return NULL;
}

static char *finder_json_first_string(const FinderH5Response *response,
                                      const char *const paths[],
                                      int path_count) {
    struct mg_str json;
    int i;
    if (!response || !response->payload_json || response->payload_size == 0) return NULL;
    json = mg_str_n(response->payload_json, response->payload_size);
    for (i = 0; i < path_count; i++) {
        char *value = mg_json_get_str(json, paths[i]);
        if (value) return value;
    }
    return finder_json_first_raw(response, paths, path_count);
}

static int finder_parse_live_identity_value(const char *value) {
    const char *p = value;
    int quoted = 0;
    int identity;
    if (!p) return 0;
    while (*p == ' ' || *p == '\t' || *p == '\r' || *p == '\n') p++;
    if (*p == '"') {
        quoted = 1;
        p++;
    }
    if (p[0] < '1' || p[0] > '4') return 0;
    identity = p[0] - '0';
    p++;
    if (quoted) {
        if (*p != '"') return 0;
        p++;
    }
    while (*p == ' ' || *p == '\t' || *p == '\r' || *p == '\n') p++;
    return *p ? 0 : identity;
}

static int finder_json_first_live_identity(const FinderH5Response *response,
                                           const char *const paths[],
                                           int path_count) {
    char *value = finder_json_first_raw(response, paths, path_count);
    int identity = finder_parse_live_identity_value(value);
    if (value) free(value);
    return identity;
}

static int finder_json_value_available(const char *value) {
    const char *p = value;
    if (!p) return 0;
    while (*p == ' ' || *p == '\t' || *p == '\r' || *p == '\n') p++;
    if (!*p || str_eq(p, "null") || str_eq(p, "\"\"")) return 0;
    return 1;
}

static long finder_response_ret(const FinderH5Response *response, int *found) {
    static const char *const paths[] = {
        "$.base_response.ret", "$.base_response.Ret",
        "$.baseResponse.ret", "$.baseResponse.Ret",
        "$.BaseResponse.ret", "$.BaseResponse.Ret",
        "$.data.base_response.ret", "$.data.base_response.Ret",
        "$.data.baseResponse.ret", "$.data.baseResponse.Ret",
        "$.err_code", "$.errCode"
    };
    const long missing = 0x7fffffffL;
    struct mg_str json;
    int i;
    if (found) *found = 0;
    if (!response || !response->payload_json || response->payload_size == 0) return missing;
    json = mg_str_n(response->payload_json, response->payload_size);
    for (i = 0; i < (int)(sizeof(paths) / sizeof(paths[0])); i++) {
        long value = mg_json_get_long(json, paths[i], missing);
        if (value != missing) {
            if (found) *found = 1;
            return value;
        }
    }
    return missing;
}

static int finder_response_ok(int transport_ok, const FinderH5Response *response) {
    int found = 0;
    long ret;
    if (!transport_ok || !response || !response->payload_json) return 0;
    ret = finder_response_ret(response, &found);
    return !found || ret == 0;
}

static void finder_append_json_value(char **p, char *end, const char *value) {
    const char *s = value ? value : "";
    while (*s == ' ' || *s == '\t' || *s == '\r' || *s == '\n') s++;
    if (*s == '{' || *s == '[' || *s == '"' || *s == '-' ||
        (*s >= '0' && *s <= '9') || str_eq(s, "true") ||
        str_eq(s, "false") || str_eq(s, "null")) {
        append_raw(p, end, s);
    } else {
        append_raw(p, end, "\"");
        append_escaped(p, end, s);
        append_raw(p, end, "\"");
    }
}

static int finder_utf8_unit_len(const unsigned char *p, SIZE_T remaining) {
    if (!p || remaining == 0 || !p[0]) return 0;
    if (p[0] < 0x80) return 1;
    if (remaining >= 2 && (p[0] & 0xE0) == 0xC0 &&
        (p[1] & 0xC0) == 0x80) return 2;
    if (remaining >= 3 && (p[0] & 0xF0) == 0xE0 &&
        (p[1] & 0xC0) == 0x80 &&
        (p[2] & 0xC0) == 0x80) return 3;
    if (remaining >= 4 && (p[0] & 0xF8) == 0xF0 &&
        (p[1] & 0xC0) == 0x80 &&
        (p[2] & 0xC0) == 0x80 && (p[3] & 0xC0) == 0x80) return 4;
    return 1;
}

static int finder_normalize_live_comment(const char *content,
                                         char **normalized_out,
                                         int *unit_count_out) {
    SIZE_T len;
    char *normalized;
    const unsigned char *src;
    char *dst;
    int units = 0;
    int has_text = 0;
    if (!normalized_out) return 0;
    *normalized_out = NULL;
    if (unit_count_out) *unit_count_out = 0;
    if (!content) return 0;
    len = (SIZE_T)lstrlenA(content);
    if (len == 0 || len > 16384) return 0;
    normalized = (char *)malloc(len + 1);
    if (!normalized) return 0;
    src = (const unsigned char *)content;
    dst = normalized;
    while (*src) {
        int unit_len;
        if (*src == '\r' || *src == '\n' || *src == '\t') {
            if (*src == '\r' && src[1] == '\n') src++;
            *dst++ = ' ';
            src++;
            units++;
        } else {
            unit_len = finder_utf8_unit_len(src, len - (SIZE_T)(src - (const unsigned char *)content));
            CopyMemory(dst, src, unit_len);
            if (unit_len > 1 || *src > ' ') has_text = 1;
            dst += unit_len;
            src += unit_len;
            units++;
        }
        if (units > 60) {
            free(normalized);
            return 0;
        }
    }
    *dst = 0;
    if (!has_text) {
        free(normalized);
        return 0;
    }
    *normalized_out = normalized;
    if (unit_count_out) *unit_count_out = units;
    return 1;
}

static int finder_live_comment_input_lengths_ok(const char *live_cookies,
                                                const char *finder_username,
                                                const char *client_msg_id,
                                                const char *object_nonce_id,
                                                const char *context_id,
                                                const char *entrance_id,
                                                const char *sub_entrance_id,
                                                const char *userver_for_h5) {
    if (live_cookies && lstrlenA(live_cookies) > 131072) return 0;
    if (finder_username && lstrlenA(finder_username) > 256) return 0;
    if (client_msg_id && lstrlenA(client_msg_id) > 512) return 0;
    if (object_nonce_id && lstrlenA(object_nonce_id) > 4096) return 0;
    if (context_id && lstrlenA(context_id) > 4096) return 0;
    if (entrance_id && lstrlenA(entrance_id) > 256) return 0;
    if (sub_entrance_id && lstrlenA(sub_entrance_id) > 256) return 0;
    if (userver_for_h5 && lstrlenA(userver_for_h5) > 131072) return 0;
    return 1;
}

static int finder_append_live_base_req(char **p,
                                       char *end,
                                       const char *userver_for_h5,
                                       int live_identity,
                                       const char *context_id,
                                       const char *entrance_id,
                                       const char *sub_entrance_id) {
    char report_buffer[4096];
    char num[64];
    char *rp = report_buffer;
    char *rend = report_buffer + sizeof(report_buffer) - 1;
    const char *effective_entrance_id =
        (entrance_id && entrance_id[0]) ? entrance_id : "88";
    append_raw(&rp, rend, "{\"entranceId\":\"");
    append_escaped(&rp, rend, effective_entrance_id);
    append_raw(&rp, rend, "\"");
    if (sub_entrance_id && sub_entrance_id[0]) {
        append_raw(&rp, rend, ",\"subEntranceid\":\"");
        append_escaped(&rp, rend, sub_entrance_id);
        append_raw(&rp, rend, "\"");
    }
    append_raw(&rp, rend, "}");
    *rp = 0;
    if (rp >= rend) return 0;

    append_raw(p, end, "\"finder_basereq\":{\"scene\":");
    wsprintfA(num, "%d", FINDER_LIVE_COMMENT_SCENE);
    append_raw(p, end, num);
    if (userver_for_h5 && userver_for_h5[0]) {
        append_raw(p, end, ",\"userver_for_h5\":");
        finder_append_json_value(p, end, userver_for_h5);
    }
    if (live_identity >= 1 && live_identity <= 4) {
        append_raw(p, end, ",\"live_identity\":");
        wsprintfA(num, "%d", live_identity);
        append_raw(p, end, num);
    }
    append_raw(p, end, ",\"ctx_info\":{");
    if (context_id && context_id[0]) {
        append_raw(p, end, "\"context_id\":\"");
        append_escaped(p, end, context_id);
        append_raw(p, end, "\",");
    }
    append_raw(p, end, "\"client_report_buff\":\"");
    append_escaped(p, end, report_buffer);
    append_raw(p, end, "\"}}");
    return *p < end;
}

static int finder_nonce_is_join_ready(const char *object_nonce_id) {
    return object_nonce_id && object_nonce_id[0] && strchr(object_nonce_id, '_') != NULL;
}

static void finder_make_detail_request_id(char *out, int out_max) {
    FILETIME file_time;
    ULARGE_INTEGER ticks;
    ULONGLONG unix_ms;
    unsigned long suffix;
    LONG seed;
    if (!out || out_max < 24) return;
    GetSystemTimeAsFileTime(&file_time);
    ticks.LowPart = file_time.dwLowDateTime;
    ticks.HighPart = file_time.dwHighDateTime;
    unix_ms = ticks.QuadPart > 116444736000000000ULL
                  ? (ticks.QuadPart - 116444736000000000ULL) / 10000ULL
                  : (ULONGLONG)GetTickCount64();
    seed = InterlockedIncrement(&g_finder_detail_request_seed);
    suffix = ((unsigned long)seed ^ (unsigned long)GetTickCount() ^
              (unsigned long)GetCurrentProcessId()) % 1000000UL;
    wsprintfA(out, "%I64u%06lu", unix_ms, suffix);
}

static int finder_build_comment_detail_request(const char *object_id,
                                               const char *object_nonce_id,
                                               const char *context_id,
                                               const char *entrance_id,
                                               const char *sub_entrance_id,
                                               char *out,
                                               int out_max) {
    char request_id[32];
    char report_buffer[4096];
    char *rp = report_buffer;
    char *rend = report_buffer + sizeof(report_buffer) - 1;
    char *p = out;
    char *end = out + out_max - 1;
    const char *effective_entrance_id =
        (entrance_id && entrance_id[0]) ? entrance_id : "88";
    if (!out || out_max < 2 || !finder_is_valid_object_id(object_id)) return 0;
    finder_make_detail_request_id(request_id, sizeof(request_id));
    if (!request_id[0]) return 0;
    append_raw(&rp, rend, "{\"entranceId\":\"");
    append_escaped(&rp, rend, effective_entrance_id);
    append_raw(&rp, rend, "\"");
    if (sub_entrance_id && sub_entrance_id[0]) {
        append_raw(&rp, rend, ",\"subEntranceid\":\"");
        append_escaped(&rp, rend, sub_entrance_id);
        append_raw(&rp, rend, "\"");
    }
    append_raw(&rp, rend, "}");
    *rp = 0;
    if (rp >= rend) return 0;

    append_raw(&p, end, "{\"finder_basereq\":{\"expt_flag\":1,\"request_id\":\"");
    append_escaped(&p, end, request_id);
    append_raw(&p, end, "\",\"scene\":142,\"ctx_info\":{");
    if (context_id && context_id[0]) {
        append_raw(&p, end, "\"context_id\":\"");
        append_escaped(&p, end, context_id);
        append_raw(&p, end, "\",");
    }
    append_raw(&p, end, "\"client_report_buff\":\"");
    append_escaped(&p, end, report_buffer);
    append_raw(&p, end, "\"}},\"platform_scene\":2,");
    if (finder_is_export_object_id(object_id)) {
        append_raw(&p, end, "\"encrypted_objectid\":\"");
        append_escaped(&p, end, object_id);
        append_raw(&p, end, "\",");
    } else {
        append_raw(&p, end, "\"objectid\":\"");
        append_escaped(&p, end, object_id);
        append_raw(&p, end, "\",");
        if (object_nonce_id && object_nonce_id[0]) {
            append_raw(&p, end, "\"object_nonce_id\":\"");
            append_escaped(&p, end, object_nonce_id);
            append_raw(&p, end, "\",");
        }
    }
    append_raw(&p, end,
               "\"need_object\":1,\"scene\":142,\"direction\":2,"
               "\"identity_scene\":2,\"pull_scene\":1,"
               "\"status\":{\"supported_coding_format\":[\"h264\",\"h265\"]}}");
    *p = 0;
    return p < end;
}

static int finder_build_init_request(const char *context_id,
                                     const char *entrance_id,
                                     const char *sub_entrance_id,
                                     char *out,
                                     int out_max) {
    char *p = out;
    char *end = out + out_max - 1;
    if (!out || out_max < 2) return 0;
    append_raw(&p, end, "{");
    if (!finder_append_live_base_req(&p, end, "", 0, context_id,
                                     entrance_id, sub_entrance_id)) return 0;
    append_raw(&p, end, "}");
    *p = 0;
    return p < end;
}

static int finder_build_join_request(const char *live_id,
                                     const char *object_id,
                                     const char *object_nonce_id,
                                     const char *finder_username,
                                     const char *userver_for_h5,
                                     const char *context_id,
                                     const char *entrance_id,
                                     const char *sub_entrance_id,
                                     int live_identity,
                                     char *out,
                                     int out_max) {
    char *p = out;
    char *end = out + out_max - 1;
    if (!out || out_max < 2 || !object_nonce_id || !object_nonce_id[0]) return 0;
    append_raw(&p, end, "{");
    if (!finder_append_live_base_req(&p, end, userver_for_h5, live_identity,
                                     context_id, entrance_id, sub_entrance_id)) return 0;
    append_raw(&p, end, ",\"live_id\":\"");
    append_escaped(&p, end, live_id);
    append_raw(&p, end, "\",\"object_id\":\"");
    append_escaped(&p, end, object_id);
    append_raw(&p, end, "\",\"scene\":2,\"object_nonce_id\":\"");
    append_escaped(&p, end, object_nonce_id);
    append_raw(&p, end, "\",\"client_status\":{\"video_decoder_support_mask\":1}");
    if (finder_username && finder_username[0]) {
        append_raw(&p, end, ",\"finder_username\":\"");
        append_escaped(&p, end, finder_username);
        append_raw(&p, end, "\"");
    }
    append_raw(&p, end, "}");
    *p = 0;
    return p < end;
}

static int finder_build_live_comment_request(const char *content,
                                             const char *live_id,
                                             const char *object_id,
                                             const char *live_cookies,
                                             const char *finder_username,
                                             const char *client_msg_id,
                                             const char *context_id,
                                             const char *entrance_id,
                                             const char *sub_entrance_id,
                                             const char *userver_for_h5,
                                             int live_identity,
                                             char *out,
                                             int out_max) {
    char *p = out;
    char *end = out + out_max - 1;
    if (!out || out_max < 2) return 0;
    append_raw(&p, end, "{");
    if (!finder_append_live_base_req(&p, end, userver_for_h5, live_identity,
                                     context_id, entrance_id, sub_entrance_id)) return 0;
    append_raw(&p, end, ",\"live_cookies\":\"");
    append_escaped(&p, end, live_cookies);
    append_raw(&p, end, "\",\"msg\":{\"content\":\"");
    append_escaped(&p, end, content);
    append_raw(&p, end, "\",\"type\":1},\"live_id\":\"");
    append_escaped(&p, end, live_id);
    append_raw(&p, end, "\",\"client_msg_id\":\"");
    append_escaped(&p, end, client_msg_id);
    append_raw(&p, end, "\",\"object_id\":\"");
    append_escaped(&p, end, object_id);
    append_raw(&p, end, "\"");
    if (finder_username && finder_username[0]) {
        append_raw(&p, end, ",\"finder_username\":\"");
        append_escaped(&p, end, finder_username);
        append_raw(&p, end, "\"");
    }
    append_raw(&p, end, "}");
    *p = 0;
    return p < end;
}

static int finder_write_live_comment_result(const char *seq,
                                            int ok,
                                            const char *stage,
                                            const char *message,
                                            const char *live_id,
                                            const char *object_id,
                                            const char *client_msg_id,
                                            const char *h5_url,
                                            int live_identity,
                                            int init_attempted,
                                            int init_ok,
                                            int join_attempted,
                                            int join_ok,
                                            int post_ok,
                                            const char *userver_for_h5,
                                            const char *live_cookies,
                                            const FinderH5Response *response) {
    WCHAR result_path[1024];
    const char *response_json = "";
    DWORD response_size = 0;
    int response_valid = 0;
    int ret_found = 0;
    long response_ret = 0;
    SIZE_T cap;
    char *json;
    char *p;
    char *end;
    char num[64];
    if (response && response->payload_json && response->payload_size > 0) {
        response_json = response->payload_json;
        response_size = response->payload_size;
        response_valid = finder_json_value_start(response_json[0]);
        response_ret = finder_response_ret(response, &ret_found);
    }
    cap = (SIZE_T)response_size +
          (message ? (SIZE_T)lstrlenA(message) * 6 : 0) +
          (live_id ? (SIZE_T)lstrlenA(live_id) * 6 : 0) +
          (object_id ? (SIZE_T)lstrlenA(object_id) * 6 : 0) +
          (client_msg_id ? (SIZE_T)lstrlenA(client_msg_id) * 6 : 0) +
          (h5_url ? (SIZE_T)lstrlenA(h5_url) * 6 : 0) +
          (userver_for_h5 ? (SIZE_T)lstrlenA(userver_for_h5) * 6 : 0) +
          (live_cookies ? (SIZE_T)lstrlenA(live_cookies) * 6 : 0) + 8192;
    if (cap > FINDER_SEARCH_MAX_RESPONSE) {
        write_result(seq, 0, "finder_live_comment", "finder live comment result too large");
        return 0;
    }
    if (cap < 8192) cap = 8192;
    json = (char *)VirtualAlloc(NULL, cap, MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE);
    if (!json) return 0;
    p = json;
    end = json + cap - 1;
    wsprintfW(result_path, L"%s\\result_%S.json", CONTROL_DIR, seq ? seq : "");
    append_raw(&p, end, "{\"ok\":");
    append_raw(&p, end, ok ? "true" : "false");
    append_raw(&p, end, ",\"seq\":\"");
    append_escaped(&p, end, seq ? seq : "");
    append_raw(&p, end, "\",\"command\":\"finder_live_comment\",\"stage\":\"");
    append_escaped(&p, end, stage ? stage : "");
    append_raw(&p, end, "\",\"message\":\"");
    append_escaped(&p, end, message ? message : "");
    append_raw(&p, end, "\",\"liveId\":\"");
    append_escaped(&p, end, live_id ? live_id : "");
    append_raw(&p, end, "\",\"objectId\":\"");
    append_escaped(&p, end, object_id ? object_id : "");
    append_raw(&p, end, "\",\"clientMsgId\":\"");
    append_escaped(&p, end, client_msg_id ? client_msg_id : "");
    append_raw(&p, end, "\",\"liveIdentity\":");
    wsprintfA(num, "%d", live_identity);
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"h5Url\":\"");
    append_escaped(&p, end, h5_url ? h5_url : "");
    append_raw(&p, end, "\",\"initAttempted\":");
    append_raw(&p, end, init_attempted ? "true" : "false");
    append_raw(&p, end, ",\"initOk\":");
    append_raw(&p, end, init_ok ? "true" : "false");
    append_raw(&p, end, ",\"joinAttempted\":");
    append_raw(&p, end, join_attempted ? "true" : "false");
    append_raw(&p, end, ",\"joinOk\":");
    append_raw(&p, end, join_ok ? "true" : "false");
    append_raw(&p, end, ",\"postOk\":");
    append_raw(&p, end, post_ok ? "true" : "false");
    append_raw(&p, end, ",\"session\":{\"userverForH5\":");
    if (finder_json_value_available(userver_for_h5)) {
        finder_append_json_value(&p, end, userver_for_h5);
    } else {
        append_raw(&p, end, "null");
    }
    append_raw(&p, end, ",\"liveCookies\":\"");
    append_escaped(&p, end, live_cookies ? live_cookies : "");
    append_raw(&p, end, "\"},\"responseSize\":");
    wsprintfA(num, "%lu", (unsigned long)response_size);
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"responseRet\":");
    if (ret_found) {
        wsprintfA(num, "%ld", response_ret);
        append_raw(&p, end, num);
    } else {
        append_raw(&p, end, "null");
    }
    append_raw(&p, end, ",\"response\":");
    append_raw(&p, end, response_valid ? response_json : "null");
    append_raw(&p, end, "}");
    *p = 0;
    write_file_bytes(result_path, json, (DWORD)(p - json));
    VirtualFree(json, 0, MEM_RELEASE);
    return ok;
}

int finder_post_live_comment_to_result(const char *seq,
                                       const char *content,
                                       const char *live_id,
                                       const char *object_id,
                                       const char *object_nonce_id,
                                       const char *live_cookies,
                                       const char *finder_username,
                                       const char *client_msg_id,
                                       const char *context_id,
                                       const char *entrance_id,
                                       const char *sub_entrance_id,
                                       const char *h5_url,
                                       const char *userver_for_h5,
                                       int live_identity,
                                       int wait_ms) {
    static const char *const userver_paths[] = {
        "$.userver_for_h5", "$.userverForH5",
        "$.data.userver_for_h5", "$.data.userverForH5"
    };
    static const char *const cookie_paths[] = {
        "$.live_cookies", "$.liveCookies",
        "$.data.live_cookies", "$.data.liveCookies"
    };
    static const char *const username_paths[] = {
        "$.myacct[0].username", "$.my_acct[0].username",
        "$.myAcct[0].username", "$.data.myacct[0].username",
        "$.data.my_acct[0].username", "$.data.myAcct[0].username"
    };
    static const char *const live_identity_paths[] = {
        "$.current_alias_role_type", "$.currentAliasRoleType",
        "$.data.current_alias_role_type", "$.data.currentAliasRoleType"
    };
    static const char *const detail_object_id_paths[] = {
        "$.object.id", "$.data.object.id"
    };
    static const char *const detail_nonce_paths[] = {
        "$.object.object_nonce_id", "$.object.objectNonceId",
        "$.data.object.object_nonce_id", "$.data.object.objectNonceId"
    };
    static const char *const detail_live_id_paths[] = {
        "$.object.live_info.live_id", "$.object.liveInfo.liveId",
        "$.data.object.live_info.live_id", "$.data.object.liveInfo.liveId"
    };
    char effective_client_msg_id[512];
    char effective_h5_url[8192];
    char resolved_object_id[64];
    char resolved_object_nonce_id[4096];
    char resolved_live_id[64];
    char stage_seq[96];
    char request_json[32768];
    char *normalized_content = NULL;
    char *effective_userver = NULL;
    char *effective_cookies = NULL;
    char *init_finder_username = NULL;
    const char *effective_finder_username = NULL;
    const char *post_finder_username = NULL;
    FinderH5Response response;
    int init_attempted = 0;
    int init_ok = 0;
    int join_attempted = 0;
    int join_ok = 0;
    int post_ok = 0;
    int transport_ok;
    int result;
    int init_required;
    int resolve_required;
    int response_live_identity = 0;
    if (!seq || !seq[0]) return 0;
    ZeroMemory(&response, sizeof(response));
    effective_client_msg_id[0] = 0;
    effective_h5_url[0] = 0;
    resolved_object_id[0] = 0;
    resolved_object_nonce_id[0] = 0;
    resolved_live_id[0] = 0;
    if (!live_id || !live_id[0] || !object_id || !object_id[0]) {
        return finder_write_live_comment_result(seq, 0, "validation",
                                                "missing liveId or objectId",
                                                live_id, object_id, client_msg_id, h5_url,
                                                live_identity, 0, 0, 0, 0, 0,
                                                userver_for_h5, live_cookies, NULL);
    }
    if (!finder_is_valid_object_id(object_id) || !finder_is_decimal_object_id(live_id)) {
        return finder_write_live_comment_result(seq, 0, "validation",
                                                "invalid finder live id",
                                                live_id, object_id, client_msg_id, h5_url,
                                                live_identity, 0, 0, 0, 0, 0,
                                                userver_for_h5, live_cookies, NULL);
    }
    if (!finder_normalize_live_comment(content, &normalized_content, NULL)) {
        return finder_write_live_comment_result(seq, 0, "validation",
                                                "comment must be non-empty and at most 60 characters",
                                                live_id, object_id, client_msg_id, h5_url,
                                                live_identity, 0, 0, 0, 0, 0,
                                                userver_for_h5, live_cookies, NULL);
    }
    if (!finder_live_comment_input_lengths_ok(live_cookies, finder_username,
                                              client_msg_id, object_nonce_id,
                                              context_id, entrance_id,
                                              sub_entrance_id, userver_for_h5)) {
        free(normalized_content);
        return finder_write_live_comment_result(seq, 0, "validation",
                                                "finder live comment field too long",
                                                live_id, object_id, client_msg_id, h5_url,
                                                live_identity, 0, 0, 0, 0, 0,
                                                userver_for_h5, live_cookies, NULL);
    }
    if (live_identity < 1 || live_identity > 4) live_identity = 0;
    effective_finder_username =
        (finder_username && finder_username[0]) ? finder_username : NULL;
    if (client_msg_id && client_msg_id[0]) {
        safe_copy(effective_client_msg_id, sizeof(effective_client_msg_id), client_msg_id);
    }
    if (h5_url && h5_url[0]) {
        if (!finder_is_valid_live_url(h5_url)) {
            free(normalized_content);
            return finder_write_live_comment_result(seq, 0, "validation",
                                                    "invalid finder live h5 url",
                                                    live_id, object_id,
                                                    effective_client_msg_id, h5_url,
                                                    live_identity, 0, 0, 0, 0, 0,
                                                    userver_for_h5, live_cookies, NULL);
        }
        if (finder_is_export_object_id(object_id) &&
            strstr(h5_url, "?eid=") == NULL && strstr(h5_url, "&eid=") == NULL) {
            if (!finder_build_live_h5_url(effective_h5_url, sizeof(effective_h5_url),
                                          object_id, object_nonce_id, context_id,
                                          entrance_id, sub_entrance_id)) {
                free(normalized_content);
                return finder_write_live_comment_result(
                    seq, 0, "validation", "unable to build finder live URL",
                    live_id, object_id, effective_client_msg_id, "", live_identity,
                    0, 0, 0, 0, 0, userver_for_h5, live_cookies, NULL);
            }
        } else {
            safe_copy(effective_h5_url, sizeof(effective_h5_url), h5_url);
        }
    } else if (!finder_build_live_h5_url(effective_h5_url, sizeof(effective_h5_url),
                                         object_id, object_nonce_id, context_id,
                                         entrance_id, sub_entrance_id)) {
        free(normalized_content);
        return finder_write_live_comment_result(seq, 0, "validation",
                                                "unable to build finder live URL",
                                                live_id, object_id,
                                                effective_client_msg_id, "",
                                                live_identity, 0, 0, 0, 0, 0,
                                                userver_for_h5, live_cookies, NULL);
    }

    resolve_required = finder_is_export_object_id(object_id) ||
                       ((!live_cookies || !live_cookies[0]) &&
                        !finder_nonce_is_join_ready(object_nonce_id));
    if (resolve_required) {
        char *detail_object_id = NULL;
        char *detail_nonce_id = NULL;
        char *detail_live_id = NULL;
        int detail_ok;
        if (!finder_build_comment_detail_request(object_id, object_nonce_id,
                                                 context_id, entrance_id,
                                                 sub_entrance_id, request_json,
                                                 sizeof(request_json))) {
            free(normalized_content);
            return finder_write_live_comment_result(
                seq, 0, "resolve", "finder live detail request too large",
                live_id, object_id, effective_client_msg_id, effective_h5_url,
                live_identity, 0, 0, 0, 0, 0, userver_for_h5, live_cookies, NULL);
        }
        wsprintfA(stage_seq, "%s_d", seq);
        transport_ok = h5ext_transfer_to_result(
            stage_seq, FINDER_GET_COMMENT_DETAIL_CGI,
            FINDER_GET_COMMENT_DETAIL_CMD_ID, request_json, FINDER_LIVE_SCOPE,
            wait_ms, 400, effective_h5_url, 0, "");
        finder_load_h5_response(stage_seq, &response);
        detail_ok = finder_response_ok(transport_ok, &response);
        if (detail_ok) {
            detail_object_id = finder_json_first_string(
                &response, detail_object_id_paths,
                (int)(sizeof(detail_object_id_paths) /
                      sizeof(detail_object_id_paths[0])));
            detail_nonce_id = finder_json_first_string(
                &response, detail_nonce_paths,
                (int)(sizeof(detail_nonce_paths) / sizeof(detail_nonce_paths[0])));
            detail_live_id = finder_json_first_string(
                &response, detail_live_id_paths,
                (int)(sizeof(detail_live_id_paths) / sizeof(detail_live_id_paths[0])));
            detail_ok = detail_object_id && detail_nonce_id && detail_live_id &&
                        finder_is_decimal_object_id(detail_object_id) &&
                        finder_is_decimal_object_id(detail_live_id) &&
                        lstrlenA(detail_object_id) < (int)sizeof(resolved_object_id) &&
                        lstrlenA(detail_nonce_id) <
                            (int)sizeof(resolved_object_nonce_id) &&
                        lstrlenA(detail_live_id) < (int)sizeof(resolved_live_id) &&
                        ((live_cookies && live_cookies[0]) ||
                         finder_nonce_is_join_ready(detail_nonce_id));
        }
        if (!detail_ok) {
            result = finder_write_live_comment_result(
                seq, 0, "resolve", "finder live detail resolution failed",
                live_id, object_id, effective_client_msg_id, effective_h5_url,
                live_identity, 0, 0, 0, 0, 0, userver_for_h5, live_cookies,
                &response);
            if (detail_object_id) free(detail_object_id);
            if (detail_nonce_id) free(detail_nonce_id);
            if (detail_live_id) free(detail_live_id);
            finder_free_h5_response(&response);
            free(normalized_content);
            return result;
        }
        safe_copy(resolved_object_id, sizeof(resolved_object_id), detail_object_id);
        safe_copy(resolved_object_nonce_id, sizeof(resolved_object_nonce_id),
                  detail_nonce_id);
        safe_copy(resolved_live_id, sizeof(resolved_live_id), detail_live_id);
        free(detail_object_id);
        free(detail_nonce_id);
        free(detail_live_id);
        finder_free_h5_response(&response);
        object_id = resolved_object_id;
        object_nonce_id = resolved_object_nonce_id;
        live_id = resolved_live_id;
    }
    if ((!live_cookies || !live_cookies[0]) &&
        !finder_nonce_is_join_ready(object_nonce_id)) {
        free(normalized_content);
        return finder_write_live_comment_result(seq, 0, "validation",
                                                "a resolved objectNonceId is required to join the live room",
                                                live_id, object_id,
                                                effective_client_msg_id, effective_h5_url,
                                                live_identity, 0, 0, 0, 0, 0,
                                                userver_for_h5, live_cookies, NULL);
    }

    init_required = !finder_json_value_available(userver_for_h5) ||
                    (live_identity == 3 && !effective_finder_username);

    if (finder_json_value_available(userver_for_h5)) {
        effective_userver = finder_heap_copy(userver_for_h5);
        init_ok = effective_userver != NULL;
    }
    if (init_required) {
        init_attempted = 1;
        if (!finder_build_init_request(context_id, entrance_id, sub_entrance_id,
                                       request_json, sizeof(request_json))) {
            free(normalized_content);
            if (effective_userver) free(effective_userver);
            return finder_write_live_comment_result(seq, 0, "init",
                                                    "finder init request too large",
                                                    live_id, object_id,
                                                    effective_client_msg_id, effective_h5_url,
                                                    live_identity, 1, 0, 0, 0, 0,
                                                    "", live_cookies, NULL);
        }
        wsprintfA(stage_seq, "%s_i", seq);
        transport_ok = h5ext_transfer_to_result(stage_seq, FINDER_INIT_CGI,
                                                FINDER_INIT_CMD_ID, request_json,
                                                FINDER_LIVE_SCOPE, wait_ms, 400,
                                                effective_h5_url, 0, "");
        finder_load_h5_response(stage_seq, &response);
        init_ok = finder_response_ok(transport_ok, &response);
        if (init_ok && !effective_userver) {
            effective_userver = finder_json_first_raw(
                &response, userver_paths,
                (int)(sizeof(userver_paths) / sizeof(userver_paths[0])));
        }
        if (init_ok && !effective_finder_username) {
            init_finder_username = finder_json_first_string(
                &response, username_paths,
                (int)(sizeof(username_paths) / sizeof(username_paths[0])));
            if (init_finder_username && init_finder_username[0] &&
                lstrlenA(init_finder_username) <= 256) {
                effective_finder_username = init_finder_username;
            }
        }
        if (init_ok && live_identity == 0) {
            response_live_identity = finder_json_first_live_identity(
                &response, live_identity_paths,
                (int)(sizeof(live_identity_paths) / sizeof(live_identity_paths[0])));
            if (response_live_identity >= 1 && response_live_identity <= 4) {
                live_identity = response_live_identity;
            }
        }
        init_ok = init_ok && finder_json_value_available(effective_userver) &&
                  (live_identity != 3 ||
                   (effective_finder_username && effective_finder_username[0]));
        if (!init_ok) {
            result = finder_write_live_comment_result(
                seq, 0, "init", "finder init failed or returned incomplete session",
                live_id, object_id, effective_client_msg_id, effective_h5_url,
                live_identity, 1, 0, 0, 0, 0, effective_userver, live_cookies,
                &response);
            finder_free_h5_response(&response);
            if (init_finder_username) free(init_finder_username);
            if (effective_userver) free(effective_userver);
            free(normalized_content);
            return result;
        }
        finder_free_h5_response(&response);
    }
    if (!effective_userver) {
        if (init_finder_username) free(init_finder_username);
        free(normalized_content);
        return finder_write_live_comment_result(seq, 0, "init",
                                                "finder userver allocation failed",
                                                live_id, object_id,
                                                effective_client_msg_id, effective_h5_url,
                                                live_identity, init_attempted, 0,
                                                0, 0, 0, "", live_cookies, NULL);
    }

    if (live_identity < 1 || live_identity > 4) {
        live_identity = (finder_username && finder_username[0]) ? 3 : 1;
    }
    if (live_identity == 3 &&
        (!effective_finder_username || !effective_finder_username[0])) {
        result = finder_write_live_comment_result(
            seq, 0, "init", "finder identity requires an account username",
            live_id, object_id, effective_client_msg_id, effective_h5_url,
            live_identity, init_attempted, 0, 0, 0, 0,
            effective_userver, live_cookies, NULL);
        free(effective_userver);
        if (init_finder_username) free(init_finder_username);
        free(normalized_content);
        return result;
    }
    if (!effective_client_msg_id[0]) {
        const char *client_username =
            (effective_finder_username && effective_finder_username[0])
                ? effective_finder_username
                : "undefined";
        LONG unique_suffix = InterlockedIncrement(&g_finder_live_client_msg_seed);
        wsprintfA(effective_client_msg_id, "pc_%s_%lu_%lu_%ld",
                  client_username,
                  (unsigned long)GetCurrentProcessId(),
                  (unsigned long)GetTickCount(), unique_suffix);
    }
    if (live_identity == 3 && effective_finder_username) {
        post_finder_username = effective_finder_username;
    }

    if (live_cookies && live_cookies[0]) {
        effective_cookies = finder_heap_copy(live_cookies);
        join_ok = effective_cookies != NULL;
    } else {
        join_attempted = 1;
        if (!finder_build_join_request(live_id, object_id, object_nonce_id,
                                       effective_finder_username, effective_userver,
                                       context_id, entrance_id, sub_entrance_id,
                                       live_identity, request_json,
                                       sizeof(request_json))) {
            result = finder_write_live_comment_result(
                seq, 0, "join", "finder join live request too large",
                live_id, object_id, effective_client_msg_id, effective_h5_url,
                live_identity, init_attempted, init_ok, 1, 0, 0,
                effective_userver, "", NULL);
            free(effective_userver);
            if (init_finder_username) free(init_finder_username);
            free(normalized_content);
            return result;
        }
        wsprintfA(stage_seq, "%s_j", seq);
        transport_ok = h5ext_transfer_to_result(stage_seq,
                                                FINDER_JOIN_LIVE_CGI,
                                                FINDER_JOIN_LIVE_CMD_ID,
                                                request_json, FINDER_LIVE_SCOPE,
                                                wait_ms, 400, effective_h5_url,
                                                0, "");
        finder_load_h5_response(stage_seq, &response);
        join_ok = finder_response_ok(transport_ok, &response);
        if (join_ok) {
            effective_cookies = finder_json_first_string(
                &response, cookie_paths,
                (int)(sizeof(cookie_paths) / sizeof(cookie_paths[0])));
            join_ok = effective_cookies && effective_cookies[0];
        }
        if (!join_ok) {
            result = finder_write_live_comment_result(
                seq, 0, "join", "finder join live failed or returned no live_cookies",
                live_id, object_id, effective_client_msg_id, effective_h5_url,
                live_identity, init_attempted, init_ok, 1, 0, 0,
                effective_userver, effective_cookies, &response);
            finder_free_h5_response(&response);
            if (effective_cookies) free(effective_cookies);
            free(effective_userver);
            if (init_finder_username) free(init_finder_username);
            free(normalized_content);
            return result;
        }
        finder_free_h5_response(&response);
    }
    if (!effective_cookies) {
        result = finder_write_live_comment_result(
            seq, 0, "join", "finder live cookies allocation failed",
            live_id, object_id, effective_client_msg_id, effective_h5_url,
            live_identity, init_attempted, init_ok, join_attempted, 0, 0,
            effective_userver, "", NULL);
        free(effective_userver);
        if (init_finder_username) free(init_finder_username);
        free(normalized_content);
        return result;
    }

    if (!finder_build_live_comment_request(normalized_content, live_id, object_id,
                                           effective_cookies, post_finder_username,
                                           effective_client_msg_id, context_id,
                                           entrance_id, sub_entrance_id,
                                           effective_userver, live_identity,
                                           request_json, sizeof(request_json))) {
        result = finder_write_live_comment_result(
            seq, 0, "post", "finder live comment request too large",
            live_id, object_id, effective_client_msg_id, effective_h5_url,
            live_identity, init_attempted, init_ok, join_attempted, join_ok, 0,
            effective_userver, effective_cookies, NULL);
        free(effective_cookies);
        free(effective_userver);
        if (init_finder_username) free(init_finder_username);
        free(normalized_content);
        return result;
    }
    wsprintfA(stage_seq, "%s_p", seq);
    transport_ok = h5ext_transfer_to_result(stage_seq,
                                            FINDER_POST_LIVE_MSG_CGI,
                                            FINDER_POST_LIVE_MSG_CMD_ID,
                                            request_json, FINDER_LIVE_SCOPE,
                                            wait_ms, 400, effective_h5_url,
                                            0, "");
    finder_load_h5_response(stage_seq, &response);
    post_ok = finder_response_ok(transport_ok, &response);
    result = finder_write_live_comment_result(
        seq, post_ok, "post",
        post_ok ? "finder live comment ok" : "finder live comment failed",
        live_id, object_id, effective_client_msg_id, effective_h5_url,
        live_identity, init_attempted, init_ok, join_attempted, join_ok, post_ok,
        effective_userver, effective_cookies, &response);
    finder_free_h5_response(&response);
    free(effective_cookies);
    free(effective_userver);
    if (init_finder_username) free(init_finder_username);
    free(normalized_content);
    return result;
}

/*
 * The old FinderUserPagePreview model (CGI 6611) is not the profile/feed
 * endpoint used by the current Web Finder page.  The page calls H5ExtTransfer
 * with pc_finderuserpage (cmd 6624), whose payload contains contact/profile
 * data and the feed object list.  Keep this path separate from the legacy
 * model probe so its transport and business status cannot be conflated.
 */
static int finder_build_userpage_h5_request(const char *username,
                                            const char *object_id,
                                            const char *context_id,
                                            const char *entrance_id,
                                            const char *finder_username,
                                            int scene,
                                            char *out,
                                            int out_max) {
    char request_id[32];
    char report_buffer[1024];
    char scene_num[32];
    char *p = out;
    char *end = out ? out + out_max - 1 : NULL;
    const char *effective_entrance =
        (entrance_id && entrance_id[0]) ? entrance_id : "1001";
    if (!out || out_max < 2 || !username || !username[0]) return 0;
    if (scene <= 0) scene = 140;
    finder_make_detail_request_id(request_id, sizeof(request_id));
    if (!request_id[0]) return 0;

    p = report_buffer;
    end = report_buffer + sizeof(report_buffer) - 1;
    append_raw(&p, end, "{\"entranceId\":\"");
    append_escaped(&p, end, effective_entrance);
    append_raw(&p, end, "\"}");
    *p = 0;

    p = out;
    end = out + out_max - 1;
    append_raw(&p, end, "{\"finder_basereq\":{\"request_id\":\"");
    append_escaped(&p, end, request_id);
    append_raw(&p, end, "\",\"scene\":");
    wsprintfA(scene_num, "%d", scene);
    append_raw(&p, end, scene_num);
    append_raw(&p, end, ",\"ctx_info\":{");
    if (context_id && context_id[0]) {
        append_raw(&p, end, "\"context_id\":\"");
        append_escaped(&p, end, context_id);
        append_raw(&p, end, "\",");
    }
    append_raw(&p, end, "\"client_report_buff\":\"");
    append_escaped(&p, end, report_buffer);
    append_raw(&p, end, "\"}},\"username\":\"");
    append_escaped(&p, end, username);
    if (object_id && object_id[0]) {
        append_raw(&p, end, "\",\"object_id\":\"");
        append_escaped(&p, end, object_id);
    }
    append_raw(&p, end, "\",\"finder_username\":\"");
    append_escaped(&p, end, finder_username ? finder_username : "");
    append_raw(&p, end, "\"}");
    *p = 0;
    return p < end;
}

static int finder_userpage_h5_business_ok(int transport_ok,
                                          const FinderH5Response *response) {
    static const char *const outer_ret_paths[] = {
        "$.base_resp.ret", "$.base_response.ret", "$.baseResponse.ret",
        "$.jsapi_resp.ret", "$.jsapiResp.ret", "$.ret", "$.Ret",
        "$.err_code", "$.errCode"
    };
    struct mg_str outer;
    if (!transport_ok || !response || !response->file_json ||
        response->file_size == 0) return 0;
    outer = mg_str_n(response->file_json, response->file_size);
    if (!finder_json_rets_ok(
            outer, outer_ret_paths,
            (int)(sizeof(outer_ret_paths) / sizeof(outer_ret_paths[0])))) {
        return 0;
    }
    return finder_response_ok(1, response);
}

static SIZE_T finder_userpage_json_string_cost(const char *value) {
    SIZE_T len = value ? (SIZE_T)lstrlenA(value) : 0;
    return len * 2 + 32;
}

static void finder_userpage_append_profile(char **p,
                                           char *end,
                                           const char *contact,
                                           const char *username) {
    const char *s = contact;
    while (s && (*s == ' ' || *s == '\t' || *s == '\r' || *s == '\n')) s++;
    if (s && s[0] == '{') {
        /* Preserve the full contact object while retaining the old parsed flag. */
        append_raw(p, end, "{\"parsed\":true");
        if (s[1] != '}') append_raw(p, end, ",");
        append_raw(p, end, s + 1);
        return;
    }
    append_raw(p, end, "{\"parsed\":false,\"username\":\"");
    append_escaped(p, end, username ? username : "");
    append_raw(p, end, "\"}");
}

static int finder_write_userpage_h5_result(const char *seq,
                                           int transport_ok,
                                           const char *username,
                                           const char *object_id,
                                           const char *context_id,
                                           const char *entrance_id,
                                           const char *finder_username,
                                           int scene,
                                           int wait_ms) {
    static const char *const contact_paths[] = {"$.contact", "$.Contact"};
    static const char *const finder_info_paths[] = {
        "$.finder_user_info", "$.finderUserInfo"
    };
    static const char *const feeds_paths[] = {"$.object", "$.objects", "$.feeds"};
    static const char *const feed_count_paths[] = {"$.feeds_count", "$.feedsCount"};
    static const char *const continue_paths[] = {"$.continue_flag", "$.continueFlag"};
    static const char *const cursor_paths[] = {"$.last_buffer", "$.lastBuffer"};
    static const char *const tags_paths[] = {"$.user_tags", "$.userTags"};
    WCHAR result_path[1024];
    WCHAR response_path[1024];
    char response_path_utf8[2048];
    char *response_json = NULL;
    char *contact = NULL;
    char *finder_info = NULL;
    char *feeds = NULL;
    char *feed_count = NULL;
    char *continue_flag = NULL;
    char *cursor = NULL;
    char *tags = NULL;
    const char *payload;
    DWORD response_size = 0;
    SIZE_T cap;
    char *json;
    char *p;
    char *end;
    char num[64];
    int business_ok;
    int response_valid;
    int has_feeds;
    int has_profile;
    int data_available;
    int result_ok;
    const long missing_ret = 0x7fffffffL;
    long jsapi_ret = 0x7fffffffL;
    long base_ret = 0x7fffffffL;
    FinderH5Response response;

    ZeroMemory(&response, sizeof(response));
    wsprintfW(result_path, L"%s\\result_%S.json", CONTROL_DIR, seq ? seq : "");
    wsprintfW(response_path, L"%s\\h5ext_%S.json", CONTROL_DIR, seq ? seq : "");
    response_path_utf8[0] = 0;
    WideCharToMultiByte(CP_UTF8, 0, response_path, -1,
                        response_path_utf8, sizeof(response_path_utf8), NULL, NULL);

    if (finder_load_h5_response(seq, &response)) {
        response_json = response.payload_json;
        response_size = response.file_size;
        payload = finder_skip_json_space(response_json, response.payload_size);
    } else {
        payload = "";
    }
    response_valid = response_json && response.payload_size > 0 &&
                     finder_json_value_start(payload[0]);
    business_ok = finder_userpage_h5_business_ok(transport_ok, &response);

    if (response.file_json && response.file_size > 0) {
        struct mg_str outer = mg_str_n(response.file_json, response.file_size);
        jsapi_ret = mg_json_get_long(outer, "$.jsapi_resp.ret", missing_ret);
        if (jsapi_ret == missing_ret) {
            jsapi_ret = mg_json_get_long(outer, "$.jsapiResp.ret", missing_ret);
        }
    }
    if (response_valid) {
        struct mg_str inner = mg_str_n(response.payload_json, response.payload_size);
        base_ret = mg_json_get_long(inner, "$.BaseResponse.Ret", missing_ret);
        if (base_ret == missing_ret) {
            base_ret = mg_json_get_long(inner, "$.base_response.ret", missing_ret);
        }
    }

    if (response_valid) {
        contact = finder_json_first_raw(&response, contact_paths,
                                        (int)(sizeof(contact_paths) /
                                              sizeof(contact_paths[0])));
        finder_info = finder_json_first_raw(&response, finder_info_paths,
                                            (int)(sizeof(finder_info_paths) /
                                                  sizeof(finder_info_paths[0])));
        feeds = finder_json_first_raw(&response, feeds_paths,
                                      (int)(sizeof(feeds_paths) /
                                            sizeof(feeds_paths[0])));
        feed_count = finder_json_first_raw(&response, feed_count_paths,
                                           (int)(sizeof(feed_count_paths) /
                                                 sizeof(feed_count_paths[0])));
        continue_flag = finder_json_first_raw(&response, continue_paths,
                                              (int)(sizeof(continue_paths) /
                                                    sizeof(continue_paths[0])));
        cursor = finder_json_first_raw(&response, cursor_paths,
                                       (int)(sizeof(cursor_paths) /
                                             sizeof(cursor_paths[0])));
        tags = finder_json_first_raw(&response, tags_paths,
                                     (int)(sizeof(tags_paths) /
                                           sizeof(tags_paths[0])));
    }
    has_feeds = feeds && feeds[0] == '[' && feeds[1] != ']';
    has_profile = contact && contact[0] == '{';
    data_available = business_ok && (has_feeds || has_profile);
    result_ok = business_ok && data_available;

    cap = (SIZE_T)(response.file_size ? response.file_size : 0) * 2 + 65536;
    cap += finder_userpage_json_string_cost(username) + finder_userpage_json_string_cost(object_id);
    cap += finder_userpage_json_string_cost(context_id) + finder_userpage_json_string_cost(entrance_id);
    cap += finder_userpage_json_string_cost(finder_username) + finder_userpage_json_string_cost(response_path_utf8);
    if (cap < 131072) cap = 131072;
    if (cap > FINDER_USERPAGE_H5_MAX_RESPONSE * 2) {
        cap = FINDER_USERPAGE_H5_MAX_RESPONSE * 2;
    }
    json = (char *)VirtualAlloc(NULL, cap, MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE);
    if (!json) {
        if (contact) free(contact);
        if (finder_info) free(finder_info);
        if (feeds) free(feeds);
        if (feed_count) free(feed_count);
        if (continue_flag) free(continue_flag);
        if (cursor) free(cursor);
        if (tags) free(tags);
        finder_free_h5_response(&response);
        return 0;
    }
    p = json;
    end = json + cap - 1;
    append_raw(&p, end, "{\"ok\":");
    append_raw(&p, end, result_ok ? "true" : "false");
    append_raw(&p, end, ",\"transportOk\":");
    append_raw(&p, end, transport_ok ? "true" : "false");
    append_raw(&p, end, ",\"businessOk\":");
    append_raw(&p, end, business_ok ? "true" : "false");
    append_raw(&p, end, ",\"seq\":\"");
    append_escaped(&p, end, seq ? seq : "");
    append_raw(&p, end, "\",\"command\":\"finder_userpage_h5\",\"message\":\"");
    append_raw(&p, end, result_ok ? "finder_userpage h5 ok" :
               (transport_ok ? (business_ok ? "finder_userpage h5 no usable data" :
                                             "finder_userpage h5 business failed") :
                                "finder_userpage h5 transport failed"));
    append_raw(&p, end, "\",\"source\":\"pc_finderuserpage\",\"cmdId\":6624");
    append_raw(&p, end, ",\"username\":\"");
    append_escaped(&p, end, username ? username : "");
    append_raw(&p, end, "\",\"objectId\":\"");
    append_escaped(&p, end, object_id ? object_id : "");
    append_raw(&p, end, "\",\"contextId\":\"");
    append_escaped(&p, end, context_id ? context_id : "");
    append_raw(&p, end, "\",\"entranceId\":\"");
    append_escaped(&p, end, entrance_id && entrance_id[0] ? entrance_id : "1001");
    append_raw(&p, end, "\",\"scene\":");
    wsprintfA(num, "%d", scene);
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"finderUsername\":\"");
    append_escaped(&p, end, finder_username ? finder_username : "");
    append_raw(&p, end, "\",\"waitMs\":");
    wsprintfA(num, "%d", wait_ms);
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"responseSize\":");
    wsprintfA(num, "%lu", (unsigned long)response_size);
    append_raw(&p, end, num);
    append_raw(&p, end, ",\"ret\":{\"jsapi\":");
    if (jsapi_ret == missing_ret) append_raw(&p, end, "null");
    else {
        wsprintfA(num, "%ld", jsapi_ret);
        append_raw(&p, end, num);
    }
    append_raw(&p, end, ",\"base\":");
    if (base_ret == missing_ret) append_raw(&p, end, "null");
    else {
        wsprintfA(num, "%ld", base_ret);
        append_raw(&p, end, num);
    }
    append_raw(&p, end, "}");
    append_raw(&p, end, ",\"responseFile\":\"");
    append_escaped(&p, end, response_path_utf8);
    append_raw(&p, end, "\",\"response\":");
    append_raw(&p, end, response_valid ? payload : "null");
    append_raw(&p, end, ",\"homepage\":{\"username\":\"");
    append_escaped(&p, end, username ? username : "");
    append_raw(&p, end, "\",\"profile\":");
    finder_userpage_append_profile(&p, end, contact, username);
    append_raw(&p, end, ",\"finderUserInfo\":");
    append_raw(&p, end, finder_info ? finder_info : "{}");
    append_raw(&p, end, ",\"feeds\":");
    append_raw(&p, end, feeds && feeds[0] == '[' ? feeds : "[]");
    append_raw(&p, end, ",\"feedsCount\":");
    finder_append_json_value(&p, end, feed_count ? feed_count : "0");
    append_raw(&p, end, ",\"cursor\":");
    finder_append_json_value(&p, end, cursor ? cursor : "\"\"");
    append_raw(&p, end, ",\"hasMore\":");
    append_raw(&p, end, continue_flag &&
               (continue_flag[0] == '1' || continue_flag[0] == '2' ||
                continue_flag[0] == '3') ? "true" : "false");
    append_raw(&p, end, ",\"userTags\":");
    append_raw(&p, end, tags && tags[0] == '[' ? tags : "[]");
    append_raw(&p, end, ",\"transportOk\":");
    append_raw(&p, end, transport_ok ? "true" : "false");
    append_raw(&p, end, ",\"lowerTransportOk\":");
    append_raw(&p, end, transport_ok ? "true" : "false");
    append_raw(&p, end, ",\"outerTransportOk\":false,\"dataAvailable\":");
    append_raw(&p, end, data_available ? "true" : "false");
    /* Close homepage and the result payload; reply_raw adds the API envelope. */
    append_raw(&p, end, "}}");
    *p = 0;
    write_file_bytes(result_path, json, (DWORD)(p - json));
    VirtualFree(json, 0, MEM_RELEASE);
    if (contact) free(contact);
    if (finder_info) free(finder_info);
    if (feeds) free(feeds);
    if (feed_count) free(feed_count);
    if (continue_flag) free(continue_flag);
    if (cursor) free(cursor);
    if (tags) free(tags);
    finder_free_h5_response(&response);
    return result_ok;
}

int finder_userpage_h5_to_result(const char *seq,
                                 const char *username,
                                 const char *object_id,
                                 const char *context_id,
                                 const char *entrance_id,
                                 const char *finder_username,
                                 const char *h5_url,
                                 int scene,
                                 int wait_ms) {
    char request_json[16384];
    int transport_ok;
    const char *effective_h5_url =
        (h5_url && h5_url[0]) ? h5_url : FINDER_USERPAGE_H5_URL;
    if (scene <= 0) scene = 140;
    if (wait_ms <= 0) wait_ms = 15000;
    if (wait_ms > 60000) wait_ms = 60000;
    if (!seq || !seq[0] || !username || !username[0]) {
        return finder_write_userpage_h5_result(seq, 0, username, object_id,
                                               context_id, entrance_id,
                                               finder_username, scene, wait_ms);
    }
    if (!finder_build_userpage_h5_request(username, object_id, context_id,
                                          entrance_id, finder_username, scene,
                                          request_json, sizeof(request_json))) {
        return finder_write_userpage_h5_result(seq, 0, username, object_id,
                                               context_id, entrance_id,
                                               finder_username, scene, wait_ms);
    }
    transport_ok = h5ext_transfer_to_result(
        seq, FINDER_USERPAGE_H5_CGI, FINDER_USERPAGE_H5_CMD_ID, request_json,
        FINDER_USERPAGE_H5_SCOPE, wait_ms, 400, effective_h5_url, 0, "");
    return finder_write_userpage_h5_result(seq, transport_ok, username, object_id,
                                           context_id, entrance_id,
                                           finder_username, scene, wait_ms);
}
