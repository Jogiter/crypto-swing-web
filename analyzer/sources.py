# -*- coding: utf-8 -*-
"""数据源适配层：多级降级，保证在美国 IP（GitHub Actions）与其他地区都可用。

K线优先级：Kraken(原生 4H/1D/1W, 美国可用) -> Coinbase(1H聚合4H, 分页日线) -> Binance(非美IP) -> CoinGecko(兜底, 无成交量)
辅助数据：alternative.me 恐惧贪婪、CoinMetrics 社区版 MVRV、Yahoo 美股三大指数、Farside ETF 流向（尽力而为）。
任何一个数据源失败都不会中断整体分析，只在报告中标注缺失。
"""
import os
import time
import logging
import datetime as dt

import requests
import pandas as pd
import numpy as np

log = logging.getLogger("sources")

UA = {"User-Agent": "Mozilla/5.0 (crypto-swing-web; +https://github.com)"}
TIMEOUT = 25

COINS = {
    "BTC": {"kraken": "XBTUSD", "coinbase": "BTC-USD", "binance": "BTCUSDT", "coingecko": "bitcoin"},
    "ETH": {"kraken": "ETHUSD", "coinbase": "ETH-USD", "binance": "ETHUSDT", "coingecko": "ethereum"},
    "SOL": {"kraken": "SOLUSD", "coinbase": "SOL-USD", "binance": "SOLUSDT", "coingecko": "solana"},
}

KLINE_COLS = ["time", "open", "high", "low", "close", "volume"]


def _get(url, params=None, headers=None, retries=None, backoff=None):
    if retries is None:
        retries = int(os.environ.get("HTTP_RETRIES", "2"))
    if backoff is None:
        backoff = float(os.environ.get("HTTP_BACKOFF", "3"))
    last = None
    for i in range(retries + 1):
        try:
            r = requests.get(url, params=params, headers=headers or UA, timeout=TIMEOUT)
            if r.status_code == 200:
                return r
            last = f"HTTP {r.status_code}"
        except Exception as e:  # noqa: BLE001
            last = repr(e)
        time.sleep(backoff * (i + 1))
    raise RuntimeError(f"GET {url} failed: {last}")


def _df(rows):
    df = pd.DataFrame(rows, columns=KLINE_COLS)
    for c in KLINE_COLS:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
    df = df.dropna(subset=["close"]).sort_values("time").reset_index(drop=True)
    return df


def _resample(df, rule):
    """将低周期K线聚合为高周期（rule 例如 '4h' / 'W-MON' / 'MS'）。"""
    g = df.set_index("time").resample(rule, label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    ).dropna(subset=["close"]).reset_index()
    return g


# ---------------- Kraken ----------------

_KRAKEN_INTERVAL = {"4h": 240, "1d": 1440, "1w": 10080}


def kraken_klines(coin, tf):
    pair = COINS[coin]["kraken"]
    r = _get("https://api.kraken.com/0/public/OHLC",
             params={"pair": pair, "interval": _KRAKEN_INTERVAL[tf]})
    j = r.json()
    if j.get("error"):
        raise RuntimeError(f"kraken error: {j['error']}")
    key = [k for k in j["result"] if k != "last"][0]
    rows = [[int(x[0]), x[1], x[2], x[3], x[4], x[6]] for x in j["result"][key]]
    df = _df(rows)
    # Kraken 最后一根为未收线K线，保留（分析时按“当前进行中K线”处理）
    return df


# ---------------- Coinbase ----------------

_CB_GRAN = {"1h": 3600, "1d": 86400}


def _coinbase_page(product, granularity, start, end):
    r = _get(f"https://api.exchange.coinbase.com/products/{product}/candles",
             params={"granularity": granularity,
                     "start": start.isoformat(), "end": end.isoformat()})
    # coinbase 返回 [time, low, high, open, close, volume]，倒序
    rows = [[int(x[0]), x[3], x[2], x[1], x[4], x[5]] for x in r.json()]
    return rows


def coinbase_klines(coin, tf, days=None):
    product = COINS[coin]["coinbase"]
    now = dt.datetime.now(dt.timezone.utc)
    if tf == "4h":
        # 用 1h 聚合，取约 60 天（360 根 4H）
        rows = []
        for k in range(5):
            end = now - dt.timedelta(hours=300 * k)
            start = end - dt.timedelta(hours=300)
            rows += _coinbase_page(product, 3600, start, end)
            time.sleep(0.4)
        df = _df(rows).drop_duplicates(subset="time").sort_values("time").reset_index(drop=True)
        return _resample(df, "4h")
    # 日线：分页取 days 天（默认 1500 天，够 200 周）
    days = days or 1500
    rows = []
    for k in range(int(np.ceil(days / 300))):
        end = now - dt.timedelta(days=300 * k)
        start = end - dt.timedelta(days=300)
        rows += _coinbase_page(product, 86400, start, end)
        time.sleep(0.4)
    df = _df(rows).drop_duplicates(subset="time").sort_values("time").reset_index(drop=True)
    if tf == "1d":
        return df
    if tf == "1w":
        return _resample(df, "W-MON")
    raise ValueError(tf)


# ---------------- Binance ----------------

_BN_INTERVAL = {"4h": "4h", "1d": "1d", "1w": "1w", "1M": "1M"}


def binance_klines(coin, tf):
    sym = COINS[coin]["binance"]
    last_err = None
    for host in ("https://data-api.binance.vision", "https://api.binance.com"):
        try:
            r = _get(f"{host}/api/v3/klines",
                     params={"symbol": sym, "interval": _BN_INTERVAL[tf], "limit": 1000},
                     retries=0)
            rows = [[int(x[0] / 1000), x[1], x[2], x[3], x[4], x[5]] for x in r.json()]
            return _df(rows)
        except Exception as e:  # noqa: BLE001
            last_err = e
    raise RuntimeError(f"binance failed: {last_err}")


# ---------------- CoinGecko（兜底，无成交量） ----------------

def coingecko_klines(coin, tf):
    cid = COINS[coin]["coingecko"]
    if tf == "4h":
        # days=30 时 OHLC 粒度为 4 小时
        r = _get(f"https://api.coingecko.com/api/v3/coins/{cid}/ohlc",
                 params={"vs_currency": "usd", "days": 30})
        rows = [[int(x[0] / 1000), x[1], x[2], x[3], x[4], 0.0] for x in r.json()]
        return _df(rows)
    r = _get(f"https://api.coingecko.com/api/v3/coins/{cid}/market_chart",
             params={"vs_currency": "usd", "days": 365, "interval": "daily"})
    j = r.json()
    prices = j["prices"]
    vols = {int(t / 1000): v for t, v in j.get("total_volumes", [])}
    rows = []
    for t, p in prices:
        ts = int(t / 1000)
        rows.append([ts, p, p, p, p, vols.get(ts, 0.0)])
    df = _df(rows)
    if tf == "1d":
        return df
    if tf == "1w":
        return _resample(df, "W-MON")
    raise ValueError(tf)


def coingecko_spot(coin):
    cid = COINS[coin]["coingecko"]
    r = _get("https://api.coingecko.com/api/v3/simple/price",
             params={"ids": cid, "vs_currencies": "usd", "include_24hr_change": "true"})
    j = r.json()[cid]
    return {"price": j["usd"], "change_24h": j.get("usd_24h_change")}


# ---------------- 统一入口 ----------------

def fetch_klines(coin, tf):
    """返回 (df, source_name)。tf: 4h / 1d / 1w / 1M。"""
    if tf == "1M":
        df, src = fetch_klines(coin, "1d")
        return _resample(df, "MS"), src + "+月线聚合"
    attempts = [
        ("Kraken", lambda: kraken_klines(coin, tf)),
        ("Coinbase", lambda: coinbase_klines(coin, tf)),
        ("Binance", lambda: binance_klines(coin, tf)),
        ("CoinGecko", lambda: coingecko_klines(coin, tf)),
    ]
    allow = os.environ.get("KLINE_SOURCES")
    if allow:
        allowed = {s.strip().lower() for s in allow.split(",")}
        attempts = [(n, f) for n, f in attempts if n.lower() in allowed]
    last = None
    for name, fn in attempts:
        try:
            df = fn()
            if len(df) >= 30:
                return df, name
            last = f"{name}: too few rows ({len(df)})"
        except Exception as e:  # noqa: BLE001
            last = f"{name}: {e}"
            log.warning("fetch_klines %s %s via %s failed: %s", coin, tf, name, e)
    raise RuntimeError(f"all kline sources failed for {coin} {tf}: {last}")


# ---------------- 辅助数据（全部尽力而为） ----------------

def fear_greed():
    r = _get("https://api.alternative.me/fng/", params={"limit": 8})
    data = r.json()["data"]
    return {
        "value": int(data[0]["value"]),
        "label": data[0]["value_classification"],
        "history": [{"value": int(d["value"]), "ts": int(d["timestamp"])} for d in data],
    }


def _coinmetrics_series(metrics, asset="btc", max_pages=10, page_size=1000):
    """拉取 CoinMetrics 社区版全历史日线序列，返回 [{time, <metric>...}, ...]（时间升序）。

    社区版免费、无需 key，但有分页；这里最多翻 max_pages 页（足够覆盖 BTC 全历史）。
    """
    url = "https://community-api.coinmetrics.io/v4/timeseries/asset-metrics"
    params = {"assets": asset, "metrics": ",".join(metrics), "frequency": "1d",
              "page_size": page_size, "paging_from": "start"}
    rows = []
    for _ in range(max_pages):
        r = _get(url, params=params)
        j = r.json()
        rows += j.get("data", [])
        nxt = j.get("next_page_token")
        if not nxt:
            break
        params = dict(params, next_page_token=nxt)
    return rows


def _to_float(row, key):
    v = row.get(key)
    if v in (None, ""):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def mvrv_btc():
    """BTC MVRV：读数 + 历史分位 + Z-Score，逐级降级，能拿多少拿多少。

    CoinMetrics 社区版（免费无 key）只开放部分指标：CapMVRVCur 可用，
    而 Z-Score 所需的 CapMrktCurUSD / CapRealUSD 会返回 403。
    因此三项分别独立请求——市值序列拿不到时，历史分位仍然保留。
    """
    try:
        out = _mvrv_with_percentile()
    except Exception as e:  # noqa: BLE001
        log.warning("mvrv history failed, falling back to latest-only: %s", e)
        return _mvrv_latest_only()

    try:
        out.update(_mvrv_zscore())
    except Exception as e:  # noqa: BLE001
        log.warning("mvrv z-score unavailable (community tier lacks cap metrics): %s", e)
        out["zscore_note"] = "社区版无市值/实现市值序列权限，Z-Score 不可用"
    return out


def _mvrv_latest_only():
    """最低降级：只取最近一条 MVRV 读数（请求极小、几乎必成功）。"""
    r = _get("https://community-api.coinmetrics.io/v4/timeseries/asset-metrics",
             params={"assets": "btc", "metrics": "CapMVRVCur", "frequency": "1d",
                     "page_size": 10, "paging_from": "end"})
    rows = r.json().get("data", [])
    for row in reversed(rows):
        v = _to_float(row, "CapMVRVCur")
        if v is not None:
            return {"value": round(v, 3), "date": str(row.get("time", ""))[:10],
                    "zone": mvrv_zone(v), "degraded": "全历史序列不可用，缺历史分位与 Z-Score"}
    raise RuntimeError("no mvrv data")


def _mvrv_with_percentile():
    """MVRV 全历史 → 当前读数 + 历史分位。只请求社区版确定可用的 CapMVRVCur。"""
    rows = _coinmetrics_series(["CapMVRVCur"])
    if not rows:
        raise RuntimeError("no mvrv data")

    hist = [v for v in (_to_float(x, "CapMVRVCur") for x in rows) if v is not None]
    if not hist:
        raise RuntimeError("no mvrv values")

    last, value = rows[-1], _to_float(rows[-1], "CapMVRVCur")
    if value is None:
        for x in reversed(rows):
            value = _to_float(x, "CapMVRVCur")
            if value is not None:
                last = x
                break
    if value is None:
        raise RuntimeError("no usable mvrv value")

    arr = np.asarray(hist, dtype=float)
    return {
        "value": round(value, 3),
        "date": str(last.get("time", ""))[:10],
        "zone": mvrv_zone(value),
        "history_days": len(hist),
        "percentile": round(float((arr <= value).sum()) / len(arr) * 100, 1),
    }


def _mvrv_zscore():
    """MVRV Z-Score = (市值 - 实现市值) / 市值全历史标准差。

    这两个指标在社区版免费层通常无权限（403），调用方需容忍失败。
    """
    rows = _coinmetrics_series(["CapMrktCurUSD", "CapRealUSD"])
    caps = [c for c in (_to_float(x, "CapMrktCurUSD") for x in rows) if c is not None]
    if len(caps) <= 30:
        raise RuntimeError("market cap series too short")

    mkt = real = None
    for x in reversed(rows):
        if mkt is None:
            mkt = _to_float(x, "CapMrktCurUSD")
        if real is None:
            real = _to_float(x, "CapRealUSD")
        if mkt is not None and real is not None:
            break
    if mkt is None or real is None:
        raise RuntimeError("missing cap values")

    sd = float(np.std(np.asarray(caps, dtype=float)))
    if sd <= 0:
        raise RuntimeError("zero stddev")
    z = (mkt - real) / sd
    return {"zscore": round(z, 2), "zscore_zone": mvrv_z_zone(z)}


def mvrv_zone(v):
    if v > 3:
        return "过热区(>3)"
    if v < 1:
        return "历史底部区(<1)"
    return "中性区"


def mvrv_z_zone(z):
    """MVRV Z-Score 常用分区：>7 顶部泡沫，<0 深度价值。"""
    if z > 7:
        return "顶部泡沫区(>7)"
    if z > 3.5:
        return "偏热(>3.5)"
    if z < 0:
        return "深度价值区(<0)"
    if z < 1:
        return "低估区(<1)"
    return "中性区"


def cbbi():
    """CBBI（colintalkscrypto Bitcoin Bull Run Index）：综合周期指数 0-100。

    数据源返回各子指标的 {unix_ts: value} 映射，'Confidence' 即 CBBI 主指数（0-1）。
    """
    r = _get("https://colintalkscrypto.com/cbbi/data/latest.json")
    j = r.json()
    conf = j.get("Confidence") or {}
    if not conf:
        raise RuntimeError("no cbbi confidence series")
    ts = max(conf.keys(), key=lambda k: int(k))
    raw = conf[ts]
    if raw is None:
        raise RuntimeError("cbbi latest value is null")
    value = round(float(raw) * 100, 1)

    out = {
        "value": value,
        "date": dt.datetime.fromtimestamp(int(ts), dt.timezone.utc).strftime("%Y-%m-%d"),
        "zone": cbbi_zone(value),
    }

    # 30 天前对照，给出周期方向
    try:
        keys = sorted(conf.keys(), key=lambda k: int(k))
        prev_key = keys[-31] if len(keys) > 31 else keys[0]
        prev = conf.get(prev_key)
        if prev is not None:
            out["value_30d_ago"] = round(float(prev) * 100, 1)
            out["change_30d"] = round(out["value"] - out["value_30d_ago"], 1)
    except (ValueError, IndexError):  # noqa: PERF203
        pass
    return out


def cbbi_zone(v):
    if v >= 90:
        return "周期顶部预警(>=90)"
    if v >= 70:
        return "偏热(70-90)"
    if v < 15:
        return "周期底部区(<15)"
    if v < 30:
        return "偏冷(15-30)"
    return "中性区(30-70)"


def us_indices():
    """Yahoo Finance chart API：标普/纳指/道指近 5 日。"""
    out = {}
    for name, sym in [("SP500", "^GSPC"), ("NASDAQ", "^IXIC"), ("DOW", "^DJI")]:
        r = _get(f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}",
                 params={"range": "10d", "interval": "1d"},
                 headers={"User-Agent": UA["User-Agent"], "Accept": "application/json"})
        res = r.json()["chart"]["result"][0]
        closes = [c for c in res["indicators"]["quote"][0]["close"] if c is not None]
        if len(closes) < 2:
            continue
        out[name] = {
            "last": round(closes[-1], 2),
            "change_1d_pct": round((closes[-1] / closes[-2] - 1) * 100, 2),
            "change_5d_pct": round((closes[-1] / closes[max(0, len(closes) - 6)] - 1) * 100, 2),
        }
        time.sleep(0.5)
    if not out:
        raise RuntimeError("no index data")
    return out


def etf_flows():
    """Farside BTC/ETH 现货 ETF 日度净流向（尽力而为：站点有 CF 防护时会失败）。"""
    out = {}
    for key, url in [("BTC", "https://farside.co.uk/btc/"), ("ETH", "https://farside.co.uk/eth/")]:
        try:
            r = _get(url, headers={"User-Agent": UA["User-Agent"]}, retries=1)
            tables = pd.read_html(r.text)
            best = None
            for t in tables:
                cols = [str(c).lower() for c in t.columns.get_level_values(-1)] \
                    if hasattr(t.columns, "get_level_values") else [str(c).lower() for c in t.columns]
                if any("total" in c for c in cols):
                    best = t
                    break
            if best is None:
                continue
            if hasattr(best.columns, "get_level_values"):
                best.columns = [str(c) for c in best.columns.get_level_values(-1)]
            total_col = [c for c in best.columns if "total" in str(c).lower()][0]
            date_col = best.columns[0]
            rows = []
            for _, row in best.iterrows():
                d = str(row[date_col])
                v = str(row[total_col]).replace(",", "").replace("(", "-").replace(")", "")
                try:
                    rows.append({"date": d, "total_musd": float(v)})
                except ValueError:
                    continue
            rows = [x for x in rows if abs(x["total_musd"]) < 1e5][-10:]
            if rows:
                out[key] = rows
        except Exception as e:  # noqa: BLE001
            log.warning("etf flow %s failed: %s", key, e)
    if not out:
        raise RuntimeError("etf flows unavailable")
    return out


def btc_power_law(price, date=None):
    """BTC 幂律走廊近似位置（公开拟合系数，仅作周期参考，非精确模型）。
    support = 10^(-17.351 + 5.836*log10(days since 2009-01-03))
    """
    date = date or dt.date.today()
    days = (date - dt.date(2009, 1, 3)).days
    ld = np.log10(days)
    support = 10 ** (-17.351 + 5.836 * ld)
    center = support * 2.5   # 走廊中轨近似
    top = support * 10       # 泡沫带近似
    pos = (np.log10(price) - np.log10(support)) / (np.log10(top) - np.log10(support))
    return {
        "days_since_genesis": days,
        "support": round(float(support), 0),
        "center": round(float(center), 0),
        "top": round(float(top), 0),
        "position_pct": round(float(pos) * 100, 1),  # 0=下轨 100=泡沫带
        "note": "幂律系数为公开近似拟合，仅供周期定位参考",
    }
