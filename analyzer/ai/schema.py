# -*- coding: utf-8 -*-
"""模型输出的 JSON Schema。

与 analyzer/playbook_schema.py 的分工：
* 本文件约束**模型生成时**的结构（喂给 output_config.format）；
* playbook_schema.validate() 做**落地前**的语义把关。

两者都要有，因为 JSON Schema 表达不了跨字段约束——「各批仓位合计 100」
「RR 要和自己的入场/止损/目标算得上」这类只能靠校验器。

模型不产出 schema_version / generated_at_utc / source：那是元数据，由代码填，
避免模型把时间戳编错或版本号写错。
"""

_NUM = {"type": "number"}
_STR = {"type": "string"}

_ANCHOR = {
    "type": "object",
    "description": "生成方案时各币种的价格，用于页面判断方案是否已被价格走离",
    "properties": {"BTC": _NUM, "ETH": _NUM, "SOL": _NUM},
    "required": ["BTC", "ETH", "SOL"],
}

_ENTRY_BATCH = {
    "type": "object",
    "properties": {
        "batch": {"type": "integer", "description": "批次号，从 1 开始"},
        "low": _NUM,
        "high": _NUM,
        "weight_pct": {"type": "number", "description": "占计划仓比例，各批合计须为 100"},
        "ideal": {"type": "boolean", "description": "是否为理想加仓位"},
        "basis": {"type": "string", "description": "依据，如「斐波 0.236 + 8/21 低点」"},
    },
    "required": ["batch", "low", "high", "weight_pct"],
}

_STOP = {
    "type": "object",
    "properties": {
        "level": {"type": "integer", "description": "1 = 最先触发的那层，依次递增"},
        "name": _STR,
        "trigger": {"type": "string", "description": "触发条件原文，如「日线收盘 < 71,890」"},
        "price": _NUM,
        "basis": _STR,
        "action": {"type": "string", "description": "触发后动作：减半 / 减至 1/3 / 清仓"},
    },
    "required": ["level", "name", "price", "action"],
}

_TARGET = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "description": "TP1 / TP2 / TP3"},
        "price": _NUM,
        "basis": _STR,
        "reduce_pct": {"type": "number", "description": "该档减仓比例"},
        "note": _STR,
    },
    "required": ["name", "price", "reduce_pct"],
}

_RR = {
    "type": "object",
    "properties": {
        "label": {"type": "string", "description": "口径说明，如「第二批入场 + 4H 结构止损」"},
        "entry": _NUM,
        "stop": _NUM,
        "target": _NUM,
        "ratio": {"type": "number", "description": "须等于 (target-entry)/(entry-stop)"},
        "verdict": {"type": "string", "enum": ["optimal", "not_recommended"]},
    },
    "required": ["label", "entry", "stop", "target", "ratio"],
}

_COIN = {
    "type": "object",
    "properties": {
        "bias": {"type": "string", "description": "方向偏好，如「做多」"},
        "bias_note": {"type": "string", "description": "修饰，如「但优先级最低」"},
        "confidence": {"type": "string", "enum": ["高", "中高", "中", "低"]},
        "confidence_reason": {"type": "string", "description": "定级理由，须含扣分项"},
        "entry_batches": {"type": "array", "items": _ENTRY_BATCH},
        "breakout_entry": {
            "type": "object",
            "properties": {"condition": _STR, "note": _STR},
            "required": ["condition"],
        },
        "stops": {"type": "array", "items": _STOP},
        "targets": {"type": "array", "items": _TARGET},
        "trailing": {
            "type": "object",
            "properties": {
                "remain_pct": {"type": "number",
                               "description": "与各档 reduce_pct 合计须为 100"},
                "note": _STR,
            },
            "required": ["remain_pct"],
        },
        "risk_reward": {"type": "array", "items": _RR},
        "rr_note": _STR,
        "position_pct": {"type": "number", "description": "占加密波段总仓比例，非占总资产"},
        "position_note": _STR,
    },
    "required": ["bias", "confidence", "position_pct"],
}

_SOURCE_ENTRY = {
    "type": "object",
    "properties": {
        "category": {"type": "string", "description": "如「ETF 资金流」"},
        "providers": {"type": "array", "items": _STR},
        "as_of": {"type": "string", "description": "该来源数据的截止时间"},
    },
    "required": ["category", "providers"],
}

OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "price_anchor": _ANCHOR,
        "coins": {
            "type": "object",
            "properties": {"BTC": _COIN, "ETH": _COIN, "SOL": _COIN},
            "required": ["BTC", "ETH", "SOL"],
        },
        "add_conditions": {
            "type": "object",
            "properties": {
                "require": {"type": "string", "description": "如「缺一不可」"},
                "goal": _STR,
                "items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {"condition": _STR, "note": _STR},
                        "required": ["condition"],
                    },
                },
                "partial_note": _STR,
            },
            "required": ["items"],
        },
        "provenance": {
            "type": "object",
            "description": "本次分析实际用到的数据来源",
            "properties": {
                "sources": {"type": "array", "items": _SOURCE_ENTRY},
                "caveats": {
                    "type": "array",
                    "description": "数据质量说明，如某源不可达、某 bar 缺失如何重建",
                    "items": {
                        "type": "object",
                        "properties": {
                            "severity": {"type": "string", "enum": ["info", "warn"]},
                            "text": _STR,
                        },
                        "required": ["severity", "text"],
                    },
                },
            },
        },
        "report_markdown": {
            "type": "string",
            "description": "完整的简体中文 Markdown 分析报告，按 report-template 的骨架",
        },
    },
    "required": ["price_anchor", "coins", "report_markdown"],
}
