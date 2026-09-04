import glob
import pandas as pd
from zoneinfo import ZoneInfo

# tvDatafeed's websocket client returns timestamps that pandas parses as
# naive local-machine time (this machine is Asia/Taipei, UTC+8), even though
# the library requests exchange-timezone data. Left uncorrected, grouping
# these naive timestamps by calendar date splits a single NYSE session
# (21:30-04:00 Taipei time) across two dates and destroys the opening-range
# definition -- the same bug found in the MT5 CFD data.
SRC_TZ = ZoneInfo("Asia/Taipei")
DST_TZ = ZoneInfo("America/New_York")

FILES = sorted(glob.glob("data_tv_intraday_5m/*_5m.csv"))


def fix_file(path):
    df = pd.read_csv(path)
    dt = pd.to_datetime(df["datetime"])
    dt = dt.dt.tz_localize(SRC_TZ).dt.tz_convert(DST_TZ).dt.tz_localize(None)
    df["datetime"] = dt
    df.to_csv(path, index=False)
    return dt.min(), dt.max()


def main():
    for path in FILES:
        lo, hi = fix_file(path)
        print(f"{path:45s} {lo} -> {hi}")
    print(f"\nfixed {len(FILES)} files")


if __name__ == "__main__":
    main()
