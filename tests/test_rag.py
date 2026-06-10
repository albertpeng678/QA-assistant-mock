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

class _FakeClient:
    def __init__(self, response):
        self.responses = _FakeResponses(response)

def test_answer_question_calls_file_search_with_vector_store():
    client = _FakeClient(_fake_response())
    result = answer_question(client, "加班費怎麼算?", vector_store_id="vs_123", model="gpt-4o-mini")
    assert result["answer"] == "加班費依第24條計算。"
    assert result["citations"] == ["勞動基準法-第24條.txt"]
    call = client.responses.calls[0]
    assert call["model"] == "gpt-4o-mini"
    assert call["input"] == "加班費怎麼算?"
    assert call["tools"] == [{"type": "file_search", "vector_store_ids": ["vs_123"]}]

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

def test_answer_question_includes_search_results():
    client = _FakeClient(_fake_response_with_results())
    answer_question(client, "個資外洩?", vector_store_id="vs_1", model="gpt-4o-mini")
    assert client.responses.calls[0]["include"] == ["file_search_call.results"]

def test_answer_question_chains_previous_response_id():
    client = _FakeClient(_fake_response_with_results())
    answer_question(client, "追問", vector_store_id="vs_1", model="gpt-4o-mini", previous_response_id="resp_abc")
    assert client.responses.calls[0]["previous_response_id"] == "resp_abc"

def test_answer_question_omits_previous_response_id_when_none():
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
from app.rag import ANSWER_INSTRUCTIONS, _MODEL_PARAMS


def test_answer_question_applies_reasoning_model_params():
    client = _FakeClient(_fake_response_with_results())
    answer_question(client, "加班費?", vector_store_id="vs_1", model="gpt-5.4-mini")
    call = client.responses.calls[0]
    assert call["reasoning"] == {"effort": "low"}
    assert call["text"]["verbosity"] == "medium"
    assert call["max_output_tokens"] == 4096  # 推理模型需更多輸出預算，避免 incomplete 截斷
    assert call["instructions"] == ANSWER_INSTRUCTIONS
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
    assert final["citations"] == ["個資法-第12條.txt"]
    assert final["evidence"][0]["score"] == 0.91
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


class _FakeAsyncClient:
    def __init__(self, events):
        self.responses = _FakeAsyncResponses(events)


def _drain_async(agen):
    async def collect():
        return [e async for e in agen]
    return asyncio.run(collect())


def test_astream_answer_yields_deltas_then_final():
    d1 = SimpleNamespace(type="response.output_text.delta", delta="部分")
    d2 = SimpleNamespace(type="response.output_text.delta", delta="答案")
    completed = SimpleNamespace(type="response.completed", response=_fake_response_with_results())
    client = _FakeAsyncClient([d1, d2, completed])

    events = _drain_async(
        astream_answer(client, "個資外洩?", vector_store_id="vs_1", model="gpt-5.4-mini")
    )

    assert events[0] == {"type": "delta", "text": "部分"}
    assert events[1] == {"type": "delta", "text": "答案"}
    assert events[2]["type"] == "final"
    assert events[2]["citations"] == ["個資法-第12條.txt"]
    assert client.responses.calls[0]["stream"] is True
    assert client.responses.calls[0]["reasoning"] == {"effort": "low"}


def test_astream_answer_skips_reasoning_deltas():
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
    assert final["citations"] == ["個資法-第12條.txt"]
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
    assert final["citations"] == ["個資法-第12條.txt"]


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
