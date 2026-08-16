"""
銘柄詳細ページ自動生成スクリプト

スクリーニング結果のTOP銘柄について、
テンプレートHTMLにデータを埋め込んで静的ページを生成。

使い方:
  python src/generate_pages.py
"""

import sqlite3
import os
import json
import yaml
from datetime import datetime

def get_config():
    config_path = os.path.join(os.path.dirname(__file__), '..', 'config', 'settings.yaml')
    with open(config_path, 'r') as f:
        return yaml.safe_load(f)

def get_db():
    config = get_config()
    db_path = os.path.join(os.path.dirname(__file__), '..', config['database']['path'])
    return sqlite3.connect(db_path)


def format_number(n, unit=''):
    """数値を読みやすく整形（yfinanceの値は円単位）"""
    if n is None:
        return '-'
    abs_n = abs(n)
    if abs_n >= 1_000_000_000_000:  # 1兆以上
        return f'{n/1_000_000_000_000:,.1f}兆'
    elif abs_n >= 100_000_000:  # 1億以上
        return f'{n/100_000_000:,.0f}億'
    elif abs_n >= 10_000:  # 1万以上
        return f'{n/10_000:,.0f}万'
    else:
        return f'{n:,.0f}'


def classify_metric(name, value):
    """指標を良い/普通/悪いに分類"""
    if value is None:
        return ''
    if name == 'per':
        if value < 10: return 'good'
        elif value < 15: return ''
        else: return 'warn'
    elif name == 'pbr':
        if value < 1.0: return 'good'
        elif value < 1.5: return ''
        else: return 'warn'
    elif name == 'roe':
        if value > 15: return 'good'
        elif value > 8: return ''
        else: return 'bad'
    return ''


def generate_insight(data):
    """スコアの根拠をニュアンスで伝える文章を生成（核心は伝えない）"""
    parts = []

    # 割安度
    per = data.get('per')
    pbr = data.get('pbr')
    if per and per < 10:
        parts.append(f'PER {per}倍は市場平均を大きく下回っており、利益に対して株価が割安な水準にある')
    if pbr and pbr < 1.0:
        parts.append(f'PBR {pbr}倍と純資産を下回る株価で、資産面からの下値余地は限定的と見られる')

    # 収益性
    roe = data.get('roe')
    if roe and roe > 20:
        parts.append(f'ROE {roe}%は非常に高い資本効率を示しており、株主資本を有効に活用できている')
    elif roe and roe > 10:
        parts.append(f'ROE {roe}%と安定した資本効率を維持している')

    # 配当
    dy = data.get('div_yield')
    if dy and dy > 4:
        parts.append(f'配当利回り{dy}%は高水準で、インカムゲインの観点からも注目に値する')
    elif dy and dy > 3:
        parts.append(f'配当利回り{dy}%と、安定した株主還元を行っている')

    # トレンド
    rev_trend = data.get('rev_trend')
    consec = data.get('consecutive_rev', 0)
    if rev_trend == 'up' and consec >= 3:
        parts.append(f'{consec}期連続の増収を達成しており、成長モメンタムが継続している')
    elif rev_trend == 'up':
        parts.append('直近の売上は増加傾向にあり、業績の方向性は前向き')
    elif rev_trend == 'down':
        parts.append('売上はやや減少傾向にあり、今後の業績動向には注意が必要')

    if not parts:
        return '複数の定量指標を総合的に評価した結果、現在のスクリーニング順位に位置しています。'

    return '。'.join(parts) + '。これらの要素を総合的に評価し、現在のスコアとなっています。'


def get_financial_history(conn, ticker):
    """過去の財務データを取得"""
    c = conn.cursor()
    c.execute('''
        SELECT fiscal_year, revenue, operating_income, net_income, eps,
               total_assets, total_equity
        FROM financials
        WHERE ticker = ?
        ORDER BY fiscal_year ASC
    ''', (ticker,))
    return c.fetchall()


def get_price_history(conn, ticker):
    """過去1年分の株価データを取得"""
    c = conn.cursor()
    c.execute('''
        SELECT date, close FROM prices
        WHERE ticker = ?
        ORDER BY date ASC
    ''', (ticker,))
    return c.fetchall()


def generate_price_svg(prices):
    """株価推移のSVGラインチャートを生成"""
    if not prices or len(prices) < 2:
        return '', 0, 0, 0

    closes = [p[1] for p in prices if p[1]]
    dates = [p[0] for p in prices if p[1]]
    if len(closes) < 2:
        return '', 0, 0, 0

    current = closes[-1]
    low = min(closes)
    high = max(closes)
    price_range = high - low if high > low else 1

    # SVGパス生成
    w, h = 680, 140
    pad_x, pad_y = 10, 10
    chart_w = w - pad_x * 2
    chart_h = h - pad_y * 2

    points = []
    for i, c in enumerate(closes):
        x = pad_x + (i / (len(closes) - 1)) * chart_w
        y = pad_y + (1 - (c - low) / price_range) * chart_h
        points.append(f'{x:.1f},{y:.1f}')

    path_d = 'M' + 'L'.join(points)

    # グラデーション塗りつぶし用
    fill_d = path_d + f'L{pad_x + chart_w:.1f},{pad_y + chart_h:.1f}L{pad_x:.1f},{pad_y + chart_h:.1f}Z'

    # 色: 上昇=緑、下落=赤
    color = '#1a8a5c' if closes[-1] >= closes[0] else '#c0392b'
    fill_color = '#edf7f1' if closes[-1] >= closes[0] else '#fdecea'

    # ラベル
    first_date = dates[0] if dates else ''
    last_date = dates[-1] if dates else ''

    svg = f'''<path d="{fill_d}" fill="{fill_color}" opacity="0.5"/>
<path d="{path_d}" fill="none" stroke="{color}" stroke-width="2" stroke-linejoin="round"/>
<circle cx="{points[-1].split(',')[0]}" cy="{points[-1].split(',')[1]}" r="4" fill="{color}"/>
<text x="{pad_x}" y="{h - 2}" font-size="10" fill="#888">{first_date}</text>
<text x="{w - pad_x}" y="{h - 2}" font-size="10" fill="#888" text-anchor="end">{last_date}</text>'''

    return svg, current, low, high


def generate_company_page(template, data, financials, price_history=None):
    """テンプレートにデータを埋め込んでHTMLを生成"""
    html = template

    # 株価データ処理
    price_svg = ''
    current_price = data.get('price', 0)
    price_low = 0
    price_high = 0
    price_change_str = ''
    change_class = ''
    price_date = ''

    if price_history and len(price_history) >= 2:
        svg_content, cur, low, high = generate_price_svg(price_history)
        price_svg = svg_content
        if cur:
            current_price = cur
        price_low = low
        price_high = high
        price_date = price_history[-1][0] if price_history else ''

        # 変動率計算（直近vs前日）
        if len(price_history) >= 2:
            prev = price_history[-2][1]
            if prev and prev > 0 and current_price:
                change_pct = (current_price - prev) / prev * 100
                change_val = current_price - prev
                if change_pct >= 0:
                    price_change_str = f'+¥{change_val:,.0f} (+{change_pct:.2f}%)'
                    change_class = 'up'
                else:
                    price_change_str = f'¥{change_val:,.0f} ({change_pct:.2f}%)'
                    change_class = 'down'

    # 基本情報
    replacements = {
        '{{TICKER}}': data.get('ticker', ''),
        '{{COMPANY_NAME}}': data.get('name', ''),
        '{{COMPANY_NAME_EN}}': data.get('name_en', data.get('name', '')),
        '{{SECTOR}}': data.get('sector', ''),
        '{{MARKET}}': data.get('market', '東証'),
        '{{MARKET_CAP}}': str(int(data['market_cap'])) if data.get('market_cap') else '-',
        '{{CURRENT_PRICE}}': f'{current_price:,.0f}' if current_price else '-',
        '{{PRICE_CHANGE}}': price_change_str or '-',
        '{{CHANGE_CLASS}}': change_class,
        '{{PRICE_DATE}}': price_date or '-',
        '{{PRICE_LOW}}': f'{price_low:,.0f}' if price_low else '-',
        '{{PRICE_HIGH}}': f'{price_high:,.0f}' if price_high else '-',
        '{{PER}}': f"{data['per']:.1f}" if data.get('per') else '-',
        '{{PBR}}': f"{data['pbr']:.2f}" if data.get('pbr') else '-',
        '{{ROE}}': f"{data['roe']:.1f}" if data.get('roe') else '-',
        '{{DIV_YIELD}}': f"{data['div_yield']:.1f}" if data.get('div_yield') else '-',
        '{{PER_CLASS}}': classify_metric('per', data.get('per')),
        '{{PBR_CLASS}}': classify_metric('pbr', data.get('pbr')),
        '{{ROE_CLASS}}': classify_metric('roe', data.get('roe')),
        '{{TOTAL_SCORE}}': f"{data.get('total_score', data.get('score', 0)):.0f}",
        '{{RANK}}': str(data.get('rank', '-')),
        '{{TOTAL}}': str(data.get('total_companies', '-')),
        '{{INSIGHT_TEXT}}': generate_insight(data),
    }

    # スコア内訳（簡易計算）
    per_score = max(0, min(100, (15 - (data.get('per') or 15)) * 10)) if data.get('per') else 50
    roe_score = min(100, (data.get('roe') or 0) * 5)
    safety_score = min(100, (data.get('equity_ratio') or 50))
    growth_score = data.get('trend_score', 30)

    replacements['{{VALUE_SCORE}}'] = str(int(per_score))
    replacements['{{PROFIT_SCORE}}'] = str(int(roe_score))
    replacements['{{SAFETY_SCORE}}'] = str(int(safety_score))
    replacements['{{GROWTH_SCORE}}'] = str(int(growth_score))

    for key, val in replacements.items():
        html = html.replace(key, val)

    # 財務テーブルのデータを動的に埋め込む
    if financials:
        fin_rows = []
        prev_rev = None
        for f in financials:
            fy, rev, op, ni, eps = f[0], f[1], f[2], f[3], f[4]
            if rev and prev_rev and prev_rev > 0:
                yoy = (rev - prev_rev) / abs(prev_rev) * 100
                yoy_str = f'{yoy:+.1f}%'
                yoy_cls = 'trend-up' if yoy > 0 else 'trend-down'
            else:
                yoy_str = '-'
                yoy_cls = ''
            prev_rev = rev

            fin_rows.append(f'''<tr>
                <td>{fy}</td>
                <td class="r">{format_number(rev)}</td>
                <td class="r">{format_number(op)}</td>
                <td class="r">{format_number(ni)}</td>
                <td class="r">{f"{eps:.1f}" if eps else "-"}</td>
                <td class="r"><span class="{yoy_cls}">{yoy_str}</span></td>
            </tr>''')

        # テーブルのサンプルデータを実データに置換
        html = html.replace(
            "tbody.innerHTML = sampleFinancials.map",
            f"// Real data injected\n// tbody.innerHTML = sampleFinancials.map"
        )

        # 直接テーブルにデータを注入するスクリプトを追加
        fin_script = f"""
<script>
document.getElementById('financials-body').innerHTML = `{''.join(fin_rows)}`;
</script>"""
        html = html.replace('</body>', fin_script + '\n</body>')

        # 棒グラフ用データ
        rev_values = [f[1] for f in financials if f[1]]
        op_values = [f[2] for f in financials if f[2]]
        fy_labels = [f[0] for f in financials if f[1]]

        if rev_values:
            max_rev = max(rev_values)

            # 適切な単位を選択
            if max_rev >= 1_000_000_000_000:
                divisor = 1_000_000_000_000
                unit = '兆'
            elif max_rev >= 100_000_000:
                divisor = 100_000_000
                unit = '億'
            else:
                divisor = 10_000
                unit = '万'

            # 静的HTMLとしてグラフを直接埋め込む（JSテンプレート不使用）
            bars_html = []
            for i, fy in enumerate(fy_labels):
                rv = rev_values[i] if i < len(rev_values) else 0
                ov = op_values[i] if i < len(op_values) else 0
                rh = max((rv / max_rev * 100), 3) if max_rev > 0 else 3
                oh = max((ov / max_rev * 100), 3) if max_rev > 0 else 3
                rv_disp = f'{rv/divisor:,.1f}'
                bars_html.append(f'''<div class="bar-group">
                    <div class="bar-val">{rv_disp}{unit}</div>
                    <div class="bar-group-inner">
                      <div class="bar bar-rev" style="height:{rh:.0f}%"></div>
                      <div class="bar bar-op" style="height:{oh:.0f}%"></div>
                    </div>
                    <div class="bar-label">{fy}</div>
                  </div>''')

            chart_inject = f"""
<script>
document.getElementById('revenue-chart').innerHTML = `{''.join(bars_html)}`;
</script>"""
            html = html.replace('</body>', chart_inject + '\n</body>')

    # 株価チャートSVGを注入
    if price_svg:
        svg_inject = f"""
<script>
document.getElementById('price-svg').innerHTML = `{price_svg}`;
</script>"""
        html = html.replace('</body>', svg_inject + '\n</body>')

    return html


def run(top_n=10):
    """メイン実行"""
    print(f"📄 銘柄詳細ページ生成（TOP {top_n}）")
    print("=" * 50)

    conn = get_db()
    c = conn.cursor()

    # テンプレート読み込み
    template_path = os.path.join(os.path.dirname(__file__), '..', 'site', 'stocks', 'template.html')
    with open(template_path, 'r', encoding='utf-8') as f:
        template = f.read()

    # 最新スクリーニング結果を取得（最新実行分のみ）
    c.execute("SELECT MAX(screened_at) FROM screening_results")
    latest_run = c.fetchone()[0]
    c.execute('''
        SELECT sr.ticker, c.name, c.sector, sr.per, sr.pbr, sr.roe,
               sr.dividend_yield, sr.equity_ratio, sr.market_cap,
               sr.score, sr.rank
        FROM screening_results sr
        JOIN companies c ON sr.ticker = c.ticker
        WHERE sr.screened_at = ?
        ORDER BY sr.rank ASC LIMIT ?
    ''', (latest_run, top_n))
    rows = c.fetchall()
    cols = ['ticker','name','sector','per','pbr','roe',
            'div_yield','equity_ratio','market_cap','score','rank']
    stocks = [dict(zip(cols, r)) for r in rows]

    if not stocks:
        print("❌ スクリーニング結果がありません")
        return

    total_companies = len(stocks)

    # 出力先: site/stocks/ と docs/stocks/ の両方に出力
    site_dir = os.path.join(os.path.dirname(__file__), '..', 'site', 'stocks')
    docs_dir = os.path.join(os.path.dirname(__file__), '..', 'docs', 'stocks')
    os.makedirs(site_dir, exist_ok=True)
    os.makedirs(docs_dir, exist_ok=True)
    output_dir = site_dir  # 一次出力先

    generated = []

    for stock in stocks[:top_n]:
        ticker = stock['ticker']
        name = stock.get('name', ticker)

        print(f"  📄 {ticker} {name}...")

        # トレンドデータ取得
        c.execute('''
            SELECT trend_score, rev_trend, op_trend, roe_trend,
                   consecutive_rev_growth
            FROM trend_scores WHERE ticker = ?
        ''', (ticker,))
        trend = c.fetchone()
        if trend:
            stock['trend_score'] = trend[0] or 0
            stock['rev_trend'] = trend[1] or 'flat'
            stock['op_trend'] = trend[2] or 'flat'
            stock['consecutive_rev'] = trend[4] or 0
        else:
            stock['trend_score'] = 0
            stock['rev_trend'] = 'flat'
            stock['op_trend'] = 'flat'
            stock['consecutive_rev'] = 0

        stock['total_companies'] = total_companies

        # div_yieldのキー名統一
        if 'dividend_yield' in stock and 'div_yield' not in stock:
            stock['div_yield'] = stock['dividend_yield']

        # total_scoreがなければscoreを使用
        if 'total_score' not in stock:
            stock['total_score'] = stock.get('score', 0)

        # 過去の財務データ
        financials = get_financial_history(conn, ticker)

        # 株価データ
        price_history = get_price_history(conn, ticker)

        # ページ生成
        html = generate_company_page(template, stock, financials, price_history)

        # ファイル出力（site/ と docs/ の両方）
        for out_dir in [site_dir, docs_dir]:
            page_path = os.path.join(out_dir, f'{ticker}.html')
            with open(page_path, 'w', encoding='utf-8') as f:
                f.write(html)

        generated.append({'ticker': ticker, 'name': name, 'path': f'stocks/{ticker}.html'})
        print(f"    ✅ {ticker}.html (site/ & docs/)")

    # リンク情報をJSONで出力
    links_path = os.path.join(os.path.dirname(__file__), '..', 'data', 'stock_pages.json')
    with open(links_path, 'w', encoding='utf-8') as f:
        json.dump(generated, f, ensure_ascii=False, indent=2)

    conn.close()

    print(f"\n{'=' * 50}")
    print(f"✅ {len(generated)}ページ生成完了")
    print(f"   出力先: site/stocks/ & docs/stocks/")
    print(f"   GitHub Pagesに反映するには git add, commit, push してください")


if __name__ == '__main__':
    run(top_n=55)
