"""
保有銘柄の配当金をYahoo Financeから自動取得して data/dividends.json に記録

使い方:
  python src/fetch_dividends.py

夕方パイプライン（run_all.py evening）から自動実行される。
dividends.json は git 管理されるため DB リセット後も履歴が保持される。
"""

import os
import json
from datetime import date

BASE_DIR       = os.path.join(os.path.dirname(__file__), '..')
PORTFOLIO_PATH = os.path.join(BASE_DIR, 'docs', 'data', 'portfolio.json')
DIVIDENDS_PATH = os.path.join(BASE_DIR, 'data', 'dividends.json')


def load_dividends_json():
    """既存の dividends.json を読み込む。(ticker, ex_date) → record の dict を返す。"""
    try:
        with open(DIVIDENDS_PATH, encoding='utf-8') as f:
            records = json.load(f)
        return {(r['ticker'], r['ex_date']): r for r in records}
    except Exception:
        return {}


def save_dividends_json(records_dict):
    os.makedirs(os.path.dirname(DIVIDENDS_PATH), exist_ok=True)
    records = sorted(records_dict.values(), key=lambda r: (r['ex_date'], r['ticker']))
    with open(DIVIDENDS_PATH, 'w', encoding='utf-8') as f:
        json.dump(records, f, ensure_ascii=False, indent=2)


def main():
    try:
        import yfinance as yf
    except ImportError:
        print('⚠️ yfinance がインストールされていません。スキップします。')
        return

    try:
        with open(PORTFOLIO_PATH, encoding='utf-8') as f:
            portfolio = json.load(f)
    except Exception:
        print('portfolio.json が見つかりません。スキップします。')
        return

    holdings = portfolio.get('holdings', [])
    if not holdings:
        print('保有銘柄なし。スキップします。')
        return

    # 銘柄ごとに (buy_date, shares) の一覧を集約
    ticker_map = {}
    for h in holdings:
        ticker = h['ticker']
        if ticker not in ticker_map:
            ticker_map[ticker] = []
        ticker_map[ticker].append((h['buy_date'], h['shares']))

    existing = load_dividends_json()
    today = date.today().isoformat()
    new_count = 0

    print(f'📊 {len(ticker_map)}銘柄の配当履歴を確認中...')

    for ticker, buys in ticker_map.items():
        buys_sorted = sorted(buys)  # [(buy_date, shares), ...]
        earliest_buy = buys_sorted[0][0]
        yf_ticker = f'{ticker}.T'

        try:
            stock = yf.Ticker(yf_ticker)
            dividends = stock.dividends  # pandas Series: index=datetime, value=per_share

            if dividends.empty:
                continue

            for ex_dt, per_share in dividends.items():
                ex_date_str = ex_dt.date().isoformat() if hasattr(ex_dt, 'date') else str(ex_dt)[:10]

                # 最初の購入日より前、または未来の配当はスキップ
                if ex_date_str < earliest_buy or ex_date_str > today:
                    continue

                key = (ticker, ex_date_str)
                if key in existing:
                    continue

                # 権利落ち日時点で保有していた株数（権利落ち日より前に購入した分のみ対象）
                shares_held = sum(s for d, s in buys_sorted if d < ex_date_str)
                if shares_held <= 0:
                    continue

                amount = round(float(per_share) * shares_held, 0)
                existing[key] = {
                    'ticker':    ticker,
                    'ex_date':   ex_date_str,
                    'per_share': round(float(per_share), 2),
                    'shares':    shares_held,
                    'amount':    amount,
                }
                new_count += 1
                print(f'  ✅ {ticker} {ex_date_str}'
                      f' ¥{per_share:.2f}/株 × {shares_held}株 = ¥{amount:,.0f}')

        except Exception as e:
            print(f'  ❌ {ticker}: {e}')

    save_dividends_json(existing)

    total = sum(r['amount'] for r in existing.values())
    print(f'\n配当取得完了: 新規{new_count}件, 累計¥{total:,.0f}')


if __name__ == '__main__':
    main()
