//+------------------------------------------------------------------+
//|                                    ExportTradeListMT5.mq5        |
//|                                  Copyright 2026, Quant Fund Team |
//|   📤 只匯出目前 EA 實盤交易的 10 檔標的之 15m K線（含真實spread）        |
//|      比 ExportEquitiesCategoryMT5.mq5 快很多，不用掃描全帳戶商品        |
//+------------------------------------------------------------------+
#property copyright "Copyright 2026, Quant Fund Team"
#property link      "https://github.com/evan7646-cloud"
#property version   "1.00"
#property script_show_inputs
#property description "只匯出 InpSymbols 清單（預設=EA實盤10檔）的 15m K線至 MQL5/Files/EQ_<SYM>_15m.csv，欄位格式與 ExportEquitiesCategoryMT5.mq5 相同，可直接餵給 Python 回測/儀表板匯出腳本使用"

//--- 參數設定（跟 BollingerMeanReversion.mq5 的 InpSymbols 保持一致）
input string InpSymbols     = "AVGO,JPM,QCOM,PLTR,AMD,INTC,SIEGn,ASML,LMT,SNOW"; // 要匯出的標的清單（逗號分隔）
input string InpSymbolSuffix = "";      // 自定義後綴（若經紀商為 AVGO.US 則填 .US，留空則自動探測）
input int    InpMaxBars     = 100000;   // 每個標的最多匯出的 K 棒數

//+------------------------------------------------------------------+
//| 智慧解析經紀商實際股票代碼（跟 EA 裡的邏輯一致）                          |
//+------------------------------------------------------------------+
string ResolveStockSymbol(string base_sym)
{
   string candidates[8];
   candidates[0] = base_sym + InpSymbolSuffix;
   candidates[1] = base_sym;
   candidates[2] = base_sym + ".US";
   candidates[3] = base_sym + ".a";
   candidates[4] = "#" + base_sym;
   candidates[5] = base_sym + "_US";
   candidates[6] = base_sym + "m";
   candidates[7] = base_sym + ".r";

   for(int i = 0; i < 8; i++)
   {
      if(candidates[i] == "") continue;
      if(SymbolSelect(candidates[i], true))
         return candidates[i];
   }

   for(int s = 0; s < SymbolsTotal(false); s++)
   {
      string sname = SymbolName(s, false);
      if(StringFind(sname, base_sym) >= 0)
      {
         SymbolSelect(sname, true);
         return sname;
      }
   }
   return "";
}

//+------------------------------------------------------------------+
//| 主動要求終端機跟伺服器同步最新K棒，不用手動開圖表                         |
//| 反覆呼叫 CopyRates 會觸發終端機向伺服器要更多歷史資料（背景下載），        |
//| 這裡等到「最新一根K棒時間夠接近現在」或逾時為止                          |
//+------------------------------------------------------------------+
bool EnsureFreshHistory(string sym, ENUM_TIMEFRAMES tf, int max_wait_sec)
{
   ulong start_tick = GetTickCount();
   datetime tf_seconds = (datetime)PeriodSeconds(tf);

   while((GetTickCount() - start_tick) < (ulong)(max_wait_sec * 1000))
   {
      MqlRates probe[];
      ArraySetAsSeries(probe, true); // index 0 = 最新
      int n = CopyRates(sym, tf, 0, 5, probe); // 每次呼叫都會促使終端機跟伺服器要資料

      if(n > 0)
      {
         datetime latest = probe[0].time;
         datetime now = TimeCurrent(); // 伺服器時間（比本機時間準確）
         if(now - latest <= tf_seconds * 3) // 最新K棒落在「現在往回3根」以內，視為已同步到位
            return true;
      }
      Sleep(400); // 短暫等待後端下載完成再檢查一次
   }
   return false; // 逾時仍未同步到最新，呼叫端仍會匯出目前拿得到的資料，並印出警告
}

//+------------------------------------------------------------------+
//| 匯出單一商品 15m K 線（含真實 Spread 點差欄位），格式跟舊腳本一致           |
//+------------------------------------------------------------------+
int ExportBarHistory15m(string sym, string base_sym, bool &was_stale)
{
   was_stale = !EnsureFreshHistory(sym, PERIOD_M15, 15); // 最多主動等15秒讓終端機同步到最新

   MqlRates rates[];
   ArraySetAsSeries(rates, false);
   int copied = CopyRates(sym, PERIOD_M15, 0, InpMaxBars, rates);
   if(copied <= 0) return 0;

   string filename = "EQ_" + base_sym + "_15m.csv"; // 檔名用原始代碼(base_sym)，跟現有CSV命名一致
   int handle = FileOpen(filename, FILE_WRITE | FILE_CSV | FILE_ANSI, ",");
   if(handle == INVALID_HANDLE) return 0;

   FileWrite(handle, "datetime", "open", "high", "low", "close", "spread_points", "spread_bps", "tick_volume", "real_volume");

   double pt = SymbolInfoDouble(sym, SYMBOL_POINT);
   if(pt <= 0) pt = 0.01;

   for(int i = 0; i < copied; i++)
   {
      string t_str = TimeToString(rates[i].time, TIME_DATE | TIME_MINUTES);
      double spread_pts = (double)rates[i].spread;
      double spread_bps = (rates[i].close > 0) ? (rates[i].spread * pt / rates[i].close) * 10000.0 : 0.0;

      FileWrite(handle, t_str, DoubleToString(rates[i].open, 4), DoubleToString(rates[i].high, 4),
                DoubleToString(rates[i].low, 4), DoubleToString(rates[i].close, 4),
                DoubleToString(spread_pts, 1), DoubleToString(spread_bps, 2),
                IntegerToString(rates[i].tick_volume), IntegerToString(rates[i].real_volume));
   }

   FileClose(handle);
   return copied;
}

//+------------------------------------------------------------------+
//| 腳本主入口                                                        |
//+------------------------------------------------------------------+
void OnStart()
{
   string parts[];
   int n = StringSplit(InpSymbols, ',', parts);
   if(n <= 0)
   {
      Print("❌ InpSymbols 沒有解析到任何標的");
      return;
   }

   Print("==================================================================");
   Print("🚀 開始匯出 ", n, " 檔實盤交易標的的 15m K線 ...");
   Print("==================================================================");

   int meta_handle = FileOpen("EQ_universe_meta.csv", FILE_WRITE | FILE_CSV | FILE_ANSI, ",");
   if(meta_handle != INVALID_HANDLE)
      FileWrite(meta_handle, "symbol", "path", "category", "bars_1m", "bars_5m", "bars_15m", "ticks");

   int ok_count = 0;
   for(int i = 0; i < n; i++)
   {
      string base = parts[i];
      StringTrimLeft(base);
      StringTrimRight(base);
      if(base == "") continue;

      string resolved = ResolveStockSymbol(base);
      if(resolved == "")
      {
         Print("⚠️ [", (i+1), "/", n, "] ", base, "：找不到對應商品，跳過");
         continue;
      }

      bool was_stale = false;
      int bars = ExportBarHistory15m(resolved, base, was_stale);
      if(bars > 0)
      {
         ok_count++;
         string stale_note = was_stale ? "（⏳ 等待15秒後仍未同步到最新，資料可能不是最新的，建議稍後重跑一次）" : "";
         Print("✅ [", (i+1), "/", n, "] ", base, " (", resolved, ")：匯出 ", bars, " 根 15m K棒", stale_note);
         if(meta_handle != INVALID_HANDLE)
         {
            string path = SymbolInfoString(resolved, SYMBOL_PATH);
            FileWrite(meta_handle, base, path, "trade_list", "0", "0", IntegerToString(bars), "0");
         }
      }
      else
      {
         Print("⚠️ [", (i+1), "/", n, "] ", base, " (", resolved, ")：匯出失敗（完全沒有歷史資料，請確認該商品在市場報價視窗可見、且終端機已連線）");
      }
   }

   if(meta_handle != INVALID_HANDLE) FileClose(meta_handle);

   Print("==================================================================");
   Print("🎉 完成！成功匯出 ", ok_count, " / ", n, " 檔標的至 MQL5/Files/");
   Print("==================================================================");
   Comment("✅ 交易清單匯出完成\n成功 ", ok_count, " / ", n, " 檔\n檔案前綴：EQ_*_15m.csv");
}
