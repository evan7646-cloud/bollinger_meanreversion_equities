import os  # 引入作業系統模組處理檔案路徑
import numpy as np  # 引入數值計算模組
import pandas as pd  # 引入資料處理模組
from datetime import datetime  # 引入時間處理模組

SPREAD_MAP = pd.read_csv("mt5_spread_by_symbol.csv").set_index("symbol")["avg_spread_bps"].to_dict()  # 讀取點差表

def analyze_symbol_holding_period(df, symbol, default_spread_bps):  # 分析單一品種持倉時間函數
    data = df.copy().reset_index(drop=True)  # 複製資料並重設索引
    data['datetime'] = pd.to_datetime(data['datetime'])  # 轉換時間格式
    
    # 計算指標
    data['tr'] = np.maximum(data['high'] - data['low'],  # 計算 TR
                            np.maximum(np.abs(data['high'] - data['close'].shift(1)),  # 高與昨收差
                                       np.abs(data['low'] - data['close'].shift(1))))  # 低與昨收差
    data['atr'] = data['tr'].rolling(window=20).mean().bfill()  # 20 ATR
    data['ema50'] = data['close'].ewm(span=50).mean()  # 50 EMA
    data['lower_band'] = data['ema50'] - (data['atr'] * 2.0)  # 2.0 ATR 下軌
    
    half_spread_ratio = (default_spread_bps / 10000.0) / 2.0  # 半點差
    cash = 25000.0  # 初始本金
    shares = 0.0  # 持股
    layer = 0  # 層數
    avg_entry_price = 0.0  # 持倉均價
    entry_time = None  # 記錄首單進場時間
    
    holding_durations_hours = []  # 存儲每筆交易持倉小時數
    
    for idx, row in data.iterrows():  # 遍歷 K 線
        close, high, low = row['close'], row['high'], row['low']  # 取得價格
        atr, ema50, lower_band = row['atr'], row['ema50'], row['lower_band']  # 取得指標
        cur_time = row['datetime']  # 當前 K 棒時間
        
        if layer == 0:  # 空倉
            if low <= lower_band:  # 觸及下軌
                exec_price = min(close, lower_band) * (1.0 + half_spread_ratio)  # 買入價
                buy_qty = 1500.0 / exec_price  # 股數
                shares = buy_qty  # 設定持股
                layer = 1  # 設為第 1 層
                avg_entry_price = exec_price  # 記錄均價
                entry_time = cur_time  # 記錄首單開倉時間戳
        else:  # 持倉中
            # 止盈
            if high >= ema50:  # 觸及均線止盈
                duration_hrs = (cur_time - entry_time).total_seconds() / 3600.0  # 計算持倉總小時數
                holding_durations_hours.append(duration_hrs)  # 存入列表
                shares = 0.0  # 清空持股
                layer = 0  # 重設層數
                avg_entry_price = 0.0  # 重設均價
                entry_time = None  # 清空開倉時間
            # 停損
            elif low <= avg_entry_price - (atr * 4.0):  # 觸發停損
                duration_hrs = (cur_time - entry_time).total_seconds() / 3600.0  # 計算持倉總小時數
                holding_durations_hours.append(duration_hrs)  # 存入列表
                shares = 0.0  # 清空持股
                layer = 0  # 重設層數
                avg_entry_price = 0.0  # 重設均價
                entry_time = None  # 清空開倉時間
            # 加倉
            elif layer < 4:  # 未達加倉上限
                next_trigger = avg_entry_price - (layer * atr * 1.5)  # 下一階加倉價
                if low <= next_trigger:  # 觸及加倉價
                    exec_price = next_trigger * (1.0 + half_spread_ratio)  # 加倉買入價
                    buy_qty = (1500.0 * 1.2) / exec_price  # 加倉股數
                    total_spent = (avg_entry_price * shares) + (exec_price * buy_qty)  # 累計總額
                    shares += buy_qty  # 累加股數
                    avg_entry_price = total_spent / shares  # 更新持倉均價
                    layer += 1  # 增加層數
                    
    durations = np.array(holding_durations_hours)  # 轉換為 numpy 陣列
    durations_days = durations / 24.0  # 轉換為天數
    
    avg_hours = np.mean(durations) if len(durations) > 0 else 0.0  # 平均小時
    avg_days = np.mean(durations_days) if len(durations_days) > 0 else 0.0  # 平均天數
    median_hours = np.median(durations) if len(durations) > 0 else 0.0  # 中位數小時
    max_days = np.max(durations_days) if len(durations_days) > 0 else 0.0  # 最長持倉天數
    
    intraday_pct = (np.sum(durations <= 6.5) / len(durations) * 100.0) if len(durations) > 0 else 0.0  # 當沖平倉比例 (美股6.5小時)
    within_1day_pct = (np.sum(durations <= 24.0) / len(durations) * 100.0) if len(durations) > 0 else 0.0  # 24小時內平倉比例
    over_3days_pct = (np.sum(durations_days > 3.0) / len(durations) * 100.0) if len(durations) > 0 else 0.0  # 超過3天的長單比例
    
    return {  # 返回統計字典
        "symbol": symbol,  # 品種
        "總回合數": len(durations),  # 總交易循環次數
        "平均持倉時間(小時)": round(avg_hours, 1),  # 平均小時
        "平均持倉天數(天)": round(avg_days, 2),  # 平均天數
        "中位數持倉(小時)": round(median_hours, 1),  # 中位數小時
        "最長持倉天數(天)": round(max_days, 1),  # 最長持倉天數
        "當日平倉比例(%)": round(intraday_pct, 1),  # 當日沖銷率
        "24H內平倉比例(%)": round(within_1day_pct, 1),  # 24H 結束率
        "超過3天長單比例(%)": round(over_3days_pct, 1)  # 超過 3 天比例
    }  # 字典結尾

def run_all_holding_analysis():  # 執行全品種持倉時間分析函數
    data_dir = "data_mt5_equities_cfd"  # 資料檔案夾
    target_symbols = ["AMD", "NVDA", "INTC", "AVGO", "QCOM", "PLTR", "ASML", "JPM", "SNOW", "LMT", "MSFT", "AAPL", "TSLA"]  # 目標品種
    results = []  # 存儲結果
    
    for sym in target_symbols:  # 遍歷各品種
        file_path = os.path.join(data_dir, f"EQ_{sym}_15m.csv")  # 檔案路徑
        if not os.path.exists(file_path):  # 檢查檔案
            continue  # 跳過
        df = pd.read_csv(file_path)  # 讀取資料
        spread = SPREAD_MAP.get(sym, 10.0)  # 取得點差
        res = analyze_symbol_holding_period(df, sym, spread)  # 執行分析
        results.append(res)  # 加入結果
        
    df_res = pd.DataFrame(results)  # 轉為 DataFrame
    print("=======================================================================================================================")  # 分隔線
    print("                                      MT5 策略真實持倉時間與週期深度統計                                                 ")  # 標題
    print("=======================================================================================================================")  # 分隔線
    pd.set_option('display.max_columns', None)  # 顯示全部欄位
    pd.set_option('display.width', 1000)  # 設置寬度
    print(df_res.to_string(index=False))  # 印出表格
    df_res.to_csv("strategy_holding_periods_summary.csv", index=False, encoding="utf-8-sig")  # 匯出 CSV

if __name__ == "__main__":  # 主程式進入點
    run_all_holding_analysis()  # 啟動統計
