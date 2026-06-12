from types import SimpleNamespace
from app.rag import parse_response

def _fake_response():
    annotation = SimpleNamespace(filename="勞動基準法-第24條.txt")
    content = SimpleNamespace(text="加班費依第24條計算。", annotations=[annotation])
    message = SimpleNamespace(type="message", content=[content])
    file_search_call = SimpleNamespace(type="file_search_call", content=None)
    return SimpleNamespace(output=[file_search_call, message])

def test_parse_response_extracts_answer_and_citations():
    result = parse_response(_fake_response())
    assert result["answer"] == "加班費依第24條計算。"
    assert result["citations"] == ["勞動基準法-第24條.txt"]

def test_parse_response_dedupes_and_handles_no_annotations():
    content = SimpleNamespace(text="無引用的回答。", annotations=[])
    message = SimpleNamespace(type="message", content=[content])
    result = parse_response(SimpleNamespace(output=[message]))
    assert result["answer"] == "無引用的回答。"
    assert result["citations"] == []

from app.rag import answer_question

class _FakeResponses:
    def __init__(self, response):
        self._response = response
        self.calls = []
    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self._response

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


def test_build_dual_kwargs_empty_context_omits_citation_protocol():
    from app.rag import _build_dual_kwargs, ANSWER_INSTRUCTIONS, CITATION_PROTOCOL
    empty = _build_dual_kwargs("問題", "", "m", None)
    assert empty["input"] == "問題"
    assert empty["instructions"] == ANSWER_INSTRUCTIONS          # 無引用規約
    full = _build_dual_kwargs("問題", "## 法規依據\n[來源1]內容", "m", None)
    assert "檢索到的依據" in full["input"]
    assert full["instructions"] == ANSWER_INSTRUCTIONS + CITATION_PROTOCOL


def test_answer_question_uses_dual_retrieval_no_file_search_tool(monkeypatch):
    import app.rag as rag
    monkeypatch.setattr(rag, "classify_intent", lambda *a, **k: "legal_question")
    client = _FakeClient(_fake_response())
    result = answer_question(client, "加班費怎麼算?", vector_store_id="vs_123", model="gpt-4o-mini")
    assert len(client.vector_stores.search_calls) == 2
    assert client.vector_stores.search_calls[0]["filters"] == {"type": "ne", "key": "doc_type", "value": "判決"}
    assert client.vector_stores.search_calls[1]["filters"] == {"type": "eq", "key": "doc_type", "value": "判決"}
    call = client.responses.calls[0]
    assert call["model"] == "gpt-4o-mini"
    assert "tools" not in call
    assert "加班費怎麼算?" in call["input"]
    assert "檢索到的依據" in call["input"]
    assert result["evidence_mode"] in ("dual_cited", "dual_retrieved")
    assert result["answer"] == "加班費依第24條計算。"

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
    result = parse_response(_fake_response())
    assert result["evidence"] == []
    assert result["response_id"] is None


def test_parse_response_strips_citation_markers():
    # gpt-5.4-mini 把 inline 引用標記（私用區字元 U+E200–U+E20F + turnXfileY token）塞進 text，
    # 這些不可漏進答案文字。
    marked_text = "加班費依第24條計算。turn0file1"
    content = SimpleNamespace(text=marked_text, annotations=[])
    message = SimpleNamespace(type="message", content=[content])
    result = parse_response(SimpleNamespace(output=[message]))
    assert result["answer"] == "加班費依第24條計算。"
    assert "" not in result["answer"]
    assert "turn0file1" not in result["answer"]


def test_parse_response_citations_fallback_to_evidence():
    # annotations 為空（gpt-5.4-mini 的實測情形），citations 應 fallback 到 evidence 檔名（去重保序）。
    content = SimpleNamespace(text="應通知當事人。", annotations=[])
    message = SimpleNamespace(type="message", content=[content])
    r1 = SimpleNamespace(filename="個資法-第12條.txt", text="…通知當事人。", score=0.91)
    r2 = SimpleNamespace(filename="個資法-第6條.txt", text="…蒐集處理。", score=0.80)
    r3 = SimpleNamespace(filename="個資法-第12條.txt", text="…重複檔。", score=0.70)
    fs_call = SimpleNamespace(type="file_search_call", content=None, results=[r1, r2, r3])
    result = parse_response(SimpleNamespace(output=[fs_call, message]))
    assert result["citations"] == ["個資法-第12條.txt", "個資法-第6條.txt"]

def test_answer_question_chains_previous_response_id(monkeypatch):
    import app.rag as rag
    monkeypatch.setattr(rag, "classify_intent", lambda *a, **k: "legal_question")
    client = _FakeClient(_fake_response_with_results())
    answer_question(client, "追問", vector_store_id="vs_1", model="gpt-4o-mini", previous_response_id="resp_abc")
    assert client.responses.calls[0]["previous_response_id"] == "resp_abc"

def test_answer_question_omits_previous_response_id_when_none(monkeypatch):
    import app.rag as rag
    monkeypatch.setattr(rag, "classify_intent", lambda *a, **k: "legal_question")
    client = _FakeClient(_fake_response_with_results())
    answer_question(client, "首問", vector_store_id="vs_1", model="gpt-4o-mini")
    assert "previous_response_id" not in client.responses.calls[0]

import json as _json
from app.rag import suggest_followups

class _FakeStructResponses:
    def __init__(self, payload):
        self._payload = payload
        self.calls = []
    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(output_text=_json.dumps(self._payload))

class _FakeStructClient:
    def __init__(self, payload):
        self.responses = _FakeStructResponses(payload)

def test_suggest_followups_returns_questions():
    client = _FakeStructClient({"questions": ["通報期限？", "罰鍰範圍？", "民事責任？"]})
    out = suggest_followups(client, "個資外洩?", "應通知當事人。", model="gpt-5.4-mini")
    assert out["questions"] == ["通報期限？", "罰鍰範圍？", "民事責任？"]
    call = client.responses.calls[0]
    assert call["text"]["format"]["type"] == "json_schema"


# ---- (B) answer_question 套用 gpt-5.4-mini 推理模型參數 + markdown 指示 ----
from app.rag import ANSWER_INSTRUCTIONS, CITATION_PROTOCOL, _MODEL_PARAMS


def test_answer_question_applies_reasoning_model_params(monkeypatch):
    import app.rag as rag
    monkeypatch.setattr(rag, "classify_intent", lambda *a, **k: "legal_question")
    client = _FakeClient(_fake_response_with_results())
    answer_question(client, "加班費?", vector_store_id="vs_1", model="gpt-5.4-mini")
    call = client.responses.calls[0]
    assert call["reasoning"] == {"effort": "low"}
    assert call["text"]["verbosity"] == "low"
    assert call["max_output_tokens"] == 4096  # 推理模型需更多輸出預算，避免 incomplete 截斷
    assert call["instructions"] == ANSWER_INSTRUCTIONS + CITATION_PROTOCOL
    # 推理模型不可帶 temperature/top_p
    assert "temperature" not in call
    assert "top_p" not in call


def test_suggest_followups_text_format_unchanged():
    # 確保 suggest_followups 的 text.format 沒被 _MODEL_PARAMS 污染
    client = _FakeStructClient({"questions": ["a", "b", "c"]})
    suggest_followups(client, "q", "a", model="gpt-5.4-mini")
    call = client.responses.calls[0]
    assert call["text"]["format"]["type"] == "json_schema"
    assert "verbosity" not in call["text"]


# ---- (C) parse_response 擷取 usage ----
def _fake_response_with_usage():
    content = SimpleNamespace(text="ok", annotations=[])
    message = SimpleNamespace(type="message", content=[content])
    usage = SimpleNamespace(input_tokens=10, output_tokens=20, total_tokens=30)
    return SimpleNamespace(output=[message], id="resp_u", usage=usage)


def test_parse_response_extracts_usage():
    result = parse_response(_fake_response_with_usage())
    assert result["usage"] == {"input_tokens": 10, "output_tokens": 20, "total_tokens": 30}


def test_parse_response_usage_zero_when_missing():
    result = parse_response(_fake_response())
    assert result["usage"] == {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}


# ---- (D) stream_answer SSE generator ----
from app.rag import stream_answer


def test_stream_answer_yields_deltas_then_final():
    delta1 = SimpleNamespace(type="response.output_text.delta", delta="部分")
    delta2 = SimpleNamespace(type="response.output_text.delta", delta="答案")
    completed = SimpleNamespace(type="response.completed", response=_fake_response_with_results())
    client = _FakeClient([delta1, delta2, completed])

    events = list(stream_answer(client, "個資外洩?", vector_store_id="vs_1", model="gpt-5.4-mini"))

    assert events[0] == {"type": "delta", "text": "部分"}
    assert events[1] == {"type": "delta", "text": "答案"}
    final = events[2]
    assert final["type"] == "final"
    # 雙路後 citations 來自 sources（_FakeVS 回傳）：法條路 + 判決路各一筆
    assert final["citations"] == ["勞動基準法-第24條.txt", "臺北地院-判決-案.txt"]
    assert final["evidence"][0]["score"] == 0.6
    assert final["response_id"] == "resp_abc"
    # 串流啟用且套用推理參數
    call = client.responses.calls[0]
    assert call["stream"] is True
    assert call["reasoning"] == {"effort": "low"}


def test_stream_answer_skips_reasoning_deltas():
    # 推理模型 gpt-5.4-mini 會送出 reasoning delta 事件，這些思考鏈不可洩漏進答案串流。
    reasoning = SimpleNamespace(type="response.reasoning_summary_text.delta", delta="思考中…")
    answer = SimpleNamespace(type="response.output_text.delta", delta="正式答案")
    completed = SimpleNamespace(type="response.completed", response=_fake_response_with_results())
    client = _FakeClient([reasoning, answer, completed])

    events = list(stream_answer(client, "個資外洩?", vector_store_id="vs_1", model="gpt-5.4-mini"))

    deltas = [e for e in events if e["type"] == "delta"]
    assert deltas == [{"type": "delta", "text": "正式答案"}]
    assert "思考中…" not in [d["text"] for d in deltas]
    assert events[-1]["type"] == "final"


# ---- (E) 缺口④/⑥：evidence 只留真正被引用的段落 + 帶官方原文 url ----
def test_parse_response_filters_evidence_to_cited_files():
    # annotations 指明只引用 A 檔；檢索結果含 A、B → evidence 僅保留 A，且標 mode=cited。
    ann = SimpleNamespace(type="file_citation", file_id="file-A", filename="個人資料保護法-第12條.txt")
    content = SimpleNamespace(text="應通知當事人。", annotations=[ann])
    message = SimpleNamespace(type="message", content=[content])
    rA = SimpleNamespace(file_id="file-A", filename="個人資料保護法-第12條.txt", text="A 段", score=0.91,
                         attributes={"url": "https://law.moj.gov.tw/A"})
    rB = SimpleNamespace(file_id="file-B", filename="個人資料保護法-第22條.txt", text="B 段", score=0.88,
                         attributes={"url": "https://law.moj.gov.tw/B"})
    fs = SimpleNamespace(type="file_search_call", content=None, results=[rA, rB])
    result = parse_response(SimpleNamespace(output=[fs, message], id="r1"))
    assert [e["filename"] for e in result["evidence"]] == ["個人資料保護法-第12條.txt"]
    assert result["evidence_mode"] == "cited"
    assert result["evidence"][0]["url"] == "https://law.moj.gov.tw/A"


def test_parse_response_evidence_url_omitted_when_no_attributes():
    # 既有契約：無 attributes 的 result，evidence dict 不含 url 鍵（向後相容、不污染既有測試）。
    result = parse_response(_fake_response_with_results())
    assert "url" not in result["evidence"][0]


def test_parse_response_retrieved_mode_limits_and_sorts_by_score():
    # annotations 空時退回 retrieved：依分數取 top-5，且 citations（來源列）與顯示一致，
    # 避免「把全部檢索段落都倒出來」（缺口④的實務情形：gpt-5.4-mini annotations 常空）。
    content = SimpleNamespace(text="回答。", annotations=[])
    message = SimpleNamespace(type="message", content=[content])
    scores = [0.50, 0.95, 0.60, 0.80, 0.40, 0.70, 0.90]
    results = [SimpleNamespace(file_id=f"f{i}", filename=f"法-第{i}條.txt", text=f"段{i}", score=s)
               for i, s in enumerate(scores, start=1)]
    fs = SimpleNamespace(type="file_search_call", content=None, results=results)
    result = parse_response(SimpleNamespace(output=[fs, message]))
    assert result["evidence_mode"] == "retrieved"
    assert len(result["evidence"]) == 5
    got = [e["score"] for e in result["evidence"]]
    assert got == sorted(got, reverse=True)
    assert result["citations"] == [e["filename"] for e in result["evidence"]]


def test_parse_response_evidence_mode_retrieved_and_dedup_when_no_annotations():
    # annotations 空（gpt-5.4-mini 常見）→ mode=retrieved，且同檔多 chunk 去重保留最高分。
    content = SimpleNamespace(text="回答。", annotations=[])
    message = SimpleNamespace(type="message", content=[content])
    r1 = SimpleNamespace(file_id="f1", filename="個人資料保護法-第12條.txt", text="高分段", score=0.91)
    r2 = SimpleNamespace(file_id="f2", filename="個人資料保護法-第6條.txt", text="次段", score=0.80)
    r3 = SimpleNamespace(file_id="f1", filename="個人資料保護法-第12條.txt", text="低分重複段", score=0.70)
    fs = SimpleNamespace(type="file_search_call", content=None, results=[r1, r2, r3])
    result = parse_response(SimpleNamespace(output=[fs, message]))
    assert result["evidence_mode"] == "retrieved"
    assert [e["filename"] for e in result["evidence"]] == ["個人資料保護法-第12條.txt", "個人資料保護法-第6條.txt"]
    assert result["evidence"][0]["text"] == "高分段"  # 同檔保留最高分 chunk


# ---- (F) 缺口①：astream_answer 以 AsyncOpenAI 非阻塞逐字串流 ----
import asyncio
from app.rag import astream_answer


class _FakeAsyncStream:
    def __init__(self, events):
        self._events = list(events)
        self._i = 0

    def __aiter__(self):
        self._i = 0
        return self

    async def __anext__(self):
        if self._i >= len(self._events):
            raise StopAsyncIteration
        ev = self._events[self._i]
        self._i += 1
        return ev


class _FakeAsyncResponses:
    def __init__(self, events):
        self._events = events
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return _FakeAsyncStream(self._events)


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


class _FakeAsyncClient:
    def __init__(self, events):
        self.responses = _FakeAsyncResponses(events)
        self.vector_stores = _FakeAsyncVS()


def _drain_async(agen):
    async def collect():
        return [e async for e in agen]
    return asyncio.run(collect())


async def _async_return(value):
    """Helper: async function that immediately returns value (for monkeypatching async classify)."""
    return value


def test_astream_answer_yields_deltas_then_final(monkeypatch):
    import app.rag as rag
    monkeypatch.setattr(rag, "aclassify_intent", lambda *a, **k: _async_return("legal_question"))
    d1 = SimpleNamespace(type="response.output_text.delta", delta="部分")
    d2 = SimpleNamespace(type="response.output_text.delta", delta="答案")
    completed = SimpleNamespace(type="response.completed", response=_fake_response_with_results())
    client = _FakeAsyncClient([d1, d2, completed])

    events = _drain_async(
        astream_answer(client, "個資外洩?", vector_store_id="vs_1", model="gpt-5.4-mini")
    )

    flow = [e for e in events if e["type"] != "stage"]   # 階段事件另測，此處驗 delta→final
    assert flow[0] == {"type": "delta", "text": "部分"}
    assert flow[1] == {"type": "delta", "text": "答案"}
    assert flow[2]["type"] == "final"
    # 雙路後 citations 來自 sources（_FakeAsyncVS 回傳）：法條路 + 判決路各一筆
    assert flow[2]["citations"] == ["勞動基準法-第24條.txt", "臺北地院-判決-案.txt"]
    assert client.responses.calls[0]["stream"] is True
    assert client.responses.calls[0]["reasoning"] == {"effort": "low"}


def test_astream_answer_skips_reasoning_deltas(monkeypatch):
    import app.rag as rag
    monkeypatch.setattr(rag, "aclassify_intent", lambda *a, **k: _async_return("legal_question"))
    reasoning = SimpleNamespace(type="response.reasoning_summary_text.delta", delta="思考中…")
    answer = SimpleNamespace(type="response.output_text.delta", delta="正式答案")
    completed = SimpleNamespace(type="response.completed", response=_fake_response_with_results())
    client = _FakeAsyncClient([reasoning, answer, completed])

    events = _drain_async(
        astream_answer(client, "個資外洩?", vector_store_id="vs_1", model="gpt-5.4-mini")
    )

    deltas = [e for e in events if e["type"] == "delta"]
    assert deltas == [{"type": "delta", "text": "正式答案"}]
    assert events[-1]["type"] == "final"


# ---- (J) inline 引用標記 → 腳註（修「依據欄空白」根因）----
# gpt-5.4-mini 把來源以 inline 引用標記塞進答案（PUA U+E200…filecite U+E202 turnNfileM U+E201），
# annotations 常空。原 _strip_citation_markers 把這些整段刪除 → 當模型把「依據」欄純用標記表達時整欄變空。
# 正解：把標記轉成可對映來源的腳註 [^k]（k 對到 file_search_call.results[M]），依據欄不再空。
def _resp_with_marker_citations():
    # 兩列表格，依據欄各只用 inline 標記（模擬截圖的「整欄空白」極端情形）
    text = (
        "| 面向 | 依據 |\n|---|---|\n"
        "| 客戶身分確認 | fileciteturn0file0 |\n"
        "| PEPs | fileciteturn0file1 fileciteturn0file0 |\n"
    )
    content = SimpleNamespace(text=text, annotations=[])  # annotations 空（gpt-5.4-mini 實況）
    message = SimpleNamespace(type="message", content=[content])
    rA = SimpleNamespace(file_id="fA", filename="洗錢防制法-第7條.txt", text="第7條…", score=0.78)
    rB = SimpleNamespace(file_id="fB", filename="洗錢防制-FAQ-PEPs.txt", text="PEPs…", score=0.77)
    fs = SimpleNamespace(type="file_search_call", content=None, results=[rA, rB])
    return SimpleNamespace(output=[fs, message], id="resp_mk")


def test_parse_response_converts_markers_to_footnotes():
    result = parse_response(_resp_with_marker_citations())
    ans = result["answer"]
    # 標記被轉成腳註、依據欄不空
    assert "[^1]" in ans and "[^2]" in ans
    # 不殘留 PUA 字元或 turnXfileY token
    assert not any("" <= ch <= "" for ch in ans)
    assert "turn0file" not in ans


def test_parse_response_marker_footnotes_map_to_cited_evidence():
    result = parse_response(_resp_with_marker_citations())
    # 腳註順序 = 首次被引用順序：file0(洗錢防制法第7條)=^1、file1(PEPs FAQ)=^2
    assert result["evidence_mode"] == "cited"
    assert result["citations"] == ["洗錢防制法-第7條.txt", "洗錢防制-FAQ-PEPs.txt"]
    assert [e["filename"] for e in result["evidence"]] == ["洗錢防制法-第7條.txt", "洗錢防制-FAQ-PEPs.txt"]


def test_parse_response_marker_dedupes_same_file_to_same_footnote():
    # 同一檔被多列引用 → 同一腳註編號（PEPs 列也引用 file0，應仍是 ^1）
    result = parse_response(_resp_with_marker_citations())
    # file0 出現在第1列與第2列，都應是 [^1]；citations 不重複
    assert result["answer"].count("[^1]") == 2
    assert result["citations"].count("洗錢防制法-第7條.txt") == 1


def test_parse_response_footnotes_follow_reading_order():
    # 混用純文字 token（在前）與 PUA span（在後）時，腳註編號須依「閱讀順序」遞增，
    # 否則上標會顯示成 ²…¹（雖不誤標，但視覺錯亂）。
    # bare(file0) 在前、完整 PUA span(file1) 在後——兩者分屬不同 pass，正是會亂序的組合。
    span = chr(0xE200) + "filecite" + chr(0xE202) + "turn0file1" + chr(0xE201)
    text = "前段fileciteturn0file0，後段" + span + "。"
    content = SimpleNamespace(text=text, annotations=[])
    message = SimpleNamespace(type="message", content=[content])
    r0 = SimpleNamespace(file_id="f0", filename="甲檔.txt", text="甲", score=0.9)
    r1 = SimpleNamespace(file_id="f1", filename="乙檔.txt", text="乙", score=0.8)
    fs = SimpleNamespace(type="file_search_call", content=None, results=[r0, r1])
    result = parse_response(SimpleNamespace(output=[fs, message]))
    # 先出現的 file0 應為 [^1]、後出現的 file1 應為 [^2]，且 [^1] 在 [^2] 之前
    assert result["answer"].index("[^1]") < result["answer"].index("[^2]")
    assert result["citations"] == ["甲檔.txt", "乙檔.txt"]


def test_parse_response_out_of_range_marker_dropped_safely():
    # turnNfileM 的 M 超出 results 範圍 → 安全移除，不產生錯誤腳註、不 crash（避免張冠李戴）
    text = "結論。fileciteturn0file9"
    content = SimpleNamespace(text=text, annotations=[])
    message = SimpleNamespace(type="message", content=[content])
    r0 = SimpleNamespace(file_id="f0", filename="只有這一檔.txt", text="…", score=0.9)
    fs = SimpleNamespace(type="file_search_call", content=None, results=[r0])
    result = parse_response(SimpleNamespace(output=[fs, message]))
    assert result["answer"] == "結論。"
    assert "[^" not in result["answer"]


# ---- (H) 串流終結事件：incomplete/failed 也要發出 final ----
# 根因：推理模型 gpt-5.4-mini 撞 max_output_tokens 時，Responses API 以 status="incomplete"
# 結束、終結串流事件為 response.incomplete（非 completed）。原碼只認 completed → 永不發 final
# → 答案截斷且無 citations/evidence/followups。下列測試鎖死「截斷時仍須發出 final」。
# 註：此 fixture 為舊 file_search 形狀，dual 流會略過 file_search_call/annotations，僅用其 status/incomplete 旗標。
def _fake_incomplete_response():
    ann = SimpleNamespace(type="file_citation", file_id="file-A", filename="個資法-第12條.txt")
    content = SimpleNamespace(text="部分答案被截斷", annotations=[ann])
    message = SimpleNamespace(type="message", content=[content])
    r = SimpleNamespace(file_id="file-A", filename="個資法-第12條.txt", text="A段", score=0.9)
    fs = SimpleNamespace(type="file_search_call", content=None, results=[r])
    return SimpleNamespace(
        output=[fs, message], id="resp_inc",
        status="incomplete",
        incomplete_details=SimpleNamespace(reason="max_output_tokens"),
        usage=SimpleNamespace(input_tokens=5, output_tokens=2048, total_tokens=2053),
    )


def test_parse_response_flags_truncated_on_incomplete():
    result = parse_response(_fake_incomplete_response())
    assert result["status"] == "incomplete"
    assert result["truncated"] is True
    # 截斷時仍須能取出已生成的 citations/evidence（供前端誠實顯示）
    assert result["citations"] == ["個資法-第12條.txt"]


def test_parse_response_not_truncated_on_normal():
    result = parse_response(_fake_response_with_results())
    assert result["truncated"] is False
    assert result["status"] in (None, "completed")


# ---- (I) 檢索分流：判決保障名額（方向1，解決判決結構性弱勢）----
# 判決語意密度天生輸法條（長論述 vs 單條法規，實測差~12%），單路檢索 top-20 會被高分法條
# 擠光（實測判決 0 筆）。retrieve_dual 走兩路：法條路(排除判決，主力) + 判決路(只取判決、
# 獨立門檻、嚴格上限 M=3，少而精避免噪音過載——context7：多餘 context 反降答案品質)。
from app.rag import retrieve_dual


class _FakeVectorStores:
    def __init__(self):
        self.search_calls = []

    def search(self, **kwargs):
        self.search_calls.append(kwargs)
        val = (kwargs.get("filters") or {}).get("value", "?")
        data = [SimpleNamespace(score=0.7, filename=f"{val}-{i}.txt",
                                attributes={"doc_type": val}) for i in range(3)]
        return SimpleNamespace(data=data)


class _FakeClientVS:
    def __init__(self):
        self.vector_stores = _FakeVectorStores()


def test_retrieve_dual_runs_two_routes_with_correct_filters():
    client = _FakeClientVS()
    out = retrieve_dual(client, "資遣費怎麼算", vector_store_id="vs_x")
    calls = client.vector_stores.search_calls
    assert len(calls) == 2
    # 法條路：排除判決（doc_type ne 判決）
    assert calls[0]["filters"] == {"type": "ne", "key": "doc_type", "value": "判決"}
    # 判決路：只取判決（eq）、上限 3
    assert calls[1]["filters"] == {"type": "eq", "key": "doc_type", "value": "判決"}
    assert calls[1]["max_num_results"] == 3
    assert "law" in out and "judgment" in out
    assert len(out["judgment"]) == 3


def test_retrieve_dual_respects_caps_and_thresholds():
    client = _FakeClientVS()
    retrieve_dual(client, "q", vector_store_id="vs_x",
                  law_k=15, law_threshold=0.5, judgment_m=2, judgment_threshold=0.45)
    law_call, jud_call = client.vector_stores.search_calls
    assert law_call["max_num_results"] == 15
    assert law_call["ranking_options"] == {"score_threshold": 0.5}
    assert jud_call["max_num_results"] == 2
    assert jud_call["ranking_options"] == {"score_threshold": 0.45}


class _FakeVSJudgmentEmpty:
    """判決路回空（一般純法條題常見），法條路正常——驗證空結果不報錯、不互相影響。"""
    def __init__(self):
        self.search_calls = []

    def search(self, **kwargs):
        self.search_calls.append(kwargs)
        # 判決路用 eq，法條路用 ne（兩者 value 都是「判決」，須以 type 區分）。
        if (kwargs.get("filters") or {}).get("type") == "eq":
            return SimpleNamespace(data=[])
        return SimpleNamespace(data=[SimpleNamespace(
            score=0.8, filename="law.txt", attributes={"doc_type": "法條"})])


def test_retrieve_dual_handles_empty_judgment_route():
    client = SimpleNamespace(vector_stores=_FakeVSJudgmentEmpty())
    out = retrieve_dual(client, "純法條問題", vector_store_id="vs_x")
    assert out["judgment"] == []      # 判決路空：不報錯、回空 list
    assert len(out["law"]) == 1       # 法條路不受判決路空影響


def test_parse_response_surfaces_incomplete_reason():
    # telemetry：截斷原因（多為 max_output_tokens）須帶出，供 server 端記錄。
    result = parse_response(_fake_incomplete_response())
    assert result["incomplete_reason"] == "max_output_tokens"


def _fake_failed_response():
    # response.failed 常見無 message 輸出（空 output）。
    return SimpleNamespace(output=[], id="resp_fail", status="failed")


def test_parse_response_status_failed_not_truncated():
    result = parse_response(_fake_failed_response())
    assert result["status"] == "failed"
    assert result["truncated"] is False
    assert result["answer"] == ""


def test_stream_answer_emits_final_on_failed():
    # response.failed 也是終結事件（帶 .response）：須發出 final 讓前端能誠實收尾，
    # 不可讓串流靜默結束、前端永遠卡住。
    failed = SimpleNamespace(type="response.failed", response=_fake_failed_response())
    client = _FakeClient([failed])

    events = list(stream_answer(client, "q", vector_store_id="vs_1", model="gpt-5.4-mini"))

    assert events[-1]["type"] == "final"
    assert events[-1]["status"] == "failed"


def test_stream_answer_emits_final_on_incomplete():
    delta = SimpleNamespace(type="response.output_text.delta", delta="部分答案")
    incomplete = SimpleNamespace(type="response.incomplete", response=_fake_incomplete_response())
    client = _FakeClient([delta, incomplete])

    events = list(stream_answer(client, "q", vector_store_id="vs_1", model="gpt-5.4-mini"))

    assert events[0] == {"type": "delta", "text": "部分答案"}
    final = events[-1]
    assert final["type"] == "final"
    # 雙路後 citations 來自 sources（_FakeVS 回傳）：法條路 + 判決路各一筆
    assert final["citations"] == ["勞動基準法-第24條.txt", "臺北地院-判決-案.txt"]
    assert final["truncated"] is True


def test_astream_answer_emits_final_on_incomplete():
    delta = SimpleNamespace(type="response.output_text.delta", delta="部分答案")
    incomplete = SimpleNamespace(type="response.incomplete", response=_fake_incomplete_response())
    client = _FakeAsyncClient([delta, incomplete])

    events = _drain_async(
        astream_answer(client, "q", vector_store_id="vs_1", model="gpt-5.4-mini")
    )

    final = events[-1]
    assert final["type"] == "final"
    assert final["truncated"] is True
    # 雙路後 citations 來自 sources（_FakeAsyncVS 回傳）：法條路 + 判決路各一筆
    assert final["citations"] == ["勞動基準法-第24條.txt", "臺北地院-判決-案.txt"]


def test_suggest_followups_graceful_on_bad_output():
    # output_text 非合法 JSON（模型故障/截斷）時，不可拋例外，須優雅回空清單。
    class _BadResponses:
        def create(self, **kwargs):
            return SimpleNamespace(output_text="not-json{")

    class _BadClient:
        def __init__(self):
            self.responses = _BadResponses()

    out = suggest_followups(_BadClient(), "q", "a", model="gpt-5.4-mini")
    assert out == {"questions": []}


# ---- (G) 缺口②/⑦：新 ANSWER_INSTRUCTIONS 涵蓋表格 + 確定性層級 + 誠實兜底 ----
def test_answer_instructions_cover_table_and_certainty_tiers():
    text = ANSWER_INSTRUCTIONS
    assert "表格" in text                       # ②表格為主（智慧判斷）
    assert "非正式法律意見" in text or "非法律意見" in text
    # ⑦：區分明文 / 解釋空間 / 語料未涵蓋（誠實兜底）
    for token in ["明文", "解釋", "語料"]:
        assert token in text


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
    assert "法規依據" in context and "判決見解" in context
    assert "[來源1]" in context and "[來源2]" in context
    assert sources[0]["filename"] == "勞動基準法-第24條.txt"
    assert sources[0]["doc_type"] == "法條"
    assert sources[1]["filename"] == "臺北地院-判決-資遣費案.txt"
    assert sources[1]["doc_type"] == "判決"
    assert "加班費依本條計算。" in sources[0]["text"]


def test_build_context_block_empty_judgment_route():
    law = [_r("公司法-第23條.txt", "負責人忠實義務。", 0.7, "法條")]
    context, sources = build_context_block(law, [])
    assert "判決見解" not in context
    assert len(sources) == 1


def test_build_context_block_preserves_url_when_present():
    law = [_r("個資法-第12條.txt", "應通知當事人。", 0.9, "法條", url="https://x")]
    _, sources = build_context_block(law, [])
    assert sources[0]["url"] == "https://x"


def test_build_context_block_dedupes_same_filename_keeps_best():
    # 真實情況（個資外洩題實測）：判決路同一份判決回多個 chunk，
    # 不應變成多個來源 / 重複 citations；同檔合併為單一 [來源N]、最高分 chunk 領頭。
    judgment = [
        _r("臺北高等行政法院-判決-個資.txt", "段落一（較低分）", 0.40, "判決"),
        _r("臺北高等行政法院-判決-個資.txt", "段落二（最高分）", 0.55, "判決"),
        _r("臺北高等行政法院-判決-個資.txt", "段落三", 0.45, "判決"),
    ]
    context, sources = build_context_block([], judgment)
    assert [s["filename"] for s in sources] == ["臺北高等行政法院-判決-個資.txt"]
    assert sources[0]["text"].startswith("段落二（最高分）")  # 最高分 chunk 領頭
    assert sources[0]["score"] == 0.55
    assert context.count("[來源") == 1


def test_build_context_block_merges_top2_chunks_per_file():
    # 去重副作用修正：同檔不只留 1 個 chunk——合併前 2 高分 chunk 進同一個來源，
    # 兼顧「來源／citations 唯一」與「長判決 context 豐富度」。第 3 名以後捨棄。
    judgment = [
        _r("臺北高等行政法院-判決-個資.txt", "段落一（第三名）", 0.40, "判決"),
        _r("臺北高等行政法院-判決-個資.txt", "段落二（最高分）", 0.55, "判決"),
        _r("臺北高等行政法院-判決-個資.txt", "段落三（次高分）", 0.45, "判決"),
    ]
    context, sources = build_context_block([], judgment)
    assert len(sources) == 1
    assert "段落二（最高分）" in sources[0]["text"]
    assert "段落三（次高分）" in sources[0]["text"]      # 第 2 名也進 context
    assert "段落一（第三名）" not in sources[0]["text"]  # 第 3 名捨棄
    assert context.count("[來源") == 1                   # 仍只有一個來源編號


def test_result_text_empty_content_falls_back_to_text():
    from app.rag import _result_text
    r = SimpleNamespace(content=[], text="後備文字")
    assert _result_text(r) == "後備文字"


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
    assert len(out["evidence"]) == 2
    assert out["citations"] == [s["filename"] for s in _SOURCES]


def test_parse_dual_drops_out_of_range_marker():
    resp = _msg("引用越界[來源9]應安全丟棄。")
    out = parse_dual_response(resp, _SOURCES)
    assert "[^" not in out["answer"]
    assert "[來源9]" not in out["answer"]
    assert out["evidence_mode"] == "dual_retrieved"


def test_parse_dual_repeated_marker_reuses_footnote():
    resp = _msg("先講[來源1]，再補充[來源1]，最後[來源2]。")
    out = parse_dual_response(resp, _SOURCES)
    assert out["answer"].count("[^1]") == 2      # 同來源重複引用 → 同腳註號
    assert "[^2]" in out["answer"]
    assert out["citations"] == ["勞動基準法-第24條.txt", "臺北地院-判決-資遣費案.txt"]


# ---- (L) 串流走雙路：先檢索組 context，再串流；final 由 parse_dual_response 組 ----
from app.rag import astream_answer as _astream, aretrieve_dual


def test_aretrieve_dual_awaits_two_routes():
    client = _FakeAsyncClient([])

    async def run():
        return await aretrieve_dual(client, "q", vector_store_id="vs_1")

    out = asyncio.run(run())
    assert len(client.vector_stores.search_calls) == 2
    assert client.vector_stores.search_calls[0]["filters"] == {"type": "ne", "key": "doc_type", "value": "判決"}
    assert client.vector_stores.search_calls[1]["filters"] == {"type": "eq", "key": "doc_type", "value": "判決"}
    assert len(out["law"]) == 1 and len(out["judgment"]) == 1


def test_astream_dual_retrieves_then_streams_and_finalizes(monkeypatch):
    import app.rag as rag
    monkeypatch.setattr(rag, "aclassify_intent", lambda *a, **k: _async_return("legal_question"))
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

    items = asyncio.run(run())
    assert len(client.vector_stores.search_calls) == 2
    assert "tools" not in client.responses.calls[0]
    assert "檢索到的依據" in client.responses.calls[0]["input"]
    assert any(i["type"] == "delta" for i in items)
    final = [i for i in items if i["type"] == "final"][0]
    assert "[^1]" in final["answer"]
    assert final["evidence_mode"] == "dual_cited"


# ---- (M) 職能揭露 intent gate ----
from app.rag import answer_question as _aq, capability_result


def test_capability_result_shape():
    out = capability_result({"doc_type_counts": {"法條": 5}, "categories": ["個資"], "cutoff": "2026-06", "total": 5})
    assert out["evidence_mode"] == "capability"
    assert out["citations"] == [] and out["evidence"] == []
    assert "我能" in out["answer"] and "非正式法律意見" in out["answer"]
    assert out["status"] == "completed" and out["truncated"] is False


def test_answer_question_meta_skips_retrieval(monkeypatch):
    import app.rag as rag
    monkeypatch.setattr(rag, "classify_intent", lambda *a, **k: "meta_capability")
    client = _FakeClient(_fake_response())          # has vector_stores with search_calls
    out = _aq(client, "你能做什麼？", vector_store_id="vs_1", model="m")
    assert out["evidence_mode"] == "capability"
    assert len(client.vector_stores.search_calls) == 0


def test_answer_question_legal_uses_retrieval(monkeypatch):
    import app.rag as rag
    monkeypatch.setattr(rag, "classify_intent", lambda *a, **k: "legal_question")
    client = _FakeClient(_fake_response())
    out = _aq(client, "個資法第6條？", vector_store_id="vs_1", model="m")
    assert len(client.vector_stores.search_calls) == 2


def test_verbosity_is_low():
    from app.rag import _MODEL_PARAMS
    assert _MODEL_PARAMS["text"]["verbosity"] == "low"


def test_dual_law_threshold_is_035():
    # 召回修正：法條路 0.5 硬門檻把臨界相關文件（實測 0.45–0.49）整批切掉，
    # 退化成 gap 答案。降至 0.35 與判決路對稱；好查詢 top-12 早被高分塞滿不受影響。
    import app.rag as rag
    assert rag.DUAL_LAW_THRESHOLD == 0.35


def test_answer_question_followup_skips_intent_gate(monkeypatch):
    # 多輪修正：延續輪（有 previous_response_id）不再過 intent gate——
    # 「好啊請幫我生成」這類延續肯定句實測 100% 被誤判 meta_capability 而吐罐頭答案。
    import app.rag as rag

    def _boom(*a, **k):
        raise AssertionError("延續輪不應呼叫 classify_intent")

    monkeypatch.setattr(rag, "classify_intent", _boom)
    monkeypatch.setattr(rag, "classify_tier", lambda *a, **k: "明文")
    client = _FakeClient(_fake_response())
    out = rag.answer_question(client, "好啊請幫我生成", vector_store_id="vs_1",
                              model="m", previous_response_id="resp_prev")
    assert len(client.vector_stores.search_calls) == 2   # 正常走檢索問答
    assert out["evidence_mode"] != "capability"


def test_astream_emits_real_stage_events(monkeypatch):
    # 等待UX（NN/g）：階段列必須掛真實管線事件，不演戲——
    # retrieving（進檢索）→ retrieved（含兩路實數）→ generating（串流建立）→ delta。
    import app.rag as rag
    monkeypatch.setattr(rag, "aclassify_intent", lambda *a, **k: _async_return("legal_question"))
    monkeypatch.setattr(rag, "aclassify_tier", lambda *a, **k: _async_return("明文"))
    delta = SimpleNamespace(type="response.output_text.delta", delta="文字")
    completed_resp = SimpleNamespace(
        output=[SimpleNamespace(type="message", content=[SimpleNamespace(text="文字", annotations=[])])],
        id="r", usage=None, status="completed", incomplete_details=None)
    completed = SimpleNamespace(type="response.completed", response=completed_resp)
    client = _FakeAsyncClient([delta, completed])

    async def run():
        return [it async for it in _astream(client, "加班費?", vector_store_id="vs_1", model="m")]

    items = asyncio.run(run())
    assert items[0] == {"type": "stage", "stage": "retrieving"}
    retrieved = next(i for i in items if i.get("stage") == "retrieved")
    assert retrieved["law_count"] == 1 and retrieved["judgment_count"] == 1   # FakeVS 兩路各 1
    gen_idx = next(n for n, i in enumerate(items) if i.get("stage") == "generating")
    first_delta_idx = next(n for n, i in enumerate(items) if i["type"] == "delta")
    assert gen_idx < first_delta_idx   # generating 先於首 token


def test_astream_client_abort_cancels_tier_task(monkeypatch):
    # 停止鍵使 client abort 成為常態路徑：generator 收 GeneratorExit 時
    # tier_task 不可成孤兒（否則多燒一次 API call + unretrieved exception 警告）。
    import app.rag as rag
    monkeypatch.setattr(rag, "aclassify_intent", lambda *a, **k: _async_return("legal_question"))
    state = {}

    async def never_tier(*a, **k):
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            state["cancelled"] = True
            raise

    monkeypatch.setattr(rag, "aclassify_tier", never_tier)
    delta = SimpleNamespace(type="response.output_text.delta", delta="部分")
    completed = SimpleNamespace(type="response.completed", response=SimpleNamespace(
        output=[], id="r", usage=None, status="completed", incomplete_details=None))
    client = _FakeAsyncClient([delta, completed])

    async def run():
        gen = _astream(client, "q", vector_store_id="vs_1", model="m")
        async for it in gen:
            if it["type"] == "delta":
                break               # 模擬使用者中途停止
        await asyncio.sleep(0)      # 讓 tier_task 先起跑（進入 await）再中止
        await asyncio.sleep(0)
        await gen.aclose()          # GeneratorExit → finally 應 cancel tier_task
        for _ in range(3):
            await asyncio.sleep(0)  # 讓 cancellation 傳遞

    asyncio.run(run())
    assert state.get("cancelled") is True


def test_astream_meta_path_has_no_stage_events(monkeypatch):
    # 能力回答是即答（無檢索），不應出現階段事件。
    import app.rag as rag
    monkeypatch.setattr(rag, "aclassify_intent", lambda *a, **k: _async_return("meta_capability"))
    client = _FakeAsyncClient([])

    async def run():
        return [it async for it in _astream(client, "你能做什麼?", vector_store_id="vs_1", model="m")]

    items = asyncio.run(run())
    assert all(i["type"] != "stage" for i in items)


def test_build_dual_kwargs_followup_relaxes_citation_lock(monkeypatch):
    # H2（實測重現）：延續輪「好啊請幫我生成對照表」檢索到不相關來源，
    # 引用規約「只依這些來源作答」迫使模型做錯主題的表。延續輪需附對話脈絡優先指引。
    from app.rag import _build_dual_kwargs, CONTINUATION_GUIDANCE
    follow = _build_dual_kwargs("好啊請幫我生成", "## 法規依據\n[來源1]無關內容", "m", "resp_prev")
    assert CONTINUATION_GUIDANCE in follow["instructions"]
    first = _build_dual_kwargs("個資法第6條？", "## 法規依據\n[來源1]內容", "m", None)
    assert CONTINUATION_GUIDANCE not in first["instructions"]   # 首輪不附（維持嚴格引用）


def test_astream_answer_followup_skips_intent_gate(monkeypatch):
    import app.rag as rag

    def _boom(*a, **k):
        raise AssertionError("延續輪不應呼叫 aclassify_intent")

    monkeypatch.setattr(rag, "aclassify_intent", _boom)
    monkeypatch.setattr(rag, "aclassify_tier", lambda *a, **k: _async_return("明文"))
    completed_resp = SimpleNamespace(
        output=[SimpleNamespace(type="message", content=[SimpleNamespace(text="表格", annotations=[])])],
        id="r2", usage=None, status="completed", incomplete_details=None)
    completed = SimpleNamespace(type="response.completed", response=completed_resp)
    client = _FakeAsyncClient([completed])

    async def run():
        items = []
        async for it in _astream(client, "好啊請幫我生成", vector_store_id="vs_1",
                                 model="m", previous_response_id="resp_prev"):
            items.append(it)
        return items

    items = asyncio.run(run())
    final = [i for i in items if i["type"] == "final"][0]
    assert final["evidence_mode"] != "capability"
    assert len(client.vector_stores.search_calls) == 2


def test_answer_question_attaches_tier(monkeypatch):
    import app.rag as rag
    monkeypatch.setattr(rag, "classify_intent", lambda *a, **k: "legal_question")
    monkeypatch.setattr(rag, "classify_tier", lambda *a, **k: "明文")
    client = _FakeClient(_fake_response())
    out = rag.answer_question(client, "個資外洩?", vector_store_id="vs_1", model="m")
    assert out["tier"] == "明文"


def test_astream_answer_attaches_tier(monkeypatch):
    import app.rag as rag
    monkeypatch.setattr(rag, "aclassify_intent", lambda *a, **k: _async_return("legal_question"))
    monkeypatch.setattr(rag, "aclassify_tier", lambda *a, **k: _async_return("實務見解"))
    delta = SimpleNamespace(type="response.output_text.delta", delta="文字")
    completed_resp = SimpleNamespace(
        output=[SimpleNamespace(type="message", content=[SimpleNamespace(text="文字", annotations=[])])],
        id="r", usage=None, status="completed", incomplete_details=None)
    completed = SimpleNamespace(type="response.completed", response=completed_resp)
    client = _FakeAsyncClient([delta, completed])
    async def run():
        items = []
        async for it in rag.astream_answer(client, "q", vector_store_id="vs_1", model="m"):
            items.append(it)
        return items
    items = asyncio.run(run())
    final = [i for i in items if i["type"] == "final"][0]
    assert final["tier"] == "實務見解"
