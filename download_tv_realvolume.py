import time
from tvDatafeed import TvDatafeed, Interval

MISSING_SYMBOLS = {
    "ARM": "NASDAQ",
    "ASML": "NASDAQ",
    "AZN": "NASDAQ",
    "BA": "NYSE",
    "BABA": "NYSE",
    "BRK.B": "NYSE",
    "FDX": "NYSE",
    "GM": "NYSE",
    "GME": "NYSE",
    "GOOG": "NASDAQ",
    "KO": "NYSE",
    "LMT": "NYSE",
    "MSTR": "NASDAQ",
    "NKE": "NYSE",
    "PLTR": "NYSE",
    "RACE": "NYSE",
    "RTX": "NYSE",
    "SNOW": "NYSE",
    "SPCX": "NASDAQ",
    "ZM": "NASDAQ",
}

OUT_DIR = "data_tv_intraday_5m"


def fetch_one(tv, sym, exch):
    df = tv.get_hist(
        symbol=sym,
        exchange=exch,
        interval=Interval.in_5_minute,
        n_bars=5000,
        extended_session=False,
    )
    if df is None or df.empty:
        raise ValueError("empty result")
    return df.reset_index()


def main():
    tv = TvDatafeed()
    ok, failed = [], []

    for sym, exch in MISSING_SYMBOLS.items():
        df = None
        for attempt in range(3):
            try:
                df = fetch_one(tv, sym, exch)
                break
            except Exception as e:
                print(f"  attempt {attempt + 1} for {sym} failed: {e}")
                if attempt < 2:
                    time.sleep(45)
                    tv = TvDatafeed()

        if df is None:
            failed.append((sym, exch, "failed after retries"))
            print(f"FAIL {sym:8s} ({exch:7s}) failed after retries")
        else:
            df.to_csv(f"{OUT_DIR}/{sym}_5m.csv", index=False)
            ok.append((sym, exch, len(df), df["datetime"].min(), df["datetime"].max()))
            print(f"OK  {sym:8s} ({exch:7s}) rows={len(df):5d}  {df['datetime'].min()} -> {df['datetime'].max()}")

        time.sleep(8)

    print("\n=== summary ===")
    print(f"downloaded: {len(ok)} / {len(MISSING_SYMBOLS)}")
    if failed:
        print("failed symbols:")
        for sym, exch, err in failed:
            print(f"  {sym} ({exch}): {err}")


if __name__ == "__main__":
    main()
