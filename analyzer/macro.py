# -*- coding: utf-8 -*-
"""规则化行情性质判定（机械版第 2 步）：加密 vs 美股的四象限 + ETF 流向确认。

注意：这是纯规则判定，只回答“数据落在哪个象限”，不做叙事性宏观解读。
阈值：美股 5 日 ±1.0% 为平/涨/跌界；BTC 5 日 ±3.0% 为平/涨/跌界。
"""

REGIMES = {
    "decouple_down": {
        "name": "加密向下脱钩",
        "meaning": "加密内部资金外流/结构抛压，不能指望宏观缓解带来反弹",
        "action": "防御，等 ETF 流向反转",
    },
    "risk_off": {
        "name": "宏观 risk-off 共振",
        "meaning": "外部冲击主导，冲击缓解则同步修复",
        "action": "盯宏观催化剂，可在恐慌底分批（需右侧确认）",
    },
    "decouple_up": {
        "name": "加密独立走强",
        "meaning": "加密内部资金回流（ETF 转流入常为先导）",
        "action": "顺势持有/加仓（按纪律分批）",
    },
    "risk_on": {
        "name": "风险偏好共振上行",
        "meaning": "最佳做多环境",
        "action": "正常执行多头策略",
    },
    "neutral": {
        "name": "无明显方向（震荡）",
        "meaning": "加密与美股均未走出显著方向",
        "action": "以 4H 信号灯为准，轻仓或观望",
    },
}


def classify(btc_5d_pct, spx_5d_pct, etf_recent=None):
    """btc_5d_pct/spx_5d_pct: 5 日涨跌幅（%）。etf_recent: 近几日 BTC ETF 净流向列表（百万美元）。"""
    if btc_5d_pct is None or spx_5d_pct is None:
        key = "neutral"
        confidence = "低（数据缺失）"
    else:
        btc_dir = 1 if btc_5d_pct > 3 else (-1 if btc_5d_pct < -3 else 0)
        spx_dir = 1 if spx_5d_pct > 1 else (-1 if spx_5d_pct < -1 else 0)
        if btc_dir == -1 and spx_dir >= 0:
            key = "decouple_down"
        elif btc_dir == -1 and spx_dir == -1:
            key = "risk_off"
        elif btc_dir == 1 and spx_dir <= 0:
            key = "decouple_up"
        elif btc_dir == 1 and spx_dir == 1:
            key = "risk_on"
        else:
            key = "neutral"
        confidence = "规则判定（阈值：BTC±3%/SPX±1%，5日）"

    etf_note = None
    if etf_recent:
        vals = [x["total_musd"] for x in etf_recent[-3:]]
        if all(v < 0 for v in vals):
            etf_note = "BTC ETF 近3日连续净流出——做多环境恶化信号"
        elif all(v > 0 for v in vals):
            etf_note = "BTC ETF 近3日连续净流入——环境改善信号"
        else:
            etf_note = "BTC ETF 流向反复，方向未确认"

    r = dict(REGIMES[key])
    r.update({"key": key, "confidence": confidence, "etf_note": etf_note,
              "btc_5d_pct": btc_5d_pct, "spx_5d_pct": spx_5d_pct})
    return r
