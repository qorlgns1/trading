import { describe, expect, it } from "vitest";

import { rebalanceBasisPointWeights } from "./replay-strategy";

describe("replay strategy weights", () => {
  it("keeps the selected weight and an exact 100 percent total", () => {
    const base = {
      long_term_trend: 3000,
      absolute_momentum: 2500,
      relative_strength: 2000,
      high_proximity: 1000,
      volatility_stability: 1000,
      trading_activity: 500,
    };

    for (let selected = 0; selected <= 10000; selected += 500) {
      const result = rebalanceBasisPointWeights(
        base,
        "long_term_trend",
        selected,
      );
      expect(result.long_term_trend).toBe(selected);
      expect(Object.values(result).reduce((sum, value) => sum + value, 0)).toBe(
        10000,
      );
      expect(Object.values(result).every((value) => value >= 0)).toBe(true);
    }
  });
});
