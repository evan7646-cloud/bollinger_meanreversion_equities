# -*- coding: utf-8 -*-
"""
Bollinger 均值回歸策略（15m, MA20±2σ，已驗證版本）—— 完整績效報表 + equity curve

補齊之前只有 win_rate/PF/total_return/t_stat 的簡易統計，這裡加上：
  年化報酬、年化 Sharpe、最大回撤、Calmar —— 跟 Phase 1c 對 residual momentum 做的
  完整績效分析同一個標準，方便直接比較。

輸出：
  - residual_alpha_results/bollinger_meanreversion_full_performance.csv（逐股票 x train/test 完整指標）
  - residual_alpha_results/bollinger_meanreversion_equity_curves.png（8檔個別 + 1條全部合併的equity curve）
"""

import os
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from bollinger_breakout_trend import load_mt5_1m, resample_ohlcv, compute_bands, MT5_SYMBOLS, TRAIN_FRACTION
from bollinger_meanreversion import backtest_mean_reversion

WINDOW = 20
TIMEFRAME_RULE = "15min"


def full_performance_stats(tdf: pd.DataFrame, period_days: float):
    """跟 Phase 1c 的 performance_stats 同標準：年化報酬/Sharpe/最大回撤/Calmar。
    trades_per_year 用「這段期間實際跨越的天數」換算，而不是假設固定bar頻率，
    因為進出場時間點是訊號觸發的，不是均勻分布的。"""
    n = len(tdf)
    if n < 5 or period_days <= 0:
        return None, None
    tdf = tdf.sort_values("exit_t").reset_index(drop=True)
    equity = (1 + tdf["pnl"]).cumprod()
    total_return = equity.iloc[-1] - 1
    trades_per_year = n / period_days * 365.25
    mean_r, std_r = tdf["pnl"].mean(), tdf["pnl"].std()
    sharpe = (mean_r / std_r) * np.sqrt(trades_per_year) if std_r > 0 else np.nan
    running_max = equity.cummax()
    dd = equity / running_max - 1
    max_dd = dd.min()
    ann_return = (1 + total_return) ** (365.25 / period_days) - 1
    calmar = ann_return / abs(max_dd) if max_dd < 0 else np.nan
    win_rate = (tdf["pnl"] > 0).mean()
    pf = tdf.loc[tdf["pnl"] > 0, "pnl"].sum() / (abs(tdf.loc[tdf["pnl"] <= 0, "pnl"].sum()) + 1e-8)
    t_stat = (mean_r / std_r) * np.sqrt(n) if std_r > 0 else np.nan

    stats = {
        "n_trades": n, "period_days": round(period_days, 1),
        "total_return_pct": total_return * 100, "ann_return_pct": ann_return * 100,
        "ann_sharpe": sharpe, "max_drawdown_pct": max_dd * 100, "calmar": calmar,
        "win_rate_pct": win_rate * 100, "profit_factor": pf, "t_stat": t_stat,
        "mean_pnl_bps": mean_r * 1e4,
    }
    return stats, equity


def main():
    all_stats = []
    per_symbol_trades = {}  # 存逐股票的完整 trade log，供畫圖與 pooled 使用

    for sym in MT5_SYMBOLS:
        raw_native = load_mt5_1m(sym)
        raw = resample_ohlcv(raw_native, TIMEFRAME_RULE)
        split_i = int(len(raw) * TRAIN_FRACTION)
        split_time = raw["datetime"].iloc[split_i]

        feat = compute_bands(raw, WINDOW)
        tdf = backtest_mean_reversion(feat)
        if tdf.empty:
            continue
        tdf = tdf.sort_values("exit_t").reset_index(drop=True)
        per_symbol_trades[sym] = (tdf, split_time, raw["datetime"].iloc[0], raw["datetime"].iloc[-1])

        train_tdf = tdf[tdf["exit_t"] < split_time]
        test_tdf = tdf[tdf["exit_t"] >= split_time]

        train_days = (split_time - raw["datetime"].iloc[0]).total_seconds() / 86400
        test_days = (raw["datetime"].iloc[-1] - split_time).total_seconds() / 86400

        for split_name, sub, days in [("TRAIN", train_tdf, train_days), ("TEST", test_tdf, test_days)]:
            stats, _ = full_performance_stats(sub, days)
            if stats is None:
                continue
            stats["symbol"] = sym
            stats["split"] = split_name
            all_stats.append(stats)

    # --- Pooled（8檔合併）---
    # 注意：不能把8檔的交易依exit時間排序後直接依序複利（那等於假設每一筆交易都能
    # 動用100%資金，但實際上8檔同時可能都有開倉部位，不可能每筆都佔滿全部資金，
    # 這樣算會嚴重高估合併報酬）。改用「資金平均分成8份、每份各自複利、
    # 依日曆時間加總」，才是寫實的組合權益曲線。
    def build_calendar_equity(tdf: pd.DataFrame, cal_index: pd.DatetimeIndex) -> pd.Series:
        tdf = tdf.sort_values("exit_t")
        eq = (1 + tdf["pnl"]).cumprod()
        eq.index = tdf["exit_t"]
        eq = eq.reindex(cal_index.union(eq.index)).ffill().fillna(1.0)
        return eq.reindex(cal_index).ffill().fillna(1.0)

    def pooled_stats_and_curve(split_name: str):
        frames = []
        for sym, (tdf, split_time, start, end) in per_symbol_trades.items():
            sub = tdf[tdf["exit_t"] < split_time] if split_name == "TRAIN" else tdf[tdf["exit_t"] >= split_time]
            if not sub.empty:
                frames.append(sub)
        if not frames:
            return None, None
        all_exit_times = pd.concat(frames)["exit_t"]
        cal_index = pd.date_range(all_exit_times.min(), all_exit_times.max(), freq="D")

        sub_equities = []
        for sym, (tdf, split_time, start, end) in per_symbol_trades.items():
            sub = tdf[tdf["exit_t"] < split_time] if split_name == "TRAIN" else tdf[tdf["exit_t"] >= split_time]
            if sub.empty:
                sub_equities.append(pd.Series(1.0, index=cal_index))
                continue
            sub_equities.append(build_calendar_equity(sub, cal_index))

        portfolio_equity = sum(sub_equities) / len(sub_equities)  # 每檔各佔 1/8 資金，各自複利後加總
        days = (cal_index[-1] - cal_index[0]).total_seconds() / 86400
        total_return = portfolio_equity.iloc[-1] - 1
        ann_return = (1 + total_return) ** (365.25 / days) - 1 if days > 0 else np.nan
        daily_ret = portfolio_equity.pct_change().dropna()
        sharpe = (daily_ret.mean() / daily_ret.std()) * np.sqrt(252) if daily_ret.std() > 0 else np.nan
        running_max = portfolio_equity.cummax()
        max_dd = (portfolio_equity / running_max - 1).min()
        n_trades_total = sum(len(f) for f in frames)
        stats = {
            "symbol": "POOLED_8SYMBOLS_EQUALWEIGHT", "split": split_name,
            "n_trades": n_trades_total, "period_days": round(days, 1),
            "total_return_pct": total_return * 100, "ann_return_pct": ann_return * 100,
            "ann_sharpe": sharpe, "max_drawdown_pct": max_dd * 100,
            "calmar": (ann_return / abs(max_dd)) if max_dd < 0 else np.nan,
            "win_rate_pct": np.nan, "profit_factor": np.nan, "t_stat": np.nan, "mean_pnl_bps": np.nan,
        }
        return stats, portfolio_equity

    train_stats, pooled_train_equity = pooled_stats_and_curve("TRAIN")
    test_stats, pooled_test_equity = pooled_stats_and_curve("TEST")
    all_stats.append(train_stats)
    all_stats.append(test_stats)

    summary = pd.DataFrame(all_stats)
    cols = ["symbol", "split", "n_trades", "period_days", "total_return_pct", "ann_return_pct",
            "ann_sharpe", "max_drawdown_pct", "calmar", "win_rate_pct", "profit_factor", "t_stat", "mean_pnl_bps"]
    summary = summary[cols]
    os.makedirs("residual_alpha_results", exist_ok=True)
    summary.to_csv("residual_alpha_results/bollinger_meanreversion_full_performance.csv", index=False)

    pd.set_option("display.width", 180)
    pd.set_option("display.max_columns", 20)
    print("=== 完整績效表（15m, MA20±2σ 均值回歸）===\n")
    print(summary.to_string(index=False))

    # --- 畫 equity curve：8檔個別小圖 + 1張pooled大圖 ---
    fig, axes = plt.subplots(len(MT5_SYMBOLS) + 1, 1, figsize=(10, 3 * (len(MT5_SYMBOLS) + 1)))

    # Pooled 圖放最上面（等權重 1/8 資金分配、依日曆時間，train 接 test 畫成一條連續曲線）
    ax = axes[0]
    test_rebased = pooled_test_equity / pooled_test_equity.iloc[0] * pooled_train_equity.iloc[-1]
    combined_equity = pd.concat([pooled_train_equity, test_rebased])
    ax.plot(combined_equity.index, combined_equity.values, linewidth=1.3, color="darkblue")
    ax.axvline(pooled_train_equity.index[-1], color="red", linestyle="--", linewidth=1, label="train/test split")
    ax.axhline(1.0, color="gray", linewidth=0.8, linestyle=":")
    n_total = train_stats["n_trades"] + test_stats["n_trades"]
    ax.set_title(f"POOLED equal-weight 1/8 capital (8 symbols, n={n_total} trades) — "
                 f"train ann_sharpe={train_stats['ann_sharpe']:.2f}, test ann_sharpe={test_stats['ann_sharpe']:.2f}")
    ax.set_ylabel("Portfolio Equity (x)")
    ax.legend(loc="upper left", fontsize=8)

    for ax, sym in zip(axes[1:], MT5_SYMBOLS):
        if sym not in per_symbol_trades:
            ax.set_title(f"{sym}: no trades")
            continue
        tdf, split_time, _, _ = per_symbol_trades[sym]
        equity = (1 + tdf["pnl"]).cumprod()
        split_idx = (tdf["exit_t"] < split_time).sum()
        ax.plot(range(len(equity)), equity.values, linewidth=1.1)
        ax.axvline(split_idx, color="red", linestyle="--", linewidth=1)
        ax.axhline(1.0, color="gray", linewidth=0.8, linestyle=":")
        total_ret = (equity.iloc[-1] - 1) * 100
        ax.set_title(f"{sym}  (n={len(tdf)}, total_ret={total_ret:.1f}%)")
        ax.set_ylabel("Equity (x)")

    plt.tight_layout()
    out_png = "residual_alpha_results/bollinger_meanreversion_equity_curves.png"
    plt.savefig(out_png, dpi=130)
    print(f"\nequity curve 已存至 {out_png}")


if __name__ == "__main__":
    main()
