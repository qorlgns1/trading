import { AlertTriangle, CheckCircle2, Database, ListFilter } from "lucide-react";
import Link from "next/link";

import { PEER_LABELS } from "@/components/candidate-table";
import { DataModeBadge } from "@/components/data-mode-badge";
import { GroupCandidateList } from "@/components/group-candidate-list";
import { ResearchSyncPanel } from "@/components/research-sync-panel";
import {
  apiFetch,
  type ResearchStatus,
  type ScreenerResponse,
} from "@/lib/api";

const PEER_GROUPS = [
  "US_STOCK",
  "US_EQUITY_ETF",
  "KR_KOSPI",
  "KR_KOSDAQ",
  "KR_DOMESTIC_EQUITY_ETF",
  "KR_OVERSEAS_EQUITY_ETF",
] as const;

function formatTimestamp(value?: string | null) {
  if (!value) return "아직 없음";
  return new Intl.DateTimeFormat("ko-KR", {
    dateStyle: "medium",
    timeStyle: "short",
    timeZone: "Asia/Seoul",
  }).format(new Date(value));
}

export default async function DashboardPage() {
  const status = await apiFetch<ResearchStatus>("/research/status");
  const isLocal = status.app_mode === "local_research";
  const unavailable = isLocal && ["MISSING", "PREPARING"].includes(status.snapshot_state);

  if (unavailable) {
    return (
      <>
        <header className="page-header">
          <div>
            <h1>실제 추세 후보 준비</h1>
            <p>한국·미국 시장의 종목 목록과 가격 이력을 준비한 뒤 후보를 계산합니다.</p>
          </div>
          <div className="page-meta"><DataModeBadge source={status.data_source} /></div>
        </header>
        <ResearchSyncPanel initial={status} />
        <div className="notice-box" style={{ marginTop: 16 }}>
          추세 점수는 투자 추천이나 미래 상승 확률이 아닙니다.
        </div>
      </>
    );
  }

  const [groupResponses, official] = await Promise.all([
    Promise.all(
      PEER_GROUPS.map((group) =>
        apiFetch<ScreenerResponse>(
          `/screener?peer_group=${group}&official_only=true&page_size=5`,
        ),
      ),
    ),
    apiFetch<ScreenerResponse>("/screener?official_only=true&page_size=1"),
  ]);
  const coverageItems = status.coverage ?? [];
  const supported = coverageItems.reduce((sum, item) => sum + item.supported_assets, 0);
  const ready = coverageItems.reduce((sum, item) => sum + item.ready_assets, 0);
  const failed = status.last_sync?.failed_tickers?.length ?? 0;
  const asOf = groupResponses.map((response) => response.as_of).sort().at(-1);
  const showSync = isLocal && (
    status.snapshot_state === "STALE" ||
    ["QUEUED", "RUNNING", "FAILED"].includes(status.last_sync?.status ?? "")
  );

  return (
    <>
      <header className="page-header">
        <div>
          <h1>오늘의 추세 후보</h1>
          <p>시장과 상품 유형을 나눈 여섯 비교군에서 공식 후보를 각각 확인합니다.</p>
        </div>
        <div className="page-meta">
          <DataModeBadge source={status.data_source} />
          <span>{asOf} 종가 기준</span>
        </div>
      </header>

      {showSync && <ResearchSyncPanel initial={status} />}

      <section className="metric-strip" aria-label="주요 지표" style={showSync ? { marginTop: 20 } : undefined}>
        <div className="metric-cell">
          <span className="metric-label">공식 추세 후보</span>
          <span className="metric-value">{official.total.toLocaleString()}개</span>
          <span className="metric-detail">신규 65점 · 기존 60점 이상</span>
        </div>
        <div className="metric-cell">
          <span className="metric-label">지원 종목</span>
          <span className="metric-value">{supported.toLocaleString()}개</span>
          <span className="metric-detail">보통주·비레버리지 주식형 ETF</span>
        </div>
        <div className="metric-cell">
          <span className="metric-label">점수 산출 가능</span>
          <span className="metric-value">{ready.toLocaleString()}개</span>
          <span className="metric-detail">253거래일 이상 가격 이력</span>
        </div>
        <div className="metric-cell">
          <span className="metric-label">마지막 정상 갱신</span>
          <span className="metric-value metric-value-small">{isLocal ? formatTimestamp(status.last_success_at) : "고정 시드"}</span>
          <span className="metric-detail">수집 실패 {failed.toLocaleString()}종목</span>
        </div>
      </section>

      <div className="candidate-group-grid">
        {PEER_GROUPS.map((group, index) => {
          const response = groupResponses[index];
          const coverage = coverageItems.find((item) => item.peer_group === group);
          return (
            <section className="section-panel" key={group}>
              <div className="panel-header">
                <div>
                  <h2>{PEER_LABELS[group]}</h2>
                  <p>
                    상위 5개 · {coverage?.as_of ?? response.as_of} 기준 · 산출 가능 {coverage?.ready_assets.toLocaleString() ?? "-"}개
                  </p>
                </div>
                <Link href={`/screener?peer_group=${group}&official_only=true`} className="text-link">
                  전체 보기
                </Link>
              </div>
              <GroupCandidateList items={response.items} />
            </section>
          );
        })}
      </div>

      <div className="dashboard-notices">
        <div className="notice-box">
          <CheckCircle2 size={15} /> 후보 점수는 같은 비교군 안에서만 비교합니다.
        </div>
        {failed > 0 ? (
          <div className="notice-box warning-box">
            <AlertTriangle size={15} /> 실패 종목은 이전 정상 가격을 유지하고 다음 갱신에서 다시 시도합니다.
          </div>
        ) : (
          <div className="notice-box neutral-box">
            <Database size={15} /> 가격·거래량만 사용하며 기업 실적과 뉴스는 반영하지 않습니다.
          </div>
        )}
      </div>

      <div className="dashboard-action-row">
        <Link href="/screener" className="button button-primary">
          <ListFilter size={16} /> 전체 스크리너
        </Link>
        <span>투자 추천 또는 상승 확률이 아닙니다.</span>
      </div>
    </>
  );
}
