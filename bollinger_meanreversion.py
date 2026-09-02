# -*- coding: utf-8 -*-
"""
Phase 4b：Bollinger Band 均值回歸（逆勢）—— Phase 4 順勢版本的反向假說

Phase 4 發現「通道突破後做順勢」在指數上系統性顯著虧損（3種不同策略都指向同一結論：
這批資料 intraday 層面偏 mean-reverting）。這裡直接反過來做：

  breakout_up（收盤突破上緣）  -> 做空（賭會拉回均值，不是繼續噴出去）
  breakout_down（收盤跌破下緣）-> 做多（賭會反彈回均值，不是繼續破底）

  出場：反彈/回落到 band_mid 視為均值回歸完成（獲利了結）、
        或 1.5xATR(14) 停損（如果繼續往不利方向噴出去）、
        或最長持有 240 根 bar，三者先到先出場。

其餘資料/方法論（15m、30m，MA20/MA50，股票用MT5 1m resample、指數用TV 5m resample，
train/test split）跟 Phase 4 完全一致，只有進出場方向相反，方便直接比較。
"""

import os
import numpy as np
import pandas as pd

from bollinger_breakout_trend import (
    load_mt5_1m, load_tv_index_5m, resample_ohlcv, compute_bands, stats_of,
    TRAIN_FRACTION, ATR_MULT, MAX_HOLD_BARS,
    MT5_SYMBOLS, TV_INDEX_SYMBOLS,
)


def backtest_mean_reversion(df: pd.DataFrame):
    trades = []
    pos = 0
    entry_p = entry_i = sl_p = 0

    for i in range(len(df)):
        r = df.iloc[i]
        if pd.isna(r["mid"]) or pd.isna(r["atr"]):
            continue
        c, h, l, mid = r["close"], r["high"], r["low"], r["mid"]

        if pos == 1:
            hit_sl = l <= sl_p
            reverted = c >= mid
            timeout = (i - entry_i) >= MAX_HOLD_BARS
            if hit_sl or reverted or timeout:
                exit_p = sl_p if hit_sl else c
                cost = (r["spread_bps"] / 1e4) if not pd.isna(r["spread_bps"]) else 0.0003
                pnl = (exit_p - entry_p) / entry_p - cost
                trades.append({"exit_t": r["datetime"], "pnl": pnl, "type": "LONG"})
                pos = 0
        elif pos == -1:
            hit_sl = h >= sl_p
            reverted = c <= mid
            timeout = (i - entry_i) >= MAX_HOLD_BARS
            if hit_sl or reverted or timeout:
                exit_p = sl_p if hit_sl else c
                cost = (r["spread_bps"] / 1e4) if not pd.isna(r["spread_bps"]) else 0.0003
                pnl = (entry_p - exit_p) / entry_p - cost
                trades.append({"exit_t": r["datetime"], "pnl": pnl, "type": "SHORT"})
                pos = 0

        if pos == 0:
            if r["breakout_down"]:               # 跌破下緣 -> 買（賭反彈）
                pos, entry_p, entry_i, sl_p = 1, c, i, c - ATR_MULT * r["atr"]
            elif r["breakout_up"]:                # 突破上緣 -> 空（賭拉回）
                pos, entry_p, entry_i, sl_p = -1, c, i, c + ATR_MULT * r["atr"]

    return pd.DataFrame(trades)


def run_universe(universe_name, symbols, loader, resample_rules, windows):
    rows = []
    for sym in symbols:
        raw_native = loader(sym)
        for tf_name, rule in resample_rules.items():
            raw = resample_ohlcv(raw_native, rule)
            if len(raw) < 200:
                continue
            split_i = int(len(raw) * TRAIN_FRACTION)
            split_time = raw["datetime"].iloc[split_i]

            for window in windows:
                feat = compute_bands(raw, window)
                tdf = backtest_mean_reversion(feat)
                if tdf.empty:
                    continue
                train_tdf = tdf[tdf["exit_t"] < split_time]
                test_tdf = tdf[tdf["exit_t"] >= split_time]
                for split_name, sub in [("TRAIN", train_tdf), ("TEST", test_tdf)]:
                    s = stats_of(sub)
                    s.update({"universe": universe_name, "symbol": sym, "timeframe": tf_name,
                               "ma_window": window, "split": split_name})
                    rows.append(s)
    return rows


def main():
    resample_rules = {"15m": "15min", "30m": "30min"}
    windows = [20, 50]

    print("=== 股票（MT5 8檔半導體，1m resample）===")
    rows_stocks = run_universe("stocks_mt5", MT5_SYMBOLS, load_mt5_1m, resample_rules, windows)

    print("=== 指數（TradingView 7檔，5m resample）===")
    rows_indices = run_universe("indices_tv", TV_INDEX_SYMBOLS, load_tv_index_5m, resample_rules, windows)

    summary = pd.DataFrame(rows_stocks + rows_indices)
    cols = ["universe", "symbol", "timeframe", "ma_window", "split", "n_trades",
            "win_rate", "profit_factor", "total_return_pct", "t_stat"]
    summary = summary[cols]
    os.makedirs("residual_alpha_results", exist_ok=True)
    summary.to_csv("residual_alpha_results/phase4b_bollinger_meanreversion.csv", index=False)

    pd.set_option("display.width", 170)
    print("\n" + "=" * 100)
    print("=== 依 universe+timeframe+window+split 分組整體統計 ===")
    print("=" * 100)
    agg = summary.groupby(["universe", "timeframe", "ma_window", "split"]).agg(
        n_symbols=("symbol", "nunique"),
        total_trades=("n_trades", "sum"),
        mean_t_stat=("t_stat", "mean"),
        pct_t_ge_1_5=("t_stat", lambda x: (x >= 1.5).mean() * 100),
        mean_total_return_pct=("total_return_pct", "mean"),
        mean_win_rate=("win_rate", "mean"),
    )
    print(agg.to_string())

    print("\n=== 逐商品明細（依 t_stat 排序，僅顯示 TEST）===")
    test_only = summary[summary["split"] == "TEST"].sort_values("t_stat", ascending=False)
    print(test_only.to_string(index=False))


if __name__ == "__main__":
    main()
