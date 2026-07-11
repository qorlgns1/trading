import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { CandidateHistoryClient } from "@/components/candidate-history-client";
import {
  apiFetch,
  type CandidateHistory,
  type ResearchStatus,
} from "@/lib/api";

export const metadata: Metadata = { title: "후보 이력" };

export default async function CandidateHistoryPage() {
  const status = await apiFetch<ResearchStatus>("/research/status");
  if (status.app_mode !== "local_research") notFound();
  const initial = await apiFetch<CandidateHistory>(
    "/research/candidate-history?page=1&page_size=50",
  );
  return <CandidateHistoryClient initial={initial} />;
}
