//+------------------------------------------------------------------+
//|                                            ListSymbolSpecs.mq5   |
//|  列出經紀商提供的全部商品與其規格，供挑選新標的用                  |
//+------------------------------------------------------------------+
#property copyright "Grid Strategy Project"
#property version   "1.00"
#property description "把 broker 提供的所有商品連同點差、隔夜利息、合約規格匯出成 CSV。"
#property description "用途：現有 12 檔都是 G8 貨幣兩兩組合，有效獨立賭注只有 8.8 個。"
#property description "要提高分散度得找結構上不同的標的，但得先知道這家 broker 有什麼。"
#property script_show_inputs

// 為什麼需要即時點差而不只是規格：SYMBOL_SPREAD 給的是當下 tick 的點差，
// 離峰時段會偏大。這支只做粗篩（找出「有哪些商品、量級如何」），
// 真正要用的中位數點差仍須跑 ExportForexRealCosts.mq5 做 7 天取樣。

input bool   InpOnlyTradable = true;   // 只列出可交易的（排除純看盤商品）
input int    InpMaxSymbols   = 2000;   // 保護用上限

//+------------------------------------------------------------------+
void OnStart()
{
   string csv = "mt5_symbol_specs.csv";
   int fh = FileOpen(csv, FILE_WRITE|FILE_CSV|FILE_ANSI, ",");
   if(fh == INVALID_HANDLE)
   { PrintFormat("❌ 無法建立 %s（錯誤 %d）", csv, GetLastError()); return; }

   FileWrite(fh, "symbol", "path", "digits", "point", "spread_points", "spread_pips",
                 "tick_value", "tick_size", "contract_size", "vol_min", "vol_step",
                 "swap_long", "swap_short", "swap_mode", "margin_initial", "trade_mode");

   int total = SymbolsTotal(false);          // false = 全部，不只 Market Watch
   if(total > InpMaxSymbols) total = InpMaxSymbols;
   int written = 0;

   for(int i = 0; i < total; i++)
   {
      string sym = SymbolName(i, false);
      if(sym == "") continue;

      ENUM_SYMBOL_TRADE_MODE tm =
         (ENUM_SYMBOL_TRADE_MODE)SymbolInfoInteger(sym, SYMBOL_TRADE_MODE);
      if(InpOnlyTradable && tm == SYMBOL_TRADE_MODE_DISABLED) continue;

      // 訂閱後規格才會填滿；讀完不取消訂閱，避免影響使用者的 Market Watch 排列
      if(!SymbolInfoInteger(sym, SYMBOL_SELECT)) SymbolSelect(sym, true);

      int    digits = (int)SymbolInfoInteger(sym, SYMBOL_DIGITS);
      double point  = SymbolInfoDouble(sym, SYMBOL_POINT);
      double pip    = (digits == 3 || digits == 5) ? point * 10.0 : point;
      long   sp_pts = SymbolInfoInteger(sym, SYMBOL_SPREAD);
      double sp_pip = (pip > 0) ? (sp_pts * point) / pip : 0.0;

      FileWrite(fh, sym,
                SymbolInfoString(sym, SYMBOL_PATH),
                IntegerToString(digits),
                DoubleToString(point, 8),
                IntegerToString(sp_pts),
                DoubleToString(sp_pip, 2),
                DoubleToString(SymbolInfoDouble(sym, SYMBOL_TRADE_TICK_VALUE), 5),
                DoubleToString(SymbolInfoDouble(sym, SYMBOL_TRADE_TICK_SIZE), 8),
                DoubleToString(SymbolInfoDouble(sym, SYMBOL_TRADE_CONTRACT_SIZE), 2),
                DoubleToString(SymbolInfoDouble(sym, SYMBOL_VOLUME_MIN), 2),
                DoubleToString(SymbolInfoDouble(sym, SYMBOL_VOLUME_STEP), 2),
                DoubleToString(SymbolInfoDouble(sym, SYMBOL_SWAP_LONG), 4),
                DoubleToString(SymbolInfoDouble(sym, SYMBOL_SWAP_SHORT), 4),
                EnumToString((ENUM_SYMBOL_SWAP_MODE)SymbolInfoInteger(sym, SYMBOL_SWAP_MODE)),
                DoubleToString(SymbolInfoDouble(sym, SYMBOL_MARGIN_INITIAL), 2),
                EnumToString(tm));
      written++;
   }

   FileFlush(fh);
   FileClose(fh);

   string path = TerminalInfoString(TERMINAL_DATA_PATH) + "\\MQL5\\Files\\" + csv;
   PrintFormat("🎉 已匯出 %d / %d 個商品的規格至：%s", written, SymbolsTotal(false), path);
   Alert(StringFormat("商品規格匯出完成\n共 %d 個\n位置：MQL5/Files/%s", written, csv));
}
//+------------------------------------------------------------------+
