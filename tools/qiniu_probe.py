# -*- coding: utf-8 -*-
"""七牛云网关能否承载本仓库 AI 分析层——一次跑完的探针。

背景：七牛的 AI 推理服务同时提供 OpenAI 兼容与 **Anthropic 原生** 两套协议
（其官方配置工具 qiniu/coding-helper 的 CLAUDE.md 写明支持 /v1/chat/completions、
/v1/messages、/v1/models）。原生 /v1/messages 意味着 analyzer/ai/runner.py 那套
调用理论上只需换 base_url 就能跑——但「协议兼容」不等于「能力等价」。

runner.py 依赖四项 Anthropic 较新的能力，它们是否透传决定了这条路可不可行：

    thinking       深度思考
    output_config  effort 档位 + json_schema 结构化输出
    cache_control  提示词缓存（system 前缀命中可省一大笔）
    web_search / web_fetch   服务端工具——skill 要靠它抓宏观与链上数据

七牛模型市场返回的元数据里有 reasoning / schema_output / content_cache /
function_calling 四个开关，唯独没有服务端工具的位置。所以先探再用，
不要假设。

本脚本按「能力阶梯」逐项探测，每项独立失败、互不牵连，最后给出一个可直接
粘贴回来的结论。探测完成后可选地跑一次真实 skill 调用，用仓库自己的契约
校验器验证产物——那才是「免费额度能不能真的跑通 skill」的答案。

用法：
    export QINIU_API_KEY=<你的七牛 key>
    python3 tools/qiniu_probe.py                 # 探能力 + 跑真实 skill
    python3 tools/qiniu_probe.py --skip-skill    # 只探能力，几乎不耗额度
    python3 tools/qiniu_probe.py --base-url https://api.modelink.ai   # 海外线路

脚本从不打印 key，报告落盘前也会脱敏。
"""
import argparse
import datetime as dt
import json
import os
import pathlib
import re
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

CN = "https://api.qnaigc.com"          # 国内线路
INTL = "https://api.modelink.ai"       # 海外线路

# 探测用的小请求，尽量不烧额度
PROBE_MAX_TOKENS = 64

_SECRET_RE = re.compile(r"(sk-[A-Za-z0-9_\-]{6,}|Bearer\s+\S+)")


def redact(text):
    return _SECRET_RE.sub("***REDACTED***", str(text or ""))


def short(e, limit=400):
    """异常转成一行可读摘要——网关的报错常常是一大坨 HTML 或 JSON。"""
    msg = redact(e).replace("\n", " ")
    return msg[:limit] + ("…" if len(msg) > limit else "")


# ---------------------------------------------------------------- 报告收集

class Report:
    def __init__(self, base_url):
        self.base_url = base_url
        self.started = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
        self.steps = []

    def add(self, name, ok, detail="", data=None):
        self.steps.append({"name": name, "ok": ok,
                           "detail": redact(detail), "data": data})
        mark = "✅" if ok else ("⚠️" if ok is None else "❌")
        print(f"  {mark} {name}" + (f" — {redact(detail)}" if detail else ""),
              flush=True)
        return ok

    def get(self, name):
        for s in self.steps:
            if s["name"] == name:
                return s
        return None

    def ok(self, name):
        s = self.get(name)
        return bool(s and s["ok"])


# ------------------------------------------------------------ REST 端点探测

def http_get(base_url, path, key, timeout=30):
    # 用 requests 而非 httpx：anthropic 1.x 底层是 httpx2，装了 SDK 未必有 httpx，
    # 而 requests 本就是本仓库机械层的依赖，一定在。
    import requests
    return requests.get(base_url.rstrip("/") + path,
                        headers={"Authorization": f"Bearer {key}",
                                 "Content-Type": "application/json"},
                        timeout=timeout)


def probe_quota(rep, key):
    """GET /v2/stat/usage —— 七牛官方 CLI 就是拿它验 key 的，顺带看今日用量。"""
    today = dt.date.today().isoformat()
    path = (f"/v2/stat/usage?granularity=day"
            f"&start={today}T00:00:00%2B08:00&end={today}T23:59:59%2B08:00")
    try:
        r = http_get(rep.base_url, path, key)
    except Exception as e:
        return rep.add("额度查询 /v2/stat/usage", False, f"{type(e).__name__}: {short(e)}")
    if r.status_code in (401, 403):
        return rep.add("额度查询 /v2/stat/usage", False,
                       f"HTTP {r.status_code}——key 无效或无权限")
    if r.status_code != 200:
        return rep.add("额度查询 /v2/stat/usage", None, f"HTTP {r.status_code}")
    try:
        body = r.json()
    except ValueError:
        return rep.add("额度查询 /v2/stat/usage", None, "返回不是 JSON")
    return rep.add("额度查询 /v2/stat/usage", True, "key 有效", body)


def probe_models(rep, key):
    """GET /v1/market/models —— 拿到模型清单与它们自报的能力。"""
    try:
        r = http_get(rep.base_url, "/v1/market/models", key)
    except Exception as e:
        return rep.add("模型市场 /v1/market/models", False,
                       f"{type(e).__name__}: {short(e)}")
    if r.status_code != 200:
        return rep.add("模型市场 /v1/market/models", False, f"HTTP {r.status_code}")
    try:
        models = r.json().get("data") or []
    except ValueError:
        return rep.add("模型市场 /v1/market/models", False, "返回不是 JSON")

    rows = []
    for m in models:
        mid = m.get("id", "")
        if "opus" not in mid.lower():
            continue
        cons = m.get("model_constraints") or {}
        arch = m.get("architecture") or {}
        rows.append({
            "id": mid,
            "features": m.get("features") or [],
            "context_length": cons.get("context_length"),
            "max_tokens": cons.get("max_tokens"),
            "reasoning": (arch.get("reasoning") or {}).get("supported"),
            "schema_output": (arch.get("schema_output") or {}).get("supported"),
            "content_cache": (arch.get("content_cache") or {}).get("supported"),
            "function_calling": (arch.get("function_calling") or {}).get("supported"),
        })
    rep.add("模型市场 /v1/market/models", True,
            f"共 {len(models)} 个模型，其中 opus 系 {len(rows)} 个",
            {"opus_models": rows, "all_ids": [m.get("id") for m in models]})
    for r_ in rows:
        print(f"       · {r_['id']}  ctx={r_['context_length']} "
              f"out={r_['max_tokens']} reasoning={r_['reasoning']} "
              f"schema={r_['schema_output']} cache={r_['content_cache']}")
    return rows


# ------------------------------------------------------- /v1/messages 能力梯

def make_client(base_url, key, mode):
    """mode: 'auth_token' → Authorization: Bearer；'api_key' → x-api-key。

    七牛官方写的是 ANTHROPIC_AUTH_TOKEN，即 Bearer；但 SDK 默认走 x-api-key，
    两种都试一遍，省得把「认证方式选错」误判成「网关不支持」。
    """
    import anthropic
    # SDK 会从环境变量兜底取凭据，先摘掉，避免误用到真实的 Anthropic key
    for var in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
        os.environ.pop(var, None)
    kw = {"base_url": base_url, "timeout": 600.0, "max_retries": 1}
    if mode == "auth_token":
        kw["auth_token"] = key
    else:
        kw["api_key"] = key
    return anthropic.Anthropic(**kw)


def try_call(client, **kw):
    """跑一次 messages.create，返回 (响应, 异常)。"""
    kw.setdefault("max_tokens", PROBE_MAX_TOKENS)
    kw.setdefault("messages", [{"role": "user", "content": "回复两个字：收到"}])
    try:
        return client.messages.create(**kw), None
    except Exception as e:
        return None, e


def probe_auth_and_native(rep, base_url, key, model):
    """最小可用性：原生 /v1/messages 通不通，以及该用哪种认证头。"""
    import anthropic
    last = None
    for mode in ("auth_token", "api_key"):
        client = make_client(base_url, key, mode)
        resp, err = try_call(client, model=model)
        if resp is not None:
            rep.add("Anthropic 原生 /v1/messages", True,
                    f"认证方式 {mode}，stop_reason={resp.stop_reason}",
                    {"auth_mode": mode,
                     "usage": {"in": resp.usage.input_tokens,
                               "out": resp.usage.output_tokens}})
            return client, mode
        last = err
        if not isinstance(err, (anthropic.AuthenticationError,
                                anthropic.PermissionDeniedError)):
            break        # 不是认证问题，换个头也没用
    rep.add("Anthropic 原生 /v1/messages", False,
            f"{type(last).__name__}: {short(last)}")
    return None, None


def probe_feature(rep, client, model, name, **extra):
    """探一项可选能力：能过就是支持，400 就是网关没透传。"""
    import anthropic
    resp, err = try_call(client, model=model, **extra)
    if resp is not None:
        return rep.add(name, True, "", {"stop_reason": resp.stop_reason})
    kind = type(err).__name__
    if isinstance(err, anthropic.BadRequestError):
        return rep.add(name, False, f"网关拒绝：{short(err, 260)}")
    return rep.add(name, False, f"{kind}: {short(err, 260)}")


def probe_cache(rep, client, model):
    """缓存要看 usage 里有没有 cache_creation/read 字段，不能只看请求没报错。"""
    big = "你是一个加密货币波段分析助手。" * 200      # 凑够最小可缓存长度
    resp, err = try_call(
        client, model=model,
        system=[{"type": "text", "text": big,
                 "cache_control": {"type": "ephemeral"}}])
    if err is not None:
        return rep.add("提示词缓存 cache_control", False, short(err, 260))
    u = resp.usage
    created = getattr(u, "cache_creation_input_tokens", None)
    read = getattr(u, "cache_read_input_tokens", None)
    if created or read:
        return rep.add("提示词缓存 cache_control", True,
                       f"cache_creation={created} cache_read={read}")
    return rep.add("提示词缓存 cache_control", None,
                   "请求未报错，但 usage 无 cache_* 字段——很可能被网关静默丢弃")


def probe_server_tools(rep, client, model):
    """服务端工具。skill 靠它抓宏观/链上数据，这项没有就得改架构。"""
    ladder = [
        ("web_search_20260209", "web_search"),
        ("web_search_20250305", "web_search"),
    ]
    import anthropic
    for tool_type, tool_name in ladder:
        resp, err = try_call(
            client, model=model, max_tokens=512,
            messages=[{"role": "user", "content": "用一句话说明比特币今天的价格大概是多少，必须联网检索。"}],
            tools=[{"type": tool_type, "name": tool_name, "max_uses": 1}])
        if resp is not None:
            used = any(getattr(b, "type", "").startswith("server_tool")
                       or getattr(b, "type", "") == "web_search_tool_result"
                       for b in resp.content)
            return rep.add("服务端工具 web_search", True,
                           f"{tool_type}，实际发起检索={used}")
        if not isinstance(err, anthropic.BadRequestError):
            return rep.add("服务端工具 web_search", False,
                           f"{type(err).__name__}: {short(err, 260)}")
    return rep.add("服务端工具 web_search", False,
                   f"网关拒绝全部版本：{short(err, 260)}")


def probe_capabilities(rep, client, model):
    probe_feature(rep, client, model, "深度思考 thinking(adaptive)",
                  max_tokens=2048, thinking={"type": "adaptive"})
    if not rep.ok("深度思考 thinking(adaptive)"):
        probe_feature(rep, client, model, "深度思考 thinking(enabled)",
                      max_tokens=2048,
                      thinking={"type": "enabled", "budget_tokens": 1024})

    tiny_schema = {"type": "object", "properties": {"ok": {"type": "boolean"}},
                   "required": ["ok"], "additionalProperties": False}
    probe_feature(rep, client, model, "结构化输出 output_config(effort+schema)",
                  output_config={"effort": "high",
                                 "format": {"type": "json_schema",
                                            "schema": tiny_schema}})
    if not rep.ok("结构化输出 output_config(effort+schema)"):
        probe_feature(rep, client, model, "结构化输出 output_config(schema)",
                      output_config={"format": {"type": "json_schema",
                                                "schema": tiny_schema}})
    probe_cache(rep, client, model)
    probe_server_tools(rep, client, model)


# ------------------------------------------------------------ 真实 skill 调用

def run_real_skill(rep, client, model, max_out):
    """按探测结果拼一份「网关能接受的最大能力集」，跑一次真实 skill。"""
    from analyzer.ai.prompt import build_system, build_user, load_skill
    from analyzer.ai.schema import OUTPUT_SCHEMA
    from analyzer.playbook_schema import SCHEMA_VERSION, validate

    mech_path = ROOT / "docs/data/latest.json"
    if not mech_path.exists():
        return rep.add("真实 skill 调用", False,
                       "缺少 docs/data/latest.json，请先跑 python3 run_analysis.py")
    mech = json.loads(mech_path.read_text(encoding="utf-8"))

    skill, template = load_skill()
    system_text = build_system(skill, template)
    user_text = build_user(mech)

    kw = {"model": model, "max_tokens": max_out,
          "messages": [{"role": "user", "content": user_text}]}
    if rep.ok("提示词缓存 cache_control"):
        kw["system"] = [{"type": "text", "text": system_text,
                         "cache_control": {"type": "ephemeral"}}]
    else:
        kw["system"] = system_text
    if rep.ok("深度思考 thinking(adaptive)"):
        kw["thinking"] = {"type": "adaptive"}
    elif rep.ok("深度思考 thinking(enabled)"):
        kw["thinking"] = {"type": "enabled", "budget_tokens": 8000}
    if rep.ok("结构化输出 output_config(effort+schema)"):
        kw["output_config"] = {"effort": "high",
                               "format": {"type": "json_schema",
                                          "schema": OUTPUT_SCHEMA}}
    elif rep.ok("结构化输出 output_config(schema)"):
        kw["output_config"] = {"format": {"type": "json_schema",
                                          "schema": OUTPUT_SCHEMA}}
    if rep.ok("服务端工具 web_search"):
        kw["tools"] = [{"type": "web_search_20260209", "name": "web_search",
                        "max_uses": 15}]

    enabled = [k for k in ("thinking", "output_config", "tools") if k in kw]
    if isinstance(kw["system"], list):
        enabled.append("cache_control")
    print(f"\n  → 本次实际启用：{'、'.join(enabled) or '仅基础参数'}")
    print(f"  → max_tokens={max_out}，system {len(system_text)} 字符，"
          f"user {len(user_text)} 字符（这一步会真的花额度）")

    t0 = time.time()
    try:
        with client.messages.stream(**kw) as stream:
            resp = stream.get_final_message()
    except Exception as e:
        return rep.add("真实 skill 调用", False,
                       f"{type(e).__name__}: {short(e, 600)}")
    elapsed = round(time.time() - t0, 1)

    u = resp.usage
    usage = {"input_tokens": getattr(u, "input_tokens", 0),
             "output_tokens": getattr(u, "output_tokens", 0),
             "cache_read": getattr(u, "cache_read_input_tokens", None),
             "cache_creation": getattr(u, "cache_creation_input_tokens", None),
             "elapsed_s": elapsed, "stop_reason": resp.stop_reason}

    text = ""
    for block in reversed(resp.content):
        if getattr(block, "type", None) == "text" and block.text.strip():
            text = block.text
            break
    if not text:
        return rep.add("真实 skill 调用", False, "响应里没有文本块", usage)
    try:
        payload = json.loads(text)
    except ValueError as e:
        (ROOT / "qiniu-probe-raw.txt").write_text(text, encoding="utf-8")
        return rep.add("真实 skill 调用", None,
                       f"调用成功但输出不是合法 JSON（已存 qiniu-probe-raw.txt）：{e}",
                       usage)

    rep.add("真实 skill 调用", True,
            f"{elapsed}s，输出 {usage['output_tokens']} tokens", usage)

    plan = {"schema_version": SCHEMA_VERSION,
            "generated_at_utc": dt.datetime.now(dt.timezone.utc)
                .isoformat(timespec="seconds").replace("+00:00", "Z"),
            "source": f"qiniu-probe · {model}",
            "report_date": mech.get("date"),
            "price_anchor": payload.get("price_anchor"),
            "coins": payload.get("coins")}
    ok, errs = validate(plan)
    (ROOT / "qiniu-probe-playbook.json").write_text(
        json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    if not ok:
        return rep.add("契约校验 playbook_schema", False,
                       f"{len(errs)} 项不通过：" + "；".join(errs[:6]),
                       {"errors": errs})
    return rep.add("契约校验 playbook_schema", True,
                   "产物可直接落地 docs/data/playbook.json")


# ------------------------------------------------------------------- 结论

def verdict(rep):
    print("\n" + "=" * 68)
    if not rep.ok("Anthropic 原生 /v1/messages"):
        print("结论：原生 /v1/messages 走不通，这条路当前不可用。")
        print("      可退而求其次改走 /v1/chat/completions（OpenAI 兼容），")
        print("      但那需要重写 runner.py 的调用层。")
        return
    lost = [n for n in ("深度思考 thinking(adaptive)", "深度思考 thinking(enabled)",
                        "结构化输出 output_config(effort+schema)",
                        "结构化输出 output_config(schema)",
                        "提示词缓存 cache_control", "服务端工具 web_search")
            if rep.get(n) and not rep.ok(n)]
    print("结论：原生协议可用。")
    if rep.ok("服务端工具 web_search"):
        print("      服务端检索可用 —— skill 能完整执行，runner.py 只需换 base_url。")
    else:
        print("      ⚠ 服务端检索不可用 —— skill 里依赖联网抓宏观/链上数据的步骤")
        print("        无法执行，需要把这部分数据改由机械层预先抓好再喂进去。")
    if lost:
        print("      未透传的能力：" + "、".join(lost))
    if rep.ok("契约校验 playbook_schema"):
        print("      真实 skill 调用产物通过仓库契约校验 —— 免费额度可以跑通全流程。")
    print("=" * 68)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default=os.environ.get("QINIU_BASE_URL", CN),
                    help=f"默认 {CN}；海外线路用 {INTL}")
    ap.add_argument("--model", default=os.environ.get("QINIU_MODEL"),
                    help="不指定则从模型市场里挑一个 opus")
    ap.add_argument("--skip-skill", action="store_true", help="只探能力，不跑真实 skill")
    ap.add_argument("--out", default="qiniu-probe-report.json")
    args = ap.parse_args()

    key = (os.environ.get("QINIU_API_KEY") or "").strip()
    if not key:
        print("请先设置 QINIU_API_KEY（七牛控制台的 key，不是 Anthropic 的 sk-ant-…）")
        return 78

    rep = Report(args.base_url)
    print(f"线路 {args.base_url}   key 长度 {len(key)}\n")

    print("[1/4] 额度与 key")
    probe_quota(rep, key)

    print("\n[2/4] 模型清单")
    opus = probe_models(rep, key) or []
    model = args.model
    max_out = 8192
    if not model:
        if not opus:
            print("      未从市场拿到 opus 模型，请用 --model 指定")
            model = "claude-opus-4-5"
        else:
            picked = sorted(opus, key=lambda m: m["id"])[-1]
            model, max_out = picked["id"], picked["max_tokens"] or 8192
    else:
        hit = next((m for m in opus if m["id"] == model), None)
        if hit:
            max_out = hit["max_tokens"] or 8192
    print(f"      使用模型：{model}（max_tokens={max_out}）")

    print("\n[3/4] 能力探测")
    client, _mode = probe_auth_and_native(rep, args.base_url, key, model)
    if client is not None:
        probe_capabilities(rep, client, model)

    print("\n[4/4] 真实 skill 调用")
    if args.skip_skill:
        print("      已跳过（--skip-skill）")
    elif client is None:
        print("      前置探测未通过，跳过")
    else:
        run_real_skill(rep, client, model,
                       min(max_out, 32000) if max_out else 8192)

    verdict(rep)
    out = pathlib.Path(args.out)
    out.write_text(json.dumps(
        {"base_url": rep.base_url, "started_utc": rep.started,
         "model": model, "steps": rep.steps},
        ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n完整报告已写入 {out}（已脱敏，可直接贴回来）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
