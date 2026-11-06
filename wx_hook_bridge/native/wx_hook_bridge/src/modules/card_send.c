/*
 * Layer: native appmsg/card send bridge
 * Source: ../../../../wx_hook_bridge.dll.c lines 4327-4451
 * This file is included by ../wx_hook_bridge.c; do not compile it as a separate translation unit yet.
 */

#include <time.h>

#include "../include/wx_bridge_send_api.h"
#include "../include/wx_bridge_debug_log.h"
#include "../include/wx_bridge_memory.h"
#include "../include/wx_bridge_offsets.h"
#include "../include/wx_bridge_forward_builders.h"
#include "../include/wx_bridge_send_runtime.h"
#include "../include/wx_bridge_runtime.h"
#include "../include/wx_bridge_native_call.h"
#include "../include/wx_bridge_wechat_string.h"

/* ===================== 原生 appmsg 卡片发送/转发 (send_card) =====================
 * 复用已验证的 CALL2/CALL3 发送路径(微信据此创建并注册真实外发 model,无 submit ready 门槛)。
 * 卡片内容对象 = 微信"富 appmsg payload"(vtable RVA 0x80D1278),由 sub_1809DE610(inner) 直接构造;
 * 其字段偏移与序列化器 sub_180A19440 实读完全一致(x64dbg 实测「晴天」音乐卡 2026-06-03,
 * 详见 docs/wechat-native-appmsg-card-fields.md):
 *   +0x08=1  +0x0C=appmsg subtype  +0xB0=目标会话  +0x1C0 title  +0x200 des  +0x220 action
 *   +0x240 showtype(dword)  +0x288 url  +0x2A8 lowurl  +0x2C8 dataurl  +0x3E0 thumburl
 *   +0x470 songalbumurl  +0x4D0 appid  +0x4F8 sourceusername  +0x518 sourcedisplayname
 * holder(vt 0x80D11E8)+inner(holder+0x10);沿用 send_message_inner 的 {inner,inner} 假控制块策略
 * (refcount 永不归零、不析构),所有字符串落 arena 由 retain_arena 保活。
 */
int send_card(const char *to_wxid, const char *title, const char *des,
                     int appmsg_subtype, const char *action,
                     const char *url, const char *dataurl,
                     const char *songalbumurl, const char *thumburl,
                     const char *appid, const char *sourceusername,
                     const char *sourcedisplayname) {
    if (!wxb_weixin_module() || !to_wxid || !title) return 0;

    const SIZE_T arena_size = 0x30000;
    BYTE *arena = (BYTE *)VirtualAlloc(NULL, arena_size, MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE);
    if (!arena) return 0;
    ZeroMemory(arena, arena_size);

    int64_t base   = (int64_t)wxb_weixin_module();
    int64_t holder = (int64_t)(arena + 0x0000);
    int64_t inner  = holder + 0x10;
    int64_t rcxs   = (int64_t)(arena + 0x1000);
    BYTE *buf1 = arena + 0x1100;
    BYTE *buf2 = arena + 0x1200;
    BYTE *buf3 = arena + 0x1300;
    BYTE *buf4 = arena + 0x1400;
    BYTE *buf5 = arena + 0x1500;
    BYTE *buf6 = arena + 0x1600;
    BYTE *pool = arena + 0x8000;
    BYTE *pool_end = arena + arena_size;
    int ok = 0;

    __try {
        char dbg[1024];
        if (!action || !action[0]) action = "view";
        if (!des) des = "";
        if (!url) url = "";
        if (!dataurl) dataurl = "";
        if (!songalbumurl) songalbumurl = "";
        if (!thumburl) thumburl = "";
        if (!appid) appid = "";
        if (!sourceusername) sourceusername = "";
        if (!sourcedisplayname) sourcedisplayname = "";

        /* 1) holder + 富 appmsg inner (调微信自带 ctor 设 vtable 0x80D1278 + 初始化 std::string) */
        w64((void *)holder, base + WX_OFF_APPMSG_HOLDER_VT);
        w64((void *)(holder + 0x08), 0x100000001LL);
        ((FnCall1)(base + WX_OFF_APPMSG_CTOR))(inner);
        if (r64((void *)inner) != base + WX_OFF_APPMSG_INNER_VT) {
            wsprintfA(dbg, "[card] ctor bad vt=%I64x expected=%I64x",
                      (unsigned __int64)r64((void *)inner),
                      (unsigned __int64)(base + WX_OFF_APPMSG_INNER_VT));
            quote_dbg(dbg);
            __leave;
        }
        w64((void *)(holder + 0x18), inner);   /* 假控制块 = inner,同 send_message_inner */
        w64((void *)(holder + 0x20), holder);

        /* 2) 目标会话 + appmsg subtype */
        wx_set_str_arena(inner + 0xB0, to_wxid, lstrlenA(to_wxid), &pool, pool_end);
        w32((void *)(inner + 0x08), 1);
        w32((void *)(inner + 0x0C), appmsg_subtype);

        /* 3) 卡片结构化字段(序列化器 A19440 实读偏移) */
        wx_set_str_arena(inner + 0x1C0, title, lstrlenA(title), &pool, pool_end);
        wx_set_str_arena(inner + 0x200, des, lstrlenA(des), &pool, pool_end);
        wx_set_str_arena(inner + 0x220, action, lstrlenA(action), &pool, pool_end);
        w32((void *)(inner + 0x240), 0);   /* showtype */
        wx_set_str_arena(inner + 0x288, url, lstrlenA(url), &pool, pool_end);
        wx_set_str_arena(inner + 0x2C8, dataurl, lstrlenA(dataurl), &pool, pool_end);
        wx_set_str_arena(inner + 0x3E0, thumburl, lstrlenA(thumburl), &pool, pool_end);
        wx_set_str_arena(inner + 0x470, songalbumurl, lstrlenA(songalbumurl), &pool, pool_end);
        wx_set_str_arena(inner + 0x4D0, appid, lstrlenA(appid), &pool, pool_end);
        wx_set_str_arena(inner + 0x4F8, sourceusername, lstrlenA(sourceusername), &pool, pool_end);
        wx_set_str_arena(inner + 0x518, sourcedisplayname, lstrlenA(sourcedisplayname), &pool, pool_end);

        wsprintfA(dbg,
                  "[card] inner=%I64x vt=%I64x sub=%d to=%s titleSz=%d desSz=%d urlSz=%d dataurlSz=%d albumSz=%d appidSz=%d",
                  (unsigned __int64)inner, (unsigned __int64)r64((void *)inner),
                  appmsg_subtype, to_wxid,
                  r32((void *)(inner + 0x1C0 + 0x10)), r32((void *)(inner + 0x200 + 0x10)),
                  r32((void *)(inner + 0x288 + 0x10)), r32((void *)(inner + 0x2C8 + 0x10)),
                  r32((void *)(inner + 0x470 + 0x10)), r32((void *)(inner + 0x4D0 + 0x10)));
        quote_dbg(dbg);

        /* 4) 经与文本完全相同的 CALL2 -> CALL3 提交 */
        w64((void *)rcxs, base + WX_OFF_SEND_CALL2);
        w64(buf1, inner);
        w64(buf1 + 8, inner);
        w64((void *)(rcxs + 8), (int64_t)buf1);
        w64((void *)(rcxs + 16), (int64_t)buf1 + 16);
        w64((void *)(rcxs + 24), (int64_t)buf1 + 16);
        w64(buf3, base + WX_OFF_SEND_CALL2_HC1);
        w64(buf3 + 56, (int64_t)buf3);
        w64(buf4, base + WX_OFF_SEND_CALL2_HC3);
        w64(buf4 + 56, (int64_t)buf4);
        w64(buf5, base + WX_OFF_SEND_CALL2_HC2);
        w64(buf5 + 56, (int64_t)buf5);
        (void)buf6;
        int64_t rax = ((FnCall2)(base + WX_OFF_SEND_CALL2))(
            (int64_t)buf2, (int64_t)buf3, (int64_t)buf4, (int64_t)buf5, (int64_t)buf6);
        wsprintfA(dbg, "[card] CALL2 rax=%I64x", (unsigned __int64)rax);
        quote_dbg(dbg);
        if (rax) {
            ((FnCall3)(base + WX_OFF_SEND_CALL3))(rcxs, rax);
            quote_dbg("[card] CALL3 done");
            ok = 1;
        }
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
        quote_dbg("[card] EXCEPTION");
        ok = 0;
    }

    retain_arena(arena);
    return ok;
}

int send_appmsg_xml(const char *to_wxid, const char *xml, int appmsg_subtype,
                    const char *source_from_wxid,
                    const char *source_to_wxid,
                    const char *source_sender_wxid) {
    if (!wxb_weixin_module() || !to_wxid || !to_wxid[0] || !xml || !xml[0]) return 0;

    const SIZE_T arena_size = 0x50000;
    BYTE *arena = (BYTE *)VirtualAlloc(NULL, arena_size, MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE);
    if (!arena) return 0;
    ZeroMemory(arena, arena_size);

    int64_t base = (int64_t)wxb_weixin_module();
    int64_t holder = (int64_t)(arena + 0x0000);
    int64_t inner = holder + 0x10;
    int64_t src_ctl = (int64_t)(arena + 0x3000);
    int64_t src_msg = (int64_t)(arena + 0x3400);
    int64_t rcxs = (int64_t)(arena + 0x5000);
    BYTE *buf1 = arena + 0x5100;
    BYTE *buf2 = arena + 0x5200;
    BYTE *buf3 = arena + 0x5300;
    BYTE *buf4 = arena + 0x5400;
    BYTE *buf5 = arena + 0x5500;
    BYTE *buf6 = arena + 0x5600;
    BYTE *pool = arena + 0x9000;
    BYTE *pool_end = arena + arena_size;
    int ok = 0;

    __try {
        char dbg[1024];
        const char *self_wxid = wxb_self_wxid()[0] ? wxb_self_wxid() : source_to_wxid;
        const char *from_wxid = (source_from_wxid && source_from_wxid[0]) ? source_from_wxid : self_wxid;
        const char *sender_wxid = (source_sender_wxid && source_sender_wxid[0]) ? source_sender_wxid : from_wxid;
        if (!self_wxid) self_wxid = "";
        if (!from_wxid) from_wxid = "";
        if (!sender_wxid) sender_wxid = "";
        if (!appmsg_subtype) appmsg_subtype = 63;

        w64((void *)holder, base + WX_OFF_APPMSG_HOLDER_VT);
        w32((void *)(holder + 0x08), 0x100000);
        w32((void *)(holder + 0x0C), 0x100000);
        ((FnCall1)(base + WX_OFF_APPMSG_CTOR))(inner);
        if (r64((void *)inner) != base + WX_OFF_APPMSG_INNER_VT) {
            wsprintfA(dbg, "[appmsg-xml] ctor bad vt=%I64x expected=%I64x",
                      (unsigned __int64)r64((void *)inner),
                      (unsigned __int64)(base + WX_OFF_APPMSG_INNER_VT));
            quote_dbg(dbg);
            __leave;
        }
        /*
         * holder is the shared_ptr control block and inner starts at holder+0x10.
         * Do not write holder+0x18/+0x20 here: those are inner+0x08/+0x10,
         * not control-block fields, and corrupt parser-owned appmsg state.
         */

        forward_build_fake_request_msg(base, src_ctl, src_msg,
                                       from_wxid,
                                       self_wxid,
                                       sender_wxid,
                                       xml,
                                       "",
                                       49,
                                       appmsg_subtype,
                                       0,
                                       0,
                                       (int32_t)time(NULL),
                                       &pool,
                                       pool_end);
        {
            int parse_ret = (int)((FnCall2)(base + WX_OFF_APPMSG_XML_PARSE))(inner, src_msg, 0, 0, 0);
            wx_set_str_arena(inner + 0xB0, to_wxid, lstrlenA(to_wxid), &pool, pool_end);
            if (!r32((void *)(inner + 0x0C))) {
                w32((void *)(inner + 0x0C), appmsg_subtype);
            }
            w32((void *)(inner + 0x08), 1);
            wsprintfA(dbg,
                      "[appmsg-xml] parse_ret=%d inner=%I64x vt=%I64x type=%d/%d to=%s xmlSz=%d finderSlots={%I64x,%I64x,%I64x,%I64x,%I64x,%I64x}",
                      parse_ret,
                      (unsigned __int64)inner,
                      (unsigned __int64)r64((void *)inner),
                      r32((void *)(inner + 0x08)),
                      r32((void *)(inner + 0x0C)),
                      to_wxid,
                      lstrlenA(xml),
                      (unsigned __int64)r64((void *)(inner + 0x5C8)),
                      (unsigned __int64)r64((void *)(inner + 0x5D0)),
                      (unsigned __int64)r64((void *)(inner + 0x5B8)),
                      (unsigned __int64)r64((void *)(inner + 0x5C0)),
                      (unsigned __int64)r64((void *)(inner + 0x5A8)),
                      (unsigned __int64)r64((void *)(inner + 0x5B0)));
            quote_dbg(dbg);
            if (!parse_ret) {
                __leave;
            }
        }

        w64((void *)rcxs, base + WX_OFF_SEND_CALL2);
        w64(buf1, inner);
        w64(buf1 + 8, holder);
        w64((void *)(rcxs + 8), (int64_t)buf1);
        w64((void *)(rcxs + 16), (int64_t)buf1 + 16);
        w64((void *)(rcxs + 24), (int64_t)buf1 + 16);
        w64(buf3, base + WX_OFF_SEND_CALL2_HC1);
        w64(buf3 + 56, (int64_t)buf3);
        w64(buf4, base + WX_OFF_SEND_CALL2_HC3);
        w64(buf4 + 56, (int64_t)buf4);
        w64(buf5, base + WX_OFF_SEND_CALL2_HC2);
        w64(buf5 + 56, (int64_t)buf5);
        (void)buf6;
        int64_t rax = ((FnCall2)(base + WX_OFF_SEND_CALL2))(
            (int64_t)buf2, (int64_t)buf3, (int64_t)buf4, (int64_t)buf5, (int64_t)buf6);
        wsprintfA(dbg, "[appmsg-xml] CALL2 rax=%I64x", (unsigned __int64)rax);
        quote_dbg(dbg);
        if (rax) {
            ((FnCall3)(base + WX_OFF_SEND_CALL3))(rcxs, rax);
            quote_dbg("[appmsg-xml] CALL3 done");
            ok = 1;
        }
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
        quote_dbg("[appmsg-xml] EXCEPTION");
        ok = 0;
    }

    retain_arena(arena);
    return ok;
}


