import os  # 引入作業系統路徑管理模組
import numpy as np  # 引入數值計算模組
import pandas as pd  # 引入資料處理與分析模組
import yfinance as yf  # 引入歷史外匯資料下載庫
from typing import Dict, List, Tuple  # 引入型別標註模組
import matplotlib.pyplot as plt  # 引入圖表繪製模組

# ==========================================================
# 1. 18 檔常見主要與交叉貨幣對真實點差表 (Real ECN Spreads in bps)
# ==========================================================

ALL_FX_SPREADS = {  # 18 檔常見貨幣對實盤點差設定字典 (bps)
    "EURUSD=X": 0.8,  # 歐美 (0.8 pips)
    "USDJPY=X": 0.9,  # 美日 (1.2 pips)
    "GBPUSD=X": 1.2,  # 鎊美 (1.5 pips)
    "AUDUSD=X": 1.2,  # 澳美 (0.8 pips)
    "USDCAD=X": 1.5,  # 美加 (2.0 pips)
    "USDCHF=X": 1.5,  # 美瑞 (1.3 pips)
    "NZDUSD=X": 1.8,  # 紐美 (1.1 pips)
    "EURGBP=X": 1.5,  # 歐鎊 (1.3 pips)
    "EURJPY=X": 1.4,  # 歐日 (2.2 pips)
    "GBPJPY=X": 2.0,  # 鎊日 (3.8 pips)
    "AUDNZD=X": 2.2,  # 澳紐 (2.4 pips)
    "EURCHF=X": 1.8,  # 歐瑞 (1.7 pips)
    "CADJPY=X": 2.2,  # 加日 (2.4 pips)
    "EURAUD=X": 2.2,  # 歐澳 (3.6 pips)
    "GBPAUD=X": 2.5,  # 鎊澳 (4.8 pips)
    "AUDCAD=X": 2.0,  # 澳加 (1.8 pips)
    "NZDCAD=X": 2.2,  # 紐加 (1.8 pips)
    "CHFJPY=X": 2.2   # 瑞日 (3.8 pips)
}  # 字典結束

# ==========================================================
# 2. 高勝率外匯自適應通道網格策略 (High Win-Rate Adaptive DCA Grid)
# ==========================================================

class HighWinRateFxGrid:  # 定義高勝率外匯自適應網格引擎
    def __init__(self, initial_capital: float = 25000.0, base_stake_usd: float = 2000.0,  # 初始本金 $25,000、底倉 $2,000
                 max_dca_layers: int = 4, stake_multiplier: float = 1.3,  # 最多加倉 4 層、每次 1.3 倍階梯加碼
                 atr_step_mult: float = 1.0, tp_atr_mult: float = 1.0,  # 加倉間距 1.0 ATR、止盈目標 1.0 ATR
                 commission_pct: float = 0.00002):  # ECN 佣金 (0.002%)
        self.initial_capital = initial_capital  # 初始資產
        self.base_stake_usd = base_stake_usd  # 首單基礎金額
        self.max_layers = max_dca_layers  # 最大加倉層數限制
        self.stake_mult = stake_multiplier  # 階梯資金乘數
        self.atr_step_mult = atr_step_mult  # 加倉 ATR 間距倍數
        self.tp_atr_mult = tp_atr_mult  # 均值回歸止盈倍數
        self.commission_pct = commission_pct  # 佣金費率

    def run(self, df: pd.DataFrame, spread_bps: float) -> dict:  # 執行回測邏輯
        data = df.copy().reset_index(drop=True)  # 複製資料並重設索引
        
        # 指標計算：20 週期 ATR 與 50 EMA 基準中軌
        data['tr'] = np.maximum(data['high'] - data['low'],  # 計算 TR
                                np.maximum(np.abs(data['high'] - data['close'].shift(1)),  # 高與昨收
                                           np.abs(data['low'] - data['close'].shift(1))))  # 低與昨收
        data['atr'] = data['tr'].rolling(window=20).mean().bfill()  # 計算 20 ATR
        data['ema50'] = data['close'].ewm(span=50).mean()  # 50 EMA
        data['lower_channel'] = data['ema50'] - (data['atr'] * 2.0)  # 2.0 ATR 超跌進場下軌
        data['upper_channel'] = data['ema50'] + (data['atr'] * 2.0)  # 2.0 ATR 超漲進場上軌
        
        cash = self.initial_capital  # 當前現金餘額
        half_spread_ratio = (spread_bps / 10000.0) / 2.0  # 半點差比例
        
        # 倉位狀態
        pos_side = None  # 當前方向 (LONG 或 SHORT)
        shares = 0.0  # 持倉數量
        layer = 0  # 當前層數
        avg_entry_p = 0.0  # 加權持倉成本均價
        
        closed_trades = []  # 存儲已平倉交易紀錄以計算勝率
        equity_curve = []  # 淨值時序
        total_friction = 0.0  # 摩擦成本
        
        for idx, row in data.iterrows():  # 逐根 Bar 撮合
            close, high, low = row['close'], row['high'], row['low']  # 價格
            atr, ema50 = row['atr'], row['ema50']  # 指標
            lower_ch, upper_ch = row['lower_channel'], row['upper_channel']  # 通道界線
            
            # 狀態 1：無倉位時，等待突破通道觸發首單
            if layer == 0:  # 目前無倉位
                if low <= lower_ch:  # 跌破超跌下軌 ➔ 做多
                    exec_p = min(close, lower_ch) * (1.0 + half_spread_ratio)  # 買入成交價
                    qty = self.base_stake_usd / exec_p  # 首單數量
                    fric = (exec_p * qty * half_spread_ratio * 2.0) + (exec_p * qty * self.commission_pct)  # 摩擦
                    cost = (exec_p * qty) * (1.0 + self.commission_pct)  # 支付金額
                    if cash >= cost:  # 確認資金充足
                        cash -= cost  # 扣款
                        shares = qty  # 持倉
                        pos_side = "LONG"  # 標記多頭
                        layer = 1  # 進入第 1 層
                        avg_entry_p = exec_p  # 設定初始均價
                        total_friction += fric  # 累計摩擦
                elif high >= upper_ch:  # 突破超漲上軌 ➔ 做空
                    exec_p = max(close, upper_ch) * (1.0 - half_spread_ratio)  # 放空成交價
                    qty = self.base_stake_usd / exec_p  # 首單數量
                    fric = (exec_p * qty * half_spread_ratio * 2.0) + (exec_p * qty * self.commission_pct)  # 摩擦
                    margin_held = (exec_p * qty)  # 佔用保證金
                    if cash >= margin_held:  # 確認保證金充裕
                        shares = qty  # 空頭部位
                        pos_side = "SHORT"  # 標記空頭
                        layer = 1  # 進入第 1 層
                        avg_entry_p = exec_p  # 設定初始均價
                        total_friction += fric  # 累計摩擦
                        
            # 狀態 2：持有多頭倉位
            elif pos_side == "LONG":  # 處理多頭加倉與止盈
                tp_target = avg_entry_p + (atr * self.tp_atr_mult)  # 動態均價止盈線
                if high >= tp_target or high >= ema50:  # 觸及止盈目標或回抽中軌
                    exec_p = max(tp_target, ema50) * (1.0 - half_spread_ratio)  # 賣出成交價
                    revenue = (exec_p * shares) * (1.0 - self.commission_pct)  # 實收金額
                    fric = (exec_p * shares * half_spread_ratio * 2.0) + (exec_p * shares * self.commission_pct)  # 摩擦
                    pnl = revenue - (avg_entry_p * shares)  # 實際獲利
                    cash += revenue  # 資金回籠
                    total_friction += fric  # 累加摩擦
                    closed_trades.append({"side": "LONG", "pnl": pnl, "layers": layer, "is_win": pnl > 0})  # 記錄平倉
                    pos_side, shares, layer, avg_entry_p = None, 0.0, 0, 0.0  # 重置倉位
                elif layer < self.max_layers:  # 未達加倉上限
                    next_trigger = avg_entry_p - (layer * atr * self.atr_step_mult)  # 下一階加倉價
                    if low <= next_trigger:  # 觸及加倉價
                        exec_p = next_trigger * (1.0 + half_spread_ratio)  # 買入價
                        stake = self.base_stake_usd * (self.stake_mult ** layer)  # 階梯放大加倉金額
                        add_qty = stake / exec_p  # 加倉股數
                        fric = (exec_p * add_qty * half_spread_ratio * 2.0) + (exec_p * add_qty * self.commission_pct)  # 摩擦
                        cost = (exec_p * add_qty) * (1.0 + self.commission_pct)  # 扣款金額
                        if cash >= cost:  # 確認資金
                            cash -= cost  # 扣款
                            tot_val = (avg_entry_p * shares) + (exec_p * add_qty)  # 累計總支出
                            shares += add_qty  # 累加持股
                            avg_entry_p = tot_val / shares  # 重新計算均價
                            layer += 1  # 增加層數
                            total_friction += fric  # 累加摩擦
                            
            # 狀態 3：持有空頭倉位
            elif pos_side == "SHORT":  # 處理空頭加倉與止盈
                tp_target = avg_entry_p - (atr * self.tp_atr_mult)  # 空頭動態止盈線
                if low <= tp_target or low <= ema50:  # 觸及止盈目標或跌回中軌
                    exec_p = min(tp_target, ema50) * (1.0 + half_spread_ratio)  # 平空買入價
                    pnl = (avg_entry_p - exec_p) * shares - (exec_p * shares * self.commission_pct)  # 實際獲利
                    fric = (exec_p * shares * half_spread_ratio * 2.0) + (exec_p * shares * self.commission_pct)  # 摩擦
                    cash += pnl  # 結算損益
                    total_friction += fric  # 累計摩擦
                    closed_trades.append({"side": "SHORT", "pnl": pnl, "layers": layer, "is_win": pnl > 0})  # 記錄平倉
                    pos_side, shares, layer, avg_entry_p = None, 0.0, 0, 0.0  # 重置倉位
                elif layer < self.max_layers:  # 未達加倉上限
                    next_trigger = avg_entry_p + (layer * atr * self.atr_step_mult)  # 下一階空單加倉價
                    if high >= next_trigger:  # 觸及加倉價
                        exec_p = next_trigger * (1.0 - half_spread_ratio)  # 放空成交價
                        stake = self.base_stake_usd * (self.stake_mult ** layer)  # 階梯放大放空金額
                        add_qty = stake / exec_p  # 加倉股數
                        fric = (exec_p * add_qty * half_spread_ratio * 2.0) + (exec_p * add_qty * self.commission_pct)  # 摩擦
                        tot_val = (avg_entry_p * shares) + (exec_p * add_qty)  # 累計名目成本
                        shares += add_qty  # 增加空單量
                        avg_entry_p = tot_val / shares  # 重新計算均價
                        layer += 1  # 增加層數
                        total_friction += fric  # 累計摩擦
                        
            # 計算當前未實現損益與帳戶總權益
            if pos_side == "LONG":  # 多頭未實現損益
                unrealized = (shares * close) - (shares * avg_entry_p)  # 多頭浮動盈虧
                cur_equity = cash + (shares * avg_entry_p) + unrealized  # 總權益
            elif pos_side == "SHORT":  # 空頭未實現損益
                unrealized = (avg_entry_p - close) * shares  # 空頭浮動盈虧
                cur_equity = cash + unrealized  # 總權益
            else:  # 空倉
                cur_equity = cash  # 純現金
            equity_curve.append(cur_equity)  # 記錄權益時序
            
        # 計算總平倉勝率
        wins = sum(1 for t in closed_trades if t["is_win"])  # 獲利次數
        total_closed = len(closed_trades)  # 總平倉循環數
        win_rate = (wins / total_closed * 100.0) if total_closed > 0 else 0.0  # 勝率百分比
        
        return {  # 返回成果
            "equity": np.array(equity_curve),  # 淨值陣列
            "closed_trades": closed_trades,  # 平倉明細
            "win_rate": win_rate,  # 勝率 %
            "total_cycles": total_closed,  # 總交易循環數
            "friction": total_friction  # 總摩擦成本
        }

# ==========================================================
# 3. 4H 與 8H 多貨幣對全市場批次回測主程序
# ==========================================================

def run_all_fx_4h_8h_test():  # 執行 4H 與 8H 全貨幣對深度實測
    pairs = list(ALL_FX_SPREADS.keys())  # 取得 18 檔貨幣對清單
    print("==========================================================================================================")  # 分隔線
    print(f"🌍 下載 18 檔全市場貨幣對數據並執行 4H 與 8H 高勝率自適應網格深度回測 (近 720 天)...")  # 標題
    print("==========================================================================================================")  # 分隔線
    
    raw_data = yf.download(pairs, period="720d", interval="1h", progress=False)  # 批次下載 1h 資料
    
    timeframe_configs = [("4H", "4h"), ("8H", "8h")]  # 測試 4H 與 8H
    engine = HighWinRateFxGrid(initial_capital=25000.0, base_stake_usd=2000.0, max_dca_layers=4, stake_multiplier=1.3)  # 實例化策略
    
    all_summary_results = []  # 存儲所有總表
    
    for tf_label, tf_rule in timeframe_configs:  # 遍歷 4H 與 8H
        print(f"\n⚡ 正在計算 Timeframe: 【{tf_label}】...")  # 提示週期
        tf_results = []  # 存儲該週期結果
        
        for p in pairs:  # 遍歷貨幣對
            try:  # 例外捕獲保護
                df_1h = pd.DataFrame({  # 擷取單一標的之 OHLC
                    'open': raw_data['Open'][p].dropna(),  # 開盤
                    'high': raw_data['High'][p].dropna(),  # 最高
                    'low': raw_data['Low'][p].dropna(),  # 最低
                    'close': raw_data['Close'][p].dropna()  # 收盤
                })  # DataFrame 結尾
                
                if len(df_1h) < 200:  # 檢查資料完整性
                    continue  # 跳過
                    
                # 重採樣至 4H 或 8H
                df_resampled = df_1h.resample(tf_rule).agg({'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last'}).dropna().reset_index()  # 聚合
                
                spread = ALL_FX_SPREADS.get(p, 1.5)  # 取得真實點差
                res = engine.run(df_resampled, spread_bps=spread)  # 執行回測
                
                eq = res["equity"]  # 淨值曲線
                ret_pct = (eq[-1] - 25000.0) / 25000.0 * 100.0  # 總報酬率 %
                max_dd = float(np.min((eq - np.maximum.accumulate(eq)) / np.maximum.accumulate(eq))) * 100.0  # 最大回撤 %
                
                # 計算獲利因子 (Profit Factor)
                gross_profit = sum(t["pnl"] for t in res["closed_trades"] if t["pnl"] > 0)  # 總盈利
                gross_loss = abs(sum(t["pnl"] for t in res["closed_trades"] if t["pnl"] < 0))  # 總虧損
                profit_factor = round(gross_profit / gross_loss, 2) if gross_loss > 0 else 99.0  # 獲利因子
                
                pair_name = p.replace("=X", "")  # 簡化名稱
                row_res = {  # 記錄單一貨幣對成績
                    "Timeframe": tf_label,  # 週期
                    "Pair": pair_name,  # 貨幣對
                    "Spread": spread,  # 點差 (bps)
                    "Win Rate %": round(res["win_rate"], 1),  # 勝率 %
                    "Total Ret %": round(ret_pct, 2),  # 總報酬率 %
                    "Max DD %": round(max_dd, 2),  # 最大回撤 %
                    "Profit Factor": profit_factor,  # 獲利因子
                    "Cycles": res["total_cycles"],  # 總平倉循環數
                    "Friction$": round(res["friction"], 1)  # 總摩擦耗損 $
                }  # 字典結束
                
                tf_results.append(row_res)  # 寫入週期清單
                all_summary_results.append(row_res)  # 寫入總清單
                print(f"  📌 {pair_name:6s} | 勝率: {res['win_rate']:>5.1f}% | 總收益: +{ret_pct:>5.2f}% | 最大回撤: {max_dd:>5.2f}% | 循環: {res['total_cycles']:>3d} 次 | 摩擦: ${res['friction']:>5.1f}")  # 輸出
            except Exception as e:  # 捕捉錯誤
                continue  # 略過
                
    full_df = pd.DataFrame(all_summary_results)  # 轉為 DataFrame
    full_df.to_csv("fx_all_pairs_4h_8h_results.csv", index=False)  # 輸出 CSV 報告
    
    # 輸出 4H 與 8H 依勝率與報酬排序之成果表
    print("\n=================================== 4H 與 8H 全外匯貨幣對回測排名表 ===================================")  # 分隔線
    sorted_df = full_df.sort_values(by=["Timeframe", "Win Rate %", "Total Ret %"], ascending=[True, False, False])  # 排序
    print(sorted_df.to_string(index=False))  # 列印完整表格
    
    # 繪製 4H vs 8H 平均指標對比圖
    plt.figure(figsize=(14, 6))  # 建立畫布
    plt.style.use('seaborn-v0_8-darkgrid' if 'seaborn-v0_8-darkgrid' in plt.style.available else 'default')  # 設定樣式
    
    # 子圖 1：4H 全貨幣對勝率分佈
    plt.subplot(1, 2, 1)  # 左子圖
    df_4h = full_df[full_df["Timeframe"] == "4H"].sort_values(by="Win Rate %", ascending=True)  # 4H 資料
    plt.barh(df_4h["Pair"], df_4h["Win Rate %"], color="#2ca02c", alpha=0.85)  # 水平長條圖
    plt.axvline(90.0, color="red", linestyle="--", alpha=0.6, label="90% Benchmark")  # 90% 基準線
    plt.title("4H Timeframe - Win Rate % by Currency Pair", fontsize=11, fontweight="bold")  # 標題
    plt.xlabel("Win Rate %", fontsize=10)  # X 軸
    plt.legend()  # 圖例
    
    # 子圖 2：8H 全貨幣對勝率分佈
    plt.subplot(1, 2, 2)  # 右子圖
    df_8h = full_df[full_df["Timeframe"] == "8H"].sort_values(by="Win Rate %", ascending=True)  # 8H 資料
    plt.barh(df_8h["Pair"], df_8h["Win Rate %"], color="#1f77b4", alpha=0.85)  # 水平長條圖
    plt.axvline(90.0, color="red", linestyle="--", alpha=0.6, label="90% Benchmark")  # 90% 基準線
    plt.title("8H Timeframe - Win Rate % by Currency Pair", fontsize=11, fontweight="bold")  # 標題
    plt.xlabel("Win Rate %", fontsize=10)  # X 軸
    plt.legend()  # 圖例
    
    plt.tight_layout()  # 自動排版
    plt.savefig("fx_4h_8h_winrate_performance.png", dpi=300)  # 儲存高畫質圖片
    print("📈 4H 與 8H 勝率全景圖已儲存至 fx_4h_8h_winrate_performance.png")  # 完成提示

if __name__ == "__main__":  # 程式主入口
    run_all_fx_4h_8h_test()  # 啟動
