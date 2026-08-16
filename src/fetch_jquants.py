"""
J-Quants API V2 クライアント

JPX公式の正確な財務データを取得。
V2: APIキー認証方式（ダッシュボードで発行）

注意: J-Quantsの生データをそのまま公開サイトに掲載することは利用規約違反。
      内部でのスクリーニング精度向上用として使用し、
      サイト掲載用の財務データは公開情報（EDINET等）を出典とすること。

使い方:
  export JQUANTS_API_KEY='your-api-key-here'
  python src/fetch_jquants.py
"""

import requests
import sqlite3
import os
import sys
import time
import yaml
from datetime import datetime, timedelta

BASE_URL = "https://api.jquants.com/v2"

def get_config():
    config_path = os.path.join(os.path.dirname(__file__), '..', 'config', 'settings.yaml')
    with open(config_path, 'r') as f:
        return yaml.safe_load(f)

def get_db():
    config = get_config()
    db_path = os.path.join(os.path.dirname(__file__), '..', config['database']['path'])
    return sqlite3.connect(db_path)


class JQuantsV2:
    """J-Quants API V2 クライアント"""

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.headers = {"x-api-key": api_key}
        # 接続テスト
        self._test_connection()

    def _test_connection(self):
        """接続テスト"""
        try:
            resp = requests.get(
                f"{BASE_URL}/equities/master",
                headers=self.headers,
                params={"code": "72030"},
                timeout=15
            )
            if resp.status_code == 200:
                print("  ✅ J-Quants V2 認証成功")
            elif resp.status_code == 401:
                print("  ❌ APIキーが無効です。ダッシュボードで再確認してください")
                sys.exit(1)
            elif resp.status_code == 403:
                print("  ❌ アクセス権限がありません。プランを確認してください")
                sys.exit(1)
            else:
                print(f"  ⚠️ 接続テスト: status {resp.status_code}")
        except Exception as e:
            print(f"  ❌ 接続エラー: {e}")
            sys.exit(1)

    def _get(self, endpoint: str, params: dict = None) -> dict:
        """APIリクエスト"""
        resp = requests.get(
            f"{BASE_URL}{endpoint}",
            headers=self.headers,
            params=params or {},
            timeout=30
        )
        resp.raise_for_status()
        return resp.json()

    def _get_all(self, endpoint: str, params: dict = None, data_key: str = "data") -> list:
        """ページネーション対応の全件取得"""
        params = params or {}
        all_data = []

        data = self._get(endpoint, params)
        all_data.extend(data.get(data_key, []))

        while data.get("pagination_key"):
            params["pagination_key"] = data["pagination_key"]
            time.sleep(0.5)
            data = self._get(endpoint, params)
            all_data.extend(data.get(data_key, []))

        return all_data

    def get_companies(self) -> list:
        """上場銘柄一覧"""
        return self._get_all("/equities/master", data_key="data")

    def get_financials(self, code: str) -> list:
        """財務サマリーデータ（銘柄コード指定）"""
        return self._get_all("/fins/summary", {"code": code}, data_key="data")

    def get_prices(self, code: str, date_from: str = None, date_to: str = None) -> list:
        """株価四本値（銘柄コード指定）"""
        params = {"code": code}
        if date_from:
            params["from"] = date_from
        if date_to:
            params["to"] = date_to
        return self._get_all("/equities/bars/daily", params, data_key="data")


def to_4digit(code):
    """5桁コード→4桁に変換"""
    return str(code)[:4]

def to_5digit(code):
    """4桁コード→5桁に変換"""
    c = str(code)[:4]
    return c + "0"


def store_companies(conn, companies: list):
    """企業マスタ保存"""
    c = conn.cursor()
    count = 0
    for co in companies:
        code = to_4digit(co.get("Code", ""))
        if not code or len(code) < 4:
            continue
        c.execute('''
            INSERT OR REPLACE INTO companies
            (ticker, name, market, sector, industry)
            VALUES (?, ?, ?, ?, ?)
        ''', (
            code,
            co.get("CoName", co.get("CompanyName", "")),
            co.get("Mkt", co.get("MarketCode", "")),
            co.get("S33", co.get("Sector33Code", "")),
            co.get("S17", co.get("Sector17Code", "")),
        ))
        count += 1
    conn.commit()
    return count


def store_financials(conn, statements: list):
    """財務データ保存（V2は既に円単位）"""
    c = conn.cursor()
    saved = 0

    for s in statements:
        code = to_4digit(s.get("Code", ""))
        if not code:
            continue

        # 決算期末日から年度を取得
        period_end = s.get("CurPerEn", s.get("PeriodEnd", ""))
        fiscal_year = str(period_end)[:4] if period_end else ""
        if not fiscal_year:
            continue

        # 通期データのみ（四半期はスキップ）
        period_type = s.get("CurPerType", "")
        if period_type and period_type not in ("FY", "Annual", ""):
            continue

        def get_val(*keys):
            for k in keys:
                v = s.get(k)
                if v is not None and v != "":
                    try:
                        return float(v)
                    except (ValueError, TypeError):
                        pass
            return None

        # V2フィールド名（全て円単位）
        revenue = get_val("Sales")
        op_income = get_val("OP")
        net_income = get_val("NP")        # "Profit"ではなく"NP"
        total_assets = get_val("TA")
        equity = get_val("Eq")            # "Equity"ではなく"Eq"
        eps = get_val("EPS")
        bps = get_val("BPS")
        dps = get_val("DivAnn", "DivFY")  # 年間配当
        shares_out = get_val("ShOutFY")

        c.execute('''
            INSERT OR REPLACE INTO financials
            (ticker, fiscal_year, revenue, operating_income,
             net_income, total_assets, total_equity,
             shares_outstanding, dividends_per_share, eps, bps)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            code, fiscal_year,
            revenue, op_income, net_income,
            total_assets, equity,
            shares_out, dps, eps, bps,
        ))
        saved += 1

    conn.commit()
    return saved


def store_prices(conn, quotes: list):
    """株価データ保存"""
    c = conn.cursor()
    saved = 0

    for q in quotes:
        code = to_4digit(q.get("Code", ""))
        # V2のカラム名は短縮（O/H/L/C/V）
        date = q.get("Date", "")
        if not code or not date:
            continue

        c.execute('''
            INSERT OR REPLACE INTO prices
            (ticker, date, open, high, low, close, volume)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (
            code, date,
            q.get("O", q.get("Open")),
            q.get("H", q.get("High")),
            q.get("L", q.get("Low")),
            q.get("C", q.get("Close")),
            q.get("V", q.get("Volume")),
        ))
        saved += 1

    conn.commit()
    return saved


def main():
    print("=" * 60)
    print("📊 J-Quants V2 — 正確な財務データ取得")
    print("=" * 60)

    api_key = os.environ.get("JQUANTS_API_KEY")
    if not api_key:
        print("\n❌ 環境変数を設定してください:")
        print("  export JQUANTS_API_KEY='ダッシュボードで発行したAPIキー'")
        sys.exit(1)

    print("\n🔑 認証中...")
    client = JQuantsV2(api_key)

    conn = get_db()
    cur = conn.cursor()

    # Step 1: 上場銘柄一覧
    print("\n📋 上場銘柄一覧を取得中...")
    companies = client.get_companies()
    count = store_companies(conn, companies)
    print(f"  → {count}社を登録")

    # Step 2: 財務データ（スクリーニング通過銘柄のみ取得）
    print("\n💰 財務データ取得中（スクリーニング通過銘柄）...")
    cur.execute("SELECT DISTINCT ticker FROM screening_results ORDER BY ticker")
    rows = cur.fetchall()
    if not rows:
        cur.execute("SELECT ticker FROM companies ORDER BY ticker")
        rows = cur.fetchall()
    tickers = [r[0] for r in rows]
    total = len(tickers)
    saved_fin = 0
    failed = 0
    for i, ticker in enumerate(tickers):
        print(f"  [{i+1}/{total}] {ticker}...", end=" ", flush=True)
        code5 = to_5digit(ticker)
        try:
            statements = client.get_financials(code=code5)
            n = store_financials(conn, statements)
            saved_fin += n
            print(f"{n}件")
            time.sleep(0.5)  # レート制限対策
        except Exception as e:
            failed += 1
            print(f"失敗: {e}")
            time.sleep(1.0)
    conn.commit()
    print(f"  → {saved_fin}件の通期決算データを保存 (失敗:{failed})")

    # Step 3: 株価データはyfinanceで取得済みのためスキップ
    saved_price = 0
    cur.execute("SELECT COUNT(DISTINCT ticker) FROM prices")
    price_count = cur.fetchone()[0]

    cur.execute("SELECT COUNT(DISTINCT ticker) FROM financials")
    fin_count = cur.fetchone()[0]

    conn.close()

    print(f"\n{'=' * 60}")
    print(f"✅ J-Quants V2 データ取得完了")
    print(f"   企業マスタ:  {count}社")
    print(f"   財務データ:  {fin_count}社 ({saved_fin}レコード)")
    print(f"   株価データ:  {price_count}社 (既存データ利用)")
    print(f"{'=' * 60}")
    print(f"\n⚠️  注意: J-Quantsの生データをサイトに直接掲載しないでください")
    print(f"   サイト掲載時は公開情報（EDINET）を出典としてください")


if __name__ == '__main__':
    main()
