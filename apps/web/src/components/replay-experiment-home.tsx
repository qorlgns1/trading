import { ArrowRight, Beaker, CheckCircle2, Clock3, Plus } from "lucide-react";
import Link from "next/link";

import type { ReplayExperimentList, ReplayOptions } from "@/lib/api";

const OBJECTIVE_LABELS: Record<string, string> = {
  RETURN: "수익 증가",
  DRAWDOWN: "낙폭 감소",
  COST: "비용 감소",
  BALANCED: "균형",
};

export function ReplayExperimentHome({
  experiments,
  options,
}: {
  experiments: ReplayExperimentList;
  options: ReplayOptions;
}) {
  const running = experiments.items.filter(
    (item) => item.status !== "ARCHIVED",
  ).length;
  const completedRuns = experiments.items.reduce(
    (total, item) => total + item.run_count,
    0,
  );
  return (
    <>
      <header className="page-header">
        <div>
          <h1>전략 실험실</h1>
          <p>기준 전략과 변경 전략을 같은 데이터·기간에서 비교합니다.</p>
        </div>
        <div className="page-actions">
          <span className="demo-chip real-data-chip">로컬 실데이터</span>
          <Link className="button button-primary" href="/replays/new">
            <Plus size={16} /> 새 실험
          </Link>
        </div>
      </header>

      <section className="metric-strip experiment-metrics">
        <div className="metric-cell">
          <span className="metric-label">저장된 실험</span>
          <span className="metric-value">{experiments.total}개</span>
          <span className="metric-note">
            <Beaker size={13} /> 비교 단위
          </span>
        </div>
        <div className="metric-cell">
          <span className="metric-label">활성 실험</span>
          <span className="metric-value">{running}개</span>
          <span className="metric-note">
            <Clock3 size={13} /> 보관 제외
          </span>
        </div>
        <div className="metric-cell">
          <span className="metric-label">연결된 실행</span>
          <span className="metric-value">{completedRuns}개</span>
          <span className="metric-note">
            <CheckCircle2 size={13} /> 캐시 포함
          </span>
        </div>
        <div className="metric-cell">
          <span className="metric-label">활성 데이터</span>
          <span className="metric-value metric-value-compact">
            {options.raw_history_start.slice(0, 4)}–
            {options.raw_history_end.slice(0, 4)}
          </span>
          <span className="metric-note">현재 상장 종목 기준</span>
        </div>
      </section>

      <section className="section-panel experiment-list-panel">
        <div className="panel-header">
          <div>
            <h2>실험 기록</h2>
            <p>가설, 기준 전략, 도전 전략과 민감도 실행을 함께 보관합니다.</p>
          </div>
        </div>
        {experiments.items.length === 0 ? (
          <div className="empty-state compact-empty-state">
            <Beaker size={26} />
            <strong>아직 저장된 실험이 없습니다.</strong>
            <Link className="button button-secondary" href="/replays/new">
              첫 실험 만들기 <ArrowRight size={16} />
            </Link>
          </div>
        ) : (
          <div className="data-table-wrap">
            <table className="data-table experiment-table">
              <thead>
                <tr>
                  <th>실험</th>
                  <th>목적</th>
                  <th>전략 수</th>
                  <th>상태</th>
                  <th>생성일</th>
                  <th aria-label="열기" />
                </tr>
              </thead>
              <tbody>
                {experiments.items.map((item) => (
                  <tr key={item.experiment_id}>
                    <td className="symbol-cell">
                      <strong>{item.name}</strong>
                      <span>{item.hypothesis}</span>
                    </td>
                    <td>
                      {OBJECTIVE_LABELS[item.objective] ?? item.objective}
                    </td>
                    <td>{item.run_count}개</td>
                    <td>
                      <span className="status-pill status-neutral">
                        {item.status}
                      </span>
                    </td>
                    <td>
                      {new Date(item.created_at).toLocaleDateString("ko-KR")}
                    </td>
                    <td>
                      <Link
                        className="icon-button table-action"
                        href={`/replays/experiments/${item.experiment_id}`}
                        aria-label={`${item.name} 열기`}
                        title="실험 열기"
                      >
                        <ArrowRight size={17} />
                      </Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <div className="notice-box warning-box experiment-bias-notice">
        현재 상장된 종목만 사용하므로 모든 결과에 생존편향이 포함됩니다. 투자
        추천 또는 상승 확률이 아닙니다.
      </div>
    </>
  );
}
