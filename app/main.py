"""FastAPI 整合層。

職責：
- /api/ask（問答）+ 非阻斷營運 log（DB 失敗不影響回應）
- /api/ask/stream（SSE 串流）
- /api/suggest（追問建議）
- /api/source/{filename}（條文原文，含目錄穿越防護）
- /api/feedback（讚/倒讚）
- Sentry 容錯初始化

鐵則：log_query 失敗只記 warning + Sentry capture，不冒泡；不改 rag.py/db.py/config.py。
"""
import json
import logging
import os
import time
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
from openai import AsyncOpenAI, OpenAI
from pydantic import BaseModel, field_validator
from sse_starlette.sse import EventSourceResponse

from app import config
from app.db import Feedback, QueryLog, make_session_factory
from app.rag import answer_question, astream_answer, suggest_followups

logger = logging.getLogger(__name__)

# ---------- Sentry（容錯）----------
try:
    import sentry_sdk
except ImportError:  # pragma: no cover - 部署環境才裝
    sentry_sdk = None

if sentry_sdk is not None and os.getenv("SENTRY_DSN"):
    try:
        sentry_sdk.init(dsn=os.getenv("SENTRY_DSN"), traces_sample_rate=0.1)
    except Exception as e:  # noqa: BLE001 - 壞 DSN 不可拖垮 app 啟動
        logging.warning("Sentry 初始化失敗: %s", e)


def _capture(exc):
    if sentry_sdk is not None:
        try:
            sentry_sdk.capture_exception(exc)
        except Exception:  # pragma: no cover - 監控本身不可拖垮主流程
            pass


# ---------- DB session + 非阻斷 log ----------
_DATABASE_URL = os.getenv("DATABASE_URL")
SessionLocal = make_session_factory(_DATABASE_URL) if _DATABASE_URL else None


def log_query(**fields) -> int | None:
    """寫一列 QueryLog 回傳 id；無 DB 回 None。

    本函式「不吞例外」——由呼叫端 try/except 包住，確保非阻斷。
    """
    if SessionLocal is None:
        return None
    with SessionLocal() as s:
        log = QueryLog(**fields)
        s.add(log)
        s.commit()
        return log.id


def save_feedback(query_log_id: int, rating: str) -> None:
    """寫一列 Feedback；無 DB 則 no-op。"""
    if SessionLocal is None:
        return
    with SessionLocal() as s:
        s.add(Feedback(query_log_id=query_log_id, rating=rating))
        s.commit()


def _build_log_fields(question, result, latency_ms, previous_response_id):
    """把 answerer 結果攤平成 QueryLog 欄位。"""
    citations = result.get("citations") or []
    answer = result.get("answer") or ""
    usage = result.get("usage") or {}
    return {
        "question": question,
        "answer": answer,
        "citations": citations,
        "evidence": result.get("evidence") or [],
        "citation_count": len(citations),
        "has_answer": bool(answer.strip()),
        "latency_ms": latency_ms,
        "input_tokens": usage.get("input_tokens", 0) or 0,
        "output_tokens": usage.get("output_tokens", 0) or 0,
        "total_tokens": usage.get("total_tokens", 0) or 0,
        "model": config.OPENAI_MODEL,
        "response_id": result.get("response_id"),
        "previous_response_id": previous_response_id,
    }


def _safe_log_query(**fields) -> int | None:
    """非阻斷包裝：log 失敗只 warning + Sentry capture，回 None。"""
    try:
        return log_query(**fields)
    except Exception as exc:  # noqa: BLE001 - 非阻斷鐵則
        logger.warning("log_query 失敗（不影響回應）：%s", exc)
        _capture(exc)
        return None


# ---------- App ----------
app = FastAPI(title="法規 RAG 問答")

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/health")
def health():
    return {"status": "ok"}


# ---------- Schemas ----------
class AskRequest(BaseModel):
    question: str
    previous_response_id: str | None = None

    @field_validator("question")
    @classmethod
    def not_blank(cls, v):
        if not v or not v.strip():
            raise ValueError("question 不可為空")
        return v.strip()


class SuggestRequest(BaseModel):
    question: str
    answer: str


class FeedbackRequest(BaseModel):
    query_log_id: int
    rating: Literal["up", "down"]


# ---------- Dependencies ----------
def get_answerer():
    client = OpenAI(api_key=config.OPENAI_API_KEY)

    def answerer(question: str, previous_response_id: str | None = None):
        return answer_question(
            client, question,
            vector_store_id=config.VECTOR_STORE_ID,
            model=config.OPENAI_MODEL,
            previous_response_id=previous_response_id,
        )

    return answerer


# ---------- /api/ask ----------
@app.post("/api/ask")
def ask(req: AskRequest, answerer=Depends(get_answerer)):
    start = time.perf_counter()
    result = answerer(req.question, req.previous_response_id)
    latency_ms = int((time.perf_counter() - start) * 1000)

    fields = _build_log_fields(req.question, result, latency_ms, req.previous_response_id)
    query_log_id = _safe_log_query(**fields)

    return {**result, "query_log_id": query_log_id}


# ---------- /api/ask/stream (SSE) ----------
@app.post("/api/ask/stream")
async def ask_stream(req: AskRequest):
    # 缺口①：用 AsyncOpenAI + async for（astream_answer）逐字串流，不阻塞 event loop；
    # 同步 DB log 以 run_in_threadpool 卸到執行緒池，避免再次阻塞串流。
    client = AsyncOpenAI(api_key=config.OPENAI_API_KEY)
    start = time.perf_counter()

    async def event_gen():
        async for item in astream_answer(
            client, req.question,
            vector_store_id=config.VECTOR_STORE_ID,
            model=config.OPENAI_MODEL,
            previous_response_id=req.previous_response_id,
        ):
            if item.get("type") == "final":
                if item.get("truncated"):
                    # 截斷為 field-reported bug 的根因；記錄供 production 確認原因（多為 max_output_tokens）
                    logger.warning(
                        "回答被截斷 status=%s reason=%s response_id=%s",
                        item.get("status"), item.get("incomplete_reason"), item.get("response_id"),
                    )
                latency_ms = int((time.perf_counter() - start) * 1000)
                fields = _build_log_fields(
                    req.question, item, latency_ms, req.previous_response_id
                )
                query_log_id = await run_in_threadpool(_safe_log_query, **fields)
                item = {**item, "query_log_id": query_log_id}
            yield {"data": json.dumps(item, ensure_ascii=False)}
        yield {"event": "done", "data": "[DONE]"}

    return EventSourceResponse(event_gen())


# ---------- /api/suggest ----------
@app.post("/api/suggest")
def suggest(req: SuggestRequest):
    # 追問建議是獨立於主問答的輔助呼叫，其失敗不可變成 500 拖垮前端體驗：
    # 比照 log_query 的非阻斷鐵則，失敗只 warning + Sentry capture，優雅降級為空清單。
    try:
        client = OpenAI(api_key=config.OPENAI_API_KEY)
        return suggest_followups(client, req.question, req.answer, model=config.OPENAI_MODEL)
    except Exception as exc:  # noqa: BLE001 - 輔助功能不可拖垮主流程
        logger.warning("suggest_followups 失敗（不影響答案）：%s", exc)
        _capture(exc)
        return {"questions": []}


# ---------- /api/source/{filename} ----------
@app.get("/api/source/{filename}")
def source(filename: str):
    target = (DATA_DIR / filename).resolve()
    data_root = DATA_DIR.resolve()
    if data_root not in target.parents or not target.is_file():
        raise HTTPException(status_code=404, detail="找不到該條文原文")
    return {"filename": filename, "text": target.read_text(encoding="utf-8")}


# ---------- /api/feedback ----------
@app.post("/api/feedback")
def feedback(req: FeedbackRequest):
    save_feedback(req.query_log_id, req.rating)
    return {"status": "ok"}
