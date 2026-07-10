"use client";

import * as Slider from "@radix-ui/react-slider";
import { FlaskConical, LoaderCircle, Play } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { StatusBadge } from "@/components/ui/status-badge";
import { apiFetch } from "@/lib/api";
import {
  DEFAULT_ALLOCATIONS,
  toBasisPoints,
  updateAllocation,
  type AllocationKey,
  type Allocations,
} from "@/lib/allocations";

const LABELS: Record<AllocationKey, string> = {
  us_stock: "미국 주식",
  kr_stock: "한국 주식",
  us_etf: "미국 ETF",
  kr_etf: "한국 ETF",
};

type Accepted = { run_id: string; status: string; cached: boolean };

export function BacktestLab() {
  const router = useRouter();
  const [allocations, setAllocations] = useState<Allocations>(DEFAULT_ALLOCATIONS);
  const [status, setStatus] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function submit() {
    setError(null);
    setStatus("QUEUED");
    try {
      const accepted = await apiFetch<Accepted>("/backtests", {
        method: "POST",
        body: JSON.stringify({ sleeve_weights_bps: toBasisPoints(allocations) }),
      });
      if (accepted.cached || accepted.status === "SUCCEEDED") {
        router.push(`/backtests/${accepted.run_id}`);
        return;
      }
      setStatus("RUNNING");
      router.push(`/backtests/${accepted.run_id}`);
    } catch (reason) {
      setStatus("FAILED");
      setError(reason instanceof Error ? reason.message : "백테스트 실행에 실패했습니다.");
    }
  }

  return (
    <>
      <header className="page-header">
        <div>
          <h1>백테스트 실험실</h1>
          <p>Trend Score와 거래 규칙은 고정하고 네 자산군의 초기 배분만 변경합니다.</p>
        </div>
        <div className="page-meta"><span className="demo-chip">10년 가상 데이터</span></div>
      </header>
      <div className="section-grid">
        <section className="section-panel">
          <div className="panel-header"><div><h2>초기 자산군 비중</h2><p>합계 100% · 5% 단위</p></div><FlaskConical size={19} color="#23699a" /></div>
          <div className="panel-body" style={{ display: "grid", gap: 24 }}>
            {(Object.keys(allocations) as AllocationKey[]).map((key) => (
              <div key={key}>
                <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 10, gap: 12 }}>
                  <label htmlFor={`allocation-${key}`} style={{ fontSize: 13, fontWeight: 700 }}>{LABELS[key]}</label>
                  <strong style={{ fontVariantNumeric: "tabular-nums" }}>{allocations[key]}%</strong>
                </div>
                <Slider.Root
                  id={`allocation-${key}`}
                  className="allocation-slider"
                  min={0}
                  max={100}
                  step={5}
                  value={[allocations[key]]}
                  onValueChange={([value]) => setAllocations((current) => updateAllocation(current, key, value))}
                  aria-label={`${LABELS[key]} 비중`}
                >
                  <Slider.Track className="slider-track"><Slider.Range className="slider-range" /></Slider.Track>
                  <Slider.Thumb className="slider-thumb" />
                </Slider.Root>
              </div>
            ))}
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12, borderTop: "1px solid var(--border)", paddingTop: 18 }}>
              <div><span className="metric-label">총 비중</span><strong>100%</strong></div>
              <Button onClick={submit} disabled={status === "QUEUED" || status === "RUNNING"}>
                {status === "QUEUED" || status === "RUNNING" ? <LoaderCircle size={16} className="spin" /> : <Play size={16} fill="currentColor" />}
                실행
              </Button>
            </div>
            {status && <div style={{ display: "flex", alignItems: "center", gap: 9 }}><StatusBadge state={status} />{error && <span style={{ color: "var(--red)", fontSize: 12 }}>{error}</span>}</div>}
          </div>
        </section>
        <section className="section-panel">
          <div className="panel-header"><div><h2>고정 전략 조건</h2><p>portfolio-v1.0.0</p></div></div>
          <div className="panel-body">
            <dl className="rule-list">
              <div><dt>시작 자금</dt><dd>50,000,000원</dd></div>
              <div><dt>최대 보유</dt><dd>12종목</dd></div>
              <div><dt>신규 진입</dt><dd>주간 65점 이상</dd></div>
              <div><dt>보유 종료</dt><dd>60점 미만 또는 필수 조건 실패</dd></div>
              <div><dt>체결 시점</dt><dd>신호 다음 거래일 시가</dd></div>
              <div><dt>수량</dt><dd>정수 주식</dd></div>
            </dl>
            <div className="notice-box warning-box" style={{ marginTop: 18 }}>
              결과는 고정 시드의 가상 시장에서 전략 동작을 검증한 값이며 실제 기대수익률이 아닙니다.
            </div>
          </div>
        </section>
      </div>
    </>
  );
}
