"""建立 vector store 並以 static chunking 上傳 data/ 內所有語料檔。
用法: python scripts/ingest.py
完成後會印出 VECTOR_STORE_ID，請填入 Railway variables。
"""
import os
import sys
from pathlib import Path
from openai import OpenAI

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

    streams = [open(p, "rb") for p in files]
    try:
        batch = client.vector_stores.file_batches.upload_and_poll(
            vector_store_id=vector_store.id,
            files=streams,
            chunking_strategy={
                "type": "static",
                "static": {
                    "max_chunk_size_tokens": MAX_CHUNK_SIZE_TOKENS,
                    "chunk_overlap_tokens": CHUNK_OVERLAP_TOKENS,
                },
            },
        )
    finally:
        for s in streams:
            s.close()

    print(f"上傳狀態: {batch.status}")
    print(f"檔案計數: {batch.file_counts}")
    print(f"\n>>> VECTOR_STORE_ID={vector_store.id}")
    print(">>> 請填入 Railway variables 的 VECTOR_STORE_ID")

if __name__ == "__main__":
    main()
