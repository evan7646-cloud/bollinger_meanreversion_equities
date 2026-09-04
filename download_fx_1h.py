import os
import time
import yfinance as yf

PAIRS = [
    "EURUSD", "USDJPY", "GBPUSD", "AUDUSD", "USDCAD", "USDCHF", "NZDUSD",
    "EURGBP", "EURJPY", "GBPJPY", "CADJPY", "CHFJPY", "EURCHF", "AUDNZD",
    "EURAUD", "GBPAUD", "AUDCAD", "NZDCAD",
]

OUT_DIR = "data_fx_1h"
os.makedirs(OUT_DIR, exist_ok=True)

for pair in PAIRS:
    ticker = f"{pair}=X"
    for attempt in range(3):
        try:
            df = yf.download(ticker, period="730d", interval="1h", progress=False, auto_adjust=False)
            if df.empty:
                raise ValueError("empty dataframe")
            df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
            df = df.reset_index()
            df = df.rename(columns={"Datetime": "datetime", "Open": "open", "High": "high", "Low": "low", "Close": "close"})
            df = df[["datetime", "open", "high", "low", "close"]]
            df.to_csv(os.path.join(OUT_DIR, f"{pair}_1h.csv"), index=False)
            print(f"{pair}: {len(df)} rows, {df['datetime'].min()} -> {df['datetime'].max()}")
            break
        except Exception as e:
            print(f"{pair}: attempt {attempt+1} failed ({e}), retrying...")
            time.sleep(3)
    else:
        print(f"{pair}: FAILED after retries")
