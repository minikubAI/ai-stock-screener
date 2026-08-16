"""
ニュース取得＋センチメント分析

Yahoo Finance の RSS と yfinance の news から
各銘柄のニュースを取得し、タイトルのポジネガを
キーワードベースで簡易スコアリングしてDBに保存。

使い方:
  python src/fetch_news.py
"""

import sqlite3
import os
import yaml
import time
import requests
import json
import re
from datetime import datetime, timedelta
from typing import Optional
import xml.etree.ElementTree as ET

def get_config():
    config_path = os.path.join(os.path.dirname(__file__), '..', 'config', 'settings.yaml')
    with open(config_path, 'r') as f:
        return yaml.safe_load(f)

def get_db():
    config = get_config()
    db_path = os.path.join(os.path.dirname(__file__), '..', config['database']['path'])
    return sqlite3.connect(db_path)


# ポジティブ・ネガティブキーワード
POSITIVE_WORDS = [
    '増収', '増益', '最高益', '過去最高', '上方修正', '増配', '自社株買',
    '好調', '黒字', '黒字転換', '業績向上', '売上増', '利益増',
    '新規受注', '契約締結', '事業拡大', '海外展開',
    'record', 'profit', 'growth', 'upgrade', 'beat', 'raise',
]
NEGATIVE_WORDS = [
    '減収', '減益', '赤字', '下方修正', '減配', '業績悪化', '損失',
    '赤字転落', '売上減', '利益減', '不正', 'リコール', '訴訟',
    '倒産', '破綻', '上場廃止', '調査',
    'loss', 'decline', 'cut', 'miss', 'downgrade', 'recall', 'fraud',
]


def sentiment_score(text: str) -> float:
    """
    キーワードベースの簡易センチメントスコア
    -1.0（ネガティブ）〜 +1.0（ポジティブ）
    """
    text_lower = text.lower()
    pos = sum(1 for w in POSITIVE_WORDS if w in text_lower)
    neg = sum(1 for w in NEGATIVE_WORDS if w in text_lower)
    total = pos + neg
    if total == 0:
        return 0.0
    return round((pos - neg) / total, 3)


def ensure_news_table(conn):
    """news テーブルがなければ作成"""
    conn.execute('''
        CREATE TABLE IF NOT EXISTS news (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL,
            published_at TEXT,
            title TEXT,
            url TEXT,
            sentiment REAL,
            fetched_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(ticker, url)
        )
    ''')
    conn.commit()


def fetch_news_for_ticker(ticker: str, company_name: str = '') -> list[dict]:
    """Google News RSS でニュースを取得"""
    try:
        query = f"{company_name or ticker} {ticker} 株"
        url = f"https://news.google.com/rss/search?q={requests.utils.quote(query)}&hl=ja&gl=JP&ceid=JP:ja"
        resp = requests.get(url, timeout=15, headers={'User-Agent': 'Mozilla/5.0'})
        if resp.status_code != 200:
            return []
        root = ET.fromstring(resp.content)
        results = []
        for item in root.findall('.//item')[:5]:
            title = item.findtext('title', '').strip()
            link  = item.findtext('link', '').strip()
            pub   = item.findtext('pubDate', '')
            try:
                from email.utils import parsedate_to_datetime
                pub_str = parsedate_to_datetime(pub).isoformat() if pub else None
            except Exception:
                pub_str = None
            if not title:
                continue
            results.append({
                'title': title,
                'url': link,
                'published_at': pub_str,
                'sentiment': sentiment_score(title),
            })
        return results
    except Exception:
        return []


def run():
    conn = get_db()
    ensure_news_table(conn)
    c = conn.cursor()

    # スクリーニング上位銘柄を対象に
    c.execute('''
        SELECT DISTINCT sr.ticker, co.name
        FROM screening_results sr
        JOIN companies co ON sr.ticker = co.ticker
        WHERE sr.screened_at = (SELECT MAX(screened_at) FROM screening_results)
        ORDER BY sr.rank
        LIMIT 50
    ''')
    targets = c.fetchall()

    if not targets:
        # フォールバック: 全銘柄から最新100件
        c.execute("SELECT ticker, name FROM companies LIMIT 100")
        targets = c.fetchall()

    print(f"📰 ニュース取得開始（{len(targets)}銘柄）")
    print("=" * 50)

    total_news = 0
    for ticker, name in targets:
        articles = fetch_news_for_ticker(ticker, name)
        if not articles:
            continue

        saved = 0
        for a in articles:
            try:
                c.execute('''
                    INSERT OR IGNORE INTO news
                    (ticker, published_at, title, url, sentiment)
                    VALUES (?, ?, ?, ?, ?)
                ''', (ticker, a['published_at'], a['title'], a['url'], a['sentiment']))
                if c.rowcount:
                    saved += 1
            except Exception:
                continue

        if saved:
            avg_sent = sum(a['sentiment'] for a in articles) / len(articles)
            sentiment_str = "😊" if avg_sent > 0.1 else "😐" if avg_sent > -0.1 else "😟"
            print(f"  {ticker} {name[:15]}: {saved}件 {sentiment_str} (avg={avg_sent:+.2f})")
            total_news += saved

        time.sleep(0.2)

    conn.commit()

    # センチメントサマリー
    c.execute('''
        SELECT ticker, AVG(sentiment) as avg_s, COUNT(*) as cnt
        FROM news
        WHERE published_at >= ?
        GROUP BY ticker
        ORDER BY avg_s DESC
        LIMIT 10
    ''', ((datetime.now() - timedelta(days=7)).isoformat(),))
    rows = c.fetchall()

    print(f"\n{'=' * 50}")
    print(f"✅ 完了: {total_news}件のニュースを保存")
    if rows:
        print("\n📊 直近7日 ポジティブ銘柄 TOP10:")
        for ticker, avg_s, cnt in rows:
            bar = "█" * int(abs(avg_s) * 10)
            sign = "+" if avg_s >= 0 else "-"
            print(f"  {ticker}: {sign}{abs(avg_s):.2f} {bar} ({cnt}件)")

    conn.close()


if __name__ == '__main__':
    run()
