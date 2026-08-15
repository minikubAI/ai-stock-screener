# 📈 Stock Screener - 日本株自動スクリーニングシステム

## 概要
EDINET（金融庁）のIR情報と株価データを自動取得し、
独自ロジックでスクリーニング → ミニ株購入候補を選出するシステム。

## セットアップ

```bash
# 1. Python仮想環境
python3 -m venv venv
source venv/bin/activate

# 2. 依存パッケージ
pip install -r requirements.txt

# 3. 設定ファイル
cp config/settings.example.yaml config/settings.yaml
# → EDINET APIキーを設定

# 4. DB初期化
python src/init_db.py

# 5. データ取得（初回）
python src/fetch_edinet.py
python src/fetch_prices.py

# 6. スクリーニング実行
python src/screener.py
```

## EDINET APIキーの取得
1. https://disclosure2dl.edinet-fsa.go.jp/ にアクセス
2. 利用者登録（無料）
3. APIキーを取得
4. config/settings.yaml に設定

## 構成
- `src/init_db.py` - DB初期化
- `src/fetch_edinet.py` - EDINET決算データ取得
- `src/fetch_prices.py` - 株価データ取得（Yahoo Finance）
- `src/screener.py` - スクリーニングロジック
- `src/models.py` - データモデル定義
- `config/settings.yaml` - 設定ファイル
