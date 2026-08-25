"""
sitemap.xml 自動生成スクリプト
docs/stocks/ 内の全HTMLファイルを走査して docs/sitemap.xml を更新する。

使い方:
  python src/generate_sitemap.py
"""

import os

BASE = 'https://minikubai.github.io/ai-stock-screener'
BASE_DIR = os.path.join(os.path.dirname(__file__), '..')
STOCKS_DIR = os.path.join(BASE_DIR, 'docs', 'stocks')
SITEMAP_PATH = os.path.join(BASE_DIR, 'docs', 'sitemap.xml')


def generate():
    tickers = sorted(
        f.replace('.html', '')
        for f in os.listdir(STOCKS_DIR)
        if f.endswith('.html') and f != 'template.html'
    )

    stock_urls = '\n'.join(
        f'  <url>\n    <loc>{BASE}/stocks/{t}.html</loc>\n'
        f'    <changefreq>daily</changefreq>\n    <priority>0.7</priority>\n  </url>'
        for t in tickers
    )

    sitemap = f'''<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url>
    <loc>{BASE}/</loc>
    <changefreq>daily</changefreq>
    <priority>1.0</priority>
  </url>
  <url>
    <loc>{BASE}/en.html</loc>
    <changefreq>daily</changefreq>
    <priority>1.0</priority>
  </url>
  <url>
    <loc>{BASE}/guide.html</loc>
    <changefreq>monthly</changefreq>
    <priority>0.8</priority>
  </url>
{stock_urls}
</urlset>'''

    with open(SITEMAP_PATH, 'w', encoding='utf-8') as f:
        f.write(sitemap)
    print(f'✅ sitemap.xml 更新完了 ({len(tickers)} 銘柄ページ)')


if __name__ == '__main__':
    generate()
