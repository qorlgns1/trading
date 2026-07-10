import { ArrowUpRight } from "lucide-react";
import Link from "next/link";

import { StatusBadge } from "@/components/ui/status-badge";
import type { ScreenerItem } from "@/lib/api";

export const PEER_LABELS: Record<string, string> = {
  US_STOCK: "미국 주식",
  KR_KOSPI: "KOSPI",
  KR_KOSDAQ: "KOSDAQ",
  US_EQUITY_ETF: "미국 ETF",
  KR_DOMESTIC_EQUITY_ETF: "국내 ETF",
  KR_OVERSEAS_EQUITY_ETF: "해외형 ETF",
};

function formatAdv(item: ScreenerItem) {
  if (item.adv60 == null) return "-";
  const currency = ["US_STOCK", "US_EQUITY_ETF"].includes(item.peer_group) ? "USD" : "KRW";
  return new Intl.NumberFormat("ko-KR", {
    style: "currency",
    currency,
    notation: "compact",
    maximumFractionDigits: 1,
  }).format(item.adv60);
}

export function CandidateTable({ items }: { items: ScreenerItem[] }) {
  if (items.length === 0) {
    return <div className="empty-state">현재 조건에 맞는 후보가 없습니다.</div>;
  }
  return (
    <div className="data-table-wrap">
      <table className="data-table">
        <thead>
          <tr>
            <th>종목</th>
            <th>비교군</th>
            <th>점수</th>
            <th>후보 상태</th>
            <th>상대 순위</th>
            <th>60일 평균 거래대금</th>
            <th>데이터</th>
            <th aria-label="상세" />
          </tr>
        </thead>
        <tbody>
          {items.map((item) => (
            <tr key={item.asset_id}>
              <td className="symbol-cell">
                <strong>{item.symbol}</strong>
                <span>{item.name}</span>
              </td>
              <td>{PEER_LABELS[item.peer_group] ?? item.peer_group}</td>
              <td><span className="score-number">{item.score?.toFixed(1) ?? "-"}</span></td>
              <td><StatusBadge state={item.state} /></td>
              <td>{item.percentile == null ? "-" : `상위 ${item.percentile.toFixed(0)}%`}</td>
              <td>{formatAdv(item)}</td>
              <td title={item.data_status_reason ?? undefined}>
                <StatusBadge state={item.data_status} />
              </td>
              <td>
                <Link
                  href={`/assets/${encodeURIComponent(item.asset_id)}`}
                  className="icon-button"
                  aria-label={`${item.symbol} 상세 보기`}
                  title="상세 보기"
                >
                  <ArrowUpRight size={16} />
                </Link>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
