import os
import sys
import time
from ib_insync import IB, Stock, util

from optimize_orb_tv import MT5_TRADABLE_US

HOST = "127.0.0.1"
PORT = 7496       # TWS live=7496, paper=7497 (Gateway: 4001 live / 4002 paper)
CLIENT_ID = 7
OUT_DIR = "data_ibkr_5m"
TOTAL_DAYS = 250   # ~1 trading year; IB paces historical requests, so we chunk
CHUNK = "10 D"     # duration per request
SLEEP_BETWEEN = 2  # seconds; IB allows ~6 historical requests / 10s, stay well under
RECONNECT_RETRIES = 5
RECONNECT_WAIT = 5


def connect():
    ib = IB()
    ib.connect(HOST, PORT, clientId=CLIENT_ID, timeout=15)
    return ib


def already_done(symbol):
    path = f"{OUT_DIR}/{symbol}_5m.csv"
    return os.path.exists(path) and os.path.getsize(path) > 0


IB_SYMBOL_OVERRIDES = {"BRK.B": "BRK B"}  # IB uses a space, not a dot, for class shares


def fetch_symbol(ib, symbol):
    contract = Stock(IB_SYMBOL_OVERRIDES.get(symbol, symbol), "SMART", "USD")
    ib.qualifyContracts(contract)

    all_bars = []
    end = ""  # "" = now
    fetched_days = 0

    while fetched_days < TOTAL_DAYS:
        bars = ib.reqHistoricalData(
            contract,
            endDateTime=end,
            durationStr=CHUNK,
            barSizeSetting="5 mins",
            whatToShow="TRADES",
            useRTH=True,       # regular trading hours only -> clean 09:30-16:00 ET session
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
    return df


def main():
    resume = "--resume" in sys.argv
    todo = [s for s in MT5_TRADABLE_US if not (resume and already_done(s))]
    if resume:
        print(f"resume模式: 略過已完成的 {len(MT5_TRADABLE_US) - len(todo)} 檔，剩 {len(todo)} 檔待抓")

    ib = connect()
    print("connected:", ib.isConnected())

    ok, failed = [], []
    for sym in todo:
        attempt = 0
        while attempt < RECONNECT_RETRIES:
            try:
                if not ib.isConnected():
                    print(f"  重新連線中 (第{attempt + 1}次)...")
                    time.sleep(RECONNECT_WAIT)
                    ib = connect()
                df = fetch_symbol(ib, sym)
                if df is None or df.empty:
                    failed.append(sym)
                    print(f"FAIL {sym}: no data")
                else:
                    df.to_csv(f"{OUT_DIR}/{sym}_5m.csv", index=False)
                    ok.append(sym)
                    print(f"OK   {sym:6s} rows={len(df):5d}  {df['date'].min()} -> {df['date'].max()}")
                break
            except Exception as e:
                attempt += 1
                print(f"  {sym} 第{attempt}次嘗試失敗: {e}")
                if attempt >= RECONNECT_RETRIES:
                    failed.append(sym)
                    print(f"FAIL {sym}: 重試{RECONNECT_RETRIES}次仍失敗")
                else:
                    time.sleep(RECONNECT_WAIT)
        time.sleep(1)

    ib.disconnect()
    print(f"\n完成: {len(ok)}/{len(todo)}（本次執行範圍）  失敗: {failed}")
    still_missing = [s for s in MT5_TRADABLE_US if not already_done(s)]
    print(f"整體45檔中仍缺: {still_missing}")


if __name__ == "__main__":
    main()
