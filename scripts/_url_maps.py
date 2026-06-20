"""共用 URL 對照表：各機構函釋/FAQ 的字號/檔名 → 特定內容頁 URL。

由 restore_source_urls.py 與 upgrade_source_urls.py 共用，避免重複定義導致分歧。
資料來源：手動從各機構網站查得 + Playwright 親驗 + LawResult 批次爬取。
"""

# 名稱去底線前綴對外公開（共用模組慣例）。
# ── PDPC 籌備處函釋：字號→specific content page ──
PDPC_HANSHI_MAP: dict[str, str] = {
    "個資籌法字第1130000035號": "https://www.pdpc.gov.tw/News_Content/102/444",
    "個資籌法字第1130000307號": "https://www.pdpc.gov.tw/News_Content/102/584",
    "個資籌法字第1130000873號": "https://www.pdpc.gov.tw/News_Content/102/650",
    "個資籌法字第1130000975號": "https://www.pdpc.gov.tw/News_Content/102/604",
    "個資籌法字第1140000029號": "https://www.pdpc.gov.tw/News_Content/102/916",
    "個資籌法字第1140000771號": "https://www.pdpc.gov.tw/News_Content/102/958",
    "個資籌法字第1140000913號": "https://www.pdpc.gov.tw/News_Content/102/972",
    "個資籌法字第1140001332號": "https://www.pdpc.gov.tw/News_Content/102/996",
    "個資籌法字第1140002000號": "https://www.pdpc.gov.tw/News_Content/102/1089",
    "個資籌法字第1140002111號": "https://www.pdpc.gov.tw/News_Content/102/1098",
    "個資籌法字第1140002420號": "https://www.pdpc.gov.tw/News_Content/102/1090",
    "個資籌法字第1140002435號": "https://www.pdpc.gov.tw/News_Content/102/1095",
    "個資籌法字第1150000083號": "https://www.pdpc.gov.tw/News_Content/102/1115",
    "個資籌法字第1150000148號": "https://www.pdpc.gov.tw/News_Content/102/1127",
    "個資籌法字第1150000700號": "https://www.pdpc.gov.tw/News_Content/102/1145",
}

# ── FTC 檔名→specific content page ──
FTC_FILE_MAP: dict[str, str] = {
    "公平交易-函釋-關係企業間是否為聯合行為之主體.txt": "https://www.ftc.gov.tw/internet/main/doc/docDetail.aspx?uid=1461&docid=14616&mid=39",
    "公平交易-函釋-同業公會自律規範不涉及聯合行為之例.txt": "https://www.ftc.gov.tw/internet/main/doc/docDetail.aspx?uid=1461&docid=14617&mid=39",
    "公平交易-函釋-政府機關訂定價格或限制競爭行政行為是否適用公平交易法.txt": "https://www.ftc.gov.tw/internet/main/doc/docDetail.aspx?uid=1461&docid=14618&mid=39",
    "公平交易-函釋-贈品贈獎額度辦法同類商品及贈品價值認定標準.txt": "https://www.ftc.gov.tw/internet/main/doc/docDetail.aspx?uid=1461&docid=14619&mid=39",
    "公平交易-函釋-聯合行為微小不罰之認定標準.txt": "https://www.ftc.gov.tw/internet/main/doc/docDetail.aspx?uid=1461&docid=14521&mid=39",
    "公平交易-FAQ-公平交易法所規範之行為或內容為何.txt": "https://www.ftc.gov.tw/internet/main/doc/docDetail.aspx?uid=1202&docid=14303&mid=1201",
    "公平交易-FAQ-哪些行為不適用公平交易法及與其他法律競合.txt": "https://www.ftc.gov.tw/internet/main/doc/docDetail.aspx?uid=1202&docid=14305&mid=1201",
    "公平交易-FAQ-公平交易法所規範之主體為何.txt": "https://www.ftc.gov.tw/internet/main/doc/docDetail.aspx?uid=1203&docid=14306&mid=1201",
    "公平交易-FAQ-行政機關是否屬於公平交易法所稱之事業.txt": "https://www.ftc.gov.tw/internet/main/doc/docDetail.aspx?uid=1203&docid=14307&mid=1201",
    "公平交易-FAQ-何謂聯合行為.txt": "https://www.ftc.gov.tw/internet/main/doc/docDetail.aspx?uid=1207&docid=13148&mid=1201",
    "公平交易-FAQ-違反聯合行為之處罰規定.txt": "https://www.ftc.gov.tw/internet/main/doc/docDetail.aspx?uid=1207&docid=13149&mid=1201",
    "公平交易-FAQ-公會訂定參考價格是否合法.txt": "https://www.ftc.gov.tw/internet/main/doc/docDetail.aspx?uid=1207&docid=13157&mid=1201",
    "公平交易-FAQ-何謂杯葛及其規範方式.txt": "https://www.ftc.gov.tw/internet/main/doc/docDetail.aspx?uid=1209&docid=13206&mid=1201",
    "公平交易-FAQ-何謂差別待遇.txt": "https://www.ftc.gov.tw/internet/main/doc/docDetail.aspx?uid=1209&docid=13208&mid=1201",
    "公平交易-FAQ-違反第20條限制競爭行為之處罰規定.txt": "https://www.ftc.gov.tw/internet/main/doc/docDetail.aspx?uid=1209&docid=13205&mid=1201",
    "公平交易-FAQ-不實廣告查處與其他機關權責劃分.txt": "https://www.ftc.gov.tw/internet/main/doc/docDetail.aspx?uid=1211&docid=14022&mid=1201",
    "公平交易-FAQ-第21條表示或表徵之意義.txt": "https://www.ftc.gov.tw/internet/main/doc/docDetail.aspx?uid=1211&docid=14025&mid=1201",
    "公平交易-FAQ-第21條虛偽不實之意義.txt": "https://www.ftc.gov.tw/internet/main/doc/docDetail.aspx?uid=1211&docid=14026&mid=1201",
    "公平交易-FAQ-第21條引人錯誤之意義.txt": "https://www.ftc.gov.tw/internet/main/doc/docDetail.aspx?uid=1211&docid=14027&mid=1201",
    "公平交易-FAQ-廣告代理業與媒體業之責任.txt": "https://www.ftc.gov.tw/internet/main/doc/docDetail.aspx?uid=1211&docid=14030&mid=1201",
    "公平交易-FAQ-何謂多層次傳銷行為.txt": "https://www.ftc.gov.tw/internet/main/doc/docDetail.aspx?uid=1213&docid=13290&mid=1213",
    "公平交易-FAQ-完成傳銷報備是否即屬合法.txt": "https://www.ftc.gov.tw/internet/main/doc/docDetail.aspx?uid=1213&docid=13283&mid=1213",
    "公平交易-FAQ-多層次傳銷招攬他人應告知事項.txt": "https://www.ftc.gov.tw/internet/main/doc/docDetail.aspx?uid=1213&docid=13281&mid=1213",
    "公平交易-FAQ-多層次傳銷違法行為類型有哪些.txt": "https://www.ftc.gov.tw/internet/main/doc/docDetail.aspx?uid=1213&docid=13272&mid=1213",
    "公平交易-FAQ-傳銷商可否退出組織及退貨還款.txt": "https://www.ftc.gov.tw/internet/main/doc/docDetail.aspx?uid=1213&docid=13278&mid=1213",
}

# ── FSC 字號→LawContent ID ──
# 由 LawResult 搜尋頁批次爬得（每個字號縮到 1 筆 → 取 LawContent.aspx?id=）。
# LawContent 頁經 Playwright 驗證為含完整法規/函釋全文的內容頁。
FSC_LAWCONTENT_MAP: dict[str, str] = {
    "0930022393": "FE059353",
    "0931000793": "FE059111",
    "0948010405": "GL002015",
    "0948010571": "FE059187",
    "09500479670": "FE059122",
    "09510004500": "FE059124",
    "09530002860": "FE059189",
    "09600131550": "FE059127",
    "09600167590": "FE059128",
    "09600169280": "FE057287",
    "09600317810": "FE059129",
    "09600430420": "FE052846",
    "09610000410": "FE058168",
    "09610001560": "FE059135",
    "09660001390": "FE053479",
    "09660004600": "FE059238",
    "09700182160": "FE053698",
    "09700210640": "FE059347",
    "09700291882": "FE059254",
    "09700343312": "FE059226",
    "09700350431": "FE059227",
    "09700526080": "GL000008",
    "09800415730": "FE220563",
    "09900123820": "FE220890",
    "09900214800": "GL000570",
    "10000158640": "GL000195",
    "10000181061": "GL000565",
    "10000215511": "GL000209",
    "10100200020": "GL000562",
    "10100207040": "GL000566",
    "10100220400": "GL000568",
    "10100238060": "GL000602",
    "10130002690": "GL000624",
    "10130003651": "GL000700",
    "10200216750": "GL000857",
    "10200321260": "GL000947",
    "10230001141": "GL000796",
    "10230002420": "GL000856",
    "10300212700": "GL001286",
    "10310000140": "GL001053",
    "10310000142": "GL001054",
    "10310006310": "GL001452",
    "10310006312": "GL001451",
    "10310007590": "GL001459",
    "10400259730": "GL001825",
    "10400914160": "GL001530",
    "10440002670": "GL001587",
    "10440002730": "GL001591",
    "10500902700": "GL001822",
    "10600009800": "GL002133",
    "10600244510": "GL002271",
    "10650000070": "GL002054",
    "10650001375": "GL002131",
    "10701198720": "GL002663",
    "10701198721": "GL002664",
    "10701218300": "GL002651",
    "10702712390": "GL002416",
    "10801036730": "GL002695",
    "10802714560": "GL002701",
    "1090145230": "GL003076",
    "1100135267": "GL003231",
    "11002724731": "GL003239",
    "11002726911": "GL003304",
    "11102253601": "GL003568",
    "11102279031": "GL003535",
    "1110272235": "GL003468",
    "11102741631": "GL003591",
    "1120139114": "GL003750",
    "11202181261": "GL003672",
    "11402740846": "GL004205",
}

# ── 臺北市勞動局(bola)/勞動檢查處(lio)：檔名→個別內容頁（Playwright 逐頁列舉+驗證標題） ──
BOLA_LIO_FILE_MAP: dict[str, str] = {
    # 臺北市政府勞動局 bola.gov.taipei
    "勞動-FAQ-休息日加班費如何計算.txt":
        "https://bola.gov.taipei/News_Content.aspx?n=FDEDF5DCB0A26A46&sms=87415A8B9CE81B16&s=473FFB4AD8DF5CC2",
    "勞動-FAQ-資遣費計算標準為何.txt":
        "https://bola.gov.taipei/News_Content.aspx?n=FDEDF5DCB0A26A46&sms=87415A8B9CE81B16&s=558B0B1319334763",
    "勞動-FAQ-預告工資以何薪資為計算基準.txt":
        "https://bola.gov.taipei/News_Content.aspx?n=FDEDF5DCB0A26A46&sms=87415A8B9CE81B16&s=D25A3877CA42E31B",
    # 臺北市勞動檢查處 lio.gov.taipei
    "勞動-FAQ-以不能勝任工作資遣任職滿3年資遣費如何計算.txt":
        "https://lio.gov.taipei/News_Content.aspx?n=DB345115745B8F8F&sms=87415A8B9CE81B16&s=00F4D825C4551C99",
    "勞動-FAQ-國定假日出勤2小時加班費如何計算.txt":
        "https://lio.gov.taipei/News_Content.aspx?n=DB345115745B8F8F&sms=87415A8B9CE81B16&s=D84228CD065890FC",
    "勞動-FAQ-時薪人員特休假如何計算.txt":
        "https://lio.gov.taipei/News_Content.aspx?n=DB345115745B8F8F&sms=87415A8B9CE81B16&s=2A88CCB74E48F560",
    "勞動-FAQ-雇主資遣勞工是否需要提前告知.txt":
        "https://lio.gov.taipei/News_Content.aspx?n=DB345115745B8F8F&sms=87415A8B9CE81B16&s=6CABD7F43EBA6E63",
}
