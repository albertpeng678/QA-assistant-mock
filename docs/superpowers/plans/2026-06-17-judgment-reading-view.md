# 判決長文閱讀檢視 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把長判決全文移出 340px 窄抽屜，改以響應式「判決閱讀檢視」呈現——被引段落釘頂、全文寬欄可讀、盡力定位高亮；短來源維持抽屜內 inline。

**Architecture:** 純前端，唯一改 `static/index.html`（CSS + 既有 `renderEvidence`/`openFullText` 渲染分支 + 新增閱讀檢視面板，沿用既有 offcanvas/scrim 樣式語彙）。不動後端、API、SSE、問答主流程與資料契約。測試以 Playwright e2e 對既有 `scripts/mock_server.py`（8001）跑；mock 補長判決 fixture。

**Tech Stack:** HTML + Tailwind CDN + 原生 JS（marked/DOMPurify 既有）；Airy Glass oklch token（已在 `:root`）；Playwright（desktop + `@mobile`）。

**設計依據：** `docs/superpowers/specs/2026-06-17-judgment-reading-view-design.md`

**常數：** 閱讀檢視觸發閾值 `READING_VIEW_THRESHOLD = 600`（字元）。

---

### Task 1: Mock fixture — 長判決全文 + 可命中/未命中高亮的被引片段

**Files:**
- Modify: `scripts/mock_server.py:41-57`（EVIDENCE 判決項與 `JUDGMENT_FULL_TEXT`）

理由：現有 `JUDGMENT_FULL_TEXT` 偏短（<600 字）且判決 evidence.text 與全文非同句，無法驗「觸發 + 高亮命中」。需要：(a) 全文 ≥ 閾值；(b) 判決 evidence.text 是全文裡某句的**逐字子字串**（驗命中）。短來源（§12，<600 字、走 `/api/source`）維持現狀驗 inline。

- [ ] **Step 1: 加長 `JUDGMENT_FULL_TEXT` 並讓被引句逐字內嵌**

把 `scripts/mock_server.py` 的判決 evidence.text 與 `JUDGMENT_FULL_TEXT` 改為（被引句 `__CITEDSENT__` 標記處為兩者共用的逐字句）：

```python
    {"filename": "臺北地院-判決-個資外洩損害賠償案.txt",
     "text": "按個人資料保護法第12條，非公務機關違反本法規定，致個人資料被竊取、洩漏或其他侵害者，應查明後以適當方式通知當事人。",
     "score": 0.41, "doc_type": "判決", "file_id": "file_mockjudgment01"},
]

# 判決原文全文（mock /api/source_vs）——加長至 ≥600 字，且內含與 evidence.text 逐字相同的被引句
JUDGMENT_FULL_TEXT = (
    "來源:司法院裁判書系統|效力:判決|字號:TPDV,112,訴,999|裁判日:2024-06-26|母法:個人資料保護法\n\n"
    "臺灣臺北地方法院民事判決\n112年度訴字第999號\n\n"
    "主　文\n被告應給付原告新臺幣伍萬元，及自起訴狀繕本送達翌日起至清償日止，按年息百分之五計算之利息。訴訟費用由被告負擔。\n\n"
    "事實及理由\n"
    "一、原告主張：被告經營網路購物平台，因系統安全維護不足，致原告之姓名、地址、電話等個人資料遭駭客竊取外洩，原告嗣後接獲詐騙電話受有損害，爰依個人資料保護法第29條請求損害賠償。\n"
    "二、被告抗辯：外洩事故肇因於不可抗力之新型攻擊，且已於事後公告週知，並無過失，原告所受損害與外洩間亦無因果關係。\n"
    "三、本院之判斷：按個人資料保護法第12條，非公務機關違反本法規定，致個人資料被竊取、洩漏或其他侵害者，應查明後以適當方式通知當事人。被告於知悉外洩事故後逾二月始以網站公告為之，未個別通知當事人，難認已盡即時且適當之通知義務，違反通知義務明確。又被告就資料庫之安全維護顯有疏失，與原告個資外洩具相當因果關係。\n"
    "四、損害賠償之範圍：原告就實際財產損害未能舉證，惟其精神上所受痛苦，審酌兩造身分、經濟狀況及侵害情節，認以新臺幣伍萬元為適當。\n"
    "五、綜上所述，原告依個資法第29條請求被告賠償，為有理由，應予准許。\n\n"
    "中華民國113年6月26日\n民事第三庭　法官　○○○"
)
```

- [ ] **Step 2: 確認長度與內嵌**

Run: `python -c "import scripts.mock_server as m; t=m.JUDGMENT_FULL_TEXT; e=[x for x in m.EVIDENCE if x.get('doc_type')=='判決'][0]['text']; print('len',len(t)); print('embed', e in t)"`
Expected: `len` ≥ 600 且 `embed True`

- [ ] **Step 3: Commit**

```bash
git add scripts/mock_server.py
git commit -m "test(mock): 加長判決全文 + 被引句逐字內嵌（閱讀檢視 e2e fixture）"
```

---

### Task 2: CSS — 判決閱讀檢視樣式（Airy Glass，含響應式與高亮）

**Files:**
- Modify: `static/index.html`（`<style>` 區塊末，offcanvas 樣式之後）

沿用既有 `:root` 的 Airy Glass token（`--primary`/`--brand-accent`/`--surface`/`--ink-12` 等已存在）。新增閱讀檢視專屬樣式。桌機=右側寬面板（與左 offcanvas 並存＝master–detail）；手機=全螢幕。

- [ ] **Step 1: 加入閱讀檢視樣式**

在 `<style>` 內既有 `#offcanvas` 區塊後新增：

```css
  /* —— 判決閱讀檢視（長文：移出窄抽屜的寬閱讀面）—— */
  #rv-scrim{ position:fixed; inset:0; background:oklch(0.225 0.018 262 / 0.40); opacity:0; pointer-events:none; transition:opacity .25s; z-index:60; }
  #rv-scrim.open{ opacity:1; pointer-events:auto; }
  #reading-view{ position:fixed; top:0; right:0; bottom:0; width:min(760px,94vw); background:var(--surface);
                 border-left:1px solid var(--ink-12); box-shadow:var(--shadow-lg);
                 transform:translateX(100%); transition:transform .28s cubic-bezier(.4,0,.2,1); z-index:70;
                 display:flex; flex-direction:column; }
  #reading-view.open{ transform:translateX(0); }
  @media (max-width:640px){ #reading-view{ width:100vw; border-left:none; } }   /* 手機全螢幕 */
  .rv-bar{ display:flex; align-items:center; gap:9px; padding:12px 16px; flex:none;
           background:var(--glass); border-bottom:1px solid var(--glass-border);
           -webkit-backdrop-filter:blur(16px) saturate(150%); backdrop-filter:blur(16px) saturate(150%); }
  .rv-bar .rv-tt{ flex:1; min-width:0; }
  .rv-bar .rv-tt .t{ font-family:'Hanken Grotesk',sans-serif; font-weight:600; font-size:14px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
  .rv-bar .rv-tt .m{ font-size:11px; color:var(--ink-50); white-space:nowrap; overflow:hidden; text-overflow:ellipsis; margin-top:1px; }
  .rv-ext{ display:inline-flex; align-items:center; gap:4px; font-size:11px; font-weight:600; color:var(--blood); border:1px solid var(--glass-border); border-radius:8px; padding:5px 9px; background:var(--surface); flex:none; text-decoration:none; }
  .rv-body{ flex:1; overflow-y:auto; overscroll-behavior:contain; -webkit-overflow-scrolling:touch; padding:16px 18px; }
  .rv-col{ max-width:640px; margin:0 auto; }
  .rv-pin{ background:oklch(0.665 0.105 200 / 0.16); border:1px solid oklch(0.665 0.105 200 / 0.45); border-radius:11px; padding:12px 14px; margin-bottom:14px; }
  .rv-pin .tg{ display:inline-flex; align-items:center; gap:4px; font-family:'Hanken Grotesk',sans-serif; font-size:10.5px; font-weight:700; color:var(--paper); background:var(--brand-accent); border-radius:7px; padding:2px 9px; margin-bottom:7px; }
  .rv-pin p{ margin:0; font-size:14.5px; line-height:1.85; }
  .rv-pin .jump{ margin-top:8px; font-size:11px; font-weight:600; color:var(--blood); background:none; border:none; cursor:pointer; padding:0; }
  .rv-flabel{ display:flex; align-items:center; gap:6px; font-size:10.5px; font-weight:700; letter-spacing:.04em; color:var(--ink-50); text-transform:uppercase; margin:6px 0 8px; }
  .rv-full{ white-space:pre-wrap; word-break:break-word; font-size:15px; line-height:1.85; color:var(--ink); }   /* 保留判決原始換行 */
  .rv-full mark.rv-hit{ background:oklch(0.665 0.105 200 / 0.20); border-bottom:2px solid oklch(0.665 0.105 200 / 0.45); border-radius:3px; padding:1px 0; color:inherit; }
  .rv-hitnote{ font-size:10px; color:var(--ink-50); font-style:italic; margin-left:4px; }
  .rv-b2t{ position:absolute; right:16px; bottom:18px; width:36px; height:36px; border-radius:999px; background:var(--blood); color:var(--paper);
           display:flex; align-items:center; justify-content:center; box-shadow:var(--shadow-lg); cursor:pointer; opacity:0; pointer-events:none; transition:opacity .2s; border:none; }
  .rv-b2t.show{ opacity:1; pointer-events:auto; }
  .rv-foot{ flex:none; border-top:1px solid var(--border); background:var(--glass); padding:9px 16px; font-size:11px; color:var(--ink-50); display:flex; align-items:center; gap:6px; }
  @media (prefers-reduced-transparency: reduce){ .rv-bar,.rv-foot{ background:var(--surface); backdrop-filter:none; -webkit-backdrop-filter:none; } }
```

- [ ] **Step 2: Commit**

```bash
git add static/index.html
git commit -m "feat(ui): 判決閱讀檢視樣式（Airy Glass，右側寬面板/手機全螢幕）"
```

---

### Task 3: 閱讀檢視 DOM 容器 + 開關控制（先不接資料）

**Files:**
- Modify: `static/index.html`（`<aside id="offcanvas">…</aside>` 之後加 DOM；`<script>` 內加控制函式）
- Test: `tests/e2e/ask.spec.ts`

- [ ] **Step 1: 寫 e2e 測試（紅）— 開關容器存在**

在 `tests/e2e/ask.spec.ts` 末新增：

```ts
test("判決閱讀檢視：容器存在且預設關閉", async ({ page }) => {
  await page.goto("/");
  const rv = page.locator("#reading-view");
  await expect(rv).toHaveCount(1);
  await expect(rv).not.toHaveClass(/open/);
});
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `npx playwright test -g "容器存在且預設關閉" --project=chromium-desktop`
Expected: FAIL（`#reading-view` count 0）

- [ ] **Step 3: 加入 DOM 與控制函式**

在 `static/index.html` 的 `</aside>`（offcanvas 結尾）後新增：

```html
  <!-- 判決閱讀檢視（長文用） -->
  <div id="rv-scrim"></div>
  <aside id="reading-view" role="dialog" aria-modal="true" aria-label="判決閱讀檢視">
    <div class="rv-bar">
      <button id="rv-close" aria-label="關閉" class="bar-btn inline-flex items-center justify-center w-8 h-8 rounded-lg" style="border:1px solid var(--border)"><i data-lucide="chevron-left" class="icon-sm"></i></button>
      <div class="rv-tt"><div class="t" id="rv-title"></div><div class="m" id="rv-meta"></div></div>
      <a id="rv-ext" class="rv-ext hidden" target="_blank" rel="noopener noreferrer"><i data-lucide="external-link" class="icon-xs"></i> 官方原文</a>
    </div>
    <div class="rv-body" id="rv-body"></div>
    <button id="rv-b2t" class="rv-b2t" aria-label="回頂部"><i data-lucide="arrow-up" class="icon-sm"></i></button>
    <div class="rv-foot"><i data-lucide="alert-triangle" class="icon-xs"></i> 全文取自判決書，AI 摘要僅供研究參考，請對照原文核對。</div>
  </aside>
```

在 `<script>` 內（offcanvas 控制附近）新增：

```js
/* —— 判決閱讀檢視控制 —— */
const READING_VIEW_THRESHOLD = 600;
const rvScrim = document.getElementById("rv-scrim");
const readingView = document.getElementById("reading-view");
const rvBody = document.getElementById("rv-body");
function closeReadingView(){ rvScrim.classList.remove("open"); readingView.classList.remove("open"); }
document.getElementById("rv-close").addEventListener("click", closeReadingView);
rvScrim.addEventListener("click", closeReadingView);
document.addEventListener("keydown", e=>{ if(e.key==="Escape" && readingView.classList.contains("open")) closeReadingView(); });
// 回頂部：捲動 >2 屏才顯
const rvB2t = document.getElementById("rv-b2t");
rvBody.addEventListener("scroll", ()=> rvB2t.classList.toggle("show", rvBody.scrollTop > rvBody.clientHeight*2));
rvB2t.addEventListener("click", ()=> rvBody.scrollTo({ top:0, behavior:"smooth" }));
```

- [ ] **Step 4: 跑測試確認通過**

Run: `npx playwright test -g "容器存在且預設關閉" --project=chromium-desktop`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add static/index.html tests/e2e/ask.spec.ts
git commit -m "feat(ui): 判決閱讀檢視 DOM + 開關/Esc/回頂部控制"
```

---

### Task 4: 回溯定位工具 + 渲染閱讀檢視（釘頂片段 + 全文 + 盡力高亮）

**Files:**
- Modify: `static/index.html`（`<script>`：新增 `findCitedRange` 與 `renderReadingView`）
- Test: `tests/e2e/ask.spec.ts`

- [ ] **Step 1: 寫 e2e 測試（紅）— 釘頂片段 + 全文 + 命中高亮**

測試走完整流程：送出問題 → 開引證 offcanvas → 點判決來源 → 開閱讀檢視。新增：

```ts
test("判決閱讀檢視：釘頂被引段落、顯示全文、命中高亮 @mobile", async ({ page }) => {
  await page.goto("/");
  await page.getByLabel("輸入法遵問題").fill("個資外洩通報");
  await page.getByLabel("送出").click();
  await expect(page.locator("[data-answer]")).toBeVisible();
  // 開引證抽屜並點判決來源
  await page.getByRole("button", { name: /展開引證：臺北地院-判決/ }).click();
  await page.getByRole("button", { name: /閱讀判決全文|展開原文全文/ }).first().click();
  const rv = page.locator("#reading-view");
  await expect(rv).toHaveClass(/open/);
  // 釘頂片段
  await expect(rv.locator(".rv-pin p")).toContainText("應查明後以適當方式通知當事人");
  // 全文
  await expect(rv.locator(".rv-full")).toContainText("臺灣臺北地方法院民事判決");
  // 命中高亮（被引句逐字內嵌 → 應標記）
  await expect(rv.locator(".rv-full mark.rv-hit")).toHaveCount(1);
});
```

> 註：本測試標 `@mobile`，desktop project 亦會跑（grep 僅限制 mobile project 只跑 @mobile，不排除 desktop 跑它）。

- [ ] **Step 2: 跑測試確認失敗**

Run: `npx playwright test -g "釘頂被引段落" --project=chromium-desktop`
Expected: FAIL（尚無 `renderReadingView`/按鈕未接）

- [ ] **Step 3: 實作定位與渲染函式**

在 `<script>` 內新增：

```js
/* 正規化模糊比對：以被引片段前綴為錨點，回傳全文中 [start,end) 或 null */
function findCitedRange(fullText, excerpt){
  if(!fullText || !excerpt) return null;
  const norm = s => s.replace(/\s+/g, "");
  const nf = norm(fullText), ne = norm(excerpt);
  if(!ne) return null;
  // 建正規化索引→原始索引對照
  const map = []; for(let i=0;i<fullText.length;i++){ if(!/\s/.test(fullText[i])) map.push(i); }
  // 先試整段，再退而求其次用前 24 字錨點
  for(const probe of [ne, ne.slice(0,24)]){
    const at = nf.indexOf(probe);
    if(at >= 0 && probe.length >= 8){
      const start = map[at];
      const end = map[Math.min(at + probe.length, map.length) - 1] + 1;
      return { start, end };
    }
  }
  return null;
}

/* 渲染閱讀檢視：釘頂片段（evidence.text）+ 全文 + 盡力高亮 */
function renderReadingView(ev, fullText){
  const range = findCitedRange(fullText, ev.text || "");
  let fullHtml;
  if(range){
    fullHtml = escapeHtml(fullText.slice(0,range.start))
      + `<mark class="rv-hit" id="rv-hit">` + escapeHtml(fullText.slice(range.start,range.end)) + `</mark>`
      + `<span class="rv-hitnote">（自動定位）</span>`
      + escapeHtml(fullText.slice(range.end));
  } else {
    fullHtml = escapeHtml(fullText);
  }
  const pin = (ev.text||"").trim()
    ? `<div class="rv-pin"><span class="tg"><i data-lucide="quote" class="icon-xs"></i> 答案引用段落</span><p>${escapeHtml(ev.text)}</p>${range?`<button class="jump" id="rv-jump">↓ 已在全文中定位</button>`:""}</div>`
    : "";
  rvBody.innerHTML = pin
    + `<div class="rv-flabel"><i data-lucide="file-text" class="icon-xs"></i> 判決全文</div>`
    + `<div class="rv-full">${fullHtml}</div>`;
  document.getElementById("rv-title").textContent = ev.filename || "原文";
  const meta = document.getElementById("rv-meta"); meta.textContent = "";
  const ext = document.getElementById("rv-ext");
  if(ev.url){ ext.href = ev.url; ext.classList.remove("hidden"); } else { ext.classList.add("hidden"); }
  rvBody.scrollTop = 0;
  icons();
  const jump = document.getElementById("rv-jump");
  if(jump) jump.addEventListener("click", ()=> document.getElementById("rv-hit")?.scrollIntoView({ block:"center", behavior:"smooth" }));
}
function openReadingView(ev, fullText){ renderReadingView(ev, fullText); rvScrim.classList.add("open"); readingView.classList.add("open"); }
```

- [ ] **Step 4: 跑測試（仍會失敗——按鈕尚未呼叫 openReadingView，Task 5 接線）**

Run: `npx playwright test -g "釘頂被引段落" --project=chromium-desktop`
Expected: FAIL（按鈕尚未觸發開啟）— Task 5 完成後轉綠。

- [ ] **Step 5: Commit**

```bash
git add static/index.html tests/e2e/ask.spec.ts
git commit -m "feat(ui): 閱讀檢視渲染 + 被引段落模糊定位高亮（findCitedRange）"
```

---

### Task 5: 長度觸發 — 接線 `openFullText` 分流（長→閱讀檢視；短→ inline）

**Files:**
- Modify: `static/index.html`（既有 `openFullText` 函式；`renderEvidence` 內按鈕標籤）

既有 `openFullText(btn)` 會打 `/api/source_vs`→`/api/source` 取回 `{text}`。改為：取回後依長度分流。

- [ ] **Step 1: 改 `openFullText` 加長度分流**

把既有 `openFullText` 取回成功的區段改為（保留既有端點 fallback 迴圈）：

```js
async function openFullText(btn){
  const card = btn.closest(".evi-card");
  const box = card.querySelector(".full-text");
  const fid = btn.dataset.fileId;
  const fname = btn.dataset.filename;
  const ev = state.lastEvidence.find(e => (e.filename||"") === fname) || { filename:fname, text:"" };
  const endpoints = [];
  if(fid) endpoints.push("/api/source_vs/"+encodeURIComponent(fid));
  endpoints.push("/api/source/"+encodeURIComponent(fname));
  // 既有 inline 展開保留作短文降級
  let fullText = box.dataset.loaded === "1" ? box.textContent : null;
  if(fullText == null){
    for(const ep of endpoints){
      try{ const r = await fetch(ep); if(!r.ok) continue; const d = await r.json(); if(d.text){ fullText = d.text; break; } }catch{}
    }
  }
  if(fullText == null){ box.classList.remove("hidden"); box.textContent = "原文尚未建立索引，請參考上方摘要。"; return; }
  // 長度觸發：長→閱讀檢視；短→ inline（既有行為）
  if(fullText.length >= READING_VIEW_THRESHOLD){
    openReadingView(ev, fullText);
  } else {
    if(!box.classList.contains("hidden")){ box.classList.add("hidden"); return; }  // toggle
    box.classList.remove("hidden"); box.textContent = fullText; box.dataset.loaded = "1";
  }
}
```

- [ ] **Step 2: 跑 Task 4 的測試確認轉綠**

Run: `npx playwright test -g "釘頂被引段落" --project=chromium-desktop`
Expected: PASS

- [ ] **Step 3: 寫短來源 inline 不觸發的測試（紅→綠）**

新增：

```ts
test("短來源：展開維持抽屜內 inline、不開閱讀檢視", async ({ page }) => {
  await page.goto("/");
  await page.getByLabel("輸入法遵問題").fill("個資外洩通報");
  await page.getByLabel("送出").click();
  await expect(page.locator("[data-answer]")).toBeVisible();
  await page.getByRole("button", { name: /展開引證：個人資料保護法-第12條/ }).click();
  await page.getByRole("button", { name: /展開原文全文/ }).first().click();
  await expect(page.locator("#reading-view")).not.toHaveClass(/open/);   // 不觸發
  await expect(page.locator(".evi-card .full-text").first()).toBeVisible(); // inline 顯示
});
```

Run: `npx playwright test -g "短來源" --project=chromium-desktop`
Expected: PASS（§12 全文 <600 字，走 inline）

> 前置確認：mock `/api/source/個人資料保護法-第12條.txt` 回的全文 <600 字。若 mock `/api/source` 找不到該檔，於 `scripts/mock_server.py` 的 `source()` 對未知檔回 `{"text": e["text"]}`（取 EVIDENCE 同名片段，必 <600）——若已如此則略過。

- [ ] **Step 4: Commit**

```bash
git add static/index.html tests/e2e/ask.spec.ts scripts/mock_server.py
git commit -m "feat(ui): 長度觸發分流——長判決開閱讀檢視、短來源維持 inline"
```

---

### Task 6: 來源卡片 — 被引段落 teal 高亮（取代純灰片段）

**Files:**
- Modify: `static/index.html`（既有 `renderEvidence` 內 `.evi-snippet` 區塊與其樣式）
- Test: `tests/e2e/ask.spec.ts`

- [ ] **Step 1: 寫測試（紅）— 卡片片段帶「答案引用段落」標記**

```ts
test("引證卡：被引片段以『答案引用段落』高亮呈現", async ({ page }) => {
  await page.goto("/");
  await page.getByLabel("輸入法遵問題").fill("個資外洩通報");
  await page.getByLabel("送出").click();
  await expect(page.locator("[data-answer]")).toBeVisible();
  await page.getByRole("button", { name: /展開引證/ }).first().click();
  await expect(page.locator(".evi-card .evi-cite-tag").first()).toContainText("答案引用");
});
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `npx playwright test -g "被引片段以" --project=chromium-desktop`
Expected: FAIL（無 `.evi-cite-tag`）

- [ ] **Step 3: 改 `renderEvidence` 片段呈現 + 加樣式**

在既有 `renderEvidence` 內，產生 `.evi-snippet` 的那段 HTML 前面加上標記，並把 snippet 包進高亮盒（保留既有 `evi-snippet` line-clamp）。將：

```js
        <div class="legal-pre evi-snippet font-body text-[13px] leading-relaxed text-[var(--ink-70)] mt-2">${escapeHtml(e.text||"")}</div>
```

改為：

```js
        <div class="evi-cite mt-2"><span class="evi-cite-tag"><i data-lucide="quote" class="icon-xs"></i> 答案引用段落</span>
          <div class="legal-pre evi-snippet font-body text-[13px] leading-relaxed mt-1">${escapeHtml(e.text||"")}</div></div>
```

在 `<style>` 加：

```css
  .evi-cite{ background:oklch(0.665 0.105 200 / 0.12); border:1px solid oklch(0.665 0.105 200 / 0.40); border-radius:8px; padding:8px 10px; }
  .evi-cite-tag{ display:inline-flex; align-items:center; gap:4px; font-family:'Hanken Grotesk',sans-serif; font-size:10px; font-weight:700; color:var(--brand-accent); margin-bottom:2px; }
```

- [ ] **Step 4: 跑測試確認通過**

Run: `npx playwright test -g "被引片段以" --project=chromium-desktop`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add static/index.html tests/e2e/ask.spec.ts
git commit -m "feat(ui): 引證卡被引片段以『答案引用段落』teal 高亮呈現"
```

---

### Task 7: 全套件回歸 + 跨裝置視覺驗證

**Files:**
- Test: 既有全 e2e 套件；手動 Playwright 視覺檢查

- [ ] **Step 1: 跑完整 e2e（desktop + mobile）**

Run: `npm run test:e2e`
Expected: 全綠（既有測試 + 本計畫新增測試；mobile project 跑 `@mobile` 標記者）

- [ ] **Step 2: 跨裝置視覺檢查（mock 8001）**

啟動 mock：`python scripts/mock_server.py`（背景），用 Playwright MCP 依序在 1280×800 / 768×1024 / 390×844 三 viewport：送出「個資外洩通報」→ 開引證 → 點判決來源 → 截圖閱讀檢視，確認：
- 桌機：右側寬面板與左 offcanvas 並存（master–detail），閱讀欄置中 ~640px，被引段落釘頂、全文命中高亮、回頂部鈕捲動後出現。
- 平板：寬面板覆蓋、單欄可讀。
- 手機：閱讀檢視全螢幕、釘頂片段 + 全文、底部對照提示。
- 配色/字體全為 Airy Glass token；無 emoji；無寫死色票（`grep -nE "#[0-9a-f]{6}" static/index.html` 僅剩既有 tier `#2e5a52`/`oklch(0.52 0.06 250)` 等預期值，無新增 hex）。

- [ ] **Step 3: 確認無殘留 hex / 對照設計系統**

Run: `grep -noiE "#7a2e2e|#1a1714|#f5f2ea|Fraunces|Spectral|Public Sans" static/index.html`
Expected: 空（本計畫不得引入 editorial 殘留）

- [ ] **Step 4: Commit（若視覺檢查有微調）**

```bash
git add static/index.html
git commit -m "polish(ui): 判決閱讀檢視跨裝置視覺微調"
```

---

## 部署
全部綠後，由使用者親自 `git push origin main` 觸發 Railway 部署（沿用現行流程；本計畫不自動 push）。

## Self-Review 註記
- Spec §5.1 觸發 → Task 5；§5.2 卡片 → Task 6；§5.3 閱讀檢視 → Task 3/4；§5.4 回溯高亮 → Task 4（`findCitedRange`）；§5.5 排版 → Task 2；§7 降級（取回失敗/未命中/空 text）→ Task 4/5 程式分支；§8 驗證 → Task 7。
- 函式命名一致：`findCitedRange` / `renderReadingView` / `openReadingView` / `openFullText` / `closeReadingView` 全篇一致。
- 非目標（AI 摘要 / 判決官方連結 / 分章目錄）未出現於任何 Task。
