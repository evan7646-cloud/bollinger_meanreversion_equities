//+------------------------------------------------------------------+
//|                                     ExportSpreadSurvey.mq5       |
//|                                  Copyright 2026, Quant Fund Team |
//|   🔬 批次匯出多檔商品、整個交易時段的逐筆報價                          |
//|      用來比較各標的的「真實有效點差」與時段分布                        |
//+------------------------------------------------------------------+
#property copyright "Copyright 2026, Quant Fund Team"
#property link      "https://github.com/evan7646-cloud"
#property version   "1.00"
#property script_show_inputs
#property description "批次匯出多檔標的在指定日期整個時段的 tick(bid/ask)，每檔一個 CSV。用來查證 K棒記錄的 spread 低估多少、以及各標的的真實點差差異"

input string InpSymbols  = "AVGO,JPM,QCOM,PLTR,AMD,INTC,SIEGn,ASML,LMT,SNOW"; // 標的清單（逗號分隔）
input string InpDate     = "2026.09.02";   // 要調查的日期（伺服器時間）
input string InpFromHHMM = "16:30";        // 時段起（伺服器時間；美股通常16:30開盤）
input string InpToHHMM   = "23:00";        // 時段迄
input int    InpMaxTicks = 200000;         // 每檔最多匯出筆數（安全上限）

//+------------------------------------------------------------------+
//| 匯出單一標的的 tick，回傳筆數                                        |
//+------------------------------------------------------------------+
int ExportOne(string sym, datetime from, datetime to)
{
   if(!SymbolSelect(sym, true))
   {
      Print("  ⚠️ ", sym, "：找不到商品，跳過");
      return 0;
   }

   MqlTick ticks[];
   int n = 0;
   ulong t0 = GetTickCount();
   // 過去區間第一次呼叫常拿到0（終端機還沒跟伺服器要資料），反覆呼叫觸發下載
   while((GetTickCount() - t0) < 20000)
   {
      n = CopyTicksRange(sym, ticks, COPY_TICKS_ALL, (ulong)from * 1000, (ulong)to * 1000);
      if(n > 0) break;
      Sleep(500);
   }
   if(n <= 0)
   {
      // 查出這檔商品的 tick 歷史最早到哪一天，方便判斷是「這個日期太舊」還是「這檔沒資料」
      datetime first_tick = 0;
      MqlTick probe[];
      if(CopyTicks(sym, probe, COPY_TICKS_ALL, 0, 1) > 0)
         first_tick = (datetime)(probe[0].time_msc / 1000);
      Print("  ⚠️ ", sym, "：等20秒仍無 tick 歷史",
            (first_tick > 0 ? ("（該商品最早的 tick 約在 " + TimeToString(first_tick, TIME_DATE) + "）") : "（完全查不到tick）"));
      return 0;
   }
   if(n > InpMaxTicks) n = InpMaxTicks;

   // 檔名帶上日期，才能同時保留多個日期的調查結果做比較（例如比較 2024 vs 2026 的點差）
   string date_tag = InpDate;
   StringReplace(date_tag, ".", "");
   string fn = "Spread_" + sym + "_" + date_tag + ".csv";
   int fh = FileOpen(fn, FILE_WRITE | FILE_CSV | FILE_ANSI, ",");
   if(fh == INVALID_HANDLE)
   {
      Print("  ❌ ", sym, "：無法開檔 ", fn);
      return 0;
   }

   FileWrite(fh, "time_str", "bid", "ask", "spread_abs", "spread_bps");
   int written = 0;
   for(int i = 0; i < n; i++)
   {
      double bid = ticks[i].bid, ask = ticks[i].ask;
      if(bid <= 0 || ask <= 0) continue; // 只有單邊報價的tick跳過，算不出點差
      datetime ts = (datetime)(ticks[i].time_msc / 1000);
      string tstr = TimeToString(ts, TIME_DATE | TIME_SECONDS) + "." +
                    StringFormat("%03d", (int)(ticks[i].time_msc % 1000));
      FileWrite(fh, tstr, DoubleToString(bid, 4), DoubleToString(ask, 4),
                DoubleToString(ask - bid, 4), DoubleToString((ask - bid) / bid * 10000.0, 2));
      written++;
   }
   FileClose(fh);
   Print("  ✅ ", sym, "：", written, " 筆 -> MQL5/Files/", fn);
   return written;
}

void OnStart()
{
   datetime from = StringToTime(InpDate + " " + InpFromHHMM + ":00");
   datetime to   = StringToTime(InpDate + " " + InpToHHMM + ":00");
   if(from <= 0 || to <= from)
   {
      Print("❌ 日期/時間格式錯誤。日期用 YYYY.MM.DD，時間用 HH:MM");
      return;
   }

   string parts[];
   int n = StringSplit(InpSymbols, ',', parts);
   if(n <= 0)
   {
      Print("❌ InpSymbols 沒有解析到任何標的");
      return;
   }

   Print("==================================================================");
   Print("🔬 點差調查：", n, " 檔標的，區間 ",
         TimeToString(from, TIME_DATE | TIME_MINUTES), " ~ ", TimeToString(to, TIME_DATE | TIME_MINUTES));
   Print("==================================================================");

   int ok = 0, total_ticks = 0;
   for(int i = 0; i < n; i++)
   {
      string s = parts[i];
      StringTrimLeft(s);
      StringTrimRight(s);
      if(s == "") continue;
      Print("[", (i + 1), "/", n, "] ", s, " ...");
      int c = ExportOne(s, from, to);
      if(c > 0) { ok++; total_ticks += c; }
   }

   Print("==================================================================");
   Print("🎉 完成！成功 ", ok, " / ", n, " 檔，共 ", total_ticks, " 筆 tick");
   Print("==================================================================");
   Comment("✅ 點差調查完成\n成功 ", ok, " / ", n, " 檔\n共 ", total_ticks, " 筆\n檔名前綴：Spread_*.csv");
}
