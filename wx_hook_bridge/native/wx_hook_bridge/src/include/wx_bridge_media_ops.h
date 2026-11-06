#ifndef WX_BRIDGE_MEDIA_OPS_H
#define WX_BRIDGE_MEDIA_OPS_H

#include "wx_bridge_platform.h"

int64_t wxb_download_image_internal(const char *msgid, const char *fileid,
                                    const char *aeskey, const char *savepath,
                                    int64_t filelen, int mode, int app_type,
                                    int file_type);
int wxb_download_image_wechat_probe_to_result(const char *seq, const char *msgid,
                                               int dl_type, int mode,
                                               int flags,
                                               int use_send_item, int wait_ms);
int wxb_download_image_task_probe_to_result(const char *seq, const char *msgid,
                                            int wait_ms,
                                            const char *content,
                                            int source_type,
                                            int source_subtype,
                                            int source_local_id,
                                            int32_t source_time,
                                            const char *source_from,
                                            const char *source_to,
                                            const char *source_sender,
                                            const char *source_msgsource);
int wxb_download_sns_media_url_to_result(const char *seq, const char *url,
                                         const char *suffix, int wait_ms);
int wxb_download_sns_media_task_to_result(const char *seq, const char *url,
                                          const char *filekey,
                                          const char *md5,
                                          const char *savepath,
                                          int64_t total_size,
                                          int wait_ms);
int wxb_download_sns_media_full_task_to_result(const char *seq, const char *url,
                                               const char *filekey,
                                               const char *md5,
                                               const char *aeskey,
                                               const char *savepath,
                                               int64_t total_size,
                                               int task_type,
                                               int wait_ms);
int wxb_decode_image_internal(const char *input_path, const char *output_path);
void wxb_refresh_image_task_trace_hooks(void);
void wxb_uninstall_image_task_trace_hooks(void);
void wxb_refresh_sns_media_probe_hook(void);
void wxb_uninstall_sns_media_probe_hook(void);
void wxb_install_sns_upload_support_hooks(void);
void wxb_refresh_sns_upload_trace_hooks(void);
void wxb_uninstall_sns_upload_trace_hooks(void);

#define download_image_internal wxb_download_image_internal
#define download_image_wechat_probe_to_result wxb_download_image_wechat_probe_to_result
#define download_image_task_probe_to_result wxb_download_image_task_probe_to_result
#define download_sns_media_url_to_result wxb_download_sns_media_url_to_result
#define download_sns_media_task_to_result wxb_download_sns_media_task_to_result
#define download_sns_media_full_task_to_result wxb_download_sns_media_full_task_to_result
#define decode_image_internal wxb_decode_image_internal
#define refresh_image_task_trace_hooks wxb_refresh_image_task_trace_hooks
#define uninstall_image_task_trace_hooks wxb_uninstall_image_task_trace_hooks
#define refresh_sns_media_probe_hook wxb_refresh_sns_media_probe_hook
#define uninstall_sns_media_probe_hook wxb_uninstall_sns_media_probe_hook
#define install_sns_upload_support_hooks wxb_install_sns_upload_support_hooks
#define refresh_sns_upload_trace_hooks wxb_refresh_sns_upload_trace_hooks
#define uninstall_sns_upload_trace_hooks wxb_uninstall_sns_upload_trace_hooks

#endif /* WX_BRIDGE_MEDIA_OPS_H */
