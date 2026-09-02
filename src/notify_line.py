"""
LINE Notify スクリーニング結果通知

使い方:
  python src/notify_line.py           # 朝の注文指示を送信
  python src/notify_line.py morning   # 朝の注文指示を送信
  python src/notify_line.py evening   # 夕方の運用サマリーを送信
  python src/notify_line.py test      # テストメッセージを送信

環境変数:
  LINE_CHANNEL_TOKEN  LINE Messaging API チャンネルアクセストークン
"""

import os
import sys
import json
import yaml
import requests
from datetime import datetime

BASE_DIR = os.path.join(os.path.dirname(__file__), '..')

# 予算設定
MONTHLY_BUDGET  = 30_000
WEEKLY_BUDGET   = MONTHLY_BUDGET // 4        # 7,500
CORE_BUDGET     = int(WEEKLY_BUDGET * 0.50)  # 3,750
SAT_A_BUDGET    = int(WEEKLY_BUDGET * 0.33)  # 2,475 → 2,500 rounded
SAT_B_BUDGET    = WEEKLY_BUDGET - CORE_BUDGET - int(WEEKLY_BUDGET * 0.33)  # 残り

WEEKLY_SKIP_THRESHOLD = WEEKLY_BUDGET // 2  # 1銘柄の株価がこれを超えたらスキップ（¥3,750）

SATB_POOL_PATH    = os.path.join(BASE_DIR, 'data', 'satb_pool.json')
REPORT_PATH       = os.path.join(BASE_DIR, 'data', 'latest_report.json')
ORDERS_PATH       = os.path.join(BASE_DIR, 'data', 'morning_orders.json')
PORTFOLIO_PATH    = os.path.join(BASE_DIR, 'docs', 'data', 'portfolio.json')


def get_config():
    config_path = os.path.join(BASE_DIR, 'config', 'settings.yaml')
    with open(config_path, 'r') as f:
        return yaml.safe_load(f)


def send_line_message(token, message):
    url = 'https://api.line.me/v2/bot/message/broadcast'
    headers = {
        'Content-Type': 'application/json',
        'Authorization': f'Bearer {token}',
    }
    resp = requests.post(url, headers=headers,
                         json={'messages': [{'type': 'text', 'text': message}]},
                         timeout=10)
    if resp.status_code == 200:
        print('✅ LINE送信成功')
        return True
    print(f'❌ LINE送信失敗: {resp.status_code} {resp.text}')
    return False


def get_token():
    token = os.environ.get('LINE_CHANNEL_TOKEN')
    if token:
        return token
    try:
        config = get_config()
        return config.get('line', {}).get('channel_token')
    except Exception:
        return None


def load_report():
    try:
        with open(REPORT_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def load_satb_pool():
    try:
        with open(SATB_POOL_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {'accumulated': 0, 'target_ticker': '', 'target_price': 0}


def save_satb_pool(pool):
    os.makedirs(os.path.dirname(SATB_POOL_PATH), exist_ok=True)
    with open(SATB_POOL_PATH, 'w', encoding='utf-8') as f:
        json.dump(pool, f, ensure_ascii=False, indent=2)


def save_morning_orders(orders):
    os.makedirs(os.path.dirname(ORDERS_PATH), exist_ok=True)
    with open(ORDERS_PATH, 'w', encoding='utf-8') as f:
        json.dump(orders, f, ensure_ascii=False, indent=2)


def load_portfolio():
    try:
        with open(PORTFOLIO_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return None


def generate_morning_message():
    report   = load_report()
    core_raw = report.get('core_results', [])[:4]
    sat_raw  = report.get('satellite_results', [])[:3]
    macro    = report.get('macro', {})
    signal   = macro.get('signal', 'NORMAL').upper()

    today = datetime.now().strftime('%Y-%m-%d')
    lines = [
        f'📊 本日の注文指示 ({today})',
        '━━━━━━━━━━━━━━',
    ]

    # マクロシグナル表示
    if signal == 'BUY':
        signal_icon = '🟢 BUY'
        budget_ratio = 1.0
    elif signal == 'CAUTION':
        signal_icon = '🟡 CAUTION'
        budget_ratio = 0.5
    elif signal == 'PAUSE':
        signal_icon = '🔴 PAUSE'
        budget_ratio = 0.0
    else:
        signal_icon = '🔵 NORMAL'
        budget_ratio = 1.0

    weekly = int(WEEKLY_BUDGET * budget_ratio)
    lines.append(f'マクロ環境: {signal_icon}')
    lines.append(f'週予算: ¥{weekly:,}（月¥{MONTHLY_BUDGET:,}）')
    lines.append('')

    orders = {'date': today, 'signal': signal, 'budget_ratio': budget_ratio, 'orders': []}

    if budget_ratio == 0.0:
        lines.append('🚫 本日は新規購入停止（PAUSEシグナル）')
        lines.append('━━━━━━━━━━━━━━')
        lines.append('📱 証券アプリで上記を確認してください')
        save_morning_orders(orders)
        return '\n'.join(lines)

    core_budget  = int(CORE_BUDGET  * budget_ratio)
    sat_a_budget = int(SAT_A_BUDGET * budget_ratio)
    sat_b_budget = int(SAT_B_BUDGET * budget_ratio)

    total_spent = 0

    # ── Core ──────────────────────────
    lines.append('【Core — バリュー＋配当】')
    core_valid = [s for s in core_raw
                  if 0 < s.get('price', 0) <= WEEKLY_SKIP_THRESHOLD]
    if core_valid:
        budget_each = core_budget // len(core_valid)
        for s in core_valid:
            price = s.get('price', 0)
            shares = max(1, int(budget_each // price))
            cost = shares * int(price)
            total_spent += cost
            name = s.get('name', s.get('ticker', ''))[:10]
            lines.append(f'  {s["ticker"]} {name} × {shares}株'
                         f' @ ¥{int(price):,} = ¥{cost:,}')
            orders['orders'].append({
                'ticker': s['ticker'], 'name': name,
                'shares': shares, 'price': int(price),
                'cost': cost, 'category': 'core',
            })
    else:
        lines.append('  （データなし）')
    lines.append('')

    # ── Satellite A ───────────────────
    lines.append('【Satellite A — 成長株】')
    sat_a_valid = [s for s in sat_raw
                   if 0 < s.get('price', 0) <= WEEKLY_SKIP_THRESHOLD]
    if sat_a_valid:
        budget_each = sat_a_budget // len(sat_a_valid)
        for s in sat_a_valid:
            price = s.get('price', 0)
            shares = max(1, int(budget_each // price))
            cost = shares * int(price)
            total_spent += cost
            name = s.get('name', s.get('ticker', ''))[:10]
            lines.append(f'  {s["ticker"]} {name} × {shares}株'
                         f' @ ¥{int(price):,} = ¥{cost:,}')
            orders['orders'].append({
                'ticker': s['ticker'], 'name': name,
                'shares': shares, 'price': int(price),
                'cost': cost, 'category': 'satellite_a',
            })
    else:
        lines.append('  （対象銘柄なし：株価¥3,750超）')
    lines.append('')

    # ── Satellite B（積立プール）──────
    lines.append('【Satellite B — 積立】')
    pool = load_satb_pool()
    pool['accumulated'] = pool.get('accumulated', 0) + sat_b_budget

    # 最も株価が高いsatellite銘柄をターゲットに
    sat_by_price = sorted(sat_raw, key=lambda s: s.get('price', 0), reverse=True)
    if sat_by_price:
        target = sat_by_price[0]
        target_price = target.get('price', 0)
        target_name  = target.get('name', target.get('ticker', ''))[:10]
        target_ticker = target.get('ticker', '')
        pool['target_ticker'] = target_ticker
        pool['target_price']  = target_price

        if pool['accumulated'] >= target_price > 0:
            shares = pool['accumulated'] // target_price
            cost   = shares * target_price
            pool['accumulated'] -= cost
            total_spent += cost
            lines.append(f'  ✅ {target_ticker} {target_name} × {shares}株'
                         f' @ ¥{target_price:,} = ¥{cost:,}（積立達成）')
            orders['orders'].append({
                'ticker': target_ticker, 'name': target_name,
                'shares': int(shares), 'price': int(target_price),
                'cost': int(cost), 'category': 'satellite_b',
            })
        else:
            lines.append(f'  {target_ticker} {target_name}:'
                         f' +¥{sat_b_budget:,}'
                         f'（累計¥{pool["accumulated"]:,}／¥{int(target_price):,}）')
    else:
        lines.append(f'  積立中: +¥{sat_b_budget:,}（累計¥{pool["accumulated"]:,}）')

    save_satb_pool(pool)
    save_morning_orders(orders)
    lines.append('')

    lines.append('━━━━━━━━━━━━━━')
    lines.append(f'💰 本日合計: ¥{total_spent:,}')
    lines.append('📱 証券アプリで上記を発注してください')

    return '\n'.join(lines)


def generate_evening_message():
    portfolio = load_portfolio()
    today = datetime.now().strftime('%Y-%m-%d')

    if not portfolio:
        return (
            f'📊 本日の運用サマリー ({today})\n'
            '━━━━━━━━━━━━━━\n'
            'まだ投資を開始していません'
        )

    cost    = portfolio.get('cost_basis', 0)
    value   = portfolio.get('market_value', 0)
    upnl    = portfolio.get('unrealized_pnl', 0)
    upnl_p  = portfolio.get('unrealized_pnl_pct', 0)
    divs    = portfolio.get('total_dividends', 0)
    tret    = portfolio.get('total_return', 0)
    tret_p  = portfolio.get('total_return_pct', 0)
    count   = portfolio.get('holdings_count', 0)

    pnl_icon = '📈' if upnl >= 0 else '📉'

    lines = [
        f'📊 本日の運用サマリー ({today})',
        '━━━━━━━━━━━━━━',
        f'投資元本: ¥{cost:,.0f}',
        f'評価額:   ¥{value:,.0f}',
        f'含み損益: {pnl_icon} ¥{upnl:+,.0f}（{upnl_p:+.1f}%）',
        f'累計配当: ¥{divs:,.0f}',
        f'トータルリターン: ¥{tret:+,.0f}（{tret_p:+.1f}%）',
        f'保有銘柄: {count}銘柄',
    ]

    # 本日の取引（morning_orders.json から）
    try:
        with open(ORDERS_PATH, 'r', encoding='utf-8') as f:
            orders_data = json.load(f)
        today_orders = orders_data.get('orders', [])
        if orders_data.get('date') == today and today_orders:
            lines.append('━━━━━━━━━━━━━━')
            lines.append('本日の取引（予定）:')
            for o in today_orders:
                lines.append(f'  🟢 {o["ticker"]} {o["name"]} × {o["shares"]}株 @ ¥{o["price"]:,}')
    except Exception:
        pass

    lines.append('━━━━━━━━━━━━━━')
    lines.append('📱 証券アプリで確認してください')
    return '\n'.join(lines)


def build_screening_message():
    report = load_report()
    core   = report.get('core_results', [])[:10]
    now    = datetime.now().strftime('%Y-%m-%d %H:%M')
    lines  = [f'📊 AI株スクリーニング結果 ({now})', '']
    for s in core:
        per_s = f'PER{s["per"]:.1f}' if s.get('per') else ''
        roe_s = f'ROE{s["roe"]:.1f}%' if s.get('roe') else ''
        lines.append(f'#{s.get("rank","?")} {s.get("ticker","")} {s.get("name","")}')
        lines.append(f'   {per_s}  {roe_s}  Score:{s.get("total_score",0):.0f}')
    lines.append('')
    lines.append('https://minikubai.github.io/ai-stock-screener/')
    return '\n'.join(lines)


def main():
    token = get_token()
    if not token:
        print('❌ LINE_CHANNEL_TOKEN が設定されていません')
        sys.exit(1)

    arg = sys.argv[1] if len(sys.argv) > 1 else ''

    if arg == 'test':
        message = (f'✅ LINE通知テスト成功\n'
                   f'{datetime.now().strftime("%Y-%m-%d %H:%M")}\n'
                   f'AI株スクリーナーからの通知です。')
    elif arg == 'evening':
        message = generate_evening_message()
    else:
        message = generate_morning_message()

    print(f'送信内容:\n{message}\n')
    send_line_message(token, message)


if __name__ == '__main__':
    main()
