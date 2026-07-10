export type AllocationKey = "us_stock" | "kr_stock" | "us_etf" | "kr_etf";

export type Allocations = Record<AllocationKey, number>;

export const DEFAULT_ALLOCATIONS: Allocations = {
  us_stock: 25,
  kr_stock: 25,
  us_etf: 25,
  kr_etf: 25,
};

const KEYS: AllocationKey[] = ["us_stock", "kr_stock", "us_etf", "kr_etf"];

export function updateAllocation(
  current: Allocations,
  changedKey: AllocationKey,
  nextValue: number,
): Allocations {
  const value = Math.max(0, Math.min(100, Math.round(nextValue / 5) * 5));
  const result = { ...current, [changedKey]: value };
  let delta = value - current[changedKey];
  const others = KEYS.filter((key) => key !== changedKey).sort(
    (left, right) => current[right] - current[left],
  );

  for (const key of others) {
    if (delta === 0) break;
    if (delta > 0) {
      const reduction = Math.min(result[key], delta);
      result[key] -= reduction;
      delta -= reduction;
    } else {
      const addition = Math.min(100 - result[key], -delta);
      result[key] += addition;
      delta += addition;
    }
  }
  return result;
}

export function toBasisPoints(allocations: Allocations): Record<AllocationKey, number> {
  return Object.fromEntries(
    Object.entries(allocations).map(([key, value]) => [key, value * 100]),
  ) as Record<AllocationKey, number>;
}
