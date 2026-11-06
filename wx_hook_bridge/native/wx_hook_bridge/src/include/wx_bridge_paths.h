#ifndef WX_BRIDGE_PATHS_H
#define WX_BRIDGE_PATHS_H

#include "wx_bridge_platform.h"

#define WXB_STARTUP_CONFIG_MAGIC 0x42584257u
#define WXB_STARTUP_CONFIG_FREE_AFTER_READ 0x00000001u
#define WXB_STARTUP_NONCE_CHARS 32u
#define WXB_PATH_MAX_SUFFIX_CHARS 69u

typedef struct WxbStartupConfig {
    DWORD magic;
    DWORD size;
    DWORD flags;
    DWORD reserved;
    WCHAR work_root[MAX_PATH];
    WCHAR startup_nonce[WXB_STARTUP_NONCE_CHARS + 1];
} WxbStartupConfig;

typedef struct WxbPaths {
    WCHAR work_root[MAX_PATH];
    WCHAR bridge_dir[MAX_PATH];
    WCHAR inbox_dir[MAX_PATH];
    WCHAR outbox_dir[MAX_PATH];
    WCHAR control_dir[MAX_PATH];
    WCHAR realtime_queue_dir[MAX_PATH];
    WCHAR outbox_file[MAX_PATH];
    WCHAR stop_file[MAX_PATH];
    WCHAR status_file[MAX_PATH];
    WCHAR realtime_status_file[MAX_PATH];
    WCHAR anti_revoke_state_file[MAX_PATH];
    WCHAR revoke_log[MAX_PATH];
    WCHAR contact_names_file[MAX_PATH];
    WCHAR forward_cache_file[MAX_PATH];
    WCHAR worker_owner_file[MAX_PATH];
    WCHAR revoke_cache_dir[MAX_PATH];
    WCHAR recv_log[MAX_PATH];
    WCHAR quote_debug_log[MAX_PATH];
    WCHAR forward_debug_log[MAX_PATH];
    WCHAR voice_probe_enable_file[MAX_PATH];
    WCHAR voice_probe_log[MAX_PATH];
    WCHAR voice_probe_last_silk_file[MAX_PATH];
    WCHAR voice_probe_final_replay_once_file[MAX_PATH];
    WCHAR voice_source_builder_shim_file[MAX_PATH];
    WCHAR voice_source_ctx_branch_file[MAX_PATH];
    WCHAR a8key_task_enable_file[MAX_PATH];
    WCHAR protocol_capture_enable_file[MAX_PATH];
    WCHAR protocol_capture_unsafe_dispatch_enable_file[MAX_PATH];
    WCHAR protocol_capture_log[MAX_PATH];
    WCHAR finder_userpage_capture_enable_file[MAX_PATH];
    WCHAR finder_userpage_capture_log[MAX_PATH];
    WCHAR sns_timeline_object_capture_log[MAX_PATH];
    WCHAR sns_timeline_response_capture_log[MAX_PATH];
    WCHAR sns_media_decrypt_capture_log[MAX_PATH];
    WCHAR sns_media_probe_enable_file[MAX_PATH];
    WCHAR sns_media_probe_log[MAX_PATH];
    WCHAR sns_upload_trace_enable_file[MAX_PATH];
    WCHAR sns_upload_trace_log[MAX_PATH];
    WCHAR sns_publish_trace_enable_file[MAX_PATH];
    WCHAR sns_publish_trace_log[MAX_PATH];
    WCHAR browser_event_log[MAX_PATH];
    WCHAR image_task_trace_enable_file[MAX_PATH];
    WCHAR image_task_trace_log[MAX_PATH];
    WCHAR hb_debug_log[MAX_PATH];
    WCHAR hb_receive_log[MAX_PATH];
    WCHAR hb_route_log[MAX_PATH];
    WCHAR hb_open_once_file[MAX_PATH];
    WCHAR hb_arm_file[MAX_PATH];
    WCHAR hb_always_file[MAX_PATH];
    WCHAR hb_noclick_file[MAX_PATH];
    WCHAR hb_noclick_always_file[MAX_PATH];
    WCHAR hb_noclick_direct_file[MAX_PATH];
    WCHAR hb_state_file[MAX_PATH];
    WCHAR hb_verbose_file[MAX_PATH];
    WCHAR hb_closed_loop_request_file[MAX_PATH];
    WCHAR hb_closed_loop_state_file[MAX_PATH];
    WCHAR hb_closed_loop_log_file[MAX_PATH];
    WCHAR hb_cold_disable_file[MAX_PATH];
    WCHAR hb_cold_open_disable_file[MAX_PATH];
    WCHAR hb_cold_fastlane_enable_file[MAX_PATH];
    WCHAR hb_cold_delay_file[MAX_PATH];
    WCHAR sns_publish_upper_enable_file[MAX_PATH];
    WCHAR sns_video_manager_entry_enable_file[MAX_PATH];
    WCHAR sns_video_thumb_map_file[MAX_PATH];
    WCHAR sns_like_probe_enable_file[MAX_PATH];
    WCHAR sns_like_probe_log_file[MAX_PATH];
    WCHAR sns_model_lookup_probe_enable_file[MAX_PATH];
    WCHAR sns_model_lookup_probe_log_file[MAX_PATH];
    WCHAR sns_model_validate_capture_log_file[MAX_PATH];
    WCHAR voice_trans_result_log[MAX_PATH];
    WCHAR sns_image_dl_param_file[MAX_PATH];
    WCHAR sns_full_cdn_param_file[MAX_PATH];
    WCHAR image_dl_param_file[MAX_PATH];
} WxbPaths;

int wxb_paths_init(HMODULE module, LPVOID startup_param);
int wxb_paths_ready(void);
const WxbPaths *wxb_paths(void);

#define WORK_ROOT       (wxb_paths()->work_root)
#define BRIDGE_DIR      (wxb_paths()->bridge_dir)
#define INBOX_DIR       (wxb_paths()->inbox_dir)
#define OUTBOX_DIR      (wxb_paths()->outbox_dir)
#define CONTROL_DIR     (wxb_paths()->control_dir)
#define REALTIME_QUEUE_DIR (wxb_paths()->realtime_queue_dir)
#define OUTBOX_FILE     (wxb_paths()->outbox_file)
#define STOP_FILE       (wxb_paths()->stop_file)
#define STATUS_FILE     (wxb_paths()->status_file)
#define REALTIME_STATUS_FILE (wxb_paths()->realtime_status_file)
#define ANTI_REVOKE_STATE_FILE (wxb_paths()->anti_revoke_state_file)
#define REVOKE_LOG      (wxb_paths()->revoke_log)
#define CONTACT_NAMES_FILE (wxb_paths()->contact_names_file)
#define FORWARD_CACHE_FILE (wxb_paths()->forward_cache_file)
#define WORKER_OWNER_FILE (wxb_paths()->worker_owner_file)
#define REVOKE_CACHE_DIR (wxb_paths()->revoke_cache_dir)
#define RECV_LOG        (wxb_paths()->recv_log)
#define QUOTE_DEBUG_LOG (wxb_paths()->quote_debug_log)
#define FORWARD_DEBUG_LOG (wxb_paths()->forward_debug_log)
#define VOICE_PROBE_ENABLE_FILE (wxb_paths()->voice_probe_enable_file)
#define VOICE_PROBE_LOG (wxb_paths()->voice_probe_log)
#define VOICE_PROBE_LAST_SILK_FILE (wxb_paths()->voice_probe_last_silk_file)
#define VOICE_PROBE_FINAL_REPLAY_ONCE_FILE (wxb_paths()->voice_probe_final_replay_once_file)
#define VOICE_SOURCE_BUILDER_SHIM_FILE (wxb_paths()->voice_source_builder_shim_file)
#define VOICE_SOURCE_CTX_BRANCH_FILE (wxb_paths()->voice_source_ctx_branch_file)
#define A8KEY_TASK_ENABLE_FILE (wxb_paths()->a8key_task_enable_file)
#define PROTOCOL_CAPTURE_ENABLE_FILE (wxb_paths()->protocol_capture_enable_file)
#define PROTOCOL_CAPTURE_UNSAFE_DISPATCH_ENABLE_FILE (wxb_paths()->protocol_capture_unsafe_dispatch_enable_file)
#define PROTOCOL_CAPTURE_LOG (wxb_paths()->protocol_capture_log)
#define FINDER_USERPAGE_CAPTURE_ENABLE_FILE (wxb_paths()->finder_userpage_capture_enable_file)
#define FINDER_USERPAGE_CAPTURE_LOG (wxb_paths()->finder_userpage_capture_log)
#define SNS_TIMELINE_OBJECT_CAPTURE_LOG (wxb_paths()->sns_timeline_object_capture_log)
#define SNS_TIMELINE_RESPONSE_CAPTURE_LOG (wxb_paths()->sns_timeline_response_capture_log)
#define SNS_MEDIA_DECRYPT_CAPTURE_LOG (wxb_paths()->sns_media_decrypt_capture_log)
#define SNS_MEDIA_PROBE_ENABLE_FILE (wxb_paths()->sns_media_probe_enable_file)
#define SNS_MEDIA_PROBE_LOG (wxb_paths()->sns_media_probe_log)
#define SNS_UPLOAD_TRACE_ENABLE_FILE (wxb_paths()->sns_upload_trace_enable_file)
#define SNS_UPLOAD_TRACE_LOG (wxb_paths()->sns_upload_trace_log)
#define SNS_PUBLISH_TRACE_ENABLE_FILE (wxb_paths()->sns_publish_trace_enable_file)
#define SNS_PUBLISH_TRACE_LOG (wxb_paths()->sns_publish_trace_log)
#define BROWSER_EVENT_LOG (wxb_paths()->browser_event_log)
#define IMAGE_TASK_TRACE_ENABLE_FILE (wxb_paths()->image_task_trace_enable_file)
#define IMAGE_TASK_TRACE_LOG (wxb_paths()->image_task_trace_log)
#define HB_DEBUG_LOG     (wxb_paths()->hb_debug_log)
#define HB_RECEIVE_LOG   (wxb_paths()->hb_receive_log)
#define HB_ROUTE_LOG     (wxb_paths()->hb_route_log)
#define HB_OPEN_ONCE_FILE (wxb_paths()->hb_open_once_file)
#define HB_ARM_FILE      (wxb_paths()->hb_arm_file)
#define HB_ALWAYS_FILE   (wxb_paths()->hb_always_file)
#define HB_NOCLICK_FILE  (wxb_paths()->hb_noclick_file)
#define HB_NOCLICK_ALWAYS_FILE (wxb_paths()->hb_noclick_always_file)
#define HB_NOCLICK_DIRECT_FILE (wxb_paths()->hb_noclick_direct_file)
#define HB_STATE_FILE    (wxb_paths()->hb_state_file)
#define HB_VERBOSE_FILE  (wxb_paths()->hb_verbose_file)
#define HB_CLOSED_LOOP_REQUEST_FILE (wxb_paths()->hb_closed_loop_request_file)
#define HB_CLOSED_LOOP_STATE_FILE (wxb_paths()->hb_closed_loop_state_file)
#define HB_CLOSED_LOOP_LOG_FILE (wxb_paths()->hb_closed_loop_log_file)
#define HB_COLD_DISABLE_FILE (wxb_paths()->hb_cold_disable_file)
#define HB_COLD_OPEN_DISABLE_FILE (wxb_paths()->hb_cold_open_disable_file)
#define HB_COLD_FASTLANE_ENABLE_FILE (wxb_paths()->hb_cold_fastlane_enable_file)
#define HB_COLD_DELAY_FILE (wxb_paths()->hb_cold_delay_file)
#define SNS_PUBLISH_UPPER_ENABLE_FILE (wxb_paths()->sns_publish_upper_enable_file)
#define SNS_VIDEO_MANAGER_ENTRY_ENABLE_FILE (wxb_paths()->sns_video_manager_entry_enable_file)
#define SNS_VIDEO_THUMB_MAP_FILE (wxb_paths()->sns_video_thumb_map_file)
#define SNS_LIKE_PROBE_ENABLE_FILE (wxb_paths()->sns_like_probe_enable_file)
#define SNS_LIKE_PROBE_LOG_FILE (wxb_paths()->sns_like_probe_log_file)
#define SNS_MODEL_LOOKUP_PROBE_ENABLE_FILE (wxb_paths()->sns_model_lookup_probe_enable_file)
#define SNS_MODEL_LOOKUP_PROBE_LOG_FILE (wxb_paths()->sns_model_lookup_probe_log_file)
#define SNS_MODEL_VALIDATE_CAPTURE_LOG_FILE (wxb_paths()->sns_model_validate_capture_log_file)
#define VOICE_TRANS_RESULT_LOG (wxb_paths()->voice_trans_result_log)
#define SNS_IMAGE_DL_PARAM_FILE (wxb_paths()->sns_image_dl_param_file)
#define SNS_FULL_CDN_PARAM_FILE (wxb_paths()->sns_full_cdn_param_file)
#define IMAGE_DL_PARAM_FILE (wxb_paths()->image_dl_param_file)

#define HB_DEFERRED_OPEN_DELAY_MS 1500
#define DIRECT_PUSH_HOST L"127.0.0.1"
#define DIRECT_PUSH_PORT 8765
#define DIRECT_PUSH_PATH L"/api/realtime/ingest"

#endif /* WX_BRIDGE_PATHS_H */
