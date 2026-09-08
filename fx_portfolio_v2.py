"""單一帳戶多貨幣對組合模擬 (v2 引擎：正確跨幣別部位與手數)"""
import numpy as np, pandas as pd
from fx_engine_v2 import (load_4h, add_indicators, build_usd_rates, load_costs_all_pairs,
                          PAIR_CCY, CFG, INITIAL_CAPITAL, CONTRACT_SIZE,
                          COMMISSION_PER_LOT_SIDE)


def run_portfolio_v2(pairs, capital=INITIAL_CAPITAL, cfg=CFG, base_order=None):
    need = set()
    for p in pairs:
        b, q = PAIR_CCY[p]; need.add(b); need.add(q)
    rates = build_usd_rates(need)
    # 用 load_costs_all_pairs()：已調查過的用真實點差，沒調查過的補保守估計值
    costs_all = load_costs_all_pairs()

    data = {p: add_indicators(load_4h(p)) for p in pairs}
    idx = sorted(set().union(*[set(d.index) for d in data.values()]))
    pos = {t: i for i, t in enumerate(idx)}
    base_order = base_order if base_order else cfg["base_order"]
    dca_order = base_order * cfg["size_mult"]

    books = {}
    for p in pairs:
        d = data[p]
        b, q = PAIR_CCY[p]
        rb = (pd.Series(1.0, index=d.index) if b == "USD" else rates[b].reindex(d.index).ffill().bfill())
        rq = (pd.Series(1.0, index=d.index) if q == "USD" else rates[q].reindex(d.index).ffill().bfill())
        books[p] = dict(
            rows=[pos[t] for t in d.index],
            high=d["high"].to_numpy(float), low=d["low"].to_numpy(float),
            close=d["close"].to_numpy(float), atr=d["atr"].to_numpy(float),
            ema=d["ema50"].to_numpy(float),
            rb=rb.to_numpy(float), rq=rq.to_numpy(float),
            date=d.index.date, wd=d.index.weekday, costs=costs_all[p],
            side=0, qty=0.0, layer=0, avg=0.0, last_date=None, last_j=0,
        )

    sched = [[] for _ in idx]
    for p in pairs:
        for j, gi in enumerate(books[p]["rows"]):
            sched[gi].append((p, j))

    cash = capital
    equity = np.empty(len(idx))
    deployed = np.empty(len(idx))
    trades, all_lots = [], []

    for gi in range(len(idx)):
        for p, j in sched[gi]:
            st = books[p]
            st["last_j"] = j
            c, h, l = st["close"][j], st["high"][j], st["low"][j]
            atr, ema = st["atr"][j], st["ema"][j]
            rb, rq = st["rb"][j], st["rq"][j]
            cs = st["costs"]
            hs = cs["spread_pips"] * cs["pip_size"] / 2.0

            if st["last_date"] is not None and st["date"][j] != st["last_date"] and st["side"] != 0:
                mult = 3.0 if st["wd"][j] == 2 else 1.0
                lots = st["qty"] / CONTRACT_SIZE
                cash += lots * (cs["swap_long"] if st["side"] > 0 else cs["swap_short"]) * mult
            st["last_date"] = st["date"][j]

            lower, upper = ema - cfg["channel_k"] * atr, ema + cfg["channel_k"] * atr

            if st["side"] == 0:
                trig = None
                if l <= lower:
                    trig, px, sd = 1, min(c, lower) + hs, 1
                elif h >= upper:
                    trig, px, sd = -1, max(c, upper) - hs, -1
                if trig:
                    q = base_order / rb
                    lots = q / CONTRACT_SIZE
                    comm = lots * COMMISSION_PER_LOT_SIDE
                    if cash > base_order * 0.05 + comm:
                        cash -= comm
                        st.update(side=sd, qty=q, layer=1, avg=px)
                        all_lots.append(lots)
            else:
                closed = False
                sd = st["side"]
                if sd > 0:
                    tp, sl = min(ema, st["avg"] + cfg["tp_atr"] * atr), st["avg"] - cfg["stop_atr"] * atr
                    px, kind = (tp - hs, "TP") if h >= tp else ((sl - hs, "SL") if l <= sl else (None, None))
                    if px is not None:
                        pnl = st["qty"] * (px - st["avg"]) * rq
                        comm = (st["qty"] / CONTRACT_SIZE) * COMMISSION_PER_LOT_SIDE
                        cash += pnl - comm; trades.append((kind, pnl - comm, p)); closed = True
                else:
                    tp, sl = max(ema, st["avg"] - cfg["tp_atr"] * atr), st["avg"] + cfg["stop_atr"] * atr
                    px, kind = (tp + hs, "TP") if l <= tp else ((sl + hs, "SL") if h >= sl else (None, None))
                    if px is not None:
                        pnl = st["qty"] * (st["avg"] - px) * rq
                        comm = (st["qty"] / CONTRACT_SIZE) * COMMISSION_PER_LOT_SIDE
                        cash += pnl - comm; trades.append((kind, pnl - comm, p)); closed = True

                if not closed and st["layer"] < cfg["max_layers"]:
                    step = st["layer"] * cfg["dca_step"] * atr
                    hit = (sd > 0 and l <= st["avg"] - step) or (sd < 0 and h >= st["avg"] + step)
                    if hit:
                        px = (st["avg"] - step + hs) if sd > 0 else (st["avg"] + step - hs)
                        q = dca_order / rb
                        lots = q / CONTRACT_SIZE
                        comm = lots * COMMISSION_PER_LOT_SIDE
                        if cash > dca_order * 0.05 + comm:
                            cash -= comm
                            st["avg"] = (st["avg"] * st["qty"] + px * q) / (st["qty"] + q)
                            st["qty"] += q; st["layer"] += 1
                            all_lots.append(lots)

                if closed:
                    st.update(side=0, qty=0.0, layer=0, avg=0.0)

        mtm, dep = 0.0, 0.0
        for p in pairs:
            st = books[p]
            if st["side"] != 0:
                j = st["last_j"]
                mtm += st["qty"] * (st["close"][j] - st["avg"]) * st["rq"][j] * st["side"]
                dep += st["qty"] * st["rb"][j]
        equity[gi] = cash + mtm
        deployed[gi] = dep

    eq = pd.Series(equity, index=pd.DatetimeIndex(idx))
    total = (eq.iloc[-1] - capital) / capital * 100.0
    peak = eq.cummax(); mdd = abs(((eq - peak) / peak).min()) * 100.0
    cur_dd = abs((eq.iloc[-1] - peak.iloc[-1]) / peak.iloc[-1]) * 100.0
    years = (idx[-1] - idx[0]).total_seconds() / 86400.0 / 365.25
    rets = eq.pct_change().dropna()
    sharpe = rets.mean() / rets.std() * np.sqrt(len(eq) / years) if rets.std() > 0 else 0.0
    ann = ((1 + total / 100.0) ** (1 / years) - 1) * 100.0
    pnls = [t[1] for t in trades]
    wins = [x for x in pnls if x > 0]

    return dict(total_return_pct=total, ann_return_pct=ann, max_dd_pct=mdd,
                current_dd_pct=cur_dd, sharpe=sharpe,
                calmar=ann / mdd if mdd > 0.01 else np.nan,
                win_rate=len(wins) / len(pnls) * 100.0 if pnls else 0.0, n_trades=len(pnls),
                avg_deployed_pct=deployed.mean() / capital * 100.0,
                peak_deployed_pct=deployed.max() / capital * 100.0,
                median_lots=float(np.median(all_lots)), max_lots=float(np.max(all_lots)),
                years=years, equity=eq)


if __name__ == "__main__":
    TOP8 = ['AUDCAD', 'AUDNZD', 'NZDCAD', 'CADJPY', 'AUDUSD', 'GBPAUD', 'USDCHF', 'EURAUD']
    for cap, bo in [(25000.0, 1500.0), (100000.0, 6000.0)]:
        r = run_portfolio_v2(TOP8, capital=cap, base_order=bo)
        print(f"\n=== ${cap:,.0f} 帳戶 / 首單 ${bo:,.0f} ===")
        print(f"年化 {r['ann_return_pct']:.2f}%  MDD {r['max_dd_pct']:.2f}%  "
              f"Sharpe {r['sharpe']:.2f}  Calmar {r['calmar']:.2f}")
        print(f"總報酬 {r['total_return_pct']:.2f}% ({r['years']:.2f}年)  勝率 {r['win_rate']:.1f}%  "
              f"交易 {r['n_trades']} 筆")
        print(f"平均動用 {r['avg_deployed_pct']:.1f}%  峰值動用 {r['peak_deployed_pct']:.1f}%")
        print(f"手數：中位 {r['median_lots']:.4f}  最大 {r['max_lots']:.4f}")
        print(f"年化獲利金額 ≈ ${cap * r['ann_return_pct'] / 100:,.0f}")
