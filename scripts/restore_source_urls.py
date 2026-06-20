"""遷移腳本：為 vector store 中所有缺 URL 的 FAQ/函釋 補上正確來源 URL。

策略（三層 fallback）：
  1. body 內提取（CPC deep link 等）
  2. construct_institutional_url（GCIS 逐條頁、MOL FLAWDOC03 全文頁、SFB 問答集 PDF）
  3. 各機構網頁爬取（PDPC / FTC / SFB / FSC / AMLD / MOJ / 臺北市）

注意：本腳本只補「缺 URL」的檔；既有列表/搜尋/死連結 URL 的升級見
scripts/upgrade_source_urls.py。

判決/裁罰不處理。

用法:
    python -m scripts.restore_source_urls [--dry-run]
"""
import argparse
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import quote

from openai import OpenAI

from scripts.ingest import (
    _NONARTICLE_RE,
    _parse_first_line,
    construct_institutional_url,
    extract_url_from_body,
)
from scripts._url_maps import (
    PDPC_HANSHI_MAP as _PDPC_HANSHI_MAP,
    FTC_FILE_MAP as _FTC_FILE_MAP,
)

_TARGET_DOC_TYPES = {"函釋", "FAQ"}
DATA_DIR = Path(__file__).resolve().parent.parent / "data"

# ── 各機構 URL 建構常數 ──

# PDPC 函釋列表頁（category 102 = 函釋要旨）
_PDPC_HANSHI_LIST = "https://www.pdpc.gov.tw/News/102/"
_PDPC_FAQ_LIST = "https://www.pdpc.gov.tw/News/101/"
_PDPC_CONTENT_URL = "https://www.pdpc.gov.tw/News_Content/{cat}/{item_id}/"

# FTC
_FTC_FAQ_LIST = "https://www.ftc.gov.tw/internet/main/doc/docList.aspx?uid=1201"
_FTC_HANSHI_LIST = "https://www.ftc.gov.tw/internet/main/doc/docList.aspx?uid=39&mid=39"

# SFB 函釋
_SFB_HANSHI_LIST = "https://www.sfb.gov.tw/ch/home.jsp?id=88&parentpath=0,3"
_SFB_FAQ_LIST = "https://www.sfb.gov.tw/ch/home.jsp?id=858&parentpath=0,6"

# FSC (law.fsc.gov.tw)
_FSC_LAW_URL = "https://law.fsc.gov.tw/LawContent.aspx?id={law_id}"

# MOL 勞動法令查詢系統
_MOL_SEARCH_URL = "https://laws.mol.gov.tw/FINT/FINTQRY04.aspx"

# AMLD (洗錢防制處，現在 mjib.gov.tw)
_AMLD_FAQ_PAGE = "https://www.mjib.gov.tw/EditPage/?PageID=15b26af4-e669-4141-95e3-e66b1cfe9fe0"
_AMLD_HANSHI_PAGE = "https://www.mjib.gov.tw/EditPage/?PageID=e3dc5929-91bb-4cbf-a66c-0ee7069ea12c"

# 法務部
_MOJ_FAQ_URL = "https://www.moj.gov.tw/2204/2528/2634/"

# 臺北市勞動檢查處
_BOLA_FAQ_URL = "https://bola.gov.taipei/News.aspx?n=FDEDF5DCB0A26A46&sms=87415A8B9CE81B16"

# 臺北市政府勞動局
_LABOR_FAQ_URL = "https://bola.gov.taipei/News.aspx?n=FDEDF5DCB0A26A46&sms=87415A8B9CE81B16"


def _try_fetch_listing(url: str, timeout: int = 10) -> str:
    """嘗試抓取網頁內容，回傳 HTML 或空字串。"""
    try:
        import urllib.request
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except Exception:
        return ""


def _build_pdpc_mapping() -> dict[str, str]:
    """爬 PDPC 函釋列表，建立 字號→URL 映射。"""
    mapping = {}
    for cat, list_url in [("102", _PDPC_HANSHI_LIST), ("101", _PDPC_FAQ_LIST)]:
        html = _try_fetch_listing(list_url)
        if not html:
            continue
        for m in re.finditer(r'/News_Content/' + cat + r'/(\d+)/', html):
            item_id = m.group(1)
            mapping[f"pdpc_{cat}_{item_id}"] = _PDPC_CONTENT_URL.format(cat=cat, item_id=item_id)
    return mapping


def _find_pdpc_url_by_refno(ref_no: str, title: str) -> str:
    """在 PDPC 網站搜尋特定函釋的 URL。"""
    if not ref_no:
        return ""
    search_url = f"https://www.pdpc.gov.tw/News/102/?keyword={quote(ref_no)}"
    html = _try_fetch_listing(search_url)
    if html:
        m = re.search(r'/News_Content/102/(\d+)/', html)
        if m:
            return _PDPC_CONTENT_URL.format(cat="102", item_id=m.group(1))
    return ""


def _find_ftc_url(ref_no: str, title: str, doc_type: str) -> str:
    """在 FTC 網站搜尋特定函釋/FAQ 的 URL。"""
    if doc_type == "函釋" and ref_no:
        search_url = f"https://www.ftc.gov.tw/internet/main/doc/docList.aspx?uid=39&mid=39&keyword={quote(ref_no)}"
    else:
        keyword = title[:20] if title else ""
        if not keyword:
            return ""
        search_url = f"https://www.ftc.gov.tw/internet/main/doc/docList.aspx?uid=1201&keyword={quote(keyword)}"
    html = _try_fetch_listing(search_url)
    if html:
        m = re.search(r'docDetail\.aspx\?uid=(\d+)&(?:amp;)?docid=(\d+)&(?:amp;)?mid=(\d+)', html)
        if m:
            return f"https://www.ftc.gov.tw/internet/main/doc/docDetail.aspx?uid={m.group(1)}&docid={m.group(2)}&mid={m.group(3)}"
    return ""


def _find_sfb_url(ref_no: str, title: str, doc_type: str) -> str:
    """在 SFB 網站搜尋 FAQ 的 URL。"""
    keyword = title[:20] if title else ""
    if not keyword:
        return ""
    search_url = f"https://www.sfb.gov.tw/ch/home.jsp?id=858&parentpath=0,6&mcustomize=faq_view.jsp&keyword={quote(keyword)}"
    html = _try_fetch_listing(search_url)
    if html:
        m = re.search(r'faq_view\.jsp[^"]*dataserno=(\d+)', html)
        if m:
            return f"https://www.sfb.gov.tw/ch/home.jsp?id=858&parentpath=0,6&mcustomize=faq_view.jsp&dataserno={m.group(1)}"
    return ""


def _find_fsc_url(ref_no: str) -> str:
    """在 FSC 法規系統搜尋函釋的 URL。"""
    if not ref_no:
        return ""
    search_url = f"https://law.fsc.gov.tw/LawSearch.aspx?keyword={quote(ref_no)}"
    html = _try_fetch_listing(search_url)
    if html:
        m = re.search(r'LawContent\.aspx\?id=([A-Z0-9]+)', html)
        if m:
            return _FSC_LAW_URL.format(law_id=m.group(1))
    return ""


def _get_agency_fallback_url(source: str, doc_type: str, law: str) -> str:
    """當搜尋失敗時，回傳該機構最接近的列表/分類頁。

    NOTE: 回傳列表/分類頁而非首頁——首頁不符 AC。
    優先順序很重要：先匹配更具體的機構（證期局 before 金管會）。
    """
    src = source or ""
    # 證期局（比金管會更具體，先匹配）
    if "證券" in src or "期貨" in src:
        if doc_type == "FAQ":
            return _SFB_FAQ_LIST
        return _SFB_HANSHI_LIST
    # 金管會（不含證期局）
    if "金管會" in src or "金融監督" in src:
        # law.fsc.gov.tw/ 是首頁，不用。改用行政規則查詢列表。
        return "https://law.fsc.gov.tw/LawSearchAll.aspx"
    # 公平交易委員會
    if "公平交易" in src:
        if doc_type == "FAQ":
            return _FTC_FAQ_LIST
        return _FTC_HANSHI_LIST
    # PDPC / 國發會個資
    if "個人資料保護" in src or "個資" in src or "國家發展委員會" in src or "國發會" in src:
        if doc_type == "FAQ":
            return _PDPC_FAQ_LIST
        return _PDPC_HANSHI_LIST
    # AMLD
    if "洗錢" in law or "法務部調查局洗錢" in src:
        if doc_type == "FAQ":
            return _AMLD_FAQ_PAGE
        return _AMLD_HANSHI_PAGE
    # 法務部（洗錢外）
    if "法務部" in src:
        if "洗錢" in law:
            return _AMLD_FAQ_PAGE
        return _MOJ_FAQ_URL
    # 臺北市勞動
    if "臺北市" in src and "勞動" in src:
        return _BOLA_FAQ_URL
    # 勞動部（MOL FAQ 無字號時）
    if "勞動" in src:
        return _MOL_SEARCH_URL
    return ""


def _build_local_file_data() -> dict[str, dict]:
    """掃描本地 data/ 所有 FAQ/函釋 → filename → {first_line, body, meta, doc_type, title}。"""
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
            "first_line": first_line,
            "body": text,
            "meta": meta,
            "doc_type": m.group("doc_type"),
            "title": m.group("title"),
        }
    return index


def _find_url_for_file(data: dict, filename: str = "") -> tuple[str, str]:
    """嘗試為單一檔案找 URL。回傳 (url, method)。"""
    meta = data["meta"]
    doc_type = data["doc_type"]
    source = meta.get("來源", "")
    law = meta.get("母法", "")
    article = meta.get("對應條號", "")
    if article == "未標明":
        article = ""
    ref_no = meta.get("字號", "")
    eff_date = meta.get("發文日") or meta.get("發布日", "")
    title = data["title"]

    # 方法 0：硬編碼映射（手動從各機構網站查得，最可靠）
    if ref_no:
        pdpc_url = _PDPC_HANSHI_MAP.get(ref_no)
        if pdpc_url:
            return pdpc_url, "pdpc_hardcoded"
    if filename and filename in _FTC_FILE_MAP:
        return _FTC_FILE_MAP[filename], "ftc_hardcoded"

    # 方法 1：body 提取
    body_url = extract_url_from_body(data["body"])
    if body_url:
        return body_url, "body_extract"

    # 方法 2：construct_institutional_url（GCIS + MOL）
    inst_url = construct_institutional_url(doc_type, source, law, article, ref_no, eff_date)
    if inst_url:
        return inst_url, "construct"

    # 方法 3：各機構網頁搜尋
    src = source or ""

    # PDPC
    if "個人資料保護" in src or "個資" in src or "國家發展委員會" in src or "國發會" in src:
        url = _find_pdpc_url_by_refno(ref_no, title)
        if url:
            return url, "pdpc_search"

    # FTC
    if "公平交易" in src:
        url = _find_ftc_url(ref_no, title, doc_type)
        if url:
            return url, "ftc_search"

    # SFB
    if "證券" in src or "期貨" in src:
        url = _find_sfb_url(ref_no, title, doc_type)
        if url:
            return url, "sfb_search"

    # FSC
    if "金管會" in src or "金融監督" in src:
        url = _find_fsc_url(ref_no)
        if url:
            return url, "fsc_search"

    # 方法 4：機構 fallback（列表/分類頁）
    fallback = _get_agency_fallback_url(source, doc_type, law)
    if fallback:
        return fallback, "agency_fallback"

    return "", "none"


def _build_fileid_to_name(client: OpenAI) -> dict[str, str]:
    """從 OpenAI files.list 建 file_id→filename 對照。"""
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
    parser = argparse.ArgumentParser(description="為所有 FAQ/函釋補上來源 URL")
    parser.add_argument("--dry-run", action="store_true", help="只列出要更新的，不實際更新")
    parser.add_argument("--no-search", action="store_true", help="不搜尋各機構網頁（只用本地方法）")
    args = parser.parse_args()

    api_key = os.getenv("OPENAI_API_KEY")
    vs_id = os.getenv("VECTOR_STORE_ID")
    if not api_key or not vs_id:
        sys.exit("需設定 OPENAI_API_KEY 與 VECTOR_STORE_ID 環境變數")

    client = OpenAI(api_key=api_key)

    # 步驟 1：建立本地檔案資料
    print("步驟 1/3：掃描本地 data/ …")
    local_data = _build_local_file_data()
    print(f"  本地 FAQ/函釋共 {len(local_data)} 個檔案")

    # 步驟 2：建立 file_id→filename 對照
    print("步驟 2/3：從 OpenAI files.list 建立 ID→filename 對照 …", flush=True)
    fileid_names = _build_fileid_to_name(client)
    print(f"  OpenAI 檔案共 {len(fileid_names)} 個")

    # 步驟 3：掃描 vector store
    print(f"步驟 3/3：掃描 vector store {vs_id} …", flush=True)
    to_update = []
    already_has_url = 0
    not_target = 0
    no_local = 0
    no_url_found = 0
    method_stats = {}
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
            if doc_type not in _TARGET_DOC_TYPES:
                not_target += 1
                continue
            if attrs.get("url"):
                already_has_url += 1
                continue

            filename = fileid_names.get(f.id, "")
            data = local_data.get(filename)
            if not data:
                no_local += 1
                continue

            if args.no_search:
                meta = data["meta"]
                ref = meta.get("字號", "")
                # 先查硬編碼映射
                pdpc_url = _PDPC_HANSHI_MAP.get(ref) if ref else None
                ftc_url = _FTC_FILE_MAP.get(filename)
                if pdpc_url:
                    url, method = pdpc_url, "pdpc_hardcoded"
                elif ftc_url:
                    url, method = ftc_url, "ftc_hardcoded"
                else:
                    body_url = extract_url_from_body(data["body"])
                    if body_url:
                        url, method = body_url, "body_extract"
                    else:
                        inst_url = construct_institutional_url(
                            doc_type, meta.get("來源", ""), meta.get("母法", ""),
                            meta.get("對應條號", ""), ref,
                            meta.get("發文日") or meta.get("發布日", ""),
                        )
                        if inst_url:
                            url, method = inst_url, "construct"
                        else:
                            fallback = _get_agency_fallback_url(
                                meta.get("來源", ""), doc_type, meta.get("母法", ""),
                            )
                            if fallback:
                                url, method = fallback, "agency_fallback"
                            else:
                                url, method = "", "none"
            else:
                url, method = _find_url_for_file(data, filename)

            if url:
                to_update.append((f.id, attrs, url, filename, method))
                method_stats[method] = method_stats.get(method, 0) + 1
            else:
                no_url_found += 1

        if not page.has_more:
            break
        after = page.data[-1].id
        if total_scanned % 2000 == 0:
            print(f"  已掃描 {total_scanned} …", flush=True)

    print(f"\n掃描完畢：共 {total_scanned} 個")
    print(f"  非 FAQ/函釋: {not_target}")
    print(f"  已有 URL: {already_has_url}")
    print(f"  無本地檔案: {no_local}")
    print(f"  待更新: {len(to_update)}")
    print(f"  無法取得 URL: {no_url_found}")
    print(f"\n方法統計:")
    for method, count in sorted(method_stats.items(), key=lambda x: -x[1]):
        print(f"  {method}: {count}")

    if args.dry_run:
        print(f"\n預覽（前 30 筆）:")
        for fid, attrs, url, fn, method in to_update[:30]:
            dt = attrs.get("doc_type", "?")
            src = attrs.get("source", "?")[:12]
            print(f"  {dt:4s} {src:12s} {method:16s} → {url[:70]}")
        if len(to_update) > 30:
            print(f"  … 還有 {len(to_update) - 30} 個")
        return

    if not to_update:
        print("無需更新。")
        return

    print(f"\n開始更新 {len(to_update)} 個檔案 …", flush=True)
    updated = 0
    errors = 0
    for fid, attrs, url, fn, method in to_update:
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
        except Exception as e:
            print(f"  警告: 更新 {fid} ({fn}) 失敗: {e}")
            errors += 1

    print(f"\n完成：更新 {updated} 個、失敗 {errors} 個")


if __name__ == "__main__":
    main()
