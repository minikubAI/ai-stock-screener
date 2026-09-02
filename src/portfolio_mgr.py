"""
ポートフォリオ管理ユーティリティ

使い方:
  python src/portfolio_mgr.py export           # docs/data/portfolio.json を生成
  python src/portfolio_mgr.py snapshot         # 日次スナップショットを記録
  python src/portfolio_mgr.py record_buys      # 当日の morning_orders.json を DB に記録
  python src/portfolio_mgr.py update_prices    # 保有銘柄の株価をYahoo Financeから更新
  python src/portfolio_mgr.py buy TICKER PRICE SHARES  # 個別買い記録
"""

import os
import sys
import json
import sqlite3
from datetime import date

BASE_DIR = os.path.join(os.path.dirname(__file__), '..')

ORDERS_PATH    = os.path.join(BASE_DIR, 'data', 'morning_orders.json')
PORTFOLIO_PATH = os.path.join(BASE_DIR, 'docs', 'data', 'portfolio.json')


def get_db():
    db_path = os.path.join(BASE_DIR, 'data', 'stocks.db')
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _load_existing_portfolio():
    """docs/data/portfolio.json から既存のholdings一覧を返す（ticker -> item のdict）"""
    try:
        with open(PORTFOLIO_PATH, 'r', encoding='utf-8') as f:
            pf = json.load(f)
        return {h['ticker']: h for h in pf.get('holdings', [])}
    except Exception:
        return {}


def export_portfolio():
    conn = get_db()

    db_rows = conn.execute(
        '''SELECT p.ticker, c.name, p.buy_date, p.buy_price, p.shares,
                  pr.close as current_price
           FROM portfolio p
           JOIN companies c ON c.ticker = p.ticker
           LEFT JOIN prices pr ON pr.ticker = p.ticker
               AND pr.date = (SELECT MAX(date) FROM prices WHERE ticker=p.ticker)
           WHERE p.status='HOLD'
           ORDER BY p.buy_date'''
    ).fetchall()

    # DBの結果をtickerキーのdictに変換
    db_items = {}
    for h in db_rows:
        cp   = h['current_price'] or h['buy_price']
        cost = h['buy_price'] * h['shares']
        mval = cp * h['shares']
        upnl_pct = (cp - h['buy_price']) / h['buy_price'] * 100 if h['buy_price'] else 0
        db_items[h['ticker']] = {
            'ticker':             h['ticker'],
            'name':               h['name'] or h['ticker'],
            'buy_date':           h['buy_date'],
            'buy_price':          h['buy_price'],
            'shares':             h['shares'],
            'current_price':      cp,
            'unrealized_pnl':     round(mval - cost, 0),
            'unrealized_pnl_pct': round(upnl_pct, 2),
        }

    # 既存のportfolio.jsonと統合（DBキャッシュリセット時にも履歴を保持）
    existing = _load_existing_portfolio()
    merged = {}
    for ticker, item in existing.items():
        # DBに同銘柄があれば株価を最新化、なければ既存データをそのまま残す
        if ticker in db_items:
            merged[ticker] = db_items[ticker]
        else:
            merged[ticker] = item
    # DBにある新規銘柄を追加
    for ticker, item in db_items.items():
        if ticker not in merged:
            merged[ticker] = item

    items = sorted(merged.values(), key=lambda x: x['buy_date'])

    cost_basis = 0.0
    market_value = 0.0
    for item in items:
        cp   = item['current_price']
        cost = item['buy_price'] * item['shares']
        mval = cp * item['shares']
        cost_basis   += cost
        market_value += mval

    # 累計配当
    div_row = conn.execute(
        "SELECT COALESCE(SUM(amount),0) as total FROM trades WHERE trade_type='DIVIDEND'"
    ).fetchone()
    total_dividends = div_row['total'] if div_row else 0.0

    unrealized_pnl     = market_value - cost_basis
    unrealized_pnl_pct = (unrealized_pnl / cost_basis * 100) if cost_basis > 0 else 0.0
    total_return       = unrealized_pnl + total_dividends
    total_return_pct   = (total_return / cost_basis * 100) if cost_basis > 0 else 0.0

    portfolio = {
        'updated_at':        date.today().isoformat(),
        'cost_basis':        round(cost_basis, 0),
        'market_value':      round(market_value, 0),
        'unrealized_pnl':    round(unrealized_pnl, 0),
        'unrealized_pnl_pct': round(unrealized_pnl_pct, 2),
        'total_dividends':   round(total_dividends, 0),
        'total_return':      round(total_return, 0),
        'total_return_pct':  round(total_return_pct, 2),
        'holdings_count':    len(items),
        'holdings':          items,
    }

    os.makedirs(os.path.dirname(PORTFOLIO_PATH), exist_ok=True)
    with open(PORTFOLIO_PATH, 'w', encoding='utf-8') as f:
        json.dump(portfolio, f, ensure_ascii=False, indent=2)

    conn.close()
    print(f'✅ portfolio.json エクスポート完了'
          f' (保有{len(items)}銘柄, 元本¥{cost_basis:,.0f}, 評価¥{market_value:,.0f})')
    return portfolio


def record_snapshot():
    conn = get_db()
    today = date.today().isoformat()

    holdings = conn.execute(
        '''SELECT p.buy_price, p.shares, pr.close as current_price
           FROM portfolio p
           LEFT JOIN prices pr ON pr.ticker = p.ticker
               AND pr.date = (SELECT MAX(date) FROM prices WHERE ticker=p.ticker)
           WHERE p.status='HOLD' '''
    ).fetchall()

    cost_basis   = sum(h['buy_price'] * h['shares'] for h in holdings)
    market_value = sum((h['current_price'] or h['buy_price']) * h['shares'] for h in holdings)
    unrealized_pnl     = market_value - cost_basis
    unrealized_pnl_pct = (unrealized_pnl / cost_basis * 100) if cost_basis > 0 else 0.0

    conn.execute(
        '''INSERT OR REPLACE INTO portfolio_snapshots
           (snapshot_date, cost_basis, market_value,
            unrealized_pnl, unrealized_pnl_pct, holdings_count)
           VALUES (?, ?, ?, ?, ?, ?)''',
        (today, cost_basis, market_value, unrealized_pnl, unrealized_pnl_pct, len(holdings))
    )
    conn.commit()
    conn.close()
    print(f'✅ スナップショット記録: {today}'
          f' (評価額¥{market_value:,.0f}, 損益{unrealized_pnl_pct:+.1f}%)')


def record_morning_buys():
    today = date.today().isoformat()

    try:
        with open(ORDERS_PATH, 'r', encoding='utf-8') as f:
            orders_data = json.load(f)
    except Exception:
        print('morning_orders.json が見つかりません。スキップします。')
        return

    if orders_data.get('date') != today:
        print(f'morning_orders.json の日付 ({orders_data.get("date")}) が本日ではありません。スキップ。')
        return

    orders = orders_data.get('orders', [])
    if not orders:
        print('本日の注文なし（PAUSEシグナル等）')
        return

    conn = get_db()
    recorded = 0

    for o in orders:
        ticker = o['ticker']
        price  = o['price']
        shares = o['shares']
        amount = o['cost']

        # 本日同一銘柄・価格の BUY がすでに記録済みなら重複スキップ
        exists = conn.execute(
            '''SELECT 1 FROM trades
               WHERE ticker=? AND trade_date=? AND trade_type='BUY' AND price=?''',
            (ticker, today, price)
        ).fetchone()
        if exists:
            print(f'  スキップ（重複）: {ticker}')
            continue

        conn.execute(
            '''INSERT INTO trades (ticker, trade_date, trade_type, price, shares, amount, reason)
               VALUES (?, ?, 'BUY', ?, ?, ?, ?)''',
            (ticker, today, price, shares, amount, '朝の推奨注文')
        )
        conn.execute(
            '''INSERT INTO portfolio (ticker, buy_date, buy_price, shares, status)
               VALUES (?, ?, ?, ?, 'HOLD')''',
            (ticker, today, price, shares)
        )
        print(f'  ✅ 買い記録: {ticker} × {shares}株 @ ¥{price:,}')
        recorded += 1

    conn.commit()
    conn.close()
    print(f'買い記録完了: {recorded}件')


def record_buy(ticker, price, shares):
    conn = get_db()
    today = date.today().isoformat()
    amount = price * shares

    conn.execute(
        '''INSERT INTO trades (ticker, trade_date, trade_type, price, shares, amount, reason)
           VALUES (?, ?, 'BUY', ?, ?, ?, '手動記録')''',
        (ticker, today, price, shares, amount)
    )
    conn.execute(
        '''INSERT INTO portfolio (ticker, buy_date, buy_price, shares, status)
           VALUES (?, ?, ?, ?, 'HOLD')''',
        (ticker, today, price, shares)
    )
    conn.commit()
    conn.close()
    print(f'✅ 買い記録: {ticker} × {shares}株 @ ¥{price:,}')


def update_portfolio_prices():
    """保有銘柄の現在株価をYahoo Financeから取得してDBのpricesテーブルを更新"""
    try:
        import yfinance as yf
    except ImportError:
        print('⚠️ yfinance がインストールされていません。スキップします。')
        return

    conn = get_db()
    today = date.today().isoformat()

    holdings = conn.execute(
        "SELECT DISTINCT ticker FROM portfolio WHERE status='HOLD'"
    ).fetchall()

    if not holdings:
        print('保有銘柄なし。スキップします。')
        conn.close()
        return

    tickers = [row['ticker'] for row in holdings]
    print(f'📊 {len(tickers)}銘柄の株価を更新中...')

    for ticker in tickers:
        yf_ticker = f"{ticker}.T"
        try:
            stock = yf.Ticker(yf_ticker)
            info = stock.info
            price = info.get('currentPrice') or info.get('regularMarketPrice')
            if not price:
                hist = stock.history(period='2d')
                if not hist.empty:
                    price = float(hist['Close'].iloc[-1])

            if price:
                conn.execute(
                    '''INSERT OR REPLACE INTO prices (ticker, date, close)
                       VALUES (?, ?, ?)''',
                    (ticker, today, round(price, 1))
                )
                print(f'  ✅ {ticker}: ¥{price:,.0f}')
            else:
                print(f'  ⚠️ {ticker}: 株価取得失敗')
        except Exception as e:
            print(f'  ❌ {ticker}: {e}')

    conn.commit()
    conn.close()
    print(f'株価更新完了: {len(tickers)}銘柄')


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'export'
    if cmd == 'export':
        export_portfolio()
    elif cmd == 'snapshot':
        record_snapshot()
    elif cmd == 'record_buys':
        record_morning_buys()
    elif cmd == 'update_prices':
        update_portfolio_prices()
    elif cmd == 'buy' and len(sys.argv) == 5:
        record_buy(sys.argv[2], float(sys.argv[3]), int(sys.argv[4]))
    else:
        print('使い方: python src/portfolio_mgr.py export|snapshot|record_buys|update_prices|buy TICKER PRICE SHARES')


if __name__ == '__main__':
    main()
