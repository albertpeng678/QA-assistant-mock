"""RAG 核心邏輯 — 本檔是學員要補完的「核心缺口」。

tests/test_rag.py 內有現成的失敗測試，你的目標是讓它們全部通過：
    pytest tests/test_rag.py -v

提示與參考見 README.md 的「缺口地圖」。完整答案在 solution branch：
    git show solution:app/rag.py
"""

import asyncio
import json
import re

from app.capability import load_manifest, capability_answer, classify_intent, aclassify_intent, classify_tier, aclassify_tier


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
    "你是台灣企業法遵研究助理。一律繁體中文、Markdown 作答，力求白話、精簡、好讀。\n"
    "\n"
    "【倒金字塔：結論優先】\n"
    "1. 第一行＝一句白話結論（≤40 字），講清楚「可以做什麼＋關鍵期限/數字（用 **粗體**）」；"
    "否定條件、除外、定義一律放到後面的條列，不要塞進第一行。\n"
    "   例：「**公開滿 18 小時後**才能買賣股票（證交法§157-1）。」\n"
    "\n"
    "【重點條列】\n"
    "2. 結論後列 2–4 個重點（bullet），每點一句；白話表達，法規以括號附條號（縮寫如 §157-1），"
    "例：「離職未滿 6 個月者也算內部人（§157-1）」。\n"
    "\n"
    "【表格：僅比較型】\n"
    "3. 只有『3 項以上需橫向比較』才用表格，且**欄數 ≤ 3**；否則用條列或句子。表格內也用白話＋§縮寫，勿貼整段法條原文。\n"
    "\n"
    "【白話與接地】\n"
    "4. 用受高中教育者可懂的語言，平均句長盡量 ≤30 字；避免整段貼法條原文，改白話＋括號條號。\n"
    "5. 僅依檢索到的依據作答，逐點標出處（§條號）；不確定用「依現有語料…」，嚴禁杜撰條號、金額或來源。\n"
    "6. 務必區分『法律確實未明文規定』與『本系統語料未收錄』，不可混為一談。\n"
    "\n"
    "【不確定與免責：末端 caveat】\n"
    "7. 涉及個案事實認定、金額試算、訴訟策略，以及不確定之處，集中放在答案末端，用一個 Markdown 引言區塊（以 > 起始）。"
    "結尾固定一句（同一引言區塊內）：本回答為研究輔助，非正式法律意見。\n"
    "\n"
    "注意：不要在內文逐句標註【明文】【解釋】等層級字樣（層級由系統另外標示）。"
)

# dual 流的引用規約（附加在 instructions 後）：模型讀注入的 [來源N] 並回標 [來源N]。
CITATION_PROTOCOL = (
    "\n\n【引用方式】下方 input 會附「# 檢索到的依據」，內含【法規依據】與【判決見解】"
    "兩區、各段以 [來源N] 標號。作答時，請在每個論點對應的句末標註其依據的 [來源N]"
    "（可多個，如 [來源1][來源3]）。只依這些來源作答；若來源不足以回答，明確聲明語料未涵蓋，不得杜撰。"
)

# 延續輪（有 previous_response_id）附加：延續句（「好啊請幫我生成對照表」）本身無實質
# 內容，拿它檢索常撈到無關來源；引用規約「只依這些來源作答」會迫使模型做錯主題的表
# （實測：內線交易追問被公司登記函釋帶歪）。延續輪放行對話脈絡優先。
CONTINUATION_GUIDANCE = (
    "\n\n【多輪延續】本輪是先前對話的延續。若使用者在回應你先前提出的建議（如「好啊」"
    "「請幫我生成」），請依先前對話的主題與已給的依據完成該請求；此時若下方檢索依據"
    "與對話主題明顯不符，忽略之、不引用 [來源N]，沿用先前回合已標註的依據即可。"
)

# gpt-5.4-mini 為推理模型：用 reasoning/text.verbosity，不可傳 temperature/top_p。
# max_output_tokens 同時涵蓋「可見輸出 + 推理 token」（OpenAI Responses API 定義）；
# 推理模型若預算太小，會在答案中途以 status="incomplete" 截斷，故給足輸出預算。
_MODEL_PARAMS = {
    "reasoning": {"effort": "low"},
    "text": {"verbosity": "low"},
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
# 法條路門檻：原 0.5 實測產生懸崖效應——「個資外洩通報」「營業秘密求償」等題的最相關
# 文件落在 0.45–0.49 被整批切掉，context 退化成不相關高分文件＋判決 → gap 答案。
# 降至 0.35（與判決路對稱）：好查詢 top-12 早被 ≥0.5 高分塞滿、尾段擠不進，不受影響；
# 被餓死的查詢救回臨界相關文件。語料量倍增（判決 1.2 萬）後 hybrid 分數整體偏移亦獲緩衝。
DUAL_LAW_THRESHOLD = 0.35
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


async def aretrieve_dual(client, query, *, vector_store_id,
                         law_k=LAW_TOP_K, law_threshold=DUAL_LAW_THRESHOLD,
                         judgment_m=JUDGMENT_TOP_M, judgment_threshold=DUAL_JUDGMENT_THRESHOLD):
    """retrieve_dual 的 async 版（AsyncOpenAI 用）：兩路獨立，用 asyncio.gather 並行檢索
    （串流前等待時間從 law+judgment 降為 max(law, judgment)）。"""
    law, judgment = await asyncio.gather(
        client.vector_stores.search(
            vector_store_id=vector_store_id, query=query, max_num_results=law_k,
            filters={"type": "ne", "key": "doc_type", "value": "判決"},
            ranking_options={"score_threshold": law_threshold}),
        client.vector_stores.search(
            vector_store_id=vector_store_id, query=query, max_num_results=judgment_m,
            filters={"type": "eq", "key": "doc_type", "value": "判決"},
            ranking_options={"score_threshold": judgment_threshold}),
    )
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


_CHUNKS_PER_FILE = 2  # 同檔合併進 context 的 chunk 上限：1 會讓長判決失去細節，>2 重複噪音


def _dedupe_results(results, seen):
    """同檔名收斂成一組（最高分前 _CHUNKS_PER_FILE 個 chunk），保留首見順序；seen 跨區段累積。

    判決路常對同一份判決回多個 chunk（實測：個資外洩題同檔 ×3），若每個 chunk 各成
    一個 [來源N]，citations/來源列就會出現重複檔名。改為同檔合併單一來源，但保留前 2
    高分 chunk 串進 context——兼顧「來源唯一」與「長判決 context 豐富度」。
    回傳 list[list[result]]：外層依首見序、內層依分數降冪（首筆=最高分，供取 score/url）。
    """
    groups = {}
    order = []
    for r in results:
        fn = getattr(r, "filename", None) or ""
        if fn in seen:
            continue
        if fn not in groups:
            groups[fn] = []
            order.append(fn)
        groups[fn].append(r)
    seen.update(order)
    return [
        sorted(groups[fn], key=lambda r: getattr(r, "score", None) or 0,
               reverse=True)[:_CHUNKS_PER_FILE]
        for fn in order
    ]


def build_context_block(law, judgment):
    """把雙路檢索結果組成「給模型讀」的 context 字串，並回確定性的 sources 清單。

    回 (context_str, sources)；sources 依【法規依據】→【判決見解】序號排列，
    每筆 {filename, text, score, doc_type, url?}，供 parse_dual_response 組 citations/evidence。
    同檔名多 chunk 先去重（留最高分），避免來源列／citations 重複。
    """
    sources = []
    lines = []
    seen = set()
    law = _dedupe_results(law, seen)
    judgment = _dedupe_results(judgment, seen)

    def add_section(title, groups):
        if not groups:
            return
        lines.append(f"## {title}")
        for chunks in groups:           # 同檔的 top chunk 群（首筆=最高分）
            idx = len(sources) + 1
            best = chunks[0]
            filename = getattr(best, "filename", None) or ""
            text = "\n…\n".join(_result_text(c) for c in chunks)
            entry = {
                "filename": filename,
                "text": text,
                "score": getattr(best, "score", None),
                "doc_type": _result_doc_type(best),
            }
            url = _result_url(best)
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


def capability_result(manifest):
    """meta 問題的回傳：與 parse_dual_response 同契約，evidence_mode=capability、無引用。"""
    return {
        "answer": capability_answer(manifest),
        "citations": [],
        "evidence": [],
        "evidence_mode": "capability",
        "response_id": None,
        "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
        "status": "completed",
        "truncated": False,
        "incomplete_reason": None,
    }


def answer_question(client, question, *, vector_store_id, model, previous_response_id=None):
    """雙路檢索 → 自組 context → Responses API（不帶 file_search tool）→ parse_dual_response。

    回傳 {"answer","citations","evidence","evidence_mode","response_id","usage",...}。
    previous_response_id 可串接多輪對話。
    """
    # intent gate 只在首輪：延續輪（有 previous_response_id）的「好啊請幫我生成」等
    # 肯定句無法條關鍵字，實測 100% 被誤判 meta_capability 而吐罐頭答案、攔截多輪。
    if previous_response_id is None and \
            classify_intent(client, question, model=model) == "meta_capability":
        return capability_result(load_manifest())
    routes = retrieve_dual(client, question, vector_store_id=vector_store_id)
    context, sources = build_context_block(routes["law"], routes["judgment"])
    kwargs = _build_dual_kwargs(question, context, model, previous_response_id)
    response = client.responses.create(**kwargs)
    result = parse_dual_response(response, sources)
    result["tier"] = classify_tier(client, question, context, model=model)
    return result


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


def _build_dual_kwargs(question, context, model, previous_response_id):
    """組 dual 流的 responses.create kwargs：context 注入 input、不帶 file_search tool。

    兩路皆無檢索結果（context 空）時：不注入來源，也不附引用規約——避免要求模型
    引用不存在的 [來源N]（模型仍會依 ANSWER_INSTRUCTIONS 聲明語料未涵蓋）。
    """
    if context:
        user_input = f"{question}\n\n# 檢索到的依據\n{context}"
        instructions = ANSWER_INSTRUCTIONS + CITATION_PROTOCOL
    else:
        user_input = question
        instructions = ANSWER_INSTRUCTIONS
    if previous_response_id:
        instructions += CONTINUATION_GUIDANCE
    kwargs = {
        "model": model,
        "input": user_input,
        "instructions": instructions,
        **_MODEL_PARAMS,
    }
    if previous_response_id:
        kwargs["previous_response_id"] = previous_response_id
    return kwargs


def stream_answer(client, question, *, vector_store_id, model, previous_response_id=None):
    """SSE 串流問答（同步）：雙路檢索 → 串流生成 → final 由 parse_dual_response 組。"""
    routes = retrieve_dual(client, question, vector_store_id=vector_store_id)
    context, sources = build_context_block(routes["law"], routes["judgment"])
    kwargs = _build_dual_kwargs(question, context, model, previous_response_id)
    kwargs["stream"] = True
    final_response = None
    for event in client.responses.create(**kwargs):
        event_type = getattr(event, "type", None) or ""
        terminal = _terminal_response(event, event_type)
        if terminal is not None:
            final_response = terminal
            continue
        delta = getattr(event, "delta", None)
        if event_type.endswith("output_text.delta") and isinstance(delta, str):
            yield {"type": "delta", "text": delta}
    if final_response is not None:
        yield {"type": "final", **parse_dual_response(final_response, sources)}


async def astream_answer(client, question, *, vector_store_id, model, previous_response_id=None):
    """SSE 串流問答（非阻塞 async）：先 await 雙路檢索組 context，再 async 串流生成。"""
    # intent gate 只在首輪（同 answer_question）：延續輪直接走檢索問答，不被罐頭攔截。
    if previous_response_id is None and \
            await aclassify_intent(client, question, model=model) == "meta_capability":
        result = capability_result(load_manifest())
        yield {"type": "delta", "text": result["answer"]}
        yield {"type": "final", **result}
        return
    # 等待UX：階段事件掛真實管線節點（NN/g：>10s 等待要顯示真步驟，不演戲）。
    yield {"type": "stage", "stage": "retrieving"}
    routes = await aretrieve_dual(client, question, vector_store_id=vector_store_id)
    context, sources = build_context_block(routes["law"], routes["judgment"])
    yield {"type": "stage", "stage": "retrieved",
           "law_count": sum(1 for s in sources if s.get("doc_type") != "判決"),
           "judgment_count": sum(1 for s in sources if s.get("doc_type") == "判決")}
    kwargs = _build_dual_kwargs(question, context, model, previous_response_id)
    kwargs["stream"] = True
    final_response = None
    stream = await client.responses.create(**kwargs)
    yield {"type": "stage", "stage": "generating"}
    tier_task = asyncio.create_task(aclassify_tier(client, question, context, model=model))
    try:
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
            final = parse_dual_response(final_response, sources)
            try:
                final["tier"] = await tier_task
            except Exception:  # noqa: BLE001
                final["tier"] = None
            yield {"type": "final", **final}
    finally:
        # 涵蓋所有離開路徑（含使用者按停止 → client abort → GeneratorExit）：
        # tier_task 不可成孤兒，否則多燒一次 API call 並留下未取回例外警告。
        if not tier_task.done():
            tier_task.cancel()


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
