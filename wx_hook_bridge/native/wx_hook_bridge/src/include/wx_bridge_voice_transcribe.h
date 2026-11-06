#ifndef WX_BRIDGE_VOICE_TRANSCRIBE_H
#define WX_BRIDGE_VOICE_TRANSCRIBE_H

#include "wx_bridge_platform.h"

int voice_transcribe_to_result(const char *seq, int64_t msgid, int32_t local_id, int active_submit);
void install_voice_trans_result_hook(void);
void uninstall_voice_trans_result_hook(void);
int voice_trans_result_hook_installed(void);

#endif /* WX_BRIDGE_VOICE_TRANSCRIBE_H */
