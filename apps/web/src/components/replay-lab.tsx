"use client";

import * as Slider from "@radix-ui/react-slider";
import { AlertTriangle, Database, LoaderCircle, Play } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { StatusBadge } from "@/components/ui/status-badge";
import { apiFetch, type ReplayAccepted } from "@/lib/api";
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

export function ReplayLab() {
  const router = useRouter();
  const [allocations, setAllocations] =
    useState<Allocations>(DEFAULT_ALLOCATIONS);
  const [status, setStatus] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function submit() {
    setError(null);
    setStatus("QUEUED");
    try {
      const accepted = await apiFetch<ReplayAccepted>("/research/replays", {
        method: "POST",
        body: JSON.stringify({
          sleeve_weights_bps: toBasisPoints(allocations),
        }),
      });
      router.push(`/replays/${accepted.run_id}`);
    } catch (reason) {
      setStatus("FAILED");
      setError(
        reason instanceof Error
          ? reason.message
          : "과거 시뮬레이션을 시작하지 못했습니다.",
      );
    }
  }

  const busy = status === "QUEUED" || status === "RUNNING";
  return (
    <>
      <header className="page-header">
        <div>
          <h1>과거 시뮬레이션</h1>
          <p>
            현재 보유한 실제 일봉으로 고정 전략을 재생하고 체결·비용·현금을 함께
            계산합니다.
          </p>
        </div>
        <div className="page-meta">
          <span className="demo-chip real-data-chip">로컬 실데이터</span>
          <span>10년 전체</span>
        </div>
      </header>

      <div className="notice-box warning-box" style={{ marginBottom: 20 }}>
        <AlertTriangle size={15} /> 현재 상장된 종목만 사용하므로 생존편향이
        포함됩니다. 공식 성과나 투자 추천으로 해석할 수 없습니다.
      </div>

      <div className="section-grid">
        <section className="section-panel">
          <div className="panel-header">
            <div>
              <h2>초기 자산군 비중</h2>
              <p>합계 100% · 실행별 결과 캐시</p>
            </div>
            <Database size={19} color="#23699a" />
          </div>
          <div className="panel-body allocation-list">
            {(Object.keys(allocations) as AllocationKey[]).map((key) => (
              <div key={key}>
                <div className="allocation-heading">
                  <label htmlFor={`replay-${key}`}>{LABELS[key]}</label>
                  <strong>{allocations[key]}%</strong>
                </div>
                <Slider.Root
                  id={`replay-${key}`}
                  className="allocation-slider"
                  min={0}
                  max={100}
                  step={5}
                  value={[allocations[key]]}
                  onValueChange={([value]) =>
                    setAllocations((current) =>
                      updateAllocation(current, key, value),
                    )
                  }
                  aria-label={`${LABELS[key]} 비중`}
                >
                  <Slider.Track className="slider-track">
                    <Slider.Range className="slider-range" />
                  </Slider.Track>
                  <Slider.Thumb className="slider-thumb" />
                </Slider.Root>
              </div>
            ))}
            <div className="allocation-submit">
              <div>
                <span className="metric-label">총 비중</span>
                <strong>100%</strong>
              </div>
              <Button onClick={submit} disabled={busy}>
                {busy ? (
                  <LoaderCircle size={16} className="spin" />
                ) : (
                  <Play size={16} fill="currentColor" />
                )}
                재생 시작
              </Button>
            </div>
            {status && (
              <div className="inline-status" aria-live="polite">
                <StatusBadge state={status} />
                {error && <span className="form-error">{error}</span>}
              </div>
            )}
          </div>
        </section>

        <section className="section-panel">
          <div className="panel-header">
            <div>
              <h2>재생 규칙</h2>
              <p>portfolio-v1.0.0</p>
            </div>
          </div>
          <div className="panel-body">
            <dl className="rule-list">
              <div>
                <dt>시작 조건</dt>
                <dd>여섯 비교군 준비 완료</dd>
              </div>
              <div>
                <dt>시작 자금</dt>
                <dd>50,000,000원</dd>
              </div>
              <div>
                <dt>최대 보유</dt>
                <dd>12종목</dd>
              </div>
              <div>
                <dt>주간 평가</dt>
                <dd>한국·미국 종가 확정 후</dd>
              </div>
              <div>
                <dt>체결</dt>
                <dd>시장별 다음 거래일 시가</dd>
              </div>
              <div>
                <dt>후보 규칙</dt>
                <dd>진입 65점 · 유지 60점</dd>
              </div>
            </dl>
          </div>
        </section>
      </div>
    </>
  );
}
