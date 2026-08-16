"""
バリュエーションギャップ分析（第5層）

業績改善度 vs バリュエーション変化のギャップを検出。
「業績は良くなっているのに株価（PER/PBR）が追いついていない銘柄」
= 市場に見落とされている銘柄を高スコアに。

使い方:
  python src/valuation_gap.py
"""

import sqlite3
import os
import yaml
import numpy as np
from datetime import datetime

def get_config():
    config_path = os.path.join(os.path.dirname(__file__), '..', 'config', 'settings.yaml')
    with open(config_path, 'r') as f:
        return yaml.safe_load(f)

def get_db():
    config = get_config()
    db_path = os.path.join(os.path.dirname(__file__), '..', config['database']['path'])
    return sqlite3.connect(db_path)


def init_vgap_table(conn):
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS valuation_gap (
            ticker TEXT PRIMARY KEY,
            revenue_trend_pct REAL,
            op_income_trend_pct REAL,
            eps_trend_pct REAL,
            per_trend_pct REAL,
            pbr_trend_pct REAL,
            fundamental_improvement REAL,
            valuation_change REAL,
            gap_score REAL,
            gap_type TEXT,
            data_years INTEGER,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()


def calc_annual_change_rate(values):
    """年平均変化率を計算（CAGR的）"""
    clean = [v for v in values if v is not None and v != 0]
    if len(clean) < 2:
        return None
    first, last = clean[0], clean[-1]
    years = len(clean) - 1
    if first <= 0 or last <= 0 or years <= 0:
        # 符号が変わる場合は単純な変化率
        return ((last - first) / abs(first)) * 100 / years if first != 0 else None
    return (pow(last / first, 1 / years) - 1) * 100


def analyze_valuation_gap(conn) -> list[dict]:
    """全銘柄のバリュエーションギャップを分析"""
    c = conn.cursor()

    # 3年以上のデータがある銘柄
    c.execute('''
        SELECT DISTINCT ticker FROM financials
        GROUP BY ticker HAVING COUNT(DISTINCT fiscal_year) >= 3
    ''')
    tickers = [r[0] for r in c.fetchall()]

    results = []

    for ticker in tickers:
        # 財務データ（年度順）
        c.execute('''
            SELECT fiscal_year, revenue, operating_income, net_income,
                   eps, total_equity, shares_outstanding
            FROM financials
            WHERE ticker = ? AND revenue IS NOT NULL
            ORDER BY fiscal_year ASC
        ''', (ticker,))
        fin_rows = c.fetchall()
        if len(fin_rows) < 3:
            continue

        # 株価データ（各年度末の株価でPER/PBRを逆算）
        c.execute('''
            SELECT SUBSTR(date, 1, 4) as yr, AVG(close) as avg_price
            FROM prices
            WHERE ticker = ?
            GROUP BY yr ORDER BY yr ASC
        ''', (ticker,))
        price_by_year = {r[0]: r[1] for r in c.fetchall()}

        # 各年度のデータを整理
        revenues = []
        op_incomes = []
        epss = []
        pers = []
        pbrs = []

        for row in fin_rows:
            fy = row[0]
            rev, op, ni, eps_val = row[1], row[2], row[3], row[4]
            equity, shares = row[5], row[6]

            revenues.append(rev)
            op_incomes.append(op)

            # EPS
            if eps_val:
                epss.append(eps_val)
            elif ni and shares and shares > 0:
                epss.append(ni / shares)
            else:
                epss.append(None)

            # 年度の平均株価からPER/PBR算出
            avg_price = price_by_year.get(fy)
            if avg_price and epss[-1] and epss[-1] > 0:
                pers.append(avg_price / epss[-1])
            else:
                pers.append(None)

            if avg_price and equity and shares and shares > 0:
                bps = equity / shares
                if bps > 0:
                    pbrs.append(avg_price / bps)
                else:
                    pbrs.append(None)
            else:
                pbrs.append(None)

        # 変化率を算出
        rev_trend = calc_annual_change_rate(revenues)
        op_trend = calc_annual_change_rate([o for o in op_incomes if o and o > 0])
        eps_trend = calc_annual_change_rate([e for e in epss if e and e > 0])
        per_trend = calc_annual_change_rate([p for p in pers if p and p > 0])
        pbr_trend = calc_annual_change_rate([p for p in pbrs if p and p > 0])

        if rev_trend is None:
            continue

        # ファンダメンタル改善度（売上・営業利益・EPSの加重平均）
        components = []
        weights = []
        if rev_trend is not None:
            components.append(rev_trend)
            weights.append(0.3)
        if op_trend is not None:
            components.append(op_trend)
            weights.append(0.35)
        if eps_trend is not None:
            components.append(eps_trend)
            weights.append(0.35)

        if not components:
            continue

        w_sum = sum(weights)
        fundamental_improvement = sum(c * w for c, w in zip(components, weights)) / w_sum

        # バリュエーション変化（PER・PBRの平均変化率）
        val_components = []
        if per_trend is not None:
            val_components.append(per_trend)
        if pbr_trend is not None:
            val_components.append(pbr_trend)

        valuation_change = np.mean(val_components) if val_components else 0

        # ギャップ = 業績改善度 - バリュエーション変化
        # 正のギャップ = 業績は改善しているのにPER/PBRが上がっていない = チャンス
        # 負のギャップ = 業績は悪化しているのにPER/PBRが高い = 危険
        gap = fundamental_improvement - valuation_change

        # ギャップタイプ分類
        if fundamental_improvement > 5 and gap > 10:
            gap_type = 'HIDDEN_GEM'      # 業績改善中＋市場未反映
        elif fundamental_improvement > 5 and gap > 0:
            gap_type = 'CATCHING_UP'     # 業績改善中＋やや織り込み
        elif fundamental_improvement > 0 and gap <= 0:
            gap_type = 'PRICED_IN'       # 業績改善済み＋株価も上昇済み
        elif fundamental_improvement <= 0 and gap > 0:
            gap_type = 'DECLINING_CHEAP'  # 業績悪化中＋株価はさらに下落（バリュートラップ注意）
        else:
            gap_type = 'OVERVALUED'      # 業績悪化中＋株価まだ高い

        # スコア化 (0-100)
        if gap_type == 'HIDDEN_GEM':
            base_score = 80 + min(20, gap * 0.5)
        elif gap_type == 'CATCHING_UP':
            base_score = 55 + min(25, gap * 1.5)
        elif gap_type == 'PRICED_IN':
            base_score = 35 + min(20, fundamental_improvement * 0.5)
        elif gap_type == 'DECLINING_CHEAP':
            base_score = 25  # バリュートラップの可能性
        else:
            base_score = max(5, 30 + gap * 0.5)

        gap_score = round(max(0, min(100, base_score)), 1)

        # 企業名取得
        c.execute("SELECT name FROM companies WHERE ticker = ?", (ticker,))
        name_row = c.fetchone()

        results.append({
            'ticker': ticker,
            'name': name_row[0] if name_row else ticker,
            'revenue_trend_pct': round(rev_trend, 1) if rev_trend else 0,
            'op_income_trend_pct': round(op_trend, 1) if op_trend else 0,
            'eps_trend_pct': round(eps_trend, 1) if eps_trend else 0,
            'per_trend_pct': round(per_trend, 1) if per_trend else 0,
            'pbr_trend_pct': round(pbr_trend, 1) if pbr_trend else 0,
            'fundamental_improvement': round(fundamental_improvement, 1),
            'valuation_change': round(valuation_change, 1),
            'gap_score': gap_score,
            'gap_type': gap_type,
            'data_years': len(fin_rows),
        })

    results.sort(key=lambda x: x['gap_score'], reverse=True)
    return results


def save_results(conn, results):
    c = conn.cursor()
    for r in results:
        c.execute('''
            INSERT OR REPLACE INTO valuation_gap
            (ticker, revenue_trend_pct, op_income_trend_pct, eps_trend_pct,
             per_trend_pct, pbr_trend_pct, fundamental_improvement,
             valuation_change, gap_score, gap_type, data_years)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            r['ticker'], r['revenue_trend_pct'], r['op_income_trend_pct'],
            r['eps_trend_pct'], r['per_trend_pct'], r['pbr_trend_pct'],
            r['fundamental_improvement'], r['valuation_change'],
            r['gap_score'], r['gap_type'], r['data_years']
        ))
    conn.commit()


def run():
    print("🔍 バリュエーションギャップ分析（第5層）")
    print("=" * 50)

    conn = get_db()
    init_vgap_table(conn)

    results = analyze_valuation_gap(conn)

    if not results:
        print("  ❌ 分析対象がありません")
        conn.close()
        return

    save_results(conn, results)

    # タイプ別集計
    types = {}
    for r in results:
        t = r['gap_type']
        types[t] = types.get(t, 0) + 1

    print(f"\n  分析銘柄数: {len(results)}")
    print(f"\n  タイプ別分布:")
    type_labels = {
        'HIDDEN_GEM': '💎 Hidden Gem（業績↑ 株価未反映）',
        'CATCHING_UP': '📈 Catching Up（業績↑ やや織り込み）',
        'PRICED_IN': '✅ Priced In（業績↑ 株価も↑）',
        'DECLINING_CHEAP': '⚠️ Declining Cheap（業績↓ バリュートラップ注意）',
        'OVERVALUED': '🔴 Overvalued（業績↓ 株価まだ高い）',
    }
    for t, label in type_labels.items():
        count = types.get(t, 0)
        if count > 0:
            print(f"    {label}: {count}社")

    # Hidden Gem TOP10
    gems = [r for r in results if r['gap_type'] == 'HIDDEN_GEM'][:10]
    if gems:
        print(f"\n  💎 Hidden Gem TOP10:")
        for r in gems:
            print(f"    {r['ticker']} {r['name'][:12]:12s} | 業績 {r['fundamental_improvement']:+.1f}%/yr | PER変化 {r['per_trend_pct']:+.1f}%/yr | Gap Score {r['gap_score']}")

    conn.close()
    print(f"\n✅ バリュエーションギャップ分析完了: {len(results)}銘柄")


if __name__ == '__main__':
    run()
