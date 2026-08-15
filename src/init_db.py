"""データベース初期化スクリプト"""

import sqlite3
import os
import yaml

def get_config():
    config_path = os.path.join(os.path.dirname(__file__), '..', 'config', 'settings.yaml')
    with open(config_path, 'r') as f:
        return yaml.safe_load(f)

def init_db():
    config = get_config()
    db_path = os.path.join(os.path.dirname(__file__), '..', config['database']['path'])
    os.makedirs(os.path.dirname(db_path), exist_ok=True)

    conn = sqlite3.connect(db_path)
    c = conn.cursor()

    # 企業マスタ
    c.execute('''
        CREATE TABLE IF NOT EXISTS companies (
            ticker TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            sector TEXT,
            industry TEXT,
            market TEXT,
            edinet_code TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # 財務データ（決算期ごと）
    c.execute('''
        CREATE TABLE IF NOT EXISTS financials (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL,
            fiscal_year TEXT NOT NULL,
            fiscal_period TEXT DEFAULT 'FY',
            revenue REAL,
            operating_income REAL,
            net_income REAL,
            total_assets REAL,
            total_equity REAL,
            shares_outstanding REAL,
            dividends_per_share REAL,
            eps REAL,
            bps REAL,
            fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(ticker, fiscal_year, fiscal_period),
            FOREIGN KEY (ticker) REFERENCES companies(ticker)
        )
    ''')

    # 株価データ（日次）
    c.execute('''
        CREATE TABLE IF NOT EXISTS prices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL,
            date DATE NOT NULL,
            open REAL,
            high REAL,
            low REAL,
            close REAL,
            volume INTEGER,
            UNIQUE(ticker, date),
            FOREIGN KEY (ticker) REFERENCES companies(ticker)
        )
    ''')

    # スクリーニング結果
    c.execute('''
        CREATE TABLE IF NOT EXISTS screening_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL,
            screened_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            per REAL,
            pbr REAL,
            roe REAL,
            dividend_yield REAL,
            op_growth REAL,
            equity_ratio REAL,
            market_cap REAL,
            score REAL,
            rank INTEGER,
            FOREIGN KEY (ticker) REFERENCES companies(ticker)
        )
    ''')

    # ポートフォリオ（保有銘柄）
    c.execute('''
        CREATE TABLE IF NOT EXISTS portfolio (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL,
            buy_date DATE NOT NULL,
            buy_price REAL NOT NULL,
            shares INTEGER NOT NULL,
            status TEXT DEFAULT 'HOLD',
            sell_date DATE,
            sell_price REAL,
            profit_loss REAL,
            profit_loss_pct REAL,
            FOREIGN KEY (ticker) REFERENCES companies(ticker)
        )
    ''')

    conn.commit()
    conn.close()
    print("✅ データベースを初期化しました")

if __name__ == '__main__':
    init_db()
