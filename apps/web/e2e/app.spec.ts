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

test("local replay history and forward tools stay usable", async ({ page }) => {
  const meta = await page.request.get("/api/v1/meta");
  const payload = await meta.json();
  test.skip(
    payload.app_mode !== "local_research",
    "local real-data snapshot required",
  );

  await page.goto("/replays");
  await expect(
    page.getByRole("heading", { name: "과거 시뮬레이션" }),
  ).toBeVisible();
  await expect(page.getByRole("button", { name: "재생 시작" })).toBeEnabled();
  await expect(page.getByText("생존편향이 포함됩니다.")).toBeVisible();

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
  await expect(page.getByRole("heading", { name: "성과 차이 분해" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "월별 수익률" })).toBeVisible();
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
  await expect(page.getByText("최대 12종목", { exact: true })).toBeVisible();
  await expect(page.getByText("통과", { exact: true }).first()).toBeVisible();

  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth > window.innerWidth + 1,
  );
  expect(overflow).toBe(false);
});
