"use client";

import { AlertTriangle, RotateCcw } from "lucide-react";

import { Button } from "@/components/ui/button";

export default function ErrorPage({ reset }: { error: Error; reset: () => void }) {
  return (
    <section className="section-panel empty-state">
      <AlertTriangle size={28} aria-hidden="true" />
      <h1>데이터를 불러오지 못했습니다</h1>
      <p>API 상태를 확인한 뒤 다시 시도해 주세요.</p>
      <Button onClick={reset}>
        <RotateCcw size={16} /> 다시 시도
      </Button>
    </section>
  );
}
