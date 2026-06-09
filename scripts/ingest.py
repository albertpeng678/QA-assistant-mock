"""建立 vector store 並以 static chunking 上傳 data/ 內所有語料檔。
用法: python scripts/ingest.py
完成後會印出 VECTOR_STORE_ID，請填入 Railway variables。
"""
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
}

# 解析檔名 {法規名稱}-第N條.txt（N 可含 "-"，例 1-1）。
_FILENAME_RE = re.compile(r"^(?P<law>.+)-(?P<article>第[\d-]+條)$")


def derive_attributes(filename: str) -> dict:
    """從 {法規名稱}-第N條.txt 解析 per-file metadata attributes（值皆為 str）。

    回傳 law / article / category / doc_type / source 五個 key（<16，符合上限）。
    解析不到時退回較安全的預設值，避免上傳中斷。
    """
    stem = Path(filename).stem  # 去副檔名
    m = _FILENAME_RE.match(stem)
    if m:
        law = m.group("law")
        article = m.group("article")
    else:
        law = stem
        article = ""
    return {
        "law": law,
        "article": article,
        "category": LAW_CATEGORY.get(law, "其他"),
        "doc_type": "法條",
        "source": "全國法規資料庫",
    }

# chunking 策略（依 context7 查得之 OpenAI file search static chunking 規格；openai-python，StaticFileChunkingStrategy）：
#   max_chunk_size_tokens 合法區間 100–4096、預設 800；chunk_overlap_tokens 預設 400 且須 ≤ max/2。
# 本語料已在上傳前逐條切成「一條一檔」，多數檔偏短（一條法規通常遠小於 800 tokens），
# 故採中小 chunk + 小 overlap：多數短檔本來就會落在單一 chunk，small chunk 讓少數長條（含多項/多款）
# 切得更細、提升檢索精度與引用定位；overlap=128 保留跨 chunk 上下文又遠低於 max/2(=256) 上限。
MAX_CHUNK_SIZE_TOKENS = 512  # 100–4096 合法區間內的中小值，貼合逐條短檔語料
CHUNK_OVERLAP_TOKENS = 128   # 小 overlap，滿足 ≤ max/2 (=256) 約束

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

def main():
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        sys.exit("請先設定 OPENAI_API_KEY 環境變數")

    files = [p for p in DATA_DIR.iterdir() if p.is_file() and p.suffix in {".txt", ".md"}]
    if not files:
        sys.exit(f"{DATA_DIR} 內沒有 .txt/.md 語料檔")

    client = OpenAI(api_key=api_key)
    vector_store = client.vector_stores.create(name="法規語料")
    print(f"已建立 vector store: {vector_store.id}")

    chunking_strategy = {
        "type": "static",
        "static": {
            "max_chunk_size_tokens": MAX_CHUNK_SIZE_TOKENS,
            "chunk_overlap_tokens": CHUNK_OVERLAP_TOKENS,
        },
    }

    # 步驟一：逐檔上傳成 file_id，並附帶從檔名解析出的 per-file attributes。
    # 容錯：單檔上傳失敗印警告跳過，不中斷整批。檔案 handle 用 with 確保關閉。
    file_specs = []
    for p in files:
        try:
            with open(p, "rb") as fh:
                f = client.files.create(file=fh, purpose="assistants")
        except Exception as e:  # noqa: BLE001 — 容錯：跳過單檔失敗
            print(f"警告: 上傳檔案失敗，已跳過 {p.name}: {e}")
            continue
        file_specs.append({
            "file_id": f.id,
            "attributes": derive_attributes(p.name),
            "chunking_strategy": chunking_strategy,
        })

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
