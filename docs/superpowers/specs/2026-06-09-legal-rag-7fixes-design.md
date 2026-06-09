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
