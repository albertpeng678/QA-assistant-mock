"""前處理台灣法院裁判書 JSON → 語料檔，並 append 上傳到既有 file_search vector store。

流程：
  1. 遞迴掃描已解壓的裁判書 JSON（每筆一檔，UTF-8）。
  2. 規則派篩選：JFULL 命中 10 個法遵領域法規全名者才處理；母法取「第一個命中」。
  3. 前處理成既有語料格式：檔名 {法院簡稱}-判決-{JTITLE}.txt、首行管線 metadata、空一行接 JFULL（保留換行）。
  4. attributes 複用 ingest.derive_attributes（doc_type=判決），再補 court / case_type。
  5. append 上傳到 .env 的 VECTOR_STORE_ID（判決專屬 chunk：2048 / 512），分批 ≤500、單檔容錯。

用法（先 dry-run 小批驗證）：
  python scripts/ingest_judgments.py --dry-run --per-domain 5
  python scripts/ingest_judgments.py --per-domain 20            # 寫檔 + 上傳（小批）
  python scripts/ingest_judgments.py                            # 全量寫檔 + 上傳
"""
import argparse
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path

# 複用既有 ingest 的 attributes 衍生與空殼判定，不重造。
sys.path.insert(0, str(Path(__file__).resolve().parent))
from ingest import derive_attributes, is_empty_article  # noqa: E402

# —— 法規全名 → 領域(category) 對照；判定「母法」用「第一個命中」，依此 dict 的插入順序 ——
# category 對齊既有 LAW_CATEGORY 的值。
JUDGMENT_LAW_CATEGORY = {
    "個人資料保護法": "個資",
    "洗錢防制法": "洗錢防制",
    "資恐防制法": "洗錢防制",
    "勞動基準法": "勞動",
    "性別平等工作法": "勞動",
    "性別工作平等法": "勞動",
    "證券交易法": "公司治理",
    "公司法": "公司治理",
    "公平交易法": "公平交易",
    "營業秘密法": "營業秘密",
    "消費者保護法": "消費者保護",
}

# Windows 非法檔名字元 + 逗號（JID/標題可能含逗號）。
_ILLEGAL_FN = re.compile(r'[\\/:*?"<>|,]')

# 預設測試資料來源（已解壓的裁判書 JSON 根目錄）。
DEFAULT_SOURCE_DIR = Path(r"C:\Users\albertpeng\judicial_work\extracted")
OUTPUT_DIR = Path(__file__).resolve().parent.parent / "data" / "judgments"

# 判決專屬 chunk 策略（context7 定案）：判決全文偏長，用較大 chunk + 較大 overlap。
JUDGMENT_CHUNKING_STRATEGY = {
    "type": "static",
    "static": {
        "max_chunk_size_tokens": 2048,
        "chunk_overlap_tokens": 512,
    },
}

BATCH_SIZE = 500


def _load_dotenv():
    """專案無 python-dotenv，手動讀根目錄 .env 補進 os.environ（不覆蓋既有值）。"""
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if not env_path.exists():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        os.environ.setdefault(k, v)


def matched_laws(jfull: str, exclude: set = None) -> list:
    """回傳 JFULL 命中的法規全名清單（依 JUDGMENT_LAW_CATEGORY 順序，去重保序）。

    exclude：要排除的法規全名集合（如洗錢防制法假陽性高，PoC 階段排除）。
    若一案僅命中被排除法規 → 回傳空清單 → 上層跳過該案。
    """
    if not jfull:
        return []
    exclude = exclude or set()
    return [law for law in JUDGMENT_LAW_CATEGORY if law in jfull and law not in exclude]


def derive_court(jid: str, dir_name: str) -> str:
    """法院名稱：優先用目錄名（如『三重簡易庭刑事』），去尾端 民事/刑事/行政 案類後綴。"""
    name = dir_name or ""
    for suffix in ("民事", "刑事", "行政"):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
            break
    return name.strip() or (jid.split(",", 1)[0] if jid else "")


def derive_case_type(jcase: str, dir_name: str) -> str:
    """案類：優先 JCASE；輔以目錄尾端 民事/刑事/行政 推得大類。"""
    if jcase:
        return jcase
    for suffix in ("民事", "刑事", "行政"):
        if (dir_name or "").endswith(suffix):
            return suffix
    return ""


def jdate_to_iso(jdate: str) -> str:
    """JDATE 'YYYYMMDD' → 'YYYY-MM-DD'；非 8 碼數字則原樣回傳。"""
    s = (jdate or "").strip()
    if re.fullmatch(r"\d{8}", s):
        return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
    return s


def _safe(part: str) -> str:
    """清理檔名片段：移除 Windows 非法字元與逗號、壓多餘空白。"""
    return _ILLEGAL_FN.sub("", part or "").strip().replace("　", " ").strip()


def build_filename(jid: str, jtitle: str, court: str) -> str:
    """檔名 {法院簡稱}-判決-{JTITLE}.txt；JID 衍生短碼確保唯一、JTITLE 過長截斷。"""
    jid_code = _safe(jid).replace(" ", "")  # 逗號已被 _safe 移除，整段當唯一短碼
    title = _safe(jtitle)[:40] or "裁判"
    court_s = _safe(court) or "法院"
    return f"{court_s}-判決-{title}-{jid_code}.txt"


def build_metadata_line(jid, jdate, mother_law, court, jcase) -> str:
    """首行管線 metadata，供 ingest._parse_first_line / derive_attributes 解析。

    對應條號留空：判決一案常引多條、無單一條號，若塞法規名會污染 article schema
    （article 語意是「第N條」）。命中法規清單改存 attrs["related_laws"]（見 process_one）。
    """
    return (
        "來源:司法院裁判書系統"
        "|效力:判決"
        f"|字號:{jid}"
        f"|裁判日:{jdate_to_iso(jdate)}"
        "|對應條號:"
        f"|母法:{mother_law}"
        f"|法院:{court}"
        f"|案由:{jcase}"
    )


def process_one(json_path: Path, exclude: set = None, strict: bool = False, min_count: int = 3):
    """讀單筆 JSON → (out_filename, file_text, attributes, category) 或 None（未命中/空殼）。"""
    try:
        data = json.loads(json_path.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        print(f"警告: 解析 JSON 失敗，已跳過 {json_path.name}: {e}")
        return None

    jfull = data.get("JFULL") or ""
    if is_empty_article(jfull):
        return None
    hit_laws = matched_laws(jfull, exclude)
    if not hit_laws:
        return None

    mother_law = hit_laws[0]
    jtitle = data.get("JTITLE", "") or ""
    # 精篩（strict）：只留「主爭點＝母法」的判決——母法在全文出現 >= min_count 次，
    # 或出現在案由標題；濾掉「附帶引用一次」的假陽性（如刑案附帶提及公司法）。
    # 實測（202603）：粗篩 1971/月 → strict(>=3) 535/月，降約 73%，公司法假陽性最明顯。
    if strict and jfull.count(mother_law) < min_count and mother_law not in jtitle:
        return None
    category = JUDGMENT_LAW_CATEGORY[mother_law]
    dir_name = json_path.parent.name
    jid = data.get("JID", "")
    court = derive_court(jid, dir_name)
    case_type = derive_case_type(data.get("JCASE", ""), dir_name)

    first_line = build_metadata_line(
        jid, data.get("JDATE", ""), mother_law, court, data.get("JCASE", "")
    )
    file_text = first_line + "\n\n" + jfull  # 保留 JFULL 原始換行

    out_filename = build_filename(jid, jtitle, court)
    attrs = derive_attributes(out_filename, law_index=None, first_line=first_line)
    # 補裁判書專屬鍵；確保值皆 str。derive_attributes 已給 category（由母法對應），此處保險覆蓋。
    attrs["category"] = category
    attrs["court"] = str(court)
    attrs["case_type"] = str(case_type)
    # 命中法規清單存獨立鍵（不污染 article）；多領域判決可保留交叉領域資訊。
    attrs["related_laws"] = "、".join(hit_laws)
    return out_filename, file_text, attrs, category


def collect(source_dir: Path, per_domain: int, limit: int, exclude: set = None,
            strict: bool = False, min_count: int = 3):
    """掃描 → 前處理 → 寫檔。回傳 (written_specs, stats)。

    written_specs: [(out_path, attributes), ...]
    stats: dict(category -> 命中數)，另含 total_scanned / total_written。
    """
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    per_domain_count = Counter()
    domain_hits = Counter()
    written_specs = []
    total_scanned = 0
    seen_names = set()

    for json_path in source_dir.rglob("*.json"):
        total_scanned += 1
        result = process_one(json_path, exclude, strict, min_count)
        if result is None:
            continue
        out_filename, file_text, attrs, category = result
        domain_hits[category] += 1

        if per_domain and per_domain_count[category] >= per_domain:
            continue
        if limit and len(written_specs) >= limit:
            break

        # 檔名去重（JID 短碼已確保唯一，但保險處理罕見碰撞）。
        out_filename = _dedupe_name(out_filename, seen_names)
        seen_names.add(out_filename)

        out_path = OUTPUT_DIR / out_filename
        out_path.write_text(file_text, encoding="utf-8")
        written_specs.append((out_path, attrs))
        per_domain_count[category] += 1

    stats = {
        "domain_hits": dict(domain_hits),
        "per_domain_written": dict(per_domain_count),
        "total_scanned": total_scanned,
        "total_written": len(written_specs),
    }
    return written_specs, stats


def _dedupe_name(name: str, seen: set) -> str:
    if name not in seen:
        return name
    stem, suffix = name[: -len(".txt")], ".txt"
    i = 2
    while f"{stem}_{i}{suffix}" in seen:
        i += 1
    return f"{stem}_{i}{suffix}"


def upload(written_specs, vector_store_id: str):
    """逐檔上傳 file_id → 組 file_specs（判決 chunking）→ 分批 create_and_poll。容錯：單檔失敗跳過。"""
    from openai import OpenAI

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        sys.exit("請先設定 OPENAI_API_KEY（.env 或環境變數）")
    if not vector_store_id:
        sys.exit("請設定 VECTOR_STORE_ID（.env 或環境變數）—— append 既有 store，絕不新建")

    client = OpenAI(api_key=api_key)
    vector_store = client.vector_stores.retrieve(vector_store_id)
    print(f"附加模式：沿用既有 vector store {vector_store.id}（判決 append，不新建）")

    file_specs = []
    for out_path, attrs in written_specs:
        try:
            with open(out_path, "rb") as fh:
                f = client.files.create(file=fh, purpose="assistants")
        except Exception as e:  # noqa: BLE001
            print(f"警告: 上傳檔案失敗，已跳過 {out_path.name}: {e}")
            continue
        file_specs.append({
            "file_id": f.id,
            "attributes": attrs,
            "chunking_strategy": JUDGMENT_CHUNKING_STRATEGY,
        })

    if not file_specs:
        sys.exit("沒有任何檔案成功上傳，中止。")

    total = len(file_specs)
    print(f"共 {total} 個判決檔待掛入，分批每批最多 {BATCH_SIZE} 檔。")
    for i in range(0, total, BATCH_SIZE):
        batch_specs = file_specs[i:i + BATCH_SIZE]
        batch = client.vector_stores.file_batches.create_and_poll(
            vector_store_id=vector_store.id,
            files=batch_specs,
        )
        print(f"批次 {i // BATCH_SIZE + 1}（{len(batch_specs)} 檔）狀態: {batch.status}；計數: {batch.file_counts}")

    print(f"\n總計: 已掛入 {total} 個判決檔到 {vector_store.id}。")


def main():
    # Windows 主控台輸出 UTF-8，避免中文亂碼。
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass

    parser = argparse.ArgumentParser(description="前處理裁判書 JSON 並 append 上傳到既有 vector store")
    parser.add_argument("--source-dir", default=str(DEFAULT_SOURCE_DIR), help="已解壓裁判書 JSON 根目錄")
    parser.add_argument("--per-domain", type=int, default=0, help="每個領域最多取 N 筆（小批驗證用）")
    parser.add_argument("--limit", type=int, default=0, help="輸出總上限")
    parser.add_argument("--dry-run", action="store_true", help="只前處理寫檔＋印統計，不上傳")
    parser.add_argument("--no-upload", action="store_true", help="只寫檔不上傳")
    parser.add_argument("--exclude-laws", default="", help="排除的法規全名（逗號分隔），如『洗錢防制法,資恐防制法』")
    parser.add_argument("--strict", action="store_true", help="精篩：只收主爭點判決（母法全文出現>=min-count次或在案由）")
    parser.add_argument("--min-count", type=int, default=3, help="strict 模式下母法出現次數門檻（預設 3）")
    args = parser.parse_args()

    _load_dotenv()

    source_dir = Path(args.source_dir)
    if not source_dir.exists():
        sys.exit(f"來源目錄不存在: {source_dir}")

    exclude = {s.strip() for s in args.exclude_laws.split(",") if s.strip()}
    if exclude:
        print(f"排除法規: {'、'.join(sorted(exclude))}")

    print(f"掃描來源: {source_dir}")
    print(f"輸出目錄: {OUTPUT_DIR}")
    if args.strict:
        print(f"精篩模式（strict）：只收主爭點判決，母法出現門檻 >= {args.min_count} 次或在案由")
    written_specs, stats = collect(source_dir, args.per_domain, args.limit, exclude,
                                   args.strict, args.min_count)

    print("\n=== 統計 ===")
    print(f"掃描 JSON 檔數: {stats['total_scanned']}")
    print("各領域命中數（全量，未受 per-domain/limit 限制）:")
    for cat, n in sorted(stats["domain_hits"].items(), key=lambda x: -x[1]):
        print(f"  {cat}: {n}")
    print("各領域實際寫出檔數:")
    for cat, n in sorted(stats["per_domain_written"].items(), key=lambda x: -x[1]):
        print(f"  {cat}: {n}")
    print(f"總寫出檔數: {stats['total_written']}（位於 {OUTPUT_DIR}）")

    if args.dry_run or args.no_upload:
        print("\n[dry-run / no-upload] 已寫檔，未上傳。")
        return

    if not written_specs:
        print("無檔案可上傳。")
        return

    upload(written_specs, os.getenv("VECTOR_STORE_ID"))


if __name__ == "__main__":
    main()
