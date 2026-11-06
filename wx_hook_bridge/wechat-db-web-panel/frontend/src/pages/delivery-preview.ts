export type PreviewCall = { id?: number; staff_name: string; call_time: string; call_duration: number; is_connected: boolean; customer_phone_masked: string; order_id: number | null; recording_available: boolean };
export type PreviewOverview = { snapshot_at: string; items: { staff_name: string; first_call_at: string | null; call_count: number; dialed_customer_count: number; connected_customer_count: number; order_customer_count: number; connected_order_customer_count: number }[] };

function timeParts(value: string) {
  return new Intl.DateTimeFormat("en-GB", { timeZone: "Asia/Shanghai", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23" }).formatToParts(new Date(value));
}

export function previewCall(call: PreviewCall) {
  const parts = timeParts(call.call_time);
  const part = (key: string) => parts.find(item => item.type === key)?.value;
  return ["【配送联系通知】", `配送员：${call.staff_name}`, `意向单：#${call.order_id}（${call.customer_phone_masked}）`,
    `拨号时间：${part("month")}-${part("day")} ${part("hour")}:${part("minute")}:${part("second")}`,
    `通话结果：${call.is_connected ? `已接通，${call.call_duration} 秒` : "未接通"}`,
    `录音：${call.recording_available ? "已到达" : "暂未到达"}`, "依据 EC 通话记录及当前分配关系；不代表已完成配送。"].join("\n");
}

export function previewOverview(day: string, data: PreviewOverview) {
  const lines = [`【配送跟进统计｜${day}】`, "人数按各配送员号码去重，跨人可能重复。"];
  for (const row of data.items) {
    const first = row.first_call_at ? new Intl.DateTimeFormat("en-GB", { timeZone: "Asia/Shanghai", hour: "2-digit", minute: "2-digit", hourCycle: "h23" }).format(new Date(row.first_call_at)) : "暂无";
    lines.push("", `${row.staff_name}｜首拨 ${first}`, `拨号 ${row.call_count} 次｜拨打 ${row.dialed_customer_count} 人｜接通 ${row.connected_customer_count} 人`,
      `意向单客户 ${row.order_customer_count} 人｜接通 ${row.connected_order_customer_count} 人`);
  }
  lines.push("", "仅统计实际 EC 通话；零记录不代表同步正常。", `快照：${data.snapshot_at}`);
  return lines.join("\n");
}

export const CALL_EXAMPLE: PreviewCall = { staff_name: "示例配送员", call_time: "2026-01-01T10:23:00+08:00", call_duration: 136, is_connected: true, customer_phone_masked: "***1234", order_id: 1001, recording_available: true };
