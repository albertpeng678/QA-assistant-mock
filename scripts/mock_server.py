"""僅供前端離線實機驗收用的 mock server（不進 production）。

以固定假資料模擬 /api/ask/stream（SSE）、/api/suggest、/api/feedback、/api/source，
讓 Playwright 能在無 OpenAI/DB 的情況下驗收 UI 與互動。
用法: python scripts/mock_server.py  → http://localhost:8001
"""
import json
import time
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

app = FastAPI()
STATIC = Path(__file__).resolve().parent.parent / "static"
app.mount("/static", StaticFiles(directory=STATIC), name="static")

ANSWER = (
    "發生個人資料外洩時，應採取下列步驟：\n\n"
    "- **查明與通知當事人**：應於查明後，以適當方式通知當事人[^1]。\n"
    "- **通報主管機關**：屬公務機關者，並應通報目的事業主管機關[^2]。\n"
    "  - 通報內容應包含外洩事實、影響範圍與已採行的應變措施。\n"
    "- **屆期未改正得按次處罰**[^3]。\n\n"
    "建議同時保存完整的事件處理紀錄，以備查核。"
)
EVIDENCE = [
    {"filename": "個人資料保護法-第12條.txt", "text": "公務機關或非公務機關違反本法規定，致個人資料被竊取、洩漏、竄改…應查明後以適當方式通知當事人。", "score": 0.91},
    {"filename": "個人資料保護法-第22條.txt", "text": "主管機關為執行資料檔案安全維護之檢查，得令限期改正。", "score": 0.84},
    {"filename": "個人資料保護法-第48條.txt", "text": "非公務機關違反限期改正之命令者，按次處新臺幣二萬元以上罰鍰。", "score": 0.79},
]
CITES = [e["filename"] for e in EVIDENCE]


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/api/ask/stream")
async def ask_stream(req: Request):
    def gen():
        for ch in [ANSWER[i:i+12] for i in range(0, len(ANSWER), 12)]:
            yield "data: " + json.dumps({"type": "delta", "text": ch}, ensure_ascii=False) + "\n\n"
            time.sleep(0.04)
        final = {"type": "final", "answer": ANSWER, "citations": CITES,
                 "evidence": EVIDENCE, "response_id": "resp_mock", "query_log_id": 1}
        yield "data: " + json.dumps(final, ensure_ascii=False) + "\n\n"
        yield "data: [DONE]\n\n"
    return StreamingResponse(gen(), media_type="text/event-stream")


@app.post("/api/suggest")
async def suggest(req: Request):
    return {"questions": ["通報主管機關的法定期限？", "罰鍰金額範圍多少？", "民事賠償責任如何認定？"]}


@app.post("/api/feedback")
async def feedback(req: Request):
    return {"ok": True}


@app.get("/api/source/{filename}")
def source(filename: str):
    match = next((e for e in EVIDENCE if e["filename"] == filename), None)
    text = (match["text"] + "\n\n（本段為 mock 全文，實際部署會回傳 data/ 內完整條文。）") if match else "（找不到該來源）"
    return {"filename": filename, "text": text}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8001, log_level="warning")
