# -*- coding: utf-8 -*-
"""技术指标：与用户 Pine Script v2.2 体系对齐的 SRSI / MACD / MFI / Volume / SuperTrend / ADX。"""
import numpy as np
import pandas as pd


def ema(s, n):
    return s.ewm(span=n, adjust=False).mean()


def rsi(close, n=14):
    delta = close.diff()
    up = delta.clip(lower=0)
    down = -delta.clip(upper=0)
    # Wilder 平滑
    ru = up.ewm(alpha=1 / n, adjust=False).mean()
    rd = down.ewm(alpha=1 / n, adjust=False).mean()
    rs = ru / rd.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(50)


def stoch_rsi(close, rsi_n=14, stoch_n=14, k=3, d=3):
    r = rsi(close, rsi_n)
    lo = r.rolling(stoch_n).min()
    hi = r.rolling(stoch_n).max()
    st = (r - lo) / (hi - lo).replace(0, np.nan) * 100
    kline = st.rolling(k).mean()
    dline = kline.rolling(d).mean()
    return kline, dline


def macd(close, fast=12, slow=26, signal=9):
    dif = ema(close, fast) - ema(close, slow)
    dea = dif.ewm(span=signal, adjust=False).mean()
    hist = dif - dea
    return dif, dea, hist


def mfi(df, n=14):
    tp = (df["high"] + df["low"] + df["close"]) / 3
    mf = tp * df["volume"]
    pos = mf.where(tp > tp.shift(1), 0.0)
    neg = mf.where(tp < tp.shift(1), 0.0)
    pr = pos.rolling(n).sum()
    nr = neg.rolling(n).sum()
    out = 100 - 100 / (1 + pr / nr.replace(0, np.nan))
    return out.fillna(50)


def atr(df, n=14):
    h, l, c = df["high"], df["low"], df["close"]
    tr = pd.concat([h - l, (h - c.shift(1)).abs(), (l - c.shift(1)).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


def supertrend(df, n=10, mult=3.0):
    """返回 (trend Series: 1 多 / -1 空, line Series)。"""
    a = atr(df, n)
    hl2 = (df["high"] + df["low"]) / 2
    upper = hl2 + mult * a
    lower = hl2 - mult * a
    c = df["close"].values
    ub, lb = upper.values.copy(), lower.values.copy()
    trend = np.ones(len(df), dtype=int)
    line = np.full(len(df), np.nan)
    for i in range(1, len(df)):
        ub[i] = min(ub[i], ub[i - 1]) if c[i - 1] <= ub[i - 1] else ub[i]
        lb[i] = max(lb[i], lb[i - 1]) if c[i - 1] >= lb[i - 1] else lb[i]
        if trend[i - 1] == 1:
            trend[i] = 1 if c[i] >= lb[i] else -1
        else:
            trend[i] = -1 if c[i] <= ub[i] else 1
        line[i] = lb[i] if trend[i] == 1 else ub[i]
    return pd.Series(trend, index=df.index), pd.Series(line, index=df.index)


def adx(df, n=14):
    h, l = df["high"], df["low"]
    up = h.diff()
    dn = -l.diff()
    plus_dm = np.where((up > dn) & (up > 0), up, 0.0)
    minus_dm = np.where((dn > up) & (dn > 0), dn, 0.0)
    a = atr(df, n)
    plus_di = 100 * pd.Series(plus_dm, index=df.index).ewm(alpha=1 / n, adjust=False).mean() / a
    minus_di = 100 * pd.Series(minus_dm, index=df.index).ewm(alpha=1 / n, adjust=False).mean() / a
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return dx.ewm(alpha=1 / n, adjust=False).mean().fillna(0), plus_di.fillna(0), minus_di.fillna(0)


def volume_ratio(df, n=20):
    ma = df["volume"].rolling(n).mean()
    return (df["volume"] / ma.replace(0, np.nan)).fillna(1.0)
