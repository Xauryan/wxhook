#ifndef WX_BRIDGE_SNS_TIMELINE_H
#define WX_BRIDGE_SNS_TIMELINE_H

#include "wx_bridge_platform.h"

int get_sns_timeline_to_result(const char *seq, int wait_ms, int rich_mode);
void install_sns_timeline_object_hook(void);
void uninstall_sns_timeline_object_hook(void);
int sns_timeline_object_hook_installed(void);
LONG sns_timeline_object_capture_count(void);

#endif /* WX_BRIDGE_SNS_TIMELINE_H */
