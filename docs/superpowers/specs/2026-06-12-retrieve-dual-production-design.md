# Spec — 把 `retrieve_dual` 接進生產（判決檢索分流）

> 建立：2026-06-12｜分支：`feat/legal-rag-gaps`｜對應 HANDOFF 步驟 1。
> 前置脈絡：`docs/superpowers/specs/2026-06-09-legal-rag-7fixes-design.md`、HANDOFF.md。

## 1. 背景與問題

目前生產問答（`answer_question` / `astream_answer`）走**單路 `file_search` tool**，對整個 vector store（`vs_6a27e1…`，2301 檔）做一次檢索。判決語意密度天生輸法條（長論述 vs 單條法規，實測差 ~12%），單路 top-20 會被高分法條擠光判決名額（實測一般題判決 0 筆）。結果：

- 想引用判決實務見解時引不到（判決被擠出 context）。
- 反之判決混進法條/FAQ 答案時會稀釋品質。

`app/rag.py` 已有 `retrieve_dual()`（兩路 `vector_stores.search`，已測試、3 綠），但**尚未接進生產**。本 spec 的範圍就是把它接上。

### 已確認的事實（2026-06-12 實查）

- vector store `vs_6a27e1fb471c8191aad6e549fb7f2492`：法條 1163 / 函釋 666 / 施行細則 197 / FAQ 169 / **判決 106**，無 doc_type 的 0 筆。
- 106 筆判決**全部正確標記** `doc_type=判決`（含 court/category/ref_no/effective_date），chunk 為判決專屬 2048/512。
- 判決 attributes **沒有 `url` 鍵**；判決原始檔不在 `data/`（gitignore），故 `/api/source` 對判決會 404（前端已優雅降級）。
- context7 確認 `vector_stores.search(vsid, query, filters, max_num_results 1–50, ranking_options={score_threshold}, rewrite_query)`，回 `SyncPage`，`.data` 每筆有 `score / filename / attributes / content[]（{type,text}）/ file_id`。

## 2. 目標與非目標

### 目標
- 生產問答改走 `retrieve_dual`：法條路（`doc_type≠判決`）+ 判決路（`doc_type=判決`）兩路獨立名額/門檻。
- citations / evidence 改為**由檢索結果確定性組出**，不再解析 model 的 `file_search_call.results` / annotations。
- 維持 `parse_response` 對外輸出契約（key 不變），使 `app/main.py`、前端、DB log **免大改**。
- 全程 TDD、完整回歸、code review、playwright e2e 把關。

### 非目標（明確排除）
- **不刪除**庫內 106 筆判決（已達共識：分流靠 metadata filter，刪檔會餓死判決路）。
- **不**新建第二個 vector store（維持單庫）。
- **不**做爬蟲擴充上傳（那是子專案 B，待使用者提供 cookie/資料）。
- **不**改 `VECTOR_STORE_ID` / Railway 變數。

## 3. 決定的參數（本次定案）

| 參數 | 值 | 理由 |
|---|---|---|
| `LAW_TOP_K` | 12 | 法規主力；法條檔極小（多 <300 token），12 段約 ~3.6k token，便宜 |
| `JUDGMENT_TOP_M` | 3 | 判決硬上限；2048 大 chunk，>3 多為重複+噪音（HANDOFF 實測） |
| `DUAL_LAW_THRESHOLD` | 0.5 | 法規路降噪 |
| `DUAL_JUDGMENT_THRESHOLD` | 0.35 | 判決弱勢題 score 低至 0.35–0.62，與法條路解耦 |
| 合計 context | 15 段（典型 ~7–8k token） | **比現況單路預設 20 段更省**，成本/延遲持平或更低 |

## 4. 架構（資料流）

```
問題 q
 └→ retrieve_dual(client, q, vector_store_id)        # 兩路 vector_stores.search（已存在）
      ├ law:      filter doc_type≠判決, max=12, thr=0.5
      └ judgment: filter doc_type=判決,  max=3,  thr=0.35
 └→ build_context_block(law, judgment)               # 新增：自組來源區塊
      【法規依據】 [來源1] {filename} … {chunk text}
      【判決見解】 [來源N] {filename} … {chunk text}
 └→ responses.create(
        input = q + "\n\n" + context_block,           # 檢索內容放 input（per-query）
        instructions = ANSWER_INSTRUCTIONS + 引用規約, # 行為規則（可快取，穩定）
        **_MODEL_PARAMS )                              # ⚠️ 不帶 file_search tool
 └→ parse_dual_response(model_response, sources)       # 新增：citations/evidence 來自 sources
      answer    = 模型文字（[來源N] → [^N] 腳註）
      evidence  = sources（已含 filename/text/score/url?），依被引用與否決定 mode
      契約 key 與 parse_response 完全一致
```

### 串流（`astream_answer`）
先 `await` 兩路檢索 → 組 context → 才開始串流生成；最後 `final` 事件帶的 citations/evidence 來自**檢索結果**（不靠解析輸出）。串流期間只 yield `output_text.delta`（沿用現有邏輯）。

### 多輪
`previous_response_id` 保留；context 每輪重新檢索注入。

## 5. 元件與介面

| 元件 | 變更 | 介面契約 |
|---|---|---|
| `retrieve_dual()` | 既有，不改 | 回 `{"law":[...], "judgment":[...]}` |
| `build_context_block(law, judgment)` | **新增** | 入：兩路 result list；出：`(context_str, sources)`，`sources` 為依序號排列的 `[{filename,text,score,url?,doc_type}]` |
| `parse_dual_response(response, sources)` | **新增** | 出：`{answer,citations,evidence,evidence_mode,response_id,usage,status,truncated,incomplete_reason}`（與 `parse_response` 同契約） |
| `_build_answer_kwargs` | 改：拿掉 file_search tool，改注入 context | 內部用 |
| `answer_question` | 改：先 `retrieve_dual` → 組 context → `responses.create` → `parse_dual_response` | 簽名不變 |
| `stream_answer` / `astream_answer` | 同上，串流版 | 簽名不變 |
| `app/main.py` | **免改**（契約穩定） | — |
| `static/index.html` | **免改**（evidence 結構不變；判決無 url 走優雅降級） | 選配：判決由 ref_no 構造司法院 url |

### 引用規約（instructions 追加）
指示模型：「依據注入的【來源N】標記，於對應論述後標 `[來源N]`」。`parse_dual_response` 把 `[來源N]` linkify 成 `[^N]` 腳註，編號對齊 `sources` 序。模型未標引用時，evidence 仍以 `dual_retrieved` 模式顯示全部檢索段（比照現行 retrieved fallback）。

### evidence_mode 值
- `dual_cited`：模型有標 `[來源N]` → evidence 為被引用的來源子集。
- `dual_retrieved`：模型未標 → evidence 顯示全部檢索段（去重、依 score）。
（前端僅作資訊顯示，相容現有 `retrieved`/`cited` 渲染。）

## 6. 測試策略

### TDD（紅 → 綠）
- `tests/test_rag.py`：`answer_question` / 串流相關測試的 mock **從 `responses.create`(file_search) 改為涵蓋 `vector_stores.search` + `responses.create`(無 tool)**。
- 新增測試：
  - `build_context_block` 正確分區、序號、保留換行、空判決路不報錯。
  - `parse_dual_response`：`[來源N]` → `[^N]`、citations/evidence 來自 sources、cited vs retrieved 模式、截斷/usage 透傳。
  - `answer_question` 端到端（mock client）：兩路檢索被呼叫、無 file_search tool、輸出契約 key 齊全。
- 既有 `retrieve_dual` 3 測試保持綠。

### 回歸
- 全套 `pytest -q`（目前 98 綠）必須維持全綠。
- `ruff check app/ scripts/` 全過。

### e2e（playwright）
- 對 `scripts/mock_server.py` 跑 `npx playwright test`：問答 → 串流 → 證據面板 → 腳註點擊。
- `mock_server.py` 的假資料需補判決路樣本，驗證【判決見解】區渲染。

### 平行 agent 切片（實作階段）
1. 後端 `rag.py` 重構 + 單元測試（核心，主序）。
2. `mock_server.py` 判決樣本 + playwright e2e。
3. 前端腳註/證據相容性驗證（多半免改，僅驗證）。
4. 回歸 + ruff + 文件更新。
切片 2/3/4 與 1 有介面相依，採 incremental，1 的契約先定。

### code review
完成後跑 requesting-code-review → receiving-code-review 把關（重點：契約相容、串流不阻塞、引用對映正確、無回歸）。

## 7. 風險與回滾

| 風險 | 緩解 |
|---|---|
| 高風險重構動 parse/串流/前端 | 嚴格 TDD + 全回歸；契約 key 不變鎖住 main.py/前端 |
| 串流前先檢索 → 首 token 延遲略增 | 檢索為兩個輕量 search；可接受，且總 context 更小抵銷 |
| 模型不照 `[來源N]` 格式引用 | `dual_retrieved` fallback 保證 evidence 不空 |
| 判決無 url → 原文連結缺 | 前端已優雅降級顯示 snippet；選配後續補司法院 url |
| 線上行為改變 | 保留舊 `parse_response`/`_build_answer_kwargs`（或以 flag 切換），必要時快速回滾 |

## 8. 完成定義（可驗證成功條件）

1. `pytest -q` 全綠（含新測試），`ruff check app/ scripts/` 全過。
2. 一般法遵題（如「資遣費怎麼算」）的回答 evidence **同時含法條與判決**（判決路不再 0 筆）。
3. 純法條題判決路回空時**不報錯**、答案正常。
4. 串流逐字、`final` 帶正確 citations/evidence；前端腳註可點、證據面板正常。
5. `playwright test` 對 mock_server 全綠。
6. 通過 code review，無未解 Critical/Important。

## 9. 子專案 B（後續，本 spec 不含）

爬蟲擴充上傳 ~2,800+ 判決（`build_judgment_corpus.py` / `ingest_judgments.py` 已存在）。**前置依賴：本 spec（A）完成**，且使用者提供 opendata cookie / 資料。屆時另開 plan。
