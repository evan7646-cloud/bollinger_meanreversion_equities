import os  # 引入作業系統路徑模組
import numpy as np  # 引入數值計算模組
import pandas as pd  # 引入資料分析處理模組
import yfinance as yf  # 引入 Yahoo Finance 歷史資料獲取庫
from typing import Dict, List, Tuple  # 引入型別提示模組
import matplotlib.pyplot as plt  # 引入視覺化繪圖庫

# ==========================================================
# 1. 盤整型貨幣對專屬點差表 (Real FX Spreads in bps)
# ==========================================================

FX_SPREADS_BPS = {  # 外匯貨幣對實盤點差設定 (以 bps 為單位)
    "AUDNZD=X": 2.2,  # 澳幣/紐幣 (典型協整盤整對，點差約 2.2 bps / 2.4 pips)
    "EURGBP=X": 1.5,  # 歐元/英鎊 (地緣經貿緊密，點差約 1.5 bps / 1.3 pips)
    "EURCHF=X": 1.8,  # 歐元/瑞郎 (長期區間震盪，點差約 1.8 bps / 1.7 pips)
    "USDCAD=X": 1.6,  # 美元/加幣 (北美商品共振，點差約 1.6 bps / 2.0 pips)
    "EURUSD=X": 0.8   # 歐元/美元 (全球流動性最高，點差約 0.8 bps / 0.8 pips)
}  # 點差字典結束

# ==========================================================
# 2. 策略一：雙向對稱均值回歸網格 (Dual-Sided Mean-Reverting Grid)
# ==========================================================

class DualSidedFxGrid:  # 定義外匯雙向多空對稱網格策略類別
    def __init__(self, initial_capital: float = 25000.0, order_size_usd: float = 1500.0,  # 初始資金與單筆金額
                 max_layers_per_side: int = 5, grid_step_pct: float = 0.004,  # 單邊最多 5 檔、每檔 0.4% (40 pips)
                 commission_pct: float = 0.00002):  # ECN 佣金費率 (0.002%)
        self.initial_capital = initial_capital  # 初始本金
        self.order_size_usd = order_size_usd  # 單格下單金額
        self.max_layers = max_layers_per_side  # 單邊最大加倉層數
        self.grid_step_pct = grid_step_pct  # 網格間距比例 (外匯通常為 0.3%~0.6%)
        self.commission_pct = commission_pct  # 手續費率

    def run(self, df: pd.DataFrame, spread_bps: float) -> dict:  # 執行雙向網格回測
        data = df.copy().reset_index(drop=True)  # 複製資料
        cash = self.initial_capital  # 現金餘額
        
        # 多頭部位狀態
        long_shares = 0.0  # 多頭持倉量
        long_avg_p = 0.0  # 多頭持倉均價
        long_layers = 0  # 多頭已開層數
        
        # 空頭部位狀態
        short_shares = 0.0  # 空頭持倉量
        short_avg_p = 0.0  # 空頭持倉均價
        short_layers = 0  # 空頭已開層數
        
        # 指標：中軌 100 EMA 作為價值中樞
        data['ema100'] = data['close'].ewm(span=100).mean()  # 計算 100 EMA 基準中心線
        data['atr'] = (data['high'] - data['low']).rolling(window=24).mean().bfill()  # 波動度
        
        half_spread_ratio = (spread_bps / 10000.0) / 2.0  # 半點差
        equity_curve = []  # 淨值曲線
        trades = []  # 成交明細
        total_friction = 0.0  # 累計摩擦成本
        
        for idx, row in data.iterrows():  # 逐根 K 線遍歷
            close, high, low = row['close'], row['high'], row['low']  # 當前 K 線價格
            center_p = row['ema100']  # 當前動態中樞價
            
            # --------------------------------------------------
            # A. 多頭網格邏輯 (中軌下方逢低佈局多單，回抽中軌或反彈獲利了結)
            # --------------------------------------------------
            if long_layers == 0:  # 無多單時，跌破中軌 0.4% 開第一層
                if low <= center_p * (1.0 - self.grid_step_pct):  # 觸及多單首開線
                    exec_p = center_p * (1.0 - self.grid_step_pct) * (1.0 + half_spread_ratio)  # 加上點差買價
                    buy_qty = self.order_size_usd / exec_p  # 買入多頭單位
                    fric = (exec_p * buy_qty * half_spread_ratio * 2.0) + (exec_p * buy_qty * self.commission_pct)  # 摩擦
                    cost = (exec_p * buy_qty) * (1.0 + self.commission_pct)  # 扣減保證金
                    if cash >= cost:  # 確認資金充足
                        cash -= cost  # 扣除資金
                        long_shares = buy_qty  # 設定持倉
                        long_avg_p = exec_p  # 設定均價
                        long_layers = 1  # 標記第 1 層
                        total_friction += fric  # 累計摩擦
                        trades.append({"type": "LONG_L1", "price": exec_p})  # 記錄
            else:  # 持有多單中
                # 多頭止盈：價格回抽至中軌或均價上方 1 格
                if high >= max(center_p, long_avg_p * (1.0 + self.grid_step_pct)):  # 觸發多頭止盈
                    exec_p = max(center_p, long_avg_p * (1.0 + self.grid_step_pct)) * (1.0 - half_spread_ratio)  # 賣出價
                    revenue = (exec_p * long_shares) * (1.0 - self.commission_pct)  # 實收金額
                    fric = (exec_p * long_shares * half_spread_ratio * 2.0) + (exec_p * long_shares * self.commission_pct)  # 摩擦
                    cash += revenue  # 資金回籠
                    total_friction += fric  # 累加摩擦
                    trades.append({"type": "LONG_TP", "price": exec_p, "pnl": revenue - (long_avg_p * long_shares)})  # 記錄止盈
                    long_shares, long_layers, long_avg_p = 0.0, 0, 0.0  # 清空多頭狀態
                # 多頭加倉：下跌每滿 1 格加一檔
                elif long_layers < self.max_layers:  # 尚未達到多頭上限
                    next_long_p = long_avg_p * (1.0 - self.grid_step_pct)  # 下一階多單加倉價
                    if low <= next_long_p:  # 觸及加倉價
                        exec_p = next_long_p * (1.0 + half_spread_ratio)  # 買入成交價
                        buy_qty = self.order_size_usd / exec_p  # 加倉單位
                        fric = (exec_p * buy_qty * half_spread_ratio * 2.0) + (exec_p * buy_qty * self.commission_pct)  # 摩擦
                        cost = (exec_p * buy_qty) * (1.0 + self.commission_pct)  # 扣款金額
                        if cash >= cost:  # 確認資金充裕
                            cash -= cost  # 扣除資金
                            tot_val = (long_avg_p * long_shares) + (exec_p * buy_qty)  # 累計總成本
                            long_shares += buy_qty  # 增加持倉
                            long_avg_p = tot_val / long_shares  # 重新計算持倉均價
                            long_layers += 1  # 增加層數
                            total_friction += fric  # 累加摩擦
                            trades.append({"type": f"LONG_L{long_layers}", "price": exec_p})  # 記錄加倉
                            
            # --------------------------------------------------
            # B. 空頭網格邏輯 (中軌上方逢高佈局空單，回抽中軌或拉回獲利了結)
            # --------------------------------------------------
            if short_layers == 0:  # 無空單時，突破中軌 0.4% 開第一層
                if high >= center_p * (1.0 + self.grid_step_pct):  # 觸及空單首開線
                    exec_p = center_p * (1.0 + self.grid_step_pct) * (1.0 - half_spread_ratio)  # 扣除點差放空價
                    short_qty = self.order_size_usd / exec_p  # 放空單位
                    fric = (exec_p * short_qty * half_spread_ratio * 2.0) + (exec_p * short_qty * self.commission_pct)  # 摩擦
                    margin_held = (exec_p * short_qty)  # 鎖定空頭保證金
                    if cash >= margin_held:  # 確認保證金充足
                        short_shares = short_qty  # 設定空頭持倉
                        short_avg_p = exec_p  # 設定空頭均價
                        short_layers = 1  # 標記第 1 層
                        total_friction += fric  # 累加摩擦
                        trades.append({"type": "SHORT_L1", "price": exec_p})  # 記錄放空
            else:  # 持有空單中
                # 空頭止盈：價格跌回中軌或均價下方 1 格
                if low <= min(center_p, short_avg_p * (1.0 - self.grid_step_pct)):  # 觸發空頭止盈
                    exec_p = min(center_p, short_avg_p * (1.0 - self.grid_step_pct)) * (1.0 + half_spread_ratio)  # 平空買入價
                    short_pnl = (short_avg_p - exec_p) * short_shares - (exec_p * short_shares * self.commission_pct)  # 空頭損益
                    fric = (exec_p * short_shares * half_spread_ratio * 2.0) + (exec_p * short_shares * self.commission_pct)  # 摩擦
                    cash += short_pnl  # 結算損益至現金
                    total_friction += fric  # 累加摩擦
                    trades.append({"type": "SHORT_TP", "price": exec_p, "pnl": short_pnl})  # 記錄空頭止盈
                    short_shares, short_layers, short_avg_p = 0.0, 0, 0.0  # 清空空頭狀態
                # 空頭加倉：上漲每滿 1 格加一檔
                elif short_layers < self.max_layers:  # 尚未達空頭上限
                    next_short_p = short_avg_p * (1.0 + self.grid_step_pct)  # 下一階空單加倉價
                    if high >= next_short_p:  # 觸及加倉價
                        exec_p = next_short_p * (1.0 - half_spread_ratio)  # 成交放空價
                        short_qty = self.order_size_usd / exec_p  # 加倉單位
                        fric = (exec_p * short_qty * half_spread_ratio * 2.0) + (exec_p * short_qty * self.commission_pct)  # 摩擦
                        tot_val = (short_avg_p * short_shares) + (exec_p * short_qty)  # 累計名目成本
                        short_shares += short_qty  # 增加空單量
                        short_avg_p = tot_val / short_shares  # 重新計算空頭均價
                        short_layers += 1  # 增加層數
                        total_friction += fric  # 累加摩擦
                        trades.append({"type": f"SHORT_L{short_layers}", "price": exec_p})  # 記錄加倉
                        
            # 計算當前總淨值 (現金 + 多頭未實現損益 + 空頭未實現損益)
            unrealized_long = (long_shares * close) - (long_shares * long_avg_p) if long_shares > 0 else 0.0  # 多單浮盈虧
            unrealized_short = (short_avg_p * short_shares) - (close * short_shares) if short_shares > 0 else 0.0  # 空單浮盈虧
            cur_equity = cash + (long_shares * long_avg_p if long_shares > 0 else 0.0) + unrealized_long + unrealized_short  # 總權益
            equity_curve.append(cur_equity)  # 紀錄淨值時序
            
        return {"equity": np.array(equity_curve), "trades": len(trades), "friction": total_friction}  # 返回成果

# ==========================================================
# 3. 執行外匯盤整對回測 (1h 週期，近 700 天大數據)
# ==========================================================

def run_fx_ranging_study():  # 執行外匯盤整對實證研究
    print("==========================================================================================================")  # 分隔線
    print("🌐 下載並回測經典【高盤整度外匯貨幣對】(AUD/NZD, EUR/GBP, EUR/CHF, USD/CAD, EUR/USD)...")  # 標題
    print("==========================================================================================================")  # 分隔線
    
    symbols = ["AUDNZD=X", "EURGBP=X", "EURCHF=X", "USDCAD=X", "EURUSD=X"]  # 外匯清單
    data_1h = yf.download(symbols, period="700d", interval="1h", progress=False)  # 批次下載 700 天 1h 資料
    
    results = []  # 存儲結果清單
    curves = {}  # 存儲繪圖時序
    grid_engine = DualSidedFxGrid(initial_capital=25000.0, order_size_usd=1500.0, max_layers_per_side=5, grid_step_pct=0.0035)  # 實例化策略
    
    for sym in symbols:  # 遍歷貨幣對
        df = pd.DataFrame({  # 擷取單一標的之 OHLC 數據
            'open': data_1h['Open'][sym].dropna(),  # 開盤
            'high': data_1h['High'][sym].dropna(),  # 最高
            'low': data_1h['Low'][sym].dropna(),  # 最低
            'close': data_1h['Close'][sym].dropna()  # 收盤
        }).reset_index()  # 重設索引
        
        if len(df) < 500:  # 檢查長度
            continue  # 跳過
            
        spread = FX_SPREADS_BPS.get(sym, 1.5)  # 取得真實點差 (bps)
        res = grid_engine.run(df, spread_bps=spread)  # 執行雙向網格回測
        
        eq = res["equity"]  # 淨值陣列
        ret_pct = (eq[-1] - 25000.0) / 25000.0 * 100.0  # 總報酬率 %
        max_dd = float(np.min((eq - np.maximum.accumulate(eq)) / np.maximum.accumulate(eq))) * 100.0  # 最大回撤 %
        
        # 計算夏普比率 (以小時 Bar 年化，每年約 6240 根 1h Bar)
        rets = np.diff(eq) / eq[:-1]  # 單期報酬
        sharpe = float(np.mean(rets) / (np.std(rets) + 1e-8) * np.sqrt(6240)) if np.std(rets) > 0 else 0.0  # 年化夏普
        
        # 標的本身淨波動 (Buy & Hold 報酬，驗證是否為無漂移盤整對)
        pair_drift = (df.iloc[-1]['close'] - df.iloc[0]['close']) / df.iloc[0]['close'] * 100.0  # 標的漂移 %
        
        sym_name = sym.replace("=X", "")  # 簡化標的名稱
        results.append({  # 寫入摘要清單
            "Currency Pair": sym_name,  # 貨幣對代號
            "Spread (bps)": spread,  # 點差 (bps)
            "Pair Drift %": round(pair_drift, 2),  # 標的自身漲跌 % (驗證盤整特質)
            "Grid Total Ret %": round(ret_pct, 2),  # 網格總報酬 %
            "Grid MaxDD %": round(max_dd, 2),  # 網格最大回撤 %
            "Sharpe Ratio": round(sharpe, 2),  # 年化夏普比率
            "Total Trades": res["trades"],  # 總成交筆數
            "Friction Drag ($)": round(res["friction"], 1)  # 摩擦成本 ($)
        })  # 寫入結尾
        
        curves[sym_name] = eq  # 儲存曲線
        print(f"✅ {sym_name:7s} | 標的漂移: {pair_drift:>5.2f}% | 雙向網格收益: +{ret_pct:>5.2f}% | 最大回撤: {max_dd:>5.2f}% | 夏普: {sharpe:>4.2f} | 交易: {res['trades']:>4d}筆")  # 輸出
        
    summary_df = pd.DataFrame(results)  # 轉為 DataFrame
    summary_df.to_csv("fx_ranging_grid_results.csv", index=False)  # 匯出 CSV 報告
    print("\n================================ 外匯盤整對雙向網格策略成果表 ================================")  # 分隔線
    print(summary_df.to_string(index=False))  # 列印表格
    
    # 繪製淨值曲線圖
    plt.figure(figsize=(12, 7))  # 設定畫布
    plt.style.use('seaborn-v0_8-darkgrid' if 'seaborn-v0_8-darkgrid' in plt.style.available else 'default')  # 設定樣式
    
    for s_name, eq_arr in curves.items():  # 遍歷繪製各貨幣對曲線
        plt.plot(eq_arr, label=f"{s_name} (Ret +{(eq_arr[-1]-25000)/250:>.1f}%)", lw=1.6)  # 繪製曲線
    plt.axhline(25000, color="gray", linestyle="--", alpha=0.6, label="Base Capital $25,000")  # 基準線
    plt.title("Dual-Sided Grid on Range-Bound FX Pairs (1h, 700 Days)", fontsize=13, fontweight="bold")  # 標題
    plt.xlabel("Bars (1-Hour)", fontsize=10)  # X 軸
    plt.ylabel("Portfolio Equity ($)", fontsize=10)  # Y 軸
    plt.legend(fontsize=9)  # 圖例
    plt.tight_layout()  # 自動排版
    plt.savefig("fx_ranging_performance.png", dpi=300)  # 儲存高畫質圖
    print("📈 外匯網格成果圖已儲存至 fx_ranging_performance.png")  # 提示完成

if __name__ == "__main__":  # 程式主入口
    run_fx_ranging_study()  # 啟動
