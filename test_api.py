"""
EPO OPS API テスト
実行前に環境変数をセット:
  $env:EPO_CONSUMER_KEY = "your_key"
  $env:EPO_CONSUMER_SECRET = "your_secret"
"""
import os
import sys
sys.path.insert(0, "src")

from collect import load_config, collect_all

# 環境変数チェック
key = os.environ.get("EPO_CONSUMER_KEY", "")
secret = os.environ.get("EPO_CONSUMER_SECRET", "")
if not key or not secret:
    print("[ERROR] 環境変数が未設定です。")
    print("  $env:EPO_CONSUMER_KEY = 'your_key'")
    print("  $env:EPO_CONSUMER_SECRET = 'your_secret'")
    sys.exit(1)

import logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")

config = load_config("config.yaml")
articles = collect_all(config)

print(f"\n--- 収集結果: {len(articles)} 件 ---")
for i, a in enumerate(articles[:10], 1):
    pub = a.published.strftime("%Y-%m-%d") if a.published else "不明"
    assignees = "、".join(a.authors[:2]) if a.authors else "不明"
    print(f"[{i}] {pub} | {a.patent_number} | {assignees}")
    print(f"     {a.title[:70]}")
    if a.ipc:
        print(f"     IPC: {a.ipc}")
    print()
