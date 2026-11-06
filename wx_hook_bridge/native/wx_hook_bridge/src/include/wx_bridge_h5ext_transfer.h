#ifndef WX_BRIDGE_H5EXT_TRANSFER_H
#define WX_BRIDGE_H5EXT_TRANSFER_H

int h5ext_transfer_to_result(const char *seq,
                             const char *cmd_url,
                             int cmd_id,
                             const char *req_json,
                             const char *scope,
                             int wait_ms,
                             int jsapi_control_byte,
                             const char *h5_url,
                             int report_security,
                             const char *transfer_cgi_url);

#endif /* WX_BRIDGE_H5EXT_TRANSFER_H */
