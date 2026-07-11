import { AlertTriangle, CheckCircle2, FlaskConical, Info } from "lucide-react";
import Link from "next/link";

import { PEER_LABELS } from "@/components/candidate-table";
import { LineChart } from "@/components/line-chart";
import {
  GapWaterfallChart,
  MonthlyReturnHeatmap,
} from "@/components/replay-analysis-charts";
import type { ReplayAnalysis, ReplayResponse, ReplayRoundTrip } from "@/lib/api";
import { formatKrw, formatNumber, formatPercent } from "@/lib/utils";

type ReplayResult = NonNullable<ReplayResponse["result"]>;

const SLEEVE_LABELS: Record<string, string> = {
  US_STOCK: "미국 주식",
  KR_STOCK: "한국 주식",
  US_ETF: "미국 ETF",
  KR_ETF: "한국 ETF",
};

const EXIT_REASON_LABELS: Record<string, string> = {
  EXIT_RULE: "점수·필수 조건 해제",
  DELISTED_RECOVERY: "상장폐지 회수",
  OPEN: "현재 보유",
};

function signedPercent(value: number, digits = 1) {
  return `${value >= 0 ? "+" : ""}${(value * 100).toFixed(digits)}%p`;
}

export function LegacyAnalysisNotice() {
  return (
    <section className="section-panel empty-state analysis-empty">
      <FlaskConical size={28} aria-hidden="true" />
      <h2>이 실행에는 원인 분석 정보가 없습니다</h2>
      <p>분석 엔진 적용 전에 생성된 결과입니다.</p>
      <Link href="/replays" className="button button-primary">
        새 기준으로 다시 실행
      </Link>
    </section>
  );
}

export function ReplayCauseAnalysis({
  result,
  analysis,
}: {
  result: ReplayResult;
  analysis: ReplayAnalysis;
}) {
  const gap = analysis.gap_analysis;
  return (
    <div className="analysis-stack">
      <section className="analysis-headline" aria-labelledby="analysis-headline-title">
        <Info size={18} aria-hidden="true" />
        <div>
          <h2 id="analysis-headline-title">{analysis.headline.title}</h2>
          <p>{analysis.headline.summary}</p>
        </div>
      </section>

      <section className="metric-strip analysis-metrics">
        <div className="metric-cell">
          <span className="metric-label">벤치마크 누적수익</span>
          <span className="metric-value">{formatPercent(gap.full_benchmark_return)}</span>
        </div>
        <div className="metric-cell">
          <span className="metric-label">실제 전략 누적수익</span>
          <span className="metric-value">{formatPercent(gap.actual_strategy_return)}</span>
        </div>
        <div className="metric-cell">
          <span className="metric-label" title="현금 비중과 200일선 진입 제한의 합산 효과">
            노출·진입 제한 효과
          </span>
          <span className="metric-value">{signedPercent(gap.exposure_effect)}</span>
        </div>
        <div className="metric-cell">
          <span className="metric-label" title="비용이 없다고 가정했을 때 전략과 노출도 보정 벤치마크의 차이">
            종목 선택·체결 효과
          </span>
          <span className="metric-value">
            {signedPercent(gap.selection_execution_effect)}
          </span>
        </div>
      </section>

      <div className="analysis-two-column">
        <section className="section-panel">
          <div className="panel-header">
            <div>
              <h2>성과 차이 분해</h2>
              <p>각 단계의 합이 실제 전략 수익률과 연결됩니다.</p>
            </div>
          </div>
          <div className="panel-body">
            <GapWaterfallChart gap={gap} />
          </div>
        </section>
        <section className="section-panel">
          <div className="panel-header">
            <div>
              <h2>비용 영향</h2>
              <p>같은 신호를 비용 없이 재생한 비교 결과</p>
            </div>
          </div>
          <dl className="analysis-definition-list">
            <div>
              <dt>최초 환전 비용</dt>
              <dd>{formatKrw(analysis.cost_summary.initial_fx_cost_krw)}</dd>
            </div>
            <div>
              <dt>누적 매매 비용</dt>
              <dd>{formatKrw(analysis.cost_summary.trade_cost_krw)}</dd>
            </div>
            <div>
              <dt>복리 기준 비용 차이</dt>
              <dd>{formatKrw(analysis.cost_summary.compounded_cost_drag_krw)}</dd>
            </div>
            <div>
              <dt>누적수익률 영향</dt>
              <dd>{signedPercent(-analysis.cost_summary.cost_drag)}</dd>
            </div>
          </dl>
        </section>
      </div>

      <section className="section-panel">
        <div className="panel-header">
          <div>
            <h2>비교 성과 곡선</h2>
            <p>현금 효과와 비용 효과를 같은 출발점에서 비교합니다.</p>
          </div>
        </div>
        <div className="panel-body">
          <LineChart
            labels={result.equity_curve.map((point) => point.date)}
            series={[
              {
                name: "실제 전략",
                values: result.equity_curve.map((point) => point.portfolio),
                color: "#176b48",
              },
              {
                name: "완전투자 벤치마크",
                values: result.equity_curve.map((point) => point.benchmark),
                color: "#23699a",
              },
              {
                name: "노출도 보정",
                values: result.equity_curve.map(
                  (point) => point.exposure_matched_benchmark ?? null,
                ),
                color: "#96630d",
              },
              {
                name: "비용 없는 전략",
                values: result.equity_curve.map(
                  (point) => point.no_cost_portfolio ?? null,
                ),
                color: "#7a5a96",
              },
            ]}
            valueFormat="krw-millions"
          />
        </div>
      </section>

      <div className="analysis-two-column analysis-period-grid">
        <section className="section-panel">
          <div className="panel-header">
            <div>
              <h2>연도별 성과</h2>
              <p>벤치마크와 격차가 커진 시기를 확인합니다.</p>
            </div>
          </div>
          <div className="data-table-wrap">
            <table className="data-table analysis-period-table">
              <thead>
                <tr>
                  <th>연도</th>
                  <th>전략</th>
                  <th>벤치마크</th>
                  <th>차이</th>
                  <th>노출도</th>
                  <th>낙폭</th>
                </tr>
              </thead>
              <tbody>
                {analysis.annual_periods.map((row) => (
                  <tr key={row.period}>
                    <td><strong>{row.period}</strong></td>
                    <td>{formatPercent(row.strategy_return)}</td>
                    <td>{formatPercent(row.benchmark_return)}</td>
                    <td className={row.excess_return >= 0 ? "positive-value" : "negative-value"}>
                      {signedPercent(row.excess_return)}
                    </td>
                    <td>{formatPercent(row.average_exposure ?? 0)}</td>
                    <td>{formatPercent(row.max_drawdown ?? 0)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
        <section className="section-panel">
          <div className="panel-header">
            <div>
              <h2>월별 수익률</h2>
              <p>월간 전략 수익률의 분포</p>
            </div>
          </div>
          <div className="panel-body heatmap-wrap">
            <MonthlyReturnHeatmap periods={analysis.monthly_periods} />
          </div>
        </section>
      </div>

      <div className="analysis-two-column">
        <section className="section-panel">
          <div className="panel-header">
            <div>
              <h2>자산군 기여</h2>
              <p>최초 배정액 대비 최종 손익</p>
            </div>
          </div>
          <div className="data-table-wrap">
            <table className="data-table analysis-compact-table">
              <thead>
                <tr>
                  <th>자산군</th>
                  <th>최종 가치</th>
                  <th>손익</th>
                  <th>전체 기여</th>
                  <th>평균 노출</th>
                </tr>
              </thead>
              <tbody>
                {analysis.sleeve_attribution.map((row) => (
                  <tr key={row.sleeve}>
                    <td><strong>{SLEEVE_LABELS[row.sleeve] ?? row.sleeve}</strong></td>
                    <td>{formatKrw(row.ending_value_krw)}</td>
                    <td className={row.pnl_krw >= 0 ? "positive-value" : "negative-value"}>
                      {formatKrw(row.pnl_krw)}
                    </td>
                    <td>{signedPercent(row.contribution)}</td>
                    <td>{formatPercent(row.average_exposure)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
        <section className="section-panel">
          <div className="panel-header">
            <div>
              <h2>시장 진입 상태</h2>
              <p>비교군 벤치마크 200일선 기준</p>
            </div>
          </div>
          <div className="data-table-wrap">
            <table className="data-table analysis-compact-table">
              <thead>
                <tr>
                  <th>비교군</th>
                  <th>진입 허용</th>
                  <th>차단 주</th>
                  <th>평균 후보</th>
                  <th>매수</th>
                </tr>
              </thead>
              <tbody>
                {analysis.market_regimes.map((row) => (
                  <tr key={row.peer_group}>
                    <td><strong>{PEER_LABELS[row.peer_group] ?? row.peer_group}</strong></td>
                    <td>{formatPercent(row.entry_allowed_rate, 0)}</td>
                    <td>{row.entry_blocked_count.toLocaleString()}주</td>
                    <td>{formatNumber(row.average_candidate_count, 1)}개</td>
                    <td>{row.planned_buy_count.toLocaleString()}회</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      </div>
    </div>
  );
}

function TradeTable({
  title,
  items,
}: {
  title: string;
  items: ReplayRoundTrip[];
}) {
  return (
    <section className="section-panel">
      <div className="panel-header">
        <div>
          <h2>{title}</h2>
          <p>배당·환율·매매 비용을 포함한 원화 기준</p>
        </div>
      </div>
      <div className="data-table-wrap">
        <table className="data-table analysis-trade-table">
          <thead>
            <tr>
              <th>종목</th>
              <th>자산군</th>
              <th>기간</th>
              <th>진입 점수</th>
              <th>순손익</th>
              <th>수익률</th>
            </tr>
          </thead>
          <tbody>
            {items.map((item) => (
              <tr key={`${title}-${item.asset_id}-${item.entry_date}`}>
                <td className="symbol-cell">
                  <strong>{item.symbol}</strong>
                  <span>{item.name}</span>
                </td>
                <td>{SLEEVE_LABELS[item.sleeve] ?? item.sleeve}</td>
                <td>{item.holding_days.toLocaleString()}일</td>
                <td>{item.entry_score.toFixed(1)}</td>
                <td className={item.net_pnl_krw >= 0 ? "positive-value" : "negative-value"}>
                  {formatKrw(item.net_pnl_krw)}
                </td>
                <td>{formatPercent(item.net_return)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

export function ReplayTradeQuality({ analysis }: { analysis: ReplayAnalysis }) {
  const trade = analysis.trade_analysis;
  return (
    <div className="analysis-stack">
      <section className="metric-strip analysis-trade-metrics">
        <div className="metric-cell">
          <span className="metric-label">완료 거래 회차</span>
          <span className="metric-value">{trade.overall.closed_count.toLocaleString()}회</span>
        </div>
        <div className="metric-cell">
          <span className="metric-label">승률</span>
          <span className="metric-value">{formatPercent(trade.overall.win_rate)}</span>
        </div>
        <div className="metric-cell">
          <span className="metric-label" title="평균 이익률을 평균 손실률 절댓값으로 나눈 값">
            손익비
          </span>
          <span className="metric-value">{formatNumber(trade.overall.payoff_ratio)}</span>
        </div>
        <div className="metric-cell">
          <span className="metric-label" title="총이익을 총손실 절댓값으로 나눈 값">
            Profit Factor
          </span>
          <span className="metric-value">{formatNumber(trade.overall.profit_factor)}</span>
        </div>
        <div className="metric-cell">
          <span className="metric-label">중앙 보유 기간</span>
          <span className="metric-value">{formatNumber(trade.overall.median_holding_days, 0)}일</span>
        </div>
      </section>

      <div className="analysis-two-column">
        <section className="section-panel">
          <div className="panel-header">
            <div>
              <h2>진입 점수 구간</h2>
              <p>완료된 거래 회차 기준</p>
            </div>
          </div>
          <div className="data-table-wrap">
            <table className="data-table analysis-compact-table">
              <thead><tr><th>점수</th><th>거래</th><th>승률</th><th>평균 이익</th><th>평균 손실</th></tr></thead>
              <tbody>
                {trade.by_entry_score.map((row) => (
                  <tr key={row.band}>
                    <td><strong>{row.band}점</strong></td>
                    <td>{row.closed_count}회</td>
                    <td>{formatPercent(row.win_rate)}</td>
                    <td>{formatPercent(row.average_gain)}</td>
                    <td>{formatPercent(row.average_loss)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
        <section className="section-panel">
          <div className="panel-header">
            <div>
              <h2>청산 사유</h2>
              <p>실제 매도 또는 회수 완료 기준</p>
            </div>
          </div>
          <div className="data-table-wrap">
            <table className="data-table analysis-compact-table">
              <thead><tr><th>사유</th><th>거래</th><th>승률</th><th>순손익</th></tr></thead>
              <tbody>
                {trade.by_exit_reason.map((row) => (
                  <tr key={row.reason}>
                    <td><strong>{EXIT_REASON_LABELS[row.reason ?? ""] ?? row.reason}</strong></td>
                    <td>{row.closed_count}회</td>
                    <td>{formatPercent(row.win_rate)}</td>
                    <td className={row.net_pnl_krw >= 0 ? "positive-value" : "negative-value"}>
                      {formatKrw(row.net_pnl_krw)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      </div>

      <div className="analysis-two-column">
        <TradeTable title="수익 기여 상위 거래" items={trade.best_trades} />
        <TradeTable title="손실 기여 상위 거래" items={trade.worst_trades} />
      </div>
    </div>
  );
}

export function ReplayIntegrity({
  result,
  analysis,
}: {
  result: ReplayResult;
  analysis: ReplayAnalysis;
}) {
  return (
    <div className="analysis-stack">
      <section className="section-panel">
        <div className="panel-header">
          <div>
            <h2>재생 무결성 검사</h2>
            <p>실패 항목이 있으면 실행 결과를 성공으로 저장하지 않습니다.</p>
          </div>
        </div>
        <div className="integrity-list">
          {analysis.integrity_checks.map((check) => (
            <div className="integrity-row" key={check.code}>
              <CheckCircle2 size={18} aria-hidden="true" />
              <div>
                <strong>{check.label}</strong>
                <span>{check.detail}</span>
              </div>
              <span className="integrity-pass">통과</span>
            </div>
          ))}
        </div>
      </section>

      <div className="analysis-two-column">
        <section className="section-panel">
          <div className="panel-header"><div><h2>실행 버전</h2><p>결과 재현에 사용된 계약</p></div></div>
          <dl className="analysis-definition-list">
            <div><dt>데이터</dt><dd title={result.data_version}>{result.data_version}</dd></div>
            <div><dt>점수</dt><dd>{result.score_version}</dd></div>
            <div><dt>포트폴리오</dt><dd>{result.portfolio_version}</dd></div>
            <div><dt>원인 분석</dt><dd>{analysis.version}</dd></div>
          </dl>
        </section>
        <section className="section-panel">
          <div className="panel-header"><div><h2>해석 시 주의사항</h2><p>결과에 남아 있는 구조적 한계</p></div></div>
          <div className="analysis-warning-list">
            {(result.warnings ?? []).map((warning) => (
              <div key={warning}><AlertTriangle size={15} aria-hidden="true" /><span>{warning}</span></div>
            ))}
          </div>
        </section>
      </div>
    </div>
  );
}
