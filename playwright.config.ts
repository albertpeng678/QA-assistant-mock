import { defineConfig, devices } from "@playwright/test";

/**
 * E2E 設定：對既有 mock server（scripts/mock_server.py，port 8001）跑測試。
 * mock server 提供固定假資料，不會打真實 OpenAI / DB。
 * webServer 區塊讓 Playwright 自動啟動 mock server 並等 /health 就緒。
 */
export default defineConfig({
  testDir: "./tests/e2e",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: [["list"], ["html", { open: "never" }]],
  use: {
    baseURL: "http://127.0.0.1:8001",
    trace: "on-first-retry",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
  },
  projects: [
    {
      name: "chromium-desktop",
      use: { ...devices["Desktop Chrome"] },
    },
    {
      name: "chromium-mobile",
      // 僅跑標了 @mobile 的測試（跨裝置流程）
      grep: /@mobile/,
      use: { ...devices["Pixel 5"] },
    },
  ],
  webServer: {
    command: "python scripts/mock_server.py",
    url: "http://127.0.0.1:8001/health",
    reuseExistingServer: !process.env.CI,
    timeout: 30_000,
  },
});
