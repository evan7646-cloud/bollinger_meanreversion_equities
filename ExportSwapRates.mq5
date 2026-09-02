//+------------------------------------------------------------------+ // 檔案標頭起始
//|                                     ExportSwapRates.mq5         | // 檔案名稱
//|                                  Copyright 2026, Quant Fund Team | // 版權宣告
//|   📤 匯出指定股票清單的真實隔夜倉息(Swap)設定，用於回測成本模型            | // 用途說明
//+------------------------------------------------------------------+ // 檔案標頭結束
#property copyright "Copyright 2026, Quant Fund Team" // 版權所有人設定
#property link      "https://github.com/evan7646-cloud" // 專案官方連結
#property version   "1.00" // 腳本版本號
#property script_show_inputs // 執行前彈出參數對話框供確認
#property description "匯出指定股票清單的真實隔夜倉息(Swap)相關屬性 —— swap模式、多空倉息率、三倍息日等，用於把隔夜庫存費用納入回測成本模型" // 功能說明

//--- 參數設定
input string InpSymbols       = "AVGO,JPM,QCOM,PLTR,AMD,INTC,SIEGn,ASML,DIS,MSFT,JNJ,LMT,SNOW,SBUX"; // 要查詢倉息的標的清單
input string InpSymbolSuffix  = ""; // 自定義後綴（若經紀商為 NVDA.US 則填 .US，留空則自動探測）
input string InpOutputFile    = "swap_rates.csv"; // 輸出檔名（存於 MQL5/Files/）

//+------------------------------------------------------------------+ // 函數分隔
//| 智慧解析經紀商實際股票代碼                                            | // 函數說明
//+------------------------------------------------------------------+ // 分隔線
string ResolveStockSymbol(string base_sym) // 解析實際美股商品名稱
{ // 函數開始
   string candidates[8]; // 候選名稱陣列
   candidates[0] = base_sym + InpSymbolSuffix; // 使用者指定後綴
   candidates[1] = base_sym;                   // 純代碼
   candidates[2] = base_sym + ".US";           // 常見美股 CFD 後綴
   candidates[3] = base_sym + ".a";            // Pepperstone Razor 後綴
   candidates[4] = "#" + base_sym;             // 部分券商前綴
   candidates[5] = base_sym + "_US";           // 底線後綴
   candidates[6] = base_sym + "m";             // 微型後綴
   candidates[7] = base_sym + ".r";            // 浮動點差後綴

   for(int i = 0; i < 8; i++) // 遍歷所有候選
   { // 迴圈開始
      if(candidates[i] == "") continue; // 空字串跳過
      if(SymbolSelect(candidates[i], true)) // 嘗試選取並加入市場報價視窗
         return candidates[i]; // 回傳實際券商代碼
   } // 迴圈結束

   for(int s = 0; s < SymbolsTotal(false); s++) // 搜尋伺服器全部商品
   { // 迴圈開始
      string sname = SymbolName(s, false); // 取得品種名稱
      if(StringFind(sname, base_sym) >= 0) // 包含該美股代碼
      { // 找到相符
         SymbolSelect(sname, true); // 啟用商品
         return sname; // 回傳該代碼
      } // 判斷結束
   } // 迴圈結束

   return ""; // 找不到商品回傳空
} // 函數結束

//+------------------------------------------------------------------+ // 函數分隔
//| 把 SYMBOL_SWAP_MODE 的數字代碼轉成可讀文字，方便判讀單位是「點數」還是「%利率」| // 函數說明
//+------------------------------------------------------------------+ // 分隔線
string SwapModeToString(long mode) // swap模式轉文字
{ // 函數開始
   switch(mode) // 依模式代碼判斷
   { // switch開始
      case SYMBOL_SWAP_MODE_DISABLED:          return "DISABLED(無倉息)"; // 無倉息
      case SYMBOL_SWAP_MODE_POINTS:            return "POINTS(以點數計)"; // 點數
      case SYMBOL_SWAP_MODE_CURRENCY_SYMBOL:   return "CURRENCY_SYMBOL(以商品幣別計)"; // 商品幣別金額
      case SYMBOL_SWAP_MODE_CURRENCY_MARGIN:   return "CURRENCY_MARGIN(以保證金幣別計)"; // 保證金幣別金額
      case SYMBOL_SWAP_MODE_CURRENCY_DEPOSIT:  return "CURRENCY_DEPOSIT(以帳戶幣別計)"; // 帳戶幣別金額
      case SYMBOL_SWAP_MODE_INTEREST_CURRENT:  return "INTEREST_CURRENT(年化%,以現價計算)"; // 年化利率
      case SYMBOL_SWAP_MODE_INTEREST_OPEN:     return "INTEREST_OPEN(年化%,以開倉價計算)"; // 年化利率
      case SYMBOL_SWAP_MODE_REOPEN_CURRENT:    return "REOPEN_CURRENT(每日平倉重開,現價)"; // 重開現價
      case SYMBOL_SWAP_MODE_REOPEN_BID:        return "REOPEN_BID(每日平倉重開,Bid價)"; // 重開Bid價
      default: return "UNKNOWN(" + IntegerToString(mode) + ")"; // 未知模式
   } // switch結束
} // 函數結束

string DayOfWeekToString(long dow) // 星期代碼轉文字（三倍息日）
{ // 函數開始
   switch(dow) // 依代碼判斷
   { // switch開始
      case 0: return "Sunday"; // 週日
      case 1: return "Monday"; // 週一
      case 2: return "Tuesday"; // 週二
      case 3: return "Wednesday"; // 週三
      case 4: return "Thursday"; // 週四
      case 5: return "Friday"; // 週五
      case 6: return "Saturday"; // 週六
      default: return "None(" + IntegerToString(dow) + ")"; // 無/未知
   } // switch結束
} // 函數結束

//+------------------------------------------------------------------+ // 腳本主入口
//| Script program start function                                    | // 主函數說明
//+------------------------------------------------------------------+ // 分隔線
void OnStart() // 腳本執行入口
{ // 主函數開始
   string sym_array[]; // 標的代碼陣列
   int total = StringSplit(InpSymbols, ',', sym_array); // 分割標的清單

   int handle = FileOpen(InpOutputFile, FILE_WRITE | FILE_CSV | FILE_ANSI, ","); // 開啟輸出檔案
   if(handle == INVALID_HANDLE) // 開檔失敗
   { // 錯誤處理
      Print("錯誤：無法建立輸出檔案 ", InpOutputFile, " 錯誤碼: ", GetLastError()); // 印出錯誤
      return; // 結束腳本
   } // 判斷結束

   // 寫入表頭
   FileWrite(handle, "symbol", "swap_mode", "swap_mode_desc", "swap_long", "swap_short", // 表頭前半
             "swap_rollover3days", "swap_rollover3days_desc", "contract_size", "point", // 表頭中段
             "tick_value", "tick_size", "currency_base", "currency_profit"); // 表頭後半

   Print("=================================================================="); // 分隔線
   Print("🚀 開始匯出 ", total, " 檔標的的隔夜倉息設定 ..."); // 標題提示

   for(int i = 0; i < total; i++) // 遍歷所有標的
   { // 迴圈開始
      string base = sym_array[i]; // 原始代碼
      StringTrimLeft(base); // 去除左空白
      StringTrimRight(base); // 去除右空白
      if(base == "") continue; // 空字串跳過

      string sym = ResolveStockSymbol(base); // 解析實際券商代碼
      if(sym == "") // 找不到
      { // 錯誤處理
         Print("⚠️ 無法解析標的: ", base, "，跳過"); // 印出警告
         continue; // 跳過
      } // 判斷結束

      long   swap_mode  = SymbolInfoInteger(sym, SYMBOL_SWAP_MODE); // 倉息計算模式
      double swap_long   = SymbolInfoDouble(sym, SYMBOL_SWAP_LONG);  // 多單倉息
      double swap_short  = SymbolInfoDouble(sym, SYMBOL_SWAP_SHORT); // 空單倉息
      long   rollover3   = SymbolInfoInteger(sym, SYMBOL_SWAP_ROLLOVER3DAYS); // 三倍息是星期幾
      double contract_sz = SymbolInfoDouble(sym, SYMBOL_TRADE_CONTRACT_SIZE); // 合約大小
      double point       = SymbolInfoDouble(sym, SYMBOL_POINT); // 最小報價單位
      double tick_value  = SymbolInfoDouble(sym, SYMBOL_TRADE_TICK_VALUE); // tick價值
      double tick_size   = SymbolInfoDouble(sym, SYMBOL_TRADE_TICK_SIZE); // tick大小
      string curr_base   = SymbolInfoString(sym, SYMBOL_CURRENCY_BASE); // 基礎幣別
      string curr_profit = SymbolInfoString(sym, SYMBOL_CURRENCY_PROFIT); // 損益幣別

      FileWrite(handle, sym, IntegerToString(swap_mode), SwapModeToString(swap_mode), // 寫入資料（前段）
                DoubleToString(swap_long, 6), DoubleToString(swap_short, 6), // 寫入資料（倉息）
                IntegerToString(rollover3), DayOfWeekToString(rollover3), // 寫入資料（三倍息日）
                DoubleToString(contract_sz, 4), DoubleToString(point, 6), // 寫入資料（合約規格）
                DoubleToString(tick_value, 6), DoubleToString(tick_size, 6), // 寫入資料（tick資訊）
                curr_base, curr_profit); // 寫入資料（幣別）

      Print("✅ [", sym, "] swap_mode=", SwapModeToString(swap_mode), // 印出結果（前段）
            " | long=", DoubleToString(swap_long, 4), " short=", DoubleToString(swap_short, 4), // 印出結果（倉息）
            " | 3x息日=", DayOfWeekToString(rollover3)); // 印出結果（三倍息日）
   } // 迴圈結束

   FileClose(handle); // 關閉檔案
   Print("=================================================================="); // 分隔線
   Print("🎉 完成！倉息設定已存至 MQL5/Files/", InpOutputFile); // 完成提示
   Print("=================================================================="); // 分隔線
   Comment("✅ 倉息匯出完成\n檔案：MQL5/Files/", InpOutputFile); // 圖表提示
} // OnStart 結束
