import type { Metadata } from "next";

import { DataModeBadge } from "@/components/data-mode-badge";
import { MethodologyScoreGuide } from "@/components/methodology-score-guide";
import { apiFetch, type ResearchStatus } from "@/lib/api";

export const metadata: Metadata = { title: "방법론" };

export default async function MethodologyPage() {
  const status = await apiFetch<ResearchStatus>("/research/status");
  const isLocal = status.app_mode === "local_research";
  return (
    <>
      <header className="page-header">
        <div>
          <h1>Trend Score v1 방법론</h1>
          <p>
            가격 추세 조건을 0~100점으로 표현하는 롱온리 후보 스크리너입니다.
          </p>
        </div>
        <div className="page-meta">
          <DataModeBadge source={status.data_source} />
          <span>trend-score-v1.0.0</span>
        </div>
      </header>
      <div className="notice-box">
        추세 점수는 미래 상승 확률이나 매수 추천이 아니라 사전에 정의한 추세
        조건의 충족도입니다.
      </div>
      <MethodologyScoreGuide />
      <div className="section-grid">
        <section className="section-panel">
          <div className="panel-header">
            <div>
              <h2>후보 필수 조건</h2>
              <p>점수와 별도 적용</p>
            </div>
          </div>
          <div className="panel-body">
            <ul className="stack-list">
              <li>유효 일봉 253개 이상</li>
              <li>비교군 유효 종목 30개 이상</li>
              <li>현재가가 200일 이동평균선 위</li>
              <li>최근 6개월 수익률이 양수</li>
              <li>최소 거래대금과 주문금액 제한 통과</li>
            </ul>
          </div>
        </section>
        <section className="section-panel">
          <div className="panel-header">
            <div>
              <h2>후보 상태 안정화</h2>
              <p>매일 종가 확정 후 갱신</p>
            </div>
          </div>
          <div className="panel-body">
            <ul className="stack-list">
              <li>신규 후보: 필수 조건 통과 및 65점 이상</li>
              <li>강한 추세 후보: 80점 이상</li>
              <li>기존 후보 유지: 60점 이상</li>
              <li>후보 해제: 필수 조건 실패 또는 60점 미만</li>
            </ul>
          </div>
        </section>
      </div>
      <section className="section-panel" style={{ marginTop: 20 }}>
        <div className="panel-header">
          <div>
            <h2>데이터 처리</h2>
            <p>{isLocal ? "개인 로컬 연구" : "공개 데모"}</p>
          </div>
        </div>
        <div className="panel-body">
          <ul className="stack-list">
            <li>
              {isLocal
                ? "Nasdaq Trader·KRX 종목 목록과 yfinance 가격을 사용"
                : "고정 시드로 생성한 가상 시장 데이터만 사용"}
            </li>
            <li>
              미국 주식·미국 ETF·KOSPI·KOSDAQ·국내 ETF·해외형 ETF를 별도로 비교
            </li>
            <li>
              가격·거래량·배당·분할만 사용하고 뉴스와 기업 실적은 반영하지 않음
            </li>
            <li>253거래일 미만 종목은 검색 가능하지만 점수는 산출하지 않음</li>
            <li>수집 실패 시 이전 정상 스냅샷을 유지하고 실패 상태를 표시</li>
          </ul>
        </div>
      </section>
      <div className="notice-box warning-box" style={{ marginTop: 16 }}>
        실제 데이터 연구 종목군은 현재 상장 종목 기준입니다. 과거 상장폐지
        종목이 빠지므로 향후 백테스트에 그대로 사용하면 생존편향이 발생합니다.
      </div>
    </>
  );
}
