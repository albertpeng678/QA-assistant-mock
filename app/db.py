"""Postgres 營運 log DB 模組。

Railway 注入 DATABASE_URL（Postgres）；測試以 SQLite in-memory 離線跑。
SQLAlchemy 2.0 風格：DeclarativeBase / Mapped / mapped_column。
"""
import logging
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    create_engine,
    func,
    inspect,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
from sqlalchemy.types import JSON

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    pass


class QueryLog(Base):
    __tablename__ = "query_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    question: Mapped[str] = mapped_column(Text)
    answer: Mapped[str] = mapped_column(Text, default="")
    citations: Mapped[list] = mapped_column(JSON, default=list)
    evidence: Mapped[list] = mapped_column(JSON, default=list)
    citation_count: Mapped[int] = mapped_column(Integer, default=0)
    has_answer: Mapped[bool] = mapped_column(Boolean, default=False)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0)
    model: Mapped[str] = mapped_column(String(64), default="")
    response_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    previous_response_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class Feedback(Base):
    __tablename__ = "feedback"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    query_log_id: Mapped[int] = mapped_column(ForeignKey("query_logs.id"))
    rating: Mapped[str] = mapped_column(String(8))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


def _normalize_db_url(url):
    """正規化 DB URL 的 driver，讓 Postgres 走 psycopg3。

    Railway 注入的 DATABASE_URL 為 postgres:// 或 postgresql://，
    SQLAlchemy 2.0 對裸 postgresql:// 預設用 psycopg2，但本專案裝的是
    psycopg3（psycopg[binary]）。明確指定 +psycopg dialect 以相容。
    sqlite 或已含 +driver 的 URL 維持不變。
    """
    if url.startswith("postgres://"):
        return "postgresql+psycopg://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        return "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


def _engine_kwargs(url):
    """Postgres 連線池健康設定，解 "SSL error: unexpected eof while reading"。

    Railway 會切斷閒置的 Postgres 連線；SQLAlchemy 預設會把已死的連線留在池中
    並於下次借出時重用 → 觸發 SSL eof / OperationalError。
    pool_pre_ping 在借出前先 ping（SELECT 1）驗連線存活、失效則自動重連；
    pool_recycle 主動汰換超過 30 分鐘的老連線（小於 Railway 閒置上限）。
    （SQLAlchemy 官方亦將 SSL 異常中止列為 pool-invalidating disconnect。）
    sqlite（測試）不需要，保持原樣。
    """
    kwargs = {"future": True}
    if url.startswith("postgresql"):
        kwargs.update(pool_pre_ping=True, pool_recycle=1800)
    return kwargs


def make_session_factory(url):
    url = _normalize_db_url(url)
    engine = create_engine(url, **_engine_kwargs(url))
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)


# ---------- 顯式初始化 ----------

_engine = None  # module-level engine，供 init_db / get_db_status 共用


def init_db(url: str) -> None:
    """顯式建立所有表並記錄初始化過程。

    在應用啟動事件中呼叫，確保 query_logs / feedback 表存在，
    解決 Railway UI 顯示 "relation does not exist" 的問題。
    """
    global _engine
    normalized = _normalize_db_url(url)
    _engine = create_engine(normalized, **_engine_kwargs(normalized))
    logger.info("init_db: 連接資料庫 %s", normalized.split("@")[-1])  # 隱藏帳密
    try:
        Base.metadata.create_all(_engine)
        inspector = inspect(_engine)
        tables = inspector.get_table_names()
        logger.info("init_db: 現有表 → %s", tables)
        for expected in ("query_logs", "feedback"):
            if expected in tables:
                logger.info("init_db: ✓ 表 %s 已存在", expected)
            else:
                logger.warning("init_db: ✗ 表 %s 建立失敗", expected)
    except Exception as exc:  # noqa: BLE001
        logger.error("init_db 失敗: %s", exc)
        raise


# ---------- 診斷工具 ----------

def get_db_status(url: str) -> dict:
    """回傳資料庫連接狀態與表存在情況，供 /api/admin/db-status 使用。

    任何步驟失敗都捕捉並回傳錯誤訊息，不拋例外。
    """
    status: dict = {
        "connected": False,
        "tables": {},
        "error": None,
    }
    try:
        normalized = _normalize_db_url(url)
        engine = _engine or create_engine(normalized, **_engine_kwargs(normalized))
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        status["connected"] = True
        inspector = inspect(engine)
        existing = set(inspector.get_table_names())
        for table in ("query_logs", "feedback"):
            status["tables"][table] = table in existing
    except Exception as exc:  # noqa: BLE001
        status["error"] = str(exc)
    return status
