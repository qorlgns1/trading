"use client";

import * as echarts from "echarts";
import { useEffect, useRef } from "react";

type ChartSeries = {
  name: string;
  values: Array<number | null>;
  color: string;
  area?: boolean;
};

export function LineChart({
  labels,
  series,
  valueFormat = "number",
  compact = false,
}: {
  labels: string[];
  series: ChartSeries[];
  valueFormat?: "number" | "score" | "percent" | "krw-millions";
  compact?: boolean;
}) {
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!containerRef.current) return;
    const formatValue = (value: number) => {
      if (valueFormat === "score") return `${value.toFixed(0)}점`;
      if (valueFormat === "percent") return `${(value * 100).toFixed(1)}%`;
      if (valueFormat === "krw-millions") return `${Math.round(value / 1_000_000)}백만원`;
      return value.toLocaleString("ko-KR", { maximumFractionDigits: 0 });
    };
    const chart = echarts.init(containerRef.current, undefined, { renderer: "canvas" });
    chart.setOption({
      animationDuration: 350,
      aria: { enabled: true },
      color: series.map((item) => item.color),
      grid: { left: 10, right: 14, top: compact ? 18 : 34, bottom: 24, containLabel: true },
      legend: compact ? undefined : { top: 0, right: 8, textStyle: { color: "#59665f", fontSize: 11 } },
      tooltip: {
        trigger: "axis",
        borderColor: "#bdc9c2",
        backgroundColor: "#ffffff",
        textStyle: { color: "#17201b", fontSize: 12 },
        valueFormatter: (value: unknown) =>
          typeof value === "number" ? formatValue(value) : String(value ?? "-"),
      },
      xAxis: {
        type: "category",
        data: labels,
        boundaryGap: false,
        axisLine: { lineStyle: { color: "#d8dfdb" } },
        axisTick: { show: false },
        axisLabel: { color: "#7a867f", fontSize: 10, hideOverlap: true },
      },
      yAxis: {
        type: "value",
        scale: true,
        axisLabel: {
          color: "#7a867f",
          fontSize: 10,
          formatter: formatValue,
        },
        splitLine: { lineStyle: { color: "#e9eeeb", type: "dashed" } },
      },
      series: series.map((item) => ({
        name: item.name,
        type: "line",
        data: item.values,
        showSymbol: false,
        connectNulls: false,
        smooth: false,
        lineStyle: { width: 2, color: item.color },
        areaStyle: item.area ? { color: item.color, opacity: 0.08 } : undefined,
      })),
    });
    const observer = new ResizeObserver(() => chart.resize());
    observer.observe(containerRef.current);
    return () => {
      observer.disconnect();
      chart.dispose();
    };
  }, [compact, labels, series, valueFormat]);

  return (
    <div
      ref={containerRef}
      className={compact ? "chart-container chart-container-small" : "chart-container"}
    />
  );
}
