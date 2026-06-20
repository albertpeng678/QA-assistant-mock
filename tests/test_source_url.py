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

    def test_company_law_hanshi_gets_gcis_url(self):
        """經濟部公司法函釋無 deep link → fallback 建構 GCIS 逐條函釋頁 URL。"""
        from scripts.ingest import derive_attributes
        first_line = "來源:經濟部 | 效力:函釋 | 字號:經商字第095 | 發文日:2006-07-14 | 對應條號:第204條 | 母法:公司法"
        attrs = derive_attributes(
            "公司-函釋-某案.txt",
            first_line=first_line, body_text=first_line + "\n\n主旨：某某...",
        )
        assert attrs["url"] == (
            "https://gcis.nat.gov.tw/elaw/constructionDetailFromSingleLaw"
            "?lawCode=19&art=204&dash=0&ln=zh"
        )

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

    def test_mol_hanshi_gets_flawdoc_url(self):
        """勞動部函釋有字號 → 建構 FLAWDOC03 全文內容頁 URL（N2=純數字文號）。

        FINTQRY05 經 Playwright 驗證只到「相關法條」導覽頁、無全文；
        FLAWDOC03 冷開 GET 即回完整主旨/說明，且只需 N2（字號數字）。
        """
        from scripts.ingest import derive_attributes
        first_line = (
            "來源:勞動部 | 效力:函釋 | 字號:勞動條2字第1060131476號函 "
            "| 發文日:2017-08-03 | 對應條號:第30條 | 母法:勞動基準法"
        )
        attrs = derive_attributes(
            "勞動-函釋-某案.txt",
            first_line=first_line, body_text=first_line + "\n\n主旨：...",
        )
        assert "url" in attrs
        assert "FLAWDOC03" in attrs["url"]
        assert "N2=1060131476" in attrs["url"]
        # 單一文件查詢的固定分頁參數，缺了會系統錯誤
        assert "datatype=etype" in attrs["url"]
        assert "recordno=1" in attrs["url"]

    def test_sfb_insider_faq_gets_pdf_url(self):
        """證期局內線交易 FAQ（對應 §157-1）→ 內線交易問答集 PDF 直接載點。

        SFB Q&A 經 Playwright 親驗：無 HTML 個別頁，全封裝於分類頁的 PDF。
        對應條號 §157-1 → 內線交易問答集.pdf。
        """
        from scripts.ingest import derive_attributes
        first_line = (
            "來源:金融監督管理委員會證券期貨局 | 效力:FAQ "
            "| 對應條號:第157-1條 | 母法:證券交易法"
        )
        attrs = derive_attributes(
            "證券-FAQ-何謂內線交易及其立法目的.txt", first_line=first_line,
        )
        assert "url" in attrs
        assert attrs["url"].endswith("flag=doc")
        assert "202511180853170.pdf" in attrs["url"]

    def test_sfb_shareholding_faq_gets_pdf_url(self):
        """證期局股權申報 FAQ（對應 §22-2）→ 內部人股權申報問答集 PDF 直接載點。"""
        from scripts.ingest import derive_attributes
        first_line = (
            "來源:金融監督管理委員會證券期貨局 | 效力:FAQ "
            "| 對應條號:第22-2條 | 母法:證券交易法"
        )
        attrs = derive_attributes(
            "證券-FAQ-何謂事前申報.txt", first_line=first_line,
        )
        assert "url" in attrs
        assert "201908191647390.pdf" in attrs["url"]

    def test_gcis_zhi_subarticle_gets_dashed_url(self):
        """公司法「之」格式條號（第172之1條）→ GCIS dash 參數（art=172&dash=1）。

        Playwright 親驗：constructionDetailFromSingleLaw?lawCode=19&art=172&dash=1
        內容為第172條之1（股東提案）全文。原僅 split('-') 會漏「之」格式而降級到
        法規總覽頁；須將「之」正規化為 dash。
        """
        from scripts.ingest import derive_attributes
        first_line = "來源:經濟部 | 效力:函釋 | 字號:經商字第123 | 對應條號:第172之1條 | 母法:公司法"
        attrs = derive_attributes(
            "公司-函釋-股東提案之審查權.txt",
            first_line=first_line, body_text=first_line + "\n\n主旨：...",
        )
        assert attrs["url"] == (
            "https://gcis.nat.gov.tw/elaw/constructionDetailFromSingleLaw"
            "?lawCode=19&art=172&dash=1&ln=zh"
        )

    def test_gcis_repealed_subarticle_falls_back_to_overview(self):
        """已廢止條號（公司法第402之1條）→ 母法總覽頁，非死的逐條頁。

        Playwright 親驗 art=402&dash=1 為「（刪除）查無結果」空頁、0 函釋；
        指向它即「點開沒到內容頁」。改退母法總覽頁（parent law）。
        """
        from scripts.ingest import derive_attributes
        first_line = "來源:經濟部 | 效力:函釋 | 字號:經商字第456 | 對應條號:第402之1條 | 母法:公司法"
        attrs = derive_attributes(
            "公司-函釋-停業申請人停業期間限制疑義.txt",
            first_line=first_line, body_text=first_line + "\n\n主旨：...",
        )
        assert attrs["url"] == (
            "https://gcis.nat.gov.tw/elaw/getElawView?ln=zh&elawKey=19"
        )
        assert "constructionDetailFromSingleLaw" not in attrs["url"]

    def test_gcis_business_dept_source_still_matched(self):
        """商業發展署來源（收緊後不用裸『商業』）仍正確進 GCIS。"""
        from scripts.ingest import derive_attributes
        first_line = "來源:經濟部商業發展署 | 效力:函釋 | 對應條號:第10條 | 母法:公司法"
        attrs = derive_attributes(
            "公司-函釋-某商業署案.txt",
            first_line=first_line, body_text=first_line + "\n\n主旨：...",
        )
        assert "constructionDetailFromSingleLaw" in attrs["url"]
        assert "art=10&dash=0" in attrs["url"]

    def test_sfb_faq_unknown_article_no_url(self):
        """證期局 FAQ 但對應條號非 §157-1/§22-2 → 不亂給 PDF（無 url）。"""
        from scripts.ingest import derive_attributes
        first_line = "來源:金融監督管理委員會證券期貨局 | 效力:FAQ | 對應條號:第36條 | 母法:證券交易法"
        attrs = derive_attributes(
            "證券-FAQ-某未知條號案.txt", first_line=first_line,
        )
        assert "url" not in attrs

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
