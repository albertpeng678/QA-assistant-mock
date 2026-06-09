"""離線單元測試：scripts/ingest.py 的 derive_attributes 純函式。

僅測純函式（從檔名解析 metadata），不呼叫 OpenAI。
"""
from scripts.ingest import derive_attributes


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
