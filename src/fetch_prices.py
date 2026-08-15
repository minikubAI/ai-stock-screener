"""
Yahoo Finance から株価データを取得するスクリプト

日本株は証券コード末尾に .T を付加
（例: 7203.T = トヨタ自動車）
"""

import sqlite3
import os
import yaml
from typing import Optional
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


def fetch_prices_for_all(period: str = "1y"):
    """
    DB登録済みの全銘柄の株価を取得

    period: "1y" = 1年分, "6mo" = 6ヶ月分, "3mo" = 3ヶ月分
    """
    conn = get_db()
    c = conn.cursor()

    # 登録済み銘柄一覧を取得
    c.execute("SELECT ticker, name FROM companies")
    companies = c.fetchall()

    if not companies:
        print("⚠️ 企業データがありません。先に fetch_edinet.py を実行してください")
        return

    print(f"📈 {len(companies)}銘柄の株価を取得します（期間: {period}）")
    print("=" * 50)

    success = 0
    failed = 0

    for ticker, name in companies:
        # 日本株のYahoo Financeティッカー
        yf_ticker = f"{ticker}.T"

        try:
            print(f"  📊 {ticker} {name}...", end=" ")
            stock = yf.Ticker(yf_ticker)
            hist = stock.history(period=period)

            if hist.empty:
                print("❌ データなし")
                failed += 1
                continue

            # DBに保存
            for date, row in hist.iterrows():
                c.execute('''
                    INSERT OR REPLACE INTO prices
                    (ticker, date, open, high, low, close, volume)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', (
                    ticker,
                    date.strftime('%Y-%m-%d'),
                    round(row['Open'], 1),
                    round(row['High'], 1),
                    round(row['Low'], 1),
                    round(row['Close'], 1),
                    int(row['Volume']),
                ))

            success += 1
            print(f"✅ {len(hist)}日分")

        except Exception as e:
            print(f"❌ エラー: {e}")
            failed += 1

    conn.commit()
    conn.close()

    print(f"\n{'=' * 50}")
    print(f"✅ 完了: {success}銘柄成功 / {failed}銘柄失敗")


def fetch_supplemental_financials():
    """
    yfinance の info から不足している財務数値を補完して financials テーブルを更新

    対象: shares_outstanding, eps, bps, dividends_per_share
    """
    conn = get_db()
    c = conn.cursor()

    c.execute("SELECT DISTINCT ticker FROM financials WHERE shares_outstanding IS NULL")
    tickers = [row[0] for row in c.fetchall()]

    if not tickers:
        print("✅ 補完データは不要です")
        conn.close()
        return

    print(f"📊 {len(tickers)}銘柄の補完財務データを yfinance から取得中...")
    print("=" * 50)

    success = 0
    for ticker in tickers:
        yf_ticker = f"{ticker}.T"
        try:
            info = yf.Ticker(yf_ticker).info
            shares = info.get('sharesOutstanding')
            eps    = info.get('trailingEps')
            bps    = info.get('bookValue')
            dps    = info.get('dividendRate')  # 年間配当額

            if shares:
                c.execute('''
                    UPDATE financials
                    SET shares_outstanding = ?,
                        eps = COALESCE(eps, ?),
                        bps = COALESCE(bps, ?),
                        dividends_per_share = COALESCE(dividends_per_share, ?)
                    WHERE ticker = ?
                ''', (shares, eps, bps, dps, ticker))
                print(f"  ✅ {ticker}: shares={shares:,.0f}, eps={eps}, bps={bps}, dps={dps}")
                success += 1
            else:
                print(f"  ⚠️ {ticker}: sharesOutstanding なし")
        except Exception as e:
            print(f"  ❌ {ticker}: {e}")

    conn.commit()
    conn.close()
    print(f"\n{'=' * 50}")
    print(f"✅ 完了: {success}/{len(tickers)}銘柄を補完")


def fetch_single(ticker: str, period: str = "1y") -> pd.DataFrame:
    """単一銘柄の株価取得（ユーティリティ）"""
    yf_ticker = f"{ticker}.T"
    stock = yf.Ticker(yf_ticker)
    return stock.history(period=period)


def get_current_price(ticker: str) -> Optional[float]:
    """現在株価を取得"""
    try:
        yf_ticker = f"{ticker}.T"
        stock = yf.Ticker(yf_ticker)
        info = stock.info
        return info.get('currentPrice') or info.get('regularMarketPrice')
    except Exception:
        return None


if __name__ == '__main__':
    fetch_prices_for_all(period="1y")
    fetch_supplemental_financials()
