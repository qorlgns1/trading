import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { ReplayExperimentHome } from "@/components/replay-experiment-home";
import {
  apiFetch,
  type ReplayExperimentList,
  type ReplayOptions,
  type ResearchStatus,
} from "@/lib/api";

export const metadata: Metadata = { title: "전략 실험실" };

export default async function ReplaysPage() {
  const status = await apiFetch<ResearchStatus>("/research/status");
  if (status.app_mode !== "local_research") notFound();
  const [options, experiments] = await Promise.all([
    apiFetch<ReplayOptions>("/research/replay-options"),
    apiFetch<ReplayExperimentList>("/research/experiments"),
  ]);
  return <ReplayExperimentHome options={options} experiments={experiments} />;
}
