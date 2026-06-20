"""升級腳本：將 fallback 列表/搜尋頁 URL 升級為具體內容頁 URL。

restore_source_urls.py 只處理無 URL 的檔案；本腳本處理「已有 URL 但指向
列表頁 / 搜尋頁而非內容頁」的情況。

用法:
    python -m scripts.upgrade_source_urls [--dry-run]
"""
import argparse
import os
import re
import sys
from pathlib import Path
from urllib.parse import quote

from openai import OpenAI

from scripts.ingest import (
    _NONARTICLE_RE,
    _REFNO_RE,
    _parse_first_line,
    _SFB_INSIDER_PDF,
    _SFB_SHAREHOLDING_PDF,
    construct_institutional_url,
)
from scripts._url_maps import (
    PDPC_HANSHI_MAP as _PDPC_HANSHI_MAP,
    FTC_FILE_MAP as _FTC_FILE_MAP,
    FSC_LAWCONTENT_MAP as _FSC_LAWCONTENT_MAP,
    BOLA_LIO_FILE_MAP as _BOLA_LIO_FILE_MAP,
)

_TARGET_DOC_TYPES = {"函釋", "FAQ"}
DATA_DIR = Path(__file__).resolve().parent.parent / "data"

# ── 列表/搜尋頁 URL（需被升級的 fallback） ──
_FALLBACK_URLS = {
    "https://law.fsc.gov.tw/LawSearchAll.aspx",
    "https://www.sfb.gov.tw/ch/home.jsp?id=858&parentpath=0,6",
    "https://www.sfb.gov.tw/ch/home.jsp?id=88&parentpath=0,3",
    "https://www.pdpc.gov.tw/News/101/",
    "https://www.pdpc.gov.tw/News/102/",
    "https://www.ftc.gov.tw/internet/main/doc/docList.aspx?uid=39&mid=39",
    "https://www.ftc.gov.tw/internet/main/doc/docList.aspx?uid=1201",
    "https://bola.gov.taipei/News.aspx?n=FDEDF5DCB0A26A46&sms=87415A8B9CE81B16",
    "https://laws.mol.gov.tw/FINT/FINTQRY04.aspx",
    "https://www.moj.gov.tw/2204/2528/2634/",
}

# NOTE: PDPC FAQ 的 NDC 遷移 nc_ 格式 URL（nc_11979_xxxxx）經 Playwright 驗證全是
# 死連結（404）——PDPC 改版後該 URL pattern 不再解析。故個資 FAQ 維持 /News/101/
# 列表頁 fallback（目前找不到可用的具體內容頁）。

_FSC_CATEGORIES = "A000%2cA101%2cA102%2cA103%2cA104%2cA105%2cA106%2cA107%2cA108%2cA109%2cB000%2cB001%2cB002%2cB003%2cB004%2cB005%2cB006%2cB007%2cB008%2cB009%2cB010%2cB011%2cB012%2cB013%2cB014%2cB015%2cB016%2cB017%2cC000%2cC001%2cC002%2cC003%2cC004%2cC005%2cC006%2cD000%2cD001%2cD002%2cD003%2cD004%2cD005%2cD006%2cD007%2cD008%2cE000%2cE001%2cE002%2cE003%2cE004%2cE005%2cE006%2cE007%2c"

# 臺北市勞動檢查處 FAQ 列表（正確域名；個別頁查無時 fallback）
_LIO_FAQ_URL = "https://lio.gov.taipei/News.aspx?n=DB345115745B8F8F&sms=87415A8B9CE81B16"

# MOL 函釋全文內容頁（FLAWDOC03，只需 N2=純數字文號）。
# 既有 FINTQRY05 URL 只到「相關法條」導覽頁、無全文，須升級。
_MOL_FLAWDOC_URL = (
    "https://laws.mol.gov.tw/FLAW/FLAWDOC03.aspx"
    "?datatype=etype&N2={eno}&cnt=1&now=1&lnabndn=1&recordno=1"
)


def _is_stale_url(url: str) -> bool:
    """URL 是否需升級：固定 fallback 列表頁，或非內容頁的導覽/列表頁。"""
    if url in _FALLBACK_URLS:
        return True
    if "FINTQRY05.aspx" in url:  # MOL 導覽頁（無全文）
        return True
    # bola/lio 列表頁（News.aspx，非 News_Content.aspx 個別頁）
    if "lio.gov.taipei/News.aspx" in url or "bola.gov.taipei/News.aspx" in url:
        return True
    # GCIS 法規總覽頁（getElawView）——有具體「之」條號者可升級到逐條頁；
    # 無條號者 _find_upgrade_url 回空、維持現狀。
    if "getElawView" in url:
        return True
    return False


def _extract_fsc_number(ref_no: str) -> str:
    """從字號提取純數字部分。例：'金管銀外 10650001375' → '10650001375'"""
    m = re.search(r'(\d{7,})', ref_no)
    return m.group(1) if m else ""


def _build_fsc_search_url(numeric: str) -> str:
    return (
        f"https://law.fsc.gov.tw/LawResult.aspx?"
        f"CategoryID={_FSC_CATEGORIES}"
        f"&NLawTypeID=all&GroupID=1%2c3%2c2%2c4%2c5"
        f"&LNumber={numeric}&now=1&fei=1"
    )


def _build_fsc_content_url(law_id: str) -> str:
    return f"https://law.fsc.gov.tw/LawContent.aspx?id={law_id}"


def _find_upgrade_url(filename: str, ref_no: str, source: str,
                      article: str = "", law: str = "",
                      doc_type: str = "") -> tuple[str, str]:
    """嘗試為已有 fallback URL 的檔案找到更好的 specific URL。"""
    # GCIS 法規總覽頁 → 逐條頁（「之」條號正規化由 construct_institutional_url 處理）
    gcis_url = construct_institutional_url(doc_type, source, law, article, ref_no)
    if gcis_url and "constructionDetailFromSingleLaw" in gcis_url:
        return gcis_url, "gcis_article"

    # PDPC 函釋 hardcoded
    if ref_no:
        pdpc_url = _PDPC_HANSHI_MAP.get(ref_no)
        if pdpc_url:
            return pdpc_url, "pdpc_content"

    # FTC hardcoded
    if filename in _FTC_FILE_MAP:
        return _FTC_FILE_MAP[filename], "ftc_content"

    # bola/lio 個別內容頁 hardcoded
    if filename in _BOLA_LIO_FILE_MAP:
        return _BOLA_LIO_FILE_MAP[filename], "bola_lio_content"

    src = source or ""
    # SFB 證期局問答集 PDF（須在 FSC 之前——來源含「金融監督」會誤入 FSC 分支）
    if "證券期貨局" in src or "證期局" in src:
        if "157-1" in article:
            return _SFB_INSIDER_PDF, "sfb_pdf"
        if "22-2" in article:
            return _SFB_SHAREHOLDING_PDF, "sfb_pdf"
        return "", ""

    # FSC: 用已知 LawContent ID（真內容頁）；查無 ID 則退 LawResult 搜尋頁
    # （仍是可用的搜尋頁，勝過已失效的 LawSearchAll 錯誤頁）。
    # 已知 6 個字號爬不到 LawContent（會落到 fsc_search）：
    #   10100369210, 09640002910, 10310004570, 10802151341（FSC 庫查無）
    #   0924000003, 0924000779（台財融時期、FSC 成立前，LawResult 回 0 筆）
    # 這 6 個未來若補到 LawContent ID 應加入 _FSC_LAWCONTENT_MAP。
    if "金管會" in src or "金融監督" in src:
        numeric = _extract_fsc_number(ref_no)
        if numeric:
            law_id = _FSC_LAWCONTENT_MAP.get(numeric)
            if law_id:
                return _build_fsc_content_url(law_id), "fsc_content"
            return _build_fsc_search_url(numeric), "fsc_search"

    # 臺北市勞動檢查處（修正域名）
    if "臺北市" in src and "勞動檢查" in src:
        return _LIO_FAQ_URL, "lio_fallback"

    # MOL 勞動部函釋：FINTQRY05 導覽頁 → FLAWDOC03 全文頁（N2=純數字文號）
    # 限「勞動部」避免誤抓臺北市勞動局/檢查處；共用 ingest 的 _REFNO_RE。
    if "勞動部" in src and ref_no:
        m = _REFNO_RE.match(ref_no)
        if m:
            return _MOL_FLAWDOC_URL.format(eno=m.group("eno")), "mol_content"

    return "", ""


def _build_local_file_data() -> dict[str, dict]:
    """掃描本地 data/ 所有 FAQ/函釋。"""
    index = {}
    for p in sorted(DATA_DIR.iterdir()):
        if not p.is_file() or p.suffix not in {".txt", ".md"}:
            continue
        stem = p.stem
        m = _NONARTICLE_RE.match(stem)
        if not m or m.group("doc_type") not in _TARGET_DOC_TYPES:
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        first_line = text.splitlines()[0] if text.splitlines() else ""
        meta = _parse_first_line(first_line)
        index[p.name] = {
            "meta": meta,
            "doc_type": m.group("doc_type"),
            "title": m.group("title"),
        }
    return index


def main():
    parser = argparse.ArgumentParser(description="升級 fallback URL 為 specific content URL")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    api_key = os.getenv("OPENAI_API_KEY")
    vs_id = os.getenv("VECTOR_STORE_ID")
    if not api_key or not vs_id:
        sys.exit("需設定 OPENAI_API_KEY 與 VECTOR_STORE_ID 環境變數")

    client = OpenAI(api_key=api_key)

    print("步驟 1/3：掃描本地 data/ …")
    local_data = _build_local_file_data()
    print(f"  本地 FAQ/函釋共 {len(local_data)} 個檔案")

    print("步驟 2/3：從 OpenAI files.list 建立 ID→filename 對照 …", flush=True)
    fileid_names = {}
    after_cursor = None
    while True:
        kwargs = {"purpose": "assistants", "limit": 100}
        if after_cursor:
            kwargs["after"] = after_cursor
        page = client.files.list(**kwargs)
        for f in page.data:
            fileid_names[f.id] = f.filename
        if not page.has_more:
            break
        after_cursor = page.data[-1].id
    print(f"  OpenAI 檔案共 {len(fileid_names)} 個")

    print(f"步驟 3/3：掃描 vector store {vs_id} …", flush=True)
    to_upgrade = []
    kept = 0
    not_target = 0
    no_url = 0
    already_good = 0
    method_stats: dict[str, int] = {}
    total_scanned = 0

    after_cursor = None
    while True:
        kwargs = {"vector_store_id": vs_id, "limit": 100}
        if after_cursor:
            kwargs["after"] = after_cursor
        page = client.vector_stores.files.list(**kwargs)
        for f in page.data:
            total_scanned += 1
            attrs = f.attributes or {}
            doc_type = attrs.get("doc_type", "")
            if doc_type not in _TARGET_DOC_TYPES:
                not_target += 1
                continue

            current_url = attrs.get("url", "")
            if not current_url:
                no_url += 1
                continue

            if not _is_stale_url(current_url):
                already_good += 1
                continue

            filename = fileid_names.get(f.id, "")
            data = local_data.get(filename)
            if not data:
                kept += 1
                continue

            ref_no = data["meta"].get("字號", "")
            source = data["meta"].get("來源", "")
            article = data["meta"].get("對應條號", "")
            if article == "未標明":
                article = ""
            law = data["meta"].get("母法", "")
            new_url, method = _find_upgrade_url(
                filename, ref_no, source, article, law, data["doc_type"],
            )

            if new_url and new_url != current_url:
                to_upgrade.append((f.id, attrs, new_url, current_url, filename, method))
                method_stats[method] = method_stats.get(method, 0) + 1
            else:
                kept += 1

        if not page.has_more:
            break
        after_cursor = page.data[-1].id
        if total_scanned % 2000 == 0:
            print(f"  已掃描 {total_scanned} …", flush=True)

    print(f"\n掃描完畢：共 {total_scanned} 個")
    print(f"  非 FAQ/函釋: {not_target}")
    print(f"  無 URL: {no_url}")
    print(f"  已是內容頁: {already_good}")
    print(f"  維持現狀: {kept}")
    print(f"  待升級: {len(to_upgrade)}")
    print(f"\n升級方法統計:")
    for method, count in sorted(method_stats.items(), key=lambda x: -x[1]):
        print(f"  {method}: {count}")

    if args.dry_run:
        print(f"\n預覽（前 30 筆）:")
        for fid, attrs, new_url, old_url, fn, method in to_upgrade[:30]:
            print(f"  {method:16s} {fn[:40]:40s}")
            print(f"    舊: {old_url[:80]}")
            print(f"    新: {new_url[:80]}")
        if len(to_upgrade) > 30:
            print(f"  … 還有 {len(to_upgrade) - 30} 個")
        return

    if not to_upgrade:
        print("無需升級。")
        return

    print(f"\n開始升級 {len(to_upgrade)} 個檔案 …", flush=True)
    updated = 0
    errors = 0
    skipped = 0
    for fid, attrs, new_url, old_url, fn, method in to_upgrade:
        # update 是「整組替換」非合併——attrs 必須完整，否則會洗掉 doc_type 等欄位。
        # 防 API 回傳殘缺 attributes（schema migration / 分頁不一致）導致靜默資料毀損。
        if "doc_type" not in attrs:
            skipped += 1
            print(f"  ⚠️  跳過 {fn}：attrs 缺 doc_type，避免覆寫毀損", file=sys.stderr)
            continue
        new_attrs = {**attrs, "url": new_url}
        try:
            client.vector_stores.files.update(
                vector_store_id=vs_id,
                file_id=fid,
                attributes=new_attrs,
            )
            updated += 1
            if updated % 20 == 0:
                print(f"  已升級 {updated}/{len(to_upgrade)} …", flush=True)
        except Exception as e:
            errors += 1
            print(f"  ❌ {fn}: {e}", file=sys.stderr)

    print(f"\n完成！升級 {updated} 個，失敗 {errors} 個，跳過 {skipped} 個。")


if __name__ == "__main__":
    main()
