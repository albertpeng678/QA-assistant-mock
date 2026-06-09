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
