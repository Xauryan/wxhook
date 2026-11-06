#include "../include/wx_bridge_contact_lookup.h"
#include "../include/wx_bridge_file_io.h"
#include "../include/wx_bridge_paths.h"
#include "../include/wx_bridge_text.h"

int wxb_is_internal_display_id(const char *s) {
    if (!s || !s[0]) return 0;
    return wxb_is_wxid_like(s) || wxb_is_chatroom_id(s) || wxb_str_eq(s, "filehelper") ||
           wxb_str_has(s, "@openim") || wxb_str_has(s, "@stranger");
}

int wxb_is_displayable_name_for_id(const char *id, const char *name) {
    if (!name || !name[0]) return 0;
    if (id && id[0] && wxb_str_eq(id, name)) return 0;
    if (wxb_is_internal_display_id(name)) return 0;
    return 1;
}

int wxb_lookup_contact_display_name(const char *wxid, char *out, int max) {
    const DWORD maxread = 262144;
    char *buf;
    int n;
    int found = 0;
    if (!out || max <= 0) return 0;
    out[0] = 0;
    if (!wxid || !wxid[0]) return 0;
    if (wxb_str_eq(wxid, "filehelper")) {
        wxb_safe_copy(out, max, "文件传输助手");
        return 1;
    }
    buf = (char *)VirtualAlloc(NULL, maxread, MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE);
    if (!buf) return 0;
    n = wxb_read_file_tail_bytes(CONTACT_NAMES_FILE, buf, maxread);
    if (n > 0) {
        char *line = buf;
        char *end = buf + n;
        while (line < end && *line) {
            char *e = line;
            char *tab;
            char *name;
            while (e < end && *e && *e != '\r' && *e != '\n') e++;
            if (e < end) *e++ = 0;
            while (e < end && (*e == '\r' || *e == '\n')) e++;

            tab = line;
            while (*tab && *tab != '\t') tab++;
            if (*tab == '\t') {
                *tab++ = 0;
                name = tab;
                while (*tab && *tab != '\t') tab++;
                if (*tab == '\t') *tab = 0;
                wxb_trim_trailing_crlf(line);
                wxb_trim_trailing_crlf(name);
                if (wxb_str_eq(line, wxid) && wxb_is_displayable_name_for_id(wxid, name)) {
                    wxb_safe_copy(out, max, name);
                    found = 1;  /* keep scanning; the latest entry wins */
                }
            }
            line = e;
        }
    }
    VirtualFree(buf, 0, MEM_RELEASE);
    return found;
}

int wxb_lookup_room_member_display_name(const char *roomid, const char *member_wxid,
                                        char *out, int max) {
    char key[900];
    char *p = key;
    char *end = key + sizeof(key) - 1;
    if (!roomid || !roomid[0] || !member_wxid || !member_wxid[0]) return 0;
    if (!wxb_is_chatroom_id(roomid)) return 0;
    wxb_append_raw(&p, end, roomid);
    wxb_append_raw(&p, end, "/");
    wxb_append_raw(&p, end, member_wxid);
    *p = 0;
    return wxb_lookup_contact_display_name(key, out, max);
}
