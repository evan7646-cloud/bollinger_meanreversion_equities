import os
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

# ==========================================================================================
# 4H (或以上) 週期驗證版
# 資料來源 A：data_ibkr_5m -> 重採樣為 4H / 1D。真實股票，1年，成本用 mt5_spread_by_symbol.csv 平均點差估算。
# 資料來源 B：data_mt5_equities_cfd -> 重採樣為 4H / 1D。真實 broker 逐根真實點差 (spread_bps 欄位)，
#            只取 2022-01-01 之後區間 (更早年份 spread 欄位大多為 0，資料不可信，見前次分析)，
#            樣本長度約 4.7 年，用來補足真實股票資料只有 1 年、4H 週期下交易次數過少的統計力問題。
# 邏輯完全比照 strategy_logic_and_backtest_truth.md 第 3 節狀態機，不新增規則。
# ==========================================================================================

INITIAL_CAPITAL = 25000.0
BASE_ORDER_USD = 1500.0
MAX_DCA_LAYERS = 4
CHANNEL_K = 2.0
DCA_STEP_ATR = 1.5
STOP_ATR = 4.0
COMMISSION_BPS = 2.0
DEFAULT_SPREAD_BPS = 5.0


def load_spread_map():
    try:
        df = pd.read_csv("mt5_spread_by_symbol.csv")
        return df.set_index("symbol")["avg_spread_bps"].to_dict()
    except Exception:
        return {}


def resample_session_anchored(df, bar_minutes):
    """美股常規時段 (09:30-16:00) 資料，從開盤時間起錨定分箱，避免用日曆時間切出不完整的假 K 棒。"""
    df = df.copy()
    df["date"] = df["datetime"].dt.date
    minutes_since_open = (df["datetime"].dt.hour - 9) * 60 + (df["datetime"].dt.minute - 30)
    df["bin"] = minutes_since_open // bar_minutes
    grouped = df.groupby(["date", "bin"], sort=True)
    agg = grouped.agg(
        datetime=("datetime", "first"),
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        volume=("volume", "sum"),
    ).reset_index(drop=True)
    return agg.sort_values("datetime").reset_index(drop=True)


def resample_calendar(df, freq):
    """CFD 近乎連續交易的資料，直接用日曆時間切 (freq='4h' 或 '1D')。"""
    d = df.set_index("datetime")
    agg_dict = {"open": "first", "high": "max", "low": "min", "close": "last"}
    if "spread_bps" in d.columns:
        agg_dict["spread_bps"] = "mean"
    out = d.resample(freq, label="left", closed="left").agg(agg_dict)
    out = out.dropna(subset=["open", "high", "low", "close"]).reset_index()
    return out


def run_grid_strategy(data: pd.DataFrame, default_spread_bps: float, use_bar_spread=False):
    data = data.copy()
    data["tr"] = np.maximum(
        data["high"] - data["low"],
        np.maximum(
            (data["high"] - data["close"].shift(1)).abs(),
            (data["low"] - data["close"].shift(1)).abs(),
        ),
    )
    data["atr"] = data["tr"].rolling(window=20).mean()
    data["ema50"] = data["close"].ewm(span=50, adjust=False).mean()
    data["lower_band"] = data["ema50"] - data["atr"] * CHANNEL_K
    data = data.dropna(subset=["atr", "ema50", "lower_band"]).reset_index(drop=True)
    if len(data) < 30:
        return None

    commission_rate = COMMISSION_BPS / 10000.0

    cash = INITIAL_CAPITAL
    shares = 0.0
    layer = 0
    avg_entry_price = 0.0
    entry_cost_basis = 0.0

    equity_curve = []
    trades = []

    for _, row in data.iterrows():
        close, high, low = row["close"], row["high"], row["low"]
        atr, ema50, lower_band = row["atr"], row["ema50"], row["lower_band"]

        if use_bar_spread and "spread_bps" in row and row["spread_bps"] and row["spread_bps"] > 0:
            spread_bps = row["spread_bps"]
        else:
            spread_bps = default_spread_bps
        half_spread = (spread_bps / 10000.0) / 2.0

        if layer == 0:
            if low <= lower_band:
                exec_price = min(close, lower_band) * (1.0 + half_spread)
                buy_qty = BASE_ORDER_USD / exec_price
                comm = exec_price * buy_qty * commission_rate
                cost = exec_price * buy_qty + comm
                if cash >= cost and buy_qty > 0:
                    cash -= cost
                    shares = buy_qty
                    avg_entry_price = exec_price
                    entry_cost_basis = cost
                    layer = 1
        else:
            if high >= ema50:
                exec_price = ema50 * (1.0 - half_spread)
                revenue = exec_price * shares
                comm = revenue * commission_rate
                cash += revenue - comm
                pnl = (revenue - comm) - entry_cost_basis
                trades.append({"exit": "TP", "pnl": pnl})
                shares, layer, avg_entry_price, entry_cost_basis = 0.0, 0, 0.0, 0.0
            elif low <= avg_entry_price - atr * STOP_ATR:
                exec_price = (avg_entry_price - atr * STOP_ATR) * (1.0 - half_spread)
                revenue = exec_price * shares
                comm = revenue * commission_rate
                cash += revenue - comm
                pnl = (revenue - comm) - entry_cost_basis
                trades.append({"exit": "SL", "pnl": pnl})
                shares, layer, avg_entry_price, entry_cost_basis = 0.0, 0, 0.0, 0.0
            elif layer < MAX_DCA_LAYERS:
                next_trigger = avg_entry_price - (layer * atr * DCA_STEP_ATR)
                if low <= next_trigger:
                    exec_price = next_trigger * (1.0 + half_spread)
                    buy_qty = (BASE_ORDER_USD * 1.2) / exec_price
                    comm = exec_price * buy_qty * commission_rate
                    cost = exec_price * buy_qty + comm
                    if cash >= cost and buy_qty > 0:
                        cash -= cost
                        total_cost_val = avg_entry_price * shares + exec_price * buy_qty
                        shares += buy_qty
                        avg_entry_price = total_cost_val / shares
                        entry_cost_basis += cost
                        layer += 1

        equity_curve.append(cash + shares * close)

    eq = pd.Series(equity_curve)
    final_equity = eq.iloc[-1]
    total_return_pct = (final_equity - INITIAL_CAPITAL) / INITIAL_CAPITAL * 100.0

    peak = eq.cummax()
    dd = (eq - peak) / peak
    max_dd_pct = abs(dd.min()) * 100.0

    elapsed_days = (data["datetime"].iloc[-1] - data["datetime"].iloc[0]).total_seconds() / 86400.0
    years = max(elapsed_days / 365.25, 1e-6)
    bars_per_year = len(data) / years
    rets = eq.pct_change().dropna()
    sharpe = (rets.mean() / rets.std()) * np.sqrt(bars_per_year) if rets.std() > 0 else 0.0

    n_trades = len(trades)
    wins = [t["pnl"] for t in trades if t["pnl"] > 0]
    losses = [t["pnl"] for t in trades if t["pnl"] <= 0]
    win_rate = len(wins) / n_trades * 100.0 if n_trades else 0.0
    profit_factor = (sum(wins) / abs(sum(losses))) if losses and sum(losses) != 0 else np.nan
    years_covered = years

    return {
        "net_profit_usd": final_equity - INITIAL_CAPITAL,
        "total_return_pct": total_return_pct,
        "annualized_return_pct": ((1 + total_return_pct / 100.0) ** (1 / years) - 1) * 100.0 if years > 0.1 else np.nan,
        "max_dd_pct": max_dd_pct,
        "sharpe": sharpe,
        "win_rate": win_rate,
        "n_trades": n_trades,
        "n_tp": sum(1 for t in trades if t["exit"] == "TP"),
        "n_sl": sum(1 for t in trades if t["exit"] == "SL"),
        "profit_factor": profit_factor,
        "still_open_at_end": layer > 0,
        "years_covered": years_covered,
        "n_bars": len(data),
        "equity_curve": eq,
    }


def run_dataset(dataset_name, tf_label, load_fn, symbols, cost_fn):
    results = []
    curves = {}
    for symbol in symbols:
        bars, spread_bps, use_bar_spread = load_fn(symbol)
        if bars is None or len(bars) < 30:
            continue
        res = run_grid_strategy(bars, spread_bps, use_bar_spread=use_bar_spread)
        if res is None:
            continue
        results.append({
            "dataset": dataset_name,
            "timeframe": tf_label,
            "symbol": symbol,
            "years_covered": round(res["years_covered"], 2),
            "n_bars": res["n_bars"],
            "net_profit_usd": round(res["net_profit_usd"], 2),
            "total_return_pct": round(res["total_return_pct"], 2),
            "annualized_return_pct": round(res["annualized_return_pct"], 2) if not np.isnan(res["annualized_return_pct"]) else None,
            "max_dd_pct": round(res["max_dd_pct"], 2),
            "sharpe": round(res["sharpe"], 2),
            "win_rate_pct": round(res["win_rate"], 1),
            "n_trades": res["n_trades"],
            "n_tp": res["n_tp"],
            "n_sl": res["n_sl"],
            "profit_factor": round(res["profit_factor"], 2) if not np.isnan(res["profit_factor"]) else None,
            "stuck_at_end": res["still_open_at_end"],
        })
        curves[symbol] = res["equity_curve"]
    return pd.DataFrame(results), curves


def summarize(df, title):
    print(f"\n=== {title} ===")
    if df.empty:
        print("(無足夠交易樣本)")
        return
    print(f"標的數: {len(df)}  平均覆蓋年數: {df['years_covered'].mean():.2f}")
    print(f"平均總報酬率: {df['total_return_pct'].mean():.2f}%  中位數: {df['total_return_pct'].median():.2f}%")
    print(f"平均年化報酬率: {df['annualized_return_pct'].mean():.2f}%")
    print(f"獲利標的數/總數: {(df['net_profit_usd'] > 0).sum()}/{len(df)}")
    print(f"平均勝率: {df['win_rate_pct'].mean():.1f}%")
    print(f"平均最大回撤: {df['max_dd_pct'].mean():.2f}%  最差: {df['max_dd_pct'].max():.2f}%")
    print(f"平均夏普: {df['sharpe'].mean():.2f}")
    print(f"總交易次數: {df['n_trades'].sum()} (TP {df['n_tp'].sum()} / SL {df['n_sl'].sum()})，平均每檔僅 {df['n_trades'].mean():.1f} 筆交易")
    print(f"回測結束仍卡倉標的數: {df['stuck_at_end'].sum()}")


def main():
    spread_map = load_spread_map()
    all_dfs = []

    # ---------- 資料來源 A：真實股票 IBKR 1年, 4H ----------
    ibkr_dir = "data_ibkr_5m"
    ibkr_symbols = sorted(f.replace("_5m.csv", "") for f in os.listdir(ibkr_dir) if f.endswith("_5m.csv"))

    def load_ibkr_4h(symbol):
        path = os.path.join(ibkr_dir, f"{symbol}_5m.csv")
        raw = pd.read_csv(path)
        raw["datetime"] = pd.to_datetime(raw["date"], utc=True).dt.tz_convert("America/New_York")
        raw = raw.rename(columns={"open": "open", "high": "high", "low": "low", "close": "close", "volume": "volume"})
        raw = raw[["datetime", "open", "high", "low", "close", "volume"]]
        raw = raw[(raw["datetime"].dt.time >= pd.Timestamp("09:30").time()) & (raw["datetime"].dt.time < pd.Timestamp("16:00").time())]
        bars = resample_session_anchored(raw, bar_minutes=240)
        spread_bps = spread_map.get(symbol, DEFAULT_SPREAD_BPS)
        return bars, spread_bps, False

    df_a, curves_a = run_dataset("IBKR真實股票(1年)", "4H", load_ibkr_4h, ibkr_symbols, None)
    all_dfs.append(df_a)
    summarize(df_a, "資料來源A：IBKR真實股票 1年, 4H週期 (session錨定)")

    # ---------- 資料來源 B：真實點差 CFD 2022起, 4H ----------
    cfd_dir = "data_mt5_equities_cfd"
    cfd_symbols = sorted(f.replace("EQ_", "").replace("_15m.csv", "") for f in os.listdir(cfd_dir) if f.endswith("_15m.csv"))

    def make_cfd_loader(freq):
        def _load(symbol):
            path = os.path.join(cfd_dir, f"EQ_{symbol}_15m.csv")
            raw = pd.read_csv(path)
            raw["datetime"] = pd.to_datetime(raw["datetime"], format="%Y.%m.%d %H:%M")
            raw = raw[raw["datetime"] >= "2022-01-01"]
            if len(raw) < 100:
                return None, None, False
            raw = raw[["datetime", "open", "high", "low", "close", "spread_bps"]]
            bars = resample_calendar(raw, freq)
            return bars, spread_map.get(symbol, DEFAULT_SPREAD_BPS), True
        return _load

    df_b, curves_b = run_dataset("MT5真實點差CFD(2022起)", "4H", make_cfd_loader("4h"), cfd_symbols, None)
    all_dfs.append(df_b)
    summarize(df_b, "資料來源B：MT5真實逐根點差 CFD, 2022-2026(約4.7年), 4H週期")

    df_c, curves_c = run_dataset("MT5真實點差CFD(2022起)", "1D", make_cfd_loader("1D"), cfd_symbols, None)
    all_dfs.append(df_c)
    summarize(df_c, "資料來源C：MT5真實逐根點差 CFD, 2022-2026(約4.7年), 日線(Daily)週期")

    combined = pd.concat(all_dfs, ignore_index=True)
    pd.set_option("display.width", 220)
    combined.to_csv("htf_grid_verify_results.csv", index=False, encoding="utf-8-sig")
    print("\n--> 完整明細已匯出至 htf_grid_verify_results.csv")

    print("\n=== 各標的報酬率排行 (資料來源B, 4H, 前10 / 後10) ===")
    if not df_b.empty:
        srt = df_b.sort_values("total_return_pct", ascending=False)
        print(srt.head(10)[["symbol", "total_return_pct", "annualized_return_pct", "win_rate_pct", "n_trades", "max_dd_pct"]].to_string(index=False))
        print("...")
        print(srt.tail(10)[["symbol", "total_return_pct", "annualized_return_pct", "win_rate_pct", "n_trades", "max_dd_pct"]].to_string(index=False))

    # 繪圖：資料來源B (最長且成本最真實) 的權益曲線
    if curves_b:
        plt.figure(figsize=(14, 8), dpi=200)
        for sym, eq in curves_b.items():
            pct = (eq / INITIAL_CAPITAL - 1.0) * 100.0
            plt.plot(pct.values, lw=1.0, alpha=0.7, label=sym)
        plt.axhline(0, color="gray", linestyle="--", alpha=0.7)
        plt.title("MT5 Real Spread CFD (2022-2026) - Adaptive Channel Grid, 4H, Return %")
        plt.xlabel("4H Bars Timeline")
        plt.ylabel("Return (%)")
        plt.legend(loc="upper left", bbox_to_anchor=(1.01, 1), fontsize=6, ncol=2)
        plt.tight_layout()
        plt.savefig("htf_grid_verify_4h_performance.png")
        print("--> 圖表已保存至 htf_grid_verify_4h_performance.png")


if __name__ == "__main__":
    main()
