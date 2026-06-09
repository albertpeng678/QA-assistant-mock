from app.db import Base, QueryLog, Feedback, make_session_factory


def test_querylog_and_feedback_roundtrip():
    Session = make_session_factory("sqlite+pysqlite:///:memory:")
    with Session() as s:
        log = QueryLog(question="個資外洩?", answer="應通知當事人。",
                       citations=["個資法-第12條.txt"], evidence=[{"filename": "個資法-第12條.txt", "score": 0.91}],
                       citation_count=1, has_answer=True, latency_ms=1200,
                       input_tokens=10, output_tokens=20, total_tokens=30,
                       model="gpt-5.4-mini", response_id="resp_abc", previous_response_id=None, error=None)
        s.add(log)
        s.commit()
        fb = Feedback(query_log_id=log.id, rating="up")
        s.add(fb)
        s.commit()
        assert s.query(QueryLog).count() == 1
        assert s.query(Feedback).first().rating == "up"
        assert s.query(QueryLog).first().created_at is not None
