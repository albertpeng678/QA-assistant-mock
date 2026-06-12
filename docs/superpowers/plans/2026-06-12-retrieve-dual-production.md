# retrieve_dual 接進生產 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把已測試但未上線的 `retrieve_dual`（單庫雙路檢索）接進生產問答，讓判決走獨立名額、不再被法條擠光，且不刪庫內 106 筆判決。

**Architecture:** `answer_question`/`stream_answer`/`astream_answer` 改為：先雙路 `vector_stores.search`（法條 doc_type≠判決 top-12、判決 doc_type=判決 top-3）→ 自組 context 區塊 → `responses.create`（**不帶 file_search tool**，context 注入 input）→ `parse_dual_response` 由檢索結果確定性組出 citations/evidence。`parse_response` 對外輸出契約不變，故 `app/main.py` 與前端免改。

**Tech Stack:** Python 3 / OpenAI SDK 2.24（`vector_stores.search` + Responses API）/ pytest / FastAPI / Starlette SSE / Playwright。

**對應 spec:** `docs/superpowers/specs/2026-06-12-retrieve-dual-production-design.md`

---

## File Structure

| 檔案 | 責任 | 變更 |
|---|---|---|
| `app/rag.py` | RAG 核心：檢索、context 組裝、解析、問答/串流 | 新增 `_result_text`/`build_context_block`/`_linkify_source_markers`/`parse_dual_response`/`aretrieve_dual`/`_build_dual_kwargs`/`CITATION_PROTOCOL`/`_response_meta`；改 `answer_question`/`stream_answer`/`astream_answer` |
| `tests/test_rag.py` | rag 單元測試 | 新增 dual 測試；遷移 `answer_question`/串流測試的 fake client（加 `vector_stores`） |
| `scripts/mock_server.py` | 前端離線 mock | EVIDENCE 加一筆判決樣本（無 url、doc_type=判決） |
| `tests/e2e/ask.spec.ts` | 前端 e2e | 新增「判決見解證據顯示」測試 |
| `app/main.py` | FastAPI 整合 | **免改**（契約穩定）|
| `static/index.html` | 前端 | **免改**（判決無 url 走優雅降級，e2e 驗證即可）|
| `CLAUDE.md` | 導覽 | 更新狀態：retrieve_dual 已上線 |

**保留不動：** 既有 `parse_response`、`_build_answer_kwargs`、`FILE_SEARCH_RANKING_OPTIONS`（留作 fallback/回滾，且既有 parse_response 單元測試續綠）。

---

## Task 1: `build_context_block` — 自組檢索來源區塊

**Files:**
- Modify: `app/rag.py`（在 `retrieve_dual` 之後新增）
- Test: `tests/test_rag.py`（檔尾新增）

- [ ] **Step 1: 寫失敗測試**

在 `tests/test_rag.py` 檔尾加入：

```python
# ---- (J) build_context_block：自組檢索來源區塊 ----
from app.rag import build_context_block


def _r(filename, text, score, doc_type, url=None):
    attrs = {"doc_type": doc_type}
    if url:
        attrs["url"] = url
    return SimpleNamespace(filename=filename, score=score, attributes=attrs,
                           content=[SimpleNamespace(type="text", text=text)])


def test_build_context_block_sections_and_indices():
    law = [_r("勞動基準法-第24條.txt", "加班費依本條計算。", 0.82, "法條")]
    judgment = [_r("臺北地院-判決-資遣費案.txt", "本院認資遣費應…", 0.40, "判決")]
    context, sources = build_context_block(law, judgment)
    # 兩區標題都在、序號連續、各帶 filename
    assert "法規依據" in context and "判決見解" in context
    assert "[來源1]" in context and "[來源2]" in context
    assert sources[0]["filename"] == "勞動基準法-第24條.txt"
    assert sources[0]["doc_type"] == "法條"
    assert sources[1]["filename"] == "臺北地院-判決-資遣費案.txt"
    assert sources[1]["doc_type"] == "判決"
    # chunk 文字保留進 sources["text"]
    assert "加班費依本條計算。" in sources[0]["text"]


def test_build_context_block_empty_judgment_route():
    law = [_r("公司法-第23條.txt", "負責人忠實義務。", 0.7, "法條")]
    context, sources = build_context_block(law, [])
    assert "判決見解" not in context          # 空判決路不留空區塊
    assert len(sources) == 1


def test_build_context_block_preserves_url_when_present():
    law = [_r("個資法-第12條.txt", "應通知當事人。", 0.9, "法條", url="https://x")]
    _, sources = build_context_block(law, [])
    assert sources[0]["url"] == "https://x"
```

- [ ] **Step 2: 跑測試確認 fail**

Run: `.venv/bin/python -m pytest tests/test_rag.py -k build_context_block -v`
Expected: FAIL（`ImportError: cannot import name 'build_context_block'`）

- [ ] **Step 3: 實作**

在 `app/rag.py` 的 `retrieve_dual` 之後加入：

```python
def _result_text(r):
    """取一筆 vector_stores.search result 的文字：content[] 串接；無則退回 .text。"""
    content = getattr(r, "content", None)
    if isinstance(content, list):
        return "\n".join((getattr(c, "text", None) or "") for c in content).strip()
    return getattr(r, "text", None) or ""


def _result_doc_type(r):
    attrs = getattr(r, "attributes", None)
    if isinstance(attrs, dict):
        return attrs.get("doc_type")
    return getattr(attrs, "doc_type", None) if attrs else None


def build_context_block(law, judgment):
    """把雙路檢索結果組成「給模型讀」的 context 字串，並回確定性的 sources 清單。

    回 (context_str, sources)；sources 依【法規依據】→【判決見解】序號排列，
    每筆 {filename, text, score, doc_type, url?}，供 parse_dual_response 組 citations/evidence。
    """
    sources = []
    lines = []

    def add_section(title, results):
        if not results:
            return
        lines.append(f"## {title}")
        for r in results:
            idx = len(sources) + 1
            filename = getattr(r, "filename", None) or ""
            text = _result_text(r)
            entry = {
                "filename": filename,
                "text": text,
                "score": getattr(r, "score", None),
                "doc_type": _result_doc_type(r),
            }
            url = _result_url(r)
            if url:
                entry["url"] = url
            sources.append(entry)
            lines.append(f"[來源{idx}]（{filename}）\n{text}")

    add_section("法規依據", law)
    add_section("判決見解", judgment)
    return "\n\n".join(lines), sources
```

- [ ] **Step 4: 跑測試確認 pass**

Run: `.venv/bin/python -m pytest tests/test_rag.py -k build_context_block -v`
Expected: PASS（3 passed）

- [ ] **Step 5: Commit**

```bash
git add app/rag.py tests/test_rag.py
git commit -m "feat(rag): build_context_block 自組雙路檢索來源區塊

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2: `parse_dual_response` — 由檢索結果確定性組 citations/evidence

**Files:**
- Modify: `app/rag.py`（新增 `_linkify_source_markers`/`_response_meta`/`parse_dual_response`；`parse_response` 改用 `_response_meta`）
- Test: `tests/test_rag.py`

- [ ] **Step 1: 寫失敗測試**

在 `tests/test_rag.py` 檔尾加入：

```python
# ---- (K) parse_dual_response：引用/證據來自檢索結果 ----
from app.rag import parse_dual_response


def _msg(text):
    content = SimpleNamespace(text=text, annotations=[])
    msg = SimpleNamespace(type="message", content=[content])
    return SimpleNamespace(output=[msg], id="resp_dual", usage=None, status="completed",
                           incomplete_details=None)


_SOURCES = [
    {"filename": "勞動基準法-第24條.txt", "text": "加班費…", "score": 0.82, "doc_type": "法條"},
    {"filename": "臺北地院-判決-資遣費案.txt", "text": "本院認…", "score": 0.40, "doc_type": "判決"},
]


def test_parse_dual_cited_maps_markers_to_footnotes():
    resp = _msg("加班費依規定計算[來源1]，實務見解參此[來源2]。")
    out = parse_dual_response(resp, _SOURCES)
    assert out["evidence_mode"] == "dual_cited"
    assert "[^1]" in out["answer"] and "[^2]" in out["answer"]
    assert "[來源1]" not in out["answer"]
    assert out["citations"] == ["勞動基準法-第24條.txt", "臺北地院-判決-資遣費案.txt"]
    assert [e["filename"] for e in out["evidence"]] == out["citations"]
    assert out["response_id"] == "resp_dual"


def test_parse_dual_retrieved_when_no_markers():
    resp = _msg("加班費依規定計算，無標來源。")
    out = parse_dual_response(resp, _SOURCES)
    assert out["evidence_mode"] == "dual_retrieved"
    # 未標引用 → evidence 顯示全部檢索段（去重）
    assert len(out["evidence"]) == 2
    assert out["citations"] == [s["filename"] for s in _SOURCES]


def test_parse_dual_drops_out_of_range_marker():
    resp = _msg("引用越界[來源9]應安全丟棄。")
    out = parse_dual_response(resp, _SOURCES)
    assert "[^" not in out["answer"]          # 越界標記不產生錯誤腳註
    assert "[來源9]" not in out["answer"]
    assert out["evidence_mode"] == "dual_retrieved"
```

- [ ] **Step 2: 跑測試確認 fail**

Run: `.venv/bin/python -m pytest tests/test_rag.py -k parse_dual -v`
Expected: FAIL（`ImportError: cannot import name 'parse_dual_response'`）

- [ ] **Step 3: 實作**

在 `app/rag.py` 中，先把 `parse_response` 內的 usage/status 區塊抽成共用 helper。找到 `parse_response` 結尾這段：

```python
    usage_obj = getattr(response, "usage", None)
    usage = {
        "input_tokens": getattr(usage_obj, "input_tokens", 0) or 0,
        "output_tokens": getattr(usage_obj, "output_tokens", 0) or 0,
        "total_tokens": getattr(usage_obj, "total_tokens", 0) or 0,
    }
    # status="incomplete" 代表答案被截斷（多為撞 max_output_tokens）；誠實標示供前端提示，
    # 法遵工具不可讓使用者誤以為看到的是完整回答。incomplete_reason 供 server 端 telemetry。
    status = getattr(response, "status", None)
    incomplete = getattr(response, "incomplete_details", None)
    incomplete_reason = getattr(incomplete, "reason", None) if incomplete else None
    return {
        "answer": answer_text,
        "citations": citations,
        "evidence": evidence,
        "evidence_mode": evidence_mode,
        "response_id": getattr(response, "id", None),
        "usage": usage,
        "status": status,
        "truncated": status == "incomplete",
        "incomplete_reason": incomplete_reason,
    }
```

改成：

```python
    return {
        "answer": answer_text,
        "citations": citations,
        "evidence": evidence,
        "evidence_mode": evidence_mode,
        **_response_meta(response),
    }
```

並在 `parse_response` 之前（緊接 `_evidence_for_files` 之後）新增 helper：

```python
def _response_meta(response):
    """抽出 response_id / usage / status / 截斷資訊（parse_response 與 parse_dual_response 共用）。"""
    usage_obj = getattr(response, "usage", None)
    usage = {
        "input_tokens": getattr(usage_obj, "input_tokens", 0) or 0,
        "output_tokens": getattr(usage_obj, "output_tokens", 0) or 0,
        "total_tokens": getattr(usage_obj, "total_tokens", 0) or 0,
    }
    status = getattr(response, "status", None)
    incomplete = getattr(response, "incomplete_details", None)
    incomplete_reason = getattr(incomplete, "reason", None) if incomplete else None
    return {
        "response_id": getattr(response, "id", None),
        "usage": usage,
        "status": status,
        "truncated": status == "incomplete",
        "incomplete_reason": incomplete_reason,
    }
```

接著在 `parse_dual_response` 區（檔尾、`suggest_followups` 之前）新增：

```python
# dual 流引用：模型以 [來源N] 引用我們注入的來源，N 對應 build_context_block 的序號（1-based）。
_SOURCE_MARKER_RE = re.compile(r"\[來源(\d+)\]")


def _linkify_source_markers(text, n_sources):
    """把 [來源N] 轉成腳註 [^k]，回 (新文字, 依首次引用序的 0-based source index 清單)。

    N 超出 1..n_sources 範圍者安全丟棄（不產生錯誤腳註）。腳註編號依閱讀順序。
    """
    order = []
    num = {}

    def repl(m):
        k = int(m.group(1))
        if not (1 <= k <= n_sources):
            return ""
        idx = k - 1
        if idx not in num:
            order.append(idx)
            num[idx] = len(order)
        return f"[^{num[idx]}]"

    return _SOURCE_MARKER_RE.sub(repl, text), order


def parse_dual_response(response, sources):
    """dual 流的解析：citations/evidence 來自 sources（確定性），非解析 model 檢索結果。

    與 parse_response 同輸出契約。模型有標 [來源N] → dual_cited（evidence 為被引用子集）；
    未標 → dual_retrieved（顯示全部檢索段，去重）。
    """
    answer_parts = []
    for item in response.output:
        if getattr(item, "type", None) != "message":
            continue
        for content in item.content or []:
            text = getattr(content, "text", None)
            if text:
                answer_parts.append(text)
    joined = "".join(answer_parts)
    linked, cited_idx = _linkify_source_markers(joined, len(sources))

    if cited_idx:
        answer_text = linked
        evidence = [dict(sources[i]) for i in cited_idx]
        citations = [sources[i]["filename"] for i in cited_idx]
        evidence_mode = "dual_cited"
    else:
        answer_text = _strip_citation_markers(joined)
        evidence = _dedupe_evidence([dict(s) for s in sources])
        citations = [e["filename"] for e in evidence]
        evidence_mode = "dual_retrieved"

    return {
        "answer": answer_text,
        "citations": citations,
        "evidence": evidence,
        "evidence_mode": evidence_mode,
        **_response_meta(response),
    }
```

- [ ] **Step 4: 跑測試確認 pass（含既有 parse_response 回歸）**

Run: `.venv/bin/python -m pytest tests/test_rag.py -k "parse_dual or parse_response" -v`
Expected: PASS（dual 3 新測試 + 既有 parse_response 測試全綠）

- [ ] **Step 5: Commit**

```bash
git add app/rag.py tests/test_rag.py
git commit -m "feat(rag): parse_dual_response 由檢索結果確定性組 citations/evidence

抽 _response_meta 供 parse_response/parse_dual_response 共用。

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 3: 接 `answer_question`（同步）走雙路

**Files:**
- Modify: `app/rag.py`（新增 `CITATION_PROTOCOL`/`_build_dual_kwargs`；改 `answer_question`）
- Test: `tests/test_rag.py`（遷移既有 answer_question 測試的 fake client）

- [ ] **Step 1: 寫/遷移失敗測試**

既有 `_FakeClient`（約 tests/test_rag.py:33）只有 `responses`。改造它與相關測試，使其支援 `vector_stores.search`。把 `tests/test_rag.py:33-49` 的 `_FakeClient` 與 `test_answer_question_calls_file_search_with_vector_store` 改為：

```python
class _FakeVS:
    def __init__(self):
        self.search_calls = []

    def search(self, **kwargs):
        self.search_calls.append(kwargs)
        is_judgment = (kwargs.get("filters") or {}).get("type") == "eq"
        dt = "判決" if is_judgment else "法條"
        fn = "臺北地院-判決-案.txt" if is_judgment else "勞動基準法-第24條.txt"
        data = [SimpleNamespace(filename=fn, score=0.6, attributes={"doc_type": dt},
                                content=[SimpleNamespace(type="text", text=f"{dt}內容")])]
        return SimpleNamespace(data=data)


class _FakeClient:
    def __init__(self, response):
        self.responses = _FakeResponses(response)
        self.vector_stores = _FakeVS()


def test_answer_question_uses_dual_retrieval_no_file_search_tool():
    client = _FakeClient(_fake_response())
    result = answer_question(client, "加班費怎麼算?", vector_store_id="vs_123", model="gpt-4o-mini")
    # 兩路檢索都被呼叫，且帶正確 doc_type filter
    assert len(client.vector_stores.search_calls) == 2
    assert client.vector_stores.search_calls[0]["filters"] == {"type": "ne", "key": "doc_type", "value": "判決"}
    assert client.vector_stores.search_calls[1]["filters"] == {"type": "eq", "key": "doc_type", "value": "判決"}
    call = client.responses.calls[0]
    assert call["model"] == "gpt-4o-mini"
    assert "tools" not in call                       # 不再帶 file_search tool
    assert "加班費怎麼算?" in call["input"]
    assert "檢索到的依據" in call["input"]            # context 已注入 input
    assert result["evidence_mode"] in ("dual_cited", "dual_retrieved")
```

刪除已不適用的 `test_answer_question_includes_search_results`（斷言 `include=file_search_call.results`，dual 流不再帶 include）—— 在 tests/test_rag.py 中刪掉該函式（約 95-98 行）。
`test_answer_question_chains_previous_response_id` 與 `test_answer_question_omits_previous_response_id_when_none` 因 `_FakeClient` 已升級，會自動相容，保留不動。

- [ ] **Step 2: 跑測試確認 fail**

Run: `.venv/bin/python -m pytest tests/test_rag.py -k "answer_question" -v`
Expected: FAIL（`test_answer_question_uses_dual_retrieval_no_file_search_tool`：目前 answer_question 仍帶 tools/走 file_search，斷言不成立）

- [ ] **Step 3: 實作**

在 `app/rag.py` 的 `ANSWER_INSTRUCTIONS` 之後新增引用規約常數：

```python
# dual 流的引用規約（附加在 instructions 後）：模型讀注入的 [來源N] 並回標 [來源N]。
CITATION_PROTOCOL = (
    "\n\n【引用方式】下方 input 會附「# 檢索到的依據」，內含【法規依據】與【判決見解】"
    "兩區、各段以 [來源N] 標號。作答時，請在每個論點對應的句末標註其依據的 [來源N]"
    "（可多個，如 [來源1][來源3]）。只依這些來源作答；若來源不足以回答，明確聲明語料未涵蓋，不得杜撰。"
)
```

把 `answer_question`（約 app/rag.py:314）整個函式改為：

```python
def answer_question(client, question, *, vector_store_id, model, previous_response_id=None):
    """雙路檢索 → 自組 context → Responses API（不帶 file_search tool）→ parse_dual_response。

    回傳 {"answer","citations","evidence","evidence_mode","response_id","usage",...}。
    previous_response_id 可串接多輪對話。
    """
    routes = retrieve_dual(client, question, vector_store_id=vector_store_id)
    context, sources = build_context_block(routes["law"], routes["judgment"])
    kwargs = _build_dual_kwargs(question, context, model, previous_response_id)
    response = client.responses.create(**kwargs)
    return parse_dual_response(response, sources)
```

把既有 `_build_answer_kwargs` 保留不動，於其後新增 `_build_dual_kwargs`：

```python
def _build_dual_kwargs(question, context, model, previous_response_id):
    """組 dual 流的 responses.create kwargs：context 注入 input、不帶 file_search tool。"""
    user_input = question if not context else f"{question}\n\n# 檢索到的依據\n{context}"
    kwargs = {
        "model": model,
        "input": user_input,
        "instructions": ANSWER_INSTRUCTIONS + CITATION_PROTOCOL,
        **_MODEL_PARAMS,
    }
    if previous_response_id:
        kwargs["previous_response_id"] = previous_response_id
    return kwargs
```

- [ ] **Step 4: 跑測試確認 pass**

Run: `.venv/bin/python -m pytest tests/test_rag.py -k "answer_question" -v`
Expected: PASS（含 previous_response_id 兩測試）

- [ ] **Step 5: Commit**

```bash
git add app/rag.py tests/test_rag.py
git commit -m "feat(rag): answer_question 接雙路檢索（移除 file_search tool）

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 4: 接 `stream_answer` / `astream_answer`（串流）走雙路 + `aretrieve_dual`

**Files:**
- Modify: `app/rag.py`（新增 `aretrieve_dual`；改 `stream_answer`/`astream_answer`）
- Test: `tests/test_rag.py`（串流測試 fake client 升級）

- [ ] **Step 1: 寫失敗測試**

先看既有串流測試的 fake（tests/test_rag.py:185 一帶用 `_FakeClient([delta1, delta2, completed])`——它把 list 當 stream）。新增 dual 串流測試於檔尾：

```python
# ---- (L) 串流走雙路：先檢索組 context，再串流；final 由 parse_dual_response 組 ----
import asyncio as _asyncio
from app.rag import astream_answer as _astream, aretrieve_dual


class _FakeAsyncVS:
    def __init__(self):
        self.search_calls = []

    async def search(self, **kwargs):
        self.search_calls.append(kwargs)
        is_j = (kwargs.get("filters") or {}).get("type") == "eq"
        dt = "判決" if is_j else "法條"
        fn = "臺北地院-判決-案.txt" if is_j else "勞動基準法-第24條.txt"
        return SimpleNamespace(data=[SimpleNamespace(
            filename=fn, score=0.6, attributes={"doc_type": dt},
            content=[SimpleNamespace(type="text", text=f"{dt}內容")])])


class _FakeAsyncStream:
    """async-iterable，吐 delta 事件 + 終結事件。"""
    def __init__(self, events):
        self._events = events

    def __aiter__(self):
        async def gen():
            for e in self._events:
                yield e
        return gen()


class _FakeAsyncResponses:
    def __init__(self, events):
        self._events = events
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return _FakeAsyncStream(self._events)


class _FakeAsyncClient:
    def __init__(self, events):
        self.responses = _FakeAsyncResponses(events)
        self.vector_stores = _FakeAsyncVS()


def test_astream_dual_retrieves_then_streams_and_finalizes():
    delta = SimpleNamespace(type="response.output_text.delta", delta="加班費[來源1]")
    completed_resp = SimpleNamespace(
        output=[SimpleNamespace(type="message",
                                content=[SimpleNamespace(text="加班費[來源1]", annotations=[])])],
        id="resp_s", usage=None, status="completed", incomplete_details=None)
    completed = SimpleNamespace(type="response.completed", response=completed_resp)
    client = _FakeAsyncClient([delta, completed])

    async def run():
        items = []
        async for it in _astream(client, "加班費?", vector_store_id="vs_1", model="m"):
            items.append(it)
        return items

    items = _asyncio.run(run())
    # 串流前已做兩路檢索
    assert len(client.vector_stores.search_calls) == 2
    # responses.create 不帶 file_search tool、context 已注入
    assert "tools" not in client.responses.calls[0]
    assert "檢索到的依據" in client.responses.calls[0]["input"]
    # 有 delta、有 final，且 final 由 parse_dual_response 組（[來源1]→[^1]）
    assert any(i["type"] == "delta" for i in items)
    final = [i for i in items if i["type"] == "final"][0]
    assert "[^1]" in final["answer"]
    assert final["evidence_mode"] == "dual_cited"
```

- [ ] **Step 2: 跑測試確認 fail**

Run: `.venv/bin/python -m pytest tests/test_rag.py -k "astream_dual or aretrieve" -v`
Expected: FAIL（`ImportError: cannot import name 'aretrieve_dual'`）

- [ ] **Step 3: 實作**

在 `app/rag.py` 的 `retrieve_dual` 之後新增 async 版：

```python
async def aretrieve_dual(client, query, *, vector_store_id,
                         law_k=LAW_TOP_K, law_threshold=DUAL_LAW_THRESHOLD,
                         judgment_m=JUDGMENT_TOP_M, judgment_threshold=DUAL_JUDGMENT_THRESHOLD):
    """retrieve_dual 的 async 版（AsyncOpenAI 用）：await 兩路 vector_stores.search。"""
    law = await client.vector_stores.search(
        vector_store_id=vector_store_id, query=query, max_num_results=law_k,
        filters={"type": "ne", "key": "doc_type", "value": "判決"},
        ranking_options={"score_threshold": law_threshold})
    judgment = await client.vector_stores.search(
        vector_store_id=vector_store_id, query=query, max_num_results=judgment_m,
        filters={"type": "eq", "key": "doc_type", "value": "判決"},
        ranking_options={"score_threshold": judgment_threshold})
    return {"law": law.data, "judgment": judgment.data}
```

把 `stream_answer`（約 app/rag.py:344）改為：

```python
def stream_answer(client, question, *, vector_store_id, model, previous_response_id=None):
    """SSE 串流問答（同步）：雙路檢索 → 串流生成 → final 由 parse_dual_response 組。"""
    routes = retrieve_dual(client, question, vector_store_id=vector_store_id)
    context, sources = build_context_block(routes["law"], routes["judgment"])
    kwargs = _build_dual_kwargs(question, context, model, previous_response_id)
    kwargs["stream"] = True
    final_response = None
    for event in client.responses.create(**kwargs):
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

把 `astream_answer`（約 app/rag.py:367）改為：

```python
async def astream_answer(client, question, *, vector_store_id, model, previous_response_id=None):
    """SSE 串流問答（非阻塞 async）：先 await 雙路檢索組 context，再 async 串流生成。"""
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

- [ ] **Step 4: 跑測試確認 pass**

Run: `.venv/bin/python -m pytest tests/test_rag.py -k "astream or stream or aretrieve" -v`
Expected: PASS（既有同步串流測試若仍用舊 `_FakeClient`，因其已升級含 `vector_stores`，應續綠；若某既有串流測試斷言 `tools`/`include`，一併比照 Task 3 移除該斷言）

- [ ] **Step 5: 全 rag 測試回歸**

Run: `.venv/bin/python -m pytest tests/test_rag.py -v`
Expected: PASS（全綠）

- [ ] **Step 6: Commit**

```bash
git add app/rag.py tests/test_rag.py
git commit -m "feat(rag): stream/astream 接雙路檢索 + aretrieve_dual（async）

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 5: `mock_server.py` 加判決證據樣本

**Files:**
- Modify: `scripts/mock_server.py`（EVIDENCE 加一筆判決）

- [ ] **Step 1: 改 mock 資料**

在 `scripts/mock_server.py` 的 `EVIDENCE` list（約 32-42 行）尾端，第 48 條那筆之後加入一筆判決（**無 url**、含 doc_type）：

```python
    {"filename": "臺北地院-判決-個資外洩損害賠償案.txt",
     "text": "本院認被告未於知悉外洩後即時通知當事人，違反個人資料保護法第12條通知義務，"
             "應負損害賠償責任。惟原告請求之金額，審酌其實際損害…",
     "score": 0.41, "doc_type": "判決"},
```

並把答案 `ANSWER`（約 13-30 行）結尾、「本回答為研究輔助」之前，加入一段帶判決腳註的實務見解，使 `[^4]` 有對應來源：

```python
    "【實務見解】臺北地院曾就未即時通知認定須負損害賠償責任[^4]。\n\n"
```

（`CITES`/`evidence_mode` 維持；此判決樣本驗證前端【判決見解】證據卡在「無 url」時走優雅降級顯示 snippet。）

- [ ] **Step 2: 啟動 mock 手動驗證**

Run: `.venv/bin/python scripts/mock_server.py` 背景啟動後，`curl -s -X POST localhost:8001/api/ask/stream -H 'content-type: application/json' -d '{"question":"個資外洩要通知嗎"}' | grep -o '判決-個資外洩損害賠償案'`
Expected: 命中字串（final payload 含判決證據）。驗畢關閉 mock。

- [ ] **Step 3: Commit**

```bash
git add scripts/mock_server.py
git commit -m "test(mock): EVIDENCE 加判決樣本（無 url，驗判決見解渲染）

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 6: Playwright e2e — 判決見解證據顯示

**Files:**
- Modify: `tests/e2e/ask.spec.ts`（新增一個 test）

- [ ] **Step 1: 寫 e2e 測試**

在 `tests/e2e/ask.spec.ts` 既有 describe 區塊內新增（沿用該檔既有 page 開啟/送問樣式；以 data 屬性/role 定位，禁座標）：

```typescript
  test("判決見解證據顯示（無 url 走優雅降級顯示 snippet）", async ({ page }) => {
    await page.goto("/");
    await page.getByRole("textbox").fill("個資外洩要通知嗎");
    await page.getByRole("button", { name: /送出|提問|問/ }).click();

    // 等 final 後證據面板出現判決那筆
    const judgmentCard = page.locator('[data-filename*="判決-個資外洩損害賠償案"]').first();
    await judgmentCard.waitFor({ state: "visible", timeout: 15000 });

    // 判決卡顯示 snippet 文字（來自檢索 text，非外連）
    await expect(judgmentCard).toContainText("通知義務");
    // 無 url → 應為「展開全文」按鈕，而非外連 <a>
    await expect(judgmentCard.locator("a.open-link")).toHaveCount(0);
  });
```

- [ ] **Step 2: 跑 e2e（對 mock_server:8001）**

Run（先確保 `npx playwright install chromium` 已裝）：`npx playwright test -g "判決見解證據顯示"`
Expected: PASS（1 passed）。若定位器與既有前端 class 不符，依 `static/index.html:447-464` 的實際 class（`evi-card`/`open-link`/`open-full`）調整 selector 後再跑。

- [ ] **Step 3: 全 e2e 回歸**

Run: `npx playwright test`
Expected: 全綠（既有 + 新測試）。

- [ ] **Step 4: Commit**

```bash
git add tests/e2e/ask.spec.ts
git commit -m "test(e2e): 判決見解證據卡顯示（無 url 優雅降級）

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 7: 全回歸 + ruff + 文件更新 + code review

**Files:**
- Modify: `CLAUDE.md`（狀態段）

- [ ] **Step 1: 全測試回歸**

Run: `.venv/bin/python -m pytest -q`
Expected: 全綠（原 98 + 新增 dual 測試 − 移除的 file_search 專屬斷言測試）。記錄實際 pass 數。

- [ ] **Step 2: lint**

Run: `.venv/bin/python -m ruff check app/ scripts/`
Expected: All checks passed。

- [ ] **Step 3: 更新 CLAUDE.md 狀態**

在 `CLAUDE.md` 的「目前實作狀態」段，把「retrieve_dual 已測試但尚未串接進生產」相關描述更新為「已接進生產：answer_question/astream_answer 走 retrieve_dual 雙路檢索 + 自組 context，parse_dual_response 確定性引用」。

- [ ] **Step 4: Commit 文件**

```bash
git add CLAUDE.md
git commit -m "docs: CLAUDE.md 更新 retrieve_dual 已上線

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

- [ ] **Step 5: Code review**

用 superpowers:requesting-code-review 對本分支自起點以來的 diff 發起審查；再用 superpowers:receiving-code-review 處理回饋。重點檢查：parse_dual_response 引用對映、串流不阻塞 event loop、契約 key 與 main.py/前端相容、無回歸、無杜撰來源風險。

---

## Self-Review（plan ↔ spec 覆蓋）

- **spec §4 架構（雙路→context→responses 無 tool→parse_dual）** → Task 1/2/3/4 ✅
- **spec §5 元件（build_context_block / parse_dual_response / aretrieve_dual / _build_dual_kwargs / main.py 免改 / 前端免改）** → Task 1/2/3/4 + Task 6 驗證前端 ✅
- **spec §5 引用規約 [來源N]→[^N]、evidence_mode dual_cited/dual_retrieved** → Task 2/3（CITATION_PROTOCOL） ✅
- **spec §3 參數 law_k=12/judgment_m=3** → 沿用既有 `retrieve_dual` 預設常數，未改動 ✅
- **spec §6 測試（TDD mock 改 vector_stores.search、playwright、回歸、ruff、code review）** → Task 3/4（mock 升級）、Task 6（playwright）、Task 7（回歸/ruff/review） ✅
- **spec §8 完成定義 1-6** → Task 7 Step1/2（全綠+lint）、Task 6（判決證據顯示+串流）、Task 4（純法條題判決路空不報錯，由 test_build_context_block_empty_judgment_route + retrieve_dual 既有空路測試覆蓋） ✅
- **spec §2 非目標（不刪檔/不分庫/不動 Railway）** → 全 plan 無刪檔/無新建 store/無動 env ✅
- **型別一致性**：`build_context_block` 回 `(context, sources)`、`sources` 為 `[{filename,text,score,doc_type,url?}]`，於 Task 2/3/4 一致引用；`parse_dual_response(response, sources)` 簽名一致；`aretrieve_dual` 與 `retrieve_dual` 回傳形狀一致（`{"law","judgment"}`）✅

**未列入（已知、刻意）：** 判決官方 url 由 ref_no 構造為 spec §7 選配增強，不在本 plan；子專案 B（爬蟲擴充）另開 plan。
