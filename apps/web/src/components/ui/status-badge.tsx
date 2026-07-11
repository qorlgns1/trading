import { cn } from "@/lib/utils";

const LABELS: Record<string, string> = {
  STRONG_CANDIDATE: "강한 추세 후보",
  CANDIDATE: "추세 후보",
  WATCH: "관찰",
  WEAK: "약한 추세",
  EXCLUDED: "제외",
  NOT_AVAILABLE: "산출 불가",
  QUEUED: "대기 중",
  RUNNING: "계산 중",
  SUCCEEDED: "완료",
  FAILED: "실패",
  READY: "정상",
  INSUFFICIENT_HISTORY: "가격 이력 부족",
  UNSUPPORTED: "유형 확인 필요",
  DOWNLOAD_FAILED: "수집 실패",
  STALE: "갱신 지연",
  INVALID_DATA: "데이터 격리",
  PASS: "통과",
  WARN: "주의",
  FAIL: "차단",
  BLOCKED: "전체 차단",
  QUARANTINED: "종목 격리",
  REPAIRED: "복구 완료",
  WARN_ONLY: "확인 필요",
  ERROR: "오류",
  WARNING: "경고",
  BASELINE: "기준 저장",
  ENTERED: "후보 편입",
  RETAINED: "후보 유지",
  EXITED: "후보 해제",
  WAITING_FOR_REVIEW: "주간 평가 대기",
  ACTIVE: "운영 중",
  REVIEW_REQUIRED: "검토 필요",
  ARCHIVED: "보관",
  PENDING: "체결 대기",
  DEFERRED: "체결 이월",
  FILLED: "체결 완료",
  CANCELLED: "취소",
};

export function StatusBadge({ state }: { state: string }) {
  return (
    <span
      className={cn(
        "status-badge",
        state === "STRONG_CANDIDATE" && "status-strong",
        state === "CANDIDATE" && "status-candidate",
        state === "WATCH" && "status-watch",
        [
          "EXCLUDED",
          "FAILED",
          "DOWNLOAD_FAILED",
          "INVALID_DATA",
          "FAIL",
          "BLOCKED",
          "ERROR",
        ].includes(state) && "status-danger",
        [
          "NOT_AVAILABLE",
          "WEAK",
          "INSUFFICIENT_HISTORY",
          "UNSUPPORTED",
        ].includes(state) && "status-muted",
        [
          "QUEUED",
          "RUNNING",
          "STALE",
          "WARN",
          "WARN_ONLY",
          "QUARANTINED",
          "WARNING",
        ].includes(state) && "status-running",
        ["SUCCEEDED", "READY", "PASS", "REPAIRED"].includes(state) &&
          "status-complete",
        ["ENTERED", "ACTIVE", "FILLED"].includes(state) && "status-complete",
        ["RETAINED", "BASELINE"].includes(state) && "status-candidate",
        ["EXITED", "REVIEW_REQUIRED"].includes(state) && "status-danger",
        ["WAITING_FOR_REVIEW", "PENDING", "DEFERRED"].includes(state) &&
          "status-running",
        ["ARCHIVED", "CANCELLED"].includes(state) && "status-muted",
      )}
    >
      {LABELS[state] ?? state}
    </span>
  );
}
