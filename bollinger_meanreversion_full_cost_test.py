# -*- coding: utf-8 -*-
"""
Phase 7：最終交易名單(14檔)+ 完整成本模型(真實spread + 手續費) + 已驗證的風控優化

在 Phase 6 只用 5 檔測過風控優化，這裡套用到實際要交易的完整 14 檔名單，
並加上使用者指定的手續費（0.002%/手，這裡假設為「每次成交(進場/出場各一次)
收 0.002% of 名目金額」，也就是一趟完整交易收 2x0.002%=0.004%——這是比較保守
常見的 ECN/CFD 收費慣例，如果實際上是「整趟交易只收一次 0.002%」，成本會更低，
結果只會更好，所以用這個假設是保守方向，不會高估策略表現）。

隔夜倉息(Swap)尚未納入 —— 需要先跑 ExportSwapRates.mq5 拿到真實倉息設定才能準確
計算（策略常態跨日持倉，這是目前成本模型最大的缺口，會在拿到資料後補上）。
"""

import os
import numpy as np
import pandas as pd

from bollinger_breakout_trend import TRAIN_FRACTION, ATR_WINDOW
from bollinger_meanreversion_equities_cfd_test import load_symbol_15m
from bollinger_meanreversion_performance_report import full_performance_stats

WINDOW = 20
STD_MULT = 2.0
ATR_MULT = 1.5
MAX_HOLD_BARS = 240
COMMISSION_PCT_PER_SIDE = 0.002  # 使用者指定：每手0.002%，這裡當作每次成交(進場/出場各一次)都收一次
DAILY_LOSS_LIMIT_PCT = 3.0
RISK_PER_TRADE_PCT = 1.0
MAX_SIZE_MULT = 3.0

TRADE_LIST = ["AVGO", "JPM", "QCOM", "PLTR", "AMD", "INTC", "SIEGn", "ASML",
              "LMT", "SNOW"]  # Phase 8：剔除MSFT/JNJ/SBUX(含倉息t-stat<1.5)+DIS(train期t-stat為負)，最終10檔


def compute_features(df: pd.DataFrame):
    df = df.copy()
    df["mid"] = df["close"].rolling(WINDOW).mean()
    std = df["close"].rolling(WINDOW).std()
    df["upper"] = df["mid"] + STD_MULT * std
    df["lower"] = df["mid"] - STD_MULT * std
    df["breakout_up"] = (df["close"] > df["upper"]) & (df["close"].shift(1) <= df["upper"].shift(1))
    df["breakout_down"] = (df["close"] < df["lower"]) & (df["close"].shift(1) >= df["lower"].shift(1))
    tr = np.maximum(df["high"] - df["low"],
                     np.maximum((df["high"] - df["close"].shift(1)).abs(),
                                (df["low"] - df["close"].shift(1)).abs()))
    df["atr"] = tr.rolling(ATR_WINDOW).mean()
    df["date"] = df["datetime"].dt.date
    return df


def backtest_with_full_cost(df: pd.DataFrame):
    """真實spread(進出場各一次) + 手續費(進出場各0.002%)。"""
    trades = []
    pos = 0
    entry_p = entry_i = sl_p = 0
    entry_spread = entry_stop_dist = 0.0

    for i in range(len(df)):
        r = df.iloc[i]
        if pd.isna(r["mid"]) or pd.isna(r["atr"]):
            continue
        c, h, l, mid = r["close"], r["high"], r["low"], r["mid"]
        exit_spread = (r["spread_bps"] / 1e4) if not pd.isna(r["spread_bps"]) else 0.0003
        exit_commission = COMMISSION_PCT_PER_SIDE / 100

        if pos == 1:
            hit_sl = l <= sl_p
            reverted = c >= mid
            timeout = (i - entry_i) >= MAX_HOLD_BARS
            if hit_sl or reverted or timeout:
                exit_p = sl_p if hit_sl else c
                pnl = ((exit_p - entry_p) / entry_p - entry_spread - exit_spread
                       - entry_commission - exit_commission)
                trades.append({"exit_t": r["datetime"], "pnl": pnl, "type": "LONG", "stop_dist_pct": entry_stop_dist})
                pos = 0
        elif pos == -1:
            hit_sl = h >= sl_p
            reverted = c <= mid
            timeout = (i - entry_i) >= MAX_HOLD_BARS
            if hit_sl or reverted or timeout:
                exit_p = sl_p if hit_sl else c
                pnl = ((entry_p - exit_p) / entry_p - entry_spread - exit_spread
                       - entry_commission - exit_commission)
                trades.append({"exit_t": r["datetime"], "pnl": pnl, "type": "SHORT", "stop_dist_pct": entry_stop_dist})
                pos = 0

        if pos == 0:
            entry_commission = COMMISSION_PCT_PER_SIDE / 100
            if r["breakout_down"]:
                pos, entry_p, entry_i, sl_p = 1, c, i, c - ATR_MULT * r["atr"]
                entry_spread = exit_spread
                entry_stop_dist = (entry_p - sl_p) / entry_p
            elif r["breakout_up"]:
                pos, entry_p, entry_i, sl_p = -1, c, i, c + ATR_MULT * r["atr"]
                entry_spread = exit_spread
                entry_stop_dist = (sl_p - entry_p) / entry_p

    return pd.DataFrame(trades)


def apply_risk_based_sizing(tdf: pd.DataFrame, risk_pct=RISK_PER_TRADE_PCT, max_mult=MAX_SIZE_MULT):
    if tdf.empty or "stop_dist_pct" not in tdf.columns:
        return tdf
    tdf = tdf.copy()
    mult = (risk_pct / 100) / tdf["stop_dist_pct"].replace(0, np.nan)
    mult = mult.clip(upper=max_mult).fillna(1.0)
    tdf["pnl"] = tdf["pnl"] * mult
    return tdf


def apply_daily_loss_limit(tdf: pd.DataFrame, limit_pct=DAILY_LOSS_LIMIT_PCT):
    if tdf.empty:
        return tdf
    tdf = tdf.sort_values("exit_t").copy()
    tdf["date"] = tdf["exit_t"].dt.date
    kept_rows = []
    for date, group in tdf.groupby("date"):
        cum, halted = 0.0, False
        for _, row in group.iterrows():
            if halted:
                continue
            kept_rows.append(row)
            cum += row["pnl"]
            if cum <= -limit_pct / 100:
                halted = True
    return pd.DataFrame(kept_rows).drop(columns=["date"]) if kept_rows else pd.DataFrame(columns=tdf.columns)


def main():
    all_stats = []
    per_symbol_trades = {}

    print(f"回測 {len(TRADE_LIST)} 檔最終交易名單，含真實spread(進出場各一次)+"
          f"手續費({COMMISSION_PCT_PER_SIDE}%/次,進出場各一次)+風險部位大小+每日熔斷 ...\n")

    for sym in TRADE_LIST:
        raw = load_symbol_15m(sym)
        feat = compute_features(raw)
        split_i = int(len(raw) * TRAIN_FRACTION)
        split_time = raw["datetime"].iloc[split_i]

        tdf = backtest_with_full_cost(feat)
        if tdf.empty:
            continue
        tdf = tdf.sort_values("exit_t").reset_index(drop=True)

        # 套用已驗證有效的優化：風險部位大小 + 每日虧損熔斷
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
            all_stats.append(stats)

    summary = pd.DataFrame(all_stats)
    cols = ["symbol", "split", "n_trades", "period_days", "total_return_pct", "ann_return_pct",
            "ann_sharpe", "max_drawdown_pct", "win_rate_pct", "t_stat"]
    summary = summary[cols]
    os.makedirs("residual_alpha_results", exist_ok=True)
    summary.to_csv("residual_alpha_results/phase7_final_list_full_cost.csv", index=False)

    pd.set_option("display.width", 170)
    test_only = summary[summary["split"] == "TEST"].sort_values("t_stat", ascending=False)
    print("=== TEST期結果（真實spread+手續費+風控優化，依t-stat排序）===")
    print(test_only.to_string(index=False))
    print(f"\n平均t-stat: {test_only['t_stat'].mean():.2f}  中位數: {test_only['t_stat'].median():.2f}")
    print(f"t-stat>=1.5的股票數: {(test_only['t_stat']>=1.5).sum()} / {len(test_only)}")

    # --- Pooled equal-weight（14檔資金平均分配）---
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
        return {"split": split_name, "n_trades": sum(len(f) for f in frames), "period_days": round(days, 1),
                "total_return_pct": total_return * 100, "ann_return_pct": ann_return * 100,
                "ann_sharpe": sharpe, "max_drawdown_pct": max_dd * 100}

    pooled = pd.DataFrame([pooled_stats("TRAIN"), pooled_stats("TEST")])
    print("\n=== Pooled equal-weight（14檔資金平均分配，含全部成本+風控優化）===")
    print(pooled.to_string(index=False))
    pooled.to_csv("residual_alpha_results/phase7_pooled_full_cost.csv", index=False)


if __name__ == "__main__":
    main()
