#ifndef WX_BRIDGE_A8KEY_TASK_H
#define WX_BRIDGE_A8KEY_TASK_H

void install_a8key_dispatch_hook(void);
void uninstall_a8key_dispatch_hook(void);
int a8key_dispatch_hook_installed(void);
int a8key_task_manager_captured(void);
int a8key_task_experimental_enabled(void);
void refresh_finder_userpage_capture_hook(void);
void uninstall_finder_userpage_capture_hook(void);
int finder_userpage_capture_hook_installed(void);
int get_a8key_task_to_result(const char *seq, const char *url, int scene, int url_type, int wait_ms);
int modify_remark_task_to_result(const char *seq, const char *wxid,
                                 const char *remark, int wait_ms);
int modify_chatroom_self_display_name_to_result(const char *seq, const char *roomid,
                                                const char *display_name, int wait_ms);
int modify_chatroom_topic_to_result(const char *seq, const char *roomid,
                                    const char *topic, int wait_ms);
int publish_chatroom_announcement_to_result(const char *seq, const char *roomid,
                                            const char *announcement, int wait_ms);
int finder_userpage_raw_task_to_result(const char *seq, const char *finder_username,
                                       const char *extra_buffer, int wait_ms);
int finder_userpage_model_to_result(const char *seq, const char *finder_username,
                                    const char *extra_buffer, int wait_ms,
                                    int req_field4, int req_field6);

#endif /* WX_BRIDGE_A8KEY_TASK_H */
