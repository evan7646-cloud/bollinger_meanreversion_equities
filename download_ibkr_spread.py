import os
import sys
import time
from ib_insync import IB, Stock, util

from optimize_orb_tv import MT5_TRADABLE_US

HOST = "127.0.0.1"
PORT = 7496
CLIENT_ID = 11
OUT_DIR = "data_ibkr_spread"
TOTAL_DAYS = 60    # two months is plenty to characterise the spread level
CHUNK = "10 D"
SLEEP_BETWEEN = 2
RECONNECT_RETRIES = 5
RECONNECT_WAIT = 5

IB_SYMBOL_OVERRIDES = {"BRK.B": "BRK B"}


def connect():
    ib = IB()
    ib.connect(HOST, PORT, clientId=CLIENT_ID, timeout=15)
    return ib


def already_done(symbol):
    path = f"{OUT_DIR}/{symbol}_spread.csv"
    return os.path.exists(path) and os.path.getsize(path) > 0


def fetch_symbol(ib, symbol):
    contract = Stock(IB_SYMBOL_OVERRIDES.get(symbol, symbol), "SMART", "USD")
    ib.qualifyContracts(contract)

    all_bars = []
    end = ""
    fetched_days = 0
    while fetched_days < TOTAL_DAYS:
        bars = ib.reqHistoricalData(
            contract,
            endDateTime=end,
            durationStr=CHUNK,
            barSizeSetting="5 mins",
            whatToShow="BID_ASK",   # open=avg bid, close=avg ask, high=max ask, low=min bid
            useRTH=True,
            formatDate=1,
        )
        if not bars:
            break
        all_bars = bars + all_bars
        end = bars[0].date.strftime("%Y%m%d %H:%M:%S")
        fetched_days += 10
        time.sleep(SLEEP_BETWEEN)

    if not all_bars:
        return None
    df = util.df(all_bars)
    df = df.drop_duplicates(subset="date").sort_values("date")
    df = df.rename(columns={"open": "avg_bid", "close": "avg_ask", "high": "max_ask", "low": "min_bid"})
    df["mid"] = (df["avg_bid"] + df["avg_ask"]) / 2
    df["spread_bps"] = (df["avg_ask"] - df["avg_bid"]) / df["mid"] * 10000
    return df[["date", "avg_bid", "avg_ask", "mid", "spread_bps"]]


def main():
    resume = "--resume" in sys.argv
    todo = [s for s in MT5_TRADABLE_US if not (resume and already_done(s))]
    if resume:
        print(f"resume: 略過 {len(MT5_TRADABLE_US) - len(todo)} 檔，剩 {len(todo)} 檔")

    ib = connect()
    print("connected:", ib.isConnected())

    ok, failed = [], []
    for sym in todo:
        attempt = 0
        while attempt < RECONNECT_RETRIES:
            try:
                if not ib.isConnected():
                    print(f"  重新連線 (第{attempt + 1}次)...")
                    time.sleep(RECONNECT_WAIT)
                    ib = connect()
                df = fetch_symbol(ib, sym)
                if df is None or df.empty:
                    failed.append(sym)
                    print(f"FAIL {sym}: no data")
                else:
                    df.to_csv(f"{OUT_DIR}/{sym}_spread.csv", index=False)
                    ok.append(sym)
                    print(f"OK   {sym:6s} rows={len(df):5d}  spread中位數={df['spread_bps'].median():.2f}bps")
                break
            except Exception as e:
                attempt += 1
                print(f"  {sym} 第{attempt}次失敗: {e}")
                if attempt >= RECONNECT_RETRIES:
                    failed.append(sym)
                    print(f"FAIL {sym}: 重試{RECONNECT_RETRIES}次仍失敗")
                else:
                    time.sleep(RECONNECT_WAIT)
        time.sleep(1)

    ib.disconnect()
    print(f"\n完成: {len(ok)}/{len(todo)}  失敗: {failed}")


if __name__ == "__main__":
    main()
