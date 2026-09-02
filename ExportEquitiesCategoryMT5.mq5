//+------------------------------------------------------------------+ // 檔案標頭起始
//|                              ExportEquitiesCategoryMT5.mq5      | // 檔案名稱
//|                                  Copyright 2026, Quant Fund Team | // 版權宣告
//|   📤 自動掃描帳戶內「Equities I CFD」「Equities II CFD」分類下的股票     | // 用途說明
//|      並匯出其 1m/5m/15m K 線（含真實 spread）至 MQL5/Files/            | // 用途說明續
//+------------------------------------------------------------------+ // 檔案標頭結束
#property copyright "Copyright 2026, Quant Fund Team" // 版權所有人設定
#property link      "https://github.com/evan7646-cloud" // 專案官方連結
#property version   "1.00" // 腳本版本號
#property script_show_inputs // 執行前彈出參數對話框供確認
#property description "自動掃描 SYMBOL_PATH 屬於 Equities I CFD / Equities II CFD 分類的商品，不需手動列清單，匯出其 1m/5m/15m K線（含真實spread）與清單摘要至 MQL5/Files/" // 功能說明

//--- 參數設定
input string InpCategory1     = "Equities I CFD";  // 分類關鍵字 1（比對 SYMBOL_PATH 是否包含此字串）
input string InpCategory2     = "Equities II CFD"; // 分類關鍵字 2
input bool   InpExport15M     = true;    // 是否匯出 15 分鐘 K 線
input bool   InpExport5M      = true;    // 是否匯出 5 分鐘 K 線
input bool   InpExport1M      = true;    // 是否匯出 1 分鐘高頻 K 線
input int    InpMaxBars       = 100000;  // 每個週期最多匯出的 K 棒數（伺服器實際保留量可能較少）
input bool   InpExportTicks   = false;   // 是否匯出真實 Tick（商品數量多時會非常耗時，預設關閉）
input int    InpMaxTicks      = 200000;  // 匯出 Tick 時每個品種最多筆數
input int    InpMaxSymbols    = 500;     // 安全上限：最多處理幾檔商品（避免分類異常導致誤掃全市場）

//+------------------------------------------------------------------+ // 函數分隔
//| 不分大小寫的字串包含比對（MQL5 StringFind 是區分大小寫的，先轉大寫再比對）| // 函數說明
//+------------------------------------------------------------------+ // 分隔線
bool ContainsIgnoreCase(string haystack, string needle) // 不分大小寫包含比對函數
{ // 函數開始
   string h = haystack; // 複製待搜尋字串
   string n = needle;   // 複製關鍵字
   StringToUpper(h); // 轉為大寫
   StringToUpper(n); // 轉為大寫
   return (StringFind(h, n) >= 0); // 回傳是否包含
} // 函數結束

//+------------------------------------------------------------------+ // 函數分隔
//| 掃描全部商品，找出 SYMBOL_PATH 屬於指定分類的商品，回傳符合數量           | // 函數說明
//+------------------------------------------------------------------+ // 分隔線
int ScanCategorySymbols(string &matched[], string &matched_path[], string &matched_category[]) // 掃描分類商品
{ // 函數開始
   int total = SymbolsTotal(false); // 取得伺服器全部商品總數
   int found = 0; // 已找到計數

   ArrayResize(matched, InpMaxSymbols); // 預先配置陣列大小
   ArrayResize(matched_path, InpMaxSymbols); // 同步配置
   ArrayResize(matched_category, InpMaxSymbols); // 同步配置

   for(int i = 0; i < total && found < InpMaxSymbols; i++) // 遍歷所有商品（受安全上限保護）
   { // 迴圈開始
      string name = SymbolName(i, false); // 取得商品名稱
      if(name == "") continue; // 名稱無效跳過

      string path = SymbolInfoString(name, SYMBOL_PATH); // 取得分類路徑

      string cat = ""; // 命中的分類名稱
      if(ContainsIgnoreCase(path, InpCategory1)) cat = InpCategory1; // 命中分類1
      else if(ContainsIgnoreCase(path, InpCategory2)) cat = InpCategory2; // 命中分類2
      else continue; // 都沒命中，跳過此商品

      matched[found] = name; // 記錄商品名稱
      matched_path[found] = path; // 記錄完整路徑
      matched_category[found] = cat; // 記錄命中的分類
      found++; // 找到數量+1
   } // 迴圈結束

   ArrayResize(matched, found); // 縮減陣列到實際找到數量
   ArrayResize(matched_path, found); // 同步縮減
   ArrayResize(matched_category, found); // 同步縮減
   return found; // 回傳找到的商品數
} // 函數結束

//+------------------------------------------------------------------+ // 函數分隔
//| 匯出單一商品指定週期的 K 線數據 (含真實 Spread 點差欄位)                | // 函數說明
//+------------------------------------------------------------------+ // 分隔線
int ExportBarHistory(string sym, ENUM_TIMEFRAMES tf, string tf_name) // 匯出 K 線函數，回傳匯出根數
{ // 函數開始
   MqlRates rates[]; // K 棒結構陣列
   ArraySetAsSeries(rates, false); // 按時間正序
   int copied = CopyRates(sym, tf, 0, InpMaxBars, rates); // 複製 K 棒
   if(copied <= 0) return 0; // 失敗回傳0

   string safe_name = sym; // 檔名用代碼（部分商品代碼含特殊字元，簡單處理常見情況）
   StringReplace(safe_name, "/", "_"); // 斜線換底線，避免檔名錯誤
   string filename = "EQ_" + safe_name + "_" + tf_name + ".csv"; // 檔案名稱
   int handle = FileOpen(filename, FILE_WRITE | FILE_CSV | FILE_ANSI, ","); // 開啟檔案
   if(handle == INVALID_HANDLE) return 0; // 開啟失敗回傳0

   // 寫入 K 線標頭 (含真實開高低收、真實點差 spread 與 Tick 成交量)
   FileWrite(handle, "datetime", "open", "high", "low", "close", "spread_points", "spread_bps", "tick_volume", "real_volume"); // 寫入標頭

   double pt = SymbolInfoDouble(sym, SYMBOL_POINT); // 點值
   if(pt <= 0) pt = 0.01; // 預設 0.01

   for(int i = 0; i < copied; i++) // 遍歷所有 K 棒
   { // 迴圈開始
      string t_str = TimeToString(rates[i].time, TIME_DATE | TIME_MINUTES); // 格式化時間
      double spread_pts = (double)rates[i].spread; // 該根 K 棒的券商實際點差 (點數)
      double spread_bps = (rates[i].close > 0) ? (rates[i].spread * pt / rates[i].close) * 10000.0 : 0.0; // 換算為基點 bps

      FileWrite(handle, t_str, DoubleToString(rates[i].open, 4), DoubleToString(rates[i].high, 4), // 寫入單根K棒（前半）
                DoubleToString(rates[i].low, 4), DoubleToString(rates[i].close, 4), // 寫入單根K棒（中段）
                DoubleToString(spread_pts, 1), DoubleToString(spread_bps, 2), // 寫入單根K棒（點差）
                IntegerToString(rates[i].tick_volume), IntegerToString(rates[i].real_volume)); // 寫入單根K棒（成交量）
   } // 迴圈結束

   FileClose(handle); // 關閉檔案
   return copied; // 回傳匯出根數
} // 函數結束

//+------------------------------------------------------------------+ // 函數分隔
//| 匯出單一商品的真實 Tick 歷史                                         | // 函數說明
//+------------------------------------------------------------------+ // 分隔線
int ExportRealTicks(string sym) // 匯出真實 Tick 函數，回傳筆數
{ // 函數開始
   MqlTick ticks[]; // Tick 結構陣列
   ArraySetAsSeries(ticks, false); // 按時間正序
   int copied = CopyTicks(sym, ticks, COPY_TICKS_ALL, 0, InpMaxTicks); // 複製 Tick
   if(copied <= 0) return 0; // 失敗回傳0

   string safe_name = sym; // 安全檔名
   StringReplace(safe_name, "/", "_"); // 斜線換底線
   string filename = "EQ_TICK_" + safe_name + ".csv"; // 檔案名稱
   int handle = FileOpen(filename, FILE_WRITE | FILE_CSV | FILE_ANSI, ","); // 開啟檔案
   if(handle == INVALID_HANDLE) return 0; // 開啟失敗回傳0

   FileWrite(handle, "datetime_msc", "time_str", "bid", "ask", "spread_points", "spread_bps", "last", "volume"); // 寫入標頭

   double pt = SymbolInfoDouble(sym, SYMBOL_POINT); // 點值
   if(pt <= 0) pt = 0.01; // 預設 0.01

   for(int i = 0; i < copied; i++) // 遍歷所有 Tick
   { // 迴圈開始
      double bid = ticks[i].bid; // 買價
      double ask = ticks[i].ask; // 賣價
      if(bid <= 0 || ask <= 0) continue; // 過濾無效報價

      datetime t_sec = (datetime)(ticks[i].time_msc / 1000); // 秒級時間
      string t_str = TimeToString(t_sec, TIME_DATE | TIME_SECONDS) + "." + StringFormat("%03d", (int)(ticks[i].time_msc % 1000)); // 格式化時間
      double spread_pts = (ask - bid) / pt; // 點差(點數)
      double spread_bps = (bid > 0) ? ((ask - bid) / bid) * 10000.0 : 0.0; // 點差(bps)

      FileWrite(handle, IntegerToString(ticks[i].time_msc), t_str, DoubleToString(bid, 4), // 寫入單筆Tick（前半）
                DoubleToString(ask, 4), DoubleToString(spread_pts, 1), DoubleToString(spread_bps, 2), // 寫入單筆Tick（中段）
                DoubleToString(ticks[i].last, 4), IntegerToString(ticks[i].volume)); // 寫入單筆Tick（後段）
   } // 迴圈結束

   FileClose(handle); // 關閉檔案
   return copied; // 回傳筆數
} // 函數結束

//+------------------------------------------------------------------+ // 腳本主入口
//| Script program start function                                    | // 主函數說明
//+------------------------------------------------------------------+ // 分隔線
void OnStart() // 腳本執行入口
{ // 主函數開始
   Print("=================================================================="); // 分隔線
   Print("🚀 掃描帳戶內 [", InpCategory1, "] / [", InpCategory2, "] 分類商品 ..."); // 標題提示
   Print("=================================================================="); // 分隔線

   string matched[], matched_path[], matched_category[]; // 命中商品陣列
   int found = ScanCategorySymbols(matched, matched_path, matched_category); // 執行掃描

   if(found == 0) // 沒找到任何商品
   { // 錯誤處理
      Print("❌ 沒有找到任何符合分類的商品，請確認分類名稱是否正確（可先跑 ExportAccountSymbolList.mq5 查看實際 path 欄位）"); // 印出錯誤提示
      return; // 結束腳本
   } // 判斷結束

   Print("✅ 找到 ", found, " 檔符合分類的商品，開始逐一匯出 ..."); // 印出找到數量

   // 寫入摘要清單（方便回傳給分析端知道匯出了哪些商品、各週期根數多少）
   int meta_handle = FileOpen("EQ_universe_meta.csv", FILE_WRITE | FILE_CSV | FILE_ANSI, ","); // 開啟摘要檔
   if(meta_handle != INVALID_HANDLE) // 開檔成功
      FileWrite(meta_handle, "symbol", "path", "category", "bars_1m", "bars_5m", "bars_15m", "ticks"); // 寫入摘要表頭

   for(int i = 0; i < found; i++) // 遍歷所有命中商品
   { // 迴圈開始
      string sym = matched[i]; // 當前商品代碼
      SymbolSelect(sym, true); // 確保加入市場報價視窗（部分商品需要先選取才能取得歷史）

      Print("🔍 [", (i+1), "/", found, "] ", sym, " (", matched_category[i], ") ..."); // 印出進度

      int n1 = InpExport1M  ? ExportBarHistory(sym, PERIOD_M1, "1m")  : 0; // 匯出1m
      int n5 = InpExport5M  ? ExportBarHistory(sym, PERIOD_M5, "5m")  : 0; // 匯出5m
      int n15 = InpExport15M ? ExportBarHistory(sym, PERIOD_M15, "15m") : 0; // 匯出15m
      int nt = InpExportTicks ? ExportRealTicks(sym) : 0; // 匯出tick（預設關閉）

      Print("   📊 1m=", n1, " 5m=", n5, " 15m=", n15, (InpExportTicks ? (" ticks=" + IntegerToString(nt)) : "")); // 印出匯出結果

      if(meta_handle != INVALID_HANDLE) // 摘要檔可寫入
         FileWrite(meta_handle, sym, matched_path[i], matched_category[i], // 寫入摘要行（前半）
                   IntegerToString(n1), IntegerToString(n5), IntegerToString(n15), IntegerToString(nt)); // 寫入摘要行（後半）
   } // 迴圈結束

   if(meta_handle != INVALID_HANDLE) FileClose(meta_handle); // 關閉摘要檔

   Print("=================================================================="); // 分隔線
   Print("🎉 完成！共匯出 ", found, " 檔股票的K線至 MQL5/Files/，摘要見 EQ_universe_meta.csv"); // 完成提示
   Print("=================================================================="); // 分隔線
   Comment("✅ Equities CFD 匯出完成\n共 ", found, " 檔商品\n檔案前綴：EQ_*.csv"); // 圖表提示
} // OnStart 結束
