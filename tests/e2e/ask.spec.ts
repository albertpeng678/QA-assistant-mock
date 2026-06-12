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
  // 串流「真正完成」的訊號：來源列（.src-row）只在 finalizeAnswer 渲染。
  // 不可只等上標引用——串流途中 markdown 即時渲染就會出現上標，但 final 會以
  // innerHTML 重渲染整段、置換這些節點，若此時點擊會點到已脫離的舊節點。
  await expect(page.locator(".src-row")).toHaveCount(4);
  await expect(page.locator("[data-answer] sup a.cite-ref")).toHaveCount(4);
  await expect(answer).toContainText("通知當事人");
}

test.describe("法遵 RAG 問答 E2E", () => {
  test("問答顯示答案與來源", async ({ page }) => {
    await askFirstStarter(page);

    // 答案非空
    const answerText = await page.locator("[data-answer]").innerText();
    expect(answerText.trim().length).toBeGreaterThan(0);

    // 四則來源列（含判決）
    await expect(page.locator(".src-row")).toHaveCount(4);
    await expect(
      page.locator('.src-row[data-filename="個人資料保護法-第12條.txt"]')
    ).toBeVisible();

    // 答案內腳註渲染成上標引用（含判決 [^4]）
    await expect(page.locator("[data-answer] sup a.cite-ref")).toHaveCount(4);
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

  test("開啟官方原文為外連連結（缺口⑥）", async ({ page }) => {
    await askFirstStarter(page);
    await page.locator('[data-answer] .cite-ref[data-ref="1"]').click();
    await expect(page.locator("#offcanvas")).toHaveClass(/open/);

    // 不再是展開重複內容，而是指向全國法規資料庫官方原文的真連結
    const link = page.locator(".evi-card").first().locator("a.open-link");
    await expect(link).toBeVisible();
    await expect(link).toHaveAttribute(
      "href",
      /law\.moj\.gov\.tw\/LawClass\/LawSingle\.aspx\?pcode=.+&flno=12/
    );
    await expect(link).toHaveAttribute("target", "_blank");
  });

  test("表格平滑渲染為 HTML 表格、無殘留 markdown 符號（缺口②③）", async ({ page }) => {
    await askFirstStarter(page);
    const answer = page.locator("[data-answer]");
    await expect(answer.locator("table")).toBeVisible();
    await expect(answer.locator("table thead th")).toHaveCount(3);
    // final 後不應殘留暫顯尾段或未渲染的分隔列符號
    await expect(answer.locator(".md-pending")).toHaveCount(0);
    await expect(answer).not.toContainText("|---");
  });

  test("確定性層級標籤渲染為 chip（缺口⑦）", async ({ page }) => {
    await askFirstStarter(page);
    const answer = page.locator("[data-answer]");
    await expect(answer.locator(".tier-explicit")).toContainText("明文");
    await expect(answer.locator(".tier-interpret")).toBeVisible();
  });

  test("引證依據誠實標示『答案引用來源』（缺口④）", async ({ page }) => {
    await askFirstStarter(page);
    await page.locator('[data-answer] .cite-ref[data-ref="1"]').click();
    await expect(page.locator("#offcanvas")).toHaveClass(/open/);
    await expect(page.locator("#oc-body")).toContainText("答案引用來源");
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

  test("串流未收到 final 時仍降級顯示答案 + 誠實提示（截斷根因防護）", async ({ page }) => {
    await page.goto("/");
    // __NOFINAL__ 讓 mock 只送 delta + [DONE]、不送 final（模擬截斷/連線中斷）
    await page.locator("#q").fill("__NOFINAL__ 個資外洩要通報誰？");
    await page.locator("#ask-form button[type=submit]").click();

    const answer = page.locator("[data-answer]");
    await expect(answer).toBeVisible();
    // 降級兜底：答案文字仍完整渲染（不再卡在串流截斷狀態），且表格成形
    await expect(answer).toContainText("通知當事人");
    await expect(answer.locator("table")).toBeVisible();
    await expect(answer.locator(".md-pending")).toHaveCount(0);
    // 誠實提示「連線中斷 / 可能不完整」
    await expect(answer.locator("xpath=following-sibling::p")).toContainText("不完整");
    // 追問建議獨立於 final，仍應載入
    await expect(page.locator(".chip.followup").first()).toBeVisible();
  });

  test("終結但無內容（failed）時顯示誠實錯誤、不留空白泡泡", async ({ page }) => {
    await page.goto("/");
    await page.locator("#q").fill("__FAILEMPTY__ 個資外洩");
    await page.locator("#ask-form button[type=submit]").click();

    const answer = page.locator("[data-answer]");
    await expect(answer).toContainText("發生錯誤");
    // 不留空白、不誤渲染來源
    await expect(page.locator(".src-row")).toHaveCount(0);
  });

  test("依據欄用 inline 引用標記時，最終以後端腳註渲染、欄位不空且可點（截斷欄根因）", async ({ page }) => {
    await page.goto("/");
    await page.locator("#q").fill("__MARKERS__ 高風險客戶要加強哪些面向");
    await page.locator("#ask-form button[type=submit]").click();

    const answer = page.locator("[data-answer]");
    await expect(answer.locator("table")).toBeVisible();
    // 依據欄轉成可點腳註上標（非空白）
    await expect(answer.locator("sup a.cite-ref")).toHaveCount(2);
    // 不可殘留原始 PUA 引用標記或 turnXfileY 文字
    await expect(answer).not.toContainText("filecite");
    await expect(answer).not.toContainText("turn0file");
    // 來源列有出現（cited）
    await expect(page.locator(".src-row")).toHaveCount(1);
  });

  test("判決見解證據卡顯示（無 url 走優雅降級顯示 snippet）", async ({ page }) => {
    await test.step("問答完成，等待串流 final", async () => {
      await askFirstStarter(page);
    });

    await test.step("點第 4 個腳註引用（判決）開 offcanvas", async () => {
      await page.locator('[data-answer] .cite-ref[data-ref="4"]').click();
      await expect(page.locator("#offcanvas")).toHaveClass(/open/);
    });

    await test.step("判決見解卡顯示 snippet 文字", async () => {
      // 在 offcanvas 的 #oc-body 內定位 .evi-card，排除 src-row
      const judgmentCard = page
        .locator("#oc-body .evi-card")
        .filter({ hasText: "臺北地院-判決-個資外洩損害賠償案.txt" });
      await expect(judgmentCard).toBeVisible();
      // snippet 含「通知義務」（來自 evidence text）
      await expect(judgmentCard).toContainText("通知義務");
    });

    await test.step("無 url → 不渲染外連 a.open-link（優雅降級）", async () => {
      const judgmentCard = page
        .locator("#oc-body .evi-card")
        .filter({ hasText: "臺北地院-判決-個資外洩損害賠償案.txt" });
      // 判決無 url，不得有外連連結
      await expect(judgmentCard.locator("a.open-link")).toHaveCount(0);
      // 改為展開全文按鈕
      await expect(judgmentCard.locator("button.open-full")).toBeVisible();
    });
  });

  test("跨裝置（手機）問答流程 @mobile", async ({ page }) => {
    // chromium-mobile project 已用 Pixel 5；此處再明確設一個窄 viewport 作雙保險。
    await page.setViewportSize({ width: 390, height: 780 });
    await askFirstStarter(page);

    // 答案與來源在窄螢幕仍顯示
    await expect(page.locator("[data-answer]")).toContainText("通知當事人");
    await expect(page.locator(".src-row")).toHaveCount(4);

    // offcanvas 在手機仍能開（寬度 84%/86vw 不細驗）
    await page.locator('[data-answer] .cite-ref[data-ref="1"]').click();
    await expect(page.locator("#offcanvas")).toHaveClass(/open/);
    await expect(page.locator(".evi-card.active")).toContainText("第12條");
  });
});
