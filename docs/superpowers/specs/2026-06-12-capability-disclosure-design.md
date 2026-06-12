# Spec — 職能揭露（capability disclosure）：meta 問題路由 + 範疇 UI + markdown 語意配色

> 建立：2026-06-12｜分支：待開 feature branch｜
> 研究依據：context7（OpenAI Responses API routing；marked custom renderer）、NN/g 7 篇（prompt suggestions / empty state / mental models / chatbot capabilities）。
> 視覺定案（brainstorming visual companion）：空狀態 **A（領域能力卡網格）**、對話三元素（去 emoji）、**markdown 語意配色①（含低飽和輔色）**。

## 1. 背景與問題

問答管線目前對**每個** message 都先走 `retrieve_dual` 雙路檢索。當使用者問**職能/範疇 meta 問題**（「你能做什麼」「涵蓋哪些領域」「你會什麼」）時，只撈到幾段法條 → 答非所問，無法告知系統可回答的全領域與語料範疇。NN/g：使用者一旦判定「它不會」就不再嘗試（one-strike），故需主動、準確揭露涵蓋範圍。

附帶：使用者希望答案的**顏色依 markdown 結構**做語意區分，且**現有問答一併套用**（不只新功能）。

## 2. 目標與非目標

### 目標
- **後端 intent gate**：辨識 meta/capability 問題 → 用**資料驅動能力清單**作答、**跳過 `retrieve_dual`**；其餘照常雙路檢索。
- **前端職能揭露 UI**：空狀態領域能力卡（A）、側欄「關於本助理」範疇卡、`dual_retrieved` 缺口提示。
- **markdown 語意配色**：以 marked custom renderer 掛 class + CSS 上色，套用到**所有答案**（含現有問答）。
- 能力清單**資料驅動**（doc_type×領域件數由向量庫實況產生），避免寫死過期（判決即將 106→~1.1萬）。

### 非目標
- 不改 `retrieve_dual` 雙路邏輯與既有引用機制。
- 不做多語系、不做帳號化偏好。
- 不引入新前端框架；沿用既有 marked + DOMPurify + 原生 CSS。

## 3. 架構

```
使用者問題
 └→ classify_intent(client, q, model)          # gpt-5.4-mini structured output
      → {"intent": "meta_capability" | "legal_question"}
 ├ meta_capability → capability_answer(manifest)         # 模板化、零檢索、零幻覺
 │     回 {answer, citations:[], evidence:[], evidence_mode:"capability", ...}
 └ legal_question  → 既有 retrieve_dual 雙路流程（不變）
                     若 evidence_mode=="dual_retrieved" → 前端顯示缺口提示

能力清單來源：scripts/build_capability_manifest.py 查向量庫 doc_type×category 計數
            → 產出 app/capability_manifest.json（部署含）；/api/capability 提供給前端
```

## 4. 元件與介面

| 元件 | 檔案 | 責任 |
|---|---|---|
| Intent 分類器 | `app/capability.py`（新） | `classify_intent(client, question, *, model)` → `{"intent": ...}`（structured output，複用 `suggest_followups` 的 `text.format.json_schema` 模式） |
| 能力清單載入 | `app/capability.py` | `load_manifest()` 讀 `capability_manifest.json`；`capability_answer(manifest)` 回模板化 Markdown（能處理／涵蓋語料／不能做＋免責） |
| 清單產生器 | `scripts/build_capability_manifest.py`（新） | 查 vector store：各 doc_type 件數、出現的 category、cutoff（取最新 effective_date 月份）→ 寫 `capability_manifest.json` |
| 路由接入 | `app/rag.py` | `answer_question`/`astream_answer` 開頭加 intent gate；meta → 回 `capability_answer`，不呼叫 `retrieve_dual` |
| 能力 API | `app/main.py` | `GET /api/capability` 回 manifest（給前端空狀態卡 + 範疇卡） |
| 前端 UI | `static/index.html` | 空狀態 A 領域能力卡、側欄「關於本助理」範疇卡、缺口提示、markdown 語意配色 |

### 4.1 Intent 分類（structured output）
```python
_INTENT_SCHEMA = {"type":"json_schema","name":"intent","strict":True,"schema":{
  "type":"object","properties":{"intent":{"type":"string","enum":["meta_capability","legal_question"]}},
  "required":["intent"],"additionalProperties":False}}
```
- meta_capability：問系統能力、可回答範圍、語料涵蓋、功能說明、「你是誰/你會什麼」。
- legal_question：實質法規問題。
- 成本：每題 +1 次 `gpt-5.4-mini` 呼叫（~100ms）；分類失敗（例外/非法 JSON）**fallback 為 legal_question**（退回現有行為，不退化）。

### 4.2 capability_answer（模板化、資料驅動）
由 manifest 組出固定結構 Markdown（**不經 LLM、零幻覺**）：
```
## 我能幫你的
- 條文查詢與解釋（法規名稱＋條號）
- 義務／罰則對照、合規要件檢核
- 實務見解佐證（函釋、判決）
## 涵蓋語料（皆真實官方開放資料）
**文件型別**：法條 {n}・施行細則 {n}・函釋 {n}・FAQ {n}・判決 {n}
**法令領域**：{categories…}
## 我不能做（請另尋專業）
- 提供正式法律意見、代理訴訟
- 個案金額試算、最新即時判決
本回答為研究輔助，非正式法律意見。
```
- `evidence_mode="capability"`、`citations=[]`、`evidence=[]`。前端對 `capability` 模式不顯示證據面板的引用卡，改顯示範疇卡。

### 4.3 缺口提示
`evidence_mode=="dual_retrieved"`（模型未精準引用、退回全部檢索段）時，前端在答案頂部插一行：
> 「本題超出語料直接涵蓋範圍，以下依相關度排列參考條文，未必逐句對應；建議查閱判決資料庫或諮詢法律顧問。」

### 4.4 前端 UI（定案視覺）
- **空狀態 A**：開場一句範疇（讀 `/api/capability`）＋ 6 個領域能力卡，每卡 1 個可點範例問題（NN/g：可點 cards、具體、貼近輸入）。
- **「關於本助理」範疇卡**（側欄）：無引用時顯示涵蓋（doc_type 件數＋領域 pills）／尚未涵蓋／語料截止；**有引用時自動換回證據卡**。**禁用 emoji。**
- 既有 starter 區改為領域能力卡資料來源 = `/api/capability`。

### 4.5 markdown 語意配色（context7/marked 主流解法）
marked custom renderer 給元素掛 class（或直接以 `.answer-prose` 元素選擇器），CSS 上色。**套用所有答案（含現有問答）**。配色（嚴守低飽和，墨 `#1a1714`／血 `#7a2e2e` 為主＋2 低飽和輔色）：

| Markdown | 處理 |
|---|---|
| `# H2` 主結構標題 | 血紅 Fraunces |
| `## H3` 次標題 | 深墨 Fraunces |
| `**bold**` | 墨色加粗（不換色） |
| `> 引文` | 血紅左槓＋淡墨斜體 |
| `` `code` ``（條號） | 血紅 mono、`--ink-08` 底 |
| 表格 | 表頭 `--ink-08` 底、`--ink-20` 框線 |
| 確定性層級（既有 tierTags） | 明文=墨／解釋裁量=teal `#2e5a52`／實務見解=amber `#8a5a12`／未涵蓋=灰 |

> renderer 掛的 class 須在 DOMPurify allowlist 內（既有已 sanitize；class 屬性預設保留）。

## 5. 測試策略

### 後端（TDD）
- `classify_intent`：mock structured response，assert meta/legal 分類；例外/壞 JSON → fallback legal。
- `capability_answer`：給 manifest，assert 含三段結構＋各 doc_type 件數＋領域＋免責；無幻覺數字（數字全來自 manifest）。
- `answer_question` 路由：meta → 不呼叫 `retrieve_dual`、`evidence_mode=="capability"`、citations 空；legal → 走 `retrieve_dual`（既有測試不回歸）。
- `build_capability_manifest`：mock vector store list，assert 計數正確。
- 全 `pytest -q` 維持綠、`ruff` 過。

### 前端（playwright e2e，對 `mock_server.py`）
- 空狀態領域能力卡可見、可點 → 帶入問題。
- 問「你能做什麼」→ 渲染能力回答（含「涵蓋語料」「不能做」段）、無證據引用卡、範疇卡可見。
- `dual_retrieved` 答案顯示缺口提示行。
- markdown 語意配色：H2 血紅、H3 深墨、tier chips 三色、表格樣式 — DOM 斷言對應 class/顏色。
- `mock_server.py` 需補 `/api/capability` mock + 一個 capability 模式 final + 一個 dual_retrieved final。
- **5x consecutive 0 flake**。

### Live demo gate（前端變動）
動 `static/index.html`（空狀態卡、範疇卡、缺口提示、配色）→ **stage 不 commit → 截 PNG → 使用者親眼確認「對」→ 才 commit**。

### Code review
完成跑 requesting/receiving-code-review。

## 6. 完成定義
1. 問「你能做什麼/涵蓋哪些」→ 得到**完整職能＋語料範疇**回答（非法條段落），不檢索。
2. 一般法規問題行為不變（雙路檢索、引用、延遲僅 +~100ms 分類）。
3. 空狀態 A 卡、範疇卡（無 emoji）、缺口提示在 production 正常；markdown 語意配色套用於所有答案。
4. 能力清單數字 = 向量庫實況（判決上傳後重跑 generator 即更新）。
5. `pytest -q` 全綠、`ruff` 過、playwright 5x 0 flake、過 code review、PNG 經使用者確認。

## 7. 風險與緩解
| 風險 | 緩解 |
|---|---|
| 分類器誤判（把 meta 當 legal 或反之） | 壞掉 fallback 為 legal（不退化）；prompt 給足範例；可後加 regex 快路徑 |
| 每題 +1 LLM 分類延遲 | gpt-5.4-mini 低成本；答案本身耗時數秒，~100ms 可接受；必要時加 regex 短路明顯 meta |
| 能力清單過期 | 資料驅動 generator；判決上傳後重跑 |
| 配色過花破壞克制美學 | 嚴守墨/血＋2 低飽和輔色；輔色僅用於確定性層級 |
| DOMPurify 濾掉 renderer class | class 在 allowlist；e2e DOM 斷言把關 |

## 8. 非涵蓋／後續
- regex 快路徑優化（避免一般問題的分類延遲）— 視實測延遲再做。
- 多輪對話中重複問 meta 的快取 — YAGNI。
