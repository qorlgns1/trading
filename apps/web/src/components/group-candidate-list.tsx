import { ArrowUpRight } from "lucide-react";
import Link from "next/link";

import { StatusBadge } from "@/components/ui/status-badge";
import type { ScreenerItem } from "@/lib/api";

export function GroupCandidateList({ items }: { items: ScreenerItem[] }) {
  if (items.length === 0) {
    return <div className="group-empty">현재 공식 후보가 없습니다.</div>;
  }
  return (
    <ol className="group-candidate-list">
      {items.map((item) => (
        <li key={item.asset_id}>
          <span className="group-rank" aria-hidden="true">
            {items.indexOf(item) + 1}
          </span>
          <span className="group-symbol">
            <strong>{item.symbol}</strong>
            <small>{item.name}</small>
          </span>
          <span className="group-score">{item.score?.toFixed(1) ?? "-"}</span>
          <StatusBadge state={item.state} />
          <Link
            href={`/assets/${encodeURIComponent(item.asset_id)}`}
            className="icon-button icon-button-small"
            aria-label={`${item.symbol} 상세 보기`}
            title="상세 보기"
          >
            <ArrowUpRight size={15} />
          </Link>
        </li>
      ))}
    </ol>
  );
}
