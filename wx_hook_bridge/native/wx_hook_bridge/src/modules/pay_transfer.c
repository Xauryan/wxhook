#include "../include/wx_bridge_pay_transfer.h"
#include "../include/wx_bridge_memory.h"
#include "../include/wx_bridge_native_call.h"
#include "../include/wx_bridge_offsets.h"
#include "../include/wx_bridge_protocol.h"
#include "../include/wx_bridge_runtime.h"
#include "../include/wx_bridge_text.h"
#include "../include/wx_bridge_wechat_string.h"
#include <string.h>

typedef int64_t (__fastcall *FnPayTaskRun)(void *outer_task, void *callback_ctx);
typedef int64_t (__fastcall *FnWxAlloc)(int64_t size);

static void pay_result_message(char *message,
                               int message_max,
                               const char *command,
                               int ok,
                               int32_t err_code,
                               const char *detail) {
    char code_s[32];
    char *p;
    char *end;
    if (!message || message_max <= 0) return;
    p = message;
    end = message + message_max - 1;
    append_raw(&p, end, command ? command : "pay_transfer");
    append_raw(&p, end, ok ? " submitted" : " failed");
    append_raw(&p, end, " err=");
    wsprintfA(code_s, "%d", (int)err_code);
    append_raw(&p, end, code_s);
    if (detail && detail[0]) {
        append_raw(&p, end, " detail=");
        append_raw(&p, end, detail);
    }
    *p = 0;
}

static int accept_transfer_native(const char *trans_id,
                                  const char *transfer_attach,
                                  int32_t invalid_time,
                                  const char *group_username,
                                  char *message,
                                  int message_max) {
    int64_t base = wxb_weixin_base();
    BYTE outer_task[64];
    BYTE callback_ctx[232];
    BYTE *fake_pay = NULL;
    int64_t task_ret = 0;
    int ok = 0;
    int32_t err_code = -1;
    int stage = 0;
    char detail[512] = {0};

    if (message && message_max > 0) message[0] = 0;
    if (!base) {
        safe_copy(message, message_max, "Weixin.dll not loaded");
        return 0;
    }
    if (!trans_id || !trans_id[0]) {
        safe_copy(message, message_max, "accept_transfer missing transferid/trans_id");
        return 0;
    }

    ZeroMemory(outer_task, sizeof(outer_task));
    ZeroMemory(callback_ctx, sizeof(callback_ctx));

    __try {
        FnWxAlloc wx_alloc = (FnWxAlloc)(base + WX_OFF_WX_ALLOC);
        FnPayTaskRun run_accept_task =
            (FnPayTaskRun)(base + WX_OFF_PAY_ACCEPT_TASK_RUN);

        stage = 1;
        fake_pay = (BYTE *)wx_alloc(0x300);
        if (!fake_pay) {
            err_code = -2;
            safe_copy(detail, sizeof(detail), "WeChat heap alloc pay object failed");
            __leave;
        }
        ZeroMemory(fake_pay, 0x300);

        /*
         * Real accept builder mode=2 reads only these pay object fields:
         * 0x210 transferid -> request trans_id
         * 0x158 invalidtime -> request invalid_time
         * 0x120 attach -> request transfer_attach; may be empty in real XML.
         */
        stage = 2;
        wx_set_std_string_heap(base, (int64_t)fake_pay + 0x210, trans_id);
        wx_set_std_string_heap(base, (int64_t)fake_pay + 0x120,
                               transfer_attach ? transfer_attach : "");
        w32((void *)((int64_t)fake_pay + 0x158), invalid_time);

        stage = 3;
        wx_set_std_string_heap(base, (int64_t)outer_task + 24,
                               group_username ? group_username : "");
        w64((void *)(outer_task + 8), (int64_t)fake_pay);
        w64((void *)(outer_task + 16), 0);
        w64((void *)(outer_task + 56), 0);

        stage = 4;
        task_ret = run_accept_task(outer_task, callback_ctx);
        err_code = 0;
        ok = 1;
        wsprintfA(detail,
                  "scheduled native CheckAndAcceptPayTransfer ret=0x%I64x attach_len=%d",
                  (unsigned __int64)task_ret,
                  transfer_attach ? lstrlenA(transfer_attach) : 0);
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        char stage_s[64];
        err_code = -3;
        wsprintfA(stage_s, "exception in accept_transfer_native stage=%d", stage);
        safe_copy(detail, sizeof(detail), stage_s);
        ok = 0;
    }

    /*
     * fake_pay and its WeChat-heap string buffers are intentionally retained.
     * sub_184A59D00 schedules async work; freeing here would race the native
     * CheckAndAcceptPayTransfer task. Cleanup needs a real native callback.
     */
    wx_free_std_string_heap(base, (int64_t)outer_task + 24);

    pay_result_message(message, message_max, "accept_transfer", ok, err_code, detail);
    return ok;
}

static int refuse_transfer_native(const char *trans_id,
                                  int32_t invalid_time,
                                  const char *group_username,
                                  char *message,
                                  int message_max) {
    int64_t base = wxb_weixin_base();
    BYTE outer_task[96];
    BYTE callback_ctx[232];
    BYTE *fake_pay = NULL;
    int64_t task_ret = 0;
    int ok = 0;
    int32_t err_code = -1;
    int stage = 0;
    char detail[512] = {0};

    if (message && message_max > 0) message[0] = 0;
    if (!base) {
        safe_copy(message, message_max, "Weixin.dll not loaded");
        return 0;
    }
    if (!trans_id || !trans_id[0]) {
        safe_copy(message, message_max, "refuse_transfer missing transferid/trans_id");
        return 0;
    }

    ZeroMemory(outer_task, sizeof(outer_task));
    ZeroMemory(callback_ctx, sizeof(callback_ctx));

    __try {
        FnWxAlloc wx_alloc = (FnWxAlloc)(base + WX_OFF_WX_ALLOC);
        FnPayTaskRun run_refuse_task =
            (FnPayTaskRun)(base + WX_OFF_PAY_REFUSE_TASK_RUN);

        stage = 1;
        fake_pay = (BYTE *)wx_alloc(0x300);
        if (!fake_pay) {
            err_code = -2;
            safe_copy(detail, sizeof(detail), "WeChat heap alloc pay object failed");
            __leave;
        }
        ZeroMemory(fake_pay, 0x300);

        /*
         * Real refuse builder mode=3 reads pay+0x210 transferid and
         * pay+0x158 invalidtime. IDA evidence shows no transfer_attach read
         * in this mode, so do not fill pay+0x120 from a lookalike field.
         */
        stage = 2;
        wx_set_std_string_heap(base, (int64_t)fake_pay + 0x210, trans_id);
        w32((void *)((int64_t)fake_pay + 0x158), invalid_time);

        /*
         * sub_184A56180 outer layout:
         * +0x10/+0x18 pay shared_ptr, +0x20 std::string group/context.
         */
        stage = 3;
        w64((void *)(outer_task + 8), 0);
        w64((void *)(outer_task + 16), (int64_t)fake_pay);
        w64((void *)(outer_task + 24), 0);
        wx_set_std_string_heap(base, (int64_t)outer_task + 32,
                               group_username ? group_username : "");

        stage = 4;
        task_ret = run_refuse_task(outer_task, callback_ctx);
        err_code = 0;
        ok = 1;
        wsprintfA(detail,
                  "scheduled native CoRefusePayTransfer ret=0x%I64x task_run=sub_184A56180 build_req=sub_184A49080(mode=3)",
                  (unsigned __int64)task_ret);
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        char stage_s[64];
        err_code = -3;
        wsprintfA(stage_s, "exception in refuse_transfer_native stage=%d", stage);
        safe_copy(detail, sizeof(detail), stage_s);
        ok = 0;
    }

    /*
     * fake_pay and its WeChat-heap string buffer are intentionally retained.
     * sub_184A56180 schedules async work; freeing here would race the native
     * CoRefusePayTransfer task. Cleanup needs a real native callback.
     */
    wx_free_std_string_heap(base, (int64_t)outer_task + 32);

    pay_result_message(message, message_max, "refuse_transfer", ok, err_code, detail);
    return ok;
}

int accept_transfer_to_result(const char *seq,
                              const char *trans_id,
                              const char *transfer_attach,
                              int32_t invalid_time,
                              const char *group_username) {
    char msg[768];
    int ok;
    wxb_ensure_weixin_module();
    ok = accept_transfer_native(trans_id,
                                transfer_attach,
                                invalid_time,
                                group_username,
                                msg,
                                sizeof(msg));
    write_result(seq,
                 ok,
                 "accept_transfer",
                 msg[0] ? msg : (ok ? "accept_transfer submitted" : "accept_transfer failed"));
    return ok;
}

int refuse_transfer_to_result(const char *seq,
                              const char *trans_id,
                              int32_t invalid_time,
                              const char *group_username) {
    char msg[768];
    int ok;
    wxb_ensure_weixin_module();
    ok = refuse_transfer_native(trans_id,
                                invalid_time,
                                group_username,
                                msg,
                                sizeof(msg));
    write_result(seq,
                 ok,
                 "refuse_transfer",
                 msg[0] ? msg : (ok ? "refuse_transfer submitted" : "refuse_transfer failed"));
    return ok;
}
