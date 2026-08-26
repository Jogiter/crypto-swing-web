# -*- coding: utf-8 -*-
"""机械版做多方案：由已有指标按确定性规则派生的参考值。

**这一层不含主观判断。** 信心等级与仓位权重都是规则映射的输出，
不是对行情的看法——同样的输入永远得到同样的结果，可复算、可审计。

skill 报告里那些真正依赖judgment的东西（方向偏好、对行情性质的解读、
"这次不一样"的例外），不在本模块范围；它们由 Phase 2 的主观层覆盖。

设计原则：每个数字都要能回答"这是怎么来的"，所以每项都带 basis 说明。
"""

# 4H ATR 占价格的基准百分比。高于此值视为高波动，仓位相应下调。
TARGET_ATR_PCT = 2.0

# 信心等级 → 基准仓位权重（%）
BASE_POSITION = {"高": 25.0, "中高": 18.0, "中": 12.0, "低": 6.0}

# 行情性质对信心的加减分（对应 macro.REGIMES 的 key）
REGIME_ADJ = {
    "risk_on": 10,
    "decouple_up": 5,
    "neutral": 0,
    "risk_off": -10,
    "decouple_down": -15,
}

POSITION_FLOOR, POSITION_CAP = 2.0, 30.0


def _clamp(x, lo, hi):
    return max(lo, min(hi, x))


def _round_price(p):
    return round(p, 2 if p < 1000 else 0)


def _fmt_price(p):
    """千分位展示，供条件文案使用。"""
    return f"{p:,.0f}" if abs(p) >= 1000 else f"{p:,.2f}"


def _confidence(score4h, regime_key, adx, st_direction):
    """信心等级：4H 评分为基底，按行情性质与趋势确认加减。"""
    parts = [f"4H 评分 {score4h}"]
    conf = float(score4h)

    adj = REGIME_ADJ.get(regime_key, 0)
    if adj:
        conf += adj
        parts.append(f"行情性质 {adj:+d}")

    if adx is not None and adx >= 25:
        if st_direction == "多":
            conf += 5
            parts.append("强趋势且 SuperTrend 多 +5")
        elif st_direction == "空":
            conf -= 8
            parts.append("强趋势但 SuperTrend 空 -8")

    conf = _clamp(conf, 0.0, 100.0)
    if conf >= 75:
        level = "高"
    elif conf >= 60:
        level = "中高"
    elif conf >= 45:
        level = "中"
    else:
        level = "低"
    return {"level": level, "score": round(conf, 1), "basis": "；".join(parts)}


def _position(level, atr_pct):
    """仓位权重：信心给基准，波动率反向调节（风险平价思路）。"""
    base = BASE_POSITION[level]
    if not atr_pct or atr_pct <= 0:
        return {"pct": base, "basis": f"信心「{level}」基准 {base}%（无波动率数据，未调整）"}
    vol_adj = _clamp(TARGET_ATR_PCT / atr_pct, 0.5, 1.5)
    pct = _clamp(base * vol_adj, POSITION_FLOOR, POSITION_CAP)
    return {
        "pct": round(pct, 1),
        "basis": (f"信心「{level}」基准 {base}% × 波动调节 {vol_adj:.2f}"
                  f"（4H ATR {atr_pct:.2f}% vs 基准 {TARGET_ATR_PCT}%）"),
    }


def _entry_zones(price, supports, atr):
    """分批入场区间：现价档 + 逐级下移的支撑档，宽度由 ATR 决定。

    各档必须严格递减且互不重叠——支撑档的上沿被前一档的下沿封住，
    否则贴近现价的支撑会算出比「现价档」还高的区间，读起来像是要
    在更高的位置挂回调单。与前一档几乎重合的档位直接丢弃。
    """
    if not price:
        return []
    band = (atr or price * 0.005) * 0.5
    prev_low = price - band
    zones = [{
        "name": "第一档（现价区）",
        "low": _round_price(prev_low),
        "high": _round_price(price),
        "note": "右侧参与",
    }]

    below = sorted([s for s in supports if s.get("price", 0) < price],
                   key=lambda s: -s["price"])
    labels = ["第二档（第一支撑区）", "第三档（次级支撑区）"]
    notes = ["理想加仓位", "深跌承接位"]
    for s in below:
        if len(zones) > 2:
            break
        p = s["price"]
        high = min(p + band * 0.6, prev_low)
        low = p - band * 0.6
        if high - low < band * 0.2:   # 已被上一档覆盖，无独立意义
            continue
        i = len(zones) - 1
        zones.append({
            "name": labels[i],
            "low": _round_price(low),
            "high": _round_price(high),
            "note": f"{notes[i]}（{s.get('basis', '支撑')}）",
        })
        prev_low = low
    return zones


def _add_conditions(frame4h, score):
    """加仓硬条件：该币自身尚未满足的进攻信号。

    刻意只用该币种自己的指标——BTC 的宏观条件（ETF 流向等）由
    macro_conditions 另行传入，两者语义不同，不混在一起。
    """
    items = []
    total = score.get("total")
    if total is not None and total < 70:
        items.append({"condition": "4H 加权评分 ≥ 70（做多信号）",
                      "gap": f"还差 {round(70 - total, 1)} 分"})

    st = score.get("supertrend") or {}
    if st.get("direction") == "空" and st.get("line"):
        items.append({"condition": "4H SuperTrend 翻多",
                      "gap": f"需 4H 收盘站上 ${_fmt_price(st['line'])}"})

    price = frame4h.get("close")
    res = sorted(((frame4h.get("levels") or {}).get("resistances") or []),
                 key=lambda x: x["price"])
    if price and res:
        r0 = res[0]
        items.append({"condition": f"放量站上第一阻力 ${_fmt_price(r0['price'])}（{r0.get('basis', '')}）",
                      "gap": f"还需上涨 {round((r0['price'] / price - 1) * 100, 2)}%"})
    return items


def build_playbook(frame4h, geometry, regime, macro_conditions=None,
                   atr_pct=None, atr_abs=None, weekly_anchor=None):
    """组装机械版做多方案。数据不足的项留空并在 notes 说明。"""
    out = {"layer": "mechanical",
           "disclaimer": "本方案由确定性规则从已有指标派生，不含主观判断，非投资建议。",
           "notes": []}

    score = (frame4h or {}).get("score") or {}
    total = score.get("total")
    if total is None:
        out["notes"].append("4H 评分缺失，无法给出机械方案")
        return out

    adx = (score.get("adx") or {}).get("value")
    st_dir = (score.get("supertrend") or {}).get("direction")
    regime_key = (regime or {}).get("key", "neutral")

    conf = _confidence(total, regime_key, adx, st_dir)
    out["confidence"] = conf
    out["position"] = _position(conf["level"], atr_pct)
    out["atr_pct"] = round(atr_pct, 2) if atr_pct else None

    price = (geometry or {}).get("entry_ref") or frame4h.get("close")
    supports = ((frame4h or {}).get("levels") or {}).get("supports") or []
    out["entry_zones"] = _entry_zones(price, supports, atr_abs)

    # 加仓硬条件：该币自身信号 + 可选的市场级前提，需全部满足
    items = _add_conditions(frame4h, score)
    for c in (macro_conditions or []):
        if not c.get("met"):
            items.append({"condition": f"[市场级] {c['condition']}", "gap": c.get("gap")})
    if items:
        out["add_conditions"] = {"require": "全部满足", "items": items[:4]}

    # 失效位：优先最终止损，其次结构止损；BTC 另附周线锚
    stops = (geometry or {}).get("stops") or []
    if stops:
        final = stops[-1]
        out["invalidation"] = {
            "price": final["price"],
            "basis": f"{final['name']}（{final.get('basis', '')}）",
        }
    else:
        out["notes"].append("无有效止损位，方案失效价缺失")
    if weekly_anchor:
        out["structural_anchor"] = weekly_anchor

    signal = score.get("signal")
    if signal:
        out["signal_context"] = f"4H 机械信号：{signal}（≥70 做多 / ≤30 离场）"
    return out
