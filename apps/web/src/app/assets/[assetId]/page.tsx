import type { Metadata } from "next";
import { AlertTriangle, CheckCircle2, CircleSlash2 } from "lucide-react";
import { notFound } from "next/navigation";

import { LineChart } from "@/components/line-chart";
import { PEER_LABELS } from "@/components/candidate-table";
import { DataModeBadge } from "@/components/data-mode-badge";
import { StatusBadge } from "@/components/ui/status-badge";
import { apiFetch, type AssetDetail, type ResearchStatus } from "@/lib/api";

export const metadata: Metadata = { title: "종목 상세" };

const COMPONENT_LABELS: Record<string, { label: string; max: number }> = {
  long_term_trend: { label: "장기 추세 구조", max: 30 },
  absolute_momentum: { label: "절대 모멘텀", max: 25 },
  relative_strength: { label: "상대강도", max: 20 },
  high_proximity: { label: "52주 고점 근접도", max: 10 },
  volatility_stability: { label: "변동성 안정성", max: 10 },
  trading_activity: { label: "거래활성도", max: 5 },
};

function percent(value: number | null | undefined) {
  return value == null ? "-" : `${(value * 100).toFixed(1)}%`;
}

function decimal(value: number | null | undefined, digits = 2) {
  return value == null
    ? "-"
    : value.toLocaleString("ko-KR", { maximumFractionDigits: digits });
}

export default async function AssetPage({
  params,
}: {
  params: Promise<{ assetId: string }>;
}) {
  const { assetId } = await params;
  let detail: AssetDetail;
  let researchStatus: ResearchStatus;
  try {
    [detail, researchStatus] = await Promise.all([
      apiFetch<AssetDetail>(
        `/assets/${encodeURIComponent(decodeURIComponent(assetId))}`,
      ),
      apiFetch<ResearchStatus>("/research/status"),
    ]);
  } catch {
    notFound();
  }
  const item = detail.asset;
  const components = Object.entries(item.components) as Array<[string, number]>;
  const currency = ["US_STOCK", "US_EQUITY_ETF"].includes(item.peer_group)
    ? "USD"
    : "KRW";
  const adv =
    item.adv60 == null
      ? "-"
      : new Intl.NumberFormat("ko-KR", {
          style: "currency",
          currency,
          notation: "compact",
          maximumFractionDigits: 1,
        }).format(item.adv60);

  return (
    <>
      <header className="page-header">
        <div>
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: 10,
              marginBottom: 5,
            }}
          >
            <span style={{ color: "var(--text-muted)", fontSize: 12 }}>
              {PEER_LABELS[item.peer_group]}
            </span>
            <StatusBadge state={item.state} />
          </div>
          <h1>
            {item.name}{" "}
            <span style={{ color: "var(--text-muted)", fontWeight: 600 }}>
              {item.symbol}
            </span>
          </h1>
          <p>최근 가격·50일선·200일선과 점수 판정 근거를 함께 확인합니다.</p>
        </div>
        <div className="page-meta">
          <DataModeBadge source={researchStatus.data_source} />
          <span>{item.as_of} 종가 기준</span>
        </div>
      </header>

      <section className="metric-strip">
        <div className="metric-cell">
          <span className="metric-label">추세 점수</span>
          <span className="metric-value" style={{ color: "var(--primary)" }}>
            {item.score?.toFixed(1) ?? "-"}
          </span>
          <span className="metric-detail">100점 만점 조건 충족도</span>
        </div>
        <div className="metric-cell">
          <span className="metric-label">비교군 상대 순위</span>
          <span className="metric-value">
            {item.percentile == null
              ? "-"
              : `상위 ${item.percentile.toFixed(0)}%`}
          </span>
          <span className="metric-detail">
            {PEER_LABELS[item.peer_group]} 내부
          </span>
        </div>
        <div className="metric-cell">
          <span className="metric-label">60일 평균 거래대금</span>
          <span className="metric-value">{adv}</span>
          <span className="metric-detail">시장별 최소 유동성 기준 적용</span>
        </div>
        <div className="metric-cell">
          <span className="metric-label">데이터 상태</span>
          <span className="metric-value metric-badge-value">
            <StatusBadge state={item.data_status} />
          </span>
          <span className="metric-detail">
            {item.data_status_reason ?? "최신 가격 이력 정상"}
          </span>
        </div>
      </section>

      {item.data_status_reason && (
        <div className="notice-box warning-box" style={{ marginTop: 16 }}>
          <AlertTriangle size={15} /> {item.data_status_reason}
        </div>
      )}

      <div className="section-grid">
        <section className="section-panel">
          <div className="panel-header">
            <div>
              <h2>가격과 이동평균선</h2>
              <p>수정주가 기준</p>
            </div>
          </div>
          <div className="panel-body">
            {detail.price_history.length > 0 ? (
              <LineChart
                labels={detail.price_history.map((point) => String(point.date))}
                series={[
                  {
                    name: "가격",
                    values: detail.price_history.map(
                      (point) => point.close as number | null,
                    ),
                    color: "#176b48",
                  },
                  {
                    name: "50일선",
                    values: detail.price_history.map(
                      (point) => point.sma50 as number | null,
                    ),
                    color: "#23699a",
                  },
                  {
                    name: "200일선",
                    values: detail.price_history.map(
                      (point) => point.sma200 as number | null,
                    ),
                    color: "#96630d",
                  },
                ]}
                valueFormat="number"
              />
            ) : (
              <div className="empty-state">표시할 가격 이력이 없습니다.</div>
            )}
          </div>
        </section>
        <section className="section-panel">
          <div className="panel-header">
            <div>
              <h2>점수 구성</h2>
              <p>구성점수 합계</p>
            </div>
          </div>
          <div className="panel-body" style={{ display: "grid", gap: 15 }}>
            {components.map(([key, value]) => {
              const definition = COMPONENT_LABELS[key];
              if (!definition) return null;
              return (
                <div key={key}>
                  <div
                    style={{
                      display: "flex",
                      justifyContent: "space-between",
                      gap: 12,
                      fontSize: 12,
                    }}
                  >
                    <span>{definition.label}</span>
                    <strong>
                      {value.toFixed(1)} / {definition.max}
                    </strong>
                  </div>
                  <div
                    style={{
                      height: 6,
                      background: "var(--surface-strong)",
                      marginTop: 7,
                      borderRadius: 3,
                    }}
                  >
                    <div
                      style={{
                        width: `${Math.min(100, (value / definition.max) * 100)}%`,
                        height: "100%",
                        background: "var(--primary)",
                        borderRadius: 3,
                      }}
                    />
                  </div>
                </div>
              );
            })}
          </div>
        </section>
      </div>

      <div className="section-grid">
        <section className="section-panel">
          <div className="panel-header">
            <div>
              <h2>점수 이력</h2>
              <p>후보 상태 변화를 포함합니다.</p>
            </div>
          </div>
          <div className="panel-body">
            {detail.score_history.length > 0 ? (
              <LineChart
                compact
                labels={detail.score_history.map((point) => String(point.date))}
                series={[
                  {
                    name: "추세 점수",
                    values: detail.score_history.map(
                      (point) => point.score as number | null,
                    ),
                    color: "#23699a",
                    area: true,
                  },
                ]}
                valueFormat="score"
              />
            ) : (
              <div className="empty-state">표시할 점수 이력이 없습니다.</div>
            )}
          </div>
        </section>
        <section className="section-panel">
          <div className="panel-header">
            <div>
              <h2>판정 근거</h2>
              <p>점수와 경고를 분리합니다.</p>
            </div>
          </div>
          <div className="panel-body" style={{ display: "grid", gap: 16 }}>
            {item.reasons.length > 0 && (
              <div>
                <strong
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: 7,
                    fontSize: 12,
                  }}
                >
                  <CheckCircle2 size={16} color="#176b48" /> 충족 조건
                </strong>
                <ul className="stack-list" style={{ marginTop: 10 }}>
                  {item.reasons.map((reason) => (
                    <li key={reason}>{reason}</li>
                  ))}
                </ul>
              </div>
            )}
            {item.warnings.length > 0 && (
              <div className="notice-box warning-box">
                <strong
                  style={{ display: "flex", alignItems: "center", gap: 7 }}
                >
                  <AlertTriangle size={15} /> 주의사항
                </strong>
                <ul className="stack-list" style={{ marginTop: 9 }}>
                  {item.warnings.map((warning) => (
                    <li key={warning}>{warning}</li>
                  ))}
                </ul>
              </div>
            )}
            {item.exclusions.length > 0 && (
              <div className="notice-box danger-box">
                <strong
                  style={{ display: "flex", alignItems: "center", gap: 7 }}
                >
                  <CircleSlash2 size={15} /> 제외 사유
                </strong>
                <ul className="stack-list" style={{ marginTop: 9 }}>
                  {item.exclusions.map((exclusion) => (
                    <li key={exclusion}>{exclusion}</li>
                  ))}
                </ul>
              </div>
            )}
            {item.reasons.length === 0 &&
              item.warnings.length === 0 &&
              item.exclusions.length === 0 && (
                <div className="empty-state compact-empty">
                  현재 생성된 판정 문장이 없습니다.
                </div>
              )}
          </div>
        </section>
      </div>

      <section className="section-panel" style={{ marginTop: 20 }}>
        <div className="panel-header">
          <div>
            <h2>점수 계산 추적</h2>
            <p>최종 점수에 사용한 원시 지표와 자격 조건입니다.</p>
          </div>
          {detail.score_trace && (
            <span className="trace-version">
              {detail.score_trace.score_version} ·{" "}
              {detail.score_trace.score_config_hash.slice(0, 10)}
            </span>
          )}
        </div>
        {detail.score_trace ? (
          <div className="score-trace-grid">
            <dl className="trace-values">
              <div>
                <dt>종가 / 수정주가</dt>
                <dd>
                  {decimal(detail.score_trace.close)} /{" "}
                  {decimal(detail.score_trace.adjusted_close)}
                </dd>
              </div>
              <div>
                <dt>50일선 / 200일선</dt>
                <dd>
                  {decimal(detail.score_trace.sma50)} /{" "}
                  {decimal(detail.score_trace.sma200)}
                </dd>
              </div>
              <div>
                <dt>3개월 수익률</dt>
                <dd>{percent(detail.score_trace.r63)}</dd>
              </div>
              <div>
                <dt>6개월 수익률</dt>
                <dd>{percent(detail.score_trace.r126)}</dd>
              </div>
              <div>
                <dt>12개월-1개월 수익률</dt>
                <dd>{percent(detail.score_trace.r12_1)}</dd>
              </div>
              <div>
                <dt>52주 고점 대비</dt>
                <dd>{percent(detail.score_trace.high_ratio)}</dd>
              </div>
              <div>
                <dt>60일 연환산 변동성</dt>
                <dd>{percent(detail.score_trace.vol60)}</dd>
              </div>
              <div>
                <dt>60일 평균 거래대금</dt>
                <dd>{adv}</dd>
              </div>
              <div>
                <dt>상대강도 백분위</dt>
                <dd>{percent(detail.score_trace.relative_strength_rank)}</dd>
              </div>
              <div>
                <dt>변동성 백분위</dt>
                <dd>{percent(detail.score_trace.volatility_rank)}</dd>
              </div>
              <div>
                <dt>거래활성도 백분위</dt>
                <dd>{percent(detail.score_trace.activity_rank)}</dd>
              </div>
              <div>
                <dt>구성점수 합계 / 최종</dt>
                <dd>
                  {detail.score_trace.component_sum.toFixed(1)} /{" "}
                  {detail.score_trace.final_score?.toFixed(1) ?? "-"}
                </dd>
              </div>
            </dl>
            <div className="trace-eligibility">
              <h3>자격 조건</h3>
              <div>
                <span>가격 이력과 데이터 완전성</span>
                <StatusBadge
                  state={detail.score_trace.data_eligible ? "PASS" : "FAIL"}
                />
              </div>
              <div>
                <span>시장별 최소 거래대금</span>
                <StatusBadge
                  state={
                    detail.score_trace.absolute_liquidity_eligible
                      ? "PASS"
                      : "FAIL"
                  }
                />
              </div>
              <div>
                <span>예정 주문의 거래대금 비중</span>
                <StatusBadge
                  state={
                    detail.score_trace.order_size_eligible ? "PASS" : "FAIL"
                  }
                />
              </div>
              <div>
                <span>공식 후보 필수 조건</span>
                <StatusBadge
                  state={
                    detail.score_trace.candidate_eligible ? "PASS" : "FAIL"
                  }
                />
              </div>
            </div>
          </div>
        ) : (
          <div className="empty-state">
            데이터 오류 또는 이력 부족으로 계산 추적을 만들 수 없습니다.
          </div>
        )}
      </section>
      <div className="notice-box" style={{ marginTop: 16 }}>
        추세 점수는 투자 추천 또는 상승 확률이 아닙니다.
      </div>
    </>
  );
}
