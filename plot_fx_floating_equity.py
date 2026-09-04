import os  # 引入作業系統路徑模組
import numpy as np  # 引入數值計算矩陣模組
import pandas as pd  # 引入資料分析處理模組
import yfinance as yf  # 引入歷史外匯資料獲取庫
import matplotlib.pyplot as plt  # 引入視覺化圖表繪製模組
from backtest_fx_4h_8h_all_pairs import ALL_FX_SPREADS  # 引入 18 檔貨幣對實盤點差字典

class DualTrackFxGrid:  # 定義雙軌時序外匯網格回測引擎
    def __init__(self, initial_capital: float = 25000.0, base_stake_usd: float = 2000.0,  # 初始本金 $25k、底倉 $2k
                 max_dca_layers: int = 4, stake_multiplier: float = 1.3,  # 最多 4 層加碼、1.3x 階梯
                 atr_step_mult: float = 1.0, tp_atr_mult: float = 1.0, commission_pct: float = 0.00002):  # 點差與佣金
        self.initial_capital = initial_capital  # 初始本金
        self.base_stake_usd = base_stake_usd  # 基礎單筆金額
        self.max_layers = max_dca_layers  # 最大加倉層數
        self.stake_mult = stake_multiplier  # 階梯加碼乘數
        self.atr_step_mult = atr_step_mult  # ATR 加倉間距倍數
        self.tp_atr_mult = tp_atr_mult  # ATR 止盈倍數
        self.commission_pct = commission_pct  # 佣金費率

    def run(self, df: pd.DataFrame, spread_bps: float) -> dict:  # 執行雙軌時序回測運算
        data = df.copy().reset_index(drop=True)  # 複製資料並重設索引
        
        # 指標計算
        data['tr'] = np.maximum(data['high'] - data['low'], np.maximum(np.abs(data['high'] - data['close'].shift(1)), np.abs(data['low'] - data['close'].shift(1))))  # TR
        data['atr'] = data['tr'].rolling(window=20).mean().bfill()  # 20 ATR
        data['ema50'] = data['close'].ewm(span=50).mean()  # 50 EMA 中軌
        data['lower_channel'] = data['ema50'] - (data['atr'] * 2.0)  # 2.0 ATR 下軌
        data['upper_channel'] = data['ema50'] + (data['atr'] * 2.0)  # 2.0 ATR 上軌
        
        realized_balance = self.initial_capital  # 已實現帳戶餘額 (無浮動損益的硬本金)
        cash = self.initial_capital  # 可用流動性現金
        half_spread_ratio = (spread_bps / 10000.0) / 2.0  # 半點差比例
        
        pos_side = None  # 持倉方向
        shares = 0.0  # 持倉量
        layer = 0  # 當前層數
        avg_entry_p = 0.0  # 加權均價
        
        balance_history = []  # 存儲已實現餘額歷史 (階梯上升線)
        equity_history = []  # 存儲即時動態淨值歷史 (含浮虧真實曲線)
        floating_pnl_history = []  # 存儲浮動盈虧金額 ($)
        
        for idx, row in data.iterrows():  # 逐根 4H Bar 模擬撮合
            close, high, low = row['close'], row['high'], row['low']  # 取得價格
            atr, ema50 = row['atr'], row['ema50']  # 取得指標
            lower_ch, upper_ch = row['lower_channel'], row['upper_channel']  # 通道界線
            
            # 無倉位時尋找開倉信號
            if layer == 0:  # 空倉狀態
                if low <= lower_ch:  # 超跌進場做多
                    exec_p = min(close, lower_ch) * (1.0 + half_spread_ratio)  # 買入價
                    qty = self.base_stake_usd / exec_p  # 股數
                    cost = (exec_p * qty) * (1.0 + self.commission_pct)  # 成本
                    if cash >= cost:  # 確認資金
                        cash -= cost  # 扣減可用現金
                        shares = qty  # 設定持倉
                        pos_side = "LONG"  # 標記多頭
                        layer = 1  # 進入第 1 層
                        avg_entry_p = exec_p  # 設定均價
                elif high >= upper_ch:  # 超漲進場放空
                    exec_p = max(close, upper_ch) * (1.0 - half_spread_ratio)  # 放空價
                    qty = self.base_stake_usd / exec_p  # 股數
                    margin = (exec_p * qty)  # 保證金
                    if cash >= margin:  # 確認資金
                        shares = qty  # 設定空頭
                        pos_side = "SHORT"  # 標記空頭
                        layer = 1  # 進入第 1 層
                        avg_entry_p = exec_p  # 設定均價
                        
            # 持有多單中
            elif pos_side == "LONG":  # 處理多單
                tp_target = avg_entry_p + (atr * self.tp_atr_mult)  # 止盈線
                if high >= tp_target or high >= ema50:  # 觸及止盈目標
                    exec_p = max(tp_target, ema50) * (1.0 - half_spread_ratio)  # 賣出價
                    revenue = (exec_p * shares) * (1.0 - self.commission_pct)  # 實收金額
                    profit = revenue - (avg_entry_p * shares)  # 結算淨利潤
                    cash += revenue  # 現金回籠
                    realized_balance += profit  # 已實現帳戶餘額向上跳增
                    pos_side, shares, layer, avg_entry_p = None, 0.0, 0, 0.0  # 清空狀態
                elif layer < self.max_layers:  # 補倉
                    next_trigger = avg_entry_p - (layer * atr * self.atr_step_mult)  # 加倉價
                    if low <= next_trigger:  # 觸及加倉價
                        exec_p = next_trigger * (1.0 + half_spread_ratio)  # 買入價
                        stake = self.base_stake_usd * (self.stake_mult ** layer)  # 加倉額
                        add_qty = stake / exec_p  # 加倉量
                        cost = (exec_p * add_qty) * (1.0 + self.commission_pct)  # 扣款
                        if cash >= cost:  # 確認資金
                            cash -= cost  # 扣減現金
                            tot_val = (avg_entry_p * shares) + (exec_p * add_qty)  # 累計成本
                            shares += add_qty  # 增加持股
                            avg_entry_p = tot_val / shares  # 重新計算均價
                            layer += 1  # 增加層數
                            
            # 持有空單中
            elif pos_side == "SHORT":  # 處理空單
                tp_target = avg_entry_p - (atr * self.tp_atr_mult)  # 止盈線
                if low <= tp_target or low <= ema50:  # 觸及止盈目標
                    exec_p = min(tp_target, ema50) * (1.0 + half_spread_ratio)  # 平空價
                    profit = (avg_entry_p - exec_p) * shares - (exec_p * shares * self.commission_pct)  # 空頭利潤
                    cash += profit  # 結算損益
                    realized_balance += profit  # 已實現帳戶餘額向上跳增
                    pos_side, shares, layer, avg_entry_p = None, 0.0, 0, 0.0  # 清空狀態
                elif layer < self.max_layers:  # 補倉
                    next_trigger = avg_entry_p + (layer * atr * self.atr_step_mult)  # 加倉價
                    if high >= next_trigger:  # 觸及加倉價
                        exec_p = next_trigger * (1.0 - half_spread_ratio)  # 放空價
                        stake = self.base_stake_usd * (self.stake_mult ** layer)  # 加倉額
                        add_qty = stake / exec_p  # 加倉量
                        tot_val = (avg_entry_p * shares) + (exec_p * add_qty)  # 累計成本
                        shares += add_qty  # 增加空單
                        avg_entry_p = tot_val / shares  # 重新計算均價
                        layer += 1  # 增加層數
                        
            # 精確計算本時刻的未實現浮動損益 (Unrealized Floating PnL)
            if pos_side == "LONG":  # 多單浮動盈虧
                unrealized_pnl = (shares * close) - (shares * avg_entry_p)  # 浮動盈虧 ($)
                current_equity = cash + (shares * avg_entry_p) + unrealized_pnl  # 動態淨值
            elif pos_side == "SHORT":  # 空單浮動盈虧
                unrealized_pnl = (avg_entry_p - close) * shares  # 浮動盈虧 ($)
                current_equity = cash + unrealized_pnl  # 動態淨值
            else:  # 空倉無浮虧
                unrealized_pnl = 0.0  # 浮動盈虧為 0
                current_equity = realized_balance  # 淨值等於餘額
                
            balance_history.append(realized_balance)  # 記錄已實現餘額
            equity_history.append(current_equity)  # 記錄動態淨值 (含浮虧)
            floating_pnl_history.append(unrealized_pnl)  # 記錄浮動損益
            
        return {  # 返回雙軌數據
            "balance": np.array(balance_history),  # 已實現餘額陣列
            "equity": np.array(equity_history),  # 動態淨值陣列
            "floating_pnl": np.array(floating_pnl_history)  # 浮動盈虧陣列
        }

def plot_floating_loss_analysis():  # 執行浮動損益視覺化主程序
    targets = ["CADJPY=X", "USDJPY=X", "EURUSD=X", "AUDNZD=X"]  # 選取 4 檔具代表性標的
    raw_data = yf.download(targets, period="720d", interval="1h", progress=False)  # 下載資料
    
    engine = DualTrackFxGrid(initial_capital=25000.0, base_stake_usd=2000.0, max_dca_layers=4, stake_multiplier=1.3)  # 實例化
    
    plt.figure(figsize=(16, 11))  # 建立大型畫布 (16x11)
    plt.style.use('seaborn-v0_8-darkgrid' if 'seaborn-v0_8-darkgrid' in plt.style.available else 'default')  # 樣式
    
    for i, p in enumerate(targets):  # 遍歷 4 檔標的
        df_1h = pd.DataFrame({  # 擷取單一標的 OHLC
            'open': raw_data['Open'][p].dropna(), 'high': raw_data['High'][p].dropna(),  # 開高
            'low': raw_data['Low'][p].dropna(), 'close': raw_data['Close'][p].dropna()  # 低收
        })  # DataFrame
        
        df_4h = df_1h.resample("4h").agg({'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last'}).dropna().reset_index()  # 4H 聚合
        spread = ALL_FX_SPREADS.get(p, 1.5)  # 點差
        sym_name = p.replace("=X", "")  # 簡稱
        
        res = engine.run(df_4h, spread_bps=spread)  # 執行回測
        balance = res["balance"]  # 已實現餘額
        equity = res["equity"]  # 動態淨值 (含浮虧)
        floating_pnl = res["floating_pnl"]  # 浮動盈虧
        
        plt.subplot(2, 2, i+1)  # 選擇 2x2 子圖
        
        # 繪製已實現餘額 (綠色平滑階梯線)
        plt.plot(balance, label="Realized Balance (Closed Profit)", color="#2ca02c", lw=2.0, linestyle="--")  # 綠虛線
        # 繪製即時動態淨值 (深藍色真實淨值線)
        plt.plot(equity, label="Dynamic Equity (with Floating PnL)", color="#1f77b4", lw=1.6)  # 藍實線
        # 填充浮虧區域陰影 (紅色警示色塊)
        plt.fill_between(np.arange(len(balance)), equity, balance, where=(equity < balance), color="#d62728", alpha=0.35, label="Floating Drawdown Area")  # 填色
        
        final_ret = (balance[-1] - 25000.0) / 250.0  # 總收益 %
        max_dd_val = float(np.min(floating_pnl))  # 最大單次浮虧金額 ($)
        max_dd_pct = (max_dd_val / 25000.0) * 100.0  # 最大浮虧比例 %
        
        plt.title(f"{sym_name} (4H) | Return: +{final_ret:.1f}% | Max Floating DD: -${abs(max_dd_val):.0f} ({max_dd_pct:.1f}%)", fontsize=11, fontweight="bold")  # 標題
        plt.xlabel("4-Hour Bars (Last 720 Days)", fontsize=9)  # X 軸
        plt.ylabel("Portfolio Value ($)", fontsize=9)  # Y 軸
        plt.legend(loc="upper left", fontsize=8)  # 圖例
        
    plt.tight_layout()  # 自動排版
    out_file = "fx_floating_equity_comparison.png"  # 檔名
    plt.savefig(out_file, dpi=300)  # 儲存高解析度圖片
    print(f"📈 浮動虧損動態對照圖已成功生成並儲存至: {out_file}")  # 提示

if __name__ == "__main__":  # 程式主入口
    plot_floating_loss_analysis()  # 啟動
