"""語料取得與切檔：全國法規資料庫 RAW XML → 一條一檔到 data/。

策略：法條全覆蓋 + 個資旗艦深度。每部白名單法規逐條切成單檔
（檔名含條號，如 `個人資料保護法-第12條.txt`），讓每檔成為天然 chunk 邊界，
配合 OpenAI file search 的 static chunking（見 scripts/ingest.py）。

來源
----
全國法規資料庫開放 XML（政府資料開放授權 v1，免申請、可商用、須註明出處）：
    https://sendlaw.moj.gov.tw/PublicData/GetFile.ashx?DType=RAW_XML&AuData=CF

XML 結構（依 kong0107/mojLawSplit `lib/parseXML.js` 驗證）
-------------------------------------------------------
    <法規資料庫>
      <法規>
        <法規名稱>個人資料保護法</法規名稱>      <!-- 或 <中文法規名稱> 作為 fallback -->
        <法規內容>
          <編章節>第 一 章 ...</編章節>          <!-- 干擾節點，略過 -->
          <條文>
            <條號>第 十二 條</條號>
            <條文內容>...</條文內容>
          </條文>
          ...
        </法規內容>
      </法規>
      ...
    </法規資料庫>

本腳本不假設固定巢狀深度，改以 `iter()` 找所有 <條文> 後代節點，對標籤微幅變動較穩健。

用法
----
    # 連網下載（會抓全量 XML，請斟酌）
    python scripts/fetch_corpus.py

    # 吃本地已下載的 XML（離線、測試、可重現）
    python scripts/fetch_corpus.py --xml path/to/CF.xml
    # 或環境變數
    set CORPUS_XML_PATH=path/to/CF.xml & python scripts/fetch_corpus.py

    # 覆寫白名單（逗號分隔）
    python scripts/fetch_corpus.py --laws 個人資料保護法,洗錢防制法

旗艦個資的「非條文素材」放置慣例（人工放置，本腳本不負責）
--------------------------------------------------------
個資的 QA-pair / 函釋 / 裁罰案例等非條文素材，請人工放進 data/，檔名形如：
    個資-函釋-外洩通報Q&A.txt
    個資-裁罰-某案.txt
內文「首行」標註來源/版次/對應條號，例如：
    來源:國發會函釋 字第000號 | 版次:2024-01 | 對應條號:第12條
讓引用可回溯、且與條文檔共用同一套 file search 索引。
"""
import argparse
import os
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

RAW_XML_URL = (
    "https://sendlaw.moj.gov.tw/PublicData/GetFile.ashx?DType=RAW_XML&AuData=CF"
)

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

# 預設白名單：法條全覆蓋的核心法遵法規
DEFAULT_WHITELIST = {
    "個人資料保護法",
    "洗錢防制法",
    "勞動基準法",
    "性別平等工作法",
    "證券交易法",
    "公司法",
    "公平交易法",
    "營業秘密法",
    "消費者保護法",
}

# Windows 不合法檔名字元： \ / : * ? " < > |
_ILLEGAL_FILENAME_CHARS = re.compile(r'[\\/:*?"<>|]')


@dataclass
class Article:
    number: str   # 正規化後條號，如 "第12條" / "第15條之1"
    content: str  # 條文內容文字


# ---------------------------------------------------------------------------
# 條號正規化
# ---------------------------------------------------------------------------
_CN_DIGIT = {
    "零": 0, "〇": 0,
    "一": 1, "二": 2, "兩": 2, "三": 3, "四": 4,
    "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
}
_CN_UNIT = {"十": 10, "百": 100, "千": 1000}


def _cn_to_int(cn: str) -> Optional[int]:
    """中文數字字串 → int。支援 一~九千、十/百/千、零。失敗回 None。"""
    cn = cn.strip()
    if not cn:
        return None
    # 純阿拉伯數字直接回傳
    if cn.isdigit():
        return int(cn)

    total = 0
    section = 0  # 當前累積（未乘單位前）
    last_was_unit = False
    for ch in cn:
        if ch in _CN_DIGIT:
            section = _CN_DIGIT[ch]
            last_was_unit = False
        elif ch in _CN_UNIT:
            unit = _CN_UNIT[ch]
            # 「十二」開頭省略「一」：十 -> 10
            if section == 0:
                section = 1
            total += section * unit
            section = 0
            last_was_unit = True
        else:
            return None  # 出現非預期字元
    total += section
    if total == 0 and not last_was_unit:
        return None
    return total


def normalize_article_number(raw: str) -> str:
    """條號正規化：'第 十二 條' -> '第12條'，'第 十五 條之 一' -> '第15條之1'。

    同時容許輸入已是阿拉伯數字（'第 1 條' -> '第1條'）。
    """
    if raw is None:
        return ""
    s = raw.strip()
    # 拆出主條號與「之N」
    # 例： 第 十五 條之 一  /  第15條之1  /  第 十二 條
    m = re.search(r"第\s*(.+?)\s*條(?:\s*之\s*(.+?))?\s*$", s)
    if not m:
        # 退而求其次：移除所有空白
        return re.sub(r"\s+", "", s)
    main_raw = m.group(1).strip()
    zhi_raw = m.group(2)

    main = _cn_to_int(main_raw)
    main_str = str(main) if main is not None else re.sub(r"\s+", "", main_raw)

    result = f"第{main_str}條"
    if zhi_raw is not None:
        zhi = _cn_to_int(zhi_raw.strip())
        zhi_str = str(zhi) if zhi is not None else re.sub(r"\s+", "", zhi_raw)
        result += f"之{zhi_str}"
    return result


# ---------------------------------------------------------------------------
# 檔名清洗
# ---------------------------------------------------------------------------
def sanitize_filename(name: str) -> str:
    """移除 Windows 不合法字元並去除前後空白/句點。"""
    cleaned = _ILLEGAL_FILENAME_CHARS.sub("", name)
    return cleaned.strip().strip(".").strip()


# ---------------------------------------------------------------------------
# XML 解析
# ---------------------------------------------------------------------------
def _text(elem) -> str:
    """取得節點內所有文字（含子節點），合併後去前後空白。"""
    if elem is None:
        return ""
    return "".join(elem.itertext()).strip()


def parse_laws(xml_str: str, whitelist=None):
    """解析 RAW XML，回傳 [(法規名稱, [Article, ...]), ...]。

    whitelist: set[str] 或 None。None 表示不過濾（回傳全部）。
    對標籤巢狀深度不做硬假設：以 iter('法規') / iter('條文') 找後代。
    """
    root = ET.fromstring(xml_str)

    # 找出所有 <法規> 節點（含 root 本身就是 <法規> 的單檔情況）
    law_nodes = list(root.iter("法規"))
    if not law_nodes and root.tag == "法規":
        law_nodes = [root]

    results = []
    for law in law_nodes:
        name_node = law.find("法規名稱")
        if name_node is None or not _text(name_node):
            name_node = law.find("中文法規名稱")
        name = _text(name_node) if name_node is not None else ""
        if not name:
            continue
        if whitelist is not None and name not in whitelist:
            continue

        articles = []
        for art in law.iter("條文"):
            num_node = art.find("條號")
            content_node = art.find("條文內容")
            number = normalize_article_number(_text(num_node)) if num_node is not None else ""
            content = _text(content_node) if content_node is not None else ""
            if not number or not content:
                continue
            articles.append(Article(number=number, content=content))

        results.append((name, articles))
    return results


# ---------------------------------------------------------------------------
# 下載 / 讀取
# ---------------------------------------------------------------------------
def load_xml(xml_path: Optional[str] = None) -> str:
    """優先吃本地檔（--xml 或 CORPUS_XML_PATH），否則才連網下載 RAW XML。"""
    path = xml_path or os.getenv("CORPUS_XML_PATH")
    if path:
        return Path(path).read_text(encoding="utf-8")
    print(f"未提供本地 XML，連網下載：{RAW_XML_URL}")
    with urllib.request.urlopen(RAW_XML_URL, timeout=120) as resp:
        raw = resp.read()
    # RAW XML 可能為 big5/utf-8；先試 utf-8，失敗退 big5
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("big5", errors="replace")


def write_articles(laws, out_dir: Path = DATA_DIR) -> int:
    """逐條輸出 data/{法規名稱}-第N條.txt，回傳產生檔案數。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    count = 0
    for name, articles in laws:
        safe_name = sanitize_filename(name)
        for art in articles:
            fname = f"{safe_name}-{art.number}.txt"
            (out_dir / fname).write_text(art.content, encoding="utf-8")
            count += 1
    return count


def main():
    parser = argparse.ArgumentParser(description="全國法規資料庫 RAW XML → 一條一檔")
    parser.add_argument("--xml", help="本地 RAW XML 檔路徑（不提供則連網下載）")
    parser.add_argument(
        "--laws",
        help="白名單法規名稱（逗號分隔）；省略則用預設核心清單",
    )
    parser.add_argument("--out", default=str(DATA_DIR), help="輸出目錄（預設 data/）")
    args = parser.parse_args()

    if args.laws:
        whitelist = {s.strip() for s in args.laws.split(",") if s.strip()}
    else:
        whitelist = set(DEFAULT_WHITELIST)

    xml_str = load_xml(args.xml)
    laws = parse_laws(xml_str, whitelist=whitelist)

    found = {name for name, _ in laws}
    missing = whitelist - found
    if missing:
        print(f"⚠ 白名單中未在 XML 找到：{', '.join(sorted(missing))}")

    n = write_articles(laws, out_dir=Path(args.out))
    print(f"完成：共產生 {n} 個條文檔案，輸出至 {args.out}")
    print("提醒：個資非條文素材（QA-pair/函釋/裁罰）請人工另放，見本檔 docstring。")


if __name__ == "__main__":
    main()
