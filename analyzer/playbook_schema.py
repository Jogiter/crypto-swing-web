# -*- coding: utf-8 -*-
"""playbook.json 的契约定义与校验。

这份文件就是「契约」本身——不是一篇说明文档，而是可执行的规则：
分析侧按它产出，页面按它读取，两边对不上时这里会报错。

设计取舍：
* 所有价格是**数字**而非字符串。报告里写「76,500 – 75,500」是给人看的，
  程序要的是 {"low": 75500, "high": 76500}，否则得去猜破折号和千分位。
* 自由文本一律放进 *_note / basis 字段，不参与任何计算。措辞怎么变都不影响渲染。
* 校验只拦「结构错」和「算不通」，不判断行情观点对错——那不是程序的事。
"""
import datetime as dt

SCHEMA_VERSION = "1.0"

COINS = ("BTC", "ETH", "SOL")
CONFIDENCE_LEVELS = ("高", "中高", "中", "低")
RR_VERDICTS = (None, "optimal", "not_recommended")

# 允许的合计误差（百分比）——报告里可能写 30/45/25 这类正好 100 的，
# 但也可能因为四舍五入差一点，不必为此判不合格。
PCT_TOLERANCE = 1.0


def _err(errors, path, msg):
    errors.append(f"{path}: {msg}")


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _check_batches(batches, path, errors):
    if not batches:
        return
    total = 0.0
    for i, b in enumerate(batches):
        p = f"{path}[{i}]"
        lo, hi, w = b.get("low"), b.get("high"), b.get("weight_pct")
        if not _num(lo) or not _num(hi):
            _err(errors, p, "low/high 必须是数字")
            continue
        if lo > hi:
            _err(errors, p, f"low({lo}) 大于 high({hi})，区间写反了")
        if not _num(w):
            _err(errors, p, "weight_pct 必须是数字")
        else:
            total += w
    if batches and abs(total - 100.0) > PCT_TOLERANCE:
        _err(errors, path, f"各批 weight_pct 合计 {total}，应为 100（占计划仓的比例）")


def _check_stops(stops, path, errors):
    prev_level = 0
    for i, s in enumerate(stops or []):
        p = f"{path}[{i}]"
        if not _num(s.get("price")):
            _err(errors, p, "price 必须是数字")
        lvl = s.get("level")
        if not isinstance(lvl, int):
            _err(errors, p, "level 必须是整数（1 = 最先触发的那层）")
        elif lvl <= prev_level:
            _err(errors, p, f"level {lvl} 未递增（上一层 {prev_level}）")
        else:
            prev_level = lvl
        if not s.get("action"):
            _err(errors, p, "缺 action——止损位没有对应动作，读的人不知道该减多少")


def _check_targets(targets, trailing, path, errors):
    total = 0.0
    for i, t in enumerate(targets or []):
        p = f"{path}[{i}]"
        if not _num(t.get("price")):
            _err(errors, p, "price 必须是数字")
        r = t.get("reduce_pct")
        if not _num(r):
            _err(errors, p, "reduce_pct 必须是数字")
        else:
            total += r
    remain = (trailing or {}).get("remain_pct")
    if _num(remain):
        total += remain
    if targets and abs(total - 100.0) > PCT_TOLERANCE:
        _err(errors, path,
             f"各档 reduce_pct 与 trailing.remain_pct 合计 {total}，应为 100（否则仓位没分完）")


def _check_rr(items, path, errors):
    for i, r in enumerate(items or []):
        p = f"{path}[{i}]"
        for k in ("entry", "stop", "target", "ratio"):
            if not _num(r.get(k)):
                _err(errors, p, f"{k} 必须是数字")
        if r.get("verdict") not in RR_VERDICTS:
            _err(errors, p, f"verdict 只能是 {RR_VERDICTS}")
        e, s, t, ratio = r.get("entry"), r.get("stop"), r.get("target"), r.get("ratio")
        if all(_num(x) for x in (e, s, t, ratio)):
            if s >= e:
                _err(errors, p, f"stop({s}) 不低于 entry({e})，做多方案不成立")
            elif t <= e:
                _err(errors, p, f"target({t}) 不高于 entry({e})")
            else:
                calc = (t - e) / (e - s)
                if abs(calc - ratio) > max(0.05, calc * 0.02):
                    _err(errors, p, f"ratio {ratio} 与入场/止损/目标算出的 {calc:.2f} 不符")


def validate(data):
    """返回 (ok, errors)。errors 为人类可读的问题列表。"""
    errors = []
    if not isinstance(data, dict):
        return False, ["顶层必须是 JSON 对象"]

    if data.get("schema_version") != SCHEMA_VERSION:
        _err(errors, "schema_version",
             f"应为 {SCHEMA_VERSION}，实际 {data.get('schema_version')!r}")

    ts = data.get("generated_at_utc")
    if not isinstance(ts, str):
        _err(errors, "generated_at_utc", "缺失或不是字符串")
    else:
        try:
            dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except ValueError:
            _err(errors, "generated_at_utc", f"不是合法 ISO 时间：{ts!r}")

    anchor = data.get("price_anchor")
    if not isinstance(anchor, dict) or not anchor:
        _err(errors, "price_anchor", "缺失——没有它就无法判断方案是否已被价格走离")
    else:
        for c, v in anchor.items():
            if not _num(v) or v <= 0:
                _err(errors, f"price_anchor.{c}", f"必须是正数，实际 {v!r}")

    coins = data.get("coins")
    if not isinstance(coins, dict) or not coins:
        _err(errors, "coins", "缺失或为空")
        return not errors, errors

    for coin, cd in coins.items():
        path = f"coins.{coin}"
        if coin not in COINS:
            _err(errors, path, f"未知币种（支持 {COINS}）")
        if not isinstance(cd, dict):
            _err(errors, path, "必须是对象")
            continue
        if not cd.get("bias"):
            _err(errors, path, "缺 bias（方向偏好）")
        conf = cd.get("confidence")
        if conf not in CONFIDENCE_LEVELS:
            _err(errors, f"{path}.confidence", f"应为 {CONFIDENCE_LEVELS} 之一，实际 {conf!r}")
        pos = cd.get("position_pct")
        if not _num(pos) or not (0 < pos <= 100):
            _err(errors, f"{path}.position_pct", f"应为 0–100 的数字，实际 {pos!r}")
        if isinstance(anchor, dict) and coin not in anchor:
            _err(errors, f"price_anchor.{coin}", "该币种有方案但缺锚定价")

        _check_batches(cd.get("entry_batches"), f"{path}.entry_batches", errors)
        _check_stops(cd.get("stops"), f"{path}.stops", errors)
        _check_targets(cd.get("targets"), cd.get("trailing"), f"{path}.targets", errors)
        _check_rr(cd.get("risk_reward"), f"{path}.risk_reward", errors)

    return not errors, errors


def final_stop(coin_plan):
    """取最后一层止损价——方案是否已被击穿以它为准。"""
    stops = [s for s in (coin_plan.get("stops") or []) if _num(s.get("price"))]
    if not stops:
        return None
    return max(stops, key=lambda s: s.get("level", 0))["price"]
