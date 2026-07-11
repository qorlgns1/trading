import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { ReplayResultView } from "@/components/replay-result";
import { apiFetch, type ResearchStatus } from "@/lib/api";

export const metadata: Metadata = { title: "과거 시뮬레이션 결과" };

export default async function ReplayResultPage({
  params,
}: {
  params: Promise<{ runId: string }>;
}) {
  const status = await apiFetch<ResearchStatus>("/research/status");
  if (status.app_mode !== "local_research") notFound();
  const { runId } = await params;
  return <ReplayResultView runId={runId} />;
}
