"use client";

import { Calculator, Lightbulb, Sparkles } from "lucide-react";
import { useState } from "react";

const SCORE_ITEMS = [
  {
    id: "long-term-trend",
    name: "장기 추세 구조",
    score: 30,
    summary: "가격이 장기 상승 흐름 위에 있는지 확인",
    meaning:
      "하루 이틀 오른 종목보다, 오랜 기간 상승 방향을 유지한 종목을 찾기 위한 점수입니다.",
    calculation: [
      "현재 수정주가가 200일선 위: 15점",
      "50일선이 200일선 위: 10점",
      "200일선이 20거래일 전보다 상승: 5점",
    ],
    example:
      "세 조건을 모두 만족하면 30점입니다. 현재가와 50일선 조건만 만족하면 25점입니다.",
  },
  {
    id: "absolute-momentum",
    name: "절대 모멘텀",
    score: 25,
    summary: "종목 자체의 기간별 수익률이 양수인지 확인",
    meaning:
      "다른 종목보다 덜 떨어졌다는 이유만으로 높은 평가를 받지 않도록, 실제로 가격이 올랐는지 확인합니다.",
    calculation: [
      "최근 3개월 수익률이 양수: 5점",
      "최근 6개월 수익률이 양수: 10점",
      "최근 1개월을 제외한 약 1년 수익률이 양수: 10점",
    ],
    example:
      "세 기간 수익률이 모두 플러스면 25점입니다. 3개월만 마이너스라면 20점입니다.",
  },
  {
    id: "relative-strength",
    name: "상대강도",
    score: 20,
    summary: "같은 비교군에서 상승 힘이 얼마나 강한지 비교",
    meaning:
      "시장마다 움직임이 다르므로 미국 주식, KOSPI, ETF처럼 비슷한 종목끼리 비교합니다.",
    calculation: [
      "3개월 수익률에 20% 반영",
      "6개월 수익률에 40% 반영",
      "최근 1개월을 제외한 약 1년 수익률에 40% 반영",
      "합산 결과의 비교군 순위를 0~20점으로 변환",
    ],
    example:
      "같은 비교군에서 대략 상위 10%라면 약 18점 이상을 받습니다. 동점 종목은 평균 순위를 사용합니다.",
  },
  {
    id: "high-proximity",
    name: "52주 고점 근접도",
    score: 10,
    summary: "현재 가격이 최근 1년 최고가에 얼마나 가까운지 확인",
    meaning:
      "강한 추세의 종목은 최근 고점 부근에서 움직이는 경우가 많다는 추세추종 관점을 반영합니다.",
    calculation: [
      "현재 수정주가 ÷ 최근 252거래일 최고 수정주가",
      "고점의 80% 이하: 0점",
      "고점의 80~95%: 비례해서 0~10점",
      "고점의 95% 이상: 10점",
    ],
    example:
      "최근 고점이 10만원이고 현재 수정주가가 9만원이면 고점의 90%입니다. 10 × ((90%-80%) ÷ 15%)로 약 6.7점입니다.",
  },
  {
    id: "volatility",
    name: "변동성 안정성",
    score: 10,
    summary: "같은 비교군보다 가격이 덜 흔들리는지 확인",
    meaning:
      "상승하더라도 가격이 지나치게 크게 흔들리면 실제 보유가 어렵고 손실 폭도 커질 수 있습니다.",
    calculation: [
      "최근 60거래일의 일간 수익률 변동성을 계산",
      "같은 비교군에서 변동성이 낮을수록 높은 순위",
      "낮은 변동성 순위를 0~10점으로 변환",
    ],
    example:
      "비교군에서 변동성이 가장 낮은 편이면 10점에 가깝고, 중간 수준이면 약 5점입니다.",
  },
  {
    id: "activity",
    name: "거래활성도",
    score: 5,
    summary: "평소 거래대금이 충분한 종목인지 비교",
    meaning:
      "거래가 드문 종목은 원하는 가격에 사고팔기 어렵기 때문에 활발하게 거래되는 종목을 선호합니다.",
    calculation: [
      "종가 × 거래량으로 일별 거래대금 계산",
      "최근 60거래일 거래대금의 중앙값 사용",
      "같은 비교군의 거래대금 순위를 0~5점으로 변환",
    ],
    example:
      "거래대금이 비교군 상위 20%라면 약 4점 이상입니다. 하루만 거래가 폭증한 영향은 중앙값으로 줄입니다.",
  },
] as const;

export function MethodologyScoreGuide() {
  const [activeId, setActiveId] = useState<(typeof SCORE_ITEMS)[number]["id"]>(
    SCORE_ITEMS[0].id,
  );
  const active =
    SCORE_ITEMS.find((item) => item.id === activeId) ?? SCORE_ITEMS[0];

  return (
    <section className="section-panel methodology-score-section">
      <div className="panel-header">
        <div>
          <h2>점수 구성</h2>
          <p>6개 기준 · 총 100점</p>
        </div>
        <span className="methodology-total-score">100점</span>
      </div>

      <div className="methodology-score-layout">
        <div
          className="methodology-score-tabs"
          role="tablist"
          aria-label="Trend Score 구성 요소"
          aria-orientation="vertical"
        >
          {SCORE_ITEMS.map((item, index) => {
            const selected = item.id === active.id;
            return (
              <button
                key={item.id}
                id={`score-tab-${item.id}`}
                className={
                  selected
                    ? "methodology-score-tab methodology-score-tab-active"
                    : "methodology-score-tab"
                }
                type="button"
                role="tab"
                aria-selected={selected}
                aria-controls={`score-panel-${item.id}`}
                tabIndex={selected ? 0 : -1}
                onMouseEnter={() => setActiveId(item.id)}
                onFocus={() => setActiveId(item.id)}
                onClick={() => setActiveId(item.id)}
              >
                <span className="methodology-score-index">
                  {String(index + 1).padStart(2, "0")}
                </span>
                <span className="methodology-score-name">
                  <strong>{item.name}</strong>
                  <small>{item.summary}</small>
                </span>
                <span className="methodology-score-points">
                  <strong>{item.score}</strong>
                  <small>점</small>
                </span>
              </button>
            );
          })}
        </div>

        <div
          id={`score-panel-${active.id}`}
          className="methodology-score-detail"
          role="tabpanel"
          aria-labelledby={`score-tab-${active.id}`}
          tabIndex={0}
        >
          <div className="methodology-detail-heading">
            <span>{active.score}점 기준</span>
            <h3>{active.name}</h3>
            <p>{active.summary}</p>
          </div>

          <div className="methodology-detail-block">
            <div className="methodology-detail-label">
              <Lightbulb size={15} aria-hidden="true" /> 왜 보나요?
            </div>
            <p>{active.meaning}</p>
          </div>

          <div className="methodology-detail-block">
            <div className="methodology-detail-label">
              <Calculator size={15} aria-hidden="true" /> 어떻게 계산하나요?
            </div>
            <ul>
              {active.calculation.map((line) => (
                <li key={line}>{line}</li>
              ))}
            </ul>
          </div>

          <div className="methodology-example">
            <Sparkles size={15} aria-hidden="true" />
            <p>
              <strong>예시</strong>
              {active.example}
            </p>
          </div>
        </div>
      </div>
    </section>
  );
}
