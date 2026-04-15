"""
オーケストレーター
情報収集 → フィルタリング → メール送信 の全ステップを実行する
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

# src ディレクトリを sys.path に追加（直接実行時のため）
sys.path.insert(0, str(Path(__file__).parent))

from collect import load_config, collect_all
from filter import run_filter
from email_sender import deliver

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="特許情報ダイジェスト 自動配信ツール")
    parser.add_argument(
        "--config",
        default="config.yaml",
        help="設定ファイルのパス（デフォルト: config.yaml）",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="メール送信をスキップして内容だけ確認する",
    )
    parser.add_argument(
        "--save-html",
        metavar="FILE",
        help="生成したHTMLをファイルに保存する（デバッグ用）",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    logger.info("=" * 50)
    logger.info("特許情報ダイジェスト 自動配信ツール 起動")
    logger.info("設定ファイル: %s", args.config)
    logger.info("=" * 50)

    # 1. 設定読み込み
    config = load_config(args.config)
    logger.info("設定読み込み完了")

    # 2. 情報収集
    logger.info("--- 情報収集フェーズ ---")
    articles = collect_all(config)

    if not articles:
        logger.warning("特許が1件も収集できませんでした。処理を終了します。")
        sys.exit(0)

    # 3. フィルタリング
    logger.info("--- フィルタリングフェーズ ---")
    filtered = run_filter(articles, config)

    if not filtered:
        logger.info("配信対象の特許が0件でした（キーワード・スコア閾値を確認してください）")

    if filtered:
        logger.info("配信対象: %d 件", len(filtered))

    # デバッグ用: 結果をコンソールに出力
    for i, a in enumerate(filtered, 1):
        logger.info(
            "  [%d] ★%d %s (%s)",
            i, a.score or 0, a.title[:60], a.source_name
        )

    total_collected = len(articles)

    # 4. HTML生成（--save-html オプション）
    if args.save_html:
        from email_sender import build_html, build_empty_html
        from datetime import datetime, timezone
        now = datetime.now(tz=timezone.utc)
        if filtered:
            keywords = config.get("interest_keywords", [])
            html = build_html(filtered, keywords, config, now, total_collected)
        else:
            html = build_empty_html(config, now, total_collected)
        out_path = Path(args.save_html)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(html, encoding="utf-8")
        logger.info("HTMLを保存しました: %s", out_path)

    # 5. メール送信
    logger.info("--- メール送信フェーズ ---")
    if args.dry_run:
        logger.info("[DRY RUN] メール送信をスキップしました")
    else:
        deliver(filtered, config, total_collected)

    logger.info("=" * 50)
    logger.info("完了")
    logger.info("=" * 50)


if __name__ == "__main__":
    main()
