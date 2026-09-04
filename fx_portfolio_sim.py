"""
外匯網格：篩選指標驗證 + 單一帳戶多貨幣對組合模擬

補三件事：
  1) ATR/點差比 (訊號成本比) 是不是比「均值回歸傾向」更好的選品指標
  2) 較短的時間停損 (前一輪 120 根從未觸發，等於沒測到)
  3) 單一 $25,000 帳戶「同時」跑多檔貨幣對 —— 單跑一檔時七成資金閒置，
     多檔共用資金池才是這個策略真正的效率槓桿
"""

import os
import numpy as np
import pandas as pd

from fx_grid_optimize import (
    load_costs, pair_costs, load_4h, add_indicators, run_engine,
    variance_ratio, BASE_CFG, DATA_DIR, INITIAL_CAPITAL,
)


# ---------------------------------------------------------------- 組合回測
def run_portfolio(data, pairs, cost_df, cfg, initial_capital=INITIAL_CAPITAL):
    """單一帳戶、共用資金池，同時交易多檔標的。"""
    idx = sorted(set().union(*[set(data[p]["datetime"]) for p in pairs]))
    idx_pos = {t: i for i, t in enumerate(idx)}

    # 每檔標的預先展開成陣列，並記錄其在全域時間軸上的位置
    books = {}
    for p in pairs:
        b = data[p]
        books[p] = dict(
            rows=b["datetime"].map(idx_pos).to_numpy(),
            high=b["high"].to_numpy(float), low=b["low"].to_numpy(float),
            close=b["close"].to_numpy(float), atr=b["atr"].to_numpy(float),
            ema50=b["ema50"].to_numpy(float), ema200=b["ema200"].to_numpy(float),
            date=b["datetime"].dt.date.to_numpy(), wd=b["datetime"].dt.weekday.to_numpy(),
            costs=pair_costs(cost_df, p),
            side=0, units=0.0, layer=0, avg=0.0, basis=0.0, last_date=None,
        )

    # 建立 (全域bar -> [(pair, 該檔第幾根)]) 的排程表
    schedule = [[] for _ in idx]
    for p in pairs:
        for j, gi in enumerate(books[p]["rows"]):
            schedule[gi].append((p, j))

    cash = initial_capital
    equity = np.empty(len(idx))
    trades = []
    deployed_hist = np.empty(len(idx))

    for gi in range(len(idx)):
        for p, j in schedule[gi]:
            st = books[p]
            c, h, l = st["close"][j], st["high"][j], st["low"][j]
            atr, ema50, ema200 = st["atr"][j], st["ema50"][j], st["ema200"][j]
            costs = st["costs"]
            hs = (costs["spread_pips"] * costs["pip_size"] / 2.0) / c if c > 0 else 0.0
            comm = costs["comm_rate"]

            if st["last_date"] is not None and st["date"][j] != st["last_date"] and st["side"] != 0:
                mult = 3.0 if st["wd"][j] == 2 else 1.0
                rate = costs["swap_long"] if st["side"] > 0 else costs["swap_short"]
                cash += st["units"] * c * rate * mult
            st["last_date"] = st["date"][j]

            lower, upper = ema50 - cfg["channel_k"] * atr, ema50 + cfg["channel_k"] * atr

            if st["side"] == 0:
                if l <= lower and (not cfg["trend_filter"] or c > ema200):
                    px = min(c, lower) * (1.0 + hs)
                    qty = cfg["base_order"] / px
                    cost = px * qty * (1.0 + comm)
                    if cash >= cost:
                        cash -= cost
                        st.update(side=1, units=qty, layer=1, avg=px, basis=cost)
                elif cfg["direction"] == "both" and h >= upper and (not cfg["trend_filter"] or c < ema200):
                    px = max(c, upper) * (1.0 - hs)
                    qty = cfg["base_order"] / px
                    if cash >= px * qty:
                        proceeds = px * qty * (1.0 - comm)
                        cash += proceeds
                        st.update(side=-1, units=qty, layer=1, avg=px, basis=proceeds)
            else:
                closed = False
                if st["side"] > 0:
                    tp = ema50 if cfg["tp_mode"] == "ema50" else min(ema50, st["avg"] + cfg["tp_atr"] * atr)
                    sl = st["avg"] - cfg["stop_atr"] * atr
                    if h >= tp:
                        rev = tp * (1 - hs) * st["units"] * (1 - comm)
                        cash += rev; trades.append(("TP", rev - st["basis"], p)); closed = True
                    elif l <= sl:
                        rev = sl * (1 - hs) * st["units"] * (1 - comm)
                        cash += rev; trades.append(("SL", rev - st["basis"], p)); closed = True
                else:
                    tp = ema50 if cfg["tp_mode"] == "ema50" else max(ema50, st["avg"] - cfg["tp_atr"] * atr)
                    sl = st["avg"] + cfg["stop_atr"] * atr
                    if l <= tp:
                        cost = tp * (1 + hs) * st["units"] * (1 + comm)
                        cash -= cost; trades.append(("TP", st["basis"] - cost, p)); closed = True
                    elif h >= sl:
                        cost = sl * (1 + hs) * st["units"] * (1 + comm)
                        cash -= cost; trades.append(("SL", st["basis"] - cost, p)); closed = True

                if not closed and st["layer"] < cfg["max_layers"]:
                    step = st["layer"] * cfg["dca_step"] * atr
                    if st["side"] > 0 and l <= st["avg"] - step:
                        px = (st["avg"] - step) * (1.0 + hs)
                        qty = cfg["base_order"] * cfg["size_mult"] / px
                        cost = px * qty * (1.0 + comm)
                        if cash >= cost:
                            cash -= cost
                            st["avg"] = (st["avg"] * st["units"] + px * qty) / (st["units"] + qty)
                            st["units"] += qty; st["basis"] += cost; st["layer"] += 1
                    elif st["side"] < 0 and h >= st["avg"] + step:
                        px = (st["avg"] + step) * (1.0 - hs)
                        qty = cfg["base_order"] * cfg["size_mult"] / px
                        if cash >= px * qty:
                            proceeds = px * qty * (1.0 - comm)
                            cash += proceeds
                            st["avg"] = (st["avg"] * st["units"] + px * qty) / (st["units"] + qty)
                            st["units"] += qty; st["basis"] += proceeds; st["layer"] += 1

                if closed:
                    st.update(side=0, units=0.0, layer=0, avg=0.0, basis=0.0)

        mtm, deployed = 0.0, 0.0
        for p in pairs:
            st = books[p]
            if st["side"] != 0:
                gi_rows = st["rows"]
                jj = np.searchsorted(gi_rows, gi, side="right") - 1
                if jj >= 0:
                    px = st["close"][jj]
                    mtm += st["units"] * px * st["side"]
                    deployed += st["units"] * px
        equity[gi] = cash + mtm
        deployed_hist[gi] = deployed

    eq = pd.Series(equity)
    total_ret = (eq.iloc[-1] - initial_capital) / initial_capital * 100.0
    peak = eq.cummax()
    mdd = abs(((eq - peak) / peak).min()) * 100.0
    years = (idx[-1] - idx[0]).total_seconds() / 86400.0 / 365.25
    rets = eq.pct_change().dropna()
    sharpe = rets.mean() / rets.std() * np.sqrt(len(eq) / years) if rets.std() > 0 else 0.0
    pnls = [t[1] for t in trades]
    wins = [p_ for p_ in pnls if p_ > 0]

    return dict(
        pairs=len(pairs), total_return_pct=total_ret,
        ann_return_pct=((1 + total_ret / 100.0) ** (1 / years) - 1) * 100.0,
        max_dd_pct=mdd, sharpe=sharpe, calmar=((1 + total_ret / 100.0) ** (1 / years) - 1) * 100.0 / mdd if mdd > 0.01 else np.nan,
        win_rate=len(wins) / len(pnls) * 100.0 if pnls else 0.0,
        n_trades=len(pnls),
        avg_deployed_pct=deployed_hist.mean() / initial_capital * 100.0,
        peak_deployed_pct=deployed_hist.max() / initial_capital * 100.0,
        years=years, equity=eq,
    )


def main():
    cost_df = load_costs()
    pairs_all = sorted(f.replace("_1h.csv", "") for f in os.listdir(DATA_DIR) if f.endswith("_1h.csv"))
    data = {}
    for p in pairs_all:
        b = add_indicators(load_4h(p))
        if len(b) >= 200:
            data[p] = b
    fx = [p for p in data if p != "XAUUSD"]
    pd.set_option("display.width", 250)

    # ============ 1. 訊號成本比 (ATR / 點差) ============
    print("=" * 100)
    print("【1】訊號成本比：每根 4H 的 ATR 相當於幾倍點差？")
    print("=" * 100)
    rows = []
    for p in fx:
        costs = pair_costs(cost_df, p)
        atr_price = data[p]["atr"].median()
        spread_price = costs["spread_pips"] * costs["pip_size"]
        base = run_engine(data[p], BASE_CFG, costs)
        rows.append(dict(
            pair=p,
            ATR_pips=atr_price / costs["pip_size"],
            spread_pips=costs["spread_pips"],
            ATR除以點差=atr_price / spread_price,
            年化報酬=base["ann_return_pct"],
            夏普=base["sharpe"],
            MDD=base["max_dd_pct"],
        ))
    sig = pd.DataFrame(rows).sort_values("ATR除以點差", ascending=False)
    print(sig.round(2).to_string(index=False))
    print(f"\n  相關性 ATR/點差 vs 年化報酬 = {sig['ATR除以點差'].corr(sig['年化報酬']):+.3f}")
    print(f"  相關性 ATR/點差 vs 夏普     = {sig['ATR除以點差'].corr(sig['夏普']):+.3f}")
    sig.to_csv("fx_signal_cost_ratio.csv", index=False, encoding="utf-8-sig")

    # ============ 2. 較短時間停損 ============
    print("\n" + "=" * 100)
    print("【2】時間停損長度測試 (雙向 + 較近止盈)")
    print("=" * 100)
    rows = []
    for tb in [0, 20, 40, 60, 90]:
        cfg = {**BASE_CFG, "direction": "both", "tp_mode": "nearer", "time_exit_bars": tb}
        res = [run_engine(data[p], cfg, pair_costs(cost_df, p)) for p in fx]
        rows.append(dict(
            時間停損根數=tb if tb else "不設限",
            平均年化報酬=np.mean([r["ann_return_pct"] for r in res]),
            獲利標的比=f"{sum(1 for r in res if r['total_return_pct'] > 0)}/{len(res)}",
            平均勝率=np.mean([r["win_rate"] for r in res]),
            平均MDD=np.mean([r["max_dd_pct"] for r in res]),
            平均夏普=np.mean([r["sharpe"] for r in res]),
            時間出場筆數=np.mean([r["n_time"] for r in res]),
        ))
    print(pd.DataFrame(rows).round(2).to_string(index=False))

    # ============ 3. 標的間相關性 ============
    print("\n" + "=" * 100)
    print("【3】各標的策略損益的相關性 (決定分散有沒有用)")
    print("=" * 100)
    curves = {}
    cfg_best = {**BASE_CFG, "direction": "both", "tp_mode": "nearer"}
    for p in fx:
        r = run_engine(data[p], cfg_best, pair_costs(cost_df, p))
        curves[p] = pd.Series(r["equity"].to_numpy(), index=data[p]["datetime"])
    cm = pd.DataFrame(curves).ffill().pct_change().corr()
    iu = np.triu_indices_from(cm.values, k=1)
    print(f"  平均兩兩相關係數 = {cm.values[iu].mean():+.3f}")
    print(f"  最高相關 = {cm.values[iu].max():+.3f} / 最低 = {cm.values[iu].min():+.3f}")

    # ============ 4. 單一帳戶多標的組合 ============
    print("\n" + "=" * 100)
    print("【4】單一 $25,000 帳戶同時跑多檔 (共用資金池)")
    print("=" * 100)

    ranked = sig.sort_values("夏普", ascending=False)["pair"].tolist()
    baskets = {
        "單跑 AUDCAD (最佳單一標的)": ["AUDCAD"],
        "前 3 檔": ranked[:3],
        "前 6 檔": ranked[:6],
        "前 10 檔": ranked[:10],
        "全部 18 檔": fx,
    }
    rows = []
    for name, bp in baskets.items():
        for cfg_name, cfg in [("md原版(只做多/中軌止盈)", BASE_CFG),
                              ("優化版(雙向/較近止盈)", cfg_best)]:
            r = run_portfolio(data, bp, cost_df, cfg)
            rows.append(dict(
                組合=name, 設定=cfg_name, 檔數=len(bp),
                年化報酬=r["ann_return_pct"], MDD=r["max_dd_pct"],
                夏普=r["sharpe"], Calmar=r["calmar"], 勝率=r["win_rate"],
                交易數=r["n_trades"], 平均動用資金比=r["avg_deployed_pct"],
                最高動用資金比=r["peak_deployed_pct"],
            ))
    port = pd.DataFrame(rows)
    print(port.round(2).to_string(index=False))
    port.to_csv("fx_portfolio_results.csv", index=False, encoding="utf-8-sig")

    # ============ 5. 組合 + 放大部位 ============
    print("\n" + "=" * 100)
    print("【5】前 10 檔組合 + 放大首單金額 (優化版設定)")
    print("=" * 100)
    rows = []
    for bo in [1500.0, 2500.0, 4000.0]:
        cfg = {**cfg_best, "base_order": bo}
        r = run_portfolio(data, ranked[:10], cost_df, cfg)
        rows.append(dict(
            首單金額=bo, 年化報酬=r["ann_return_pct"], MDD=r["max_dd_pct"],
            夏普=r["sharpe"], Calmar=r["calmar"],
            平均動用資金比=r["avg_deployed_pct"], 最高動用資金比=r["peak_deployed_pct"],
        ))
    print(pd.DataFrame(rows).round(2).to_string(index=False))
    print("\n--> 已輸出 fx_signal_cost_ratio.csv / fx_portfolio_results.csv")


if __name__ == "__main__":
    main()
