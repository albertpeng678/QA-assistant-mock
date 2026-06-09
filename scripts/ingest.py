"""建立 vector store 並以 static chunking 上傳 data/ 內所有語料檔。
用法: python scripts/ingest.py
完成後會印出 VECTOR_STORE_ID，請填入 Railway variables。
"""
import argparse
import json
import os
import re
import sys
from pathlib import Path
from openai import OpenAI

# 法規名稱 → 領域分類（category）對照表；查不到回 "其他"。
LAW_CATEGORY = {
    "個人資料保護法": "個資",
    "洗錢防制法": "洗錢防制",
    "勞動基準法": "勞動",
    "性別平等工作法": "勞動",
    "證券交易法": "公司治理",
    "公司法": "公司治理",
    "公平交易法": "公平交易",
    "營業秘密法": "營業秘密",
    "消費者保護法": "消費者保護",
    # 補語料衍生的相關母法（稽核 P1：消除 category=其他）
    "商業登記法": "公司治理",
    "多層次傳銷管理法": "公平交易",
}

# 純空殼條文標記（稽核 P0）：ingest 時略過，避免上傳零價值「（刪除）」檔。
_EMPTY_MARKERS = {"（刪除）", "(刪除)", "（保留）", "(保留)", ""}


def is_empty_article(content: str) -> bool:
    """判斷是否為純「（刪除）/（保留）」空殼條文（精確比對，含字樣的完整條文不誤殺）。"""
    return (content or "").strip() in _EMPTY_MARKERS

# 解析檔名 {法規名稱}-第N條.txt（N 可含 "-"，例 1-1）。
_FILENAME_RE = re.compile(r"^(?P<law>.+)-(?P<article>第[\d-]+條)$")

# 非條文素材檔名：{簡稱}-{doc_type}-{標題}（函釋/FAQ/判決/裁罰）。
_NONARTICLE_RE = re.compile(r"^(?P<prefix>.+?)-(?P<doc_type>函釋|FAQ|判決|裁罰)-(?P<title>.+)$")


def _parse_first_line(first_line: str) -> dict:
    """解析非條文檔首行的管線分隔 metadata（來源/效力/字號/發文日/對應條號/母法…）。"""
    out = {}
    if not first_line:
        return out
    for part in first_line.replace("：", ":").split("|"):
        if ":" in part:
            k, v = part.split(":", 1)
            out[k.strip()] = v.strip()
    return out


# 官方單一條文頁 URL 模板（全國法規資料庫）。
_LAW_SINGLE_URL = "https://law.moj.gov.tw/LawClass/LawSingle.aspx?pcode={pcode}&flno={flno}"


def article_to_flno(article: str) -> str:
    """條號 → flno：'第12條'→'12'、'第1-1條'→'1-1'、'第7條'→'7'。

    去掉「第」「條」字，保留數字與中間的 '-'。空字串/無法解析回空字串。
    """
    if not article:
        return ""
    return article.replace("第", "").replace("條", "").strip()


def derive_attributes(filename: str, law_index=None, first_line: str = None) -> dict:
    """從檔名（與非條文素材的首行 metadata）解析 per-file attributes（值皆為 str，≤16 鍵）。

    條文/施行細則：{法規名稱}-第N條.txt → law/article/category/doc_type/source(+url)。
    非條文（函釋/FAQ/判決/裁罰）：{簡稱}-{doc_type}-{標題}.txt，doc_type 由檔名定，
    其餘（母法/字號/發文日/對應條號/來源）由 first_line 取，category 由母法對應。
    若提供 law_index 且 pcode/條號齊備，加官方原文 url。解析不到退回安全預設。
    """
    stem = Path(filename).stem  # 去副檔名

    # —— 非條文素材（函釋/FAQ/判決/裁罰）——
    m_non = _NONARTICLE_RE.match(stem)
    if m_non:
        doc_type = m_non.group("doc_type")
        meta = _parse_first_line(first_line)
        law = meta.get("母法") or m_non.group("prefix")
        article = meta.get("對應條號", "")
        if article in ("", "未標明"):
            article = ""
        attrs = {
            "law": law,
            "article": article,
            "category": LAW_CATEGORY.get(law, "其他"),
            "doc_type": doc_type,
            "source": meta.get("來源", "") or "全國法規資料庫",
            "authority_level": meta.get("效力", doc_type),
        }
        if meta.get("字號"):
            attrs["ref_no"] = meta["字號"]
        eff = meta.get("發文日") or meta.get("發布日") or meta.get("裁判日")
        if eff:
            attrs["effective_date"] = eff
        if law_index and article:
            pcode = law_index.get(law, "")
            flno = article_to_flno(article)
            # 僅阿拉伯數字條號才組 url，避免中文數字條號（如「四十五」）產生指向錯誤的連結（稽核 P2）。
            if pcode and flno and re.fullmatch(r"[\d-]+", flno):
                attrs["url"] = _LAW_SINGLE_URL.format(pcode=pcode, flno=flno)
        return attrs

    m = _FILENAME_RE.match(stem)
    if m:
        law = m.group("law")
        article = m.group("article")
    else:
        law = stem
        article = ""
    # 施行細則本身是含條文的法規（同切檔管線）；doc_type 標「施行細則」、
    # category 沿用母法分類（母法名 = 去掉「施行細則」後綴）。
    if law.endswith("施行細則"):
        doc_type = "施行細則"
        base_law = law[: -len("施行細則")]
        category = LAW_CATEGORY.get(base_law, "其他")
    else:
        doc_type = "法條"
        category = LAW_CATEGORY.get(law, "其他")
    attrs = {
        "law": law,
        "article": article,
        "category": category,
        "doc_type": doc_type,
        "source": "全國法規資料庫",
    }
    if law_index:
        pcode = law_index.get(law, "")
        flno = article_to_flno(article)
        if pcode and flno:
            attrs["url"] = _LAW_SINGLE_URL.format(pcode=pcode, flno=flno)
    return attrs

# chunking 策略（依 context7 查得之 OpenAI file search static chunking 規格；openai-python，StaticFileChunkingStrategy）：
#   max_chunk_size_tokens 合法區間 100–4096、預設 800；chunk_overlap_tokens 預設 400 且須 ≤ max/2。
# 本語料已在上傳前逐條切成「一條一檔」，多數檔偏短（一條法規通常遠小於 800 tokens），
# 故採中小 chunk + 小 overlap：多數短檔本來就會落在單一 chunk，small chunk 讓少數長條（含多項/多款）
# 切得更細、提升檢索精度與引用定位；overlap=128 保留跨 chunk 上下文又遠低於 max/2(=256) 上限。
MAX_CHUNK_SIZE_TOKENS = 512  # 100–4096 合法區間內的中小值，貼合逐條短檔語料
CHUNK_OVERLAP_TOKENS = 128   # 小 overlap，滿足 ≤ max/2 (=256) 約束

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

def main():
    parser = argparse.ArgumentParser(description="建立 / 擴充法規 file_search vector store")
    parser.add_argument(
        "--vector-store-id",
        default=os.getenv("INGEST_VECTOR_STORE_ID"),
        help="附加到既有 vector store（沿用同一 ID，不建新）；不指定則新建。",
    )
    args = parser.parse_args()

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        sys.exit("請先設定 OPENAI_API_KEY 環境變數")

    files = [p for p in DATA_DIR.iterdir() if p.is_file() and p.suffix in {".txt", ".md"}]
    if not files:
        sys.exit(f"{DATA_DIR} 內沒有 .txt/.md 語料檔")

    # 載入法規→pcode 索引（fetch_corpus 產出），用以為各 chunk 加官方原文 url。
    # 不存在則 law_index=None，derive_attributes 退回無 url 行為（向後相容）。
    index_path = DATA_DIR / "_law_index.json"
    law_index = None
    if index_path.exists():
        law_index = json.loads(index_path.read_text(encoding="utf-8"))
        print(f"已載入法規→pcode 索引：{len(law_index)} 部法規")
    else:
        print("未找到 data/_law_index.json，將不附 url attribute（先跑 fetch_corpus.py 可產生）")

    client = OpenAI(api_key=api_key)
    append_mode = bool(args.vector_store_id)
    if append_mode:
        vector_store = client.vector_stores.retrieve(args.vector_store_id)
        print(f"附加模式：沿用既有 vector store {vector_store.id}（法條已在內，只上傳新語料）")
    else:
        vector_store = client.vector_stores.create(name="法規語料")
        print(f"已建立 vector store: {vector_store.id}")

    chunking_strategy = {
        "type": "static",
        "static": {
            "max_chunk_size_tokens": MAX_CHUNK_SIZE_TOKENS,
            "chunk_overlap_tokens": CHUNK_OVERLAP_TOKENS,
        },
    }

    # 步驟一：逐檔上傳成 file_id，並附帶從檔名 + 首行 metadata 解析出的 per-file attributes。
    # 略過純「（刪除）/（保留）」空殼條文（稽核 P0）；首行供函釋/FAQ 解析母法/字號等。
    # 容錯：單檔上傳失敗印警告跳過，不中斷整批。檔案 handle 用 with 確保關閉。
    file_specs = []
    skipped_empty = 0
    skipped_existing = 0
    for p in files:
        text = p.read_text(encoding="utf-8", errors="replace")
        if is_empty_article(text):
            skipped_empty += 1
            continue
        first_line = text.splitlines()[0] if text.splitlines() else ""
        attributes = derive_attributes(p.name, law_index=law_index, first_line=first_line)
        # 附加模式：法條已在既有 store，只上傳新語料（施行細則/函釋/FAQ…），避免重複。
        if append_mode and attributes["doc_type"] == "法條":
            skipped_existing += 1
            continue
        try:
            with open(p, "rb") as fh:
                f = client.files.create(file=fh, purpose="assistants")
        except Exception as e:  # noqa: BLE001 — 容錯：跳過單檔失敗
            print(f"警告: 上傳檔案失敗，已跳過 {p.name}: {e}")
            continue
        file_specs.append({
            "file_id": f.id,
            "attributes": attributes,
            "chunking_strategy": chunking_strategy,
        })
    if skipped_empty:
        print(f"已略過 {skipped_empty} 個純（刪除/保留）空殼條文")
    if append_mode and skipped_existing:
        print(f"附加模式：已略過 {skipped_existing} 個法條（既有 store 已含）")

    if not file_specs:
        sys.exit("沒有任何檔案成功上傳，中止。")

    # 步驟二：分批（每批 ≤ BATCH_SIZE）掛入 vector store。
    BATCH_SIZE = 500
    total = len(file_specs)
    print(f"共 {total} 個檔案待掛入，分批每批最多 {BATCH_SIZE} 檔。")
    for i in range(0, total, BATCH_SIZE):
        batch_specs = file_specs[i:i + BATCH_SIZE]
        batch = client.vector_stores.file_batches.create_and_poll(
            vector_store_id=vector_store.id,
            files=batch_specs,
        )
        print(f"批次 {i // BATCH_SIZE + 1}（{len(batch_specs)} 檔）狀態: {batch.status}；計數: {batch.file_counts}")

    print(f"\n總計: 已掛入 {total} 個檔案。")
    print(f"\n>>> VECTOR_STORE_ID={vector_store.id}")
    print(">>> 請填入 Railway variables 的 VECTOR_STORE_ID")

if __name__ == "__main__":
    main()
