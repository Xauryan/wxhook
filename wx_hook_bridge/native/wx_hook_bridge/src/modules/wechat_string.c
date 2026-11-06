#include "../include/wx_bridge_wechat_string.h"
#include "../include/wx_bridge_memory.h"
#include "../include/wx_bridge_native_call.h"
#include "../include/wx_bridge_offsets.h"
#include "../include/wx_bridge_runtime.h"
#include "../include/wx_bridge_text.h"

int wxb_wx_read_str(int64_t s, char *out, int max) {
    int len, tag, n;
    const char *src;
    if (!s || max <= 0) return 0;
    out[0] = 0;
    len = r32((void *)(s + 0x10));
    tag = r32((void *)(s + 0x18));
    if (len <= 0 || tag <= 0) return 0;
    src = (tag == 15) ? (const char *)s : (const char *)r64((void *)s);
    if (!src) return 0;
    n = (len < max - 1) ? len : max - 1;
    __try {
        for (int i = 0; i < n; i++) out[i] = src[i];
        out[n] = 0;
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
        out[0] = 0;
        return 0;
    }
    return n;
}

int wxb_wx_read_sso_str(int64_t s, char *out, int max) {
    int64_t len64, cap64;
    int len, n;
    const char *src;
    if (!s || !out || max <= 0) return 0;
    out[0] = 0;
    len64 = r64((void *)(s + 0x10));
    cap64 = r64((void *)(s + 0x18));
    if (len64 <= 0 || len64 > 0x100000 || cap64 <= 0) return 0;
    len = (int)len64;
    src = (cap64 >= 0x10) ? (const char *)r64((void *)s) : (const char *)s;
    if (!src) return 0;
    n = (len < max - 1) ? len : max - 1;
    __try {
        for (int i = 0; i < n; i++) out[i] = src[i];
        out[n] = 0;
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
        out[0] = 0;
        return 0;
    }
    return n;
}

int64_t wxb_resolve_revoke_payload(int64_t a3) {
    FnRTDynamicCast cast_fn;
    int64_t obj;
    BYTE *base = wxb_weixin_addr(0);
    if (!base || !a3) return 0;
    cast_fn = (FnRTDynamicCast)(base + WX_OFF_RT_DYNAMIC_CAST);
    obj = r64((void *)(a3 + WX_REVOKE_CTX_OBJ_OFF));
    if (!cast_fn || !obj) return 0;
    __try {
        return cast_fn(
            obj,
            0,
            (int64_t)(base + WX_OFF_REVOKE_RTTI_SRC),
            (int64_t)(base + WX_OFF_REVOKE_RTTI_DST),
            0);
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
        return 0;
    }
}

void wxb_wx_set_str_arena(int64_t header, const char *utf8, int len, BYTE **pool, BYTE *pool_end) {
    if (!utf8) {
        w32((void *)(header + 0x10), 0);
        w32((void *)(header + 0x18), 15);
        return;
    }
    if (len <= 15) {
        copy_bytes((void *)header, utf8, len);
        w32((void *)(header + 0x10), len);
        w32((void *)(header + 0x18), 15);
        return;
    }
    if (*pool + len + 1 >= pool_end) len = (int)(pool_end - *pool - 1);
    if (len < 0) len = 0;
    copy_bytes(*pool, utf8, len);
    (*pool)[len] = 0;
    w64((void *)header, (int64_t)(*pool));
    w32((void *)(header + 0x10), len);
    w32((void *)(header + 0x18), len + 5);
    *pool += len + 16;
}

void wxb_wx_set_std_string_arena(int64_t header, const char *utf8, int len, BYTE **pool, BYTE *pool_end) {
    if (!utf8) utf8 = "";
    if (len < 0) len = lstrlenA(utf8);
    if (len <= 15) {
        copy_bytes((void *)header, utf8, len);
        if (len < 16) w8((void *)(header + len), 0);
        w64((void *)(header + 0x10), len);
        w64((void *)(header + 0x18), 15);
        return;
    }
    if (*pool + len + 1 >= pool_end) len = (int)(pool_end - *pool - 1);
    if (len < 0) len = 0;
    copy_bytes(*pool, utf8, len);
    (*pool)[len] = 0;
    w64((void *)header, (int64_t)(*pool));
    w64((void *)(header + 0x10), len);
    w64((void *)(header + 0x18), len);
    *pool += len + 16;
}

void wxb_wx_set_std_string_heap(int64_t base, int64_t header, const char *utf8) {
    int len, cap;
    int64_t ptr;
    if (!header) return;
    if (!utf8) utf8 = "";
    len = lstrlenA(utf8);
    if (len <= 15) {
        ZeroMemory((void *)header, 16);
        copy_bytes((void *)header, utf8, len);
        w64((void *)(header + 0x10), len);
        w64((void *)(header + 0x18), 15);
        return;
    }
    cap = len | 0xF;
    if (cap < 22) cap = 22;
    ptr = ((FnCall1)(base + WX_OFF_WX_ALLOC))(cap + 1);
    if (!ptr) {
        ZeroMemory((void *)header, 16);
        w64((void *)(header + 0x10), 0);
        w64((void *)(header + 0x18), 15);
        return;
    }
    copy_bytes((void *)ptr, utf8, len);
    w8((void *)(ptr + len), 0);
    w64((void *)header, ptr);
    w64((void *)(header + 0x10), len);
    w64((void *)(header + 0x18), cap);
}

void wxb_wx_free_std_string_heap(int64_t base, int64_t header) {
    int64_t ptr, cap;
    if (!base || !header) return;
    cap = r64((void *)(header + 0x18));
    if (cap < 0x10) return;
    ptr = r64((void *)header);
    if (!ptr) return;
    ((FnCall1)(base + WX_OFF_WX_FREE))(ptr);
    ZeroMemory((void *)header, 16);
    w64((void *)(header + 0x10), 0);
    w64((void *)(header + 0x18), 15);
}

void wxb_wx_set_wstr_arena(int64_t header, const char *utf8, int len, BYTE **pool, BYTE *pool_end) {
    int wlen;
    WCHAR *dst;
    if (!utf8) {
        w32((void *)(header + 0x10), 0);
        w32((void *)(header + 0x18), 15);
        return;
    }
    wlen = MultiByteToWideChar(CP_UTF8, 0, utf8, len, NULL, 0);
    if (wlen <= 0) return;
    if (wlen <= 15) {
        dst = (WCHAR *)header;
        MultiByteToWideChar(CP_UTF8, 0, utf8, len, dst, 15);
        w32((void *)(header + 0x10), wlen);
        w32((void *)(header + 0x18), 15);
        return;
    }
    if (*pool + ((SIZE_T)wlen + 1) * sizeof(WCHAR) >= pool_end) {
        wlen = (int)((pool_end - *pool) / sizeof(WCHAR)) - 1;
    }
    if (wlen < 0) wlen = 0;
    dst = (WCHAR *)(*pool);
    MultiByteToWideChar(CP_UTF8, 0, utf8, len, dst, wlen);
    dst[wlen] = 0;
    w64((void *)header, (int64_t)dst);
    w32((void *)(header + 0x10), wlen);
    w32((void *)(header + 0x18), wlen + 5);
    *pool += ((SIZE_T)wlen + 1) * sizeof(WCHAR) + 16;
}

void wxb_set_utf8_ptr_fields(int64_t st, int off_ptr, int off_chars, int off_bytes,
                             const char *s, BYTE **pool, BYTE *pool_end) {
    int len = s ? lstrlenA(s) : 0;
    BYTE *dst = *pool;
    if (!s) s = "";
    if (*pool + len + 1 >= pool_end) len = (int)(pool_end - *pool - 1);
    if (len < 0) len = 0;
    copy_bytes(dst, s, len);
    dst[len] = 0;
    w64((void *)(st + off_ptr), (int64_t)dst);
    w64((void *)(st + off_chars), len);
    w64((void *)(st + off_bytes), len);
    *pool += len + 16;
}
