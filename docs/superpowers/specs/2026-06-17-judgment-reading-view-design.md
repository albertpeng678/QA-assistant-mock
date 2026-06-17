# 判決長文閱讀檢視（Judgment Reading View）— 設計

> 日期：2026-06-17
> 範圍：前端單檔（`static/index.html`）。**不動後端、不動 SSE/問答邏輯、不動既有資料契約。**
> 設計系統：嚴格遵循 `design-system.md`「Airy Glass」token（navy/teal、oklch、Hanken Grotesk + Spline Sans）。

## 1. 背景與問題

引證依據（offcanvas 左抽屜，寬 340px / max 86vw）的來源卡，目前以 8 行夾制片段呈現，按「展開原文全文」會把**整份判決全文 inline 塞進這個窄抽屜**。判決全文常達數千字，於是出現：

- 窄欄每行僅約 17–22 個中文字（遠低於舒適行寬），形成文字牆；
- 被引用的關鍵段落埋在深處（NN/g：81% 注意力在前三屏），使用者找不到也讀不下去；
- 三種裝置（mobile / tablet / desktop）都受影響，手機尤甚。

## 2. 研究依據（NN/g，摘要）

7 路 NN/g 調研收斂結論（完整引用見對話紀錄）：

- **長內容不該塞窄 overlay/inline 展開**：太長就升級到寬容器或全頁（Overuse of Overlays、Accordions for Complex Content、Progressive Disclosure ≤2 層）。
- **行寬/易讀性**：舒適行寬 50–75 字元；窄視口損害理解（Chunking、Legibility）。
- **回溯驗證**：使用者很少點引用卻過度信任；驗證摩擦高就不做 → 深連到「確切被引段落」最關鍵（Explainable AI、In-Page Links）。
- **響應式**：長內容在手機應全螢幕，勿沿用窄抽屜（Bottom Sheets、Mobile First not Mobile Only）。
- **可信**：連權威原文、明示「請對照原文」（Trustworthy Design、Regulatory Usability）。

## 3. 現有資料契約（v1 只能用這些）

`app/rag.py` 產出的每筆 evidence：

| 欄位 | 內容 | v1 用途 |
|---|---|---|
| `filename` | 來源檔名（判決字號 / 法條名） | 標題、來源標籤 |
| `text` | **被檢索回的 chunk**（同檔已 merge 最高分段）＝答案實際引用片段 | 「答案引用段落」釘頂、卡片高亮 |
| `score` | 相關度 | （沿用既有顯示） |
| `file_id` | vector store 檔 id | 取全文 `/api/source_vs/{file_id}` |
| `url` | 官方原文連結（**僅部分來源有；判決無**） | 有才顯示「官方原文」 |

全文取回（既有端點，不改）：
- `/api/source/{filename}` → `{text}`（本地法條等）
- `/api/source_vs/{file_id}` → `{text}`（判決，自 vector store；純文字、保留換行）

## 4. 目標 / 非目標

**目標（v1）**
- 把長判決全文移出 340px 窄抽屜，提供可讀的寬閱讀面（響應式）。
- 「引用擷取 → 原文回溯」：被引段落**釘在閱讀面最上方**（保證有，來自 `evidence.text`），並在全文中**盡力定位高亮**。
- 短來源維持輕量：抽屜內 inline 展開，不過度升級。
- 全程只用現有後端與資料契約；純前端；不改 SSE/問答邏輯。

**非目標（v1 明確排除，無資料源 / 需額外後端）**
- AI 生成的「為何相關」一句摘要。
- 判決的官方司法院連結（目前無 url）。
- 判決分章目錄（主文/事實/理由/爭點）——全文為純文字、解析可靠度不足，延後。

## 5. 設計

### 5.1 觸發規則（依長度，type-agnostic）
點來源/「展開原文」時取回全文後，依**全文字數**決定呈現：
- `len < THRESHOLD`（預設 ~600 字，常數可調）→ **抽屜內 inline 展開**（沿用既有 `.legal-pre`）。
- `len ≥ THRESHOLD` → **開判決閱讀檢視**。
- 標籤提示：`filename` 含「判決」者，按鈕顯「閱讀判決全文」；否則「展開原文」。最終呈現仍以取回長度為準（長函釋也會自動升級）。

### 5.2 來源卡（抽屜，全來源適用）
- 沿用既有 evi-card 結構與既有 `renderEvidence()` 流程。
- 片段區改以「**答案引用段落**」teal 高亮樣式呈現 `evidence.text`（取代純灰片段）。
- 動作列：長→「閱讀判決全文 ›」；短→ inline 展開；`url` 存在→「↗ 官方原文」。
- **不**加 AI 摘要行。

### 5.3 判決閱讀檢視（新元件）
單一響應式容器，依斷點換角色：
- **Desktop（≥1024px）**：master–detail——左窄「引證清單」（被引片段即內容）＋右寬「判決閱讀欄」。
- **Tablet（640–1024px）**：寬版面板覆蓋。
- **Mobile（<640px）**：全螢幕閱讀檢視。

內容結構（三裝置一致）：
1. 頂列：返回/關閉、標題（`filename`／判決字號）、（有 `url` 才有）官方原文。
2. **釘頂「答案引用段落」callout**：`evidence.text`，teal 高亮（§Airy Glass `--brand-accent`）。**一定顯示**。
3. **判決全文**：`/api/source_vs` 或 `/api/source` 取回，`white-space:pre-wrap` 保留原始換行；閱讀欄 `max-width ~600–680px` 置中。
4. 全文中**盡力高亮**被引段落（見 5.4）；命中才標、附「（自動定位）」字樣。
5. 回頂部鈕；底部「⚠ 請對照原文判決核對」。
6. 關閉/Esc/系統返回鍵可離開；保留可選取文字（Ctrl-F 找字）。

### 5.4 回溯高亮（盡力、可降級）
- 以 `evidence.text` 對全文做**正規化模糊比對**（去除空白差異；取片段前 N 字或最長共同子字串為錨點）。
- 命中 → 在全文中以 teal 背景＋底線標記，並可由釘頂 callout 的「↓ 已在全文中定位」捲動過去。
- **未命中 → 不標記**；釘頂 callout 已保證「被引段落」可見，功能不退化。
- 比對純前端字串處理，不呼叫後端。

### 5.5 排版（NN/g + CJK 實務）
- 閱讀欄 `max-width` ~600–680px、置中、左右留白。
- 字級 15–16px、中文行高 ~1.85、段落留白；保留判決原始換行。
- 色彩/字體/圓角/陰影一律引用 Airy Glass token，無寫死字面值、無 emoji（icon 用既有 Lucide）。

## 6. 實作方式（低侵入）
- **唯一改動檔**：`static/index.html`（CSS + `renderEvidence()` / `openFullText()` 渲染分支 + 新增閱讀檢視 DOM/控制）。
- **不改**：後端、API、SSE、`submit()`/`finalizeAnswer()` 問答主流程、既有 DOM ID 與資料契約。
- 新增元件以既有 offcanvas/scrim 模式延伸（同樣式語彙），桌機 master–detail 以 CSS 斷點切換。
- 閾值、行寬等以常數集中，便於調整。

## 7. Edge cases / 優雅降級
- 全文取回失敗（兩端點皆 404）→ 沿用既有「請參考上方摘要」，閱讀檢視仍顯示釘頂片段。
- `evidence.text` 為空 → 不顯示釘頂 callout，僅全文。
- 模糊比對未命中 → 不高亮（不報錯）。
- 延續輪/無 evidence → 沿用既有空狀態。
- `prefers-reduced-motion` / `prefers-reduced-transparency` → 沿用既有 fallback。

## 8. 驗證計畫
- Playwright 跨裝置（mobile/tablet/desktop viewport）視覺驗證：空狀態、短來源 inline、長判決閱讀檢視、回溯高亮命中/未命中兩路、回頂部、關閉。
- 以 mock/注入資料跑既有渲染管線（不需真實 OpenAI）；長度觸發兩側都測。
- 對照 `design-system.md`：無寫死色票、無 emoji、token 引用正確。

## 9. 部署
- 純前端、純樣式 + 渲染分支；commit 後由使用者親自 push 觸發 Railway 部署（沿用現行流程）。
