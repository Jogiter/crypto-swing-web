# -*- coding: utf-8 -*-
"""报告渲染端到端测试：完整数据 / 数据缺失两种情形都不得崩溃。"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from analyzer.geometry import trade_geometry  # noqa: E402
from analyzer.report import build_report  # noqa: E402
from analyzer.triggers import build_conditions  # noqa: E402

LEVELS = {
    "supports": [{"price": 95000.0, "basis": "前低"}, {"price": 92000.0, "basis": "EMA50"}],
    "resistances": [{"price": 105000.0, "basis": "前高"}, {"price": 110000.0, "basis": "整数关口"}],
}
SCORE = {
    "srsi": {"k": 40.0, "d": 35.0, "score": 16.3, "max": 25},
    "macd": {"dif": 120.0, "dea": 100.0, "hist": 20.0, "score": 22.5, "max": 25},
    "mfi": {"value": 52.0, "score": 10.0, "max": 20},
    "volume": {"ratio_vs_ma20": 1.1, "score": 8.4, "max": 15},
    "supertrend": {"direction": "多", "line": 96000.0, "score": 15.0, "max": 15},
    "adx": {"value": 22.0, "plus_di": 25.0, "minus_di": 18.0, "trend_strength": "有趋势"},
    "total": 72.2, "signal": "做多信号",
}


def _full_data():
    frame = {"close": 100000.0, "bar_time_utc": "2026-08-25T00:00:00+00:00",
             "score": SCORE, "levels": LEVELS, "n_bars": 300}
    coin = {
        "frames": {tf: dict(frame) for tf in ["4h", "1d", "1w", "1M"]},
        "sources": {tf: "Kraken" for tf in ["4h", "1d", "1w", "1M"]},
        "spot": {"price": 100000.0, "change_24h_pct": 1.2, "source": "CoinGecko"},
        "price": 100000.0, "change_5d_pct": -4.5,
    }
    coin["geometry"] = trade_geometry(100000.0, LEVELS, 96000.0, "4h", LEVELS)
    d = {
        "generated_at_utc": "2026-08-25T00:15:00+00:00",
        "generated_at_beijing": "2026-08-25 08:15 (北京时间)",
        "date": "2026-08-25",
        "coins": {c: dict(coin) for c in ["BTC", "ETH", "SOL"]},
        "macro": {
            "fear_greed": {"value": 30, "label": "Fear",
                           "history": [{"value": 30, "ts": 1}, {"value": 18, "ts": 2}]},
            "indices": {"SP500": {"last": 6000.0, "change_1d_pct": 0.4, "change_5d_pct": 1.5},
                        "NASDAQ": {"last": 20000.0, "change_1d_pct": 0.6, "change_5d_pct": 2.0},
                        "DOW": {"last": 44000.0, "change_1d_pct": 0.1, "change_5d_pct": 0.8}},
            "etf_flows": {"BTC": [{"date": "23 Aug", "total_musd": -120.0},
                                  {"date": "24 Aug", "total_musd": 45.0},
                                  {"date": "25 Aug", "total_musd": -30.0}]},
            "regime": {"name": "加密向下脱钩", "meaning": "内部资金外流", "action": "防御",
                       "key": "decouple_down", "confidence": "规则判定",
                       "etf_note": "BTC ETF 流向反复，方向未确认",
                       "btc_5d_pct": -4.5, "spx_5d_pct": 1.5},
        },
        "btc_cycle": {
            "ma200w": 45000.0, "price_over_ma200w": 2.222,
            "power_law": {"support": 60000.0, "center": 150000.0, "top": 600000.0,
                          "position_pct": 22.2, "note": "近似拟合", "days_since_genesis": 6400},
            "mvrv": {"value": 2.1, "date": "2026-08-24", "zone": "中性区",
                     "percentile": 74.5, "history_days": 5800,
                     "zscore": 2.4, "zscore_zone": "中性区"},
            "cbbi": {"value": 68.0, "date": "2026-08-24", "zone": "中性区(30-70)",
                     "value_30d_ago": 61.0, "change_30d": 7.0},
        },
        "triggers": ["BTC 4H 加权评分 72.2 → 做多信号"],
        "missing": [],
        "disclaimer": "测试用免责声明。",
    }
    d["trigger_conditions"] = build_conditions(d)
    return d


def test_full_report_contains_new_sections():
    md = build_report(_full_data())
    # 交易几何
    assert "交易几何（纯计算 · 非入场建议）" in md
    assert "结构止损" in md and "最终止损" in md
    assert "TP1" in md and "减 1/3" in md
    assert "风险回报比" in md and "口径" in md
    # 周期估值增强
    assert "MVRV Z-Score 2.4" in md
    assert "历史分位 **74.5%**" in md
    assert "**CBBI**：68.0" in md
    assert "30日变化 +7.0" in md
    # 触发阈值
    assert "触发阈值（前瞻条件" in md
    assert "转进攻条件" in md and "转防御条件" in md
    assert "○ 未满足" in md
    # 信号灯仍在
    assert "信号灯（当前状态快照）" in md
    # 来源声明更新
    assert "CBBI" in md.split("TL;DR")[0]


def test_report_survives_missing_optional_data():
    d = _full_data()
    d["btc_cycle"] = {}
    d["trigger_conditions"] = {"offense": [], "defense": []}
    for c in d["coins"].values():
        c.pop("geometry", None)
    d["macro"]["etf_flows"] = None
    d["missing"] = ["CBBI（网络失败）", "MVRV（网络失败）"]
    md = build_report(d)
    assert "交易几何（纯计算 · 非入场建议）" not in md
    assert "触发阈值（前瞻条件" not in md
    assert "CBBI（网络失败）" in md
    assert "# 加密波段量化快照" in md


def test_geometry_notes_render_when_levels_empty():
    d = _full_data()
    for c in d["coins"].values():
        c["geometry"] = trade_geometry(100000.0, {"supports": [], "resistances": []}, None, "4h")
    md = build_report(d)
    assert "无法给出结构止损" in md


def test_disclaimer_distinguishes_geometry_from_advice():
    md = build_report(_full_data())
    assert "不判断是否应当入场" in md
    assert "纯几何计算" in md
    assert "禁止向下摊平" in md


def test_report_explains_missing_zscore():
    """Z-Score 因社区版权限缺席时，报告应说明原因而非静默省略。"""
    d = _full_data()
    d["btc_cycle"]["mvrv"] = {
        "value": 1.491, "date": "2026-08-24", "zone": "中性区",
        "percentile": 62.0, "history_days": 5800,
        "zscore_note": "社区版无市值/实现市值序列权限",
    }
    md = build_report(d)
    assert "历史分位 **62.0%**" in md
    assert "Z-Score 不可用" in md
    assert "MVRV Z-Score" not in md


def test_report_flags_fully_degraded_mvrv():
    d = _full_data()
    d["btc_cycle"]["mvrv"] = {
        "value": 1.491, "date": "2026-08-24", "zone": "中性区",
        "degraded": "全历史序列不可用，缺历史分位与 Z-Score",
    }
    md = build_report(d)
    assert "全历史序列不可用" in md
    assert "历史分位 **" not in md      # 不渲染分位数值，仅说明为何缺失
