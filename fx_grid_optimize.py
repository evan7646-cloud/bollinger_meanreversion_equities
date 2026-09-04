"""
外匯網格策略：標的篩選 + 優化方向驗證

三個部分：
  1) 量化每檔標的的「均值回歸傾向」(Variance Ratio)，找出可事前判斷的選品規則
  2) 結構性優化方向測試 (雙向交易 / 趨勢過濾 / 提早止盈 / 時間停損 / 盤中撮合假設)
  3) 參數掃描 + 樣本內(前60%) / 樣本外(後40%) 切分，檢查優化是不是過度擬合

所有變體都保留 md 原始的硬停損與完整成本 (點差 + 佣金 + 隔夜利息)，
不重複先前在別的腳本裡抓到的「拿掉停損」「空單不扣成本」等錯誤。
"""

import os
import numpy as np
import pandas as pd

DATA_DIR = "data_fx_1h"
COST_TABLE = "forex_real_costs_summary.csv"
JPY_PAIRS = {"USDJPY", "EURJPY", "GBPJPY", "CADJPY", "CHFJPY"}

INITIAL_CAPITAL = 25000.0

BASE_CFG = dict(
    base_order=1500.0,      # 首單金額
    size_mult=1.2,          # 每層加碼倍數
    max_layers=4,           # 最大加倉層數
    channel_k=2.0,          # 進場通道 ATR 倍數
    dca_step=1.5,           # 加倉間距 ATR 倍數
    stop_atr=4.0,           # 硬停損 ATR 倍數
    tp_mode="ema50",        # ema50 = md 原版(回到中軌全平) / nearer = 較近目標(仍保留停損)
    tp_atr=1.0,             # tp_mode='nearer' 時的較近目標倍數
    direction="long",       # long = 只做多 / both = 雙向
    trend_filter=False,     # True = 只在 ema200 順勢方向進場
    time_exit_bars=0,       # >0 時，持倉超過 N 根 K 棒強制平倉
    intrabar="optimistic",  # optimistic = 同根 K 棒先判止盈 / pessimistic = 先判停損
)


# ---------------------------------------------------------------- 資料處理
def load_costs():
    df = pd.read_csv(COST_TABLE).set_index("品種")
    return df


def pair_costs(cost_df, pair):
    if pair in cost_df.index:
        row = cost_df.loc[pair]
        spread_pips = float(row["典型點差(Pips)"])
        comm_rt = float(row["單手來回佣金($)"])
        swap_l = float(row["做多Swap($/手/日)"])
        swap_s = float(row["做空Swap($/手/日)"])
    else:  # 資料表沒有的標的用保守預設
        spread_pips, comm_rt, swap_l, swap_s = 2.0, 6.0, -3.0, -3.0
    pip_size = 0.01 if pair in JPY_PAIRS else (0.1 if pair == "XAUUSD" else 0.0001)
    return dict(
        pip_size=pip_size,
        spread_pips=spread_pips,
        comm_rate=(comm_rt / 100000.0) / 2.0,
        swap_long=swap_l / 100000.0,
        swap_short=swap_s / 100000.0,
    )


def load_4h(pair):
    df = pd.read_csv(os.path.join(DATA_DIR, f"{pair}_1h.csv"))
    df["datetime"] = pd.to_datetime(df["datetime"], utc=True)
    df = df.set_index("datetime").sort_index()
    out = (df.resample("4h", label="left", closed="left")
             .agg({"open": "first", "high": "max", "low": "min", "close": "last"})
             .dropna().reset_index())
    return out


def add_indicators(bars):
    d = bars.copy()
    prev_close = d["close"].shift(1)
    d["tr"] = np.maximum(d["high"] - d["low"],
                         np.maximum((d["high"] - prev_close).abs(), (d["low"] - prev_close).abs()))
    d["atr"] = d["tr"].rolling(20).mean()
    d["ema50"] = d["close"].ewm(span=50, adjust=False).mean()
    d["ema200"] = d["close"].ewm(span=200, adjust=False).mean()
    return d.dropna(subset=["atr", "ema50", "ema200"]).reset_index(drop=True)


# ---------------------------------------------------------------- 回測引擎
def run_engine(bars, cfg, costs, initial_capital=INITIAL_CAPITAL):
    """單一標的回測。回傳績效字典。多空對稱、完整成本、保留硬停損。"""
    n = len(bars)
    if n < 60:
        return None

    high = bars["high"].to_numpy(float)
    low = bars["low"].to_numpy(float)
    close = bars["close"].to_numpy(float)
    atr_a = bars["atr"].to_numpy(float)
    ema50_a = bars["ema50"].to_numpy(float)
    ema200_a = bars["ema200"].to_numpy(float)
    dates = bars["datetime"].dt.date.to_numpy()
    weekdays = bars["datetime"].dt.weekday.to_numpy()

    half_spread_price = costs["spread_pips"] * costs["pip_size"] / 2.0
    comm_rate = costs["comm_rate"]

    cash = initial_capital
    side = 0            # 0 空手 / +1 多 / -1 空
    units = 0.0
    layer = 0
    avg_entry = 0.0
    basis = 0.0         # 多單=已付出總成本；空單=已收到淨收入
    entry_i = -1

    equity = np.empty(n)
    trades = []
    hold_bars = []
    bars_in_market = 0
    last_date = None

    for i in range(n):
        c, h, l = close[i], high[i], low[i]
        atr, ema50, ema200 = atr_a[i], ema50_a[i], ema200_a[i]
        hs = half_spread_price / c if c > 0 else 0.0   # 半點差 (比例)

        # --- 跨日隔夜利息 ---
        if last_date is not None and dates[i] != last_date and side != 0:
            mult = 3.0 if weekdays[i] == 2 else 1.0   # 週三三倍
            rate = costs["swap_long"] if side > 0 else costs["swap_short"]
            cash += units * c * rate * mult
        last_date = dates[i]

        lower_band = ema50 - cfg["channel_k"] * atr
        upper_band = ema50 + cfg["channel_k"] * atr

        if side == 0:
            # ---- 進場 ----
            if l <= lower_band and (not cfg["trend_filter"] or c > ema200):
                px = min(c, lower_band) * (1.0 + hs)
                qty = cfg["base_order"] / px
                cost = px * qty * (1.0 + comm_rate)
                if cash >= cost:
                    cash -= cost
                    side, units, layer, avg_entry, basis, entry_i = 1, qty, 1, px, cost, i
            elif (cfg["direction"] == "both" and h >= upper_band
                  and (not cfg["trend_filter"] or c < ema200)):
                px = max(c, upper_band) * (1.0 - hs)
                qty = cfg["base_order"] / px
                proceeds = px * qty * (1.0 - comm_rate)
                if cash >= px * qty:          # 保證金約束：名目不超過現金
                    cash += proceeds
                    side, units, layer, avg_entry, basis, entry_i = -1, qty, 1, px, proceeds, i
        else:
            bars_in_market += 1
            closed = False

            if side > 0:
                tp_px = ema50 if cfg["tp_mode"] == "ema50" else min(ema50, avg_entry + cfg["tp_atr"] * atr)
                sl_px = avg_entry - cfg["stop_atr"] * atr
                hit_tp, hit_sl = h >= tp_px, l <= sl_px
                order = ["tp", "sl"] if cfg["intrabar"] == "optimistic" else ["sl", "tp"]
                for kind in order:
                    if kind == "tp" and hit_tp:
                        px = tp_px * (1.0 - hs)
                    elif kind == "sl" and hit_sl:
                        px = sl_px * (1.0 - hs)
                    else:
                        continue
                    rev = px * units * (1.0 - comm_rate)
                    cash += rev
                    trades.append((kind.upper(), rev - basis))
                    hold_bars.append(i - entry_i)
                    closed = True
                    break
            else:
                tp_px = ema50 if cfg["tp_mode"] == "ema50" else max(ema50, avg_entry - cfg["tp_atr"] * atr)
                sl_px = avg_entry + cfg["stop_atr"] * atr
                hit_tp, hit_sl = l <= tp_px, h >= sl_px
                order = ["tp", "sl"] if cfg["intrabar"] == "optimistic" else ["sl", "tp"]
                for kind in order:
                    if kind == "tp" and hit_tp:
                        px = tp_px * (1.0 + hs)
                    elif kind == "sl" and hit_sl:
                        px = sl_px * (1.0 + hs)
                    else:
                        continue
                    cost = px * units * (1.0 + comm_rate)
                    cash -= cost
                    trades.append((kind.upper(), basis - cost))
                    hold_bars.append(i - entry_i)
                    closed = True
                    break

            # ---- 時間停損 ----
            if not closed and cfg["time_exit_bars"] and (i - entry_i) >= cfg["time_exit_bars"]:
                if side > 0:
                    px = c * (1.0 - hs)
                    rev = px * units * (1.0 - comm_rate)
                    cash += rev
                    trades.append(("TIME", rev - basis))
                else:
                    px = c * (1.0 + hs)
                    cost = px * units * (1.0 + comm_rate)
                    cash -= cost
                    trades.append(("TIME", basis - cost))
                hold_bars.append(i - entry_i)
                closed = True

            # ---- 加倉 ----
            if not closed and layer < cfg["max_layers"]:
                step = layer * cfg["dca_step"] * atr
                if side > 0 and l <= avg_entry - step:
                    px = (avg_entry - step) * (1.0 + hs)
                    qty = cfg["base_order"] * cfg["size_mult"] / px
                    cost = px * qty * (1.0 + comm_rate)
                    if cash >= cost:
                        cash -= cost
                        avg_entry = (avg_entry * units + px * qty) / (units + qty)
                        units += qty
                        basis += cost
                        layer += 1
                elif side < 0 and h >= avg_entry + step:
                    px = (avg_entry + step) * (1.0 - hs)
                    qty = cfg["base_order"] * cfg["size_mult"] / px
                    if cash >= px * qty:
                        proceeds = px * qty * (1.0 - comm_rate)
                        cash += proceeds
                        avg_entry = (avg_entry * units + px * qty) / (units + qty)
                        units += qty
                        basis += proceeds
                        layer += 1

            if closed:
                side, units, layer, avg_entry, basis, entry_i = 0, 0.0, 0, 0.0, 0.0, -1

        equity[i] = cash + units * c if side > 0 else (cash - units * c if side < 0 else cash)

    eq = pd.Series(equity)
    total_ret = (eq.iloc[-1] - initial_capital) / initial_capital * 100.0
    peak = eq.cummax()
    mdd = abs(((eq - peak) / peak).min()) * 100.0

    days = (bars["datetime"].iloc[-1] - bars["datetime"].iloc[0]).total_seconds() / 86400.0
    years = max(days / 365.25, 1e-6)
    rets = eq.pct_change().dropna()
    sharpe = rets.mean() / rets.std() * np.sqrt(n / years) if rets.std() > 0 else 0.0

    pnls = [p for _, p in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]

    return dict(
        total_return_pct=total_ret,
        ann_return_pct=((1 + total_ret / 100.0) ** (1 / years) - 1) * 100.0,
        max_dd_pct=mdd,
        sharpe=sharpe,
        calmar=(((1 + total_ret / 100.0) ** (1 / years) - 1) * 100.0 / mdd) if mdd > 0.01 else np.nan,
        win_rate=len(wins) / len(pnls) * 100.0 if pnls else 0.0,
        n_trades=len(pnls),
        n_tp=sum(1 for k, _ in trades if k == "TP"),
        n_sl=sum(1 for k, _ in trades if k == "SL"),
        n_time=sum(1 for k, _ in trades if k == "TIME"),
        avg_win=np.mean(wins) if wins else 0.0,
        avg_loss=np.mean(losses) if losses else 0.0,
        profit_factor=(sum(wins) / abs(sum(losses))) if losses and sum(losses) != 0 else np.nan,
        avg_hold_bars=np.mean(hold_bars) if hold_bars else 0.0,
        exposure_pct=bars_in_market / n * 100.0,
        stuck_at_end=side != 0,
        years=years,
        equity=eq,
    )


# ---------------------------------------------------------------- 標的特徵
def variance_ratio(closes, k):
    """VR<1 = 均值回歸傾向；VR>1 = 趨勢延續傾向。"""
    lr = np.diff(np.log(closes))
    if len(lr) < k * 10:
        return np.nan
    var1 = np.var(lr, ddof=1)
    agg = np.add.reduceat(lr, np.arange(0, len(lr) - len(lr) % k, k))
    vark = np.var(agg, ddof=1)
    return vark / (k * var1) if var1 > 0 else np.nan


def main():
    cost_df = load_costs()
    pairs = sorted(f.replace("_1h.csv", "") for f in os.listdir(DATA_DIR) if f.endswith("_1h.csv"))

    data = {}
    for p in pairs:
        b = add_indicators(load_4h(p))
        if len(b) >= 200:
            data[p] = b

    # ============ 1. 標的特徵 + 基準績效 ============
    rows = []
    for p, bars in data.items():
        costs = pair_costs(cost_df, p)
        base = run_engine(bars, BASE_CFG, costs)
        closes = bars["close"].to_numpy(float)
        rows.append(dict(
            pair=p,
            VR6=variance_ratio(closes, 6),      # 約 1 天
            VR30=variance_ratio(closes, 30),    # 約 1 週
            ann_vol_pct=np.std(np.diff(np.log(closes))) * np.sqrt(len(closes) / base["years"]) * 100,
            swap_long_per_lot=float(cost_df.loc[p, "做多Swap($/手/日)"]) if p in cost_df.index else np.nan,
            spread_pips=costs["spread_pips"],
            base_ann_return_pct=base["ann_return_pct"],
            base_mdd_pct=base["max_dd_pct"],
            base_sharpe=base["sharpe"],
            base_win_rate=base["win_rate"],
            base_trades=base["n_trades"],
            base_avg_hold_bars=base["avg_hold_bars"],
            base_exposure_pct=base["exposure_pct"],
        ))
    feat = pd.DataFrame(rows).sort_values("base_ann_return_pct", ascending=False)
    feat.to_csv("fx_pair_characteristics.csv", index=False, encoding="utf-8-sig")

    pd.set_option("display.width", 250)
    print("=" * 110)
    print("【1】各標的均值回歸特徵 vs 基準策略績效 (4H, md 原版邏輯, 只做多)")
    print("=" * 110)
    print(feat.round(3).to_string(index=False))

    fx_only = feat[feat["pair"] != "XAUUSD"]
    for col in ["VR6", "VR30", "ann_vol_pct", "spread_pips"]:
        c = fx_only[col].corr(fx_only["base_ann_return_pct"])
        print(f"  相關性 {col:12s} vs 年化報酬 = {c:+.3f}")

    # ============ 2. 結構性優化方向 ============
    print("\n" + "=" * 110)
    print("【2】結構性優化方向測試 (全部保留硬停損與完整成本)")
    print("=" * 110)

    variants = {
        "A 基準 (md原版, 只做多)": {},
        "B 雙向交易 (加做空)": dict(direction="both"),
        "C 趨勢過濾 (EMA200順勢)": dict(trend_filter=True),
        "D 雙向 + 趨勢過濾": dict(direction="both", trend_filter=True),
        "E 較近止盈 (保留停損)": dict(tp_mode="nearer"),
        "F 雙向 + 較近止盈": dict(direction="both", tp_mode="nearer"),
        "G 雙向 + 時間停損(120根)": dict(direction="both", time_exit_bars=120),
        "H 悲觀撮合 (同根先停損)": dict(intrabar="pessimistic"),
        "I 雙向+悲觀撮合": dict(direction="both", intrabar="pessimistic"),
    }

    var_rows = []
    for name, override in variants.items():
        cfg = {**BASE_CFG, **override}
        res = [run_engine(data[p], cfg, pair_costs(cost_df, p)) for p in data if p != "XAUUSD"]
        res = [r for r in res if r]
        var_rows.append(dict(
            variant=name,
            平均年化報酬=np.mean([r["ann_return_pct"] for r in res]),
            中位年化報酬=np.median([r["ann_return_pct"] for r in res]),
            獲利標的比=f"{sum(1 for r in res if r['total_return_pct'] > 0)}/{len(res)}",
            平均勝率=np.mean([r["win_rate"] for r in res]),
            平均MDD=np.mean([r["max_dd_pct"] for r in res]),
            最差MDD=max(r["max_dd_pct"] for r in res),
            平均夏普=np.mean([r["sharpe"] for r in res]),
            平均交易數=np.mean([r["n_trades"] for r in res]),
            平均持倉根數=np.mean([r["avg_hold_bars"] for r in res]),
            資金在場比=np.mean([r["exposure_pct"] for r in res]),
        ))
    var_df = pd.DataFrame(var_rows)
    print(var_df.round(2).to_string(index=False))
    var_df.to_csv("fx_optimize_variants.csv", index=False, encoding="utf-8-sig")

    # ============ 3. 參數掃描 + 樣本內外切分 ============
    print("\n" + "=" * 110)
    print("【3】參數掃描：樣本內(前60%) 訓練 vs 樣本外(後40%) 驗證")
    print("=" * 110)

    fx_pairs = [p for p in data if p != "XAUUSD"]
    splits = {}
    for p in fx_pairs:
        b = data[p]
        cut = int(len(b) * 0.6)
        splits[p] = (b.iloc[:cut].reset_index(drop=True), b.iloc[cut:].reset_index(drop=True))

    sweep = []
    for direction in ["long", "both"]:
        for channel_k in [1.5, 2.0, 2.5, 3.0]:
            for stop_atr in [3.0, 4.0, 6.0]:
                for dca_step in [1.0, 1.5, 2.0]:
                    cfg = {**BASE_CFG, "direction": direction, "channel_k": channel_k,
                           "stop_atr": stop_atr, "dca_step": dca_step}
                    is_r, oos_r = [], []
                    for p in fx_pairs:
                        costs = pair_costs(cost_df, p)
                        a = run_engine(splits[p][0], cfg, costs)
                        b_ = run_engine(splits[p][1], cfg, costs)
                        if a: is_r.append(a)
                        if b_: oos_r.append(b_)
                    if not is_r or not oos_r:
                        continue
                    sweep.append(dict(
                        direction=direction, channel_k=channel_k, stop_atr=stop_atr, dca_step=dca_step,
                        IS_年化=np.mean([r["ann_return_pct"] for r in is_r]),
                        OOS_年化=np.mean([r["ann_return_pct"] for r in oos_r]),
                        OOS_獲利比=sum(1 for r in oos_r if r["total_return_pct"] > 0) / len(oos_r) * 100,
                        OOS_MDD=np.mean([r["max_dd_pct"] for r in oos_r]),
                        OOS_夏普=np.mean([r["sharpe"] for r in oos_r]),
                        OOS_交易數=np.mean([r["n_trades"] for r in oos_r]),
                    ))
    sw = pd.DataFrame(sweep)
    sw.to_csv("fx_optimize_sweep.csv", index=False, encoding="utf-8-sig")

    print("\n-- 樣本內最佳 10 組，看它們在樣本外的表現 --")
    print(sw.sort_values("IS_年化", ascending=False).head(10).round(2).to_string(index=False))
    print("\n-- 樣本外最佳 10 組 --")
    print(sw.sort_values("OOS_年化", ascending=False).head(10).round(2).to_string(index=False))
    print(f"\n  樣本內 vs 樣本外 年化報酬相關性 = {sw['IS_年化'].corr(sw['OOS_年化']):+.3f}")
    print(f"  (相關性低 → 參數優化多半是在擬合雜訊，不是真的找到穩定邊際)")

    print("\n" + "=" * 110)
    print("【4】資金運用率的影響 (雙向 + 基準參數，放大首單金額)")
    print("=" * 110)
    cap_rows = []
    for base_order in [1500.0, 3000.0, 4500.0, 6000.0]:
        cfg = {**BASE_CFG, "direction": "both", "base_order": base_order}
        res = [run_engine(data[p], cfg, pair_costs(cost_df, p)) for p in fx_pairs]
        res = [r for r in res if r]
        ladder = base_order * (1 + 1.2 * 3)
        cap_rows.append(dict(
            首單金額=base_order,
            滿倉動用資金=ladder,
            佔本金比=f"{ladder / INITIAL_CAPITAL * 100:.0f}%",
            平均年化報酬=np.mean([r["ann_return_pct"] for r in res]),
            平均MDD=np.mean([r["max_dd_pct"] for r in res]),
            最差MDD=max(r["max_dd_pct"] for r in res),
            平均夏普=np.mean([r["sharpe"] for r in res]),
        ))
    print(pd.DataFrame(cap_rows).round(2).to_string(index=False))

    print("\n--> 已輸出 fx_pair_characteristics.csv / fx_optimize_variants.csv / fx_optimize_sweep.csv")


if __name__ == "__main__":
    main()
