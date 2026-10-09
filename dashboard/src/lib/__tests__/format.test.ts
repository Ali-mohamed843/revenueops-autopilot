import { describe, expect, it } from "vitest";

import { niceTicks } from "@/lib/scale";
import { money, moneyCompact, paramsText, percent, relativeTime } from "@/lib/format";

describe("niceTicks", () => {
  it("steps by 1, 2, 2.5 or 5 x 10^n and covers the maximum", () => {
    expect(niceTicks(3)).toEqual([0, 1, 2, 3]);
    expect(niceTicks(2_216_686)).toEqual([0, 1_000_000, 2_000_000, 3_000_000]);
    expect(niceTicks(141)).toEqual([0, 50, 100, 150]);
    expect(niceTicks(9)).toEqual([0, 2.5, 5, 7.5, 10]);
  });
  it("has a usable axis when everything is zero", () => {
    expect(niceTicks(0)).toEqual([0, 1]);
  });
});

describe("format", () => {
  it("prints money in full and compact", () => {
    expect(money("41084.3")).toBe("41,084.30 EGP");
    expect(moneyCompact("41084.3")).toBe("41.1K EGP");
    expect(moneyCompact(950)).toBe("950 EGP");
    expect(moneyCompact(2_216_686.3, "")).toBe("2.2M ");
  });
  it("prints rates, params and times for people", () => {
    expect(percent(0.6667)).toBe("67%");
    expect(percent(null)).toBe("—");
    expect(paramsText({ channel: "sms", percent: 10, valid_hours: 48, new_eta_days: 3 })).toBe("sms · 10% · 48h · 3 days");
    const now = Date.parse("2026-10-08T12:00:00Z");
    expect(relativeTime("2026-10-08T11:59:30Z", now)).toBe("just now");
    expect(relativeTime("2026-10-08T09:00:00Z", now)).toBe("3 hours ago");
  });
});
