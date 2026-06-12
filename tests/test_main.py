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


# ---- /api/source_vs/{file_id}：判決原文自 vector store 取回（檔案不在 production）----
class _FakeContentPart:
    def __init__(self, text): self.text = text; self.type = "text"

class _FakeVSFiles:
    def __init__(self, parts=None, err=None):
        self._parts = parts or []; self._err = err; self.calls = []
    def content(self, file_id, *, vector_store_id):
        if self._err: raise self._err
        self.calls.append((file_id, vector_store_id))
        return iter(self._parts)

def _patch_vs_client(monkeypatch, files):
    import app.main as main_module
    class _C:
        def __init__(self, **kw): self.vector_stores = type("VS", (), {"files": files})()
    monkeypatch.setattr(main_module, "OpenAI", _C)

def test_source_vs_returns_joined_full_text(monkeypatch):
    files = _FakeVSFiles(parts=[_FakeContentPart("第一段。"), _FakeContentPart("第二段。")])
    _patch_vs_client(monkeypatch, files)
    resp = client.get("/api/source_vs/file_abc123XYZ")
    assert resp.status_code == 200
    assert resp.json()["text"] == "第一段。\n第二段。"
    # 確認 file_id 與自家 VECTOR_STORE_ID 確實傳入（store 綁定 env，不能由 client 指定）
    from app import config
    assert files.calls == [("file_abc123XYZ", config.VECTOR_STORE_ID)]

def test_source_vs_rejects_malformed_file_id():
    # 白名單格式防護：file_id 非 file_/file- 前綴一律 404，不打 OpenAI
    resp = client.get("/api/source_vs/..%2Fetc%2Fpasswd")
    assert resp.status_code == 404
    resp = client.get("/api/source_vs/notafileid")
    assert resp.status_code == 404

def test_source_vs_openai_error_degrades_to_404(monkeypatch):
    files = _FakeVSFiles(err=RuntimeError("boom"))
    _patch_vs_client(monkeypatch, files)
    resp = client.get("/api/source_vs/file_abc")
    assert resp.status_code == 404


def test_source_vs_empty_content_degrades_to_404(monkeypatch):
    # 空 parts／全空白 → 404（前端退回摘要），不可回 200 空字串
    files = _FakeVSFiles(parts=[_FakeContentPart("  "), _FakeContentPart("")])
    _patch_vs_client(monkeypatch, files)
    resp = client.get("/api/source_vs/file_empty1")
    assert resp.status_code == 404

def test_source_vs_overlong_file_id_rejected_without_openai_call(monkeypatch):
    files = _FakeVSFiles(parts=[_FakeContentPart("x")])
    _patch_vs_client(monkeypatch, files)
    resp = client.get("/api/source_vs/file_" + "a" * 80)
    assert resp.status_code == 404
    assert files.calls == []      # 白名單短路，未打 OpenAI
