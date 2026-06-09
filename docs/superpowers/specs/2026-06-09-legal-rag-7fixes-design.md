# 法遵 RAG 七項修正 — 設計 spec

日期：2026-06-09　｜　模型：gpt-5.4-mini　｜　引擎：OpenAI Responses API + file_search（託管，**不自建 RAG**）

本 spec 由 16 個平行研究 agent（context7 + WebSearch）調研後綜整，並經使用者拍板決策。

## 決策摘要（使用者拍板）

- **②表格化**：智慧判斷（hybrid）。可結構化內容才用表格；單一結論/推理論證/解釋空間型答案用段落。
- **⑦資料空白**：實作 (a) IRAC 推理 + 校準不確定 + 誠實兜底（prompt 層）、(b) 補語料：施行細則/命令檔、(c) 補語料：函釋/判決/FAQ。（**不**做 query-rewriting 那一項。）
- **重建索引**：程式由我改＋本地驗證；實際重跑 `ingest.py`（新建 vector store、計費、更新 Railway `VECTOR_STORE_ID`）改用本機 `.env` 金鑰 + railway CLI，於建立 vector store 前再確認。

## 缺口診斷與採納解法

| # | 需求 | 根因 | 採納解法 | 需重建索引 |
|---|---|---|---|---|
| ① | SSE 非逐字 | `async def event_gen()` 內同步迭代同步 OpenAI `Stream` → 阻塞 event loop，token 批次沖出 | rag.py 新增 `astream_answer`（`AsyncOpenAI` + `async for`）；main.py 用 `AsyncOpenAI`，DB log 包 `run_in_threadpool` | 否 |
| ② | 表格為主 | prompt 只要求條列 | 改寫 `ANSWER_INSTRUCTIONS`：一句結論→主表格（欄位：項目／法規依據／重點說明／注意事項）→補充段落；GFM 規則；智慧判斷何時用表格 | 否 |
| ③ | 表格平滑渲染 | 串流半成品表格先顯示 raw 符號；且 sanitize 順序 | 前端「緩衝未閉合區塊」（未見 `\|---\|` 的表格、未閉合 code fence 暫緩，尾段純文字暫顯）＋ `requestAnimationFrame` debounce ＋ **先 marked 再 DOMPurify** | 否 |
| ④ | 只顯示被引用段落 | evidence = 全部檢索結果 | `parse_response` 用 annotations(`file_citation`) 的 `file_id`/`filename` 過濾 evidence；一檔多 chunk 取 score 最高；annotations 空時 fallback top-k by score 並標「檢索參考」 | 否 |
| ⑤ | chunk 結構消失 | 前端 `escapeHtml` 無 `white-space:pre-wrap` → 換行被吸收；且 `fetch_corpus._text()` 攤平款/目 | 前端證據/原文區 `white-space:pre-wrap`（立即）；`fetch_corpus._text()` 區塊間補 `\n`、款/目正規化（重建索引） | 部分 |
| ⑥ | 「開啟原文」重複內容無連結 | url 已在 vector store attributes，但 `parse_response` 未讀、前端只重 fetch 本地檔 | `parse_response` 讀 `result.attributes.url`；前端按鈕改 `<a href=url target=_blank>` | 否（既有索引若無 url 則需重建） |
| ⑦ | 法規留解釋空間/資料空白 | 法律 open texture（刻意）＋語料只有法條 | prompt 走 IRAC + 區分「明文/解釋/實務/語料未涵蓋」+ 校準不確定 + 誠實兜底；補語料：命令檔→函釋/FAQ/判決；attributes 擴充 authority_level/issuer/ref_no/effective_date | 是（補語料） |

## 實作分期

- **Phase A（免重建索引，先交付）**：① ② ③ ④ ⑥ + ⑤前端層 + ⑦prompt 層。全程 TDD（pytest 後端、Playwright 前端）。
- **Phase B（需重跑 ingest）**：⑤深層結構（fetch_corpus）+ ⑦補語料（命令檔/函釋/判決 + attributes schema）→ 重建 vector store → 更新 Railway。

## 驗證

- 後端：`pytest -v`（既有測試不得退步 + 每項新增 TDD 測試）。
- 前端：更新 `scripts/mock_server.py` 反映新格式；補 `tests/e2e/*.spec.ts`；用 Playwright MCP 實機排查（桌機 + 行動裝置）。
- ①串流：Playwright 量測 network frame 時間戳，確認逐字到達而非批次。

## 硬約束（沿用 CLAUDE.md）

不自建 RAG；secrets 走環境變數（`.env`/Railway，不進 repo）；部署走 GitHub 連結式 CICD；file_search chunking 僅 `max_chunk_size_tokens`(100–4096)/`chunk_overlap_tokens`(≤max/2)，結構感知靠上傳前前處理。

---

# 最終實作落地（2026-06-10 實況；commit 101ad7d Phase A、f4c00cf Phase B，皆於 main）

> 以下為「實際交付」內容，含程式介面、attributes schema、語料盤點、驗證結果與未竟事項。與上方設計表互補。

## A. 後端 `app/rag.py`

- `parse_response(response)` → `{answer, citations, evidence, evidence_mode, response_id, usage}`
  - **evidence（缺口④）**：先以 message `annotations`（`type=file_citation`）的 `file_id`/`filename` 過濾出「真正被引用」的結果（`evidence_mode="cited"`）；annotations 為空時（gpt-5.4-mini 實測常空）退回「依 score 取 top-N」（`evidence_mode="retrieved"`，`EVIDENCE_DISPLAY_LIMIT=5`）。一律 `_dedupe_evidence` 同檔名去重保留最高分、保序。
  - **evidence dict**：`{filename, text, score}`，**有 url 才加** `url`（向後相容既有測試的精確比對）。
  - **citations**：cited 模式＝annotation 檔名（保序去重）；retrieved 模式＝實際顯示 evidence 的檔名（與顯示一致，不再傾倒全部）。
  - `_strip_citation_markers`：剝除 gpt-5.4-mini 的 inline 引用標記（PUA U+E200–E20F + `turnXfileY`）。
  - 輔助：`_result_url(r)`（取 `result.attributes.url`，缺口⑥）、`_dedupe_evidence(entries)`。
- `ANSWER_INSTRUCTIONS`（缺口②⑦）：一句結論 → GFM 表格為主（智慧判斷，欄位「項目／法規依據／重點說明／注意事項」）→ 補充段落；確定性分層標籤 **【明文】【解釋/裁量】【實務見解】【語料未涵蓋】**；複數解釋列甲說/乙說；明確區分「**法律確實未明文** vs **本系統語料未收錄**」；grounded 引用、不杜撰、結尾「非正式法律意見」。
- `_MODEL_PARAMS`：`reasoning={"effort":"low"}`、`text={"verbosity":"medium"}`、`max_output_tokens=2048`（gpt-5.4-mini 推理模型，不可帶 temperature/top_p）。
- `_build_answer_kwargs(...)`：answer/stream 共用。
- `answer_question(...)`：同步（非串流，/api/ask 用）。
- `stream_answer(...)`：**同步** generator（保留、有測試）。
- `astream_answer(...)`：**新增**，async generator（`AsyncOpenAI` + `await create(stream=True)` + `async for`）——**缺口①根因修法**：同步 Stream 在 async generator 內迭代會阻塞 event loop、token 批次沖出。
- `suggest_followups(...)`：structured outputs 產 3 題追問。

## B. 後端 `app/main.py`

- `/api/ask/stream`：改用 `AsyncOpenAI` + `astream_answer`；非阻斷 DB log 以 `run_in_threadpool(_safe_log_query)` 卸到執行緒池（避免再次阻塞串流）。新 import：`AsyncOpenAI`、`astream_answer`、`starlette.concurrency.run_in_threadpool`。
- `/api/ask`、`/api/suggest`、`/api/source/{filename}`（含目錄穿越防護）、`/api/feedback`：行為不變。

## C. 前端 `static/index.html`

- **串流平滑渲染（缺口③）**：`splitUnstableTail(text)` 把尾端「未成形表格（尚未出現 `|---|` 分隔列）/未閉合 code fence」切出暫緩；`renderStream(el, full)` 穩定段交 `md()`、未成形尾段以 `.md-pending` 淡墨純文字暫顯；delta loop 以 `requestAnimationFrame` 節流，`final` 取消未完成 frame 後整段 `md()`。
- `md(t) = tierTags(footnotes(DOMPurify.sanitize(marked.parse(stripCite(t),{breaks,gfm}))))`。
- **確定性 chip（缺口⑦）**：`tierTags` 把【明文】【解釋/裁量】【實務見解】【語料未涵蓋】包成 `.tier` chip（嚴守墨/紙/血三色，靠濃淡與血底區分強調）。
- **結構保留（缺口⑤）**：證據卡/原文區套 `.legal-pre`（`white-space:pre-wrap`）→ 法條項/款換行可見。
- **引證顯示（缺口④⑥）**：`renderEvidence` 依 `state.lastEvidenceMode` 標頭顯示「答案引用來源」(cited) / 「檢索參考（未必逐句引用）」(retrieved)；每張卡有 url 就渲染 `<a class="open-link" target="_blank" rel="noopener noreferrer">開啟官方原文</a>`，無 url 才退回 `.open-full` 展開本地全文。
- 新 CSS：`table/th/td`、`.md-pending`、`.legal-pre`、`.tier/.tier-explicit/.tier-interpret/.tier-practice/.tier-gap`。

## D. 取材與索引 `scripts/`

### `fetch_corpus.py`
- 法律檔：`GetFile.ashx?DType=RAW_XML&AuData=CF`；**命令檔（施行細則）：`AuData=CM`**（本次探索確認，data.gov.tw/dataset/18290，政府開放授權可商用）。
- `parse_laws` / `normalize_article_number`（中文數字→阿拉伯）/ `build_law_index`（{法規:pcode}）/ `_decode_bytes`（utf-8→big5 fallback）。

### `ingest.py`
- `derive_attributes(filename, law_index=None, first_line=None)`：
  - **法條** `{法規}-第N條.txt` → `law/article/category/doc_type=法條/source=全國法規資料庫`（+url）。
  - **施行細則**（law 以「施行細則」結尾）→ `doc_type=施行細則`、category 沿用母法。
  - **非條文** `_NONARTICLE_RE = ^(prefix)-(函釋|FAQ|判決|裁罰)-(title)$` → doc_type 由檔名定；`_parse_first_line` 解析首行管線 metadata 取 `母法/字號/發文日|發布日|裁判日/對應條號/來源`，組出 `law/article/category(由母法)/source/authority_level(+ref_no/+effective_date/+url)`。
  - **url 安全**：僅阿拉伯數字條號（`re.fullmatch(r"[\d-]+", flno)`）才組 url，避免中文數字條號產生指向錯誤的引用連結（稽核 P2）。
- `is_empty_article(content)`：正文 strip 後 ∈ {「（刪除）」「(刪除)」「（保留）」「(保留)」「」} → ingest 略過（稽核 P0；精確比對，不誤殺含字樣的有效條文如證交法第155條）。
- `LAW_CATEGORY` 補：`商業登記法→公司治理`、`多層次傳銷管理法→公平交易`（稽核 P1，消除 category=其他）。
- chunking：`MAX_CHUNK_SIZE_TOKENS=512`、`CHUNK_OVERLAP_TOKENS=128`。
- **append 模式（`--vector-store-id` / env `INGEST_VECTOR_STORE_ID`）**：沿用既有 vector store、不新建；逐檔讀文字→略過空殼→傳首行；append 模式略過 `doc_type=法條`（既有 store 已含），只上傳新語料。

## E. Per-file attributes schema（≤16 鍵，值皆 str）

| key | 法條 | 施行細則 | 函釋 | FAQ | 說明 |
|---|---|---|---|---|---|
| `law` | ✓ | ✓ | ✓ | ✓ | 母法全名（非條文取首行「母法」） |
| `article` | ✓ | ✓ | △ | △ | 第N條；非條文取首行「對應條號」（無則空） |
| `category` | ✓ | ✓ | ✓ | ✓ | 由 LAW_CATEGORY（母法）對應 |
| `doc_type` | 法條 | 施行細則 | 函釋 | FAQ | 亦支援 判決/裁罰 |
| `source` | 全國法規資料庫 | 全國法規資料庫 | 機關 | 機關 | 非條文取首行「來源」 |
| `url` | △ | △ | △ | △ | pcode+阿拉伯條號才有 |
| `authority_level` | — | — | ✓ | ✓ | 首行「效力」 |
| `ref_no` | — | — | △ | — | 字號（有才加） |
| `effective_date` | — | — | △ | △ | 發文/發布/裁判日（有才加） |

非條文**檔名慣例**：`{簡稱}-{doc_type}-{標題}.txt`（簡稱如 個資/洗錢防制/勞動/證券/公司/公平交易/消費者保護）。
非條文**首行 metadata**：`來源:X | 效力:X | 字號:X | 發文日:YYYY-MM-DD | 對應條號:第N條 | 母法:XXX法`（首行後空一行接全文）。

## F. 語料盤點（data/，平行 agent 抓取之真實官方開放資料）

| doc_type | 將上傳數 | 來源 / 備註 |
|---|---|---|
| 法條 | 1042 | 9 部法規逐條（AuData=CF）；另 121 純「（刪除）」空殼 ingest 略過 |
| 施行細則 | 197 | 6 部（AuData=CM）：個資33/公平37/勞基70/性平20/消保43/證交15；洗錢·公司·營業秘密**無**施行細則 |
| 函釋 | 666 | 金管會76 + 勞動部15 + 經濟部公司法552 + 個資會18 + 公平會5；已剔除 22 則母法誤標公司法（實為銀行法/金控法、越界）的金管會函釋 |
| FAQ | 169 | 個資16/洗錢25/證券36/消保30/公司26/公平交易20/勞動14/多層次傳銷2（官網 Q&A 與 PDF 問答集；個資取自國發會專區 Wayback 典藏） |
| **合計** | **2074** | data/ 另含 141 純空殼（ingest 略過）；判決 = **0** |

- `data/_law_index.json`：15 部（9 母法 + 6 施行細則的 pcode）。
- **vector store**：沿用 `vs_6a27e1fb471c8191aad6e549fb7f2492`（append 1032 新檔，3 批全 completed、failed=0；ID 不變→Railway 變數免改）。

## G. DB 稽核（release gate；判定 PASS-WITH-FIXES）

平行 agent 抓料後派專屬 DB 稽核 agent 盤查，已落實修正：
- **P0**：略過 141 純空殼；剔除 22 則金管會函釋（母法誤標公司法、含 7 個錯誤引用 URL）。
- **P1**：補 LAW_CATEGORY（消除 category=其他）；url 僅阿拉伯條號（殺掉中文數字條號的壞連結）。
- 通過：編碼全 UTF-8、attributes 全 ≤16 鍵且值皆 str、真實性抽查無杜撰、無實質重複。
- 揭露：公司法占比 ~48%（主因公司法本身條文最多，屬結構性，非偏斜；保留全部有效函釋）。

## H. 驗證結果

- **pytest 81 綠**（rag/main/api/db/ingest_metadata/fetch_corpus）。
- **Playwright E2E 9 綠**（chromium 桌機 + Pixel 5 行動；含表格平滑、官方原文連結、確定性 chip、引用標籤、跨裝置）。
- **真 OpenAI API 抽查**：①async 串流 610 deltas；②表格+IRAC 格式；④retrieved 由 20→5；⑥ url 真實；⑦正確區分開放性留白 vs 語料未收錄；上線後新語料（施行細則/函釋/FAQ）確實進檢索、補足空白。
- **Playwright MCP 實機**：逐字成長時間軸、`|---|` 原始符號全程未閃現、抽屜連結/pre-wrap/模式標籤、行動裝置版面。

## I. 未竟事項 / 已知限制

1. **判決語料 = 0**：司法院 opendata API 需「註冊帳號 + 限 00–06 時服務 + JList 無關鍵字檢索」三重限制；依鐵則不杜撰。待提供帳密（環境變數）後於服務時段補。
2. **營業秘密法函釋**：主管機關未發條文式行政函釋（公開來源僅法院裁判），故無。
3. **⑤深層結構**：款/目縮排為 cosmetic，未做（pre-wrap 已還原項/款換行）。
4. **gpt-5.4-mini annotations 常空** → 缺口④實務上多走「top-5 by score」而非精準 annotation 過濾；已用 instructions 要求引用以提高回填率，並以 `evidence_mode` 誠實標示。
5. **🔐 金鑰**：`.env`/對話中的 `sk-svcacct-…` 已外洩，需撤換。
