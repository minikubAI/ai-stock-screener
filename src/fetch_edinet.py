"""
EDINET API から決算データを取得するスクリプト

EDINET API v2 を使用して：
1. 書類一覧を取得（有価証券報告書・決算短信）
2. XBRL データをパースして財務数値を抽出
3. DBに保存
"""

import requests
import sqlite3
import os
import time
import json
import zipfile
import io
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from typing import Optional
import yaml

def get_config():
    config_path = os.path.join(os.path.dirname(__file__), '..', 'config', 'settings.yaml')
    with open(config_path, 'r') as f:
        return yaml.safe_load(f)

def get_db():
    config = get_config()
    db_path = os.path.join(os.path.dirname(__file__), '..', config['database']['path'])
    return sqlite3.connect(db_path)


class EdinetClient:
    """EDINET API クライアント"""

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.base_url = "https://api.edinet-fsa.go.jp/api/v2"

    def get_document_list(self, date: str, doc_type: str = "2") -> list:
        """
        指定日の書類一覧を取得

        doc_type:
          "2" = 有価証券報告書等
          "1" = EDINETに提出された全書類
        """
        url = f"{self.base_url}/documents.json"
        params = {
            "date": date,
            "type": doc_type,
            "Subscription-Key": self.api_key
        }

        try:
            resp = requests.get(url, params=params, timeout=30)
            resp.raise_for_status()
            data = resp.json()

            if data.get("metadata", {}).get("status") != "200":
                print(f"  ⚠️ API応答異常: {data.get('metadata')}")
                return []

            results = data.get("results", [])
            # 有価証券報告書・決算短信のみフィルタ
            filtered = [
                r for r in results
                if r.get("docTypeCode") in ["120", "130", "140", "150"]
                # 120: 有価証券報告書
                # 130: 四半期報告書
                # 140: 半期報告書
                # 150: 臨時報告書
                and r.get("secCode")  # 証券コードがあるもの
            ]
            return filtered

        except requests.exceptions.RequestException as e:
            print(f"  ❌ API通信エラー: {e}")
            return []

    def download_xbrl(self, doc_id: str) -> Optional[bytes]:
        """XBRL書類をダウンロード"""
        url = f"{self.base_url}/documents/{doc_id}"
        params = {
            "type": "1",  # XBRL
            "Subscription-Key": self.api_key
        }

        try:
            resp = requests.get(url, params=params, timeout=60)
            resp.raise_for_status()
            return resp.content
        except requests.exceptions.RequestException as e:
            print(f"  ❌ XBRL取得エラー ({doc_id}): {e}")
            return None


def parse_xbrl_financials(xbrl_zip: bytes) -> Optional[dict]:
    """
    XBRLのZIPからキー財務指標を抽出

    名前空間バージョンを動的に検出して対応する
    """
    # 取得対象タグ名（名前空間なし）
    TARGET_TAGS = {
        'revenue': ['NetSales', 'Revenue'],
        'operating_income': ['OperatingIncome'],
        'net_income': ['ProfitLossAttributableToOwnersOfParent', 'ProfitLoss'],
        'total_assets': ['TotalAssets'],
        'total_equity': ['EquityAttributableToOwnersOfParent', 'NetAssets'],
    }

    try:
        with zipfile.ZipFile(io.BytesIO(xbrl_zip)) as zf:
            xbrl_files = [
                f for f in zf.namelist()
                if f.endswith('.xbrl') and 'AuditDoc' not in f
            ]
            if not xbrl_files:
                return None

            with zf.open(xbrl_files[0]) as f:
                content = f.read()

            root = ET.fromstring(content)

            # ファイル内の全名前空間URIを収集
            ns_uris = set()
            for elem in root.iter():
                tag = elem.tag
                if tag.startswith('{'):
                    uri = tag[1:tag.index('}')]
                    if 'edinet-fsa.go.jp/taxonomy/jppfs' in uri:
                        ns_uris.add(uri)

            financials = {}

            for key, local_names in TARGET_TAGS.items():
                for local_name in local_names:
                    # 検出した全名前空間で試行
                    for ns_uri in ns_uris:
                        full_tag = f'{{{ns_uri}}}{local_name}'
                        elements = root.findall(f'.//{full_tag}')
                        for elem in elements:
                            ctx = elem.get('contextRef', '')
                            if 'CurrentYear' in ctx or 'Current' in ctx:
                                try:
                                    financials[key] = float(elem.text)
                                except (TypeError, ValueError):
                                    pass
                                break
                        if key in financials:
                            break
                    if key in financials:
                        break

            return financials if financials else None

    except Exception as e:
        print(f"  ⚠️ XBRLパースエラー: {e}")
        return None


def fetch_and_store(days_back: int = 30):
    """
    過去N日分のEDINET書類を取得してDBに保存
    """
    config = get_config()
    client = EdinetClient(config['edinet']['api_key'])
    conn = get_db()
    c = conn.cursor()

    today = datetime.now()
    total_docs = 0
    total_saved = 0

    print(f"📊 EDINET データ取得開始（過去{days_back}日分）")
    print("=" * 50)

    for i in range(days_back):
        date = (today - timedelta(days=i)).strftime('%Y-%m-%d')
        print(f"\n📅 {date} の書類を検索中...")

        docs = client.get_document_list(date)
        if not docs:
            print(f"  → 該当なし")
            continue

        print(f"  → {len(docs)}件の書類を発見")
        total_docs += len(docs)

        for doc in docs:
            sec_code = doc.get('secCode', '')[:4]  # 4桁に正規化
            company_name = doc.get('filerName', '')
            edinet_code = doc.get('edinetCode', '')
            doc_id = doc.get('docID', '')

            if not sec_code or not doc_id:
                continue

            # 企業マスタに登録/更新
            c.execute('''
                INSERT OR IGNORE INTO companies (ticker, name, edinet_code)
                VALUES (?, ?, ?)
            ''', (sec_code, company_name, edinet_code))

            # XBRLダウンロード & パース
            print(f"  📄 {sec_code} {company_name} のXBRLを取得中...")
            xbrl_data = client.download_xbrl(doc_id)

            if xbrl_data:
                financials = parse_xbrl_financials(xbrl_data)
                if financials:
                    fiscal_year = (doc.get('periodEnd') or date or '')[:4] or date[:4]

                    c.execute('''
                        INSERT OR REPLACE INTO financials
                        (ticker, fiscal_year, revenue, operating_income,
                         net_income, total_assets, total_equity)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                    ''', (
                        sec_code, fiscal_year,
                        financials.get('revenue'),
                        financials.get('operating_income'),
                        financials.get('net_income'),
                        financials.get('total_assets'),
                        financials.get('total_equity'),
                    ))
                    total_saved += 1
                    print(f"    ✅ 財務データ保存完了")
                else:
                    print(f"    ⚠️ 財務データ抽出できず")

            # API制限対策
            time.sleep(1)

        conn.commit()

    conn.close()
    print(f"\n{'=' * 50}")
    print(f"✅ 完了: {total_docs}件中 {total_saved}件の財務データを保存")


if __name__ == '__main__':
    fetch_and_store(days_back=30)
