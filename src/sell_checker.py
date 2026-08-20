"""
売りシグナル判定

Tier 1: ニュースに上場廃止・不祥事等のキーワード → 即時売り
Tier 2: TOP50圏外が3回連続 OR 売上+営業利益が2期連続減少 → 売り
Tier 3: Core -30% / Satellite -25% ストップロス; +50% 半売り
         ※ Nikkei -15%以上の暴落時はストップロスを停止

使い方:
  python src/sell_checker.py
"""

import os
import sys
import json
import sqlite3
import yaml
import requests
from datetime import date, datetime

BASE_DIR = os.path.join(os.path.dirname(__file__), '..')

TIER1_KEYWORDS = ['上場廃止', '債務超過', '粉飾', '不正', '行政処分']

REPORT_PATH = os.path.join(BASE_DIR, 'data', 'latest_report.json')


def get_db():
    db_path = os.path.join(BASE_DIR, 'data', 'stocks.db')
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def get_line_token():
    token = os.environ.get('LINE_CHANNEL_TOKEN')
    if token:
        return token
    try:
        config_path = os.path.join(BASE_DIR, 'config', 'settings.yaml')
        with open(config_path) as f:
            cfg = yaml.safe_load(f)
        return cfg.get('line', {}).get('channel_token')
    except Exception:
        return None


def send_line(token, message):
    if not token:
        print(f'  [LINE未送信] {message[:50]}...')
        return
    url = 'https://api.line.me/v2/bot/message/broadcast'
    headers = {'Content-Type': 'application/json', 'Authorization': f'Bearer {token}'}
    resp = requests.post(url, headers=headers,
                         json={'messages': [{'type': 'text', 'text': message}]},
                         timeout=10)
    if resp.status_code != 200:
        print(f'  ⚠️ LINE送信失敗: {resp.status_code}')


def check_tier1(conn, ticker):
    rows = conn.execute(
        '''SELECT title FROM news
           WHERE ticker=? AND published_at >= date('now', '-7 days')''',
        (ticker,)
    ).fetchall()
    for row in rows:
        title = row['title'] or ''
        for kw in TIER1_KEYWORDS:
            if kw in title:
                return True, f'ニュースに「{kw}」を検出'
    return False, None


def check_tier2(conn, ticker):
    # TOP50圏外が3回連続
    rows = conn.execute(
        '''SELECT rank FROM screening_results
           WHERE ticker=? ORDER BY screened_at DESC LIMIT 3''',
        (ticker,)
    ).fetchall()
    if len(rows) >= 3:
        ranks = [r['rank'] for r in rows]
        if all(r is None or r > 50 for r in ranks):
            return True, 'TOP50圏外が3回連続'

    # 売上・営業利益が2期連続減少
    fins = conn.execute(
        '''SELECT fiscal_year, revenue, operating_income
           FROM financials WHERE ticker=?
           ORDER BY fiscal_year DESC LIMIT 3''',
        (ticker,)
    ).fetchall()
    if len(fins) >= 3:
        rev = [f['revenue'] for f in fins]
        op  = [f['operating_income'] for f in fins]
        if all(rev) and rev[0] < rev[1] < rev[2] and all(op[:2]) and op[0] < op[1]:
            return True, '売上・営業利益が2期連続減少'

    return False, None


def is_market_crash():
    try:
        with open(REPORT_PATH) as f:
            report = json.load(f)
        drop = report.get('macro', {}).get('nikkei_drop_pct', 0)
        return drop <= -15
    except Exception:
        return False


def check_tier3(ticker, category, buy_price, current_price):
    if buy_price <= 0 or current_price <= 0:
        return False, None
    pct = (current_price - buy_price) / buy_price * 100
    stop_thresh = -30 if category == 'core' else -25

    if pct <= stop_thresh:
        if is_market_crash():
            return False, None  # 市場暴落時はストップロス停止
        return True, f'ストップロス（{pct:.1f}%）'

    if pct >= 50:
        return True, f'利確+50%（{pct:.1f}%、半売り）'

    return False, None


def record_sell(conn, ticker, shares, buy_price, current_price, reason):
    today = date.today().isoformat()
    amount = shares * current_price
    profit_loss = (current_price - buy_price) * shares
    profit_loss_pct = (current_price - buy_price) / buy_price * 100 if buy_price else 0

    conn.execute(
        '''INSERT INTO trades (ticker, trade_date, trade_type, price, shares, amount, reason)
           VALUES (?, ?, 'SELL', ?, ?, ?, ?)''',
        (ticker, today, current_price, shares, amount, reason)
    )
    conn.execute(
        '''UPDATE portfolio SET status='SOLD', sell_date=?, sell_price=?,
           profit_loss=?, profit_loss_pct=?
           WHERE ticker=? AND status='HOLD' ''',
        (today, current_price, profit_loss, profit_loss_pct, ticker)
    )
    conn.commit()
    return profit_loss, profit_loss_pct


def build_sell_message(ticker, name, reason, buy_price, current_price, profit_loss, profit_loss_pct):
    today = date.today().isoformat()
    icon = '📈' if profit_loss >= 0 else '📉'
    return (
        f'🔴 売りシグナル ({today})\n'
        f'{ticker} {name}\n'
        f'理由: {reason}\n'
        f'取得価格: ¥{int(buy_price):,} → 終値: ¥{int(current_price):,}\n'
        f'損益: {icon} ¥{int(profit_loss):,}（{profit_loss_pct:+.1f}%）'
    )


def run_sell_check():
    conn = get_db()
    token = get_line_token()
    signals = []

    holdings = conn.execute(
        '''SELECT p.ticker, p.buy_price, p.shares, c.name,
                  pr.close as current_price,
                  CASE
                    WHEN sr.rank IS NOT NULL AND sr.rank <= 50 THEN 'core'
                    ELSE 'satellite'
                  END as category
           FROM portfolio p
           JOIN companies c ON c.ticker = p.ticker
           LEFT JOIN prices pr ON pr.ticker = p.ticker
               AND pr.date = (SELECT MAX(date) FROM prices WHERE ticker=p.ticker)
           LEFT JOIN (
               SELECT ticker, rank FROM screening_results
               WHERE screened_at = (SELECT MAX(screened_at) FROM screening_results)
           ) sr ON sr.ticker = p.ticker
           WHERE p.status='HOLD' '''
    ).fetchall()

    print(f'保有銘柄チェック: {len(holdings)}銘柄')

    for h in holdings:
        ticker       = h['ticker']
        name         = h['name'] or ticker
        buy_price    = h['buy_price'] or 0
        current_price = h['current_price'] or buy_price
        shares       = h['shares']
        category     = h['category']

        triggered, reason = check_tier1(conn, ticker)
        if not triggered:
            triggered, reason = check_tier2(conn, ticker)
        if not triggered:
            triggered, reason = check_tier3(ticker, category, buy_price, current_price)

        if triggered:
            # 利確+50%の場合は半分のみ売却
            sell_shares = shares // 2 if '半売り' in (reason or '') else shares
            sell_shares = max(sell_shares, 1)

            profit_loss, profit_loss_pct = record_sell(
                conn, ticker, sell_shares, buy_price, current_price, reason
            )
            msg = build_sell_message(ticker, name, reason, buy_price, current_price,
                                     profit_loss, profit_loss_pct)
            print(f'🔴 {ticker} {name}: {reason}')
            send_line(token, msg)
            signals.append(msg)
        else:
            print(f'  ✅ {ticker} {name}: 保有継続')

    conn.close()
    return signals


if __name__ == '__main__':
    signals = run_sell_check()
    if not signals:
        print('売りシグナルなし')
