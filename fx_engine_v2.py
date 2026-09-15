"""
外匯網格引擎 v2：修正跨幣別部位計算

v1 的問題：`qty = 美元金額 / 報價` 只有在「報價的計價貨幣是 USD」時，qty 才是真實可下單的
基礎貨幣單位數。對 CADJPY 這類日圓計價的交叉盤，這個公式算出來的 qty 小了約 150 倍，
無法直接換算成 MT5 的手數。

v2 的修正：
  1. 部位大小改為 qty = 目標美元名目 / (該基礎貨幣的美元匯率)，得到真正的基礎貨幣單位數
  2. 損益改為 P&L(USD) = qty × 價格變動 × (計價貨幣的美元匯率)
  3. 手數 lots = qty / 合約規模，直接可用於 MT5 下單
  4. 隔夜利息改用「手數 × $/手/日」精確計算（v1 用名目/100000 近似）
  5. 佣金改用「手數 × $3/手/邊」精確計算（v1 用 bps 近似）

注意：v1 的百分比報酬其實是對的（因為 P&L/投入金額 = 價格變動百分比，與報價幣別無關），
v2 主要修正的是「可下單手數」與「隔夜利息/佣金的絕對金額」。
"""

import os
import numpy as np
import pandas as pd

# 預設用 TradingView 抓的資料（GitHub Actions 每小時更新用，連不到使用者的 MT5）。
# 設環境變數 FX_DATA_DIR=data_mt5_h1 可切換到使用者 MT5 匯出的 10 年 H1，
# 那份資料時間戳就是 broker 伺服器時間、夏令時間由 broker 處理，不需外部換算。
DATA_DIR = os.environ.get("FX_DATA_DIR", "data_fx_1h")
REAL_COST_TABLE = "mt5_forex_real_costs.csv"

INITIAL_CAPITAL = 25000.0
CONTRACT_SIZE = 100000.0          # 外匯標準手 = 100,000 基礎貨幣單位
COMMISSION_PER_LOT_SIDE = 3.0     # 單邊佣金 $3/手（來回 $6）

# broker 00:00（= 21:00 UTC）是換日結算、全日流動性最低的一小時，點差會擴到
# 平常的十幾倍。實測 TradingView Pepperstone 的 1H 資料在這一根有系統性假跳空：
# 開盤對前根收盤的跳空中位數是其他時段的 50~80 倍（AUDCHF 8.2 pips vs 0.1 pips，
# GBPNZD 10.0 vs 0.2），最大到 254 pips。用那根的 high/low 觸發進出場會憑空
# 生出實盤拿不到的止盈——實測有 80 筆止盈只有這根尖刺碰得到，佔全期淨利 6.7%，
# 而且全是止盈、零停損（停損距離 4×ATR 太遠，尖刺只夠灌水不夠傷人）。
# 因此重採樣成 4H 前先把這一根整個丟掉：00:00-04:00 那根改由 01/02/03 三根組成。
EXCLUDE_ROLLOVER_HOUR = 0         # 設為 None 可關閉此過濾

# 各貨幣對的 (基礎貨幣, 計價貨幣) —— G8 貨幣兩兩組合，共 28 檔 (major+minor+cross全覆蓋)
PAIR_CCY = {
    # 7 檔 major
    "EURUSD": ("EUR", "USD"), "GBPUSD": ("GBP", "USD"), "USDJPY": ("USD", "JPY"),
    "USDCHF": ("USD", "CHF"), "USDCAD": ("USD", "CAD"), "AUDUSD": ("AUD", "USD"),
    "NZDUSD": ("NZD", "USD"),
    # EUR 交叉盤
    "EURGBP": ("EUR", "GBP"), "EURJPY": ("EUR", "JPY"), "EURCHF": ("EUR", "CHF"),
    "EURCAD": ("EUR", "CAD"), "EURAUD": ("EUR", "AUD"), "EURNZD": ("EUR", "NZD"),
    # GBP 交叉盤
    "GBPJPY": ("GBP", "JPY"), "GBPCHF": ("GBP", "CHF"), "GBPCAD": ("GBP", "CAD"),
    "GBPAUD": ("GBP", "AUD"), "GBPNZD": ("GBP", "NZD"),
    # JPY 交叉盤 (JPY當計價)
    "CHFJPY": ("CHF", "JPY"), "CADJPY": ("CAD", "JPY"), "AUDJPY": ("AUD", "JPY"),
    "NZDJPY": ("NZD", "JPY"),
    # 其餘商品貨幣交叉盤
    "CADCHF": ("CAD", "CHF"), "AUDCHF": ("AUD", "CHF"), "NZDCHF": ("NZD", "CHF"),
    "AUDCAD": ("AUD", "CAD"), "NZDCAD": ("NZD", "CAD"), "AUDNZD": ("AUD", "NZD"),
}

# 非 G8 標的：目的是提高「有效獨立賭注數」。現有 12 檔全由 8 個貨幣兩兩組成，
# 策略報酬平均相關 0.140、有效獨立賭注只有 8.8 個（名目 12）。
# 下列 11 檔帶進 SGD/CNH/NOK/SEK/PLN/CZK/HUF 共 7 個全新貨幣。
# 挑選邏輯（尚待實測驗證）：
#   · SGD 由 MAS 用政策區間管理、CNH 有中間價機制 → 結構上偏均值回歸
#   · NOK/SEK 對 EUR 長期區間震盪
#   · PLN/CZK/HUF 對 EUR 相對穩定（歐盟外圍但與歐元區連動）
# 刻意不納入 TRY/ZAR/MXN：有持續性貶值趨勢（carry），均值回歸會一路接刀，
# 跟已實測為負的黃金（年化 −0.48%、ADX>38 佔 15.1%）是同一種失敗模式。
NON_G8_PAIRS = {
    "USDSGD": ("USD", "SGD"), "USDCNH": ("USD", "CNH"),
    "USDNOK": ("USD", "NOK"), "EURNOK": ("EUR", "NOK"), "USDSEK": ("USD", "SEK"),
    "EURPLN": ("EUR", "PLN"), "USDPLN": ("USD", "PLN"),
    "EURCZK": ("EUR", "CZK"), "USDCZK": ("USD", "CZK"),
    "EURHUF": ("EUR", "HUF"), "USDHUF": ("USD", "HUF"),
}
PAIR_CCY.update(NON_G8_PAIRS)
JPY_QUOTED = {p for p, (b, q) in PAIR_CCY.items() if q == "JPY"}
# HUF 跟 JPY 一樣是「大數字報價」（報價約 380、小數 2~3 位），pip 定義同為 0.01
BIG_QUOTE = JPY_QUOTED | {p for p, (b, q) in PAIR_CCY.items() if q == "HUF"}
ALL_28_PAIRS = [p for p in PAIR_CCY if p not in NON_G8_PAIRS]
ALL_PAIRS = list(PAIR_CCY.keys())

CFG = dict(
    base_order=1500.0,     # 首單目標名目金額 (USD)
    size_mult=1.2,         # 加碼金額倍數（每層固定 1500×1.2 = $1,800，非複利）
    max_layers=4,
    channel_k=2.0,
    dca_step=1.5,
    stop_atr=4.0,
    tp_atr=1.0,            # 較近止盈：min(EMA50, 均價 + 1×ATR)
    # 波動度目標化：把首單名目乘上 (ATR長期中位數 / 當下ATR)，讓「一次停損虧多少美元」
    # 在不同波動環境下大致固定。不開啟時（None）是固定美元名目，ATR 膨脹時每次停損
    # 的絕對金額會等比放大——2020 年的平均單筆虧損 −$65.68 就是這樣來的，
    # 對照 2025 年的 −$37.83，停損次數其實差不多（102 vs 67）。
    vol_target=None,       # 例：1.0 = 完全目標化；None = 關閉
    vol_clip=(0.4, 1.5),   # 縮放倍數的上下限，避免極端值把部位放到失控或縮到沒有
    # ADX 趨勢強度過濾：均值回歸最怕在強趨勢裡一路接刀。
    # adx_max=None 關閉；設數值則 ADX 高於它時禁止動作。
    # adx_block_dca=True 時連加碼一起擋（加碼才是趨勢盤裡真正致命的部分）。
    adx_max=None,
    adx_block_dca=True,
    # adx_directional=True 時，ADX 超標只擋「逆勢方向」而非兩邊都擋：
    # 強下跌趨勢中買回檔（接刀）才是殺手，賣反彈其實是順勢的。
    # 2024-07 日圓套利平倉就是這種情形——ADX 平均 33.6、37% 時間 >38，
    # 但 ATR 完全正常（0.98倍），純趨勢事件。
    adx_directional=False,
)


# ----------------------------------------------------------------- 資料
def load_4h(pair):
    """讀取 data_fx_1h/{pair}_1h.csv（來源：TradingView 的 Pepperstone 報價，
    由 fx_data_pepperstone.py 抓取並已轉換成 MT5 broker 時間）。
    這裡刻意不加 tz（不是 UTC），因為整條 pipeline 現在統一用「broker 時間」
    當作唯一時鐘——resample("4h") 切出來的 K棒邊界會跟 MT5 內建 PERIOD_H4 對齊。"""
    df = pd.read_csv(os.path.join(DATA_DIR, f"{pair}_1h.csv"))
    df["datetime"] = pd.to_datetime(df["datetime"])
    df = df.set_index("datetime").sort_index()
    if EXCLUDE_ROLLOVER_HOUR is not None:
        df = df[df.index.hour != EXCLUDE_ROLLOVER_HOUR]
    out = (df.resample("4h", label="left", closed="left")
             .agg({"open": "first", "high": "max", "low": "min", "close": "last"})
             .dropna())
    return out


def _adx(d, period=14):
    """Wilder 的 ADX。衡量趨勢強度（不含方向）：>25 通常視為趨勢盤、<20 為盤整盤。
    均值回歸策略最怕的就是在強趨勢裡一路接刀，所以拿它當進場過濾器。"""
    up = d["high"].diff()
    dn = -d["low"].diff()
    plus_dm = np.where((up > dn) & (up > 0), up, 0.0)
    minus_dm = np.where((dn > up) & (dn > 0), dn, 0.0)
    pc = d["close"].shift(1)
    tr = np.maximum(d["high"] - d["low"],
                    np.maximum((d["high"] - pc).abs(), (d["low"] - pc).abs()))
    a = 1.0 / period                      # Wilder 平滑等價於 alpha = 1/period 的 EMA
    atr_w = pd.Series(tr, index=d.index).ewm(alpha=a, adjust=False).mean()
    pdi = 100 * pd.Series(plus_dm, index=d.index).ewm(alpha=a, adjust=False).mean() / atr_w
    mdi = 100 * pd.Series(minus_dm, index=d.index).ewm(alpha=a, adjust=False).mean() / atr_w
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    return dx.ewm(alpha=a, adjust=False).mean(), pdi, mdi


def add_indicators(bars):
    d = bars.copy()
    pc = d["close"].shift(1)
    d["tr"] = np.maximum(d["high"] - d["low"],
                         np.maximum((d["high"] - pc).abs(), (d["low"] - pc).abs()))
    d["atr"] = d["tr"].rolling(20).mean()
    d["ema50"] = d["close"].ewm(span=50, adjust=False).mean()
    # 一律往後推一根，讓「第 i 根的訊號」只用得到第 i-1 根收盤時就已知的資訊。
    # 這是為了跟實盤 EA 對齊：EA 呼叫 CopyBuffer(handle, 0, 1, 1, buf)，shift=1
    # 代表前一根「已收盤」的 K 棒，不會用到還在跑的當根。舊版直接用當根的
    # EMA/ATR（含當根收盤價）去比對當根的 high/low，是輕微的未來函數。
    _ad, _pdi, _mdi = _adx(d)
    d["adx"] = _ad.shift(1)              # 跟 EMA/ATR 一樣只用前一根已收盤的值
    # 趨勢方向：+DI > -DI 為上升、反之為下降。用來做「只擋逆勢那一邊」。
    d["di_up"] = (_pdi > _mdi).shift(1).astype(float)
    d["atr"] = d["atr"].shift(1)
    d["ema50"] = d["ema50"].shift(1)
    return d.dropna(subset=["atr", "ema50"])


def build_usd_rates(need_ccys):
    """回傳 {貨幣: Series(該貨幣 1 單位值多少美元)}，索引為 4H 時間。"""
    rates = {"USD": None}   # USD 恆為 1.0，後面特別處理
    # (報價對, 是否需要取倒數)。取倒數代表該貨幣是計價方，例如 USDCAD 報價是
    # 「1 USD = 幾 CAD」，要換成「1 CAD = 幾 USD」得取倒數。
    direct = {"EUR": ("EURUSD", False), "GBP": ("GBPUSD", False),
              "AUD": ("AUDUSD", False), "NZD": ("NZDUSD", False),
              "CAD": ("USDCAD", True), "CHF": ("USDCHF", True),
              "JPY": ("USDJPY", True),
              # 非 G8：全部都有 USDxxx 報價，所以一律取倒數
              "SGD": ("USDSGD", True), "CNH": ("USDCNH", True),
              "NOK": ("USDNOK", True), "SEK": ("USDSEK", True),
              "PLN": ("USDPLN", True), "CZK": ("USDCZK", True),
              "HUF": ("USDHUF", True)}
    for ccy in need_ccys:
        if ccy == "USD":
            continue
        pair, invert = direct[ccy]
        s = load_4h(pair)["close"]
        # 匯率序列的缺口是靜默殺手：下游 reindex(...).ffill() 會把最後一筆值
        # 一路凍結著用，部位換算與損益全部悄悄算錯，而且不會報錯。
        # （實例：MT5 匯出的 GBPUSD 整個 2018 年缺漏，導致所有 GBP 交叉盤
        #   在那一年用 2017 年底的匯率換算；walk-forward 視窗完全落在缺口內時
        #   則直接變成 NaN。已用 EURUSD ÷ EURGBP 三角合成補回。）
        gaps = s.index.to_series().diff()
        big = gaps[gaps > pd.Timedelta(days=5)]
        if len(big):
            worst = big.max()
            print(f"  ⚠️ 匯率基準 {pair} 有 {len(big)} 處超過 5 天的缺口"
                  f"（最大 {worst.days} 天，最近一處在 {big.idxmax().date()}）"
                  f"——{ccy} 相關貨幣對的換算可能失真")
        rates[ccy] = (1.0 / s) if invert else s
    return rates


# ----------------------------------------------------------------- 引擎
def run_engine_v2(bars, pair, costs, rates, cfg=CFG, initial_capital=INITIAL_CAPITAL,
                  capital_scale=1.0):
    """單一貨幣對回測。qty 為真實基礎貨幣單位數，損益經計價貨幣換算為美元。"""
    base_ccy, quote_ccy = PAIR_CCY[pair]
    idx = bars.index

    # 對齊匯率序列（USD 恆為 1.0）
    def rate_series(ccy):
        if ccy == "USD":
            return pd.Series(1.0, index=idx)
        return rates[ccy].reindex(idx).ffill().bfill()

    r_base = rate_series(base_ccy).to_numpy(float)     # 1 單位基礎貨幣 = 幾美元
    r_quote = rate_series(quote_ccy).to_numpy(float)   # 1 單位計價貨幣 = 幾美元

    high = bars["high"].to_numpy(float)
    low = bars["low"].to_numpy(float)
    close = bars["close"].to_numpy(float)
    atr_a = bars["atr"].to_numpy(float)
    ema_a = bars["ema50"].to_numpy(float)
    adx_a = (bars["adx"].to_numpy(float) if "adx" in bars
             else np.zeros(len(bars)))
    adx_max = cfg.get("adx_max")
    adx_block_dca = cfg.get("adx_block_dca", True)
    adx_dir = cfg.get("adx_directional", False)
    di_up_a = (bars["di_up"].to_numpy(float) if "di_up" in bars else np.ones(len(bars)))
    dates = idx.date
    weekdays = idx.weekday

    half_spread = costs["spread_pips"] * costs["pip_size"] / 2.0   # 半點差（價格單位）
    swap_long_per_lot = costs["swap_long"]      # $/手/日
    swap_short_per_lot = costs["swap_short"]

    base_order = cfg["base_order"] * capital_scale
    dca_order = base_order * cfg["size_mult"]
    # 波動度縮放係數：逐根計算，用「該根當下可見的 ATR」對比其長期中位數。
    # rolling(500).median() 約等於過去 250 天的水準，且只用到過去資料。
    if cfg.get("vol_target"):
        atr_ref = bars["atr"].rolling(500, min_periods=100).median()
        vs = (atr_ref / bars["atr"]).clip(*cfg["vol_clip"]).fillna(1.0).to_numpy(float)
    else:
        vs = np.ones(len(bars))

    cash = initial_capital * capital_scale
    side = 0
    qty = 0.0            # 基礎貨幣單位數
    layer = 0
    avg_entry = 0.0
    invested_usd = 0.0   # 已投入的美元名目（含成本）
    last_date = None

    equity = np.empty(len(bars))
    # 同一根K棒的「盤中最不利價」計價：多單用 low、空單用 high。
    # 收盤價計價會低估回撤——K棒收盤前浮虧可能深得多，而那才是盯盤時真正看到的數字。
    # 反過來說這條曲線是保守上界（假設所有部位在同一瞬間都摸到各自最差點），
    # 真實體感落在兩者之間。
    equity_adv = np.empty(len(bars))
    trades = []
    trade_log = []       # 每筆完整進出場明細
    lots_log = []
    entry_time, entry_l1_price, entry_i = None, 0.0, 0
    n = len(bars)

    for i in range(n):
        c, h, l = close[i], high[i], low[i]
        atr, ema = atr_a[i], ema_a[i]
        rb, rq = r_base[i], r_quote[i]

        # 隔夜利息：以真實手數計算
        if last_date is not None and dates[i] != last_date and side != 0:
            mult = 3.0 if weekdays[i] == 2 else 1.0
            lots = qty / CONTRACT_SIZE
            cash += lots * (swap_long_per_lot if side > 0 else swap_short_per_lot) * mult
        last_date = dates[i]

        lower = ema - cfg["channel_k"] * atr
        upper = ema + cfg["channel_k"] * atr

        def open_leg(target_usd, exec_price, direction):
            """依目標美元名目換算真實基礎貨幣單位數，回傳 (qty, 成本USD)"""
            q = target_usd / rb                       # ← 修正核心：用基礎貨幣美元匯率換算
            lots = q / CONTRACT_SIZE
            comm = lots * COMMISSION_PER_LOT_SIDE
            return q, comm, lots

        # ADX 過濾：趨勢太強時不開新倉（可選擇是否連加碼一起擋）
        hot = bool(adx_max) and np.isfinite(adx_a[i]) and adx_a[i] > adx_max
        if not hot:
            long_ok = short_ok = True
        elif adx_dir:
            # 只擋逆勢那一邊：
            #   上升趨勢（di_up=1）→ 做多是順勢放行、做空是逆勢擋掉
            #   下降趨勢（di_up=0）→ 做空是順勢放行、做多是接刀擋掉
            long_ok  = (di_up_a[i] >= 0.5)
            short_ok = (di_up_a[i] < 0.5)
        else:
            long_ok = short_ok = False
        adx_ok = long_ok or short_ok

        if side == 0:
            if l <= lower and long_ok:
                px = min(c, lower) + half_spread
                q, comm, lots = open_leg(base_order * vs[i], px, 1)
                notional_usd = q * rb
                if cash >= notional_usd * 0.05 + comm:      # 需有足夠保證金(5%)與佣金
                    cash -= comm
                    side, qty, layer, avg_entry = 1, q, 1, px
                    invested_usd = notional_usd
                    lots_log.append(lots)
                    entry_time, entry_l1_price, entry_i = idx[i], px, i
            elif h >= upper and short_ok:
                px = max(c, upper) - half_spread
                q, comm, lots = open_leg(base_order * vs[i], px, -1)
                notional_usd = q * rb
                if cash >= notional_usd * 0.05 + comm:
                    cash -= comm
                    side, qty, layer, avg_entry = -1, q, 1, px
                    invested_usd = notional_usd
                    lots_log.append(lots)
                    entry_time, entry_l1_price, entry_i = idx[i], px, i
        else:
            closed = False
            if side > 0:
                tp = min(ema, avg_entry + cfg["tp_atr"] * atr)
                sl = avg_entry - cfg["stop_atr"] * atr
                exit_px = None
                if h >= tp:
                    exit_px, kind = tp - half_spread, "TP"
                elif l <= sl:
                    exit_px, kind = sl - half_spread, "SL"
                if exit_px is not None:
                    pnl = qty * (exit_px - avg_entry) * rq          # ← 修正核心：經計價貨幣換算
                    comm = (qty / CONTRACT_SIZE) * COMMISSION_PER_LOT_SIDE
                    cash += pnl - comm
                    trades.append((kind, pnl - comm))
                    trade_log.append(dict(
                        pair=pair, side="LONG", entry_time=entry_time, entry_price=entry_l1_price,
                        avg_entry=avg_entry, exit_time=idx[i], exit_price=exit_px, reason=kind,
                        layers=layer, lots=round(qty / CONTRACT_SIZE, 4), pnl_usd=round(pnl - comm, 2),
                        hold_bars=i - entry_i))
                    closed = True
            else:
                tp = max(ema, avg_entry - cfg["tp_atr"] * atr)
                sl = avg_entry + cfg["stop_atr"] * atr
                exit_px = None
                if l <= tp:
                    exit_px, kind = tp + half_spread, "TP"
                elif h >= sl:
                    exit_px, kind = sl + half_spread, "SL"
                if exit_px is not None:
                    pnl = qty * (avg_entry - exit_px) * rq
                    comm = (qty / CONTRACT_SIZE) * COMMISSION_PER_LOT_SIDE
                    cash += pnl - comm
                    trades.append((kind, pnl - comm))
                    trade_log.append(dict(
                        pair=pair, side="SHORT", entry_time=entry_time, entry_price=entry_l1_price,
                        avg_entry=avg_entry, exit_time=idx[i], exit_price=exit_px, reason=kind,
                        layers=layer, lots=round(qty / CONTRACT_SIZE, 4), pnl_usd=round(pnl - comm, 2),
                        hold_bars=i - entry_i))
                    closed = True

            if not closed and layer < cfg["max_layers"]:
                step = layer * cfg["dca_step"] * atr
                # 加碼的方向就是既有部位的方向，所以看那一邊能不能動
                _side_ok = long_ok if side > 0 else short_ok
                dca_allowed = _side_ok or not adx_block_dca
                if side > 0 and l <= avg_entry - step and dca_allowed:
                    px = (avg_entry - step) + half_spread
                    q, comm, lots = open_leg(dca_order * vs[i], px, 1)
                    cash -= comm
                    avg_entry = (avg_entry * qty + px * q) / (qty + q)
                    qty += q
                    invested_usd += q * rb
                    layer += 1
                    lots_log.append(lots)
                elif side < 0 and h >= avg_entry + step and dca_allowed:
                    px = (avg_entry + step) - half_spread
                    q, comm, lots = open_leg(dca_order * vs[i], px, -1)
                    cash -= comm
                    avg_entry = (avg_entry * qty + px * q) / (qty + q)
                    qty += q
                    invested_usd += q * rb
                    layer += 1
                    lots_log.append(lots)

            if closed:
                side, qty, layer, avg_entry, invested_usd = 0, 0.0, 0, 0.0, 0.0

        # 權益 = 現金 + 未實現損益（已換算為美元）
        if side > 0:
            equity[i] = cash + qty * (c - avg_entry) * rq
            equity_adv[i] = cash + qty * (l - avg_entry) * rq
        elif side < 0:
            equity[i] = cash + qty * (avg_entry - c) * rq
            equity_adv[i] = cash + qty * (avg_entry - h) * rq
        else:
            equity[i] = cash
            equity_adv[i] = cash

    # 回測結束當下若還持有部位（尚未等到止盈/停損），記錄下來給「目前持倉」區塊用
    open_position = None
    if side != 0:
        last_close = close[-1]
        unreal_pnl = qty * (last_close - avg_entry) * r_quote[-1] * side
        open_position = dict(
            pair=pair, side="LONG" if side > 0 else "SHORT",
            entry_time=entry_time, entry_price=entry_l1_price, avg_entry=avg_entry,
            layers=layer, lots=round(qty / CONTRACT_SIZE, 4),
            current_price=float(last_close), unrealized_pnl=round(float(unreal_pnl), 2),
            hold_bars=(n - 1) - entry_i, as_of=idx[-1],
        )

    eq = pd.Series(equity, index=idx)
    cap = initial_capital * capital_scale
    total_ret = (eq.iloc[-1] - cap) / cap * 100.0
    peak = eq.cummax()
    mdd_close = abs(((eq - peak) / peak).min()) * 100.0
    cur_dd = abs((eq.iloc[-1] - peak.iloc[-1]) / peak.iloc[-1]) * 100.0
    # 盤中計價的回撤：峰值仍用收盤價曲線（那是真正「帳面最高點」），
    # 但谷底用盤中最不利價，才反映實際看到的最深浮虧。
    eq_adv = pd.Series(equity_adv, index=idx)
    mdd = abs(((eq_adv - peak) / peak).min()) * 100.0
    years = (idx[-1] - idx[0]).total_seconds() / 86400.0 / 365.25
    rets = eq.pct_change().dropna()
    sharpe = rets.mean() / rets.std() * np.sqrt(n / years) if rets.std() > 0 else 0.0
    ann = ((1 + total_ret / 100.0) ** (1 / years) - 1) * 100.0
    pnls = [p for _, p in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]

    return dict(
        pair=pair, total_return_pct=total_ret, ann_return_pct=ann, max_dd_pct=mdd,
        max_dd_close_pct=mdd_close,
        current_dd_pct=cur_dd, sharpe=sharpe, calmar=ann / mdd if mdd > 0.01 else np.nan,
        win_rate=len(wins) / len(pnls) * 100.0 if pnls else 0.0,
        n_trades=len(pnls), n_tp=sum(1 for k, _ in trades if k == "TP"),
        n_sl=sum(1 for k, _ in trades if k == "SL"),
        profit_factor=(sum(wins) / abs(sum(losses))) if losses and sum(losses) != 0 else np.nan,
        median_lots=float(np.median(lots_log)) if lots_log else 0.0,
        max_lots=float(np.max(lots_log)) if lots_log else 0.0,
        years=years, equity=eq, trade_log=trade_log, open_position=open_position,
    )


def load_real_costs():
    df = pd.read_csv(REAL_COST_TABLE).set_index("貨幣對")
    out = {}
    for pair in df.index:
        if pair not in PAIR_CCY:
            continue
        row = df.loc[pair]
        out[pair] = dict(
            pip_size=0.01 if pair in BIG_QUOTE else 0.0001,
            spread_pips=float(row["點差中位數(Pips)"]),
            swap_long=float(row["做多Swap($/手/日)"]),
            swap_short=float(row["做空Swap($/手/日)"]),
            cost_is_estimated=False,
        )
    return out


def load_costs_all_pairs(default_spread_pips: float = 2.5):
    """跟 load_real_costs() 一樣，但對『沒被 ExportForexRealCosts.mq5 調查過』的貨幣對，
    補一個保守估計值（標明 cost_is_estimated=True），讓大規模掃描不會漏掉沒調查過的貨幣對。
    這些估計值只是佔位，用來先做初步篩選；真的要交易之前應該重新用 MQL5 腳本把
    InpSymbols 補上這些貨幣對，取得真實點差。"""
    real = load_real_costs()
    out = dict(real)
    for pair in PAIR_CCY:
        if pair in out:
            continue
        out[pair] = dict(
            pip_size=0.01 if pair in BIG_QUOTE else 0.0001,
            spread_pips=default_spread_pips,
            swap_long=-3.0, swap_short=-3.0,
            cost_is_estimated=True,
        )
    return out
