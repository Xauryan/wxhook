#ifndef WX_BRIDGE_VOICE_PROBE_H
#define WX_BRIDGE_VOICE_PROBE_H

#include "wx_bridge_platform.h"

void install_voice_probe_hook(void);
void uninstall_voice_probe_hook(void);
void refresh_voice_probe_hook(void);
int voice_probe_hook_installed(void);
void voice_source_builder_shim_enter(void);
void voice_source_builder_shim_leave(void);
int voice_source_builder_shim_active(void);
void voice_probe_log_msg_model(int64_t msg_base, const char *msg_id,
                               int msg_type, int msg_subtype, int is_outgoing);
int voice_probe_copy_cached_state(int64_t *holder_out, int64_t *state_out,
                                  char *target_out, int target_out_sz);
int voice_probe_prepare_cached_state_for_target(const char *target);

#endif /* WX_BRIDGE_VOICE_PROBE_H */
