"use client";

import {
  ChevronLeft,
  ChevronRight,
  Download,
  LoaderCircle,
  RotateCcw,
  Search,
} from "lucide-react";
import Link from "next/link";
import { useEffect, useMemo, useState } from "react";

import { PEER_LABELS } from "@/components/candidate-table";
import { DataModeBadge } from "@/components/data-mode-badge";
import { Button } from "@/components/ui/button";
import { StatusBadge } from "@/components/ui/status-badge";
import { apiFetch, type QualityIssues, type QualityReport } from "@/lib/api";

const GROUPS = [
  { value: "", label: "전체 비교군" },
  { value: "US_STOCK", label: "미국 주식" },
  { value: "KR_KOSPI", label: "KOSPI" },
  { value: "KR_KOSDAQ", label: "KOSDAQ" },
  { value: "US_EQUITY_ETF", label: "미국 ETF" },
  { value: "KR_DOMESTIC_EQUITY_ETF", label: "국내 ETF" },
  { value: "KR_OVERSEAS_EQUITY_ETF", label: "해외형 ETF" },
];

type QualityFilters = {
  group: string;
  severity: string;
  resolution: string;
  search: string;
};

function buildQuery(filters: QualityFilters, page: number) {
  const query = new URLSearchParams({ page: String(page), page_size: "25" });
  if (filters.group) query.set("peer_group", filters.group);
  if (filters.severity) query.set("severity", filters.severity);
  if (filters.resolution) query.set("resolution", filters.resolution);
  if (filters.search) query.set("q", filters.search);
  return query.toString();
}

function compact(value: number) {
  return new Intl.NumberFormat("ko-KR", {
    notation: "compact",
    maximumFractionDigits: 1,
  }).format(value);
}

export function QualityClient({
  initialReport,
  initialIssues,
  dataSource,
}: {
  initialReport: QualityReport;
  initialIssues: QualityIssues;
  dataSource: string;
}) {
  const [group, setGroup] = useState("");
  const [severity, setSeverity] = useState("");
  const [resolution, setResolution] = useState("");
  const [search, setSearch] = useState("");
  const [debouncedSearch, setDebouncedSearch] = useState("");
  const [page, setPage] = useState(1);
  const [data, setData] = useState(initialIssues);
  const [loadedQuery, setLoadedQuery] = useState(() =>
    buildQuery({ group: "", severity: "", resolution: "", search: "" }, 1),
  );
  const [error, setError] = useState<string | null>(null);
  const queryString = useMemo(
    () =>
      buildQuery(
        { group, severity, resolution, search: debouncedSearch },
        page,
      ),
    [debouncedSearch, group, page, resolution, severity],
  );
  const loading = loadedQuery !== queryString;
  const csvQuery = useMemo(() => {
    const query = new URLSearchParams(queryString);
    query.delete("page");
    query.delete("page_size");
    return query.toString();
  }, [queryString]);

  useEffect(() => {
    const timer = window.setTimeout(
      () => setDebouncedSearch(search.trim()),
      300,
    );
    return () => window.clearTimeout(timer);
  }, [search]);

  useEffect(() => {
    const controller = new AbortController();
    apiFetch<QualityIssues>(`/research/quality/issues?${queryString}`, {
      signal: controller.signal,
    })
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
    setSeverity("");
    setResolution("");
    setSearch("");
    setPage(1);
  }

  return (
    <>
      <header className="page-header">
        <div>
          <h1>데이터 품질</h1>
          <p>
            수집 데이터의 구조와 가격 이상을 검사하고, 점수 계산에 사용할 수
            없는 종목을 분리합니다.
          </p>
        </div>
        <div className="page-meta">
          <DataModeBadge source={dataSource} />
          <StatusBadge state={initialReport.status} />
          <span>
            {new Date(initialReport.checked_at).toLocaleString("ko-KR")}
          </span>
        </div>
      </header>

      <section className="metric-strip">
        <div className="metric-cell">
          <span className="metric-label">검사한 가격 행</span>
          <span className="metric-value">
            {compact(initialReport.totals.rows)}
          </span>
          <span className="metric-detail">
            정책 {initialReport.policy_version}
          </span>
        </div>
        <div className="metric-cell">
          <span className="metric-label">검사한 종목</span>
          <span className="metric-value">
            {initialReport.totals.assets.toLocaleString()}개
          </span>
          <span className="metric-detail">6개 비교군 합계</span>
        </div>
        <div className="metric-cell">
          <span className="metric-label">복구 완료</span>
          <span className="metric-value">
            {initialReport.totals.repaired_assets.toLocaleString()}개
          </span>
          <span className="metric-detail">재수집 또는 공급자 복구</span>
        </div>
        <div className="metric-cell">
          <span className="metric-label">점수 계산 제외</span>
          <span className="metric-value">
            {initialReport.totals.quarantined_assets.toLocaleString()}개
          </span>
          <span className="metric-detail">오류가 남은 종목만 격리</span>
        </div>
      </section>

      {initialReport.status === "FAIL" && (
        <div className="notice-box danger-box" style={{ marginTop: 16 }}>
          전체 스냅샷 활성화가 차단됐습니다. 직전 정상 스냅샷은 그대로
          유지됩니다.
        </div>
      )}

      <div className="quality-layout">
        <section className="section-panel">
          <div className="panel-header">
            <div>
              <h2>비교군별 커버리지</h2>
              <p>지원 종목 중 정상적으로 점수를 계산한 비율입니다.</p>
            </div>
          </div>
          <div className="data-table-wrap">
            <table className="data-table quality-group-table">
              <thead>
                <tr>
                  <th>비교군</th>
                  <th>산출 가능</th>
                  <th>커버리지</th>
                  <th>격리</th>
                  <th>수집 실패</th>
                  <th>이력 부족</th>
                </tr>
              </thead>
              <tbody>
                {initialReport.groups.map((item) => (
                  <tr key={item.peer_group}>
                    <td>
                      <strong>{PEER_LABELS[item.peer_group]}</strong>
                    </td>
                    <td>
                      {item.ready_assets.toLocaleString()} /{" "}
                      {item.supported_assets.toLocaleString()}
                    </td>
                    <td>
                      <div className="coverage-cell">
                        <span>{(item.ready_rate * 100).toFixed(1)}%</span>
                        <span className="coverage-track" aria-hidden="true">
                          <span
                            style={{
                              width: `${Math.min(100, item.ready_rate * 100)}%`,
                            }}
                          />
                        </span>
                      </div>
                    </td>
                    <td>{item.quarantined_assets.toLocaleString()}</td>
                    <td>{item.download_failed_assets.toLocaleString()}</td>
                    <td>{item.insufficient_history_assets.toLocaleString()}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>

        <section className="section-panel">
          <div className="panel-header">
            <div>
              <h2>검사 항목</h2>
              <p>치명적 오류만 전체 활성화를 차단합니다.</p>
            </div>
          </div>
          <div className="quality-check-list">
            {initialReport.checks.map((check) => (
              <div key={check.check_id} className="quality-check-row">
                <div>
                  <strong>{check.label}</strong>
                  <small>{check.check_id}</small>
                </div>
                <span>{check.affected_count.toLocaleString()}건</span>
                <StatusBadge state={check.status} />
              </div>
            ))}
          </div>
        </section>
      </div>

      <section className="section-panel" style={{ marginTop: 20 }}>
        <div className="panel-header">
          <div>
            <h2>문제 및 복구 내역</h2>
            <p>
              종목별 문제는 상세 화면에서 현재 점수 상태와 함께 확인할 수
              있습니다.
            </p>
          </div>
          <a
            className="button button-secondary"
            href={`/api/v1/research/quality/issues.csv${csvQuery ? `?${csvQuery}` : ""}`}
          >
            <Download size={15} /> CSV
          </a>
        </div>
        <div className="filter-bar">
          <label className="search-control">
            <Search size={15} aria-hidden="true" />
            <span className="sr-only">문제 종목 검색</span>
            <input
              type="search"
              value={search}
              placeholder="종목명, 티커 또는 검사 코드"
              onChange={(event) => {
                setSearch(event.target.value);
                resetPage();
              }}
            />
          </label>
          <select
            className="select-control"
            value={group}
            aria-label="비교군"
            onChange={(event) => {
              setGroup(event.target.value);
              resetPage();
            }}
          >
            {GROUPS.map((item) => (
              <option key={item.value} value={item.value}>
                {item.label}
              </option>
            ))}
          </select>
          <select
            className="select-control"
            value={severity}
            aria-label="심각도"
            onChange={(event) => {
              setSeverity(event.target.value);
              resetPage();
            }}
          >
            <option value="">모든 심각도</option>
            <option value="ERROR">오류</option>
            <option value="WARNING">경고</option>
          </select>
          <select
            className="select-control"
            value={resolution}
            aria-label="처리 상태"
            onChange={(event) => {
              setResolution(event.target.value);
              resetPage();
            }}
          >
            <option value="">모든 처리 상태</option>
            <option value="BLOCKED">전체 차단</option>
            <option value="QUARANTINED">종목 격리</option>
            <option value="REPAIRED">복구 완료</option>
            <option value="WARN_ONLY">확인 필요</option>
          </select>
          <span className="result-count">
            {loading && <LoaderCircle size={14} className="spin" />}
            {data.total.toLocaleString()}개 내역
          </span>
          <Button
            variant="ghost"
            type="button"
            onClick={resetFilters}
            title="필터 초기화"
          >
            <RotateCcw size={15} /> 초기화
          </Button>
        </div>
        {error ? (
          <div className="notice-box danger-box">{error}</div>
        ) : data.items.length === 0 ? (
          <div className="empty-state">조건에 맞는 품질 문제가 없습니다.</div>
        ) : (
          <div className="data-table-wrap">
            <table className="data-table quality-issue-table">
              <thead>
                <tr>
                  <th>종목</th>
                  <th>검사 항목</th>
                  <th>처리</th>
                  <th>발생 구간</th>
                  <th>건수</th>
                  <th>
                    <span className="sr-only">상세</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {data.items.map((item, index) => (
                  <tr
                    key={`${item.check_id}-${item.asset_id ?? "dataset"}-${index}`}
                  >
                    <td className="symbol-cell">
                      <strong>{item.symbol ?? "전체 데이터셋"}</strong>
                      <span>{item.name ?? item.peer_group ?? "공통 검사"}</span>
                    </td>
                    <td>
                      <strong className="quality-message">
                        {item.message}
                      </strong>
                      <small className="quality-code">{item.check_id}</small>
                    </td>
                    <td>
                      <StatusBadge state={item.resolution} />
                    </td>
                    <td>
                      {item.first_date ?? "-"}
                      {item.last_date && item.last_date !== item.first_date
                        ? ` – ${item.last_date}`
                        : ""}
                    </td>
                    <td>{item.row_count.toLocaleString()}</td>
                    <td>
                      {item.asset_id && (
                        <Link
                          className="text-link"
                          href={`/assets/${encodeURIComponent(item.asset_id)}`}
                        >
                          상세
                        </Link>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <div className="pagination-bar">
          <span>
            {data.total === 0 ? 0 : (data.page - 1) * data.page_size + 1}–
            {Math.min(data.page * data.page_size, data.total)} /{" "}
            {data.total.toLocaleString()}
          </span>
          <div>
            <button
              type="button"
              className="icon-button"
              disabled={data.page <= 1 || loading}
              onClick={() => setPage((current) => Math.max(1, current - 1))}
              aria-label="이전 페이지"
              title="이전 페이지"
            >
              <ChevronLeft size={17} />
            </button>
            <strong>
              {data.page} / {data.total_pages}
            </strong>
            <button
              type="button"
              className="icon-button"
              disabled={data.page >= data.total_pages || loading}
              onClick={() => setPage((current) => current + 1)}
              aria-label="다음 페이지"
              title="다음 페이지"
            >
              <ChevronRight size={17} />
            </button>
          </div>
        </div>
      </section>

      <div className="notice-box" style={{ marginTop: 16 }}>
        품질 통과는 데이터 형식과 계산 일관성을 뜻합니다. 투자 성과나 가격
        정확성을 보증하지 않습니다.
      </div>
    </>
  );
}
