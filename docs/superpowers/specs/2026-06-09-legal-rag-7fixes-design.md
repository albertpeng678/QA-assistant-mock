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

- `parse_response(response)` → `{answer, citations, evidence, evidence_mode, response_id, usage, status, truncated, incomplete_reason}`（後三鍵為 Phase C 新增，見 §J）
  - **evidence（缺口④）**：先以 message `annotations`（`type=file_citation`）的 `file_id`/`filename` 過濾出「真正被引用」的結果（`evidence_mode="cited"`）；annotations 為空時（gpt-5.4-mini 實測常空）退回「依 score 取 top-N」（`evidence_mode="retrieved"`，`EVIDENCE_DISPLAY_LIMIT=5`）。一律 `_dedupe_evidence` 同檔名去重保留最高分、保序。
  - **evidence dict**：`{filename, text, score}`，**有 url 才加** `url`（向後相容既有測試的精確比對）。
  - **citations**：cited 模式＝annotation 檔名（保序去重）；retrieved 模式＝實際顯示 evidence 的檔名（與顯示一致，不再傾倒全部）。
  - `_strip_citation_markers`：剝除 gpt-5.4-mini 的 inline 引用標記（PUA U+E200–E20F + `turnXfileY`）。
  - 輔助：`_result_url(r)`（取 `result.attributes.url`，缺口⑥）、`_dedupe_evidence(entries)`。
- `ANSWER_INSTRUCTIONS`（缺口②⑦）：一句結論 → GFM 表格為主（智慧判斷，欄位「項目／法規依據／重點說明／注意事項」）→ 補充段落；確定性分層標籤 **【明文】【解釋/裁量】【實務見解】【語料未涵蓋】**；複數解釋列甲說/乙說；明確區分「**法律確實未明文** vs **本系統語料未收錄**」；grounded 引用、不杜撰、結尾「非正式法律意見」。
- `_MODEL_PARAMS`：`reasoning={"effort":"low"}`、`text={"verbosity":"medium"}`、`max_output_tokens=4096`（gpt-5.4-mini 推理模型，不可帶 temperature/top_p；Phase C 由 2048 調高——此上限含推理 token，太小會截斷）。
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
4. **gpt-5.4-mini annotations 常空** → 缺口④實務上多走「top-5 by score」；**Phase C 後**改以答案內 inline 引用標記（`turnNfileM`）轉腳註並建 cited evidence 為第一優先（見 §J），annotation/retrieved 為後備。
5. **🔐 金鑰**：`.env`/對話中的 `sk-svcacct-…`、`RAILWAY_API_TOKEN` 已外洩，需撤換。

---

# J. 上線後生產修補（Phase C，2026-06-10；commit `f9c72e3`、`fab1842`，皆於 main）

> 上線後使用者回報兩類生產問題，各以 systematic-debugging（真實 API 診斷定根因）+ context7（主流解法）+ TDD + request/receive code review 處理，並經真實 API 與 production 雙重驗證。

## J-① 答案截斷且無來源/追問（commit `f9c72e3`）

- **症狀**：問答常答到一半截斷，且截斷時無參考來源、無追問建議（三症狀同源）。
- **根因（真實 API 實證）**：gpt-5.4-mini 為推理模型，`max_output_tokens` 同時涵蓋「可見輸出＋推理 token」；撞上限時 Responses API 以 `status="incomplete"` 結束，**串流終結事件是 `response.incomplete`（非 `response.completed`）**。原 `stream_answer`/`astream_answer` 只認 `response.completed` 才擷取 final → 截斷時永不發 `final` → 前端 `finalizeAnswer` 不執行（答案卡截斷、無 citations/evidence/followups）。
- **解法（主流：終結事件全收 + 誠實標示）**：
  - `rag.py`：`_TERMINAL_EVENT_TYPES=(completed, incomplete, failed)` + `_terminal_response(event, type)`（精確比對），兩個 stream 函式皆用之 → 截斷/失敗也發 `final`。`parse_response` 新增 `status`/`truncated`(=status=="incomplete")/`incomplete_reason`。`max_output_tokens` 2048→4096。`suggest_followups` 防護壞/缺 `output_text` → `{"questions": []}`（追問是輔助功能，不可拋例外）。
  - `main.py`：`/api/suggest` try/except 優雅降級為 `{"questions": []}` + Sentry（比照 log_query 非阻斷鐵則）；截斷時 `logger.warning` 記 `status/incomplete_reason`（telemetry）。
  - `index.html`：串流結束未收 `final` 時降級 finalize（保住已串流文字＋追問）；`evt.truncated||partial` 顯示誠實提示；`failed`/空輸出顯示錯誤、不留空白泡泡；收尾 flush `buf` 殘段；JSON.parse 失敗 `console.warn`。
- **驗證**：pytest +9、e2e +2（`__NOFINAL__`/`__FAILEMPTY__` mock 鉤子）；真實 API 強制小 budget 確認真送 `response.incomplete` 且 final 帶 `truncated=True/reason=max_output_tokens`；production 串流 final + 5 citations + DB log。

## J-③ 答案表格「依據」欄整欄空白（commit `fab1842`）

- **症狀**：表格有「依據」欄，但每列依據格空白。
- **根因（真實 API 實證）**：gpt-5.4-mini 把來源以 **inline 引用標記**（PUA `U+E200 filecite U+E202 turnNfileM U+E201`，及純文字 `fileciteturnNfileM`）塞進答案文字，annotations 為空。`M` 為 `file_search_call.results` 索引（對映已驗證正確）。原 `_strip_citation_markers` 把標記**整段刪除且不回收**；當模型把依據欄純用標記表達時整欄變空（raw 含 72 PUA + 24 token 全在依據欄）。
- **解法（主流：render-don't-delete，轉腳註）**：
  - `rag.py`：新增 `_linkify_citation_markers(text, raw_results)`——單一交替 regex（PUA span 在前、純文字 token 在後）一次左到右掃描，把標記轉成腳註 `[^k]`（依閱讀順序編號、同檔同號、超範圍 `M` 安全丟棄不誤標）；殘餘 PUA 清理。新增 `_evidence_for_files(filenames, all_results)` 依引用序取每檔最高分 chunk。`parse_response` 新增「inline 標記」為**第一優先**路徑（marker_files → cited evidence + 腳註），annotation/retrieved 為後備；腳註 `[^k]` 與 evidence 第 k-1 筆對齊（前端 `cite-ref` 點擊開該來源）。
  - `index.html`：`finalizeAnswer` 最終以後端權威 `evt.answer`（含腳註）渲染，非串流累積的 raw delta（後者只經前端 `stripCite` 刪標記、不會回收）。
- **驗證**：pytest +5（標記轉腳註、腳註↔evidence 對映、同檔同號、閱讀順序、超範圍丟棄）、e2e +1（`__MARKERS__` 鉤子：依據欄腳註可點、無殘留標記）；真實 API 確認依據欄 **0/10 空白列**、PUA 殘留 0、腳註 28、`evidence_mode=cited`；production 同樣 0 空白列。

## Phase C 驗證總結

- **pytest 95 綠**（Phase B 81 → +14）、**Playwright e2e 11 桌機 + 1 行動**、**ruff(app/) 全過無警告**。
- code review 兩輪（request→receive）：J-① 補修 `failed`/空輸出 UX；J-③ 採納單一 pass regex 改善腳註顯示順序。
- 兩 commit 皆 push main、Railway 自動部署 SUCCESS、production 煙霧測試通過。

# K. 判決語料擴充 + 檢索分流抗稀釋（Phase D，2026-06-11；進行中）

> 目標：補齊 `doc_type=判決` 語料層（[[legal-corpus-research]] 的素材分層最深層），並解決判決在檢索時的「結構性弱勢」。詳見 memory `judgment-corpus-pipeline`。

## K-① 判決語料取得 pipeline（已驗證可行）
- **取得**：`opendata.judicial.gov.tw` 會員月度 RAR 批次下載（非 API；用瀏覽器登入 cookie，Python requests 即可，opendata 站未擋）。下載端點 `api/FilesetLists/{fid}/file`，**fid = datasetId + 12310**，`202603=fid 65335`，每月 −1、跨年連續（已驗證至 202304）。授權：政府資料開放授權條款第1版。
- **規模**：每月約 10.5 萬筆 JSON（欄位 `JID/JYEAR/JCASE/JNO/JDATE/JTITLE/JFULL/JPDF`，**無結構化法條欄位**）。
- **篩選分類（坑）**：純關鍵字 recall 高但 precision 低——洗錢防制法 9420/月多為刑事附帶沒收（假陽性），故**排除洗錢**。精篩 `--strict`：母法在 JFULL 出現 ≥3 次或在案由 → 1971→535/月（公司法假陽性 -80%）。
- **chunk 分流**：判決長文走 `max=2048/overlap=512`，法條維持 `512/128`（OpenAI batch API 原生 per-file `chunking_strategy`，依 doc_type 分流）。
- **腳本**：`scripts/ingest_judgments.py`（前處理＋上傳，`--strict`/`--exclude-laws`）、`scripts/build_judgment_corpus.py`（多月 orchestrator）。

## K-② 抗稀釋第一層：score_threshold（已落地）
- `app/rag.py` `FILE_SEARCH_RANKING_OPTIONS = {"score_threshold": 0.5}`，掛上 file_search tool。
- **刻意不拉高 `max_num_results`**（判決 chunk 2048 token，拉高＝成本＋延遲硬傷）。
- 法條 score 天然高於判決（短檔精準，hybrid 下 0.82 vs 0.73），混排時法條自動優先。TDD 鎖定（測試 `test_answer_question_calls_file_search_with_vector_store`）。

## K-③ 判決結構性弱勢 + 檢索分流（方向1，retrieve_dual 核心已落地）
- **問題（實證）**：判決語意密度天生輸法條（長篇論述 vs 單條法規，embedding 實測差 ~12%）。去 boilerplate 僅提升 2.5%（14/18 篇），**純前處理追不平**。單路 file_search top-20 對一般法遵題實測**判決 0 筆**（被高分法條擠光）；`score_threshold` 一旦上升，判決整批被誤殺。
- **解法**：`retrieve_dual()`（`app/rag.py`）走兩路獨立 `vector_stores.search`——
  - 法條路：`doc_type≠判決`，`max_num_results=12`、`threshold=0.5`（主力）。
  - 判決路：`doc_type=判決`，`max_num_results=3`、獨立 `threshold=0.35`（保障名額＋少而精）。
- **參數依據（context7 + 實測）**：判決路上限 **3**——實測判決路 score 高且平坦（一般判決導向題 0.64–0.87），>3 多為重複資訊；context7 明示「多餘低相關 context 反降答案品質、燒 token」。
- **判決路 threshold 為何 0.35（非 0.5）**：判決 score **因題而異**——判決導向題達 0.64–0.87，但**弱勢題**（如資遣費，法條主導）判決僅 0.35–0.62。若沿用法條的 0.5 會砍掉這些「正要救」的弱勢題判決，故判決路門檻與法條路**解耦**、設 0.35。判決用獨立門檻 → 對「法條池門檻上升」免疫。
- **PoC 驗證**：一般題「資遣費」現狀單路判決 0 筆 → 分流後判決 6 筆進場（score 0.35–0.62），模型答案同時含【法規】與【判決見解】（雇主終止效力之實務認定）。
- **TDD**：`test_retrieve_dual_*`（2 案，pytest 97 綠）。

## K-④ 待辦（下個 session）
1. **生產串接**：`answer_question`/`astream_answer` 改走 `retrieve_dual` 自組 context（取代 file_search tool 自動檢索）；連帶調整 `parse_response`（citations/evidence 改自分流結果）、SSE 串流、前端 `.legal-pre` 渲染。**屬架構級重構，需完整測試**。
2. **語料上傳**：近 2–3 年分層抽樣（每月 ~535 strict 判決，目標 1–1.5 萬筆）；上傳前可選做去 boilerplate 提升判決路 score。
3. **目前狀態**：12 月語料寫檔已備（`data/judgments/` ~2,800 筆原料），**上傳已暫停**待生產整合定案；store 現僅含早期 PoC 106 筆判決。
