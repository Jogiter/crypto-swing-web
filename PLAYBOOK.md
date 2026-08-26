# playbook.json 契约

`docs/data/playbook.json` 是**主观层**——由 crypto-swing-analysis skill（或人工）产出的
做多方案，页面读它来渲染「AI 分析方案」区块。

它与仓库里其他数据的关系：

| | 谁产出 | 更新方式 | 含主观判断 |
|---|---|---|---|
| `docs/data/latest.json` | `run_analysis.py` | GitHub Actions 每日自动 | 否 |
| `docs/data/playbook.json` | skill / 人工 | 目前手工放置 | **是** |

页面同时读两者。**主观层缺失、损坏或过期，都不影响机械层正常显示**——
这是刻意的：自动化那条线永远得能独立跑。

## 为什么要契约

skill 报告是写给人看的：

```
第一批 **76,500 – 75,500**（4H SuperTrend 76,125 附近）—— 40% 计划仓
```

页面要把它变成卡片，就得从文字里抠出批次号、区间上下限、仓位比例。而报告由
Opus 每天现写，措辞天然会变——今天「第一批」明天「首批」，破折号可能是 `–` 也
可能是 `-`。靠解析必然静默出错。

所以约定：**上游直接给结构化数据，价格一律是数字，自由文本只放在 `*_note` /
`basis` 里，不参与任何计算。** 措辞怎么变都不影响渲染。

## 字段

顶层：

| 字段 | 类型 | 必填 | 说明 |
|---|---|:--:|---|
| `schema_version` | string | ✓ | 当前 `"1.0"`，不匹配直接判不合格 |
| `generated_at_utc` | string | ✓ | ISO 时间，用于判断方案新鲜度 |
| `source` | string | | 产出方，如 `"crypto-swing-analysis skill · Opus"` |
| `report_date` | string | | 对应报告日期 |
| `price_anchor` | object | ✓ | 各币**生成方案时**的价格，时效判定的基准 |
| `coins` | object | ✓ | 键为 `BTC` / `ETH` / `SOL` |
| `add_conditions` | object | | 全局加仓硬条件 |

`coins.<币种>`：

| 字段 | 类型 | 必填 | 说明 |
|---|---|:--:|---|
| `bias` | string | ✓ | 方向偏好，如 `"做多"` |
| `bias_note` | string | | 修饰，如 `"但优先级最低"` |
| `confidence` | string | ✓ | 只能是 `高` / `中高` / `中` / `低` |
| `confidence_reason` | string | | 定级理由，含扣分项 |
| `entry_batches[]` | array | | `batch` `low` `high` `weight_pct` `basis` `ideal` |
| `breakout_entry` | object | | `condition` `note`——突破式入场替代方案 |
| `stops[]` | array | | `level` `name` `trigger` `price` `basis` `action` |
| `targets[]` | array | | `name` `price` `basis` `reduce_pct` `note` |
| `trailing` | object | | `remain_pct` `note`——剩余转移动止盈 |
| `risk_reward[]` | array | | `label` `entry` `stop` `target` `ratio` `verdict` |
| `rr_note` | string | | RR 的补充说明 |
| `position_pct` | number | ✓ | 占加密波段总仓的百分比（**不是**占总资产） |
| `position_note` | string | | 仓位理由 |

要点：

* `stops[].level` 从 1 递增，1 是最先触发的那层；**每层必须有 `action`**
  （「减半」「减至 1/3」「清仓」），只给价格不给动作，读的人不知道该做什么。
* `risk_reward[].verdict` 可为 `"optimal"`（最优执行）或 `"not_recommended"`
  （如止损过窄易被噪音扫掉），页面会相应标注。
* `entry_batches[].weight_pct` 各批合计须为 100。
* `targets[].reduce_pct` 与 `trailing.remain_pct` 合计须为 100，否则仓位没分完。

## 校验

契约不只是这篇文档，`analyzer/playbook_schema.py` 是它的可执行形式：

```python
from analyzer.playbook_schema import validate
ok, errors = validate(json.load(open("docs/data/playbook.json")))
```

它拦结构错与算不通（区间写反、仓位没分完、RR 与自身数字不符、止损缺动作、
做多方案里止损高于入场……），不判断行情观点对错——那不是程序的事。

`tests/test_playbook_schema.py` 会校验随仓库发布的示例始终合格。

## 时效机制

**过期的操作建议比没有建议更危险**——止损位可能早已失效。页面按三条线判定，
从重到轻：

1. **现价跌破最后一层止损** → 标记作废（红条、置灰、默认折叠），并把机械版
   方案摊开兜底。方案的前提已经不成立。
2. **现价偏离 `price_anchor` ≥ 5%** → 标记「点位需重新评估」（黄条）。
3. **`generated_at_utc` 早于 48 小时** → 标记生成时间。

阈值定义在 `docs/index.html` 顶部的 `DRIFT_WARN_PCT` / `AGE_WARN_HOURS`。

判定按币种独立进行——BTC 作废不影响 ETH / SOL 的方案照常显示。

## 目前的限制

上游还没有自动写入通道（Cowork 会话的 GitHub 推送权限未确认），
所以 `playbook.json` 目前是**手工放置**的一份样本，取自 2026-08-26 的报告。
通道打通后，产出方按本契约生成即可，页面无需改动。
