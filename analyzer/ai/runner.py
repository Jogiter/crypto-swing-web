# -*- coding: utf-8 -*-
"""调用 Claude 产出主观层方案。

在 GitHub Actions 中无人值守运行。三条原则：

1. **绝不阻断机械层**——本层任何失败都只影响 playbook.json，量化报告照常发布。
2. **配置错与临时故障区别对待**——见 errors.py 的退出码约定。
3. **算不通就不落地**——宁可页面显示昨天的方案（带过期提示），也不写入一份
   仓位加不到 100%、RR 算不通的方案。
"""
import datetime as dt
import json
import logging
import pathlib

import anthropic

from ..playbook_schema import SCHEMA_VERSION, validate
from .errors import (EX_CONFIG, EX_DATAERR, EX_TEMPFAIL, fail, notice,
                     preflight_key, read_status, redact, write_status)
from .prompt import build_system, build_user, load_skill
from .schema import OUTPUT_SCHEMA

log = logging.getLogger("ai")

MODEL = "claude-opus-5"
MAX_TOKENS = 32000
EFFORT = "high"

# 检索次数上限——同时是成本护栏
MAX_SEARCHES = 15
MAX_FETCHES = 20

# 单价（美元 / 1M tokens），用于用量估算与预算刹车
PRICE_IN, PRICE_OUT = 5.0, 25.0
MONTHLY_BUDGET_USD = 100.0

PLAYBOOK_PATH = pathlib.Path("docs/data/playbook.json")
REPORT_DIR = pathlib.Path("docs/ai-reports")
HISTORY_DIR = pathlib.Path("docs/data/ai-usage")


def _usage_of(resp):
    u = resp.usage
    tin = (getattr(u, "input_tokens", 0) or 0)
    tin += (getattr(u, "cache_read_input_tokens", 0) or 0)
    tin += (getattr(u, "cache_creation_input_tokens", 0) or 0)
    tout = getattr(u, "output_tokens", 0) or 0
    return {
        "input_tokens": tin,
        "output_tokens": tout,
        "cost_usd_est": round(tin / 1e6 * PRICE_IN + tout / 1e6 * PRICE_OUT, 4),
    }


def _month_spent():
    """本月累计估算支出。防的是「成功但烧钱」——某天出 bug 疯狂调用。"""
    prefix = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m")
    total = 0.0
    if HISTORY_DIR.exists():
        for p in HISTORY_DIR.glob(f"{prefix}-*.json"):
            try:
                total += float(json.loads(p.read_text(encoding="utf-8"))
                               .get("cost_usd_est", 0))
            except (ValueError, OSError):
                continue
    return total


def _record_usage(date, usage):
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    (HISTORY_DIR / f"{date}.json").write_text(
        json.dumps(usage, ensure_ascii=False), encoding="utf-8")


def _extract_json(resp):
    """取出最终的 JSON 文本块。"""
    for block in reversed(resp.content):
        if getattr(block, "type", None) == "text" and block.text.strip():
            return json.loads(block.text)
    raise ValueError("响应中没有文本块")


def _call(client, system, user):
    # 大 max_tokens 必须走 streaming，否则会撞 HTTP 超时
    with client.messages.stream(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=[{"type": "text", "text": system,
                 "cache_control": {"type": "ephemeral"}}],
        thinking={"type": "adaptive"},
        output_config={"effort": EFFORT,
                       "format": {"type": "json_schema", "schema": OUTPUT_SCHEMA}},
        tools=[
            {"type": "web_search_20260209", "name": "web_search",
             "max_uses": MAX_SEARCHES},
            {"type": "web_fetch_20260209", "name": "web_fetch",
             "max_uses": MAX_FETCHES},
        ],
        messages=[{"role": "user", "content": user}],
    ) as stream:
        return stream.get_final_message()


def _assemble(payload, mech):
    """把模型产出的内容补上元数据，组装成符合契约的 playbook。

    schema_version / generated_at_utc / source 由代码填——它们是元数据，
    让模型编容易出错。
    """
    plan = {
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": dt.datetime.now(dt.timezone.utc)
                              .isoformat(timespec="seconds").replace("+00:00", "Z"),
        "source": f"crypto-swing-analysis skill · {MODEL} · GitHub Actions",
        "report_date": mech.get("date"),
        "price_anchor": payload.get("price_anchor"),
        "coins": payload.get("coins"),
    }
    for opt in ("add_conditions", "provenance"):
        if payload.get(opt):
            plan[opt] = payload[opt]
    return plan


def run():
    key = preflight_key()
    if key is None:
        return 0                     # 未配置 key：跳过，不算失败

    spent = _month_spent()
    if spent >= MONTHLY_BUDGET_USD:
        fail("error", f"本月 AI 分析支出已达上限 ${MONTHLY_BUDGET_USD}",
             f"当前累计约 ${spent:.2f}，本次跳过。调整 MONTHLY_BUDGET_USD 或等下月重置",
             EX_CONFIG, status="budget_exceeded")

    mech_path = pathlib.Path("docs/data/latest.json")
    if not mech_path.exists():
        fail("error", "找不到机械层结果 latest.json",
             "AI 层依赖 run_analysis.py 的产出，请确认它已先行运行", EX_CONFIG)
    mech = json.loads(mech_path.read_text(encoding="utf-8"))

    skill, template = load_skill()
    system = build_system(skill, template)
    client = anthropic.Anthropic(api_key=key)

    prior_errors = None
    for attempt in (1, 2):
        try:
            resp = _call(client, system, build_user(mech, prior_errors))

        # ---- 配置类：不动手就永远不会好 ----
        except anthropic.AuthenticationError:
            fail("error", "Claude API 认证失败",
                 "ANTHROPIC_API_KEY 无效、已撤销或已过期。"
                 "请在 Settings → Secrets and variables → Actions 更新该 secret",
                 EX_CONFIG, status="auth_failed")
        except anthropic.PermissionDeniedError:
            fail("error", "Claude API 权限不足",
                 f"该 key 无权访问 {MODEL} 或其所属 workspace，请在 Console 检查 key 的归属与权限",
                 EX_CONFIG, status="permission_denied")
        except anthropic.BadRequestError as e:
            # 请求构造错了——这是代码 bug，要显眼
            fail("error", "Claude API 请求无效（可能是代码问题）",
                 redact(e), EX_CONFIG, status="bad_request")

        # ---- 临时类：下次定时任务大概率自愈 ----
        except anthropic.RateLimitError as e:
            ra = "未提供"
            try:
                ra = e.response.headers.get("retry-after", "未提供")
            except AttributeError:
                pass
            fail("warning", "Claude API 限流或额度耗尽",
                 f"SDK 自动重试后仍未通过（retry-after: {ra}）。"
                 "若为额度问题请在 Console 检查用量，否则下次定时任务会自动重试",
                 EX_TEMPFAIL, status="rate_limited")
        except anthropic.APIStatusError as e:
            server = e.status_code >= 500
            fail("warning" if server else "error",
                 f"Claude API 返回 {e.status_code}", redact(e),
                 EX_TEMPFAIL if server else EX_CONFIG, status="api_error")
        except (anthropic.APIConnectionError, anthropic.APITimeoutError) as e:
            fail("warning", "无法连接 Claude API",
                 f"{type(e).__name__}；下次定时任务会重试", EX_TEMPFAIL,
                 status="connection_error")

        usage = _usage_of(resp)

        if resp.stop_reason == "refusal":
            cat = getattr(getattr(resp, "stop_details", None), "category", None)
            fail("warning", "模型拒绝了本次请求",
                 f"category={cat}；本次跳过，保留上一版方案", EX_DATAERR,
                 status="refused")

        if resp.stop_reason == "max_tokens":
            notice("warning", "输出触及 max_tokens 上限", "报告可能被截断，将继续尝试校验")

        try:
            payload = _extract_json(resp)
        except (ValueError, json.JSONDecodeError) as e:
            prior_errors = [f"输出不是合法 JSON：{redact(e)}"]
            if attempt == 2:
                fail("warning", "AI 输出两次都不是合法 JSON，保留上一版方案",
                     redact(e), EX_DATAERR, status="invalid_output")
            notice("warning", "AI 输出不是合法 JSON，重试一次", redact(e))
            continue

        plan = _assemble(payload, mech)
        ok, errors = validate(plan)
        if ok:
            return _publish(plan, payload, mech, usage)

        prior_errors = errors
        if attempt == 2:
            fail("warning", "AI 输出两次未通过契约校验，保留上一版方案",
                 "；".join(errors[:5]), EX_DATAERR, status="invalid_output")
        notice("warning", "AI 输出未通过契约校验，回喂错误重试一次",
               "；".join(errors[:3]))

    return EX_DATAERR


def _publish(plan, payload, mech, usage):
    PLAYBOOK_PATH.parent.mkdir(parents=True, exist_ok=True)
    PLAYBOOK_PATH.write_text(
        json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8")

    report = payload.get("report_markdown")
    if report:
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        (REPORT_DIR / f"{mech.get('date')}.md").write_text(report, encoding="utf-8")

    _record_usage(mech.get("date"), usage)
    write_status("ok", "AI 分析完成",
                 f"本次约 ${usage['cost_usd_est']}，本月累计约 ${_month_spent():.2f}",
                 usage=usage)
    notice("notice", "AI 分析层完成",
           f"输入 {usage['input_tokens']:,} tok / 输出 {usage['output_tokens']:,} tok "
           f"≈ ${usage['cost_usd_est']}")
    return 0
