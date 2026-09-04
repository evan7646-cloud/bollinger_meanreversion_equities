import os
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

# ==========================================================================================
# 目的：使用資料夾中「真實股票數據」(data_ibkr_5m，IBKR 實際成交價，非 CFD 合成商品)
# 重新驗證 strategy_logic_and_backtest_truth.md 所描述的自適應通道網格 (Adaptive Channel DCA Grid)
# 嚴格按照 md 第 3 節「核心程式碼」的狀態機邏輯實作，不加入未在 md 中出現的額外規則，
# 以確認先前 AI 回測數字是否可信。
# ==========================================================================================

DATA_DIR = "data_ibkr_5m"
SPREAD_TABLE = "mt5_spread_by_symbol.csv"  # 取自真實 MT5 broker 實測平均點差，作為估算真實買賣價差的參考

INITIAL_CAPITAL = 25000.0
BASE_ORDER_USD = 1500.0
MAX_DCA_LAYERS = 4
CHANNEL_K = 2.0          # 進場：跌破 50EMA - 2.0*ATR
DCA_STEP_ATR = 1.5       # 每跌 1.5 ATR 加碼一次
STOP_ATR = 4.0           # 停損：跌破均價 4.0 ATR
COMMISSION_BPS = 2.0     # 單邊手續費 (0.02%)，貼近 IBKR 大型股實際費率量級
DEFAULT_SPREAD_BPS = 5.0 # 找不到真實點差資料時的保守預設值


def load_spread_map():
    try:
        df = pd.read_csv(SPREAD_TABLE)
        return df.set_index("symbol")["avg_spread_bps"].to_dict()
    except Exception:
        return {}


def load_and_resample_15m(path):
    df = pd.read_csv(path)
    df["datetime"] = pd.to_datetime(df["date"], utc=True).dt.tz_convert("America/New_York")
    df = df.set_index("datetime")[["open", "high", "low", "close", "volume"]]
    agg = df.resample("15min", label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    ).dropna(subset=["open", "high", "low", "close"])
    # 只保留美股常規交易時段 09:30-16:00 的 K 棒，避免盤前/盤後低流動性雜訊
    agg = agg.between_time("09:30", "15:45")
    agg = agg.reset_index()
    return agg


def run_grid_strategy(data: pd.DataFrame, spread_bps: float):
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

    half_spread = (spread_bps / 10000.0) / 2.0
    commission_rate = COMMISSION_BPS / 10000.0

    cash = INITIAL_CAPITAL
    shares = 0.0
    layer = 0
    avg_entry_price = 0.0

    equity_curve = []
    trades = []  # 完整回合 (entry->exit) 的損益紀錄

    entry_cost_basis = 0.0  # 目前持倉的累計成本 (含手續費)，用於計算逐筆真實損益

    for _, row in data.iterrows():
        close, high, low = row["close"], row["high"], row["low"]
        atr, ema50, lower_band = row["atr"], row["ema50"], row["lower_band"]

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
                trades.append({"exit": "TP", "pnl": pnl, "layers": layer})
                shares, layer, avg_entry_price, entry_cost_basis = 0.0, 0, 0.0, 0.0
            elif low <= avg_entry_price - atr * STOP_ATR:
                exec_price = (avg_entry_price - atr * STOP_ATR) * (1.0 - half_spread)
                revenue = exec_price * shares
                comm = revenue * commission_rate
                cash += revenue - comm
                pnl = (revenue - comm) - entry_cost_basis
                trades.append({"exit": "SL", "pnl": pnl, "layers": layer})
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

    eq = pd.Series(equity_curve, index=data["datetime"] if len(equity_curve) == len(data) else None)
    final_equity = eq.iloc[-1] if len(eq) else INITIAL_CAPITAL
    total_return_pct = (final_equity - INITIAL_CAPITAL) / INITIAL_CAPITAL * 100.0

    peak = eq.cummax()
    dd = (eq - peak) / peak
    max_dd_pct = abs(dd.min()) * 100.0 if len(dd) else 0.0

    rets = eq.pct_change().dropna()
    sharpe = (rets.mean() / rets.std()) * np.sqrt(252 * 26) if rets.std() > 0 else 0.0

    n_trades = len(trades)
    wins = [t["pnl"] for t in trades if t["pnl"] > 0]
    losses = [t["pnl"] for t in trades if t["pnl"] <= 0]
    win_rate = len(wins) / n_trades * 100.0 if n_trades else 0.0
    profit_factor = (sum(wins) / abs(sum(losses))) if losses and sum(losses) != 0 else np.nan
    still_open_at_end = layer > 0
    unrealized_stuck_pnl = (shares * data["close"].iloc[-1] - entry_cost_basis) if still_open_at_end else 0.0

    return {
        "final_equity": final_equity,
        "net_profit_usd": final_equity - INITIAL_CAPITAL,
        "total_return_pct": total_return_pct,
        "max_dd_pct": max_dd_pct,
        "sharpe": sharpe,
        "win_rate": win_rate,
        "n_trades": n_trades,
        "n_tp": sum(1 for t in trades if t["exit"] == "TP"),
        "n_sl": sum(1 for t in trades if t["exit"] == "SL"),
        "avg_win": np.mean(wins) if wins else 0.0,
        "avg_loss": np.mean(losses) if losses else 0.0,
        "profit_factor": profit_factor,
        "still_open_at_end": still_open_at_end,
        "unrealized_stuck_pnl": unrealized_stuck_pnl,
        "equity_curve": eq,
        "n_bars": len(data),
    }


def main():
    spread_map = load_spread_map()
    files = sorted(f for f in os.listdir(DATA_DIR) if f.endswith("_5m.csv"))

    results = []
    curves = {}
    for f in files:
        symbol = f.replace("_5m.csv", "")
        path = os.path.join(DATA_DIR, f)
        bars15 = load_and_resample_15m(path)
        if len(bars15) < 100:
            continue
        spread_bps = spread_map.get(symbol, DEFAULT_SPREAD_BPS)
        res = run_grid_strategy(bars15, spread_bps)
        results.append({
            "symbol": symbol,
            "real_spread_bps": round(spread_bps, 2),
            "bars_15m": res["n_bars"],
            "net_profit_usd": round(res["net_profit_usd"], 2),
            "return_pct": round(res["total_return_pct"], 2),
            "max_dd_pct": round(res["max_dd_pct"], 2),
            "sharpe": round(res["sharpe"], 2),
            "win_rate_pct": round(res["win_rate"], 1),
            "n_trades": res["n_trades"],
            "n_tp": res["n_tp"],
            "n_sl": res["n_sl"],
            "avg_win_usd": round(res["avg_win"], 2),
            "avg_loss_usd": round(res["avg_loss"], 2),
            "profit_factor": round(res["profit_factor"], 2) if not np.isnan(res["profit_factor"]) else None,
            "stuck_position_at_end": res["still_open_at_end"],
            "unrealized_pnl_if_stuck": round(res["unrealized_stuck_pnl"], 2),
        })
        curves[symbol] = res["equity_curve"]

    df = pd.DataFrame(results).sort_values("return_pct", ascending=False)
    pd.set_option("display.width", 200)
    print(df.to_string(index=False))
    df.to_csv("real_stock_grid_verify_results.csv", index=False, encoding="utf-8-sig")
    print("\n--> 匯出至 real_stock_grid_verify_results.csv")

    print("\n=== 全樣本彙總 (依標的等權重, 每檔各自獨立 $25,000 本金) ===")
    print(f"標的數: {len(df)}")
    print(f"平均報酬率: {df['return_pct'].mean():.2f}%  中位數: {df['return_pct'].median():.2f}%")
    print(f"獲利標的數/總數: {(df['net_profit_usd'] > 0).sum()}/{len(df)}")
    print(f"平均勝率: {df['win_rate_pct'].mean():.1f}%")
    print(f"平均最大回撤: {df['max_dd_pct'].mean():.2f}%  最差單一標的回撤: {df['max_dd_pct'].max():.2f}%")
    print(f"平均夏普: {df['sharpe'].mean():.2f}")
    print(f"總交易次數: {df['n_trades'].sum()}  (TP {df['n_tp'].sum()} / SL {df['n_sl'].sum()})")
    print(f"回測結束時仍卡倉(未平倉)的標的數: {df['stuck_position_at_end'].sum()}")

    plt.figure(figsize=(14, 8), dpi=200)
    for sym, eq in curves.items():
        pct = (eq / INITIAL_CAPITAL - 1.0) * 100.0
        plt.plot(pct.values, lw=1.0, alpha=0.7, label=sym)
    plt.axhline(0, color="gray", linestyle="--", alpha=0.7)
    plt.title("Real Stock Data (IBKR, 1yr, 15m resampled) - Adaptive Channel Grid, Return %")
    plt.xlabel("15-Minute Bars Timeline")
    plt.ylabel("Return (%)")
    plt.legend(loc="upper left", bbox_to_anchor=(1.01, 1), fontsize=6, ncol=2)
    plt.tight_layout()
    plt.savefig("real_stock_grid_verify_performance.png")
    print("--> 圖表已保存至 real_stock_grid_verify_performance.png")


if __name__ == "__main__":
    main()
