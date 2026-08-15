"""
毎日の自動実行スクリプト

1. EDINET新着チェック
2. 株価更新
3. スクリーニング実行
4. ポートフォリオ監視（損切り/利確チェック）
5. レポート生成

cron設定例:
  0 19 * * 1-5 cd /path/to/stock-screener && python src/daily_run.py
  （平日19:00に実行 = 市場終了後）
"""

import os
import sys
import json
from datetime import datetime

# 自前モジュール
from fetch_edinet import fetch_and_store
from fetch_prices import fetch_prices_for_all
from screener import run_screening
from portfolio_monitor import check_portfolio


def generate_daily_report(output_dir: str = "data"):
    """日次レポートをJSON出力（サイト用）"""
    import sqlite3
    import yaml

    config_path = os.path.join(os.path.dirname(__file__), '..', 'config', 'settings.yaml')
    with open(config_path, 'r') as f:
        config = yaml.safe_load(f)

    db_path = os.path.join(os.path.dirname(__file__), '..', config['database']['path'])
    conn = sqlite3.connect(db_path)
    c = conn.cursor()

    today = datetime.now().strftime('%Y-%m-%d')

    # 最新スクリーニング結果TOP20
    c.execute('''
        SELECT sr.rank, sr.ticker, c.name, sr.per, sr.pbr, sr.roe,
               sr.dividend_yield, sr.op_growth, sr.equity_ratio,
               sr.market_cap, sr.score
        FROM screening_results sr
        JOIN companies c ON sr.ticker = c.ticker
        WHERE DATE(sr.screened_at) = ?
        ORDER BY sr.rank
        LIMIT 20
    ''', (today,))

    results = c.fetchall()
    columns = ['rank', 'ticker', 'name', 'per', 'pbr', 'roe',
               'dividend_yield', 'op_growth', 'equity_ratio',
               'market_cap', 'score']

    report = {
        'date': today,
        'generated_at': datetime.now().isoformat(),
        'screening_results': [dict(zip(columns, row)) for row in results],
    }

    # ポートフォリオサマリー
    c.execute('''
        SELECT
            COUNT(*) as positions,
            SUM(CASE WHEN profit_loss > 0 THEN 1 ELSE 0 END) as winners,
            SUM(CASE WHEN profit_loss < 0 THEN 1 ELSE 0 END) as losers,
            SUM(profit_loss) as total_pnl
        FROM portfolio
        WHERE status = 'HOLD'
    ''')

    pf = c.fetchone()
    report['portfolio'] = {
        'positions': pf[0] or 0,
        'winners': pf[1] or 0,
        'losers': pf[2] or 0,
        'total_pnl': pf[3] or 0,
    }

    # JSON出力
    report_path = os.path.join(
        os.path.dirname(__file__), '..', output_dir, f'report_{today}.json'
    )
    with open(report_path, 'w', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    # latest.json にもコピー（サイト用）
    latest_path = os.path.join(
        os.path.dirname(__file__), '..', output_dir, 'latest_report.json'
    )
    with open(latest_path, 'w', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print(f"📝 日次レポート出力: {report_path}")

    conn.close()
    return report


def main():
    print("=" * 60)
    print(f"🚀 日次バッチ実行: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 60)

    try:
        # Step 1: EDINET更新（直近3日分）
        print("\n[Step 1/5] EDINET データ更新")
        fetch_and_store(days_back=3)

        # Step 2: 株価更新
        print("\n[Step 2/5] 株価データ更新")
        fetch_prices_for_all(period="5d")

        # Step 3: スクリーニング
        print("\n[Step 3/5] スクリーニング実行")
        run_screening()

        # Step 4: ポートフォリオ監視
        print("\n[Step 4/5] ポートフォリオ監視")
        try:
            check_portfolio()
        except Exception as e:
            print(f"  ⚠️ ポートフォリオ監視スキップ: {e}")

        # Step 5: レポート生成
        print("\n[Step 5/5] 日次レポート生成")
        report = generate_daily_report()

        print(f"\n{'=' * 60}")
        print(f"✅ 日次バッチ完了: {datetime.now().strftime('%H:%M')}")
        print(f"   スクリーニング候補: {len(report['screening_results'])}銘柄")
        print(f"   保有ポジション: {report['portfolio']['positions']}銘柄")
        print(f"{'=' * 60}")

    except Exception as e:
        print(f"\n❌ バッチ実行エラー: {e}")
        raise


if __name__ == '__main__':
    main()
