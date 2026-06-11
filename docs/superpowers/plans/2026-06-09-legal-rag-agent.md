# 法規 RAG 問答 agent 實作計畫

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 補完工作坊 6 個缺口，做出企業法遵 RAG 問答 agent（OpenAI file search 引擎、左側 offcanvas 引證、多輪對話、suggestion chips），部署上 Railway。

**Architecture:** FastAPI 後端 `/api/ask` 呼叫 `answer_question`（file_search tool + `include=["file_search_call.results"]`）→ `parse_response` 拼出 answer/citations/evidence/response_id；`/api/suggest` 用 structured outputs 另算追問；`/api/source/{filename}` 供抽屜內展開原文。前端方向 A（墨黑·紙白·酒紅 / Fraunces+Spectral / Lucide），左側 hamburger offcanvas。語料用全國法規資料庫 XML 經 mojLawSplit 切成一條一檔。

**Tech Stack:** Python 3.11、FastAPI、openai SDK（Responses API）、pytest、Tailwind CDN、Lucide、Playwright、Sentry。

**現況：** 缺口 1 基礎版 `parse_response`（answer+citations）已在工作樹實作並通過既有 2 測試；本計畫 Task 1 在其上向後相容擴充。`pytest tests/test_rag.py` 目前 parse 2 綠、`answer_question` 1 紅。

---

## 檔案結構

- `app/rag.py` — 核心：`parse_response`（擴充 evidence/response_id）、`answer_question`（file_search）、`suggest_followups`（新）
- `app/main.py` — 路由：`/api/ask`（擴充）、`/api/suggest`（新）、`/api/source/{filename}`（新）
- `app/config.py` — 環境變數（既有）
- `scripts/ingest.py` — chunking 參數（缺口 3）
- `scripts/fetch_corpus.py` — 新：抓全國法規 XML + mojLawSplit 切一條一檔到 data/
- `static/index.html` — 前端（缺口 4）
- `tests/test_rag.py` — 既有 + 新增單元測試
- `tests/test_api.py` — 新：FastAPI 端點測試
- `tests/e2e/test_ask.spec.ts` — 新：Playwright E2E（缺口 5）

---

## Task 1: parse_response 向後相容擴充（evidence + response_id）

**Files:**
- Modify: `app/rag.py`（`parse_response`）
- Test: `tests/test_rag.py`

- [ ] **Step 1: 寫失敗測試（新增 evidence + response_id）**

加到 `tests/test_rag.py` 末尾：

```python
def _fake_response_with_results():
    annotation = SimpleNamespace(filename="個資法-第12條.txt")
    content = SimpleNamespace(text="應通知當事人。", annotations=[annotation])
    message = SimpleNamespace(type="message", content=[content])
    result = SimpleNamespace(filename="個資法-第12條.txt", text="…應查明後以適當方式通知當事人。", score=0.91)
    fs_call = SimpleNamespace(type="file_search_call", content=None, results=[result])
    return SimpleNamespace(output=[fs_call, message], id="resp_abc")

def test_parse_response_extracts_evidence_and_response_id():
    result = parse_response(_fake_response_with_results())
    assert result["response_id"] == "resp_abc"
    assert result["evidence"] == [
        {"filename": "個資法-第12條.txt", "text": "…應查明後以適當方式通知當事人。", "score": 0.91}
    ]

def test_parse_response_evidence_empty_when_no_results():
    # 既有 _fake_response() 的 file_search_call 無 results 屬性
    result = parse_response(_fake_response())
    assert result["evidence"] == []
    assert result["response_id"] is None
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `pytest tests/test_rag.py -v -k "evidence or response_id"`
Expected: FAIL（`KeyError: 'evidence'` / `'response_id'`）

- [ ] **Step 3: 擴充 parse_response（保留既有 answer/citations 邏輯）**

在 `app/rag.py` 的 `parse_response` return 前，新增 evidence 收集；改 return：

```python
    evidence = []
    for item in response.output:
        if getattr(item, "type", None) != "file_search_call":
            continue
        for r in getattr(item, "results", None) or []:
            evidence.append({
                "filename": getattr(r, "filename", None),
                "text": getattr(r, "text", None),
                "score": getattr(r, "score", None),
            })
    return {
        "answer": "".join(answer_parts),
        "citations": citations,
        "evidence": evidence,
        "response_id": getattr(response, "id", None),
    }
```

- [ ] **Step 4: 跑全部 parse 測試確認綠（含既有）**

Run: `pytest tests/test_rag.py -v -k parse_response`
Expected: 4 PASS（既有 2 + 新 2）

- [ ] **Step 5: Commit**

```bash
git add app/rag.py tests/test_rag.py
git commit -m "feat: parse_response 擴充 evidence 與 response_id（缺口1）"
```

---

## Task 2: answer_question — file_search + include + previous_response_id（缺口 2）

**Files:**
- Modify: `app/rag.py`（`answer_question`）
- Test: `tests/test_rag.py`

- [ ] **Step 1: 寫失敗測試（補 include 與 previous_response_id）**

加到 `tests/test_rag.py`（既有 `_FakeClient` 之後）：

```python
def test_answer_question_includes_search_results():
    client = _FakeClient(_fake_response_with_results())
    answer_question(client, "個資外洩?", vector_store_id="vs_1", model="gpt-4o-mini")
    call = client.responses.calls[0]
    assert call["include"] == ["file_search_call.results"]

def test_answer_question_chains_previous_response_id():
    client = _FakeClient(_fake_response_with_results())
    answer_question(client, "追問", vector_store_id="vs_1", model="gpt-4o-mini",
                    previous_response_id="resp_abc")
    call = client.responses.calls[0]
    assert call["previous_response_id"] == "resp_abc"

def test_answer_question_omits_previous_response_id_when_none():
    client = _FakeClient(_fake_response_with_results())
    answer_question(client, "首問", vector_store_id="vs_1", model="gpt-4o-mini")
    assert "previous_response_id" not in client.responses.calls[0]
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `pytest tests/test_rag.py -v -k answer_question`
Expected: 既有 1 紅（NotImplementedError）+ 新 3 紅

- [ ] **Step 3: 實作 answer_question**

替換 `app/rag.py` 的 `answer_question`：

```python
def answer_question(client, question, *, vector_store_id, model, previous_response_id=None):
    """以 file_search tool 對 vector store 提問，回傳 answer + citations + evidence + response_id。"""
    kwargs = {
        "model": model,
        "input": question,
        "tools": [{"type": "file_search", "vector_store_ids": [vector_store_id]}],
        "include": ["file_search_call.results"],
    }
    if previous_response_id:
        kwargs["previous_response_id"] = previous_response_id
    response = client.responses.create(**kwargs)
    return parse_response(response)
```

- [ ] **Step 4: 跑全部 rag 測試確認綠**

Run: `pytest tests/test_rag.py -v`
Expected: 全綠（既有 3 + 新 5）

- [ ] **Step 5: Commit**

```bash
git add app/rag.py tests/test_rag.py
git commit -m "feat: answer_question 接 file_search + include + 多輪（缺口2）"
```

---

## Task 3: suggest_followups — structured outputs 追問建議

**Files:**
- Modify: `app/rag.py`（新增 `suggest_followups`）
- Test: `tests/test_rag.py`

- [ ] **Step 1: 寫失敗測試**

```python
import json
from app.rag import suggest_followups

class _FakeStructResponses:
    def __init__(self, payload):
        self._payload = payload
        self.calls = []
    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(output_text=json.dumps(self._payload))

class _FakeStructClient:
    def __init__(self, payload):
        self.responses = _FakeStructResponses(payload)

def test_suggest_followups_returns_questions():
    client = _FakeStructClient({"questions": ["通報期限？", "罰鍰範圍？", "民事責任？"]})
    out = suggest_followups(client, "個資外洩?", "應通知當事人。", model="gpt-4o-mini")
    assert out["questions"] == ["通報期限？", "罰鍰範圍？", "民事責任？"]
    # 確認用了 structured outputs（json_schema）
    call = client.responses.calls[0]
    assert call["text"]["format"]["type"] == "json_schema"
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `pytest tests/test_rag.py -v -k suggest_followups`
Expected: FAIL（ImportError / NotImplemented）

- [ ] **Step 3: 實作 suggest_followups**

加到 `app/rag.py`：

```python
import json

_FOLLOWUP_SCHEMA = {
    "type": "json_schema",
    "name": "followups",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "questions": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["questions"],
        "additionalProperties": False,
    },
}

def suggest_followups(client, question, answer, *, model):
    """用 structured outputs 產生 3 題法遵追問建議。與主問答分離，不影響 citation annotations。"""
    prompt = (
        "你是法遵助理。根據以下問答，產生 3 個使用者可能想接著問的繁體中文追問問題，"
        f"每題簡短。\n問題：{question}\n回答：{answer}"
    )
    response = client.responses.create(
        model=model, input=prompt, text={"format": _FOLLOWUP_SCHEMA}
    )
    return json.loads(response.output_text)
```

- [ ] **Step 4: 跑測試確認綠**

Run: `pytest tests/test_rag.py -v -k suggest_followups`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/rag.py tests/test_rag.py
git commit -m "feat: suggest_followups 追問建議（structured outputs）"
```

---

## Task 4: API 端點 — /api/ask 擴充、/api/suggest、/api/source

**Files:**
- Modify: `app/main.py`
- Test: `tests/test_api.py`（新）

- [ ] **Step 1: 寫失敗測試（用 FastAPI 依賴覆寫，不打真 API）**

建立 `tests/test_api.py`：

```python
from fastapi.testclient import TestClient
from app.main import app, get_answerer
from app import config

def _fake_answerer(question, previous_response_id=None):
    return {"answer": "應通知當事人。", "citations": ["個資法-第12條.txt"],
            "evidence": [{"filename": "個資法-第12條.txt", "text": "…通知當事人。", "score": 0.91}],
            "response_id": "resp_abc"}

def test_ask_returns_evidence_and_response_id():
    app.dependency_overrides[get_answerer] = lambda: _fake_answerer
    client = TestClient(app)
    r = client.post("/api/ask", json={"question": "個資外洩?"})
    assert r.status_code == 200
    body = r.json()
    assert body["evidence"][0]["filename"] == "個資法-第12條.txt"
    assert body["response_id"] == "resp_abc"
    app.dependency_overrides.clear()

def test_source_endpoint_rejects_path_traversal():
    client = TestClient(app)
    r = client.get("/api/source/..%2F..%2Fapp%2Fmain.py")
    assert r.status_code in (400, 404)
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `pytest tests/test_api.py -v`
Expected: FAIL（evidence 未回傳 / source 路由不存在）

- [ ] **Step 3: 擴充 main.py**

`AskRequest` 加選用欄位、`answerer` 收 previous_response_id、新增兩路由：

```python
from fastapi import HTTPException

class AskRequest(BaseModel):
    question: str
    previous_response_id: str | None = None

    @field_validator("question")
    @classmethod
    def not_blank(cls, v):
        if not v or not v.strip():
            raise ValueError("question 不可為空")
        return v.strip()

def get_answerer():
    client = OpenAI(api_key=config.OPENAI_API_KEY)
    def answerer(question: str, previous_response_id=None):
        return answer_question(
            client, question, vector_store_id=config.VECTOR_STORE_ID,
            model=config.OPENAI_MODEL, previous_response_id=previous_response_id,
        )
    return answerer

@app.post("/api/ask")
def ask(req: AskRequest, answerer=Depends(get_answerer)):
    return answerer(req.question, req.previous_response_id)

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

@app.get("/api/source/{filename}")
def source(filename: str):
    target = (DATA_DIR / filename).resolve()
    if DATA_DIR.resolve() not in target.parents or not target.is_file():
        raise HTTPException(status_code=404, detail="找不到該來源檔")
    return {"filename": filename, "text": target.read_text(encoding="utf-8")}
```

並新增 `/api/suggest`（用同一 client）：

```python
from app.rag import suggest_followups

class SuggestRequest(BaseModel):
    question: str
    answer: str

@app.post("/api/suggest")
def suggest(req: SuggestRequest):
    client = OpenAI(api_key=config.OPENAI_API_KEY)
    return suggest_followups(client, req.question, req.answer, model=config.OPENAI_MODEL)
```

- [ ] **Step 4: 跑測試確認綠**

Run: `pytest tests/test_api.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/main.py tests/test_api.py
git commit -m "feat: /api/ask 擴充 + /api/suggest + /api/source 端點"
```

---

## Task 5: 語料取得與切檔（fetch_corpus.py）

**Files:**
- Create: `scripts/fetch_corpus.py`
- Create: `data/.gitkeep`（既有）

- [ ] **Step 1: 寫切檔腳本（先小範圍驗證）**

`scripts/fetch_corpus.py`：下載全國法規資料庫 RAW XML，篩選 §5.2 清單法規，逐條輸出 `data/{法規名稱}-第N條.txt`。優先評估直接套用 `kong0107/mojLawSplit` 的 `json_split` 輸出再轉檔。腳本須印出產生檔數。

實作要點（無測試，研究型；先用 context7/該 repo README 確認 XML 結構）：
- GET `https://sendlaw.moj.gov.tw/PublicData/GetFile.ashx?DType=RAW_XML&AuData=CF`
- 解析每部法規的「法規名稱」與「條文」節點，條號正規化（第十二條→第12條）
- 旗艦個資：另手動放入 2-3 個 QA-pair `.txt`（國發會 Q&A、1 則裁罰案例），檔名如 `個資-函釋-外洩通報Q&A.txt`，內文首行標 `來源/版次/對應條號`

- [ ] **Step 2: 本地跑、人工抽查 3 個檔**

Run: `python scripts/fetch_corpus.py`
Expected: `data/` 出現一條一檔，抽查 `個人資料保護法-第12條.txt` 內容正確、檔名含條號

- [ ] **Step 3: Commit（程式碼；語料是否入庫見備註）**

```bash
git add scripts/fetch_corpus.py
git commit -m "feat: 語料取得切檔腳本（全國法規XML→一條一檔）"
```

> 備註：法條屬政府開放資料（須註明出處）。data/ 大量條文檔可選擇入庫或由部署時跑 ingest 前生成；旗艦 QA-pair 檔建議入庫示範。

---

## Task 6: chunking 參數（缺口 3）

**Files:**
- Modify: `scripts/ingest.py:15-16`

- [ ] **Step 1: 用 context7 研究參數範圍與法規 chunking 主流值**

查 OpenAI file search static chunking 限制（max 100–4096、overlap ≤ max/2）與長文/法規 RAG 建議。記錄於 commit message。

- [ ] **Step 2: 填入參數**

因已「一條一檔」，多數檔短。設中小 chunk + 小 overlap 保留跨項語意，例如：

```python
MAX_CHUNK_SIZE_TOKENS = 800
CHUNK_OVERLAP_TOKENS = 200
```

（實際值依 Step 1 研究調整；務必滿足 overlap ≤ max/2。）

- [ ] **Step 3: 乾跑驗證（需 OPENAI_API_KEY 與少量 data/ 檔）**

Run: `python scripts/ingest.py`
Expected: 印出 `VECTOR_STORE_ID=vs_...`、上傳狀態 completed

- [ ] **Step 4: Commit**

```bash
git add scripts/ingest.py
git commit -m "feat: chunking 參數（缺口3，context7 研究後定值）"
```

---

## Task 7: 前端方向 A + 左側 offcanvas（缺口 4）

**Files:**
- Modify: `static/index.html`

- [ ] **Step 1: 重寫 index.html（完整實作）**

依設計：墨黑 `#1a1714`·紙白 `#f5f2ea`·酒紅 `#7a2e2e`；Fraunces+Spectral+Public Sans（Google Fonts CDN）；Lucide（CDN，禁 emoji）；Tailwind CDN。元件：訊息氣泡（多輪，前端保存上一輪 `response_id` 於下一次帶 `previous_response_id`）、答案數字標記 ¹²³、下方去重彙整來源列、左上 hamburger（紅點數）開左側 offcanvas（330px / 手機 84% + 遮罩）、evidence card「開啟原文全文」呼叫 `/api/source/{filename}` 抽屜內展開、suggestion chips（呼叫 `/api/suggest`；空狀態 starter chips）、loading 狀態、錯誤狀態、持久 disclaimer、ESC/遮罩關閉、RWD。

**SSE 串流 + markdown 渲染（新需求）**：
- 用 `fetch` POST `/api/ask/stream` + `ReadableStream` 讀 SSE（EventSource 僅 GET，故用 fetch streaming）。逐 `delta` 累加文字、**增量渲染 markdown**；收到 `final` 事件後渲染來源列/evidence/`query_log_id`。
- Markdown 渲染用 `marked`（CDN）解析 + `DOMPurify`（CDN）淨化後插入，避免 XSS。
- 排版（閱讀順暢）：答案區套 prose 風格——`line-height: 1.75`、段落 `margin` 留白、`ul/ol` 巢狀縮排（`padding-left`）、清單項 `margin` 間距，呼應墨黑/紙白/酒紅。
- 回饋 UI：每則答案下方加 Lucide `thumbs-up`/`thumbs-down`（禁 emoji），點擊 POST `/api/feedback` 帶該則 `query_log_id`。

> 落地時參照 `.superpowers/brainstorm/.../mockup-A-offcanvas-v2.html` 的版面與樣式作為基準。

- [ ] **Step 2: 本地啟動目視驗證**

Run: `uvicorn app.main:app --reload`，開 http://localhost:8000
Expected: 介面為方向 A；hamburger 開左側抽屜；無 emoji；三尺寸 RWD 正常（用瀏覽器縮放/裝置模擬）

- [ ] **Step 3: Commit**

```bash
git add static/index.html
git commit -m "feat: 前端方向A + 左側offcanvas引證 + suggestion chips（缺口4）"
```

---

## Task 8: E2E 測試（缺口 5）

**Files:**
- Create: `tests/e2e/test_ask.spec.ts`、`playwright.config.ts`、`package.json`

- [ ] **Step 1: 用 playwright skill 初始化、寫 E2E**

測試流程：開首頁 → 輸入問題送出 → 等到答案出現 + 至少一條來源列 → 點 hamburger → offcanvas 出現 evidence card → 點「開啟原文全文」→ 抽屜內展開全文。後端 `/api/ask` 用路由攔截（route.fulfill）回固定 JSON，避免打真 OpenAI。

- [ ] **Step 2: 跑 E2E**

Run: `npx playwright test`
Expected: PASS（含 trace 可回放）

- [ ] **Step 3: Commit**

```bash
git add tests/e2e playwright.config.ts package.json
git commit -m "test: E2E 問答→引用→展開原文（缺口5）"
```

---

## Task 9: 錯誤監控 Sentry（缺口 6）

**Files:**
- Modify: `app/main.py`、`requirements.txt`

- [ ] **Step 1: 用 sentry-sdk-setup skill 裝 SDK**

`requirements.txt` 加 `sentry-sdk[fastapi]`；`app/main.py` 啟動時 `sentry_sdk.init(dsn=os.getenv("SENTRY_DSN"), ...)`（DSN 走環境變數）。針對缺 key / vector store 空 / OpenAI 逾時加結構化錯誤處理並讓 Sentry 捕捉。

- [ ] **Step 2: 故意觸發一個錯誤並用 sentry MCP 查到 issue**

Run: 觸發測試錯誤端點 → 用 sentry MCP `search_issues` 確認該 issue 出現
Expected: Sentry 收到該 issue

- [ ] **Step 3: Commit**

```bash
git add app/main.py requirements.txt
git commit -m "feat: Sentry 錯誤監控（缺口6）"
```

---

## Task 10: 部署 Railway（GitHub 連結式 CICD）

- [ ] **Step 1: push 到 GitHub**（master）
- [ ] **Step 2: Railway New Project → Deploy from GitHub repo → 選此 repo**
- [ ] **Step 3: Variables 設 `OPENAI_API_KEY`、`VECTOR_STORE_ID`、`OPENAI_MODEL`、`SENTRY_DSN`**（不進 repo）
- [ ] **Step 4: 確認自動 build & deploy，`railway open` 開線上頁問一題驗證**
- [ ] **Step 5: 對線上頁跑 Task 8 的 E2E（指向部署 URL）**

> 硬約束：**不用 `railway up` 本地直推**，一律 GitHub 連結式 CICD。

---

## Task 11: Postgres 營運 log — DB 模組（query_logs + feedback）

> 新增需求（產品營運可觀測性）。Railway Postgres 注入 `DATABASE_URL`。**可靠性鐵則：log 寫入失敗絕不可影響問答主流程。**

**Files:**
- Create: `app/db.py`（SQLAlchemy engine/session/models/init）
- Modify: `requirements.txt`（加 `sqlalchemy>=2.0`、`psycopg[binary]`）
- Test: `tests/test_db.py`（用 SQLite in-memory 覆寫，離線可跑）

- [ ] **Step 1: 寫失敗測試**

```python
from app.db import Base, QueryLog, Feedback, make_session_factory

def test_querylog_and_feedback_roundtrip():
    Session = make_session_factory("sqlite+pysqlite:///:memory:")
    with Session() as s:
        log = QueryLog(question="個資外洩?", answer="應通知當事人。",
                       citations=["個資法-第12條.txt"], evidence=[{"filename":"個資法-第12條.txt","score":0.91}],
                       citation_count=1, has_answer=True, latency_ms=1200,
                       input_tokens=10, output_tokens=20, total_tokens=30,
                       model="gpt-4o-mini", response_id="resp_abc", previous_response_id=None, error=None)
        s.add(log); s.commit()
        fb = Feedback(query_log_id=log.id, rating="up")
        s.add(fb); s.commit()
        assert s.query(QueryLog).count() == 1
        assert s.query(Feedback).first().rating == "up"
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `pip install "sqlalchemy>=2.0" "psycopg[binary]"` 後 `python -m pytest tests/test_db.py -v`
Expected: FAIL（ImportError app.db）

- [ ] **Step 3: 實作 app/db.py**

```python
from sqlalchemy import create_engine, String, Integer, Boolean, Text, DateTime, ForeignKey, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
from sqlalchemy.types import JSON

class Base(DeclarativeBase):
    pass

class QueryLog(Base):
    __tablename__ = "query_logs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped["DateTime"] = mapped_column(DateTime(timezone=True), server_default=func.now())
    question: Mapped[str] = mapped_column(Text)
    answer: Mapped[str] = mapped_column(Text, default="")
    citations: Mapped[list] = mapped_column(JSON, default=list)
    evidence: Mapped[list] = mapped_column(JSON, default=list)
    citation_count: Mapped[int] = mapped_column(Integer, default=0)
    has_answer: Mapped[bool] = mapped_column(Boolean, default=False)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0)
    model: Mapped[str] = mapped_column(String(64), default="")
    response_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    previous_response_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

class Feedback(Base):
    __tablename__ = "feedback"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    query_log_id: Mapped[int] = mapped_column(ForeignKey("query_logs.id"))
    rating: Mapped[str] = mapped_column(String(8))  # "up" | "down"
    created_at: Mapped["DateTime"] = mapped_column(DateTime(timezone=True), server_default=func.now())

def make_session_factory(url):
    engine = create_engine(url, future=True)
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)
```

- [ ] **Step 4: 跑測試確認綠**

Run: `python -m pytest tests/test_db.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/db.py tests/test_db.py requirements.txt
git commit -m "feat: Postgres 營運 log DB 模組（query_logs + feedback）"
```

---

## Task 12: 問答 log 寫入整合（非阻斷）+ usage 擷取

**Files:**
- Modify: `app/rag.py`（`parse_response` 增 `usage`，`answer_question` 透傳）
- Modify: `app/main.py`（`/api/ask` 量測 latency、寫 log，try/except 包覆）
- Test: `tests/test_api.py`

- [ ] **Step 1: 寫失敗測試（log 寫入失敗不可影響回應）**

```python
def test_ask_logs_but_failure_does_not_break_response(monkeypatch):
    app.dependency_overrides[get_answerer] = lambda: _fake_answerer
    # 故意讓 log 寫入丟例外
    import app.main as m
    monkeypatch.setattr(m, "log_query", lambda **kw: (_ for _ in ()).throw(RuntimeError("db down")))
    client = TestClient(app)
    r = client.post("/api/ask", json={"question": "個資外洩?"})
    assert r.status_code == 200
    assert r.json()["answer"] == "應通知當事人。"
    app.dependency_overrides.clear()
```

- [ ] **Step 2: 跑確認失敗** — `python -m pytest tests/test_api.py -v -k logs` → FAIL（log_query 不存在）

- [ ] **Step 3: 實作**
  - `app/rag.py` `parse_response`：新增 `usage` 欄位 = `{input_tokens, output_tokens, total_tokens}`（取自 `getattr(response, "usage", None)`，無則 0），並加對應測試到 `tests/test_rag.py`。
  - `app/main.py`：模組層初始化 session factory（`DATABASE_URL` 有才建，無則 None）；`log_query(**fields)` 函式寫一列 `QueryLog`；`/api/ask` 用 `time.perf_counter()` 量 latency、組欄位、**在 try/except 內**呼叫 `log_query`（失敗只 `logging.warning`/Sentry，不 raise）。

- [ ] **Step 4: 跑確認綠** — `python -m pytest tests/test_api.py tests/test_rag.py -v`

- [ ] **Step 5: Commit**

```bash
git add app/rag.py app/main.py tests/test_api.py tests/test_rag.py
git commit -m "feat: /api/ask 非阻斷寫入營運 log + usage 擷取"
```

---

## Task 13: /api/feedback 端點

**Files:**
- Modify: `app/main.py`
- Test: `tests/test_api.py`

- [ ] **Step 1: 寫失敗測試**

```python
def test_feedback_accepts_rating(monkeypatch):
    import app.main as m
    saved = {}
    monkeypatch.setattr(m, "save_feedback", lambda query_log_id, rating: saved.update(id=query_log_id, r=rating))
    client = TestClient(app)
    r = client.post("/api/feedback", json={"query_log_id": 1, "rating": "up"})
    assert r.status_code == 200
    assert saved == {"id": 1, "r": "up"}
```

- [ ] **Step 2: 跑確認失敗**
- [ ] **Step 3: 實作** `/api/feedback`（`FeedbackRequest{query_log_id:int, rating:Literal["up","down"]}` → `save_feedback` 寫 Feedback；DB 無則回 200 但記 warning）。`/api/ask` 回應需含 `query_log_id`（log 寫入後回傳 id；無 DB 時為 None）供前端回饋用。
- [ ] **Step 4: 跑確認綠**
- [ ] **Step 5: Commit** `feat: /api/feedback 使用者回饋端點`

---

## Task 14: SSE 串流 + markdown 格式 + gpt-5.4-mini 參數

> gpt-5.4-mini 為 GPT-5 推理模型：用 `reasoning={"effort":"low"}`、`text={"verbosity":"medium"}`、**不傳 temperature/top_p**、`max_output_tokens=2048`。輸出指示模型用 markdown list/巢狀縮排/段落。

**Files:**
- Modify: `app/rag.py`（新增 `stream_answer` generator + 共用 `ANSWER_INSTRUCTIONS`/`_MODEL_PARAMS`；retrofit `answer_question` 套用相同 params）
- Modify: `app/main.py`（新增 SSE 端點 `/api/ask/stream`）
- Modify: `requirements.txt`（加 `sse-starlette`）
- Test: `tests/test_rag.py`、`tests/test_api.py`

- [ ] **Step 1: rag.py 加共用參數與指示（先寫測試）**

```python
ANSWER_INSTRUCTIONS = (
    "你是台灣企業法遵研究助理。回答務必：以繁體中文；使用 Markdown 結構——"
    "重點用條列（- ），有層次時用巢狀縮排，段落之間空行分隔；先結論後依據；"
    "引用條文時標明法規名稱與條號。本回答為研究輔助，非正式法律意見。"
)
_MODEL_PARAMS = {
    "reasoning": {"effort": "low"},
    "text": {"verbosity": "medium"},
    "max_output_tokens": 2048,
}
```
測試（tests/test_rag.py）：斷言 `answer_question` 呼叫 create 時帶 `reasoning`、`text.verbosity`、`instructions`、且**未帶 temperature/top_p**。

- [ ] **Step 2: 跑紅** — `python -m pytest tests/test_rag.py -v -k "params or instructions"`

- [ ] **Step 3: retrofit answer_question + 新增 stream_answer**
  - `answer_question` 的 kwargs 併入 `**_MODEL_PARAMS` 與 `instructions=ANSWER_INSTRUCTIONS`（既有 model/input/tools/include 斷言不受影響）。
  - 新增 `stream_answer(client, question, *, vector_store_id, model, previous_response_id=None)`：與 answer_question 同 kwargs 但 `stream=True`，逐事件 yield `{"type":"delta","text":...}`；串流結束後從最終 response 物件用 `parse_response` 取 citations/evidence/response_id/usage，yield `{"type":"final", ...}`。用 fake streaming client（yield 一串假 event）寫離線測試。

- [ ] **Step 4: main.py SSE 端點**
  - `/api/ask/stream`（POST，body 同 AskRequest）回 `sse_starlette.EventSourceResponse`：逐筆把 delta 以 `data: {json}` 推送；最後送 `final`（含 citations/evidence/response_id/query_log_id）與 `done`。串流完成後**非阻斷**寫 query_log（latency 從開始到完成）。
  - 保留非串流 `/api/ask` 作為 fallback 與測試路徑。

- [ ] **Step 5: 跑綠 + Commit**

```bash
git add app/rag.py app/main.py requirements.txt tests/test_rag.py tests/test_api.py
git commit -m "feat: SSE 串流問答 + markdown 指示 + gpt-5.4-mini 參數（reasoning/verbosity）"
```

---

## 部署補充（Railway Postgres）

- [ ] `railway link`（選 QA-assistant-mock 專案；互動式，由使用者執行）
- [ ] `railway add`（加 PostgreSQL plugin）→ Railway 自動注入 `DATABASE_URL` 到服務
- [ ] App 啟動時若有 `DATABASE_URL` 則建表（`Base.metadata.create_all`）

> 前端（Task 7）追加：每則答案下方加「讚/倒讚」icon（Lucide `thumbs-up`/`thumbs-down`，無 emoji），點擊 POST `/api/feedback` 帶該則 `query_log_id`。

---

## 三位 auditor 嚴格稽核（release gate）

全部 Task 完成後，平行派 3 位 auditor，全數通過才可 merge/release：
- **frontend auditor**：方向 A 設計合規（色系≤3、禁 emoji、左側 offcanvas、RWD）、a11y、與 API 契約一致、playwright 跨裝置結果。
- **backend auditor**：rag/api 正確性、錯誤處理、log 非阻斷鐵則、無祕密入庫、測試品質。
- **data/db auditor**：語料切檔正確性、檔名條號、ingest chunking、DB schema/索引、log 寫入與隱私（問答內容入庫的合理性）。

---

## 收尾

- [ ] `pytest -v` 全綠（含既有 + 新增單元 + API + DB 測試）
- [ ] 對照 §9 成功標準逐項驗證
- [ ] 三 auditor 全數通過
- [ ] 用 `workshop/proposal-template.md` 吃 spec+plan → 生成 7 步提案

---

# 最終實作紀錄（2026-06-10，原 14 任務之後續完成）

> 6 個起始缺口完成上線後，使用者再提「七項需求修正（Phase A）＋ 語料擴充（Phase B）」。
> 完整介面/schema/語料盤點/驗證見 `docs/superpowers/specs/2026-06-09-legal-rag-7fixes-design.md` §A–I。本段為任務層摘要。
> 規範：TDD（先紅再綠）、systematic-debugging（缺口①根因）、karpathy（最小改動）、playwright skill（E2E+MCP）。

## Phase A — 七項需求修正（免重建索引，commit `101ad7d`，已 push main 部署）

- [x] **① SSE 逐字串流**：root cause = 同步 `Stream` 在 `async def event_gen()` 內迭代阻塞 event loop。
  `app/rag.py` 新增 `astream_answer`（`AsyncOpenAI`+`async for`）；`app/main.py` `/api/ask/stream` 改 async + `run_in_threadpool` 寫 log。
  測試：`test_astream_answer_*`（用 fake AsyncStream、`asyncio.run` 驅動，免裝 pytest-asyncio）。
- [x] **② 表格為主**：重寫 `ANSWER_INSTRUCTIONS`（表格智慧判斷 + GFM 規則）。測試 `test_answer_instructions_cover_table_and_certainty_tiers`。
- [x] **③ 表格平滑渲染**：前端 `splitUnstableTail`/`renderStream` + rAF；修正 sanitize 順序。E2E 斷言 `<table>` 出現且無殘留 `|---`。
- [x] **④ 只顯示被引用段落**：`parse_response` annotations 過濾（cited）+ 空時 top-5 by score（retrieved）+ `evidence_mode`。測試 `test_parse_response_filters_evidence_to_cited_files`、`test_parse_response_retrieved_mode_limits_and_sorts_by_score` 等。
- [x] **⑤ 保留結構**：前端 `.legal-pre`（pre-wrap）。深層款/目縮排判定 cosmetic 未做。
- [x] **⑥ 開啟原文連結**：`parse_response` 取 `result.attributes.url`（既有 store 已含 url，免重建）；前端按鈕改 `<a target=_blank>`。
- [x] **⑦ 資料空白（prompt 層）**：IRAC + 確定性分層 + 區分「法律未明文 vs 語料未收錄」誠實兜底。
- [x] **驗證**：pytest 75 綠 → E2E 9 綠（桌機+Pixel5）→ 真 API 抽查 → Playwright MCP 實機。`test_api.py::test_ask_stream_*` 改 patch async 路徑。`scripts/mock_server.py` 更新為新格式（表格/url/evidence_mode）。

## Phase B — 語料擴充 + ingest 強化（commit `f4c00cf`，已 push main）

- [x] **平行 agent 抓語料**（dispatching-parallel-agents；鐵則：只用真實官方開放資料、抓不到回報、**不杜撰**）：
  - 施行細則 197（命令檔 `AuData=CM`，6 部）
  - 函釋 666（金管會/勞動部/經濟部公司552/個資會/公平會；剔除 22 母法誤標）
  - FAQ 169（個資/金融/消保/公司/公平/勞動，官網 Q&A + PDF 問答集）
  - 判決 0（司法院 API 需帳密+限時段，未杜撰）
- [x] **`scripts/ingest.py` 強化**（TDD via `test_ingest_metadata.py`）：
  - `derive_attributes` 支援施行細則 + 非條文（`_NONARTICLE_RE` + `_parse_first_line` 首行 metadata → doc_type/母法/字號/發文日/對應條號 + authority_level/ref_no/effective_date）
  - `is_empty_article` 略過純「（刪除）/（保留）」（精確比對）
  - `LAW_CATEGORY` 補商業登記法/多層次傳銷管理法；url 僅阿拉伯條號
  - **append 模式 `--vector-store-id`**：沿用同一 vector store、只上傳新語料（不再每次新建）
- [x] **DB 稽核 agent**（release gate，判定 PASS-WITH-FIXES）：盤查組成/空殼/母法對應/重複/真實性/編碼 → 落實 P0/P1 修正（剔除 22、略過 141、補分類、防壞 URL）。
- [x] **重建索引**：append 1032 檔到 `vs_6a27e1fb…`（同 ID、3 批 completed/failed=0、Railway 變數免改）。上線檢索抽查確認施行細則/函釋/FAQ 進得了檢索、補足空白。

## Phase C — 上線後生產修補（2026-06-10，commit `f9c72e3`、`fab1842`，皆已 push main 部署）

> 上線後使用者回報兩類生產問題；各以 systematic-debugging（真實 API 診斷定根因）+ context7 主流解法 + TDD + request/receive code review 處理，真實 API 與 production 雙重驗證。完整根因/解法/驗證見 spec §J。

- [x] **① 答案截斷且無來源/追問**（commit `f9c72e3`）：root cause = 串流只認 `response.completed`，但推理模型撞 `max_output_tokens`（含推理 token）時以 `response.incomplete` 終結 → 永不發 `final` → 截斷且無 citations/evidence/followups（三症狀同源）。
  `rag.py` 終結事件全收（completed/incomplete/failed，`_terminal_response` 精確比對）+ `parse_response` 帶 `status/truncated/incomplete_reason` + `max_output_tokens` 2048→4096 + `suggest_followups` 防壞 JSON；`main.py` `/api/suggest` 優雅降級 + 截斷 telemetry；`index.html` 降級 finalize + 截斷/failed 誠實提示 + flush buf。測試 pytest +9、e2e +2（`__NOFINAL__`/`__FAILEMPTY__`）。
- [x] **③ 表格「依據」欄整欄空白**（commit `fab1842`）：root cause = gpt-5.4-mini 把來源以 inline 引用標記（`turnNfileM`，M 對映 `file_search_call.results`）塞進答案，`_strip_citation_markers` 整段刪除不回收 → 純標記表達的依據欄變空。
  `rag.py` 新增 `_linkify_citation_markers`（單一交替 regex 轉腳註 `[^k]`、依閱讀順序、同檔同號、超範圍安全丟棄）+ `_evidence_for_files`；`parse_response` 以 inline 標記為第一優先（cited evidence 與腳註對齊）；`index.html` 最終以權威 `evt.answer` 渲染。測試 pytest +5、e2e +1（`__MARKERS__`）。
- [x] **驗證**：pytest 95 綠、e2e 11 桌機+1 行動、ruff(app/) 全過；真實 API 確認（incomplete 終結事件、依據欄 0 空白列）；production 煙霧測試通過。

## 未竟（後續）

- [ ] **判決語料**：取得司法院 opendata 帳密（環境變數）後，於 00–06 時段以 `Auth→JList→JDoc` 補；建議改用裁判書查詢系統挑案號再 `JDoc` 逐則抓。
- [ ] **金鑰撤換**：`.env`/對話外洩之 `sk-svcacct-…` 與 `RAILWAY_API_TOKEN`。
- [ ] **（選）公司法函釋抽樣**：目前 ~48%（結構性，已保留全量）；如要更均衡可依時近性抽樣。

## Phase D — 判決語料 + 檢索分流抗稀釋（2026-06-11，進行中；詳 spec §K）

> 補齊 `doc_type=判決` 語料層並解決判決檢索「結構性弱勢」。完整設計、PoC 數據、參數依據見
> `docs/superpowers/specs/2026-06-09-legal-rag-7fixes-design.md` §K。memory：`judgment-corpus-pipeline`。

**已完成（本 session）**
- [x] 判決取得 pipeline：**opendata 會員月度 RAR 下載**（`fid=datasetId+12310`，跨年連續）——
      **取代** 上面「判決語料」那條原構想的「API 00–06 時段 Auth→JList→JDoc」（月檔無時段限制、可批次，務實得多）。
- [x] 精篩 `--strict`（母法 ≥3 次或在案由，排洗錢假陽性）；chunk 判決 2048/512 分流。
- [x] 抗稀釋①：`rag.py` `score_threshold=0.5`（不拉高 max_num_results）。TDD 鎖定。
- [x] 抗稀釋②：`retrieve_dual()` 兩路檢索保障判決名額（法條 top-12 + 判決獨立 top-3）。
      實證：單路判決 0 筆 → 分流 6 筆進場。TDD `test_retrieve_dual_*`（pytest 97 綠）。

**待辦（下個 session）**
- [ ] **生產串接**：`answer_question`/`astream_answer` 改走 `retrieve_dual` 自組 context；連帶
      `parse_response`（citations/evidence）、SSE 串流、前端 `.legal-pre`。屬架構級重構，需完整測試。
- [ ] **語料上傳**：近 2–3 年分層抽樣（每月 ~535 strict，目標 1–1.5 萬筆）。`data/judgments/`
      已備 ~2,800 筆原料；上傳暫停待生產整合定案（store 現僅早期 PoC 106 筆判決）。
- [ ] **（選）去 boilerplate**：剝程序套語提升判決路 score（實測僅 +2.5%，輔助性）。
