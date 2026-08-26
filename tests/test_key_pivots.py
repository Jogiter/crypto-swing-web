# -*- coding: utf-8 -*-
"""关键翻多线 / 结构生死线：周线级两个决定性价位。"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from analyzer.levels import key_pivots  # noqa: E402


def _weekly(st_line=None, ma200w=None, ema20w=None):
    f = {"score": {"supertrend": {"line": st_line}}} if st_line else {"score": {"supertrend": {}}}
    if ma200w:
        f["ma200w"] = ma200w
    if ema20w:
        f["ema20w"] = ema20w
    return f


def test_btc_shape_supertrend_above_ma200w_below():
    """线上 BTC 真实形态：周线 ST 在上方，200 周均线在下方。"""
    kp = key_pivots(_weekly(st_line=79300.52, ma200w=64671.0, ema20w=71000.0), 79092.2)
    assert kp["flip_long"]["price"] == 79301
    assert kp["flip_long"]["basis"] == "周线 SuperTrend"
    assert kp["structural_line"]["price"] == 64671
    assert kp["structural_line"]["basis"] == "200 周均线"


def test_eth_shape_supertrend_below_ma200w_above():
    """线上 ETH 真实形态：周线 ST 已翻多在下方，200 周均线仍在上方——
    此时翻多线应是 200 周均线，与 skill 报告的判断一致。"""
    kp = key_pivots(_weekly(st_line=1643.93, ma200w=2493.0, ema20w=2039.0), 2464.77)
    assert kp["flip_long"]["basis"] == "200 周均线"
    assert kp["flip_long"]["price"] == 2493
    # 下方最低者为最后防线
    assert kp["structural_line"]["price"] == 1644


def test_flip_long_picks_nearest_above_not_highest():
    kp = key_pivots(_weekly(st_line=110000.0, ma200w=105000.0), 100000.0)
    assert kp["flip_long"]["price"] == 105000


def test_structural_line_picks_lowest_below_not_nearest():
    kp = key_pivots(_weekly(st_line=95000.0, ma200w=60000.0, ema20w=80000.0), 100000.0)
    assert kp["structural_line"]["price"] == 60000


def test_distance_pct_signs():
    kp = key_pivots(_weekly(st_line=110000.0, ma200w=90000.0), 100000.0)
    assert kp["flip_long"]["distance_pct"] == 10.0
    assert kp["structural_line"]["distance_pct"] == -10.0


def test_all_above_yields_no_structural_line():
    kp = key_pivots(_weekly(st_line=110000.0, ma200w=105000.0), 100000.0)
    assert "structural_line" not in kp


def test_all_below_yields_no_flip_long():
    kp = key_pivots(_weekly(st_line=90000.0, ma200w=60000.0), 100000.0)
    assert "flip_long" not in kp


def test_no_weekly_inputs_yields_empty():
    assert key_pivots({"score": {}}, 100000.0) == {}


def test_only_ema20w_available():
    kp = key_pivots(_weekly(ema20w=95000.0), 100000.0)
    assert kp["structural_line"]["basis"] == "20 周 EMA"
    assert "flip_long" not in kp
