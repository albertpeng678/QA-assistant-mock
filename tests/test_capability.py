import json
from types import SimpleNamespace
from scripts import build_capability_manifest as bcm


class _FakePage:
    def __init__(self, data, has_more=False):
        self.data = data
        self.has_more = has_more


class _FakeFiles:
    def __init__(self, files):
        self._files = files

    def list(self, vector_store_id, limit=100, after=None):
        return _FakePage(self._files, has_more=False)


class _FakeVS:
    def __init__(self, files):
        self.files = _FakeFiles(files)


class _FakeClient:
    def __init__(self, files):
        self.vector_stores = _FakeVS(files)


def _f(doc_type, category="個資", eff="2026-03-01"):
    return SimpleNamespace(id="f", attributes={"doc_type": doc_type, "category": category, "effective_date": eff})


def test_build_manifest_ignores_non_date_effective_date():
    files = [
        _f("法條", eff="2026-05-10"),
        _f("函釋", eff="未標明"),
        _f("判決", eff="2024-03-01"),
    ]
    m = bcm.build_manifest(_FakeClient(files), "vs_x")
    assert m["cutoff"] == "2026-05"


def test_build_manifest_counts_by_doc_type_and_category():
    files = [_f("法條"), _f("法條"), _f("函釋"), _f("判決", "勞動", "2026-05-10"), _f("判決", "個資", "2024-03-01")]
    m = bcm.build_manifest(_FakeClient(files), "vs_x")
    assert m["doc_type_counts"]["法條"] == 2
    assert m["doc_type_counts"]["函釋"] == 1
    assert m["doc_type_counts"]["判決"] == 2
    assert set(m["categories"]) == {"個資", "勞動"}
    assert m["cutoff"] == "2026-05"
    assert m["total"] == 5


from app.capability import capability_answer, load_manifest

_MANIFEST = {
    "doc_type_counts": {"法條": 1163, "施行細則": 197, "函釋": 666, "FAQ": 169, "判決": 106},
    "categories": ["個資", "勞動", "公司治理", "公平交易", "營業秘密", "消費者保護", "洗錢防制"],
    "cutoff": "2026-06", "total": 2301,
}


def test_capability_answer_is_data_driven_and_structured():
    ans = capability_answer(_MANIFEST)
    assert "我能" in ans
    assert "1,163" in ans
    assert "判決" in ans
    assert "個資" in ans and "洗錢防制" in ans
    assert "不能" in ans
    assert "非正式法律意見" in ans


def test_capability_answer_no_hallucinated_numbers():
    m = dict(_MANIFEST, doc_type_counts={"法條": 5})
    ans = capability_answer(m)
    assert "5" in ans
    assert "1,163" not in ans
