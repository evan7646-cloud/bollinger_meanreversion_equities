import os
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

# ==========================================================================================
# 用真實 Yahoo Finance FX 報價 (1H -> 重採樣 4H)，完全比照
# strategy_logic_and_backtest_truth.md 第 3 節的狀態機邏輯測試 18 檔貨幣對。
# 不加入任何 md 沒提到的規則 (例如先前抓到的「提早止盈」bug)。
# 只做多方向 (跌破下軌超跌買進、反彈回中軌全平)，因為 md 描述的就是多頭均值回歸網格。
# ==========================================================================================

INITIAL_CAPITAL = 25000.0
BASE_ORDER_USD = 1500.0
MAX_DCA_LAYERS = 4
CHANNEL_K = 2.0
DCA_STEP_ATR = 1.5
STOP_ATR = 4.0

DATA_DIR = "data_fx_1h"
COST_TABLE = "forex_real_costs_summary.csv"

JPY_PAIRS = {"USDJPY", "EURJPY", "GBPJPY", "CADJPY", "CHFJPY"}


def load_cost_table():
    df = pd.read_csv(COST_TABLE)
    df = df.set_index("品種")
    return df


def get_pair_costs(cost_df, pair):
    row = cost_df.loc[pair]
    pip_size = 0.01 if pair in JPY_PAIRS else 0.0001
    spread_pips = float(row["典型點差(Pips)"])
    commission_rt_usd_per_lot = float(row["單手來回佣金($)"])
    swap_long_usd_per_lot_per_day = float(row["做多Swap($/手/日)"])
    return {
        "pip_size": pip_size,
        "spread_pips": spread_pips,
        "commission_rate": (commission_rt_usd_per_lot / 100000.0) / 2.0,  # 單邊佣金率 (占名目本金比例)
        "swap_rate_per_day": swap_long_usd_per_lot_per_day / 100000.0,     # 每日多單利息 (占名目本金比例，以標準手$100,000估算)
    }


def resample_4h_utc(df):
    d = df.copy()
    d["datetime"] = pd.to_datetime(d["datetime"], utc=True)
    d = d.set_index("datetime").sort_index()
    out = d.resample("4h", label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last"}
    ).dropna().reset_index()
    return out


def run_grid_strategy_fx(data: pd.DataFrame, pip_size, spread_pips, commission_rate, swap_rate_per_day):
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
    data["date"] = data["datetime"].dt.date
    data = data.dropna(subset=["atr", "ema50", "lower_band"]).reset_index(drop=True)
    if len(data) < 30:
        return None

    cash = INITIAL_CAPITAL
    units = 0.0
    layer = 0
    avg_entry_price = 0.0
    entry_cost_basis = 0.0
    last_date = None
    total_swap_cost = 0.0

    equity_curve = []
    trades = []

    for _, row in data.iterrows():
        close, high, low = row["close"], row["high"], row["low"]
        atr, ema50, lower_band = row["atr"], row["ema50"], row["lower_band"]
        cur_date = row["date"]

        # 跨日累計 Swap 利息 (只有持有多單部位時)，週三計 3 倍 (模擬週末利息)
        if last_date is not None and cur_date != last_date and units > 0:
            mult = 3.0 if cur_date.weekday() == 2 else 1.0  # 2 = Wednesday
            notional = units * close
            day_swap = notional * swap_rate_per_day * mult
            cash += day_swap  # swap_rate 可正可負，正值為收息、負值為扣息
            total_swap_cost -= day_swap
        last_date = cur_date

        half_spread = (spread_pips * pip_size / close) / 2.0

        if layer == 0:
            if low <= lower_band:
                exec_price = min(close, lower_band) * (1.0 + half_spread)
                buy_qty = BASE_ORDER_USD / exec_price
                comm = exec_price * buy_qty * commission_rate
                cost = exec_price * buy_qty + comm
                if cash >= cost and buy_qty > 0:
                    cash -= cost
                    units = buy_qty
                    avg_entry_price = exec_price
                    entry_cost_basis = cost
                    layer = 1
        else:
            if high >= ema50:
                exec_price = ema50 * (1.0 - half_spread)
                revenue = exec_price * units
                comm = revenue * commission_rate
                cash += revenue - comm
                pnl = (revenue - comm) - entry_cost_basis
                trades.append({"exit": "TP", "pnl": pnl})
                units, layer, avg_entry_price, entry_cost_basis = 0.0, 0, 0.0, 0.0
            elif low <= avg_entry_price - atr * STOP_ATR:
                exec_price = (avg_entry_price - atr * STOP_ATR) * (1.0 - half_spread)
                revenue = exec_price * units
                comm = revenue * commission_rate
                cash += revenue - comm
                pnl = (revenue - comm) - entry_cost_basis
                trades.append({"exit": "SL", "pnl": pnl})
                units, layer, avg_entry_price, entry_cost_basis = 0.0, 0, 0.0, 0.0
            elif layer < MAX_DCA_LAYERS:
                next_trigger = avg_entry_price - (layer * atr * DCA_STEP_ATR)
                if low <= next_trigger:
                    exec_price = next_trigger * (1.0 + half_spread)
                    buy_qty = (BASE_ORDER_USD * 1.2) / exec_price
                    comm = exec_price * buy_qty * commission_rate
                    cost = exec_price * buy_qty + comm
                    if cash >= cost and buy_qty > 0:
                        cash -= cost
                        total_cost_val = avg_entry_price * units + exec_price * buy_qty
                        units += buy_qty
                        avg_entry_price = total_cost_val / units
                        entry_cost_basis += cost
                        layer += 1

        equity_curve.append(cash + units * close)

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

    return {
        "net_profit_usd": final_equity - INITIAL_CAPITAL,
        "total_return_pct": total_return_pct,
        "annualized_return_pct": ((1 + total_return_pct / 100.0) ** (1 / years) - 1) * 100.0,
        "max_dd_pct": max_dd_pct,
        "sharpe": sharpe,
        "win_rate": win_rate,
        "n_trades": n_trades,
        "n_tp": sum(1 for t in trades if t["exit"] == "TP"),
        "n_sl": sum(1 for t in trades if t["exit"] == "SL"),
        "profit_factor": profit_factor,
        "still_open_at_end": layer > 0,
        "swap_cost_usd": total_swap_cost,
        "years_covered": years,
        "n_bars": len(data),
        "equity_curve": eq,
    }


def main():
    cost_df = load_cost_table()
    pairs = [f.replace("_1h.csv", "") for f in sorted(os.listdir(DATA_DIR)) if f.endswith("_1h.csv")]

    results = []
    curves = {}
    for pair in pairs:
        raw = pd.read_csv(os.path.join(DATA_DIR, f"{pair}_1h.csv"))
        bars = resample_4h_utc(raw)
        costs = get_pair_costs(cost_df, pair)
        res = run_grid_strategy_fx(bars, costs["pip_size"], costs["spread_pips"], costs["commission_rate"], costs["swap_rate_per_day"])
        if res is None:
            continue
        results.append({
            "貨幣對": pair,
            "點差(pips)": costs["spread_pips"],
            "覆蓋年數": round(res["years_covered"], 2),
            "K棒數(4H)": res["n_bars"],
            "淨利潤($)": round(res["net_profit_usd"], 2),
            "總報酬率(%)": round(res["total_return_pct"], 2),
            "年化報酬率(%)": round(res["annualized_return_pct"], 2),
            "最大回撤(%)": round(res["max_dd_pct"], 2),
            "夏普值": round(res["sharpe"], 2),
            "勝率(%)": round(res["win_rate"], 1),
            "交易次數": res["n_trades"],
            "止盈次數": res["n_tp"],
            "停損次數": res["n_sl"],
            "獲利因子": round(res["profit_factor"], 2) if not np.isnan(res["profit_factor"]) else None,
            "隔夜利息淨額($)": round(res["swap_cost_usd"], 2),
            "結束時仍卡倉": res["still_open_at_end"],
        })
        curves[pair] = res["equity_curve"]

    df = pd.DataFrame(results).sort_values("總報酬率(%)", ascending=False)
    pd.set_option("display.width", 220)
    print(df.to_string(index=False))
    df.to_csv("fx_grid_faithful_4h_results.csv", index=False, encoding="utf-8-sig")
    print("\n--> 匯出至 fx_grid_faithful_4h_results.csv")

    print("\n=== 18 檔貨幣對彙總 (4H, 各自獨立 $25,000 本金) ===")
    print(f"標的數: {len(df)}  平均覆蓋年數: {df['覆蓋年數'].mean():.2f}")
    print(f"平均總報酬率: {df['總報酬率(%)'].mean():.2f}%  中位數: {df['總報酬率(%)'].median():.2f}%")
    print(f"平均年化報酬率: {df['年化報酬率(%)'].mean():.2f}%")
    print(f"獲利標的數/總數: {(df['淨利潤($)'] > 0).sum()}/{len(df)}")
    print(f"平均勝率: {df['勝率(%)'].mean():.1f}%")
    print(f"平均最大回撤: {df['最大回撤(%)'].mean():.2f}%  最差: {df['最大回撤(%)'].max():.2f}%")
    print(f"平均夏普: {df['夏普值'].mean():.2f}")
    print(f"總交易次數: {df['交易次數'].sum()} (止盈 {df['止盈次數'].sum()} / 停損 {df['停損次數'].sum()})")
    print(f"回測結束仍卡倉標的數: {df['結束時仍卡倉'].sum()}")

    plt.figure(figsize=(14, 8), dpi=200)
    for pair, eq in curves.items():
        pct = (eq / INITIAL_CAPITAL - 1.0) * 100.0
        plt.plot(pct.values, lw=1.2, alpha=0.8, label=pair)
    plt.axhline(0, color="gray", linestyle="--", alpha=0.7)
    plt.title("FX Real Data (Yahoo Finance, 1H->4H) - Faithful Adaptive Channel Grid, Return %")
    plt.xlabel("4H Bars Timeline")
    plt.ylabel("Return (%)")
    plt.legend(loc="upper left", bbox_to_anchor=(1.01, 1), fontsize=8, ncol=2)
    plt.tight_layout()
    plt.savefig("fx_grid_faithful_4h_performance.png")
    print("--> 圖表已保存至 fx_grid_faithful_4h_performance.png")


if __name__ == "__main__":
    main()
