"""
可交易貨幣對完整績效報告 + 權益曲線

universe 篩選規則 (結構性條件，非依回測績效排名挑選)：
  - 排除 ATR/點差比 < 10 者 (訊號幅度相對摩擦成本太小)：EURGBP, NZDUSD
  - 排除有長期政策干預壓抑波動者：EURCHF

策略設定 = 前一輪驗證後的建議版本：
  md 原始狀態機 + 雙向交易 + 較近止盈 min(EMA50, 均價±1ATR) + 保留 4ATR 硬停損
  完整成本：真實點差 + 佣金 + 隔夜利息(週三三倍)
"""

import os
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter

# 中文字型 (macOS)，否則 CJK 會變成方框
matplotlib.rcParams["font.sans-serif"] = ["PingFang HK", "Heiti TC", "Arial Unicode MS", "DejaVu Sans"]
matplotlib.rcParams["axes.unicode_minus"] = False

from fx_grid_optimize import (
    load_costs, pair_costs, load_4h, add_indicators, run_engine,
    BASE_CFG, DATA_DIR, INITIAL_CAPITAL,
)
from fx_portfolio_sim import run_portfolio

EXCLUDE = {"EURGBP", "NZDUSD", "EURCHF"}   # 見上方篩選規則
CFG = {**BASE_CFG, "direction": "both", "tp_mode": "nearer"}

# dataviz 參考色票
SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK2 = "#52514e"
GRID = "#e6e5e1"
BLUE = "#2a78d6"
ORANGE = "#eb6834"
RED = "#e34948"


def style_axes(ax):
    ax.set_facecolor(SURFACE)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color(GRID)
        ax.spines[s].set_linewidth(0.8)
    ax.tick_params(colors=INK2, labelsize=8, length=3, width=0.8)
    ax.grid(True, color=GRID, linewidth=0.7, alpha=0.9)
    ax.set_axisbelow(True)


def main():
    cost_df = load_costs()
    all_pairs = sorted(f.replace("_1h.csv", "") for f in os.listdir(DATA_DIR) if f.endswith("_1h.csv"))

    data = {}
    for p in all_pairs:
        b = add_indicators(load_4h(p))
        if len(b) >= 200:
            data[p] = b

    universe = [p for p in data if p != "XAUUSD" and p not in EXCLUDE]
    universe.sort()

    # ---------------- 逐檔回測 ----------------
    rows, curves = [], {}
    for p in universe:
        r = run_engine(data[p], CFG, pair_costs(cost_df, p))
        curves[p] = pd.Series((r["equity"].to_numpy() / INITIAL_CAPITAL - 1.0) * 100.0,
                              index=data[p]["datetime"])
        rows.append(dict(
            貨幣對=p,
            年化報酬=r["ann_return_pct"],
            MDD=r["max_dd_pct"],
            Sharpe=r["sharpe"],
            Calmar=r["calmar"],
            總報酬=r["total_return_pct"],
            勝率=r["win_rate"],
            獲利因子=r["profit_factor"],
            交易數=r["n_trades"],
            止盈=r["n_tp"],
            停損=r["n_sl"],
            平均持倉根數=r["avg_hold_bars"],
            資金在場率=r["exposure_pct"],
            回測年數=r["years"],
        ))
    perf = pd.DataFrame(rows).sort_values("Calmar", ascending=False).reset_index(drop=True)

    # ---------------- 組合回測 ----------------
    port = run_portfolio(data, universe, cost_df, CFG)
    port_curve = pd.Series((port["equity"].to_numpy() / INITIAL_CAPITAL - 1.0) * 100.0)

    pd.set_option("display.width", 250)
    print("=" * 118)
    print(f"【可交易貨幣對績效總表】{len(universe)} 檔 · 4H · 雙向+較近止盈+4ATR停損 · 各自獨立 $25,000 本金")
    print("=" * 118)
    print(perf.round(3).to_string(index=False))

    print("\n" + "=" * 118)
    print("【統計摘要】")
    print("=" * 118)
    for col, unit, lower_is_better in [("年化報酬", "%", False), ("MDD", "%", True),
                                       ("Sharpe", "", False), ("Calmar", "", False)]:
        s = perf[col]
        best_i, worst_i = (s.idxmin(), s.idxmax()) if lower_is_better else (s.idxmax(), s.idxmin())
        print(f"  {col:8s} 平均 {s.mean():7.3f}{unit}   中位 {s.median():7.3f}{unit}   "
              f"最佳 {s[best_i]:7.3f}{unit} ({perf.loc[best_i, '貨幣對']})   "
              f"最差 {s[worst_i]:7.3f}{unit} ({perf.loc[worst_i, '貨幣對']})")
    print(f"  獲利檔數 {(perf['總報酬'] > 0).sum()}/{len(perf)}   平均勝率 {perf['勝率'].mean():.1f}%   "
          f"平均資金在場率 {perf['資金在場率'].mean():.1f}%")

    print("\n" + "=" * 118)
    print(f"【單一 $25,000 帳戶同時交易全部 {len(universe)} 檔】")
    print("=" * 118)
    print(f"  年化報酬 {port['ann_return_pct']:.2f}%   MDD {port['max_dd_pct']:.2f}%   "
          f"Sharpe {port['sharpe']:.2f}   Calmar {port['calmar']:.2f}")
    print(f"  總報酬 {port['total_return_pct']:.2f}% ({port['years']:.2f}年)   勝率 {port['win_rate']:.1f}%   "
          f"交易數 {port['n_trades']}")
    print(f"  平均動用資金 {port['avg_deployed_pct']:.1f}%   峰值動用資金 {port['peak_deployed_pct']:.1f}%")

    perf.round(4).to_csv("fx_tradeable_performance.csv", index=False, encoding="utf-8-sig")

    # ================= 圖 1：小倍數權益曲線 =================
    n = len(universe)
    ncol = 5
    nrow = int(np.ceil(n / ncol))
    fig, axes = plt.subplots(nrow, ncol, figsize=(15, 2.5 * nrow), dpi=200,
                             sharex=False, sharey=True, facecolor=SURFACE)
    axes = np.atleast_1d(axes).ravel()

    ymin = min(c.min() for c in curves.values())
    ymax = max(c.max() for c in curves.values())
    pad = (ymax - ymin) * 0.12

    order = perf["貨幣對"].tolist()
    for ax, p in zip(axes, order):
        c = curves[p]
        style_axes(ax)
        ax.axhline(0, color=INK2, linewidth=0.8, alpha=0.5)
        ax.plot(c.index, c.values, color=BLUE, linewidth=1.4)
        ax.fill_between(c.index, 0, c.values, color=BLUE, alpha=0.10, linewidth=0)
        row = perf[perf["貨幣對"] == p].iloc[0]
        ax.set_title(p, fontsize=11, color=INK, fontweight="bold", pad=8, loc="left")
        ax.text(0.02, 0.90,
                f"年化 {row['年化報酬']:.2f}%  MDD {row['MDD']:.2f}%",
                transform=ax.transAxes, fontsize=7.5, color=INK2, va="top")
        ax.text(0.02, 0.79,
                f"Sharpe {row['Sharpe']:.2f}  Calmar {row['Calmar']:.2f}",
                transform=ax.transAxes, fontsize=7.5, color=INK2, va="top")
        ax.set_ylim(ymin - pad, ymax + pad)
        ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:.0f}%"))
        for lab in ax.get_xticklabels():
            lab.set_rotation(0)
        ax.xaxis.set_major_locator(plt.MaxNLocator(3))
    for ax in axes[len(order):]:
        ax.set_visible(False)

    fig.text(0.01, 0.985, "可交易貨幣對權益曲線 · 4H 自適應通道網格 (雙向 + 較近止盈 + 4ATR 硬停損)",
             fontsize=14, fontweight="bold", color=INK, ha="left", va="top")
    fig.text(0.01, 0.958,
             f"每檔各自獨立 $25,000 本金 · 含真實點差/佣金/隔夜利息 · 樣本 {perf['回測年數'].max():.1f} 年 · "
             f"共用縱軸刻度 · 依 Calmar 由高至低排列",
             fontsize=9, color=INK2, ha="left", va="top")
    fig.tight_layout(rect=[0, 0, 1, 0.935])
    fig.savefig("fx_tradeable_equity_curves.png", facecolor=SURFACE)
    print("\n--> 圖表已存至 fx_tradeable_equity_curves.png")

    # ================= 圖 2：組合曲線 + 風險調整後報酬 =================
    fig2 = plt.figure(figsize=(15, 6.2), dpi=200, facecolor=SURFACE)
    gs = fig2.add_gridspec(1, 2, width_ratios=[1.5, 1], wspace=0.22)

    ax1 = fig2.add_subplot(gs[0, 0])
    style_axes(ax1)
    ax1.axhline(0, color=INK2, linewidth=0.8, alpha=0.5)
    ax1.plot(range(len(port_curve)), port_curve.values, color=BLUE, linewidth=1.8)
    ax1.fill_between(range(len(port_curve)), 0, port_curve.values, color=BLUE, alpha=0.12, linewidth=0)

    peak = port_curve.cummax()
    dd_idx = ((port_curve - peak)).idxmin()
    ax1.plot([dd_idx], [port_curve.iloc[dd_idx]], marker="o", markersize=8,
             color=RED, markeredgecolor=SURFACE, markeredgewidth=2, zorder=5)
    ax1.annotate(f"最大回撤 -{port['max_dd_pct']:.2f}%",
                 xy=(dd_idx, port_curve.iloc[dd_idx]), xytext=(12, -18),
                 textcoords="offset points", fontsize=9, color=RED)
    ax1.annotate(f"總報酬 +{port['total_return_pct']:.2f}%",
                 xy=(len(port_curve) - 1, port_curve.iloc[-1]), xytext=(-4, 10),
                 textcoords="offset points", fontsize=10, color=INK, fontweight="bold", ha="right")
    ax1.set_title(f"單一 $25,000 帳戶同時交易 {len(universe)} 檔 · 共用資金池",
                  fontsize=12, color=INK, fontweight="bold", loc="left", pad=30)
    ax1.text(0, 1.015,
             f"年化 {port['ann_return_pct']:.2f}%   MDD {port['max_dd_pct']:.2f}%   "
             f"Sharpe {port['sharpe']:.2f}   Calmar {port['calmar']:.2f}   "
             f"峰值動用資金 {port['peak_deployed_pct']:.0f}%",
             transform=ax1.transAxes, fontsize=9, color=INK2)
    ax1.set_xlabel("4H K 棒", fontsize=9, color=INK2)
    ax1.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:.0f}%"))

    ax2 = fig2.add_subplot(gs[0, 1])
    style_axes(ax2)
    ax2.grid(False, axis="y")
    d = perf.sort_values("Calmar")
    ypos = np.arange(len(d))
    colors = [BLUE if v >= 0 else RED for v in d["Calmar"]]
    ax2.barh(ypos, d["Calmar"], color=colors, height=0.68)
    ax2.set_yticks(ypos)
    ax2.set_yticklabels(d["貨幣對"], fontsize=8.5, color=INK)
    ax2.axvline(0, color=INK2, linewidth=0.8, alpha=0.5)
    span = d["Calmar"].max()
    for y, v, s in zip(ypos, d["Calmar"], d["Sharpe"]):
        # 標籤一律置於零線右側，避免負值長條的標籤壓到左側的貨幣對名稱
        ax2.text(max(v, 0) + span * 0.015, y, f"{v:.2f}   Sharpe {s:.2f}",
                 va="center", ha="left", fontsize=7.5, color=INK2)
    ax2.set_xlim(min(0, d["Calmar"].min() * 1.6) - span * 0.02, span * 1.45)
    ax2.set_title("風險調整後報酬 · Calmar (年化報酬 ÷ 最大回撤)",
                  fontsize=12, color=INK, fontweight="bold", loc="left", pad=30)
    ax2.set_xlabel("Calmar Ratio", fontsize=9, color=INK2)

    fig2.tight_layout()
    fig2.savefig("fx_tradeable_portfolio.png", facecolor=SURFACE)
    print("--> 圖表已存至 fx_tradeable_portfolio.png")

    # 供 artifact 使用的曲線資料
    export = {p: curves[p] for p in order}
    idx = sorted(set().union(*[set(c.index) for c in export.values()]))
    aligned = pd.DataFrame({p: c.reindex(idx).ffill() for p, c in export.items()}, index=idx)
    aligned.index.name = "datetime"
    aligned.iloc[::6].round(4).to_csv("fx_tradeable_curves_export.csv", encoding="utf-8-sig")
    pd.Series(port_curve.values[::6]).round(4).to_csv("fx_portfolio_curve_export.csv",
                                                      index=False, header=["portfolio_pct"])
    print("--> 曲線資料已存至 fx_tradeable_curves_export.csv / fx_portfolio_curve_export.csv")


if __name__ == "__main__":
    main()
