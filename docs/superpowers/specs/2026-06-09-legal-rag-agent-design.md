# 法規 RAG 問答 agent — 設計規格（Design Spec）

> 日期：2026-06-09 ｜ 狀態：待使用者審閱
> 引擎：OpenAI file search（託管服務）。**不自建 RAG**（不裝向量資料庫 / LangChain）。

## 1. 目的與情境

企業法遵 × 法務的法規問答助理：對法遵語料（個資法、洗錢防制法等精選條文）做問答，**每個答案都附條文引用與可審查的引證依據**。使用者為法務 / 合規人員，重視精確引用、條號、可驗證性。

定位（依 NNgroup 調研）：模型是「**證據的敘述者**（narrator of evidence）」，非 oracle。設計刻意對抗 overtrust。

## 2. 範圍（Scope）

MVP + Evidence panel（左側 offcanvas）+ 多輪對話 + suggestion chips。對應工作坊 6 缺口：

| 缺口 | 本 spec 的決策 |
|---|---|
| 1 `parse_response` | 已完成；將**向後相容擴充**（多回 evidence、response_id） |
| 2 `answer_question` | file_search + `include=["file_search_call.results"]` + 選用 `previous_response_id` |
| 3 chunking | 缺口 3：context7 研究後填 `MAX_CHUNK_SIZE_TOKENS` / `CHUNK_OVERLAP_TOKENS`（法規逐條，傾向中小 chunk） |
| 4 前端 | 方向 A（Statute）+ 左側 offcanvas 引證 + 數字標記彙整來源 + suggestion chips + RWD |
| 5 E2E | playwright：問一題 → 看到答案 + 引用 + 可展開引證 |
| 6 監控 | Sentry：缺 key / vector store 空 / OpenAI 逾時 |

**不在範圍**：使用者帳號、對話歷史持久化（僅靠 `previous_response_id` 於 session 內串接）、原文新分頁路由（改抽屜內展開）。

## 3. 架構與資料流

```
前端 static/index.html
  └ POST /api/ask { question, previous_response_id? }
        └ answer_question(client, q, vector_store_id, model, previous_response_id?)
              └ client.responses.create(
                    model, input, previous_response_id?,
                    tools=[{type:file_search, vector_store_ids:[...]}],
                    include=["file_search_call.results"])
              └ parse_response(response) → { answer, citations, evidence, response_id }
        └ 回傳 { answer, citations, evidence, response_id }
  └ POST /api/suggest { question, answer }  → suggest_followups() → { questions:[...] }
  └ GET  /api/source/{filename}             → 回傳 data/ 內條文全文（offcanvas 內展開用）
```

### 3.1 後端契約（缺口 1/2 擴充，保持既有測試綠）

**`parse_response(response)` → dict**
- `answer: str` — 串接 message 的 output_text（**既有**）
- `citations: [str]` — 去重保序檔名（**既有**）
- `evidence: [{filename, text, score}]` — 取自 `file_search_call.results`（**新增**）
- `response_id: str | None` — `response.id`，供下一輪 `previous_response_id`（**新增**）
- 既有測試只斷言 `answer`/`citations` → 加欄位不破壞。新增欄位以 TDD 補新測試。

**`answer_question(client, question, *, vector_store_id, model, previous_response_id=None)` → dict**
- 呼叫 `responses.create`，帶 `include=["file_search_call.results"]`；有 `previous_response_id` 時帶入。
- 既有測試斷言的 `model`/`input`/`tools` 不變 → 綠燈保留；新增 `include`/`previous_response_id` 行為以新測試覆蓋。

**`suggest_followups(client, question, answer, *, model)` → {questions: [str]}**（新檔/新函式）
- 用 Responses API structured outputs（`text.format` = `json_schema`, strict）回傳 3 題追問。
- **與主問答分離**，避免 json_schema 干擾 file_search 的 citation annotations。

**`GET /api/source/{filename}`** — 安全讀取 `data/` 內檔案全文（防目錄穿越），offcanvas 內展開用。

### 3.2 多引用呈現（web 調研主流作法）

- 正文：輕量數字標記 ¹²³（與正文色區隔、酒紅）。
- 答案下方：去重彙整「來源 N 則」清單（檔名 + 相關度分數），多則收合「+N 更多」。
- 點正文標記 / 來源列 → 開左側 offcanvas，捲到並高亮對應 evidence card。

## 4. 前端設計（方向 A · Statute）

- **色系（≤3）**：墨黑 `#1a1714` · 紙白 `#f5f2ea` · 酒紅 `#7a2e2e`。
- **字體**：Fraunces（display 襯線）+ Spectral（body 襯線）+ Public Sans（UI/label）。Google Fonts CDN。
- **Icon**：Lucide line icon set（CDN）。**全程禁 emoji**。
- **Tailwind**：CDN，免 build。

### 4.1 引證依據 = 左側 offcanvas（hamburger 觸發）
- 觸發鈕：頂列**最左側 hamburger（☰）**，與抽屜同側；紅點顯示引證數。
- 點 ☰ 或答案下方來源列 → 抽屜自左滑入（桌機 330px / 手機 84%），覆半透明遮罩 + 內容輕微 dim。
- 點來源 → 對應 evidence card 高亮（酒紅 active 框）並捲到可視區。
- evidence card「開啟原文全文」→ **抽屜內就地展開**該條全文（snippet 高亮），不離開頁面。
- 關閉：再點 hamburger / 抽屜 X / 遮罩 / Esc。

### 4.2 其他元件
- 訊息氣泡（多輪）、loading 狀態、錯誤狀態。
- suggestion chips：答案下方「你可能想追問」；空狀態用預設 starter chips。
- 持久 disclaimer：輸入區下方常駐「本工具協助研究，非法律意見，請核對條文原文」（NNgroup：平實語言、近輸入框、非 footer）。
- RWD：桌機 / 平板 / 手機；平板與手機引證收合為 offcanvas。

## 5. 語料（缺口 3 配套）
- `data/` 放精選法遵條文（個資法、洗錢防制法等），**條號入檔名**（如 `個人資料保護法-第12條.txt`），每檔乾淨分界。
- structure-awareness 靠**上傳前前處理**（逐條切檔），非 chunking 旋鈕。
- chunking 兩旋鈕：`max_chunk_size_tokens`（100–4096）、`chunk_overlap_tokens`（≤ max/2），由缺口 3 用 context7 研究後決定。

## 6. 錯誤處理與監控（缺口 6）
- 缺 `OPENAI_API_KEY` / `VECTOR_STORE_ID`：啟動即明確報錯。
- vector store 空 / 無命中：友善訊息，不假裝有答案。
- OpenAI 逾時 / 例外：前端錯誤狀態 + Sentry 捕捉。

## 7. 測試
- **單元（缺口 1/2）**：既有 3 測試保綠；新增 evidence / response_id / previous_response_id / suggest_followups / source 端點測試（TDD）。
- **E2E（缺口 5）**：playwright — 問一題 → 看到答案 + 數字標記 + 來源列；開 offcanvas → 看到 evidence；展開原文。

## 8. 部署（硬約束）
- GitHub 連結式 CICD：push → Railway 自動 build。**不用 `railway up`**。
- Secrets 走 Railway variables：`OPENAI_API_KEY`、`VECTOR_STORE_ID`、`OPENAI_MODEL`。**不進 repo**。

## 9. 成功標準（可驗證）
1. `pytest -v` 全綠（含既有 + 新增測試）。
2. 本地 / 線上問一題 → 得到答案 + 至少一條引用 + 可展開的引證 snippet。
3. 追問可帶 `previous_response_id` 延續上下文。
4. 前端在三尺寸皆可用，色系 ≤3、無 emoji、左側 offcanvas 行為如 §4.1。
5. 部署於 Railway 且 Sentry 能收到刻意觸發的錯誤。

## 10. 主要取捨
- **擴充而非重寫**缺口 1/2：保住既有 TDD 綠燈。
- **suggest_followups 與主問答分離**：保住 file_search citation annotations。
- **原文抽屜內展開**而非新分頁：省一個路由 + anchor，貼合 3 小時範圍。
