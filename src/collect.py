"""
情報収集モジュール
EPO Open Patent Services (OPS) API からキーワード検索で特許を収集する
https://ops.epo.org/

認証: OAuth2 Client Credentials
  EPO_CONSUMER_KEY / EPO_CONSUMER_SECRET 環境変数に設定する
  取得先: https://developers.epo.org/
"""

from __future__ import annotations

import base64
import json
import logging
import os
import ssl
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

import certifi
import yaml

logger = logging.getLogger(__name__)

JST = timezone(timedelta(hours=9))

OPS_AUTH_URL = "https://ops.epo.org/3.2/auth/accesstoken"
OPS_SEARCH_URL = "https://ops.epo.org/3.2/rest-services/published-data/search/biblio"

# OPS XML 名前空間
NS = {
    "ops": "http://ops.epo.org/3.2",
    "": "http://www.epo.org/exchange",
    "epo": "http://www.epo.org/exchange",
}


@dataclass
class Article:
    """収集した特許の共通データ型"""
    source_type: str          # "patent"
    source_name: str          # "EPO OPS"
    title: str
    summary: str              # abstract（取得できた場合）
    url: str                  # Espacenet へのリンク
    published: Optional[datetime] = None
    authors: list[str] = field(default_factory=list)   # 出願人（applicant）
    patent_number: Optional[str] = None
    ipc: Optional[str] = None  # IPC分類
    score: Optional[int] = None
    score_reason: Optional[str] = None
    ai_summary: Optional[str] = None
    matched_groups: list[str] = field(default_factory=list)


def load_config(config_path: str = "config.yaml") -> dict:
    with open(config_path, encoding="utf-8") as f:
        return yaml.safe_load(f)


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


# --- OAuth トークン管理 ---

_token_cache: dict = {"token": None, "expires_at": 0.0}


def _get_ops_token(key: str, secret: str) -> str:
    """OPS OAuth2 アクセストークンを取得する（キャッシュ付き、有効期限18分）"""
    now = time.time()
    if _token_cache["token"] and now < _token_cache["expires_at"]:
        return _token_cache["token"]

    credentials = base64.b64encode(f"{key}:{secret}".encode()).decode()
    ssl_ctx = ssl.create_default_context(cafile=certifi.where())
    req = urllib.request.Request(
        OPS_AUTH_URL,
        data=b"grant_type=client_credentials",
        headers={
            "Authorization": f"Basic {credentials}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30, context=ssl_ctx) as r:
        data = json.loads(r.read())

    token = data["access_token"]
    expires_in = int(data.get("expires_in", 1200))
    _token_cache["token"] = token
    _token_cache["expires_at"] = now + expires_in - 120  # 2分余裕を持つ
    logger.info("OPS トークン取得完了（有効期限 %d 秒）", expires_in)
    return token


# --- CQL クエリ構築 ---

def _build_cql(keywords: list[str], since: datetime, until: datetime, search_fields: str = "ti,cl") -> str:
    """
    EPO CQL クエリを構築する。
    search_fields:
      "ti"     = タイトルのみ
      "ti,cl"  = タイトル＋請求項（デフォルト）
      "ti,ab"  = タイトル＋概要
      "ti,ab,cl" = タイトル＋概要＋請求項
    """
    since_str = since.strftime("%Y%m%d")
    until_str = until.strftime("%Y%m%d")

    fields = [f.strip() for f in search_fields.split(",")]
    kw_clauses = []
    for kw in keywords[:10]:
        field_parts = [f'{f} all "{kw}"' for f in fields]
        if len(field_parts) == 1:
            kw_clauses.append(field_parts[0])
        else:
            kw_clauses.append(f'({" OR ".join(field_parts)})')

    kw_query = " OR ".join(kw_clauses)
    return f"({kw_query}) AND pd within \"{since_str},{until_str}\""


# --- OPS 検索・パース ---

def _search_ops(cql: str, token: str, range_begin: int, range_end: int) -> bytes:
    """OPS biblio 検索を実行し、レスポンスの生XMLを返す"""
    params = urllib.parse.urlencode({"q": cql})
    url = f"{OPS_SEARCH_URL}?{params}"
    ssl_ctx = ssl.create_default_context(cafile=certifi.where())
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/xml",
            "X-OPS-Range": f"{range_begin}-{range_end}",
        },
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=30, context=ssl_ctx) as r:
        return r.read()


def _parse_text(el: Optional[ET.Element]) -> str:
    if el is None:
        return ""
    return (el.text or "").strip()


def _parse_ops_xml(xml_data: bytes) -> list[Article]:
    """OPS biblio レスポンスのXMLをパースして Article リストを返す"""
    try:
        root = ET.fromstring(xml_data)
    except ET.ParseError as e:
        logger.warning("OPS XML パースエラー: %s", e)
        return []

    articles: list[Article] = []

    # exchange-document を列挙（名前空間は空文字列）
    for xdoc in root.iter("{http://www.epo.org/exchange}exchange-document"):
        country = xdoc.get("country", "")
        doc_number = xdoc.get("doc-number", "")
        kind = xdoc.get("kind", "")

        bib = xdoc.find("{http://www.epo.org/exchange}bibliographic-data")
        if bib is None:
            continue

        # タイトル（英語優先）
        title = ""
        for t in bib.iter("{http://www.epo.org/exchange}invention-title"):
            if t.get("lang", "en") == "en":
                title = (t.text or "").strip()
                break
        if not title:
            t_any = bib.find(".//{http://www.epo.org/exchange}invention-title")
            title = _parse_text(t_any)

        if not title:
            continue

        # 公開日
        pub_date: Optional[datetime] = None
        for doc_id in bib.iter("{http://www.epo.org/exchange}document-id"):
            date_el = doc_id.find("{http://www.epo.org/exchange}date")
            date_str = _parse_text(date_el)
            if len(date_str) == 8:
                try:
                    pub_date = datetime.strptime(date_str, "%Y%m%d").replace(
                        tzinfo=timezone.utc
                    )
                    break
                except ValueError:
                    pass

        # 出願人
        applicants: list[str] = []
        for applicant in bib.iter("{http://www.epo.org/exchange}applicant"):
            name_el = applicant.find(
                ".//{http://www.epo.org/exchange}name"
            )
            name = _parse_text(name_el)
            if name and name not in applicants:
                applicants.append(name)

        # IPC分類
        ipc_codes: list[str] = []
        for cls in bib.iter("{http://www.epo.org/exchange}classification-ipc"):
            sym = cls.find("{http://www.epo.org/exchange}symbol")
            code = _parse_text(sym)
            if code and code not in ipc_codes:
                ipc_codes.append(code)
        for cls in bib.iter("{http://www.epo.org/exchange}patent-classification"):
            sym = cls.find("{http://www.epo.org/exchange}classification-symbol")
            code = _parse_text(sym)
            if code and code not in ipc_codes:
                ipc_codes.append(code[:8])

        ipc_str = "、".join(ipc_codes[:3]) if ipc_codes else None

        # Espacenet URL
        patent_id = f"{country}{doc_number}{kind}"
        url = (
            f"https://worldwide.espacenet.com/patent/search/family/"
            f"publication/{country}.{doc_number}.{kind}/en"
        )

        articles.append(Article(
            source_type="patent",
            source_name="EPO OPS",
            title=title,
            summary="",        # biblio では abstract なし（filter.py で title を使用）
            url=url,
            published=pub_date,
            authors=applicants[:5],
            patent_number=patent_id,
            ipc=ipc_str,
        ))

    return articles


def collect_all(config: dict) -> list[Article]:
    """EPO OPS API からキーワード検索で特許を収集して返す"""
    epo_key = os.environ.get("EPO_CONSUMER_KEY", "")
    epo_secret = os.environ.get("EPO_CONSUMER_SECRET", "")

    if not epo_key or not epo_secret:
        logger.error(
            "EPO_CONSUMER_KEY / EPO_CONSUMER_SECRET が設定されていません。"
            "https://developers.epo.org/ でアプリを登録してください。"
        )
        return []

    days_back = config["delivery"].get("days_back", 7)
    since = datetime.now(tz=timezone.utc) - timedelta(days=days_back)
    until = datetime.now(tz=timezone.utc)
    logger.info("収集期間: 過去 %d 日（%s 〜 %s）",
                days_back, since.strftime("%Y-%m-%d"), until.strftime("%Y-%m-%d"))

    keyword_groups = config.get("keyword_groups", [])
    keywords = _groups_to_keywords(keyword_groups) if keyword_groups else config.get("interest_keywords", [])
    max_results = config["delivery"].get("max_patents", 50)

    # トークン取得
    try:
        token = _get_ops_token(epo_key, epo_secret)
    except Exception as e:
        logger.error("OPS トークン取得失敗: %s", e)
        return []

    search_fields = config["delivery"].get("search_fields", "ti,cl")
    country_filter = config["delivery"].get("country_filter", "")  # 例: "JP"
    cql = _build_cql(keywords, since, until, search_fields=search_fields)
    logger.info("検索フィールド: %s", search_fields)
    if country_filter:
        logger.info("国フィルタ: %s", country_filter)
    logger.info("CQL クエリ: %s", cql)

    # ページング（OPS は 1 リクエスト最大 100 件）
    page_size = min(max_results, 100)
    all_articles: list[Article] = []
    seen: set[str] = set()

    try:
        xml_data = _search_ops(cql, token, 1, page_size)
        fetched = _parse_ops_xml(xml_data)

        for a in fetched:
            # 国フィルタ（Python側で処理）
            if country_filter and a.patent_number:
                if not a.patent_number.startswith(country_filter):
                    continue
            key = a.patent_number or a.url
            if key not in seen:
                seen.add(key)
                all_articles.append(a)

        logger.info("収集合計: %d 件（重複除き: %d 件）",
                    len(fetched), len(all_articles))

    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")[:300]
        logger.error("OPS API エラー %d: %s | %s", e.code, e.reason, body)
    except Exception as e:
        logger.error("OPS API エラー: %s", e)

    time.sleep(1.0)
    return all_articles
