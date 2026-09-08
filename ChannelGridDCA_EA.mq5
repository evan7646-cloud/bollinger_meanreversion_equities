//+------------------------------------------------------------------+
//|                                            ChannelGridDCA_EA.mq5 |
//|                            通道均值回歸 DCA 網格 EA（多貨幣對版）  |
//|                                                                  |
//|  對應回測：fx_engine_v2.py / fx_portfolio_v2.py（網頁為準）        |
//|  資料來源：TradingView 的 Pepperstone 報價，已轉換成 MT5 broker 時間|
//+------------------------------------------------------------------+
#property copyright "Grid Strategy Project"
#property version   "3.00"
#property description "通道均值回歸 DCA 網格：跌破 EMA50-2ATR 做多、突破 EMA50+2ATR 做空，"
#property description "最多 4 層 DCA，止盈 min(EMA50, 均價+1ATR)，硬停損 均價-4ATR。"
#property description "手數以帳戶幣別名目金額換算（正確處理交叉盤跨幣別），非固定手數。"
#property description "v3：改回用 MT5 內建 PERIOD_H4 算 EMA/ATR（v2 曾經自建 UTC 對齊4H，"
#property description "現在 Python 回測改成直接對齊 MT5 broker 時間，兩邊統一用 broker 時區，"
#property description "MT5 原生 H4 本來就是照 broker 伺服器時間切的，不需要再自己組K棒）。"
#property description "v3.1：標的改為 G8 全28檔掃描後 Sharpe 前10（GBPNZD/GBPCHF/NZDCHF 點差為估計值）。"

#include <Trade/Trade.mqh>

//--- 交易標的與資金
input group "═══ 標的與資金 ═══"
input string InpSymbols        = "AUDCAD,AUDCHF,GBPNZD,CADJPY,GBPCHF,EURCHF,NZDUSD,EURAUD,AUDUSD,NZDCHF"; // 交易貨幣對（G8全28檔掃描後Sharpe前10）
input bool   InpSizeByEquity   = true;      // 部位大小依帳戶淨值百分比（false = 用固定美元金額）
input double InpBaseOrderPct   = 6.0;       // 首單名目金額 = 淨值的百分之幾（6% ≈ $25,000 帳戶的 $1,500）
input double InpBaseOrderUSD   = 1500.0;    // 首單名目金額（InpSizeByEquity=false 時使用）
input double InpSizeMultiplier = 1.2;       // 加碼金額倍數（每層固定 1.2 倍首單，非複利）
input double InpLotMultiplier  = 1.0;       // 全域下單倍數（例如輸入 2 = 所有手數放大兩倍；不影響風控比例）

//--- 策略參數（與回測完全一致，不建議調整）
input group "═══ 策略參數（已驗證，勿隨意調整）═══"
input int    InpEmaPeriod      = 50;        // EMA 週期（中軌）
input int    InpAtrPeriod      = 20;        // ATR 週期
input double InpChannelK       = 2.0;       // 進場通道 ATR 倍數
input double InpDcaStepAtr     = 1.5;       // 每層加碼間距（ATR 倍數）
input double InpStopAtr        = 4.0;       // 硬停損距離（ATR 倍數）── 絕對不可設為 0
input double InpTpAtr          = 1.0;       // 較近止盈距離（ATR 倍數）
input int    InpMaxLayers      = 4;         // 最大加碼層數
input bool   InpAllowLong      = true;      // 允許做多
input bool   InpAllowShort     = true;      // 允許做空

//--- 風控與執行
input group "═══ 風控與執行 ═══"
input double InpMaxSpreadMult  = 3.0;       // 點差超過長期中位數的幾倍時禁止新開倉
input string InpMedianSpreads  = "1.2,0.8,2.5,1.0,2.5,0.9,0.5,1.1,0.4,2.5"; // 各對點差中位數(pips)，順序須對應 InpSymbols（GBPNZD/GBPCHF/NZDCHF 為估計值）
input double InpMaxTotalRiskPct= 60.0;      // 所有部位名目總和上限（占淨值百分比）
input long   InpMagic          = 20260904;  // magic number
input int    InpSlippagePoints = 20;        // 允許滑價（points）
input bool   InpVerboseLog     = true;      // 詳細日誌

//--- 全域
CTrade         trade;
string         g_syms[];
double         g_median_spread[];
int            g_ema_handle[];
int            g_atr_handle[];
int            g_count = 0;

//+------------------------------------------------------------------+
//| 初始化                                                            |
//+------------------------------------------------------------------+
int OnInit()
{
   trade.SetExpertMagicNumber(InpMagic);
   trade.SetDeviationInPoints(InpSlippagePoints);
   trade.SetTypeFillingBySymbol(_Symbol);

   int n = StringSplit(InpSymbols, ',', g_syms);
   if(n <= 0) { Print("❌ InpSymbols 解析失敗"); return INIT_PARAMETERS_INCORRECT; }

   string sp_parts[];
   int m = StringSplit(InpMedianSpreads, ',', sp_parts);

   ArrayResize(g_median_spread, n);
   ArrayResize(g_ema_handle, n);
   ArrayResize(g_atr_handle, n);

   for(int i = 0; i < n; i++)
   {
      StringTrimLeft(g_syms[i]); StringTrimRight(g_syms[i]);

      if(!SymbolSelect(g_syms[i], true))
         PrintFormat("⚠️ 無法訂閱 %s，請確認經紀商代碼（可能需要後綴，如 %s.r）", g_syms[i], g_syms[i]);

      g_median_spread[i] = (i < m) ? StringToDouble(sp_parts[i]) : 1.5;

      g_ema_handle[i] = iMA(g_syms[i], PERIOD_H4, InpEmaPeriod, 0, MODE_EMA, PRICE_CLOSE);
      g_atr_handle[i] = iATR(g_syms[i], PERIOD_H4, InpAtrPeriod);

      if(g_ema_handle[i] == INVALID_HANDLE || g_atr_handle[i] == INVALID_HANDLE)
      {
         PrintFormat("❌ %s 指標建立失敗", g_syms[i]);
         return INIT_FAILED;
      }
   }
   g_count = n;

   if(InpStopAtr <= 0.0)
   {
      Print("❌ 硬停損被設為 0 —— 這正是先前那些「100% 勝率」假回測的成因，拒絕啟動。");
      return INIT_PARAMETERS_INCORRECT;
   }

   PrintFormat("✅ EA v3 啟動：%d 檔標的 | MT5內建H4 (broker伺服器時間) | 伺服器時間 %s",
               g_count, TimeToString(TimeCurrent(), TIME_DATE | TIME_SECONDS));
   PrintFormat("   Python回測資料來源已改為 TradingView Pepperstone 報價、換算成同一個 broker 時區，");
   PrintFormat("   兩邊現在用同一套 4H K棒切法，不需要 EA 自己組K棒。");
   return INIT_SUCCEEDED;
}

void OnDeinit(const int reason)
{
   for(int i = 0; i < g_count; i++)
   {
      if(g_ema_handle[i] != INVALID_HANDLE) IndicatorRelease(g_ema_handle[i]);
      if(g_atr_handle[i] != INVALID_HANDLE) IndicatorRelease(g_atr_handle[i]);
   }
   Comment("");
}

//+------------------------------------------------------------------+
//| 依「帳戶幣別名目金額」換算手數                                     |
//| 核心：1 手的名目價值 = (tick_value / tick_size) × 現價             |
//| 這個式子由 MT5 自行提供的 tick_value 帶入，已含跨幣別轉換，        |
//| 因此 AUDCAD、CADJPY 這類交叉盤也會得到正確手數。                   |
//+------------------------------------------------------------------+
double CalcLots(string sym, double target_notional, bool &undersized)
{
   undersized = false;
   double tick_val = SymbolInfoDouble(sym, SYMBOL_TRADE_TICK_VALUE);
   double tick_sz  = SymbolInfoDouble(sym, SYMBOL_TRADE_TICK_SIZE);
   double price    = SymbolInfoDouble(sym, SYMBOL_BID);
   if(tick_val <= 0 || tick_sz <= 0 || price <= 0) return 0.0;

   double notional_per_lot = (tick_val / tick_sz) * price;   // 1 手在帳戶幣別下的名目價值
   if(notional_per_lot <= 0) return 0.0;

   double lots = target_notional / notional_per_lot;

   double vmin = SymbolInfoDouble(sym, SYMBOL_VOLUME_MIN);
   double vmax = SymbolInfoDouble(sym, SYMBOL_VOLUME_MAX);
   double vstep= SymbolInfoDouble(sym, SYMBOL_VOLUME_STEP);
   if(vstep <= 0) vstep = 0.01;

   lots = MathFloor(lots / vstep) * vstep;                   // 無條件捨去，避免超額下單
   lots = NormalizeDouble(lots, 2);

   if(lots < vmin)
   {
      undersized = true;                                     // 目標金額小於最小手數，實際風險會偏大
      lots = vmin;
   }
   if(lots > vmax) lots = vmax;
   return lots;
}

//+------------------------------------------------------------------+
//| 取得某標的的合計部位（同時支援 netting 與 hedging 帳戶）           |
//+------------------------------------------------------------------+
bool GetAggregatePosition(string sym, int &dir, double &volume, double &avg_price, int &n_pos)
{
   dir = 0; volume = 0.0; avg_price = 0.0; n_pos = 0;
   double weighted = 0.0;

   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0) continue;
      if(PositionGetString(POSITION_SYMBOL) != sym) continue;
      if(PositionGetInteger(POSITION_MAGIC) != InpMagic) continue;

      long ptype = PositionGetInteger(POSITION_TYPE);
      double vol = PositionGetDouble(POSITION_VOLUME);
      double opx = PositionGetDouble(POSITION_PRICE_OPEN);

      dir = (ptype == POSITION_TYPE_BUY) ? 1 : -1;
      volume  += vol;
      weighted += opx * vol;
      n_pos++;
   }
   if(volume <= 0) { dir = 0; return false; }
   avg_price = weighted / volume;
   return true;
}

//+------------------------------------------------------------------+
//| 層數：優先讀全域變數（跨重啟保存），退而求其次用持倉筆數           |
//+------------------------------------------------------------------+
string LayerKey(string sym) { return StringFormat("CGDCA_%d_%s_layer", InpMagic, sym); }

int GetLayer(string sym, int n_pos)
{
   string k = LayerKey(sym);
   if(GlobalVariableCheck(k)) return (int)GlobalVariableGet(k);
   return MathMax(n_pos, 1);
}
void SetLayer(string sym, int layer) { GlobalVariableSet(LayerKey(sym), layer); }
void ClearLayer(string sym)          { GlobalVariableDel(LayerKey(sym)); }

//+------------------------------------------------------------------+
//| 目前所有部位的名目總和（帳戶幣別）                                 |
//+------------------------------------------------------------------+
double TotalNotional()
{
   double total = 0.0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0) continue;
      if(PositionGetInteger(POSITION_MAGIC) != InpMagic) continue;
      string sym = PositionGetString(POSITION_SYMBOL);
      double vol = PositionGetDouble(POSITION_VOLUME);
      double tv = SymbolInfoDouble(sym, SYMBOL_TRADE_TICK_VALUE);
      double ts = SymbolInfoDouble(sym, SYMBOL_TRADE_TICK_SIZE);
      double px = SymbolInfoDouble(sym, SYMBOL_BID);
      if(tv > 0 && ts > 0 && px > 0) total += vol * (tv / ts) * px;
   }
   return total;
}

//+------------------------------------------------------------------+
//| 更新該標的所有部位的停損（DCA 後均價改變，停損必須跟著移動）       |
//+------------------------------------------------------------------+
void UpdateStops(string sym, int dir, double avg_price, double atr)
{
   double sl = (dir > 0) ? avg_price - InpStopAtr * atr : avg_price + InpStopAtr * atr;
   double tp = (dir > 0) ? avg_price + InpTpAtr   * atr : avg_price - InpTpAtr   * atr;
   int digits = (int)SymbolInfoInteger(sym, SYMBOL_DIGITS);
   sl = NormalizeDouble(sl, digits);
   tp = NormalizeDouble(tp, digits);

   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0) continue;
      if(PositionGetString(POSITION_SYMBOL) != sym) continue;
      if(PositionGetInteger(POSITION_MAGIC) != InpMagic) continue;

      double cur_sl = PositionGetDouble(POSITION_SL);
      double cur_tp = PositionGetDouble(POSITION_TP);
      if(MathAbs(cur_sl - sl) > SymbolInfoDouble(sym, SYMBOL_POINT) ||
         MathAbs(cur_tp - tp) > SymbolInfoDouble(sym, SYMBOL_POINT))
      {
         if(!trade.PositionModify(ticket, sl, tp))
            PrintFormat("⚠️ %s 修改停損失敗 retcode=%d", sym, trade.ResultRetcode());
      }
   }
}

//+------------------------------------------------------------------+
//| 平掉該標的所有部位                                                 |
//+------------------------------------------------------------------+
void CloseAll(string sym, string reason)
{
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0) continue;
      if(PositionGetString(POSITION_SYMBOL) != sym) continue;
      if(PositionGetInteger(POSITION_MAGIC) != InpMagic) continue;
      if(!trade.PositionClose(ticket, InpSlippagePoints))
         PrintFormat("⚠️ %s 平倉失敗 retcode=%d", sym, trade.ResultRetcode());
   }
   ClearLayer(sym);
   if(InpVerboseLog) PrintFormat("🔵 %s 全部平倉（%s）", sym, reason);
}

//+------------------------------------------------------------------+
//| 主迴圈                                                            |
//+------------------------------------------------------------------+
void OnTick()
{
   double equity = AccountInfoDouble(ACCOUNT_EQUITY);
   double lot_mult = (InpLotMultiplier > 0.0) ? InpLotMultiplier : 1.0;   // 防呆：0 或負值視為 1 倍
   double base_notional = (InpSizeByEquity ? equity * InpBaseOrderPct / 100.0 : InpBaseOrderUSD) * lot_mult;
   double dca_notional  = base_notional * InpSizeMultiplier;
   double max_notional  = equity * InpMaxTotalRiskPct / 100.0;   // 上限不隨倍數放大，天然剎車

   string dashboard = StringFormat("通道網格 DCA v3（MT5內建H4，broker時區）─ 淨值 %.2f ─ 首單名目 %.0f（下單倍數 x%.2f）\n",
                                    equity, base_notional, lot_mult);

   for(int i = 0; i < g_count; i++)
   {
      string sym = g_syms[i];
      if(sym == "") continue;

      //--- 指標值（用「已收盤」的 K 棒，不會重繪）
      double ema_buf[], atr_buf[];
      if(CopyBuffer(g_ema_handle[i], 0, 1, 1, ema_buf) <= 0) continue;
      if(CopyBuffer(g_atr_handle[i], 0, 1, 1, atr_buf) <= 0) continue;
      double ema = ema_buf[0];
      double atr = atr_buf[0];
      if(atr <= 0) continue;

      double bid = SymbolInfoDouble(sym, SYMBOL_BID);
      double ask = SymbolInfoDouble(sym, SYMBOL_ASK);
      if(bid <= 0 || ask <= 0) continue;

      //--- 點差過濾：真實資料顯示極端時段點差可達中位數的 10~40 倍
      double point = SymbolInfoDouble(sym, SYMBOL_POINT);
      int    digits = (int)SymbolInfoInteger(sym, SYMBOL_DIGITS);
      double pip_size = (digits == 3 || digits == 5) ? point * 10.0 : point;
      double spread_pips = (ask - bid) / pip_size;
      bool spread_ok = (spread_pips <= g_median_spread[i] * InpMaxSpreadMult);

      double lower = ema - InpChannelK * atr;
      double upper = ema + InpChannelK * atr;

      int dir, n_pos, layer;
      double vol, avg;
      bool has_pos = GetAggregatePosition(sym, dir, vol, avg, n_pos);

      if(!has_pos)
      {
         ClearLayer(sym);

         if(spread_ok && TotalNotional() + base_notional <= max_notional)
         {
            bool go_long  = InpAllowLong  && (bid <= lower);
            bool go_short = InpAllowShort && (bid >= upper);

            if(go_long || go_short)
            {
               bool undersized;
               double lots = CalcLots(sym, base_notional, undersized);
               if(lots > 0)
               {
                  double entry = go_long ? ask : bid;
                  double sl = go_long ? entry - InpStopAtr * atr : entry + InpStopAtr * atr;
                  double tp = go_long ? entry + InpTpAtr   * atr : entry - InpTpAtr   * atr;
                  sl = NormalizeDouble(sl, digits);
                  tp = NormalizeDouble(tp, digits);

                  bool ok = go_long ? trade.Buy(lots, sym, 0.0, sl, tp, "CGDCA L1")
                                    : trade.Sell(lots, sym, 0.0, sl, tp, "CGDCA L1");
                  if(ok)
                  {
                     SetLayer(sym, 1);
                     PrintFormat("🟢 %s 第1層 %s %.2f 手（名目 %.0f）%s | 點差 %.1f pips",
                                 sym, go_long ? "買進" : "賣出", lots, base_notional,
                                 undersized ? "⚠️低於最小手數已提高至最小值" : "", spread_pips);
                  }
                  else PrintFormat("⚠️ %s 開倉失敗 retcode=%d", sym, trade.ResultRetcode());
               }
            }
         }
      }
      else
      {
         layer = GetLayer(sym, n_pos);

         //--- 止盈：min(EMA50, 均價+1ATR)，較近者先到就出（EMA50 會移動，須由 EA 監控）
         double tp_level = (dir > 0) ? MathMin(ema, avg + InpTpAtr * atr)
                                     : MathMax(ema, avg - InpTpAtr * atr);
         bool hit_tp = (dir > 0) ? (bid >= tp_level) : (ask <= tp_level);

         if(hit_tp)
         {
            CloseAll(sym, StringFormat("止盈 @ %.5f", tp_level));
         }
         else if(layer < InpMaxLayers && spread_ok)
         {
            //--- DCA 加碼：均價 ∓ 層數 × 1.5ATR
            double step = layer * InpDcaStepAtr * atr;
            bool hit_dca = (dir > 0) ? (bid <= avg - step) : (ask >= avg + step);

            if(hit_dca && TotalNotional() + dca_notional <= max_notional)
            {
               bool undersized;
               double lots = CalcLots(sym, dca_notional, undersized);
               if(lots > 0)
               {
                  bool ok = (dir > 0) ? trade.Buy(lots, sym, 0.0, 0, 0, StringFormat("CGDCA L%d", layer + 1))
                                      : trade.Sell(lots, sym, 0.0, 0, 0, StringFormat("CGDCA L%d", layer + 1));
                  if(ok)
                  {
                     SetLayer(sym, layer + 1);
                     // 重新讀取合併後均價，並把停損/止盈一起移動
                     int d2, n2; double v2, a2;
                     if(GetAggregatePosition(sym, d2, v2, a2, n2))
                        UpdateStops(sym, d2, a2, atr);
                     PrintFormat("🟡 %s 第%d層加碼 %.2f 手（名目 %.0f）新均價 %.5f",
                                 sym, layer + 1, lots, dca_notional, a2);
                  }
                  else PrintFormat("⚠️ %s 加碼失敗 retcode=%d", sym, trade.ResultRetcode());
               }
            }
         }

         //--- 確保停損永遠存在且對齊目前均價（ATR 會隨 K 棒變動）
         if(has_pos && !hit_tp)
            UpdateStops(sym, dir, avg, atr);
      }

      if(InpVerboseLog)
         dashboard += StringFormat("%-8s %s L%d %.2f手 | 中軌%.5f 下軌%.5f 上軌%.5f | 點差%.1f%s\n",
                                   sym, has_pos ? (dir > 0 ? "多" : "空") : "－",
                                   has_pos ? GetLayer(sym, n_pos) : 0, vol,
                                   ema, lower, upper, spread_pips, spread_ok ? "" : " ⛔");
   }

   if(InpVerboseLog) Comment(dashboard);
}
//+------------------------------------------------------------------+
