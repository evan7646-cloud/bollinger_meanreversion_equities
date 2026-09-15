"""單一帳戶多貨幣對組合模擬 (v2 引擎：正確跨幣別部位與手數)"""
import numpy as np, pandas as pd
from fx_engine_v2 import (load_4h, add_indicators, build_usd_rates, load_costs_all_pairs,
                          PAIR_CCY, CFG, INITIAL_CAPITAL, CONTRACT_SIZE,
                          COMMISSION_PER_LOT_SIDE)


def run_portfolio_v2(pairs, capital=INITIAL_CAPITAL, cfg=CFG, base_order=None,
                     start=None, end=None):
    need = set()
    for p in pairs:
        b, q = PAIR_CCY[p]; need.add(b); need.add(q)
    rates = build_usd_rates(need)
    # 用 load_costs_all_pairs()：已調查過的用真實點差，沒調查過的補保守估計值
    costs_all = load_costs_all_pairs()

    # 指標先用完整歷史算（EMA50/ATR20 需要暖機），算完才切區間，
    # 否則每個 walk-forward 視窗的頭 70 根會因為暖機不足而失真。
    data = {}
    for p in pairs:
        d = add_indicators(load_4h(p))
        if start is not None: d = d[d.index >= pd.Timestamp(start)]
        if end is not None:   d = d[d.index <  pd.Timestamp(end)]
        data[p] = d
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
            adx=(d["adx"].to_numpy(float) if "adx" in d else np.zeros(len(d))),
            di_up=(d["di_up"].to_numpy(float) if "di_up" in d else np.ones(len(d))),
            rb=rb.to_numpy(float), rq=rq.to_numpy(float),
            # 波動度目標化縮放（見 fx_engine_v2.CFG["vol_target"] 的說明）
            vs=((d["atr"].rolling(500, min_periods=100).median() / d["atr"])
                .clip(*cfg["vol_clip"]).fillna(1.0).to_numpy(float)
                if cfg.get("vol_target") else np.ones(len(d))),
            date=d.index.date, wd=d.index.weekday, costs=costs_all[p],
            side=0, qty=0.0, layer=0, avg=0.0, last_date=None, last_j=0,
        )

    sched = [[] for _ in idx]
    for p in pairs:
        for j, gi in enumerate(books[p]["rows"]):
            sched[gi].append((p, j))

    # 回撤熔斷：權益距離歷史高點超過 halt_dd% 時停止新開倉（既有部位仍照常管理），
    # 等回撤收斂到 resume_dd% 以內才恢復。針對的是「區間被打破、策略連續接刀」的情境，
    # 例如 2024-07 日圓套利平倉：ADX 過濾抓得到趨勢，但抓不到趨勢「急轉」。
    halt_dd = cfg.get("halt_dd_pct")
    resume_dd = cfg.get("resume_dd_pct", 0.0)
    halted = False
    run_peak = capital

    cash = capital
    equity = np.empty(len(idx))
    # 盤中最不利價計價（多單用 low、空單用 high）——收盤價計價會低估浮虧，
    # 這條是保守上界（假設所有部位同一瞬間都摸到各自最差點），真實落在兩者之間。
    equity_adv = np.empty(len(idx))
    floating = np.empty(len(idx))
    deployed = np.empty(len(idx))
    trades, all_lots = [], []

    for gi in range(len(idx)):
        if halt_dd and gi > 0:
            cur_dd = (run_peak - equity[gi - 1]) / run_peak * 100.0
            if not halted and cur_dd >= halt_dd:
                halted = True
            elif halted and cur_dd <= resume_dd:
                halted = False

        for p, j in sched[gi]:
            st = books[p]
            st["last_j"] = j
            c, h, l = st["close"][j], st["high"][j], st["low"][j]
            atr, ema = st["atr"][j], st["ema"][j]
            # ADX 趨勢強度過濾（見 fx_engine_v2.CFG["adx_max"]）
            _am = cfg.get("adx_max")
            _hot = bool(_am) and np.isfinite(st["adx"][j]) and st["adx"][j] > _am
            if not _hot:
                long_ok = short_ok = True
            elif cfg.get("adx_directional", False):
                # 只擋逆勢那一邊（上升趨勢放行做多、下降趨勢放行做空）
                long_ok  = (st["di_up"][j] >= 0.5)
                short_ok = (st["di_up"][j] < 0.5)
            else:
                long_ok = short_ok = False
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
                if halted:
                    trig = None
                elif l <= lower and long_ok:
                    trig, px, sd = 1, min(c, lower) + hs, 1
                elif h >= upper and short_ok:
                    trig, px, sd = -1, max(c, upper) - hs, -1
                if trig:
                    bo = base_order * st["vs"][j]
                    q = bo / rb
                    lots = q / CONTRACT_SIZE
                    comm = lots * COMMISSION_PER_LOT_SIDE
                    if cash > bo * 0.05 + comm:
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

                _side_ok = long_ok if sd > 0 else short_ok
                dca_allowed = _side_ok or not cfg.get("adx_block_dca", True)
                if not closed and st["layer"] < cfg["max_layers"] and dca_allowed:
                    step = st["layer"] * cfg["dca_step"] * atr
                    hit = (sd > 0 and l <= st["avg"] - step) or (sd < 0 and h >= st["avg"] + step)
                    if hit:
                        px = (st["avg"] - step + hs) if sd > 0 else (st["avg"] + step - hs)
                        do = dca_order * st["vs"][j]
                        q = do / rb
                        lots = q / CONTRACT_SIZE
                        comm = lots * COMMISSION_PER_LOT_SIDE
                        if cash > do * 0.05 + comm:
                            cash -= comm
                            st["avg"] = (st["avg"] * st["qty"] + px * q) / (st["qty"] + q)
                            st["qty"] += q; st["layer"] += 1
                            all_lots.append(lots)

                if closed:
                    st.update(side=0, qty=0.0, layer=0, avg=0.0)

        mtm, dep, mtm_adv = 0.0, 0.0, 0.0
        for p in pairs:
            st = books[p]
            if st["side"] != 0:
                j = st["last_j"]
                mtm += st["qty"] * (st["close"][j] - st["avg"]) * st["rq"][j] * st["side"]
                adv_px = st["low"][j] if st["side"] > 0 else st["high"][j]
                mtm_adv += st["qty"] * (adv_px - st["avg"]) * st["rq"][j] * st["side"]
                dep += st["qty"] * st["rb"][j]
        equity[gi] = cash + mtm
        if equity[gi] > run_peak: run_peak = equity[gi]
        equity_adv[gi] = cash + mtm_adv
        # 未平倉部位的浮動損益（權益 − 現金）。這跟「目前回撤」是兩回事：
        # 回撤量的是「比權益高點低多少」，而權益高點本身通常也掛著浮虧，
        # 所以回撤永遠小於浮虧，差額就是高點當時的浮虧深度。
        floating[gi] = mtm
        deployed[gi] = dep

    eq = pd.Series(equity, index=pd.DatetimeIndex(idx))
    total = (eq.iloc[-1] - capital) / capital * 100.0
    peak = eq.cummax(); mdd_close = abs(((eq - peak) / peak).min()) * 100.0
    cur_dd = abs((eq.iloc[-1] - peak.iloc[-1]) / peak.iloc[-1]) * 100.0
    eq_adv = pd.Series(equity_adv, index=pd.DatetimeIndex(idx))
    mdd = abs(((eq_adv - peak) / peak).min()) * 100.0
    cur_float_pct = floating[-1] / capital * 100.0
    years = (idx[-1] - idx[0]).total_seconds() / 86400.0 / 365.25
    rets = eq.pct_change().dropna()
    sharpe = rets.mean() / rets.std() * np.sqrt(len(eq) / years) if rets.std() > 0 else 0.0
    ann = ((1 + total / 100.0) ** (1 / years) - 1) * 100.0
    pnls = [t[1] for t in trades]
    wins = [x for x in pnls if x > 0]

    return dict(total_return_pct=total, ann_return_pct=ann, max_dd_pct=mdd,
                max_dd_close_pct=mdd_close, current_float_pct=cur_float_pct,
                current_dd_pct=cur_dd, sharpe=sharpe,
                calmar=ann / mdd if mdd > 0.01 else np.nan,
                win_rate=len(wins) / len(pnls) * 100.0 if pnls else 0.0, n_trades=len(pnls),
                avg_deployed_pct=deployed.mean() / capital * 100.0,
                peak_deployed_pct=deployed.max() / capital * 100.0,
                # 短視窗（walk-forward 的單一持有段）可能完全沒有訊號、一筆都沒開，
                # 此時 all_lots 是空陣列，np.median/np.max 會拋 ValueError。
                median_lots=float(np.median(all_lots)) if all_lots else 0.0,
                max_lots=float(np.max(all_lots)) if all_lots else 0.0,
                years=years, equity=eq, equity_adv=eq_adv)


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
