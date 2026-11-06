import { describe, expect, it } from "vitest";
import { parseRealtimeEvent, queryScopesForRealtimeEvent, snapshotTimeFromRealtimeEvent } from "./realtime";

describe("realtime event helpers", () => {
  it("parses the SSE JSON contract without inventing payload data", () => {
    const event = parseRealtimeEvent(JSON.stringify({
      event_id: 42,
      event_type: "message",
      payload: { snapshot_time: "2026-08-18T00:00:00+08:00", message_id: "real-id" },
    }));

    expect(event).toEqual({
      event_id: "42",
      event_type: "message",
      payload: { snapshot_time: "2026-08-18T00:00:00+08:00", message_id: "real-id" },
    });
    expect(snapshotTimeFromRealtimeEvent(event!)).toBe("2026-08-18T00:00:00+08:00");
  });

  it("rejects malformed events and maps only known event types", () => {
    expect(parseRealtimeEvent("not-json")).toBeUndefined();
    expect(parseRealtimeEvent(JSON.stringify({ event_type: "message" }))).toBeUndefined();
    expect(queryScopesForRealtimeEvent("resource")).toContain("media");
    expect(queryScopesForRealtimeEvent("message.received")).toContain("overview");
    expect(queryScopesForRealtimeEvent("message.recalled")).toContain("chat-list");
    expect(queryScopesForRealtimeEvent("bridge.raw")).toContain("search");
    expect(queryScopesForRealtimeEvent("database_updated")).toContain("realtime-status");
    expect(queryScopesForRealtimeEvent("sns.updated")).toEqual([
      "sns", "sns-stats", "sns-detail", "sns-sync-status",
    ]);
    expect(queryScopesForRealtimeEvent("realtime.reset")).toContain("conversations");
    expect(queryScopesForRealtimeEvent("reset")).toContain("chat-list");
    expect(queryScopesForRealtimeEvent("unknown")).toEqual([]);
  });
});
