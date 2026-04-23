"""
フィルタリングモジュール
1. キーワード一次フィルタ（高速）
2. キーワードマッチ数によるスコアリング（★1〜5、API不要）
3. Gemini による日本語要約付加（オプション・失敗時は英語原文にフォールバック）
"""

from __future__ import annotations

import logging
import os
import time

from collect import Article

logger = logging.getLogger(__name__)


def _groups_to_keywords(keyword_groups: list[dict]) -> list[str]:
    """keyword_groups から重複なしのキーワードリストを返す"""
    seen: set[str] = set()
    result: list[str] = []
    for group in keyword_groups:
        for kw in group.get("keywords", []):
            if kw not in seen:
                seen.add(kw)
                result.append(kw)
    return result


def _detect_matched_groups(article: Article, keyword_groups: list[dict]) -> list[str]:
    """記事がマッチするグループ名のリストを返す"""
    target = (article.title + " " + article.summary).lower()
    matched: list[str] = []
    for group in keyword_groups:
        lower_kws = [kw.lower() for kw in group.get("keywords", [])]
        if any(kw in target for kw in lower_kws):
            matched.append(group["name"])
    return matched


def keyword_filter(articles: list[Article], keywords: list[str]) -> list[Article]:
    """
    タイトルと要約にキーワードが1つでも含まれる特許だけを残す
    大文字小文字は区別しない
    """
    lower_keywords = [kw.lower() for kw in keywords]
    passed: list[Article] = []

    for article in articles:
        target = (article.title + " " + article.summary).lower()
        if any(kw in target for kw in lower_keywords):
            passed.append(article)

    logger.info("キーワードフィルタ: %d → %d 件", len(articles), len(passed))
    return passed


def _keyword_score(article: Article, keywords: list[str]) -> dict:
    """
    タイトル・要約にマッチするキーワード数でスコアを決める
    タイトルマッチは重み2、要約マッチは重み1
    """
    lower_keywords = [kw.lower() for kw in keywords]
    title_lower = article.title.lower()
    summary_lower = article.summary.lower()

    matched_kws: list[str] = []
    weighted_count = 0

    for kw in lower_keywords:
        in_title = kw in title_lower
        in_summary = kw in summary_lower
        if in_title or in_summary:
            matched_kws.append(kw)
            weighted_count += (2 if in_title else 0) + (1 if in_summary else 0)

    if weighted_count >= 6:
        score = 5
    elif weighted_count >= 4:
        score = 4
    elif weighted_count >= 2:
        score = 3
    else:
        score = 2

    matched_str = "、".join(matched_kws[:5])
    summary = (article.summary[:200] + "…") if len(article.summary) > 200 else article.summary
    if not summary:
        summary = article.title

    return {
        "relevance_score": score,
        "relevance_reason": f"キーワード {len(matched_kws)} 件マッチ（{matched_str}）",
        "summary": summary,
    }


def ai_score_filter(
    articles: list[Article],
    keywords: list[str],
    min_score: int = 3,
    min_score_jp: int = 3,
    min_score_world: int = 4,
) -> list[Article]:
    """
    キーワードマッチ数でスコアリングし、国別閾値以上の特許をスコア順に返す
    JP特許: min_score_jp 以上 / それ以外: min_score_world 以上
    """
    passed: list[Article] = []
    logger.info("キーワードスコアリング中 (%d 件)...", len(articles))
    logger.info("スコア閾値: JP=★%d以上 / 世界=★%d以上", min_score_jp, min_score_world)

    for i, article in enumerate(articles, 1):
        result = _keyword_score(article, keywords)
        score = result["relevance_score"]
        article.score = score
        article.score_reason = result["relevance_reason"]
        article.ai_summary = result["summary"]

        is_jp = (article.patent_number or "").startswith("JP")
        threshold = min_score_jp if is_jp else min_score_world
        country_label = "JP" if is_jp else "世界"
        status = "✓" if score >= threshold else "✗"

        logger.info(
            "  [%d/%d] %s ★%d [%s] %s",
            i, len(articles), status, score, country_label, article.title[:50],
        )

        if score >= threshold:
            passed.append(article)

    passed.sort(key=lambda a: a.score or 0, reverse=True)
    logger.info("スコアリング完了: %d 件通過 / %d 件", len(passed), len(articles))
    return passed


def _build_summary_prompt(article: Article, keywords: list[str]) -> str:
    keyword_str = "、".join(keywords[:8])
    assignees = "、".join(article.authors[:3]) if article.authors else "不明"
    ipc_str = f"IPC: {article.ipc}" if article.ipc else ""
    patent_num = f"特許番号: US{article.patent_number}" if article.patent_number else ""

    return f"""あなたは特許情報を研究者・エンジニアに届けるキュレーターです。
以下の特許について、技術者が「読みたい」と思えるような紹介文を日本語で書いてください。

## 書き方のルール
- 全体で4〜6文（読むのに15〜20秒程度）
- 1文目：この特許が「{keyword_str}」とどう関わるかを具体的に示す
- 2〜3文目：権利範囲（何を特許として主張しているか）・技術的な新規性を端的に述べる（数値・材料名・手法名があれば積極的に使う）
- 4文目：出願人（{assignees}）のビジネス的・技術的意図や、業界への影響を一言添える
- 「〜です。〜ます。」調で書く。箇条書き・見出しは使わない

## 特許情報
タイトル: {article.title}
出願人: {assignees}
{patent_num}
{ipc_str}
概要: {article.summary[:800]}

紹介文のみ出力してください。"""


def add_ai_summaries(articles: list[Article], config: dict) -> None:
    """
    Gemini で日本語要約を生成して article.ai_summary に上書きする。
    GEMINI_API_KEY が未設定またはエラー時はスキップ（英語原文のまま）。
    """
    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        logger.info("GEMINI_API_KEY が未設定のため日本語要約をスキップします")
        return

    try:
        from google import genai
        from google.genai import types
    except ImportError:
        logger.warning("google-genai がインストールされていません。日本語要約をスキップします")
        return

    model_name = config.get("gemini_model", "gemini-2.5-flash")
    keyword_groups = config.get("keyword_groups", [])
    keywords = _groups_to_keywords(keyword_groups) if keyword_groups else config.get("interest_keywords", [])

    try:
        client = genai.Client(api_key=api_key)
    except Exception as exc:
        logger.warning("Gemini クライアント初期化失敗: %s", exc)
        return

    logger.info("Gemini 日本語要約中 (%d 件)...", len(articles))

    for i, article in enumerate(articles, 1):
        prompt = _build_summary_prompt(article, keywords)
        success = False

        for attempt in range(3):
            try:
                response = client.models.generate_content(
                    model=model_name,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        temperature=0.5,
                        max_output_tokens=600,
                        thinking_config=types.ThinkingConfig(thinking_budget=0),
                    ),
                )
                japanese_summary = response.text.strip()
                if japanese_summary:
                    article.ai_summary = japanese_summary
                    success = True
                    break
            except Exception as exc:
                logger.warning("要約失敗 (attempt %d/3) %s: %s", attempt + 1, article.title[:40], exc)
                if attempt < 2:
                    time.sleep(2 ** attempt)

        status = "✓" if success else "✗（英語原文）"
        logger.info("  [%d/%d] %s %s", i, len(articles), status, article.title[:50])
        time.sleep(0.5)


def run_filter(articles: list[Article], config: dict) -> list[Article]:
    """キーワードフィルタ → スコアリングの2段階フィルタを実行する"""
    keyword_groups = config.get("keyword_groups", [])
    keywords = _groups_to_keywords(keyword_groups) if keyword_groups else config.get("interest_keywords", [])
    delivery = config.get("delivery", {})
    min_score_jp = delivery.get("min_score_jp", 3)
    min_score_world = delivery.get("min_score_world", 4)

    step1 = keyword_filter(articles, keywords)
    if not step1:
        logger.info("キーワードフィルタで0件になりました")
        return []

    step2 = ai_score_filter(
        step1, keywords,
        min_score_jp=min_score_jp,
        min_score_world=min_score_world,
    )

    if step2 and keyword_groups:
        for article in step2:
            article.matched_groups = _detect_matched_groups(article, keyword_groups)

    if step2:
        add_ai_summaries(step2, config)

    return step2
