from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

def test_health_returns_ok():
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}

from app.main import app, get_answerer

def test_ask_returns_answer_and_citations():
    def fake_answerer(question, previous_response_id=None):
        return {"answer": f"回答：{question}", "citations": ["a.txt"]}
    app.dependency_overrides[get_answerer] = lambda: fake_answerer
    try:
        resp = client.post("/api/ask", json={"question": "測試問題"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["answer"] == "回答：測試問題"
        assert body["citations"] == ["a.txt"]
    finally:
        app.dependency_overrides.clear()

def test_ask_rejects_empty_question():
    resp = client.post("/api/ask", json={"question": "  "})
    assert resp.status_code == 422

def test_index_serves_html():
    resp = client.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "法規 RAG" in resp.text
