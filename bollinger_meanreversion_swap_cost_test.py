# -*- coding: utf-8 -*-
"""
Phase 8：加入真實隔夜倉息(Swap)的完整成本模型

Phase 7 已經有真實spread(進出場各一次) + 手續費(0.002%/次)，這裡補上最後一塊：
隔夜倉息。資料來自 ExportSwapRates.mq5 匯出的 swap_rates.csv，是這 14 檔在
使用者實際帳戶上的真實倉息設定（swap_mode=POINTS，多空皆為負值——不管做多做空
隔夜都要付費，週五收3倍息對應週末）。

換算公式（MT5 POINTS模式）：
  swap_$_per_lot_per_night = swap_points * tick_value
  （因為 point == tick_size，兩者可以直接對應）
  swap_pct_of_price = swap_$ / entry_price（因為 contract_size=1.0，1手=1股，
  所以「每股每晚的swap金額」除以進場價，就是佔部位的百分比成本）

每筆交易的過夜次數：用進場日期到出場日期之間跨過幾個日曆日（即跨過幾次rollover）
計算，若跨過的日期是週五則計3倍（對應週末的3倍息慣例）。
"""

import os
import numpy as np
import pandas as pd

from bollinger_breakout_trend import TRAIN_FRACTION, ATR_WINDOW
from bollinger_meanreversion_equities_cfd_test import load_symbol_15m
from bollinger_meanreversion_performance_report import full_performance_stats
from bollinger_meanreversion_full_cost_test import (
    compute_features, apply_risk_based_sizing, apply_daily_loss_limit,
    COMMISSION_PCT_PER_SIDE, ATR_MULT, MAX_HOLD_BARS, TRADE_LIST,
)

SWAP_FILE = "data_mt5_equities_cfd/swap_rates.csv"


def load_swap_rates():
    # MQL5 FILE_ANSI 用系統編碼寫出中文描述欄位，在mac上讀取UTF-8會失敗；
    # 我們只需要數值欄位，用latin-1安全解碼即可（不影響數字解析）
    df = pd.read_csv(SWAP_FILE, encoding="latin-1")
    return df.set_index("symbol")[["swap_long", "swap_short", "tick_value"]].to_dict("index")


def count_swap_nights(entry_date, exit_date):
    """算進場到出場之間跨過幾個 rollover 日，週五算3倍（對應週末）。"""
    if exit_date <= entry_date:
        return 0
    total = 0
    d = entry_date
    while d < exit_date:
        total += 3 if d.weekday() == 4 else 1  # Python weekday(): Monday=0...Friday=4...Sunday=6
        d += pd.Timedelta(days=1)
    return total


def backtest_with_swap(df: pd.DataFrame, swap_long_pts: float, swap_short_pts: float, tick_value: float):
    trades = []
    pos = 0
    entry_p = entry_i = sl_p = 0
    entry_spread = entry_stop_dist = 0.0
    entry_t = None

    for i in range(len(df)):
        r = df.iloc[i]
        if pd.isna(r["mid"]) or pd.isna(r["atr"]):
            continue
        c, h, l, mid = r["close"], r["high"], r["low"], r["mid"]
        exit_spread = (r["spread_bps"] / 1e4) if not pd.isna(r["spread_bps"]) else 0.0003
        commission = COMMISSION_PCT_PER_SIDE / 100

        if pos == 1:
            hit_sl = l <= sl_p
            reverted = c >= mid
            timeout = (i - entry_i) >= MAX_HOLD_BARS
            if hit_sl or reverted or timeout:
                exit_p = sl_p if hit_sl else c
                nights = count_swap_nights(entry_t.date(), r["datetime"].date())
                swap_cost_pct = (nights * swap_long_pts * tick_value) / entry_p  # swap_long_pts已是負值，直接相加即為成本
                pnl = ((exit_p - entry_p) / entry_p - entry_spread - exit_spread
                       - 2 * commission + swap_cost_pct)
                trades.append({"exit_t": r["datetime"], "entry_t": entry_t, "pnl": pnl,
                                "type": "LONG", "stop_dist_pct": entry_stop_dist, "nights": nights})
                pos = 0
        elif pos == -1:
            hit_sl = h >= sl_p
            reverted = c <= mid
            timeout = (i - entry_i) >= MAX_HOLD_BARS
            if hit_sl or reverted or timeout:
                exit_p = sl_p if hit_sl else c
                nights = count_swap_nights(entry_t.date(), r["datetime"].date())
                swap_cost_pct = (nights * swap_short_pts * tick_value) / entry_p
                pnl = ((entry_p - exit_p) / entry_p - entry_spread - exit_spread
                       - 2 * commission + swap_cost_pct)
                trades.append({"exit_t": r["datetime"], "entry_t": entry_t, "pnl": pnl,
                                "type": "SHORT", "stop_dist_pct": entry_stop_dist, "nights": nights})
                pos = 0

        if pos == 0:
            if r["breakout_down"]:
                pos, entry_p, entry_i, sl_p = 1, c, i, c - ATR_MULT * r["atr"]
                entry_spread = exit_spread
                entry_stop_dist = (entry_p - sl_p) / entry_p
                entry_t = r["datetime"]
            elif r["breakout_up"]:
                pos, entry_p, entry_i, sl_p = -1, c, i, c + ATR_MULT * r["atr"]
                entry_spread = exit_spread
                entry_stop_dist = (sl_p - entry_p) / entry_p
                entry_t = r["datetime"]

    return pd.DataFrame(trades)


def main():
    swap_rates = load_swap_rates()
    all_stats = []
    per_symbol_trades = {}

    print(f"回測 {len(TRADE_LIST)} 檔最終交易名單，含真實spread+手續費+真實隔夜倉息+風控優化 ...\n")

    for sym in TRADE_LIST:
        if sym not in swap_rates:
            print(f"  {sym}: 缺少倉息資料，跳過")
            continue
        sw = swap_rates[sym]
        raw = load_symbol_15m(sym)
        feat = compute_features(raw)
        split_i = int(len(raw) * TRAIN_FRACTION)
        split_time = raw["datetime"].iloc[split_i]

        tdf = backtest_with_swap(feat, sw["swap_long"], sw["swap_short"], sw["tick_value"])
        if tdf.empty:
            continue
        tdf = tdf.sort_values("exit_t").reset_index(drop=True)
        avg_nights = tdf["nights"].mean()

        tdf_sized = apply_risk_based_sizing(tdf)
        tdf_final = apply_daily_loss_limit(tdf_sized)
        tdf_final = tdf_final.sort_values("exit_t").reset_index(drop=True)
        per_symbol_trades[sym] = (tdf_final, split_time, raw["datetime"].iloc[0], raw["datetime"].iloc[-1])

        train_tdf = tdf_final[tdf_final["exit_t"] < split_time]
        test_tdf = tdf_final[tdf_final["exit_t"] >= split_time]
        train_days = (split_time - raw["datetime"].iloc[0]).total_seconds() / 86400
        test_days = (raw["datetime"].iloc[-1] - split_time).total_seconds() / 86400

        for split_name, sub, days in [("TRAIN", train_tdf, train_days), ("TEST", test_tdf, test_days)]:
            stats, _ = full_performance_stats(sub, days)
            if stats is None:
                continue
            stats["symbol"] = sym
            stats["split"] = split_name
            stats["avg_nights_held"] = round(avg_nights, 2)
            all_stats.append(stats)

    summary = pd.DataFrame(all_stats)
    cols = ["symbol", "split", "n_trades", "avg_nights_held", "period_days", "total_return_pct",
            "ann_return_pct", "ann_sharpe", "max_drawdown_pct", "win_rate_pct", "t_stat"]
    summary = summary[cols]
    os.makedirs("residual_alpha_results", exist_ok=True)
    summary.to_csv("residual_alpha_results/phase8_swap_cost_full_performance.csv", index=False)

    pd.set_option("display.width", 175)
    test_only = summary[summary["split"] == "TEST"].sort_values("t_stat", ascending=False)
    print("=== TEST期結果（真實spread+手續費+真實隔夜倉息+風控優化，依t-stat排序）===")
    print(test_only.to_string(index=False))
    print(f"\n平均t-stat: {test_only['t_stat'].mean():.2f}  中位數: {test_only['t_stat'].median():.2f}")
    print(f"t-stat>=1.5的股票數: {(test_only['t_stat']>=1.5).sum()} / {len(test_only)}")
    print(f"正報酬股票數: {(test_only['total_return_pct']>0).sum()} / {len(test_only)}")

    # 跟 Phase 7（無倉息）比較
    phase7 = pd.read_csv("residual_alpha_results/phase7_final_list_full_cost.csv")
    phase7_test = phase7[phase7["split"] == "TEST"].set_index("symbol")
    compare = test_only.set_index("symbol")[["t_stat", "total_return_pct"]].join(
        phase7_test[["t_stat", "total_return_pct"]], lsuffix="_with_swap", rsuffix="_no_swap")
    compare["t_stat_drop"] = compare["t_stat_no_swap"] - compare["t_stat_with_swap"]
    compare["total_return_drop_pct"] = compare["total_return_pct_no_swap"] - compare["total_return_pct_with_swap"]
    print("\n=== 加入倉息前後比較 ===")
    print(compare.sort_values("t_stat_drop", ascending=False).to_string())

    # --- Pooled equal-weight ---
    def build_calendar_equity(tdf, cal_index):
        tdf = tdf.sort_values("exit_t")
        eq = (1 + tdf["pnl"]).cumprod()
        eq.index = tdf["exit_t"]
        eq = eq.reindex(cal_index.union(eq.index)).ffill().fillna(1.0)
        return eq.reindex(cal_index).ffill().fillna(1.0)

    def pooled_stats(split_name):
        frames = []
        for sym, (tdf, split_time, start, end) in per_symbol_trades.items():
            sub = tdf[tdf["exit_t"] < split_time] if split_name == "TRAIN" else tdf[tdf["exit_t"] >= split_time]
            if not sub.empty:
                frames.append(sub)
        if not frames:
            return None
        all_t = pd.concat(frames)["exit_t"]
        cal_index = pd.date_range(all_t.min(), all_t.max(), freq="D")
        sub_eqs = []
        for sym, (tdf, split_time, start, end) in per_symbol_trades.items():
            sub = tdf[tdf["exit_t"] < split_time] if split_name == "TRAIN" else tdf[tdf["exit_t"] >= split_time]
            sub_eqs.append(build_calendar_equity(sub, cal_index) if not sub.empty else pd.Series(1.0, index=cal_index))
        portfolio_equity = sum(sub_eqs) / len(sub_eqs)
        days = (cal_index[-1] - cal_index[0]).total_seconds() / 86400
        total_return = portfolio_equity.iloc[-1] - 1
        ann_return = (1 + total_return) ** (365.25 / days) - 1 if days > 0 else np.nan
        daily_ret = portfolio_equity.pct_change().dropna()
        sharpe = (daily_ret.mean() / daily_ret.std()) * np.sqrt(252) if daily_ret.std() > 0 else np.nan
        max_dd = (portfolio_equity / portfolio_equity.cummax() - 1).min()
        stats = {"split": split_name, "n_trades": sum(len(f) for f in frames), "period_days": round(days, 1),
                 "total_return_pct": total_return * 100, "ann_return_pct": ann_return * 100,
                 "ann_sharpe": sharpe, "max_drawdown_pct": max_dd * 100}
        return stats, portfolio_equity

    train_stats, train_equity = pooled_stats("TRAIN")
    test_stats, test_equity = pooled_stats("TEST")
    pooled = pd.DataFrame([train_stats, test_stats])
    print(f"\n=== Pooled equal-weight（{len(TRADE_LIST)}檔，含真實倉息+全部成本+風控優化）===")
    print(pooled.to_string(index=False))
    pooled.to_csv("residual_alpha_results/phase8_pooled_swap_cost.csv", index=False)

    # --- 畫圖：pooled 曲線 ---
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(11, 5))
    test_rebased = test_equity / test_equity.iloc[0] * train_equity.iloc[-1]
    combined = pd.concat([train_equity, test_rebased])
    ax.plot(combined.index, combined.values, linewidth=1.3, color="darkblue")
    ax.axvline(train_equity.index[-1], color="red", linestyle="--", linewidth=1, label="train/test split")
    ax.axhline(1.0, color="gray", linewidth=0.8, linestyle=":")
    ax.set_title(f"Phase 8 POOLED equal-weight, {len(TRADE_LIST)} symbols, 含真實spread+手續費+隔夜倉息 — "
                 f"train Sharpe={train_stats['ann_sharpe']:.2f}, test Sharpe={test_stats['ann_sharpe']:.2f}, "
                 f"test MaxDD={test_stats['max_drawdown_pct']:.1f}%")
    ax.set_ylabel("Portfolio Equity (x, 每檔等權1/N資金)")
    ax.legend(loc="upper left", fontsize=9)
    plt.tight_layout()
    plt.savefig("residual_alpha_results/phase8_pooled_equity_curve.png", dpi=130)
    print("\npooled equity curve 已存至 residual_alpha_results/phase8_pooled_equity_curve.png")

    # --- 畫圖：逐股票小圖網格 ---
    n = len(per_symbol_trades)
    ncols = 4
    nrows = int(np.ceil(n / ncols))
    fig, axes = plt.subplots(nrows, ncols, figsize=(4 * ncols, 2.6 * nrows))
    axes_flat = axes.flatten() if n > 1 else [axes]
    for ax, sym in zip(axes_flat, sorted(per_symbol_trades.keys())):
        tdf, split_time, _, _ = per_symbol_trades[sym]
        equity = (1 + tdf["pnl"]).cumprod()
        split_idx = (tdf["exit_t"] < split_time).sum()
        color = "darkgreen" if equity.iloc[-1] > 1 else "darkred"
        ax.plot(range(len(equity)), equity.values, linewidth=0.9, color=color)
        ax.axvline(split_idx, color="red", linestyle="--", linewidth=0.7)
        ax.axhline(1.0, color="gray", linewidth=0.6, linestyle=":")
        ax.set_title(f"{sym}  (ret={((equity.iloc[-1]-1)*100):.0f}%)", fontsize=10)
        ax.tick_params(labelsize=7)
    for ax in axes_flat[n:]:
        ax.axis("off")
    plt.tight_layout()
    plt.savefig("residual_alpha_results/phase8_all_symbols_grid.png", dpi=120)
    print("逐股票 equity curve 網格已存至 residual_alpha_results/phase8_all_symbols_grid.png")


if __name__ == "__main__":
    main()
