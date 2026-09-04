import pandas as pd  # 匯入 pandas 處理表格
import sys  # 匯入 sys 處理命令行參數
import os  # 匯入 os 處理路徑

def analyze_history_file(file_path):  # 歷史檔案成本分析函數
    if not os.path.exists(file_path):  # 檢查檔案是否存在
        print(f"錯誤: 找不到檔案 {file_path}")  # 印出錯誤
        return  # 結束

    print(f"=== 正在分析 MT5 交易歷史成本: {file_path} ===")  # 印出提示
    if file_path.endswith('.csv'):  # 若為 CSV 檔案
        try:  # 嘗試以 utf-8 讀取
            df = pd.read_csv(file_path, encoding='utf-8')  # 讀取 CSV
        except:  # 若編碼錯誤
            df = pd.read_csv(file_path, encoding='latin1')  # 改用 latin1 讀取
    elif file_path.endswith('.html') or file_path.endswith('.htm'):  # 若為 MT5 匯出的 HTML 報表
        dfs = pd.read_html(file_path)  # 解析 HTML 中的所有表格
        df = dfs[0] if len(dfs) > 0 else None  # 取第一個表格
        if df is None:  # 若無表格
            print("無法從 HTML 解析出交易表格")  # 印出提示
            return  # 結束
    else:  # 若為其他格式
        print("請提供 .csv 或 .html 格式的 MT5 報告")  # 提示格式
        return  # 結束

    # 尋找關鍵欄位 (模糊匹配大小寫與不同經紀商名稱)
    cols = {c.lower().strip(): c for c in df.columns}  # 建立小寫欄位對照字典
    comm_col = next((cols[c] for c in cols if 'commission' in c or '佣金' in c or '手續費' in c), None)  # 尋找手續費欄位
    swap_col = next((cols[c] for c in cols if 'swap' in c or '隔夜' in c or '庫存' in c), None)  # 尋找隔夜利息欄位
    profit_col = next((cols[c] for c in cols if 'profit' in c or '利潤' in c or '損益' in c), None)  # 尋找獲利欄位
    symbol_col = next((cols[c] for c in cols if 'symbol' in c or '品種' in c or '項目' in c), None)  # 尋找品種欄位

    print("已識別欄位:")  # 列印欄位資訊
    print(f"  手續費欄位: {comm_col}")  # 印出手續費欄位
    print(f"  隔夜息欄位: {swap_col}")  # 印出隔夜息欄位
    print(f"  毛利潤欄位: {profit_col}")  # 印出毛利潤欄位
    print(f"  交易品種欄: {symbol_col}")  # 印出品種欄位

    if comm_col:  # 若有手續費欄位
        df[comm_col] = pd.to_numeric(df[comm_col].astype(str).str.replace(' ', '').str.replace(',', ''), errors='coerce').fillna(0)  # 清理數值
    if swap_col:  # 若有隔夜息欄位
        df[swap_col] = pd.to_numeric(df[swap_col].astype(str).str.replace(' ', '').str.replace(',', ''), errors='coerce').fillna(0)  # 清理數值
    if profit_col:  # 若有利潤欄位
        df[profit_col] = pd.to_numeric(df[profit_col].astype(str).str.replace(' ', '').str.replace(',', ''), errors='coerce').fillna(0)  # 清理數值

    tot_comm = df[comm_col].sum() if comm_col else 0.0  # 總手續費
    tot_swap = df[swap_col].sum() if swap_col else 0.0  # 總隔夜息
    tot_profit = df[profit_col].sum() if profit_col else 0.0  # 總毛利潤
    net_pnl = tot_profit + tot_comm + tot_swap  # 淨損益

    print("\n【 成本統計總結 】")  # 印出總結
    print(f"  總交易筆數: {len(df)}")  # 總筆數
    print(f"  總手續費支出 (Commission): {tot_comm:.2f}")  # 總手續費
    print(f"  總隔夜利息收付 (Swap)     : {tot_swap:.2f}")  # 總隔夜息
    print(f"  總交易摩擦成本 (Cost)    : {(abs(tot_comm) + abs(tot_swap)):.2f}")  # 總摩擦成本
    print(f"  總毛利潤 (Gross Profit)  : {tot_profit:.2f}")  # 總毛利
    print(f"  總淨損益 (Net Profit)    : {net_pnl:.2f}")  # 淨損益
    if tot_profit != 0:  # 避免除以零
        cost_ratio = (abs(tot_comm) + abs(tot_swap)) / abs(tot_profit) * 100.0  # 計算成本佔毛利百分比
        print(f"  交易成本佔毛利比例       : {cost_ratio:.2f}%")  # 顯示成本佔比

    if symbol_col and (comm_col or swap_col):  # 若有品種資訊
        agg_dict = {}  # 聚合字典
        if comm_col: agg_dict[comm_col] = 'sum'  # 聚合手續費
        if swap_col: agg_dict[swap_col] = 'sum'  # 聚合利息
        if profit_col: agg_dict[profit_col] = 'sum'  # 聚合獲利
        sym_summary = df.groupby(symbol_col).agg(agg_dict).reset_index()  # 依品種匯總
        print("\n【 各品種手續費與隔夜息統計 】")  # 顯示各品種標題
        print(sym_summary.to_string(index=False))  # 印出表格

if __name__ == "__main__":  # 主程式進入點
    target_file = sys.argv[1] if len(sys.argv) > 1 else "mt5_history_deals.csv"  # 預設或命令列檔案路徑
    analyze_history_file(target_file)  # 執行分析
