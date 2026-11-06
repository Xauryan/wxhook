/*
 * Module: native forward queue and high/dispatch submit paths
 * Source: ../../../../wx_hook_bridge.dll.c lines 2924-4057
 */

#include "../include/wx_bridge_forward_send.h"
#include "../include/wx_bridge_debug_log.h"
#include "../include/wx_bridge_file_io.h"
#include "../include/wx_bridge_forward_builders.h"
#include "../include/wx_bridge_forward_entrypoints.h"
#include "../include/wx_bridge_forward_hooks.h"
#include "../include/wx_bridge_forward_protocol.h"
#include "../include/wx_bridge_forward_retained.h"
#include "../include/wx_bridge_memory.h"
#include "../include/wx_bridge_offsets.h"
#include "../include/wx_bridge_paths.h"
#include "../include/wx_bridge_send_runtime.h"
#include "../include/wx_bridge_runtime.h"
#include "../include/wx_bridge_native_call.h"
#include "../include/wx_bridge_text.h"
#include "../include/wx_bridge_wechat_string.h"

typedef struct ForwardQueueTask {
    volatile LONG status; /* 0=pending, 2=running, 1=ok, -1=exception, -2=cancelled */
    int mode;             /* 0=legacy low wrapper, 1=real UI high-entry */
    int variant;
    int64_t manager;
    int64_t controller;
    int64_t pair;
    int64_t ui_msg;
    int64_t high_pair;
    int64_t high_variant;
    int64_t high_out;
    int64_t patch_template;
    int patch_payload_target;
    int probe_only;
    int require_dispatch_confirm;
    int pre_dispatch_msg;
    char self_wxid[128];
    char to_wxid[512];
} ForwardQueueTask;

typedef struct ForwardRuntimeState {
    volatile LONG patch_active;
    volatile LONG patch_probe_only;
    volatile LONG patch_hit;
    char patch_self_wxid[128];
    char patch_to_wxid[512];
    volatile LONG64 patch_template;
    volatile LONG dispatch_seen;
    volatile LONG submit_seen;
} ForwardRuntimeState;

static ForwardRuntimeState g_forward_runtime_state = {0};

void forward_reset_runtime_patch_state(void) {
    InterlockedExchange(&g_forward_runtime_state.patch_active, 0);
    InterlockedExchange(&g_forward_runtime_state.patch_probe_only, 0);
    InterlockedExchange64(&g_forward_runtime_state.patch_template, 0);
}

static int forward_probe_ready(int64_t base, int64_t manager, int64_t pair, int variant) {
    int ready = -1;
    __try {
        int64_t msg = r64((void *)pair);
        int64_t out_pair[2] = {0, 0};
        BYTE scratch[0x300];
        int64_t obj = 0;
        char dbg[1024];
        ZeroMemory(scratch, sizeof(scratch));
        if (!msg || !manager) return -2;

        ((FnCall1)(base + WX_OFF_FORWARD_PREPARE_MSG))(msg);
        ((FnCall4)(base + WX_OFF_FORWARD_BUILD_TASK))(
            r64((void *)(manager + 920)),
            (int64_t)out_pair,
            msg,
            variant
        );
        obj = ((FnCall3)(base + WX_OFF_FORWARD_TASK_CAST))((int64_t)scratch, (int64_t)out_pair);
        ready = obj ? (int)*(volatile BYTE *)(obj + 0x290) : 0;
        wsprintfA(dbg,
                  "[forward-native-probe] msg=%I64x out={%I64x,%I64x} obj=%I64x ready=%d obj300=%d obj304=%d msg104=%d msg108=%I64x msg110=%I64x msg118=%d msg120=%d msg124=%d msg128=%d msg12c=%d msg160_len=%I64d msg180_len=%I64d",
                  (unsigned __int64)msg,
                  (unsigned __int64)out_pair[0],
                  (unsigned __int64)out_pair[1],
                  (unsigned __int64)obj,
                  ready,
                  obj ? r32((void *)(obj + 300)) : 0,
                  obj ? r32((void *)(obj + 304)) : 0,
                  r32((void *)(msg + 0x104)),
                  (unsigned __int64)r64((void *)(msg + 0x108)),
                  (unsigned __int64)r64((void *)(msg + 0x110)),
                  r32((void *)(msg + 0x118)),
                  r32((void *)(msg + 0x120)),
                  r32((void *)(msg + 0x124)),
                  r32((void *)(msg + 0x128)),
                  r32((void *)(msg + 0x12C)),
                  (long long)r64((void *)(msg + 0x170)),
                  (long long)r64((void *)(msg + 0x190)));
        forward_dbg(dbg);
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
        forward_dbg("[forward-native-probe] EXCEPTION");
        ready = -3;
    }
    return ready;
}

static void forward_patch_payload_target(int64_t base, int64_t obj, ForwardQueueTask *task) {
    char before_from[160] = {0};
    char before_to[560] = {0};
    char before_sender[160] = {0};
    char template_from[160] = {0};
    char template_to[560] = {0};
    char template_sender[160] = {0};
    char template_content[12288] = {0};
    char template_msgsource[4096] = {0};
    char after_from[160] = {0};
    char after_to[560] = {0};
    char after_sender[160] = {0};
    char dbg[2048];
    const char *self_wxid;
    const char *to_wxid;
    int64_t template_msg;
    int before_local;
    int after_local;
    int before_f128;
    int after_f128;
    int64_t before_msgid;
    int64_t after_msgid;
    if (!obj || !task || !task->patch_payload_target) return;
    self_wxid = task->self_wxid[0] ? task->self_wxid : wxb_self_wxid();
    to_wxid = task->to_wxid;
    if (!self_wxid || !self_wxid[0] || !to_wxid || !to_wxid[0]) return;
    template_msg = (int64_t)InterlockedCompareExchange64(&g_forward_runtime_state.patch_template, 0, 0);

    wx_read_sso_str(obj + 0x18, before_from, sizeof(before_from));
    wx_read_sso_str(obj + 0x38, before_to, sizeof(before_to));
    wx_read_sso_str(obj + 0x58, before_sender, sizeof(before_sender));
    before_local = r32((void *)(obj + 0x104));
    before_msgid = r64((void *)(obj + 0x108));
    before_f128 = r32((void *)(obj + 0x128));

    if (template_msg && template_msg != obj && is_readable_ptr(template_msg, 0x1A0)) {
        wx_read_sso_str(template_msg + 0x18, template_from, sizeof(template_from));
        wx_read_sso_str(template_msg + 0x38, template_to, sizeof(template_to));
        wx_read_sso_str(template_msg + 0x58, template_sender, sizeof(template_sender));
        wx_read_sso_str(template_msg + 0x160, template_content, sizeof(template_content));
        wx_read_sso_str(template_msg + 0x180, template_msgsource, sizeof(template_msgsource));

        w64((void *)(obj + 0x8), r64((void *)(template_msg + 0x8)));
        w64((void *)(obj + 0x10), r64((void *)(template_msg + 0x10)));
        wx_set_std_string_heap(base, obj + 0x18, template_from[0] ? template_from : self_wxid);
        wx_set_std_string_heap(base, obj + 0x38, template_to[0] ? template_to : to_wxid);
        wx_set_std_string_heap(base, obj + 0x58, template_sender[0] ? template_sender : self_wxid);
        if (template_content[0]) {
            wx_set_std_string_heap(base, obj + 0x160, template_content);
        }
        if (template_msgsource[0]) {
            wx_set_std_string_heap(base, obj + 0x180, template_msgsource);
        }
        w32((void *)(obj + 0x104), r32((void *)(template_msg + 0x104)));
        w64((void *)(obj + 0x108), r64((void *)(template_msg + 0x108)));
        w64((void *)(obj + 0x110), r64((void *)(template_msg + 0x110)));
        w32((void *)(obj + 0x118), r32((void *)(template_msg + 0x118)));
        w32((void *)(obj + 0x120), r32((void *)(template_msg + 0x120)));
        w32((void *)(obj + 0x124), r32((void *)(template_msg + 0x124)));
        w32((void *)(obj + 0x128), r32((void *)(template_msg + 0x128)));
        w32((void *)(obj + 0x12C), r32((void *)(template_msg + 0x12C)));
        w8((void *)(obj + 0x138), (BYTE)r32((void *)(template_msg + 0x138)));
    } else {
        wx_set_std_string_heap(base, obj + 0x18, self_wxid);
        wx_set_std_string_heap(base, obj + 0x38, to_wxid);
        wx_set_std_string_heap(base, obj + 0x58, self_wxid);
    }

    wx_read_sso_str(obj + 0x18, after_from, sizeof(after_from));
    wx_read_sso_str(obj + 0x38, after_to, sizeof(after_to));
    wx_read_sso_str(obj + 0x58, after_sender, sizeof(after_sender));
    after_local = r32((void *)(obj + 0x104));
    after_msgid = r64((void *)(obj + 0x108));
    after_f128 = r32((void *)(obj + 0x128));
    wsprintfA(dbg,
              "[forward-native-patch] obj=%I64x tmpl=%I64x local=%d->%d msgid=%I64x->%I64x f128=%d->%d from='%s'->'%s' to='%s'->'%s' sender='%s'->'%s'",
              (unsigned __int64)obj,
              (unsigned __int64)template_msg,
              before_local, after_local,
              (unsigned __int64)before_msgid, (unsigned __int64)after_msgid,
              before_f128, after_f128,
              before_from, after_from,
              before_to, after_to,
              before_sender, after_sender);
    forward_dbg(dbg);
}

static void cache_filehelper_copy_from_forward_model(int64_t msg, const char *hook_source) {
    char from[160] = {0};
    char to[560] = {0};
    char sender[160] = {0};
    char content[4096] = {0};
    char msgsource[1024] = {0};
    int32_t local_id;
    int64_t msgid;
    int32_t msg_type;
    int32_t msg_subtype;
    int32_t createtime;
    char dbg[512];
    if (!msg || !is_readable_ptr(msg, 0x1A0)) return;
    __try {
        wx_read_sso_str(msg + 0x18, from, sizeof(from));
        wx_read_sso_str(msg + 0x38, to, sizeof(to));
        wx_read_sso_str(msg + 0x58, sender, sizeof(sender));
        if (!str_eq(to, "filehelper")) return;
        if (wxb_self_wxid()[0] && !str_eq(from, wxb_self_wxid()) && !str_eq(sender, wxb_self_wxid())) return;
        if (!wxb_self_wxid()[0] && is_wxid_like(from) && !is_chatroom_id(from)) wxb_learn_self_wxid(from);

        local_id = r32((void *)(msg + 0x104));
        msgid = r64((void *)(msg + 0x108));
        if (!local_id || !msgid) return;

        msg_type = r32((void *)(msg + 0x118));
        if (!msg_type) msg_type = r32((void *)(msg + 0x0C));
        if (!msg_type) return;

        /* +0x120/+0x128 are submit state fields. The original subtype survives in msg+0x10 low32. */
        msg_subtype = (int32_t)r64((void *)(msg + 0x10));
        createtime = r32((void *)(msg + 0x124));
        if (!createtime) createtime = (int32_t)time(NULL);
        wx_read_sso_str(msg + 0x160, content, sizeof(content));
        wx_read_sso_str(msg + 0x180, msgsource, sizeof(msgsource));

        wsprintfA(dbg,
                  "filehelper_out_copy_hook: source=%s msgid=%I64d local=%d type=%d/%d state120=%d state128=%d\r\n",
                  hook_source ? hook_source : "",
                  (long long)msgid,
                  (int)local_id,
                  (int)msg_type,
                  (int)msg_subtype,
                  (int)r32((void *)(msg + 0x120)),
                  (int)r32((void *)(msg + 0x128)));
        append_file_bytes(REVOKE_LOG, dbg, (DWORD)lstrlenA(dbg));

        cache_filehelper_outgoing_copy(msgid, local_id, msg_type, msg_subtype,
                                       createtime, content,
                                       sender[0] ? sender : from,
                                       to, msgsource);
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
        append_file_bytes(REVOKE_LOG,
                          "filehelper_out_copy_hook: exception\r\n",
                          (DWORD)lstrlenA("filehelper_out_copy_hook: exception\r\n"));
    }
}

int64_t __fastcall forward_dispatch_hook(int64_t manager, int64_t seq, int64_t obj) {
    int active = InterlockedCompareExchange(&g_forward_runtime_state.patch_active, 0, 0);
    int64_t retv;
    InterlockedIncrement(&g_forward_runtime_state.dispatch_seen);
    {
        char dbg[1024];
        char from[96] = {0};
        char to[160] = {0};
        char sender[96] = {0};
        int64_t ret = (int64_t)_ReturnAddress();
        int probe = (int)InterlockedCompareExchange(&g_forward_runtime_state.patch_probe_only, 0, 0);
        if (obj) {
            wx_read_sso_str(obj + 0x18, from, sizeof(from));
            wx_read_sso_str(obj + 0x38, to, sizeof(to));
            wx_read_sso_str(obj + 0x58, sender, sizeof(sender));
        }
        wsprintfA(dbg,
                  "[forward-dispatch-hook] hit ret=%I64x manager=%I64x seq=%I64x obj=%I64x vt=%I64x active=%d probe=%d q8=%I64x q10=%I64x d104=%d d118=%d d128=%d obj300=%d obj304=%d from='%s' to='%s' sender='%s'",
                  (unsigned __int64)ret,
                  (unsigned __int64)manager,
                  (unsigned __int64)seq,
                  (unsigned __int64)obj,
                  (unsigned __int64)(obj ? r64((void *)obj) : 0),
                  active,
                  probe,
                  (unsigned __int64)(obj ? r64((void *)(obj + 0x8)) : 0),
                  (unsigned __int64)(obj ? r64((void *)(obj + 0x10)) : 0),
                  obj ? r32((void *)(obj + 0x104)) : 0,
                  obj ? r32((void *)(obj + 0x118)) : 0,
                  obj ? r32((void *)(obj + 0x128)) : 0,
                  obj ? r32((void *)(obj + 0x300)) : 0,
                  obj ? r32((void *)(obj + 0x304)) : 0,
                  from, to, sender);
        forward_dbg(dbg);
    }
    if (active && obj) {
        ForwardQueueTask temp;
        ZeroMemory(&temp, sizeof(temp));
        temp.patch_payload_target = 1;
        safe_copy(temp.self_wxid, sizeof(temp.self_wxid), g_forward_runtime_state.patch_self_wxid);
        safe_copy(temp.to_wxid, sizeof(temp.to_wxid), g_forward_runtime_state.patch_to_wxid);
        forward_patch_payload_target((int64_t)wxb_weixin_module(), obj, &temp);
        InterlockedIncrement(&g_forward_runtime_state.patch_hit);
        if (InterlockedCompareExchange(&g_forward_runtime_state.patch_probe_only, 0, 0)) {
            forward_dbg("[forward-dispatch-hook] probe-only hit; original dispatch skipped");
            return 0;
        }
    }
    retv = forward_call_dispatch_original(manager, seq, obj);
    return retv;
}

int64_t __fastcall forward_submit_hook(int64_t manager, int64_t pair, int64_t variant, int64_t do_dispatch) {
    int64_t msg_for_cache = 0;
    int64_t retv;
    InterlockedIncrement(&g_forward_runtime_state.submit_seen);
    __try {
        char dbg[1024];
        char from[96] = {0};
        char to[160] = {0};
        char sender[96] = {0};
        int64_t base = (int64_t)wxb_weixin_module();
        int64_t ctxvt = manager ? r64((void *)manager) : 0;
        int64_t ret = (int64_t)_ReturnAddress();
        int64_t msg = pair ? r64((void *)pair) : 0;
        forward_remember_manager(base, manager);
        msg_for_cache = msg;
        if (msg) {
            wx_read_sso_str(msg + 0x18, from, sizeof(from));
            wx_read_sso_str(msg + 0x38, to, sizeof(to));
            wx_read_sso_str(msg + 0x58, sender, sizeof(sender));
        }
        wsprintfA(dbg,
                  "[forward-submit-hook] ret=%I64x manager=%I64x vt=%I64x pair=%I64x msg=%I64x msgvt=%I64x q8=%I64x q10=%I64x variant=%d dispatch=%d active=%d mgr398={%I64x,%I64x} sig1state=%I64x type=%d/%d local=%d msgid=%I64x ts=%d f128=%d f12c=%d from='%s' to='%s' sender='%s'",
                  (unsigned __int64)ret,
                  (unsigned __int64)manager,
                  (unsigned __int64)ctxvt,
                  (unsigned __int64)pair,
                  (unsigned __int64)msg,
                  (unsigned __int64)(msg ? r64((void *)msg) : 0),
                  (unsigned __int64)(msg ? r64((void *)(msg + 0x8)) : 0),
                  (unsigned __int64)(msg ? r64((void *)(msg + 0x10)) : 0),
                  (int)variant,
                  (int)do_dispatch,
                  (int)InterlockedCompareExchange(&g_forward_runtime_state.patch_active, 0, 0),
                  (unsigned __int64)(manager ? r64((void *)(manager + 0x398)) : 0),
                  (unsigned __int64)(manager ? r64((void *)(manager + 0x3A0)) : 0),
                  (unsigned __int64)(manager ? r64((void *)(manager + 0xA0)) : 0),
                  msg ? r32((void *)(msg + 0x118)) : 0,
                  msg ? r32((void *)(msg + 0x120)) : 0,
                  msg ? r32((void *)(msg + 0x104)) : 0,
                  (unsigned __int64)(msg ? r64((void *)(msg + 0x108)) : 0),
                  msg ? r32((void *)(msg + 0x124)) : 0,
                  msg ? r32((void *)(msg + 0x128)) : 0,
                  msg ? r32((void *)(msg + 0x12C)) : 0,
                  from, to, sender);
        forward_dbg(dbg);
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
        forward_dbg("[forward-submit-hook] EXCEPTION before original");
    }
    retv = forward_call_submit_original(manager, pair, variant, do_dispatch);
    cache_filehelper_copy_from_forward_model(msg_for_cache, "submit");
    return retv;
}

int64_t __fastcall forward_ui_entry_hook(int64_t ctx, int64_t arg2, int64_t arg3, int64_t arg4) {
    __try {
        char dbg[1536];
        char from[96] = {0};
        char to[160] = {0};
        char sender[96] = {0};
        int64_t base = (int64_t)wxb_weixin_module();
        int64_t a2_0 = arg2 ? r64((void *)arg2) : 0;
        int64_t a2_8 = arg2 ? r64((void *)(arg2 + 8)) : 0;
        int64_t a2_10 = arg2 ? r64((void *)(arg2 + 0x10)) : 0;
        int64_t a3_0 = arg3 ? r64((void *)arg3) : 0;
        int64_t a3_8 = arg3 ? r64((void *)(arg3 + 8)) : 0;
        int64_t a3_10 = arg3 ? r64((void *)(arg3 + 0x10)) : 0;
        int64_t direct_msgid = is_readable_ptr(arg3, 0x190) ? r64((void *)(arg3 + 0x108)) : 0;
        int64_t pointee_msgid = is_readable_ptr(a3_0, 0x190) ? r64((void *)(a3_0 + 0x108)) : 0;
        int64_t msg = direct_msgid ? arg3 : (pointee_msgid ? a3_0 : 0);
        if (!msg && is_readable_ptr(arg3, 0x190)) msg = arg3;
        if (!msg && is_readable_ptr(a3_0, 0x190)) msg = a3_0;
        if (msg) {
            wx_read_sso_str(msg + 0x18, from, sizeof(from));
            wx_read_sso_str(msg + 0x38, to, sizeof(to));
            wx_read_sso_str(msg + 0x58, sender, sizeof(sender));
        }
        wsprintfA(dbg,
                  "[forward-ui-entry-hook] ctx=%I64x arg2=%I64x arg3=%I64x arg4=%I64x active=%d is_mgr=%d ctxvt=%I64x mgr398={%I64x,%I64x} mgr920={%I64x,%I64x} arg2={%I64x,%I64x,%I64x} arg3={%I64x,%I64x,%I64x} msg=%I64x type=%d/%d local=%d msgid=%I64x f128=%d f12c=%d from='%s' to='%s' sender='%s'",
                  (unsigned __int64)ctx,
                  (unsigned __int64)arg2,
                  (unsigned __int64)arg3,
                  (unsigned __int64)arg4,
                  (int)InterlockedCompareExchange(&g_forward_runtime_state.patch_active, 0, 0),
                  (int)(base && is_forward_manager_candidate_any(base, ctx)),
                  (unsigned __int64)(ctx ? r64((void *)ctx) : 0),
                  (unsigned __int64)(ctx ? r64((void *)(ctx + 0x398)) : 0),
                  (unsigned __int64)(ctx ? r64((void *)(ctx + 0x3A0)) : 0),
                  (unsigned __int64)(ctx ? r64((void *)(ctx + 920)) : 0),
                  (unsigned __int64)(ctx ? r64((void *)(ctx + 928)) : 0),
                  (unsigned __int64)a2_0,
                  (unsigned __int64)a2_8,
                  (unsigned __int64)a2_10,
                  (unsigned __int64)a3_0,
                  (unsigned __int64)a3_8,
                  (unsigned __int64)a3_10,
                  (unsigned __int64)msg,
                  msg ? r32((void *)(msg + 0x118)) : 0,
                  msg ? r32((void *)(msg + 0x120)) : 0,
                  msg ? r32((void *)(msg + 0x104)) : 0,
                  (unsigned __int64)(msg ? r64((void *)(msg + 0x108)) : 0),
                  msg ? r32((void *)(msg + 0x128)) : 0,
                  msg ? r32((void *)(msg + 0x12C)) : 0,
                  from, to, sender);
        forward_dbg(dbg);
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
        forward_dbg("[forward-ui-entry-hook] EXCEPTION before original");
    }
    return forward_call_ui_entry_original(ctx, arg2, arg3, arg4);
}

static void forward_queue_worker_high(ForwardQueueTask *task, int64_t base) {
    if (!task || !base) return;
    __try {
        char dbg[2048];
        char marker[384] = {0};
        char target[560] = {0};
        LONG dispatch_before, dispatch_after, submit_before, submit_after;
        int64_t hv = task->high_variant;
        int64_t pair_obj = task->high_pair ? r64((void *)task->high_pair) : 0;
        int64_t pair_ctl = task->high_pair ? r64((void *)(task->high_pair + 8)) : 0;
        int64_t ctl_refs = pair_ctl ? r64((void *)(pair_ctl + 8)) : 0;
        int64_t ret = 0;
        int gate_raw = -9;
        int gate_low8 = -9;
        int use_wrapper_b = 0;
        LONG patch_before = InterlockedCompareExchange(&g_forward_runtime_state.patch_hit, 0, 0);
        LONG patch_after = patch_before;

        if (hv) {
            wx_read_sso_str(hv + 0x38, marker, sizeof(marker));
            wx_read_sso_str(hv + 0xB0, target, sizeof(target));
        }

        wsprintfA(dbg,
                  "[forward-high-entry] enter controller=%I64x vt=%I64x holder=%I64x map90=%I64x out=%I64x pair=%I64x pair_obj=%I64x pair_ctl=%I64x objvt=%I64x ctlvt=%I64x ctlrefs=%I64x probe=%d marker='%s' target='%s'",
                  (unsigned __int64)task->controller,
                  (unsigned __int64)(task->controller ? r64((void *)task->controller) : 0),
                  (unsigned __int64)(task->controller ? r64((void *)(task->controller + 0xB78)) : 0),
                  (unsigned __int64)(task->controller ? r64((void *)(r64((void *)(task->controller + 0xB78)) + 0x90)) : 0),
                  (unsigned __int64)task->high_out,
                  (unsigned __int64)task->high_pair,
                  (unsigned __int64)pair_obj,
                  (unsigned __int64)pair_ctl,
                  (unsigned __int64)(pair_obj ? r64((void *)pair_obj) : 0),
                  (unsigned __int64)(pair_ctl ? r64((void *)pair_ctl) : 0),
                  (unsigned __int64)ctl_refs,
                  task->probe_only,
                  marker,
                  target);
        forward_dbg(dbg);

        if (hv) {
            int64_t item = hv + 0xD8;
            char key_s70[192] = {0};
            char key_spc[512] = {0};
            char key_b[768] = {0};
            char fallback_key_a[768] = {0};
            int key_local94 = 0;
            unsigned int key_a8 = 0;
            unsigned __int64 key_type_subtype = 0;
            int64_t key_spc_obj = 0;
            int64_t key_spc_ctl = 0;
            __try {
                gate_raw = (int)((FnCall1)(base + WX_OFF_SEND_ITEM_READY_CHECK))(item);
                gate_low8 = gate_raw & 0xFF;
            }
            __except (EXCEPTION_EXECUTE_HANDLER) {
                gate_raw = -3;
                gate_low8 = -3;
            }
            wsprintfA(dbg,
                      "[forward-high-ready] item=%I64x gate_raw=%d gate_low8=%d vt=%I64x type=%d/%d local=%d msgid=%I64x ts=%I64x state=%d media=%d content_len=%I64d source_len=%I64d srcA={%I64x,%I64x} srcB={%I64x,%I64x}",
                      (unsigned __int64)item,
                      gate_raw,
                      gate_low8,
                      (unsigned __int64)r64((void *)item),
                      r32((void *)(item + 0x08)),
                      r32((void *)(item + 0x0C)),
                      r32((void *)(item + 0x94)),
                      (unsigned __int64)r64((void *)(item + 0x98)),
                      (unsigned __int64)r64((void *)(item + 0xA0)),
                      r32((void *)(item + 0xAC)),
                      r32((void *)(item + 0xB4)),
                      (long long)r64((void *)(item + 0xC8)),
                      (long long)r64((void *)(item + 0xE8)),
                      (unsigned __int64)r64((void *)(item + 0x270)),
                      (unsigned __int64)r64((void *)(item + 0x278)),
                      (unsigned __int64)r64((void *)(item + 0x290)),
                      (unsigned __int64)r64((void *)(item + 0x298)));
            forward_dbg(dbg);

            /*
             * Wrapper-B (181659DD0) reaches 18165A320 with:
             *
             *   a3 = &copied_send_item[0x70]     => primary map key A
             *   a4 = copied_qword_at_0x08        => used by 1834EC3C0 fallback key
             *   a5 = 18099C9D0(copied_send_item) => candidate-list match key B
             *
             * 18099C9D0 itself derives key B from:
             *   +0x70  std::string
             *   +0x94  dword
             *   +0xA8  dword
             *   +0x290/+0x298 shared_ptr C, where obj+0x270 (=> send_item+0x4E0)
             *   is read as an optional fallback string source through 1809A29C0.
             * Log the evidence directly so future traces can compare against the
             * real UI-selected source item without re-reading IDA.
             */
            wx_read_sso_str(item + 0x70, key_s70, sizeof(key_s70));
            key_local94 = r32((void *)(item + 0x94));
            key_a8 = (unsigned int)r32((void *)(item + 0xA8));
            key_type_subtype = (unsigned __int64)r64((void *)(item + 0x08));
            key_spc_obj = r64((void *)(item + 0x290));
            key_spc_ctl = r64((void *)(item + 0x298));
            if (key_spc_obj) {
                wx_read_sso_str(key_spc_obj + 0x270, key_spc, sizeof(key_spc));
            }
            if (key_local94 == 0 && key_spc[0]) {
                safe_copy(key_b, sizeof(key_b), key_spc);
            } else {
                wsprintfA(key_b, "%s_%u_%d", key_s70, key_a8, key_local94);
            }
            wsprintfA(fallback_key_a, "%s_%I64u", key_s70, key_type_subtype);
            wsprintfA(dbg,
                      "[forward-high-key] item=%I64x keyA.s70='%s' local94=%d a8=%u type_sub=%I64u spC={%I64x,%I64x} spC_270='%s' keyB='%s' fallbackKeyA='%s'",
                      (unsigned __int64)item,
                      key_s70,
                      key_local94,
                      key_a8,
                      key_type_subtype,
                      (unsigned __int64)key_spc_obj,
                      (unsigned __int64)key_spc_ctl,
                      key_spc,
                      key_b,
                      fallback_key_a);
            forward_dbg(dbg);
            if (gate_low8 == 0) {
                wsprintfA(dbg,
                          "[forward-high-entry] diagnostic: 18099EDA0 low8=%d; route via wrapper-B for helper resolution",
                          gate_low8);
                forward_dbg(dbg);
                use_wrapper_b = 1;
            }
        }

        dispatch_before = InterlockedCompareExchange(&g_forward_runtime_state.dispatch_seen, 0, 0);
        submit_before = InterlockedCompareExchange(&g_forward_runtime_state.submit_seen, 0, 0);
        safe_copy(g_forward_runtime_state.patch_self_wxid, sizeof(g_forward_runtime_state.patch_self_wxid), task->self_wxid);
        safe_copy(g_forward_runtime_state.patch_to_wxid, sizeof(g_forward_runtime_state.patch_to_wxid), task->to_wxid);
        InterlockedExchange(&g_forward_runtime_state.patch_probe_only, task->probe_only ? 1 : 0);
        InterlockedExchange(&g_forward_runtime_state.patch_active, task->patch_payload_target ? 1 : 0);

        if (use_wrapper_b) {
            forward_dbg("[forward-high-entry] route=wrapper_b(181659DD0)");
            ret = ((FnCall3Args)(base + WX_OFF_FORWARD_HIGH_WRAP_B))(task->controller, task->high_out, task->high_pair);
        } else {
            forward_dbg("[forward-high-entry] route=wrapper_a(181659D10)");
            ret = ((FnCall3Args)(base + WX_OFF_FORWARD_HIGH_WRAP_A))(task->controller, task->high_out, task->high_pair);
        }

        if (task->patch_payload_target || task->require_dispatch_confirm) {
            int max_wait = task->require_dispatch_confirm ? 500 : 300;
            for (int i = 0; i < max_wait; i++) {
                patch_after = InterlockedCompareExchange(&g_forward_runtime_state.patch_hit, 0, 0);
                dispatch_after = InterlockedCompareExchange(&g_forward_runtime_state.dispatch_seen, 0, 0);
                submit_after = InterlockedCompareExchange(&g_forward_runtime_state.submit_seen, 0, 0);
                if (patch_after != patch_before ||
                    dispatch_after != dispatch_before ||
                    submit_after != submit_before) {
                    break;
                }
                Sleep(10);
            }
        }
        InterlockedExchange(&g_forward_runtime_state.patch_active, 0);
        InterlockedExchange(&g_forward_runtime_state.patch_probe_only, 0);
        dispatch_after = InterlockedCompareExchange(&g_forward_runtime_state.dispatch_seen, 0, 0);
        submit_after = InterlockedCompareExchange(&g_forward_runtime_state.submit_seen, 0, 0);
        patch_after = InterlockedCompareExchange(&g_forward_runtime_state.patch_hit, 0, 0);
        wsprintfA(dbg,
                  "[forward-high-entry] returned ret=%I64x out={%I64x,%I64x,%I64x,%I64x} submit_delta=%d dispatch_delta=%d patch_delta=%d",
                  (unsigned __int64)ret,
                  (unsigned __int64)(task->high_out ? r64((void *)task->high_out) : 0),
                  (unsigned __int64)(task->high_out ? r64((void *)(task->high_out + 8)) : 0),
                  (unsigned __int64)(task->high_out ? r64((void *)(task->high_out + 0x10)) : 0),
                  (unsigned __int64)(task->high_out ? r64((void *)(task->high_out + 0x18)) : 0),
                  (int)(submit_after - submit_before),
                  (int)(dispatch_after - dispatch_before),
                  (int)(patch_after - patch_before));
        forward_dbg(dbg);

        if (patch_after != patch_before ||
            submit_after != submit_before ||
            dispatch_after != dispatch_before) {
            InterlockedExchange(&task->status, 1);              /* confirmed: submit/dispatch fired */
        } else if (task->require_dispatch_confirm) {
            InterlockedExchange(&task->status, -5);
            forward_dbg("[forward-high-entry] rejected: type=47 gate accepted but no submit/dispatch confirmation");
        } else if (!task->patch_payload_target && gate_raw != 0 && gate_raw != -3 && gate_raw != -9) {
            /* The forward gate (18099EDA0) accepted the real send_item, so wrapper-A built the
             * container and registered the forward. The actual submit/dispatch fires ASYNC on a
             * separate WeChat send thread AFTER this worker returns (proven by the post-return
             * [forward-submit-hook]/[forward-dispatch-hook] lines), so the synchronous delta is 0
             * here. Treat gate acceptance as success rather than a false-negative -4. */
            InterlockedExchange(&task->status, 1);
            forward_dbg("[forward-high-entry] success via gate acceptance (async submit pending)");
        } else {
            InterlockedExchange(&task->status, -4);
        }
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
        InterlockedExchange(&g_forward_runtime_state.patch_active, 0);
        InterlockedExchange(&g_forward_runtime_state.patch_probe_only, 0);
        forward_dbg("[forward-high-entry] EXCEPTION");
        InterlockedExchange(&task->status, -1);
    }
}

static void forward_queue_worker(int64_t task_addr) {
    ForwardQueueTask *task = (ForwardQueueTask *)task_addr;
    int64_t base = (int64_t)wxb_weixin_module();
    if (!task || !base) return;
    if (InterlockedCompareExchange(&task->status, 2, 0) != 0) return;
    if (task->mode == 1) {
        forward_queue_worker_high(task, base);
        return;
    }
    __try {
        char dbg[1024];
        /* ui_msg is the OUTGOING model (from=self, to=target, target_fields=1) built by
         * forward_build_submit_msg. We must NOT use task->pair/src_msg here: for appmsg
         * (target_fields=0) src_msg keeps to=SOURCE peer, so build_task would resolve the
         * wrong (source) session. Only text happened to get to=target in src_msg. */
        int64_t model = task->ui_msg ? task->ui_msg : r64((void *)task->pair);
        int64_t pre_dispatch_model = task->patch_template ? task->patch_template : model;
        int64_t out_task[2] = {0, 0};
        BYTE scratch[0x300];
        int64_t task_body = 0;
        int ready = 0;
        char msg_from[160] = {0};
        char msg_to[560] = {0};
        char msg_sender[160] = {0};
        ZeroMemory(scratch, sizeof(scratch));
        if (!model || !task->manager) {
            forward_dbg("[forward-submit] missing model/manager");
            InterlockedExchange(&task->status, -4);
            return;
        }
        wx_read_sso_str(model + 0x18, msg_from, sizeof(msg_from));
        wx_read_sso_str(model + 0x38, msg_to, sizeof(msg_to));
        wx_read_sso_str(model + 0x58, msg_sender, sizeof(msg_sender));
        wsprintfA(dbg,
                  "[forward-submit] enter manager=%I64x mgr398={%I64x,%I64x} model=%I64x variant=%d type=%d/%d from='%s' to='%s' sender='%s'",
                  (unsigned __int64)task->manager,
                  (unsigned __int64)r64((void *)(task->manager + 920)),
                  (unsigned __int64)r64((void *)(task->manager + 928)),
                  (unsigned __int64)model, task->variant,
                  r32((void *)(model + 0xC)), r32((void *)(model + 0x10)),
                  msg_from, msg_to, msg_sender);
        forward_dbg(dbg);

        if (task->pre_dispatch_msg && pre_dispatch_model) {
            wsprintfA(dbg,
                      "[forward-submit] pre-dispatch manager=%I64x model=%I64x type=%d/%d q8=%I64x",
                      (unsigned __int64)task->manager,
                      (unsigned __int64)pre_dispatch_model,
                      r32((void *)(pre_dispatch_model + 0x118)),
                      r32((void *)(pre_dispatch_model + 0x120)),
                      (unsigned __int64)r64((void *)(pre_dispatch_model + 0x8)));
            forward_dbg(dbg);
            ((FnCall3Args)(base + WX_OFF_FORWARD_DISPATCH))(task->manager, 0xFFFFFFFFLL, pre_dispatch_model);
        }

        /*
         * Clean submit/dispatch path (sub_1834E73F0 body MINUS the manager+0x398 guard):
         *   prepare_msg(model) -> build_task(_, &out, model, variant) -> task_cast -> dispatch
         * build_task (sub_1834176F0) resolves the TARGET session from the model itself
         * (sub_1809D5170 -> model peer wxid) via the global controller chain and IGNORES
         * its first arg, so no live forward-UI session (mgr398) is needed. The model carries
         * the full content XML at +0x160, which is sufficient for appmsg (card/link) forwarding
         * WITHOUT the binary media sub-objects the gate's send-item path requires.
         */
        ((FnCall1)(base + WX_OFF_FORWARD_PREPARE_MSG))(model);
        ((FnCall4)(base + WX_OFF_FORWARD_BUILD_TASK))(
            r64((void *)(task->manager + 920)),   /* mgr398 if present; build_task ignores it */
            (int64_t)out_task,
            model,
            task->variant);
        task_body = ((FnCall3)(base + WX_OFF_FORWARD_TASK_CAST))((int64_t)scratch, (int64_t)out_task);
        ready = task_body ? (int)*(volatile BYTE *)(task_body + 0x290) : 0;
        wsprintfA(dbg,
                  "[forward-submit] out={%I64x,%I64x} task_body=%I64x ready=%d obj300=%d obj304=%d",
                  (unsigned __int64)out_task[0], (unsigned __int64)out_task[1],
                  (unsigned __int64)task_body, ready,
                  task_body ? r32((void *)(task_body + 300)) : 0,
                  task_body ? r32((void *)(task_body + 304)) : 0);
        forward_dbg(dbg);
        if (!task_body) {
            forward_dbg("[forward-submit] build_task produced no task body (target session unresolved?)");
            InterlockedExchange(&task->status, -4);
            return;
        }
        if (task->probe_only) {
            wsprintfA(dbg, "[forward-submit] probe-only ready=%d (no dispatch)", ready);
            forward_dbg(dbg);
            InterlockedExchange(&task->status, ready ? 1 : -5);
            return;
        }
        /* The real submit (sub_1834E73F0) ONLY dispatches when the task is ready
         * (*(task_body+0x290) != 0); a ready==0 task is incomplete and dispatching
         * it crashes. So gate the dispatch on the same flag. */
        if (!ready) {
            forward_dbg("[forward-submit] task NOT ready (task_body+0x290==0) -> skip dispatch (model likely incomplete for this type)");
            InterlockedExchange(&task->status, -5);
            return;
        }
        safe_copy(g_forward_runtime_state.patch_self_wxid, sizeof(g_forward_runtime_state.patch_self_wxid), task->self_wxid);
        safe_copy(g_forward_runtime_state.patch_to_wxid, sizeof(g_forward_runtime_state.patch_to_wxid), task->to_wxid);
        if (!InterlockedCompareExchange64(&g_forward_runtime_state.patch_template, 0, 0) && task->patch_template) {
            InterlockedExchange64(&g_forward_runtime_state.patch_template, task->patch_template);
        }
        InterlockedExchange(&g_forward_runtime_state.patch_probe_only, task->probe_only ? 1 : 0);
        InterlockedExchange(&g_forward_runtime_state.patch_active, task->patch_payload_target ? 1 : 0);
        ((FnCall3Args)(base + WX_OFF_FORWARD_DISPATCH))(task->manager, 0xFFFFFFFFLL, task_body);
        InterlockedExchange(&g_forward_runtime_state.patch_active, 0);
        InterlockedExchange(&g_forward_runtime_state.patch_probe_only, 0);
        InterlockedExchange64(&g_forward_runtime_state.patch_template, 0);
        forward_dbg("[forward-submit] dispatched ok");
        InterlockedExchange(&task->status, 1);
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
        InterlockedExchange(&g_forward_runtime_state.patch_active, 0);
        InterlockedExchange(&g_forward_runtime_state.patch_probe_only, 0);
        InterlockedExchange64(&g_forward_runtime_state.patch_template, 0);
        forward_dbg("[forward-submit] EXCEPTION");
        InterlockedExchange(&task->status, -1);
    }
}

static int build_forward_queue_asm(BYTE *p, int64_t task_addr, int64_t worker_addr) {
    BYTE *start = p;
    BYTE pro[] = {0x48,0x83,0xEC,0x28}; /* sub rsp, 0x28 */
    BYTE epi[] = {0x48,0x83,0xC4,0x28,0xC3}; /* add rsp, 0x28; ret */
    copy_bytes(p, pro, sizeof(pro)); p += sizeof(pro);
    *p++ = 0x48; *p++ = 0xB9;        /* mov rcx, imm64 */
    *(int64_t *)p = task_addr; p += 8;
    *p++ = 0x48; *p++ = 0xB8;        /* mov rax, imm64 */
    *(int64_t *)p = worker_addr; p += 8;
    *p++ = 0xFF; *p++ = 0xD0;        /* call rax */
    copy_bytes(p, epi, sizeof(epi)); p += sizeof(epi);
    return (int)(p - start);
}

static int send_forward_text_native(const char *to_wxid, const char *content,
                                    int64_t source_msg_id,
                                    const char *source_sender_wxid) {
    return send_forward_any_native(to_wxid, content, 1, 0, 0, source_msg_id, 0,
                                   wxb_self_wxid(), to_wxid, source_sender_wxid, "",
                                   NULL, NULL, 0);
}

int send_forward_any_native(const char *to_wxid, const char *content,
                                   int source_type, int source_subtype,
                                   int source_local_id, int64_t source_msg_id,
                                   int32_t source_time,
                                   const char *source_from_wxid,
                                   const char *source_to_wxid,
                                   const char *source_sender_wxid,
                                   const char *source_msgsource,
                                   int *out_variant,
                                   int *out_queue_status,
                                   int probe_only) {
    int variant = forward_variant_from_type(source_type, source_subtype);
    if (out_variant) *out_variant = variant;
    if (out_queue_status) *out_queue_status = 0;
    if (source_type == 47) {
        if (out_queue_status) *out_queue_status = -15;
        forward_dbg("[forward-native] type=47 requires high-entry retained send_item; low-level fallback rejected");
        return 0;
    }
    if (!wxb_weixin_module() || !to_wxid || !content) {
        if (out_queue_status) *out_queue_status = -10;
        forward_dbg("[forward-native] guard failed: missing base/to/content");
        return 0;
    }

    const SIZE_T arena_size = 0x30000;
    BYTE *arena = (BYTE *)VirtualAlloc(NULL, arena_size, MEM_COMMIT | MEM_RESERVE, PAGE_EXECUTE_READWRITE);
    if (!arena) {
        if (out_queue_status) *out_queue_status = -11;
        return 0;
    }
    ZeroMemory(arena, arena_size);

    int64_t base = (int64_t)wxb_weixin_module();
    int64_t manager = find_forward_manager(base);
    int64_t pair = (int64_t)(arena + 0x0200);
    int64_t src_ctl = (int64_t)(arena + 0x0400);
    int64_t src_msg = (int64_t)(arena + 0x0800);
    int64_t ui_msg = (int64_t)(arena + 0x1000);
    ForwardQueueTask *task = (ForwardQueueTask *)(arena + 0x1800);
    BYTE *asm_addr = arena + 0x1C00;
    BYTE *pool = arena + 0x4000;
    BYTE *pool_end = arena + arena_size;
    const char *effective_self_wxid = (source_to_wxid && source_to_wxid[0]) ? source_to_wxid : wxb_self_wxid();
    const char *patch_self_wxid = wxb_self_wxid()[0] ? wxb_self_wxid() : effective_self_wxid;
    int ok = 0;

    __try {
        char dbg[1024];
        if (!manager) {
            if (out_queue_status) *out_queue_status = -3;
            forward_dbg("[forward-native] manager not found");
            __leave;
        }
        forward_build_submit_msg(base, src_msg, to_wxid, content,
                                 source_type, source_subtype,
                                 source_local_id, source_msg_id, source_time,
                                 source_from_wxid, source_to_wxid, source_sender_wxid,
                                 source_msgsource, 0, &pool, pool_end);
        forward_build_submit_msg(base, ui_msg, to_wxid, content,
                                 source_type, source_subtype,
                                 source_local_id, source_msg_id, source_time,
                                 source_from_wxid, source_to_wxid, source_sender_wxid,
                                 source_msgsource, 1, &pool, pool_end);

        /* SUBMIT/DISPATCH path needs a COMPLETE model: a hand-built model yields a NOT-ready
         * task (ready=0) that cannot be dispatched. If we retained the REAL source model at
         * receive, copy it, rewrite from=self/to=target/sender=self, and use THAT as the
         * outgoing model so build_task produces a ready task. Falls back to ui_msg otherwise. */
        int64_t submit_model = ui_msg;
        {
            void *retained_model = find_retained_model(source_msg_id);
            if (retained_model) {
                int64_t fwd_model = (int64_t)(arena + 0x2000);
                __try {
                    const char *self_w = wxb_self_wxid()[0] ? wxb_self_wxid() : effective_self_wxid;
                    char template_from[160] = {0};
                    char template_to[560] = {0};
                    char template_sender[160] = {0};
                    char template_content[12288] = {0};
                    char template_msgsource[4096] = {0};
                    ZeroMemory((void *)fwd_model, MODEL_ITEM_BYTES);
                    ((FnCall3)(base + WX_OFF_MODEL_COPY))(fwd_model, (int64_t)retained_model);
                    if (r64((void *)fwd_model) == base + WX_OFF_SRC_MSG_VT) {
                        /* Keep the rich media/appmsg sub-objects from the real retained source
                         * model, but overwrite the message-shell fields from the already-built
                         * target-state template so build_task sees the same outward-facing model
                         * shape as a real UI forward submit object. */
                        wx_read_sso_str(ui_msg + 0x18, template_from, sizeof(template_from));
                        wx_read_sso_str(ui_msg + 0x38, template_to, sizeof(template_to));
                        wx_read_sso_str(ui_msg + 0x58, template_sender, sizeof(template_sender));
                        wx_read_sso_str(ui_msg + 0x160, template_content, sizeof(template_content));
                        wx_read_sso_str(ui_msg + 0x180, template_msgsource, sizeof(template_msgsource));

                        w64((void *)(fwd_model + 0x8), r64((void *)(ui_msg + 0x8)));
                        w64((void *)(fwd_model + 0x10), r64((void *)(ui_msg + 0x10)));
                        wx_set_std_string_arena(
                            fwd_model + 0x18,
                            template_from[0] ? template_from : self_w,
                            lstrlenA(template_from[0] ? template_from : self_w),
                            &pool,
                            pool_end);
                        wx_set_std_string_arena(
                            fwd_model + 0x38,
                            template_to[0] ? template_to : to_wxid,
                            lstrlenA(template_to[0] ? template_to : to_wxid),
                            &pool,
                            pool_end);
                        wx_set_std_string_arena(
                            fwd_model + 0x58,
                            template_sender[0] ? template_sender : self_w,
                            lstrlenA(template_sender[0] ? template_sender : self_w),
                            &pool,
                            pool_end);
                        if (template_content[0]) {
                            wx_set_std_string_arena(fwd_model + 0x160, template_content, lstrlenA(template_content), &pool, pool_end);
                        }
                        if (template_msgsource[0]) {
                            wx_set_std_string_arena(fwd_model + 0x180, template_msgsource, lstrlenA(template_msgsource), &pool, pool_end);
                        }
                        w32((void *)(fwd_model + 0x104), r32((void *)(ui_msg + 0x104)));
                        w64((void *)(fwd_model + 0x108), r64((void *)(ui_msg + 0x108)));
                        w64((void *)(fwd_model + 0x110), r64((void *)(ui_msg + 0x110)));
                        w32((void *)(fwd_model + 0x118), r32((void *)(ui_msg + 0x118)));
                        w32((void *)(fwd_model + 0x120), r32((void *)(ui_msg + 0x120)));
                        w32((void *)(fwd_model + 0x124), r32((void *)(ui_msg + 0x124)));
                        w32((void *)(fwd_model + 0x128), r32((void *)(ui_msg + 0x128)));
                        w32((void *)(fwd_model + 0x12C), r32((void *)(ui_msg + 0x12C)));
                        w8((void *)(fwd_model + 0x138), (BYTE)r32((void *)(ui_msg + 0x138)));
                        submit_model = fwd_model;
                        forward_dbg("[forward-submit] using RETAINED real model (copied via 1801CA4C0; shell fields rewritten from ui_msg)");
                    } else {
                        forward_dbg("[forward-submit] retained-model copy bad vtable; fall back to hand-built");
                    }
                }
                __except (EXCEPTION_EXECUTE_HANDLER) {
                    submit_model = ui_msg;
                    forward_dbg("[forward-submit] retained-model copy EXCEPTION; fall back to hand-built");
                }
            } else {
                forward_dbg("[forward-submit] no retained model for source_msg_id (hand-built model -> likely ready=0)");
            }
        }
        w64((void *)src_ctl, base + WX_OFF_SRC_MSG_CTL_VT);
        w32((void *)(src_ctl + 0x8), 0x100000);
        w32((void *)(src_ctl + 0xC), 0x100000);
        w64((void *)pair, src_msg);
        w64((void *)(pair + 8), src_ctl);
        ZeroMemory(task, sizeof(*task));
        task->status = 0;
        task->variant = variant;
        task->manager = manager;
        task->pair = pair;
        task->ui_msg = submit_model;
        task->patch_template = ui_msg;
        task->patch_payload_target = 1;
        task->probe_only = probe_only;
        task->pre_dispatch_msg = 1;
        safe_copy(task->self_wxid, sizeof(task->self_wxid), patch_self_wxid ? patch_self_wxid : "");
        safe_copy(task->to_wxid, sizeof(task->to_wxid), to_wxid ? to_wxid : "");
        int asm_len = build_forward_queue_asm(asm_addr, (int64_t)task, (int64_t)forward_queue_worker);
        FlushInstructionCache(GetCurrentProcess(), asm_addr, asm_len);
        wsprintfA(dbg,
                  "[forward-native-submit] queue manager=%I64x inline={%I64x,%I64x} pair={%I64x,%I64x} task=%I64x asm=%I64x self=%s to=%s type=%d/%d variant=%d src_local=%d src_id=%I64d src_time=%d fwd_local=%d fwd_id=%I64d fwd_time=%I64x content_len=%d",
                  (unsigned __int64)manager,
                  (unsigned __int64)r64((void *)(manager + 920)),
                  (unsigned __int64)r64((void *)(manager + 928)),
                  (unsigned __int64)r64((void *)pair),
                  (unsigned __int64)r64((void *)(pair + 8)),
                  (unsigned __int64)(int64_t)task,
                  (unsigned __int64)(int64_t)asm_addr,
                  effective_self_wxid, to_wxid, source_type, source_subtype,
                  variant, source_local_id,
                  (long long)source_msg_id, (int)source_time,
                  (int)r32((void *)(src_msg + 0x104)),
                  (long long)r64((void *)(src_msg + 0x108)),
                  (unsigned __int64)r64((void *)(src_msg + 0x110)),
                  lstrlenA(content));
        forward_dbg(dbg);
        queue_send((int64_t)asm_addr);
        for (int i = 0; i < 1000; i++) {
            LONG status = InterlockedCompareExchange(&task->status, 0, 0);
            if (status == 1 || status < 0) break;
            Sleep(10);
        }
        LONG status = InterlockedCompareExchange(&task->status, 0, 0);
        if (status == 0) {
            InterlockedCompareExchange(&task->status, -2, 0);
            status = InterlockedCompareExchange(&task->status, 0, 0);
        }
        wsprintfA(dbg, "[forward-native-submit] queue status=%d", (int)status);
        forward_dbg(dbg);
        if (out_queue_status) *out_queue_status = (int)status;
        ok = (status == 1);
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
        forward_dbg("[forward-native-submit] queue setup EXCEPTION");
        if (out_queue_status) *out_queue_status = -1;
        ok = 0;
    }

    retain_arena(arena);
    return ok;
}

int send_forward_high_native(const char *to_wxid, const char *content,
                                     int source_type, int source_subtype,
                                     int source_local_id, int64_t source_msg_id,
                                     int32_t source_time,
                                     const char *source_from_wxid,
                                     const char *source_to_wxid,
                                     const char *source_sender_wxid,
                                     const char *source_msgsource,
                                     int *out_variant,
                                      int *out_queue_status,
                                      int *out_retained,
                                      int *out_fallback_kind,
                                      int probe_only) {
    int variant = forward_variant_from_type(source_type, source_subtype);
    int effective_subtype = forward_effective_subtype_for_content(source_type, source_subtype, content);
    char marker[384];
    if (out_variant) *out_variant = variant;
    if (out_queue_status) *out_queue_status = 0;
    if (out_retained) *out_retained = 0;
    if (out_fallback_kind) *out_fallback_kind = 0;
    if (!wxb_weixin_module() || !to_wxid || !to_wxid[0]) {
        if (out_queue_status) *out_queue_status = -10;
        forward_dbg("[forward-high-submit] guard failed: missing base/to");
        return 0;
    }
    if (!source_msg_id) {
        if (out_queue_status) *out_queue_status = -12;
        forward_dbg("[forward-high-submit] guard failed: missing source_msg_id");
        return 0;
    }

    const SIZE_T arena_size = 0x30000;
    BYTE *arena = (BYTE *)VirtualAlloc(NULL, arena_size, MEM_COMMIT | MEM_RESERVE, PAGE_EXECUTE_READWRITE);
    if (!arena) {
        if (out_queue_status) *out_queue_status = -11;
        return 0;
    }
    ZeroMemory(arena, arena_size);

    int64_t base = (int64_t)wxb_weixin_module();
    int64_t controller = find_forward_controller(base);
    int64_t high_pair = (int64_t)(arena + 0x0200);
    int64_t high_ctl = (int64_t)(arena + 0x05F0);
    int64_t high_obj = (int64_t)(arena + 0x0600);
    int64_t high_out = (int64_t)(arena + 0x1000);
    ForwardQueueTask *task = (ForwardQueueTask *)(arena + 0x1800);
    BYTE *asm_addr = arena + 0x1C00;
    int64_t high_src_ctl = (int64_t)(arena + 0x2400);
    int64_t high_src_msg = (int64_t)(arena + 0x2800);
    BYTE *pool = arena + 0x4000;
    BYTE *pool_end = arena + arena_size;
    const char *effective_self_wxid = wxb_self_wxid()[0] ? wxb_self_wxid() : source_to_wxid;
    int ok = 0;

    __try {
        char dbg[1536];
        if (!controller) {
            if (out_queue_status) *out_queue_status = -3;
            forward_dbg("[forward-high-submit] controller not found");
            __leave;
        }
        if (!effective_self_wxid || !effective_self_wxid[0]) effective_self_wxid = source_from_wxid;
        if (!effective_self_wxid) effective_self_wxid = "";
        ZeroMemory(marker, sizeof(marker));
        forward_build_source_marker(marker, sizeof(marker), source_msg_id,
                                    source_sender_wxid, source_from_wxid, source_to_wxid);

        w64((void *)high_ctl, base + WX_OFF_FORWARD_HIGH_VARIANT_CTL_VT);
        w32((void *)(high_ctl + 0x8), 0x100000);
        w32((void *)(high_ctl + 0xC), 0x100000);

        w64((void *)high_obj, base + WX_OFF_FORWARD_HIGH_VARIANT_VT);
        w64((void *)(high_obj + 0x8), high_obj);
        w64((void *)(high_obj + 0x10), high_ctl);
        wx_set_std_string_arena(high_obj + 0x38, marker, lstrlenA(marker), &pool, pool_end);
        wx_set_std_string_arena(high_obj + 0xB0, to_wxid, lstrlenA(to_wxid), &pool, pool_end);

        int hydrated_retained = 0;
        void *retained_si = find_retained_si(source_msg_id);
        if (source_type == 47 && retained_si && retained_si_origin(source_msg_id) != 1) {
            /* Synthetic emoji send_items may hold shared refs to their fake source model.
             * Rebuild from persistent XML for each type=47 send unless the item came from
             * the real receive hook in this WeChat process. */
            retained_si = NULL;
        }
        if (!retained_si && source_type == 47 && content && content[0]) {
            if (hydrate_retained_send_item_from_fields(source_msg_id,
                                                       content,
                                                       source_type,
                                                       effective_subtype,
                                                       source_local_id,
                                                       source_time,
                                                       source_from_wxid,
                                                       source_to_wxid,
                                                       source_sender_wxid,
                                                       source_msgsource)) {
                retained_si = find_retained_si(source_msg_id);
                hydrated_retained = retained_si ? 1 : 0;
            }
        }
        if (retained_si) {
            if (out_retained) *out_retained = 1;
            if (out_fallback_kind) *out_fallback_kind = hydrated_retained ? 5 : 1;
            /* REAL path: deep-copy WeChat's own converted send_item into the high_obj.
             * high_obj+0xD8 is raw zeroed arena memory, so the copy-ctor constructs in place.
             * This carries the full media MD5/CDN/store linkage that hand-building cannot. */
            int copy_ok = 0;
            __try {
                ((FnCall3)(base + WX_OFF_SEND_ITEM_COPY))(high_obj + 0xD8, (int64_t)retained_si);
                copy_ok = (r64((void *)(high_obj + 0xD8)) == base + WX_OFF_SEND_ITEM_VT);
            }
            __except (EXCEPTION_EXECUTE_HANDLER) {
                copy_ok = 0;
            }
            if (!copy_ok) {
                if (out_queue_status) *out_queue_status = -14;
                forward_dbg("[forward-high-submit] retained send_item copy failed");
                __leave;
            }
            if (source_type == 49 && effective_subtype != source_subtype) {
                int64_t copied_item = high_obj + 0xD8;
                int old_subtype = r32((void *)(copied_item + 0x0C));
                int old_state = r32((void *)(copied_item + 0xAC));
                int old_media = r32((void *)(copied_item + 0xB4));
                w32((void *)(copied_item + 0x0C), effective_subtype);
                if (appmsg_content_is_file_like(source_type, source_subtype, content)) {
                    w32((void *)(copied_item + 0xAC), 3);
                    w32((void *)(copied_item + 0xB4), 0);
                }
                wsprintfA(dbg,
                          "[forward-high-submit] normalized retained send_item subtype %d->%d state %d->%d media %d->%d",
                          old_subtype,
                          effective_subtype,
                          old_state,
                          r32((void *)(copied_item + 0xAC)),
                          old_media,
                          r32((void *)(copied_item + 0xB4)));
                forward_dbg(dbg);
            }
            if (source_type == 47 && hydrated_retained) {
                int64_t copied_item = high_obj + 0xD8;
                int old_state = r32((void *)(copied_item + 0xAC));
                int64_t src_a_obj = r64((void *)(copied_item + 0x270));
                int64_t src_a_ctl = r64((void *)(copied_item + 0x278));
                int64_t src_b_obj = r64((void *)(copied_item + 0x290));
                int64_t src_b_ctl = r64((void *)(copied_item + 0x298));
                if (!src_a_obj && src_b_obj) w64((void *)(copied_item + 0x270), src_b_obj);
                if (!src_a_ctl && src_b_ctl) w64((void *)(copied_item + 0x278), src_b_ctl);
                if (old_state == 2) w32((void *)(copied_item + 0xAC), 3);
                wsprintfA(dbg,
                          "[forward-high-submit] normalized hydrated emoji state %d->%d srcA {%I64x,%I64x}->{%I64x,%I64x} srcB={%I64x,%I64x}",
                          old_state,
                          r32((void *)(copied_item + 0xAC)),
                          (unsigned __int64)src_a_obj,
                          (unsigned __int64)src_a_ctl,
                          (unsigned __int64)r64((void *)(copied_item + 0x270)),
                          (unsigned __int64)r64((void *)(copied_item + 0x278)),
                          (unsigned __int64)src_b_obj,
                          (unsigned __int64)src_b_ctl);
                forward_dbg(dbg);
            }
            forward_dbg(hydrated_retained
                        ? "[forward-high-submit] using HYDRATED retained send_item from persistent XML (copied via 1801AD0E0)"
                        : "[forward-high-submit] using RETAINED real send_item (copied via 1801AD0E0)");
        } else {
            if (out_retained) *out_retained = 0;
            if (out_fallback_kind) *out_fallback_kind = 2;
            if (source_type == 47) {
                if (out_queue_status) *out_queue_status = -15;
                if (out_fallback_kind) *out_fallback_kind = 3;
                forward_dbg("[forward-high-submit] type=47 requires RETAINED real send_item; HAND-BUILT fallback rejected");
                __leave;
            }
            /* Fallback (message predates injection / not retained): hand-build.
             * Works for text; media will be incomplete. */
            forward_build_fake_request_msg(base, high_src_ctl, high_src_msg,
                                            source_from_wxid, source_to_wxid, source_sender_wxid,
                                            content ? content : "", source_msgsource,
                                            source_type, effective_subtype, source_local_id,
                                            source_msg_id, source_time, &pool, pool_end);
            if (!forward_fill_high_send_item(base, high_obj + 0xD8,
                                              content ? content : "",
                                             source_type, source_subtype,
                                             source_local_id, source_msg_id, source_time,
                                             source_from_wxid, source_to_wxid, source_sender_wxid,
                                             source_msgsource,
                                             high_src_msg, high_src_ctl,
                                             &pool, pool_end)) {
                if (out_queue_status) *out_queue_status = -13;
                forward_dbg("[forward-high-submit] send_item ctor/fill failed");
                __leave;
            }
            forward_dbg("[forward-high-submit] using HAND-BUILT send_item (no retained item)");
        }
        w64((void *)high_pair, high_obj);
        w64((void *)(high_pair + 8), high_ctl);

        ZeroMemory(task, sizeof(*task));
        task->status = 0;
        task->mode = 1;
        task->variant = variant;
        task->controller = controller;
        task->high_pair = high_pair;
        task->high_variant = high_obj;
        task->high_out = high_out;
        task->patch_template = 0;
        task->patch_payload_target =
            (probe_only || appmsg_content_is_file_like(source_type, source_subtype, content)) ? 1 : 0;
        task->probe_only = probe_only;
        task->require_dispatch_confirm = (source_type == 47) ? 1 : 0;
        task->pre_dispatch_msg = 0;
        safe_copy(task->self_wxid, sizeof(task->self_wxid), effective_self_wxid ? effective_self_wxid : "");
        safe_copy(task->to_wxid, sizeof(task->to_wxid), to_wxid ? to_wxid : "");

        int asm_len = build_forward_queue_asm(asm_addr, (int64_t)task, (int64_t)forward_queue_worker);
        FlushInstructionCache(GetCurrentProcess(), asm_addr, asm_len);
        wsprintfA(dbg,
                  "[forward-high-submit] queue controller=%I64x holder=%I64x high_pair={%I64x,%I64x} high_obj=%I64x high_ctl=%I64x source={%I64x,%I64x} task=%I64x asm=%I64x self=%s to=%s marker=%s type=%d/%d variant=%d src_local=%d src_id=%I64d src_time=%d content_len=%d probe=%d",
                  (unsigned __int64)controller,
                  (unsigned __int64)r64((void *)(controller + 0xB78)),
                  (unsigned __int64)r64((void *)high_pair),
                  (unsigned __int64)r64((void *)(high_pair + 8)),
                  (unsigned __int64)high_obj,
                  (unsigned __int64)high_ctl,
                  (unsigned __int64)high_src_msg,
                  (unsigned __int64)high_src_ctl,
                  (unsigned __int64)(int64_t)task,
                  (unsigned __int64)(int64_t)asm_addr,
                  effective_self_wxid, to_wxid, marker,
                  source_type, source_subtype, variant, source_local_id,
                  (long long)source_msg_id, (int)source_time,
                  content ? lstrlenA(content) : 0,
                  probe_only);
        forward_dbg(dbg);
        queue_send((int64_t)asm_addr);
        for (int i = 0; i < 1000; i++) {
            LONG status = InterlockedCompareExchange(&task->status, 0, 0);
            if (status == 1 || status < 0) break;
            Sleep(10);
        }
        LONG status = InterlockedCompareExchange(&task->status, 0, 0);
        if (status == 0) {
            InterlockedCompareExchange(&task->status, -2, 0);
            status = InterlockedCompareExchange(&task->status, 0, 0);
        }
        wsprintfA(dbg, "[forward-high-submit] queue status=%d", (int)status);
        forward_dbg(dbg);
        if (out_queue_status) *out_queue_status = (int)status;
        ok = (status == 1);
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {
        forward_dbg("[forward-high-submit] queue setup EXCEPTION");
        if (out_queue_status) *out_queue_status = -1;
        ok = 0;
    }

    retain_arena(arena);
    return ok;
}


