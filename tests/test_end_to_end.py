# -*- coding: utf-8 -*-
"""端到端：mock 掉全部网络，跑完整 run()，验证接线与产物。"""
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import pytest  # noqa: E402

from analyzer import main as main_mod  # noqa: E402
from analyzer import sources  # noqa: E402


def _synth_df(n=320, start=90000.0, seed=7):
    """合成一段有趋势+噪声的 K 线，保证指标可计算。"""
    rng = np.random.default_rng(seed)
    steps = rng.normal(0.0008, 0.012, n)
    close = start * np.exp(np.cumsum(steps))
    high = close * (1 + np.abs(rng.normal(0, 0.004, n)))
    low = close * (1 - np.abs(rng.normal(0, 0.004, n)))
    open_ = np.concatenate([[close[0]], close[:-1]])
    vol = np.abs(rng.normal(1000, 200, n))
    t = pd.date_range("2024-01-01", periods=n, freq="4h", tz="UTC")
    return pd.DataFrame({"time": t, "open": open_, "high": high,
                         "low": low, "close": close, "volume": vol})


@pytest.fixture
def patched(monkeypatch, tmp_path):
    monkeypatch.setattr(main_mod, "ROOT", tmp_path)
    monkeypatch.setattr(sources, "fetch_klines",
                        lambda coin, tf: (_synth_df(), f"MockSource-{tf}"))
    monkeypatch.setattr(sources, "coingecko_spot",
                        lambda coin: {"price": 100000.0, "change_24h": 1.5})
    monkeypatch.setattr(sources, "fear_greed", lambda: {
        "value": 30, "label": "Fear", "history": [{"value": 30, "ts": 1}, {"value": 18, "ts": 2}]})
    monkeypatch.setattr(sources, "us_indices", lambda: {
        "SP500": {"last": 6000.0, "change_1d_pct": 0.4, "change_5d_pct": 1.5}})
    monkeypatch.setattr(sources, "etf_flows", lambda: {
        "BTC": [{"date": "23 Aug", "total_musd": -120.0},
                {"date": "24 Aug", "total_musd": -45.0},
                {"date": "25 Aug", "total_musd": -30.0}]})
    monkeypatch.setattr(sources, "mvrv_btc", lambda: {
        "value": 2.1, "date": "2026-08-24", "zone": "中性区",
        "percentile": 74.5, "history_days": 5800, "zscore": 2.4, "zscore_zone": "中性区"})
    monkeypatch.setattr(sources, "cbbi", lambda: {
        "value": 68.0, "date": "2026-08-24", "zone": "中性区(30-70)",
        "value_30d_ago": 61.0, "change_30d": 7.0})
    return tmp_path


def test_run_produces_all_artifacts(patched):
    d = main_mod.run()
    root = patched
    assert (root / "docs" / "data" / "latest.json").exists()
    assert (root / "docs" / "data" / "index.json").exists()
    assert (root / "reports" / f"{d['date']}.md").exists()
    assert (root / "docs" / "reports" / f"{d['date']}.md").exists()


def test_run_wires_new_features(patched):
    d = main_mod.run()
    # CBBI 进入周期估值
    assert d["btc_cycle"]["cbbi"]["value"] == 68.0
    assert d["btc_cycle"]["mvrv"]["zscore"] == 2.4
    # 每个币种都有交易几何
    for coin in ["BTC", "ETH", "SOL"]:
        g = d["coins"][coin]["geometry"]
        assert g["entry_ref"] is not None
        assert g["stops"], f"{coin} 缺结构止损"
        assert g["targets"], f"{coin} 缺止盈目标"
    # 前瞻条件已生成
    tc = d["trigger_conditions"]
    assert tc["offense"] and tc["defense"]
    # ETF 连续三日流出应被判定为已满足
    out = next(c for c in tc["defense"] if "净流出" in c["condition"])
    assert out["met"] is True


def test_run_markdown_contains_new_sections(patched):
    d = main_mod.run()
    md = (patched / "reports" / f"{d['date']}.md").read_text(encoding="utf-8")
    assert "交易几何（纯计算 · 非入场建议）" in md
    assert "触发阈值（前瞻条件" in md
    assert "**CBBI**：68.0" in md
    assert "MVRV Z-Score 2.4" in md
    assert "历史分位 **74.5%**" in md


def test_json_is_serialisable_and_reloadable(patched):
    d = main_mod.run()
    raw = (patched / "docs" / "data" / "latest.json").read_text(encoding="utf-8")
    reloaded = json.loads(raw)
    assert reloaded["date"] == d["date"]
    assert "trigger_conditions" in reloaded
    assert reloaded["coins"]["BTC"]["geometry"]["stops"]


def test_missing_optional_sources_do_not_break_run(patched, monkeypatch):
    """CBBI / MVRV / ETF 全部失败时，run() 仍应完成并记录缺失。"""
    def boom(*a, **k):
        raise RuntimeError("network down")

    monkeypatch.setattr(sources, "cbbi", boom)
    monkeypatch.setattr(sources, "mvrv_btc", boom)
    monkeypatch.setattr(sources, "etf_flows", boom)
    d = main_mod.run()
    assert any("CBBI" in m for m in d["missing"])
    assert any("MVRV" in m for m in d["missing"])
    assert "cbbi" not in d["btc_cycle"]
    # 几何仍在（不依赖这些源）
    assert d["coins"]["BTC"]["geometry"]["stops"]
    md = (patched / "reports" / f"{d['date']}.md").read_text(encoding="utf-8")
    assert "network down" in md


def test_ma200w_shortfall_is_recorded_not_silent(patched, monkeypatch):
    """周线不足 205 根时应记入 missing，而不是静默消失。"""
    monkeypatch.setattr(sources, "fetch_klines",
                        lambda coin, tf: (_synth_df(n=60), f"Mock-{tf}"))
    d = main_mod.run()
    assert any("200周均线" in m for m in d["missing"]), d["missing"]


def test_playbook_generated_for_every_coin(patched):
    d = main_mod.run()
    for coin in ["BTC", "ETH", "SOL"]:
        pb = d["coins"][coin]["playbook"]
        assert pb["layer"] == "mechanical"
        assert pb["confidence"]["level"] in {"高", "中高", "中", "低"}
        assert pb["position"]["pct"] > 0
        assert pb["entry_zones"], f"{coin} 缺入场区间"
    # BTC 带周线结构锚
    assert d["coins"]["BTC"]["playbook"]["structural_anchor"]["name"] == "周线 200 周均线"


def test_playbook_uses_regime_and_atr(patched):
    d = main_mod.run()
    pb = d["coins"]["BTC"]["playbook"]
    assert pb["atr_pct"] is not None
    assert "行情性质" in pb["confidence"]["basis"] or d["macro"]["regime"]["key"] == "neutral"
    assert "ATR" in pb["position"]["basis"]


def test_etf_condition_reaches_playbook_as_market_level(patched):
    """ETF 流向是全市场先行灯，应作为市场级前提出现在各币加仓条件里。"""
    d = main_mod.run()
    for coin in ["BTC", "ETH", "SOL"]:
        conds = [c["condition"] for c in d["coins"][coin]["playbook"]["add_conditions"]["items"]]
        assert any(c.startswith("[市场级]") and "ETF" in c for c in conds), coin


def test_report_has_key_levels_and_playbook(patched):
    d = main_mod.run()
    md = (patched / "reports" / f"{d['date']}.md").read_text(encoding="utf-8")
    assert "## 关键位速查" in md
    assert "| 币种 | 现价 | 4H 支撑 |" in md
    assert "做多方案（机械版 · 规则派生，非主观建议）" in md
    assert "**信心等级**" in md and "**仓位权重**" in md
    assert "加仓硬条件" in md
