# Spec — 答案清晰化與資訊架構（answer clarity + IA）

> 建立：2026-06-12｜分支：`feat/answer-clarity`｜
> 研究依據：context7（OpenAI Responses API verbosity / structured output）、web（Harvey/CoCounsel/Vincent/Lexis 等企業 legal AI）、NN/g（倒金字塔、Plain Language、Progressive Disclosure、Tabs/Accordion、Visual Hierarchy、Less Chat More Answer、IA Study Guide）。

## 1. 背景與問題

使用者回饋：現在的答案**太艱澀（legalese）且資訊密度過大**。診斷：我們**做反了最佳實務**——先給結構（表格／確定性分層 chip／腳註），結論埋在中段；一次全攤開；用法條原文。三路研究高度收斂於「倒金字塔 + 白話 + 漸進揭露 + 降密度」。

附帶觀察（使用者）：**確定性層級標籤實際很少出現在答案**——因為它靠模型自己在 markdown 寫 `【明文】` 等標記、前端 `tierTags()` 才轉成 chip；模型命中率不穩 → chip 常不顯。prompt-only 的層級標記不可靠（context7：enum 結構化才保證）。

## 2. 目標與非目標

### 目標
- 答案改走**三層資訊架構（IA）**：Layer 0 BLUF 結論 + 確定性 badge（永遠可見）→ Layer 1 重點/細節 → Layer 2 證據（沿用既有 offcanvas）→ Meta 範疇提示。
- **降密度**：`text.verbosity` `medium→low`；白話文（術語括號附條號）；表格只在 3+ 項比較時用；主體精簡（NN/g：可見主體精簡、Longer ≠ better）。
- **標籤可靠**：確定性層級在答案**開頭穩定出現一次**，前端可靠轉 Layer-0 badge。
- 套用到**所有答案**（既有問答 + 能力回答不適用層級）。

### 非目標（本 spec / 範圍 1）
- **不**改成 json_schema structured output、**不**動 SSE 串流為緩衝（保留逐字串流；schema 強制 IA 列為後續，見 §7）。
- **不**重構前端為折疊面板/手風琴（證據層已是 offcanvas；Layer 1 細節用 markdown 標題層次表達，不做收合 widget）。
- **不**改 `retrieve_dual`/引用機制。

## 3. 設計

### 3.1 API 參數
```python
_MODEL_PARAMS = {"reasoning": {"effort": "low"},
                 "text": {"verbosity": "low"},   # 變更：medium→low（context7：最高 CP 值降密度）
                 "max_output_tokens": 4096}       # 不動（截斷防護）
```

### 3.2 ANSWER_INSTRUCTIONS 重寫（倒金字塔 + 白話 + 降密度）
新指示結構（取代現行「表格為主」版）：
1. **第一行＝確定性層級標記（恰一個）**：`【明文】`／`【解釋/裁量】`／`【實務見解】`／`【語料未涵蓋】`，緊接一句**白話結論**（BLUF，≤40 字）。務必每次輸出且只輸出一個層級標記於開頭。
2. **接 2–4 個重點**（bullet，每點一句；白話＋括號附條號，如「公司不得買回自家股票（公司法§167）」）。
3. **需要時才展開細節**（段落或表格）；**表格只在 3+ 項比較**時用，否則用 bullet/句子。
4. 區分「法律未明文 vs 語料未收錄」；不確定用「依現有語料…」。
5. 結尾固定免責：本回答為研究輔助，非正式法律意見。
6. 風格：受高中教育者可懂；避免貼法條原文整段，改白話＋括號條號（NN/g Plain Language）。

### 3.3 標籤可靠性（解決使用者觀察）
- **主方案（範圍 1）**：靠 §3.2 第 1 點「第一行恰一個層級標記」的強制指示提升命中率；前端既有 `tierTags()` 轉 Layer-0 badge；**命中失敗 → 不顯 badge（優雅降級，不報錯）**。
- **備案（若實測命中率仍不足）**：加輕量 `classify_tier(client, answer, *, model)`（structured output enum，比照 `classify_intent`）做**確定性 100% 命中**的後分類，前端 badge 取自此值。代價：每題多一次 LLM 呼叫（~100ms）。**本 spec 預設主方案，實測決定是否啟用備案。**

### 3.4 前端（最小）
- markdown 配色已就緒（H2 血紅、層級 chip 墨/teal/amber/灰）。
- Layer-0 badge：沿用 `tierTags()`（答案開頭的 `【…】` → chip）。確認開頭單一標記時 chip 顯示在結論前。
- **不新增折疊 widget**；Layer 1 細節靠 markdown H2/H3 + bullet 表達層次（NN/g：可掃描即可，能滾動就不必折疊）。

### 3.5 易理解性稽核補強（UX audit 必補項，皆 prompt + CSS，仍屬範圍 1）
1. **BLUF 首行硬約束**：≤40 字、**只陳述「可做什麼＋關鍵期限（粗體）」**;所有否定/除外/定義一律下沉到 bullet。指示附正例：「**公開滿 18 小時後**才能買賣（證交法§157-1）。」否則 C1 復發。
2. **重點 bullet**：基準 3 個、上限 4;每點 ≤1 句,句首放動作/條件,法條括號收尾;超過 4 點才升級表格。
3. **表格裝置制約（context7/Tailwind v3 認證做法）**：
   - **`overflow-x-auto` 包裹**：用 **marked custom renderer** 覆寫 `table`,將 `<table>` 包進 `<div class="overflow-x-auto -mx-1">…</div>`（Tailwind class,CDN 已載）。寬表格在窄螢幕**橫向捲動**而非爆版,**無需 media query、全尺寸通用**（契合本產品零 `@media` 的事實）。
   - **源頭縮寬**：prompt 端表格**欄數上限 3**（4 欄禁用）+ 條號一律縮寫 `§157-1`（勿用「第157條之1第1項」長 token）,降低需捲動的機率。
   - 不採「表格→卡片堆疊」（需 media query + 複雜 CSS,較重）。context7 來源:`/websites/v3_tailwindcss` overflow / breakpoints。
   - 屬**前端**（marked renderer + CSS,走 live demo gate）。
4. **兩維度分離**：badge = 確定性層級（明文/解釋裁量/實務見解）;**scope/coverage（已收錄 vs 語料未收錄 vs 法律未明文）另以 meta 提示表達**,不擠進同一 enum。`classify_tier` enum 含 `語料未涵蓋` 以涵蓋「未收錄」。
5. **caveat 隔離**：免責聲明與不確定性以 blockquote/callout **視覺隔離**置於答案末端 meta 區,不混進內容流。
6. **關鍵數字強調一次**：BLUF 的關鍵數字/期限用 `**粗體**`,全答案僅一處主焦點（勿重複稀釋）。
7. **可驗證閱讀程度**：平均句長 ≤30 字、術語首見必括號白話、避免連續名詞堆疊——列入 cold-read 驗收清單。
8. **（後續）側欄來源分組/限縮**：來源過多時按 doc_type 分組或顯 top-N（非本次必做,記著）。

## 4. 驗證
- **單元**：ANSWER_INSTRUCTIONS 為字串，無法單測語意；改測「`_MODEL_PARAMS["text"]["verbosity"]=="low"`」與（若啟用備案）`classify_tier` 的 structured-output 解析/fallback。全 `pytest -q` 維持綠、`ruff` 過。
- **真實驗證（IL-2，cold-read）**：Playwright 對 mock + **production** 跑數題（明文題/裁量題/未涵蓋題），cold-read PNG 確認：結論在第一行、層級 badge 出現、白話、密度下降、表格只在比較題。**5x 一致**。
- **回歸**：既有 e2e 17 綠（注意：既有 e2e 若斷言舊密集格式需同步調整）。
- **production 部署後實測**（使用者要求）：push → 驗證上線 → cold-read 真實答案密度/結構。

## 5. 完成定義
1. `verbosity=low`、ANSWER_INSTRUCTIONS 重寫上線。
2. 真實答案：第一行 BLUF 結論 + 層級 badge 穩定出現；白話；密度明顯下降；表格只在比較題。
3. `pytest` 綠、`ruff` 過、e2e 綠、production cold-read 通過。

## 6. 風險
| 風險 | 緩解 |
|---|---|
| 層級標記命中率仍低 | §3.3 備案 classify_tier（structured，100% 命中）|
| verbosity low 過度精簡、漏重要資訊 | 指示明列「重點 2–4 點＋條號」；cold-read 驗證 |
| 既有 e2e 斷言舊格式 | 同步更新 e2e 期望 |
| 法遵正確性 | 仍嚴禁杜撰條號；白話僅改表達不改實質；保留免責 |

## 7. 後續（非本 spec）
- json_schema 強制 IA（tldr/key_points/tier/detail/citations）+ 前端分層面板 + SSE 緩衝（context7：強制 IA 最穩，但犧牲逐字串流、增延遲、需前端重構）。值得在 prompt 版實測後評估。
