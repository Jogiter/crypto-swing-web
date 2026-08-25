# -*- coding: utf-8 -*-
"""CBBI / MVRV 的解析与分区逻辑测试（mock HTTP，不触网）。"""
import datetime as dt
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from analyzer import sources  # noqa: E402


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


# ---------------- 分区函数 ----------------

@pytest.mark.parametrize("v,expect", [(3.5, "过热区(>3)"), (0.8, "历史底部区(<1)"), (2.0, "中性区")])
def test_mvrv_zone(v, expect):
    assert sources.mvrv_zone(v) == expect


@pytest.mark.parametrize("z,expect", [
    (8.0, "顶部泡沫区(>7)"), (4.0, "偏热(>3.5)"),
    (-0.5, "深度价值区(<0)"), (0.5, "低估区(<1)"), (2.0, "中性区"),
])
def test_mvrv_z_zone(z, expect):
    assert sources.mvrv_z_zone(z) == expect


@pytest.mark.parametrize("v,expect", [
    (95.0, "周期顶部预警(>=90)"), (75.0, "偏热(70-90)"),
    (10.0, "周期底部区(<15)"), (20.0, "偏冷(15-30)"), (50.0, "中性区(30-70)"),
])
def test_cbbi_zone(v, expect):
    assert sources.cbbi_zone(v) == expect


# ---------------- MVRV 解析 ----------------

def _cm_rows(n=100):
    """构造 CoinMetrics 风格的响应行：MVRV 线性从 1.0 升到 3.0。"""
    rows = []
    for i in range(n):
        mvrv = 1.0 + 2.0 * i / (n - 1)
        rows.append({
            "asset": "btc",
            "time": f"2026-01-{(i % 28) + 1:02d}T00:00:00.000000000Z",
            "CapMVRVCur": str(mvrv),
            "CapMrktCurUSD": str(1e12 * mvrv),
            "CapRealUSD": str(1e12),
        })
    return rows


def test_mvrv_parses_value_percentile_and_zscore(monkeypatch):
    rows = _cm_rows(100)
    monkeypatch.setattr(sources, "_get", lambda *a, **k: _Resp({"data": rows}))
    out = sources.mvrv_btc()
    assert out["value"] == 3.0
    assert out["zone"] == "中性区"          # 3.0 不 > 3
    assert out["percentile"] == 100.0       # 末值为序列最大
    assert out["history_days"] == 100
    assert out["date"] == "2026-01-16"
    # Z = (mkt - real) / std(mkt)
    assert isinstance(out["zscore"], float)
    assert out["zscore_zone"] in {"中性区", "偏热(>3.5)", "顶部泡沫区(>7)", "低估区(<1)", "深度价值区(<0)"}


def test_mvrv_percentile_midpoint(monkeypatch):
    """末值处于历史中位时，分位应接近 50%。"""
    rows = _cm_rows(101)
    mid = rows[50]
    rows = rows[:51]              # 让末行成为中位值
    rows[-1] = dict(mid)
    monkeypatch.setattr(sources, "_get", lambda *a, **k: _Resp({"data": rows}))
    out = sources.mvrv_btc()
    assert 95 <= out["percentile"] <= 100  # 截断后末值即最大值


def test_mvrv_falls_back_when_last_row_null(monkeypatch):
    rows = _cm_rows(50)
    rows[-1]["CapMVRVCur"] = None
    monkeypatch.setattr(sources, "_get", lambda *a, **k: _Resp({"data": rows}))
    out = sources.mvrv_btc()
    assert out["value"] is not None


def test_mvrv_raises_on_empty(monkeypatch):
    monkeypatch.setattr(sources, "_get", lambda *a, **k: _Resp({"data": []}))
    with pytest.raises(RuntimeError):
        sources.mvrv_btc()


def test_mvrv_skips_zscore_without_cap_series(monkeypatch):
    rows = [{"time": "2026-01-01T00:00:00Z", "CapMVRVCur": "2.0"} for _ in range(40)]
    monkeypatch.setattr(sources, "_get", lambda *a, **k: _Resp({"data": rows}))
    out = sources.mvrv_btc()
    assert "zscore" not in out
    assert out["percentile"] == 100.0


# ---------------- CBBI 解析 ----------------

def _cbbi_payload(latest=0.82, days=40):
    base = int(dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc).timestamp())
    conf = {str(base + i * 86400): 0.5 + 0.3 * i / (days - 1) for i in range(days)}
    keys = sorted(conf, key=int)
    conf[keys[-1]] = latest
    return {"Price": {}, "Confidence": conf}


def test_cbbi_scales_to_0_100(monkeypatch):
    monkeypatch.setattr(sources, "_get", lambda *a, **k: _Resp(_cbbi_payload(latest=0.92)))
    out = sources.cbbi()
    assert out["value"] == 92.0
    assert out["zone"] == "周期顶部预警(>=90)"
    assert out["date"].startswith("2026-02")


def test_cbbi_reports_30d_change(monkeypatch):
    monkeypatch.setattr(sources, "_get", lambda *a, **k: _Resp(_cbbi_payload(latest=0.80, days=40)))
    out = sources.cbbi()
    assert out["value_30d_ago"] is not None
    assert out["change_30d"] == round(out["value"] - out["value_30d_ago"], 1)


def test_cbbi_short_series_skips_30d(monkeypatch):
    monkeypatch.setattr(sources, "_get", lambda *a, **k: _Resp(_cbbi_payload(latest=0.5, days=5)))
    out = sources.cbbi()
    assert out["value"] == 50.0


def test_cbbi_raises_without_confidence(monkeypatch):
    monkeypatch.setattr(sources, "_get", lambda *a, **k: _Resp({"Price": {}}))
    with pytest.raises(RuntimeError):
        sources.cbbi()


def test_cbbi_raises_on_null_latest(monkeypatch):
    payload = _cbbi_payload()
    keys = sorted(payload["Confidence"], key=int)
    payload["Confidence"][keys[-1]] = None
    monkeypatch.setattr(sources, "_get", lambda *a, **k: _Resp(payload))
    with pytest.raises(RuntimeError):
        sources.cbbi()


# ---------------- 降级与分页 ----------------

def test_mvrv_falls_back_to_latest_only(monkeypatch):
    """全历史请求失败时应降级取最新读数，而不是整体失败。"""
    calls = {"n": 0}

    def fake_get(url, params=None, **kw):
        calls["n"] += 1
        if params and params.get("page_size") == 10000:
            raise RuntimeError("HTTP 400 page_size too large")
        return _Resp({"data": [{"time": "2026-08-24T00:00:00Z", "CapMVRVCur": "2.15"}]})

    monkeypatch.setattr(sources, "_get", fake_get)
    out = sources.mvrv_btc()
    assert out["value"] == 2.15
    assert out["zone"] == "中性区"
    assert "degraded" in out
    assert "percentile" not in out
    assert calls["n"] >= 2


def test_mvrv_fallback_raises_when_both_fail(monkeypatch):
    def fake_get(url, params=None, **kw):
        if params and params.get("page_size") == 10000:
            raise RuntimeError("boom")
        return _Resp({"data": []})

    monkeypatch.setattr(sources, "_get", fake_get)
    with pytest.raises(RuntimeError):
        sources.mvrv_btc()


def test_coinmetrics_series_follows_pagination(monkeypatch):
    pages = [
        {"data": [{"time": "2026-01-01T00:00:00Z", "CapMVRVCur": "1.0"}], "next_page_token": "t1"},
        {"data": [{"time": "2026-01-02T00:00:00Z", "CapMVRVCur": "2.0"}]},
    ]
    seen = []

    def fake_get(url, params=None, **kw):
        seen.append((params or {}).get("next_page_token"))
        return _Resp(pages[min(len(seen) - 1, len(pages) - 1)])

    monkeypatch.setattr(sources, "_get", fake_get)
    rows = sources._coinmetrics_series(["CapMVRVCur"])
    assert len(rows) == 2
    assert seen == [None, "t1"]


def test_coinmetrics_series_respects_max_pages(monkeypatch):
    """始终返回 next_page_token 时不得无限翻页。"""
    monkeypatch.setattr(sources, "_get", lambda *a, **k: _Resp(
        {"data": [{"time": "2026-01-01T00:00:00Z", "CapMVRVCur": "1.0"}], "next_page_token": "always"}))
    rows = sources._coinmetrics_series(["CapMVRVCur"], max_pages=3)
    assert len(rows) == 3
