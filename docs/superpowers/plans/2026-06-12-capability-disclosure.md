# 職能揭露（capability disclosure）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 讓 agent 辨識「你能做什麼／涵蓋哪些範圍」這類 meta 問題，改用資料驅動的能力清單作答（跳過檢索），並在前端揭露職能與語料範疇（空狀態漸進揭露、offcanvas 範疇卡、缺口提示、markdown 語意配色）。

**Architecture:** 後端在 `answer_question`/`astream_answer` 入口加 intent gate（structured-output 分類 meta vs legal）；meta → `capability_answer(manifest)` 模板化作答、不檢索；legal → 既有 `retrieve_dual`。能力清單由 `scripts/build_capability_manifest.py` 查向量庫 doc_type×category 產出 JSON，經 `/api/capability` 給前端。前端沿用真實單欄+offcanvas 結構，加漸進揭露空狀態、範疇卡、缺口提示、markdown 配色。

**Tech Stack:** OpenAI Responses API（structured output `text.format.json_schema`）/ FastAPI / 原生 JS + marked + DOMPurify / pytest / Playwright。

**對應 spec:** `docs/superpowers/specs/2026-06-12-capability-disclosure-design.md`

---

## File Structure

| 檔案 | 責任 | 變更 |
|---|---|---|
| `app/capability.py` | 能力清單載入、模板化能力回答、intent 分類（sync+async） | **新增** |
| `scripts/build_capability_manifest.py` | 查向量庫產出 `capability_manifest.json` | **新增** |
| `app/capability_manifest.json` | 能力清單資料（doc_type 件數、領域、cutoff） | **新增（產生物，進 repo）** |
| `app/rag.py` | `answer_question`/`astream_answer` 加 intent gate | 修改 |
| `app/main.py` | `GET /api/capability` | 修改 |
| `static/index.html` | 空狀態(方向2)、能力回答相容、範疇卡、缺口提示、markdown 配色+行距 | 修改（**逐項過 live demo gate**） |
| `scripts/mock_server.py` | `/api/capability`、capability final、dual_retrieved final | 修改 |
| `tests/test_capability.py` | capability 後端單元測試 | **新增** |
| `tests/e2e/ask.spec.ts` | 職能揭露 e2e | 修改 |

**前端鐵則（live demo gate）：** Task 6–9 動 `static/index.html`／`mock_server.py` → **stage 不 commit → 截 PNG → 使用者親眼確認「對」→ 才 commit**。後端 Task 1–5、10 走 TDD。

---

## Task 1: 能力清單產生器 `build_capability_manifest.py`

**Files:**
- Create: `scripts/build_capability_manifest.py`
- Create: `app/capability_manifest.json`（由腳本產生）
- Test: `tests/test_capability.py`

- [ ] **Step 1: 寫失敗測試**

建立 `tests/test_capability.py`：

```python
import json
from types import SimpleNamespace
from scripts import build_capability_manifest as bcm


class _FakePage:
    def __init__(self, data, has_more=False):
        self.data = data
        self.has_more = has_more


class _FakeFiles:
    def __init__(self, files):
        self._files = files

    def list(self, vector_store_id, limit=100, after=None):
        return _FakePage(self._files, has_more=False)


class _FakeVS:
    def __init__(self, files):
        self.files = _FakeFiles(files)


class _FakeClient:
    def __init__(self, files):
        self.vector_stores = _FakeVS(files)


def _f(doc_type, category="個資", eff="2026-03-01"):
    return SimpleNamespace(id="f", attributes={"doc_type": doc_type, "category": category, "effective_date": eff})


def test_build_manifest_counts_by_doc_type_and_category():
    files = [_f("法條"), _f("法條"), _f("函釋"), _f("判決", "勞動", "2026-05-10"), _f("判決", "個資", "2024-03-01")]
    m = bcm.build_manifest(_FakeClient(files), "vs_x")
    assert m["doc_type_counts"]["法條"] == 2
    assert m["doc_type_counts"]["函釋"] == 1
    assert m["doc_type_counts"]["判決"] == 2
    assert set(m["categories"]) == {"個資", "勞動"}
    assert m["cutoff"] == "2026-05"          # 取最新 effective_date 的 YYYY-MM
    assert m["total"] == 5
```

- [ ] **Step 2: 跑測試確認 fail**

Run: `.venv/bin/python -m pytest tests/test_capability.py -k build_manifest -v`
Expected: FAIL（`ModuleNotFoundError`/`AttributeError: build_manifest`）

- [ ] **Step 3: 實作**

建立 `scripts/build_capability_manifest.py`：

```python
"""查向量庫 doc_type×category 件數與語料截止月，產出 app/capability_manifest.json。
判決上傳後重跑即更新。用法：python scripts/build_capability_manifest.py"""
import json
import os
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANIFEST_PATH = ROOT / "app" / "capability_manifest.json"


def _load_dotenv():
    env = ROOT / ".env"
    if not env.exists():
        return
    for raw in env.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def build_manifest(client, vector_store_id):
    """回 {doc_type_counts, categories, cutoff, total}。逐頁掃描向量庫檔案的 attributes。"""
    doc_type_counts = Counter()
    categories = set()
    latest = ""
    total = 0
    after = None
    while True:
        page = client.vector_stores.files.list(vector_store_id=vector_store_id, limit=100, after=after)
        for f in page.data:
            total += 1
            attrs = f.attributes or {}
            dt = attrs.get("doc_type") if isinstance(attrs, dict) else None
            if dt:
                doc_type_counts[dt] += 1
            cat = attrs.get("category") if isinstance(attrs, dict) else None
            if cat and dt == "判決":   # 領域以判決/法條皆可；此處用所有檔的 category
                categories.add(cat)
            elif cat:
                categories.add(cat)
            eff = (attrs.get("effective_date") or "") if isinstance(attrs, dict) else ""
            if eff > latest:
                latest = eff
        if not page.has_more:
            break
        after = page.data[-1].id
    return {
        "doc_type_counts": dict(doc_type_counts),
        "categories": sorted(categories),
        "cutoff": latest[:7] if latest else "",
        "total": total,
    }


def main():
    _load_dotenv()
    from openai import OpenAI
    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    m = build_manifest(client, os.environ["VECTOR_STORE_ID"])
    MANIFEST_PATH.write_text(json.dumps(m, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已寫 {MANIFEST_PATH}：{m['total']} 檔，doc_type={m['doc_type_counts']}，cutoff={m['cutoff']}")


if __name__ == "__main__":
    main()
```

確保 `tests/__init__.py`/`scripts` 可匯入：測試用 `from scripts import build_capability_manifest`，需 `scripts/__init__.py` 存在（若無則建空檔 `scripts/__init__.py`）。

- [ ] **Step 4: 跑測試確認 pass**

Run: `.venv/bin/python -m pytest tests/test_capability.py -k build_manifest -v`
Expected: PASS

- [ ] **Step 5: 產生真實 manifest（連線）+ commit**

Run: `.venv/bin/python scripts/build_capability_manifest.py`（產 `app/capability_manifest.json`）
```bash
git add scripts/build_capability_manifest.py scripts/__init__.py app/capability_manifest.json tests/test_capability.py
git commit -m "feat(capability): 向量庫產出能力清單 manifest

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2: 模板化能力回答 `capability_answer`

**Files:**
- Create: `app/capability.py`
- Test: `tests/test_capability.py`

- [ ] **Step 1: 寫失敗測試**（append 到 `tests/test_capability.py`）

```python
from app.capability import capability_answer, load_manifest

_MANIFEST = {
    "doc_type_counts": {"法條": 1163, "施行細則": 197, "函釋": 666, "FAQ": 169, "判決": 106},
    "categories": ["個資", "勞動", "公司治理", "公平交易", "營業秘密", "消費者保護", "洗錢防制"],
    "cutoff": "2026-06", "total": 2301,
}


def test_capability_answer_is_data_driven_and_structured():
    ans = capability_answer(_MANIFEST)
    assert "我能" in ans                       # 能處理段
    assert "法條 1,163" in ans or "1,163" in ans  # 數字來自 manifest（千分位）
    assert "判決" in ans
    assert "個資" in ans and "洗錢防制" in ans   # 領域全列
    assert "不能" in ans                        # 明說不能做（NN/g）
    assert "非正式法律意見" in ans              # 免責


def test_capability_answer_no_hallucinated_numbers():
    m = dict(_MANIFEST, doc_type_counts={"法條": 5})
    ans = capability_answer(m)
    assert "5" in ans
    assert "1,163" not in ans                    # 不得出現非本 manifest 的數字
```

- [ ] **Step 2: 跑測試確認 fail**

Run: `.venv/bin/python -m pytest tests/test_capability.py -k capability_answer -v`
Expected: FAIL（ImportError）

- [ ] **Step 3: 實作** — 建立 `app/capability.py`：

```python
"""職能揭露：能力清單載入、模板化能力回答、intent 分類。"""
import json
from pathlib import Path

_MANIFEST_PATH = Path(__file__).resolve().parent / "capability_manifest.json"

# doc_type 顯示順序
_DOC_ORDER = ["法條", "施行細則", "函釋", "FAQ", "判決", "裁罰"]


def load_manifest():
    """讀 capability_manifest.json；缺檔回安全空殼（不讓前端/回答崩潰）。"""
    try:
        return json.loads(_MANIFEST_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {"doc_type_counts": {}, "categories": [], "cutoff": "", "total": 0}


def _fmt_counts(counts):
    parts = []
    for dt in _DOC_ORDER:
        if dt in counts:
            n = counts[dt]
            parts.append(f"{dt} {n:,}" if dt != "判決" else f"{dt} {n:,}")
    return "・".join(parts) if parts else "（語料盤點中）"


def capability_answer(manifest):
    """由 manifest 組出固定結構 Markdown（不經 LLM、零幻覺）。"""
    counts = manifest.get("doc_type_counts", {})
    cats = "・".join(manifest.get("categories", [])) or "（領域盤點中）"
    cutoff = manifest.get("cutoff", "")
    cutoff_line = f"\n\n語料截止：{cutoff}" if cutoff else ""
    return (
        "## 我能幫你的\n"
        "條文查詢與解釋（法規名稱＋條號）、義務／罰則對照、合規要件檢核、實務見解佐證（函釋・判決）。\n\n"
        "## 涵蓋語料（皆真實官方開放資料）\n"
        f"**文件型別**：{_fmt_counts(counts)}\n\n"
        f"**法令領域**：{cats}"
        f"{cutoff_line}\n\n"
        "## 我不能做（請另尋專業）\n"
        "提供正式法律意見、代理訴訟、個案金額試算、最新即時判決。\n\n"
        "本回答為研究輔助，非正式法律意見。"
    )
```

- [ ] **Step 4: 跑測試確認 pass**

Run: `.venv/bin/python -m pytest tests/test_capability.py -k capability_answer -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/capability.py tests/test_capability.py
git commit -m "feat(capability): capability_answer 模板化能力回答（資料驅動、零幻覺）

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 3: intent 分類（sync + async）

**Files:**
- Modify: `app/capability.py`
- Test: `tests/test_capability.py`

- [ ] **Step 1: 寫失敗測試**（append）

```python
import asyncio
from app.capability import classify_intent, aclassify_intent, INTENT_INSTRUCTIONS


class _FakeResp:
    def __init__(self, text):
        self.output_text = text


class _FakeResponses:
    def __init__(self, text):
        self._text = text
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return _FakeResp(self._text)


class _FakeClient:
    def __init__(self, text):
        self.responses = _FakeResponses(text)


def test_classify_intent_meta():
    c = _FakeClient('{"intent": "meta_capability"}')
    assert classify_intent(c, "你能做什麼？", model="m") == "meta_capability"
    assert c.responses.calls[0]["text"]["format"]["type"] == "json_schema"


def test_classify_intent_legal():
    c = _FakeClient('{"intent": "legal_question"}')
    assert classify_intent(c, "個資法第6條？", model="m") == "legal_question"


def test_classify_intent_bad_json_falls_back_legal():
    c = _FakeClient("not json")
    assert classify_intent(c, "x", model="m") == "legal_question"   # 壞掉退回 legal，不退化


class _FakeAResponses:
    def __init__(self, text): self._text = text
    async def create(self, **kwargs): return _FakeResp(self._text)
class _FakeAClient:
    def __init__(self, text): self.responses = _FakeAResponses(text)


def test_aclassify_intent_meta():
    out = asyncio.run(aclassify_intent(_FakeAClient('{"intent":"meta_capability"}'), "你會什麼", model="m"))
    assert out == "meta_capability"
```

- [ ] **Step 2: 跑測試確認 fail**

Run: `.venv/bin/python -m pytest tests/test_capability.py -k classify -v`
Expected: FAIL（ImportError）

- [ ] **Step 3: 實作**（append to `app/capability.py`）

```python
INTENT_INSTRUCTIONS = (
    "判斷使用者查詢的意圖類別。\n"
    "- meta_capability：詢問本系統能做什麼、可回答哪類問題、語料涵蓋範圍、功能說明、"
    "「你是誰／你會什麼／你能幫我什麼」。\n"
    "- legal_question：實質法規問題（需要檢索條文／函釋／判決）。\n"
    "範例：『你能回答什麼？』→ meta_capability；『個資法第6條規定什麼？』→ legal_question。"
)

_INTENT_SCHEMA = {
    "type": "json_schema", "name": "intent", "strict": True,
    "schema": {"type": "object",
               "properties": {"intent": {"type": "string", "enum": ["meta_capability", "legal_question"]}},
               "required": ["intent"], "additionalProperties": False},
}


def _parse_intent(output_text):
    try:
        data = json.loads(output_text or "")
        intent = data.get("intent")
    except (json.JSONDecodeError, TypeError, AttributeError):
        return "legal_question"
    return intent if intent in ("meta_capability", "legal_question") else "legal_question"


def classify_intent(client, question, *, model):
    """同步：structured output 分類。壞掉 → legal_question（不退化既有行為）。"""
    try:
        resp = client.responses.create(model=model, instructions=INTENT_INSTRUCTIONS,
                                       input=question, text={"format": _INTENT_SCHEMA})
    except Exception:  # noqa: BLE001 分類失敗不可拖垮主問答
        return "legal_question"
    return _parse_intent(getattr(resp, "output_text", ""))


async def aclassify_intent(client, question, *, model):
    """非同步版（AsyncOpenAI）。"""
    try:
        resp = await client.responses.create(model=model, instructions=INTENT_INSTRUCTIONS,
                                              input=question, text={"format": _INTENT_SCHEMA})
    except Exception:  # noqa: BLE001
        return "legal_question"
    return _parse_intent(getattr(resp, "output_text", ""))
```

（`json` 已在檔首 import。）

- [ ] **Step 4: 跑測試確認 pass**

Run: `.venv/bin/python -m pytest tests/test_capability.py -k classify -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/capability.py tests/test_capability.py
git commit -m "feat(capability): classify_intent / aclassify_intent（structured output，壞掉退 legal）

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 4: 把 intent gate 接進 `answer_question` / `astream_answer`

**Files:**
- Modify: `app/rag.py`
- Test: `tests/test_rag.py`

- [ ] **Step 1: 寫失敗測試**（append to `tests/test_rag.py`）

```python
# ---- (M) 職能揭露 intent gate ----
from app.rag import answer_question as _aq, capability_result


def test_capability_result_shape():
    out = capability_result({"doc_type_counts": {"法條": 5}, "categories": ["個資"], "cutoff": "2026-06", "total": 5})
    assert out["evidence_mode"] == "capability"
    assert out["citations"] == [] and out["evidence"] == []
    assert "我能" in out["answer"] and "非正式法律意見" in out["answer"]
    assert out["status"] == "completed" and out["truncated"] is False


class _MetaClient:
    """classify→meta：vector_stores.search 不應被呼叫。"""
    def __init__(self):
        self.vector_stores = _FakeVS()           # 來自 Task 3 既有 fake（有 search_calls）
        self.responses = _FakeResponses(_fake_response())

    # responses.create 被 classify_intent 用：回 meta
    def _classify(self):
        pass


def test_answer_question_meta_skips_retrieval(monkeypatch):
    import app.rag as rag
    monkeypatch.setattr(rag, "classify_intent", lambda *a, **k: "meta_capability")
    client = _FakeClient(_fake_response())       # 有 vector_stores（含 search_calls）
    out = _aq(client, "你能做什麼？", vector_store_id="vs_1", model="m")
    assert out["evidence_mode"] == "capability"
    assert len(client.vector_stores.search_calls) == 0   # 完全沒檢索


def test_answer_question_legal_uses_retrieval(monkeypatch):
    import app.rag as rag
    monkeypatch.setattr(rag, "classify_intent", lambda *a, **k: "legal_question")
    client = _FakeClient(_fake_response())
    out = _aq(client, "個資法第6條？", vector_store_id="vs_1", model="m")
    assert len(client.vector_stores.search_calls) == 2   # 走雙路檢索
```

- [ ] **Step 2: 跑測試確認 fail**

Run: `.venv/bin/python -m pytest tests/test_rag.py -k "capability or meta_skips or legal_uses" -v`
Expected: FAIL（`cannot import name capability_result`）

- [ ] **Step 3: 實作** — 在 `app/rag.py` 檔首 import 後加：

```python
from app.capability import load_manifest, capability_answer, classify_intent, aclassify_intent
```

新增 helper（放在 `answer_question` 之前）：

```python
def capability_result(manifest):
    """meta 問題的回傳：與 parse_dual_response 同契約，evidence_mode=capability、無引用。"""
    return {
        "answer": capability_answer(manifest),
        "citations": [],
        "evidence": [],
        "evidence_mode": "capability",
        "response_id": None,
        "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
        "status": "completed",
        "truncated": False,
        "incomplete_reason": None,
    }
```

改 `answer_question` 開頭加 gate：

```python
def answer_question(client, question, *, vector_store_id, model, previous_response_id=None):
    if classify_intent(client, question, model=model) == "meta_capability":
        return capability_result(load_manifest())
    routes = retrieve_dual(client, question, vector_store_id=vector_store_id)
    context, sources = build_context_block(routes["law"], routes["judgment"])
    kwargs = _build_dual_kwargs(question, context, model, previous_response_id)
    response = client.responses.create(**kwargs)
    return parse_dual_response(response, sources)
```

改 `astream_answer` 開頭加 gate（meta → 先 yield 答案為單一 delta、再 final）：

```python
async def astream_answer(client, question, *, vector_store_id, model, previous_response_id=None):
    if await aclassify_intent(client, question, model=model) == "meta_capability":
        result = capability_result(load_manifest())
        yield {"type": "delta", "text": result["answer"]}
        yield {"type": "final", **result}
        return
    routes = await aretrieve_dual(client, question, vector_store_id=vector_store_id)
    context, sources = build_context_block(routes["law"], routes["judgment"])
    kwargs = _build_dual_kwargs(question, context, model, previous_response_id)
    kwargs["stream"] = True
    final_response = None
    stream = await client.responses.create(**kwargs)
    async for event in stream:
        event_type = getattr(event, "type", None) or ""
        terminal = _terminal_response(event, event_type)
        if terminal is not None:
            final_response = terminal
            continue
        delta = getattr(event, "delta", None)
        if event_type.endswith("output_text.delta") and isinstance(delta, str):
            yield {"type": "delta", "text": delta}
    if final_response is not None:
        yield {"type": "final", **parse_dual_response(final_response, sources)}
```

注意：`_FakeClient`（test）的 `responses.create` 會被 classify_intent 呼叫一次再被主流程呼叫；既有 legal 測試已 monkeypatch classify 或 fake 回傳合理值——若既有 answer_question 測試未 patch classify，會多一次 create 呼叫。**修正**：既有 `test_answer_question_*`（Task 3 段）在每個測試前 `monkeypatch.setattr(rag,"classify_intent",lambda *a,**k:"legal_question")`，確保走檢索路。逐一加上。

- [ ] **Step 4: 跑測試確認 pass + 全 rag 回歸**

Run: `.venv/bin/python -m pytest tests/test_rag.py -v`
Expected: PASS（含既有 dual 測試；必要時依上一步為既有 answer_question/stream 測試補 classify monkeypatch）

- [ ] **Step 5: Commit**

```bash
git add app/rag.py tests/test_rag.py
git commit -m "feat(rag): intent gate — meta 問題走 capability_answer、跳過 retrieve_dual

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 5: `/api/capability` 端點

**Files:**
- Modify: `app/main.py`
- Test: `tests/test_api.py`

- [ ] **Step 1: 寫失敗測試**（append to `tests/test_api.py`）

```python
def test_api_capability_returns_manifest():
    from fastapi.testclient import TestClient
    from app.main import app
    r = TestClient(app).get("/api/capability")
    assert r.status_code == 200
    body = r.json()
    assert "doc_type_counts" in body and "categories" in body and "cutoff" in body
```

- [ ] **Step 2: 跑測試確認 fail**

Run: `.venv/bin/python -m pytest tests/test_api.py -k capability -v`
Expected: FAIL（404）

- [ ] **Step 3: 實作** — 在 `app/main.py` 加（import 區加 `from app.capability import load_manifest`）：

```python
@app.get("/api/capability")
def capability():
    return load_manifest()
```

- [ ] **Step 4: 跑測試確認 pass**

Run: `.venv/bin/python -m pytest tests/test_api.py -k capability -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/main.py tests/test_api.py
git commit -m "feat(api): GET /api/capability 回能力清單

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 6: 前端 — markdown 語意配色 + 行距（live demo gate）

**Files:**
- Modify: `static/index.html`（`<style>` 內 `.answer-prose` 區）

- [ ] **Step 1: 改 CSS** — 在 `static/index.html` 的 `.answer-prose` 既有規則後，加入/調整（保留既有，補語意色與行距）：

```css
  .answer-prose{ line-height:1.65; }                          /* 本文 ≥1.5（WCAG/NN-g） */
  .answer-prose p{ margin:0 0 1.4em; }                        /* 段間趨近 2×字級 */
  .answer-prose h2{ color:var(--blood); line-height:1.3; }    /* 主結構標題：血紅 */
  .answer-prose h3{ color:var(--ink-90); line-height:1.3; }   /* 次標題：深墨 */
  .answer-prose li{ margin:.45em 0; line-height:1.6; }        /* 清單項加大 */
  .answer-prose blockquote{ line-height:1.6; margin:1.2em 0; }
  .answer-prose code{ color:var(--blood); }                   /* 條號：血紅 mono */
  /* 確定性層級三色（既有 tier-* class，補輔色） */
  .tier-interpret{ background:#2e5a52; color:#fff; }          /* 解釋/裁量：teal */
  .tier-practice{ background:#8a5a12; color:#fff; }           /* 實務見解：amber */
```
（檢查既有 `.tier`/`.tier-explicit`/`.tier-gap` 定義；`tier-explicit`=墨、`tier-gap`=灰若已存在則不動，僅補 interpret/practice 兩色。）

- [ ] **Step 2: 啟動 mock 截 PNG**

Run: 啟 `scripts/mock_server.py`，用 Playwright 開首頁問一題（既有 mock 答案含 H2/表格/tier），截 `.verify/md-color.png`。

- [ ] **Step 3: Director cold-Read PNG → 使用者確認**

把 PNG 給使用者：H2 血紅、H3 墨、tier 三色、行距是否如 mockup。**等使用者「對」。**

- [ ] **Step 4: 使用者「對」後 commit；「不對」→ `git restore static/index.html`**

```bash
git add static/index.html
git commit -m "feat(ui): markdown 語意配色 + NN/g 行距（過視覺 gate）

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 7: 前端 — 空狀態方向2（漸進揭露 tag→範例）（live demo gate）

**Files:**
- Modify: `static/index.html`（`renderStarters` 改寫 + CSS）

- [ ] **Step 1: 改 `renderStarters`** — 改為讀 `/api/capability` 渲染領域 tag，點 tag 揭露範例題。範例題對映表寫在 JS：

```javascript
const DOMAIN_EXAMPLES = {
  "個資": ["個資外洩要通報誰？期限多久？", "如何合法蒐集員工個資？", "違反個資法的罰則有哪些？"],
  "勞動": ["資遣費如何計算？", "加班費怎麼算？", "競業禁止條款有效嗎？"],
  "公司治理": ["內線交易何時可買賣股票？", "獨立董事有哪些義務？", "董事競業禁止規範？"],
  "公平交易": ["不實廣告的法律責任？", "聯合行為如何認定？"],
  "營業秘密": ["員工帶走客戶名單怎麼辦？", "營業秘密的保護要件？"],
  "消費者保護": ["定型化契約無效要件？", "網購七天鑑賞期適用範圍？"],
  "洗錢防制": ["客戶盡職調查（KYC）要做到什麼程度？", "可疑交易申報門檻？"],
};

async function renderStarters(){
  let manifest = { categories: Object.keys(DOMAIN_EXAMPLES), doc_type_counts:{}, cutoff:"" };
  try { const r = await fetch("/api/capability"); if (r.ok) manifest = await r.json(); } catch {}
  const cats = (manifest.categories && manifest.categories.length) ? manifest.categories : Object.keys(DOMAIN_EXAMPLES);
  const dc = manifest.doc_type_counts || {};
  const scope = ["法條","施行細則","函釋","FAQ","判決"].filter(k=>dc[k]).map(k=>`${k}`).join("・") || "法條・函釋・判決";
  const box = document.createElement("div");
  box.className = "py-8";
  box.innerHTML = `
    <div class="text-center">
      <div class="font-display text-[22px] font-semibold mb-1 whitespace-nowrap">企業法遵研究助理</div>
      <p class="font-body text-[14px] text-[var(--ink-70)] mb-5" style="line-height:1.75">點一個領域看範例問題，或直接輸入。涵蓋 ${scope} 等。</p>
      <div id="dom-tags" class="flex flex-wrap justify-center gap-2 max-w-xl mx-auto"></div>
      <div id="dom-reveal" class="max-w-md mx-auto mt-4 text-left"></div>
    </div>`;
  thread.appendChild(box);
  const tagsEl = box.querySelector("#dom-tags"), revealEl = box.querySelector("#dom-reveal");
  tagsEl.innerHTML = cats.map((c,i)=>`<button class="dom-tag${i===0?' on':''}" data-dom="${c}">${c}</button>`).join("");
  const showDomain = (dom)=>{
    tagsEl.querySelectorAll(".dom-tag").forEach(t=> t.classList.toggle("on", t.dataset.dom===dom));
    const qs = DOMAIN_EXAMPLES[dom] || [];
    revealEl.innerHTML = `<div class="text-[10px] font-bold uppercase tracking-wide text-[var(--blood)] mb-2">${dom} — 範例問題</div>` +
      qs.map(q=>`<button class="exq starter block w-full text-left mb-2" data-q="${q.replace(/"/g,'&quot;')}">${q}</button>`).join("");
    revealEl.querySelectorAll(".starter").forEach(b=> b.addEventListener("click", ()=> submit(b.dataset.q)));
  };
  tagsEl.querySelectorAll(".dom-tag").forEach(t=> t.addEventListener("click", ()=> showDomain(t.dataset.dom)));
  if (cats.length) showDomain(cats[0]);
  icons();
}
```

加 CSS（`<style>` 內）：

```css
  .dom-tag{ font-family:'Public Sans'; font-size:13px; font-weight:600; border:1px solid var(--ink-20);
            color:var(--ink-70); border-radius:14px; padding:6px 13px; background:var(--surface); cursor:pointer; transition:all .15s; }
  .dom-tag:hover{ border-color:#7a2e2e55; } .dom-tag.on{ border-color:var(--blood); color:var(--blood); background:#7a2e2e0d; }
  .exq{ font-family:'Spectral',serif; font-size:13px; color:var(--ink); border:1px solid #7a2e2e33; border-radius:10px;
        padding:10px 12px; background:var(--surface); box-shadow:var(--shadow-sm); cursor:pointer; transition:border-color .15s; line-height:1.5; }
  .exq:hover{ border-color:#7a2e2e66; }
```

`renderStarters` 已改為 async；確認呼叫端（初始化空狀態處）相容（`renderStarters()` 不需 await，內部自行 fetch）。

- [ ] **Step 2–4: mock 截 PNG（桌機+手機）→ 使用者確認 → commit/restore**

桌機與手機(375px viewport)各截 `.verify/empty-desktop.png`、`.verify/empty-mobile.png`（標題單行、tag 一排、點 tag 出範例）。使用者「對」→ commit；「不對」→ restore。

```bash
git add static/index.html
git commit -m "feat(ui): 空狀態漸進揭露（領域 tag→範例，方向2）（過視覺 gate）

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 8: 前端 — offcanvas「關於本助理」範疇卡（live demo gate）

**Files:**
- Modify: `static/index.html`（`renderEvidence` + state）

- [ ] **Step 1: 改 `renderEvidence`** — 無引用（`!state.lastEvidence.length`）或 `evidence_mode==="capability"` 時，顯示範疇卡（讀已快取的 manifest）。在 `renderEvidence` 開頭替換「尚無引證依據」分支：

```javascript
function renderEvidence(activeFilename){
  const noCite = !state.lastEvidence.length || state.lastEvidenceMode === "capability";
  if(noCite){
    const m = state.capabilityManifest || {};
    const dc = m.doc_type_counts || {};
    const counts = ["法條","施行細則","函釋","FAQ","判決"].filter(k=>dc[k])
      .map(k=>`<b>${k}</b> ${Number(dc[k]).toLocaleString()}`).join("　") || "語料盤點中";
    const cats = (m.categories||[]).map(c=>`<span class="pill">${c}</span>`).join("");
    ocBody.innerHTML = `
      <div class="text-[10px] font-bold uppercase tracking-wide text-[var(--blood)] mb-1">關於本助理</div>
      <div class="font-body text-[13px] text-[var(--ink-70)] mb-3" style="line-height:1.6">涵蓋範圍：${counts}　${m.cutoff?("· 判決擴充中"):""}</div>
      <div class="mb-3">${cats}</div>
      ${m.cutoff?`<div class="text-[10px] font-bold uppercase tracking-wide text-[var(--blood)] mb-1">語料截止</div>
      <div class="font-body text-[13px] text-[var(--ink-70)] mb-3">${m.cutoff}</div>`:""}
      <div class="text-[10px] font-bold uppercase tracking-wide text-[var(--blood)] mb-1">尚未涵蓋</div>
      <div class="font-body text-[13px] text-[var(--ink-70)]">早期判決、部分裁罰案例</div>`;
    return;
  }
  // …（既有 cited/retrieved 證據卡渲染不變）
```

加 `.pill` CSS：

```css
  .pill{ display:inline-block; font-family:'Public Sans'; font-size:11px; font-weight:600; border:1px solid var(--ink-20);
         color:var(--ink-70); border-radius:12px; padding:2px 9px; margin:2px 4px 2px 0; }
```

在初始化處 fetch 並快取 manifest 給範疇卡：`fetch("/api/capability").then(r=>r.json()).then(m=>state.capabilityManifest=m).catch(()=>{});`（放在 state 初始化後）。

- [ ] **Step 2–4: mock 截 PNG → 使用者確認 → commit/restore**

開 offcanvas（無引用時）截 `.verify/scope-card.png`（無 emoji、件數+領域 pills+截止+未涵蓋）。使用者「對」→ commit。

```bash
git add static/index.html
git commit -m "feat(ui): offcanvas 關於本助理 範疇卡（無引用時）（過視覺 gate）

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 9: 前端 — 缺口提示（dual_retrieved）（live demo gate）

**Files:**
- Modify: `static/index.html`（final 渲染處）

- [ ] **Step 1: 加缺口提示** — 在 final 渲染（`static/index.html` 約 356 行 `ui.answerEl.innerHTML = md(...)` 之後、截斷提示之前）插入：

```javascript
  // 缺口提示：dual_retrieved（無精準引用）→ 誠實標示超出涵蓋範圍
  if((evt.evidence_mode || "") === "dual_retrieved"){
    const g = document.createElement("p");
    g.className = "text-[12px] mb-2 px-3 py-2 rounded-lg";
    g.style.cssText = "color:var(--blood); background:#7a2e2e10; border:1px solid #7a2e2e2e; line-height:1.6";
    g.textContent = "本題超出語料直接涵蓋範圍，以下依相關度排列參考條文，未必逐句對應；建議查閱判決資料庫或諮詢法律顧問。";
    ui.answerEl.before(g);
  }
```

- [ ] **Step 2–4: mock（補 dual_retrieved final，見 Task 10）截 PNG → 使用者確認 → commit/restore**

```bash
git add static/index.html
git commit -m "feat(ui): dual_retrieved 缺口提示（過視覺 gate）

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 10: mock_server + Playwright e2e

**Files:**
- Modify: `scripts/mock_server.py`、`tests/e2e/ask.spec.ts`

- [ ] **Step 1: mock_server 加 3 樣本**

(a) `GET /api/capability` 回固定 manifest：
```python
CAPABILITY = {"doc_type_counts": {"法條":1163,"施行細則":197,"函釋":666,"FAQ":169,"判決":106},
              "categories": ["個資","勞動","公司治理","公平交易","營業秘密","消費者保護","洗錢防制"],
              "cutoff": "2026-06", "total": 2301}

@app.get("/api/capability")
def capability(): return CAPABILITY
```
(b) 問題含 `__META__` → 串流回 capability final（`evidence_mode:"capability"`、citations/evidence 空、answer 用 capability_answer 風格文字）。
(c) 問題含 `__GAP__` → final `evidence_mode:"dual_retrieved"`，answer 一段、evidence 給 1 筆。

- [ ] **Step 2: e2e 測試**（append to `tests/e2e/ask.spec.ts`，role/data-attr 定位、test.step、5x 穩定）

```typescript
test("空狀態漸進揭露：領域 tag 點擊出範例", async ({ page }) => {
  await page.goto("/");
  const tags = page.locator("#dom-tags .dom-tag");
  await expect(tags.first()).toBeVisible();
  await expect(await tags.count()).toBeGreaterThanOrEqual(5);
  await tags.nth(1).click();
  await expect(page.locator("#dom-reveal .exq").first()).toBeVisible();
});

test("能力回答：問你能做什麼，顯涵蓋語料與不能做", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("textbox").fill("你能做什麼 __META__");
  await page.getByRole("button", { name: /送出/ }).click();
  const ans = page.locator("[data-answer]").last();
  await expect(ans).toContainText("涵蓋語料");
  await expect(ans).toContainText("不能");
});

test("缺口提示：dual_retrieved 顯示超出涵蓋範圍", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("textbox").fill("冷門問題 __GAP__");
  await page.getByRole("button", { name: /送出/ }).click();
  await expect(page.getByText("本題超出語料直接涵蓋範圍")).toBeVisible();
});
```

- [ ] **Step 3: 跑 e2e（5x 穩定）**

Run: `npx playwright test -g "漸進揭露|能力回答|缺口提示"`，再全套 `npx playwright test`；5x consecutive 0 flake。

- [ ] **Step 4: Commit**（mock+e2e 屬測試基建，非生產 UI；可一般 commit）

```bash
git add scripts/mock_server.py tests/e2e/ask.spec.ts
git commit -m "test: mock_server 職能揭露樣本 + e2e（漸進揭露/能力回答/缺口提示）

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 11: 全回歸 + ruff + 文件 + code review

- [ ] **Step 1: 全測試** — `.venv/bin/python -m pytest -q`（全綠）
- [ ] **Step 2: lint** — `.venv/bin/python -m ruff check app/ scripts/`（All checks passed）
- [ ] **Step 3: e2e 全套 5x** — `npx playwright test`（0 flake）
- [ ] **Step 4: 更新 `CLAUDE.md`** — 「目前實作狀態」加一條：職能揭露（meta intent gate + 能力清單 + 漸進揭露空狀態 + 範疇卡 + markdown 語意配色）。Commit。
- [ ] **Step 5: Code review** — requesting/receiving-code-review；重點：intent 分類延遲與 fallback、capability_answer 零幻覺、前端 sanitization、契約相容、無回歸。

---

## Self-Review（plan ↔ spec 覆蓋）

- **spec §4.1 intent 分類** → Task 3 ✅（sync+async、壞掉 fallback legal）
- **spec §4.2 capability_answer 模板化資料驅動** → Task 2 ✅
- **spec §4 清單產生器/manifest** → Task 1 ✅
- **spec §4 路由接入（meta 跳檢索）** → Task 4 ✅（answer_question/astream_answer）
- **spec §4 /api/capability** → Task 5 ✅
- **spec §4.4 狀態1 空狀態方向2（tag→reveal、標題單行）** → Task 7 ✅
- **spec §4.4 狀態2 能力回答渲染** → Task 4（後端）+ 既有 md() 渲染 ✅
- **spec §4.4 狀態5 範疇卡 offcanvas 無 emoji** → Task 8 ✅
- **spec §4.3 缺口提示 dual_retrieved** → Task 9 ✅
- **spec §4.5 markdown 語意配色** → Task 6 ✅
- **spec §4.6 行距 1.65/1.75/1.4em + 8px 尺標** → Task 6（本文）+ Task 7（lede）✅
- **spec §6 完成定義** → Task 10（e2e）+ Task 11（回歸/ruff/review）✅
- **spec 測試（後端 TDD / playwright / live demo gate）** → Task 1–5/10 TDD、Task 6–9 PNG gate、Task 10 e2e ✅
- **型別一致**：`capability_result` 回傳鍵 = parse_dual_response 契約；`classify_intent`/`aclassify_intent` 簽名一致；`load_manifest`/`capability_answer`/`build_manifest` 跨 Task 一致 ✅

**已知/刻意**：判決上傳後需重跑 `build_capability_manifest.py` 更新件數（manifest 是產生物）。regex 快路徑優化列為 spec §8 後續，不在本 plan。
