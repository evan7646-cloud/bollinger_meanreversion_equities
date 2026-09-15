//+------------------------------------------------------------------+
//|                                              DiagnoseEAState.mq5 |
//|  比對兩個帳戶的 EA 狀態，找出「同一支 EA 卻交易不一樣」的原因      |
//+------------------------------------------------------------------+
#property copyright "Grid Strategy Project"
#property version   "1.00"
#property description "在每一個要比對的 MT5 終端機各跑一次，輸出 ea_state_<帳號>.csv，"
#property description "然後把兩份檔案給 Claude 對比。"
#property description "回答的是：兩個帳戶是不是同一家 broker、EA 有沒有在跑、"
#property description "上次動作是什麼時候、層數記錄是否同步、指標讀數是否相同。"
#property script_show_inputs

// 為什麼需要這支：實盤 A 抱著舊部位、又沒跟上新進場，可能的原因有好幾個
// （EA 停掉、版本不同、broker 不同導致 H4 邊界不同、品種後綴不同、點差過濾擋掉），
// 光看持倉截圖分不出來。這支把每個可能性各自對應的證據一次全部倒出來。
//
// 最關鍵的一項是 GlobalVariableTime()：EA 每次加碼都會寫 CGDCA_*_layer，
// 那個時間戳就是「EA 最後一次真的動手」的時刻。若 A 的時間戳停在好幾天前，
// 就直接證明 A 的 EA 沒在跑，不必再猜。

input string InpSymbols = "AUDCAD,AUDCHF,GBPNZD,GBPCHF,EURCHF,NZDUSD,EURAUD,AUDUSD,NZDCHF,CADCHF,GBPCAD,NZDCAD,USDNOK"; // 要檢查的商品（須與 EA 的 InpSymbols 一致）
input long   InpMagic   = 20260904;   // magic number（須與 EA 一致）
input int    InpAtrPeriod  = 20;      // ATR 週期（須與 EA 一致）
input int    InpEmaPeriod  = 50;      // EMA 週期（須與 EA 一致）
input int    InpAdxPeriod  = 14;      // ADX 週期（須與 EA 一致）
input double InpMaxTotalRiskPct = 60.0;  // 名目總額上限%（須與 EA 一致）
input double InpBaseOrderPct    = 6.0;   // 首單名目占淨值%（須與 EA 一致）
input double InpLotMultiplier   = 1.0;   // 下單倍數（須與 EA 一致）

//+------------------------------------------------------------------+
//| 與 EA 相同的品種解析邏輯（處理 .r / + / m 等 broker 後綴）        |
//+------------------------------------------------------------------+
string ResolveSymbol(string base_sym)
{
   string suffixes[] = {"", ".r", "+", "m", ".raw", ".pro", "_i", ".c", ".s", ".ecn", "_sb", ".a", ".b"};
   for(int s = 0; s < ArraySize(suffixes); s++)
   {
      string c = base_sym + suffixes[s];
      if(SymbolInfoInteger(c, SYMBOL_SELECT) || SymbolSelect(c, true)) return c;
   }
   return "";
}

//+------------------------------------------------------------------+
//| 讀單一指標緩衝區的「前一根收盤」值（index=1，與 EA 取法一致）      |
//+------------------------------------------------------------------+
double ReadPrevBar(int handle)
{
   if(handle == INVALID_HANDLE) return 0.0;
   double buf[];
   if(CopyBuffer(handle, 0, 1, 1, buf) != 1) return 0.0;
   return buf[0];
}

//+------------------------------------------------------------------+
void OnStart()
{
   long   acct   = AccountInfoInteger(ACCOUNT_LOGIN);
   string fname  = StringFormat("ea_state_%I64d.csv", acct);
   int fh = FileOpen(fname, FILE_WRITE|FILE_CSV|FILE_ANSI, ",");
   if(fh == INVALID_HANDLE)
   { PrintFormat("❌ 無法建立 %s（錯誤 %d）", fname, GetLastError()); return; }

   FileWrite(fh, "section", "key", "value", "extra1", "extra2", "extra3", "extra4");

   //--- 1) 帳戶與終端機：先確認兩個帳戶是不是同一家 broker ---------
   // 若 server / company 不同，兩邊的伺服器時間可能差幾小時，H4 K棒切點就不同，
   // 指標讀數天生不一樣——那樣「交易不同」是必然的，不是故障。
   datetime srv = TimeCurrent();
   datetime gmt = TimeGMT();
   double   off = (double)(srv - gmt) / 3600.0;

   FileWrite(fh, "ACCOUNT", "login",        IntegerToString(acct), "", "", "", "");
   FileWrite(fh, "ACCOUNT", "server",       AccountInfoString(ACCOUNT_SERVER), "", "", "", "");
   FileWrite(fh, "ACCOUNT", "company",      AccountInfoString(ACCOUNT_COMPANY), "", "", "", "");
   FileWrite(fh, "ACCOUNT", "currency",     AccountInfoString(ACCOUNT_CURRENCY), "", "", "", "");
   FileWrite(fh, "ACCOUNT", "balance",      DoubleToString(AccountInfoDouble(ACCOUNT_BALANCE), 2), "", "", "", "");
   FileWrite(fh, "ACCOUNT", "equity",       DoubleToString(AccountInfoDouble(ACCOUNT_EQUITY), 2), "", "", "", "");
   FileWrite(fh, "ACCOUNT", "leverage",     IntegerToString(AccountInfoInteger(ACCOUNT_LEVERAGE)), "", "", "", "");
   FileWrite(fh, "ACCOUNT", "server_time",  TimeToString(srv, TIME_DATE|TIME_SECONDS), "", "", "", "");
   FileWrite(fh, "ACCOUNT", "gmt_offset_h", DoubleToString(off, 1), "", "", "", "");
   FileWrite(fh, "TERMINAL", "build",       IntegerToString(TerminalInfoInteger(TERMINAL_BUILD)), "", "", "", "");
   FileWrite(fh, "TERMINAL", "trade_allowed",
             TerminalInfoInteger(TERMINAL_TRADE_ALLOWED) ? "YES" : "NO（自動交易被關掉！）", "", "", "", "");
   FileWrite(fh, "TERMINAL", "connected",
             TerminalInfoInteger(TERMINAL_CONNECTED) ? "YES" : "NO", "", "", "", "");
   FileWrite(fh, "TERMINAL", "data_path",   TerminalInfoString(TERMINAL_DATA_PATH), "", "", "", "");

   //--- 2) EA 的層數記錄：時間戳＝EA 最後一次動手的時刻 -------------
   // GlobalVariable 存在終端機的資料夾裡，不隨帳戶走。
   // 若兩個帳戶共用同一個終端機，這裡會互相污染；若時間戳很舊，代表 EA 早就沒在動。
   int n_gv = 0;
   datetime newest = 0;
   for(int i = GlobalVariablesTotal() - 1; i >= 0; i--)
   {
      string k = GlobalVariableName(i);
      if(StringFind(k, "CGDCA_") != 0) continue;
      datetime t = GlobalVariableTime(k);
      if(t > newest) newest = t;
      FileWrite(fh, "LAYER_GV", k,
                DoubleToString(GlobalVariableGet(k), 0),
                TimeToString(t, TIME_DATE|TIME_SECONDS), "", "", "");
      n_gv++;
   }
   FileWrite(fh, "LAYER_GV", "_count", IntegerToString(n_gv),
             newest > 0 ? TimeToString(newest, TIME_DATE|TIME_SECONDS) : "（完全沒有記錄）",
             "最新時間戳＝EA最後一次加碼", "", "");

   //--- 3) 目前持倉：含非本 EA 的，才看得到孤兒倉 -------------------
   int n_mine = 0, n_other = 0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong tk = PositionGetTicket(i);
      if(tk == 0) continue;
      long mg = PositionGetInteger(POSITION_MAGIC);
      bool mine = (mg == InpMagic);
      if(mine) n_mine++; else n_other++;
      FileWrite(fh, mine ? "POS_EA" : "POS_OTHER",
                PositionGetString(POSITION_SYMBOL),
                (PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY) ? "BUY" : "SELL",
                DoubleToString(PositionGetDouble(POSITION_VOLUME), 2),
                TimeToString((datetime)PositionGetInteger(POSITION_TIME), TIME_DATE|TIME_MINUTES),
                StringFormat("open=%s sl=%s tp=%s",
                             DoubleToString(PositionGetDouble(POSITION_PRICE_OPEN), 5),
                             DoubleToString(PositionGetDouble(POSITION_SL), 5),
                             DoubleToString(PositionGetDouble(POSITION_TP), 5)),
                StringFormat("magic=%I64d pnl=%.2f comment=%s",
                             mg, PositionGetDouble(POSITION_PROFIT),
                             PositionGetString(POSITION_COMMENT)));
   }
   FileWrite(fh, "POS", "_count_ea", IntegerToString(n_mine),
             "_count_other", IntegerToString(n_other), "", "");

   //--- 3b) 名目總額 vs 上限：EA 最容易「安靜地」拒絕開倉的地方 -----
   // EA 在 TotalNotional() + base_n > max_notional 時直接跳過開倉，而且不印任何訊息，
   // 所以 log 看起來一切正常卻沒進場。這裡把當下的佔用率算出來。
   double tot_notional = 0.0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong tk = PositionGetTicket(i);
      if(tk == 0) continue;
      if(PositionGetInteger(POSITION_MAGIC) != InpMagic) continue;
      string sy = PositionGetString(POSITION_SYMBOL);
      double tv = SymbolInfoDouble(sy, SYMBOL_TRADE_TICK_VALUE);
      double ts = SymbolInfoDouble(sy, SYMBOL_TRADE_TICK_SIZE);
      double px = SymbolInfoDouble(sy, SYMBOL_BID);
      if(tv > 0 && ts > 0 && px > 0)
         tot_notional += PositionGetDouble(POSITION_VOLUME) * (tv / ts) * px;
   }
   double eq_now  = AccountInfoDouble(ACCOUNT_EQUITY);
   double cap_amt = eq_now * InpMaxTotalRiskPct / 100.0;
   double next_n  = eq_now * InpBaseOrderPct / 100.0 * InpLotMultiplier;
   bool   blocked = (InpMaxTotalRiskPct > 0.0) && (tot_notional + next_n > cap_amt);

   FileWrite(fh, "NOTIONAL", "total_now", DoubleToString(tot_notional, 0),
             StringFormat("%.1f%% 淨值", eq_now > 0 ? tot_notional / eq_now * 100.0 : 0.0),
             StringFormat("上限 %.0f%% = %.0f", InpMaxTotalRiskPct, cap_amt),
             StringFormat("下一筆首單需要 %.0f", next_n),
             blocked ? "🔴 已滿，新開倉會被安靜擋掉" : "✅ 還有額度");

   //--- 4) 每檔商品：解析結果、點差、以及 EA 當下會讀到的指標 -------
   // 兩邊若同一 broker、同一時間跑，這些數字應該幾乎一模一樣。
   // 差很多 → broker 不同或歷史資料不同步（工具→歷史資料中心 重新下載）。
   string parts[];
   int n = StringSplit(InpSymbols, ',', parts);
   for(int i = 0; i < n; i++)
   {
      string b = parts[i];
      StringTrimLeft(b); StringTrimRight(b);
      if(b == "") continue;

      string sym = ResolveSymbol(b);
      if(sym == "")
      { FileWrite(fh, "SYMBOL", b, "❌ 此 broker 沒有這個品種", "", "", "", ""); continue; }

      int digits = (int)SymbolInfoInteger(sym, SYMBOL_DIGITS);
      double point = SymbolInfoDouble(sym, SYMBOL_POINT);
      double pip = (digits == 3 || digits == 5) ? point * 10.0 : point;
      double sp_pip = (pip > 0) ? (SymbolInfoInteger(sym, SYMBOL_SPREAD) * point) / pip : 0.0;

      int h_atr = iATR(sym, PERIOD_H4, InpAtrPeriod);
      int h_ema = iMA (sym, PERIOD_H4, InpEmaPeriod, 0, MODE_EMA, PRICE_CLOSE);
      int h_adx = iADX(sym, PERIOD_H4, InpAdxPeriod);
      // 指標剛建立時緩衝區還沒算完，等一下再讀，否則全部拿到 0
      Sleep(200);
      double atr = ReadPrevBar(h_atr);
      double ema = ReadPrevBar(h_ema);
      double adx = ReadPrevBar(h_adx);

      int bars_h4 = Bars(sym, PERIOD_H4);
      datetime last_bar = (datetime)SeriesInfoInteger(sym, PERIOD_H4, SERIES_LASTBAR_DATE);

      FileWrite(fh, "SYMBOL", b,
                StringFormat("resolved=%s spread=%.1fpip", sym, sp_pip),
                StringFormat("atr=%.5f ema=%.5f adx=%.1f", atr, ema, adx),
                StringFormat("bid=%.5f 通道下=%.5f 通道上=%.5f",
                             SymbolInfoDouble(sym, SYMBOL_BID),
                             ema - 2.0 * atr, ema + 2.0 * atr),
                StringFormat("h4bars=%d lastbar=%s", bars_h4,
                             TimeToString(last_bar, TIME_DATE|TIME_MINUTES)),
                adx > 38.0 ? "ADX>38 加碼被擋" : "");

      IndicatorRelease(h_atr); IndicatorRelease(h_ema); IndicatorRelease(h_adx);
   }

   FileFlush(fh);
   FileClose(fh);

   string full = TerminalInfoString(TERMINAL_DATA_PATH) + "\\MQL5\\Files\\" + fname;
   Print("================================================================================");
   PrintFormat("🎉 診斷完成：%s", full);
   PrintFormat("   帳號 %I64d @ %s | 伺服器時間 %s (UTC%+.1f)",
               acct, AccountInfoString(ACCOUNT_SERVER),
               TimeToString(srv, TIME_DATE|TIME_SECONDS), off);
   PrintFormat("   EA 持倉 %d 筆、其他持倉 %d 筆、層數記錄 %d 筆", n_mine, n_other, n_gv);
   if(newest > 0)
      PrintFormat("   ⏱️ EA 最後一次加碼：%s", TimeToString(newest, TIME_DATE|TIME_SECONDS));
   else
      Print("   ⚠️ 完全沒有層數記錄——EA 可能從未在這個終端機成功加碼過");
   if(!TerminalInfoInteger(TERMINAL_TRADE_ALLOWED))
      Print("   🔴 自動交易是關閉的，EA 不會下任何單！");
   Print("================================================================================");

   Alert(StringFormat("診斷完成\n帳號 %I64d\nEA持倉 %d 筆\n檔案：MQL5/Files/%s",
                      acct, n_mine, fname));
}
//+------------------------------------------------------------------+
