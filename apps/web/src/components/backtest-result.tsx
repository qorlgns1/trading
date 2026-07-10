"use client";

import { Download, LoaderCircle, RotateCcw } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { LineChart } from "@/components/line-chart";
import { PEER_LABELS } from "@/components/candidate-table";
import { StatusBadge } from "@/components/ui/status-badge";
import { apiFetch, type Artifact, type BacktestResponse } from "@/lib/api";
import { formatKrw, formatNumber, formatPercent } from "@/lib/utils";

type EquityPoint = { date: string; portfolio: number; benchmark: number };
type DrawdownPoint = { date: string; drawdown: number };
type Position = {
  asset_id: string;
  symbol: string;
  name: string;
  peer_group: string;
  quantity: number;
  market_value_krw: number;
  score: number;
};
type Summary = {
  data_version: string;
  score_version: string;
  portfolio_version: string;
  started_on: string;
  ended_on: string;
  metrics: Record<string, number>;
  equity_curve: EquityPoint[];
  drawdown_curve: DrawdownPoint[];
  final_positions: Position[];
  warnings: string[];
};

const METRICS: Array<{ key: string; label: string; format: (value: number) => string }> = [
  { key: "cagr", label: "연환산 수익률", format: (value) => formatPercent(value) },
  { key: "annual_volatility", label: "연환산 변동성", format: (value) => formatPercent(value) },
  { key: "max_drawdown", label: "최대 낙폭", format: (value) => formatPercent(value) },
  { key: "sharpe", label: "Sharpe", format: (value) => formatNumber(value) },
  { key: "sortino", label: "Sortino", format: (value) => formatNumber(value) },
  { key: "turnover", label: "누적 회전율", format: (value) => formatNumber(value) },
  { key: "trade_count", label: "거래 수", format: (value) => `${value.toFixed(0)}회` },
  { key: "final_value_krw", label: "최종 평가액", format: (value) => formatKrw(value) },
];

export function BacktestResultView({ runId }: { runId: string }) {
  const [run, setRun] = useState<BacktestResponse | null>(null);
  const [artifacts, setArtifacts] = useState<Artifact[]>([]);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const next = await apiFetch<BacktestResponse>(`/backtests/${runId}`);
      setRun(next);
      if (next.status === "SUCCEEDED") {
        setArtifacts(await apiFetch<Artifact[]>(`/backtests/${runId}/artifacts`));
      }
      if (next.status === "FAILED") setError(next.error_message ?? "백테스트가 실패했습니다.");
      return next.status;
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "결과를 불러오지 못했습니다.");
      return "FAILED";
    }
  }, [runId]);

  useEffect(() => {
    let active = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const poll = async () => {
      const status = await load();
      if (active && ["QUEUED", "RUNNING"].includes(status)) timer = setTimeout(poll, 1200);
    };
    void poll();
    return () => {
      active = false;
      if (timer) clearTimeout(timer);
    };
  }, [load]);

  if (!run || ["QUEUED", "RUNNING"].includes(run.status)) {
    return (
      <section className="section-panel empty-state" aria-live="polite">
        <LoaderCircle size={30} className="spin" aria-hidden="true" />
        <h1>백테스트를 계산하고 있습니다</h1>
        <p>10년 일봉과 주간 후보 규칙을 순서대로 처리합니다.</p>
        <StatusBadge state={run?.status ?? "QUEUED"} />
      </section>
    );
  }

  if (error || run.status === "FAILED" || !run.result) {
    return (
      <section className="section-panel empty-state">
        <h1>백테스트를 완료하지 못했습니다</h1>
        <p>{error ?? run.error_message}</p>
        <Link href="/backtests" className="button button-secondary"><RotateCcw size={16} /> 설정으로 돌아가기</Link>
      </section>
    );
  }

  const result = run.result as unknown as Summary;
  return (
    <>
      <header className="page-header">
        <div>
          <div style={{ marginBottom: 7 }}><StatusBadge state={run.status} /></div>
          <h1>백테스트 결과</h1>
          <p>{result.started_on}부터 {result.ended_on}까지 고정 규칙으로 계산했습니다.</p>
        </div>
        <div className="page-meta"><span className="demo-chip">가상 데이터</span><span>{result.portfolio_version}</span></div>
      </header>

      <section className="metric-strip backtest-metrics">
        {METRICS.map((metric) => (
          <div className="metric-cell" key={metric.key}>
            <span className="metric-label">{metric.label}</span>
            <span className="metric-value">{metric.format(result.metrics[metric.key] ?? 0)}</span>
          </div>
        ))}
      </section>

      <section className="section-panel" style={{ marginTop: 20 }}>
        <div className="panel-header"><div><h2>포트폴리오 가치</h2><p>동일 자산군 비중 합성 벤치마크 비교</p></div></div>
        <div className="panel-body">
          <LineChart
            labels={result.equity_curve.map((point) => point.date)}
            series={[
              { name: "포트폴리오", values: result.equity_curve.map((point) => point.portfolio), color: "#176b48", area: true },
              { name: "합성 벤치마크", values: result.equity_curve.map((point) => point.benchmark), color: "#23699a" },
            ]}
            valueFormat="krw-millions"
          />
        </div>
      </section>

      <div className="section-grid">
        <section className="section-panel">
          <div className="panel-header"><div><h2>낙폭</h2><p>이전 최고점 대비 하락률</p></div></div>
          <div className="panel-body">
            <LineChart compact labels={result.drawdown_curve.map((point) => point.date)} series={[{ name: "낙폭", values: result.drawdown_curve.map((point) => point.drawdown), color: "#a23a3a", area: true }]} valueFormat="percent" />
          </div>
        </section>
        <section className="section-panel">
          <div className="panel-header"><div><h2>결과 파일</h2><p>생성 후 7일 보관</p></div></div>
          <div className="panel-body" style={{ display: "grid", gap: 9 }}>
            {artifacts.map((artifact) => (
              <a key={artifact.name} href={artifact.download_url} className="button button-secondary" target="_blank" rel="noreferrer">
                <Download size={15} /> {artifact.name} <span style={{ color: "var(--text-muted)", fontWeight: 500 }}>{Math.ceil(artifact.size_bytes / 1024)}KB</span>
              </a>
            ))}
          </div>
        </section>
      </div>

      <section className="section-panel" style={{ marginTop: 20 }}>
        <div className="panel-header"><div><h2>최종 보유 종목</h2><p>종료일 종가 평가</p></div></div>
        <div className="data-table-wrap">
          <table className="data-table">
            <thead><tr><th>종목</th><th>비교군</th><th>수량</th><th>평가액</th><th>점수</th></tr></thead>
            <tbody>{result.final_positions.map((position) => (
              <tr key={position.asset_id}><td className="symbol-cell"><strong>{position.symbol}</strong><span>{position.name}</span></td><td>{PEER_LABELS[position.peer_group] ?? position.peer_group}</td><td>{position.quantity.toLocaleString()}</td><td>{formatKrw(position.market_value_krw)}</td><td className="score-number">{position.score.toFixed(1)}</td></tr>
            ))}</tbody>
          </table>
        </div>
      </section>
      <div className="notice-box warning-box" style={{ marginTop: 16 }}>{result.warnings.join(" ")}</div>
    </>
  );
}
