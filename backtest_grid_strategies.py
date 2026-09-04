import os  # 引入作業系統路徑與檔案管理模組
import glob  # 引入檔案搜尋模組
import numpy as np  # 引入數值計算矩陣模組
import pandas as pd  # 引入資料處理與分析模組
from dataclasses import dataclass, field  # 引入資料類別以結構化管理訂單與狀態
from typing import List, Dict, Tuple, Optional  # 引入型別標註模組
import matplotlib.pyplot as plt  # 引入圖表繪製模組

# ==========================================================
# 1. 策略一：OctoBot 風格智慧動態平移網格 (Smart Dynamic Moving Grid)
# ==========================================================

@dataclass  # 資料結構裝飾器
class OctoGridOrder:  # 定義網格掛單物件
    order_id: int  # 訂單唯一識別碼
    side: str  # 買賣方向 (BUY 或 SELL)
    price: float  # 目標掛單價格
    qty: float  # 掛單數量 (股數)
    grid_index: int  # 對應的網格檔位索引
    paired_price: float = 0.0  # 對應 Ping-Pong 獲利平倉價格

class OctoBotSmartGridStrategy:  # 定義 OctoBot 智慧平移網格回測引擎
    def __init__(self, initial_capital: float = 25000.0, grid_count: int = 10,  # 初始化資金與網格層數
                 spread_bps: float = 3.0, commission_pct: float = 0.0002):  # 初始化點差基點與佣金費率
        self.initial_capital = initial_capital  # 初始本金 ($25,000)
        self.grid_count = grid_count  # 網格總層數
        self.spread_bps = spread_bps  # 交易點差 (bps)
        self.commission_pct = commission_pct  # 券商手續費率

    def run(self, df: pd.DataFrame, grid_span_pct: float = 0.08, shift_threshold: float = 0.02) -> dict:  # 執行 OctoBot 網格回測
        data = df.copy().reset_index(drop=True)  # 複製資料並重設索引
        cash = self.initial_capital * 0.5  # 配置 50% 初始現金
        current_price = data.iloc[0]['close']  # 取得初始起始價格
        shares = (self.initial_capital * 0.5) / current_price  # 配置 50% 初始現貨底倉
        
        lower_bound = current_price * (1.0 - grid_span_pct / 2.0)  # 計算網格初始下界
        upper_bound = current_price * (1.0 + grid_span_pct / 2.0)  # 計算網格初始上界
        step = (upper_bound - lower_bound) / (self.grid_count - 1)  # 計算每檔等差間距
        
        # 指標預先計算：趨勢評估器 (EMA 50 vs EMA 200 與 ADX 趨勢強度)
        data['ema50'] = data['close'].ewm(span=50).mean()  # 計算 50 EMA
        data['ema200'] = data['close'].ewm(span=200).mean()  # 計算 200 EMA
        
        orders: List[OctoGridOrder] = []  # 當前有效掛單清單
        order_counter = 0  # 訂單計數器
        
        def refresh_orders(l_bound: float, u_bound: float, mid_p: float, cur_cash: float, cur_shares: float):  # 重新掛出網格訂單
            nonlocal order_counter  # 引用外部計數器
            new_orders = []  # 儲存新掛單清單
            s = (u_bound - l_bound) / (self.grid_count - 1)  # 計算檔位間距
            per_grid_cash = max(100.0, cur_cash / (self.grid_count // 2 + 1))  # 每個買單檔位分配的資金
            per_grid_shares = max(1.0, cur_shares / (self.grid_count // 2 + 1))  # 每個賣單檔位分配的股數
            
            for i in range(self.grid_count):  # 遍歷所有網格檔位
                lvl_price = l_bound + i * s  # 計算該檔位價格
                if lvl_price < mid_p * 0.999:  # 低於當前價掛買單
                    buy_qty = per_grid_cash / lvl_price  # 計算買單股數
                    order_counter += 1  # 累加訂單編號
                    new_orders.append(OctoGridOrder(order_counter, "BUY", lvl_price, buy_qty, i, lvl_price + s))  # 加入買單
                elif lvl_price > mid_p * 1.001:  # 高於當前價掛賣單
                    sell_qty = min(cur_shares, per_grid_shares)  # 計算賣單股數
                    if sell_qty > 0:  # 確保有足夠庫存賣出
                        order_counter += 1  # 累加訂單編號
                        new_orders.append(OctoGridOrder(order_counter, "SELL", lvl_price, sell_qty, i, lvl_price - s))  # 加入賣單
            return new_orders  # 返回新掛單矩陣
        
        orders = refresh_orders(lower_bound, upper_bound, current_price, cash, shares)  # 鋪設初始網格
        
        equity_curve = []  # 權益曲線記錄
        trades_log = []  # 交易成交紀錄
        shifts_count = 0  # 記錄網格平移次數
        
        for idx, row in data.iterrows():  # 逐根 K 線模擬撮合
            high, low, close = row['high'], row['low'], row['close']  # 取得本根 K 線高低收
            ema50, ema200 = row['ema50'], row['ema200']  # 取得均線值
            trend_bullish = (ema50 > ema200) if not np.isnan(ema200) else True  # 判定趨勢是否為多頭
            
            # 1. 智慧平移檢驗 (Smart Trailing Shift)
            if close > upper_bound and trend_bullish:  # 多頭突破網格頂部
                shift_dist = (upper_bound - lower_bound) * 0.25  # 向上平移 25% 區間
                lower_bound += shift_dist  # 上調下界
                upper_bound += shift_dist  # 上調上界
                shifts_count += 1  # 記錄平移次數
                orders = refresh_orders(lower_bound, upper_bound, close, cash, shares)  # 重新在更高區間鋪網
            elif close < lower_bound and not trend_bullish:  # 空頭跌破網格底部
                shift_dist = (upper_bound - lower_bound) * 0.25  # 向下平移防禦
                lower_bound -= shift_dist  # 下調下界
                upper_bound -= shift_dist  # 下調上界
                shifts_count += 1  # 記錄平移次數
                orders = refresh_orders(lower_bound, upper_bound, close, cash, shares)  # 重新鋪網
                
            # 2. 訂單撮合 (Order Matching with Spread and Fees)
            remaining_orders = []  # 保存未成交訂單
            half_spread = close * (self.spread_bps / 10000.0) / 2.0  # 半點差成本
            
            for o in orders:  # 遍歷所有有效掛單
                if o.side == "BUY" and low <= o.price:  # 買單觸發撮合
                    exec_price = o.price + half_spread  # 加上買入滑點與點差
                    cost = exec_price * o.qty * (1.0 + self.commission_pct)  # 計算總買入成本
                    if cash >= cost:  # 確認現金充足
                        cash -= cost  # 扣除現金
                        shares += o.qty  # 增加現貨庫存
                        trades_log.append({"time": row['datetime'], "side": "BUY", "price": exec_price, "qty": o.qty})  # 記錄買單交易
                        # Ping-Pong 模式：立即掛出對應獲利賣單
                        target_sell = o.paired_price  # 獲利目標價
                        order_counter += 1  # 累加訂單編號
                        remaining_orders.append(OctoGridOrder(order_counter, "SELL", target_sell, o.qty, o.grid_index, o.price))  # 掛出對手賣單
                elif o.side == "SELL" and high >= o.price:  # 賣單觸發撮合
                    exec_price = o.price - half_spread  # 扣除賣出滑點與點差
                    revenue = exec_price * o.qty * (1.0 - self.commission_pct)  # 計算總賣出所得
                    if shares >= o.qty * 0.999:  # 確認現貨庫存充足
                        shares -= o.qty  # 扣減現貨庫存
                        cash += revenue  # 增加現金餘額
                        trades_log.append({"time": row['datetime'], "side": "SELL", "price": exec_price, "qty": o.qty})  # 記錄賣單交易
                        # Ping-Pong 模式：立即掛出對應低吸買單
                        target_buy = o.paired_price  # 低吸目標價
                        order_counter += 1  # 累加訂單編號
                        remaining_orders.append(OctoGridOrder(order_counter, "BUY", target_buy, o.qty, o.grid_index, o.price))  # 掛出對手買單
                else:  # 訂單未撮合
                    remaining_orders.append(o)  # 保留掛單
            orders = remaining_orders  # 更新當前掛單清單
            
            # 計算當前總權益
            total_equity = cash + (shares * close)  # 總權益 = 現金 + 現貨市值
            equity_curve.append(total_equity)  # 記錄權益
            
        return {  # 返回回測統計報告
            "strategy": "OctoBot_SmartGrid",  # 策略名稱
            "initial_capital": self.initial_capital,  # 初始本金
            "final_equity": equity_curve[-1],  # 最終資產淨值
            "total_return_pct": (equity_curve[-1] - self.initial_capital) / self.initial_capital * 100.0,  # 總報酬率
            "trades_count": len(trades_log),  # 總成交次數
            "shifts_count": shifts_count,  # 網格平移次數
            "equity_curve": equity_curve  # 權益時序曲線
        }

# ==========================================================
# 2. 策略二：Freqtrade 風格動態通道調倉網格 (Adaptive Channel DCA Grid)
# ==========================================================

class FreqtradeDcaGridStrategy:  # 定義 Freqtrade 動態通道 DCA 網格回測引擎
    def __init__(self, initial_capital: float = 25000.0, max_layers: int = 5,  # 初始化資金與最大加倉層數
                 base_stake_pct: float = 0.15, stake_scale: float = 1.25,  # 初始底倉比例與馬丁階梯乘數
                 spread_bps: float = 3.0, commission_pct: float = 0.0002):  # 點差與佣金費率
        self.initial_capital = initial_capital  # 初始資金
        self.max_layers = max_layers  # 最大加倉次數 (最多 5 層)
        self.base_stake_pct = base_stake_pct  # 每次初始底倉佔總資金比例 (15%)
        self.stake_scale = stake_scale  # 每層加倉金額倍率 (1.25x)
        self.spread_bps = spread_bps  # 點差 (bps)
        self.commission_pct = commission_pct  # 佣金費率

    def run(self, df: pd.DataFrame, atr_period: int = 14, channel_mult: float = 1.8,  # 執行 Freqtrade DCA 網格回測
            step_atr_mult: float = 0.8, tp_atr_mult: float = 1.2, sl_atr_mult: float = 4.0) -> dict:  # 參數配置
        data = df.copy().reset_index(drop=True)  # 複製資料並重設索引
        
        # 指標計算：ATR 與動態通道中軌
        data['tr'] = np.maximum(data['high'] - data['low'],  # 計算真實波幅 TR
                                np.maximum(np.abs(data['high'] - data['close'].shift(1)),  # 高與昨收之差
                                           np.abs(data['low'] - data['close'].shift(1))))  # 低與昨收之差
        data['atr'] = data['tr'].rolling(window=atr_period).mean().bfill()  # 計算滾動 ATR 並向前補值
        data['ema20'] = data['close'].ewm(span=20).mean()  # 20 EMA 作為網格動態中線
        data['lower_channel'] = data['ema20'] - (data['atr'] * channel_mult)  # 動態通道下軌 (進場超跌線)
        data['ema200'] = data['close'].ewm(span=200).mean()  # 200 EMA 大趨勢過濾線
        
        cash = self.initial_capital  # 當前現金
        shares = 0.0  # 當前持股數量
        layer_count = 0  # 當前加倉層數
        avg_entry_price = 0.0  # 持倉加權均價
        equity_curve = []  # 權益時序
        trades_log = []  # 成交紀錄
        
        for idx, row in data.iterrows():  # 逐根 K 線模擬事件驅動調倉
            close, high, low = row['close'], row['high'], row['low']  # 取得價格
            atr = row['atr']  # 取得當前 ATR
            lower_chan = row['lower_channel']  # 取得通道下軌
            ema200 = row['ema200']  # 取得大趨勢均線
            half_spread = close * (self.spread_bps / 10000.0) / 2.0  # 點差成本
            
            # 大趨勢過濾：只在價格高於 200 EMA 或貼近均線時開新倉 (避開毀滅性單邊崩盤)
            trend_ok = (close >= ema200 * 0.95) if not np.isnan(ema200) else True  # 趨勢過濾判定
            
            # 狀態一：無倉位，尋找通道下軌超跌首開信號
            if layer_count == 0:  # 目前無倉位
                if low <= lower_chan and trend_ok:  # 觸及動態下軌且趨勢正常
                    exec_p = min(close, lower_chan) + half_spread  # 買入成交價
                    stake_val = self.initial_capital * self.base_stake_pct  # 初始底倉金額
                    stake_shares = stake_val / exec_p  # 計算買入股數
                    cost = exec_p * stake_shares * (1.0 + self.commission_pct)  # 總支付金額
                    if cash >= cost:  # 確認資金充足
                        cash -= cost  # 扣減現金
                        shares = stake_shares  # 設定持股
                        layer_count = 1  # 標記第一層已開倉
                        avg_entry_price = exec_p  # 初始化持倉均價
                        trades_log.append({"time": row['datetime'], "action": "ENTRY_L1", "price": exec_p, "qty": stake_shares})  # 紀錄開倉
            
            # 狀態二：已有持倉，評估 adjust_trade_position (加倉 DCA 或止盈/止損平倉)
            else:  # 已有持倉中
                # 1. 檢驗全域獲利出場 (Take Profit)
                tp_target = avg_entry_price + (atr * tp_atr_mult)  # 計算動態均價止盈價
                if high >= tp_target:  # 觸及全域止盈目標
                    exec_p = tp_target - half_spread  # 賣出成交價
                    revenue = exec_p * shares * (1.0 - self.commission_pct)  # 總賣出所得
                    cash += revenue  # 資金回籠
                    trades_log.append({"time": row['datetime'], "action": "EXIT_TP", "price": exec_p, "qty": shares, "pnl": revenue - (avg_entry_price * shares)})  # 紀錄止盈
                    shares = 0.0  # 清空持股
                    layer_count = 0  # 重設加倉層數
                    avg_entry_price = 0.0  # 重設持倉均價
                
                # 2. 檢驗全域停損保護 (Hard Stop Loss)
                elif low <= avg_entry_price - (atr * sl_atr_mult):  # 觸及全域防禦停損
                    exec_p = (avg_entry_price - (atr * sl_atr_mult)) - half_spread  # 停損成交價
                    revenue = exec_p * shares * (1.0 - self.commission_pct)  # 賣出金額
                    cash += revenue  # 現金回籠
                    trades_log.append({"time": row['datetime'], "action": "EXIT_SL", "price": exec_p, "qty": shares, "pnl": revenue - (avg_entry_price * shares)})  # 紀錄停損
                    shares = 0.0  # 清空持股
                    layer_count = 0  # 重設加倉層數
                    avg_entry_price = 0.0  # 重設持倉均價
                
                # 3. 檢驗逐層補倉 (adjust_trade_position)
                elif layer_count < self.max_layers:  # 尚未達到最大加倉層數
                    next_dca_trigger = avg_entry_price - (layer_count * atr * step_atr_mult)  # 計算下一層加倉觸發價
                    if low <= next_dca_trigger:  # 價格下跌至補倉檔位
                        exec_p = next_dca_trigger + half_spread  # 加倉成交價
                        layer_stake_val = (self.initial_capital * self.base_stake_pct) * (self.stake_scale ** layer_count)  # 階梯計算加倉資金
                        add_shares = layer_stake_val / exec_p  # 加倉股數
                        cost = exec_p * add_shares * (1.0 + self.commission_pct)  # 加倉總成本
                        if cash >= cost:  # 確認現金充足
                            cash -= cost  # 扣減現金
                            total_spent = (avg_entry_price * shares) + (exec_p * add_shares)  # 計算累計總投入
                            shares += add_shares  # 累加持股
                            avg_entry_price = total_spent / shares  # 重新計算持倉加權均價
                            layer_count += 1  # 增加層數計數
                            trades_log.append({"time": row['datetime'], "action": f"DCA_L{layer_count}", "price": exec_p, "qty": add_shares})  # 紀錄加倉
            
            # 計算本根 K 線結束後的總資產
            total_equity = cash + (shares * close)  # 總淨值 = 現金 + 持股市值
            equity_curve.append(total_equity)  # 記錄淨值
            
        return {  # 返回統計結果
            "strategy": "Freqtrade_ChannelDCA",  # 策略名稱
            "initial_capital": self.initial_capital,  # 初始本金
            "final_equity": equity_curve[-1],  # 最終資產
            "total_return_pct": (equity_curve[-1] - self.initial_capital) / self.initial_capital * 100.0,  # 總報酬率
            "trades_count": len(trades_log),  # 總交易調倉次數
            "equity_curve": equity_curve  # 淨值曲線
        }

# ==========================================================
# 3. 績效評估指標計算器
# ==========================================================

def calculate_metrics(equity_series: List[float], initial_capital: float, bar_per_year: float = 252 * 78) -> dict:  # 計算專業量化績效指標
    eq = np.array(equity_series)  # 轉為 numpy 陣列
    total_ret = (eq[-1] - initial_capital) / initial_capital  # 總報酬率
    n_bars = len(eq)  # 總 K 線根數
    years = max(0.1, n_bars / bar_per_year)  # 計算折合年數 (美股 5m 每天 78 根)
    cagr = ((eq[-1] / initial_capital) ** (1.0 / years) - 1.0) if eq[-1] > 0 else -1.0  # 計算年化複合增長率 CAGR
    
    # 計算最大回撤 (Max Drawdown)
    cummax = np.maximum.accumulate(eq)  # 歷史累計最高淨值
    drawdowns = (eq - cummax) / cummax  # 各時間點回撤比例
    max_dd = float(np.min(drawdowns))  # 最大回撤百分比
    
    # 計算夏普率 (Sharpe Ratio)
    returns = np.diff(eq) / eq[:-1]  # 每根 K 線收益率
    mean_r = np.mean(returns)  # 平均單期收益率
    std_r = np.std(returns) if np.std(returns) > 1e-8 else 1e-8  # 單期收益波動率
    sharpe = float(mean_r / std_r * np.sqrt(bar_per_year))  # 年化夏普值
    
    # 計算卡瑪比率 (Calmar Ratio)
    calmar = float(cagr / abs(max_dd)) if abs(max_dd) > 1e-4 else 0.0  # 年化收益 / 最大回撤
    
    return {  # 回傳指標字典
        "Total Return %": round(total_ret * 100.0, 2),  # 總報酬率 %
        "CAGR %": round(cagr * 100.0, 2),  # 年化收益率 %
        "Max DD %": round(max_dd * 100.0, 2),  # 最大回撤 %
        "Sharpe": round(sharpe, 2),  # 年化夏普比率
        "Calmar": round(calmar, 2),  # 卡瑪比率
        "Final Equity ($)": round(eq[-1], 2)  # 期末淨值
    }

# ==========================================================
# 4. 主執行流程：多標的跨市場回測與結果彙整
# ==========================================================

def main():  # 主程式進入點
    symbols = ["EQ_AAPL_5m.csv", "EQ_NVDA_5m.csv", "EQ_MSFT_5m.csv", "EQ_TSLA_5m.csv",  # 科技成長股群
               "EQ_AMZN_5m.csv", "EQ_GOOG_5m.csv", "EQ_JPM_5m.csv", "EQ_WMT_5m.csv",  # 傳統龍頭與消費群
               "EQ_KO_5m.csv", "EQ_DIS_5m.csv"]  # 防禦型與娛樂群
    
    results = []  # 存儲所有回測結果
    data_dir = "data_mt5_equities_cfd"  # 資料目錄路徑
    
    octo_strat = OctoBotSmartGridStrategy(initial_capital=25000.0)  # 實例化 OctoBot 策略
    freq_strat = FreqtradeDcaGridStrategy(initial_capital=25000.0)  # 實例化 Freqtrade 策略
    
    print("==========================================================================================")  # 分隔線
    print(f"🚀 開始執行 MT5 美股歷史資料回測 (OctoBot Smart Grid vs Freqtrade Channel DCA)...")  # 輸出啟動訊息
    print("==========================================================================================")  # 分隔線
    
    curves_dict = {}  # 儲存繪圖用淨值曲線
    
    for filename in symbols:  # 遍歷股票清單
        file_path = os.path.join(data_dir, filename)  # 取得完整檔案路徑
        if not os.path.exists(file_path):  # 檢查檔案是否存在
            continue  # 若不存在則跳過
            
        ticker = filename.replace("EQ_", "").replace("_5m.csv", "")  # 解析標的代碼
        df = pd.read_csv(file_path)  # 讀取 CSV 檔案
        
        # 統一欄位名稱
        df.rename(columns={"close": "close", "open": "open", "high": "high", "low": "low", "datetime": "datetime"}, inplace=True)  # 標準化欄位
        df['close'] = df['close'].astype(float)  # 確保收盤價為浮點數
        df['high'] = df['high'].astype(float)  # 確保最高價為浮點數
        df['low'] = df['low'].astype(float)  # 確保最低價為浮點數
        df['open'] = df['open'].astype(float)  # 確保開盤價為浮點數
        
        # 執行 OctoBot 策略回測
        res_octo = octo_strat.run(df, grid_span_pct=0.08, shift_threshold=0.02)  # 運行回測
        m_octo = calculate_metrics(res_octo["equity_curve"], 25000.0)  # 計算指標
        
        # 執行 Freqtrade 策略回測
        res_freq = freq_strat.run(df)  # 運行回測
        m_freq = calculate_metrics(res_freq["equity_curve"], 25000.0)  # 計算指標
        
        # 計算單純 Buy & Hold 基準
        bh_return = (df.iloc[-1]['close'] - df.iloc[0]['close']) / df.iloc[0]['close'] * 100.0  # 買入持有報酬率
        
        results.append({  # 紀錄結果摘要
            "Symbol": ticker,  # 標的代號
            "Bars": len(df),  # 總 K 線數
            "B&H Ret %": round(bh_return, 1),  # 買入持有收益率
            "Octo Ret %": m_octo["Total Return %"],  # OctoBot 總收益 %
            "Octo MaxDD %": m_octo["Max DD %"],  # OctoBot 最大回撤 %
            "Octo Sharpe": m_octo["Sharpe"],  # OctoBot 夏普比率
            "Octo Trades": res_octo["trades_count"],  # OctoBot 成交次數
            "Freq Ret %": m_freq["Total Return %"],  # Freqtrade 總收益 %
            "Freq MaxDD %": m_freq["Max DD %"],  # Freqtrade 最大回撤 %
            "Freq Sharpe": m_freq["Sharpe"],  # Freqtrade 夏普比率
            "Freq Trades": res_freq["trades_count"]  # Freqtrade 成交次數
        })  # 寫入摘要清單
        
        curves_dict[ticker] = {  # 保存代表性資產淨值曲線以供繪圖
            "Octo": res_octo["equity_curve"],  # Octo 曲線
            "Freq": res_freq["equity_curve"]  # Freq 曲線
        }
        
        print(f"✅ 完成 {ticker:6s} | Octo: Ret {m_octo['Total Return %']:>6.1f}%, DD {m_octo['Max DD %']:>5.1f}% | Freq: Ret {m_freq['Total Return %']:>6.1f}%, DD {m_freq['Max DD %']:>5.1f}%")  # 輸出進度
        
    res_df = pd.DataFrame(results)  # 轉為 DataFrame
    res_df.to_csv("grid_backtest_mt5_results.csv", index=False)  # 匯出 CSV 報告
    print("\n========================= 回測總結統計表 =========================")  # 分隔線
    print(res_df.to_string(index=False))  # 列印完整表格
    
    # 繪製代表性標的資產權益圖
    plt.figure(figsize=(14, 8))  # 建立圖表畫布
    plt.style.use('seaborn-v0_8-darkgrid' if 'seaborn-v0_8-darkgrid' in plt.style.available else 'default')  # 設定圖表樣式
    
    sub_tickers = ["NVDA", "AAPL", "MSFT", "TSLA"]  # 選擇 4 檔代表性股票
    for i, t in enumerate(sub_tickers):  # 繪製子圖
        if t in curves_dict:  # 確保有資料
            plt.subplot(2, 2, i+1)  # 選擇子圖位置
            plt.plot(curves_dict[t]["Octo"], label="OctoBot Smart Grid", color="#1f77b4", lw=1.5)  # 繪製 Octo 淨值線
            plt.plot(curves_dict[t]["Freq"], label="Freqtrade Channel DCA", color="#ff7f0e", lw=1.5)  # 繪製 Freq 淨值線
            plt.title(f"{t} Grid Performance Comparison", fontsize=12, fontweight="bold")  # 設定標題
            plt.xlabel("Bars (5-min)", fontsize=9)  # 設定 X 軸標籤
            plt.ylabel("Portfolio Value ($)", fontsize=9)  # 設定 Y 軸標籤
            plt.legend(fontsize=8)  # 顯示圖例
            
    plt.tight_layout()  # 自動調整佈局
    plt.savefig("grid_performance_comparison.png", dpi=300)  # 儲存高畫質圖片
    print("📈 圖表已儲存至 grid_performance_comparison.png")  # 提示圖表完成

if __name__ == "__main__":  # 程式主入口判斷
    main()  # 啟動主函數
