"""RAG 核心邏輯 — 本檔是學員要補完的「核心缺口」。

tests/test_rag.py 內有現成的失敗測試，你的目標是讓它們全部通過：
    pytest tests/test_rag.py -v

提示與參考見 README.md 的「缺口地圖」。完整答案在 solution branch：
    git show solution:app/rag.py
"""

import json
import re


# gpt-5.4-mini 把 inline 引用標記塞進答案 text，需在輸出前剝除：
#   - 私用區引用字元 U+E200–U+E20F（如 、）
#   - turn-file token（如 turn0file1，亦可能為 fileciteturn0file1 文字形式）
_CITATION_RE = re.compile("\ue200.*?\ue201", re.DOTALL)
_PUA_CITATION_RE = re.compile("[\ue200-\ue20f]")
_TURN_FILE_RE = re.compile(r"(?:filecite)?turn\d+file\d+")


def _strip_citation_markers(text):
    # 官方格式 START(U+E200)…STOP(U+E201) 整段移除；殘缺標記做防禦性清理
    text = _CITATION_RE.sub("", text)
    text = _PUA_CITATION_RE.sub("", text)
    text = _TURN_FILE_RE.sub("", text)
    return text


ANSWER_INSTRUCTIONS = (
    "你是台灣企業法遵研究助理。一律以繁體中文、Markdown 作答，並遵循下列規則。\n"
    "\n"
    "【輸出結構：表格為主、文字為輔（智慧判斷）】\n"
    "1. 先用一句話下結論；涉及解釋空間時明說「此題無單一確定答案」。\n"
    "2. 可結構化的內容（多項對照、條文清單、義務/罰則、要件檢核）一律用 GFM Markdown 表格，"
    "建議欄位：｜項目／情境｜法規依據（法規名稱＋條號）｜重點說明｜注意事項｜，"
    "同一表格欄位數固定、每列一次寫完、儲存格保持單行精簡；儲存格內出現 | 一律寫成全形｜。\n"
    "3. 單一結論、推理論證、需解釋衡量的內容用段落，不要硬塞進表格。\n"
    "\n"
    "【確定性分層：每個依據標明層級】\n"
    "- 【明文】法律明文規定，附法規名稱與條號。\n"
    "- 【解釋/裁量】條文須解釋或屬主管機關裁量；說明理由與前提。\n"
    "- 【實務見解】函釋、判決、通說，註明來源與權威性。\n"
    "- 【語料未涵蓋】檢索不到依據時，明確聲明本系統語料未收錄，不要編造。\n"
    "務必區分『法律確實未明文規定』與『本系統語料未收錄』兩者，不可混為一談。\n"
    "\n"
    "【法律刻意保留的解釋空間】\n"
    "當存在複數合理解釋時，列出各說（甲說／乙說）的論據與適用情境，標示傾向與前提假設，"
    "不得武斷給單一答案。涉及個案事實認定、金額試算或訴訟策略時，加註"
    "「此問題涉及個案判斷，建議諮詢專業律師」。\n"
    "\n"
    "【接地與引用】僅依檢索到的條文／函釋作答，逐點標明出處（法規名稱＋條號），"
    "不確定時用「依現有語料…」而非絕對斷言，嚴禁杜撰條號、金額或來源。\n"
    "結尾固定附：本回答為研究輔助，非正式法律意見。"
)

# gpt-5.4-mini 為推理模型：用 reasoning/text.verbosity，不可傳 temperature/top_p。
# max_output_tokens 同時涵蓋「可見輸出 + 推理 token」（OpenAI Responses API 定義）；
# 推理模型若預算太小，會在答案中途以 status="incomplete" 截斷，故給足輸出預算。
_MODEL_PARAMS = {
    "reasoning": {"effort": "low"},
    "text": {"verbosity": "medium"},
    "max_output_tokens": 4096,
}

# file_search 檢索品質把關（語料含長篇判決後的「抗稀釋」設計）：
# 以 hybrid score（0–1）的 score_threshold 過濾低相關 chunk——相關判決 score 夠高自然
# 補上實務見解，不相關判決被擋在 context 外。刻意「不」拉高 max_num_results（維持預設 20）：
# 判決 chunk 偏大（2048 token），拉高筆數會灌爆 context、徒增成本與首 token 延遲；
# 用 threshold 降噪是零額外成本（甚至更省 token）的抗稀釋手段。
FILE_SEARCH_RANKING_OPTIONS = {"score_threshold": 0.5}

# 串流終結事件型別：Responses API 除了正常完成的 response.completed，還可能以
# response.incomplete（撞 max_output_tokens 等）或 response.failed 結束——三者都帶 .response。
# 原本只認 completed，導致 incomplete 截斷時永不發 final（無 citations/evidence/followups）。
_TERMINAL_EVENT_TYPES = ("response.completed", "response.incomplete", "response.failed")


def _terminal_response(event, event_type):
    """若 event 為串流終結事件，回傳其攜帶的 response 物件；否則回 None。

    用精確比對（非 substring）：事件型別字串已正規化，精確比對同樣容錯，
    且不會被未來新增的事件名稱誤判為終結。
    """
    if event_type in _TERMINAL_EVENT_TYPES:
        return getattr(event, "response", None)
    return None


# retrieved（無 annotations）模式下，最多顯示幾則檢索依據（依分數）。
EVIDENCE_DISPLAY_LIMIT = 5


def _result_url(r):
    """從 file_search result 的 attributes 取官方原文 url（缺口⑥）；無則回 None。

    attributes 為上傳時掛在 vector store file 的 metadata（dict）；
    亦相容物件型別（getattr）。
    """
    attrs = getattr(r, "attributes", None)
    if not attrs:
        return None
    if isinstance(attrs, dict):
        return attrs.get("url")
    return getattr(attrs, "url", None)


def _dedupe_evidence(entries):
    """同檔名去重：保留分數最高的 chunk，維持首次出現順序；移除內部 _file_id 鍵。"""
    best = {}
    order = []
    for e in entries:
        fn = e["filename"]
        if fn not in best:
            best[fn] = e
            order.append(fn)
        elif (e.get("score") or 0) > (best[fn].get("score") or 0):
            best[fn] = e
    out = []
    for fn in order:
        e = dict(best[fn])
        e.pop("_file_id", None)
        out.append(e)
    return out


# 單一交替式：完整 PUA span 在前（於 U+E200 位置先吃整段），純文字 token 在後。
# 一次左到右掃描 → 腳註編號依閱讀順序（避免 span/bare 分兩 pass 造成亂序）。
_CITE_MARKER_RE = re.compile(".*?" r"|(?:filecite)?turn\d+file(\d+)", re.DOTALL)


def _linkify_citation_markers(text, raw_results):
    """把 inline 引用標記轉成可對映來源的腳註 [^k]，回 (新文字, 依首次引用序的檔名清單)。

    gpt-5.4-mini 以 inline 標記（PUA U+E200…filecite U+E202 turnNfileM U+E201，或純文字
    fileciteturnNfileM）表達來源；M 為 file_search_call.results 的索引。原本整段刪除會讓
    「依據」欄變空——改為轉腳註，並讓腳註編號對齊實際被引用的來源（與證據面板一致）。
    超出範圍的 M 安全丟棄（不產生錯誤腳註，避免張冠李戴）。
    """
    order = []
    num = {}

    def footnote_for(m):
        if not (0 <= m < len(raw_results)):
            return None
        fn = raw_results[m].get("filename")
        if not fn:
            return None
        if fn not in num:
            order.append(fn)
            num[fn] = len(order)
        return num[fn]

    def repl(match):
        # group(0) 可能是整段 PUA span 或純文字 token；兩者都能從中找出 turnNfileM。
        mm = re.search(r"turn\d+file(\d+)", match.group(0))
        if mm:
            n = footnote_for(int(mm.group(1)))
            if n:
                return f"[^{n}]"
        return ""

    text = _CITE_MARKER_RE.sub(repl, text)       # 單一 pass，依閱讀順序編號
    text = _PUA_CITATION_RE.sub("", text)        # 殘缺/孤兒 PUA 字元清理
    return text, order


def _evidence_for_files(filenames, all_results):
    """依給定檔名順序，從 all_results 取每檔分數最高的 chunk 組出 evidence（移除內部鍵）。"""
    out = []
    for fn in filenames:
        cands = [e for e in all_results if e["filename"] == fn]
        if not cands:
            continue
        best = max(cands, key=lambda e: e.get("score") or 0)
        e = dict(best)
        e.pop("_file_id", None)
        out.append(e)
    return out


def _response_meta(response):
    """抽出 response_id / usage / status / 截斷資訊（parse_response 與 parse_dual_response 共用）。"""
    usage_obj = getattr(response, "usage", None)
    usage = {
        "input_tokens": getattr(usage_obj, "input_tokens", 0) or 0,
        "output_tokens": getattr(usage_obj, "output_tokens", 0) or 0,
        "total_tokens": getattr(usage_obj, "total_tokens", 0) or 0,
    }
    status = getattr(response, "status", None)
    incomplete = getattr(response, "incomplete_details", None)
    incomplete_reason = getattr(incomplete, "reason", None) if incomplete else None
    return {
        "response_id": getattr(response, "id", None),
        "usage": usage,
        "status": status,
        "truncated": status == "incomplete",
        "incomplete_reason": incomplete_reason,
    }


def parse_response(response):
    """把 Responses API 回應解析為
    {"answer", "citations", "evidence", "evidence_mode", "response_id", "usage"}。

    缺口④：evidence 只保留「真正被引用」的段落——以 message annotations
    （type=file_citation）的 file_id/filename 過濾 file_search_call.results；
    annotations 為空時（gpt-5.4-mini 常見）退回全部檢索結果並標 evidence_mode="retrieved"。
    缺口⑥：evidence 附官方原文 url（取自 result.attributes）。
    """
    answer_parts = []
    citations = []
    seen = set()
    cited_ids = set()  # 被引用的 file_id / filename（annotations 衍生）
    for item in response.output:
        if getattr(item, "type", None) != "message":
            continue
        for content in item.content or []:
            text = getattr(content, "text", None)
            if text:
                answer_parts.append(text)
            for annotation in getattr(content, "annotations", None) or []:
                filename = getattr(annotation, "filename", None)
                file_id = getattr(annotation, "file_id", None)
                if filename and filename not in seen:
                    seen.add(filename)
                    citations.append(filename)
                if filename:
                    cited_ids.add(filename)
                if file_id:
                    cited_ids.add(file_id)

    # 收集全部檢索結果（含 url），稍後依 cited 與否過濾。
    all_results = []
    for item in response.output:
        if getattr(item, "type", None) != "file_search_call":
            continue
        for r in getattr(item, "results", None) or []:
            entry = {
                "filename": getattr(r, "filename", None),
                "text": getattr(r, "text", None),
                "score": getattr(r, "score", None),
                "_file_id": getattr(r, "file_id", None),
            }
            url = _result_url(r)
            if url:
                entry["url"] = url
            all_results.append(entry)

    # 答案文字的 inline 引用標記 → 腳註（修「依據欄空白」根因）。gpt-5.4-mini annotations 常空，
    # 來源資訊只在這些標記裡；轉腳註後依據欄不再空，且編號對齊被引用來源。
    joined_answer = "".join(answer_parts)
    linked_answer, marker_files = _linkify_citation_markers(joined_answer, all_results)

    # evidence 三路：① 答案內 inline 標記（gpt-5.4-mini 實況，最精準）；② annotations；
    # ③ 皆無 → 退回 retrieved top-N（依分數）。前兩者為 cited，來源列與腳註一致。
    if marker_files:
        answer_text = linked_answer
        evidence = _evidence_for_files(marker_files, all_results)
        citations = marker_files
        evidence_mode = "cited"
    elif cited_ids:
        answer_text = _strip_citation_markers(joined_answer)
        kept = [e for e in all_results
                if e["filename"] in cited_ids or e["_file_id"] in cited_ids]
        evidence = _dedupe_evidence(kept)
        evidence_mode = "cited"
    else:
        answer_text = _strip_citation_markers(joined_answer)
        deduped = _dedupe_evidence(all_results)
        deduped.sort(key=lambda e: (e.get("score") or 0), reverse=True)
        evidence = deduped[:EVIDENCE_DISPLAY_LIMIT]
        evidence_mode = "retrieved"
        # citations（來源列）對齊實際顯示的 evidence，不再傾倒全部檔名。
        citations = [e["filename"] for e in evidence]

    return {
        "answer": answer_text,
        "citations": citations,
        "evidence": evidence,
        "evidence_mode": evidence_mode,
        **_response_meta(response),
    }


# —— 檢索分流：判決保障名額（方向1，解決判決結構性弱勢）——
# 判決 score 天生輸法條（context7 + 實測：長篇論述 vs 單條法規，語意密度差 ~12%），
# 單路 file_search top-20 會被高分法條擠光名額（實測一般題判決 0 筆）。故改走兩路獨立
# vector_stores.search：法條路為主力（多取），判決路獨立、低門檻、嚴格上限（少而精——
# context7：多餘低相關 context 反而降低答案品質、燒 token、增延遲）。
LAW_TOP_K = 12
JUDGMENT_TOP_M = 3            # 判決上限：實測 score 高且平坦，>3 多為重複資訊＋噪音
DUAL_LAW_THRESHOLD = 0.5
# 判決專屬門檻（與法條路解耦）：判決弱勢題 score 實測低至 0.35–0.62（PoC 資遣費題），
# 若沿用法條的 0.5 會把這些「正要救」的判決砍掉，故設 0.35。對「法條池門檻上升」免疫。
DUAL_JUDGMENT_THRESHOLD = 0.35


def retrieve_dual(client, query, *, vector_store_id,
                  law_k=LAW_TOP_K, law_threshold=DUAL_LAW_THRESHOLD,
                  judgment_m=JUDGMENT_TOP_M, judgment_threshold=DUAL_JUDGMENT_THRESHOLD):
    """兩路檢索保障判決名額。回傳 {"law": [...], "judgment": [...]}（各為 search result list）。

    法條路（doc_type≠判決）取 law_k 筆為主力；判決路（doc_type=判決）獨立檢索、上限
    judgment_m 筆。用獨立 vector_stores.search 而非 file_search tool，才能對兩類各設名額
    與門檻——否則判決會被天生高分的法條擠光。
    """
    law = client.vector_stores.search(
        vector_store_id=vector_store_id, query=query, max_num_results=law_k,
        filters={"type": "ne", "key": "doc_type", "value": "判決"},
        ranking_options={"score_threshold": law_threshold})
    judgment = client.vector_stores.search(
        vector_store_id=vector_store_id, query=query, max_num_results=judgment_m,
        filters={"type": "eq", "key": "doc_type", "value": "判決"},
        ranking_options={"score_threshold": judgment_threshold})
    return {"law": law.data, "judgment": judgment.data}


def _result_text(r):
    """取一筆 vector_stores.search result 的文字：content[] 串接；無則退回 .text。"""
    content = getattr(r, "content", None)
    if isinstance(content, list) and content:
        return "\n".join((getattr(c, "text", None) or "") for c in content).strip()
    return getattr(r, "text", None) or ""


def _result_doc_type(r):
    attrs = getattr(r, "attributes", None)
    if isinstance(attrs, dict):
        return attrs.get("doc_type")
    return getattr(attrs, "doc_type", None) if attrs else None


def build_context_block(law, judgment):
    """把雙路檢索結果組成「給模型讀」的 context 字串，並回確定性的 sources 清單。

    回 (context_str, sources)；sources 依【法規依據】→【判決見解】序號排列，
    每筆 {filename, text, score, doc_type, url?}，供 parse_dual_response 組 citations/evidence。
    """
    sources = []
    lines = []

    def add_section(title, results):
        if not results:
            return
        lines.append(f"## {title}")
        for r in results:
            idx = len(sources) + 1
            filename = getattr(r, "filename", None) or ""
            text = _result_text(r)
            entry = {
                "filename": filename,
                "text": text,
                "score": getattr(r, "score", None),
                "doc_type": _result_doc_type(r),
            }
            url = _result_url(r)
            if url:
                entry["url"] = url
            sources.append(entry)
            lines.append(f"[來源{idx}]（{filename}）\n{text}")

    add_section("法規依據", law)
    add_section("判決見解", judgment)
    return "\n\n".join(lines), sources


# dual 流引用：模型以 [來源N] 引用我們注入的來源，N 對應 build_context_block 的序號（1-based）。
_SOURCE_MARKER_RE = re.compile(r"\[來源(\d+)\]")


def _linkify_source_markers(text, n_sources):
    """把 [來源N] 轉成腳註 [^k]，回 (新文字, 依首次引用序的 0-based source index 清單)。

    N 超出 1..n_sources 範圍者安全丟棄（不產生錯誤腳註）。腳註編號依閱讀順序。
    """
    order = []
    num = {}

    def repl(m):
        k = int(m.group(1))
        if not (1 <= k <= n_sources):
            return ""
        idx = k - 1
        if idx not in num:
            order.append(idx)
            num[idx] = len(order)
        return f"[^{num[idx]}]"

    return _SOURCE_MARKER_RE.sub(repl, text), order


def parse_dual_response(response, sources):
    """dual 流的解析：citations/evidence 來自 sources（確定性），非解析 model 檢索結果。

    與 parse_response 同輸出契約。模型有標 [來源N] → dual_cited（evidence 為被引用子集）；
    未標 → dual_retrieved（顯示全部檢索段，去重）。
    """
    answer_parts = []
    for item in response.output:
        if getattr(item, "type", None) != "message":
            continue
        for content in item.content or []:
            text = getattr(content, "text", None)
            if text:
                answer_parts.append(text)
    joined = "".join(answer_parts)
    # _linkify_source_markers 同時把有效 [來源N] → [^k]、無效/越界 [來源N] → ""；
    # 兩路都用 linked，確保不殘留任何 [來源N] 標記（包含越界情形）。
    linked, cited_idx = _linkify_source_markers(joined, len(sources))

    if cited_idx:
        answer_text = linked
        evidence = [dict(sources[i]) for i in cited_idx]
        citations = [e["filename"] for e in evidence]
        evidence_mode = "dual_cited"
    else:
        answer_text = _strip_citation_markers(linked)
        evidence = _dedupe_evidence([dict(s) for s in sources])
        citations = [e["filename"] for e in evidence]
        evidence_mode = "dual_retrieved"

    return {
        "answer": answer_text,
        "citations": citations,
        "evidence": evidence,
        "evidence_mode": evidence_mode,
        **_response_meta(response),
    }


def answer_question(client, question, *, vector_store_id, model, previous_response_id=None):
    """以 file_search tool 對 vector store 提問，回傳 parse_response 的結果。

    回傳 {"answer", "citations", "evidence", "response_id", "usage"}。傳入 previous_response_id
    可串接多輪對話（Responses API 的對話狀態）。
    """
    kwargs = _build_answer_kwargs(question, vector_store_id, model, previous_response_id)
    response = client.responses.create(**kwargs)
    return parse_response(response)


def _build_answer_kwargs(question, vector_store_id, model, previous_response_id):
    """組出 file_search 問答呼叫的 kwargs（answer_question / stream_answer 共用）。"""
    kwargs = {
        "model": model,
        "input": question,
        "instructions": ANSWER_INSTRUCTIONS,
        "tools": [{
            "type": "file_search",
            "vector_store_ids": [vector_store_id],
            "ranking_options": FILE_SEARCH_RANKING_OPTIONS,
        }],
        "include": ["file_search_call.results"],
        **_MODEL_PARAMS,
    }
    if previous_response_id:
        kwargs["previous_response_id"] = previous_response_id
    return kwargs


def stream_answer(client, question, *, vector_store_id, model, previous_response_id=None):
    """SSE 串流問答：逐事件 yield {"type":"delta","text":...}，最後 yield {"type":"final", **parse_response(final)}。

    事件型別判斷採容錯策略，相容 OpenAI Responses 串流事件物件的差異。
    """
    kwargs = _build_answer_kwargs(question, vector_store_id, model, previous_response_id)
    kwargs["stream"] = True
    final_response = None
    for event in client.responses.create(**kwargs):
        event_type = getattr(event, "type", None) or ""
        terminal = _terminal_response(event, event_type)
        if terminal is not None:
            final_response = terminal
            continue
        # 只接受答案文字增量事件（output_text.delta）；推理模型的 reasoning/function_call
        # delta 事件雖也帶 .delta 屬性，但屬思考鏈，不可洩漏進答案串流。
        delta = getattr(event, "delta", None)
        if event_type.endswith("output_text.delta") and isinstance(delta, str):
            yield {"type": "delta", "text": delta}
    if final_response is not None:
        yield {"type": "final", **parse_response(final_response)}


async def astream_answer(client, question, *, vector_store_id, model, previous_response_id=None):
    """SSE 串流問答的「非阻塞」版本（缺口①）。

    用 AsyncOpenAI + `async for`，避免同步 Stream 在 async generator 內阻塞 event loop、
    導致 token 批次沖出（非逐字）。事件語意與 stream_answer 相同：
    逐 output_text.delta yield {"type":"delta",...}，最後 yield {"type":"final", **parse_response}。
    """
    kwargs = _build_answer_kwargs(question, vector_store_id, model, previous_response_id)
    kwargs["stream"] = True
    final_response = None
    stream = await client.responses.create(**kwargs)
    async for event in stream:
        event_type = getattr(event, "type", None) or ""
        terminal = _terminal_response(event, event_type)
        if terminal is not None:
            final_response = terminal
            continue
        delta = getattr(event, "delta", None)
        if event_type.endswith("output_text.delta") and isinstance(delta, str):
            yield {"type": "delta", "text": delta}
    if final_response is not None:
        yield {"type": "final", **parse_response(final_response)}


_FOLLOWUP_SCHEMA = {
    "type": "json_schema",
    "name": "followups",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {"questions": {"type": "array", "items": {"type": "string"}}},
        "required": ["questions"],
        "additionalProperties": False,
    },
}


def suggest_followups(client, question, answer, *, model):
    """用 structured outputs 產生 3 題法遵追問建議。與主問答分離，不影響 citation annotations。"""
    prompt = (
        "你是法遵助理。根據以下問答，產生 3 個使用者可能想接著問的繁體中文追問問題，每題簡短。\n"
        f"問題：{question}\n回答：{answer}"
    )
    response = client.responses.create(model=model, input=prompt, text={"format": _FOLLOWUP_SCHEMA})
    # 模型故障/截斷可能讓 output_text 缺失或非合法 JSON；追問建議屬輔助功能，不可因此拋例外。
    try:
        data = json.loads(getattr(response, "output_text", "") or "")
    except (json.JSONDecodeError, TypeError):
        return {"questions": []}
    if not isinstance(data, dict) or not isinstance(data.get("questions"), list):
        return {"questions": []}
    return data
