# -*- coding: utf-8 -*-
"""CBBI / MVRV 的解析与分区逻辑测试（mock HTTP，不触网）。"""
import datetime as dt
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402
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

def _mvrv_rows(n=100, lo=1.0, hi=3.0):
    """CoinMetrics 风格的 MVRV 序列，线性从 lo 升到 hi。"""
    return [{"asset": "btc",
             "time": f"2026-01-{(i % 28) + 1:02d}T00:00:00.000000000Z",
             "CapMVRVCur": str(lo + (hi - lo) * i / (n - 1))} for i in range(n)]


def _cap_rows(n=100):
    return [{"asset": "btc",
             "time": f"2026-01-{(i % 28) + 1:02d}T00:00:00.000000000Z",
             "CapMrktCurUSD": str(1e12 + 1e10 * i),
             "CapRealUSD": str(8e11)} for i in range(n)]


def _router(mvrv=None, caps=None):
    """按请求的 metrics 分派响应；传入 Exception 实例表示该请求失败。"""
    def fake_get(url, params=None, **kw):
        metrics = (params or {}).get("metrics", "")
        target = mvrv if "CapMVRVCur" in metrics else caps
        if isinstance(target, Exception):
            raise target
        return _Resp({"data": target or []})
    return fake_get


def test_mvrv_parses_value_and_percentile(monkeypatch):
    monkeypatch.setattr(sources, "_get", _router(mvrv=_mvrv_rows(100), caps=_cap_rows()))
    out = sources.mvrv_btc()
    assert out["value"] == 3.0
    assert out["percentile"] == 100.0       # 末值为序列最大
    assert out["history_days"] == 100
    assert out["date"] == "2026-01-16"
    assert out["zscore"] is not None


def test_mvrv_percentile_reflects_rank(monkeypatch):
    """末值处于历史中位时，分位应接近 50%。"""
    rows = _mvrv_rows(101)
    rows.append(dict(rows[50]))             # 末行取中位值
    monkeypatch.setattr(sources, "_get", _router(mvrv=rows, caps=_cap_rows()))
    out = sources.mvrv_btc()
    assert 48 <= out["percentile"] <= 53, out["percentile"]


def test_zscore_403_still_keeps_percentile(monkeypatch):
    """真实回归：社区版对市值指标返回 403 时，历史分位必须保留。

    这正是 2026-08-26 首次 Actions 运行暴露的问题——当时三个指标打包在
    同一请求里，403 让分位和 Z-Score 一起丢失，只剩降级读数。
    """
    monkeypatch.setattr(sources, "_get", _router(
        mvrv=_mvrv_rows(200), caps=RuntimeError("GET ... failed: HTTP 403")))
    out = sources.mvrv_btc()
    assert out["percentile"] is not None     # 分位保住
    assert out["history_days"] == 200
    assert "zscore" not in out               # Z-Score 缺席
    assert "社区版无市值" in out["zscore_note"]
    assert "degraded" not in out             # 未跌到最低降级档


def test_mvrv_falls_back_to_latest_only_when_history_fails(monkeypatch):
    """MVRV 序列本身失败时，才降到只取读数。"""
    def fake_get(url, params=None, **kw):
        if (params or {}).get("page_size") == 1000:
            raise RuntimeError("HTTP 403")
        return _Resp({"data": [{"time": "2026-08-24T00:00:00Z", "CapMVRVCur": "2.15"}]})

    monkeypatch.setattr(sources, "_get", fake_get)
    out = sources.mvrv_btc()
    assert out["value"] == 2.15
    assert "degraded" in out
    assert "percentile" not in out


def test_mvrv_raises_when_everything_fails(monkeypatch):
    def fake_get(url, params=None, **kw):
        if (params or {}).get("page_size") == 1000:
            raise RuntimeError("boom")
        return _Resp({"data": []})

    monkeypatch.setattr(sources, "_get", fake_get)
    with pytest.raises(RuntimeError):
        sources.mvrv_btc()


def test_mvrv_handles_null_trailing_value(monkeypatch):
    rows = _mvrv_rows(50)
    rows[-1]["CapMVRVCur"] = None
    monkeypatch.setattr(sources, "_get", _router(mvrv=rows, caps=_cap_rows()))
    out = sources.mvrv_btc()
    assert out["value"] is not None


def test_zscore_math(monkeypatch):
    """Z = (市值 - 实现市值) / 市值标准差。"""
    caps = _cap_rows(100)
    monkeypatch.setattr(sources, "_get", _router(mvrv=_mvrv_rows(100), caps=caps))
    out = sources.mvrv_btc()
    mkt = float(caps[-1]["CapMrktCurUSD"])
    real = float(caps[-1]["CapRealUSD"])
    series = np.asarray([float(c["CapMrktCurUSD"]) for c in caps])
    assert out["zscore"] == round((mkt - real) / float(series.std()), 2)


def test_zscore_skipped_when_series_too_short(monkeypatch):
    monkeypatch.setattr(sources, "_get", _router(mvrv=_mvrv_rows(60), caps=_cap_rows(5)))
    out = sources.mvrv_btc()
    assert "zscore" not in out
    assert out["percentile"] is not None


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
