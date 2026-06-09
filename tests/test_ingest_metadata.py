"""離線單元測試：scripts/ingest.py 的 derive_attributes 純函式。

僅測純函式（從檔名解析 metadata），不呼叫 OpenAI。
"""
from scripts.ingest import derive_attributes, article_to_flno


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
