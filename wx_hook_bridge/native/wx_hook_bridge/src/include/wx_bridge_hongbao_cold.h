#ifndef WX_BRIDGE_HONGBAO_COLD_H
#define WX_BRIDGE_HONGBAO_COLD_H

#include "wx_bridge_platform.h"

/* Cold red-packet claim (冷抢) — same pipeline as RE/frida_cold_hongbao_auto.py:
 *   RECV LocalMsg 0x290 → FillFromLocalMsg → Service_StartReceive → deferred Launch_receive
 *   ParseExtra → timing → Service_StartOpen → deferred Launch_open
 * Never constructs CGI payloads; never hooks SendCgi in cold mode.
 */

int wxb_hb_cold_enabled(void);
int wxb_hb_cold_open_enabled(void);

void wxb_hb_cold_refresh(void);
void wxb_hb_cold_poll(void);
void wxb_hb_cold_shutdown(void);

/* Scan a 0x290 LocalMsg/source vector (same layout as source_submit / parser).
 * Only builds claim (Fill+ServiceRecv); does NOT Launch 1581 yet. */
int wxb_hb_cold_scan_source_vector(LONG hit, int64_t a1, int64_t vector_arg);

/*
 * Earliest claim entry: DoAddMsg / process_message already has a live LocalMsg-like
 * object. Builds claim only; pair with wxb_hb_cold_flush_launches().
 */
int wxb_hb_cold_try_claim_local_msg(int64_t msg);

/*
 * Frida parity: Launch_receive after the RECV hook stack finishes.
 * Call AFTER original source_submit / after DoAddMsg processing returns.
 */
void wxb_hb_cold_flush_launches(void);

/* Passive cgiTable cache when Factory_receive(a1=cgiTable, ...) is observed. */
void wxb_hb_cold_note_factory_cgi(int64_t cgi_table);

void install_hb_cold_parse_extra_hook(void);
void uninstall_hb_cold_parse_extra_hook(void);

#define hb_cold_enabled wxb_hb_cold_enabled
#define hb_cold_open_enabled wxb_hb_cold_open_enabled
#define hb_cold_refresh wxb_hb_cold_refresh
#define hb_cold_poll wxb_hb_cold_poll
#define hb_cold_shutdown wxb_hb_cold_shutdown
#define hb_cold_scan_source_vector wxb_hb_cold_scan_source_vector
#define hb_cold_try_claim_local_msg wxb_hb_cold_try_claim_local_msg
#define hb_cold_flush_launches wxb_hb_cold_flush_launches
#define hb_cold_note_factory_cgi wxb_hb_cold_note_factory_cgi

#endif /* WX_BRIDGE_HONGBAO_COLD_H */
