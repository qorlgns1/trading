"use client";

import * as echarts from "echarts";
import { useEffect, useRef } from "react";

import type { ReplayAnalysis } from "@/lib/api";

function useReplayChart(
  configure: (container: HTMLDivElement) => echarts.ECharts,
  dependencies: unknown[],
) {
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!containerRef.current) return;
    const chart = configure(containerRef.current);
    const observer = new ResizeObserver(() => chart.resize());
    observer.observe(containerRef.current);
    return () => {
      observer.disconnect();
      chart.dispose();
    };
    // Chart inputs are passed explicitly by each caller.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, dependencies);

  return containerRef;
}

export function GapWaterfallChart({
  gap,
}: {
  gap: ReplayAnalysis["gap_analysis"];
}) {
  const labels = [
    "완전투자\n벤치마크",
    "노출·진입\n제한",
    "종목 선택·\n체결",
    "매매·환전\n비용",
    "실제 전략",
  ];
  const displayed = [
    gap.full_benchmark_return,
    gap.exposure_effect,
    gap.selection_execution_effect,
    gap.cost_effect,
    gap.actual_strategy_return,
  ];
  const bases: number[] = [];
  const heights: number[] = [];
  let cumulative = 0;
  displayed.forEach((value, index) => {
    if (index === 0 || index === displayed.length - 1) {
      bases.push(Math.min(0, value));
      heights.push(Math.abs(value));
      if (index === 0) cumulative = value;
      return;
    }
    bases.push(value >= 0 ? cumulative : cumulative + value);
    heights.push(Math.abs(value));
    cumulative += value;
  });
  const colors = displayed.map((value, index) => {
    if (index === 0) return "#23699a";
    if (index === displayed.length - 1) return "#176b48";
    return value >= 0 ? "#2f855a" : "#b54747";
  });

  const containerRef = useReplayChart(
    (container) => {
      const chart = echarts.init(container, undefined, { renderer: "canvas" });
      chart.setOption({
        animationDuration: 350,
        aria: { enabled: true },
        grid: { left: 16, right: 18, top: 24, bottom: 18, containLabel: true },
        tooltip: {
          trigger: "axis",
          axisPointer: { type: "shadow" },
          borderColor: "#bdc9c2",
          backgroundColor: "#ffffff",
          textStyle: { color: "#17201b", fontSize: 12 },
          valueFormatter: (value: unknown) =>
            typeof value === "number" ? `${(value * 100).toFixed(2)}%p` : "-",
        },
        xAxis: {
          type: "category",
          data: labels,
          axisTick: { show: false },
          axisLine: { lineStyle: { color: "#d8dfdb" } },
          axisLabel: { color: "#59665f", fontSize: 10, interval: 0 },
        },
        yAxis: {
          type: "value",
          axisLabel: {
            color: "#7a867f",
            formatter: (value: number) => `${(value * 100).toFixed(0)}%p`,
          },
          splitLine: { lineStyle: { color: "#e9eeeb", type: "dashed" } },
        },
        series: [
          {
            type: "bar",
            stack: "gap",
            silent: true,
            itemStyle: { color: "transparent" },
            emphasis: { itemStyle: { color: "transparent" } },
            data: bases,
          },
          {
            name: "성과",
            type: "bar",
            stack: "gap",
            barMaxWidth: 58,
            label: {
              show: true,
              position: "top",
              color: "#17201b",
              fontSize: 11,
              fontWeight: 700,
              formatter: (params: unknown) => {
                const index = (params as { dataIndex: number }).dataIndex;
                const sign =
                  index > 0 &&
                  index < displayed.length - 1 &&
                  displayed[index] >= 0
                    ? "+"
                    : "";
                return `${sign}${(displayed[index] * 100).toFixed(1)}%p`;
              },
            },
            data: heights.map((value, index) => ({
              value,
              itemStyle: { color: colors[index] },
            })),
          },
        ],
      });
      return chart;
    },
    [gap],
  );

  return <div ref={containerRef} className="analysis-chart" />;
}

export function MonthlyReturnHeatmap({
  periods,
}: {
  periods: ReplayAnalysis["monthly_periods"];
}) {
  const years = [...new Set(periods.map((row) => row.period.slice(0, 4)))];
  const months = Array.from({ length: 12 }, (_, index) => `${index + 1}월`);
  const values = periods.map((row) => ({
    value: [Number(row.period.slice(5, 7)) - 1, years.indexOf(row.period.slice(0, 4)), row.strategy_return],
    benchmark: row.benchmark_return,
    excess: row.excess_return,
  }));
  const maxAbsolute = Math.max(
    0.05,
    ...periods.map((row) => Math.abs(row.strategy_return)),
  );

  const containerRef = useReplayChart(
    (container) => {
      const chart = echarts.init(container, undefined, { renderer: "canvas" });
      chart.setOption({
        animationDuration: 250,
        aria: { enabled: true },
        grid: { left: 10, right: 16, top: 10, bottom: 42, containLabel: true },
        tooltip: {
          borderColor: "#bdc9c2",
          backgroundColor: "#ffffff",
          textStyle: { color: "#17201b", fontSize: 12 },
          formatter: (params: unknown) => {
            const item = params as {
              data: { value: [number, number, number]; benchmark: number; excess: number };
            };
            const [month, year, strategy] = item.data.value;
            return [
              `<strong>${years[year]}년 ${months[month]}</strong>`,
              `전략 ${(strategy * 100).toFixed(2)}%`,
              `벤치마크 ${(item.data.benchmark * 100).toFixed(2)}%`,
              `차이 ${(item.data.excess * 100).toFixed(2)}%p`,
            ].join("<br>");
          },
        },
        xAxis: {
          type: "category",
          data: months,
          splitArea: { show: true },
          axisTick: { show: false },
          axisLabel: { color: "#59665f", fontSize: 10 },
        },
        yAxis: {
          type: "category",
          data: years,
          splitArea: { show: true },
          axisTick: { show: false },
          axisLabel: { color: "#59665f", fontSize: 10 },
        },
        visualMap: {
          min: -maxAbsolute,
          max: maxAbsolute,
          calculable: false,
          orient: "horizontal",
          left: "center",
          bottom: 0,
          text: ["상승", "하락"],
          textStyle: { color: "#7a867f", fontSize: 10 },
          inRange: { color: ["#c75b5b", "#f6f7f6", "#33865f"] },
        },
        series: [
          {
            name: "월 수익률",
            type: "heatmap",
            data: values,
            label: {
              show: true,
              color: "#17201b",
              fontSize: 9,
              formatter: (params: unknown) => {
                const item = params as { value: [number, number, number] };
                return `${(item.value[2] * 100).toFixed(1)}%`;
              },
            },
            itemStyle: { borderColor: "#ffffff", borderWidth: 2 },
          },
        ],
      });
      return chart;
    },
    [periods],
  );

  return <div ref={containerRef} className="analysis-chart heatmap-chart" />;
}
