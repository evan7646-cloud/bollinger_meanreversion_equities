//+------------------------------------------------------------------+ // 檔案標頭註釋開始
//|                                         ExportForexRealCosts.mq5 | // 腳本檔案名稱
//|                                  Copyright 2026, Antigravity AI  | // 版權聲明
//|                                             https://www.mql5.com | // 參考網址
//+------------------------------------------------------------------+ // 檔案標頭註釋結束
#property copyright "Antigravity AI"                                   // 屬性：版權宣告
#property link      "https://www.mql5.com"                             // 屬性：官方連結
#property version   "3.00"                                             // 屬性：v3 改為真實歷史點差調查
#property script_show_inputs                                           // 執行前彈出參數視窗，方便調整天數/標的

// v2 只在腳本執行的那一瞬間抓一次 Bid/Ask，等於用「單一時刻的點差」代表整檔商品——
// 但外匯點差會隨交易時段（雪梨/東京/倫敦/紐約）劇烈變動，離峰時段可能是尖峰時段的 3~10 倍。
// v3 改用 CopyTicksRange() 向broker伺服器抓「過去N天、每天多個時間點」的真實歷史 tick 報價，
// 對每個時間點各取樣一次 Bid/Ask，最後統計出 mean/median/min/max，才是真正「考慮實際點差」的做法。

input string InpSymbols       = "EURUSD,USDJPY,GBPUSD,AUDUSD,USDCAD,USDCHF,NZDUSD,EURGBP,EURJPY,GBPJPY,AUDNZD,EURCHF,CADJPY,EURAUD,GBPAUD,AUDCAD,NZDCAD,CHFJPY,XAUUSD,AUDCHF,EURCAD"; // 目標貨幣對清單（逗號分隔）
input int    InpDays          = 7;      // 往回調查幾天（建議 >=7 天以涵蓋一整週各時段）
input int    InpSamplesPerDay = 48;     // 每天取樣次數（預設每 30 分鐘一次）
input int    InpWindowSeconds = 5;      // 每次取樣往後抓幾秒內的 tick（在該窗口內取最後一筆雙邊報價）
input int    InpWarmupWaitSec = 20;     // 每檔商品第一次呼叫若無資料，最長等待秒數（觸發終端機下載歷史）

//+------------------------------------------------------------------+ // 函數區隔線
//| 計算外匯每 1 標準手每日 Swap 實際金額 (以帳戶幣別 USD 計算)       | // 函數說明
//+------------------------------------------------------------------+ // 函數區隔線
double GetSwapUSD(string sym, double swap_val, double price)           // 計算利息金額函數
{                                                                      // 函數開始
   ENUM_SYMBOL_SWAP_MODE mode = (ENUM_SYMBOL_SWAP_MODE)SymbolInfoInteger(sym, SYMBOL_SWAP_MODE); // 取得利息計算模式
   double pt = SymbolInfoDouble(sym, SYMBOL_POINT);                    // 最小點值
   double ts = SymbolInfoDouble(sym, SYMBOL_TRADE_TICK_SIZE);          // 最小跳動價
   if(ts <= 0) ts = pt;                                                // 防除零保護
   double tv = SymbolInfoDouble(sym, SYMBOL_TRADE_TICK_VALUE);         // 單跳點值
   double cs = SymbolInfoDouble(sym, SYMBOL_TRADE_CONTRACT_SIZE);      // 合約大小

   if(mode == SYMBOL_SWAP_MODE_POINTS)                                 // 若為點數制
   {                                                                   // 點數模式
      return (swap_val * (pt / ts) * tv);                              // 點數換算金額
   }                                                                   // 結束點數模式
   else if(mode == SYMBOL_SWAP_MODE_CURRENCY_SYMBOL)                   // 若為基礎貨幣
   {                                                                   // 基礎貨幣模式
      return (swap_val * (tv / ts));                                   // 換算帳戶幣別
   }                                                                   // 結束基礎貨幣
   else if(mode == SYMBOL_SWAP_MODE_CURRENCY_MARGIN || mode == SYMBOL_SWAP_MODE_CURRENCY_DEPOSIT) // 若為保證金/存款幣別
   {                                                                   // 直接模式
      return swap_val;                                                 // 直接返回數值
   }                                                                   // 結束直接模式
   else if(mode == SYMBOL_SWAP_MODE_INTEREST_CURRENT)                  // 若為年化利率 %
   {                                                                   // 利率模式
      return ((swap_val / 100.0) * (price * cs) / 360.0);              // 換算每日金額
   }                                                                   // 結束利率模式
   return swap_val;                                                    // 預設返回原始值
}                                                                      // 結束函數

//+------------------------------------------------------------------+ // 函數區隔線
//| 智慧比對經紀商實際品種名稱（處理 .r / + / m 等後綴）              | // 函數說明
//+------------------------------------------------------------------+ // 函數區隔線
string ResolveSymbol(string base_sym)                                  // 解析實際品種名稱函數
{                                                                      // 函數開始
   string suffixes[] = {"", ".r", "+", "m", ".raw", ".pro", "_i", ".c", ".s", ".ecn", "_sb", ".a", ".b"}; // 經紀商常見後綴清單
   for(int s = 0; s < ArraySize(suffixes); s++)                        // 遍歷後綴清單
   {                                                                   // 後綴迴圈
      string candidate = base_sym + suffixes[s];                       // 拼接候選名稱
      if(SymbolInfoInteger(candidate, SYMBOL_SELECT) || SymbolSelect(candidate, true)) // 檢查品種是否可用
         return candidate;                                             // 找到就回傳
   }                                                                   // 結束後綴迴圈
   return "";                                                         // 找不到回傳空字串
}                                                                      // 結束函數

//+------------------------------------------------------------------+ // 函數區隔線
//| 在指定時間點附近取樣一次真實 Bid/Ask（取窗口內最後一筆雙邊報價） | // 函數說明
//+------------------------------------------------------------------+ // 函數區隔線
bool SampleTickAt(string sym, datetime t, int window_sec, double &bid_out, double &ask_out) // 取樣函數
{                                                                      // 函數開始
   MqlTick ticks[];                                                    // 宣告 tick 陣列
   ulong from_ms = (ulong)t * 1000;                                    // 起始毫秒時間戳
   ulong to_ms   = (ulong)(t + window_sec) * 1000;                     // 結束毫秒時間戳
   int n = CopyTicksRange(sym, ticks, COPY_TICKS_ALL, from_ms, to_ms); // 抓取該窗口內所有真實歷史 tick
   if(n <= 0) return false;                                            // 該窗口內無 tick（例如週末休市）
   for(int i = n - 1; i >= 0; i--)                                     // 由新到舊尋找
   {                                                                   // 迴圈開始
      if(ticks[i].bid > 0 && ticks[i].ask > 0)                         // 必須是雙邊報價
      {                                                                // 找到有效報價
         bid_out = ticks[i].bid;                                       // 賦值 Bid
         ask_out = ticks[i].ask;                                       // 賦值 Ask
         return true;                                                  // 取樣成功
      }                                                                // 結束檢查
   }                                                                   // 結束迴圈
   return false;                                                       // 窗口內全是單邊報價，視為取樣失敗
}                                                                      // 結束函數

//+------------------------------------------------------------------+ // 函數區隔線
//| 暖機：確保終端機已向伺服器要到該商品的歷史 tick（首次呼叫常為空）| // 函數說明
//+------------------------------------------------------------------+ // 函數區隔線
bool WarmupHistory(string sym, datetime probe_time, int window_sec, int max_wait_sec) // 暖機函數
{                                                                      // 函數開始
   double b, a;                                                        // 暫存變數
   ulong t0 = GetTickCount();                                          // 記錄起始毫秒
   while((GetTickCount() - t0) < (ulong)max_wait_sec * 1000)           // 在限時內反覆嘗試
   {                                                                   // 迴圈開始
      if(SampleTickAt(sym, probe_time, window_sec, b, a)) return true; // 一旦拿到資料即成功
      Sleep(500);                                                      // 稍等終端機下載
   }                                                                   // 結束迴圈
   return false;                                                       // 逾時仍無資料
}                                                                      // 結束函數

//+------------------------------------------------------------------+ // 函數區隔線
//| 中位數計算（會排序傳入陣列的複本）                                | // 函數說明
//+------------------------------------------------------------------+ // 函數區隔線
double Median(double &arr[], int n)                                    // 中位數函數
{                                                                      // 函數開始
   if(n <= 0) return 0.0;                                              // 空陣列保護
   double tmp[];                                                       // 建立複本陣列
   ArrayResize(tmp, n);                                                // 配置大小
   for(int i = 0; i < n; i++) tmp[i] = arr[i];                         // 複製內容
   ArraySort(tmp);                                                     // 由小到大排序
   if(n % 2 == 1) return tmp[n / 2];                                   // 奇數取中間值
   return (tmp[n / 2 - 1] + tmp[n / 2]) / 2.0;                         // 偶數取中間兩值平均
}                                                                      // 結束函數

//+------------------------------------------------------------------+ // 函數區隔線
//| 腳本執行入口函數                                                 | // 函數說明
//+------------------------------------------------------------------+ // 函數區隔線
void OnStart()                                                         // 主程式啟動函數
{                                                                      // 開始執行
   Print("================================================================================"); // 印出日誌分隔線
   Print(">>> 【開始執行】MT5 外匯全貨幣對「真實歷史點差」調查與匯出腳本 v3.0 <<<");             // 印出啟動日誌
   PrintFormat("    調查區間：過去 %d 天，每天 %d 個取樣點，每點取窗口內最後一筆真實報價", InpDays, InpSamplesPerDay); // 說明調查邏輯
   Print("================================================================================"); // 印出日誌分隔線

   string sym_parts[];                                                 // 品種清單解析陣列
   int total_targets = StringSplit(InpSymbols, ',', sym_parts);        // 依逗號拆分輸入字串
   if(total_targets <= 0)                                              // 若解析不到任何品種
   {                                                                   // 錯誤處理
      Print("❌ InpSymbols 沒有解析到任何標的");                        // 印出錯誤
      return;                                                          // 結束執行
   }                                                                   // 結束檢查

   string csv_name = "mt5_forex_real_costs.csv";                       // 輸出的 CSV 檔案名稱
   ResetLastError();                                                   // 重設系統錯誤代碼
   int fh = FileOpen(csv_name, FILE_WRITE|FILE_CSV|FILE_ANSI, ",");    // 開啟 CSV 寫入句柄
   if(fh == INVALID_HANDLE)                                            // 檢查檔案是否建立成功
   {                                                                   // 若開啟失敗
      Print("❌ 無法建立檔案: ", csv_name, " 錯誤碼: ", GetLastError()); // 印出錯誤代碼
      return;                                                          // 結束執行
   }                                                                   // 結束檢查

   // 寫入 CSV 欄位標題：點差改成「取樣統計」而非單一瞬間值
   FileWrite(fh, "貨幣對", "取樣成功次數", "點差中位數(Pips)", "點差平均(Pips)", "點差最小(Pips)", "點差最大(Pips)", // 點差統計欄位
                 "點差中位數(bps)", "單手點差成本_中位數($)",                                                       // 點差成本欄位
                 "做多Swap($/手/日)", "做空Swap($/手/日)", "3倍利息日",                                             // 隔夜利息欄位
                 "1Pip價值($)", "標準合約量", "調查起始時間", "調查結束時間");                                     // 取樣區間欄位

   datetime now = TimeCurrent();                                       // 伺服器目前時間
   datetime range_from = now - (datetime)InpDays * 86400;              // 調查起始時間
   int success_count = 0;                                              // 成功匯出計數

   for(int i = 0; i < total_targets; i++)                              // 遍歷所有目標品種
   {                                                                   // 迴圈開始
      string base_sym = sym_parts[i];                                  // 目標基準名稱
      StringTrimLeft(base_sym);                                        // 去除左側空白
      StringTrimRight(base_sym);                                       // 去除右側空白
      if(base_sym == "") continue;                                     // 跳過空字串

      Print("[", (i + 1), "/", total_targets, "] ", base_sym, " 調查中 ..."); // 進度提示
      Comment("🔬 真實點差調查中：", base_sym, " (", (i + 1), "/", total_targets, ")"); // 圖表上顯示進度

      string active_sym = ResolveSymbol(base_sym);                     // 解析經紀商實際品種名稱
      if(active_sym == "")                                             // 若找不到該品種
      {                                                                // 未找到
         Print("  ⚠️ [未找到] 經紀商未提供此品種: ", base_sym);         // 印出提示
         continue;                                                     // 換下一檔
      }                                                                // 結束檢查
      SymbolSelect(active_sym, true);                                  // 訂閱品種報價

      if(!WarmupHistory(active_sym, range_from, InpWindowSeconds, InpWarmupWaitSec)) // 暖機確認有歷史資料
      {                                                                // 暖機失敗
         MqlTick probe[];                                               // 探測陣列
         datetime first_tick = 0;                                       // 最早 tick 時間
         if(CopyTicks(active_sym, probe, COPY_TICKS_ALL, 0, 1) > 0)      // 嘗試取得任一筆最早歷史
            first_tick = (datetime)(probe[0].time_msc / 1000);          // 記錄最早時間
         Print("  ⚠️ ", active_sym, "：等 ", InpWarmupWaitSec, " 秒仍無歷史 tick",
               (first_tick > 0 ? ("（該商品最早的 tick 約在 " + TimeToString(first_tick, TIME_DATE) + "，可能比調查區間晚）")
                                : "（完全查不到 tick 歷史，請確認經紀商是否提供此商品的歷史報價）")); // 說明可能原因
         continue;                                                     // 換下一檔
      }                                                                // 結束暖機檢查

      // ---- 在過去 InpDays 天內，每天均勻取樣 InpSamplesPerDay 次，蒐集真實歷史點差 ----
      double spread_pips_samples[];                                    // 點差(Pips)取樣陣列
      int    sample_count = 0;                                         // 有效取樣計數
      int    step_sec = (int)(86400 / InpSamplesPerDay);               // 每次取樣間隔秒數

      for(int d = 0; d < InpDays; d++)                                 // 逐日迴圈
      {                                                                // 日迴圈開始
         datetime day_start = range_from + (datetime)d * 86400;        // 該日起始時間
         for(int k = 0; k < InpSamplesPerDay; k++)                     // 逐時段迴圈
         {                                                              // 時段迴圈開始
            datetime t = day_start + (datetime)k * step_sec;            // 該次取樣時間點
            if(t > now) break;                                          // 不超過目前時間
            double bid, ask;                                            // 暫存報價
            if(SampleTickAt(active_sym, t, InpWindowSeconds, bid, ask)) // 嘗試取樣（假日/離峰常抓不到，屬正常現象）
            {                                                           // 取樣成功
               double digits = (double)SymbolInfoInteger(active_sym, SYMBOL_DIGITS); // 小數位數
               double point  = SymbolInfoDouble(active_sym, SYMBOL_POINT);           // 點大小
               double pip_size = (digits == 3 || digits == 5) ? point * 10.0 : point; // 1 Pip 大小
               double sp_pips = (pip_size > 0) ? (ask - bid) / pip_size : 0.0;         // 換算成 Pips
               if(sp_pips >= 0)                                         // 排除異常負值（極端錯誤報價）
               {                                                        // 記錄取樣
                  ArrayResize(spread_pips_samples, sample_count + 1);    // 擴充陣列
                  spread_pips_samples[sample_count] = sp_pips;           // 寫入取樣值
                  sample_count++;                                       // 計數加一
               }                                                        // 結束記錄
            }                                                           // 結束取樣成功分支
         }                                                              // 結束時段迴圈
      }                                                                 // 結束日迴圈

      if(sample_count < 5)                                              // 有效取樣過少視為不可信
      {                                                                 // 樣本不足處理
         Print("  ⚠️ ", active_sym, "：僅取得 ", sample_count, " 筆有效取樣，資料不足以代表真實點差，已略過");
         continue;                                                      // 換下一檔
      }                                                                 // 結束樣本不足檢查

      // ---- 統計 ----
      double sp_median = Median(spread_pips_samples, sample_count);     // 中位數（抗極端尖峰點差）
      double sp_sum = 0.0, sp_min = spread_pips_samples[0], sp_max = spread_pips_samples[0]; // 初始化統計量
      for(int j = 0; j < sample_count; j++)                             // 遍歷所有取樣
      {                                                                 // 統計迴圈
         sp_sum += spread_pips_samples[j];                              // 累加總和
         if(spread_pips_samples[j] < sp_min) sp_min = spread_pips_samples[j]; // 更新最小值
         if(spread_pips_samples[j] > sp_max) sp_max = spread_pips_samples[j]; // 更新最大值
      }                                                                 // 結束統計迴圈
      double sp_mean = sp_sum / sample_count;                           // 平均值

      double digits = (double)SymbolInfoInteger(active_sym, SYMBOL_DIGITS); // 小數位數
      double point  = SymbolInfoDouble(active_sym, SYMBOL_POINT);           // 點大小
      double pip_size = (digits == 3 || digits == 5) ? point * 10.0 : point; // 1 Pip 大小
      double tick_size = SymbolInfoDouble(active_sym, SYMBOL_TRADE_TICK_SIZE); // 跳動大小
      if(tick_size <= 0) tick_size = point;                             // 防除零保護
      double tick_val = SymbolInfoDouble(active_sym, SYMBOL_TRADE_TICK_VALUE); // 跳動價值
      double contract_size = SymbolInfoDouble(active_sym, SYMBOL_TRADE_CONTRACT_SIZE); // 合約量
      double pip_val_usd = (pip_size / tick_size) * tick_val;           // 1 Pip 美元價值
      double spread_cost_median = sp_median * pip_val_usd;              // 用中位數點差換算單手成本

      double sp_median_bps = 0.0;                                       // bps 中位數初始化
      {                                                                 // 計算 bps 區塊
         double mid_bid, mid_ask;                                       // 用最近一次成功取樣估算中間價
         if(SampleTickAt(active_sym, now - 60, InpWindowSeconds * 6, mid_bid, mid_ask) && mid_bid > 0) // 取近期報價估算比例
            sp_median_bps = (sp_median * pip_size) / ((mid_bid + mid_ask) / 2.0) * 10000.0; // 換算 bps
      }                                                                 // 結束 bps 計算

      double swap_long = SymbolInfoDouble(active_sym, SYMBOL_SWAP_LONG); // 多單利息
      double swap_short = SymbolInfoDouble(active_sym, SYMBOL_SWAP_SHORT); // 空單利息
      MqlTick cur_tick = {};                                             // 目前報價（僅用於 swap 換算的參考價，歸零初始化避免未定義值）
      SymbolInfoTick(active_sym, cur_tick);                              // 取得目前 tick
      double ref_price = (cur_tick.bid > 0) ? (cur_tick.bid + cur_tick.ask) / 2.0 : sp_mean; // 參考價
      double swap_long_usd = GetSwapUSD(active_sym, swap_long, ref_price); // 換算做多每日美元
      double swap_short_usd = GetSwapUSD(active_sym, swap_short, ref_price); // 換算做空每日美元

      ENUM_DAY_OF_WEEK swap_3day_enum = (ENUM_DAY_OF_WEEK)SymbolInfoInteger(active_sym, SYMBOL_SWAP_ROLLOVER3DAYS); // 3 倍利息日
      string swap_3day_str = (swap_3day_enum == WEDNESDAY) ? "週三 (Wed)" : EnumToString(swap_3day_enum); // 格式化星期

      // 寫入 CSV 檔案（一檔商品一列，欄位為整個調查區間的統計結果）
      FileWrite(fh, active_sym,                                        // 品種代碼
                    IntegerToString(sample_count),                     // 取樣成功次數
                    DoubleToString(sp_median, 2),                      // 點差中位數(Pips)
                    DoubleToString(sp_mean, 2),                        // 點差平均(Pips)
                    DoubleToString(sp_min, 2),                         // 點差最小(Pips)
                    DoubleToString(sp_max, 2),                         // 點差最大(Pips)
                    DoubleToString(sp_median_bps, 2),                  // 點差中位數(bps)
                    DoubleToString(spread_cost_median, 2),             // 單手點差成本_中位數
                    DoubleToString(swap_long_usd, 2),                  // 做多利息
                    DoubleToString(swap_short_usd, 2),                 // 做空利息
                    swap_3day_str,                                     // 3倍利息日
                    DoubleToString(pip_val_usd, 2),                    // 點值
                    DoubleToString(contract_size, 0),                  // 合約大小
                    TimeToString(range_from, TIME_DATE|TIME_MINUTES),  // 調查起始時間
                    TimeToString(now, TIME_DATE|TIME_MINUTES));        // 調查結束時間

      success_count++;                                                 // 累加計數
      PrintFormat("  ✅ [%-8s] 取樣%4d次 | 點差中位數: %5.2f pips ($%5.2f/手) | 範圍 %.2f~%.2f pips | 做多Swap: $%6.2f/日 | 做空Swap: $%6.2f/日",
                  active_sym, sample_count, sp_median, spread_cost_median, sp_min, sp_max, swap_long_usd, swap_short_usd); // 格式化輸出
   }                                                                   // 結束迴圈

   FileFlush(fh);                                                      // 確保寫入硬碟
   FileClose(fh);                                                      // 關閉檔案
   Comment("");                                                        // 清除圖表上的進度提示

   string path = TerminalInfoString(TERMINAL_DATA_PATH) + "\\MQL5\\Files\\" + csv_name; // 完整路徑
   Print("================================================================================"); // 分隔線
   PrintFormat("🎉 【匯出成功】共匯出 %d / %d 檔外匯品種的真實歷史點差統計！檔案已儲存至: %s", success_count, total_targets, path); // 印出成功路徑
   Print("================================================================================"); // 分隔線

   Alert(StringFormat("外匯真實歷史點差調查完成！\n成功 %d / %d 檔\n每檔統計過去 %d 天、每天 %d 個時段的真實報價\n請至 MQL5/Files 開啟: %s",
                       success_count, total_targets, InpDays, InpSamplesPerDay, csv_name)); // 彈窗提示
}                                                                      // 腳本結束
