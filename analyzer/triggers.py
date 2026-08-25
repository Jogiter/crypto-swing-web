# -*- coding: utf-8 -*-
"""前瞻性触发阈值：可验证的「转进攻 / 转防御」条件，附当前状态与差距。

与「信号灯」的区别（对应 skill 第 7 步）：
- 信号灯回答「现在是什么样」——当前评分、当前指数读数；
- 触发阈值回答「接下来盯什么才改变决策」——条件、是否已满足、还差多少。

所有条件均为机械规则，数据缺失时该条自动跳过，不做主观推断。
"""


def _fmt_price(p):
    if p is None:
        return "—"
    return f"{p:,.0f}" if abs(p) >= 1000 else f"{p:,.2f}"


def _cond(name, met, current, gap=None):
    return {"condition": name, "met": bool(met), "current": current, "gap": gap}


def _btc_frame(data, tf):
    return data.get("coins", {}).get("BTC", {}).get("frames", {}).get(tf, {}) or {}


def _ref_price(data, tf="4h"):
    """几何比较基准价：优先该周期的收盘价。

    支撑阻力是从 K 线算出来的，若拿另一个来源的现货价去比，两者不同源时
    会得出「还需上涨 -27%」这类自相矛盾的结论。现货价仅用于展示。
    """
    return _btc_frame(data, tf).get("close") or data.get("coins", {}).get("BTC", {}).get("price")


def _etf_recent(data, key="BTC", n=3):
    rows = (data.get("macro", {}).get("etf_flows", {}) or {}).get(key)
    if not rows:
        return None
    return [x["total_musd"] for x in rows[-n:]]


def build_conditions(data):
    """返回 {"offense": [...], "defense": [...]}，每项为 _cond() 结构。"""
    offense, defense = [], []

    f4 = _btc_frame(data, "4h")
    price = _ref_price(data, "4h")
    score4 = f4.get("score", {}) or {}
    levels4 = f4.get("levels", {}) or {}
    cycle = data.get("btc_cycle", {}) or {}

    # ---------- ETF 流向（先行灯） ----------
    etf = _etf_recent(data, "BTC", 3)
    if etf:
        seq = " / ".join(f"{v:+,.0f}M" for v in etf)
        pos, neg = sum(1 for v in etf if v > 0), sum(1 for v in etf if v < 0)
        offense.append(_cond(
            "BTC ETF 连续 3 日净流入", all(v > 0 for v in etf), f"近3日 {seq}",
            None if all(v > 0 for v in etf) else f"当前 {pos}/3 日为正，还需 {3 - pos} 日转正"))
        defense.append(_cond(
            "BTC ETF 连续 3 日净流出", all(v < 0 for v in etf), f"近3日 {seq}",
            None if all(v < 0 for v in etf) else f"当前 {neg}/3 日为负"))

    # ---------- 4H 加权评分 ----------
    total = score4.get("total")
    if total is not None:
        offense.append(_cond(
            "BTC 4H 加权评分 ≥ 70（做多信号）", total >= 70, f"当前 {total}/100",
            None if total >= 70 else f"还差 {round(70 - total, 1)} 分"))
        defense.append(_cond(
            "BTC 4H 加权评分 ≤ 30（离场信号）", total <= 30, f"当前 {total}/100",
            None if total <= 30 else f"距触发还有 {round(total - 30, 1)} 分空间"))

    # ---------- SuperTrend 方向 ----------
    st = score4.get("supertrend", {}) or {}
    st_dir, st_line = st.get("direction"), st.get("line")
    if st_dir:
        line_s = f"，趋势线 ${_fmt_price(st_line)}" if st_line else ""
        if st_dir == "空":
            offense.append(_cond(
                "BTC 4H SuperTrend 翻多", False, f"当前为空{line_s}",
                f"需 4H 收盘站上 ${_fmt_price(st_line)}" if st_line else "需 4H 收盘翻多"))
        else:
            defense.append(_cond(
                "BTC 4H SuperTrend 翻空", False, f"当前为多{line_s}",
                f"跌破 ${_fmt_price(st_line)} 即翻空"
                + (f"（距现价 {round((st_line / price - 1) * 100, 2)}%）" if st_line and price else "")))

    # ---------- 关键点位突破 / 失守 ----------
    if price:
        res = sorted((levels4.get("resistances") or []), key=lambda x: x["price"])
        sup = sorted((levels4.get("supports") or []), key=lambda x: -x["price"])
        if res:
            r0 = res[0]
            gap_pct = round((r0["price"] / price - 1) * 100, 2)
            above = gap_pct <= 0
            offense.append(_cond(
                f"BTC 4H 放量站上第一阻力 ${_fmt_price(r0['price'])}（{r0.get('basis', '')}）",
                above, f"参考价 ${_fmt_price(price)}",
                None if above else f"还需上涨 {gap_pct}%"))
        if sup:
            s0 = sup[0]
            drop_pct = round((1 - s0["price"] / price) * 100, 2)
            below = drop_pct <= 0
            defense.append(_cond(
                f"BTC 4H 失守第一支撑 ${_fmt_price(s0['price'])}（{s0.get('basis', '')}）",
                below, f"参考价 ${_fmt_price(price)}",
                None if below else f"下跌 {drop_pct}% 即触发"))

    # ---------- 周线 200 周均线（结构生死线） ----------
    ma200w = cycle.get("ma200w")
    w_price = _ref_price(data, "1w") or price
    if ma200w and w_price:
        below = w_price < ma200w
        defense.append(_cond(
            f"BTC 周线失守 200 周均线 ${_fmt_price(ma200w)}", below,
            f"周线收盘 ${_fmt_price(w_price)}，价格/200周均线 = {cycle.get('price_over_ma200w', '—')}",
            None if below else f"需下跌 {round((1 - ma200w / w_price) * 100, 1)}% 才触及"))

    # ---------- 周期估值过热（CBBI / MVRV） ----------
    cb = cycle.get("cbbi")
    if cb and cb.get("value") is not None:
        v = cb["value"]
        defense.append(_cond(
            "CBBI ≥ 90（周期顶部预警）", v >= 90, f"当前 {v}（{cb.get('zone', '')}）",
            None if v >= 90 else f"还差 {round(90 - v, 1)} 点"))
        offense.append(_cond(
            "CBBI < 15（周期底部区）", v < 15, f"当前 {v}（{cb.get('zone', '')}）",
            None if v < 15 else f"需再降 {round(v - 15, 1)} 点"))
    mv = cycle.get("mvrv")
    if mv and mv.get("value") is not None:
        v = mv["value"]
        pctl = f"，历史分位 {mv['percentile']}%" if mv.get("percentile") is not None else ""
        z = f"，Z={mv['zscore']}" if mv.get("zscore") is not None else ""
        defense.append(_cond(
            "MVRV > 3（过热区）", v > 3, f"当前 {v}{z}{pctl}",
            None if v > 3 else f"还差 {round(3 - v, 2)}"))
        offense.append(_cond(
            "MVRV < 1（历史底部区）", v < 1, f"当前 {v}{z}{pctl}",
            None if v < 1 else f"需再降 {round(v - 1, 2)}"))

    # ---------- 情绪 ----------
    fg = data.get("macro", {}).get("fear_greed")
    if fg and fg.get("value") is not None:
        v = fg["value"]
        was_extreme = _fg_was_extreme(fg)
        rebounded = v > 30
        if was_extreme and rebounded:
            fg_gap = None
        elif was_extreme:
            fg_gap = f"近期已探入极度恐惧区，当前 {v}，还需回升 {31 - v} 点至 >30"
        else:
            fg_gap = "近8期未探入 <25，条件尚未起步"
        offense.append(_cond(
            "恐惧贪婪指数自极度恐惧区(<25)回升至 >30", was_extreme and rebounded,
            f"当前 {v}（{fg.get('label', '')}）", fg_gap))
        defense.append(_cond(
            "恐惧贪婪指数 > 75（极度贪婪）", v > 75, f"当前 {v}（{fg.get('label', '')}）",
            None if v > 75 else f"还差 {75 - v} 点"))

    return {"offense": offense, "defense": defense}


def _fg_was_extreme(fg, lookback=8, threshold=25):
    """近期是否曾进入极度恐惧区。"""
    hist = fg.get("history") or []
    return any(h.get("value", 100) < threshold for h in hist[:lookback])
