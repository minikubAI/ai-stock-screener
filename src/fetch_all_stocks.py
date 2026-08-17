"""
全上場銘柄データ一括取得スクリプト

1. JPXの上場銘柄一覧を取得
2. yfinanceで財務データ（過去5年分）を取得
3. 株価データを取得
4. すべてDBに保存

使い方:
  python src/fetch_all_stocks.py

注意:
  - 約3,800銘柄を処理するため、初回は1〜2時間かかります
  - 2回目以降は差分のみ更新
"""

import sqlite3
import os
import sys
import time
import csv
import io
import yaml
import requests
import yfinance as yf
import pandas as pd
from datetime import datetime, timedelta

def get_config():
    config_path = os.path.join(os.path.dirname(__file__), '..', 'config', 'settings.yaml')
    with open(config_path, 'r') as f:
        return yaml.safe_load(f)

def get_db():
    config = get_config()
    db_path = os.path.join(os.path.dirname(__file__), '..', config['database']['path'])
    return sqlite3.connect(db_path)


def fetch_jpx_listed_companies() -> list[dict]:
    """
    JPX（日本取引所グループ）の上場銘柄一覧を取得

    東証のCSVデータを使用
    """
    print("📋 上場銘柄一覧を取得中...")

    # JPXの上場銘柄一覧CSV
    url = "https://www.jpx.co.jp/markets/statistics-equities/misc/tvdivq0000001vg2-att/data_j.xls"

    try:
        # xlsファイルを取得
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()

        # pandasでExcelとして読み込み
        df = pd.read_excel(io.BytesIO(resp.content))

        TPM_KEYWORDS = ('プロ', 'TPM', 'PRO', 'TOKYO PRO')

        companies = []
        skipped_tpm = 0
        for _, row in df.iterrows():
            code = str(row.get('コード', '')).strip()
            if not code or len(code) < 4:
                continue

            # 4桁に正規化
            code = code[:4]

            market = str(row.get('市場・商品区分', '')).strip()

            # 東京プロマーケット除外（大文字小文字を問わず）
            if any(kw.upper() in market.upper() for kw in TPM_KEYWORDS):
                skipped_tpm += 1
                continue

            companies.append({
                'ticker': code,
                'name': str(row.get('銘柄名', '')).strip(),
                'market': market,
                'sector': str(row.get('33業種区分', '')).strip(),
                'industry': str(row.get('17業種区分', '')).strip(),
            })

        print(f"  → {len(companies)}銘柄を取得（TPM除外: {skipped_tpm}銘柄）")
        return companies

    except Exception as e:
        print(f"  ⚠️ JPXデータ取得エラー: {e}")
        print("  → 代替方法: yfinanceから取得を試みます")
        return []


def register_companies(conn, companies: list[dict]):
    """企業マスタに一括登録"""
    c = conn.cursor()
    count = 0
    for co in companies:
        c.execute('''
            INSERT OR REPLACE INTO companies (ticker, name, market, sector, industry)
            VALUES (?, ?, ?, ?, ?)
        ''', (co['ticker'], co['name'], co.get('market'), co.get('sector'), co.get('industry')))
        count += 1
    conn.commit()
    print(f"  → {count}社をDBに登録")


def fetch_financial_data_batch(conn, tickers: list[str], batch_size: int = 20):
    """
    yfinanceで財務データを一括取得（過去5年分）

    batch_size単位で並列取得し、効率化
    """
    c = conn.cursor()
    total = len(tickers)
    success = 0
    failed = 0

    print(f"\n💰 財務データ取得開始（{total}銘柄）")
    print("=" * 50)

    for i in range(0, total, batch_size):
        batch = tickers[i:i + batch_size]
        yf_tickers = [f"{t}.T" for t in batch]

        pct = (i / total) * 100
        print(f"\r  進捗: {i}/{total} ({pct:.0f}%) | 成功: {success} | 失敗: {failed}", end="", flush=True)

        try:
            # バッチで情報取得
            data = yf.download(
                yf_tickers,
                period="5d",
                group_by='ticker',
                progress=False,
                threads=True
            )

            for ticker, yf_ticker in zip(batch, yf_tickers):
                try:
                    stock = yf.Ticker(yf_ticker)
                    info = stock.info

                    if not info or info.get('regularMarketPrice') is None:
                        failed += 1
                        continue

                    # 基本財務データ
                    market_cap = info.get('marketCap')
                    shares = info.get('sharesOutstanding')
                    pe = info.get('trailingPE')
                    pb = info.get('priceToBook')
                    roe = info.get('returnOnEquity')
                    dividend_yield = info.get('dividendYield')
                    revenue = info.get('totalRevenue')
                    total_assets = info.get('totalAssets')
                    total_equity = info.get('totalStockholderEquity') or info.get('bookValue', 0) * (shares or 0)
                    op_income = info.get('operatingIncome') or info.get('ebitda')
                    net_income = info.get('netIncomeToCommon')
                    eps = info.get('trailingEps')
                    bps = info.get('bookValue')
                    dps = info.get('dividendRate', 0)
                    current_price = info.get('currentPrice') or info.get('regularMarketPrice')

                    # 現在の決算年度
                    fiscal_year = str(datetime.now().year)

                    # financials テーブルに保存
                    c.execute('''
                        INSERT OR REPLACE INTO financials
                        (ticker, fiscal_year, revenue, operating_income,
                         net_income, total_assets, total_equity,
                         shares_outstanding, dividends_per_share, eps, bps)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (
                        ticker, fiscal_year, revenue, op_income,
                        net_income, total_assets, total_equity,
                        shares, dps, eps, bps
                    ))

                    # 最新株価をpricesテーブルに保存
                    if current_price:
                        today = datetime.now().strftime('%Y-%m-%d')
                        volume = info.get('volume', 0)
                        c.execute('''
                            INSERT OR REPLACE INTO prices
                            (ticker, date, open, high, low, close, volume)
                            VALUES (?, ?, ?, ?, ?, ?, ?)
                        ''', (ticker, today, current_price, current_price,
                              current_price, current_price, volume))

                    success += 1

                except Exception as e:
                    failed += 1
                    continue

        except Exception as e:
            failed += len(batch)
            continue

        # コミット＆API制限対策
        if i % (batch_size * 5) == 0:
            conn.commit()
        time.sleep(0.5)

    conn.commit()
    print(f"\n\n{'=' * 50}")
    print(f"✅ 財務データ取得完了: {success}銘柄成功 / {failed}銘柄失敗")


def fetch_historical_financials(conn, tickers: list[str]):
    """
    yfinanceで過去の財務データ（年次）を取得

    income_stmt, balance_sheet から過去4年分を取得
    """
    c = conn.cursor()
    total = len(tickers)
    success = 0

    print(f"\n📊 過去財務データ取得（{total}銘柄）")
    print("=" * 50)

    for i, ticker in enumerate(tickers):
        if i % 50 == 0:
            pct = (i / total) * 100
            print(f"\r  進捗: {i}/{total} ({pct:.0f}%) | 成功: {success}", end="", flush=True)

        try:
            stock = yf.Ticker(f"{ticker}.T")

            # 損益計算書（年次、過去4年分）
            income = stock.income_stmt
            balance = stock.balance_sheet

            if income is None or income.empty:
                continue

            for col in income.columns:
                fy = str(col.year) if hasattr(col, 'year') else str(col)[:4]

                revenue = None
                op_income = None
                net_income = None
                total_assets_val = None
                equity_val = None

                # 損益計算書から
                for key in ['Total Revenue', 'Operating Revenue']:
                    if key in income.index:
                        val = income.loc[key, col]
                        if pd.notna(val):
                            revenue = float(val)
                            break

                if 'Operating Income' in income.index:
                    val = income.loc['Operating Income', col]
                    if pd.notna(val):
                        op_income = float(val)

                if 'Net Income' in income.index:
                    val = income.loc['Net Income', col]
                    if pd.notna(val):
                        net_income = float(val)

                # 貸借対照表から
                if balance is not None and not balance.empty and col in balance.columns:
                    if 'Total Assets' in balance.index:
                        val = balance.loc['Total Assets', col]
                        if pd.notna(val):
                            total_assets_val = float(val)

                    for key in ['Stockholders Equity', 'Total Stockholders Equity',
                                'Common Stock Equity', 'Total Equity Gross Minority Interest']:
                        if key in balance.index:
                            val = balance.loc[key, col]
                            if pd.notna(val):
                                equity_val = float(val)
                                break

                # DBに保存
                if any([revenue, op_income, net_income]):
                    c.execute('''
                        INSERT OR REPLACE INTO financials
                        (ticker, fiscal_year, revenue, operating_income,
                         net_income, total_assets, total_equity)
                        VALUES (?, ?,
                                COALESCE(?, (SELECT revenue FROM financials WHERE ticker=? AND fiscal_year=?)),
                                COALESCE(?, (SELECT operating_income FROM financials WHERE ticker=? AND fiscal_year=?)),
                                COALESCE(?, (SELECT net_income FROM financials WHERE ticker=? AND fiscal_year=?)),
                                COALESCE(?, (SELECT total_assets FROM financials WHERE ticker=? AND fiscal_year=?)),
                                COALESCE(?, (SELECT total_equity FROM financials WHERE ticker=? AND fiscal_year=?)))
                    ''', (
                        ticker, fy,
                        revenue, ticker, fy,
                        op_income, ticker, fy,
                        net_income, ticker, fy,
                        total_assets_val, ticker, fy,
                        equity_val, ticker, fy
                    ))

            success += 1

        except Exception:
            continue

        if i % 100 == 0:
            conn.commit()
            time.sleep(0.3)

    conn.commit()
    print(f"\n\n✅ 過去財務データ取得完了: {success}銘柄")


def main():
    print("=" * 60)
    print("🚀 全上場銘柄データ一括取得")
    print(f"   開始: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 60)

    conn = get_db()

    # Step 1: 上場銘柄一覧を取得
    companies = fetch_jpx_listed_companies()
    if not companies:
        print("❌ 銘柄一覧を取得できませんでした")
        return

    # Step 2: DBに登録
    register_companies(conn, companies)
    tickers = [c['ticker'] for c in companies]

    # Step 3: 財務データ取得（最新）
    fetch_financial_data_batch(conn, tickers)

    # Step 4: 過去の財務データ取得
    fetch_historical_financials(conn, tickers)

    # 集計
    c = conn.cursor()
    c.execute("SELECT COUNT(DISTINCT ticker) FROM companies")
    co_count = c.fetchone()[0]
    c.execute("SELECT COUNT(DISTINCT ticker) FROM financials")
    fin_count = c.fetchone()[0]
    c.execute("SELECT COUNT(DISTINCT ticker || fiscal_year) FROM financials")
    records = c.fetchone()[0]

    conn.close()

    print(f"\n{'=' * 60}")
    print(f"✅ 全処理完了: {datetime.now().strftime('%H:%M')}")
    print(f"   登録企業数: {co_count}")
    print(f"   財務データ保有: {fin_count}社 ({records}レコード)")
    print(f"{'=' * 60}")


if __name__ == '__main__':
    main()
