"""
業界超過成長率スコア

各企業の成長率を同業種平均と比較し、
業界平均を上回る成長をしている企業を加点。

「業界全体が伸びている中で伸びている」より
「業界が停滞しているのに伸びている」企業を高く評価。

使い方:
  python src/industry_score.py
"""

from __future__ import annotations
import sqlite3
import os
import yaml
import numpy as np
from datetime import datetime

def get_config():
    config_path = os.path.join(os.path.dirname(__file__), '..', 'config', 'settings.yaml')
    with open(config_path, 'r') as f:
        return yaml.safe_load(f)

def get_db():
    config = get_config()
    db_path = os.path.join(os.path.dirname(__file__), '..', config['database']['path'])
    return sqlite3.connect(db_path)


def init_industry_table(conn):
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS industry_scores (
            ticker TEXT PRIMARY KEY,
            sector TEXT,
            company_rev_growth REAL,
            sector_avg_growth REAL,
            excess_growth REAL,
            sector_company_count INTEGER,
            industry_score REAL,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()


def calculate_revenue_growth(conn, ticker) -> float | None:
    """銘柄の直近売上成長率を算出"""
    c = conn.cursor()
    c.execute('''
        SELECT fiscal_year, revenue FROM financials
        WHERE ticker = ? AND revenue IS NOT NULL AND revenue > 0
        ORDER BY fiscal_year DESC LIMIT 2
    ''', (ticker,))
    rows = c.fetchall()
    if len(rows) < 2:
        return None
    current, previous = rows[0][1], rows[1][1]
    if previous <= 0:
        return None
    return (current - previous) / abs(previous) * 100


def calculate_all_industry_scores(conn) -> list[dict]:
    """全銘柄の業界超過成長率を計算"""
    c = conn.cursor()

    # セクター情報のある全銘柄を取得
    c.execute('''
        SELECT DISTINCT c.ticker, c.sector
        FROM companies c
        INNER JOIN financials f ON c.ticker = f.ticker
        WHERE c.sector IS NOT NULL AND c.sector != ''
    ''')
    companies = c.fetchall()

    # 銘柄ごとの成長率を計算
    growth_data = {}  # ticker -> growth_rate
    sector_growth = {}  # sector -> [growth_rates]

    for ticker, sector in companies:
        growth = calculate_revenue_growth(conn, ticker)
        if growth is not None and -200 < growth < 500:  # 異常値を除外
            growth_data[ticker] = {'growth': growth, 'sector': sector}
            if sector not in sector_growth:
                sector_growth[sector] = []
            sector_growth[sector].append(growth)

    # セクター平均を計算
    sector_avg = {}
    for sector, rates in sector_growth.items():
        if len(rates) >= 3:  # 3社以上のセクターのみ
            sector_avg[sector] = {
                'avg': np.mean(rates),
                'median': np.median(rates),
                'count': len(rates)
            }

    # 超過成長率 & スコア算出
    results = []
    for ticker, data in growth_data.items():
        sector = data['sector']
        company_growth = data['growth']

        if sector not in sector_avg:
            continue

        avg = sector_avg[sector]['avg']
        excess = company_growth - avg

        # スコア計算 (0-100)
        # 超過成長率が高いほど高スコア
        # +20%超過 → 90-100点
        # +10%超過 → 70-90点
        # +0%超過 → 50-70点
        # -10%以下 → 30-50点
        # -20%以下 → 10-30点
        if excess >= 20:
            score = min(100, 85 + excess * 0.5)
        elif excess >= 10:
            score = 70 + (excess - 10) * 1.5
        elif excess >= 0:
            score = 50 + excess * 2
        elif excess >= -10:
            score = 30 + (excess + 10) * 2
        else:
            score = max(0, 30 + excess)

        results.append({
            'ticker': ticker,
            'sector': sector,
            'company_rev_growth': round(company_growth, 1),
            'sector_avg_growth': round(avg, 1),
            'excess_growth': round(excess, 1),
            'sector_company_count': sector_avg[sector]['count'],
            'industry_score': round(score, 1),
        })

    results.sort(key=lambda x: x['industry_score'], reverse=True)
    return results


def save_industry_scores(conn, results):
    c = conn.cursor()
    for r in results:
        c.execute('''
            INSERT OR REPLACE INTO industry_scores
            (ticker, sector, company_rev_growth, sector_avg_growth,
             excess_growth, sector_company_count, industry_score)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (
            r['ticker'], r['sector'], r['company_rev_growth'],
            r['sector_avg_growth'], r['excess_growth'],
            r['sector_company_count'], r['industry_score'],
        ))
    conn.commit()


def run():
    print("🏭 業界超過成長率スコア算出")
    print("=" * 50)

    conn = get_db()
    init_industry_table(conn)

    results = calculate_all_industry_scores(conn)

    if not results:
        print("  ❌ 分析対象がありません")
        conn.close()
        return

    save_industry_scores(conn, results)

    # サマリー
    top10 = results[:10]
    print(f"\n  分析銘柄数: {len(results)}")
    print(f"\n  📈 業界超過成長率 TOP10:")
    for r in top10:
        print(f"    {r['ticker']} | 自社 {r['company_rev_growth']:+.1f}% | 業界平均 {r['sector_avg_growth']:+.1f}% | 超過 {r['excess_growth']:+.1f}% | Score {r['industry_score']}")

    # 業界別サマリー
    sectors = {}
    for r in results:
        s = r['sector']
        if s not in sectors:
            sectors[s] = {'count': 0, 'avg_excess': []}
        sectors[s]['count'] += 1
        sectors[s]['avg_excess'].append(r['excess_growth'])

    print(f"\n  🏭 セクター別サマリー:")
    for s, data in sorted(sectors.items(), key=lambda x: np.mean(x[1]['avg_excess']), reverse=True)[:10]:
        avg_ex = np.mean(data['avg_excess'])
        print(f"    {s}: {data['count']}社 / 平均超過成長 {avg_ex:+.1f}%")

    conn.close()
    print(f"\n✅ 業界スコア算出完了: {len(results)}銘柄")


if __name__ == '__main__':
    run()
