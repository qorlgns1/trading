"use client";

import {
  AlertTriangle,
  Archive,
  ArrowRight,
  BarChart3,
  Beaker,
  CheckCircle2,
  FlaskConical,
  Gauge,
  LineChart as LineChartIcon,
  ListChecks,
  LoaderCircle,
  Play,
  ShieldCheck,
  WalletCards,
  XCircle,
} from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { LineChart } from "@/components/line-chart";
import { ReplayParetoChart } from "@/components/replay-pareto-chart";
import { Button } from "@/components/ui/button";
import { StatusBadge } from "@/components/ui/status-badge";
import {
  apiFetch,
  type ReplayComparison,
  type ReplayExperiment,
  type ReplayStrategy,
} from "@/lib/api";
import { cn, formatKrw, formatNumber, formatPercent } from "@/lib/utils";

type Tab =
  | "summary"
  | "compare"
  | "causes"
  | "validation"
  | "sweep"
  | "trades"
  | "integrity";
type RunResult = {
  data_version?: string;
  score_version?: string;
  portfolio_version?: string;
  warnings?: string[];
  metrics?: Record<string, number>;
  equity_curve?: Array<{ date: string; portfolio: number; benchmark: number }>;
  analysis?: {
    version?: string;
    cost_summary?: { explicit_cost_krw?: number };
    headline?: { title?: string; summary?: string };
    gap_analysis?: Record<string, number>;
    sleeve_attribution?: Array<{
      sleeve?: string;
      pnl_krw?: number;
      contribution?: number;
      average_exposure?: number;
    }>;
    trade_analysis?: { overall?: Record<string, number> };
    integrity_checks?: Array<{
      code: string;
      label: string;
      status: string;
      detail: string;
    }>;
  };
  validation?: {
    training?: Record<string, number>;
    continuous_validation?: Record<string, number>;
    independent_validation?: { metrics?: Record<string, number> };
  };
  walk_forward?: {
    summary?: Record<string, number | null>;
    windows?: Array<{
      test_start: string;
      test_end: string;
      metrics: Record<string, number>;
    }>;
  };
  stress_tests?: {
    costs_x2?: { metrics?: Record<string, number> };
    execution_delay_plus_one?: { metrics?: Record<string, number> };
    winner_concentration?: Record<string, number | string>;
  };
  strategy_config?: ReplayStrategy;
  rows?: SweepRow[];
  pareto?: SweepRow[];
  diagnostics?: SweepDiagnostics;
  axes?: Array<{ path: string; values: unknown[] }>;
};
type ExperimentRun = {
  run_id: string;
  role: string;
  label: string;
  status: string;
  stage: string;
  config: { strategy?: ReplayStrategy };
  result?: RunResult | null;
  error_message?: string | null;
};
type SweepRow = {
  index: number;
  strategy: ReplayStrategy;
  training: Record<string, number>;
  validation: Record<string, number>;
  full: Record<string, number>;
  trade_count: number;
  transaction_cost_krw: number;
  winner_concentration?: {
    top_1_profit_ratio?: number;
    top_3_profit_ratio?: number;
  };
};
type SweepDiagnostics = {
  trial_count?: number;
  valid_trial_count?: number;
  invalid_trial_count?: number;
  train_validation_spearman?: number;
  top_decile_overlap?: number;
  top_decile_size?: number;
  top_decile_overlap_rate?: number;
  best_train_validation_rank?: number;
  trade_count?: { minimum?: number; median?: number; maximum?: number };
  maximum_top_3_profit_ratio?: number;
  pareto_boundary_axes?: string[];
  warnings?: string[];
};
type ComparedRun = {
  run_id: string;
  label: string;
  role: string;
  metrics?: Record<string, number>;
  full_metrics?: Record<string, number>;
  classification?: string;
  cost_krw?: number;
  differences?: Record<string, number>;
  explanation?: string;
};

const TABS: Array<[Tab, string, typeof Gauge]> = [
  ["summary", "요약", Gauge],
  ["compare", "전략 비교", BarChart3],
  ["causes", "원인 분석", LineChartIcon],
  ["validation", "기간 검증", LineChartIcon],
  ["sweep", "민감도", FlaskConical],
  ["trades", "거래 품질", ListChecks],
  ["integrity", "무결성", ShieldCheck],
];

type SweepValueKind =
  | "number"
  | "enum"
  | "boolean"
  | "weight-percent"
  | "rate-percent"
  | "optional-percent";
const SWEEP_AXES: Array<{
  path: string;
  label: string;
  kind: SweepValueKind;
  defaults: string;
}> = [
  {
    path: "signal.entry_score",
    label: "진입 점수",
    kind: "number",
    defaults: "65,70,75,80,85,90",
  },
  {
    path: "signal.exit_score",
    label: "해제 점수",
    kind: "number",
    defaults: "50,55,60,65,70,75,80",
  },
  {
    path: "signal.minimum_adv_multiplier",
    label: "거래대금 배수",
    kind: "number",
    defaults: "0.8,1,1.2,1.5",
  },
  {
    path: "signal.market_gate_mode",
    label: "시장 200일선 방어",
    kind: "enum",
    defaults: "BLOCK_NEW_ENTRIES_BELOW_SMA200,OFF",
  },
  {
    path: "signal.require_above_sma200",
    label: "종목 200일선 필수",
    kind: "boolean",
    defaults: "true,false",
  },
  {
    path: "signal.require_positive_six_month",
    label: "6개월 상승 필수",
    kind: "boolean",
    defaults: "true,false",
  },
  ...[
    ["long_term_trend", "장기 추세 가중치"],
    ["absolute_momentum", "절대 모멘텀 가중치"],
    ["relative_strength", "상대 강도 가중치"],
    ["high_proximity", "고점 근접도 가중치"],
    ["volatility_stability", "변동성 안정성 가중치"],
    ["trading_activity", "거래 활동 가중치"],
  ].map(([key, label]) => ({
    path: `signal.component_weights_bps.${key}`,
    label,
    kind: "weight-percent" as const,
    defaults: "10,20,30,40",
  })),
  ...[
    ["us_stock", "미국 주식 비중"],
    ["kr_stock", "한국 주식 비중"],
    ["us_etf", "미국 ETF 비중"],
    ["kr_etf", "한국 ETF 비중"],
  ].map(([key, label]) => ({
    path: `portfolio.sleeve_weights_bps.${key}`,
    label,
    kind: "weight-percent" as const,
    defaults: "10,20,30,40",
  })),
  {
    path: "portfolio.position_sizing",
    label: "종목 투자금 방식",
    kind: "enum",
    defaults: "EQUAL_SLOT,INVERSE_VOLATILITY",
  },
  {
    path: "portfolio.replacement_policy",
    label: "보유 종목 교체",
    kind: "enum",
    defaults: "FILL_VACANCIES,TOP_SCORE_REBALANCE",
  },
  {
    path: "portfolio.replacement_score_gap",
    label: "교체 점수 차이",
    kind: "number",
    defaults: "0,5,10,15",
  },
  ...[
    ["us_stock", "미국 주식 슬롯"],
    ["us_equity_etf", "미국 ETF 슬롯"],
    ["kr_kospi", "KOSPI 슬롯"],
    ["kr_kosdaq", "KOSDAQ 슬롯"],
    ["kr_domestic_equity_etf", "국내 ETF 슬롯"],
    ["kr_overseas_equity_etf", "해외형 한국 ETF 슬롯"],
  ].map(([key, label]) => ({
    path: `portfolio.peer_group_slots.${key}`,
    label,
    kind: "number" as const,
    defaults: "1,2,3,4",
  })),
  {
    path: "risk.fixed_stop_loss",
    label: "고정 손절",
    kind: "optional-percent",
    defaults: "off,5,10,15,20",
  },
  {
    path: "risk.trailing_stop_loss",
    label: "추적 손절",
    kind: "optional-percent",
    defaults: "off,10,15,20,25",
  },
  {
    path: "execution.review_frequency",
    label: "평가 주기",
    kind: "enum",
    defaults: "DAILY,WEEKLY,MONTHLY",
  },
  {
    path: "execution.execution_delay_sessions",
    label: "체결 지연",
    kind: "number",
    defaults: "1,2,3,4,5",
  },
  {
    path: "execution.us_buy_cost",
    label: "미국 매수 비용",
    kind: "rate-percent",
    defaults: "0.1,0.15,0.25",
  },
  {
    path: "execution.us_sell_cost",
    label: "미국 매도 비용",
    kind: "rate-percent",
    defaults: "0.1,0.15,0.25",
  },
  {
    path: "execution.kr_buy_cost",
    label: "한국 매수 비용",
    kind: "rate-percent",
    defaults: "0.15,0.25,0.35",
  },
  {
    path: "execution.kr_sell_cost",
    label: "한국 매도 비용",
    kind: "rate-percent",
    defaults: "0.15,0.25,0.35",
  },
  {
    path: "execution.initial_fx_cost",
    label: "최초 환전 비용",
    kind: "rate-percent",
    defaults: "0.1,0.25,0.5",
  },
  {
    path: "execution.slippage_bps",
    label: "슬리피지",
    kind: "number",
    defaults: "0,5,10,20",
  },
];

function parseSweepValues(raw: string, kind: SweepValueKind): unknown[] {
  const tokens = raw
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
  if (kind === "enum") return tokens;
  if (kind === "boolean")
    return tokens.map((item) => item.toLowerCase() === "true");
  if (kind === "optional-percent")
    return tokens.map((item) =>
      ["off", "null", "끔"].includes(item.toLowerCase())
        ? null
        : Number(item) / 100,
    );
  if (kind === "weight-percent")
    return tokens.map((item) => Number(item) * 100);
  if (kind === "rate-percent") return tokens.map((item) => Number(item) / 100);
  return tokens.map(Number);
}

function nestedValue(payload: unknown, path: string): unknown {
  return path.split(".").reduce<unknown>((current, part) => {
    if (!current || typeof current !== "object") return undefined;
    return (current as Record<string, unknown>)[part];
  }, payload);
}

function runsOf(experiment: ReplayExperiment): ExperimentRun[] {
  return (experiment.runs ?? []) as ExperimentRun[];
}

export function ReplayExperimentDetail({
  initialExperiment,
  initialComparison,
}: {
  initialExperiment: ReplayExperiment;
  initialComparison: ReplayComparison;
}) {
  const [experiment, setExperiment] = useState(initialExperiment);
  const [comparison, setComparison] = useState(initialComparison);
  const [tab, setTab] = useState<Tab>("summary");
  const [message, setMessage] = useState<string | null>(null);

  const reload = useCallback(async () => {
    const [nextExperiment, nextComparison] = await Promise.all([
      apiFetch<ReplayExperiment>(
        `/research/experiments/${initialExperiment.experiment_id}`,
      ),
      apiFetch<ReplayComparison>(
        `/research/experiments/${initialExperiment.experiment_id}/comparison`,
      ),
    ]);
    setExperiment(nextExperiment);
    setComparison(nextComparison);
    return runsOf(nextExperiment).some((run) =>
      ["QUEUED", "RUNNING"].includes(run.status),
    );
  }, [initialExperiment.experiment_id]);

  useEffect(() => {
    if (
      !runsOf(experiment).some((run) =>
        ["QUEUED", "RUNNING"].includes(run.status),
      )
    )
      return;
    const timer = window.setInterval(() => void reload(), 2000);
    return () => window.clearInterval(timer);
  }, [experiment, reload]);

  const runs = runsOf(experiment);
  const strategyRuns = runs.filter((run) => run.role !== "SWEEP");
  const baseline = strategyRuns.find((run) => run.role === "BASELINE");
  const completed = strategyRuns.filter(
    (run) => run.status === "SUCCEEDED" && run.result,
  );
  const sweep = [...runs].reverse().find((run) => run.role === "SWEEP");

  async function promote(
    run: ExperimentRun,
    accountType: "BASELINE" | "EXPERIMENT",
  ) {
    setMessage(null);
    try {
      await apiFetch(`/research/replays/${run.run_id}/promote`, {
        method: "POST",
        body: JSON.stringify({
          account_type: accountType,
          name: run.label,
          experiment_id: experiment.experiment_id,
        }),
      });
      setMessage(`${run.label}을 포워드 계좌로 연결했습니다.`);
    } catch (reason) {
      setMessage(
        reason instanceof Error
          ? reason.message
          : "포워드 계좌를 만들지 못했습니다.",
      );
    }
  }

  async function cancel(runId: string) {
    setMessage(null);
    try {
      await apiFetch(`/research/replays/${runId}/cancel`, { method: "POST" });
      setMessage("취소 요청을 저장했습니다.");
      await reload();
    } catch (reason) {
      setMessage(
        reason instanceof Error ? reason.message : "취소하지 못했습니다.",
      );
    }
  }

  async function archive() {
    await apiFetch(`/research/experiments/${experiment.experiment_id}`, {
      method: "PATCH",
      body: JSON.stringify({ archived: true }),
    });
    window.location.href = "/replays";
  }

  return (
    <>
      <header className="page-header experiment-detail-header">
        <div>
          <div className="eyebrow-row">
            <span>{experiment.objective}</span>
            <span>{experiment.run_count}개 실행</span>
          </div>
          <h1>{experiment.name}</h1>
          <p>{experiment.hypothesis}</p>
        </div>
        <div className="page-actions">
          <Button
            variant="ghost"
            title="실험 보관"
            aria-label="실험 보관"
            onClick={archive}
          >
            <Archive size={17} />
          </Button>
          <Link
            className="button button-secondary"
            href={`/replays/new?experiment_id=${experiment.experiment_id}`}
          >
            <Beaker size={16} />
            도전 전략 추가
          </Link>
        </div>
      </header>

      <nav className="result-tabs experiment-tabs" aria-label="실험 결과">
        {TABS.map(([id, label, Icon]) => (
          <button
            key={id}
            type="button"
            className={cn(tab === id && "result-tab-active")}
            onClick={() => setTab(id)}
          >
            <Icon size={16} />
            {label}
          </button>
        ))}
      </nav>

      {message && (
        <div className="notice-box info-box experiment-message">{message}</div>
      )}
      {tab === "summary" && (
        <SummaryTab
          runs={strategyRuns}
          comparison={comparison}
          onPromote={promote}
          onCancel={cancel}
        />
      )}
      {tab === "compare" && (
        <CompareTab runs={completed} comparison={comparison} />
      )}
      {tab === "causes" && <CauseAnalysisTab runs={completed} />}
      {tab === "validation" && <ValidationTab runs={completed} />}
      {tab === "sweep" && (
        <SweepTab
          experiment={experiment}
          baseline={baseline}
          sweep={sweep}
          onReload={reload}
          onCancel={cancel}
        />
      )}
      {tab === "trades" && (
        <TradesTab runs={completed} comparison={comparison} />
      )}
      {tab === "integrity" && <IntegrityTab runs={completed} />}
    </>
  );
}

function SummaryTab({
  runs,
  comparison,
  onPromote,
  onCancel,
}: {
  runs: ExperimentRun[];
  comparison: ReplayComparison;
  onPromote: (run: ExperimentRun, type: "BASELINE" | "EXPERIMENT") => void;
  onCancel: (runId: string) => void;
}) {
  const comparisonByRun = new Map(
    (comparison.runs ?? []).map((item) => [String(item.run_id), item]),
  );
  return (
    <div className="analysis-stack experiment-run-stack">
      {runs.map((run) => {
        const result = run.result;
        const metrics = result?.metrics;
        const compared = comparisonByRun.get(run.run_id) as
          | ComparedRun
          | undefined;
        return (
          <section className="section-panel run-summary-row" key={run.run_id}>
            <div className="run-summary-main">
              <div>
                <div className="run-title-row">
                  <h2>{run.label}</h2>
                  <StatusBadge state={run.status} />
                  {compared?.classification && (
                    <span className="status-pill status-neutral">
                      {compared.classification}
                    </span>
                  )}
                </div>
                <p>
                  {compared?.explanation ??
                    (run.role === "BASELINE"
                      ? "사용자가 선택한 비교 기준"
                      : "기준 전략과 동일 기간 비교")}
                </p>
              </div>
              {run.status === "SUCCEEDED" && (
                <div className="run-actions">
                  <Link
                    className="button button-ghost"
                    href={`/replays/${run.run_id}`}
                  >
                    상세
                    <ArrowRight size={15} />
                  </Link>
                  <Button
                    variant="secondary"
                    onClick={() =>
                      onPromote(
                        run,
                        run.role === "BASELINE" ? "BASELINE" : "EXPERIMENT",
                      )
                    }
                  >
                    <WalletCards size={15} />
                    포워드
                  </Button>
                </div>
              )}
              {["QUEUED", "RUNNING"].includes(run.status) && (
                <Button
                  variant="secondary"
                  onClick={() => onCancel(run.run_id)}
                >
                  <XCircle size={15} />
                  취소
                </Button>
              )}
            </div>
            {metrics ? (
              <div className="compact-metric-grid">
                <Metric
                  label="최종 평가액"
                  value={formatKrw(metrics.final_value_krw ?? 0)}
                />
                <Metric label="CAGR" value={formatPercent(metrics.cagr ?? 0)} />
                <Metric
                  label="최대 낙폭"
                  value={formatPercent(metrics.max_drawdown ?? 0)}
                />
                <Metric
                  label="Sharpe"
                  value={formatNumber(metrics.sharpe ?? 0)}
                />
                <Metric
                  label="거래"
                  value={`${Math.round(metrics.trade_count ?? 0)}회`}
                />
              </div>
            ) : (
              <div className="run-progress">
                <LoaderCircle
                  size={17}
                  className={run.status === "FAILED" ? undefined : "spin"}
                />
                <span>{run.error_message ?? run.stage}</span>
              </div>
            )}
          </section>
        );
      })}
    </div>
  );
}

function CompareTab({
  runs,
  comparison,
}: {
  runs: ExperimentRun[];
  comparison: ReplayComparison;
}) {
  if (runs.length === 0) return <EmptyResult />;
  const baseline = runs.find((run) => run.role === "BASELINE") ?? runs[0];
  const labels =
    baseline.result?.equity_curve?.map((point) => point.date) ?? [];
  const assessments = new Map(
    (comparison.success_assessments ?? []).map((item) => [
      String(item.run_id),
      item,
    ]),
  );
  const comparedByRun = new Map(
    ((comparison.runs ?? []) as ComparedRun[]).map((item) => [
      item.run_id,
      item,
    ]),
  );
  return (
    <div className="analysis-stack">
      <section className="section-panel">
        <div className="panel-header">
          <div>
            <h2>포트폴리오 가치 비교</h2>
            <p>모든 전략은 같은 초기 자금과 데이터 기간을 사용합니다.</p>
          </div>
        </div>
        <div className="panel-body">
          <LineChart
            labels={labels}
            valueFormat="krw-millions"
            series={runs.map((run, index) => {
              const map = new Map(
                run.result?.equity_curve?.map((point) => [
                  point.date,
                  point.portfolio,
                ]) ?? [],
              );
              return {
                name: run.label,
                values: labels.map((date) => map.get(date) ?? null),
                color: ["#176b48", "#23699a", "#96630d", "#a23a3a"][index],
              };
            })}
          />
        </div>
      </section>
      <div className="comparison-explanation-grid">
        {runs.map((run) => {
          const compared = comparedByRun.get(run.run_id);
          return (
            <section className="comparison-explanation" key={run.run_id}>
              <div>
                <strong>{run.label}</strong>
                <span>{compared?.classification ?? "계산 중"}</span>
              </div>
              <p>{compared?.explanation ?? "검증 결과를 계산하고 있습니다."}</p>
            </section>
          );
        })}
      </div>
      <section className="section-panel">
        <div className="data-table-wrap">
          <table className="data-table experiment-comparison-table">
            <thead>
              <tr>
                <th>전략</th>
                <th>최종 금액</th>
                <th>검증 CAGR</th>
                <th>검증 MDD</th>
                <th>Sharpe</th>
                <th>비용</th>
                <th>거래</th>
                <th>평균 노출</th>
                <th>성공 기준</th>
              </tr>
            </thead>
            <tbody>
              {runs.map((run) => {
                const compared = comparedByRun.get(run.run_id);
                const metrics = compared?.metrics ?? {};
                const fullMetrics = compared?.full_metrics ?? {};
                const differences = compared?.differences ?? {};
                const assessment = assessments.get(run.run_id) as
                  | { passed?: boolean }
                  | undefined;
                const isBaseline = run.role === "BASELINE";
                return (
                  <tr key={run.run_id}>
                    <td>
                      <strong>{run.label}</strong>
                    </td>
                    <ComparisonValue
                      value={formatKrw(fullMetrics.final_value_krw ?? 0)}
                      difference={
                        isBaseline
                          ? undefined
                          : formatSignedKrw(differences.final_value_krw ?? 0)
                      }
                    />
                    <ComparisonValue
                      value={formatPercent(metrics.cagr ?? 0)}
                      difference={
                        isBaseline
                          ? undefined
                          : formatSigned(differences.cagr_pp ?? 0, "%p")
                      }
                    />
                    <ComparisonValue
                      value={formatPercent(metrics.max_drawdown ?? 0)}
                      difference={
                        isBaseline
                          ? undefined
                          : `${formatSigned(
                              differences.mdd_improvement_pp ?? 0,
                              "%p",
                            )} 개선`
                      }
                    />
                    <ComparisonValue
                      value={formatNumber(metrics.sharpe ?? 0)}
                      difference={
                        isBaseline
                          ? undefined
                          : formatSigned(differences.sharpe ?? 0)
                      }
                    />
                    <ComparisonValue
                      value={formatKrw(compared?.cost_krw ?? 0)}
                      difference={
                        isBaseline
                          ? undefined
                          : formatSignedKrw(differences.cost_krw ?? 0)
                      }
                    />
                    <ComparisonValue
                      value={`${Math.round(fullMetrics.trade_count ?? 0)}회`}
                      difference={
                        isBaseline
                          ? undefined
                          : formatSigned(differences.trade_count ?? 0, "회", 0)
                      }
                    />
                    <ComparisonValue
                      value={formatPercent(fullMetrics.average_exposure ?? 0)}
                      difference={
                        isBaseline
                          ? undefined
                          : formatSigned(
                              differences.average_exposure_pp ?? 0,
                              "%p",
                            )
                      }
                    />
                    <td>
                      {isBaseline ? (
                        "기준"
                      ) : assessment?.passed ? (
                        <span className="positive-text">통과</span>
                      ) : (
                        <span className="negative-text">미통과</span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}

function CauseAnalysisTab({ runs }: { runs: ExperimentRun[] }) {
  if (runs.length === 0) return <EmptyResult />;
  return (
    <div className="analysis-stack">
      {runs.map((run) => {
        const analysis = run.result?.analysis;
        const gap = analysis?.gap_analysis ?? {};
        return (
          <section className="section-panel" key={run.run_id}>
            <div className="panel-header">
              <div>
                <h2>{run.label}</h2>
                <p>
                  {analysis?.headline?.title ?? "성과 원인을 계산했습니다."}
                </p>
              </div>
            </div>
            <div className="panel-body analysis-stack">
              <p className="cause-summary">
                {analysis?.headline?.summary ??
                  "시장 노출, 종목 선택·체결, 비용 효과를 분리해 비교합니다."}
              </p>
              <div className="compact-metric-grid cause-metric-grid">
                <Metric
                  label="완전투자 벤치마크"
                  value={formatPercent(gap.full_benchmark_return ?? 0)}
                />
                <Metric
                  label="노출·시장 제한 효과"
                  value={formatPercent(gap.exposure_effect ?? 0)}
                />
                <Metric
                  label="종목 선택·체결 효과"
                  value={formatPercent(gap.selection_execution_effect ?? 0)}
                />
                <Metric
                  label="비용 효과"
                  value={formatPercent(gap.cost_effect ?? 0)}
                />
                <Metric
                  label="실제 전략 수익률"
                  value={formatPercent(gap.actual_strategy_return ?? 0)}
                />
              </div>
              <div className="data-table-wrap">
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>자산군</th>
                      <th>손익</th>
                      <th>전체 기여</th>
                      <th>평균 노출</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(analysis?.sleeve_attribution ?? []).map((item) => (
                      <tr key={item.sleeve}>
                        <td>{item.sleeve}</td>
                        <td>{formatKrw(item.pnl_krw ?? 0)}</td>
                        <td>{formatPercent(item.contribution ?? 0)}</td>
                        <td>{formatPercent(item.average_exposure ?? 0)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          </section>
        );
      })}
    </div>
  );
}

function ValidationTab({ runs }: { runs: ExperimentRun[] }) {
  if (runs.length === 0) return <EmptyResult />;
  return (
    <div className="analysis-stack">
      {runs.map((run) => {
        const validation = run.result?.validation;
        const walk = run.result?.walk_forward;
        return (
          <section className="section-panel" key={run.run_id}>
            <div className="panel-header">
              <div>
                <h2>{run.label}</h2>
                <p>연속 검증과 현금으로 다시 시작한 독립 검증</p>
              </div>
            </div>
            <div className="panel-body">
              <div className="validation-grid">
                <ValidationMetric title="학습" metrics={validation?.training} />
                <ValidationMetric
                  title="연속 검증"
                  metrics={validation?.continuous_validation}
                />
                <ValidationMetric
                  title="독립 검증"
                  metrics={validation?.independent_validation?.metrics}
                />
                <div className="validation-cell">
                  <span>워크포워드</span>
                  <strong>{walk?.summary?.window_count ?? 0}개 구간</strong>
                  <small>
                    중앙 CAGR{" "}
                    {formatPercent(Number(walk?.summary?.median_cagr ?? 0))}
                  </small>
                </div>
              </div>
              {run.result?.stress_tests && (
                <div className="stress-row">
                  <span>
                    비용 2배 CAGR{" "}
                    <strong>
                      {formatPercent(
                        run.result.stress_tests.costs_x2?.metrics?.cagr ?? 0,
                      )}
                    </strong>
                  </span>
                  <span>
                    체결 +1일 CAGR{" "}
                    <strong>
                      {formatPercent(
                        run.result.stress_tests.execution_delay_plus_one
                          ?.metrics?.cagr ?? 0,
                      )}
                    </strong>
                  </span>
                  <span>
                    상위 3거래 제외 수익률{" "}
                    <strong>
                      {formatPercent(
                        Number(
                          run.result.stress_tests.winner_concentration
                            ?.return_without_top_3 ?? 0,
                        ),
                      )}
                    </strong>
                  </span>
                </div>
              )}
              {(walk?.windows?.length ?? 0) > 0 && (
                <div className="data-table-wrap walk-forward-table">
                  <table className="data-table">
                    <thead>
                      <tr>
                        <th>워크포워드 검증 구간</th>
                        <th>CAGR</th>
                        <th>MDD</th>
                        <th>Sharpe</th>
                      </tr>
                    </thead>
                    <tbody>
                      {walk?.windows?.map((window) => (
                        <tr key={`${window.test_start}-${window.test_end}`}>
                          <td>
                            {window.test_start}–{window.test_end}
                          </td>
                          <td>{formatPercent(window.metrics.cagr ?? 0)}</td>
                          <td>
                            {formatPercent(window.metrics.max_drawdown ?? 0)}
                          </td>
                          <td>{formatNumber(window.metrics.sharpe ?? 0)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          </section>
        );
      })}
    </div>
  );
}

function SweepTab({
  experiment,
  baseline,
  sweep,
  onReload,
  onCancel,
}: {
  experiment: ReplayExperiment;
  baseline?: ExperimentRun;
  sweep?: ExperimentRun;
  onReload: () => Promise<boolean>;
  onCancel: (runId: string) => void;
}) {
  const [axis1, setAxis1] = useState("signal.entry_score");
  const [axis2, setAxis2] = useState("signal.exit_score");
  const [values1, setValues1] = useState("65,70,75,80,85,90");
  const [values2, setValues2] = useState("50,55,60,65,70,75,80");
  const [busy, setBusy] = useState(false);
  const [selectedIndex, setSelectedIndex] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const result = sweep?.result;
  const rows = (result?.rows ?? []) as SweepRow[];
  const pareto = (result?.pareto ?? []) as SweepRow[];
  const diagnostics = result?.diagnostics;
  function changeAxis(
    next: string,
    setAxis: (value: string) => void,
    setValues: (value: string) => void,
  ) {
    setAxis(next);
    setValues(SWEEP_AXES.find((item) => item.path === next)?.defaults ?? "");
  }
  async function start() {
    if (!baseline?.config.strategy) return;
    setBusy(true);
    setError(null);
    try {
      if (axis1 === axis2) throw new Error("서로 다른 두 조건을 선택하세요.");
      const firstMeta = SWEEP_AXES.find((item) => item.path === axis1)!;
      const secondMeta = SWEEP_AXES.find((item) => item.path === axis2)!;
      const firstValues = parseSweepValues(values1, firstMeta.kind);
      const secondValues = parseSweepValues(values2, secondMeta.kind);
      if (
        firstValues.length < 2 ||
        secondValues.length < 2 ||
        firstValues.length * secondValues.length > 100
      )
        throw new Error(
          "각 축은 2개 이상, 전체는 100개 이하 조합이어야 합니다.",
        );
      if (
        [...firstValues, ...secondValues].some(
          (value) => typeof value === "number" && !Number.isFinite(value),
        )
      )
        throw new Error("시험 값에 올바른 숫자를 입력하세요.");
      await apiFetch(
        `/research/experiments/${experiment.experiment_id}/sweeps`,
        {
          method: "POST",
          body: JSON.stringify({
            label: "점수 민감도",
            base_strategy: baseline.config.strategy,
            axes: [
              { path: axis1, values: firstValues },
              { path: axis2, values: secondValues },
            ],
          }),
        },
      );
      await onReload();
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "민감도 분석을 시작하지 못했습니다.",
      );
    } finally {
      setBusy(false);
    }
  }
  async function addCandidate(row: SweepRow) {
    setSelectedIndex(row.index);
    setError(null);
    try {
      await apiFetch(`/research/experiments/${experiment.experiment_id}/runs`, {
        method: "POST",
        body: JSON.stringify({
          label: `Pareto 후보 #${row.index + 1}`,
          role: "PARETO",
          strategy: row.strategy,
        }),
      });
      await onReload();
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "Pareto 후보를 추가하지 못했습니다.",
      );
    } finally {
      setSelectedIndex(null);
    }
  }
  return (
    <div className="analysis-stack">
      <section className="section-panel">
        <div className="panel-header">
          <div>
            <h2>두 조건 민감도</h2>
            <p>최대 100개 조합 · 학습 CAGR과 MDD Pareto 비교</p>
          </div>
          <Button disabled={busy || !baseline} onClick={start}>
            {busy ? (
              <LoaderCircle size={16} className="spin" />
            ) : (
              <Play size={16} />
            )}
            분석 시작
          </Button>
        </div>
        <div className="panel-body sweep-controls">
          <SweepAxis
            value={axis1}
            onChange={(next) => changeAxis(next, setAxis1, setValues1)}
            values={values1}
            onValues={setValues1}
          />
          <SweepAxis
            value={axis2}
            onChange={(next) => changeAxis(next, setAxis2, setValues2)}
            values={values2}
            onValues={setValues2}
          />
          {error && <div className="inline-error">{error}</div>}
        </div>
      </section>
      {sweep && sweep.status !== "SUCCEEDED" && (
        <section className="section-panel run-progress">
          <StatusBadge state={sweep.status} />
          <span>{sweep.stage}</span>
          {["QUEUED", "RUNNING"].includes(sweep.status) && (
            <Button variant="secondary" onClick={() => onCancel(sweep.run_id)}>
              <XCircle size={15} /> 취소
            </Button>
          )}
        </section>
      )}
      {rows.length > 0 && (
        <>
          <SweepDiagnosticsPanel diagnostics={diagnostics} rows={rows} />
          <section className="section-panel">
            <div className="panel-header">
              <div>
                <h2>검증 CAGR 히트맵</h2>
                <p>색이 진할수록 검증 구간 CAGR이 높습니다.</p>
              </div>
            </div>
            <div className="panel-body">
              <SweepHeatmap rows={rows} axes={result?.axes ?? []} />
            </div>
          </section>
          <section className="section-panel">
            <div className="panel-header">
              <div>
                <h2>Pareto 후보</h2>
                <p>
                  {rows.length}개 시험 · {pareto.length}개 비지배 조합
                </p>
              </div>
            </div>
            <div className="panel-body">
              <ReplayParetoChart
                rows={rows}
                paretoIndexes={new Set(pareto.map((row) => row.index))}
              />
            </div>
          </section>
          <section className="section-panel">
            <div className="data-table-wrap">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>조합</th>
                    <th>학습 CAGR</th>
                    <th>학습 MDD</th>
                    <th>검증 CAGR</th>
                    <th>거래</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {pareto.slice(0, 12).map((row) => (
                    <tr key={row.index}>
                      <td>#{row.index + 1}</td>
                      <td>{formatPercent(row.training.cagr)}</td>
                      <td>{formatPercent(row.training.max_drawdown)}</td>
                      <td>{formatPercent(row.validation.cagr)}</td>
                      <td>{row.trade_count}회</td>
                      <td className="pareto-actions">
                        <Link
                          className="button button-ghost"
                          href={`/replays/new?experiment_id=${experiment.experiment_id}&sweep_run_id=${sweep?.run_id}&candidate_index=${row.index}`}
                        >
                          후보 편집
                        </Link>
                        <Button
                          variant="secondary"
                          disabled={selectedIndex !== null}
                          onClick={() => addCandidate(row)}
                        >
                          {selectedIndex === row.index ? (
                            <LoaderCircle size={15} className="spin" />
                          ) : (
                            <Beaker size={15} />
                          )}
                          도전 전략 추가
                        </Button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        </>
      )}
    </div>
  );
}

function SweepDiagnosticsPanel({
  diagnostics,
  rows,
}: {
  diagnostics?: SweepDiagnostics;
  rows: SweepRow[];
}) {
  if (!diagnostics) return null;
  const training = [...rows].sort(
    (left, right) => right.training.cagr - left.training.cagr,
  );
  const validation = [...rows].sort(
    (left, right) => right.validation.cagr - left.validation.cagr,
  );
  const validationRanks = new Map(
    validation.map((row, index) => [row.index, index + 1]),
  );
  return (
    <section className="section-panel">
      <div className="panel-header">
        <div>
          <h2>과최적화 점검</h2>
          <p>
            학습에서 좋았던 조건이 검증에서도 비슷한 순위를 유지했는지 봅니다.
          </p>
        </div>
      </div>
      <div className="panel-body">
        <div className="compact-metric-grid sweep-diagnostic-grid">
          <Metric
            label="시험 조합"
            value={`${diagnostics.valid_trial_count ?? rows.length}/${diagnostics.trial_count ?? rows.length}개 유효`}
          />
          <Metric
            label="학습·검증 순위 상관"
            value={formatNumber(diagnostics.train_validation_spearman ?? 0)}
          />
          <Metric
            label="상위 10% 재현"
            value={`${diagnostics.top_decile_overlap ?? 0}/${diagnostics.top_decile_size ?? 0}`}
          />
          <Metric
            label="최고 학습 조합의 검증 순위"
            value={`${diagnostics.best_train_validation_rank ?? 0}위`}
          />
          <Metric
            label="거래 수 범위"
            value={`${diagnostics.trade_count?.minimum ?? 0}–${diagnostics.trade_count?.maximum ?? 0}회`}
          />
          <Metric
            label="상위 3거래 최대 집중"
            value={formatPercent(diagnostics.maximum_top_3_profit_ratio ?? 0)}
          />
        </div>
        {(diagnostics.warnings?.length ?? 0) > 0 && (
          <div className="sweep-warning-list">
            {diagnostics.warnings?.map((warning) => (
              <div key={warning}>
                <AlertTriangle size={15} />
                <span>{warning}</span>
              </div>
            ))}
          </div>
        )}
        <div className="data-table-wrap rank-shift-table">
          <table className="data-table">
            <thead>
              <tr>
                <th>학습 순위</th>
                <th>조합</th>
                <th>학습 CAGR</th>
                <th>검증 순위</th>
                <th>검증 CAGR</th>
              </tr>
            </thead>
            <tbody>
              {training.slice(0, 10).map((row, index) => (
                <tr key={row.index}>
                  <td>{index + 1}위</td>
                  <td>#{row.index + 1}</td>
                  <td>{formatPercent(row.training.cagr)}</td>
                  <td>{validationRanks.get(row.index)}위</td>
                  <td>{formatPercent(row.validation.cagr)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </section>
  );
}

function SweepHeatmap({
  rows,
  axes,
}: {
  rows: SweepRow[];
  axes: Array<{ path: string; values: unknown[] }>;
}) {
  if (axes.length === 0) return <p>민감도 축 정보가 없습니다.</p>;
  const first = axes[0];
  const second = axes[1] ?? { path: "single", values: ["결과"] };
  const byCoordinate = new Map(
    rows.map((row) => [
      JSON.stringify([
        nestedValue(row.strategy, first.path),
        axes[1] ? nestedValue(row.strategy, second.path) : "결과",
      ]),
      row,
    ]),
  );
  const returns = rows.map((row) => row.validation.cagr);
  const minimum = Math.min(...returns);
  const maximum = Math.max(...returns);
  const spread = Math.max(maximum - minimum, 0.0001);
  return (
    <div className="sweep-heatmap-wrap">
      <table className="sweep-heatmap">
        <thead>
          <tr>
            <th>
              {SWEEP_AXES.find((item) => item.path === second.path)?.label ??
                "결과"}
            </th>
            {first.values.map((value) => (
              <th key={JSON.stringify(value)}>
                {formatSweepAxisValue(first.path, value)}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {second.values.map((secondValue) => (
            <tr key={JSON.stringify(secondValue)}>
              <th>{formatSweepAxisValue(second.path, secondValue)}</th>
              {first.values.map((firstValue) => {
                const row = byCoordinate.get(
                  JSON.stringify([firstValue, secondValue]),
                );
                const value = row?.validation.cagr;
                const strength =
                  value == null
                    ? 0
                    : 0.12 + ((value - minimum) / spread) * 0.58;
                return (
                  <td
                    key={JSON.stringify(firstValue)}
                    style={{
                      backgroundColor:
                        value == null
                          ? undefined
                          : value >= 0
                            ? `rgba(23, 107, 72, ${strength})`
                            : `rgba(162, 58, 58, ${strength})`,
                      color: strength >= 0.5 ? "#fff" : undefined,
                    }}
                    title={row ? `조합 #${row.index + 1}` : undefined}
                  >
                    {value == null ? "-" : formatPercent(value)}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function formatSweepAxisValue(path: string, value: unknown) {
  if (value == null) return "끔";
  if (
    path.startsWith("signal.component_weights_bps.") ||
    path.startsWith("portfolio.sleeve_weights_bps.")
  )
    return `${Number(value) / 100}%`;
  if (path.startsWith("risk.")) return `${Number(value) * 100}%`;
  if (path.includes("_cost")) return `${Number(value) * 100}%`;
  if (typeof value === "boolean") return value ? "사용" : "끔";
  return String(value);
}

function TradesTab({
  runs,
  comparison,
}: {
  runs: ExperimentRun[];
  comparison: ReplayComparison;
}) {
  const compared = new Map(
    (comparison.runs ?? []).map((item) => [String(item.run_id), item]),
  );
  return (
    <section className="section-panel">
      <div className="data-table-wrap">
        <table className="data-table">
          <thead>
            <tr>
              <th>전략</th>
              <th>분류</th>
              <th>거래 수</th>
              <th>승률</th>
              <th>비용</th>
              <th>상세</th>
            </tr>
          </thead>
          <tbody>
            {runs.map((run) => {
              const item = compared.get(run.run_id) as
                | { classification?: string; cost_krw?: number }
                | undefined;
              const overall =
                run.result?.analysis?.trade_analysis?.overall ?? {};
              return (
                <tr key={run.run_id}>
                  <td>
                    <strong>{run.label}</strong>
                  </td>
                  <td>{item?.classification ?? "-"}</td>
                  <td>{Math.round(run.result?.metrics?.trade_count ?? 0)}회</td>
                  <td>{formatPercent(overall.win_rate ?? 0)}</td>
                  <td>{formatKrw(item?.cost_krw ?? 0)}</td>
                  <td>
                    <Link
                      href={`/replays/${run.run_id}`}
                      className="icon-button table-action"
                      aria-label="거래 상세"
                    >
                      <ArrowRight size={16} />
                    </Link>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function IntegrityTab({ runs }: { runs: ExperimentRun[] }) {
  return (
    <div className="analysis-stack">
      {runs.map((run) => (
        <section className="section-panel" key={run.run_id}>
          <div className="panel-header">
            <div>
              <h2>{run.label}</h2>
              <p>{run.result?.strategy_config?.version ?? "이전 전략 설정"}</p>
            </div>
          </div>
          <div className="integrity-list">
            {(run.result?.analysis?.integrity_checks ?? []).map((check) => (
              <div key={check.code}>
                <span
                  className={cn(
                    "integrity-icon",
                    check.status === "PASS"
                      ? "integrity-pass"
                      : "integrity-fail",
                  )}
                >
                  {check.status === "PASS" ? (
                    <CheckCircle2 size={15} />
                  ) : (
                    <XCircle size={15} />
                  )}
                </span>
                <div>
                  <strong>{check.label}</strong>
                  <p>{check.detail}</p>
                </div>
              </div>
            ))}
          </div>
          <dl className="integrity-version-grid">
            <div>
              <dt>데이터</dt>
              <dd>{run.result?.data_version ?? "-"}</dd>
            </div>
            <div>
              <dt>점수</dt>
              <dd>{run.result?.score_version ?? "-"}</dd>
            </div>
            <div>
              <dt>포트폴리오</dt>
              <dd>{run.result?.portfolio_version ?? "-"}</dd>
            </div>
            <div>
              <dt>분석</dt>
              <dd>{run.result?.analysis?.version ?? "-"}</dd>
            </div>
          </dl>
          {(run.result?.warnings?.length ?? 0) > 0 && (
            <div className="integrity-warning-list">
              {run.result?.warnings?.map((warning) => (
                <p key={warning}>{warning}</p>
              ))}
            </div>
          )}
        </section>
      ))}
    </div>
  );
}

function SweepAxis({
  value,
  onChange,
  values,
  onValues,
}: {
  value: string;
  onChange: (value: string) => void;
  values: string;
  onValues: (value: string) => void;
}) {
  return (
    <div className="sweep-axis">
      <label className="field">
        <span>조건 축</span>
        <select
          value={value}
          onChange={(event) => onChange(event.target.value)}
        >
          {SWEEP_AXES.map((item) => (
            <option value={item.path} key={item.path}>
              {item.label}
            </option>
          ))}
        </select>
      </label>
      <label className="field">
        <span>시험 값</span>
        <input
          value={values}
          onChange={(event) => onValues(event.target.value)}
          placeholder={
            SWEEP_AXES.find((item) => item.path === value)?.defaults ??
            "값1,값2"
          }
        />
        <small>쉼표로 구분 · 비중과 비용은 % · 손절을 끄려면 off</small>
      </label>
    </div>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}
function ComparisonValue({
  value,
  difference,
}: {
  value: string;
  difference?: string;
}) {
  return (
    <td>
      <strong>{value}</strong>
      {difference && (
        <small className="comparison-difference">{difference}</small>
      )}
    </td>
  );
}
function formatSigned(value: number, suffix = "", digits = 2) {
  const prefix = value > 0 ? "+" : "";
  return `${prefix}${value.toLocaleString("ko-KR", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })}${suffix}`;
}
function formatSignedKrw(value: number) {
  return `${value > 0 ? "+" : ""}${Math.round(value).toLocaleString("ko-KR")}원`;
}
function ValidationMetric({
  title,
  metrics,
}: {
  title: string;
  metrics?: Record<string, number>;
}) {
  return (
    <div className="validation-cell">
      <span>{title}</span>
      <strong>{formatPercent(metrics?.cagr ?? 0)}</strong>
      <small>MDD {formatPercent(metrics?.max_drawdown ?? 0)}</small>
    </div>
  );
}
function EmptyResult() {
  return (
    <section className="section-panel empty-state">
      <LoaderCircle size={24} />
      <strong>완료된 실행을 기다리고 있습니다.</strong>
    </section>
  );
}
