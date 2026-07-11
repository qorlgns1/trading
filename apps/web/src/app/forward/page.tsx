import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { ForwardPortfolio } from "@/components/forward-portfolio";
import { apiFetch, type ResearchStatus } from "@/lib/api";

export const metadata: Metadata = { title: "포워드 포트폴리오" };

export default async function ForwardPage() {
  const status = await apiFetch<ResearchStatus>("/research/status");
  if (status.app_mode !== "local_research") notFound();
  return <ForwardPortfolio />;
}
