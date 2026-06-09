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
    assert call["max_output_tokens"] == 2048
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
