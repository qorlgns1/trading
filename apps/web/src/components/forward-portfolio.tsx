"use client";

import * as Slider from "@radix-ui/react-slider";
import {
  AlertTriangle,
  Archive,
  CalendarClock,
  LoaderCircle,
  Play,
  RefreshCw,
  WalletCards,
} from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { PEER_LABELS } from "@/components/candidate-table";
import { Button } from "@/components/ui/button";
import { StatusBadge } from "@/components/ui/status-badge";
import {
  apiFetch,
  type ForwardAccount,
  type ForwardAccounts,
  type ForwardActivity,
} from "@/lib/api";
import {
  DEFAULT_ALLOCATIONS,
  toBasisPoints,
  updateAllocation,
  type AllocationKey,
  type Allocations,
} from "@/lib/allocations";
import { formatKrw, formatPercent } from "@/lib/utils";

const LABELS: Record<AllocationKey, string> = {
  us_stock: "미국 주식",
  kr_stock: "한국 주식",
  us_etf: "미국 ETF",
  kr_etf: "한국 ETF",
};

export function ForwardPortfolio() {
  const [account, setAccount] = useState<ForwardAccount | null>(null);
  const [accounts, setAccounts] = useState<ForwardAccounts | null>(null);
  const [activity, setActivity] = useState<ForwardActivity | null>(null);
  const [allocations, setAllocations] =
    useState<Allocations>(DEFAULT_ALLOCATIONS);
  const [loading, setLoading] = useState(true);
  const [working, setWorking] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const list = await apiFetch<ForwardAccounts>("/forward/accounts");
      setAccounts(list);
      const availableAccounts = list.accounts ?? [];
      const next =
        availableAccounts.find(
          (item) => item.account_id === account?.account_id,
        ) ??
        availableAccounts[0] ??
        null;
      setAccount(next);
      setActivity(
        next
          ? await apiFetch<ForwardActivity>(
              `/forward/accounts/${next.account_id}/activity?page=1&page_size=30`,
            )
          : null,
      );
    } catch (reason) {
      const message =
        reason instanceof Error
          ? reason.message
          : "계좌를 불러오지 못했습니다.";
      setError(message);
    } finally {
      setLoading(false);
    }
  }, [account?.account_id]);

  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 0);
    return () => window.clearTimeout(timer);
  }, [load]);

  async function selectAccount(next: ForwardAccount) {
    setAccount(next);
    setActivity(
      await apiFetch<ForwardActivity>(
        `/forward/accounts/${next.account_id}/activity?page=1&page_size=30`,
      ),
    );
  }

  async function createAccount() {
    setWorking(true);
    setError(null);
    try {
      const created = await apiFetch<ForwardAccount>("/forward/accounts", {
        method: "POST",
        body: JSON.stringify({
          sleeve_weights_bps: toBasisPoints(allocations),
        }),
      });
      setAccount(created);
      setAccounts((current) => ({
        total: (current?.total ?? 0) + 1,
        common_start_date: current?.common_start_date ?? null,
        accounts: [...(current?.accounts ?? []), created],
      }));
      setActivity({
        account_id: created.account_id,
        total: 0,
        page: 1,
        page_size: 30,
        total_pages: 1,
        items: [],
      });
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "포워드 계좌를 만들지 못했습니다.",
      );
    } finally {
      setWorking(false);
    }
  }

  async function archiveAccount() {
    if (!account || !window.confirm("현재 포워드 계좌를 보관 처리할까요?"))
      return;
    setWorking(true);
    try {
      await apiFetch<ForwardAccount>(
        `/forward/accounts/${account.account_id}/archive`,
        { method: "POST" },
      );
      setAccount(null);
      await load();
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "계좌를 보관하지 못했습니다.",
      );
    } finally {
      setWorking(false);
    }
  }

  async function retry() {
    if (!account) return;
    setWorking(true);
    try {
      setAccount(
        await apiFetch<ForwardAccount>(
          `/forward/accounts/${account.account_id}/retry`,
          { method: "POST" },
        ),
      );
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "포워드 처리를 재시도하지 못했습니다.",
      );
    } finally {
      setWorking(false);
    }
  }

  if (loading) {
    return (
      <section className="section-panel empty-state" aria-live="polite">
        <LoaderCircle size={28} className="spin" />
        <p>포워드 장부를 불러오고 있습니다.</p>
      </section>
    );
  }

  if (!account) {
    return (
      <>
        <header className="page-header">
          <div>
            <h1>포워드 포트폴리오</h1>
            <p>
              계좌를 만든 이후의 첫 공식 주간 평가부터 별도 장부로 추적합니다.
            </p>
          </div>
          <div className="page-meta">
            <span className="demo-chip real-data-chip">로컬 실데이터</span>
          </div>
        </header>
        {error && (
          <div className="notice-box danger-box" style={{ marginBottom: 20 }}>
            {error}
          </div>
        )}
        <div className="section-grid">
          <section className="section-panel">
            <div className="panel-header">
              <div>
                <h2>시작 비중</h2>
                <p>계좌 시작 후 변경할 수 없습니다.</p>
              </div>
              <WalletCards size={19} color="#23699a" />
            </div>
            <div className="panel-body allocation-list">
              {(Object.keys(allocations) as AllocationKey[]).map((key) => (
                <div key={key}>
                  <div className="allocation-heading">
                    <label htmlFor={`forward-${key}`}>{LABELS[key]}</label>
                    <strong>{allocations[key]}%</strong>
                  </div>
                  <Slider.Root
                    id={`forward-${key}`}
                    className="allocation-slider"
                    min={0}
                    max={100}
                    step={5}
                    value={[allocations[key]]}
                    onValueChange={([value]) =>
                      setAllocations((current) =>
                        updateAllocation(current, key, value),
                      )
                    }
                    aria-label={`${LABELS[key]} 비중`}
                  >
                    <Slider.Track className="slider-track">
                      <Slider.Range className="slider-range" />
                    </Slider.Track>
                    <Slider.Thumb className="slider-thumb" />
                  </Slider.Root>
                </div>
              ))}
              <div className="allocation-submit">
                <div>
                  <span className="metric-label">시작 자금</span>
                  <strong>50,000,000원</strong>
                </div>
                <Button onClick={createAccount} disabled={working}>
                  {working ? (
                    <LoaderCircle size={16} className="spin" />
                  ) : (
                    <Play size={16} fill="currentColor" />
                  )}
                  추적 시작
                </Button>
              </div>
            </div>
          </section>
          <section className="section-panel">
            <div className="panel-header">
              <div>
                <h2>시작 시점</h2>
                <p>과거 결과와 분리</p>
              </div>
              <CalendarClock size={19} color="#96630d" />
            </div>
            <div className="panel-body">
              <dl className="rule-list">
                <div>
                  <dt>첫 기록</dt>
                  <dd>현재 후보를 기준선으로 저장</dd>
                </div>
                <div>
                  <dt>첫 주문</dt>
                  <dd>다음 공식 주간 평가</dd>
                </div>
                <div>
                  <dt>체결</dt>
                  <dd>각 시장의 다음 정상 시가</dd>
                </div>
                <div>
                  <dt>데이터 오류</dt>
                  <dd>자동 매도 대신 검토 보류</dd>
                </div>
              </dl>
            </div>
          </section>
        </div>
        <div className="notice-box neutral-box" style={{ marginTop: 16 }}>
          실제 주문을 전송하지 않는 개인 연구용 장부이며 투자 추천이 아닙니다.
        </div>
      </>
    );
  }

  const exposure =
    account.current_value_krw > 0
      ? account.invested_krw / account.current_value_krw
      : 0;
  const positions = account.positions ?? [];
  const pendingOrders = account.pending_orders ?? [];
  const marketDates = account.market_dates ?? {};
  const strategySlots = (
    account.strategy_config as
      | { portfolio?: { peer_group_slots?: Record<string, number> } }
      | null
      | undefined
  )?.portfolio?.peer_group_slots;
  const maximumPositions = strategySlots
    ? Object.values(strategySlots).reduce((sum, value) => sum + value, 0)
    : 12;
  const commonMetrics = account.common_period_metrics as
    | {
        start_date?: string;
        cumulative_return?: number;
        max_drawdown?: number;
        observation_count?: number;
      }
    | null
    | undefined;
  return (
    <>
      <header className="page-header">
        <div>
          <div style={{ marginBottom: 7 }}>
            <StatusBadge state={account.status} />
          </div>
          <h1>{account.name}</h1>
          <p>
            {account.account_type === "BASELINE" ? "기준" : "실험"} 포워드 계좌
            · 계좌 생성 이후 기록만 누적
          </p>
        </div>
        <div className="page-actions">
          {account.status === "ERROR" && (
            <Button variant="secondary" onClick={retry} disabled={working}>
              <RefreshCw size={15} /> 재시도
            </Button>
          )}
          {account.status !== "ARCHIVED" ? (
            <Button
              variant="secondary"
              onClick={archiveAccount}
              disabled={working}
            >
              <Archive size={15} /> 보관
            </Button>
          ) : (
            <Button variant="secondary" onClick={() => setAccount(null)}>
              새 계좌 준비
            </Button>
          )}
          <button
            className="icon-button"
            type="button"
            title="새로고침"
            aria-label="새로고침"
            onClick={() => load()}
            disabled={working}
          >
            <RefreshCw size={17} />
          </button>
        </div>
      </header>

      {(accounts?.accounts?.length ?? 0) > 1 && (
        <>
          <nav className="forward-account-tabs" aria-label="포워드 계좌">
            {(accounts?.accounts ?? []).map((item) => (
              <button
                type="button"
                key={item.account_id}
                className={
                  item.account_id === account.account_id ? "active" : undefined
                }
                onClick={() => void selectAccount(item)}
              >
                <span>
                  {item.account_type === "BASELINE" ? "기준" : "실험"}
                </span>
                <strong>{item.name}</strong>
              </button>
            ))}
          </nav>
          {commonMetrics && (
            <div className="forward-common-period">
              <span>공통 비교 기간 · {commonMetrics.start_date}</span>
              <strong>
                수익률 {formatPercent(commonMetrics.cumulative_return ?? 0)}
              </strong>
              <span>
                MDD {formatPercent(commonMetrics.max_drawdown ?? 0)} · 관측{" "}
                {commonMetrics.observation_count ?? 0}일
              </span>
            </div>
          )}
        </>
      )}

      {account.status === "WAITING_FOR_REVIEW" && (
        <div className="notice-box warning-box" style={{ marginBottom: 20 }}>
          <CalendarClock size={15} /> 기준선은 저장됐습니다. 계좌 생성 이후 첫
          공식 주간 평가가 끝나면 환전과 주문을 시작합니다.
        </div>
      )}
      {account.status === "REVIEW_REQUIRED" && (
        <div className="notice-box danger-box" style={{ marginBottom: 20 }}>
          <AlertTriangle size={15} /> 데이터 확인이 필요한 보유 종목은 자동
          매도하지 않고 마지막 정상 가격으로 평가합니다.
        </div>
      )}
      {error && (
        <div className="notice-box danger-box" style={{ marginBottom: 20 }}>
          {error}
        </div>
      )}

      <section className="metric-strip">
        <div className="metric-cell">
          <span className="metric-label">현재 평가액</span>
          <span className="metric-value">
            {formatKrw(account.current_value_krw)}
          </span>
        </div>
        <div className="metric-cell">
          <span className="metric-label">누적 수익률</span>
          <span className="metric-value">
            {formatPercent(account.cumulative_return)}
          </span>
        </div>
        <div className="metric-cell">
          <span className="metric-label">최대 낙폭</span>
          <span className="metric-value">
            {formatPercent(account.max_drawdown)}
          </span>
        </div>
        <div className="metric-cell">
          <span className="metric-label">투자 노출도</span>
          <span className="metric-value">{formatPercent(exposure, 0)}</span>
          <span className="metric-detail">
            관측 {account.observation_count.toLocaleString()}일
          </span>
        </div>
      </section>

      {account.observation_count < 252 && (
        <div className="notice-box neutral-box" style={{ marginTop: 16 }}>
          연환산 수익률과 Sharpe는 관측치 252개가 쌓인 뒤 표시합니다.
        </div>
      )}

      <div className="section-grid">
        <section className="section-panel">
          <div className="panel-header">
            <div>
              <h2>현재 포지션</h2>
              <p>
                {positions.length} / {maximumPositions}종목
              </p>
            </div>
          </div>
          <div className="data-table-wrap">
            <table className="data-table forward-position-table">
              <thead>
                <tr>
                  <th>종목</th>
                  <th>비교군</th>
                  <th>수량</th>
                  <th>가격</th>
                  <th>점수</th>
                  <th>상태</th>
                </tr>
              </thead>
              <tbody>
                {positions.map((position) => (
                  <tr key={String(position.asset_id)}>
                    <td className="symbol-cell">
                      <strong>{String(position.symbol)}</strong>
                      <span>{String(position.name)}</span>
                    </td>
                    <td>
                      {PEER_LABELS[String(position.peer_group)] ??
                        String(position.peer_group)}
                    </td>
                    <td>{Number(position.quantity).toLocaleString()}</td>
                    <td>{Number(position.last_price).toLocaleString()}</td>
                    <td className="score-number">
                      {Number(position.last_score).toFixed(1)}
                    </td>
                    <td>
                      {Boolean(position.review_required) ? (
                        <StatusBadge state="REVIEW_REQUIRED" />
                      ) : (
                        <StatusBadge state="READY" />
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {positions.length === 0 && (
              <div className="empty-state">현재 보유 포지션이 없습니다.</div>
            )}
          </div>
        </section>

        <section className="section-panel">
          <div className="panel-header">
            <div>
              <h2>시장 기준일</h2>
              <p>마지막 정상 평가</p>
            </div>
          </div>
          <div className="panel-body">
            <dl className="rule-list">
              <div>
                <dt>한국 시장</dt>
                <dd>{marketDates.KR ?? "대기"}</dd>
              </div>
              <div>
                <dt>미국 시장</dt>
                <dd>{marketDates.US ?? "대기"}</dd>
              </div>
              <div>
                <dt>현금</dt>
                <dd>{formatKrw(account.cash_krw)}</dd>
              </div>
              <div>
                <dt>투자 금액</dt>
                <dd>{formatKrw(account.invested_krw)}</dd>
              </div>
            </dl>
          </div>
        </section>
      </div>

      <div className="section-grid">
        <section className="section-panel">
          <div className="panel-header">
            <div>
              <h2>미체결 주문</h2>
              <p>{pendingOrders.length}건</p>
            </div>
          </div>
          <div className="ledger-list">
            {pendingOrders.map((order) => (
              <div className="ledger-row" key={String(order.order_id)}>
                <div>
                  <strong>
                    {String(order.side)} · {String(order.symbol)}
                  </strong>
                  <small>{String(order.scheduled_date)} 예정</small>
                </div>
                <StatusBadge state={String(order.status)} />
              </div>
            ))}
            {pendingOrders.length === 0 && (
              <div className="empty-state">미체결 주문이 없습니다.</div>
            )}
          </div>
        </section>
        <section className="section-panel">
          <div className="panel-header">
            <div>
              <h2>최근 활동</h2>
              <p>평가·주문·체결 원장</p>
            </div>
          </div>
          <div className="ledger-list">
            {activity?.items.map((item, index) => (
              <div className="ledger-row" key={`${String(item.date)}:${index}`}>
                <div>
                  <strong>
                    {String(item.type)}
                    {item.symbol ? ` · ${String(item.symbol)}` : ""}
                  </strong>
                  <small>{String(item.date)}</small>
                </div>
                <StatusBadge state={String(item.status)} />
              </div>
            ))}
            {!activity?.items.length && (
              <div className="empty-state">아직 기록된 활동이 없습니다.</div>
            )}
          </div>
        </section>
      </div>
    </>
  );
}
