import { test, expect, Page } from "@playwright/test";

/**
 * 法遵 RAG 前端 E2E。對 scripts/mock_server.py（固定假資料）執行，不打真 OpenAI/DB。
 *
 * 流程：問一題 → 看到答案 + 來源 + 上標引用 → 點上標開 offcanvas 並高亮
 *       → 抽屜內展開原文全文 → 追問 chip 與 feedback。
 *
 * 穩健性策略：
 *  - 一律用 web-first assertions（toBeVisible / toContainText），自帶輪詢等待，無硬 sleep。
 *  - SSE 是逐段串流，答案文字會逐漸長出；用「等最終腳註上標出現」作為串流完成的訊號，
 *    上標只在 final 之後（markdown 重新渲染後）穩定存在，比等某個關鍵字更可靠。
 */

const FIRST_STARTER = "個資外洩要通報誰？期限多久？";

/** 點第一個 starter chip 並等串流完成（答案非空 + 三個上標引用已渲染）。 */
async function askFirstStarter(page: Page) {
  await page.goto("/");
  await page.locator(`button.starter[data-q="${FIRST_STARTER}"]`).click();

  const answer = page.locator("[data-answer]");
  await expect(answer).toBeVisible();
  // 串流完成的訊號：final 後 markdown 重渲染出 3 個上標引用
  await expect(page.locator("[data-answer] sup a.cite-ref")).toHaveCount(3);
  await expect(answer).toContainText("通知當事人");
}

test.describe("法遵 RAG 問答 E2E", () => {
  test("問答顯示答案與來源", async ({ page }) => {
    await askFirstStarter(page);

    // 答案非空
    const answerText = await page.locator("[data-answer]").innerText();
    expect(answerText.trim().length).toBeGreaterThan(0);

    // 三則來源列
    await expect(page.locator(".src-row")).toHaveCount(3);
    await expect(
      page.locator('.src-row[data-filename="個人資料保護法-第12條.txt"]')
    ).toBeVisible();

    // 答案內腳註渲染成上標引用
    await expect(page.locator("[data-answer] sup a.cite-ref")).toHaveCount(3);
  });

  test("上標引用開 offcanvas 並高亮", async ({ page }) => {
    await askFirstStarter(page);

    await page.locator('[data-answer] .cite-ref[data-ref="1"]').click();

    const offcanvas = page.locator("#offcanvas");
    await expect(offcanvas).toHaveClass(/open/);
    await expect(offcanvas).toBeVisible();

    // 第 1 個引用對到第12條，該卡為 active 並含「第12條」
    const active = page.locator(".evi-card.active");
    await expect(active).toBeVisible();
    await expect(active).toContainText("第12條");
  });

  test("開啟原文全文抽屜內展開", async ({ page }) => {
    await askFirstStarter(page);
    await page.locator('[data-answer] .cite-ref[data-ref="1"]').click();
    await expect(page.locator("#offcanvas")).toHaveClass(/open/);

    const firstCard = page.locator(".evi-card").first();
    const fullText = firstCard.locator(".full-text");
    await expect(fullText).toBeHidden();

    await firstCard.locator(".open-full").click();

    await expect(fullText).toBeVisible();
    await expect(fullText).not.toBeEmpty();
    // mock /api/source 回傳的全文含此標記
    await expect(fullText).toContainText("mock 全文");
  });

  test("追問 chip 與 feedback", async ({ page }) => {
    await askFirstStarter(page);

    // 追問 chips（loadFollowups 非阻斷，需等待出現）
    await expect(page.locator(".chip.followup").first()).toBeVisible();
    await expect(page.locator(".chip.followup")).toHaveCount(3);

    // feedback：點「有幫助」後該按鈕得到 active class
    const up = page.locator('.fb-btn[data-rate="up"]').first();
    await up.click();
    await expect(up).toHaveClass(/active/);
  });

  test("跨裝置（手機）問答流程 @mobile", async ({ page }) => {
    // chromium-mobile project 已用 Pixel 5；此處再明確設一個窄 viewport 作雙保險。
    await page.setViewportSize({ width: 390, height: 780 });
    await askFirstStarter(page);

    // 答案與來源在窄螢幕仍顯示
    await expect(page.locator("[data-answer]")).toContainText("通知當事人");
    await expect(page.locator(".src-row")).toHaveCount(3);

    // offcanvas 在手機仍能開（寬度 84%/86vw 不細驗）
    await page.locator('[data-answer] .cite-ref[data-ref="1"]').click();
    await expect(page.locator("#offcanvas")).toHaveClass(/open/);
    await expect(page.locator(".evi-card.active")).toContainText("第12條");
  });
});
