import type { Metadata } from "next";
import { ShieldCheck } from "lucide-react";
import { notFound } from "next/navigation";

import { DataModeBadge } from "@/components/data-mode-badge";
import { QualityClient } from "@/components/quality-client";
import { ResearchSyncPanel } from "@/components/research-sync-panel";
import {
  apiFetch,
  type QualityIssues,
  type QualityReport,
  type ResearchStatus,
} from "@/lib/api";

export const metadata: Metadata = { title: "데이터 품질" };

export default async function QualityPage() {
  const status = await apiFetch<ResearchStatus>("/research/status");
  if (status.app_mode !== "local_research") notFound();

  let report: QualityReport | null = null;
  let issues: QualityIssues | null = null;
  try {
    [report, issues] = await Promise.all([
      apiFetch<QualityReport>("/research/quality"),
      apiFetch<QualityIssues>("/research/quality/issues?page=1&page_size=25"),
    ]);
  } catch {
    // An existing pre-quality snapshot stays usable until the first validated sync finishes.
  }

  if (!report || !issues) {
    return (
      <>
        <header className="page-header">
          <div>
            <h1>데이터 품질</h1>
            <p>
              다음 실데이터 동기화부터 수집 결과를 검사하고 오류 종목을
              격리합니다.
            </p>
          </div>
          <div className="page-meta">
            <DataModeBadge source={status.data_source} />
          </div>
        </header>
        <div className="notice-box warning-box" style={{ marginBottom: 20 }}>
          <ShieldCheck size={15} /> 현재 활성 스냅샷은 품질 정책 적용 전
          버전입니다. 새 동기화가 완료되면 보고서가 표시됩니다.
        </div>
        <ResearchSyncPanel initial={status} />
      </>
    );
  }

  return (
    <QualityClient
      initialReport={report}
      initialIssues={issues}
      dataSource={status.data_source}
    />
  );
}
