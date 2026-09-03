//+------------------------------------------------------------------+
//|                                       ExportBandsCheck.mq5       |
//|                                  Copyright 2026, Quant Fund Team |
//|   🔬 匯出 MT5 iBands 指標的真實數值，用來驗證它的標準差公式             |
//|      （母體std ÷N vs 樣本std ÷N-1），確保 Python 回測跟實盤一致        |
//+------------------------------------------------------------------+
#property copyright "Copyright 2026, Quant Fund Team"
#property link      "https://github.com/evan7646-cloud"
#property version   "1.00"
#property script_show_inputs
#property description "匯出指定標的的 iBands(20,2.0) 上中下軌真實數值與對應收盤價至 MQL5/Files/BandsCheck_<SYM>.csv，供 Python 端比對驗證 MT5 用的是母體標準差還是樣本標準差"

input string InpSymbol    = "AMD";          // 要檢查的標的
input ENUM_TIMEFRAMES InpTimeframe = PERIOD_M15; // 週期（跟策略一致）
input int    InpBandPeriod = 20;            // Bollinger 均線週期
input double InpBandDev    = 2.0;           // 標準差倍數
input int    InpBars       = 500;           // 匯出最近幾根K棒

void OnStart()
{
   string sym = InpSymbol;
   if(!SymbolSelect(sym, true))
   {
      Print("❌ 找不到商品 ", sym);
      return;
   }

   int handle = iBands(sym, InpTimeframe, InpBandPeriod, 0, InpBandDev, PRICE_CLOSE);
   if(handle == INVALID_HANDLE)
   {
      Print("❌ iBands handle 建立失敗");
      return;
   }

   // 等待指標計算完成（剛建立handle時緩衝區可能還沒算好）
   int wait = 0;
   while(BarsCalculated(handle) < InpBars && wait < 50) { Sleep(200); wait++; }

   double upper[], mid[], lower[];
   ArraySetAsSeries(upper, false);
   ArraySetAsSeries(mid, false);
   ArraySetAsSeries(lower, false);

   int n_up  = CopyBuffer(handle, 1, 0, InpBars, upper); // buffer 1 = 上軌
   int n_mid = CopyBuffer(handle, 0, 0, InpBars, mid);   // buffer 0 = 中軌
   int n_low = CopyBuffer(handle, 2, 0, InpBars, lower); // buffer 2 = 下軌
   if(n_up <= 0 || n_mid <= 0 || n_low <= 0)
   {
      Print("❌ CopyBuffer 失敗 up=", n_up, " mid=", n_mid, " low=", n_low);
      IndicatorRelease(handle);
      return;
   }

   MqlRates rates[];
   ArraySetAsSeries(rates, false);
   int n_rates = CopyRates(sym, InpTimeframe, 0, InpBars, rates);
   if(n_rates <= 0)
   {
      Print("❌ CopyRates 失敗");
      IndicatorRelease(handle);
      return;
   }

   int n = MathMin(MathMin(n_up, n_mid), MathMin(n_low, n_rates));

   string filename = "BandsCheck_" + sym + ".csv";
   int fh = FileOpen(filename, FILE_WRITE | FILE_CSV | FILE_ANSI, ",");
   if(fh == INVALID_HANDLE)
   {
      Print("❌ 無法開啟輸出檔 ", filename);
      IndicatorRelease(handle);
      return;
   }

   FileWrite(fh, "datetime", "close", "mt5_mid", "mt5_upper", "mt5_lower");
   for(int i = 0; i < n; i++)
   {
      FileWrite(fh, TimeToString(rates[i].time, TIME_DATE | TIME_MINUTES),
                DoubleToString(rates[i].close, 5),
                DoubleToString(mid[i], 6), DoubleToString(upper[i], 6), DoubleToString(lower[i], 6));
   }
   FileClose(fh);
   IndicatorRelease(handle);

   Print("🎉 完成！", sym, " 共匯出 ", n, " 根K棒的 iBands 真實數值 -> MQL5/Files/", filename);
   Comment("✅ iBands 數值匯出完成\n", sym, " ", n, " 根\n檔案：", filename);
}
