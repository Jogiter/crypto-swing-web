# -*- coding: utf-8 -*-
"""AI 层的错误处理、脱敏与状态记录。

两条约束贯穿本文件：

1. **本仓库是 public，Actions 日志任何人可见。** 任何写往 stdout 或状态文件的
   文本都必须先过 redact()。GitHub 只遮蔽 secret 的字面量，变换过就不遮，
   所以不依赖平台的遮蔽。
2. **配置错与临时故障要区别对待。** 前者不动手就永远不会好，值得红色告警；
   后者下次定时任务大概率自愈，黄色提示即可。退出码沿用 sysexits 惯例。
"""
import datetime as dt
import json
import os
import pathlib
import re
import sys

# sysexits.h 惯例
EX_OK = 0
EX_DATAERR = 65     # 模型输出算不通
EX_TEMPFAIL = 75    # 限流/服务器/网络——下次会自愈
EX_CONFIG = 78      # key 缺失/无效/权限——需要人介入

STATUS_PATH = pathlib.Path("docs/data/ai-status.json")

# 覆盖 sk-ant-api… / sk-ant-oat01… 等所有形态
_SECRET_RE = re.compile(r"sk-ant-[A-Za-z0-9_\-]{8,}")


def redact(text):
    """抹掉任何疑似凭据的片段。

    调用点不该假设 SDK 的异常里没有凭据——今天没有不代表将来没有。
    """
    return _SECRET_RE.sub("sk-ant-***REDACTED***", str(text or ""))


def _now():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def notice(level, title, detail=""):
    """GitHub Actions 注解，显示在运行页面顶部。

    level: notice | warning | error
    """
    safe_title = redact(title).replace("\n", " ")
    safe_detail = redact(detail).replace("\n", " ")
    print(f"::{level} title={safe_title}::{safe_detail}", flush=True)


def read_status():
    if not STATUS_PATH.exists():
        return {}
    try:
        return json.loads(STATUS_PATH.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {}


def write_status(status, title="", detail="", usage=None, keep_success=True):
    """写状态文件。

    保留 last_success_utc——只说「这次失败了」不够，要能一眼看出已经断了几天。
    """
    prev = read_status()
    data = {
        "last_attempt_utc": _now(),
        "status": status,
        "title": redact(title),
        "detail": redact(detail),
        "usage": usage,
    }
    if status == "ok":
        data["last_success_utc"] = data["last_attempt_utc"]
    elif keep_success and prev.get("last_success_utc"):
        data["last_success_utc"] = prev["last_success_utc"]

    STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATUS_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return data


def fail(level, title, detail, code, status=None):
    """报错三件事：注解、状态文件、按码退出。

    detail 一律写「下一步该做什么」，不是只抛个异常名。
    """
    notice(level, title, detail)
    write_status(status or _status_for(code), title, detail)
    sys.exit(code)


def _status_for(code):
    return {
        EX_CONFIG: "config_error",
        EX_TEMPFAIL: "temp_failure",
        EX_DATAERR: "invalid_output",
    }.get(code, "error")


def preflight_key():
    """返回 API key；未配置时返回 None（跳过，不算失败）。

    空串单独处理：secret 未配置时 ${{ secrets.X }} 正好展开成空串，而空串
    仍会占据 SDK 的凭据优先级，直接调用会得到一个费解的 401。
    """
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()

    if not key:
        notice("warning", "未配置 ANTHROPIC_API_KEY，跳过 AI 分析层",
               "机械层不受影响。在 Settings → Secrets and variables → Actions "
               "添加该 secret 后本层自动生效")
        write_status("skipped_no_key", "未配置 API key",
                     "机械层照常运行；配置 secret 后 AI 层自动启用")
        return None

    if not key.startswith("sk-ant-"):
        # 只报长度不报内容——足够判断是不是粘贴时少了一截
        fail("error", "ANTHROPIC_API_KEY 格式不正确",
             f"应以 sk-ant- 开头，实际长度 {len(key)}。请检查 secret 是否完整粘贴",
             EX_CONFIG)

    return key
