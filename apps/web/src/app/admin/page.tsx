import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { ProviderAdmin } from "@/components/provider-admin";
import { apiFetch, type ProviderList, type ResearchStatus } from "@/lib/api";

export const metadata: Metadata = { title: "공급자 관리" };

export default async function AdminPage() {
  const status = await apiFetch<ResearchStatus>("/research/status");
  if (status.app_mode !== "local_research") notFound();
  const providers = await apiFetch<ProviderList>("/admin/providers");

  return <ProviderAdmin initial={providers} />;
}
