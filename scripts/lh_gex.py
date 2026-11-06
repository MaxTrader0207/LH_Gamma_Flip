"""
生豬期權 Gamma Flip 每日資料管線（大連商品交易所）
- 大商所自 2025-12 起改用「對外門戶 API 服務」，需免費註冊取得 apikey 與 secret：
  環境變數 DCE_API_KEY、DCE_SECRET（在 GitHub repo 的 Settings → Secrets and variables → Actions 設定）
  1) POST http://www.dce.com.cn/dceapi/cms/auth/accessToken   headers: apikey   body: {"secret": ...}  → data.token
  2) POST http://www.dce.com.cn/dceapi/forward/publicweb/dailystat/dayQuotes
     headers: apikey, Authorization: Bearer <token>   body: {"varietyId":"lh","tradeDate":"YYYYMMDD","tradeType":"1"(期貨)/"2"(期權),"lang":"zh"}
- 沒有憑證時改試舊版公開匯出頁（可能已停用）
- 每個期權月份以同月份生豬期貨為標的；以主力期貨（持倉量最大）為基準，履約價換成主力等價後合併計算 GEX 與 Flip
- 輸出 data/latest.json、data/history.json、data/daily/YYYYMMDD.json；第一次取得資料時另存 data/_sample_*.json 供核對欄位

用法：python scripts/lh_gex.py [--days 14]
"""
import argparse
import json
import math
import os
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone

import requests

CST = timezone(timedelta(hours=8))
PRODUCT = "LH"
MULT = 16           # 生豬期貨與期權交易單位：16 噸／手
RATE = 0.015
DATA = os.path.join(os.path.dirname(__file__), "..", "data")
UA = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Referer": "http://www.dce.com.cn/dce/channel/list/168.html",
    "Accept-Language": "zh-CN,zh;q=0.9",
}
HOST = os.environ.get("DCE_HOST", "www.dce.com.cn")
API_KEY = os.environ.get("DCE_API_KEY", "").strip()
SECRET = os.environ.get("DCE_SECRET", "").strip()
HOLIDAYS = set()
DEBUG = []
_TOKEN = {"v": None, "t": 0}
_SAMPLED = set()


# ---------------- download ----------------
def token(sess):
    if _TOKEN["v"] and time.time() - _TOKEN["t"] < 6 * 3600:
        return _TOKEN["v"]
    r = sess.post(f"http://{HOST}/dceapi/cms/auth/accessToken", json={"secret": SECRET},
                  headers={**UA, "apikey": API_KEY, "Content-Type": "application/json"}, timeout=30)
    DEBUG.append(f"auth -> {r.status_code} {r.text[:160]}")
    tk = (r.json().get("data") or {}).get("token")
    if not tk:
        raise RuntimeError("大商所 API 登入失敗：" + r.text[:200])
    _TOKEN.update(v=tk, t=time.time())
    return tk


def fetch_api(kind, d, sess):
    """kind: fut / opt；回傳 list[dict]（大商所 JSON 原始欄位）"""
    trade_type = "1" if kind == "fut" else "2"
    for attempt in range(3):
        try:
            tk = token(sess)
            r = sess.post(f"http://{HOST}/dceapi/forward/publicweb/dailystat/dayQuotes",
                          json={"varietyId": "lh", "tradeDate": d.strftime("%Y%m%d"), "tradeType": trade_type, "lang": "zh",
                                **({"statisticsType": 0} if kind == "opt" else {})},
                          headers={**UA, "apikey": API_KEY, "Authorization": f"Bearer {tk}", "Content-Type": "application/json",
                                   "tradeType": trade_type, "lang": "zh"},
                          timeout=30)
            DEBUG.append(f"api {kind} {d} -> {r.status_code} {len(r.content)}")
            if r.status_code == 401:
                _TOKEN["v"] = None
                DEBUG.append("401：憑證有效但可能尚未開通「日行情」資料權限，或 token 過期")
                continue
            js = r.json()
            if js.get("success") is False or (js.get("code") not in (None, 200, "200", 0, "0")):
                DEBUG.append(f"api {kind} {d} 回應：{str(js)[:200]}")
                if "频次" in str(js):
                    time.sleep(62)
                    continue
            data = js.get("data") or []
            if isinstance(data, dict):
                data = data.get("list") or data.get("rows") or data.get("data") or []
            if data and kind not in _SAMPLED:
                _SAMPLED.add(kind)
                os.makedirs(DATA, exist_ok=True)
                json.dump(data[:12], open(os.path.join(DATA, f"_sample_{kind}.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
            return data
        except Exception as e:  # noqa: BLE001
            DEBUG.append(f"api {kind} {d} error {e}")
            time.sleep(3 * (attempt + 1))
    return []


def fetch_old(kind, d, sess):
    """舊版公開匯出（無需憑證，可能已停用）；回傳 list[dict]"""
    form = {"dayQuotes.variety": "lh", "dayQuotes.trade_type": "0" if kind == "fut" else "1",
            "year": str(d.year), "month": str(d.month - 1), "day": str(d.day), "exportFlag": "txt"}
    for url in (f"http://{HOST}/publicweb/quotesdata/exportDayQuotesChData.html",
                "http://portal.dce.com.cn/publicweb/quotesdata/exportDayQuotesChData.html"):
        try:
            r = sess.post(url, data=form, headers=UA, timeout=30)
            DEBUG.append(f"old {kind} {url} -> {r.status_code} {len(r.content)}")
            if r.status_code != 200:
                continue
            txt = None
            for enc in ("utf-8-sig", "gbk", "gb18030"):
                try:
                    txt = r.content.decode(enc)
                    break
                except UnicodeDecodeError:
                    pass
            if not txt or "<html" in txt[:300].lower():
                continue
            lines = [l for l in txt.splitlines() if l.strip()]
            hi = next((i for i, l in enumerate(lines) if "结算" in l and ("合约" in l or "交割月份" in l)), None)
            if hi is None:
                continue
            head = re.split(r"\s+|\t", lines[hi].strip())
            out = []
            for l in lines[hi + 1:]:
                f = re.split(r"\s+|\t", l.strip())
                if len(f) < len(head) - 1:
                    continue
                out.append(dict(zip(head, f)))
            if out and kind not in _SAMPLED:
                _SAMPLED.add(kind)
                json.dump(out[:12], open(os.path.join(DATA, f"_sample_{kind}.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
            return out
        except Exception as e:  # noqa: BLE001
            DEBUG.append(f"old {kind} error {e}")
    return []


def fetch(kind, d, sess):
    return fetch_api(kind, d, sess) if API_KEY and SECRET else fetch_old(kind, d, sess)


def pick(rec, *keys):
    """依多個可能的欄位名稱取值（新 API 英文鍵／舊版中文表頭）"""
    for k in keys:
        if k in rec and rec[k] not in (None, "", "-", "--"):
            return rec[k]
    lk = {str(k).lower(): v for k, v in rec.items()}
    for k in keys:
        v = lk.get(str(k).lower())
        if v not in (None, "", "-", "--"):
            return v
    return None


def num(s):
    try:
        return float(str(s).replace(",", "").strip())
    except ValueError:
        return float("nan")


def table(txt):
    """回傳 (表頭欄位, 資料列)；表頭是含「代码／月份」與「今结算」的那一行"""
    lines = [l for l in txt.splitlines() if "|" in l]
    head, rows = None, []
    for l in lines:
        f = [x.strip() for x in l.split("|")]
        if head is None:
            if any("结算" in x for x in f) and any(("代码" in x or "月份" in x) for x in f):
                head = f
            continue
        rows.append(f)
    return head or [], rows


def col(head, *keys):
    for i, h in enumerate(head):
        if any(k in h for k in keys):
            return i
    return -1


def full_year(digit, ref):
    y = (ref.year // 10) * 10 + digit
    if y < ref.year - 2:
        y += 10
    return y


# ---------------- dates ----------------
def is_trading(d):
    return d.weekday() < 5 and d.isoformat() not in HOLIDAYS


def option_expiry(y, m):
    """大商所規則：標的期貨交割月份前一個月的第 12 個交易日"""
    py, pm = (y, m - 1) if m > 1 else (y - 1, 12)
    d, n = date(py, pm, 1), 0
    while True:
        if is_trading(d):
            n += 1
            if n == 12:
                return d
        d += timedelta(days=1)


# ---------------- parse ----------------
FUT_RE = re.compile(r"^(?:lh|LH)?\s*(\d{2})(\d{2})$")
OPT_RE = re.compile(r"^(?:lh|LH)(\d{2})(\d{2})-?([CP])-?(\d+)$", re.I)


def parse_futures(recs, d):
    out = {}
    for x in recs:
        cid = str(pick(x, "contractId", "合约名称", "合约", "交割月份", "deliveryMonth") or "").strip()
        var = str(pick(x, "variety", "varietyId", "商品名称") or "")
        if var and not re.search(r"生猪|lh", var, re.I) and not cid.lower().startswith("lh"):
            continue
        m = FUT_RE.match(cid.replace("lh", "").replace("LH", "")) if not OPT_RE.match(cid) else None
        if not m:
            continue
        code = f"LH{m[1]}{m[2]}"
        settle = num(pick(x, "clearPrice", "settlePrice", "结算价"))
        if not settle > 0:
            continue
        out[code] = {"settle": settle, "open": num(pick(x, "open", "开盘价")), "high": num(pick(x, "high", "最高价")),
                     "low": num(pick(x, "low", "最低价")), "close": num(pick(x, "close", "收盘价")),
                     "oi": num(pick(x, "openInterest", "持仓量")), "year": 2000 + int(m[1]), "month": int(m[2])}
    return out


def parse_options(recs, d, futs):
    out = []
    for x in recs:
        cid = str(pick(x, "contractId", "合约名称", "合约") or "").strip()
        m = OPT_RE.match(cid)
        if not m:
            continue
        und = f"LH{m[1]}{m[2]}"
        if und not in futs:
            continue
        oi = num(pick(x, "openInterest", "持仓量"))
        if not oi > 0:
            continue
        exp = option_expiry(2000 + int(m[1]), int(m[2]))
        if exp <= d:
            continue
        px = num(pick(x, "clearPrice", "settlePrice", "结算价"))
        if not px > 0:
            px = num(pick(x, "close", "收盘价"))
        iv = num(pick(x, "impliedVolatility", "impliedVol", "iv", "隐含波动率"))
        if iv > 3:
            iv /= 100.0
        out.append([und, exp.isoformat(), float(m[4]), m[3].upper(), round(px, 2) if px > 0 else None, int(oi),
                    round(iv, 4) if iv > 0 else None, und])
    return out


# ---------------- math（與網頁計算引擎相同） ----------------
def ncdf(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def b76(F, K, T, s, call):
    sq = s * math.sqrt(T)
    d1 = (math.log(F / K) + 0.5 * s * s * T) / sq
    d2 = d1 - sq
    df = math.exp(-RATE * T)
    return df * (F * ncdf(d1) - K * ncdf(d2)) if call else df * (K * ncdf(-d2) - F * ncdf(-d1))


def gamma(F, K, T, s):
    sq = s * math.sqrt(T)
    d1 = (math.log(F / K) + 0.5 * s * s * T) / sq
    return math.exp(-RATE * T) * math.exp(-0.5 * d1 * d1) / math.sqrt(2 * math.pi) / (F * sq)


def implied_vol(px, F, K, T, call):
    if px is None or px <= 0:
        return None
    intr = math.exp(-RATE * T) * max(F - K if call else K - F, 0)
    if px <= intr + 0.05 or b76(F, K, T, 3.0, call) < px:
        return None
    lo, hi = 0.01, 3.0
    for _ in range(70):
        m = (lo + hi) / 2
        lo, hi = (lo, m) if b76(F, K, T, m, call) > px else (m, hi)
    return (lo + hi) / 2


def year_frac(exp_iso, val):
    e = datetime.fromisoformat(exp_iso).replace(hour=15, minute=0, tzinfo=CST)
    return max((e - val).total_seconds() / 86400, 0.15) / 365


def enrich(rows, futs, S0, val, fallback=0.2):
    """轉成主力等價：(code, rank, K_eq, call, T, iv, oi, w)"""
    exps = sorted({r[1] for r in rows})
    rank = {e: i for i, e in enumerate(exps)}
    by = {}
    for code, exp, k, cp, px, oi, ivx, und in rows:
        F0 = futs[und]["settle"]
        ratio = S0 / F0
        T = year_frac(exp, val)
        call = cp == "C"
        iv = ivx if ivx else implied_vol(px, F0, k, T, call)
        by.setdefault(code, []).append(dict(code=code, rank=rank[exp], K=k * ratio, call=call, T=T, oi=oi, iv=iv, w=F0 / S0))
    out = []
    for code, lst in by.items():
        otm = lambda x: (x["call"] and x["K"] >= S0) or (not x["call"] and x["K"] <= S0)  # noqa: E731
        pool = [x for x in lst if x["iv"] and otm(x)] or [x for x in lst if x["iv"]]
        for x in lst:
            iv = x["iv"] if (x["iv"] and otm(x)) else None
            if iv is None:
                iv = min(pool, key=lambda p: abs(p["K"] - x["K"]))["iv"] if pool else fallback
            out.append((code, x["rank"], x["K"], x["call"], x["T"], iv, x["oi"], x["w"]))
    return out


def grp(rank):
    return "N1" if rank == 0 else "N2" if rank == 1 else "M"


def gex(cs, S, mode):
    g = 0.0
    for _, _, K, call, T, iv, oi, w in cs:
        sign = -1 if mode == "short" else (1 if call else -1)
        g += sign * gamma(S, K, T, iv) * oi * w * MULT * S * S * 0.01
    return g / 1e4  # 萬元


def flip(cs, S0, mode, pct=0.08, n=400):
    if not cs:
        return None
    grid = [S0 * (1 - pct + 2 * pct * i / n) for i in range(n + 1)]
    vals = [gex(cs, s, mode) for s in grid]
    best = None
    for i in range(1, len(grid)):
        a, b = vals[i - 1], vals[i]
        if (a <= 0 < b) or (a >= 0 > b):
            x = grid[i - 1] + (grid[i] - grid[i - 1]) * (-a) / (b - a)
            if best is None or abs(x - S0) < abs(best - S0):
                best = x
    return round(best, 1) if best else None


def summarize(rows, futs, S0, val):
    cs = enrich(rows, futs, S0, val)
    out = {}
    for mode in ("naive", "short"):
        m = {"gex": round(gex(cs, S0, mode), 2), "flip": flip(cs, S0, mode)}
        for g in ("N1", "N2", "M"):
            sub = [c for c in cs if grp(c[1]) == g]
            m[f"flip{g}"] = flip(sub, S0, mode)
            m[f"gex{g}"] = round(gex(sub, S0, mode), 2) if sub else None
        out[mode] = m
    return out


# ---------------- main ----------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=14, help="往回抓幾個日曆天（首次建議 45）")
    a = ap.parse_args()
    hol = os.path.join(DATA, "holidays.json")
    if os.path.exists(hol):
        HOLIDAYS.update(json.load(open(hol, encoding="utf-8")))
    now = datetime.now(CST)
    today = now.date()
    sess = requests.Session()
    print("使用", "大商所 API（有憑證）" if API_KEY and SECRET else "舊版公開匯出（無憑證）")
    days = []
    for i in range(a.days, -1, -1):
        d = today - timedelta(days=i)
        if d.weekday() >= 5:
            continue
        fut_recs = fetch("fut", d, sess)
        if not fut_recs:
            continue
        opt_recs = fetch("opt", d, sess)
        if not opt_recs:
            continue
        futs = parse_futures(fut_recs, d)
        rows = parse_options(opt_recs, d, futs)
        DEBUG.append(f"{d} futs={len(futs)} opts={len(rows)}")
        if futs and rows:
            days.append((d, futs, rows))
        time.sleep(2.5 if API_KEY else 0.8)

    os.makedirs(os.path.join(DATA, "daily"), exist_ok=True)
    if not days:
        print("沒有可用的交易日資料（休市、尚未公布、憑證未設定，或連線被拒）")
        with open(os.path.join(DATA, "_debug.txt"), "w", encoding="utf-8") as f:
            f.write(now.isoformat() + "\n" + "\n".join(DEBUG[-60:]))
        sys.exit(0)

    hist_path = os.path.join(DATA, "history.json")
    hist = json.load(open(hist_path, encoding="utf-8")) if os.path.exists(hist_path) else {"items": []}
    items = {it["date"]: it for it in hist["items"]}
    prev_futs = None
    latest = None
    for d, futs, rows in days:
        main_code = max(futs, key=lambda k: futs[k]["oi"] if futs[k]["oi"] == futs[k]["oi"] else 0)
        S0 = futs[main_code]["settle"]
        val = datetime(d.year, d.month, d.day, 15, 0, tzinfo=CST)
        rows = [r for r in rows if abs(r[2] * S0 / futs[r[7]]["settle"] - S0) / S0 <= 0.25]
        mf = futs[main_code]
        pc = prev_futs[main_code]["close"] if prev_futs and main_code in prev_futs else None
        hi, lo, cl = mf["high"], mf["low"], mf["close"]
        tr = None
        if all(v == v and v > 0 for v in (hi, lo)):
            tr = hi - lo if pc is None else max(hi - lo, abs(hi - pc), abs(lo - pc))
        prev_futs = futs
        summ = summarize(rows, futs, S0, val)
        fut_out = {k: {kk: (None if v != v else v) for kk, v in f.items() if kk in ("settle", "open", "high", "low", "close", "oi")}
                   for k, f in futs.items()}
        snap = {"tradeDate": d.isoformat(), "valuation": val.isoformat(), "F": S0, "futContract": main_code,
                "product": "LH", "mult": MULT, "unit": "萬元",
                "fields": ["code", "expiry", "strike", "cp", "settle", "oi", "iv", "und"],
                "rows": rows, "futs": fut_out, "summary": summ,
                "generatedAt": datetime.now(CST).isoformat(timespec="seconds")}
        it = {"date": d.isoformat(), "F": S0, "main": main_code,
              **{f"{m}_{k}": v for m, s in summ.items() for k, v in s.items()}}
        if all(v == v and v > 0 for v in (mf["open"], hi, lo, cl)):
            it.update({"open": mf["open"], "high": hi, "low": lo, "close": cl})
        if tr is not None:
            it["tr"] = round(tr, 1)
        items[d.isoformat()] = it
        json.dump(snap, open(os.path.join(DATA, "daily", d.strftime("%Y%m%d") + ".json"), "w", encoding="utf-8"),
                  ensure_ascii=False, separators=(",", ":"))
        latest = snap
        print(d, main_code, "S0", S0, "flip", summ["naive"]["flip"], "rows", len(rows), "tr", tr)

    seq = sorted(items.values(), key=lambda x: x["date"])
    trs = []
    for it in seq:
        if "tr" in it:
            trs.append(it["tr"])
            if len(trs) >= 5:
                it["atr5"] = round(sum(trs[-5:]) / 5, 1)
    # 把 ATR 寫回這次產生的每日檔，讓網頁開啟歷史 JSON 時也有 ATR
    for d, _, _ in days:
        fp = os.path.join(DATA, "daily", d.strftime("%Y%m%d") + ".json")
        it = items.get(d.isoformat(), {})
        if os.path.exists(fp) and it.get("atr5"):
            sn = json.load(open(fp, encoding="utf-8"))
            sn["atr5"] = it["atr5"]
            sn["ohlc"] = [it.get(k) for k in ("open", "high", "low", "close")]
            json.dump(sn, open(fp, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    latest["atr5"] = items[latest["tradeDate"]].get("atr5")
    li = items[latest["tradeDate"]]
    latest["ohlc"] = [li.get(k) for k in ("open", "high", "low", "close")]
    json.dump(latest, open(os.path.join(DATA, "latest.json"), "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    hist["items"] = seq[-250:]
    hist["updatedAt"] = datetime.now(CST).isoformat(timespec="seconds")
    json.dump(hist, open(hist_path, "w", encoding="utf-8"), ensure_ascii=False, indent=0)
    dbg = os.path.join(DATA, "_debug.txt")
    if os.path.exists(dbg):
        os.remove(dbg)


if __name__ == "__main__":
    main()
