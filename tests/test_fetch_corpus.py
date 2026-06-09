"""離線單元測試：條號正規化 + XML 解析切檔（缺口 5 配套）。

不連網。用 inline 假 XML（模仿全國法規資料庫 RAW_XML 結構）驗證：
- 條號正規化（中文數字 → 阿拉伯數字、之N）
- 解析法規名稱 + 逐條切出（檔名、內容）
- Windows 不合法字元清洗
"""
import pytest

from scripts.fetch_corpus import (
    normalize_article_number,
    sanitize_filename,
    parse_laws,
    Article,
    _decode_bytes,
)

# 模仿全國法規資料庫 RAW_XML：根 <法規資料庫> 內多個 <法規>，
# 每部法規 <法規名稱> + <法規內容> 內含若干 <條文>（含 <編章節> 干擾節點）。
SAMPLE_XML = """<?xml version="1.0" encoding="utf-8"?>
<法規資料庫>
  <法規>
    <法規名稱>個人資料保護法</法規名稱>
    <法規內容>
      <編章節>第 一 章 總則</編章節>
      <條文>
        <條號>第 一 條</條號>
        <條文內容>為規範個人資料之蒐集、處理及利用，特制定本法。</條文內容>
      </條文>
      <條文>
        <條號>第 十二 條</條號>
        <條文內容>公務機關或非公務機關違反本法規定，致個人資料被竊取、洩漏。</條文內容>
      </條文>
      <條文>
        <條號>第 十五 條之 一</條號>
        <條文內容>本條為之一測試條文。</條文內容>
      </條文>
    </法規內容>
  </法規>
  <法規>
    <法規名稱>洗錢防制法</法規名稱>
    <法規內容>
      <條文>
        <條號>第 二 條</條號>
        <條文內容>本法所稱洗錢，指下列行為。</條文內容>
      </條文>
    </法規內容>
  </法規>
  <法規>
    <法規名稱>不在白名單的法</法規名稱>
    <法規內容>
      <條文>
        <條號>第 一 條</條號>
        <條文內容>不該被切出。</條文內容>
      </條文>
    </法規內容>
  </法規>
</法規資料庫>
"""


class TestNormalizeArticleNumber:
    def test_arabic_passthrough(self):
        assert normalize_article_number("第 1 條") == "第1條"

    def test_simple_chinese(self):
        assert normalize_article_number("第 一 條") == "第1條"

    def test_two_digit_chinese(self):
        assert normalize_article_number("第 十二 條") == "第12條"

    def test_tens(self):
        assert normalize_article_number("第 二十 條") == "第20條"

    def test_hundreds(self):
        assert normalize_article_number("第 一百零五 條") == "第105條"
        assert normalize_article_number("第 一百一十 條") == "第110條"
        assert normalize_article_number("第 二百三十四 條") == "第234條"

    def test_zhi(self):
        assert normalize_article_number("第 十五 條之 一") == "第15條之1"

    def test_strip_whitespace(self):
        assert normalize_article_number("  第十二條  ") == "第12條"


class TestSanitizeFilename:
    def test_illegal_chars_replaced(self):
        # Windows 不合法： \ / : * ? " < > |
        out = sanitize_filename('個資/保護:法<v1>?')
        for ch in '\\/:*?"<>|':
            assert ch not in out


class TestDecodeBytes:
    """_decode_bytes：本地檔與網路路徑共用的解碼邏輯（utf-8 → big5 fallback）。

    全國法規資料庫 RAW XML 實務上常為 Big5，離線用 --xml 餵 Big5 檔不得炸 UnicodeDecodeError。
    """

    SAMPLE = "個人資料保護法 第12條 洩漏"

    def test_big5_bytes_decoded(self):
        raw = self.SAMPLE.encode("big5")
        # 確認測資真的是 big5（utf-8 解會失敗），否則測試沒鑑別力
        with pytest.raises(UnicodeDecodeError):
            raw.decode("utf-8")
        assert _decode_bytes(raw) == self.SAMPLE

    def test_utf8_bytes_decoded(self):
        raw = self.SAMPLE.encode("utf-8")
        assert _decode_bytes(raw) == self.SAMPLE


class TestParseLaws:
    def test_parse_whitelist_filters(self):
        laws = parse_laws(SAMPLE_XML, whitelist={"個人資料保護法", "洗錢防制法"})
        names = {law_name for law_name, _ in laws}
        assert names == {"個人資料保護法", "洗錢防制法"}

    def test_articles_extracted(self):
        laws = dict(parse_laws(SAMPLE_XML, whitelist={"個人資料保護法"}))
        articles = laws["個人資料保護法"]
        assert all(isinstance(a, Article) for a in articles)
        nums = [a.number for a in articles]
        assert nums == ["第1條", "第12條", "第15條之1"]

    def test_article_content(self):
        laws = dict(parse_laws(SAMPLE_XML, whitelist={"個人資料保護法"}))
        a12 = next(a for a in laws["個人資料保護法"] if a.number == "第12條")
        assert "洩漏" in a12.content

    def test_filename_built(self):
        laws = dict(parse_laws(SAMPLE_XML, whitelist={"個人資料保護法"}))
        a = laws["個人資料保護法"][0]
        fname = f"{sanitize_filename('個人資料保護法')}-{a.number}.txt"
        assert fname == "個人資料保護法-第1條.txt"

    def test_no_whitelist_returns_all(self):
        laws = parse_laws(SAMPLE_XML, whitelist=None)
        assert len(laws) == 3
