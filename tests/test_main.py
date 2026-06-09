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


def test_bad_sentry_dsn_does_not_break_import(monkeypatch):
    """壞 DSN 時 sentry_sdk.init 會拋 BadDsn；模組載入必須容錯不炸。"""
    import importlib
    import app.main as main_module

    monkeypatch.setenv("SENTRY_DSN", "not-a-valid-dsn")
    try:
        reloaded = importlib.reload(main_module)
        # import 不拋例外，且 app 物件仍可用
        assert reloaded.app is not None
        c = TestClient(reloaded.app)
        assert c.get("/health").json() == {"status": "ok"}
    finally:
        # 復原：清掉壞 DSN 後重載，避免汙染其他測試
        monkeypatch.delenv("SENTRY_DSN", raising=False)
        importlib.reload(main_module)
