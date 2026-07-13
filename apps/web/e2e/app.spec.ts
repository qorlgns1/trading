import { expect, test } from "@playwright/test";

test("dashboard exposes the operational product surface", async ({ page }) => {
  const meta = await page.request.get("/api/v1/meta");
  const mode = await meta.json();
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "오늘의 추세 후보" }),
  ).toBeVisible();
  await expect(
    page
      .locator(".page-meta")
      .getByText(
        mode.app_mode === "local_research"
          ? "실제 데이터 · 로컬 연구"
          : "가상 데이터",
        { exact: true },
      ),
  ).toBeVisible();
  await expect(page.getByRole("heading", { name: "미국 주식" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "KOSPI" })).toBeVisible();
  await expect(page.getByText("공식 추세 후보", { exact: true })).toBeVisible();
});

test("screener filters remain usable without horizontal page overflow", async ({
  page,
}) => {
  await page.goto("/screener");
  await expect(
    page.getByRole("heading", { name: "추세 스크리너" }),
  ).toBeVisible();
  await page.getByRole("tab", { name: "미국 주식" }).click();
  await expect(page.getByText(/개 결과/)).toBeVisible();
  const candidates = await page.request.get(
    "/api/v1/screener?peer_group=US_STOCK&page_size=25",
  );
  const candidateItems = (await candidates.json()).items as Array<{
    symbol: string;
  }>;
  const symbol = candidateItems
    .map((item) => item.symbol)
    .sort((left, right) => right.length - left.length)[0];
  await page.getByPlaceholder("종목명 또는 티커").fill(symbol);
  await expect(page.getByText(/개 결과/)).toBeVisible();
  await expect(page.getByText(symbol, { exact: true })).toBeVisible();
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth > window.innerWidth + 1,
  );
  expect(overflow).toBe(false);
});

test("backtest allocations always display a 100 percent total", async ({
  page,
}) => {
  await page.goto("/backtests");
  await expect(
    page.getByRole("heading", { name: "백테스트 실험실" }),
  ).toBeVisible();
  await expect(page.getByText("100%", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "실행" })).toBeEnabled();
});

test("local research exposes quality checks and an asset score trace", async ({
  page,
}) => {
  const meta = await page.request.get("/api/v1/meta");
  const payload = await meta.json();
  test.skip(
    payload.app_mode !== "local_research",
    "local real-data snapshot required",
  );

  await page.goto("/quality");
  await expect(
    page.getByRole("heading", { name: "데이터 품질" }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "비교군별 커버리지" }),
  ).toBeVisible();
  await expect(page.getByRole("heading", { name: "검사 항목" })).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "문제 및 복구 내역" }),
  ).toBeVisible();

  await page.goto("/screener?official_only=true");
  const firstDetail = page.getByRole("link", { name: "상세 보기" }).first();
  await expect(firstDetail).toBeVisible();
  await firstDetail.click();
  await expect(
    page.getByRole("heading", { name: "점수 계산 추적" }),
  ).toBeVisible();

  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth > window.innerWidth + 1,
  );
  expect(overflow).toBe(false);
});

test("local provider admin checks standby Toss connection without changing providers", async ({
  page,
}) => {
  const meta = await page.request.get("/api/v1/meta");
  const payload = await meta.json();
  test.skip(
    payload.app_mode !== "local_research",
    "local provider settings required",
  );

  await page.route("**/api/v1/admin/providers/toss/check", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        provider: "TOSS",
        display_name: "토스증권 Open API",
        role: "연결 대기 중인 보조 공급자",
        description:
          "향후 시세 조회나 공급자 교차 검증에 사용할 연결 기반입니다.",
        enabled: true,
        configured: true,
        used_in_pipeline: false,
        status: "AVAILABLE",
        capabilities: ["국내·미국 종목 정보", "일봉 가격"],
        last_checked_at: "2026-07-11T09:30:00+09:00",
        latency_ms: 145,
        message:
          "국내·미국 대표 종목 조회에 성공했습니다. 현재 데이터 흐름에는 사용하지 않습니다.",
      }),
    });
  });

  await page.goto("/admin");
  await expect(
    page.getByRole("heading", { name: "공급자 관리" }),
  ).toBeVisible();
  await expect(page.getByText("Yahoo Finance", { exact: true })).toBeVisible();
  await expect(page.getByText("KRX", { exact: true })).toBeVisible();
  await expect(
    page.getByText("토스증권 Open API", { exact: true }),
  ).toBeVisible();
  await expect(page.getByText("대기 공급자", { exact: true })).toBeVisible();

  await page.getByRole("button", { name: "연결 확인" }).click();
  await expect(page.getByText("연결 가능", { exact: true })).toBeVisible();
  await expect(page.getByText("응답 145ms", { exact: true })).toBeVisible();

  await page.unroute("**/api/v1/admin/providers/toss/check");
  await page.route("**/api/v1/admin/providers/toss/check", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        provider: "TOSS",
        display_name: "토스증권 Open API",
        role: "연결 대기 중인 보조 공급자",
        description:
          "향후 시세 조회나 공급자 교차 검증에 사용할 연결 기반입니다.",
        enabled: true,
        configured: true,
        used_in_pipeline: false,
        status: "UNAVAILABLE",
        capabilities: ["국내·미국 종목 정보", "일봉 가격"],
        last_checked_at: "2026-07-11T09:31:00+09:00",
        latency_ms: 310,
        message: "토스 Open API 인증에 실패했습니다.",
      }),
    });
  });
  await page.getByRole("button", { name: "연결 확인" }).click();
  await expect(page.getByText("연결 실패", { exact: true })).toBeVisible();

  await page.setViewportSize({ width: 390, height: 844 });
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth > window.innerWidth + 1,
  );
  expect(overflow).toBe(false);
});

test("local replay history and forward tools stay usable", async ({ page }) => {
  const meta = await page.request.get("/api/v1/meta");
  const payload = await meta.json();
  test.skip(
    payload.app_mode !== "local_research",
    "local real-data snapshot required",
  );

  await page.goto("/replays");
  await expect(
    page.getByRole("heading", { name: "전략 실험실" }),
  ).toBeVisible();
  await expect(page.getByRole("link", { name: "새 실험" })).toBeVisible();
  await expect(page.getByText(/생존편향이 포함됩니다/)).toBeVisible();

  await page.goto("/candidate-history");
  await expect(page.getByRole("heading", { name: "후보 이력" })).toBeVisible();
  await expect(page.getByLabel("비교군")).toBeVisible();
  await expect(page.getByLabel("변화")).toBeVisible();
  await expect(
    page.getByRole("cell", { name: "기준 저장", exact: true }).first(),
  ).toBeVisible();

  await page.goto("/forward");
  await expect(
    page.getByRole("heading", { name: "포워드 포트폴리오" }),
  ).toBeVisible();
  await expect(page.getByRole("button", { name: "추적 시작" })).toBeEnabled();

  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth > window.innerWidth + 1,
  );
  expect(overflow).toBe(false);
});

test("local strategy builder exposes the complete controlled experiment flow", async ({
  page,
}) => {
  const meta = await page.request.get("/api/v1/meta");
  const payload = await meta.json();
  test.skip(
    payload.app_mode !== "local_research",
    "local real-data snapshot required",
  );

  await page.goto("/replays/new");
  await expect(
    page.getByRole("heading", { name: "새 전략 실험" }),
  ).toBeVisible();
  await expect(page.getByRole("button", { name: /균형/ })).toBeVisible();
  await page.getByRole("button", { name: "점수" }).click();
  await expect(page.getByRole("button", { name: /선별 진입/ })).toBeVisible();
  const entryScore = page.getByRole("slider", {
    name: "진입 점수",
    exact: true,
  });
  await expect(entryScore).toBeVisible();
  await page.getByLabel(/진입 점수 설명:/).focus();
  await expect(page.getByRole("tooltip")).toContainText(
    "공식 후보만 새로 매수",
  );

  await page.getByRole("button", { name: "포트폴리오" }).click();
  await expect(page.getByLabel("종목 투자금")).toBeVisible();
  await expect(page.getByLabel("보유 종목 교체")).toBeVisible();
  await page.getByRole("button", { name: "위험·체결" }).click();
  await expect(page.getByLabel("평가 주기")).toBeVisible();
  await expect(page.getByText("워크포워드 검증")).toBeVisible();

  await page.setViewportSize({ width: 390, height: 844 });
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth > window.innerWidth + 1,
  );
  expect(overflow).toBe(false);
});

test("local replay analysis tabs explain and validate a completed run", async ({
  page,
}) => {
  test.setTimeout(300_000);
  const meta = await page.request.get("/api/v1/meta");
  const payload = await meta.json();
  test.skip(
    payload.app_mode !== "local_research",
    "local real-data snapshot required",
  );

  const accepted = await page.request.post("/api/v1/research/replays", {
    data: {},
  });
  expect(accepted.status()).toBe(202);
  const runId = (await accepted.json()).run_id as string;
  await expect
    .poll(
      async () => {
        const response = await page.request.get(
          `/api/v1/research/replays/${runId}`,
        );
        return (await response.json()).status;
      },
      { timeout: 240_000, intervals: [1_000, 2_000, 5_000] },
    )
    .toBe("SUCCEEDED");

  await page.goto(`/replays/${runId}`);
  await expect(
    page.getByRole("heading", { name: "과거 시뮬레이션 결과" }),
  ).toBeVisible();
  await page.getByRole("tab", { name: "원인 분석" }).click();
  await expect(
    page.getByRole("heading", { name: "성과 차이 분해" }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "월별 수익률" }),
  ).toBeVisible();
  const canvas = page.locator("canvas").first();
  await expect(canvas).toBeVisible();
  const canvasSize = await canvas.evaluate((element) => {
    const chart = element as HTMLCanvasElement;
    return { width: chart.width, height: chart.height };
  });
  expect(canvasSize.width).toBeGreaterThan(100);
  expect(canvasSize.height).toBeGreaterThan(100);

  await page.getByRole("tab", { name: "거래 품질" }).click();
  await expect(page.getByText("완료 거래 회차", { exact: true })).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "진입 점수 구간" }),
  ).toBeVisible();

  await page.getByRole("tab", { name: "검증" }).click();
  await expect(
    page.getByRole("heading", { name: "재생 무결성 검사" }),
  ).toBeVisible();
  await expect(
    page.getByText("설정된 최대 보유 종목", { exact: true }),
  ).toBeVisible();
  await expect(page.getByText("통과", { exact: true }).first()).toBeVisible();

  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth > window.innerWidth + 1,
  );
  expect(overflow).toBe(false);
});
