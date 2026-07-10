import type { Metadata } from "next";

import { BacktestLab } from "@/components/backtest-lab";

export const metadata: Metadata = { title: "백테스트" };

export default function BacktestsPage() {
  return <BacktestLab />;
}
