"use client";

import * as echarts from "echarts";
import { useEffect, useRef } from "react";

export function ReplayParetoChart({
  rows,
  paretoIndexes,
}: {
  rows: Array<{
    index: number;
    training: Record<string, number>;
    validation: Record<string, number>;
  }>;
  paretoIndexes: Set<number>;
}) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!ref.current) return;
    const chart = echarts.init(ref.current, undefined, { renderer: "canvas" });
    chart.setOption({
      animationDuration: 300,
      aria: { enabled: true },
      grid: { left: 18, right: 20, top: 28, bottom: 26, containLabel: true },
      tooltip: {
        trigger: "item",
        formatter: (params: { data: [number, number, number] }) =>
          `조합 ${params.data[2] + 1}<br/>학습 CAGR ${(params.data[1] * 100).toFixed(2)}%<br/>MDD ${(params.data[0] * 100).toFixed(2)}%`,
      },
      xAxis: {
        name: "절대 MDD",
        type: "value",
        axisLabel: {
          formatter: (value: number) => `${(value * 100).toFixed(0)}%`,
        },
        splitLine: { lineStyle: { color: "#e9eeeb", type: "dashed" } },
      },
      yAxis: {
        name: "학습 CAGR",
        type: "value",
        axisLabel: {
          formatter: (value: number) => `${(value * 100).toFixed(0)}%`,
        },
        splitLine: { lineStyle: { color: "#e9eeeb", type: "dashed" } },
      },
      series: [
        {
          type: "scatter",
          symbolSize: 9,
          data: rows
            .filter((row) => !paretoIndexes.has(row.index))
            .map((row) => [
              Math.abs(row.training.max_drawdown),
              row.training.cagr,
              row.index,
            ]),
          itemStyle: { color: "#8b9891" },
          name: "전체 조합",
        },
        {
          type: "scatter",
          symbolSize: 13,
          data: rows
            .filter((row) => paretoIndexes.has(row.index))
            .map((row) => [
              Math.abs(row.training.max_drawdown),
              row.training.cagr,
              row.index,
            ]),
          itemStyle: { color: "#176b48", borderColor: "#fff", borderWidth: 2 },
          name: "Pareto 후보",
        },
      ],
    });
    const observer = new ResizeObserver(() => chart.resize());
    observer.observe(ref.current);
    return () => {
      observer.disconnect();
      chart.dispose();
    };
  }, [paretoIndexes, rows]);
  return <div ref={ref} className="chart-container pareto-chart" />;
}
