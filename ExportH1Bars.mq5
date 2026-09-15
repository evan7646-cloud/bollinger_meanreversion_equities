//+------------------------------------------------------------------+
//|                                                 ExportH1Bars.mq5 |
//|  從 MT5 匯出 1 小時 K 棒，供 Python 回測使用                       |
//+------------------------------------------------------------------+
#property copyright "Grid Strategy Project"
#property version   "1.00"
#property description "匯出 G8 全 28 檔（+XAUUSD）的 H1 OHLC 到 MQL5/Files/mt5_h1/。"
#property description "用途：取代 TradingView 免登入模式只能拿到 ~1.7 年的限制。"
#property description "MT5 的 H1 歷史通常有數年，且時間戳就是 broker 伺服器時間，"
#property description "夏令時間由 broker 自己處理，不需要外部換算，也不會對不準。"
#property script_show_inputs

// 為什麼匯出 H1 而不是 H4：
// 回測端需要自行把 broker 00:00 那一根丟掉再重採樣成 4H（換日結算時段點差
// 十幾倍、資料有系統性假跳空，見 strategy_logic_and_backtest_truth.md 第 2.4 節）。
// 若直接匯出 H4，那根尖刺已經被併進 K 棒的 high/low，無法事後剔除。

input string InpSymbols = "EURUSD,GBPUSD,USDJPY,USDCHF,USDCAD,AUDUSD,NZDUSD,EURGBP,EURJPY,EURCHF,EURCAD,EURAUD,EURNZD,GBPJPY,GBPCHF,GBPCAD,GBPAUD,GBPNZD,CHFJPY,CADJPY,AUDJPY,NZDJPY,CADCHF,AUDCHF,NZDCHF,AUDCAD,NZDCAD,AUDNZD,XAUUSD,USDSGD,USDCNH,USDNOK,EURNOK,USDSEK,EURPLN,USDPLN,EURCZK,USDCZK,EURHUF,USDHUF"; // 目標商品清單（G8全28檔＋黃金＋11檔非G8：SGD/CNH/NOK/SEK/PLN/CZK/HUF）
input int    InpYearsBack     = 10;   // 往回抓幾年（MT5 沒那麼多就給多少算多少）
input int    InpMaxWaitSec    = 30;   // 每檔等待終端機下載歷史的最長秒數
input string InpOutSubdir     = "mt5_h1"; // 輸出子資料夾（位於 MQL5/Files/ 底下）

//+------------------------------------------------------------------+
//| 智慧比對經紀商實際品種名稱（處理 .r / + / m 等後綴）              |
//+------------------------------------------------------------------+
string ResolveSymbol(string base_sym)
{
   string suffixes[] = {"", ".r", "+", "m", ".raw", ".pro", "_i", ".c", ".s", ".ecn", "_sb", ".a", ".b"};
   for(int s = 0; s < ArraySize(suffixes); s++)
   {
      string candidate = base_sym + suffixes[s];
      if(SymbolInfoInteger(candidate, SYMBOL_SELECT) || SymbolSelect(candidate, true))
         return candidate;
   }
   return "";
}

//+------------------------------------------------------------------+
//| 觸發並等待終端機下載該商品的 H1 歷史                              |
//+------------------------------------------------------------------+
int WarmupRates(string sym, datetime from_time, MqlRates &out[])
{
   ulong t0 = GetTickCount();
   int got = 0;
   while((GetTickCount() - t0) < (ulong)InpMaxWaitSec * 1000)
   {
      got = CopyRates(sym, PERIOD_H1, from_time, TimeCurrent(), out);
      if(got > 0) return got;          // 拿到就走
      Sleep(500);                      // 首次呼叫常為空，這是在等終端機向伺服器要資料
   }
   return CopyRates(sym, PERIOD_H1, from_time, TimeCurrent(), out);  // 最後再試一次
}

//+------------------------------------------------------------------+
void OnStart()
{
   Print("================================================================================");
   Print(">>> 匯出 MT5 H1 歷史 K 棒 <<<");
   PrintFormat("    往回 %d 年，輸出至 MQL5/Files/%s/", InpYearsBack, InpOutSubdir);
   Print("================================================================================");

   string parts[];
   int n = StringSplit(InpSymbols, ',', parts);
   if(n <= 0) { Print("❌ InpSymbols 解析不到任何商品"); return; }

   datetime from_time = TimeCurrent() - (datetime)InpYearsBack * 365 * 86400;
   int ok_count = 0;
   long total_bars = 0;

   for(int i = 0; i < n; i++)
   {
      string base_sym = parts[i];
      StringTrimLeft(base_sym); StringTrimRight(base_sym);
      if(base_sym == "") continue;

      Comment(StringFormat("匯出中：%s (%d/%d)", base_sym, i + 1, n));

      string sym = ResolveSymbol(base_sym);
      if(sym == "")
      { PrintFormat("  ⚠️ [未找到] 經紀商未提供 %s", base_sym); continue; }
      SymbolSelect(sym, true);

      MqlRates rates[];
      ArraySetAsSeries(rates, false);          // 由舊到新，寫檔順序才正確
      int got = WarmupRates(sym, from_time, rates);
      if(got <= 0)
      { PrintFormat("  ⚠️ %s：取不到 H1 歷史（等了 %d 秒）", sym, InpMaxWaitSec); continue; }

      // 用 base_sym 當檔名，Python 端才對得上（不含 broker 後綴）
      string path = InpOutSubdir + "\\" + base_sym + "_h1.csv";
      int fh = FileOpen(path, FILE_WRITE|FILE_CSV|FILE_ANSI, ",");
      if(fh == INVALID_HANDLE)
      { PrintFormat("  ❌ %s：無法建立檔案 %s（錯誤 %d）", sym, path, GetLastError()); continue; }

      FileWrite(fh, "datetime", "open", "high", "low", "close");
      int digits = (int)SymbolInfoInteger(sym, SYMBOL_DIGITS);
      for(int k = 0; k < got; k++)
      {
         FileWrite(fh,
                   TimeToString(rates[k].time, TIME_DATE|TIME_MINUTES),
                   DoubleToString(rates[k].open,  digits),
                   DoubleToString(rates[k].high,  digits),
                   DoubleToString(rates[k].low,   digits),
                   DoubleToString(rates[k].close, digits));
      }
      FileFlush(fh);
      FileClose(fh);

      double years = (double)(rates[got-1].time - rates[0].time) / (365.25 * 86400.0);
      PrintFormat("  ✅ [%-8s] %6d 根 H1 | %s → %s | 約 %.2f 年",
                  base_sym, got,
                  TimeToString(rates[0].time, TIME_DATE),
                  TimeToString(rates[got-1].time, TIME_DATE),
                  years);
      ok_count++;
      total_bars += got;
   }

   Comment("");
   string dir = TerminalInfoString(TERMINAL_DATA_PATH) + "\\MQL5\\Files\\" + InpOutSubdir;
   Print("================================================================================");
   PrintFormat("🎉 完成：%d / %d 檔，共 %I64d 根 H1。檔案位於：%s", ok_count, n, total_bars, dir);
   Print("   注意：時間戳為 broker 伺服器時間，Python 端不需要再做時區換算。");
   Print("================================================================================");

   Alert(StringFormat("H1 匯出完成\n成功 %d / %d 檔\n共 %I64d 根\n位置：MQL5/Files/%s/",
                      ok_count, n, total_bars, InpOutSubdir));
}
//+------------------------------------------------------------------+
