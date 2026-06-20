"""遷移腳本：為 vector store 中的 FAQ/函釋檔案補上正確來源 URL。

策略：
  1. 預掃本地 data/ 所有 FAQ/函釋 → 建 filename→url 對照（body deep link 提取）
  2. 預掃 OpenAI files.list → 建 file_id→filename 對照
  3. 掃 vector store → 比對 → 批量 update

只設定真正能到達原始內容的 deep link URL（如 cpc.ey.gov.tw/Page/…）。
機構首頁 URL 不算（使用者期望點開即到原始內容頁）。

用法:
    python -m scripts.add_source_urls [--dry-run]
"""
import argparse
import os
import sys
from pathlib import Path

from openai import OpenAI

from scripts.ingest import (
    _NONARTICLE_RE,
    _parse_first_line,
    extract_url_from_body,
)

_TARGET_DOC_TYPES = {"函釋", "FAQ"}
DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def _build_local_url_index() -> dict[str, str]:
    """預掃本地 data/ 所有 FAQ/函釋檔案，回傳 filename→url 對照。"""
    index = {}
    for p in DATA_DIR.iterdir():
        if not p.is_file() or p.suffix not in {".txt", ".md"}:
            continue
        stem = p.stem
        m = _NONARTICLE_RE.match(stem)
        if not m or m.group("doc_type") not in _TARGET_DOC_TYPES:
            continue

        text = p.read_text(encoding="utf-8", errors="replace")
        first_line = text.splitlines()[0] if text.splitlines() else ""
        meta = _parse_first_line(first_line)

        url = extract_url_from_body(text)
        if url:
            index[p.name] = url
    return index


def _build_fileid_to_name(client: OpenAI) -> dict[str, str]:
    """從 OpenAI files.list 建 file_id→filename 對照（一次分頁取完）。"""
    mapping = {}
    after = None
    while True:
        kwargs = {"purpose": "assistants", "limit": 100}
        if after:
            kwargs["after"] = after
        page = client.files.list(**kwargs)
        for f in page.data:
            mapping[f.id] = f.filename
        if not page.has_more:
            break
        after = page.data[-1].id
    return mapping


def main():
    parser = argparse.ArgumentParser(description="為 FAQ/函釋補上來源 URL attribute")
    parser.add_argument("--dry-run", action="store_true", help="只列出會新增的 URL，不實際更新")
    args = parser.parse_args()

    api_key = os.getenv("OPENAI_API_KEY")
    vs_id = os.getenv("VECTOR_STORE_ID")
    if not api_key or not vs_id:
        sys.exit("需設定 OPENAI_API_KEY 與 VECTOR_STORE_ID 環境變數")

    client = OpenAI(api_key=api_key)

    # 步驟 1：建立本地 filename→url 對照
    print("步驟 1/3：掃描本地 data/ 建立 URL 對照 …")
    local_urls = _build_local_url_index()
    print(f"  本地 FAQ/函釋共 {len(local_urls)} 個檔案有 URL")

    # 步驟 2：建立 file_id→filename 對照
    print("步驟 2/3：從 OpenAI files.list 建立 ID→filename 對照 …", flush=True)
    fileid_names = _build_fileid_to_name(client)
    print(f"  OpenAI 檔案共 {len(fileid_names)} 個")

    # 步驟 3：掃描 vector store，找 FAQ/函釋 無 URL 的，對照更新
    print(f"步驟 3/3：掃描 vector store {vs_id} …", flush=True)
    to_update = []
    total_scanned = 0
    already_has_url = 0
    not_faq_hanshi = 0
    no_match = 0
    by_body = 0
    by_inst = 0

    after = None
    while True:
        kwargs = {"vector_store_id": vs_id, "limit": 100}
        if after:
            kwargs["after"] = after
        page = client.vector_stores.files.list(**kwargs)
        for f in page.data:
            total_scanned += 1
            attrs = f.attributes or {}
            doc_type = attrs.get("doc_type", "")
            if doc_type not in _TARGET_DOC_TYPES:
                not_faq_hanshi += 1
                continue
            if attrs.get("url"):
                already_has_url += 1
                continue

            filename = fileid_names.get(f.id, "")
            url = local_urls.get(filename, "")
            if url:
                to_update.append((f.id, attrs, url, filename))
            else:
                no_match += 1
        if not page.has_more:
            break
        after = page.data[-1].id
        if total_scanned % 2000 == 0:
            print(f"  已掃描 {total_scanned} …", flush=True)

    print(f"\n掃描完畢：共 {total_scanned} 個")
    print(f"  非 FAQ/函釋: {not_faq_hanshi}")
    print(f"  已有 URL: {already_has_url}")
    print(f"  待更新: {len(to_update)}")
    print(f"  無法取得 URL: {no_match}")

    if args.dry_run:
        for fid, attrs, url, fn in to_update[:30]:
            dt = attrs.get("doc_type", "?")
            print(f"  {fid}  {dt:4s}  {fn[:40]:40s}  → {url[:70]}")
        if len(to_update) > 30:
            print(f"  … 還有 {len(to_update) - 30} 個")
        return

    if not to_update:
        print("無需更新。")
        return

    print(f"\n開始更新 {len(to_update)} 個檔案 …", flush=True)
    updated = 0
    errors = 0
    for fid, attrs, url, fn in to_update:
        new_attrs = {**attrs, "url": url}
        try:
            client.vector_stores.files.update(
                vector_store_id=vs_id,
                file_id=fid,
                attributes=new_attrs,
            )
            updated += 1
            if updated % 50 == 0:
                print(f"  已更新 {updated}/{len(to_update)} …", flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"  警告: 更新 {fid} ({fn}) 失敗: {e}")
            errors += 1

    print(f"\n完成：更新 {updated} 個、失敗 {errors} 個、無 URL {no_match} 個")


if __name__ == "__main__":
    main()
