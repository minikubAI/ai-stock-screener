"""
強化版スクリーナー v2

第1層（財務スクリーニング）× 第2層（トレンド分析）を統合。
両方のスコアを加重平均して総合ランキングを算出。

使い方:
  python src/screener_v2.py
"""

import sqlite3
import os
import json
import yaml
from datetime import datetime
from rich.console import Console
from rich.table import Table

console = Console()

def get_config():
    config_path = os.path.join(os.path.dirname(__file__), '..', 'config', 'settings.yaml')
    with open(config_path, 'r') as f:
        return yaml.safe_load(f)

def get_db():
    config = get_config()
    db_path = os.path.join(os.path.dirname(__file__), '..', config['database']['path'])
    return sqlite3.connect(db_path)


def get_financial_scores(conn, config) -> dict:
    """第1層：財務スコアを計算（screener.pyのロジック流用）"""
    c = conn.cursor()

    c.execute('''
        SELECT
            c.ticker, c.name, c.sector,
            f.revenue, f.operating_income, f.net_income,
            f.total_assets, f.total_equity,
            f.shares_outstanding, f.eps, f.bps, f.dividends_per_share,
            p.close, p.volume
        FROM companies c
        INNER JOIN financials f ON c.ticker = f.ticker
        INNER JOIN (
            SELECT ticker, close, volume,
                   ROW_NUMBER() OVER (PARTITION BY ticker ORDER BY date DESC) as rn
            FROM prices
        ) p ON c.ticker = p.ticker AND p.rn = 1
        WHERE f.fiscal_year = (
            SELECT MAX(f2.fiscal_year) FROM financials f2 WHERE f2.ticker = f.ticker
        )
    ''')

    rows = c.fetchall()
    cols = [d[0] for d in c.description]
    stocks = [dict(zip(cols, r)) for r in rows]

    sc = config['screening']
    scored = {}

    for s in stocks:
        price = s['close']
        if not price or price <= 0:
            continue

        ticker = s['ticker']
        eps = s['eps']
        bps = s['bps']

        # EPSがなければ計算
        if not eps and s['net_income'] and s['shares_outstanding']:
            eps = s['net_income'] / s['shares_outstanding']
        if not bps and s['total_equity'] and s['shares_outstanding']:
            bps = s['total_equity'] / s['shares_outstanding']

        per = price / eps if eps and eps > 0 else None
        pbr = price / bps if bps and bps > 0 else None
        roe = (s['net_income'] / s['total_equity'] * 100) if s['net_income'] and s['total_equity'] and s['total_equity'] > 0 else None

        dps = s.get('dividends_per_share') or 0
        div_yield = (dps / price * 100) if price > 0 else None

        equity_ratio = None
        if s['total_equity'] and s['total_assets'] and s['total_assets'] > 0:
            equity_ratio = s['total_equity'] / s['total_assets'] * 100

        market_cap = None
        if s['shares_outstanding'] and price:
            market_cap = price * s['shares_outstanding'] / 100_000_000

        # フィルタリング
        pass_filter = True
        if per is not None and not (sc['per_min'] <= per <= sc['per_max']):
            pass_filter = False
        if pbr is not None and pbr > sc['pbr_max']:
            pass_filter = False
        if roe is not None and roe < sc['roe_min']:
            pass_filter = False
        if equity_ratio is not None and equity_ratio < sc['equity_ratio_min']:
            pass_filter = False
        if market_cap is not None:
            if not (sc['market_cap_min'] <= market_cap <= sc['market_cap_max']):
                pass_filter = False

        if not pass_filter:
            continue

        scored[ticker] = {
            'ticker': ticker,
            'name': s['name'],
            'sector': s['sector'] or '',
            'price': price,
            'per': round(per, 1) if per else None,
            'pbr': round(pbr, 2) if pbr else None,
            'roe': round(roe, 1) if roe else None,
            'div_yield': round(div_yield, 1) if div_yield else None,
            'equity_ratio': round(equity_ratio, 1) if equity_ratio else None,
            'market_cap': round(market_cap, 0) if market_cap else None,
        }

    # 財務スコア計算（正規化）
    if not scored:
        return {}

    values = list(scored.values())

    def safe_list(key):
        return [v[key] for v in values if v[key] is not None] or [0]

    def norm(val, min_v, max_v, invert=False):
        if val is None or max_v == min_v:
            return 0.5
        n = (val - min_v) / (max_v - min_v)
        return (1 - n) if invert else n

    per_vals = safe_list('per')
    pbr_vals = safe_list('pbr')
    roe_vals = safe_list('roe')
    dy_vals = safe_list('div_yield')

    for v in values:
        score = 0
        score += norm(v['per'], min(per_vals), max(per_vals), invert=True) * 30
        score += norm(v['pbr'], min(pbr_vals), max(pbr_vals), invert=True) * 25
        score += norm(v['roe'], min(roe_vals), max(roe_vals)) * 25
        score += norm(v['div_yield'], min(dy_vals), max(dy_vals)) * 20
        v['financial_score'] = round(score, 1)

    return scored


def get_trend_scores(conn) -> dict:
    """第2層：トレンドスコアを取得"""
    c = conn.cursor()
    c.execute('''
        SELECT ticker, trend_score, rev_trend, op_trend, roe_trend,
               rev_avg_growth, op_avg_growth,
               consecutive_rev_growth, consecutive_op_growth
        FROM trend_scores
    ''')
    rows = c.fetchall()

    trends = {}
    for r in rows:
        trends[r[0]] = {
            'trend_score': r[1] or 0,
            'rev_trend': r[2] or 'flat',
            'op_trend': r[3] or 'flat',
            'roe_trend': r[4] or 'flat',
            'rev_avg_growth': r[5] or 0,
            'op_avg_growth': r[6] or 0,
            'consecutive_rev': r[7] or 0,
            'consecutive_op': r[8] or 0,
        }
    return trends


def combine_scores(financial: dict, trends: dict) -> list[dict]:
    """
    第1層×第2層を統合

    総合スコア = 財務スコア × 60% + トレンドスコア × 40%
    """
    combined = []

    for ticker, fin in financial.items():
        trend = trends.get(ticker, {})
        trend_score = trend.get('trend_score', 0)

        # 総合スコア
        total = fin['financial_score'] * 0.6 + trend_score * 0.4

        entry = {**fin}
        entry['trend_score'] = round(trend_score, 1)
        entry['total_score'] = round(total, 1)
        entry['rev_trend'] = trend.get('rev_trend', '-')
        entry['op_trend'] = trend.get('op_trend', '-')
        entry['roe_trend'] = trend.get('roe_trend', '-')
        entry['rev_avg_growth'] = trend.get('rev_avg_growth', 0)
        entry['consecutive_rev'] = trend.get('consecutive_rev', 0)

        combined.append(entry)

    combined.sort(key=lambda x: x['total_score'], reverse=True)

    for i, s in enumerate(combined):
        s['rank'] = i + 1

    return combined


def display(results: list[dict], top_n: int = 20):
    """結果表示"""
    def arrow(d):
        if d == 'up': return '[green]↑[/green]'
        elif d == 'down': return '[red]↓[/red]'
        elif d == '-': return '[dim]-[/dim]'
        else: return '[dim]→[/dim]'

    table = Table(title=f"🏆 統合スクリーニング TOP {top_n}（財務×トレンド）")
    table.add_column("#", width=3)
    table.add_column("コード", style="bold", width=5)
    table.add_column("企業名", max_width=16)
    table.add_column("株価", justify="right", width=8)
    table.add_column("PER", justify="right", width=5)
    table.add_column("PBR", justify="right", width=5)
    table.add_column("ROE", justify="right", width=5)
    table.add_column("配当", justify="right", width=5)
    table.add_column("売上", justify="center", width=4)
    table.add_column("営利", justify="center", width=4)
    table.add_column("連続", justify="center", width=4)
    table.add_column("財務", justify="right", width=5)
    table.add_column("成長", justify="right", width=5)
    table.add_column("総合", justify="right", style="bold green", width=5)

    for s in results[:top_n]:
        table.add_row(
            str(s['rank']),
            s['ticker'],
            s['name'][:16],
            f"¥{s['price']:,.0f}",
            f"{s['per']}" if s['per'] else "-",
            f"{s['pbr']}" if s['pbr'] else "-",
            f"{s['roe']}%" if s['roe'] else "-",
            f"{s['div_yield']}%" if s['div_yield'] else "-",
            arrow(s['rev_trend']),
            arrow(s['op_trend']),
            f"{s['consecutive_rev']}年" if s['consecutive_rev'] > 0 else "-",
            f"{s['financial_score']}",
            f"{s['trend_score']}",
            f"{s['total_score']}",
        )

    console.print(table)


def export_for_site(results: list[dict], top_n: int = 20):
    """サイト用JSONを出力"""
    output = {
        'date': datetime.now().strftime('%Y-%m-%d'),
        'generated_at': datetime.now().isoformat(),
        'version': 'v2',
        'screening_results': []
    }

    for s in results[:top_n]:
        output['screening_results'].append({
            'rank': s['rank'],
            'ticker': s['ticker'],
            'name': s['name'],
            'sector': s['sector'],
            'price': s['price'],
            'per': s['per'],
            'pbr': s['pbr'],
            'roe': s['roe'],
            'dividend_yield': s['div_yield'],
            'equity_ratio': s['equity_ratio'],
            'market_cap': s['market_cap'],
            'rev_trend': s['rev_trend'],
            'op_trend': s['op_trend'],
            'roe_trend': s['roe_trend'],
            'financial_score': s['financial_score'],
            'trend_score': s['trend_score'],
            'total_score': s['total_score'],
        })

    # JSON出力
    data_dir = os.path.join(os.path.dirname(__file__), '..', 'data')
    os.makedirs(data_dir, exist_ok=True)

    path = os.path.join(data_dir, 'latest_report.json')
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    # 日付入りバックアップ
    backup_path = os.path.join(data_dir, f"report_{output['date']}.json")
    with open(backup_path, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    console.print(f"\n  📁 サイト用JSON: {path}")


def run():
    console.print("\n[bold]🏆 統合スクリーニング v2（財務 × トレンド）[/bold]\n")

    config = get_config()
    conn = get_db()

    # 第1層
    console.print("  1️⃣  財務スクリーニング実行中...")
    financial = get_financial_scores(conn, config)
    console.print(f"     → {len(financial)}銘柄が財務条件を通過")

    if not financial:
        console.print("[red]  ❌ 条件を通過した銘柄がありません[/red]")
        conn.close()
        return

    # 第2層
    console.print("  2️⃣  トレンドスコア取得中...")
    trends = get_trend_scores(conn)
    matched = sum(1 for t in financial if t in trends)
    console.print(f"     → {matched}/{len(financial)}銘柄にトレンドデータあり")

    # 統合
    console.print("  3️⃣  スコア統合中...\n")
    results = combine_scores(financial, trends)

    # 表示
    display(results, top_n=20)

    # サイト用JSON出力
    export_for_site(results, top_n=20)

    # サマリー
    console.print(f"\n[bold green]✅ 統合スクリーニング完了: {len(results)}銘柄[/bold green]")

    trend_up = sum(1 for r in results if r['rev_trend'] == 'up')
    console.print(f"  うち売上↑トレンド: {trend_up}銘柄")
    console.print(f"  トレンドデータなし: {len(results) - matched}銘柄（トレンドスコア=0で計算）")

    conn.close()


if __name__ == '__main__':
    run()
