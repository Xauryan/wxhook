#ifndef WX_BRIDGE_FORWARD_ENTRYPOINTS_H
#define WX_BRIDGE_FORWARD_ENTRYPOINTS_H

#include "wx_bridge_platform.h"

int64_t __fastcall forward_dispatch_hook(int64_t manager, int64_t seq, int64_t obj);
int64_t __fastcall forward_submit_hook(int64_t manager, int64_t pair,
                                       int64_t variant, int64_t do_dispatch);
int64_t __fastcall forward_ui_entry_hook(int64_t ctx, int64_t arg2,
                                         int64_t arg3, int64_t arg4);
void forward_reset_runtime_patch_state(void);

#endif /* WX_BRIDGE_FORWARD_ENTRYPOINTS_H */
