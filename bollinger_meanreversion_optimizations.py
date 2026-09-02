# -*- coding: utf-8 -*-
"""
Phase 6：對已驗證的 Bollinger 均值回歸策略（15m, MA20+-2sigma）測試 4 個優化方向

背景：美股日內交易策略研究報告.md 提出幾個文獻上的優化原則（該報告是文獻整理，
不是我們自己資料的回測，數字不能直接套用，但設計原則可以借來測）。這裡逐一實測：

  A) RVOL 日濾網：只在「今天 tick_volume 相對過去20天明顯放大」的日子交易
     （broker沒有真實成交量，用 tick_volume 當活躍度替代指標）
  B) 風險部位大小 + 每日虧損熔斷：不影響進出場訊號，只影響倉位大小與是否繼續交易
  C) VWAP 動態移動停損：取代原本固定 1.5xATR 停損
  D) 收盤前強制平倉：取代原本「最長持有240根bar」，避免跨日跳空風險

用 5 檔已驗證的半導體+設備股（AMD, AVGO, QCOM, INTC, ASML）在 Equities CFD 資料上測試，
逐一加上每個優化、對比 baseline，看 t-stat / Sharpe / MaxDD 有沒有改善。
"""

import os
import numpy as np
import pandas as pd

from bollinger_breakout_trend import TRAIN_FRACTION, ATR_WINDOW
from bollinger_meanreversion_equities_cfd_test import load_symbol_15m
from bollinger_meanreversion_performance_report import full_performance_stats

DATA_DIR = "data_mt5_equities_cfd"
WINDOW = 20
STD_MULT = 2.0
ATR_MULT = 1.5
MAX_HOLD_BARS = 240
TEST_SYMBOLS = ["AMD", "AVGO", "QCOM", "INTC", "ASML"]

RISK_PER_TRADE_PCT = 1.0     # 優化B：每筆風險佔帳戶1%
DAILY_LOSS_LIMIT_PCT = 3.0   # 優化B：單日虧損達3%停止當日交易
RVOL_LOOKBACK_DAYS = 20      # 優化A：RVOL回看天數
RVOL_THRESHOLD = 1.2         # 優化A：當日tick_volume要達到過去20天均值的1.2倍才交易


def compute_features(df: pd.DataFrame, window: int = WINDOW):
    df = df.copy()
    df["mid"] = df["close"].rolling(window).mean()
    std = df["close"].rolling(window).std()
    df["upper"] = df["mid"] + STD_MULT * std
    df["lower"] = df["mid"] - STD_MULT * std
    df["breakout_up"] = (df["close"] > df["upper"]) & (df["close"].shift(1) <= df["upper"].shift(1))
    df["breakout_down"] = (df["close"] < df["lower"]) & (df["close"].shift(1) >= df["lower"].shift(1))

    tr = np.maximum(df["high"] - df["low"],
                     np.maximum((df["high"] - df["close"].shift(1)).abs(),
                                (df["low"] - df["close"].shift(1)).abs()))
    df["atr"] = tr.rolling(ATR_WINDOW).mean()

    # --- 優化A: 日層級 RVOL（用 tick_volume 當活躍度替代真實成交量）---
    df["date"] = df["datetime"].dt.date
    daily_vol = df.groupby("date")["volume"].sum()
    daily_vol_ma = daily_vol.rolling(RVOL_LOOKBACK_DAYS).mean()
    rvol = (daily_vol / daily_vol_ma).rename("rvol")
    df["rvol"] = df["date"].map(rvol)

    # --- 優化C: VWAP（同樣用 tick_volume 加權，因為沒有真實成交量）---
    df["tp"] = (df["high"] + df["low"] + df["close"]) / 3.0
    df["pv"] = df["tp"] * df["volume"]
    cum_pv = df.groupby("date")["pv"].cumsum()
    cum_vol = df.groupby("date")["volume"].cumsum()
    df["vwap"] = cum_pv / (cum_vol + 1e-8)

    # --- 優化D: 每日剩餘bar數（用於收盤前強制平倉）---
    df["bar_seq"] = df.groupby("date").cumcount()
    df["day_len"] = df.groupby("date")["bar_seq"].transform("max")

    return df


def backtest(df: pd.DataFrame, use_rvol_filter=False, use_vwap_stop=False, use_eod_flat=False):
    trades = []
    pos = 0
    entry_p = entry_i = sl_p = 0
    entry_stop_dist = 0.0

    for i in range(len(df)):
        r = df.iloc[i]
        if pd.isna(r["mid"]) or pd.isna(r["atr"]):
            continue
        c, h, l, mid, vwap = r["close"], r["high"], r["low"], r["mid"], r["vwap"]

        if pos == 1:
            hard_sl = l <= sl_p  # 固定ATR停損永遠當硬底線（intrabar觸發，用停損價成交）
            vwap_sl = use_vwap_stop and (c <= vwap - ATR_MULT * r["atr"] * 0.5)  # VWAP移動停損（用收盤價觸發）
            reverted = c >= mid
            timeout = (i - entry_i) >= MAX_HOLD_BARS
            eod = use_eod_flat and (r["bar_seq"] >= r["day_len"] - 1)
            if hard_sl or vwap_sl or reverted or timeout or eod:
                exit_p = sl_p if hard_sl else c
                pnl = (exit_p - entry_p) / entry_p - (r["spread_bps"] / 1e4 if not pd.isna(r["spread_bps"]) else 0.0003)
                trades.append({"exit_t": r["datetime"], "pnl": pnl, "type": "LONG", "stop_dist_pct": entry_stop_dist})
                pos = 0
        elif pos == -1:
            hard_sl = h >= sl_p
            vwap_sl = use_vwap_stop and (c >= vwap + ATR_MULT * r["atr"] * 0.5)
            reverted = c <= mid
            timeout = (i - entry_i) >= MAX_HOLD_BARS
            eod = use_eod_flat and (r["bar_seq"] >= r["day_len"] - 1)
            if hard_sl or vwap_sl or reverted or timeout or eod:
                exit_p = sl_p if hard_sl else c
                pnl = (entry_p - exit_p) / entry_p - (r["spread_bps"] / 1e4 if not pd.isna(r["spread_bps"]) else 0.0003)
                trades.append({"exit_t": r["datetime"], "pnl": pnl, "type": "SHORT", "stop_dist_pct": entry_stop_dist})
                pos = 0

        if pos == 0:
            rvol_ok = True if not use_rvol_filter else (not pd.isna(r["rvol"]) and r["rvol"] >= RVOL_THRESHOLD)
            if not use_eod_flat or r["bar_seq"] < r["day_len"] - 1:  # 收盤前強制平倉版本不在最後一根新開倉
                if r["breakout_down"] and rvol_ok:
                    pos, entry_p, entry_i, sl_p = 1, c, i, c - ATR_MULT * r["atr"]
                    entry_stop_dist = (entry_p - sl_p) / entry_p
                elif r["breakout_up"] and rvol_ok:
                    pos, entry_p, entry_i, sl_p = -1, c, i, c + ATR_MULT * r["atr"]
                    entry_stop_dist = (sl_p - entry_p) / entry_p

    return pd.DataFrame(trades)


def apply_daily_loss_limit(tdf: pd.DataFrame):
    """優化B：每日累積虧損達到 DAILY_LOSS_LIMIT_PCT 後，當天剩餘交易的pnl歸零
    （模擬熔斷後不再交易，但不能改變已發生的交易，只影響同一天後續的新交易）。"""
    if tdf.empty:
        return tdf
    tdf = tdf.sort_values("exit_t").copy()
    tdf["date"] = tdf["exit_t"].dt.date
    kept_rows = []
    for date, group in tdf.groupby("date"):
        cum = 0.0
        halted = False
        for _, row in group.iterrows():
            if halted:
                continue  # 熔斷後，當天後續交易視為未發生（不納入）
            kept_rows.append(row)
            cum += row["pnl"]
            if cum <= -DAILY_LOSS_LIMIT_PCT / 100:
                halted = True
    return pd.DataFrame(kept_rows).drop(columns=["date"]) if kept_rows else pd.DataFrame(columns=tdf.columns)


def apply_risk_based_sizing(tdf: pd.DataFrame, risk_pct: float = RISK_PER_TRADE_PCT, max_mult: float = 3.0):
    """優化B：用「帳戶風險1% / 停損距離」決定每筆交易的部位大小，取代固定100%曝險。
    停損距離越遠（風險越大）-> 部位越小；停損距離越近 -> 部位越大（但設上限避免槓桿失控）。
    position_multiplier = (risk_pct/100) / stop_dist_pct，再把原本用100%曝險算出的
    百分比報酬乘上這個倍數，近似模擬「用風險%決定部位大小」的資金曲線效果。"""
    if tdf.empty or "stop_dist_pct" not in tdf.columns:
        return tdf
    tdf = tdf.copy()
    mult = (risk_pct / 100) / tdf["stop_dist_pct"].replace(0, np.nan)
    mult = mult.clip(upper=max_mult).fillna(1.0)
    tdf["pnl"] = tdf["pnl"] * mult
    return tdf


def main():
    print("讀取資料並套用優化 A/C/D 的特徵計算 ...")
    features = {}
    for sym in TEST_SYMBOLS:
        raw = load_symbol_15m(sym)
        feat = compute_features(raw)
        split_i = int(len(raw) * TRAIN_FRACTION)
        split_time = raw["datetime"].iloc[split_i]
        features[sym] = (feat, split_time, raw["datetime"].iloc[0], raw["datetime"].iloc[-1])

    variants = [
        ("A_baseline", dict(use_rvol_filter=False, use_vwap_stop=False, use_eod_flat=False)),
        ("B_rvol_filter", dict(use_rvol_filter=True, use_vwap_stop=False, use_eod_flat=False)),
        ("C_vwap_stop", dict(use_rvol_filter=False, use_vwap_stop=True, use_eod_flat=False)),
        ("D_eod_flat", dict(use_rvol_filter=False, use_vwap_stop=False, use_eod_flat=True)),
        ("E_all_combined", dict(use_rvol_filter=True, use_vwap_stop=True, use_eod_flat=True)),
    ]

    all_rows = []
    daily_loss_rows = []

    for sym in TEST_SYMBOLS:
        feat, split_time, start, end = features[sym]
        for label, kwargs in variants:
            tdf = backtest(feat, **kwargs)
            if tdf.empty:
                continue
            tdf = tdf.sort_values("exit_t").reset_index(drop=True)
            test_tdf = tdf[tdf["exit_t"] >= split_time]
            test_days = (end - split_time).total_seconds() / 86400
            stats, _ = full_performance_stats(test_tdf, test_days)
            if stats is None:
                continue
            stats["symbol"] = sym
            stats["variant"] = label
            all_rows.append(stats)

            # 優化B（每日虧損熔斷 + 風險部位大小）獨立套用在 baseline 交易序列上比較
            if label == "A_baseline":
                limited = apply_daily_loss_limit(test_tdf)
                stats2, _ = full_performance_stats(limited, test_days)
                if stats2 is not None:
                    stats2["symbol"] = sym
                    stats2["variant"] = "F_daily_loss_limit"
                    daily_loss_rows.append(stats2)

                risk_sized = apply_risk_based_sizing(test_tdf)
                stats3, _ = full_performance_stats(risk_sized, test_days)
                if stats3 is not None:
                    stats3["symbol"] = sym
                    stats3["variant"] = "G_risk_based_sizing"
                    daily_loss_rows.append(stats3)

    all_rows.extend(daily_loss_rows)
    summary = pd.DataFrame(all_rows)
    cols = ["symbol", "variant", "n_trades", "period_days", "total_return_pct", "ann_return_pct",
            "ann_sharpe", "max_drawdown_pct", "win_rate_pct", "t_stat"]
    summary = summary[cols]
    os.makedirs("residual_alpha_results", exist_ok=True)
    summary.to_csv("residual_alpha_results/phase6_optimizations_comparison.csv", index=False)

    pd.set_option("display.width", 170)
    print("\n=== 逐股票逐優化版本結果（TEST期）===")
    print(summary.sort_values(["symbol", "variant"]).to_string(index=False))

    print("\n=== 依 variant 分組平均（跨5檔）===")
    agg = summary.groupby("variant").agg(
        mean_t_stat=("t_stat", "mean"),
        mean_sharpe=("ann_sharpe", "mean"),
        mean_maxdd=("max_drawdown_pct", "mean"),
        mean_total_return=("total_return_pct", "mean"),
        mean_n_trades=("n_trades", "mean"),
    )
    order = ["A_baseline", "B_rvol_filter", "C_vwap_stop", "D_eod_flat", "E_all_combined",
             "F_daily_loss_limit", "G_risk_based_sizing"]
    print(agg.reindex(order).to_string())


if __name__ == "__main__":
    main()
