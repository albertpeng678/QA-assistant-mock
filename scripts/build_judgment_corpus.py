"""近 N 月裁判書語料 orchestrator：下載→解壓→rg 預篩→strict 精篩寫檔→逐月清理。

依賴：
  - 登入 cookie 走環境變數 JCOOKIE / JUA（opendata.judicial.gov.tw 會員登入後取得）。
  - WinRAR UnRAR.exe 解 RAR5；ripgrep(rg) 在 PATH。
  - 下載 id 規律：202603=fid 65335，每往前一月 fid-1（跨年連續，已實證）。

用法：
  # 先小批驗證（處理 202602 一月、只寫檔不上傳）
  python scripts/build_judgment_corpus.py --months 1 --skip-latest
  # 近 12 月、寫檔＋上傳 append 到 .env 的 VECTOR_STORE_ID
  python scripts/build_judgment_corpus.py --months 12 --skip-latest --upload
"""
import argparse
import os
import shutil
import subprocess
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ingest_judgments as ij  # noqa: E402  複用 process_one / upload / _load_dotenv / OUTPUT_DIR

UNRAR = r"C:\Program Files\WinRAR\UnRAR.exe"
WORK = Path(r"C:\Users\albertpeng\judicial_work\corpus_tmp")
FID_BASE = 65335          # 202603 對應的下載 fid
YM_BASE = (2026, 3)       # 對應年月
# 排洗錢後的 9 個法遵領域法規名（rg alternation 預篩候選用）。
LAWS_RG = ("個人資料保護法|勞動基準法|性別平等工作法|性別工作平等法|證券交易法"
           "|公司法|公平交易法|營業秘密法|消費者保護法")
EXCLUDE = {"洗錢防制法", "資恐防制法"}


def _find_rg():
    """rg 不一定在 python 繼承的 PATH（Bash tool 自帶但 subprocess 看不到）；
    依序：PATH → 已知第三方安裝路徑 → None（None 時 rg_candidates 退回 python 掃描）。"""
    p = shutil.which("rg")
    if p:
        return p
    for cand in (
        r"C:\Users\albertpeng\AppData\Local\Packages\OpenAI.Codex_2p2nqsd0c76g0\LocalCache\Local\OpenAI\Codex\bin\rg.exe",
        r"C:\Users\albertpeng\AppData\Local\Programs\Microsoft VS Code\1b50d58d73\resources\app\node_modules\@vscode\ripgrep-universal\bin\win32-x64\rg.exe",
    ):
        if os.path.exists(cand):
            return cand
    return None


RG = _find_rg()


def gen_months(n: int, skip_latest: bool):
    """從 202603 往前生成 [(ym_str, fid)]；skip_latest 跳過 202603（PoC 已上傳）。"""
    out = []
    y, mo, fid = YM_BASE[0], YM_BASE[1], FID_BASE
    start = 1 if skip_latest else 0
    for i in range(n + start):
        if i >= start:
            out.append((f"{y}{mo:02d}", fid))
        fid -= 1
        mo -= 1
        if mo == 0:
            mo = 12
            y -= 1
    return out


def download(fid: int, dest: Path):
    import requests
    cookie = os.environ["JCOOKIE"]
    ua = os.environ["JUA"]
    url = f"https://opendata.judicial.gov.tw/api/FilesetLists/{fid}/file"
    h = {"User-Agent": ua, "Cookie": cookie, "Referer": "https://opendata.judicial.gov.tw/"}
    with requests.get(url, headers=h, stream=True, timeout=300) as r:
        r.raise_for_status()
        with open(dest, "wb") as f:
            for ch in r.iter_content(1024 * 256):
                f.write(ch)


def extract(rar: Path, outdir: Path):
    subprocess.run([UNRAR, "x", "-o+", "-idq", str(rar), str(outdir) + os.sep], check=True)


def rg_candidates(root: Path):
    """回傳含任一法規名的候選 json 路徑。優先用 rg（快）；無 rg 退回 python 掃描（慢）。"""
    if RG:
        r = subprocess.run([RG, "-l", "--no-messages", LAWS_RG, str(root)],
                           capture_output=True, text=True, encoding="utf-8")
        return [ln for ln in r.stdout.splitlines() if ln.strip()]
    keys = [k.encode("utf-8") for k in LAWS_RG.split("|")]
    out = []
    for p in root.rglob("*.json"):
        try:
            if any(k in p.read_bytes() for k in keys):
                out.append(str(p))
        except Exception:  # noqa: BLE001
            continue
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--months", type=int, default=12)
    ap.add_argument("--skip-latest", action="store_true", help="跳過 202603（PoC 已上傳）")
    ap.add_argument("--min-count", type=int, default=3, help="strict 母法出現次數門檻")
    ap.add_argument("--upload", action="store_true", help="寫檔後上傳 append 到 VECTOR_STORE_ID")
    ap.add_argument("--keep-rar", action="store_true", help="保留下載的 rar（預設處理完即刪省空間）")
    args = ap.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

    ij._load_dotenv()
    if "JCOOKIE" not in os.environ or "JUA" not in os.environ:
        sys.exit("需設定 JCOOKIE / JUA 環境變數（opendata 會員登入 cookie）")

    WORK.mkdir(parents=True, exist_ok=True)
    ij.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    months = gen_months(args.months, args.skip_latest)
    print(f"處理 {len(months)} 月: {months[0][0]} ~ {months[-1][0]}（strict>={args.min_count}, 排洗錢）", flush=True)

    specs = []
    cat_cnt = Counter()
    seen = set()
    for ym, fid in months:
        rar = WORK / f"{ym}.rar"
        ed = WORK / ym
        try:
            print(f"[{ym}] 下載 fid={fid} ...", flush=True)
            download(fid, rar)
            print(f"[{ym}] 解壓 ...", flush=True)
            ed.mkdir(exist_ok=True)
            extract(rar, ed)
            cands = rg_candidates(ed)
            w = 0
            for c in cands:
                r = ij.process_one(Path(c), EXCLUDE, strict=True, min_count=args.min_count)
                if r is None:
                    continue
                fn, txt, attrs, cat = r
                if fn in seen:
                    fn = fn[:-4] + f"_{ym}.txt"
                seen.add(fn)
                op = ij.OUTPUT_DIR / fn
                op.write_text(txt, encoding="utf-8")
                specs.append((op, attrs))
                cat_cnt[cat] += 1
                w += 1
            print(f"[{ym}] 候選 {len(cands)} → 精篩寫出 {w}（累積 {len(specs)}）", flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"[{ym}] 失敗跳過: {e}", flush=True)
        finally:
            if not args.keep_rar and rar.exists():
                rar.unlink()
            if ed.exists():
                shutil.rmtree(ed, ignore_errors=True)

    print("\n=== 領域分佈 ===", flush=True)
    for c, n in cat_cnt.most_common():
        print(f"  {c}: {n}", flush=True)
    print(f"總寫出 {len(specs)} 筆 → {ij.OUTPUT_DIR}", flush=True)

    if args.upload and specs:
        print("\n=== 上傳 append 到既有 store ===", flush=True)
        ij.upload(specs, os.environ.get("VECTOR_STORE_ID"))


if __name__ == "__main__":
    main()
