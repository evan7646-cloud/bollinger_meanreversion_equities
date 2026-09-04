import os  # 引入作業系統路徑模組
import numpy as np  # 引入數值計算矩陣模組
import pandas as pd  # 引入資料處理與分析模組
import yfinance as yf  # 引入 Yahoo Finance 資料獲取庫
import matplotlib.pyplot as plt  # 引入圖表繪製模組
from backtest_fx_4h_8h_all_pairs import HighWinRateFxGrid, ALL_FX_SPREADS  # 從回測引擎導入策略類別與點差字典

# ==========================================================
# 1. 執行多貨幣對時序淨值收集與繪圖引擎
# ==========================================================

def plot_fx_line_charts():  # 定義外匯淨值折線圖生成主函式
    selected_pairs = [  # 選取代表性與高收益貨幣對清單
        "USDJPY=X", "CADJPY=X", "GBPJPY=X", "NZDUSD=X",  # 高波高收益組
        "EURUSD=X", "GBPUSD=X", "AUDUSD=X", "USDCAD=X",  # G10 主力直盤組
        "AUDNZD=X", "EURGBP=X", "EURCHF=X", "NZDCAD=X"   # 經典低波盤整組
    ]  # 貨幣對清單結尾
    
    print("==========================================================================================================")  # 分隔線
    print("📈 正在下載數據並生成 4H 與 8H 全貨幣對【資產淨值時序折線圖 (Equity Line Curves)】...")  # 提示訊息
    print("==========================================================================================================")  # 分隔線
    
    raw_data = yf.download(selected_pairs, period="720d", interval="1h", progress=False)  # 下載歷史 1h 數據
    
    curves_4h: dict = {}  # 儲存 4H 各幣種淨值時序字典
    curves_8h: dict = {}  # 儲存 8H 各幣種淨值時序字典
    date_index_4h = None  # 儲存 4H 時間索引
    
    engine = HighWinRateFxGrid(initial_capital=25000.0, base_stake_usd=2000.0, max_dca_layers=4, stake_multiplier=1.3)  # 實例化策略
    
    for p in selected_pairs:  # 遍歷貨幣對
        try:  # 例外保護
            df_1h = pd.DataFrame({  # 擷取單一標的 OHLC
                'open': raw_data['Open'][p].dropna(),  # 開盤
                'high': raw_data['High'][p].dropna(),  # 最高
                'low': raw_data['Low'][p].dropna(),  # 最低
                'close': raw_data['Close'][p].dropna()  # 收盤
            })  # DataFrame 結束
            
            if len(df_1h) < 200:  # 檢查資料長度
                continue  # 資料不足則跳過
                
            spread = ALL_FX_SPREADS.get(p, 1.5)  # 取得真實點差
            sym_name = p.replace("=X", "")  # 簡化標的名稱
            
            # 1. 運算 4H 淨值曲線
            df_4h = df_1h.resample("4h").agg({'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last'}).dropna().reset_index()  # 重採樣 4H
            res_4h = engine.run(df_4h, spread_bps=spread)  # 運行 4H 回測
            curves_4h[sym_name] = res_4h["equity"]  # 存入 4H 淨值陣列
            if date_index_4h is None:  # 設定時間參考軸
                date_index_4h = df_4h['index'] if 'index' in df_4h else np.arange(len(res_4h["equity"]))  # 時間軸
                
            # 2. 運算 8H 淨值曲線
            df_8h = df_1h.resample("8h").agg({'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last'}).dropna().reset_index()  # 重採樣 8H
            res_8h = engine.run(df_8h, spread_bps=spread)  # 運行 8H 回測
            curves_8h[sym_name] = res_8h["equity"]  # 存入 8H 淨值陣列
            
            print(f"✅ 完成 {sym_name:6s} 淨值時序計算 | 4H 終值: ${res_4h['equity'][-1]:>7.2f} | 8H 終值: ${res_8h['equity'][-1]:>7.2f}")  # 輸出
        except Exception as e:  # 捕捉異常
            continue  # 略過
            
    # ==========================================================
    # 2. 繪製專業多子圖折線圖表 (Multi-Panel Equity Line Charts)
    # ==========================================================
    
    plt.figure(figsize=(16, 11))  # 設定畫布尺寸 (寬 16 吋、高 11 吋)
    plt.style.use('seaborn-v0_8-darkgrid' if 'seaborn-v0_8-darkgrid' in plt.style.available else 'default')  # 設定圖表背景網格樣式
    
    # ----------------------------------------------------------
    # 子圖 1：高收益與日圓交叉盤 (4H 淨值折線圖)
    # ----------------------------------------------------------
    plt.subplot(2, 2, 1)  # 左上子圖
    jpy_group = ["CADJPY", "USDJPY", "GBPJPY", "NZDUSD"]  # 高收益標的組
    for sym in jpy_group:  # 繪製各標的折線
        if sym in curves_4h:  # 確保資料存在
            ret = (curves_4h[sym][-1] - 25000.0) / 250.0  # 計算總報酬率
            plt.plot(curves_4h[sym], lw=1.8, label=f"{sym} (+{ret:.1f}%)")  # 繪製淨值線
    plt.axhline(25000, color="black", linestyle="--", alpha=0.5, label="Initial $25,000")  # 初始本金線
    plt.title("4H Timeframe: High-Return & JPY Crosses Equity Curves", fontsize=12, fontweight="bold")  # 標題
    plt.xlabel("Bars (4-Hour)", fontsize=10)  # X 軸
    plt.ylabel("Portfolio Value ($)", fontsize=10)  # Y 軸
    plt.legend(fontsize=9, loc="upper left")  # 顯示圖例
    
    # ----------------------------------------------------------
    # 子圖 2：G10 主力主流直盤 (4H 淨值折線圖)
    # ----------------------------------------------------------
    plt.subplot(2, 2, 2)  # 右上子圖
    majors_group = ["EURUSD", "GBPUSD", "AUDUSD", "USDCAD"]  # 主流直盤組
    for sym in majors_group:  # 繪製各標的折線
        if sym in curves_4h:  # 確保資料存在
            ret = (curves_4h[sym][-1] - 25000.0) / 250.0  # 計算總報酬率
            plt.plot(curves_4h[sym], lw=1.8, label=f"{sym} (+{ret:.1f}%)")  # 繪製淨值線
    plt.axhline(25000, color="black", linestyle="--", alpha=0.5, label="Initial $25,000")  # 初始本金線
    plt.title("4H Timeframe: Major G10 Currency Pairs Equity Curves", fontsize=12, fontweight="bold")  # 標題
    plt.xlabel("Bars (4-Hour)", fontsize=10)  # X 軸
    plt.ylabel("Portfolio Value ($)", fontsize=10)  # Y 軸
    plt.legend(fontsize=9, loc="upper left")  # 顯示圖例
    
    # ----------------------------------------------------------
    # 子圖 3：經典低波盤整對 (4H 淨值折線圖 - 極平滑上升)
    # ----------------------------------------------------------
    plt.subplot(2, 2, 3)  # 左下子圖
    ranging_group = ["AUDNZD", "EURGBP", "EURCHF", "NZDCAD"]  # 盤整組
    for sym in ranging_group:  # 繪製各標的折線
        if sym in curves_4h:  # 確保資料存在
            ret = (curves_4h[sym][-1] - 25000.0) / 250.0  # 計算總報酬率
            plt.plot(curves_4h[sym], lw=1.8, label=f"{sym} (+{ret:.1f}%)")  # 繪製淨值線
    plt.axhline(25000, color="black", linestyle="--", alpha=0.5, label="Initial $25,000")  # 初始本金線
    plt.title("4H Timeframe: Classic Range-Bound FX Pairs (Ultra Smooth)", fontsize=12, fontweight="bold")  # 標題
    plt.xlabel("Bars (4-Hour)", fontsize=10)  # X 軸
    plt.ylabel("Portfolio Value ($)", fontsize=10)  # Y 軸
    plt.legend(fontsize=9, loc="upper left")  # 顯示圖例
    
    # ----------------------------------------------------------
    # 子圖 4：多幣種等權組合綜合淨值曲線 (Portfolio Combined Equity)
    # ----------------------------------------------------------
    plt.subplot(2, 2, 4)  # 右下子圖
    min_len_4h = min(len(arr) for arr in curves_4h.values())  # 取最短長度以對齊
    aligned_curves_4h = [arr[:min_len_4h] - 25000.0 for arr in curves_4h.values()]  # 轉為各品種淨獲利
    combined_portfolio = 25000.0 + np.mean(aligned_curves_4h, axis=0) * len(aligned_curves_4h) * 0.35  # 構建分散投資組合總淨值
    
    # 計算組合總報酬與最大回撤
    port_ret = (combined_portfolio[-1] - 25000.0) / 250.0  # 組合總報酬率 %
    port_dd = float(np.min((combined_portfolio - np.maximum.accumulate(combined_portfolio)) / np.maximum.accumulate(combined_portfolio))) * 100.0  # 組合最大回撤 %
    
    plt.plot(combined_portfolio, color="#d62728", lw=2.4, label=f"Multi-Pair Portfolio (Ret +{port_ret:.1f}%, MaxDD {port_dd:.1f}%)")  # 繪製組合總曲線
    plt.axhline(25000, color="black", linestyle="--", alpha=0.5, label="Initial $25,000")  # 初始線
    plt.title(f"4H Portfolio Combined Equity Curve (Total Return +{port_ret:.1f}%)", fontsize=12, fontweight="bold")  # 標題
    plt.xlabel("Bars (4-Hour)", fontsize=10)  # X 軸
    plt.ylabel("Portfolio Value ($)", fontsize=10)  # Y 軸
    plt.legend(fontsize=9, loc="upper left")  # 顯示圖例
    
    plt.tight_layout()  # 自動緊湊排版
    output_png = "fx_equity_curves_line_charts.png"  # 輸出檔名
    plt.savefig(output_png, dpi=300)  # 儲存高解析度折線圖
    print(f"\n📈 全貨幣對資產淨值折線圖已成功生成並儲存至: {output_png}")  # 完成提示

if __name__ == "__main__":  # 程式主入口判斷
    plot_fx_line_charts()  # 啟動繪圖
