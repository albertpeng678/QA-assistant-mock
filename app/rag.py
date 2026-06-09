"""RAG 核心邏輯 — 本檔是學員要補完的「核心缺口」。

tests/test_rag.py 內有現成的失敗測試，你的目標是讓它們全部通過：
    pytest tests/test_rag.py -v

提示與參考見 README.md 的「缺口地圖」。完整答案在 solution branch：
    git show solution:app/rag.py
"""

import json


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
    return {
        "answer": "".join(answer_parts),
        "citations": citations,
        "evidence": evidence,
        "response_id": getattr(response, "id", None),
    }


def answer_question(client, question, *, vector_store_id, model, previous_response_id=None):
    """以 file_search tool 對 vector store 提問，回傳 parse_response 的結果。

    回傳 {"answer", "citations", "evidence", "response_id"}。傳入 previous_response_id
    可串接多輪對話（Responses API 的對話狀態）。
    """
    kwargs = {
        "model": model,
        "input": question,
        "tools": [{"type": "file_search", "vector_store_ids": [vector_store_id]}],
        "include": ["file_search_call.results"],
    }
    if previous_response_id:
        kwargs["previous_response_id"] = previous_response_id
    response = client.responses.create(**kwargs)
    return parse_response(response)


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
