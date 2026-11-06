#ifndef WX_BRIDGE_HONGBAO_CLOSED_LOOP_H
#define WX_BRIDGE_HONGBAO_CLOSED_LOOP_H

#include "wx_bridge_platform.h"

int wxb_hb_closed_loop_enabled(void);
void wxb_hb_closed_loop_refresh(void);
void wxb_hb_closed_loop_poll(void);
void wxb_hb_closed_loop_note_message(const char *sendid,
                                     const char *msgid,
                                     const char *native_url,
                                     const char *peer,
                                     const char *sender);
void wxb_hb_closed_loop_note_receive_task(int64_t parent_ctx, int64_t arg);
void wxb_hb_closed_loop_note_sentinel(const char *name,
                                      DWORD rva,
                                      int64_t a1);
void wxb_hb_closed_loop_maybe_open_page_ctx(int64_t page_ctx,
                                            const char *reason);

#endif /* WX_BRIDGE_HONGBAO_CLOSED_LOOP_H */
