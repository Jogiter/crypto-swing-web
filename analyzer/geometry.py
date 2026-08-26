# -*- coding: utf-8 -*-
"""交易几何：结构止损 / 最终止损 / 分批止盈 / 风险回报比。

**纯几何计算，不含任何方向判断或入场建议。**
输入是已经算好的支撑阻力与 SuperTrend 线，输出是"若在参考价位做多，
止损应放在哪个结构位、目标位在哪、风险回报比是多少"。

是否入场、仓位多大、信心几何——这些依赖对行情性质的综合判断，
不在本模块（也不在本项目）的范围内。
"""

# 止损缓冲：设在结构位下方一点，避免插针扫损。按周期给不同幅度。
STOP_BUFFER = {"4h": 0.004, "1d": 0.008, "1w": 0.015, "1M": 0.025}

# 分批止盈的减仓比例（对齐 skill 报告模板：TP1/TP2 各减 1/3，TP3 移动止盈）
TP_PLAN = ["减 1/3", "减 1/3", "剩余转移动止盈"]


def _pct(a, b):
    """a 相对 b 的百分比距离。"""
    if not b:
        return None
    return round((a / b - 1) * 100, 2)


def _round_price(p):
    return round(p, 2 if p < 1000 else 0)


def trade_geometry(price, levels, supertrend_line=None, tf="4h", wider_levels=None):
    """计算做多方向的止损/止盈/RR。

    price           : 参考入场价（通常是现价）
    levels          : {"supports": [{price, basis}], "resistances": [...]}（本周期）
    supertrend_line : 本周期 SuperTrend 线值，多头时可作为跟踪止损参考
    tf              : 周期标签，决定止损缓冲
    wider_levels    : 更大周期的 levels，用于寻找最终止损（可选）

    返回 dict；数据不足时相应字段为 None 并在 notes 说明。
    """
    out = {
        "entry_ref": _round_price(price) if price else None,
        "entry_basis": "参考入场 = 当前价（本工具不判断入场时机，仅提供几何口径）",
        "stops": [],
        "targets": [],
        "rr": {},
        "notes": [],
    }
    if not price or price <= 0:
        out["notes"].append("现价缺失，无法计算交易几何")
        return out

    buf = STOP_BUFFER.get(tf, 0.005)
    supports = [s for s in (levels or {}).get("supports", []) if s.get("price", 0) < price]
    resistances = [r for r in (levels or {}).get("resistances", []) if r.get("price", 0) > price]

    # ---------- 结构止损：最近的支撑（或 SuperTrend 线，取更靠近价格者） ----------
    struct_candidates = []
    if supports:
        nearest = max(supports, key=lambda s: s["price"])  # 最靠近价格的支撑
        struct_candidates.append((nearest["price"], nearest.get("basis", "支撑")))
    if supertrend_line and 0 < supertrend_line < price:
        struct_candidates.append((supertrend_line, "SuperTrend 线"))

    struct_stop = None
    if struct_candidates:
        # 取最高者：跌破最近的结构位即视为结构破坏，止损更紧、亏损更小
        lvl, basis = max(struct_candidates, key=lambda x: x[0])
        struct_stop = lvl * (1 - buf)
        out["stops"].append({
            "name": "结构止损",
            "price": _round_price(struct_stop),
            "level": _round_price(lvl),
            "basis": f"{basis} 下方 {buf * 100:.1f}%",
            "distance_pct": _pct(struct_stop, price),
        })
    else:
        out["notes"].append("本周期无有效支撑且 SuperTrend 未在价格下方，无法给出结构止损")

    # ---------- 最终止损：更下方的支撑（本周期次级支撑，或更大周期首个支撑） ----------
    final_candidates = []
    ref = struct_stop if struct_stop else price
    for s in supports:
        if s["price"] < ref * (1 - buf):
            final_candidates.append((s["price"], s.get("basis", "支撑")))
    for s in (wider_levels or {}).get("supports", []):
        if 0 < s.get("price", 0) < ref * (1 - buf):
            final_candidates.append((s["price"], f"{s.get('basis', '支撑')}（大周期）"))

    if final_candidates:
        lvl, basis = max(final_candidates, key=lambda x: x[0])
        final_stop = lvl * (1 - buf)
        out["stops"].append({
            "name": "最终止损",
            "price": _round_price(final_stop),
            "level": _round_price(lvl),
            "basis": f"{basis} 下方 {buf * 100:.1f}%",
            "distance_pct": _pct(final_stop, price),
        })

    # ---------- 分批止盈：三档阻力 ----------
    for i, r in enumerate(sorted(resistances, key=lambda x: x["price"])[:3]):
        out["targets"].append({
            "name": f"TP{i + 1}",
            "price": _round_price(r["price"]),
            "basis": r.get("basis", "阻力"),
            "distance_pct": _pct(r["price"], price),
            "plan": TP_PLAN[i] if i < len(TP_PLAN) else "移动止盈",
        })
    if not out["targets"]:
        out["notes"].append("本周期价格上方无有效阻力位，无法给出分批止盈目标")

    # ---------- 风险回报比 ----------
    if out["stops"] and out["targets"]:
        stop_price = out["stops"][0]["price"]
        risk = price - stop_price
        if risk > 0:
            for t in out["targets"]:
                reward = t["price"] - price
                if reward > 0:
                    out["rr"][t["name"]] = round(reward / risk, 2)
            out["rr_basis"] = (
                f"口径：入场 {_round_price(price)} / 结构止损 {stop_price} "
                f"（风险 {risk / price * 100:.2f}%）/ 目标见 TP 各档"
            )
            # 用最终止损再给一个更保守的 RR
            if len(out["stops"]) > 1:
                risk2 = price - out["stops"][1]["price"]
                if risk2 > 0 and out["targets"]:
                    tp1_reward = out["targets"][0]["price"] - price
                    if tp1_reward > 0:
                        out["rr"]["TP1（最终止损口径）"] = round(tp1_reward / risk2, 2)
        else:
            out["notes"].append("止损位不低于入场价，风险回报比无意义")

    return out
