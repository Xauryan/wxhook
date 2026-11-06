#ifndef WX_BRIDGE_SNS_PUBLISH_H
#define WX_BRIDGE_SNS_PUBLISH_H

int publish_sns_text_to_result(const char *seq, const char *content, int wait_ms);
int publish_sns_image_to_result(const char *seq, const char *content, const char *image_path,
                                const char *md5, int width, int height, int wait_ms,
                                const char *cdn_url, const char *thumb_url);
int publish_sns_video_to_result(const char *seq, const char *content, const char *video_url,
                                const char *md5, int width, int height, int wait_ms,
                                const char *thumb_url, int duration_ms);
void wxb_refresh_sns_publish_trace_hooks(void);
void wxb_uninstall_sns_publish_trace_hooks(void);

#define refresh_sns_publish_trace_hooks wxb_refresh_sns_publish_trace_hooks
#define uninstall_sns_publish_trace_hooks wxb_uninstall_sns_publish_trace_hooks

#endif /* WX_BRIDGE_SNS_PUBLISH_H */
