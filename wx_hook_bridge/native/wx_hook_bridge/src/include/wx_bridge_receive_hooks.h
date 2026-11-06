#ifndef WX_BRIDGE_RECEIVE_HOOKS_H
#define WX_BRIDGE_RECEIVE_HOOKS_H

void install_recv_hook(void);
void uninstall_recv_hook(void);
void install_async_queue_hook(void);
void uninstall_async_queue_hook(void);
int recv_hook_installed(void);
int async_queue_hook_installed(void);

#endif /* WX_BRIDGE_RECEIVE_HOOKS_H */
