"""
スクリーニング結果をもとにAIが投資分析記事を生成するスクリプト

最新スクリーニング結果をDBから読み込み → Anthropic API でレポート生成 → ファイル保存
"""

import sqlite3
import os
import yaml
from datetime import datetime
import anthropic

def get_config():
    config_path = os.path.join(os.path.dirname(__file__), '..', 'config', 'settings.yaml')
    with open(config_path, 'r') as f:
        return yaml.safe_load(f)

def get_db():
    config = get_config()
    db_path = os.path.join(os.path.dirname(__file__), '..', config['database']['path'])
    return sqlite3.connect(db_path)


def load_latest_results() -> list[dict]:
    """最新スクリーニング結果をDBから取得"""
    conn = get_db()
    c = conn.cursor()

    c.execute('''
        SELECT
            sr.rank,
            sr.ticker,
            co.name,
            co.sector,
            sr.per,
            sr.pbr,
            sr.roe,
            sr.dividend_yield,
            sr.op_growth,
            sr.equity_ratio,
            sr.market_cap,
            sr.score,
            f.revenue,
            f.operating_income,
            f.net_income,
            f.total_equity,
            f.fiscal_year,
            p.close as current_price,
            sr.screened_at
        FROM screening_results sr
        JOIN companies co ON sr.ticker = co.ticker
        JOIN financials f ON sr.ticker = f.ticker
        INNER JOIN (
            SELECT ticker, close
            FROM prices
            WHERE (ticker, date) IN (
                SELECT ticker, MAX(date) FROM prices GROUP BY ticker
            )
        ) p ON sr.ticker = p.ticker
        WHERE sr.screened_at = (SELECT MAX(screened_at) FROM screening_results)
          AND f.fiscal_year = (
              SELECT MAX(f2.fiscal_year) FROM financials f2 WHERE f2.ticker = f.ticker
          )
        ORDER BY sr.rank
    ''')

    rows = c.fetchall()
    cols = [d[0] for d in c.description]
    conn.close()
    return [dict(zip(cols, row)) for row in rows]


def format_oku(value) -> str:
    """円を億円表記に変換"""
    if value is None:
        return "N/A"
    return f"{value / 1e8:,.1f}億円"


def build_prompt(stocks: list[dict], screened_at: str) -> str:
    """Claude へ渡すプロンプトを構築"""
    date_str = screened_at[:10]

    lines = [
        f"以下は {date_str} 時点の日本株バリュー株スクリーニング結果（上位{len(stocks)}銘柄）です。",
        "スクリーニング条件: PER 3〜15倍、PBR 1.5倍以下、ROE 8%以上、配当利回り 2.5%以上、自己資本比率 40%以上",
        "スコアは PER逆数(25%)・PBR逆数(20%)・ROE(20%)・配当利回り(20%)・営業利益成長率(15%) の加重合計で正規化",
        "",
        "【スクリーニング結果】",
    ]

    for s in stocks:
        rev = format_oku(s.get('revenue'))
        op  = format_oku(s.get('operating_income'))
        ni  = format_oku(s.get('net_income'))
        eq  = format_oku(s.get('total_equity'))
        dy  = f"{s['dividend_yield']:.1f}%" if s['dividend_yield'] else "N/A"
        og  = f"{s['op_growth']:.1f}%" if s['op_growth'] else "N/A"
        er  = f"{s['equity_ratio']:.1f}%" if s['equity_ratio'] else "N/A"
        mc  = f"{s['market_cap']:.0f}億円" if s['market_cap'] else "N/A"
        price = f"¥{s['current_price']:,.0f}" if s['current_price'] else "N/A"

        lines += [
            f"",
            f"■ 第{s['rank']}位 [{s['ticker']}] {s['name']}",
            f"  株価: {price}  時価総額: {mc}  スコア: {s['score']:.1f}",
            f"  PER: {s['per']:.1f}倍  PBR: {s['pbr']:.2f}倍  ROE: {s['roe']:.1f}%",
            f"  配当利回り: {dy}  営業利益成長率: {og}  自己資本比率: {er}",
            f"  売上高: {rev}  営業利益: {op}  純利益: {ni}  純資産: {eq}",
            f"  セクター: {s['sector'] or '不明'}  対象決算期: {s['fiscal_year']}年度",
        ]

    lines += [
        "",
        "---",
        "上記データをもとに、以下の構成で日本語の投資分析レポートを作成してください。",
        "",
        "## 出力フォーマット（Markdown）",
        "",
        "# バリュー株スクリーニングレポート — {date}".replace("{date}", date_str),
        "",
        "## 概況サマリー（3〜5文）",
        "- スクリーニング全体の傾向、注目ポイントを簡潔に",
        "",
        "## 注目銘柄 TOP3 詳細分析",
        "各銘柄について以下を記述（各200〜300字）：",
        "- 事業概要（知名度に関わらず説明）",
        "- バリュー評価の根拠（指標の強みを具体的に）",
        "- 投資上の留意点・リスク",
        "",
        "## 全候補銘柄一覧（表形式）",
        "| 順位 | コード | 企業名 | 株価 | PER | PBR | ROE | 配当% | スコア |",
        "",
        "## 投資判断のポイント（箇条書き3〜5点）",
        "- バリュー投資家として押さえるべき共通の着眼点",
        "",
        "## 免責事項",
        "- 本レポートは情報提供目的であり、投資勧誘ではない旨を明記",
        "",
        "文体は客観的・専門的に。数値は必ず記載データを正確に使用してください。",
    ]

    return "\n".join(lines)


def generate_article(stocks: list[dict]) -> str:
    """Anthropic API でレポートを生成"""
    if not stocks:
        raise ValueError("スクリーニング結果がありません")

    screened_at = stocks[0].get('screened_at', datetime.now().isoformat())
    prompt = build_prompt(stocks, screened_at)

    client = anthropic.Anthropic()

    print("  🤖 Claude に記事生成を依頼中...")
    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}],
    )

    return message.content[0].text


def save_article(content: str, output_dir: str = "output") -> str:
    """生成した記事をファイルに保存"""
    os.makedirs(output_dir, exist_ok=True)
    date_tag = datetime.now().strftime("%Y%m%d_%H%M")
    filename = os.path.join(output_dir, f"report_{date_tag}.md")
    with open(filename, "w", encoding="utf-8") as f:
        f.write(content)
    return filename


def main():
    print("\n📰 AI投資分析レポート生成\n" + "=" * 50)

    print("  📊 最新スクリーニング結果を読み込み中...")
    stocks = load_latest_results()

    if not stocks:
        print("❌ スクリーニング結果がありません。先に screener.py を実行してください。")
        return

    print(f"     → {len(stocks)}銘柄のデータを取得")

    article = generate_article(stocks)

    base_dir = os.path.join(os.path.dirname(__file__), '..')
    output_dir = os.path.join(base_dir, 'output')
    filepath = save_article(article, output_dir)

    print(f"\n{'=' * 50}")
    print(f"✅ レポートを保存しました: {filepath}\n")
    print(article)


if __name__ == '__main__':
    main()
