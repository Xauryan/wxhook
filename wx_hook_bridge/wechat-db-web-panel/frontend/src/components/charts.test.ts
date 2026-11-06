import { describe, expect, it } from "vitest";
import { graphNodeIdFromEvent } from "../lib/chartEvents";

describe("graphNodeIdFromEvent", () => {
  it("returns the real node id for an ECharts node click", () => {
    expect(graphNodeIdFromEvent({ dataType: "node", data: { id: "wxid_contact" } })).toBe("wxid_contact");
  });

  it("ignores edge and malformed click events", () => {
    expect(graphNodeIdFromEvent({ dataType: "edge", data: { id: "edge" } })).toBeUndefined();
    expect(graphNodeIdFromEvent({ dataType: "node", data: {} })).toBeUndefined();
  });
});
