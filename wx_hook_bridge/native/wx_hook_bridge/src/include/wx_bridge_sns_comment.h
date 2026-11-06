#ifndef WX_BRIDGE_SNS_COMMENT_H
#define WX_BRIDGE_SNS_COMMENT_H

#include "wx_bridge_platform.h"

int sns_comment_to_result(const char *seq,
                          const char *sns_id,
                          const char *username,
                          const char *content,
                          const char *reply_username,
                          const char *reply_nickname,
                          const char *reply_comment_id,
                          const char *reply_comment_64id,
                          int reply_source,
                          int reply_type,
                          int reply_create_time,
                          int wait_ms);

int sns_like_to_result(const char *seq,
                       const char *sns_id,
                       const char *username,
                       const char *sns_key,
                       int wait_ms);

int sns_delete_to_result(const char *seq,
                         const char *sns_id,
                         const char *username,
                         const char *sns_key,
                         int wait_ms);

int sns_set_private_to_result(const char *seq,
                              const char *sns_id,
                              const char *username,
                              const char *sns_key,
                              const char *model_ptr,
                              const char *model_ctl,
                              int wait_ms);

int sns_set_visibility_to_result(const char *seq,
                                 const char *sns_id,
                                 const char *username,
                                 const char *sns_key,
                                 const char *model_ptr,
                                 const char *model_ctl,
                                 unsigned int visibility_state,
                                 int wait_ms);

void install_sns_like_probe_hook(void);
void uninstall_sns_like_probe_hook(void);
void refresh_sns_like_probe_hook(void);
int sns_like_probe_hook_installed(void);
LONG sns_like_probe_capture_count(void);

void install_sns_model_lookup_probe_hook(void);
void uninstall_sns_model_lookup_probe_hook(void);
void refresh_sns_model_lookup_probe_hook(void);
int sns_model_lookup_probe_hook_installed(void);
LONG sns_model_lookup_probe_capture_count(void);
void install_sns_model_validate_capture_hook(void);
void uninstall_sns_model_validate_capture_hook(void);

#endif /* WX_BRIDGE_SNS_COMMENT_H */
