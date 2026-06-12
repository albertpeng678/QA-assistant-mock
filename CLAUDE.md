# CLAUDE.md — 工作坊起始 repo 導覽

> 這份檔案會被 Claude Code 自動讀取。學員也請先讀一遍，建立全貌。

## 這是什麼

這是一個**工作坊起始 repo（starter）**。它能跑起來，但問答功能還沒實作 ——
你的任務是用 AI 工具鏈把 6 個缺口補完，部署上 Railway，最後用提案模板把成果「提案」出來。

產品本身：一個**法規 RAG 問答 agent**，用 **OpenAI file search（託管服務）** 對法規語料做問答並附條文引用。

> ⚠️ **不要自建 RAG**（不要裝 LangChain/向量資料庫）。引擎就是 OpenAI file search 託管服務 —— 這是 3 小時做得完的關鍵。

## 目前實作狀態（2026-06-13，給接手的 Claude）

> 6 個起始缺口**已全部補完**並上線（production：`https://corp-law-qa-assistant.up.railway.app`）。
> 其後依序完成：七項需求修正＋語料擴充（spec `2026-06-09-legal-rag-7fixes-design.md`）→
> 答案清晰化＋資訊架構（spec `2026-06-12-answer-clarity-ia-design.md`）→
> 24 月判決全量上傳 → 召回/多輪/等待UX/manifest 四修正 → 判決全文功能 → **範疇防護 guardrail**。
> 測試基線：**pytest 155 綠、e2e 22 綠**（`tests/e2e/ask.spec.ts` 對 mock:8001）。

### 問答管線（`app/rag.py`）
- `answer_question`(同步) / `astream_answer`(async，SSE 用) → `parse_dual_response` 回 `{answer, citations, evidence, evidence_mode, response_id, usage, tier}`。
- **檢索分流**：`retrieve_dual`/`aretrieve_dual` 單庫雙路——法條路（`doc_type≠判決`，top-12、**門檻 0.35**）+ 判決路（`doc_type=判決`，top-3、門檻 0.35）。法條路原 0.5 有懸崖效應（相關文件落 0.45–0.49 被切、答案退化成 gap），勿調回。`build_context_block` 自組 context（**不帶 file_search tool**）→ Responses API。**同檔多 chunk 合併**：`_dedupe_results` 同檔取前 2 高分 chunk 併成單一 `[來源N]`（來源唯一×context 豐富度），sources 帶 `file_id`。
- **範疇防護（topical guardrail，OpenAI cookbook 模式）**：`classify_intent`/`aclassify_intent`（`app/capability.py`，structured enum）三類——`meta_capability`→`capability_result` 罐頭、`out_of_scope`（翻譯/算數/閒聊等域外）→`out_of_scope_result()` **確定性拒答模板**（不檢索不生成；拒答交 LLM 會「好心幫忙＋掛假引用」，實測踩過）、`legal_question`→正常檢索。**每輪都跑**；延續輪把 `previous_response_id` 傳給 classifier（fork 主鏈看脈絡、`store=False`；官方文件：instructions 不繼承、fork 不污染主鏈）。async 路 gate 與檢索 `asyncio.gather` 平行（≈零增時）+`return_exceptions`（meta/oos 不被檢索失敗拖死）。分類壞掉退 `legal_question` 不退化。INTENT_INSTRUCTIONS 含防誤殺邊界範例（法遵題夾外文/計算仍 legal）。
- **縱深第二層**：ANSWER_INSTRUCTIONS【服務範疇】條款 + `parse_dual_response` 後置檢查（答案含 `_OOS_MARKER` 且零引用 → 改判 `out_of_scope`、清空證據——classifier 漏判時拒答不得掛證據面板）。
- **多輪**：`previous_response_id` 串 Responses 鏈；延續輪 instructions 附 `CONTINUATION_GUIDANCE`（檢索與對話主題不符時忽略之、沿用前輪依據——「好啊請幫我生成對照表」實測正常）。capability/oos 輪 `response_id=None`，前端沿用舊鏈。
- **確定性層級**：`classify_tier`/`aclassify_tier`（structured enum：明文/解釋裁量/實務見解/語料未涵蓋）每輪後分類，**非** inline【明文】標記（prompt-only 命中率不穩）；async 與生成並行（`tier_task`，try/finally 防停止鍵孤兒）。
- **引證**：模型標 `[來源N]`（**方括號 optional**，裸寫「來源4」也轉）→ `[^N]` 腳註（`dual_cited`）；未標→顯示全部檢索段（`dual_retrieved`）。`evidence_mode ∈ {dual_cited, dual_retrieved, capability, out_of_scope}`。
- **輸出格式（答案清晰化）**：`_MODEL_PARAMS` verbosity=low；ANSWER_INSTRUCTIONS 走 BLUF 倒金字塔（首行白話結論≤40字+關鍵數字粗體）→ 2-4 白話 bullet（括號附條號）→ 表格僅 3+ 項比較（≤3 欄、條號縮寫 §157-1）→ caveat 結尾。

### SSE 與等待 UX
- `/api/ask/stream` 用 `AsyncOpenAI` + `astream_answer`（同步 Stream 放 async generator 會阻塞 event loop）；DB log 走 `run_in_threadpool`。
- **階段事件**：`{"type":"stage","stage":"retrieving"|"retrieved"(含 law_count/judgment_count 實數)|"generating"}`，meta/oos 輪零 stage。前端：階段步驟列（打勾+實數+脈動）+ 答案形骨架 → 首 token 收合；>12s 顯安撫文案+**停止鍵**（AbortController，中止後中性訊息）。NN/g：>10s 等待要顯真實步驟、非 spinner。

### 前端（`static/index.html`，單欄、無 @media、Tailwind CDN）
- 設計 token：`--ink #1a1714`/`--paper #f5f2ea`/`--blood #7a2e2e`；Fraunces/Spectral/Public Sans。
- Layer-0 tier badge 來自 `evt.tier`（明文=**朱紅 --blood**、解釋裁量=teal、實務見解=amber、未涵蓋=灰）。
- 表格包 `overflow-x-auto`（窄螢幕橫捲不爆版）；caveat 為淡底軟盒（**不可用左槓鑲邊**，使用者明確否決）。
- offcanvas 證據卡：摘要夾制 8 行（`.evi-snippet` line-clamp；同檔合併後 snippet 達數千字，不夾形同攤開）；「展開原文全文」優先以 `data-file-id` 打 `/api/source_vs/{file_id}`（**判決原文不在 production 檔案系統**，自 vector store `files.content` 取回）→ 退 `/api/source`（本地法條）→ 雙敗友善降級；toggle 有快取。
- 延續輪（`ui.isFollowup`）抑制「超出涵蓋範圍」缺口橫幅；空狀態注入有競態 guard（對話已開始不注入）。

### 語料與 vector store
- **vector store**：`vs_6a27e1fb471c8191aad6e549fb7f2492`（**單一、append 維護**，勿新建）。**14,161 檔**：法條 1,171・施行細則 197・函釋 666・FAQ 169・**判決 11,958**（24 月全量，司法院裁判書，cutoff 2026-04）。
- `data/` 進 repo；**`data/judgments/`（11,868 檔）gitignored**（量大含個資）——production 無判決本地檔，這就是 `/api/source_vs` 存在的原因。
- 檔名 `{簡稱}-{doc_type}-{標題}.txt`、首行管線 metadata（`來源|效力|字號|發文日|對應條號|母法`）；法律檔 `AuData=CF`、命令檔 `AuData=CM`。
- 語料變動後重跑 `scripts/build_capability_manifest.py` → `app/capability_manifest.json`（`GET /api/capability` 給前端空狀態/能力回答）→ 部署。

### 已知 backlog（詳見 memory `answer-clarity-followups`）
- 個資法 §12 召回弱（語意未命中，屬 embedding/語料調校，另開單）。
- guardrail 可觀測性：QueryLog 無 evidence_mode/intent 欄位（oos 觸發率不可監控）；classifier fork 延續輪付全鏈 input token（長對話 ~2x，可改傳截斷文字）。
- 「語料未涵蓋」tier 出現率需觀察（門檻 0.35 後域外題不再拿空 context）。
- 同步 `stream_answer` 無 gate（production 不用它；啟用前須補）。

## 整體流程（你會走的路）

1. **brainstorming** 釐清要做什麼，順手用 **visual companion + frontend-design** 出前端 mockup
2. **writing-plans** 把需求落成 `spec.md` + `plan.md`（最後會餵給提案模板）
3. 補 6 個缺口（見下）→ 跑通問答
4. 部署上 **Railway**（GitHub 連結式 CICD）
5. 用 `workshop/proposal-template.md` 吃 spec+plan → 生成 7 步提案 → 丟 Gamma

## 6 個缺口（完整三層提示卡在 README.md）

| # | 缺口 | 用哪個工具 |
|---|---|---|
| 1 | `app/rag.py` `parse_response`（核心·有測試） | 看 test 反推、brainstorming |
| 2 | `app/rag.py` `answer_question`（核心·有測試） | **context7** 查 file_search 用法 |
| 3 | `scripts/ingest.py` chunking 參數 | **context7** 研究法規 chunking |
| 4 | `static/index.html` 前端美化 | **frontend-design** + visual companion |
| 5 | E2E 測試 | **playwright** skill + MCP |
| 6 | 錯誤監控 | **sentry** MCP + sdk-setup skill |

> 起點驗證：`pytest -v` 應為 **3 紅 4 綠**。紅的就是缺口 1/2 的 TDD 目標。

## 怎麼工作（給 Claude 的指引）

- **遵循 superpowers 工作流**：實作前先 TDD（先讓失敗測試通過）、卡關用 systematic-debugging、寫完用 code review。
- **遵循 karpathy guidelines**：最小改動、不過度設計、surface 假設、定義可驗證的成功條件。
- **缺口要引導學員自己想**，不要直接把答案貼上去。優先「指方向 + 教學員用對的 skill/工具」，例如缺口 2、3 一律先用 context7 研究，而非憑記憶給 code。
- 完整答案在 `solution` branch（`git show solution:app/rag.py`），**僅作最後參考**，別一開始就看。

## 硬約束

- **部署走 GitHub 連結式 CICD**：push 到 GitHub → Railway 連結該 repo → 自動 build。**不要用 `railway up` 本地直推**。
- **Secrets 走環境變數**：`OPENAI_API_KEY`、`VECTOR_STORE_ID`、`OPENAI_MODEL`、`DATABASE_URL`、`SENTRY_DSN` 走 `.env`(本地) / Railway variables，**不要進 repo**。`VECTOR_STORE_ID` 用 append 維護同一個，重建索引通常**不需改 Railway 變數**。
- file search 的 chunking 只有兩顆旋鈕（`max_chunk_size_tokens` 100–4096、`chunk_overlap_tokens` ≤ max/2）；structure-awareness 靠**上傳前的語料前處理**（含 doc_type、首行 metadata、url attribute）。
- **語料品質**：補語料只用真實官方開放資料、不杜撰；新增資料型別後跑 DB 稽核盤查（編碼/空殼/母法對應/重複/真實性）。

## 常用指令

```bash
python -m venv .venv && . .venv/bin/activate       # macOS/Linux（Windows: .venv\Scripts\activate）
pip install -r requirements.txt
pytest -q                          # 全測試（目前 155 綠）
uvicorn app.main:app --reload      # 本地啟動 http://localhost:8000
npx playwright test                # 前端 E2E（對 scripts/mock_server.py:8001；先 npx playwright install chromium）
python scripts/mock_server.py      # 前端離線驗收 mock（固定假資料，無 OpenAI/DB）

# 語料 → 索引
python scripts/fetch_corpus.py --xml <CF.xml>      # 法律檔（AuData=CF）；命令檔(施行細則)用 AuData=CM 下載
python scripts/ingest.py                            # 新建 vector store（首建）
python scripts/ingest.py --vector-store-id <vsid>   # append 模式：加新語料到既有 store（ID 不變、Railway 免改）
```
