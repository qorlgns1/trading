export function DataModeBadge({ source }: { source: string }) {
  const real = source === "YFINANCE";
  return (
    <span className={`demo-chip ${real ? "real-data-chip" : ""}`}>
      {real ? "실제 데이터 · 로컬 연구" : "가상 데이터"}
    </span>
  );
}
