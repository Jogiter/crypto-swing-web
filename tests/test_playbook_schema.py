# -*- coding: utf-8 -*-
"""契约校验：既要放行真实数据，也要拦住各类结构错误。"""
import copy
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from analyzer.playbook_schema import SCHEMA_VERSION, final_stop, validate  # noqa: E402

FIXTURE = pathlib.Path(__file__).resolve().parent.parent / "docs" / "data" / "playbook.json"


@pytest.fixture
def plan():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _errs(data):
    ok, errs = validate(data)
    assert not ok, "本应校验失败却通过了"
    return " ".join(errs)


# ---------------- 真实数据必须放行 ----------------

def test_shipped_example_is_valid(plan):
    ok, errs = validate(plan)
    assert ok, f"随仓库发布的示例不符合契约：{errs}"


def test_example_covers_all_three_coins(plan):
    assert set(plan["coins"]) == {"BTC", "ETH", "SOL"}


def test_example_exercises_optional_fields(plan):
    """示例要覆盖可选字段，否则契约的这些分支从未被验证过。"""
    assert plan["coins"]["SOL"]["bias_note"]            # 方向偏好的修饰
    assert plan["coins"]["ETH"]["rr_note"]              # RR 补充说明
    assert any(b.get("ideal") for b in plan["coins"]["BTC"]["entry_batches"])
    assert any(r.get("verdict") == "not_recommended"
               for r in plan["coins"]["ETH"]["risk_reward"])
    assert plan["add_conditions"]["partial_note"]


def test_final_stop_takes_last_level(plan):
    assert final_stop(plan["coins"]["BTC"]) == 64284
    assert final_stop(plan["coins"]["SOL"]) == 81.0
    assert final_stop({"stops": []}) is None


# ---------------- 结构错误必须拦住 ----------------

def test_wrong_schema_version(plan):
    plan["schema_version"] = "0.9"
    assert SCHEMA_VERSION in _errs(plan)


def test_bad_timestamp(plan):
    plan["generated_at_utc"] = "2026/08/26 01:22"
    assert "ISO" in _errs(plan)


def test_missing_price_anchor(plan):
    del plan["price_anchor"]
    assert "price_anchor" in _errs(plan)


def test_anchor_missing_a_coin_that_has_a_plan(plan):
    del plan["price_anchor"]["SOL"]
    assert "SOL" in _errs(plan)


def test_entry_range_reversed(plan):
    b = plan["coins"]["BTC"]["entry_batches"][0]
    b["low"], b["high"] = b["high"], b["low"]
    assert "区间写反" in _errs(plan)


def test_entry_weights_must_total_100(plan):
    plan["coins"]["BTC"]["entry_batches"][0]["weight_pct"] = 10
    assert "合计" in _errs(plan)


def test_target_reductions_must_total_100_with_trailing(plan):
    plan["coins"]["BTC"]["trailing"]["remain_pct"] = 5
    assert "仓位没分完" in _errs(plan)


def test_stop_without_action_rejected(plan):
    del plan["coins"]["BTC"]["stops"][0]["action"]
    assert "action" in _errs(plan)


def test_stop_levels_must_ascend(plan):
    plan["coins"]["BTC"]["stops"][1]["level"] = 1
    assert "未递增" in _errs(plan)


def test_rr_ratio_must_match_its_own_numbers(plan):
    plan["coins"]["BTC"]["risk_reward"][0]["ratio"] = 99.0
    assert "不符" in _errs(plan)


def test_rr_stop_above_entry_rejected(plan):
    rr = plan["coins"]["BTC"]["risk_reward"][0]
    rr["stop"] = rr["entry"] + 1
    assert "做多方案不成立" in _errs(plan)


def test_unknown_confidence_level(plan):
    plan["coins"]["BTC"]["confidence"] = "很高"
    assert "confidence" in _errs(plan)


def test_position_pct_out_of_range(plan):
    plan["coins"]["ETH"]["position_pct"] = 150
    assert "position_pct" in _errs(plan)


def test_unknown_coin_rejected(plan):
    plan["coins"]["DOGE"] = copy.deepcopy(plan["coins"]["BTC"])
    assert "未知币种" in _errs(plan)


def test_missing_bias(plan):
    del plan["coins"]["BTC"]["bias"]
    assert "bias" in _errs(plan)


def test_non_dict_input():
    ok, errs = validate([1, 2, 3])
    assert not ok and "JSON 对象" in errs[0]


def test_empty_coins(plan):
    plan["coins"] = {}
    assert "coins" in _errs(plan)


def test_rounding_tolerance_accepted(plan):
    """99 与 100 的差属四舍五入，不该判不合格。"""
    plan["coins"]["BTC"]["entry_batches"][0]["weight_pct"] = 39.5
    plan["coins"]["BTC"]["entry_batches"][1]["weight_pct"] = 40.5
    ok, _ = validate(plan)
    assert ok
