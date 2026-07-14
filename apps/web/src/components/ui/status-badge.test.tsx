import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { StatusBadge } from "./status-badge";

describe("StatusBadge", () => {
  it("supports a context-specific label without changing status styling", () => {
    const markup = renderToStaticMarkup(
      <StatusBadge state="RUNNING" label="갱신 중" />,
    );

    expect(markup).toContain("갱신 중");
    expect(markup).toContain("status-running");
    expect(markup).not.toContain("계산 중");
  });
});
