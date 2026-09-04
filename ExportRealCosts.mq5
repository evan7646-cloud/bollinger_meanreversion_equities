//+------------------------------------------------------------------+ // 檔案標頭註釋開始
//|                                              ExportRealCosts.mq5 | // 腳本檔案名稱
//|                                  Copyright 2026, Antigravity AI  | // 版權聲明
//|                                             https://www.mql5.com | // 參考網址
//+------------------------------------------------------------------+ // 檔案標頭註釋結束
#property copyright "Antigravity AI"                                   // 屬性：版權聲明
#property link      "https://www.mql5.com"                             // 屬性：官方連結
#property version   "1.00"                                             // 屬性：版本號碼

//+------------------------------------------------------------------+ // 函數區隔線
//| 計算每手每日 Swap 實際換算金額 (以帳戶幣別計)                     | // 函數說明
//+------------------------------------------------------------------+ // 函數區隔線
double CalculateSwapCostUSD(string symbol, double swap_val, double price) // 計算隔夜利息金額函數
{                                                                      // 函數主體開始
   ENUM_SYMBOL_SWAP_MODE mode = (ENUM_SYMBOL_SWAP_MODE)SymbolInfoInteger(symbol, SYMBOL_SWAP_MODE); // 獲取利息計算模式
   double point = SymbolInfoDouble(symbol, SYMBOL_POINT);              // 獲取最小點值
   double tick_size = SymbolInfoDouble(symbol, SYMBOL_TRADE_TICK_SIZE);// 獲取最小跳動價格
   if(tick_size <= 0) tick_size = point;                               // 防止除以零錯誤
   double tick_val = SymbolInfoDouble(symbol, SYMBOL_TRADE_TICK_VALUE);// 獲取單跳點值價值
   double contract_size = SymbolInfoDouble(symbol, SYMBOL_TRADE_CONTRACT_SIZE); // 獲取合約大小

   if(mode == SYMBOL_SWAP_MODE_POINTS)                                 // 若以點數計算
   {                                                                   // 點數計算區塊
      return (swap_val * (point / tick_size) * tick_val);             // 點數乘上單點價值
   }                                                                   // 結束點數計算
   else if(mode == SYMBOL_SWAP_MODE_CURRENCY_SYMBOL)                   // 若以基礎貨幣計算
   {                                                                   // 基礎貨幣區塊
      return (swap_val * (tick_val / tick_size));                      // 換算為帳戶貨幣
   }                                                                   // 結束基礎貨幣
   else if(mode == SYMBOL_SWAP_MODE_CURRENCY_MARGIN || mode == SYMBOL_SWAP_MODE_CURRENCY_DEPOSIT) // 若為保證金或帳戶貨幣
   {                                                                   // 貨幣直接計算
      return swap_val;                                                 // 直接返回數值
   }                                                                   // 結束貨幣直接計算
   else if(mode == SYMBOL_SWAP_MODE_INTEREST_CURRENT)                  // 若為年化利率百分比
   {                                                                   // 利率計算區塊
      return ((swap_val / 100.0) * (price * contract_size) / 360.0);   // 年化百分比除以 360 天
   }                                                                   // 結束利率計算
   return swap_val;                                                    // 預設返回原始值
}                                                                      // 結束函數

//+------------------------------------------------------------------+ // 函數區隔線
//| 腳本主執行入口函數                                               | // 函數說明
//+------------------------------------------------------------------+ // 函數區隔線
void OnStart()                                                         // 主程式啟動函數
{                                                                      // 開始執行
   Print(">>> [開始執行] 市場報價真實成本匯出腳本 <<<");              // 印出開始訊息
   string file_name = "mt5_real_costs_mql5.csv";                       // 匯出的 CSV 檔案名稱
   ResetLastError();                                                   // 重設錯誤代碼
   int file_handle = FileOpen(file_name, FILE_WRITE|FILE_CSV|FILE_ANSI, ","); // 開啟/建立 CSV 檔案
   if(file_handle == INVALID_HANDLE)                                   // 檢查檔案開啟是否成功
   {                                                                   // 若開啟失敗
      Print("❌ 無法建立檔案: ", file_name, " 錯誤代碼: ", GetLastError());// 印出錯誤代碼
      return;                                                          // 結束執行
   }                                                                   // 結束檢查

   // 寫入 CSV 標題列
   FileWrite(file_handle, "Symbol", "Bid", "Ask", "Spread_Pts", "Spread_USD_Per_Lot", "Spread_bps", "Swap_Long_USD_Day", "Swap_Short_USD_Day", "3Day_Swap_Day", "Contract_Size", "Tick_Value"); // 寫入欄位名稱

   int total_symbols = SymbolsTotal(true);                             // 獲取市場報價中選取的品種總數
   if(total_symbols <= 0) total_symbols = SymbolsTotal(false);         // 若市場報價為空則抓全部品種
   int export_count = 0;                                               // 成功計數

   for(int i = 0; i < total_symbols; i++)                              // 遍歷所有品種
   {                                                                   // 迴圈主體開始
      string symbol = SymbolName(i, true);                             // 獲取當前品種名稱
      if(symbol == "") symbol = SymbolName(i, false);                  // 若為空則從全商品清單獲取
      if(symbol == "") continue;                                       // 若仍為空則跳過

      SymbolSelect(symbol, true);                                      // 確保品種已訂閱並可取即時數據
      MqlTick tick;                                                    // 宣告即時報價結構體
      if(!SymbolInfoTick(symbol, tick) || tick.bid <= 0 || tick.ask <= 0) continue; // 無即時報價跳過

      double point = SymbolInfoDouble(symbol, SYMBOL_POINT);           // 獲取點單位
      double tick_size = SymbolInfoDouble(symbol, SYMBOL_TRADE_TICK_SIZE); // 獲取跳動單位
      if(tick_size <= 0) tick_size = point;                            // 防除以零保護
      double tick_val = SymbolInfoDouble(symbol, SYMBOL_TRADE_TICK_VALUE); // 獲取跳動點值
      double contract_size = SymbolInfoDouble(symbol, SYMBOL_TRADE_CONTRACT_SIZE); // 獲取合約大小

      double spread_pts = (tick.ask - tick.bid) / (point > 0 ? point : 1.0); // 計算點差點數
      double mid_price = (tick.ask + tick.bid) / 2.0;                  // 計算中間價格
      double spread_bps = (mid_price > 0) ? ((tick.ask - tick.bid) / mid_price) * 10000.0 : 0; // 計算點差 bps
      double spread_cost_1lot = spread_pts * (point / tick_size) * tick_val; // 計算每標準手點差美元成本

      double swap_long = SymbolInfoDouble(symbol, SYMBOL_SWAP_LONG);   // 獲取做多 Swap 原始值
      double swap_short = SymbolInfoDouble(symbol, SYMBOL_SWAP_SHORT); // 獲取做空 Swap 原始值
      double swap_long_usd = CalculateSwapCostUSD(symbol, swap_long, mid_price); // 換算做多每手每日美元
      double swap_short_usd = CalculateSwapCostUSD(symbol, swap_short, mid_price); // 換算做空每手每日美元

      ENUM_DAY_OF_WEEK swap_3day_enum = (ENUM_DAY_OF_WEEK)SymbolInfoInteger(symbol, SYMBOL_SWAP_ROLLOVER3DAYS); // 獲取 3 倍利息日
      string swap_3day_str = EnumToString(swap_3day_enum);             // 轉換枚舉為字串

      // 寫入數據行到 CSV
      FileWrite(file_handle, symbol,                                   // 寫入品種名稱
                             DoubleToString(tick.bid, (int)SymbolInfoInteger(symbol, SYMBOL_DIGITS)), // 寫入 Bid
                             DoubleToString(tick.ask, (int)SymbolInfoInteger(symbol, SYMBOL_DIGITS)), // 寫入 Ask
                             DoubleToString(spread_pts, 1),            // 寫入點差點數
                             DoubleToString(spread_cost_1lot, 2),      // 寫入單手點差成本
                             DoubleToString(spread_bps, 2),            // 寫入點差 bps
                             DoubleToString(swap_long_usd, 2),         // 寫入多單每日 Swap
                             DoubleToString(swap_short_usd, 2),        // 寫入空單每日 Swap
                             swap_3day_str,                            // 寫入 3 倍利息結算日
                             DoubleToString(contract_size, 0),         // 寫入合約大小
                             DoubleToString(tick_val, 4));             // 寫入每跳點值
      export_count++;                                                  // 計數加一
   }                                                                   // 結束品種迴圈

   FileFlush(file_handle);                                             // 強制寫入
   FileClose(file_handle);                                             // 關閉 CSV 檔案
   string path = TerminalInfoString(TERMINAL_DATA_PATH) + "\\MQL5\\Files\\" + file_name; // 獲取實際檔案儲存路徑
   Print("🎉 匯出完成！共匯出 ", export_count, " 檔品種至: ", path);   // 印出成功訊息與路徑
   Alert("真實成本資料已成功匯出！共 ", export_count, " 檔商品\n檔案: ", file_name); // 彈出視窗提示使用者
}                                                                      // 腳本執行結束
