"use client";

import { ChevronLeft, ChevronRight, LoaderCircle, RotateCcw, Search } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { CandidateTable } from "@/components/candidate-table";
import { DataModeBadge } from "@/components/data-mode-badge";
import { Button } from "@/components/ui/button";
import { apiFetch, type ScreenerResponse } from "@/lib/api";

const GROUPS = [
  { value: "", label: "전체" },
  { value: "US_STOCK", label: "미국 주식" },
  { value: "KR_KOSPI", label: "KOSPI" },
  { value: "KR_KOSDAQ", label: "KOSDAQ" },
  { value: "US_EQUITY_ETF", label: "미국 ETF" },
  { value: "KR_DOMESTIC_EQUITY_ETF", label: "국내 ETF" },
  { value: "KR_OVERSEAS_EQUITY_ETF", label: "해외형 ETF" },
];

export type ScreenerFilters = {
  group: string;
  state: string;
  minimumScore: number;
  search: string;
  officialOnly: boolean;
};

function buildQuery(filters: ScreenerFilters, page: number) {
  const query = new URLSearchParams({
    minimum_score: String(filters.minimumScore),
    page: String(page),
    page_size: "25",
    official_only: String(filters.officialOnly),
  });
  if (filters.group) query.set("peer_group", filters.group);
  if (filters.state) query.set("candidate_state", filters.state);
  if (filters.search) query.set("q", filters.search);
  return query.toString();
}

export function ScreenerClient({
  initial,
  initialFilters,
  dataSource,
}: {
  initial: ScreenerResponse;
  initialFilters: ScreenerFilters;
  dataSource: string;
}) {
  const [group, setGroup] = useState(initialFilters.group);
  const [state, setState] = useState(initialFilters.state);
  const [minimumScore, setMinimumScore] = useState(initialFilters.minimumScore);
  const [search, setSearch] = useState(initialFilters.search);
  const [debouncedSearch, setDebouncedSearch] = useState(initialFilters.search);
  const [officialOnly, setOfficialOnly] = useState(initialFilters.officialOnly);
  const [page, setPage] = useState(1);
  const [data, setData] = useState(initial);
  const [loadedQuery, setLoadedQuery] = useState(() => buildQuery(initialFilters, 1));
  const [error, setError] = useState<string | null>(null);
  const queryString = useMemo(
    () => buildQuery({ group, state, minimumScore, search: debouncedSearch, officialOnly }, page),
    [debouncedSearch, group, minimumScore, officialOnly, page, state],
  );
  const loading = loadedQuery !== queryString;

  useEffect(() => {
    const timer = window.setTimeout(() => setDebouncedSearch(search.trim()), 300);
    return () => window.clearTimeout(timer);
  }, [search]);

  useEffect(() => {
    const controller = new AbortController();
    apiFetch<ScreenerResponse>(`/screener?${queryString}`, { signal: controller.signal })
      .then((response) => {
        setData(response);
        setLoadedQuery(queryString);
        setError(null);
      })
      .catch((reason: Error) => {
        if (reason.name !== "AbortError") {
          setError(reason.message);
          setLoadedQuery(queryString);
        }
      });
    return () => controller.abort();
  }, [queryString]);

  function resetPage() {
    setPage(1);
  }

  function resetFilters() {
    setGroup("");
    setState("");
    setMinimumScore(0);
    setSearch("");
    setOfficialOnly(false);
    setPage(1);
  }

  return (
    <>
      <header className="page-header">
        <div>
          <h1>추세 스크리너</h1>
          <p>같은 시장·상품 비교군 안에서 계산한 점수와 데이터 상태를 함께 확인합니다.</p>
        </div>
        <div className="page-meta"><DataModeBadge source={dataSource} /><span>{data.as_of} 종가 기준</span></div>
      </header>
      <section className="section-panel">
        <div className="tab-list" role="tablist" aria-label="비교군">
          {GROUPS.map((item) => (
            <button
              key={item.value}
              type="button"
              role="tab"
              aria-selected={group === item.value}
              className={`tab-button ${group === item.value ? "tab-button-active" : ""}`}
              onClick={() => { setGroup(item.value); resetPage(); }}
            >
              {item.label}
            </button>
          ))}
        </div>
        <div className="filter-bar">
          <label className="search-control">
            <Search size={15} aria-hidden="true" />
            <span className="sr-only">종목 검색</span>
            <input
              type="search"
              value={search}
              placeholder="종목명 또는 티커"
              onChange={(event) => { setSearch(event.target.value); resetPage(); }}
            />
          </label>
          <select
            className="select-control"
            value={state}
            onChange={(event) => { setState(event.target.value); resetPage(); }}
            aria-label="후보 상태"
          >
            <option value="">모든 후보 상태</option>
            <option value="STRONG_CANDIDATE">강한 추세 후보</option>
            <option value="CANDIDATE">추세 후보</option>
            <option value="WATCH">관찰</option>
            <option value="WEAK">약한 추세</option>
            <option value="EXCLUDED">제외</option>
            <option value="NOT_AVAILABLE">산출 불가</option>
          </select>
          <label className="inline-field">
            <span>최소 점수</span>
            <input
              className="number-control"
              type="number"
              min={0}
              max={100}
              step={5}
              value={minimumScore}
              onChange={(event) => { setMinimumScore(Number(event.target.value)); resetPage(); }}
            />
          </label>
          <label className="check-control">
            <input
              type="checkbox"
              checked={officialOnly}
              onChange={(event) => { setOfficialOnly(event.target.checked); resetPage(); }}
            />
            <span>공식 후보만</span>
          </label>
          <span className="result-count">
            {loading && <LoaderCircle size={14} className="spin" />}
            {data.total.toLocaleString()}개 결과
          </span>
          <Button variant="ghost" type="button" onClick={resetFilters} title="필터 초기화">
            <RotateCcw size={15} /> 초기화
          </Button>
        </div>
        {error ? <div className="notice-box danger-box">{error}</div> : <CandidateTable items={data.items} />}
        <div className="pagination-bar">
          <span>{data.total === 0 ? 0 : (data.page - 1) * data.page_size + 1}–{Math.min(data.page * data.page_size, data.total)} / {data.total.toLocaleString()}</span>
          <div>
            <button
              type="button"
              className="icon-button"
              disabled={data.page <= 1 || loading}
              onClick={() => setPage((current) => Math.max(1, current - 1))}
              aria-label="이전 페이지"
              title="이전 페이지"
            ><ChevronLeft size={17} /></button>
            <strong>{data.page} / {data.total_pages}</strong>
            <button
              type="button"
              className="icon-button"
              disabled={data.page >= data.total_pages || loading}
              onClick={() => setPage((current) => current + 1)}
              aria-label="다음 페이지"
              title="다음 페이지"
            ><ChevronRight size={17} /></button>
          </div>
        </div>
      </section>
      <div className="notice-box" style={{ marginTop: 16 }}>
        점수는 후보 탐색 기준입니다. 서로 다른 비교군의 점수를 기대수익률처럼 직접 비교할 수 없습니다.
      </div>
    </>
  );
}
