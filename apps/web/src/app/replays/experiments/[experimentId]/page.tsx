import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { ReplayExperimentDetail } from "@/components/replay-experiment-detail";
import {
  apiFetch,
  type ReplayComparison,
  type ReplayExperiment,
  type ResearchStatus,
} from "@/lib/api";

export const metadata: Metadata = { title: "전략 실험 결과" };

export default async function ReplayExperimentPage({
  params,
}: {
  params: Promise<{ experimentId: string }>;
}) {
  const { experimentId } = await params;
  const status = await apiFetch<ResearchStatus>("/research/status");
  if (status.app_mode !== "local_research") notFound();
  const [experiment, comparison] = await Promise.all([
    apiFetch<ReplayExperiment>(`/research/experiments/${experimentId}`),
    apiFetch<ReplayComparison>(
      `/research/experiments/${experimentId}/comparison`,
    ),
  ]);
  return (
    <ReplayExperimentDetail
      initialExperiment={experiment}
      initialComparison={comparison}
    />
  );
}
