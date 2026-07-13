import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { ReplayExperimentBuilder } from "@/components/replay-experiment-builder";
import {
  apiFetch,
  type ReplayExperiment,
  type ReplayOptions,
  type ReplayStrategy,
  type ResearchStatus,
} from "@/lib/api";

export const metadata: Metadata = { title: "새 전략 실험" };

export default async function NewReplayExperimentPage({
  searchParams,
}: {
  searchParams: Promise<{
    experiment_id?: string;
    sweep_run_id?: string;
    candidate_index?: string;
  }>;
}) {
  const status = await apiFetch<ResearchStatus>("/research/status");
  if (status.app_mode !== "local_research") notFound();
  const {
    experiment_id: experimentId,
    sweep_run_id: sweepRunId,
    candidate_index: candidateIndex,
  } = await searchParams;
  const [options, experiment] = await Promise.all([
    apiFetch<ReplayOptions>("/research/replay-options"),
    experimentId
      ? apiFetch<ReplayExperiment>(`/research/experiments/${experimentId}`)
      : Promise.resolve(undefined),
  ]);
  const sweepRun = (
    experiment?.runs as
      | Array<{
          run_id?: string;
          result?: {
            rows?: Array<{ index?: number; strategy?: ReplayStrategy }>;
          };
        }>
      | undefined
  )?.find((run) => run.run_id === sweepRunId);
  const selectedCandidate = sweepRun?.result?.rows?.find(
    (row) => row.index === Number(candidateIndex),
  );
  return (
    <ReplayExperimentBuilder
      options={options}
      existingExperiment={experiment}
      initialStrategy={selectedCandidate?.strategy}
      suggestedName={
        selectedCandidate
          ? `Pareto 후보 #${Number(candidateIndex) + 1}`
          : undefined
      }
    />
  );
}
