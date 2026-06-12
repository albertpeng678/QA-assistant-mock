"""僅供前端離線實機驗收用的 mock server（不進 production）。

以固定假資料模擬 /api/ask/stream（SSE）、/api/suggest、/api/feedback、/api/source，
讓 Playwright 能在無 OpenAI/DB 的情況下驗收 UI 與互動。
用法: python scripts/mock_server.py  → http://localhost:8001
"""
import json
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

app = FastAPI()
STATIC = Path(__file__).resolve().parent.parent / "static"
app.mount("/static", StaticFiles(directory=STATIC), name="static")

# 反映新輸出：BLUF-first + 表格（≤3欄）+ 確定性層級標籤（缺口⑦）+ 腳註引用 + 末尾 blockquote 免責
ANSWER = (
    "個資外洩經查明後應**即時通知當事人**，達一定規模並須通報主管機關（個資法§12）。\n\n"
    "- 知悉外洩、查明後應即時通知當事人；達一定規模並通報主管機關（§12）。[^1]\n"
    "- 主管機關得令限期改正資料安全維護缺失（§22）。[^2]\n"
    "- 違反限期改正命令者，按次處 2 萬元以上罰鍰（§48）。[^3]\n"
    "- 實務上未即時通知，可能構成違反通知義務並須負損害賠償（臺北地院判決）。[^4]\n\n"
    "| 對象 | 通知時機 | 依據 |\n|---|---|---|\n"
    "| 當事人 | 查明後即時 | `§12` |\n| 主管機關 | 達一定規模時 | `§12` |\n| 限期未改正 | 按次處罰鍰 | `§48` |\n\n"
    "> 提醒：「一定規模」通報門檻依各主管機關辦法認定，建議確認所屬主管機關規範。本回答為研究輔助，非正式法律意見。"
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
    {"filename": "臺北地院-判決-個資外洩損害賠償案.txt",
     "text": "本院認被告未於知悉外洩後即時通知當事人，違反個人資料保護法第12條通知義務，"
             "應負損害賠償責任。惟原告請求之金額，審酌其實際損害…",
     "score": 0.41, "doc_type": "判決", "file_id": "file_mockjudgment01"},
]

# 判決原文全文（mock /api/source_vs：production 走 vector_stores.files.content）
JUDGMENT_FULL_TEXT = (
    "來源:司法院裁判書系統|效力:判決|字號:TPDV,112,訴,999|裁判日:2024-06-26|母法:個人資料保護法\n\n"
    "臺灣臺北地方法院民事判決\n112年度訴字第999號\n"
    "原告主張：被告經營網路平台，因系統疏失致原告個人資料外洩…\n"
    "本院判斷：按個人資料保護法第12條，公務機關或非公務機關違反本法規定，"
    "致個人資料被竊取、洩漏、竄改或其他侵害者，應查明後以適當方式通知當事人。"
    "被告於知悉外洩事故後逾二月始通知當事人，難認即時，違反通知義務明確…\n"
    "綜上所述，原告依個資法第29條請求被告賠償，為有理由，應予准許。\n"
    "中華民國113年6月26日\n民事第三庭法官"
)
CITES = [e["filename"] for e in EVIDENCE]

# 職能揭露 mock：能力清單 + 能力回答 + 缺口答案
CAPABILITY = {"doc_type_counts": {"法條": 1163, "施行細則": 197, "函釋": 666, "FAQ": 169, "判決": 106},
              "categories": ["個資", "勞動", "公司治理", "公平交易", "營業秘密", "消費者保護", "洗錢防制"],
              "cutoff": "2026-06", "total": 2301}
META_ANSWER = (
    "## 我能幫你的\n條文查詢與解釋、義務／罰則對照、合規要件檢核、實務見解佐證（函釋・判決）。\n\n"
    "## 涵蓋語料（皆真實官方開放資料）\n"
    "**文件型別**：法條 1,163・施行細則 197・函釋 666・FAQ 169・判決 106\n\n"
    "**法令領域**：個資・勞動・公司治理・公平交易・營業秘密・消費者保護・洗錢防制\n\n"
    "## 我不能做（請另尋專業）\n提供正式法律意見、代理訴訟、個案金額試算、最新即時判決。\n\n"
    "本回答為研究輔助，非正式法律意見。"
)
GAP_ANSWER = (
    "個資外洩是否「即時」通知，個案會依外洩規模與風險裁量認定；屆期未改正得按次處罰鍰"
    "（個資法§48），惟具體抗辯須視個案事實而定。\n\n本回答為研究輔助，非正式法律意見。"
)


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/api/capability")
def capability():
    return CAPABILITY


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
    # 職能揭露鉤子：__META__ 能力回答（capability 模式、不檢索）；__GAP__ 缺口提示（dual_retrieved）。
    meta = "__META__" in question
    gap = "__GAP__" in question
    nocover = "__NOCOVER__" in question  # 語料未涵蓋：無證據、語料未涵蓋層級
    # 等待UX鉤子：__SLOWGEN__ 把 generating→首 token 拉長到 13s，驗收 >12s 安撫文案＋停止鍵。
    slowgen = "__SLOWGEN__" in question

    def gen():
        if nocover:
            ans = ("【語料未涵蓋】本系統語料目前未收錄此主題的條文、函釋或判決，"
                   "無法提供可審查的依據。建議查詢其他法規資料庫或諮詢專業律師。\n\n"
                   "本回答為研究輔助，非正式法律意見。")
            for ch in [ans[i:i+12] for i in range(0, len(ans), 12)]:
                yield "data: " + json.dumps({"type": "delta", "text": ch}, ensure_ascii=False) + "\n\n"
                time.sleep(0.02)
            final = {"type": "final", "answer": ans, "citations": [], "evidence": [],
                     "evidence_mode": "dual_retrieved", "tier": "語料未涵蓋",
                     "response_id": "resp_nc", "query_log_id": 4}
            yield "data: " + json.dumps(final, ensure_ascii=False) + "\n\n"
            yield "data: [DONE]\n\n"
            return
        if meta:
            yield "data: " + json.dumps({"type": "delta", "text": META_ANSWER}, ensure_ascii=False) + "\n\n"
            final = {"type": "final", "answer": META_ANSWER, "citations": [], "evidence": [],
                     "evidence_mode": "capability", "response_id": "resp_meta", "query_log_id": None}
            yield "data: " + json.dumps(final, ensure_ascii=False) + "\n\n"
            yield "data: [DONE]\n\n"
            return
        if gap:
            for ch in [GAP_ANSWER[i:i+12] for i in range(0, len(GAP_ANSWER), 12)]:
                yield "data: " + json.dumps({"type": "delta", "text": ch}, ensure_ascii=False) + "\n\n"
                time.sleep(0.02)
            final = {"type": "final", "answer": GAP_ANSWER, "citations": [EVIDENCE[0]["filename"]],
                     "evidence": [EVIDENCE[0]], "evidence_mode": "dual_retrieved", "tier": "解釋/裁量",
                     "response_id": "resp_gap", "query_log_id": 3}
            yield "data: " + json.dumps(final, ensure_ascii=False) + "\n\n"
            yield "data: [DONE]\n\n"
            return
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
                     "evidence": [EVIDENCE[0]], "evidence_mode": "dual_cited",
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
        # 等待UX：階段事件（與 astream_answer 同契約），mock 以小延遲讓階段推進可見。
        yield "data: " + json.dumps({"type": "stage", "stage": "retrieving"}, ensure_ascii=False) + "\n\n"
        time.sleep(0.6)
        yield "data: " + json.dumps({"type": "stage", "stage": "retrieved",
                                     "law_count": 3, "judgment_count": 1}, ensure_ascii=False) + "\n\n"
        time.sleep(0.5)
        yield "data: " + json.dumps({"type": "stage", "stage": "generating"}, ensure_ascii=False) + "\n\n"
        time.sleep(13 if slowgen else 0.7)
        for ch in [ANSWER[i:i+12] for i in range(0, len(ANSWER), 12)]:
            yield "data: " + json.dumps({"type": "delta", "text": ch}, ensure_ascii=False) + "\n\n"
            time.sleep(0.04)
        if not no_final:
            final = {"type": "final", "answer": ANSWER, "citations": CITES,
                     "evidence": EVIDENCE, "evidence_mode": "dual_cited", "tier": "明文",
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


@app.get("/api/source_vs/{file_id}")
def source_vs(file_id: str):
    if file_id == "file_mockjudgment01":
        return {"file_id": file_id, "text": JUDGMENT_FULL_TEXT}
    raise HTTPException(status_code=404, detail="原文取回失敗")


@app.get("/api/source/{filename}")
def source(filename: str):
    match = next((e for e in EVIDENCE if e["filename"] == filename), None)
    text = (match["text"] + "\n\n（本段為 mock 全文，實際部署會回傳 data/ 內完整條文。）") if match else "（找不到該來源）"
    return {"filename": filename, "text": text}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8001, log_level="warning")
