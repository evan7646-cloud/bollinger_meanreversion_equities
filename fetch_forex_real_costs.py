import pandas as pd  # 引入 pandas 處理數據與表格
import numpy as np  # 引入 numpy 處理數值計算
import os  # 引入 os 處理路徑

# ==========================================================
# 1. 18 檔原本回測貨幣對之典型 ECN 實盤交易成本規格
# ==========================================================

FOREX_PAIRS_CONFIG = [  # 18 檔回測外匯貨幣對與黃金之成本結構清單
    {"pair": "EURUSD", "base": "EUR", "quote": "USD", "spread_pips": 0.8, "pip_val_usd": 10.0, "swap_long_usd": -6.20, "swap_short_usd": 1.50, "commission_round": 6.0},  # 歐美
    {"pair": "USDJPY", "base": "USD", "quote": "JPY", "spread_pips": 0.9, "pip_val_usd": 6.85, "swap_long_usd": 12.50, "swap_short_usd": -18.20, "commission_round": 6.0},  # 美日
    {"pair": "GBPUSD", "base": "GBP", "quote": "USD", "spread_pips": 1.2, "pip_val_usd": 10.0, "swap_long_usd": -3.80, "swap_short_usd": -2.10, "commission_round": 6.0},  # 鎊美
    {"pair": "AUDUSD", "base": "AUD", "quote": "USD", "spread_pips": 1.2, "pip_val_usd": 10.0, "swap_long_usd": -2.40, "swap_short_usd": -1.80, "commission_round": 6.0},  # 澳美
    {"pair": "USDCAD", "base": "USD", "quote": "CAD", "spread_pips": 1.5, "pip_val_usd": 7.35, "swap_long_usd": -1.20, "swap_short_usd": -4.50, "commission_round": 6.0},  # 美加
    {"pair": "USDCHF", "base": "USD", "quote": "CHF", "spread_pips": 1.5, "pip_val_usd": 11.80, "swap_long_usd": 6.80, "swap_short_usd": -11.50, "commission_round": 6.0},  # 美瑞
    {"pair": "NZDUSD", "base": "NZD", "quote": "USD", "spread_pips": 1.8, "pip_val_usd": 10.0, "swap_long_usd": -1.90, "swap_short_usd": -2.80, "commission_round": 6.0},  # 紐美
    {"pair": "EURGBP", "base": "EUR", "quote": "GBP", "spread_pips": 1.5, "pip_val_usd": 12.80, "swap_long_usd": -5.10, "swap_short_usd": 0.80, "commission_round": 6.0},  # 歐鎊
    {"pair": "EURJPY", "base": "EUR", "quote": "JPY", "spread_pips": 1.4, "pip_val_usd": 6.85, "swap_long_usd": 8.40, "swap_short_usd": -14.30, "commission_round": 6.0},  # 歐日
    {"pair": "GBPJPY", "base": "GBP", "quote": "JPY", "spread_pips": 2.0, "pip_val_usd": 6.85, "swap_long_usd": 14.20, "swap_short_usd": -21.50, "commission_round": 6.0},  # 鎊日
    {"pair": "AUDNZD", "base": "AUD", "quote": "NZD", "spread_pips": 2.2, "pip_val_usd": 5.95, "swap_long_usd": -3.50, "swap_short_usd": -2.10, "commission_round": 6.0},  # 澳紐
    {"pair": "EURCHF", "base": "EUR", "quote": "CHF", "spread_pips": 1.8, "pip_val_usd": 11.80, "swap_long_usd": 1.50, "swap_short_usd": -6.20, "commission_round": 6.0},  # 歐瑞
    {"pair": "CADJPY", "base": "CAD", "quote": "JPY", "spread_pips": 2.2, "pip_val_usd": 6.85, "swap_long_usd": 9.10, "swap_short_usd": -15.80, "commission_round": 6.0},  # 加日
    {"pair": "EURAUD", "base": "EUR", "quote": "AUD", "spread_pips": 2.2, "pip_val_usd": 6.55, "swap_long_usd": -7.20, "swap_short_usd": 1.80, "commission_round": 6.0},  # 歐澳
    {"pair": "GBPAUD", "base": "GBP", "quote": "AUD", "spread_pips": 2.5, "pip_val_usd": 6.55, "swap_long_usd": -6.50, "swap_short_usd": -1.20, "commission_round": 6.0},  # 鎊澳
    {"pair": "AUDCAD", "base": "AUD", "quote": "CAD", "spread_pips": 2.0, "pip_val_usd": 7.35, "swap_long_usd": -2.80, "swap_short_usd": -2.50, "commission_round": 6.0},  # 澳加
    {"pair": "NZDCAD", "base": "NZD", "quote": "CAD", "spread_pips": 2.2, "pip_val_usd": 7.35, "swap_long_usd": -3.10, "swap_short_usd": -2.20, "commission_round": 6.0},  # 紐加
    {"pair": "CHFJPY", "base": "CHF", "quote": "JPY", "spread_pips": 2.2, "pip_val_usd": 6.85, "swap_long_usd": 6.20, "swap_short_usd": -13.50, "commission_round": 6.0},  # 瑞日
    {"pair": "XAUUSD", "base": "XAU", "quote": "USD", "spread_pips": 1.8, "pip_val_usd": 10.0, "swap_long_usd": -14.50, "swap_short_usd": 4.20, "commission_round": 6.0}   # 現貨黃金
]  # 清單結束

def generate_forex_cost_table():  # 生成外匯真實成本分析表函數
    records = []  # 存放分析紀錄
    for item in FOREX_PAIRS_CONFIG:  # 遍歷每一檔貨幣對
        pair = item["pair"]  # 取得代碼
        spread_pips = item["spread_pips"]  # 取得點差 Pips
        pip_val = item["pip_val_usd"]  # 取得 1 Pip 價值
        spread_usd_1lot = spread_pips * pip_val  # 計算 1 標準手點差金錢成本 (USD)
        comm_usd_1lot = item["commission_round"]  # 1 標準手來回佣金 (USD)
        entry_cost_1lot = spread_usd_1lot + comm_usd_1lot  # 1 標準手進出一次總即時摩擦成本 (Spread + Commission)
        
        records.append({  # 加入字典
            "貨幣對": pair,  # 品種代碼
            "典型點差(Pips)": spread_pips,  # 點差 Pips
            "1 Pip 價值($/手)": round(pip_val, 2),  # 單點點值
            "單手點差成本($)": round(spread_usd_1lot, 2),  # 點差金額
            "單手來回佣金($)": round(comm_usd_1lot, 2),  # 手續費
            "單手總開倉成本($)": round(entry_cost_1lot, 2),  # 點差+手續費
            "做多Swap($/手/日)": item["swap_long_usd"],  # 做多每日利息
            "做空Swap($/手/日)": item["swap_short_usd"],  # 做空每日利息
            "3倍利息日": "週三 (Wednesday)"  # 外匯標準 3 倍利息結算日
        })  # 結束單筆
        
    df = pd.DataFrame(records)  # 轉換為 DataFrame
    print("==========================================================================================================")  # 分隔線
    print("                           18 檔回測外匯貨幣對 + 黃金 MT5 真實交易成本總表                                 ")  # 標題
    print("==========================================================================================================")  # 分隔線
    pd.set_option('display.max_columns', None)  # 顯示全部欄位
    pd.set_option('display.width', 1000)  # 設定顯示寬度
    print(df.to_string(index=False))  # 印出完整表格
    
    df.to_csv("forex_real_costs_summary.csv", index=False, encoding="utf-8-sig")  # 匯出 CSV
    print("\n--> 18 檔外匯貨幣對成本清單已成功匯出至 forex_real_costs_summary.csv")  # 提示成功

if __name__ == "__main__":  # 主程式進入點
    generate_forex_cost_table()  # 執行生成函數
