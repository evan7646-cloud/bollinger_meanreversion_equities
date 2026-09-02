# -*- coding: utf-8 -*-
"""
Phase 5：Bollinger 均值回歸策略（15m, MA20±2σ，已驗證版本）套用在 59 檔 Equities CFD

跟 Phase 4b（8檔半導體股）同一套規則，換成更大、更多元的資料集：
  46 檔 Equities I CFD（美股大型股，涵蓋科技/金融/能源/消費/工業等多個sector）
  13 檔 Equities II CFD（歐股，LVMH/BMW/SAP等）
  資料直接是 broker 匯出的 15m K線（不用像半導體股那樣從1m resample），
  部分標的回溯到 2015 年，比先前3個月/1年的樣本長很多。

目的：驗證這個策略是否只在半導體 sector 有效（先前8檔同產業，訊號高度相關），
      還是跨產業、跨更長期間也成立。
"""

import os
import glob
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from bollinger_breakout_trend import compute_bands, TRAIN_FRACTION
from bollinger_meanreversion import backtest_mean_reversion
from bollinger_meanreversion_performance_report import full_performance_stats

DATA_DIR = "data_mt5_equities_cfd"
WINDOW = 20


def load_symbol_15m(symbol: str) -> pd.DataFrame:
    path = os.path.join(DATA_DIR, f"EQ_{symbol}_15m.csv")
    df = pd.read_csv(path)
    df["datetime"] = pd.to_datetime(df["datetime"], format="%Y.%m.%d %H:%M")
    df = df.sort_values("datetime").reset_index(drop=True)
    df = df.rename(columns={"tick_volume": "volume"})
    return df[["datetime", "open", "high", "low", "close", "volume", "spread_bps"]]


def main():
    meta = pd.read_csv(os.path.join(DATA_DIR, "EQ_universe_meta.csv"))
    symbols = meta["symbol"].tolist()
    category_map = dict(zip(meta["symbol"], meta["category"]))

    all_stats = []
    per_symbol_trades = {}

    print(f"讀取並回測 {len(symbols)} 檔標的 ...")
    for sym in symbols:
        path = os.path.join(DATA_DIR, f"EQ_{sym}_15m.csv")
        if not os.path.exists(path):
            continue
        raw = load_symbol_15m(sym)
        if len(raw) < 200:
            print(f"  {sym}: 資料太少（{len(raw)}根），跳過")
            continue

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
            stats["category"] = category_map.get(sym, "?")
            stats["split"] = split_name
            all_stats.append(stats)

    summary = pd.DataFrame(all_stats)
    cols = ["symbol", "category", "split", "n_trades", "period_days", "total_return_pct", "ann_return_pct",
            "ann_sharpe", "max_drawdown_pct", "calmar", "win_rate_pct", "profit_factor", "t_stat", "mean_pnl_bps"]
    summary = summary[cols]
    os.makedirs("residual_alpha_results", exist_ok=True)
    summary.to_csv("residual_alpha_results/phase5_equities_cfd_full_performance.csv", index=False)

    pd.set_option("display.width", 180)
    pd.set_option("display.max_rows", 200)

    test_summary = summary[summary["split"] == "TEST"].sort_values("t_stat", ascending=False)
    print("\n" + "=" * 100)
    print(f"=== TEST 期逐股票結果（依 t-stat 排序，共 {len(test_summary)} 檔）===")
    print("=" * 100)
    print(test_summary.to_string(index=False))

    n_sig = (test_summary["t_stat"] >= 1.5).sum()
    n_total = len(test_summary)
    print(f"\nTEST 期 t-stat >= 1.5 的股票數: {n_sig} / {n_total} ({n_sig/n_total*100:.1f}%)")
    print(f"TEST 期平均 t-stat: {test_summary['t_stat'].mean():.2f}")
    print(f"TEST 期 t-stat 中位數: {test_summary['t_stat'].median():.2f}")
    print(f"TEST 期正報酬股票數: {(test_summary['total_return_pct']>0).sum()} / {n_total}")

    print("\n=== 依分類(Equities I vs II)分組統計 ===")
    cat_agg = test_summary.groupby("category").agg(
        n_symbols=("symbol", "nunique"),
        mean_t_stat=("t_stat", "mean"),
        median_t_stat=("t_stat", "median"),
        pct_t_ge_1_5=("t_stat", lambda x: (x >= 1.5).mean() * 100),
        mean_total_return_pct=("total_return_pct", "mean"),
        mean_win_rate=("win_rate_pct", "mean"),
    )
    print(cat_agg.to_string())

    # --- Pooled equal-weight（全部59檔，資金平均分配）---
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
        n_syms = len(per_symbol_trades)
        for sym, (tdf, split_time, start, end) in per_symbol_trades.items():
            sub = tdf[tdf["exit_t"] < split_time] if split_name == "TRAIN" else tdf[tdf["exit_t"] >= split_time]
            if sub.empty:
                sub_equities.append(pd.Series(1.0, index=cal_index))
                continue
            sub_equities.append(build_calendar_equity(sub, cal_index))

        portfolio_equity = sum(sub_equities) / len(sub_equities)
        days = (cal_index[-1] - cal_index[0]).total_seconds() / 86400
        total_return = portfolio_equity.iloc[-1] - 1
        ann_return = (1 + total_return) ** (365.25 / days) - 1 if days > 0 else np.nan
        daily_ret = portfolio_equity.pct_change().dropna()
        sharpe = (daily_ret.mean() / daily_ret.std()) * np.sqrt(252) if daily_ret.std() > 0 else np.nan
        running_max = portfolio_equity.cummax()
        max_dd = (portfolio_equity / running_max - 1).min()
        n_trades_total = sum(len(f) for f in frames)
        stats = {
            "split": split_name, "n_symbols": n_syms, "n_trades": n_trades_total, "period_days": round(days, 1),
            "total_return_pct": total_return * 100, "ann_return_pct": ann_return * 100,
            "ann_sharpe": sharpe, "max_drawdown_pct": max_dd * 100,
        }
        return stats, portfolio_equity

    train_stats, pooled_train_equity = pooled_stats_and_curve("TRAIN")
    test_stats, pooled_test_equity = pooled_stats_and_curve("TEST")

    print("\n" + "=" * 100)
    print("=== Pooled equal-weight（59檔資金平均分配）===")
    print("=" * 100)
    print(pd.DataFrame([train_stats, test_stats]).to_string(index=False))

    # --- 畫圖：pooled 曲線 ---
    fig, ax = plt.subplots(figsize=(11, 5))
    test_rebased = pooled_test_equity / pooled_test_equity.iloc[0] * pooled_train_equity.iloc[-1]
    combined_equity = pd.concat([pooled_train_equity, test_rebased])
    ax.plot(combined_equity.index, combined_equity.values, linewidth=1.3, color="darkblue")
    ax.axvline(pooled_train_equity.index[-1], color="red", linestyle="--", linewidth=1, label="train/test split")
    ax.axhline(1.0, color="gray", linewidth=0.8, linestyle=":")
    ax.set_title(f"POOLED equal-weight, {train_stats['n_symbols']} symbols (Equities I+II CFD) — "
                 f"train Sharpe={train_stats['ann_sharpe']:.2f}, test Sharpe={test_stats['ann_sharpe']:.2f}")
    ax.set_ylabel("Portfolio Equity (x)")
    ax.legend(loc="upper left", fontsize=9)
    plt.tight_layout()
    plt.savefig("residual_alpha_results/phase5_pooled_equity_curve.png", dpi=130)
    print("\npooled equity curve 已存至 residual_alpha_results/phase5_pooled_equity_curve.png")

    # --- 畫圖：59檔小圖網格 ---
    n = len(per_symbol_trades)
    ncols = 8
    nrows = int(np.ceil(n / ncols))
    fig, axes = plt.subplots(nrows, ncols, figsize=(3 * ncols, 2.2 * nrows))
    axes_flat = axes.flatten()
    for ax, sym in zip(axes_flat, sorted(per_symbol_trades.keys())):
        tdf, split_time, _, _ = per_symbol_trades[sym]
        equity = (1 + tdf["pnl"]).cumprod()
        split_idx = (tdf["exit_t"] < split_time).sum()
        color = "darkgreen" if equity.iloc[-1] > 1 else "darkred"
        ax.plot(range(len(equity)), equity.values, linewidth=0.8, color=color)
        ax.axvline(split_idx, color="red", linestyle="--", linewidth=0.6)
        ax.axhline(1.0, color="gray", linewidth=0.5, linestyle=":")
        ax.set_title(sym, fontsize=8)
        ax.tick_params(labelsize=6)
    for ax in axes_flat[n:]:
        ax.axis("off")
    plt.tight_layout()
    plt.savefig("residual_alpha_results/phase5_all_symbols_grid.png", dpi=110)
    print("59檔小圖網格已存至 residual_alpha_results/phase5_all_symbols_grid.png")


if __name__ == "__main__":
    main()
