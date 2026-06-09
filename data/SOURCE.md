# 語料來源與授權

本目錄之法規條文（`{法規名稱}-第N條.txt`，一條一檔）由 `scripts/fetch_corpus.py`
自下列來源下載並切分：

- **來源**：全國法規資料庫（https://law.moj.gov.tw/）開放資料
- **取得網址**：`https://sendlaw.moj.gov.tw/PublicData/GetFile.ashx?DType=RAW_XML&AuData=CF`
- **授權**：政府資料開放授權條款第 1 版（可重製、改作、商用，**須註明出處**）
- **出處標註**：資料來源「全國法規資料庫 http://law.moj.gov.tw/」

收錄範圍（企業法遵核心法規，母法逐條）：個人資料保護法、洗錢防制法、勞動基準法、
性別平等工作法、證券交易法、公司法、公平交易法、營業秘密法、消費者保護法。

> 法規會修正，條文以全國法規資料庫最新公告為準；本快照僅供 RAG 檢索與研究輔助，
> 非正式法律意見。重建語料請執行 `python scripts/fetch_corpus.py`。
