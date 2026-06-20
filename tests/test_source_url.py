"""TDD 測試：FAQ/函釋來源 URL 提取邏輯。

只有真正能到達原始內容的 deep link 才設為 url attribute。
機構首頁（pdpc.gov.tw/、ftc.gov.tw/ 等）不算——使用者點「官方原文」
期望看到原始內容，不是首頁。
"""
from scripts.ingest import extract_url_from_body


# ── extract_url_from_body：從內文末段提取 deep link URL ─────────

class TestExtractUrlFromBody:
    def test_cpc_faq_extracts_full_page_url(self):
        """消費者保護 FAQ 末行含完整 cpc.ey.gov.tw per-page URL。"""
        body = (
            "Q：何謂定型化契約？\n"
            "A：依消費者保護法第2條...\n\n"
            "資料來源：行政院消費者保護處"
            "（https://cpc.ey.gov.tw/Page/4432D6D5FA6677B9/"
            "2b4c84a5-0313-461f-a004-c3f56c0cbb98）"
        )
        url = extract_url_from_body(body)
        assert url == (
            "https://cpc.ey.gov.tw/Page/4432D6D5FA6677B9/"
            "2b4c84a5-0313-461f-a004-c3f56c0cbb98"
        )

    def test_pdpc_homepage_rejected(self):
        """PDPC 首頁 URL 不是 deep link，應拒絕。"""
        body = (
            "一、某某規定...\n\n"
            "（資料來源：國家發展委員會個人資料保護專區，"
            "現由個人資料保護委員會籌備處 https://www.pdpc.gov.tw/ "
            "收錄；採CC BY 4.0 姓名標示授權，須註明出處）"
        )
        assert extract_url_from_body(body) == ""

    def test_ftc_homepage_rejected(self):
        """FTC 首頁 URL 不是 deep link，應拒絕。"""
        body = (
            "主旨：某某案...\n\n"
            "（資料來源：公平交易委員會 https://www.ftc.gov.tw/ "
            "行政解釋-解釋令；採政府資料開放授權條款第1版，須註明出處）"
        )
        assert extract_url_from_body(body) == ""

    def test_onestop_homepage_rejected(self):
        """onestop.nat.gov.tw 無 path，只是入口頁，應拒絕。"""
        body = (
            "Q：可以用網路申請商業登記嗎？\n"
            "A：可至全國商工行政服務入口網"
            "（https://onestop.nat.gov.tw）申請之..."
        )
        assert extract_url_from_body(body) == ""

    def test_deep_link_with_path_accepted(self):
        """有具體 path 的 URL 應被提取。"""
        body = "資料來源（https://www.ftc.gov.tw/internet/main/doc/docDetail.aspx?uid=126）"
        url = extract_url_from_body(body)
        assert url == "https://www.ftc.gov.tw/internet/main/doc/docDetail.aspx?uid=126"

    def test_body_without_url_returns_empty(self):
        """無 URL 的內文回空字串。"""
        body = "來源:經濟部 | 效力:函釋\n\n主旨：某某案之解釋..."
        assert extract_url_from_body(body) == ""

    def test_empty_body_returns_empty(self):
        assert extract_url_from_body("") == ""

    def test_none_body_returns_empty(self):
        assert extract_url_from_body(None) == ""

    def test_ignores_law_moj_url(self):
        """內文中出現 law.moj.gov.tw 不應被提取（那是法條頁，非來源頁）。"""
        body = (
            "依個人資料保護法（參見 https://law.moj.gov.tw/LawClass/"
            "LawAll.aspx?pcode=I0050021）第12條..."
        )
        assert extract_url_from_body(body) == ""

    def test_gcis_elaw_list_page_rejected(self):
        """gcis.nat.gov.tw/elaw/ 是法規查詢入口列表頁，非特定內容頁，應拒絕。"""
        body = (
            "主旨：某某函釋...\n\n"
            "（資料來源：經濟部商業司 https://gcis.nat.gov.tw/elaw/ "
            "經濟部商工法規檢索系統）"
        )
        assert extract_url_from_body(body) == ""

    def test_strips_trailing_punctuation(self):
        """URL 後緊接中文括號等標點應剝除。"""
        body = "資料來源（https://cpc.ey.gov.tw/Page/ABC/def-123）"
        url = extract_url_from_body(body)
        assert not url.endswith("）")
        assert url == "https://cpc.ey.gov.tw/Page/ABC/def-123"


# ── 整合測試：derive_attributes + body_text ──────────────────

class TestDeriveAttributesWithBodyUrl:
    def test_cpc_faq_body_url_populates_attrs(self):
        """消費者保護 FAQ body 有 per-page URL → attrs 含 url。"""
        from scripts.ingest import derive_attributes
        first_line = "來源:行政院消費者保護處 | 效力:FAQ | 對應條號:第22條 | 母法:消費者保護法"
        body = (
            first_line + "\n\n"
            "Q：企業經營者就其廣告...\n\n"
            "資料來源：行政院消費者保護處"
            "（https://cpc.ey.gov.tw/Page/AAAA/bbbb）"
        )
        attrs = derive_attributes(
            "消費者保護-FAQ-企業就廣告應負之義務.txt",
            first_line=first_line, body_text=body,
        )
        assert attrs["url"] == "https://cpc.ey.gov.tw/Page/AAAA/bbbb"

    def test_company_law_hanshi_no_url_without_deep_link(self):
        """經濟部公司法函釋無 deep link body URL → 不應有 url attr。"""
        from scripts.ingest import derive_attributes
        first_line = "來源:經濟部 | 效力:函釋 | 字號:經商字第095 | 發文日:2006-07-14 | 對應條號:第204條 | 母法:公司法"
        attrs = derive_attributes(
            "公司-函釋-某案.txt",
            first_line=first_line, body_text=first_line + "\n\n主旨：某某...",
        )
        assert "url" not in attrs

    def test_pdpc_hanshi_homepage_not_in_attrs(self):
        """PDPC 函釋 body 只有首頁 URL → 不應有 url attr。"""
        from scripts.ingest import derive_attributes
        first_line = "來源:國家發展委員會 | 效力:函釋 | 字號:發法字第1070021284號 | 發文日:2018-11-02 | 對應條號:第51條 | 母法:個人資料保護法"
        body = (
            first_line + "\n\n按個資法第51條...\n\n"
            "（資料來源：國家發展委員會個人資料保護專區，"
            "現由個人資料保護委員會籌備處 https://www.pdpc.gov.tw/ "
            "收錄；採CC BY 4.0 姓名標示授權，須註明出處）"
        )
        attrs = derive_attributes(
            "個資-函釋-自然人單純個人或家庭活動.txt",
            first_line=first_line, body_text=body,
        )
        assert "url" not in attrs

    def test_metadata_url_takes_priority_over_body(self):
        """metadata url: 欄位優先於 body 內提取的 URL。"""
        from scripts.ingest import derive_attributes
        first_line = (
            "來源:公平交易委員會 | 效力:FAQ | 母法:公平交易法 | "
            "url:https://www.ftc.gov.tw/specific-page"
        )
        body = first_line + "\n\n資料來源（https://www.ftc.gov.tw/other-page）"
        attrs = derive_attributes(
            "公平交易-FAQ-某問題.txt",
            first_line=first_line, body_text=body,
        )
        assert attrs["url"] == "https://www.ftc.gov.tw/specific-page"

    def test_faq_without_deep_link_no_url(self):
        """無 deep link 的 FAQ → 不應有 url attr（不退回首頁）。"""
        from scripts.ingest import derive_attributes
        first_line = "來源:勞動部 | 效力:FAQ | 發布日:2023-01-01 | 對應條號:第24條 | 母法:勞動基準法"
        attrs = derive_attributes(
            "勞動-FAQ-加班費如何計算.txt", first_line=first_line,
        )
        assert "url" not in attrs

    def test_law_article_url_unchanged_with_body_text(self):
        """法條帶 body_text 參數時不影響原有行為。"""
        from scripts.ingest import derive_attributes
        law_index = {"個人資料保護法": "I0050021"}
        attrs = derive_attributes(
            "個人資料保護法-第12條.txt",
            law_index=law_index,
            body_text="第12條 公務機關或非公務機關...",
        )
        assert attrs["url"] == (
            "https://law.moj.gov.tw/LawClass/LawSingle.aspx"
            "?pcode=I0050021&flno=12"
        )
