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
