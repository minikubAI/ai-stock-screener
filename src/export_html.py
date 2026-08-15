"""
スクリーニング結果を site/index.html として書き出す

GitHub Pages で公開するための静的HTMLを生成
"""

import sqlite3
import os
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


def load_results() -> list[dict]:
    conn = get_db()
    c = conn.cursor()
    c.execute('''
        SELECT
            sr.rank, sr.ticker, co.name, co.sector,
            sr.per, sr.pbr, sr.roe, sr.dividend_yield,
            sr.op_growth, sr.equity_ratio, sr.market_cap, sr.score,
            p.close as price, sr.screened_at
        FROM screening_results sr
        JOIN companies co ON sr.ticker = co.ticker
        LEFT JOIN (
            SELECT ticker, close FROM prices
            WHERE (ticker, date) IN (SELECT ticker, MAX(date) FROM prices GROUP BY ticker)
        ) p ON sr.ticker = p.ticker
        WHERE sr.screened_at = (SELECT MAX(screened_at) FROM screening_results)
        ORDER BY sr.rank
    ''')
    rows = c.fetchall()
    cols = [d[0] for d in c.description]
    conn.close()
    return [dict(zip(cols, row)) for row in rows]


def fmt(val, suffix="", decimals=1):
    if val is None:
        return '<span class="text-gray-400">—</span>'
    return f"{val:.{decimals}f}{suffix}"


def score_bar(score):
    pct = min(int(score), 100)
    color = "bg-emerald-500" if pct >= 60 else "bg-amber-400" if pct >= 40 else "bg-sky-400"
    return f'''
        <div class="flex items-center gap-2">
          <div class="w-20 bg-gray-200 rounded-full h-2">
            <div class="{color} h-2 rounded-full" style="width:{pct}%"></div>
          </div>
          <span class="font-semibold">{score:.1f}</span>
        </div>'''


def generate_html(stocks: list[dict]) -> str:
    if not stocks:
        screened_at = "—"
        rows_html = '<tr><td colspan="10" class="text-center py-8 text-gray-400">データがありません</td></tr>'
    else:
        screened_at = stocks[0].get('screened_at', '')[:16].replace('T', ' ')
        row_parts = []
        for s in stocks:
            medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(s['rank'], f"#{s['rank']}")
            row_parts.append(f"""
            <tr class="border-b border-gray-100 hover:bg-gray-50 transition-colors">
              <td class="py-3 px-4 text-center font-bold text-lg">{medal}</td>
              <td class="py-3 px-4">
                <span class="font-mono text-sm bg-gray-100 px-2 py-0.5 rounded">{s['ticker']}</span>
              </td>
              <td class="py-3 px-4 font-medium">{s['name']}</td>
              <td class="py-3 px-4 text-right font-mono">
                {'¥{:,.0f}'.format(s['price']) if s['price'] else '<span class="text-gray-400">—</span>'}
              </td>
              <td class="py-3 px-4 text-right">{fmt(s['per'], '×')}</td>
              <td class="py-3 px-4 text-right">{fmt(s['pbr'], '×', 2)}</td>
              <td class="py-3 px-4 text-right">{fmt(s['roe'], '%')}</td>
              <td class="py-3 px-4 text-right">{fmt(s['dividend_yield'], '%')}</td>
              <td class="py-3 px-4 text-right">
                {'¥{:,.0f}億'.format(s['market_cap']) if s['market_cap'] else '<span class="text-gray-400">—</span>'}
              </td>
              <td class="py-3 px-4">{score_bar(s['score'])}</td>
            </tr>""")
        rows_html = "\n".join(row_parts)

    total = len(stocks)
    updated = datetime.now().strftime("%Y年%m月%d日 %H:%M")

    return f"""<!DOCTYPE html>
<html lang="ja">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>AI バリュー株スクリーナー</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <style>
    @import url('https://fonts.googleapis.com/css2?family=Noto+Sans+JP:wght@400;500;700&display=swap');
    body {{ font-family: 'Noto Sans JP', sans-serif; }}
  </style>
</head>
<body class="bg-gray-50 min-h-screen">

  <!-- Header -->
  <header class="bg-white border-b border-gray-200 shadow-sm">
    <div class="max-w-7xl mx-auto px-4 py-5 flex items-center justify-between">
      <div>
        <h1 class="text-2xl font-bold text-gray-900">📈 AI バリュー株スクリーナー</h1>
        <p class="text-sm text-gray-500 mt-0.5">EDINET × Yahoo Finance × Claude AI</p>
      </div>
      <div class="text-right text-sm text-gray-500">
        <div>スクリーニング実施: <span class="font-medium text-gray-700">{screened_at}</span></div>
        <div>ページ更新: <span class="font-medium text-gray-700">{updated}</span></div>
      </div>
    </div>
  </header>

  <main class="max-w-7xl mx-auto px-4 py-8">

    <!-- Stats -->
    <div class="grid grid-cols-2 md:grid-cols-4 gap-4 mb-8">
      <div class="bg-white rounded-xl p-5 shadow-sm border border-gray-100">
        <div class="text-3xl font-bold text-emerald-600">{total}</div>
        <div class="text-sm text-gray-500 mt-1">候補銘柄数</div>
      </div>
      <div class="bg-white rounded-xl p-5 shadow-sm border border-gray-100">
        <div class="text-3xl font-bold text-sky-600">{'—' if not stocks else f"{stocks[0]['per']:.1f}×"}</div>
        <div class="text-sm text-gray-500 mt-1">最低PER（1位）</div>
      </div>
      <div class="bg-white rounded-xl p-5 shadow-sm border border-gray-100">
        <div class="text-3xl font-bold text-violet-600">{'—' if not stocks else f"{stocks[0]['roe']:.1f}%"}</div>
        <div class="text-sm text-gray-500 mt-1">首位ROE</div>
      </div>
      <div class="bg-white rounded-xl p-5 shadow-sm border border-gray-100">
        <div class="text-3xl font-bold text-amber-600">{'—' if not stocks else f"{max(s['dividend_yield'] for s in stocks if s['dividend_yield']):.1f}%"}</div>
        <div class="text-sm text-gray-500 mt-1">最高配当利回り</div>
      </div>
    </div>

    <!-- Criteria -->
    <div class="bg-white rounded-xl p-5 shadow-sm border border-gray-100 mb-8">
      <h2 class="font-bold text-gray-700 mb-3">🔍 スクリーニング条件</h2>
      <div class="flex flex-wrap gap-2 text-sm">
        <span class="bg-blue-50 text-blue-700 px-3 py-1 rounded-full">PER 3〜15倍</span>
        <span class="bg-blue-50 text-blue-700 px-3 py-1 rounded-full">PBR ≤ 1.5倍</span>
        <span class="bg-green-50 text-green-700 px-3 py-1 rounded-full">ROE ≥ 8%</span>
        <span class="bg-green-50 text-green-700 px-3 py-1 rounded-full">配当利回り ≥ 2.5%</span>
        <span class="bg-purple-50 text-purple-700 px-3 py-1 rounded-full">自己資本比率 ≥ 40%</span>
        <span class="bg-orange-50 text-orange-700 px-3 py-1 rounded-full">時価総額 100〜5,000億円</span>
      </div>
    </div>

    <!-- Table -->
    <div class="bg-white rounded-xl shadow-sm border border-gray-100 overflow-hidden">
      <div class="px-6 py-4 border-b border-gray-100">
        <h2 class="font-bold text-gray-900 text-lg">🏆 スクリーニング結果</h2>
      </div>
      <div class="overflow-x-auto">
        <table class="w-full text-sm">
          <thead>
            <tr class="bg-gray-50 text-gray-600 text-xs uppercase tracking-wide">
              <th class="py-3 px-4 text-center">順位</th>
              <th class="py-3 px-4 text-left">コード</th>
              <th class="py-3 px-4 text-left">企業名</th>
              <th class="py-3 px-4 text-right">株価</th>
              <th class="py-3 px-4 text-right">PER</th>
              <th class="py-3 px-4 text-right">PBR</th>
              <th class="py-3 px-4 text-right">ROE</th>
              <th class="py-3 px-4 text-right">配当</th>
              <th class="py-3 px-4 text-right">時価総額</th>
              <th class="py-3 px-4 text-left">スコア</th>
            </tr>
          </thead>
          <tbody>
            {rows_html}
          </tbody>
        </table>
      </div>
    </div>

    <!-- Disclaimer -->
    <p class="text-xs text-gray-400 mt-6 text-center">
      本ページは情報提供を目的としており、投資勧誘を意図するものではありません。
      投資判断はご自身の責任で行ってください。データはEDINET・Yahoo Financeより取得。
    </p>

  </main>
</body>
</html>"""


def main():
    base_dir = os.path.join(os.path.dirname(__file__), '..')
    out_path = os.path.join(base_dir, 'docs', 'index.html')
    os.makedirs(os.path.dirname(out_path), exist_ok=True)

    print("📄 スクリーニング結果を読み込み中...")
    stocks = load_results()
    print(f"   → {len(stocks)}銘柄")

    html = generate_html(stocks)
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(html)

    print(f"✅ 生成完了: {out_path}")


if __name__ == '__main__':
    main()
