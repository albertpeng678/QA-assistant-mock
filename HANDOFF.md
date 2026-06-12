# HANDOFF — 判決語料 + 檢索分流（Phase D 接手指南）

> ⚠️ **本檔含密碼，已加入 `.gitignore`、不進 repo。換機接手請用安全管道手動複製此檔，勿提交 git。**
> 建立：2026-06-11｜分支：`feat/legal-rag-gaps`｜接手者：在另一台電腦繼續 Phase D。
> 完整設計見 `docs/superpowers/specs/2026-06-09-legal-rag-7fixes-design.md` §K、`docs/.../plans/2026-06-09-legal-rag-agent.md` Phase D、memory `judgment-corpus-pipeline`。

---

## 0. 帳號 / 金鑰（全部放 `.env`，本檔不存明文）

> ⚠️ **repo 是 PUBLIC**，本檔會進 repo，故**不寫任何明文密碼**。所有 secret 一律放 `.env`
> （已 gitignore、不進 repo）；換機時用安全管道手動帶 `.env`，不要靠 git。

| 用途 | `.env` 鍵 |
|---|---|
| 司法院 opendata 登入（下載判決用）| `JUDICIAL_USER` / `JUDICIAL_PASS`｜登入頁 https://opendata.judicial.gov.tw/member/login |
| OpenAI API key | `OPENAI_API_KEY` |
| Vector store（append 維護，勿新建）| `VECTOR_STORE_ID` |
| 模型 | `OPENAI_MODEL`（gpt-5.4-mini）|

> 🔐 memory `release-followups` 已記：`OPENAI_API_KEY`（`sk-svcacct-…`）與 `RAILWAY_API_TOKEN`
> 曾外洩、**待撤換**。接手後請優先輪換金鑰。

---

## 1. 換機環境設定（接手第一步）

```bash
git clone <repo> && cd QA-assistant && git checkout feat/legal-rag-gaps
python -m venv .venv && .venv\Scripts\activate        # Windows
pip install -r requirements.txt
pip install rarfile numpy                               # pipeline / 驗證用，未列入 requirements
# 手動放入 .env（OPENAI_API_KEY / VECTOR_STORE_ID / OPENAI_MODEL）
python -m pytest tests/ -q                              # 應 98 綠
```

**外部工具（pipeline 需要，路徑寫在 `scripts/build_judgment_corpus.py` 頂部，換機要改）：**
- **UnRAR**：`UNRAR = r"C:\Program Files\WinRAR\UnRAR.exe"`（解 RAR5）。換機改成你機器的 UnRAR/7z 路徑。
- **ripgrep**：`_find_rg()` 自動偵測 PATH / VS Code / Codex 內建 rg.exe；換機若無，會自動退回 python 掃描（慢但可跑）。

**取得登入 cookie（下載判決必需，約 24h 過期）：**
1. 用上方帳密登入 https://opendata.judicial.gov.tw/member/login
2. F12 Console 執行 `document.cookie` 複製整串 → 設環境變數 `JCOOKIE`
3. `JUA` 設為你瀏覽器的 User-Agent（或沿用 `Mozilla/5.0 (Windows NT 10.0; Win64; x64) ... Chrome/...`）
4. 關鍵 cookie 是 `Authorization=Bearer …`（JWT）；過期就重登再取。

---

## 2. 現況（本 session 已完成，已驗證）

- **A** `app/rag.py` `FILE_SEARCH_RANKING_OPTIONS={"score_threshold":0.5}`（抗稀釋①，掛在現用單路 file_search）。
- **B** `scripts/ingest_judgments.py`：判決 JSON→語料檔+上傳，`--strict`（母法≥3次或在案由）、`--exclude-laws`。
- **C** `scripts/build_judgment_corpus.py`：多月 orchestrator（下載→解壓→rg 預篩→strict→上傳）。**已內建 strict**。
- **方向1** `app/rag.py` `retrieve_dual()`：兩路檢索（法條 top-12 thr0.5 + 判決獨立 top-3 thr0.35）。**已測試、但尚未串接進生產**。
- 全部 **pytest 98 綠 + ruff app/ & scripts 全過**，已過 code review（1 Critical+3 Important 已修）。

**store 現況**：僅含早期 PoC 的 **106 筆判決**（202603）。12 月語料的 ~2,800 筆原料**未上傳**（且 `data/judgments/` 已 gitignore，換機 git 不帶 → 需重跑 orchestrator 生成）。

### 換機：哪些不必搬（重要）
- **不要手動搬下載的大檔**。本機 `C:\Users\albertpeng\judicial_work\` 的 RAR + 解壓 JSON（~16GB）與 `data/judgments/` 的 txt **都不必搬**——新機只要設好 cookie、重跑 `build_judgment_corpus.py`，它會**自動重新下載→解壓→精篩**，省去搬幾 GB。
- **上傳後更不依賴本地**：語料一旦 `--upload` 進雲端 vector store（`vs_6a27e1…`），檢索打的是 OpenAI 雲端，新機**完全不需要**本地語料檔；本地原料只是「可重建的中間產物」，丟了也沒差。
- 換機真正要帶的只有三樣：① `git pull`（程式碼）② 手動帶 `HANDOFF.md` + `.env`（含密碼）③ 新機重新登入 opendata 取 cookie。其餘自動重建。
- （若真的想省一次下載：`data/judgments/` 的 txt 體積不大，也可手動 copy 過去跳過重跑；但 `judicial_work\` 的 16GB 無必要搬。）

---

## 3. 後續步驟（依序，細節）

### 步驟 1 ⭐ 生產串接 `retrieve_dual`（架構級重構，最重要）
目前 `answer_question`/`astream_answer` 仍走**單路 file_search tool**（`_build_answer_kwargs`）；分流還沒生效於線上。要改：
1. 問答流程改為：先 `retrieve_dual(client, question, vector_store_id=…)` 取 `{"law":[...],"judgment":[...]}`。
2. **自組 context**：把兩路 chunk 文字組成 prompt（標示【法規】【判決見解】兩區、各帶 filename/url 供引用），餵 `responses.create` 的 `input`/`instructions`，**不再帶 file_search tool**。
3. 改 `parse_response`：citations/evidence 改**自 retrieve_dual 結果**（不再解析 model 的 `file_search_call.results`/annotations）；引用編號自行組（來源序→filename→官方 url）。
4. `astream_answer`（SSE 串流）同步調整：串流前先檢索組 context，再串流生成。
5. 前端 `static/index.html` 證據面板 `.legal-pre` 應相容（來源結構不變即可）；驗證引用腳註對映。
6. **測試**：`tests/test_rag.py` 既有 `answer_question` 測試的 mock 要從 `responses.create` 改成涵蓋 `vector_stores.search`；補串接後的引用/證據測試。跑 `npx playwright test`（對 `scripts/mock_server.py`）確認前端 e2e。
- ⚠️ 這會動 `parse_response`/串流/前端，務必 TDD + 完整回歸；屬高風險重構，預留完整時段。

### 步驟 2 語料上傳（**務必在步驟 1 之後**，否則判決沒分流會稀釋）
1. 取新 cookie（見 §1）。
2. 跑近 2–3 年：`python scripts/build_judgment_corpus.py --months 24 --skip-latest --no-upload`（先寫檔看量）。
   - 每月精篩後約 535 筆；24 月 ≈ 1.3 萬筆（目標 1–1.5 萬）。
   - 領域配額：公司治理/勞動偏多，可考慮加每領域上限（orchestrator 目前未做配額，需小改）。
3. 確認量與分佈合理後上傳：加 `--upload`（append 到 `VECTOR_STORE_ID`，判決 chunk 2048/512）。
4. 上傳後用 §4 的驗證腳本確認 store file_counts + 分流檢索效果。

### 步驟 3（選）去 boilerplate
- 剝判決頭部當事人/程序套語提升判決路 score（實測僅 +2.5%，輔助）。可在 `ingest_judgments.process_one` 寫入前對 JFULL 做 `re.search(r'主[\s　]*文', …)` 起始裁切。

### review 剩餘 Minor（記得處理）
- `build_judgment_corpus.py` `FID_BASE=65335 / YM_BASE=(2026,3)` 是**時間敏感**假設（fid=datasetId+12310、每月-1）。接手時先驗證規律仍成立（開一個近月 dataset detail 頁對照 fid），失效就更新基準。硬編碼 `UNRAR`/rg 路徑換機要改。

---

## 4. 常用指令 / 驗證

```bash
# 測試 + lint
python -m pytest tests/ -q                  # 98 綠
python -m ruff check app/ scripts/

# 判決 pipeline（cookie 設好後）
python scripts/ingest_judgments.py --dry-run --strict --per-domain 5     # 單月乾跑
python scripts/build_judgment_corpus.py --months 12 --skip-latest --no-upload   # 多月寫檔
# 加 --upload 才上傳

# 驗證 store / 分流（參考本 session 用過的臨時腳本，在 C:\Users\albertpeng\judicial_work\）
#   verify.py（store file_counts + filter 檢索）、dual_retrieval_demo.py（分流對比）、
#   judgment_topk.py（判決路 score 分佈）。換機需重寫或複製。
```

## 5. 關鍵檔案
- `app/rag.py`：`FILE_SEARCH_RANKING_OPTIONS`、`retrieve_dual()`、`LAW_TOP_K/JUDGMENT_TOP_M/DUAL_*THRESHOLD`、`answer_question`/`astream_answer`（待改）。
- `scripts/ingest_judgments.py`：判決前處理+上傳。
- `scripts/build_judgment_corpus.py`：多月 orchestrator。
- `data/judgments/`（gitignore）：判決語料原料，由 pipeline 重建。
- 暫存原料/腳本：`C:\Users\albertpeng\judicial_work\`（本機；換機沒有，重跑即可）。
