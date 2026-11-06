import { expect, it } from "vitest";
import { previewCall, previewOverview } from "./delivery-preview";

it("formats real call data using Beijing time and the server message format", () => {
  expect(previewCall({ staff_name: "Fixture", order_id: 42, customer_phone_masked: "***1234", call_time: "2026-09-20T01:02:03Z", is_connected: true, call_duration: 12, recording_available: true })).toBe([
    "【配送联系通知】", "配送员：Fixture", "意向单：#42（***1234）", "拨号时间：09-20 09:02:03", "通话结果：已接通，12 秒", "录音：已到达", "依据 EC 通话记录及当前分配关系；不代表已完成配送。",
  ].join("\n"));
});

it("formats daily snapshot without inventing contacts", () => {
  const text = previewOverview("2026-09-20", { snapshot_at: "2026-09-20T12:00:00+08:00", items: [{ staff_name: "Fixture", first_call_at: null, call_count: 0, dialed_customer_count: 0, connected_customer_count: 0, order_customer_count: 0, connected_order_customer_count: 0 }] });
  expect(text).toContain("Fixture｜首拨 暂无");
  expect(text).toContain("拨号 0 次｜拨打 0 人｜接通 0 人");
  expect(text).toContain("快照：2026-09-20T12:00:00+08:00");
});
