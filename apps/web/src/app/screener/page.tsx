import type { Metadata } from "next";

import { DataModeBadge } from "@/components/data-mode-badge";
import { ResearchSyncPanel } from "@/components/research-sync-panel";
import { ScreenerClient, type ScreenerFilters } from "@/components/screener-client";
import {
  apiFetch,
  type ResearchStatus,
  type ScreenerResponse,
} from "@/lib/api";

export const metadata: Metadata = { title: "추세 스크리너" };

const VALID_GROUPS = new Set([
  "US_STOCK",
  "KR_KOSPI",
  "KR_KOSDAQ",
  "US_EQUITY_ETF",
  "KR_DOMESTIC_EQUITY_ETF",
  "KR_OVERSEAS_EQUITY_ETF",
]);

export default async function ScreenerPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const params = await searchParams;
  const status = await apiFetch<ResearchStatus>("/research/status");
  const unavailable = status.app_mode === "local_research" &&
    ["MISSING", "PREPARING"].includes(status.snapshot_state);

  if (unavailable) {
    return (
      <>
        <header className="page-header">
          <div><h1>추세 스크리너</h1><p>첫 실데이터 스냅샷이 준비되면 전체 종목을 검색할 수 있습니다.</p></div>
          <div className="page-meta"><DataModeBadge source={status.data_source} /></div>
        </header>
        <ResearchSyncPanel initial={status} />
      </>
    );
  }

  const groupParam = typeof params.peer_group === "string" ? params.peer_group : "";
  const filters: ScreenerFilters = {
    group: VALID_GROUPS.has(groupParam) ? groupParam : "",
    state: "",
    minimumScore: 0,
    search: "",
    officialOnly: params.official_only === "true",
  };
  const query = new URLSearchParams({ page: "1", page_size: "25", minimum_score: "0" });
  if (filters.group) query.set("peer_group", filters.group);
  if (filters.officialOnly) query.set("official_only", "true");
  const initial = await apiFetch<ScreenerResponse>(`/screener?${query.toString()}`);
  return <ScreenerClient initial={initial} initialFilters={filters} dataSource={status.data_source} />;
}
