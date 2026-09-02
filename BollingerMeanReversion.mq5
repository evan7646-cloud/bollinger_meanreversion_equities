//+------------------------------------------------------------------+ // 檔案標頭起始
//|                                   BollingerMeanReversion.mq5    | // 檔案名稱
//|                                  Copyright 2026, Quant Fund Team | // 版權宣告
//|   15分鐘 Bollinger Band 均值回歸 EA (MA20±2σ逆勢)                      | // 用途說明
//|   v2: 加入風險部位大小(取代固定手數) + 每日虧損熔斷                        | // 用途說明續
//|   v2.1: 新增「等權重滿倉」配資模式，貼齊 Phase 7/8 組合回測的資金配置邏輯      | // 用途說明續
//+------------------------------------------------------------------+ // 檔案標頭結束
#property copyright "Copyright 2026, Quant Fund Team" // 版權所有人設定
#property link      "https://github.com/evan7646-cloud" // 專案官方連結
#property version   "2.10" // EA版本號
#property description "15m MA20+-2std 均值回歸：跌破下緣買、突破上緣空，出場條件為回到MA20/1.5xATR停損/最長持有240根bar。v2加入：(1)風險部位大小=帳戶權益x風險% / 停損距離換算手數，取代固定手數 (2)每日虧損熔斷，當日虧損達門檻即停止開新倉。兩項優化皆經 Phase 6 回測驗證：風險部位大小提升Sharpe/報酬，每日熔斷幾乎不影響回測績效但提供保護。RVOL濾網/VWAP移動停損/收盤前強制平倉三項回測證實會讓表現變差，故不採用。v2.01：Phase 8加入真實spread+手續費+隔夜倉息的完整成本回測後，交易名單從14檔收斂為11檔核心持股（排除含倉息後t-stat跌破1.5的MSFT/JNJ/SBUX）。v2.1：Phase 6的風險式下單只在「單檔、全倉」情境下驗證過，跟 Phase 7/8 組合回測（各檔固定拿 equity/n_symbols 滿倉、非按停損%反推）用的是不同資金配置邏輯，兩者數字對不上。新增 InpSizingMode=EQUAL_WEIGHT 模式，讓實盤手數計算對齊組合回測的等權重滿倉配置，預設改用此模式；風險式下單保留為可選項（RISK_BASED）。" // 功能說明
#property strict // 嚴格編譯模式

#include <Trade\Trade.mqh> // 引入交易操作類別

//--- 策略參數（跟 Python 回測 bollinger_meanreversion.py 的預設值保持一致）
input string   InpSymbols        = "AVGO,JPM,QCOM,PLTR,AMD,INTC,SIEGn,ASML,LMT,SNOW"; // 交易標的清單（Phase 8：含真實spread+手續費+隔夜倉息的完整成本回測後，train/test雙期都顯著的10檔核心持股。已排除ARM/MSTR/TTE/NVDA/MSFT/JNJ/SBUX/DIS——DIS雖test期1.38，但train期t-stat為負(-0.87)、總報酬-42%，判定為樣本內無效訊號）
input string   InpSymbolSuffix   = "";      // 自定義後綴（若經紀商為 NVDA.US 則填 .US，留空則自動探測）
input ENUM_TIMEFRAMES InpTimeframe = PERIOD_M15; // 交易週期（回測驗證用15分鐘）
input int      InpBandPeriod     = 20;      // Bollinger Band 均線週期（回測驗證 MA20 優於 MA50）
input double   InpBandDeviation  = 2.0;     // Bollinger Band 標準差倍數
input int      InpATRPeriod      = 14;      // ATR 週期（用於停損）
input double   InpATRMultiplier  = 1.5;     // 停損距離 = ATRMultiplier x ATR
input int      InpMaxHoldingBars = 240;     // 最長持有根數（240根15m bar = 60小時），逾時強制平倉
input ulong    InpMagicNumber    = 20260901; // EA magic number，用於識別本EA開的倉位
input int      InpSlippagePoints = 30;      // 允許滑價點數

enum ENUM_SIZING_MODE // 資金配置模式
{ // enum開始
   EQUAL_WEIGHT = 0, // 等權重滿倉（貼齊 Phase 7/8 組合回測：每檔固定拿 equity/n_symbols 去買/放空）
   RISK_BASED   = 1  // 風險式下單（Phase 6 單檔驗證版：帳戶權益 x 風險% / 停損距離）
}; // enum結束

input group "=== 資金配置模式 ==="
input ENUM_SIZING_MODE InpSizingMode = EQUAL_WEIGHT; // 選擇手數計算方式，預設對齊組合回測

input group "=== 等權重滿倉（對齊 Phase 7/8 組合回測的資金配置邏輯）==="
input double   InpMaxLotSize     = 5.0;     // 單筆手數安全上限（避免槓桿過大時算出離譜大的部位）
input double   InpFallbackLot    = 0.10;    // 無法取得合約規格等資訊時的保底固定手數

input group "=== 風險部位大小（Phase 6 單檔驗證：提升Sharpe與總報酬，MaxDD不變）==="
input double   InpRiskPercent    = 1.0;     // 每筆交易風險 = 帳戶權益 x 此百分比（僅 InpSizingMode=RISK_BASED 時生效）

input group "=== 每日虧損熔斷（Phase 6 驗證：backtest幾乎不影響績效，屬於免費保險）==="
input bool     InpEnableDailyLossLimit = true; // 是否啟用每日虧損熔斷
input double   InpDailyLossLimitPct    = 3.0;  // 當日虧損達帳戶權益此百分比即停止開新倉（既有倉位仍正常出場）

CTrade trade; // 建立交易操作物件

//--- 每個標的的執行期狀態（因為一個EA要同時管理多檔，所以用陣列存放各標的的狀態與指標handle）
string   g_symbols[];      // 已解析的實際券商代碼陣列
int      g_bandHandle[];   // 每個標的的 iBands handle
int      g_atrHandle[];    // 每個標的的 iATR handle
datetime g_lastBarTime[];  // 每個標的最後處理過的 bar 時間（用於偵測新K棒，避免同一根bar重複進場）

//--- 每日虧損熔斷的全域狀態
double   g_dayStartEquity = 0.0;   // 當日開盤時的帳戶權益
datetime g_currentDay = 0;         // 目前記錄中的交易日（僅日期部分）
bool     g_tradingHaltedToday = false; // 今日是否已觸發熔斷

//+------------------------------------------------------------------+ // 函數分隔
//| 智慧解析經紀商實際股票代碼 (處理 NVDA, NVDA.US, NVDA.a, #NVDA 等格式)   | // 函數說明
//+------------------------------------------------------------------+ // 分隔線
string ResolveStockSymbol(string base_sym) // 解析實際美股商品名稱
{ // 函數開始
   string candidates[8]; // 候選名稱陣列
   candidates[0] = base_sym + InpSymbolSuffix; // 使用者指定後綴
   candidates[1] = base_sym;                   // 純代碼 (如 NVDA，也涵蓋 SIEGn 這類broker原生代碼)
   candidates[2] = base_sym + ".US";           // 常見美股 CFD 後綴 (如 NVDA.US)
   candidates[3] = base_sym + ".a";            // Pepperstone Razor 後綴 (如 NVDA.a)
   candidates[4] = "#" + base_sym;             // 部分券商前綴 (如 #NVDA)
   candidates[5] = base_sym + "_US";           // 底線後綴 (如 NVDA_US)
   candidates[6] = base_sym + "m";             // 微型後綴
   candidates[7] = base_sym + ".r";            // 浮動點差後綴

   for(int i = 0; i < 8; i++) // 遍歷所有候選
   { // 迴圈開始
      if(candidates[i] == "") continue; // 空字串跳過
      if(SymbolSelect(candidates[i], true)) // 嘗試選取並加入市場報價視窗
         return candidates[i]; // 回傳實際券商代碼
   } // 迴圈結束

   for(int s = 0; s < SymbolsTotal(false); s++) // 搜尋市場報價視窗所有品種
   { // 迴圈開始
      string sname = SymbolName(s, false); // 取得品種名稱
      if(StringFind(sname, base_sym) >= 0) // 包含該美股代碼
      { // 找到相符
         SymbolSelect(sname, true); // 啟用商品
         return sname; // 回傳該代碼
      } // 判斷結束
   } // 迴圈結束

   return ""; // 找不到商品回傳空
} // 函數結束

//+------------------------------------------------------------------+ // 函數分隔
//| EA 初始化：解析標的清單、建立每個標的的指標 handle                     | // 函數說明
//+------------------------------------------------------------------+ // 分隔線
int OnInit() // 初始化函數
{ // 函數開始
   string parts[]; // 分割後的標的代碼暫存
   int n = StringSplit(InpSymbols, ',', parts); // 用逗號分割標的清單
   if(n <= 0) // 沒有解析到任何標的
   { // 錯誤處理
      Print("錯誤：InpSymbols 沒有解析到任何標的"); // 印出錯誤訊息
      return(INIT_PARAMETERS_INCORRECT); // 回傳初始化失敗
   } // 判斷結束

   ArrayResize(g_symbols, n); // 依標的數量調整陣列大小
   ArrayResize(g_bandHandle, n); // 調整 Bollinger Band handle 陣列
   ArrayResize(g_atrHandle, n); // 調整 ATR handle 陣列
   ArrayResize(g_lastBarTime, n); // 調整最後K棒時間陣列

   int valid_count = 0; // 有效標的計數
   for(int i = 0; i < n; i++) // 遍歷每個標的代碼
   { // 迴圈開始
      string base = parts[i]; // 原始代碼（未加後綴）
      StringTrimLeft(base); // 去除左側空白
      StringTrimRight(base); // 去除右側空白
      string resolved = ResolveStockSymbol(base); // 解析實際券商代碼

      if(resolved == "") // 解析失敗
      { // 錯誤處理
         Print("警告：無法解析標的 ", base, "，跳過此標的"); // 印出警告
         continue; // 跳過此標的
      } // 判斷結束

      int bh = iBands(resolved, InpTimeframe, InpBandPeriod, 0, InpBandDeviation, PRICE_CLOSE); // 建立 Bollinger Band handle
      int ah = iATR(resolved, InpTimeframe, InpATRPeriod); // 建立 ATR handle
      if(bh == INVALID_HANDLE || ah == INVALID_HANDLE) // 指標建立失敗
      { // 錯誤處理
         Print("警告：標的 ", resolved, " 指標 handle 建立失敗，跳過此標的"); // 印出警告
         continue; // 跳過此標的
      } // 判斷結束

      g_symbols[valid_count] = resolved; // 存入已解析代碼
      g_bandHandle[valid_count] = bh; // 存入 Bollinger Band handle
      g_atrHandle[valid_count] = ah; // 存入 ATR handle
      g_lastBarTime[valid_count] = 0; // 初始化最後K棒時間
      valid_count++; // 有效標的數+1

      Print("已註冊標的：", resolved, " (", EnumToString(InpTimeframe), ")"); // 印出註冊成功訊息
   } // 迴圈結束

   if(valid_count == 0) // 沒有任何標的成功註冊
   { // 錯誤處理
      Print("錯誤：沒有任何標的成功註冊"); // 印出錯誤訊息
      return(INIT_FAILED); // 回傳初始化失敗
   } // 判斷結束

   ArrayResize(g_symbols, valid_count); // 縮減陣列到實際有效數量
   ArrayResize(g_bandHandle, valid_count); // 同步縮減
   ArrayResize(g_atrHandle, valid_count); // 同步縮減
   ArrayResize(g_lastBarTime, valid_count); // 同步縮減

   trade.SetExpertMagicNumber(InpMagicNumber); // 設定 magic number
   trade.SetDeviationInPoints(InpSlippagePoints); // 設定允許滑價

   g_dayStartEquity = AccountInfoDouble(ACCOUNT_EQUITY); // 初始化當日起始權益
   g_currentDay = 0; // 強制下一次 OnTick 觸發一次日期重置

   Print("BollingerMeanReversion EA v2.1 初始化完成，共管理 ", valid_count, " 檔標的，",
         "配資模式=", (InpSizingMode == EQUAL_WEIGHT ? "等權重滿倉(對齊組合回測)" : ("風險式下單 " + DoubleToString(InpRiskPercent, 2) + "%/筆")),
         "，每日熔斷=", (InpEnableDailyLossLimit ? "啟用" : "停用"),
         "(", InpDailyLossLimitPct, "%)"); // 印出初始化完成訊息
   return(INIT_SUCCEEDED); // 回傳初始化成功
} // 函數結束

//+------------------------------------------------------------------+ // 函數分隔
//| EA 反初始化：釋放所有指標 handle                                      | // 函數說明
//+------------------------------------------------------------------+ // 分隔線
void OnDeinit(const int reason) // 反初始化函數
{ // 函數開始
   for(int i = 0; i < ArraySize(g_bandHandle); i++) // 遍歷所有 handle
   { // 迴圈開始
      if(g_bandHandle[i] != INVALID_HANDLE) IndicatorRelease(g_bandHandle[i]); // 釋放 Bollinger Band handle
      if(g_atrHandle[i] != INVALID_HANDLE) IndicatorRelease(g_atrHandle[i]); // 釋放 ATR handle
   } // 迴圈結束
} // 函數結束

//+------------------------------------------------------------------+ // 函數分隔
//| 每日虧損熔斷：偵測新交易日、重置起始權益，並判斷今日是否已觸發熔斷           | // 函數說明
//+------------------------------------------------------------------+ // 分隔線
void CheckDailyReset() // 檢查並處理跨日重置
{ // 函數開始
   MqlDateTime dt; // 時間結構
   TimeToStruct(TimeCurrent(), dt); // 取得目前伺服器時間
   dt.hour = 0; dt.min = 0; dt.sec = 0; // 只取日期部分
   datetime today = StructToTime(dt); // 轉回只含日期的時間戳

   if(today != g_currentDay) // 偵測到新的一天
   { // 執行重置
      g_currentDay = today; // 更新記錄中的交易日
      g_dayStartEquity = AccountInfoDouble(ACCOUNT_EQUITY); // 記錄今日起始權益
      g_tradingHaltedToday = false; // 重置熔斷旗標
      Print("📅 新交易日開始，起始權益: ", DoubleToString(g_dayStartEquity, 2)); // 印出提示
   } // 判斷結束
} // 函數結束

//+------------------------------------------------------------------+ // 函數分隔
//| 判斷今日是否已觸發每日虧損熔斷（若觸發，只擋新倉，不影響既有倉位出場）        | // 函數說明
//+------------------------------------------------------------------+ // 分隔線
bool IsDailyLossLimitHit() // 檢查每日虧損熔斷
{ // 函數開始
   if(!InpEnableDailyLossLimit) return false; // 未啟用直接回傳false
   if(g_dayStartEquity <= 0) return false; // 尚未初始化跳過

   double equity = AccountInfoDouble(ACCOUNT_EQUITY); // 目前權益
   double loss_pct = (g_dayStartEquity - equity) / g_dayStartEquity * 100.0; // 今日虧損百分比

   if(loss_pct >= InpDailyLossLimitPct) // 達到熔斷門檻
   { // 觸發熔斷
      if(!g_tradingHaltedToday) // 第一次觸發才印出訊息（避免洗版）
         Print("⚠️ 觸發每日虧損熔斷！今日虧損 ", DoubleToString(loss_pct, 2),
               "% (門檻 ", InpDailyLossLimitPct, "%)，今日停止開新倉，既有倉位仍正常管理"); // 印出警告
      g_tradingHaltedToday = true; // 標記已熔斷
   } // 判斷結束
   return g_tradingHaltedToday; // 回傳是否已熔斷
} // 函數結束

//+------------------------------------------------------------------+ // 函數分隔
//| 風險部位大小：用「帳戶權益 x 風險% / 停損距離」換算成手數                    | // 函數說明
//| 對應 Phase 6 回測驗證的 G_risk_based_sizing（停損越遠部位越小），            | // 函數說明續
//| 取代原本的固定手數，backtest顯示可提升 Sharpe 與總報酬、MaxDD不變           | // 函數說明續
//+------------------------------------------------------------------+ // 分隔線
double CalcRiskBasedLot(string symbol, double entry_price, double stop_price) // 計算風險部位手數
{ // 函數開始
   double stop_distance = MathAbs(entry_price - stop_price); // 停損距離（價格單位）
   if(stop_distance <= 0) return InpFallbackLot; // 距離異常，用保底手數

   double tick_size = SymbolInfoDouble(symbol, SYMBOL_TRADE_TICK_SIZE); // 最小報價變動單位
   double tick_value = SymbolInfoDouble(symbol, SYMBOL_TRADE_TICK_VALUE); // 每檔位對應帳戶幣別的價值
   if(tick_size <= 0 || tick_value <= 0) return InpFallbackLot; // 取得失敗，用保底手數

   double loss_per_lot = (stop_distance / tick_size) * tick_value; // 每 1 手在此停損距離下的虧損金額
   if(loss_per_lot <= 0) return InpFallbackLot; // 異常保護

   double risk_amount = AccountInfoDouble(ACCOUNT_EQUITY) * (InpRiskPercent / 100.0); // 此筆允許的風險金額
   double lot = risk_amount / loss_per_lot; // 風險金額 / 每手虧損 = 應下手數

   double min_lot  = SymbolInfoDouble(symbol, SYMBOL_VOLUME_MIN); // broker最小手數
   double max_lot  = SymbolInfoDouble(symbol, SYMBOL_VOLUME_MAX); // broker最大手數
   double lot_step = SymbolInfoDouble(symbol, SYMBOL_VOLUME_STEP); // broker手數步進

   if(lot_step > 0) lot = MathFloor(lot / lot_step) * lot_step; // 依步進取整（無條件捨去，避免超出風險預算）
   lot = MathMax(lot, min_lot); // 不得低於最小手數
   if(max_lot > 0) lot = MathMin(lot, max_lot); // 不得超過broker最大手數
   lot = MathMin(lot, InpMaxLotSize); // 不得超過使用者設定的安全上限

   return lot; // 回傳計算出的手數
} // 函數結束

//+------------------------------------------------------------------+ // 函數分隔
//| 等權重滿倉：對齊 Phase 7/8 組合回測的資金配置邏輯                        | // 函數說明
//| 回測是「每檔固定拿 equity/n_symbols 去做多空」（不管停損多遠），            | // 函數說明續
//| 不是按停損距離反推風險%。這裡用合約規格換算出同等的名目曝險手數。            | // 函數說明續
//+------------------------------------------------------------------+ // 分隔線
double CalcEqualWeightLot(string symbol, double price) // 計算等權重滿倉手數
{ // 函數開始
   int n_symbols = ArraySize(g_symbols); // 目前管理的標的數量
   if(n_symbols <= 0 || price <= 0) return InpFallbackLot; // 異常保護，用保底手數

   double contract_size = SymbolInfoDouble(symbol, SYMBOL_TRADE_CONTRACT_SIZE); // 每 1 手代表的股數/合約單位
   if(contract_size <= 0) return InpFallbackLot; // 取得失敗，用保底手數

   double slice_equity = AccountInfoDouble(ACCOUNT_EQUITY) / n_symbols; // 這一檔應分配到的資金（等權重）
   double notional_per_lot = price * contract_size; // 1 手在目前價位的名目價值
   if(notional_per_lot <= 0) return InpFallbackLot; // 異常保護

   double lot = slice_equity / notional_per_lot; // 該分配資金換算成的手數（滿倉，不用槓桿放大）

   double min_lot  = SymbolInfoDouble(symbol, SYMBOL_VOLUME_MIN); // broker最小手數
   double max_lot  = SymbolInfoDouble(symbol, SYMBOL_VOLUME_MAX); // broker最大手數
   double lot_step = SymbolInfoDouble(symbol, SYMBOL_VOLUME_STEP); // broker手數步進

   if(lot_step > 0) lot = MathFloor(lot / lot_step) * lot_step; // 依步進取整（無條件捨去，避免超出分配資金）
   lot = MathMax(lot, min_lot); // 不得低於最小手數
   if(max_lot > 0) lot = MathMin(lot, max_lot); // 不得超過broker最大手數
   lot = MathMin(lot, InpMaxLotSize); // 不得超過使用者設定的安全上限

   return lot; // 回傳計算出的手數
} // 函數結束

//+------------------------------------------------------------------+ // 函數分隔
//| 依照 InpSizingMode 選擇手數計算方式                                   | // 函數說明
//+------------------------------------------------------------------+ // 分隔線
double CalcLot(string symbol, double entry_price, double stop_price) // 統一入口：依模式選擇算法
{ // 函數開始
   if(InpSizingMode == EQUAL_WEIGHT) // 等權重滿倉模式（對齊組合回測）
      return CalcEqualWeightLot(symbol, entry_price); // 用等權重滿倉算法
   return CalcRiskBasedLot(symbol, entry_price, stop_price); // 用風險式下單算法
} // 函數結束

//+------------------------------------------------------------------+ // 函數分隔
//| 檢查某標的目前是否持有本EA開的倉位，回傳方向(0=無, 1=多, -1=空)         | // 函數說明
//+------------------------------------------------------------------+ // 分隔線
int GetPositionDirection(string symbol) // 取得持倉方向
{ // 函數開始
   for(int i = PositionsTotal() - 1; i >= 0; i--) // 反向遍歷所有持倉
   { // 迴圈開始
      ulong ticket = PositionGetTicket(i); // 取得持倉 ticket
      if(ticket <= 0) continue; // ticket無效跳過
      if(!PositionSelectByTicket(ticket)) continue; // 選取失敗跳過
      if(PositionGetString(POSITION_SYMBOL) != symbol) continue; // 標的不符跳過
      if((long)PositionGetInteger(POSITION_MAGIC) != (long)InpMagicNumber) continue; // 不是本EA的倉位跳過

      long ptype = PositionGetInteger(POSITION_TYPE); // 取得持倉類型
      return (ptype == POSITION_TYPE_BUY) ? 1 : -1; // 回傳方向
   } // 迴圈結束
   return 0; // 沒有持倉
} // 函數結束

//+------------------------------------------------------------------+ // 函數分隔
//| 平掉某標的本EA持有的倉位                                              | // 函數說明
//+------------------------------------------------------------------+ // 分隔線
void CloseSymbolPosition(string symbol) // 平倉函數
{ // 函數開始
   for(int i = PositionsTotal() - 1; i >= 0; i--) // 反向遍歷所有持倉（反向遍歷避免平倉後索引錯位）
   { // 迴圈開始
      ulong ticket = PositionGetTicket(i); // 取得持倉 ticket
      if(ticket <= 0) continue; // ticket無效跳過
      if(!PositionSelectByTicket(ticket)) continue; // 選取失敗跳過
      if(PositionGetString(POSITION_SYMBOL) != symbol) continue; // 標的不符跳過
      if((long)PositionGetInteger(POSITION_MAGIC) != (long)InpMagicNumber) continue; // 不是本EA的倉位跳過

      trade.PositionClose(ticket); // 平倉
   } // 迴圈結束
} // 函數結束

//+------------------------------------------------------------------+ // 函數分隔
//| 處理單一標的的訊號判斷與進出場（每根新K棒收盤時呼叫一次）                 | // 函數說明
//+------------------------------------------------------------------+ // 分隔線
void ProcessSymbol(int idx) // 處理單一標的
{ // 函數開始
   string symbol = g_symbols[idx]; // 取得標的代碼

   double upper[], mid[], lower[], atr[]; // 指標數值暫存陣列
   ArraySetAsSeries(upper, true); // 設定為序列（index 0 = 最新）
   ArraySetAsSeries(mid, true); // 設定為序列
   ArraySetAsSeries(lower, true); // 設定為序列
   ArraySetAsSeries(atr, true); // 設定為序列

   // 取最近3根已收盤K棒的指標值（index 1=最新已收盤bar，index 2=前一根，index 0=正在形成中不用）
   if(CopyBuffer(g_bandHandle[idx], 1, 0, 3, upper) < 3) return; // 複製上緣失敗則跳過
   if(CopyBuffer(g_bandHandle[idx], 0, 0, 3, mid) < 3) return; // 複製中軸失敗則跳過
   if(CopyBuffer(g_bandHandle[idx], 2, 0, 3, lower) < 3) return; // 複製下緣失敗則跳過
   if(CopyBuffer(g_atrHandle[idx], 0, 0, 2, atr) < 2) return; // 複製ATR失敗則跳過

   double close1 = iClose(symbol, InpTimeframe, 1); // 最新已收盤bar的收盤價
   double close2 = iClose(symbol, InpTimeframe, 2); // 前一根已收盤bar的收盤價

   double upper1 = upper[1], upper2 = upper[2]; // 最新/前一根的上緣值
   double lower1 = lower[1], lower2 = lower[2]; // 最新/前一根的下緣值
   double mid1   = mid[1];                       // 最新已收盤bar的中軸值
   double atr1   = atr[1];                        // 最新已收盤bar的ATR值

   int direction = GetPositionDirection(symbol); // 目前持倉方向

   //--- 若有持倉，先檢查出場條件（回到中軸 / 最長持有 / 停損由broker端SL自動觸發，這裡只補中軸和逾時出場）
   if(direction != 0) // 有持倉
   { // 判斷開始
      bool exit_signal = false; // 出場旗標
      if(direction == 1 && close1 >= mid1) exit_signal = true; // 多單：價格回到中軸以上，均值回歸完成
      if(direction == -1 && close1 <= mid1) exit_signal = true; // 空單：價格回到中軸以下，均值回歸完成

      //--- 檢查是否超過最長持有時間（用持倉開倉時間換算已經過幾根bar）
      for(int i = PositionsTotal() - 1; i >= 0; i--) // 遍歷持倉找出對應的開倉時間
      { // 迴圈開始
         ulong ticket = PositionGetTicket(i); // 取得ticket
         if(ticket <= 0) continue; // 無效跳過
         if(!PositionSelectByTicket(ticket)) continue; // 選取失敗跳過
         if(PositionGetString(POSITION_SYMBOL) != symbol) continue; // 標的不符跳過
         if((long)PositionGetInteger(POSITION_MAGIC) != (long)InpMagicNumber) continue; // 非本EA倉位跳過

         datetime open_time = (datetime)PositionGetInteger(POSITION_TIME); // 開倉時間
         int bars_held = iBarShift(symbol, InpTimeframe, open_time, false); // 換算已經過幾根bar
         if(bars_held >= InpMaxHoldingBars) exit_signal = true; // 超過最長持有時間，標記出場
         break; // 只需要找到第一筆即可
      } // 迴圈結束

      if(exit_signal) // 需要出場
      { // 執行出場
         CloseSymbolPosition(symbol); // 平倉（既有倉位出場不受每日熔斷影響）
         direction = 0; // 更新方向為無持倉
      } // 判斷結束
   } // 判斷結束

   //--- 若目前無持倉，檢查進場條件（熔斷觸發時完全跳過進場判斷，只允許出場邏輯運作）
   if(direction == 0 && !IsDailyLossLimitHit()) // 無持倉且今日未觸發熔斷
   { // 判斷開始
      bool breakout_down = (close1 < lower1) && (close2 >= lower2); // 收盤由上方跌破下緣
      bool breakout_up   = (close1 > upper1) && (close2 <= upper2); // 收盤由下方突破上緣

      double ask = SymbolInfoDouble(symbol, SYMBOL_ASK); // 取得目前賣價（買進用）
      double bid = SymbolInfoDouble(symbol, SYMBOL_BID); // 取得目前買價（賣出用）

      if(breakout_down) // 跌破下緣 -> 買進賭反彈
      { // 執行買進
         double sl = close1 - InpATRMultiplier * atr1; // 停損價（用收盤價估算，跟回測一致）
         double lot = CalcLot(symbol, ask, sl); // 依 InpSizingMode 計算手數
         if(!trade.Buy(lot, symbol, ask, sl, 0, "BB_MeanRev_Long")) // 送出買進市價單
            Print("買進失敗 ", symbol, " 錯誤碼: ", GetLastError()); // 印出失敗訊息
         else
            Print("買進 ", symbol, " lot=", DoubleToString(lot, 2), " @ ", ask, " SL=", sl); // 印出成功訊息
      } // 判斷結束
      else if(breakout_up) // 突破上緣 -> 放空賭拉回
      { // 執行放空
         double sl = close1 + InpATRMultiplier * atr1; // 停損價
         double lot = CalcLot(symbol, bid, sl); // 依 InpSizingMode 計算手數
         if(!trade.Sell(lot, symbol, bid, sl, 0, "BB_MeanRev_Short")) // 送出放空市價單
            Print("放空失敗 ", symbol, " 錯誤碼: ", GetLastError()); // 印出失敗訊息
         else
            Print("放空 ", symbol, " lot=", DoubleToString(lot, 2), " @ ", bid, " SL=", sl); // 印出成功訊息
      } // 判斷結束
   } // 判斷結束
} // 函數結束

//+------------------------------------------------------------------+ // 函數分隔
//| 主循環：每個 tick 呼叫，但只在每個標的出現新K棒時才真正評估訊號          | // 函數說明
//+------------------------------------------------------------------+ // 分隔線
void OnTick() // 主循環函數
{ // 函數開始
   CheckDailyReset(); // 每個tick先檢查是否跨日，跨日則重置每日虧損熔斷狀態

   for(int i = 0; i < ArraySize(g_symbols); i++) // 遍歷所有管理中的標的
   { // 迴圈開始
      datetime cur_bar_time = iTime(g_symbols[i], InpTimeframe, 0); // 取得目前正在形成中的K棒開盤時間
      if(cur_bar_time == 0) continue; // 取得失敗跳過

      if(cur_bar_time != g_lastBarTime[i]) // 出現新K棒（跟上次處理的時間不同）
      { // 新K棒處理
         g_lastBarTime[i] = cur_bar_time; // 更新最後處理時間
         ProcessSymbol(i); // 評估該標的的訊號
      } // 判斷結束
   } // 迴圈結束
} // 函數結束
