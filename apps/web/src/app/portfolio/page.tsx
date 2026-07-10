import type { Metadata } from "next";

import { PEER_LABELS } from "@/components/candidate-table";
import { apiFetch, type PaperPortfolio } from "@/lib/api";
import { formatKrw, formatPercent } from "@/lib/utils";

export const metadata: Metadata = { title: "모의 포트폴리오" };

export default async function PortfolioPage() {
  const portfolio = await apiFetch<PaperPortfolio>("/paper-portfolio");
  const exposure = portfolio.invested_krw / portfolio.initial_capital_krw;
  return (
    <>
      <header className="page-header">
        <div><h1>모의 포트폴리오</h1><p>기본 자산군 배분과 최신 공식 후보를 적용한 읽기 전용 원장입니다.</p></div>
        <div className="page-meta"><span className="demo-chip">가상 데이터</span><span>{portfolio.as_of}</span></div>
      </header>
      <section className="metric-strip">
        <div className="metric-cell"><span className="metric-label">기준 자금</span><span className="metric-value">{formatKrw(portfolio.initial_capital_krw)}</span></div>
        <div className="metric-cell"><span className="metric-label">투자 금액</span><span className="metric-value">{formatKrw(portfolio.invested_krw)}</span></div>
        <div className="metric-cell"><span className="metric-label">현금</span><span className="metric-value">{formatKrw(portfolio.cash_krw)}</span></div>
        <div className="metric-cell"><span className="metric-label">노출도</span><span className="metric-value">{formatPercent(exposure, 0)}</span><span className="metric-detail">{portfolio.positions.length} / 12종목</span></div>
      </section>
      <section className="section-panel" style={{ marginTop: 20 }}>
        <div className="panel-header"><div><h2>현재 보유 종목</h2><p>정수 주식 기준</p></div></div>
        <div className="data-table-wrap">
          <table className="data-table">
            <thead><tr><th>종목</th><th>비교군</th><th>수량</th><th>평가액</th><th>추세 점수</th></tr></thead>
            <tbody>{portfolio.positions.map((position) => (
              <tr key={String(position.asset_id)}><td className="symbol-cell"><strong>{String(position.symbol)}</strong><span>{String(position.name)}</span></td><td>{PEER_LABELS[String(position.peer_group)] ?? String(position.peer_group)}</td><td>{Number(position.quantity).toLocaleString()}</td><td>{formatKrw(Number(position.market_value_krw))}</td><td><span className="score-number">{Number(position.score).toFixed(1)}</span></td></tr>
            ))}</tbody>
          </table>
        </div>
      </section>
      <div className="notice-box" style={{ marginTop: 16 }}>{portfolio.note}</div>
    </>
  );
}
