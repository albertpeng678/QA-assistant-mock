"""職能揭露：能力清單載入、模板化能力回答、intent 分類。"""
import json
from pathlib import Path

_MANIFEST_PATH = Path(__file__).resolve().parent / "capability_manifest.json"

# doc_type 顯示順序
_DOC_ORDER = ["法條", "施行細則", "函釋", "FAQ", "判決", "裁罰"]


def load_manifest():
    """讀 capability_manifest.json；缺檔回安全空殼（不讓前端/回答崩潰）。"""
    try:
        return json.loads(_MANIFEST_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {"doc_type_counts": {}, "categories": [], "cutoff": "", "total": 0}


def _fmt_counts(counts):
    parts = []
    for dt in _DOC_ORDER:
        if dt in counts:
            n = counts[dt]
            parts.append(f"{dt} {n:,}")
    return "・".join(parts) if parts else "（語料盤點中）"


def capability_answer(manifest):
    """由 manifest 組出固定結構 Markdown（不經 LLM、零幻覺）。"""
    counts = manifest.get("doc_type_counts", {})
    # 濾掉 catch-all「其他」：它對使用者不是有意義的法令領域（與前端空狀態 tag 一致）
    cats = "・".join(c for c in manifest.get("categories", []) if c != "其他") or "（領域盤點中）"
    cutoff = manifest.get("cutoff", "")
    cutoff_line = f"\n\n語料截止：{cutoff}" if cutoff else ""
    return (
        "## 我能幫你的\n"
        "條文查詢與解釋（法規名稱＋條號）、義務／罰則對照、合規要件檢核、實務見解佐證（函釋・判決）。\n\n"
        "## 涵蓋語料（皆真實官方開放資料）\n"
        f"**文件型別**：{_fmt_counts(counts)}\n\n"
        f"**法令領域**：{cats}"
        f"{cutoff_line}\n\n"
        "## 我不能做（請另尋專業）\n"
        "提供正式法律意見、代理訴訟、個案金額試算、最新即時判決。\n\n"
        "本回答為研究輔助，非正式法律意見。"
    )


INTENT_INSTRUCTIONS = (
    "你是台灣法遵研究助理的意圖守門員（topical guardrail）。判斷使用者本輪查詢的意圖類別：\n"
    "- meta_capability：詢問本系統能做什麼、可回答哪類問題、語料涵蓋範圍、功能說明、"
    "「你是誰／你會什麼／你能幫我什麼」。\n"
    "- legal_question：實質法規／法遵問題（需要條文／函釋／判決），"
    "**包含對先前法遵回答的延續請求**——如「好啊請幫我生成對照表」「請整理成表格」「繼續」等，"
    "只要延續的主題是法遵，一律 legal_question。\n"
    "- out_of_scope：與法遵無關的請求——翻譯、算數／數學、寫程式、創作、閒聊、"
    "一般知識問答等。**即使出現在多輪對話中間，與法遵無關就是 out_of_scope**"
    "（例：前輪在談個資法，本輪「幫我把公司治理翻譯成英文」→ out_of_scope）。\n"
    "邊界（防誤殺）：法遵問題**夾帶**外文用語或數字換算，仍是 legal_question——"
    "只有「純粹的翻譯／計算服務」才是 out_of_scope。\n"
    "範例：『你能回答什麼？』→ meta_capability；『個資法第6條規定什麼？』→ legal_question；"
    "『好啊，請幫我生成對照表』（前輪為法遵回答）→ legal_question；"
    "『個資法第6條的官方英譯怎麼說？』→ legal_question（法遵題夾外文）；"
    "『罰鍰上限 30 萬、違規 3 次最多罰多少？』→ legal_question（法遵題夾計算）；"
    "『幫我算 1234×5678』→ out_of_scope；『翻譯成英文』→ out_of_scope。"
)

INTENT_VALUES = ["meta_capability", "legal_question", "out_of_scope"]

_INTENT_SCHEMA = {
    "type": "json_schema", "name": "intent", "strict": True,
    "schema": {"type": "object",
               "properties": {"intent": {"type": "string", "enum": INTENT_VALUES}},
               "required": ["intent"], "additionalProperties": False},
}


def _parse_intent(output_text):
    try:
        data = json.loads(output_text or "")
        intent = data.get("intent")
    except (json.JSONDecodeError, TypeError, AttributeError):
        return "legal_question"
    return intent if intent in INTENT_VALUES else "legal_question"


def _intent_kwargs(question, *, model, previous_response_id):
    kwargs = {"model": model, "instructions": INTENT_INSTRUCTIONS,
              "input": question, "text": {"format": _INTENT_SCHEMA}}
    if previous_response_id:
        # 延續輪：fork 主對話鏈讓 classifier 看見脈絡（官方文件：instructions 不繼承、
        # fork 不污染主鏈）；store=False——葉節點無人接鏈，不入庫。
        kwargs["previous_response_id"] = previous_response_id
        kwargs["store"] = False
    return kwargs


def classify_intent(client, question, *, model, previous_response_id=None):
    """同步：structured output 三類分類（每輪都跑）。壞掉 → legal_question（不退化）。"""
    try:
        resp = client.responses.create(
            **_intent_kwargs(question, model=model, previous_response_id=previous_response_id))
    except Exception:  # noqa: BLE001 分類失敗不可拖垮主問答
        return "legal_question"
    return _parse_intent(getattr(resp, "output_text", ""))


async def aclassify_intent(client, question, *, model, previous_response_id=None):
    """非同步版（AsyncOpenAI）。"""
    try:
        resp = await client.responses.create(
            **_intent_kwargs(question, model=model, previous_response_id=previous_response_id))
    except Exception:  # noqa: BLE001
        return "legal_question"
    return _parse_intent(getattr(resp, "output_text", ""))


TIER_VALUES = ["明文", "解釋/裁量", "實務見解", "語料未涵蓋"]

TIER_INSTRUCTIONS = (
    "依『問題』與『檢索到的依據』判斷這題答案的確定性層級，回傳其一：\n"
    "- 明文：法律條文明文規定、答案清楚。\n"
    "- 解釋/裁量：條文須解釋或屬主管機關裁量。\n"
    "- 實務見解：主要靠函釋／判決等實務見解。\n"
    "- 語料未涵蓋：檢索不到足以回答的依據。"
)

_TIER_SCHEMA = {
    "type": "json_schema", "name": "tier", "strict": True,
    "schema": {"type": "object",
               "properties": {"tier": {"type": "string", "enum": TIER_VALUES}},
               "required": ["tier"], "additionalProperties": False},
}


def _parse_tier(output_text):
    try:
        t = json.loads(output_text or "").get("tier")
    except (json.JSONDecodeError, TypeError, AttributeError):
        return "語料未涵蓋"
    return t if t in TIER_VALUES else "語料未涵蓋"


def _tier_input(question, context):
    return f"問題：{question}\n\n檢索到的依據：\n{context or '（無）'}"


def classify_tier(client, question, context, *, model):
    try:
        resp = client.responses.create(model=model, instructions=TIER_INSTRUCTIONS,
                                       input=_tier_input(question, context), text={"format": _TIER_SCHEMA})
    except Exception:  # noqa: BLE001 分類失敗不可拖垮主問答
        return "語料未涵蓋"
    return _parse_tier(getattr(resp, "output_text", ""))


async def aclassify_tier(client, question, context, *, model):
    try:
        resp = await client.responses.create(model=model, instructions=TIER_INSTRUCTIONS,
                                              input=_tier_input(question, context), text={"format": _TIER_SCHEMA})
    except Exception:  # noqa: BLE001 分類失敗不可拖垮主問答
        return "語料未涵蓋"
    return _parse_tier(getattr(resp, "output_text", ""))
