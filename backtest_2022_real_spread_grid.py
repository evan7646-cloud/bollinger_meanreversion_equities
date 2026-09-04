import os  # 引入作業系統檔案與目錄管理模組
import glob  # 引入檔案搜尋模組
import numpy as np  # 引入數值計算矩陣模組
import pandas as pd  # 引入資料處理與分析模組
from typing import List, Dict, Tuple  # 引入型別提示模組
import matplotlib.pyplot as plt  # 引入繪圖模組

# ==========================================================
# 1. 讀取真實歷史 Spread 表
# ==========================================================

SPREAD_TABLE = pd.read_csv("mt5_spread_by_symbol.csv").set_index("symbol")["avg_spread_bps"].to_dict()  # 讀取真實平均點差對照字典 (bps)

# ==========================================================
# 2. 策略一：OctoBot 風格低頻智慧平移網格 (2022+ 嚴格真實 Spread 與 0.002% 手續費)
# ==========================================================

class RealOctoBotSmartGrid:  # 定義真實點差與手續費下的 OctoBot 智慧網格引擎
    def __init__(self, initial_capital: float = 25000.0, order_size_usd: float = 1500.0,  # 初始化本金 $25,000 與每格 $1,500
                 max_grid_levels: int = 5, grid_step_pct: float = 0.035, commission_pct: float = 0.00002):  # 5 檔網格、3.5% 間距、0.002% 佣金
        self.initial_capital = initial_capital  # 初始本金
        self.order_size_usd = order_size_usd  # 單筆固定下單金額 ($1,500)
        self.max_grid_levels = max_grid_levels  # 最大持倉網格層數 (上限 5 筆，最大曝險 $7,500，佔 30%)
        self.grid_step_pct = grid_step_pct  # 網格獲利間距 (3.5%)
        self.commission_pct = commission_pct  # 買賣手續費率 (0.002% = 0.00002)

    def run(self, df: pd.DataFrame, default_spread_bps: float) -> dict:  # 執行 2022+ 回測運算
        data = df.copy().reset_index(drop=True)  # 複製資料並重設索引
        cash = self.initial_capital  # 當前現金餘額
        shares = 0.0  # 當前持股數量
        
        # 指標計算：大趨勢 50 EMA 與 200 EMA 體制濾網
        data['ema50'] = data['close'].ewm(span=50).mean()  # 計算 50 EMA
        data['ema200'] = data['close'].ewm(span=200).mean()  # 計算 200 EMA
        
        anchor_price = data.iloc[0]['close']  # 初始化網格基準錨定價
        trades_log = []  # 記錄實際交易明細
        equity_curve = []  # 記錄時序權益淨值
        total_spread_cost = 0.0  # 累計支付的點差摩擦成本 ($)
        total_comm_cost = 0.0  # 累計支付的佣金手續費 ($)
        
        for idx, row in data.iterrows():  # 逐根 5 分鐘 K 線模擬撮合
            close, high, low = row['close'], row['high'], row['low']  # 取得高低收價格
            ema50, ema200 = row['ema50'], row['ema200']  # 取得均線
            
            # 取得當前 Bar 的精確點差 (若有記錄則使用，否則使用標的歷史平均值)
            bar_spread_bps = row['spread_bps'] if ('spread_bps' in row and row['spread_bps'] > 0) else default_spread_bps  # 點差 (bps)
            half_spread_ratio = (bar_spread_bps / 10000.0) / 2.0  # 半點差比例
            trend_bullish = (ema50 >= ema200 * 0.98) if not np.isnan(ema200) else True  # 判定大趨勢方向
            
            # 1. 智慧向上平移獲利 (觸及上方 3.5% 檔位，賣出獲利並平移錨點)
            if high >= anchor_price * (1.0 + self.grid_step_pct) and trend_bullish:  # 觸發上方獲利平移線
                if shares > 0:  # 確認手上持有庫存
                    sell_qty = min(shares, self.order_size_usd / close)  # 賣出單格對應股數
                    raw_price = anchor_price * (1.0 + self.grid_step_pct)  # 名目目標賣價
                    exec_price = raw_price * (1.0 - half_spread_ratio)  # 扣除真實點差後之 Bid 成交價
                    spread_paid = (raw_price - exec_price) * sell_qty  # 計算本次承擔的點差金額
                    comm_paid = exec_price * sell_qty * self.commission_pct  # 計算 0.002% 手續費金額
                    revenue = (exec_price * sell_qty) - comm_paid  # 實收淨現金
                    
                    cash += revenue  # 現金增加
                    shares -= sell_qty  # 持股扣除
                    total_spread_cost += spread_paid  # 累計點差成本
                    total_comm_cost += comm_paid  # 累計手續費
                    trades_log.append({"type": "SELL_GRID", "price": exec_price, "qty": sell_qty, "pnl": revenue})  # 記錄賣出明細
                anchor_price = anchor_price * (1.0 + self.grid_step_pct)  # 網格基準價向上平移
                
            # 2. 向下低吸觸發 (下跌每滿 3.5% 補一檔，最多 5 檔)
            current_layer = int(round((anchor_price - close) / (anchor_price * self.grid_step_pct)))  # 計算所屬下跌階梯
            if 1 <= current_layer <= self.max_grid_levels and low <= anchor_price * (1.0 - current_layer * self.grid_step_pct):  # 觸及買進條件
                target_buy_p = anchor_price * (1.0 - current_layer * self.grid_step_pct)  # 名目目標買價
                exec_price = target_buy_p * (1.0 + half_spread_ratio)  # 加上真實點差之 Ask 成交價
                buy_qty = self.order_size_usd / exec_price  # 計算單格股數
                spread_paid = (exec_price - target_buy_p) * buy_qty  # 計算本次點差摩擦
                comm_paid = exec_price * buy_qty * self.commission_pct  # 計算 0.002% 手續費
                cost = (exec_price * buy_qty) + comm_paid  # 總支付現金
                
                if cash >= cost and (shares * close) < (self.order_size_usd * self.max_grid_levels):  # 嚴守總部位曝險上限
                    cash -= cost  # 扣除現金
                    shares += buy_qty  # 增加持股
                    total_spread_cost += spread_paid  # 累計點差
                    total_comm_cost += comm_paid  # 累計手續費
                    trades_log.append({"type": f"BUY_L{current_layer}", "price": exec_price, "qty": buy_qty})  # 記錄買入明細
                    
            # 3. 趨勢逆轉防禦停損 (跌破 6 層且大趨勢走空，全數市價清倉止血)
            if low <= anchor_price * (1.0 - (self.max_grid_levels + 1) * self.grid_step_pct) and not trend_bullish:  # 觸發停損
                if shares > 0:  # 手上持有倉位
                    raw_price = close  # 名目市價
                    exec_price = raw_price * (1.0 - half_spread_ratio)  # 扣除點差後賣價
                    spread_paid = (raw_price - exec_price) * shares  # 點差損失
                    comm_paid = exec_price * shares * self.commission_pct  # 手續費
                    revenue = (exec_price * shares) - comm_paid  # 實收金額
                    
                    cash += revenue  # 資金回籠
                    shares = 0.0  # 清空持股
                    anchor_price = close  # 重新設定基準錨點
                    total_spread_cost += spread_paid  # 累加點差
                    total_comm_cost += comm_paid  # 累加手續費
                    trades_log.append({"type": "STOP_LOSS", "price": exec_price, "qty": shares})  # 記錄停損明細
                    
            # 計算每根 K 線結束後的帳戶總權益
            total_equity = cash + (shares * close)  # 總權益 = 現金 + 持股市值
            equity_curve.append(total_equity)  # 記錄權益歷史
            
        return {  # 返回策略統計報告
            "equity_curve": equity_curve,  # 權益曲線
            "trades": trades_log,  # 交易紀錄
            "total_spread_cost": total_spread_cost,  # 總點差消耗 ($)
            "total_comm_cost": total_comm_cost  # 總佣金消耗 ($)
        }

# ==========================================================
# 3. 策略二：Freqtrade 風格低頻通道 DCA (2022+ 嚴格真實 Spread 與 0.002% 手續費)
# ==========================================================

class RealFreqtradeChannelDca:  # 定義真實點差與手續費下的 Freqtrade 通道 DCA 引擎
    def __init__(self, initial_capital: float = 25000.0, base_order_usd: float = 2000.0,  # 初始化本金 $25,000 與首單 $2,000
                 max_dca_layers: int = 3, commission_pct: float = 0.00002):  # 最大補倉 3 次 (總曝險 $6,000)、0.002% 佣金
        self.initial_capital = initial_capital  # 初始本金
        self.base_order_usd = base_order_usd  # 基礎下單金額
        self.max_dca_layers = max_dca_layers  # 最大加倉層數
        self.commission_pct = commission_pct  # 買賣手續費率 (0.002%)

    def run(self, df: pd.DataFrame, default_spread_bps: float) -> dict:  # 執行 2022+ DCA 回測
        data = df.copy().reset_index(drop=True)  # 複製資料並重設索引
        
        # 指標計算：50 ATR 波動通道與 50 EMA 基準中軌
        data['tr'] = np.maximum(data['high'] - data['low'],  # 計算 TR
                                np.maximum(np.abs(data['high'] - data['close'].shift(1)),  # 高與昨收
                                           np.abs(data['low'] - data['close'].shift(1))))  # 低與昨收
        data['atr'] = data['tr'].rolling(window=50).mean().bfill()  # 計算 50 週期平滑 ATR
        data['ema50'] = data['close'].ewm(span=50).mean()  # 50 EMA 中軌
        data['lower_band'] = data['ema50'] - (data['atr'] * 2.5)  # 2.5 倍 ATR 超跌進場線
        
        cash = self.initial_capital  # 現金餘額
        shares = 0.0  # 持股數量
        layer = 0  # 當前加倉層級
        avg_entry_price = 0.0  # 持倉加權均價
        equity_curve = []  # 淨值歷史記錄
        trades_log = []  # 成交明細清單
        total_spread_cost = 0.0  # 累計點差成本
        total_comm_cost = 0.0  # 累計手續費
        
        for idx, row in data.iterrows():  # 逐根 5 分鐘 K 線模擬撮合
            close, high, low = row['close'], row['high'], row['low']  # 取得價格
            atr, ema50, lower_band = row['atr'], row['ema50'], row['lower_band']  # 取得指標
            bar_spread_bps = row['spread_bps'] if ('spread_bps' in row and row['spread_bps'] > 0) else default_spread_bps  # 真實點差
            half_spread_ratio = (bar_spread_bps / 10000.0) / 2.0  # 半點差比例
            
            if layer == 0:  # 無倉位時尋找超跌開倉
                if low <= lower_band:  # 觸及超跌下軌
                    raw_price = min(close, lower_band)  # 名目買價
                    exec_price = raw_price * (1.0 + half_spread_ratio)  # 加上真實點差後買價
                    buy_qty = self.base_order_usd / exec_price  # 計算首單股數
                    spread_paid = (exec_price - raw_price) * buy_qty  # 點差損耗
                    comm_paid = exec_price * buy_qty * self.commission_pct  # 0.002% 手續費
                    cost = (exec_price * buy_qty) + comm_paid  # 總支付金額
                    
                    if cash >= cost:  # 確認資金充足
                        cash -= cost  # 扣除現金
                        shares = buy_qty  # 建立持倉
                        layer = 1  # 進入第 1 層
                        avg_entry_price = exec_price  # 設定初始均價
                        total_spread_cost += spread_paid  # 累計點差
                        total_comm_cost += comm_paid  # 累計手續費
                        trades_log.append({"type": "DCA_ENTRY_L1", "price": exec_price, "qty": buy_qty})  # 記錄開倉
            else:  # 持倉中進行出場或補倉評估
                # 1. 均值回歸止盈 (價格反彈回抽至 50 EMA 中軌)
                if high >= ema50:  # 觸及中軌止盈目標
                    raw_price = ema50  # 名目賣價
                    exec_price = raw_price * (1.0 - half_spread_ratio)  # 扣除真實點差後賣價
                    spread_paid = (raw_price - exec_price) * shares  # 點差損耗
                    comm_paid = exec_price * shares * self.commission_pct  # 0.002% 手續費
                    revenue = (exec_price * shares) - comm_paid  # 實收淨額
                    
                    cash += revenue  # 資金回籠
                    total_spread_cost += spread_paid  # 累加點差
                    total_comm_cost += comm_paid  # 累加手續費
                    trades_log.append({"type": "EXIT_TP", "price": exec_price, "qty": shares, "pnl": revenue - (avg_entry_price * shares)})  # 記錄止盈
                    shares = 0.0  # 清空持股
                    layer = 0  # 重設層數
                    avg_entry_price = 0.0  # 重設均價
                # 2. 防禦性停損 (跌破均價超過 4.0 倍 ATR)
                elif low <= avg_entry_price - (atr * 4.0):  # 觸發停損線
                    raw_price = avg_entry_price - (atr * 4.0)  # 名目停損價
                    exec_price = raw_price * (1.0 - half_spread_ratio)  # 扣除點差賣價
                    spread_paid = (raw_price - exec_price) * shares  # 點差損耗
                    comm_paid = exec_price * shares * self.commission_pct  # 0.002% 手續費
                    revenue = (exec_price * shares) - comm_paid  # 實收淨額
                    
                    cash += revenue  # 資金回籠
                    total_spread_cost += spread_paid  # 累加點差
                    total_comm_cost += comm_paid  # 累加手續費
                    trades_log.append({"type": "EXIT_SL", "price": exec_price, "qty": shares, "pnl": revenue - (avg_entry_price * shares)})  # 記錄停損
                    shares = 0.0  # 清空持股
                    layer = 0  # 重設層數
                    avg_entry_price = 0.0  # 重設均價
                # 3. 逐層補倉 DCA (每再下跌 1.5 倍 ATR 補一次倉，最多 3 層)
                elif layer < self.max_dca_layers:  # 尚未達到最大補倉限制
                    next_trigger = avg_entry_price - (layer * atr * 1.5)  # 下一階補倉觸發價
                    if low <= next_trigger:  # 觸及補倉價
                        raw_price = next_trigger  # 名目買價
                        exec_price = raw_price * (1.0 + half_spread_ratio)  # 加上真實點差買價
                        buy_qty = self.base_order_usd / exec_price  # 加倉股數
                        spread_paid = (exec_price - raw_price) * buy_qty  # 點差損耗
                        comm_paid = exec_price * buy_qty * self.commission_pct  # 0.002% 手續費
                        cost = (exec_price * buy_qty) + comm_paid  # 支付金額
                        
                        if cash >= cost:  # 確認現金充足
                            cash -= cost  # 扣除現金
                            total_spent = (avg_entry_price * shares) + (exec_price * buy_qty)  # 累計總支出
                            shares += buy_qty  # 增加持股
                            avg_entry_price = total_spent / shares  # 重新計算加權均價
                            layer += 1  # 增加層數
                            total_spread_cost += spread_paid  # 累加點差
                            total_comm_cost += comm_paid  # 累加手續費
                            trades_log.append({"type": f"DCA_ADD_L{layer}", "price": exec_price, "qty": buy_qty})  # 記錄補倉
                            
            # 計算當前總權益
            cur_equity = cash + (shares * close)  # 總權益
            equity_curve.append(cur_equity)  # 記錄淨值時序
            
        return {  # 返回策略結果
            "equity_curve": equity_curve,  # 淨值曲線
            "trades": trades_log,  # 交易紀錄
            "total_spread_cost": total_spread_cost,  # 總點差消耗 ($)
            "total_comm_cost": total_comm_cost  # 總佣金消耗 ($)
        }

# ==========================================================
# 4. 2022年以後 (2022-01-01 ~ 2026-08-31) 批量回測主程序
# ==========================================================

def run_2022_backtest():  # 執行 2022+ 專項回測函式
    symbols = ["AAPL", "NVDA", "MSFT", "TSLA", "AMZN", "GOOG", "META", "AMD", "JPM", "WMT", "KO", "DIS"]  # 12 檔代表性美股標的
    data_dir = "data_mt5_equities_cfd"  # 資料目錄路徑
    
    octo_engine = RealOctoBotSmartGrid(initial_capital=25000.0, order_size_usd=1500.0, max_grid_levels=5,  # 實例化 OctoBot
                                       grid_step_pct=0.035, commission_pct=0.00002)  # 0.002% 手續費
    freq_engine = RealFreqtradeChannelDca(initial_capital=25000.0, base_order_usd=2000.0, max_dca_layers=3,  # 實例化 Freqtrade
                                          commission_pct=0.00002)  # 0.002% 手續費
    
    results = []  # 儲存統計指標
    curves_2022 = {}  # 儲存繪圖資料
    
    print("==========================================================================================================")  # 分隔線
    print("📊 執行【2022年以後 (2022.01.03 ~ 2026.08.31)】美股回測 | 包含真實 Spread + 0.002% 雙向手續費")  # 標題
    print("==========================================================================================================")  # 分隔線
    
    for sym in symbols:  # 遍歷標的
        file_path = os.path.join(data_dir, f"EQ_{sym}_5m.csv")  # 取得完整路徑
        if not os.path.exists(file_path):  # 檢查檔案
            continue  # 跳過不存在檔案
            
        df = pd.read_csv(file_path)  # 讀取 CSV
        df['dt'] = pd.to_datetime(df['datetime'], format='%Y.%m.%d %H:%M')  # 解析日期時間
        df_2022 = df[df['dt'] >= '2022-01-01'].copy().reset_index(drop=True)  # 精確篩選 2022 年以後之數據
        
        if len(df_2022) < 1000:  # 檢查資料長度
            continue  # 資料不足則跳過
            
        df_2022['close'] = df_2022['close'].astype(float)  # 確保收盤價為浮點數
        df_2022['high'] = df_2022['high'].astype(float)  # 確保最高價為浮點數
        df_2022['low'] = df_2022['low'].astype(float)  # 確保最低價為浮點數
        df_2022['open'] = df_2022['open'].astype(float)  # 確保開盤價為浮點數
        
        real_spread_bps = SPREAD_TABLE.get(sym, 5.0)  # 取得真實平均 Spread (bps)
        
        # 1. 運行 OctoBot 回測
        res_octo = octo_engine.run(df_2022, default_spread_bps=real_spread_bps)  # 運行
        eq_octo = np.array(res_octo["equity_curve"])  # 轉為陣列
        ret_octo = (eq_octo[-1] - 25000.0) / 25000.0 * 100.0  # 總報酬率 %
        dd_octo = float(np.min((eq_octo - np.maximum.accumulate(eq_octo)) / np.maximum.accumulate(eq_octo))) * 100.0  # 最大回撤 %
        
        # 2. 運行 Freqtrade 回測
        res_freq = freq_engine.run(df_2022, default_spread_bps=real_spread_bps)  # 運行
        eq_freq = np.array(res_freq["equity_curve"])  # 轉為陣列
        ret_freq = (eq_freq[-1] - 25000.0) / 25000.0 * 100.0  # 總報酬率 %
        dd_freq = float(np.min((eq_freq - np.maximum.accumulate(eq_freq)) / np.maximum.accumulate(eq_freq))) * 100.0  # 最大回撤 %
        
        # 基準買入持有收益 (2022 年初至 2026 年底)
        bh_ret = (df_2022.iloc[-1]['close'] - df_2022.iloc[0]['close']) / df_2022.iloc[0]['close'] * 100.0  # B&H 收益 %
        
        results.append({  # 寫入摘要字典
            "Symbol": sym,  # 標的代號
            "Spread(bps)": round(real_spread_bps, 1),  # 真實點差
            "2022+ B&H %": round(bh_ret, 1),  # 2022+ 買入持有收益 %
            "Octo Ret %": round(ret_octo, 2),  # Octo 收益率 %
            "Octo MaxDD %": round(dd_octo, 2),  # Octo 最大回撤 %
            "Octo Trades": len(res_octo["trades"]),  # Octo 交易次數
            "Octo Spread$": round(res_octo["total_spread_cost"], 1),  # Octo 總支付點差
            "Octo Comm$": round(res_octo["total_comm_cost"], 2),  # Octo 總手續費 (0.002%)
            "Freq Ret %": round(ret_freq, 2),  # Freq 收益率 %
            "Freq MaxDD %": round(dd_freq, 2),  # Freq 最大回撤 %
            "Freq Trades": len(res_freq["trades"]),  # Freq 交易次數
            "Freq Spread$": round(res_freq["total_spread_cost"], 1),  # Freq 總支付點差
            "Freq Comm$": round(res_freq["total_comm_cost"], 2)  # Freq 總手續費 (0.002%)
        })  # 加入清單
        
        curves_2022[sym] = {"Octo": eq_octo, "Freq": eq_freq}  # 儲存時序
        print(f"✅ {sym:5s} (Spread {real_spread_bps:>4.1f} bps) | B&H: {bh_ret:>6.1f}% | Octo: +{ret_octo:>5.2f}% (DD {dd_octo:>5.2f}%, {len(res_octo['trades']):>3d}筆) | Freq: {ret_freq:>5.2f}% (DD {dd_freq:>5.2f}%, {len(res_freq['trades']):>4d}筆)")  # 輸出進度
        
    res_df = pd.DataFrame(results)  # 轉為 DataFrame
    res_df.to_csv("grid_2022_results.csv", index=False)  # 匯出 CSV 檔案
    print("\n=================================== 2022 年以後完整回測成果表 ===================================")  # 分隔線
    print(res_df.to_string(index=False))  # 印出完整表格
    
    # 繪製代表性標的走勢圖
    plt.figure(figsize=(14, 8))  # 建立畫布
    plt.style.use('seaborn-v0_8-darkgrid' if 'seaborn-v0_8-darkgrid' in plt.style.available else 'default')  # 設定樣式
    
    sample_symbols = ["NVDA", "AAPL", "MSFT", "TSLA"]  # 4 檔代表股票
    for i, s in enumerate(sample_symbols):  # 循環繪製
        if s in curves_2022:  # 確保存在
            plt.subplot(2, 2, i+1)  # 子圖位置
            plt.plot(curves_2022[s]["Octo"], label="OctoBot Smart Grid ($1.5k/grid)", color="#2ca02c", lw=1.6)  # 繪製 Octo 淨值
            plt.plot(curves_2022[s]["Freq"], label="Freqtrade Channel DCA ($2k/dca)", color="#1f77b4", lw=1.6)  # 繪製 Freq 淨值
            plt.axhline(25000, color="gray", linestyle="--", alpha=0.6, label="Base Capital $25k")  # 初始本金參考線
            plt.title(f"{s} (2022-2026) Real Cost Backtest", fontsize=12, fontweight="bold")  # 標題
            plt.xlabel("Bars (5-min since 2022)", fontsize=9)  # X 軸
            plt.ylabel("Portfolio Value ($)", fontsize=9)  # Y 軸
            plt.legend(fontsize=8)  # 圖例
            
    plt.tight_layout()  # 自動排版
    plt.savefig("grid_2022_performance.png", dpi=300)  # 儲存高畫質圖表
    print("📈 2022+ 淨值圖已儲存至 grid_2022_performance.png")  # 完成提示

if __name__ == "__main__":  # 程式主入口
    run_2022_backtest()  # 啟動回測
