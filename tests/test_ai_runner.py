# -*- coding: utf-8 -*-
"""AI runner 的错误矩阵与落地保障（全程 mock，不触网）。"""
import json
import pathlib
import sys
import types

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import anthropic  # noqa: E402
import httpx2  # noqa: E402
import pytest  # noqa: E402

from analyzer.ai import errors as E  # noqa: E402
from analyzer.ai import runner as R  # noqa: E402

FAKE_KEY = "sk-ant-api03-" + "A" * 40
REQ = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")


def _http_err(cls, status, headers=None):
    return cls("boom", response=httpx2.Response(status, request=REQ, headers=headers or {}),
               body=None)


def _valid_payload():
    """一份能通过契约校验的最小方案。"""
    def coin(price, stop, tgt):
        return {
            "bias": "做多", "confidence": "中高", "position_pct": 30,
            "entry_batches": [
                {"batch": 1, "low": price * 0.97, "high": price, "weight_pct": 60},
                {"batch": 2, "low": price * 0.93, "high": price * 0.95, "weight_pct": 40},
            ],
            "stops": [
                {"level": 1, "name": "结构止损", "price": stop, "action": "减半"},
                {"level": 2, "name": "最终止损", "price": stop * 0.95, "action": "清仓"},
            ],
            "targets": [{"name": "TP1", "price": tgt, "reduce_pct": 50}],
            "trailing": {"remain_pct": 50},
            "risk_reward": [{
                "label": "基准", "entry": price, "stop": stop, "target": tgt,
                "ratio": round((tgt - price) / (price - stop), 2),
            }],
        }
    return {
        "price_anchor": {"BTC": 79000, "ETH": 2460, "SOL": 97},
        "coins": {"BTC": coin(79000, 77000, 83000),
                  "ETH": coin(2460, 2400, 2600),
                  "SOL": coin(97, 93, 108)},
        "report_markdown": "# 报告\n\n正文",
    }


def _resp(payload, stop_reason="end_turn", **extra):
    text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    return types.SimpleNamespace(
        content=[types.SimpleNamespace(type="text", text=text)],
        stop_reason=stop_reason,
        usage=types.SimpleNamespace(input_tokens=1000, output_tokens=2000,
                                    cache_read_input_tokens=0,
                                    cache_creation_input_tokens=0),
        **extra,
    )


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    """全部产物写临时目录；提供一份 latest.json 与既有 playbook.json。"""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_KEY)
    monkeypatch.setattr(E, "STATUS_PATH", tmp_path / "docs/data/ai-status.json")
    monkeypatch.setattr(R, "PLAYBOOK_PATH", tmp_path / "docs/data/playbook.json")
    monkeypatch.setattr(R, "REPORT_DIR", tmp_path / "docs/ai-reports")
    monkeypatch.setattr(R, "HISTORY_DIR", tmp_path / "docs/data/ai-usage")

    (tmp_path / "docs/data").mkdir(parents=True)
    (tmp_path / "docs/data/latest.json").write_text(json.dumps({
        "date": "2026-08-26", "generated_at_utc": "2026-08-26T00:15:00Z",
        "generated_at_beijing": "2026-08-26 08:15 (北京时间)",
        "coins": {"BTC": {"price": 79000, "frames": {}}}, "macro": {},
        "btc_cycle": {}, "missing": [],
    }, ensure_ascii=False), encoding="utf-8")

    # 既有方案——校验失败时必须原样保留
    R.PLAYBOOK_PATH.write_text(json.dumps({"schema_version": "1.0", "marker": "OLD"}),
                               encoding="utf-8")
    # skill 文件
    sd = tmp_path / "skills/crypto-swing-analysis/references"
    sd.mkdir(parents=True)
    (sd.parent / "SKILL.md").write_text("# skill", encoding="utf-8")
    (sd / "report-template.md").write_text("# template", encoding="utf-8")
    return tmp_path


def _patch_call(monkeypatch, side_effect):
    calls = []

    def fake(client, system, user):
        calls.append(user)
        r = side_effect(len(calls))
        if isinstance(r, Exception):
            raise r
        return r

    monkeypatch.setattr(R, "_call", fake)
    monkeypatch.setattr(R.anthropic, "Anthropic", lambda **kw: object())
    return calls


def _old_plan_intact():
    return json.loads(R.PLAYBOOK_PATH.read_text(encoding="utf-8")).get("marker") == "OLD"


# ---------------- 配置类错误：不动手就不会好 ----------------

@pytest.mark.parametrize("exc,status", [
    (_http_err(anthropic.AuthenticationError, 401), "auth_failed"),
    (_http_err(anthropic.PermissionDeniedError, 403), "permission_denied"),
    (_http_err(anthropic.BadRequestError, 400), "bad_request"),
])
def test_config_errors_exit_78_and_keep_old_plan(monkeypatch, capsys, exc, status):
    _patch_call(monkeypatch, lambda n: exc)
    with pytest.raises(SystemExit) as ex:
        R.run()
    assert ex.value.code == E.EX_CONFIG
    assert E.read_status()["status"] == status
    assert "::error" in capsys.readouterr().out       # 配置错用红色
    assert _old_plan_intact()


# ---------------- 临时类错误：下次会自愈 ----------------

def test_rate_limit_exits_75_with_warning(monkeypatch, capsys):
    _patch_call(monkeypatch, lambda n: _http_err(
        anthropic.RateLimitError, 429, {"retry-after": "30"}))
    with pytest.raises(SystemExit) as ex:
        R.run()
    assert ex.value.code == E.EX_TEMPFAIL
    assert E.read_status()["status"] == "rate_limited"
    out = capsys.readouterr().out
    assert "::warning" in out and "30" in out          # 临时故障用黄色
    assert _old_plan_intact()


def test_server_error_is_temp_but_client_error_is_config(monkeypatch):
    _patch_call(monkeypatch, lambda n: _http_err(anthropic.APIStatusError, 503))
    with pytest.raises(SystemExit) as ex:
        R.run()
    assert ex.value.code == E.EX_TEMPFAIL

    _patch_call(monkeypatch, lambda n: _http_err(anthropic.APIStatusError, 404))
    with pytest.raises(SystemExit) as ex2:
        R.run()
    assert ex2.value.code == E.EX_CONFIG


@pytest.mark.parametrize("exc", [
    anthropic.APIConnectionError(request=REQ),
    anthropic.APITimeoutError(request=REQ),
])
def test_network_errors_exit_75(monkeypatch, exc):
    _patch_call(monkeypatch, lambda n: exc)
    with pytest.raises(SystemExit) as ex:
        R.run()
    assert ex.value.code == E.EX_TEMPFAIL
    assert E.read_status()["status"] == "connection_error"


def test_error_detail_is_redacted(monkeypatch):
    err = _http_err(anthropic.BadRequestError, 400)
    err.message = f"invalid key {FAKE_KEY}"
    _patch_call(monkeypatch, lambda n: err)
    with pytest.raises(SystemExit):
        R.run()
    assert FAKE_KEY not in E.STATUS_PATH.read_text(encoding="utf-8")


# ---------------- 模型侧异常 ----------------

def test_refusal_keeps_old_plan(monkeypatch):
    _patch_call(monkeypatch, lambda n: _resp(
        _valid_payload(), stop_reason="refusal",
        stop_details=types.SimpleNamespace(category="cyber")))
    with pytest.raises(SystemExit) as ex:
        R.run()
    assert ex.value.code == E.EX_DATAERR
    assert E.read_status()["status"] == "refused"
    assert _old_plan_intact()


def test_non_json_output_retries_then_gives_up(monkeypatch):
    calls = _patch_call(monkeypatch, lambda n: _resp("这不是 JSON"))
    with pytest.raises(SystemExit) as ex:
        R.run()
    assert ex.value.code == E.EX_DATAERR
    assert len(calls) == 2                    # 重试了一次
    assert _old_plan_intact()


def test_invalid_output_feeds_errors_back_on_retry(monkeypatch):
    bad = _valid_payload()
    bad["coins"]["BTC"]["entry_batches"][0]["weight_pct"] = 10   # 合计不再是 100
    calls = _patch_call(monkeypatch, lambda n: _resp(bad))
    with pytest.raises(SystemExit):
        R.run()
    assert len(calls) == 2
    assert "上一次输出未通过校验" in calls[1]      # 错误被回喂
    assert "合计" in calls[1]


def test_retry_succeeds_publishes(monkeypatch):
    bad = _valid_payload()
    bad["coins"]["ETH"]["position_pct"] = 999
    _patch_call(monkeypatch, lambda n: _resp(bad if n == 1 else _valid_payload()))
    assert R.run() == 0
    assert not _old_plan_intact()             # 已被新方案替换


# ---------------- 成功路径 ----------------

def test_success_writes_playbook_report_usage_status(monkeypatch):
    _patch_call(monkeypatch, lambda n: _resp(_valid_payload()))
    assert R.run() == 0

    plan = json.loads(R.PLAYBOOK_PATH.read_text(encoding="utf-8"))
    assert plan["schema_version"] == "1.0"
    assert plan["report_date"] == "2026-08-26"
    assert "claude-opus-5" in plan["source"]
    assert plan["generated_at_utc"].endswith("Z")
    assert set(plan["coins"]) == {"BTC", "ETH", "SOL"}

    assert (R.REPORT_DIR / "2026-08-26.md").read_text(encoding="utf-8").startswith("# 报告")
    usage = json.loads((R.HISTORY_DIR / "2026-08-26.json").read_text(encoding="utf-8"))
    assert usage["cost_usd_est"] > 0
    assert E.read_status()["status"] == "ok"


def test_published_plan_passes_the_shipped_validator(monkeypatch):
    """落地的东西必须能过契约校验——这是页面渲染的前提。"""
    from analyzer.playbook_schema import validate
    _patch_call(monkeypatch, lambda n: _resp(_valid_payload()))
    R.run()
    ok, errs = validate(json.loads(R.PLAYBOOK_PATH.read_text(encoding="utf-8")))
    assert ok, errs


def test_metadata_is_filled_by_code_not_model(monkeypatch):
    """模型编的元数据应被忽略，防止它写错时间戳或版本号。"""
    payload = _valid_payload()
    payload["schema_version"] = "9.9"
    payload["generated_at_utc"] = "昨天"
    _patch_call(monkeypatch, lambda n: _resp(payload))
    R.run()
    plan = json.loads(R.PLAYBOOK_PATH.read_text(encoding="utf-8"))
    assert plan["schema_version"] == "1.0"
    assert plan["generated_at_utc"] != "昨天"


# ---------------- 护栏 ----------------

def test_budget_guard_blocks_when_month_exceeded(monkeypatch, capsys):
    R.HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    (R.HISTORY_DIR / "2026-08-01.json").write_text(
        json.dumps({"cost_usd_est": R.MONTHLY_BUDGET_USD + 1}), encoding="utf-8")
    monkeypatch.setattr(R, "_month_spent", lambda: R.MONTHLY_BUDGET_USD + 1)
    with pytest.raises(SystemExit) as ex:
        R.run()
    assert ex.value.code == E.EX_CONFIG
    assert E.read_status()["status"] == "budget_exceeded"


def test_missing_key_skips_without_calling_api(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    called = _patch_call(monkeypatch, lambda n: _resp(_valid_payload()))
    assert R.run() == 0
    assert not called                         # 根本没调 API
    assert _old_plan_intact()


def test_missing_mechanical_layer_is_config_error(monkeypatch):
    (pathlib.Path("docs/data/latest.json")).unlink()
    _patch_call(monkeypatch, lambda n: _resp(_valid_payload()))
    with pytest.raises(SystemExit) as ex:
        R.run()
    assert ex.value.code == E.EX_CONFIG
