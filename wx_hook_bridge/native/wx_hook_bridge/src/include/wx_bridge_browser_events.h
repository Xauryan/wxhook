#ifndef WX_BRIDGE_BROWSER_EVENTS_H
#define WX_BRIDGE_BROWSER_EVENTS_H

#include "wx_bridge_platform.h"

void wxb_browser_note_open_url(const char *seq, const char *url);
void wxb_install_browser_event_hook(void);
void wxb_uninstall_browser_event_hook(void);
int wxb_browser_event_hook_installed(void);

#define browser_note_open_url wxb_browser_note_open_url
#define install_browser_event_hook wxb_install_browser_event_hook
#define uninstall_browser_event_hook wxb_uninstall_browser_event_hook
#define browser_event_hook_installed wxb_browser_event_hook_installed

#endif /* WX_BRIDGE_BROWSER_EVENTS_H */
