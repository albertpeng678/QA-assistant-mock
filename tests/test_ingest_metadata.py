"""離線單元測試：scripts/ingest.py 的 derive_attributes 純函式。

僅測純函式（從檔名解析 metadata），不呼叫 OpenAI。
"""
from scripts.ingest import derive_attributes, article_to_flno, is_empty_article


def test_is_empty_article_detects_deleted_or_reserved():
    # 稽核 P0：純「（刪除）/（保留）」空殼條文 ingest 時應略過（精確比對，非含字樣）。
    assert is_empty_article("（刪除）")
    assert is_empty_article("  （刪除）\n")
    assert is_empty_article("(保留)")
    assert is_empty_article("")
    assert not is_empty_article("前條股款繳足後，董事會應於三十日內為之。")
    # 含「（刪除）」字樣但實為完整有效條文 → 不可誤殺
    assert not is_empty_article("第一百五十五條 對於在證券交易所… （刪除前段） 仍有效。")


def test_derive_attributes_business_registration_and_mlm_category():
    # 稽核 P1：補母法分類，消除 category=其他。
    a1 = derive_attributes("公司-FAQ-如何申請商業登記.txt",
                           first_line="來源:經濟部 | 效力:FAQ | 對應條號:第3條 | 母法:商業登記法")
    assert a1["category"] == "公司治理"
    a2 = derive_attributes("多層次傳銷-FAQ-保護機構之任務.txt",
                           first_line="來源:公平交易委員會 | 效力:FAQ | 母法:多層次傳銷管理法")
    assert a2["category"] == "公平交易"


def test_derive_attributes_basic():
    assert derive_attributes("個人資料保護法-第12條.txt") == {
        "law": "個人資料保護法",
        "article": "第12條",
        "category": "個資",
        "doc_type": "法條",
        "source": "全國法規資料庫",
    }


def test_derive_attributes_category_aml():
    assert derive_attributes("洗錢防制法-第7條.txt")["category"] == "洗錢防制"


def test_derive_attributes_enforcement_rules_doc_type():
    # 缺口⑦補語料：施行細則本身為含條文的法規，沿用同一切檔/解析管線，
    # 但 doc_type 應標「施行細則」、category 沿用母法分類。
    attrs = derive_attributes("個人資料保護法施行細則-第12條.txt")
    assert attrs["doc_type"] == "施行細則"
    assert attrs["category"] == "個資"
    assert attrs["law"] == "個人資料保護法施行細則"
    assert attrs["article"] == "第12條"


def test_derive_attributes_hanshi_from_first_line():
    # 缺口⑦補語料：函釋檔 {簡稱}-函釋-{標題}.txt，doc_type=函釋，
    # 並從首行 metadata 取母法/字號/發文日/對應條號，category 由母法對應。
    first = ("來源:金管會 | 效力:函釋 | 字號:金管銀法字第10300212700號 | "
             "發文日:2014-03-05 | 對應條號:第41條 | 母法:證券交易法")
    attrs = derive_attributes("證券-函釋-某案.txt", first_line=first)
    assert attrs["doc_type"] == "函釋"
    assert attrs["category"] == "公司治理"
    assert attrs["law"] == "證券交易法"
    assert attrs["article"] == "第41條"
    assert attrs["ref_no"] == "金管銀法字第10300212700號"
    assert attrs["effective_date"] == "2014-03-05"
    assert attrs["authority_level"] == "函釋"
    assert len(attrs) <= 16
    assert all(isinstance(v, str) for v in attrs.values())


def test_derive_attributes_faq_doc_type_and_category():
    first = "來源:勞動部 | 效力:FAQ | 發布日:2023-01-01 | 對應條號:第24條 | 母法:勞動基準法"
    attrs = derive_attributes("勞動-FAQ-加班費如何計算.txt", first_line=first)
    assert attrs["doc_type"] == "FAQ"
    assert attrs["category"] == "勞動"
    assert attrs["law"] == "勞動基準法"
    assert attrs["article"] == "第24條"


def test_derive_attributes_hanshi_without_first_line_still_tagged():
    # 無首行時至少 doc_type 要對（category 退「其他」），不可誤標成「法條」。
    attrs = derive_attributes("個資-函釋-某案.txt")
    assert attrs["doc_type"] == "函釋"


def test_derive_attributes_compound_article():
    attrs = derive_attributes("公司法-第1-1條.txt")
    assert attrs["article"] == "第1-1條"
    assert attrs["category"] == "公司治理"


def test_derive_attributes_unknown_law_category_other():
    assert derive_attributes("不存在的法規-第1條.txt")["category"] == "其他"


def test_derive_attributes_values_all_str():
    attrs = derive_attributes("個人資料保護法-第12條.txt")
    assert all(isinstance(v, str) for v in attrs.values())
    assert len(attrs) <= 16


# --- 條號 → flno ---------------------------------------------------------
def test_article_to_flno_basic():
    assert article_to_flno("第12條") == "12"


def test_article_to_flno_single_digit():
    assert article_to_flno("第7條") == "7"


def test_article_to_flno_compound():
    assert article_to_flno("第1-1條") == "1-1"


def test_article_to_flno_empty():
    assert article_to_flno("") == ""


# --- url attribute（需 law_index）---------------------------------------
def test_derive_attributes_with_law_index_adds_url():
    law_index = {"個人資料保護法": "I0050021"}
    attrs = derive_attributes("個人資料保護法-第12條.txt", law_index=law_index)
    assert attrs["url"] == (
        "https://law.moj.gov.tw/LawClass/LawSingle.aspx?pcode=I0050021&flno=12"
    )
    assert len(attrs["url"]) <= 512
    assert len(attrs) <= 16


def test_derive_attributes_url_compound_article():
    law_index = {"公司法": "J0080001"}
    attrs = derive_attributes("公司法-第1-1條.txt", law_index=law_index)
    assert attrs["url"].endswith("flno=1-1")


def test_derive_attributes_no_url_without_index():
    """向後相容：law_index=None 時無 url，仍是既有 5 key。"""
    attrs = derive_attributes("個人資料保護法-第12條.txt")
    assert "url" not in attrs
    assert len(attrs) == 5


def test_derive_attributes_no_url_when_pcode_missing():
    """law_index 提供但該 law 無 pcode（空字串）→ 不加 url。"""
    law_index = {"個人資料保護法": ""}
    attrs = derive_attributes("個人資料保護法-第12條.txt", law_index=law_index)
    assert "url" not in attrs


# --- FAQ/函釋 不應用法條 URL（bug fix）--------------------------------------

def test_faq_should_not_get_law_article_url():
    """FAQ 的「對應條號」只是參考標籤，不能用來組法條 URL；無 deep link 則無 url。"""
    law_index = {"勞動基準法": "N0030001"}
    first = "來源:勞動部 | 效力:FAQ | 發布日:2023-01-01 | 對應條號:第24條 | 母法:勞動基準法"
    attrs = derive_attributes("勞動-FAQ-加班費如何計算.txt",
                              law_index=law_index, first_line=first)
    assert attrs["doc_type"] == "FAQ"
    assert "url" not in attrs


def test_hanshi_should_not_get_law_article_url():
    """函釋的「對應條號」只是參考標籤，不能用來組法條 URL；無 deep link 則無 url。"""
    law_index = {"證券交易法": "G0400001"}
    first = ("來源:金管會 | 效力:函釋 | 字號:金管銀法字第10300212700號 | "
             "發文日:2014-03-05 | 對應條號:第41條 | 母法:證券交易法")
    attrs = derive_attributes("證券-函釋-某案.txt",
                              law_index=law_index, first_line=first)
    assert attrs["doc_type"] == "函釋"
    assert "url" not in attrs


def test_faq_with_explicit_url_in_metadata():
    """若 FAQ 首行 metadata 含 url 欄位，直接採用。"""
    first = ("來源:公平交易委員會 | 效力:FAQ | 對應條號:第15條 | 母法:公平交易法 | "
             "url:https://www.ftc.gov.tw/internet/main/doc/docDetail.aspx?uid=126")
    attrs = derive_attributes("公平交易-FAQ-公會訂定參考價格.txt", first_line=first)
    assert attrs["url"] == "https://www.ftc.gov.tw/internet/main/doc/docDetail.aspx?uid=126"


def test_hanshi_with_explicit_url_in_metadata():
    """若函釋首行 metadata 含 url 欄位，直接採用。"""
    law_index = {"個人資料保護法": "I0050021"}
    first = ("來源:國發會 | 效力:函釋 | 字號:發法字第1131200456號 | "
             "發文日:2024-06-01 | 對應條號:第12條 | 母法:個人資料保護法 | "
             "url:https://www.ndc.gov.tw/Content_List.aspx?n=B3D59B291A3E081A")
    attrs = derive_attributes("個資-函釋-外洩通報.txt",
                              law_index=law_index, first_line=first)
    assert attrs["url"] == "https://www.ndc.gov.tw/Content_List.aspx?n=B3D59B291A3E081A"
    assert "LawSingle" not in attrs["url"]


def test_law_article_url_unchanged():
    """法條的 URL 行為不受影響。"""
    law_index = {"個人資料保護法": "I0050021"}
    attrs = derive_attributes("個人資料保護法-第12條.txt", law_index=law_index)
    assert attrs["url"] == (
        "https://law.moj.gov.tw/LawClass/LawSingle.aspx?pcode=I0050021&flno=12"
    )


def test_enforcement_rules_url_unchanged():
    """施行細則的 URL 行為不受影響。"""
    law_index = {"個人資料保護法施行細則": "I0050022"}
    attrs = derive_attributes("個人資料保護法施行細則-第12條.txt", law_index=law_index)
    assert attrs["url"] == (
        "https://law.moj.gov.tw/LawClass/LawSingle.aspx?pcode=I0050022&flno=12"
    )


def test_judgment_no_url():
    """判決不應有 URL（使用 /api/source_vs 取全文）。"""
    law_index = {"公司法": "J0080001"}
    first = "來源:司法院 | 效力:判決 | 裁判日:2025-01-15 | 對應條號:第23條 | 母法:公司法"
    attrs = derive_attributes("高法-判決-某案.txt",
                              law_index=law_index, first_line=first)
    assert attrs["doc_type"] == "判決"
    assert "url" not in attrs


def test_caifa_no_url():
    """裁罰不應有法條頁 URL。"""
    law_index = {"個人資料保護法": "I0050021"}
    first = "來源:國發會 | 效力:裁罰 | 對應條號:第48條 | 母法:個人資料保護法"
    attrs = derive_attributes("個資-裁罰-某案.txt",
                              law_index=law_index, first_line=first)
    assert attrs["doc_type"] == "裁罰"
    assert "url" not in attrs
