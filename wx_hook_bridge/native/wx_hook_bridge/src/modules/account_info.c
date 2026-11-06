#include "../include/wx_bridge_account_info.h"
#include "../include/wx_bridge_contact_lookup.h"
#include "../include/wx_bridge_memory.h"
#include "../include/wx_bridge_runtime.h"
#include "../include/wx_bridge_text.h"
#include "../include/wx_bridge_wechat_string.h"

static DWORD account_weixin_image_size(void) {
    DWORD size = 0;
    BYTE *base = (BYTE *)wxb_weixin_module();
    if (!base) return 0;
    __try {
        IMAGE_DOS_HEADER *dos = (IMAGE_DOS_HEADER *)base;
        IMAGE_NT_HEADERS64 *nt;
        if (dos->e_magic != IMAGE_DOS_SIGNATURE) return 0;
        nt = (IMAGE_NT_HEADERS64 *)(base + dos->e_lfanew);
        if (nt->Signature != IMAGE_NT_SIGNATURE) return 0;
        size = nt->OptionalHeader.SizeOfImage;
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
        size = 0;
    }
    return size;
}

static int is_scan_readable_protect(DWORD protect) {
    if (!protect || (protect & PAGE_GUARD) || (protect & PAGE_NOACCESS)) return 0;
    return (protect & (PAGE_READONLY | PAGE_READWRITE | PAGE_WRITECOPY |
                       PAGE_EXECUTE_READ | PAGE_EXECUTE_READWRITE |
                       PAGE_EXECUTE_WRITECOPY)) != 0;
}

static int find_login_manager_ptr(int64_t *out_manager_ptr_addr) {
    BYTE *base = (BYTE *)wxb_weixin_module();
    DWORD size = account_weixin_image_size();
    if (!out_manager_ptr_addr) return 0;
    *out_manager_ptr_addr = 0;
    if (!base || size < 18) return 0;
    __try {
        for (DWORD i = 0; i + 18 < size; i++) {
            BYTE *p = base + i;
            if (p[0] == 0x48 && p[1] == 0x8B && p[2] == 0x0D &&
                p[7] == 0x48 && p[8] == 0x8B && p[9] == 0x01 &&
                p[10] == 0x48 && p[11] == 0x8D && p[12] == 0x55 &&
                p[13] == 0xD8 && p[14] == 0xFF && p[15] == 0x50 &&
                p[16] == 0x60) {
                int32_t rel = *(int32_t *)(p + 3);
                *out_manager_ptr_addr = (int64_t)(p + 7 + rel);
                return 1;
            }
        }
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
        *out_manager_ptr_addr = 0;
    }
    return 0;
}

static int find_contact_object_vt(int64_t *out) {
    BYTE *base = (BYTE *)wxb_weixin_module();
    DWORD size = account_weixin_image_size();
    if (!out) return 0;
    *out = 0;
    if (!base || size < 32) return 0;
    __try {
        for (DWORD i = 0; i + 17 < size; i++) {
            BYTE *p = base + i;
            if (p[0] == 0x48 && p[1] == 0x8D && p[2] == 0x05 &&
                p[7] == 0x49 && p[8] == 0x89 && p[9] == 0x06 &&
                p[10] == 0x4C && p[11] == 0x89 && p[12] == 0xB5 &&
                p[13] == 0x58 && p[14] == 0x01 && p[15] == 0x00 && p[16] == 0x00) {
                int32_t rel = *(int32_t *)(p + 3);
                *out = (int64_t)(p + 7 + rel);
                return *out != 0;
            }
        }
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
        *out = 0;
    }
    return 0;
}

static int is_contact_wxid_candidate(const char *wxid) {
    int len;
    if (!wxid || !wxid[0]) return 0;
    if (str_eq(wxid, "filehelper")) return 1;
    if (str_has(wxid, "@chatroom")) return 1;
    if (str_has(wxid, "gh_") || str_has(wxid, "weixin") ||
        str_has(wxid, "filehelp") || str_has(wxid, "qqmail") ||
        str_has(wxid, "weibo") || str_has(wxid, "floatbottle") ||
        str_has(wxid, "medianote") || str_has(wxid, "message") ||
        str_has(wxid, "mphelper") || str_has(wxid, "newsapp") ||
        str_has(wxid, "pmnew2010") || str_has(wxid, "qqsafe")) {
        return 1;
    }
    if (wxid[0] == 'w' && wxid[1] == 'x' && wxid[2] == 'i' && wxid[3] == 'd' && wxid[4] == '_') {
        return 1;
    }
    len = lstrlenA(wxid);
    if (len < 5 || len > 32) return 0;
    if (!((wxid[0] >= 'A' && wxid[0] <= 'Z') || (wxid[0] >= 'a' && wxid[0] <= 'z'))) return 0;
    for (int i = 1; i < len; i++) {
        char c = wxid[i];
        if (!((c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') ||
              (c >= '0' && c <= '9') || c == '_' || c == '-')) {
            return 0;
        }
    }
    return 1;
}

static const char *contact_category(const char *wxid, BYTE state) {
    if (wxid && str_has(wxid, "@chatroom")) return "room";
    if (wxid && (str_has(wxid, "gh_") || str_has(wxid, "weixin") ||
                 str_has(wxid, "filehelp") || str_has(wxid, "qqmail") ||
                 str_has(wxid, "weibo") || str_has(wxid, "floatbottle") ||
                 str_has(wxid, "medianote") || str_has(wxid, "message") ||
                 str_has(wxid, "mphelper") || str_has(wxid, "newsapp") ||
                 str_has(wxid, "pmnew2010") || str_has(wxid, "qqsafe"))) {
        return "system";
    }
    if (state == 2) return "deleted";
    if ((state & 1) != 0) return "friend";
    return "stranger";
}

static void append_json_field(char **p, char *end, const char *key, const char *value) {
    append_raw(p, end, "\"");
    append_raw(p, end, key);
    append_raw(p, end, "\":\"");
    append_escaped(p, end, value ? value : "");
    append_raw(p, end, "\"");
}

static void read_c_string_ptr(int64_t ptr, char *out, int max) {
    int i;
    if (!out || max <= 0) return;
    out[0] = 0;
    if (!ptr) return;
    __try {
        for (i = 0; i < max - 1; i++) {
            char c = ((const char *)ptr)[i];
            if (!c) break;
            out[i] = c;
        }
        out[i] = 0;
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
        out[0] = 0;
    }
}

int wxb_self_profile_json(char *out, int max) {
    int64_t manager_ptr_addr = 0;
    int64_t user_manager = 0;
    int64_t user_info = 0;
    char wxid[256] = {0}, account[256] = {0}, nickname[512] = {0};
    char mobile[256] = {0}, signature[1024] = {0}, city[256] = {0};
    char province[256] = {0}, country[256] = {0}, sns_bg[1024] = {0};
    char device_type[256] = {0};
    char *p = out;
    char *end = out + max - 1;
    if (!out || max <= 0) return 0;
    out[0] = 0;
    if (!wxb_weixin_module() || !find_login_manager_ptr(&manager_ptr_addr)) return 0;
    __try {
        user_manager = r64((void *)manager_ptr_addr);
        user_info = user_manager ? r64((void *)(user_manager + 0x68)) : 0;
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
        user_info = 0;
    }
    if (!user_info || !is_readable_ptr(user_info, 0x1D0)) return 0;

    wx_read_str(user_info + 0x48, wxid, sizeof(wxid));
    wx_read_str(user_info + 0x68, account, sizeof(account));
    wx_read_str(user_info + 0x88, nickname, sizeof(nickname));
    wx_read_str(user_info + 0xA8, mobile, sizeof(mobile));
    wx_read_str(user_info + 0xD0, signature, sizeof(signature));
    wx_read_str(user_info + 0xF0, city, sizeof(city));
    wx_read_str(user_info + 0x110, province, sizeof(province));
    wx_read_str(user_info + 0x130, country, sizeof(country));
    read_c_string_ptr(r64((void *)(user_info + 0x180)), sns_bg, sizeof(sns_bg));
    wx_read_str(user_info + 0x1B0, device_type, sizeof(device_type));
    if (is_wxid_like(wxid)) wxb_learn_self_wxid(wxid);

    append_raw(&p, end, "{");
    append_json_field(&p, end, "wxid", wxid); append_raw(&p, end, ",");
    append_json_field(&p, end, "account", account); append_raw(&p, end, ",");
    append_json_field(&p, end, "nickname", nickname); append_raw(&p, end, ",");
    append_json_field(&p, end, "mobile", mobile); append_raw(&p, end, ",");
    append_json_field(&p, end, "signature", signature); append_raw(&p, end, ",");
    append_json_field(&p, end, "city", city); append_raw(&p, end, ",");
    append_json_field(&p, end, "province", province); append_raw(&p, end, ",");
    append_json_field(&p, end, "country", country); append_raw(&p, end, ",");
    append_json_field(&p, end, "snsBackground", sns_bg); append_raw(&p, end, ",");
    append_json_field(&p, end, "deviceType", device_type);
    append_raw(&p, end, "}");
    *p = 0;
    return wxid[0] != 0;
}

char *wxb_contact_list_json(int limit) {
    int64_t target_vt = 0;
    MEMORY_BASIC_INFORMATION mbi;
    BYTE *addr = (BYTE *)0x10000;
    BYTE *max_addr = (BYTE *)0x00007FFFFFFFFFFFULL;
    int count = 0;
    SIZE_T cap;
    char *json;
    char *p;
    char *end;
    if (limit <= 0 || limit > 10000) limit = 5000;
    if (!wxb_weixin_module() || !find_contact_object_vt(&target_vt) || !target_vt) return NULL;
    cap = (SIZE_T)limit * 1800 + 4096;
    json = (char *)VirtualAlloc(NULL, cap, MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE);
    if (!json) return NULL;
    p = json;
    end = json + cap - 1;
    append_raw(&p, end, "[");
    while (addr < max_addr && count < limit) {
        SIZE_T got = VirtualQuery(addr, &mbi, sizeof(mbi));
        BYTE *base;
        SIZE_T size;
        if (got != sizeof(mbi)) {
            addr += 0x10000;
            continue;
        }
        base = (BYTE *)mbi.BaseAddress;
        size = mbi.RegionSize;
        if (size > 0 && mbi.State == MEM_COMMIT &&
            is_scan_readable_protect(mbi.Protect) &&
            size <= 64ULL * 1024ULL * 1024ULL) {
            __try {
                for (SIZE_T off = 0; off + sizeof(int64_t) <= size && count < limit; off += sizeof(void *)) {
                    int64_t cand = (int64_t)(base + off);
                    char wxid[512] = {0}, account[512] = {0}, v3[512] = {0};
                    char remark[512] = {0}, nickname[512] = {0}, head_image[1024] = {0};
                    char signature[1024] = {0}, city1[256] = {0}, city2[256] = {0}, city3[256] = {0};
                    BYTE state, attr;
                    char state_hex[3], attr_hex[3];
                    const char *hex = "0123456789abcdef";
                    if (*(int64_t *)cand != target_vt) continue;
                    if (!is_readable_ptr(cand, 0x250)) continue;
                    wx_read_str(cand + 0x18, wxid, sizeof(wxid));
                    if (!is_contact_wxid_candidate(wxid)) continue;
                    wx_read_str(cand + 0x38, account, sizeof(account));
                    wx_read_str(cand + 0x58, v3, sizeof(v3));
                    wx_read_str(cand + 0x88, remark, sizeof(remark));
                    wx_read_str(cand + 0xE8, nickname, sizeof(nickname));
                    wx_read_str(cand + 0x148, head_image, sizeof(head_image));
                    wx_read_str(cand + 0x210, signature, sizeof(signature));
                    wx_read_str(cand + 0x218, city1, sizeof(city1));
                    wx_read_str(cand + 0x220, city2, sizeof(city2));
                    wx_read_str(cand + 0x228, city3, sizeof(city3));
                    state = *(BYTE *)(cand + 0x7C);
                    attr = *(BYTE *)(cand + 0x7D);
                    state_hex[0] = hex[(state >> 4) & 0xF]; state_hex[1] = hex[state & 0xF]; state_hex[2] = 0;
                    attr_hex[0] = hex[(attr >> 4) & 0xF]; attr_hex[1] = hex[attr & 0xF]; attr_hex[2] = 0;
                    if (count) append_raw(&p, end, ",");
                    append_raw(&p, end, "{");
                    append_json_field(&p, end, "wxid", wxid); append_raw(&p, end, ",");
                    append_json_field(&p, end, "account", account); append_raw(&p, end, ",");
                    append_json_field(&p, end, "v3", v3); append_raw(&p, end, ",");
                    append_json_field(&p, end, "remark", remark); append_raw(&p, end, ",");
                    append_json_field(&p, end, "nickname", nickname); append_raw(&p, end, ",");
                    append_json_field(&p, end, "headImage", head_image); append_raw(&p, end, ",");
                    append_json_field(&p, end, "head_image", head_image); append_raw(&p, end, ",");
                    append_json_field(&p, end, "signature", signature); append_raw(&p, end, ",");
                    append_raw(&p, end, "\"city\":\"");
                    append_escaped(&p, end, city1);
                    if (city2[0]) { append_raw(&p, end, " "); append_escaped(&p, end, city2); }
                    if (city3[0]) { append_raw(&p, end, " "); append_escaped(&p, end, city3); }
                    append_raw(&p, end, "\",");
                    append_json_field(&p, end, "state", state_hex); append_raw(&p, end, ",");
                    append_json_field(&p, end, "attr", attr_hex); append_raw(&p, end, ",");
                    append_json_field(&p, end, "category", contact_category(wxid, state));
                    append_raw(&p, end, "}");
                    count++;
                }
            }
            __except (EXCEPTION_EXECUTE_HANDLER) {
            }
        }
        if (!size || base + size <= addr) break;
        addr = base + size;
    }
    append_raw(&p, end, "]");
    *p = 0;
    return json;
}
