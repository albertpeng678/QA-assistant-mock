"""遷移腳本：移除 vector store 中 FAQ/函釋的無效首頁 URL。

根因：construct_institutional_url 產生的 URL 全部是機構首頁，
使用者點「官方原文」期望到達原始內容頁，首頁無用。
只保留真正的 deep link（如 cpc.ey.gov.tw/Page/…）。

策略：
  掃 vector store → 檢查每個 FAQ/函釋的 url attr →
  用 _is_homepage_url 判斷是否為首頁 → 移除無效 URL

用法:
    python -m scripts.remove_homepage_urls [--dry-run]
"""
import argparse
import os
import re
import sys

from openai import OpenAI

_TARGET_DOC_TYPES = {"函釋", "FAQ"}


def _is_homepage_url(url: str) -> bool:
    """URL 是否僅為機構首頁（domain + optional /，無有意義的 path）。"""
    after_domain = re.sub(r'^https?://(www\.)?[^/]+', '', url)
    return after_domain in ('', '/')


def _is_gcis_article_query(url: str) -> bool:
    """GCIS lawDtlAction.do URL — 連結至逐條函釋列表頁但非個別文件。"""
    return "gcis.nat.gov.tw/elaw/lawDtlAction.do" in url


def _is_dead_ip(url: str) -> bool:
    """舊政府 IP（210.69.121.50 等），早已失效。"""
    return "210.69.121.50" in url


def main():
    parser = argparse.ArgumentParser(description="移除 FAQ/函釋的無效首頁 URL")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    api_key = os.getenv("OPENAI_API_KEY")
    vs_id = os.getenv("VECTOR_STORE_ID")
    if not api_key or not vs_id:
        sys.exit("需設定 OPENAI_API_KEY 與 VECTOR_STORE_ID 環境變數")

    client = OpenAI(api_key=api_key)

    print(f"掃描 vector store {vs_id} …", flush=True)
    to_remove = []
    total = 0
    faq_hanshi = 0
    no_url = 0
    valid_deep_link = 0

    after = None
    while True:
        kwargs = {"vector_store_id": vs_id, "limit": 100}
        if after:
            kwargs["after"] = after
        page = client.vector_stores.files.list(**kwargs)
        for f in page.data:
            total += 1
            attrs = f.attributes or {}
            doc_type = attrs.get("doc_type", "")
            if doc_type not in _TARGET_DOC_TYPES:
                continue
            faq_hanshi += 1
            url = attrs.get("url", "")
            if not url:
                no_url += 1
                continue
            if _is_homepage_url(url) or _is_gcis_article_query(url) or _is_dead_ip(url):
                to_remove.append((f.id, attrs, url))
            else:
                valid_deep_link += 1
        if not page.has_more:
            break
        after = page.data[-1].id
        if total % 2000 == 0:
            print(f"  已掃描 {total} …", flush=True)

    print(f"\n掃描完畢：共 {total} 個")
    print(f"  FAQ/函釋: {faq_hanshi}")
    print(f"  無 URL: {no_url}")
    print(f"  有效 deep link（保留）: {valid_deep_link}")
    print(f"  無效首頁/GCIS（待移除）: {len(to_remove)}")

    if args.dry_run:
        for fid, attrs, url in to_remove[:20]:
            dt = attrs.get("doc_type", "?")
            print(f"  {fid}  {dt:4s}  {url}")
        if len(to_remove) > 20:
            print(f"  … 還有 {len(to_remove) - 20} 個")
        return

    if not to_remove:
        print("無需移除。")
        return

    print(f"\n開始移除 {len(to_remove)} 個無效 URL …", flush=True)
    removed = 0
    errors = 0
    for fid, attrs, url in to_remove:
        new_attrs = {k: v for k, v in attrs.items() if k != "url"}
        try:
            client.vector_stores.files.update(
                vector_store_id=vs_id,
                file_id=fid,
                attributes=new_attrs,
            )
            removed += 1
            if removed % 50 == 0:
                print(f"  已移除 {removed}/{len(to_remove)} …", flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"  警告: {fid} 失敗: {e}")
            errors += 1

    print(f"\n完成：移除 {removed} 個、失敗 {errors} 個")


if __name__ == "__main__":
    main()
