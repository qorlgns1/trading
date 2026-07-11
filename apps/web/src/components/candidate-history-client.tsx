"use client";

import { ChevronLeft, ChevronRight, Filter, History } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { PEER_LABELS } from "@/components/candidate-table";
import { Button } from "@/components/ui/button";
import { StatusBadge } from "@/components/ui/status-badge";
import { apiFetch, type CandidateHistory } from "@/lib/api";

const EVENT_LABELS: Record<string, string> = {
  BASELINE: "기준 저장",
  ENTERED: "후보 편입",
  RETAINED: "후보 유지",
  EXITED: "후보 해제",
};

const GROUPS = [
  "US_STOCK",
  "US_EQUITY_ETF",
  "KR_KOSPI",
  "KR_KOSDAQ",
  "KR_DOMESTIC_EQUITY_ETF",
  "KR_OVERSEAS_EQUITY_ETF",
];

export function CandidateHistoryClient({
  initial,
}: {
  initial: CandidateHistory;
}) {
  const [data, setData] = useState(initial);
  const [group, setGroup] = useState("");
  const [eventType, setEventType] = useState("");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function load(page = 1) {
    setLoading(true);
    setError(null);
    const query = new URLSearchParams({
      page: String(page),
      page_size: String(data.page_size),
    });
    if (group) query.set("peer_group", group);
    if (eventType) query.set("event_type", eventType);
    if (dateFrom) query.set("date_from", dateFrom);
    if (dateTo) query.set("date_to", dateTo);
    try {
      setData(
        await apiFetch<CandidateHistory>(
          `/research/candidate-history?${query.toString()}`,
        ),
      );
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "후보 이력을 불러오지 못했습니다.",
      );
    } finally {
      setLoading(false);
    }
  }

  return (
    <>
      <header className="page-header">
        <div>
          <h1>후보 이력</h1>
          <p>실데이터 동기화마다 공식 후보의 편입·유지·해제를 기록합니다.</p>
        </div>
        <div className="page-meta">
          <span className="demo-chip real-data-chip">로컬 실데이터</span>
          <span>{data.total.toLocaleString()}건</span>
        </div>
      </header>

      <section className="section-panel">
        <div className="filter-bar history-filter-bar">
          <label className="field-control">
            <span>비교군</span>
            <select
              value={group}
              onChange={(event) => setGroup(event.target.value)}
            >
              <option value="">전체 비교군</option>
              {GROUPS.map((value) => (
                <option key={value} value={value}>
                  {PEER_LABELS[value] ?? value}
                </option>
              ))}
            </select>
          </label>
          <label className="field-control">
            <span>변화</span>
            <select
              value={eventType}
              onChange={(event) => setEventType(event.target.value)}
            >
              <option value="">전체 변화</option>
              {Object.entries(EVENT_LABELS).map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
          </label>
          <label className="field-control date-control">
            <span>시작일</span>
            <input
              type="date"
              value={dateFrom}
              onChange={(event) => setDateFrom(event.target.value)}
            />
          </label>
          <label className="field-control date-control">
            <span>종료일</span>
            <input
              type="date"
              value={dateTo}
              onChange={(event) => setDateTo(event.target.value)}
            />
          </label>
          <Button
            variant="secondary"
            onClick={() => load(1)}
            disabled={loading}
          >
            <Filter size={15} /> 조회
          </Button>
        </div>
        {error && <div className="notice-box danger-box">{error}</div>}
        <div className="data-table-wrap">
          <table className="data-table candidate-history-table">
            <thead>
              <tr>
                <th>기준일</th>
                <th>변화</th>
                <th>종목</th>
                <th>비교군</th>
                <th>이전 점수</th>
                <th>현재 점수</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {data.items.map((item) => (
                <tr
                  key={`${item.data_version}:${item.asset_id}:${item.event_type}`}
                >
                  <td>{item.as_of}</td>
                  <td>
                    <StatusBadge state={item.event_type} />
                  </td>
                  <td className="symbol-cell">
                    <strong>{item.symbol}</strong>
                    <span>{item.name}</span>
                  </td>
                  <td>{PEER_LABELS[item.peer_group] ?? item.peer_group}</td>
                  <td>
                    {item.previous_score == null
                      ? "-"
                      : item.previous_score.toFixed(1)}
                  </td>
                  <td className="score-number">
                    {item.score == null ? "-" : item.score.toFixed(1)}
                  </td>
                  <td>
                    <Link
                      href={`/assets/${encodeURIComponent(item.asset_id)}`}
                      className="text-link"
                    >
                      상세
                    </Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {data.items.length === 0 && (
            <div className="empty-state">
              <History size={25} />
              <p>조건에 맞는 후보 변화가 없습니다.</p>
            </div>
          )}
        </div>
        <div className="pagination-bar">
          <span>{data.total.toLocaleString()}건</span>
          <div>
            <button
              className="icon-button icon-button-small"
              type="button"
              aria-label="이전 페이지"
              disabled={loading || data.page <= 1}
              onClick={() => load(data.page - 1)}
            >
              <ChevronLeft size={16} />
            </button>
            <strong>
              {data.page} / {data.total_pages}
            </strong>
            <button
              className="icon-button icon-button-small"
              type="button"
              aria-label="다음 페이지"
              disabled={loading || data.page >= data.total_pages}
              onClick={() => load(data.page + 1)}
            >
              <ChevronRight size={16} />
            </button>
          </div>
        </div>
      </section>

      <div className="notice-box neutral-box" style={{ marginTop: 16 }}>
        후보 이력은 투자 추천이나 상승 확률이 아니라 동일한 규칙의 일별 판정
        기록입니다.
      </div>
    </>
  );
}
