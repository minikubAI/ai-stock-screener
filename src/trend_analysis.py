"""
トレンド分析スクリプト

過去の財務データの推移から、各銘柄の成長トレンドを判定。
- 売上高の推移
- 営業利益の推移
- ROEの推移
- EPSの推移
を回帰分析し、改善中/悪化中をスコア化。

使い方:
  python src/trend_analysis.py
"""

import sqlite3
import os
import yaml
import numpy as np
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


def linear_trend(values: list[float]) -> dict:
    """
    時系列データの線形回帰でトレンドを判定

    Returns:
        slope: 傾き（正=改善、負=悪化）
        r_squared: 決定係数（1に近いほどトレンドが明確）
        direction: 'up' / 'down' / 'flat'
        strength: 0-100のスコア
    """
    if len(values) < 2:
        return {'slope': 0, 'r_squared': 0, 'direction': 'flat', 'strength': 0}

    n = len(values)
    x = np.arange(n, dtype=float)
    y = np.array(values, dtype=float)

    # 線形回帰
    x_mean = x.mean()
    y_mean = y.mean()

    ss_xy = np.sum((x - x_mean) * (y - y_mean))
    ss_xx = np.sum((x - x_mean) ** 2)
    ss_yy = np.sum((y - y_mean) ** 2)

    if ss_xx == 0 or ss_yy == 0:
        return {'slope': 0, 'r_squared': 0, 'direction': 'flat', 'strength': 0}

    slope = ss_xy / ss_xx
    r_squared = (ss_xy ** 2) / (ss_xx * ss_yy)

    # 傾きを基準値で正規化（%変化/年に変換）
    base = abs(y_mean) if y_mean != 0 else 1
    normalized_slope = (slope / base) * 100  # 年あたりの%変化

    # 方向判定
    if normalized_slope > 3:
        direction = 'up'
    elif normalized_slope < -3:
        direction = 'down'
    else:
        direction = 'flat'

    # 強度スコア（0-100）
    # 傾きの大きさ × トレンドの明確さ（R²）
    raw_strength = min(abs(normalized_slope), 50) * 2  # 最大100
    strength = raw_strength * r_squared  # R²で重み付け

    return {
        'slope': round(normalized_slope, 2),
        'r_squared': round(r_squared, 3),
        'direction': direction,
        'strength': round(strength, 1)
    }


def calculate_growth_rates(values: list[float]) -> dict:
    """
    年次成長率を計算

    Returns:
        yoy_rates: 各年の前年比成長率リスト
        avg_growth: 平均成長率
        latest_growth: 直近の成長率
        consecutive_growth: 連続増収/増益の年数
    """
    if len(values) < 2:
        return {'yoy_rates': [], 'avg_growth': 0, 'latest_growth': 0, 'consecutive_growth': 0}

    rates = []
    for i in range(1, len(values)):
        if values[i - 1] != 0 and values[i - 1] is not None:
            rate = (values[i] - values[i - 1]) / abs(values[i - 1]) * 100
            rates.append(round(rate, 2))
        else:
            rates.append(0)

    # 連続成長カウント（直近から遡る）
    consecutive = 0
    for r in reversed(rates):
        if r > 0:
            consecutive += 1
        else:
            break

    return {
        'yoy_rates': rates,
        'avg_growth': round(np.mean(rates), 2) if rates else 0,
        'latest_growth': rates[-1] if rates else 0,
        'consecutive_growth': consecutive
    }


def analyze_all_stocks(conn) -> list[dict]:
    """
    全銘柄のトレンド分析を実行
    """
    c = conn.cursor()

    # 2年分以上のデータがある銘柄を対象
    c.execute('''
        SELECT DISTINCT ticker FROM financials
        GROUP BY ticker
        HAVING COUNT(DISTINCT fiscal_year) >= 2
        ORDER BY ticker
    ''')
    tickers = [row[0] for row in c.fetchall()]

    console.print(f"  対象: {len(tickers)}銘柄（2年分以上のデータあり）")

    results = []

    for ticker in tickers:
        # 財務データを年度順に取得
        c.execute('''
            SELECT fiscal_year, revenue, operating_income, net_income,
                   total_assets, total_equity, eps
            FROM financials
            WHERE ticker = ?
            ORDER BY fiscal_year ASC
        ''', (ticker,))

        rows = c.fetchall()
        if len(rows) < 2:
            continue

        years = [r[0] for r in rows]

        # 各指標の時系列を抽出（Noneを除外しつつ）
        revenue_series = [r[1] for r in rows if r[1] is not None]
        op_income_series = [r[2] for r in rows if r[2] is not None]
        net_income_series = [r[3] for r in rows if r[3] is not None]
        equity_series = [r[5] for r in rows if r[5] is not None]
        eps_series = [r[6] for r in rows if r[6] is not None]

        # ROE時系列を計算
        roe_series = []
        for r in rows:
            ni, eq = r[3], r[5]
            if ni is not None and eq is not None and eq > 0:
                roe_series.append(ni / eq * 100)

        # トレンド分析
        rev_trend = linear_trend(revenue_series)
        op_trend = linear_trend(op_income_series)
        ni_trend = linear_trend(net_income_series)
        roe_trend = linear_trend(roe_series)
        eps_trend = linear_trend(eps_series)

        # 成長率分析
        rev_growth = calculate_growth_rates(revenue_series)
        op_growth = calculate_growth_rates(op_income_series)

        # 企業名を取得
        c.execute("SELECT name, sector FROM companies WHERE ticker = ?", (ticker,))
        company = c.fetchone()
        name = company[0] if company else ticker
        sector = company[1] if company else ''

        # 総合トレンドスコア（0-100）
        # 売上トレンド25% + 営業利益トレンド30% + ROEトレンド25% + EPSトレンド20%
        total_score = (
            rev_trend['strength'] * 0.25 +
            op_trend['strength'] * 0.30 +
            roe_trend['strength'] * 0.25 +
            eps_trend['strength'] * 0.20
        )

        # 連続増収増益ボーナス
        consecutive_bonus = min(rev_growth['consecutive_growth'] * 5, 15)
        total_score = min(total_score + consecutive_bonus, 100)

        results.append({
            'ticker': ticker,
            'name': name,
            'sector': sector,
            'data_years': len(rows),
            # トレンド
            'rev_trend': rev_trend['direction'],
            'rev_slope': rev_trend['slope'],
            'op_trend': op_trend['direction'],
            'op_slope': op_trend['slope'],
            'roe_trend': roe_trend['direction'],
            'roe_slope': roe_trend['slope'],
            'eps_trend': eps_trend['direction'],
            # 成長率
            'rev_avg_growth': rev_growth['avg_growth'],
            'op_avg_growth': op_growth['avg_growth'],
            'rev_latest_growth': rev_growth['latest_growth'],
            'op_latest_growth': op_growth['latest_growth'],
            'consecutive_rev_growth': rev_growth['consecutive_growth'],
            'consecutive_op_growth': op_growth['consecutive_growth'],
            # スコア
            'trend_score': round(total_score, 1),
        })

    # スコア順にソート
    results.sort(key=lambda x: x['trend_score'], reverse=True)
    return results


def save_trend_results(conn, results: list[dict]):
    """トレンド分析結果をDBに保存"""
    c = conn.cursor()

    # トレンドテーブルがなければ作成
    c.execute('''
        CREATE TABLE IF NOT EXISTS trend_scores (
            ticker TEXT PRIMARY KEY,
            analyzed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            data_years INTEGER,
            rev_trend TEXT,
            rev_slope REAL,
            op_trend TEXT,
            op_slope REAL,
            roe_trend TEXT,
            roe_slope REAL,
            eps_trend TEXT,
            rev_avg_growth REAL,
            op_avg_growth REAL,
            rev_latest_growth REAL,
            op_latest_growth REAL,
            consecutive_rev_growth INTEGER,
            consecutive_op_growth INTEGER,
            trend_score REAL
        )
    ''')

    for r in results:
        c.execute('''
            INSERT OR REPLACE INTO trend_scores
            (ticker, data_years, rev_trend, rev_slope, op_trend, op_slope,
             roe_trend, roe_slope, eps_trend, rev_avg_growth, op_avg_growth,
             rev_latest_growth, op_latest_growth,
             consecutive_rev_growth, consecutive_op_growth, trend_score)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            r['ticker'], r['data_years'],
            r['rev_trend'], r['rev_slope'],
            r['op_trend'], r['op_slope'],
            r['roe_trend'], r['roe_slope'],
            r['eps_trend'],
            r['rev_avg_growth'], r['op_avg_growth'],
            r['rev_latest_growth'], r['op_latest_growth'],
            r['consecutive_rev_growth'], r['consecutive_op_growth'],
            r['trend_score'],
        ))

    conn.commit()


def display_results(results: list[dict], top_n: int = 30):
    """リッチテーブルで結果表示"""

    # トレンド方向のアイコン
    def arrow(direction):
        if direction == 'up': return '[green]↑[/green]'
        elif direction == 'down': return '[red]↓[/red]'
        else: return '[dim]→[/dim]'

    table = Table(title=f"📈 トレンド分析 TOP {top_n}")
    table.add_column("#", style="dim", width=4)
    table.add_column("コード", style="bold", width=6)
    table.add_column("企業名", max_width=18)
    table.add_column("年数", justify="center", width=4)
    table.add_column("売上", justify="center", width=6)
    table.add_column("営利", justify="center", width=6)
    table.add_column("ROE", justify="center", width=6)
    table.add_column("売上成長", justify="right", width=8)
    table.add_column("連続増収", justify="center", width=6)
    table.add_column("スコア", justify="right", style="bold", width=7)

    for i, r in enumerate(results[:top_n], 1):
        growth_style = "green" if r['rev_avg_growth'] > 0 else "red"
        score_style = "bold green" if r['trend_score'] >= 50 else "bold"

        table.add_row(
            str(i),
            r['ticker'],
            r['name'][:18],
            str(r['data_years']),
            arrow(r['rev_trend']),
            arrow(r['op_trend']),
            arrow(r['roe_trend']),
            f"[{growth_style}]{r['rev_avg_growth']:+.1f}%[/{growth_style}]",
            f"{r['consecutive_rev_growth']}年" if r['consecutive_rev_growth'] > 0 else "-",
            f"[{score_style}]{r['trend_score']:.1f}[/{score_style}]",
        )

    console.print(table)

    # サマリー
    up_count = sum(1 for r in results if r['rev_trend'] == 'up')
    down_count = sum(1 for r in results if r['rev_trend'] == 'down')
    flat_count = sum(1 for r in results if r['rev_trend'] == 'flat')

    console.print(f"\n  全体傾向: ↑改善 {up_count}社 / →横ばい {flat_count}社 / ↓悪化 {down_count}社")
    console.print(f"  3年連続増収: {sum(1 for r in results if r['consecutive_rev_growth'] >= 3)}社")
    console.print(f"  スコア50以上: {sum(1 for r in results if r['trend_score'] >= 50)}社")


def run():
    """メイン実行"""
    console.print("\n[bold]📈 トレンド分析実行[/bold]\n")

    conn = get_db()

    # 分析実行
    console.print("  1️⃣  過去データからトレンドを分析中...")
    results = analyze_all_stocks(conn)

    if not results:
        console.print("[red]  ❌ 分析対象の銘柄がありません[/red]")
        console.print("  先に fetch_all_stocks.py を実行してください")
        conn.close()
        return

    # 保存
    console.print(f"  2️⃣  {len(results)}銘柄の分析結果を保存中...")
    save_trend_results(conn, results)

    # 表示
    console.print(f"  3️⃣  結果表示\n")
    display_results(results, top_n=30)

    conn.close()
    console.print(f"\n[green]✅ トレンド分析完了: {len(results)}銘柄[/green]\n")


if __name__ == '__main__':
    run()
