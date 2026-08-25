# -*- coding: utf-8 -*-
"""主流程：采集 -> 指标评分 -> 支撑阻力 -> 周期估值 -> 规则化判定 -> 输出 JSON + Markdown 报告。"""
import json
import logging
import pathlib
import datetime as dt

from . import sources, macro
from .scoring import score_frame
from .levels import support_resistance
from .report import build_report

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("main")

ROOT = pathlib.Path(__file__).resolve().parent.parent
TIMEFRAMES = ["4h", "1d", "1w", "1M"]
TF_LOOKBACK = {"4h": 120, "1d": 180, "1w": 150, "1M": 60}


def _pct(series, bars):
    if len(series) <= bars:
        return None
    return round((float(series.iloc[-1]) / float(series.iloc[-1 - bars]) - 1) * 100, 2)


def analyze_coin(coin, missing):
    out = {"frames": {}, "sources": {}}
    daily_close = None
    for tf in TIMEFRAMES:
        try:
            df, src = sources.fetch_klines(coin, tf)
            frame = {
                "close": round(float(df["close"].iloc[-1]), 2),
                "bar_time_utc": df["time"].iloc[-1].isoformat(),
                "score": score_frame(df),
                "levels": support_resistance(df, tf, lookback=TF_LOOKBACK[tf]),
                "n_bars": int(len(df)),
            }
            if tf == "1d":
                daily_close = df["close"]
                frame["change_5d_pct"] = _pct(df["close"], 5)
                frame["change_30d_pct"] = _pct(df["close"], 30)
            if tf == "1w" and coin == "BTC" and len(df) >= 205:
                ma200w = float(df["close"].rolling(200).mean().iloc[-1])
                frame["ma200w"] = round(ma200w, 0)
                frame["price_over_ma200w"] = round(float(df["close"].iloc[-1]) / ma200w, 3)
            out["frames"][tf] = frame
            out["sources"][tf] = src
        except Exception as e:  # noqa: BLE001
            log.warning("%s %s failed entirely: %s", coin, tf, e)
            missing.append(f"{coin} {tf} K线（{str(e)[:90]}）")

    # 现货价（CoinGecko 兜底，也做交叉验证）
    try:
        spot = sources.coingecko_spot(coin)
        out["spot"] = {"price": spot["price"],
                       "change_24h_pct": round(spot["change_24h"], 2) if spot["change_24h"] is not None else None,
                       "source": "CoinGecko"}
    except Exception as e:  # noqa: BLE001
        missing.append(f"{coin} 现货价（{str(e)[:90]}）")

    out["price"] = out.get("spot", {}).get("price") or (
        out["frames"].get("4h", {}).get("close") or out["frames"].get("1d", {}).get("close"))
    out["change_5d_pct"] = out["frames"].get("1d", {}).get("change_5d_pct")
    return out, daily_close


def run():
    now = dt.datetime.now(dt.timezone.utc)
    beijing = now.astimezone(dt.timezone(dt.timedelta(hours=8)))
    missing = []
    data = {
        "generated_at_utc": now.isoformat(timespec="seconds"),
        "generated_at_beijing": beijing.strftime("%Y-%m-%d %H:%M (北京时间)"),
        "date": beijing.strftime("%Y-%m-%d"),
        "coins": {},
        "macro": {},
        "btc_cycle": {},
        "missing": missing,
        "disclaimer": "本报告由开源脚本全自动生成，仅为量化指标快照，不含任何主观判断，不构成投资建议。数字资产风险极高，可能损失全部本金。下单前请以交易所实时行情二次确认。",
    }

    # ---- 币种 ----
    for coin in ["BTC", "ETH", "SOL"]:
        log.info("analyzing %s", coin)
        data["coins"][coin], _ = analyze_coin(coin, missing)

    # ---- 宏观辅助 ----
    for key, fn in [("fear_greed", sources.fear_greed),
                    ("indices", sources.us_indices),
                    ("etf_flows", sources.etf_flows)]:
        try:
            data["macro"][key] = fn()
        except Exception as e:  # noqa: BLE001
            missing.append(f"{key}（{str(e)[:90]}）")

    # ---- BTC 周期估值 ----
    btc = data["coins"].get("BTC", {})
    btc_price = btc.get("price")
    if btc_price:
        try:
            data["btc_cycle"]["power_law"] = sources.btc_power_law(btc_price)
        except Exception as e:  # noqa: BLE001
            missing.append(f"幂律位置（{str(e)[:90]}）")
    w = btc.get("frames", {}).get("1w", {})
    if "ma200w" in w:
        data["btc_cycle"]["ma200w"] = w["ma200w"]
        data["btc_cycle"]["price_over_ma200w"] = w["price_over_ma200w"]
    try:
        data["btc_cycle"]["mvrv"] = sources.mvrv_btc()
    except Exception as e:  # noqa: BLE001
        missing.append(f"MVRV（{str(e)[:90]}）")

    # ---- 规则化性质判定 ----
    spx = data["macro"].get("indices", {}).get("SP500", {})
    data["macro"]["regime"] = macro.classify(
        btc.get("change_5d_pct"), spx.get("change_5d_pct"),
        data["macro"].get("etf_flows", {}).get("BTC"))

    # ---- 规则化触发提醒（机械条件，非建议） ----
    triggers = []
    for coin, cd in data["coins"].items():
        s4 = cd.get("frames", {}).get("4h", {}).get("score", {})
        if s4:
            triggers.append(f"{coin} 4H 加权评分 {s4['total']} → {s4['signal']}")
    if data["btc_cycle"].get("price_over_ma200w"):
        r = data["btc_cycle"]["price_over_ma200w"]
        triggers.append(f"BTC / 200周均线 = {r}（{'高于' if r >= 1 else '⚠️ 低于'}200周均线）")
    etf_note = data["macro"]["regime"].get("etf_note")
    if etf_note:
        triggers.append(etf_note)
    fg = data["macro"].get("fear_greed")
    if fg:
        triggers.append(f"恐惧贪婪指数 {fg['value']}（{fg['label']}）")
    data["triggers"] = triggers

    # ---- 与上次对照 ----
    latest_path = ROOT / "docs" / "data" / "latest.json"
    prev = None
    if latest_path.exists():
        try:
            prev = json.loads(latest_path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            prev = None
    if prev and prev.get("date") != data["date"]:
        cmp_ = {"prev_date": prev.get("date"), "items": []}
        for coin in ["BTC", "ETH", "SOL"]:
            p_old = prev.get("coins", {}).get(coin, {}).get("price")
            p_new = data["coins"].get(coin, {}).get("price")
            s_old = prev.get("coins", {}).get(coin, {}).get("frames", {}).get("4h", {}).get("score", {})
            s_new = data["coins"].get(coin, {}).get("frames", {}).get("4h", {}).get("score", {})
            if p_old and p_new:
                cmp_["items"].append({
                    "coin": coin,
                    "price_prev": p_old, "price_now": p_new,
                    "price_chg_pct": round((p_new / p_old - 1) * 100, 2),
                    "score_prev": s_old.get("total"), "score_now": s_new.get("total"),
                    "signal_prev": s_old.get("signal"), "signal_now": s_new.get("signal"),
                })
        data["prev_compare"] = cmp_

    # ---- 输出 ----
    def _dump(obj):
        return json.dumps(obj, ensure_ascii=False, indent=1, default=str)

    (ROOT / "docs" / "data").mkdir(parents=True, exist_ok=True)
    (ROOT / "docs" / "data" / "history").mkdir(parents=True, exist_ok=True)
    (ROOT / "docs" / "reports").mkdir(parents=True, exist_ok=True)
    (ROOT / "reports").mkdir(parents=True, exist_ok=True)

    latest_path.write_text(_dump(data), encoding="utf-8")
    (ROOT / "docs" / "data" / "history" / f"{data['date']}.json").write_text(_dump(data), encoding="utf-8")

    # history index
    idx = sorted(p.stem for p in (ROOT / "docs" / "data" / "history").glob("*.json"))
    (ROOT / "docs" / "data" / "index.json").write_text(_dump({"dates": idx}), encoding="utf-8")

    md = build_report(data)
    (ROOT / "reports" / f"{data['date']}.md").write_text(md, encoding="utf-8")
    (ROOT / "docs" / "reports" / f"{data['date']}.md").write_text(md, encoding="utf-8")

    log.info("done: %s, missing=%d", data["date"], len(missing))
    return data


if __name__ == "__main__":
    run()
