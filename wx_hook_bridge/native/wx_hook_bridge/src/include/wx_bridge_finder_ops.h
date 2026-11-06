#ifndef WX_BRIDGE_FINDER_OPS_H
#define WX_BRIDGE_FINDER_OPS_H

int finder_search_to_result(const char *seq,
                            const char *query,
                            int offset,
                            int scene,
                            int business_type,
                            int sub_type,
                            int current_page,
                            const char *session_id,
                            const char *request_id,
                            int wait_ms);

int finder_enter_live_to_result(const char *seq,
                                const char *url,
                                const char *object_id,
                                const char *nonce_id,
                                const char *context_id,
                                const char *entrance_id,
                                const char *sub_entrance_id,
                                int muted);

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
                                       int wait_ms);

/* Finder profile/feed page through the current H5 CGI (cmd 6624). */
int finder_userpage_h5_to_result(const char *seq,
                                 const char *username,
                                 const char *object_id,
                                 const char *context_id,
                                 const char *entrance_id,
                                 const char *finder_username,
                                 const char *h5_url,
                                 int scene,
                                 int wait_ms);

#endif /* WX_BRIDGE_FINDER_OPS_H */
