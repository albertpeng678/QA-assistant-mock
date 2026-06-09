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
    "你是台灣企業法遵研究助理。回答務必：以繁體中文；使用 Markdown 結構——"
    "重點用條列（- ），有層次時用巢狀縮排，段落之間以空行分隔；先結論後依據；"
    "引用條文時標明法規名稱與條號。本回答為研究輔助，非正式法律意見。"
)

# gpt-5.4-mini 為推理模型：用 reasoning/text.verbosity，不可傳 temperature/top_p。
_MODEL_PARAMS = {
    "reasoning": {"effort": "low"},
    "text": {"verbosity": "medium"},
    "max_output_tokens": 2048,
}


def parse_response(response):
    """把 Responses API 回應解析為 {"answer": str, "citations": [str], "evidence": [{"filename", "text", "score"}], "response_id": str|None}。

    🎯 缺口 1（核心，有測試）：實作此函式，讓 test_rag.py 的 parse_response 測試通過。
    """
    answer_parts = []
    citations = []
    seen = set()
    for item in response.output:
        if getattr(item, "type", None) != "message":
            continue
        for content in item.content or []:
            text = getattr(content, "text", None)
            if text:
                answer_parts.append(text)
            for annotation in getattr(content, "annotations", None) or []:
                filename = getattr(annotation, "filename", None)
                if filename and filename not in seen:
                    seen.add(filename)
                    citations.append(filename)
    evidence = []
    for item in response.output:
        if getattr(item, "type", None) != "file_search_call":
            continue
        for r in getattr(item, "results", None) or []:
            evidence.append({
                "filename": getattr(r, "filename", None),
                "text": getattr(r, "text", None),
                "score": getattr(r, "score", None),
            })
    # annotations 收集後若為空（gpt-5.4-mini 實測 annotations 常為空陣列），
    # fallback 從 evidence 檔名衍生 citations（去重保序）。
    if not citations:
        for ev in evidence:
            filename = ev.get("filename")
            if filename and filename not in seen:
                seen.add(filename)
                citations.append(filename)
    usage_obj = getattr(response, "usage", None)
    usage = {
        "input_tokens": getattr(usage_obj, "input_tokens", 0) or 0,
        "output_tokens": getattr(usage_obj, "output_tokens", 0) or 0,
        "total_tokens": getattr(usage_obj, "total_tokens", 0) or 0,
    }
    return {
        "answer": _strip_citation_markers("".join(answer_parts)),
        "citations": citations,
        "evidence": evidence,
        "response_id": getattr(response, "id", None),
        "usage": usage,
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
        "tools": [{"type": "file_search", "vector_store_ids": [vector_store_id]}],
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
        if "response.completed" in event_type and getattr(event, "response", None) is not None:
            final_response = event.response
            continue
        # 只接受答案文字增量事件（output_text.delta）；推理模型的 reasoning/function_call
        # delta 事件雖也帶 .delta 屬性，但屬思考鏈，不可洩漏進答案串流。
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
    return json.loads(response.output_text)
