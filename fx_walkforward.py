"""滾動式選股（walk-forward）比較框架

回答的問題：**定期依「近期表現」重選交易清單，會比固定清單好嗎？**

作法：把樣本切成一連串「回看視窗 → 持有視窗」。每到再平衡日，只用回看視窗
（也就是當下真的看得到的資料）算出排名、選出前 K 檔，然後在接下來的持有視窗
交易那 K 檔。持有視窗結束才重選。這樣每一段的選股都沒有用到未來資料。

為什麼需要這支程式：第 4 節已經證明「用全樣本 Sharpe 排名」挑標的在樣本外
沒有預測力（相關性 +0.01，且當初排名最差的 10 檔在樣本外反而更好）。但那是
「排一次、用到底」的靜態檢驗。滾動式重選是另一個問題——也許排名沒有長期
持續性，卻有短期動能。這支程式就是拿來檢驗這件事，而不是預設它有效。

每一段持有視窗都從乾淨的資金重跑，段與段之間把報酬率相乘串起來。等於假設
再平衡當下把所有部位平掉——這正是實務上換掉一檔商品該做的事（留著不管會變成
EA 不再管理的孤兒倉，見 strategy_logic_and_backtest_truth.md 第 2.6 節）。
"""
import numpy as np
import pandas as pd

from fx_engine_v2 import (ALL_28_PAIRS, load_4h, add_indicators, build_usd_rates,
                          load_costs_all_pairs, run_engine_v2, PAIR_CCY, INITIAL_CAPITAL)
from fx_portfolio_v2 import run_portfolio_v2


# --------------------------------------------------------------- 選股規則
# 每個規則吃「回看視窗內的單檔回測結果 + 該檔價格」，回傳分數，越高越先被選。
# 分成兩類：
#   ·「績效類」用策略在回看視窗的表現排名（sharpe / calmar / smooth）
#   ·「結構類」不看策略績效，只看價格本身的統計性質（revert / cost）
#     ——後者比較不容易過擬合，因為它衡量的是「這個商品適不適合均值回歸」，
#       而不是「這個商品最近剛好賺了多少」。

def _smoothness(equity):
    """權益曲線與時間的 Pearson 相關係數：越接近 1 越像單調斜線上升。"""
    if len(equity) < 10:
        return np.nan
    return np.corrcoef(np.arange(len(equity)), equity)[0, 1]


def _variance_ratio(close, q=8):
    """Lo-MacKinlay 變異比。VR < 1 代表均值回歸、VR > 1 代表趨勢。
    這裡回傳 (1 - VR)，所以數值越大代表回歸性越強、越適合本策略。"""
    r = np.diff(np.log(close))
    if len(r) < q * 10:
        return np.nan
    var1 = np.var(r, ddof=1)
    rq = np.array([r[i:i + q].sum() for i in range(0, len(r) - q + 1)])
    varq = np.var(rq, ddof=1) / q
    if var1 <= 0:
        return np.nan
    return 1.0 - varq / var1


def score_sharpe(res, bars, costs):
    return res["sharpe"]


def score_calmar(res, bars, costs):
    return res["calmar"] if np.isfinite(res["calmar"]) else -9e9


def score_smooth(res, bars, costs):
    return _smoothness(res["equity"].to_numpy())


def score_revert(res, bars, costs):
    """純結構：價格本身的均值回歸強度，完全不看策略賺了多少。"""
    return _variance_ratio(bars["close"].to_numpy())


def score_cost(res, bars, costs):
    """純結構：ATR 相對點差的倍數。越大代表可賺的波動遠大於摩擦成本。"""
    atr = bars["atr"].median()
    pip = costs["pip_size"]
    spread = costs["spread_pips"] * pip
    return (atr / spread) if spread > 0 else np.nan


RULES = {
    "sharpe": score_sharpe,
    "calmar": score_calmar,
    "smooth": score_smooth,
    "revert": score_revert,
    "cost":   score_cost,
}


# --------------------------------------------------------------- 主流程
def rank_pairs(pairs, start, end, rule, rates, costs_all):
    """在 [start, end) 這個回看視窗內，對每檔算分數並排序。"""
    fn = RULES[rule]
    rows = []
    for p in pairs:
        bars = add_indicators(load_4h(p))
        w = bars[(bars.index >= pd.Timestamp(start)) & (bars.index < pd.Timestamp(end))]
        if len(w) < 60:            # 視窗太短，指標與統計都不可信
            continue
        try:
            res = run_engine_v2(w, p, costs_all[p], rates)
            s = fn(res, w, costs_all[p])
        except Exception:
            s = np.nan
        if s is not None and np.isfinite(s):
            rows.append((p, float(s)))
    rows.sort(key=lambda x: -x[1])
    return [p for p, _ in rows]


def walk_forward(rule, top_k=12, lookback_days=180, hold_days=60,
                 universe=None, capital=INITIAL_CAPITAL, verbose=True):
    """滾動選股回測。回傳每一段的結果與串接後的總績效。"""
    universe = universe or ALL_28_PAIRS
    costs_all = load_costs_all_pairs()
    need = set()
    for p in universe:
        b, q = PAIR_CCY[p]
        need.add(b); need.add(q)
    rates = build_usd_rates(need)

    idx = add_indicators(load_4h(universe[0])).index
    t0, tN = idx[0], idx[-1]

    segs = []
    cur = t0 + pd.Timedelta(days=lookback_days)
    while cur < tN:
        nxt = min(cur + pd.Timedelta(days=hold_days), tN)
        if (nxt - cur).days < hold_days * 0.5:      # 尾巴太短就併掉，避免雜訊
            break
        segs.append((cur - pd.Timedelta(days=lookback_days), cur, nxt))
        cur = nxt

    equity_mult = 1.0
    out = []
    for lb_s, lb_e, hold_e in segs:
        sel = rank_pairs(universe, lb_s, lb_e, rule, rates, costs_all)[:top_k]
        if not sel:
            continue
        r = run_portfolio_v2(sel, capital=capital, start=lb_e, end=hold_e)
        seg_ret = r["total_return_pct"] / 100.0
        equity_mult *= (1 + seg_ret)
        out.append(dict(rebalance=lb_e.date(), hold_to=hold_e.date(),
                        ret_pct=seg_ret * 100, mdd_pct=r["max_dd_pct"],
                        n_trades=r["n_trades"], picked=",".join(sel)))
        if verbose:
            print(f"  {lb_e.date()} → {hold_e.date()}  報酬 {seg_ret*100:+6.2f}%  "
                  f"MDD {r['max_dd_pct']:5.2f}%  選中 {' '.join(sel[:6])}...")

    df = pd.DataFrame(out)
    years = (tN - (t0 + pd.Timedelta(days=lookback_days))).days / 365.25
    total = (equity_mult - 1) * 100
    ann = ((equity_mult) ** (1 / years) - 1) * 100 if years > 0 else np.nan
    return dict(rule=rule, top_k=top_k, lookback=lookback_days, hold=hold_days,
                total_return_pct=total, ann_return_pct=ann,
                worst_seg_pct=df["ret_pct"].min() if len(df) else np.nan,
                max_seg_mdd_pct=df["mdd_pct"].max() if len(df) else np.nan,
                n_segments=len(df), segments=df, years=years)


def static_baseline(pairs, lookback_days=180, capital=INITIAL_CAPITAL):
    """對照組：固定清單、完全不重選，跑同一段可交易期間。"""
    idx = add_indicators(load_4h(pairs[0])).index
    start = idx[0] + pd.Timedelta(days=lookback_days)
    r = run_portfolio_v2(pairs, capital=capital, start=start)
    return dict(total_return_pct=r["total_return_pct"], ann_return_pct=r["ann_return_pct"],
                max_dd_pct=r["max_dd_pct"], n_trades=r["n_trades"])
