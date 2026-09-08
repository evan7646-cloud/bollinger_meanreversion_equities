"""給 GitHub Actions 用：判斷距離上次成功更新是否已滿 55 分鐘。
印出 true/false 到 stdout，供 workflow 的 shell 讀取。"""
import json
import os
import sys
from datetime import datetime, timezone

path = "docs/data.json"
if not os.path.exists(path):
    print("true")
    sys.exit(0)

with open(path, encoding="utf-8") as f:
    meta = json.load(f)["meta"]

ts = meta["generated_at"].replace(" UTC", "")
last = datetime.strptime(ts, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
diff_min = (datetime.now(timezone.utc) - last).total_seconds() / 60.0

print(f"距離上次成功更新：{diff_min:.0f} 分鐘", file=sys.stderr)
print("true" if diff_min >= 55 else "false")
