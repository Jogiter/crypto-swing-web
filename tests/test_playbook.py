# -*- coding: utf-8 -*-
"""机械版做多方案：规则必须确定、可解释、边界安全。"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from analyzer.playbook import BASE_POSITION, TARGET_ATR_PCT, build_playbook  # noqa: E402

LEVELS = {
    "supports": [{"price": 95000.0, "basis": "前低"}, {"price": 92000.0, "basis": "EMA50"}],
    "resistances": [{"price": 105000.0, "basis": "前高"}, {"price": 110000.0, "basis": "整数关口"}],
}


def _frame(total=60.0, adx=20.0, st="多", line=96000.0, close=100000.0):
    return {
        "close": close,
        "levels": LEVELS,
        "score": {"total": total, "signal": "观望",
                  "adx": {"value": adx},
                  "supertrend": {"direction": st, "line": line}},
    }


def _geo():
    return {"entry_ref": 100000.0,
            "stops": [{"name": "结构止损", "price": 94620.0, "basis": "前低 下方 0.4%"},
                      {"name": "最终止损", "price": 91632.0, "basis": "EMA50 下方 0.4%"}]}


def _build(**kw):
    kw.setdefault("frame4h", _frame())
    kw.setdefault("geometry", _geo())
    kw.setdefault("regime", {"key": "neutral"})
    kw.setdefault("atr_pct", 2.0)
    kw.setdefault("atr_abs", 2000.0)
    return build_playbook(**kw)


# ---------------- 信心等级 ----------------

def test_confidence_is_deterministic():
    a, b = _build(), _build()
    assert a["confidence"] == b["confidence"]


@pytest.mark.parametrize("total,expect", [(80.0, "高"), (65.0, "中高"), (50.0, "中"), (20.0, "低")])
def test_confidence_levels_from_score(total, expect):
    pb = _build(frame4h=_frame(total=total, adx=10.0))
    assert pb["confidence"]["level"] == expect


def test_regime_shifts_confidence_both_ways():
    base = _build(frame4h=_frame(total=60.0, adx=10.0))["confidence"]["score"]
    up = _build(frame4h=_frame(total=60.0, adx=10.0), regime={"key": "risk_on"})["confidence"]["score"]
    down = _build(frame4h=_frame(total=60.0, adx=10.0), regime={"key": "decouple_down"})["confidence"]["score"]
    assert up == base + 10
    assert down == base - 15


def test_strong_trend_with_short_supertrend_penalised():
    long_ = _build(frame4h=_frame(total=60.0, adx=30.0, st="多"))["confidence"]["score"]
    short = _build(frame4h=_frame(total=60.0, adx=30.0, st="空"))["confidence"]["score"]
    assert long_ == 65.0 and short == 52.0


def test_weak_trend_skips_supertrend_adjustment():
    """ADX < 25 时不做趋势确认加减。"""
    pb = _build(frame4h=_frame(total=60.0, adx=15.0, st="空"))
    assert pb["confidence"]["score"] == 60.0
    assert "SuperTrend" not in pb["confidence"]["basis"]


def test_confidence_clamped_to_0_100():
    hi = _build(frame4h=_frame(total=100.0, adx=30.0, st="多"), regime={"key": "risk_on"})
    lo = _build(frame4h=_frame(total=0.0, adx=30.0, st="空"), regime={"key": "decouple_down"})
    assert hi["confidence"]["score"] == 100.0
    assert lo["confidence"]["score"] == 0.0


def test_confidence_basis_is_explainable():
    pb = _build(frame4h=_frame(total=60.0, adx=30.0, st="多"), regime={"key": "risk_on"})
    b = pb["confidence"]["basis"]
    assert "4H 评分 60.0" in b and "行情性质 +10" in b and "SuperTrend 多 +5" in b


# ---------------- 仓位 ----------------

def test_position_baseline_at_reference_volatility():
    pb = _build(frame4h=_frame(total=80.0, adx=10.0), atr_pct=TARGET_ATR_PCT)
    assert pb["position"]["pct"] == BASE_POSITION["高"]


def test_high_volatility_cuts_position():
    calm = _build(frame4h=_frame(total=80.0, adx=10.0), atr_pct=2.0)["position"]["pct"]
    wild = _build(frame4h=_frame(total=80.0, adx=10.0), atr_pct=4.0)["position"]["pct"]
    assert wild < calm
    assert wild == pytest.approx(BASE_POSITION["高"] * 0.5, abs=0.1)


def test_volatility_adjustment_is_capped():
    """极低波动不得把仓位放大到无限。"""
    pb = _build(frame4h=_frame(total=80.0, adx=10.0), atr_pct=0.01)
    assert pb["position"]["pct"] <= 30.0


def test_position_without_volatility_data():
    pb = _build(frame4h=_frame(total=80.0, adx=10.0), atr_pct=None)
    assert pb["position"]["pct"] == BASE_POSITION["高"]
    assert "未调整" in pb["position"]["basis"]


# ---------------- 入场区间 ----------------

def test_entry_zones_ordered_and_below_price():
    pb = _build()
    zones = pb["entry_zones"]
    assert len(zones) == 3
    assert zones[0]["high"] == 100000
    for z in zones:
        assert z["low"] < z["high"] or z["low"] == z["high"]
    assert zones[1]["high"] < zones[0]["high"]
    assert "理想加仓位" in zones[1]["note"]


def test_entry_zones_ignore_levels_above_price():
    frame = _frame()
    frame["levels"] = {"supports": [{"price": 120000.0, "basis": "错位"}], "resistances": []}
    pb = _build(frame4h=frame)
    assert len(pb["entry_zones"]) == 1      # 只剩现价档


# ---------------- 加仓条件 ----------------

def test_add_conditions_from_own_signals():
    pb = _build(frame4h=_frame(total=55.0, st="空", line=101000.0))
    conds = [c["condition"] for c in pb["add_conditions"]["items"]]
    assert any("评分 ≥ 70" in c for c in conds)
    assert any("SuperTrend 翻多" in c for c in conds)
    assert any("第一阻力" in c for c in conds)
    assert pb["add_conditions"]["require"] == "全部满足"


def test_macro_conditions_are_labelled_and_only_unmet():
    macro = [{"condition": "BTC ETF 连续 3 日净流入", "met": False, "gap": "还需 2 日转正"},
             {"condition": "已满足的条件", "met": True}]
    pb = _build(macro_conditions=macro)
    conds = [c["condition"] for c in pb["add_conditions"]["items"]]
    assert any(c.startswith("[市场级]") for c in conds)
    assert not any("已满足的条件" in c for c in conds)


def test_score_above_threshold_drops_that_condition():
    pb = _build(frame4h=_frame(total=75.0, st="多"))
    conds = [c["condition"] for c in pb["add_conditions"]["items"]]
    assert not any("评分 ≥ 70" in c for c in conds)


# ---------------- 失效位与降级 ----------------

def test_invalidation_uses_final_stop():
    pb = _build()
    assert pb["invalidation"]["price"] == 91632.0
    assert "最终止损" in pb["invalidation"]["basis"]


def test_missing_stops_recorded_in_notes():
    pb = _build(geometry={"entry_ref": 100000.0, "stops": []})
    assert "invalidation" not in pb
    assert any("失效价缺失" in n for n in pb["notes"])


def test_missing_score_degrades_safely():
    pb = _build(frame4h={"close": 100.0, "levels": LEVELS, "score": {}})
    assert "confidence" not in pb
    assert any("无法给出机械方案" in n for n in pb["notes"])


def test_weekly_anchor_passthrough():
    pb = _build(weekly_anchor={"name": "周线 200 周均线", "price": 64669.0, "ratio": 1.219})
    assert pb["structural_anchor"]["price"] == 64669.0


def test_layer_and_disclaimer_present():
    pb = _build()
    assert pb["layer"] == "mechanical"
    assert "不含主观判断" in pb["disclaimer"]


def test_condition_prices_are_thousands_formatted():
    """条件文案里的价格要带千分位与 $，不能是裸浮点。"""
    pb = _build(frame4h=_frame(total=55.0, st="空", line=101000.0))
    text = " ".join(c["condition"] + " " + (c.get("gap") or "")
                    for c in pb["add_conditions"]["items"])
    assert "$101,000" in text
    assert "$105,000" in text
    assert "101000.0" not in text and "105000.0" not in text
