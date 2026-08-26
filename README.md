# crypto-swing-web

加密波段量化面板：BTC / ETH / SOL 的多周期（4H/日/周/月）纯量化分析，每天由 GitHub Actions 自动运行一次，
结果以 Markdown 报告 + JSON 数据提交回仓库，并通过 GitHub Pages 展示。

> 本项目由 [crypto-swing-analysis skill] 的量化部分转化而来。**不含 AI/人工判断**——
> 行情性质判定、信号灯均为机械规则输出，不构成投资建议。

## 功能

- **加权评分体系**（对齐 Pine Script v2.2）：SRSI 25 / MACD 25 / MFI 20 / Volume 15 / SuperTrend 15，辅以 ADX；
  ≥70 做多信号，≤30 离场信号，其余观望
- **多周期支撑/阻力**：分形摆动点 + EMA(20/50/100/200) + 心理整数关口，多依据聚类，每个点位带来源标注
- **BTC 周期估值锚**：200 周均线比值、幂律走廊近似位置、**CBBI 周期指数**、
  **MVRV + 历史分位 + MVRV Z-Score**（CoinMetrics 社区版；全历史请求失败时自动降级为仅读数）
- **交易几何**（纯计算，非入场建议）：结构止损 / 最终止损（4H 结构位与 SuperTrend 线取近者，
  留缓冲）、分批止盈 TP1/TP2/TP3、风险回报比（标注入场/止损/目标口径）
- **触发阈值**（前瞻条件）：「转进攻 / 转防御」的可验证条件清单，每条附当前值与**还差多少**——
  回答"接下来盯什么才改变决策"，区别于只讲"现在是什么样"的信号灯
- **宏观与风险偏好**：美股三大指数、加密恐惧贪婪指数、BTC/ETH 现货 ETF 日度净流向（Farside，尽力抓取）
- **规则化行情性质判定**：加密 vs 美股四象限（脱钩/共振），ETF 流向确认注记
- **与上次报告对照**：价格 / 评分 / 信号变化速查表

## 数据源（多级降级，任一失败不中断）

K线：Kraken（主，美国 IP 可用）→ Coinbase → Binance → CoinGecko。
辅助：alternative.me（恐惧贪婪）、CoinMetrics 社区版（MVRV / Z-Score）、colintalkscrypto（CBBI）、Yahoo Finance（美股）、Farside（ETF）。
缺失的数据会在报告和面板中明确标注。

## 运行

```bash
pip install -r requirements-dev.txt lxml
python -m pytest tests/ -q      # 纯逻辑测试，不触网
python run_analysis.py
```

环境变量（可选）：`KLINE_SOURCES=Kraken,CoinGecko` 限定数据源；`HTTP_RETRIES` / `HTTP_BACKOFF` 控制重试。

输出：

- `reports/YYYY-MM-DD.md` — 简体中文日报
- `docs/data/latest.json` / `docs/data/history/*.json` — 结构化数据
- `docs/` — GitHub Pages 静态面板（Settings → Pages → Deploy from branch → `main` `/docs`）

## 定时任务

`.github/workflows/daily-analysis.yml`：每天 00:15 UTC（北京时间 08:15）运行，也可在 Actions 页手动触发
（workflow_dispatch）。运行结果自动 commit 回 `main`。

## 主观层：AI 分析方案

分析本身在本仓库内运行——GitHub Actions 每天先跑机械层，再调 Claude API
执行 `skills/crypto-swing-analysis/`（版本化的分析框架），产出结构化方案与
完整报告，直接提交回仓库。没有外部会话、没有手工搬运。

```
run_analysis.py   → docs/data/latest.json     机械层：K线、指标、支撑阻力、几何
                         ↓ 作为既有事实喂给 AI（明确禁止重算）
run_ai_analysis.py → docs/data/playbook.json  主观层：方向、信心、仓位、分批
                     docs/ai-reports/*.md     完整中文报告
```

AI 只做两件机械层做不到的事：**判断**（方向偏好、信心等级、仓位与分批权重），
以及**抓机械层抓不到的数据**（ETF 流向、链上指标、宏观与 IPO 信号）。K 线与
技术指标直接采信机械层，既省 token，也避免两层数据源不同导致价格对不上。

### 运行与容错

| 情况 | 行为 |
|---|---|
| 未配置 `ANTHROPIC_API_KEY` | 跳过 AI 层，机械层照常发布 |
| key 无效 / 权限不足 | 红色注解 + 状态文件，保留上一版方案 |
| 限流 / 网络 / 服务器错误 | 黄色注解，下次定时任务自动重试 |
| 输出未通过契约校验 | 回喂错误重试一次，仍失败则**不覆盖**已有方案 |
| 本月支出达上限 | 跳过并告警（`MONTHLY_BUDGET_USD`） |

失败在三处可见：Actions 注解、`docs/data/ai-status.json`、以及页面顶部的状态条
（区分「今天没更新」和「今天没跑成」）。

### 安全

- workflow 按 job 拆分权限：**持有 API key 的 job 没有写权限，有写权限的 job 拿不到 key**
- 绝不使用 `pull_request_target`（会在 fork PR 上下文交出 secrets）
- 本仓库是 public，运行日志公开：所有输出经 `redact()` 过滤凭据模式，不打印环境变量
- key 只注入需要它的单个 step

配置：在 Settings → Secrets and variables → Actions 添加 `ANTHROPIC_API_KEY`。
建议用专用 key 并在创建时设置过期时间（过期只能在创建时指定）。



`docs/data/playbook.json` 承载 skill 报告里的做多方案（分批入场、多层止损、
分批止盈、多口径风险回报比、加仓硬条件），页面以「AI 分析方案」区块渲染，
与机械层视觉区分。

它是**可选**的——缺失、损坏或过期都不影响机械层显示。页面按三条线判定时效：
现价跌破最后一层止损即标记作废并摊开机械版兜底；偏离锚定价 ≥5% 或生成超过
48 小时则给出提示。

字段定义见 [PLAYBOOK.md](PLAYBOOK.md)，可执行校验在 `analyzer/playbook_schema.py`。
目前为手工放置的样本（取自 2026-08-26 报告），上游自动写入通道尚未打通。

## 与 crypto-swing-analysis skill 的边界

本项目自动化的是 skill 中**可机械计算**的部分。仍需人工判断（或在 Claude 中运行该 skill）的有：
方向偏好与信心等级、仓位权重、入场时机、暴跌后的应对基调。

「交易几何」一节给出的是**若做多则止损放哪、赔率多少**的几何口径，
**不回答现在该不该做多**——这条边界在报告免责声明中亦有明示。

尚未覆盖的 skill 指标：LTH 供应变化、交易所储备、NUPL / SOPR、清算数据、稳定币总量、
SOL 专属链上指标（质押率 / DEX 量 / TVL）、IPO 风险偏好信号、宏观日历。

## 免责声明

本项目输出为纯量化指标快照，不含任何主观判断，不构成投资建议。数字资产风险极高，可能损失全部本金。
下单前请以交易所实时行情二次确认。
