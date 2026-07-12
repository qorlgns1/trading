import type { components } from "./api-schema";

export type Meta = components["schemas"]["MetaResponse"];
export type ScreenerResponse = components["schemas"]["ScreenerResponse"];
export type ScreenerItem = components["schemas"]["ScreenerItem"];
export type AssetDetail = components["schemas"]["AssetDetail"];
export type PaperPortfolio = components["schemas"]["PaperPortfolioResponse"];
export type BacktestResponse = components["schemas"]["BacktestResponse"];
export type Artifact = components["schemas"]["ArtifactResponse"];
export type ResearchStatus = components["schemas"]["ResearchStatusResponse"];
export type ResearchSync = components["schemas"]["ResearchSyncResponse"];
export type ResearchSyncAccepted =
  components["schemas"]["ResearchSyncAccepted"];
export type PeerCoverage = components["schemas"]["PeerCoverage"];
export type QualityReport = components["schemas"]["QualityReportResponse"];
export type QualityIssues = components["schemas"]["QualityIssuesResponse"];
export type QualityIssue = components["schemas"]["QualityIssue"];
export type ScoreTrace = components["schemas"]["ScoreTrace"];
export type ReplayResponse = components["schemas"]["ReplayResponse"];
export type ReplayAccepted = components["schemas"]["ReplayAccepted"];
export type ReplayAnalysis = components["schemas"]["ReplayAnalysis"];
export type ReplayRoundTrip = components["schemas"]["ReplayRoundTrip"];
export type CandidateHistory =
  components["schemas"]["CandidateHistoryResponse"];
export type CandidateHistoryItem =
  components["schemas"]["CandidateHistoryItem"];
export type ForwardAccount = components["schemas"]["ForwardAccountResponse"];
export type ForwardActivity = components["schemas"]["ForwardActivityResponse"];
export type ProviderList = components["schemas"]["ProviderListResponse"];
export type ProviderStatus = components["schemas"]["ProviderStatusResponse"];

const serverOrigin = process.env.API_INTERNAL_URL ?? "http://localhost:8000";

export async function apiFetch<T>(
  path: string,
  init?: RequestInit,
): Promise<T> {
  const base =
    typeof window === "undefined" ? `${serverOrigin}/api/v1` : "/api/v1";
  const response = await fetch(`${base}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...init?.headers,
    },
    cache: "no-store",
  });
  if (!response.ok) {
    const detail = await response.json().catch(() => null);
    throw new Error(
      detail?.detail ?? `요청에 실패했습니다. (${response.status})`,
    );
  }
  return response.json() as Promise<T>;
}
