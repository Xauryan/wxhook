#ifndef WX_BRIDGE_REVOKE_HOOKS_H
#define WX_BRIDGE_REVOKE_HOOKS_H

#include "wx_bridge_platform.h"

void install_revoke_entry_hook(void);
void uninstall_revoke_entry_hook(void);
void install_revoke_dispatch_hook(void);
void uninstall_revoke_dispatch_hook(void);
void install_revoke_real_hook(void);
void uninstall_revoke_real_hook(void);
void restore_revoke_dispatch_site_if_stale(void);
int revoke_entry_hook_installed(void);
int revoke_dispatch_hook_installed(void);
int revoke_real_hook_installed(void);

#endif /* WX_BRIDGE_REVOKE_HOOKS_H */
