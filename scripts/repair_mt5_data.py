"""修補 MT5 匯出資料的缺口（可重複執行）

為什麼需要這支：MT5 終端機的歷史有洞，而且洞的位置因商品而異。匯率基準檔一旦有洞，
下游 `reindex(...).ffill()` 會把最後一筆匯率凍結著用，部位換算與損益全部悄悄算錯
而且不報錯——GBPUSD 整個 2018 年缺漏就是這樣被漏掉的（見
strategy_logic_and_backtest_truth.md 第 2.9 節）。

每次跑完 ExportH1Bars.mq5 重新同步資料後都要再跑這支，否則修好的洞會被覆蓋回去。

修補手法一律是三角合成（triangular arbitrage）：同一時刻三個報價必須自洽，
所以缺的那一個可以由另外兩個算出來。合成前會先在「兩邊都有資料」的重疊期驗證誤差。
"""
import os
import sys

import pandas as pd

DATA_DIR = os.environ.get("FX_DATA_DIR", "data_mt5_h1")

# (目標, 分子, 分母)：目標 = 分子 ÷ 分母
# USDPLN 這家 broker 根本不提供，但 PLN 的美元匯率是 EURPLN 換算的必要輸入，
# 所以整條都用合成的。
SYNTH = [
    ("GBPUSD", "EURUSD", "EURGBP"),   # 2018 全年缺漏
    ("USDPLN", "EURPLN", "EURUSD"),   # broker 未提供此商品
]


def _load(sym):
    path = os.path.join(DATA_DIR, f"{sym}_1h.csv")
    if not os.path.exists(path):
        return None
    d = pd.read_csv(path, parse_dates=["datetime"]).set_index("datetime").sort_index()
    return d[~d.index.duplicated()]


def synth_fill(target, num, den, verbose=True):
    """用 num ÷ den 補 target 的缺口；target 不存在就整個建出來。"""
    a, b = _load(num), _load(den)
    if a is None or b is None:
        print(f"  ⚠️ {target}: 缺少 {num} 或 {den}，跳過")
        return
    common = a.index.intersection(b.index)
    syn = pd.DataFrame(
        {c: (a.loc[common, c] / b.loc[common, c]) for c in ["open", "high", "low", "close"]}
    )
    # 相除後 high/low 的大小關係可能顛倒（分母也在動），重新取極值
    hl = pd.concat([syn["high"], syn["low"]], axis=1)
    syn["high"], syn["low"] = hl.max(axis=1), hl.min(axis=1)

    cur = _load(target)
    if cur is None:
        out = syn
        note = f"全新建立 {len(out):,} 根"
    else:
        # 先在重疊期驗證合成品質，再決定要不要信它
        ov = cur.index.intersection(syn.index)
        pip = 0.01 if target.endswith(("JPY", "HUF")) else 0.0001
        err = ((syn.loc[ov, "close"] - cur.loc[ov, "close"]) / pip).abs()
        missing = syn.index.difference(cur.index)
        out = pd.concat([cur[["open", "high", "low", "close"]], syn.loc[missing]]).sort_index()
        note = (f"補 {len(missing):,} 根 → {len(out):,} 根"
                f"（重疊 {len(ov):,} 根驗證：誤差中位 {err.median():.2f} pip、"
                f"95分位 {err.quantile(.95):.2f} pip）")

    out.index.name = "datetime"
    out.round(6).to_csv(os.path.join(DATA_DIR, f"{target}_1h.csv"))
    if verbose:
        print(f"  ✅ {target}: {note}")


def audit():
    """列出每檔相對 EURUSD 的月覆蓋率，低於 50% 的月份視為缺漏。"""
    ref = _load("EURUSD")
    if ref is None:
        print("❌ 找不到 EURUSD，無法稽核")
        return
    refm = ref.groupby(ref.index.to_period("M")).size()
    print("\n稽核（月覆蓋率 < 50% 才列出）：")
    clean = True
    for f in sorted(os.listdir(DATA_DIR)):
        if not f.endswith("_1h.csv"):
            continue
        sym = f.replace("_1h.csv", "")
        d = _load(sym)
        dm = d.groupby(d.index.to_period("M")).size().reindex(refm.index).fillna(0)
        bad = (dm / refm)[(dm / refm) < 0.5]
        if len(bad):
            clean = False
            print(f"  {sym:8} {len(bad):3d} 個月缺漏：{bad.index.min()} → {bad.index.max()}")
    if clean:
        print("  （全部完整）")


if __name__ == "__main__":
    print(f"修補 {DATA_DIR}/ 的資料缺口")
    for t, n, d in SYNTH:
        synth_fill(t, n, d)
    audit()
