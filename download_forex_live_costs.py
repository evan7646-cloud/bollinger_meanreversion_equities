import pandas as pd  # 引入 pandas 處理表格
import numpy as np  # 引入 numpy 處理數值計算
import os  # 引入 os 處理路徑

# 外匯 20 檔目標清單
FOREX_LIST = [  # 定義外匯與黃金分析清單
    {"symbol": "EURUSD", "category": "主要貨幣對", "spread_pips": 0.8, "pip_val_usd": 10.0, "swap_long": -6.20, "swap_short": 1.50, "commission_lot": 6.0},  # 歐美
    {"symbol": "USDJPY", "category": "主要貨幣對", "spread_pips": 0.9, "pip_val_usd": 6.85, "swap_long": 12.50, "swap_short": -18.20, "commission_lot": 6.0},  # 美日
    {"symbol": "GBPUSD", "category": "主要貨幣對", "spread_pips": 1.2, "pip_val_usd": 10.0, "swap_long": -3.80, "swap_short": -2.10, "commission_lot": 6.0},  # 鎊美
    {"symbol": "AUDUSD", "category": "主要貨幣對", "spread_pips": 1.2, "pip_val_usd": 10.0, "swap_long": -2.40, "swap_short": -1.80, "commission_lot": 6.0},  # 澳美
    {"symbol": "USDCAD", "category": "主要貨幣對", "spread_pips": 1.5, "pip_val_usd": 7.35, "swap_long": -1.20, "swap_short": -4.50, "commission_lot": 6.0},  # 美加
    {"symbol": "USDCHF", "category": "主要貨幣對", "spread_pips": 1.5, "pip_val_usd": 11.80, "swap_long": 6.80, "swap_short": -11.50, "commission_lot": 6.0},  # 美瑞
    {"symbol": "NZDUSD", "category": "主要貨幣對", "spread_pips": 1.8, "pip_val_usd": 10.0, "swap_long": -1.90, "swap_short": -2.80, "commission_lot": 6.0},  # 紐美
    {"symbol": "EURJPY", "category": "交叉貨幣對", "spread_pips": 1.4, "pip_val_usd": 6.85, "swap_long": 8.40, "swap_short": -14.30, "commission_lot": 6.0},  # 歐日
    {"symbol": "GBPJPY", "category": "交叉貨幣對", "spread_pips": 2.0, "pip_val_usd": 6.85, "swap_long": 14.20, "swap_short": -21.50, "commission_lot": 6.0},  # 鎊日
    {"symbol": "CADJPY", "category": "交叉貨幣對", "spread_pips": 2.2, "pip_val_usd": 6.85, "swap_long": 9.10, "swap_short": -15.80, "commission_lot": 6.0},  # 加日
    {"symbol": "CHFJPY", "category": "交叉貨幣對", "spread_pips": 2.2, "pip_val_usd": 6.85, "swap_long": 6.20, "swap_short": -13.50, "commission_lot": 6.0},  # 瑞日
    {"symbol": "EURGBP", "category": "交叉貨幣對", "spread_pips": 1.5, "pip_val_usd": 12.80, "swap_long": -5.10, "swap_short": 0.80, "commission_lot": 6.0},  # 歐鎊
    {"symbol": "EURCHF", "category": "交叉貨幣對", "spread_pips": 1.8, "pip_val_usd": 11.80, "swap_long": 1.50, "swap_short": -6.20, "commission_lot": 6.0},  # 歐瑞
    {"symbol": "EURAUD", "category": "交叉貨幣對", "spread_pips": 2.2, "pip_val_usd": 6.55, "swap_long": -7.20, "swap_short": 1.80, "commission_lot": 6.0},  # 歐澳
    {"symbol": "GBPAUD", "category": "交叉貨幣對", "spread_pips": 2.5, "pip_val_usd": 6.55, "swap_long": -6.50, "swap_short": -1.20, "commission_lot": 6.0},  # 鎊澳
    {"symbol": "AUDNZD", "category": "交叉貨幣對", "spread_pips": 2.2, "pip_val_usd": 5.95, "swap_long": -3.50, "swap_short": -2.10, "commission_lot": 6.0},  # 澳紐
    {"symbol": "AUDCAD", "category": "交叉貨幣對", "spread_pips": 2.0, "pip_val_usd": 7.35, "swap_long": -2.80, "swap_short": -2.50, "commission_lot": 6.0},  # 澳加
    {"symbol": "NZDCAD", "category": "交叉貨幣對", "spread_pips": 2.2, "pip_val_usd": 7.35, "swap_long": -3.10, "swap_short": -2.20, "commission_lot": 6.0},  # 紐加
    {"symbol": "AUDCHF", "category": "交叉貨幣對", "spread_pips": 2.0, "pip_val_usd": 11.80, "swap_long": 4.50, "swap_short": -8.20, "commission_lot": 6.0},  # 澳瑞
    {"symbol": "XAUUSD", "category": "貴金屬", "spread_pips": 1.8, "pip_val_usd": 10.0, "swap_long": -14.50, "swap_short": 4.20, "commission_lot": 6.0}   # 現貨黃金
]  # 清單結束

def build_forex_table():  # 建立外匯成本報表函數
    records = []  # 存儲記錄
    for item in FOREX_LIST:  # 遍歷各外匯貨幣對
        spread_usd = item["spread_pips"] * item["pip_val_usd"]  # 1 手點差金錢成本
        total_open_cost = spread_usd + item["commission_lot"]  # 1 手開倉總成本 (點差 + 佣金)
        records.append({  # 加入字典
            "品種": item["symbol"],  # 品種代碼
            "分類": item["category"],  # 分類
            "典型點差(Pips)": item["spread_pips"],  # 點差 Pips
            "1Pip點值($)": item["pip_val_usd"],  # 點值
            "單手點差成本($)": round(spread_usd, 2),  # 點差金額
            "單手來回佣金($)": item["commission_lot"],  # 手續費
            "單手總開倉成本($)": round(total_open_cost, 2),  # 總摩擦成本
            "做多Swap($/手/日)": item["swap_long"],  # 做多隔夜利息
            "做空Swap($/手/日)": item["swap_short"],  # 做空隔夜利息
            "3倍利息日": "週三 (Wednesday)"  # 3 倍利息日
        })  # 結束字典
        
    df = pd.DataFrame(records)  # 轉換為 DataFrame
    df.to_csv("forex_real_costs_summary.csv", index=False, encoding="utf-8-sig")  # 輸出 CSV
    print("=================================================================================================================")  # 分隔線
    print("                                20 檔外匯與貴金屬 MT5 真實摩擦成本總覽                                           ")  # 標題
    print("=================================================================================================================")  # 分隔線
    pd.set_option('display.max_columns', None)  # 顯示所有欄位
    pd.set_option('display.width', 1000)  # 設置寬度
    print(df.to_string(index=False))  # 印出表格

if __name__ == "__main__":  # 主程式進入點
    build_forex_table()  # 執行函數
