"""
外匯真實資料來源：TradingView 上的 Pepperstone 報價（PEPPERSTONE:xxx）

取代 yfinance 的兩個原因：
  1. yfinance 是通用聚合「指示性報價」，不綁定任何特定 broker/ECN，
     實測 quoteSourceName='Delayed Quote' 且 bid/ask 方向還會顛倒——資料品質不明。
  2. Pepperstone 是這個策略實際下單的 broker，用它的報價回測才是真正跟實盤同源。

時區處理（這是這份檔案存在的主要理由，務必看完再改動）：
  `tvDatafeed` 套件內部用 `datetime.datetime.fromtimestamp(epoch)` 把 TradingView
  回傳的 Unix 秒數轉成字串時間，這個函式在「沒有指定 tz」時，會用**執行當下這台機器
  的作業系統本地時區**去轉換。也就是說同一份資料，在台北時間(UTC+8)的電腦上跑，
  跟在 GitHub Actions(UTC+0) 上跑，拿到的 naive datetime 數字會完全不同——
  但兩者背後代表的真實 UTC 時刻是一樣的。

  所以這裡不能寫死「減8小時」這種常數，而是在執行當下動態量出「這台機器的本地時區
  跟 UTC 差幾小時」，用這個量出來的差值把 tvDatafeed 給的 naive 時間換回真正 UTC，
  再加上 broker 的伺服器時區（`InpBrokerGmtOffsetHours` 對應的同一個數字，預設 UTC+3）
  換成「MT5 看到的時間」。這樣不管這支程式在哪台機器上跑，算出來的 4H K棒切法都會
  跟你 MT5 終端機的 PERIOD_H4 完全對齊。
"""

import time
import datetime as dt

import pandas as pd
from tvDatafeed import TvDatafeed, Interval

BROKER_GMT_OFFSET_HOURS = 3.0   # 跟 ChannelGridDCA_EA.mq5 的 InpBrokerGmtOffsetHours 保持同一個數字
MAX_BARS = 15000                # tvDatafeed 1H 大約能拿到 600+ 天

_tv = None


def _get_tv():
    global _tv
    if _tv is None:
        _tv = TvDatafeed()
    return _tv


def local_utc_offset_hours() -> float:
    """量測『這台機器現在的本地時區』跟 UTC 差幾小時——不是寫死的常數。

    做法：同一瞬間分別讀「naive 本地時間」與「tz-aware UTC 時間」，兩者相減
    就是這台機器的本地時區偏移。只要機器的時區設定沒有 DST（台北、多數
    GitHub Actions runner 用的 UTC 都沒有），這個偏移量對任何歷史時間點都成立。
    """
    utc_now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    local_now = dt.datetime.now()
    return round((local_now - utc_now).total_seconds() / 3600.0, 2)


def fetch_pepperstone_1h(symbol: str, retries: int = 3) -> pd.DataFrame:
    """回傳欄位 [datetime, open, high, low, close]，datetime 已經是『MT5 broker 時間』。"""
    offset_local = local_utc_offset_hours()
    shift_hours = BROKER_GMT_OFFSET_HOURS - offset_local  # 一次性換算：本地naive -> broker時間

    last_err = None
    for attempt in range(retries):
        try:
            tv = _get_tv()
            df = tv.get_hist(symbol=symbol, exchange="PEPPERSTONE",
                             interval=Interval.in_1_hour, n_bars=MAX_BARS)
            if df is None or df.empty:
                raise ValueError("empty result")
            df = df.reset_index().rename(columns={"datetime": "raw_local"})
            df["datetime"] = df["raw_local"] + pd.Timedelta(hours=shift_hours)
            df = df[["datetime", "open", "high", "low", "close"]].sort_values("datetime")
            df = df.drop_duplicates(subset="datetime")
            return df.reset_index(drop=True)
        except Exception as e:
            last_err = e
            print(f"  {symbol}: attempt {attempt + 1}/{retries} failed ({e})")
            time.sleep(5)
            global _tv
            _tv = None  # 重新建立連線再試
    raise RuntimeError(f"{symbol}: 抓取失敗 ({last_err})")


def resample_4h_broker_time(df: pd.DataFrame) -> pd.DataFrame:
    """把已經是 MT5 broker 時間的 1H 資料，切成跟 MT5 內建 PERIOD_H4 對齊的 4H K棒。"""
    d = df.set_index("datetime").sort_index()
    out = (d.resample("4h", label="left", closed="left")
            .agg({"open": "first", "high": "max", "low": "min", "close": "last"})
            .dropna())
    return out.reset_index()


if __name__ == "__main__":
    offset = local_utc_offset_hours()
    print(f"本機時區相對UTC：{offset:+.1f} 小時")
    print(f"broker時區相對UTC：{BROKER_GMT_OFFSET_HOURS:+.1f} 小時")
    print(f"換算位移量：{BROKER_GMT_OFFSET_HOURS - offset:+.1f} 小時（套用在 tvDatafeed 回傳的naive時間上）")

    df = fetch_pepperstone_1h("EURUSD")
    print(f"\nEURUSD: {len(df)} 筆，{df['datetime'].min()} → {df['datetime'].max()}（已是 MT5 broker 時間）")
    print(df.tail(3))

    b4 = resample_4h_broker_time(df)
    print(f"\n重採樣成 4H：{len(b4)} 根，最後一根 bucket 起始於 {b4['datetime'].iloc[-1]}（MT5 broker 時間）")
