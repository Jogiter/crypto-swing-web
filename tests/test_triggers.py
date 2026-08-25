# -*- coding: utf-8 -*-
"""前瞻性触发条件的纯逻辑测试（不触网）。"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from analyzer.triggers import build_conditions  # noqa: E402


def _data(score=55.0, price=100000.0, st_dir="多", st_line=96000.0,
          etf=None, ma200w=45000.0, cbbi=None, mvrv=None, fg=None):
    return {
        "coins": {"BTC": {
            "price": price,
            "frames": {"4h": {
                "close": price,
                "score": {"total": score, "supertrend": {"direction": st_dir, "line": st_line}},
                "levels": {
                    "supports": [{"price": 95000.0, "basis": "前低"}],
                    "resistances": [{"price": 105000.0, "basis": "前高"}],
                },
            }},
        }},
        "btc_cycle": {"ma200w": ma200w, "price_over_ma200w": round(price / ma200w, 3) if ma200w else None,
                      "cbbi": cbbi, "mvrv": mvrv},
        "macro": {"etf_flows": {"BTC": etf} if etf else {}, "fear_greed": fg},
    }


def _find(items, keyword):
    return next((c for c in items if keyword in c["condition"]), None)


def test_etf_three_day_inflow_met():
    etf = [{"date": "d1", "total_musd": 10.0}, {"date": "d2", "total_musd": 20.0},
           {"date": "d3", "total_musd": 30.0}]
    tc = build_conditions(_data(etf=etf))
    c = _find(tc["offense"], "ETF 连续 3 日净流入")
    assert c["met"] is True and c["gap"] is None
    assert _find(tc["defense"], "ETF 连续 3 日净流出")["met"] is False


def test_etf_mixed_reports_gap():
    etf = [{"date": "d1", "total_musd": -10.0}, {"date": "d2", "total_musd": 20.0},
           {"date": "d3", "total_musd": -5.0}]
    tc = build_conditions(_data(etf=etf))
    c = _find(tc["offense"], "ETF 连续 3 日净流入")
    assert c["met"] is False and "还需 2 日转正" in c["gap"]


def test_score_gap_is_numeric_distance():
    tc = build_conditions(_data(score=62.5))
    off = _find(tc["offense"], "加权评分 ≥ 70")
    assert off["met"] is False and "还差 7.5 分" in off["gap"]
    def_ = _find(tc["defense"], "加权评分 ≤ 30")
    assert def_["met"] is False and "32.5 分空间" in def_["gap"]


def test_score_threshold_met():
    tc = build_conditions(_data(score=72.0))
    assert _find(tc["offense"], "加权评分 ≥ 70")["met"] is True


def test_supertrend_long_appears_only_in_defense():
    tc = build_conditions(_data(st_dir="多", st_line=96000.0))
    assert _find(tc["defense"], "SuperTrend 翻空") is not None
    assert _find(tc["offense"], "SuperTrend 翻多") is None


def test_supertrend_short_appears_only_in_offense():
    tc = build_conditions(_data(st_dir="空", st_line=103000.0))
    assert _find(tc["offense"], "SuperTrend 翻多") is not None
    assert _find(tc["defense"], "SuperTrend 翻空") is None


def test_level_breach_distances():
    tc = build_conditions(_data(price=100000.0))
    up = _find(tc["offense"], "站上第一阻力")
    assert "还需上涨 5.0%" in up["gap"]
    down = _find(tc["defense"], "失守第一支撑")
    assert "下跌 5.0% 即触发" in down["gap"]


def test_ma200w_not_breached_reports_distance():
    tc = build_conditions(_data(price=100000.0, ma200w=45000.0))
    c = _find(tc["defense"], "200 周均线")
    assert c["met"] is False and "需下跌 55.0%" in c["gap"]


def test_ma200w_breached_is_met():
    tc = build_conditions(_data(price=40000.0, ma200w=45000.0))
    assert _find(tc["defense"], "200 周均线")["met"] is True


def test_cbbi_and_mvrv_conditions():
    tc = build_conditions(_data(
        cbbi={"value": 92.0, "zone": "周期顶部预警(>=90)"},
        mvrv={"value": 3.4, "zone": "过热区(>3)", "percentile": 88.0, "zscore": 4.1}))
    assert _find(tc["defense"], "CBBI ≥ 90")["met"] is True
    assert _find(tc["offense"], "CBBI < 15")["met"] is False
    mv = _find(tc["defense"], "MVRV > 3")
    assert mv["met"] is True and "Z=4.1" in mv["current"] and "88.0%" in mv["current"]


def test_fear_greed_rebound_requires_prior_extreme():
    fg_no = {"value": 45, "label": "Neutral", "history": [{"value": 45}, {"value": 40}]}
    assert _find(build_conditions(_data(fg=fg_no))["offense"], "恐惧贪婪指数自极度恐惧")["met"] is False
    fg_yes = {"value": 35, "label": "Fear", "history": [{"value": 35}, {"value": 18}]}
    assert _find(build_conditions(_data(fg=fg_yes))["offense"], "恐惧贪婪指数自极度恐惧")["met"] is True


def test_missing_data_skips_conditions_without_crashing():
    tc = build_conditions({})
    assert tc == {"offense": [], "defense": []}


def test_partial_data_only_yields_available_conditions():
    tc = build_conditions({"coins": {"BTC": {"price": 100.0, "frames": {}}}, "btc_cycle": {}, "macro": {}})
    assert tc["offense"] == [] and tc["defense"] == []


def test_fear_greed_gap_distinguishes_reasons():
    """已探入极度恐惧但未回升 vs 从未探入，两种未满足原因应可区分。"""
    fg_dipped = {"value": 28, "label": "Fear", "history": [{"value": 28}, {"value": 20}]}
    c = _find(build_conditions(_data(fg=fg_dipped))["offense"], "恐惧贪婪指数自极度恐惧")
    assert c["met"] is False and "已探入极度恐惧区" in c["gap"] and "还需回升 3 点" in c["gap"]

    fg_never = {"value": 55, "label": "Greed", "history": [{"value": 55}, {"value": 50}]}
    c2 = _find(build_conditions(_data(fg=fg_never))["offense"], "恐惧贪婪指数自极度恐惧")
    assert c2["met"] is False and "尚未起步" in c2["gap"]


def test_levels_use_kline_close_not_spot_price():
    """支撑阻力源自 K 线，比较基准必须同源，否则会得出「还需上涨负数」。"""
    d = _data(price=100000.0)
    d["coins"]["BTC"]["frames"]["4h"]["close"] = 72000.0   # K 线收盘
    d["coins"]["BTC"]["price"] = 100000.0                  # 现货价（另一来源）
    tc = build_conditions(d)
    up = _find(tc["offense"], "站上第一阻力")
    # 阻力 105000 相对 K 线收盘 72000 在上方，应为正的上涨幅度
    assert "还需上涨 45.83%" in up["gap"]
    assert "72,000" in up["current"]


def test_already_above_resistance_is_met_not_negative_gap():
    d = _data(price=110000.0)
    d["coins"]["BTC"]["frames"]["4h"]["close"] = 110000.0  # 已高于阻力 105000
    tc = build_conditions(d)
    up = _find(tc["offense"], "站上第一阻力")
    assert up["met"] is True and up["gap"] is None


def test_already_below_support_is_met():
    d = _data(price=90000.0)
    d["coins"]["BTC"]["frames"]["4h"]["close"] = 90000.0   # 已低于支撑 95000
    tc = build_conditions(d)
    down = _find(tc["defense"], "失守第一支撑")
    assert down["met"] is True and down["gap"] is None


def test_ma200w_uses_weekly_close_when_available():
    d = _data(price=100000.0, ma200w=45000.0)
    d["coins"]["BTC"]["frames"]["1w"] = {"close": 46000.0}
    tc = build_conditions(d)
    c = _find(tc["defense"], "200 周均线")
    assert c["met"] is False
    assert "周线收盘 $46,000" in c["current"]
    assert "需下跌 2.2%" in c["gap"]
