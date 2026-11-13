#!/usr/bin/env python3
"""每日抓美股收盤價（Twelve Data），輸出 latest.json。
API 金鑰從環境變數 TWELVEDATA_API_KEY 讀取（GitHub Secrets），不要寫進程式或檔案。
要追蹤的代號放在 tickers.txt（一行一檔，或用逗號/空白分隔）。
"""
import json, os, sys, time, math, datetime, urllib.request, urllib.parse, urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
API = "https://api.twelvedata.com/time_series"
BATCH = 7            # 免費方案每分鐘 8 個額度，一批 7 檔
PAUSE = 65           # 批次之間等待秒數
HISTORY_DAYS = 6     # latest.json 的 history 保留最近幾個「全部齊全」的交易日
OUTPUTSIZE = 130     # 抓約半年日線，用來計算歷史波動率（同樣只算 1 次額度）


def load_tickers():
    raw = "\n".join(l.split("#")[0] for l in open(os.path.join(HERE, "tickers.txt"), encoding="utf-8"))
    out = []
    for t in raw.replace(",", " ").split():
        t = t.strip().upper()
        if t and t not in out:
            out.append(t)
    return out


def get(url, tries=3):
    for i in range(tries):
        try:
            with urllib.request.urlopen(url, timeout=60) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 429 and i < tries - 1:
                time.sleep(PAUSE)
                continue
            raise
    return {}


def parse_batch(data, symbols):
    """回傳 ({代號: {日期: 收盤}}, [問題])。單檔回應沒有外層代號，要自己處理。"""
    series, problems = {}, []
    if len(symbols) == 1 and "values" in data:
        data = {symbols[0]: data}
    for s in symbols:
        d = data.get(s) if isinstance(data, dict) else None
        if not isinstance(d, dict) or d.get("status") == "error" or "values" not in d:
            msg = (d or {}).get("message", "no data") if isinstance(d, dict) else "no data"
            problems.append(f"{s}: {msg}")
            continue
        series[s] = {v["datetime"][:10]: round(float(v["close"]), 4) for v in d["values"]}
    return series, problems


def hist_vol(closes_by_date, n):
    """最近 n 個交易日的年化歷史波動率（%），資料不足回傳 None。"""
    ds = sorted(closes_by_date)
    px = [closes_by_date[d] for d in ds][-(n + 1):]
    if len(px) < n + 1:
        return None
    r = [math.log(px[i] / px[i - 1]) for i in range(1, len(px)) if px[i - 1] > 0 and px[i] > 0]
    if len(r) < 2:
        return None
    m = sum(r) / len(r)
    var = sum((x - m) ** 2 for x in r) / (len(r) - 1)
    return round(math.sqrt(var) * math.sqrt(252) * 100, 1)


def main():
    key = os.environ.get("TWELVEDATA_API_KEY", "").strip()
    if not key:
        print("缺少 TWELVEDATA_API_KEY", file=sys.stderr)
        sys.exit(2)
    tickers = load_tickers()
    series, problems = {}, []
    batches = [tickers[i:i + BATCH] for i in range(0, len(tickers), BATCH)]
    for n, b in enumerate(batches):
        q = urllib.parse.urlencode({"symbol": ",".join(b), "interval": "1day",
                                    "outputsize": OUTPUTSIZE, "apikey": key})
        try:
            s, p = parse_batch(get(f"{API}?{q}"), b)
            series.update(s); problems += p
        except Exception as e:  # 不把含金鑰的網址印出來
            problems.append(f"批次 {b}: {type(e).__name__}")
        if n < len(batches) - 1:
            time.sleep(PAUSE)

    missing = [t for t in tickers if t not in series]
    problems += [f"{t}: 缺資料" for t in missing if not any(x.startswith(t + ":") for x in problems)]
    latest_each = {t: max(series[t]) for t in series}
    asof = max(latest_each.values()) if latest_each else None
    stale = [t for t, d in latest_each.items() if d != asof]
    problems += [f"{t}: 最新日期 {latest_each[t]} 與整體 {asof} 不同" for t in stale]
    complete = bool(tickers) and not missing and not stale and not problems

    prices = {t: series[t][asof] for t in tickers if t in series and asof in series[t]}
    common = None
    for t in series:
        ds = set(series[t])
        common = ds if common is None else common & ds
    dates = sorted(common or [])[-HISTORY_DAYS:]
    history = {d: {t: series[t][d] for t in tickers if t in series} for d in dates}

    hv = {t: {"hv20": hist_vol(series[t], 20), "hv60": hist_vol(series[t], 60),
              "hv120": hist_vol(series[t], 120)} for t in tickers if t in series}
    hi52 = {t: {"hi": max(series[t].values()), "lo": min(series[t].values()),
                "days": len(series[t])} for t in tickers if t in series}

    out = {
        "complete": complete,
        "asof": asof,
        "fetched_at_utc": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        "problems": problems,
        "prices": prices,
        "history": history,
        "hv": hv,            # 年化歷史波動率 %（20／60／120 個交易日）
        "range": hi52,       # 抓取期間（約半年）最高／最低收盤
    }
    with open(os.path.join(HERE, "latest.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print("complete" if complete else "INCOMPLETE", asof, len(prices), "檔", problems)
    sys.exit(0 if complete else 1)


if __name__ == "__main__":
    main()
