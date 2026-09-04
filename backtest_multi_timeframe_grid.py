import os  # 引入作業系統檔案路徑模組
import numpy as np  # 引入數值計算模組
import pandas as pd  # 引入資料分析處理模組
from typing import List, Dict  # 引入型別提示模組
import matplotlib.pyplot as plt  # 引入繪圖模組

# ==========================================================
# 1. 讀取真實歷史 Spread 表
# ==========================================================

SPREAD_TABLE = pd.read_csv("mt5_spread_by_symbol.csv").set_index("symbol")["avg_spread_bps"].to_dict()  # 載入真實平均點差表 (bps)

# ==========================================================
# 2. 多週期重採樣輔助函數
# ==========================================================

def resample_ohlc(df_5m: pd.DataFrame, timeframe_rule: str) -> pd.DataFrame:  # 將 5m K 線精確重採樣為高層次週期
    df = df_5m.copy()  # 複製資料
    df.set_index('dt', inplace=True)  # 將時間設為索引
    agg_dict = {  # 定義重採樣聚合規則
        'open': 'first',  # 開盤價取首筆
        'high': 'max',  # 最高價取最大值
        'low': 'min',  # 最低價取最小值
        'close': 'last',  # 收盤價取末筆
        'spread_bps': 'mean'  # 點差取平均值
    }  # 規則字典結尾
    resampled = df.resample(timeframe_rule).agg(agg_dict).dropna().reset_index()  # 執行聚合並刪除空值
    return resampled  # 返回重採樣後的 DataFrame

# ==========================================================
# 3. 核心網格回測引擎 (含多週期適應參數)
# ==========================================================

def run_octobot_smart_grid(df: pd.DataFrame, default_spread_bps: float, grid_step_pct: float = 0.035,  # 執行 OctoBot 智慧平移網格
                           order_size_usd: float = 1500.0, max_levels: int = 5, comm_pct: float = 0.00002) -> dict:  # 參數
    cash = 25000.0  # 初始本金
    shares = 0.0  # 持股數量
    df['ema50'] = df['close'].ewm(span=50).mean()  # 50 EMA
    df['ema200'] = df['close'].ewm(span=200).mean()  # 200 EMA
    anchor_price = df.iloc[0]['close']  # 初始基準價
    trades = []  # 交易記錄
    equity_curve = []  # 淨值歷史
    total_friction = 0.0  # 總摩擦成本 (點差 + 佣金)
    
    for idx, row in df.iterrows():  # 逐根 Bar 撮合
        close, high, low = row['close'], row['high'], row['low']  # 取得高低收
        ema50, ema200 = row['ema50'], row['ema200']  # 均線值
        spread_bps = row['spread_bps'] if row['spread_bps'] > 0 else default_spread_bps  # 當根點差
        half_spread = (spread_bps / 10000.0) / 2.0  # 半點差
        trend_ok = (ema50 >= ema200 * 0.98) if not np.isnan(ema200) else True  # 趨勢判定
        
        # 1. 向上平移獲利
        if high >= anchor_price * (1.0 + grid_step_pct) and trend_ok:  # 觸發上方獲利線
            if shares > 0:  # 手上有倉位
                sell_qty = min(shares, order_size_usd / close)  # 賣出單格股數
                raw_p = anchor_price * (1.0 + grid_step_pct)  # 名目賣價
                exec_p = raw_p * (1.0 - half_spread)  # 扣除真實點差後賣價
                fric = (raw_p - exec_p) * sell_qty + (exec_p * sell_qty * comm_pct)  # 計算摩擦成本
                rev = (exec_p * sell_qty) * (1.0 - comm_pct)  # 實收金額
                cash += rev  # 現金增加
                shares -= sell_qty  # 持股減少
                total_friction += fric  # 累計摩擦
                trades.append({"side": "SELL", "price": exec_p, "qty": sell_qty})  # 記錄
            anchor_price = anchor_price * (1.0 + grid_step_pct)  # 網格上移
            
        # 2. 向下分批低吸
        cur_layer = int(round((anchor_price - close) / (anchor_price * grid_step_pct)))  # 計算層數
        if 1 <= cur_layer <= max_levels and low <= anchor_price * (1.0 - cur_layer * grid_step_pct):  # 觸及買入檔位
            target_p = anchor_price * (1.0 - cur_layer * grid_step_pct)  # 目標買價
            exec_p = target_p * (1.0 + half_spread)  # 加上真實點差後買價
            buy_qty = order_size_usd / exec_p  # 買入股數
            fric = (exec_p - target_p) * buy_qty + (exec_p * buy_qty * comm_pct)  # 計算摩擦
            cost = (exec_p * buy_qty) * (1.0 + comm_pct)  # 總支付金額
            if cash >= cost and (shares * close) < (order_size_usd * max_levels):  # 嚴守曝險上限
                cash -= cost  # 扣除現金
                shares += buy_qty  # 增加持股
                total_friction += fric  # 累計摩擦
                trades.append({"side": f"BUY_L{cur_layer}", "price": exec_p, "qty": buy_qty})  # 記錄
                
        # 3. 趨勢逆轉防禦停損
        if low <= anchor_price * (1.0 - (max_levels + 1) * grid_step_pct) and not trend_ok:  # 觸發停損
            if shares > 0:  # 手上持有倉位
                exec_p = close * (1.0 - half_spread)  # 停損平倉價
                fric = (close - exec_p) * shares + (exec_p * shares * comm_pct)  # 計算摩擦
                rev = (exec_p * shares) * (1.0 - comm_pct)  # 實收金額
                cash += rev  # 回籠資金
                shares = 0.0  # 清空持股
                anchor_price = close  # 重設錨點
                total_friction += fric  # 累計摩擦
                trades.append({"side": "SL", "price": exec_p, "qty": shares})  # 記錄
                
        equity_curve.append(cash + (shares * close))  # 紀錄總淨值時序
        
    return {"equity": np.array(equity_curve), "trades": len(trades), "friction": total_friction}  # 返回結果

def run_freqtrade_channel_dca(df: pd.DataFrame, default_spread_bps: float, base_order_usd: float = 2000.0,  # 執行 Freqtrade 通道 DCA
                              max_layers: int = 3, comm_pct: float = 0.00002) -> dict:  # 參數配置
    cash = 25000.0  # 初始本金
    shares = 0.0  # 持股數量
    layer = 0  # 當前加倉層數
    avg_price = 0.0  # 持倉均價
    df['tr'] = np.maximum(df['high'] - df['low'], np.maximum(np.abs(df['high'] - df['close'].shift(1)), np.abs(df['low'] - df['close'].shift(1))))  # TR
    df['atr'] = df['tr'].rolling(window=30).mean().bfill()  # 30 ATR
    df['ema50'] = df['close'].ewm(span=50).mean()  # 50 EMA 中軌
    df['lower_band'] = df['ema50'] - (df['atr'] * 2.2)  # 2.2 ATR 超跌下軌
    trades = []  # 交易記錄
    equity_curve = []  # 淨值歷史
    total_friction = 0.0  # 總摩擦成本
    
    for idx, row in df.iterrows():  # 逐根 Bar 撮合
        close, high, low = row['close'], row['high'], row['low']  # 取得價格
        atr, ema50, lower_band = row['atr'], row['ema50'], row['lower_band']  # 取得指標
        spread_bps = row['spread_bps'] if row['spread_bps'] > 0 else default_spread_bps  # 當根點差
        half_spread = (spread_bps / 10000.0) / 2.0  # 半點差
        
        if layer == 0:  # 無倉位時尋找超跌進場
            if low <= lower_band:  # 觸及超跌下軌
                raw_p = min(close, lower_band)  # 名目買價
                exec_p = raw_p * (1.0 + half_spread)  # 加上真實點差買價
                buy_qty = base_order_usd / exec_p  # 首筆股數
                fric = (exec_p - raw_p) * buy_qty + (exec_p * buy_qty * comm_pct)  # 摩擦成本
                cost = (exec_p * buy_qty) * (1.0 + comm_pct)  # 總成本
                if cash >= cost:  # 確認現金充足
                    cash -= cost  # 扣減現金
                    shares = buy_qty  # 設定持股
                    layer = 1  # 進入第 1 層
                    avg_price = exec_p  # 設定初始均價
                    total_friction += fric  # 累計摩擦
                    trades.append({"side": "ENTRY", "price": exec_p})  # 記錄
        else:  # 持倉中
            # 止盈回抽至 EMA 50
            if high >= ema50:  # 觸及中軌止盈
                exec_p = ema50 * (1.0 - half_spread)  # 扣除點差賣價
                fric = (ema50 - exec_p) * shares + (exec_p * shares * comm_pct)  # 摩擦成本
                rev = (exec_p * shares) * (1.0 - comm_pct)  # 實收金額
                cash += rev  # 回籠資金
                total_friction += fric  # 累計摩擦
                trades.append({"side": "TP", "price": exec_p})  # 記錄
                shares, layer, avg_price = 0.0, 0, 0.0  # 重置倉位狀態
            # 停損跌破 3.5 ATR
            elif low <= avg_price - (atr * 3.5):  # 觸發停損
                exec_p = (avg_price - atr * 3.5) * (1.0 - half_spread)  # 停損價
                fric = ((avg_price - atr * 3.5) - exec_p) * shares + (exec_p * shares * comm_pct)  # 摩擦
                rev = (exec_p * shares) * (1.0 - comm_pct)  # 實收金額
                cash += rev  # 回籠資金
                total_friction += fric  # 累計摩擦
                trades.append({"side": "SL", "price": exec_p})  # 記錄
                shares, layer, avg_price = 0.0, 0, 0.0  # 重置狀態
            # 逐層加倉 DCA
            elif layer < max_layers:  # 未達加倉上限
                next_p = avg_price - (layer * atr * 1.2)  # 下一階加倉價
                if low <= next_p:  # 觸及加倉價
                    exec_p = next_p * (1.0 + half_spread)  # 加倉買價
                    buy_qty = base_order_usd / exec_p  # 加倉股數
                    fric = (exec_p - next_p) * buy_qty + (exec_p * buy_qty * comm_pct)  # 摩擦
                    cost = (exec_p * buy_qty) * (1.0 + comm_pct)  # 支付金額
                    if cash >= cost:  # 確認資金
                        cash -= cost  # 扣除現金
                        tot_cost = (avg_price * shares) + (exec_p * buy_qty)  # 總支出
                        shares += buy_qty  # 增加持股
                        avg_price = tot_cost / shares  # 重新計算均價
                        layer += 1  # 增加層數
                        total_friction += fric  # 累加摩擦
                        trades.append({"side": f"DCA_{layer}", "price": exec_p})  # 記錄
                        
        equity_curve.append(cash + (shares * close))  # 記錄淨值時序
        
    return {"equity": np.array(equity_curve), "trades": len(trades), "friction": total_friction}  # 返回結果

# ==========================================================
# 4. 多 Timeframe (5m vs 15m vs 30m vs 1h vs 4h vs 1D) 批量回測
# ==========================================================

def run_multi_timeframe_study():  # 執行多週期對比分析
    timeframes = [  # 測試週期列表
        ("5m", None),  # 原始 5 分鐘
        ("15m", "15min"),  # 15 分鐘
        ("30m", "30min"),  # 30 分鐘
        ("1h", "1h"),  # 1 小時
        ("4h", "4h"),  # 4 小時
        ("1D", "1D")  # 日線級別
    ]  # 週期列表結尾
    
    test_symbols = ["AAPL", "NVDA", "MSFT", "TSLA", "AMZN", "GOOG", "JPM", "AMD"]  # 代表性股票清單
    data_dir = "data_mt5_equities_cfd"  # 資料目錄
    
    results = []  # 存儲所有結果
    
    print("==========================================================================================================")  # 分隔線
    print("⏳ 啟動多週期 (5m ➔ 15m ➔ 30m ➔ 1h ➔ 4h ➔ 1D) 網格策略深度對比研究 (2022~2026)...")  # 提示
    print("==========================================================================================================")  # 分隔線
    
    for tf_name, tf_rule in timeframes:  # 遍歷所有週期
        octo_rets, octo_dds, octo_trades, octo_frics = [], [], [], []  # 存儲 Octo 指標
        freq_rets, freq_dds, freq_trades, freq_frics = [], [], [], []  # 存儲 Freq 指標
        
        for sym in test_symbols:  # 遍歷股票
            fpath = os.path.join(data_dir, f"EQ_{sym}_5m.csv")  # 檔案路徑
            if not os.path.exists(fpath):  # 檢查檔案
                continue  # 跳過
                
            df = pd.read_csv(fpath)  # 讀取 CSV
            df['dt'] = pd.to_datetime(df['datetime'], format='%Y.%m.%d %H:%M')  # 轉換時間
            df_2022 = df[df['dt'] >= '2022-01-01'].copy().reset_index(drop=True)  # 篩選 2022+ 數據
            
            # 若為大週期則重採樣
            if tf_rule is not None:  # 需要重採樣
                df_tf = resample_ohlc(df_2022, tf_rule)  # 執行重採樣
            else:  # 原生 5m
                df_tf = df_2022  # 直接使用
                
            if len(df_tf) < 50:  # 檢查有效 Bar 數
                continue  # 跳過
                
            real_spread = SPREAD_TABLE.get(sym, 4.0)  # 取得真實點差
            
            # 運行 OctoBot
            res_octo = run_octobot_smart_grid(df_tf, default_spread_bps=real_spread)  # 運行
            eq_o = res_octo["equity"]  # 淨值陣列
            ret_o = (eq_o[-1] - 25000.0) / 25000.0 * 100.0  # 報酬 %
            dd_o = float(np.min((eq_o - np.maximum.accumulate(eq_o)) / np.maximum.accumulate(eq_o))) * 100.0  # 最大回撤 %
            octo_rets.append(ret_o)  # 記錄報酬
            octo_dds.append(dd_o)  # 記錄回撤
            octo_trades.append(res_octo["trades"])  # 記錄交易次數
            octo_frics.append(res_octo["friction"])  # 記錄摩擦費用
            
            # 運行 Freqtrade
            res_freq = run_freqtrade_channel_dca(df_tf, default_spread_bps=real_spread)  # 運行
            eq_f = res_freq["equity"]  # 淨值陣列
            ret_f = (eq_f[-1] - 25000.0) / 25000.0 * 100.0  # 報酬 %
            dd_f = float(np.min((eq_f - np.maximum.accumulate(eq_f)) / np.maximum.accumulate(eq_f))) * 100.0  # 最大回撤 %
            freq_rets.append(ret_f)  # 記錄報酬
            freq_dds.append(dd_f)  # 記錄回撤
            freq_trades.append(res_freq["trades"])  # 記錄交易次數
            freq_frics.append(res_freq["friction"])  # 記錄摩擦費用
            
        results.append({  # 寫入彙整表
            "Timeframe": tf_name,  # 週期名稱
            "Octo Avg Ret %": round(np.mean(octo_rets), 2),  # Octo 平均收益率 %
            "Octo Avg MaxDD %": round(np.mean(octo_dds), 2),  # Octo 平均最大回撤 %
            "Octo Avg Trades": int(np.mean(octo_trades)),  # Octo 平均交易筆數
            "Octo Avg Friction$": round(np.mean(octo_frics), 1),  # Octo 平均摩擦耗損 $
            "Freq Avg Ret %": round(np.mean(freq_rets), 2),  # Freq 平均收益率 %
            "Freq Avg MaxDD %": round(np.mean(freq_dds), 2),  # Freq 平均最大回撤 %
            "Freq Avg Trades": int(np.mean(freq_trades)),  # Freq 平均交易筆數
            "Freq Avg Friction$": round(np.mean(freq_frics), 1)  # Freq 平均摩擦耗損 $
        })  # 寫入結尾
        
        print(f"🎯 Timeframe {tf_name:>4s} | Octo: Ret {np.mean(octo_rets):>5.2f}%, DD {np.mean(octo_dds):>5.2f}%, Trades {int(np.mean(octo_trades)):>4d} | Freq: Ret {np.mean(freq_rets):>5.2f}%, DD {np.mean(freq_dds):>5.2f}%, Trades {int(np.mean(freq_trades)):>4d}")  # 輸出
        
    summary_df = pd.DataFrame(results)  # 轉為 DataFrame
    summary_df.to_csv("multi_timeframe_grid_results.csv", index=False)  # 輸出 CSV
    print("\n================================ 多週期 Timeframe 策略對比總表 ================================")  # 分隔線
    print(summary_df.to_string(index=False))  # 列印表格
    
    # 繪製不同 Timeframe 對比圖
    plt.figure(figsize=(12, 6))  # 建立畫布
    plt.style.use('seaborn-v0_8-darkgrid' if 'seaborn-v0_8-darkgrid' in plt.style.available else 'default')  # 設定樣式
    
    tfs = [r["Timeframe"] for r in results]  # X 軸標籤
    x = np.arange(len(tfs))  # X 軸座標
    width = 0.35  # 柱狀圖寬度
    
    plt.subplot(1, 2, 1)  # 子圖 1：平均報酬率
    plt.bar(x - width/2, [r["Octo Avg Ret %"] for r in results], width, label="OctoBot Smart Grid", color="#2ca02c")  # Octo 柱
    plt.bar(x + width/2, [r["Freq Avg Ret %"] for r in results], width, label="Freqtrade Channel DCA", color="#1f77b4")  # Freq 柱
    plt.xticks(x, tfs, fontweight="bold")  # X 軸刻度
    plt.title("Avg Total Return % by Timeframe", fontsize=12, fontweight="bold")  # 標題
    plt.ylabel("Return %", fontsize=10)  # Y 軸
    plt.legend()  # 圖例
    
    plt.subplot(1, 2, 2)  # 子圖 2：平均摩擦成本損耗
    plt.bar(x - width/2, [r["Octo Avg Friction$"] for r in results], width, label="Octo Friction ($)", color="#2ca02c")  # Octo 摩擦柱
    plt.bar(x + width/2, [r["Freq Avg Friction$"] for r in results], width, label="Freq Friction ($)", color="#d62728")  # Freq 摩擦柱
    plt.xticks(x, tfs, fontweight="bold")  # X 軸刻度
    plt.title("Friction Drag (Spread + Comm $) by Timeframe", fontsize=12, fontweight="bold")  # 標題
    plt.ylabel("Total Friction ($)", fontsize=10)  # Y 軸
    plt.yscale('log')  # 對數座標以清晰展示巨幅摩擦差異
    plt.legend()  # 圖例
    
    plt.tight_layout()  # 自動緊湊排版
    plt.savefig("timeframe_comparison.png", dpi=300)  # 儲存圖片
    print("📈 多週期比較圖已儲存至 timeframe_comparison.png")  # 完成提示

if __name__ == "__main__":  # 程式主入口
    run_multi_timeframe_study()  # 啟動
