import os  # 引入作業系統路徑模組
import glob  # 引入檔案搜尋模組
import numpy as np  # 引入數值計算模組
import pandas as pd  # 引入資料分析處理模組
from typing import List, Dict  # 引入型別提示模組
import matplotlib.pyplot as plt  # 引入視覺化繪圖模組

# ==========================================================
# 1. 美股適應型：巨觀趨勢動態無限網格 (Macro Trend-Following Infinity Grid)
# ==========================================================

class MacroInfinityGrid:  # 定義美股趨勢無限網格策略類別
    def __init__(self, initial_capital: float = 25000.0, grid_step_pct: float = 0.035,  # 初始化資金與每格利潤間距 (3.5% 寬網格抗手續費)
                 reserve_cash_pct: float = 0.3, spread_bps: float = 3.0, commission_pct: float = 0.0002):  # 預留現金與手續費
        self.initial_capital = initial_capital  # 初始資產
        self.grid_step_pct = grid_step_pct  # 網格間距百分比 (3.5%)
        self.reserve_cash_pct = reserve_cash_pct  # 預留防禦現金比例 (30%)
        self.spread_bps = spread_bps  # 交易點差 (bps)
        self.commission_pct = commission_pct  # 佣金比例

    def run(self, df: pd.DataFrame) -> dict:  # 執行無限網格回測
        data = df.copy().reset_index(drop=True)  # 複製並重設索引
        
        # 1. 巨觀濾網：200 週期均線判定多空體制
        data['sma200'] = data['close'].rolling(window=200).mean().bfill()  # 計算 200 均線
        data['atr20'] = (data['high'] - data['low']).rolling(window=20).mean().bfill()  # 計算波動度
        
        # 初始化部位
        p0 = data.iloc[0]['close']  # 起始價格
        active_cap = self.initial_capital * (1.0 - self.reserve_cash_pct)  # 用於網格運作的活躍資金
        cash = self.initial_capital * self.reserve_cash_pct + (active_cap * 0.5)  # 總現金 (保留現金 + 50% 網格現金)
        shares = (active_cap * 0.5) / p0  # 初始持有股票數量
        
        base_anchor_price = p0  # 基準錨定價
        equity_curve = []  # 權益曲線
        trades = []  # 成交紀錄
        
        half_spread = self.spread_bps / 10000.0 / 2.0  # 半點差
        
        for idx, row in data.iterrows():  # 逐根 K 線遍歷
            close, high, low = row['close'], row['high'], row['low']  # 取得當前價格
            sma200 = row['sma200']  # 取得巨觀趨勢線
            
            # 向上獲利觸發：價格上漲超過 1 格間距
            upper_trigger = base_anchor_price * (1.0 + self.grid_step_pct)  # 向上止盈線
            lower_trigger = base_anchor_price * (1.0 - self.grid_step_pct)  # 向下補倉線
            
            # 向上出清部分並將錨點上移 (Infinity Trailing)
            if high >= upper_trigger:  # 觸及上方網格線
                sell_shares = shares * (self.grid_step_pct / (1.0 + self.grid_step_pct))  # 鎖定增值利潤賣出股數
                if sell_shares > 0:  # 確保有可賣股數
                    exec_p = upper_trigger * (1.0 - half_spread)  # 賣出成交價
                    revenue = exec_p * sell_shares * (1.0 - self.commission_pct)  # 實收金額
                    cash += revenue  # 增加現金
                    shares -= sell_shares  # 扣減持股
                    base_anchor_price = upper_trigger  # 基準價格向上錨定 (平移)
                    trades.append({"side": "SELL", "price": exec_p, "shares": sell_shares})  # 紀錄交易
                    
            # 向下低吸補倉 (僅在趨勢未崩壞或超跌時執行)
            elif low <= lower_trigger and close >= sma200 * 0.85:  # 觸及下方網格線且非崩盤態
                buy_val = (shares * close) * self.grid_step_pct  # 計算補倉所需等比例資金
                if cash >= buy_val and buy_val > 50.0:  # 確保現金充裕
                    exec_p = lower_trigger * (1.0 + half_spread)  # 買入成交價
                    buy_shares = buy_val / exec_p  # 買入股數
                    cost = exec_p * buy_shares * (1.0 + self.commission_pct)  # 總支付金額
                    cash -= cost  # 扣除現金
                    shares += buy_shares  # 增加持股
                    base_anchor_price = lower_trigger  # 基準價格向下錨定
                    trades.append({"side": "BUY", "price": exec_p, "shares": buy_shares})  # 紀錄交易
                    
            # 計算淨值
            total_equity = cash + (shares * close)  # 總資產淨值
            equity_curve.append(total_equity)  # 記錄時序
            
        return {"equity_curve": equity_curve, "trades": len(trades)}  # 返回回測成果

# ==========================================================
# 2. 美股適應型：日內 VWAP / ATR 區間均值回歸網格 (Intraday Mean-Reversion Grid)
# ==========================================================

class IntradayVwapGrid:  # 定義日內動態通道網格策略類別
    def __init__(self, initial_capital: float = 25000.0, max_layers: int = 4,  # 初始化資金與最大層數
                 spread_bps: float = 3.0, commission_pct: float = 0.0002):  # 點差與手續費
        self.initial_capital = initial_capital  # 初始本金
        self.max_layers = max_layers  # 最大加倉層數限制
        self.spread_bps = spread_bps  # 交易點差 (bps)
        self.commission_pct = commission_pct  # 佣金比例

    def run(self, df: pd.DataFrame) -> dict:  # 執行日內回測
        data = df.copy().reset_index(drop=True)  # 複製資料
        
        # 指標計算：滾動 50 EMA 與 2.5 倍 ATR 寬通道
        data['ema50'] = data['close'].ewm(span=50).mean()  # 計算中軌
        data['tr'] = np.maximum(data['high'] - data['low'],  # 計算 TR
                                np.maximum(np.abs(data['high'] - data['close'].shift(1)),  # 高與昨收
                                           np.abs(data['low'] - data['close'].shift(1))))  # 低與昨收
        data['atr'] = data['tr'].rolling(window=20).mean().bfill()  # 計算 20 ATR
        data['lower_entry'] = data['ema50'] - (data['atr'] * 2.2)  # 超跌進場下軌 (2.2 ATR)
        
        cash = self.initial_capital  # 當前現金
        shares = 0.0  # 當前持股
        layer = 0  # 當前加倉層數
        avg_entry = 0.0  # 加權成本均價
        equity_curve = []  # 淨值紀錄
        trades = []  # 成交紀錄
        half_spread = self.spread_bps / 10000.0 / 2.0  # 半點差
        
        for idx, row in data.iterrows():  # 逐根 K 線遍歷
            close, high, low = row['close'], row['high'], row['low']  # 取得價格
            ema50, atr = row['ema50'], row['atr']  # 取得指標
            lower_entry = row['lower_entry']  # 取得超跌進場線
            
            if layer == 0:  # 無倉位時尋找開倉機會
                if low <= lower_entry:  # 價格打穿超跌下軌
                    exec_p = min(close, lower_entry) * (1.0 + half_spread)  # 買入價
                    stake = self.initial_capital * 0.20  # 首筆資金 20%
                    buy_shares = stake / exec_p  # 買入股數
                    cost = exec_p * buy_shares * (1.0 + self.commission_pct)  # 總支付金額
                    if cash >= cost:  # 確認現金充足
                        cash -= cost  # 扣減現金
                        shares = buy_shares  # 建立持倉
                        layer = 1  # 進入第 1 層
                        avg_entry = exec_p  # 設定均價
                        trades.append({"side": "ENTRY_L1", "price": exec_p})  # 紀錄交易
            else:  # 持倉中
                # 止盈：價格回抽至中軌 EMA 50
                if high >= ema50:  # 觸及均值中線
                    exec_p = ema50 * (1.0 - half_spread)  # 賣出價
                    revenue = exec_p * shares * (1.0 - self.commission_pct)  # 實收現金
                    cash += revenue  # 回收資金
                    trades.append({"side": "EXIT_TP", "price": exec_p})  # 紀錄止盈
                    shares = 0.0  # 清空部位
                    layer = 0  # 重置層數
                    avg_entry = 0.0  # 重置均價
                # 停損：跌破均價超過 3.5 倍 ATR
                elif low <= avg_entry - (atr * 3.5):  # 觸發停損保護
                    exec_p = (avg_entry - atr * 3.5) * (1.0 - half_spread)  # 停損價
                    revenue = exec_p * shares * (1.0 - self.commission_pct)  # 實收金額
                    cash += revenue  # 回收資金
                    trades.append({"side": "EXIT_SL", "price": exec_p})  # 紀錄停損
                    shares = 0.0  # 清空部位
                    layer = 0  # 重置層數
                    avg_entry = 0.0  # 重置均價
                # 分層加倉 DCA
                elif layer < self.max_layers:  # 尚未達加倉上限
                    next_layer_p = avg_entry - (layer * atr * 1.0)  # 下一階加倉價
                    if low <= next_layer_p:  # 觸及加倉價
                        exec_p = next_layer_p * (1.0 + half_spread)  # 買入成交價
                        stake = (self.initial_capital * 0.20) * 1.2  # 加倉金額
                        add_shares = stake / exec_p  # 加倉股數
                        cost = exec_p * add_shares * (1.0 + self.commission_pct)  # 支付金額
                        if cash >= cost:  # 確認現金充足
                            cash -= cost  # 扣減現金
                            total_spent = (avg_entry * shares) + (exec_p * add_shares)  # 計算總成本
                            shares += add_shares  # 累加股數
                            avg_entry = total_spent / shares  # 重新計算均價
                            layer += 1  # 增加層數
                            trades.append({"side": f"DCA_L{layer}", "price": exec_p})  # 紀錄加倉
                            
            total_equity = cash + (shares * close)  # 計算總資產
            equity_curve.append(total_equity)  # 記錄時序
            
        return {"equity_curve": equity_curve, "trades": len(trades)}  # 返回成果

# ==========================================================
# 3. 執行優化後策略回測並輸出對比
# ==========================================================

def run_optimized_comparison():  # 執行優化策略比較
    symbols = ["EQ_AAPL_5m.csv", "EQ_NVDA_5m.csv", "EQ_MSFT_5m.csv", "EQ_AMZN_5m.csv",  # 標的清單
               "EQ_GOOG_5m.csv", "EQ_JPM_5m.csv", "EQ_WMT_5m.csv", "EQ_KO_5m.csv"]  # 傳統龍頭
    data_dir = "data_mt5_equities_cfd"  # 資料目錄
    
    inf_engine = MacroInfinityGrid(initial_capital=25000.0, grid_step_pct=0.035)  # 實例化寬區間無限網格
    dca_engine = IntradayVwapGrid(initial_capital=25000.0, max_layers=4)  # 實例化通道均值回歸網格
    
    res_list = []  # 儲存結果
    curves = {}  # 儲存繪圖曲線
    
    for filename in symbols:  # 遍歷股票
        path = os.path.join(data_dir, filename)  # 取得路徑
        if not os.path.exists(path):  # 檢查檔案
            continue  # 跳過
        ticker = filename.replace("EQ_", "").replace("_5m.csv", "")  # 解析代號
        df = pd.read_csv(path)  # 讀取資料
        df['close'] = df['close'].astype(float)  # 確保浮點數
        df['high'] = df['high'].astype(float)  # 確保浮點數
        df['low'] = df['low'].astype(float)  # 確保浮點數
        df['open'] = df['open'].astype(float)  # 確保浮點數
        
        # 1. 無限網格回測
        inf_res = inf_engine.run(df)  # 運行
        inf_eq = np.array(inf_res["equity_curve"])  # 轉陣列
        inf_ret = (inf_eq[-1] - 25000.0) / 25000.0 * 100.0  # 總報酬 %
        inf_dd = float(np.min((inf_eq - np.maximum.accumulate(inf_eq)) / np.maximum.accumulate(inf_eq))) * 100.0  # 最大回撤 %
        
        # 2. 均值回歸通道網格回測
        dca_res = dca_engine.run(df)  # 運行
        dca_eq = np.array(dca_res["equity_curve"])  # 轉陣列
        dca_ret = (dca_eq[-1] - 25000.0) / 25000.0 * 100.0  # 總報酬 %
        dca_dd = float(np.min((dca_eq - np.maximum.accumulate(dca_eq)) / np.maximum.accumulate(dca_eq))) * 100.0  # 最大回撤 %
        
        # 3. 基準買入持有
        bh_ret = (df.iloc[-1]['close'] - df.iloc[0]['close']) / df.iloc[0]['close'] * 100.0  # B&H 報酬 %
        
        res_list.append({  # 記錄摘要
            "Symbol": ticker,  # 標的
            "B&H %": round(bh_ret, 1),  # 買入持有 %
            "Infinity Ret %": round(inf_ret, 1),  # 無限網格收益 %
            "Infinity MaxDD %": round(inf_dd, 1),  # 無限網格最大回撤 %
            "Infinity Trades": inf_res["trades"],  # 無限網格交易數
            "Channel DCA Ret %": round(dca_ret, 1),  # 通道 DCA 收益 %
            "Channel DCA MaxDD %": round(dca_dd, 1),  # 通道 DCA 最大回撤 %
            "Channel Trades": dca_res["trades"]  # 通道 DCA 交易數
        })  # 寫入清單
        
        curves[ticker] = {"Infinity": inf_eq, "Channel": dca_eq, "Price": df['close'].values}  # 保存曲線
        print(f"🌟 {ticker:6s} | Infinity: +{inf_ret:>6.1f}% (DD {inf_dd:>5.1f}%, {inf_res['trades']} trades) | DCA: +{dca_ret:>6.1f}% (DD {dca_dd:>5.1f}%, {dca_res['trades']} trades)")  # 輸出進度
        
    summary_df = pd.DataFrame(res_list)  # 轉 DataFrame
    summary_df.to_csv("grid_optimized_results.csv", index=False)  # 輸出 CSV
    print("\n========================= 優化後美股網格策略對比表 =========================")  # 分隔線
    print(summary_df.to_string(index=False))  # 列印表格
    
    # 繪製圖表
    plt.figure(figsize=(14, 8))  # 畫布尺寸
    plt.style.use('seaborn-v0_8-darkgrid' if 'seaborn-v0_8-darkgrid' in plt.style.available else 'default')  # 樣式
    
    plot_tickers = ["NVDA", "AAPL", "MSFT", "AMZN"]  # 選定 4 檔繪圖
    for i, t in enumerate(plot_tickers):  # 循環繪製
        if t in curves:  # 確保存在
            plt.subplot(2, 2, i+1)  # 子圖佈局
            plt.plot(curves[t]["Infinity"], label="Macro Infinity Grid (Octo-Style)", color="#2ca02c", lw=1.6)  # 無限網格線
            plt.plot(curves[t]["Channel"], label="Channel Reversion Grid (Freq-Style)", color="#1f77b4", lw=1.6)  # 通道網格線
            plt.title(f"{t} - Optimized Equity Grid Strategy", fontsize=12, fontweight="bold")  # 標題
            plt.xlabel("Bars (5-min)", fontsize=9)  # X 軸
            plt.ylabel("Portfolio Value ($)", fontsize=9)  # Y 軸
            plt.legend(fontsize=8)  # 圖例
            
    plt.tight_layout()  # 自動緊湊排版
    plt.savefig("grid_optimized_performance.png", dpi=300)  # 儲存圖片
    print("📈 優化曲線圖已儲存至 grid_optimized_performance.png")  # 完成提示

if __name__ == "__main__":  # 主入口
    run_optimized_comparison()  # 啟動
