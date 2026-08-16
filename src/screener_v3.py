"""
統合スクリーナー v3 — 全4層統合 + Core/Satellite分類

Layer 1: Financial screening (35%)
Layer 2: Trend analysis (30%)
Layer 3: News sentiment (20%)
Layer 4: Macro environment (15%)

+ 業界超過成長率ボーナス
+ Core/Satellite自動分類

使い方:
  # 事前に全レイヤーのデータを更新
  python src/fetch_news.py
  python src/trend_analysis.py
  python src/macro_score.py
  python src/industry_score.py

  # 統合スクリーニング実行
  python src/screener_v3.py
"""

from __future__ import annotations
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


# ============================================================
# Layer 1: Financial Screening (35%)
# ============================================================
def get_financial_data(conn) -> list[dict]:
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
    return [dict(zip(cols, r)) for r in rows]


def calculate_financial_score(stock: dict) -> dict | None:
    """個別銘柄の財務スコアを計算"""
    price = stock['close']
    if not price or price <= 0:
        return None

    eps = stock['eps']
    bps = stock['bps']
    if not eps and stock['net_income'] and stock['shares_outstanding']:
        eps = stock['net_income'] / stock['shares_outstanding']
    if not bps and stock['total_equity'] and stock['shares_outstanding']:
        bps = stock['total_equity'] / stock['shares_outstanding']

    per = price / eps if eps and eps > 0 else None
    pbr = price / bps if bps and bps > 0 else None
    roe = (stock['net_income'] / stock['total_equity'] * 100) if stock['net_income'] and stock['total_equity'] and stock['total_equity'] > 0 else None
    dps = stock.get('dividends_per_share') or 0
    div_yield = (dps / price * 100) if price > 0 else None
    equity_ratio = (stock['total_equity'] / stock['total_assets'] * 100) if stock['total_equity'] and stock['total_assets'] and stock['total_assets'] > 0 else None
    market_cap = (price * stock['shares_outstanding'] / 100_000_000) if stock['shares_outstanding'] else None

    return {
        'ticker': stock['ticker'],
        'name': stock['name'],
        'sector': stock['sector'] or '',
        'price': price,
        'per': round(per, 1) if per else None,
        'pbr': round(pbr, 2) if pbr else None,
        'roe': round(roe, 1) if roe else None,
        'div_yield': round(div_yield, 1) if div_yield else None,
        'equity_ratio': round(equity_ratio, 1) if equity_ratio else None,
        'market_cap': round(market_cap, 0) if market_cap else None,
        'volume': stock['volume'],
    }


def filter_core(stocks: list[dict], config: dict) -> list[dict]:
    """Core枠フィルター（バリュー基準）"""
    sc = config['screening']
    passed = []
    for s in stocks:
        if s['per'] is not None and not (sc['per_min'] <= s['per'] <= sc['per_max']):
            continue
        if s['pbr'] is not None and s['pbr'] > sc['pbr_max']:
            continue
        if s['roe'] is not None and s['roe'] < sc['roe_min']:
            continue
        if s['div_yield'] is not None and s['div_yield'] < sc['dividend_yield_min']:
            continue
        if s['equity_ratio'] is not None and s['equity_ratio'] < sc['equity_ratio_min']:
            continue
        passed.append(s)
    return passed


def filter_satellite(stocks: list[dict]) -> list[dict]:
    """Satellite枠フィルター（成長基準）"""
    passed = []
    for s in stocks:
        # PER上限40倍（成長株は高PER許容）
        if s['per'] is not None and (s['per'] <= 0 or s['per'] > 40):
            continue
        # ROE 5%以上（緩め）
        if s['roe'] is not None and s['roe'] < 5:
            continue
        # 営業黒字必須（赤字成長企業は除外）
        # → この判定はトレンドスコアで補完
        passed.append(s)
    return passed


# ============================================================
# Layer 2: Trend Score (30%)
# ============================================================
def get_trend_scores(conn) -> dict:
    c = conn.cursor()
    c.execute('SELECT ticker, trend_score, rev_trend, op_trend, consecutive_rev_growth FROM trend_scores')
    return {r[0]: {
        'trend_score': r[1] or 0,
        'rev_trend': r[2] or 'flat',
        'op_trend': r[3] or 'flat',
        'consecutive_rev': r[4] or 0
    } for r in c.fetchall()}


# ============================================================
# Layer 3: Sentiment Score (20%)
# ============================================================
def get_sentiment_scores(conn) -> dict:
    c = conn.cursor()
    c.execute('''
        SELECT ticker,
               AVG(sentiment) as avg_sentiment,
               COUNT(*) as news_count,
               SUM(CASE WHEN sentiment > 0 THEN 1 ELSE 0 END) as positive_count,
               SUM(CASE WHEN sentiment < 0 THEN 1 ELSE 0 END) as negative_count
        FROM news
        GROUP BY ticker
    ''')
    result = {}
    for r in c.fetchall():
        # avg_sentiment is -1 to +1, normalize to 0-100
        raw = (r[1] or 0) * 100
        normalized = max(0, min(100, (raw + 100) / 2))
        result[r[0]] = {
            'sentiment_score': round(normalized, 1),
            'news_count': r[2] or 0,
            'positive': r[3] or 0,
            'negative': r[4] or 0,
        }
    return result


# ============================================================
# Layer 4: Macro Score (15%)
# ============================================================
def get_macro_score(conn) -> dict:
    c = conn.cursor()
    c.execute('SELECT combined_score, signal FROM macro_scores ORDER BY date DESC LIMIT 1')
    row = c.fetchone()
    if row:
        return {'macro_score': row[0] or 50, 'signal': row[1] or 'NORMAL'}
    return {'macro_score': 50, 'signal': 'NORMAL'}


# ============================================================
# Industry Bonus
# ============================================================
def get_industry_scores(conn) -> dict:
    c = conn.cursor()
    c.execute('SELECT ticker, excess_growth, industry_score FROM industry_scores')
    return {r[0]: {
        'excess_growth': r[1] or 0,
        'industry_score': r[2] or 50
    } for r in c.fetchall()}


# ============================================================
# Layer 5: Valuation Gap (bonus)
# ============================================================
def get_valuation_gaps(conn) -> dict:
    c = conn.cursor()
    c.execute('SELECT ticker, gap_score, gap_type, fundamental_improvement, valuation_change FROM valuation_gap')
    return {r[0]: {
        'vgap_score': r[1] or 50,
        'gap_type': r[2] or 'UNKNOWN',
        'fundamental_improvement': r[3] or 0,
        'valuation_change': r[4] or 0,
    } for r in c.fetchall()}


# ============================================================
# Score Integration
# ============================================================
def calculate_total_score(financial_score, trend_score, sentiment_score, macro_score, vgap_score, industry_bonus):
    """
    Total = Financial(25%) + Trend(25%) + VGap(20%) + Sentiment(15%) + Macro(15%)
          + Industry bonus

    V2からの変更:
    - Financial 35→25%（バリュエーションギャップに一部移管）
    - Trend 30→25%
    - Valuation Gap 0→20%（新規）
    - Sentiment 20→15%
    - Macro 15→15%（維持）
    """
    total = (
        financial_score * 0.25 +
        trend_score * 0.25 +
        vgap_score * 0.20 +
        sentiment_score * 0.15 +
        macro_score * 0.15
    )

    # 業界超過成長率ボーナス
    excess = industry_bonus.get('excess_growth', 0)
    if excess > 20:
        total += 10
    elif excess > 10:
        total += 5
    elif excess > 5:
        total += 2

    return round(min(100, max(0, total)), 1)


def normalize_financial_scores(stocks: list[dict]) -> list[dict]:
    """財務指標を0-100に正規化"""
    if not stocks:
        return stocks

    def vals(key):
        return [s[key] for s in stocks if s[key] is not None] or [0]

    def norm(val, min_v, max_v, invert=False):
        if val is None or max_v == min_v:
            return 50
        n = (val - min_v) / (max_v - min_v)
        return ((1 - n) if invert else n) * 100

    per_v = vals('per')
    pbr_v = vals('pbr')
    roe_v = vals('roe')
    dy_v = vals('div_yield')

    for s in stocks:
        score = (
            norm(s['per'], min(per_v), max(per_v), invert=True) * 0.30 +
            norm(s['pbr'], min(pbr_v), max(pbr_v), invert=True) * 0.25 +
            norm(s['roe'], min(roe_v), max(roe_v)) * 0.25 +
            norm(s['div_yield'], min(dy_v), max(dy_v)) * 0.20
        )
        s['financial_score'] = round(score, 1)

    return stocks


# ============================================================
# Main
# ============================================================
def run():
    console.print("\n[bold]🏆 統合スクリーナー v3 — 全4層統合[/bold]\n")

    config = get_config()
    conn = get_db()

    # 全銘柄の財務データ取得
    console.print("  [1/6] 財務データ取得...")
    raw_stocks = get_financial_data(conn)
    all_stocks = [s for s in (calculate_financial_score(st) for st in raw_stocks) if s is not None]
    console.print(f"        → {len(all_stocks)}銘柄")

    # Layer 2: Trend
    console.print("  [2/6] トレンドスコア取得...")
    trends = get_trend_scores(conn)
    console.print(f"        → {len(trends)}銘柄にデータあり")

    # Layer 3: Sentiment
    console.print("  [3/6] センチメントスコア取得...")
    sentiments = get_sentiment_scores(conn)
    console.print(f"        → {len(sentiments)}銘柄にデータあり")

    # Layer 4: Macro
    console.print("  [4/6] マクロ環境スコア取得...")
    macro = get_macro_score(conn)
    console.print(f"        → Score: {macro['macro_score']} / Signal: {macro['signal']}")

    # Industry bonus
    console.print("  [5/7] 業界超過成長率取得...")
    industry = get_industry_scores(conn)
    console.print(f"        → {len(industry)}銘柄にデータあり")

    # Layer 5: Valuation Gap
    console.print("  [6/7] バリュエーションギャップ取得...")
    vgaps = get_valuation_gaps(conn)
    gems = sum(1 for v in vgaps.values() if v['gap_type'] == 'HIDDEN_GEM')
    console.print(f"        → {len(vgaps)}銘柄にデータあり（Hidden Gem: {gems}社）")

    # === Core枠スクリーニング ===
    console.print("\n  [7/7] スコア統合 & ランキング...")

    core_filtered = filter_core(all_stocks, config)
    core_filtered = normalize_financial_scores(core_filtered)

    core_results = []
    for s in core_filtered:
        t = trends.get(s['ticker'], {})
        se = sentiments.get(s['ticker'], {'sentiment_score': 50})
        ind = industry.get(s['ticker'], {})
        vg = vgaps.get(s['ticker'], {'vgap_score': 50, 'gap_type': 'UNKNOWN'})

        total = calculate_total_score(
            s['financial_score'],
            t.get('trend_score', 30),
            se['sentiment_score'],
            macro['macro_score'],
            vg['vgap_score'],
            ind
        )

        s['trend_score'] = t.get('trend_score', 0)
        s['sentiment_score'] = se['sentiment_score']
        s['vgap_score'] = vg['vgap_score']
        s['gap_type'] = vg['gap_type']
        s['industry_bonus'] = ind.get('excess_growth', 0)
        s['total_score'] = total
        s['tier'] = 'core'
        s['rev_trend'] = t.get('rev_trend', '-')
        s['op_trend'] = t.get('op_trend', '-')
        core_results.append(s)

    core_results.sort(key=lambda x: x['total_score'], reverse=True)

    # === Satellite枠スクリーニング ===
    sat_filtered = filter_satellite(all_stocks)
    sat_filtered = normalize_financial_scores(sat_filtered)

    sat_results = []
    for s in sat_filtered:
        t = trends.get(s['ticker'], {})
        se = sentiments.get(s['ticker'], {'sentiment_score': 50})
        ind = industry.get(s['ticker'], {})
        vg = vgaps.get(s['ticker'], {'vgap_score': 50, 'gap_type': 'UNKNOWN'})

        # Satellite: トレンド必須（上昇トレンドのみ）
        if t.get('rev_trend') != 'up':
            continue

        total = calculate_total_score(
            s['financial_score'],
            t.get('trend_score', 30),
            se['sentiment_score'],
            macro['macro_score'],
            vg['vgap_score'],
            ind
        )

        # Satellite加点: 成長性をさらに重視
        growth_bonus = min(15, t.get('trend_score', 0) * 0.2)
        # Hidden Gem加点: バリュエーションギャップが大きい成長株
        if vg['gap_type'] == 'HIDDEN_GEM':
            growth_bonus += 10
        total = min(100, total + growth_bonus)

        s['trend_score'] = t.get('trend_score', 0)
        s['sentiment_score'] = se['sentiment_score']
        s['vgap_score'] = vg['vgap_score']
        s['gap_type'] = vg['gap_type']
        s['industry_bonus'] = ind.get('excess_growth', 0)
        s['total_score'] = total
        s['tier'] = 'satellite'
        s['rev_trend'] = t.get('rev_trend', '-')
        s['op_trend'] = t.get('op_trend', '-')
        sat_results.append(s)

    sat_results.sort(key=lambda x: x['total_score'], reverse=True)

    # Core結果から上位をSatelliteから除外（重複防止）
    core_tickers = {s['ticker'] for s in core_results[:20]}
    sat_results = [s for s in sat_results if s['ticker'] not in core_tickers]

    # === 結果表示 ===
    def arrow(d):
        if d == 'up': return '[green]↑[/green]'
        elif d == 'down': return '[red]↓[/red]'
        else: return '[dim]→[/dim]'

    # Core TOP15
    console.print(f"\n[bold blue]━━━ Core枠 TOP15（バリュー＋配当）━━━[/bold blue]")
    table_c = Table()
    table_c.add_column("#", width=3)
    table_c.add_column("Code", width=5)
    table_c.add_column("Name", max_width=14)
    table_c.add_column("Price", justify="right", width=7)
    table_c.add_column("PER", justify="right", width=5)
    table_c.add_column("Div%", justify="right", width=5)
    table_c.add_column("Trend", justify="center", width=5)
    table_c.add_column("Sent", justify="right", width=4)
    table_c.add_column("VGap", justify="right", width=5)
    table_c.add_column("Ind+", justify="right", width=5)
    table_c.add_column("Total", justify="right", style="bold green", width=5)

    for i, s in enumerate(core_results[:15], 1):
        gap_style = "bold green" if s.get('gap_type') == 'HIDDEN_GEM' else ""
        table_c.add_row(
            str(i), s['ticker'], s['name'][:14],
            f"¥{s['price']:,.0f}",
            f"{s['per']}" if s['per'] else "-",
            f"{s['div_yield']}%" if s['div_yield'] else "-",
            arrow(s['rev_trend']),
            f"{s['sentiment_score']:.0f}",
            f"[{gap_style}]{s.get('vgap_score', 0):.0f}[/{gap_style}]" if gap_style else f"{s.get('vgap_score', 0):.0f}",
            f"{s['industry_bonus']:+.0f}%",
            f"{s['total_score']}"
        )
    console.print(table_c)

    # Satellite TOP10
    console.print(f"\n[bold rgb(235,104,52)]━━━ Satellite枠 TOP10（成長株）━━━[/bold rgb(235,104,52)]")
    table_s = Table()
    table_s.add_column("#", width=3)
    table_s.add_column("Code", width=5)
    table_s.add_column("Name", max_width=14)
    table_s.add_column("Price", justify="right", width=7)
    table_s.add_column("PER", justify="right", width=5)
    table_s.add_column("ROE", justify="right", width=5)
    table_s.add_column("Trend", justify="center", width=5)
    table_s.add_column("Ind+", justify="right", width=5)
    table_s.add_column("Total", justify="right", style="bold", width=5)

    for i, s in enumerate(sat_results[:10], 1):
        table_s.add_row(
            str(i), s['ticker'], s['name'][:14],
            f"¥{s['price']:,.0f}",
            f"{s['per']}" if s['per'] else "-",
            f"{s['roe']}%" if s['roe'] else "-",
            arrow(s['rev_trend']),
            f"{s['industry_bonus']:+.0f}%",
            f"{s['total_score']}"
        )
    console.print(table_s)

    # マクロシグナル
    signal_display = {
        'BUY': '[bold green]BUY — 積極的に購入[/bold green]',
        'NORMAL': '[bold]NORMAL — 通常通り購入[/bold]',
        'CAUTION': '[bold yellow]CAUTION — 購入額50%に減額[/bold yellow]',
        'PAUSE': '[bold red]PAUSE — 新規購入停止[/bold red]',
    }
    console.print(f"\n  🌍 マクロシグナル: {signal_display.get(macro['signal'], macro['signal'])}")

    # JSON出力
    output = {
        'date': datetime.now().strftime('%Y-%m-%d'),
        'version': 'v3',
        'macro': macro,
        'score_weights': {'financial': 25, 'trend': 25, 'valuation_gap': 20, 'sentiment': 15, 'macro': 15},
        'core_results': [{
            'rank': i+1, 'tier': 'core', **{k: s[k] for k in [
                'ticker','name','sector','price','per','pbr','roe',
                'div_yield','equity_ratio','market_cap',
                'financial_score','trend_score','sentiment_score',
                'industry_bonus','total_score','rev_trend','op_trend'
            ] if k in s}, 'vgap_score': s.get('vgap_score', 0), 'gap_type': s.get('gap_type', ''),
        } for i, s in enumerate(core_results[:20])],
        'satellite_results': [{
            'rank': i+1, 'tier': 'satellite', **{k: s[k] for k in [
                'ticker','name','sector','price','per','pbr','roe',
                'div_yield','equity_ratio','market_cap',
                'financial_score','trend_score','sentiment_score',
                'industry_bonus','total_score','rev_trend','op_trend'
            ] if k in s}, 'vgap_score': s.get('vgap_score', 0), 'gap_type': s.get('gap_type', ''),
        } for i, s in enumerate(sat_results[:10])],
    }

    data_dir = os.path.join(os.path.dirname(__file__), '..', 'data')
    os.makedirs(data_dir, exist_ok=True)

    path = os.path.join(data_dir, 'latest_report.json')
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    # screening_results テーブルにも書き込む
    screened_at = datetime.now().isoformat()
    all_ranked = [(s, i + 1) for i, s in enumerate(core_results[:20])] + \
                 [(s, 20 + i + 1) for i, s in enumerate(sat_results[:10])]
    total = len(all_ranked)
    c = conn.cursor()
    for s, rank in all_ranked:
        c.execute('''
            INSERT INTO screening_results
              (ticker, screened_at, per, pbr, roe, dividend_yield, equity_ratio, market_cap, score, rank)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            s['ticker'], screened_at,
            s.get('per'), s.get('pbr'), s.get('roe'),
            s.get('div_yield'), s.get('equity_ratio'), s.get('market_cap'),
            s.get('total_score', 0), rank,
        ))
        # companies テーブルに銘柄名を確保
        c.execute('''
            INSERT OR IGNORE INTO companies (ticker, name) VALUES (?, ?)
        ''', (s['ticker'], s.get('name', '')))
    conn.commit()
    console.print(f"  💾 screening_results に {total}銘柄を保存（screened_at: {screened_at[:16]}）")

    console.print(f"\n  📁 レポート出力: {path}")
    console.print(f"\n[bold green]✅ v3スクリーニング完了[/bold green]")
    console.print(f"   Core: {len(core_results)}銘柄（TOP20保存）")
    console.print(f"   Satellite: {len(sat_results)}銘柄（TOP10保存）")

    conn.close()


if __name__ == '__main__':
    run()
