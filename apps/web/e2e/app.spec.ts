import { expect, test } from "@playwright/test";

test("dashboard exposes the operational product surface", async ({ page }) => {
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "오늘의 추세 후보" }),
  ).toBeVisible();
  await expect(
    page.locator(".page-meta").getByText("가상 데이터", { exact: true }),
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
  await page.getByPlaceholder("종목명 또는 티커").fill("UST024");
  await expect(page.getByText("1개 결과", { exact: true })).toBeVisible();
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
