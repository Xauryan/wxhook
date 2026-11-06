#include "../include/wx_bridge_anti_revoke.h"
#include "../include/wx_bridge_file_io.h"
#include "../include/wx_bridge_memory.h"
#include "../include/wx_bridge_offsets.h"
#include "../include/wx_bridge_paths.h"
#include "../include/wx_bridge_protocol.h"
#include "../include/wx_bridge_runtime.h"

static BYTE g_revoke_orig[6] = {0};
static volatile LONG g_revoke_patch_enabled = 0;

int wxb_set_anti_revoke(int enable) {
    BYTE *target;
    DWORD old = 0;
    if (!wxb_weixin_module()) return 0;
    target = (BYTE *)((int64_t)wxb_weixin_module() + WX_OFF_REVOKE_PATCH);
    if (enable) {
        BYTE expected_orig[6] = {0x0F, 0x84, 0x1B, 0x01, 0x00, 0x00};
        BYTE patch_jnz[6]     = {0x0F, 0x85, 0x1B, 0x01, 0x00, 0x00};
        BYTE cur[6];
        if (g_revoke_patch_enabled) {
            wxb_write_status("anti_revoke enabled\n");
            return 1;
        }
        wxb_copy_bytes(cur, target, 6);
        if (cur[0] == expected_orig[0] && cur[1] == expected_orig[1]) {
            wxb_copy_bytes(g_revoke_orig, cur, 6);
        } else {
            wxb_copy_bytes(g_revoke_orig, expected_orig, 6);
        }
        VirtualProtect(target, 6, PAGE_EXECUTE_READWRITE, &old);
        wxb_copy_bytes(target, patch_jnz, 6);
        VirtualProtect(target, 6, old, &old);
        FlushInstructionCache(GetCurrentProcess(), target, 6);
        InterlockedExchange(&g_revoke_patch_enabled, 1);
        wxb_write_status("anti_revoke enabled\n");
        return 1;
    }
    if (g_revoke_patch_enabled) {
        VirtualProtect(target, 6, PAGE_EXECUTE_READWRITE, &old);
        wxb_copy_bytes(target, g_revoke_orig, 6);
        VirtualProtect(target, 6, old, &old);
        FlushInstructionCache(GetCurrentProcess(), target, 6);
        InterlockedExchange(&g_revoke_patch_enabled, 0);
    }
    wxb_write_status("anti_revoke disabled\n");
    return 1;
}

void wxb_restore_anti_revoke_state(void) {
    char state[16] = {0};
    int n = wxb_read_file_bytes(ANTI_REVOKE_STATE_FILE, state, sizeof(state));
    if (n <= 0) return;
    if (state[0] == '1' || state[0] == 't' || state[0] == 'T' ||
        state[0] == 'y' || state[0] == 'Y') {
        wxb_set_anti_revoke(1);
    } else {
        wxb_set_anti_revoke(0);
    }
}
