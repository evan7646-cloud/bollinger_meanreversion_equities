# -*- coding: utf-8 -*-
"""
Phase 4：Bollinger Band 突破順勢策略（20MA / 50MA ± 2 標準差），15m / 30m

規則（單純突破，這次不疊加量能/加速度濾網 —— Phase 2a/3 已經證明疊加技術面濾網
      系統性地讓表現變差，這次先測「乾淨版本」有沒有 edge，避免重蹈覆轍）：
  band_mid = close.rolling(window).mean()      # window = 20 或 50
  band_std = close.rolling(window).std(ddof=0)   # 母體標準差，跟 MT5 iBands 一致
  upper = band_mid + 2 * band_std
  lower = band_mid - 2 * band_std

  breakout_up：close 由下往上穿越 upper -> 做多（賭突破後延續，順勢）
  breakout_down：close 由上往下穿越 lower -> 做空

  出場：close 反向穿越回 band_mid（均值回歸到通道中軸視為順勢動能結束）、
        或 1.5xATR(14) 停損、或最長持有 240 根 bar，三者先到先出場。

資料：
  股票：MT5 8 檔半導體股 1m 資料 resample 到 15m/30m（歷史約一年，train/test 相對可靠）
  指數：TradingView 7 個指數 5m 資料 resample 到 15m/30m（只有3個月，較不可靠，僅供參考）

一樣做 train/test（前75% vs 後25%）。
"""

import os
import glob
import numpy as np
import pandas as pd

TRAIN_FRACTION = 0.75
ATR_WINDOW = 14
ATR_MULT = 1.5
MAX_HOLD_BARS = 240
STD_MULT = 2.0

MT5_DIR = "data_mt5_semiconductor_ticks"
MT5_SYMBOLS = ["AMD", "AMAT", "AVGO", "INTC", "MU", "NVDA", "QCOM", "TSM"]
TV_INDEX_DIR = "data_tv_indices_5m"
TV_INDEX_SYMBOLS = ["SP500", "NAS100", "US30", "UK100", "JPN225", "AUS200", "FRA40"]


def load_mt5_1m(ticker: str) -> pd.DataFrame:
    df = pd.read_csv(os.path.join(MT5_DIR, f"MT5_{ticker}_1m.csv"))
    df["datetime"] = pd.to_datetime(df["datetime"], format="%Y.%m.%d %H:%M")
    df = df.sort_values("datetime").reset_index(drop=True)
    df = df.rename(columns={"tick_volume": "volume"})
    return df[["datetime", "open", "high", "low", "close", "volume", "spread_bps"]]


def load_tv_index_5m(name: str) -> pd.DataFrame:
    df = pd.read_csv(os.path.join(TV_INDEX_DIR, f"{name}_5m.csv"))
    df.rename(columns={"datetime": "timestamp"}, inplace=True)
    df["datetime"] = pd.to_datetime(df["timestamp"])
    df = df.sort_values("datetime").reset_index(drop=True)
    df["spread_bps"] = 3.0
    return df[["datetime", "open", "high", "low", "close", "volume", "spread_bps"]]


def resample_ohlcv(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    df = df.set_index("datetime")
    out = df.resample(rule).agg({
        "open": "first", "high": "max", "low": "min", "close": "last",
        "volume": "sum", "spread_bps": "mean",
    }).dropna(subset=["open", "high", "low", "close"])
    return out.reset_index()


def compute_bands(df: pd.DataFrame, window: int) -> pd.DataFrame:
    df = df.copy()
    df["mid"] = df["close"].rolling(window).mean()
    # ddof=0（母體標準差，除以N）是布林通道的原始定義，也是 MT5 iBands 實際使用的公式。
    # pandas 的 .std() 預設 ddof=1（樣本標準差，除以N-1），會讓通道寬 2.6%(=sqrt(20/19))，
    # 導致回測訊號跟實盤 EA 對不起來（已用 ExportBandsCheck.mq5 匯出481根iBands實測驗證）。
    std = df["close"].rolling(window).std(ddof=0)
    df["upper"] = df["mid"] + STD_MULT * std
    df["lower"] = df["mid"] - STD_MULT * std
    df["breakout_up"] = (df["close"] > df["upper"]) & (df["close"].shift(1) <= df["upper"].shift(1))
    df["breakout_down"] = (df["close"] < df["lower"]) & (df["close"].shift(1) >= df["lower"].shift(1))

    tr = np.maximum(df["high"] - df["low"],
                     np.maximum((df["high"] - df["close"].shift(1)).abs(),
                                (df["low"] - df["close"].shift(1)).abs()))
    df["atr"] = tr.rolling(ATR_WINDOW).mean()
    return df


def backtest(df: pd.DataFrame):
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
            revert = c <= mid
            timeout = (i - entry_i) >= MAX_HOLD_BARS
            if hit_sl or revert or timeout:
                exit_p = sl_p if hit_sl else c
                cost = (r["spread_bps"] / 1e4) if not pd.isna(r["spread_bps"]) else 0.0003
                pnl = (exit_p - entry_p) / entry_p - cost
                trades.append({"exit_t": r["datetime"], "pnl": pnl, "type": "LONG"})
                pos = 0
        elif pos == -1:
            hit_sl = h >= sl_p
            revert = c >= mid
            timeout = (i - entry_i) >= MAX_HOLD_BARS
            if hit_sl or revert or timeout:
                exit_p = sl_p if hit_sl else c
                cost = (r["spread_bps"] / 1e4) if not pd.isna(r["spread_bps"]) else 0.0003
                pnl = (entry_p - exit_p) / entry_p - cost
                trades.append({"exit_t": r["datetime"], "pnl": pnl, "type": "SHORT"})
                pos = 0

        if pos == 0:
            if r["breakout_up"]:
                pos, entry_p, entry_i, sl_p = 1, c, i, c - ATR_MULT * r["atr"]
            elif r["breakout_down"]:
                pos, entry_p, entry_i, sl_p = -1, c, i, c + ATR_MULT * r["atr"]

    return pd.DataFrame(trades)


def stats_of(tdf: pd.DataFrame):
    n = len(tdf)
    if n == 0:
        return {"n_trades": 0, "win_rate": np.nan, "profit_factor": np.nan,
                "total_return_pct": np.nan, "t_stat": np.nan}
    wins = tdf[tdf["pnl"] > 0]
    losses = tdf[tdf["pnl"] <= 0]
    win_rate = len(wins) / n * 100
    pf = wins["pnl"].sum() / (abs(losses["pnl"].sum()) + 1e-8)
    tot_ret = ((1 + tdf["pnl"]).cumprod().iloc[-1] - 1) * 100
    mean_pnl, std_pnl = tdf["pnl"].mean(), tdf["pnl"].std()
    t_stat = (mean_pnl / std_pnl) * np.sqrt(n) if std_pnl > 0 else np.nan
    return {"n_trades": n, "win_rate": win_rate, "profit_factor": pf,
            "total_return_pct": tot_ret, "t_stat": t_stat}


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
                tdf = backtest(feat)
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
    summary.to_csv("residual_alpha_results/phase4_bollinger_breakout.csv", index=False)

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
