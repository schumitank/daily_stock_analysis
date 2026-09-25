#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
5802 (Sumitomo Electric) low-frequency swing signal.

Purpose:
- Reuse daily_stock_analysis's existing history/data-source layer.
- Evaluate the user's 100万円 / 3x100-share swing plan deterministically.
- NEVER place an order.
- Print a compact status every run.
- Optionally send a short alert through the repository's existing
  NotificationService, using the existing "alert" notification route.

Plan:
  1) <= 2050 after a rebound/stop attempt -> inspect first 100 shares
  2) 2150~2200 with MA5 > MA20 -> inspect second 100 shares
  3) cross >= 2300 with volume >= 1.5x 20-day average -> inspect third 100 shares
  4) < 1950 -> stop adding / re-evaluate
  5) cross >= 2500 -> inspect first profit-taking
  6) cross >= 2600 -> inspect further profit-taking

These are mechanical rules, not investment advice and not automatic orders.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

# Make imports work when running:
#   python scripts/stock_5802_signal.py
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pandas as pd

from src.services.history_loader import load_history_df
from src.notification import NotificationService


CODE = "5802.T"


@dataclass(frozen=True)
class Plan:
    low_entry: float = 2050.0
    confirm_low: float = 2150.0
    confirm_high: float = 2200.0
    breakout: float = 2300.0
    risk: float = 1950.0
    take_profit_1: float = 2500.0
    take_profit_2: float = 2600.0
    volume_confirm: float = 1.5


def normalize_history(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()

    if "date" not in out.columns:
        if isinstance(out.index, pd.DatetimeIndex):
            out["date"] = out.index
        else:
            raise ValueError("Historical data has no 'date' column.")

    out["date"] = pd.to_datetime(out["date"], errors="coerce")

    for column in ("close", "high", "low", "volume"):
        if column in out.columns:
            out[column] = pd.to_numeric(out[column], errors="coerce")

    out = (
        out.dropna(subset=["date", "close"])
        .sort_values("date")
        .drop_duplicates(subset=["date"], keep="last")
        .reset_index(drop=True)
    )

    out["ma5"] = out["close"].rolling(5).mean()
    out["ma20"] = out["close"].rolling(20).mean()

    if "volume" in out.columns:
        out["volume20"] = out["volume"].rolling(20).mean()
        out["volume_ratio20"] = out["volume"] / out["volume20"]
    else:
        out["volume_ratio20"] = pd.NA

    return out


def evaluate(df: pd.DataFrame, plan: Plan = Plan()) -> dict:
    df = normalize_history(df)

    if len(df) < 20:
        raise RuntimeError(
            f"{CODE}: insufficient history ({len(df)} rows; need >= 20)."
        )

    last = df.iloc[-1]
    previous = df.iloc[-2]

    close = float(last["close"])
    previous_close = float(previous["close"])
    ma5 = float(last["ma5"])
    ma20 = float(last["ma20"])

    volume_ratio = None
    if not pd.isna(last.get("volume_ratio20")):
        volume_ratio = float(last["volume_ratio20"])

    # State is deliberately simple.  "WAIT" is the normal state.
    if close < plan.risk:
        state = "🔴 RISK"
        action = "停止加仓；重新检查 2000 附近支撑是否失效。"

    elif (
        close >= plan.breakout
        and previous_close < plan.breakout
        and (volume_ratio is None or volume_ratio >= plan.volume_confirm)
    ):
        state = "🟢 BREAKOUT"
        action = "突破 2300 且量能确认：检查第三格 100 股条件。"

    elif (
        plan.low_entry <= close <= plan.confirm_low
        and close > previous_close
    ):
        state = "🟢 LOW_ZONE"
        action = "进入 2000～2050 附近且收盘止跌：检查第一格 100 股条件。"

    elif (
        plan.confirm_low <= close <= plan.confirm_high
        and ma5 > ma20
    ):
        state = "🟡 CONFIRM"
        action = "趋势改善：检查第二格 100 股条件。"

    elif close >= plan.take_profit_2:
        state = "🟡 TAKE_PROFIT"
        action = "进入 2600 以上：检查剩余仓位的分批止盈计划。"

    elif close >= plan.take_profit_1:
        state = "🟡 TAKE_PROFIT"
        action = "进入 2500 附近：检查是否兑现第一批 100 股。"

    else:
        state = "🟡 WAIT"
        action = "不操作。"

    return {
        "date": last["date"].date().isoformat(),
        "close": close,
        "previous_close": previous_close,
        "ma5": ma5,
        "ma20": ma20,
        "volume_ratio20": volume_ratio,
        "state": state,
        "action": action,
    }


def crossed_up(previous: float, current: float, threshold: float) -> bool:
    return previous < threshold <= current


def crossed_down(previous: float, current: float, threshold: float) -> bool:
    return previous >= threshold > current


def should_notify(result: dict, plan: Plan = Plan()) -> bool:
    """
    Only notify on a new trigger.

    This prevents the same condition from producing a Telegram/email alert
    every trading day while the price remains in the same zone.
    """
    previous = result["previous_close"]
    current = result["close"]

    if crossed_down(previous, current, plan.risk):
        return True

    if crossed_down(previous, current, plan.low_entry) and current >= plan.risk:
        return True

    if crossed_up(previous, current, plan.confirm_low):
        return True

    if crossed_up(previous, current, plan.breakout):
        # If volume is available, the breakout alert is only actionable
        # when volume confirms it.  Otherwise the daily status still prints.
        ratio = result["volume_ratio20"]
        return ratio is None or ratio >= plan.volume_confirm

    if crossed_up(previous, current, plan.take_profit_1):
        return True

    if crossed_up(previous, current, plan.take_profit_2):
        return True

    return False


def format_message(result: dict) -> str:
    ratio = result["volume_ratio20"]
    volume_text = "N/A" if ratio is None else f"{ratio:.2f}x"

    return (
        "5802 住友电工｜低频波段信号\n"
        f"日期：{result['date']}\n"
        f"收盘：¥{result['close']:.1f}\n"
        f"MA5：¥{result['ma5']:.1f}｜"
        f"MA20：¥{result['ma20']:.1f}｜"
        f"20日量比：{volume_text}\n"
        f"状态：{result['state']}\n"
        f"动作：{result['action']}\n"
        "计划：2050低位｜2150～2200确认｜2300突破｜"
        "1950风险｜2500/2600止盈观察\n"
        "注：机械规则，仅用于提醒；不会自动下单。"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--notify",
        action="store_true",
        help="Send only newly-triggered signals through NotificationService.",
    )
    args = parser.parse_args()

    df, source = load_history_df(CODE, days=60)

    if df is None or df.empty:
        raise RuntimeError(f"Unable to load history for {CODE}.")

    result = evaluate(df)
    message = format_message(result)

    print(message)
    print(f"数据源：{source}")

    if args.notify and should_notify(result):
        try:
            ok = NotificationService().send(message, route_type="alert")
        except TypeError:
            # Compatibility fallback for older forks whose send() has no
            # route_type keyword.
            ok = NotificationService().send(message)

        if not ok:
            print(
                "WARNING: NotificationService returned false.",
                file=sys.stderr,
            )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
