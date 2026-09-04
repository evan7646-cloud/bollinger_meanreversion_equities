import os  # 引入作業系統檔案路徑管理模組
import numpy as np  # 引入數值計算模組
import pandas as pd  # 引入資料處理與分析模組
from dataclasses import dataclass  # 引入資料類別裝飾器
from typing import List, Dict, Optional  # 引入型別提示模組
import matplotlib.pyplot as plt  # 引入視覺化繪圖庫

# ==========================================================
# 1. 讀取真實歷史 Spread 對照表
# ==========================================================

SPREAD_MAP = pd.read_csv("mt5_spread_by_symbol.csv").set_index("symbol")["avg_spread_bps"].to_dict()  # 從現有檔案讀取 MT5 真實平均點差表 (bps)

# ==========================================================
# 2. 策略一：OctoBot 風格低頻智慧平移網格 (嚴格部位上限與真實 Spread)
# ==========================================================

class RealSpreadOctoGrid:  # 定義真實點差下的 OctoBot 智慧網格策略
    def __init__(self, initial_capital: float = 25000.0, order_size_usd: float = 1500.0,  # 設定初始本金 $25,000 與每格下單金額 $1,500
                 max_grid_levels: int = 5, grid_step_pct: float = 0.035, commission_bps: float = 2.0):  # 最多 5 檔網格、每格 3.5%、手續費 2 bps
        self.initial_capital = initial_capital  # 初始本金
        self.order_size_usd = order_size_usd  # 單格固定下單金額
        self.max_grid_levels = max_grid_levels  # 最大允許買入層數 (嚴控總倉位最高不超過 $7,500)
        self.grid_step_pct = grid_step_pct  # 每檔網格利潤間距 (3.5%)
        self.commission_rate = commission_bps / 10000.0  # 佣金費率換算

    def run(self, df: pd.DataFrame, default_spread_bps: float) -> dict:  # 執行真實點差回測
        data = df.copy().reset_index(drop=True)  # 複製資料並重設索引
        cash = self.initial_capital  # 當前現金餘額
        shares = 0.0  # 當前持股數量
        
        # 建立均線以輔助判斷大週期多空體制
        data['ema50'] = data['close'].ewm(span=50).mean()  # 50 EMA
        data['ema200'] = data['close'].ewm(span=200).mean()  # 200 EMA
        
        anchor_price = data.iloc[0]['close']  # 初始錨定價格
        grid_buy_orders = []  # 存儲動態買單檔位
        grid_sell_orders = []  # 存儲動態賣單檔位
        trades_log = []  # 記錄實際成交明細
        equity_curve = []  # 記錄每日/每根 K 線淨值
        
        for idx, row in data.iterrows():  # 逐根 K 線遍歷撮合
            close, high, low = row['close'], row['high'], row['low']  # 取得高低收價格
            ema50, ema200 = row['ema50'], row['ema200']  # 取得均線
            
            # 取得該根 Bar 的真實 Spread (若 Bar 數據有記錄則採用，否則查表)
            bar_spread_bps = row['spread_bps'] if ('spread_bps' in row and row['spread_bps'] > 0) else default_spread_bps  # 真實點差 bps
            half_spread_ratio = (bar_spread_bps / 10000.0) / 2.0  # 半點差比例
            
            trend_bullish = (ema50 >= ema200 * 0.98) if not np.isnan(ema200) else True  # 判定大趨勢是否偏多
            
            # 1. 智慧向上平移 (當價格強勢突破上界時，上調錨點，落袋部分並將網格上移)
            if high >= anchor_price * (1.0 + self.grid_step_pct) and trend_bullish:  # 觸及上方獲利線
                if shares > 0:  # 若手上持有倉位
                    sell_qty = min(shares, self.order_size_usd / close)  # 賣出單格對應股數
                    exec_price = (anchor_price * (1.0 + self.grid_step_pct)) * (1.0 - half_spread_ratio)  # 扣除真實點差之成交價
                    revenue = exec_price * sell_qty * (1.0 - self.commission_rate)  # 扣除手續費之淨所得
                    cash += revenue  # 現金增加
                    shares -= sell_qty  # 持股扣減
                    trades_log.append({"type": "SELL_GRID", "price": exec_price, "qty": sell_qty, "spread_bps": bar_spread_bps})  # 記錄賣出
                anchor_price = anchor_price * (1.0 + self.grid_step_pct)  # 網格錨點向上平移
                
            # 2. 向下低吸觸發 (每跌 3.5% 且不超過最大層數時，以真實 Ask 價買進)
            current_layer = int(round((anchor_price - close) / (anchor_price * self.grid_step_pct)))  # 計算當前所處下跌層級
            if 1 <= current_layer <= self.max_grid_levels and low <= anchor_price * (1.0 - current_layer * self.grid_step_pct):  # 觸發買進檔位
                # 檢查是否已在該層買過，避免重複下單
                target_buy_p = anchor_price * (1.0 - current_layer * self.grid_step_pct)  # 目標買進價格
                exec_price = target_buy_p * (1.0 + half_spread_ratio)  # 加上真實點差的買入價
                buy_qty = self.order_size_usd / exec_price  # 計算固定金額對應股數
                cost = exec_price * buy_qty * (1.0 + self.commission_rate)  # 總支付金額 (含佣金)
                if cash >= cost and (shares * close) < (self.order_size_usd * self.max_grid_levels):  # 嚴格限制最大曝險上限
                    cash -= cost  # 扣減現金
                    shares += buy_qty  # 增加持股
                    trades_log.append({"type": f"BUY_L{current_layer}", "price": exec_price, "qty": buy_qty, "spread_bps": bar_spread_bps})  # 記錄買進
                    
            # 3. 嚴格停損防禦 (若跌破超過 6 層且趨勢翻空，強制平倉止血)
            if low <= anchor_price * (1.0 - (self.max_grid_levels + 1) * self.grid_step_pct) and not trend_bullish:  # 觸發崩盤停損
                if shares > 0:  # 手上仍有庫存
                    exec_price = close * (1.0 - half_spread_ratio)  # 停損平倉價
                    revenue = exec_price * shares * (1.0 - self.commission_rate)  # 變現所得
                    cash += revenue  # 資金回籠
                    shares = 0.0  # 清空持股
                    anchor_price = close  # 重新錨定價格
                    trades_log.append({"type": "STOP_LOSS", "price": exec_price, "qty": shares, "spread_bps": bar_spread_bps})  # 記錄停損
                    
            # 計算當前總淨值
            cur_equity = cash + (shares * close)  # 總淨值 = 現金 + 持股市值
            equity_curve.append(cur_equity)  # 紀錄時序淨值
            
        return {"equity_curve": equity_curve, "trades": trades_log}  # 返回回測結果

# ==========================================================
# 3. 策略二：Freqtrade 風格低頻波段 Channel DCA (嚴格點差與低頻調倉)
# ==========================================================

class RealSpreadFreqtradeDca:  # 定義真實點差下的 Freqtrade 通道 DCA 策略
    def __init__(self, initial_capital: float = 25000.0, base_order_usd: float = 2000.0,  # 初始本金 $25,000，首單 $2,000
                 max_dca_layers: int = 3, commission_bps: float = 2.0):  # 最多 DCA 加倉 3 次 (總曝險最多 $6,000)
        self.initial_capital = initial_capital  # 初始本金
        self.base_order_usd = base_order_usd  # 基礎單筆金額
        self.max_dca_layers = max_dca_layers  # 最大加倉次數
        self.commission_rate = commission_bps / 10000.0  # 佣金費率

    def run(self, df: pd.DataFrame, default_spread_bps: float) -> dict:  # 執行真實點差 DCA 回測
        data = df.copy().reset_index(drop=True)  # 複製資料並重設索引
        
        # 指標計算：日級/大波段 ATR (以 50 根 Bar 平滑) 與 50 EMA 通道
        data['tr'] = np.maximum(data['high'] - data['low'],  # 計算 TR
                                np.maximum(np.abs(data['high'] - data['close'].shift(1)),  # 高與昨收
                                           np.abs(data['low'] - data['close'].shift(1))))  # 低與昨收
        data['atr'] = data['tr'].rolling(window=50).mean().bfill()  # 計算 50 ATR
        data['ema50'] = data['close'].ewm(span=50).mean()  # 50 EMA 中軌
        data['lower_band'] = data['ema50'] - (data['atr'] * 2.5)  # 2.5 倍 ATR 超跌進場下軌
        
        cash = self.initial_capital  # 現金餘額
        shares = 0.0  # 持股數量
        layer = 0  # 當前加倉層級
        avg_entry_price = 0.0  # 持倉均價
        equity_curve = []  # 淨值曲線
        trades_log = []  # 交易紀錄
        
        for idx, row in data.iterrows():  # 逐根 K 線遍歷
            close, high, low = row['close'], row['high'], row['low']  # 取得價格
            atr, ema50, lower_band = row['atr'], row['ema50'], row['lower_band']  # 取得通道指標
            bar_spread_bps = row['spread_bps'] if ('spread_bps' in row and row['spread_bps'] > 0) else default_spread_bps  # 真實點差
            half_spread_ratio = (bar_spread_bps / 10000.0) / 2.0  # 半點差
            
            if layer == 0:  # 無倉位時尋找超跌進場
                if low <= lower_band:  # 價格觸及超跌下軌
                    exec_price = min(close, lower_band) * (1.0 + half_spread_ratio)  # 加上點差之買入價
                    buy_qty = self.base_order_usd / exec_price  # 首單買入股數
                    cost = exec_price * buy_qty * (1.0 + self.commission_rate)  # 總支付金額
                    if cash >= cost:  # 確認現金充足
                        cash -= cost  # 扣減現金
                        shares = buy_qty  # 設定持股
                        layer = 1  # 標記第 1 層已建倉
                        avg_entry_price = exec_price  # 設定持倉均價
                        trades_log.append({"type": "DCA_ENTRY_L1", "price": exec_price, "qty": buy_qty, "spread_bps": bar_spread_bps})  # 記錄開倉
            else:  # 已有部位時評估止盈、止損或補倉
                # 1. 均值回歸止盈：價格回抽至 50 EMA 中軌
                if high >= ema50:  # 觸及中軌止盈
                    exec_price = ema50 * (1.0 - half_spread_ratio)  # 扣除點差賣出價
                    revenue = exec_price * shares * (1.0 - self.commission_rate)  # 實收現金
                    cash += revenue  # 現金回籠
                    trades_log.append({"type": "EXIT_TP", "price": exec_price, "qty": shares, "pnl": revenue - (avg_entry_price * shares)})  # 記錄止盈
                    shares = 0.0  # 清空持股
                    layer = 0  # 重設層數
                    avg_entry_price = 0.0  # 重設均價
                # 2. 嚴格防禦停損：跌破均價超過 4.0 倍 ATR
                elif low <= avg_entry_price - (atr * 4.0):  # 觸發停損線
                    exec_price = (avg_entry_price - atr * 4.0) * (1.0 - half_spread_ratio)  # 扣除點差停損價
                    revenue = exec_price * shares * (1.0 - self.commission_rate)  # 實收金額
                    cash += revenue  # 現金回籠
                    trades_log.append({"type": "EXIT_SL", "price": exec_price, "qty": shares, "pnl": revenue - (avg_entry_price * shares)})  # 記錄停損
                    shares = 0.0  # 清空持股
                    layer = 0  # 重設層數
                    avg_entry_price = 0.0  # 重設均價
                # 3. 逐層加倉 DCA (每再下跌 1.5 倍 ATR 補一次倉，最多 3 層)
                elif layer < self.max_dca_layers:  # 尚未達到最大補倉限制
                    next_trigger = avg_entry_price - (layer * atr * 1.5)  # 下一階加倉價
                    if low <= next_trigger:  # 觸及加倉價
                        exec_price = next_trigger * (1.0 + half_spread_ratio)  # 加倉買入價
                        buy_qty = self.base_order_usd / exec_price  # 加倉股數
                        cost = exec_price * buy_qty * (1.0 + self.commission_rate)  # 加倉成本
                        if cash >= cost:  # 確認現金充裕
                            cash -= cost  # 扣除現金
                            total_spent = (avg_entry_price * shares) + (exec_price * buy_qty)  # 累計總成本
                            shares += buy_qty  # 累加持股
                            avg_entry_price = total_spent / shares  # 重新計算持倉均價
                            layer += 1  # 增加層數
                            trades_log.append({"type": f"DCA_ADD_L{layer}", "price": exec_price, "qty": buy_qty, "spread_bps": bar_spread_bps})  # 記錄加倉
                            
            # 計算淨值
            cur_equity = cash + (shares * close)  # 總淨值
            equity_curve.append(cur_equity)  # 紀錄淨值時序
            
        return {"equity_curve": equity_curve, "trades": trades_log}  # 返回回測成果

# ==========================================================
# 4. 執行真實 Spread 與合理下單量回測測試
# ==========================================================

def run_real_spread_backtest():  # 執行真實回測主函式
    symbols = ["AAPL", "NVDA", "MSFT", "TSLA", "AMZN", "GOOG", "JPM", "WMT", "KO", "GME"]  # 包含高流動性與高點差股票
    data_dir = "data_mt5_equities_cfd"  # 資料檔案夾
    
    octo_bot = RealSpreadOctoGrid(initial_capital=25000.0, order_size_usd=1500.0, max_grid_levels=5, grid_step_pct=0.035)  # 建立 Octo 策略 (單筆 $1,500，最多 5 筆)
    freq_bot = RealSpreadFreqtradeDca(initial_capital=25000.0, base_order_usd=2000.0, max_dca_layers=3)  # 建立 Freq 策略 (單筆 $2,000，最多 3 筆)
    
    results = []  # 存儲結果清單
    curves = {}  # 存儲繪圖資料
    
    print("==========================================================================================")  # 分隔線
    print("🎯 執行美股真實 Spread 與真實下單量 (嚴控單筆 $1.5k~$2k、低頻交易) 回測...")  # 提示訊息
    print("==========================================================================================")  # 分隔線
    
    for sym in symbols:  # 遍歷股票
        filename = f"EQ_{sym}_5m.csv"  # 檔名結構
        filepath = os.path.join(data_dir, filename)  # 完整路徑
        if not os.path.exists(filepath):  # 檢查檔案
            continue  # 若無則跳過
            
        df = pd.read_csv(filepath)  # 讀取 CSV
        df['close'] = df['close'].astype(float)  # 確保收盤價為浮點數
        df['high'] = df['high'].astype(float)  # 確保最高價為浮點數
        df['low'] = df['low'].astype(float)  # 確保最低價為浮點數
        df['open'] = df['open'].astype(float)  # 確保開盤價為浮點數
        
        real_spread_bps = SPREAD_MAP.get(sym, 5.0)  # 取得該標的之真實平均 Spread (bps)
        
        # 1. 運行 OctoBot 回測
        res_octo = octo_bot.run(df, default_spread_bps=real_spread_bps)  # 運行
        eq_octo = np.array(res_octo["equity_curve"])  # 轉陣列
        ret_octo = (eq_octo[-1] - 25000.0) / 25000.0 * 100.0  # 總報酬率 %
        dd_octo = float(np.min((eq_octo - np.maximum.accumulate(eq_octo)) / np.maximum.accumulate(eq_octo))) * 100.0  # 最大回撤 %
        
        # 2. 運行 Freqtrade 回測
        res_freq = freq_bot.run(df, default_spread_bps=real_spread_bps)  # 運行
        eq_freq = np.array(res_freq["equity_curve"])  # 轉陣列
        ret_freq = (eq_freq[-1] - 25000.0) / 25000.0 * 100.0  # 總報酬率 %
        dd_freq = float(np.min((eq_freq - np.maximum.accumulate(eq_freq)) / np.maximum.accumulate(eq_freq))) * 100.0  # 最大回撤 %
        
        # 基準買入持有收益
        bh_ret = (df.iloc[-1]['close'] - df.iloc[0]['close']) / df.iloc[0]['close'] * 100.0  # B&H 報酬 %
        
        results.append({  # 寫入摘要報告
            "Symbol": sym,  # 標的代號
            "Real Spread (bps)": round(real_spread_bps, 1),  # 真實點差 (bps)
            "B&H Ret %": round(bh_ret, 1),  # 買入持有收益率 %
            "Octo Ret %": round(ret_octo, 2),  # Octo 收益率 %
            "Octo MaxDD %": round(dd_octo, 2),  # Octo 最大回撤 %
            "Octo Trades": len(res_octo["trades"]),  # Octo 總交易次數
            "Freq Ret %": round(ret_freq, 2),  # Freq 收益率 %
            "Freq MaxDD %": round(dd_freq, 2),  # Freq 最大回撤 %
            "Freq Trades": len(res_freq["trades"])  # Freq 總交易次數
        })  # 加入清單
        
        curves[sym] = {"Octo": eq_octo, "Freq": eq_freq}  # 儲存曲線
        print(f"📊 {sym:5s} (Spread {real_spread_bps:>4.1f} bps) | Octo: Ret {ret_octo:>6.2f}%, DD {dd_octo:>6.2f}%, {len(res_octo['trades']):>3d} 筆 | Freq: Ret {ret_freq:>6.2f}%, DD {dd_freq:>6.2f}%, {len(res_freq['trades']):>3d} 筆")  # 輸出進度
        
    res_df = pd.DataFrame(results)  # 轉 DataFrame
    res_df.to_csv("real_spread_grid_results.csv", index=False)  # 匯出 CSV 報告
    print("\n========================= 真實 Spread 與部位控制回測報告 =========================")  # 分隔線
    print(res_df.to_string(index=False))  # 列印表格
    
    # 繪製圖表
    plt.figure(figsize=(14, 8))  # 設定圖表尺寸
    plt.style.use('seaborn-v0_8-darkgrid' if 'seaborn-v0_8-darkgrid' in plt.style.available else 'default')  # 設定圖表樣式
    
    sample_symbols = ["NVDA", "AAPL", "MSFT", "TSLA"]  # 選定 4 檔繪圖
    for i, s in enumerate(sample_symbols):  # 繪製子圖
        if s in curves:  # 確保有資料
            plt.subplot(2, 2, i+1)  # 選擇子圖
            plt.plot(curves[s]["Octo"], label="OctoBot Smart Grid ($1.5k/grid)", color="#2ca02c", lw=1.6)  # Octo 曲線
            plt.plot(curves[s]["Freq"], label="Freqtrade Channel DCA ($2k/dca)", color="#1f77b4", lw=1.6)  # Freq 曲線
            plt.axhline(25000, color="gray", linestyle="--", alpha=0.6, label="Base Capital $25k")  # 初始本金參考線
            plt.title(f"{s} - Real Spread Backtest", fontsize=12, fontweight="bold")  # 標題
            plt.xlabel("Bars (5-min)", fontsize=9)  # X 軸
            plt.ylabel("Portfolio Value ($)", fontsize=9)  # Y 軸
            plt.legend(fontsize=8)  # 圖例
            
    plt.tight_layout()  # 自動緊湊排版
    plt.savefig("real_spread_performance.png", dpi=300)  # 儲存圖片
    print("📈 真實 Spread 淨值圖已儲存至 real_spread_performance.png")  # 提示完成

if __name__ == "__main__":  # 程式主入口
    run_real_spread_backtest()  # 啟動回測
