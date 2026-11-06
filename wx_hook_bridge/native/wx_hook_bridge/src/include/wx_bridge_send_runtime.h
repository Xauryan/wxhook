#ifndef WX_BRIDGE_SEND_RUNTIME_H
#define WX_BRIDGE_SEND_RUNTIME_H

#include "wx_bridge_platform.h"

const char *wxb_basename_utf8(const char *path);
int64_t wxb_file_size_utf8(const char *path);
void wxb_retain_arena(void *arena);
int wxb_queue_send(int64_t asm_addr);

#define basename_utf8 wxb_basename_utf8
#define file_size_utf8 wxb_file_size_utf8
#define retain_arena wxb_retain_arena
#define queue_send wxb_queue_send

#endif /* WX_BRIDGE_SEND_RUNTIME_H */
