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

依設計：墨黑 `#1a1714`·紙白 `#f5f2ea`·酒紅 `#7a2e2e`；Fraunces+Spectral+Public Sans（Google Fonts CDN）；Lucide（CDN，禁 emoji）；Tailwind CDN。元件：訊息氣泡（多輪，前端保存上一輪 `response_id` 於下一次 `/api/ask` 帶 `previous_response_id`）、答案數字標記 ¹²³、下方去重彙整來源列、左上 hamburger（紅點數）開左側 offcanvas（330px / 手機 84% + 遮罩）、evidence card「開啟原文全文」呼叫 `/api/source/{filename}` 抽屜內展開、suggestion chips（呼叫 `/api/suggest`；空狀態 starter chips）、loading 狀態、錯誤狀態、持久 disclaimer、ESC/遮罩關閉、RWD。

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

## 收尾

- [ ] `pytest -v` 全綠（含既有 + 新增單元 + API 測試）
- [ ] 對照 §9 成功標準逐項驗證
- [ ] 用 `workshop/proposal-template.md` 吃 spec+plan → 生成 7 步提案
