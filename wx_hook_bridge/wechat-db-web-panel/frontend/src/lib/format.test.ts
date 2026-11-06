import { describe, expect, it } from "vitest";

import {
  formatCompact,
  formatDuration,
  formatNumber,
  localDateKey,
  profileName,
  safeText,
} from "./format";

describe("format helpers", () => {
  it("formats finite numbers and rejects missing values", () => {
    expect(formatNumber(48092)).toBe("48,092");
    expect(formatNumber(Number.NaN)).toBe("—");
    expect(formatNumber("48092")).toBe("—");
  });

  it("uses compact notation only for large values", () => {
    expect(formatCompact(9999)).toBe("9,999");
    expect(formatCompact(10000)).not.toBe("10,000");
  });

  it("formats duration boundaries deterministically", () => {
    expect(formatDuration(59)).toBe("59 秒");
    expect(formatDuration(60)).toBe("1 分");
    expect(formatDuration(3660)).toBe("1 小时 1 分");
    expect(formatDuration(86400)).toBe("1 天");
  });

  it("preserves zero and applies stable profile fallbacks", () => {
    expect(safeText(0)).toBe("0");
    expect(safeText("   ", "空值")).toBe("空值");
    expect(profileName({ remark: "备注名", nickname: "昵称" })).toBe("备注名");
    expect(profileName({ username: "wxid_local" })).toBe("wxid_local");
  });

  it("builds local date keys without UTC conversion", () => {
    expect(localDateKey(new Date(2026, 7, 17, 23, 59))).toBe("2026-08-17");
  });
});
