# 答案清晰化與資訊架構 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把法遵答案從「密集、艱澀、結構先行」改成「BLUF 結論優先＋白話＋降密度」的三層資訊架構,並讓確定性層級以可靠的 structured-output 產生 badge。

**Architecture:** 後端 `verbosity:low` + 重寫 `ANSWER_INSTRUCTIONS`(倒金字塔/白話/表格 opt-in/層級不再內嵌)＋新增 `classify_tier`(structured enum,與答案生成並行)把 tier 放進回傳契約。前端:marked custom renderer 把表格包 `overflow-x-auto`(裝置制約,無 media query)、依 `tier` 渲染頂部 badge、caveat/blockquote 改無左槓軟盒。

**Tech Stack:** OpenAI Responses API（verbosity / structured output enum）/ marked + DOMPurify / Tailwind CDN / pytest / Playwright。

**對應 spec:** `docs/superpowers/specs/2026-06-12-answer-clarity-ia-design.md`

---

## File Structure

| 檔案 | 變更 |
|---|---|
| `app/rag.py` | `_MODEL_PARAMS.verbosity` low；重寫 `ANSWER_INSTRUCTIONS`；`answer_question`/`astream_answer` 接 `classify_tier`、回傳加 `tier` |
| `app/capability.py` | 新增 `classify_tier`/`aclassify_tier`（structured enum，複用 classify_intent 模式） |
| `static/index.html` | marked table renderer 包 overflow；tier badge（讀 `evt.tier`）；blockquote/caveat 改無左槓軟盒（前端，**走 live demo gate**） |
| `scripts/mock_server.py` | final 加 `tier`；ANSWER 改 BLUF 範例；e2e 用 |
| `tests/test_rag.py`、`tests/test_capability.py` | TDD |
| `tests/e2e/ask.spec.ts` | badge / overflow 表 / caveat e2e |

**前端鐵則：** 動 `static/index.html` → stage 不 commit → 截 PNG → 使用者「對」→ 才 commit（live demo gate）。後端走 TDD。

---

## Task 1: classify_tier（structured enum，sync + async）

**Files:** Modify `app/capability.py`；Test `tests/test_capability.py`

- [ ] **Step 1: 失敗測試**（append to tests/test_capability.py）
```python
import asyncio as _aio2
from app.capability import classify_tier, aclassify_tier, TIER_VALUES


def test_tier_values():
    assert TIER_VALUES == ["明文", "解釋/裁量", "實務見解", "語料未涵蓋"]


def test_classify_tier_parses_enum():
    c = _FakeIntentClient('{"tier": "實務見解"}')   # 複用 Task CD-3 的 fake
    assert classify_tier(c, "q", "context", model="m") == "實務見解"


def test_classify_tier_bad_json_falls_back():
    c = _FakeIntentClient("garbage")
    assert classify_tier(c, "q", "ctx", model="m") == "語料未涵蓋"   # 壞掉退最保守


def test_aclassify_tier():
    out = _aio2.run(aclassify_tier(_FakeAIntentClient('{"tier":"明文"}'), "q", "ctx", model="m"))
    assert out == "明文"
```

- [ ] **Step 2: 跑，確認 fail**：`.venv/bin/python -m pytest tests/test_capability.py -k "tier" -v` → ImportError。

- [ ] **Step 3: 實作**（append to app/capability.py）
```python
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
    except Exception:  # noqa: BLE001
        return "語料未涵蓋"
    return _parse_tier(getattr(resp, "output_text", ""))
```

- [ ] **Step 4: 跑，pass + 全回歸**：`.venv/bin/python -m pytest tests/test_capability.py -k tier -v` 然後 `.venv/bin/python -m pytest -q`、`ruff check app/`。

- [ ] **Step 5: Commit**
```bash
git add app/capability.py tests/test_capability.py
git commit -m "feat(capability): classify_tier 確定性層級分類（structured enum）

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2: verbosity=low + ANSWER_INSTRUCTIONS 重寫 + 接 classify_tier

**Files:** Modify `app/rag.py`；Test `tests/test_rag.py`

- [ ] **Step 1: 失敗測試**（append to tests/test_rag.py）
```python
def test_verbosity_is_low():
    from app.rag import _MODEL_PARAMS
    assert _MODEL_PARAMS["text"]["verbosity"] == "low"


def test_answer_question_attaches_tier(monkeypatch):
    import app.rag as rag
    monkeypatch.setattr(rag, "classify_intent", lambda *a, **k: "legal_question")
    monkeypatch.setattr(rag, "classify_tier", lambda *a, **k: "明文")
    client = _FakeClient(_fake_response())
    out = rag.answer_question(client, "個資外洩?", vector_store_id="vs_1", model="m")
    assert out["tier"] == "明文"
```

- [ ] **Step 2: 跑，確認 fail**：`.venv/bin/python -m pytest tests/test_rag.py -k "verbosity_is_low or attaches_tier" -v` → verbosity 仍 medium / 無 tier 鍵。

- [ ] **Step 3a: 改 `_MODEL_PARAMS`**（app/rag.py）
```python
_MODEL_PARAMS = {
    "reasoning": {"effort": "low"},
    "text": {"verbosity": "low"},   # 變更：medium→low（降密度）
    "max_output_tokens": 4096,
}
```

- [ ] **Step 3b: 重寫 `ANSWER_INSTRUCTIONS`**（取代整段現有字串）
```python
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
```
（移除舊的「確定性分層 chip 每點標註」要求；層級改由 classify_tier。`CITATION_PROTOCOL`、`_build_dual_kwargs` 等不動。）

- [ ] **Step 3c: 接 classify_tier**（app/rag.py）— import 已有 `from app.capability import ...`，補 `classify_tier, aclassify_tier`。
`answer_question` 的 legal 路尾段改為（在 `parse_dual_response` 後附 tier）：
```python
    response = client.responses.create(**kwargs)
    result = parse_dual_response(response, sources)
    result["tier"] = classify_tier(client, question, context, model=model)
    return result
```
`astream_answer` 的 final 改為先並行分類 tier、再附上：
```python
    final_response = None
    tier = None
    stream = await client.responses.create(**kwargs)
    # tier 與串流並行：開一個背景工作（依賴 context，不依賴答案文字）
    tier_task = asyncio.create_task(aclassify_tier(client, question, context, model=model))
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
    else:
        tier_task.cancel()
```
（`asyncio` 已於先前匯入。）`capability_result` 不加 tier（能力回答無層級）。

- [ ] **Step 4: 跑，pass + 全回歸**：`.venv/bin/python -m pytest tests/test_rag.py -v`（既有測試:answer_question 已 monkeypatch classify_intent;若新斷言需要也 monkeypatch classify_tier。逐一補到綠）然後 `.venv/bin/python -m pytest -q`、`ruff check app/`。

- [ ] **Step 5: Commit**
```bash
git add app/rag.py tests/test_rag.py
git commit -m "feat(rag): verbosity=low + ANSWER_INSTRUCTIONS 重寫（BLUF/白話/降密度）+ 接 classify_tier

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 3: mock_server — 反映新答案形狀（tier + BLUF）

**Files:** Modify `scripts/mock_server.py`

- [ ] **Step 1: 改 mock**
- `ANSWER` 改成 BLUF 範例（第一行白話結論、2–4 bullet、≤3 欄表、末端 `>` caveat）：
```python
ANSWER = (
    "**重大消息公開滿 18 小時後**才能買賣股票（證交法§157-1）。\n\n"
    "- 消息明確後到公開前、公開後 18 小時內，都不得買賣（§157-1）。[^1]\n"
    "- 對象不只內部人：董事、經理人、大股東、離職未滿 6 個月者也算（§157-1）。[^2]\n"
    "- 「滿 18 小時」從實際公開時間起算，不是公告當天（金管會FAQ）。[^3]\n\n"
    "| 情境 | 可否交易 | 依據 |\n|---|---|---|\n"
    "| 公開前 | 不可 | `§157-1` |\n| 公開後18小時內 | 不可 | `§157-1` |\n| 滿18小時後 | 可 | `§157-1` |\n\n"
    "> 提醒：重大消息成立時點屬個案認定，建議諮詢專業律師。本回答為研究輔助，非正式法律意見。"
)
```
- 成功路 final 加 `"tier": "明文"`：找到 `final = {"type": "final", "answer": ANSWER, "citations": CITES, "evidence": EVIDENCE, "evidence_mode": "dual_cited", ...}`，加入 `"tier": "明文"`。
- `__GAP__` final 加 `"tier": "解釋/裁量"`；`__NOCOVER__` final 加 `"tier": "語料未涵蓋"`。

- [ ] **Step 2: 驗證**：`.venv/bin/python -c "import ast; ast.parse(open('scripts/mock_server.py').read()); print('ok')"`；啟動 mock，`curl -s -X POST localhost:8001/api/ask/stream -H 'content-type: application/json' -d '{"question":"x"}' | grep -o '"tier": "明文"'`（命中）。

- [ ] **Step 3: Commit**
```bash
git add scripts/mock_server.py
git commit -m "test(mock): final 加 tier + ANSWER 改 BLUF 範例（答案清晰化）

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 4: 前端 — tier badge + 表格 overflow + caveat 軟盒（live demo gate）

**Files:** Modify `static/index.html`

- [ ] **Step 1: marked table renderer 包 overflow**（在既有 `md` / renderer 設定處加 custom renderer）
```javascript
// 表格裝置制約：包 overflow-x-auto，窄螢幕橫向捲動不爆版（context7/Tailwind）
const _renderer = new marked.Renderer();
const _origTable = _renderer.table.bind(_renderer);
_renderer.table = (...args) => `<div class="overflow-x-auto -mx-1">${_origTable(...args)}</div>`;
marked.use({ renderer: _renderer });
```
（放在 `marked` 載入後、`md()` 定義前。）

- [ ] **Step 2: tier badge（讀 evt.tier）** — 在 final 渲染處（`ui.answerEl.innerHTML = md(...)` 之前）插入 badge：
```javascript
  // 確定性層級 badge（Layer-0，來自後端 classify_tier，可靠）
  const TIER_BADGE = { "明文":"明文規定", "解釋/裁量":"解釋/裁量", "實務見解":"實務見解", "語料未涵蓋":"語料未涵蓋" };
  if(evt.tier && TIER_BADGE[evt.tier]){
    const b = document.createElement("div");
    b.className = "tier-badge";
    b.textContent = "● " + TIER_BADGE[evt.tier];
    ui.answerEl.before(b);
  }
```
CSS（`<style>` 內）：
```css
  .tier-badge{ display:inline-flex; align-items:center; font-family:'Public Sans'; font-size:12px; font-weight:700;
               color:var(--paper); background:var(--ink); border-radius:12px; padding:3px 11px; margin-bottom:8px; }
```

- [ ] **Step 3: caveat/blockquote 改無左槓軟盒** — 改既有 `.answer-prose blockquote` 規則：
```css
  .answer-prose blockquote{ background:#7a2e2e0a; border:1px solid var(--ink-12); border-radius:10px;
                            padding:10px 13px; color:var(--ink-70); font-style:normal; margin:1em 0 0; }
```
（移除先前的 `border-left:3px solid var(--blood)` 左槓；font-style 改 normal。）

- [ ] **Step 4: 啟 mock、Playwright 截 PNG（桌機+手機 375px）** — 問一題（預設 ANSWER）截桌機、手機;`__GAP__`、`__NOCOVER__` 各截一張驗 badge/caveat/overflow。Director cold-read。

- [ ] **Step 5: 使用者確認「對」→ commit；「不對」→ `git restore static/index.html`**
```bash
git add static/index.html
git commit -m "feat(ui): tier badge + 表格 overflow + caveat 軟盒（答案清晰化，過視覺 gate）

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 5: e2e + 回歸 + production 部署實測

**Files:** Modify `tests/e2e/ask.spec.ts`

- [ ] **Step 1: e2e**（append；role/data-attr、web-first、5x）
```typescript
test("答案清晰化：tier badge + BLUF 結論 + caveat", async ({ page }) => {
  await page.goto("/");
  await page.locator("#q").fill("內線交易何時可買賣");
  await page.locator("#ask-form button[type=submit]").click();
  await expect(page.locator(".tier-badge")).toBeVisible();
  await expect(page.locator(".tier-badge")).toContainText("明文");
  await expect(page.locator("[data-answer] blockquote")).toContainText("非正式法律意見");
});
```

- [ ] **Step 2: 跑 e2e（5x）+ 全回歸**：`npx playwright test`（全綠）；`for i in 1 2 3 4 5; do npx playwright test -g "答案清晰化" --reporter=line 2>&1 | tail -1; done`；`.venv/bin/python -m pytest -q`、`ruff check app/ scripts/`。

- [ ] **Step 3: Commit e2e**
```bash
git add tests/e2e/ask.spec.ts
git commit -m "test(e2e): 答案清晰化 — tier badge / BLUF / caveat（5x）

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

- [ ] **Step 4: 合併 main + 部署 + production cold-read** — 依使用者指示合併 `feat/answer-clarity` → push origin main（觸發 Railway）→ 待 `/api/capability` 指紋確認新版（此 feature 不改該端點，改以「真實答案首行 BLUF + tier badge」為部署驗證指紋）→ Playwright 對 production 問明文題/裁量題/未涵蓋題，cold-read 驗:首行 BLUF、badge 正確、白話、密度下降、表格手機 overflow。

---

## Self-Review（plan ↔ spec 覆蓋）
- spec §3.1 verbosity low → Task 2 ✅
- spec §3.2 ANSWER_INSTRUCTIONS 重寫（BLUF/bullet/表格 opt-in/白話） → Task 2 ✅
- spec §3.3 標籤可靠（classify_tier structured，並行） → Task 1 + Task 2（async gather）✅
- spec §3.4 前端 badge / 不新增折疊 widget → Task 4 ✅
- spec §3.5 #1 BLUF 硬約束＋正例 → Task 2 指示 ✅；#2 bullet 2–4 → Task 2 ✅；#3 表格 overflow（context7）+ ≤3 欄 + §縮寫 → Task 4（renderer）+ Task 2（prompt）✅；#4 兩維度（badge=層級;coverage 由 dual_retrieved 缺口提示/語料未涵蓋 tier 表達）✅；#5 caveat 軟盒無左槓 → Task 4 ✅；#6 關鍵數字粗體 → Task 2 指示 ✅；#7 可驗證閱讀程度 → Task 2 指示 + Task 5 cold-read ✅
- spec §4 驗證（mock/production cold-read/回歸） → Task 3/5 ✅
- 型別一致：`classify_tier`/`aclassify_tier`/`TIER_VALUES`/`_TIER_SCHEMA` 跨 Task 1-2 一致；回傳 `tier` 鍵 Task 2 加、Task 4 前端讀 `evt.tier`、mock Task 3 供應 ✅
**已知/刻意：** json_schema 強制全 IA + 前端折疊面板列為 spec §7 後續，不在本 plan。
