"""
ポートフォリオ監視スクリプト

保有銘柄の現在価格をチェックし、
損切り/利確ラインに達した銘柄をアラート
"""

import sqlite3
import os
import yaml
from datetime import datetime
from rich.console import Console
from rich.table import Table
from fetch_prices import get_current_price

console = Console()

def get_config():
    config_path = os.path.join(os.path.dirname(__file__), '..', 'config', 'settings.yaml')
    with open(config_path, 'r') as f:
        return yaml.safe_load(f)

def get_db():
    config = get_config()
    db_path = os.path.join(os.path.dirname(__file__), '..', config['database']['path'])
    return sqlite3.connect(db_path)


def check_portfolio():
    """保有銘柄を監視"""
    config = get_config()
    conn = get_db()
    c = conn.cursor()

    stop_loss = config['trading']['stop_loss_pct']
    take_profit = config['trading']['take_profit_pct']

    # 保有中の銘柄を取得
    c.execute('''
        SELECT p.id, p.ticker, c.name, p.buy_date, p.buy_price, p.shares
        FROM portfolio p
        JOIN companies c ON p.ticker = c.ticker
        WHERE p.status = 'HOLD'
    ''')

    holdings = c.fetchall()

    if not holdings:
        console.print("[dim]保有銘柄はありません[/dim]")
        return

    console.print(f"\n[bold]💼 ポートフォリオ監視 ({len(holdings)}銘柄)[/bold]\n")

    table = Table()
    table.add_column("コード", style="bold")
    table.add_column("企業名", max_width=16)
    table.add_column("購入日")
    table.add_column("購入価格", justify="right")
    table.add_column("現在価格", justify="right")
    table.add_column("損益%", justify="right")
    table.add_column("損益額", justify="right")
    table.add_column("判定", justify="center")

    alerts = []

    for row in holdings:
        pid, ticker, name, buy_date, buy_price, shares = row
        current_price = get_current_price(ticker)

        if current_price is None:
            table.add_row(ticker, name, buy_date, f"¥{buy_price:,.0f}",
                          "取得失敗", "-", "-", "⚠️")
            continue

        pnl_pct = (current_price - buy_price) / buy_price * 100
        pnl_amount = (current_price - buy_price) * shares

        # 判定
        if pnl_pct <= stop_loss:
            action = "🔴 損切り"
            alerts.append(('STOP_LOSS', ticker, name, pnl_pct))
        elif pnl_pct >= take_profit:
            action = "🟢 利確"
            alerts.append(('TAKE_PROFIT', ticker, name, pnl_pct))
        elif pnl_pct >= 0:
            action = "⬆️ 含み益"
        else:
            action = "⬇️ 含み損"

        # 色分け
        pnl_style = "green" if pnl_pct >= 0 else "red"

        # DB更新（含み損益を記録）
        c.execute('''
            UPDATE portfolio
            SET profit_loss = ?, profit_loss_pct = ?
            WHERE id = ?
        ''', (round(pnl_amount, 0), round(pnl_pct, 2), pid))

        table.add_row(
            ticker,
            name[:16],
            buy_date,
            f"¥{buy_price:,.0f}",
            f"¥{current_price:,.0f}",
            f"[{pnl_style}]{pnl_pct:+.1f}%[/{pnl_style}]",
            f"[{pnl_style}]¥{pnl_amount:+,.0f}[/{pnl_style}]",
            action,
        )

    conn.commit()
    conn.close()

    console.print(table)

    # アラート表示
    if alerts:
        console.print("\n[bold red]⚡ アクション推奨:[/bold red]")
        for action_type, ticker, name, pct in alerts:
            if action_type == 'STOP_LOSS':
                console.print(f"  🔴 {ticker} {name}: {pct:+.1f}% → 損切りライン到達")
            else:
                console.print(f"  🟢 {ticker} {name}: {pct:+.1f}% → 利確ライン到達")


if __name__ == '__main__':
    check_portfolio()
