import { describe, expect, it } from "vitest";

import {
  getResearchSyncButtonLabel,
  getResearchSyncPresentation,
} from "./research-sync-presentation";

describe("research sync presentation", () => {
  it("describes an initial full collection when no active snapshot exists", () => {
    const presentation = getResearchSyncPresentation("FULL", false);

    expect(presentation).toEqual({
      activeButtonLabel: "최초 수집 중",
      description:
        "시장 전체 10년 일봉을 처음 내려받는 작업으로 시간이 걸릴 수 있습니다.",
      downloadStageLabel: "10년 가격 데이터 최초 수집",
    });
  });

  it("describes a full rebuild while preserving an active snapshot", () => {
    const presentation = getResearchSyncPresentation("FULL", true);

    expect(presentation).toEqual({
      activeButtonLabel: "재구축 중",
      description: "기존 정상 데이터는 유지한 채 새 가격 스냅샷을 구성합니다.",
      downloadStageLabel: "전체 가격 이력 재구축",
    });
  });

  it("describes an incremental update and its full-history exceptions", () => {
    const presentation = getResearchSyncPresentation("INCREMENTAL", true);

    expect(presentation).toEqual({
      activeButtonLabel: "갱신 중",
      description:
        "기존 10년 데이터는 유지하고 최근 구간만 갱신합니다. 신규·배당·분할 종목은 전체 이력을 보정할 수 있습니다.",
      downloadStageLabel: "최근 가격 데이터 갱신",
    });
  });

  it("uses neutral wording for a legacy run without a collection mode", () => {
    const presentation = getResearchSyncPresentation(null, true);

    expect(presentation).toEqual({
      activeButtonLabel: "갱신 중",
      description: "가격 데이터 갱신 작업의 진행 상태입니다.",
      downloadStageLabel: "가격 데이터 갱신",
    });
  });

  it("keeps the mode-specific running label and exposes retry after failure", () => {
    expect(
      getResearchSyncButtonLabel({
        active: true,
        activeButtonLabel: "재구축 중",
        failed: false,
      }),
    ).toBe("재구축 중");

    expect(
      getResearchSyncButtonLabel({
        active: false,
        activeButtonLabel: "재구축 중",
        failed: true,
      }),
    ).toBe("이어받아 다시 시도");
  });

  it("uses the common idle action after a completed run", () => {
    expect(
      getResearchSyncButtonLabel({
        active: false,
        activeButtonLabel: "최초 수집 중",
        failed: false,
      }),
    ).toBe("시장 데이터 갱신");
  });
});
