"""
LINE Notify スクリーニング結果通知

使い方:
  python src/notify_line.py           # スクリーニング結果を送信
  python src/notify_line.py test      # テストメッセージを送信

環境変数:
  LINE_CHANNEL_TOKEN  LINE Messaging API チャンネルアクセストークン
"""

import os
import sys
import sqlite3
import yaml
import requests
from datetime import datetime


def get_config():
    config_path = os.path.join(os.path.dirname(__file__), '..', 'config', 'settings.yaml')
    with open(config_path, 'r') as f:
        return yaml.safe_load(f)


def get_db():
    config = get_config()
    db_path = os.path.join(os.path.dirname(__file__), '..', config['database']['path'])
    return sqlite3.connect(db_path)


def send_line_message(token: str, message: str) -> bool:
    url = 'https://api.line.me/v2/bot/message/broadcast'
    headers = {
        'Content-Type': 'application/json',
        'Authorization': f'Bearer {token}',
    }
    payload = {
        'messages': [{'type': 'text', 'text': message}]
    }
    resp = requests.post(url, headers=headers, json=payload, timeout=10)
    if resp.status_code == 200:
        print(f'✅ LINE送信成功')
        return True
    else:
        print(f'❌ LINE送信失敗: {resp.status_code} {resp.text}')
        return False


def build_screening_message() -> str:
    conn = get_db()
    c = conn.cursor()
    c.execute('''
        SELECT sr.ticker, co.name, sr.rank, sr.per, sr.pbr, sr.roe, sr.score
        FROM screening_results sr
        JOIN companies co ON sr.ticker = co.ticker
        WHERE sr.screened_at = (SELECT MAX(screened_at) FROM screening_results)
        ORDER BY sr.rank LIMIT 10
    ''')
    rows = c.fetchall()
    conn.close()

    now = datetime.now().strftime('%Y-%m-%d %H:%M')
    lines = [f'📊 AI株スクリーニング結果 ({now})', '']
    for ticker, name, rank, per, pbr, roe, score in rows:
        per_s = f'PER{per:.1f}' if per else ''
        roe_s = f'ROE{roe:.1f}%' if roe else ''
        lines.append(f'#{rank} {ticker} {name}')
        lines.append(f'   {per_s}  {roe_s}  Score:{score:.0f}')
    lines.append('')
    lines.append('https://minikubai.github.io/ai-stock-screener/')
    return '\n'.join(lines)


def get_token():
    # 環境変数優先、なければ settings.yaml から取得
    token = os.environ.get('LINE_CHANNEL_TOKEN')
    if token:
        return token
    try:
        config = get_config()
        return config.get('line', {}).get('channel_token')
    except Exception:
        return None


def main():
    token = get_token()
    if not token:
        print('❌ LINE_CHANNEL_TOKEN が設定されていません')
        sys.exit(1)

    if len(sys.argv) > 1 and sys.argv[1] == 'test':
        message = f'✅ LINE通知テスト成功\n{datetime.now().strftime("%Y-%m-%d %H:%M")}\nAI株スクリーナーからの通知です。'
    else:
        message = build_screening_message()

    print(f'送信内容:\n{message}\n')
    send_line_message(token, message)


if __name__ == '__main__':
    main()
