# -*- coding: utf-8 -*-
"""交易几何的纯计算测试（不触网）。"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from analyzer.geometry import STOP_BUFFER, trade_geometry  # noqa: E402

LEVELS = {
    "supports": [
        {"price": 95000.0, "basis": "前低"},
        {"price": 92000.0, "basis": "EMA50"},
        {"price": 88000.0, "basis": "整数关口"},
    ],
    "resistances": [
        {"price": 105000.0, "basis": "前高"},
        {"price": 110000.0, "basis": "整数关口"},
        {"price": 118000.0, "basis": "EMA200"},
    ],
}
PRICE = 100000.0
BUF = STOP_BUFFER["4h"]


def test_structure_stop_uses_nearest_support():
    g = trade_geometry(PRICE, LEVELS, supertrend_line=None, tf="4h")
    stop = g["stops"][0]
    assert stop["name"] == "结构止损"
    # 最近支撑 95000，止损设其下方 buffer
    assert stop["level"] == 95000
    assert abs(stop["price"] - 95000 * (1 - BUF)) < 1e-6
    assert stop["distance_pct"] < 0


def test_supertrend_wins_when_closer_than_support():
    """SuperTrend 线比最近支撑更靠近价格时，应作为结构止损依据。"""
    g = trade_geometry(PRICE, LEVELS, supertrend_line=97000.0, tf="4h")
    assert g["stops"][0]["level"] == 97000
    assert "SuperTrend" in g["stops"][0]["basis"]


def test_supertrend_above_price_is_ignored():
    """SuperTrend 在价格上方（空头）时不可作为做多止损。"""
    g = trade_geometry(PRICE, LEVELS, supertrend_line=103000.0, tf="4h")
    assert g["stops"][0]["level"] == 95000


def test_final_stop_is_below_structure_stop():
    g = trade_geometry(PRICE, LEVELS, tf="4h")
    assert len(g["stops"]) == 2
    assert g["stops"][1]["name"] == "最终止损"
    assert g["stops"][1]["price"] < g["stops"][0]["price"]
    assert g["stops"][1]["level"] == 92000


def test_targets_are_ascending_with_plan():
    g = trade_geometry(PRICE, LEVELS, tf="4h")
    prices = [t["price"] for t in g["targets"]]
    assert prices == sorted(prices) and len(prices) == 3
    assert [t["name"] for t in g["targets"]] == ["TP1", "TP2", "TP3"]
    assert g["targets"][0]["plan"] == "减 1/3"
    assert all(t["distance_pct"] > 0 for t in g["targets"])


def test_risk_reward_math():
    g = trade_geometry(PRICE, LEVELS, tf="4h")
    stop = g["stops"][0]["price"]
    risk = PRICE - stop
    expected_tp1 = round((105000.0 - PRICE) / risk, 2)
    assert g["rr"]["TP1"] == expected_tp1
    # 更远的目标赔率更高
    assert g["rr"]["TP3"] > g["rr"]["TP2"] > g["rr"]["TP1"]
    # 按最终止损的 RR 更保守
    assert g["rr"]["TP1（最终止损口径）"] < g["rr"]["TP1"]
    assert "口径" in g["rr_basis"]


def test_no_supports_yields_note_not_crash():
    g = trade_geometry(PRICE, {"supports": [], "resistances": LEVELS["resistances"]}, tf="4h")
    assert g["stops"] == []
    assert g["rr"] == {}
    assert any("无法给出结构止损" in n for n in g["notes"])


def test_no_resistances_yields_note():
    g = trade_geometry(PRICE, {"supports": LEVELS["supports"], "resistances": []}, tf="4h")
    assert g["targets"] == []
    assert any("无有效阻力" in n for n in g["notes"])


def test_missing_price_is_safe():
    g = trade_geometry(None, LEVELS, tf="4h")
    assert g["entry_ref"] is None
    assert any("现价缺失" in n for n in g["notes"])


def test_wider_levels_used_for_final_stop():
    """本周期只有一个支撑时，最终止损应回退到大周期支撑。"""
    narrow = {"supports": [{"price": 95000.0, "basis": "前低"}], "resistances": LEVELS["resistances"]}
    wider = {"supports": [{"price": 85000.0, "basis": "日线前低"}], "resistances": []}
    g = trade_geometry(PRICE, narrow, tf="4h", wider_levels=wider)
    assert len(g["stops"]) == 2
    assert g["stops"][1]["level"] == 85000
    assert "大周期" in g["stops"][1]["basis"]


def test_levels_at_or_above_price_are_not_supports():
    """价格上下方的分类必须严格。"""
    weird = {
        "supports": [{"price": 101000.0, "basis": "错位"}, {"price": 99000.0, "basis": "真支撑"}],
        "resistances": [{"price": 99500.0, "basis": "错位"}, {"price": 106000.0, "basis": "真阻力"}],
    }
    g = trade_geometry(PRICE, weird, tf="4h")
    assert g["stops"][0]["level"] == 99000
    assert [t["price"] for t in g["targets"]] == [106000]
