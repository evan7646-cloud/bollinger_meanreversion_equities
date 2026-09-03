//+------------------------------------------------------------------+
//|                                   ExportYesterdayTrades.mq5      |
//|                                  Copyright 2026, Quant Fund Team |
//|   📤 匯出「昨天」EA 實際成交紀錄（帳戶History，用 magic number 篩選）      |
//|      每筆倉位還原成一行完整交易（進場時間/價、出場時間/價、損益）           |
//+------------------------------------------------------------------+
#property copyright "Copyright 2026, Quant Fund Team"
#property link      "https://github.com/evan7646-cloud"
#property version   "1.00"
#property script_show_inputs
#property description "匯出昨天帳戶History裡本EA(依magic number篩選)的實際成交紀錄，每個position還原成一行（進場/出場時間價格、損益/swap/手續費），輸出至 MQL5/Files/EA_LiveTrades_<日期>.csv"

input ulong InpMagicNumber = 20260901; // 篩選用的EA magic number（跟BollingerMeanReversion.mq5一致）
input int   InpDaysBack    = 1;        // 0=今天, 1=昨天, 2=前天...以此類推

//+------------------------------------------------------------------+
//| 依 position_id 找出該倉位的進場資訊（可能是更早之前開的倉）                |
//+------------------------------------------------------------------+
bool FindEntryDeal(ulong position_id, datetime &entry_time, double &entry_price, string &symbol,
                   string &direction, double &volume, double &entry_commission, double &entry_swap)
{
   // 從很早以前到現在整段搜尋這個 position_id 的歷史（進場可能發生在很久之前）
   if(!HistorySelectByPosition(position_id)) return false;

   entry_commission = 0;
   entry_swap = 0;

   int total = HistoryDealsTotal();
   for(int i = 0; i < total; i++)
   {
      ulong ticket = HistoryDealGetTicket(i);
      if(ticket == 0) continue;
      long entry_type = HistoryDealGetInteger(ticket, DEAL_ENTRY);
      if(entry_type == DEAL_ENTRY_IN)
      {
         entry_time  = (datetime)HistoryDealGetInteger(ticket, DEAL_TIME);
         entry_price = HistoryDealGetDouble(ticket, DEAL_PRICE);
         symbol      = HistoryDealGetString(ticket, DEAL_SYMBOL);
         volume      = HistoryDealGetDouble(ticket, DEAL_VOLUME);
         long dtype  = HistoryDealGetInteger(ticket, DEAL_TYPE);
         direction   = (dtype == DEAL_TYPE_BUY) ? "LONG" : "SHORT"; // 買進進場=做多，賣出進場=做空
         // 進場這一腿的手續費/倉息也要計入，否則成本會被低估（MT5是進出場各收一次手續費）
         entry_commission = HistoryDealGetDouble(ticket, DEAL_COMMISSION);
         entry_swap       = HistoryDealGetDouble(ticket, DEAL_SWAP);
         return true;
      }
   }
   return false;
}

//+------------------------------------------------------------------+
//| 腳本主入口                                                        |
//+------------------------------------------------------------------+
void OnStart()
{
   datetime now = TimeCurrent(); // 伺服器時間
   MqlDateTime dt;
   TimeToStruct(now, dt);
   dt.hour = 0; dt.min = 0; dt.sec = 0;
   datetime today_midnight = StructToTime(dt);

   datetime range_from = today_midnight - InpDaysBack * 86400;
   datetime range_to   = range_from + 86400; // 該天 00:00 ~ 隔天 00:00

   Print("==================================================================");
   Print("🚀 匯出 ", TimeToString(range_from, TIME_DATE), " 的 EA 實際成交紀錄（magic=", InpMagicNumber, "）...");
   Print("==================================================================");

   if(!HistorySelect(range_from, range_to))
   {
      Print("❌ HistorySelect 失敗，無法讀取帳戶歷史");
      return;
   }

   int total = HistoryDealsTotal();
   Print("該時段內帳戶總成交筆數（含其他EA/手動交易）: ", total);

   string date_part = TimeToString(range_from, TIME_DATE); // 格式為 2026.09.02
   StringReplace(date_part, ".", "-");                      // 只把日期裡的點換成減號，避免副檔名的點也被換掉
   string filename = "EA_LiveTrades_" + date_part + ".csv";
   int handle = FileOpen(filename, FILE_WRITE | FILE_CSV | FILE_ANSI, ",");
   if(handle == INVALID_HANDLE)
   {
      Print("❌ 無法開啟輸出檔案 ", filename);
      return;
   }
   FileWrite(handle, "position_id", "symbol", "direction", "volume",
             "entry_time", "entry_price", "exit_time", "exit_price",
             "profit", "swap", "commission", "net_pnl", "status", "comment");

   // 用陣列記錄已處理過的 position_id，避免同一倉位的 IN/OUT 兩筆deal被重複輸出兩行
   ulong processed_positions[];
   int processed_count = 0;
   int row_count = 0;

   for(int i = 0; i < total; i++)
   {
      ulong ticket = HistoryDealGetTicket(i);
      if(ticket == 0) continue;

      long magic = HistoryDealGetInteger(ticket, DEAL_MAGIC);
      if(magic != (long)InpMagicNumber) continue; // 只留本EA的deal

      long deal_entry = HistoryDealGetInteger(ticket, DEAL_ENTRY);
      if(deal_entry == DEAL_ENTRY_IN) continue; // IN deal 交給 FindEntryDeal 統一處理，這裡跳過避免重複

      ulong position_id = HistoryDealGetInteger(ticket, DEAL_POSITION_ID);

      bool already = false;
      for(int p = 0; p < processed_count; p++)
         if(processed_positions[p] == position_id) { already = true; break; }
      if(already) continue;

      ArrayResize(processed_positions, processed_count + 1);
      processed_positions[processed_count] = position_id;
      processed_count++;

      // 這是 OUT deal（平倉），彙總這個position在昨天範圍內所有OUT deal的損益/swap/手續費
      // （逾時/停損有時會分批平倉，同一position可能有多筆OUT deal）
      double total_profit = 0, total_swap = 0, total_commission = 0;
      datetime exit_time = 0;
      double exit_price = 0;
      string symbol_out = "";
      for(int j = 0; j < total; j++)
      {
         ulong t2 = HistoryDealGetTicket(j);
         if(t2 == 0) continue;
         if(HistoryDealGetInteger(t2, DEAL_MAGIC) != (long)InpMagicNumber) continue;
         if(HistoryDealGetInteger(t2, DEAL_POSITION_ID) != position_id) continue;
         if(HistoryDealGetInteger(t2, DEAL_ENTRY) == DEAL_ENTRY_IN) continue;

         total_profit     += HistoryDealGetDouble(t2, DEAL_PROFIT);
         total_swap       += HistoryDealGetDouble(t2, DEAL_SWAP);
         total_commission += HistoryDealGetDouble(t2, DEAL_COMMISSION);
         datetime tt = (datetime)HistoryDealGetInteger(t2, DEAL_TIME);
         if(tt > exit_time) { exit_time = tt; exit_price = HistoryDealGetDouble(t2, DEAL_PRICE); }
         symbol_out = HistoryDealGetString(t2, DEAL_SYMBOL);
      }

      datetime entry_time = 0; double entry_price = 0, volume = 0;
      double entry_commission = 0, entry_swap = 0;
      string symbol = symbol_out, direction = "?";
      FindEntryDeal(position_id, entry_time, entry_price, symbol, direction, volume, entry_commission, entry_swap);

      total_commission += entry_commission; // 補上進場腿的手續費
      total_swap       += entry_swap;       // 補上掛在進場腿的倉息（少數broker會這樣記）
      double net_pnl = total_profit + total_swap + total_commission;

      FileWrite(handle, IntegerToString((long)position_id), symbol, direction, DoubleToString(volume, 2),
                (entry_time > 0 ? TimeToString(entry_time, TIME_DATE|TIME_MINUTES) : "?"),
                DoubleToString(entry_price, 4),
                TimeToString(exit_time, TIME_DATE|TIME_MINUTES), DoubleToString(exit_price, 4),
                DoubleToString(total_profit, 2), DoubleToString(total_swap, 2), DoubleToString(total_commission, 2),
                DoubleToString(net_pnl, 2), "CLOSED", "");
      row_count++;
   }

   // 再找「昨天有開倉，但到現在還沒平倉」的position（IN deal在昨天，但目前仍是open position）
   for(int i = 0; i < total; i++)
   {
      ulong ticket = HistoryDealGetTicket(i);
      if(ticket == 0) continue;
      if(HistoryDealGetInteger(ticket, DEAL_MAGIC) != (long)InpMagicNumber) continue;
      if(HistoryDealGetInteger(ticket, DEAL_ENTRY) != DEAL_ENTRY_IN) continue;

      ulong position_id = HistoryDealGetInteger(ticket, DEAL_POSITION_ID);
      bool already = false;
      for(int p = 0; p < processed_count; p++)
         if(processed_positions[p] == position_id) { already = true; break; }
      if(already) continue; // 已經在上面當作CLOSED處理過了

      if(PositionSelectByTicket(position_id)) // 目前還是open position
      {
         string symbol   = HistoryDealGetString(ticket, DEAL_SYMBOL);
         double volume    = HistoryDealGetDouble(ticket, DEAL_VOLUME);
         datetime entry_time = (datetime)HistoryDealGetInteger(ticket, DEAL_TIME);
         double entry_price  = HistoryDealGetDouble(ticket, DEAL_PRICE);
         long dtype = HistoryDealGetInteger(ticket, DEAL_TYPE);
         string direction = (dtype == DEAL_TYPE_BUY) ? "LONG" : "SHORT";
         double floating_pnl = PositionGetDouble(POSITION_PROFIT) + PositionGetDouble(POSITION_SWAP);

         FileWrite(handle, IntegerToString((long)position_id), symbol, direction, DoubleToString(volume, 2),
                   TimeToString(entry_time, TIME_DATE|TIME_MINUTES), DoubleToString(entry_price, 4),
                   "-", "-", "0.00", "0.00", "0.00", DoubleToString(floating_pnl, 2), "OPEN(尚未平倉)", "");
         row_count++;
      }
   }

   FileClose(handle);
   Print("==================================================================");
   Print("🎉 完成！共 ", row_count, " 筆倉位紀錄，已存至 MQL5/Files/", filename);
   Print("==================================================================");
   Comment("✅ 昨日交易紀錄匯出完成\n共 ", row_count, " 筆\n檔案：", filename);
}
