"""
產生 GitHub Pages 靜態報告（docs/index.html）

流程：
  1. 用 TradingView 上的 Pepperstone 報價（PEPPERSTONE:xxx）重新抓取交易清單貨幣對的
     1H 報價，並轉換成 MT5 broker 時間（見 fx_data_pepperstone.py 的時區換算說明）
  2. 重採樣為「跟 MT5 內建 H4 對齊」的 4H K棒，跑 fx_engine_v2 的通道均值回歸 DCA 網格
     （雙向、較近止盈、4ATR硬停損）
  3. 輸出：逐檔績效、組合績效、完整交易明細（entry/exit）
  4. 把上述資料塞進 docs/report_template.html 產生 docs/index.html

改用 Pepperstone 而非 yfinance 的原因：yfinance 是不綁定特定 broker 的聚合「指示性報價」
（quoteSourceName 甚至標註為 'Delayed Quote'），Pepperstone 是這個策略實際下單的 broker，
用它的報價回測才是真正跟實盤同源、時區也才能跟 MT5 對上。

真實點差與隔夜利息（mt5_forex_real_costs.csv）不在這個排程內更新——那份資料只能由使用者
在自己電腦上的 MT5 終端機執行 ExportForexRealCosts.mq5 才能取得（GitHub Actions 的雲端伺服器
連不到使用者的 broker 帳戶）。這支腳本只自動更新「價格資料」與「回測結果」，成本假設沿用
repo 裡目前的 mt5_forex_real_costs.csv，直到使用者手動重新匯出並提交更新。
"""
import os
import sys
import json
from datetime import datetime, timezone

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# G8 全 28 檔掃描後、依 Sharpe 排序，人工微調後的清單（fx_28pairs_scan_results.csv）
# v3.4：拿掉 CADJPY（近期 JPY 趨勢性強，不利均值回歸策略），
# 加入 CADCHF、GBPCAD。28 檔全數使用 ExportForexRealCosts.mq5 實測點差，無估計值。
TOP8 = ["AUDCAD", "AUDCHF", "GBPNZD", "GBPCHF", "EURCHF",
        "NZDUSD", "EURAUD", "AUDUSD", "NZDCHF", "CADCHF", "GBPCAD"]
DATA_DIR = os.path.join(ROOT, "data_fx_1h")
DOCS_DIR = os.path.join(ROOT, "docs")


def refresh_price_data():
    """用 TradingView 的 Pepperstone 報價重新抓取交易清單及匯率換算所需的所有貨幣對（1H）。"""
    from fx_data_pepperstone import fetch_pepperstone_1h, local_utc_offset_hours, BROKER_GMT_OFFSET_HOURS
    from fx_engine_v2 import PAIR_CCY

    need_pairs = set(TOP8)
    for p in TOP8:
        b, q = PAIR_CCY[p]
        for ccy, proxy in [("EUR", "EURUSD"), ("GBP", "GBPUSD"), ("AUD", "AUDUSD"),
                          ("NZD", "NZDUSD"), ("CAD", "USDCAD"), ("CHF", "USDCHF"),
                          ("JPY", "USDJPY")]:
            if b == ccy or q == ccy:
                need_pairs.add(proxy)

    local_off = local_utc_offset_hours()
    print(f"  本機時區 UTC{local_off:+.1f}，broker時區 UTC{BROKER_GMT_OFFSET_HOURS:+.1f}，"
          f"換算位移 {BROKER_GMT_OFFSET_HOURS - local_off:+.1f} 小時")

    os.makedirs(DATA_DIR, exist_ok=True)
    for pair in sorted(need_pairs):
        try:
            df = fetch_pepperstone_1h(pair)
            df.to_csv(os.path.join(DATA_DIR, f"{pair}_1h.csv"), index=False)
            print(f"  {pair}: {len(df)} rows, {df['datetime'].min()} -> {df['datetime'].max()} (MT5 broker 時間)")
        except Exception as e:
            existing = os.path.join(DATA_DIR, f"{pair}_1h.csv")
            if os.path.exists(existing):
                print(f"  {pair}: 抓取失敗（{e}），沿用舊資料")
            else:
                print(f"  ⚠️ {pair}: 抓取失敗（{e}）且無舊資料，可能影響回測")


def run_backtest():
    from fx_engine_v2 import (load_4h, add_indicators, build_usd_rates, run_engine_v2,
                              load_costs_all_pairs, PAIR_CCY, CFG, INITIAL_CAPITAL)
    from fx_portfolio_v2 import run_portfolio_v2

    need = set()
    for p in TOP8:
        b, q = PAIR_CCY[p]
        need.add(b); need.add(q)
    rates = build_usd_rates(need)
    costs_all = load_costs_all_pairs()

    perf_rows, curves, all_trades, open_positions = [], {}, [], []
    for p in TOP8:
        bars = add_indicators(load_4h(p))
        r = run_engine_v2(bars, p, costs_all[p], rates)
        curves[p] = (r["equity"] / INITIAL_CAPITAL - 1.0) * 100.0
        perf_rows.append(dict(
            貨幣對=p, 年化報酬=r["ann_return_pct"], MDD=r["max_dd_pct"], Sharpe=r["sharpe"],
            Calmar=r["calmar"], 總報酬=r["total_return_pct"], 勝率=r["win_rate"],
            獲利因子=r["profit_factor"], 交易數=r["n_trades"], 止盈=r["n_tp"], 停損=r["n_sl"],
            中位手數=r["median_lots"], 最大手數=r["max_lots"], 回測年數=r["years"],
            成本估計=costs_all[p].get("cost_is_estimated", False),
            點差=costs_all[p]["spread_pips"]))
        all_trades.extend(r["trade_log"])
        if r["open_position"] is not None:
            open_positions.append(r["open_position"])

    perf = pd.DataFrame(perf_rows).sort_values("Sharpe", ascending=False).reset_index(drop=True)
    port = run_portfolio_v2(TOP8)
    port_curve = (port["equity"] / INITIAL_CAPITAL - 1.0) * 100.0

    trades_df = pd.DataFrame(all_trades).sort_values("exit_time", ascending=False).reset_index(drop=True)
    return perf, curves, port, port_curve, trades_df, open_positions


def build_payload(perf, curves, port, port_curve, trades_df, open_positions):
    order = perf["貨幣對"].tolist()
    step = 2
    cs_idx = sorted(set().union(*[set(c.index) for c in curves.values()]))
    aligned = pd.DataFrame({p: curves[p].reindex(cs_idx) for p in order}, index=cs_idx).iloc[::step]
    dts = aligned.index

    pairs_json = []
    for p in order:
        row = perf[perf["貨幣對"] == p].iloc[0]
        series = [None if pd.isna(v) else round(float(v), 3) for v in aligned[p]]
        pairs_json.append(dict(
            name=p, ann=round(row["年化報酬"], 3), mdd=round(row["MDD"], 3),
            sharpe=round(row["Sharpe"], 3), calmar=round(row["Calmar"], 3),
            total=round(row["總報酬"], 3), win=round(row["勝率"], 2), pf=round(row["獲利因子"], 3),
            trades=int(row["交易數"]), tp=int(row["止盈"]), sl=int(row["停損"]),
            lots=round(row["中位手數"], 4), maxlots=round(row["最大手數"], 4),
            years=round(row["回測年數"], 2), spread=round(float(row["點差"]), 2),
            est=bool(row["成本估計"]), curve=series))

    pc = port_curve.iloc[::2].tolist()

    trades_json = []
    for _, t in trades_df.iterrows():
        trades_json.append(dict(
            pair=t["pair"], side=t["side"],
            entry_time=t["entry_time"].strftime("%Y-%m-%d %H:%M"),
            entry_price=round(float(t["entry_price"]), 5),
            avg_entry=round(float(t["avg_entry"]), 5),
            exit_time=t["exit_time"].strftime("%Y-%m-%d %H:%M"),
            exit_price=round(float(t["exit_price"]), 5),
            reason=t["reason"], layers=int(t["layers"]), lots=round(float(t["lots"]), 4),
            pnl=round(float(t["pnl_usd"]), 2), hold=int(t["hold_bars"]),
        ))

    positions_json = []
    for p in open_positions:
        positions_json.append(dict(
            pair=p["pair"], side=p["side"],
            entry_time=p["entry_time"].strftime("%Y-%m-%d %H:%M"),
            entry_price=round(float(p["entry_price"]), 5),
            avg_entry=round(float(p["avg_entry"]), 5),
            layers=int(p["layers"]), lots=round(float(p["lots"]), 4),
            current_price=round(float(p["current_price"]), 5),
            unrealized_pnl=round(float(p["unrealized_pnl"]), 2),
            hold_bars=int(p["hold_bars"]),
            as_of=p["as_of"].strftime("%Y-%m-%d %H:%M"),
        ))

    payload = dict(
        pairs=pairs_json,
        portfolio=dict(ann=round(port["ann_return_pct"], 2), mdd=round(port["max_dd_pct"], 2),
                       currentDd=round(port["current_dd_pct"], 2),
                       sharpe=round(port["sharpe"], 2), calmar=round(port["calmar"], 2),
                       total=round(port["total_return_pct"], 2), win=round(port["win_rate"], 1),
                       trades=int(port["n_trades"]), avgDep=round(port["avg_deployed_pct"], 1),
                       peakDep=round(port["peak_deployed_pct"], 1), years=round(port["years"], 2),
                       lots=round(port["median_lots"], 4), maxlots=round(port["max_lots"], 4),
                       curve=[round(float(v), 3) for v in pc]),
        trades=trades_json,
        positions=positions_json,
        meta=dict(start=str(dts[0].date()), end=str(dts[-1].date()), n=len(pairs_json),
                  n_trades=len(trades_json),
                  dates=[str(d.date()) for d in dts],
                  generated_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")),
    )
    return payload


def main():
    print("=== 1. 重新抓取真實外匯報價 (TradingView Pepperstone, 1H) ===")
    refresh_price_data()

    print("\n=== 2. 執行回測 (fx_engine_v2, 交易清單, 4H) ===")
    perf, curves, port, port_curve, trades_df, open_positions = run_backtest()
    print(perf.round(3).to_string(index=False))
    print(f"\n組合: 年化 {port['ann_return_pct']:.2f}% MDD {port['max_dd_pct']:.2f}% "
          f"目前回撤 {port['current_dd_pct']:.2f}% Sharpe {port['sharpe']:.2f} Calmar {port['calmar']:.2f}")
    print(f"總交易明細筆數: {len(trades_df)}")
    print(f"目前持倉部位: {len(open_positions)} 檔")
    for p in open_positions:
        print(f"  {p['pair']} {p['side']} 第{p['layers']}層 均價{p['avg_entry']:.5f} "
              f"現價{p['current_price']:.5f} 未實現損益 ${p['unrealized_pnl']:+.2f}")

    print("\n=== 3. 產生報告資料 ===")
    payload = build_payload(perf, curves, port, port_curve, trades_df, open_positions)

    os.makedirs(DOCS_DIR, exist_ok=True)
    with open(os.path.join(DOCS_DIR, "data.json"), "w", encoding="utf-8") as f:
        json.dump(payload, f, separators=(",", ":"), ensure_ascii=False)

    tpl_path = os.path.join(DOCS_DIR, "report_template.html")
    with open(tpl_path, encoding="utf-8") as f:
        tpl = f.read()
    data_str = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    out = tpl.replace("/*__DATA__*/null", data_str)
    with open(os.path.join(DOCS_DIR, "index.html"), "w", encoding="utf-8") as f:
        f.write(out)

    perf.round(4).to_csv(os.path.join(DOCS_DIR, "fx_top8_performance.csv"),
                         index=False, encoding="utf-8-sig")
    trades_df.to_csv(os.path.join(DOCS_DIR, "fx_trade_log.csv"), index=False, encoding="utf-8-sig")

    print(f"\n✅ 已產生 docs/index.html ({os.path.getsize(os.path.join(DOCS_DIR, 'index.html')):,} bytes)")
    print(f"   更新時間: {payload['meta']['generated_at']}")


if __name__ == "__main__":
    main()
