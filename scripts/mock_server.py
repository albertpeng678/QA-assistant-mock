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

# 反映新輸出：表格為主（缺口②③）+ 確定性層級標籤（缺口⑦）+ 腳註引用
ANSWER = (
    "發生個人資料外洩時，應依下列步驟通知當事人並通報主管機關：\n\n"
    "| 步驟 | 法規依據 | 重點說明 |\n"
    "|---|---|---|\n"
    "| 查明並通知當事人 | 個人資料保護法第12條 | 應於查明後以適當方式通知當事人[^1] |\n"
    "| 通報主管機關 | 個人資料保護法第22條 | 公務機關應通報目的事業主管機關[^2] |\n"
    "| 屆期未改正按次處罰 | 個人資料保護法第48條 | 得按次處新臺幣罰鍰[^3] |\n\n"
    "【明文】上述通知與通報義務於條文有明文規定。\n\n"
    "【解釋/裁量】「適當方式」屬解釋空間，實務上依個案性質認定。\n\n"
    "本回答為研究輔助，非正式法律意見。"
)
_URL = "https://law.moj.gov.tw/LawClass/LawSingle.aspx?pcode=I0050021&flno={}"
EVIDENCE = [
    {"filename": "個人資料保護法-第12條.txt",
     "text": "公務機關或非公務機關知悉所保有之個人資料被竊取、竄改、毀損、滅失或洩漏時，應通知當事人。\n前項情形符合一定通報範圍者，應通報下列機關：\n一、公務機關：向主管機關通報。\n二、非公務機關：向主管機關通報。",
     "score": 0.91, "url": _URL.format("12")},
    {"filename": "個人資料保護法-第22條.txt",
     "text": "主管機關為執行資料檔案安全維護之檢查，得令限期改正。",
     "score": 0.84, "url": _URL.format("22")},
    {"filename": "個人資料保護法-第48條.txt",
     "text": "非公務機關違反限期改正之命令者，按次處新臺幣二萬元以上罰鍰。",
     "score": 0.79, "url": _URL.format("48")},
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
    body = await req.json()
    question = (body or {}).get("question", "") or ""
    # 測試鉤子：問題含 __NOFINAL__ 時只送 delta + [DONE]、不送 final，
    # 模擬後端截斷/連線中斷，用以驗收前端的「降級兜底 finalize」。
    no_final = "__NOFINAL__" in question
    # 測試鉤子：__FAILEMPTY__ 模擬 response.failed/無可見輸出——送出空 final + status=failed，
    # 用以驗收前端「不留空白泡泡、顯示誠實錯誤」。
    fail_empty = "__FAILEMPTY__" in question
    # 測試鉤子：__MARKERS__ 模擬 gpt-5.4-mini 把依據欄純用 inline 引用標記表達——
    # delta 串流帶 PUA 標記、但 final.answer 為後端轉好的腳註版，驗收前端「最終以 evt.answer 渲染、
    # 依據欄不空、標記轉成可點腳註」。
    markers = "__MARKERS__" in question

    def gen():
        if markers:
            CITE = "fileciteturn0file0"  # 模擬 OpenAI inline 引用標記
            stream_text = (
                "| 面向 | 依據 |\n|---|---|\n"
                f"| 客戶身分確認 | {CITE} |\n"
                f"| 交易監控 | {CITE} |\n"
            )
            for ch in [stream_text[i:i+10] for i in range(0, len(stream_text), 10)]:
                yield "data: " + json.dumps({"type": "delta", "text": ch}, ensure_ascii=False) + "\n\n"
                time.sleep(0.02)
            final_answer = (
                "| 面向 | 依據 |\n|---|---|\n"
                "| 客戶身分確認 | [^1] |\n"
                "| 交易監控 | [^1] |\n"
            )
            final = {"type": "final", "answer": final_answer, "citations": [EVIDENCE[0]["filename"]],
                     "evidence": [EVIDENCE[0]], "evidence_mode": "cited",
                     "response_id": "resp_mk", "query_log_id": 2}
            yield "data: " + json.dumps(final, ensure_ascii=False) + "\n\n"
            yield "data: [DONE]\n\n"
            return
        if fail_empty:
            final = {"type": "final", "answer": "", "citations": [], "evidence": [],
                     "evidence_mode": "retrieved", "status": "failed", "truncated": False,
                     "response_id": "resp_fail", "query_log_id": None}
            yield "data: " + json.dumps(final, ensure_ascii=False) + "\n\n"
            yield "data: [DONE]\n\n"
            return
        for ch in [ANSWER[i:i+12] for i in range(0, len(ANSWER), 12)]:
            yield "data: " + json.dumps({"type": "delta", "text": ch}, ensure_ascii=False) + "\n\n"
            time.sleep(0.04)
        if not no_final:
            final = {"type": "final", "answer": ANSWER, "citations": CITES,
                     "evidence": EVIDENCE, "evidence_mode": "cited",
                     "response_id": "resp_mock", "query_log_id": 1}
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
