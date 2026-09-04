import os  # 引入作業系統路徑管理模組
import numpy as np  # 引入數值計算模組
import pandas as pd  # 引入資料處理與分析模組
from typing import List, Dict, Optional  # 引入型別提示模組
import matplotlib.pyplot as plt  # 引入圖表繪製模組

# ==========================================================
# 1. 載入 MT5 真實點差與隔夜利息設定
# ==========================================================

SPREAD_MAP = pd.read_csv("mt5_spread_by_symbol.csv").set_index("symbol")["avg_spread_bps"].to_dict()  # 讀取真實平均點差表 (bps)
try:  # 嘗試以 utf-8 讀取 Swap 利率表
    SWAP_DF = pd.read_csv("data_mt5_equities_cfd/swap_rates.csv", encoding="utf-8").set_index("symbol")  # 讀取隔夜利息設定表
except Exception:  # 若 utf-8 解碼失敗
    SWAP_DF = pd.read_csv("data_mt5_equities_cfd/swap_rates.csv", encoding="latin1").set_index("symbol")  # 改以 latin1 編碼讀取

def get_symbol_swap_config(symbol: str) -> dict:  # 獲取品種隔夜利息配置函數
    if symbol in SWAP_DF.index:  # 若品種在 Swap 表中
        row = SWAP_DF.loc[symbol]  # 取得該品種資料列
        if isinstance(row, pd.DataFrame):  # 若有多筆重複
            row = row.iloc[0]  # 取第一筆
        return {  # 回傳設定字典
            "swap_long": float(row["swap_long"]),  # 做多每日利息點數
            "swap_short": float(row["swap_short"]),  # 做空每日利息點數
            "point": float(row["point"]),  # 最小點數單位
            "tick_size": float(row["tick_size"]),  # 跳動點大小
            "tick_val": float(row["tick_value"]),  # 單跳價值
            "rollover_day": int(row["swap_rollover3days"])  # 3 倍利息結算日
        }  # 字典結尾
    return {  # 預設通用設定
        "swap_long": -5.0,  # 預設多單點數
        "swap_short": -3.5,  # 預設空單點數
        "point": 0.01,  # 預設點大小
        "tick_size": 0.01,  # 預設跳動大小
        "tick_val": 0.01,  # 預設跳動價值
        "rollover_day": 5  # 預設週五 3 倍利息
    }  # 字典結尾

# ==========================================================
# 2. 全真實成本自適應通道網格回測引擎 (Real All-Costs Grid Engine)
# ==========================================================

class RealAllCostsAdaptiveGrid:  # 定義全真實成本通道網格策略類別
    def __init__(self, initial_capital: float = 25000.0, base_order_usd: float = 1500.0,  # 設定初始本金 $25,000 與首單 $1,500
                 max_dca_layers: int = 4, channel_k: float = 2.0, tp_mult: float = 1.0,  # 最多加倉 4 次、通道 2.0 ATR、止盈 1.0 ATR
                 commission_bps: float = 2.0):  # 設定單邊手續費 2 bps (0.02%)
        self.initial_capital = initial_capital  # 初始本金
        self.base_order_usd = base_order_usd  # 基礎下單金額
        self.max_dca_layers = max_dca_layers  # 最大加倉層數
        self.channel_k = channel_k  # 通道倍數
        self.tp_mult = tp_mult  # 止盈倍數
        self.commission_rate = commission_bps / 10000.0  # 手續費費率換算

    def run(self, df: pd.DataFrame, symbol: str, default_spread_bps: float) -> dict:  # 執行單一品種回測函數
        data = df.copy().reset_index(drop=True)  # 複製資料並重設索引
        data['datetime'] = pd.to_datetime(data['datetime'])  # 轉換時間欄位為 datetime
        data['date'] = data['datetime'].dt.date  # 取得日期部分
        data['weekday'] = data['datetime'].dt.weekday  # 取得星期幾 (0=週一, 4=週五)
        
        # 計算 ATR 與 50 EMA 中軌
        data['tr'] = np.maximum(data['high'] - data['low'],  # 計算 TR 高低差
                                np.maximum(np.abs(data['high'] - data['close'].shift(1)),  # 高與昨收差
                                           np.abs(data['low'] - data['close'].shift(1))))  # 低與昨收差
        data['atr'] = data['tr'].rolling(window=20).mean().bfill()  # 20 ATR
        data['ema50'] = data['close'].ewm(span=50).mean()  # 50 EMA
        data['lower_band'] = data['ema50'] - (data['atr'] * self.channel_k)  # 下軌進場線
        
        swap_cfg = get_symbol_swap_config(symbol)  # 取得品種利息參數
        swap_pts_long = swap_cfg["swap_long"]  # 多單利息點數
        point = swap_cfg["point"]  # 最小點
        tick_size = swap_cfg["tick_size"] if swap_cfg["tick_size"] > 0 else point  # 跳動點
        tick_val = swap_cfg["tick_val"]  # 單跳價值
        daily_swap_cost_per_share = abs(swap_pts_long * (point / tick_size) * tick_val)  # 每股每日隔夜利息扣費金額
        
        cash = self.initial_capital  # 當前現金
        shares = 0.0  # 持股數量
        layer = 0  # 目前加倉層數
        avg_entry_price = 0.0  # 持倉均價
        last_date = None  # 上一根 K 線日期
        
        # 成本統計累積變數
        total_spread_cost = 0.0  # 累計點差成本
        total_commission_cost = 0.0  # 累計手續費成本
        total_swap_cost = 0.0  # 累計隔夜利息成本
        total_trades = 0  # 總交易次數
        winning_trades = 0  # 獲利交易次數
        
        equity_curve = []  # 淨值曲線列表
        trades_log = []  # 交易明細列表
        
        for idx, row in data.iterrows():  # 逐根 K 線遍歷
            close, high, low = row['close'], row['high'], row['low']  # 取得價格
            atr, ema50, lower_band = row['atr'], row['ema50'], row['lower_band']  # 取得通道指標
            cur_date = row['date']  # 當前日期
            cur_weekday = row['weekday']  # 當前星期
            
            # 處理跨日隔夜利息 (Swap)
            if last_date is not None and cur_date != last_date and shares > 0:  # 若換日且持有部位
                mult = 3.0 if cur_weekday == swap_cfg["rollover_day"] else 1.0  # 若為 3 倍利息日則乘 3
                day_swap = daily_swap_cost_per_share * shares * mult  # 計算當日隔夜利息扣款
                cash -= day_swap  # 從現金扣除利息
                total_swap_cost += day_swap  # 累計隔夜息支出
                
            last_date = cur_date  # 更新最後日期
            
            bar_spread_bps = row['spread_bps'] if ('spread_bps' in row and row['spread_bps'] > 0) else default_spread_bps  # 取得該根真實 Spread
            half_spread_ratio = (bar_spread_bps / 10000.0) / 2.0  # 計算半點差
            
            if layer == 0:  # 無倉位時尋找超跌進場
                if low <= lower_band:  # 觸及下軌超跌
                    exec_price = min(close, lower_band) * (1.0 + half_spread_ratio)  # 買入加半點差
                    buy_qty = self.base_order_usd / exec_price  # 首單股數
                    comm = exec_price * buy_qty * self.commission_rate  # 計算佣金
                    spread_paid = (exec_price - min(close, lower_band)) * buy_qty  # 點差摩擦
                    
                    cost = (exec_price * buy_qty) + comm  # 總支出
                    if cash >= cost:  # 確認資金充足
                        cash -= cost  # 扣減現金
                        shares = buy_qty  # 設定持股
                        layer = 1  # 進入第 1 層
                        avg_entry_price = exec_price  # 記錄持倉均價
                        total_commission_cost += comm  # 累計佣金
                        total_spread_cost += spread_paid  # 累計點差
                        trades_log.append({"type": "ENTRY_L1", "price": exec_price, "qty": buy_qty, "time": row['datetime']})  # 記錄進場
            else:  # 已有倉位
                # 1. 均值回歸止盈
                tp_target = avg_entry_price + (atr * self.tp_mult)  # 止盈目標價
                if high >= tp_target or high >= ema50:  # 觸及止盈目標或中軌
                    exec_price = max(tp_target, ema50) * (1.0 - half_spread_ratio)  # 賣出扣半點差
                    revenue = exec_price * shares  # 賣出總額
                    comm = revenue * self.commission_rate  # 賣出佣金
                    spread_paid = (max(tp_target, ema50) - exec_price) * shares  # 賣出點差
                    
                    cash += (revenue - comm)  # 現金回籠
                    trade_pnl = (exec_price * shares) - (avg_entry_price * shares) - comm  # 該筆交易損益
                    total_commission_cost += comm  # 累計佣金
                    total_spread_cost += spread_paid  # 累計點差
                    total_trades += 1  # 交易次數累加
                    if trade_pnl > 0: winning_trades += 1  # 記錄獲利單
                    
                    trades_log.append({"type": "EXIT_TP", "price": exec_price, "qty": shares, "pnl": trade_pnl, "time": row['datetime']})  # 記錄止盈
                    shares = 0.0  # 清空部位
                    layer = 0  # 重設層數
                    avg_entry_price = 0.0  # 重設均價
                # 2. 嚴格防禦停損 (跌破均價 4.0 ATR)
                elif low <= avg_entry_price - (atr * 4.0):  # 觸發停損
                    exec_price = (avg_entry_price - (atr * 4.0)) * (1.0 - half_spread_ratio)  # 停損扣點差
                    revenue = exec_price * shares  # 賣出金額
                    comm = revenue * self.commission_rate  # 佣金
                    spread_paid = ((avg_entry_price - (atr * 4.0)) - exec_price) * shares  # 點差
                    
                    cash += (revenue - comm)  # 現金回籠
                    trade_pnl = (exec_price * shares) - (avg_entry_price * shares) - comm  # 停損損益
                    total_commission_cost += comm  # 累計佣金
                    total_spread_cost += spread_paid  # 累計點差
                    total_trades += 1  # 交易次數累加
                    if trade_pnl > 0: winning_trades += 1  # 記錄獲利
                    
                    trades_log.append({"type": "EXIT_SL", "price": exec_price, "qty": shares, "pnl": trade_pnl, "time": row['datetime']})  # 記錄停損
                    shares = 0.0  # 清空持股
                    layer = 0  # 重設層數
                    avg_entry_price = 0.0  # 重設均價
                # 3. 逐階加倉 DCA (每再跌 1.5 ATR 加碼一次)
                elif layer < self.max_dca_layers:  # 未達加倉上限
                    next_trigger = avg_entry_price - (layer * atr * 1.5)  # 下階加倉價
                    if low <= next_trigger:  # 觸及加倉價
                        exec_price = next_trigger * (1.0 + half_spread_ratio)  # 加倉加點差
                        buy_qty = (self.base_order_usd * 1.2) / exec_price  # 遞增加碼股數
                        comm = exec_price * buy_qty * self.commission_rate  # 佣金
                        spread_paid = (exec_price - next_trigger) * buy_qty  # 點差
                        
                        cost = (exec_price * buy_qty) + comm  # 支出
                        if cash >= cost:  # 確認現金充足
                            cash -= cost  # 扣減現金
                            total_spent = (avg_entry_price * shares) + (exec_price * buy_qty)  # 總持倉成本
                            shares += buy_qty  # 累加股數
                            avg_entry_price = total_spent / shares  # 更新持倉均價
                            layer += 1  # 增加層數
                            total_commission_cost += comm  # 累計佣金
                            total_spread_cost += spread_paid  # 累計點差
                            trades_log.append({"type": f"ENTRY_L{layer}", "price": exec_price, "qty": buy_qty, "time": row['datetime']})  # 記錄加倉
                            
            # 計算該根 Bar 總動態資產
            cur_equity = cash + (shares * close)  # 現金加浮動市值
            equity_curve.append(cur_equity)  # 加入淨值時序
            
        final_equity = equity_curve[-1] if equity_curve else self.initial_capital  # 最終淨值
        total_return_pct = ((final_equity - self.initial_capital) / self.initial_capital) * 100.0  # 總報酬率
        
        # 計算最大回撤 (MDD)
        eq_series = pd.Series(equity_curve)  # 轉換為 Series
        peak = eq_series.cummax()  # 累計最高點
        drawdown = (eq_series - peak) / peak  # 計算回撤
        max_dd_pct = abs(drawdown.min()) * 100.0 if not drawdown.empty else 0.0  # 最大回撤百分比
        
        # 計算夏普率 (Sharpe Ratio)
        returns = eq_series.pct_change().dropna()  # 計算每根 K 線收益率
        sharpe = (returns.mean() / returns.std()) * np.sqrt(252 * 26) if returns.std() > 0 else 0.0  # 年化夏普 (15分K)
        win_rate = (winning_trades / total_trades * 100.0) if total_trades > 0 else 0.0  # 交易勝率
        
        return {  # 返回完整回測統計字典
            "symbol": symbol,  # 品種名稱
            "initial_capital": self.initial_capital,  # 初始本金
            "final_equity": final_equity,  # 最終淨值
            "net_profit_usd": final_equity - self.initial_capital,  # 淨利潤金額
            "total_return_pct": total_return_pct,  # 總報酬率
            "max_dd_pct": max_dd_pct,  # 最大回撤
            "sharpe": sharpe,  # 夏普率
            "win_rate": win_rate,  # 勝率
            "total_trades": total_trades,  # 總完成回合次數
            "spread_cost_usd": total_spread_cost,  # 總付出點差
            "commission_cost_usd": total_commission_cost,  # 總付出手續費
            "swap_cost_usd": total_swap_cost,  # 總付出隔夜利息
            "total_friction_cost_usd": total_spread_cost + total_commission_cost + total_swap_cost,  # 總交易摩擦成本
            "equity_curve": equity_curve  # 淨值走勢
        }  # 結束字典

# ==========================================================
# 3. 執行批次回測並匯出報告與圖表
# ==========================================================

def run_all_real_costs_backtests():  # 執行全部品種回測函數
    data_dir = "data_mt5_equities_cfd"  # 資料目錄
    target_symbols = ["AMD", "NVDA", "INTC", "AVGO", "QCOM", "PLTR", "ASML", "JPM", "SNOW", "LMT", "MSFT", "AAPL", "TSLA"]  # 目標美股 CFD
    results = []  # 存放回測結果
    equity_curves_dict = {}  # 存放各品種淨值曲線
    
    engine = RealAllCostsAdaptiveGrid(initial_capital=25000.0, base_order_usd=1500.0, max_dca_layers=4)  # 實例化策略引擎
    
    print("=========================================================================================")  # 分隔線
    print("                MT5 全真實成本回測 (Real Spread + Swap + Commission)                ")  # 標題
    print("=========================================================================================")  # 分隔線
    
    for sym in target_symbols:  # 遍歷目標品種
        file_path = os.path.join(data_dir, f"EQ_{sym}_15m.csv")  # 構建 15 分 K 線路徑
        if not os.path.exists(file_path):  # 若檔案不存在
            continue  # 跳過
            
        df = pd.read_csv(file_path)  # 讀取 K 線資料
        default_spread = SPREAD_MAP.get(sym, 10.0)  # 取得品種平均點差 (bps)
        
        res = engine.run(df, symbol=sym, default_spread_bps=default_spread)  # 執行回測
        results.append({  # 加入結果表
            "品種": sym,  # 品種
            "真實點差(bps)": default_spread,  # 點差
            "總淨利潤($)": round(res["net_profit_usd"], 2),  # 淨利
            "報酬率(%)": round(res["total_return_pct"], 2),  # 報酬
            "最大回撤(%)": round(res["max_dd_pct"], 2),  # 回撤
            "夏普值": round(res["sharpe"], 2),  # 夏普
            "勝率(%)": round(res["win_rate"], 1),  # 勝率
            "交易次數": res["total_trades"],  # 筆數
            "點差支出($)": round(res["spread_cost_usd"], 2),  # 點差
            "手續費($)": round(res["commission_cost_usd"], 2),  # 佣金
            "隔夜利息($)": round(res["swap_cost_usd"], 2),  # 利息
            "總摩擦成本($)": round(res["total_friction_cost_usd"], 2)  # 摩擦總計
        })  # 結束單筆字典
        equity_curves_dict[sym] = res["equity_curve"]  # 儲存淨值走勢
        
    df_res = pd.DataFrame(results)  # 轉換為 DataFrame
    print(df_res.to_string(index=False))  # 印出回測結果總表
    
    df_res.to_csv("mt5_real_all_costs_backtest_results.csv", index=False, encoding="utf-8-sig")  # 輸出 CSV
    print("\n--> 回測成果已匯出至 mt5_real_all_costs_backtest_results.csv")  # 提示匯出成功
    
    # 繪製各品種淨值曲線
    plt.figure(figsize=(14, 8), dpi=300)  # 建立高解析度畫布
    plt.style.use('seaborn-v0_8-darkgrid' if 'seaborn-v0_8-darkgrid' in plt.style.available else 'default')  # 設定圖表風格
    
    for sym, eq in equity_curves_dict.items():  # 遍歷繪製各品種曲線
        plt.plot(eq, label=f"{sym} (Net: ${df_res.loc[df_res['品種']==sym, '總淨利潤($)'].values[0]:,.0f})", lw=1.5)  # 繪製曲線
        
    plt.axhline(25000.0, color='gray', linestyle='--', alpha=0.7, label='Initial Capital ($25,000)')  # 標示本金基準線
    plt.title("MT5 Adaptive DCA Grid - Real All-Costs Performance (Spread + Swap + Commission)", fontsize=14, fontweight='bold')  # 圖表標題
    plt.xlabel("15-Minute Bars Timeline", fontsize=12)  # X 軸標籤
    plt.ylabel("Portfolio Equity ($)", fontsize=12)  # Y 軸標籤
    plt.legend(loc="upper left", bbox_to_anchor=(1.01, 1), fontsize=9)  # 顯示圖例
    plt.tight_layout()  # 自動緊湊排版
    plt.savefig("mt5_real_all_costs_performance.png")  # 儲存圖檔
    print("--> 績效圖表已保存至 mt5_real_all_costs_performance.png")  # 提示圖檔儲存

if __name__ == "__main__":  # 主程式進入點
    run_all_real_costs_backtests()  # 啟動回測
