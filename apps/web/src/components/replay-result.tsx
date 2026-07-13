"use client";

import {
  AlertTriangle,
  BarChart3,
  ClipboardCheck,
  Download,
  LayoutDashboard,
  ListChecks,
  LoaderCircle,
  RotateCcw,
} from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { PEER_LABELS } from "@/components/candidate-table";
import { LineChart } from "@/components/line-chart";
import {
  LegacyAnalysisNotice,
  ReplayCauseAnalysis,
  ReplayIntegrity,
  ReplayTradeQuality,
} from "@/components/replay-analysis-view";
import { StatusBadge } from "@/components/ui/status-badge";
import { apiFetch, type Artifact, type ReplayResponse } from "@/lib/api";
import { formatKrw, formatNumber, formatPercent } from "@/lib/utils";

type ReplayResult = NonNullable<ReplayResponse["result"]>;
type ResultTab = "summary" | "analysis" | "trades" | "integrity";

const STAGE_LABELS: Record<string, string> = {
  QUEUED: "실행 대기",
  RUNNING: "준비",
  SCORE_PARTITIONS: "연도별 추세 점수 계산",
  WEEKLY_SIGNALS: "주간 후보 구성",
  BUILD_SIGNALS: "전략 조건별 후보 구성",
  LOAD_MARKET_EVENTS: "가격·배당·분할 불러오기",
  PREPARE_MARKET: "시장 데이터 행렬 준비",
  SIMULATE_ACTUAL: "실제 비용 포트폴리오 재생",
  SIMULATE_NO_COST: "비용 없는 비교 포트폴리오 재생",
  ANALYZE: "성과 원인과 무결성 분석",
  VALIDATE: "독립 검증·워크포워드·스트레스 검사",
  SUCCEEDED: "완료",
  FAILED: "실패",
  CANCELLED: "사용자 취소",
};

const METRICS = [
  ["cagr", "연환산 수익률", formatPercent],
  ["annual_volatility", "연환산 변동성", formatPercent],
  ["max_drawdown", "최대 낙폭", formatPercent],
  ["sharpe", "Sharpe", formatNumber],
  ["sortino", "Sortino", formatNumber],
  ["turnover", "누적 회전율", formatNumber],
  ["trade_count", "거래 수", (value: number) => `${value.toFixed(0)}회`],
  ["final_value_krw", "최종 평가액", formatKrw],
] as const;

const TABS: Array<{
  id: ResultTab;
  label: string;
  icon: typeof LayoutDashboard;
}> = [
  { id: "summary", label: "요약", icon: LayoutDashboard },
  { id: "analysis", label: "원인 분석", icon: BarChart3 },
  { id: "trades", label: "거래 품질", icon: ListChecks },
  { id: "integrity", label: "검증", icon: ClipboardCheck },
];

function ReplaySummaryView({
  result,
  artifacts,
}: {
  result: ReplayResult;
  artifacts: Artifact[];
}) {
  return (
    <div className="analysis-stack">
      <section className="metric-strip backtest-metrics">
        {METRICS.map(([key, label, formatter]) => (
          <div className="metric-cell" key={key}>
            <span className="metric-label">{label}</span>
            <span className="metric-value">
              {formatter(result.metrics[key] ?? 0)}
            </span>
          </div>
        ))}
      </section>

      <section className="section-panel">
        <div className="panel-header">
          <div>
            <h2>포트폴리오 가치</h2>
            <p>동일 자산군 비중 합성 벤치마크 비교</p>
          </div>
        </div>
        <div className="panel-body">
          <LineChart
            labels={result.equity_curve.map((point) => point.date)}
            series={[
              {
                name: "포트폴리오",
                values: result.equity_curve.map((point) => point.portfolio),
                color: "#176b48",
                area: true,
              },
              {
                name: "합성 벤치마크",
                values: result.equity_curve.map((point) => point.benchmark),
                color: "#23699a",
              },
            ]}
            valueFormat="krw-millions"
          />
        </div>
      </section>

      <div className="section-grid analysis-section-grid">
        <section className="section-panel">
          <div className="panel-header">
            <div>
              <h2>낙폭</h2>
              <p>초기 자금과 이전 최고 평가액 대비 하락률</p>
            </div>
          </div>
          <div className="panel-body">
            <LineChart
              compact
              labels={result.drawdown_curve.map((point) => point.date)}
              series={[
                {
                  name: "낙폭",
                  values: result.drawdown_curve.map((point) => point.drawdown),
                  color: "#a23a3a",
                  area: true,
                },
              ]}
              valueFormat="percent"
            />
          </div>
        </section>
        <section className="section-panel">
          <div className="panel-header">
            <div>
              <h2>결과 파일</h2>
              <p>보고서와 계산 원장</p>
            </div>
          </div>
          <div className="panel-body artifact-list artifact-list-scroll">
            {artifacts.map((artifact) => (
              <a
                key={artifact.name}
                href={artifact.download_url}
                className="button button-secondary"
                target="_blank"
                rel="noreferrer"
              >
                <Download size={15} /> {artifact.name}
                <span>{Math.ceil(artifact.size_bytes / 1024)}KB</span>
              </a>
            ))}
          </div>
        </section>
      </div>

      <section className="section-panel">
        <div className="panel-header">
          <div>
            <h2>최종 포지션</h2>
            <p>{result.final_positions.length}종목</p>
          </div>
        </div>
        <div className="data-table-wrap">
          <table className="data-table">
            <thead>
              <tr>
                <th>종목</th>
                <th>비교군</th>
                <th>수량</th>
                <th>평가액</th>
                <th>점수</th>
              </tr>
            </thead>
            <tbody>
              {result.final_positions.map((position) => (
                <tr key={position.asset_id}>
                  <td className="symbol-cell">
                    <strong>{position.symbol}</strong>
                    <span>{position.name}</span>
                  </td>
                  <td>{PEER_LABELS[position.peer_group] ?? position.peer_group}</td>
                  <td>{position.quantity.toLocaleString()}</td>
                  <td>{formatKrw(position.market_value_krw)}</td>
                  <td className="score-number">{position.score.toFixed(1)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <div className="notice-box warning-box">
        {result.warnings?.join(" ")}
      </div>
      {(result.review_required_assets?.length ?? 0) > 0 && (
        <div className="notice-box danger-box">
          데이터 검토 필요: {result.review_required_assets?.join(", ")}
        </div>
      )}
    </div>
  );
}

export function ReplayResultView({ runId }: { runId: string }) {
  const [run, setRun] = useState<ReplayResponse | null>(null);
  const [artifacts, setArtifacts] = useState<Artifact[]>([]);
  const [activeTab, setActiveTab] = useState<ResultTab>("summary");
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const next = await apiFetch<ReplayResponse>(`/research/replays/${runId}`);
      setRun(next);
      if (next.status === "SUCCEEDED") {
        setArtifacts(
          await apiFetch<Artifact[]>(`/research/replays/${runId}/artifacts`),
        );
      }
      if (next.status === "FAILED") {
        setError(next.error_message ?? "과거 재생이 실패했습니다.");
      }
      return next.status;
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "결과를 불러오지 못했습니다.",
      );
      return "FAILED";
    }
  }, [runId]);

  useEffect(() => {
    let active = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const poll = async () => {
      const status = await load();
      if (active && ["QUEUED", "RUNNING"].includes(status)) {
        timer = setTimeout(poll, 1500);
      }
    };
    void poll();
    return () => {
      active = false;
      if (timer) clearTimeout(timer);
    };
  }, [load]);

  if (!run || ["QUEUED", "RUNNING"].includes(run.status)) {
    const progress = run?.progress_percent ?? 0;
    return (
      <section className="section-panel replay-progress" aria-live="polite">
        <LoaderCircle size={30} className="spin" aria-hidden="true" />
        <h1>실데이터를 과거 순서대로 재생하고 있습니다</h1>
        <p>{STAGE_LABELS[run?.stage ?? "QUEUED"] ?? run?.stage}</p>
        <div className="progress-track" aria-label={`진행률 ${progress}%`}>
          <div className="progress-value" style={{ width: `${progress}%` }} />
        </div>
        <div className="replay-progress-meta">
          <StatusBadge state={run?.status ?? "QUEUED"} />
          <span>{progress.toFixed(1)}%</span>
        </div>
      </section>
    );
  }

  if (error || run.status === "FAILED" || !run.result) {
    return (
      <section className="section-panel empty-state">
        <h1>과거 시뮬레이션을 완료하지 못했습니다</h1>
        <p>{error ?? run.error_message}</p>
        <Link href="/replays" className="button button-secondary">
          <RotateCcw size={16} /> 설정으로 돌아가기
        </Link>
      </section>
    );
  }

  const result = run.result;
  const analysis = result.analysis;
  return (
    <>
      <header className="page-header">
        <div>
          <div style={{ marginBottom: 7 }}>
            <StatusBadge state={run.status} />
          </div>
          <h1>과거 시뮬레이션 결과</h1>
          <p>
            {result.started_on}부터 {result.ended_on}까지 실제 일봉을 시간순으로
            재생했습니다.
          </p>
        </div>
        <div className="page-meta">
          <span className="demo-chip real-data-chip">로컬 실데이터</span>
          <span>{result.portfolio_version}</span>
        </div>
      </header>

      <div className="notice-box warning-box replay-bias-notice">
        <AlertTriangle size={15} /> {run.bias_warning} 공식 성과나 투자 추천이
        아닙니다.
      </div>

      <div className="result-tabs" role="tablist" aria-label="시뮬레이션 결과 보기">
        {TABS.map((tab) => {
          const Icon = tab.icon;
          return (
            <button
              key={tab.id}
              type="button"
              role="tab"
              aria-selected={activeTab === tab.id}
              aria-controls={`replay-tabpanel-${tab.id}`}
              className={activeTab === tab.id ? "result-tab result-tab-active" : "result-tab"}
              onClick={() => setActiveTab(tab.id)}
            >
              <Icon size={16} aria-hidden="true" />
              {tab.label}
            </button>
          );
        })}
      </div>

      <div
        id={`replay-tabpanel-${activeTab}`}
        role="tabpanel"
        className="result-tabpanel"
      >
        {activeTab === "summary" && (
          <ReplaySummaryView result={result} artifacts={artifacts} />
        )}
        {activeTab !== "summary" && !analysis && <LegacyAnalysisNotice />}
        {activeTab === "analysis" && analysis && (
          <ReplayCauseAnalysis result={result} analysis={analysis} />
        )}
        {activeTab === "trades" && analysis && (
          <ReplayTradeQuality analysis={analysis} />
        )}
        {activeTab === "integrity" && analysis && (
          <ReplayIntegrity result={result} analysis={analysis} />
        )}
      </div>
    </>
  );
}
