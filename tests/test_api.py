"""Task 4/12/13 + SSE + Sentry 整合層測試（main.py）。

注入策略：
- answerer 用 dependency_overrides 注入 fake。
- log_query / save_feedback / stream_answer / suggest_followups 用 monkeypatch 替換模組層符號。
"""
import json

import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.main import app, get_answerer

client = TestClient(app)


@pytest.fixture
def fake_answer_result():
    return {
        "answer": "依個資法第12條，應通知當事人。",
        "citations": ["個資法.txt"],
        "evidence": [{"filename": "個資法.txt", "text": "第12條…", "score": 0.9}],
        "response_id": "resp_123",
        "usage": {"input_tokens": 10, "output_tokens": 20, "total_tokens": 30},
    }


@pytest.fixture
def override_answerer(fake_answer_result):
    captured = {}

    def fake_answerer(question, previous_response_id=None):
        captured["question"] = question
        captured["previous_response_id"] = previous_response_id
        return fake_answer_result

    app.dependency_overrides[get_answerer] = lambda: fake_answerer
    try:
        yield captured
    finally:
        app.dependency_overrides.clear()


# ---------- /api/ask ----------

def test_ask_returns_evidence_and_response_id(override_answerer, monkeypatch):
    monkeypatch.setattr(main, "log_query", lambda **kw: 42)
    resp = client.post("/api/ask", json={"question": "個資外洩怎麼辦"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["evidence"][0]["filename"] == "個資法.txt"
    assert body["response_id"] == "resp_123"
    assert body["query_log_id"] == 42


def test_ask_passes_previous_response_id(override_answerer, monkeypatch):
    monkeypatch.setattr(main, "log_query", lambda **kw: 1)
    resp = client.post(
        "/api/ask", json={"question": "後續呢", "previous_response_id": "resp_prev"}
    )
    assert resp.status_code == 200
    assert override_answerer["previous_response_id"] == "resp_prev"


def test_ask_logs_but_failure_does_not_break_response(override_answerer, monkeypatch):
    def boom(**kw):
        raise RuntimeError("DB down")

    monkeypatch.setattr(main, "log_query", boom)
    resp = client.post("/api/ask", json={"question": "個資外洩怎麼辦"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["answer"] == "依個資法第12條，應通知當事人。"
    assert body["query_log_id"] is None


# ---------- /api/ask/stream (SSE) ----------

def test_ask_stream_emits_deltas_and_final(monkeypatch):
    # 缺口①：串流改走非阻塞 astream_answer（async generator）+ AsyncOpenAI。
    async def fake_astream(client_, question, **kw):
        yield {"type": "delta", "text": "依個資法"}
        yield {"type": "delta", "text": "第12條"}
        yield {
            "type": "final",
            "answer": "依個資法第12條…",
            "citations": ["個資法.txt"],
            "evidence": [],
            "evidence_mode": "cited",
            "response_id": "resp_s",
            "usage": {"input_tokens": 1, "output_tokens": 2, "total_tokens": 3},
        }

    monkeypatch.setattr(main, "astream_answer", fake_astream)
    monkeypatch.setattr(main, "log_query", lambda **kw: 7)
    monkeypatch.setattr(main, "AsyncOpenAI", lambda **kw: object())

    with client.stream("POST", "/api/ask/stream", json={"question": "問"}) as resp:
        assert resp.status_code == 200
        text = "".join(resp.iter_text())

    assert "依個資法" in text
    assert "第12條" in text
    assert "個資法.txt" in text
    # final 併入了 query_log_id
    assert "query_log_id" in text


# ---------- /api/suggest ----------

def test_suggest_returns_questions(monkeypatch):
    monkeypatch.setattr(main, "OpenAI", lambda **kw: object())
    monkeypatch.setattr(
        main, "suggest_followups",
        lambda client_, q, a, **kw: {"questions": ["Q1", "Q2", "Q3"]},
    )
    resp = client.post("/api/suggest", json={"question": "問", "answer": "答"})
    assert resp.status_code == 200
    assert resp.json() == {"questions": ["Q1", "Q2", "Q3"]}


# ---------- /api/source/{filename} ----------

def test_source_endpoint_rejects_path_traversal():
    resp = client.get("/api/source/..%2F..%2Fapp%2Fmain.py")
    assert resp.status_code in (400, 404)


def test_source_endpoint_returns_text(tmp_path, monkeypatch):
    f = main.DATA_DIR / "個資法.txt"
    f.write_text("第12條 內容", encoding="utf-8")
    try:
        resp = client.get("/api/source/個資法.txt")
        assert resp.status_code == 200
        body = resp.json()
        assert body["filename"] == "個資法.txt"
        assert "第12條" in body["text"]
    finally:
        f.unlink(missing_ok=True)


def test_source_endpoint_missing_file_404():
    resp = client.get("/api/source/不存在.txt")
    assert resp.status_code == 404


# ---------- /api/feedback ----------

def test_feedback_calls_save(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        main, "save_feedback",
        lambda query_log_id, rating: captured.update(id=query_log_id, rating=rating),
    )
    resp = client.post("/api/feedback", json={"query_log_id": 5, "rating": "up"})
    assert resp.status_code == 200
    assert captured == {"id": 5, "rating": "up"}


def test_feedback_rejects_bad_rating():
    resp = client.post("/api/feedback", json={"query_log_id": 5, "rating": "meh"})
    assert resp.status_code == 422
