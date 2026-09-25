# 5802 低频波段模块

## 设计目标

这是对 `daily_stock_analysis` fork 的最小侵入式扩展：

- 不修改核心行情 provider。
- 不修改 `history_loader`。
- 不修改原有 AI 报告。
- 不修改原有 Telegram / Email / Discord 等通知实现。
- 不自动下单。
- 每个交易日收盘后的现有 GitHub Actions 中，额外执行一次确定性规则。
- 正常 `WAIT` 不额外通知。
- 只有新的价格事件触发时，才通过现有 `NotificationService` 的 `alert` 路由发送短消息。

## 规则

| 条件 | 状态 | 含义 |
|---|---|---|
| 收盘跌破 1950 | 🔴 RISK | 停止加仓，重新检查逻辑 |
| 从 2050 上方跌入 2050 以下且仍 ≥1950 | 🟢 LOW_ZONE | 检查第一格 100 股 |
| 收盘上穿 2150 | 🟡 CONFIRM | 如果 MA5 > MA20，检查第二格 100 股 |
| 收盘上穿 2300，且20日量比 ≥1.5x | 🟢 BREAKOUT | 检查第三格 100 股 |
| 首次上穿 2500 | 🟡 TAKE_PROFIT | 检查第一批止盈 |
| 首次上穿 2600 | 🟡 TAKE_PROFIT | 检查剩余仓位止盈 |

> 2050/2150/2200/2300/1950/2500/2600 是本次讨论中的交易计划参数。
> 它们不是模型自动计算出的“公允价值”。

## 本地测试

在仓库根目录：

```bash
python scripts/stock_5802_signal.py
```

只打印，不发送通知。

测试通知：

```bash
python scripts/stock_5802_signal.py --notify
```

## 为什么不直接调用 AkShare？

因为当前 DSA 已经有统一的 `history_loader.load_history_df()`。
它优先读取本地历史数据库，不足时再通过 `DataFetcherManager` 走现有数据源 fallback。

因此这个模块不应该再自己实现 AkShare/YFinance 请求。

## GitHub Actions

在现有 `python main.py ...` 分析步骤之后增加：

```yaml
- name: 5802 低频波段信号
  if: success()
  run: |
    python scripts/stock_5802_signal.py --notify
```

现有 workflow 已经把 Telegram、Email、Discord 等通知环境变量注入 runner，因此无需新增 token。

## 一个重要限制

仓库当前的实时告警中心 worker 只在 `--schedule` 模式持续运行；
默认的 `00-daily-analysis.yml` 是一次性每日分析 workflow。

所以这里故意采用“收盘后每日检查”的模式，而不是尝试在 GitHub Actions 中做 5 分钟轮询。
这更符合本次低频波段计划，也避免额外的常驻服务。
