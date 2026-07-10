import type { Metadata } from "next";

import { BacktestResultView } from "@/components/backtest-result";

export const metadata: Metadata = { title: "백테스트 결과" };

export default async function BacktestResultPage({ params }: { params: Promise<{ runId: string }> }) {
  const { runId } = await params;
  return <BacktestResultView runId={runId} />;
}
