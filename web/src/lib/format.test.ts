import { describe, expect, it } from "vitest";

import { formatBytes, formatDateTime, formatDuration, formatPercent, formatRelative, shortSha } from "./format";

describe("formatDuration", () => {
  it.each([
    [0.042, "42 ms"],
    [3.21, "3.2 s"],
    [42.4, "42 s"],
    [245, "4m 05s"],
    [3720, "1h 02m"],
    [null, "—"],
  ])("%s → %s", (seconds, expected) => expect(formatDuration(seconds)).toBe(expected));
});

describe("formatBytes", () => {
  it.each([
    [512, "512 B"],
    [1536, "1.5 KB"],
    [5 * 1024 * 1024, "5.0 MB"],
    [300 * 1024 * 1024, "300 MB"],
    [undefined, "—"],
  ])("%s → %s", (bytes, expected) => expect(formatBytes(bytes)).toBe(expected));
});

describe("dates", () => {
  const now = new Date("2026-10-19T12:00:00Z");

  it("formats relative times", () => {
    expect(formatRelative("2026-10-19T11:55:00Z", now)).toBe("5 minutes ago");
    expect(formatRelative("2026-10-19T11:59:58Z", now)).toBe("2 seconds ago");
    expect(formatRelative("2026-10-18T12:00:00Z", now)).toBe("yesterday");
    expect(formatRelative(null, now)).toBe("—");
  });

  it("formats local date and time", () => {
    expect(formatDateTime("2026-10-19T12:00:00Z")).toContain("2026");
    expect(formatDateTime("not a date")).toBe("—");
  });
});

describe("shortSha and formatPercent", () => {
  it("shortens", () => {
    expect(shortSha("0123456789abcdef")).toBe("0123456");
    expect(shortSha("0123456789abcdef", 12)).toBe("0123456789ab");
    expect(shortSha(null)).toBe("—");
  });

  it("formats ratios", () => {
    expect(formatPercent(0.1234)).toBe("12%");
    expect(formatPercent(0.1234, 1)).toBe("12.3%");
    expect(formatPercent(undefined)).toBe("—");
  });
});
