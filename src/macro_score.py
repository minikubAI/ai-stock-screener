"""
マクロ環境スコア（第4層）

日経平均・VIX・USD/JPY・金利からマクロ環境を自動評価。
0-100のスコアで「買い時 / 通常 / 様子見」を判定。

使い方:
  python src/macro_score.py
"""

import yfinance as yf
import sqlite3
import os
import yaml
import numpy as np
from datetime import datetime, timedelta

def get_config():
    config_path = os.path.join(os.path.dirname(__file__), '..', 'config', 'settings.yaml')
    with open(config_path, 'r') as f:
        return yaml.safe_load(f)

def get_db():
    config = get_config()
    db_path = os.path.join(os.path.dirname(__file__), '..', config['database']['path'])
    return sqlite3.connect(db_path)


def init_macro_table(conn):
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS macro_scores (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT UNIQUE,
            nikkei_score REAL,
            vix_score REAL,
            fx_score REAL,
            combined_score REAL,
            signal TEXT,
            nikkei_value REAL,
            nikkei_200ma REAL,
            nikkei_drawdown REAL,
            vix_value REAL,
            usdjpy REAL,
            details TEXT
        )
    ''')
    conn.commit()


def score_nikkei() -> dict:
    """
    日経平均のスコア (0-100)
    - 200日移動平均線との乖離率
    - 直近高値からの下落率
    """
    try:
        nk = yf.Ticker("^N225")
        hist = nk.history(period="1y")
        if hist.empty or len(hist) < 50:
            return {'score': 50, 'value': 0, 'ma200': 0, 'drawdown': 0}

        current = hist['Close'].iloc[-1]
        ma200 = hist['Close'].rolling(200).mean().iloc[-1] if len(hist) >= 200 else hist['Close'].mean()
        high_52w = hist['Close'].max()

        # 200MA乖離率: MAの上なら良い、下なら悪い
        ma_deviation = (current - ma200) / ma200 * 100

        # ドローダウン: 高値からの下落率
        drawdown = (current - high_52w) / high_52w * 100

        # スコア計算
        # MA上方: 60-80点, MA付近: 40-60点, MA下方: 20-40点
        if ma_deviation > 10:
            ma_score = 80
        elif ma_deviation > 0:
            ma_score = 60 + (ma_deviation / 10) * 20
        elif ma_deviation > -10:
            ma_score = 40 + (ma_deviation + 10) / 10 * 20
        else:
            ma_score = max(10, 40 + ma_deviation)

        # ドローダウン補正: -15%以下は暴落=買いチャンス（逆張りスコア加点）
        if drawdown < -15:
            dd_bonus = 15  # 暴落時は逆張り加点
        elif drawdown < -5:
            dd_bonus = 0
        else:
            dd_bonus = 5  # 高値圏は少し加点

        score = max(0, min(100, ma_score + dd_bonus))

        return {
            'score': round(score, 1),
            'value': round(current, 0),
            'ma200': round(ma200, 0),
            'drawdown': round(drawdown, 1),
            'ma_deviation': round(ma_deviation, 1)
        }
    except Exception as e:
        print(f"  ⚠️ 日経平均取得エラー: {e}")
        return {'score': 50, 'value': 0, 'ma200': 0, 'drawdown': 0}


def score_vix() -> dict:
    """
    VIXのスコア (0-100)
    VIX低い = 市場安定 = 買い環境良好
    VIX高い = 恐怖 = 買い控え（ただし極端に高いと逆張りチャンス）
    """
    try:
        vix = yf.Ticker("^VIX")
        hist = vix.history(period="5d")
        if hist.empty:
            return {'score': 50, 'value': 0}

        current = hist['Close'].iloc[-1]

        if current < 15:
            score = 85  # 非常に安定
        elif current < 20:
            score = 70  # 安定
        elif current < 25:
            score = 55  # やや警戒
        elif current < 30:
            score = 35  # 警戒
        elif current < 40:
            score = 20  # 恐怖
        else:
            score = 40  # 極度の恐怖 → 逆張りチャンス

        return {'score': round(score, 1), 'value': round(current, 2)}
    except Exception as e:
        print(f"  ⚠️ VIX取得エラー: {e}")
        return {'score': 50, 'value': 0}


def score_fx() -> dict:
    """
    USD/JPYのスコア (0-100)
    円安 = 輸出関連に追い風、外国人投資家にとって割安
    急激な円高 = リスク
    """
    try:
        fx = yf.Ticker("USDJPY=X")
        hist = fx.history(period="6mo")
        if hist.empty:
            return {'score': 50, 'value': 0}

        current = hist['Close'].iloc[-1]
        avg_6mo = hist['Close'].mean()
        change = (current - avg_6mo) / avg_6mo * 100

        # 安定〜緩やかな円安が最も好環境
        if -3 < change < 5:
            score = 75  # 安定
        elif 5 <= change < 10:
            score = 65  # やや円安進行
        elif change >= 10:
            score = 50  # 急激な円安（介入リスク）
        elif -8 < change <= -3:
            score = 55  # やや円高
        else:
            score = 35  # 急激な円高

        return {'score': round(score, 1), 'value': round(current, 2), 'change_6mo': round(change, 1)}
    except Exception as e:
        print(f"  ⚠️ 為替取得エラー: {e}")
        return {'score': 50, 'value': 0}


def calculate_combined_score(nikkei, vix, fx) -> dict:
    """
    マクロ総合スコア
    日経40% + VIX35% + 為替25%
    """
    combined = (
        nikkei['score'] * 0.40 +
        vix['score'] * 0.35 +
        fx['score'] * 0.25
    )
    combined = round(combined, 1)

    if combined >= 70:
        signal = 'BUY'      # 積極的に買い
    elif combined >= 50:
        signal = 'NORMAL'   # 通常通り
    elif combined >= 35:
        signal = 'CAUTION'  # 買い金額を50%に減額
    else:
        signal = 'PAUSE'    # 新規買い停止（配当再投資のみ）

    return {'score': combined, 'signal': signal}


def run():
    print("🌍 マクロ環境スコア算出")
    print("=" * 50)

    conn = get_db()
    init_macro_table(conn)

    print("\n  📊 日経平均...")
    nikkei = score_nikkei()
    print(f"     値: ¥{nikkei.get('value', 0):,.0f} / 200MA: ¥{nikkei.get('ma200', 0):,.0f} / Score: {nikkei['score']}")

    print("  📊 VIX...")
    vix = score_vix()
    print(f"     値: {vix.get('value', 0)} / Score: {vix['score']}")

    print("  📊 USD/JPY...")
    fx = score_fx()
    print(f"     値: ¥{fx.get('value', 0):,.2f} / Score: {fx['score']}")

    combined = calculate_combined_score(nikkei, vix, fx)
    print(f"\n  🎯 総合スコア: {combined['score']} / Signal: {combined['signal']}")

    # DB保存
    c = conn.cursor()
    today = datetime.now().strftime('%Y-%m-%d')
    c.execute('''
        INSERT OR REPLACE INTO macro_scores
        (date, nikkei_score, vix_score, fx_score, combined_score, signal,
         nikkei_value, nikkei_200ma, nikkei_drawdown, vix_value, usdjpy)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        today, nikkei['score'], vix['score'], fx['score'],
        combined['score'], combined['signal'],
        nikkei.get('value'), nikkei.get('ma200'),
        nikkei.get('drawdown'), vix.get('value'), fx.get('value')
    ))
    conn.commit()
    conn.close()

    print(f"\n{'=' * 50}")
    print(f"✅ マクロスコア保存完了")


if __name__ == '__main__':
    run()
