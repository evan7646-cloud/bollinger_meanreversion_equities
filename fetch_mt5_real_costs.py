import MetaTrader5 as mt5  # 匯入 MetaTrader 5 官方 API 套件
import pandas as pd  # 匯入 pandas 處理數據與表格計算
from datetime import datetime, timedelta  # 匯入時間處理模組
import sys  # 匯入系統模組處理退出與參數

def get_swap_cost_usd(sym_info, swap_val, current_price):  # 計算每手每日 Swap 美元金額的輔助函數
    mode = sym_info.swap_mode  # 獲取隔夜利息計算模式
    point = sym_info.point  # 獲取品種的最小報價單位
    tick_size = sym_info.tick_size if sym_info.tick_size > 0 else point  # 獲取跳動點大小，避免除以零
    tick_val = sym_info.tick_value  # 獲取跳動點價值 (以帳戶幣別計算)
    contract_size = sym_info.trade_contract_size  # 獲取標準合約大小
    if mode == mt5.SYMBOL_SWAP_MODE_POINTS:  # 若計算模式為點數制
        cost = swap_val * (point / tick_size) * tick_val  # 計算公式：點數 * 點值
        return cost  # 返回換算後的每日金額
    elif mode == mt5.SYMBOL_SWAP_MODE_CURRENCY_SYMBOL:  # 若計算模式為基礎貨幣
        cost = swap_val * (tick_val / tick_size)  # 換算為帳戶貨幣
        return cost  # 返回每日利息金額
    elif mode == mt5.SYMBOL_SWAP_MODE_CURRENCY_MARGIN:  # 若計算模式為保證金貨幣
        return swap_val  # 直接返回金額
    elif mode == mt5.SYMBOL_SWAP_MODE_CURRENCY_DEPOSIT:  # 若計算模式為入金帳戶貨幣
        return swap_val  # 直接返回利息金額
    elif mode == mt5.SYMBOL_SWAP_MODE_INTEREST_CURRENT:  # 若計算模式為年化百分比
        cost = (swap_val / 100.0) * (current_price * contract_size) / 360.0  # 年化利息除以 360 天
        return cost  # 返回每日換算金額
    return swap_val  # 其他模式預設返回原始值

def fetch_real_costs():  # 主函數：抓取並輸出所有即時真實交易成本
    if not mt5.initialize():  # 初始化 MT5 連線
        print(f"MT5 初始化失敗，錯誤代碼: {mt5.last_error()}")  # 印出連線失敗訊息
        sys.exit(1)  # 連線失敗則退出程式

    account_info = mt5.account_info()  # 獲取當前登入帳戶資訊
    if account_info is None:  # 若無法獲取帳戶資訊
        print("無法獲取帳戶資訊，請確認 MT5 是否已登入")  # 印出警告
        mt5.shutdown()  # 關閉 MT5 連線
        return  # 結束函數

    acc_currency = account_info.currency  # 獲取帳戶結算貨幣 (如 USD)
    print(f"=== 帳戶: {account_info.login} | 槓桿: 1:{account_info.leverage} | 結算貨幣: {acc_currency} ===")  # 顯示帳戶概要

    symbols = mt5.symbols_get()  # 獲取所有品種資訊
    selected_symbols = [s for s in symbols if s.select]  # 僅篩選目前在「市場報價」中已勾選/顯示的品種
    if not selected_symbols:  # 若市場報價為空
        selected_symbols = symbols[:30]  # 預設選取前 30 個品種

    records = []  # 建立存放即時成本結果的列表

    for s in selected_symbols:  # 遍歷每一個品種
        sym_name = s.name  # 取得品種代碼
        tick = mt5.symbol_info_tick(sym_name)  # 取得最新報價 Tick
        if tick is None or tick.bid == 0 or tick.ask == 0:  # 若無有效報價則跳過
            continue  # 跳過當前品種

        bid = tick.bid  # 最新買入價 (Bid)
        ask = tick.ask  # 最新賣出價 (Ask)
        spread_pts = (ask - bid) / s.point if s.point > 0 else 0  # 計算點差點數 (Points)
        mid_price = (ask + bid) / 2.0  # 計算中間價格
        spread_bps = ((ask - bid) / mid_price) * 10000.0 if mid_price > 0 else 0  # 換算為基點 bps (0.01%)

        tick_size = s.tick_size if s.tick_size > 0 else s.point  # 最小跳動點
        tick_val = s.tick_value  # 每跳動 1 個 tick_size 的帳戶幣值價值
        spread_cost_1lot = spread_pts * (s.point / tick_size) * tick_val  # 計算做 1 標準手的點差金錢成本 (以帳戶幣別計)

        swap_long_usd = get_swap_cost_usd(s, s.swap_long, mid_price)  # 計算做多 1 手每日 Swap 金額
        swap_short_usd = get_swap_cost_usd(s, s.swap_short, mid_price)  # 計算做空 1 手每日 Swap 金額

        day_map = {0: "週一", 1: "週二", 2: "週三", 3: "週四", 4: "週五", 5: "週六", 6: "週日"}  # 星期對照表
        swap_3day = day_map.get(s.swap_rollover3days, str(s.swap_rollover3days))  # 三日隔夜利息收付日

        records.append({  # 將成本資料加入字典
            "品種代碼": sym_name,  # 品種名稱
            "現價(Bid)": bid,  # 賣出價
            "現價(Ask)": ask,  # 買入價
            "點差(Pts)": round(spread_pts, 1),  # 點差點數
            "點差成本/手": round(spread_cost_1lot, 2),  # 每 1 標準手買賣點差金額
            "點差(bps)": round(spread_bps, 2),  # 點差基點 (bps)
            "做多Swap/手/日": round(swap_long_usd, 2),  # 做多 1 手每日 Swap
            "做空Swap/手/日": round(swap_short_usd, 2),  # 做空 1 手每日 Swap
            "3倍Swap日": swap_3day,  # 三倍利息結算日
            "合約大小": s.trade_contract_size,  # 單手合約大小
            "每跳點值": round(tick_val, 4)  # 最小跳動點值
        })  # 結束紀錄結構

    df_live = pd.DataFrame(records)  # 轉換為 pandas DataFrame 表格
    print("\n【 1. 即時交易成本規格表 (Market Specification Real Cost) 】")  # 列印區塊標題
    pd.set_option('display.max_columns', None)  # 設置顯示所有欄位
    pd.set_option('display.width', 1000)  # 設置終端顯示寬度
    print(df_live.to_string(index=False))  # 印出完整表格

    df_live.to_csv("mt5_live_costs.csv", index=False, encoding="utf-8-sig")  # 輸出即時成本 CSV 檔
    print("\n--> 即時成本已成功匯出至 mt5_live_costs.csv")  # 提示匯出成功

    # 接下來拉取歷史實際成交紀錄 (Deals) 統計真實發生的手續費、利息與損益
    now = datetime.now()  # 獲取當前時間
    start_date = now - timedelta(days=90)  # 預設統計過去 90 天
    deals = mt5.history_deals_get(start_date, now)  # 從 MT5 抓取歷史成交紀錄

    if deals and len(deals) > 0:  # 若有成交紀錄
        df_deals = pd.DataFrame(list(deals), columns=deals[0]._asdict().keys())  # 轉換成交紀錄為 DataFrame
        df_deals["time"] = pd.to_datetime(df_deals["time"], unit="s")  # 將時間戳轉換為標準時間格式

        total_commission = df_deals["commission"].sum()  # 統計總付出佣金/手續費
        total_swap = df_deals["swap"].sum()  # 統計總付出隔夜利息
        total_fee = df_deals["fee"].sum() if "fee" in df_deals.columns else 0.0  # 統計其他費用
        total_profit = df_deals["profit"].sum()  # 統計總實現毛利潤
        net_profit = total_profit + total_commission + total_swap + total_fee  # 計算淨利潤

        print("\n【 2. 歷史真實支出成本統計 (過去 90 天 Deals 總計) 】")  # 印出歷史成本標題
        print(f"總成交筆數 (Deals)  : {len(df_deals)} 筆")  # 顯示成交筆數
        print(f"總手續費 (Commission): {total_commission:.2f} {acc_currency}")  # 顯示總手續費
        print(f"總隔夜息 (Swap)      : {total_swap:.2f} {acc_currency}")  # 顯示總隔夜利息
        print(f"其他規費 (Fee)       : {total_fee:.2f} {acc_currency}")  # 顯示其他手續費
        print(f"總毛利潤 (Gross PnL) : {total_profit:.2f} {acc_currency}")  # 顯示總毛利潤
        print(f"總淨利潤 (Net PnL)   : {net_profit:.2f} {acc_currency}")  # 顯示淨利潤
        total_cost = abs(total_commission) + abs(total_swap) + abs(total_fee)  # 計算絕對值總交易成本
        print(f"真實交易摩擦總成本   : {total_cost:.2f} {acc_currency}")  # 顯示總摩擦成本

        # 各品種的手續費與利息匯總
        by_symbol = df_deals.groupby("symbol").agg({  # 依品種聚合
            "volume": "sum",  # 累計成交手數
            "commission": "sum",  # 累計手續費
            "swap": "sum",  # 累計利息
            "profit": "sum"  # 累計毛利
        }).reset_index()  # 重設索引
        by_symbol["淨損益"] = by_symbol["profit"] + by_symbol["commission"] + by_symbol["swap"]  # 計算各品種淨損益
        print("\n【 各品種歷史成本分布 】")  # 印出分布標題
        print(by_symbol.to_string(index=False))  # 印出各品種統計
        df_deals.to_csv("mt5_history_deals.csv", index=False, encoding="utf-8-sig")  # 匯出歷史成交明細
        print("--> 歷史成交紀錄已匯出至 mt5_history_deals.csv")  # 提示匯出成功
    else:  # 若無歷史成交
        print("\n過去 90 天無成交紀錄或帳戶為新帳戶")  # 提示無紀錄

    mt5.shutdown()  # 關閉 MT5 連線

if __name__ == "__main__":  # 主程式進入點
    fetch_real_costs()  # 執行成本抓取函數
