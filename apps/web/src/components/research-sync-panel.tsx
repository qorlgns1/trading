"use client";

import { AlertTriangle, CheckCircle2, Database, RefreshCw } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { StatusBadge } from "@/components/ui/status-badge";
import {
  getResearchSyncButtonLabel,
  getResearchSyncPresentation,
} from "@/components/research-sync-presentation";
import {
  apiFetch,
  type ResearchStatus,
  type ResearchSyncAccepted,
} from "@/lib/api";

export const STAGE_LABELS: Record<string, string> = {
  CREATED: "준비 중",
  UNIVERSE: "거래소 종목 목록 수집",
  REFRESH_ACTIONS: "기업행사 전체 이력 보정",
  MATERIALIZE: "가격 스냅샷 정리",
  SCORE: "최신 추세 점수 계산",
  ACTIVATE: "정상 스냅샷 전환",
  SUCCEEDED: "완료",
  FAILED: "실패",
  CANCELLED: "중단됨",
};

export function ResearchSyncPanel({ initial }: { initial: ResearchStatus }) {
  const router = useRouter();
  const [status, setStatus] = useState(initial);
  const [requesting, setRequesting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const priorSnapshot = useRef(initial.snapshot_state);
  const active = ["QUEUED", "RUNNING"].includes(status.last_sync?.status ?? "");

  useEffect(() => {
    if (!active && status.snapshot_state !== "PREPARING") return;
    const timer = window.setInterval(() => {
      apiFetch<ResearchStatus>("/research/status")
        .then((next) => {
          setStatus(next);
          setError(null);
          if (
            ["MISSING", "PREPARING"].includes(priorSnapshot.current) &&
            ["READY", "STALE"].includes(next.snapshot_state)
          ) {
            router.refresh();
          }
          priorSnapshot.current = next.snapshot_state;
        })
        .catch((reason: Error) => setError(reason.message));
    }, 2000);
    return () => window.clearInterval(timer);
  }, [active, router, status.snapshot_state]);

  async function startSync() {
    setRequesting(true);
    try {
      await apiFetch<ResearchSyncAccepted>("/research/sync", { method: "POST" });
      setStatus(await apiFetch<ResearchStatus>("/research/status"));
      setError(null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "동기화 요청에 실패했습니다.");
    } finally {
      setRequesting(false);
    }
  }

  const sync = status.last_sync;
  const progress = sync?.progress_percent ?? 0;
  const presentation = getResearchSyncPresentation(
    sync?.collection_mode,
    Boolean(status.data_version),
  );
  const buttonLabel = getResearchSyncButtonLabel({
    active,
    activeButtonLabel: presentation.activeButtonLabel,
    failed: sync?.status === "FAILED",
  });

  return (
    <section className="section-panel research-sync-panel">
      <div className="sync-heading">
        <span className="sync-icon"><Database size={22} /></span>
        <div>
          <h2>{status.snapshot_state === "MISSING" ? "첫 실데이터 준비" : "시장 데이터 갱신"}</h2>
          <p>원본 가격은 이 컴퓨터의 로컬 연구 저장소에만 보관됩니다.</p>
        </div>
        {sync && (
          <StatusBadge
            state={sync.status}
            label={sync.status === "RUNNING" ? "갱신 중" : undefined}
          />
        )}
      </div>

      {sync && (
        <div className="sync-progress-block">
          <div className="sync-progress-label">
            <strong>
              {sync.stage === "DOWNLOAD"
                ? presentation.downloadStageLabel
                : (STAGE_LABELS[sync.stage] ?? sync.stage)}
            </strong>
            <span>{progress.toFixed(0)}%</span>
          </div>
          <div
            className="progress-track"
            aria-label={`이번 작업 진행률 ${progress.toFixed(0)}%`}
          >
            <div className="progress-value" style={{ width: `${progress}%` }} />
          </div>
          <span className="sync-batch-count">
            {sync.total_batches > 0
              ? `이번 작업 ${sync.completed_batches.toLocaleString()} / ${sync.total_batches.toLocaleString()} 배치`
              : "이번 작업의 종목 목록을 준비하고 있습니다."}
          </span>
        </div>
      )}

      {sync?.status === "SUCCEEDED" && (
        <div className="notice-box success-box">
          <CheckCircle2 size={15} /> 정상 스냅샷으로 전환했습니다.
        </div>
      )}
      {sync?.status === "FAILED" && (
        <div className="notice-box danger-box">
          <AlertTriangle size={15} /> {sync.error_message ?? "수집 작업이 실패했습니다."}
        </div>
      )}
      {error && <div className="notice-box danger-box">{error}</div>}

      <div className="sync-actions">
        <Button type="button" onClick={startSync} disabled={requesting || active}>
          <RefreshCw size={16} className={requesting || active ? "spin" : ""} />
          {buttonLabel}
        </Button>
        <span>{presentation.description}</span>
      </div>
    </section>
  );
}
