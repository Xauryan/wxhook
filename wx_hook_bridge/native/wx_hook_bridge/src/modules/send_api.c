/*
 * Layer: quote reply path plus text/image send entry helpers
 * Source: ../../../../wx_hook_bridge.dll.c lines 4058-4326
 * This file is included by ../wx_hook_bridge.c; do not compile it as a separate translation unit yet.
 */

#include "../include/wx_bridge_send_api.h"
#include "../include/wx_bridge_debug_log.h"
#include "../include/wx_bridge_forward_builders.h"
#include "../include/wx_bridge_forward_retained.h"
#include "../include/wx_bridge_memory.h"
#include "../include/wx_bridge_offsets.h"
#include "../include/wx_bridge_file_io.h"
#include "../include/wx_bridge_paths.h"
#include "../include/wx_bridge_send_runtime.h"
#include "../include/wx_bridge_runtime.h"
#include "../include/wx_bridge_native_call.h"
#include "../include/wx_bridge_text.h"
#include "../include/wx_bridge_voice_probe.h"
#include "../include/wx_bridge_wechat_string.h"
#include <string.h>

/* ===================== 引用消息 (quote reply) — 复用已验证的文本发送路径 =====================
 * 微信4.1.9.23 引用消息 = appmsg type49 / subtype57 + <refermsg>。x64dbg 实测确认:它复用普通
 * 文本的发送对象与发送路径,但 refermsg 是 optional send_item 子对象:
 * 先用 sub_182337CF0 构造 +0x208 子对象,确认 vtable 后置 presence byte(inner+0x590)=1,
 * 随后写 type=49/subtype=57 及字段。
 *
 * 【刻意照搬 send_message_inner 的文本构造方式】(手动建对象 + {inner,inner} shared_ptr),
 * 不用工厂 sub_18063DF30。原因(静态反编译 sub_18063DF30 得出):
 *   工厂用"微信 operator new"建带【真实控制块】的 wrapper 并返回 {inner,wrapper};若把该真实控制块
 *   交给发送队列,微信用完后会【真正析构】对象 -> 释放 inner 各 std::string;而我们 >15B 的长字符串
 *   数据落在 VirtualAlloc 的 arena 里,对其做 CRT delete[] = 非法释放 -> 崩溃/堆损坏。
 *   send_message_inner 用 {inner,inner} 假控制块(refcount 全 0,release 时计数变负、永不析构),
 *   故 arena 字符串安全 —— 文本/图片已实测稳定。引用沿用同一机制,inner 初始化(CALL1_TEXT)完全一致。
 *
 * IDA 静态证据(2026-06-02):
 *   CALL1_TEXT/sub_1806A35B0 -> sub_1806A36B0 只清零 +0x1B0..+0x5FF,不会构造 +0x208。
 *   sub_182337CF0 是 send_item 默认构造器:写 off_187FB6B58,初始化 +0x208 子对象内的 string/字段。
 *   sub_182337C30(a1) 在 presence byte(a1+0x3E0) 为 1 时返回 a1+0x58；调用者传 inner+0x1B0,
 *   因此 quote send_item=inner+0x208, presence byte=inner+0x590。证明 presence+构造缺一不可。
 *
 * 字段偏移(相对内层对象;send_item 位于 inner+0x208。2026-06-02 IDA 序列化路径修正):
 *   +0xB0  接收会话      +0xD8 type=49   +0xDC subtype=57
 *   +0x1B0 optional wrapper base; +0x208 send_item object; +0x590 presence byte
 *   +0x210 refermsg.type (差分铁证:文本引用=1 / 图片引用=3)
 *   +0x218 fromusr(被引所属会话;IDA sub_1821A9660 -> sub_1821AAFC0)
 *   +0x238 source to/self(源消息 msg+0x38;不直接进 refermsg XML)
 *   +0x258 chatusr(被引原发送者;IDA sub_1821A9660 -> sub_1821AB250)
 *   +0x278 send_item key chat(IDA sub_18216E5E0 reads send_item+0x70; real quote object is non-empty)
 *   +0x29C srcMsgLocalid/local id, from source msg+0x104 (IDA sub_1809D8F80)
 *   +0x2A0 source MsgSvrID, from source msg+0x108 (IDA sub_1809D8F80)
 *   +0x2A8 source create-time/key, from source msg+0x110 via the same OWORD copy
 *   +0x2B0 createtime(dword)  +0x2C0 content  +0x2E0 msgsource
 *   +0x320 displayname(空)  +0x660 title(回复正文)
 *
 * 参数:to_wxid 目标会话 / reply_text 回复正文 / quoted_svrid 被引 MsgSvrID / quoted_local_id 被引 local id /
 *      quoted_content 被引原文 /
 *      quoted_from 被引原发送者wxid / quoted_type 被引类型(文本1/图片3/语音34/视频43/链接49) /
 *      createtime 被引时间戳 / quoted_chat 被引所属会话(群=群id,1:1可空->回退to_wxid) /
 *      quoted_msgsource 被引msgsource / source_to_wxid 源消息 to/self(可空->回退to_wxid)
 */
int send_quote_v2(const char *to_wxid, const char *reply_text,
                          int64_t quoted_svrid, int32_t quoted_local_id, const char *quoted_content,
                          const char *quoted_from, int quoted_type, int32_t createtime,
                          const char *quoted_chat, const char *quoted_msgsource, const char *source_to_wxid) {
    if (!wxb_weixin_module() || !to_wxid || !reply_text || !quoted_content || !quoted_from) return 0;
    if (!quoted_svrid || !quoted_local_id || !createtime) {
        char dbg[256];
        wsprintfA(dbg, "[quote-v2] missing source ids: local=%d svrid=%I64d ctime=%d",
                  (int)quoted_local_id, (long long)quoted_svrid, (int)createtime);
        quote_dbg(dbg);
        return 0;
    }

    const SIZE_T arena_size = 0x30000;
    BYTE *arena = (BYTE *)VirtualAlloc(NULL, arena_size, MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE);
    if (!arena) return 0;
    ZeroMemory(arena, arena_size);

    int64_t base  = (int64_t)wxb_weixin_module();
    int64_t msg   = (int64_t)(arena + 0x0000);   /* 外层包装(holder),与 send_message_inner 同 */
    int64_t inner = msg + 0x10;                  /* 内层内容对象;以下字段偏移均相对它 */
    int64_t rcxs  = (int64_t)(arena + 0x1000);
    BYTE *buf1 = arena + 0x1100;
    BYTE *buf2 = arena + 0x1200;
    BYTE *buf3 = arena + 0x1300;
    BYTE *buf4 = arena + 0x1400;
    BYTE *buf5 = arena + 0x1500;
    BYTE *buf6 = arena + 0x1600;
    int64_t src_ctl = (int64_t)(arena + 0x1F00);
    int64_t src_msg = (int64_t)(arena + 0x2000);
    BYTE *pool = arena + 0x8000;
    BYTE *pool_end = arena + arena_size;

    int ok = 0;
    __try {
        char dbg[1024];

        /* 1) 照普通文本建对象(与 send_message_inner 的文本头完全一致) */
        w64((void *)(msg + 0x8), 0x100000001LL);            /* 类型 tag(文本) */
        w64((void *)msg, base + WX_OFF_SEND_HC1_TEXT);      /* 外层 vtable 805E4E8 */
        ((FnCall1)(base + WX_OFF_SEND_CALL1_TEXT))(inner);  /* CALL1文本(6A35B0):设内层 vtable 805E578;不会构造 refermsg send_item */
        w64((void *)(msg + 0x18), inner);                   /* 假控制块 = inner,与文本同 */
        w64((void *)(msg + 0x20), msg);

        const char *refer_chat = (quoted_chat && quoted_chat[0]) ? quoted_chat : to_wxid;
        const char *source_to = (source_to_wxid && source_to_wxid[0]) ? source_to_wxid : to_wxid;

        /* 2) 接收会话 + 把"文本(1)"改成"引用(49/57)" */
        wx_set_str_arena(inner + 0xB0, to_wxid, lstrlenA(to_wxid), &pool, pool_end);
        w64((void *)(inner + 0xD8), 0x3900000031LL);        /* 一次写入 +0xD8=0x31(49) 且 +0xDC=0x39(57) */

        /*
         * 3) 构造 refermsg optional 子对象。
         *    IDA: callers pass inner+0x1B0 to sub_182337C30; it returns a1+0x58 only when byte(a1+0x3E0) is set.
         */
        ((FnCall1)(base + WX_OFF_SEND_ITEM_CTOR))(inner + 0x208);
        if (r64((void *)(inner + 0x208)) != base + WX_OFF_SEND_ITEM_VT) {
            wsprintfA(dbg, "[quote-v2] send_item ctor bad vt=%I64x expected=%I64x",
                      (unsigned __int64)r64((void *)(inner + 0x208)),
                      (unsigned __int64)(base + WX_OFF_SEND_ITEM_VT));
            quote_dbg(dbg);
            __leave;
        }
        w8((void *)(inner + 0x590), 1);

        /* 4) refermsg 各字段(按 IDA 序列化路径写入) */
        w32((void *)(inner + 0x210), quoted_type);                                              /* refermsg.type */
        wx_set_str_arena(inner + 0x218, refer_chat, lstrlenA(refer_chat), &pool, pool_end);      /* fromusr = 被引所属会话 */
        wx_set_str_arena(inner + 0x238, source_to, lstrlenA(source_to), &pool, pool_end);        /* source to/self, not XML refer field */
        wx_set_str_arena(inner + 0x258, quoted_from, lstrlenA(quoted_from), &pool, pool_end);    /* chatusr = 被引原发送者 */
        wx_set_str_arena(inner + 0x278, refer_chat, lstrlenA(refer_chat), &pool, pool_end);      /* send_item+0x70: 发送路径 sub_18216E5E0 会导出该 key */
        w32((void *)(inner + 0x29C), quoted_local_id);                                          /* send_item+0x94: source msg+0x104 */
        w64((void *)(inner + 0x2A0), quoted_svrid);                                             /* send_item+0x98: source MsgSvrID msg+0x108 */
        w64((void *)(inner + 0x2A8), (int64_t)createtime);                                      /* send_item+0xA0: source msg+0x110, not XML svrid */
        w32((void *)(inner + 0x2B0), createtime);                                               /* createtime(dword) */
        wx_set_str_arena(inner + 0x2C0, quoted_content, lstrlenA(quoted_content), &pool, pool_end); /* content */
        wx_set_str_arena(inner + 0x2E0, quoted_msgsource ? quoted_msgsource : "",
                         quoted_msgsource ? lstrlenA(quoted_msgsource) : 0, &pool, pool_end);    /* msgsource */
        wx_set_str_arena(inner + 0x320, "", 0, &pool, pool_end);                                /* displayname(空) */

        /*
         * 5) The refermsg serializer rebuilds XML from live source-message shared_ptrs.
         *    Real UI quote has non-null shared_ptrs at send_item+0x270 and +0x290; leaving
         *    +0x290 NULL crashes in sub_1809DA1F0(NULL, out).
         */
        quote_build_fake_source_msg(base, src_ctl, src_msg, quoted_svrid, quoted_local_id,
                                    quoted_content, quoted_from, quoted_type, createtime,
                                    refer_chat, source_to, quoted_msgsource, &pool, pool_end);
        w64((void *)(inner + 0x478), src_msg);
        w64((void *)(inner + 0x480), src_ctl);
        w64((void *)(inner + 0x498), src_msg);
        w64((void *)(inner + 0x4A0), src_ctl);

        /* 6) 回复正文 */
        wx_set_str_arena(inner + 0x660, reply_text, lstrlenA(reply_text), &pool, pool_end);      /* title */

        wsprintfA(dbg,
                  "[quote-v2] inner=%I64x vt=%I64x tag=%I64x type=%x subtype=%x presence590=%x +0x208=%I64x srcA={%I64x,%I64x} srcB={%I64x,%I64x} local=%d svrid=%I64d ctime=%d",
                  (unsigned __int64)inner, (unsigned __int64)r64((void *)inner),
                  (unsigned __int64)r64((void *)(msg + 8)),
                  r32((void *)(inner + 0xD8)), r32((void *)(inner + 0xDC)),
                  (int)*(volatile BYTE *)(inner + 0x590), (unsigned __int64)r64((void *)(inner + 0x208)),
                  (unsigned __int64)r64((void *)(inner + 0x478)), (unsigned __int64)r64((void *)(inner + 0x480)),
                  (unsigned __int64)r64((void *)(inner + 0x498)), (unsigned __int64)r64((void *)(inner + 0x4A0)),
                  (int)quoted_local_id, (long long)quoted_svrid, (int)createtime);
        quote_dbg(dbg);

        /* 7) 经与文本完全相同的 CALL2 -> CALL3 提交 */
        w64((void *)rcxs, base + WX_OFF_SEND_CALL2);
        w64(buf1, inner);
        w64(buf1 + 8, r64((void *)(msg + 0x18)));   /* = inner(假控制块,同 send_message_inner) */
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
        wsprintfA(dbg, "[quote-v2] CALL2 rax=%I64x item={%I64x,%I64x}",
                  (unsigned __int64)rax, (unsigned __int64)r64(buf1),
                  (unsigned __int64)r64(buf1 + 8));
        quote_dbg(dbg);
        if (rax) {
            ((FnCall3)(base + WX_OFF_SEND_CALL3))(rcxs, rax);
            quote_dbg("[quote-v2] CALL3 done");
            ok = 1;
        }
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
        quote_dbg("[quote-v2] EXCEPTION");
        ok = 0;
    }

    retain_arena(arena);
    return ok;
}

/* ===================== 图片消息引用文字 (image message carrying a refermsg) =====================
 * 与 send_quote_v2 同源:图片的引用复用【同一个】optional send_item 子对象
 * (构造 inner+0x208 + presence inner+0x590),只有两处不同:
 *   1) 外层消息类型 = 3(图片)而非 49/57;图片字段写 inner+0xD8/0xE0/0x168/0x188。
 *   2) 图片序列化器把 refermsg 输出到 <extcommoninfo><refermsg> 而不是 <appmsg><refermsg>,
 *      且只输出 createtime + svrid 两个字段。
 *
 * 客户端实测样本(本机 4.1.9.23,文件传输助手,己方发送 status=2):
 *   <msg><img .../><commenturl/><extcommoninfo><refermsg>
 *     <createtime>1789975996</createtime><svrid>2004859562852108979</svrid>
 *   </refermsg></extcommoninfo></msg>
 * 对照:普通图片 XML 到 <commenturl/> 即结束,不含 <extcommoninfo>。
 * 两个值取自被引消息自身的行:svrid = source.server_id,createtime = source.create_time,
 * 与 send_quote_v2 写在 inner+0x2A0 / inner+0x2B0 的是同两个槽位。
 *
 * 失败不降级:任一来源字段缺失直接返回 0,绝不退化成普通图片。
 */
int send_image_quote(const char *to_wxid, const char *image_path,
                     int64_t quoted_svrid, int32_t quoted_local_id,
                     const char *quoted_content, const char *quoted_from,
                     int quoted_type, int32_t createtime,
                     const char *quoted_chat, const char *quoted_msgsource,
                     const char *source_to_wxid) {
    if (!wxb_weixin_module() || !to_wxid || !to_wxid[0] ||
        !image_path || !image_path[0]) return 0;
    /* 引用必须指向一条真实消息:svrid/localid/createtime 任一缺失都拒绝,不静默降级。 */
    if (!quoted_svrid || !quoted_local_id || !createtime ||
        !quoted_from || !quoted_from[0]) {
        char dbg[256];
        wsprintfA(dbg, "[img-quote] rejected: local=%d svrid=%I64d ctime=%d from='%s'",
                  (int)quoted_local_id, (long long)quoted_svrid, (int)createtime,
                  quoted_from ? quoted_from : "");
        quote_dbg(dbg);
        return 0;
    }
    if (file_size_utf8(image_path) <= 0) {
        quote_dbg("[img-quote] rejected: image path missing or empty");
        return 0;
    }

    const SIZE_T arena_size = 0x30000;
    BYTE *arena = (BYTE *)VirtualAlloc(NULL, arena_size, MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE);
    if (!arena) return 0;
    ZeroMemory(arena, arena_size);

    int64_t base  = (int64_t)wxb_weixin_module();
    int64_t msg   = (int64_t)(arena + 0x0000);
    int64_t inner = msg + 0x10;
    int64_t rcxs  = (int64_t)(arena + 0x1000);
    BYTE *buf1 = arena + 0x1100;
    BYTE *buf2 = arena + 0x1200;
    BYTE *buf3 = arena + 0x1300;
    BYTE *buf4 = arena + 0x1400;
    BYTE *buf5 = arena + 0x1500;
    BYTE *buf6 = arena + 0x1600;
    int64_t src_ctl = (int64_t)(arena + 0x1F00);
    int64_t src_msg = (int64_t)(arena + 0x2000);
    BYTE *pool = arena + 0x8000;
    BYTE *pool_end = arena + arena_size;

    int ok = 0;
    __try {
        char dbg[1024];
        int64_t fsz = file_size_utf8(image_path);
        const char *bname = basename_utf8(image_path);
        const char *refer_chat = (quoted_chat && quoted_chat[0]) ? quoted_chat : to_wxid;
        const char *source_to = (source_to_wxid && source_to_wxid[0]) ? source_to_wxid : to_wxid;

        /* 1) 图片外层,与 send_message_inner(msg_type=3) 构造完全一致 */
        w64((void *)(msg + 0x8), 0x200000006LL);
        w64((void *)msg, base + WX_OFF_SEND_HC1_IMAGE);
        ((FnCall1)(base + WX_OFF_SEND_CALL1_IMAGE))(inner);
        w64((void *)(msg + 0x18), inner);
        w64((void *)(msg + 0x20), msg);

        w64((void *)(msg + 0xE8), 1);
        wx_set_str_arena(inner + 0xB0, to_wxid, lstrlenA(to_wxid), &pool, pool_end);
        wx_set_wstr_arena(inner + 0xE0, image_path, lstrlenA(image_path), &pool, pool_end);
        wx_set_str_arena(inner + 0x168, bname, lstrlenA(bname), &pool, pool_end);
        w64((void *)(inner + 0x188), fsz);
        w64((void *)(inner + 0xD8), 3);   /* 图片;引用路径这里是 0x3900000031(49/57) */

        /* 2) refermsg optional 子对象 —— 与 send_quote_v2 同一套槽位 */
        ((FnCall1)(base + WX_OFF_SEND_ITEM_CTOR))(inner + 0x208);
        if (r64((void *)(inner + 0x208)) != base + WX_OFF_SEND_ITEM_VT) {
            wsprintfA(dbg, "[img-quote] send_item ctor bad vt=%I64x expected=%I64x",
                      (unsigned __int64)r64((void *)(inner + 0x208)),
                      (unsigned __int64)(base + WX_OFF_SEND_ITEM_VT));
            quote_dbg(dbg);
            __leave;
        }
        w8((void *)(inner + 0x590), 1);   /* presence byte:与构造缺一不可 */

        w32((void *)(inner + 0x210), quoted_type);                                            /* 被引消息类型:文字=1 */
        wx_set_str_arena(inner + 0x218, refer_chat, lstrlenA(refer_chat), &pool, pool_end);
        wx_set_str_arena(inner + 0x238, source_to, lstrlenA(source_to), &pool, pool_end);
        wx_set_str_arena(inner + 0x258, quoted_from, lstrlenA(quoted_from), &pool, pool_end);
        wx_set_str_arena(inner + 0x278, refer_chat, lstrlenA(refer_chat), &pool, pool_end);
        w32((void *)(inner + 0x29C), quoted_local_id);                                        /* 被引 local id */
        w64((void *)(inner + 0x2A0), quoted_svrid);                                           /* <svrid> */
        w64((void *)(inner + 0x2A8), (int64_t)createtime);
        w32((void *)(inner + 0x2B0), createtime);                                             /* <createtime> */
        wx_set_str_arena(inner + 0x2C0, quoted_content ? quoted_content : "",
                         quoted_content ? lstrlenA(quoted_content) : 0, &pool, pool_end);
        wx_set_str_arena(inner + 0x2E0, quoted_msgsource ? quoted_msgsource : "",
                         quoted_msgsource ? lstrlenA(quoted_msgsource) : 0, &pool, pool_end);
        wx_set_str_arena(inner + 0x320, "", 0, &pool, pool_end);

        /* 3) refermsg 序列化器依赖 source-message shared_ptr(与 send_quote_v2 同,置空会崩) */
        quote_build_fake_source_msg(base, src_ctl, src_msg, quoted_svrid, quoted_local_id,
                                    quoted_content ? quoted_content : "", quoted_from, quoted_type,
                                    createtime, refer_chat, source_to, quoted_msgsource,
                                    &pool, pool_end);
        w64((void *)(inner + 0x478), src_msg);
        w64((void *)(inner + 0x480), src_ctl);
        w64((void *)(inner + 0x498), src_msg);
        w64((void *)(inner + 0x4A0), src_ctl);

        wsprintfA(dbg,
                  "[img-quote] inner=%I64x vt=%I64x typeD8=%d path='%.300s' size=%I64d "
                  "presence590=%x svrid=%I64d ctime=%d local=%d",
                  (unsigned __int64)inner, (unsigned __int64)r64((void *)inner),
                  r32((void *)(inner + 0xD8)), image_path, (long long)fsz,
                  (int)*(volatile BYTE *)(inner + 0x590),
                  (long long)quoted_svrid, (int)createtime, (int)quoted_local_id);
        quote_dbg(dbg);

        /* 4) 与文本/引用完全相同的 CALL2 -> CALL3 提交 */
        w64((void *)rcxs, base + WX_OFF_SEND_CALL2);
        w64(buf1, inner);
        w64(buf1 + 8, r64((void *)(msg + 0x18)));
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
        wsprintfA(dbg, "[img-quote] CALL2 rax=%I64x item={%I64x,%I64x}",
                  (unsigned __int64)rax, (unsigned __int64)r64(buf1),
                  (unsigned __int64)r64(buf1 + 8));
        quote_dbg(dbg);
        if (rax) {
            ((FnCall3)(base + WX_OFF_SEND_CALL3))(rcxs, rax);
            quote_dbg("[img-quote] CALL3 done");
            ok = 1;
        }
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
        quote_dbg("[img-quote] EXCEPTION");
        ok = 0;
    }

    retain_arena(arena);
    return ok;
}

#define TEXT_HIGH_WXID_MAX 512
#define TEXT_HIGH_CONTENT_MAX 60000
#define IMAGE_HIGH_PATH_MAX 1024
#define SOURCE_VOICE_ARENA_SIZE 0x120000
#define SOURCE_VOICE_PATH_MAX 2048

typedef struct TextHighTask {
    volatile LONG status; /* 0=pending, 2=running, 1=ok, negative=failure */
    int send_type;
    int64_t ret;
    int64_t text_obj;
    int64_t text_ctl;
    int64_t service;
    char wxid[TEXT_HIGH_WXID_MAX];
    char content[TEXT_HIGH_CONTENT_MAX];
} TextHighTask;

typedef struct ImageHighTask {
    volatile LONG status; /* 0=pending, 2=running, 1=ok, negative=failure */
    int64_t ret;
    int64_t image_obj;
    int64_t service;
    char wxid[TEXT_HIGH_WXID_MAX];
    char path[IMAGE_HIGH_PATH_MAX];
} ImageHighTask;

typedef struct SourceImageTask {
    volatile LONG status; /* 0=pending, 2=running, 1=ok, negative=failure */
    int64_t ret;
    int64_t source_obj;
    int64_t source_ctl;
    int64_t out0;
    int64_t out1;
    int64_t out2;
    int64_t out3;
    char wxid[TEXT_HIGH_WXID_MAX];
    char path[IMAGE_HIGH_PATH_MAX];
} SourceImageTask;

typedef struct SourceFileTask {
    volatile LONG status; /* 0=pending, 2=running, 1=ok, negative=failure */
    int64_t ret;
    int64_t source_obj;
    int64_t source_ctl;
    int64_t out0;
    int64_t out1;
    int64_t out2;
    int64_t out3;
    char wxid[TEXT_HIGH_WXID_MAX];
    char path[IMAGE_HIGH_PATH_MAX];
} SourceFileTask;

typedef struct SourceTextTask {
    volatile LONG status; /* 0=pending, 2=running, 1=ok, negative=failure */
    int64_t ret;
    int64_t source_obj;
    int64_t source_ctl;
    int64_t out0;
    int64_t out1;
    int64_t out2;
    int64_t out3;
    char wxid[TEXT_HIGH_WXID_MAX];
    char content[TEXT_HIGH_CONTENT_MAX];
} SourceTextTask;

typedef struct SourceEmojiXmlTask {
    volatile LONG status; /* 0=pending, 2=running, 1=ok, negative=failure */
    int64_t ret;
    int64_t source_obj;
    int64_t source_ctl;
    int64_t payload_obj;
    int64_t payload_ctl;
    int64_t retained_si;
    int64_t out0;
    int64_t out1;
    int64_t out2;
    int64_t out3;
    char wxid[TEXT_HIGH_WXID_MAX];
    char content[TEXT_HIGH_CONTENT_MAX];
} SourceEmojiXmlTask;

typedef struct SourceMusicXmlTask {
    volatile LONG status; /* 0=pending, 2=running, 1=ok, negative=failure */
    int64_t ret;
    int64_t source_obj;
    int64_t source_ctl;
    int64_t out0;
    int64_t out1;
    int64_t out2;
    int64_t out3;
    char wxid[TEXT_HIGH_WXID_MAX];
    char content[TEXT_HIGH_CONTENT_MAX];
} SourceMusicXmlTask;

typedef struct SourceContactCardXmlTask {
    volatile LONG status; /* 0=pending, 2=running, 1=ok, negative=failure */
    int64_t ret;
    int64_t source_obj;
    int64_t source_ctl;
    int64_t payload_obj;
    int64_t payload_ctl;
    int64_t out0;
    int64_t out1;
    int64_t out2;
    int64_t out3;
    char wxid[TEXT_HIGH_WXID_MAX];
    char content[TEXT_HIGH_CONTENT_MAX];
} SourceContactCardXmlTask;

typedef struct SourceFinderFeedXmlTask {
    volatile LONG status; /* 0=pending, 2=running, 1=ok, negative=failure */
    int64_t ret;
    int64_t source_obj;
    int64_t source_ctl;
    int64_t payload_obj;
    int64_t payload_ctl;
    int64_t out0;
    int64_t out1;
    int64_t out2;
    int64_t out3;
    char wxid[TEXT_HIGH_WXID_MAX];
    char content[TEXT_HIGH_CONTENT_MAX];
} SourceFinderFeedXmlTask;

typedef struct SourceVoiceTask {
    volatile LONG status; /* 0=pending, 2=running, 1=ok, negative=failure */
    int64_t ret;
    int64_t source_obj;
    int64_t source_ctl;
    int64_t out0;
    int64_t out1;
    int64_t out2;
    int64_t out3;
    int duration_ms;
    int voice_format;
    char wxid[TEXT_HIGH_WXID_MAX];
    char content[TEXT_HIGH_CONTENT_MAX];
} SourceVoiceTask;

typedef struct VoiceFinalTask {
    volatile LONG status; /* 0=pending, 2=running, 1=ok, negative=failure */
    int64_t ret;
    int64_t holder;
    int64_t state;
    int duration_ms;
    int voice_format;
    int voice_len;
    char cached_target[TEXT_HIGH_WXID_MAX];
    char wxid[TEXT_HIGH_WXID_MAX];
    char content[TEXT_HIGH_CONTENT_MAX];
} VoiceFinalTask;

typedef struct TextOneCallTask {
    volatile LONG status; /* 0=pending, 2=running, 1=ok, negative=failure */
    int use_dispatch;
    int64_t ret;
    int64_t record;
    char wxid[TEXT_HIGH_WXID_MAX];
    char content[TEXT_HIGH_CONTENT_MAX];
} TextOneCallTask;

typedef void (__fastcall *FnSharedCtlVFunc)(int64_t ctl);
typedef int64_t (*FnCall2Args)(int64_t rcx, int64_t rdx);

static void release_shared_ctl(int64_t ctl) {
    if (!ctl) return;
    __try {
        int64_t vt = r64((void *)ctl);
        if (!InterlockedDecrement((volatile LONG *)(ctl + 8))) {
            ((FnSharedCtlVFunc)r64((void *)vt))(ctl);
            if (!InterlockedDecrement((volatile LONG *)(ctl + 12))) {
                ((FnSharedCtlVFunc)r64((void *)(vt + 8)))(ctl);
            }
        }
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
        quote_dbg("[send-high-text] shared release EXCEPTION");
    }
}

static void retain_shared_ctl(int64_t ctl) {
    if (!ctl) return;
    __try {
        InterlockedIncrement((volatile LONG *)(ctl + 8));
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
        quote_dbg("[send-source-xml] shared retain EXCEPTION");
    }
}

static void release_shared_pair(int64_t pair_addr) {
    if (!pair_addr) return;
    release_shared_ctl(r64((void *)(pair_addr + 8)));
    w64((void *)pair_addr, 0);
    w64((void *)(pair_addr + 8), 0);
}

static int build_text_high_queue_asm(BYTE *p, int64_t task_addr, int64_t worker_addr) {
    BYTE *start = p;
    BYTE pro[] = {0x48,0x83,0xEC,0x28};          /* sub rsp, 0x28 */
    BYTE epi[] = {0x48,0x83,0xC4,0x28,0xC3};     /* add rsp, 0x28; ret */
    copy_bytes(p, pro, sizeof(pro)); p += sizeof(pro);
    *p++ = 0x48; *p++ = 0xB9;                    /* mov rcx, imm64 */
    *(int64_t *)p = task_addr; p += 8;
    *p++ = 0x48; *p++ = 0xB8;                    /* mov rax, imm64 */
    *(int64_t *)p = worker_addr; p += 8;
    *p++ = 0xFF; *p++ = 0xD0;                    /* call rax */
    copy_bytes(p, epi, sizeof(epi)); p += sizeof(epi);
    return (int)(p - start);
}

static void text_high_queue_worker(int64_t task_addr) {
    TextHighTask *task = (TextHighTask *)task_addr;
    int64_t base = (int64_t)wxb_weixin_module();
    int64_t text_pair[2] = {0, 0};
    int64_t root_pair[2] = {0, 0};
    int64_t mid_pair[2] = {0, 0};
    int64_t service_pair[2] = {0, 0};
    BYTE out[0x80];
    if (!task || !base) return;
    if (InterlockedCompareExchange(&task->status, 2, 0) != 0) return;
    ZeroMemory(out, sizeof(out));

    __try {
        char dbg[1024];
        char read_to[256] = {0};
        char read_content[256] = {0};
        int64_t inner;
        int64_t ret;

        ((FnCall1)(base + WX_OFF_SEND_FACTORY_TEXT))((int64_t)text_pair);
        inner = text_pair[0];
        task->text_obj = text_pair[0];
        task->text_ctl = text_pair[1];
        if (!inner || !text_pair[1]) {
            quote_dbg("[send-high-text] text factory returned empty pair");
            InterlockedExchange(&task->status, -3);
            __leave;
        }

        wx_set_std_string_heap(base, inner + 0xB0, task->wxid);
        w32((void *)(inner + 0xD8), task->send_type);
        w32((void *)(inner + 0x9C), 1);
        wx_set_std_string_heap(base, inner + 0x660, task->content);

        ((FnCall1)(base + WX_OFF_ROOM_INIT1))((int64_t)root_pair);
        if (!root_pair[0]) {
            quote_dbg("[send-high-text] root service init failed");
            InterlockedExchange(&task->status, -4);
            __leave;
        }
        ((FnCall3)(base + WX_OFF_ROOM_INIT2))(root_pair[0], (int64_t)mid_pair);
        if (!mid_pair[0]) {
            quote_dbg("[send-high-text] mid service init failed");
            InterlockedExchange(&task->status, -5);
            __leave;
        }
        ((FnCall3)(base + WX_OFF_SEND_TEXT_SERVICE))(mid_pair[0], (int64_t)service_pair);
        task->service = service_pair[0];
        if (!service_pair[0]) {
            quote_dbg("[send-high-text] text service resolver failed");
            InterlockedExchange(&task->status, -6);
            __leave;
        }

        wx_read_sso_str(inner + 0xB0, read_to, sizeof(read_to));
        wx_read_sso_str(inner + 0x660, read_content, sizeof(read_content));
        wsprintfA(dbg,
                  "[send-high-text] enter service=%I64x pair={%I64x,%I64x} inner_vt=%I64x to='%s' content_len=%d typeD8=%d flag9C=%d preview='%.180s'",
                  (unsigned __int64)service_pair[0],
                  (unsigned __int64)text_pair[0],
                  (unsigned __int64)text_pair[1],
                  (unsigned __int64)r64((void *)inner),
                  read_to,
                  lstrlenA(task->content),
                  r32((void *)(inner + 0xD8)),
                  r32((void *)(inner + 0x9C)),
                  read_content);
        quote_dbg(dbg);

        ret = ((FnCall3Args)(base + WX_OFF_SEND_TEXT_HIGH_WRAP))(
            service_pair[0],
            (int64_t)out,
            (int64_t)text_pair
        );
        task->ret = ret;
        wsprintfA(dbg,
                  "[send-high-text] returned ret=%I64x out={%I64x,%I64x,%I64x,%I64x}",
                  (unsigned __int64)ret,
                  (unsigned __int64)r64((void *)out),
                  (unsigned __int64)r64((void *)(out + 8)),
                  (unsigned __int64)r64((void *)(out + 0x10)),
                  (unsigned __int64)r64((void *)(out + 0x18)));
        quote_dbg(dbg);
        InterlockedExchange(&task->status, 1);
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
        quote_dbg("[send-high-text] EXCEPTION");
        InterlockedExchange(&task->status, -1);
    }

    release_shared_pair((int64_t)service_pair);
    release_shared_pair((int64_t)mid_pair);
    release_shared_pair((int64_t)root_pair);
    release_shared_pair((int64_t)text_pair);
}

static int send_text_high_experimental_typed(const char *wxid, const char *content, int send_type) {
    const SIZE_T arena_size = 0x20000;
    BYTE *arena;
    BYTE *asm_addr;
    TextHighTask *task;
    int asm_len;
    LONG status;
    char dbg[512];
    int wxid_len;
    int content_len;
    if (!wxb_weixin_module() || !wxid || !content) return 0;
    wxid_len = lstrlenA(wxid);
    content_len = lstrlenA(content);
    if (wxid_len <= 0 || wxid_len >= TEXT_HIGH_WXID_MAX ||
        content_len < 0 || content_len >= TEXT_HIGH_CONTENT_MAX) {
        quote_dbg("[send-high-text] skipped: input too large/empty for experimental path");
        return 0;
    }

    arena = (BYTE *)VirtualAlloc(NULL, arena_size, MEM_COMMIT | MEM_RESERVE, PAGE_EXECUTE_READWRITE);
    if (!arena) return 0;
    ZeroMemory(arena, arena_size);
    asm_addr = arena + 0x200;
    task = (TextHighTask *)(arena + 0x1000);
    task->status = 0;
    task->send_type = send_type;
    safe_copy(task->wxid, sizeof(task->wxid), wxid);
    safe_copy(task->content, sizeof(task->content), content);

    asm_len = build_text_high_queue_asm(asm_addr, (int64_t)task, (int64_t)text_high_queue_worker);
    FlushInstructionCache(GetCurrentProcess(), asm_addr, asm_len);
    wsprintfA(dbg, "[send-high-text] queue to='%s' content_len=%d send_type=%d task=%I64x asm=%I64x",
              task->wxid,
              content_len,
              send_type,
              (unsigned __int64)(int64_t)task,
              (unsigned __int64)(int64_t)asm_addr);
    quote_dbg(dbg);

    queue_send((int64_t)asm_addr);
    for (int i = 0; i < 500; i++) {
        status = InterlockedCompareExchange(&task->status, 0, 0);
        if (status == 1 || status < 0) break;
        Sleep(10);
    }
    status = InterlockedCompareExchange(&task->status, 0, 0);
    if (status == 0) {
        InterlockedCompareExchange(&task->status, -2, 0);
        status = InterlockedCompareExchange(&task->status, 0, 0);
    }
    wsprintfA(dbg, "[send-high-text] queue status=%d ret=%I64x service=%I64x pair={%I64x,%I64x}",
              (int)status,
              (unsigned __int64)task->ret,
              (unsigned __int64)task->service,
              (unsigned __int64)task->text_obj,
              (unsigned __int64)task->text_ctl);
    quote_dbg(dbg);

    retain_arena(arena);
    return status == 1;
}

static int send_text_high_experimental(const char *wxid, const char *content) {
    return send_text_high_experimental_typed(wxid, content, 10000);
}

static void text_onecall_queue_worker(int64_t task_addr) {
    TextOneCallTask *task = (TextOneCallTask *)task_addr;
    int64_t base = (int64_t)wxb_weixin_module();
    BYTE *arena;
    BYTE *record;
    BYTE *empty_arg;
    BYTE *pool;
    BYTE *pool_end;
    if (!task || !base) return;
    if (InterlockedCompareExchange(&task->status, 2, 0) != 0) return;

    arena = ((BYTE *)task) - 0x1000;
    record = arena + 0x2000;
    empty_arg = arena + 0x4800;
    pool = arena + 0x5000;
    pool_end = arena + 0x30000;
    ZeroMemory(record, 0x278);
    ZeroMemory(empty_arg, 0x40);

    __try {
        char dbg[1024];
        char read_to[256] = {0};
        char read_route[256] = {0};
        char read_content[256] = {0};

        /*
         * IDA: sub_18526C760 dispatches by *(int*)record; case 5 calls sub_18526EDF0.
         * sub_18526EDF0 then reads:
         *   +0x158 std::string -> text object +0xB0 (to wxid)
         *   +0x178 std::string -> sub_18005A180 helper input
         *   +0x198 std::string -> text object +0x18 (content)
         */
        w32(record + 0x00, 5);
        wx_set_std_string_arena((int64_t)(record + 0x158), task->wxid, lstrlenA(task->wxid), &pool, pool_end);
        wx_set_std_string_arena((int64_t)(record + 0x178), task->wxid, lstrlenA(task->wxid), &pool, pool_end);
        wx_set_std_string_arena((int64_t)(record + 0x198), task->content, lstrlenA(task->content), &pool, pool_end);
        wx_set_std_string_arena((int64_t)empty_arg, "", 0, &pool, pool_end);
        task->record = (int64_t)record;

        wx_read_sso_str((int64_t)(record + 0x158), read_to, sizeof(read_to));
        wx_read_sso_str((int64_t)(record + 0x178), read_route, sizeof(read_route));
        wx_read_sso_str((int64_t)(record + 0x198), read_content, sizeof(read_content));
        wsprintfA(dbg,
                  "[send-onecall-text] enter fn=%s record=%I64x type=%d to='%s' route='%s' content_len=%d preview='%.180s'",
                  task->use_dispatch ? "18526C760" : "18526EDF0",
                  (unsigned __int64)(int64_t)record,
                  r32(record),
                  read_to,
                  read_route,
                  lstrlenA(task->content),
                  read_content);
        quote_dbg(dbg);

        if (task->use_dispatch) {
            task->ret = ((FnCall3Args)(base + WX_OFF_SEND_ONECALL_DISPATCH))(
                (int64_t)record,
                (int64_t)empty_arg,
                0
            );
        } else {
            task->ret = ((FnCall1)(base + WX_OFF_SEND_ONECALL_TEXT_TASK))((int64_t)record);
        }

        wsprintfA(dbg,
                  "[send-onecall-text] returned ret=%I64x record=%I64x len_to=%I64d len_route=%I64d len_content=%I64d",
                  (unsigned __int64)task->ret,
                  (unsigned __int64)(int64_t)record,
                  (long long)r64(record + 0x168),
                  (long long)r64(record + 0x188),
                  (long long)r64(record + 0x1A8));
        quote_dbg(dbg);
        InterlockedExchange(&task->status, 1);
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
        quote_dbg("[send-onecall-text] EXCEPTION");
        InterlockedExchange(&task->status, -1);
    }
}

static int send_text_onecall_experimental(const char *wxid, const char *content, int use_dispatch) {
    const SIZE_T arena_size = 0x30000;
    BYTE *arena;
    BYTE *asm_addr;
    TextOneCallTask *task;
    int asm_len;
    LONG status;
    char dbg[768];
    int wxid_len;
    int content_len;
    if (!wxb_weixin_module() || !wxid || !content) return 0;
    wxid_len = lstrlenA(wxid);
    content_len = lstrlenA(content);
    if (wxid_len <= 0 || wxid_len >= TEXT_HIGH_WXID_MAX ||
        content_len < 0 || content_len >= TEXT_HIGH_CONTENT_MAX) {
        quote_dbg("[send-onecall-text] skipped: input too large/empty for experimental path");
        return 0;
    }

    arena = (BYTE *)VirtualAlloc(NULL, arena_size, MEM_COMMIT | MEM_RESERVE, PAGE_EXECUTE_READWRITE);
    if (!arena) return 0;
    ZeroMemory(arena, arena_size);
    asm_addr = arena + 0x200;
    task = (TextOneCallTask *)(arena + 0x1000);
    task->status = 0;
    task->use_dispatch = use_dispatch ? 1 : 0;
    safe_copy(task->wxid, sizeof(task->wxid), wxid);
    safe_copy(task->content, sizeof(task->content), content);

    asm_len = build_text_high_queue_asm(asm_addr, (int64_t)task, (int64_t)text_onecall_queue_worker);
    FlushInstructionCache(GetCurrentProcess(), asm_addr, asm_len);
    wsprintfA(dbg,
              "[send-onecall-text] queue fn=%s to='%s' content_len=%d task=%I64x asm=%I64x",
              task->use_dispatch ? "18526C760" : "18526EDF0",
              task->wxid,
              content_len,
              (unsigned __int64)(int64_t)task,
              (unsigned __int64)(int64_t)asm_addr);
    quote_dbg(dbg);

    queue_send((int64_t)asm_addr);
    for (int i = 0; i < 500; i++) {
        status = InterlockedCompareExchange(&task->status, 0, 0);
        if (status == 1 || status < 0) break;
        Sleep(10);
    }
    status = InterlockedCompareExchange(&task->status, 0, 0);
    if (status == 0) {
        InterlockedCompareExchange(&task->status, -2, 0);
        status = InterlockedCompareExchange(&task->status, 0, 0);
    }
    wsprintfA(dbg,
              "[send-onecall-text] queue status=%d ret=%I64x record=%I64x",
              (int)status,
              (unsigned __int64)task->ret,
              (unsigned __int64)task->record);
    quote_dbg(dbg);

    retain_arena(arena);
    return status == 1;
}

static void wx_set_wide_std_string_heap(int64_t base, int64_t header, const char *utf8) {
    int wlen;
    int cap;
    int bytes;
    int64_t ptr;
    if (!header || !base) return;
    if (!utf8) utf8 = "";
    wlen = MultiByteToWideChar(CP_UTF8, 0, utf8, -1, NULL, 0);
    if (wlen <= 0) {
        ZeroMemory((void *)header, 16);
        w64((void *)(header + 0x10), 0);
        w64((void *)(header + 0x18), 7);
        return;
    }
    wlen -= 1; /* exclude terminating NUL */
    if (wlen <= 7) {
        ZeroMemory((void *)header, 16);
        MultiByteToWideChar(CP_UTF8, 0, utf8, -1, (WCHAR *)header, 8);
        w64((void *)(header + 0x10), wlen);
        w64((void *)(header + 0x18), 7);
        return;
    }

    cap = wlen | 7;
    bytes = (cap + 1) * (int)sizeof(WCHAR);
    ptr = ((FnCall1)(base + WX_OFF_WX_ALLOC))(bytes);
    if (!ptr) {
        ZeroMemory((void *)header, 16);
        w64((void *)(header + 0x10), 0);
        w64((void *)(header + 0x18), 7);
        return;
    }
    MultiByteToWideChar(CP_UTF8, 0, utf8, -1, (WCHAR *)ptr, cap + 1);
    ((WCHAR *)ptr)[wlen] = 0;
    w64((void *)header, ptr);
    w64((void *)(header + 0x10), wlen);
    w64((void *)(header + 0x18), cap);
}

static void wx_free_wide_std_string_heap(int64_t base, int64_t header) {
    int64_t ptr;
    int64_t cap;
    if (!base || !header) return;
    cap = r64((void *)(header + 0x18));
    if (cap <= 7) return;
    ptr = r64((void *)header);
    if (!ptr) return;
    ((FnCall1)(base + WX_OFF_WX_FREE))(ptr);
    ZeroMemory((void *)header, 16);
    w64((void *)(header + 0x10), 0);
    w64((void *)(header + 0x18), 7);
}

static void rb_init_head(int64_t head) {
    ZeroMemory((void *)head, 32);
    w64((void *)(head + 0x00), head);
    w64((void *)(head + 0x08), head);
    w64((void *)(head + 0x10), head);
    *(WORD *)(head + 0x18) = 0x0101;
}

static void rb_init_single_node(int64_t node, int64_t head) {
    ZeroMemory((void *)node, 32);
    w64((void *)(node + 0x00), head);
    w64((void *)(node + 0x08), head);
    w64((void *)(node + 0x10), head);
    *(WORD *)(node + 0x18) = 0x0000;
}

static void build_single_target_ctx(BYTE *ctx, const char *wxid, BYTE **pool, BYTE *pool_end) {
    int64_t map_head = (int64_t)(ctx + 0x180);
    int64_t map_node = (int64_t)(ctx + 0x1C0);
    int64_t set_head = (int64_t)(ctx + 0x220);
    int64_t set_node = (int64_t)(ctx + 0x270);

    ZeroMemory(ctx, 0x400);

    rb_init_head(map_head);
    rb_init_single_node(map_node, map_head);
    rb_init_head(set_head);
    rb_init_single_node(set_node, set_head);

    w64((void *)(map_head + 0x00), map_node);
    w64((void *)(map_head + 0x08), map_node);
    w64((void *)(map_head + 0x10), map_node);
    w32((void *)(map_node + 0x20), 2);

    w64((void *)(set_head + 0x00), set_node);
    w64((void *)(set_head + 0x08), set_node);
    w64((void *)(set_head + 0x10), set_node);
    wx_set_std_string_arena(set_node + 0x20, wxid, lstrlenA(wxid), pool, pool_end);

    w64((void *)(map_node + 0x28), set_head);
    w64((void *)(map_node + 0x30), 1);

    w64((void *)(ctx + 0x00), map_head);
    w64((void *)(ctx + 0x08), 1);
    wx_set_std_string_arena((int64_t)(ctx + 0x28), "", 0, pool, pool_end);
}

static void extract_xml_attr_value(const char *xml, const char *attr, char *out, int out_sz) {
    const char *p;
    int attr_len;
    int n = 0;
    if (!out || out_sz <= 0) return;
    out[0] = 0;
    if (!xml || !attr || !attr[0]) return;
    attr_len = lstrlenA(attr);
    for (p = xml; *p; p++) {
        if ((p == xml || p[-1] == ' ' || p[-1] == '<' || p[-1] == '\t') &&
            !strncmp(p, attr, attr_len) && p[attr_len] == '=') {
            char q;
            if (p[attr_len + 1] == '\\' && (p[attr_len + 2] == '"' || p[attr_len + 2] == '\'')) {
                q = p[attr_len + 2];
                p += attr_len + 3;
            } else if (p[attr_len + 1] == '"' || p[attr_len + 1] == '\'') {
                q = p[attr_len + 1];
                p += attr_len + 2;
            } else {
                continue;
            }
            while (*p && *p != q && n + 1 < out_sz) {
                if (p[0] == '\\' && p[1] == q) break;
                out[n++] = *p++;
            }
            out[n] = 0;
            return;
        }
    }
}

static void xml_decode_basic_entities_inplace(char *s) {
    char *r;
    char *w;
    if (!s || !s[0]) return;
    r = s;
    w = s;
    while (*r) {
        if (r[0] == '\\' && (r[1] == '"' || r[1] == '\'' || r[1] == '\\')) {
            *w++ = r[1];
            r += 2;
        } else if (!strncmp(r, "&amp;", 5)) {
            *w++ = '&';
            r += 5;
        } else if (!strncmp(r, "&lt;", 4)) {
            *w++ = '<';
            r += 4;
        } else if (!strncmp(r, "&gt;", 4)) {
            *w++ = '>';
            r += 4;
        } else if (!strncmp(r, "&quot;", 6)) {
            *w++ = '"';
            r += 6;
        } else if (!strncmp(r, "&apos;", 6)) {
            *w++ = '\'';
            r += 6;
        } else {
            *w++ = *r++;
        }
    }
    *w = 0;
}

static const char *find_xml_msg_start(const char *s) {
    const char *p;
    if (!s) return "";
    p = str_find_sub(s, "<msg");
    return p ? p : s;
}

static void extract_xml_tag_value(const char *xml, const char *tag, char *out, int out_sz) {
    char open[64];
    char close[64];
    const char *p;
    const char *start;
    const char *end;
    int tag_len;
    int n;
    if (!out || out_sz <= 0) return;
    out[0] = 0;
    if (!xml || !tag || !tag[0]) return;
    tag_len = lstrlenA(tag);
    if (tag_len <= 0 || tag_len > 48) return;
    wsprintfA(open, "<%s", tag);
    wsprintfA(close, "</%s>", tag);
    p = str_find_sub(xml, open);
    if (!p) return;
    start = strchr(p, '>');
    if (!start) return;
    start++;
    end = str_find_sub(start, close);
    if (!end || end < start) return;
    if (end - start >= 9 && !strncmp(start, "<![CDATA[", 9)) {
        start += 9;
        if (end - start >= 3 && !strncmp(end - 3, "]]>", 3)) end -= 3;
    }
    n = (int)(end - start);
    if (n >= out_sz) n = out_sz - 1;
    if (n > 0) memcpy(out, start, n);
    out[n] = 0;
}

static unsigned int hash32_ascii(const char *s) {
    unsigned int h = 2166136261u;
    if (!s) return h;
    while (*s) {
        h ^= (unsigned char)*s++;
        h *= 16777619u;
    }
    return h;
}

static int xml_attr_i32(const char *xml, const char *attr, int fallback) {
    char buf[32];
    extract_xml_attr_value(xml, attr, buf, sizeof(buf));
    return buf[0] ? (int)a2i64(buf) : fallback;
}

static int64_t make_synthetic_msgid_from_xml(const char *xml) {
    char md5[80];
    unsigned int h;
    extract_xml_attr_value(xml, "md5", md5, sizeof(md5));
    h = hash32_ascii(md5[0] ? md5 : xml);
    return (((int64_t)(unsigned int)GetTickCount()) << 32) | (int64_t)h;
}

static void init_emoji_model_string_slots(int64_t obj) {
    int offs[] = { 0x10, 0x30, 0x50, 0x70, 0x90, 0xB0, 0xD0, 0xF0, 0x110, 0x130 };
    for (int i = 0; i < (int)(sizeof(offs) / sizeof(offs[0])); i++) {
        w64((void *)(obj + offs[i] + 0x10), 0);
        w64((void *)(obj + offs[i] + 0x18), 15);
    }
}

static void set_emoji_payload_attr(int64_t obj, int off, const char *xml, const char *attr,
                                   BYTE **pool, BYTE *pool_end) {
    char value[1024] = {0};
    extract_xml_attr_value(xml, attr, value, sizeof(value));
    wx_set_std_string_arena(obj + off, value, lstrlenA(value), pool, pool_end);
}

static void set_emoji_payload_attr2(int64_t obj, int off, const char *xml,
                                    const char *attr, const char *fallback_attr,
                                    BYTE **pool, BYTE *pool_end) {
    char value[1024] = {0};
    extract_xml_attr_value(xml, attr, value, sizeof(value));
    if (!value[0] && fallback_attr) {
        extract_xml_attr_value(xml, fallback_attr, value, sizeof(value));
    }
    wx_set_std_string_arena(obj + off, value, lstrlenA(value), pool, pool_end);
}

static int xml_attr_i32_2(const char *xml, const char *attr, const char *fallback_attr, int fallback) {
    char buf[32];
    extract_xml_attr_value(xml, attr, buf, sizeof(buf));
    if (!buf[0] && fallback_attr) {
        extract_xml_attr_value(xml, fallback_attr, buf, sizeof(buf));
    }
    return buf[0] ? (int)a2i64(buf) : fallback;
}

static void build_emoji_payload_from_xml(int64_t base, int64_t ctl, const char *xml,
                                         BYTE **pool, BYTE *pool_end,
                                         int64_t *out_obj, int64_t *out_ctl) {
    int64_t obj = ctl + 0x10;
    BYTE *model = *pool;
    if (model + 0x180 >= pool_end) {
        *out_obj = 0;
        *out_ctl = 0;
        return;
    }
    ZeroMemory(model, 0x180);
    *pool += 0x180;

    ZeroMemory((void *)ctl, 0x1B0);
    w64((void *)ctl, base + WX_OFF_EMOJI_PAYLOAD_CTL_VT);
    w32((void *)(ctl + 0x08), 0x100000);
    w32((void *)(ctl + 0x0C), 0x100000);

    /*
     * IDA: sub_18404A2F0(payload, model) copies model+0x10..0x130 into the
     * EmoticonDataInfo payload, sets object vtable off_188755528, and copies
     * model+8 as the payload subtype. sub_1837D1E40 forces subtype 4.
     */
    init_emoji_model_string_slots((int64_t)model);
    w32((void *)(model + 0x08), 4);
    set_emoji_payload_attr((int64_t)model, 0x10, xml, "md5", pool, pool_end);
    set_emoji_payload_attr((int64_t)model, 0x30, xml, "productid", pool, pool_end);
    set_emoji_payload_attr2((int64_t)model, 0x50, xml, "cdnurlstring", "cdnurl", pool, pool_end);
    set_emoji_payload_attr2((int64_t)model, 0x70, xml, "encrypturlstring", "encrypturl", pool, pool_end);
    set_emoji_payload_attr((int64_t)model, 0x90, xml, "aeskey", pool, pool_end);
    set_emoji_payload_attr((int64_t)model, 0xB0, xml, "externurl", pool, pool_end);
    set_emoji_payload_attr((int64_t)model, 0xD0, xml, "externmd5", pool, pool_end);
    set_emoji_payload_attr((int64_t)model, 0xF0, xml, "designerid", pool, pool_end);
    set_emoji_payload_attr2((int64_t)model, 0x110, xml, "thumbimgurl", "thumburl", pool, pool_end);
    set_emoji_payload_attr2((int64_t)model, 0x130, xml, "tpurlstring", "tpurl", pool, pool_end);

    ((FnCall2Args)(base + WX_OFF_EMOJI_PAYLOAD_BUILD))((int64_t)obj, (int64_t)model);
    {
        char desc[512] = {0};
        int width = xml_attr_i32_2(xml, "uiemoticonwidth", "width", 0);
        int height = xml_attr_i32_2(xml, "uiemoticonheight", "height", 0);
        extract_xml_attr_value(xml, "emoticonsvrdescstr", desc, sizeof(desc));
        if (!desc[0]) extract_xml_attr_value(xml, "desc", desc, sizeof(desc));
        if (desc[0]) wx_set_std_string_heap(base, obj + 0x160, desc);
        if (width) w32((void *)(obj + 0x180), width);
        if (height) w32((void *)(obj + 0x184), height);
    }

    *out_obj = obj;
    *out_ctl = ctl;
}

static void source_text_queue_worker(int64_t task_addr) {
    SourceTextTask *task = (SourceTextTask *)task_addr;
    int64_t base = (int64_t)wxb_weixin_module();
    BYTE *arena;
    BYTE *ctx;
    BYTE *pool;
    BYTE *pool_end;
    int64_t source_pair[2] = {0, 0};
    int64_t source_vec[3] = {0, 0, 0};
    BYTE out[0x80];
    BYTE extra[0xA0];
    BYTE cb[0xE0];
    if (!task || !base) return;
    if (InterlockedCompareExchange(&task->status, 2, 0) != 0) return;

    arena = ((BYTE *)task) - 0x1000;
    ctx = arena + 0x4000;
    pool = arena + 0x9000;
    pool_end = arena + 0x50000;
    ZeroMemory(out, sizeof(out));
    ZeroMemory(extra, sizeof(extra));
    ZeroMemory(cb, sizeof(cb));

    __try {
        char dbg[1024];
        char read_content[256] = {0};
        int64_t source;
        int64_t ctl;
        int64_t elem;
        int content_len;

        build_single_target_ctx(ctx, task->wxid, &pool, pool_end);

        ((FnCall1)(base + WX_OFF_SEND_FACTORY_TEXT))((int64_t)source_pair);
        source = source_pair[0];
        ctl = source_pair[1];
        task->source_obj = source;
        task->source_ctl = ctl;
        if (!source || !ctl) {
            quote_dbg("[send-source-text] text source factory returned empty pair");
            InterlockedExchange(&task->status, -3);
            __leave;
        }

        content_len = lstrlenA(task->content);
        w32((void *)(source + 216), 1);
        wx_set_std_string_heap(base, source + 1632, task->content);
        w64((void *)(source + 392), content_len);

        elem = ((FnCall1)(base + WX_OFF_WX_ALLOC))(16);
        if (!elem) {
            quote_dbg("[send-source-text] source vector alloc failed");
            InterlockedExchange(&task->status, -4);
            __leave;
        }
        source_vec[0] = elem;
        source_vec[1] = elem + 16;
        source_vec[2] = elem + 16;
        w64((void *)elem, source);
        InterlockedIncrement((volatile LONG *)(ctl + 8));
        w64((void *)(elem + 8), ctl);

        wx_read_sso_str(source + 1632, read_content, sizeof(read_content));
        wsprintfA(dbg,
                  "[send-source-text] enter fn=1820291D0 source={%I64x,%I64x} vec=%I64x to='%s' len=%d type=%d preview='%.180s'",
                  (unsigned __int64)source,
                  (unsigned __int64)ctl,
                  (unsigned __int64)elem,
                  task->wxid,
                  content_len,
                  r32((void *)(source + 216)),
                  read_content);
        quote_dbg(dbg);

        task->ret = ((FnCall2)(base + WX_OFF_SOURCE_SEND))(
            (int64_t)out,
            (int64_t)ctx,
            (int64_t)source_vec,
            (int64_t)extra,
            (int64_t)cb
        );
        task->out0 = r64(out);
        task->out1 = r64(out + 8);
        task->out2 = r64(out + 0x10);
        task->out3 = r64(out + 0x18);
        wsprintfA(dbg,
                  "[send-source-text] returned ret=%I64x out={%I64x,%I64x,%I64x,%I64x} vec_after={%I64x,%I64x,%I64x}",
                  (unsigned __int64)task->ret,
                  (unsigned __int64)task->out0,
                  (unsigned __int64)task->out1,
                  (unsigned __int64)task->out2,
                  (unsigned __int64)task->out3,
                  (unsigned __int64)source_vec[0],
                  (unsigned __int64)source_vec[1],
                  (unsigned __int64)source_vec[2]);
        quote_dbg(dbg);
        InterlockedExchange(&task->status, 1);
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
        quote_dbg("[send-source-text] EXCEPTION");
        InterlockedExchange(&task->status, -1);
    }

    release_shared_pair((int64_t)source_pair);
}

static int send_text_source_experimental(const char *wxid, const char *content) {
    const SIZE_T arena_size = 0x50000;
    BYTE *arena;
    BYTE *asm_addr;
    SourceTextTask *task;
    int asm_len;
    LONG status;
    char dbg[768];
    int wxid_len;
    int content_len;
    if (!wxb_weixin_module() || !wxid || !content) return 0;
    wxid_len = lstrlenA(wxid);
    content_len = lstrlenA(content);
    if (wxid_len <= 0 || wxid_len >= TEXT_HIGH_WXID_MAX ||
        content_len <= 0 || content_len >= TEXT_HIGH_CONTENT_MAX) {
        quote_dbg("[send-source-text] skipped: bad input/content for source-send path");
        return 0;
    }

    arena = (BYTE *)VirtualAlloc(NULL, arena_size, MEM_COMMIT | MEM_RESERVE, PAGE_EXECUTE_READWRITE);
    if (!arena) return 0;
    ZeroMemory(arena, arena_size);
    asm_addr = arena + 0x200;
    task = (SourceTextTask *)(arena + 0x1000);
    task->status = 0;
    safe_copy(task->wxid, sizeof(task->wxid), wxid);
    safe_copy(task->content, sizeof(task->content), content);

    asm_len = build_text_high_queue_asm(asm_addr, (int64_t)task, (int64_t)source_text_queue_worker);
    FlushInstructionCache(GetCurrentProcess(), asm_addr, asm_len);
    wsprintfA(dbg,
              "[send-source-text] queue to='%s' content_len=%d task=%I64x asm=%I64x",
              task->wxid,
              content_len,
              (unsigned __int64)(int64_t)task,
              (unsigned __int64)(int64_t)asm_addr);
    quote_dbg(dbg);

    queue_send((int64_t)asm_addr);
    for (int i = 0; i < 500; i++) {
        status = InterlockedCompareExchange(&task->status, 0, 0);
        if (status == 1 || status < 0) break;
        Sleep(10);
    }
    status = InterlockedCompareExchange(&task->status, 0, 0);
    if (status == 0) {
        InterlockedCompareExchange(&task->status, -2, 0);
        status = InterlockedCompareExchange(&task->status, 0, 0);
    }
    wsprintfA(dbg,
              "[send-source-text] queue status=%d ret=%I64x source={%I64x,%I64x} out={%I64x,%I64x,%I64x,%I64x}",
              (int)status,
              (unsigned __int64)task->ret,
              (unsigned __int64)task->source_obj,
              (unsigned __int64)task->source_ctl,
              (unsigned __int64)task->out0,
              (unsigned __int64)task->out1,
              (unsigned __int64)task->out2,
              (unsigned __int64)task->out3);
    quote_dbg(dbg);

    retain_arena(arena);
    return status == 1;
}

static void source_image_queue_worker(int64_t task_addr) {
    SourceImageTask *task = (SourceImageTask *)task_addr;
    int64_t base = (int64_t)wxb_weixin_module();
    BYTE *arena;
    BYTE *ctx;
    BYTE *pool;
    BYTE *pool_end;
    int64_t source_pair[2] = {0, 0};
    int64_t source_vec[3] = {0, 0, 0};
    BYTE out[0x80];
    BYTE extra[0xA0];
    BYTE cb[0xE0];
    if (!task || !base) return;
    if (InterlockedCompareExchange(&task->status, 2, 0) != 0) return;

    arena = ((BYTE *)task) - 0x1000;
    ctx = arena + 0x4000;
    pool = arena + 0x9000;
    pool_end = arena + 0x50000;
    ZeroMemory(out, sizeof(out));
    ZeroMemory(extra, sizeof(extra));
    ZeroMemory(cb, sizeof(cb));

    __try {
        char dbg[1024];
        int64_t source;
        int64_t ctl;
        int64_t elem;
        int64_t fsz;

        build_single_target_ctx(ctx, task->wxid, &pool, pool_end);

        ((FnCall1)(base + WX_OFF_SOURCE_IMAGE_FACTORY))((int64_t)source_pair);
        source = source_pair[0];
        ctl = source_pair[1];
        task->source_obj = source;
        task->source_ctl = ctl;
        if (!source || !ctl) {
            quote_dbg("[send-source-image] image source factory returned empty pair");
            InterlockedExchange(&task->status, -3);
            __leave;
        }

        fsz = file_size_utf8(task->path);
        w32((void *)(source + 216), 3);
        wx_set_wide_std_string_heap(base, source + 224, task->path);
        wx_set_std_string_heap(base, source + 360, basename_utf8(task->path));
        w64((void *)(source + 392), fsz);

        elem = ((FnCall1)(base + WX_OFF_WX_ALLOC))(16);
        if (!elem) {
            quote_dbg("[send-source-image] source vector alloc failed");
            InterlockedExchange(&task->status, -4);
            __leave;
        }
        source_vec[0] = elem;
        source_vec[1] = elem + 16;
        source_vec[2] = elem + 16;
        w64((void *)elem, source);
        InterlockedIncrement((volatile LONG *)(ctl + 8));
        w64((void *)(elem + 8), ctl);

        wsprintfA(dbg,
                  "[send-source-image] enter fn=1820291D0 source={%I64x,%I64x} vec=%I64x to='%s' path='%.420s' size=%I64d type=%d",
                  (unsigned __int64)source,
                  (unsigned __int64)ctl,
                  (unsigned __int64)elem,
                  task->wxid,
                  task->path,
                  (long long)fsz,
                  r32((void *)(source + 216)));
        quote_dbg(dbg);

        task->ret = ((FnCall2)(base + WX_OFF_SOURCE_SEND))(
            (int64_t)out,
            (int64_t)ctx,
            (int64_t)source_vec,
            (int64_t)extra,
            (int64_t)cb
        );
        task->out0 = r64(out);
        task->out1 = r64(out + 8);
        task->out2 = r64(out + 0x10);
        task->out3 = r64(out + 0x18);
        wsprintfA(dbg,
                  "[send-source-image] returned ret=%I64x out={%I64x,%I64x,%I64x,%I64x} vec_after={%I64x,%I64x,%I64x}",
                  (unsigned __int64)task->ret,
                  (unsigned __int64)task->out0,
                  (unsigned __int64)task->out1,
                  (unsigned __int64)task->out2,
                  (unsigned __int64)task->out3,
                  (unsigned __int64)source_vec[0],
                  (unsigned __int64)source_vec[1],
                  (unsigned __int64)source_vec[2]);
        quote_dbg(dbg);
        InterlockedExchange(&task->status, 1);
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
        quote_dbg("[send-source-image] EXCEPTION");
        InterlockedExchange(&task->status, -1);
    }

    release_shared_pair((int64_t)source_pair);
}

static int send_image_source_experimental(const char *wxid, const char *path) {
    const SIZE_T arena_size = 0x50000;
    BYTE *arena;
    BYTE *asm_addr;
    SourceImageTask *task;
    int asm_len;
    LONG status;
    char dbg[768];
    int wxid_len;
    int path_len;
    if (!wxb_weixin_module() || !wxid || !path) return 0;
    wxid_len = lstrlenA(wxid);
    path_len = lstrlenA(path);
    if (wxid_len <= 0 || wxid_len >= TEXT_HIGH_WXID_MAX ||
        path_len <= 0 || path_len >= IMAGE_HIGH_PATH_MAX ||
        file_size_utf8(path) <= 0) {
        quote_dbg("[send-source-image] skipped: bad input/path for source-send path");
        return 0;
    }

    arena = (BYTE *)VirtualAlloc(NULL, arena_size, MEM_COMMIT | MEM_RESERVE, PAGE_EXECUTE_READWRITE);
    if (!arena) return 0;
    ZeroMemory(arena, arena_size);
    asm_addr = arena + 0x200;
    task = (SourceImageTask *)(arena + 0x1000);
    task->status = 0;
    safe_copy(task->wxid, sizeof(task->wxid), wxid);
    safe_copy(task->path, sizeof(task->path), path);

    asm_len = build_text_high_queue_asm(asm_addr, (int64_t)task, (int64_t)source_image_queue_worker);
    FlushInstructionCache(GetCurrentProcess(), asm_addr, asm_len);
    wsprintfA(dbg,
              "[send-source-image] queue to='%s' path='%.500s' task=%I64x asm=%I64x",
              task->wxid,
              task->path,
              (unsigned __int64)(int64_t)task,
              (unsigned __int64)(int64_t)asm_addr);
    quote_dbg(dbg);

    queue_send((int64_t)asm_addr);
    for (int i = 0; i < 500; i++) {
        status = InterlockedCompareExchange(&task->status, 0, 0);
        if (status == 1 || status < 0) break;
        Sleep(10);
    }
    status = InterlockedCompareExchange(&task->status, 0, 0);
    if (status == 0) {
        InterlockedCompareExchange(&task->status, -2, 0);
        status = InterlockedCompareExchange(&task->status, 0, 0);
    }
    wsprintfA(dbg,
              "[send-source-image] queue status=%d ret=%I64x source={%I64x,%I64x} out={%I64x,%I64x,%I64x,%I64x}",
              (int)status,
              (unsigned __int64)task->ret,
              (unsigned __int64)task->source_obj,
              (unsigned __int64)task->source_ctl,
              (unsigned __int64)task->out0,
              (unsigned __int64)task->out1,
              (unsigned __int64)task->out2,
              (unsigned __int64)task->out3);
    quote_dbg(dbg);

    retain_arena(arena);
    return status == 1;
}

static void source_file_queue_worker(int64_t task_addr) {
    SourceFileTask *task = (SourceFileTask *)task_addr;
    int64_t base = (int64_t)wxb_weixin_module();
    BYTE *arena;
    BYTE *ctx;
    BYTE *pool;
    BYTE *pool_end;
    int64_t source_pair[2] = {0, 0};
    int64_t source_vec[3] = {0, 0, 0};
    BYTE out[0x80];
    BYTE extra[0xA0];
    BYTE cb[0xE0];
    if (!task || !base) return;
    if (InterlockedCompareExchange(&task->status, 2, 0) != 0) return;

    arena = ((BYTE *)task) - 0x1000;
    ctx = arena + 0x4000;
    pool = arena + 0x9000;
    pool_end = arena + 0x50000;
    ZeroMemory(out, sizeof(out));
    ZeroMemory(extra, sizeof(extra));
    ZeroMemory(cb, sizeof(cb));

    __try {
        char dbg[1024];
        int64_t source;
        int64_t ctl;
        int64_t elem;
        int64_t fsz;

        build_single_target_ctx(ctx, task->wxid, &pool, pool_end);

        ((FnCall1)(base + WX_OFF_SOURCE_FILE_FACTORY))((int64_t)source_pair);
        source = source_pair[0];
        ctl = source_pair[1];
        task->source_obj = source;
        task->source_ctl = ctl;
        if (!source || !ctl) {
            quote_dbg("[send-source-file] file source factory returned empty pair");
            InterlockedExchange(&task->status, -3);
            __leave;
        }

        fsz = file_size_utf8(task->path);
        w64((void *)(source + 216), 0x600000031LL);
        wx_set_wide_std_string_heap(base, source + 224, task->path);
        wx_set_std_string_heap(base, source + 360, basename_utf8(task->path));
        w64((void *)(source + 392), fsz);

        elem = ((FnCall1)(base + WX_OFF_WX_ALLOC))(16);
        if (!elem) {
            quote_dbg("[send-source-file] source vector alloc failed");
            InterlockedExchange(&task->status, -4);
            __leave;
        }
        source_vec[0] = elem;
        source_vec[1] = elem + 16;
        source_vec[2] = elem + 16;
        w64((void *)elem, source);
        InterlockedIncrement((volatile LONG *)(ctl + 8));
        w64((void *)(elem + 8), ctl);

        wsprintfA(dbg,
                  "[send-source-file] enter fn=1820291D0 source={%I64x,%I64x} vec=%I64x to='%s' path='%.420s' size=%I64d type=%I64x tail1664=%I64x",
                  (unsigned __int64)source,
                  (unsigned __int64)ctl,
                  (unsigned __int64)elem,
                  task->wxid,
                  task->path,
                  (long long)fsz,
                  (unsigned __int64)r64((void *)(source + 216)),
                  (unsigned __int64)r64((void *)(source + 1664)));
        quote_dbg(dbg);

        task->ret = ((FnCall2)(base + WX_OFF_SOURCE_SEND))(
            (int64_t)out,
            (int64_t)ctx,
            (int64_t)source_vec,
            (int64_t)extra,
            (int64_t)cb
        );
        task->out0 = r64(out);
        task->out1 = r64(out + 8);
        task->out2 = r64(out + 0x10);
        task->out3 = r64(out + 0x18);
        wsprintfA(dbg,
                  "[send-source-file] returned ret=%I64x out={%I64x,%I64x,%I64x,%I64x} vec_after={%I64x,%I64x,%I64x}",
                  (unsigned __int64)task->ret,
                  (unsigned __int64)task->out0,
                  (unsigned __int64)task->out1,
                  (unsigned __int64)task->out2,
                  (unsigned __int64)task->out3,
                  (unsigned __int64)source_vec[0],
                  (unsigned __int64)source_vec[1],
                  (unsigned __int64)source_vec[2]);
        quote_dbg(dbg);
        InterlockedExchange(&task->status, 1);
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
        quote_dbg("[send-source-file] EXCEPTION");
        InterlockedExchange(&task->status, -1);
    }

    release_shared_pair((int64_t)source_pair);
}

static int send_file_source_experimental(const char *wxid, const char *path) {
    const SIZE_T arena_size = 0x50000;
    BYTE *arena;
    BYTE *asm_addr;
    SourceFileTask *task;
    int asm_len;
    LONG status;
    char dbg[768];
    int wxid_len;
    int path_len;
    if (!wxb_weixin_module() || !wxid || !path) return 0;
    wxid_len = lstrlenA(wxid);
    path_len = lstrlenA(path);
    if (wxid_len <= 0 || wxid_len >= TEXT_HIGH_WXID_MAX ||
        path_len <= 0 || path_len >= IMAGE_HIGH_PATH_MAX ||
        file_size_utf8(path) <= 0) {
        quote_dbg("[send-source-file] skipped: bad input/path for source-send path");
        return 0;
    }

    arena = (BYTE *)VirtualAlloc(NULL, arena_size, MEM_COMMIT | MEM_RESERVE, PAGE_EXECUTE_READWRITE);
    if (!arena) return 0;
    ZeroMemory(arena, arena_size);
    asm_addr = arena + 0x200;
    task = (SourceFileTask *)(arena + 0x1000);
    task->status = 0;
    safe_copy(task->wxid, sizeof(task->wxid), wxid);
    safe_copy(task->path, sizeof(task->path), path);

    asm_len = build_text_high_queue_asm(asm_addr, (int64_t)task, (int64_t)source_file_queue_worker);
    FlushInstructionCache(GetCurrentProcess(), asm_addr, asm_len);
    wsprintfA(dbg,
              "[send-source-file] queue to='%s' path='%.500s' task=%I64x asm=%I64x",
              task->wxid,
              task->path,
              (unsigned __int64)(int64_t)task,
              (unsigned __int64)(int64_t)asm_addr);
    quote_dbg(dbg);

    queue_send((int64_t)asm_addr);
    for (int i = 0; i < 500; i++) {
        status = InterlockedCompareExchange(&task->status, 0, 0);
        if (status == 1 || status < 0) break;
        Sleep(10);
    }
    status = InterlockedCompareExchange(&task->status, 0, 0);
    if (status == 0) {
        InterlockedCompareExchange(&task->status, -2, 0);
        status = InterlockedCompareExchange(&task->status, 0, 0);
    }
    wsprintfA(dbg,
              "[send-source-file] queue status=%d ret=%I64x source={%I64x,%I64x} out={%I64x,%I64x,%I64x,%I64x}",
              (int)status,
              (unsigned __int64)task->ret,
              (unsigned __int64)task->source_obj,
              (unsigned __int64)task->source_ctl,
              (unsigned __int64)task->out0,
              (unsigned __int64)task->out1,
              (unsigned __int64)task->out2,
              (unsigned __int64)task->out3);
    quote_dbg(dbg);

    retain_arena(arena);
    return status == 1;
}

static void source_emoji_queue_worker(int64_t task_addr) {
    SourceFileTask *task = (SourceFileTask *)task_addr;
    int64_t base = (int64_t)wxb_weixin_module();
    BYTE *arena;
    BYTE *ctx;
    BYTE *pool;
    BYTE *pool_end;
    int64_t source_pair[2] = {0, 0};
    int64_t source_vec[3] = {0, 0, 0};
    BYTE meta[32];
    BYTE file_name[32];
    BYTE out[0x80];
    BYTE extra[0xA0];
    BYTE cb[0xE0];
    if (!task || !base) return;
    if (InterlockedCompareExchange(&task->status, 2, 0) != 0) return;

    arena = ((BYTE *)task) - 0x1000;
    ctx = arena + 0x4000;
    pool = arena + 0x9000;
    pool_end = arena + 0x50000;
    ZeroMemory(meta, sizeof(meta));
    ZeroMemory(file_name, sizeof(file_name));
    ZeroMemory(out, sizeof(out));
    ZeroMemory(extra, sizeof(extra));
    ZeroMemory(cb, sizeof(cb));

    __try {
        char dbg[1024];
        int64_t source;
        int64_t ctl;
        int64_t elem;
        int64_t fsz;

        build_single_target_ctx(ctx, task->wxid, &pool, pool_end);

        ((FnCall1)(base + WX_OFF_SOURCE_FILE_SPECIAL_FACTORY))((int64_t)source_pair);
        source = source_pair[0];
        ctl = source_pair[1];
        task->source_obj = source;
        task->source_ctl = ctl;
        if (!source || !ctl) {
            quote_dbg("[send-source-emoji] special source factory returned empty pair");
            InterlockedExchange(&task->status, -3);
            __leave;
        }

        fsz = file_size_utf8(task->path);
        wx_set_wide_std_string_heap(base, (int64_t)file_name, task->path);
        /*
         * 180484770's decompiler signature drops the second argument. 1839986E0
         * calls it as sub_180484770(meta, FileName), and the callee preserves RDX
         * into sub_180483990. Passing only RCX leaves RDX stale and can crash.
         */
        ((FnCall3)(base + WX_OFF_SOURCE_EMOJI_META_BUILDER))((int64_t)meta, (int64_t)file_name);
        w32((void *)(source + 216), 47);
        wx_set_wide_std_string_heap(base, source + 224, task->path);
        wx_set_std_string_heap(base, source + 360, basename_utf8(task->path));
        w64((void *)(source + 392), fsz);
        w64((void *)(source + 1664), r64(meta + 24));

        elem = ((FnCall1)(base + WX_OFF_WX_ALLOC))(16);
        if (!elem) {
            quote_dbg("[send-source-emoji] source vector alloc failed");
            InterlockedExchange(&task->status, -4);
            __leave;
        }
        source_vec[0] = elem;
        source_vec[1] = elem + 16;
        source_vec[2] = elem + 16;
        w64((void *)elem, source);
        InterlockedIncrement((volatile LONG *)(ctl + 8));
        w64((void *)(elem + 8), ctl);

        wsprintfA(dbg,
                  "[send-source-emoji] enter fn=1820291D0 source={%I64x,%I64x} vec=%I64x to='%s' path='%.420s' size=%I64d type=%d tail1664=%I64x",
                  (unsigned __int64)source,
                  (unsigned __int64)ctl,
                  (unsigned __int64)elem,
                  task->wxid,
                  task->path,
                  (long long)fsz,
                  r32((void *)(source + 216)),
                  (unsigned __int64)r64((void *)(source + 1664)));
        quote_dbg(dbg);

        task->ret = ((FnCall2)(base + WX_OFF_SOURCE_SEND))(
            (int64_t)out,
            (int64_t)ctx,
            (int64_t)source_vec,
            (int64_t)extra,
            (int64_t)cb
        );
        task->out0 = r64(out);
        task->out1 = r64(out + 8);
        task->out2 = r64(out + 0x10);
        task->out3 = r64(out + 0x18);
        wsprintfA(dbg,
                  "[send-source-emoji] returned ret=%I64x out={%I64x,%I64x,%I64x,%I64x} vec_after={%I64x,%I64x,%I64x}",
                  (unsigned __int64)task->ret,
                  (unsigned __int64)task->out0,
                  (unsigned __int64)task->out1,
                  (unsigned __int64)task->out2,
                  (unsigned __int64)task->out3,
                  (unsigned __int64)source_vec[0],
                  (unsigned __int64)source_vec[1],
                  (unsigned __int64)source_vec[2]);
        quote_dbg(dbg);
        InterlockedExchange(&task->status, 1);
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
        quote_dbg("[send-source-emoji] EXCEPTION");
        InterlockedExchange(&task->status, -1);
    }

    release_shared_pair((int64_t)source_pair);
    ((FnCall1)(base + WX_OFF_SOURCE_EMOJI_META_DTOR))((int64_t)(meta + 8));
    wx_free_wide_std_string_heap(base, (int64_t)file_name);
}

static int send_emoji_source_experimental(const char *wxid, const char *path) {
    const SIZE_T arena_size = 0x50000;
    BYTE *arena;
    BYTE *asm_addr;
    SourceFileTask *task;
    int asm_len;
    LONG status;
    char dbg[768];
    int wxid_len;
    int path_len;
    if (!wxb_weixin_module() || !wxid || !path) return 0;
    quote_dbg("[send-source-emoji] disabled: ABI is fixed but local type=47 path still sends as a file card; needs real emoji metadata/source chain");
    return 0;
    wxid_len = lstrlenA(wxid);
    path_len = lstrlenA(path);
    if (wxid_len <= 0 || wxid_len >= TEXT_HIGH_WXID_MAX ||
        path_len <= 0 || path_len >= IMAGE_HIGH_PATH_MAX ||
        file_size_utf8(path) <= 0) {
        quote_dbg("[send-source-emoji] skipped: bad input/path for source-send path");
        return 0;
    }

    arena = (BYTE *)VirtualAlloc(NULL, arena_size, MEM_COMMIT | MEM_RESERVE, PAGE_EXECUTE_READWRITE);
    if (!arena) return 0;
    ZeroMemory(arena, arena_size);
    asm_addr = arena + 0x200;
    task = (SourceFileTask *)(arena + 0x1000);
    task->status = 0;
    safe_copy(task->wxid, sizeof(task->wxid), wxid);
    safe_copy(task->path, sizeof(task->path), path);

    asm_len = build_text_high_queue_asm(asm_addr, (int64_t)task, (int64_t)source_emoji_queue_worker);
    FlushInstructionCache(GetCurrentProcess(), asm_addr, asm_len);
    wsprintfA(dbg,
              "[send-source-emoji] queue to='%s' path='%.500s' task=%I64x asm=%I64x",
              task->wxid,
              task->path,
              (unsigned __int64)(int64_t)task,
              (unsigned __int64)(int64_t)asm_addr);
    quote_dbg(dbg);

    queue_send((int64_t)asm_addr);
    for (int i = 0; i < 500; i++) {
        status = InterlockedCompareExchange(&task->status, 0, 0);
        if (status == 1 || status < 0) break;
        Sleep(10);
    }
    status = InterlockedCompareExchange(&task->status, 0, 0);
    if (status == 0) {
        InterlockedCompareExchange(&task->status, -2, 0);
        status = InterlockedCompareExchange(&task->status, 0, 0);
    }
    wsprintfA(dbg,
              "[send-source-emoji] queue status=%d ret=%I64x source={%I64x,%I64x} out={%I64x,%I64x,%I64x,%I64x}",
              (int)status,
              (unsigned __int64)task->ret,
              (unsigned __int64)task->source_obj,
              (unsigned __int64)task->source_ctl,
              (unsigned __int64)task->out0,
              (unsigned __int64)task->out1,
              (unsigned __int64)task->out2,
              (unsigned __int64)task->out3);
    quote_dbg(dbg);

    retain_arena(arena);
    return status == 1;
}

static void source_emoji_xml_queue_worker(int64_t task_addr) {
    SourceEmojiXmlTask *task = (SourceEmojiXmlTask *)task_addr;
    int64_t base = (int64_t)wxb_weixin_module();
    BYTE *arena;
    BYTE *ctx;
    BYTE *pool;
    BYTE *pool_end;
    int64_t source_pair[2] = {0, 0};
    int64_t source_vec[3] = {0, 0, 0};
    BYTE out[0x80];
    BYTE extra[0xA0];
    BYTE cb[0xE0];
    if (!task || !base) return;
    if (InterlockedCompareExchange(&task->status, 2, 0) != 0) return;

    arena = ((BYTE *)task) - 0x1000;
    ctx = arena + 0x4000;
    pool = arena + 0x9000;
    pool_end = arena + SOURCE_VOICE_ARENA_SIZE;
    ZeroMemory(out, sizeof(out));
    ZeroMemory(extra, sizeof(extra));
    ZeroMemory(cb, sizeof(cb));

    __try {
        char dbg[1536];
        char md5[96] = {0};
        char from_wxid[160] = {0};
        char to_wxid[560] = {0};
        char payload_preview[256] = {0};
        const char *xml = find_xml_msg_start(task->content);
        int len_attr;
        int64_t payload_storage;
        int64_t payload_obj;
        int64_t payload_ctl;
        int64_t old_payload_ctl;
        int64_t source;
        int64_t ctl;
        int64_t elem;

        extract_xml_attr_value(xml, "md5", md5, sizeof(md5));
        extract_xml_attr_value(xml, "fromusername", from_wxid, sizeof(from_wxid));
        extract_xml_attr_value(xml, "tousername", to_wxid, sizeof(to_wxid));
        if (!to_wxid[0]) safe_copy(to_wxid, sizeof(to_wxid), task->wxid);
        if (!from_wxid[0]) safe_copy(from_wxid, sizeof(from_wxid), wxb_self_wxid());
        len_attr = xml_attr_i32(xml, "len", lstrlenA(xml));
        payload_storage = (int64_t)(arena + 0x8000);
        build_emoji_payload_from_xml(base, payload_storage, xml, &pool, pool_end, &payload_obj, &payload_ctl);
        task->retained_si = 0;
        task->payload_obj = payload_obj;
        task->payload_ctl = payload_ctl;
        if (!payload_obj || !payload_ctl || !is_readable_ptr(payload_obj, 0x240) || !is_readable_ptr(payload_ctl, 0x10)) {
            wsprintfA(dbg,
                      "[send-source-emoji-xml] built payload invalid payload={%I64x,%I64x}",
                      (unsigned __int64)payload_obj,
                      (unsigned __int64)payload_ctl);
            quote_dbg(dbg);
            InterlockedExchange(&task->status, -7);
            __leave;
        }

        build_single_target_ctx(ctx, task->wxid, &pool, pool_end);

        ((FnCall1)(base + WX_OFF_SOURCE_FILE_SPECIAL_FACTORY))((int64_t)source_pair);
        source = source_pair[0];
        ctl = source_pair[1];
        task->source_obj = source;
        task->source_ctl = ctl;
        if (!source || !ctl) {
            quote_dbg("[send-source-emoji-xml] special source factory returned empty pair");
            InterlockedExchange(&task->status, -8);
            __leave;
        }

        w32((void *)(source + 216), 47);
        if (md5[0]) wx_set_std_string_heap(base, source + 176, md5);
        w64((void *)(source + 392), len_attr);

        retain_shared_ctl(payload_ctl);
        old_payload_ctl = r64((void *)(source + 1640));
        w64((void *)(source + 1632), payload_obj);
        w64((void *)(source + 1640), payload_ctl);
        release_shared_ctl(old_payload_ctl);

        elem = ((FnCall1)(base + WX_OFF_WX_ALLOC))(16);
        if (!elem) {
            quote_dbg("[send-source-emoji-xml] source vector alloc failed");
            InterlockedExchange(&task->status, -9);
            __leave;
        }
        source_vec[0] = elem;
        source_vec[1] = elem + 16;
        source_vec[2] = elem + 16;
        w64((void *)elem, source);
        InterlockedIncrement((volatile LONG *)(ctl + 8));
        w64((void *)(elem + 8), ctl);

        wx_read_sso_str(payload_obj, payload_preview, sizeof(payload_preview));
        wsprintfA(dbg,
                  "[send-source-emoji-xml] enter fn=1820291D0 source={%I64x,%I64x} vec=%I64x to='%s' md5='%s' len=%d payload={%I64x,%I64x} payload_md5='%.180s'",
                  (unsigned __int64)source,
                  (unsigned __int64)ctl,
                  (unsigned __int64)elem,
                  task->wxid,
                  md5,
                  len_attr,
                  (unsigned __int64)payload_obj,
                  (unsigned __int64)payload_ctl,
                  payload_preview);
        quote_dbg(dbg);

        task->ret = ((FnCall2)(base + WX_OFF_SOURCE_SEND))(
            (int64_t)out,
            (int64_t)ctx,
            (int64_t)source_vec,
            (int64_t)extra,
            (int64_t)cb
        );
        task->out0 = r64(out);
        task->out1 = r64(out + 8);
        task->out2 = r64(out + 0x10);
        task->out3 = r64(out + 0x18);
        wsprintfA(dbg,
                  "[send-source-emoji-xml] returned ret=%I64x out={%I64x,%I64x,%I64x,%I64x} vec_after={%I64x,%I64x,%I64x}",
                  (unsigned __int64)task->ret,
                  (unsigned __int64)task->out0,
                  (unsigned __int64)task->out1,
                  (unsigned __int64)task->out2,
                  (unsigned __int64)task->out3,
                  (unsigned __int64)source_vec[0],
                  (unsigned __int64)source_vec[1],
                  (unsigned __int64)source_vec[2]);
        quote_dbg(dbg);
        InterlockedExchange(&task->status, 1);
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
        quote_dbg("[send-source-emoji-xml] EXCEPTION");
        InterlockedExchange(&task->status, -1);
    }

    release_shared_pair((int64_t)source_pair);
}

static int send_emoji_xml_source_experimental(const char *wxid, const char *content) {
    const SIZE_T arena_size = 0x70000;
    BYTE *arena;
    BYTE *asm_addr;
    SourceEmojiXmlTask *task;
    int asm_len;
    LONG status;
    char dbg[768];
    int wxid_len;
    int content_len;
    const char *xml;
    if (!wxb_weixin_module() || !wxid || !content) return 0;
    wxid_len = lstrlenA(wxid);
    content_len = lstrlenA(content);
    xml = find_xml_msg_start(content);
    if (wxid_len <= 0 || wxid_len >= TEXT_HIGH_WXID_MAX ||
        content_len <= 0 || content_len >= TEXT_HIGH_CONTENT_MAX ||
        !xml[0] || !str_has(xml, "<emoji")) {
        quote_dbg("[send-source-emoji-xml] skipped: bad target/content for XML source-send path");
        return 0;
    }

    arena = (BYTE *)VirtualAlloc(NULL, arena_size, MEM_COMMIT | MEM_RESERVE, PAGE_EXECUTE_READWRITE);
    if (!arena) return 0;
    ZeroMemory(arena, arena_size);
    asm_addr = arena + 0x200;
    task = (SourceEmojiXmlTask *)(arena + 0x1000);
    task->status = 0;
    safe_copy(task->wxid, sizeof(task->wxid), wxid);
    safe_copy(task->content, sizeof(task->content), content);

    asm_len = build_text_high_queue_asm(asm_addr, (int64_t)task, (int64_t)source_emoji_xml_queue_worker);
    FlushInstructionCache(GetCurrentProcess(), asm_addr, asm_len);
    wsprintfA(dbg,
              "[send-source-emoji-xml] queue to='%s' content_len=%d task=%I64x asm=%I64x",
              task->wxid,
              content_len,
              (unsigned __int64)(int64_t)task,
              (unsigned __int64)(int64_t)asm_addr);
    quote_dbg(dbg);

    queue_send((int64_t)asm_addr);
    for (int i = 0; i < 700; i++) {
        status = InterlockedCompareExchange(&task->status, 0, 0);
        if (status == 1 || status < 0) break;
        Sleep(10);
    }
    status = InterlockedCompareExchange(&task->status, 0, 0);
    if (status == 0) {
        InterlockedCompareExchange(&task->status, -2, 0);
        status = InterlockedCompareExchange(&task->status, 0, 0);
    }
    wsprintfA(dbg,
              "[send-source-emoji-xml] queue status=%d ret=%I64x source={%I64x,%I64x} payload={%I64x,%I64x} retained_si=%I64x out={%I64x,%I64x,%I64x,%I64x}",
              (int)status,
              (unsigned __int64)task->ret,
              (unsigned __int64)task->source_obj,
              (unsigned __int64)task->source_ctl,
              (unsigned __int64)task->payload_obj,
              (unsigned __int64)task->payload_ctl,
              (unsigned __int64)task->retained_si,
              (unsigned __int64)task->out0,
              (unsigned __int64)task->out1,
              (unsigned __int64)task->out2,
              (unsigned __int64)task->out3);
    quote_dbg(dbg);

    retain_arena(arena);
    return status == 1;
}

static void xml_tag_or_attr_value(const char *xml, const char *tag, const char *attr,
                                  char *out, int out_sz) {
    if (!out || out_sz <= 0) return;
    out[0] = 0;
    if (tag && tag[0]) extract_xml_tag_value(xml, tag, out, out_sz);
    if (!out[0] && attr && attr[0]) extract_xml_attr_value(xml, attr, out, out_sz);
}

static void source_music_set_field(int64_t base, int64_t source, int off, const char *value) {
    wx_set_std_string_heap(base, source + off, value ? value : "");
}

static void source_music_xml_queue_worker(int64_t task_addr) {
    SourceMusicXmlTask *task = (SourceMusicXmlTask *)task_addr;
    int64_t base = (int64_t)wxb_weixin_module();
    BYTE *arena;
    BYTE *ctx;
    BYTE *pool;
    BYTE *pool_end;
    int64_t source_pair[2] = {0, 0};
    int64_t source_vec[3] = {0, 0, 0};
    BYTE out[0x80];
    BYTE extra[0xA0];
    BYTE cb[0xE0];
    if (!task || !base) return;
    if (InterlockedCompareExchange(&task->status, 2, 0) != 0) return;

    arena = ((BYTE *)task) - 0x1000;
    ctx = arena + 0x4000;
    pool = arena + 0x9000;
    pool_end = arena + 0x70000;
    ZeroMemory(out, sizeof(out));
    ZeroMemory(extra, sizeof(extra));
    ZeroMemory(cb, sizeof(cb));

    __try {
        char dbg[1536];
        char title[1024] = {0};
        char des[1024] = {0};
        char action[128] = {0};
        char url[2048] = {0};
        char lowurl[2048] = {0};
        char dataurl[2048] = {0};
        char lowdataurl[2048] = {0};
        char songalbumurl[2048] = {0};
        char thumburl[2048] = {0};
        char songlyric[4096] = {0};
        char appid[256] = {0};
        char appname[256] = {0};
        char sourceusername[256] = {0};
        char sourcedisplayname[256] = {0};
        char statextstr[1024] = {0};
        char type_buf[32] = {0};
        char read_title[256] = {0};
        char read_des[256] = {0};
        char read_url[256] = {0};
        const char *xml = find_xml_msg_start(task->content);
        int appmsg_type = 0;
        int64_t source;
        int64_t ctl;
        int64_t elem;

        extract_xml_tag_value(xml, "type", type_buf, sizeof(type_buf));
        appmsg_type = type_buf[0] ? (int)a2i64(type_buf) : 3;
        extract_xml_tag_value(xml, "title", title, sizeof(title));
        extract_xml_tag_value(xml, "des", des, sizeof(des));
        extract_xml_tag_value(xml, "action", action, sizeof(action));
        extract_xml_tag_value(xml, "url", url, sizeof(url));
        extract_xml_tag_value(xml, "lowurl", lowurl, sizeof(lowurl));
        extract_xml_tag_value(xml, "dataurl", dataurl, sizeof(dataurl));
        extract_xml_tag_value(xml, "lowdataurl", lowdataurl, sizeof(lowdataurl));
        extract_xml_tag_value(xml, "songalbumurl", songalbumurl, sizeof(songalbumurl));
        extract_xml_tag_value(xml, "thumburl", thumburl, sizeof(thumburl));
        extract_xml_tag_value(xml, "songlyric", songlyric, sizeof(songlyric));
        xml_tag_or_attr_value(xml, "appid", "appid", appid, sizeof(appid));
        extract_xml_tag_value(xml, "appname", appname, sizeof(appname));
        extract_xml_tag_value(xml, "sourceusername", sourceusername, sizeof(sourceusername));
        extract_xml_tag_value(xml, "sourcedisplayname", sourcedisplayname, sizeof(sourcedisplayname));
        extract_xml_tag_value(xml, "statextstr", statextstr, sizeof(statextstr));
        xml_decode_basic_entities_inplace(title);
        xml_decode_basic_entities_inplace(des);
        xml_decode_basic_entities_inplace(action);
        xml_decode_basic_entities_inplace(url);
        xml_decode_basic_entities_inplace(lowurl);
        xml_decode_basic_entities_inplace(dataurl);
        xml_decode_basic_entities_inplace(lowdataurl);
        xml_decode_basic_entities_inplace(songalbumurl);
        xml_decode_basic_entities_inplace(thumburl);
        xml_decode_basic_entities_inplace(songlyric);
        xml_decode_basic_entities_inplace(appid);
        xml_decode_basic_entities_inplace(appname);
        xml_decode_basic_entities_inplace(sourceusername);
        xml_decode_basic_entities_inplace(sourcedisplayname);
        xml_decode_basic_entities_inplace(statextstr);
        if (!action[0]) safe_copy(action, sizeof(action), "view");
        if (!thumburl[0] && songalbumurl[0]) safe_copy(thumburl, sizeof(thumburl), songalbumurl);

        build_single_target_ctx(ctx, task->wxid, &pool, pool_end);

        ((FnCall1)(base + WX_OFF_SOURCE_APPMSG_MUSIC_FACTORY))((int64_t)source_pair);
        source = source_pair[0];
        ctl = source_pair[1];
        task->source_obj = source;
        task->source_ctl = ctl;
        if (!source || !ctl) {
            quote_dbg("[send-source-music-xml] music source factory returned empty pair");
            InterlockedExchange(&task->status, -3);
            __leave;
        }

        /*
         * sub_184317570 case 7/0x1D builds this source type. sub_181848B30
         * then maps these source fields into the real appmsg payload:
         *   +0x660 title, +0x680 des, +0x6A0 appid,
         *   +0x6E8 url+lowurl, +0x708 lowdataurl, +0x728 dataurl,
         *   +0x748 songlyric, +0x768 songalbumurl,
         *   +0x790 cdn/tp thumb url, +0x7B0 thumb aeskey.
         */
        w64((void *)(source + 216), 0x300000031LL);
        source_music_set_field(base, source, 0x660, title);
        source_music_set_field(base, source, 0x680, des);
        source_music_set_field(base, source, 0x6A0, appid);
        source_music_set_field(base, source, 0x6E8, url);
        source_music_set_field(base, source, 0x708, lowdataurl);
        source_music_set_field(base, source, 0x728, dataurl);
        source_music_set_field(base, source, 0x748, songlyric);
        source_music_set_field(base, source, 0x768, songalbumurl[0] ? songalbumurl : thumburl);
        w32((void *)(source + 0x788), 0);
        source_music_set_field(base, source, 0x790, thumburl);
        source_music_set_field(base, source, 0x7B0, "");

        elem = ((FnCall1)(base + WX_OFF_WX_ALLOC))(16);
        if (!elem) {
            quote_dbg("[send-source-music-xml] source vector alloc failed");
            InterlockedExchange(&task->status, -4);
            __leave;
        }
        source_vec[0] = elem;
        source_vec[1] = elem + 16;
        source_vec[2] = elem + 16;
        w64((void *)elem, source);
        InterlockedIncrement((volatile LONG *)(ctl + 8));
        w64((void *)(elem + 8), ctl);

        wx_read_sso_str(source + 0x660, read_title, sizeof(read_title));
        wx_read_sso_str(source + 0x680, read_des, sizeof(read_des));
        wx_read_sso_str(source + 0x6E8, read_url, sizeof(read_url));
        wsprintfA(dbg,
                  "[send-source-music-xml] enter fn=1820291D0 source={%I64x,%I64x} vec=%I64x to='%s' packed=%I64x xml_type=%d titleSz=%I64d desSz=%I64d appidSz=%I64d urlSz=%I64d lowdataSz=%I64d dataurlSz=%I64d lyricSz=%I64d albumSz=%I64d thumbSz=%I64d title='%.120s' des='%.120s' url='%.160s'",
                  (unsigned __int64)source,
                  (unsigned __int64)ctl,
                  (unsigned __int64)elem,
                  task->wxid,
                  (unsigned __int64)r64((void *)(source + 216)),
                  appmsg_type,
                  (unsigned __int64)r64((void *)(source + 0x660 + 0x10)),
                  (unsigned __int64)r64((void *)(source + 0x680 + 0x10)),
                  (unsigned __int64)r64((void *)(source + 0x6A0 + 0x10)),
                  (unsigned __int64)r64((void *)(source + 0x6E8 + 0x10)),
                  (unsigned __int64)r64((void *)(source + 0x708 + 0x10)),
                  (unsigned __int64)r64((void *)(source + 0x728 + 0x10)),
                  (unsigned __int64)r64((void *)(source + 0x748 + 0x10)),
                  (unsigned __int64)r64((void *)(source + 0x768 + 0x10)),
                  (unsigned __int64)r64((void *)(source + 0x790 + 0x10)),
                  read_title,
                  read_des,
                  read_url);
        quote_dbg(dbg);

        task->ret = ((FnCall2)(base + WX_OFF_SOURCE_SEND))(
            (int64_t)out,
            (int64_t)ctx,
            (int64_t)source_vec,
            (int64_t)extra,
            (int64_t)cb
        );
        task->out0 = r64(out);
        task->out1 = r64(out + 8);
        task->out2 = r64(out + 0x10);
        task->out3 = r64(out + 0x18);
        wsprintfA(dbg,
                  "[send-source-music-xml] returned ret=%I64x out={%I64x,%I64x,%I64x,%I64x} vec_after={%I64x,%I64x,%I64x}",
                  (unsigned __int64)task->ret,
                  (unsigned __int64)task->out0,
                  (unsigned __int64)task->out1,
                  (unsigned __int64)task->out2,
                  (unsigned __int64)task->out3,
                  (unsigned __int64)source_vec[0],
                  (unsigned __int64)source_vec[1],
                  (unsigned __int64)source_vec[2]);
        quote_dbg(dbg);
        InterlockedExchange(&task->status, 1);
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
        quote_dbg("[send-source-music-xml] EXCEPTION");
        InterlockedExchange(&task->status, -1);
    }

    release_shared_pair((int64_t)source_pair);
}

static int send_music_xml_source_experimental(const char *wxid, const char *content) {
    const SIZE_T arena_size = 0x70000;
    BYTE *arena;
    BYTE *asm_addr;
    SourceMusicXmlTask *task;
    int asm_len;
    LONG status;
    char dbg[768];
    int wxid_len;
    int content_len;
    const char *xml;
    if (!wxb_weixin_module() || !wxid || !content) return 0;
    wxid_len = lstrlenA(wxid);
    content_len = lstrlenA(content);
    xml = find_xml_msg_start(content);
    if (wxid_len <= 0 || wxid_len >= TEXT_HIGH_WXID_MAX ||
        content_len <= 0 || content_len >= TEXT_HIGH_CONTENT_MAX ||
        !xml[0] || !str_has(xml, "<appmsg")) {
        quote_dbg("[send-source-music-xml] skipped: bad target/content for XML source-send path");
        return 0;
    }

    arena = (BYTE *)VirtualAlloc(NULL, arena_size, MEM_COMMIT | MEM_RESERVE, PAGE_EXECUTE_READWRITE);
    if (!arena) return 0;
    ZeroMemory(arena, arena_size);
    asm_addr = arena + 0x200;
    task = (SourceMusicXmlTask *)(arena + 0x1000);
    task->status = 0;
    safe_copy(task->wxid, sizeof(task->wxid), wxid);
    safe_copy(task->content, sizeof(task->content), content);

    asm_len = build_text_high_queue_asm(asm_addr, (int64_t)task, (int64_t)source_music_xml_queue_worker);
    FlushInstructionCache(GetCurrentProcess(), asm_addr, asm_len);
    wsprintfA(dbg,
              "[send-source-music-xml] queue to='%s' content_len=%d task=%I64x asm=%I64x",
              task->wxid,
              content_len,
              (unsigned __int64)(int64_t)task,
              (unsigned __int64)(int64_t)asm_addr);
    quote_dbg(dbg);

    queue_send((int64_t)asm_addr);
    for (int i = 0; i < 700; i++) {
        status = InterlockedCompareExchange(&task->status, 0, 0);
        if (status == 1 || status < 0) break;
        Sleep(10);
    }
    status = InterlockedCompareExchange(&task->status, 0, 0);
    if (status == 0) {
        InterlockedCompareExchange(&task->status, -2, 0);
        status = InterlockedCompareExchange(&task->status, 0, 0);
    }
    wsprintfA(dbg,
              "[send-source-music-xml] queue status=%d ret=%I64x source={%I64x,%I64x} out={%I64x,%I64x,%I64x,%I64x}",
              (int)status,
              (unsigned __int64)task->ret,
              (unsigned __int64)task->source_obj,
              (unsigned __int64)task->source_ctl,
              (unsigned __int64)task->out0,
              (unsigned __int64)task->out1,
              (unsigned __int64)task->out2,
              (unsigned __int64)task->out3);
    quote_dbg(dbg);

    retain_arena(arena);
    return status == 1;
}

static BYTE *source_contact_pool_alloc_aligned(BYTE **pool, BYTE *pool_end, SIZE_T size, SIZE_T align) {
    ULONG_PTR p;
    ULONG_PTR aligned;
    if (!pool || !*pool || !pool_end || !size) return NULL;
    if (!align) align = 16;
    p = (ULONG_PTR)(*pool);
    aligned = (p + align - 1) & ~((ULONG_PTR)align - 1);
    if ((BYTE *)aligned + size >= pool_end) return NULL;
    *pool = (BYTE *)aligned + size;
    return (BYTE *)aligned;
}

static void source_contact_init_full_payload_strings(int64_t obj) {
    int off;
    if (!obj) return;
    for (off = 0x00; off <= 0x180; off += 0x20) {
        w64((void *)(obj + off + 0x18), 15);
    }
    for (off = 0x1A8; off <= 0x388; off += 0x20) {
        w64((void *)(obj + off + 0x18), 15);
    }
}

static void source_contact_xml_attr_decoded(const char *xml, const char *attr, char *out, int out_sz) {
    extract_xml_attr_value(xml, attr, out, out_sz);
    xml_decode_basic_entities_inplace(out);
}

static void source_contact_set_payload_str(int64_t base, int64_t obj, int off, const char *value) {
    wx_set_std_string_heap(base, obj + off, value ? value : "");
}

static int source_contact_build_full_payload(int64_t base, int64_t source, const char *xml,
                                             int64_t *out_obj, int64_t *out_ctl) {
    char username[512] = {0};
    char nickname[512] = {0};
    char bigheadimgurl[1024] = {0};
    char smallheadimgurl[1024] = {0};
    char sex[32] = {0};
    char fullpy[256] = {0};
    char shortpy[256] = {0};
    char alias[256] = {0};
    char imagestatus[32] = {0};
    char scene[32] = {0};
    char province[256] = {0};
    char city[256] = {0};
    char sign[1024] = {0};
    char certflag[64] = {0};
    char certinfo[512] = {0};
    char brand_icon_url[1024] = {0};
    char brand_home_url[1024] = {0};
    char brand_subscript_config_url[1024] = {0};
    char brand_flags[64] = {0};
    char region_code[128] = {0};
    char biznamecardinfo[1024] = {0};
    char antispamticket[1024] = {0};
    int64_t ctl;
    int64_t obj;
    int64_t old_ctl;
    if (out_obj) *out_obj = 0;
    if (out_ctl) *out_ctl = 0;
    if (!base || !source || !xml) return 0;

    source_contact_xml_attr_decoded(xml, "username", username, sizeof(username));
    source_contact_xml_attr_decoded(xml, "nickname", nickname, sizeof(nickname));
    source_contact_xml_attr_decoded(xml, "bigheadimgurl", bigheadimgurl, sizeof(bigheadimgurl));
    source_contact_xml_attr_decoded(xml, "smallheadimgurl", smallheadimgurl, sizeof(smallheadimgurl));
    source_contact_xml_attr_decoded(xml, "sex", sex, sizeof(sex));
    source_contact_xml_attr_decoded(xml, "fullpy", fullpy, sizeof(fullpy));
    source_contact_xml_attr_decoded(xml, "shortpy", shortpy, sizeof(shortpy));
    source_contact_xml_attr_decoded(xml, "alias", alias, sizeof(alias));
    source_contact_xml_attr_decoded(xml, "imagestatus", imagestatus, sizeof(imagestatus));
    source_contact_xml_attr_decoded(xml, "scene", scene, sizeof(scene));
    source_contact_xml_attr_decoded(xml, "province", province, sizeof(province));
    source_contact_xml_attr_decoded(xml, "city", city, sizeof(city));
    source_contact_xml_attr_decoded(xml, "sign", sign, sizeof(sign));
    source_contact_xml_attr_decoded(xml, "certflag", certflag, sizeof(certflag));
    source_contact_xml_attr_decoded(xml, "certinfo", certinfo, sizeof(certinfo));
    source_contact_xml_attr_decoded(xml, "brandIconUrl", brand_icon_url, sizeof(brand_icon_url));
    source_contact_xml_attr_decoded(xml, "brandHomeUrl", brand_home_url, sizeof(brand_home_url));
    source_contact_xml_attr_decoded(xml, "brandSubscriptConfigUrl", brand_subscript_config_url, sizeof(brand_subscript_config_url));
    source_contact_xml_attr_decoded(xml, "brandFlags", brand_flags, sizeof(brand_flags));
    source_contact_xml_attr_decoded(xml, "regionCode", region_code, sizeof(region_code));
    source_contact_xml_attr_decoded(xml, "biznamecardinfo", biznamecardinfo, sizeof(biznamecardinfo));
    source_contact_xml_attr_decoded(xml, "antispamticket", antispamticket, sizeof(antispamticket));
    if (!username[0]) return 0;
    if (!nickname[0]) safe_copy(nickname, sizeof(nickname), username);
    if (!sex[0]) safe_copy(sex, sizeof(sex), "0");
    if (!imagestatus[0]) safe_copy(imagestatus, sizeof(imagestatus), "3");
    if (!scene[0]) safe_copy(scene, sizeof(scene), "17");
    if (!certflag[0]) safe_copy(certflag, sizeof(certflag), "0");
    if (!brand_flags[0]) safe_copy(brand_flags, sizeof(brand_flags), "0");

    ctl = ((FnCall1)(base + WX_OFF_WX_ALLOC))(960);
    if (!ctl) return 0;
    ZeroMemory((void *)ctl, 960);
    obj = ctl + 0x10;

    /*
     * Real type=42 send path:
     *   sub_1837DCCB0(out) -> source+0xD8 = 42
     *   off_187FB7638 full contact payload -> source+0x660/+0x668
     * The older compact payload at source+0x6C0/+0x6C8 is model/parser side and
     * is unsafe for MessageSendSource sends.
     */
    w64((void *)ctl, base + WX_OFF_CONTACT_FULL_CTL_VT);
    w64((void *)(ctl + 0x08), 0x100000001LL);
    source_contact_init_full_payload_strings(obj);

    source_contact_set_payload_str(base, obj, 0x000, smallheadimgurl);
    source_contact_set_payload_str(base, obj, 0x020, bigheadimgurl);
    source_contact_set_payload_str(base, obj, 0x040, nickname);
    source_contact_set_payload_str(base, obj, 0x060, username);
    source_contact_set_payload_str(base, obj, 0x080, sex);
    source_contact_set_payload_str(base, obj, 0x0A0, fullpy);
    source_contact_set_payload_str(base, obj, 0x0C0, shortpy);
    source_contact_set_payload_str(base, obj, 0x0E0, alias);
    source_contact_set_payload_str(base, obj, 0x100, imagestatus);
    source_contact_set_payload_str(base, obj, 0x120, scene);
    source_contact_set_payload_str(base, obj, 0x140, province);
    source_contact_set_payload_str(base, obj, 0x160, city);
    source_contact_set_payload_str(base, obj, 0x180, sign);
    w32((void *)(obj + 0x1A0), (int)a2i64(certflag));
    source_contact_set_payload_str(base, obj, 0x1A8, certinfo);
    source_contact_set_payload_str(base, obj, 0x1C8, brand_icon_url);
    source_contact_set_payload_str(base, obj, 0x1E8, brand_home_url);
    source_contact_set_payload_str(base, obj, 0x208, brand_subscript_config_url);
    source_contact_set_payload_str(base, obj, 0x228, brand_flags);
    source_contact_set_payload_str(base, obj, 0x248, region_code);
    source_contact_set_payload_str(base, obj, 0x268, biznamecardinfo);
    source_contact_set_payload_str(base, obj, 0x288, antispamticket);

    retain_shared_ctl(ctl);
    old_ctl = r64((void *)(source + 0x668));
    w64((void *)(source + 0x660), obj);
    w64((void *)(source + 0x668), ctl);
    release_shared_ctl(old_ctl);

    if (out_obj) *out_obj = obj;
    if (out_ctl) *out_ctl = ctl;
    release_shared_ctl(ctl);
    return 1;
}

static void source_contact_card_xml_queue_worker(int64_t task_addr) {
    SourceContactCardXmlTask *task = (SourceContactCardXmlTask *)task_addr;
    int64_t base = (int64_t)wxb_weixin_module();
    BYTE *arena;
    BYTE *ctx;
    BYTE *pool;
    BYTE *pool_end;
    int64_t source_pair[2] = {0, 0};
    int64_t source_vec[3] = {0, 0, 0};
    BYTE out[0x80];
    BYTE extra[0xA0];
    BYTE cb[0xE0];
    if (!task || !base) return;
    if (InterlockedCompareExchange(&task->status, 2, 0) != 0) return;

    arena = ((BYTE *)task) - 0x1000;
    ctx = arena + 0x4000;
    pool = arena + 0x9000;
    pool_end = arena + 0x50000;
    ZeroMemory(out, sizeof(out));
    ZeroMemory(extra, sizeof(extra));
    ZeroMemory(cb, sizeof(cb));

    __try {
        char dbg[1024];
        char username[256] = {0};
        char nickname[256] = {0};
        char read_content[256] = {0};
        const char *xml = find_xml_msg_start(task->content);
        int64_t source;
        int64_t ctl;
        int64_t elem;
        int content_len;

        extract_xml_attr_value(xml, "username", username, sizeof(username));
        extract_xml_attr_value(xml, "nickname", nickname, sizeof(nickname));
        xml_decode_basic_entities_inplace(username);
        xml_decode_basic_entities_inplace(nickname);

        build_single_target_ctx(ctx, task->wxid, &pool, pool_end);

        ((FnCall1)(base + WX_OFF_SOURCE_CONTACT_FACTORY))((int64_t)source_pair);
        source = source_pair[0];
        ctl = source_pair[1];
        task->source_obj = source;
        task->source_ctl = ctl;
        if (!source || !ctl) {
            quote_dbg("[send-source-contact-card-xml] contact source factory returned empty pair");
            InterlockedExchange(&task->status, -3);
            __leave;
        }

        content_len = lstrlenA(xml);
        w32((void *)(source + 216), 42);
        w64((void *)(source + 392), 0);
        if (!source_contact_build_full_payload(base, source, xml,
                                               &task->payload_obj, &task->payload_ctl)) {
            quote_dbg("[send-source-contact-card-xml] full payload build failed");
            InterlockedExchange(&task->status, -5);
            __leave;
        }

        elem = ((FnCall1)(base + WX_OFF_WX_ALLOC))(16);
        if (!elem) {
            quote_dbg("[send-source-contact-card-xml] source vector alloc failed");
            InterlockedExchange(&task->status, -4);
            __leave;
        }
        source_vec[0] = elem;
        source_vec[1] = elem + 16;
        source_vec[2] = elem + 16;
        w64((void *)elem, source);
        InterlockedIncrement((volatile LONG *)(ctl + 8));
        w64((void *)(elem + 8), ctl);

        wx_read_sso_str(task->payload_obj + 0x60, read_content, sizeof(read_content));
        wsprintfA(dbg,
                  "[send-source-contact-card-xml] enter fn=1820291D0 real-contact source={%I64x,%I64x} vec=%I64x to='%s' type=%d xml_len=%d payload660={%I64x,%I64x} username='%.120s' nickname='%.120s' payload_user='%.160s'",
                  (unsigned __int64)source,
                  (unsigned __int64)ctl,
                  (unsigned __int64)elem,
                  task->wxid,
                  r32((void *)(source + 216)),
                  content_len,
                  (unsigned __int64)task->payload_obj,
                  (unsigned __int64)task->payload_ctl,
                  username,
                  nickname,
                  read_content);
        quote_dbg(dbg);

        task->ret = ((FnCall2)(base + WX_OFF_SOURCE_SEND))(
            (int64_t)out,
            (int64_t)ctx,
            (int64_t)source_vec,
            (int64_t)extra,
            (int64_t)cb
        );
        task->out0 = r64(out);
        task->out1 = r64(out + 8);
        task->out2 = r64(out + 0x10);
        task->out3 = r64(out + 0x18);
        wsprintfA(dbg,
                  "[send-source-contact-card-xml] returned ret=%I64x out={%I64x,%I64x,%I64x,%I64x} vec_after={%I64x,%I64x,%I64x}",
                  (unsigned __int64)task->ret,
                  (unsigned __int64)task->out0,
                  (unsigned __int64)task->out1,
                  (unsigned __int64)task->out2,
                  (unsigned __int64)task->out3,
                  (unsigned __int64)source_vec[0],
                  (unsigned __int64)source_vec[1],
                  (unsigned __int64)source_vec[2]);
        quote_dbg(dbg);
        InterlockedExchange(&task->status, 1);
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
        quote_dbg("[send-source-contact-card-xml] EXCEPTION");
        InterlockedExchange(&task->status, -1);
    }

    release_shared_pair((int64_t)source_pair);
}

static int send_contact_card_xml_source_experimental(const char *wxid, const char *content) {
    const SIZE_T arena_size = 0x50000;
    BYTE *arena;
    BYTE *asm_addr;
    SourceContactCardXmlTask *task;
    int asm_len;
    LONG status;
    char dbg[768];
    int wxid_len;
    int content_len;
    const char *xml;
    if (!wxb_weixin_module() || !wxid || !content) return 0;
    wxid_len = lstrlenA(wxid);
    content_len = lstrlenA(content);
    xml = find_xml_msg_start(content);
    if (wxid_len <= 0 || wxid_len >= TEXT_HIGH_WXID_MAX ||
        content_len <= 0 || content_len >= TEXT_HIGH_CONTENT_MAX ||
        !xml[0] || !str_has(xml, "<msg") || !str_has(xml, "username=")) {
        quote_dbg("[send-source-contact-card-xml] skipped: bad target/content for XML source-send path");
        return 0;
    }

    arena = (BYTE *)VirtualAlloc(NULL, arena_size, MEM_COMMIT | MEM_RESERVE, PAGE_EXECUTE_READWRITE);
    if (!arena) return 0;
    ZeroMemory(arena, arena_size);
    asm_addr = arena + 0x200;
    task = (SourceContactCardXmlTask *)(arena + 0x1000);
    task->status = 0;
    safe_copy(task->wxid, sizeof(task->wxid), wxid);
    safe_copy(task->content, sizeof(task->content), content);

    asm_len = build_text_high_queue_asm(asm_addr, (int64_t)task, (int64_t)source_contact_card_xml_queue_worker);
    FlushInstructionCache(GetCurrentProcess(), asm_addr, asm_len);
    wsprintfA(dbg,
              "[send-source-contact-card-xml] queue to='%s' content_len=%d task=%I64x asm=%I64x",
              task->wxid,
              content_len,
              (unsigned __int64)(int64_t)task,
              (unsigned __int64)(int64_t)asm_addr);
    quote_dbg(dbg);

    queue_send((int64_t)asm_addr);
    for (int i = 0; i < 500; i++) {
        status = InterlockedCompareExchange(&task->status, 0, 0);
        if (status == 1 || status < 0) break;
        Sleep(10);
    }
    status = InterlockedCompareExchange(&task->status, 0, 0);
    if (status == 0) {
        InterlockedCompareExchange(&task->status, -2, 0);
        status = InterlockedCompareExchange(&task->status, 0, 0);
    }
    wsprintfA(dbg,
              "[send-source-contact-card-xml] queue status=%d ret=%I64x source={%I64x,%I64x} payload={%I64x,%I64x} out={%I64x,%I64x,%I64x,%I64x}",
              (int)status,
              (unsigned __int64)task->ret,
              (unsigned __int64)task->source_obj,
              (unsigned __int64)task->source_ctl,
              (unsigned __int64)task->payload_obj,
              (unsigned __int64)task->payload_ctl,
              (unsigned __int64)task->out0,
              (unsigned __int64)task->out1,
              (unsigned __int64)task->out2,
              (unsigned __int64)task->out3);
    quote_dbg(dbg);

    retain_arena(arena);
    return status == 1;
}

static const char *source_finder_xml_section(const char *xml, const char *tag) {
    char open[80];
    const char *p;
    if (!xml) return "";
    if (!tag || !tag[0]) return xml;
    wsprintfA(open, "<%s", tag);
    p = str_find_sub(xml, open);
    return p ? p : xml;
}

static void source_finder_xml_tag_decoded(const char *xml, const char *tag,
                                          char *out, int out_sz) {
    extract_xml_tag_value(xml, tag, out, out_sz);
    xml_decode_basic_entities_inplace(out);
}

static void source_finder_xml_tag_decoded2(const char *xml, const char *tag,
                                           const char *fallback_tag,
                                           char *out, int out_sz) {
    source_finder_xml_tag_decoded(xml, tag, out, out_sz);
    if (!out[0] && fallback_tag && fallback_tag[0]) {
        source_finder_xml_tag_decoded(xml, fallback_tag, out, out_sz);
    }
}

static void source_finder_set_payload_str(int64_t base, int64_t obj, int off, const char *value) {
    wx_set_std_string_heap(base, obj + off, value ? value : "");
}

static void source_finder_init_string_slots(int64_t obj, int slot_count) {
    int i;
    if (!obj || slot_count <= 0) return;
    for (i = 0; i < slot_count; i++) {
        w64((void *)(obj + i * 0x20 + 0x10), 0);
        w64((void *)(obj + i * 0x20 + 0x18), 15);
    }
}

static void source_finder_media_set_str(int64_t base, int64_t media_obj, int off, const char *value) {
    wx_set_std_string_heap(base, media_obj + off, value ? value : "");
}

static int source_finder_feed_set_single_media(int64_t base, int64_t payload_obj,
                                               const char *media_type,
                                               const char *media_url,
                                               const char *thumb_url,
                                               const char *cover_url,
                                               const char *full_cover_url,
                                               const char *full_clip_inset,
                                               const char *width,
                                               const char *height,
                                               const char *video_play_duration) {
    BYTE media_obj[0x120];
    int64_t insert_pos;
    if (!base || !payload_obj) return 0;
    if ((!media_url || !media_url[0]) && (!thumb_url || !thumb_url[0]) &&
        (!cover_url || !cover_url[0]) && (!full_cover_url || !full_cover_url[0])) {
        return 1;
    }

    ZeroMemory(media_obj, sizeof(media_obj));
    source_finder_init_string_slots((int64_t)media_obj, 9);

    /*
     * sub_18223BA90/sub_18223C177 media item layout in Weixin 4.1.9.23:
     * +0x00 mediaType, +0x20 url, +0x40 thumbUrl, +0x60 width, +0x80 height,
     * +0xA0 coverUrl, +0xC0 fullCoverUrl, +0xE0 fullClipInset,
     * +0x100 videoPlayDuration.  The parent vector is at finderFeed+0x228.
     * Let Weixin's vector helper allocate/copy the item; direct begin/end/cap
     * stitching is not accepted consistently by the source-send serializer.
     */
    source_finder_media_set_str(base, (int64_t)media_obj, 0x000, media_type && media_type[0] ? media_type : "4");
    source_finder_media_set_str(base, (int64_t)media_obj, 0x020, media_url);
    source_finder_media_set_str(base, (int64_t)media_obj, 0x040, thumb_url);
    source_finder_media_set_str(base, (int64_t)media_obj, 0x060, width);
    source_finder_media_set_str(base, (int64_t)media_obj, 0x080, height);
    source_finder_media_set_str(base, (int64_t)media_obj, 0x0A0, cover_url);
    source_finder_media_set_str(base, (int64_t)media_obj, 0x0C0, full_cover_url && full_cover_url[0] ? full_cover_url : cover_url);
    source_finder_media_set_str(base, (int64_t)media_obj, 0x0E0, full_clip_inset);
    source_finder_media_set_str(base, (int64_t)media_obj, 0x100, video_play_duration);

    insert_pos = r64((void *)(payload_obj + 0x230));
    ((FnCall3Args)(base + WX_OFF_FINDER_MEDIA_VECTOR_PUSH))(
        payload_obj + 0x228,
        insert_pos,
        (int64_t)media_obj
    );
    ((FnCall1)(base + WX_OFF_FINDER_MEDIA_ITEM_DTOR))((int64_t)media_obj);
    return 1;
}

static int source_finder_feed_build_payload(int64_t base, int64_t source, const char *xml,
                                            int64_t *out_obj, int64_t *out_ctl) {
    const char *finder;
    const char *media;
    int64_t payload_pair[2] = {0, 0};
    int64_t payload_obj;
    int64_t payload_ctl;
    int64_t old_ctl;
    char object_id[256] = {0};
    char object_nonce_id[256] = {0};
    char feed_type[64] = {0};
    char local_id[128] = {0};
    char nickname[512] = {0};
    char username[512] = {0};
    char avatar[2048] = {0};
    char desc[4096] = {0};
    char media_count[64] = {0};
    char auth_icon_type[64] = {0};
    char auth_icon_url[2048] = {0};
    char web_url[2048] = {0};
    char content_type[64] = {0};
    char media_title[1024] = {0};
    char media_type[64] = {0};
    char media_url[2048] = {0};
    char thumb_url[2048] = {0};
    char cover_url[2048] = {0};
    char full_cover_url[2048] = {0};
    char full_clip_inset[256] = {0};
    char width[64] = {0};
    char height[64] = {0};
    char video_play_duration[64] = {0};
    char source_comment_scene[64] = {0};
    if (out_obj) *out_obj = 0;
    if (out_ctl) *out_ctl = 0;
    if (!base || !source || !xml) return 0;

    finder = source_finder_xml_section(xml, "finderFeed");
    media = source_finder_xml_section(finder, "media");

    source_finder_xml_tag_decoded2(finder, "objectId", "finderObjectID", object_id, sizeof(object_id));
    source_finder_xml_tag_decoded2(finder, "objectNonceId", "finderNonceID", object_nonce_id, sizeof(object_nonce_id));
    source_finder_xml_tag_decoded(finder, "feedType", feed_type, sizeof(feed_type));
    source_finder_xml_tag_decoded(finder, "localId", local_id, sizeof(local_id));
    source_finder_xml_tag_decoded(finder, "nickname", nickname, sizeof(nickname));
    source_finder_xml_tag_decoded2(finder, "username", "finderUsername", username, sizeof(username));
    source_finder_xml_tag_decoded(finder, "avatar", avatar, sizeof(avatar));
    source_finder_xml_tag_decoded(finder, "desc", desc, sizeof(desc));
    source_finder_xml_tag_decoded(finder, "mediaCount", media_count, sizeof(media_count));
    source_finder_xml_tag_decoded(finder, "authIconType", auth_icon_type, sizeof(auth_icon_type));
    source_finder_xml_tag_decoded(finder, "authIconUrl", auth_icon_url, sizeof(auth_icon_url));
    source_finder_xml_tag_decoded(finder, "webUrl", web_url, sizeof(web_url));
    source_finder_xml_tag_decoded(finder, "contentType", content_type, sizeof(content_type));
    source_finder_xml_tag_decoded(media, "title", media_title, sizeof(media_title));
    if (!media_title[0]) source_finder_xml_tag_decoded(xml, "title", media_title, sizeof(media_title));
    source_finder_xml_tag_decoded(media, "mediaType", media_type, sizeof(media_type));
    source_finder_xml_tag_decoded(media, "url", media_url, sizeof(media_url));
    source_finder_xml_tag_decoded(media, "thumbUrl", thumb_url, sizeof(thumb_url));
    source_finder_xml_tag_decoded(media, "coverUrl", cover_url, sizeof(cover_url));
    source_finder_xml_tag_decoded(media, "fullCoverUrl", full_cover_url, sizeof(full_cover_url));
    source_finder_xml_tag_decoded(media, "fullClipInset", full_clip_inset, sizeof(full_clip_inset));
    source_finder_xml_tag_decoded(media, "width", width, sizeof(width));
    source_finder_xml_tag_decoded(media, "height", height, sizeof(height));
    source_finder_xml_tag_decoded(media, "videoPlayDuration", video_play_duration, sizeof(video_play_duration));
    source_finder_xml_tag_decoded(finder, "sourceCommentScene", source_comment_scene, sizeof(source_comment_scene));
    if (!feed_type[0]) safe_copy(feed_type, sizeof(feed_type), "0");
    if (!media_count[0]) safe_copy(media_count, sizeof(media_count), "1");
    if (!auth_icon_type[0]) safe_copy(auth_icon_type, sizeof(auth_icon_type), "0");
    if (!media_type[0]) safe_copy(media_type, sizeof(media_type), "4");
    if (!thumb_url[0] && cover_url[0]) safe_copy(thumb_url, sizeof(thumb_url), cover_url);
    if (!thumb_url[0] && full_cover_url[0]) safe_copy(thumb_url, sizeof(thumb_url), full_cover_url);
    if (!cover_url[0] && thumb_url[0]) safe_copy(cover_url, sizeof(cover_url), thumb_url);
    if (!full_cover_url[0] && cover_url[0]) safe_copy(full_cover_url, sizeof(full_cover_url), cover_url);

    ((FnCall1)(base + WX_OFF_SOURCE_FINDER_FEED_PAYLOAD_FACTORY))((int64_t)payload_pair);
    payload_obj = payload_pair[0];
    payload_ctl = payload_pair[1];
    if (!payload_obj || !payload_ctl) {
        release_shared_pair((int64_t)payload_pair);
        return 0;
    }
    if (r64((void *)payload_ctl) != base + WX_OFF_FINDER_FEED_PAYLOAD_CTL_VT) {
        release_shared_pair((int64_t)payload_pair);
        return 0;
    }

    /*
     * MessageSendSource finder source-send uses the compact source payload
     * order that already produced playable cards.  Do not swap this to the
     * appmsg XML parser's inner finderFeed layout; that makes 0x20291D0 throw.
     *
     * Empirical receiver-side fix:
     *   +0x1C0 was width -> blank cover/avatar slot.
     *   +0x1E0 was height -> displayed as "1920.0" account name.
     */
    source_finder_set_payload_str(base, payload_obj, 0x000, object_id);
    source_finder_set_payload_str(base, payload_obj, 0x020, object_nonce_id);
    source_finder_set_payload_str(base, payload_obj, 0x040, feed_type);
    source_finder_set_payload_str(base, payload_obj, 0x060, nickname);
    source_finder_set_payload_str(base, payload_obj, 0x080, username);
    source_finder_set_payload_str(base, payload_obj, 0x0A0, avatar);
    source_finder_set_payload_str(base, payload_obj, 0x0C0, desc);
    source_finder_set_payload_str(base, payload_obj, 0x0E0, media_count);
    source_finder_set_payload_str(base, payload_obj, 0x100, auth_icon_type);
    source_finder_set_payload_str(base, payload_obj, 0x120, auth_icon_url);
    source_finder_set_payload_str(base, payload_obj, 0x140, media_title);
    source_finder_set_payload_str(base, payload_obj, 0x160, media_url);
    source_finder_set_payload_str(base, payload_obj, 0x180, thumb_url);
    source_finder_set_payload_str(base, payload_obj, 0x1A0, cover_url);
    source_finder_set_payload_str(base, payload_obj, 0x1C0, width);
    source_finder_set_payload_str(base, payload_obj, 0x1E0, nickname);
    source_finder_set_payload_str(base, payload_obj, 0x200, video_play_duration);
    w32((void *)(payload_obj + 0x220), source_comment_scene[0] ? (int)a2i64(source_comment_scene) : 0);

    if (!source_finder_feed_set_single_media(base, payload_obj,
                                             media_type,
                                             media_url,
                                             thumb_url,
                                             cover_url,
                                             full_cover_url,
                                             full_clip_inset,
                                             width,
                                             height,
                                             video_play_duration)) {
        release_shared_pair((int64_t)payload_pair);
        return 0;
    }

    retain_shared_ctl(payload_ctl);
    old_ctl = r64((void *)(source + 0x6F0));
    w64((void *)(source + 0x6E8), payload_obj);
    w64((void *)(source + 0x6F0), payload_ctl);
    release_shared_ctl(old_ctl);

    if (out_obj) *out_obj = payload_obj;
    if (out_ctl) *out_ctl = payload_ctl;
    release_shared_pair((int64_t)payload_pair);
    return 1;
}

static void source_finder_feed_xml_queue_worker(int64_t task_addr) {
    SourceFinderFeedXmlTask *task = (SourceFinderFeedXmlTask *)task_addr;
    int64_t base = (int64_t)wxb_weixin_module();
    BYTE *arena;
    BYTE *ctx;
    BYTE *pool;
    BYTE *pool_end;
    int64_t source_pair[2] = {0, 0};
    int64_t source_vec[3] = {0, 0, 0};
    BYTE out[0x80];
    BYTE extra[0xA0];
    BYTE cb[0xE0];
    if (!task || !base) return;
    if (InterlockedCompareExchange(&task->status, 2, 0) != 0) return;

    arena = ((BYTE *)task) - 0x1000;
    ctx = arena + 0x4000;
    pool = arena + 0x9000;
    pool_end = arena + 0x70000;
    ZeroMemory(out, sizeof(out));
    ZeroMemory(extra, sizeof(extra));
    ZeroMemory(cb, sizeof(cb));

    __try {
        char dbg[1536];
        char read_object[256] = {0};
        char read_nickname[256] = {0};
        char read_biz_nickname[256] = {0};
        char read_desc[256] = {0};
        char read_url[256] = {0};
        char read_thumb[256] = {0};
        char read_cover[256] = {0};
        const char *xml = find_xml_msg_start(task->content);
        int64_t source;
        int64_t ctl;
        int64_t elem;
        int64_t media_obj;
        int content_len;

        build_single_target_ctx(ctx, task->wxid, &pool, pool_end);

        ((FnCall1)(base + WX_OFF_SOURCE_APPMSG_FINDER_FACTORY))((int64_t)source_pair);
        source = source_pair[0];
        ctl = source_pair[1];
        task->source_obj = source;
        task->source_ctl = ctl;
        if (!source || !ctl) {
            quote_dbg("[send-source-finder-feed-xml] finder source factory returned empty pair");
            InterlockedExchange(&task->status, -3);
            __leave;
        }

        content_len = lstrlenA(xml);
        w64((void *)(source + 216), 0x3300000031LL);
        if (!source_finder_feed_build_payload(base, source, xml,
                                              &task->payload_obj, &task->payload_ctl)) {
            quote_dbg("[send-source-finder-feed-xml] finderFeed payload build failed");
            InterlockedExchange(&task->status, -5);
            __leave;
        }

        elem = ((FnCall1)(base + WX_OFF_WX_ALLOC))(16);
        if (!elem) {
            quote_dbg("[send-source-finder-feed-xml] source vector alloc failed");
            InterlockedExchange(&task->status, -4);
            __leave;
        }
        source_vec[0] = elem;
        source_vec[1] = elem + 16;
        source_vec[2] = elem + 16;
        w64((void *)elem, source);
        InterlockedIncrement((volatile LONG *)(ctl + 8));
        w64((void *)(elem + 8), ctl);

        wx_read_sso_str(task->payload_obj + 0x000, read_object, sizeof(read_object));
        wx_read_sso_str(task->payload_obj + 0x060, read_nickname, sizeof(read_nickname));
        wx_read_sso_str(task->payload_obj + 0x0C0, read_desc, sizeof(read_desc));
        wx_read_sso_str(task->payload_obj + 0x1E0, read_biz_nickname, sizeof(read_biz_nickname));
        wx_read_sso_str(task->payload_obj + 0x160, read_url, sizeof(read_url));
        wx_read_sso_str(task->payload_obj + 0x180, read_thumb, sizeof(read_thumb));
        wx_read_sso_str(task->payload_obj + 0x1C0, read_cover, sizeof(read_cover));
        media_obj = r64((void *)(task->payload_obj + 0x228));
        if (media_obj) {
            wx_read_sso_str(media_obj + 0x020, read_url, sizeof(read_url));
            wx_read_sso_str(media_obj + 0x040, read_thumb, sizeof(read_thumb));
            wx_read_sso_str(media_obj + 0x0A0, read_cover, sizeof(read_cover));
        }
        wsprintfA(dbg,
                  "[send-source-finder-feed-xml] enter fn=1820291D0 source={%I64x,%I64x} vec=%I64x to='%s' packed=%I64x xml_len=%d payload6E8={%I64x,%I64x} objSz=%I64d nickSz=%I64d bizNickSz=%I64d descSz=%I64d media={%I64x,%I64x,%I64x} contentType=%d object='%.120s' nick='%.120s' bizNick='%.120s' url='%.160s' thumb='%.160s' cover='%.160s' desc='%.120s'",
                  (unsigned __int64)source,
                  (unsigned __int64)ctl,
                  (unsigned __int64)elem,
                  task->wxid,
                  (unsigned __int64)r64((void *)(source + 216)),
                  content_len,
                  (unsigned __int64)task->payload_obj,
                  (unsigned __int64)task->payload_ctl,
                  (unsigned __int64)r64((void *)(task->payload_obj + 0x000 + 0x10)),
                  (unsigned __int64)r64((void *)(task->payload_obj + 0x060 + 0x10)),
                  (unsigned __int64)r64((void *)(task->payload_obj + 0x1E0 + 0x10)),
                  (unsigned __int64)r64((void *)(task->payload_obj + 0x0C0 + 0x10)),
                  (unsigned __int64)r64((void *)(task->payload_obj + 0x228)),
                  (unsigned __int64)r64((void *)(task->payload_obj + 0x230)),
                  (unsigned __int64)r64((void *)(task->payload_obj + 0x238)),
                  r32((void *)(task->payload_obj + 0x220)),
                  read_object,
                  read_nickname,
                  read_biz_nickname,
                  read_url,
                  read_thumb,
                  read_cover,
                  read_desc);
        quote_dbg(dbg);

        task->ret = ((FnCall2)(base + WX_OFF_SOURCE_SEND))(
            (int64_t)out,
            (int64_t)ctx,
            (int64_t)source_vec,
            (int64_t)extra,
            (int64_t)cb
        );
        task->out0 = r64(out);
        task->out1 = r64(out + 8);
        task->out2 = r64(out + 0x10);
        task->out3 = r64(out + 0x18);
        wsprintfA(dbg,
                  "[send-source-finder-feed-xml] returned ret=%I64x out={%I64x,%I64x,%I64x,%I64x} vec_after={%I64x,%I64x,%I64x}",
                  (unsigned __int64)task->ret,
                  (unsigned __int64)task->out0,
                  (unsigned __int64)task->out1,
                  (unsigned __int64)task->out2,
                  (unsigned __int64)task->out3,
                  (unsigned __int64)source_vec[0],
                  (unsigned __int64)source_vec[1],
                  (unsigned __int64)source_vec[2]);
        quote_dbg(dbg);
        InterlockedExchange(&task->status, 1);
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
        quote_dbg("[send-source-finder-feed-xml] EXCEPTION");
        InterlockedExchange(&task->status, -1);
    }

    release_shared_pair((int64_t)source_pair);
}

static int send_finder_feed_xml_source_experimental(const char *wxid, const char *content) {
    const SIZE_T arena_size = 0x70000;
    BYTE *arena;
    BYTE *asm_addr;
    SourceFinderFeedXmlTask *task;
    int asm_len;
    LONG status;
    char dbg[768];
    int wxid_len;
    int content_len;
    const char *xml;
    if (!wxb_weixin_module() || !wxid || !content) return 0;
    wxid_len = lstrlenA(wxid);
    content_len = lstrlenA(content);
    xml = find_xml_msg_start(content);
    if (wxid_len <= 0 || wxid_len >= TEXT_HIGH_WXID_MAX ||
        content_len <= 0 || content_len >= TEXT_HIGH_CONTENT_MAX ||
        !xml[0] || !str_has(xml, "<appmsg") || !str_has(xml, "<finderFeed")) {
        quote_dbg("[send-source-finder-feed-xml] skipped: bad target/content for XML source-send path");
        return 0;
    }

    arena = (BYTE *)VirtualAlloc(NULL, arena_size, MEM_COMMIT | MEM_RESERVE, PAGE_EXECUTE_READWRITE);
    if (!arena) return 0;
    ZeroMemory(arena, arena_size);
    asm_addr = arena + 0x200;
    task = (SourceFinderFeedXmlTask *)(arena + 0x1000);
    task->status = 0;
    safe_copy(task->wxid, sizeof(task->wxid), wxid);
    safe_copy(task->content, sizeof(task->content), content);

    asm_len = build_text_high_queue_asm(asm_addr, (int64_t)task, (int64_t)source_finder_feed_xml_queue_worker);
    FlushInstructionCache(GetCurrentProcess(), asm_addr, asm_len);
    wsprintfA(dbg,
              "[send-source-finder-feed-xml] queue to='%s' content_len=%d task=%I64x asm=%I64x",
              task->wxid,
              content_len,
              (unsigned __int64)(int64_t)task,
              (unsigned __int64)(int64_t)asm_addr);
    quote_dbg(dbg);

    queue_send((int64_t)asm_addr);
    for (int i = 0; i < 700; i++) {
        status = InterlockedCompareExchange(&task->status, 0, 0);
        if (status == 1 || status < 0) break;
        Sleep(10);
    }
    status = InterlockedCompareExchange(&task->status, 0, 0);
    if (status == 0) {
        InterlockedCompareExchange(&task->status, -2, 0);
        status = InterlockedCompareExchange(&task->status, 0, 0);
    }
    wsprintfA(dbg,
              "[send-source-finder-feed-xml] queue status=%d ret=%I64x source={%I64x,%I64x} payload={%I64x,%I64x} out={%I64x,%I64x,%I64x,%I64x}",
              (int)status,
              (unsigned __int64)task->ret,
              (unsigned __int64)task->source_obj,
              (unsigned __int64)task->source_ctl,
              (unsigned __int64)task->payload_obj,
              (unsigned __int64)task->payload_ctl,
              (unsigned __int64)task->out0,
              (unsigned __int64)task->out1,
              (unsigned __int64)task->out2,
              (unsigned __int64)task->out3);
    quote_dbg(dbg);

    retain_arena(arena);
    return status == 1;
}

static void source_voice_copy_until_pipe(const char *s, char *out, int out_sz, const char **next) {
    int n = 0;
    if (out && out_sz > 0) out[0] = 0;
    if (next) *next = NULL;
    if (!s || !out || out_sz <= 0) return;
    while (*s && *s != '|' && n + 1 < out_sz) {
        out[n++] = *s++;
    }
    out[n] = 0;
    if (next && *s == '|') *next = s + 1;
}

static void source_voice_parse_kv_tail(const char *tail, int *duration_ms, int *voice_format) {
    const char *p = tail;
    while (p && *p) {
        const char *next = NULL;
        char item[256] = {0};
        source_voice_copy_until_pipe(p, item, sizeof(item), &next);
        if (has_prefix(item, "duration=") && duration_ms) {
            *duration_ms = (int)a2i64(item + 9);
        } else if (has_prefix(item, "duration_ms=") && duration_ms) {
            *duration_ms = (int)a2i64(item + 12);
        } else if (has_prefix(item, "format=") && voice_format) {
            *voice_format = (int)a2i64(item + 7);
        } else if (has_prefix(item, "voiceformat=") && voice_format) {
            *voice_format = (int)a2i64(item + 12);
        }
        p = next;
    }
}

static int source_voice_read_file_utf8(const char *path, BYTE *out, int out_max) {
    WCHAR wpath[SOURCE_VOICE_PATH_MAX];
    HANDLE h;
    LARGE_INTEGER size;
    DWORD rd = 0;
    int wlen;
    const char *p = path;
    if (!p || !p[0] || !out || out_max <= 0) return -1;
    if (has_prefix(p, "file:")) p += 5;
    if (has_prefix(p, "@file:")) p += 6;
    wlen = MultiByteToWideChar(CP_UTF8, 0, p, -1, wpath, SOURCE_VOICE_PATH_MAX);
    if (wlen <= 0 || wlen >= SOURCE_VOICE_PATH_MAX) return -1;
    h = CreateFileW(wpath, GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
                    NULL, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, NULL);
    if (h == INVALID_HANDLE_VALUE) return -1;
    if (!GetFileSizeEx(h, &size) || size.QuadPart <= 0 || size.QuadPart > out_max) {
        CloseHandle(h);
        return -1;
    }
    if (!ReadFile(h, out, (DWORD)size.QuadPart, &rd, NULL) || rd != (DWORD)size.QuadPart) {
        CloseHandle(h);
        return -1;
    }
    CloseHandle(h);
    return (int)rd;
}

static void source_voice_hex_sample(const BYTE *ptr, int len, char *out, int out_sz) {
    static const char hx[] = "0123456789abcdef";
    int want;
    int pos = 0;
    if (!out || out_sz <= 0) return;
    out[0] = 0;
    if (!ptr || len <= 0) return;
    want = len < 64 ? len : 64;
    for (int i = 0; i < want && pos + 3 < out_sz; i++) {
        BYTE b = ptr[i];
        out[pos++] = hx[(b >> 4) & 0xF];
        out[pos++] = hx[b & 0xF];
        if (i + 1 < want && pos + 1 < out_sz) out[pos++] = ' ';
    }
    out[pos] = 0;
}

static int source_voice_parse_body(const char *content,
                                   char *voice_data, int voice_data_sz,
                                   char *voice_path, int voice_path_sz,
                                   char *extra_data, int extra_data_sz,
                                   int *duration_ms, int *voice_format,
                                   int *is_xml, int *is_file) {
    const char *xml;
    if (voice_data && voice_data_sz > 0) voice_data[0] = 0;
    if (voice_path && voice_path_sz > 0) voice_path[0] = 0;
    if (extra_data && extra_data_sz > 0) extra_data[0] = 0;
    if (duration_ms) *duration_ms = 1000;
    if (voice_format) *voice_format = 4;
    if (is_xml) *is_xml = 0;
    if (is_file) *is_file = 0;
    if (!content || !content[0]) return 0;

    if (has_prefix(content, "path=") || has_prefix(content, "file=")) {
        const char *value = content + (has_prefix(content, "path=") ? 5 : 5);
        const char *tail = NULL;
        source_voice_copy_until_pipe(value, voice_path, voice_path_sz, &tail);
        source_voice_parse_kv_tail(tail, duration_ms, voice_format);
        if (is_file) *is_file = 1;
        return voice_path && voice_path[0];
    }

    xml = find_xml_msg_start(content);
    if (xml && str_has(xml, "<voicemsg")) {
        char duration_buf[64] = {0};
        char format_buf[64] = {0};
        if (is_xml) *is_xml = 1;
        extract_xml_attr_value(xml, "voiceurl", voice_data, voice_data_sz);
        extract_xml_attr_value(xml, "aeskey", extra_data, extra_data_sz);
        extract_xml_attr_value(xml, "voicelength", duration_buf, sizeof(duration_buf));
        if (!duration_buf[0]) extract_xml_attr_value(xml, "length", duration_buf, sizeof(duration_buf));
        extract_xml_attr_value(xml, "voiceformat", format_buf, sizeof(format_buf));
        xml_decode_basic_entities_inplace(voice_data);
        xml_decode_basic_entities_inplace(extra_data);
        if (duration_ms && duration_buf[0]) *duration_ms = (int)a2i64(duration_buf);
        if (voice_format && format_buf[0]) *voice_format = (int)a2i64(format_buf);
        return voice_data && voice_data[0];
    }

    {
        char first[64] = {0};
        const char *rest = NULL;
        const char *extra = NULL;
        source_voice_copy_until_pipe(content, first, sizeof(first), &rest);
        if (rest) {
            int n = 0;
            if (duration_ms) *duration_ms = (int)a2i64(first);
            if (has_prefix(rest, "file:") || has_prefix(rest, "@file:") || has_prefix(rest, "path=")) {
                source_voice_copy_until_pipe(rest, voice_path, voice_path_sz, &extra);
                if (has_prefix(voice_path, "path=")) {
                    char tmp[SOURCE_VOICE_PATH_MAX];
                    safe_copy(tmp, sizeof(tmp), voice_path + 5);
                    safe_copy(voice_path, voice_path_sz, tmp);
                }
                source_voice_parse_kv_tail(extra, duration_ms, voice_format);
                if (is_file) *is_file = 1;
                return voice_path && voice_path[0];
            }
            while (rest[n] && rest[n] != '|' && n + 1 < voice_data_sz) {
                voice_data[n] = rest[n];
                n++;
            }
            voice_data[n] = 0;
            if (rest[n] == '|') extra = rest + n + 1;
            if (extra && extra_data && extra_data_sz > 0) {
                safe_copy(extra_data, extra_data_sz, extra);
            }
        } else {
            safe_copy(voice_data, voice_data_sz, content);
        }
    }

    if (duration_ms && *duration_ms <= 0) *duration_ms = 1000;
    if (voice_format && *voice_format <= 0) *voice_format = 4;
    return voice_data && voice_data[0];
}

static void source_voice_source_queue_worker(int64_t task_addr) {
    SourceVoiceTask *task = (SourceVoiceTask *)task_addr;
    int64_t base = (int64_t)wxb_weixin_module();
    BYTE *arena;
    BYTE *ctx;
    BYTE *pool;
    BYTE *pool_end;
    BYTE *target_arg;
    BYTE *voice_arg;
    BYTE *voice_meta;
    int64_t source_pair[2] = {0, 0};
    int64_t source_vec[3] = {0, 0, 0};
    BYTE out[0x80];
    BYTE extra[0xA0];
    BYTE cb[0xE0];
    int builder_shim_active = 0;
    if (!task || !base) return;
    if (InterlockedCompareExchange(&task->status, 2, 0) != 0) return;

    arena = ((BYTE *)task) - 0x1000;
    ctx = arena + 0x4000;
    target_arg = arena + 0x4800;
    voice_arg = arena + 0x4820;
    voice_meta = arena + 0x4860;
    pool = arena + 0x9000;
    pool_end = arena + 0x70000;
    ZeroMemory(out, sizeof(out));
    ZeroMemory(extra, sizeof(extra));
    ZeroMemory(cb, sizeof(cb));
    ZeroMemory(target_arg, 0x20);
    ZeroMemory(voice_arg, 0x20);
    ZeroMemory(voice_meta, 0x80);

    __try {
        char dbg[1536];
        char voice_data[8192] = {0};
        char voice_path[SOURCE_VOICE_PATH_MAX] = {0};
        char extra_data[2048] = {0};
        char read_target[256] = {0};
        char read_voice[256] = {0};
        char read_extra[256] = {0};
        char voice_hex[256] = {0};
        int duration_ms = 1000;
        int voice_format = 4;
        int is_xml = 0;
        int is_file = 0;
        int voice_len = 0;
        BYTE *voice_bytes = NULL;
        int64_t source;
        int64_t ctl;
        int64_t elem;
        int64_t meta_type;

        if (!source_voice_parse_body(task->content,
                                     voice_data, sizeof(voice_data),
                                     voice_path, sizeof(voice_path),
                                     extra_data, sizeof(extra_data),
                                     &duration_ms, &voice_format, &is_xml, &is_file)) {
            quote_dbg("[send-source-voice] voice body parse failed");
            InterlockedExchange(&task->status, -6);
            __leave;
        }
        if (duration_ms <= 0) duration_ms = 1000;
        if (voice_format <= 0) voice_format = 4;
        task->duration_ms = duration_ms;
        task->voice_format = voice_format;

        build_single_target_ctx(ctx, task->wxid, &pool, pool_end);
        if (file_exists(VOICE_SOURCE_CTX_BRANCH_FILE)) {
            char ctx_tag[256] = {0};
            int n = read_file_bytes(VOICE_SOURCE_CTX_BRANCH_FILE, ctx_tag, sizeof(ctx_tag) - 1);
            if (n > 0) {
                int start = 0;
                int end;
                ctx_tag[n] = 0;
                for (int i = 0; ctx_tag[i]; i++) {
                    if (ctx_tag[i] == '\r' || ctx_tag[i] == '\n') {
                        ctx_tag[i] = 0;
                        break;
                    }
                }
                while (ctx_tag[start] == ' ' || ctx_tag[start] == '\t') start++;
                if (start > 0) {
                    int i = 0;
                    while (ctx_tag[start + i]) {
                        ctx_tag[i] = ctx_tag[start + i];
                        i++;
                    }
                    ctx_tag[i] = 0;
                }
                end = lstrlenA(ctx_tag);
                while (end > 0 && (ctx_tag[end - 1] == ' ' || ctx_tag[end - 1] == '\t')) {
                    ctx_tag[--end] = 0;
                }
            }
            if (ctx_tag[0]) {
                wx_set_std_string_arena((int64_t)(ctx + 0x28), ctx_tag, lstrlenA(ctx_tag), &pool, pool_end);
                wsprintfA(dbg,
                          "[send-source-voice] experimental ctx branch enabled observed_route_key='%.200s' ctx28_len=%I64d",
                          ctx_tag,
                          (unsigned __int64)r64((void *)(ctx + 0x38)));
                quote_dbg(dbg);
            } else {
                quote_dbg("[send-source-voice] ctx branch control present but no observed route key; ctx+0x28 left empty");
            }
        }
        wx_set_std_string_arena((int64_t)target_arg, task->wxid, lstrlenA(task->wxid), &pool, pool_end);
        if (is_file) {
            int max_voice = (int)(pool_end - pool - 0x1000);
            if (max_voice <= 0) {
                quote_dbg("[send-source-voice] no arena room for voice file");
                InterlockedExchange(&task->status, -8);
                __leave;
            }
            voice_bytes = pool;
            voice_len = source_voice_read_file_utf8(voice_path, voice_bytes, max_voice);
            if (voice_len <= 0) {
                quote_dbg("[send-source-voice] voice file read failed");
                InterlockedExchange(&task->status, -9);
                __leave;
            }
            if (!(voice_len >= 11 && voice_bytes[0] == 0x02 &&
                  voice_bytes[1] == '#' && voice_bytes[2] == '!' &&
                  voice_bytes[3] == 'S' && voice_bytes[4] == 'I' &&
                  voice_bytes[5] == 'L' && voice_bytes[6] == 'K')) {
                quote_dbg("[send-source-voice] voice file is not captured Silk bytes");
                InterlockedExchange(&task->status, -10);
                __leave;
            }
            pool += voice_len + 16;
            wx_set_std_string_arena((int64_t)voice_arg, (const char *)voice_bytes, voice_len, &pool, pool_end);
            source_voice_hex_sample(voice_bytes, voice_len, voice_hex, sizeof(voice_hex));
        } else {
            voice_len = lstrlenA(voice_data);
            wx_set_std_string_arena((int64_t)voice_arg, voice_data, voice_len, &pool, pool_end);
            source_voice_hex_sample((const BYTE *)voice_data, voice_len, voice_hex, sizeof(voice_hex));
        }

        /*
         * Real recorder path:
         *   sub_185EF9010(out, target_str, voice_data_str, &duration, meta)
         *   sub_185EF8B90 then writes source+0xD8 = 34 before dispatch.
         * meta[0] is 0x300000004 for voiceformat=4; meta+0x18 is an optional string.
         */
        meta_type = (3LL << 32) | (unsigned int)voice_format;
        w64((void *)(voice_meta + 0x00), meta_type);
        w64((void *)(voice_meta + 0x08), 30000);
        w8((void *)(voice_meta + 0x10), 0);
        wx_set_std_string_arena((int64_t)(voice_meta + 0x18), extra_data, lstrlenA(extra_data), &pool, pool_end);

        ((FnCall2)(base + WX_OFF_SOURCE_VOICE_FACTORY))(
            (int64_t)source_pair,
            (int64_t)target_arg,
            (int64_t)voice_arg,
            (int64_t)&duration_ms,
            (int64_t)voice_meta
        );
        source = source_pair[0];
        ctl = source_pair[1];
        task->source_obj = source;
        task->source_ctl = ctl;
        if (!source || !ctl) {
            quote_dbg("[send-source-voice] voice source factory returned empty pair");
            InterlockedExchange(&task->status, -3);
            __leave;
        }
        if (r64((void *)ctl) != base + WX_OFF_SOURCE_VOICE_CTL_VT ||
            r64((void *)source) != base + WX_OFF_SOURCE_VOICE_OBJ_VT) {
            quote_dbg("[send-source-voice] voice source factory vtable mismatch");
            InterlockedExchange(&task->status, -7);
            __leave;
        }

        w32((void *)(source + 0xD8), 34);

        elem = ((FnCall1)(base + WX_OFF_WX_ALLOC))(16);
        if (!elem) {
            quote_dbg("[send-source-voice] source vector alloc failed");
            InterlockedExchange(&task->status, -4);
            __leave;
        }
        source_vec[0] = elem;
        source_vec[1] = elem + 16;
        source_vec[2] = elem + 16;
        w64((void *)elem, source);
        InterlockedIncrement((volatile LONG *)(ctl + 8));
        w64((void *)(elem + 8), ctl);

        wx_read_sso_str(source + 0xB0, read_target, sizeof(read_target));
        wx_read_sso_str(source + 0x660, read_voice, sizeof(read_voice));
        wx_read_sso_str(source + 0x6A0, read_extra, sizeof(read_extra));
        wsprintfA(dbg,
                  "[send-source-voice] enter fn=1820291D0 source={%I64x,%I64x} vec=%I64x to='%s' type=%d duration=%d format=%d is_xml=%d is_file=%d voiceLen=%d voiceSz=%I64d extraSz=%I64d target='%.120s' voice='%.180s' extra='%.120s' voice_hex64=%s",
                  (unsigned __int64)source,
                  (unsigned __int64)ctl,
                  (unsigned __int64)elem,
                  task->wxid,
                  r32((void *)(source + 0xD8)),
                  duration_ms,
                  voice_format,
                  is_xml,
                  is_file,
                  voice_len,
                  (unsigned __int64)r64((void *)(source + 0x660 + 0x10)),
                  (unsigned __int64)r64((void *)(source + 0x6A0 + 0x10)),
                  read_target,
                  read_voice,
                  read_extra,
                  voice_hex);
        quote_dbg(dbg);

        if (file_exists(VOICE_SOURCE_BUILDER_SHIM_FILE)) {
            builder_shim_active = 1;
            voice_source_builder_shim_enter();
        }
        task->ret = ((FnCall2)(base + WX_OFF_SOURCE_SEND))(
            (int64_t)out,
            (int64_t)ctx,
            (int64_t)source_vec,
            (int64_t)extra,
            (int64_t)cb
        );
        if (builder_shim_active) {
            voice_source_builder_shim_leave();
            builder_shim_active = 0;
        }
        task->out0 = r64(out);
        task->out1 = r64(out + 8);
        task->out2 = r64(out + 0x10);
        task->out3 = r64(out + 0x18);
        wsprintfA(dbg,
                  "[send-source-voice] returned ret=%I64x out={%I64x,%I64x,%I64x,%I64x} vec_after={%I64x,%I64x,%I64x}",
                  (unsigned __int64)task->ret,
                  (unsigned __int64)task->out0,
                  (unsigned __int64)task->out1,
                  (unsigned __int64)task->out2,
                  (unsigned __int64)task->out3,
                  (unsigned __int64)source_vec[0],
                  (unsigned __int64)source_vec[1],
                  (unsigned __int64)source_vec[2]);
        quote_dbg(dbg);
        InterlockedExchange(&task->status, 1);
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
        if (builder_shim_active) {
            voice_source_builder_shim_leave();
            builder_shim_active = 0;
        }
        quote_dbg("[send-source-voice] EXCEPTION");
        InterlockedExchange(&task->status, -1);
    }

    release_shared_pair((int64_t)source_pair);
}

static void voice_final_queue_worker(int64_t task_addr) {
    VoiceFinalTask *task = (VoiceFinalTask *)task_addr;
    int64_t base = (int64_t)wxb_weixin_module();
    BYTE *arena;
    BYTE *buf;
    BYTE *pool;
    BYTE *pool_end;
    if (!task || !base) return;
    if (InterlockedCompareExchange(&task->status, 2, 0) != 0) return;

    arena = ((BYTE *)task) - 0x1000;
    buf = arena + 0x4000;
    pool = arena + 0x5000;
    pool_end = arena + SOURCE_VOICE_ARENA_SIZE;
    ZeroMemory(buf, 0x80);

    __try {
        char dbg[1536];
        char voice_data[8192] = {0};
        char voice_path[SOURCE_VOICE_PATH_MAX] = {0};
        char extra_data[2048] = {0};
        char voice_hex[256] = {0};
        int duration_ms = 1000;
        int voice_format = 4;
        int is_xml = 0;
        int is_file = 0;
        int voice_len = 0;
        BYTE *voice_bytes = NULL;

        if (!voice_probe_prepare_cached_state_for_target(task->wxid)) {
            quote_dbg("[send-voice-final] prepare target/controller failed; skip final send to avoid partial VoiceState");
            InterlockedExchange(&task->status, -13);
            __leave;
        }
        if (!voice_probe_copy_cached_state(&task->holder, &task->state,
                                           task->cached_target,
                                           sizeof(task->cached_target))) {
            quote_dbg("[send-voice-final] no cached real VoiceState; open/switch to target chat so voice holder is constructed");
            InterlockedExchange(&task->status, -11);
            __leave;
        }
        if (!task->cached_target[0] || lstrcmpA(task->cached_target, task->wxid) != 0) {
            wsprintfA(dbg,
                      "[send-voice-final] cached target mismatch cached='%.180s' requested='%.180s' holder=%I64x state=%I64x",
                      task->cached_target,
                      task->wxid,
                      (unsigned __int64)task->holder,
                      (unsigned __int64)task->state);
            quote_dbg(dbg);
            InterlockedExchange(&task->status, -12);
            __leave;
        }
        if (!source_voice_parse_body(task->content,
                                     voice_data, sizeof(voice_data),
                                     voice_path, sizeof(voice_path),
                                     extra_data, sizeof(extra_data),
                                     &duration_ms, &voice_format, &is_xml, &is_file)) {
            quote_dbg("[send-voice-final] voice body parse failed");
            InterlockedExchange(&task->status, -6);
            __leave;
        }
        (void)extra_data;
        if (duration_ms <= 0) duration_ms = 1000;
        if (voice_format <= 0) voice_format = 4;
        task->duration_ms = duration_ms;
        task->voice_format = voice_format;

        if (is_file) {
            int max_voice = (int)(pool_end - pool - 0x1000);
            if (max_voice <= 0) {
                quote_dbg("[send-voice-final] no arena room for voice file");
                InterlockedExchange(&task->status, -8);
                __leave;
            }
            voice_bytes = pool;
            voice_len = source_voice_read_file_utf8(voice_path, voice_bytes, max_voice);
            if (voice_len <= 0) {
                quote_dbg("[send-voice-final] voice file read failed");
                InterlockedExchange(&task->status, -9);
                __leave;
            }
            if (!(voice_len >= 11 && voice_bytes[0] == 0x02 &&
                  voice_bytes[1] == '#' && voice_bytes[2] == '!' &&
                  voice_bytes[3] == 'S' && voice_bytes[4] == 'I' &&
                  voice_bytes[5] == 'L' && voice_bytes[6] == 'K')) {
                quote_dbg("[send-voice-final] voice file is not captured Silk bytes");
                InterlockedExchange(&task->status, -10);
                __leave;
            }
            pool += voice_len + 16;
            wx_set_std_string_arena((int64_t)(buf + 8), (const char *)voice_bytes, voice_len, &pool, pool_end);
            source_voice_hex_sample(voice_bytes, voice_len, voice_hex, sizeof(voice_hex));
        } else {
            voice_len = lstrlenA(voice_data);
            wx_set_std_string_arena((int64_t)(buf + 8), voice_data, voice_len, &pool, pool_end);
            source_voice_hex_sample((const BYTE *)voice_data, voice_len, voice_hex, sizeof(voice_hex));
        }
        task->voice_len = voice_len;
        w32((void *)buf, duration_ms);
        w32((void *)(buf + 4), 1);

        wsprintfA(dbg,
                  "[send-voice-final] enter fn=1852F0F20 holder=%I64x state=%I64x target='%.180s' duration=%d format=%d is_file=%d is_xml=%d voice_len=%d voice_hex64=%s",
                  (unsigned __int64)task->holder,
                  (unsigned __int64)task->state,
                  task->cached_target,
                  duration_ms,
                  voice_format,
                  is_file,
                  is_xml,
                  voice_len,
                  voice_hex);
        quote_dbg(dbg);

        task->ret = ((FnCall4)(base + WX_OFF_VOICE_STAGE_SEND_FINAL))(task->state, (int64_t)buf, 0, 0);
        wsprintfA(dbg,
                  "[send-voice-final] returned ret=%I64x state=%I64x buf_len=%I64d inflight=%d",
                  (unsigned __int64)task->ret,
                  (unsigned __int64)task->state,
                  (int64_t)r64((void *)(buf + 0x18)),
                  (int)(BYTE)r32((void *)(task->state + 0x142)));
        quote_dbg(dbg);
        InterlockedExchange(&task->status, 1);
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
        quote_dbg("[send-voice-final] EXCEPTION");
        InterlockedExchange(&task->status, -1);
    }
}

static int send_voice_final_experimental(const char *wxid, const char *content) {
    const SIZE_T arena_size = SOURCE_VOICE_ARENA_SIZE;
    BYTE *arena;
    BYTE *asm_addr;
    VoiceFinalTask *task;
    int asm_len;
    LONG status;
    char dbg[768];
    int wxid_len;
    int content_len;
    if (!wxb_weixin_module() || !wxid || !content) return 0;
    wxid_len = lstrlenA(wxid);
    content_len = lstrlenA(content);
    if (wxid_len <= 0 || wxid_len >= TEXT_HIGH_WXID_MAX ||
        content_len <= 0 || content_len >= TEXT_HIGH_CONTENT_MAX) {
        quote_dbg("[send-voice-final] skipped: bad target/content");
        return 0;
    }

    arena = (BYTE *)VirtualAlloc(NULL, arena_size, MEM_COMMIT | MEM_RESERVE, PAGE_EXECUTE_READWRITE);
    if (!arena) return 0;
    ZeroMemory(arena, arena_size);
    asm_addr = arena + 0x200;
    task = (VoiceFinalTask *)(arena + 0x1000);
    task->status = 0;
    safe_copy(task->wxid, sizeof(task->wxid), wxid);
    safe_copy(task->content, sizeof(task->content), content);

    asm_len = build_text_high_queue_asm(asm_addr, (int64_t)task, (int64_t)voice_final_queue_worker);
    FlushInstructionCache(GetCurrentProcess(), asm_addr, asm_len);
    wsprintfA(dbg,
              "[send-voice-final] queue to='%s' content_len=%d task=%I64x asm=%I64x",
              task->wxid,
              content_len,
              (unsigned __int64)(int64_t)task,
              (unsigned __int64)(int64_t)asm_addr);
    quote_dbg(dbg);

    queue_send((int64_t)asm_addr);
    for (int i = 0; i < 700; i++) {
        status = InterlockedCompareExchange(&task->status, 0, 0);
        if (status == 1 || status < 0) break;
        Sleep(10);
    }
    status = InterlockedCompareExchange(&task->status, 0, 0);
    if (status == 0) {
        InterlockedCompareExchange(&task->status, -2, 0);
        status = InterlockedCompareExchange(&task->status, 0, 0);
    }
    wsprintfA(dbg,
              "[send-voice-final] queue status=%d ret=%I64x holder=%I64x state=%I64x cached='%.180s' duration=%d format=%d voice_len=%d",
              (int)status,
              (unsigned __int64)task->ret,
              (unsigned __int64)task->holder,
              (unsigned __int64)task->state,
              task->cached_target,
              task->duration_ms,
              task->voice_format,
              task->voice_len);
    quote_dbg(dbg);

    retain_arena(arena);
    return status == 1;
}

static int send_voice_source_experimental(const char *wxid, const char *content) {
    const SIZE_T arena_size = SOURCE_VOICE_ARENA_SIZE;
    BYTE *arena;
    BYTE *asm_addr;
    SourceVoiceTask *task;
    int asm_len;
    LONG status;
    char dbg[768];
    int wxid_len;
    int content_len;
    if (!wxb_weixin_module() || !wxid || !content) return 0;
    wxid_len = lstrlenA(wxid);
    content_len = lstrlenA(content);
    if (wxid_len <= 0 || wxid_len >= TEXT_HIGH_WXID_MAX ||
        content_len <= 0 || content_len >= TEXT_HIGH_CONTENT_MAX) {
        quote_dbg("[send-source-voice] skipped: bad target/content for source-send path");
        return 0;
    }

    arena = (BYTE *)VirtualAlloc(NULL, arena_size, MEM_COMMIT | MEM_RESERVE, PAGE_EXECUTE_READWRITE);
    if (!arena) return 0;
    ZeroMemory(arena, arena_size);
    asm_addr = arena + 0x200;
    task = (SourceVoiceTask *)(arena + 0x1000);
    task->status = 0;
    task->duration_ms = 0;
    task->voice_format = 0;
    safe_copy(task->wxid, sizeof(task->wxid), wxid);
    safe_copy(task->content, sizeof(task->content), content);

    asm_len = build_text_high_queue_asm(asm_addr, (int64_t)task, (int64_t)source_voice_source_queue_worker);
    FlushInstructionCache(GetCurrentProcess(), asm_addr, asm_len);
    wsprintfA(dbg,
              "[send-source-voice] queue to='%s' content_len=%d task=%I64x asm=%I64x",
              task->wxid,
              content_len,
              (unsigned __int64)(int64_t)task,
              (unsigned __int64)(int64_t)asm_addr);
    quote_dbg(dbg);

    queue_send((int64_t)asm_addr);
    for (int i = 0; i < 700; i++) {
        status = InterlockedCompareExchange(&task->status, 0, 0);
        if (status == 1 || status < 0) break;
        Sleep(10);
    }
    status = InterlockedCompareExchange(&task->status, 0, 0);
    if (status == 0) {
        InterlockedCompareExchange(&task->status, -2, 0);
        status = InterlockedCompareExchange(&task->status, 0, 0);
    }
    wsprintfA(dbg,
              "[send-source-voice] queue status=%d ret=%I64x source={%I64x,%I64x} duration=%d format=%d out={%I64x,%I64x,%I64x,%I64x}",
              (int)status,
              (unsigned __int64)task->ret,
              (unsigned __int64)task->source_obj,
              (unsigned __int64)task->source_ctl,
              task->duration_ms,
              task->voice_format,
              (unsigned __int64)task->out0,
              (unsigned __int64)task->out1,
              (unsigned __int64)task->out2,
              (unsigned __int64)task->out3);
    quote_dbg(dbg);

    retain_arena(arena);
    return status == 1;
}

static void image_high_queue_worker(int64_t task_addr) {
    ImageHighTask *task = (ImageHighTask *)task_addr;
    int64_t base = (int64_t)wxb_weixin_module();
    int64_t image_pair[2] = {0, 0};
    int64_t root_pair[2] = {0, 0};
    int64_t mid_pair[2] = {0, 0};
    int64_t service_pair[2] = {0, 0};
    BYTE out[0x80];
    BYTE *arena;
    BYTE *pool;
    BYTE *pool_end;
    if (!task || !base) return;
    if (InterlockedCompareExchange(&task->status, 2, 0) != 0) return;
    arena = ((BYTE *)task) - 0x1000;
    pool = arena + 0x5000;
    pool_end = arena + 0x30000;
    ZeroMemory(out, sizeof(out));

    __try {
        char dbg[1024];
        char read_to[256] = {0};
        int64_t inner = (int64_t)(arena + 0x2000);
        int64_t ret;
        int64_t fsz = file_size_utf8(task->path);

        ((FnCall1)(base + WX_OFF_SEND_CALL1_IMAGE))(inner);
        wx_set_str_arena(inner + 0xB0, task->wxid, lstrlenA(task->wxid), &pool, pool_end);
        w32((void *)(inner + 0xD8), 3);
        wx_set_wstr_arena(inner + 0xE0, task->path, lstrlenA(task->path), &pool, pool_end);
        wx_set_str_arena(inner + 0x168, basename_utf8(task->path), lstrlenA(basename_utf8(task->path)), &pool, pool_end);
        w64((void *)(inner + 0x188), fsz);

        ((FnCall1)(base + WX_OFF_ROOM_INIT1))((int64_t)root_pair);
        if (!root_pair[0]) {
            quote_dbg("[send-high-image] root service init failed");
            InterlockedExchange(&task->status, -4);
            __leave;
        }
        ((FnCall3)(base + WX_OFF_ROOM_INIT2))(root_pair[0], (int64_t)mid_pair);
        if (!mid_pair[0]) {
            quote_dbg("[send-high-image] mid service init failed");
            InterlockedExchange(&task->status, -5);
            __leave;
        }
        ((FnCall3)(base + WX_OFF_SEND_TEXT_SERVICE))(mid_pair[0], (int64_t)service_pair);
        task->service = service_pair[0];
        if (!service_pair[0]) {
            quote_dbg("[send-high-image] service resolver failed");
            InterlockedExchange(&task->status, -6);
            __leave;
        }

        image_pair[0] = inner;
        image_pair[1] = 0; /* No control block: retain this arena and avoid freeing arena strings. */
        task->image_obj = inner;
        wx_read_sso_str(inner + 0xB0, read_to, sizeof(read_to));
        wsprintfA(dbg,
                  "[send-high-image] enter service=%I64x pair={%I64x,%I64x} inner_vt=%I64x to='%s' path='%.420s' typeD8=%d file_size=%I64d",
                  (unsigned __int64)service_pair[0],
                  (unsigned __int64)image_pair[0],
                  (unsigned __int64)image_pair[1],
                  (unsigned __int64)r64((void *)inner),
                  read_to,
                  task->path,
                  r32((void *)(inner + 0xD8)),
                  (long long)fsz);
        quote_dbg(dbg);

        ret = ((FnCall3Args)(base + WX_OFF_SEND_TEXT_HIGH_WRAP))(
            service_pair[0],
            (int64_t)out,
            (int64_t)image_pair
        );
        task->ret = ret;
        wsprintfA(dbg,
                  "[send-high-image] returned ret=%I64x out={%I64x,%I64x,%I64x,%I64x}",
                  (unsigned __int64)ret,
                  (unsigned __int64)r64((void *)out),
                  (unsigned __int64)r64((void *)(out + 8)),
                  (unsigned __int64)r64((void *)(out + 0x10)),
                  (unsigned __int64)r64((void *)(out + 0x18)));
        quote_dbg(dbg);
        InterlockedExchange(&task->status, 1);
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
        quote_dbg("[send-high-image] EXCEPTION");
        InterlockedExchange(&task->status, -1);
    }

    release_shared_pair((int64_t)service_pair);
    release_shared_pair((int64_t)mid_pair);
    release_shared_pair((int64_t)root_pair);
}

static int send_image_high_experimental(const char *wxid, const char *path) {
    const SIZE_T arena_size = 0x30000;
    BYTE *arena;
    BYTE *asm_addr;
    ImageHighTask *task;
    int asm_len;
    LONG status;
    char dbg[768];
    int wxid_len;
    int path_len;
    if (!wxb_weixin_module() || !wxid || !path) return 0;
    wxid_len = lstrlenA(wxid);
    path_len = lstrlenA(path);
    if (wxid_len <= 0 || wxid_len >= TEXT_HIGH_WXID_MAX ||
        path_len <= 0 || path_len >= IMAGE_HIGH_PATH_MAX ||
        file_size_utf8(path) <= 0) {
        quote_dbg("[send-high-image] skipped: bad input/path for experimental path");
        return 0;
    }

    arena = (BYTE *)VirtualAlloc(NULL, arena_size, MEM_COMMIT | MEM_RESERVE, PAGE_EXECUTE_READWRITE);
    if (!arena) return 0;
    ZeroMemory(arena, arena_size);
    asm_addr = arena + 0x200;
    task = (ImageHighTask *)(arena + 0x1000);
    task->status = 0;
    safe_copy(task->wxid, sizeof(task->wxid), wxid);
    safe_copy(task->path, sizeof(task->path), path);

    asm_len = build_text_high_queue_asm(asm_addr, (int64_t)task, (int64_t)image_high_queue_worker);
    FlushInstructionCache(GetCurrentProcess(), asm_addr, asm_len);
    wsprintfA(dbg,
              "[send-high-image] queue to='%s' path='%.500s' task=%I64x asm=%I64x",
              task->wxid,
              task->path,
              (unsigned __int64)(int64_t)task,
              (unsigned __int64)(int64_t)asm_addr);
    quote_dbg(dbg);

    queue_send((int64_t)asm_addr);
    for (int i = 0; i < 500; i++) {
        status = InterlockedCompareExchange(&task->status, 0, 0);
        if (status == 1 || status < 0) break;
        Sleep(10);
    }
    status = InterlockedCompareExchange(&task->status, 0, 0);
    if (status == 0) {
        InterlockedCompareExchange(&task->status, -2, 0);
        status = InterlockedCompareExchange(&task->status, 0, 0);
    }
    wsprintfA(dbg,
              "[send-high-image] queue status=%d ret=%I64x service=%I64x image_obj=%I64x",
              (int)status,
              (unsigned __int64)task->ret,
              (unsigned __int64)task->service,
              (unsigned __int64)task->image_obj);
    quote_dbg(dbg);

    retain_arena(arena);
    return status == 1;
}

static int has_prefix(const char *s, const char *prefix) {
    int i = 0;
    if (!s || !prefix) return 0;
    while (prefix[i]) {
        if (s[i] != prefix[i]) return 0;
        i++;
    }
    return 1;
}

static int send_message_inner(int msg_type, const char *wxid, const char *content,
                              const char *at_user_list) {
    if (!wxb_weixin_module() || !wxid || !content) return 0;

    const SIZE_T arena_size = 0x30000;
    BYTE *arena = (BYTE *)VirtualAlloc(NULL, arena_size, MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE);
    if (!arena) return 0;
    ZeroMemory(arena, arena_size);

    int64_t base = (int64_t)wxb_weixin_module();
    int64_t msg = (int64_t)(arena + 0x0000);
    int64_t rcxs = (int64_t)(arena + 0x1000);
    BYTE *buf1 = arena + 0x1100;
    BYTE *buf2 = arena + 0x1200;
    BYTE *buf3 = arena + 0x1300;
    BYTE *buf4 = arena + 0x1400;
    BYTE *buf5 = arena + 0x1500;
    BYTE *buf6 = arena + 0x1600;
    BYTE *pool = arena + 0x2000;
    BYTE *pool_end = arena + arena_size;

    int ok = 0;
    __try {
        if (msg_type == 3) {
            w64((void *)(msg + 0x8), 0x200000006LL);
            w64((void *)msg, base + WX_OFF_SEND_HC1_IMAGE);
            ((FnCall1)(base + WX_OFF_SEND_CALL1_IMAGE))(msg + 0x10);
        } else {
            w64((void *)(msg + 0x8), 0x100000001LL);
            w64((void *)msg, base + WX_OFF_SEND_HC1_TEXT);
            ((FnCall1)(base + WX_OFF_SEND_CALL1_TEXT))(msg + 0x10);
        }

        w64((void *)(msg + 0xE8), 1);
        w64((void *)(msg + 0x18), msg + 0x10);
        w64((void *)(msg + 0x20), msg);

        wx_set_str_arena(msg + 0xC0, wxid, lstrlenA(wxid), &pool, pool_end);
        if (msg_type == 3) {
            int64_t fsz = file_size_utf8(content);
            wx_set_wstr_arena(msg + 0xF0, content, lstrlenA(content), &pool, pool_end);
            wx_set_str_arena(msg + 0x178, basename_utf8(content), lstrlenA(basename_utf8(content)), &pool, pool_end);
            w64((void *)(msg + 0x198), fsz);
            w64((void *)(msg + 0xE8), 3);
        } else {
            if (at_user_list && at_user_list[0]) {
                /* Text inner+0x620 is the serialized atuserlist field. */
                wx_set_str_arena(msg + 0x630, at_user_list,
                                 lstrlenA(at_user_list), &pool, pool_end);
            }
            wx_set_str_arena(msg + 0x670, content, lstrlenA(content), &pool, pool_end);
        }

        w64((void *)rcxs, base + WX_OFF_SEND_CALL2);
        w64(buf1, msg + 0x10);
        w64(buf1 + 8, r64((void *)(msg + 0x18)));
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
        if (rax) {
            ((FnCall3)(base + WX_OFF_SEND_CALL3))(rcxs, rax);
            ok = 1;
        }
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
        ok = 0;
    }

    retain_arena(arena);
    return ok;
}

int send_text(const char *wxid, const char *content) {
    const char *source_contact_card_xml_prefix = "[[wx_source_contact_card_xml]]";
    const char *contact_card_xml_prefix = "[[wx_contact_card_xml]]";
    const char *source_music_xml_prefix = "[[wx_source_music_xml]]";
    const char *source_finder_feed_xml_prefix = "[[wx_source_finder_feed_xml]]";
    const char *source_voice_xml_prefix = "[[wx_source_voice_xml]]";
    const char *voice_final_prefix = "[[wx_voice_final]]";
    const char *source_voice_prefix = "[[wx_source_voice]]";
    const char *source_emoji_xml_prefix = "[[wx_source_emoji_xml]]";
    const char *source_text_prefix = "[[wx_source_text]]";
    const char *notice_prefix = "[[wx_high_notice]]";
    const char *type1_prefix = "[[wx_high_type1]]";
    const char *onecall_task_prefix = "[[wx_onecall_text_task]]";
    const char *onecall_dispatch_prefix = "[[wx_onecall_text_dispatch]]";
    if (has_prefix(content, source_contact_card_xml_prefix)) {
        return send_contact_card_xml_source_experimental(wxid, content + lstrlenA(source_contact_card_xml_prefix));
    }
    if (has_prefix(content, contact_card_xml_prefix)) {
        return send_text_high_experimental_typed(wxid, content + lstrlenA(contact_card_xml_prefix), 42);
    }
    if (has_prefix(content, source_music_xml_prefix)) {
        return send_music_xml_source_experimental(wxid, content + lstrlenA(source_music_xml_prefix));
    }
    if (has_prefix(content, source_finder_feed_xml_prefix)) {
        return send_finder_feed_xml_source_experimental(wxid, content + lstrlenA(source_finder_feed_xml_prefix));
    }
    if (has_prefix(content, source_voice_xml_prefix)) {
        quote_dbg("[send-source-voice] XML/CDN replay disabled after async crash; needs real voice-data source");
        return 0;
    }
    if (has_prefix(content, voice_final_prefix)) {
        return send_voice_final_experimental(wxid, content + lstrlenA(voice_final_prefix));
    }
    if (has_prefix(content, source_voice_prefix)) {
        if (file_exists(VOICE_SOURCE_BUILDER_SHIM_FILE)) {
            quote_dbg("[send-source-voice] experimental 0x20291D0 builder shim enabled for this process");
            return send_voice_source_experimental(wxid, content + lstrlenA(source_voice_prefix));
        }
        if (file_exists(VOICE_SOURCE_CTX_BRANCH_FILE)) {
            quote_dbg("[send-source-voice] experimental 0x20291D0 ctx branch enabled for this process");
            return send_voice_source_experimental(wxid, content + lstrlenA(source_voice_prefix));
        }
        quote_dbg("[send-source-voice] Silk path replay disabled after async crash; direct 0x20291D0 path needs voice-specific dispatch context");
        return 0;
    }
    if (has_prefix(content, source_emoji_xml_prefix)) {
        return send_emoji_xml_source_experimental(wxid, content + lstrlenA(source_emoji_xml_prefix));
    }
    if (has_prefix(content, source_text_prefix)) {
        return send_text_source_experimental(wxid, content + lstrlenA(source_text_prefix));
    }
    if (has_prefix(content, onecall_task_prefix)) {
        return send_text_onecall_experimental(wxid, content + lstrlenA(onecall_task_prefix), 0);
    }
    if (has_prefix(content, onecall_dispatch_prefix)) {
        return send_text_onecall_experimental(wxid, content + lstrlenA(onecall_dispatch_prefix), 1);
    }
    if (has_prefix(content, notice_prefix)) {
        return send_text_high_experimental(wxid, content + lstrlenA(notice_prefix));
    }
    if (has_prefix(content, type1_prefix)) {
        const char *body = content + lstrlenA(type1_prefix);
        if (send_text_high_experimental_typed(wxid, body, 1)) return 1;
        quote_dbg("[send-high-text] type1 fallback to CALL1/CALL2/CALL3");
        return send_message_inner(1, wxid, body, NULL);
    }
    return send_message_inner(1, wxid, content, NULL);
}

int send_at_text(const char *room_id, const char *wxids,
                 const char *nickname, const char *content) {
    SIZE_T nickname_len;
    SIZE_T content_len;
    SIZE_T body_size;
    char *body;
    int ok;
    if (!room_id || !room_id[0] || !wxids || !wxids[0] ||
        !nickname || !nickname[0] || !content || !content[0]) {
        return 0;
    }
    nickname_len = (SIZE_T)lstrlenA(nickname);
    content_len = (SIZE_T)lstrlenA(content);
    if (nickname_len > 4096 || content_len > 65536) return 0;
    body_size = nickname_len + content_len + 4;
    body = (char *)calloc(1, body_size);
    if (!body) return 0;
    body[0] = '@';
    CopyMemory(body + 1, nickname, nickname_len);
    body[1 + nickname_len] = ' ';
    CopyMemory(body + 2 + nickname_len, content, content_len);
    ok = send_message_inner(1, room_id, body, wxids);
    free(body);
    return ok;
}

int send_image(const char *wxid, const char *path) {
    const char *emoji_source_prefix = "[[wx_source_emoji]]";
    const char *file_source_prefix = "[[wx_source_file]]";
    const char *image_source_prefix = "[[wx_source_image]]";
    const char *image_high_prefix = "[[wx_high_image]]";
    const char *image_high_probe_prefix = "[[wx_high_image_probe]]";
    if (has_prefix(path, emoji_source_prefix)) {
        const char *body = path + lstrlenA(emoji_source_prefix);
        return send_emoji_source_experimental(wxid, body);
    }
    if (has_prefix(path, file_source_prefix)) {
        const char *body = path + lstrlenA(file_source_prefix);
        return send_file_source_experimental(wxid, body);
    }
    if (has_prefix(path, image_source_prefix)) {
        const char *body = path + lstrlenA(image_source_prefix);
        return send_image_source_experimental(wxid, body);
    }
    if (has_prefix(path, image_high_probe_prefix)) {
        const char *body = path + lstrlenA(image_high_probe_prefix);
        return send_image_high_experimental(wxid, body);
    }
    if (has_prefix(path, image_high_prefix)) {
        const char *body = path + lstrlenA(image_high_prefix);
        quote_dbg("[send-high-image] high-entry image disabled after blank-placeholder validation; fallback to CALL1/CALL2/CALL3");
        return send_message_inner(3, wxid, body, NULL);
    }
    return send_message_inner(3, wxid, path, NULL);
}

