"""
スクリーニングエンジン

財務データ + 株価データからバリュー株をスクリーニング
複合スコアでランキング → 投資候補を選出
"""

import sqlite3
import os
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


def calculate_metrics(conn) -> list[dict]:
    """
    全銘柄の投資指標を計算

    Returns:
        指標付きの銘柄リスト
    """
    c = conn.cursor()

    # 最新の財務データと最新株価を結合
    c.execute('''
        SELECT
            c.ticker,
            c.name,
            c.sector,
            f.fiscal_year,
            f.revenue,
            f.operating_income,
            f.net_income,
            f.total_assets,
            f.total_equity,
            f.shares_outstanding,
            f.dividends_per_share,
            f.eps,
            f.bps,
            p.close as current_price,
            p.volume
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
    columns = [desc[0] for desc in c.description]
    stocks = [dict(zip(columns, row)) for row in rows]

    # 前年の財務データも取得（成長率計算用）
    enriched = []

    for stock in stocks:
        ticker = stock['ticker']
        current_fy = stock['fiscal_year']

        # 前年データ
        c.execute('''
            SELECT operating_income, net_income, revenue
            FROM financials
            WHERE ticker = ? AND fiscal_year < ?
            ORDER BY fiscal_year DESC LIMIT 1
        ''', (ticker, current_fy))

        prev = c.fetchone()
        prev_op = prev[0] if prev else None
        prev_ni = prev[1] if prev else None
        prev_rev = prev[2] if prev else None

        price = stock['current_price']
        if not price or price <= 0:
            continue

        # === 指標計算 ===
        metrics = {
            'ticker': ticker,
            'name': stock['name'],
            'sector': stock['sector'] or '不明',
            'price': price,
        }

        # EPS（1株利益）
        eps = stock.get('eps')
        if not eps and stock['net_income'] and stock['shares_outstanding']:
            eps = stock['net_income'] / stock['shares_outstanding']

        # BPS（1株純資産）
        bps = stock.get('bps')
        if not bps and stock['total_equity'] and stock['shares_outstanding']:
            bps = stock['total_equity'] / stock['shares_outstanding']

        # PER
        metrics['per'] = round(price / eps, 2) if eps and eps > 0 else None

        # PBR
        metrics['pbr'] = round(price / bps, 2) if bps and bps > 0 else None

        # ROE
        if stock['net_income'] and stock['total_equity'] and stock['total_equity'] > 0:
            metrics['roe'] = round(stock['net_income'] / stock['total_equity'] * 100, 2)
        else:
            metrics['roe'] = None

        # 配当利回り
        dps = stock.get('dividends_per_share', 0) or 0
        metrics['dividend_yield'] = round(dps / price * 100, 2) if price > 0 else None

        # 営業利益成長率
        if prev_op and stock['operating_income'] and prev_op > 0:
            metrics['op_growth'] = round(
                (stock['operating_income'] - prev_op) / abs(prev_op) * 100, 2
            )
        else:
            metrics['op_growth'] = None

        # 自己資本比率
        if stock['total_equity'] and stock['total_assets'] and stock['total_assets'] > 0:
            metrics['equity_ratio'] = round(
                stock['total_equity'] / stock['total_assets'] * 100, 2
            )
        else:
            metrics['equity_ratio'] = None

        # 時価総額（億円）- 概算
        if stock['shares_outstanding']:
            metrics['market_cap'] = round(
                price * stock['shares_outstanding'] / 100_000_000, 1
            )
        else:
            metrics['market_cap'] = None

        metrics['volume'] = stock['volume']

        enriched.append(metrics)

    return enriched


def apply_screening(stocks: list[dict], config: dict) -> list[dict]:
    """
    スクリーニング条件でフィルタリング
    """
    sc = config['screening']
    passed = []

    for s in stocks:
        # 各条件をチェック（データがない項目はスキップ）
        if s['per'] is not None:
            if not (sc['per_min'] <= s['per'] <= sc['per_max']):
                continue

        if s['pbr'] is not None:
            if s['pbr'] > sc['pbr_max']:
                continue

        if s['roe'] is not None:
            if s['roe'] < sc['roe_min']:
                continue

        if s['dividend_yield'] is not None:
            if s['dividend_yield'] < sc['dividend_yield_min']:
                continue

        if s['op_growth'] is not None:
            if s['op_growth'] < sc['op_growth_min']:
                continue

        if s['equity_ratio'] is not None:
            if s['equity_ratio'] < sc['equity_ratio_min']:
                continue

        if s['market_cap'] is not None:
            if not (sc['market_cap_min'] <= s['market_cap'] <= sc['market_cap_max']):
                continue

        passed.append(s)

    return passed


def calculate_score(stocks: list[dict]) -> list[dict]:
    """
    複合スコアを計算してランキング

    スコア = PER逆数×25 + PBR逆数×20 + ROE×20 + 配当利回り×20 + 成長率×15
    （各指標を正規化してから重み付け）
    """
    if not stocks:
        return []

    # 各指標の最大値・最小値（正規化用）
    def safe_values(key):
        vals = [s[key] for s in stocks if s[key] is not None]
        return vals if vals else [0]

    def normalize(val, min_v, max_v, invert=False):
        if val is None or max_v == min_v:
            return 0.5
        norm = (val - min_v) / (max_v - min_v)
        return (1 - norm) if invert else norm

    per_vals = safe_values('per')
    pbr_vals = safe_values('pbr')
    roe_vals = safe_values('roe')
    dy_vals = safe_values('dividend_yield')
    og_vals = safe_values('op_growth')

    for s in stocks:
        score = 0
        # PER: 低いほど良い → invert
        score += normalize(s['per'], min(per_vals), max(per_vals), invert=True) * 25
        # PBR: 低いほど良い → invert
        score += normalize(s['pbr'], min(pbr_vals), max(pbr_vals), invert=True) * 20
        # ROE: 高いほど良い
        score += normalize(s['roe'], min(roe_vals), max(roe_vals)) * 20
        # 配当利回り: 高いほど良い
        score += normalize(s['dividend_yield'], min(dy_vals), max(dy_vals)) * 20
        # 営業利益成長率: 高いほど良い
        score += normalize(s['op_growth'], min(og_vals), max(og_vals)) * 15

        s['score'] = round(score, 2)

    # スコア順にソート
    stocks.sort(key=lambda x: x['score'], reverse=True)

    # ランク付け
    for i, s in enumerate(stocks):
        s['rank'] = i + 1

    return stocks


def save_results(conn, stocks: list[dict]):
    """スクリーニング結果をDBに保存"""
    c = conn.cursor()
    now = datetime.now().isoformat()

    for s in stocks:
        c.execute('''
            INSERT INTO screening_results
            (ticker, screened_at, per, pbr, roe, dividend_yield,
             op_growth, equity_ratio, market_cap, score, rank)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            s['ticker'], now, s['per'], s['pbr'], s['roe'],
            s['dividend_yield'], s['op_growth'], s['equity_ratio'],
            s['market_cap'], s['score'], s['rank']
        ))

    conn.commit()


def display_results(stocks: list[dict], top_n: int = 20):
    """結果をリッチテーブルで表示"""
    table = Table(title=f"🏆 スクリーニング結果 TOP {top_n}")

    table.add_column("順位", style="bold cyan", justify="center")
    table.add_column("コード", style="bold")
    table.add_column("企業名", max_width=20)
    table.add_column("株価", justify="right")
    table.add_column("PER", justify="right")
    table.add_column("PBR", justify="right")
    table.add_column("ROE%", justify="right")
    table.add_column("配当%", justify="right")
    table.add_column("成長%", justify="right")
    table.add_column("自己資本%", justify="right")
    table.add_column("スコア", justify="right", style="bold green")

    for s in stocks[:top_n]:
        table.add_row(
            str(s['rank']),
            s['ticker'],
            s['name'][:20],
            f"¥{s['price']:,.0f}" if s['price'] else "-",
            f"{s['per']:.1f}" if s['per'] else "-",
            f"{s['pbr']:.2f}" if s['pbr'] else "-",
            f"{s['roe']:.1f}" if s['roe'] else "-",
            f"{s['dividend_yield']:.1f}" if s['dividend_yield'] else "-",
            f"{s['op_growth']:.1f}" if s['op_growth'] else "-",
            f"{s['equity_ratio']:.1f}" if s['equity_ratio'] else "-",
            f"{s['score']:.1f}",
        )

    console.print(table)


def run_screening():
    """メイン実行"""
    config = get_config()
    conn = get_db()

    console.print("\n[bold]📊 スクリーニング実行中...[/bold]\n")

    # 1. 指標計算
    console.print("  1️⃣  投資指標を計算中...")
    all_stocks = calculate_metrics(conn)
    console.print(f"     → {len(all_stocks)}銘柄のデータ取得")

    if not all_stocks:
        console.print("[red]❌ 分析対象のデータがありません。")
        console.print("   fetch_edinet.py と fetch_prices.py を先に実行してください[/red]")
        conn.close()
        return

    # 2. フィルタリング
    console.print("  2️⃣  条件でフィルタリング中...")
    filtered = apply_screening(all_stocks, config)
    console.print(f"     → {len(filtered)}銘柄が条件通過")

    if not filtered:
        console.print("[yellow]⚠️ 条件を満たす銘柄がありません。\n   config/settings.yaml の条件を緩和してみてください[/yellow]")
        conn.close()
        return

    # 3. スコアリング
    console.print("  3️⃣  スコア計算 & ランキング中...")
    ranked = calculate_score(filtered)

    # 4. 結果保存
    save_results(conn, ranked)
    console.print("  4️⃣  結果をDBに保存しました\n")

    # 5. 結果表示
    display_results(ranked, top_n=20)

    # サマリー
    console.print(f"\n[bold green]✅ {len(all_stocks)}銘柄中 {len(ranked)}銘柄が候補として選出[/bold green]")
    console.print(f"[dim]結果は screening_results テーブルに保存済み[/dim]\n")

    conn.close()


if __name__ == '__main__':
    run_screening()
