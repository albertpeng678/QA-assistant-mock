"""一次性遷移：移除 vector store 中 FAQ/函釋/判決/裁罰 檔案的錯誤 url attribute。

這些 doc_type 的「對應條號」僅為參考標籤，不應組成法條頁 URL。
本腳本讀取 vector store 所有檔案，對 doc_type ∈ {函釋, FAQ, 判決, 裁罰} 且有
LawSingle URL 的檔案，移除 url 欄位。若日後 metadata 加上正確 url，需重新 ingest。

用法:
    python scripts/fix_faq_urls.py [--dry-run]
"""
import argparse
import os
import sys
from openai import OpenAI


_LAW_SINGLE_MARKER = "LawSingle"
_NON_LAW_DOC_TYPES = {"函釋", "FAQ", "判決", "裁罰"}


def main():
    parser = argparse.ArgumentParser(description="移除 FAQ/函釋/判決 的錯誤法條 URL")
    parser.add_argument("--dry-run", action="store_true", help="只列出需修改的檔案，不實際更新")
    args = parser.parse_args()

    api_key = os.getenv("OPENAI_API_KEY")
    vs_id = os.getenv("VECTOR_STORE_ID")
    if not api_key or not vs_id:
        sys.exit("需設定 OPENAI_API_KEY 與 VECTOR_STORE_ID 環境變數")

    client = OpenAI(api_key=api_key)

    print(f"掃描 vector store {vs_id} …")
    to_fix = []
    total_scanned = 0
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
            url = attrs.get("url", "")
            if doc_type in _NON_LAW_DOC_TYPES and _LAW_SINGLE_MARKER in url:
                to_fix.append((f.id, attrs))
        if not page.has_more:
            break
        after = page.data[-1].id

    print(f"掃描完畢：共 {total_scanned} 個檔案，其中 {len(to_fix)} 個需修正")

    if args.dry_run:
        for fid, attrs in to_fix[:20]:
            print(f"  {fid}  doc_type={attrs.get('doc_type')}  url={attrs.get('url')}")
        if len(to_fix) > 20:
            print(f"  … 還有 {len(to_fix) - 20} 個")
        return

    fixed = 0
    errors = 0
    for fid, attrs in to_fix:
        new_attrs = {k: v for k, v in attrs.items() if k != "url"}
        try:
            client.vector_stores.files.update(
                vector_store_id=vs_id,
                file_id=fid,
                attributes=new_attrs,
            )
            fixed += 1
            if fixed % 50 == 0:
                print(f"  已修正 {fixed}/{len(to_fix)} …")
        except Exception as e:  # noqa: BLE001
            print(f"  警告: 更新 {fid} 失敗: {e}")
            errors += 1

    print(f"\n完成：修正 {fixed} 個、失敗 {errors} 個")


if __name__ == "__main__":
    main()
