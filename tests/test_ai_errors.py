# -*- coding: utf-8 -*-
"""AI 层的脱敏、预检与状态记录。

错误路径只有在出事时才会跑到，所以更要测——线上第一次触发它们的时候，
往往正是最不想再遇到一个 bug 的时候。
"""
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from analyzer.ai import errors as E  # noqa: E402

FAKE_KEY = "sk-ant-api03-" + "A" * 40


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    """状态文件写到临时目录，别污染仓库。"""
    monkeypatch.setattr(E, "STATUS_PATH", tmp_path / "ai-status.json")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    return tmp_path


# ---------------- 脱敏 ----------------

def test_redact_masks_api_key():
    out = E.redact(f"failed with key {FAKE_KEY} at line 3")
    assert FAKE_KEY not in out
    assert "REDACTED" in out


def test_redact_masks_oauth_token():
    tok = "sk-ant-oat01-" + "z" * 30
    assert tok not in E.redact(f"token={tok}")


def test_redact_masks_multiple_occurrences():
    out = E.redact(f"{FAKE_KEY} and again {FAKE_KEY}")
    assert FAKE_KEY not in out
    assert out.count("REDACTED") == 2


def test_redact_handles_none_and_exceptions():
    assert E.redact(None) == ""
    assert FAKE_KEY not in E.redact(RuntimeError(f"boom {FAKE_KEY}"))


def test_notice_output_is_redacted(capsys):
    E.notice("error", "标题", f"detail with {FAKE_KEY}")
    out = capsys.readouterr().out
    assert FAKE_KEY not in out
    assert out.startswith("::error title=标题::")


def test_notice_strips_newlines_so_annotation_survives(capsys):
    """换行会截断 GitHub 注解，必须压平。"""
    E.notice("warning", "标题\n第二行", "detail\nsecond")
    out = capsys.readouterr().out.strip()
    assert len(out.splitlines()) == 1


# ---------------- 预检 ----------------

def test_missing_key_skips_without_failing(capsys):
    assert E.preflight_key() is None
    out = capsys.readouterr().out
    assert "::warning" in out and "跳过 AI 分析层" in out
    assert E.read_status()["status"] == "skipped_no_key"


def test_empty_string_key_treated_as_missing(monkeypatch):
    """secret 未配置时 ${{ secrets.X }} 展开成空串，而空串仍占据 SDK 凭据优先级。"""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    assert E.preflight_key() is None
    assert E.read_status()["status"] == "skipped_no_key"


def test_whitespace_only_key_treated_as_missing(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "   \n ")
    assert E.preflight_key() is None


def test_malformed_key_exits_config_error(monkeypatch, capsys):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "not-a-real-key")
    with pytest.raises(SystemExit) as ex:
        E.preflight_key()
    assert ex.value.code == E.EX_CONFIG
    out = capsys.readouterr().out
    assert "格式不正确" in out


def test_malformed_key_reports_length_not_content(monkeypatch, capsys):
    """报长度足以判断是否粘漏，报内容则是泄露。"""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "wrong-secret-value")
    with pytest.raises(SystemExit):
        E.preflight_key()
    out = capsys.readouterr().out
    assert "长度 18" in out
    assert "wrong-secret-value" not in out


def test_valid_key_passes_through(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", f"  {FAKE_KEY}  ")
    assert E.preflight_key() == FAKE_KEY      # 两端空白被去掉


# ---------------- 状态文件 ----------------

def test_status_preserves_last_success_across_failures():
    """只说「这次失败了」不够，要能看出已经断了几天。"""
    E.write_status("ok", "成功")
    first_success = E.read_status()["last_success_utc"]

    E.write_status("auth_failed", "认证失败")
    after = E.read_status()
    assert after["status"] == "auth_failed"
    assert after["last_success_utc"] == first_success


def test_status_redacts_detail():
    E.write_status("auth_failed", "标题", f"key {FAKE_KEY} rejected")
    assert FAKE_KEY not in E.STATUS_PATH.read_text(encoding="utf-8")


def test_status_survives_corrupt_existing_file():
    E.STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    E.STATUS_PATH.write_text("{ not json", encoding="utf-8")
    assert E.read_status() == {}
    E.write_status("ok", "成功")            # 不得抛异常
    assert E.read_status()["status"] == "ok"


def test_fail_writes_status_and_exits_with_code(capsys):
    with pytest.raises(SystemExit) as ex:
        E.fail("warning", "限流", "稍后重试", E.EX_TEMPFAIL, status="rate_limited")
    assert ex.value.code == E.EX_TEMPFAIL
    assert E.read_status()["status"] == "rate_limited"
    assert "::warning" in capsys.readouterr().out


def test_fail_derives_status_from_exit_code(capsys):
    with pytest.raises(SystemExit):
        E.fail("error", "配置错", "去改 secret", E.EX_CONFIG)
    assert E.read_status()["status"] == "config_error"


def test_status_json_is_valid_and_reloadable():
    E.write_status("ok", "成功", "一切正常", usage={"input_tokens": 1, "output_tokens": 2})
    data = json.loads(E.STATUS_PATH.read_text(encoding="utf-8"))
    assert data["usage"]["input_tokens"] == 1
    assert data["last_attempt_utc"].endswith("+00:00")
