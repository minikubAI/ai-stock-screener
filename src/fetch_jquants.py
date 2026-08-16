"""
J-Quants API から財務データを取得してDBに保存

J-Quants は金融庁が提供する日本株の公式データAPI。
EDINETより取得精度が高く、EPS・BPS・配当も含む。

使い方:
  export JQUANTS_EMAIL='your@email.com'
  export JQUANTS_PASSWORD='yourpassword'
  python src/fetch_jquants.py
"""

import sqlite3
import os
import yaml
import time
import requests
from datetime import datetime, timedelta
from typing import Optional

def get_config():
    config_path = os.path.join(os.path.dirname(__file__), '..', 'config', 'settings.yaml')
    with open(config_path, 'r') as f:
        return yaml.safe_load(f)

def get_db():
    config = get_config()
    db_path = os.path.join(os.path.dirname(__file__), '..', config['database']['path'])
    return sqlite3.connect(db_path)


class JQuantsClient:
    BASE = "https://api.jquants.com/v2"

    def __init__(self, email: str, password: str):
        self.email = email
        self.password = password
        self.id_token: Optional[str] = None
        self.refresh_token: Optional[str] = None

    def login(self) -> bool:
        """リフレッシュトークン取得"""
        resp = requests.post(f"{self.BASE}/token/auth_user", json={
            "mailaddress": self.email,
            "password": self.password,
        }, timeout=30)
        if resp.status_code != 200:
            print(f"  ❌ ログイン失敗: {resp.status_code} {resp.text[:200]}")
            return False
        self.refresh_token = resp.json().get("refreshToken")
        return self._refresh_id_token()

    def _refresh_id_token(self) -> bool:
        """IDトークン更新"""
        resp = requests.post(
            f"{self.BASE}/token/auth_refresh",
            params={"refreshtoken": self.refresh_token},
            timeout=30
        )
        if resp.status_code != 200:
            print(f"  ❌ トークン更新失敗: {resp.status_code}")
            return False
        self.id_token = resp.json().get("idToken")
        return True

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.id_token}"}

    def get_statements(self, code: str) -> list:
        """財務諸表データ取得"""
        resp = requests.get(
            f"{self.BASE}/fins/statements",
            params={"code": code},
            headers=self._headers(),
            timeout=30
        )
        if resp.status_code == 401:
            self._refresh_id_token()
            resp = requests.get(
                f"{self.BASE}/fins/statements",
                params={"code": code},
                headers=self._headers(),
                timeout=30
            )
        if resp.status_code != 200:
            return []
        return resp.json().get("statements", [])

    def get_listed_info(self) -> list:
        """上場銘柄情報一覧"""
        resp = requests.get(
            f"{self.BASE}/listed/info",
            headers=self._headers(),
            timeout=30
        )
        if resp.status_code != 200:
            return []
        return resp.json().get("info", [])

    def get_prices_daily(self, code: str, date_from: str, date_to: str) -> list:
        """日次株価データ取得"""
        resp = requests.get(
            f"{self.BASE}/prices/daily_quotes",
            params={"code": code, "from": date_from, "to": date_to},
            headers=self._headers(),
            timeout=30
        )
        if resp.status_code == 401:
            self._refresh_id_token()
            resp = requests.get(
                f"{self.BASE}/prices/daily_quotes",
                params={"code": code, "from": date_from, "to": date_to},
                headers=self._headers(),
                timeout=30
            )
        if resp.status_code != 200:
            return []
        return resp.json().get("daily_quotes", [])


def fetch_and_store():
    email = os.environ.get("JQUANTS_EMAIL", "")
    password = os.environ.get("JQUANTS_PASSWORD", "")
    if not email or not password:
        print("❌ JQUANTS_EMAIL / JQUANTS_PASSWORD が未設定です")
        return

    print("=" * 60)
    print("📊 J-Quants データ取得開始")
    print("=" * 60)

    client = JQuantsClient(email, password)
    print("  🔑 ログイン中...")
    if not client.login():
        return
    print("  ✅ ログイン成功")

    conn = get_db()
    c = conn.cursor()

    # DBに登録済みの銘柄を対象にする
    c.execute("SELECT ticker, name FROM companies ORDER BY ticker")
    companies = c.fetchall()
    print(f"\n  📋 対象: {len(companies)}銘柄")

    date_to = datetime.now().strftime("%Y-%m-%d")
    date_from = (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%d")

    success = 0
    failed = 0
    total = len(companies)

    for i, (ticker, name) in enumerate(companies):
        if i % 100 == 0:
            pct = i / total * 100
            print(f"\r  進捗: {i}/{total} ({pct:.0f}%) 成功:{success} 失敗:{failed}", end="", flush=True)

        # 4桁コード → J-Quants は5桁（末尾0を付加）
        code5 = ticker.ljust(5, '0') if len(ticker) == 4 and ticker.isdigit() else ticker

        try:
            stmts = client.get_statements(code5)
            if not stmts:
                failed += 1
                continue

            for s in stmts:
                fy = str(s.get("FiscalYear", ""))[:4]
                if not fy:
                    continue

                c.execute('''
                    INSERT OR REPLACE INTO financials
                    (ticker, fiscal_year, revenue, operating_income,
                     net_income, total_assets, total_equity,
                     shares_outstanding, dividends_per_share, eps, bps)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    ticker, fy,
                    s.get("NetSales"),
                    s.get("OperatingProfit"),
                    s.get("NetIncome"),
                    s.get("TotalAssets"),
                    s.get("NetAssets"),
                    s.get("NumberOfSharesIssuedAndOutstanding"),
                    s.get("DividendPerShare"),
                    s.get("EarningsPerShare"),
                    s.get("BookValuePerShare"),
                ))

            success += 1
            time.sleep(0.05)

        except Exception as e:
            failed += 1
            continue

        # 100銘柄ごとにコミット
        if i % 100 == 0:
            conn.commit()

    conn.commit()

    print(f"\n\n{'=' * 60}")
    print(f"✅ 完了: {success}銘柄成功 / {failed}銘柄失敗")

    # 集計
    c.execute("SELECT COUNT(DISTINCT ticker || fiscal_year) FROM financials")
    total_records = c.fetchone()[0]
    print(f"   財務レコード総数: {total_records}件")
    print("=" * 60)

    conn.close()


if __name__ == '__main__':
    fetch_and_store()
