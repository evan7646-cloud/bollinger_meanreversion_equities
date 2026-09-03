//+------------------------------------------------------------------+
//|                                        ExportTickWindow.mq5      |
//|                                  Copyright 2026, Quant Fund Team |
//|   🔬 匯出指定標的、指定時間區間的逐筆報價(bid/ask)與1分K            |
//|      用來查證某一筆成交當下的真實買賣價，釐清滑價來源                 |
//+------------------------------------------------------------------+
#property copyright "Copyright 2026, Quant Fund Team"
#property link      "https://github.com/evan7646-cloud"
#property version   "1.00"
#property script_show_inputs
#property description "匯出指定時間區間的 tick(bid/ask/spread) 與 1分K 至 MQL5/Files/，用來查證某筆成交的滑價到底是點差、跳空、還是真實波動造成的"

input string InpSymbol   = "";                     // 標的（留空=自動用目前掛圖表的商品）
input string InpFromTime = "";                     // 起始時間（伺服器時間，格式YYYY.MM.DD HH:MM:SS，留空=目前伺服器時間往前10分鐘）
input string InpToTime   = "";                     // 結束時間（留空=目前伺服器時間）

void OnStart()
{
   string sym = (InpSymbol == "") ? _Symbol : InpSymbol; // 留空就用腳本掛的那張圖表的商品
   if(!SymbolSelect(sym, true))
   {
      Print("❌ 找不到商品 ", sym);
      return;
   }

   datetime default_to   = TimeCurrent();
   datetime default_from = default_to - 10 * 60; // 預設抓最近10分鐘

   datetime from = (InpFromTime == "") ? default_from : StringToTime(InpFromTime);
   datetime to   = (InpToTime   == "") ? default_to   : StringToTime(InpToTime);
   if(from <= 0 || to <= from)
   {
      Print("❌ 時間格式錯誤，請用 YYYY.MM.DD HH:MM:SS");
      return;
   }
   Print("📌 標的=", sym, "  區間=", TimeToString(from, TIME_DATE|TIME_SECONDS),
         " ~ ", TimeToString(to, TIME_DATE|TIME_SECONDS));

   //--- 1) 匯出逐筆 tick
   MqlTick ticks[];
   int n = CopyTicksRange(sym, ticks, COPY_TICKS_ALL, (ulong)from * 1000, (ulong)to * 1000);
   Print("CopyTicksRange 取得 ", n, " 筆 tick");

   if(n > 0)
   {
      string fn = "TickWindow_" + sym + ".csv";
      int fh = FileOpen(fn, FILE_WRITE | FILE_CSV | FILE_ANSI, ",");
      if(fh != INVALID_HANDLE)
      {
         FileWrite(fh, "time_msc", "time_str", "bid", "ask", "spread_abs", "spread_bps", "last", "volume", "flags");
         for(int i = 0; i < n; i++)
         {
            double bid = ticks[i].bid, ask = ticks[i].ask;
            datetime ts = (datetime)(ticks[i].time_msc / 1000);
            string tstr = TimeToString(ts, TIME_DATE | TIME_SECONDS) + "." +
                          StringFormat("%03d", (int)(ticks[i].time_msc % 1000));
            double sp_abs = (bid > 0 && ask > 0) ? (ask - bid) : 0;
            double sp_bps = (bid > 0 && ask > 0) ? (ask - bid) / bid * 10000.0 : 0;
            FileWrite(fh, IntegerToString(ticks[i].time_msc), tstr,
                      DoubleToString(bid, 4), DoubleToString(ask, 4),
                      DoubleToString(sp_abs, 4), DoubleToString(sp_bps, 2),
                      DoubleToString(ticks[i].last, 4), IntegerToString(ticks[i].volume),
                      IntegerToString(ticks[i].flags));
         }
         FileClose(fh);
         Print("✅ tick 已存至 MQL5/Files/", fn);
      }
   }
   else
   {
      Print("⚠️ 該區間沒有 tick 歷史（券商可能未提供該商品的tick資料）");
   }

   //--- 2) 匯出同區間的 1 分 K（tick 拿不到時的備援）
   MqlRates m1[];
   ArraySetAsSeries(m1, false);
   int nb = CopyRates(sym, PERIOD_M1, from - 300, to + 300, m1);
   Print("CopyRates(M1) 取得 ", nb, " 根");
   if(nb > 0)
   {
      string fn2 = "M1Window_" + sym + ".csv";
      int fh2 = FileOpen(fn2, FILE_WRITE | FILE_CSV | FILE_ANSI, ",");
      if(fh2 != INVALID_HANDLE)
      {
         FileWrite(fh2, "datetime", "open", "high", "low", "close", "spread_points", "tick_volume");
         for(int i = 0; i < nb; i++)
            FileWrite(fh2, TimeToString(m1[i].time, TIME_DATE | TIME_SECONDS),
                      DoubleToString(m1[i].open, 4), DoubleToString(m1[i].high, 4),
                      DoubleToString(m1[i].low, 4), DoubleToString(m1[i].close, 4),
                      IntegerToString(m1[i].spread), IntegerToString(m1[i].tick_volume));
         FileClose(fh2);
         Print("✅ 1分K 已存至 MQL5/Files/", fn2);
      }
   }

   Comment("✅ 匯出完成\n", sym, "\ntick=", n, " M1=", nb);
}
