#ifndef WX_BRIDGE_FORWARD_HOOKS_H
#define WX_BRIDGE_FORWARD_HOOKS_H

#include "wx_bridge_platform.h"

void install_forward_ui_entry_trace_hook(void);
void install_forward_submit_trace_hook(void);
void install_forward_dispatch_hook(void);
void uninstall_forward_submit_trace_hook(void);
void uninstall_forward_ui_entry_trace_hook(void);
void uninstall_forward_dispatch_hook(void);
int forward_ui_entry_trace_hook_installed(void);
int forward_submit_trace_hook_installed(void);
int forward_dispatch_hook_installed(void);
int64_t forward_call_dispatch_original(int64_t manager, int64_t seq, int64_t obj);
int64_t forward_call_submit_original(int64_t manager, int64_t pair,
                                     int64_t variant, int64_t do_dispatch);
int64_t forward_call_ui_entry_original(int64_t ctx, int64_t arg2,
                                       int64_t arg3, int64_t arg4);

#endif /* WX_BRIDGE_FORWARD_HOOKS_H */
