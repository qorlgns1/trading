export type ResearchCollectionMode = "FULL" | "INCREMENTAL";

type ResearchSyncPresentation = {
  activeButtonLabel: string;
  description: string;
  downloadStageLabel: string;
};

const PRESENTATIONS: Record<
  "INITIAL" | "REBUILD" | "INCREMENTAL" | "UNKNOWN",
  ResearchSyncPresentation
> = {
  INITIAL: {
    activeButtonLabel: "최초 수집 중",
    description:
      "시장 전체 10년 일봉을 처음 내려받는 작업으로 시간이 걸릴 수 있습니다.",
    downloadStageLabel: "10년 가격 데이터 최초 수집",
  },
  REBUILD: {
    activeButtonLabel: "재구축 중",
    description: "기존 정상 데이터는 유지한 채 새 가격 스냅샷을 구성합니다.",
    downloadStageLabel: "전체 가격 이력 재구축",
  },
  INCREMENTAL: {
    activeButtonLabel: "갱신 중",
    description:
      "기존 10년 데이터는 유지하고 최근 구간만 갱신합니다. 신규·배당·분할 종목은 전체 이력을 보정할 수 있습니다.",
    downloadStageLabel: "최근 가격 데이터 갱신",
  },
  UNKNOWN: {
    activeButtonLabel: "갱신 중",
    description: "가격 데이터 갱신 작업의 진행 상태입니다.",
    downloadStageLabel: "가격 데이터 갱신",
  },
};

export function getResearchSyncPresentation(
  collectionMode: ResearchCollectionMode | null | undefined,
  hasActiveSnapshot: boolean,
): ResearchSyncPresentation {
  if (collectionMode === "INCREMENTAL") return PRESENTATIONS.INCREMENTAL;
  if (collectionMode === "FULL") {
    return hasActiveSnapshot ? PRESENTATIONS.REBUILD : PRESENTATIONS.INITIAL;
  }
  return PRESENTATIONS.UNKNOWN;
}

export function getResearchSyncButtonLabel({
  active,
  activeButtonLabel,
  failed,
}: {
  active: boolean;
  activeButtonLabel: string;
  failed: boolean;
}): string {
  if (active) return activeButtonLabel;
  if (failed) return "이어받아 다시 시도";
  return "시장 데이터 갱신";
}
