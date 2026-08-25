# crypto-swing-web

加密波段量化面板：BTC / ETH / SOL 的多周期（4H/日/周/月）纯量化分析，每天由 GitHub Actions 自动运行一次，
结果以 Markdown 报告 + JSON 数据提交回仓库，并通过 GitHub Pages 展示。

> 本项目由 [crypto-swing-analysis skill] 的量化部分转化而来。**不含 AI/人工判断**——
> 行情性质判定、信号灯均为机械规则输出，不构成投资建议。

## 功能

- **加权评分体系**（对齐 Pine Script v2.2）：SRSI 25 / MACD 25 / MFI 20 / Volume 15 / SuperTrend 15，辅以 ADX；
  ≥70 做多信号，≤30 离场信号，其余观望
- **多周期支撑/阻力**：分形摆动点 + EMA(20/50/100/200) + 心理整数关口，多依据聚类，每个点位带来源标注
- **BTC 周期估值锚**：200 周均线比值、幂律走廊近似位置、MVRV（CoinMetrics 社区版）
- **宏观与风险偏好**：美股三大指数、加密恐惧贪婪指数、BTC/ETH 现货 ETF 日度净流向（Farside，尽力抓取）
- **规则化行情性质判定**：加密 vs 美股四象限（脱钩/共振），ETF 流向确认注记
- **与上次报告对照**：价格 / 评分 / 信号变化速查表

## 数据源（多级降级，任一失败不中断）

K线：Kraken（主，美国 IP 可用）→ Coinbase → Binance → CoinGecko。
辅助：alternative.me（恐惧贪婪）、CoinMetrics 社区版（MVRV）、Yahoo Finance（美股）、Farside（ETF）。
缺失的数据会在报告和面板中明确标注。

## 运行

```bash
pip install -r requirements.txt lxml
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

## 免责声明

本项目输出为纯量化指标快照，不含任何主观判断，不构成投资建议。数字资产风险极高，可能损失全部本金。
下单前请以交易所实时行情二次确认。
