"""查向量庫 doc_type×category 件數與語料截止月，產出 app/capability_manifest.json。
判決上傳後重跑即更新。用法：python scripts/build_capability_manifest.py"""
import json
import os
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANIFEST_PATH = ROOT / "app" / "capability_manifest.json"


def _load_dotenv():
    env = ROOT / ".env"
    if not env.exists():
        return
    for raw in env.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def build_manifest(client, vector_store_id):
    """回 {doc_type_counts, categories, cutoff, total}。逐頁掃描向量庫檔案的 attributes。"""
    doc_type_counts = Counter()
    categories = set()
    latest = ""
    total = 0
    after = None
    while True:
        page = client.vector_stores.files.list(vector_store_id=vector_store_id, limit=100, after=after)
        for f in page.data:
            total += 1
            attrs = f.attributes or {}
            dt = attrs.get("doc_type") if isinstance(attrs, dict) else None
            if dt:
                doc_type_counts[dt] += 1
            cat = attrs.get("category") if isinstance(attrs, dict) else None
            if cat:
                categories.add(cat)
            eff = (attrs.get("effective_date") or "") if isinstance(attrs, dict) else ""
            if eff > latest:
                latest = eff
        if not page.has_more:
            break
        after = page.data[-1].id
    return {
        "doc_type_counts": dict(doc_type_counts),
        "categories": sorted(categories),
        "cutoff": latest[:7] if latest else "",
        "total": total,
    }


def main():
    _load_dotenv()
    from openai import OpenAI
    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    m = build_manifest(client, os.environ["VECTOR_STORE_ID"])
    MANIFEST_PATH.write_text(json.dumps(m, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已寫 {MANIFEST_PATH}：{m['total']} 檔，doc_type={m['doc_type_counts']}，cutoff={m['cutoff']}")


if __name__ == "__main__":
    main()
