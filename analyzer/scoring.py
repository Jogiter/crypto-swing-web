# -*- coding: utf-8 -*-
"""加权评分：SRSI 25 / MACD 25 / MFI 20 / Volume 15 / SuperTrend 15，辅以 ADX。
≥70 做多信号，≤30 离场信号，其余观望。评分逻辑为连续打分（非全有全无），
方向性组件在多头形态给高分、空头形态给低分。
"""
import numpy as np

from . import indicators as ind


def _clip(x, lo=0.0, hi=1.0):
    return float(np.clip(x, lo, hi))


def score_frame(df):
    """对一个K线 DataFrame（含 volume）计算最新的加权评分与组件明细。"""
    close = df["close"]
    out = {}

    # --- SRSI (25) ---
    k, d = ind.stoch_rsi(close)
    kv, dv = float(k.iloc[-1]), float(d.iloc[-1])
    k_prev = float(k.iloc[-2]) if len(k) > 1 else kv
    s = 0.5
    if not np.isnan(kv) and not np.isnan(dv):
        cross_up = kv > dv
        rising = kv > k_prev
        if cross_up and rising:
            s = 0.85 if kv < 80 else 0.6   # 高位钝化打折
        elif cross_up:
            s = 0.65
        elif rising:
            s = 0.5
        else:
            s = 0.15 if kv > 20 else 0.35  # 低位死叉留出底背离空间
        if kv < 20 and cross_up:
            s = 1.0                        # 低位金叉最强
    out["srsi"] = {"k": None if np.isnan(kv) else round(kv, 1),
                   "d": None if np.isnan(dv) else round(dv, 1),
                   "score": round(s * 25, 1), "max": 25}

    # --- MACD (25) ---
    dif, dea, hist = ind.macd(close)
    dv_, ev_, hv = float(dif.iloc[-1]), float(dea.iloc[-1]), float(hist.iloc[-1])
    h_prev = float(hist.iloc[-2]) if len(hist) > 1 else hv
    s = 0.5
    if dv_ > ev_ and hv > 0:
        s = 0.9 if hv > h_prev else 0.7    # 红柱扩张/收缩
        if dv_ > 0:
            s = min(1.0, s + 0.1)          # 零轴上方更强
    elif dv_ > ev_:
        s = 0.6
    else:
        s = 0.3 if hv > h_prev else 0.1    # 绿柱收敛给一点分
    out["macd"] = {"dif": round(dv_, 2), "dea": round(ev_, 2), "hist": round(hv, 2),
                   "score": round(s * 25, 1), "max": 25}

    # --- MFI (20) ---
    has_volume = df["volume"].tail(30).sum() > 0
    if has_volume:
        m = float(ind.mfi(df).iloc[-1])
        m_prev = float(ind.mfi(df).iloc[-2])
        if m >= 80:
            s = 0.45                        # 超买
        elif m >= 55:
            s = 0.8 if m > m_prev else 0.65
        elif m >= 45:
            s = 0.5
        elif m >= 20:
            s = 0.35 if m < m_prev else 0.5
        else:
            s = 0.6                         # 超卖回升预期（但不单独构成入场理由）
        out["mfi"] = {"value": round(m, 1), "score": round(s * 20, 1), "max": 20}
    else:
        out["mfi"] = {"value": None, "score": 10.0, "max": 20, "note": "数据源无成交量，给中性分"}

    # --- Volume (15) ---
    if has_volume:
        vr = float(ind.volume_ratio(df).iloc[-1])
        up_bar = float(close.iloc[-1]) >= float(df["open"].iloc[-1])
        if up_bar:
            s = _clip(0.4 + 0.4 * (vr - 0.8))   # 放量上涨加分
        else:
            s = _clip(0.6 - 0.45 * (vr - 0.8))  # 放量下跌减分
        out["volume"] = {"ratio_vs_ma20": round(vr, 2), "score": round(s * 15, 1), "max": 15}
    else:
        out["volume"] = {"ratio_vs_ma20": None, "score": 7.5, "max": 15, "note": "数据源无成交量，给中性分"}

    # --- SuperTrend (15) ---
    trend, line = ind.supertrend(df)
    st_up = int(trend.iloc[-1]) == 1
    st_line = float(line.iloc[-1]) if not np.isnan(line.iloc[-1]) else None
    out["supertrend"] = {"direction": "多" if st_up else "空",
                         "line": round(st_line, 2) if st_line else None,
                         "score": 15.0 if st_up else 0.0, "max": 15}

    # --- ADX（辅助，不计分） ---
    a, pdi, mdi = ind.adx(df)
    out["adx"] = {"value": round(float(a.iloc[-1]), 1),
                  "plus_di": round(float(pdi.iloc[-1]), 1),
                  "minus_di": round(float(mdi.iloc[-1]), 1),
                  "trend_strength": "强趋势" if a.iloc[-1] >= 25 else ("有趋势" if a.iloc[-1] >= 20 else "震荡")}

    total = round(sum(out[k]["score"] for k in ("srsi", "macd", "mfi", "volume", "supertrend")), 1)
    signal = "做多信号" if total >= 70 else ("离场信号" if total <= 30 else "观望")
    out["total"] = total
    out["signal"] = signal
    return out
