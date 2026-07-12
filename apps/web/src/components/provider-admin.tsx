"use client";

import {
  Activity,
  CheckCircle2,
  Clock3,
  Database,
  LoaderCircle,
  RefreshCw,
  ShieldCheck,
} from "lucide-react";
import { useState } from "react";

import { DataModeBadge } from "@/components/data-mode-badge";
import { Button } from "@/components/ui/button";
import { apiFetch, type ProviderList, type ProviderStatus } from "@/lib/api";
import { cn } from "@/lib/utils";

const STATUS_LABELS: Record<string, string> = {
  ACTIVE: "사용 중",
  AVAILABLE: "연결 가능",
  NOT_CHECKED: "확인 전",
  NOT_CONFIGURED: "미설정",
  UNAVAILABLE: "연결 실패",
};

function statusClass(status: string) {
  if (["ACTIVE", "AVAILABLE"].includes(status)) return "status-complete";
  if (status === "UNAVAILABLE") return "status-danger";
  if (status === "NOT_CHECKED") return "status-running";
  return "status-muted";
}

function formatCheckedAt(value: string | null | undefined) {
  if (!value) return "확인 기록 없음";
  return new Intl.DateTimeFormat("ko-KR", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}

export function ProviderAdmin({ initial }: { initial: ProviderList }) {
  const [items, setItems] = useState(initial.items);
  const [checking, setChecking] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function checkToss() {
    setChecking(true);
    setError(null);
    try {
      const result = await apiFetch<ProviderStatus>(
        "/admin/providers/toss/check",
        { method: "POST" },
      );
      setItems((current) =>
        current.map((item) =>
          item.provider === result.provider ? result : item,
        ),
      );
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "토스 연결 상태를 확인하지 못했습니다.",
      );
    } finally {
      setChecking(false);
    }
  }

  const toss = items.find((item) => item.provider === "TOSS");
  const activeCount = items.filter((item) => item.used_in_pipeline).length;
  const readyCount = items.filter((item) =>
    ["ACTIVE", "AVAILABLE"].includes(item.status),
  ).length;

  return (
    <>
      <header className="page-header">
        <div>
          <h1>공급자 관리</h1>
          <p>
            시장 데이터가 어디에서 들어오는지 확인하고, 대기 중인 외부 공급자의
            연결 상태를 점검합니다.
          </p>
        </div>
        <div className="page-meta">
          <DataModeBadge source="YFINANCE" />
          <span>로컬 전용</span>
        </div>
      </header>

      <section className="metric-strip provider-metrics">
        <div className="metric-cell">
          <span className="metric-label">등록 공급자</span>
          <span className="metric-value">{items.length}개</span>
          <span className="metric-detail">가격·종목군·대기 공급자</span>
        </div>
        <div className="metric-cell">
          <span className="metric-label">현재 데이터 흐름에서 사용</span>
          <span className="metric-value">{activeCount}개</span>
          <span className="metric-detail">yfinance와 KRX</span>
        </div>
        <div className="metric-cell">
          <span className="metric-label">정상 또는 연결 가능</span>
          <span className="metric-value">{readyCount}개</span>
          <span className="metric-detail">마지막 확인 결과 기준</span>
        </div>
      </section>

      <div className="notice-box provider-notice">
        <ShieldCheck size={16} aria-hidden="true" />
        <span>
          토스는 연결만 준비된 대기 공급자입니다. 이 화면에서 확인해도 가격
          수집, 추세 점수, 과거 시뮬레이션 결과는 바뀌지 않습니다.
        </span>
      </div>

      {error && (
        <div className="notice-box danger-box" role="alert">
          {error}
        </div>
      )}

      <section className="section-panel provider-panel">
        <div className="panel-header">
          <div>
            <h2>데이터 공급자</h2>
            <p>연결 여부와 실제 계산 사용 여부를 구분해서 표시합니다.</p>
          </div>
        </div>
        <div className="provider-table-heading" aria-hidden="true">
          <span>공급자</span>
          <span>역할과 기능</span>
          <span>연결 상태</span>
          <span>마지막 확인</span>
          <span>작업</span>
        </div>
        <div className="provider-list">
          {items.map((item) => (
            <article className="provider-row" key={item.provider}>
              <div className="provider-identity">
                <span className="provider-icon" aria-hidden="true">
                  {item.provider === "TOSS" ? (
                    <Activity size={19} />
                  ) : (
                    <Database size={19} />
                  )}
                </span>
                <div>
                  <strong>{item.display_name}</strong>
                  <span className="provider-pipeline-state">
                    {item.used_in_pipeline ? (
                      <>
                        <CheckCircle2 size={13} /> 현재 계산에 사용
                      </>
                    ) : (
                      <>
                        <Clock3 size={13} /> 대기 공급자
                      </>
                    )}
                  </span>
                </div>
              </div>
              <div className="provider-role">
                <strong>{item.role}</strong>
                <p>{item.description}</p>
                <div className="provider-capabilities">
                  {(item.capabilities ?? []).map((capability) => (
                    <span key={capability}>{capability}</span>
                  ))}
                </div>
              </div>
              <div className="provider-status">
                <span className={cn("status-badge", statusClass(item.status))}>
                  {STATUS_LABELS[item.status] ?? item.status}
                </span>
                <small>{item.message}</small>
              </div>
              <div className="provider-checked-at">
                <span>{formatCheckedAt(item.last_checked_at)}</span>
                {item.latency_ms != null && (
                  <small>응답 {item.latency_ms.toLocaleString()}ms</small>
                )}
              </div>
              <div className="provider-action">
                {item.provider === "TOSS" && (
                  <Button
                    variant="secondary"
                    type="button"
                    disabled={checking || !item.enabled || !item.configured}
                    onClick={checkToss}
                    title="토스 국내·미국 대표 종목 연결 확인"
                  >
                    {checking ? (
                      <LoaderCircle size={15} className="spin" />
                    ) : (
                      <RefreshCw size={15} />
                    )}
                    {checking ? "확인 중" : "연결 확인"}
                  </Button>
                )}
              </div>
            </article>
          ))}
        </div>
      </section>

      <p className="provider-footnote" aria-live="polite">
        {checking
          ? "토스 인증 후 삼성전자와 Apple 종목 정보를 확인하고 있습니다."
          : toss?.last_checked_at
            ? `토스 마지막 확인: ${formatCheckedAt(toss.last_checked_at)}`
            : "토스 연결은 버튼을 누를 때만 확인합니다."}
      </p>
    </>
  );
}
