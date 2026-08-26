# -*- coding: utf-8 -*-
"""组装 system / user prompt。

设计要点：

* **skill 内容作为 system prompt 且置于最前**——它每天完全相同，配合
  cache_control 能吃满 prompt caching，缓存命中率接近 100%。
* **机械层结果作为既有事实喂进去**，并明确禁止重算。仓库脚本已经算好了
  K 线、指标、支撑阻力、几何；让模型再抓一遍既烧 token，又会因为数据源
  不同（脚本走 Kraken，模型走 Yahoo）产生对不上的两套价格。
* **只喂分析需要的字段**。latest.json 有 31KB，其中大半是模型用不到的
  中间量；精简后约 1/4，省下的额度留给 web 检索。
"""
import json
import pathlib

SKILL_DIR = pathlib.Path("skills/crypto-swing-analysis")

# 机械层已覆盖、无需模型再抓的部分
_ALREADY_COVERED = "价格、四周期 K 线指标、支撑阻力、关键翻多线与结构生死线、交易几何"

# 机械层抓不到、必须由模型补的部分
_MUST_FETCH = """- **ETF 资金流**（先行灯，最重要）：BTC / ETH 现货 ETF 近期日度净流向
- **链上与周期指标**：LTH 供应变化、交易所储备、NUPL / SOPR、清算数据、稳定币总量
- **SOL 专项**：质押率、DEX 成交量、原生计价 TVL、生态升级进度
- **宏观与风险偏好**：美股三大指数、AI/科技股与 IPO 温度计、美联储 / 通胀 / 美元
- **关键日历**：议息、CPI/PCE、期权到期、链上升级节点"""


def load_skill():
    """读取仓库内的 skill 文件。它们是版本化的分析框架，改动会进 git 历史。"""
    skill = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    template = (SKILL_DIR / "references" / "report-template.md").read_text(encoding="utf-8")
    return skill, template


def build_system(skill, template):
    return (
        f"{skill}\n\n---\n\n"
        f"# 报告模板\n\n{template}\n\n---\n\n"
        "# 本次运行的执行环境\n\n"
        "你在 GitHub Actions 中无人值守运行，没有人可以追问澄清。因此：\n"
        "- 跳过 skill 里的「第 0 步：澄清」，用户画像按 skill 中的默认假设执行\n"
        "- 数据缺失时不要中断，按 skill 要求在报告中明确标注缺失项\n"
        "- 最终必须输出符合给定 JSON Schema 的结构化结果，不要输出任何额外文字"
    )


def _compact_coin(cd):
    """只保留分析必需的字段。"""
    out = {"price": cd.get("price"), "change_5d_pct": cd.get("change_5d_pct")}
    spot = cd.get("spot") or {}
    if spot.get("change_24h_pct") is not None:
        out["change_24h_pct"] = spot["change_24h_pct"]

    frames = {}
    for tf, fr in (cd.get("frames") or {}).items():
        sc = fr.get("score") or {}
        frames[tf] = {
            "close": fr.get("close"),
            "score_total": sc.get("total"),
            "signal": sc.get("signal"),
            "srsi": {"k": (sc.get("srsi") or {}).get("k"),
                     "d": (sc.get("srsi") or {}).get("d")},
            "macd_hist": (sc.get("macd") or {}).get("hist"),
            "mfi": (sc.get("mfi") or {}).get("value"),
            "volume_ratio": (sc.get("volume") or {}).get("ratio_vs_ma20"),
            "supertrend": sc.get("supertrend"),
            "adx": sc.get("adx"),
            "levels": fr.get("levels"),
        }
        for extra in ("ma200w", "ema20w", "price_over_ma200w", "atr", "atr_pct"):
            if fr.get(extra) is not None:
                frames[tf][extra] = fr[extra]
    out["frames"] = frames
    out["key_pivots"] = cd.get("key_pivots")
    out["geometry"] = cd.get("geometry")
    return out


def build_user(mech, prior_errors=None):
    payload = {
        "generated_at_utc": mech.get("generated_at_utc"),
        "date": mech.get("date"),
        "kline_sources": {c: (cd.get("sources") or {})
                          for c, cd in (mech.get("coins") or {}).items()},
        "coins": {c: _compact_coin(cd) for c, cd in (mech.get("coins") or {}).items()},
        "btc_cycle": mech.get("btc_cycle"),
        "macro": {k: v for k, v in (mech.get("macro") or {}).items()
                  if k in ("indices", "fear_greed", "etf_flows", "regime")},
        "missing": mech.get("missing"),
    }

    parts = [
        f"今天是 {mech.get('date')}（北京时间 {mech.get('generated_at_beijing')}）。",
        "",
        "## 一、本仓库量化脚本刚算出的结果（直接采信）",
        "",
        f"以下数据由本仓库的 `run_analysis.py` 于同一次运行中实时计算，涵盖 {_ALREADY_COVERED}。",
        "**直接采信这些数值，不要重新抓取 K 线、也不要重算这些指标**——重算既浪费检索额度，",
        "又会因数据源不同产生两套对不上的价格。注意 `missing` 字段列出了机械层本次未取到的数据。",
        "",
        "```json",
        json.dumps(payload, ensure_ascii=False, indent=1, default=str),
        "```",
        "",
        "## 二、需要你用 web 工具补齐的部分",
        "",
        _MUST_FETCH,
        "",
        "## 三、你的任务",
        "",
        "在上述机械层数据 + 你检索到的资料之上，按 skill 的框架完成分析，产出：",
        "",
        "1. `report_markdown`：完整的简体中文分析报告，遵循报告模板的骨架，"
        "包含数据来源声明、行情性质判定、多周期技术分析、链上与资金面、美股与风险偏好、"
        "实操建议、动态触发阈值与关键日历、风险提示",
        "2. `coins`：三个币种的结构化做多方案（分批入场、多层止损、分批止盈、多口径 RR、仓位）",
        "3. `add_conditions`：全局加仓硬条件",
        "4. `price_anchor`：你据以制定方案时各币的价格",
        "5. `provenance`：你实际用到的数据来源与数据质量说明",
        "",
        "**结构化字段的约束**（不满足会被程序拒绝，方案不会发布）：",
        "- 各批 `weight_pct` 合计必须为 100",
        "- 各档 `reduce_pct` 与 `trailing.remain_pct` 合计必须为 100",
        "- 每层止损必须有 `action`（减半 / 减至 1/3 / 清仓），`level` 从 1 递增",
        "- 每个 `risk_reward.ratio` 必须等于 (target-entry)/(entry-stop)，自己算准",
        "- 做多方案中 `stop` 必须低于 `entry`，`target` 必须高于 `entry`",
    ]

    if prior_errors:
        parts += [
            "",
            "## ⚠️ 上一次输出未通过校验，请修正后重新输出",
            "",
            *[f"- {e}" for e in prior_errors[:10]],
        ]

    return "\n".join(parts)
