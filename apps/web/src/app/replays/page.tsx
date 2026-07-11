import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { ReplayLab } from "@/components/replay-lab";
import { apiFetch, type ResearchStatus } from "@/lib/api";

export const metadata: Metadata = { title: "과거 시뮬레이션" };

export default async function ReplaysPage() {
  const status = await apiFetch<ResearchStatus>("/research/status");
  if (status.app_mode !== "local_research") notFound();
  return <ReplayLab />;
}
