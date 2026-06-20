"""驗證腳本：匯出 vector store 中所有 FAQ/函釋 的 URL，供 Playwright 批次驗證。

用法:
    python -m scripts.verify_source_urls > url_verification_list.json
"""
import json
import os
import sys

from openai import OpenAI


def main():
    api_key = os.getenv("OPENAI_API_KEY")
    vs_id = os.getenv("VECTOR_STORE_ID")
    if not api_key or not vs_id:
        sys.exit("需設定 OPENAI_API_KEY 與 VECTOR_STORE_ID 環境變數")

    client = OpenAI(api_key=api_key)

    results = []
    after = None
    total = 0

    while True:
        kwargs = {"vector_store_id": vs_id, "limit": 100}
        if after:
            kwargs["after"] = after
        page = client.vector_stores.files.list(**kwargs)
        for f in page.data:
            attrs = getattr(f, "attributes", None) or {}
            doc_type = attrs.get("doc_type", "")
            if doc_type not in ("FAQ", "函釋"):
                continue
            total += 1
            url = attrs.get("url", "")
            results.append({
                "file_id": f.id,
                "doc_type": doc_type,
                "source": attrs.get("source", ""),
                "title": attrs.get("title", ""),
                "url": url,
            })
        if not page.has_more:
            break
        after = page.data[-1].id
        if total % 200 == 0:
            print(f"  掃描中… {total}", file=sys.stderr, flush=True)

    # Group by URL domain for parallel verification
    by_domain = {}
    no_url = []
    for r in results:
        url = r["url"]
        if not url:
            no_url.append(r)
            continue
        from urllib.parse import urlparse
        domain = urlparse(url).netloc
        by_domain.setdefault(domain, []).append(r)

    output = {
        "total": len(results),
        "has_url": len(results) - len(no_url),
        "no_url": len(no_url),
        "no_url_files": no_url,
        "by_domain": {
            domain: {"count": len(items), "sample_urls": [i["url"] for i in items[:3]]}
            for domain, items in sorted(by_domain.items(), key=lambda x: -len(x[1]))
        },
        "all_urls": [{"url": r["url"], "source": r["source"], "title": r["title"]} for r in results if r["url"]],
    }

    json.dump(output, sys.stdout, ensure_ascii=False, indent=2)
    print(f"\n\n總計 {len(results)} 個 FAQ/函釋，{len(results) - len(no_url)} 有 URL，{len(no_url)} 無 URL", file=sys.stderr)


if __name__ == "__main__":
    main()
