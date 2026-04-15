# 特許情報ダイジェスト 自動配信ツール

USPTO PatentsView API からキーワードで特許を収集し、毎週月曜に HTML メールで配信する。

## 構成

```
patent-digest-tool/
├── config.yaml              ← キーワード・配信設定（ここだけ編集すれば OK）
├── requirements.txt
├── src/
│   ├── main.py              ← エントリポイント
│   ├── collect.py           ← PatentsView API で特許収集
│   ├── filter.py            ← キーワードフィルタ + Gemini 日本語要約
│   └── email_sender.py      ← HTML メール生成・送信
└── .github/workflows/
    └── weekly.yml           ← GitHub Actions 週次自動実行
```

## セットアップ

### 1. 依存インストール

```bash
pip install -r requirements.txt
```

### 2. EPO OPS API キーを取得

1. [developers.epo.org](https://developers.epo.org/) にアクセスしてアカウント登録
2. ログイン後「My Apps」→「Create Application」
3. 生成された **Consumer Key** と **Consumer Secret** をメモ

### 3. GitHub Secrets の設定

| シークレット名 | 内容 |
|---|---|
| `EPO_CONSUMER_KEY` | EPO OPS Consumer Key |
| `EPO_CONSUMER_SECRET` | EPO OPS Consumer Secret |
| `GMAIL_ADDRESS` | 送信元 Gmail アドレス |
| `GMAIL_APP_PASSWORD` | Gmail アプリパスワード |
| `TO_ADDRESSES` | 宛先（カンマ区切りで複数可） |
| `GEMINI_API_KEY` | Gemini API キー（省略可・なければ英語原文） |

### 4. キーワード設定

`config.yaml` の `interest_keywords` を編集する。英語のみ対応（EPO OPS CQL 検索）。

## 実行方法

```bash
# 環境変数をセット（ローカルテスト時）
$env:EPO_CONSUMER_KEY="your_key"
$env:EPO_CONSUMER_SECRET="your_secret"

# ドライラン（メール送信なし・内容確認のみ）
python src/main.py --config config.yaml --dry-run

# HTML をファイルに保存して確認
python src/main.py --config config.yaml --save-html output/digest.html

# 本番実行
python src/main.py --config config.yaml
```

## データソース

- **EPO Open Patent Services (OPS)** (`https://ops.epo.org/3.2/`)
  - 無料（週 4GB まで）・OAuth 登録必要（無料）
  - 米国・欧州・日本を含む世界中の特許をカバー
  - CQL キーワード検索（タイトル・概要）・IPC分類で絞り込み可能
  - 登録先: https://developers.epo.org/

## 自動実行

GitHub Actions で毎週火曜 23:00 UTC（水曜 08:00 JST）に自動実行される。
手動実行は Actions タブの `patent-digest-weekly` から `workflow_dispatch` で可能。
