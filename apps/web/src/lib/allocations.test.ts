import { describe, expect, it } from "vitest";

import { DEFAULT_ALLOCATIONS, toBasisPoints, updateAllocation } from "./allocations";

describe("updateAllocation", () => {
  it("keeps the four sleeves at exactly 100 percent", () => {
    const result = updateAllocation(DEFAULT_ALLOCATIONS, "us_stock", 45);
    expect(Object.values(result).reduce((sum, value) => sum + value, 0)).toBe(100);
    expect(result.us_stock).toBe(45);
  });

  it("converts percentages to basis points", () => {
    expect(toBasisPoints(DEFAULT_ALLOCATIONS)).toEqual({
      us_stock: 2500,
      kr_stock: 2500,
      us_etf: 2500,
      kr_etf: 2500,
    });
  });
});
