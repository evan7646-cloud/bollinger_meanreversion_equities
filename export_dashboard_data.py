# -*- coding: utf-8 -*-
"""
匯出 Phase 8（含真實spread+手續費+隔夜倉息+風控優化）回測結果為 JSON，
供 docs/dashboard.html 網頁儀表板讀取。跟 EA 實盤清單(TRADE_LIST 10檔)完全一致。
"""

import json
import os
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from bollinger_meanreversion_equities_cfd_test import load_symbol_15m
from bollinger_meanreversion_full_cost_test import (
    compute_features, apply_risk_based_sizing, apply_daily_loss_limit,
    TRAIN_FRACTION,
)
from bollinger_meanreversion_swap_cost_test import (
    load_swap_rates, backtest_with_swap, TRADE_LIST,
)
from bollinger_meanreversion_performance_report import full_performance_stats


def build_calendar_equity(tdf: pd.DataFrame, cal_index: pd.DatetimeIndex) -> pd.Series:
    tdf = tdf.sort_values("exit_t")
    eq = (1 + tdf["pnl"]).cumprod()
    eq.index = tdf["exit_t"]
    eq = eq.reindex(cal_index.union(eq.index)).ffill().fillna(1.0)
    return eq.reindex(cal_index).ffill().fillna(1.0)


def clean(obj):
    """把 numpy/NaN 轉成 JSON 安全型別。"""
    if isinstance(obj, dict):
        return {k: clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [clean(v) for v in obj]
    if isinstance(obj, (np.floating, float)):
        return None if (obj is None or np.isnan(obj)) else round(float(obj), 6)
    if isinstance(obj, (np.integer,)):
        return int(obj)
    return obj


def main():
    swap_rates = load_swap_rates()
    per_symbol_trades = {}
    symbols_meta = []

    for sym in TRADE_LIST:
        if sym not in swap_rates:
            print(f"  {sym}: 缺少倉息資料，跳過")
            continue
        sw = swap_rates[sym]
        raw = load_symbol_15m(sym)
        feat = compute_features(raw)
        split_i = int(len(raw) * TRAIN_FRACTION)
        split_time = raw["datetime"].iloc[split_i]

        tdf = backtest_with_swap(feat, sw["swap_long"], sw["swap_short"], sw["tick_value"])
        if tdf.empty:
            continue
        tdf = tdf.sort_values("exit_t").reset_index(drop=True)
        tdf_sized = apply_risk_based_sizing(tdf)
        tdf_final = apply_daily_loss_limit(tdf_sized).sort_values("exit_t").reset_index(drop=True)
        per_symbol_trades[sym] = (tdf_final, split_time, raw["datetime"].iloc[0], raw["datetime"].iloc[-1])

        train_tdf = tdf_final[tdf_final["exit_t"] < split_time]
        test_tdf = tdf_final[tdf_final["exit_t"] >= split_time]
        train_days = (split_time - raw["datetime"].iloc[0]).total_seconds() / 86400
        test_days = (raw["datetime"].iloc[-1] - split_time).total_seconds() / 86400

        train_stats, _ = full_performance_stats(train_tdf, train_days)
        test_stats, _ = full_performance_stats(test_tdf, test_days)
        symbols_meta.append({
            "symbol": sym,
            "avg_nights_held": round(tdf["nights"].mean(), 2),
            "train": train_stats,
            "test": test_stats,
        })
        print(f"  {sym}: train_trades={len(train_tdf)} test_trades={len(test_tdf)}")

    def pooled(split_name):
        frames = []
        for sym, (tdf, split_time, start, end) in per_symbol_trades.items():
            sub = tdf[tdf["exit_t"] < split_time] if split_name == "TRAIN" else tdf[tdf["exit_t"] >= split_time]
            if not sub.empty:
                frames.append(sub.assign(symbol=sym))
        if not frames:
            return None, None, None
        all_trades = pd.concat(frames).sort_values("exit_t").reset_index(drop=True)
        cal_index = pd.date_range(all_trades["exit_t"].min(), all_trades["exit_t"].max(), freq="D")
        sub_eqs = []
        for sym, (tdf, split_time, start, end) in per_symbol_trades.items():
            sub = tdf[tdf["exit_t"] < split_time] if split_name == "TRAIN" else tdf[tdf["exit_t"] >= split_time]
            sub_eqs.append(build_calendar_equity(sub, cal_index) if not sub.empty else pd.Series(1.0, index=cal_index))
        portfolio_equity = sum(sub_eqs) / len(sub_eqs)
        days = (cal_index[-1] - cal_index[0]).total_seconds() / 86400
        total_return = portfolio_equity.iloc[-1] - 1
        ann_return = (1 + total_return) ** (365.25 / days) - 1 if days > 0 else np.nan
        daily_ret = portfolio_equity.pct_change().dropna()
        sharpe = (daily_ret.mean() / daily_ret.std()) * np.sqrt(252) if daily_ret.std() > 0 else np.nan
        running_max = portfolio_equity.cummax()
        max_dd = (portfolio_equity / running_max - 1).min()
        stats = {
            "n_symbols": len(per_symbol_trades), "n_trades": len(all_trades), "period_days": round(days, 1),
            "total_return_pct": total_return * 100, "ann_return_pct": ann_return * 100,
            "ann_sharpe": sharpe, "max_drawdown_pct": max_dd * 100,
            "win_rate_pct": (all_trades["pnl"] > 0).mean() * 100,
        }
        return stats, portfolio_equity, all_trades

    train_stats, train_equity, train_trades = pooled("TRAIN")
    test_stats, test_equity, test_trades = pooled("TEST")

    equity_curve = {
        "train": [{"date": d.strftime("%Y-%m-%d"), "equity": v} for d, v in train_equity.items()],
        "test": [{"date": d.strftime("%Y-%m-%d"), "equity": v} for d, v in test_equity.items()],
    }

    all_trades_test = [
        {
            "symbol": row["symbol"],
            "type": row["type"],
            "entry_t": row["entry_t"].strftime("%Y-%m-%d %H:%M"),
            "exit_t": row["exit_t"].strftime("%Y-%m-%d %H:%M"),
            "pnl_pct": row["pnl"] * 100,
            "nights": int(row["nights"]),
        }
        for _, row in test_trades.sort_values("exit_t", ascending=False).iterrows()
    ]

    output = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "strategy_name": "Bollinger Band 均值回歸 (MA20±2σ, 15m)",
        "universe": TRADE_LIST,
        "timeframe": "M15",
        "cost_model": "真實 spread(進出場各一次) + 手續費 0.002%x2 + 真實隔夜倉息(swap) + 風險部位 1%/筆 + 每日虧損熔斷 3%",
        "ea_file": "BollingerMeanReversion.mq5",
        "portfolio_metrics": {"train": train_stats, "test": test_stats},
        "symbols_meta": symbols_meta,
        "equity_curve": equity_curve,
        "all_trades_test": all_trades_test,
    }

    os.makedirs("docs", exist_ok=True)
    out_path = "docs/strategy_data.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(clean(output), f, ensure_ascii=False, indent=None)
    size_kb = os.path.getsize(out_path) / 1024
    print(f"\n已匯出 {out_path}（{size_kb:.1f} KB），TEST期交易數={len(all_trades_test)}")
    print(f"Pooled TEST: ann_return={test_stats['ann_return_pct']:.2f}%  max_dd={test_stats['max_drawdown_pct']:.2f}%  sharpe={test_stats['ann_sharpe']:.2f}")


if __name__ == "__main__":
    main()
