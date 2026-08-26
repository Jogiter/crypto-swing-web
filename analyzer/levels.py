# -*- coding: utf-8 -*-
"""支撑/阻力：分形摆动点 + EMA + 心理整数关口，多源合并去重，每个点位带依据。"""
import numpy as np

from .indicators import ema


def _swing_points(df, left=3, right=3):
    """分形摆动高低点。"""
    highs, lows = [], []
    h, l = df["high"].values, df["low"].values
    n = len(df)
    for i in range(left, n - right):
        if h[i] == max(h[i - left:i + right + 1]):
            highs.append(float(h[i]))
        if l[i] == min(l[i - left:i + right + 1]):
            lows.append(float(l[i]))
    return highs, lows


def _round_levels(price):
    """心理整数关口：按价格量级取整。"""
    mag = 10 ** int(np.floor(np.log10(price)))
    step = mag / 2 if price / mag < 3 else mag
    base = np.floor(price / step) * step
    return [float(base - step), float(base), float(base + step), float(base + 2 * step)]


def _cluster(levels, tol_pct):
    """按容差聚类合并相近点位，返回 [(price, [bases])]。"""
    levels = sorted(levels, key=lambda x: x[0])
    out = []
    for price, basis in levels:
        if out and abs(price - out[-1][0]) / out[-1][0] < tol_pct:
            prev_p, prev_b = out[-1]
            merged = (prev_p + price) / 2
            out[-1] = (merged, prev_b + [basis])
        else:
            out.append((price, [basis]))
    return out


def support_resistance(df, tf_label, lookback=120, max_each=3):
    """返回 {supports: [...], resistances: [...]}，每项 {price, basis}。"""
    d = df.tail(lookback).reset_index(drop=True)
    price = float(df["close"].iloc[-1])
    cands = []

    highs, lows = _swing_points(d)
    for x in highs[-12:]:
        cands.append((x, "前高"))
    for x in lows[-12:]:
        cands.append((x, "前低"))

    for n in (20, 50, 100, 200):
        if len(df) >= n + 5:
            v = float(ema(df["close"], n).iloc[-1])
            cands.append((v, f"EMA{n}"))

    for x in _round_levels(price):
        if x > 0:
            cands.append((x, "整数关口"))

    tol = {"4h": 0.006, "1d": 0.012, "1w": 0.02, "1M": 0.03}.get(tf_label, 0.01)
    clustered = _cluster(cands, tol)

    sup = [(p, b) for p, b in clustered if p < price * 0.998]
    res = [(p, b) for p, b in clustered if p > price * 1.002]

    def _fmt(items, reverse):
        items = sorted(items, key=lambda x: abs(x[0] - price))
        # 多依据点位优先
        items = sorted(items[:max_each * 2], key=lambda x: (-len(x[1]), abs(x[0] - price)))[:max_each]
        items = sorted(items, key=lambda x: x[0], reverse=reverse)
        return [{"price": round(p, 2 if p < 1000 else 0), "basis": "+".join(dict.fromkeys(b))}
                for p, b in items]

    return {"supports": _fmt(sup, True), "resistances": _fmt(res, False)}


# 生死线的结构重要度排序：越靠前越有"跌破即证伪"的分量
STRUCTURAL_PRIORITY = ["200 周均线", "20 周 EMA", "周线 SuperTrend"]


def key_pivots(weekly_frame, price):
    """关键翻多线与结构生死线——周线级的两个决定性价位。

    翻多线：价格**上方**最近的周线级关键位，站上即中期结构转多。
    生死线：价格**下方**结构分量最重的周线级防线，跌破即中期论点证伪
            （按 STRUCTURAL_PRIORITY 取，不是取最低——见下方说明）。

    候选只取周线级（周线 SuperTrend / 200 周均线 / 20 周 EMA）——
    日内点位不具备"结构性"含义，混进来会稀释这两行的分量。
    """
    cands = []
    st = (weekly_frame.get("score") or {}).get("supertrend") or {}
    if st.get("line"):
        cands.append((float(st["line"]), "周线 SuperTrend"))
    if weekly_frame.get("ma200w"):
        cands.append((float(weekly_frame["ma200w"]), "200 周均线"))
    if weekly_frame.get("ema20w"):
        cands.append((float(weekly_frame["ema20w"]), "20 周 EMA"))

    def _fmt(p, basis):
        return {"price": round(p, 2 if p < 1000 else 0),
                "basis": basis,
                "distance_pct": round((p / price - 1) * 100, 2)}

    out = {}
    above = [c for c in cands if c[0] > price]
    below = [c for c in cands if c[0] < price]

    # 翻多线取「上方最近」——挡在前面的第一道墙才是要突破的那道。
    if above:
        p, b = min(above, key=lambda x: x[0])
        out["flip_long"] = _fmt(p, b)

    # 生死线按「结构重要度」而非「最低」。二者不对称是有意的：
    # 取最低会选到离现价 -33% 的位，那种距离下论点早已证伪，不构成防线。
    for basis in STRUCTURAL_PRIORITY:
        hit = [c for c in below if c[1] == basis]
        if hit:
            out["structural_line"] = _fmt(hit[0][0], basis)
            break
    return out
