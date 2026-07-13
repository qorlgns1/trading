"use client";

import {
  ArrowLeft,
  ArrowRight,
  CalendarRange,
  Check,
  CircleHelp,
  Gauge,
  LoaderCircle,
  ShieldCheck,
  SlidersHorizontal,
  WalletCards,
} from "lucide-react";
import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  toBasisPoints,
  updateAllocation,
  type AllocationKey,
  type Allocations,
} from "@/lib/allocations";
import {
  apiFetch,
  type ReplayExperiment,
  type ReplayOptions,
  type ReplayStrategy,
} from "@/lib/api";
import { rebalanceBasisPointWeights } from "@/lib/replay-strategy";
import { cn } from "@/lib/utils";

type Objective = "RETURN" | "DRAWDOWN" | "COST" | "BALANCED";
type Criteria = {
  minimum_cagr_improvement_pp: number;
  minimum_mdd_improvement_pp: number;
  minimum_cost_reduction_ratio: number;
  minimum_sharpe_improvement: number;
  maximum_cagr_degradation_pp: number;
  maximum_mdd_degradation_pp: number;
};

const STEPS = [
  ["목적", Gauge],
  ["기간", CalendarRange],
  ["점수", SlidersHorizontal],
  ["포트폴리오", WalletCards],
  ["위험·체결", ShieldCheck],
  ["확인", Check],
] as const;

const OBJECTIVES: Array<{ id: Objective; label: string; detail: string }> = [
  { id: "RETURN", label: "수익 증가", detail: "검증 CAGR 개선" },
  { id: "DRAWDOWN", label: "낙폭 감소", detail: "큰 하락 폭 축소" },
  { id: "COST", label: "비용 감소", detail: "거래 비용과 회전율 축소" },
  { id: "BALANCED", label: "균형", detail: "수익과 위험을 함께 비교" },
];

const PEER_GROUPS = [
  ["US_STOCK", "미국 주식", "us_stock"],
  ["US_EQUITY_ETF", "미국 ETF", "us_equity_etf"],
  ["KR_KOSPI", "KOSPI", "kr_kospi"],
  ["KR_KOSDAQ", "KOSDAQ", "kr_kosdaq"],
  ["KR_DOMESTIC_EQUITY_ETF", "국내 주식형 ETF", "kr_domestic_equity_etf"],
  ["KR_OVERSEAS_EQUITY_ETF", "해외형 한국 ETF", "kr_overseas_equity_etf"],
] as const;

const SCORE_COMPONENTS = [
  ["long_term_trend", "장기 추세"],
  ["absolute_momentum", "절대 모멘텀"],
  ["relative_strength", "상대 강도"],
  ["high_proximity", "고점 근접도"],
  ["volatility_stability", "변동성 안정성"],
  ["trading_activity", "거래 활동"],
] as const;

const SCORE_HELP: Record<string, string> = {
  long_term_trend:
    "현재 가격이 200일 이동평균보다 얼마나 높은지 봅니다. 예: 200일선보다 15% 이상 높으면 이 항목의 원점수는 만점입니다.",
  absolute_momentum:
    "최근 3·6·12개월 수익률이 모두 양수인지와 상승 폭을 봅니다. 시장과 무관하게 스스로 오르고 있는지 확인합니다.",
  relative_strength:
    "같은 비교군 안에서 최근 수익률 순위를 계산합니다. 예: 상위 10% 종목은 하위 종목보다 높은 점수를 받습니다.",
  high_proximity:
    "현재 수정주가를 최근 52주 최고 수정주가로 나눕니다. 80% 이하는 0점, 95% 이상은 이 항목 만점입니다.",
  volatility_stability:
    "최근 60거래일 변동성이 같은 비교군에서 낮을수록 높은 점수를 줍니다.",
  trading_activity:
    "최근 거래대금이 평소보다 활발한지 같은 비교군 안에서 비교합니다.",
};

const SCORE_PRESETS = [
  {
    id: "BASE",
    label: "기본",
    entry: 65,
    exit: 60,
    detail: "후보를 넓게 잡지만 점수 변화에 따른 거래가 늘 수 있습니다.",
  },
  {
    id: "SELECTIVE",
    label: "선별 진입",
    entry: 80,
    exit: 60,
    detail: "강한 후보만 사고 약해질 때까지 오래 보유합니다.",
  },
  {
    id: "STRICT",
    label: "매우 엄격",
    entry: 90,
    exit: 80,
    detail: "후보 수는 줄지만 고점 진입과 잦은 해제가 생길 수 있습니다.",
  },
] as const;

const ALLOCATION_LABELS: Record<AllocationKey, string> = {
  us_stock: "미국 주식",
  kr_stock: "한국 주식",
  us_etf: "미국 ETF",
  kr_etf: "한국 ETF",
};

function defaultCriteria(objective: Objective): Criteria {
  const empty: Criteria = {
    minimum_cagr_improvement_pp: 0,
    minimum_mdd_improvement_pp: 0,
    minimum_cost_reduction_ratio: 0,
    minimum_sharpe_improvement: 0,
    maximum_cagr_degradation_pp: 0,
    maximum_mdd_degradation_pp: 0,
  };
  if (objective === "RETURN")
    return {
      ...empty,
      minimum_cagr_improvement_pp: 0.5,
      maximum_mdd_degradation_pp: 2,
    };
  if (objective === "DRAWDOWN")
    return {
      ...empty,
      minimum_mdd_improvement_pp: 2,
      maximum_cagr_degradation_pp: 1,
    };
  if (objective === "COST")
    return {
      ...empty,
      minimum_cost_reduction_ratio: 0.1,
      maximum_cagr_degradation_pp: 1,
    };
  return {
    ...empty,
    minimum_sharpe_improvement: 0.1,
    maximum_cagr_degradation_pp: 0.5,
    maximum_mdd_degradation_pp: 1,
  };
}

function cloneStrategy(strategy: ReplayStrategy): ReplayStrategy {
  return structuredClone(strategy);
}

function completeStrategy(strategy: ReplayStrategy): Required<ReplayStrategy> {
  return strategy as Required<ReplayStrategy>;
}

export function ReplayExperimentBuilder({
  options,
  existingExperiment,
  initialStrategy,
  suggestedName,
}: {
  options: ReplayOptions;
  existingExperiment?: ReplayExperiment;
  initialStrategy?: ReplayStrategy;
  suggestedName?: string;
}) {
  const router = useRouter();
  const initial = useMemo(() => {
    if (initialStrategy) return cloneStrategy(initialStrategy);
    if (!existingExperiment) return cloneStrategy(options.default_strategy);
    const baseline = (existingExperiment.runs ?? []).find(
      (run) => run.role === "BASELINE",
    );
    const config = baseline?.config as
      | { strategy?: ReplayStrategy }
      | undefined;
    return cloneStrategy(config?.strategy ?? options.default_strategy);
  }, [existingExperiment, initialStrategy, options.default_strategy]);
  const [step, setStep] = useState(0);
  const [strategy, setStrategy] = useState<ReplayStrategy>(initial);
  const [name, setName] = useState(
    suggestedName ??
      (existingExperiment ? "새 도전 전략" : "엄격한 진입 기준 비교"),
  );
  const [hypothesis, setHypothesis] = useState(
    existingExperiment
      ? existingExperiment.hypothesis
      : "진입 기준을 높이면 낙폭이 줄어드는가?",
  );
  const [objective, setObjective] = useState<Objective>(
    (existingExperiment?.objective as Objective | undefined) ?? "BALANCED",
  );
  const [criteria, setCriteria] = useState<Criteria>(
    defaultCriteria(objective),
  );
  const [allocations, setAllocations] = useState<Allocations>(() => {
    const weights = completeStrategy(initial).portfolio.sleeve_weights_bps!;
    return {
      us_stock: weights.us_stock / 100,
      kr_stock: weights.kr_stock / 100,
      us_etf: weights.us_etf / 100,
      kr_etf: weights.kr_etf / 100,
    };
  });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const full = completeStrategy(strategy);

  function mutate(mutator: (draft: Required<ReplayStrategy>) => void) {
    setStrategy((current) => {
      const next = completeStrategy(cloneStrategy(current));
      mutator(next);
      return next;
    });
  }

  function changeObjective(next: Objective) {
    setObjective(next);
    setCriteria(defaultCriteria(next));
  }

  function changeScoreWeight(key: string, value: number) {
    mutate((draft) => {
      const weights = draft.signal.component_weights_bps! as Record<
        string,
        number
      >;
      draft.signal.component_weights_bps = rebalanceBasisPointWeights(
        weights,
        key,
        value,
      ) as typeof draft.signal.component_weights_bps;
    });
  }

  function changeAllocation(key: AllocationKey, value: number) {
    const next = updateAllocation(allocations, key, value);
    setAllocations(next);
    mutate((draft) => {
      draft.portfolio.sleeve_weights_bps = toBasisPoints(next);
    });
  }

  async function submit() {
    if (validationIssues.length > 0) {
      setError(validationIssues[0]);
      return;
    }
    setBusy(true);
    setError(null);
    try {
      if (existingExperiment) {
        await apiFetch(
          `/research/experiments/${existingExperiment.experiment_id}/runs`,
          {
            method: "POST",
            body: JSON.stringify({ label: name, role: "CHALLENGER", strategy }),
          },
        );
        router.push(`/replays/experiments/${existingExperiment.experiment_id}`);
      } else {
        const created = await apiFetch<ReplayExperiment>(
          "/research/experiments",
          {
            method: "POST",
            body: JSON.stringify({
              name,
              hypothesis,
              objective,
              success_criteria: criteria,
              baseline_label: "기준 전략",
              baseline_strategy: strategy,
            }),
          },
        );
        router.push(`/replays/experiments/${created.experiment_id}`);
      }
      router.refresh();
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "실험을 저장하지 못했습니다.",
      );
      setBusy(false);
    }
  }

  const dateValid =
    full.data.start_date < full.data.split_date &&
    full.data.split_date < full.data.end_date;
  const enabledPeers = new Set(full.data.peer_groups ?? []);
  const slots = full.portfolio.peer_group_slots as Record<string, number>;
  const enabledSlotTotal = PEER_GROUPS.reduce(
    (sum, [id, , key]) => sum + (enabledPeers.has(id) ? slots[key] : 0),
    0,
  );
  const validationIssues: string[] = [];
  const daysBetween = (start: string, end: string) =>
    (Date.parse(end) - Date.parse(start)) / 86_400_000;
  if (!name.trim()) validationIssues.push("실험 또는 전략 이름을 입력하세요.");
  if (!existingExperiment && !hypothesis.trim())
    validationIssues.push("결과로 확인할 질문을 입력하세요.");
  if (!dateValid)
    validationIssues.push("시작일, 분할일, 종료일 순서를 확인하세요.");
  if (
    dateValid &&
    (daysBetween(full.data.start_date, full.data.split_date) < 330 ||
      daysBetween(full.data.split_date, full.data.end_date) < 330)
  )
    validationIssues.push("학습과 검증 구간은 각각 최소 약 1년이어야 합니다.");
  if (enabledPeers.size === 0)
    validationIssues.push("비교군을 한 개 이상 선택하세요.");
  if (enabledSlotTotal < 1 || enabledSlotTotal > 30)
    validationIssues.push("선택한 비교군의 전체 슬롯은 1~30개여야 합니다.");
  const peerSleeves: Record<AllocationKey, string[]> = {
    us_stock: ["US_STOCK"],
    kr_stock: ["KR_KOSPI", "KR_KOSDAQ"],
    us_etf: ["US_EQUITY_ETF"],
    kr_etf: ["KR_DOMESTIC_EQUITY_ETF", "KR_OVERSEAS_EQUITY_ETF"],
  };
  for (const key of Object.keys(peerSleeves) as AllocationKey[]) {
    const availableSlots = PEER_GROUPS.filter(([id]) =>
      peerSleeves[key].includes(id),
    ).reduce(
      (sum, [id, , slotKey]) =>
        sum + (enabledPeers.has(id) ? slots[slotKey] : 0),
      0,
    );
    if (allocations[key] > 0 && availableSlots === 0)
      validationIssues.push(
        `${ALLOCATION_LABELS[key]} 비중이 있으면 해당 비교군과 슬롯이 필요합니다.`,
      );
  }
  if (full.signal.exit_score > full.signal.entry_score - 5)
    validationIssues.push("해제 점수는 진입 점수보다 5점 이상 낮아야 합니다.");
  for (const [peer, threshold] of Object.entries(
    full.signal.peer_overrides ?? {},
  )) {
    if (threshold.exit_score > threshold.entry_score - 5)
      validationIssues.push(`${peer}의 진입·해제 점수 간격을 확인하세요.`);
  }
  return (
    <>
      <header className="page-header builder-page-header">
        <div>
          <h1>{existingExperiment ? "도전 전략 추가" : "새 전략 실험"}</h1>
          <p>
            {existingExperiment?.name ??
              "기준 전략의 가설과 검증 조건을 함께 저장합니다."}
          </p>
        </div>
        <span className="demo-chip real-data-chip">
          {options.data_version.slice(0, 18)}
        </span>
      </header>

      <ol className="builder-steps" aria-label="전략 설정 단계">
        {STEPS.map(([label, Icon], index) => (
          <li
            key={label}
            className={cn(
              index === step && "builder-step-active",
              index < step && "builder-step-done",
            )}
          >
            <button type="button" onClick={() => setStep(index)}>
              <Icon size={16} /> <span>{label}</span>
            </button>
          </li>
        ))}
      </ol>

      <section className="section-panel strategy-builder-panel">
        {step === 0 && (
          <div className="panel-body builder-body">
            <div className="builder-heading">
              <h2>실험 목적</h2>
              <p>결과를 보기 전에 성공 기준을 고정합니다.</p>
            </div>
            <div className="form-grid two-column-form">
              <label className="field">
                <span>{existingExperiment ? "전략 이름" : "실험 이름"}</span>
                <input
                  value={name}
                  onChange={(event) => setName(event.target.value)}
                  maxLength={120}
                />
              </label>
              {!existingExperiment && (
                <label className="field">
                  <span>검증할 질문</span>
                  <input
                    value={hypothesis}
                    onChange={(event) => setHypothesis(event.target.value)}
                    maxLength={500}
                  />
                </label>
              )}
            </div>
            {!existingExperiment && (
              <>
                <div className="segmented-grid objective-grid">
                  {OBJECTIVES.map((item) => (
                    <button
                      key={item.id}
                      type="button"
                      className={cn(
                        "segment-option",
                        objective === item.id && "segment-option-active",
                      )}
                      onClick={() => changeObjective(item.id)}
                    >
                      <strong>{item.label}</strong>
                      <span>{item.detail}</span>
                    </button>
                  ))}
                </div>
                <div className="criteria-row">
                  {objective === "RETURN" && (
                    <NumberField
                      label="CAGR 최소 개선"
                      value={criteria.minimum_cagr_improvement_pp}
                      suffix="%p"
                      step={0.1}
                      onChange={(value) =>
                        setCriteria({
                          ...criteria,
                          minimum_cagr_improvement_pp: value,
                        })
                      }
                    />
                  )}
                  {objective === "DRAWDOWN" && (
                    <NumberField
                      label="MDD 최소 개선"
                      value={criteria.minimum_mdd_improvement_pp}
                      suffix="%p"
                      step={0.5}
                      onChange={(value) =>
                        setCriteria({
                          ...criteria,
                          minimum_mdd_improvement_pp: value,
                        })
                      }
                    />
                  )}
                  {objective === "COST" && (
                    <NumberField
                      label="비용 최소 감소"
                      value={criteria.minimum_cost_reduction_ratio * 100}
                      suffix="%"
                      step={1}
                      onChange={(value) =>
                        setCriteria({
                          ...criteria,
                          minimum_cost_reduction_ratio: value / 100,
                        })
                      }
                    />
                  )}
                  {objective === "BALANCED" && (
                    <NumberField
                      label="Sharpe 최소 개선"
                      value={criteria.minimum_sharpe_improvement}
                      step={0.05}
                      onChange={(value) =>
                        setCriteria({
                          ...criteria,
                          minimum_sharpe_improvement: value,
                        })
                      }
                    />
                  )}
                  <NumberField
                    label="허용 CAGR 감소"
                    value={criteria.maximum_cagr_degradation_pp}
                    suffix="%p"
                    step={0.5}
                    onChange={(value) =>
                      setCriteria({
                        ...criteria,
                        maximum_cagr_degradation_pp: value,
                      })
                    }
                  />
                  <NumberField
                    label="허용 MDD 악화"
                    value={criteria.maximum_mdd_degradation_pp}
                    suffix="%p"
                    step={0.5}
                    onChange={(value) =>
                      setCriteria({
                        ...criteria,
                        maximum_mdd_degradation_pp: value,
                      })
                    }
                  />
                </div>
              </>
            )}
          </div>
        )}

        {step === 1 && (
          <div className="panel-body builder-body">
            <div className="builder-heading">
              <h2>데이터와 기간</h2>
              <p>분할일 이전은 학습, 이후는 검증으로 계산합니다.</p>
            </div>
            <div className="form-grid three-column-form">
              {(["start_date", "split_date", "end_date"] as const).map(
                (key) => (
                  <label className="field" key={key}>
                    <span>
                      {
                        {
                          start_date: "시작일",
                          split_date: "검증 분할일",
                          end_date: "종료일",
                        }[key]
                      }
                    </span>
                    <input
                      type="date"
                      value={full.data[key]}
                      min={options.raw_history_start}
                      max={options.raw_history_end}
                      onChange={(event) =>
                        mutate((draft) => {
                          draft.data[key] = event.target.value;
                        })
                      }
                    />
                  </label>
                ),
              )}
            </div>
            {!dateValid && (
              <div className="inline-error">
                시작일, 검증 분할일, 종료일 순서를 확인하세요.
              </div>
            )}
            <div className="peer-selector">
              {PEER_GROUPS.map(([id, label]) => {
                const checked = (full.data.peer_groups ?? []).includes(id);
                return (
                  <label
                    key={id}
                    className={cn(
                      "check-option",
                      checked && "check-option-active",
                    )}
                  >
                    <input
                      type="checkbox"
                      checked={checked}
                      onChange={(event) =>
                        mutate((draft) => {
                          const set = new Set(draft.data.peer_groups ?? []);
                          if (event.target.checked) set.add(id);
                          else set.delete(id);
                          draft.data.peer_groups = Array.from(
                            set,
                          ) as typeof draft.data.peer_groups;
                        })
                      }
                    />
                    <span>{label}</span>
                  </label>
                );
              })}
            </div>
            <div className="segmented-control compact-segments">
              <button
                type="button"
                className={cn(
                  full.data.universe_mode === "CURRENT_LISTED" &&
                    "segment-active",
                )}
                onClick={() =>
                  mutate((draft) => {
                    draft.data.universe_mode = "CURRENT_LISTED";
                  })
                }
              >
                현재 상장 종목
              </button>
              <button
                type="button"
                disabled={!options.supports_point_in_time}
                title="역사 종목군 데이터가 준비되면 활성화됩니다."
                className={cn(
                  full.data.universe_mode === "POINT_IN_TIME" &&
                    "segment-active",
                )}
                onClick={() =>
                  mutate((draft) => {
                    draft.data.universe_mode = "POINT_IN_TIME";
                  })
                }
              >
                시점 기준 종목군
              </button>
            </div>
            <div className="notice-box neutral-box compact-notice">
              성과 시작 전 253거래일은 이동평균과 점수 계산용 준비 구간으로 자동
              사용됩니다.
            </div>
          </div>
        )}

        {step === 2 && (
          <div className="panel-body builder-body">
            <div className="builder-heading">
              <h2>후보 점수</h2>
              <p>
                진입과 해제 사이의 간격이 작을수록 거래가 잦아질 수 있습니다.
              </p>
            </div>
            <div className="strategy-preset-grid">
              {SCORE_PRESETS.map((preset) => (
                <button
                  type="button"
                  key={preset.id}
                  className={cn(
                    "strategy-preset",
                    full.signal.entry_score === preset.entry &&
                      full.signal.exit_score === preset.exit &&
                      "strategy-preset-active",
                  )}
                  onClick={() =>
                    mutate((draft) => {
                      draft.signal.entry_score = preset.entry;
                      draft.signal.exit_score = preset.exit;
                    })
                  }
                >
                  <strong>{preset.label}</strong>
                  <span>
                    {preset.entry}점 진입 · {preset.exit}점 해제
                  </span>
                  <small>{preset.detail}</small>
                </button>
              ))}
            </div>
            <div className="score-threshold-grid">
              <RangeField
                label="진입 점수"
                help="이 점수 이상인 공식 후보만 새로 매수합니다. 기준을 높이면 후보가 줄고 현금 비중이 커질 수 있습니다."
                value={full.signal.entry_score}
                min={50}
                max={100}
                step={5}
                suffix="점"
                onChange={(value) =>
                  mutate((draft) => {
                    draft.signal.entry_score = value;
                    if (draft.signal.exit_score > value - 5)
                      draft.signal.exit_score = Math.max(50, value - 5);
                  })
                }
              />
              <RangeField
                label="해제 점수"
                help="보유 종목이 이 점수보다 낮아지면 다음 시가에 매도합니다. 진입 점수보다 낮게 두어 작은 흔들림에 바로 팔지 않게 합니다."
                value={full.signal.exit_score}
                min={50}
                max={95}
                step={5}
                suffix="점"
                onChange={(value) =>
                  mutate((draft) => {
                    draft.signal.exit_score = Math.min(
                      value,
                      draft.signal.entry_score - 5,
                    );
                  })
                }
              />
            </div>
            {full.signal.entry_score - full.signal.exit_score <= 10 && (
              <div className="notice-box warning-box compact-notice">
                점수 간격이 {full.signal.entry_score - full.signal.exit_score}
                점이라 작은 점수 변화에도 거래가 늘 수 있습니다.
              </div>
            )}
            <div className="weight-list">
              {SCORE_COMPONENTS.map(([key, label]) => {
                const value = (
                  full.signal.component_weights_bps as Record<string, number>
                )[key];
                return (
                  <RangeField
                    key={key}
                    label={label}
                    help={SCORE_HELP[key]}
                    value={value / 100}
                    min={0}
                    max={100}
                    step={5}
                    suffix="%"
                    onChange={(next) => changeScoreWeight(key, next * 100)}
                  />
                );
              })}
            </div>
            <details className="advanced-settings">
              <summary>필수 조건과 비교군별 기준</summary>
              <div className="toggle-grid">
                <Toggle
                  label="200일선 위"
                  checked={full.signal.require_above_sma200}
                  onChange={(checked) =>
                    mutate((draft) => {
                      draft.signal.require_above_sma200 = checked;
                    })
                  }
                />
                <Toggle
                  label="6개월 수익률 양수"
                  checked={full.signal.require_positive_six_month}
                  onChange={(checked) =>
                    mutate((draft) => {
                      draft.signal.require_positive_six_month = checked;
                    })
                  }
                />
                <Toggle
                  label="최소 거래대금"
                  checked={full.signal.require_absolute_liquidity}
                  onChange={(checked) =>
                    mutate((draft) => {
                      draft.signal.require_absolute_liquidity = checked;
                    })
                  }
                />
                <Toggle
                  label="주문 크기 제한"
                  checked={full.signal.require_order_size_liquidity}
                  onChange={(checked) =>
                    mutate((draft) => {
                      draft.signal.require_order_size_liquidity = checked;
                    })
                  }
                />
              </div>
              <div className="advanced-row">
                <div>
                  <strong>시장 방어 규칙</strong>
                  <p>
                    비교군 벤치마크가 200일선 아래일 때 새 매수만 막고 기존 보유
                    종목은 유지합니다.
                  </p>
                </div>
                <div className="segmented-control compact-segments">
                  <button
                    type="button"
                    className={cn(
                      full.signal.market_gate_mode ===
                        "BLOCK_NEW_ENTRIES_BELOW_SMA200" && "segment-active",
                    )}
                    onClick={() =>
                      mutate((draft) => {
                        draft.signal.market_gate_mode =
                          "BLOCK_NEW_ENTRIES_BELOW_SMA200";
                      })
                    }
                  >
                    사용
                  </button>
                  <button
                    type="button"
                    className={cn(
                      full.signal.market_gate_mode === "OFF" &&
                        "segment-active",
                    )}
                    onClick={() =>
                      mutate((draft) => {
                        draft.signal.market_gate_mode = "OFF";
                      })
                    }
                  >
                    끔
                  </button>
                </div>
              </div>
              <RangeField
                label="최소 거래대금 배수"
                help="기존 시장별 최소 거래대금 기준에 곱합니다. 예: 1.2배면 미국 주식은 하루 평균 600만 달러 이상이어야 합니다."
                value={full.signal.minimum_adv_multiplier}
                min={0.5}
                max={2}
                step={0.1}
                suffix="배"
                onChange={(value) =>
                  mutate((draft) => {
                    draft.signal.minimum_adv_multiplier = value;
                  })
                }
              />
              <div className="peer-threshold-list">
                {PEER_GROUPS.map(([id, label]) => {
                  const override = full.signal.peer_overrides?.[id];
                  return (
                    <div key={id} className="peer-threshold-row">
                      <label>
                        <input
                          type="checkbox"
                          checked={Boolean(override)}
                          onChange={(event) =>
                            mutate((draft) => {
                              draft.signal.peer_overrides ??= {};
                              if (event.target.checked)
                                draft.signal.peer_overrides[id] = {
                                  entry_score: draft.signal.entry_score,
                                  exit_score: draft.signal.exit_score,
                                };
                              else delete draft.signal.peer_overrides[id];
                            })
                          }
                        />
                        {label}
                      </label>
                      {override && (
                        <>
                          <input
                            type="number"
                            min={50}
                            max={100}
                            step={5}
                            value={override.entry_score}
                            onChange={(event) =>
                              mutate((draft) => {
                                const value = Number(event.target.value);
                                draft.signal.peer_overrides![id]!.entry_score =
                                  value;
                                if (
                                  draft.signal.peer_overrides![id]!.exit_score >
                                  value - 5
                                )
                                  draft.signal.peer_overrides![id]!.exit_score =
                                    Math.max(50, value - 5);
                              })
                            }
                          />
                          <input
                            type="number"
                            min={50}
                            max={95}
                            step={5}
                            value={override.exit_score}
                            onChange={(event) =>
                              mutate((draft) => {
                                draft.signal.peer_overrides![id]!.exit_score =
                                  Math.min(
                                    Number(event.target.value),
                                    draft.signal.peer_overrides![id]!
                                      .entry_score - 5,
                                  );
                              })
                            }
                          />
                        </>
                      )}
                    </div>
                  );
                })}
              </div>
            </details>
          </div>
        )}

        {step === 3 && (
          <div className="panel-body builder-body">
            <div className="builder-heading">
              <h2>포트폴리오</h2>
              <p>자산군별 예산과 비교군별 최대 종목 수를 정합니다.</p>
            </div>
            <label className="field compact-field">
              <span>초기 자금</span>
              <input
                type="number"
                min={1000000}
                max={1000000000}
                step={1000000}
                value={full.portfolio.initial_capital_krw}
                onChange={(event) =>
                  mutate((draft) => {
                    draft.portfolio.initial_capital_krw = Number(
                      event.target.value,
                    );
                  })
                }
              />
              <small>원</small>
            </label>
            <div className="allocation-builder-grid">
              {(Object.keys(allocations) as AllocationKey[]).map((key) => (
                <RangeField
                  key={key}
                  label={ALLOCATION_LABELS[key]}
                  value={allocations[key]}
                  min={0}
                  max={100}
                  step={5}
                  suffix="%"
                  onChange={(value) => changeAllocation(key, value)}
                />
              ))}
            </div>
            <div className="slot-grid">
              {PEER_GROUPS.map(([, label, slotKey]) => (
                <label className="field" key={slotKey}>
                  <span>{label}</span>
                  <input
                    type="number"
                    min={0}
                    max={10}
                    value={
                      (
                        full.portfolio.peer_group_slots as Record<
                          string,
                          number
                        >
                      )[slotKey]
                    }
                    onChange={(event) =>
                      mutate((draft) => {
                        (
                          draft.portfolio.peer_group_slots as Record<
                            string,
                            number
                          >
                        )[slotKey] = Number(event.target.value);
                      })
                    }
                  />
                </label>
              ))}
            </div>
            <div className="form-grid two-column-form">
              <label className="field">
                <span>종목 투자금</span>
                <select
                  value={full.portfolio.position_sizing}
                  onChange={(event) =>
                    mutate((draft) => {
                      draft.portfolio.position_sizing = event.target
                        .value as typeof draft.portfolio.position_sizing;
                    })
                  }
                >
                  <option value="EQUAL_SLOT">슬롯별 균등</option>
                  <option value="INVERSE_VOLATILITY">
                    진입 시 변동성 역가중
                  </option>
                </select>
                <small>
                  역가중은 변동성이 낮은 종목에 더 큰 진입 금액을 배정하며 기존
                  보유 수량은 다시 맞추지 않습니다.
                </small>
              </label>
              <label className="field">
                <span>보유 종목 교체</span>
                <select
                  value={full.portfolio.replacement_policy}
                  onChange={(event) =>
                    mutate((draft) => {
                      draft.portfolio.replacement_policy = event.target
                        .value as typeof draft.portfolio.replacement_policy;
                    })
                  }
                >
                  <option value="FILL_VACANCIES">빈 슬롯만 채우기</option>
                  <option value="TOP_SCORE_REBALANCE">
                    더 높은 점수로 교체
                  </option>
                </select>
                <small>
                  교체를 켜면 기존 종목이 해제 기준을 통과해도 더 높은 점수
                  후보로 바꿀 수 있습니다.
                </small>
              </label>
            </div>
            {full.portfolio.replacement_policy === "TOP_SCORE_REBALANCE" && (
              <RangeField
                label="교체에 필요한 점수 차이"
                help="새 후보 점수가 보유 종목보다 이 값 이상 높을 때만 교체합니다. 차이가 작을수록 거래가 잦아집니다."
                value={full.portfolio.replacement_score_gap}
                min={0}
                max={20}
                step={1}
                suffix="점"
                onChange={(value) =>
                  mutate((draft) => {
                    draft.portfolio.replacement_score_gap = value;
                  })
                }
              />
            )}
          </div>
        )}

        {step === 4 && (
          <div className="panel-body builder-body">
            <div className="builder-heading">
              <h2>위험과 체결</h2>
              <p>모든 신호는 확인 이후 시장의 다음 시가부터 체결됩니다.</p>
            </div>
            <div className="form-grid three-column-form">
              <label className="field">
                <span>평가 주기</span>
                <select
                  value={full.execution.review_frequency}
                  onChange={(event) =>
                    mutate((draft) => {
                      draft.execution.review_frequency = event.target
                        .value as typeof draft.execution.review_frequency;
                    })
                  }
                >
                  <option value="DAILY">매일</option>
                  <option value="WEEKLY">매주</option>
                  <option value="MONTHLY">매월</option>
                </select>
              </label>
              <label className="field">
                <span>체결 지연</span>
                <select
                  value={full.execution.execution_delay_sessions}
                  onChange={(event) =>
                    mutate((draft) => {
                      draft.execution.execution_delay_sessions = Number(
                        event.target.value,
                      );
                    })
                  }
                >
                  {[1, 2, 3, 4, 5].map((value) => (
                    <option key={value} value={value}>
                      {value}거래일
                    </option>
                  ))}
                </select>
              </label>
              <NumberField
                label="슬리피지"
                value={full.execution.slippage_bps}
                suffix="bp"
                step={1}
                min={0}
                max={200}
                onChange={(value) =>
                  mutate((draft) => {
                    draft.execution.slippage_bps = value;
                  })
                }
              />
            </div>
            <div className="risk-row">
              <Toggle
                label="고정 손절"
                checked={full.risk.fixed_stop_loss != null}
                onChange={(checked) =>
                  mutate((draft) => {
                    draft.risk.fixed_stop_loss = checked ? 0.1 : null;
                  })
                }
              />
              {full.risk.fixed_stop_loss != null && (
                <RangeField
                  label="진입가 대비"
                  help="종가가 실제 진입가보다 설정 비율 이상 하락하면 다음 시장 시가에 매도합니다."
                  value={full.risk.fixed_stop_loss * 100}
                  min={5}
                  max={30}
                  step={1}
                  suffix="%"
                  onChange={(value) =>
                    mutate((draft) => {
                      draft.risk.fixed_stop_loss = value / 100;
                    })
                  }
                />
              )}
              <Toggle
                label="추적 손절"
                checked={full.risk.trailing_stop_loss != null}
                onChange={(checked) =>
                  mutate((draft) => {
                    draft.risk.trailing_stop_loss = checked ? 0.15 : null;
                  })
                }
              />
              {full.risk.trailing_stop_loss != null && (
                <RangeField
                  label="최고 종가 대비"
                  help="보유 이후 가장 높았던 종가에서 설정 비율만큼 내려오면 다음 시장 시가에 매도합니다."
                  value={full.risk.trailing_stop_loss * 100}
                  min={5}
                  max={30}
                  step={1}
                  suffix="%"
                  onChange={(value) =>
                    mutate((draft) => {
                      draft.risk.trailing_stop_loss = value / 100;
                    })
                  }
                />
              )}
            </div>
            <details className="advanced-settings">
              <summary>거래·환전 비용</summary>
              <div className="cost-grid">
                {(
                  [
                    ["us_buy_cost", "미국 매수"],
                    ["us_sell_cost", "미국 매도"],
                    ["kr_buy_cost", "한국 매수"],
                    ["kr_sell_cost", "한국 매도"],
                    ["initial_fx_cost", "최초 환전"],
                  ] as const
                ).map(([key, label]) => (
                  <NumberField
                    key={key}
                    label={label}
                    value={full.execution[key] * 100}
                    suffix="%"
                    step={0.01}
                    min={0}
                    max={1}
                    onChange={(value) =>
                      mutate((draft) => {
                        draft.execution[key] = value / 100;
                      })
                    }
                  />
                ))}
              </div>
            </details>
            <details className="advanced-settings">
              <summary>워크포워드 검증</summary>
              <div className="form-grid three-column-form">
                <NumberField
                  label="학습 길이"
                  value={full.validation.walk_forward_train_years}
                  suffix="년"
                  min={2}
                  max={5}
                  onChange={(value) =>
                    mutate((draft) => {
                      draft.validation.walk_forward_train_years = value;
                    })
                  }
                />
                <NumberField
                  label="검증 길이"
                  value={full.validation.walk_forward_test_years}
                  suffix="년"
                  min={1}
                  max={2}
                  onChange={(value) =>
                    mutate((draft) => {
                      draft.validation.walk_forward_test_years = value;
                    })
                  }
                />
                <NumberField
                  label="이동 간격"
                  value={full.validation.walk_forward_step_years}
                  suffix="년"
                  min={1}
                  max={2}
                  onChange={(value) =>
                    mutate((draft) => {
                      draft.validation.walk_forward_step_years = value;
                    })
                  }
                />
              </div>
              <p className="field-help">
                예: 3년으로 관찰한 뒤 다음 1년을 검증하고, 시작점을 1년씩 옮겨
                반복합니다. 한 시기에만 잘 맞는 전략인지 확인합니다.
              </p>
            </details>
          </div>
        )}

        {step === 5 && (
          <div className="panel-body builder-body">
            <div className="builder-heading">
              <h2>실행 전 확인</h2>
              <p>저장 후 전략 설정과 성공 기준은 변경되지 않습니다.</p>
            </div>
            <dl className="review-summary">
              <div>
                <dt>전략</dt>
                <dd>
                  진입 {full.signal.entry_score}점 · 해제{" "}
                  {full.signal.exit_score}점
                </dd>
              </div>
              <div>
                <dt>기간</dt>
                <dd>
                  {full.data.start_date}–{full.data.end_date}
                </dd>
              </div>
              <div>
                <dt>검증 시작</dt>
                <dd>{full.data.split_date}</dd>
              </div>
              <div>
                <dt>평가·체결</dt>
                <dd>
                  {full.execution.review_frequency} ·{" "}
                  {full.execution.execution_delay_sessions}거래일 뒤 시가
                </dd>
              </div>
              <div>
                <dt>초기 자금</dt>
                <dd>
                  {full.portfolio.initial_capital_krw.toLocaleString("ko-KR")}원
                </dd>
              </div>
              <div>
                <dt>워크포워드</dt>
                <dd>
                  {full.validation.walk_forward_train_years}년 학습 ·{" "}
                  {full.validation.walk_forward_test_years}년 검증
                </dd>
              </div>
            </dl>
            <div className="notice-box warning-box">
              현재 상장 종목 기준이므로 생존편향이 포함됩니다. 결과는 투자 추천
              또는 상승 확률이 아닙니다.
            </div>
            {validationIssues.length > 0 && (
              <div className="builder-validation" role="alert">
                <strong>실행 전 확인할 항목</strong>
                <ul>
                  {validationIssues.map((issue) => (
                    <li key={issue}>{issue}</li>
                  ))}
                </ul>
              </div>
            )}
            {error && (
              <div className="inline-error" role="alert">
                {error}
              </div>
            )}
          </div>
        )}

        <footer className="builder-footer">
          <Button
            variant="secondary"
            disabled={step === 0 || busy}
            onClick={() => setStep((value) => Math.max(0, value - 1))}
          >
            <ArrowLeft size={16} />
            이전
          </Button>
          {step < STEPS.length - 1 ? (
            <Button
              disabled={!dateValid}
              onClick={() =>
                setStep((value) => Math.min(STEPS.length - 1, value + 1))
              }
            >
              다음
              <ArrowRight size={16} />
            </Button>
          ) : (
            <Button
              disabled={busy || validationIssues.length > 0}
              onClick={submit}
            >
              {busy ? (
                <LoaderCircle size={16} className="spin" />
              ) : (
                <Check size={16} />
              )}
              저장하고 실행
            </Button>
          )}
        </footer>
      </section>
    </>
  );
}

function NumberField({
  label,
  value,
  onChange,
  suffix,
  step = 1,
  min,
  max,
}: {
  label: string;
  value: number;
  onChange: (value: number) => void;
  suffix?: string;
  step?: number;
  min?: number;
  max?: number;
}) {
  return (
    <label className="field number-field">
      <span>{label}</span>
      <span className="number-input-wrap">
        <input
          type="number"
          value={Number.isFinite(value) ? value : 0}
          step={step}
          min={min}
          max={max}
          onChange={(event) => onChange(Number(event.target.value))}
        />
        {suffix && <small>{suffix}</small>}
      </span>
    </label>
  );
}

function RangeField({
  label,
  value,
  min,
  max,
  step,
  suffix,
  help,
  onChange,
}: {
  label: string;
  value: number;
  min: number;
  max: number;
  step: number;
  suffix: string;
  help?: string;
  onChange: (value: number) => void;
}) {
  return (
    <label className="range-field">
      <span className="range-label">
        <span>
          {label}
          {help && (
            <span
              className="help-trigger"
              tabIndex={0}
              aria-label={`${label} 설명: ${help}`}
            >
              <CircleHelp size={13} aria-hidden="true" />
              <span className="help-tooltip" role="tooltip">
                {help}
              </span>
            </span>
          )}
        </span>
        <strong>
          {Number(value.toFixed(2))}
          {suffix}
        </strong>
      </span>
      <input
        type="range"
        aria-label={label}
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(event) => onChange(Number(event.target.value))}
      />
    </label>
  );
}

function Toggle({
  label,
  checked,
  onChange,
}: {
  label: string;
  checked: boolean;
  onChange: (checked: boolean) => void;
}) {
  return (
    <label className="toggle-control">
      <input
        type="checkbox"
        checked={checked}
        onChange={(event) => onChange(event.target.checked)}
      />
      <span className="toggle-track" aria-hidden="true">
        <span />
      </span>
      <strong>{label}</strong>
    </label>
  );
}
