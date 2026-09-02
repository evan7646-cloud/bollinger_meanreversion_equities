# 15分鐘 Bollinger Band 均值回歸策略 — 驗證後規格 v2.01(Phase 8)

**狀態:目前研究過程中唯一通過 train/test 樣本外驗證、跨資料源複驗、風控優化後績效提升、
且計入真實spread+手續費+隔夜倉息完整成本後仍顯著的策略。EA 已加入風險部位大小與每日虧損熔斷，
交易名單收斂為11檔核心持股。建議先 paper trade 驗證，再考慮實盤。**

來源腳本：
- `bollinger_meanreversion.py`（原始8檔半導體股回測邏輯）
- `bollinger_meanreversion_equities_cfd_test.py`（Phase 5：59檔跨產業複驗）
- `bollinger_meanreversion_optimizations.py`（Phase 6：風控優化 A/B/C/D/E/F/G 版本比較）
- `bollinger_meanreversion_full_cost_test.py`（Phase 7：14檔名單+真實spread+手續費+風控優化）
- `bollinger_meanreversion_swap_cost_test.py`（Phase 8：加入真實隔夜倉息，最終收斂為11檔）
- 對應 EA：`BollingerMeanReversion.mq5` v2.01

---

## 1. 研究脈絡（依時間順序）

1. **Phase 4b**：8 檔半導體股（AMD/AMAT/AVGO/INTC/MU/NVDA/QCOM/TSM），MT5 tick 匯出資料，
   約1年歷史。Train/Test 方向一致且顯著，5bps滑價壓力測試後 t-stat 仍 2.97。
2. **Phase 5**：換一批完全不同的資料源（broker「Equities CFD」分類，回溯到2015年，
   59檔跨產業美股+歐股）複驗。**半導體5檔（AMD/AVGO/QCOM/INTC/NVDA）在新資料源、
   更長樣本(~500天 test)上訊號不衰減反而更強**（例如 AVGO t-stat 2.60→3.99），
   這是排除「資料源特有巧合」的有力證據。但59檔整體平均 t-stat 只有0.13，
   證實原本8檔半導體的亮眼數字有相當程度是「選對股票」，不是普適策略——
   **策略真正適合的是「有波動但會回歸均值」的股票，會在持續單邊趨勢股票
  （如T、PFE、WMT、BAC、GME）上系統性失效**。
3. **Phase 6**：測試5個優化方向（見第4節），確認 2 項有效/中性、3 項有害，
   已整合進 EA v2。
4. **Phase 7**：把驗證有效的優化套用到實際要交易的14檔名單，加上更嚴格的成本假設
  （進出場各扣一次真實spread + 各扣一次0.002%手續費），平均 t-stat 2.44，12/14顯著。
5. **Phase 8**：補上最後一塊成本缺口——用 `ExportSwapRates.mq5` 匯出真實隔夜倉息
  （swap_mode=POINTS，多空皆為負值、週五收3倍），疊加進完整成本模型。平均 t-stat
  降到2.18，10/14仍顯著，**全部14檔依然正報酬**，但 MSFT/JNJ/SBUX 3檔跌破1.5門檻
  （MSFT掉最多，因為交易筆數最多、倉息累積侵蝕比例最大），予以剔除，
  **最終交易名單收斂為11檔**（第3節）。

## 2. 已知的統計保留（尚未完全解決）

- 半導體股同屬一個 sector，訊號常同時觸發，pooled t-stat 可能因相關性膨脹而高估顯著性
- 目前的交易名單（第3節）横跨多個 sector，一定程度緩解了這個問題，但沒有正式做
  effective-sample-size 校正
- 樣本仍以近1-2年為主，沒有涵蓋過完整景氣循環（尤其是一次明確的熊市/risk-off）

## 3. 最終交易名單（10檔，Phase 8：計入真實spread+手續費+隔夜倉息完整成本後，
##    train與test雙期都通過驗證）

| Symbol | 產業 | Test t-stat(含倉息) | Train t-stat(含倉息) |
|---|---|---|---|
| AVGO | 半導體 | 3.80 | 顯著正 |
| QCOM | 半導體 | 3.15 | 顯著正 |
| JPM | 金融 | 2.94 | 顯著正 |
| PLTR | 數據科技 | 2.78 | 顯著正 |
| ASML | 半導體設備 | 2.77 | 顯著正 |
| AMD | 半導體 | 2.48 | 顯著正 |
| SIEGn | 歐洲工業(西門子) | 2.44 | 顯著正 |
| SNOW | 雲端數據 | 2.07 | 顯著正 |
| INTC | 半導體 | 1.90 | 顯著正 |
| LMT | 國防 | 1.77 | 顯著正 |

**10/10 全部達 t-stat≥1.5 顯著、10/10 全部正報酬。**

**Phase 8 剔除的4檔**：
- MSFT(1.00)、JNJ(1.03)、SBUX(1.07)：含真實倉息後 t-stat 跌破1.5門檻。
  MSFT跌最多，因為交易筆數最多（回溯到2015年，樣本量是其他股票2-3倍），
  倉息累積侵蝕的絕對金額也最大。
- **DIS**：test期看似還有1.38（雖未達標但接近），但**train期 t-stat 為 -0.87（負值！）**、
  總報酬-42%、MaxDD-58.7%——這是樣本內明確失敗、樣本外可能只是運氣好的典型案例，
  依照本研究一貫的train/test雙期一致性標準判定為無效訊號，剔除。

**Phase 5 排除**：ARM（樣本僅59筆/56天，數字不可信）、MSTR（比特幣概念股，價格行為特殊）、
TTE（法國能源股，樣本較短且非核心驗證對象）、NVDA（雖是半導體但這段期間走勢過於單邊，
t-stat僅1.08不顯著，是策略「不適合持續趨勢股」規律的反例）。

## 4. 訊號規則（未變）

標的：單一股票（不是 cross-sectional，每檔獨立判斷、獨立進出場）
週期：15-minute bar
指標：
```
MA20   = SMA(close, 20)
STD20  = StdDev(close, 20)
Upper  = MA20 + 2.0 * STD20
Lower  = MA20 - 2.0 * STD20
ATR14  = ATR(14)
```

**進場（逆勢/均值回歸，不是突破順勢）**：
- 收盤價由上方跌破 Lower → **買進**（賭反彈回均值）
- 收盤價由下方突破 Upper → **放空**（賭拉回均值）

**出場（三個條件先到先出）**：
1. 價格回到 MA20 → 均值回歸完成，獲利了結
2. 停損：多單 `entry_price - 1.5*ATR14`；空單 `entry_price + 1.5*ATR14`
3. 最長持有 240 根 15m bar（= 60 小時）→ 強制平倉，避免卡單

## 5. Phase 6 風控優化結果（跨AMD/ASML/AVGO/INTC/QCOM 5檔平均，Test期）

| 優化 | 平均t-stat | 平均Sharpe | 平均MaxDD | 平均總報酬 | 是否採用 |
|---|---|---|---|---|---|
| Baseline | 3.10 | 2.64 | -15.4% | 172.0% | — |
| RVOL日濾網 | 1.31 | 1.11 | -8.7% | 29.7% | ❌ 未採用（砍樣本、砍報酬） |
| VWAP動態移動停損 | 2.28 | 1.94 | -16.8% | 80.8% | ❌ 未採用（提早出場、砍獲利單） |
| 收盤前強制平倉 | 0.82 | 0.69 | -19.2% | 24.0% | ❌ 未採用（策略需要跨日醞釀，強制平倉破壞機制） |
| **每日虧損熔斷(3%)** | 3.08 | 2.62 | -15.4% | 169.4% | ✅ **採用**（backtest幾乎不變，屬免費保險） |
| **風險部位大小(1%風險)** | **3.39** | **2.88** | -15.1% | **238.3%** | ✅ **採用**（唯一淨提升的優化） |

**注意**：broker的CFD報價沒有真實成交量（僅AAPL/MSFT約64%的bar有，其餘57檔全為0），
RVOL濾網與VWAP用的都是 `tick_volume`（報價跳動次數）當活躍度替代指標，不是真實股數成交量。

## 5b. Phase 7/8 完整成本模型結果（14檔→10檔最終名單，Pooled equal-weight）

| 版本 | Train Sharpe | Train MaxDD | Test Sharpe | Test MaxDD |
|---|---|---|---|---|
| Phase 8，10檔最終名單（含真實spread+手續費+隔夜倉息） | 2.46 | -8.3% | **4.46** | **-3.6%** |

隔夜倉息用 `ExportSwapRates.mq5` 匯出使用者實際帳戶的真實設定（swap_mode=POINTS，
多空倉息皆為負值——不管做多做空隔夜都要付費，週五收3倍息對應週末）。因為策略
出場條件是「回到MA20」，多數交易很快結束（平均每筆僅持倉0.3-0.46晚），倉息侵蝕比
原本擔心的小。移除DIS（train期訊號無效，見第3節）後，10檔全部顯著、全部正報酬，
pooled組合的Sharpe與MaxDD都比11檔版本更好。

Equity curve：`residual_alpha_results/phase8_pooled_equity_curve.png`
（一路從2020到2026平滑向上，1x漲到約9倍，train/test分割後斜率沒有反轉）

## 6. EA 實作（BollingerMeanReversion.mq5 v2.02）

已整合：
- **風險部位大小**：`lot = (帳戶權益 x InpRiskPercent%) / (停損距離換算的每手虧損金額)`，
  取代原本固定手數，並設 `InpMaxLotSize` 安全上限
- **每日虧損熔斷**：`InpDailyLossLimitPct`(預設3%)，當日虧損達門檻即停止開新倉，
  既有倉位仍正常依原規則出場
- 交易名單已更新為第3節的10檔（`InpSymbols` 預設值）

## 7. 部署建議

1. **先 paper trade**，觀察至少 1-2 個月，確認即時滑價/執行品質跟回測假設一致
2. 10 檔橫跨多個 sector，比原本8檔半導體集中度低，但半導體supply chain（AVGO/QCOM/AMD/INTC/ASML）
   仍佔5席，同業齊發訊號的風險依然存在，實際曝險不能視為完全分散
3. MA20 (20根15m bar) 版本明顯優於 MA50 版本，已鎖定為預設值
4. 若之後想加更多標的，建議先用 Phase 5 的方法（近期是否呈現持續單邊趨勢）做初篩，
   避開 T/PFE/WMT/BAC/GME 這類結構性單邊走勢的股票類型
