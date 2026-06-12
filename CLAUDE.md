# CLAUDE.md — 工作坊起始 repo 導覽

> 這份檔案會被 Claude Code 自動讀取。學員也請先讀一遍，建立全貌。

## 這是什麼

這是一個**工作坊起始 repo（starter）**。它能跑起來，但問答功能還沒實作 ——
你的任務是用 AI 工具鏈把 6 個缺口補完，部署上 Railway，最後用提案模板把成果「提案」出來。

產品本身：一個**法規 RAG 問答 agent**，用 **OpenAI file search（託管服務）** 對法規語料做問答並附條文引用。

> ⚠️ **不要自建 RAG**（不要裝 LangChain/向量資料庫）。引擎就是 OpenAI file search 託管服務 —— 這是 3 小時做得完的關鍵。

## 目前實作狀態（2026-06，給接手的 Claude）

> 6 個起始缺口**已全部補完**並上線；其後又完成「七項需求修正（Phase A）＋ 語料擴充（Phase B）」。
> 詳見 `docs/superpowers/specs/2026-06-09-legal-rag-7fixes-design.md`（含程式介面、attributes schema、語料盤點、驗證）。

- **問答管線**：`app/rag.py` `answer_question`(同步) / `astream_answer`(async，SSE 用) → `parse_response` 回 `{answer, citations, evidence, evidence_mode, response_id, usage}`。
- **檢索分流（已上線）**：問答管線改走 `retrieve_dual` 單庫雙路檢索——法條路（`doc_type≠判決`，top-12）+ 判決路（`doc_type=判決`，top-3、門檻 0.35），各設名額避免判決被高分法條擠光。`answer_question`/`stream_answer`/`astream_answer` 皆先檢索 → `build_context_block` 自組 context（**不帶 file_search tool**）→ 餵 Responses API → `parse_dual_response`。`aretrieve_dual` 用 `asyncio.gather` 並行兩路。判決留庫內、靠 filter 分流（**不刪檔**）。
- **SSE 逐字串流**：`/api/ask/stream` 用 `AsyncOpenAI` + `astream_answer`（同步 Stream 放 async generator 會阻塞 event loop，是逐字失效的根因）；串流前先 await `aretrieve_dual` 組 context，DB log 走 `run_in_threadpool`。
- **引證（確定性，來自檢索）**：citations/evidence 由 `retrieve_dual` 結果確定性組出（非解析 model annotations）；模型標 `[來源N]` → linkify 成 `[^N]` 腳註（`evidence_mode="dual_cited"`），未標則顯示全部檢索段（`dual_retrieved`）。舊 `parse_response`/`_build_answer_kwargs`（file_search 路）保留作回滾。
- **輸出格式**：`ANSWER_INSTRUCTIONS` 走「表格為主（智慧判斷）＋ 確定性分層【明文】【解釋/裁量】【實務見解】【語料未涵蓋】＋ 區分『法律未明文 vs 語料未收錄』」。前端 `static/index.html` 串流時緩衝未成形表格（`splitUnstableTail`/`renderStream`），證據用 `.legal-pre` 保留換行，原文以官方 url 外連。
- **語料（`data/`，~2074 檔上傳）**：法條 1042 + 施行細則 197 + 函釋 666 + FAQ 169（皆真實政府開放資料）。doc_type ∈ {法條, 施行細則, 函釋, FAQ, 判決, 裁罰}。**判決尚缺**（司法院 API 需帳密+限時段）。
- **語料來源**：法律檔 `AuData=CF`、命令檔（施行細則）`AuData=CM`；函釋/FAQ 為各主管機關開放資料/官網問答，**檔名 `{簡稱}-{doc_type}-{標題}.txt`、首行管線 metadata**（`來源|效力|字號|發文日|對應條號|母法`）。
- **vector store**：`vs_6a27e1fb471c8191aad6e549fb7f2492`（**單一、用 append 維護**，勿每次新建）。

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
pytest -q                          # 全測試（目前 81 綠）
uvicorn app.main:app --reload      # 本地啟動 http://localhost:8000
npx playwright test                # 前端 E2E（對 scripts/mock_server.py:8001；先 npx playwright install chromium）
python scripts/mock_server.py      # 前端離線驗收 mock（固定假資料，無 OpenAI/DB）

# 語料 → 索引
python scripts/fetch_corpus.py --xml <CF.xml>      # 法律檔（AuData=CF）；命令檔(施行細則)用 AuData=CM 下載
python scripts/ingest.py                            # 新建 vector store（首建）
python scripts/ingest.py --vector-store-id <vsid>   # append 模式：加新語料到既有 store（ID 不變、Railway 免改）
```
