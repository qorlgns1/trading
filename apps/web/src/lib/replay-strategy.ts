export function rebalanceBasisPointWeights(
  current: Record<string, number>,
  selectedKey: string,
  selectedValue: number,
) {
  const selected = Math.max(
    0,
    Math.min(10000, Math.round(selectedValue / 500) * 500),
  );
  const others = Object.keys(current).filter((key) => key !== selectedKey);
  const remaining = 10000 - selected;
  const currentTotal = others.reduce((sum, key) => sum + current[key], 0);
  const raw = others.map((key) => ({
    key,
    value:
      currentTotal === 0
        ? remaining / others.length
        : (remaining * current[key]) / currentTotal,
  }));
  const result: Record<string, number> = { [selectedKey]: selected };
  for (const item of raw) result[item.key] = Math.floor(item.value);
  let remainder =
    remaining - raw.reduce((sum, item) => sum + result[item.key], 0);
  for (const item of [...raw].sort((left, right) => {
    const fraction =
      right.value -
      Math.floor(right.value) -
      (left.value - Math.floor(left.value));
    return fraction || left.key.localeCompare(right.key);
  })) {
    if (remainder <= 0) break;
    result[item.key] += 1;
    remainder -= 1;
  }
  return result;
}
