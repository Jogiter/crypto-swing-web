# -*- coding: utf-8 -*-
"""简体中文 Markdown 报告生成（纯量化版，格式对齐 crypto-swing-analysis skill 的报告骨架）。"""

TF_NAME = {"4h": "4H", "1d": "日线", "1w": "周线", "1M": "月线"}


def _fmt_price(p):
    if p is None:
        return "—"
    return f"{p:,.0f}" if p >= 1000 else f"{p:,.2f}"


def _levels_lines(levels):
    if not levels:
        return "- 数据缺失\n"
    out = ""
    res = levels.get("resistances", [])
    sup = levels.get("supports", [])
    if res:
        out += "  - 阻力：" + " / ".join(f"**{_fmt_price(x['price'])}**（{x['basis']}）" for x in res) + "\n"
    if sup:
        out += "  - 支撑：" + " / ".join(f"**{_fmt_price(x['price'])}**（{x['basis']}）" for x in sup) + "\n"
    return out or "- 无有效点位\n"


def _score_table(sc):
    if not sc:
        return "（评分数据缺失）\n"
    rows = [
        ("SRSI", sc["srsi"]["score"], 25,
         f"K={sc['srsi']['k']} D={sc['srsi']['d']}"),
        ("MACD", sc["macd"]["score"], 25,
         f"DIF={sc['macd']['dif']} DEA={sc['macd']['dea']} 柱={sc['macd']['hist']}"),
        ("MFI", sc["mfi"]["score"], 20,
         f"MFI={sc['mfi'].get('value', '—')}" + ("（" + sc["mfi"]["note"] + "）" if "note" in sc["mfi"] else "")),
        ("Volume", sc["volume"]["score"], 15,
         f"量/20均量={sc['volume'].get('ratio_vs_ma20', '—')}"),
        ("SuperTrend", sc["supertrend"]["score"], 15,
         f"方向：{sc['supertrend']['direction']}，趋势线 {_fmt_price(sc['supertrend'].get('line'))}"),
    ]
    t = "| 组件 | 得分 | 满分 | 状态 |\n|---|---:|---:|---|\n"
    for name, s, m, note in rows:
        t += f"| {name} | {s} | {m} | {note} |\n"
    t += f"| **合计** | **{sc['total']}** | 100 | **{sc['signal']}**（ADX={sc['adx']['value']}，{sc['adx']['trend_strength']}） |\n"
    return t


def _signal_emoji(sig):
    return {"做多信号": "🟢", "离场信号": "🔴", "观望": "🟡"}.get(sig, "⚪")


def _geometry_block(g):
    """渲染交易几何（纯计算，不含方向判断）。"""
    if not g:
        return ""
    out = ["\n**交易几何（纯计算 · 非入场建议）**\n"]
    if g.get("entry_ref"):
        out.append(f"- 参考入场：**${_fmt_price(g['entry_ref'])}**（{g.get('entry_basis', '')}）")
    for st in g.get("stops", []):
        d = st.get("distance_pct")
        dist = f"，距现价 {d:+.2f}%" if d is not None else ""
        out.append(f"- {st['name']}：**${_fmt_price(st['price'])}**（{st.get('basis', '')}{dist}）")
    for t in g.get("targets", []):
        d = t.get("distance_pct")
        dist = f"，距现价 {d:+.2f}%" if d is not None else ""
        out.append(f"- {t['name']}：**${_fmt_price(t['price'])}**（{t.get('basis', '')}{dist}）— {t.get('plan', '')}")
    rr = g.get("rr") or {}
    if rr:
        out.append("- **风险回报比**：" + "，".join(f"{k} **{v}:1**" for k, v in rr.items()))
        if g.get("rr_basis"):
            out.append(f"  - {g['rr_basis']}")
    for n in g.get("notes", []):
        out.append(f"- ⚠️ {n}")
    return "\n".join(out) + "\n"


def _conditions_section(tc):
    """渲染前瞻性触发阈值。"""
    if not tc or (not tc.get("offense") and not tc.get("defense")):
        return ""
    out = ["\n---\n\n## 触发阈值（前瞻条件 · 盯这些才改变决策）\n"]
    for key, title, icon in [("offense", "转进攻条件", "🟢"), ("defense", "转防御条件", "🔴")]:
        items = tc.get(key) or []
        if not items:
            continue
        out.append(f"\n### {icon} {title}\n")
        out.append("| 条件 | 状态 | 当前 | 差距 |")
        out.append("|---|:--:|---|---|")
        for c in items:
            mark = "✅ 已满足" if c["met"] else "○ 未满足"
            out.append(f"| {c['condition']} | {mark} | {c.get('current', '—')} | {c.get('gap') or '—'} |")
    return "\n".join(out) + "\n"


def build_report(d):
    md = []
    md.append(f"# 加密波段量化快照 · {d['date']}\n")
    md.append(f"> **数据采集时间**：{d['generated_at_beijing']}（UTC: {d['generated_at_utc']}）\n>\n"
              "> **生成方式**：GitHub Actions 定时任务全自动运行，纯量化脚本计算，**不含 AI/人工判断**。"
              "行情性质判定与信号灯均为机械规则输出。\n")

    # 数据来源
    src_notes = []
    for coin, cd in d["coins"].items():
        s = cd.get("sources", {})
        if s:
            uniq = sorted(set(s.values()))
            src_notes.append(f"{coin}: {'/'.join(uniq)}")
    md.append("> **K线来源**：" + "；".join(src_notes) +
              "。辅助数据：alternative.me（恐惧贪婪）、CoinMetrics 社区版（MVRV/Z-Score）、colintalkscrypto（CBBI）、Yahoo Finance（美股）、Farside（ETF，尽力抓取）。\n")

    # TL;DR
    md.append("\n## TL;DR（规则生成）\n")
    regime = d["macro"].get("regime", {})
    tldr = []
    btc = d["coins"].get("BTC", {})
    s4 = btc.get("frames", {}).get("4h", {}).get("score", {})
    if s4:
        tldr.append(f"{_signal_emoji(s4['signal'])} BTC 4H 加权评分 **{s4['total']}/100 → {s4['signal']}**"
                    f"（≥70 做多 / ≤30 离场）")
    if regime:
        tldr.append(f"📊 行情性质（规则判定）：**{regime.get('name')}** —— {regime.get('action')}")
    if regime.get("etf_note"):
        tldr.append(f"💰 {regime['etf_note']}")
    for line in tldr[:3]:
        md.append(f"- {line}")

    # 与上次对照
    pc = d.get("prev_compare")
    if pc and pc.get("items"):
        md.append(f"\n## 与上次报告对照（{pc['prev_date']}）\n")
        md.append("| 币种 | 上次价格 | 本次价格 | 涨跌 | 上次评分 | 本次评分 | 信号变化 |")
        md.append("|---|---:|---:|---:|---:|---:|---|")
        for it in pc["items"]:
            sig = f"{it['signal_prev']} → {it['signal_now']}" if it["signal_prev"] != it["signal_now"] else it["signal_now"]
            md.append(f"| {it['coin']} | {_fmt_price(it['price_prev'])} | {_fmt_price(it['price_now'])} "
                      f"| {it['price_chg_pct']:+.2f}% | {it.get('score_prev', '—')} | {it.get('score_now', '—')} | {sig} |")

    # 各币种
    for coin in ["BTC", "ETH", "SOL"]:
        cd = d["coins"].get(coin)
        if not cd:
            continue
        md.append(f"\n---\n\n## {coin}\n")
        spot = cd.get("spot", {})
        chg = spot.get("change_24h_pct")
        chg_s = f"（24h {chg:+.2f}%）" if chg is not None else ""
        md.append(f"**现价**：${_fmt_price(cd.get('price'))} {chg_s}"
                  f"{'　5日 ' + format(cd['change_5d_pct'], '+.2f') + '%' if cd.get('change_5d_pct') is not None else ''}\n")

        for tf in ["4h", "1d", "1w", "1M"]:
            fr = cd.get("frames", {}).get(tf)
            if not fr:
                md.append(f"### {TF_NAME[tf]}\n\n- ⚠️ 数据缺失\n")
                continue
            sc = fr.get("score", {})
            md.append(f"### {TF_NAME[tf]}　{_signal_emoji(sc.get('signal'))} {sc.get('total', '—')}/100\n")
            md.append(_levels_lines(fr.get("levels")))
            if tf == "4h":
                md.append("\n" + _score_table(sc))
                geo = _geometry_block(cd.get("geometry"))
                if geo:
                    md.append(geo)
            else:
                st = sc.get("supertrend", {})
                md.append(f"  - 指标概要：MACD柱 {sc.get('macd', {}).get('hist', '—')}，"
                          f"SRSI K={sc.get('srsi', {}).get('k', '—')}，"
                          f"SuperTrend {st.get('direction', '—')}，"
                          f"ADX {sc.get('adx', {}).get('value', '—')}（{sc.get('adx', {}).get('trend_strength', '')}）\n")

    # BTC 周期估值
    cyc = d.get("btc_cycle", {})
    if cyc:
        md.append("\n---\n\n## BTC 周期估值锚\n")
        if cyc.get("ma200w"):
            r = cyc.get("price_over_ma200w")
            md.append(f"- **200周均线**：${_fmt_price(cyc['ma200w'])}，价格/200周均线 = **{r}**"
                      f"{'（低于200周均线，历史级深值区）' if r and r < 1 else ''}")
        pl = cyc.get("power_law")
        if pl:
            md.append(f"- **幂律走廊（近似）**：下轨 ${_fmt_price(pl['support'])} / 中轨 ${_fmt_price(pl['center'])} / "
                      f"泡沫带 ${_fmt_price(pl['top'])}，当前位于走廊 **{pl['position_pct']}%** 处（0=下轨）。{pl['note']}")
        mv = cyc.get("mvrv")
        if mv:
            zone = mv.get("zone", "")
            icon = "⚠️ " if "过热" in zone else ("🟦 " if "底部" in zone else "")
            parts = [f"- **MVRV**：{mv['value']}（{mv['date']}，CoinMetrics）→ {icon}{zone}"]
            if mv.get("percentile") is not None:
                parts.append(f"历史分位 **{mv['percentile']}%**"
                             f"（{mv.get('history_days', '—')} 天样本）")
            if mv.get("zscore") is not None:
                parts.append(f"**MVRV Z-Score {mv['zscore']}**（{mv.get('zscore_zone', '')}）")
            md.append("；".join(parts))
        cb = cyc.get("cbbi")
        if cb:
            zone = cb.get("zone", "")
            icon = "⚠️ " if "顶部" in zone else ("🟦 " if "底部" in zone else "")
            line = f"- **CBBI**：{cb['value']}（{cb.get('date', '')}）→ {icon}{zone}"
            if cb.get("change_30d") is not None:
                line += f"；30日变化 {cb['change_30d']:+.1f}（30日前 {cb.get('value_30d_ago')}）"
            md.append(line)

    # 宏观
    md.append("\n---\n\n## 美股与风险偏好\n")
    idx = d["macro"].get("indices")
    if idx:
        md.append("| 指数 | 收盘 | 1日 | 5日 |")
        md.append("|---|---:|---:|---:|")
        for name, label in [("SP500", "标普500"), ("NASDAQ", "纳指"), ("DOW", "道指")]:
            v = idx.get(name)
            if v:
                md.append(f"| {label} | {v['last']:,} | {v['change_1d_pct']:+.2f}% | {v['change_5d_pct']:+.2f}% |")
    else:
        md.append("- ⚠️ 美股数据缺失")
    fg = d["macro"].get("fear_greed")
    if fg:
        md.append(f"\n- **加密恐惧贪婪指数**：{fg['value']}（{fg['label']}）")
    etf = d["macro"].get("etf_flows")
    if etf:
        for k, rows in etf.items():
            recent = rows[-5:]
            s = "，".join(f"{x['date']}: {x['total_musd']:+,.0f}M" for x in recent)
            md.append(f"- **{k} 现货 ETF 日度净流向**（Farside）：{s}")
    else:
        md.append("- ⚠️ ETF 流向抓取失败（Farside 防护），请人工查看 farside.co.uk")

    if regime:
        md.append(f"\n### 行情性质判定（规则）\n")
        md.append(f"- 判定：**{regime.get('name')}**（BTC 5日 {regime.get('btc_5d_pct', '—')}% vs 标普 5日 {regime.get('spx_5d_pct', '—')}%）")
        md.append(f"- 含义：{regime.get('meaning')}")
        md.append(f"- 机械应对：{regime.get('action')}")
        md.append(f"- {regime.get('confidence')}")

    # 信号灯（当前状态）
    if d.get("triggers"):
        md.append("\n---\n\n## 信号灯（当前状态快照）\n")
        for t in d["triggers"]:
            md.append(f"- {t}")

    # 触发阈值（前瞻性可验证条件）
    cond_md = _conditions_section(d.get("trigger_conditions"))
    if cond_md:
        md.append(cond_md)

    # 缺失
    if d.get("missing"):
        md.append("\n## ⚠️ 本次缺失的数据\n")
        for m in d["missing"]:
            md.append(f"- {m}")

    md.append("\n---\n\n## 免责声明\n")
    md.append(f"{d['disclaimer']}\n")
    md.append("本工具**不判断是否应当入场、方向偏好或仓位大小**——这些依赖对行情性质的综合判断，"
              "请结合人工分析（或在 Claude 中运行 crypto-swing-analysis skill）自行决策。\n\n"
              "「交易几何」一节是**纯几何计算**：以现价为参考入场点，按已算出的结构位推导止损/目标/风险回报比，"
              "回答的是「若做多，止损该放哪、赔率是多少」，而非「现在该不该做多」。\n\n"
              "纪律提示：止损触发后禁止向下摊平；超卖不构成入场理由；重建仓位需等右侧确认。\n")
    return "\n".join(md)
