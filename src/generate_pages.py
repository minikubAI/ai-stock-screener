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


def format_number(n, unit='百万'):
    """数値を読みやすく整形"""
    if n is None:
        return '-'
    if abs(n) >= 1_000_000_000:
        return f'{n/1_000_000_000:,.1f}兆'
    elif abs(n) >= 100_000_000:
        return f'{n/100_000_000:,.0f}億'
    elif abs(n) >= 10_000:
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


def generate_company_page(template, data, financials):
    """テンプレートにデータを埋め込んでHTMLを生成"""
    html = template

    # 基本情報
    replacements = {
        '{{TICKER}}': data.get('ticker', ''),
        '{{COMPANY_NAME}}': data.get('name', ''),
        '{{COMPANY_NAME_EN}}': data.get('name_en', data.get('name', '')),
        '{{SECTOR}}': data.get('sector', ''),
        '{{MARKET}}': data.get('market', '東証'),
        '{{MARKET_CAP}}': str(int(data['market_cap'])) if data.get('market_cap') else '-',
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
            chart_data = []
            for i, fy in enumerate(fy_labels):
                rv = rev_values[i] if i < len(rev_values) else 0
                ov = op_values[i] if i < len(op_values) else 0
                rh = (rv / max_rev * 100) if max_rev > 0 else 0
                oh = (ov / max_rev * 100) if max_rev > 0 else 0
                chart_data.append(
                    f'{{"fy":"{fy}","rev":{int(rv/100000000)},"op":{int(ov/100000000)},"rh":{rh:.0f},"oh":{oh:.0f}}}'
                )

            chart_script = f"""
<script>
const cd = [{','.join(chart_data)}];
const ce = document.getElementById('revenue-chart');
if(ce) ce.innerHTML = cd.map(d =>
  `<div class="bar-group">
    <div class="bar-val">${"${d.rev}"}億</div>
    <div style="display:flex;gap:3px;align-items:flex-end;width:100%;height:100px">
      <div class="bar bar-rev" style="height:${"${d.rh}"}%;flex:1"></div>
      <div class="bar bar-op" style="height:${"${d.oh}"}%;flex:1"></div>
    </div>
    <div class="bar-label">${"${d.fy}"}</div>
  </div>`
).join('');
</script>"""
            html = html.replace('</body>', chart_script + '\n</body>')

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

    # 最新スクリーニング結果を取得
    report_path = os.path.join(os.path.dirname(__file__), '..', 'data', 'latest_report.json')
    if os.path.exists(report_path):
        with open(report_path, 'r', encoding='utf-8') as f:
            report = json.load(f)
        stocks = report.get('screening_results', [])
    else:
        # レポートがない場合はDBから直接取得
        c.execute('''
            SELECT sr.ticker, c.name, c.sector, sr.per, sr.pbr, sr.roe,
                   sr.dividend_yield, sr.equity_ratio, sr.market_cap,
                   sr.score, sr.rank
            FROM screening_results sr
            JOIN companies c ON sr.ticker = c.ticker
            ORDER BY sr.rank ASC LIMIT ?
        ''', (top_n,))
        rows = c.fetchall()
        cols = ['ticker','name','sector','per','pbr','roe',
                'div_yield','equity_ratio','market_cap','score','rank']
        stocks = [dict(zip(cols, r)) for r in rows]

    if not stocks:
        print("❌ スクリーニング結果がありません")
        return

    total_companies = len(stocks)
    output_dir = os.path.join(os.path.dirname(__file__), '..', 'site', 'stocks')
    os.makedirs(output_dir, exist_ok=True)

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

        # ページ生成
        html = generate_company_page(template, stock, financials)

        # ファイル出力
        page_path = os.path.join(output_dir, f'{ticker}.html')
        with open(page_path, 'w', encoding='utf-8') as f:
            f.write(html)

        generated.append({'ticker': ticker, 'name': name, 'path': f'stocks/{ticker}.html'})
        print(f"    ✅ {page_path}")

    # インデックスのスクリーニング結果テーブルにリンクを追加するためのJSON
    links_path = os.path.join(os.path.dirname(__file__), '..', 'data', 'stock_pages.json')
    with open(links_path, 'w', encoding='utf-8') as f:
        json.dump(generated, f, ensure_ascii=False, indent=2)

    conn.close()

    print(f"\n{'=' * 50}")
    print(f"✅ {len(generated)}ページ生成完了")
    print(f"   出力先: {output_dir}/")


if __name__ == '__main__':
    run(top_n=10)
